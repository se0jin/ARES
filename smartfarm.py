#!/usr/bin/env python3
"""
스마트팜 데이터 수집기 (센서 수집 + DB 저장 전용)

- 실내 센서: SHT31(온습도) / BH1750(조도) / ADS1115+SEN0193(토양수분) / MH-Z19(CO2)
- 실외 기상: 기상청 초단기실황 API (천안 nx=63, ny=89)
- 저장: DB1(학습용) / DB2(예측 입출력) / DB3(모니터링용)
- 주기: 1시간
"""

import time
import board
import busio
import serial
import requests
import schedule
import json

from datetime import datetime, timedelta
from supabase import create_client

import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ─────────────────────────────────────
# 설정
# ─────────────────────────────────────

KMA_API_KEY = "91d35aba86632a9a336b4166ddb9507a9d1a94cc8ab97e88bf34c57fc3d465ac"

SUPABASE_URL  = "https://hheeyhsiaqhxgufrvkui.supabase.co"
SUPABASE_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWV5aHNpYXFoeGd1ZnJ2a3VpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkxMzc0MjIsImV4cCI6MjA5NDcxMzQyMn0.ri_w6QVJwgTIf2U2hp1L30uasJVO4Igqo08Wg8hF0xk"

SUPABASE_URL2 = "https://yubqlportprwjyadsbie.supabase.co"
SUPABASE_KEY2 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1YnFscG9ydHByd2p5YWRzYmllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkzNDkwOTIsImV4cCI6MjA5NDkyNTA5Mn0.cuHidkUzm6Af5ThEL5f6EdA_MOGevsOXcuPK2wh-evk"

SUPABASE_URL3 = "https://grqjtjvjmgewrcujfpjd.supabase.co"
SUPABASE_KEY3 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdycWp0anZqbWdld3JjdWpmcGpkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkzNTkyODEsImV4cCI6MjA5NDkzNTI4MX0.k1I6yAL23iXkSbvaVNsbRQpoLWqijuNHoeoOCd1-2DQ"

NX = 63
NY = 89

# ─────────────────────────────────────
# 초기화
# ─────────────────────────────────────

supabase  = create_client(SUPABASE_URL,  SUPABASE_KEY)
supabase2 = create_client(SUPABASE_URL2, SUPABASE_KEY2)
supabase3 = create_client(SUPABASE_URL3, SUPABASE_KEY3)

i2c  = busio.I2C(board.SCL, board.SDA)
sht  = adafruit_sht31d.SHT31D(i2c)
bh   = adafruit_bh1750.BH1750(i2c, address=0x23)
ads  = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
ser  = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)

# ─────────────────────────────────────
# CO2 읽기
# ─────────────────────────────────────

def read_co2():
    try:
        ser.reset_input_buffer()
        ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
        time.sleep(0.5)
        resp = ser.read(9)
        print(f"[DEBUG] CO2 응답길이: {len(resp)}, hex: {resp.hex()}")
        if len(resp) < 9:
            return None
        co2 = (resp[2] << 8) | resp[3]
        print(f"[DEBUG] CO2 값: {co2} ppm")
        return co2
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
        pct = (voltage - 0.190) / (0.572 - 0.190) * 100
        return round(max(0, min(100, pct)))
    except Exception as e:
        print(f"[ERROR] 토양수분: {e}")
        return None

# ─────────────────────────────────────
# 실내 센서 읽기
# ─────────────────────────────────────

def read_indoor():
    try:
        return {
            "co2_in":   read_co2(),
            "hum_in":   round(float(sht.relative_humidity), 1),
            "temp_in":  round(float(sht.temperature), 1),
            "soil_hum": read_soil(),
            "lux_in":   round(float(bh.lux), 1),
        }
    except Exception as e:
        print(f"[ERROR] 실내센서: {e}")
        return None

# ─────────────────────────────────────
# 기상청 API
# ─────────────────────────────────────

def get_base_time():
    now = datetime.now()
    if now.minute < 40:
        now -= timedelta(hours=1)
    return now.strftime("%Y%m%d"), now.strftime("%H00")

def fetch_weather():
    base_date, base_time = get_base_time()
    url = (
        "http://apis.data.go.kr/"
        "1360000/VilageFcstInfoService_2.0/"
        "getUltraSrtNcst"
    )
    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": 1, "numOfRows": 100, "dataType": "JSON",
        "base_date": base_date, "base_time": base_time,
        "nx": NX, "ny": NY,
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if "body" not in data["response"]:
            print("[ERROR] 기상청 데이터 없음")
            return None
        items = data["response"]["body"]["items"]["item"]
        weather = {"temp_out": None, "rain_out": 0, "wind_out": None, "solar_out": None}
        for item in items:
            cat = item["category"]
            val = item["obsrValue"]
            if cat == "T1H":
                weather["temp_out"] = float(val)
            elif cat == "RN1":
                # 이진값 처리: 강수없음=0, 강수있음=1
                weather["rain_out"] = 0 if val == "강수없음" else 1
            elif cat == "WSD":
                weather["wind_out"] = float(val)
            elif cat == "SI":
                weather["solar_out"] = float(val)
        return weather
    except Exception as e:
        print(f"[ERROR] 기상청 API: {e}")
        return None

# ─────────────────────────────────────
# 메인 작업
# ─────────────────────────────────────

def job():
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print("\n" + "=" * 50)
    print(f"[{now}] 데이터 수집 시작")
    print("=" * 50)

    indoor  = read_indoor()
    weather = fetch_weather()

    if indoor is None and weather is None:
        print("[SKIP] 데이터 없음")
        return

    dt = datetime.now().isoformat()

    base_row = {
        "datetime": dt,
        "hum_in":   indoor.get("hum_in")   if indoor  else None,
        "temp_in":  indoor.get("temp_in")  if indoor  else None,
        "soil_hum": indoor.get("soil_hum") if indoor  else None,
        "lux_in":   indoor.get("lux_in")   if indoor  else None,
        "temp_out": weather.get("temp_out") if weather else None,
        "rain_out": weather.get("rain_out") if weather else None,
        "wind_out": weather.get("wind_out") if weather else None,
    }

    # DB1: 학습용 — co2_in 항상 실제값
    row1 = {**base_row, "co2_in": indoor.get("co2_in") if indoor else None}

    # DB2: 예측 입출력 — 짝수시간만 co2_in 저장 (홀수=NULL → 고장 시뮬레이션)
    #      co2_predicted: 02_pipeline.py가 채움
    #      co2_source: 제어기(smartfarm_controller.py)가 채움
    row2 = {
    **base_row,
    "co2_in": indoor.get("co2_in") if (indoor and datetime.now().hour % 2 == 0) else None,
    "co2_predicted": None,
    "co2_source": f"sensor_{datetime.now().strftime('%Y%m%d%H%M%S')}",
}

    # DB3: 모니터링용 — co2_in 실제값만
    row3 = {"datetime": dt, "co2_in": indoor.get("co2_in") if indoor else None}

    for table, db, row in [
        ("sensor_data",   supabase,  row1),
        ("sensor_data_2", supabase2, row2),
        ("sensor_data_3", supabase3, row3),
    ]:
        try:
            db.table(table).insert(row).execute()
            print(f"[SUCCESS] {table} 저장 완료: {json.dumps(row, indent=2, ensure_ascii=False)}")
        except Exception as e:
            print(f"[ERROR] {table} 저장 실패: {e}")

# ─────────────────────────────────────
# 실행
# ─────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("스마트팜 데이터 수집기 시작 (1시간 주기)")
    print("=" * 60)

    job()

    schedule.every(1).hours.do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)