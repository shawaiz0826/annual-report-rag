# annual-report-rag — API (default) or Streamlit UI
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# CPU-only torch first (small wheel from the PyTorch index), then the pinned stack
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

# Pre-download the embedding model so the container works offline after build
COPY config.yaml .
RUN python -c "import yaml; from sentence_transformers import SentenceTransformer; \
    m = yaml.safe_load(open('config.yaml'))['embeddings']['model']; SentenceTransformer(m, device='cpu'); print('cached', m)"

COPY core ./core
COPY api ./api
COPY apps ./apps
COPY scripts ./scripts
COPY eval ./eval
COPY data/raw ./data/raw
COPY data/derived ./data/derived

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)" || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
