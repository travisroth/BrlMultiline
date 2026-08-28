# BrlMultiline: configuration.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Per display configuration, and the settings of NVDA's own the add-on has to obey.

Settings are stored per display rather than globally, because the useful layout differs
sharply between displays: an 8 row Monarch and an 80 cell single row Focus want different
segment counts, and reversed panning keys are wanted on one and not the other.

Displays are keyed on driver name plus geometry, since the same driver can present
different sized displays.

L{isSpeechOutputMode} and the readers under "What NVDA itself was told" are the exception:
they are NVDA's settings rather than this add-on's, and live here because every place that
has to consult them reads its configuration through this module already.
"""

from typing import Optional

import braille
import config
from config.configFlags import BrailleMode, ReportTableHeaders
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

FOLLOW_NVDA = "follow"
ALWAYS = "always"
NEVER = "never"

FOLLOWING = (FOLLOW_NVDA, ALWAYS, NEVER)
"""What a setting that can defer to NVDA may say.

Three states rather than two, and the third is the default. NVDA's Document Formatting
settings apply outside browse mode as well, and a reader who has told NVDA what they want
from a table has said it once and should not have to say it again here.

The other two exist because braille has room speech does not. Turning table headers off for
speech is usually about *repetition* — "From, Received" before every message in a list, which
the reader already knows — and a header shown once on its own row costs none of that. So a
reader may well want the row in braille having turned the announcement off, and the reverse
on a short display where a row is a quarter of everything there is."""

LEGACY_FOLLOWING = {
	"flowTableHeadersMode": "flowTableHeaders",
	"flowTablePinKeyMode": "flowTablePinKey",
}
"""The three state settings that used to be checkboxes, and the key each was stored under.

Both of these were `boolean(default=True)` before they learned to defer to NVDA. A stored
`False` is not a value the new specification allows, and `configobj` replaces a value that
fails validation with the default before any code of this add-on is reached — so a reader who
had turned the header row off would have been given it back, and told nothing about it,
because the new default is to follow NVDA and NVDA reports table headers by default.

Hence a new key beside the old one rather than the old one reused. The old key stays in the
specification, because a key with no specification is a key `configobj` throws away, and it
is read only where the reader answered it and has not answered its replacement.
"""

FOLLOWING_OPTIONS = ", ".join(f'"{state}"' for state in FOLLOWING)
"""The three states as `configobj` writes an option list, built from the one definition."""

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
			"flowTablePinKeyMode": f'option({FOLLOWING_OPTIONS}, default="{FOLLOW_NVDA}")',
			"flowTablePinKey": "boolean(default=True)",
			"flowLiveSeconds": "integer(default=0, min=0, max=60)",
			"flowLiveUpdates": "boolean(default=True)",
			"flowTableHeadersMode": f'option({FOLLOWING_OPTIONS}, default="{FOLLOW_NVDA}")',
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
- `flowTablePinKeyMode`: whether the first column is repeated at the left of every page after
	the first, so that a reader six columns across a watchlist still knows whose row it is.
	One of `FOLLOWING`; following means NVDA's own `reportTableHeaders` asking for row
	headers.
- `flowTablePinKey`: what the setting above used to be, when it was a checkbox. Kept and read
	where the reader answered it and has not answered the new one. See L{LEGACY_FOLLOWING}.
- `flowLiveSeconds`: how often the band reads its content again on a timer, over and above
	reading it when the page says it changed. Zero waits to be told.
- `flowLiveUpdates`: whether the band follows a page that changes under it at all.
- `flowTableHeadersMode`: whether a table's header row is held on the top row of the band,
	whatever the rest of it is showing. One of `FOLLOWING`; following means NVDA's own
	`reportTableHeaders` asking for column headers.
- `flowTableHeaders`: what the setting above used to be, when it was a checkbox. Kept and read
	where the reader answered it and has not answered the new one. See L{LEGACY_FOLLOWING}.

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

	A table wider than the band is read a page of columns at a time, and six columns across a
	watchlist the reader is feeling four numbers with nothing to say whose numbers they are —
	the symbol that would say so is two page turns back. The repeated column costs its width
	on every page after the first and buys back the one thing that makes the rest of the page
	mean anything.

	Nothing is repeated when the table fits on one page, which is most tables: the cost is
	paid only where the problem exists.

	Follows NVDA by default, and the setting it follows is `reportTableHeaders` asking for
	*row* headers. A row header is the thing that says which row you are on, which is what
	this column is; NVDA says it before every cell, and a spatial layout says it once at the
	left of the page. See L{wantsRowHeaders} and `flowTable.ColumnPlan.keyColumn`.
	"""
	try:
		return _following(storedFollowing(displayKey, "flowTablePinKeyMode"), wantsRowHeaders)
	except Exception:
		log.debugWarning("Could not read flowTablePinKeyMode", exc_info=True)
		# The default, so a setting that cannot be read behaves as one that was never set.
		return wantsRowHeaders()


