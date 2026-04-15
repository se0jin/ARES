#!/usr/bin/env python3
import time
import RPi.GPIO as GPIO
import sys

from src.sht31_sensor import SHT31Sensor
from src.mhz19c_sensor import MHZ19CSensor
from src.pzem004t_sensor import PZEM004TSensor
from src.mcp3008_adc import MCP3008ADC
from src.ssr_pwm import SSR_PWM
from src.drv8825_stepper import DRV8825Stepper

# --- Configuration --- #
# SSR PWM 제어 핀
HEATER_SSR_PIN = 17          # PTC 팬히터
LED_SSR_PIN = 27             # COB LED바
FAN_SSR_PIN = 22             # JK 쿨러 (팬)
PUMP_SSR_PIN = 23            # 워터펌프

# DRV8825 스테퍼 드라이버
STEPPER_STEP_PIN = 18        # STEP 신호
STEPPER_DIR_PIN = 24         # DIR 신호

# 센서 설정
SHT31_ADDRESS = 0x44
MHZ19C_PORT = '/dev/ttyAMA0'  # UART0
PZEM_PORT = '/dev/ttyS0'      # UART1

# --- Hardware Initialization --- #
def initialize_hardware():
    global heater_ssr, led_ssr, fan_ssr, pump_ssr, stepper, sht31, mhz19c, pzem, adc
    
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    print("=" * 60)
    print("ARES System Initialization")
    print("=" * 60)
    
    try:
        # SSR PWM 초기화
        print("\n[1/7] Initializing SSR PWM modules...")
        heater_ssr = SSR_PWM(pin=HEATER_SSR_PIN)
        led_ssr = SSR_PWM(pin=LED_SSR_PIN)
        fan_ssr = SSR_PWM(pin=FAN_SSR_PIN)
        pump_ssr = SSR_PWM(pin=PUMP_SSR_PIN)
        
        # DRV8825 스테퍼 드라이버 초기화
        print("[2/7] Initializing DRV8825 Stepper Driver...")
        stepper = DRV8825Stepper(step_pin=STEPPER_STEP_PIN, dir_pin=STEPPER_DIR_PIN)
        
        # SHT31 센서 초기화
        print("[3/7] Initializing SHT31 Temperature/Humidity Sensor...")
        sht31 = SHT31Sensor(i2c_address=SHT31_ADDRESS)
        
        # MH-Z19C CO2 센서 초기화
        print("[4/7] Initializing MH-Z19C CO2 Sensor...")
        mhz19c = MHZ19CSensor(port=MHZ19C_PORT)
        
        # PZEM-004T 전력 측정 센서 초기화
        print("[5/7] Initializing PZEM-004T Power Sensor...")
        pzem = PZEM004TSensor(port=PZEM_PORT)
        
        # MCP3008 ADC 초기화
        print("[6/7] Initializing MCP3008 ADC...")
        adc = MCP3008ADC()
        
        print("[7/7] All hardware initialized successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"Error during hardware initialization: {e}")
        cleanup_hardware()
        sys.exit(1)

def cleanup_hardware():
    print("\nCleaning up hardware resources...")
    try:
        heater_ssr.cleanup()
        led_ssr.cleanup()
        fan_ssr.cleanup()
        pump_ssr.cleanup()
        stepper.cleanup()
        sht31.cleanup()
        mhz19c.cleanup()
        pzem.cleanup()
        adc.cleanup()
    except:
        pass
    GPIO.cleanup()
    print("Hardware cleanup complete.")

# --- Sensor Monitoring --- #
def read_sensor_data():
    data = {}
    
    # SHT31 데이터
    try:
        temp, hum = sht31.read_data()
        if temp is not None and hum is not None:
            data["temperature"] = f"{temp:.2f}C"
            data["humidity"] = f"{hum:.2f}%"
        else:
            data["temperature"] = "Error"
            data["humidity"] = "Error"
    except Exception as e:
        data["temperature"] = data["humidity"] = "Error"
    
    # MH-Z19C CO2 데이터
    try:
        co2 = mhz19c.read_co2()
        if co2 is not None:
            data["co2"] = f"{co2} ppm"
        else:
            data["co2"] = "Error"
    except Exception as e:
        data["co2"] = "Error"
    
    # PZEM-004T 전력 데이터
    try:
        power_data = pzem.read_power_data()
        if power_data:
            data["voltage"] = f"{power_data['voltage']:.1f}V"
            data["current"] = f"{power_data['current']:.3f}A"
            data["power"] = f"{power_data['power']:.1f}W"
            data["energy"] = f"{power_data['energy']:.3f}kWh"
        else:
            data["voltage"] = data["current"] = data["power"] = data["energy"] = "Error"
    except Exception as e:
        data["voltage"] = data["current"] = data["power"] = data["energy"] = "Error"
    
    # MCP3008 ADC 데이터
    try:
        ch0 = adc.read_channel(0)  # 외부 일사량 (조도)
        ch1 = adc.read_channel(1)  # 외부 온도 (환경 참조)
        ch2 = adc.read_channel(2)  # 강우 감지 (보안)
        
        data["light"] = f"{adc.convert_to_voltage(ch0):.3f}V" if ch0 else "Error"
        data["ext_temp"] = f"{adc.convert_to_voltage(ch1):.3f}V" if ch1 else "Error"
        data["rain"] = f"{adc.convert_to_voltage(ch2):.3f}V" if ch2 else "Error"
    except Exception as e:
        data["light"] = data["ext_temp"] = data["rain"] = "Error"
    
    return data

# --- CLI for Control and Monitoring --- #
def run_cli():
    print("\n" + "=" * 60)
    print("ARES System CLI - Type 'help' for commands")
    print("=" * 60 + "\n")
    
    while True:
        try:
            command = input("Ares> ").strip().lower()
            
            if command == "help":
                print("\nAvailable Commands:")
                print("  status              - Display all sensor readings")
                print("  heater <0-100>      - Set heater power (e.g., heater 50)")
                print("  led <0-100>         - Set LED brightness (e.g., led 75)")
                print("  fan <0-100>         - Set fan speed (e.g., fan 80)")
                print("  pump <0-100>        - Set pump power (e.g., pump 60)")
                print("  window <steps> <dir> - Control window (e.g., window 100 open)")
                print("  exit                - Exit the CLI and clean up")
                print()
            
            elif command == "status":
                sensor_data = read_sensor_data()
                print("\n" + "-" * 60)
                print("SENSOR STATUS")
                print("-" * 60)
                print(f"Temperature:        {sensor_data.get('temperature', 'N/A')}")
                print(f"Humidity:           {sensor_data.get('humidity', 'N/A')}")
                print(f"CO2 Concentration:  {sensor_data.get('co2', 'N/A')}")
                print(f"Voltage:            {sensor_data.get('voltage', 'N/A')}")
                print(f"Current:            {sensor_data.get('current', 'N/A')}")
                print(f"Power:              {sensor_data.get('power', 'N/A')}")
                print(f"Energy:             {sensor_data.get('energy', 'N/A')}")
                print(f"Light:              {sensor_data.get('light', 'N/A')}")
                print(f"Ext. Temp:          {sensor_data.get('ext_temp', 'N/A')}")
                print(f"Rain:               {sensor_data.get('rain', 'N/A')}")
                print("-" * 60 + "\n")
            
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
            
            elif command.startswith("fan "):
                try:
                    percentage = int(command.split(" ")[1])
                    fan_ssr.set_duty_cycle(percentage)
                except (IndexError, ValueError):
                    print("Invalid command. Usage: fan <0-100>")
            
            elif command.startswith("pump "):
                try:
                    percentage = int(command.split(" ")[1])
                    pump_ssr.set_duty_cycle(percentage)
                except (IndexError, ValueError):
                    print("Invalid command. Usage: pump <0-100>")
            
            elif command.startswith("window "):
                try:
                    parts = command.split(" ")
                    steps = int(parts[1])
                    direction_str = parts[2].lower()
                    direction = 1 if direction_str == "open" else -1
                    stepper.move_steps(steps, direction)
                except (IndexError, ValueError):
                    print("Invalid command. Usage: window <steps> <open/close>")
            
            elif command == "exit":
                print("Exiting ARES System CLI...")
                break
            
            else:
                print("Unknown command. Type 'help' for available commands.")
        
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            break
        except Exception as e:
            print(f"Error: {e}")

# --- Main Execution --- #
if __name__ == '__main__':
    try:
        initialize_hardware()
        run_cli()
    except KeyboardInterrupt:
        print("\n\nProgram interrupted by user.")
    except Exception as e:
        print(f"A critical error occurred: {e}")
    finally:
        cleanup_hardware()
        print("\nARES System shutdown complete.")
