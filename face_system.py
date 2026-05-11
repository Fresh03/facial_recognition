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
#  CONFIGURARE BAZA DE DATE (TiDB Cloud)
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":         "gateway01.eu-central-1.prod.aws.tidbcloud.com",
    "port":         4000,
    "user":         "3JPT5nLBgUvxaRJ.root",
    "password":     "lVuFmrdPxkTmcTT0",
    "database":     "facial_recognition",
    "ssl_disabled": False,
}

# ─────────────────────────────────────────────
#  CONFIGURARE GPIO / SERVO
# ─────────────────────────────────────────────
os.environ['GPIOZERO_PIN_FACTORY'] = 'lgpio'
try:
    from gpiozero import Servo
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[ATENTIE] gpiozero nu este disponibil - servo simulat.")

# Credentiale administrator 
ADMIN_USER = "admin"
ADMIN_PASS = "admin1234"

# Constante servo si camera
SERVO_PIN       = 18
PULSE_MIN       = 0.5 / 1000   # puls minim servo (secunde)
PULSE_MAX       = 2.5 / 1000   # puls maxim servo (secunde)
DOOR_OPEN_DELAY = 10            # cat timp ramane usa deschisa (secunde)
FRAME_SKIP      = 5             # analizeaza un cadru din 5 (performanta)
TOLERANCE       = 0.4           # pragul de recunoastere faciala
SCALE           = 0.25          # redimensionare cadru pentru viteza
SAVE_DIR        = "known_faces" # folder local pentru poze de rezerva
os.makedirs(SAVE_DIR, exist_ok=True)


# =============================================================================
#  FUNCTII BAZA DE DATE
# =============================================================================

def conectare_db():
    # Incearca sa se conecteze la TiDB Cloud
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        print(f"[EROARE DB] Nu s-a putut conecta: {e}")
        return None


