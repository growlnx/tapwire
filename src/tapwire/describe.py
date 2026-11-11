"""Human-readable (translated) text for session events, shared by the clients."""
from __future__ import annotations

from .events import DropReason, Event, EventKind
from .i18n import _, pgettext
from .models import PlaybackStatus, PlayerInfo
from .text import printable


def _reason_text(reason: DropReason) -> str:
    return {
        DropReason.PAUSED: _("paused"),
        DropReason.SEEKED: _("seeked within the track"),
        DropReason.TRACK_CHANGED: _("track changed"),
        DropReason.PLAYER_STOPPED: _("player stopped"),
        DropReason.SHUTDOWN: _("session ended"),
    }[reason]


def describe(event: Event) -> str:
    track = printable(event.track.label) if event.track else ""
    kind = event.kind
    if kind is EventKind.READY:
        if track:
            return _("Ready; {track} is already playing, recording starts with the next track").format(track=track)
        return _("Ready")
    if kind is EventKind.RECORDING:
        return _("Recording: {track}").format(track=track)
    if kind is EventKind.PAUSED:
        return _("Paused: {track}").format(track=track)
    if kind is EventKind.RESUMED:
        return _("Resumed: {track}").format(track=track)
    if kind is EventKind.SAVED:
        return _("Saved: {track} → {path}").format(track=track, path=event.path)
    if kind is EventKind.DROPPED:
        return _("Discarded ({reason}): {track}").format(
            reason=_reason_text(event.reason) if event.reason else "", track=track)
    if kind is EventKind.WARNING:
        return _("Warning: {message}").format(message=printable(event.message))
    if kind is EventKind.ERROR:
        return _("Error: {message}").format(message=printable(event.message))
    if kind is EventKind.LIMIT:
        return _("Done: {count} tracks saved").format(count=event.count)
    return _("Stopped")


def _status_text(status: PlaybackStatus) -> str:
    return {
        PlaybackStatus.PLAYING: pgettext("player status", "playing"),
        PlaybackStatus.PAUSED: pgettext("player status", "paused"),
        PlaybackStatus.STOPPED: pgettext("player status", "stopped"),
    }[status]


def player_label(info: PlayerInfo, max_chars: int = 70) -> str:
    """One line for a player selector: 'Rhythmbox — Artist - Title' or 'Code (stopped)'."""
    identity = printable(info.identity)
    if info.track and info.status is not PlaybackStatus.STOPPED:
        text = f"{identity} — {printable(info.track.label)}"
    else:
        text = _("{identity} ({status})").format(identity=identity, status=_status_text(info.status))
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
