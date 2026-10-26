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

Following NVDA's Document Formatting setting is the default. The other two exist because a
reader may turn headers off for speech, to avoid repetition, and still want one header row
in braille, or the reverse on a short display."""

LEGACY_FOLLOWING = {
	"flowTableHeadersMode": "flowTableHeaders",
	"flowTablePinKeyMode": "flowTablePinKey",
}
"""The three state settings that used to be checkboxes, and the key each was stored under.

A new key beside each old boolean, since `configobj` would replace an old `False` that fails
the new validation with the default. The old key stays in the specification, or `configobj`
discards it, and is read only where the new one is unanswered.
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

A flow is turned on for a display and then per kind of content. Adding a kind means adding
its name here, its default below, and its label in `settingsPanel`.
"""

FLOW_MODE_DEFAULTS: dict[str, bool] = {"browseMode": True, "objects": False, "editableText": False}
"""Whether each kind of content flows when a flow is turned on at all.

Browse mode on; objects and standalone editable text off, so dialogs and editors change
only when the reader asks.
"""


def flowModeKey(mode: str) -> str:
	""":return: the configuration key holding whether a flow is used for one kind of content.

	One key per kind, since a profile overrides single values.
	"""
	return f"flow{mode[0].upper()}{mode[1:]}"


configSpec = {
	"tableLayouts": 'string(default="")',
	"tableArrows": "boolean(default=False)",
	"keyLayerDevices": {
		"__many__": {
			"layers": 'string(default="")',
		},
	},
	"displays": {
		"__many__": {
			"segmentsEnabled": "boolean(default=True)",
			"segmentCount": f"integer(default=1, min=1, max={MAX_UI_SEGMENTS})",
			"segmentSizes": "int_list(default=list())",
			"focusSegment": "integer(default=-1, min=-1)",
			"messageSegment": "integer(default=-1, min=-1)",
			"reverseScrollBtns": "boolean(default=False)",
			"reverseScrollBtnsMigrated": "boolean(default=False)",
			"drawGlyphs": "boolean(default=False)",
			"appLayouts": "boolean(default=False)",
			"appLayoutSpread": "boolean(default=False)",
			"flowEnabled": "boolean(default=False)",
			"flowRows": f"integer(default=0, min=0, max={MAX_FLOW_ROWS})",
			"flowDisplay": 'string(default="")',
			"flowGroundOnQuickNav": "boolean(default=True)",
			"flowWriteByParagraph": "boolean(default=True)",
			"flowUnwrapLines": "boolean(default=False)",
			"flowIndentStyle": f'option({INDENT_STYLE_OPTIONS}, default="{DEFAULT_INDENT_STYLE}")',
			"flowLineFocus": "boolean(default=True)",
			"flowTableRowHeight": f"integer(default={flowTable.DEFAULT_MAX_ROWS}, min=1, max={flowTable.MAX_TABLE_ROWS})",
			"flowTableTruncate": "boolean(default=False)",
			"flowTablePinKeyMode": f'option({FOLLOWING_OPTIONS}, default="{FOLLOW_NVDA}")',
			"flowTablePinKey": "boolean(default=True)",
			"flowLiveSeconds": "integer(default=0, min=0, max=60)",
			"flowLiveUpdates": "boolean(default=True)",
			"flowScrollToNewContent": "boolean(default=True)",
			"flowCaretByDisplay": "boolean(default=False)",
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

- `tableArrows`: whether the plain arrow keys, home and end move by row and cell inside a
	browse mode table. Off by default, since it changes NVDA's own keys. Not per display. See
	`tableArrows`.

- `segmentsEnabled`: whether to divide the display at all. Turning it off overrides the
	layout below without disturbing it, so that turning it back on restores the arrangement
	rather than requiring it to be typed again.
- `segmentCount`: how many segments to divide the display into. Used when
	`segmentSizes` is empty, which is the usual case.
- `segmentSizes`: explicit segment sizes, in rows on a multi row display and in cells on
	a single row display. Empty means divide evenly into `segmentCount`.
- `focusSegment`: which segment tracks the system focus. -1 means the last. Not bounded by
	`MAX_UI_SEGMENTS`, since a composite numbers across all its displays; `views` checks it.
- `messageSegment`: which segment NVDA's flash messages appear in. -1 means the one
	following the focus.
- `reverseScrollBtns`: swap the panning keys. Per physical display; see `panning`.
- `reverseScrollBtnsMigrated`: bookkeeping, and only on a composite display's own section.
	Set once the value above has been carried onto the displays behind it. See
	L{migrateReverseScrollButtons}.
- `drawGlyphs`: whether the roles and states NVDA writes as short words are drawn as pin
	shapes, on a display that can. Off by default. Applies to everything shown.
- `appLayouts`: whether an app module may arrange the part of this display no pin holds.
	Off by default. See `appLayout`.
- `appLayoutSpread`: whether an app layout spreads across several displays, the focus
	alone on its display. Off by default. See `appLayout.planFor`.
- `flowEnabled`: whether a band of this display reads content as one flowing document.
- `flow<Mode>`: one per entry in `FLOW_MODES`, whether that kind of content flows.
- `flowRows`: how many rows the band takes, counted from the top of the display it is on.
	0, the default, means all of them.
- `flowDisplay`: the driver name of the physical display to put the band on, when several
	are driven as one. Empty, the default, means the tallest of them.
- `flowGroundOnQuickNav`: whether a browse mode jump by structure re-grounds the band.
- `flowWriteByParagraph`: whether a multi line edit being written in is cut into blocks by
	paragraph rather than by the reader's own read by paragraph setting.
- `flowUnwrapLines`: whether every line is drawn on one row and panned across, for code.
	Off by default. See `FlowBand.unwrapLines`.
- `flowIndentStyle`: how one level of depth is drawn, for content that has any.
- `flowLineFocus`: whether the row the focus is on is marked at the left margin where its
	indent has room for the mark.
- `flowTableRowHeight`: how many rows of the band one row of a table may use.
- `flowTableTruncate`: whether a cell too long for its column is cut rather than wrapped.
- `flowTablePinKeyMode`: whether the first column is repeated at the left of every later page.
	One of `FOLLOWING`; following means NVDA's `reportTableHeaders` asking for row headers.
- `flowTablePinKey`: the old checkbox form of the above. See `LEGACY_FOLLOWING`.
- `flowLiveSeconds`: how often the band reads its content again on a timer, over and above
	reading it when the page says it changed. Zero waits to be told.
- `flowLiveUpdates`: whether the band follows a page that changes under it at all.
- `flowScrollToNewContent`: whether content arriving at the end of a pinned object scrolls
	onto the display, while the reader is at that end.
- `flowCaretByDisplay`: whether a caret that moves off the band brings a whole display of what
	lies beyond it, rather than one row. Off by default.
- `flowTableHeadersMode`: whether a table's header row is held on the top row of the band.
	One of `FOLLOWING`; following means NVDA's `reportTableHeaders` asking for column headers.
- `flowTableHeaders`: the old checkbox form of the above. See `LEGACY_FOLLOWING`.

All of it is read through `config.conf`, so all of it can differ per profile.
"""


