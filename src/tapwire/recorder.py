"""A pw-record process linked to an app's stream."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Callable, Protocol

from gi.repository import GLib

from .i18n import _
from .pipewire import PipeWire
from .tools import ToolNotFound, find_tool


class RecorderLike(Protocol):
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...


class Recorder:
    """Creates a recorder WITHOUT autoconnect and links the app's output ports to it with pw-link.

    (`pw-record --target <stream>` links nothing when the target is an app stream: it records silence.)
    Pausing = removing the links; with no input pw-record writes nothing.
    """

    LINK_RETRIES = 20
    LINK_INTERVAL_MS = 150

    def __init__(self, app: str, part_path: Path, pipewire: PipeWire | None = None,
                 on_warning: Callable[[str], None] | None = None) -> None:
        self.app = app
        self.part_path = part_path
        self._pw = pipewire or PipeWire()
        self._on_warning = on_warning or (lambda _msg: None)
        self._name = f"tapwire-{os.getpid()}-{id(self)}"
        self._proc: subprocess.Popen | None = None
        self._generation = 0  # invalidates stale link timers

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [find_tool("pw-record"), "-P", f"{{ node.name={self._name} node.autoconnect=false }}", str(self.part_path)],
            stdout=subprocess.DEVNULL)
        self._schedule_link()

    def pause(self) -> None:
        self._generation += 1
        for port in self._pw.app_ports(self.app):
            self._pw.unlink(port, self._name)

    def resume(self) -> None:
        self._schedule_link()

    def stop(self) -> None:
        """Finalizes the file (SIGINT makes pw-record close the WAV header)."""
        self._generation += 1
        if not self._proc:
            return
        self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def _schedule_link(self) -> None:
        self._generation += 1
        GLib.timeout_add(100, self._link_attempt, self._generation, 0)

    def _link_attempt(self, generation: int, attempt: int) -> bool:
        # the app stream may take a while to appear / change id between tracks: retry a few times
        if generation != self._generation or not self._proc:
            return False
        try:
            linked = sum(self._pw.link(port, self._name) for port in self._pw.app_ports(self.app))
        except ToolNotFound as exc:
            self._on_warning(str(exc))
            return False
        if linked:
            return False
        if attempt >= self.LINK_RETRIES:
            self._on_warning(_("no ports of '{app}' linked to the recorder").format(app=self.app))
            return False
        GLib.timeout_add(self.LINK_INTERVAL_MS, self._link_attempt, generation, attempt + 1)
        return False
