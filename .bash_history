sudo apt update
sudo apt upgrade
sudo raspi-config
sudo apt install ibus ibus-hangul
sudo rebot
sudo reboot
pip install -r requirements.txt --break-system-packages
cd ~/Desktop/arestest/arestest
pip install -r requirements.txt --break-system-packages
sudo raspi-config
sudo reboot
pip install -r requirements.txt
i2cdetect -y 1
sudo raspi-config
sudo reboot
i2cdetect -y 1
ls /dev/ttyAMA0
ls /dev/serial*
python3 -c "
import serial, time
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
print('응답:', resp.hex())
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
python3 -c "
import RPi.GPIO as GPIO, time
GPIO.setmode(GPIO.BCM)
GPIO.setup(24, GPIO.OUT)  # STEP
GPIO.setup(25, GPIO.OUT)  # DIR
GPIO.setup(8, GPIO.OUT)   # ENABLE
GPIO.output(8, GPIO.LOW)  # 활성화
GPIO.output(25, GPIO.HIGH)
for _ in range(200):
    GPIO.output(24, GPIO.HIGH); time.sleep(0.005)
    GPIO.output(24, GPIO.LOW);  time.sleep(0.005)
GPIO.output(8, GPIO.HIGH)
GPIO.cleanup()
print('스텝퍼 완료')
"
python3 -c "
import RPi.GPIO as GPIO, time
GPIO.setmode(GPIO.BCM)
for pin in [17, 27, 22]:
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

print('PTC 히터 ON')
GPIO.output(17, GPIO.HIGH); time.sleep(2)
GPIO.output(17, GPIO.LOW);  print('OFF')

print('냉각팬 ON')
GPIO.output(27, GPIO.HIGH); time.sleep(2)
GPIO.output(27, GPIO.LOW);  print('OFF')

print('펠티에 ON')
GPIO.output(22, GPIO.HIGH); time.sleep(2)
GPIO.output(22, GPIO.LOW);  print('OFF')

GPIO.cleanup()
"
python3 -c "
import board, busio, adafruit_htu21d
i2c = busio.I2C(board.SCL, board.SDA)
s = adafruit_htu21d.HTU21D(i2c)
print(f'온도: {s.temperature:.1f}°C')
print(f'습도: {s.relative_humidity:.1f}%')
"
python3 -c "
import board, busio, adafruit_sht31d
i2c = busio.I2C(board.SCL, board.SDA)
s = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {s.temperature:.1f}°C')
print(f'습도: {s.relative_humidity:.1f}%')
"
pip install adafruit-circuitpython-sht31d --break-system-packages
python3 -c "
import board, busio, adafruit_sht31d
i2c = busio.I2C(board.SCL, board.SDA)
s = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {s.temperature:.1f}°C')
print(f'습도: {s.relative_humidity:.1f}%')
"
pip install pzem004t --break-system-packages
sudo nano /boot/config.txt
pip install pzem004t --break-system-packages
sudo reboot
python3 -c "
import serial, time

ser = serial.Serial('/dev/ttyAMA4', 9600, timeout=1)  # GPIO20,21은 ttyAMA4
# 전압 읽기 명령
ser.write(bytes.fromhex('01 04 00 00 00 0A 70 0D'.replace(' ','')))
time.sleep(0.1)
resp = ser.read(25)
print('응답:', resp.hex())
"
pip install pzem004t --break-system-packages
sudo nano /boot/config.txt
sudo reboot
pip install pzem004t --break-system-packages
ls /dev/ttyAMA*
pip install pyserial --break-system-packages
python3 -c "
import serial, time

ser = serial.Serial('/dev/ttyAMA4', 9600, timeout=1)  # GPIO20,21은 ttyAMA4
# 전압 읽기 명령
ser.write(bytes.fromhex('01 04 00 00 00 0A 70 0D'.replace(' ','')))
time.sleep(0.1)
resp = ser.read(25)
print('응답:', resp.hex())
"
python3 -c "
import serial, time
ser = serial.Serial('/dev/ttyAMA10', 9600, timeout=1)
ser.write(bytes.fromhex('01040000000A700D'))
time.sleep(0.1)
resp = ser.read(25)
print('응답:', resp.hex())
"
cat /boot/firmware/config.txt | grep uart
sudo nano /boot/firmware/config.txt
sudo reboot
sudo nano /boot/firmware/config.txt
sudo reboot
ls /dev/ttyAMA*
cat /boot/firmware/config.txt
ls /dev/ttyAMA*
pip install pyserial --break-system-packages
ls /dev/ttyAMA*
python3 -c "
import serial, time