def flowIndentStyle(displayKey: Optional[str] = None) -> str:
	""":return: how one level of depth is drawn on this display.

	See `flowIndent.INDENT_STYLES`. An unreadable setting reads as the default, as with every
	accessor here, so it cannot stop a display being drawn.
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


def tableLayouts() -> str:
	""":return: the saved table layouts, as the JSON they are stored in.

	Not per display: layouts are choices about a table, and widths are worked out on the
	display it lands on. One string, since the keys are URLs and window classes, which
	`configobj` keys cannot be. See `flowTableLayouts`. Always in the base configuration, so
	one profile cannot hide the rest; layouts left in a profile by older versions are not read.
	"""
	try:
		section = config.conf.profiles[0].get(CONFIG_SECTION)
		if not section or "tableLayouts" not in section:
			return ""
		return str(section["tableLayouts"] or "")
	except Exception:
		log.debugWarning("Could not read the saved table layouts", exc_info=True)
		return ""


def setTableLayouts(said: str) -> None:
	"""Store the saved table layouts, in the base configuration. See `tableLayouts`.

	The base configuration is always written when NVDA saves, so nothing has to be marked as changed.

	:param said: the JSON to store. See `flowTableLayouts`.
	"""
	base = config.conf.profiles[0]
	if CONFIG_SECTION not in base:
		base[CONFIG_SECTION] = {}
	base[CONFIG_SECTION]["tableLayouts"] = said
	_forgetCachedTableLayouts()


def _forgetCachedTableLayouts() -> None:
	"""Keep NVDA's own view of the setting in step with a write that went round it."""
	try:
		config.conf[CONFIG_SECTION]._cache.pop("tableLayouts", None)
	except Exception:
		pass


