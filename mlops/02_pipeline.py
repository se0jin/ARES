"""
02_pipeline.py — 스마트팜 환경 예측 실시간 MLOps 파이프라인  (v4.0 · nowcast)
=====================================================================
[v4.0 주요 변경 — 설계 A(nowcast) 전환: '현재 시점 값' 예측]
  ① 모델 로딩: 번들(dict: model+features+clip+target) 로딩 — 피처 순서/클리핑 동봉
  ② 다중 센서: CO2 + 온도 단일 고장 예측 (SENSORS 설정)
     └ DB2 교차 결측: 홀수시 co2_in=Null(co2 예측) / 짝수시 temp_in=Null(temp 예측)
  ③ 결측 학습(augmentation): 재학습 시 '고장 센서의 lag'를 마스킹한 복제본 추가
     └ nowcast에선 현재값이 타겟(=피처 아님) → 마스킹 대상은 lag뿐 (원본+lag마스킹=2배)
  ④ 고장 센서 현재값은 NaN 그대로 전달(애초에 피처에서 제외됨). lag는 예측값 재활용.
  ⑤ ★ nowcast: 미래 shift 없음. 모델은 '현재 시점' 값을 예측.
     └ 모니터링은 pred(t) ↔ DB3 actual(t) '같은 시각' 매칭 (t↔t)
  ⑥ 재학습 성능 게이트는 실제 운영 조건(현재값 제외, lag 有)으로 평가

[배포 전 반드시 확인]
  - models/lgbm_co2_aug.pkl, models/lgbm_temp_aug.pkl 가 번들 형식(target=co2_in/temp_in)
  - DB2(sensor_data_2)에 temp_predicted 컬럼 추가 (UNIQUE 제약 걸지 말 것)
  - DB3(sensor_data_3)에 temp_in 컬럼 추가 + 하드웨어가 실제 온도 적재 (없으면 temp 모니터링 스킵)
  - 전처리 CSV(train/val/test)가 nowcast 버전(원본 co2_in/temp_in이 타겟, next_* 없음)인지
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

# ── ⑤ nowcast: 현재 시점 값 예측 (미래 shift 없음, 모니터링 t↔t 동일 시각) ──
NOWCAST   = True
LAG_DEPTH = 3                 # 전처리에서 생성한 lag 깊이 (1~3)

# ── 재학습 트리거 임계값 ────────────────────────────────────────────
RETRAIN_R2_THRESHOLD  = 0.70
KS_PVALUE_THRESHOLD   = 0.05
RETRAIN_WINDOW        = 48
RETRAIN_DATA_MIN_ROWS = 500
RETRAIN_SCHEDULE_HOUR = 2
DISCORD_R2_THRESHOLD  = 0.60   # (구) R² 단독 경고 임계 — 융합 판단 도입 후 폴백용으로만 잔류

# ── 재학습 트리거: RMSE·MAE·R² 융합 (상대 열화 + 다수결) ──────────────
#   교수 피드백 반영: R² 단독이 아니라 세 지표를 종합해 판단.
#   - REF = 모델 Test(직후고장·lag 有) 기준 성능. 재학습 성공 시 자동 갱신.
#   - 각 지표를 '기준 대비 상대 열화'로 평가(스케일 다른 co2/temp를 한 함수로).
#   - R²은 소표본·저분산에서 음수로 폭주(예: -716)하므로 R2_FLOOR로 클립 후 비교.
#   - 3개 중 FUSION_MIN_VOTES개 이상 '열화'일 때만 트리거(단일 지표 오탐 차단).
REF_METRICS = {
    "co2":  {"rmse": 43.70, "mae": 33.54, "r2": 0.669},   # 신규 nowcast Test(직후고장)
    "temp": {"rmse": 1.65,  "mae": 1.22,  "r2": 0.838},
}
RMSE_DEGRADE_RATIO = 1.5    # RMSE > 기준×1.5  → 열화 1표
MAE_DEGRADE_RATIO  = 1.5    # MAE  > 기준×1.5  → 열화 1표
R2_DROP_ABS        = 0.20   # R²   < 기준−0.2  → 열화 1표
R2_FLOOR           = -1.0   # 소표본 음수 R² 폭주 방어 클립
FUSION_MIN_VOTES   = 2      # 3개 지표 중 N개 이상 동의 시 재학습

# ── ② 센서 설정 (CO2 + 온도 단일 고장) ─────────────────────────────
#   나머지(model/features/clip/target)는 번들에서 로드
SENSORS = {
    "co2": {
        "label":        "CO2",
        "unit":         "ppm",
        "bundle_path":  "./models/lgbm_co2_aug.pkl",
        "registry":     "smartfarm_co2_lgbm",
        "null_col":     "co2_in",         # 고장 시 NULL 인 입력 컬럼 (= 예측 대상)
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
    ① 번들 로딩. 저장 형식: {model, features, clip:(lo,hi), target, mode, ...}
    구버전 bare 모델이면 최소 번들로 감싼다(피처/클립은 폴백).
    """
    obj = joblib.load(path)
    if isinstance(obj, dict) and "model" in obj:
        b = obj
    else:  # 폴백: bare 모델
        b = {"model": obj, "features": None, "clip": (None, None),
             "target": None}
        log.warning(f"{path}: 번들이 아닌 bare 모델 — 피처/클립 정보 없음")
    b["lag_depth"] = _lag_depth(b.get("features"))
    log.info(
        f"모델 로드: {path}  타겟={b.get('target')}  mode={b.get('mode','nowcast')}  "
        f"피처={len(b['features']) if b.get('features') else '?'}개  "
        f"clip={b.get('clip')}  lag_depth={b['lag_depth']}"
    )
    return b