ser = serial.Serial('/dev/ttyAMA4', 9600, timeout=1)  # GPIO20,21은 ttyAMA4
# 전압 읽기 명령
ser.write(bytes.fromhex('01 04 00 00 00 0A 70 0D'.replace(' ','')))
time.sleep(0.1)
resp = ser.read(25)
print('응답:', resp.hex())
"
python3 -c "
import serial, time
ser = serial.Serial('/dev/ttyAMA3', 9600, timeout=1)
ser.write(bytes.fromhex('01040000000A700D'))
time.sleep(0.5)
resp = ser.read(25)
print('응답:', resp.hex())
"
i2cdetect -y 1
sudo python3 -c "
import board, busio, adafruit_bh1750
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {sensor.lux:.1f} lux')
"
i2cdetect -y 1
sudo raspi-config
sudo reboot
ls /dev/i2c*
i2detect -y 1

i2cdetect -y 1
sudo i2cdetect -y 1
i2cdetect -y 1
ls /dev/i2c*
i2cdetect -y 1
python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'전압: {voltage:.3f}V')
print(f'토양수분: {pct:.1f}%')
"
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo pip install adafruit-circuitpython-sht31d --break-system-packages
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo pip install adafruit-circuitpython-ads1x15 --break-system-packages
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo python3 -c "
import board, busio
import adafruit_sht31d
import adafruit_bh1750
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import serial, time

# SHT31 온습도
i2c = busio.I2C(board.SCL, board.SDA)
sht = adafruit_sht31d.SHT31D(i2c)
print(f'온도: {sht.temperature:.1f}°C  습도: {sht.relative_humidity:.1f}%')

# BH1750 조도
bh = adafruit_bh1750.BH1750(i2c, address=0x23)
print(f'조도: {bh.lux:.1f} lux')

# ADS1115 토양수분
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
voltage = chan.voltage
pct = max(0, min(100, (2.8 - voltage) / (2.8 - 1.2) * 100))
print(f'토양수분: {pct:.1f}%  전압: {voltage:.3f}V')

# MH-Z19 CO2
ser = serial.Serial('/dev/ttyAMA0', 9600, timeout=1)
ser.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')
time.sleep(0.1)
resp = ser.read(9)
co2 = resp[2]*256 + resp[3]
print(f'CO2: {co2} ppm')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
sudo i2cdetect -y 1
sudo python3 -c "
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan = AnalogIn(ads, 0)
print(f'전압: {chan.voltage:.3f}V')
"
nano ~/smartfarm.py
git remote add origin https://github.com/se0jin/ARES
git add smartfarm.py
git commit -m "토양수분 보정값 수정"
git push origin develop
git pull origin develop --rebase
git add smartfarm.py
git commit -m "토양수분 보정값 수정"
git push origin develop
git pull origin develop
git add sensor_test.py
git commit -m "센서 확인 테스트"
git add sensor_test.py
git commit -m "센서 확인 테스트"
git push origin develop
sudo python3 sensor_test.py
sudo i2cdetect -y 1
cat /sys/class/thermal/cooling_device0/cur_state
echo 3 | sudo tee /sys/class/thermal/cooling_device0/cur_state
echo 0 | sudo tee /sys/class/thermal/cooling_device0/cur_state
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 24)
lgpio.gpio_claim_output(h, 25)
lgpio.gpio_claim_output(h, 8)
lgpio.gpio_write(h, 8, 0)
lgpio.gpio_write(h, 25, 1)
for _ in range(200):
    lgpio.gpio_write(h, 24, 1); time.sleep(0.005)
    lgpio.gpio_write(h, 24, 0); time.sleep(0.005)
