"""file:// covers (Chrome publishes /tmp/.com.google.Chrome.XXXXXX): strict path and file validation."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tapwire import art
from tapwire.art import ArtError, fetch_image, local_cover_bytes, parse_local_cover_url

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


class PathShapeTests(unittest.TestCase):
    def test_accepts_chrome_style_paths(self):
        for url in ("file:///tmp/.com.google.Chrome.qphX7E", "file:///tmp/cover.jpg", "file://localhost/tmp/a-b_c.1",
                    "FILE:///tmp/x", "file:///tmp/%2Ecom.google.Chrome.abc"):  # %2E = "."
            with self.subTest(url=url):
                self.assertTrue(parse_local_cover_url(url).startswith("/tmp/"))

    def test_rejects_traversal_in_every_spelling(self):
        for url in ("file:///tmp/../etc/passwd", "file:///tmp/%2e%2e/etc/passwd", "file:///tmp/%2E%2E%2Fetc%2Fpasswd",
                    "file:///tmp/a/../../etc/passwd", "file:///tmp/..%2f..%2fetc/passwd", "file:///tmp/./x",
                    "file:///tmp//x", "file:////tmp/x", "file:///tmp/x/..", "file:///tmp/x/.", "file:///tmp/..",
                    "file:///tmp/%252e%252e/x", "file:///tmp/\\..\\x"):
            with self.subTest(url=url), self.assertRaises(ArtError):
                parse_local_cover_url(url)

    def test_rejects_anything_not_directly_in_tmp(self):
        for url in ("file:///etc/passwd", "file:///home/user/.ssh/id_rsa", "file:///tmp/sub/x", "file:///tmp/a%2Fb",
                    "file:///tmpx/x", "file:///TMP/x", "file:///var/tmp/x", "file:///tmp", "file:///tmp/", "file:///",
                    "file:///proc/self/environ", "file:///dev/zero", "file:tmp/x", "file:x", "file://"):
            with self.subTest(url=url), self.assertRaises(ArtError):
                parse_local_cover_url(url)

    def test_rejects_other_hosts_and_url_decorations(self):
        for url in ("file://evil.com/tmp/x", "file://10.0.0.1/tmp/x", "file:///tmp/x?q=1", "file:///tmp/x#f",
                    "file:///tmp/x;p=1", "file://user@localhost/tmp/x"):
            with self.subTest(url=url), self.assertRaises(ArtError):
                parse_local_cover_url(url)

    def test_rejects_odd_names(self):
        for url in ("file:///tmp/a%00b", "file:///tmp/a%20b", "file:///tmp/a%0Ab", "file:///tmp/a;b", "file:///tmp/$x",
                    "file:///tmp/" + "a" * 101, "file:///tmp/%C3%A9", "file:///tmp/a%5Cb"):
            with self.subTest(url=url), self.assertRaises(ArtError):
                parse_local_cover_url(url)


class FileChecksTests(unittest.TestCase):
    """Real files in /tmp (the only place the URL check allows)."""

    def setUp(self):
        self._made = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for p in self._made:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except IsADirectoryError:
                p.rmdir()

    def tmpfile(self, data: bytes) -> Path:
        fd, name = tempfile.mkstemp(prefix=".tapwire-test-", dir="/tmp")
        os.write(fd, data)
        os.close(fd)
        self._made.append(Path(name))
        return Path(name)

    def url(self, path: Path) -> str:
        return "file://" + str(path)

    def test_valid_jpeg_and_png_are_read(self):
        self.assertEqual(fetch_image(self.url(self.tmpfile(JPEG))), JPEG)
        self.assertEqual(fetch_image(self.url(self.tmpfile(PNG))), PNG)

    def test_non_images_refused_even_with_innocent_names(self):
        for body in (b"ffconcat version 1.0\nfile x\n", b"#EXTM3U\n", b"secret text", b"", b"GIF89a....", b"\x00" + JPEG):
            with self.subTest(body=body[:10]), self.assertRaises(ArtError):
                fetch_image(self.url(self.tmpfile(body)))

    def test_symlink_refused_even_to_a_valid_image(self):
        target = self.tmpfile(JPEG)
        link = Path(tempfile.mktemp(prefix=".tapwire-link-", dir="/tmp"))
        link.symlink_to(target)
        self._made.append(link)
        with self.assertRaises(ArtError):
            fetch_image(self.url(link))

    def test_fifo_directory_and_missing_refused_without_hanging(self):
        fifo = Path(tempfile.mktemp(prefix=".tapwire-fifo-", dir="/tmp"))
        os.mkfifo(fifo)
        self._made.append(fifo)
        directory = Path(tempfile.mkdtemp(prefix=".tapwire-dir-", dir="/tmp"))
        self._made.append(directory)
        for path in (fifo, directory, Path("/tmp/.tapwire-does-not-exist")):
            with self.subTest(path=path.name), self.assertRaises(ArtError):
                fetch_image(self.url(path))

    def test_file_owned_by_someone_else_refused(self):
        path = self.tmpfile(JPEG)
        with mock.patch.object(art.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(ArtError):
                fetch_image(self.url(path))

    def test_size_cap(self):
        path = self.tmpfile(JPEG + b"x" * 200)
        with mock.patch.object(art, "MAX_BYTES", 100):
            with self.assertRaises(ArtError):
                fetch_image(self.url(path))

    def test_local_cover_bytes_is_safe_on_its_own(self):
        """Even if a path slipped past the URL check, the file checks still hold."""
        with self.assertRaises(ArtError):
            local_cover_bytes("/etc/passwd")      # a regular file we may read, but not an image (and not ours)
        with self.assertRaises(ArtError):
            local_cover_bytes("/dev/zero")        # not a regular file


if __name__ == "__main__":
    unittest.main()
