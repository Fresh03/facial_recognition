import face_recognition
import cv2
import os
import numpy as np
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
import mysql.connector
from mysql.connector import Error
import pickle

# ─────────────────────────────────────────────
#  DATABASE CONFIGURATION  (TiDB Cloud)
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":         "gateway01.eu-central-1.prod.aws.tidbcloud.com",
    "port":         4000,
    "user":         "3JPT5nLBgUvxaRJ.root",
    "password":     "lVuFmrdPxkTmcTT0",   # <- inlocuieste cu parola generata
    "database":     "facial_recognition",
    "ssl_disabled": False,
}

# ─────────────────────────────────────────────
#  GPIO / SERVO CONFIGURATION
# ─────────────────────────────────────────────
os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'
try:
    from gpiozero import Servo
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[WARNING] gpiozero not available - servo simulated.")

SERVO_PIN       = 18
PULSE_MIN       = 0.5 / 1000
PULSE_MAX       = 2.5 / 1000
DOOR_OPEN_DELAY = 10
FRAME_SKIP      = 5
TOLERANCE       = 0.4
SCALE           = 0.25
SAVE_DIR        = "known_faces"
os.makedirs(SAVE_DIR, exist_ok=True)


# =============================================================================
#  DATABASE HELPERS
# =============================================================================

def get_db_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        print(f"[DB ERROR] Could not connect: {e}")
        return None



def init_db():
    """Create all tables if they don't exist."""
    conn = get_db_connection()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        # users — no FK, plain table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                name       VARCHAR(100) NOT NULL UNIQUE,
                pin        VARCHAR(10)  NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # known_faces — no FK constraint, just name string
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS known_faces (
                id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                name       VARCHAR(100) NOT NULL,
                encoding   LONGBLOB     NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # access_logs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS access_logs (
                id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                person_name VARCHAR(100) NOT NULL,
                status      ENUM('GRANTED','DENIED') NOT NULL,
                accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("[DB] Tables initialised.")
    except Error as e:
        print(f"[DB ERROR] init_db: {e}")
    finally:
        cursor.close()
        conn.close()








def db_register_user(name: str, pin: str) -> bool:
    """Insert a new user record."""
    conn = get_db_connection()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (name, pin) VALUES (%s, %s)",
            (name, pin)
        )
        conn.commit()
        print(f"[DB] User '{name}' registered.")
        return True
    except Error as e:
        print(f"[DB ERROR] db_register_user: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def db_verify_pin(name: str, pin: str) -> bool:
    """Return True if the name+PIN combination is valid."""
    conn = get_db_connection()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM users WHERE name = %s AND pin = %s",
            (name, pin)
        )
        return cursor.fetchone() is not None
    except Error as e:
        print(f"[DB ERROR] db_verify_pin: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def db_user_exists(name: str) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE name = %s", (name,))
        return cursor.fetchone() is not None
    finally:
        cursor.close()
        conn.close()


def db_save_face(name: str, encoding: np.ndarray) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO known_faces (name, encoding) VALUES (%s, %s)",
            (name, pickle.dumps(encoding))
        )
        conn.commit()
        return True
    except Error as e:
        print(f"[DB ERROR] db_save_face: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def db_load_faces(name: str = None):
    """Load encodings for one user (or all if name=None).
    Returns (list[ndarray], list[str]).
    """
    conn = get_db_connection()
    if not conn:
        return [], []
    cursor = conn.cursor()
    encodings, names = [], []
    try:
        if name:
            cursor.execute(
                "SELECT name, encoding FROM known_faces WHERE name = %s", (name,))
        else:
            cursor.execute("SELECT name, encoding FROM known_faces")
        for (n, blob) in cursor.fetchall():
            encodings.append(pickle.loads(blob))
            names.append(n)
        print(f"[DB] Loaded {len(encodings)} encoding(s).")
    except Error as e:
        print(f"[DB ERROR] db_load_faces: {e}")
    finally:
        cursor.close()
        conn.close()
    return encodings, names


def db_log_access(person_name: str, status: str):
    conn = get_db_connection()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO access_logs (person_name, status) VALUES (%s, %s)",
            (person_name, status)
        )
        conn.commit()
        print(f"[DB] Access logged: {person_name} -> {status}")
    except Error as e:
        print(f"[DB ERROR] db_log_access: {e}")
    finally:
        cursor.close()
        conn.close()