KEY_LAYER_SPEC = {"layers": 'string(default="")'}
"""The specification of one device's layered keys section. See L{keyLayerText}."""


def _keyLayerSection(device: str):
	devices = config.conf[CONFIG_SECTION]["keyLayerDevices"]
	if not devices.isSet(device):
		devices[device] = {}
	section = devices[device]
	section.spec.update(KEY_LAYER_SPEC)
	return section


def keyLayerText(device: str) -> str:
	""":return: one device's layered keys, as the JSON `keyLayers.LayerSet` stores them.

	Keyed by driver name, not display key, since keys are hardware and the Monarch's geometry
	changes with its pitch. The keyboard is the device `keyboard`. One JSON string per device,
	as `tableLayouts` gives, so a profile can change one device's layers alone.

	:param device: a braille display's driver name, or `keyboard`.
	:return: the stored text, or an empty string when there is none or it cannot be read.
	"""
	try:
		return str(_keyLayerSection(device)["layers"] or "")
	except Exception:
		log.debugWarning(f"Could not read the layered keys for {device}", exc_info=True)
		return ""


def keyLayerTexts(device: str) -> list[str]:
	""":return: one device's layered keys as each configuration profile in force stores them: the
	normal configuration's first, then each profile in the order NVDA activated them, an empty
	string where a profile stores none.

	Profile by profile, since `keyLayers.stacked` stacks them, where `config.conf` gives only
	the most specific.
	"""
	texts = []
	try:
		profiles = list(config.conf.profiles)
	except Exception:
		log.debugWarning("Could not list the configuration profiles", exc_info=True)
		return [keyLayerText(device)]
	for profile in profiles:
		try:
			section = (profile or {}).get(CONFIG_SECTION) or {}
			devices = section.get("keyLayerDevices") or {}
			texts.append(str((devices.get(device) or {}).get("layers") or ""))
		except Exception:
			log.debugWarning(f"Could not read a profile's layered keys for {device}", exc_info=True)
			texts.append("")
	return texts or [""]


def keyLayersGoToProfile() -> bool:
	""":return: whether a save of layered keys goes to a configuration profile rather than the normal
	configuration: NVDA writes changed settings to the most recently activated profile."""
	try:
		return len(config.conf.profiles) > 1
	except Exception:
		return False


def setKeyLayerText(device: str, text: str) -> None:
	"""Store one device's layered keys, in the profile in force.

	:param device: a braille display's driver name, or `keyboard`.
	:param text: from `keyLayers.LayerSet.toText`.
	"""
	_keyLayerSection(device)["layers"] = text