def _lag_depth(features) -> int:
    if not features:
        return LAG_DEPTH
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
       nowcast에선 현재값이 '예측 대상'이라 애초에 features에 없음. lag 는
       이전 예측값(pred_col)을 재활용해 채운다(교차 결측 대응).
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
        row["temp_in"]   = np.nan          # ④ 고장 → NaN (features에도 없음)
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
    # ④ ffill/bfill 없음 — 고장 센서 NaN 을 모델이 직접 처리
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

                log.info(f"  [{name}][{dt_str}] {cfg['pred_col']} = {pred:.1f} {cfg['unit']} (현재값 추정)")
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
# 8. 모니터링 — 센서별 예측 vs 실제 (⑤ t ↔ t 같은 시각 매칭)
# ─────────────────────────────────────────────────────────────────────
def run_monitoring(db2: Client, db3: Client, cfg: dict,
                   ref_values: np.ndarray | None = None) -> dict | None:
    """
    ⑤ nowcast: pred(t) ↔ DB3 actual(t) '같은 시각' 매칭.
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

    # ⑤ nowcast — 같은 시각(t↔t) 매칭
    merged = pd.merge(
        pred_df[["dt_hour", pred_col]], actual_df[["dt_hour", db3_col]],
        on="dt_hour", how="inner",
    )
    log.info(f"[모니터링·{name}] 동일 시각 매칭 (t↔t): {len(merged)}개")
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
    y_std = float(np.std(y_true))
    log.info(f"[모니터링·{name}] n={len(merged)} RMSE={rmse:.2f} MAE={mae:.2f} R²={r2:.4f} MAPE={mape:.2f}% (실측std={y_std:.2f})")

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
                "sensor": name, "mode": "nowcast",
                "monitoring_window": RETRAIN_WINDOW, "n_samples": len(merged),
                "match_mode": "t_to_t_same_hour",
            })
            mlflow.log_metrics({
                f"{cfg['pred_col']}_rmse": rmse, f"{cfg['pred_col']}_mae": mae,
                f"{cfg['pred_col']}_r2": r2, f"{cfg['pred_col']}_mape": mape,
                f"{cfg['pred_col']}_ks_pvalue": ks_result["ks_pvalue"] or -1.0,
                f"{cfg['pred_col']}_drift": float(drift_detected),
            })
    except Exception as e:
        log.warning(f"MLflow 기록 실패: {e}")

    # 성능 열화 경고/트리거 판단은 hourly_step의 fusion_retrain_decision()에서 일괄 처리.
    # (여기서는 분포 드리프트만 별도 경고 — 성능 지표와 직교)
    if drift_detected:
        kp = ks_result["ks_pvalue"]
        send_discord_alert(
            f"⚠️ [{name} 드리프트] KS p={f'{kp:.4f}' if kp is not None else 'N/A'} | "
            f"Evidently={evidently_drift}\n→ 재학습 트리거 검토"
        )

    return {"rmse": rmse, "mae": mae, "r2": r2, "mape": mape, "y_std": y_std,
            "drift_detected": drift_detected, "ks_pvalue": ks_result["ks_pvalue"],
            "n_samples": len(merged)}


# ── 8-B. 재학습 트리거 판단 — RMSE·MAE·R² 융합 (상대 열화 + 다수결) ──
def fusion_retrain_decision(name: str, m: dict) -> tuple[bool, list[str]]:
    """
    교수 피드백 반영: R² 단독이 아니라 RMSE·MAE·R² 세 지표를 종합해 판단.
      - 각 지표를 '기준(REF) 대비 상대 열화'로 평가 (스케일 다른 co2/temp를 한 함수로)
      - R²은 소표본·저분산에서 음수로 폭주(예: -716)하므로 R2_FLOOR로 클립 후 비교
      - 3개 중 FUSION_MIN_VOTES개 이상이 '열화'면 재학습 트리거 (단일 지표 오탐 차단)
    """
    ref = REF_METRICS.get(name)
    if ref is None:   # 기준 미설정 → 보수적 R² 단독 폴백
        deg = m["r2"] < RETRAIN_R2_THRESHOLD
        return deg, [f"R²={m['r2']:.3f} {'<' if deg else '≥'} {RETRAIN_R2_THRESHOLD} (기준 미설정 폴백)"]

    rmse_thr = ref["rmse"] * RMSE_DEGRADE_RATIO
    mae_thr  = ref["mae"]  * MAE_DEGRADE_RATIO
    r2_thr   = ref["r2"]   - R2_DROP_ABS
    r2_clip  = max(m["r2"], R2_FLOOR)   # 소표본 음수 R² 폭주 무력화

    votes, reasons = [], []
    if m["rmse"] > rmse_thr:
        votes.append("RMSE"); reasons.append(f"RMSE {m['rmse']:.2f}>{rmse_thr:.2f}")
    if m["mae"] > mae_thr:
        votes.append("MAE");  reasons.append(f"MAE {m['mae']:.2f}>{mae_thr:.2f}")
    if r2_clip < r2_thr:
        votes.append("R2");   reasons.append(f"R²(clip) {r2_clip:.2f}<{r2_thr:.2f}")

    trigger = len(votes) >= FUSION_MIN_VOTES
    reasons.append(f"열화 {len(votes)}/3표" if votes else "3개 지표 모두 정상")
    return trigger, reasons


# ─────────────────────────────────────────────────────────────────────
# 9. 학습용 피처 엔지니어링 + Augmentation  (nowcast)
# ─────────────────────────────────────────────────────────────────────
def build_train_features(raw_df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """
    DB1 원본 → 학습 피처 생성 (nowcast).
    ⑤ 타겟 = 원본 현재값 컬럼(co2_in, temp_in) 자체. shift/next_* 없음.
       미래 행을 끌어오지 않으므로 future-leak(gap_before) 처리도 불필요.
    """
    depth = _lag_depth(features) or LAG_DEPTH
    df = raw_df.copy().sort_values("datetime").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"], format="mixed", utc=True)

    # 시간 비연속 구간(1시간 간격 위반) → lag 오염 행만 NaN 처리
    step_h = df["datetime"].diff().dt.total_seconds() / 3600
    gap_after = set(df.index[step_h > 1.5].tolist())
    gap_lag = set()
    for idx in gap_after:
        for k in range(0, depth + 1):
            if idx + k < len(df):
                gap_lag.add(idx + k)
    if gap_after:
        log.warning(f"비연속 구간 {len(gap_after)}개 → lag 오염 행 NaN 처리")

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

    # ⑤ nowcast: 타겟은 원본 co2_in / temp_in (이미 컬럼으로 존재) — 별도 생성 없음
    log.info(f"학습 피처 생성 완료(nowcast): {len(df):,}행 (lag_depth={depth})")
    return df


def make_aug_train(df: pd.DataFrame, fault_base: str, features: list[str]) -> pd.DataFrame:
    """
    ③ nowcast Augmentation: '고장 센서의 lag'만 마스킹한 복제본을 추가.
       현재값은 타겟(=피처 아님)이라 마스킹 대상이 아님.
       원본(직후고장·lag有) + lag마스킹본(장기고장·lag NaN) = 2배.
    """
    lags = [c for c in features if c.startswith(fault_base + "_lag")]
    d_long = df.copy()
    if lags:
        d_long[lags] = np.nan
    return pd.concat([df, d_long], ignore_index=True)


# ─────────────────────────────────────────────────────────────────────
# 10. 자동 재학습 (센서별, augmentation + 운영 시나리오 게이트)
# ─────────────────────────────────────────────────────────────────────
def _load_train_val_test(db1: Client, features: list[str]):
    """전처리 CSV 우선, 없으면 DB1 원본 → build_train_features(nowcast)."""
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
    raw_targets = [c for c in ("co2_in", "temp_in") if c in feat.columns]
    feat = feat.dropna(subset=features + raw_targets).reset_index(drop=True)
    if len(feat) < RETRAIN_DATA_MIN_ROWS:
        return None, None, None
    n = len(feat)
    return feat.iloc[:int(n*0.70)], feat.iloc[int(n*0.70):int(n*0.85)], feat.iloc[int(n*0.85):]


def _mask_fault(X: pd.DataFrame, fault_base: str, features: list[str]) -> pd.DataFrame:
    """
    ⑥ nowcast 게이트: 현재값은 이미 features에서 제외돼 있어(고장 가정 내장)
       추가 마스킹 없이 실제 운영 입력 그대로 평가한다.
       (교차 시뮬레이션은 항상 1시간 고장 → lag가 살아있는 조건이 운영 기준)
    """
    return X.copy()


def run_retrain(db1: Client, name: str, bundle: dict, trigger_reason="manual") -> dict | None:
    """③⑥ 센서별 재학습: augmentation 학습 → 운영 시나리오로 현재 모델과 비교 → 승격."""
    cfg = SENSORS[name]
    features   = bundle["features"]
    fault_base = cfg["null_col"]
    target_col = bundle.get("target") or fault_base   # nowcast: 원본 현재값 컬럼
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

        # ③ augmentation 학습셋 (원본 + lag 마스킹)
        aug = make_aug_train(train_df, fault_base, features)
        X_tr, y_tr = aug[features], aug[target_col]
        X_va, y_va = val_df[features], val_df[target_col]
        X_te, y_te = test_df[features], test_df[target_col]

        params = copy.deepcopy(LGB_PARAMS); n_est = params.pop("n_estimators")
        new_model = lgb.LGBMRegressor(**params, n_estimators=n_est)
        new_model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)],
                      callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(200)])

        # ⑥ 운영 시나리오(현재값 제외·lag 有)로 평가 — 신규 vs 기존 동일 조건
        X_te_eval = _mask_fault(X_te, fault_base, features)
        pred_new  = new_model.predict(X_te_eval)
        pred_curr = bundle["model"].predict(X_te_eval)
        r2_new    = float(r2_score(y_te, pred_new))
        rmse_new  = float(np.sqrt(mean_squared_error(y_te, pred_new)))
        mae_new   = float(mean_absolute_error(y_te, pred_new))
        r2_curr   = float(r2_score(y_te, pred_curr))
        rmse_curr = float(np.sqrt(mean_squared_error(y_te, pred_curr)))
        log.info(f"[재학습·{name}] 신규 R²={r2_new:.4f}/RMSE={rmse_new:.2f} "
                 f"vs 기존 R²={r2_curr:.4f}/RMSE={rmse_curr:.2f}")

        # 융합 승격 게이트: RMSE 개선(또는 동급) + R² 비악화 (둘 중 하나라도 악화면 보류)
        improved = (rmse_new <= rmse_curr) and (r2_new >= r2_curr - 0.01)
        if not improved:
            log.warning(f"[재학습·{name}] 성능 미달 — 교체 보류 "
                        f"(RMSE {rmse_new:.2f} vs {rmse_curr:.2f}, R² {r2_new:.3f} vs {r2_curr:.3f})")
            send_discord_alert(
                f"ℹ️ [{cfg['label']} 재학습·보류] 신규 RMSE={rmse_new:.2f}/R²={r2_new:.3f} "
                f"≤ 기존 RMSE={rmse_curr:.2f}/R²={r2_curr:.3f}"
            )
            return None

        # 번들 갱신 후 저장 (피처/클립/타겟 유지)
        new_bundle = dict(bundle)
        new_bundle.update({"model": new_model, "trained_with": "lag_missingness_augmentation(long_fault)",
                           "mode": "nowcast", "target": target_col})
        path = Path(cfg["bundle_path"]); path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(new_bundle, path)
        new_bundle["lag_depth"] = _lag_depth(features)
        log.info(f"[재학습·{name}] 번들 갱신: {path}")

        # 재학습 성공 → 융합 트리거 기준(REF) 갱신: 새 모델의 운영 성능으로 최신화
        if name in REF_METRICS:
            REF_METRICS[name] = {"rmse": rmse_new, "mae": mae_new, "r2": r2_new}
            log.info(f"[재학습·{name}] REF 갱신 → {REF_METRICS[name]}")

        try:
            with mlflow.start_run(run_name=f"retrain_{name}_{datetime.now().strftime('%Y%m%d_%H%M')}"):
                mlflow.log_params({"sensor": name, "trigger": trigger_reason, "mode": "nowcast",
                                   "train_rows_aug": len(aug), "r2_previous": r2_curr,
                                   "rmse_previous": rmse_curr})
                mlflow.log_metrics({"test_r2": r2_new, "test_rmse": rmse_new, "test_mae": mae_new,
                                    "r2_improvement": r2_new - r2_curr,
                                    "rmse_improvement": rmse_curr - rmse_new})
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
            send_discord_alert(f"✅ [{cfg['label']} 재학습+승격] R²={r2_new:.4f} (기존 {r2_curr:.4f}) "
                               f"RMSE={rmse_new:.2f}{cfg['unit']} (기존 {rmse_curr:.2f})")
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

        # 제어 — 예측·실측을 coalesce 해서 사용 (매시 한쪽은 실측, 한쪽은 예측)
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

        # 센서별 모니터링 + 트리거 (RMSE·MAE·R² 융합 판단)
        for name, cfg in SENSORS.items():
            metrics = run_monitoring(self.db2, self.db3, cfg, ref_values=self.ref[name])
            if metrics is None:
                continue

            # ── 성능 열화: 세 지표 융합 (상대 열화 + 다수결) ──
            perf_degraded, reasons = fusion_retrain_decision(name, metrics)
            log.info(f"[재학습 판단·{name}] {'열화' if perf_degraded else '정상'} | "
                     + " · ".join(reasons))

            trigger = None
            if perf_degraded:
                trigger = "perf_fusion"
                send_discord_alert(
                    f"🚨 [{cfg['label']} 성능 열화·재학습] " + " · ".join(reasons) +
                    f"\n(n={metrics['n_samples']}, RMSE={metrics['rmse']:.2f}{cfg['unit']} "
                    f"MAE={metrics['mae']:.2f} R²={metrics['r2']:.3f})"
                )
            elif metrics["drift_detected"]:
                kp = metrics.get("ks_pvalue")
                trigger = "ks_drift" if (kp and kp < KS_PVALUE_THRESHOLD) else "evidently_drift"

            if trigger:
                log.warning(f"🔄 [{name}] 재학습 트리거: {trigger} | "
                            f"RMSE={metrics['rmse']:.2f} MAE={metrics['mae']:.2f} R²={metrics['r2']:.3f}")
                nb = run_retrain(self.db1, name, self.bundles[name], trigger_reason=trigger)
                if nb is not None:
                    self.bundles[name] = nb
                    self.ref[name] = self._load_ref(cfg["db3_col"])
                    log.info(f"✅ [{name}] 모델 핫스왑 완료")
            else:
                log.info(f"✅ [{name}] 성능 정상 (RMSE={metrics['rmse']:.2f} R²={metrics['r2']:.3f})")

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
    log.info("스마트팜 MLOps 파이프라인 시작 (v4.1 — nowcast · 다중 센서 + augmentation + 융합 트리거)")
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
