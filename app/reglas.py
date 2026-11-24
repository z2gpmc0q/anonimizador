"""Reglas de detección españolas (puras, sin dependencias externas).

Se usan desde detector.py (envueltas en Presidio) y desde los tests.
"""
import re

LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"

# Tipo interno -> etiqueta del marcador que verá el LLM (ASCII, sin espacios)
ETIQUETAS = {
    "PERSON": "PERSONA",
    "LOCATION": "LUGAR",
    "ORGANIZATION": "ORGANIZACION",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "TELEFONO",
    "IBAN_CODE": "IBAN",
    "CREDIT_CARD": "TARJETA",
    "IP_ADDRESS": "IP",
    "ES_DNI": "DNI",
    "ES_NIE": "NIE",
    "ES_NSS": "NSS",
    "ES_DIRECCION": "DIRECCION",
    "ES_CP": "CP",
    "ES_MATRICULA": "MATRICULA",
    "FECHA": "FECHA",
    "ES_CUPS": "CUPS",
    "ES_CATASTRO": "CATASTRO",
    "ES_SANITARIO": "SANITARIO",
    "ES_REFERENCIA": "REFERENCIA",
    "DATO": "DATO",
}

# Tipos que la interfaz deja SIN marcar por defecto (suelen ser contexto útil)
DESMARCADOS_POR_DEFECTO = {"ORGANIZATION", "FECHA"}


def _solo_alnum(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", s).upper()


def valida_dni(texto: str) -> bool:
    s = _solo_alnum(texto)
    if not re.fullmatch(r"\d{7,8}[A-Z]", s):
        return False
    return LETRAS_DNI[int(s[:-1]) % 23] == s[-1]


def valida_nie(texto: str) -> bool:
    s = _solo_alnum(texto)
    if not re.fullmatch(r"[XYZ]\d{7}[A-Z]", s):
        return False
    num = "XYZ".index(s[0]) * 10_000_000 + int(s[1:8])
    return LETRAS_DNI[num % 23] == s[-1]


def _stdnum(modulo: str, texto: str):
    """Valida con python-stdnum (librería de referencia). Sin ella: None = no decide."""
    try:
        import importlib
        return importlib.import_module(f"stdnum.es.{modulo}").is_valid(texto)
    except ImportError:
        return None


def valida_cups(texto: str):
    return _stdnum("cups", texto)


def valida_catastro(texto: str):
    """True si cuadran los caracteres de control. Si no, None (no se descarta): algunas
    referencias rústicas reales no siguen el control, así que dependen del contexto."""
    return True if _stdnum("referenciacatastral", texto.replace(" ", "")) else None


def valida_nss(texto: str) -> bool:
    s = re.sub(r"\D", "", texto)
    if len(s) != 12:
        return False
    prov, num, dc = int(s[:2]), int(s[2:10]), int(s[10:])
    if not 1 <= prov <= 53:
        return False
    base = prov * 10_000_000 + num if num < 10_000_000 else int(s[:10])
    return base % 97 == dc


# (nombre, regex, puntuación base, flags propios o None = IGNORECASE por defecto)
PATRONES = {
    # Regla propia: el EmailRecognizer de Presidio usa tldextract, que descarga
    # la lista de sufijos públicos de Internet. Así no sale ninguna conexión.
    "EMAIL_ADDRESS": [
        ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}\b", 0.9, None),
    ],
    "ES_DNI": [
        ("dni", r"\b\d{1,2}\.?\d{3}\.?\d{3}[- ]?[A-Za-z]\b", 0.5, re.MULTILINE),
    ],
    "ES_NIE": [
        ("nie", r"\b[XYZxyz][- ]?\d{7}[- ]?[A-Za-z]\b", 0.5, re.MULTILINE),
    ],
    "ES_NSS": [
        ("nss", r"\b\d{2}[ /-]?\d{8}[ /-]?\d{2}\b", 0.4, re.MULTILINE),
    ],
    "ES_MATRICULA": [
        ("matricula", r"\b\d{4}[ -]?[BCDFGHJKLMNPRSTVWXYZ]{3}\b", 0.6, re.MULTILINE),
    ],
    "ES_CP": [
        # Solo puntúa por encima del umbral si hay contexto ("C.P.", "código postal"...)
        ("cp", r"\b(?:0[1-9]|[1-4]\d|5[0-2])\d{3}\b", 0.1, None),
    ],
    "ES_DIRECCION": [
        (
            # Vías en español y catalán: «C/ Mayor 12, 3º B», «Carrer de Balmes, 45», «Plaça Major s/n»
            "via",
            r"\b(?:C/|Calle|Carrer|Avda\.?|Av\.|Avenida|Avinguda|Plaza|Pza\.?|Pl\.|Pla[çc]a|"
            r"Paseo|Passeig|Pg\.|Pº|Passatge|Ptge\.|Camino|Cam[ií]|Ctra\.?|Carretera|Ronda|"
            r"Traves[ií]a|Travessera|Urb\.?|Urbanizaci[oó]n?|Urbanitzaci[oó]|Glorieta|Rambla)"
            r"[ \t]+[^\n,;]{2,50}?"
            r"(?:,?[ \t]*(?:(?:n[ºo°]\.?|n[uú]m\.?)?[ \t]*\d{1,4}[A-Za-z]?\b|s/n))"
            r"(?:,?[ \t]*\d{1,2}[ºª°]?[ \t]*[A-Za-z0-9]?[ªa]?\b)?",
            0.6,
            None,
        ),
        (
            # Inglés: «221B Baker Street», «10 Downing St.»
            "via_en",
            r"\b\d{1,5}[A-Za-z]?[ \t]+(?:[A-Z][\w'-]*[ \t]+){1,4}"
            r"(?:Street|St\.|Road|Rd\.|Avenue|Ave\.|Lane|Ln\.|Drive|Dr\.|Way|Close|Court|Ct\.|"
            r"Boulevard|Blvd\.|Place|Pl\.|Square|Sq\.|Crescent|Terrace|Gardens)(?=\W|$)",
            0.6,
            re.MULTILINE,
        ),
    ],
    # CUPS de luz/gas: ES + 16 dígitos + 2 letras de control (+ 2 opcionales de frontera)
    "ES_CUPS": [
        ("cups", r"\bES ?\d{4} ?\d{4} ?\d{4} ?\d{4} ?[A-Z]{2}(?: ?\d[A-Z])?\b", 0.5, re.MULTILINE),
    ],
    # Referencia catastral: 14 caracteres de finca/hoja + 4 de inmueble + 2 de control
    "ES_CATASTRO": [
        # 0.3: sin control válido solo pasa el umbral con contexto («catastral»…)
        ("catastro", r"\b[0-9A-Z]{7} ?[0-9A-Z]{7} ?\d{4} ?[A-Z]{2}\b", 0.3, re.MULTILINE),
    ],
    # Tarjeta sanitaria / historia clínica: formatos variados según comunidad,
    # así que solo cuenta si hay una palabra de contexto cerca («CIP», «NHC»…)
    "ES_SANITARIO": [
        ("cip", r"\b[A-Z]{4} ?\d{10}\b", 0.3, re.MULTILINE),
        ("generico", r"\b(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{5,19}\b", 0.05, re.MULTILINE),
    ],
    # Nº de póliza, cliente, contrato, expediente: solo con contexto
    "ES_REFERENCIA": [
        # (excluye fechas tipo 01/04/2025)
        ("referencia", r"\b(?!\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b)(?=[A-Z0-9/-]*\d{4})[A-Z0-9][A-Z0-9/-]{5,24}\b",
         0.05, re.MULTILINE),
    ],
    "FECHA": [
        (
            "fecha",
            r"\b(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}\b",
            0.5,
            None,
        ),
    ],
}

