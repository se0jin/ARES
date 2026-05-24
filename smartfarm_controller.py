#!/usr/bin/env python3
"""
스마트팜 통합 제어 시스템 (Raspberry Pi 5 전용)
─────────────────────────────────────────────────────────────────
[확인된 배선]  ← docx CH 배정 기준으로 수정
  릴레이 4채널 (Active-LOW)
    GPIO5  → CH1 → 환기팬
    GPIO6  → CH2 → 창문 (스테퍼 OPEN 릴레이)
    GPIO13 → CH3 → 히터
    GPIO19 → CH4 → 워터펌프
    GPIO26 → LED  → 식물생장 LED (별도 릴레이)

  DRV8825 스테퍼모터 (창문)
    GPIO24 → STEP (PUL+)#!/usr/bin/env python3
"""
스마트팜 제어기 (제어 전용 — 수집기와 별도 실행)

- DB2에서 최신 센서값 읽어서 제어 계산
- CO2 고장 시 fallback (DB2 예측값 → Render API → 기본값 700)
- 4채널 릴레이 + 스테퍼 모터 제어
- DB1 next_co2_in 업데이트 (재학습 타겟 누적)
- 주기: 1시간 (수집기와 동일 주기)
"""

import time
import board
import busio
import serial
import requests
import schedule
import logging

from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from supabase import create_client

import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ─────────────────────────────────────────────────────────────────
# 로거
# ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("smartfarm_ctrl")

# ─────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────

SUPABASE_URL  = "https://hheeyhsiaqhxgufrvkui.supabase.co"
SUPABASE_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWV5aHNpYXFoeGd1ZnJ2a3VpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkxMzc0MjIsImV4cCI6MjA5NDcxMzQyMn0.ri_w6QVJwgTIf2U2hp1L30uasJVO4Igqo08Wg8hF0xk"

SUPABASE_URL2 = "https://yubqlportprwjyadsbie.supabase.co"
SUPABASE_KEY2 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1YnFscG9ydHByd2p5YWRzYmllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkzNDkwOTIsImV4cCI6MjA5NDkyNTA5Mn0.cuHidkUzm6Af5ThEL5f6EdA_MOGevsOXcuPK2wh-evk"

RENDER_API_URL = "https://smartfarm-co2-api-latest.onrender.com"

# ─────────────────────────────────────────────────────────────────
# 주기 상수
# ─────────────────────────────────────────────────────────────────

CYCLE      = 600   # 환기팬·창문·히터 주기(초) = 10분
PUMP_CYCLE = 1800  # 워터펌프 주기(초) = 30분

# ─────────────────────────────────────────────────────────────────
# GPIO 핀 번호 (BCM)
# ─────────────────────────────────────────────────────────────────

PIN_FAN    = 5
PIN_WINDOW = 6
PIN_HEATER = 13
PIN_PUMP   = 19
PIN_LED    = 26
PIN_STEP   = 24
PIN_DIR    = 25

STEP_DELAY       = 0.001
WINDOW_MAX_STEPS = 1000
_window_current_steps = 0

# ─────────────────────────────────────────────────────────────────
# 데이터 컨테이너
# ─────────────────────────────────────────────────────────────────

@dataclass
class SensorData:
    temp_in:   Optional[float] = None
    hum_in:    Optional[float] = None
    co2_in:    Optional[int]   = None
    soil_hum:  Optional[int]   = None
    lux_in:    Optional[float] = None
    temp_out:  Optional[float] = None
    rain_out:  Optional[int]   = None
    wind_out:  Optional[float] = None
    solar_out: Optional[float] = None


@dataclass
class ControlResult:
    fan_on_sec:    int  = 0
    window_on_sec: int  = 0
    heater_on_sec: int  = 0
    pump_on_sec:   int  = 0
    led_on:        bool = False
    notes:         list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# GPIO 초기화 (lgpio)
# ─────────────────────────────────────────────────────────────────

try:
    import lgpio
    _chip = lgpio.gpiochip_open(0)
    for pin in [PIN_FAN, PIN_WINDOW, PIN_HEATER, PIN_PUMP, PIN_LED, PIN_STEP, PIN_DIR]:
        lgpio.gpio_claim_output(_chip, pin, 1)
    GPIO_AVAILABLE = True
    log.info("lgpio 초기화 완료 (Pi5)")
except Exception as e:
    GPIO_AVAILABLE = False
    log.warning(f"lgpio 없음 — 시뮬레이션 모드: {e}")


def _relay_on(pin: int):
    if GPIO_AVAILABLE:
        lgpio.gpio_write(_chip, pin, 0)


def _relay_off(pin: int):
    if GPIO_AVAILABLE:
        lgpio.gpio_write(_chip, pin, 1)


def _step_motor(target_pct: int):
    global _window_current_steps
    if not GPIO_AVAILABLE:
        log.info(f"[SIM] 창문 스테퍼 -> {target_pct}%")
        return
    target_steps = int(WINDOW_MAX_STEPS * target_pct / 100)
    diff = target_steps - _window_current_steps
    if diff == 0:
        return
    direction = 1 if diff > 0 else 0
    lgpio.gpio_write(_chip, PIN_DIR, direction)
    time.sleep(0.001)
    for _ in range(abs(diff)):
        lgpio.gpio_write(_chip, PIN_STEP, 1)
        time.sleep(STEP_DELAY)
        lgpio.gpio_write(_chip, PIN_STEP, 0)
        time.sleep(STEP_DELAY)
    _window_current_steps = target_steps


# ─────────────────────────────────────────────────────────────────
# 제어 계산 함수들
# ─────────────────────────────────────────────────────────────────

def calc_fan(s: SensorData) -> tuple[int, list]:
    notes = []
    t = s.temp_in or 0.0
    if t > 32:   base = 510
    elif t > 28: base = 390
    elif t > 25: base = 270
    elif t > 22: base = 120
    else:        base = 0
    notes.append(f"[FAN] Base(temp_in={t}C) -> {base}s")
    adj = 0
    h = s.hum_in or 0.0
    if h > 85:   adj += 108; notes.append("  +108s (hum_in>85%)")
    elif h > 75: adj += 48;  notes.append("  +48s  (hum_in 75~85%)")
    c = s.co2_in or 0
    if c > 1500:   adj += 120; notes.append("  +120s (co2>1500ppm)")
    elif c > 1000: adj += 72;  notes.append("  +72s  (co2 1000~1500ppm)")
    elif c > 800:  adj += 30;  notes.append("  +30s  (co2 800~1000ppm)")
    to = s.temp_out
    if to is not None:
        if to < t - 5:  adj += 48; notes.append("  +48s (temp_out<temp_in-5)")
        elif to >= t:   adj -= 48; notes.append("  -48s (temp_out>=temp_in)")
    if (s.solar_out or 0) > 600: adj += 30; notes.append("  +30s (solar>600)")
    if (s.wind_out  or 0) > 5:   adj -= 30; notes.append("  -30s (wind>5m/s)")
    on_time = base + adj
    if s.rain_out == 1:
        on_time = min(on_time, 180)
        notes.append("  [SAFETY] rain -> cap 180s")
    on_time = max(0, min(CYCLE, on_time))
    notes.append(f"[FAN] result: {on_time}s (duty {on_time/CYCLE*100:.0f}%)")
    return on_time, notes


def calc_window(s: SensorData) -> tuple[int, list]:
    notes = []
    w    = s.wind_out or 0.0
    rain = s.rain_out or 0
    if rain == 1:
        notes.append("[WIN] [SAFETY] rain -> 0s")
        return 0, notes
    if w > 8:
        notes.append("[WIN] [SAFETY] wind>8m/s -> 0s")
        return 0, notes
    wind_cap = 180 if 5 <= w <= 8 else CYCLE
    t = s.temp_in or 0.0
    if t > 32:   base = 510
    elif t > 28: base = 390
    elif t > 25: base = 270
    elif t > 22: base = 120
    else:        base = 0
    notes.append(f"[WIN] Base(temp_in={t}C) -> {base}s")
    adj = 0
    c = s.co2_in or 0
    if c > 1500:   adj += 120; notes.append("  +120s (co2>1500ppm)")
    elif c > 1000: adj += 72;  notes.append("  +72s  (co2 1000~1500ppm)")
    h = s.hum_in or 0.0
    if h > 85: adj += 48; notes.append("  +48s (hum_in>85%)")
    to = s.temp_out
    if to is not None:
        if to < t - 8:  adj += 48;  notes.append("  +48s  (temp_out<temp_in-8)")
        elif to >= t:   adj -= 72;  notes.append("  -72s  (temp_out>=temp_in)")
    if 2 <= w <= 5:               adj += 30; notes.append("  +30s (wind 2~5m/s)")
    if (s.solar_out or 0) > 800:  adj += 30; notes.append("  +30s (solar>800)")
    on_time = max(0, min(wind_cap, base + adj))
    notes.append(f"[WIN] result: {on_time}s (duty {on_time/CYCLE*100:.0f}%)")
    return on_time, notes


def calc_heater(s: SensorData) -> tuple[int, list]:
    notes = []
    t = s.temp_in or 20.0
    if t > 18:
        notes.append(f"[HTR] [SAFETY] temp_in={t}C > 18C -> 0s")
        return 0, notes
    if t >= 15:   base = 90
    elif t >= 12: base = 210
    elif t >= 8:  base = 360
    else:         base = 480
    notes.append(f"[HTR] Base(temp_in={t}C) -> {base}s")
    adj = 0
    to = s.temp_out or 10.0
    if to < -5:      adj += 180; notes.append("  +180s (temp_out<-5C)")
    elif to < 0:     adj += 120; notes.append("  +120s (temp_out -5~0C)")
    elif to < 5:     adj += 60;  notes.append("  +60s  (temp_out 0~5C)")
    w = s.wind_out or 0.0
    if w > 7:    adj += 120; notes.append("  +120s (wind>7m/s)")
    elif w >= 3: adj += 60;  notes.append("  +60s  (wind 3~7m/s)")
    if (s.rain_out  or 0) == 1:   adj -= 30; notes.append("  -30s  (비)")
    if (s.co2_in    or 0) > 1200: adj -= 30; notes.append("  -30s  (co2>1200ppm)")
    on_time = base + adj
    if (s.solar_out or 0) > 400:
        on_time -= 120
        notes.append("  [SAFETY] solar>400 -> -120s")
    h = s.hum_in or 60.0
    if h < 40:
        on_time = min(on_time, 300)
        notes.append("  [SAFETY] hum_in<40% -> cap 300s")
    on_time = max(0, min(CYCLE, on_time))
    notes.append(f"[HTR] result: {on_time}s (duty {on_time/CYCLE*100:.0f}%)")
    return on_time, notes


def calc_pump(s: SensorData) -> tuple[int, list]:
    notes = []
    soil = s.soil_hum if s.soil_hum is not None else 50
    if s.rain_out == 1:
        notes.append("[PMP] [SAFETY] rain -> 0s")
        return 0, notes
    if soil > 70:
        notes.append(f"[PMP] [SAFETY] soil={soil}% > 70% -> 0s")
        return 0, notes
    if soil < 10:   base = 120
    elif soil < 20: base = 60
    else:
        notes.append(f"[PMP] soil={soil}% -> 0s (적정)")
        return 0, notes
    notes.append(f"[PMP] Base(soil={soil}%) -> {base}s")
    adj = 0
    t = s.temp_in or 20.0
    if t > 28:   adj += 30; notes.append("  +30s (temp_in>28C)")
    elif t < 15: adj -= 15; notes.append("  -15s (temp_in<15C)")
    h = s.hum_in or 60.0
    if h < 50:   adj += 20; notes.append("  +20s (hum_in<50%)")
    elif h > 80: adj -= 15; notes.append("  -15s (hum_in>80%)")
    sol = s.solar_out or 0.0
    if sol > 600:  adj += 20; notes.append("  +20s (solar>600)")
    elif sol < 50: adj -= 15; notes.append("  -15s (solar<50 야간)")
    if (s.wind_out  or 0) > 4:  adj += 15; notes.append("  +15s (wind>4m/s)")
    if (s.temp_out  or 0) > 30: adj += 15; notes.append("  +15s (temp_out>30C)")
    on_time = max(0, min(180, base + adj))
    notes.append(f"[PMP] result: {on_time}s")
    return on_time, notes


def calc_led(s: SensorData) -> tuple[bool, list]:
    notes = []
    hour = datetime.now().hour
    t    = s.temp_in or 20.0
    sol  = s.solar_out or 0.0
    if hour < 6 or hour >= 20:
        notes.append(f"[LED] [SAFETY] 심야({hour}h) -> OFF")
        return False, notes
    if t > 35:
        notes.append(f"[LED] [SAFETY] temp_in={t}C > 35C -> OFF")
        return False, notes
    if s.rain_out == 1 and sol < 100:
        notes.append("[LED] rain+solar<100 -> ON")
        return True, notes
    led_on = sol <= 400
    notes.append(f"[LED] solar={sol} -> {'ON' if led_on else 'OFF'}")
    if led_on:
        if t > 30:
            notes.append(f"  [ADJ] temp_in={t}C > 30C -> OFF")
            return False, notes
        h = s.hum_in or 60.0
        if h > 90:
            notes.append(f"  [ADJ] hum_in={h}% > 90% -> OFF")
            return False, notes
    notes.append(f"[LED] result: {'ON' if led_on else 'OFF'}")
    return led_on, notes


def compute_control(s: SensorData) -> ControlResult:
    result = ControlResult()
    all_notes = []
    result.fan_on_sec,    n = calc_fan(s);     all_notes.extend(n)
    result.window_on_sec, n = calc_window(s);  all_notes.extend(n)
    result.heater_on_sec, n = calc_heater(s);  all_notes.extend(n)
    result.pump_on_sec,   n = calc_pump(s);    all_notes.extend(n)
    result.led_on,        n = calc_led(s);     all_notes.extend(n)
    result.notes = all_notes
    return result


# ─────────────────────────────────────────────────────────────────
# 하드웨어 적용
# ─────────────────────────────────────────────────────────────────

def apply_control(ctrl: ControlResult):
    if ctrl.fan_on_sec > 0:
        _relay_on(PIN_FAN)
        log.info(f"[HW] 환기팬 ON -> {ctrl.fan_on_sec}s")
        time.sleep(ctrl.fan_on_sec)
        _relay_off(PIN_FAN)
    else:
        _relay_off(PIN_FAN)
        log.info("[HW] 환기팬 OFF (0s)")

    window_pct = int(ctrl.window_on_sec / CYCLE * 100)
    log.info(f"[HW] 창문 -> {window_pct}% ({ctrl.window_on_sec}s)")
    _step_motor(window_pct)

    if ctrl.heater_on_sec > 0:
        _relay_on(PIN_HEATER)
        log.info(f"[HW] 히터 ON -> {ctrl.heater_on_sec}s")
        time.sleep(ctrl.heater_on_sec)
        _relay_off(PIN_HEATER)
    else:
        _relay_off(PIN_HEATER)
        log.info("[HW] 히터 OFF (0s)")

    if ctrl.pump_on_sec > 0:
        _relay_on(PIN_PUMP)
        log.info(f"[HW] 워터펌프 ON -> {ctrl.pump_on_sec}s")
        time.sleep(ctrl.pump_on_sec)
        _relay_off(PIN_PUMP)
    else:
        _relay_off(PIN_PUMP)
        log.info("[HW] 워터펌프 OFF (0s)")

    if ctrl.led_on:
        _relay_on(PIN_LED)
        log.info("[HW] LED ON")
    else:
        _relay_off(PIN_LED)
        log.info("[HW] LED OFF")


# ─────────────────────────────────────────────────────────────────
# DB 클라이언트 초기화
# ─────────────────────────────────────────────────────────────────

supabase  = create_client(SUPABASE_URL,  SUPABASE_KEY)
supabase2 = create_client(SUPABASE_URL2, SUPABASE_KEY2)


# ─────────────────────────────────────────────────────────────────
# DB2에서 최신 센서값 읽기
# ─────────────────────────────────────────────────────────────────

def read_latest_from_db2() -> SensorData:
    """수집기가 저장한 최신 1행을 DB2에서 읽어옴"""
    try:
        res = (
            supabase2.table("sensor_data_2")
            .select("*")
            .order("datetime", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            log.warning("[DB2] 데이터 없음")
            return SensorData()
        row = res.data[0]
        return SensorData(
            temp_in   = row.get("temp_in"),
            hum_in    = row.get("hum_in"),
            co2_in    = row.get("co2_in"),
            soil_hum  = row.get("soil_hum"),
            lux_in    = row.get("lux_in"),
            temp_out  = row.get("temp_out"),
            rain_out  = row.get("rain_out"),
            wind_out  = row.get("wind_out"),
            solar_out = row.get("solar_out"),
        )
    except Exception as e:
        log.error(f"[DB2] 읽기 실패: {e}")
        return SensorData()


# ─────────────────────────────────────────────────────────────────
# CO2 Fallback
# ─────────────────────────────────────────────────────────────────

def get_co2_with_fallback(s: SensorData) -> int:
    """
    1순위: 실제 센서값
    2순위: DB2에서 3시간 전 co2_predicted 조회
    3순위: Render API 호출
    4순위: 기본값 700
    """
    if s.co2_in is not None:
        log.info(f"[CO2] 센서 정상 → 실제값: {s.co2_in} ppm")
        return s.co2_in

    log.warning("[CO2] 센서 고장(None) → fallback 시작")

    try:
        three_hours_ago = (datetime.now() - timedelta(hours=3)).isoformat()
        rows = (
            supabase2.table("sensor_data_2")
            .select("datetime, co2_predicted")
            .not_.is_("co2_predicted", "null")
            .lte("datetime", three_hours_ago)
            .order("datetime", desc=True)
            .limit(1)
            .execute()
        )
        if rows.data:
            val = int(rows.data[0]["co2_predicted"])
            log.info(f"[CO2] DB2 fallback 성공 → {rows.data[0]['datetime']} 예측값 {val} ppm")
            return val
        else:
            log.warning("[CO2] DB2 예측값 없음 → API fallback")
    except Exception as e:
        log.error(f"[CO2] DB2 조회 실패: {e} → API fallback")

    try:
        resp = requests.post(
            f"{RENDER_API_URL}/predict/simple",
            json={
                "temp_in":  s.temp_in  or 20.0,
                "hum_in":   s.hum_in   or 60.0,
                "co2_in":   700.0,
                "soil_hum": s.soil_hum or 50,
                "temp_out": s.temp_out or 15.0,
                "rain_out": s.rain_out or 0,
                "wind_out": s.wind_out or 0.0,
            },
            timeout=10,
        )
        resp.raise_for_status()
        val = int(resp.json()["co2_predicted_ppm"])
        log.info(f"[CO2] Render API fallback 성공 → {val} ppm")
        return val
    except Exception as e:
        log.error(f"[CO2] Render API 실패: {e} → 기본값 700")

    log.warning("[CO2] 모든 fallback 실패 → 기본값 700 ppm")
    return 700


# ─────────────────────────────────────────────────────────────────
# DB1 next_co2_in 업데이트
# ─────────────────────────────────────────────────────────────────

def update_next_co2_in(raw_co2: Optional[int]):
    """3시간 전 DB1 행에 현재 co2값을 next_co2_in으로 기록 (재학습 타겟 누적)"""
    if raw_co2 is None:
        log.warning("[next_co2_in] raw_co2 없음 → 스킵")
        return
    try:
        target_dt = datetime.now() - timedelta(hours=3)
        dt_from   = (target_dt - timedelta(minutes=5)).isoformat()
        dt_to     = (target_dt + timedelta(minutes=5)).isoformat()
        rows = (
            supabase.table("sensor_data")
            .select("id, datetime")
            .gte("datetime", dt_from)
            .lte("datetime", dt_to)
            .order("datetime", desc=True)
            .limit(1)
            .execute()
        )
        if not rows.data:
            log.warning(f"[next_co2_in] 3시간 전 행 없음 ({target_dt.strftime('%H:%M')} ±5분)")
            return
        row_id = rows.data[0]["id"]
        row_dt = rows.data[0]["datetime"]
        supabase.table("sensor_data").update(
            {"next_co2_in": float(raw_co2)}
        ).eq("id", row_id).execute()
        log.info(f"[next_co2_in] 업데이트 완료 → id={row_id} ({row_dt}) = {raw_co2} ppm")
    except Exception as e:
        log.error(f"[next_co2_in] 업데이트 실패: {e}")


# ─────────────────────────────────────────────────────────────────
# 메인 루프
# ─────────────────────────────────────────────────────────────────

def job():
    log.info("=" * 60)
    log.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 제어 사이클 시작")
    log.info("=" * 60)

    # 1. DB2에서 최신 센서값 읽기 (수집기가 저장한 값)
    sensor = read_latest_from_db2()
    log.info(f"[센서] {sensor}")

    # 2. CO2 원본 보존 후 fallback 처리
    raw_co2    = sensor.co2_in
    co2_source = "sensor" if raw_co2 is not None else "fallback"
    sensor.co2_in = get_co2_with_fallback(sensor)
    log.info(f"[CO2] 원본={raw_co2} / 제어용={sensor.co2_in} ppm / source={co2_source}")

    # 3. 제어 계산
    ctrl = compute_control(sensor)
    for note in ctrl.notes:
        log.info(음표)

    # 4. 하드웨어 제어
    apply_control(ctrl)

    # 5. DB1 next_co2_in 업데이트
    update_next_co2_in(raw_co2)

    window_pct = int(ctrl.window_on_sec / CYCLE * 100)
    log.info(
        f"[결과] 팬={ctrl.fan_on_sec}s  "
        f"창문={ctrl.window_on_sec}s({window_pct}%)  "
        f"히터={ctrl.heater_on_sec}s  "
        f"펌프={ctrl.pump_on_sec}s  "
        f"LED={'ON' if ctrl.led_on else 'OFF'}"
    )


# ─────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("스마트팜 제어기 시작 (1시간 주기)")
    log.info("=" * 60)

    job()

    schedule.every(1).hours.do(job)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("종료 요청")
    finally:
        if GPIO_AVAILABLE:
            for pin in [PIN_FAN, PIN_WINDOW, PIN_HEATER, PIN_PUMP, PIN_LED]:
                lgpio.gpio_write(_chip, pin, 1)
            lgpio.gpiochip_close(_chip)
        log.info("종료 완료")
    GPIO25 → DIR

  I2C 센서
    0x23 → BH1750  (조도)
    0x44 → SHT31   (온습도)
    0x48 → ADS1115 (토양수분)

  UART
    /dev/ttyAMA0 → MH-Z19 (CO2)

[제어 논리 — 우선순위 계층 방식]
  1계층 Safety  : 강수·강풍·극한값 즉시 강제
  2계층 Base    : 핵심 센서 → 기준 ON 시간(초) 결정
  3계층 Adjust  : 보조 센서 → ±초 보정
  4계층 Clip    : max(0, min(주기, 결과)) 클리핑

[주기 정의]
  CYCLE      = 600초  (10분) — 환기팬·창문·히터
  PUMP_CYCLE = 1800초 (30분) — 워터펌프

[주의] Pi5는 RPi.GPIO 미지원 → lgpio 사용
─────────────────────────────────────────────────────────────────
"""

