"""Seudonimización reversible y restauración (puro Python)."""
import re

from .reglas import ETIQUETAS

INSTRUCCION_LLM = (
    "NOTA: este documento está anonimizado. Los textos entre corchetes como "
    "[PERSONA_1] o [DNI_1] son marcadores que sustituyen datos personales. "
    "Cuando te refieras a ellos, reprodúcelos exactamente igual, con corchetes "
    "incluidos, y no intentes adivinar su contenido.\n\n"
)

_RE_MARCADOR = re.compile(r"\[?\b([A-Z]+_\d+)\b\]?")


def normaliza(valor: str) -> str:
    return " ".join(valor.split()).casefold()


def regex_valor(valor: str) -> re.Pattern:
    """Regex tolerante a saltos de línea/espacios múltiples y a mayúsculas."""
    partes = [re.escape(p) for p in valor.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(partes) + r"(?!\w)", re.IGNORECASE)


def cuenta(texto: str, valor: str) -> int:
    return len(regex_valor(valor).findall(texto))


def construye_mapa(texto: str, items: list[dict]) -> list[dict]:
    """items: [{valor, tipo}] -> [{marcador, valor, tipo, apariciones}].

    Numera por tipo en orden de primera aparición; deduplica por valor normalizado.
    """
    vistos, candidatos = set(), []
    for it in items:
        valor = " ".join(it["valor"].split())
        clave = normaliza(valor)
        if len(clave) < 2 or clave in vistos:
            continue
        m = regex_valor(valor).search(texto)
        if not m:
            continue
        vistos.add(clave)
        candidatos.append((m.start(), valor, it["tipo"]))

    candidatos.sort(key=lambda c: c[0])
    contadores: dict[str, int] = {}
    mapa = []
    for _, valor, tipo in candidatos:
        etiqueta = ETIQUETAS.get(tipo, "DATO")
        contadores[etiqueta] = contadores.get(etiqueta, 0) + 1
        mapa.append({
            "marcador": f"{etiqueta}_{contadores[etiqueta]}",
            "valor": valor,
            "tipo": tipo,
            "apariciones": cuenta(texto, valor),
        })
    return mapa


def aplica(texto: str, mapa: list[dict]) -> str:
    """Sustituye todas las apariciones; los valores largos primero."""
    for e in sorted(mapa, key=lambda e: len(e["valor"]), reverse=True):
        texto = regex_valor(e["valor"]).sub(f"[{e['marcador']}]", texto)
    return texto


def restaura(texto: str, mapa: list[dict]) -> tuple[str, list[str]]:
    """Devuelve (texto restaurado, marcadores desconocidos encontrados)."""
    tabla = {e["marcador"]: e["valor"] for e in mapa}
    desconocidos: list[str] = []

    def _sub(m: re.Match) -> str:
        clave = m.group(1)
        if clave in tabla:
            return tabla[clave]
        prefijo = clave.rsplit("_", 1)[0]
        if prefijo in ETIQUETAS.values() and clave not in desconocidos:
            desconocidos.append(clave)
        return m.group(0)

    return _RE_MARCADOR.sub(_sub, texto), desconocidos


def residuales(texto: str, mapa: list[dict]) -> list[dict]:
    """Valores del mapa que siguen apareciendo en un texto ya anonimizado."""
    out = []
    for e in mapa:
        n = cuenta(texto, e["valor"])
        if n:
            out.append({"marcador": e["marcador"], "valor": e["valor"], "apariciones": n})
    return out
