#!/usr/bin/env python3
"""
AV Pipeline Test
================
MPV plays video + subtitles (no audio).
sounddevice streams mixed vocal + nonvocal audio independently.
A sync thread polls MPV's IPC socket and corrects drift.

Dependencies:
    pip install sounddevice soundfile librosa pyrubberband numpy
    sudo apt install mpv libportaudio2
"""

import json
import socket
import subprocess
import sys
import threading
import time

import librosa
import numpy as np
import pyrubberband as rb
import sounddevice as sd

# ════════════════════════════════════════════════════════════════════════════════
#  CONSTANTS — edit these before running
# ════════════════════════════════════════════════════════════════════════════════

VIDEO_FILE      = "../song/video.mp4"
VOCAL_FILE      = "../song/vocal.m4a"      # vocal track
NONVOCAL_FILE   = "../song/nonvocal.m4a"   # instrumental track
SUBTITLE_FILE   = "../song/subs.srt"       # set to "" to skip subtitles
SUBTITLE_DELAY  = -1.0                     # subtitle delay in seconds: positive = later, negative = earlier

SAMPLE_RATE     = 44100   # Hz — must match your source audio, or librosa will resample
BLOCK_SIZE      = 1024    # samples per audio callback; lower = less latency, more CPU load
CHANNELS        = 2       # output channels: 1 = mono, 2 = stereo

VOCAL_VOLUME    = 0.25     # vocal track volume: 0.0 (silent) – 2.0 (double amplitude)
NONVOCAL_VOLUME = 1.0     # nonvocal track volume: 0.0 (silent) – 2.0 (double amplitude)
PITCH_SEMITONES = -1.0     # semitones to shift: 0.0 = no change, +2.0 = up a whole step

MPV_IPC_SOCKET  = "/tmp/mpv_test.sock"
MPV_IPC_TIMEOUT = 10.0    # seconds to wait for MPV IPC socket to become available

SYNC_INTERVAL   = 0.5     # seconds between A/V drift checks
SYNC_THRESHOLD  = 0.080   # seconds of drift before snapping audio position (80 ms)

# ════════════════════════════════════════════════════════════════════════════════
#  SHARED STATE
# ════════════════════════════════════════════════════════════════════════════════

vocal_data: np.ndarray = None    # vocal track samples
nonvocal_data: np.ndarray = None # nonvocal track samples
audio_pos  = 0                   # current read head in samples
pos_lock   = threading.Lock()
stop_event = threading.Event()


# ════════════════════════════════════════════════════════════════════════════════
#  AUDIO LOADING + PITCH SHIFT
# ════════════════════════════════════════════════════════════════════════════════

def load_audio(path: str, sr: int, pitch_semitones: float) -> np.ndarray:
    """Load audio file via librosa (handles m4a/AAC), optionally pitch-shift."""
    print(f"[load] Reading: {path}")
    y, native_sr = librosa.load(path, sr=None, mono=True)

    if native_sr != sr:
        print(f"[load] Resampling {native_sr} Hz → {sr} Hz")
        y = librosa.resample(y, orig_sr=native_sr, target_sr=sr)

    if pitch_semitones != 0.0:
        print(f"[load] Pitch shifting {pitch_semitones:+.1f} semitones (may take a few seconds)…")
        y = rb.pitch_shift(y, sr, pitch_semitones)

    duration = len(y) / sr
    print(f"[load] Ready — {duration:.1f}s of audio")
    return y.astype(np.float32)


# ════════════════════════════════════════════════════════════════════════════════
#  MPV IPC
# ════════════════════════════════════════════════════════════════════════════════

class MPVSocket:
    """Minimal MPV IPC client over a Unix domain socket."""

    def __init__(self, path: str):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(path)
        self._sock.settimeout(0.4)
        self._lock = threading.Lock()

    def get_property(self, prop: str):
        """Send get_property and return the value, or None on failure."""
        cmd = json.dumps({"command": ["get_property", prop]}) + "\n"
        with self._lock:
            try:
                self._sock.sendall(cmd.encode())
                buf = b""
                while True:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    # Scan for complete JSON lines; MPV sends one object per line
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            # Ignore async events; we want the reply with "data"
                            if "data" in obj and obj.get("error") == "success":
                                return obj["data"]
                            elif obj.get("error") not in (None, "success"):
                                return None  # property unavailable (e.g. during seek)
                        except json.JSONDecodeError:
                            pass
            except (socket.timeout, BrokenPipeError, OSError):
                pass
        return None

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