import time
import board
import busio
import serial
import requests
import schedule
import logging

from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from supabase import create_client

import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ─────────────────────────────────────────────────────────────────
# 로거
# ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("smartfarm")

# ─────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────

KMA_API_KEY   = "91d35aba86632a9a336b4166ddb9507a9d1a94cc8ab97e88bf34c57fc3d465ac"

SUPABASE_URL  = "https://hheeyhsiaqhxgufrvkui.supabase.co"
SUPABASE_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWV5aHNpYXFoeGd1ZnJ2a3VpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkxMzc0MjIsImV4cCI6MjA5NDcxMzQyMn0.ri_w6QVJwgTIf2U2hp1L30uasJVO4Igqo08Wg8hF0xk"

SUPABASE_URL2 = "https://yubqlportprwjyadsbie.supabase.co"
SUPABASE_KEY2 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1YnFscG9ydHByd2p5YWRzYmllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkzNDkwOTIsImV4cCI6MjA5NDkyNTA5Mn0.cuHidkUzm6Af5ThEL5f6EdA_MOGevsOXcuPK2wh-evk"

SUPABASE_URL3 = "https://grqjtjvjmgewrcujfpjd.supabase.co"
SUPABASE_KEY3 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdycWp0anZqbWdld3JjdWpmcGpkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkzNTkyODEsImV4cCI6MjA5NDkzNTI4MX0.k1I6yAL23iXkSbvaVNsbRQpoLWqijuNHoeoOCd1-2DQ"

