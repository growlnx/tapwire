import socket
import unittest
from unittest import mock

from tapwire import art
from tapwire.art import ArtError, parse_art_url, resolve_public_ipv4


def addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


class ParseUrlTests(unittest.TestCase):
    def test_ip_literals_go_through_the_ip_check_not_the_parser(self):
        self.assertEqual(parse_art_url("https://10.0.0.1/a")[0], "10.0.0.1")
        with self.assertRaises(ArtError):
            resolve_public_ipv4("10.0.0.1")  # real resolver, no network needed for a literal


class ResolveTests(unittest.TestCase):
    def resolve(self, *ips):
        with mock.patch("socket.getaddrinfo", return_value=addrinfo(*ips)):
            return resolve_public_ipv4("example.test")

    def test_public_address_ok(self):
        self.assertEqual(self.resolve("151.101.1.1"), "151.101.1.1")

    def test_non_public_addresses_refused(self):
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.5", "192.168.1.1", "169.254.169.254",
                   "100.64.0.1", "0.0.0.0", "224.0.0.1", "239.255.255.250", "240.0.0.1", "255.255.255.255",
                   "192.0.0.1", "198.18.0.1", "192.0.2.1"):
            with self.subTest(ip=ip), self.assertRaises(ArtError):
                self.resolve(ip)

    def test_one_private_among_public_refuses(self):
        with self.assertRaises(ArtError):
            self.resolve("151.101.1.1", "127.0.0.1")  # rebinding-style mixed answer

    def test_no_ipv4_refused(self):
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("no A record")):
            with self.assertRaises(ArtError):
                resolve_public_ipv4("ipv6-only.test")


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"\xff\xd8\xffJPEGDATA"):
        self.status, self._h, self._body = status, headers or {"Content-Type": "image/jpeg"}, body

    def getheader(self, name, default=None):
        return self._h.get(name, default)

    def read(self, n):
        return self._body[:n]


class FetchTests(unittest.TestCase):
    def fetch(self, response):
        conn = mock.Mock()
        conn.getresponse.return_value = response
        with mock.patch.object(art, "resolve_public_ipv4", return_value="151.101.1.1"), \
                mock.patch.object(art, "_PinnedHTTPSConnection", return_value=conn) as ctor:
            data = art.fetch_image("https://example.com/a")
        return data, ctor, conn

    def test_redirect_not_followed(self):
        with self.assertRaises(ArtError):
            self.fetch(FakeResponse(302, {"Location": "https://127.0.0.1/"}))

    def test_non_image_refused(self):
        with self.assertRaises(ArtError):
            self.fetch(FakeResponse(headers={"Content-Type": "text/html"}))

    def test_bytes_must_really_be_jpeg_or_png(self):
        for body in (b"ffconcat version 1.0\nfile secret.ts\n", b"#EXTM3U\n", b"<html>", b"GIF89a", b"", b"\x00\xff\xd8\xff"):
            with self.subTest(body=body[:12]), self.assertRaises(ArtError):
                self.fetch(FakeResponse(body=body))  # claims image/jpeg, is not
        self.assertEqual(self.fetch(FakeResponse(body=b"\x89PNG\r\n\x1a\nrest"))[0][:4], b"\x89PNG")

    def test_extension_comes_from_the_bytes(self):
        self.assertEqual(art.image_extension(b"\xff\xd8\xff\xe0"), "jpg")
        self.assertEqual(art.image_extension(b"\x89PNG\r\n\x1a\n"), "png")
        self.assertIsNone(art.image_extension(b"RIFF....WEBP"))

    def test_size_cap(self):
        with mock.patch.object(art, "MAX_BYTES", 4):
            with self.assertRaises(ArtError):
                self.fetch(FakeResponse(body=b"\xff\xd8\xff45"))
            with self.assertRaises(ArtError):
                self.fetch(FakeResponse(headers={"Content-Type": "image/png", "Content-Length": "999"}))


if __name__ == "__main__":
    unittest.main()