def initializare_db():
    # Creeaza tabelele daca nu exista deja
    conn = conectare_db()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        # Tabela utilizatori - un rand per persoana
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                name       VARCHAR(100) NOT NULL UNIQUE,
                pin        VARCHAR(10)  NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Tabela encodari faciale - mai multe per persoana
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS known_faces (
                id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                name       VARCHAR(100) NOT NULL,
                encoding   LONGBLOB     NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Tabela loguri acces - fiecare intrare sau refuz
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS access_logs (
                id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                person_name VARCHAR(100) NOT NULL,
                status      ENUM('GRANTED','DENIED') NOT NULL,
                accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Tabela administratori - conturi cu acces complet
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                username   VARCHAR(100) NOT NULL UNIQUE,
                password   VARCHAR(255) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        # Insereaza contul admin implicit daca nu exista deja
        cursor.execute("SELECT id FROM admins WHERE username = %s", (ADMIN_USER,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO admins (username, password) VALUES (%s, %s)",
                (ADMIN_USER, ADMIN_PASS)
            )
            conn.commit()
            print(f"[DB] Cont admin implicit creat: '{ADMIN_USER}'")

        print("[DB] Tabele initializate.")
    except Error as e:
        print(f"[EROARE DB] initializare_db: {e}")
    finally:
        cursor.close()
        conn.close()


def db_verifica_admin(username: str, parola: str) -> bool:
    # Verifica daca credentialele de admin sunt corecte
    conn = conectare_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM admins WHERE username = %s AND password = %s",
            (username, parola)
        )
        return cursor.fetchone() is not None
    except Error as e:
        print(f"[EROARE DB] db_verifica_admin: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def db_lista_utilizatori():
    # Returneaza lista tuturor utilizatorilor cu data inregistrarii
    conn = conectare_db()
    if not conn:
        return []
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name, created_at FROM users ORDER BY created_at DESC")
        return cursor.fetchall()
    except Error as e:
        print(f"[EROARE DB] db_lista_utilizatori: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def db_ultima_accesare(nume: str):
    # Returneaza ultimul eveniment de acces pentru un utilizator
    conn = conectare_db()
    if not conn:
        return None
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT status, accessed_at
            FROM access_logs
            WHERE person_name = %s
            ORDER BY accessed_at DESC
            LIMIT 1
        """, (nume,))
        return cursor.fetchone()
    except Error as e:
        print(f"[EROARE DB] db_ultima_accesare: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def db_loguri_acces(limit: int = 50):
    # Returneaza ultimele N loguri de acces din baza de date
    conn = conectare_db()
    if not conn:
        return []
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT person_name, status, accessed_at
            FROM access_logs
            ORDER BY accessed_at DESC
            LIMIT %s
        """, (limit,))
        return cursor.fetchall()
    except Error as e:
        print(f"[EROARE DB] db_loguri_acces: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def db_statistici():
    # Returneaza statistici generale: total useri, total accese, granted, denied
    conn = conectare_db()
    if not conn:
        return {}
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM users")
        total_useri = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM access_logs")
        total_accese = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM access_logs WHERE status = 'GRANTED'")
        total_granted = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM access_logs WHERE status = 'DENIED'")
        total_denied = cursor.fetchone()[0]

        return {
            "total_useri":   total_useri,
            "total_accese":  total_accese,
            "total_granted": total_granted,
            "total_denied":  total_denied,
        }
    except Error as e:
        print(f"[EROARE DB] db_statistici: {e}")
        return {}
    finally:
        cursor.close()
        conn.close()


def db_reseteaza_pin(nume: str, pin_nou: str) -> bool:
    # Actualizeaza PIN-ul unui utilizator (folosit de admin, fara a cere PIN-ul vechi)
    conn = conectare_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET pin = %s WHERE name = %s",
            (pin_nou, nume)
        )
        conn.commit()
        ok = cursor.rowcount > 0
        if ok:
            print(f"[DB] PIN resetat pentru '{nume}'.")
        return ok
    except Error as e:
        print(f"[EROARE DB] db_reseteaza_pin: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def db_inregistrare_user(nume: str, pin: str) -> bool:
    # Adauga un utilizator nou in baza de date
    conn = conectare_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (name, pin) VALUES (%s, %s)",
            (nume, pin)
        )
        conn.commit()
        print(f"[DB] Utilizator '{nume}' inregistrat.")
        return True
    except Error as e:
        print(f"[EROARE DB] db_inregistrare_user: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def db_verifica_pin(nume: str, pin: str) -> bool:
    # Verifica daca combinatia nume + PIN este corecta
    conn = conectare_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM users WHERE name = %s AND pin = %s",
            (nume, pin)
        )
        return cursor.fetchone() is not None
    except Error as e:
        print(f"[EROARE DB] db_verifica_pin: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def db_user_exista(nume: str) -> bool:
    # Verifica daca un utilizator cu acest nume exista deja
    conn = conectare_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE name = %s", (nume,))
        return cursor.fetchone() is not None
    finally:
        cursor.close()
        conn.close()


def db_salveaza_fata(nume: str, encoding: np.ndarray) -> bool:
    # Salveaza o encodare faciala in baza de date (serializata cu pickle)
    conn = conectare_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO known_faces (name, encoding) VALUES (%s, %s)",
            (nume, pickle.dumps(encoding))
        )
        conn.commit()
        return True
    except Error as e:
        print(f"[EROARE DB] db_salveaza_fata: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def db_incarca_fete(nume: str = None):
    # Incarca encodarile faciale pentru un user (sau toate daca nume=None)
    # Returneaza (lista_encodari, lista_nume)
    conn = conectare_db()
    if not conn:
        return [], []
    cursor = conn.cursor()
    encodari, nume_lista = [], []
    try:
        if nume:
            cursor.execute(
                "SELECT name, encoding FROM known_faces WHERE name = %s", (nume,))
        else:
            cursor.execute("SELECT name, encoding FROM known_faces")
        for (n, blob) in cursor.fetchall():
            encodari.append(pickle.loads(blob))
            nume_lista.append(n)
        print(f"[DB] {len(encodari)} encodare(i) incarcata(e).")
    except Error as e:
        print(f"[EROARE DB] db_incarca_fete: {e}")
    finally:
        cursor.close()
        conn.close()
    return encodari, nume_lista


def db_log_acces(nume_persoana: str, status: str):
    # Salveaza un eveniment de acces (GRANTED sau DENIED) in log
    conn = conectare_db()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO access_logs (person_name, status) VALUES (%s, %s)",
            (nume_persoana, status)
        )
        conn.commit()
        print(f"[DB] Acces logat: {nume_persoana} -> {status}")
    except Error as e:
        print(f"[EROARE DB] db_log_acces: {e}")
    finally:
        cursor.close()
        conn.close()


def db_sterge_user(nume: str) -> bool:
    # Sterge utilizatorul si toate encodarile lui din baza de date
    conn = conectare_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM users WHERE name = %s", (nume,))
        cursor.execute("DELETE FROM known_faces WHERE name = %s", (nume,))
        conn.commit()
        print(f"[DB] Utilizator '{nume}' sters.")
        return True
    except Error as e:
        print(f"[EROARE DB] db_sterge_user: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


# =============================================================================
#  FUNCTII SERVO
# =============================================================================

def trimite_puls(puls_ms: float, timp_stabilizare: float = 0.7):
    # Trimite un semnal PWM la servo pentru a-l muta la pozitia dorita
    if not GPIO_AVAILABLE:
        print(f"[SIMULARE] Puls servo: {puls_ms:.3f} ms")
        return
    s = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)
    valoare = ((puls_ms / 1000) - PULSE_MIN) / (PULSE_MAX - PULSE_MIN) * 2 - 1
    s.value = max(-1.0, min(1.0, valoare))
    time.sleep(timp_stabilizare)
    s.detach()


def usa_deschisa(puls_ms: float) -> float:
    # Roteste servo la pozitia de usa deschisa (+90 grade)
    interval = (PULSE_MAX - PULSE_MIN) * 1000
    nou = min(puls_ms + interval / 2, PULSE_MAX * 1000)
    print(f"[SERVO] Usa DESCHISA ({puls_ms:.3f} -> {nou:.3f} ms)")
    trimite_puls(nou)
    return nou


def usa_inchisa(puls_ms: float) -> float:
    # Roteste servo la pozitia de usa inchisa (-90 grade)
    interval = (PULSE_MAX - PULSE_MIN) * 1000
    nou = max(puls_ms - interval / 2, PULSE_MIN * 1000)
    print(f"[SERVO] Usa INCHISA ({puls_ms:.3f} -> {nou:.3f} ms)")
    trimite_puls(nou)
    return nou


def roteste(viteza, durata):
    # Roteste servo cu o anumita viteza pentru o durata data
    if not GPIO_AVAILABLE:
        print(f"[SIMULARE] Rotire viteza={viteza}, durata={durata}s")
        return
    s = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)
    s.value = viteza
    time.sleep(durata)
    s.value = 0
    s.detach()
    time.sleep(0.5)


def calibrare_servo():
    # Misca servo mic la stanga si dreapta pentru calibrare initiala
    print("Calibrare servo...")
    roteste(0.4, 0.2)
    time.sleep(1)
    roteste(-0.4, 0.15)
    time.sleep(1)
    print("Calibrare finalizata!")


def test_servo():
    # Testeaza servo: stanga, mijloc, dreapta
    if not GPIO_AVAILABLE:
        print("[SIMULARE] Test servo: stanga -> mijloc -> dreapta")
        return
    servo = Servo(SERVO_PIN, min_pulse_width=PULSE_MIN, max_pulse_width=PULSE_MAX)
    for pozitie, eticheta in [(servo.min, "Stanga"), (servo.mid, "Mijloc"), (servo.max, "Dreapta")]:
        pozitie()
        print(eticheta)
        time.sleep(1)
    servo.detach()


# =============================================================================
#  CAPTURA FETE (mod linie de comanda)
# =============================================================================

def captureaza_fete(nume: str):
    # Porneste camera si captureaza 20 de imagini ale fetei persoanei date
    cap = cv2.VideoCapture(0)
    cap.set(3, 640)
    cap.set(4, 480)
    detector = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    count, max_imagini = 0, 20
    ultima_salvare = time.time()
    print(f"[INFO] Captureaza fete pentru '{nume}'...")

    while count < max_imagini:
        ret, frame = cap.read()
        if not ret:
            break
        gri  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        fete = detector.detectMultiScale(gri, 1.3, 5)

        for (x, y, w, h) in fete:
            if time.time() - ultima_salvare < 0.4:
                continue
            # Decupeaza fata si calculeaza encodarea
            imagine_fata = frame[y:y+h, x:x+w]
            cv2.imwrite(os.path.join(SAVE_DIR, f"{nume}_{count}.jpeg"), imagine_fata)
            rgb_fata = cv2.cvtColor(imagine_fata, cv2.COLOR_BGR2RGB)
            encodari = face_recognition.face_encodings(rgb_fata)
            if encodari:
                db_salveaza_fata(nume, encodari[0])
            count         += 1
            ultima_salvare = time.time()
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
            print(f"  [{count}/{max_imagini}] Capturata.")

        cv2.putText(frame, f"Capturate: {count}/{max_imagini}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        cv2.imshow("Captureaza fete - Q pentru iesire", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"[INFO] Finalizat. {count} imagine(i) salvata(e).")


# =============================================================================
#  TEST RECUNOASTERE FACIALA
# =============================================================================

def test_recunoastere():
    # Porneste camera si afiseaza in timp real cine este recunoscut
    encodari_cunoscute, nume_cunoscute = db_incarca_fete()
    if not encodari_cunoscute:
        print("[EROARE] Nu exista fete in baza de date!")
        return

    cap = cv2.VideoCapture(0)
    contor_cadre = 0
    locatii_fete, nume_fete = [], []

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        mic     = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
        rgb_mic = cv2.cvtColor(mic, cv2.COLOR_BGR2RGB)

        # Analizeaza doar un cadru din FRAME_SKIP pentru performanta
        if contor_cadre % FRAME_SKIP == 0:
            locatii_fete = face_recognition.face_locations(rgb_mic, model="hog")
            encodari     = face_recognition.face_encodings(rgb_mic, locatii_fete)
            nume_fete    = []
            for enc in encodari:
                distante = face_recognition.face_distance(encodari_cunoscute, enc)
                nume = "Necunoscut"
                if len(distante):
                    cel_mai_bun = np.argmin(distante)
                    if distante[cel_mai_bun] <= TOLERANCE:
                        nume = nume_cunoscute[cel_mai_bun]
                nume_fete.append(nume)

        contor_cadre += 1

        # Deseneaza dreptunghi si nume pe fiecare fata detectata
        for (sus, dr, jos, st), nume in zip(locatii_fete, nume_fete):
            sus, dr, jos, st = sus*4, dr*4, jos*4, st*4
            culoare = (0, 255, 0) if nume != "Necunoscut" else (0, 0, 255)
            simbol  = "OK" if nume != "Necunoscut" else "X"
            cv2.rectangle(frame, (st, sus), (dr, jos), culoare, 2)
            cv2.putText(frame, f"{simbol} {nume}", (st, sus - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, culoare, 2)

        cv2.imshow("Test Recunoastere - Q pentru iesire", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


# =============================================================================
#  BLOCARE USA
# =============================================================================

def blocare_usa(nume_verificat: str = None):
    """
    Mod GUI : primeste nume_verificat (PIN deja verificat), merge direct la scanare.
    Mod CLI : cere numele si PIN-ul singur, apoi scaneaza.
    """
    if nume_verificat:
        nume = nume_verificat
    else:
        # Mod linie de comanda: cere date manual
        nume = input("Introduceti numele: ").strip()
        pin  = input("Introduceti PIN-ul: ").strip()
        if not db_verifica_pin(nume, pin):
            print("[ACCES REFUZAT] Nume sau PIN incorect.")
            db_log_acces(nume, "DENIED")
            return

    # Incarca encodarile faciale ale utilizatorului din DB
    encodari_cunoscute, nume_cunoscute = db_incarca_fete(nume)
    if not encodari_cunoscute:
        print(f"[EROARE] Nu exista date faciale pentru '{nume}'. Inregistrati-va mai intai.")
        return

    print(f"[INFO] PIN verificat pentru '{nume}'. Pornesc scanarea fetei...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[EROARE] Nu pot deschide camera!")
        return

    contor_cadre    = 0
    locatii_fete    = []
    nume_fete       = []
    usa_deschisa_f  = False   # starea usii: True = deschisa
    timp_deschidere = None
    puls_curent     = None
    PULS_START_MS   = (PULSE_MIN + PULSE_MAX) / 2 * 1000  # pozitia initiala servo

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            mic     = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
            rgb_mic = cv2.cvtColor(mic, cv2.COLOR_BGR2RGB)

            if contor_cadre % FRAME_SKIP == 0:
                locatii_fete = face_recognition.face_locations(rgb_mic, model="hog")
                encodari     = face_recognition.face_encodings(rgb_mic, locatii_fete)
                nume_fete    = []
                for enc in encodari:
                    distante = face_recognition.face_distance(encodari_cunoscute, enc)
                    det_nume = "Necunoscut"
                    if len(distante):
                        cel_mai_bun = np.argmin(distante)
                        if distante[cel_mai_bun] <= TOLERANCE:
                            det_nume = nume_cunoscute[cel_mai_bun]
                    nume_fete.append(det_nume)

                # Logeaza incercari esuate cat timp usa e inchisa
                for det_nume in nume_fete:
                    if det_nume != nume and not usa_deschisa_f:
                        db_log_acces(nume, "DENIED")

            contor_cadre += 1

            # Daca fata corespunde utilizatorului autentificat, deschide usa
            if any(n == nume for n in nume_fete) and not usa_deschisa_f:
                print(f"[ACCES ACORDAT] {nume}")
                db_log_acces(nume, "GRANTED")
                puls_curent     = usa_deschisa(PULS_START_MS)
                usa_deschisa_f  = True
                timp_deschidere = time.time()

            # Afiseaza cronometrul de inchidere
            if usa_deschisa_f:
                scurs = time.time() - timp_deschidere
                ramas = max(0, int(DOOR_OPEN_DELAY - scurs))
                cv2.putText(frame, f"Usa se inchide in: {ramas}s",
                            (10, frame.shape[0] - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
                if scurs >= DOOR_OPEN_DELAY:
                    print("[INFO] Timp expirat - inchid usa.")
                    usa_inchisa(puls_curent)
                    break

            cv2.putText(frame, f"Scanez pentru: {nume}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 255), 2)

            # Deseneaza rezultatele recunoasterii pe ecran
            for (sus, dr, jos, st), det_nume in zip(locatii_fete, nume_fete):
                sus, dr, jos, st = sus*4, dr*4, jos*4, st*4
                culoare = (0, 255, 0) if det_nume == nume else (0, 0, 255)
                simbol  = "OK" if det_nume == nume else "X"
                cv2.rectangle(frame, (st, sus), (dr, jos), culoare, 2)
                cv2.putText(frame, f"{simbol} {det_nume}", (st, sus - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, culoare, 2)

            cv2.imshow("Blocare Usa - Q pentru iesire", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                if usa_deschisa_f and puls_curent:
                    usa_inchisa(puls_curent)
                break

    except KeyboardInterrupt:
        if usa_deschisa_f and puls_curent:
            usa_inchisa(puls_curent)
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("[INFO] Program oprit.")


# =============================================================================
#  INTERFATA GRAFICA (GUI)
# =============================================================================

class AplicatieFata:
    def __init__(self, root):
        self.root = root
        self.root.title("Sistem Control Acces")
        self.root.geometry("420x540")
        self.root.configure(bg="#1a1a2e")

        # Titlu principal
        tk.Label(root, text="Sistem Control Acces",
                 font=("Arial", 17, "bold"),
                 bg="#1a1a2e", fg="#e0e0e0", pady=20).pack()

        stil_btn = dict(width=28, height=2, relief="flat", font=("Arial", 11))

        # Buton inregistrare persoana noua
        tk.Button(root, text="Inregistrare Persoana Noua",
                  command=self._flux_inregistrare,
                  bg="#16213e", fg="#a8dadc", **stil_btn).pack(pady=8)

        # Buton deschidere usa prin recunoastere
        tk.Button(root, text="Deschide Usa",
                  command=self._flux_usa,
                  bg="#16213e", fg="#a8ff78", **stil_btn).pack(pady=8)

        # Buton test recunoastere faciala
        tk.Button(root, text="Testeaza Recunoasterea",
                  command=test_recunoastere,
                  bg="#16213e", fg="#f9c74f", **stil_btn).pack(pady=8)

        # Buton stergere persoana
        tk.Button(root, text="Sterge Persoana",
                  command=self._flux_stergere,
                  bg="#16213e", fg="#ef233c", **stil_btn).pack(pady=8)

        # Buton login administrator
        tk.Button(root, text="Panou Administrator",
                  command=self._flux_admin,
                  bg="#16213e", fg="#c77dff", **stil_btn).pack(pady=8)

        # Informatii conexiune baza de date
        tk.Label(root, text="DB: TiDB Cloud  |  facial_recognition",
                 font=("Arial", 8), bg="#1a1a2e", fg="#555577").pack(side="bottom", pady=6)

    # ── Flux inregistrare: nume -> PIN -> confirmare PIN -> captura fata ──

    def _flux_inregistrare(self):
        # Cere numele persoanei
        nume = simpledialog.askstring("Inregistrare", "Introduceti numele persoanei:", parent=self.root)
        if not nume:
            return
        nume = nume.strip()

        # Verifica daca exista deja in baza de date
        if db_user_exista(nume):
            messagebox.showerror("Eroare", f"'{nume}' este deja inregistrat!", parent=self.root)
            return

        # Cere PIN-ul dorit
        pin = simpledialog.askstring("PIN", f"Alege un PIN pentru '{nume}' (minim 4 cifre):",
                                     show='*', parent=self.root)
        if not pin or not pin.strip():
            return
        pin = pin.strip()

        # Valideaza PIN-ul: doar cifre, minim 4
        if not pin.isdigit() or len(pin) < 4:
            messagebox.showerror("Eroare", "PIN-ul trebuie sa contina doar cifre (minim 4)!", parent=self.root)
            return

        # Confirmare PIN - trebuie sa coincida
        pin2 = simpledialog.askstring("PIN", "Confirma PIN-ul:", show='*', parent=self.root)
        if pin2 != pin:
            messagebox.showerror("Eroare", "PIN-urile nu coincid!", parent=self.root)
            return

        # Salveaza utilizatorul in baza de date
        if not db_inregistrare_user(nume, pin):
            messagebox.showerror("Eroare", "Nu s-a putut salva utilizatorul in DB.", parent=self.root)
            return

        messagebox.showinfo("Succes",
                            f"Utilizatorul '{nume}' a fost inregistrat!\nAcum incepe captura fetei.",
                            parent=self.root)

        # Porneste captura fetei dupa 100ms (permite ferestrei sa se inchida)
        self.root.after(100, lambda: self._captureaza_fete(nume))

    def _captureaza_fete(self, nume: str):
        # Deschide camera si captureaza pana la 20 de imagini ale fetei
        cap      = cv2.VideoCapture(0)
        detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

        count, max_imagini = 0, 20
        ultima_salvare = time.time()

        while count < max_imagini:
            ret, frame = cap.read()
            if not ret:
                break
            gri  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            fete = detector.detectMultiScale(gri, 1.3, 5)

            for (x, y, w, h) in fete:
                if time.time() - ultima_salvare < 0.4:
                    continue
                # Calculeaza encodarea si o salveaza in DB
                imagine_fata = frame[y:y+h, x:x+w]
                rgb_fata     = cv2.cvtColor(imagine_fata, cv2.COLOR_BGR2RGB)
                encodari     = face_recognition.face_encodings(rgb_fata)
                if encodari:
                    db_salveaza_fata(nume, encodari[0])
                    cv2.imwrite(os.path.join(SAVE_DIR, f"{nume}_{count}.jpeg"), imagine_fata)
                    count         += 1
                    ultima_salvare = time.time()
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)

            cv2.putText(frame, f"Capturate: {count}/{max_imagini}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.imshow("Inregistrare Fata - Q pentru oprire", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()
        messagebox.showinfo("Finalizat",
                            f"S-au inregistrat {count} imagine(i) pentru '{nume}'.",
                            parent=self.root)

    # ── Flux deschidere usa: nume -> PIN -> scanare faciala ──────────

    def _flux_usa(self):
        # Cere numele utilizatorului
        nume = simpledialog.askstring("Acces", "Introduceti numele:", parent=self.root)
        if not nume:
            return
        nume = nume.strip()

        # Cere PIN-ul
        pin = simpledialog.askstring("Acces", f"Introduceti PIN-ul pentru '{nume}':",
                                     show='*', parent=self.root)
        if not pin:
            return

        # Verifica PIN-ul in baza de date
        if not db_verifica_pin(nume, pin):
            messagebox.showerror("Acces Refuzat", "Nume sau PIN incorect!", parent=self.root)
            db_log_acces(nume, "DENIED")
            return

        # PIN corect - porneste scanarea faciala
        self.root.after(100, lambda: blocare_usa(nume_verificat=nume))

    # ── Flux stergere: nume -> PIN -> confirmare -> stergere ─────────

    def _flux_stergere(self):
        # Cere numele persoanei de sters
        nume = simpledialog.askstring("Stergere", "Introduceti numele de sters:", parent=self.root)
        if not nume:
            return
        nume = nume.strip()

        # Cere PIN-ul pentru confirmare identitate
        pin = simpledialog.askstring("Stergere",
                                     f"Introduceti PIN-ul pentru '{nume}' pentru confirmare:",
                                     show='*', parent=self.root)
        if not pin:
            return

        # Verifica PIN-ul inainte de stergere
        if not db_verifica_pin(nume, pin):
            messagebox.showerror("Eroare", "Nume sau PIN incorect!", parent=self.root)
            return

        # Sterge utilizatorul si encodarile din DB
        if db_sterge_user(nume):
            messagebox.showinfo("Sters",
                                f"'{nume}' a fost eliminat din baza de date.",
                                parent=self.root)
        else:
            messagebox.showerror("Eroare",
                                 "Nu s-a putut sterge - verifica conexiunea la DB.",
                                 parent=self.root)


    # ── Flux administrator: username -> parola -> panou admin ───────

    def _flux_admin(self):
        # Cere numele de utilizator al adminului
        username = simpledialog.askstring("Admin", "Nume utilizator admin:", parent=self.root)
        if not username:
            return

        # Cere parola adminului (mascata)
        parola = simpledialog.askstring("Admin", "Parola:", show='*', parent=self.root)
        if not parola:
            return

        # Verifica credentialele in baza de date
        if not db_verifica_admin(username.strip(), parola.strip()):
            messagebox.showerror("Acces Refuzat", "Credentiale incorecte!", parent=self.root)
            return

        # Deschide fereastra de administrare
        FereastraAdmin(self.root)


# =============================================================================
#  FEREASTRA ADMINISTRATOR
# =============================================================================

class FereastraAdmin:
    def __init__(self, parinte):
        # Creaza o fereastra separata pentru panoul de administrare
        self.win = tk.Toplevel(parinte)
        self.win.title("Panou Administrator")
        self.win.geometry("680x620")
        self.win.configure(bg="#0d0d1a")
        self.win.grab_set()   # blocheaza fereastra principala cat timp adminul e deschis

        tk.Label(self.win, text="Panou Administrator",
                 font=("Arial", 16, "bold"),
                 bg="#0d0d1a", fg="#c77dff", pady=15).pack()

        # ── Sectiunea statistici ──────────────────────────────────────
        cadru_stat = tk.LabelFrame(self.win, text="  Statistici Generale  ",
                                   bg="#0d0d1a", fg="#aaaacc",
                                   font=("Arial", 10, "bold"), padx=10, pady=8)
        cadru_stat.pack(fill="x", padx=20, pady=(0, 10))

        self.label_stat = tk.Label(cadru_stat, text="Se incarca...",
                                   bg="#0d0d1a", fg="#e0e0e0",
                                   font=("Courier", 10), justify="left")
        self.label_stat.pack(anchor="w")
        self._actualizeaza_statistici()

        # ── Sectiunea utilizatori + actiuni ──────────────────────────
        cadru_useri = tk.LabelFrame(self.win, text="  Utilizatori Inregistrati  ",
                                    bg="#0d0d1a", fg="#aaaacc",
                                    font=("Arial", 10, "bold"), padx=10, pady=8)
        cadru_useri.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        # Lista cu scrollbar
        scroll = tk.Scrollbar(cadru_useri)
        scroll.pack(side="right", fill="y")

        self.lista_useri = tk.Listbox(cadru_useri, yscrollcommand=scroll.set,
                                      bg="#111133", fg="#e0e0e0",
                                      font=("Courier", 10), selectbackground="#c77dff",
                                      height=10)
        self.lista_useri.pack(fill="both", expand=True)
        scroll.config(command=self.lista_useri.yview)
        self._actualizeaza_lista_useri()

        # Butoane actiuni pe utilizatori
        cadru_btn = tk.Frame(self.win, bg="#0d0d1a")
        cadru_btn.pack(pady=8)

        stil = dict(width=20, height=1, relief="flat", font=("Arial", 10))

        tk.Button(cadru_btn, text="Reseteaza PIN",
                  command=self._reseteaza_pin,
                  bg="#1a1a3e", fg="#a8dadc", **stil).grid(row=0, column=0, padx=8, pady=4)

        tk.Button(cadru_btn, text="Sterge Utilizator",
                  command=self._sterge_user,
                  bg="#1a1a3e", fg="#ef233c", **stil).grid(row=0, column=1, padx=8, pady=4)

        tk.Button(cadru_btn, text="Vezi Loguri Acces",
                  command=self._vezi_loguri,
                  bg="#1a1a3e", fg="#f9c74f", **stil).grid(row=0, column=2, padx=8, pady=4)

        # Buton inchidere panou
        tk.Button(self.win, text="Inchide Panoul",
                  command=self.win.destroy,
                  bg="#2a0a0a", fg="#ff6b6b",
                  width=20, height=1, relief="flat", font=("Arial", 10)).pack(pady=8)

    def _actualizeaza_statistici(self):
        # Interogheaza DB si afiseaza statisticile generale
        stat = db_statistici()
        if stat:
            text = (
                f"  Utilizatori inregistrati : {stat['total_useri']}\n"
                f"  Total accese             : {stat['total_accese']}\n"
                f"  Accese permise (GRANTED) : {stat['total_granted']}\n"
                f"  Accese refuzate (DENIED) : {stat['total_denied']}"
            )
        else:
            text = "  Nu s-au putut incarca statisticile."
        self.label_stat.config(text=text)

    def _actualizeaza_lista_useri(self):
        # Goleste si reincarca lista utilizatorilor cu ultima accesare
        self.lista_useri.delete(0, tk.END)
        useri = db_lista_utilizatori()
        for (nume, creat_la) in useri:
            # Cauta ultimul acces pentru fiecare utilizator
            ultim = db_ultima_accesare(nume)
            if ultim:
                status_str = "GRANTED" if ultim[0] == "GRANTED" else "DENIED"
                data_str   = str(ultim[1])[:16]
                linie = f"  {nume:<20} | Ultim acces: {data_str} [{status_str}]"
            else:
                linie = f"  {nume:<20} | Nicio accesare inregistrata"
            self.lista_useri.insert(tk.END, linie)

    def _get_nume_selectat(self) -> str:
        # Extrage numele utilizatorului selectat din lista
        selectie = self.lista_useri.curselection()
        if not selectie:
            messagebox.showwarning("Atentie", "Selectati un utilizator din lista!", parent=self.win)
            return ""
        linie = self.lista_useri.get(selectie[0])
        # Numele este primul camp din linie (inainte de '|')
        return linie.split("|")[0].strip()

    def _reseteaza_pin(self):
        # Permite adminului sa seteze un PIN nou pentru utilizatorul selectat
        nume = self._get_nume_selectat()
        if not nume:
            return

        # Cere PIN-ul nou
        pin_nou = simpledialog.askstring("Resetare PIN",
                                         f"PIN nou pentru '{nume}' (minim 4 cifre):",
                                         show='*', parent=self.win)
        if not pin_nou or not pin_nou.strip():
            return
        pin_nou = pin_nou.strip()

        # Valideaza PIN-ul nou
        if not pin_nou.isdigit() or len(pin_nou) < 4:
            messagebox.showerror("Eroare", "PIN-ul trebuie sa contina doar cifre (minim 4)!",
                                 parent=self.win)
            return

        # Confirmare PIN nou
        confirmare = simpledialog.askstring("Resetare PIN", "Confirma PIN-ul nou:",
                                            show='*', parent=self.win)
        if confirmare != pin_nou:
            messagebox.showerror("Eroare", "PIN-urile nu coincid!", parent=self.win)
            return

        # Salveaza PIN-ul nou in baza de date
        if db_reseteaza_pin(nume, pin_nou):
            messagebox.showinfo("Succes", f"PIN-ul pentru '{nume}' a fost resetat.", parent=self.win)
        else:
            messagebox.showerror("Eroare", "Nu s-a putut reseta PIN-ul.", parent=self.win)

    def _sterge_user(self):
        # Permite adminului sa stearga orice utilizator fara a-i cunoaste PIN-ul
        nume = self._get_nume_selectat()
        if not nume:
            return

        # Confirmare inainte de stergere
        confirmat = messagebox.askyesno(
            "Confirmare Stergere",
            f"Esti sigur ca vrei sa stergi utilizatorul '{nume}'?\n"
            f"Aceasta actiune este ireversibila!",
            parent=self.win
        )
        if not confirmat:
            return

        # Sterge utilizatorul si encodarile din DB
        if db_sterge_user(nume):
            messagebox.showinfo("Sters", f"'{nume}' a fost eliminat din baza de date.",
                                parent=self.win)
            # Reincarca lista si statisticile dupa stergere
            self._actualizeaza_lista_useri()
            self._actualizeaza_statistici()
        else:
            messagebox.showerror("Eroare", "Nu s-a putut sterge utilizatorul.", parent=self.win)

    def _vezi_loguri(self):
        # Deschide o fereastra separata cu ultimele 50 de loguri de acces
        loguri = db_loguri_acces(limit=50)

        win_log = tk.Toplevel(self.win)
        win_log.title("Loguri Acces - Ultimele 50")
        win_log.geometry("600x450")
        win_log.configure(bg="#0d0d1a")

        tk.Label(win_log, text="Loguri Acces (cele mai recente)",
                 font=("Arial", 13, "bold"),
                 bg="#0d0d1a", fg="#f9c74f", pady=10).pack()

        # Text area cu scrollbar pentru loguri
        cadru = tk.Frame(win_log, bg="#0d0d1a")
        cadru.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        scroll_log = tk.Scrollbar(cadru)
        scroll_log.pack(side="right", fill="y")

        text_log = tk.Text(cadru, yscrollcommand=scroll_log.set,
                           bg="#111133", fg="#e0e0e0",
                           font=("Courier", 10), state="normal")
        text_log.pack(fill="both", expand=True)
        scroll_log.config(command=text_log.yview)

        if loguri:
            # Scrie antetul coloanelor
            text_log.insert(tk.END, f"{'Nume':<22} {'Status':<10} {'Data si Ora'}\n")
            text_log.insert(tk.END, "-" * 55 + "\n")
            for (persoana, status, data) in loguri:
                # Coloreaza GRANTED cu verde si DENIED cu rosu
                culoare_tag = "granted" if status == "GRANTED" else "denied"
                linie = f"{persoana:<22} {status:<10} {str(data)[:19]}\n"
                text_log.insert(tk.END, linie, culoare_tag)
        else:
            text_log.insert(tk.END, "Nu exista loguri de acces.")

        # Defineste culorile pentru taguri
        text_log.tag_config("granted", foreground="#a8ff78")
        text_log.tag_config("denied",  foreground="#ef233c")

        # Blocheaza editarea textului
        text_log.config(state="disabled")

        tk.Button(win_log, text="Inchide", command=win_log.destroy,
                  bg="#2a0a0a", fg="#ff6b6b",
                  width=15, relief="flat", font=("Arial", 10)).pack(pady=8)


# =============================================================================
#  PUNCT DE INTRARE
# =============================================================================

if __name__ == "__main__":
    import sys

    # Initializeaza tabelele la pornire (sigur cu IF NOT EXISTS)
    initializare_db()

    if len(sys.argv) > 1:
        mod = sys.argv[1]
        if mod == "calibrare":
            calibrare_servo()
        elif mod == "captureaza" and len(sys.argv) > 2:
            captureaza_fete(sys.argv[2])
        elif mod == "captureaza":
            print("Utilizare: python face_system.py captureaza <nume>")
        elif mod == "test":
            test_recunoastere()
        elif mod == "usa":
            blocare_usa()
        elif mod == "test_servo":
            test_servo()
        else:
            print("Moduri disponibile: calibrare | captureaza <nume> | test | usa | test_servo")
    else:
        # Lanseaza interfata grafica
        root = tk.Tk()
        app  = AplicatieFata(root)
        root.mainloop()