NX, NY = 63, 89  # 천안 기상청 격자

# ─────────────────────────────────────────────────────────────────
# 주기 상수 (docx 기준)
# ─────────────────────────────────────────────────────────────────

CYCLE      = 600   # 환기팬·창문·히터 주기(초) = 10분
PUMP_CYCLE = 1800  # 워터펌프 주기(초) = 30분

# ─────────────────────────────────────────────────────────────────
# GPIO 핀 번호 (BCM) — docx CH 배정 기준
# ─────────────────────────────────────────────────────────────────

PIN_FAN    = 5    # CH1 환기팬
PIN_WINDOW = 6    # CH2 창문 (스테퍼 릴레이)
PIN_HEATER = 13   # CH3 히터
PIN_PUMP   = 19   # CH4 워터펌프
PIN_LED    = 26   # LED 별도 릴레이

PIN_STEP   = 24
PIN_DIR    = 25

STEP_DELAY       = 0.001
WINDOW_MAX_STEPS = 1000

_window_current_steps = 0

# ─────────────────────────────────────────────────────────────────
# 데이터 컨테이너
# ─────────────────────────────────────────────────────────────────

@dataclass
class SensorData:
    temp_in:   Optional[float] = None
    hum_in:    Optional[float] = None
    co2_in:    Optional[int]   = None
    soil_hum:  Optional[int]   = None
    lux_in:    Optional[float] = None
    temp_out:  Optional[float] = None
    rain_out:  Optional[int]   = None
    wind_out:  Optional[float] = None
    solar_out: Optional[float] = None


