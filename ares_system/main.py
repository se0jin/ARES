
import time
import RPi.GPIO as GPIO

from src.ssr_pwm import SSR_PWM
from src.relay_control import RelayControl
from src.stepper_motor import StepperMotor
from src.sht31_sensor import SHT31Sensor
from src.pzem004t_sensor import PZEM004TSensor
from src.mcp3008_adc import MCP3008ADC

# --- Configuration --- #
# GPIO Pin Definitions (BCM numbering)
# Adjust these based on your actual wiring

# SSR PWM
HEATER_SSR_PIN = 18 # Example GPIO pin for Heater SSR
LED_SSR_PIN = 19    # Example GPIO pin for LED SSR

# Relays (for water pump, fan, etc.)
WATER_PUMP_RELAY_PIN = 23 # Example GPIO pin for Water Pump Relay
FAN_RELAY_PIN = 24        # Example GPIO pin for Fan Relay

# Stepper Motors (ULN2003 driver)
# Stepper Motor 1 (e.g., for main group)
STEPPER1_IN1 = 5
STEPPER1_IN2 = 6
STEPPER1_IN3 = 13
STEPPER1_IN4 = 19

# Stepper Motor 2 (e.g., for experimental group)
STEPPER2_IN1 = 16
STEPPER2_IN2 = 20
STEPPER2_IN3 = 21
STEPPER2_IN4 = 26

# SHT31 Sensor (I2C)
SHT31_I2C_ADDRESS = 0x44 # Default address, check your sensor
SHT31_BUS_NUM = 1        # I2C bus number (usually 1 for Raspberry Pi)

# PZEM-004T Sensor (UART)
# Based on Excel: #0(ID1), #1(ID0) are used for PZEM-004T (A)
# On RPi 5, GPIO 0/1 are often ID_SD/ID_SC but can be used as UART
PZEM_SERIAL_PORT = "/dev/ttyAMA0" # Adjust if using a specific UART port
PZEM_BAUD_RATE = 9600
PZEM_SLAVE_ADDRESS = 0xF8

# MCP3008 ADC (SPI)
ADC_SPI_CHANNEL = 0 # SPI bus 0
ADC_SPI_DEVICE = 0  # SPI device 0 (CE0)
# ADC Channels based on Excel
# Soil moisture (A) -> CH0, Soil moisture (B) -> CH1
ADC_SOIL_MOISTURE_A_CHANNEL = 0
ADC_SOIL_MOISTURE_B_CHANNEL = 1
ADC_LIGHT_CHANNEL = 2 # Adjusted as CH0/1 are taken by soil moisture sensors

# --- Hardware Initialization --- #
def initialize_hardware():
    global heater_ssr, led_ssr, water_pump_relay, fan_relay, stepper1, stepper2, sht31, pzem, adc

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    print("Initializing hardware components...")

    # SSR PWM
    heater_ssr = SSR_PWM(pin=HEATER_SSR_PIN)
    led_ssr = SSR_PWM(pin=LED_SSR_PIN)

    # Relays
    water_pump_relay = RelayControl(pin=WATER_PUMP_RELAY_PIN)
    fan_relay = RelayControl(pin=FAN_RELAY_PIN)

    # Stepper Motors
    stepper1 = StepperMotor(in1_pin=STEPPER1_IN1, in2_pin=STEPPER1_IN2, in3_pin=STEPPER1_IN3, in4_pin=STEPPER1_IN4)
    stepper2 = StepperMotor(in1_pin=STEPPER2_IN1, in2_pin=STEPPER2_IN2, in3_pin=STEPPER2_IN3, in4_pin=STEPPER2_IN4)

    # SHT31 Sensor
    sht31 = SHT31Sensor(i2c_address=SHT31_I2C_ADDRESS, bus_num=SHT31_BUS_NUM)

    # PZEM-004T Sensor
    pzem = PZEM004TSensor(serial_port=PZEM_SERIAL_PORT, baud_rate=PZEM_BAUD_RATE, slave_address=PZEM_SLAVE_ADDRESS)

    # MCP3008 ADC
    adc = MCP3008ADC(spi_channel=ADC_SPI_CHANNEL, spi_device=ADC_SPI_DEVICE)

    print("Hardware initialization complete.")

def cleanup_hardware():
    print("Cleaning up hardware resources...")
    heater_ssr.cleanup()
    led_ssr.cleanup()
    water_pump_relay.cleanup()
    fan_relay.cleanup()
    stepper1.cleanup()
    stepper2.cleanup()
    pzem.cleanup() # SHT31 and ADC don't have explicit cleanup methods beyond closing bus
    adc.cleanup()
    GPIO.cleanup()
    print("Hardware cleanup complete.")

