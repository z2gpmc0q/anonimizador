"""Genera los documentos ficticios de prueba (tests/fixtures). Datos inventados."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

DIR = Path(__file__).parent / "fixtures"
DIR.mkdir(exist_ok=True)
FUENTE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
pdfmetrics.registerFont(TTFont("DejaVu", FUENTE))

PAGINAS = {
    "es": [
        "CONTRATO DE ARRENDAMIENTO DE VIVIENDA",
        "",
        "En Madrid, a 3 de marzo de 2025.",
        "De una parte, D. Jordi Puig Ferrer, mayor de edad, con DNI 45127839E",
        "y domicilio en Calle Mayor 12, 3º B, 28013 Madrid.",
        "De otra parte, Dña. Laura Martínez Soto, con DNI 38291746N,",
        "teléfono 612 345 678 y correo laura.martinez@correo-ejemplo.es.",
        "La renta se abonará en la cuenta ES91 2100 0418 4502 0005 1332.",
        "Firmado: Laura Martínez Soto.",
    ],
    "ca": [
        "CONTRACTE DE PRESTACIÓ DE SERVEIS",
        "",
        "A Girona, la senyora Montserrat Vilaró Casals, amb NIE X1234567L,",
        "amb domicili al Carrer de Balmes, 45, 2n 1a, i el senyor",
        "Pere Soler Bosch acorden les condicions següents per a la reforma",
        "de l'habitatge. El pagament es farà per transferència bancària.",
    ],
    "en": [
        "SERVICE AGREEMENT",
        "",
        "This agreement is made between Mr. John Smith, residing at",
        "221B Baker Street, London, and Mrs. Emily Clarke, who can be",
        "reached at emily.clarke@example.co.uk or on +44 20 7946 0958.",
        "Both parties agree to the terms and conditions below.",
    ],
}


def pdf_digital():
    c = canvas.Canvas(str(DIR / "contrato_digital.pdf"), pagesize=A4)
    for lineas in PAGINAS.values():
        y = 780
        for linea in lineas:
            c.setFont("DejaVu", 12)
            c.drawString(60, y, linea)
            y -= 22
        c.showPage()
    c.setAuthor("Laura Martínez Soto")  # metadato con dato personal: debe desaparecer
    c.save()


def escaneado():
    w, h = 2480, 3508  # A4 a 300 ppp
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FUENTE, 46)
    y = 300
    for linea in PAGINAS["es"]:
        d.text((220, y), linea, font=f, fill=0)
        y += 90
    img = img.rotate(0.6, fillcolor=255)  # ligera inclinación, como un escáner real
    img.save(DIR / "contrato_escaneado.png", dpi=(300, 300))


if __name__ == "__main__":
    pdf_digital()
    escaneado()
    print("fixtures generados en", DIR)