@dataclass
class ControlResult:
    fan_on_sec:    int  = 0    # 0~600초
    window_on_sec: int  = 0    # 0~600초
    heater_on_sec: int  = 0    # 0~600초
    pump_on_sec:   int  = 0    # 0~180초
    led_on:        bool = False
    notes:         list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# GPIO 초기화 (lgpio)
# ─────────────────────────────────────────────────────────────────

try:
    import lgpio
    _chip = lgpio.gpiochip_open(0)
    for pin in [PIN_FAN, PIN_WINDOW, PIN_HEATER, PIN_PUMP, PIN_LED, PIN_STEP, PIN_DIR]:
        lgpio.gpio_claim_output(_chip, pin, 1)
    GPIO_AVAILABLE = True
    log.info("lgpio 초기화 완료 (Pi5)")
except Exception as e:
    GPIO_AVAILABLE = False
    log.warning(f"lgpio 없음 — 시뮬레이션 모드: {e}")


def _relay_on(pin: int):
    if GPIO_AVAILABLE:
        lgpio.gpio_write(_chip, pin, 0)


def _relay_off(pin: int):
    if GPIO_AVAILABLE:
        lgpio.gpio_write(_chip, pin, 1)


def _step_motor(target_pct: int):
    global _window_current_steps
    if not GPIO_AVAILABLE:
        log.info(f"[SIM] 창문 스테퍼 -> {target_pct}%")
        return
    target_steps = int(WINDOW_MAX_STEPS * target_pct / 100)
    diff = target_steps - _window_current_steps
    if diff == 0:
        return
    direction = 1 if diff > 0 else 0
    lgpio.gpio_write(_chip, PIN_DIR, direction)
    time.sleep(0.001)
    steps = abs(diff)
    log.info(f"[WIN] 스테퍼 {'열기' if direction else '닫기'} {steps}스텝")
    for _ in range(steps):
        lgpio.gpio_write(_chip, PIN_STEP, 1)
        time.sleep(STEP_DELAY)
        lgpio.gpio_write(_chip, PIN_STEP, 0)
        time.sleep(STEP_DELAY)
    _window_current_steps = target_steps


