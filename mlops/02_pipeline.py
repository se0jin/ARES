"""
02_pipeline.py — 스마트팜 환경 예측 실시간 MLOps 파이프라인  (v3.0)
=====================================================================
[v3.0 주요 변경 — 모델 비교·결측 강건성 실험 결과 반영]
  ① 모델 로딩: bare pkl → 번들(dict: model+features+clip+fault_sensor) 로딩
       └ 피처 순서/클리핑이 모델과 함께 따라와 '피처 불일치' 버그 차단
  ② 다중 센서: CO2 단일 예측 → CO2 + 온도 단일 고장 예측 (SENSORS 설정)
  ③ 결측 학습(augmentation): 재학습 시 고장 센서를 마스킹한 복제본을 추가 학습
       └ 안 하면 재학습 때마다 결측 취약 모델로 회귀 (R²<0 붕괴)
  ④ 고장 센서 현재값을 기본값으로 채우지 않고 NaN 그대로 모델에 전달
       └ augmentation 모델이 결측을 학습된 경로로 처리 (ffill/bfill 제거)
  ⑤ 예측 시점 3시간으로 통일 (PREDICT_HORIZON_H=3, shift(-3), t→t+3h 매칭)
       └ 기존 v2.2가 2h였으나 전처리/모델이 3h이므로 정합
  ⑥ 재학습 성능 게이트를 '고장(마스킹) 시나리오'로 평가 → augmentation 모델 정당 비교

[배포 전 반드시 확인]
  - models/lgbm_co2_aug.pkl, models/lgbm_temp_aug.pkl 가 번들 형식으로 존재
  - DB2(sensor_data_2)에 temp_predicted 컬럼 추가 (UNIQUE 제약 걸지 말 것)
  - DB3(sensor_data_3)에 temp_in 컬럼 추가 + 하드웨어가 실제 온도 적재
  - PREDICT_HORIZON_H 값이 전처리 shift 와 동일한지 확인
"""

# ─────────────────────────────────────────────────────────────────────
# 0. 임포트 & 설정
# ─────────────────────────────────────────────────────────────────────
import copy
import logging
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import requests
import schedule
import time
import lightgbm as lgb
from scipy import stats
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from supabase import create_client, Client

warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("pipeline.log", encoding="utf-8")],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# 1. 환경 변수 & 상수
# ─────────────────────────────────────────────────────────────────────
import os

SUPABASE_URL_DB1 = os.getenv("SUPABASE_URL_DB1", "https://your-db1.supabase.co")
SUPABASE_KEY_DB1 = os.getenv("SUPABASE_KEY_DB1", "your-db1-key")
SUPABASE_URL_DB2 = os.getenv("SUPABASE_URL_DB2", "https://your-db2.supabase.co")
SUPABASE_KEY_DB2 = os.getenv("SUPABASE_KEY_DB2", "your-db2-key")
SUPABASE_URL_DB3 = os.getenv("SUPABASE_URL_DB3", "https://your-db3.supabase.co")
SUPABASE_KEY_DB3 = os.getenv("SUPABASE_KEY_DB3", "your-db3-key")

DB2_TABLE_NAME = "sensor_data_2"
DB3_TABLE_NAME = "sensor_data_3"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
EXPERIMENT_NAME     = "smartfarm_env_monitoring"

# ── ⑤ 예측 시점 (전처리 shift 와 반드시 동일) ───────────────────────
PREDICT_HORIZON_H = 3                 # 3시간 후 예측 (shift(-3))

# ── 재학습 트리거 임계값 ────────────────────────────────────────────
RETRAIN_R2_THRESHOLD  = 0.70
KS_PVALUE_THRESHOLD   = 0.05
RETRAIN_WINDOW        = 48
RETRAIN_DATA_MIN_ROWS = 500
RETRAIN_SCHEDULE_HOUR = 2
DISCORD_R2_THRESHOLD  = 0.60

# ── ② 센서 설정 (CO2 + 온도 단일 고장) ─────────────────────────────
#   나머지(model/features/clip/fault_sensor/target)는 번들에서 로드
SENSORS = {
    "co2": {
        "label":        "CO2",
        "unit":         "ppm",
        "bundle_path":  "./models/lgbm_co2_aug.pkl",
        "registry":     "smartfarm_co2_lgbm",
        "null_col":     "co2_in",         # 고장 시 NULL 인 입력 컬럼
        "pred_col":     "co2_predicted",  # 예측값 저장 컬럼
        "db3_col":      "co2_in",         # DB3 실제값(모니터링) 컬럼
    },
    "temp": {
        "label":        "온도",
        "unit":         "°C",
        "bundle_path":  "./models/lgbm_temp_aug.pkl",
        "registry":     "smartfarm_temp_lgbm",
        "null_col":     "temp_in",
        "pred_col":     "temp_predicted",
        "db3_col":      "temp_in",
    },
}
# 고장 센서 현재값 → 예측값 컬럼 매핑 (lag 재활용용)
PRED_COL_OF = {c["null_col"]: c["pred_col"] for c in SENSORS.values()}

