"""Recording session: a state machine tying an MPRIS player to per-track recordings.

Rules:
- only records a track it saw START: a track change, or playback (re)starting from the beginning
  (position <= start_tolerance, e.g. you zeroed the track and pressed play). The track already
  playing at startup, or resumed from the middle after a pause, is not recorded;
- pause, seek and a mid-track track change DISCARD the recording;
- saves only when the recorded time reaches (length - tolerance), i.e. the track ended on its own.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from .i18n import _
from .events import DropReason, Event, EventKind, Signal
from .models import PlaybackStatus, Track
from .naming import ensure_inside, track_filename, unique_path
from .settings import PauseMode, Settings
from .take import Take
from .tools import ToolNotFound

if TYPE_CHECKING:
    from .recorder import RecorderLike


class PlayerLike(Protocol):
    changed: Signal
    seeked: Signal
    status: PlaybackStatus
    track: Track | None

    def connect(self) -> bool: ...
    def disconnect(self) -> None: ...
    def position(self) -> float | None: ...


class RecordingSession:
    def __init__(self, settings: Settings, player: PlayerLike,
                 recorder_factory: Callable[[Path], RecorderLike],
                 encoder, art_fetcher=None, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.player = player
        self.events = Signal()  # emits Event
        self._recorder_factory = recorder_factory
        self._encoder = encoder
        self._art_fetcher = art_fetcher
        self._clock = clock
        self._take: Take | None = None
        self._tid: str | None = None   # current track
        self._ignored = True           # the current track must not be (re)recorded
        self._running = False
        self._saved = 0                # tracks saved so far
        self._encoding = 0             # tracks being converted right now
        self.finished_by_limit = False

    @classmethod
    def create(cls, settings: Settings, player: PlayerLike | None = None) -> "RecordingSession":
        """Builds the session with the real implementations (PipeWire, MPRIS, ffmpeg)."""
        from .art import ArtFetcher
        from .encoder import FlacEncoder
        from .pipewire import PipeWire
        from .player import MprisPlayer
        from .recorder import Recorder

        pipewire = PipeWire()
        session: RecordingSession

        def make_recorder(part: Path) -> Recorder:
            return Recorder(settings.app_hint, part, pipewire,
                            on_warning=lambda msg: session._emit(EventKind.WARNING, message=msg))

        session = cls(settings, player or MprisPlayer(settings.player), make_recorder, FlacEncoder(),
                      ArtFetcher(on_error=lambda msg: session._emit(EventKind.WARNING, message=msg)))
        return session

    # --- lifecycle ---
    @property
    def current_take(self) -> Take | None:
        return self._take

    def start(self) -> None:
        self.settings.out_dir.mkdir(parents=True, exist_ok=True)
        self.player.changed.connect(self._on_changed)
        self.player.seeked.connect(self._on_seeked)
        available = self.player.connect()
        track = self.player.track
        self._tid = track.track_id if track else None
        self._ignored = True  # a track already in progress cannot be recorded in full
        self._running = True
        if not available:
            self._emit(EventKind.WARNING, message=_("player '{player}' not found; waiting").format(player=self.settings.player))
        playing = track is not None and self.player.status is PlaybackStatus.PLAYING
        if playing and self._at_start():
            # it has only just started: record it from here on, like any track that begins
            self._ignored = False
            self._emit(EventKind.READY)
            self._begin(track)
        else:
            # READY carries the track only when it is really playing midway (so it gets skipped)
            self._emit(EventKind.READY, track=track if playing else None)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self.player.changed.disconnect(self._on_changed)
        self.player.seeked.disconnect(self._on_seeked)
        self.player.disconnect()
        if self._take:
            self._drop(DropReason.SHUTDOWN)
        self._emit(EventKind.STOPPED)

    # --- reacting to the player ---
    def _on_changed(self, status: PlaybackStatus, track: Track | None) -> None:
        tid = track.track_id if track else None
        track_changed = tid != self._tid
        if track_changed:
            self._tid = tid
            self._ignored = False
        playing = status is PlaybackStatus.PLAYING

        take = self._take
        if take:
            finished = take.is_complete(self.settings.end_tolerance)
            if track_changed:
                self._finish(finished, DropReason.TRACK_CHANGED)
            elif status is PlaybackStatus.PAUSED:
                if finished:
                    self._finish(True)
                elif self.settings.pause_mode is PauseMode.JOIN:
                    take.pause()
                    self._emit(EventKind.PAUSED, track=take.track)
                else:
                    self._drop(DropReason.PAUSED)
            elif not playing:
                self._finish(finished, DropReason.PLAYER_STOPPED)
            elif take.paused:
                take.resume()
                self._emit(EventKind.RESUMED, track=take.track)

        if track_changed:
            self._ignored = False  # closing the previous take marks "ignored"; the new track is eligible

        if playing and track and not self._take and (not self._ignored or self._at_start()):
            self._begin(track)

    def _on_seeked(self, position: float) -> None:
        take = self._take
        if take and not take.paused and take.segment_age > self.settings.seek_grace:
            # Players also emit Seeked on their own (end of track, resync). Only a real jump counts:
            # the new position must be far from where the recording says playback should be, and a
            # take that already covers the whole track is not thrown away.
            expected = take.elapsed
            if (not take.is_complete(self.settings.end_tolerance)
                    and abs(position - expected) > self.settings.seek_tolerance):
                self._drop(DropReason.SEEKED)
        # jumped back to the beginning while playing: the track is playing from the start again
        track = self.player.track
        if (not self._take and track and self.player.status is PlaybackStatus.PLAYING
                and position <= self.settings.start_tolerance):
            self._begin(track)

    def _at_start(self) -> bool:
        """True if the player is currently near the beginning of the track (asks the player)."""
        position = self.player.position()
        return position is not None and position <= self.settings.start_tolerance

    # --- takes ---
    def _limit_reached(self) -> bool:
        """True once enough tracks are saved, or being saved, to satisfy max_tracks."""
        limit = self.settings.max_tracks
        return limit > 0 and self._saved + self._encoding >= limit

    def _begin(self, track: Track) -> None:
        if self._limit_reached():
            self._ignored = True
            return
        final = unique_path(self.settings.out_dir / (track_filename(", ".join(track.artists), track.title) + ".flac"))
        try:
            ensure_inside(self.settings.out_dir, final)
        except ValueError as exc:
            self._ignored = True
            self._emit(EventKind.ERROR, track=track, message=str(exc))
            return
        try:
            take = Take(track, final, self._recorder_factory, self._clock)
        except (ToolNotFound, OSError) as exc:  # building the recorder can already fail
            self._ignored = True
            self._emit(EventKind.ERROR, track=track, message=str(exc))
            return
        self._take = take
        if self._art_fetcher and track.art_url:
            self._art_fetcher.fetch(track.art_url, take.art_base, take.set_art)
        try:
            take.start()
        except (ToolNotFound, OSError) as exc:
            self._drop_failed_start(take)
            self._emit(EventKind.ERROR, track=track, message=str(exc))
            return
        self._emit(EventKind.RECORDING, track=track)

    def _drop_failed_start(self, take: Take) -> None:
        take.discard_files()
        self._take = None
        self._ignored = True

    def _finish(self, keep: bool, reason: DropReason | None = None) -> None:
        """Closes the current take: saves it if `keep`, otherwise discards it."""
        if keep:
            self._save()
        else:
            self._drop(reason or DropReason.TRACK_CHANGED)

    def _save(self) -> None:
        take, self._take = self._take, None
        self._ignored = True
        take.stop()
        self._encoding += 1

        def done(ok: bool) -> None:
            self._encoding -= 1
            if ok:
                self._saved += 1
                self._emit(EventKind.SAVED, track=take.track, path=take.final)
                if self._running and self.settings.max_tracks and self._saved >= self.settings.max_tracks:
                    self.finished_by_limit = True
                    self._emit(EventKind.LIMIT, count=self._saved)
                    self.stop()
            else:
                self._emit(EventKind.ERROR, track=take.track,
                           message=_("ffmpeg failed; raw WAV kept at {path}").format(path=take.part))

        try:
            self._encoder.encode(take.part, take.final, take.track, take.close(), done)
        except (ToolNotFound, OSError) as exc:
            self._encoding -= 1  # done() will never run
            self._emit(EventKind.ERROR, track=take.track, message=f"{exc} ({take.part})")

    def _drop(self, reason: DropReason) -> None:
        take, self._take = self._take, None
        self._ignored = True  # do not re-record the same track from the middle
        take.stop()
        take.discard_files()
        self._emit(EventKind.DROPPED, track=take.track, reason=reason)

    def _emit(self, kind: EventKind, **fields) -> None:
        self.events.emit(Event(kind, **fields))
