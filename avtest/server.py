"""
server.py
---------
Flask server exposing control routes for the karaoke pipeline.
Runs in a background thread — the main thread owns the pygame window.

Routes:
  GET  /              — main UI page
  GET  /status        — current pipeline state as JSON
  POST /pitch         — set pitch in semitones      { "value": -2 }
  POST /vocal_vol     — set vocal volume 0.0–1.0    { "value": 0.4 }
  POST /nonvocal_vol  — set nonvocal volume 0.0–1.0 { "value": 1.0 }
  POST /sub_delay     — set subtitle delay seconds  { "value": -0.8 }
  POST /pause         — toggle pause/play
"""

import logging
import threading

from flask import Flask, jsonify, render_template, request

log = logging.getLogger(__name__)

app  = Flask(__name__)
pipe = None   # set by main.py before server starts


def start(pipeline, host="0.0.0.0", port=5000):
    """Start Flask in a daemon thread. Call after pipeline is created."""
    global pipe
    pipe = pipeline
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    log.info(f"[server]  Listening on http://{host}:{port}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", state=pipe.state)


@app.route("/status")
def status():
    return jsonify(pipe.state)


@app.route("/pitch", methods=["POST"])
def set_pitch():
    val = float(request.json["value"])
    # Run restart in a thread so the HTTP response returns immediately
    threading.Thread(target=pipe.set_pitch, args=(val,), daemon=True).start()
    return jsonify({"ok": True, "pitch_semitones": val})


@app.route("/vocal_vol", methods=["POST"])
def set_vocal_vol():
    val = float(request.json["value"])
    threading.Thread(target=pipe.set_vocal_vol, args=(val,), daemon=True).start()
    return jsonify({"ok": True, "vocal_vol": val})


@app.route("/nonvocal_vol", methods=["POST"])
def set_nonvocal_vol():
    val = float(request.json["value"])
    threading.Thread(target=pipe.set_nonvocal_vol, args=(val,), daemon=True).start()
    return jsonify({"ok": True, "nonvocal_vol": val})


@app.route("/sub_delay", methods=["POST"])
def set_sub_delay():
    val = float(request.json["value"])
    pipe.set_sub_delay(val)   # IPC — fast enough to call directly
    return jsonify({"ok": True, "sub_delay": val})


@app.route("/pause", methods=["POST"])
def toggle_pause():
    is_paused = pipe.toggle_pause()
    return jsonify({"ok": True, "paused": is_paused})
