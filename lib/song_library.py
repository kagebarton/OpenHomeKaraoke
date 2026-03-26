"""
Song library — filesystem CRUD for the local song collection.

Extracted from karaoke.py.  Add to the Karaoke class via inheritance:

    from lib.song_library import SongLibraryMixin

    class Karaoke(SongLibraryMixin, ...):
        ...

Expected attributes on `self`:
    self.download_path   – root folder that holds the song files
    self.songname_trans  – dict[filepath, transliterated_name]  (maintained here)
    self.available_songs – sorted list of full file paths        (maintained here)
    self.rename_history  – dict[old_basename, new_basename]      (maintained here)
    self.delays          – per-song data dict (from ConfigMixin)
    self.delays_dirty    – dirty flag         (from ConfigMixin)
    self.auto_save_delays – method            (from ConfigMixin)
    self.queue           – queue list         (from QueueMixin)
"""

import logging
import os
import shutil

from unidecode import unidecode

from constants import media_types


class SongLibraryMixin:

    def filename_from_path(self, file_path: str) -> str:
        rc = os.path.basename(file_path)
        rc = os.path.splitext(rc)[0]
        rc = rc.split("---")[0]  # removes YouTube id if present
        return rc

    def get_available_songs(self) -> None:
        logging.info("Fetching available songs in: " + self.download_path)
        files_grabbed = []
        self.songname_trans = {}
        for bn in os.listdir(self.download_path):
            fn = self.download_path + bn
            if not bn.startswith('.') and os.path.isfile(fn):
                if os.path.splitext(fn)[1].lower() in media_types:
                    files_grabbed.append(fn)
                    trans = unidecode(self.filename_from_path(fn)).lower()
                    # strip leading non-transliterable symbols
                    while trans and not trans[0].islower() and not trans[0].isdigit():
                        trans = trans[1:]
                    self.songname_trans[fn] = trans
        self.available_songs = sorted(self.songname_trans, key=self.songname_trans.get)

    def get_all_assoc_files(self, song_path: str) -> list:
        """Return all file paths associated with a song (main file + CDG/vocal/subs)."""
        basename = os.path.basename(song_path)
        basestem = os.path.splitext(basename)
        return [
            self.download_path + basename,
            self.download_path + basestem[0] + '.cdg',
            self.download_path + 'nonvocal/' + basename + '.m4a',
            self.download_path + 'nonvocal/.' + basename + '.m4a',
            self.download_path + 'vocal/' + basename + '.m4a',
            self.download_path + 'vocal/.' + basename + '.m4a',
            self.download_path + 'subs/' + basestem[0] + '.srt',
        ]

    def delete_if_exist(self, filename: str) -> None:
        if os.path.isfile(filename):
            try:
                os.remove(filename)
            except Exception:
                pass

    def delete(self, song_path: str) -> None:
        logging.info("Deleting song: " + song_path)
        for fn in self.get_all_assoc_files(song_path):
            self.delete_if_exist(fn)
        self.get_available_songs()

    def rename_if_exist(self, old_path: str, new_path: str) -> None:
        if os.path.isfile(old_path):
            try:
                shutil.move(old_path, new_path)
            except Exception:
                pass

    def rename(self, song_path: str, new_basestem: str) -> None:
        logging.info("Renaming song: '" + song_path + "' to: " + new_basestem)
        ext = os.path.splitext(song_path)
        if len(ext) < 2:
            ext += ['']
        new_basename = new_basestem + ext[1]

        # Handle the case where the file has been renamed multiple times while
        # the vocal splitter was still processing it.
        old_basename = os.path.basename(song_path)
        self.rename_history[old_basename] = new_basename
        for k, v in self.rename_history.items():
            if v == old_basename:
                self.rename_history[k] = new_basename

        # Rename all associated CDG / vocal / nonvocal files if they exist.
        for src, tgt in zip(
            self.get_all_assoc_files(song_path),
            self.get_all_assoc_files(new_basename),
        ):
            self.rename_if_exist(src, tgt)

        # Update any matching queue entry in-place.
        for item in self.queue:
            if item['file'] == song_path:
                item['file'] = self.download_path + new_basename
                item['title'] = self.filename_from_path(item['file'])
                break

        # Migrate saved delays to the new filename.
        if self.save_delays and old_basename in self.delays:
            self.delays[new_basename] = self.delays.pop(old_basename)
            self.delays_dirty = True
            self.auto_save_delays()

        self.get_available_songs()
