"""
app.py — 스마트팜 환경값 예측 FastAPI 서버 (06강) · nowcast(설계 A) / co2+temp 듀얼 모델
=====================================================================
설계:
  - DB2 교차 결측 시뮬레이션:
      홀수 시간 → co2_in = Null  (co2 센서 고장)  → co2 모델로 현재 co2 예측
      짝수 시간 → temp_in = Null (temp 센서 고장) → temp 모델로 현재 temp 예측
  - 두 모델 모두 'nowcast'(현재 시점 값 예측). 고장난 센서의 그 시각 값을 즉시 추정.
  - 단일 고장 가정: co2 예측 시 temp는 살아있고, temp 예측 시 co2는 살아있음(교차라 항상 성립).

핵심 동작:
  1) lgbm_co2_aug.pkl, lgbm_temp_aug.pkl 두 '번들'(dict) 로드
  2) 피처 목록은 각 번들의 features를 그대로 사용(하드코딩 X)
     - co2 모델 피처엔 co2_in 없음 / temp 모델 피처엔 temp_in·temp_diff 없음
  3) /predict/simple : 센서 입력 → Null인 센서를 자동 감지해 해당 모델로 예측
     (target을 명시하면 그걸 우선)
  4) 고장 센서의 lag는 미제공 시 NaN(장기 고장), 살아있는 센서 lag는 현재값으로 대체

엔드포인트:
  GET  /                헬스 체크 (로드된 모델 목록)
  GET  /model           두 모델 정보(번들 메타 + 피처)
  POST /predict         target + 전처리 완료 피처(dict) → 현재값 예측
  POST /predict/simple  원본 센서 입력 → 타겟 자동 감지 → 현재값 예측 + 제어

실행:  uvicorn app:app --host 0.0.0.0 --port 8000 --reload
"""

import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────
# 모델(번들) 로드 — target 이름(co2_in / temp_in) 기준으로 관리
#   ★ 파일명은 레포 mlops/models/ 실제 파일과 반드시 일치시킬 것
# ─────────────────────────────────────────────────────────────────────
MODEL_FILES = {
    "co2_in":  Path("./models/lgbm_co2_aug.pkl"),
    "temp_in": Path("./models/lgbm_temp_aug.pkl"),
}

BUNDLES: Dict[str, dict] = {}   # target -> {model, features, clip, ...}

for tgt, path in MODEL_FILES.items():
    try:
        b = joblib.load(path)
        if isinstance(b, dict) and "model" in b:
            b["clip"] = tuple(b.get("clip", (None, None)))
            b["features"] = list(b["features"])
            BUNDLES[tgt] = b
            print(f"✅ {tgt:8s} 로드: {path} | 피처 {len(b['features'])}개 | mode={b.get('mode','nowcast')}")
        else:
            print(f"⚠️  {tgt}: 번들 형식 아님({path}) — features 포함 번들로 재학습 권장")
    except FileNotFoundError:
        print(f"⚠️  {tgt}: 파일 없음 {path}")

app = FastAPI(
    title="스마트팜 환경 예측 API (nowcast · co2+temp)",
    description="LightGBM 기반 — 센서 고장 시 그 시각 값을 즉시 추정. 홀수시 co2 / 짝수시 temp 교차 예측.",
    version="3.0.0",
)

# 센서별 lag 컬럼 베이스
LAG_BASES = ["temp_in", "hum_in", "co2_in", "soil_hum"]


# ─────────────────────────────────────────────────────────────────────
# Pydantic 입력 모델
# ─────────────────────────────────────────────────────────────────────
class PredictInput(BaseModel):
    """전처리 완료 피처 직접 입력. target은 'co2_in' 또는 'temp_in'."""
    target:   str = Field(..., example="co2_in", description="예측 대상: co2_in 또는 temp_in")
    features: Dict[str, float] = Field(..., description="피처명:값 딕셔너리 (해당 모델 피처 일부/전부)")


