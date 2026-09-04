"""Process-wide internet guard for V5.1 subprocess characterization tests."""

import socket

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guarded_connect(self, *args, **kwargs):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise AssertionError("internet socket blocked by V5.1 subprocess harness")
    return _real_connect(self, *args, **kwargs)


def _guarded_connect_ex(self, *args, **kwargs):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise AssertionError("internet socket blocked by V5.1 subprocess harness")
    return _real_connect_ex(self, *args, **kwargs)


def _blocked_connection(*_args, **_kwargs):
    raise AssertionError("internet connection blocked by V5.1 subprocess harness")


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.create_connection = _blocked_connection
