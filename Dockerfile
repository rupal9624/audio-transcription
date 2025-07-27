###############################################################################
# Dockerfile – Whisper + Flask on Python 3.11 Slim (CPU/GCP Cloud Run Ready) #
###############################################################################

FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ----------------------------- OS dependencies -------------------------------
# Includes build tools, ffmpeg, and libsndfile for torchaudio
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git build-essential curl ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

# Optional: Use CUDA wheels for torch (remove if using CPU only)
RUN mkdir -p /etc/pip \
 && printf "[global]\nextra-index-url = https://download.pytorch.org/whl/cu128\n" > /etc/pip.conf

WORKDIR /app

COPY requirements*.txt ./

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# -------------------------- Runtime Stage (slim) -----------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONWARNINGS="ignore:Unverified HTTPS request" \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install runtime dependencies (ffmpeg, libsndfile1 needed for torchaudio)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# Flask app entrypoint
CMD ["python", "main.py"]
