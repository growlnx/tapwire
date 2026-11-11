import unittest

from tapwire.models import PlaybackStatus, Track
from tapwire.naming import safe_filename, unique_path


class TrackTests(unittest.TestCase):
    META = {"mpris:trackid": "/org/example/track/abc", "mpris:length": 199_880_000,
            "xesam:title": "Sinal", "xesam:artist": ["A", "B"], "xesam:album": "Alb",
            "xesam:trackNumber": 3, "mpris:artUrl": "https://x/y.jpg"}

    def test_from_mpris(self):
        t = Track.from_mpris(self.META)
        self.assertEqual(t.label, "A, B - Sinal")
        self.assertAlmostEqual(t.length, 199.88)
        self.assertEqual(t.tags()["track"], "3")
        self.assertNotIn("comment", t.tags())  # empty url is left out

    def test_no_track_id(self):
        self.assertIsNone(Track.from_mpris({}))

    def test_status_parse(self):
        self.assertIs(PlaybackStatus.parse("Playing"), PlaybackStatus.PLAYING)
        self.assertIs(PlaybackStatus.parse("???"), PlaybackStatus.STOPPED)


class NamingTests(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(safe_filename('AC/DC: "Back"?'), "ACDC Back")

    def test_unique_path(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.flac"
            self.assertEqual(unique_path(p), p)
            p.write_bytes(b"")
            self.assertEqual(unique_path(p).name, "a (2).flac")


if __name__ == "__main__":
    unittest.main()
