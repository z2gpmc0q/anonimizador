"""Tests de la lógica pura (no requieren Presidio, spaCy ni PyMuPDF).

Ejecutar:  python3 -m pytest tests   (o)   python3 tests/test_basico.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import reglas, seudo  # noqa: E402


def _busca(entidad, texto):
    out = []
    for _, rx, _, flags in reglas.PATRONES[entidad]:
        f = flags if flags is not None else (re.IGNORECASE | re.MULTILINE)
        out += [m.group(0) for m in re.finditer(rx, texto, f)]
    return out


def test_dni():
    assert reglas.valida_dni("12345678Z")
    assert reglas.valida_dni("12.345.678-Z")
    assert reglas.valida_dni("12345678z")
    assert not reglas.valida_dni("12345678A")
    assert _busca("ES_DNI", "DNI: 12.345.678-Z, tel") == ["12.345.678-Z"]


def test_nie():
    assert reglas.valida_nie("X1234567L")
    assert reglas.valida_nie("Y-1234567-X")
    assert not reglas.valida_nie("X1234567A")


def test_nss():
    # 28/12345678/xx -> 2812345678 % 97
    dc = 2812345678 % 97
    assert reglas.valida_nss(f"28/12345678/{dc:02d}")
    assert not reglas.valida_nss(f"28/12345678/{(dc + 1) % 97:02d}")
    # número < 10^7: regla especial
    dc2 = (8 * 10_000_000 + 1234567) % 97
    assert reglas.valida_nss(f"08 01234567 {dc2:02d}")


def test_matricula_y_direccion():
    assert _busca("ES_MATRICULA", "Vehículo 1234 BCD y año 2024 del") == ["1234 BCD"]
    d = _busca("ES_DIRECCION", "Domicilio: C/ Mayor 12, 3º B\n28001 Madrid")
    assert d and d[0].startswith("C/ Mayor 12"), d
    d = _busca("ES_DIRECCION", "vive en Avenida de la Constitución, nº 45")
    assert d and "45" in d[0], d
    d = _busca("ES_DIRECCION", "Adreça: Carrer de Balmes, 45, 2n 1a, Barcelona")
    assert d and d[0].startswith("Carrer de Balmes, 45"), d
    d = _busca("ES_DIRECCION", "a la Plaça Major s/n de Vic")
    assert d and d[0].endswith("s/n"), d
    d = _busca("ES_DIRECCION", "He lives at 221B Baker Street, London")
    assert d == ["221B Baker Street"], d
    assert _busca("ES_DIRECCION", "The meeting is on 12 March") == []


def test_cups_catastro_referencias():
    assert _busca("ES_CUPS", "CUPS: ES0031408123456789HH.") == ["ES0031408123456789HH"]
    assert _busca("ES_CUPS", "CUPS ES 0031 4081 2345 6789 HH 0F") == ["ES 0031 4081 2345 6789 HH 0F"]
    assert _busca("ES_CATASTRO", "Ref. catastral 9872023VH5797S0001WX") == ["9872023VH5797S0001WX"]
    assert _busca("ES_CATASTRO", "rústica 13077A018000390000FP") == ["13077A018000390000FP"]
    assert "01/04/2025" not in _busca("ES_REFERENCIA", "contrato desde el 01/04/2025")
    assert "POL2024-0098765" in _busca("ES_REFERENCIA", "Nº de póliza: POL2024-0098765")
    assert "PUFE0123456789" in _busca("ES_SANITARIO", "CIP: PUFE0123456789")
    try:
        import stdnum  # noqa: F401  (en la CI se instala; en local puede no estar)
    except ImportError:
        print("   (python-stdnum no instalado: se omite la validación de CUPS/catastro)")
        return
    assert reglas.valida_cups("ES0031408123456789HH") is True
    assert reglas.valida_cups("ES0031408123456789HA") is False
    # Ejemplos oficiales de la web del Catastro
    assert reglas.valida_catastro("9872023VH5797S0001WX") is True
    assert reglas.valida_catastro("13077A018000390000FP") in (True, None)   # rústica: no se descarta
    assert reglas.valida_catastro("9872023VH5797S0001WA") is None          # depende del contexto


def test_email():
    assert _busca("EMAIL_ADDRESS", "Escribe a ana.ruiz+nas@correo.ejemplo.es.") == ["ana.ruiz+nas@correo.ejemplo.es"]


def test_quita_tratamiento():
    assert reglas.quita_tratamiento("D. Jordi Puig Ferrer") == "Jordi Puig Ferrer"
    assert reglas.quita_tratamiento("Dña. Laura Martínez") == "Laura Martínez"
    assert reglas.quita_tratamiento("Sr. D. Juan Gil") == "Juan Gil"
    assert reglas.quita_tratamiento("Mrs. Emily Clarke") == "Emily Clarke"
    assert reglas.quita_tratamiento("la senyora Montserrat Vilaró") == "Montserrat Vilaró"
    assert reglas.quita_tratamiento("Daniel Ruiz") == "Daniel Ruiz"      # «D» sin punto + palabra no
    assert reglas.quita_tratamiento("Dolores Fuertes") == "Dolores Fuertes"
    assert reglas.es_solo_tratamiento("Dña") and reglas.es_solo_tratamiento("Mr.")
    assert not reglas.es_solo_tratamiento("Dolores")


def test_tipo_desde():
    assert reglas.tipo_desde("PERSONA") == "PERSON"
    assert reglas.tipo_desde("person") == "PERSON"
    assert reglas.tipo_desde("") == "DATO"
    assert reglas.tipo_desde("XYZ") == "DATO"


def test_seudonimizacion_ida_y_vuelta():
    texto = ("Contrato entre Juan Pérez García (DNI 12345678Z) y JUAN PÉREZ\nGARCÍA.\n"
             "Contacto: juan@ejemplo.es. Testigo: Ana Ruiz.")
    items = [
        {"valor": "Ana Ruiz", "tipo": "PERSON"},
        {"valor": "Juan Pérez García", "tipo": "PERSON"},
        {"valor": "12345678Z", "tipo": "ES_DNI"},
        {"valor": "juan@ejemplo.es", "tipo": "EMAIL_ADDRESS"},
        {"valor": "no aparece", "tipo": "PERSON"},
    ]
    mapa = seudo.construye_mapa(texto, items)
    marc = {e["valor"]: e["marcador"] for e in mapa}
    assert marc["Juan Pérez García"] == "PERSONA_1"  # primera aparición
    assert marc["Ana Ruiz"] == "PERSONA_2"
    assert "no aparece" not in marc
    assert next(e for e in mapa if e["marcador"] == "PERSONA_1")["apariciones"] == 2

    anon = seudo.aplica(texto, mapa)
    assert "Pérez" not in anon and "PÉREZ" not in anon and "12345678Z" not in anon
    assert anon.count("[PERSONA_1]") == 2
    assert seudo.residuales(anon, mapa) == []

    respuesta = "El firmante [PERSONA_1] (DNI_1) y PERSONA_2 acuerdan… [PERSONA_9]"
    rest, desconocidos = seudo.restaura(respuesta, mapa)
    assert "Juan Pérez García" in rest and "12345678Z" in rest and "Ana Ruiz" in rest
    assert desconocidos == ["PERSONA_9"]


def test_conversacion_comparte_marcadores():
    doc1 = "Contrato de Ana Ruiz con Pedro Gil, DNI 12345678Z."
    mapa1 = seudo.construye_mapa(doc1, [
        {"valor": "Ana Ruiz", "tipo": "PERSON"}, {"valor": "Pedro Gil", "tipo": "PERSON"},
        {"valor": "12345678Z", "tipo": "ES_DNI"}])
    doc2 = "Factura a nombre de Luis Mora. Pagador: Pedro Gil."
    mapa2 = seudo.construye_mapa(doc2, [{"valor": "Luis Mora", "tipo": "PERSON"},
                                        {"valor": "Pedro Gil", "tipo": "PERSON"}], previo=mapa1)
    m = {e["valor"]: e["marcador"] for e in mapa2}
    assert m["Ana Ruiz"] == "PERSONA_1" and m["Pedro Gil"] == "PERSONA_2"   # se conservan
    assert m["Luis Mora"] == "PERSONA_3"                                     # continúa la numeración
    assert m["12345678Z"] == "DNI_1"
    anon2 = seudo.aplica(doc2, mapa2)
    assert anon2 == "Factura a nombre de [PERSONA_3]. Pagador: [PERSONA_2]."
    rest, _ = seudo.restaura("[PERSONA_1] y [PERSONA_3]", mapa2)
    assert rest == "Ana Ruiz y Luis Mora"


def test_no_toca_palabras_parciales():
    mapa = seudo.construye_mapa("Ana y Anabel", [{"valor": "Ana", "tipo": "PERSON"}])
    assert seudo.aplica("Ana y Anabel", mapa) == "[PERSONA_1] y Anabel"


if __name__ == "__main__":
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_"):
            f()
            print("OK", nombre)
