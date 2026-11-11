"""Converts the raw WAV into FLAC with tags and cover art, via ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from .models import Track
from .tools import find_tool


class FlacEncoder:
    POLL_MS = 200

    def encode(self, part: Path, final: Path, track: Track, art: Path | None,
               on_done: Callable[[bool], None]) -> None:
        """Runs in the background; on_done(ok) is called on the main loop.

        On success the WAV and the temporary cover are deleted; on failure the raw WAV is kept.
        """
        has_art = bool(art) and art.exists() and art.stat().st_size > 0
        tmp = final.with_name(final.name + ".tmp")
        # Hardening (the cover is untrusted data from the network):
        # - force each input's format (-f): ffmpeg must not probe content and pick another demuxer
        #   (HLS/concat playlists etc. could make it read other local files or URLs);
        # - -protocol_whitelist file: inputs may only be plain local files;
        # - absolute paths, so a name can never look like an option or a protocol ("http:...").
        # Metadata values go as separate argv items after -metadata: never parsed as options, no shell.
        local = ["-protocol_whitelist", "file"]
        cmd = [find_tool("ffmpeg"), "-nostdin", "-loglevel", "error", "-y", *local, "-f", "wav", "-i", str(part.resolve())]
        if has_art:
            image_format = "image2"  # the file extension (.jpg/.png) was chosen by us from the magic bytes
            cmd += [*local, "-f", image_format, "-i", str(art.resolve()), "-map", "0:a", "-map", "1:v",
                    "-c:v", "copy", "-disposition:v", "attached_pic", "-metadata:s:v", "title=Cover",
                    "-metadata:s:v", "comment=Cover (front)"]
        for key, value in track.tags().items():
            cmd += ["-metadata", f"{key}={value}"]
        cmd += ["-c:a", "flac", "-f", "flac", str(tmp.resolve())]
        proc = subprocess.Popen(cmd)

        # poll() instead of GLib child_watch: the subprocess module also reaps children and would race with it
        def check() -> bool:
            status = proc.poll()
            if status is None:
                return True
            ok = status == 0
            if ok:
                tmp.replace(final)
                part.unlink(missing_ok=True)
            else:
                tmp.unlink(missing_ok=True)
            if art:
                art.unlink(missing_ok=True)
            on_done(ok)
            return False

        GLib.timeout_add(self.POLL_MS, check)