LAG_COLS_RAW = ["temp_in", "hum_in", "co2_in", "soil_hum"]

# ── 비-고장 컨텍스트 피처 기본값 (없을 때만 보정) ───────────────────
CONTEXT_DEFAULT = {
    "temp_in": 20.0, "hum_in": 60.0, "co2_in": 700.0, "soil_hum": 30.0,
    "temp_out": 15.0, "rain_out": 0.0, "wind_out": 2.0,
}

# ── LightGBM 재학습 하이퍼파라미터 ──────────────────────────────────
LGB_PARAMS = dict(
    n_estimators=1000, learning_rate=0.05, max_depth=6, num_leaves=63,
    min_child_samples=20, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
    reg_alpha=0.1, reg_lambda=1.0, objective="regression", metric="rmse",
    random_state=42, n_jobs=-1, verbose=-1,
)


# ─────────────────────────────────────────────────────────────────────
# 2. 초기화 & 모델(번들) 로딩
# ─────────────────────────────────────────────────────────────────────
def init_clients() -> tuple[Client, Client, Client]:
    db1 = create_client(SUPABASE_URL_DB1, SUPABASE_KEY_DB1)
    db2 = create_client(SUPABASE_URL_DB2, SUPABASE_KEY_DB2)
    db3 = create_client(SUPABASE_URL_DB3, SUPABASE_KEY_DB3)
    log.info("Supabase 클라이언트 초기화 완료 (DB1/DB2/DB3)")
    return db1, db2, db3


def load_bundle(path: str | Path) -> dict:
    """
    ① 번들 로딩. 저장 형식:
       {model, features, clip:(lo,hi), fault_sensor, target, ...}
    구버전 bare 모델이면 최소 번들로 감싼다(피처/클립은 폴백).
    """
    obj = joblib.load(path)
    if isinstance(obj, dict) and "model" in obj:
        b = obj
    else:  # 폴백: bare 모델
        b = {"model": obj, "features": None, "clip": (None, None),
             "fault_sensor": None, "target": None}
        log.warning(f"{path}: 번들이 아닌 bare 모델 — 피처/클립 정보 없음")
    b["lag_depth"] = _lag_depth(b.get("features"))
    log.info(
        f"모델 로드: {path}  타겟={b.get('target')}  "
        f"피처={len(b['features']) if b.get('features') else '?'}개  "
        f"clip={b.get('clip')}  lag_depth={b['lag_depth']}"
    )
    return b


def _lag_depth(features) -> int:
    if not features:
        return PREDICT_HORIZON_H
    idxs = []
    for f in features:
        m = re.search(r"_lag(\d+)$", f)
        if m:
            idxs.append(int(m.group(1)))
    return max(idxs) if idxs else 0


def init_mlflow():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)
    log.info(f"MLflow 초기화: {MLFLOW_TRACKING_URI} / {EXPERIMENT_NAME}")


# ─────────────────────────────────────────────────────────────────────
# 3. 피처 엔지니어링 (추론용)
# ─────────────────────────────────────────────────────────────────────
def build_features(target_row: dict, lag_rows: list[dict],
                   features: list[str], fault_base: str) -> pd.DataFrame:
    """
    DB2 신규 1행 + 직전 lag 행 → 모델 피처 DataFrame(1행)

    ④ 핵심: 고장 센서(fault_base)의 현재값·현재 파생은 NaN 그대로 둔다.
       augmentation 모델이 결측을 학습된 경로로 처리하므로 임의 값으로
       채우지 않는다. (lag 는 예측값 재활용으로 채움 = mild 패턴)
    """
    row = dict(target_row)
    dt = pd.to_datetime(row["datetime"])

    # 시간 파생
    row["hour"]        = dt.hour
    row["month"]       = dt.month
    row["day_of_week"] = dt.dayofweek
    row["is_daytime"]  = float(6 <= dt.hour <= 19)
    row["hour_sin"]    = np.sin(2 * np.pi * dt.hour / 24)
    row["hour_cos"]    = np.cos(2 * np.pi * dt.hour / 24)
    row["month_sin"]   = np.sin(2 * np.pi * dt.month / 12)
    row["month_cos"]   = np.cos(2 * np.pi * dt.month / 12)

    def ctx(col):  # 비-고장 컨텍스트 값 (없으면 기본값)
        v = row.get(col)
        return float(v) if v is not None else CONTEXT_DEFAULT.get(col, np.nan)

    temp_out = ctx("temp_out"); row["temp_out"] = temp_out

    # temp_in / temp_diff
    if fault_base == "temp_in":
        row["temp_in"]   = np.nan          # ④ 고장 → NaN 유지
        row["temp_diff"] = np.nan
    else:
        ti = ctx("temp_in"); row["temp_in"] = ti
        row["temp_diff"] = ti - temp_out

    # co2_in
    row["co2_in"] = np.nan if fault_base == "co2_in" else ctx("co2_in")  # ④

    # 기타 센서
    row["hum_in"]   = ctx("hum_in")
    row["soil_hum"] = ctx("soil_hum")
    row["rain_out"] = ctx("rain_out")
    row["wind_out"] = ctx("wind_out")

    # lag 피처 — 고장 센서 lag 는 예측값 재활용, 나머지는 실측
    depth = _lag_depth(features)
    for lag_idx, lag_row in enumerate(lag_rows[:depth], start=1):
        for col in LAG_COLS_RAW:
            if col == fault_base and col in PRED_COL_OF:
                val = lag_row.get(PRED_COL_OF[col]) or lag_row.get(col)
            else:
                val = lag_row.get(col)
            row[f"{col}_lag{lag_idx}"] = float(val) if val is not None else np.nan

    feat_df = pd.DataFrame([{k: row.get(k, np.nan) for k in features}])
    for col in feat_df.columns:
        feat_df[col] = pd.to_numeric(feat_df[col], errors="coerce")
    # ④ ffill/bfill 제거 — 고장 센서 NaN 을 모델이 직접 처리
    return feat_df


