import cv2
import os
import time

name = input("Introdu numele persoanei: ")

save_dir = "known_faces"
os.makedirs(save_dir, exist_ok=True)

cap = cv2.VideoCapture(0)

cap.set(3, 640)
cap.set(4, 480)

face_detector = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

count = 0
max_images = 20

print("Uită-te la cameră. Se vor face poze automat...")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face_detector.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:

        face_img = frame[y:y+h, x:x+w]

        filename = f"{name}_{count}.jpeg"
        path = os.path.join(save_dir, filename)

        cv2.imwrite(path, face_img)

        count += 1

        cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)

        print("Salvat:", filename)

        time.sleep(0.3)

    cv2.imshow("Capture Faces", frame)

    if count >= max_images:
        break

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()

print("Captura terminată!")