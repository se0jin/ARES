import spidev
import time

class MCP3008ADC:
    """
    MCP3008 8채널 ADC 모듈
    SPI0 (GPIO 8, 9, 10, 11)을 통해 아날로그 센서 데이터를 수집합니다.
    """
    def __init__(self, spi_channel=0, spi_device=0, clock_speed=1000000):
        self.spi = spidev.SpiDev()
        self.spi.open(spi_channel, spi_device)
        self.spi.max_speed_hz = clock_speed
        print(f"MCP3008ADC initialized on SPI{spi_channel}.{spi_device}")
    
    def read_channel(self, channel):
        """
        지정된 채널의 아날로그 값을 읽어옵니다.
        channel: 0~7
        반환: 0~1023 범위의 디지털 값
        """
        if not 0 <= channel <= 7:
            print(f"Invalid channel: {channel}. Must be 0-7.")
            return None
        
        try:
            # MCP3008 읽기 명령어
            # 첫 바이트: 0x01 (시작 비트)
            # 두 번째 바이트: 0x80 | (channel << 4) (채널 선택)
            # 세 번째 바이트: 0x00 (더미)
            cmd = [0x01, 0x80 | (channel << 4), 0x00]
            response = self.spi.xfer2(cmd)
            
            # 응답에서 10비트 값 추출
            value = ((response[1] & 0x03) << 8) | response[2]
            return value
        except Exception as e:
            print(f"Error reading MCP3008 channel {channel}: {e}")
            return None
    
    def read_all_channels(self):
        """
        모든 채널의 값을 읽어옵니다.
        반환: 8개 채널의 값 리스트
        """
        values = []
        for channel in range(8):
            value = self.read_channel(channel)
            values.append(value)
        return values
    
    def convert_to_voltage(self, value, vref=3.3):
        """
        디지털 값을 전압으로 변환합니다.
        value: 0~1023 범위의 디지털 값
        vref: 참조 전압 (기본값 3.3V)
        반환: 전압 값 (V)
        """
        if value is None:
            return None
        return (value / 1023.0) * vref
    
    def cleanup(self):
        """SPI 포트를 정리합니다."""
        self.spi.close()
        print("MCP3008ADC cleaned up")

if __name__ == '__main__':
    try:
        adc = MCP3008ADC()
        for _ in range(5):
            values = adc.read_all_channels()
            voltages = [adc.convert_to_voltage(v) for v in values]
            print(f"CH0: {voltages[0]:.3f}V, CH1: {voltages[1]:.3f}V, CH2: {voltages[2]:.3f}V")
            time.sleep(1)
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'adc' in locals():
            adc.cleanup()
