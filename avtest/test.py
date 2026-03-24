"""
test.py - FFmpeg + python-mpv karaoke pipeline test
-----------------------------------------------------
Muxes video.mp4 + vocal.m4a + nonvocal.m4a, applies per-track rubberband
pitch shift, streams mpegts over UDP to python-mpv for playback.

Hard-coded parameters:
  - nonvocal audio : 100% volume
  - vocal audio    : 40% volume
  - rubberband     : -2 semitones, per-track optimised settings
  - output format  : mpegts, AAC audio, video passthrough
  - subtitles      : subs.srt with configurable delay in seconds
"""

import os
import sys
import time
import signal
import subprocess

import mpv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SONG_FOLDER = "."
VIDEO    = os.path.join(SONG_FOLDER, "video.mp4")
VOCAL    = os.path.join(SONG_FOLDER, "vocal.m4a")
NONVOCAL = os.path.join(SONG_FOLDER, "nonvocal.m4a")
SUBS     = os.path.join(SONG_FOLDER, "subs.srt")

UDP_PORT = 9000

# Negative = subtitles appear earlier. python-mpv uses seconds (float).
SUB_DELAY_S = -1.5

PITCH_SEMITONES = -2
PITCH_RATIO     = 2 ** (PITCH_SEMITONES / 12)   # ≈ 0.890899

# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------

def build_ffmpeg_cmd() -> list[str]:
    """
    FFmpeg sends mpegts over UDP at realtime speed (-re).
    pkt_size=1316 = 7 x 188-byte mpegts packets, the correct UDP MTU.

    Filter graph:
      vocal    -> rubberband (formant=preserved, pitchq=quality) -> volume=0.4
      nonvocal -> rubberband (pitchq=consistency) -> volume=1.0
      both     -> amix -> [out]
    """
    filter_complex = (
        f"[1:a]rubberband=pitch={PITCH_RATIO:.7f}:window=long:pitchq=quality"
        f":transients=crisp:detector=compound:formant=preserved,"
        f"volume=0.4[vocal];"

        f"[2:a]rubberband=pitch={PITCH_RATIO:.7f}:window=standard:pitchq=consistency"
        f":transients=crisp:detector=compound:formant=shifted,"
        f"volume=1.0[nonvocal];"

        f"[vocal][nonvocal]amix=inputs=2:normalize=0[out]"
    )
    return [
        "ffmpeg", "-y",
        "-re",                  # realtime — don't flood the UDP buffer
        "-i", VIDEO,
        "-i", VOCAL,
        "-i", NONVOCAL,
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[out]",
        "-c:v", "copy",         # no re-encode
        "-c:a", "aac",
        "-b:a", "192k",
        "-f", "mpegts",
        f"udp://127.0.0.1:{UDP_PORT}?pkt_size=1316",
    ]


# ---------------------------------------------------------------------------
# python-mpv player
# ---------------------------------------------------------------------------

def create_player() -> mpv.MPV:
    player = mpv.MPV(
        cache=False,
        demuxer='lavf',
        demuxer_lavf_format='mpegts',
    )

    player.sub_delay = SUB_DELAY_S
    player.msg_level = 'all=warn'

    @player.event_callback('start-file')
    def on_start(event):
        # sub_add must run off the event loop thread
        import threading
        def _load_subs():
            time.sleep(0.5)   # wait for stream to be active
            try:
                player.sub_add(SUBS)
                print(f"\n[mpv]    Subtitles loaded: {SUBS}")
            except Exception as e:
                print(f"\n[mpv]    sub_add failed: {e}")
        threading.Thread(target=_load_subs, daemon=True).start()

    @player.event_callback('end-file')
    def on_end(event):
        print(f"\n[mpv]    Playback ended — reason: {event.reason}")   # object, not dict

    @player.property_observer('time-pos')
    def on_time(name, value):
        if value is not None:
            print(f"[mpv]    time-pos: {value:.2f}s", end='\r')

    return player


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    missing = [f for f in (VIDEO, VOCAL, NONVOCAL, SUBS) if not os.path.exists(f)]
    if missing:
        for f in missing:
            print(f"[error]  Missing file: {f}")
        sys.exit(1)

    print(f"[main]   Pitch : {PITCH_SEMITONES:+d} semitones (ratio={PITCH_RATIO:.7f})")
    print(f"[main]   Subs  : {SUBS}  delay={SUB_DELAY_S:+.3f}s")
    print(f"[main]   UDP   : 127.0.0.1:{UDP_PORT}")
    print()

    player = create_player()
    ffmpeg_proc: subprocess.Popen | None = None

    def _shutdown(sig=None, frame=None):
        print("\n[main]   Shutting down...")
        if ffmpeg_proc and ffmpeg_proc.poll() is None:
            print(f"[main]   Terminating ffmpeg (pid {ffmpeg_proc.pid})")
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
        player.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)

    try:
        # mpv first — start listening on the UDP port
        stream_url = f"udp://@:{UDP_PORT}"
        print(f"[mpv]    Opening {stream_url}")
        player.play(stream_url)

        # Give mpv a moment to bind before FFmpeg starts sending
        time.sleep(1.0)

        # FFmpeg second — start blasting UDP packets at realtime speed
        ffmpeg_cmd = build_ffmpeg_cmd()
        print("[ffmpeg] " + " ".join(ffmpeg_cmd))
        print()
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd)

        print("[main]   Pipeline running.  Press Ctrl-C to stop.")

        # Wait for FFmpeg to finish
        ffmpeg_proc.wait()
        rc = ffmpeg_proc.returncode
        if rc == 0:
            print("\n[ffmpeg] Finished successfully.")
        else:
            print(f"\n[ffmpeg] Exited with code {rc}.")

        # Give mpv a moment to drain before we shut it down
        time.sleep(2.0)

    except Exception as exc:
        print(f"[error]  {exc}")

    finally:
        _shutdown()


if __name__ == "__main__":
    main()
