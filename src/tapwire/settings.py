"""Settings for a recording session."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class PauseMode(str, Enum):
    DROP = "drop"  # pausing discards the track (default: never produces a spliced file)
    JOIN = "join"  # experimental: suspends and resumes in the same file


def default_out_dir() -> Path:
    music = os.environ.get("XDG_MUSIC_DIR") or Path.home() / "Music"
    return Path(music) / "tapwire"


@dataclass
class Settings:
    player: str = ""                   # MPRIS name (without the org.mpris.MediaPlayer2. prefix)
    out_dir: Path = field(default_factory=default_out_dir)
    pause_mode: PauseMode = PauseMode.DROP
    max_tracks: int = 0                # stop after this many saved tracks; 0 = manual (until stopped)
    end_tolerance: float = 3.0         # s: track is "complete" if recorded >= length - tolerance
    seek_grace: float = 1.5            # s: a Seeked right after recording starts is ignored
    seek_tolerance: float = 3.0        # s: a Seeked this close to the recorded time is not a real jump
    start_tolerance: float = 2.0       # s: playing at a position <= this counts as "from the start"

    def __post_init__(self) -> None:
        if self.max_tracks < 0:
            raise ValueError("max_tracks must be 0 (manual) or more")
        self.out_dir = Path(self.out_dir).expanduser().resolve()  # absolute: never looks like an option/protocol

    @property
    def app_hint(self) -> str:
        """Fragment used to find the app's audio stream ('chromium.instance123' -> 'chromium')."""
        return self.player.split(".")[0]
