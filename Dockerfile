# ── Stage 1: Build Next.js frontend ─────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
# Install ALL deps (incl. devDependencies): next build needs typescript,
# @types/*, tailwindcss, postcss, autoprefixer at build time. Dev deps never
# reach the runtime image — this is a multi-stage build and only .next output
# is copied to stage 2, so Jest etc. are excluded regardless.
RUN npm install --legacy-peer-deps
COPY frontend/ .
RUN npm run build

# ── Stage 2: Combined runtime ────────────────────────────────────────────────
FROM python:3.11-slim

ENV HF_HOME=/app/.hf_cache \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=production

# System deps: Python build tools + Node.js 20
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (backend + crawler, so this image can also run the re-index job)
COPY backend/requirements.txt ./requirements-backend.txt
COPY crawler/requirements.txt ./requirements-crawler.txt
# pinned-requirements.txt pins exact versions so builds are reproducible (see the file
# header; fill it with scripts/lock_deps.sh). Empty constraints = no pins.
COPY pinned-requirements.txt ./pinned-requirements.txt
# CPU-only PyTorch. Cloud Run has no GPU, but the default Linux wheel bundles
# the CUDA runtime (several GB of nvidia-* packages) that is downloaded, stored
# and pulled on every cold start for nothing. Installing torch from the CPU
# index first means the later install sees it as already satisfied.
RUN pip install --no-cache-dir -c pinned-requirements.txt \
        --index-url https://download.pytorch.org/whl/cpu torch
RUN pip install --no-cache-dir -c pinned-requirements.txt \
        -r requirements-backend.txt -r requirements-crawler.txt

# Pre-bake ML models into the image so cold starts don't pay the download cost
RUN python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Also bake the int8 ONNX build of the reranker (23 MB), used when
# RERANK_BACKEND=onnx. Must be downloaded here: the runtime is offline.
RUN python -c "\
from huggingface_hub import hf_hub_download; \
[hf_hub_download('cross-encoder/ms-marco-MiniLM-L6-v2', f) for f in \
 ('onnx/model_quint8_avx2.onnx', 'config.json', 'tokenizer.json', \
  'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt')]"

# Models are baked into the image cache above. Force offline mode at RUNTIME so
# startup uses the local cache instead of HEAD-checking HuggingFace for updates.
# Those checks were returning HTTP 429 (rate-limited) and adding ~2.5 minutes to
# every cold start. Set AFTER the bake step so the download above still works.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Backend source + crawler (used by the ecen-reindex Cloud Run Job)
COPY backend/ ./backend/
COPY crawler/ ./crawler/

# Next.js standalone output
COPY --from=frontend-builder /app/.next/standalone ./frontend/
COPY --from=frontend-builder /app/.next/static     ./frontend/.next/static
COPY --from=frontend-builder /app/public           ./frontend/public

# Startup script
COPY start.sh ./start.sh
RUN chmod +x ./start.sh

EXPOSE 8080
CMD ["./start.sh"]
