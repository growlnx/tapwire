"""Command-injection / untrusted-player hardening."""
import shutil
import unittest.mock
import subprocess
import tempfile
import unittest
from pathlib import Path

from gi.repository import GLib

from tapwire.encoder import FlacEncoder
from tapwire.models import MAX_TAG_CHARS, Track, clean_tag
from tapwire.pipewire import OutPort
from tapwire.settings import Settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class MetadataTypeTests(unittest.TestCase):
    def test_wrong_types_do_not_crash_or_leak(self):
        t = Track.from_mpris({"mpris:trackid": "/x", "xesam:title": 5, "xesam:artist": [1, "ok", None],
                              "xesam:album": ["a"], "xesam:trackNumber": "3", "mpris:length": "9",
                              "xesam:url": 7, "mpris:artUrl": {"a": 1}})
        self.assertEqual((t.title, t.artists, t.album, t.number, t.length, t.url, t.art_url),
                         ("", ("ok",), "", None, 0.0, "", ""))

    def test_bare_string_artist_is_not_split_into_characters(self):
        self.assertEqual(Track.from_mpris({"mpris:trackid": "/x", "xesam:artist": "abc"}).artists, ())

    def test_trackid_must_be_a_dbus_object_path(self):
        for bad in ("not a path", "/a b", "/../x", "", "x/y", "/a//b", "/a/", 5, None, "/x\n", "/a;rm -rf"):
            with self.subTest(trackid=bad):
                self.assertIsNone(Track.from_mpris({"mpris:trackid": bad}))
        self.assertIsNotNone(Track.from_mpris({"mpris:trackid": "/org/example/track/3ilX9A7SMDo2e"}))

    def test_negative_length_and_number_ignored(self):
        t = Track.from_mpris({"mpris:trackid": "/x", "mpris:length": -5, "xesam:trackNumber": -1})
        self.assertEqual((t.length, t.number), (0.0, None))


class TagTests(unittest.TestCase):
    def test_tags_are_cleaned_and_capped(self):
        t = Track("/x", title="a\x1b[31m\nb‮", artists=("x" * 5000,), url="https://e/\x00")
        tags = t.tags()
        self.assertEqual(tags["title"], "a[31m b")
        self.assertEqual(len(tags["artist"]), MAX_TAG_CHARS)
        self.assertEqual(tags["comment"], "https://e/")

    def test_all_scripts_preserved(self):
        self.assertEqual(clean_tag("Кино — 宇多田 ‘q’"), "Кино — 宇多田 ‘q’")


class PipeWireArgumentTests(unittest.TestCase):
    def test_port_id_and_channel_validated(self):
        OutPort(12, "FL")
        for pid, ch in ((-5, "FL"), ("12", "FL"), (True, "FL"), (1.5, "FL"), (3, "FL --help"), (3, "-x"),
                        (3, ""), (3, "a" * 40), (3, "FL\n"), (3, None), (3, "../x")):
            with self.subTest(pid=pid, ch=ch), self.assertRaises(ValueError):
                OutPort(pid, ch)


class SettingsTests(unittest.TestCase):
    def test_out_dir_is_absolute(self):
        for raw in ("music", "-rf", "concat:x", "http://x", "~/m"):
            self.assertTrue(Settings(out_dir=raw).out_dir.is_absolute(), raw)


