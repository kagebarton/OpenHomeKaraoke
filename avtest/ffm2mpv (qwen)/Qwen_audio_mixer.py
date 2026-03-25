"""
audio_mixer.py
--------------
Real-time audio mixer with rubberband pitch shifting.

Handles:
- Decoding vocal/nonvocal audio files via ffmpeg
- Real-time pitch shifting via rubberband library
- Per-track volume control
- Stereo mixing to output buffer
"""

import os
import subprocess
import threading
import queue
import logging
import numpy as np
from typing import Optional, Callable

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHANNELS = 1  # Mono input tracks
BUFFER_SIZE = 1024  # Samples per chunk


class RubberbandProcessor:
    """
    Audio passthrough - no pitch shifting.
    
    Pitch shifting has been disabled. This class now just passes
    audio through unchanged.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.sample_rate = sample_rate
        self.channels = channels
        self.pitch_semitones = 0.0

    def set_pitch(self, semitones: float):
        """Pitch shifting is disabled."""
        pass

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Pass audio through unchanged."""
        return audio

    def reset(self):
        """No-op."""
        pass

    def close(self):
        """No-op."""
        pass


class AudioDecoder:
    """Decodes audio file to PCM via ffmpeg pipe."""

    def __init__(self, file_path: str, sample_rate: int = SAMPLE_RATE):
        self.file_path = file_path
        self.sample_rate = sample_rate
        self._proc: Optional[subprocess.Popen] = None
        self._buffer = queue.Queue(maxsize=100)
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        """Start decoding audio file."""
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Audio file not found: {self.file_path}")

        log.info(f"[AudioDecoder] Decoding: {self.file_path} at {self.sample_rate} Hz")
        
        cmd = [
            "ffmpeg", "-y",
            "-i", self.file_path,
            "-f", "f32le",
            "-acodec", "pcm_f32le",
            "-ar", str(self.sample_rate),
            "-ac", "1",  # Mono
            "-"
        ]

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1024 * 1024
        )

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        """Read PCM data from ffmpeg stdout."""
        try:
            while self._running:
                chunk = self._proc.stdout.read(BUFFER_SIZE * 4)  # 4 bytes per float32
                if not chunk:
                    break
                self._buffer.put(chunk, timeout=1.0)
        except Exception as e:
            log.error(f"Decoder read error: {e}")
        finally:
            self._running = False

    def read(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """Read next audio chunk as numpy array."""
        try:
            chunk = self._buffer.get(timeout=timeout)
            return np.frombuffer(chunk, dtype=np.float32).reshape(-1, 1)
        except queue.Empty:
            return None

    def is_eof(self) -> bool:
        """Check if decoder has reached end of file."""
        return self._proc is not None and self._proc.poll() is not None

    def stop(self):
        """Stop decoding."""
        self._running = False
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


class AudioMixer:
    """
    Real-time audio mixer for vocal/nonvocal tracks.

    Features:
    - Independent pitch shifting per track
    - Independent volume control per track
    - Stereo mixing (vocal center, nonvocal center)
    - Callback-based output for low-latency playback
    """

    def __init__(
        self,
        vocal_path: str,
        nonvocal_path: str,
        sample_rate: int = SAMPLE_RATE,
        output_callback: Optional[Callable[[np.ndarray], None]] = None
    ):
        self.sample_rate = sample_rate
        self.output_callback = output_callback

        # Create decoders
        self.vocal_decoder = AudioDecoder(vocal_path, sample_rate)
        self.nonvocal_decoder = AudioDecoder(nonvocal_path, sample_rate)

        # Create pitch shifters
        self.vocal_rb = RubberbandProcessor(sample_rate, CHANNELS)
        self.nonvocal_rb = RubberbandProcessor(sample_rate, CHANNELS)

        # Volume levels (0.0 to 1.0)
        self.vocal_vol = 0.4
        self.nonvocal_vol = 1.0

        # Mixer state
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Sync tracking
        self._samples_played = 0

    def set_vocal_pitch(self, semitones: float):
        """Set vocal track pitch in semitones."""
        self.vocal_rb.set_pitch(semitones)

    def set_nonvocal_pitch(self, semitones: float):
        """Set nonvocal track pitch in semitones."""
        self.nonvocal_rb.set_pitch(semitones)

    def set_pitch(self, semitones: float):
        """Set both tracks to same pitch."""
        self.set_vocal_pitch(semitones)
        self.set_nonvocal_pitch(semitones)

    def set_vocal_volume(self, volume: float):
        """Set vocal volume (0.0 to 1.0)."""
        with self._lock:
            self.vocal_vol = max(0.0, min(1.0, volume))

    def set_nonvocal_volume(self, volume: float):
        """Set nonvocal volume (0.0 to 1.0)."""
        with self._lock:
            self.nonvocal_vol = max(0.0, min(1.0, volume))

    def start(self):
        """Start mixing and playback."""
        log.info("[AudioMixer] Starting...")
        log.info(f"[AudioMixer] Sample rate: {self.sample_rate} Hz, Buffer size: {BUFFER_SIZE} samples")

        self.vocal_decoder.start()
        self.nonvocal_decoder.start()

        self._running = True
        self._thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._thread.start()

    def _mix_loop(self):
        """Main mixing loop - reads, processes, mixes, outputs."""
        log.info("[AudioMixer] Mix loop started")

        while self._running:
            # Read from both decoders
            vocal_chunk = self.vocal_decoder.read(timeout=0.1)
            nonvocal_chunk = self.nonvocal_decoder.read(timeout=0.1)

            # Handle EOF
            if vocal_chunk is None and self.vocal_decoder.is_eof():
                if self.nonvocal_decoder.is_eof():
                    log.info("[AudioMixer] Both tracks reached EOF")
                    break
                # Vocal ended first, continue with nonvocal only
                vocal_chunk = np.zeros((BUFFER_SIZE, 1), dtype=np.float32)

            if nonvocal_chunk is None and self.nonvocal_decoder.is_eof():
                if self.vocal_decoder.is_eof():
                    break
                # Nonvocal ended first, continue with vocal only
                nonvocal_chunk = np.zeros((BUFFER_SIZE, 1), dtype=np.float32)

            if vocal_chunk is None or nonvocal_chunk is None:
                continue

            # Apply pitch shifting (currently passthrough)
            vocal_shifted = self.vocal_rb.process(vocal_chunk)
            nonvocal_shifted = self.nonvocal_rb.process(nonvocal_chunk)

            # Apply volume and mix
            with self._lock:
                mixed = (
                    vocal_shifted * self.vocal_vol +
                    nonvocal_shifted * self.nonvocal_vol
                )

            # Clip to prevent distortion
            mixed = np.clip(mixed, -1.0, 1.0)

            # Send to output (with flow control)
            if self.output_callback:
                self.output_callback(mixed)

            self._samples_played += len(mixed)

        log.info(f"[AudioMixer] Mix loop finished. Total samples: {self._samples_played}")

    def stop(self):
        """Stop mixing."""
        log.info("[AudioMixer] Stopping...")
        self._running = False

        self.vocal_decoder.stop()
        self.nonvocal_decoder.stop()

        self.vocal_rb.close()
        self.nonvocal_rb.close()

        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def time_position(self) -> float:
        """Current playback position in seconds."""
        return self._samples_played / self.sample_rate
