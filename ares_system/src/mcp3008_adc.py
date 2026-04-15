
import spidev
import time
import RPi.GPIO as GPIO

class MCP3008ADC:
    def __init__(self, spi_channel=0, spi_device=0):
        self.spi = spidev.SpiDev()
        self.spi.open(spi_channel, spi_device)
        self.spi.max_speed_hz = 1350000 # MCP3008 max speed is 1.35MHz
        print(f"MCP3008ADC initialized on SPI channel {spi_channel}, device {spi_device}.")

    def read_channel(self, channel):
        # Read SPI data from MCP3008
        # Channel must be 0-7
        if not 0 <= channel <= 7:
            raise ValueError("ADC channel must be between 0 and 7.")

        # MCP3008 command format:
        # 1: Start bit
        # 1: Single-ended mode (0 for differential)
        # 3: Channel number
        # 0: Don't care
        adc_command = 0x18 | channel # 0x18 = 0001 1000

        # Send 3 bytes: 1st byte is start bit + single-ended bit + channel high bit
        # 2nd byte is channel low bits + don't care
        # 3rd byte is don't care
        # The MCP3008 expects 3 bytes, but only the first two are command related.
        # The third byte is a dummy byte to clock out the 10-bit result.
        r = self.spi.xfer2([1, (8 + channel) << 4, 0])

        # Extract the 10-bit data from the response
        # The result is in the second and third bytes
        # The first byte is always 0
        # The second byte contains the last 2 bits of the channel selection and the first 8 bits of the 10-bit data
        # The third byte contains the last 2 bits of the 10-bit data
        adc_out = ((r[1] & 0x03) << 8) + r[2]

        return adc_out

    def cleanup(self):
        self.spi.close()
        print("MCP3008ADC SPI connection closed.")

if __name__ == '__main__':
    # Example usage:
    try:
        # Ensure SPI is enabled on your Raspberry Pi (sudo raspi-config -> Interface Options -> SPI)
        adc = MCP3008ADC()
        print("Testing MCP3008 ADC Sensor...")
        for _ in range(10):
            # Assuming photoresistor on channel 0, soil moisture on channel 1, CO2 on channel 2
            light_value = adc.read_channel(0)
            soil_moisture_value = adc.read_channel(1)
            co2_value = adc.read_channel(2)

            print(f"Light: {light_value}, Soil Moisture: {soil_moisture_value}, CO2: {co2_value}")
            time.sleep(1)

    except KeyboardInterrupt:
        print("Test interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'adc' in locals():
            adc.cleanup()
        GPIO.cleanup() # Clean up all GPIOs if any were used (e.g., for other sensors)
