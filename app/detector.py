"""Detección de datos personales con Presidio + spaCy (es/ca/en) + reglas propias.

Gestión de memoria: cada idioma tiene su propio motor, que se carga solo cuando
aparece una página en ese idioma y se descarga tras un tiempo sin uso
(IDLE_SECONDS). En reposo el contenedor apenas ocupa memoria.
"""
import ctypes
import gc
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    IbanRecognizer,
    IpRecognizer,
    PhoneRecognizer,
    SpacyRecognizer,
)

from lingua import Language, LanguageDetectorBuilder

from . import reglas
from .seudo import cuenta, normaliza

# Español con el modelo grande (máxima calidad); catalán e inglés con el mediano (menos RAM)
MODELOS_SPACY = {"es": "es_core_news_lg", "ca": "ca_core_news_md", "en": "en_core_web_md"}
IDIOMAS = tuple(MODELOS_SPACY)
UMBRAL = 0.4
TIPOS_NER = {"PERSON", "LOCATION", "ORGANIZATION"}
# Segundo detector: GLiNER multilingüe especializado en datos personales (Apache-2.0)
GLINER_ACTIVO = os.environ.get("GLINER", "1") != "0"
GLINER_MODELO = "urchade/gliner_multi_pii-v1"
GLINER_UMBRAL = float(os.environ.get("GLINER_UMBRAL", "0.5"))
GLINER_HILOS = int(os.environ.get("GLINER_HILOS", "2"))
# Etiqueta que se le pide a GLiNER -> tipo interno
GLINER_ETIQUETAS = {
    "person": "PERSON",
    "full address": "ES_DIRECCION",
    "phone number": "PHONE_NUMBER",
    "email": "EMAIL_ADDRESS",
    "passport number": "DATO",
    "identity card number": "DATO",
    "social security number": "ES_NSS",
    "bank account number": "IBAN_CODE",
    "date of birth": "FECHA",
    "license plate number": "ES_MATRICULA",
}
GLINER_PALABRAS_TROZO = 220   # el modelo admite ~384 subpalabras; trozos con margen
GLINER_SOLAPE = 30
# Segundos sin uso tras los que se liberan los modelos (0 = no liberar nunca)
IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "900"))
log = logging.getLogger("uvicorn.error")


class _ReconocedorValidado(PatternRecognizer):
    """PatternRecognizer con validación de dígito de control."""

    def __init__(self, validador, **kw):
        self._validador = validador
        super().__init__(**kw)

    def validate_result(self, pattern_text: str) -> Optional[bool]:
        return self._validador(pattern_text)


def _crea_motor(idioma: str) -> AnalyzerEngine:
    """Motor de un único idioma (solo carga el modelo de spaCy de ese idioma)."""
    conf = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": idioma, "model_name": MODELOS_SPACY[idioma]}],
        "ner_model_configuration": {
            # es/ca usan PER/LOC/ORG; en usa PERSON/GPE/LOC/FAC/ORG
            "model_to_presidio_entity_mapping": {
                "PER": "PERSON", "PERSON": "PERSON",
                "LOC": "LOCATION", "GPE": "LOCATION", "FAC": "LOCATION",
                "ORG": "ORGANIZATION",
            },
            "labels_to_ignore": [
                "MISC", "NORP", "DATE", "TIME", "CARDINAL", "ORDINAL", "QUANTITY", "MONEY",
                "PERCENT", "EVENT", "WORK_OF_ART", "LAW", "LANGUAGE", "PRODUCT",
            ],
        },
    }
    nlp = NlpEngineProvider(nlp_configuration=conf).create_engine()

    try:
        registro = RecognizerRegistry(supported_languages=[idioma])
    except TypeError:  # versiones antiguas de Presidio
        registro = RecognizerRegistry()

    ctx = reglas.CONTEXTO
    registro.add_recognizer(SpacyRecognizer(supported_language=idioma, supported_entities=sorted(TIPOS_NER)))
    registro.add_recognizer(IbanRecognizer(supported_language=idioma, context=ctx["IBAN_CODE"]))
    registro.add_recognizer(CreditCardRecognizer(supported_language=idioma, context=ctx["CREDIT_CARD"]))
    registro.add_recognizer(IpRecognizer(supported_language=idioma))
    registro.add_recognizer(PhoneRecognizer(
        supported_language=idioma, supported_regions=("ES", "AD", "FR", "GB", "US"),
        context=ctx["PHONE_NUMBER"],
    ))
    for entidad, patrones in reglas.PATRONES.items():
        for nombre, regex, score, flags in patrones:
            kw = dict(
                supported_entity=entidad,
                patterns=[Pattern(name=nombre, regex=regex, score=score)],
                context=ctx.get(entidad),
                supported_language=idioma,
                name=f"{entidad}_{nombre}_{idioma}",
            )
            if flags is not None:
                kw["global_regex_flags"] = flags
            if entidad in reglas.VALIDADORES:
                registro.add_recognizer(_ReconocedorValidado(reglas.VALIDADORES[entidad], **kw))
            else:
                registro.add_recognizer(PatternRecognizer(**kw))

    return AnalyzerEngine(registry=registro, nlp_engine=nlp, supported_languages=[idioma])


