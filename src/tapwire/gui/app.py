"""GUI entry point (GTK4 + libadwaita)."""
from __future__ import annotations

import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from .window import Window  # noqa: E402


class App(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="dev.tapwire.gui")

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        # Ctrl+C / SIGTERM in the terminal: close the window like the user would (this stops the session and
        # discards the partial recording) and leave. GTK does not do this by itself.
        for number in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, self._on_signal)

    def _on_signal(self) -> bool:
        for window in self.get_windows():
            window.close()
        self.quit()
        return GLib.SOURCE_REMOVE

    def do_activate(self) -> None:
        (self.props.active_window or Window(self)).present()


def main() -> int:
    # Only the program name: arguments such as the `gui` in `tapwire gui` are ours, and GApplication would
    # take every extra argument for a file to open ("This application can not open files").
    return App().run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
