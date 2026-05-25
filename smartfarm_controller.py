#!/usr/bin/env python3
"""
스마트팜 제어기 (Raspberry Pi 5 전용)
─────────────────────────────────────────────────────────────────
[역할]
  1. DB2에서 최신 센서값 읽기
  2. CO2 Fallback: 센서 고장 시 DB2 예측값 → Render API → 기본값 순서로 대체
  3. 제어 계산  : 릴레이 ON 시간 계산 (Safety→Base→Adjust→Clip)
  4. 하드웨어   : 환기팬·창문·워터펌프·LED 실제 제어 (히터 제외)

[확인된 배선]
  릴레이 4채널 (NC 연결 — 신호 1=ON, 0=OFF)
    GPIO13 → IN3 → 환기팬
    GPIO6  → IN2 → 워터펌프
    GPIO5  → IN1 → LED

  DRV8825 스테퍼모터 (창문)
    GPIO24 → STEP
    GPIO25 → DIR

[주기]
  CYCLE      = 600초  (10분) — 환기팬·창문
  PUMP_CYCLE = 1800초 (30분) — 워터펌프

[주의] Pi5는 RPi.GPIO 미지원 → lgpio 사용
─────────────────────────────────────────────────────────────────
"""

import time
import requests
import schedule
import logging

from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from supabase import create_client

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

SUPABASE_URL  = "https://hheeyhsiaqhxgufrvkui.supabase.co"
SUPABASE_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWV5aHNpYXFoeGd1ZnJ2a3VpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkxMzc0MjIsImV4cCI6MjA5NDcxMzQyMn0.ri_w6QVJwgTIf2U2hp1L30uasJVO4Igqo08Wg8hF0xk"

SUPABASE_URL2 = "https://yubqlportprwjyadsbie.supabase.co"
SUPABASE_KEY2 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1YnFscG9ydHByd2p5YWRzYmllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkzNDkwOTIsImV4cCI6MjA5NDkyNTA5Mn0.cuHidkUzm6Af5ThEL5f6EdA_MOGevsOXcuPK2wh-evk"

RENDER_API_URL = "https://smartfarm-co2-api-latest.onrender.com"

# ─────────────────────────────────────────────────────────────────
# 주기 상수
# ─────────────────────────────────────────────────────────────────

CYCLE      = 600   # 환기팬·창문 주기(초) = 10분
PUMP_CYCLE = 1800  # 워터펌프 주기(초) = 30분

# ─────────────────────────────────────────────────────────────────
# GPIO 핀 번호 (BCM)
# ─────────────────────────────────────────────────────────────────

PIN_FAN    = 13   # IN3 → 환기팬
PIN_PUMP   = 6    # IN2 → 워터펌프
PIN_LED    = 5    # IN1 → LED
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
    pump_on_sec:   int  = 0
    led_on:        bool = False
    notes:         list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# GPIO 초기화 (lgpio)
# ─────────────────────────────────────────────────────────────────

try:
    import lgpio
    _chip = lgpio.gpiochip_open(0)
    for pin in [PIN_FAN, PIN_PUMP, PIN_LED, PIN_STEP, PIN_DIR]:
        lgpio.gpio_claim_output(_chip, pin, 0)  # 0 = OFF (NC 반전)
    GPIO_AVAILABLE = True
    log.info("lgpio 초기화 완료 (Pi5) — 모든 릴레이 OFF")
except Exception as e:
    GPIO_AVAILABLE = False
    log.warning(f"lgpio 없음 — 시뮬레이션 모드: {e}")


def _relay_on(pin: int):
    if GPIO_AVAILABLE:
        lgpio.gpio_write(_chip, pin, 1)  # NC 반전: 1 = ON


def _relay_off(pin: int):
    if GPIO_AVAILABLE:
        lgpio.gpio_write(_chip, pin, 0)  # NC 반전: 0 = OFF


def _all_relay_off():
    for pin in [PIN_FAN, PIN_PUMP, PIN_LED]:
        _relay_off(pin)
    log.info("[GPIO] 모든 릴레이 OFF")


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
    log.info(f"[WIN] 스테퍼 {'열기' if direction else '닫기'} {abs(diff)}스텝")
    for _ in range(abs(diff)):
        lgpio.gpio_write(_chip, PIN_STEP, 1)
        time.sleep(STEP_DELAY)
        lgpio.gpio_write(_chip, PIN_STEP, 0)
        time.sleep(STEP_DELAY)
    _window_current_steps = target_steps


