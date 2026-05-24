#!/usr/bin/env python3
"""
스마트팜 시스템 통합 테스트 코드
연결 부품:
  - HTU21D (온습도, I2C)
  - SEN0193 (토양수분, ADC via MCP3008 or ADS1115)
  - MH-Z19 (CO2, UART /dev/ttyAMA0)
  - 전력측정 모듈 (I2C, INA219 가정)
  - 스텝퍼 모터 (GPIO, A4988/DRV8825)
  - SSR x4: PTC히터, 냉각팬, 펠티에소자, (LED 제외)
"""

import time
import sys
import logging
import RPi.GPIO as GPIO

# ── 로깅 설정 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SmartFarm")

# ══════════════════════════════════════════════════════════════════════════════
# GPIO 핀 번호 (BCM 기준) — 회로도 보고 실제 배선에 맞게 수정하세요
# ══════════════════════════════════════════════════════════════════════════════
PIN_SSR_PTC    = 17   # SSR1: PTC 히터
PIN_SSR_FAN    = 27   # SSR2: 냉각팬  (SSR(냉각팬))
PIN_SSR_PELTIER= 22   # SSR3: 펠티에 소자  (SSR(펠티에소자))
# PIN_SSR_LED  = 23   # SSR4: LED — 미연결, 주석 처리

# 스텝퍼 모터 (A4988 / DRV8825)
PIN_STEP  = 24
PIN_DIR   = 25
PIN_ENABLE= 8   # LOW=활성, HIGH=비활성

# ══════════════════════════════════════════════════════════════════════════════
# GPIO 초기화
# ══════════════════════════════════════════════════════════════════════════════
def gpio_setup():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in [PIN_SSR_PTC, PIN_SSR_FAN, PIN_SSR_PELTIER]:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    for pin in [PIN_STEP, PIN_DIR, PIN_ENABLE]:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
    log.info("GPIO 초기화 완료")

def gpio_cleanup():
    GPIO.cleanup()
    log.info("GPIO 정리 완료")

# ══════════════════════════════════════════════════════════════════════════════
# HTU21D — 온습도 센서 (I2C)
# pip install adafruit-circuitpython-htu21d
# ══════════════════════════════════════════════════════════════════════════════
def read_htu21d():
    try:
        import board
        import busio
        import adafruit_htu21d
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_htu21d.HTU21D(i2c)
        temp = sensor.temperature
        hum  = sensor.relative_humidity
        log.info(f"[HTU21D] 온도: {temp:.1f}°C  습도: {hum:.1f}%")
        return temp, hum
    except Exception as e:
        log.error(f"[HTU21D] 읽기 실패: {e}")
        return None, None

