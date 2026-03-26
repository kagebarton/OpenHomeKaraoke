"""
Cloud automatic speech recognition (ASR) helpers for PiKaraoke.

Extracted from app.py.  Register the routes in app.py with:

    from lib.asr import register_asr_routes
    register_asr_routes(app, lambda: K, lambda: args, getString)

Or wire up run_asr / add_spoken_async manually for testing.

Dependencies:
    requests            – HTTP calls to the cloud ASR service
    lib.NLP             – findMedia
    lib.get_platform    – asr_postprocess
    lib.notifications   – ip2websock (websocket push)
"""

import json
import logging
import sys
import threading

import requests

from lib.NLP import findMedia
from lib.get_platform import asr_postprocess
from lib.notifications import ip2websock


def run_asr(tmp_dir: str, cloud_url: str) -> dict:
    """POST the buffered audio to the cloud ASR endpoint and return the result dict."""
    with open(f'{tmp_dir}/rec.webm', 'rb') as f:
        r = requests.post(cloud_url + '/run_asr/base', files={'file': f}, timeout=8)
    return json.loads(r.text) if r.status_code == 200 else {}


def add_spoken_async(
    client_ip: str,
    user: str,
    tmp_dir: str,
    cloud_url: str,
    download_path: str,
    enqueue_fn,
    filename_from_path_fn,
    getString,
) -> None:
    """Run ASR and enqueue or suggest the recognised song (called in a background thread)."""
    asr_output = run_asr(tmp_dir, cloud_url)

    print(f'ASR result: {asr_output}', file=sys.stderr)
    if asr_output == {} or type(asr_output) == str:
        return logging.error('Cloud ASR returned an unexpected response')
    if not asr_output['text']:
        return logging.error('ASR output is empty')

    res = findMedia(download_path, asr_postprocess(asr_output['text']), lang=asr_output['language'])
    ws = ip2websock.get(client_ip, '')
    if not res:
        return ws.send(f"showNotification('{getString(226) % asr_output['text']}', 'is-info')")

    res_titles = [filename_from_path_fn(s) for s in res]
    if len(res) == 1:
        add_res = enqueue_fn(res[0], user)
        return ws.send(
            f'add1song("{res_titles[0]}","{res[0]}")'
            if add_res
            else f"showNotification('{getString(116) + res_titles[0]}', 'is-info')"
        )
    return ws.send(f"addSongs('{json.dumps([res_titles, res])}')")


def register_asr_routes(app, get_K, get_args, getString):
    """
    Attach the /add_spoken and /get_ASR routes to *app*.

    Pass callables rather than the objects directly so the routes always
    see the current value of K and args (they are set after the Flask app
    is created).

        register_asr_routes(app, lambda: K, lambda: args, getString)
    """
    from flask import request

    @app.route('/add_spoken/<user>', methods=['POST'])
    def add_spoken(user):
        K, args = get_K(), get_args()
        with open(f'{K.tmp_dir}/rec.webm', 'wb') as fp:
            fp.write(request.data)
        threading.Thread(
            target=add_spoken_async,
            args=(
                request.remote_addr, user,
                K.tmp_dir, args.cloud,
                K.download_path, K.enqueue, K.filename_from_path, getString,
            ),
        ).start()
        return 'OK'