# Palabras de contexto en español, catalán e inglés (suben la puntuación si están cerca)
CONTEXTO = {
    "ES_DNI": ["dni", "nif", "documento", "document", "identidad", "identitat", "id"],
    "ES_NIE": ["nie", "extranjero", "estranger", "identidad", "identitat"],
    "ES_NSS": ["seguridad", "seguretat", "social", "nss", "afiliación", "afiliacion", "afiliació", "naf"],
    "ES_MATRICULA": ["matrícula", "matricula", "vehículo", "vehiculo", "vehicle", "plate"],
    "ES_CP": ["cp", "c.p", "postal", "código", "codigo", "codi", "zip"],
    "FECHA": ["nacimiento", "nacido", "nacida", "naixement", "nascut", "nascuda", "birth", "born", "dob"],
    "PHONE_NUMBER": ["teléfono", "telefono", "telèfon", "telefon", "tlf", "tel", "móvil", "movil", "mòbil",
                     "phone", "mobile", "fax"],
    "EMAIL_ADDRESS": ["correo", "correu", "email", "e-mail", "mail"],
    "IBAN_CODE": ["iban", "cuenta", "compte", "account", "banco", "banc", "bank"],
    "CREDIT_CARD": ["tarjeta", "targeta", "card", "visa", "mastercard"],
    "ES_CUPS": ["cups", "suministro", "subministrament"],
    "ES_CATASTRO": ["catastral", "catastro", "cadastral", "cadastre", "inmueble", "immoble"],
    "ES_SANITARIO": ["sanitaria", "sanitari", "cip", "cipa", "tsi", "sns", "nhc", "historia", "història",
                     "clínica", "clinica", "hc", "paciente", "pacient"],
    "ES_REFERENCIA": ["póliza", "poliza", "pòlissa", "polissa", "asegurado", "assegurat", "abonado",
                      "cliente", "client", "contrato", "contracte", "expediente", "expedient", "socio", "soci"],
}

VALIDADORES = {"ES_DNI": valida_dni, "ES_NIE": valida_nie, "ES_NSS": valida_nss,
               "ES_CUPS": valida_cups, "ES_CATASTRO": valida_catastro}


def tipo_desde(texto: str) -> str:
    """Acepta tipo interno (PERSON) o etiqueta (PERSONA); por defecto DATO."""
    t = texto.strip().upper()
    if t in ETIQUETAS:
        return t
    for interno, etiqueta in ETIQUETAS.items():
        if etiqueta == t:
            return interno
    return "DATO"


# Tratamientos que no forman parte del nombre: «D. Jordi Puig» -> «Jordi Puig»
_RE_TRATAMIENTO = re.compile(
    r"^(?:(?:D|Dª|Dña|Dn|Sr|Sra|Srta|Dr|Dra|Mr|Mrs|Ms|Miss|Sir|Prof)\.?|Don|Doña|Señor|Señora|"
    r"Senyor|Senyora|el\s+senyor|la\s+senyora|el\s+señor|la\s+señora)\s+",
    re.IGNORECASE,
)


def quita_tratamiento(valor: str) -> str:
    anterior = None
    while anterior != valor:  # «Sr. D. Juan» -> «Juan»
        anterior, valor = valor, _RE_TRATAMIENTO.sub("", valor, count=1)
    return valor


_SOLO_TRATAMIENTO = {"d", "dª", "dña", "dn", "don", "doña", "sr", "sra", "srta", "señor", "señora", "senyor",
                     "senyora", "dr", "dra", "mr", "mrs", "ms", "miss", "sir", "prof"}


def es_solo_tratamiento(valor: str) -> bool:
    return valor.strip().rstrip(".").casefold() in _SOLO_TRATAMIENTO
