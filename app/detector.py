"""Detección de datos personales con Presidio + spaCy (español) + reglas propias."""
import re
import threading
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


class _ReconocedorValidado(PatternRecognizer):
    """PatternRecognizer con validación de dígito de control."""

    def __init__(self, validador, **kw):
        self._validador = validador
        super().__init__(**kw)

    def validate_result(self, pattern_text: str) -> Optional[bool]:
        return self._validador(pattern_text)


def _crea_motor() -> AnalyzerEngine:
    conf = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": l, "model_name": m} for l, m in MODELOS_SPACY.items()],
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
        registro = RecognizerRegistry(supported_languages=list(IDIOMAS))
    except TypeError:  # versiones antiguas de Presidio
        registro = RecognizerRegistry()

    ctx = reglas.CONTEXTO
    for idioma in IDIOMAS:
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

    return AnalyzerEngine(registry=registro, nlp_engine=nlp, supported_languages=list(IDIOMAS))


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
        self._motor = _crea_motor()
        self._idioma = _crea_detector_idioma()
        self._lock = threading.Lock()
        self._dir_config = dir_config

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
            clave = normaliza(valor)
            if clave in permitidos or _es_basura(valor, tipo):
                return
            e = agrupado.get(clave)
            if e is None or score > e["score"]:
                agrupado[clave] = {"valor": valor, "tipo": tipo, "score": round(score, 2), "origen": origen}

        idiomas: list[str] = []
        with self._lock:
            for texto in paginas:
                if not texto.strip():
                    continue
                lang = idioma if idioma in IDIOMAS else self.idioma_de(texto)
                idiomas.append(lang)
                for r in self._motor.analyze(text=texto, language=lang, score_threshold=UMBRAL):
                    _anota(texto[r.start:r.end], r.entity_type, r.score, "auto")

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