def keyLayerDevices() -> list[str]:
	""":return: every device with a layered keys section.

	Iterated rather than asked for `keys()`, which NVDA's `AggregatedSection` does not have; its
	iteration also yields the specification's `__many__`, which names no device.
	"""
	try:
		return sorted(key for key in config.conf[CONFIG_SECTION]["keyLayerDevices"] if key != "__many__")
	except Exception:
		log.debugWarning("Could not list the devices with layered keys", exc_info=True)
		return []


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

	NVDA does not create a `__many__` subsection on demand, so it is created here, as
	`autoSettingsUtils.autoSettings` does, with the specification attached so its settings
	have defaults.

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

	A master switch over the user's layout, so it can be put aside without retyping. Views
	and panels activated by code are not affected.
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

	Written through `config.conf`, to the profile in force.

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

	The setting moved from the composite to each physical display. Copied to every member
	without its own value; `AggregatedSection.isSet` tells an explicit `False` from unset.
	Done once and marked last, so a failed attempt is retried.

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

	Written through `config.conf`, to the profile in force.

	:param enabled: whether to read a band of this display as a flowing document.
	:param displayKey: the display to store against, or None for the current one.
	"""
	getDisplayConfig(displayKey)["flowEnabled"] = bool(enabled)


def isFlowEnabledFor(mode: str, displayKey: str | None = None) -> bool:
	""":return: whether one kind of content should be read as a flow.

	Both the display's switch and this kind's must be on.

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

	Only when some kind of content is enabled, so a band never takes rows for nothing.
	"""
	return any(isFlowEnabledFor(mode, displayKey) for mode in FLOW_MODES)


def getFlowRows(displayKey: str | None = None) -> int:
	""":return: how many rows the band takes, 0 meaning all of the display it is on.

	From the top of that display; a band taller than it is trimmed rather than refused.
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

	On, a structural quick navigation jump puts its target at the top of the band; off, the
	window only tracks the cursor. Which moves count is `flowQuickNav.GROUNDING_TYPES`.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowGroundOnQuickNav"])
	except Exception:
		log.debugWarning("Could not read flowGroundOnQuickNav", exc_info=True)
		return True


def shouldDrawGlyphs(displayKey: str | None = None) -> bool:
	""":return: whether roles and states are drawn as shapes rather than written as words.

	Applies to everything the display shows, except table row cells, whose positions are
	packed. Off by default, since the shapes must be learned. See `glyphs` and `glyphFlow`.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["drawGlyphs"])
	except Exception:
		log.debugWarning("Could not read drawGlyphs", exc_info=True)
		return False


def shouldAllowAppLayouts(displayKey: str | None = None) -> bool:
	""":return: whether an app module may arrange the free part of this display.

	A display setting. Off by default and when unreadable, since it hands the arrangement to
	another add-on's code. See `appLayout` and docs/design/app-layouts-plan.md.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["appLayouts"])
	except Exception:
		log.debugWarning("Could not read appLayouts", exc_info=True)
		return False


def shouldSpreadAppLayouts(displayKey: str | None = None) -> bool:
	""":return: whether an app layout is spread across the displays it reaches. See `appLayout.planFor`."""
	try:
		return bool(getDisplayConfig(displayKey)["appLayoutSpread"])
	except Exception:
		log.debugWarning("Could not read appLayoutSpread", exc_info=True)
		return False


def setSpreadAppLayouts(spread: bool, displayKey: str | None = None) -> None:
	"""Store whether app layouts are spread across displays.

	Written through `config.conf`, so the answer belongs to the profile in force: pressed in
	Teams, with a Teams profile, it is Teams' answer and nobody else's.
	"""
	getDisplayConfig(displayKey)["appLayoutSpread"] = bool(spread)


def shouldWriteByParagraph(displayKey: str | None = None) -> bool:
	""":return: whether a multi line edit being written in is read a paragraph at a time.

	On by default, whatever NVDA's read by paragraph setting says; see
	`flowBuild.readingUnitFor`. Off, an edit is cut as a page is, by `flowBuild.readingUnit`.
	Reading anywhere else follows the reader's own setting.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowWriteByParagraph"])
	except Exception:
		log.debugWarning("Could not read flowWriteByParagraph", exc_info=True)
		return True


def shouldUnwrapLines(displayKey: str | None = None) -> bool:
	""":return: whether the band draws each line on one row and pans across rather than wrapping.

	What the settings and the profile in force say. The reader can turn it over for the time being
	without changing either; what the band actually does is `FlowBand.unwrapLines`.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowUnwrapLines"])
	except Exception:
		log.debugWarning("Could not read flowUnwrapLines", exc_info=True)
		return False


