"""Test package bootstrap.

This blocks outbound network calls in unit tests so Reddit/Temporal APIs are
never reached accidentally.
"""

import socket

from ._env import ensure_test_env

ensure_test_env()


def _blocked_network(*args, **kwargs):
    raise RuntimeError("Network access is blocked in unit tests")


socket.create_connection = _blocked_network
socket.socket.connect = _blocked_network
socket.socket.connect_ex = _blocked_network
