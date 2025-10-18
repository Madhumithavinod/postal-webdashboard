from flask import Flask, render_template, request, Response
import easyocr
import cv2
from textblob import TextBlob
import numpy as np
import requests
import firebase_admin
from firebase_admin import credentials, db
import time
import re  # ✅ for 6-digit number detection

app = Flask(__name__)

# ---------------- FIREBASE INITIALIZATION ----------------
cred = credentials.Certificate("pass.json")  # same folder
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://textdetection-d1e46-default-rtdb.firebaseio.com/'
})
# 🧹 Clear old data at server start
try:
    db.reference("detected_numbers").delete()
    print("🧹 Firebase cleared — starting fresh session.")
except Exception as e:
    print(f"⚠️ Could not clear Firebase: {e}")

# ---------------- EASYOCR INITIALIZATION ----------------
reader = easyocr.Reader(['en'])

camera_on = False
IP_STREAM_URL = "http://192.168.227.234:8084/?action=stream"


# ---------------- AUTOCORRECT FUNCTION ----------------
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


# ---------------- STORE TO FIREBASE ----------------
def store_to_firebase(number_list):
    """Update Firebase with counts of detected pincodes."""
    try:
        ref = db.reference("detected_numbers")
        current_data = ref.get() or {}

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        for pin in number_list:
            if pin in current_data:
                current_data[pin]["count"] += 1
                current_data[pin]["last_detected"] = timestamp
            else:
                current_data[pin] = {"count": 1, "last_detected": timestamp}

        ref.set(current_data)
        print(f"✅ Firebase updated with counts: {number_list}")

    except Exception as e:
        print(f"❌ Firebase update failed: {e}")

# ---------------- OCR FUNCTION ----------------
def perform_ocr(frame):
    result = reader.readtext(frame)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1.5
    thickness = 3

    six_digit_numbers = []

    for detection in result:
        top_left = tuple(map(int, detection[0][0]))
        bottom_right = tuple(map(int, detection[0][2]))
        text = detection[1]

        corrected_text = autocorrect_sentence(text)

        # ✅ Clean up unwanted characters (dots, commas, etc.)
        cleaned_text = re.sub(r'[^0-9\s]', '', corrected_text)

        # ✅ Match patterns like "600041", "600 041", "641 602"
        matches = re.findall(r"\b\d{3}\s?\d{3}\b", cleaned_text)

        for m in matches:
            clean_number = m.replace(" ", "")
            if len(clean_number) == 6:
                six_digit_numbers.append(clean_number)
                print(f"📮 Found PIN-like number: {clean_number}")

        # Draw bounding box and text
        cv2.rectangle(frame, top_left, bottom_right, (0, 255, 0), 2)
        cv2.putText(frame, corrected_text, (top_left[0], top_left[1] - 10),
                    font, font_scale, (0, 255, 0), thickness, cv2.LINE_AA)

    # ✅ Upload only if valid PIN numbers found
    if six_digit_numbers:
        store_to_firebase(six_digit_numbers)

    return frame


# ---------------- CAMERA STREAM ----------------
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

        # Run OCR every 5 frames
        if frame_count % 5 == 0:
            last_frame = perform_ocr(small_frame)

        display_frame = last_frame if last_frame is not None else small_frame

        ret, buffer = cv2.imencode('.jpg', display_frame)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    cap.release()


# ---------------- ROUTES ----------------
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

@app.route('/report')
def report():
    try:
        ref = db.reference("detected_numbers")
        data = ref.get() or {}

        # ✅ Custom mapping: known pincodes and regions
        region_map = {
            "600071": "Chennai",
            "600041": "Thiruvanmiyur",
            "600072": "Chennai",
            "641602": "Coimbatore",
            "560001": "Bangalore",
            # ➕ add more here as needed
        }

        report_data = []

        for pin, info in data.items():
            # Default to "Unknown" unless found or fetched
            region_name = region_map.get(pin, "Unknown")

            # Only call API if not in custom list
            if region_name == "Unknown":
                try:
                    api_url = f"https://api.postalpincode.in/pincode/{pin}"
                    response = requests.get(api_url, timeout=3).json()
                    if response[0]["Status"] == "Success":
                        region_name = response[0]["PostOffice"][0]["District"]
                except Exception:
                    pass

            report_data.append({
                "pincode": pin,
                "region": region_name,
                "count": info.get("count", 0),
                "last_detected": info.get("last_detected", "")
            })

        # Sort by count (descending)
        report_data.sort(key=lambda x: x["count"], reverse=True)

        return render_template("report.html", data=report_data)

    except Exception as e:
        return f"<h3>Error loading report: {e}</h3>"


@app.route('/stop_camera', methods=['POST'])
def stop_camera():
    global camera_on
    camera_on = False
    return ('', 204)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
