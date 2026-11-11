"""A recording in progress (one track)."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .models import Track

if TYPE_CHECKING:  # avoids importing GLib just for the type
    from .recorder import RecorderLike


class Take:
    """A track being recorded: files, time actually recorded and pause state."""

    def __init__(self, track: Track, final: Path, recorder_factory: Callable[[Path], RecorderLike],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.track = track
        self.final = final
        self.part = final.with_name(final.stem + ".part.wav")
        # random name: the cover is untrusted remote data, its file name must not derive from
        # anything the player/server controls (and parallel takes can never collide)
        self.art_base = final.with_name(f".cover-{uuid.uuid4()}")  # the fetcher adds .jpg/.png
        self.art: Path | None = None  # set once the cover has been downloaded
        self._art_lock = threading.Lock()
        self._closed = False
        self.recorder = recorder_factory(self.part)
        self._clock = clock
        self._recorded = 0.0           # seconds accumulated in previous segments
        self._segment_start = clock()  # start of the current segment
        self.paused = False

    @property
    def elapsed(self) -> float:
        """Seconds actually recorded (paused time does not count)."""
        return self._recorded + (0.0 if self.paused else self._clock() - self._segment_start)

    @property
    def segment_age(self) -> float:
        """How many seconds ago the current segment started (start or last resume)."""
        return self._clock() - self._segment_start

    def is_complete(self, tolerance: float) -> bool:
        """True if (almost) the whole length was recorded, i.e. the track ended on its own."""
        return self.track.length > 0 and self.elapsed >= self.track.length - tolerance

    def start(self) -> None:
        self.recorder.start()

    def pause(self) -> None:
        if not self.paused:
            self._recorded = self.elapsed
            self.paused = True
            self.recorder.pause()

    def resume(self) -> None:
        if self.paused:
            self.paused = False
            self._segment_start = self._clock()
            self.recorder.resume()

    def stop(self) -> None:
        self.recorder.stop()

    def set_art(self, path: Path) -> None:
        """Called by the download thread. If the take is already over, the file is removed at once."""
        with self._art_lock:
            if self._closed:
                path.unlink(missing_ok=True)
            else:
                self.art = path

    def close(self) -> Path | None:
        """No more covers will be accepted. Returns the cover downloaded so far, if any."""
        with self._art_lock:
            self._closed = True
            return self.art

    def discard_files(self) -> None:
        self.part.unlink(missing_ok=True)
        art = self.close()
        if art:
            art.unlink(missing_ok=True)