def shouldScrollToNewContent(displayKey: str | None = None) -> bool:
	""":return: whether new content at the end of a pinned object scrolls onto the display.

	For following a chat. On by default, so new content at a full display scrolls on, like a
	terminal; off, it waits below until the reader pans. Never while the reader has panned
	back into the history. See `FlowController.reconsiderEnd`.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowScrollToNewContent"])
	except Exception:
		log.debugWarning("Could not read flowScrollToNewContent", exc_info=True)
		return True


def shouldScrollCaretByDisplay(displayKey: str | None = None) -> bool:
	""":return: whether following the caret off the band scrolls a whole display.

	Off by default: the band moves by one row. On, the caret's row goes to the top and the
	band fills after it, as the Monarch's own software scrolls. Wherever the band follows a
	caret or focus.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowCaretByDisplay"])
	except Exception:
		log.debugWarning("Could not read flowCaretByDisplay", exc_info=True)
		return False


def shouldMarkLineFocus(displayKey: str | None = None) -> bool:
	""":return: whether the row the focus is on is marked at the left margin.

	On by default, since the mark is in the same column on every row and can be found in one
	pass of the hand. Drawn only where the indent has room, so only in indented content.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowLineFocus"])
	except Exception:
		log.debugWarning("Could not read flowLineFocus", exc_info=True)
		return True


def tableRowHeight(displayKey: str | None = None) -> int:
	""":return: how many rows of the band one row of a table may use.

	One by default, so the most rows sit under one hand. See `flowTable.planFor` and
	`flowTable.MAX_TABLE_ROWS`.
	"""
	try:
		wanted = int(getDisplayConfig(displayKey)["flowTableRowHeight"])
	except Exception:
		log.debugWarning("Could not read flowTableRowHeight", exc_info=True)
		return flowTable.DEFAULT_MAX_ROWS
	return max(1, min(wanted, flowTable.MAX_TABLE_ROWS))


def shouldTruncateTableCells(displayKey: str | None = None) -> bool:
	""":return: whether a cell too long for its column is cut rather than wrapped.

	Off by default. See `flowTable.OVERFLOW_STYLES`.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowTableTruncate"])
	except Exception:
		log.debugWarning("Could not read flowTableTruncate", exc_info=True)
		return False


def shouldPinKeyColumn(displayKey: str | None = None) -> bool:
	""":return: whether the first column is repeated at the left of every later page.

	Only matters for a table wider than one page. Follows NVDA's `reportTableHeaders` row
	headers by default. See `wantsRowHeaders` and `flowTable.ColumnPlan.keyColumn`.
	"""
	try:
		return _following(storedFollowing(displayKey, "flowTablePinKeyMode"), wantsRowHeaders)
	except Exception:
		log.debugWarning("Could not read flowTablePinKeyMode", exc_info=True)
		# The default, so a setting that cannot be read behaves as one that was never set.
		return wantsRowHeaders()


def shouldFollowLiveContent(displayKey: str | None = None) -> bool:
	""":return: whether the band re-reads its content when the page changes under it.

	On by default, since NVDA refreshes only the caret's line. An escape hatch; see
	`liveReadSeconds` and `FlowBand.documentChanged`.
	"""
	try:
		return bool(getDisplayConfig(displayKey)["flowLiveUpdates"])
	except Exception:
		log.debugWarning("Could not read flowLiveUpdates", exc_info=True)
		return True


def liveReadSeconds(displayKey: str | None = None) -> int:
	""":return: how often a table laid out in columns is read again on a timer, in seconds.

	Zero by default, meaning wait to be told: NVDA's virtual buffer reports changes, and the
	band answers those. A number adds a timer, for pages that change silently, and
	`FlowBand._pollMillis` falls back to one without the patch. See `FlowBand.documentChanged`
	and `patches._handleUpdateTellingTheBand`.
	"""
	try:
		wanted = int(getDisplayConfig(displayKey)["flowLiveSeconds"])
	except Exception:
		log.debugWarning("Could not read flowLiveSeconds", exc_info=True)
		return 0
	return max(0, min(wanted, 60))


