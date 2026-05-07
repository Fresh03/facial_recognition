import time
import os

os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'

from gpiozero import Servo

SERVO_PIN = 18
PULSE_MIN = 0.5 / 1000
PULSE_MAX = 2.5 / 1000

s = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)

def roteste(viteza, durata):
    """viteza: -1.0 la 1.0 | durata: secunde"""
    s.value = viteza
    time.sleep(durata)
    s.value = 0   # STOP
    s.detach()
    time.sleep(0.5)

print("Rotire înainte...")
roteste(0.6, 0.17)   # ajustează durata până face exact 90°
time.sleep(1)

print("Rotire înapoi...")
roteste(-0.6, 0.13)  # aceeași durată = revine la poziția inițială
time.sleep(1)

print("Gata!")