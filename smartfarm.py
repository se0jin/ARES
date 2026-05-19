import time
import board
import busio
import serial
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from supabase import create_client

# Supabase 설정
SUPABASE_URL = "https://hheeyhsiaqhxgufrvkui.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWV5aHNpYXFoeGd1ZnJ2a3VpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzkxMzc0MjIsImV4cCI6MjA5NDcxMzQyMn0.ri_w6QVJwgTIf2U2hp1L30uasJVO4Igqo08Wg8hF0xk"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 센서 초기화
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)

def read_co2():
    ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
    time.sleep(0.1)
    resp = ser.read(9)
    return resp[2]*256 + resp[3]

def read_soil():
    voltage = chan.voltage
    pct = max(0, min(100, (0.572 - voltage) / (0.572 - 0.190) * 100))
    return round(pct)

def send_data():
    data = {
        "co2_in": read_co2(),
        "hum_in": round(sht.relative_humidity, 1),
        "temp_in": round(sht.temperature, 1),
        "soil_hum": read_soil(),
        "lux_in": round(bh.lux, 1),
        "solar_out": None,
        "temp_out": None,
        "rain_out": None,
        "wind_out": None
    }
    supabase.table("sensor_data").insert(data).execute()
    print(f"전송 완료: {data}")

# 1시간마다 실행
while True:
    send_data()
    time.sleep(3600)
