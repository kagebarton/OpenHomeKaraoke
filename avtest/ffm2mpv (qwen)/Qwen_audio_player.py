"""
audio_player.py
---------------
Audio output handler using PulseAudio/simpleaudio.

Provides:
- Low-latency audio playback
- Callback-based interface for real-time audio
- Volume control at output stage
"""

import logging
import threading
import queue
import numpy as np
from typing import Optional

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHANNELS = 1
BUFFER_SIZE = 1024


class AudioPlayer:
    """
    Audio output player using sounddevice.
    
    Uses synchronous playback for reliable timing.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.sample_rate = sample_rate
        self.channels = channels
        self.volume = 1.0

        try:
            import sounddevice as sd
            self._sd = sd
            log.info("[AudioPlayer] Using sounddevice backend")
        except ImportError:
            log.error("[AudioPlayer] sounddevice not installed. Run: pip install sounddevice")
            raise RuntimeError("sounddevice not available")

        self._running = False
        self._play_thread: Optional[threading.Thread] = None
        self._buffer = queue.Queue(maxsize=10)

    def set_volume(self, volume: float):
        """Set master output volume (0.0 to 1.0)."""
        self.volume = max(0.0, min(1.0, volume))

    def _play_loop(self):
        """Play audio chunks from buffer synchronously."""
        log.info("[AudioPlayer] Play loop started")
        
        while self._running:
            try:
                # Get audio data from buffer
                data = self._buffer.get(timeout=0.5)
                
                # Apply volume
                data = data.astype(np.float32) * self.volume
                
                # Play synchronously - blocks until this chunk is done playing
                try:
                    self._sd.play(data, self.sample_rate, blocking=True)
                except Exception as play_err:
                    log.error(f"[AudioPlayer] Playback error: {play_err}")
                
            except queue.Empty:
                continue
            except Exception as e:
                log.debug(f"[AudioPlayer] Play loop error: {e}")
        
        log.info("[AudioPlayer] Play loop stopped")

    def start(self):
        """Start audio playback."""
        log.info("[AudioPlayer] Starting playback")
        self._running = True
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self._play_thread.start()

    def write(self, audio: np.ndarray):
        """Write audio samples to output buffer."""
        if not self._running:
            return
        try:
            self._buffer.put(audio, timeout=0.1)
        except queue.Full:
            log.debug("[AudioPlayer] Buffer full, dropping frame")

    def stop(self):
        """Stop playback."""
        log.info("[AudioPlayer] Stopping...")
        self._running = False
        if self._play_thread:
            self._play_thread.join(timeout=2)
        self._sd.stop()

    @property
    def is_running(self) -> bool:
        return self._running


class DummyAudioPlayer:
    """
    Dummy audio player that discards audio.
    Useful for testing without audio hardware.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.sample_rate = sample_rate
        self.channels = channels
        self.volume = 1.0
        self._running = False
        self._samples_written = 0

    def start(self):
        self._running = True
        log.info("[DummyAudioPlayer] Started (discarding audio)")

    def write(self, audio: np.ndarray):
        if self._running:
            self._samples_written += len(audio)

    def stop(self):
        self._running = False
        log.info(f"[DummyAudioPlayer] Stopped. Samples written: {self._samples_written}")

    @property
    def is_running(self) -> bool:
        return self._running