class SimpleSensorInput(BaseModel):
    """
    원본 센서 입력 → 시간/lag 피처 자동 생성.
    고장난 센서값(co2_in 또는 temp_in)은 None으로 두면 그 센서를 예측 대상으로 자동 인식.
    """
    datetime_str: Optional[str] = Field(None, example="2025-05-23 13:00:00",
                                        description="측정 시각(없으면 현재 시각)")
    target:       Optional[str] = Field(None, example="co2_in",
                                        description="예측 대상 직접 지정(미지정 시 Null 센서 자동 감지)")
    # 센서 현재값 — 고장난 것은 None
    co2_in:   Optional[float] = Field(None, example=None,  description="현재 CO2(고장이면 None)")
    temp_in:  Optional[float] = Field(None, example=25.3,  description="현재 내부온도(고장이면 None)")
    hum_in:   float = Field(..., example=65.0)
    soil_hum: float = Field(..., example=35.0)
    temp_out: float = Field(..., example=18.0)
    rain_out: float = Field(0.0, example=0.0)
    wind_out: float = Field(2.5, example=2.5)
    # lag (있으면 정확도↑). 고장 센서 lag는 모르면 생략 → NaN
    co2_in_lag1:   Optional[float] = Field(None)
    co2_in_lag2:   Optional[float] = Field(None)
    co2_in_lag3:   Optional[float] = Field(None)
    temp_in_lag1:  Optional[float] = Field(None)
    temp_in_lag2:  Optional[float] = Field(None)
    temp_in_lag3:  Optional[float] = Field(None)
    hum_in_lag1:   Optional[float] = Field(None)
    hum_in_lag2:   Optional[float] = Field(None)
    hum_in_lag3:   Optional[float] = Field(None)
    soil_hum_lag1: Optional[float] = Field(None)
    soil_hum_lag2: Optional[float] = Field(None)
    soil_hum_lag3: Optional[float] = Field(None)


# ─────────────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────────────
def _get_bundle(target: str) -> dict:
    if target not in BUNDLES:
        raise HTTPException(status_code=503,
                            detail=f"'{target}' 모델 미로드. 로드됨: {list(BUNDLES.keys())}")
    return BUNDLES[target]


def _predict(target: str, row: dict) -> float:
    b = _get_bundle(target)
    feats, (lo, hi) = b["features"], b["clip"]
    X = pd.DataFrame([[row.get(c, np.nan) for c in feats]], columns=feats)
    pred = float(b["model"].predict(X)[0])
    return float(np.clip(pred, lo, hi)) if lo is not None else pred


def _build_control(co2: Optional[float], temp_in: Optional[float],
                   hum_in: float, rain_out: float, wind_out: float) -> dict:
    """현재 co2·temp 기반 간단 제어 요약(상세 계산은 02_pipeline.py)."""
    c = co2 if co2 is not None else 0.0
    t = temp_in if temp_in is not None else 20.0
    return {
        "ventilation_fan": "ON" if (t > 28 or c > 1000) and rain_out == 0 else "OFF",
        "window":          "ON" if (t > 28 or c > 1500) and rain_out == 0 and wind_out < 8 else "OFF",
        "heater":          "ON" if t < 12 else "OFF",
        "water_pump":      "ON" if hum_in < 10 else "OFF",
        "co2_level":       "위험" if c > 1500 else ("주의" if c > 1000 else "정상"),
    }


# ─────────────────────────────────────────────────────────────────────
# 엔드포인트
# ─────────────────────────────────────────────────────────────────────
@app.get("/", summary="헬스 체크")
def read_root():
    return {
        "status":        "ok",
        "service":       "스마트팜 환경 예측 API (nowcast · co2+temp)",
        "models_loaded": list(BUNDLES.keys()),
        "timestamp":     datetime.now().isoformat(),
    }


@app.get("/model", summary="모델 정보(두 모델)")
def model_info():
    if not BUNDLES:
        raise HTTPException(status_code=503, detail="로드된 모델 없음")
    out = {}
    for tgt, b in BUNDLES.items():
        out[tgt] = {
            "model_type":    type(b["model"]).__name__,
            "mode":          b.get("mode", "nowcast"),
            "target":        f"{tgt} (현재 시점 값)",
            "clip":          list(b["clip"]),
            "n_features":    len(b["features"]),
            "self_excluded": tgt not in b["features"],   # nowcast면 True여야 정상
            "feature_cols":  b["features"],
        }
    return out


