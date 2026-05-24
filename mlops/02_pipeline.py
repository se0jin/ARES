"""
02_pipeline.py — 스마트팜 CO2 예측 실시간 MLOps 파이프라인
=====================================================================
강의교안 1~10강 적용 항목:
  01강  schedule CT(지속적 학습), MLflow Tracking
  02강  MLflow Model Registry (Staging → Production 자동 승격)
  05강  mlflow.log_params / log_metric 완전 분리
  06강  FastAPI 예측 API 서버 (별도 app.py 동시 참조)
  07강  Dockerfile 작성 안내 (파이프라인 내 경로 관리)
  08강  GitHub Actions CI/CD 트리거 안내
  09강  Discord Webhook 실시간 경고, Evidently AI 드리프트
  10강  KS Test 드리프트 탐지, 재학습 트리거 4종, Production 승격 비교

동작 흐름 (1시간 주기 schedule):
  1. DB2 폴링   : co2_in = Null + co2_predicted = Null 인 신규 행 감지
  2. 피처 구성  : 직전 3행 → 28개 피처 (co2_predicted lag 재활용)
  3. 예측       : lgbm_co2_1h.pkl → co2_predicted (300~2000 ppm 클리핑)
  4. DB2 업데이트: co2_predicted 컬럼 채움
  5. 규칙 기반 제어: 4채널 릴레이 ON 시간 계산 (논문 근거 우선순위 계층)
  6. 모니터링   : DB2 예측값 vs DB3 실제값 비교 → MLflow 기록
  7. 드리프트   : Evidently AI + KS Test → Discord 경고
  8. 재학습 판단: 4종 트리거 → DB1 최신 데이터 재학습 → Production 승격

필요 패키지:
  pip install supabase mlflow lightgbm scikit-learn joblib schedule \
              evidently scipy pandas numpy requests fastapi uvicorn
"""

# ─────────────────────────────────────────────────────────────────────
# 0. 임포트 & 설정
# ─────────────────────────────────────────────────────────────────────
import copy
import logging
import warnings
from datetime import datetime, timezone, timedelta
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

# ── 로깅 설정 ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# 1. 환경 변수 & 상수
# ─────────────────────────────────────────────────────────────────────
import os

# ── Supabase 연결 (DB1: 학습 / DB2: 예측 입출력 / DB3: 실제값 모니터링)
SUPABASE_URL_DB1 = os.getenv("SUPABASE_URL_DB1", "https://your-db1.supabase.co")
SUPABASE_KEY_DB1 = os.getenv("SUPABASE_KEY_DB1", "your-db1-key")
SUPABASE_URL_DB2 = os.getenv("SUPABASE_URL_DB2", "https://your-db2.supabase.co")
SUPABASE_KEY_DB2 = os.getenv("SUPABASE_KEY_DB2", "your-db2-key")
SUPABASE_URL_DB3 = os.getenv("SUPABASE_URL_DB3", "https://your-db3.supabase.co")
SUPABASE_KEY_DB3 = os.getenv("SUPABASE_KEY_DB3", "your-db3-key")

# ── 09강: Discord Webhook URL ────────────────────────────────────────
#   Discord 서버 → 채널 편집 → 연동 → 웹후크 → 새 웹후크 → URL 복사
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── 경로 ─────────────────────────────────────────────────────────────
MODEL_PATH          = Path("./models/lgbm_co2_1h.pkl")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
EXPERIMENT_NAME     = "smartfarm_co2_monitoring"
REGISTRY_MODEL_NAME = "smartfarm_co2_lgbm"   # MLflow Model Registry 이름

# ── 재학습 트리거 임계값 (10강: 4종 트리거) ─────────────────────────
RETRAIN_R2_THRESHOLD    = 0.85   # ① R² 기준 이하
KS_PVALUE_THRESHOLD     = 0.05   # ② KS Test p-value < 0.05 → 드리프트
RETRAIN_WINDOW          = 48     # 최근 N개 예측으로 성능 평가
RETRAIN_DATA_MIN_ROWS   = 500    # ③ 신규 데이터 최소 누적량
RETRAIN_SCHEDULE_HOUR   = 2      # ④ 정기 배치: 매일 새벽 2시 (schedule 별도 등록)

