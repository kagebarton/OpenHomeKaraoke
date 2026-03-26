"""
Configuration and per-song delay persistence for PiKaraoke.

Extracted from karaoke.py.  Add to the Karaoke class via inheritance:

    from lib.config_manager import ConfigMixin

    class Karaoke(ConfigMixin, ...):
        ...

The mixin expects the following attributes to already be set on `self`
before load_config() / init_save_delays() are called:
    self.base_path      – project root directory (used to locate pikaraoke.cfg)
    self.save_delays    – path string or None  (set in __init__ before load_config)
    self.dft_delays_file – default path for the delays JSON file
"""

import json
import logging
import os
import configparser


class ConfigMixin:

    # ═══════════════════════════════════════════════════════════════════════════
    # CONFIG DEFAULTS — Change these values to set defaults for new installations
    # These values are written to pikaraoke.cfg when it's first created.
    # Existing installations keep their saved settings in pikaraoke.cfg.
    # ═══════════════════════════════════════════════════════════════════════════
    CONFIG_DEFAULTS = {
        # UI settings (in order of appearance)
        'save_play_settings': True,
        'default_subtitle_delay': -0.8,
        'normalize_vol': True,
        'use_dnn_vocal': True,
        'language': '',
        # Config-file only settings
        'admin_password': '',
        'show_overlay': True,
    }

    # Config file — plain INI format, safe to hand-edit while the app is not running.
    # Lives in the project folder (alongside app.py) rather than the song folder.
    CONFIG_TEMPLATE = """\
# pikaraoke settings
# This file is written automatically by the web UI.
# You can also edit it by hand while the app is not running.

[pikaraoke]

# Save per-song play settings (audio delay, subtitle delay, subtitle on/off).
# Settings are stored alongside the song library.
save_play_settings = {save_play_settings}

# Default subtitle delay in seconds (negative = subtitles appear earlier).
# This is the baseline delay used when no per-song delay is saved.
default_subtitle_delay = {default_subtitle_delay}

# Normalize volume levels across songs so loud and quiet songs play at similar levels.
# Requires ffmpeg to be installed.
normalize_vol = {normalize_vol}

# Use the DNN (neural network) model for vocal separation.
# Produces better quality results and uses the GPU if available.
# Set to false to use the faster stereo subtraction method instead.
use_dnn_vocal = {use_dnn_vocal}

# Display language code (e.g., en_US, ja_JP). Leave empty for system default.
language = {language}

# Administrator password for restricting certain web UI features. Leave empty for no password.
admin_password = {admin_password}

# Show overlay with QR code and IP address on top of video.
show_overlay = {show_overlay}
"""

    # ─── delays / per-song data ───────────────────────────────────────────────

    def init_save_delays(self) -> None:
        self.delays_dirty = False
        if os.path.isfile(self.save_delays):
            try:
                self.delays = json.load(open(self.save_delays))
                return
            except Exception:
                logging.warning(
                    f"Could not read delays file {self.save_delays}, starting with empty delays"
                )
        self.delays = {}
        with open(self.save_delays, 'w') as fp:
            json.dump(self.delays, fp, indent=1)

    def set_save_delays(self, state: bool) -> None:
        if state != bool(self.save_delays):
            if state:
                self.save_delays = self.dft_delays_file
                self.init_save_delays()
            else:
                self.save_delays = None
        self.save_play_settings = state
        self.save_config()

    def auto_save_delays(self) -> None:
        if self.save_delays and self.delays_dirty:
            self.delays_dirty = False
            with open(self.save_delays, 'w') as fp:
                json.dump(self.delays, fp, indent=1)

    # ─── main config file ─────────────────────────────────────────────────────

    def load_config(self) -> None:
        self.config_path = os.path.join(self.base_path, 'pikaraoke.cfg')
        if not os.path.isfile(self.config_path):
            logging.info(f"No config file found, creating defaults at {self.config_path}")
            self.save_config()
        config = configparser.ConfigParser()
        config.read(self.config_path)
        if 'pikaraoke' in config:
            s = config['pikaraoke']
            # UI settings (in order of appearance)
            try:
                self.save_play_settings = s.getboolean(
                    'save_play_settings', fallback=self.CONFIG_DEFAULTS['save_play_settings']
                )
            except ValueError:
                logging.warning(
                    f"Invalid save_play_settings value, using default: {self.CONFIG_DEFAULTS['save_play_settings']}"
                )
                self.save_play_settings = self.CONFIG_DEFAULTS['save_play_settings']
            try:
                self.default_subtitle_delay = s.getfloat(
                    'default_subtitle_delay', fallback=self.CONFIG_DEFAULTS['default_subtitle_delay']
                )
            except ValueError:
                logging.warning(
                    f"Invalid default_subtitle_delay value, using default: {self.CONFIG_DEFAULTS['default_subtitle_delay']}"
                )
                self.default_subtitle_delay = self.CONFIG_DEFAULTS['default_subtitle_delay']
            try:
                self.normalize_vol = s.getboolean(
                    'normalize_vol', fallback=self.CONFIG_DEFAULTS['normalize_vol']
                )
            except ValueError:
                logging.warning(
                    f"Invalid normalize_vol value, using default: {self.CONFIG_DEFAULTS['normalize_vol']}"
                )
                self.normalize_vol = self.CONFIG_DEFAULTS['normalize_vol']
            try:
                self.use_DNN_vocal = s.getboolean(
                    'use_dnn_vocal', fallback=self.CONFIG_DEFAULTS['use_dnn_vocal']
                )
            except ValueError:
                logging.warning(
                    f"Invalid use_dnn_vocal value, using default: {self.CONFIG_DEFAULTS['use_dnn_vocal']}"
                )
                self.use_DNN_vocal = self.CONFIG_DEFAULTS['use_dnn_vocal']
            # String settings
            self.language = s.get('language', fallback=self.CONFIG_DEFAULTS['language'])
            # Config-file only settings
            self.admin_password = s.get('admin_password', fallback=self.CONFIG_DEFAULTS['admin_password'])
            try:
                self.show_overlay = s.getboolean(
                    'show_overlay', fallback=self.CONFIG_DEFAULTS['show_overlay']
                )
            except ValueError:
                logging.warning(
                    f"Invalid show_overlay value, using default: {self.CONFIG_DEFAULTS['show_overlay']}"
                )
                self.show_overlay = self.CONFIG_DEFAULTS['show_overlay']
        self.set_save_delays(self.save_play_settings)
        logging.info(f"Config loaded from {self.config_path}")

    def save_config(self) -> None:
        try:
            with open(self.config_path, 'w') as f:
                f.write(self.CONFIG_TEMPLATE.format(
                    save_play_settings=str(self.save_play_settings).lower(),
                    default_subtitle_delay=self.default_subtitle_delay,
                    normalize_vol=str(self.normalize_vol).lower(),
                    use_dnn_vocal=str(self.use_DNN_vocal).lower(),
                    language=self.language,
                    admin_password=self.admin_password,
                    show_overlay=str(self.show_overlay).lower(),
                ))
            logging.info(f"Config saved to {self.config_path}")
        except Exception as e:
            logging.error(f"Failed to save config: {e}")