@app.post("/predict", summary="현재값 예측 (target + 피처 dict)")
def predict(inp: PredictInput):
    _get_bundle(inp.target)
    row = dict(inp.features)
    base = inp.target
    for L in (1, 2, 3):                       # 고장 센서 lag 누락 시 NaN
        row.setdefault(f"{base}_lag{L}", np.nan)
    val = _predict(inp.target, row)
    return {
        "target":            inp.target,
        "predicted_value":   round(val, 1),
        "prediction_target": f"{inp.target} 현재 시점 값",
        "timestamp":         datetime.now().isoformat(),
    }


@app.post("/predict/simple", summary="현재값 예측 (센서 입력 → 타겟 자동 감지)")
def predict_simple(inp: SimpleSensorInput):
    """
    라즈베리파이/파이프라인이 호출. co2_in 또는 temp_in 중 None인 센서를 자동으로 예측 대상으로 인식.
    (홀수시 co2_in=None / 짝수시 temp_in=None)
    """
    # 1) 예측 대상 결정
    target = inp.target
    if target is None:
        if inp.co2_in is None and inp.temp_in is not None:
            target = "co2_in"
        elif inp.temp_in is None and inp.co2_in is not None:
            target = "temp_in"
        else:
            raise HTTPException(status_code=400,
                detail="예측 대상을 정할 수 없음 — co2_in/temp_in 중 정확히 하나만 None이거나 target을 지정하세요.")
    _get_bundle(target)

    dt = pd.to_datetime(inp.datetime_str) if inp.datetime_str else pd.Timestamp.now()
    d = inp.model_dump()

    cur = {"co2_in": inp.co2_in, "temp_in": inp.temp_in,
           "hum_in": inp.hum_in, "soil_hum": inp.soil_hum}

    row = {
        "hum_in": inp.hum_in, "soil_hum": inp.soil_hum,
        "temp_out": inp.temp_out, "rain_out": inp.rain_out, "wind_out": inp.wind_out,
        "hour": float(dt.hour), "month": float(dt.month),
        "day_of_week": float(dt.dayofweek), "is_daytime": float(6 <= dt.hour <= 19),
        "hour_sin":  float(np.sin(2 * np.pi * dt.hour / 24)),
        "hour_cos":  float(np.cos(2 * np.pi * dt.hour / 24)),
        "month_sin": float(np.sin(2 * np.pi * dt.month / 12)),
        "month_cos": float(np.cos(2 * np.pi * dt.month / 12)),
    }
    # 살아있는 센서 현재값(타겟 센서는 None → 피처에도 없음)
    if inp.co2_in is not None:
        row["co2_in"] = inp.co2_in
    if inp.temp_in is not None:
        row["temp_in"] = inp.temp_in
        row["temp_diff"] = inp.temp_in - inp.temp_out   # temp 살아있을 때만

    # lag 채우기
    for base in LAG_BASES:
        for L in (1, 2, 3):
            v = d.get(f"{base}_lag{L}")
            if v is not None:
                row[f"{base}_lag{L}"] = float(v)
            elif base == target:
                row[f"{base}_lag{L}"] = np.nan                  # 고장 센서 lag 모름
            elif cur.get(base) is not None:
                row[f"{base}_lag{L}"] = float(cur[base])        # 살아있는 센서 lag 없으면 현재값
            else:
                row[f"{base}_lag{L}"] = np.nan

    pred = _predict(target, row)
    row[target] = pred   # 제어 계산에 반영

    control = _build_control(row.get("co2_in"), row.get("temp_in"),
                             inp.hum_in, inp.rain_out, inp.wind_out)
    return {
        "target":            target,
        "predicted_value":   round(pred, 1),
        "prediction_target": f"{target} 현재 시점 값",
        "control_commands":  control,
        "input_datetime":    dt.isoformat(),
        "timestamp":         datetime.now().isoformat(),
    }
