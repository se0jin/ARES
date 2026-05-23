# ─────────────────────────────────────────────────────────────────────
# Dockerfile — 스마트팜 CO2 예측 API 컨테이너화 (07강)
# ─────────────────────────────────────────────────────────────────────
# 빌드:  docker build -t smartfarm-co2-api:latest .
# 실행:  docker run -p 8000:8000 \
#          -e SUPABASE_URL_DB2=https://xxx.supabase.co \
#          -e SUPABASE_KEY_DB2=your-key \
#          smartfarm-co2-api:latest
# ─────────────────────────────────────────────────────────────────────

# 1단계: 경량 Python 3.10 베이스 이미지
FROM python:3.10-slim

# 2단계: 작업 디렉토리 설정
WORKDIR /app

# 3단계: 의존성 파일 먼저 복사 (레이어 캐시 최적화)
#   requirements.txt가 바뀌지 않으면 이 레이어는 재사용됨 → 빌드 속도 향상
COPY requirements.txt .

# 4단계: 패키지 설치
RUN pip install --no-cache-dir -r requirements.txt

# 5단계: 소스코드 및 모델 복사
COPY app.py .
COPY models/ ./models/

# 6단계: 리포트 디렉토리 생성 (Evidently 리포트 저장용)
RUN mkdir -p reports

# 7단계: 포트 노출
EXPOSE 8000

# 8단계: 컨테이너 실행 시 FastAPI 서버 자동 시작
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