# ══════════════════════════════════════════════════════════════════════════════
# SEN0193 — 토양수분 센서 (아날로그 → ADS1115 I2C ADC)
# pip install adafruit-circuitpython-ads1x15
# ══════════════════════════════════════════════════════════════════════════════
def read_soil_moisture():
    try:
        import board
        import busio
        import adafruit_ads1x15.ads1115 as ADS
        from adafruit_ads1x15.analog_in import AnalogIn

        i2c  = busio.I2C(board.SCL, board.SDA)
        ads  = ADS.ADS1115(i2c)
        chan = AnalogIn(ads, ADS.P0)   # A0 채널

        raw     = chan.value           # 0 ~ 32767
        voltage = chan.voltage
        # SEN0193: 건조(공기)≈2.8V, 포화≈1.2V → 퍼센트 변환
        pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
        log.info(f"[SEN0193] 토양수분: {pct:.1f}%  전압: {voltage:.3f}V  raw: {raw}")
        return pct
    except Exception as e:
        log.error(f"[SEN0193] 읽기 실패: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# MH-Z19 — CO2 센서 (UART)
# pip install mh-z19   또는   pip install pyserial
# ══════════════════════════════════════════════════════════════════════════════
def read_co2():
    try:
        import mh_z19
        result = mh_z19.read_all()
        co2 = result.get("CO2", None)
        log.info(f"[MH-Z19] CO2: {co2} ppm  전체: {result}")
        return co2
    except ImportError:
        # mh_z19 미설치 시 pyserial 직접 통신
        try:
            import serial
            ser = serial.Serial("/dev/ttyAMA0", 9600, timeout=1)
            ser.write(b"\xff\x01\x86\x00\x00\x00\x00\x00\x79")
            resp = ser.read(9)
            ser.close()
            if len(resp) == 9 and resp[0] == 0xFF and resp[1] == 0x86:
                co2 = resp[2] * 256 + resp[3]
                log.info(f"[MH-Z19] CO2: {co2} ppm")
                return co2
            else:
                log.warning("[MH-Z19] 응답 이상")
                return None
        except Exception as e:
            log.error(f"[MH-Z19] 읽기 실패: {e}")
            return None
    except Exception as e:
        log.error(f"[MH-Z19] 읽기 실패: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# 전력측정 — INA219 (I2C)
# pip install adafruit-circuitpython-ina219
# ══════════════════════════════════════════════════════════════════════════════
def read_power():
    try:
        import board
        import busio
        from adafruit_ina219 import INA219
        i2c    = busio.I2C(board.SCL, board.SDA)
        ina    = INA219(i2c)
        bus_v  = ina.bus_voltage      # V
        current= ina.current          # mA
        power  = ina.power            # mW
        shunt_v= ina.shunt_voltage    # mV
        log.info(f"[INA219] 전압: {bus_v:.2f}V  전류: {current:.1f}mA  전력: {power:.1f}mW")
        return bus_v, current, power
    except Exception as e:
        log.error(f"[INA219] 읽기 실패: {e}")
        return None, None, None

# ══════════════════════════════════════════════════════════════════════════════
# SSR 제어 헬퍼
# ══════════════════════════════════════════════════════════════════════════════
def ssr_on(pin, name):
    GPIO.output(pin, GPIO.HIGH)
    log.info(f"[SSR] {name} ON")

def ssr_off(pin, name):
    GPIO.output(pin, GPIO.LOW)
    log.info(f"[SSR] {name} OFF")

# ══════════════════════════════════════════════════════════════════════════════
# 스텝퍼 모터 테스트
# ══════════════════════════════════════════════════════════════════════════════
def stepper_test(steps=200, delay=0.005, direction=GPIO.HIGH):
    """
    steps : 펄스 수 (기본 200 = 1회전, 1/1 마이크로스텝 기준)
    delay : 스텝 간격(초) — 작을수록 빠름
    direction: GPIO.HIGH(CW) / GPIO.LOW(CCW)
    """
    GPIO.output(PIN_ENABLE, GPIO.LOW)   # 모터 활성
    GPIO.output(PIN_DIR, direction)
    log.info(f"[스텝퍼] {'CW' if direction == GPIO.HIGH else 'CCW'} {steps}스텝 시작")
    for _ in range(steps):
        GPIO.output(PIN_STEP, GPIO.HIGH)
        time.sleep(delay)
        GPIO.output(PIN_STEP, GPIO.LOW)
        time.sleep(delay)
    GPIO.output(PIN_ENABLE, GPIO.HIGH)  # 모터 비활성 (열 절약)
    log.info("[스텝퍼] 완료")

# ══════════════════════════════════════════════════════════════════════════════
# 통합 테스트
# ══════════════════════════════════════════════════════════════════════════════
def run_sensor_test():
    log.info("=" * 50)
    log.info("  센서 읽기 테스트")
    log.info("=" * 50)
    read_htu21d()
    read_soil_moisture()
    read_co2()
    read_power()

def run_actuator_test():
    log.info("=" * 50)
    log.info("  액추에이터 테스트 (각 2초)")
    log.info("=" * 50)

    # PTC 히터
    ssr_on(PIN_SSR_PTC, "PTC 히터")
    time.sleep(2)
    ssr_off(PIN_SSR_PTC, "PTC 히터")
    time.sleep(1)

    # 냉각팬
    ssr_on(PIN_SSR_FAN, "냉각팬")
    time.sleep(2)
    ssr_off(PIN_SSR_FAN, "냉각팬")
    time.sleep(1)

    # 펠티에 소자
    ssr_on(PIN_SSR_PELTIER, "펠티에 소자")
    time.sleep(2)
    ssr_off(PIN_SSR_PELTIER, "펠티에 소자")
    time.sleep(1)

    # 스텝퍼 모터 — CW 1회전 후 CCW 복귀
    stepper_test(steps=200, delay=0.005, direction=GPIO.HIGH)
    time.sleep(0.5)
    stepper_test(steps=200, delay=0.005, direction=GPIO.LOW)

def run_continuous_monitor(interval=5, cycles=6):
    """interval초마다 센서값 출력, cycles번 반복"""
    log.info("=" * 50)
    log.info(f"  연속 모니터링 ({interval}초 간격 × {cycles}회)")
    log.info("=" * 50)
    for i in range(1, cycles + 1):
        log.info(f"--- [{i}/{cycles}] ---")
        temp, hum = read_htu21d()
        moisture  = read_soil_moisture()
        co2       = read_co2()
        bus_v, current, power = read_power()

        # 간단 자동 제어 예시
        if temp is not None:
            if temp > 28:
                ssr_on(PIN_SSR_FAN, "냉각팬(자동)")
            else:
                ssr_off(PIN_SSR_FAN, "냉각팬(자동)")

            if temp < 18:
                ssr_on(PIN_SSR_PTC, "PTC히터(자동)")
            else:
                ssr_off(PIN_SSR_PTC, "PTC히터(자동)")

        time.sleep(interval)

    # 모니터링 종료 시 전부 OFF
    for pin, name in [(PIN_SSR_PTC,"PTC히터"), (PIN_SSR_FAN,"냉각팬"), (PIN_SSR_PELTIER,"펠티에")]:
        ssr_off(pin, name)

# ══════════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════════
def main():
    gpio_setup()
    try:
        print("\n선택하세요:")
        print("  1) 센서 읽기 테스트")
        print("  2) 액추에이터 테스트")
        print("  3) 연속 모니터링 (5초 간격 × 6회)")
        print("  4) 전체 테스트 (1+2+3)")
        choice = input("번호 입력 [1-4]: ").strip()

        if choice == "1":
            run_sensor_test()
        elif choice == "2":
            run_actuator_test()
        elif choice == "3":
            run_continuous_monitor()
        elif choice == "4":
            run_sensor_test()
            run_actuator_test()
            run_continuous_monitor()
        else:
            log.warning("잘못된 입력. 전체 테스트를 실행합니다.")
            run_sensor_test()
            run_actuator_test()
            run_continuous_monitor()

    except KeyboardInterrupt:
        log.info("사용자 중단 (Ctrl+C)")
    finally:
        # 안전: 모든 SSR OFF
        for pin, name in [(PIN_SSR_PTC,"PTC히터"), (PIN_SSR_FAN,"냉각팬"), (PIN_SSR_PELTIER,"펠티에")]:
            ssr_off(pin, name)
        gpio_cleanup()
        log.info("종료")

if __name__ == "__main__":
    main()
