import time
import os

os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'

from gpiozero import Servo

SERVO_PIN = 18
PULSE_MIN = 0.5 / 1000
PULSE_MAX = 2.5 / 1000
PULSE_RANGE_MS = (PULSE_MAX - PULSE_MIN) * 1000

def unghi_la_puls(grade):
    return PULSE_MIN * 1000 + (grade / 180.0) * PULSE_RANGE_MS

def muta_servo(pulse_ms):
    s = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)
    value = ((pulse_ms / 1000) - PULSE_MIN) / (PULSE_MAX - PULSE_MIN) * 2 - 1
    s.value = max(-1.0, min(1.0, value))
    time.sleep(0.7)
    s.detach()

u1 = 91
u2 = 106


print("Pornire la 90° (centru)...")
muta_servo(unghi_la_puls(u1))
time.sleep(1)

print("Rotire la 180° (dreapta)...")
muta_servo(unghi_la_puls(u2))
time.sleep(2)

print("Revenire la 90° (centru)...")
muta_servo(unghi_la_puls(u1))
time.sleep(1)

print("Gata!")