# ─────────────────────────────────────────────────────────────────────
# 4. DB2 폴링 & 예측 (CO2 + 온도)
# ─────────────────────────────────────────────────────────────────────
def poll_and_predict(db2: Client, bundles: dict[str, dict]) -> list[dict]:
    """
    각 센서별로 null_col=Null + pred_col=Null 행 감지 → 예측 → DB2 업데이트.
    반환: 예측이 채워진 행 목록(센서 정보 포함).
    """
    predicted = []

    for name, cfg in SENSORS.items():
        b = bundles[name]
        if b.get("features") is None:
            log.warning(f"[{name}] 번들 피처 정보 없음 — 예측 스킵")
            continue

        res = (
            db2.table(DB2_TABLE_NAME).select("*")
            .is_(cfg["null_col"], "null")
            .is_(cfg["pred_col"], "null")
            .order("datetime", desc=False).limit(10).execute()
        )
        rows = res.data
        if not rows:
            log.info(f"[{name}] 신규 Null 행 없음")
            continue
        log.info(f"[{name}] 감지된 Null 행: {len(rows)}개")

        depth = b["lag_depth"]
        lo, hi = b["clip"]

        for r in rows:
            try:
                dt_str = r["datetime"]
                lag_res = (
                    db2.table(DB2_TABLE_NAME).select("*")
                    .lt("datetime", dt_str)
                    .order("datetime", desc=True).limit(depth).execute()
                )
                lag_rows = lag_res.data
                if len(lag_rows) < depth:
                    log.warning(f"[{name}] lag 행 부족 ({len(lag_rows)}/{depth}) — 스킵: {dt_str}")
                    continue

                feat_df = build_features(r, lag_rows, b["features"], cfg["null_col"])
                raw = float(b["model"].predict(feat_df)[0])
                pred = float(np.clip(raw, lo, hi)) if lo is not None else raw

                db2.table(DB2_TABLE_NAME).update(
                    {cfg["pred_col"]: round(pred, 2)}
                ).eq("ID", r["ID"]).execute()

                log.info(f"  [{name}][{dt_str}] {cfg['pred_col']} = {pred:.1f} {cfg['unit']}")
                r[cfg["pred_col"]] = pred
                r["_sensor"] = name
                predicted.append(r)

            except Exception as e:
                log.error(f"[{name}] 예측 오류 ({r.get('datetime', '?')}): {e}")

    return predicted