def shouldPinTableHeaders(displayKey: str | None = None) -> bool:
	""":return: whether a table's header row stays on the top row of the band.

	Costs a row, and shows the headers wherever in the table the reader is. Follows NVDA's
	`reportTableHeaders` column headers by default. See `wantsColumnHeaders`.
	"""
	try:
		return _following(storedFollowing(displayKey, "flowTableHeadersMode"), wantsColumnHeaders)
	except Exception:
		log.debugWarning("Could not read flowTableHeadersMode", exc_info=True)
		# The default, so a setting that cannot be read behaves as one that was never set.
		return wantsColumnHeaders()


def storedFollowing(displayKey, key: str) -> str:
	""":return: what a display was told about one three state setting, one of L{FOLLOWING}.

	The new value where answered, else the old checkbox (ticked is `ALWAYS`, cleared is
	`NEVER`), else following. Walked profile by profile, most specific first, since `isSet`
	looks across all profiles and two keys carry one question. Nothing is written back.

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

	From `AggregatedSection.profiles`. A section without one, as in a dialog or a test, stands
	for itself.
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
# NVDA's Document Formatting settings, read as "does the reader want this kind of header at
# all", since a spatial layout shows a header once rather than repeating it. A reader can
# differ per display. See `FOLLOWING`.


def _formatting(name: str, default):
	""":return: one of NVDA's document formatting settings, or a default if it cannot be read.

	Read on each call, since the reader can change it at any moment.
	"""
	try:
		return config.conf["documentFormatting"][name]
	except Exception:
		log.debugWarning(f"Could not read NVDA's {name} setting", exc_info=True)
		return default


def wantsColumnHeaders() -> bool:
	""":return: whether NVDA was told to report the headers of a table's columns.

	The column axis of `reportTableHeaders`, used for the pinned header row. See
	`shouldPinTableHeaders`.
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


def shouldArrowsMoveByRow() -> bool:
	""":return: whether the arrow keys move by row and cell inside a browse mode table.

	Off by default and when unreadable, since it changes NVDA's own keys. See `tableArrows`.
	"""
	try:
		return bool(config.conf[CONFIG_SECTION]["tableArrows"])
	except Exception:
		log.debugWarning("Could not read tableArrows", exc_info=True)
		return False


def setArrowsMoveByRow(enabled: bool) -> None:
	"""Store whether the arrow keys move by row and cell. See L{shouldArrowsMoveByRow}."""
	config.conf[CONFIG_SECTION]["tableArrows"] = bool(enabled)


def wantsTables() -> bool:
	""":return: whether NVDA was told to report tables at all.

	Decides whether a table is offered as one, not whether the reader's own column command
	works, as NVDA's `documentBase._tableMovementScriptHelper` also forces it on.
	"""
	return bool(_formatting("reportTables", True))


def wantsCellCoordinates() -> bool:
	""":return: whether NVDA was told to report which cell of a table the reader is in.

	For positions said, such as a page turn's; what is drawn is not affected.
	"""
	return bool(_formatting("reportTableCellCoords", True))


def wantsLayoutTables() -> bool:
	""":return: whether NVDA was told to treat tables used for layout as tables.

	Off by default in NVDA; a layout table has no columns worth laying out.
	"""
	return bool(_formatting("includeLayoutTables", False))


def isSpeechOutputMode() -> bool:
	""":return: whether NVDA is showing speech on the display rather than following cursors.

	NVDA's own braille mode. In it NVDA handles no focus, caret or review moves, so nothing
	would refresh or remove content drawn by this add-on; every path that writes its own
	content asks this first, such as `GlobalPlugin.refreshMonitors` and `patches`.
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
