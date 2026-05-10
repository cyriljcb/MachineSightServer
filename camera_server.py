"""
camera_server.py
Capture la webcam Logitech et diffuse le flux en MJPEG sur HTTP port 5000.
Le client C# n'a qu'à lire http://<ip_raspberry>:5000/stream

Endpoints de debug (Polly / tests de résilience) :
  POST /debug/disconnect?seconds=5   →  coupe le serveur OPC UA N secondes
  GET  /debug/status                 →  état courant du serveur OPC UA
"""

import cv2
import threading
import time
import logging
from flask import Flask, Response, jsonify, request

log = logging.getLogger("camera_server")
app = Flask(__name__)

# ── État global de la caméra ────────────────────────────────────────────────
_frame_lock  = threading.Lock()
_latest_frame: bytes | None = None
_camera_ok   = False
_fps_counter = 0
_fps_actual  = 0.0

# Référence vers OpcUaServer, injectée par start_camera_server()
_opc_server = None


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

        _draw_overlay(frame)

        ok, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            with _frame_lock:
                _latest_frame = buffer.tobytes()
            frame_count += 1

        now = time.time()
        if now - last_fps_t >= 1.0:
            _fps_actual  = frame_count / (now - last_fps_t)
            frame_count  = 0
            last_fps_t   = now

        time.sleep(0.033)

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
        for y in range(480):
            val = int(30 + 20 * abs(((y + t * 2) % 480) / 480 - 0.5))
            frame[y, :] = [val, val // 2, val // 3]

        cv2.putText(frame, "SIMULATION MODE", (160, 200),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 220, 80), 2)
        cv2.putText(frame, "No camera detected", (185, 250),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 1)
        cv2.putText(frame, f"t = {t/10:.1f}s", (280, 310),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 180, 255), 1)

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


# ── Routes Flask — caméra ───────────────────────────────────────────────────

def _generate_mjpeg():
    """Générateur de frames MJPEG pour le streaming HTTP."""
    while True:
        with _frame_lock:
            frame = _latest_frame
        if frame:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + f"Content-Length: {len(frame)}\r\n\r\n".encode()
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


# ── Routes Flask — debug OPC UA ─────────────────────────────────────────────

@app.route("/debug/disconnect", methods=["POST"])
def debug_disconnect():
    """
    Coupe le serveur OPC UA pendant N secondes pour tester la résilience
    du client C# (Polly retry / circuit breaker).

    Usage :
        POST http://<ip>:5000/debug/disconnect?seconds=10
        POST http://<ip>:5000/debug/disconnect          # défaut : 5s

    Réponses :
        200  { "ok": true,  "duration_s": 10, "message": "..." }
        409  { "ok": false, "message": "Déconnexion déjà en cours" }
        503  { "ok": false, "message": "OPC UA server non disponible" }
    """
    if _opc_server is None:
        return jsonify({"ok": False, "message": "OPC UA server non disponible"}), 503

    try:
        seconds = float(request.args.get("seconds", 5))
        seconds = max(1.0, min(seconds, 120.0))   # garde-fou : 1–120 s
    except (TypeError, ValueError):
        seconds = 5.0

    accepted = _opc_server.schedule_disconnect(seconds)
    if not accepted:
        return jsonify({
            "ok": False,
            "message": "Déconnexion déjà en cours, réessayez plus tard."
        }), 409

    log.warning(f"[DEBUG] Déconnexion OPC UA demandée via HTTP — {seconds:.0f}s")
    return jsonify({
        "ok": True,
        "duration_s": seconds,
        "message": f"Serveur OPC UA sera hors ligne pendant {seconds:.0f}s."
    })


@app.route("/debug/status")
def debug_status():
    """
    Retourne l'état courant du serveur OPC UA.

    Usage :
        GET http://<ip>:5000/debug/status
    """
    if _opc_server is None:
        return jsonify({"opc_connected": False, "message": "OPC UA server non disponible"}), 503

    connected = not _opc_server.is_disconnected
    return jsonify({
        "opc_connected": connected,
        "message": "En ligne" if connected else "Déconnexion temporaire en cours"
    })


# ── Démarrage ────────────────────────────────────────────────────────────────

def start_camera_server(device_index: int = 0, opc_server=None):
    """
    Lance le thread de capture et le serveur Flask.

    Args:
        device_index: index de la caméra (0 par défaut).
        opc_server:   instance OpcUaServer, nécessaire pour les endpoints /debug/*.
    """
    global _opc_server
    _opc_server = opc_server

    t = threading.Thread(target=_capture_loop, args=(device_index,), daemon=True)
    t.start()
    for _ in range(30):
        with _frame_lock:
            if _latest_frame:
                break
        time.sleep(0.1)
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)