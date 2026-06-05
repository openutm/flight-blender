"""Minimal Signal implementation — drop-in for django.dispatch.Signal.

Pure dispatch pattern with no I/O; lives in core so operations modules can
declare and connect receivers without depending on infrastructure.
"""


class Signal:
    def __init__(self):
        self._receivers: list = []

    def connect(self, receiver, sender=None, **kwargs):
        self._receivers.append(receiver)

    def send(self, sender=None, **kwargs):
        for receiver in self._receivers:
            try:
                receiver(sender=sender, **kwargs)
            except Exception:
                pass
        return []

    def disconnect(self, receiver=None, **kwargs):
        if receiver in self._receivers:
            self._receivers.remove(receiver)


def receiver(signal, **kwargs):
    def decorator(func):
        signal.connect(func)
        return func

    return decorator
