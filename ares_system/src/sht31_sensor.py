
import smbus
import time

class SHT31Sensor:
    def __init__(self, i2c_address=0x44, bus_num=1):
        self.i2c_address = i2c_address
        self.bus = smbus.SMBus(bus_num)
        # Send break command to clear any pending commands
        self.bus.write_byte(self.i2c_address, 0x30)
        self.bus.write_byte(self.i2c_address, 0xA2)
        time.sleep(0.01)
        print(f"SHT31Sensor initialized on I2C address {hex(self.i2c_address)}.")

    def read_data(self):
        # Send high repeatability measurement command
        self.bus.write_byte_data(self.i2c_address, 0x24, 0x00)
        time.sleep(0.015) # Wait for measurement to complete (15ms for high repeatability)

        data = self.bus.read_i2c_block_data(self.i2c_address, 0x00, 6)

        # Convert the data
        temp_raw = data[0] << 8 | data[1]
        humidity_raw = data[3] << 8 | data[4]

        # Calculate temperature in Celsius
        temperature = -45 + 175 * (temp_raw / 65535.0)
        # Calculate humidity in %
        humidity = 100 * (humidity_raw / 65535.0)

        return temperature, humidity

if __name__ == '__main__':
    # Example usage:
    try:
        sht31 = SHT31Sensor()
        print("Testing SHT31 Sensor...")
        for _ in range(5):
            temperature, humidity = sht31.read_data()
            print(f"Temperature: {temperature:.2f} C, Humidity: {humidity:.2f} %")
            time.sleep(1)

    except KeyboardInterrupt:
        print("Test interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")

