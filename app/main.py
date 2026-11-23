"""Anonimizador local de documentos - API + interfaz web."""
import json
import os
import re
import secrets
import shutil
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from . import pipeline, reglas, seudo
from .detector import Detector

USUARIO = os.environ.get("ANON_USER", "")
CLAVE = os.environ.get("ANON_PASSWORD", "")
DIR_DATOS = Path(os.environ.get("DATA_DIR", "/data"))
RETENCION_DIAS = float(os.environ.get("RETENTION_DAYS", "7"))
MAX_BYTES = int(os.environ.get("MAX_MB", "50")) * 1024 * 1024

if not USUARIO or len(CLAVE) < 12:
    raise SystemExit("Define ANON_USER y ANON_PASSWORD (mínimo 12 caracteres) en el archivo .env")

DIR_TRABAJOS = DIR_DATOS / "trabajos"
DIR_TRABAJOS.mkdir(parents=True, exist_ok=True)
# Listas editables desde la web; se guardan en el volumen de datos
DIR_LISTAS = DIR_DATOS / "listas"
DIR_LISTAS.mkdir(parents=True, exist_ok=True)
LISTAS = ("terminos", "permitidos")
for _n in LISTAS:
    if not (DIR_LISTAS / f"{_n}.txt").exists():
        shutil.copyfile(Path(__file__).parent / "listas_defecto" / f"{_n}.txt", DIR_LISTAS / f"{_n}.txt")
RE_ID = re.compile(r"^[0-9a-f]{32}$")
ESTATICO = Path(__file__).parent / "static"

app = FastAPI(title="Anonimizador", docs_url=None, redoc_url=None, openapi_url=None)
seguridad = HTTPBasic(realm="Anonimizador")
detector = Detector(DIR_LISTAS)


def autentica(cred: HTTPBasicCredentials = Depends(seguridad)) -> None:
    ok_u = secrets.compare_digest(cred.username.encode(), USUARIO.encode())
    ok_c = secrets.compare_digest(cred.password.encode(), CLAVE.encode())
    if not (ok_u and ok_c):
        time.sleep(1)
        raise HTTPException(401, "Credenciales incorrectas", headers={"WWW-Authenticate": "Basic"})


def _limpia_antiguos() -> None:
    limite = time.time() - RETENCION_DIAS * 86400
    for d in DIR_TRABAJOS.iterdir():
        if d.is_dir() and d.stat().st_mtime < limite:
            shutil.rmtree(d, ignore_errors=True)


def _dir(job: str) -> Path:
    if not RE_ID.match(job):
        raise HTTPException(400, "Identificador no válido")
    d = DIR_TRABAJOS / job
    if not d.is_dir():
        raise HTTPException(404, "Trabajo no encontrado (puede haber caducado)")
    return d


def _lee(d: Path, nombre: str):
    return json.loads((d / nombre).read_text(encoding="utf-8"))


def _escribe(d: Path, nombre: str, datos) -> None:
    (d / nombre).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


@app.get("/salud", include_in_schema=False)
def salud():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(autentica)])
def inicio():
    return (ESTATICO / "index.html").read_text(encoding="utf-8")


@app.post("/api/analizar", dependencies=[Depends(autentica)])
def analizar(archivo: UploadFile = File(...), terminos: str = Form(""), idioma: str = Form("auto")):
    _limpia_antiguos()
    nombre = Path(archivo.filename or "documento.pdf").name
    ext = Path(nombre).suffix.lower()
    if ext not in pipeline.EXT_ADMITIDAS:
        raise HTTPException(415, f"Formato no admitido ({ext}). Usa PDF, JPG, PNG o TIFF.")

    job = uuid.uuid4().hex
    d = DIR_TRABAJOS / job
    d.mkdir(mode=0o700)
    original = d / f"original{ext}"
    total = 0
    with original.open("wb") as f:
        while bloque := archivo.file.read(1024 * 1024):
            total += len(bloque)
            if total > MAX_BYTES:
                f.close()
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, "Archivo demasiado grande")
            f.write(bloque)

    try:
        ocr = pipeline.prepara(original, d / "trabajo.pdf")
        paginas = pipeline.extrae_paginas(d / "trabajo.pdf")
        entidades, idiomas = detector.analiza(paginas, terminos.splitlines(), idioma)
    except pipeline.ErrorDocumento as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(422, str(e))
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    finally:
        original.unlink(missing_ok=True)  # el original no se conserva

    info = {"id": job, "nombre": nombre, "creado": time.time(), "paginas": len(paginas), "ocr": ocr,
            "idiomas": sorted(set(idiomas))}
    _escribe(d, "info.json", info)
    _escribe(d, "paginas.json", paginas)
    _escribe(d, "entidades.json", entidades)
    return {**info, "entidades": entidades}


