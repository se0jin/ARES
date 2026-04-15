
import RPi.GPIO as GPIO
import time

class RelayControl:
    def __init__(self, pin):
        self.pin = pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.OUT)
        self.off() # Initialize relay to OFF state
        print(f"RelayControl initialized on pin {self.pin}.")

    def on(self):
        GPIO.output(self.pin, GPIO.HIGH) # Assuming active-high relay. Adjust if active-low.
        print(f"Relay on pin {self.pin} turned ON.")

    def off(self):
        GPIO.output(self.pin, GPIO.LOW) # Assuming active-high relay. Adjust if active-low.
        print(f"Relay on pin {self.pin} turned OFF.")

    def cleanup(self):
        GPIO.cleanup(self.pin)
        print(f"RelayControl on pin {self.pin} cleaned up.")

if __name__ == '__main__':
    # Example usage:
    # This part will only run when the script is executed directly
    # It's for testing the RelayControl class independently
    try:
        # Assuming GPIO pin 23 for testing
        water_pump_relay = RelayControl(pin=23)
        print("Testing RelayControl for 3 seconds...")
        water_pump_relay.on()
        time.sleep(1)
        water_pump_relay.off()
        time.sleep(1)
        water_pump_relay.on()
        time.sleep(1)

    except KeyboardInterrupt:
        print("Test interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        # Always clean up GPIO settings
        if 'water_pump_relay' in locals():
            water_pump_relay.cleanup()
        else:
            GPIO.cleanup() # Clean up all GPIOs if RelayControl wasn't fully initialized