# --- Sensor Monitoring --- #
def read_sensor_data():
    data = {}
    try:
        temp, hum = sht31.read_data()
        data["temperature"] = f"{temp:.2f} C"
        data["humidity"] = f"{hum:.2f} %"
    except Exception as e:
        data["temperature"] = "Error"
        data["humidity"] = "Error"
        print(f"Error reading SHT31: {e}")

    try:
        pzem_data = pzem.read_all_data()
        if pzem_data:
            data["voltage"] = f"{pzem_data['voltage']:.2f}V"
            data["current"] = f"{pzem_data['current']:.3f}A"
            data["power"] = f"{pzem_data['power']:.2f}W"
            data["energy"] = f"{pzem_data['energy']:.3f}kWh"
        else:
            data["voltage"] = data["current"] = data["power"] = data["energy"] = "Error"
    except Exception as e:
        data["voltage"] = data["current"] = data["power"] = data["energy"] = "Error"
        print(f"Error reading PZEM-004T: {e}")

    try:
        data["light"] = adc.read_channel(ADC_LIGHT_CHANNEL)
        data["soil_moisture_a"] = adc.read_channel(ADC_SOIL_MOISTURE_A_CHANNEL)
        data["soil_moisture_b"] = adc.read_channel(ADC_SOIL_MOISTURE_B_CHANNEL)
    except Exception as e:
        data["light"] = data["soil_moisture_a"] = data["soil_moisture_b"] = "Error"
        print(f"Error reading MCP3008 ADC: {e}")

    return data

# --- CLI for Control and Monitoring --- #
def run_cli():
    print("\n--- Ares System CLI ---")
    print("Type 'help' for commands.")

    while True:
        try:
            command = input("Ares> ").strip().lower()

            if command == "help":
                print("Commands:")
                print("  status        - Display current sensor readings")
                print("  heater <0-100> - Set heater power percentage (e.g., heater 50)")
                print("  led <0-100>    - Set LED power percentage (e.g., led 75)")
                print("  pump on/off   - Turn water pump on or off")
                print("  fan on/off    - Turn fan on or off")
                print("  stepper1 <steps> <dir> - Move stepper motor 1 (e.g., stepper1 200 forward)")
                print("  stepper2 <steps> <dir> - Move stepper motor 2")
                print("  exit          - Exit the CLI and clean up")
            elif command == "status":
                sensor_data = read_sensor_data()
                print("\n--- Sensor Status ---")
                for key, value in sensor_data.items():
                    print(f"  {key.replace('_', ' ').title()}: {value}")
                print("---------------------")
            elif command.startswith("heater "):
                try:
                    percentage = int(command.split(" ")[1])
                    heater_ssr.set_duty_cycle(percentage)
                except (IndexError, ValueError):
                    print("Invalid command. Usage: heater <0-100>")
            elif command.startswith("led "):
                try:
                    percentage = int(command.split(" ")[1])
                    led_ssr.set_duty_cycle(percentage)
                except (IndexError, ValueError):
                    print("Invalid command. Usage: led <0-100>")
            elif command == "pump on":
                water_pump_relay.on()
            elif command == "pump off":
                water_pump_relay.off()
            elif command == "fan on":
                fan_relay.on()
            elif command == "fan off":
                fan_relay.off()
            elif command.startswith("stepper1 "):
                try:
                    parts = command.split(" ")
                    steps = int(parts[1])
                    direction_str = parts[2].lower()
                    direction = 1 if direction_str == "forward" else -1
                    stepper1.move_steps(steps, direction)
                except (IndexError, ValueError):
                    print("Invalid command. Usage: stepper1 <steps> <forward/backward>")
            elif command.startswith("stepper2 "):
                try:
                    parts = command.split(" ")
                    steps = int(parts[1])
                    direction_str = parts[2].lower()
                    direction = 1 if direction_str == "forward" else -1
                    stepper2.move_steps(steps, direction)
                except (IndexError, ValueError):
                    print("Invalid command. Usage: stepper2 <steps> <forward/backward>")
            elif command == "exit":
                print("Exiting CLI.")
                break
            else:
                print("Unknown command. Type 'help' for commands.")

        except Exception as e:
            print(f"An error occurred in CLI: {e}")

# --- Main Execution --- #
if __name__ == '__main__':
    try:
        initialize_hardware()
        run_cli()
    except KeyboardInterrupt:
        print("Program interrupted by user.")
    except Exception as e:
        print(f"A critical error occurred: {e}")
    finally:
        cleanup_hardware()

