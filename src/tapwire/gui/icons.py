"""App icons for MPRIS players."""
from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio  # noqa: E402

from ..models import PlayerInfo  # noqa: E402

FALLBACK = "audio-x-generic-symbolic"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _find_app(info: PlayerInfo) -> Gio.AppInfo | None:
    """The installed application a player belongs to.

    1. its own DesktopEntry (validated: a plain file-name stem, never a path);
    2. otherwise the installed app whose id or name matches the player's Identity
       ("Code" -> code.desktop, "Chrome" -> google-chrome.desktop). Browsers/Electron apps
       usually do not publish DesktopEntry.
    Only apps that are really installed (and visible) are considered.
    """
    if info.desktop_entry:
        app = Gio.DesktopAppInfo.new(info.desktop_entry + ".desktop")
        if app:
            return app
    ident = _normalize(info.identity)
    if not ident:
        return None
    apps = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_icon() is not None]
    tiers = (
        lambda stem, name: stem == ident,                                  # code.desktop        <- "Code"
        lambda stem, name: name == ident,                                  # "Rhythmbox"         <- "Rhythmbox"
        lambda stem, name: stem.endswith(" " + ident),                     # google-chrome       <- "Chrome"
        lambda stem, name: ident in name.split(),                          # "Google Chrome"     <- "Chrome"
    )
    for matches in tiers:
        for app in apps:
            stem = _normalize((app.get_id() or "").removesuffix(".desktop"))
            if matches(stem, _normalize(app.get_name() or "")):
                return app
    return None


def icon_for(info: PlayerInfo) -> Gio.Icon | str:
    """A Gio.Icon for the player's app, or a generic icon name if none can be found."""
    app = _find_app(info)
    return (app.get_icon() if app else None) or FALLBACK