def connect_mpv_ipc(path: str, timeout: float = MPV_IPC_TIMEOUT) -> MPVSocket:
    """Retry connecting until MPV creates the socket or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return MPVSocket(path)
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for MPV IPC socket: {path}")


# ════════════════════════════════════════════════════════════════════════════════
#  SYNC THREAD — keeps audio head aligned with MPV playback position
# ════════════════════════════════════════════════════════════════════════════════

def sync_thread_fn(mpv: MPVSocket):
    global audio_pos

    while not stop_event.wait(SYNC_INTERVAL):
        mpv_time = mpv.get_property("playback-time")
        if mpv_time is None:
            continue  # MPV is seeking or idle

        with pos_lock:
            audio_time = audio_pos / SAMPLE_RATE

        drift = audio_time - mpv_time
        if abs(drift) > SYNC_THRESHOLD:
            corrected = int(mpv_time * SAMPLE_RATE)
            corrected = max(0, min(corrected, len(vocal_data) - 1))
            with pos_lock:
                audio_pos = corrected
            print(f"[sync] drift {drift * 1000:+.0f} ms → snapped to {mpv_time:.2f}s")
        else:
            print(f"[sync] drift {drift * 1000:+.0f} ms — ok")


# ════════════════════════════════════════════════════════════════════════════════
#  AUDIO CALLBACK — called by sounddevice on a real-time thread
# ════════════════════════════════════════════════════════════════════════════════

def audio_callback(outdata: np.ndarray, frames: int, time_info, status):
    """Fill outdata with the next chunk of mixed audio, applying per-track volume."""
    global audio_pos

    if status:
        print(f"[audio] stream status: {status}", file=sys.stderr)

    with pos_lock:
        start     = audio_pos
        audio_pos = start + frames

    end         = start + frames
    
    # Get vocal chunk
    vocal_available = len(vocal_data) - start
    if vocal_available <= 0:
        outdata[:] = 0
        raise sd.CallbackStop()
    vocal_chunk = vocal_data[start:end]
    if len(vocal_chunk) < frames:
        vocal_chunk = np.pad(vocal_chunk, (0, frames - len(vocal_chunk)))
    
    # Get nonvocal chunk
    nonvocal_available = len(nonvocal_data) - start
    nonvocal_chunk = nonvocal_data[start:end]
    if len(nonvocal_chunk) < frames:
        nonvocal_chunk = np.pad(nonvocal_chunk, (0, frames - len(nonvocal_chunk)))
    
    # Mix both tracks with independent volume control
    mixed = (vocal_chunk * VOCAL_VOLUME) + (nonvocal_chunk * NONVOCAL_VOLUME)
    
    # Broadcast mono → stereo
    mixed = mixed.reshape(-1, 1)
    outdata[:] = np.broadcast_to(mixed, (frames, CHANNELS))


# ════════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════════

def main():
    global vocal_data, nonvocal_data

    # 1. Load and (optionally) pitch-shift both audio tracks
    print("[main] Loading vocal track...")
    vocal_data = load_audio(VOCAL_FILE, SAMPLE_RATE, PITCH_SEMITONES)
    
    print("[main] Loading nonvocal track...")
    nonvocal_data = load_audio(NONVOCAL_FILE, SAMPLE_RATE, PITCH_SEMITONES)

    # 2. Build MPV command — video only, IPC socket for sync
    mpv_cmd = [
        "mpv",
        "--no-audio",
        f"--input-ipc-server={MPV_IPC_SOCKET}",
        "--keep-open=no",   # exit when video ends
        "--hr-seek=yes",    # more accurate seeks
    ]
    if SUBTITLE_FILE:
        mpv_cmd += [
            f"--sub-file={SUBTITLE_FILE}",
            f"--sub-delay={SUBTITLE_DELAY}",
        ]
    mpv_cmd.append(VIDEO_FILE)

    print("[mpv ] Launching MPV…")
    mpv_proc = subprocess.Popen(mpv_cmd)

    # 3. Wait for MPV's IPC socket to appear
    print("[mpv ] Waiting for IPC socket…")
    mpv_ipc = connect_mpv_ipc(MPV_IPC_SOCKET)
    print("[mpv ] IPC connected.")

    # 4. Open audio output stream
    stream = sd.OutputStream(
        samplerate = SAMPLE_RATE,
        blocksize  = BLOCK_SIZE,
        channels   = CHANNELS,
        dtype      = "float32",
        callback   = audio_callback,
    )

    # 5. Start sync thread
    sync_t = threading.Thread(target=sync_thread_fn, args=(mpv_ipc,), daemon=True)

    print(f"[main] Starting playback (vocal={VOCAL_VOLUME}, nonvocal={NONVOCAL_VOLUME}). Close the MPV window to quit.")
    with stream:
        sync_t.start()
        mpv_proc.wait()     # block until MPV exits

    stop_event.set()        # signal sync thread to stop
    mpv_ipc.close()
    print("[main] Done.")


if __name__ == "__main__":
    main()
