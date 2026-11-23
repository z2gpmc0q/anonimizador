"""Prueba de extremo a extremo contra el contenedor en marcha (la ejecuta GitHub Actions).

Uso: python3 tests/e2e.py http://127.0.0.1:8010 usuario contraseña
Solo usa la biblioteca estándar.
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE, USER, CLAVE = sys.argv[1], sys.argv[2], sys.argv[3]
FIX = Path(__file__).parent / "fixtures"
AUTH = "Basic " + base64.b64encode(f"{USER}:{CLAVE}".encode()).decode()
fallos: list[str] = []


def peticion(metodo, ruta, cuerpo=None, tipo=None, auth=True, crudo=False):
    req = urllib.request.Request(BASE + ruta, data=cuerpo, method=metodo)
    if auth:
        req.add_header("Authorization", AUTH)
    if tipo:
        req.add_header("Content-Type", tipo)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            datos = r.read()
            return r.status, (datos if crudo else json.loads(datos))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def multipart(ruta_archivo: Path, campos: dict):
    lim = uuid.uuid4().hex
    partes = []
    for k, v in campos.items():
        partes.append(f'--{lim}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    partes.append(
        f'--{lim}\r\nContent-Disposition: form-data; name="archivo"; filename="{ruta_archivo.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode() + ruta_archivo.read_bytes() + b"\r\n"
    )
    partes.append(f"--{lim}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={lim}"


def comprueba(cond, texto):
    print(("  OK   " if cond else "  FALLO ") + texto)
    if not cond:
        fallos.append(texto)


def espera_salud():
    for _ in range(90):
        try:
            with urllib.request.urlopen(BASE + "/salud", timeout=3) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(2)
    sys.exit("El contenedor no respondió en /salud")


def analiza_y_genera(archivo: Path, idioma="auto", conversacion=""):
    cuerpo, tipo = multipart(archivo, {"terminos": "", "idioma": idioma, "conversacion": conversacion,
                                       "nombre_conversacion": "Prueba CI"})
    t0 = time.time()
    st, a = peticion("POST", "/api/analizar", cuerpo, tipo)
    comprueba(st == 200, f"{archivo.name}: análisis responde 200 ({time.time() - t0:.1f} s) {'' if st == 200 else a}")
    if st != 200:
        return None, None
    seleccion = [e["id"] for e in a["entidades"] if e["marcado"] and not e.get("marcador")]
    st, g = peticion("POST", f"/api/generar/{a['id']}",
                     json.dumps({"seleccion": seleccion, "extra": [], "instruccion": True}).encode(),
                     "application/json")
    comprueba(st == 200, f"{archivo.name}: generación responde 200")
    return a, (g if st == 200 else None)


espera_salud()
print("Contenedor listo")

print("\n[Seguridad]")
st, _ = peticion("GET", "/api/trabajos", auth=False)
comprueba(st == 401, "sin credenciales → 401")

print("\n[Listas]")
st, listas = peticion("GET", "/api/listas")
comprueba(st == 200 and "terminos" in listas, "leer listas")
nuevo = listas["terminos"] + "\nPere Soler Bosch;PERSONA\n"
st, _ = peticion("PUT", "/api/listas", json.dumps({"terminos": nuevo, "permitidos": listas["permitidos"]}).encode(),
                 "application/json")
comprueba(st == 200, "guardar listas")

print("\n[PDF digital es/ca/en]")
a, g = analiza_y_genera(FIX / "contrato_digital.pdf")
if a and g:
    valores = {e["valor"]: e for e in a["entidades"]}
    tipos = {e["tipo"] for e in a["entidades"]}
    comprueba(set(a["idiomas"]) == {"es", "ca", "en"}, f"idiomas detectados {a['idiomas']}")
    comprueba(a["ocr"] is False, "no aplica OCR a un PDF digital")
    for v in ["45127839E", "38291746N", "X1234567L", "ES91 2100 0418 4502 0005 1332",
              "laura.martinez@correo-ejemplo.es", "emily.clarke@example.co.uk"]:
        comprueba(v in valores, f"detecta {v}")
    comprueba(any("612 345 678" in v for v in valores), "detecta teléfono español")
    comprueba(any("Laura Martínez Soto" in v for v in valores), "detecta persona (es)")
    comprueba(any("Montserrat Vilaró" in v for v in valores), "detecta persona (ca)")
    comprueba(any("John Smith" in v for v in valores), "detecta persona (en)")
    comprueba(any(v.startswith("Calle Mayor 12") for v in valores), "detecta dirección (es)")
    comprueba(any(v.startswith("Carrer de Balmes") for v in valores), "detecta dirección (ca)")
    comprueba(any("Baker Street" in v for v in valores), "detecta dirección (en)")
    comprueba(valores.get("Pere Soler Bosch", {}).get("origen") == "lista", "aplica la lista 'siempre anonimizar'")
    comprueba(g["residuales_pdf"] == [], f"PDF tachado sin restos {g['residuales_pdf']}")
    comprueba(g["residuales_texto"] == [], "texto sin restos")
    comprueba("45127839E" not in g["texto"] and "[DNI_1]" in g["texto"], "texto seudonimizado")
    st, pdf = peticion("GET", f"/api/descargar/{a['id']}/tachado.pdf", crudo=True)
    comprueba(st == 200 and pdf[:4] == b"%PDF", "descarga del PDF tachado")
    comprueba(b"Laura" not in pdf and b"45127839" not in pdf, "PDF tachado sin datos ni metadatos en claro")
    respuesta = "Según el contrato, [PERSONA_1] y PERSONA_2 firman; DNI [DNI_1]."
    st, r = peticion("POST", f"/api/restaurar/{a['conversacion']}", json.dumps({"texto": respuesta}).encode(),
                     "application/json")
    real = {m["marcador"]: m["valor"] for m in g["mapa"]}
    comprueba(st == 200 and real["DNI_1"] in r["texto"] and real["PERSONA_2"] in r["texto"], "restauración")
    marcador_dni = {m["valor"]: m["marcador"] for m in g["mapa"]}.get("45127839E")
    conv1 = a["conversacion"]
    st, salud = peticion("GET", "/salud", auth=False)
    comprueba("gliner" in salud.get("modelos_cargados", []), f"GLiNER cargado {salud.get('modelos_cargados')}")
    print("  INFO  detecciones aportadas por GLiNER:",
          [e["valor"] for e in a["entidades"] if e["origen"] == "gliner"])

print("\n[Imagen escaneada, en la MISMA conversación]")
a, g = analiza_y_genera(FIX / "contrato_escaneado.png", conversacion=conv1)
if a and g:
    comprueba(a["conversacion"] == conv1, "se añade a la conversación existente")
    previo = {e["valor"]: e.get("marcador") for e in a["entidades"]}
    comprueba(previo.get("45127839E") == marcador_dni, f"el DNI conserva su marcador ({marcador_dni})")
    comprueba(f"[{marcador_dni}]" in g["texto"], "el texto usa el mismo marcador que el primer documento")
    valores = {e["valor"] for e in a["entidades"]}
    comprueba(a["ocr"] is True, "aplica OCR")
    comprueba("45127839E" in valores, "detecta DNI tras OCR")
    comprueba(any("2100 0418" in v for v in valores), "detecta IBAN tras OCR")
    comprueba(g["residuales_pdf"] == [], f"PDF escaneado tachado sin restos {g['residuales_pdf']}")
    comprueba(not g["avisos"], f"sin avisos de tachado {g['avisos']}")

print("\n[Limpieza]")
st, t = peticion("GET", "/api/conversaciones")
comprueba(st == 200 and len(t) == 1 and len(t[0]["documentos"]) == 2, "una conversación con 2 documentos")
for c in t:
    st, _ = peticion("DELETE", f"/api/conversaciones/{c['id']}")
comprueba(st == 200, "borrar conversación")
st, t = peticion("GET", "/api/conversaciones")
comprueba(st == 200 and t == [], "no queda nada")

print(f"\n{'TODO CORRECTO' if not fallos else f'{len(fallos)} FALLO(S)'}")
sys.exit(1 if fallos else 0)
