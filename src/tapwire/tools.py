"""Locating the external programs we run (ffmpeg, pw-record, pw-link, pw-dump).

Passing a bare name like "ffmpeg" to subprocess means "first match in $PATH", and $PATH commonly
starts with user-writable directories (~/.local/bin, ~/.npm-global/bin, ~/.cargo/bin...) where a
package install, another account or a stray download could drop a fake `ffmpeg` that we would then
run with our privileges. So we resolve each tool ourselves, once, to an absolute path:

1. TAPWIRE_<NAME> (e.g. TAPWIRE_FFMPEG=/opt/ffmpeg/bin/ffmpeg): an explicit choice by the user, used as is
   (must be an absolute path to an executable file);
2. otherwise the first $PATH entry that is an ABSOLUTE directory owned by root and not writable by
   group/others, containing a root-owned, not group/other-writable executable. The current directory
   (and "", ".", relative entries) is never searched.
"""
from __future__ import annotations

import os
import stat
from functools import lru_cache

ROOT_UID = 0


class ToolNotFound(RuntimeError):
    pass


def _trusted_dir(path: str) -> bool:
    """Absolute, root-owned, and not writable by group or others."""
    if not os.path.isabs(path):
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == ROOT_UID and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _trusted_executable(path: str) -> bool:
    """The real file behind `path` (symlinks followed) is root-owned, not group/other-writable, executable."""
    real = os.path.realpath(path)
    try:
        st = os.stat(real)
    except OSError:
        return False
    return (stat.S_ISREG(st.st_mode) and os.access(real, os.X_OK) and st.st_uid == ROOT_UID
            and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


@lru_cache(maxsize=None)
def find_tool(name: str) -> str:
    """Absolute path of the trusted executable `name`. Raises ToolNotFound."""
    override = os.environ.get(f"TAPWIRE_{name.upper().replace('-', '_')}")
    if override:
        if os.path.isabs(override) and os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        raise ToolNotFound(f"TAPWIRE_{name.upper().replace('-', '_')}={override!r} is not an absolute path to an executable")
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if _trusted_dir(directory):
            candidate = os.path.join(directory, name)
            if _trusted_executable(candidate):
                return candidate
    raise ToolNotFound(
        f"'{name}' not found in a root-owned, non-writable $PATH directory "
        f"(set TAPWIRE_{name.upper().replace('-', '_')} to its absolute path to use another copy)")
