"""File names.

Track titles are untrusted input (a web page can set them through MPRIS), but they are also
real text in many scripts (Cyrillic, CJK, accents), so we sanitize instead of transliterating.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

# Linux limit is 255 bytes per name; leave room for suffixes (".flac", ".part.wav", " (12)", ".tmp").
MAX_NAME_BYTES = 200
ARTIST_BYTES = 80
TITLE_BYTES = MAX_NAME_BYTES - ARTIST_BYTES - len(" - ")


def safe_filename(text: str, max_bytes: int = MAX_NAME_BYTES) -> str:
    """Allow-list sanitizer: keeps only what is text, drops everything else.

    Kept: letters (L*), numbers (N*) and combining marks (M*: accents, Indic vowel signs...) of
    any script; whitespace becomes a single space. Dropped: punctuation, symbols, emoji, controls,
    invisible/bidi characters... so no separators, no dots, no hidden or special names are possible.
    Never empty, never longer than `max_bytes` (cut on a character boundary).
    """
    kept = []
    for ch in unicodedata.normalize("NFC", text):
        if unicodedata.category(ch)[0] in "LNM":
            kept.append(ch)
        elif ch.isspace():
            kept.append(" ")
    name = " ".join("".join(kept).split())
    name = name.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    return name or "unknown"


def track_filename(artist: str, title: str) -> str:
    """'Artist - Title' built from two sanitized parts (the ' - ' is ours, not the track's)."""
    return f"{safe_filename(artist, ARTIST_BYTES)} - {safe_filename(title, TITLE_BYTES)}"


def unique_path(path: Path) -> Path:
    """path, or 'name (2).ext' if it already exists."""
    if not path.exists():
        return path
    n = 2
    while (candidate := path.with_name(f"{path.stem} ({n}){path.suffix}")).exists():
        n += 1
    return candidate


def ensure_inside(directory: Path, path: Path) -> None:
    """Raises ValueError unless `path` really lives directly in `directory`.

    resolve() follows symlinks (even dangling ones), so a planted symlink pointing elsewhere is refused.
    """
    if path.resolve().parent != directory.resolve():
        raise ValueError(f"{path} is not directly inside {directory}")