# ─────────────────────────────────────────────────────────────────────
# 5. 규칙 기반 제어 — 4채널 릴레이  (기존 로직 유지)
# ─────────────────────────────────────────────────────────────────────
def compute_control(co2_pred: float, temp_in: float, hum_in: float,
                    soil_hum: float, temp_out: float, rain_out: float,
                    wind_out: float, solar_out: float, hour: int) -> dict:
    CYCLE   = 600
    CYCLE_P = 180

    if   temp_in <= 22: fan_base = 0
    elif temp_in <= 25: fan_base = 120
    elif temp_in <= 28: fan_base = 270
    elif temp_in <= 32: fan_base = 390
    else:               fan_base = 510
    fan_adj = 0
    if   75 <= hum_in <= 85:        fan_adj += 48
    elif hum_in > 85:               fan_adj += 108
    if   800 <= co2_pred <= 1000:   fan_adj += 30
    elif 1000 < co2_pred <= 1500:   fan_adj += 72
    elif co2_pred > 1500:           fan_adj += 120
    if   temp_out < temp_in - 5:    fan_adj += 48
    elif temp_out >= temp_in:       fan_adj -= 48
    if   solar_out > 600:           fan_adj += 30
    if   wind_out > 5:              fan_adj -= 30
    fan_sec = fan_base + fan_adj
    if rain_out == 1:
        fan_sec = min(fan_sec, 180)
    fan_sec = int(max(0, min(CYCLE, fan_sec)))

    if rain_out == 1 or wind_out > 8:
        win_sec = 0
    else:
        if   temp_in <= 22: win_base = 0
        elif temp_in <= 25: win_base = 120
        elif temp_in <= 28: win_base = 270
        elif temp_in <= 32: win_base = 390
        else:               win_base = 510
        win_adj = 0
        if   1000 < co2_pred <= 1500: win_adj += 72
        elif co2_pred > 1500:         win_adj += 120
        if   hum_in > 85:             win_adj += 48
        if   temp_out < temp_in - 8:  win_adj += 48
        elif temp_out >= temp_in:     win_adj -= 72
        if   2 <= wind_out <= 5:      win_adj += 30
        if   solar_out > 800:         win_adj += 30
        win_sec = win_base + win_adj
        if 5 <= wind_out <= 8:
            win_sec = min(win_sec, 180)
        win_sec = int(max(0, min(CYCLE, win_sec)))

    if temp_in > 18:
        heat_sec = 0
    else:
        if   temp_in >= 15: heat_base = 90
        elif temp_in >= 12: heat_base = 210
        elif temp_in >= 8:  heat_base = 360
        else:               heat_base = 480
        heat_adj = 0
        if   0 <= temp_out <= 5:   heat_adj += 60
        elif -5 <= temp_out < 0:   heat_adj += 120
        elif temp_out < -5:        heat_adj += 180
        if   3 <= wind_out <= 7:   heat_adj += 60
        elif wind_out > 7:         heat_adj += 120
        if   rain_out == 1:        heat_adj -= 30
        if   co2_pred > 1200:      heat_adj -= 30
        heat_sec = heat_base + heat_adj
        if solar_out > 400:  heat_sec -= 120
        if hum_in < 40:      heat_sec  = min(heat_sec, 300)
        heat_sec = int(max(0, min(CYCLE, heat_sec)))

    if rain_out == 1 or soil_hum > 70:
        pump_sec = 0
    else:
        if   soil_hum < 10: pump_base = 120
        elif soil_hum < 20: pump_base = 60
        else:               pump_base = 0
        pump_adj = 0
        if   temp_in > 28:      pump_adj += 30
        elif temp_in < 15:      pump_adj -= 15
        if   hum_in < 50:       pump_adj += 20
        elif hum_in > 80:       pump_adj -= 15
        if   solar_out > 600:   pump_adj += 20
        elif solar_out < 50:    pump_adj -= 15
        if   wind_out > 4:      pump_adj += 15
        if   temp_out > 30:     pump_adj += 15
        pump_sec = int(max(0, min(CYCLE_P, pump_base + pump_adj)))

    return {"fan_on_sec": fan_sec, "window_on_sec": win_sec,
            "heater_on_sec": heat_sec, "pump_on_sec": pump_sec}


# ─────────────────────────────────────────────────────────────────────
# 6. Discord Webhook 경고
# ─────────────────────────────────────────────────────────────────────
def send_discord_alert(message: str):
    if not DISCORD_WEBHOOK_URL:
        log.warning(f"[Discord 경고 — Webhook 미설정] {message}")
        return
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
        if resp.status_code in (200, 204):
            log.info(f"Discord 경고 발송 완료: {message[:60]}...")
        else:
            log.warning(f"Discord 발송 실패 ({resp.status_code}): {message}")
    except Exception as e:
        log.error(f"Discord 요청 오류: {e}")


# ─────────────────────────────────────────────────────────────────────
# 7. KS Test 드리프트 탐지
# ─────────────────────────────────────────────────────────────────────
def run_ks_test(ref_values: np.ndarray, curr_values: np.ndarray, feature_name="co2") -> dict:
    if ref_values is None or len(ref_values) < 5 or len(curr_values) < 5:
        return {"ks_stat": None, "ks_pvalue": None, "drift": False}
    ks_stat, p_value = stats.ks_2samp(ref_values, curr_values)
    drift = bool(p_value < KS_PVALUE_THRESHOLD)
    log.info(f"KS Test [{feature_name}] stat={ks_stat:.4f} p={p_value:.4f} "
             f"drift={'⚠️ 감지' if drift else '✅ 없음'}")
    return {"ks_stat": float(ks_stat), "ks_pvalue": float(p_value), "drift": drift}


