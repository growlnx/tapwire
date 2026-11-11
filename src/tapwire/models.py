"""Plain data types (no GLib/GTK): track and playback state."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

_DESKTOP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}")  # no "/", so it can only name a file in the apps dirs
_OBJECT_PATH = re.compile(r"/[A-Za-z0-9_]+(/[A-Za-z0-9_]+)*")  # D-Bus object path syntax
MAX_TAG_CHARS = 500


def _text(value) -> str:
    """Metadata from an untrusted player: anything that is not a string counts as empty."""
    return value if isinstance(value, str) else ""


def _texts(value) -> tuple[str, ...]:
    """A list of strings; a bare string, wrong types or a non-list yield only the valid strings."""
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, (list, tuple)) else ()


def _number(value, default=0):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def valid_desktop_id(value) -> str:
    """The player-supplied DesktopEntry if it is a plain .desktop file name stem, else ''."""
    return value if isinstance(value, str) and _DESKTOP_ID.fullmatch(value) else ""


def clean_tag(value: str) -> str:
    """Tag text without control/invisible characters, newlines folded to spaces, length-capped."""
    kept = (" " if ch.isspace() else ch for ch in value
            if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))
    return "".join(kept).strip()[:MAX_TAG_CHARS]


class PlaybackStatus(str, Enum):
    PLAYING = "Playing"
    PAUSED = "Paused"
    STOPPED = "Stopped"

    @classmethod
    def parse(cls, value: str | None) -> "PlaybackStatus":
        try:
            return cls(value)
        except ValueError:
            return cls.STOPPED


@dataclass(frozen=True)
class PlayerInfo:
    """What a running MPRIS player says about itself (all of it untrusted text)."""
    name: str                 # D-Bus name without the org.mpris.MediaPlayer2. prefix, e.g. "chromium.instance123"
    identity: str             # friendly application name, e.g. "Rhythmbox"
    status: "PlaybackStatus"
    track: "Track | None" = None
    desktop_entry: str = ""   # basename of the app's .desktop file ("org.gnome.Rhythmbox3"), validated, may be empty


@dataclass(frozen=True)
class Track:
    track_id: str
    title: str = ""
    artists: tuple[str, ...] = ()
    album: str = ""
    album_artists: tuple[str, ...] = ()
    number: int | None = None
    url: str = ""
    art_url: str = ""
    length: float = 0.0  # seconds; 0 = unknown

    @classmethod
    def from_mpris(cls, meta: dict) -> "Track | None":
        """Builds from the MPRIS `Metadata` dict; None if there is no track."""
        track_id = meta.get("mpris:trackid")
        # must be a valid D-Bus object path: it is sent back to the player on seek
        if not isinstance(track_id, str) or not _OBJECT_PATH.fullmatch(track_id):
            return None
        number = meta.get("xesam:trackNumber")
        return cls(
            track_id=track_id,
            title=_text(meta.get("xesam:title")),
            artists=_texts(meta.get("xesam:artist")),
            album=_text(meta.get("xesam:album")),
            album_artists=_texts(meta.get("xesam:albumArtist")),
            number=number if isinstance(number, int) and not isinstance(number, bool) and number > 0 else None,
            url=_text(meta.get("xesam:url")),
            art_url=_text(meta.get("mpris:artUrl")),
            length=max(float(_number(meta.get("mpris:length"))), 0.0) / 1e6,
        )

    @property
    def label(self) -> str:
        return f"{', '.join(self.artists) or '?'} - {self.title or '?'}"

    def tags(self) -> dict[str, str]:
        """Tags for the file; empty fields are left out."""
        tags = {
            "artist": ", ".join(self.artists),
            "title": self.title,
            "album": self.album,
            "album_artist": ", ".join(self.album_artists),
            "track": str(self.number) if self.number else "",
            "comment": self.url,
        }
        cleaned = {k: clean_tag(v) for k, v in tags.items()}
        return {k: v for k, v in cleaned.items() if v}