# ─────────────────────────────────────────────────────────────────
# ① 환기팬 (CH1) — 주기 10분, ON 0~600초
# ─────────────────────────────────────────────────────────────────

def calc_fan(s: SensorData) -> tuple[int, list]:
    notes = []
    t = s.temp_in or 0.0

    # 2계층 Base
    if t > 32:   base = 510
    elif t > 28: base = 390
    elif t > 25: base = 270
    elif t > 22: base = 120
    else:        base = 0
    notes.append(f"[FAN] Base(temp_in={t}C) -> {base}s")

    # 3계층 Adjust
    adj = 0
    h = s.hum_in or 0.0
    if h > 85:   adj += 108; notes.append("  +108s (hum_in>85%)")
    elif h > 75: adj += 48;  notes.append("  +48s  (hum_in 75~85%)")

    c = s.co2_in or 0
    if c > 1500:   adj += 120; notes.append("  +120s (co2>1500ppm)")
    elif c > 1000: adj += 72;  notes.append("  +72s  (co2 1000~1500ppm)")
    elif c > 800:  adj += 30;  notes.append("  +30s  (co2 800~1000ppm)")

    to = s.temp_out
    if to is not None:
        if to < t - 5:  adj += 48; notes.append("  +48s (temp_out<temp_in-5)")
        elif to >= t:   adj -= 48; notes.append("  -48s (temp_out>=temp_in)")

    if (s.solar_out or 0) > 600: adj += 30; notes.append("  +30s (solar>600)")
    if (s.wind_out  or 0) > 5:   adj -= 30; notes.append("  -30s (wind>5m/s)")

    on_time = base + adj

    # 1계층 Safety: 강수 -> 최대 180초
    if s.rain_out == 1:
        on_time = min(on_time, 180)
        notes.append("  [SAFETY] rain -> cap 180s")

    on_time = max(0, min(CYCLE, on_time))
    notes.append(f"[FAN] result: {on_time}s (duty {on_time/CYCLE*100:.0f}%)")
    return on_time, notes


