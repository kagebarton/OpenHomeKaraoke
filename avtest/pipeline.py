"""
pipeline.py
-----------
Manages the FFmpeg + mpv subprocess pipeline.

FFmpeg:
  - Muxes video + vocal + nonvocal
  - Applies per-track rubberband pitch shift
  - Streams mpegts over UDP

mpv:
  - Receives UDP stream and renders into a pygame window via --wid
  - Loads subtitles from disk independently
  - Controlled at runtime via a Unix IPC socket

Restart policy:
  - Pitch / vocal vol / nonvocal vol  → FFmpeg restart (mpv keeps running)
  - Subtitle delay                    → IPC command (zero pause)
  - Pause / play                      → IPC command (zero pause)
"""

import os
import json
import time
import socket
import logging
import threading
import subprocess

log = logging.getLogger(__name__)

UDP_PORT = 9000
IPC_PATH = "/tmp/mpv-karaoke-test"


class Pipeline:

    def __init__(self, song_folder: str, window_id: int):
        self.song_folder   = song_folder
        self.window_id     = window_id

        self.video    = os.path.join(song_folder, "video.mp4")
        self.vocal    = os.path.join(song_folder, "vocal.m4a")
        self.nonvocal = os.path.join(song_folder, "nonvocal.m4a")
        self.subs     = os.path.join(song_folder, "subs.srt")

        # Playback state
        self.pitch_semitones = -2
        self.vocal_vol       = 0.4
        self.nonvocal_vol    = 1.0
        self.sub_delay       = -0.8

        self._ffmpeg_proc: subprocess.Popen | None = None
        self._mpv_proc:    subprocess.Popen | None = None
        self._restart_lock = threading.Lock()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def start(self):
        """Start mpv then FFmpeg. Call once from main thread after pygame init."""
        missing = [f for f in (self.video, self.vocal, self.nonvocal)
                   if not os.path.exists(f)]
        if missing:
            raise FileNotFoundError(f"Missing song files: {missing}")

        log.info("[pipeline] Starting mpv...")
        self._mpv_proc = subprocess.Popen(self._mpv_cmd())
        time.sleep(1.0)  # give mpv time to bind the UDP port

        log.info("[pipeline] Starting FFmpeg...")
        self._ffmpeg_proc = subprocess.Popen(self._ffmpeg_cmd())

        log.info("[pipeline] Pipeline running.")

    def stop(self):
        """Terminate both processes cleanly."""
        self._kill(self._ffmpeg_proc, "ffmpeg")
        self._kill(self._mpv_proc, "mpv")

    def set_pitch(self, semitones: float):
        """Change pitch — requires FFmpeg restart."""
        self.pitch_semitones = float(semitones)
        self._restart_ffmpeg()

    def set_vocal_vol(self, vol: float):
        """Change vocal volume (0.0–1.0) — requires FFmpeg restart."""
        self.vocal_vol = float(vol)
        self._restart_ffmpeg()

    def set_nonvocal_vol(self, vol: float):
        """Change nonvocal volume (0.0–1.0) — requires FFmpeg restart."""
        self.nonvocal_vol = float(vol)
        self._restart_ffmpeg()

    def set_sub_delay(self, delay: float):
        """Change subtitle delay in seconds — zero pause, IPC only."""
        self.sub_delay = float(delay)
        self._ipc({"command": ["set_property", "sub-delay", self.sub_delay]})

    def toggle_pause(self) -> bool:
        """Toggle pause/play. Returns True if now paused."""
        self._ipc({"command": ["cycle", "pause"]})
        result = self._ipc_get("pause")
        return bool(result)

    def get_time_pos(self) -> float | None:
        """Return current playback position in seconds, or None."""
        return self._ipc_get("time-pos")

    def get_duration(self) -> float | None:
        """Return total duration in seconds, or None."""
        return self._ipc_get("duration")

    def is_running(self) -> bool:
        return (self._ffmpeg_proc is not None and
                self._ffmpeg_proc.poll() is None)

    @property
    def state(self) -> dict:
        return {
            "pitch_semitones": self.pitch_semitones,
            "vocal_vol":       round(self.vocal_vol, 2),
            "nonvocal_vol":    round(self.nonvocal_vol, 2),
            "sub_delay":       round(self.sub_delay, 2),
            "time_pos":        self.get_time_pos(),
            "duration":        self.get_duration(),
            "running":         self.is_running(),
        }

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _pitch_ratio(self) -> float:
        return 2 ** (self.pitch_semitones / 12)

    def _ffmpeg_cmd(self, start_time: float = 0.0) -> list[str]:
        r = self._pitch_ratio()
        filter_complex = (
            f"[1:a]rubberband=pitch={r:.7f}:window=long:pitchq=quality"
            f":transients=crisp:detector=compound:formant=preserved,"
            f"volume={self.vocal_vol:.4f}[vocal];"

            f"[2:a]rubberband=pitch={r:.7f}:window=standard:pitchq=consistency"
            f":transients=crisp:detector=compound:formant=shifted,"
            f"volume={self.nonvocal_vol:.4f}[nonvocal];"

            f"[vocal][nonvocal]amix=inputs=2:normalize=0[out]"
        )
        # -ss before each -i seeks that input stream to the same position.
        # -copyts preserves the original PTS values in the output so mpv
        # receives packets already stamped at ~T rather than restarting from 0.
        # Without -copyts mpv's internal clock (at T) sees PTS=0 packets and
        # plays in slow motion for T seconds waiting for timestamps to catch up.
        ss = ["-ss", f"{start_time:.3f}"] if start_time > 0 else []
        copyts = ["-copyts"] if start_time > 0 else []
        return [
            "ffmpeg", "-y",
            "-re",
            *copyts,
            *ss, "-i", self.video,
            *ss, "-i", self.vocal,
            *ss, "-i", self.nonvocal,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[out]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-f", "mpegts",
            #f"udp://127.0.0.1:{UDP_PORT}?pkt_size=1316",
            f"udp://127.0.0.1:{UDP_PORT}?pkt_size=1500",
        ]

    def _mpv_cmd(self) -> list[str]:
        cmd = [
            "mpv",
            f"udp://@:{UDP_PORT}",
            "--no-cache",
            #"--demuxer=lavf",
            #"--demuxer-lavf-format=mpegts",
            f"--wid={self.window_id}",
            #"--no-terminal",
            "--sub-visibility=yes",     # required for subtitle rendering in --wid mode
            f"--input-ipc-server={IPC_PATH}",
        ]
        if os.path.exists(self.subs):
            cmd += [
                f"--sub-file={self.subs}",
                f"--sub-delay={self.sub_delay}",
            ]
        else:
            log.warning(f"[pipeline] No subtitle file found at {self.subs}")
        return cmd

    def _restart_ffmpeg(self):
        """Query mpv's current position, start a new FFmpeg process seeked to
        that point, then kill the old one. mpv keeps playing from its buffer
        during the brief overlap so the gap is minimised.
        """
        with self._restart_lock:
            # Get current playback position before we do anything else
            pos = self.get_time_pos() or 0.0
            log.info(f"[pipeline] Restarting FFmpeg at {pos:.1f}s "
                     f"(pitch={self.pitch_semitones:+}st, "
                     f"vocal={self.vocal_vol:.2f}, "
                     f"nonvocal={self.nonvocal_vol:.2f})")

            new_proc = subprocess.Popen(self._ffmpeg_cmd(start_time=pos))
            time.sleep(0.5)  # let new process initialise before cutting over

            old_proc = self._ffmpeg_proc
            self._ffmpeg_proc = new_proc
            self._kill(old_proc, "ffmpeg (old)")

    def _ipc(self, cmd: dict) -> None:
        """Fire-and-forget IPC command to mpv."""
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(1.0)
                s.connect(IPC_PATH)
                s.sendall((json.dumps(cmd) + "\n").encode())
        except Exception as e:
            log.warning(f"[pipeline] IPC send failed: {e}")

    def _ipc_get(self, prop: str):
        """Send a get_property command and return the data value."""
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(1.0)
                s.connect(IPC_PATH)
                cmd = {"command": ["get_property", prop]}
                s.sendall((json.dumps(cmd) + "\n").encode())
                raw = s.recv(4096).decode().strip()
                return json.loads(raw).get("data")
        except Exception:
            return None

    @staticmethod
    def _kill(proc: subprocess.Popen | None, name: str):
        if proc and proc.poll() is None:
            log.info(f"[pipeline] Terminating {name} (pid {proc.pid})")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