def db_delete_user(name: str) -> bool:
    """Delete user + all their face encodings."""
    conn = get_db_connection()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM users WHERE name = %s", (name,))
        cursor.execute("DELETE FROM known_faces WHERE name = %s", (name,))
        conn.commit()
        print(f"[DB] User '{name}' deleted.")
        return True
    except Error as e:
        print(f"[DB ERROR] db_delete_user: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


# =============================================================================
#  SERVO HELPERS
# =============================================================================

def send_pulse(pulse_ms: float, settle_time: float = 0.7):
    if not GPIO_AVAILABLE:
        print(f"[SIMULATION] Servo pulse: {pulse_ms:.3f} ms")
        return
    s = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)
    value = ((pulse_ms / 1000) - PULSE_MIN) / (PULSE_MAX - PULSE_MIN) * 2 - 1
    s.value = max(-1.0, min(1.0, value))
    time.sleep(settle_time)
    s.detach()


def usa_deschisa(pulse_ms: float) -> float:
    PULSE_RANGE_MS = (PULSE_MAX - PULSE_MIN) * 1000
    nou = min(pulse_ms + PULSE_RANGE_MS / 2, PULSE_MAX * 1000)
    print(f"[SERVO] Door OPEN  ({pulse_ms:.3f} -> {nou:.3f} ms)")
    send_pulse(nou)
    return nou


def usa_inchisa(pulse_ms: float) -> float:
    PULSE_RANGE_MS = (PULSE_MAX - PULSE_MIN) * 1000
    nou = max(pulse_ms - PULSE_RANGE_MS / 2, PULSE_MIN * 1000)
    print(f"[SERVO] Door CLOSE ({pulse_ms:.3f} -> {nou:.3f} ms)")
    send_pulse(nou)
    return nou


def roteste(viteza, durata):
    if not GPIO_AVAILABLE:
        print(f"[SIMULATION] Rotate speed={viteza}, duration={durata}s")
        return
    s = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)
    s.value = viteza
    time.sleep(durata)
    s.value = 0
    s.detach()
    time.sleep(0.5)


def calibrate_servo():
    print("Calibrating servo...")
    roteste(0.4, 0.2)
    time.sleep(1)
    roteste(-0.4, 0.15)
    time.sleep(1)
    print("Calibration done!")


def test_servo():
    if not GPIO_AVAILABLE:
        print("[SIMULATION] Testing servo: min -> mid -> max")
        return
    servo = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)
    for pos, label in [(servo.min, "Left"), (servo.mid, "Middle"), (servo.max, "Right")]:
        pos()
        print(label)
        time.sleep(1)
    servo.detach()


# =============================================================================
#  FACE COLLECTION
# =============================================================================