# ─────────────────────────────────────────────────────────────────
# ② 창문 (CH2) — 주기 10분, ON 0~600초
# ─────────────────────────────────────────────────────────────────

def calc_window(s: SensorData) -> tuple[int, list]:
    notes = []
    w    = s.wind_out or 0.0
    rain = s.rain_out or 0

    # 1계층 Safety
    if rain == 1:
        notes.append("[WIN] [SAFETY] rain -> 0s")
        return 0, notes
    if w > 8:
        notes.append("[WIN] [SAFETY] wind>8m/s -> 0s")
        return 0, notes

    wind_cap = 180 if 5 <= w <= 8 else CYCLE

    # 2계층 Base
    t = s.temp_in or 0.0
    if t > 32:   base = 510
    elif t > 28: base = 390
    elif t > 25: base = 270
    elif t > 22: base = 120
    else:        base = 0
    notes.append(f"[WIN] Base(temp_in={t}C) -> {base}s")

    # 3계층 Adjust
    adj = 0
    c = s.co2_in or 0
    if c > 1500:   adj += 120; notes.append("  +120s (co2>1500ppm)")
    elif c > 1000: adj += 72;  notes.append("  +72s  (co2 1000~1500ppm)")

    h = s.hum_in or 0.0
    if h > 85: adj += 48; notes.append("  +48s (hum_in>85%)")

    to = s.temp_out
    if to is not None:
        if to < t - 8:  adj += 48;  notes.append("  +48s  (temp_out<temp_in-8)")
        elif to >= t:   adj -= 72;  notes.append("  -72s  (temp_out>=temp_in)")

    if 2 <= w <= 5:               adj += 30; notes.append("  +30s (wind 2~5m/s)")
    if (s.solar_out or 0) > 800:  adj += 30; notes.append("  +30s (solar>800)")

    on_time = max(0, min(wind_cap, base + adj))
    notes.append(f"[WIN] result: {on_time}s (duty {on_time/CYCLE*100:.0f}%)")
    return on_time, notes


# ─────────────────────────────────────────────────────────────────
# ③ 히터 (CH3) — 주기 10분, ON 0~600초  ← 신규 구현
# ─────────────────────────────────────────────────────────────────

def calc_heater(s: SensorData) -> tuple[int, list]:
    notes = []
    t = s.temp_in or 20.0

    # 1계층 Safety: 18°C 초과 즉시 0초
    if t > 18:
        notes.append(f"[HTR] [SAFETY] temp_in={t}C > 18C -> 0s")
        return 0, notes

    # 2계층 Base
    if t >= 15:   base = 90
    elif t >= 12: base = 210
    elif t >= 8:  base = 360
    else:         base = 480
    notes.append(f"[HTR] Base(temp_in={t}C) -> {base}s")

    # 3계층 Adjust
    adj = 0
    to = s.temp_out or 10.0
    if to < -5:      adj += 180; notes.append("  +180s (temp_out<-5C)")
    elif to < 0:     adj += 120; notes.append("  +120s (temp_out -5~0C)")
    elif to < 5:     adj += 60;  notes.append("  +60s  (temp_out 0~5C)")

    w = s.wind_out or 0.0
    if w > 7:    adj += 120; notes.append("  +120s (wind>7m/s)")
    elif w >= 3: adj += 60;  notes.append("  +60s  (wind 3~7m/s)")

    if (s.rain_out  or 0) == 1:   adj -= 30; notes.append("  -30s  (비)")
    if (s.co2_in    or 0) > 1200: adj -= 30; notes.append("  -30s  (co2>1200ppm)")

    on_time = base + adj

    # 1계층 Safety 추가: 강한 일사 -120초
    if (s.solar_out or 0) > 400:
        on_time -= 120
        notes.append("  [SAFETY] solar>400 -> -120s")

    # 1계층 Safety 추가: 극건조 300초 상한
    h = s.hum_in or 60.0
    if h < 40:
        on_time = min(on_time, 300)
        notes.append("  [SAFETY] hum_in<40% -> cap 300s")

    on_time = max(0, min(CYCLE, on_time))
    notes.append(f"[HTR] result: {on_time}s (duty {on_time/CYCLE*100:.0f}%)")
    return on_time, notes


