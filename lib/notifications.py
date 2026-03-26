"""
Shared websocket state and flash notifications.

Extracted from karaoke.py so that both karaoke.py and lib/ submodules
can import these without creating circular dependencies.

karaoke.py and app.py should replace their local definitions with:
    from lib.notifications import flash, ws_send, ip2websock, ip2pane
"""

from flask import request

ip2websock: dict = {}
ip2pane: dict = {}

ws_send = lambda ip, msg: ip2websock[ip].send(msg) if ip in ip2websock else None


def flash(message: str, category: str = "message", client_ip: str = '') -> None:
    ws_send(client_ip or request.remote_addr, f'showNotification("{message}", "{category}")')
