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

from typing import Optional

import braille
import config
from config.configFlags import BrailleMode
from logHandler import log

from .flowIndent import DEFAULT_STYLE as DEFAULT_INDENT_STYLE
from .flowIndent import INDENT_STYLES
from . import flowTable

CONFIG_SECTION = "BrlMultiline"

INDENT_STYLE_OPTIONS = ", ".join(f'"{style}"' for style in INDENT_STYLES)
"""The indent styles as `configobj` writes an option list, built from the one definition.

Written from `flowIndent.INDENT_STYLES` rather than beside it, so that adding a style is one
edit in the module that knows what a style is, and this specification cannot drift from it.
"""

#: Largest number of segments the settings dialog offers, and the largest that the per
#: segment commands are generated for. This is a limit on the user interface only: there
#: is no fixed limit on how many segments a view may have, because the real constraint is
#: geometric, and `layout.validateRects` enforces it. Code driving the display directly
#: may build views with more segments than this.
MAX_UI_SEGMENTS = 8

MAX_FLOW_ROWS = 64
"""Largest band height the settings dialog offers. A limit on the spin control only."""

FLOW_MODES: tuple[str, ...] = ("browseMode", "objects", "editableText")
"""The kinds of content a flow may be used for, in the order the settings dialog offers them.

A flow is turned on for a display and then for each kind of content separately, because
they are different pieces of work and they arrive one at a time: browse mode is built and
tested, objects and focused controls are not. A reader who wants a flow on a web page and
NVDA's own presentation everywhere else says so here rather than reaching for the command
each time.

Adding a kind means adding its name here, its default below, and its label in
`settingsPanel`. Everything that reads the setting works from this list.
"""

FLOW_MODE_DEFAULTS: dict[str, bool] = {"browseMode": True, "objects": False, "editableText": False}
"""Whether each kind of content flows when a flow is turned on at all.

Browse mode is on by default because it is what turning a flow on has meant so far, and a
reader upgrading should get what the command gave them.

Objects — a list, a menu, the choices of a combo box — and standalone editable text are
off, because those readings are new. Turning the flow on should not change how a reader's
dialogs and editors behave until they ask for it.
"""


def flowModeKey(mode: str) -> str:
	""":return: the configuration key holding whether a flow is used for one kind of content.

	One key per kind rather than a list of the enabled ones, because a configuration profile
	overrides single values: a profile for one browser can turn browse mode off without
	restating every other kind.
	"""
	return f"flow{mode[0].upper()}{mode[1:]}"