# ─────────────────────────────────────────────────────────────────
# ④ 워터펌프 (CH4) — 주기 30분, ON 0~180초
# ─────────────────────────────────────────────────────────────────

def calc_pump(s: SensorData) -> tuple[int, list]:
    notes = []
    soil = s.soil_hum if s.soil_hum is not None else 50

    # 1계층 Safety
    if s.rain_out == 1:
        notes.append("[PMP] [SAFETY] rain -> 0s")
        return 0, notes
    if soil > 70:
        notes.append(f"[PMP] [SAFETY] soil={soil}% > 70% -> 0s")
        return 0, notes

    # 2계층 Base
    if soil < 10:
        base = 120
    elif soil < 20:
        base = 60
    else:
        notes.append(f"[PMP] soil={soil}% -> 0s (적정)")
        return 0, notes
    notes.append(f"[PMP] Base(soil={soil}%) -> {base}s")

    # 3계층 Adjust
    adj = 0
    t = s.temp_in or 20.0
    if t > 28:   adj += 30; notes.append("  +30s (temp_in>28C)")
    elif t < 15: adj -= 15; notes.append("  -15s (temp_in<15C)")

    h = s.hum_in or 60.0
    if h < 50:   adj += 20; notes.append("  +20s (hum_in<50%)")
    elif h > 80: adj -= 15; notes.append("  -15s (hum_in>80%)")

    sol = s.solar_out or 0.0
    if sol > 600:  adj += 20; notes.append("  +20s (solar>600)")
    elif sol < 50: adj -= 15; notes.append("  -15s (solar<50 야간)")

    if (s.wind_out  or 0) > 4:  adj += 15; notes.append("  +15s (wind>4m/s)")
    if (s.temp_out  or 0) > 30: adj += 15; notes.append("  +15s (temp_out>30C)")

    on_time = max(0, min(180, base + adj))
    notes.append(f"[PMP] result: {on_time}s")
    return on_time, notes


# ─────────────────────────────────────────────────────────────────
# ⑤ 식물생장 LED — 단순 ON/OFF
# ─────────────────────────────────────────────────────────────────

def calc_led(s: SensorData) -> tuple[bool, list]:
    notes = []
    hour = datetime.now().hour
    t    = s.temp_in or 20.0
    sol  = s.solar_out or 0.0

    # 1계층 Safety
    if hour < 6 or hour >= 20:
        notes.append(f"[LED] [SAFETY] 심야({hour}h) -> OFF")
        return False, notes
    if t > 35:
        notes.append(f"[LED] [SAFETY] temp_in={t}C > 35C -> OFF")
        return False, notes

    # 1계층 특수: 강수+저조도 강제 ON
    if s.rain_out == 1 and sol < 100:
        notes.append("[LED] rain+solar<100 -> ON")
        return True, notes

    # 2계층 Base: solar<=400 -> ON
    led_on = sol <= 400
    notes.append(f"[LED] solar={sol} -> {'ON' if led_on else 'OFF'}")

    # 3계층 Adjust
    if led_on:
        if t > 30:
            notes.append(f"  [ADJ] temp_in={t}C > 30C -> OFF")
            return False, notes
        h = s.hum_in or 60.0
        if h > 90:
            notes.append(f"  [ADJ] hum_in={h}% > 90% -> OFF")
            return False, notes

    notes.append(f"[LED] result: {'ON' if led_on else 'OFF'}")
    return led_on, notes


# ─────────────────────────────────────────────────────────────────
# 통합 제어 계산
# ─────────────────────────────────────────────────────────────────

def compute_control(s: SensorData) -> ControlResult:
    result = ControlResult()
    all_notes = []

    result.fan_on_sec,    n = calc_fan(s);     all_notes.extend(n)
    result.window_on_sec, n = calc_window(s);  all_notes.extend(n)
    result.heater_on_sec, n = calc_heater(s);  all_notes.extend(n)
    result.pump_on_sec,   n = calc_pump(s);    all_notes.extend(n)
    result.led_on,        n = calc_led(s);     all_notes.extend(n)

    result.notes = all_notes
    return result


# ─────────────────────────────────────────────────────────────────
# 하드웨어 적용
# ─────────────────────────────────────────────────────────────────

def apply_control(ctrl: ControlResult):
    # CH1 환기팬
    if ctrl.fan_on_sec > 0:
        _relay_on(PIN_FAN)
        log.info(f"[HW] 환기팬 ON -> {ctrl.fan_on_sec}s")
        time.sleep(ctrl.fan_on_sec)
        _relay_off(PIN_FAN)
        log.info("[HW] 환기팬 OFF")
    else:
        _relay_off(PIN_FAN)
        log.info("[HW] 환기팬 OFF (0s)")

    # CH2 창문 스테퍼
    window_pct = int(ctrl.window_on_sec / CYCLE * 100)
    log.info(f"[HW] 창문 -> {window_pct}% ({ctrl.window_on_sec}s)")
    _step_motor(window_pct)

    # CH3 히터
    if ctrl.heater_on_sec > 0:
        _relay_on(PIN_HEATER)
        log.info(f"[HW] 히터 ON -> {ctrl.heater_on_sec}s")
        time.sleep(ctrl.heater_on_sec)
        _relay_off(PIN_HEATER)
        log.info("[HW] 히터 OFF")
    else:
        _relay_off(PIN_HEATER)
        log.info("[HW] 히터 OFF (0s)")

    # CH4 워터펌프
    if ctrl.pump_on_sec > 0:
        _relay_on(PIN_PUMP)
        log.info(f"[HW] 워터펌프 ON -> {ctrl.pump_on_sec}s 후 OFF")
        time.sleep(ctrl.pump_on_sec)
        _relay_off(PIN_PUMP)
        log.info("[HW] 워터펌프 OFF")
    else:
        _relay_off(PIN_PUMP)
        log.info("[HW] 워터펌프 OFF (0s)")

    # LED
    if ctrl.led_on:
        _relay_on(PIN_LED)
        log.info("[HW] LED ON")
    else:
        _relay_off(PIN_LED)
        log.info("[HW] LED OFF")


