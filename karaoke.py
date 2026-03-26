import os, sys, time, json, re
import logging, socket, subprocess, threading
import multiprocessing as mp
import shutil, psutil, traceback, tarfile, requests
from subprocess import check_output
from collections import *

import numpy as np

import pygame
import qrcode
import arabic_reshaper
from bidi.algorithm import get_display
from lib import omxclient, vlcclient
from lib.get_platform import *
from lib.NLP import *
from lib.notifications import flash, ws_send, ip2websock, ip2pane
from lib.config_manager import ConfigMixin
from lib.song_library import SongLibraryMixin
from lib.downloader import DownloaderMixin
from lib.queue_manager import QueueMixin
from app import getString

if get_platform() != "windows":
	from signal import SIGALRM, alarm, signal, SIGTERM
	signal(SIGTERM, lambda signum, stack_frame: os.K.stop())

TARGET_LOUDNESS_LUFS = -16.0  # Target integrated loudness for normalization (EBU R128 standard)


class Karaoke(ConfigMixin, SongLibraryMixin, DownloaderMixin, QueueMixin):
	ref_W, ref_H = 1920, 1080      # reference screen size, control drawing scale

	queue = []
	queue_json = ''
	available_songs = []
	rename_history = {}
	songname_trans = {} # transliteration is used for sorting and initial letter search
	now_playing = None
	now_playing_filename = None
	now_playing_user = None
	now_playing_transpose = 0
	now_playing_slave = ''
	audio_delay = 0
	has_video = True
	has_subtitle = False
	subtitle_delay = 0
	play_speed = 1.0
	show_subtitle = True
	last_vocal_info = 0
	last_vocal_time = 0
	run_vocal = False
	vocal_process = None
	vocal_device = None
	vocal_mode = 'mixed'
	is_paused = True
	firstSongStarted = False
	switchingSong = False
	qr_code_path = None
	base_path = os.path.dirname(__file__)
	volume_offset = 0
	default_logo_path = os.path.join(base_path, "logo.jpg")
	logical_volume = None   # for normalized volume
	status_dirty = True
	event_dirty = threading.Event()

	def __init__(self, args):

		# Initialize config attributes with defaults before loading config
		# UI settings (in order of appearance)
		self.save_play_settings = self.CONFIG_DEFAULTS['save_play_settings']
		self.default_subtitle_delay = self.CONFIG_DEFAULTS['default_subtitle_delay']
		self.normalize_vol = self.CONFIG_DEFAULTS['normalize_vol']
		self.use_DNN_vocal = self.CONFIG_DEFAULTS['use_dnn_vocal']
		self.language = self.CONFIG_DEFAULTS['language']
		# Config-file only settings
		self.admin_password = self.CONFIG_DEFAULTS['admin_password']
		self.show_overlay = self.CONFIG_DEFAULTS['show_overlay']

		# override with supplied constructor args if provided
		self.__dict__.update(args.__dict__)

		# save_delays: use args value if provided, otherwise use config default
		if self.save_delays is None:
			self.save_delays = self.dft_delays_file if self.save_play_settings else None
		self.omxplayer_adev = 'both'
		self.download_path = args.dl_path
		self.volume_offset = self.volume = args.volume
		self.logo_path = self.default_logo_path if args.logo_path == None else args.logo_path

		# other initializations
		self.platform = get_platform()
		self.vlcclient = None
		self.omxclient = None
		self.screen = None
		self.player_state = {}
		self.downloading_songs = {}
		self.log_level = int(args.log_level)

		logging.basicConfig(
			format = "[%(asctime)s] %(levelname)s: %(message)s",
			datefmt = "%Y-%m-%d %H:%M:%S",
			level = self.log_level,
		)

		logging.debug(vars(args))

		if self.save_delays:
			self.init_save_delays()

		self.load_config()

		# Set subtitle delay from loaded config
		self.subtitle_delay = self.default_subtitle_delay

		# Generate connection URL and QR code, retry in case pi is still starting up
		# and doesn't have an IP yet (occurs when launched from /etc/rc.local)
		end_time = int(time.time()) + 30

		if self.platform == "raspberry_pi":
			while int(time.time()) < end_time:
				addresses_str = check_output(["hostname", "-I"]).strip().decode("utf-8")
				addresses = addresses_str.split(" ")
				self.ip = addresses[0]
				if not self.is_network_connected():
					logging.debug("Couldn't get IP, retrying....")
				else:
					break
		else:
			self.ip = self.get_ip()

		logging.debug("IP address (for QR code and splash screen): " + self.ip)

		self.url = "%s://%s:%s" % (('https' if self.ssl else 'http'), self.ip, self.port)

		# get songs from download_path
		self.get_available_songs()
		self.get_youtubedl_version()

		# Ensure required subfolders exist
		os.makedirs(self.download_path + 'subs', exist_ok=True)
		os.makedirs(self.download_path + 'vocal', exist_ok=True)
		os.makedirs(self.download_path + 'nonvocal', exist_ok=True)

		# Automatically upgrade yt-dlp if using pip
		if not args.youtubedl_path:
			threading.Thread(target=self._upgrade_yt_dlp).start()

		# clean up old sessions
		self.kill_player()

		self.generate_qr_code()
		if self.use_vlc:
			self.vlcclient = vlcclient.VLCClient(port = self.vlc_port, path = self.vlc_path,
			                                     qrcode = (self.qr_code_path if self.show_overlay else None), url = self.url)
		else:
			self.omxclient = omxclient.OMXClient(path = self.omxplayer_path, adev = self.omxplayer_adev,
			                                     dual_screen = self.dual_screen, volume_offset = self.volume_offset)

		if not self.hide_splash_screen:
			self.initialize_screen(not args.windowed)
			self.render_splash_screen()

		self.cloud = args.cloud
		if args.cloud:
			self.cloud_trigger = threading.Event()
			self.cloud_tasks = []
			threading.Thread(target=self._cloud_thread).start()

	def _cloud_thread(self):
		while True:
			self.cloud_trigger.wait()
			self.cloud_trigger.clear()
			if not self.running: return
			while self.cloud_tasks:
				try:
					fn = self.cloud_tasks.pop(0)
					bn, dn = os.path.basename(fn), os.path.dirname(fn)
					if os.path.isfile(f'{self.download_path}nonvocal/{bn}.m4a') and os.path.isfile(f'{self.download_path}vocal/{bn}.m4a'):
						continue
					os.system(f'ffmpeg -y -i "{fn}" -vn -c copy {self.tmp_dir}/input.m4a')
					with open(f'{self.tmp_dir}/input.m4a', 'rb') as f:
						r = requests.post(self.cloud+'/split_vocal', files={'file': f})
					with open(f'{self.tmp_dir}/output.tar.gz', 'wb') as f:
						f.write(r.content)
					with tarfile.open(f'{self.tmp_dir}/output.tar.gz') as tar:
						tar.extract('nonvocal.m4a', f'{self.download_path}nonvocal')
						os.rename(f'{self.download_path}nonvocal/nonvocal.m4a', f'{self.download_path}nonvocal/{bn}.m4a')
						tar.extract('vocal.m4a', f'{self.download_path}vocal')
						os.rename(f'{self.download_path}vocal/vocal.m4a', f'{self.download_path}vocal/{bn}.m4a')
				except:
					traceback.print_exc()

	# Other ip-getting methods are unreliable and sometimes return 127.0.0.1
	# https://stackoverflow.com/a/28950776
	def get_ip(self):
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		try:
			# doesn't even have to be reachable
			s.connect(("8.8.8.8", 1))
			IP = s.getsockname()[0]
		except Exception:
			IP = "127.0.0.1"
		finally:
			s.close()
		return IP

	def is_network_connected(self):
		return not len(self.ip) < 7

	def generate_qr_code(self):
		logging.debug("Generating URL QR code")
		qr = qrcode.QRCode(version = 1, box_size = 1, border = 4, error_correction = qrcode.constants.ERROR_CORRECT_H)
		qr.add_data(self.url)
		qr.make()
		img = qr.make_image()
		self.qr_code_path = os.path.join(self.base_path, "qrcode.png")
		img.save(self.qr_code_path)

	def get_default_display_mode(self):
		if self.use_vlc:
			if self.platform == "raspberry_pi":
				# HACK apparently if display mode is fullscreen the vlc window will be at the bottom of pygame
				os.environ["SDL_VIDEO_CENTERED"] = "1"
				return pygame.NOFRAME
			else:
				return pygame.FULLSCREEN
		else:
			return pygame.FULLSCREEN

	def initialize_screen(self, fullscreen=True):
		if not self.hide_splash_screen:
			logging.debug("Initializing pygame")
			pygame.init()
			pygame.display.set_caption("pikaraoke")
			pygame.mouse.set_visible(0)
			self.fonts = {}
			self.WIDTH = pygame.display.Info().current_w
			self.HEIGHT = pygame.display.Info().current_h
			logging.debug("Initializing screen mode")

			if self.platform != "raspberry_pi":
				self.toggle_full_screen(fullscreen)
			else:
				# this section is an unbelievable nasty hack - for some reason Pygame
				# needs a keyboardinterrupt to initialise in some limited circumstances
				# source: https://stackoverflow.com/questions/17035699/pygame-requires-keyboard-interrupt-to-init-display
				class Alarm(Exception):
					pass

				def alarm_handler(signum, frame):
					raise Alarm

				signal(SIGALRM, alarm_handler)
				alarm(3)
				try:
					self.toggle_full_screen(fullscreen)
					alarm(0)
				except Alarm:
					raise KeyboardInterrupt
			logging.debug("Done initializing splash screen")

	def toggle_full_screen(self, fullscreen=None):
		if not self.hide_splash_screen:
			logging.debug("Toggling fullscreen...")
			self.full_screen = not self.full_screen if fullscreen is None else fullscreen
			if self.full_screen:
				self.screen = pygame.display.set_mode([self.WIDTH, self.HEIGHT], self.get_default_display_mode())
			else:
				self.screen = pygame.display.set_mode([self.WIDTH*3//4, self.HEIGHT*3//4], pygame.RESIZABLE)
			if self.is_file_playing():
				self.play_transposed(self.now_playing_transpose)
			else:
				self.render_splash_screen()

	def normalize(self, v):
		r = self.screen.get_width()/self.ref_W
		if type(v) is list:
			return [i*r for i in v]
		elif type(v) is tuple:
			return tuple(i * r for i in v)
		return v*r

	def render_splash_screen(self):
		if self.hide_splash_screen:
			return

		# Clear the screen and start
		logging.debug("Rendering splash screen")
		self.screen.fill((0, 0, 0))
		blitY = self.ref_W*self.screen.get_height()//self.screen.get_width() - 40
		sysfont_size = 30

		# Draw logo and name
		text = self.render_font(sysfont_size * 2, getString(136), (255, 255, 255))
		if not hasattr(self, 'logo'):
			self.logo = pygame.image.load(self.logo_path)
		_, _, W, H = self.normalize(list(self.logo.get_rect()))
		W, H = W/2, H/2
		center = self.screen.get_rect().center
		self.logo1 = pygame.transform.scale(self.logo, (W, H))
		self.screen.blit(self.logo1, (center[0]-W/2, center[1]-H/2-text[1].height/2))
		self.screen.blit(text[0], (center[0]-text[1].width/2, center[1]+H/2))

		if not self.hide_ip:
			qr_size = 150
			if not hasattr(self, 'p_image'):
				self.p_image = pygame.image.load(self.qr_code_path)
			self.p_image1 = pygame.transform.scale(self.p_image, self.normalize((qr_size, qr_size)))
			self.screen.blit(self.p_image1, self.normalize((20, blitY - 125)))
			if not self.is_network_connected():
				text = self.render_font(sysfont_size, getString(48), (255, 255, 255))
				self.screen.blit(text[0], self.normalize((qr_size + 35, blitY)))
				time.sleep(10)
				logging.info("No IP found. Network/Wifi configuration required. For wifi config, try: sudo raspi-config or the desktop GUI: startx")
				self.stop()
			else:
				text = self.render_font(sysfont_size, getString(49) + self.url, (255, 255, 255))
				self.screen.blit(text[0], self.normalize((qr_size + 35, blitY)))
				# Windows and Mac-OS should use screen projection and AirPlay
				if self.streamer_alive():
					text = self.render_font(sysfont_size, getString(50) + self.url.rsplit(":", 1)[0] + ":4000", (255, 255, 255))
					self.screen.blit(text[0], self.normalize((qr_size + 35, blitY - 40)))
				if not self.firstSongStarted:
					text = self.render_font(sysfont_size, getString(51), (255, 255, 255))
					self.screen.blit(text[0], self.normalize((qr_size + 35, blitY - 120)))
					text = self.render_font(sysfont_size, getString(52), (255, 255, 255))
					self.screen.blit(text[0], self.normalize((qr_size + 35, blitY - 80)))

		blitY = 10
		if not self.has_video:
			logging.debug("Rendering current song to splash screen")
			render_next_song = self.render_font([60, 50, 40], getString(58) + (self.now_playing or ''), (255, 255, 0))
			render_next_user = self.render_font([50, 40, 30], getString(57) + (self.now_playing_user or ''), (0, 240, 0))
			self.screen.blit(render_next_song[0], (self.screen.get_width() - render_next_song[1].width - 10, self.normalize(10)))
			self.screen.blit(render_next_user[0], (self.screen.get_width() - render_next_user[1].width - 10, self.normalize(80)))
			blitY += 140

		if len(self.queue) >= 1:
			logging.debug("Rendering next song to splash screen")
			next_song = self.queue[0]["title"]
			next_user = self.queue[0]["user"]
			render_next_song = self.render_font([60, 50, 40], getString(56) + next_song, (255, 255, 0))
			render_next_user = self.render_font([50, 40, 30], getString(57) + next_user, (0, 240, 0))
			self.screen.blit(render_next_song[0], (self.screen.get_width() - render_next_song[1].width - 10, self.normalize(blitY)))
			self.screen.blit(render_next_user[0], (self.screen.get_width() - render_next_user[1].width - 10, self.normalize(blitY+70)))
		elif not self.firstSongStarted:
			text1 = self.render_font(sysfont_size, getString(196) + ': ' + self.download_path, (255, 255, 0))
			self.screen.blit(text1[0], self.normalize((20, 20)))
			text2 = self.render_font(sysfont_size, getString(197) + ': %d'%len(self.available_songs), (255, 255, 0))
			self.screen.blit(text2[0], self.normalize((20, 30+sysfont_size)))

	def render_font(self, sizes, text, *kargs):
		if type(sizes) != list:
			sizes = [sizes]

		# normalize font size
		sizes = [s*self.screen.get_width()/self.ref_W for s in sizes]

		# initialize fonts if not found
		for size in sizes:
			if size not in self.fonts:
				self.fonts[size] = [pygame.freetype.SysFont(pygame.freetype.get_default_font(), size)] \
						+ [pygame.freetype.Font(f'font/{name}', size) for name in ['arial-unicode-ms.ttf', 'unifont.ttf']]

		# find a font that contains all characters of the song title, if cannot find, then display transliteration instead
		found = None
		for ii, font in enumerate(self.fonts[size]):
			if None not in font.get_metrics(text):
				found = ii
				break
		if found is None:
			from unidecode import unidecode
			text = unidecode(text)
			found = 0

		# reshape Arabic text
		text = get_display(arabic_reshaper.reshape(text))

		# draw the font, if too wide, half the string
		width = self.screen.get_width()
		for size in sorted(sizes, reverse = True):
			font = self.fonts[size][found]
			render = font.render(text, *kargs)
			# reduce font size if text too long
			if render[1].width > width and size != min(sizes):
				continue
			while render[1].width >= width:
				text = text[:int(len(text) * min(width / render[1].width, 0.618))] + '…'
				del render
				render = font.render(text, *kargs)
			break
		return render

	def kill_player(self):
		if self.use_vlc:
			logging.debug("Killing old VLC processes")
			if self.vlcclient != None:
				self.vlcclient.kill()
		elif self.omxclient != None:
				self.omxclient.kill()

	def play_file(self, file_path, extra_params = []):
		self.switchingSong = True
		if self.use_vlc:
			if self.save_delays:
				saved_delays = self.delays.get(os.path.basename(file_path), {})
				self.audio_delay = self.audio_delay if self.audio_delay is not None else saved_delays.get('audio_delay', 0)
				self.subtitle_delay = saved_delays.get('subtitle_delay', self.default_subtitle_delay)
				self.show_subtitle = False if self.show_subtitle==False else saved_delays.get('show_subtitle', True)
			extra_params1 = []
			logging.info("Playing video in VLC: " + file_path)
			if self.platform != 'osx':
				extra_params1 += ['--drawable-hwnd' if self.platform == 'windows' else '--drawable-xid',
				                  hex(pygame.display.get_wm_info()['window'])]
			self.now_playing_slave = self.try_set_vocal_mode(self.vocal_mode, file_path)
			if os.path.isfile(self.now_playing_slave):
				extra_params1 += [f'--input-slave={self.now_playing_slave}', '--audio-track=1']
			if self.audio_delay:
				extra_params1 += [f'--audio-desync={self.audio_delay * 1000}']
			if self.subtitle_delay:
				extra_params1 += [f'--sub-delay={self.subtitle_delay * 10}']
			if self.show_subtitle:
				extra_params1 += [f'--sub-track=0']
			if self.play_speed != 1:
				extra_params1 += [f'--rate={self.play_speed}']
			self.now_playing = self.filename_from_path(file_path)
			self.now_playing_filename = file_path
			self.is_paused = ('--start-paused' in extra_params1)
			if self.normalize_vol and self.logical_volume is not None:
				self.volume = self.logical_volume / np.sqrt(self.get_mp3_volume(file_path))
			if self.now_playing_transpose == 0:
				xml = self.vlcclient.play_file(file_path, self.volume, extra_params + extra_params1)
			else:
				xml = self.vlcclient.play_file_transpose(file_path, self.now_playing_transpose, self.volume, extra_params + extra_params1)
			self.has_subtitle = "<info name='Type'>Subtitle</info>" in xml
			self.has_video = "<info name='Type'>Video</info>" in xml
			self.volume = round(float(self.vlcclient.get_val_xml(xml, 'volume')))
			if self.normalize_vol:
				self.media_vol = self.get_mp3_volume(self.now_playing_filename)
				self.logical_volume = self.volume * np.sqrt(self.media_vol)
		else:
			logging.info("Playing video in omxplayer: " + file_path)
			self.omxclient.play_file(file_path)

		self.switchingSong = False
		self.status_dirty = True
		self.render_splash_screen()  # remove old previous track

	def play_transposed(self, semitones):
		if self.use_vlc:
			self.now_playing_transpose = semitones
			status_xml = self.vlcclient.command().text if self.is_paused else self.vlcclient.pause(False).text
			info = self.vlcclient.get_info_xml(status_xml)
			posi = info['position']*info['length']
			self.play_file(self.now_playing_filename, [f'--start-time={posi}'] + (['--start-paused'] if self.is_paused else []))
		else:
			logging.error("Not using VLC. Can't transpose track.")

	def is_file_playing(self):
		client = self.vlcclient if self.use_vlc else self.omxclient
		if client is not None and client.is_running():
			return True
		elif self.now_playing_filename:
			self.now_playing = self.now_playing_filename = None
		return False

	def skip(self):
		if self.is_file_playing():
			logging.info("Skipping: " + self.now_playing)
			if self.use_vlc:
				self.vlcclient.stop()
			else:
				self.omxclient.stop()
			self.reset_now_playing()
			return True
		logging.warning("Tried to skip, but no file is playing!")
		return False

	def seek(self, seek_sec):
		if self.is_file_playing():
			if self.use_vlc:
				self.vlcclient.seek(seek_sec)
			else:
				logging.warning("OMXplayer cannot seek track!")
			return True
		logging.warning("Tried to seek, but no file is playing!")
		return False

	def set_delays_dict(self, filename, key, val, dft_val=0):
		basename = os.path.basename(filename)
		delays = self.delays.get(basename, {})
		if val == dft_val:
			delays.pop(key, None)
		else:
			delays[key] = val
		if delays:
			self.delays[basename] = delays
		else:
			self.delays.pop(basename, {})
		self.delays_dirty = True

	def set_audio_delay(self, delay):
		if delay == '+':
			self.audio_delay += 0.1
		elif delay == '-':
			self.audio_delay -= 0.1
		elif delay == '':
			self.audio_delay = 0
		else:
			try:
				self.audio_delay = float(delay)
			except:
				logging.warning(f"Tried to set audio delay to an invalid value {delay}, ignored!")
				return False

		if self.save_delays:
			self.set_delays_dict(self.now_playing_filename, 'audio_delay', self.audio_delay)

		if self.is_file_playing():
			if self.use_vlc:
				self.vlcclient.command(f"audiodelay&val={self.audio_delay}")
			else:
				logging.warning("OMXplayer cannot set audio delay!")
			self.status_dirty = True
			return self.audio_delay
		logging.warning("Tried to set audio delay, but no file is playing!")
		return False

	def set_subtitle_delay(self, delay):
		if delay == '+':
			self.subtitle_delay += 0.1
		elif delay == '-':
			self.subtitle_delay -= 0.1
		elif delay == '':
			self.subtitle_delay = 0
		else:
			try:
				self.subtitle_delay = float(delay)
			except:
				logging.warning(f"Tried to set subtitle delay to an invalid value {delay}, ignored!")
				return False

		if self.save_delays:
			self.set_delays_dict(self.now_playing_filename, 'subtitle_delay', self.subtitle_delay)

		if self.is_file_playing():
			if self.use_vlc:
				self.vlcclient.command(f"subdelay&val={self.subtitle_delay}")
			else:
				logging.warning("OMXplayer cannot set subtitle delay!")
			self.status_dirty = True
			return self.subtitle_delay
		logging.warning("Tried to set subtitle delay, but no file is playing!")
		return False

	def toggle_subtitle(self):
		self.show_subtitle = not self.show_subtitle
		if self.save_delays:
			self.set_delays_dict(self.now_playing_filename, 'show_subtitle', self.show_subtitle, True)
		self.play_vocal(force=True)

	def pause(self):
		if self.is_file_playing():
			logging.info("Toggling pause: " + self.now_playing)
			if self.use_vlc:
				if self.vlcclient.is_playing():
					self.vlcclient.pause()
					self.is_paused = True
				else:
					self.vlcclient.play()
					self.is_paused = False
			else:
				if self.omxclient.is_playing():
					self.omxclient.pause()
					self.is_paused = True
				else:
					self.omxclient.play()
					self.is_paused = False
			self.status_dirty = True
			return True
		else:
			logging.warning("Tried to pause, but no file is playing!")
			return False

	def vol_up(self):
		if self.is_file_playing():
			if self.use_vlc:
				self.vlcclient.vol_up()
				xml = self.vlcclient.command().text
				self.volume = int(self.vlcclient.get_val_xml(xml, 'volume'))
			else:
				self.volume = self.omxclient.vol_up()
			self.update_logical_vol()
			return self.volume
		else:
			logging.warning("Tried to volume up, but no file is playing!")
			return False

	def vol_down(self):
		if self.is_file_playing():
			if self.use_vlc:
				self.vlcclient.vol_down()
				xml = self.vlcclient.command().text
				self.volume = int(self.vlcclient.get_val_xml(xml, 'volume'))
			else:
				self.volume = self.omxclient.vol_down()
			self.update_logical_vol()
			return self.volume
		else:
			logging.warning("Tried to volume down, but no file is playing!")
			return False

	def vol_set(self, volume):
		if self.is_file_playing():
			if self.use_vlc:
				self.vlcclient.vol_set(volume)
				xml = self.vlcclient.command().text
				self.volume = int(self.vlcclient.get_val_xml(xml, 'volume'))
			else:
				logging.warning("Only VLC player can set volume, ignored!")
				self.volume = self.omxclient.volume_offset
			self.update_logical_vol()
			return self.volume
		else:
			logging.warning("Tried to set volume, but no file is playing!")
			return False

	def play_speed_set(self, speed):
		if self.is_file_playing():
			if self.use_vlc:
				self.vlcclient.playspeed_set(speed)
				xml = self.vlcclient.command().text
				self.play_speed = float(self.vlcclient.get_val_xml(xml, 'rate'))
				logging.info(f"Playback speed set to {self.play_speed}")
			else:
				logging.warning("Only VLC player can set playback speed, ignored!")
			return self.play_speed
		else:
			logging.warning("Tried to set play speed, but no file is playing!")
			return False

	def try_set_vocal_mode(self, mode, now_playing_filename):
		if mode not in ['mixed', 'vocal', 'nonvocal']:
			mode = {1: 'nonvocal', 2: 'mixed', 3: 'vocal'}[self.get_vocal_mode()]
		play_slave = '' if mode == 'mixed' else self.download_path + mode + '/' + ('' if self.use_DNN_vocal else '.') \
		                                       + os.path.basename(now_playing_filename) + '.m4a'
		if os.path.isfile(play_slave):
			self.vocal_mode = mode
		else:
			play_slave = ''
			self.vocal_mode = 'mixed'
		return play_slave

	def play_vocal(self, mode = None, force = False):
		# mode=vocal/nonvocal/mixed, or else (use current)
		if self.use_vlc:
			play_slave = self.try_set_vocal_mode(mode, self.now_playing_filename)
			if not force and self.now_playing_slave == play_slave:
				return
			status_xml = self.vlcclient.command().text if self.is_paused else self.vlcclient.pause(False).text
			info = self.vlcclient.get_info_xml(status_xml)
			posi = info['position']*info['length']
			self.play_file(self.now_playing_filename, [f'--start-time={posi}'] + (['--start-paused'] if self.is_paused else []))
			self.get_vocal_info(True)
		else:
			logging.error("Not using VLC. Can't play vocal/nonvocal.")

	def get_vocal_mode(self):
		if '/nonvocal/' in self.now_playing_slave.replace('\\', '/'):
			return 1
		elif '/vocal/' in self.now_playing_slave.replace('\\', '/'):
			return 3
		return 2

	def get_vocal_info(self, force_update=False):
		tm = time.time()
		if not force_update and tm-self.last_vocal_time < 2:
			return self.last_vocal_info
		if not self.now_playing_filename:
			return 0
		mask = 0
		bn = os.path.basename(self.now_playing_filename)
		if os.path.isfile(f'{self.download_path}nonvocal/{bn}.m4a'):
			mask |= 0b00000001
		if os.path.isfile(f'{self.download_path}vocal/{bn}.m4a'):
			mask |= 0b00000010
		if os.path.isfile(f'{self.download_path}nonvocal/.{bn}.m4a'):
			mask |= 0b00000100
		if os.path.isfile(f'{self.download_path}vocal/.{bn}.m4a'):
			mask |= 0b00001000
		if 'vocal/.' in self.now_playing_slave:
			mask |= 0b10000000
		if self.use_DNN_vocal:
			mask |= 0b01000000
		mask |= (self.get_vocal_mode() << 4)
		self.last_vocal_info = mask
		self.last_vocal_time = tm
		return mask

	def get_mp3_volume(self, filename):
		try:
			basename = os.path.basename(filename)
			md5, fsize = md5sum(filename), os.stat(filename).st_size
			# Check cache in delays dict
			vol_data = self.delays.get(basename, {}).get('volume', {})
			if vol_data and fsize == vol_data.get('size') and md5 == vol_data.get('md5'):
				return vol_data['val']
			# Measure EBU R128 integrated loudness using ebur128 filter
			output = subprocess.check_output(
				['ffmpeg', '-i', filename, '-vn', '-af', 'ebur128=peak=true', '-f', 'null', '-'],
				stderr=subprocess.STDOUT
			).decode('utf-8', errors='ignore')
			# Parse integrated loudness from Summary section
			# Format (multi-line):
			#   Integrated loudness:
			#     I:         -12.3 LUFS
			i_match = re.search(r'Integrated loudness:\s*\n\s*I:\s*([-+]?\d+\.?\d*)\s*LUFS', output)
			if i_match:
				integrated_loudness = float(i_match.group(1))
			else:
				logging.warning(f"Could not parse integrated loudness for {filename}, using default")
				integrated_loudness = TARGET_LOUDNESS_LUFS
			# Calculate volume multiplier to reach target loudness
			gain_db = TARGET_LOUDNESS_LUFS - integrated_loudness
			volume_val = np.clip(10 ** (gain_db / 20), 1/16, 16)
			# Store in delays dict
			if basename not in self.delays:
				self.delays[basename] = {}
			self.delays[basename]['volume'] = {'val': volume_val, 'size': fsize, 'md5': md5, 'lufs': integrated_loudness}
			self.delays_dirty = True
			self.auto_save_delays()
			return volume_val
		except:
			logging.warning(f"Could not analyse volume for {filename}, skipping normalisation for this song")
			return 1

	def update_logical_vol(self):
		if hasattr(self, 'media_vol'):
			self.logical_volume = self.volume * self.media_vol

	def enable_vol_norm(self, enable):
		self.normalize_vol = enable
		if enable and shutil.which('ffmpeg') is None:
			self.normalize_vol = enable = False
		if enable and self.now_playing_filename:
			self.volume = self.vlcclient.get_info_xml()['volume']
			self.media_vol = self.get_mp3_volume(self.now_playing_filename)
			self.update_logical_vol()
		self.save_config()
		return str(self.logical_volume)

	def set_dnn_vocal(self, enabled):
		self.use_DNN_vocal = enabled
		self.save_config()
		self.play_vocal()

	def get_state(self):
		if self.use_vlc and self.vlcclient.is_transposing:
			return defaultdict(lambda: None, self.player_state)
		if not self.is_file_playing():
			self.player_state['now_playing'] = None
			return defaultdict(lambda: None, self.player_state)
		new_state = self.vlcclient.get_info_xml() if self.use_vlc else {
			'volume': self.omxclient.volume_offset,
			'state': ('paused' if self.omxclient.paused else 'playing')
		}
		self.player_state.update(new_state)
		return defaultdict(lambda: None, self.player_state)

	def restart(self):
		if self.is_file_playing():
			if self.use_vlc:
				self.vlcclient.restart()
			else:
				self.omxclient.restart()
			self.is_paused = False
			return True
		else:
			logging.warning("Tried to restart, but no file is playing!")
			return False

	def stop(self):
		self.running = False

	def handle_run_loop(self):
		for event in pygame.event.get():
			if event.type == pygame.QUIT:
				logging.warning("Window closed: Exiting pikaraoke...")
				self.running = False
			elif event.type == pygame.KEYDOWN:
				if event.key == pygame.K_ESCAPE:
					logging.warning("ESC pressed: Exiting pikaraoke...")
					self.running = False
				if event.key == pygame.K_f:
					self.toggle_full_screen()
		if not self.is_file_playing() or not self.has_video:
			self.render_splash_screen()
			pygame.display.update()
		pygame.time.wait(100)

	# Use this to reset the screen in case it loses focus
	# This seems to occur in windows after playing a video
	def pygame_reset_screen(self):
		if not self.hide_splash_screen:
			logging.debug("Resetting pygame screen...")
			pygame.display.quit()
			self.initialize_screen()
			self.render_splash_screen()

	def reset_now_playing(self):
		self.auto_save_delays()
		self.now_playing = None
		self.now_playing_filename = None
		self.now_playing_user = None
		self.is_paused = True
		self.now_playing_transpose = 0
		self.now_playing_slave = ''
		self.audio_delay = 0
		self.subtitle_delay = self.default_subtitle_delay
		self.show_subtitle = True
		self.has_subtitle = False
		self.has_video = True
		self.last_vocal_info = 0
		self.play_speed = 1

	def streamer_alive(self):
		try:
			return bool([1 for p in psutil.process_iter() if './screencapture.sh' in p.cmdline()])
		except:
			return None

	def streamer_restart(self, delay=0):
		if self.platform in ['windows', 'osx']:
			return
		os.system(f"sleep {delay} && tmux send-keys -t PiKaraoke:0.3 C-c && tmux send-keys -t PiKaraoke:0.3 Up Enter")

	def streamer_stop(self, delay=0):
		if self.platform in ['windows', 'osx']:
			return
		os.system(f"sleep {delay} && tmux send-keys -t PiKaraoke:0.3 C-c")

	def vocal_alive(self):
		try:
			return bool(self.vocal_process and self.vocal_process.is_alive())\
					or bool([1 for p in psutil.process_iter() if 'vocal_splitter.py' in p.cmdline()])
		except:
			return None

	def vocal_restart(self):
		if self.platform == 'windows' or self.run_vocal:
			import vocal_splitter
			if self.vocal_process is not None and self.vocal_process.is_alive():
				self.vocal_process.kill()
			if shutil.which('ffmpeg'):
				self.vocal_process = mp.Process(target=vocal_splitter.main, args=(['-p', '-d', self.download_path],))
				self.vocal_process.start()
		else:
			os.system(f"tmux send-keys -t PiKaraoke:0.4 C-c && tmux send-keys -t PiKaraoke:0.4 Up Enter")

	def vocal_stop(self):
		if self.vocal_process is not None and self.vocal_process.is_alive():
			self.vocal_process.kill()
		elif self.platform != 'windows':
			os.system(f"tmux send-keys -t PiKaraoke:0.4 C-c")

	def run(self):
		logging.info("Starting PiKaraoke!")
		self.running = True

		# Windows does not have tmux, vocal splitter can only be invoked from the main program
		if self.platform == 'windows' or self.run_vocal:
			Try(lambda: self.vocal_restart())

		while self.running:
			try:
				if not self.is_file_playing() and self.now_playing != None:
					self.reset_now_playing()
				if self.queue:
					if not self.is_file_playing():
						self.reset_now_playing()
						self.render_splash_screen()
						tm = time.time()
						while time.time()-tm < self.splash_delay:
							self.handle_run_loop()
						head = self.queue.pop(0)
						self.play_file(head['file'])
						if self.cloud:
							self.cloud_tasks += [head['file']]
							self.cloud_trigger.set()
						if not self.firstSongStarted:
							if self.streamer_alive():
								self.streamer_restart(1)
							self.firstSongStarted = True
						self.now_playing_user = head["user"]
						self.update_queue()
				self.handle_run_loop()
			except KeyboardInterrupt:
				logging.warning("Keyboard interrupt: Exiting pikaraoke...")
				self.running = False

		# Clean up before quit
		self.streamer_stop()
		self.vocal_stop()
		vplayer = self.vlcclient if self.use_vlc else self.omxclient
		if vplayer is not None: vplayer.stop()
		self.auto_save_delays()
		time.sleep(1)
		if vplayer is not None: vplayer.kill()
