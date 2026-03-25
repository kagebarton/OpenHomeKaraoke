#!/usr/bin/env python3
"""
Qwen_web_test.py
----------------
Simple web UI for testing Qwen Pipeline V2.

Usage:
    python Qwen_web_test.py /path/to/song/folder [--port 5000]

Features:
- Real-time pitch control (no restart)
- Real-time vocal/nonvocal volume control
- Play/Pause/Seek
- Subtitle delay
- Live status display
"""

import os
import sys
import time
import logging
import threading
from flask import Flask, render_template_string, jsonify, request

# Add parent directory to path so we can import as 'qwen' package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from qwen import PipelineV2, RUBBERBAND_AVAILABLE

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

# Global pipeline instance
pipeline: PipelineV2 = None
pipeline_lock = threading.Lock()


# -----------------------------------------------------------------------------
# HTML Template (inline for simplicity)
# -----------------------------------------------------------------------------

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Qwen Pipeline V2 Test</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #eee;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        h1 {
            text-align: center;
            margin-bottom: 10px;
            color: #00d9ff;
        }
        .status-bar {
            background: rgba(255,255,255,0.1);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .status-indicator {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #666;
        }
        .status-dot.running { background: #0f0; box-shadow: 0 0 10px #0f0; }
        .status-dot.stopped { background: #f00; }
        .time-display {
            font-family: monospace;
            font-size: 1.2em;
            background: rgba(0,0,0,0.3);
            padding: 8px 15px;
            border-radius: 5px;
        }
        .control-section {
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .control-section h2 {
            margin-bottom: 15px;
            color: #00d9ff;
            font-size: 1.1em;
        }
        .control-row {
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 15px;
        }
        .control-row:last-child { margin-bottom: 0; }
        .control-label {
            min-width: 140px;
            font-weight: 500;
        }
        .control-value {
            min-width: 60px;
            text-align: right;
            font-family: monospace;
            color: #00d9ff;
        }
        input[type="range"] {
            flex: 1;
            height: 8px;
            -webkit-appearance: none;
            background: rgba(255,255,255,0.2);
            border-radius: 4px;
            outline: none;
        }
        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 20px;
            height: 20px;
            background: #00d9ff;
            border-radius: 50%;
            cursor: pointer;
            box-shadow: 0 0 10px rgba(0,217,255,0.5);
        }
        .btn-row {
            display: flex;
            gap: 10px;
            justify-content: center;
            flex-wrap: wrap;
        }
        button {
            padding: 12px 25px;
            font-size: 1em;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            font-weight: 600;
        }
        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        .btn-primary {
            background: linear-gradient(135deg, #00d9ff, #0099cc);
            color: #000;
        }
        .btn-primary:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0,217,255,0.4);
        }
        .btn-secondary {
            background: rgba(255,255,255,0.2);
            color: #fff;
        }
        .btn-secondary:hover:not(:disabled) {
            background: rgba(255,255,255,0.3);
        }
        .btn-danger {
            background: linear-gradient(135deg, #ff4757, #cc3344);
            color: #fff;
        }
        .warning-box {
            background: rgba(255,193,7,0.2);
            border: 1px solid #ffc107;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
        }
        .warning-box p {
            color: #ffc107;
            font-size: 0.9em;
        }
        .log-box {
            background: rgba(0,0,0,0.3);
            border-radius: 10px;
            padding: 15px;
            max-height: 200px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 0.85em;
        }
        .log-entry {
            padding: 3px 0;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .log-entry.error { color: #ff4757; }
        .log-entry.info { color: #00d9ff; }
        .log-entry.success { color: #0f0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎤 Qwen Pipeline V2 Test</h1>
        
        {% if not rubberband_available %}
        <div class="warning-box">
            <p>⚠️ <strong>Rubberband not available</strong> - Pitch changes will have no effect.</p>
            <p style="margin-top: 8px;">Install: <code>sudo apt install librubberband2-dev libsndfile1-dev portaudio19-dev && pip install rubberband sounddevice</code></p>
        </div>
        {% endif %}

        <div class="status-bar">
            <div class="status-indicator">
                <div class="status-dot" id="statusDot"></div>
                <span id="statusText">Stopped</span>
            </div>
            <div class="time-display" id="timeDisplay">0:00 / 0:00</div>
        </div>

        <div class="control-section">
            <h2>▶️ Playback Control</h2>
            <div class="btn-row">
                <button class="btn-primary" id="playBtn" onclick="togglePlay()">Play</button>
                <button class="btn-secondary" onclick="seek(-10)">-10s</button>
                <button class="btn-secondary" onclick="seek(10)">+10s</button>
                <button class="btn-danger" onclick="stop()">Stop</button>
            </div>
        </div>

        <div class="control-section">
            <h2>🎵 Pitch Control (Real-time)</h2>
            <div class="control-row">
                <span class="control-label">Pitch (semitones)</span>
                <input type="range" id="pitchSlider" min="-12" max="12" step="1" value="-2"
                       oninput="updatePitch(this.value)">
                <span class="control-value" id="pitchValue">-2</span>
            </div>
        </div>

        <div class="control-section">
            <h2>🔊 Volume Control (Real-time)</h2>
            <div class="control-row">
                <span class="control-label">Vocal Volume</span>
                <input type="range" id="vocalVolSlider" min="0" max="1" step="0.05" value="0.4"
                       oninput="updateVocalVol(this.value)">
                <span class="control-value" id="vocalVolValue">0.40</span>
            </div>
            <div class="control-row">
                <span class="control-label">Nonvocal Volume</span>
                <input type="range" id="nonvocalVolSlider" min="0" max="1" step="0.05" value="1.0"
                       oninput="updateNonvocalVol(this.value)">
                <span class="control-value" id="nonvocalVolValue">1.00</span>
            </div>
        </div>

        <div class="control-section">
            <h2>📝 Subtitle Delay</h2>
            <div class="control-row">
                <span class="control-label">Delay (seconds)</span>
                <input type="range" id="subDelaySlider" min="-5" max="5" step="0.1" value="-0.8"
                       oninput="updateSubDelay(this.value)">
                <span class="control-value" id="subDelayValue">-0.8</span>
            </div>
        </div>

        <div class="control-section">
            <h2>📋 Log</h2>
            <div class="log-box" id="logBox"></div>
        </div>
    </div>

    <script>
        let isPlaying = false;
        let updateInterval = null;

        function log(message, type = 'info') {
            const logBox = document.getElementById('logBox');
            const entry = document.createElement('div');
            entry.className = `log-entry ${type}`;
            entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
            logBox.insertBefore(entry, logBox.firstChild);
            while (logBox.children.length > 50) {
                logBox.removeChild(logBox.lastChild);
            }
        }

        async function api(endpoint, method = 'GET') {
            try {
                const response = await fetch(`/api${endpoint}`, { method });
                const data = await response.json();
                return data;
            } catch (e) {
                log(`API error: ${e.message}`, 'error');
                return null;
            }
        }

        async function updateStatus() {
            const state = await api('/state');
            if (!state) return;

            const statusDot = document.getElementById('statusDot');
            const statusText = document.getElementById('statusText');
            const timeDisplay = document.getElementById('timeDisplay');
            const playBtn = document.getElementById('playBtn');

            if (state.running) {
                statusDot.className = 'status-dot running';
                statusText.textContent = 'Playing';
                playBtn.textContent = state.paused ? 'Play' : 'Pause';
                
                const audioTime = state.audio_time || 0;
                const duration = state.duration || 0;
                timeDisplay.textContent = `${formatTime(audioTime)} / ${formatTime(duration)}`;
            } else {
                statusDot.className = 'status-dot stopped';
                statusText.textContent = 'Stopped';
                playBtn.textContent = 'Play';
            }

            // Update sliders if not being dragged
            if (!document.activeElement.matches('input[type="range"]')) {
                if (state.pitch_semitones !== undefined) {
                    document.getElementById('pitchSlider').value = state.pitch_semitones;
                    document.getElementById('pitchValue').textContent = 
                        (state.pitch_semitones >= 0 ? '+' : '') + state.pitch_semitones;
                }
                if (state.vocal_vol !== undefined) {
                    document.getElementById('vocalVolSlider').value = state.vocal_vol;
                    document.getElementById('vocalVolValue').textContent = state.vocal_vol.toFixed(2);
                }
                if (state.nonvocal_vol !== undefined) {
                    document.getElementById('nonvocalVolSlider').value = state.nonvocal_vol;
                    document.getElementById('nonvocalVolValue').textContent = state.nonvocal_vol.toFixed(2);
                }
                if (state.sub_delay !== undefined) {
                    document.getElementById('subDelaySlider').value = state.sub_delay;
                    document.getElementById('subDelayValue').textContent = state.sub_delay.toFixed(1);
                }
            }
        }

        function formatTime(seconds) {
            if (!seconds || isNaN(seconds)) return '0:00';
            const m = Math.floor(seconds / 60);
            const s = Math.floor(seconds % 60);
            return `${m}:${s.toString().padStart(2, '0')}`;
        }

        async function togglePlay() {
            const result = await api('/toggle_pause', 'POST');
            if (result) {
                isPlaying = !result.paused;
                log(isPlaying ? 'Playing' : 'Paused', 'success');
            }
        }

        async function stop() {
            const result = await api('/stop', 'POST');
            if (result) {
                isPlaying = false;
                log('Stopped', 'success');
                updateStatus();
            }
        }

        async function seek(seconds) {
            const result = await api(`/seek/${seconds}`, 'POST');
            if (result) {
                log(`Seek ${seconds >= 0 ? '+' : ''}${seconds}s`, 'info');
            }
        }

        async function updatePitch(value) {
            document.getElementById('pitchValue').textContent = (value >= 0 ? '+' : '') + value;
            await api(`/pitch/${value}`, 'POST');
        }

        async function updateVocalVol(value) {
            document.getElementById('vocalVolValue').textContent = parseFloat(value).toFixed(2);
            await api(`/vocal_vol/${value}`, 'POST');
        }

        async function updateNonvocalVol(value) {
            document.getElementById('nonvocalVolValue').textContent = parseFloat(value).toFixed(2);
            await api(`/nonvocal_vol/${value}`, 'POST');
        }

        async function updateSubDelay(value) {
            document.getElementById('subDelayValue').textContent = parseFloat(value).toFixed(1);
            await api(`/sub_delay/${value}`, 'POST');
        }

        // Start status updates
        setInterval(updateStatus, 500);

        // Initial status
        updateStatus();
        log('Web UI loaded', 'success');
    </script>
</body>
</html>
"""


# -----------------------------------------------------------------------------
# API Routes
# -----------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
        rubberband_available=RUBBERBAND_AVAILABLE
    )


@app.route('/api/state')
def get_state():
    with pipeline_lock:
        if pipeline:
            return jsonify(pipeline.state)
    return jsonify({"error": "Pipeline not initialized"})


@app.route('/api/toggle_pause', methods=['POST'])
def toggle_pause():
    with pipeline_lock:
        if pipeline and pipeline.is_running:
            paused = pipeline.toggle_pause()
            return jsonify({"paused": paused})
    return jsonify({"error": "Pipeline not running"})


@app.route('/api/stop', methods=['POST'])
def stop():
    with pipeline_lock:
        if pipeline:
            pipeline.stop()
            return jsonify({"success": True})
    return jsonify({"error": "Pipeline not initialized"})


@app.route('/api/pitch/<value>', methods=['POST'])
def set_pitch(value):
    with pipeline_lock:
        if pipeline and pipeline.is_running:
            pipeline.set_pitch(float(value))
            return jsonify({"success": True})
    return jsonify({"error": "Pipeline not running"})


@app.route('/api/vocal_vol/<value>', methods=['POST'])
def set_vocal_vol(value):
    with pipeline_lock:
        if pipeline and pipeline.is_running:
            pipeline.set_vocal_volume(float(value))
            return jsonify({"success": True})
    return jsonify({"error": "Pipeline not running"})


@app.route('/api/nonvocal_vol/<value>', methods=['POST'])
def set_nonvocal_vol(value):
    with pipeline_lock:
        if pipeline and pipeline.is_running:
            pipeline.set_nonvocal_volume(float(value))
            return jsonify({"success": True})
    return jsonify({"error": "Pipeline not running"})


@app.route('/api/sub_delay/<value>', methods=['POST'])
def set_sub_delay(value):
    with pipeline_lock:
        if pipeline and pipeline.is_running:
            pipeline.set_subtitle_delay(float(value))
            return jsonify({"success": True})
    return jsonify({"error": "Pipeline not running"})


@app.route('/api/seek/<offset>', methods=['POST'])
def seek(offset):
    with pipeline_lock:
        if pipeline and pipeline.is_running:
            state = pipeline.state
            current = state.get('video_time', 0) or 0
            new_pos = max(0, current + float(offset))
            pipeline.video_player.seek(new_pos)
            return jsonify({"success": True, "new_position": new_pos})
    return jsonify({"error": "Pipeline not running"})


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    global pipeline

    if len(sys.argv) < 2:
        print("Usage: python Qwen_web_test.py /path/to/song/folder [--port 5000]")
        print("")
        print("The song folder should contain:")
        print("  - video.mp4")
        print("  - vocal.m4a")
        print("  - nonvocal.m4a")
        print("  - subs.srt (optional)")
        sys.exit(1)

    song_folder = sys.argv[1]

    # Parse port argument
    port = 5000
    if '--port' in sys.argv:
        idx = sys.argv.index('--port')
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    if not os.path.isdir(song_folder):
        log.error(f"Song folder not found: {song_folder}")
        sys.exit(1)

    # Initialize pipeline
    log.info(f"Initializing pipeline for: {song_folder}")
    pipeline = PipelineV2(song_folder, use_dummy_audio=not RUBBERBAND_AVAILABLE, fullscreen_video=True)

    # Start Flask
    log.info(f"Starting web UI at http://localhost:{port}")
    log.info(f"Rubberband available: {RUBBERBAND_AVAILABLE}")

    # Run Flask in a thread so we can handle Ctrl+C
    flask_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()

    log.info("Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        with pipeline_lock:
            if pipeline:
                pipeline.stop()


if __name__ == "__main__":
    import time
    main()
