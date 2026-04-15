import RPi.GPIO as GPIO
import time

class DRV8825Stepper:
    """
    DRV8825 스테퍼 드라이버 모듈
    STEP/DIR 방식으로 스테퍼 모터를 제어하여 창문 개폐를 조절합니다.
    """
    def __init__(self, step_pin=18, dir_pin=24, step_delay=0.002):
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.step_delay = step_delay
        self._setup()
    
    def _setup(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup([self.step_pin, self.dir_pin], GPIO.OUT)
        GPIO.output([self.step_pin, self.dir_pin], GPIO.LOW)
        print(f"DRV8825Stepper initialized on STEP GPIO {self.step_pin}, DIR GPIO {self.dir_pin}")
    
    def move_steps(self, steps, direction=1):
        """
        스테퍼 모터를 지정된 스텝 수만큼 이동시킵니다.
        steps: 이동할 스텝 수
        direction: 1 (정방향/열기), -1 (역방향/닫기)
        """
        # 방향 설정
        GPIO.output(self.dir_pin, GPIO.HIGH if direction > 0 else GPIO.LOW)
        
        # 스텝 펄스 생성
        for _ in range(steps):
            GPIO.output(self.step_pin, GPIO.HIGH)
            time.sleep(self.step_delay / 2)
            GPIO.output(self.step_pin, GPIO.LOW)
            time.sleep(self.step_delay / 2)
        
        print(f"Stepper moved {steps} steps in {'open' if direction > 0 else 'close'} direction")
    
    def set_speed(self, rpm):
        """
        스테퍼 모터의 속도를 RPM으로 설정합니다.
        rpm: 분당 회전 수
        """
        # 스텝 딜레이 계산 (200 스텝/회전 기준)
        self.step_delay = (60.0 / (rpm * 200)) * 1000
        print(f"Stepper speed set to {rpm} RPM")
    
    def cleanup(self):
        """GPIO 핀 설정을 정리합니다."""
        GPIO.cleanup([self.step_pin, self.dir_pin])
        print("DRV8825Stepper cleaned up")

if __name__ == '__main__':
    try:
        stepper = DRV8825Stepper()
        print("Testing DRV8825 Stepper...")
        stepper.move_steps(200, direction=1)  # 열기
        time.sleep(1)
        stepper.move_steps(200, direction=-1)  # 닫기
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'stepper' in locals():
            stepper.cleanup()
