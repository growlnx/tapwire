"""Test doubles to exercise the session without PipeWire, D-Bus or ffmpeg."""
from tapwire.events import Signal
from tapwire.models import PlaybackStatus, Track


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakePlayer:
    def __init__(self, track=None, status=PlaybackStatus.STOPPED):
        self.track, self.status = track, status
        self.changed, self.seeked = Signal(), Signal()
        self.available = True
        self.pos = 0.0  # what position() reports

    def connect(self):
        return self.available

    def disconnect(self):
        pass

    def position(self):
        return self.pos

    # test helpers
    def set(self, status=None, track=None):
        if status is not None:
            self.status = status
        if track is not None:
            self.track = track
        self.changed.emit(self.status, self.track)


class FakeRecorder:
    def __init__(self, part):
        self.part, self.calls = part, []
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"wav")

    def start(self): self.calls.append("start")
    def pause(self): self.calls.append("pause")
    def resume(self): self.calls.append("resume")
    def stop(self): self.calls.append("stop")


class FakeEncoder:
    def __init__(self, ok=True):
        self.ok, self.encoded = ok, []

    def encode(self, part, final, track, art, on_done):
        self.encoded.append((part, final, track))
        if self.ok:
            final.write_bytes(b"flac")
            part.unlink(missing_ok=True)
        on_done(self.ok)


def track(tid, length=100.0, **kw):
    return Track(track_id=f"/t/{tid}", title=kw.pop("title", tid), artists=("Artista",), length=length, **kw)
