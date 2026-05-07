import face_recognition
import cv2
import os
import numpy as np

# --- 1. Încarcă fețele cunoscute ---
known_faces_dir = "known_faces"
known_encodings = []
known_names = []

for filename in os.listdir(known_faces_dir):
    if filename.endswith((".jpeg", ".jpg", ".png")):
        path = os.path.join(known_faces_dir, filename)
        image = face_recognition.load_image_file(path)
        encodings = face_recognition.face_encodings(image, num_jitters = 5)
        if encodings:
            known_encodings.append(encodings[0])
            known_names.append(os.path.splitext(filename)[0])

# --- 2. Pornește camera ---
video_capture = cv2.VideoCapture(0)
frame_counter = 0
face_locations = []
face_names = []

while True:
    ret, frame = video_capture.read()
    if not ret:
        break

    # Redimensionare pentru viteză
    small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
    rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

    # --- 3. Procesare la fiecare 5 frame-uri ---
    if frame_counter % 5 == 0:
        face_locations = face_recognition.face_locations(rgb_small_frame, model="cnn")
        face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)

        face_names = []
        for face_encoding in face_encodings:
            matches = face_recognition.compare_faces(known_encodings, face_encoding, tolerance=0.3)
            name = "Unknown"

            face_distances = face_recognition.face_distance(known_encodings, face_encoding)
            if len(face_distances) > 0:
                best_match_index = np.argmin(face_distances)
                if matches[best_match_index]:
                    name = known_names[best_match_index]
            face_names.append(name)

    frame_counter += 1

    # --- 4. Desenează pe ecran ---
    for (top, right, bottom, left), name in zip(face_locations, face_names):
        top *= 4
        right *= 4
        bottom *= 4
        left *= 4

        if name != "Unknown":
            color = (0, 255, 0)
            symbol = "✔️"
        else:
            color = (0, 0, 255)
            symbol = "❌"

        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.putText(frame, f"{symbol} {name}", (left, top-10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    cv2.imshow("Face Recognition", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

video_capture.release()
cv2.destroyAllWindows()
