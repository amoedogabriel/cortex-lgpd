FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependências de sistema necessárias para PyMuPDF, pikepdf (qpdf), python-magic (libmagic),
# PaddleOCR (libgl1, libglib2.0) e pytesseract (tesseract-ocr + idioma português)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libmagic1 \
    libqpdf-dev \
    tesseract-ocr \
    tesseract-ocr-por \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt && \
    python -m spacy download pt_core_news_lg

COPY . /app

# Cria diretório de armazenamento transitório com permissões restritas
RUN mkdir -p /tmp/ufam_redaction_spool && chmod 1770 /tmp/ufam_redaction_spool

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