def _carga_gliner():
    import torch  # importación diferida: solo ocupa memoria si se usa
    from gliner import GLiNER

    torch.set_num_threads(GLINER_HILOS)
    modelo = GLiNER.from_pretrained(GLINER_MODELO, local_files_only=True)
    modelo.eval()
    return modelo


def _trozos(texto: str):
    """Divide en trozos de ~GLINER_PALABRAS_TROZO palabras con solape; devuelve (inicio, fin)."""
    palabras = [m.span() for m in re.finditer(r"\S+", texto)]
    paso = GLINER_PALABRAS_TROZO - GLINER_SOLAPE
    for i in range(0, max(len(palabras), 1), paso):
        grupo = palabras[i:i + GLINER_PALABRAS_TROZO]
        if not grupo:
            break
        yield grupo[0][0], grupo[-1][1]
        if i + GLINER_PALABRAS_TROZO >= len(palabras):
            break


def _devuelve_memoria_al_sistema() -> None:
    """Python no siempre devuelve al sistema la memoria liberada; malloc_trim lo fuerza."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


_LINGUA = {"SPANISH": "es", "CATALAN": "ca", "ENGLISH": "en"}


def _crea_detector_idioma():
    return LanguageDetectorBuilder.from_languages(Language.SPANISH, Language.CATALAN, Language.ENGLISH).build()


def _lee_lista(ruta: Path) -> list[tuple[str, str]]:
    """Formato: una entrada por línea, 'valor' o 'valor;TIPO'. '#' = comentario."""
    if not ruta.is_file():
        return []
    out = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        valor, _, tipo = linea.partition(";")
        out.append((valor.strip(), reglas.tipo_desde(tipo)))
    return out


def _es_basura(valor: str, tipo: str) -> bool:
    if len(valor) < 2 or len(valor) > 80:
        return True
    if tipo in TIPOS_NER:
        if len(valor) < 3 or not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2}", valor):
            return True
        if re.search(r"\d{3,}", valor):
            return True
    return False


class Detector:
    def __init__(self, dir_config: Path):
        self._motores: dict[str, AnalyzerEngine] = {}
        self._idioma = _crea_detector_idioma()
        self._lock = threading.Lock()
        self._dir_config = dir_config
        self._ultimo_uso = time.monotonic()
        if IDLE_SECONDS > 0:
            threading.Thread(target=self._vigila_inactividad, daemon=True).start()

    def _motor(self, idioma: str) -> AnalyzerEngine:
        """Devuelve el motor del idioma, cargándolo si hace falta (llamar con el lock)."""
        if idioma not in self._motores:
            t0 = time.monotonic()
            self._motores[idioma] = _crea_motor(idioma)
            log.info("Modelo '%s' cargado en %.1f s", idioma, time.monotonic() - t0)
        return self._motores[idioma]

    def _gliner(self):
        if "gliner" not in self._motores:
            t0 = time.monotonic()
            self._motores["gliner"] = _carga_gliner()
            log.info("Modelo 'gliner' cargado en %.1f s", time.monotonic() - t0)
        return self._motores["gliner"]

    def _analiza_gliner(self, texto: str):
        modelo = self._gliner()
        etiquetas = list(GLINER_ETIQUETAS)
        for ini, fin in _trozos(texto):
            for e in modelo.predict_entities(texto[ini:fin], etiquetas, threshold=GLINER_UMBRAL):
                yield texto[ini + e["start"]:ini + e["end"]], GLINER_ETIQUETAS[e["label"]], float(e["score"])

    def _vigila_inactividad(self) -> None:
        while True:
            time.sleep(min(60, max(5, IDLE_SECONDS // 4)))
            with self._lock:
                if self._motores and time.monotonic() - self._ultimo_uso > IDLE_SECONDS:
                    cargados = ", ".join(self._motores)
                    self._motores.clear()
                    _devuelve_memoria_al_sistema()
                    log.info("Modelos liberados por inactividad: %s", cargados)

    def modelos_cargados(self) -> list[str]:
        return sorted(self._motores)

    def idioma_de(self, texto: str, defecto: str = "es") -> str:
        if len(texto.strip()) < 30:
            return defecto
        lang = self._idioma.detect_language_of(texto)
        return _LINGUA.get(lang.name, defecto) if lang is not None else defecto

    def analiza(self, paginas: list[str], terminos_extra: list[str], idioma: str = "auto") -> tuple[list[dict], list[str]]:
        """Devuelve (entidades, idioma usado en cada página)."""
        texto_total = "\n\n".join(paginas)
        permitidos = {normaliza(v) for v, _ in _lee_lista(self._dir_config / "permitidos.txt")}
        agrupado: dict[str, dict] = {}

        def _anota(valor: str, tipo: str, score: float, origen: str):
            valor = " ".join(valor.split()).strip(" .,;:()[]\"'")
            if tipo == "PERSON":
                valor = reglas.quita_tratamiento(valor)
                if reglas.es_solo_tratamiento(valor):
                    return
            clave = normaliza(valor)
            if clave in permitidos or _es_basura(valor, tipo):
                return
            e = agrupado.get(clave)
            if e is not None and e["tipo"] in reglas.VALIDADORES and origen == "gliner":
                return  # un DNI/NIE/NSS validado por su dígito de control manda
            if e is None or score > e["score"]:
                agrupado[clave] = {"valor": valor, "tipo": tipo, "score": round(score, 2), "origen": origen}

        idiomas: list[str] = []
        with self._lock:
            for texto in paginas:
                if not texto.strip():
                    continue
                lang = idioma if idioma in IDIOMAS else self.idioma_de(texto)
                idiomas.append(lang)
                for r in self._motor(lang).analyze(text=texto, language=lang, score_threshold=UMBRAL):
                    _anota(texto[r.start:r.end], r.entity_type, r.score, "auto")
                if GLINER_ACTIVO:
                    for valor, tipo, score in self._analiza_gliner(texto):
                        _anota(valor, tipo, score, "gliner")
            self._ultimo_uso = time.monotonic()

        fijos = _lee_lista(self._dir_config / "terminos.txt")
        fijos += [(t.partition(";")[0].strip(), reglas.tipo_desde(t.partition(";")[2]))
                  for t in terminos_extra if t.strip()]
        for valor, tipo in fijos:
            _anota(valor, tipo, 1.0, "lista")

        entidades = []
        for i, e in enumerate(sorted(agrupado.values(), key=lambda e: (e["tipo"], e["valor"].casefold()))):
            n = cuenta(texto_total, e["valor"])
            if n == 0:
                continue
            entidades.append({
                "id": i,
                **e,
                "etiqueta": reglas.ETIQUETAS.get(e["tipo"], "DATO"),
                "apariciones": n,
                "marcado": e["tipo"] not in reglas.DESMARCADOS_POR_DEFECTO,
            })
        return entidades, idiomas
