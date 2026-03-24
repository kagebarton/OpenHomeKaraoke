"""
test.py - FFmpeg preprocessor test
-----------------------------------
Muxes video.mp4 + vocal.m4a + nonvocal.m4a, applies per-track rubberband
pitch shift, and streams mpegts over UDP to mpv for playback.

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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SONG_FOLDER = "."
VIDEO    = os.path.join(SONG_FOLDER, "video.mp4")
VOCAL    = os.path.join(SONG_FOLDER, "vocal.m4a")
NONVOCAL = os.path.join(SONG_FOLDER, "nonvocal.m4a")
SUBS     = os.path.join(SONG_FOLDER, "subs.srt")

UDP_PORT = 9000
MPV_IPC  = "/tmp/mpv-karaoke"

# Negative = subtitles appear earlier. mpv uses seconds (float).
SUB_DELAY_S = -0.8

PITCH_SEMITONES = -2
PITCH_RATIO     = 2 ** (PITCH_SEMITONES / 12)   # ≈ 0.890899

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def build_ffmpeg_cmd() -> list[str]:
    """
    FFmpeg sends mpegts over UDP at realtime speed (-re).
    pkt_size=1316 = 7 x 188-byte mpegts packets, the standard UDP MTU.

    Filter graph:
      vocal    -> volume=0.4 -> rubberband (formant=preserved, pitchq=quality)
      nonvocal -> rubberband (pitchq=consistency) -> volume=1.0
      both     -> amix -> [out]
    """
    filter_complex = (
        f"[1:a]rubberband=pitch={PITCH_RATIO:.7f}:window=long:pitchq=quality"
        f":transients=crisp:detector=compound:formant=preserved,"
        f"volume=0.2[vocal];"

        f"[2:a]rubberband=pitch={PITCH_RATIO:.7f}:window=standard:pitchq=consistency"
        f":transients=crisp:detector=compound:formant=shifted,"
        f"volume=1.0[nonvocal];"

        f"[vocal][nonvocal]amix=inputs=2:normalize=0[out]"
    )
    return [
        "ffmpeg", "-y",
        "-re",                  # send at realtime speed — don't flood the buffer
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


def build_mpv_cmd() -> list[str]:
    """
    mpv binds the UDP port and receives the stream.
    --no-cache         disables buffering — this is a live stream
    --demuxer=mpegts   explicit demuxer, no sniffing needed
    --sub-file         load subtitles from disk (independent of stream)
    --sub-delay        shift subs in seconds; negative = appear earlier
    --input-ipc-server exposes a JSON socket for runtime control
    """
    return [
        "mpv",
        f"udp://@:{UDP_PORT}",
        "--no-cache",
        f"--sub-file={SUBS}",
        f"--sub-delay={SUB_DELAY_S}",
        f"--input-ipc-server={MPV_IPC}",
    ]

# ---------------------------------------------------------------------------
# Optional: runtime IPC control (for future use)
# ---------------------------------------------------------------------------

def mpv_command(cmd: dict) -> None:
    """Send a JSON command to the running mpv instance over the IPC socket."""
    import socket, json
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(MPV_IPC)
            s.sendall((json.dumps(cmd) + "\n").encode())
    except Exception as e:
        print(f"[ipc]    Error: {e}")

# Example usage (not called during normal playback):
#   mpv_command({"command": ["sub-step", 0.1]})               # nudge subs +100ms
#   mpv_command({"command": ["set_property", "volume", 80]})  # set volume to 80%
#   mpv_command({"command": ["set_property", "sub-delay", -2.0]})  # adjust delay live

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
    print(f"[main]   IPC   : {MPV_IPC}")
    print()

    ffmpeg_proc: subprocess.Popen | None = None
    mpv_proc:    subprocess.Popen | None = None

    def _shutdown(sig=None, frame=None):
        print("\n[main]   Shutting down...")
        for proc, name in [(ffmpeg_proc, "ffmpeg"), (mpv_proc, "mpv")]:
            if proc and proc.poll() is None:
                print(f"[main]   Terminating {name} (pid {proc.pid})")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)

    try:
        # mpv first — binds the UDP port and waits for packets
        mpv_cmd = build_mpv_cmd()
        print("[mpv]    " + " ".join(mpv_cmd))
        mpv_proc = subprocess.Popen(mpv_cmd)

        # Give mpv a moment to bind before FFmpeg starts sending
        time.sleep(1.0)

        # FFmpeg second — starts blasting UDP packets at realtime speed
        ffmpeg_cmd = build_ffmpeg_cmd()
        print("[ffmpeg] " + " ".join(ffmpeg_cmd))
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd)

        print()
        print("[main]   Pipeline running.  Press Ctrl-C to stop.")

        ffmpeg_proc.wait()
        rc = ffmpeg_proc.returncode
        if rc == 0:
            print("[ffmpeg] Finished successfully.")
        else:
            print(f"[ffmpeg] Exited with code {rc}.")

        mpv_proc.wait()

    except Exception as exc:
        print(f"[error]  {exc}")
        _shutdown()


if __name__ == "__main__":
    main()
