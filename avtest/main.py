"""
main.py
-------
Entry point for the karaoke test project.

Startup sequence:
  1. Initialize pygame and create the display window
  2. Get the X11 window ID so mpv can render into it
  3. Create the pipeline (does not start processes yet)
  4. Start the Flask server in a background thread
  5. Start the pipeline (launches mpv then FFmpeg)
  6. Run the pygame event loop (keeps the window alive)

Usage:
  python main.py [--song-folder ./song] [--port 5000] [--windowed]
"""

import os
import sys
import signal
import logging
import argparse
import threading

import pygame

from pipeline import Pipeline
import server

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

WINDOW_W = 1280
WINDOW_H = 720


def parse_args():
    p = argparse.ArgumentParser(description="Karaoke test — FFmpeg + mpv + pygame")
    p.add_argument("--song-folder", default="./song",
                   help="Folder containing video.mp4, vocal.m4a, nonvocal.m4a, subs.srt")
    p.add_argument("--port", type=int, default=5000,
                   help="Flask web UI port (default: 5000)")
    p.add_argument("--windowed", action="store_true",
                   help="Run in a window instead of fullscreen")
    return p.parse_args()


def main():
    args = parse_args()

    # ------------------------------------------------------------------
    # 1. Pygame window — must happen in main thread before anything else
    # ------------------------------------------------------------------
    pygame.init()
    pygame.display.set_caption("Karaoke Test")
    pygame.mouse.set_visible(False)

    if args.windowed:
        screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)
    else:
        info = pygame.display.Info()
        screen = pygame.display.set_mode(
            (info.current_w, info.current_h), pygame.FULLSCREEN
        )

    screen.fill((0, 0, 0))
    pygame.display.flip()

    window_id = pygame.display.get_wm_info()["window"]
    log.info(f"[main]    Pygame window ID: {window_id}")

    # ------------------------------------------------------------------
    # 2. Pipeline
    # ------------------------------------------------------------------
    song_folder = os.path.abspath(args.song_folder)
    pipe = Pipeline(song_folder=song_folder, window_id=window_id)

    # ------------------------------------------------------------------
    # 3. Flask server (background thread)
    # ------------------------------------------------------------------
    server.start(pipe, port=args.port)
    log.info(f"[main]    Web UI → http://localhost:{args.port}")

    # ------------------------------------------------------------------
    # 4. Start pipeline (mpv then FFmpeg)
    # ------------------------------------------------------------------
    try:
        pipe.start()
    except FileNotFoundError as e:
        log.error(f"[main]    {e}")
        pygame.quit()
        sys.exit(1)

    # ------------------------------------------------------------------
    # 5. Signal handler
    # ------------------------------------------------------------------
    def _shutdown(sig=None, frame=None):
        log.info("[main]    Shutting down...")
        pipe.stop()
        pygame.quit()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ------------------------------------------------------------------
    # 6. Pygame event loop — keeps the window alive and handles Escape
    # ------------------------------------------------------------------
    log.info("[main]    Running. Press Escape or close window to quit.")
    clock = pygame.time.Clock()

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                _shutdown()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    _shutdown()
                elif event.key == pygame.K_SPACE:
                    # Space bar also toggles pause
                    threading.Thread(target=pipe.toggle_pause, daemon=True).start()
            elif event.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(
                    event.size, pygame.RESIZABLE
                )

        # mpv owns the window content — pygame just keeps the window alive
        clock.tick(30)


if __name__ == "__main__":
    main()
