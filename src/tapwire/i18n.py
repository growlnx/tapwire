"""Translations via gettext.

The language comes from the usual environment (LANGUAGE, LC_ALL, LC_MESSAGES, LANG), which
GNOME and other desktops set from the user's language choice. Source strings are English;
catalogs live in `locale/<lang>/LC_MESSAGES/tapwire.mo`. Without a catalog the English text is used.
"""
from __future__ import annotations

import gettext
from pathlib import Path

DOMAIN = "tapwire"
LOCALE_DIR = Path(__file__).parent / "locale"

_translation = gettext.translation(DOMAIN, localedir=LOCALE_DIR, fallback=True)
_ = _translation.gettext
pgettext = _translation.pgettext  # pgettext(context, msgid): same word, different meaning
