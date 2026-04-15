import serial
import time

class PZEM004TSensor:
    """
    PZEM-004T 전력 측정 센서 모듈
    UART1 (GPIO 12, 13)을 통해 전압, 전류, 전력, 에너지를 측정합니다.
    """
    def __init__(self, port='/dev/ttyS0', baudrate=9600, slave_id=0xF8):
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self.serial = serial.Serial(port, baudrate, timeout=1)
        print(f"PZEM004TSensor initialized on {port} at {baudrate} baud")
    
    def _calculate_crc(self, data):
        """CRC16 체크섬을 계산합니다."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc
    
    def read_power_data(self):
        """
        전압, 전류, 전력, 에너지 데이터를 읽어옵니다.
        반환: {'voltage': V, 'current': A, 'power': W, 'energy': kWh}
        """
        try:
            # PZEM-004T 읽기 명령어 (함수 코드: 0x04)
            cmd = [self.slave_id, 0x04, 0x00, 0x00, 0x00, 0x0A]
            crc = self._calculate_crc(cmd)
            cmd.extend([crc & 0xFF, (crc >> 8) & 0xFF])
            
            self.serial.write(cmd)
            time.sleep(0.1)
            
            # 응답 읽기 (25 바이트)
            response = self.serial.read(25)
            
            if len(response) >= 25:
                # 데이터 추출
                voltage = ((response[3] << 8) | response[4]) / 10.0  # V
                current = ((response[5] << 8) | response[6]) / 1000.0  # A
                power = ((response[7] << 8) | response[8]) / 10.0  # W
                energy = ((response[9] << 8) | response[10] | (response[11] << 16) | (response[12] << 24)) / 1000.0  # kWh
                
                return {
                    'voltage': voltage,
                    'current': current,
                    'power': power,
                    'energy': energy
                }
            else:
                print("Invalid response from PZEM-004T")
                return None
        except Exception as e:
            print(f"Error reading PZEM-004T: {e}")
            return None
    
    def cleanup(self):
        """시리얼 포트를 정리합니다."""
        self.serial.close()
        print("PZEM004TSensor cleaned up")

if __name__ == '__main__':
    try:
        pzem = PZEM004TSensor()
        for _ in range(5):
            data = pzem.read_power_data()
            if data:
                print(f"Voltage: {data['voltage']:.1f}V, Current: {data['current']:.3f}A, Power: {data['power']:.1f}W, Energy: {data['energy']:.3f}kWh")
            time.sleep(2)
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'pzem' in locals():
            pzem.cleanup()
