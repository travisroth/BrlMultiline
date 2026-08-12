# BrlMultiline: configuration.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Per display configuration, and the one setting of NVDA's own the add-on has to obey.

Settings are stored per display rather than globally, because the useful layout differs
sharply between displays: an 8 row Monarch and an 80 cell single row Focus want different
segment counts, and reversed panning keys are wanted on one and not the other.

Displays are keyed on driver name plus geometry, since the same driver can present
different sized displays.

L{isSpeechOutputMode} is the exception: it is NVDA's setting rather than this add-on's, and
lives here because every place that has to consult it reads its configuration through this
module already.
"""

import braille
import config
from config.configFlags import BrailleMode
from logHandler import log

CONFIG_SECTION = "BrlMultiline"

#: Largest number of segments the settings dialog offers, and the largest that the per
#: segment commands are generated for. This is a limit on the user interface only: there
#: is no fixed limit on how many segments a view may have, because the real constraint is
#: geometric, and `layout.validateRects` enforces it. Code driving the display directly
#: may build views with more segments than this.
MAX_UI_SEGMENTS = 8

configSpec = {
	"displays": {
		"__many__": {
			"segmentsEnabled": "boolean(default=True)",
			"segmentCount": f"integer(default=1, min=1, max={MAX_UI_SEGMENTS})",
			"segmentSizes": "int_list(default=list())",
			"focusSegment": f"integer(default=-1, min=-1, max={MAX_UI_SEGMENTS - 1})",
			"reverseScrollBtns": "boolean(default=False)",
			"showDocumentLines": "boolean(default=False)",
		},
	},
}
"""Configuration specification, registered under `config.conf.spec[CONFIG_SECTION]`.

- `segmentsEnabled`: whether to divide the display at all. Turning it off overrides the
	layout below without disturbing it, so that turning it back on restores the arrangement
	rather than requiring it to be typed again.
- `segmentCount`: how many segments to divide the display into. Used when
	`segmentSizes` is empty, which is the usual case.
- `segmentSizes`: explicit segment sizes, in rows on a multi row display and in cells on
	a single row display. Empty means divide evenly into `segmentCount`.
- `focusSegment`: which segment tracks the system focus. -1 means the last.
- `reverseScrollBtns`: swap the panning keys. NVDA has no such setting of its own.
- `showDocumentLines`: fill the segments around the focus segment with the document lines
	above and below the caret.
"""


def initialize() -> None:
	"""Register the add-on's configuration specification."""
	config.conf.spec[CONFIG_SECTION] = configSpec


def getDisplayKey() -> str:
	""":return: a configuration key identifying the currently connected display.

	The key combines the driver name with the display geometry, so that the same driver
	attached to differently sized hardware keeps separate settings.
	"""
	handler = braille.handler
	dimensions = handler.displayDimensions
	name = handler.display.name if handler.display else "noBraille"
	return f"{name}_{dimensions.numRows}x{dimensions.numCols}"


def getDisplayConfig(displayKey: str | None = None):
	"""Get the configuration section for a display, creating it if this is a new display.

	NVDA does not materialise a `__many__` subsection on demand: reading a display that has
	never been written raises `KeyError`, and every display is new until something writes
	settings for it. So the section is created here, as NVDA creates one per synthesiser and
	per braille display driver in `autoSettingsUtils.autoSettings`. A section created that
	way is given an empty specification, which would leave the settings inside it raising
	`KeyError` in turn, so the specification is attached to it as well.

	:param displayKey: the display to look up, or None for the current one.
	:return: the configuration section. Values not yet set return their defaults.
	"""
	if displayKey is None:
		displayKey = getDisplayKey()
	displays = config.conf[CONFIG_SECTION]["displays"]
	if not displays.isSet(displayKey):
		displays[displayKey] = {}
	section = displays[displayKey]
	section.spec.update(configSpec["displays"]["__many__"])
	return section


def areSegmentsEnabled(displayKey: str | None = None) -> bool:
	""":return: whether this display should be divided at all.

	The master switch over everything the user configured, so that a layout can be put aside
	for a while — a task that wants the whole display as one piece, a display being lent to
	someone else — and taken up again without being retyped. It overrides the user's own
	settings only: a view or a panel activated by code is a claim about what the display is
	being used for rather than a preference, and is not affected.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["segmentsEnabled"])
	except Exception:
		# A configuration problem should leave the add-on doing what it was asked to do,
		# rather than silently taking the display back to one segment.
		log.debugWarning("Could not read segmentsEnabled", exc_info=True)
		return True


def setSegmentsEnabled(enabled: bool, displayKey: str | None = None) -> None:
	"""Turn division of a display on or off, leaving its layout alone.

	:param enabled: True to use the configured layout, False to show one segment.
	:param displayKey: the display to store against, or None for the current one.
	"""
	getDisplayConfig(displayKey)["segmentsEnabled"] = bool(enabled)


def getLayout(displayKey: str | None = None) -> int | list[int]:
	"""Get the segment layout configured for a display.

	:param displayKey: the display to look up, or None for the current one.
	:return: a list of explicit segment sizes if one is configured, otherwise a count of
		segments to divide the display evenly into.
	"""
	section = getDisplayConfig(displayKey)
	sizes = list(section["segmentSizes"])
	if sizes:
		return sizes
	return int(section["segmentCount"])


def getFocusSegment(displayKey: str | None = None) -> int:
	""":return: the configured focus segment number, -1 meaning the last segment."""
	return int(getDisplayConfig(displayKey)["focusSegment"])


def shouldReverseScrollButtons(displayKey: str | None = None) -> bool:
	""":return: whether the panning keys should be swapped on this display."""
	try:
		return bool(getDisplayConfig(displayKey)["reverseScrollBtns"])
	except Exception:
		# Never let a configuration problem break panning.
		log.debugWarning("Could not read reverseScrollBtns", exc_info=True)
		return False


def shouldShowDocumentLines(displayKey: str | None = None) -> bool:
	""":return: whether free segments should show the lines around the caret."""
	try:
		return bool(getDisplayConfig(displayKey)["showDocumentLines"])
	except Exception:
		log.debugWarning("Could not read showDocumentLines", exc_info=True)
		return False


def isSpeechOutputMode() -> bool:
	""":return: whether NVDA is showing speech on the display rather than following cursors.

	This is NVDA's own braille mode, not a setting of this add-on's, and it decides whether
	the add-on may write to the display at all. In speech output mode NVDA stops handling
	focus, caret and review moves, and everything shown comes from speech. Anything this
	add-on drew into a segment would sit there under the reader's fingers beside the speech,
	with nothing to refresh or remove it, so every path that writes content of the add-on's
	own asks this first: pinned objects in L{GlobalPlugin.refreshMonitors}, and document
	lines in `patches`.
	"""
	try:
		return config.conf["braille"]["mode"] == BrailleMode.SPEECH_OUTPUT.value
	except Exception:
		# Reading it failed, so assume the ordinary mode. Refusing to draw would be the more
		# cautious guess, but it would leave a display that never updates.
		log.debugWarning("Could not read NVDA's braille mode", exc_info=True)
		return False


def setLayout(segmentCount: int, segmentSizes: list[int] | None, displayKey: str | None = None) -> None:
	"""Store a segment layout for a display.

	:param segmentCount: number of segments to divide the display evenly into.
	:param segmentSizes: explicit sizes, or None or an empty list to divide evenly.
	:param displayKey: the display to store against, or None for the current one.
	"""
	section = getDisplayConfig(displayKey)
	section["segmentCount"] = segmentCount
	section["segmentSizes"] = list(segmentSizes) if segmentSizes else []
