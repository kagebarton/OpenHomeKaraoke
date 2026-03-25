#!/usr/bin/env python3
"""
Qwen Pipeline V2 - Simple Test Script

Plays a full song with configurable volumes.
No pitch shifting, no web UI - just playback.

Usage:
    python Qwen_test_pipeline_v2.py /path/to/song/folder
"""

import os
import sys
import time
import logging
import signal

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from qwen import PipelineV2

# =============================================================================
# CONFIGURATION - Edit these values
# =============================================================================

# Volume levels (0.0 to 1.0)
VOCAL_VOLUME = 0.4       # Vocal track volume
NONVOCAL_VOLUME = 1.0    # Nonvocal/instrumental track volume

# Initial pitch (semitones) - currently disabled, but here for future use
PITCH_SEMITONES = 0

# Subtitle delay (seconds)
SUBTITLE_DELAY = -0.8

# =============================================================================


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)


def main():
    if len(sys.argv) < 2:
        print("Usage: python Qwen_test_pipeline_v2.py /path/to/song/folder")
        print("")
        print("The song folder should contain:")
        print("  - video.mp4")
        print("  - vocal.m4a")
        print("  - nonvocal.m4a")
        print("  - subs.srt (optional)")
        sys.exit(1)

    song_folder = sys.argv[1]

    if not os.path.isdir(song_folder):
        log.error(f"Song folder not found: {song_folder}")
        sys.exit(1)

    # Check required files
    required_files = ["video.mp4", "vocal.m4a", "nonvocal.m4a"]
    missing = [f for f in required_files if not os.path.exists(os.path.join(song_folder, f))]
    if missing:
        log.error(f"Missing files: {missing}")
        sys.exit(1)

    log.info("=" * 60)
    log.info("Qwen Pipeline V2 - Full Song Playback Test")
    log.info("=" * 60)
    log.info(f"Song folder: {song_folder}")
    log.info(f"Vocal volume: {VOCAL_VOLUME}")
    log.info(f"Nonvocal volume: {NONVOCAL_VOLUME}")
    log.info("")

    # Handle Ctrl+C gracefully
    pipeline = None

    def signal_handler(sig, frame):
        log.info("\nInterrupted by user")
        if pipeline:
            pipeline.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        # Create and start pipeline
        log.info("Starting pipeline...")
        pipeline = PipelineV2(
            song_folder,
            use_dummy_audio=False,  # Always use real audio output
            fullscreen_video=True
        )

        # Set volumes
        pipeline.set_vocal_volume(VOCAL_VOLUME)
        pipeline.set_nonvocal_volume(NONVOCAL_VOLUME)
        pipeline.set_subtitle_delay(SUBTITLE_DELAY)

        # Start playback
        pipeline.start()
        log.info("Playback started. Press Ctrl+C to stop.")
        log.info("")

        # Give pipeline time to initialize
        time.sleep(0.5)
        
        # Debug: check initial state
        log.info(f"Initial state: running={pipeline.is_running}")
        log.info(f"  audio_mixer.is_running={pipeline.audio_mixer.is_running if pipeline.audio_mixer else 'N/A'}")
        log.info(f"  video_player.is_running()={pipeline.video_player.is_running() if pipeline.video_player else 'N/A'}")

        # Monitor playback
        last_pos = 0
        loop_count = 0
        while pipeline.is_running:
            state = pipeline.state
            time_pos = state.get('video_time') or state.get('audio_time') or 0
            duration = state.get('duration') or 0
            
            loop_count += 1

            # Print progress every second
            if int(time_pos) > int(last_pos):
                if duration > 0:
                    progress = (time_pos / duration) * 100
                    log.info(f"Progress: {time_pos:.1f}s / {duration:.1f}s ({progress:.1f}%)")
                else:
                    log.info(f"Progress: {time_pos:.1f}s")
                last_pos = time_pos

            time.sleep(1)
            
            # Safety: exit after 300 seconds max
            if loop_count > 300:
                log.info("Max playback time reached")
                break

        log.info(f"Playback loop exited. Total iterations: {loop_count}")
        log.info("")
        log.info("Playback completed!")

    except FileNotFoundError as e:
        log.error(f"File error: {e}")
        sys.exit(1)
    except Exception as e:
        log.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if pipeline:
            pipeline.stop()

    log.info("Done.")


if __name__ == "__main__":
    main()
