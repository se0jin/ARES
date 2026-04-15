
import RPi.GPIO as GPIO
import time

class SSR_PWM:
    def __init__(self, pin, frequency=100):
        self.pin = pin
        self.frequency = frequency
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.OUT)
        self.pwm = GPIO.PWM(self.pin, self.frequency)
        self.pwm.start(0) # Start with 0% duty cycle (off)
        print(f"SSR_PWM initialized on pin {self.pin} with frequency {self.frequency}Hz")

    def set_duty_cycle(self, percentage):
        # Ensure percentage is within 0-100
        if not 0 <= percentage <= 100:
            raise ValueError("Duty cycle percentage must be between 0 and 100.")
        self.pwm.ChangeDutyCycle(percentage)
        print(f"SSR_PWM on pin {self.pin} set to {percentage}% duty cycle.")

    def off(self):
        self.pwm.ChangeDutyCycle(0)
        print(f"SSR_PWM on pin {self.pin} turned off.")

    def cleanup(self):
        self.pwm.stop()
        GPIO.cleanup(self.pin)
        print(f"SSR_PWM on pin {self.pin} cleaned up.")

if __name__ == '__main__':
    # Example usage:
    # This part will only run when the script is executed directly
    # It's for testing the SSR_PWM class independently
    try:
        # Assuming GPIO pin 18 for testing
        heater_ssr = SSR_PWM(pin=18, frequency=100)
        print("Testing SSR_PWM for 5 seconds...")
        heater_ssr.set_duty_cycle(50) # 50% power
        time.sleep(2)
        heater_ssr.set_duty_cycle(100) # 100% power
        time.sleep(1)
        heater_ssr.off()
        time.sleep(1)

    except KeyboardInterrupt:
        print("Test interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        # Always clean up GPIO settings
        if 'heater_ssr' in locals():
            heater_ssr.cleanup()
        else:
            GPIO.cleanup() # Clean up all GPIOs if SSR_PWM wasn't fully initialized


