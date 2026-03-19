import os
import logging
import subprocess
import threading
import time

FIFO_PATH = "/tmp/pikaraoke/ffmpeg_out.mkv"


class FFmpegProcessor:
    def __init__(self, ffmpeg_path="ffmpeg"):
        self.ffmpeg_path = ffmpeg_path
        self.fifo_path = FIFO_PATH
        self.process = None
        self._lock = threading.Lock()

    def _ensure_fifo(self):
        os.makedirs(os.path.dirname(self.fifo_path), exist_ok=True)
        if os.path.exists(self.fifo_path):
            os.remove(self.fifo_path)
        os.mkfifo(self.fifo_path)

    def start(self, params: dict, recreate_fifo=True):
        """Start or restart FFmpeg.

        recreate_fifo=False keeps the existing FIFO open — use this for
        volume-only restarts when VLC is still reading the FIFO so VLC
        does not need to restart.
        """
        with self._lock:
            self._stop_process()
            if recreate_fifo:
                self._ensure_fifo()
            cmd = self._build_command(params)
            logging.info("FFmpeg cmd: " + " ".join(str(x) for x in cmd))
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            threading.Thread(target=self._log_stderr, daemon=True).start()
            # Give FFmpeg a moment to start; it should block on the FIFO
            # write-open until VLC connects.  An immediate exit means an error.
            time.sleep(0.1)
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"FFmpeg exited immediately (code {self.process.returncode}). "
                    "Check debug logs for FFmpeg stderr output."
                )

    def stop(self):
        with self._lock:
            self._stop_process()

    def _stop_process(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None

    def cleanup(self):
        """Stop FFmpeg and remove the FIFO. Call on application exit."""
        self._stop_process()
        if os.path.exists(self.fifo_path):
            try:
                os.remove(self.fifo_path)
            except OSError:
                pass

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def _log_stderr(self):
        try:
            for line in self.process.stderr:
                logging.debug("FFmpeg: " + line.decode(errors="replace").rstrip())
        except Exception:
            pass

    def _build_command(self, p: dict) -> list:
        file_path     = p["file_path"]
        nonvocal_path = p.get("nonvocal_path", "")
        vocal_path    = p.get("vocal_path", "")
        nonvocal_vol  = float(p.get("nonvocal_vol", 1.0))
        vocal_vol     = float(p.get("vocal_vol", 1.0))
        semitones     = int(p.get("semitones", 0))
        start_time    = float(p.get("start_time", 0))

        has_split = bool(nonvocal_path and vocal_path)

        # Zero-overhead path: no split files, no pitch shift — full stream copy
        if not has_split and semitones == 0:
            cmd = [self.ffmpeg_path, "-y"]
            if start_time > 0:
                cmd += ["-ss", str(start_time)]
            cmd += ["-i", file_path]
            cmd += ["-map", "0:v", "-map", "0:a", "-map", "0:s?"]
            cmd += ["-vcodec", "copy", "-acodec", "copy", "-scodec", "copy"]
            cmd += ["-f", "matroska", self.fifo_path]
            return cmd

        cmd = [self.ffmpeg_path, "-y"]

        # Primary input (video + original audio + subtitles)
        if start_time > 0:
            cmd += ["-ss", str(start_time)]
        cmd += ["-i", file_path]

        # Split audio inputs
        if has_split:
            if start_time > 0:
                cmd += ["-ss", str(start_time)]
            cmd += ["-i", nonvocal_path]
            if start_time > 0:
                cmd += ["-ss", str(start_time)]
            cmd += ["-i", vocal_path]

        # Build filter_complex
        filters = []
        if has_split:
            filters.append(f"[1:a]volume={nonvocal_vol}[a_nv]")
            filters.append(f"[2:a]volume={vocal_vol}[a_v]")
            filters.append("[a_nv][a_v]amix=inputs=2:normalize=0[a_mixed]")
            audio_label = "a_mixed"
        else:
            audio_label = "0:a"

        if semitones != 0:
            pitch_factor = 2 ** (semitones / 12.0)
            filters.append(
                f"[{audio_label}]arubberband=pitch={pitch_factor:.6f}"
                f":tempo=1:transients=2:detector=2:phase=1:window=0"
                f":smoothing=0:formant=0:pitchq=2:channels=2[a_out]"
            )
            audio_label = "a_out"

        cmd += ["-filter_complex", ";".join(filters)]
        cmd += ["-map", "0:v:0"]
        cmd += ["-map", f"[{audio_label}]"]
        cmd += ["-map", "0:s?"]
        cmd += ["-vcodec", "copy"]
        cmd += ["-acodec", "pcm_s16le"]  # lossless; FIFO is RAM-only so size is irrelevant
        cmd += ["-scodec", "copy"]
        cmd += ["-f", "matroska", self.fifo_path]
        return cmd