configSpec = {
	"displays": {
		"__many__": {
			"segmentsEnabled": "boolean(default=True)",
			"segmentCount": f"integer(default=1, min=1, max={MAX_UI_SEGMENTS})",
			"segmentSizes": "int_list(default=list())",
			"focusSegment": "integer(default=-1, min=-1)",
			"messageSegment": "integer(default=-1, min=-1)",
			"reverseScrollBtns": "boolean(default=False)",
			"reverseScrollBtnsMigrated": "boolean(default=False)",
			"showDocumentLines": "boolean(default=False)",
			"flowEnabled": "boolean(default=False)",
			"flowRows": f"integer(default=0, min=0, max={MAX_FLOW_ROWS})",
			"flowDisplay": 'string(default="")',
			"flowGroundOnQuickNav": "boolean(default=True)",
			"flowWriteByParagraph": "boolean(default=True)",
			"flowIndentStyle": f'option({INDENT_STYLE_OPTIONS}, default="{DEFAULT_INDENT_STYLE}")',
			"flowLineFocus": "boolean(default=True)",
			"flowTableRowHeight": f"integer(default={flowTable.DEFAULT_MAX_ROWS}, min=1, max={flowTable.MAX_TABLE_ROWS})",
			"flowTableTruncate": "boolean(default=False)",
			"flowTablePinKey": "boolean(default=True)",
			"flowTableLiveSeconds": "integer(default=2, min=0, max=60)",
			"flowTableHeaders": "boolean(default=True)",
			**{
				flowModeKey(mode): f"boolean(default={FLOW_MODE_DEFAULTS.get(mode, False)})"
				for mode in FLOW_MODES
			},
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
- `focusSegment`: which segment tracks the system focus. -1 means the last. Not bounded
	above by `MAX_UI_SEGMENTS`, because on a display made of several these are numbered
	across all of them and there can be more of them than any one display offers. Both
	readers — `views.viewFromConfig` and `views.deviceView` — check the number against the
	segments that actually exist and fall back to the last, so a bound here would only
	truncate a legitimate number without making an illegitimate one safe.
- `messageSegment`: which segment NVDA's flash messages appear in. -1 means whichever segment
	is following the focus, which is where a message appears on an undivided display and is
	therefore the familiar answer.
- `reverseScrollBtns`: swap the panning keys. NVDA has no such setting of its own. Stored
	per physical display rather than per arrangement, because the keys sit differently on
	each piece of hardware; see `panning` for which display's setting applies when several
	are driven as one.
- `reverseScrollBtnsMigrated`: bookkeeping, and only on a composite display's own section.
	Set once the value above has been carried onto the displays behind it. See
	L{migrateReverseScrollButtons}.
- `showDocumentLines`: fill the segments around the focus segment with the document lines
	above and below the caret.
- `flowEnabled`: whether a band of this display reads content as one flowing document.
- `flow<Mode>`: one per entry in `FLOW_MODES`, whether that kind of content flows.
- `flowRows`: how many rows the band takes, counted from the top of the display it is on.
	0, the default, means all of them.
- `flowDisplay`: the driver name of the physical display to put the band on, when several
	are driven as one. Empty, the default, means the tallest of them.
- `flowGroundOnQuickNav`: whether a browse mode jump by structure re-grounds the band.
- `flowWriteByParagraph`: whether a multi line edit being written in is cut into blocks by
	paragraph rather than by the reader's own read by paragraph setting.
- `flowIndentStyle`: how one level of depth is drawn, for content that has any. Per display
	and per profile like everything else here, because the answer depends on how many cells
	the row has: two spaces on a Monarch's 32 is a different proposition from two on 80.
- `flowLineFocus`: whether the row the focus is on is marked at the left margin where its
	indent has room for the mark.
- `flowTableRowHeight`: how many rows of the band one row of a table may use.
- `flowTableTruncate`: whether a cell too long for its column is cut rather than wrapped.
- `flowTablePinKey`: whether the first column is repeated at the left of every page after
	the first, so that a reader six columns across a watchlist still knows whose row it is.
- `flowTableLiveSeconds`: how often a table laid out in columns reads itself again, so that
	values changing under the reader's hand reach the display. Zero turns it off.
- `flowTableHeaders`: whether a table's header row is held on the top row of the band,
	whatever the rest of it is showing.

Everything above is read through `config.conf`, which is profile aware, so all of it can
differ per configuration profile. That matters most for the flow: a profile triggered by
one browser can flow while the normal configuration does not, which is how a reader keeps
one application reading the way it always has.
"""


def flowIndentStyle(displayKey: Optional[str] = None) -> str:
	""":return: how one level of depth is drawn on this display.

	See `flowIndent.INDENT_STYLES` for what the answers mean. An unreadable setting reads as
	the default rather than raising, for the reason every accessor here does: a configuration
	this version does not understand must not stop a display being drawn.
	"""
	try:
		style = str(getDisplayConfig(displayKey)["flowIndentStyle"])
	except Exception:
		log.debugWarning("Could not read flowIndentStyle", exc_info=True)
		return DEFAULT_INDENT_STYLE
	return style if style in INDENT_STYLES else DEFAULT_INDENT_STYLE


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


def setFocusSegment(number: int, displayKey: str | None = None) -> None:
	"""Store which segment follows the system focus.

	Written through `config.conf`, so the answer belongs to the profile in force, as the
	same setting typed into the dialog does.

	:param number: the segment to follow the focus, counted across the whole display, or -1
		for the last one.
	:param displayKey: the display to store against, or None for the current one.
	"""
	getDisplayConfig(displayKey)["focusSegment"] = int(number)


def getMessageSegment(displayKey: str | None = None) -> int:
	""":return: the segment NVDA's flash messages should appear in.

	-1, the default, means the segment following the focus. That is where a message lands on
	an undivided display, so it is what a user changing nothing should get.
	"""
	try:
		return int(getDisplayConfig(displayKey)["messageSegment"])
	except Exception:
		# A configuration problem must not cost the user their messages.
		log.debugWarning("Could not read messageSegment", exc_info=True)
		return -1


def shouldReverseScrollButtons(displayKey: str | None = None) -> bool:
	""":return: whether the panning keys should be swapped on this display.

	Callers driving a composite pass the key of the member whose key was pressed, which
	`panning.shouldReverse` works out. Passing None means the connected display, which is
	the right answer whenever there is only one.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["reverseScrollBtns"])
	except Exception:
		# Never let a configuration problem break panning.
		log.debugWarning("Could not read reverseScrollBtns", exc_info=True)
		return False


def migrateReverseScrollButtons(displayKey: str, memberKeys) -> None:
	"""Carry a composite display's stored panning direction onto the displays behind it.

	Reversed panning used to be one setting for the whole composite, and is now a setting of
	each physical display, because which key means forward depends on where the keys sit. The
	old value is no longer read anywhere, so without this an existing user's panning silently
	swaps back to the default the first time they run this version — and panning the wrong way
	is the kind of thing a reader blames on themselves for a while before blaming the software.

	Copied to every member that has not been asked, because that is what the old setting meant:
	it applied to whichever keys were pressed. A member with a setting of its own keeps it,
	including an explicit `False` — `AggregatedSection.isSet` reports whether a key is stored in
	any profile rather than whether it differs from its default, so the two are distinguishable
	and a choice the user made by hand is never the thing to overwrite.

	Done once, and recorded, so that a member left unset here is not asked again the next time
	the display is rebuilt. The mark goes on last: an attempt that failed part way through is
	better retried than remembered as finished.

	:param displayKey: the composite display's own configuration key.
	:param memberKeys: the configuration keys of the physical displays behind it.
	"""
	try:
		section = getDisplayConfig(displayKey)
		if section["reverseScrollBtnsMigrated"]:
			return
		keys = list(memberKeys)
		if section["reverseScrollBtns"]:
			carried = []
			for key in keys:
				member = getDisplayConfig(key)
				if member.isSet("reverseScrollBtns"):
					# This display has been asked directly. Its own answer is the better one.
					continue
				member["reverseScrollBtns"] = True
				carried.append(key)
			if carried:
				log.info(
					f"BrlMultiline: the panning keys were reversed for {displayKey} as a whole. "
					f"That is now a setting of each display, and has been copied to {carried}.",
				)
		section["reverseScrollBtnsMigrated"] = True
	except Exception:
		# Panning the default way round is a poor outcome and not one worth losing a display to.
		# The mark is unset, so the next rebuild tries again.
		log.debugWarning("Could not carry over the reversed panning setting", exc_info=True)


def shouldShowDocumentLines(displayKey: str | None = None) -> bool:
	""":return: whether free segments should show the lines around the caret."""
	try:
		return bool(getDisplayConfig(displayKey)["showDocumentLines"])
	except Exception:
		log.debugWarning("Could not read showDocumentLines", exc_info=True)
		return False


def isFlowEnabled(displayKey: str | None = None) -> bool:
	""":return: whether a band of this display should read content as a flowing document.

	The master switch, and the one the toggle command sets. It says nothing about whether
	there is anything to flow here and now: see L{shouldClaimFlowBand}.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowEnabled"])
	except Exception:
		# A configuration problem should leave the display presenting content the way NVDA
		# always has, which is what a flow that never starts amounts to.
		log.debugWarning("Could not read flowEnabled", exc_info=True)
		return False


def setFlowEnabled(enabled: bool, displayKey: str | None = None) -> None:
	"""Turn the flow on or off for a display, in the profile being edited.

	Written through `config.conf`, so a profile triggered by one application stores its own
	answer and the normal configuration keeps its.

	:param enabled: whether to read a band of this display as a flowing document.
	:param displayKey: the display to store against, or None for the current one.
	"""
	getDisplayConfig(displayKey)["flowEnabled"] = bool(enabled)


def isFlowEnabledFor(mode: str, displayKey: str | None = None) -> bool:
	""":return: whether one kind of content should be read as a flow.

	Both switches have to be on: the display's, and this kind of content's. A reader who
	turns the flow off gets NVDA's presentation everywhere without having to visit each
	kind, and a reader who leaves it on chooses which kinds it applies to.

	:param mode: one of L{FLOW_MODES}. An unknown one never flows.
	:param displayKey: the display to look up, or None for the current one.
	"""
	if mode not in FLOW_MODES:
		return False
	try:
		section = getDisplayConfig(displayKey)
		return bool(section["flowEnabled"]) and bool(section[flowModeKey(mode)])
	except Exception:
		log.debugWarning(f"Could not read whether {mode} flows", exc_info=True)
		return False


def shouldClaimFlowBand(displayKey: str | None = None) -> bool:
	""":return: whether a band should be claimed on this display at all.

	A claim with no kind of content enabled would take rows the reader configured for
	something else and never put anything in them, so the band is only laid down when it
	could show something.
	"""
	return any(isFlowEnabledFor(mode, displayKey) for mode in FLOW_MODES)


def getFlowRows(displayKey: str | None = None) -> int:
	""":return: how many rows the band takes, 0 meaning all of the display it is on.

	Counted from the top of that display, so the rows below it keep whatever the segment
	layout puts there. A band taller than the display it lands on is trimmed to fit rather
	than refused: the setting outliving the hardware it was typed for is the ordinary case.
	"""
	try:
		return max(0, int(getDisplayConfig(displayKey)["flowRows"]))
	except Exception:
		log.debugWarning("Could not read flowRows", exc_info=True)
		return 0


def getFlowDisplay(displayKey: str | None = None) -> str:
	""":return: the driver name of the display to put the band on, empty for the tallest.

	Only meaningful when several displays are driven as one. A band must lie inside one
	physical display's live cells, so it is always on one of them; this says which.
	"""
	try:
		return str(getDisplayConfig(displayKey)["flowDisplay"] or "")
	except Exception:
		log.debugWarning("Could not read flowDisplay", exc_info=True)
		return ""


def shouldGroundOnQuickNav(displayKey: str | None = None) -> bool:
	""":return: whether a browse mode jump by structure re-grounds a flow.

	On, a quick navigation key that skips a section — a heading, a table, a landmark — puts
	its target at the top of the band and flows from there, which is a place to start
	reading rather than the old window nudged along. Off, every cursor move keeps the
	reader's window and merely tracks the cursor. Which moves count as structural is
	`flowQuickNav.GROUNDING_TYPES`, and is not a setting.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowGroundOnQuickNav"])
	except Exception:
		log.debugWarning("Could not read flowGroundOnQuickNav", exc_info=True)
		return True


def shouldWriteByParagraph(displayKey: str | None = None) -> bool:
	""":return: whether a multi line edit being written in is read a paragraph at a time.

	On, which is the default, a block of such an edit is a paragraph whatever NVDA's read by
	paragraph setting says. A paragraph is what the writer typed; a line is what the
	control's wrapping made of it, and a writer thinking about their own text thinks in the
	first. The evidence behind the default is in `flowBuild.readingUnitFor`.

	Off, an edit is cut up the same way a page is, by L{flowBuild.readingUnit}, so the one
	setting governs everything. That is the setting to reach for when a particular editor
	answers better by line, and it is what makes the choice testable in more places than the
	one editor the default was chosen from.

	Reading, everywhere else, follows the reader's own setting either way. This says nothing
	about a page, a list, or an editor being read rather than written in.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowWriteByParagraph"])
	except Exception:
		log.debugWarning("Could not read flowWriteByParagraph", exc_info=True)
		return True


def shouldMarkLineFocus(displayKey: str | None = None) -> bool:
	""":return: whether the row the focus is on is marked at the left margin.

	On, which is the default. The cursor already says where the focus is and it is not
	enough for reading across rows: it is dots 7 and 8 under the text, so it is found by
	reading the row it is under, and a reader running a hand down eight rows of a folder
	tree to see where they are has to read all eight. The mark is in the same column on
	every row, so the same question is one pass of the hand.

	Off for a reader who would rather have the two cells, or who finds a second mark beside
	the cursor is one mark too many. Nothing is lost by turning it off: the cursor is still
	where it always was, and the mark is never the only thing saying where the focus is.

	It draws only where the row's indent already has room for it, so it is a setting about
	indented content — trees, nested lists — and does nothing at all in flat prose.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowLineFocus"])
	except Exception:
		log.debugWarning("Could not read flowLineFocus", exc_info=True)
		return True


def tableRowHeight(displayKey: str | None = None) -> int:
	""":return: how many rows of the band one row of a table may use.

	One by default. The comparison a table is for is between rows, and rows the reader can
	hold under one hand at once: eight one-row records beat two four-row ones for every
	question a watchlist is asked. Raising it is what a reader does when a column they need
	whole will not fit, and the alternative to raising it is hiding a column.

	See `flowTable.planFor`, which does the fitting, and `flowTable.MAX_TABLE_ROWS`, which is
	where this stops being a table and reading order becomes the better answer.
	"""
	try:
		wanted = int(getDisplayConfig(displayKey)["flowTableRowHeight"])
	except Exception:
		log.debugWarning("Could not read flowTableRowHeight", exc_info=True)
		return flowTable.DEFAULT_MAX_ROWS
	return max(1, min(wanted, flowTable.MAX_TABLE_ROWS))


def shouldTruncateTableCells(displayKey: str | None = None) -> bool:
	""":return: whether a cell too long for its column is cut rather than wrapped.

	Off by default, because a display that silently drops data is not a display of the data.
	Wrapped, a table row grows as tall as its longest cell needs and the value is all there;
	the reader can tell a short cell from a cut one, which they cannot when everything is cut
	to the same width.

	On, a table row is always one row of the band and a long cell is cut at its column. That
	is worth having and it is a judgement about a particular table: an options watchlist has
	symbols long enough to push every other column into uselessness, and a reader who can
	recognise a contract from its first few cells would rather have the whole table under
	their hands than have every row grow to fit the one column they can already read.

	See `flowTable.OVERFLOW_STYLES`.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowTableTruncate"])
	except Exception:
		log.debugWarning("Could not read flowTableTruncate", exc_info=True)
		return False


def shouldPinKeyColumn(displayKey: str | None = None) -> bool:
	""":return: whether the first column is repeated at the left of every later page.

	On by default. A table wider than the band is read a page of columns at a time, and six
	columns across a watchlist the reader is feeling four numbers with nothing to say whose
	numbers they are — the symbol that would say so is two page turns back. The repeated
	column costs its width on every page after the first and buys back the one thing that
	makes the rest of the page mean anything.

	Nothing is repeated when the table fits on one page, which is most tables: the cost is
	paid only where the problem exists.

	See `flowTable.ColumnPlan.keyColumn`.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowTablePinKey"])
	except Exception:
		log.debugWarning("Could not read flowTablePinKey", exc_info=True)
		return True


def liveTableSeconds(displayKey: str | None = None) -> int:
	""":return: how often a table laid out in columns reads itself again, in seconds.

	Two by default; zero turns it off.

	A watchlist during market hours changes under the reader's hand and nothing tells the
	band. NVDA reports a cell's new value only while the browse mode caret is in that cell —
	the right answer for speech and for a display showing one cell at a time, and no answer at
	all for a display showing a page of a table at once, where the reader feels a price that
	was true when they arrived and has no way to know it is not true now.

	Off is worth having for a table that cannot change, and for a document where reading a
	page of cells every two seconds is more than the reader wants spent. On a table that is
	not live the pass costs the read and nothing else: the display is only written when the
	cells came out different.

	See `FlowBand._refreshLiveTable`.
	"""
	try:
		wanted = int(getDisplayConfig(displayKey)["flowTableLiveSeconds"])
	except Exception:
		log.debugWarning("Could not read flowTableLiveSeconds", exc_info=True)
		return 2
	return max(0, min(wanted, 60))


def shouldPinTableHeaders(displayKey: str | None = None) -> bool:
	""":return: whether a table's header row stays on the top row of the band.

	On by default, and it costs the band a row. What it buys is that the headers are there at
	all: the window starts where the reader is, so a layout turned on from the middle of a
	table showed the columns and never said what any of them was. Scrolling back up to look
	is not an answer either, because the answer is wanted while reading somewhere else.

	Off is worth having on a short band, where a row is a quarter of what there is, and on a
	table whose first row is not headers.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowTableHeaders"])
	except Exception:
		log.debugWarning("Could not read flowTableHeaders", exc_info=True)
		return True


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
