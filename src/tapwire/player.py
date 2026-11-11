"""MPRIS client: state, signals and control of a player (any app that speaks MPRIS)."""
from __future__ import annotations

from gi.repository import Gio, GLib

from .events import Signal
from .models import PlayerInfo, PlaybackStatus, Track, clean_tag, valid_desktop_id

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
IFACE = "org.mpris.MediaPlayer2.Player"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PROPS = "org.freedesktop.DBus.Properties"


class MprisPlayer:
    """Signals:
    changed(status: PlaybackStatus, track: Track | None) — full state after every change
    seeked(position: float) — the user jumped to another point in the track
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.bus = PREFIX + name
        self.status = PlaybackStatus.STOPPED
        self.track: Track | None = None
        self.changed = Signal()
        self.seeked = Signal()
        self._conn: Gio.DBusConnection | None = None
        self._subs: list[int] = []

    @staticmethod
    def list_players() -> list[str]:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                               "ListNames", None, GLib.VariantType("(as)"), 0, -1, None).unpack()[0]
        return sorted(n[len(PREFIX):] for n in names if n.startswith(PREFIX))

    @staticmethod
    def discover() -> list[PlayerInfo]:
        """Running players with their friendly name, state and current track (blocking, short timeouts)."""
        infos = []
        for name in MprisPlayer.list_players():
            player = MprisPlayer(name)
            player._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            identity = player._get("Identity", ROOT_IFACE)
            player.refresh()
            infos.append(PlayerInfo(
                name=name,
                identity=clean_tag(identity)[:60] if isinstance(identity, str) and identity else name,
                status=player.status, track=player.track,
                desktop_entry=valid_desktop_id(player._get("DesktopEntry", ROOT_IFACE))))
        return infos

    # --- lifecycle ---
    def connect(self) -> bool:
        """Subscribes to the signals and reads the current state. False if the player is not up (keeps subscribing)."""
        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._subs = [
            self._conn.signal_subscribe(self.bus, PROPS, "PropertiesChanged", PATH, None,
                                        Gio.DBusSignalFlags.NONE, self._on_properties),
            self._conn.signal_subscribe(self.bus, IFACE, "Seeked", PATH, None,
                                        Gio.DBusSignalFlags.NONE, self._on_seeked),
        ]
        return self.refresh()

    def disconnect(self) -> None:
        if self._conn:
            for sub in self._subs:
                self._conn.signal_unsubscribe(sub)
        self._subs = []

    def refresh(self) -> bool:
        """Re-reads metadata and status without emitting signals."""
        meta = self._get("Metadata")
        status = self._get("PlaybackStatus")
        if meta is None and status is None:
            return False
        self.track = Track.from_mpris(meta or {})
        self.status = PlaybackStatus.parse(status)
        return True

    # --- reading ---
    def position(self) -> float | None:
        micros = self._get("Position")
        return None if micros is None else micros / 1e6

    def _get(self, prop: str, iface: str = IFACE):
        if not self._conn:
            return None
        try:
            r = self._conn.call_sync(self.bus, PATH, PROPS, "Get", GLib.Variant("(ss)", (iface, prop)),
                                     GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 500, None)
            return r.unpack()[0]
        except GLib.Error:
            return None

    # --- control ---
    def play_pause(self) -> None:
        self._call("PlayPause")

    def next(self) -> None:
        self._call("Next")

    def previous(self) -> None:
        self._call("Previous")

    def seek_to(self, seconds: float) -> None:
        if self.track:
            self._call("SetPosition", GLib.Variant("(ox)", (self.track.track_id, int(seconds * 1e6))))

    def _call(self, method: str, params: GLib.Variant | None = None) -> None:
        if self._conn:
            self._conn.call(self.bus, PATH, IFACE, method, params, None, Gio.DBusCallFlags.NONE,
                            500, None, None, None)

    # --- D-Bus signals ---
    def _on_properties(self, _conn, _sender, _path, _iface, _signal, params, *_):
        iface, changed, _invalidated = params.unpack()
        if iface != IFACE:
            return
        if "Metadata" in changed:
            self.track = Track.from_mpris(changed["Metadata"])
        if "PlaybackStatus" in changed:
            self.status = PlaybackStatus.parse(changed["PlaybackStatus"])
        if "Metadata" in changed or "PlaybackStatus" in changed:
            self.changed.emit(self.status, self.track)

    def _on_seeked(self, _conn, _sender, _path, _iface, _signal, params, *_):
        self.seeked.emit(params.unpack()[0] / 1e6)
