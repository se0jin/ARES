# ─────────────────────────────────────────────────────────────────────
# Dockerfile — 스마트팜 CO2 예측 API 컨테이너화
# app.py (FastAPI) + 02_pipeline.py (스케줄러) 동시 실행
# ─────────────────────────────────────────────────────────────────────

FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .

RUN apt-get update && apt-get install -y libgomp1 git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY 02_pipeline.py .
COPY models/ ./models/

RUN mkdir -p reports

EXPOSE 8000

# app.py와 02_pipeline.py 동시 실행
CMD ["sh", "-c", "python 02_pipeline.py & uvicorn app:app --host 0.0.0.0 --port 8000"]
