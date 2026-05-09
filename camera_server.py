"""
camera_server.py
Capture la webcam Logitech et diffuse le flux en MJPEG sur HTTP port 5000.
Le client C# n'a qu'à lire http://<ip_raspberry>:5000/stream
"""

import cv2
import threading
import time
import logging
from flask import Flask, Response, jsonify

log = logging.getLogger("camera_server")
app = Flask(__name__)

# ── État global de la caméra ────────────────────────────────────────────────
_frame_lock  = threading.Lock()
_latest_frame: bytes | None = None
_camera_ok   = False
_fps_counter = 0
_fps_actual  = 0.0


def _capture_loop(device_index: int = 0):
    """Thread de capture : lit la caméra et encode en JPEG en continu."""
    global _latest_frame, _camera_ok, _fps_counter, _fps_actual

    cap = cv2.VideoCapture(device_index)
    if not cap.isOpened():
        log.warning(f"Caméra {device_index} non disponible — mode fallback activé")
        _camera_ok = False
        _run_fallback_loop()
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    _camera_ok = True
    log.info(f"Caméra {device_index} ouverte — 640x480 @ 30fps")

    last_fps_t = time.time()
    frame_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            log.warning("Perte de trame caméra")
            time.sleep(0.05)
            continue

        # Overlays informatifs sur l'image
        _draw_overlay(frame)

        ok, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            with _frame_lock:
                _latest_frame = buffer.tobytes()
            frame_count += 1

        # Calcul FPS réel
        now = time.time()
        if now - last_fps_t >= 1.0:
            _fps_actual  = frame_count / (now - last_fps_t)
            frame_count  = 0
            last_fps_t   = now

        time.sleep(0.033)   # ~30 fps max

    cap.release()


def _run_fallback_loop():
    """
    Si pas de caméra physique : génère une image synthétique animée.
    Utile pour tester l'interface C# sans hardware.
    """
    global _latest_frame
    import numpy as np
    log.info("Mode fallback : génération d'image synthétique")
    t = 0
    while True:
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Fond dégradé animé
        for y in range(480):
            val = int(30 + 20 * abs(((y + t * 2) % 480) / 480 - 0.5))
            frame[y, :] = [val, val // 2, val // 3]

        # Texte indicatif
        cv2.putText(frame, "SIMULATION MODE", (160, 200),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 220, 80), 2)
        cv2.putText(frame, "No camera detected", (185, 250),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 1)
        cv2.putText(frame, f"t = {t/10:.1f}s", (280, 310),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 180, 255), 1)

        # Cercle animé simulant une pièce détectée
        cx = 320 + int(100 * __import__('math').sin(t * 0.15))
        cy = 380
        cv2.circle(frame, (cx, cy), 20, (0, 200, 255), 2)
        cv2.putText(frame, "Part", (cx - 15, cy - 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

        _draw_overlay(frame)
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        with _frame_lock:
            _latest_frame = buffer.tobytes()
        t += 1
        time.sleep(0.033)


def _draw_overlay(frame):
    """Ajoute timestamp et étiquette sur chaque frame."""
    ts = time.strftime("%H:%M:%S")
    cv2.rectangle(frame, (0, 0), (250, 28), (0, 0, 0), -1)
    cv2.putText(frame, f"MachineSight Cam  {ts}", (6, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 230, 120), 1)
    cv2.putText(frame, f"{_fps_actual:.1f} fps", (580, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)


# ── Routes Flask ────────────────────────────────────────────────────────────

def _generate_mjpeg():
    """Générateur de frames MJPEG pour le streaming HTTP."""
    while True:
        with _frame_lock:
            frame = _latest_frame
        if frame:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + frame +
                b"\r\n"
            )
        time.sleep(0.033)


@app.route("/stream")
def stream():
    """Endpoint MJPEG — consommé par le client C# Avalonia."""
    return Response(
        _generate_mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/snapshot")
def snapshot():
    """Renvoie une seule frame JPEG."""
    with _frame_lock:
        frame = _latest_frame
    if frame:
        return Response(frame, mimetype="image/jpeg")
    return Response(status=503)


@app.route("/status")
def status():
    """Statut de la caméra — utile pour le healthcheck."""
    return jsonify({
        "camera_ok": _camera_ok,
        "fps": round(_fps_actual, 1),
        "stream_url": "http://<raspberry_ip>:5000/stream"
    })


def start_camera_server(device_index: int = 0):
    """Lance le thread de capture et le serveur Flask."""
    t = threading.Thread(target=_capture_loop, args=(device_index,), daemon=True)
    t.start()
    # Attendre la première frame
    for _ in range(30):
        with _frame_lock:
            if _latest_frame:
                break
        time.sleep(0.1)
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
