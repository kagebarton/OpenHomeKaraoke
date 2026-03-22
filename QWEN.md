# OpenHomeKaraoke

A fork of xuancong84's PiKaraoke (2022), enhanced with additional features for home karaoke entertainment.

## Project Overview

OpenHomeKaraoke is a Python-based karaoke system that runs on Linux, macOS, Windows, and Raspberry Pi. It provides a web-based interface for searching, downloading, and playing karaoke songs from YouTube and other video sites, with advanced features like vocal removal, pitch shifting, and subtitle synchronization.

### Key Features

- **Vocal Removal**: Demucs-based DNN vocal splitter (CPU/GPU supported) or traditional stereo subtraction
- **Volume Normalization**: All songs play at consistent volume levels
- **Multi-language Support**: UI and subtitles in 100+ languages with automatic transliteration
- **Smart Subtitle Handling**: Auto-retrieval, language selection, and per-song delay memory
- **Web Interface**: Mobile/desktop responsive UI for queue management and song search
- **Voice Recognition**: OpenAI Whisper-based speech recognition for song search
- **Persistent Settings**: Config file (`pikaraoke.cfg`) for system-wide preferences
- **Stream-to-HTTP**: Cast to any device with a web browser

## Project Structure

```
OpenHomeKaraoke/
├── app.py              # Main Flask web application
├── karaoke.py          # Core karaoke logic, player control, config management
├── constants.py        # Version and media type constants
├── vocal_splitter.py   # Demucs-based vocal separation
├── requirements.txt    # Python dependencies
├── pikaraoke.cfg       # Persistent configuration file
├── run.sh              # Launch script (Linux/macOS)
├── templates/          # Jinja2 HTML templates
├── static/             # CSS, JavaScript, images
├── lang/               # Translation files (100+ languages)
├── lib/                # Utility modules
│   ├── get_platform.py # Platform detection, language handling
│   ├── vlcclient.py    # VLC player control
│   ├── omxclient.py    # OMX player control (Raspberry Pi)
│   └── NLP.py          # Natural language processing
├── models/             # Demucs ML models (git-ignored)
└── scripts/            # Setup and utility scripts
```

## Building and Running

### Prerequisites

