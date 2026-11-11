#!/usr/bin/env bash
# Builds an architecture-independent .deb (pure Python) into dist/.
# Usage: tools/build-deb.sh [version]     (default: the version in pyproject.toml)
set -euo pipefail
cd "$(dirname "$0")/.."

APP=tapwire
APP_ID=dev.tapwire.gui
VERSION="${1:-$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)}"
[[ "$VERSION" =~ ^[0-9][0-9A-Za-z.+~-]*$ ]] || { echo "invalid version: '$VERSION'" >&2; exit 2; }

PKG="${APP}_${VERSION}_all"
STAGE="build/deb/$PKG"
PYDIR="$STAGE/usr/lib/python3/dist-packages"

rm -rf "$STAGE"
mkdir -p "$PYDIR" "$STAGE/usr/bin" "$STAGE/DEBIAN" dist

# Fresh translation catalogs (needs gettext's msgfmt; the committed .mo files are the fallback).
if command -v msgfmt >/dev/null; then tools/i18n.sh compile; fi

# Python package, including locale/*.mo
cp -r src/tapwire "$PYDIR/tapwire"
find "$PYDIR" -name '__pycache__' -prune -exec rm -rf {} +

# Command-line entry points
write_launcher() {  # name, import path
  cat > "$STAGE/usr/bin/$1" <<EOF
#!/usr/bin/python3
from $2 import main
raise SystemExit(main())
EOF
}
write_launcher tapwire     tapwire.cli
write_launcher tapwire-gui tapwire.gui.app

# Desktop integration
install -Dm644 "data/$APP_ID.desktop" "$STAGE/usr/share/applications/$APP_ID.desktop"
install -Dm644 "data/icons/hicolor/scalable/apps/$APP_ID.svg" \
  "$STAGE/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
install -Dm644 "data/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg" \
  "$STAGE/usr/share/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
install -Dm644 LICENSE "$STAGE/usr/share/doc/$APP/copyright"

# Permissions: directories 755, files 644, launchers 755
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -exec chmod 644 {} +
chmod 755 "$STAGE/usr/bin/"*

INSTALLED_SIZE=$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $APP
Version: $VERSION
Architecture: all
Maintainer: growlnx <growlnx@protonmail.com>
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, ffmpeg, pipewire-bin
Section: sound
Priority: optional
Description: Record what an app plays as tagged FLAC files, one per track
 tapwire taps the audio of an app on PipeWire while it plays and saves every
 track as its own FLAC file with title, artist, album and cover art. Tracks are
 split using the player's own track information (MPRIS over D-Bus), and the
 sound keeps going to the speakers the whole time.
EOF

dpkg-deb --root-owner-group --build "$STAGE" "dist/$PKG.deb"
echo "built dist/$PKG.deb"
