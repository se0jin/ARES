
import serial
import time

class PZEM004TSensor:
    def __init__(self, serial_port="/dev/ttyS0", baud_rate=9600, slave_address=0xF8):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.slave_address = slave_address
        self.ser = None
        self._connect()
        print(f"PZEM004TSensor initialized on {self.serial_port} with baud rate {self.baud_rate}.")

    def _connect(self):
        try:
            self.ser = serial.Serial(self.serial_port, self.baud_rate, timeout=1)
            if not self.ser.isOpen():
                self.ser.open()
        except serial.SerialException as e:
            print(f"Error opening serial port {self.serial_port}: {e}")
            self.ser = None

    def _read_registers(self, register_address, num_registers):
        if not self.ser or not self.ser.isOpen():
            self._connect()
            if not self.ser or not self.ser.isOpen():
                return None

        # Modbus RTU read holding registers command (Function Code 0x03)
        # Request format: Slave Address (1 byte) + Function Code (1 byte) + Starting Address (2 bytes) + Quantity of Registers (2 bytes) + CRC (2 bytes)
        request = bytearray([
            self.slave_address,
            0x03,
            (register_address >> 8) & 0xFF, # Starting Address High Byte
            register_address & 0xFF,      # Starting Address Low Byte
            (num_registers >> 8) & 0xFF,  # Quantity of Registers High Byte
            num_registers & 0xFF          # Quantity of Registers Low Byte
        ])
        crc = self._calculate_crc(request)
        request.append(crc & 0xFF)
        request.append((crc >> 8) & 0xFF)

        try:
            self.ser.write(request)
            time.sleep(0.1) # Give some time for the sensor to respond
            response = self.ser.read(7 + num_registers * 2) # 7 bytes header/footer + 2 bytes per register

            if len(response) < 7 + num_registers * 2:
                print(f"Incomplete response from PZEM-004T: {response.hex()}")
                return None

            # Validate CRC
            received_crc = response[-2] | (response[-1] << 8)
            calculated_crc = self._calculate_crc(response[:-2])
            if received_crc != calculated_crc:
                print(f"CRC mismatch. Received: {hex(received_crc)}, Calculated: {hex(calculated_crc)}")
                return None

            # Extract data (each register is 2 bytes)
            data_bytes = response[3:-2]
            values = []
            for i in range(0, len(data_bytes), 2):
                values.append((data_bytes[i] << 8) | data_bytes[i+1])
            return values

        except serial.SerialException as e:
            print(f"Serial communication error with PZEM-004T: {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred during PZEM-004T read: {e}")
            return None

    def _calculate_crc(self, data):
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc

    def read_all_data(self):
        # Register addresses for PZEM-004T V3.0 (example, check datasheet for exact model)
        # Voltage: 0x0000 (2 bytes)
        # Current: 0x0001 (2 bytes)
        # Power: 0x0002 (2 bytes)
        # Energy: 0x0003 (2 bytes)
        # Frequency: 0x0004 (2 bytes)
        # Power Factor: 0x0005 (2 bytes)
        # Alarm: 0x0006 (2 bytes)
        # Reading 7 registers starting from 0x0000
        values = self._read_registers(0x0000, 7)
        if values is None or len(values) < 7:
            return None

        voltage = values[0] / 10.0
        current = (values[1] + (values[2] << 16)) / 1000.0 # Current is 4 bytes, two registers
        power = (values[3] + (values[4] << 16)) / 10.0    # Power is 4 bytes, two registers
        energy = (values[5] + (values[6] << 16)) / 1000.0 # Energy is 4 bytes, two registers
        # Note: The above current/power/energy parsing might need adjustment based on specific PZEM-004T model and its register mapping.
        # Some models might return these as single 2-byte registers or different scaling factors.
        # For PZEM-004T V3.0, current and power are typically 2 registers each.
        # Let's assume for now that the provided values list contains all 7 registers as 2-byte values.
        # A more robust implementation would read 0x0000 for voltage, 0x0001 for current_L, 0x0002 for current_H, etc.
        # For simplicity, let's re-read the datasheet for the exact register mapping and data types.

        # For now, let's simplify based on common PZEM-004T V3.0 register map where each value is 2 bytes (1 register)
        # and current/power/energy are 4 bytes (2 registers). This requires reading 10 registers (0x00 to 0x09).
        # Let's assume the values list contains:
        # [Voltage, Current_L, Current_H, Power_L, Power_H, Energy_L, Energy_H, Frequency, PowerFactor, Alarm]
        # This means we need to read 10 registers (0x0000 to 0x0009).

        # Let's adjust the read to 10 registers for a more complete set of data for V3.0
        values_full = self._read_registers(0x0000, 10)
        if values_full is None or len(values_full) < 10:
            return None

        voltage = values_full[0] / 10.0
        current = ((values_full[2] << 16) + values_full[1]) / 1000.0 # Current is 4 bytes (reg 0x01, 0x02)
        power = ((values_full[4] << 16) + values_full[3]) / 10.0    # Power is 4 bytes (reg 0x03, 0x04)
        energy = ((values_full[6] << 16) + values_full[5]) / 1000.0 # Energy is 4 bytes (reg 0x05, 0x06)
        frequency = values_full[7] / 10.0
        power_factor = values_full[8] / 100.0
        alarm = values_full[9]

        return {
            "voltage": voltage,
            "current": current,
            "power": power,
            "energy": energy,
            "frequency": frequency,
            "power_factor": power_factor,
            "alarm": alarm
        }

    def cleanup(self):
        if self.ser and self.ser.isOpen():
            self.ser.close()
            print(f"PZEM004TSensor serial port {self.serial_port} closed.")

if __name__ == '__main__':
    # Example usage:
    # Ensure your Raspberry Pi's serial port is enabled and correctly configured.
    # For Raspberry Pi 5, /dev/ttyS0 is typically the hardware UART.
    # You might need to disable console on serial port and enable serial port hardware.
    try:
        pzem = PZEM004TSensor(serial_port="/dev/ttyS0") # Adjust serial_port if needed
        print("Testing PZEM-004T Sensor...")
        for _ in range(5):
            data = pzem.read_all_data()
            if data:
                print(f"Voltage: {data['voltage']:.2f}V, Current: {data['current']:.3f}A, Power: {data['power']:.2f}W, Energy: {data['energy']:.3f}kWh, Frequency: {data['frequency']:.1f}Hz, Power Factor: {data['power_factor']:.2f}, Alarm: {data['alarm']}")
            else:
                print("Failed to read data from PZEM-004T.")
            time.sleep(2)

    except KeyboardInterrupt:
        print("Test interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'pzem' in locals():
            pzem.cleanup()