# ── 09강: Discord 경고 임계값 ────────────────────────────────────────
DISCORD_R2_THRESHOLD    = 0.80   # R² < 0.80 이면 경고 발송

# ── 모델 예측 범위 ────────────────────────────────────────────────────
CO2_CLIP_MIN = 300
CO2_CLIP_MAX = 2000

# ── 피처 / 타겟 (01_train_v2.ipynb 동일 순서) ───────────────────────
FEATURE_COLS = [
    "temp_in", "hum_in", "co2_in", "soil_hum",
    "temp_out", "rain_out", "wind_out",
    "temp_diff",
    "hour", "month", "day_of_week", "is_daytime",
    "hour_sin", "hour_cos", "month_sin", "month_cos",
    "temp_in_lag1",  "temp_in_lag2",  "temp_in_lag3",
    "hum_in_lag1",   "hum_in_lag2",   "hum_in_lag3",
    "co2_in_lag1",   "co2_in_lag2",   "co2_in_lag3",
    "soil_hum_lag1", "soil_hum_lag2", "soil_hum_lag3",
]
TARGET_COL   = "next_co2_in"
LAG_COLS_RAW = ["temp_in", "hum_in", "co2_in", "soil_hum"]

# ── LightGBM 재학습 하이퍼파라미터 ──────────────────────────────────
LGB_PARAMS = dict(
    n_estimators      = 1000,
    learning_rate     = 0.05,
    max_depth         = 6,
    num_leaves        = 63,
    min_child_samples = 20,
    subsample         = 0.8,
    subsample_freq    = 1,
    colsample_bytree  = 0.8,
    reg_alpha         = 0.1,
    reg_lambda        = 1.0,
    objective         = "regression",
    metric            = "rmse",
    random_state      = 42,
    n_jobs            = -1,
    verbose           = -1,
)


# ─────────────────────────────────────────────────────────────────────
# 2. 초기화
# ─────────────────────────────────────────────────────────────────────
def init_clients() -> tuple[Client, Client, Client]:
    db1 = create_client(SUPABASE_URL_DB1, SUPABASE_KEY_DB1)
    db2 = create_client(SUPABASE_URL_DB2, SUPABASE_KEY_DB2)
    db3 = create_client(SUPABASE_URL_DB3, SUPABASE_KEY_DB3)
    log.info("Supabase 클라이언트 초기화 완료 (DB1/DB2/DB3)")
    return db1, db2, db3


def load_model() -> lgb.LGBMRegressor:
    model = joblib.load(MODEL_PATH)
    log.info(f"모델 로드: {MODEL_PATH}  best_iter={getattr(model, 'best_iteration_', 'N/A')}")
    return model


def init_mlflow():
    """02강: MLflow Tracking + Model Registry 초기화"""
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)
    log.info(f"MLflow 초기화: {MLFLOW_TRACKING_URI} / {EXPERIMENT_NAME}")