# ─────────────────────────────────────────────────────────────────
# 제어 계산 함수
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
        if to < t - 8:  adj += 48; notes.append("  +48s  (temp_out<temp_in-8)")
        elif to >= t:   adj -= 72; notes.append("  -72s  (temp_out>=temp_in)")
    if 2 <= w <= 5:              adj += 30; notes.append("  +30s (wind 2~5m/s)")
    if (s.solar_out or 0) > 800: adj += 30; notes.append("  +30s (solar>800)")
    on_time = max(0, min(wind_cap, base + adj))
    notes.append(f"[WIN] result: {on_time}s (duty {on_time/CYCLE*100:.0f}%)")
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
    result.fan_on_sec,    n = calc_fan(s);    all_notes.extend(n)
    result.window_on_sec, n = calc_window(s); all_notes.extend(n)
    result.pump_on_sec,   n = calc_pump(s);   all_notes.extend(n)
    result.led_on,        n = calc_led(s);    all_notes.extend(n)
    result.notes = all_notes
    return result


# ─────────────────────────────────────────────────────────────────
# 하드웨어 적용
# ─────────────────────────────────────────────────────────────────

def apply_control(ctrl: ControlResult):
    if ctrl.fan_on_sec > 0:
        _relay_on(PIN_FAN)
        log.info(f"[HW] 환기팬 ON -> {ctrl.fan_on_sec}s 후 OFF")
        time.sleep(ctrl.fan_on_sec)
        _relay_off(PIN_FAN)
        log.info("[HW] 환기팬 OFF ✓")
    else:
        _relay_off(PIN_FAN)
        log.info("[HW] 환기팬 OFF (0s)")

    window_pct = int(ctrl.window_on_sec / CYCLE * 100)
    log.info(f"[HW] 창문 -> {window_pct}% ({ctrl.window_on_sec}s)")
    _step_motor(window_pct)

    if ctrl.pump_on_sec > 0:
        _relay_on(PIN_PUMP)
        log.info(f"[HW] 워터펌프 ON -> {ctrl.pump_on_sec}s 후 OFF")
        time.sleep(ctrl.pump_on_sec)
        _relay_off(PIN_PUMP)
        log.info("[HW] 워터펌프 OFF ✓")
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
# Supabase 클라이언트
# ─────────────────────────────────────────────────────────────────

supabase  = create_client(SUPABASE_URL,  SUPABASE_KEY)
supabase2 = create_client(SUPABASE_URL2, SUPABASE_KEY2)


# ─────────────────────────────────────────────────────────────────
# CO2 Fallback
# ─────────────────────────────────────────────────────────────────

def get_co2_with_fallback(s: SensorData) -> int:
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
# 메인 루프
# ─────────────────────────────────────────────────────────────────

def job():
    log.info("=" * 60)
    log.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 사이클 시작")
    log.info("=" * 60)

    # 1. DB2에서 최신 센서값 읽기
    try:
        res = (
            supabase2.table("sensor_data_2")
            .select("*")
            .order("datetime", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            log.warning("[DB2] 데이터 없음 — 스킵")
            return
        row = res.data[0]
        sensor = SensorData(
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
        log.info(f"[센서] {sensor}")
    except Exception as e:
        log.error(f"[DB2] 읽기 실패: {e}")
        return

    # 2. CO2 fallback 처리
    sensor.co2_in = get_co2_with_fallback(sensor)
    log.info(f"[CO2] 제어용: {sensor.co2_in} ppm")

    # 3. 제어 계산
    ctrl = compute_control(sensor)
    for note in ctrl.notes:
        log.info(note)

    # 4. 하드웨어 제어
    apply_control(ctrl)

    window_pct = int(ctrl.window_on_sec / CYCLE * 100)
    log.info(
        f"[결과] 팬={ctrl.fan_on_sec}s  "
        f"창문={ctrl.window_on_sec}s({window_pct}%)  "
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

    _all_relay_off()
    job()

    schedule.every(1).hours.do(job)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("종료 요청")
    finally:
        _all_relay_off()
        if GPIO_AVAILABLE:
            lgpio.gpiochip_close(_chip)
        log.info("종료 완료")