# ─────────────────────────────────────────────────────────────────────
# 8. 모니터링 — 센서별 예측 vs 실제 (t → t+Hh 매칭)
# ─────────────────────────────────────────────────────────────────────
def run_monitoring(db2: Client, db3: Client, cfg: dict,
                   ref_values: np.ndarray | None = None) -> dict | None:
    """
    ⑤ pred(t) ↔ DB3 actual(t+PREDICT_HORIZON_H) 시프트 매칭.
    DB3 에 해당 센서 컬럼이 없으면(예: temp_in 미추가) 안전하게 스킵.
    """
    name, pred_col, db3_col, unit = cfg["label"], cfg["pred_col"], cfg["db3_col"], cfg["unit"]

    res2 = (
        db2.table(DB2_TABLE_NAME).select(f"datetime, {pred_col}")
        .not_.is_(pred_col, "null")
        .order("datetime", desc=True).limit(RETRAIN_WINDOW).execute()
    )
    pred_df = pd.DataFrame(res2.data)
    if pred_df.empty:
        log.info(f"[모니터링·{name}] DB2 예측값 없음")
        return None

    try:
        res3 = (
            db3.table(DB3_TABLE_NAME).select(f"datetime, {db3_col}")
            .not_.is_(db3_col, "null")
            .order("datetime", desc=True).limit(RETRAIN_WINDOW * 2).execute()
        )
        actual_df = pd.DataFrame(res3.data)
    except Exception as e:
        log.warning(f"[모니터링·{name}] DB3 '{db3_col}' 조회 실패 — 스킵 ({e})")
        return None
    if actual_df.empty:
        log.info(f"[모니터링·{name}] DB3 실제값 없음 (누적 중)")
        return None

    pred_df["dt_hour"]   = pd.to_datetime(pred_df["datetime"], format="mixed", utc=True).dt.floor("h")
    actual_df["dt_hour"] = pd.to_datetime(actual_df["datetime"], format="mixed", utc=True).dt.floor("h")
    pred_df["dt_match"]  = pred_df["dt_hour"] + pd.Timedelta(hours=PREDICT_HORIZON_H)   # ⑤

    merged = pd.merge(
        pred_df[["dt_match", pred_col]], actual_df[["dt_hour", db3_col]],
        left_on="dt_match", right_on="dt_hour", how="inner",
    )
    log.info(f"[모니터링·{name}] 시프트 매칭 (t→t+{PREDICT_HORIZON_H}h): {len(merged)}개")
    if len(merged) < 5:
        log.warning(f"[모니터링·{name}] 매칭 부족 ({len(merged)}개) — 대기")
        return None

    y_pred = merged[pred_col].values.astype(float)
    y_true = merged[db3_col].values.astype(float)
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mae  = float(mean_absolute_error(y_true, y_pred))
    r2   = float(r2_score(y_true, y_pred))
    denom = np.where(np.abs(y_true) < 1.0, 1.0, np.abs(y_true))
    mape  = float(np.mean(np.abs((y_true - y_pred) / denom)) * 100)
    log.info(f"[모니터링·{name}] n={len(merged)} RMSE={rmse:.2f} MAE={mae:.2f} R²={r2:.4f} MAPE={mape:.2f}%")

    ks_result = run_ks_test(ref_values, y_pred, feature_name=pred_col)

    evidently_drift = False
    try:
        from evidently.report import Report
        from evidently.metric_preset import DataDriftPreset
        ref_df  = pd.DataFrame({"v": ref_values}) if ref_values is not None else pd.DataFrame({"v": y_true})
        curr_df = pd.DataFrame({"v": y_pred})
        report = Report(metrics=[DataDriftPreset()])
        report.run(reference_data=ref_df, current_data=curr_df)
        evidently_drift = report.as_dict()["metrics"][0]["result"]["dataset_drift"]
        Path("reports").mkdir(exist_ok=True)
        rp = f"reports/drift_{name}_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
        report.save_html(rp)
        log.info(f"[모니터링·{name}] Evidently 저장: {rp} drift={evidently_drift}")
    except ImportError:
        log.info("Evidently 미설치 — 스킵")
    except Exception as e:
        log.warning(f"Evidently 오류: {e}")

    drift_detected = bool(evidently_drift or ks_result["drift"])

    try:
        with mlflow.start_run(run_name=f"monitor_{name}_{datetime.now().strftime('%Y%m%d_%H%M')}"):
            mlflow.log_params({
                "sensor": name, "horizon_h": PREDICT_HORIZON_H,
                "monitoring_window": RETRAIN_WINDOW, "n_samples": len(merged),
                "match_mode": f"t_to_t+{PREDICT_HORIZON_H}h_shift",
            })
            mlflow.log_metrics({
                f"{cfg['pred_col']}_rmse": rmse, f"{cfg['pred_col']}_mae": mae,
                f"{cfg['pred_col']}_r2": r2, f"{cfg['pred_col']}_mape": mape,
                f"{cfg['pred_col']}_ks_pvalue": ks_result["ks_pvalue"] or -1.0,
                f"{cfg['pred_col']}_drift": float(drift_detected),
            })
    except Exception as e:
        log.warning(f"MLflow 기록 실패: {e}")

    if r2 < DISCORD_R2_THRESHOLD:
        send_discord_alert(
            f"🚨 [{name} 모델 경고] R²={r2:.4f} < {DISCORD_R2_THRESHOLD}\n"
            f"RMSE={rmse:.2f}{unit} | MAPE={mape:.2f}%\n"
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')} → 재학습 검토"
        )
    if drift_detected:
        kp = ks_result["ks_pvalue"]
        send_discord_alert(
            f"⚠️ [{name} 드리프트] KS p={f'{kp:.4f}' if kp is not None else 'N/A'} | "
            f"Evidently={evidently_drift}\n→ 재학습 트리거"
        )

    return {"rmse": rmse, "mae": mae, "r2": r2, "mape": mape,
            "drift_detected": drift_detected, "ks_pvalue": ks_result["ks_pvalue"],
            "n_samples": len(merged)}


