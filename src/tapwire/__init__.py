"""tapwire: records, track by track, what an app plays through PipeWire."""
from .events import DropReason, Event, EventKind, Signal
from .models import PlaybackStatus, Track
from .session import RecordingSession
from .settings import PauseMode, Settings

__version__ = "0.1.0"
__all__ = ["DropReason", "Event", "EventKind", "PauseMode", "PlaybackStatus", "RecordingSession",
           "Settings", "Signal", "Track"]