# ─────────────────────────────────────────────────────────────────────
# 3. 피처 엔지니어링
# ─────────────────────────────────────────────────────────────────────
def build_features(target_row: dict, lag_rows: list[dict]) -> pd.DataFrame:
    """
    DB2 신규 1행 + 직전 3행 → 28개 피처 DataFrame(1행)
    co2_in = Null 일 때 co2_predicted로 lag 피처 재활용 (오차 누적 최소화)
    """
    row = dict(target_row)

    # co2_in Null 처리 → co2_predicted 재활용
    if row.get("co2_in") is None:
        row["co2_in"] = (
            row.get("co2_predicted")
            or lag_rows[0].get("co2_predicted")
            or lag_rows[0].get("co2_in", 700.0)
        )

    dt = pd.to_datetime(row["datetime"])

    # 파생 피처
    # None 방어 처리
    temp_in  = float(row.get("temp_in")  or 20.0)
    temp_out = float(row.get("temp_out") or 15.0)
    row["temp_in"]  = temp_in
    row["temp_out"] = temp_out
    row["temp_diff"] = temp_in - temp_out

    # 시간 피처
    row["hour"]        = dt.hour
    row["month"]       = dt.month
    row["day_of_week"] = dt.dayofweek
    row["is_daytime"]  = float(6 <= dt.hour <= 19)
    row["hour_sin"]    = np.sin(2 * np.pi * dt.hour / 24)
    row["hour_cos"]    = np.cos(2 * np.pi * dt.hour / 24)
    row["month_sin"]   = np.sin(2 * np.pi * dt.month / 12)
    row["month_cos"]   = np.cos(2 * np.pi * dt.month / 12)

    # lag 피처 (co2 → co2_predicted 재활용)
    for lag_idx, lag_row in enumerate(lag_rows, start=1):
        for col in LAG_COLS_RAW:
            val = (lag_row.get("co2_predicted") or lag_row.get(col)) if col == "co2_in" \
                  else lag_row.get(col)
            row[f"{col}_lag{lag_idx}"] = float(val) if val is not None else np.nan

    feat_df = pd.DataFrame([{k: row.get(k, np.nan) for k in FEATURE_COLS}])

    # 모든 피처 float 변환 (object 타입 방지)
    for col in feat_df.columns:
        feat_df[col] = pd.to_numeric(feat_df[col], errors="coerce")

    if feat_df.isna().sum().sum() > 0:
        log.warning("피처 NaN 발생 → ffill/bfill 처리")
        feat_df = feat_df.ffill().bfill()

    return feat_df


# ─────────────────────────────────────────────────────────────────────
# 4. DB2 폴링 & 예측
# ─────────────────────────────────────────────────────────────────────
def poll_and_predict(db2: Client, model: lgb.LGBMRegressor) -> list[dict]:
    """
    co2_in = Null + co2_predicted = Null 행 감지 → 예측 → DB2 업데이트
    Returns: 예측 완료된 행 목록 (제어 판단용)
    """
    res = (
        db2.table("sensor_data_2")
        .select("*")
        .is_("co2_in", "null")
        .is_("co2_predicted", "null")
        .order("datetime", desc=False)
        .limit(10)
        .execute()
    )

    target_rows = res.data
    if not target_rows:
        log.info("신규 Null 행 없음 — 대기 중")
        return []

    log.info(f"감지된 Null 행: {len(target_rows)}개")
    predicted_rows = []

    for target_row in target_rows:
        try:
            dt_str = target_row["datetime"]

            lag_res = (
                db2.table("sensor_data_2")
                .select("*")
                .lt("datetime", dt_str)
                .order("datetime", desc=True)
                .limit(3)
                .execute()
            )
            lag_rows = lag_res.data
            if len(lag_rows) < 3:
                log.warning(f"lag 행 부족 ({len(lag_rows)}개) — 스킵: {dt_str}")
                continue

            feat_df = build_features(target_row, lag_rows)
            pred    = float(np.clip(model.predict(feat_df)[0], CO2_CLIP_MIN, CO2_CLIP_MAX))

            db2.table("sensor_data_2").update(
                {"co2_predicted": round(pred, 2)}
            ).eq("ID", target_row["ID"]).execute()

            log.info(f"  [{dt_str}] co2_predicted = {pred:.1f} ppm")
            target_row["co2_predicted"] = pred
            predicted_rows.append(target_row)

        except Exception as e:
            log.error(f"예측 오류 ({target_row.get('datetime', '?')}): {e}")

    return predicted_rows


# ─────────────────────────────────────────────────────────────────────
# 5. 규칙 기반 제어 — 4채널 릴레이
# ─────────────────────────────────────────────────────────────────────
def compute_control(co2_pred: float, temp_in: float, hum_in: float,
                    soil_hum: float, temp_out: float, rain_out: float,
                    wind_out: float, solar_out: float, hour: int) -> dict:
    """
    논문 근거 우선순위 계층 (Safety → Base → Adjust → Clip)
    CH1=환기팬, CH2=창문, CH3=히터, CH4=워터펌프
    반환값: 초/주기 (환기팬·창문·히터: 600초 주기 / 워터펌프: 180초 주기)
    """
    CYCLE   = 600
    CYCLE_P = 180

    # ── 환기팬 (CH1) ──────────────────────────────────────────────
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

    # ── 창문 (CH2) ───────────────────────────────────────────────
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

    # ── 히터 (CH3) ───────────────────────────────────────────────
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

    # ── 워터펌프 (CH4) ───────────────────────────────────────────
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

    return {
        "fan_on_sec":    fan_sec,
        "window_on_sec": win_sec,
        "heater_on_sec": heat_sec,
        "pump_on_sec":   pump_sec,
    }


