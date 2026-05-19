"""
스마트팜 센서 통합 테스트 코드
작동 확인된 센서:
  - SHT31  (온습도, I2C 0x44)
  - BH1750 (조도,  I2C 0x23)
  - ADS1115 + SEN0193 (토양수분, I2C 0x48)
  - MH-Z19 (CO2, UART /dev/ttyAMA0)
"""

import time
import board
import busio
import serial
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ── I2C 초기화 ────────────────────────────────────────────────
i2c = busio.I2C(board.SCL, board.SDA)

# ── SHT31 온습도 센서 ─────────────────────────────────────────
def read_sht31():
    try:
        sht = adafruit_sht31d.SHT31D(i2c)
        temp = round(sht.temperature, 1)
        hum  = round(sht.relative_humidity, 1)
        print(f"[SHT31]  온도: {temp}°C  습도: {hum}%")
        return temp, hum
    except Exception as e:
        print(f"[SHT31]  오류: {e}")
        return None, None

# ── BH1750 조도 센서 ──────────────────────────────────────────
def read_bh1750():
    try:
        bh  = adafruit_bh1750.BH1750(i2c, address=0x23)
        lux = round(bh.lux, 1)
        print(f"[BH1750] 조도: {lux} lux")
        return lux
    except Exception as e:
        print(f"[BH1750] 오류: {e}")
        return None

# ── ADS1115 + SEN0193 토양수분 ────────────────────────────────
# 보정값: 건조=0.572V / 포화=0.190V
def read_soil():
    try:
        ads  = ADS.ADS1115(i2c)
        chan = AnalogIn(ads, 0)
        
        # 5번 읽어서 평균
        voltages = []
        for _ in range(5):
            voltages.append(chan.voltage)
            time.sleep(0.1)
        
        voltage = sum(voltages) / len(voltages)
        pct = max(0, min(100, (voltage - 0.190) / (0.572 - 0.190) * 100))
        print(f"[SEN0193] 토양수분: {pct:.1f}%  전압: {voltage:.3f}V")
        return round(pct)
    except Exception as e:
        print(f"[SEN0193] 오류: {e}")
        return None

# ── MH-Z19 CO2 센서 ───────────────────────────────────────────
def read_co2():
    try:
        ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
        ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
        time.sleep(0.1)
        resp = ser.read(9)
        ser.close()
        co2 = resp[2] * 256 + resp[3]
        print(f"[MH-Z19] CO2: {co2} ppm")
        return co2
    except Exception as e:
        print(f"[MH-Z19] 오류: {e}")
        return None

# ── 메인 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 40)
    print("  스마트팜 센서 테스트 시작")
    print("=" * 40)
    read_sht31()
    read_bh1750()
    read_soil()
    read_co2()
    print("=" * 40)
    print("  테스트 완료")
    print("=" * 40)