def run_encoder(part: Path, final: Path, art: Path | None) -> bool:
    """Runs FlacEncoder.encode and spins a GLib main loop until it reports back."""
    loop, result = GLib.MainLoop(), []
    FlacEncoder().encode(part, final, Track("/x", title="t", artists=("a",)), art,
                         lambda ok: (result.append(ok), loop.quit()))
    GLib.timeout_add(20000, loop.quit)
    loop.run()
    return bool(result and result[0])


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class EncoderHardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)
        self.part = self.d / "t.part.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", str(self.part)], check=True)
        # a real, valid JPEG
        self.jpg = self.d / ".cover-ok.jpg"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=32x32:d=1",
                        "-frames:v", "1", str(self.jpg)], check=True)

    def streams(self, path: Path) -> str:
        return subprocess.run(["ffprobe", "-loglevel", "error", "-show_entries", "stream=codec_type",
                               "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.split()

    def test_valid_cover_is_embedded(self):
        final = self.d / "ok.flac"
        self.assertTrue(run_encoder(self.part, final, self.jpg))
        self.assertEqual(sorted(self.streams(final)), ["audio", "video"])
        self.assertFalse(self.jpg.exists())  # temp cover cleaned up

    def test_hostile_cover_cannot_make_ffmpeg_open_other_files(self):
        secret = self.d / "secret.ts"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=1:d=1",
                        "-c:v", "mpeg2video", "-f", "mpegts", str(secret)], check=True)
        for name, body in ((".cover-1.jpg", b"ffconcat version 1.0\nfile secret.ts\n"),
                           (".cover-2.png", f"#EXTM3U\n#EXTINF:1,\nfile://{secret}\n#EXT-X-ENDLIST\n".encode())):
            with self.subTest(cover=name):
                art = self.d / name
                art.write_bytes(body)  # (the downloader would already have refused these: no magic bytes)
                final = self.d / (name + ".flac")
                ok = run_encoder(self.part, final, art)
                # either it fails, or it produces no video stream sourced from the other file
                self.assertTrue(not ok or "video" not in self.streams(final))

    def test_relative_and_odd_paths_are_made_absolute(self):
        weird = self.d / "-weird"
        weird.mkdir()
        part = weird / "-p.part.wav"
        part.write_bytes(self.part.read_bytes())
        self.assertTrue(run_encoder(part, weird / "-out.flac", None))


if __name__ == "__main__":
    unittest.main()


class DesktopEntryTests(unittest.TestCase):
    def test_only_plain_file_name_stems_are_accepted(self):
        from tapwire.models import valid_desktop_id
        for ok in ("player", "org.mozilla.firefox", "google-chrome", "a", "My_App-2"):
            self.assertEqual(valid_desktop_id(ok), ok)
        for bad in ("../../tmp/evil", "/etc/evil", "a/b", "..", ".hidden", "", "a b", "a\n", "x" * 200, "-rf",
                    5, None, ["player"]):
            with self.subTest(entry=bad):
                self.assertEqual(valid_desktop_id(bad), "")


class IconMatchingTests(unittest.TestCase):
    """Matches a player's Identity to an installed app (Gio mocked: no display or real apps needed)."""

    def app(self, app_id, name, icon="x"):
        app = unittest.mock.Mock()
        app.get_id.return_value, app.get_name.return_value = app_id, name
        app.get_icon.return_value, app.should_show.return_value = icon, True
        return app

    def find(self, identity, apps, entry=""):
        from tapwire.gui import icons
        from tapwire.models import PlaybackStatus, PlayerInfo
        info = PlayerInfo("n", identity, PlaybackStatus.STOPPED, desktop_entry=entry)
        with unittest.mock.patch.object(icons.Gio.AppInfo, "get_all", return_value=apps), \
                unittest.mock.patch.object(icons.Gio.DesktopAppInfo, "new", return_value=None):
            return icons._find_app(info)

    def test_exact_stem_beats_word_match(self):
        code = self.app("code.desktop", "Visual Studio Code")
        handler = self.app("claude-code-url-handler.desktop", "Claude Code URL Handler")
        self.assertIs(self.find("Code", [handler, code]), code)

    def test_suffix_and_word_match(self):
        chrome = self.app("google-chrome.desktop", "Google Chrome")
        self.assertIs(self.find("Chrome", [self.app("other.desktop", "Other"), chrome]), chrome)

    def test_no_match_and_hidden_apps(self):
        self.assertIsNone(self.find("Nothing", [self.app("code.desktop", "Visual Studio Code")]))
        hidden = self.app("player.desktop", "Player")
        hidden.should_show.return_value = False
        self.assertIsNone(self.find("Player", [hidden]))

    def test_app_without_icon_is_skipped(self):
        self.assertIsNone(self.find("Code", [self.app("code.desktop", "Code", icon=None)]))


if __name__ == "__main__":
    unittest.main()
