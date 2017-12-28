# pylint: disable=C0111,W0603
import logging as l
import wave as wav
from queue import Queue, Empty
from threading import Thread, Event, Lock
import jack
from .music_data import build_track_data, SongInfo, Song, map_dubs_type
from .jack_client import J_CLIENT, MUSIC_L, MUSIC_R

CURR_SONG: Song = None
CURR_SONG_LOCK = Lock()
PLAYING = Event()
PLAYING.clear()
CHANNELS = 2
BITRATE = 48000


@J_CLIENT.set_process_callback
def process(frames):
    assert frames == J_CLIENT.blocksize
    l_buffer = MUSIC_L.get_array()
    r_buffer = MUSIC_R.get_array()
    if not PLAYING.is_set() or CURR_SONG is None:
        return
    if not CURR_SONG_LOCK.acquire(blocking=False):
        return
    CURR_SONG.write_buffer(l_buffer, r_buffer)
    CURR_SONG_LOCK.release()


@J_CLIENT.set_xrun_callback
def got_xrun(usecs):
    if usecs < 0.0001:
        l.debug("Minor XRun, %lfusecs", usecs)
        return
    l.warning("Jack just had a XRun, and lost %.4fusecs, is the CPU under "
              "heavy load?", usecs)


def finish_jack():
    with CURR_SONG_LOCK:
        if CURR_SONG is not None:
            CURR_SONG.flush()


def song_event_handler(*args, **kwargs: dict):
    if len(args) <= 0 or args[0] != 'org.mpris.MediaPlayer2.Player':
        return
    status = args[1].get('PlaybackStatus', '')
    dicta: dict = args[1].get("Metadata", dict())
    song_info = build_track_data(dicta)
    l.debug("Got song event, the status is: %s", status)
    play = status == 'Playing'
    was_playing = PLAYING.is_set()
    if play:
        PLAYING.set()
    else:
        PLAYING.clear()
        return
    with CURR_SONG_LOCK:
        global CURR_SONG
        if song_info.artist[0] == '':
            PLAYING.clear()
            if CURR_SONG is not None:
                CURR_SONG.flush()
                CURR_SONG = None
            l.info('Advertisement, skipping.')
            return
        if CURR_SONG is not None:
            if play and not was_playing and \
                    song_info.title == CURR_SONG.info.title:
                l.info('Resuming!')
                return
            CURR_SONG.flush()
            if CURR_SONG.info.title != song_info.title:
                l.info('Started recording %s by %s',
                       song_info.title, song_info.artist[0])
        song = Song(song_info)
        CURR_SONG = song