# ─────────────────────────────────────────────────────────────────────
# 6. 09강: Discord Webhook 실시간 경고
# ─────────────────────────────────────────────────────────────────────
def send_discord_alert(message: str):
    """
    09강: 성능 임계값 이하 시 Discord Webhook으로 자동 경고 발송
    DISCORD_WEBHOOK_URL 환경변수 미설정 시 로그만 출력
    """
    if not DISCORD_WEBHOOK_URL:
        log.warning(f"[Discord 경고 — Webhook 미설정] {message}")
        return

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=5,
        )
        if resp.status_code in (200, 204):
            log.info(f"Discord 경고 발송 완료: {message[:60]}...")
        else:
            log.warning(f"Discord 발송 실패 ({resp.status_code}): {message}")
    except Exception as e:
        log.error(f"Discord 요청 오류: {e}")


# ─────────────────────────────────────────────────────────────────────
# 7. 10강: KS Test 데이터 드리프트 탐지
# ─────────────────────────────────────────────────────────────────────
def run_ks_test(ref_values: np.ndarray, curr_values: np.ndarray,
                feature_name: str = "co2") -> dict:
    """
    10강: KS Test — 두 분포 비교 → p-value < 0.05 이면 드리프트 판정
    ref_values  : 기준 데이터 (train.csv의 co2_in 분포 등)
    curr_values : 현재 실시간 예측값 분포
    """
    if len(ref_values) < 5 or len(curr_values) < 5:
        return {"ks_stat": None, "ks_pvalue": None, "drift": False}

    ks_stat, p_value = stats.ks_2samp(ref_values, curr_values)
    drift = bool(p_value < KS_PVALUE_THRESHOLD)

    log.info(
        f"KS Test [{feature_name}]  statistic={ks_stat:.4f}  "
        f"p-value={p_value:.4f}  drift={'⚠️ 감지' if drift else '✅ 없음'}"
    )
    return {"ks_stat": float(ks_stat), "ks_pvalue": float(p_value), "drift": drift}


