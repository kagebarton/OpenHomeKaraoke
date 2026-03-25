"""
video_player.py
---------------
Video-only player using mpv.

Handles:
- Video playback from file or stream
- Subtitle rendering
- Sync with external audio
- IPC control (pause, seek, subtitle delay)
"""

import os
import json
import time
import socket
import logging
import subprocess
from typing import Optional

log = logging.getLogger(__name__)

IPC_PATH = "/tmp/mpv-karaoke-video"


class VideoPlayer:
    """
    mpv-based video player for karaoke.

    Plays video only (no audio) with subtitle support.
    Controlled via IPC socket for sync with external audio.
    """

    def __init__(
        self,
        video_path: str,
        subtitle_path: Optional[str] = None,
        window_id: Optional[int] = None,
        fullscreen: bool = False
    ):
        self.video_path = video_path
        self.subtitle_path = subtitle_path
        self.window_id = window_id
        self.fullscreen = fullscreen

        self._proc: Optional[subprocess.Popen] = None
        self._ipc_socket: Optional[socket.socket] = None
        self._sub_delay = 0.0

    def start(self, start_time: float = 0.0):
        """Start video playback."""
        if not os.path.exists(self.video_path):
            raise FileNotFoundError(f"Video not found: {self.video_path}")

        cmd = self._build_cmd(start_time)
        log.info(f"[VideoPlayer] Starting: {' '.join(cmd)}")

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Wait for IPC socket to be ready
        time.sleep(0.5)
        self._connect_ipc()

    def _build_cmd(self, start_time: float = 0.0) -> list:
        """Build mpv command line."""
        cmd = [
            "mpv",
            self.video_path,
            "--no-audio",  # No audio - handled separately
            "--no-terminal",
        ]

        # Window embedding (for pygame)
        if self.window_id:
            cmd.append(f"--wid={self.window_id}")
        elif self.fullscreen:
            cmd.append("--fullscreen")

        # Subtitles
        if self.subtitle_path and os.path.exists(self.subtitle_path):
            cmd.extend([
                f"--sub-file={self.subtitle_path}",
                f"--sub-delay={self._sub_delay}",
                "--sub-visibility=yes",
            ])

        # Seek to start position
        if start_time > 0:
            cmd.append(f"--start={start_time:.3f}")

        # IPC for control
        cmd.append(f"--input-ipc-server={IPC_PATH}")

        # Low-latency settings
        cmd.extend([
            "--cache=no",
            "--demuxer-max-bytes=50MiB",
            "--demuxer-max-back-bytes=10MiB",
        ])

        return cmd

    def _connect_ipc(self):
        """Connect to mpv IPC socket."""
        try:
            self._ipc_socket = socket.socket(socket.AF_UNIX)
            self._ipc_socket.settimeout(1.0)
            self._ipc_socket.connect(IPC_PATH)
            log.info("[VideoPlayer] Connected to IPC")
        except Exception as e:
            log.warning(f"[VideoPlayer] IPC connect failed: {e}")
            self._ipc_socket = None

    def _send_ipc(self, command: dict) -> Optional[dict]:
        """Send command to mpv via IPC."""
        if not self._ipc_socket:
            return None

        try:
            msg = json.dumps(command) + "\n"
            self._ipc_socket.sendall(msg.encode())

            # Read response
            response = b""
            while True:
                chunk = self._ipc_socket.recv(4096)
                if not chunk:
                    break
                response += chunk
                if chunk.endswith(b"\n"):
                    break

            return json.loads(response.decode().strip())

        except Exception as e:
            log.warning(f"[VideoPlayer] IPC send failed: {e}")
            return None

    def pause(self):
        """Pause playback."""
        self._send_ipc({"command": ["set_property", "pause", True]})

    def resume(self):
        """Resume playback."""
        self._send_ipc({"command": ["set_property", "pause", False]})

    def toggle_pause(self) -> bool:
        """Toggle pause state. Returns True if now paused."""
        result = self._send_ipc({"command": ["cycle", "pause"]})
        state = self._send_ipc({"command": ["get_property", "pause"]})
        return state.get("data", False) if state else False

    def seek(self, position: float, mode: str = "absolute"):
        """Seek to position in seconds."""
        self._send_ipc({
            "command": ["seek", position, mode]
        })

    def set_subtitle_delay(self, delay: float):
        """Set subtitle delay in seconds."""
        self._sub_delay = delay
        self._send_ipc({
            "command": ["set_property", "sub-delay", delay]
        })

    def get_time_pos(self) -> Optional[float]:
        """Get current playback position in seconds."""
        result = self._send_ipc({"command": ["get_property", "time-pos"]})
        return result.get("data") if result else None

    def get_duration(self) -> Optional[float]:
        """Get total duration in seconds."""
        result = self._send_ipc({"command": ["get_property", "duration"]})
        return result.get("data") if result else None

    def is_paused(self) -> bool:
        """Check if playback is paused."""
        result = self._send_ipc({"command": ["get_property", "pause"]})
        return result.get("data", False) if result else False

    def is_running(self) -> bool:
        """Check if video player is still running."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def stop(self):
        """Stop playback."""
        log.info("[VideoPlayer] Stopping...")

        if self._ipc_socket:
            try:
                self._ipc_socket.close()
            except:
                pass
            self._ipc_socket = None

        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

        # Clean up IPC socket file
        if os.path.exists(IPC_PATH):
            try:
                os.unlink(IPC_PATH)
            except:
                pass

    @property
    def state(self) -> dict:
        """Get player state."""
        return {
            "time_pos": self.get_time_pos(),
            "duration": self.get_duration(),
            "paused": self.is_paused(),
            "running": self.is_running(),
            "sub_delay": self._sub_delay,
        }


class VideoStreamer:
    """
    Video player that receives UDP stream (from ffmpeg).

    For use with ffmpeg's UDP output.
    """

    def __init__(
        self,
        udp_port: int = 9000,
        window_id: Optional[int] = None,
        fullscreen: bool = False
    ):
        self.udp_port = udp_port
        self.window_id = window_id
        self.fullscreen = fullscreen

        self._proc: Optional[subprocess.Popen] = None
        self._ipc_socket: Optional[socket.socket] = None

    def start(self):
        """Start receiving and playing UDP stream."""
        cmd = [
            "mpv",
            f"udp://@:{self.udp_port}",
            "--no-audio",
            "--no-terminal",
            "--cache=no",
            "--demuxer=lavf",
            "--demuxer-lavf-format=mpegts",
        ]

        if self.window_id:
            cmd.append(f"--wid={self.window_id}")
        elif self.fullscreen:
            cmd.append("--fullscreen")

        cmd.append(f"--input-ipc-server={IPC_PATH}")

        log.info(f"[VideoStreamer] Starting UDP receiver on port {self.udp_port}")
        self._proc = subprocess.Popen(cmd)

        time.sleep(0.5)
        self._connect_ipc()

    def _connect_ipc(self):
        """Connect to mpv IPC socket."""
        try:
            self._ipc_socket = socket.socket(socket.AF_UNIX)
            self._ipc_socket.settimeout(1.0)
            self._ipc_socket.connect(IPC_PATH)
        except Exception as e:
            log.warning(f"[VideoStreamer] IPC connect failed: {e}")
            self._ipc_socket = None

    def stop(self):
        """Stop playback."""
        if self._ipc_socket:
            self._ipc_socket.close()
            self._ipc_socket = None

        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

        if os.path.exists(IPC_PATH):
            try:
                os.unlink(IPC_PATH)
            except:
                pass
