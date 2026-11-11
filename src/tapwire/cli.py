"""tapwire CLI: `tapwire run`, `tapwire players`, `tapwire streams`."""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from .describe import describe
from .events import Event, EventKind
from .i18n import _
from .text import printable
from .settings import PauseMode, Settings


def _log(event: Event) -> None:
    print(time.strftime("%H:%M:%S"), describe(event), flush=True)


def _non_negative(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(_("must be 0 or more"))
    return value


def _only_player() -> str | None:
    """The name of the single open MPRIS player; None (after saying why) if there is not exactly one."""
    from .player import MprisPlayer

    names = [info.name for info in MprisPlayer.discover()]
    if len(names) == 1:
        return names[0]
    reason = _("no MPRIS player is open") if not names else _("several players are open")
    print(_("{reason}: choose one with -p (see `tapwire players`)").format(reason=reason), file=sys.stderr)
    return None


def cmd_run(args: argparse.Namespace) -> int:
    from gi.repository import GLib

    from .session import RecordingSession

    player = args.player or _only_player()
    if not player:
        return 2
    settings = Settings(player=player, pause_mode=PauseMode(args.pause), max_tracks=args.count)
    if args.out:
        settings.out_dir = Path(args.out).expanduser()
    session = RecordingSession.create(settings)
    session.events.connect(_log)
    loop = GLib.MainLoop()
    session.events.connect(lambda event: loop.quit() if event.kind is EventKind.STOPPED else None)

    def shutdown(*_):
        session.stop()  # discards the partial in-progress recording
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)
    print(f"player={settings.player} out={settings.out_dir} pause={settings.pause_mode.value}", flush=True)
    session.start()
    loop.run()
    return 0


def cmd_players(_args: argparse.Namespace) -> int:
    from .player import MprisPlayer

    for info in MprisPlayer.discover():
        track = printable(info.track.label) if info.track else ""
        print("\t".join((printable(info.name), printable(info.identity), info.status.value, track)))
    return 0


def cmd_streams(_args: argparse.Namespace) -> int:
    from .pipewire import PipeWire

    for s in PipeWire().streams():
        print(f"{s.node_id}\t{printable(s.app_name)}\t{printable(s.binary)}\t{printable(s.media_name)}")
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    from .gui.app import main as gui_main  # GTK is only imported when the window is wanted

    return gui_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tapwire", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help=_("record each track of the player into a FLAC file (runs until Ctrl+C)"))
    run.add_argument("-p", "--player", metavar="NAME",
                     help=_("MPRIS name (see `tapwire players`); optional if only one player is open"))
    run.add_argument("-o", "--out", help=_("output folder (default: ~/Music/tapwire)"))
    run.add_argument("-n", "--count", type=_non_negative, default=0, metavar="N",
                     help=_("stop after saving N tracks (default 0: manual, until Ctrl+C)"))
    run.add_argument("--pause", choices=[m.value for m in PauseMode], default=PauseMode.DROP.value,
                     help=_("what to do on pause: drop (discard, default) or join (experimental)"))
    run.set_defaults(func=cmd_run)

    players = sub.add_parser("players", help=_("list open MPRIS players"))
    players.set_defaults(func=cmd_players)

    streams = sub.add_parser("streams", help=_("list audio streams playing on PipeWire"))
    streams.set_defaults(func=cmd_streams)

    gui = sub.add_parser("gui", help=_("open the graphical interface"))
    gui.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
