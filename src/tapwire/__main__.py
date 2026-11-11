"""Lets the package run without being installed: `python -m tapwire gui`, `python src/tapwire run`, ...

Same as the installed `tapwire` command.
"""
import sys

if __package__ in (None, ""):
    # Started as `python src/tapwire ...` (a directory, not a package): make `import tapwire` work.
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tapwire.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
