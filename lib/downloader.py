"""
YouTube / yt-dlp download logic for PiKaraoke.

Extracted from karaoke.py.  Add to the Karaoke class via inheritance:

    from lib.downloader import DownloaderMixin

    class Karaoke(DownloaderMixin, ...):
        ...

Expected attributes on `self`:
    self.youtubedl_path   – path to yt-dlp binary, or '' to use the pip package
    self.download_path    – destination folder for finished downloads
    self.tmp_dir          – temporary working directory
    self.cookies_opt      – list of extra yt-dlp cookie arguments
    self.high_quality     – bool, prefer 1080p downloads
    self.downloading_songs – dict[url, status]  (maintained here)
"""

import datetime
import glob
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import threading

import requests

from lib.notifications import flash, ws_send
from lib.NLP import Try, sec2hhmmss


def cleanse_modules(name: str) -> None:
    """Force-unload a module family so it can be reimported at a new version."""
    try:
        for module_name in sorted(sys.modules.keys()):
            if module_name.startswith(name):
                del sys.modules[module_name]
        del globals()[name]
    except Exception:
        pass


class DownloaderMixin:

    # ─── yt-dlp version management ───────────────────────────────────────────

    def get_youtubedl_version(self) -> str:
        self.youtubedl_version = self.call_yt_dlp(['--version'], True).strip()
        return self.youtubedl_version

    def upgrade_youtubedl(self) -> None:
        logging.info("Upgrading youtube-dl, current version: %s" % self.youtubedl_version)
        if self.youtubedl_path:
            self.call_yt_dlp(['-U'])
        else:
            try:
                import pip
                pip.main(['install', 'yt-dlp', '-U'])
                cleanse_modules('yt_dlp')
                import yt_dlp  # noqa: F401 – re-import to confirm upgrade succeeded
            except Exception:
                pass
        logging.info("Done. New version: %s" % self.get_youtubedl_version())

    def _upgrade_yt_dlp(self) -> None:
        """Background thread: upgrade yt-dlp at most once per calendar day."""
        import pip, yt_dlp  # noqa: F401
        fn = '.yt-dlp.last-update'
        date_today = datetime.datetime.today().isoformat()[:10]
        date_last = Try(lambda: open(fn).read().strip(), '')
        if date_today == date_last:
            logging.info(f"yt-dlp is up-to-date at {date_today}")
            return
        self.upgrade_youtubedl()
        self.get_youtubedl_version()
        with open(fn, 'w') as fp:
            print(date_today, file=fp)

    # ─── yt-dlp invocation ───────────────────────────────────────────────────

    def call_yt_dlp(self, argv: list, get_stdout: bool = False):
        """Run yt-dlp either as an external binary or via the pip package."""
        if self.youtubedl_path:
            if get_stdout:
                return subprocess.check_output([self.youtubedl_path] + argv).decode("utf-8")
            else:
                return subprocess.call([self.youtubedl_path] + argv)
        ret_code = 0
        if get_stdout:
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
        try:
            import yt_dlp
            yt_dlp.main(argv)
        except SystemExit as e:
            ret_code = e.code
        if get_stdout:
            ret_stdout = sys.stdout
            sys.stdout = old_stdout
            return ret_stdout.getvalue()
        return ret_code

    # ─── search ──────────────────────────────────────────────────────────────

    def get_search_results(self, textToSearch: str) -> list:
        logging.info("Searching YouTube for: " + textToSearch)
        num_results = 10
        yt_search = 'ytsearch%d:%s' % (num_results, textToSearch)
        cmd = ["-j", "--no-playlist", "--flat-playlist", yt_search]
        logging.debug("Youtube-dl search command: " + " ".join(cmd))
        try:
            output = self.call_yt_dlp(cmd, True)
            logging.debug("Search results: " + output)
            rc = []
            for each in output.split("\n"):
                if len(each) > 2:
                    j = json.loads(each)
                    if "title" not in j or "url" not in j:
                        continue
                    rc.append([j["title"], j["url"], j["id"], sec2hhmmss(j["duration"])])
            return rc
        except Exception as e:
            logging.debug("Error while executing search: " + str(e))
            raise

    def get_yt_dlp_json(self, url: str) -> dict:
        out_json = self.call_yt_dlp(['-j', '--remote-components', 'ejs:github', url], True)
        return json.loads(out_json)

    # ─── download ────────────────────────────────────────────────────────────

    def get_downloaded_file_basename(self, url: str):
        try:
            youtube_id = url.split("watch?v=")[1].split('&')[0]
        except Exception:
            try:
                info_json = self.get_yt_dlp_json(url)
                youtube_id = info_json['id']
            except Exception:
                logging.error("Error parsing video id from url: " + url)
                return None

        try:
            return [i for i in os.listdir(self.tmp_dir) if youtube_id in i][0]
        except Exception:
            pass

        try:
            info_json = self.get_yt_dlp_json(url)
            filename = f"{info_json['title']}---{info_json['id']}.{info_json['ext']}"
            return filename if os.path.isfile(self.tmp_dir + '/' + filename) else None
        except Exception:
            return None

    def download_video(
        self,
        client_lang: str = '',
        client_ip: str = '',
        song_url: str = '',
        enqueue: bool = False,
        song_added_by: str = "Pikaraoke",
        sub_langs: str = '',
        high_quality: bool = False,
    ) -> None:
        import os as _os
        logging.info("Downloading video: " + song_url)
        getString2 = lambda ii: _os.langs.get(client_lang, _os.langs['en_US'])[ii]
        self.downloading_songs[song_url] = 1

        dl_path  = "%(title)s---%(id)s.%(ext)s"
        fmt_hq   = (
            'bestvideo[height<=1080][vcodec~="h264|avc1"]+bestaudio[acodec~="aac|mp4a"]'
            '/bestvideo[height<=1080][vcodec~="h264|avc1"]+bestaudio'
            '/bestvideo[height<=1080]+bestaudio'
        )
        fmt_std  = (
            'bestvideo[height<=720][vcodec~="h264|avc1"]+bestaudio[acodec~="aac|mp4a"]'
            '/bestvideo[height<=720][vcodec~="h264|avc1"]+bestaudio'
            '/bestvideo[height<=720]+bestaudio'
        )
        opt_sub  = (
            ['--sub-langs', sub_langs, '--write-auto-subs', '--write-subs',
             '--sub-format', 'srt/vtt/best', '--convert-subs', 'srt']
            if sub_langs else []
        )
        base_opts = ['--fixup', 'force', '--socket-timeout', '3', '-R', 'infinite',
                     '--remux-video', 'mp4']
        out_opt   = ["-o", self.tmp_dir + '/' + dl_path]

        # Try requested quality first, fall back to standard, then no format constraint.
        attempts = ([fmt_hq, fmt_std] if high_quality else [fmt_std]) + [None]
        rc = 1
        for fmt in attempts:
            opt_quality = ['-f', fmt] if fmt else []
            cmd = base_opts + self.cookies_opt + opt_quality + out_opt + opt_sub + [song_url]
            logging.info("Youtube-dl command: " + " ".join(cmd))
            rc = self.call_yt_dlp(cmd)
            if rc == 0:
                break
            logging.error(f"Download failed with format '{fmt}', trying next fallback ...")

        if rc == 0:
            logging.debug("Song successfully downloaded: " + song_url)
            self.downloading_songs[song_url] = 0
            bn = self.get_downloaded_file_basename(song_url)
            if bn:
                shutil.move(self.tmp_dir + '/' + bn, self.download_path + bn)

                # Move any SRT subtitle files to the subs subfolder.
                basestem_match = os.path.splitext(bn)[0]
                for srt_file in glob.glob(self.tmp_dir + '/*.srt'):
                    try:
                        dst_srt = os.path.join(self.download_path, 'subs', basestem_match + '.srt')
                        shutil.move(srt_file, dst_srt)
                        logging.debug(f"Moved subtitle file to: {dst_srt}")
                        break  # one language per video
                    except Exception:
                        pass

                self.get_available_songs()
                if enqueue:
                    self.enqueue(self.download_path + bn, song_added_by)
                    self.downloading_songs[song_url] = '00'
                    flash(getString2(189) + ' ' + getString2(191), client_ip=client_ip)
                else:
                    flash(getString2(189), client_ip=client_ip)
            else:
                logging.error("Error queueing song: " + song_url)
                self.downloading_songs[song_url] = '01'
                flash(getString2(189) + ' ' + getString2(192), client_ip=client_ip)
        else:
            logging.error("Error downloading song: " + song_url)
            self.downloading_songs[song_url] = -1
            flash(getString2(190), client_ip=client_ip)

        return ws_send(client_ip, 'download_ended()')
