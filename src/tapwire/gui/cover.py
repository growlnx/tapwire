"""A square, always-same-size cover area (with or without a cover image)."""
from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GObject, Graphene, Gtk  # noqa: E402


class CoverPaintable(GObject.Object, Gdk.Paintable):
    """Paintable with a fixed 1:1 aspect ratio.

    A Gtk.Picture derives its height from the paintable's aspect ratio, so using this one makes the
    cover area a square that fills the available width no matter what: no cover (flat placeholder
    background), a square cover, or a 16:9 video thumbnail (cropped to fill, like CSS `object-fit: cover`).
    """

    def __init__(self) -> None:
        super().__init__()
        self._texture: Gdk.Texture | None = None

    def set_texture(self, texture: Gdk.Texture | None) -> None:
        self._texture = texture
        self.invalidate_contents()

    # --- Gdk.Paintable vfuncs ---
    def do_get_intrinsic_aspect_ratio(self) -> float:
        return 1.0

    def do_get_intrinsic_width(self) -> int:
        return 0  # no natural size: the layout decides (otherwise a 640 px cover would ask for 640 px)

    def do_get_intrinsic_height(self) -> int:
        return 0

    def do_get_flags(self) -> Gdk.PaintableFlags:
        return Gdk.PaintableFlags(0)

    def do_snapshot(self, snapshot: Gtk.Snapshot, width: float, height: float) -> None:
        area = Graphene.Rect().init(0, 0, width, height)
        texture = self._texture
        if texture is None:
            snapshot.append_color(Gdk.RGBA(red=0.5, green=0.5, blue=0.5, alpha=0.15), area)
            return
        scale = max(width / texture.get_width(), height / texture.get_height())  # "cover": fill, crop the excess
        draw_w, draw_h = texture.get_width() * scale, texture.get_height() * scale
        snapshot.push_clip(area)
        snapshot.translate(Graphene.Point().init((width - draw_w) / 2, (height - draw_h) / 2))
        texture.snapshot(snapshot, draw_w, draw_h)
        snapshot.pop()
