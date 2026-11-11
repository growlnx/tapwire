"""Player component: cover art + title/artist/album + progress bar + controls."""
from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..art import ArtError, fetch_image  # noqa: E402
from ..models import PlaybackStatus, Track  # noqa: E402
from ..player import MprisPlayer  # noqa: E402
from .cover import CoverPaintable  # noqa: E402

ART_MIN = 96     # px: the cover never gets smaller than this (the page scrolls instead)
ART_COMPACT = 112  # px: size beside the text in the narrow, single-column layout


def format_time(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PlayerView(Gtk.Box):
    """Shows and controls an MprisPlayer. Switch players with bind()."""

    def __init__(self) -> None:
        super().__init__()
        self.player: MprisPlayer | None = None
        self._art_url: str | None = None

        # the card lives in a frame; the margin stays inside so the top aligns with the other columns
        frame = Gtk.Box(hexpand=True)
        frame.add_css_class("card")
        self.append(frame)
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, hexpand=True,
                            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        frame.append(self.card)

        # The art area is always a square that fills the column (capped), with or without a cover:
        # CoverPaintable has a fixed 1:1 aspect ratio and draws a flat background until a cover arrives.
        self.cover = CoverPaintable()
        self.art = Gtk.Picture(paintable=self.cover, content_fit=Gtk.ContentFit.FILL, can_shrink=True)
        self.placeholder = Gtk.Image(icon_name="audio-x-generic-symbolic", pixel_size=64,
                                     halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
        self.placeholder.add_css_class("dim-label")
        self.art_box = overlay = Gtk.Overlay(child=self.art, overflow=Gtk.Overflow.HIDDEN)
        overlay.add_overlay(self.placeholder)
        overlay.add_css_class("card")
        # Clamp = maximum width (and centering) of the art. _fit_art() keeps it as large as the window allows.
        self.art_clamp = Adw.Clamp(maximum_size=4096, tightening_threshold=4096, child=overlay)
        self.card.append(self.art_clamp)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label="—", xalign=0.5, ellipsize=3)
        self.title.add_css_class("title-3")
        self.artist = Gtk.Label(label="", xalign=0.5, ellipsize=3)
        self.album = Gtk.Label(label="", xalign=0.5, ellipsize=3)
        self.album.add_css_class("dim-label")
        self.labels = (self.title, self.artist, self.album)
        for label in self.labels:
            info.append(label)

        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.scale.set_draw_value(False)
        self.scale.connect("change-value", self._on_seek)  # only fires on user interaction
        info.append(self.scale)

        times = Gtk.Box()
        self.pos_label = Gtk.Label(label="0:00", xalign=0, hexpand=True)
        self.len_label = Gtk.Label(label="0:00", xalign=1)
        for label in (self.pos_label, self.len_label):
            label.add_css_class("dim-label")
            label.add_css_class("caption")
            times.append(label)
        info.append(times)

        controls = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_btn.add_css_class("suggested-action")
        for button, action in ((Gtk.Button(icon_name="media-skip-backward-symbolic"), "previous"),
                               (self.play_btn, "play_pause"),
                               (Gtk.Button(icon_name="media-skip-forward-symbolic"), "next")):
            button.add_css_class("circular")
            button.connect("clicked", lambda _b, a=action: self._control(a))
            controls.append(button)
        info.append(controls)
        self.card.append(info)

        self._art_side = 0
        self.compact = False                 # True in the single-column layout (set by the window)
        self.available_height = lambda: 0    # height the player may use; set by the window
        self.add_tick_callback(self._on_tick)
        GLib.timeout_add(500, self._tick)

    def _on_tick(self, _widget, _clock) -> bool:
        self._fit_art()
        self._keep_art_square()
        return GLib.SOURCE_CONTINUE

    def _fit_art(self) -> None:
        """Make the cover as big as the window allows, so the whole player is visible without scrolling.

        Width: the Clamp never exceeds the column. Height: whatever is left once everything that is not
        the cover (margins, title, bar, buttons) is subtracted from the height available to the player.
        Narrow layout: fixed small cover beside the text.
        """
        if self.compact:
            side = ART_COMPACT
        else:
            other = self.get_height() - self.art_clamp.get_height()  # card margins + spacing + info block
            available = self.available_height()
            if available <= 0 or other <= 0:
                return  # not laid out yet
            side = max(ART_MIN, available - other)
        if side != self.art_clamp.get_maximum_size():
            self.art_clamp.set_maximum_size(side)
            self.art_clamp.set_tightening_threshold(side)

    def _keep_art_square(self) -> None:
        """Height-for-width does not survive the stack of boxes above us (the cover came out 480x384),
        so make the square explicit: whenever the art gets a new width, its minimum height follows it."""
        side = self.art_box.get_width()
        if side > 0 and side != self._art_side:
            self._art_side = side
            self.art_box.set_size_request(-1, side)

    # --- binding to the player ---
    def bind(self, player: MprisPlayer | None) -> None:
        if self.player:
            self.player.disconnect()
        self.player = player
        if player and player.connect():
            player.changed.connect(self._on_changed)
            self._show(player.track, player.status)
        else:
            self._show(None, PlaybackStatus.STOPPED)

    def _control(self, action: str) -> None:
        if self.player:
            getattr(self.player, action)()

    def _on_changed(self, status: PlaybackStatus, track: Track | None) -> None:
        self._show(track, status)

    def _on_seek(self, _scale, _scroll, value: float) -> bool:
        if self.player:
            self.player.seek_to(value)
        return False

    # --- display ---
    def _show(self, track: Track | None, status: PlaybackStatus) -> None:
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if status is PlaybackStatus.PLAYING
                                    else "media-playback-start-symbolic")
        self.title.set_label(track.title if track and track.title else "—")
        self.artist.set_label(", ".join(track.artists) if track else "")
        self.album.set_label(track.album if track else "")
        length = track.length if track else 0
        self.scale.set_range(0, max(length, 1))
        self.len_label.set_label(format_time(length))
        self._load_art(track.art_url if track else None)
        self._tick()

    def _tick(self) -> bool:
        if self.player and self.player.status is not PlaybackStatus.STOPPED:
            position = self.player.position()
            if position is not None:
                self.scale.set_value(position)
                self.pos_label.set_label(format_time(position))
        return True

    def _load_art(self, url: str | None) -> None:
        if url == self._art_url:
            return
        self._art_url = url
        self._show_art(None)  # drop the previous track's cover at once; the placeholder shows meanwhile
        if not url:
            return

        def work() -> None:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(fetch_image(url)))
            except (ArtError, GLib.Error):  # refused URL, failed download or undecodable image
                texture = None
            GLib.idle_add(self._set_art, url, texture)

        threading.Thread(target=work, daemon=True).start()

    def _set_art(self, url: str, texture) -> bool:
        if url == self._art_url:  # ignore responses for tracks that are already gone
            self._show_art(texture)
        return False

    def _show_art(self, texture) -> None:
        self.cover.set_texture(texture)
        self.placeholder.set_visible(texture is None)
