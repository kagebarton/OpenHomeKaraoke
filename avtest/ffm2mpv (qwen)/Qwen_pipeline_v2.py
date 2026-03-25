"""
pipeline_v2.py
--------------
Main pipeline coordinating audio mixer, audio player, and video player.

Architecture:
- Audio: ffmpeg decode → rubberband pitch shift → volume mix → output
- Video: mpv video-only playback with subtitle support
- Sync: Both start together, audio leads (lower latency)

Features:
- Real-time pitch shifting (no restart)
- Real-time volume control (no restart)
- A/V sync monitoring and correction
"""

import os
import time
import logging
import threading
from typing import Optional

from .Qwen_audio_mixer import AudioMixer
from .Qwen_audio_player import AudioPlayer, DummyAudioPlayer
from .Qwen_video_player import VideoPlayer

log = logging.getLogger(__name__)

# Sync settings
SYNC_CHECK_INTERVAL = 1.0  # Check A/V sync every second
SYNC_TOLERANCE = 0.3  # Max allowed drift in seconds
SYNC_CORRECTION_STEP = 0.1  # How much to adjust video per correction


class PipelineV2:
    """
    Karaoke pipeline v2 with real-time audio control.

    Unlike v1:
    - Pitch changes are instant (rubberband, no ffmpeg restart)
    - Volume changes are instant (mixer, no ffmpeg restart)
    - Video is separate from audio (mpv video-only)
    """

    def __init__(
        self,
        song_folder: str,
        window_id: Optional[int] = None,
        use_dummy_audio: bool = False,
        fullscreen_video: bool = False
    ):
        self.song_folder = song_folder
        self.window_id = window_id
        self.fullscreen_video = fullscreen_video
        self._use_dummy_audio = use_dummy_audio

        # Song files
        self.video_path = os.path.join(song_folder, "video.mp4")
        self.vocal_path = os.path.join(song_folder, "vocal.m4a")
        self.nonvocal_path = os.path.join(song_folder, "nonvocal.m4a")
        self.subtitle_path = os.path.join(song_folder, "subs.srt")

        # Check for subtitle file
        if not os.path.exists(self.subtitle_path):
            self.subtitle_path = None

        # State
        self.pitch_semitones = 0.0  # Pitch shifting disabled
        self.vocal_vol = 0.4
        self.nonvocal_vol = 1.0
        self.sub_delay = -0.8

        # Components
        self.audio_mixer: Optional[AudioMixer] = None
        self.audio_player: Optional[AudioPlayer] = None
        self.video_player: Optional[VideoPlayer] = None

        # Sync monitoring
        self._sync_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        """Start audio and video playback."""
        log.info("[PipelineV2] Starting...")

        # Validate files
        missing = []
        for f in [self.video_path, self.vocal_path, self.nonvocal_path]:
            if not os.path.exists(f):
                missing.append(f)
        if missing:
            raise FileNotFoundError(f"Missing song files: {missing}")

        # Create audio player
        if self._use_dummy_audio:
            log.warning("[PipelineV2] Using dummy audio (discarding)")
            self.audio_player = DummyAudioPlayer()
        else:
            self.audio_player = AudioPlayer()

        # Create audio mixer with callback to player
        self.audio_mixer = AudioMixer(
            vocal_path=self.vocal_path,
            nonvocal_path=self.nonvocal_path,
            output_callback=self.audio_player.write
        )

        # Create video player
        self.video_player = VideoPlayer(
            video_path=self.video_path,
            subtitle_path=self.subtitle_path,
            window_id=self.window_id,
            fullscreen=self.fullscreen_video
        )

        # Set initial state
        self.audio_mixer.set_pitch(self.pitch_semitones)
        self.audio_mixer.set_vocal_volume(self.vocal_vol)
        self.audio_mixer.set_nonvocal_volume(self.nonvocal_vol)

        # Start playback
        self._running = True
        log.info("[PipelineV2] Starting audio player...")

        self.audio_player.start()
        log.info("[PipelineV2] Audio player started")
        
        log.info("[PipelineV2] Starting audio mixer...")
        self.audio_mixer.start()
        log.info("[PipelineV2] Audio mixer started")

        # Small delay to let audio buffer fill
        log.info("[PipelineV2] Sleeping 0.2s...")
        time.sleep(0.2)
        log.info("[PipelineV2] Woke up from sleep")
        
        log.info("[PipelineV2] Starting video player...")
        self.video_player.start()
        log.info("[PipelineV2] Video player started")

        # Start sync monitoring
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

        log.info("[PipelineV2] Started")

    def _sync_loop(self):
        """Monitor A/V sync drift (no correction - just logging)."""
        log.info("[PipelineV2] Sync monitor started")

        while self._running:
            time.sleep(SYNC_CHECK_INTERVAL)

            if not self.audio_mixer or not self.video_player:
                continue

            if not self.audio_mixer.is_running:
                break

            if not self.video_player.is_running():
                break

            # Get positions
            audio_pos = self.audio_mixer.time_position
            video_pos = self.video_player.get_time_pos()

            if audio_pos is None or video_pos is None:
                continue

            # Log sync status (no automatic correction)
            drift = audio_pos - video_pos
            if abs(drift) > 1.0:  # Only log if more than 1 second drift
                log.debug(f"[PipelineV2] A/V sync: audio={audio_pos:.2f}s, video={video_pos:.2f}s, drift={drift:.2f}s")

    def stop(self):
        """Stop playback."""
        log.info("[PipelineV2] Stopping...")
        self._running = False

        if self.audio_mixer:
            self.audio_mixer.stop()

        if self.audio_player:
            self.audio_player.stop()

        if self.video_player:
            self.video_player.stop()

        if self._sync_thread:
            self._sync_thread.join(timeout=2)

        log.info("[PipelineV2] Stopped")

    # -------------------------------------------------------------------------
    # Real-time controls (no restart needed)
    # -------------------------------------------------------------------------

    def set_pitch(self, semitones: float):
        """Change pitch in semitones. Instant effect."""
        self.pitch_semitones = float(semitones)
        if self.audio_mixer:
            self.audio_mixer.set_pitch(semitones)
        log.info(f"[PipelineV2] Pitch set to {semitones:+.1f} semitones")

    def set_vocal_volume(self, volume: float):
        """Change vocal volume (0.0 to 1.0). Instant effect."""
        self.vocal_vol = float(volume)
        if self.audio_mixer:
            self.audio_mixer.set_vocal_volume(volume)
        log.info(f"[PipelineV2] Vocal volume set to {volume:.2f}")

    def set_nonvocal_volume(self, volume: float):
        """Change nonvocal volume (0.0 to 1.0). Instant effect."""
        self.nonvocal_vol = float(volume)
        if self.audio_mixer:
            self.audio_mixer.set_nonvocal_volume(volume)
        log.info(f"[PipelineV2] Nonvocal volume set to {volume:.2f}")

    def set_subtitle_delay(self, delay: float):
        """Change subtitle delay in seconds."""
        self.sub_delay = float(delay)
        if self.video_player:
            self.video_player.set_subtitle_delay(delay)
        log.info(f"[PipelineV2] Subtitle delay set to {delay:.2f}s")

    def toggle_pause(self) -> bool:
        """Toggle pause. Returns True if now paused."""
        if self.video_player:
            return self.video_player.toggle_pause()
        return False

    def pause(self):
        """Pause playback."""
        if self.video_player:
            self.video_player.pause()

    def resume(self):
        """Resume playback."""
        if self.video_player:
            self.video_player.resume()

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Check if pipeline is running."""
        return (
            self._running and
            self.audio_mixer and
            self.audio_mixer.is_running and
            self.video_player and
            self.video_player.is_running()
        )

    @property
    def state(self) -> dict:
        """Get current pipeline state."""
        state = {
            "pitch_semitones": self.pitch_semitones,
            "vocal_vol": round(self.vocal_vol, 2),
            "nonvocal_vol": round(self.nonvocal_vol, 2),
            "sub_delay": round(self.sub_delay, 2),
            "running": self.is_running,
        }

        if self.audio_mixer:
            state["audio_time"] = round(self.audio_mixer.time_position, 2)

        if self.video_player:
            vp_state = self.video_player.state
            state.update({
                "video_time": vp_state.get("time_pos"),
                "duration": vp_state.get("duration"),
                "paused": vp_state.get("paused"),
            })

        return state

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
