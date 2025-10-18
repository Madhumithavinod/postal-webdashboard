from flask import Flask, render_template, request, Response
import easyocr
import cv2
from textblob import TextBlob
import numpy as np
import requests

app = Flask(__name__)

# Initialize EasyOCR
reader = easyocr.Reader(['en'])

# Global variable to control camera
camera_on = False

# Your IP camera stream URL
IP_STREAM_URL = "http://10.164.179.234:8084/?action=stream"

# Autocorrect function
def autocorrect_sentence(sentence):
    sentence = sentence.strip()
    if len(sentence) <= 1 or sentence.isnumeric():
        return sentence
    try:
        blob = TextBlob(sentence)
        corrected = str(blob.correct())
        return corrected
    except:
        return sentence

# OCR on a frame
def perform_ocr(frame):
    result = reader.readtext(frame)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1.5
    thickness = 3

    for detection in result:
        top_left = tuple(map(int, detection[0][0]))
        bottom_right = tuple(map(int, detection[0][2]))
        text = detection[1]

        corrected_text = autocorrect_sentence(text)

        cv2.rectangle(frame, top_left, bottom_right, (0, 255, 0), 2)
        cv2.putText(frame, corrected_text, (top_left[0], top_left[1] - 10),
                    font, font_scale, (0, 255, 0), thickness, cv2.LINE_AA)
    return frame

# Camera generator for streaming
def gen_frames():
    global camera_on
    cap = cv2.VideoCapture(IP_STREAM_URL)
    frame_count = 0
    last_frame = None

    if not cap.isOpened():
        print("Error: Could not open IP camera stream.")
        return

    while camera_on:
        success, frame = cap.read()
        if not success:
            print("Error: Failed to read frame from IP stream.")
            break

        frame_count += 1

        # Resize for faster processing
        small_frame = cv2.resize(frame, (640, 480))

        # Run OCR only every 5 frames
        if frame_count % 5 == 0:
            last_frame = perform_ocr(small_frame)

        # If OCR not run this frame, use last processed frame
        display_frame = last_frame if last_frame is not None else small_frame

        ret, buffer = cv2.imencode('.jpg', display_frame)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    cap.release()

# Routes
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        option = request.form.get('option')
        if option == 'file':
            file = request.files['file']
            if file and file.filename != '':
                file_bytes = np.frombuffer(file.read(), np.uint8)
                frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
                frame = perform_ocr(frame)
                _, buffer = cv2.imencode('.jpg', frame)
                return Response(buffer.tobytes(), mimetype='image/jpeg')
        elif option == 'camera':
            global camera_on
            camera_on = True
            return Response(gen_frames(),
                            mimetype='multipart/x-mixed-replace; boundary=frame')
    return render_template('index.html')

@app.route('/stop_camera', methods=['POST'])
def stop_camera():
    global camera_on
    camera_on = False
    return ('', 204)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
