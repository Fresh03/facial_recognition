import cv2
import os
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
import face_recognition
import numpy as np

# --- CONFIGURAȚII SERVO ---
# Setăm factory-ul pentru pini înainte de importul gpiozero
os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'

try:
    from gpiozero import Servo
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[AVERTISMENT] Biblioteca gpiozero nu este instalată.")

# --- PARAMETRI SG90 CORECȚI ---
SERVO_PIN       = 18
PULSE_MIN       = 0.5 / 1000   # 0.5ms → 0°
PULSE_MAX       = 2.5 / 1000   # 2.5ms → 180°
DOOR_OPEN_DELAY = 10

PULSE_RANGE_MS  = (PULSE_MAX - PULSE_MIN) * 1000   # = 2.0ms
PULSE_CENTER_MS = PULSE_MIN * 1000 + PULSE_RANGE_MS / 2  # = 1.5ms → 90°

PIN_CORECT = "1234"
SAVE_DIR = "known_faces"
os.makedirs(SAVE_DIR, exist_ok=True)

class FaceApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Program Recunoaștere Facială")
        self.root.geometry("400x350")

        self.label = tk.Label(root, text="Sistem Control Acces", font=("Arial", 16, "bold"), pady=20)
        self.label.pack()

        self.btn_reg = tk.Button(root, text="Înregistrare Față", command=self.verificare_pin_reg, 
                                 width=25, height=2, bg="#d1e7dd")
        self.btn_reg.pack(pady=10)

        self.btn_det = tk.Button(root, text="Detectare Față (Deschide Ușa)", command=self.verificare_pin_det, 
                                 width=25, height=2, bg="#cfe2ff")
        self.btn_det.pack(pady=10)

    def send_pulse(self, pulse_ms):
        if not GPIO_AVAILABLE:
            print(f"[SIMULARE] Servo la {pulse_ms}ms")
            return
        
        s = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)
        value = ((pulse_ms / 1000) - PULSE_MIN) / (PULSE_MAX - PULSE_MIN) * 2 - 1
        s.value = max(-1.0, min(1.0, value))
        time.sleep(0.7)   # Timp suficient ca SG90 să ajungă la poziție
        s.detach()

    def unghi_la_puls(self, grade):
        "Convertește grade (0-180) în milisecunde pentru SG90"
        return PULSE_MIN * 1000 + (grade / 180.0) * PULSE_RANGE_MS

    def actioneaza_usa(self):
        self.send_pulse(self.unghi_la_puls(90))   # Deschide: rotire +90° față de poziția inițială
        time.sleep(DOOR_OPEN_DELAY)
        self.send_pulse(self.unghi_la_puls(0))    # Închide: rotire înapoi la 0° (poziția inițială)

    def verificare_pin_reg(self):
        pin = simpledialog.askstring("PIN", "Introdu codul PIN:", show='*')
        if pin == PIN_CORECT:
            nume = simpledialog.askstring("Nume", "Introdu numele pentru salvare:")
            if nume: self.porneste_inregistrarea(nume)
        else:
            messagebox.showerror("Eroare", "PIN incorect!")

    def verificare_pin_det(self):
        pin = simpledialog.askstring("PIN", "Introdu codul PIN:", show='*')
        if pin == PIN_CORECT:
            nume = simpledialog.askstring("Nume", "Introdu numele persoanei căutate:")
            if nume: self.porneste_detectarea(nume)
        else:
            messagebox.showerror("Eroare", "PIN incorect!")

    def porneste_inregistrarea(self, name):
        cap = cv2.VideoCapture(0)
        xml_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_detector = cv2.CascadeClassifier(xml_path)
        
        count = 0
        max_images = 20
        last_save = time.time()

        while count < max_images:
            ret, frame = cap.read()
            if not ret: break
            
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_detector.detectMultiScale(gray, 1.3, 5)

            for (x, y, w, h) in faces:
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                if time.time() - last_save > 0.4:
                    face_img = frame[y:y+h, x:x+w]
                    path = os.path.join(SAVE_DIR, f"{name}_{count}.jpeg")
                    cv2.imwrite(path, face_img)
                    count += 1
                    last_save = time.time()

            cv2.putText(frame, f"Captura: {count}/{max_images}", (10, 30), 1, 1.5, (255,255,255), 2)
            cv2.imshow("Inregistrare Fata", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'): break

        cap.release()
        cv2.destroyAllWindows()

    def porneste_detectarea(self, nume_filtrat):
        known_encodings = []
        for filename in os.listdir(SAVE_DIR):
            if filename.startswith(nume_filtrat) and filename.lower().endswith((".jpeg", ".jpg", ".png")):
                path = os.path.join(SAVE_DIR, filename)
                img = face_recognition.load_image_file(path)
                enc = face_recognition.face_encodings(img)
                if enc: known_encodings.append(enc[0])

        if not known_encodings:
            messagebox.showwarning("Eroare", f"Nu există date salvate pentru {nume_filtrat}!")
            return

        cap = cv2.VideoCapture(0)
        found_authorized = False

        while True:
            ret, frame = cap.read()
            if not ret: break

            small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

            face_locs = face_recognition.face_locations(rgb_small)
            face_encs = face_recognition.face_encodings(rgb_small, face_locs)

            for face_encoding in face_encs:
                matches = face_recognition.compare_faces(known_encodings, face_encoding, tolerance=0.4)
                if True in matches:
                    cv2.putText(frame, f"SALUT, {nume_filtrat}!", (10, 50), 1, 2, (0, 255, 0), 3)
                    found_authorized = True
                    break

            cv2.imshow("Detectie - Apasa Q pentru a anula", frame)
            if found_authorized or (cv2.waitKey(1) & 0xFF == ord('q')):
                break

        cap.release()
        cv2.destroyAllWindows()

        if found_authorized:
            messagebox.showinfo("Acces", "Acces Permis! Se deschide ușa.")
            self.actioneaza_usa()

if __name__ == "__main__":
    root = tk.Tk()
    app = FaceApp(root)
    root.mainloop()