def collect_faces(name: str):
    cap = cv2.VideoCapture(0)
    cap.set(3, 640)
    cap.set(4, 480)
    detector = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    count, max_images = 0, 20
    last_save = time.time()
    print(f"[INFO] Capturing faces for '{name}'...")

    while count < max_images:
        ret, frame = cap.read()
        if not ret:
            break
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, 1.3, 5)

        for (x, y, w, h) in faces:
            if time.time() - last_save < 0.4:
                continue
            face_img = frame[y:y+h, x:x+w]
            cv2.imwrite(os.path.join(SAVE_DIR, f"{name}_{count}.jpeg"), face_img)
            rgb_face = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
            encs = face_recognition.face_encodings(rgb_face)
            if encs:
                db_save_face(name, encs[0])
            count    += 1
            last_save = time.time()
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
            print(f"  [{count}/{max_images}] Captured.")

        cv2.putText(frame, f"Captured: {count}/{max_images}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        cv2.imshow("Capture Faces - Q to quit", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"[INFO] Done. {count} sample(s) saved.")


# =============================================================================
#  FACE RECOGNITION TEST
# =============================================================================

def test_face_recognition():
    known_encodings, known_names = db_load_faces()
    if not known_encodings:
        print("[ERROR] No faces in database!")
        return

    cap = cv2.VideoCapture(0)
    frame_counter = 0
    face_locations, face_names = [], []

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        small     = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

        if frame_counter % FRAME_SKIP == 0:
            face_locations = face_recognition.face_locations(rgb_small, model="hog")
            face_encs      = face_recognition.face_encodings(rgb_small, face_locations)
            face_names     = []
            for enc in face_encs:
                distances = face_recognition.face_distance(known_encodings, enc)
                name = "Unknown"
                if len(distances):
                    best = np.argmin(distances)
                    if distances[best] <= TOLERANCE:
                        name = known_names[best]
                face_names.append(name)

        frame_counter += 1

        for (top, right, bottom, left), name in zip(face_locations, face_names):
            top, right, bottom, left = top*4, right*4, bottom*4, left*4
            color  = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
            symbol = "v" if name != "Unknown" else "x"
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.putText(frame, f"{symbol} {name}", (left, top - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        cv2.imshow("Face Recognition Test - Q to quit", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


# =============================================================================
#  DOOR LOCK  — verified_name already passed PIN check in GUI
# =============================================================================

def door_lock(verified_name: str = None):
    """
    CLI mode  : asks name + PIN itself, then scans face.
    GUI mode  : receives verified_name (PIN already checked), goes straight to scan.
    """
    if verified_name:
        name = verified_name
    else:
        # CLI: ask name first, then PIN
        name = input("Enter your name: ").strip()
        pin  = input("Enter your PIN:  ").strip()
        if not db_verify_pin(name, pin):
            print("[ACCESS DENIED] Incorrect name or PIN.")
            db_log_access(name, "DENIED")
            return

    known_encodings, known_names = db_load_faces(name)
    if not known_encodings:
        print(f"[ERROR] No face data for '{name}'. Please register first.")
        return

    print(f"[INFO] PIN verified for '{name}'. Starting face scan...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[ERROR] Cannot open camera!")
        return

    frame_counter  = 0
    face_locations = []
    face_names     = []
    door_opened    = False
    open_timestamp = None
    pulse_current  = None
    PULSE_START_MS = (PULSE_MIN + PULSE_MAX) / 2 * 1000

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            small     = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
            rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

            if frame_counter % FRAME_SKIP == 0:
                face_locations = face_recognition.face_locations(rgb_small, model="hog")
                face_encs      = face_recognition.face_encodings(rgb_small, face_locations)
                face_names     = []
                for enc in face_encs:
                    distances = face_recognition.face_distance(known_encodings, enc)
                    det_name  = "Unknown"
                    if len(distances):
                        best = np.argmin(distances)
                        if distances[best] <= TOLERANCE:
                            det_name = known_names[best]
                    face_names.append(det_name)

                # Log failed face matches while door still closed
                for det_name in face_names:
                    if det_name != name and not door_opened:
                        db_log_access(name, "DENIED")

            frame_counter += 1

            # Grant access when face matches the authenticated user
            if any(n == name for n in face_names) and not door_opened:
                print(f"[ACCESS GRANTED] {name}")
                db_log_access(name, "GRANTED")
                pulse_current  = usa_deschisa(PULSE_START_MS)
                door_opened    = True
                open_timestamp = time.time()

            if door_opened:
                elapsed   = time.time() - open_timestamp
                remaining = max(0, int(DOOR_OPEN_DELAY - elapsed))
                cv2.putText(frame, f"Door closes in: {remaining}s",
                            (10, frame.shape[0] - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
                if elapsed >= DOOR_OPEN_DELAY:
                    print("[INFO] Time expired - closing door.")
                    usa_inchisa(pulse_current)
                    break

            cv2.putText(frame, f"Scanning for: {name}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 255), 2)

            for (top, right, bottom, left), det_name in zip(face_locations, face_names):
                top, right, bottom, left = top*4, right*4, bottom*4, left*4
                color  = (0, 255, 0) if det_name == name else (0, 0, 255)
                symbol = "OK" if det_name == name else "NO"
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                cv2.putText(frame, f"{symbol} {det_name}", (left, top - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            cv2.imshow("Door Lock - Q to exit", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                if door_opened and pulse_current:
                    usa_inchisa(pulse_current)
                break

    except KeyboardInterrupt:
        if door_opened and pulse_current:
            usa_inchisa(pulse_current)
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("[INFO] Program stopped.")


# =============================================================================
#  GUI
# =============================================================================

class FaceApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Face Recognition - Access Control")
        self.root.geometry("420x460")
        self.root.configure(bg="#1a1a2e")

        tk.Label(root, text="Access Control System",
                 font=("Arial", 17, "bold"),
                 bg="#1a1a2e", fg="#e0e0e0", pady=20).pack()

        btn = dict(width=28, height=2, relief="flat", font=("Arial", 11))

        tk.Button(root, text="Register New Person",
                  command=self._register_flow,
                  bg="#16213e", fg="#a8dadc", **btn).pack(pady=8)

        tk.Button(root, text="Open Door",
                  command=self._door_flow,
                  bg="#16213e", fg="#a8ff78", **btn).pack(pady=8)

        tk.Button(root, text="Test Recognition",
                  command=test_face_recognition,
                  bg="#16213e", fg="#f9c74f", **btn).pack(pady=8)

        tk.Button(root, text="Delete Person",
                  command=self._delete_flow,
                  bg="#16213e", fg="#ef233c", **btn).pack(pady=8)

        tk.Label(root, text="DB: TiDB Cloud  |  facial_recognition",
                 font=("Arial", 8), bg="#1a1a2e", fg="#555577").pack(side="bottom", pady=6)

    # ── Register: name first, then auto-generate PIN, then capture ───

    def _register_flow(self):
        name = simpledialog.askstring("Register", "Enter person's name:", parent=self.root)
        if not name:
            return
        name = name.strip()

        if db_user_exists(name):
            messagebox.showerror("Error", f"'{name}' is already registered!", parent=self.root)
            return

        pin = simpledialog.askstring("PIN", f"Alege un PIN pentru '{name}' (min. 4 cifre):",
                                         show='*', parent=self.root)
        if not pin or not pin.strip():
            return
        pin = pin.strip()

        if not pin.isdigit() or len(pin) < 4:
            messagebox.showerror("Error", "PIN-ul trebuie să conțină doar cifre (min. 4)!", parent=self.root)
            return

        pin2 = simpledialog.askstring("PIN", "Confirmă PIN-ul:", show='*', parent=self.root)
        if pin2 != pin:
            messagebox.showerror("Error", "PIN-urile nu coincid!", parent=self.root)
            return

        if not db_register_user(name, pin):
            messagebox.showerror("Error", "Could not save user to DB.", parent=self.root)
            return

        messagebox.showinfo("Succes", f"Utilizator '{name}' înregistrat!\nAcum începe captura feței.",
                            parent=self.root)

        self.root.after(100, lambda: self._capture_faces(name))

    def _capture_faces(self, name: str):
        cap      = cv2.VideoCapture(0)
        detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

        count, max_images = 0, 20
        last_save = time.time()

        while count < max_images:
            ret, frame = cap.read()
            if not ret:
                break
            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, 1.3, 5)

            for (x, y, w, h) in faces:
                if time.time() - last_save < 0.4:
                    continue
                face_img = frame[y:y+h, x:x+w]
                rgb_face = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
                encs     = face_recognition.face_encodings(rgb_face)
                if encs:
                    db_save_face(name, encs[0])
                    cv2.imwrite(os.path.join(SAVE_DIR, f"{name}_{count}.jpeg"), face_img)
                    count    += 1
                    last_save = time.time()
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)

            cv2.putText(frame, f"Captured: {count}/{max_images}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.imshow("Register Face - Q to stop", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()
        messagebox.showinfo("Done", f"Registered {count} sample(s) for '{name}'.",
                            parent=self.root)

    # ── Door: name first, then PIN, then face scan ───────────────────

    def _door_flow(self):
        name = simpledialog.askstring("Access", "Enter your name:", parent=self.root)
        if not name:
            return
        name = name.strip()

        pin = simpledialog.askstring("Access", f"Enter PIN for '{name}':",
                                     show='*', parent=self.root)
        if not pin:
            return

        if not db_verify_pin(name, pin):
            messagebox.showerror("Access Denied", "Incorrect name or PIN!", parent=self.root)
            db_log_access(name, "DENIED")
            return

        # PIN correct — go straight to face scan
        self.root.after(100, lambda: door_lock(verified_name=name))

    # ── Delete: name, then PIN to confirm ────────────────────────────

    def _delete_flow(self):
        name = simpledialog.askstring("Delete", "Enter name to delete:", parent=self.root)
        if not name:
            return
        name = name.strip()

        pin = simpledialog.askstring("Delete", f"Enter PIN for '{name}' to confirm:",
                                     show='*', parent=self.root)
        if not pin:
            return

        if not db_verify_pin(name, pin):
            messagebox.showerror("Error", "Incorrect name or PIN!", parent=self.root)
            return

        if db_delete_user(name):
            messagebox.showinfo("Deleted", f"'{name}' removed from database.", parent=self.root)
        else:
            messagebox.showerror("Error", "Could not delete - check DB connection.", parent=self.root)


# =============================================================================
#  ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import sys

    init_db()       # safe — uses IF NOT EXISTS

    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "calibrate":
            calibrate_servo()
        elif mode == "collect" and len(sys.argv) > 2:
            collect_faces(sys.argv[2])
        elif mode == "collect":
            print("Usage: python face_system.py collect <name>")
        elif mode == "test":
            test_face_recognition()
        elif mode == "door":
            door_lock()
        elif mode == "test_servo":
            test_servo()
        else:
            print("Available modes: calibrate | collect <name> | test | door | test_servo")
    else:
        root = tk.Tk()
        app  = FaceApp(root)
        root.mainloop()