# ─────────────────────────────────────────────────────────────────────
# 9. 학습용 피처 엔지니어링 + Augmentation
# ─────────────────────────────────────────────────────────────────────
def build_train_features(raw_df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """
    DB1 원본 → 학습 피처 + 두 타겟(next_co2_in, next_temp_in) 생성.
    ⑤ shift(-PREDICT_HORIZON_H), lag1..lag_depth.
    """
    depth = _lag_depth(features) or PREDICT_HORIZON_H
    df = raw_df.copy().sort_values("datetime").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"], format="mixed", utc=True)

    # 시간 비연속 구간(1시간 간격 위반) 오염 처리
    step_h = df["datetime"].diff().dt.total_seconds() / 3600
    gap_after = set(df.index[step_h > 1.5].tolist())
    gap_before, gap_lag = set(), set()
    for idx in gap_after:
        for k in range(1, PREDICT_HORIZON_H + 1):
            if idx - k >= 0:
                gap_before.add(idx - k)
        for k in range(0, depth + 1):
            if idx + k < len(df):
                gap_lag.add(idx + k)
    if gap_after:
        log.warning(f"비연속 구간 {len(gap_after)}개 → 오염 행 타겟/lag NaN 처리")

    df["hour"]        = df["datetime"].dt.hour
    df["month"]       = df["datetime"].dt.month
    df["day_of_week"] = df["datetime"].dt.dayofweek
    df["is_daytime"]  = ((df["hour"] >= 6) & (df["hour"] <= 19)).astype(float)
    df["hour_sin"]    = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"]    = np.cos(2 * np.pi * df["hour"] / 24)
    df["month_sin"]   = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"]   = np.cos(2 * np.pi * df["month"] / 12)

    df["temp_in"]   = pd.to_numeric(df.get("temp_in"),  errors="coerce").fillna(20.0)
    df["temp_out"]  = pd.to_numeric(df.get("temp_out"), errors="coerce").fillna(15.0)
    df["temp_diff"] = df["temp_in"] - df["temp_out"]
    df["rain_out"]  = pd.to_numeric(df.get("rain_out", 0), errors="coerce").fillna(0)
    df["wind_out"]  = pd.to_numeric(df.get("wind_out", 0), errors="coerce").fillna(0)

    for col in LAG_COLS_RAW:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        for lag in range(1, depth + 1):
            df[f"{col}_lag{lag}"] = df[col].shift(lag)
    lag_cols = [f"{c}_lag{l}" for c in LAG_COLS_RAW for l in range(1, depth + 1)]
    if gap_lag:
        df.loc[list(gap_lag), [c for c in lag_cols if c in df.columns]] = np.nan

    # 타겟: 두 센서 모두 shift(-HORIZON)
    for base in ("co2_in", "temp_in"):
        df[f"next_{base}"] = df[base].shift(-PREDICT_HORIZON_H)
        if gap_before:
            df.loc[list(gap_before), f"next_{base}"] = np.nan

    log.info(f"학습 피처 생성 완료: {len(df):,}행 (lag_depth={depth})")
    return df


def make_aug_train(df: pd.DataFrame, fault_base: str, features: list[str]) -> pd.DataFrame:
    """
    ③ Missingness Augmentation: 고장 센서를 마스킹한 복제본을 추가.
       원본 + 경증(현재값 NaN) + 중증(현재값+lag NaN) = 3배.
    """
    cur = [fault_base] + (["temp_diff"] if fault_base == "temp_in" else [])
    cur = [c for c in cur if c in features]
    lags = [c for c in features if c.startswith(fault_base + "_lag")]
    d_mild = df.copy(); d_mild[cur] = np.nan
    d_sev  = df.copy(); d_sev[cur + lags] = np.nan
    return pd.concat([df, d_mild, d_sev], ignore_index=True)


# ─────────────────────────────────────────────────────────────────────
# 10. 자동 재학습 (센서별, augmentation + 고장 시나리오 게이트)
# ─────────────────────────────────────────────────────────────────────
def _load_train_val_test(db1: Client, features: list[str]):
    """전처리 CSV 우선, 없으면 DB1 원본 → build_train_features."""
    for tr, va, te in [
        (Path("./data/train.csv"), Path("./data/val.csv"), Path("./data/test.csv")),
        (Path("/app/data/train.csv"), Path("/app/data/val.csv"), Path("/app/data/test.csv")),
        (Path("./train.csv"), Path("./val.csv"), Path("./test.csv")),
    ]:
        if tr.exists() and va.exists() and te.exists():
            log.info("전처리 CSV 로드")
            return pd.read_csv(tr), pd.read_csv(va), pd.read_csv(te)

    log.info("전처리 CSV 없음 — DB1 원본 사용")
    all_data, offset, batch = [], 0, 1000
    while True:
        res = db1.table("sensor_data").select("*").order("datetime").range(offset, offset + batch - 1).execute()
        if not res.data:
            break
        all_data.extend(res.data)
        if len(res.data) < batch:
            break
        offset += batch
    raw = pd.DataFrame(all_data)
    log.info(f"DB1 로드: {len(raw):,}행")
    if len(raw) < RETRAIN_DATA_MIN_ROWS:
        return None, None, None
    feat = build_train_features(raw, features)
    feat = feat.dropna(subset=features + ["next_co2_in", "next_temp_in"]).reset_index(drop=True)
    if len(feat) < RETRAIN_DATA_MIN_ROWS:
        return None, None, None
    n = len(feat)
    return feat.iloc[:int(n*0.70)], feat.iloc[int(n*0.70):int(n*0.85)], feat.iloc[int(n*0.85):]


def _mask_fault(X: pd.DataFrame, fault_base: str, features: list[str]) -> pd.DataFrame:
    """⑥ 재학습 성능 게이트용 — 고장(경증) 시나리오로 test 마스킹."""
    Xm = X.copy()
    cols = [fault_base] + (["temp_diff"] if fault_base == "temp_in" else [])
    Xm[[c for c in cols if c in features]] = np.nan
    return Xm


def run_retrain(db1: Client, name: str, bundle: dict, trigger_reason="manual") -> dict | None:
    """③⑥ 센서별 재학습: augmentation 학습 → 고장 시나리오로 현재 모델과 비교 → 승격."""
    cfg = SENSORS[name]
    features  = bundle["features"]
    fault_base = cfg["null_col"]
    target_col = bundle.get("target") or f"next_{fault_base}"
    log.info(f"=== [재학습·{name}] 트리거: {trigger_reason} ===")

    if features is None:
        log.warning(f"[재학습·{name}] 번들 피처 없음 — 스킵")
        return None

    try:
        train_df, val_df, test_df = _load_train_val_test(db1, features)
        if train_df is None:
            log.warning(f"[재학습·{name}] 데이터 부족 — 스킵")
            return None
        for d in (train_df, val_df, test_df):
            if target_col not in d.columns:
                log.warning(f"[재학습·{name}] 타겟 '{target_col}' 없음 — 스킵")
                return None

        # ③ augmentation 학습셋
        aug = make_aug_train(train_df, fault_base, features)
        X_tr, y_tr = aug[features], aug[target_col]
        X_va, y_va = val_df[features], val_df[target_col]
        X_te, y_te = test_df[features], test_df[target_col]

        params = copy.deepcopy(LGB_PARAMS); n_est = params.pop("n_estimators")
        new_model = lgb.LGBMRegressor(**params, n_estimators=n_est)
        new_model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)],
                      callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(200)])

        # ⑥ 고장(경증) 시나리오로 평가 — 실제 배포 조건
        X_te_fault = _mask_fault(X_te, fault_base, features)
        r2_new   = float(r2_score(y_te, new_model.predict(X_te_fault)))
        rmse_new = float(np.sqrt(mean_squared_error(y_te, new_model.predict(X_te_fault))))
        r2_curr  = float(r2_score(y_te, bundle["model"].predict(X_te_fault)))
        log.info(f"[재학습·{name}] 고장 시나리오 R² — 신규={r2_new:.4f} / 기존={r2_curr:.4f}")

        if r2_new <= r2_curr:
            log.warning(f"[재학습·{name}] 성능 미달 — 교체 보류")
            send_discord_alert(f"ℹ️ [{name} 재학습·보류] 신규 R²={r2_new:.4f} ≤ 기존 {r2_curr:.4f}")
            return None

        # 번들 갱신 후 저장 (피처/클립/타겟 유지)
        new_bundle = dict(bundle)
        new_bundle.update({"model": new_model, "trained_with": "missingness_augmentation(mild+severe)",
                           "fault_sensor": fault_base, "target": target_col})
        path = Path(cfg["bundle_path"]); path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(new_bundle, path)
        new_bundle["lag_depth"] = _lag_depth(features)
        log.info(f"[재학습·{name}] 번들 갱신: {path}")

        try:
            with mlflow.start_run(run_name=f"retrain_{name}_{datetime.now().strftime('%Y%m%d_%H%M')}"):
                mlflow.log_params({"sensor": name, "trigger": trigger_reason,
                                   "train_rows_aug": len(aug), "r2_previous_fault": r2_curr})
                mlflow.log_metrics({"test_r2_fault": r2_new, "test_rmse_fault": rmse_new,
                                    "r2_improvement_fault": r2_new - r2_curr})
                mlflow.sklearn.log_model(new_model, artifact_path=name,
                                         registered_model_name=cfg["registry"])
                from mlflow.tracking import MlflowClient
                client = MlflowClient()
                vers = client.get_latest_versions(cfg["registry"], stages=["None"])
                if vers:
                    client.transition_model_version_stage(
                        name=cfg["registry"], version=vers[-1].version,
                        stage="Production", archive_existing_versions=True)
                    log.info(f"[{name}] Registry v{vers[-1].version} → Production")
            send_discord_alert(f"✅ [{name} 재학습+승격] 고장 R²={r2_new:.4f} > {r2_curr:.4f} "
                               f"RMSE={rmse_new:.2f}{cfg['unit']}")
        except Exception as e:
            log.warning(f"[{name}] MLflow/Registry 실패: {e}")

        return new_bundle

    except Exception as e:
        log.error(f"[재학습·{name}] 오류: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# 11. 파이프라인 클래스
# ─────────────────────────────────────────────────────────────────────
class Pipeline:
    def __init__(self):
        self.db1, self.db2, self.db3 = init_clients()
        self.bundles = {name: load_bundle(cfg["bundle_path"]) for name, cfg in SENSORS.items()}
        self.ref = {name: self._load_ref(cfg["db3_col"]) for name, cfg in SENSORS.items()}
        init_mlflow()
        log.info("Pipeline 초기화 완료")

    def _load_ref(self, col: str) -> np.ndarray | None:
        cache = Path(f"./models/ref_{col}.npy")
        if cache.exists():
            return np.load(str(cache))
        for p in [Path("./data/train.csv"), Path("/app/data/train.csv"), Path("./train.csv")]:
            if p.exists():
                df = pd.read_csv(p)
                if col in df.columns:
                    arr = df[col].dropna().values
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    np.save(str(cache), arr)
                    log.info(f"KS 기준값[{col}]: {len(arr)}개 캐싱")
                    return arr
        log.info(f"KS 기준값[{col}] 없음 — KS 스킵")
        return None

    def hourly_step(self):
        log.info("=" * 65)
        log.info(f"[HOURLY STEP] {datetime.now(timezone.utc).isoformat()}")

        predicted = poll_and_predict(self.db2, self.bundles)

        # 제어 — 예측·실측을 coalesce 해서 사용
        for row in predicted:
            try:
                co2  = float(row.get("co2_predicted")  or row.get("co2_in")  or 700)
                temp = float(row.get("temp_predicted") or row.get("temp_in") or 20)
                ctrl = compute_control(
                    co2_pred=co2, temp_in=temp,
                    hum_in=float(row.get("hum_in") or 60), soil_hum=float(row.get("soil_hum") or 30),
                    temp_out=float(row.get("temp_out") or 15), rain_out=float(row.get("rain_out") or 0),
                    wind_out=float(row.get("wind_out") or 2), solar_out=0.0,
                    hour=pd.to_datetime(row["datetime"]).hour,
                )
                log.info(f"  제어 [{row['datetime']}] CO2={co2:.0f} 온도={temp:.1f} | "
                         f"팬={ctrl['fan_on_sec']}s 창문={ctrl['window_on_sec']}s "
                         f"히터={ctrl['heater_on_sec']}s 펌프={ctrl['pump_on_sec']}s")
            except Exception as e:
                log.error(f"제어 계산 오류: {e}")

        # 센서별 모니터링 + 트리거
        for name, cfg in SENSORS.items():
            metrics = run_monitoring(self.db2, self.db3, cfg, ref_values=self.ref[name])
            if metrics is None:
                continue
            trigger = None
            if metrics["r2"] < RETRAIN_R2_THRESHOLD:
                trigger = "r2_drop"
            elif metrics["drift_detected"]:
                kp = metrics.get("ks_pvalue")
                trigger = "ks_drift" if (kp and kp < KS_PVALUE_THRESHOLD) else "evidently_drift"
            if trigger:
                log.warning(f"🔄 [{name}] 재학습 트리거: {trigger} R²={metrics['r2']:.4f}")
                nb = run_retrain(self.db1, name, self.bundles[name], trigger_reason=trigger)
                if nb is not None:
                    self.bundles[name] = nb
                    self.ref[name] = self._load_ref(cfg["db3_col"])
                    log.info(f"✅ [{name}] 모델 핫스왑 완료")
            else:
                log.info(f"✅ [{name}] 성능 정상 (R²={metrics['r2']:.4f})")

        log.info("[HOURLY STEP] 완료")

    def scheduled_step(self):
        log.info("=" * 65)
        log.info(f"[SCHEDULED RETRAIN] {datetime.now().isoformat()}")
        for name in SENSORS:
            nb = run_retrain(self.db1, name, self.bundles[name], trigger_reason="scheduled")
            if nb is not None:
                self.bundles[name] = nb
                self.ref[name] = self._load_ref(SENSORS[name]["db3_col"])
                log.info(f"✅ [{name}] 정기 재학습 핫스왑")
        log.info("[SCHEDULED RETRAIN] 완료")


# ─────────────────────────────────────────────────────────────────────
# 12. 진입점
# ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 65)
    log.info("스마트팜 MLOps 파이프라인 시작 (v3.0 — 다중 센서 + augmentation)")
    log.info("=" * 65)

    pipeline = Pipeline()
    pipeline.hourly_step()

    schedule.every(1).hours.do(pipeline.hourly_step)
    schedule.every().day.at(f"{RETRAIN_SCHEDULE_HOUR:02d}:00").do(pipeline.scheduled_step)
    log.info("스케줄 등록: 매 1시간(예측+모니터링+재학습) / 매일 "
             f"{RETRAIN_SCHEDULE_HOUR:02d}:00(정기 재학습)")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
