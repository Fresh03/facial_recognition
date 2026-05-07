import face_recognition
import cv2
import os
import numpy as np
import time

os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'

from gpiozero import Servo

# ─────────────────────────────────────────────
# CONFIGURARE
# ─────────────────────────────────────────────
SERVO_PIN       = 18
PULSE_MIN       = 0.5 / 1000    # 0°
PULSE_MAX       = 2.4 / 1000    # 180°
DOOR_OPEN_DELAY = 10
FRAME_SKIP      = 5
TOLERANCE       = 0.4
SCALE           = 0.25

# ─────────────────────────────────────────────
# SERVO — mișcare relativă de exact 90°
#
# Logica gpiozero: value=-1.0 → PULSE_MIN (0°)
#                  value= 0.0 → PULSE_MID (90°)
#                  value= 1.0 → PULSE_MAX (180°)
#
# Scara e liniară, deci 90° = 1.0 unitate din cele 2.0 totale.
# Dacă pornim de la o valoare necunoscută (brațul pus manual),
# folosim RPi.GPIO direct cu pulsuri absolute ca să facem
# o deplasare RELATIVĂ de exact 90° față de poziția curentă.
#
# Strategia corectă fără a ști poziția inițială:
#   - Reținem pulsul curent și adăugăm/scădem echivalentul a 90°.
#   - 90° din 180° = 50% din intervalul de puls.
#   - Interval total = PULSE_MAX - PULSE_MIN = 1.9ms
#   - 90° = 1.9ms / 2 = 0.95ms
# ─────────────────────────────────────────────

PULSE_RANGE_MS = (PULSE_MAX - PULSE_MIN) * 1000   # 1.9 ms pentru 180°
PULSE_90DEG_MS = PULSE_RANGE_MS / 2               # 0.95 ms pentru 90°

# Pulsul curent — nu știm unde e brațul, deci nu îl mișcăm la init.
# Îl vom seta la prima mișcare și îl vom ține minte.
current_pulse_ms = None   # necunoscut până la prima detecție

def send_pulse(pulse_ms: float, settle_time: float = 0.7):
    """Trimite un puls absolut (în ms) și detașează după ce servo-ul ajunge."""
    s = Servo(
        SERVO_PIN,
        min_pulse_width=PULSE_MIN,
        max_pulse_width=PULSE_MAX
    )
    # Convertim ms → valoare gpiozero (-1.0 … 1.0)
    # value = (pulse_ms/1000 - PULSE_MIN) / (PULSE_MAX - PULSE_MIN) * 2 - 1
    value = ((pulse_ms / 1000) - PULSE_MIN) / (PULSE_MAX - PULSE_MIN) * 2 - 1
    value = max(-1.0, min(1.0, value))   # clamp la interval valid
    s.value = value
    time.sleep(settle_time)
    s.detach()
    return pulse_ms

def usa_deschisa(pulse_pornire_ms: float) -> float:
    """
    Deschide ușa: mișcă servo-ul cu +90° față de poziția curentă.
    Returnează noul puls după mișcare.
    """
    nou_puls = pulse_pornire_ms + PULSE_90DEG_MS
    nou_puls = min(nou_puls, PULSE_MAX * 1000)   # nu depășim limita fizică
    print(f"[SERVO] Ușă DESCHISĂ (+90° | puls: {pulse_pornire_ms:.3f}ms → {nou_puls:.3f}ms)")
    send_pulse(nou_puls)
    return nou_puls

def usa_inchisa(pulse_pornire_ms: float) -> float:
    """
    Închide ușa: mișcă servo-ul cu -90° înapoi la poziția inițială.
    Returnează noul puls după mișcare.
    """
    nou_puls = pulse_pornire_ms - PULSE_90DEG_MS
    nou_puls = max(nou_puls, PULSE_MIN * 1000)   # nu depășim limita fizică
    print(f"[SERVO] Ușă ÎNCHISĂ (-90° | puls: {pulse_pornire_ms:.3f}ms → {nou_puls:.3f}ms)")
    send_pulse(nou_puls)
    return nou_puls

# ─────────────────────────────────────────────
# ÎNCĂRCARE FEȚE CUNOSCUTE
# ─────────────────────────────────────────────
known_faces_dir = "known_faces"
known_encodings = []
known_names     = []

print("[INFO] Se încarcă fețele cunoscute...")

if not os.path.isdir(known_faces_dir):
    print(f"[EROARE] Directorul '{known_faces_dir}' nu există!")
    exit(1)