def shouldFollowLiveContent(displayKey: str | None = None) -> bool:
	""":return: whether the band re-reads its content when the page changes under it.

	On by default. NVDA refreshes the line the browse mode caret is in and nothing else,
	which is right when the line the caret is in is all that is showing; a band showing eight
	lines is showing seven that nobody is refreshing, and on a watchlist that means seven
	prices that were true when the reader arrived.

	The escape hatch, not the feature's main knob — see `liveReadSeconds` for how often, and
	`FlowBand.documentChanged` for what triggers a read. Worth turning off on a page whose
	churn is not worth following, or if following it ever fights the reader.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowLiveUpdates"])
	except Exception:
		log.debugWarning("Could not read flowLiveUpdates", exc_info=True)
		return True


def liveReadSeconds(displayKey: str | None = None) -> int:
	""":return: how often a table laid out in columns is read again on a timer, in seconds.

	Zero by default, which does not mean never: it means wait to be told. NVDA's virtual
	buffer says when a browse mode document changed under it — for any accessibility event
	its backend acted on, not only for a live region — and the band answers that instead of
	a clock. A page that says nothing then costs nothing at all, and a repricing watchlist is
	heard in a quarter of a second rather than in two.

	A number here reads on a timer as well, which is what a reader sets when a page changes
	without saying so. `FlowBand._pollMillis` also falls back to a timer when the patch that
	carries the news is not installed, so zero never means silently losing the updates.

	The band is what NVDA's own answer cannot be. NVDA reports a cell's new value only while
	the browse mode caret is in that cell, which is right for speech and for a display showing
	one cell at a time, and no answer at all for a display showing a page of a table at once.

	See `FlowBand.documentChanged` and `patches._handleUpdateTellingTheBand`.
	"""
	try:
		wanted = int(getDisplayConfig(displayKey)["flowLiveSeconds"])
	except Exception:
		log.debugWarning("Could not read flowLiveSeconds", exc_info=True)
		return 0
	return max(0, min(wanted, 60))


def shouldPinTableHeaders(displayKey: str | None = None) -> bool:
	""":return: whether a table's header row stays on the top row of the band.

	It costs the band a row. What it buys is that the headers are there at all: the window
	starts where the reader is, so a layout turned on from the middle of a table showed the
	columns and never said what any of them was. Scrolling back up to look is not an answer
	either, because the answer is wanted while reading somewhere else.

	Off is worth having on a short band, where a row is a quarter of what there is, and on a
	table whose first row is not headers.

	Follows NVDA by default, and the setting it follows is `reportTableHeaders` asking for
	*column* headers. See L{wantsColumnHeaders}.
	"""
	try:
		return _following(storedFollowing(displayKey, "flowTableHeadersMode"), wantsColumnHeaders)
	except Exception:
		log.debugWarning("Could not read flowTableHeadersMode", exc_info=True)
		# The default, so a setting that cannot be read behaves as one that was never set.
		return wantsColumnHeaders()


def storedFollowing(displayKey, key: str) -> str:
	""":return: what a display was told about one three state setting, one of L{FOLLOWING}.

	The setting's own value where the reader has answered it. The checkbox it replaced where
	they have not, read as the two answers a checkbox could give: ticked is L{ALWAYS} and
	cleared is L{NEVER}, because both were answers about this add-on rather than about NVDA
	and neither meant "do whatever NVDA does". Failing both, the default, which is to follow.

	Asked one profile at a time, most specific first, and that is the whole subtlety here.
	The obvious version asked `isSet`, which reports whether a key is stored in *any* active
	profile rather than in the one whose answer counts. Two keys carry one question, so that
	is not a detail: a reader with the new setting answered in their base configuration and
	the old checkbox answered in a profile triggered by one application got the base answer in
	that application, where the whole point of an application profile is that its answer wins.
	Walking the profiles restores the ordinary rule — the most specific profile that answered
	either form of the question decides — and it keeps the distinction the old code was
	reaching for, since a profile that never stored either key does not answer.

	Nothing is written back. A migration that rewrites the configuration has to choose a
	profile to write to, and an answer given in a profile triggered by one application is an
	answer about that application; reading down the profiles asks the question where the
	answer was given.

	:param displayKey: the display to read, or None for the current one.
	:param key: the new key, which must be one of L{LEGACY_FOLLOWING}.
	"""
	section = getDisplayConfig(displayKey)
	legacy = LEGACY_FOLLOWING[key]
	try:
		for profile in reversed(_profilesBehind(section)):
			if profile is None:
				continue
			if key in profile:
				return str(profile[key] or FOLLOW_NVDA)
			if legacy in profile:
				return ALWAYS if profile[legacy] else NEVER
	except Exception:
		# A section that cannot say what was stored in it is one to read plainly.
		log.debugWarning(f"Could not ask which profile answered {key}", exc_info=True)
	return str(section[key] or FOLLOW_NVDA)


def _profilesBehind(section) -> list:
	""":return: the profiles an aggregated section is made of, least specific first.

	`AggregatedSection.profiles` is how NVDA holds them, and how its own reads resolve: the
	value comes from the last profile that has the key. A section with no such list stands for
	itself, which is what a settings dialog hands about and what a test builds.
	"""
	profiles = getattr(section, "profiles", None)
	if not profiles:
		return [section]
	return list(profiles)


def _following(setting, whatNvdaWasTold) -> bool:
	""":return: what a three state setting comes to, asking NVDA only where it defers.

	:param setting: the stored value, one of L{FOLLOWING}.
	:param whatNvdaWasTold: called for the answer when the setting defers.
	"""
	if setting == ALWAYS:
		return True
	if setting == NEVER:
		return False
	return whatNvdaWasTold()


# What NVDA itself was told.
#
# NVDA's Document Formatting settings are not only about browse mode: an application module
# reads them for its own objects too, which is how turning table reporting off in Outlook
# stops "From" and "Received" being announced before every message in the list. They are the
# reader's answer to a question this add-on asks again in a different shape, so where the
# question is the same the answer is taken rather than asked for twice.
#
# The shape is not the same, and that is the point of following rather than obeying. NVDA's
# settings decide what is *said with each thing you touch*, and repetition is most of what a
# reader turns off. A spatial layout does not repeat: a header row is drawn once at the top
# and a key column once at the left, however many rows are under them. So each of these is
# read as "does the reader want this kind of header at all", and a reader who wants it in
# braille having turned it off for speech says so per display. See L{FOLLOWING}.


def _formatting(name: str, default):
	""":return: one of NVDA's document formatting settings, or a default if it cannot be read.

	Read on each call rather than cached. It is a dictionary lookup, the reader can change it
	from the Document Formatting dialog or from NVDA's own cycling commands at any moment,
	and a table already laid out is asked again on its next build.
	"""
	try:
		return config.conf["documentFormatting"][name]
	except Exception:
		log.debugWarning(f"Could not read NVDA's {name} setting", exc_info=True)
		return default


def wantsColumnHeaders() -> bool:
	""":return: whether NVDA was told to report the headers of a table's columns.

	`reportTableHeaders` is one setting with two axes, and this add-on has one feature for
	each of them: the pinned header row is the column axis, the repeated key column is the
	row axis. See L{shouldPinTableHeaders}.
	"""
	return _formatting("reportTableHeaders", ReportTableHeaders.ROWS_AND_COLUMNS.value) in (
		ReportTableHeaders.ROWS_AND_COLUMNS.value,
		ReportTableHeaders.COLUMNS.value,
	)


def wantsRowHeaders() -> bool:
	""":return: whether NVDA was told to report the headers of a table's rows.

	See L{shouldPinKeyColumn}, which is what a row header is in a spatial layout.
	"""
	return _formatting("reportTableHeaders", ReportTableHeaders.ROWS_AND_COLUMNS.value) in (
		ReportTableHeaders.ROWS_AND_COLUMNS.value,
		ReportTableHeaders.ROWS.value,
	)


def wantsTables() -> bool:
	""":return: whether NVDA was told to report tables at all.

	What it decides here is whether a table is *offered* as a table — a layout this add-on
	proposes on its own account, and anything it says about being in one. It does not decide
	whether the reader's own command to lay a table out in columns works: NVDA draws the same
	line, and `documentBase._tableMovementScriptHelper` draws it in code by copying the
	format configuration and forcing `reportTables` on before it speaks a cell, because the
	reader pressing a table navigation key has asked.
	"""
	return bool(_formatting("reportTables", True))


def wantsCellCoordinates() -> bool:
	""":return: whether NVDA was told to report which cell of a table the reader is in.

	The coordinates a page turn reports — "columns five to nine of twenty-nine" — and any
	cell position said rather than shown. What is *drawn* is not a coordinate: a column under
	the reader's finger is its own answer to where it is, which is the whole argument for
	laying a table out spatially, and it is not suppressed by this.
	"""
	return bool(_formatting("reportTableCellCoords", True))


def wantsLayoutTables() -> bool:
	""":return: whether NVDA was told to treat tables used for layout as tables.

	Off by default in NVDA, and a table used to arrange a page has no columns worth laying
	out: its cells are a banner, a sidebar and an article. A reader who has told NVDA to
	ignore them has said what they want here too.
	"""
	return bool(_formatting("includeLayoutTables", False))


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
