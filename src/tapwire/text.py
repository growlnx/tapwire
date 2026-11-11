"""Text helpers for showing things: untrusted text made safe, paths made short."""
from __future__ import annotations

import unicodedata
from pathlib import Path

_INVISIBLE = {"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"}


def printable(text: str) -> str:
    """Shows control/invisible characters as visible \\uXXXX escapes.

    Stops terminal escape sequences (ESC[...), line breaks and bidi overrides in a title from
    acting on the terminal or the UI. Normal text in any script is left untouched.
    """
    return "".join(f"\\u{ord(ch):04x}" if unicodedata.category(ch) in _INVISIBLE else ch for ch in text)


def _home_dirs() -> list[Path]:
    """The home directory as written and, if it is a symlink, where it really points."""
    try:
        home = Path.home()
    except RuntimeError:  # no HOME and no user entry
        return []
    resolved = home.resolve()
    return [home] if resolved == home else [home, resolved]


def display_path(path) -> str:
    """For showing only: a path inside the user's home directory starts with "~" instead of the full home.

    `/home/ana/Music/tapwire` -> `~/Music/tapwire`, the home itself -> `~`. Anything else is unchanged.
    The path is never altered for use, this only builds the label. Matching is by path component, so
    `/home/ana2/x` is not "inside" `/home/ana`.
    """
    path = Path(path)
    for home in _home_dirs():
        try:
            relative = path.relative_to(home)
        except ValueError:
            continue
        return "~" if not relative.parts else "~/" + relative.as_posix()
    return str(path)
