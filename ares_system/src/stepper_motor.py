
import RPi.GPIO as GPIO
import time

class StepperMotor:
    def __init__(self, in1_pin, in2_pin, in3_pin, in4_pin, step_sleep_time=0.002):
        self.in1 = in1_pin
        self.in2 = in2_pin
        self.in3 = in3_pin
        self.in4 = in4_pin
        self.step_sleep_time = step_sleep_time

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.in1, GPIO.OUT)
        GPIO.setup(self.in2, GPIO.OUT)
        GPIO.setup(self.in3, GPIO.OUT)
        GPIO.setup(self.in4, GPIO.OUT)

        self.pins = [self.in1, self.in2, self.in3, self.in4]
        for pin in self.pins:
            GPIO.output(pin, GPIO.LOW)

        # Define the step sequence for full step drive
        self.step_sequence = [
            [1, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ]
        print(f"StepperMotor initialized with pins: {self.pins}")

    def _set_step(self, seq):
        GPIO.output(self.in1, seq[0])
        GPIO.output(self.in2, seq[1])
        GPIO.output(self.in3, seq[2])
        GPIO.output(self.in4, seq[3])

    def move_steps(self, steps, direction=1): # direction: 1 for forward, -1 for backward
        for _ in range(abs(steps)):
            if direction == 1:
                # Forward
                for i in range(len(self.step_sequence)):
                    self._set_step(self.step_sequence[i])
                    time.sleep(self.step_sleep_time)
            else:
                # Backward
                for i in range(len(self.step_sequence) - 1, -1, -1):
                    self._set_step(self.step_sequence[i])
                    time.sleep(self.step_sleep_time)
        print(f"StepperMotor moved {steps} steps in {'forward' if direction == 1 else 'backward'} direction.")

    def cleanup(self):
        for pin in self.pins:
            GPIO.output(pin, GPIO.LOW)
        GPIO.cleanup(self.pins)
        print(f"StepperMotor on pins {self.pins} cleaned up.")

if __name__ == '__main__':
    # Example usage:
    # This part will only run when the script is executed directly
    try:
        # Assuming GPIO pins for testing (adjust as per your T-Cobbler/wiring)
        # Example: IN1=5, IN2=6, IN3=13, IN4=19
        stepper1 = StepperMotor(in1_pin=5, in2_pin=6, in3_pin=13, in4_pin=19)
        print("Testing StepperMotor for 5 seconds...")
        stepper1.move_steps(200, direction=1) # Move 200 steps forward
        time.sleep(1)
        stepper1.move_steps(200, direction=-1) # Move 200 steps backward
        time.sleep(1)

    except KeyboardInterrupt:
        print("Test interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'stepper1' in locals():
            stepper1.cleanup()
        else:
            GPIO.cleanup() # Clean up all GPIOs if StepperMotor wasn't fully initialized
