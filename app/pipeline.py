"""OCR, extracción de texto y tachado de PDF (todo local)."""
import shutil
import subprocess
from pathlib import Path

import fitz  # PyMuPDF

from .seudo import regex_valor

EXT_IMAGEN = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
EXT_ADMITIDAS = EXT_IMAGEN | {".pdf"}
MIN_CHARS_PAGINA = 20
OCR_TIMEOUT_S = 900
REDACT_IMG = getattr(fitz, "PDF_REDACT_IMAGE_PIXELS", 2)


class ErrorDocumento(Exception):
    pass


def _paginas_sin_texto(pdf: Path) -> int:
    with fitz.open(pdf) as doc:
        if doc.needs_pass:
            raise ErrorDocumento("El PDF está protegido con contraseña.")
        return sum(1 for p in doc if len(p.get_text("text").strip()) < MIN_CHARS_PAGINA)


def _ocr(entrada: Path, salida: Path, es_imagen: bool) -> None:
    cmd = [
        "ocrmypdf", "-l", "spa+cat+eng", "--skip-text", "--rotate-pages", "--deskew",
        "--output-type", "pdf", "--optimize", "0", "--jobs", "2", "--quiet",
    ]
    if es_imagen:
        cmd += ["--image-dpi", "300"]
    cmd += [str(entrada), str(salida)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=OCR_TIMEOUT_S)
    if r.returncode == 8:
        raise ErrorDocumento("El PDF está cifrado.")
    if r.returncode != 0:
        raise ErrorDocumento(f"Fallo de OCR (código {r.returncode}): {r.stderr.strip()[-400:]}")


def _aplana(pdf: Path) -> None:
    """Convierte formularios y anotaciones en contenido fijo para que su texto se analice."""
    with fitz.open(pdf) as doc:
        if hasattr(doc, "bake"):
            doc.bake(annots=True, widgets=True)
            doc.save(pdf.with_suffix(".tmp.pdf"), garbage=3, deflate=True)
    pdf.with_suffix(".tmp.pdf").replace(pdf)


def prepara(original: Path, trabajo: Path) -> bool:
    """Deja en `trabajo` un PDF con capa de texto. Devuelve True si se hizo OCR."""
    ext = original.suffix.lower()
    if ext not in EXT_ADMITIDAS:
        raise ErrorDocumento(f"Formato no admitido: {ext}")
    if ext in EXT_IMAGEN:
        _ocr(original, trabajo, es_imagen=True)
        return True
    if _paginas_sin_texto(original) == 0:
        shutil.copyfile(original, trabajo)
        _aplana(trabajo)
        return False
    _ocr(original, trabajo, es_imagen=False)
    _aplana(trabajo)
    return True


def extrae_paginas(pdf: Path) -> list[str]:
    with fitz.open(pdf) as doc:
        return [p.get_text("text") for p in doc]


def texto_con_paginas(paginas: list[str]) -> str:
    return "\n\n".join(f"--- Página {i} ---\n{t.strip()}" for i, t in enumerate(paginas, 1))


def tacha(entrada: Path, salida: Path, mapa: list[dict]) -> list[str]:
    """Tacha en negro cada aparición (con el marcador en blanco encima).

    Busca todas las variantes reales del valor (mayúsculas, saltos de línea)
    tal como aparecen en el texto de cada página. Devuelve avisos.
    """
    avisos: list[str] = []
    orden = sorted(mapa, key=lambda e: len(e["valor"]), reverse=True)
    with fitz.open(entrada) as doc:
        for n, pagina in enumerate(doc, 1):
            texto = pagina.get_text("text")
            hay = False
            usados: list = []
            for e in orden:
                variantes = {" ".join(m.group(0).split()) for m in regex_valor(e["valor"]).finditer(texto)}
                for v in variantes:
                    quads = pagina.search_for(v, quads=True)
                    if not quads:
                        avisos.append(f"Pág. {n}: no se pudo localizar «{v}» para tacharlo.")
                    for q in quads:
                        if any(r.contains(q.rect) for r in usados):
                            continue  # ya cubierto por un valor más largo
                        usados.append(q.rect)
                        pagina.add_redact_annot(
                            q, text=e["marcador"], fontname="helv", fontsize=6,
                            fill=(0, 0, 0), text_color=(1, 1, 1), cross_out=False,
                        )
                        hay = True
            for enlace in pagina.get_links():
                pagina.delete_link(enlace)
            if hay:
                pagina.apply_redactions(images=REDACT_IMG)

        doc.set_metadata({})
        doc.del_xml_metadata()
        for nombre in list(doc.embfile_names()):
            doc.embfile_del(nombre)
        doc.save(salida, garbage=4, deflate=True, clean=True)
    return avisos
