#!/usr/bin/env bash
# Usage: tools/i18n.sh extract   -> regenerates po/tapwire.pot and merges into every po/*.po
#        tools/i18n.sh compile   -> builds src/tapwire/locale/<lang>/LC_MESSAGES/tapwire.mo
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in
  extract)
    xgettext --language=Python --from-code=UTF-8 --keyword=_ --keyword=pgettext:1c,2 --add-comments \
      --package-name=tapwire \
      --copyright-holder=growlnx --msgid-bugs-address=growlnx@protonmail.com -o po/tapwire.pot $(find src -name '*.py' | sort)
    for po in po/*.po; do msgmerge --update --backup=none -q "$po" po/tapwire.pot; done ;;
  compile)
    for po in po/*.po; do
      lang=$(basename "$po" .po)
      mkdir -p "src/tapwire/locale/$lang/LC_MESSAGES"
      msgfmt --check -o "src/tapwire/locale/$lang/LC_MESSAGES/tapwire.mo" "$po"
    done ;;
  *) echo "usage: $0 extract|compile" >&2; exit 2 ;;
esac
