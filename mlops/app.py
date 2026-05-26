"""
app.py — 스마트팜 CO2 예측 FastAPI 서버 (06강)
=====================================================================
엔드포인트:
  GET  /          헬스 체크
  GET  /model     현재 모델 정보 (버전, 피처 수)
  POST /predict   24개 피처 입력 → CO2 예측값 + 제어 명령 반환
  POST /predict/simple  원본 센서 7개만 입력 → 피처 자동 생성 후 예측

실행:
  uvicorn app:app --host 0.0.0.0 --port 8000 --reload

Swagger UI: http://localhost:8000/docs
"""

import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────
# 상수 & 모델 로드
# ─────────────────────────────────────────────────────────────────────
MODEL_PATH   = Path("./models/lgbm_co2.pkl")   # ★ 수정
CO2_CLIP_MIN = 300
CO2_CLIP_MAX = 2000

FEATURE_COLS = [
    "temp_in", "hum_in", "co2_in", "soil_hum",
    "temp_out", "rain_out", "wind_out",
    "temp_diff",
    "hour", "month", "day_of_week", "is_daytime",
    "hour_sin", "hour_cos", "month_sin", "month_cos",
    "temp_in_lag1",  "temp_in_lag2",          # ★ lag3 제거
    "hum_in_lag1",   "hum_in_lag2",           # ★ lag3 제거
    "co2_in_lag1",   "co2_in_lag2",           # ★ lag3 제거
    "soil_hum_lag1", "soil_hum_lag2",         # ★ lag3 제거
]

try:
    model = joblib.load(MODEL_PATH)
    print(f"✅ 모델 로드 완료: {MODEL_PATH}")
except FileNotFoundError:
    model = None
    print(f"⚠️  모델 파일 없음: {MODEL_PATH}  — /predict 호출 시 503 반환")

app = FastAPI(
    title="스마트팜 CO2 예측 API",
    description="LightGBM 기반 온실 CO2 2시간 후 예측 + 규칙 기반 제어 명령 API",
    version="1.1.0",
)


# ─────────────────────────────────────────────────────────────────────
# Pydantic 입력 모델 (06강)
# ─────────────────────────────────────────────────────────────────────
class CO2Input(BaseModel):
    """24개 피처 전체 직접 입력 (전처리 완료 상태)"""
    temp_in:       float = Field(..., example=25.3,  description="내부 온도 (°C)")
    hum_in:        float = Field(..., example=65.0,  description="내부 습도 (%)")
    co2_in:        float = Field(..., example=750.0, description="현재 CO2 (ppm) — 고장 시 이전 예측값 사용")
    soil_hum:      float = Field(..., example=35.0,  description="토양 수분 (%)")
    temp_out:      float = Field(..., example=18.0,  description="외부 온도 (°C)")
    rain_out:      float = Field(..., example=0.0,   description="강우 여부 (0=없음, 1=있음)")
    wind_out:      float = Field(..., example=2.5,   description="외부 풍속 (m/s)")
    temp_diff:     float = Field(..., example=7.3,   description="내외부 온도 차 (temp_in - temp_out)")
    hour:          float = Field(..., example=14.0,  description="시 (0~23)")
    month:         float = Field(..., example=5.0,   description="월 (1~12)")
    day_of_week:   float = Field(..., example=2.0,   description="요일 (0=월 ~ 6=일)")
    is_daytime:    float = Field(..., example=1.0,   description="주간 여부 (6~19시=1)")
    hour_sin:      float = Field(..., example=0.0,   description="시간 sin 인코딩")
    hour_cos:      float = Field(..., example=-1.0,  description="시간 cos 인코딩")
    month_sin:     float = Field(..., example=1.0,   description="월 sin 인코딩")
    month_cos:     float = Field(..., example=0.0,   description="월 cos 인코딩")
    temp_in_lag1:  float = Field(..., example=25.0)
    temp_in_lag2:  float = Field(..., example=24.8)
    hum_in_lag1:   float = Field(..., example=64.0)
    hum_in_lag2:   float = Field(..., example=63.5)
    co2_in_lag1:   float = Field(..., example=740.0)
    co2_in_lag2:   float = Field(..., example=730.0)
    soil_hum_lag1: float = Field(..., example=35.5)
    soil_hum_lag2: float = Field(..., example=36.0)


class SimpleSensorInput(BaseModel):
    """원본 센서 7개 입력 → 피처 자동 생성 (간편 엔드포인트)"""
    datetime_str:  Optional[str]   = Field(None, example="2025-05-23 14:00:00", description="측정 시각 (없으면 현재 시각 사용)")
    temp_in:       float = Field(..., example=25.3)
    hum_in:        float = Field(..., example=65.0)
    co2_in:        float = Field(..., example=750.0)
    soil_hum:      float = Field(..., example=35.0)
    temp_out:      float = Field(..., example=18.0)
    rain_out:      float = Field(0.0, example=0.0)
    wind_out:      float = Field(2.5, example=2.5)
    # lag 1~2만 사용 (★ lag3 제거)
    co2_in_lag1:   Optional[float] = Field(None, example=740.0)
    co2_in_lag2:   Optional[float] = Field(None, example=730.0)
    temp_in_lag1:  Optional[float] = Field(None, example=25.0)
    temp_in_lag2:  Optional[float] = Field(None, example=24.8)
    hum_in_lag1:   Optional[float] = Field(None, example=64.0)
    hum_in_lag2:   Optional[float] = Field(None, example=63.5)
    soil_hum_lag1: Optional[float] = Field(None, example=35.5)
    soil_hum_lag2: Optional[float] = Field(None, example=36.0)


