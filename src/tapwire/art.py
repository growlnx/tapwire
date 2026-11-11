"""Cover art download (mpris:artUrl).

The URL is attacker-influenced (any MPRIS player, e.g. a web page in a browser, can set it), so there
are exactly two accepted shapes.

1) https:// (most players):
- port 443 only, without embedded credentials;
- the host is resolved here and every address must be a public IPv4 address (no loopback,
  private, link-local, CGNAT...), which blocks SSRF to the local network;
- we then connect to that exact IP (pinned), so a second DNS answer cannot swap it (DNS rebinding);
- redirects are not followed (they would need the same checks);
- the response must be an image and is capped at MAX_BYTES;
- the BYTES must really be a JPEG or PNG (magic numbers). The file is later handed to ffmpeg, which
  would otherwise probe it and could be steered into other demuxers (HLS/concat playlists...) that
  read other local files or URLs. The extension of the saved file comes from this check, never from the server.

2) file:// (Chrome/Chromium write the thumbnail to /tmp/.com.google.Chrome.XXXXXX and publish that path):
- the decoded path must be a normalized absolute path (no "..", ".", "//", NUL) whose directory is
  exactly /tmp: no subdirectories, so it can never point at the user's own files (~/.ssh, ~/Pictures...);
- the file name is a plain token ([A-Za-z0-9._-]);
- the file is opened with O_NOFOLLOW (no symlinks) and O_NONBLOCK (a FIFO cannot hang us), and must be a
  regular file owned by the current user (anything else in the shared /tmp is someone else's);
- same size cap and the same JPEG/PNG magic-number check as above.
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import re
import socket
import stat
import ssl
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from .i18n import _

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT = 10.0
LOCAL_COVER_DIR = "/tmp"  # the only directory a file:// cover may live in (directly, no subdirectories)
_LOCAL_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")


_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"))


def image_extension(data: bytes) -> str | None:
    """'jpg' or 'png' if `data` starts with that format's magic number, else None."""
    for magic, extension in _MAGIC:
        if data.startswith(magic):
            return extension
    return None


class ArtError(Exception):
    """The cover URL was refused or the download failed."""


def parse_art_url(url: str) -> tuple[str, str]:
    """Validates the URL and returns (host, path-with-query). Raises ArtError."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ArtError(f"invalid URL: {exc}") from exc
    if parsed.scheme != "https":
        raise ArtError("only https:// URLs (or file:// in /tmp) are allowed")
    if not parsed.hostname:
        raise ArtError("URL has no host")
    if parsed.username is not None or parsed.password is not None:
        raise ArtError("credentials in the URL are not allowed")
    if port not in (None, 443):
        raise ArtError("only port 443 is allowed")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return parsed.hostname, path


def resolve_public_ipv4(host: str) -> str:
    """Resolves `host` over IPv4 and requires ALL its addresses to be public. Returns one."""
    try:
        infos = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ArtError(f"cannot resolve {host!r} to an IPv4 address: {exc}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise ArtError(f"{host!r} has no IPv4 address")
    for address in addresses:
        ip = ipaddress.IPv4Address(address)
        # is_global alone still lets multicast (224/4) through
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ArtError(f"{host!r} resolves to a non-public address ({address})")
    return addresses[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to a pre-validated IP, while TLS/SNI/Host still use the original hostname."""

    def __init__(self, host: str, ip: str, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout)
        self._ip = ip
        self._tls = ssl.create_default_context()  # verifies the certificate against `host`

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)


def parse_local_cover_url(url: str) -> str:
    """Validates a file:// cover URL and returns its path. Raises ArtError.

    Only checks the *shape* (see module docstring); local_cover_bytes() checks the file itself.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ArtError(f"invalid URL: {exc}") from exc
    if parsed.scheme != "file":
        raise ArtError("not a file:// URL")
    if parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment or parsed.params:
        raise ArtError("only plain local file URLs are allowed")
    path = unquote(parsed.path)  # decode once: %2e%2e and %2F become visible to the checks below
    if "\x00" in path or not path.startswith("/"):
        raise ArtError("invalid file path")
    if os.path.normpath(path) != path:  # rejects "..", ".", "//", trailing "/", and "/tmp/x/../y"
        raise ArtError("path is not normalized (traversal?)")
    directory, name = os.path.split(path)
    if directory != LOCAL_COVER_DIR:
        raise ArtError(f"local covers must be directly inside {LOCAL_COVER_DIR}")
    if not _LOCAL_NAME.fullmatch(name) or name in (".", ".."):
        raise ArtError("invalid file name")
    return path


def local_cover_bytes(path: str) -> bytes:
    """Reads an already validated path, refusing anything but a small regular file of ours."""
    try:
        # O_NOFOLLOW: the last component must not be a symlink. O_NONBLOCK: opening a FIFO must not hang.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise ArtError(f"cannot open cover: {exc.strerror or exc}") from exc
    try:
        info = os.fstat(fd)  # on the open descriptor: no check/use race with the path
        if not stat.S_ISREG(info.st_mode):
            raise ArtError("cover is not a regular file")
        if info.st_uid != os.getuid():
            raise ArtError("cover is not owned by the current user")
        if info.st_size > MAX_BYTES:
            raise ArtError("image is too large")
        with os.fdopen(fd, "rb", closefd=False) as handle:  # only wrapped once known to be a regular file
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ArtError(f"cannot read cover: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)  # on every path, including the refusals above
    if len(data) > MAX_BYTES:
        raise ArtError("image is too large")
    if image_extension(data) is None:
        raise ArtError("not a JPEG or PNG image")
    return data


def fetch_image(url: str, timeout: float = TIMEOUT) -> bytes:
    """Gets the image at `url` (https:// download or restricted file://). Raises ArtError."""
    try:
        scheme = urlparse(url).scheme
    except ValueError as exc:
        raise ArtError(f"invalid URL: {exc}") from exc
    if scheme == "file":
        return local_cover_bytes(parse_local_cover_url(url))
    host, path = parse_art_url(url)
    ip = resolve_public_ipv4(host)
    conn = _PinnedHTTPSConnection(host, ip, timeout)
    try:
        conn.request("GET", path, headers={"User-Agent": "tapwire", "Accept": "image/*"})
        response = conn.getresponse()
        if response.status != 200:  # includes 3xx: redirects are not followed
            raise ArtError(f"unexpected HTTP status {response.status}")
        if not response.getheader("Content-Type", "").lower().startswith("image/"):
            raise ArtError("response is not an image")
        length = response.getheader("Content-Length")
        if length and length.isdigit() and int(length) > MAX_BYTES:
            raise ArtError("image is too large")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ArtError("image is too large")
        if image_extension(data) is None:
            raise ArtError("not a JPEG or PNG image")
        return data
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise ArtError(str(exc)) from exc
    finally:
        conn.close()


class ArtFetcher:
    """Downloads the cover in a thread, without blocking the main loop."""

    def __init__(self, on_error: Callable[[str], None] | None = None) -> None:
        self._on_error = on_error

    def fetch(self, url: str, base: Path, on_done: Callable[[Path], None]) -> None:
        """Saves the cover as `<base>.<jpg|png>` and calls on_done(path) (from the worker thread)."""
        def work() -> None:
            try:
                data = fetch_image(url)
                path = base.with_name(f"{base.name}.{image_extension(data)}")
                path.write_bytes(data)
            except (ArtError, OSError) as exc:  # missing cover art is not fatal
                if self._on_error:
                    self._on_error(_("no cover art: {error}").format(error=exc))
                return
            on_done(path)

        threading.Thread(target=work, daemon=True).start()