for filename in os.listdir(known_faces_dir):
    if filename.lower().endswith((".jpeg", ".jpg", ".png")):
        path  = os.path.join(known_faces_dir, filename)
        image = face_recognition.load_image_file(path)
        encodings = face_recognition.face_encodings(image, num_jitters=5)
        if encodings:
            known_encodings.append(encodings[0])
            known_names.append(os.path.splitext(filename)[0])
            print(f"  - Față încărcată: {os.path.splitext(filename)[0]}")

if not known_encodings:
    print("[EROARE] Nu s-au găsit fețe valide în 'known_faces'!")
    exit(1)

print(f"[INFO] {len(known_encodings)} față/fețe încărcate.")
print("[INFO] Servo-ul NU se mișcă la pornire — lasă brațul în poziția dorită manual.\n")

# ─────────────────────────────────────────────
# PORNIRE CAMERĂ
# ─────────────────────────────────────────────
print("[INFO] Pornire cameră... Apasă 'q' pentru ieșire manuală.")
video_capture = cv2.VideoCapture(0)

if not video_capture.isOpened():
    print("[EROARE] Nu s-a putut deschide camera!")
    exit(1)

frame_counter       = 0
face_locations      = []
face_names          = []
door_opened         = False
open_timestamp      = None
pulse_initial_ms    = None   # pulsul la momentul detecției (= poziția "închis")
pulse_current_ms    = None   # pulsul curent al servo-ului

# ─────────────────────────────────────────────
# BUCLĂ PRINCIPALĂ
# ─────────────────────────────────────────────
try:
    while True:
        ret, frame = video_capture.read()
        if not ret:
            break

        small_frame     = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
        rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

        if frame_counter % FRAME_SKIP == 0:
            face_locations      = face_recognition.face_locations(
                rgb_small_frame, model="hog"
            )
            face_encodings_list = face_recognition.face_encodings(
                rgb_small_frame, face_locations
            )

            face_names = []
            for face_encoding in face_encodings_list:
                matches        = face_recognition.compare_faces(
                    known_encodings, face_encoding, tolerance=TOLERANCE
                )
                name           = "Unknown"
                face_distances = face_recognition.face_distance(
                    known_encodings, face_encoding
                )
                if len(face_distances) > 0:
                    best_match_index = np.argmin(face_distances)
                    if matches[best_match_index]:
                        name = known_names[best_match_index]
                face_names.append(name)

        frame_counter += 1

        # ── Deschide ușa la prima detecție ──
        authorized_detected = any(n != "Unknown" for n in face_names)

        if authorized_detected and not door_opened:
            print(f"[ACCES PERMIS] {[n for n in face_names if n != 'Unknown']}")

            # Presupunem că brațul e acum la mijlocul intervalului de puls
            # ca punct de plecare neutru — ajustează PULSE_START_MS dacă știi
            # exact unde e brațul tău la pornire (ex: 0.5ms dacă e la 0°).
            PULSE_START_MS  = (PULSE_MIN + PULSE_MAX) / 2 * 1000   # ~1.45ms = 90°
            # DACĂ brațul tău e la 0° la pornire, comentează linia de sus și folosește:
            # PULSE_START_MS = PULSE_MIN * 1000   # 0.5ms = 0°

            pulse_initial_ms = PULSE_START_MS
            pulse_current_ms = usa_deschisa(pulse_initial_ms)
            door_opened      = True
            open_timestamp   = time.time()

        # ── Countdown și închidere automată ──
        if door_opened:
            elapsed   = time.time() - open_timestamp
            remaining = int(DOOR_OPEN_DELAY - elapsed)

            if elapsed >= DOOR_OPEN_DELAY:
                print("[INFO] Timp expirat. Se închide ușa.")
                usa_inchisa(pulse_current_ms)   # -90° exact înapoi
                break

            cv2.putText(
                frame,
                f"Usa se inchide in: {remaining}s",
                (10, frame.shape[0] - 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75, (0, 255, 255), 2
            )

        # ── GUI identic cu face_test.py ──
        for (top, right, bottom, left), name in zip(face_locations, face_names):
            top    *= 4
            right  *= 4
            bottom *= 4
            left   *= 4

            if name != "Unknown":
                color  = (0, 255, 0)
                symbol = "✔"
            else:
                color  = (0, 0, 255)
                symbol = "✘"

            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.putText(
                frame,
                f"{symbol} {name}",
                (left, top - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8, color, 2
            )

        cv2.imshow("Face Recognition", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("[INFO] Ieșire manuală.")
            if door_opened and pulse_current_ms:
                usa_inchisa(pulse_current_ms)
            break

except KeyboardInterrupt:
    print("\n[INFO] Întrerupt de utilizator.")
    if door_opened and pulse_current_ms:
        usa_inchisa(pulse_current_ms)

finally:
    video_capture.release()
    cv2.destroyAllWindows()
    print("[INFO] Program oprit.")
