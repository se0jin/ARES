import RPi.GPIO as GPIO
import time

class SSR_PWM:
    """
    SSR(Solid State Relay) PWM 제어 모듈
    4개의 SSR을 통해 히터, LED, 팬, 워터펌프를 정밀 제어합니다.
    """
    def __init__(self, pin, frequency=100):
        self.pin = pin
        self.frequency = frequency
        self.pwm = None
        self._setup()
    
    def _setup(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.OUT)
        self.pwm = GPIO.PWM(self.pin, self.frequency)
        self.pwm.start(0)
        print(f"SSR_PWM initialized on GPIO {self.pin} with frequency {self.frequency}Hz")
    
    def set_duty_cycle(self, percentage):
        """
        듀티 사이클을 설정하여 전력 출력을 조절합니다.
        percentage: 0~100 (%)
        """
        if not 0 <= percentage <= 100:
            print(f"Invalid percentage: {percentage}. Must be between 0 and 100.")
            return
        self.pwm.ChangeDutyCycle(percentage)
        print(f"SSR on GPIO {self.pin} set to {percentage}%")
    
    def off(self):
        """SSR을 끕니다."""
        self.pwm.ChangeDutyCycle(0)
        print(f"SSR on GPIO {self.pin} turned OFF")
    
    def cleanup(self):
        """GPIO 핀 설정을 정리합니다."""
        if self.pwm:
            self.pwm.stop()
        GPIO.cleanup(self.pin)
        print(f"SSR on GPIO {self.pin} cleaned up")

if __name__ == '__main__':
    try:
        ssr = SSR_PWM(pin=17)
        for i in range(0, 101, 20):
            ssr.set_duty_cycle(i)
            time.sleep(1)
        ssr.off()
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'ssr' in locals():
            ssr.cleanup()
