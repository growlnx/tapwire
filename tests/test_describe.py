import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

from tapwire.describe import describe
from tapwire.events import DropReason, Event, EventKind

from .fakes import track

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run_in(language: str, code: str) -> str:
    """Runs code in a fresh interpreter: the language is bound at import time."""
    env = {**os.environ, "LANGUAGE": language, "PYTHONPATH": SRC}
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True).stdout.strip()


CODE = ("from tapwire.describe import describe; from tapwire.events import *; from tapwire.models import Track;"
        "print(describe(Event(EventKind.DROPPED, Track('/t/1', title='T', artists=('A',)), reason=DropReason.SEEKED)))")


class DescribeTests(unittest.TestCase):
    def test_english_source_strings(self):
        event = Event(EventKind.RECORDING, track("A"))
        self.assertEqual(describe(event), "Recording: Artista - A") if os.environ.get("LANGUAGE", "") in ("", "en") \
            else None

    def test_catalogs(self):
        self.assertEqual(run_in("en", CODE), "Discarded (seeked within the track): A - T")
        self.assertEqual(run_in("pt_BR", CODE), "Descartada (pulo dentro da faixa): A - T")

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(run_in("xx", CODE), "Discarded (seeked within the track): A - T")


if __name__ == "__main__":
    unittest.main()


class PlayerLabelTests(unittest.TestCase):
    def info(self, **kw):
        from tapwire.models import PlaybackStatus, PlayerInfo
        base = dict(name="chromium.instance1", identity="Chrome", status=PlaybackStatus.PAUSED, track=track("A"))
        return PlayerInfo(**{**base, **kw})

    def test_playing_or_paused_shows_identity_and_track(self):
        from tapwire.describe import player_label
        self.assertEqual(player_label(self.info()), "Chrome — Artista - A")

    def test_stopped_shows_identity_and_status(self):
        from tapwire.describe import player_label
        from tapwire.models import PlaybackStatus
        label = player_label(self.info(identity="Code", status=PlaybackStatus.STOPPED, track=None))
        self.assertIn("Code", label)
        self.assertNotIn("chromium", label)  # the technical D-Bus name is not what the user sees

    def test_hostile_and_long_text(self):
        from tapwire.describe import player_label
        label = player_label(self.info(identity="Evil\x1b[2J", track=track("x", title="T" * 500)))
        self.assertNotIn("\x1b", label)
        self.assertLessEqual(len(label), 70)

    def test_translated_status(self):
        from tapwire.models import PlaybackStatus
        code = ("from tapwire.describe import player_label; from tapwire.models import *;"
                "print(player_label(PlayerInfo('x','Code',PlaybackStatus.STOPPED)))")
        self.assertEqual(run_in("en", code), "Code (stopped)")
        self.assertEqual(run_in("pt_BR", code), "Code (parado)")


class CliGuiCommandTests(unittest.TestCase):
    def test_gui_subcommand_opens_the_window(self):
        from tapwire import cli
        with unittest.mock.patch("tapwire.gui.app.main", return_value=0) as gui_main:
            self.assertEqual(cli.main(["gui"]), 0)
        gui_main.assert_called_once_with()

    def test_importing_the_cli_does_not_import_gtk(self):
        code = "import sys, tapwire.cli; print('gi.repository.Gtk' in sys.modules)"
        self.assertEqual(run_in("en", code), "False")


class GuiArgumentsTests(unittest.TestCase):
    def test_cli_arguments_are_not_forwarded_to_gapplication(self):
        """`tapwire gui` leaves "gui" in sys.argv; GApplication would treat it as a file to open and fail."""
        from tapwire.gui import app
        with unittest.mock.patch.object(app, "App") as application, \
                unittest.mock.patch.object(app.sys, "argv", ["tapwire", "gui", "--whatever"]):
            application.return_value.run.return_value = 0
            app.main()
        application.return_value.run.assert_called_once_with(["tapwire"])
