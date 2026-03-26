"""
In-memory playback queue management for PiKaraoke.

Extracted from karaoke.py.  Add to the Karaoke class via inheritance:

    from lib.queue_manager import QueueMixin

    class Karaoke(QueueMixin, ...):
        ...

Expected attributes on `self`:
    self.queue           – list of dicts: [{"user": str, "file": str, "title": str}, ...]
    self.queue_json      – JSON string mirror of self.queue  (maintained here)
    self.available_songs – list of full file paths (from SongLibraryMixin)
    self.status_dirty    – bool flag watched by the status broadcast thread
    self.filename_from_path – method (from SongLibraryMixin)
"""

import json
import logging
import random


class QueueMixin:

    def is_song_in_queue(self, song_path: str) -> bool:
        return song_path in map(lambda t: t['file'], self.queue)

    def enqueue(self, song_path: str, user: str = "Pikaraoke") -> bool:
        if self.is_song_in_queue(song_path):
            logging.warning("Song is already in queue, will not add: " + song_path)
            return False
        logging.info("'%s' is adding song to queue: %s" % (user, song_path))
        self.queue.append({
            "user": user,
            "file": song_path,
            "title": self.filename_from_path(song_path),
        })
        self.update_queue()
        return True

    def queue_add_random(self, amount: int) -> bool:
        logging.info("Adding %d random songs to queue" % amount)
        songs = list(self.available_songs)  # copy so we can pop without mutating
        if not songs:
            logging.warning("No available songs!")
            return False
        i = 0
        while i < amount:
            r = random.randint(0, len(songs) - 1)
            if self.is_song_in_queue(songs[r]):
                logging.warning("Song already in queue, trying another... " + songs[r])
            else:
                self.queue.append({
                    "user": "Random",
                    "file": songs[r],
                    "title": self.filename_from_path(songs[r]),
                })
                i += 1
            songs.pop(r)
            if not songs:
                self.update_queue()
                logging.warning("Ran out of songs!")
                return False
        self.update_queue()
        return True

    def update_queue(self) -> None:
        self.queue_json = json.dumps(self.queue)
        self.status_dirty = True

    def queue_clear(self) -> None:
        logging.info("Clearing queue!")
        self.queue = []
        self.update_queue()

    def queue_edit(self, song_file, action: str, **kwargs) -> bool:
        if action == "move":
            try:
                src, tgt, size = [int(kwargs[n]) for n in ['src', 'tgt', 'size']]
                if size > len(self.queue):
                    # New songs started playing while the user was dragging the list.
                    diff = size - len(self.queue)
                    src -= diff
                    tgt -= diff
                song = self.queue.pop(src)
                self.queue.insert(tgt, song)
            except Exception:
                logging.error("Invalid move song request: " + str(kwargs))
                return False
        else:
            match = [(ii, each) for ii, each in enumerate(self.queue) if song_file in each["file"]]
            index, song = match[0] if match else (-1, None)
            if song is None:
                logging.error("Song not found in queue: " + str(song_file))
                return False
            if action == "up":
                if index < 1:
                    logging.warning("Song is up next, can't bump up in queue: " + song["file"])
                    return False
                logging.info("Bumping song up in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index - 1, song)
            elif action == "down":
                if index == len(self.queue) - 1:
                    logging.warning("Song is already last, can't bump down in queue: " + song["file"])
                    return False
                logging.info("Bumping song down in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index + 1, song)
            elif action == "delete":
                logging.info("Deleting song from queue: " + song["file"])
                del self.queue[index]
            else:
                logging.error("Unrecognized action: " + action)
                return False
        self.update_queue()
        return True
