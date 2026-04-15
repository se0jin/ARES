import serial
import time

class MHZ19CSensor:
    """
    MH-Z19C CO2 센서 모듈
    UART0 (GPIO 14, 15)를 통해 CO2 농도를 측정합니다.
    """
    def __init__(self, port='/dev/ttyAMA0', baudrate=9600):
        self.port = port
        self.baudrate = baudrate
        self.serial = serial.Serial(port, baudrate, timeout=1)
        print(f"MHZ19CSensor initialized on {port} at {baudrate} baud")
    
    def read_co2(self):
        """
        CO2 농도를 읽어옵니다.
        반환: CO2 농도 (ppm)
        """
        try:
            # CO2 측정 명령어
            cmd = [0xFF, 0x01, 0x86, 0x00, 0x00, 0x00, 0x00, 0x00, 0x79]
            self.serial.write(cmd)
            time.sleep(0.1)
            
            # 응답 읽기 (9 바이트)
            response = self.serial.read(9)
            
            if len(response) == 9 and response[0] == 0xFF and response[1] == 0x86:
                # CO2 농도 계산 (상위 바이트, 하위 바이트)
                co2 = (response[2] << 8) | response[3]
                return co2
            else:
                print("Invalid response from MH-Z19C")
                return None
        except Exception as e:
            print(f"Error reading MH-Z19C: {e}")
            return None
    
    def cleanup(self):
        """시리얼 포트를 정리합니다."""
        self.serial.close()
        print("MHZ19CSensor cleaned up")

if __name__ == '__main__':
    try:
        mhz19c = MHZ19CSensor()
        for _ in range(5):
            co2 = mhz19c.read_co2()
            if co2 is not None:
                print(f"CO2 Concentration: {co2} ppm")
            time.sleep(2)
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'mhz19c' in locals():
            mhz19c.cleanup()