lgpio.gpio_write(h, 8, 1)
lgpio.gpiochip_close(h)
print('스텝퍼 완료!')
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('환기팬 ON')
time.sleep(3)
lgpio.gpio_write(h, 17, 0)
print('환기팬 OFF')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('환기팬 ON')
time.sleep(3)
lgpio.gpio_write(h, 17, 0)
print('환기팬 OFF')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('GPIO17 HIGH')
time.sleep(5)
lgpio.gpio_write(h, 17, 0)
print('GPIO17 LOW')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('환기팬 ON')
time.sleep(3)
lgpio.gpio_write(h, 17, 0)
print('환기팬 OFF')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('GPIO17 HIGH')
time.sleep(5)
lgpio.gpio_write(h, 17, 0)
print('GPIO17 LOW')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 24)
lgpio.gpio_claim_output(h, 25)
lgpio.gpio_claim_output(h, 8)
lgpio.gpio_write(h, 8, 0)
lgpio.gpio_write(h, 25, 1)
for _ in range(200):
    lgpio.gpio_write(h, 24, 1); time.sleep(0.005)
    lgpio.gpio_write(h, 24, 0); time.sleep(0.005)
lgpio.gpio_write(h, 8, 1)
lgpio.gpiochip_close(h)
print('스텝퍼 완료!')
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 24)
lgpio.gpio_claim_output(h, 25)
lgpio.gpio_claim_output(h, 8)
lgpio.gpio_write(h, 8, 0)
lgpio.gpio_write(h, 25, 1)
for _ in range(200):
    lgpio.gpio_write(h, 24, 1); time.sleep(0.005)
    lgpio.gpio_write(h, 24, 0); time.sleep(0.005)
lgpio.gpio_write(h, 8, 1)
lgpio.gpiochip_close(h)
print('스텝퍼 완료!')
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 24)
lgpio.gpio_claim_output(h, 25)
lgpio.gpio_claim_output(h, 8)
lgpio.gpio_write(h, 8, 0)
lgpio.gpio_write(h, 25, 1)
for _ in range(200):
    lgpio.gpio_write(h, 24, 1); time.sleep(0.005)
    lgpio.gpio_write(h, 24, 0); time.sleep(0.005)
lgpio.gpio_write(h, 8, 1)
lgpio.gpiochip_close(h)
print('스텝퍼 완료!')
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 24)
lgpio.gpio_claim_output(h, 25)
lgpio.gpio_claim_output(h, 8)
lgpio.gpio_write(h, 8, 0)
lgpio.gpio_write(h, 25, 1)
for _ in range(200):
    lgpio.gpio_write(h, 24, 1); time.sleep(0.005)
    lgpio.gpio_write(h, 24, 0); time.sleep(0.005)
lgpio.gpio_write(h, 8, 1)
lgpio.gpiochip_close(h)
print('스텝퍼 완료!')
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('환기팬 ON')
time.sleep(3)
lgpio.gpio_write(h, 17, 0)
print('환기팬 OFF')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('환기팬 ON')
time.sleep(3)
lgpio.gpio_write(h, 17, 0)
print('환기팬 OFF')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('환기팬 ON')
time.sleep(3)
lgpio.gpio_write(h, 17, 0)
print('환기팬 OFF')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 17)
lgpio.gpio_write(h, 17, 1)
print('환기팬 ON')
time.sleep(3)
lgpio.gpio_write(h, 17, 0)
print('환기팬 OFF')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 5)
lgpio.gpio_write(h, 5, 1)
print('LED OFF')
time.sleep(3)
lgpio.gpio_write(h, 5, 0)
print('LED ON')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 5)
lgpio.gpio_write(h, 5, 1)
print('HIGH')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 5)
lgpio.gpio_write(h, 5, 1)
print('LED OFF')
time.sleep(3)
lgpio.gpio_write(h, 5, 0)
print('LED ON')
lgpio.gpiochip_close(h)
"
python3 -c "
import lgpio, time
h = lgpio.gpiochip_open(0)
lgpio.gpio_claim_output(h, 5)
lgpio.gpio_write(h, 5, 1)
print('LED OFF')
time.sleep(3)
lgpio.gpio_write(h, 5, 0)
print('LED ON')
lgpio.gpiochip_close(h)
"
sudo python3 -c "
import requests
from datetime import datetime

url = 'https://apihub.kma.go.kr/api/typ01/url/stn_inf.php'
params = {
    'inf': 'AWS',
    'stn': '',
    'tm': datetime.now().strftime('%Y%m%d%H%M'),
    'help': 0,
    'authKey': 'dgPNFI74TRODzRSO-A0TYA'
}
response = requests.get(url, params=params, timeout=10)
response.encoding = 'euc-kr'
lines = response.text.split('\n')
for line in lines:
    if '천안' in line:
        print(line)
"
sudo python3 -c "
import requests
from datetime import datetime