# ─────────────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────────────
def _check_model():
    if model is None:
        raise HTTPException(status_code=503, detail=f"모델 파일 없음: {MODEL_PATH}")


def _predict_raw(feat_df: pd.DataFrame) -> float:
    pred = float(model.predict(feat_df)[0])
    return float(np.clip(pred, CO2_CLIP_MIN, CO2_CLIP_MAX))


def _build_control_summary(co2_pred: float, temp_in: float, hum_in: float,
                            rain_out: float, wind_out: float) -> dict:
    """간단 제어 상태 요약 반환 (상세 계산은 02_pipeline.py 참조)"""
    return {
        "ventilation_fan": "ON"  if (temp_in > 28 or co2_pred > 1000) and rain_out == 0 else "OFF",
        "window":          "ON"  if (temp_in > 28 or co2_pred > 1500) and rain_out == 0 and wind_out < 8 else "OFF",
        "heater":          "ON"  if temp_in < 12 else "OFF",
        "water_pump":      "ON"  if hum_in < 10 else "OFF",
        "co2_level":       "위험" if co2_pred > 1500 else ("주의" if co2_pred > 1000 else "정상"),
    }


# ─────────────────────────────────────────────────────────────────────
# 엔드포인트
# ─────────────────────────────────────────────────────────────────────
@app.get("/", summary="헬스 체크")
def read_root():
    return {
        "status":       "ok",
        "service":      "스마트팜 CO2 예측 API",
        "model_loaded": model is not None,
        "timestamp":    datetime.now().isoformat(),
    }


@app.get("/model", summary="현재 모델 정보")
def model_info():
    _check_model()
    return {
        "model_path":   str(MODEL_PATH),
        "model_type":   type(model).__name__,
        "best_iter":    getattr(model, "best_iteration_", "N/A"),
        "n_features":   len(FEATURE_COLS),
        "feature_cols": FEATURE_COLS,
        "target":       "next_co2_in (2시간 후 CO2 ppm)",   # ★ 수정
    }


@app.post("/predict", summary="CO2 예측 (24개 피처 전체 입력)")
def predict(input_data: CO2Input):
    """
    06강 FastAPI 예측 엔드포인트

    - 입력: 24개 피처 (전처리 완료 상태)
    - 출력: co2_predicted (2시간 후 CO2 ppm) + 제어 명령 요약
    """
    _check_model()

    row     = input_data.model_dump()
    feat_df = pd.DataFrame([[row[c] for c in FEATURE_COLS]], columns=FEATURE_COLS)

    co2_pred = _predict_raw(feat_df)
    control  = _build_control_summary(
        co2_pred, row["temp_in"], row["hum_in"], row["rain_out"], row["wind_out"]
    )

    return {
        "co2_predicted_ppm": round(co2_pred, 1),
        "prediction_target": "2시간 후 CO2 농도",           # ★ 수정
        "control_commands":  control,
        "timestamp":         datetime.now().isoformat(),
    }


@app.post("/predict/simple", summary="CO2 예측 (센서 7개 간편 입력)")
def predict_simple(input_data: SimpleSensorInput):
    """
    원본 센서값 7개만 입력하면 피처를 자동 생성하여 예측합니다.
    lag 값이 없으면 현재 센서값으로 대체합니다.
    """
    _check_model()

    dt = pd.to_datetime(input_data.datetime_str) if input_data.datetime_str \
         else pd.Timestamp.now()

    row = {
        "temp_in":       input_data.temp_in,
        "hum_in":        input_data.hum_in,
        "co2_in":        input_data.co2_in,
        "soil_hum":      input_data.soil_hum,
        "temp_out":      input_data.temp_out,
        "rain_out":      input_data.rain_out,
        "wind_out":      input_data.wind_out,
        "temp_diff":     input_data.temp_in - input_data.temp_out,
        "hour":          float(dt.hour),
        "month":         float(dt.month),
        "day_of_week":   float(dt.dayofweek),
        "is_daytime":    float(6 <= dt.hour <= 19),
        "hour_sin":      float(np.sin(2 * np.pi * dt.hour / 24)),
        "hour_cos":      float(np.cos(2 * np.pi * dt.hour / 24)),
        "month_sin":     float(np.sin(2 * np.pi * dt.month / 12)),
        "month_cos":     float(np.cos(2 * np.pi * dt.month / 12)),
        # lag1, lag2만 (★ lag3 제거)
        "temp_in_lag1":  input_data.temp_in_lag1  or input_data.temp_in,
        "temp_in_lag2":  input_data.temp_in_lag2  or input_data.temp_in,
        "hum_in_lag1":   input_data.hum_in_lag1   or input_data.hum_in,
        "hum_in_lag2":   input_data.hum_in_lag2   or input_data.hum_in,
        "co2_in_lag1":   input_data.co2_in_lag1   or input_data.co2_in,
        "co2_in_lag2":   input_data.co2_in_lag2   or input_data.co2_in,
        "soil_hum_lag1": input_data.soil_hum_lag1 or input_data.soil_hum,
        "soil_hum_lag2": input_data.soil_hum_lag2 or input_data.soil_hum,
    }

    feat_df  = pd.DataFrame([[row[c] for c in FEATURE_COLS]], columns=FEATURE_COLS)
    co2_pred = _predict_raw(feat_df)
    control  = _build_control_summary(
        co2_pred, row["temp_in"], row["hum_in"], row["rain_out"], row["wind_out"]
    )

    return {
        "co2_predicted_ppm": round(co2_pred, 1),
        "prediction_target": "2시간 후 CO2 농도",           # ★ 수정
        "control_commands":  control,
        "input_datetime":    dt.isoformat(),
        "timestamp":         datetime.now().isoformat(),
    }
