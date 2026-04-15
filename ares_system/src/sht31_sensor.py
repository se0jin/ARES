import smbus2
import time

class SHT31Sensor:
    """
    SHT31 고정밀 온습도 센서 모듈
    I2C 통신으로 온도와 습도를 측정합니다.
    """
    def __init__(self, i2c_address=0x44, bus_num=1):
        self.i2c_address = i2c_address
        self.bus_num = bus_num
        self.bus = smbus2.SMBus(bus_num)
        print(f"SHT31Sensor initialized at I2C address {hex(i2c_address)}")
    
    def read_data(self):
        """
        센서로부터 온도와 습도를 읽어옵니다.
        반환: (temperature, humidity) 튜플
        """
        try:
            # SHT31 측정 시작 명령어 (0x2C06: 정밀도 높음)
            self.bus.write_i2c_block_data(self.i2c_address, 0x2C, [0x06])
            time.sleep(0.5)  # 측정 완료 대기
            
            # 데이터 읽기 (6 바이트)
            data = self.bus.read_i2c_block_data(self.i2c_address, 0x00, 6)
            
            # 온도 계산
            temp_raw = (data[0] << 8) | data[1]
            temperature = -45 + (175 * temp_raw / 65535)
            
            # 습도 계산
            hum_raw = (data[3] << 8) | data[4]
            humidity = 100 * hum_raw / 65535
            
            return temperature, humidity
        except Exception as e:
            print(f"Error reading SHT31: {e}")
            return None, None
    
    def cleanup(self):
        """I2C 버스를 정리합니다."""
        self.bus.close()
        print("SHT31Sensor cleaned up")

if __name__ == '__main__':
    try:
        sht31 = SHT31Sensor()
        for _ in range(5):
            temp, hum = sht31.read_data()
            if temp is not None and hum is not None:
                print(f"Temperature: {temp:.2f}C, Humidity: {hum:.2f}%")
            time.sleep(1)
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'sht31' in locals():
            sht31.cleanup()
