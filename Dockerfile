FROM python:3.12-slim

LABEL org.opencontainers.image.title="anonimizador" \
      org.opencontainers.image.description="Anonimizador local de documentos (es/ca/en) previo a enviar a un LLM"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    HF_HOME=/srv/hf

# OCR: Tesseract (español + catalán + inglés + detección de orientación) y Ghostscript para OCRmyPDF
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-spa tesseract-ocr-cat tesseract-ocr-eng tesseract-ocr-osd \
      ghostscript pngquant \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
# Los modelos (spaCy es/ca/en y GLiNER) se descargan AHORA, al construir; en ejecución no se conecta a nada
# PyTorch solo CPU (mucho más ligero que la versión con CUDA) para GLiNER
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r requirements.txt \
 && python -m spacy download es_core_news_lg \
 && python -m spacy download ca_core_news_md \
 && python -m spacy download en_core_web_md \
 && python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_multi_pii-v1')" \
 && chmod -R a+rX /srv/hf

# En ejecución, nada de descargas: todo debe estar ya en la imagen
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin anon \
 && mkdir -p /data && chown 10001:10001 /data && chmod 700 /data
COPY --chown=root:root app ./app
COPY --chown=root:root VERSION ./VERSION

USER 10001
EXPOSE 8010
HEALTHCHECK --interval=60s --timeout=5s --start-period=90s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/salud',timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1", "--no-server-header"]