- **Python**: 3.8+ (3.12/3.13 supported with pygame-ce)
- **Conda**: Anaconda3 or Miniconda for environment management
- **System Dependencies**: VLC, ffmpeg, deno, git
- **PyTorch**: Install separately for GPU vocal splitting (https://pytorch.org)

### Installation

```bash
# Clone repository
git clone https://github.com/kagebarton/OpenHomeKaraoke.git
cd OpenHomeKaraoke

# Create conda environment and install PyTorch (see pytorch.org for CUDA options)
conda create -n karaoke python=3.11
conda activate karaoke
pip install torch torchaudio  # Add CUDA flags if needed

# Install project dependencies
pip install -r requirements.txt

# Create song directories
mkdir -p ~/pikaraoke-songs/{vocal,nonvocal}
```

### Launch

**Linux/macOS:**
```bash
./run.sh
# Or directly:
python3 app.py
```

**Raspberry Pi:**
```bash
sudo env PATH=/path/to/conda/bin:$PATH python3 app.py
```

**Windows:**
```powershell
# In Anaconda Prompt
python3 app.py -d <your-song-folder>
```

### Command Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `-p, --port` | HTTP port | 5000 |
| `-d, --download-path` | Song download directory | `~/pikaraoke-songs` |
| `-v, --volume` | Initial volume (millibels) | 0 |
| `-s, --splash-delay` | Splash screen delay (seconds) | 3 |
| `-l, --log-level` | Logging level (10-50) | 20 |
| `-w, --windowed` | Windowed mode | fullscreen |
| `--ssl` | Use HTTPS | HTTP |
| `--admin-password` | Admin password for UI restrictions | None |
| `--cloud` | Cloud URL for GPU vocal splitting/ASR | local |
| `--browser-cookies` | Browser cookies for yt-dlp | auto |

**Note**: Most settings (language, admin password, overlay) are now managed via `pikaraoke.cfg`.

## Configuration

### pikaraoke.cfg

Settings are persisted in `pikaraoke.cfg` (INI format, safe to hand-edit when app is not running):

```ini
[pikaraoke]
save_play_settings = true       # Remember per-song audio/subtitle delays
default_subtitle_delay = -0.8   # Default subtitle offset (seconds)
normalize_vol = true            # Normalize volume across songs
use_dnn_vocal = true            # Use Demucs DNN for vocal splitting
language =                      # UI language (empty = system locale)
admin_password =                # Admin password (empty = no restrictions)
show_overlay = true             # Show QR code/IP overlay on video
```

### CONFIG_DEFAULTS (karaoke.py)

To change defaults for new installations, edit the `CONFIG_DEFAULTS` dict in `karaoke.py`:

```python
CONFIG_DEFAULTS = {
    'save_play_settings': True,
    'default_subtitle_delay': -0.8,
    'normalize_vol': True,
    'use_dnn_vocal': True,
    'language': '',
    'admin_password': '',
    'show_overlay': True,
}
```

## Development Conventions

### Code Style

- **Python**: Follows existing indentation (tabs in `karaoke.py`, spaces in `app.py`)
- **Naming**: `snake_case` for functions/variables, `CamelCase` for classes
- **Comments**: Minimal inline comments; docstrings for public functions
- **Error Handling**: Try/except with logging warnings for config parsing

### Architecture

- **Flask App** (`app.py`): Web routes, WebSocket handlers, template rendering
- **Karaoke Class** (`karaoke.py`): Player control, config management, song queue
- **VLC/OMX Clients** (`lib/`): Platform-specific player wrappers
- **Vocal Splitter** (`vocal_splitter.py`): Separate process for Demucs inference

### Template Structure (SPA Design)

The web UI uses a **single-page application (SPA)** architecture:

- **`index.html`**: Main shell with navbar, loads fragments dynamically via AJAX
- **`f_*.html` fragments**: `f_home.html`, `f_queue.html`, `f_info.html`, etc.
  - Loaded into `#container` div when user clicks navigation
  - Preserve WebSocket connection and JavaScript state
  - No full page reloads

**Note**: Duplicate full-page templates (`home.html`, `queue.html`, `info.html`, `search.html`, `edit.html`, `login.html`) were removed. All navigation goes through fragments.

**Trade-off**: Direct URLs like `/home`, `/queue` no longer work. Users must start at `/` and navigate via the navbar.

### Testing Practices

- Manual testing across platforms (Ubuntu, macOS, Windows, Raspberry Pi)
- Config file round-trip testing (edit → load → save → verify)
- Error handling for invalid config values (graceful fallback to defaults)

### Translation Workflow

Language files are in `lang/` directory. To add/update translations:

```bash
# Install translation dependency
pip install googletrans==3.1.0a0

# Edit en_US and zh_CN base files, then regenerate others
./translate-all.sh -c
```

## Key Files Reference

| File | Purpose |
|------|---------|
| `app.py` | Flask web server, routes, WebSocket handlers |
| `karaoke.py` | Core logic, config management, player control |
| `pikaraoke.cfg` | Persistent user settings |
| `requirements.txt` | Python dependencies (pinned versions) |
| `run.sh` | Multi-process launch script with tmux |
| `lib/get_platform.py` | Platform detection, language handling |
| `lib/vlcclient.py` | VLC player remote control |
| `vocal_splitter.py` | Demucs vocal separation process |

## Troubleshooting

### Vocal modes greyed out
Create `vocal/` and `nonvocal/` subdirectories in your songs folder.

### No audio from headphone jack (Raspberry Pi)
```bash
sudo raspi-config  # Advanced Options > Audio > Force 3.5mm
```

### Songs not downloading
Update yt-dlp: `pip install -U yt-dlp` or use UI: Info > Update Youtube-dl

### Config not persisting
Ensure `pikaraoke.cfg` is writable and app is shut down properly (not killed).

## External Resources

- **Original PiKaraoke**: https://github.com/vicwomg/pikaraoke
- **Demucs**: https://github.com/facebookresearch/demucs
- **yt-dlp**: https://github.com/yt-dlp/yt-dlp
- **PyTorch**: https://pytorch.org
