# Qwen Pipeline V2 - Real-time Karaoke Audio Pipeline

Real-time audio mixing with rubberband pitch shifting and per-track volume control.

**Note:** The pipeline works without rubberband using a fallback pitch-shifting method.
Install rubberband for high-quality, tempo-independent pitch shifting.

## Quick Start

```bash
# 1. Install Python dependencies (works without system libs)
pip install -r qwen/Qwen_requirements_full.txt

# 2. Run the web test UI
python qwen/Qwen_web_test.py /path/to/song/folder --port 5000

# 3. Open browser to http://localhost:5000
```

### Optional: High-Quality Pitch Shifting

For better pitch shifting (tempo-independent):

```bash
sudo apt-get install librubberband-dev libsndfile1-dev portaudio19-dev
pip install rubberband
```

## Features

| Feature | Description |
|---------|-------------|
| **Real-time Pitch** | Change pitch instantly without restarting ffmpeg |
| **Per-track Volume** | Independent vocal/nonvocal volume control |
| **A/V Sync** | Automatic sync monitoring and correction |
| **Web UI** | Simple browser-based control interface |

## Files

| File | Purpose |
|------|---------|
| `Qwen_pipeline_v2.py` | Main pipeline coordinator |
| `Qwen_audio_mixer.py` | Audio mixing + rubberband pitch shift |
| `Qwen_audio_player.py` | Audio output (sounddevice/simpleaudio/pyaudio) |
| `Qwen_video_player.py` | mpv video-only player |
| `Qwen_web_test.py` | Web UI for testing |
| `Qwen_test_pipeline_v2.py` | Command-line test suite |

## Requirements

**Already installed ✓:**
- numpy
- Flask
- sounddevice
- soundfile

**Optional (for high-quality pitch shifting):**
```bash
sudo apt-get install librubberband-dev libsndfile1-dev
pip install rubberband
```

Without rubberband, the pipeline uses a fallback resampling method
for pitch shifting (changes tempo with pitch).

## Usage in Your Code

```python
from qwen import PipelineV2

# Initialize with song folder
pipeline = PipelineV2("/path/to/song/folder", window_id=12345)

# Start playback
pipeline.start()

# Real-time controls (no restart needed!)
pipeline.set_pitch(-2)              # Pitch shift
pipeline.set_vocal_volume(0.5)      # Vocal volume
pipeline.set_nonvocal_volume(0.8)   # Nonvocal volume
pipeline.set_subtitle_delay(-0.5)   # Subtitle sync

# Stop when done
pipeline.stop()
```

## Song Folder Structure

```
/path/to/song/folder/
├── video.mp4       # Video file (required)
├── vocal.m4a       # Vocal track (required)
├── nonvocal.m4a    # Instrumental track (required)
└── subs.srt        # Subtitles (optional)
```
