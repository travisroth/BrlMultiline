# BrlMultiline: translation for the modules that also run without NVDA.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The gettext functions for modules that are pure Python, so they also run outside NVDA.

Every module of the add-on with words to translate calls `addonHandler.initTranslation()`, which
points its `_`, `ngettext` and `pgettext` at the add-on's translations rather than NVDA's. The
layout overview's rules and drawing (`layoutRegions`, `layoutDraw`, `appRegions`) are kept free
of NVDA so they can be tested against saved pages and run by the probe spikes, where there is no
add-on to find. They import the functions from here instead:

	from .i18n import _, ngettext, pgettext

Inside NVDA these are the add-on's own. Anywhere else they give the English back as it is,
which is what the tests and the spikes read.
"""

from __future__ import annotations

try:
	import addonHandler

	# Sets this module's `_`, `ngettext`, `pgettext` and `npgettext` to the add-on's.
	addonHandler.initTranslation()
except Exception:
	# Outside NVDA, or a copy loaded by the probe from somewhere NVDA does not know as the add-on.
	pass


def _english(message: str) -> str:
	return message


def _englishPlural(singular: str, plural: str, count: int) -> str:
	return singular if count == 1 else plural


def _englishInContext(context: str, message: str) -> str:
	return message


# The add-on's, where `initTranslation` set them, or else English; never whatever `builtins._`
# holds, which outside NVDA can be the interactive interpreter's last result.
_ = globals().get("_", _english)
ngettext = globals().get("ngettext", _englishPlural)
pgettext = globals().get("pgettext", _englishInContext)

__all__ = ["_", "ngettext", "pgettext"]