# ─────────────────────────────────────────────────────────────────────
# 8. 09강: 모니터링 — 예측 vs 실제 + Evidently + KS Test
# ─────────────────────────────────────────────────────────────────────
def run_monitoring(db2: Client, db3: Client,
                   ref_co2_values: np.ndarray | None = None) -> dict | None:
    """
    DB2 예측값 vs DB3 실제값 비교
    → RMSE/MAE/R²/MAPE 계산
    → Evidently AI 드리프트 리포트
    → KS Test 드리프트 탐지
    → MLflow 기록 (05강: params + metrics 분리)
    → Discord 경고 (09강)
    """
    # DB2 최근 예측값
    res2 = (
        db2.table("sensor_data_2")
        .select("datetime, co2_predicted")
        .not_.is_("co2_predicted", "null")
        .order("datetime", desc=True)
        .limit(RETRAIN_WINDOW)
        .execute()
    )
    pred_df = pd.DataFrame(res2.data)
    if pred_df.empty:
        log.warning("모니터링: DB2 예측값 없음")
        return None

    # DB3 실제값
    dt_list = pred_df["datetime"].tolist()
    res3 = (
        db3.table("sensor_data_3")
        .select("datetime, co2_in")
        .in_("datetime", dt_list)
        .execute()
    )
    actual_df = pd.DataFrame(res3.data)
    if actual_df.empty:
        log.warning("모니터링: DB3 실제값 없음 (아직 누적 중)")
        return None

    merged = pd.merge(pred_df, actual_df, on="datetime", how="inner")
    if len(merged) < 5:
        log.warning(f"모니터링: 매칭 행 부족 ({len(merged)}개)")
        return None

    y_pred = merged["co2_predicted"].values.astype(float)
    y_true = merged["co2_in"].values.astype(float)

    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mae  = float(mean_absolute_error(y_true, y_pred))
    r2   = float(r2_score(y_true, y_pred))
    denom = np.where(np.abs(y_true) < 1.0, 1.0, np.abs(y_true))
    mape  = float(np.mean(np.abs((y_true - y_pred) / denom)) * 100)

    log.info(
        f"모니터링 (n={len(merged)})  "
        f"RMSE={rmse:.2f}  MAE={mae:.2f}  R²={r2:.4f}  MAPE={mape:.2f}%"
    )

    # ── 10강: KS Test 드리프트 탐지 ─────────────────────────────
    ks_result = {"ks_stat": None, "ks_pvalue": None, "drift": False}
    if ref_co2_values is not None and len(ref_co2_values) > 0:
        ks_result = run_ks_test(ref_co2_values, y_pred, feature_name="co2_predicted")

    # ── 09강: Evidently AI 드리프트 리포트 ──────────────────────
    evidently_drift = False
    try:
        from evidently.report import Report
        from evidently.metric_preset import DataDriftPreset

        ref_df  = pd.DataFrame({"co2": ref_co2_values}) if ref_co2_values is not None \
                  else pd.DataFrame({"co2": y_true})
        curr_df = pd.DataFrame({"co2": y_pred})

        report = Report(metrics=[DataDriftPreset()])
        report.run(reference_data=ref_df, current_data=curr_df)
        result_dict      = report.as_dict()
        evidently_drift  = result_dict["metrics"][0]["result"]["dataset_drift"]

        Path("reports").mkdir(exist_ok=True)
        report_path = f"reports/drift_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
        report.save_html(report_path)
        log.info(f"Evidently 리포트 저장: {report_path}  drift={evidently_drift}")

    except ImportError:
        log.info("Evidently 미설치 — 드리프트 감지 스킵 (pip install evidently)")
    except Exception as e:
        log.warning(f"Evidently 오류: {e}")

    drift_detected = evidently_drift or ks_result["drift"]

    # ── 05강/09강: MLflow Tracking — params / metrics 완전 분리 ─
    try:
        with mlflow.start_run(
            run_name=f"monitor_{datetime.now().strftime('%Y%m%d_%H%M')}"
        ):
            # log_params: 설정값
            mlflow.log_params({
                "retrain_r2_threshold":  RETRAIN_R2_THRESHOLD,
                "ks_pvalue_threshold":   KS_PVALUE_THRESHOLD,
                "monitoring_window":     RETRAIN_WINDOW,
                "model_path":            str(MODEL_PATH),
                "n_samples":             len(merged),
            })
            # log_metrics: 성능 지표
            mlflow.log_metrics({
                "rmse":          rmse,
                "mae":           mae,
                "r2":            r2,
                "mape":          mape,
                "ks_stat":       ks_result["ks_stat"] or -1.0,
                "ks_pvalue":     ks_result["ks_pvalue"] or -1.0,
                "drift_flag":    float(drift_detected),
            })
        log.info("MLflow 기록 완료")
    except Exception as e:
        log.warning(f"MLflow 기록 실패: {e}")

    # ── 09강: Discord 경고 ───────────────────────────────────────
    if r2 < DISCORD_R2_THRESHOLD:
        alert_msg = (
            f"🚨 [스마트팜 CO2 모델 경고]\n"
            f"R² = {r2:.4f} < 기준 {DISCORD_R2_THRESHOLD}\n"
            f"RMSE = {rmse:.2f} ppm | MAPE = {mape:.2f}%\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"→ 자동 재학습 검토 중"
        )
        send_discord_alert(alert_msg)

    if drift_detected:
        drift_msg = (
            f"⚠️ [스마트팜 데이터 드리프트 감지]\n"
            f"KS p-value={ks_result['ks_pvalue']:.4f} | Evidently={evidently_drift}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"→ 재학습 트리거 발동"
        )
        send_discord_alert(drift_msg)

    return {
        "rmse": rmse, "mae": mae, "r2": r2, "mape": mape,
        "drift_detected": drift_detected,
        "ks_pvalue": ks_result["ks_pvalue"],
        "n_samples": len(merged),
    }


