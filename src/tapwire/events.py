"""Simple signals and the events the recording session publishes to clients."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from .models import Track


class Signal:
    """Minimal observer: connect(callback) / emit(*args)."""

    def __init__(self) -> None:
        self._subscribers: list[Callable] = []

    def connect(self, callback: Callable) -> Callable:
        self._subscribers.append(callback)
        return callback

    def disconnect(self, callback: Callable) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def emit(self, *args) -> None:
        for callback in list(self._subscribers):
            callback(*args)


class EventKind(Enum):
    READY = "ready"          # session started; the in-progress track is ignored
    RECORDING = "recording"  # started recording a track
    PAUSED = "paused"        # recording suspended (join mode)
    RESUMED = "resumed"
    SAVED = "saved"          # track recorded and converted to FLAC
    DROPPED = "dropped"      # recording discarded (see DropReason)
    WARNING = "warning"
    ERROR = "error"
    LIMIT = "limit"          # the requested number of tracks was saved; the session stops itself
    STOPPED = "stopped"      # session ended


class DropReason(Enum):
    PAUSED = "paused"
    SEEKED = "seeked"
    TRACK_CHANGED = "track_changed"
    PLAYER_STOPPED = "player_stopped"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    track: Track | None = None
    path: Path | None = None
    reason: DropReason | None = None
    message: str = ""
    count: int = 0