class PeticionGenerar(BaseModel):
    seleccion: list[int] = []
    extra: list[str] = []
    instruccion: bool = True


@app.post("/api/generar/{job}", dependencies=[Depends(autentica)])
def generar(job: str, p: PeticionGenerar):
    d = _dir(job)
    paginas = _lee(d, "paginas.json")
    entidades = {e["id"]: e for e in _lee(d, "entidades.json")}
    items = [{"valor": entidades[i]["valor"], "tipo": entidades[i]["tipo"]} for i in p.seleccion if i in entidades]
    for t in p.extra:
        valor, _, tipo = t.partition(";")
        if valor.strip():
            items.append({"valor": valor.strip(), "tipo": reglas.tipo_desde(tipo)})

    texto = pipeline.texto_con_paginas(paginas)
    mapa = seudo.construye_mapa(texto, items)
    salida = seudo.aplica(texto, mapa)
    if p.instruccion:
        salida = seudo.INSTRUCCION_LLM + salida

    avisos = pipeline.tacha(d / "trabajo.pdf", d / "tachado.pdf", mapa)
    # Verificación: ¿queda algún valor en el PDF tachado o en el texto?
    texto_pdf = "\n".join(pipeline.extrae_paginas(d / "tachado.pdf"))
    resto_pdf = seudo.residuales(texto_pdf, mapa)
    resto_txt = seudo.residuales(salida, mapa)

    (d / "texto.txt").write_text(salida, encoding="utf-8")
    _escribe(d, "mapa.json", mapa)
    os.utime(d)
    return {"texto": salida, "mapa": mapa, "avisos": avisos,
            "residuales_pdf": resto_pdf, "residuales_texto": resto_txt}


@app.get("/api/descargar/{job}/{fichero}", dependencies=[Depends(autentica)])
def descargar(job: str, fichero: str):
    d = _dir(job)
    if fichero not in {"tachado.pdf", "texto.txt"} or not (d / fichero).is_file():
        raise HTTPException(404, "No disponible")
    base = Path(_lee(d, "info.json")["nombre"]).stem
    sufijo = "_anonimizado.pdf" if fichero.endswith(".pdf") else "_anonimizado.txt"
    return FileResponse(d / fichero, filename=base + sufijo)


@app.get("/api/trabajos", dependencies=[Depends(autentica)])
def trabajos():
    _limpia_antiguos()
    out = []
    for d in DIR_TRABAJOS.iterdir():
        if (d / "mapa.json").is_file():
            info = _lee(d, "info.json")
            info["marcadores"] = len(_lee(d, "mapa.json"))
            out.append(info)
    return sorted(out, key=lambda i: i["creado"], reverse=True)


class PeticionRestaurar(BaseModel):
    texto: str


@app.post("/api/restaurar/{job}", dependencies=[Depends(autentica)])
def restaurar(job: str, p: PeticionRestaurar):
    d = _dir(job)
    texto, desconocidos = seudo.restaura(p.texto, _lee(d, "mapa.json"))
    return {"texto": texto, "desconocidos": desconocidos}


@app.delete("/api/trabajos/{job}", dependencies=[Depends(autentica)])
def borrar(job: str):
    shutil.rmtree(_dir(job))
    return {"ok": True}


class PeticionListas(BaseModel):
    terminos: str
    permitidos: str


@app.get("/api/listas", dependencies=[Depends(autentica)])
def leer_listas():
    return {n: (DIR_LISTAS / f"{n}.txt").read_text(encoding="utf-8") for n in LISTAS}


@app.put("/api/listas", dependencies=[Depends(autentica)])
def guardar_listas(p: PeticionListas):
    for n in LISTAS:
        contenido = getattr(p, n)
        if len(contenido) > 200_000:
            raise HTTPException(413, f"La lista {n} es demasiado larga")
        tmp = DIR_LISTAS / f"{n}.txt.tmp"
        tmp.write_text(contenido.replace("\r\n", "\n"), encoding="utf-8")
        tmp.replace(DIR_LISTAS / f"{n}.txt")
    return {"ok": True}