# ─────────────────────────────────────────────────────────────────────
# 9. 10강: 자동 재학습 — 4종 트리거 + Production 승격 비교
# ─────────────────────────────────────────────────────────────────────
def run_retrain(db1: Client, trigger_reason: str = "manual") -> lgb.LGBMRegressor | None:
    """
    10강: DB1 최신 데이터 재학습 → 기존 Production 모델과 성능 비교
          성능 향상 시 Model Registry Production 승격 → Discord 알림

    trigger_reason: "r2_drop" | "ks_drift" | "evidently_drift" | "scheduled" | "manual"
    """
    log.info(f"=== [재학습 시작] 트리거: {trigger_reason} ===")

    try:
        # DB1 전체 데이터 로드
        res = db1.table("sensor_data").select("*").order("datetime").execute()
        raw_df = pd.DataFrame(res.data)
        log.info(f"DB1 로드: {len(raw_df):,}행")

        if len(raw_df) < RETRAIN_DATA_MIN_ROWS:
            log.warning(f"재학습 데이터 부족 ({len(raw_df)} < {RETRAIN_DATA_MIN_ROWS}행) — 스킵")
            return None

        required = FEATURE_COLS + [TARGET_COL]
        missing  = [c for c in required if c not in raw_df.columns]
        if missing:
            log.error(f"재학습 컬럼 부재: {missing}")
            return None

        raw_df = raw_df.dropna(subset=required).sort_values("datetime").reset_index(drop=True)
        n = len(raw_df)
        train_df = raw_df.iloc[:int(n * 0.70)]
        val_df   = raw_df.iloc[int(n * 0.70):int(n * 0.85)]
        test_df  = raw_df.iloc[int(n * 0.85):]

        X_tr, y_tr = train_df[FEATURE_COLS], train_df[TARGET_COL]
        X_va, y_va = val_df[FEATURE_COLS],   val_df[TARGET_COL]
        X_te, y_te = test_df[FEATURE_COLS],  test_df[TARGET_COL]

        # 재학습
        params = copy.deepcopy(LGB_PARAMS)
        n_est  = params.pop("n_estimators")
        new_model = lgb.LGBMRegressor(**params, n_estimators=n_est)
        new_model.fit(
            X_tr, y_tr,
            eval_set=[(X_va, y_va)],
            callbacks=[
                lgb.early_stopping(stopping_rounds=50, verbose=False),
                lgb.log_evaluation(period=200),
            ],
        )

        # 신규 모델 성능
        pred_new  = new_model.predict(X_te)
        rmse_new  = float(np.sqrt(mean_squared_error(y_te, pred_new)))
        r2_new    = float(r2_score(y_te, pred_new))
        mae_new   = float(mean_absolute_error(y_te, pred_new))
        log.info(f"[재학습] 신규 모델 — RMSE={rmse_new:.4f}  R²={r2_new:.4f}")

        # 10강: 기존 Production 모델과 성능 비교
        current_model = load_model()
        pred_curr = current_model.predict(X_te)
        r2_curr   = float(r2_score(y_te, pred_curr))
        log.info(f"[재학습] 기존 모델 — R²={r2_curr:.4f}")

        if r2_new <= r2_curr:
            log.warning(
                f"[재학습] 신규 모델 성능 미달 (R²: {r2_new:.4f} ≤ {r2_curr:.4f}) "
                f"— Production 교체 보류"
            )
            send_discord_alert(
                f"ℹ️ [재학습 완료 — 교체 보류]\n"
                f"신규 R²={r2_new:.4f} ≤ 기존 R²={r2_curr:.4f}\n"
                f"현재 모델 유지"
            )
            return None

        # pkl 갱신
        joblib.dump(new_model, MODEL_PATH)
        log.info(f"[재학습] pkl 갱신 완료: {MODEL_PATH}")

        # 10강: MLflow Model Registry 등록 + Production 승격
        try:
            with mlflow.start_run(
                run_name=f"retrain_{datetime.now().strftime('%Y%m%d_%H%M')}"
            ):
                # log_params: 재학습 설정
                mlflow.log_params({
                    "trigger_reason":    trigger_reason,
                    "train_rows":        len(train_df),
                    "val_rows":          len(val_df),
                    "test_rows":         len(test_df),
                    "best_iter":         getattr(new_model, "best_iteration_", -1),
                    "r2_previous":       r2_curr,
                })
                # log_metrics: 성능 지표
                mlflow.log_metrics({
                    "test_rmse": rmse_new,
                    "test_mae":  mae_new,
                    "test_r2":   r2_new,
                    "r2_improvement": r2_new - r2_curr,
                })

                # 02강: Model Registry 등록
                model_info = mlflow.sklearn.log_model(
                    new_model,
                    artifact_path="lgbm_co2",
                    registered_model_name=REGISTRY_MODEL_NAME,
                )

            # 10강: Production 승격 (MlflowClient 사용)
            from mlflow.tracking import MlflowClient
            client      = MlflowClient()
            latest_vers = client.get_latest_versions(REGISTRY_MODEL_NAME, stages=["None"])
            if latest_vers:
                new_ver = latest_vers[-1].version
                client.transition_model_version_stage(
                    name    = REGISTRY_MODEL_NAME,
                    version = new_ver,
                    stage   = "Production",
                    archive_existing_versions=True,  # 기존 Production → Archive
                )
                log.info(f"Model Registry: v{new_ver} → Production 승격 (기존 → Archive)")

            send_discord_alert(
                f"✅ [재학습 + Production 승격]\n"
                f"트리거: {trigger_reason}\n"
                f"신규 R²={r2_new:.4f} > 기존 R²={r2_curr:.4f}\n"
                f"RMSE={rmse_new:.2f} ppm\n"
                f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

        except Exception as e:
            log.warning(f"MLflow Registry 처리 실패: {e}")

        return new_model

    except Exception as e:
        log.error(f"재학습 오류: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# 10. 파이프라인 클래스 — 전체 통합
# ─────────────────────────────────────────────────────────────────────
class Pipeline:
    def __init__(self):
        self.db1, self.db2, self.db3 = init_clients()
        self.model        = load_model()
        self.ref_co2      = self._load_ref_co2()   # KS Test 기준 분포
        init_mlflow()
        log.info("Pipeline 초기화 완료")

    def _load_ref_co2(self) -> np.ndarray | None:
        """train.csv가 있으면 co2_in 분포를 기준값으로 로드 (KS Test용)"""
        ref_paths = [
            Path("./models/train_co2_ref.npy"),          # 미리 저장된 npy
            Path("/content/drive/MyDrive/스마트팜 프로젝트"
                 "/data/2차데이터/최종 데이터(전처리 후)/train.csv"),
        ]
        for p in ref_paths:
            if p.suffix == ".npy" and p.exists():
                arr = np.load(str(p))
                log.info(f"KS Test 기준값 로드: {p}  ({len(arr)}개)")
                return arr
            if p.suffix == ".csv" and p.exists():
                df = pd.read_csv(p)
                if "co2_in" in df.columns:
                    arr = df["co2_in"].dropna().values
                    np.save("./models/train_co2_ref.npy", arr)
                    log.info(f"KS Test 기준값 로드 (CSV): {len(arr)}개 → npy 캐싱")
                    return arr
        log.info("KS Test 기준값 없음 — KS Test 스킵")
        return None

    def reload_model(self):
        self.model = load_model()

    def hourly_step(self):
        """매 1시간마다 실행 — 예측 → 제어 → 모니터링 → 재학습 판단"""
        log.info("=" * 65)
        log.info(f"[HOURLY STEP] {datetime.now(timezone.utc).isoformat()}")

        # Step 1~4: DB2 폴링 & 예측
        predicted_rows = poll_and_predict(self.db2, self.model)

        # Step 5: 제어 계산
        for row in predicted_rows:
            try:
                ctrl = compute_control(
                    co2_pred  = float(row.get("co2_predicted", 700)),
                    temp_in   = float(row.get("temp_in",  20)),
                    hum_in    = float(row.get("hum_in",   60)),
                    soil_hum  = float(row.get("soil_hum", 30)),
                    temp_out  = float(row.get("temp_out", 15)),
                    rain_out  = float(row.get("rain_out",  0)),
                    wind_out  = float(row.get("wind_out",  2)),
                    solar_out = float(row.get("solar_out", 0)),
                    hour      = pd.to_datetime(row["datetime"]).hour,
                )
                log.info(
                    f"  제어 [{row['datetime']}] CO2={row['co2_predicted']:.0f}ppm | "
                    f"팬={ctrl['fan_on_sec']}s 창문={ctrl['window_on_sec']}s "
                    f"히터={ctrl['heater_on_sec']}s 펌프={ctrl['pump_on_sec']}s"
                )
                # DB2 제어 컬럼 업데이트 생략 (sensor_data_2에 제어 컬럼 없음)
                # 제어는 smartfarm_controller.py가 직접 담당
                pass
            except Exception as e:
                log.error(f"제어 계산 오류: {e}")

        # Step 6~7: 모니터링 + 드리프트 감지 + Discord 경고
        metrics = run_monitoring(self.db2, self.db3, ref_co2_values=self.ref_co2)

        # Step 8: 재학습 트리거 판단 (10강 4종 트리거 중 이벤트 기반 2종)
        #   ③ 배치(정기) 재학습은 scheduled_step()에서 처리
        #   ④ 신규 데이터 누적량은 run_retrain() 내부에서 체크
        if metrics is not None:
            trigger = None
            if metrics["r2"] < RETRAIN_R2_THRESHOLD:
                trigger = "r2_drop"
            elif metrics["drift_detected"]:
                ks_p = metrics.get("ks_pvalue")
                trigger = "ks_drift" if (ks_p and ks_p < KS_PVALUE_THRESHOLD) \
                          else "evidently_drift"

            if trigger:
                log.warning(f"🔄 재학습 트리거: {trigger}  R²={metrics['r2']:.4f}")
                new_model = run_retrain(self.db1, trigger_reason=trigger)
                if new_model is not None:
                    self.model = new_model          # 핫스왑
                    log.info("✅ 모델 핫스왑 완료")
            else:
                log.info(f"✅ 성능 정상 (R²={metrics['r2']:.4f}) — 재학습 불필요")

        log.info("[HOURLY STEP] 완료")

    def scheduled_step(self):
        """10강 ④ 정기 배치 재학습 — 매일 새벽 2시"""
        log.info("=" * 65)
        log.info(f"[SCHEDULED RETRAIN] {datetime.now().isoformat()}")
        new_model = run_retrain(self.db1, trigger_reason="scheduled")
        if new_model is not None:
            self.model = new_model
            log.info("✅ 정기 재학습 완료 — 모델 핫스왑")
        log.info("[SCHEDULED RETRAIN] 완료")


# ─────────────────────────────────────────────────────────────────────
# 11. 진입점
# ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 65)
    log.info("스마트팜 MLOps 파이프라인 시작")
    log.info("강의교안 1~10강 MLOps 구성요소 통합 적용")
    log.info("=" * 65)

    pipeline = Pipeline()

    # 즉시 1회 실행
    pipeline.hourly_step()

    # 01강 CT: 1시간 주기 이벤트 기반 재학습
    schedule.every(1).hours.do(pipeline.hourly_step)

    # 10강 ④: 매일 새벽 2시 정기 배치 재학습
    schedule.every().day.at(f"{RETRAIN_SCHEDULE_HOUR:02d}:00").do(pipeline.scheduled_step)

    log.info("스케줄 등록 완료:")
    log.info("  - 매 1시간: 예측 + 모니터링 + 이벤트 기반 재학습")
    log.info(f"  - 매일 {RETRAIN_SCHEDULE_HOUR:02d}:00: 정기 배치 재학습")
    log.info("Ctrl+C 로 종료")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
