"""Access to the PipeWire graph via pw-dump / pw-link."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from .tools import find_tool


@dataclass(frozen=True)
class StreamInfo:
    node_id: int
    app_name: str
    binary: str
    media_name: str


_CHANNEL = re.compile(r"[A-Za-z0-9_]{1,16}")


@dataclass(frozen=True)
class OutPort:
    port_id: int
    channel: str  # FL, FR, ...

    def __post_init__(self) -> None:
        # Both come from properties other PipeWire clients can set, and end up on pw-link's command line:
        # the id must be a plain non-negative integer (a "-5" would parse as an option) and the channel a short token.
        if not (isinstance(self.port_id, int) and not isinstance(self.port_id, bool) and self.port_id >= 0):
            raise ValueError(f"invalid port id: {self.port_id!r}")
        if not isinstance(self.channel, str) or not _CHANNEL.fullmatch(self.channel):
            raise ValueError(f"invalid channel name: {self.channel!r}")


class PipeWire:
    """Thin facade over the PipeWire CLIs (easy to swap for a fake in tests)."""

    def dump(self) -> list[dict]:
        out = subprocess.run([find_tool("pw-dump")], capture_output=True, text=True).stdout
        return json.loads(out or "[]")

    @staticmethod
    def _props(obj: dict) -> dict:
        return (obj.get("info") or {}).get("props") or {}

    def streams(self, objects: list[dict] | None = None) -> list[StreamInfo]:
        """All audio streams currently playing (Stream/Output/Audio)."""
        result = []
        for obj in objects if objects is not None else self.dump():
            p = self._props(obj)
            if p.get("media.class") == "Stream/Output/Audio":
                result.append(StreamInfo(obj["id"], p.get("application.name", ""),
                                         p.get("application.process.binary", ""), p.get("media.name", "")))
        return result

    def app_ports(self, app: str) -> list[OutPort]:
        """Output ports of the streams whose binary/name contains `app` (case-insensitive)."""
        objects = self.dump()
        nodes = {s.node_id for s in self.streams(objects)
                 if app.lower() in (s.binary or s.app_name).lower()}
        ports = []
        for obj in objects:
            p = self._props(obj)
            if (obj.get("type") == "PipeWire:Interface:Port" and p.get("node.id") in nodes
                    and p.get("port.direction") == "out"):
                try:
                    ports.append(OutPort(obj["id"], p.get("audio.channel", "")))
                except ValueError:
                    continue  # malformed/hostile port properties: ignore that port
        return ports

    @staticmethod
    def link(port: OutPort, sink_node_name: str) -> bool:
        r = subprocess.run([find_tool("pw-link"), str(port.port_id), f"{sink_node_name}:input_{port.channel}"],
                           capture_output=True)
        return r.returncode == 0

    @staticmethod
    def unlink(port: OutPort, sink_node_name: str) -> None:
        subprocess.run([find_tool("pw-link"), "-d", str(port.port_id), f"{sink_node_name}:input_{port.channel}"],
                       capture_output=True)