url = 'https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min'
params = {
    'tm2': datetime.now().strftime('%Y%m%d%H%M'),
    'stn': 232,
    'disp': 1,
    'help': 0,
    'authKey': 'dgPNFI74TRODzRSO-A0TYA'
}
response = requests.get(url, params=params, timeout=10)
print(response.text)
"
sudo python3 -c "
import requests
from datetime import datetime, timedelta

tm = (datetime.now() - timedelta(minutes=10)).strftime('%Y%m%d%H%M')

url = 'https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min'

params = {
    'tm2': tm,
    'stn': 129,
    'disp': 1,
    'help': 0,
    'authKey': 'YOUR_KEY'
}

response = requests.get(url, params=params)

print(response.text)
"
sudo python3 -c "
import requests
from datetime import datetime, timedelta

tm = (datetime.now() - timedelta(minutes=10)).strftime('%Y%m%d%H%M')

url = 'https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min'

params = {
    'tm2': tm,
    'stn': 129,
    'disp': 1,
    'help': 0,
    'authKey':'dgPNFI74TRODzRSO-A0TYA'
}

response = requests.get(url, params=params)

print(response.text)
"
sudo python3 -c "
import requests
from datetime import datetime, timedelta

tm = (datetime.now() - timedelta(hours=1)).strftime('%Y%m%d%H00')

url = 'https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php'

params = {
    'tm': tm,
    'stn': 129,
    'authKey': 'dgPNFI74TRODzRSO-A0TYA'
}

response = requests.get(url, params=params, timeout=10)

print(response.text)
"
sudo python3 -c "
import requests
from datetime import datetime, timedelta

now = datetime.now()

if now.minute < 40:
    now -= timedelta(hours=1)

base_date = now.strftime('%Y%m%d')
base_time = now.strftime('%H00')

url = (
    'http://apis.data.go.kr/'
    '1360000/VilageFcstInfoService_2.0/'
    'getUltraSrtNcst'
)

params = {
    'serviceKey': 'YOUR_API_KEY',
    'pageNo': 1,
    'numOfRows': 100,
    'dataType': 'JSON',
    'base_date': base_date,
    'base_time': base_time,
    'nx': 68,
    'ny': 100
}

response = requests.get(url, params=params)

data = response.json()

items = data['response']['body']['items']['item']

print('===== CATEGORY LIST =====')

for item in items:
    print(item['category'])
"
sudo python3 -c "
import requests
from datetime import datetime, timedelta

now = datetime.now()

if now.minute < 40:
    now -= timedelta(hours=1)

base_date = now.strftime('%Y%m%d')
base_time = now.strftime('%H00')

url = (
    'http://apis.data.go.kr/'
    '1360000/VilageFcstInfoService_2.0/'
    'getUltraSrtNcst'
)

params = {
    'serviceKey': 'dgPNFI74TRODzRSO-A0TYA',
    'pageNo': 1,
    'numOfRows': 100,
    'dataType': 'JSON',
    'base_date': base_date,
    'base_time': base_time,
    'nx': 68,
    'ny': 100
}

response = requests.get(url, params=params)

print(response.text)
"
sudo python3 -c "
import requests
from datetime import datetime, timedelta

now = datetime.now()

if now.minute < 40:
    now -= timedelta(hours=1)

base_date = now.strftime('%Y%m%d')
base_time = now.strftime('%H00')

url = (
    'http://apis.data.go.kr/'
    '1360000/VilageFcstInfoService_2.0/'
    'getUltraSrtNcst'
)

params = {
    'serviceKey': '91d35aba86632a9a336b4166ddb9507a9d1a94cc8ab97e88bf34c57fc3d465ac',
    'pageNo': 1,
    'numOfRows': 100,
    'dataType': 'JSON',
    'base_date': base_date,
    'base_time': base_time,
    'nx': 68,
    'ny': 100
}

response = requests.get(url, params=params)

print(response.text)
"
sudo python3 -c "
import requests
from datetime import datetime, timedelta

tm = (datetime.now() - timedelta(minutes=10)) \
    .strftime('%Y%m%d%H%M')

url = 'https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min'

params = {
    'tm2': tm,
    'stn': 129,
    'disp': 1,
    'help': 0,
    'authKey': 'dgPNFI74TRODzRSO-A0TYA'
}

response = requests.get(url, params=params, timeout=10)

text = response.text

print(text)

print('\n===== HEADER CHECK =====')

for line in text.splitlines():

    if line.startswith('# YYMMDD'):
        print(line)
"
