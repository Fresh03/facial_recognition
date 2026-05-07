import os
# Forțăm folosirea lgpio înainte de a importa gpiozero
os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'

from gpiozero import Servo
from time import sleep

# GPIO 18 (Pinul fizic 12)
# Setări specifice pentru SG90: 0.5ms la 2.4ms puls
servo = Servo(18, min_pulse_width=0.5/1000, max_pulse_width=2.4/1000)

print("Servo SG90 este gata!")

try:
    servo.min()
    print("Stânga")
    sleep(1)
    servo.mid()
    print("Mijloc")
    sleep(1)
    servo.max()
    print("Dreapta")
    sleep(1)
except KeyboardInterrupt:
    servo.detach()
    print("Program oprit.")