# ─────────────────────────────────────────────────────────────────
# 센서 읽기
# ─────────────────────────────────────────────────────────────────

i2c  = busio.I2C(board.SCL, board.SDA)
sht  = adafruit_sht31d.SHT31D(i2c)
bh   = adafruit_bh1750.BH1750(i2c, address=0x23)
ads  = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
ser  = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)

supabase  = create_client(SUPABASE_URL,  SUPABASE_KEY)
supabase2 = create_client(SUPABASE_URL2, SUPABASE_KEY2)
supabase3 = create_client(SUPABASE_URL3, SUPABASE_KEY3)


def read_co2() -> Optional[int]:
    try:
        ser.reset_input_buffer()
        ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
        time.sleep(0.5)
        resp = ser.read(9)
        if len(resp) < 9:
            return None
        return (resp[2] << 8) | resp[3]
    except Exception as e:
        log.error(f"CO2: {e}")
        return None


def read_soil() -> Optional[int]:
    try:
        v = sum(chan.voltage for _ in range(5)) / 5
        time.sleep(0.1)
        pct = (v - 0.190) / (0.572 - 0.190) * 100
        return round(max(0, min(100, pct)))
    except Exception as e:
        log.error(f"토양수분: {e}")
        return None


def get_base_time():
    now = datetime.now()
    if now.minute < 40:
        now -= timedelta(hours=1)
    return now.strftime("%Y%m%d"), now.strftime("%H00")


def fetch_weather() -> dict:
    base_date, base_time = get_base_time()
    url = ("http://apis.data.go.kr/1360000/"
           "VilageFcstInfoService_2.0/getUltraSrtNcst")
    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": 1, "numOfRows": 100, "dataType": "JSON",
        "base_date": base_date, "base_time": base_time,
        "nx": NX, "ny": NY,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        items = r.json()["response"]["body"]["items"]["item"]
        out = {"temp_out": None, "rain_out": 0, "wind_out": None, "solar_out": None}
        for item in items:
            cat, val = item["category"], item["obsrValue"]
            if   cat == "T1H": out["temp_out"]  = float(val)
            elif cat == "RN1": out["rain_out"]  = 0 if val == "강수없음" else 1
            elif cat == "WSD": out["wind_out"]  = float(val)
            elif cat == "SI":  out["solar_out"] = float(val)
        return out
    except Exception as e:
        log.error(f"기상청 API: {e}")
        return {}


def read_all_sensors() -> SensorData:
    s = SensorData()
    try:
        s.co2_in   = read_co2()
        s.hum_in   = round(float(sht.relative_humidity), 1)
        s.temp_in  = round(float(sht.temperature), 1)
        s.soil_hum = read_soil()
        s.lux_in   = round(float(bh.lux), 1)
    except Exception as e:
        log.error(f"실내 센서: {e}")
    wx = fetch_weather()
    s.temp_out  = wx.get("temp_out")
    s.rain_out  = wx.get("rain_out", 0)
    s.wind_out  = wx.get("wind_out")
    s.solar_out = wx.get("solar_out")
    return s


# ─────────────────────────────────────────────────────────────────
# DB 저장
# ─────────────────────────────────────────────────────────────────

def save_to_db(s: SensorData):
    dt = datetime.now().isoformat()

    base_row = {
        "datetime":  dt,
        "hum_in":    s.hum_in,
        "temp_in":   s.temp_in,
        "soil_hum":  s.soil_hum,
        "lux_in":    s.lux_in,
        "temp_out":  s.temp_out,
        "rain_out":  s.rain_out,
        "wind_out":  s.wind_out,
    }

    row1 = {**base_row, "co2_in": s.co2_in}
    row2 = {**base_row, "co2_in": s.co2_in if datetime.now().hour % 2 == 0 else None,
            "co2_predicted": None}
    row3 = {"datetime": dt, "co2_in": s.co2_in}

    for db, table, row in [
        (supabase,  "sensor_data",   row1),
        (supabase2, "sensor_data_2", row2),
        (supabase3, "sensor_data_3", row3),
    ]:
        try:
            db.table(table).insert(row).execute()
            log.info(f"[DB] {table} 저장 완료")
        except Exception as e:
            log.error(f"[DB] {table} 저장 실패: {e}")


# ─────────────────────────────────────────────────────────────────
# 메인 루프
# ─────────────────────────────────────────────────────────────────

def job():
    log.info("=" * 60)
    log.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 사이클 시작")
    log.info("=" * 60)

    sensor = read_all_sensors()
    log.info(f"[센서] {sensor}")

    ctrl = compute_control(sensor)
    for note in ctrl.notes:    # ← 버그 수정: '음표' -> note
        log.info(note)

    apply_control(ctrl)
    save_to_db(sensor)

    window_pct = int(ctrl.window_on_sec / CYCLE * 100)
    log.info(
        f"[결과] 팬={ctrl.fan_on_sec}s  "
        f"창문={ctrl.window_on_sec}s({window_pct}%)  "
        f"히터={ctrl.heater_on_sec}s  "
        f"펌프={ctrl.pump_on_sec}s  "
        f"LED={'ON' if ctrl.led_on else 'OFF'}"
    )


# ─────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("스마트팜 통합 제어 시스템 시작 (1시간 주기)")
    log.info("=" * 60)

    job()

    schedule.every(1).hours.do(job)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("종료 요청")
    finally:
        if GPIO_AVAILABLE:
            for pin in [PIN_FAN, PIN_WINDOW, PIN_HEATER, PIN_PUMP, PIN_LED]:
                lgpio.gpio_write(_chip, pin, 1)
            lgpio.gpiochip_close(_chip)
        log.info("종료 완료")