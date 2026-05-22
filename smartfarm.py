#!/usr/bin/env python3
"""
스마트팜 통합 데이터 수집기

- 실내 센서
  - SHT31 : 온습도
  - BH1750 : 조도
  - ADS1115 + SEN0193 : 토양수분
  - MH-Z19 : CO2

- 실외 기상
  - 기상청 초단기실황 API

- 저장
  - Supabase sensor_data 테이블

- 주기
  - 10분
"""

import time
import board
import busio
import serial
import requests
import schedule

from datetime import datetime, timedelta
from supabase import create_client

import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS

from adafruit_ads1x15.analog_in import AnalogIn

# ─────────────────────────────────────
# 설정
# ─────────────────────────────────────

KMA_API_KEY  = "91d35aba86632a9a336b4166ddb9507a9d1a94cc8ab97e88bf34c57fc3d465ac"
SUPABASE_URL = "https://hheeyhsiaqhxgufrvkui.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWV5aHNpYXFoeGd1ZnJ2a3VpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkxMzc0MjIsImV4cCI6MjA5NDcxMzQyMn0.ri_w6QVJwgTIf2U2hp1L30uasJVO4Igqo08Wg8hF0xk"
 
# 서산 좌표
NX = 63
NY = 89

# ─────────────────────────────────────
# Supabase 초기화
# ─────────────────────────────────────

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─────────────────────────────────────
# I2C 초기화
# ─────────────────────────────────────

i2c = busio.I2C(board.SCL, board.SDA)

# SHT31
sht = adafruit_sht31d.SHT31D(i2c)

# BH1750
bh = adafruit_bh1750.BH1750(i2c, address=0x23)

# ADS1115
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)

# MH-Z19
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)

# ─────────────────────────────────────
# CO2 읽기
# ─────────────────────────────────────

def read_co2():

    try:
        ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')

        time.sleep(0.1)

        resp = ser.read(9)

        if len(resp) < 9:
            return None

        co2 = resp[2] * 256 + resp[3]

        return int(co2)

    except Exception as e:
        print(f"[ERROR] CO2: {e}")
        return None

# ─────────────────────────────────────
# 토양수분 읽기
# ─────────────────────────────────────

def read_soil():

    try:
        voltages = []

        for _ in range(5):
            voltages.append(chan.voltage)
            time.sleep(0.1)

        voltage = sum(voltages) / len(voltages)

        # 보정값
        dry = 0.572
        wet = 0.190

        pct = (voltage - wet) / (dry - wet) * 100

        pct = max(0, min(100, pct))

        return round(pct)

    except Exception as e:
        print(f"[ERROR] 토양수분: {e}")
        return None

# ─────────────────────────────────────
# 실내 센서 읽기
# ─────────────────────────────────────

def read_indoor():

    try:

        data = {
            "co2_in": read_co2(),
            "hum_in": round(float(sht.relative_humidity), 1),
            "temp_in": round(float(sht.temperature), 1),
            "soil_hum": read_soil(),
            "lux_in": round(float(bh.lux), 1)
        }

        return data

    except Exception as e:
        print(f"[ERROR] 실내센서: {e}")
        return None

# ─────────────────────────────────────
# 기상청 기준 시간 계산
# ─────────────────────────────────────

def get_base_time():

    now = datetime.now()

    # 기상청은 매 시각 40분 이후 데이터 제공
    if now.minute < 40:
        now -= timedelta(hours=1)

    return now.strftime("%Y%m%d"), now.strftime("%H00")

# ─────────────────────────────────────
# 기상청 API
# ─────────────────────────────────────

def fetch_weather():

    base_date, base_time = get_base_time()

    url = (
        "http://apis.data.go.kr/"
        "1360000/VilageFcstInfoService_2.0/"
        "getUltraSrtNcst"
    )

    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": 1,
        "numOfRows": 100,
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": NX,
        "ny": NY
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        print("[DEBUG WEATHER]")
        print(data)

        # body 없는 경우 처리
        if "body" not in data["response"]:
            print("[ERROR] 기상청 데이터 없음")
            return None

        items = data["response"]["body"]["items"]["item"]

        weather = {
            "temp_out": None,
            "rain_out": None,
            "wind_out": None
        }

        for item in items:

            category = item["category"]
            value = item["obsrValue"]

            # 기온
            if category == "T1H":
                weather["temp_out"] = float(value)

            # 강수량
            elif category == "RN1":

                if value == "강수없음":
                    weather["rain_out"] = 0
                else:
                    weather["rain_out"] = float(value)

            # 풍속
            elif category == "WSD":
                weather["wind_out"] = float(value)

        return weather

    except Exception as e:
        print(f"[ERROR] 기상청 API: {e}")
        return None

# ─────────────────────────────────────
# 데이터 저장
# ─────────────────────────────────────

def save_data(row):

    try:

        supabase.table("sensor_data") \
            .insert(row) \
            .execute()

        print("[SUCCESS] 저장 완료")
        print(row)

    except Exception as e:
        print(f"[ERROR] Supabase 저장 실패: {e}")

# ─────────────────────────────────────
# 메인 작업
# ─────────────────────────────────────

def job():

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    print("\n" + "=" * 50)
    print(f"[{now}] 데이터 수집 시작")
    print("=" * 50)

    indoor = read_indoor()
    weather = fetch_weather()

    if indoor is None and weather is None:
        print("[SKIP] 데이터 없음")
        return

    row = {

        "datetime": datetime.now().isoformat(),

        # 실내
        "co2_in": indoor.get("co2_in") if indoor else None,
        "hum_in": indoor.get("hum_in") if indoor else None,
        "temp_in": indoor.get("temp_in") if indoor else None,
        "soil_hum": indoor.get("soil_hum") if indoor else None,
        "lux_in": indoor.get("lux_in") if indoor else None,

        # 실외
        "temp_out": weather.get("temp_out") if weather else None,
        "rain_out": weather.get("rain_out") if weather else None,
        "wind_out": weather.get("wind_out") if weather else None,

        # 사용 안 함
        "solar_out": None
    }

    save_data(row)

# ─────────────────────────────────────
# 실행
# ─────────────────────────────────────

if __name__ == "__main__":

    print("=" * 60)
    print("스마트팜 데이터 수집기 시작")
    print("=" * 60)

    # 시작 즉시 실행
    job()

    # 40분마다 실행
    schedule.every(40).minutes.do(job)

    while True:

        schedule.run_pending()

        time.sleep(30)