import tempfile
import unittest
from pathlib import Path

from tapwire.naming import MAX_NAME_BYTES, ensure_inside, safe_filename, track_filename
from tapwire.text import printable


class SafeFilenameTests(unittest.TestCase):
    def test_keeps_letters_and_numbers_of_any_script(self):
        for text in ("Кино Группа крови", "Não Me Deixe", "宇多田ヒカル First Love", "Björk Jóga", "שיר עברי",
                     "أغنية 2024", "हिन्दी गाना", "한국어 노래", "Track 07 ½", "ひらがなー"):
            with self.subTest(text=text):
                self.assertEqual(safe_filename(text), text)

    def test_special_characters_are_dropped(self):
        self.assertEqual(safe_filename('AC/DC: "Back"?'), "ACDC Back")
        self.assertEqual(safe_filename("a-b_c.d,e!f@g#h$i%j&k(l)m[n]o{p}q+r=s"), "abcdefghijklmnopqrs")
        self.assertEqual(safe_filename("Rock 🎸 Roll 😀"), "Rock Roll")
        self.assertEqual(safe_filename("「日本語」、。！"), "日本語")

    def test_path_traversal_and_separators(self):
        for text in ("../../etc/passwd", "..\\..\\windows", "/abs/path", "a/b", "C:\\boot"):
            name = safe_filename(text)
            self.assertTrue(all(c.isalnum() or c == " " for c in name), name)
            self.assertEqual(Path(name).name, name)

    def test_dot_and_hidden_names_cannot_happen(self):
        for text in (".", "..", "...", ".hidden", " . ", ". ."):
            self.assertNotIn(".", safe_filename(text))
        self.assertEqual(safe_filename(".."), "unknown")
        self.assertEqual(safe_filename(".hidden"), "hidden")

    def test_control_and_invisible_characters_dropped(self):
        self.assertEqual(safe_filename("A\x1b[31mred"), "A31mred")
        self.assertEqual(safe_filename("a\nb\r\tc\x00d\x7fe"), "a b cde")
        self.assertEqual(safe_filename("a\u2028b\u2029c"), "a b c")
        self.assertEqual(safe_filename("a\u202egnp.flac"), "agnpflac")   # bidi override
        self.assertEqual(safe_filename("a\u200bb\ufeffc"), "abc")         # zero-width, BOM

    def test_whitespace_collapsed(self):
        self.assertEqual(safe_filename("  a   b \u00a0 c  "), "a b c")

    def test_length_limit_cuts_on_character_boundary(self):
        for text in ("a" * 500, "я" * 500, "宇" * 500):
            name = safe_filename(text)
            self.assertLessEqual(len(name.encode()), MAX_NAME_BYTES)
            self.assertGreater(len(name), 0)
            name.encode("utf-8")
        self.assertLessEqual(len(safe_filename("宇" * 500, 10).encode()), 10)

    def test_never_empty(self):
        for text in ("", "   ", "\x00\x00", "\u202e", "!!!", "🎸"):
            self.assertEqual(safe_filename(text), "unknown")

    def test_normalizes_unicode(self):
        self.assertEqual(safe_filename("e\u0301"), "\u00e9")  # NFC

    def test_track_filename_keeps_our_separator_only(self):
        self.assertEqual(track_filename("Кино", "Группа крови"), "Кино - Группа крови")
        self.assertEqual(track_filename("AC/DC", "T.N.T."), "ACDC - TNT")
        self.assertEqual(track_filename("", "x"), "unknown - x")
        name = track_filename("я" * 300, "宇" * 300)
        self.assertLessEqual(len(name.encode()), MAX_NAME_BYTES)
        self.assertIn(" - ", name)


class EnsureInsideTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.out = self.root / "out"
        self.out.mkdir()

    def test_regular_file_ok(self):
        ensure_inside(self.out, self.out / "a.flac")

    def test_parent_escape_refused(self):
        with self.assertRaises(ValueError):
            ensure_inside(self.out, self.out / ".." / "a.flac")

    def test_dangling_symlink_refused(self):
        (self.out / "a.flac").symlink_to(self.root / "elsewhere.flac")  # target does not exist yet
        with self.assertRaises(ValueError):
            ensure_inside(self.out, self.out / "a.flac")

    def test_out_dir_symlink_itself_is_fine(self):
        link = self.root / "link"
        link.symlink_to(self.out)
        ensure_inside(link, link / "a.flac")


class PrintableTests(unittest.TestCase):
    def test_escape_sequences_are_made_visible(self):
        self.assertEqual(printable("A\x1b[2Jb"), "A\\u001b[2Jb")
        self.assertEqual(printable("x\ny"), "x\\u000ay")
        self.assertEqual(printable("a\u202eb"), "a\\u202eb")

    def test_normal_text_untouched(self):
        for text in ("Кино", "宇多田", "A & B <i>", "Não"):
            self.assertEqual(printable(text), text)


if __name__ == "__main__":
    unittest.main()


class DisplayPathTests(unittest.TestCase):
    """display_path() only shortens the label; it must be exact about what is "inside the home"."""

    def shown(self, path, home="/home/ana"):
        from unittest import mock
        from tapwire.text import display_path
        with mock.patch.dict("os.environ", {"HOME": home}):
            return display_path(path)

    def test_inside_home(self):
        self.assertEqual(self.shown("/home/ana/Music/tapwire"), "~/Music/tapwire")
        self.assertEqual(self.shown(Path("/home/ana/Music/tapwire")), "~/Music/tapwire")
        self.assertEqual(self.shown("/home/ana/x"), "~/x")

    def test_home_itself(self):
        self.assertEqual(self.shown("/home/ana"), "~")
        self.assertEqual(self.shown("/home/ana/"), "~")

    def test_outside_home_is_unchanged(self):
        for path in ("/tmp/out", "/", "/home", "/media/usb/music", "/var/home/ana/Music"):
            with self.subTest(path=path):
                self.assertEqual(self.shown(path), path)

    def test_matches_by_component_not_by_text_prefix(self):
        for path in ("/home/ana2/Music", "/home/anaconda", "/home/ana.old/x"):
            with self.subTest(path=path):
                self.assertEqual(self.shown(path), path)

    def test_home_that_is_a_symlink(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            real = Path(d, "var-home-ana"); real.mkdir()
            (real / "Music").mkdir()
            link = Path(d, "home-ana"); link.symlink_to(real)
            # HOME is the symlink; the out_dir has been resolve()d to the real location (as Settings does)
            self.assertEqual(self.shown(real / "Music", home=str(link)), "~/Music")
            self.assertEqual(self.shown(link / "Music", home=str(link)), "~/Music")

    def test_no_home_available(self):
        from unittest import mock
        from tapwire.text import display_path
        with mock.patch("pathlib.Path.home", side_effect=RuntimeError("no home")):
            self.assertEqual(display_path("/home/ana/Music"), "/home/ana/Music")

    def test_the_input_path_is_never_modified(self):
        original = Path("/home/ana/Music/tapwire")
        self.shown(original)
        self.assertEqual(str(original), "/home/ana/Music/tapwire")

    def test_odd_characters_survive(self):
        self.assertEqual(self.shown("/home/ana/Música & Co <x>"), "~/Música & Co <x>")
