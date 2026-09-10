# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Enough of NVDA to import and drive the add-on's upper layers.

`layout`, `panels` and `routing` import nothing from NVDA and could be tested by putting
the add-on directory on the path, as the older tests do. `views`, `segments` and
`container` cannot: they reach for the log, the configuration, and `BrailleBuffer` itself.

Rather than test those layers only inside a running screen reader, this module registers
stand-ins for the handful of NVDA modules they touch, plus a stand-in for the add-on
package so that relative imports resolve. `BrailleBuffer` is reimplemented here in the
crudest way that still answers the container honestly: regions concatenate, the window is
a slice, and panning moves it by one display.

Call L{installStubs} before importing anything under `brlMultiline`. It is idempotent, so
every test module may call it.
"""

import dataclasses
import enum
import os
import re
import sys
import types

ADDON_DIR = os.path.abspath(
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

DRIVER_DIR = os.path.abspath(
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineVirtual",
	),
)

PACKAGE = "brlMultiline"


def _specDefault(spec: str):
	"""Read the default out of a configobj specification string.

	Crude beside NVDA's validator, which the stub has no access to, but the add-on's
	settings are only integers, booleans and integer lists.

	:param spec: a specification such as `integer(default=1, min=1, max=8)`.
	:return: the default the validator would produce.
	"""
	match = re.search(r"default=(list\(\)|[^,)]+)", spec)
	if not match:
		raise KeyError(spec)
	text = match.group(1).strip()
	if text == "list()":
		return []
	if text in ("True", "False"):
		return text == "True"
	if text.lstrip("-").isdigit():
		return int(text)
	return text.strip("\"'")


class DisplaySection(dict):
	"""One display's settings, as NVDA's configuration presents them.

	Carries a `spec` because a section NVDA creates on the fly is given an empty one, and a
	setting only falls back to its default once a specification has been attached. A key
	with neither a stored value nor a specification raises `KeyError`, as in NVDA.
	"""

	def __init__(self, *args, fallback=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.spec = {}
		self.fallback = fallback
		"""Where a setting this display has none of its own comes from. See L{DisplaysSection}."""
		self.profiles = None
		"""The configuration profiles behind this section, least specific first.

		What `AggregatedSection.profiles` is upstream, and None here for the ordinary test,
		which is not about profiles and wants one dictionary. A test that sets it gets the
		upstream reading rules: the last profile holding a key answers for it, and `isSet`
		says whether any of them stored it at all — the two being different is the whole of
		what a setting read through two keys can get wrong.
		"""

	def __missing__(self, key):
		if self.profiles is not None:
			for profile in reversed(self.profiles):
				if profile is not None and key in profile:
					return profile[key]
		if self.fallback is not None and key in self.fallback:
			return self.fallback[key]
		return _specDefault(self.spec[key])

	def isSet(self, key) -> bool:
		"""Whether this display has a value of its own, as `AggregatedSection.isSet` reports.

		Upstream that means the key is stored in some profile, as against merely having a
		default — which is the distinction the whole method exists for. Here the fallback
		deliberately does not count: it stands in for what a display reports before anything
		asked it, which upstream is the specification's default.
		"""
		if self.profiles is not None:
			return key in self or any(
				profile is not None and key in profile for profile in self.profiles
			)
		return key in self


class DisplaysSection(dict):
	"""The `__many__` section holding the settings of each display.

	NVDA does not materialise one of these subsections on demand: reading a display that
	has never been written raises `KeyError`, so anything wanting one has to create it
	first, and the stub insists on that too.

	Each display gets a section of its own, falling back to the one stub section L{CONFIG} for
	anything nothing has written to it. So the ordinary test still sets a value in one
	dictionary and has every display see it, while a test about *which* display a value lands
	under can tell the difference — which the settings that are per physical display, and the
	migration that moved one of them there, are entirely about.
	"""

	def isSet(self, key) -> bool:
		return key in self

	def __setitem__(self, key, value):
		if not isinstance(value, dict):
			raise ValueError("Value must be a section")
		super().__setitem__(key, DisplaySection(fallback=CONFIG))


CONFIG = DisplaySection(
	{
		"segmentsEnabled": True,
		"segmentCount": 4,
		"segmentSizes": [],
		"focusSegment": -1,
		"reverseScrollBtns": False,
		"showDocumentLines": False,
		"flowEnabled": False,
		"flowBrowseMode": True,
		"flowObjects": False,
		"flowEditableText": False,
		"flowRows": 0,
		"flowDisplay": "",
		"flowGroundOnQuickNav": True,
		"flowScrollToNewContent": True,
		"flowWriteByParagraph": True,
		"flowIndentStyle": "twoSpaces",
		"tableLayouts": "",
	},
)
"""The stub configuration the fake `bmConfig` reads. Tests mutate this directly.

The flow settings are here at their real defaults, off, so that a test wanting a flow turns
it on as a reader would. That is not ceremony: whether the band is claimed at all is now a
setting, and a stub that quietly had it on could not show a test that it is being read.
"""

BAND_CONFIG: dict[str, "DisplaySection"] = {}
"""Settings belonging to one named display, for tests that need them to differ.

Everything reads L{CONFIG} unless a test puts a section here under the display key it is
asking about. That keeps the common case a single dictionary, while letting the tests of a
composite display give each of the displays behind it a layout of its own — which is the
whole point of that view, and cannot be shown by a stub with one set of settings.
"""


def setBandConfig(displayKey: str, **values) -> None:
	"""Give one display settings of its own, filling the rest in from the defaults.

	:param displayKey: the display to store against.
	:param values: the settings that differ.
	"""
	section = DisplaySection(
		{
			"segmentsEnabled": True,
			"segmentCount": 1,
			"segmentSizes": [],
			"focusSegment": -1,
			"reverseScrollBtns": False,
			"showDocumentLines": False,
			"flowEnabled": False,
			"flowBrowseMode": True,
			"flowObjects": False,
			"flowEditableText": False,
			"flowRows": 0,
			"flowDisplay": "",
			"flowGroundOnQuickNav": True,
			"flowScrollToNewContent": True,
			"flowWriteByParagraph": True,
			"flowIndentStyle": "twoSpaces",
		},
	)
	section.update(values)
	BAND_CONFIG[displayKey] = section


def _sectionFor(displayKey):
	""":return: the settings a display key resolves to, which is L{CONFIG} unless overridden."""
	if displayKey is not None and displayKey in BAND_CONFIG:
		return BAND_CONFIG[displayKey]
	return CONFIG


realBmConfig: dict = {}
"""The `bmConfig` functions L{installStubs} replaces, keyed on name, before it replaces them.

Everything above `bmConfig` is tested against L{CONFIG} rather than against NVDA's
configuration, so the accessors are redirected. Testing `bmConfig` itself means calling the
real ones, which is what this is for.
"""

FOLLOW_CURSORS_MODE = "followCursors"
SPEECH_OUTPUT_MODE = "speechOutput"

BRAILLE_CONFIG = {
	"mode": FOLLOW_CURSORS_MODE,
	"focusContextPresentation": "changedContext",
	"expandAtCursor": True,
}
"""NVDA's own braille settings, which the add-on reads through the real `bmConfig`.

Unlike L{CONFIG} these are not redirected, so a test changing the mode here changes what
`bmConfig.isSpeechOutputMode` answers. Use L{setSpeechOutputMode} rather than writing to it.
"""


FORMAT_CONFIG = {
	"reportTables": True,
	"includeLayoutTables": False,
	"reportTableHeaders": 1,
	"reportTableCellCoords": True,
}
"""NVDA's own document formatting settings, as much of them as the add-on reads.

The defaults are NVDA's own, from its `configSpec`, and `reportTableHeaders` is the integer
`ReportTableHeaders.ROWS_AND_COLUMNS`. Like L{BRAILLE_CONFIG} these are not redirected: a
test writing here changes what `bmConfig.wantsColumnHeaders` and its neighbours answer.
"""

ReportTableHeaders = types.SimpleNamespace(
	OFF=types.SimpleNamespace(value=0),
	ROWS_AND_COLUMNS=types.SimpleNamespace(value=1),
	ROWS=types.SimpleNamespace(value=2),
	COLUMNS=types.SimpleNamespace(value=3),
)
"""NVDA's `config.configFlags.ReportTableHeaders`, in what the add-on uses of it.

The values are NVDA's own and are what the configuration stores, so a test may write the
integer or the member's value and mean the same thing.
"""


def setSpeechOutputMode(enabled: bool) -> None:
	"""Put NVDA into or out of speech output braille mode, as the user's toggle does.

	Only half of what entering the mode looks like: NVDA also assigns a fresh list to
	`container.regions`, which empties every segment. A test entering the mode wants both.

	:param enabled: True for speech output, False to follow cursors.
	"""
	BRAILLE_CONFIG["mode"] = SPEECH_OUTPUT_MODE if enabled else FOLLOW_CURSORS_MODE


class FakeLog:
	"""Swallows log output, but records it so a test can assert something was reported."""

	def __init__(self):
		self.messages: list[tuple[str, str]] = []

	def _record(self, level, message):
		self.messages.append((level, str(message)))

	def debug(self, message, **kwargs):
		self._record("debug", message)

	def debugWarning(self, message, **kwargs):
		self._record("debugWarning", message)

	def info(self, message, **kwargs):
		self._record("info", message)

	def warning(self, message, **kwargs):
		self._record("warning", message)

	def error(self, message, **kwargs):
		self._record("error", message)


log = FakeLog()


class _AutoPropertyMeta(type):
	"""Turns `_get_X` and `_set_X` into a property, as NVDA's `AutoPropertyObject` does."""

	def __new__(mcls, name, bases, namespace):
		cls = super().__new__(mcls, name, bases, namespace)
		names = set()
		for klass in (cls, *bases):
			for attribute in dir(klass):
				if attribute.startswith(("_get_", "_set_")):
					names.add(attribute[5:])
		for prop in names:
			getter = getattr(cls, f"_get_{prop}", None)
			setter = getattr(cls, f"_set_{prop}", None)
			if getter or setter:
				setattr(cls, prop, property(getter, setter))
		return cls


class AutoPropertyObject(metaclass=_AutoPropertyMeta):
	pass


class ScriptableObject(AutoPropertyObject):
	"""NVDA's ScriptableObject, reduced to the lookup that matters outside it.

	A real class rather than `object`, because the virtual braille display inherits from it
	and calls `super().getScript` when no member claims a gesture — exactly as NVDA's own
	drivers do. Returning None for an unbound gesture is the behaviour being stood in for.
	"""

	_gestureMap: dict = {}

	def getScript(self, gesture):
		for identifier in getattr(gesture, "identifiers", ()):
			script = self._gestureMap.get(identifier)
			if script is not None:
				return script
		return None


class DisplayDimensions:
	def __init__(self, numRows, numCols):
		self.numRows = numRows
		self.numCols = numCols

	@property
	def displaySize(self):
		return self.numRows * self.numCols

	def __eq__(self, other):
		"""By value, as NVDA's is: it is a named tuple, and the handler compares one reading
		against the last to decide whether the display has changed size."""
		if not isinstance(other, DisplayDimensions):
			return NotImplemented
		return (self.numRows, self.numCols) == (other.numRows, other.numCols)

	def __hash__(self):
		return hash((self.numRows, self.numCols))

	def __repr__(self):
		return f"DisplayDimensions(numRows={self.numRows}, numCols={self.numCols})"


class WindowRowPositions:
	def __init__(self, start, end, showContinuationMark=False):
		self.start = start
		self.end = end
		self.showContinuationMark = showContinuationMark

	def __eq__(self, other):
		return (self.start, self.end, self.showContinuationMark) == (
			other.start,
			other.end,
			other.showContinuationMark,
		)

	def __repr__(self):
		return f"<row {self.start}:{self.end} mark={self.showContinuationMark}>"


class Region:
	"""A region whose braille is simply its text, one cell per character.

	The attributes below are the ones NVDA's own `braille.regions.base.Region` declares, and
	they are here in full rather than as the subset this add-on happens to read. A region
	goes into NVDA's buffer, and the buffer asks for all of them.
	"""

	hidePreviousRegions = False
	expandedAtCursor = False
	"""Whether the last translation asked liblouis to expand the word at the cursor.

	**What NVDA's `Region.update` decides, reproduced because it is decided there.** The
	setting is "expand to computer braille for the word at the cursor", and the region that
	answers "yes, at character zero" has its first word written out uncontracted. A region
	that must not do that has to have no cursor *before* it is translated; clearing one
	afterwards leaves the cells already wrong, and a stub that translated first could not tell
	the two apart. See `pinnedRegions.CursorOnlyWhereTheReaderIs`.
	"""

	cursorPos = None
	selectionStart = None
	selectionEnd = None
	rawTextTypeforms = None
	brailleCursorPos = None
	brailleSelectionStart = None
	brailleSelectionEnd = None

	def __init__(self, text=""):
		self.rawText = text
		self.brailleCells = [ord(character) & 0xFF for character in text]
		self.rawToBraillePos = list(range(len(text)))
		self.brailleToRawPos = list(range(len(text)))
		self.hidden = False
		self.obj = None
		# NVDA's own Region carries this, and `_doNewObject` reads and writes it.
		self.focusToHardLeft = False

	def update(self):
		# Read here and not afterwards, which is where NVDA reads it: the mode handed to
		# liblouis is decided from the cursor at the moment of translating. See
		# `expandedAtCursor`.
		self.expandedAtCursor = bool(BRAILLE_CONFIG.get("expandAtCursor")) and (
			self.cursorPos is not None
		)
		self.brailleCells = [ord(character) & 0xFF for character in self.rawText]
		# Rebuilt here as NVDA rebuilds them, so that a region re-read with different text
		# does not keep the map the text before it had.
		self.rawToBraillePos = list(range(len(self.rawText)))
		self.brailleToRawPos = list(range(len(self.rawText)))

	def routeTo(self, pos):
		self.routedTo = pos

	def __repr__(self):
		return f"<Region {self.rawText!r}>"


class TextRegion(Region):
	"""NVDA's plain text region: a string, translated, and nothing that reads a document.

	One cell per character here, as `Region` is, so a drawn table row reads back as the
	string it came from.
	"""


class BrailleLabel(enum.Enum):
	"""A stand-in for the `controlTypes` members NVDA's braille labels are keyed on.

	Only the name matters. `glyphFlow` reads NVDA's labels and asks each key what it is called,
	rather than importing `controlTypes` and naming the members itself, so that a reader running
	NVDA in another language still gets shapes over their own abbreviations.
	"""

	BUTTON = "BUTTON"
	TOGGLEBUTTON = "TOGGLEBUTTON"
	RADIOBUTTON = "RADIOBUTTON"
	EDITABLETEXT = "EDITABLETEXT"
	PASSWORDEDIT = "PASSWORDEDIT"
	COMBOBOX = "COMBOBOX"
	LINK = "LINK"
	LIST = "LIST"
	MENUITEM = "MENUITEM"
	TABLE = "TABLE"
	GRAPHIC = "GRAPHIC"
	PROGRESSBAR = "PROGRESSBAR"
	SEPARATOR = "SEPARATOR"
	CHECKBOX = "CHECKBOX"
	HEADING = "HEADING"
	CHECKED = "CHECKED"
	HALFCHECKED = "HALFCHECKED"
	ON = "ON"
	PRESSED = "PRESSED"
	HASPOPUP = "HASPOPUP"
	SELECTED = "SELECTED"


ROLE_LABELS = {
	BrailleLabel.BUTTON: "btn",
	BrailleLabel.TOGGLEBUTTON: "tgbtn",
	BrailleLabel.RADIOBUTTON: "rbtn",
	BrailleLabel.EDITABLETEXT: "edt",
	BrailleLabel.PASSWORDEDIT: "pwdedt",
	BrailleLabel.COMBOBOX: "cbo",
	BrailleLabel.LINK: "lnk",
	BrailleLabel.LIST: "lst",
	BrailleLabel.MENUITEM: "mnuitem",
	BrailleLabel.TABLE: "tbl",
	BrailleLabel.GRAPHIC: "gra",
	BrailleLabel.PROGRESSBAR: "prgbar",
	BrailleLabel.SEPARATOR: "⠤⠤⠤⠤⠤",
	BrailleLabel.CHECKBOX: "chk",
	BrailleLabel.HEADING: "hdng",
}
"""NVDA's `braille.labels.roleLabels`, as far as anything here reads it.

The strings are NVDA's own, copied rather than guessed: a checkbox is "chk" and a separator is
five cells of dashes. Roles with no shape are in the list on purpose, so that a test can say
that a word nobody wrote a glyph for is left alone.
"""

POSITIVE_STATE_LABELS = {
	BrailleLabel.SELECTED: "sel",
	BrailleLabel.PRESSED: "⢎⣿⡱",
	BrailleLabel.CHECKED: "⣏⣿⣹",
	BrailleLabel.HALFCHECKED: "⣏⣸⣹",
	BrailleLabel.ON: "⣏⣿⣹",
	BrailleLabel.HASPOPUP: "submnu",
}
"""NVDA's `braille.labels.positiveStateLabels`.

The states are written as literal braille pattern characters rather than as words, which is why
the vocabulary records those three cells rather than a wording to translate."""

NEGATIVE_STATE_LABELS = {
	BrailleLabel.SELECTED: "nsel",
	BrailleLabel.PRESSED: "⢎⣀⡱",
	BrailleLabel.CHECKED: "⣏⣀⣹",
	BrailleLabel.ON: "⣏⣀⣹",
}
"""NVDA's `braille.labels.negativeStateLabels`."""


TEXT_SEPARATOR = " "
"""NVDA's `braille.constants.TEXT_SEPARATOR`: what one thing on a braille line is parted from
the next by."""


class BrailleBuffer(AutoPropertyObject):
	"""The smallest buffer that answers everything the container asks of a segment.

	An `AutoPropertyObject` because NVDA's own `BrailleBuffer` is one, and the difference is
	not cosmetic. A subclass overriding `_get_windowBrailleCells` overrides nothing at all
	against a base that declares `windowBrailleCells` with `@property`: the parent's property
	wins, the override is never called, and a test of it passes while testing the stub. The
	buffer's geometry is defined here in `_get_` form for that reason.
	"""

	def __init__(self, handler):
		self.handler = handler
		self.regions = []
		self.rawText = ""
		self.brailleCells = []
		self.cursorPos = None
		self.windowStartPos = 0
		self.routedTo = None
		self.scrolled = None
		self._savedWindow = None

	def _get_visibleRegions(self):
		# `hidePreviousRegions` is read the way NVDA's own buffer reads it, off the *last*
		# region and before anything else. That detail is not decoration: a stand-in region
		# that carried the attributes this add-on happens to ask for and not the ones NVDA
		# does passed every test here and then raised inside a display update, because this
		# stub asked a different question from the real buffer. A stub that diverges from the
		# thing it stands in for hides exactly the bugs it exists to catch.
		if not self.regions:
			return []
		if self.regions[-1].hidePreviousRegions:
			return [self.regions[-1]]
		return [region for region in self.regions if not getattr(region, "hidden", False)]

	def clear(self):
		self.regions = []
		self.rawText = ""
		self.brailleCells = []
		self.cursorPos = None
		self.windowStartPos = 0

	def update(self):
		self.rawText = "".join(region.rawText for region in self.regions)
		self.brailleCells = [cell for region in self.regions for cell in region.brailleCells]

	def updateDisplay(self):
		if self is self.handler.buffer:
			self.handler.update()

	def _get_windowEndPos(self):
		return min(len(self.brailleCells), self.windowStartPos + self.handler.displaySize)

	def _set_windowEndPos(self, endPos):
		self.windowStartPos = max(0, endPos - self.handler.displaySize)

	def _calculateWindowRowBufferOffsets(self, pos):
		self._windowRowBufferOffsets = []

	def _isMidWordCut(self, end, bufferEnd):
		return False

	def _get_windowBrailleCells(self):
		return self.brailleCells[self.windowStartPos : self.windowEndPos]

	def _get_windowRawText(self):
		return self.rawText[self.windowStartPos : self.windowEndPos]

	def _get_cursorWindowPos(self):
		return self.cursorPos

	def focus(self, region):
		if region not in self.regions:
			raise LookupError(f"{region!r} is not in this buffer")
		self.windowStartPos = 0

	def scrollTo(self, region, pos):
		if region not in self.regions:
			raise LookupError(f"{region!r} is not in this buffer")

	def routeTo(self, pos):
		self.routedTo = pos

	def saveWindow(self):
		if not self.regions:
			raise LookupError("An empty buffer has no window to save")
		self._savedWindow = self.windowStartPos

	def restoreWindow(self):
		if self._savedWindow is None:
			raise LookupError("No window was saved")
		self.windowStartPos = self._savedWindow

	def regionPosToBufferPos(self, region, pos, allowNearest=False):
		start = 0
		for candidate in self.visibleRegions:
			end = start + len(candidate.brailleCells)
			if candidate is region:
				if pos < end - start:
					return start + pos
				if allowNearest:
					return start
				break
			start = end
		if allowNearest:
			return start
		raise LookupError("No such position")

	def scrollForward(self):
		self.scrolled = "forward"

	def scrollBack(self):
		self.scrolled = "back"

	def _nextWindow(self):
		if self.windowEndPos >= len(self.brailleCells):
			return False
		self.windowStartPos = self.windowEndPos
		return True

	def _previousWindow(self):
		if self.windowStartPos <= 0:
			return False
		self.windowStartPos = max(0, self.windowStartPos - self.handler.displaySize)
		return True

	def getTextInfoForWindowPos(self, pos):
		return f"info@{pos}"


UNIT_LINE = "line"
UNIT_PARAGRAPH = "paragraph"
NEWLINE = chr(10)
"""What L{OffsetDocument} separates its lines with, spelled without an escape."""
POSITION_SELECTION = "selection"
POSITION_FIRST = "first"


@dataclasses.dataclass
class FakeBookmark:
	"""A mark for a position, shaped like NVDA's: comparable, and not hashable."""

	story: int
	index: int
	offset: int = 0
	"""Where in the line, because NVDA's own bookmark is a pair of offsets and not a line
	number. A block built at the caret therefore has a different identity for every character
	the reader passes, which is a fault a bookmark that knew only about lines could hide."""

	before: int = 0
	"""How many characters of the document come before this line.

	NVDA's bookmark is an offset into the buffer and nothing else, so a line's identity moves
	when text *earlier in the document* grows or shrinks. A stub that knew only about line
	numbers could not show that happening, and it is what `DocumentFlowSource.blockAt` refuses
	to hand a block back through: expanding at a position that has moved gives a neighbour
	under this block's name, which draws one line of the page twice.
	"""


class FakeTextInfo:
	"""A caret in a list of lines: a line, and a place within it.

	`text` is the whole line whatever the offset, which is the one simplification kept from
	the earlier version: everything above reads by the line, and giving a collapsed position
	an empty text would mean modelling ranges rather than positions.
	"""

	def __init__(
		self,
		lines,
		index,
		offset=0,
		expandsBackAt=None,
		paragraphBreaks=None,
		expandsOnAt=None,
	):
		self.lines = lines
		self.index = index
		self.offset = offset
		self.expanded = False
		"""Whether this range covers its whole unit, as `expand` makes it."""

		self.reachedOn = None
		"""The last line this range covers, when its expansion reached past its own.

		Set by `expand` from `expandsOnAt`, and the only way a range here ends anywhere but
		on the line it starts on. See `expandsOnAt`."""

		self.expandedUnit = None
		"""Which unit `expand` covered, because a line and a paragraph are different ranges."""

		self.expandsBackAt = expandsBackAt
		"""Units whose expansion reaches back to an earlier one.

		What a rich editor does at a position just past a break: asked to expand the unit
		there it takes in everything before it, so the unit's start is an earlier unit's
		start. Modelled because a flow that walks forward and lands on an earlier block shows
		the same text twice, and that is the whole of the duplicate row a comment box gave.

		An int means expansion at that line reaches the start of the document, which is the
		shape the probe caught in a plain textarea. A dict maps each affected line to the
		line its expansion reaches back to, for the partial reach a paragraph-structured
		editor produces."""

		self.expandsOnAt = expandsOnAt
		"""Units whose expansion reaches *forward* over the unit after them.

		The other half of the same transient, and the half that shows. A unit that reaches
		back hides its neighbour behind it; a unit that reaches on holds the next line as
		well as its own, so the band draws that line twice — once here and once in the block
		below — which is what a reader typing into a multi-line edit felt at the bottom of
		the display.

		The same shapes as `expandsBackAt`: an int means expansion at that line runs to the
		end of the document, a dict maps each affected line to the last line its expansion
		covers."""

		self.paragraphBreaks = paragraphBreaks
		"""Which lines end a paragraph, or None for every line being its own paragraph.

		A set of line indices, each the last line of its paragraph; the document's last line
		always is. Where a line is *not* in the set, the next line is the same paragraph
		wrapped on — which is the difference between the two units, and the difference the
		flow lost when its source walked by one and its regions rendered by the other. None
		keeps the old behaviour, where the two units agree, since a document of unwrapped
		single-line paragraphs is the common case and most tests mean it."""

	def _paragraphStart(self, index):
		""":return: the first line of the paragraph a line is in."""
		while index > 0 and (index - 1) not in self.paragraphBreaks:
			index -= 1
		return index

	def _paragraphEnd(self, index):
		""":return: the last line of the paragraph a line is in."""
		while index < len(self.lines) - 1 and index not in self.paragraphBreaks:
			index += 1
		return index

	@property
	def text(self):
		if not 0 <= self.index < len(self.lines):
			return ""
		if self.expanded and self.reachedOn is not None:
			# This line and everything the expansion took in after it, which is the text the
			# band draws from this block — and draws again in the block below it.
			return "".join(self.lines[self.index : self.reachedOn + 1])
		if self.expanded and self.expandedUnit == UNIT_PARAGRAPH and self.paragraphBreaks is not None:
			# A paragraph's wrapped lines are continuous text: the wrap is presentation, and
			# NVDA reading the paragraph gets it whole.
			return "".join(self.lines[self.index : self._paragraphEnd(self.index) + 1])
		return self.lines[self.index]

	def copy(self):
		copied = type(self)(
			self.lines,
			self.index,
			self.offset,
			self.expandsBackAt,
			self.paragraphBreaks,
			self.expandsOnAt,
		)
		copied.expanded = self.expanded
		copied.expandedUnit = self.expandedUnit
		copied.reachedOn = self.reachedOn
		return copied

	def _startPoint(self):
		""":return: where this range begins, as a comparable position."""
		return (self.index, 0 if self.expanded else self.offset)

	def _endPoint(self):
		""":return: where this range ends, as a comparable position.

		A collapsed position ends where it starts. An expanded one ends at the end of the
		last line it covers: its own, the end of its paragraph, or wherever its expansion
		reached on to.
		"""
		if not self.expanded:
			return (self.index, self.offset)
		last = self.index
		if self.reachedOn is not None:
			last = self.reachedOn
		elif self.expandedUnit == UNIT_PARAGRAPH and self.paragraphBreaks is not None:
			last = self._paragraphEnd(self.index)
		length = len(self.lines[last]) if 0 <= last < len(self.lines) else 0
		return (last, length)

	def compareEndPoints(self, other, which="startToStart"):
		"""Order two ranges, as NVDA's TextInfo does.

		**Both ends, because a range has two.** This modelled starts alone and answered
		start to start whatever it was asked, which is right until something asks whether one
		block runs into the next — the question a duplicated row on the display is the answer
		to. A stand-in that quietly answers a different question from the one asked would let
		a guard against that pass its tests while doing nothing at all.
		"""
		here = self._startPoint() if which.startswith("start") else self._endPoint()
		there = other._startPoint() if which.endswith("Start") else other._endPoint()
		return (here > there) - (here < there)

	@property
	def bookmark(self):
		"""A comparable mark for this position, as NVDA's TextInfo carries.

		Unhashable on purpose. NVDA's own bookmark for a virtual buffer is
		`textInfos.offsets.Offsets`, a plain dataclass, so it defines `__eq__` and Python
		sets its `__hash__` to None. Anything keying a dictionary by a bookmark works
		against a tuple and raises against the real thing, which is a failure worth having
		in the tests rather than on a display.
		"""
		return FakeBookmark(
			id(self.lines),
			self.index,
			0 if self.expanded else self.offset,
			sum(len(line) for line in self.lines[: self.index]),
		)

	def collapse(self, end=False):
		"""Reduce a range to one of its ends. A position is already one, and does not move."""
		if self.expanded:
			if end and self.reachedOn is not None:
				self.index = self.reachedOn
			elif end and self.expandedUnit == UNIT_PARAGRAPH and self.paragraphBreaks is not None:
				self.index = self._paragraphEnd(self.index)
			self.expanded = False
			self.expandedUnit = None
			self.reachedOn = None
			self.offset = len(self.text) if end else 0
			return
		self.expandedUnit = None

	def _reachesOnTo(self):
		""":return: the last line expansion here covers, or None for an honest answer."""
		if self.expandsOnAt is None:
			return None
		if isinstance(self.expandsOnAt, dict):
			return self.expandsOnAt.get(self.index)
		return len(self.lines) - 1 if self.index == self.expandsOnAt else None

	def _reachesBackTo(self):
		""":return: where expansion at this position lands, or None for an honest answer."""
		if self.expandsBackAt is None:
			return None
		if isinstance(self.expandsBackAt, dict):
			return self.expandsBackAt.get(self.index)
		return 0 if self.index == self.expandsBackAt else None

	def expand(self, unit):
		"""Cover the whole unit, whose start is the start of its first line.

		Unless this is a position that reaches back — see `expandsBackAt` — where the unit
		begins at an earlier line instead.
		"""
		target = self._reachesBackTo()
		reachedOn = self._reachesOnTo()
		if target is not None:
			self.index = target
		elif unit == UNIT_PARAGRAPH and self.paragraphBreaks is not None:
			self.index = self._paragraphStart(self.index)
		self.offset = 0
		self.expanded = True
		self.expandedUnit = unit
		self.reachedOn = reachedOn

	def move(self, unit, count):
		"""Move by whole units, reporting how far it actually got, as NVDA's TextInfo does.

		Lines and paragraphs move differently only when the document declares its paragraph
		breaks; without them every line is its own paragraph and the two units agree.
		"""
		self.expanded = False
		self.expandedUnit = None
		self.reachedOn = None
		self.offset = 0
		if unit == UNIT_PARAGRAPH and self.paragraphBreaks is not None:
			moved = 0
			while moved != count:
				if count > 0:
					end = self._paragraphEnd(self.index)
					if end >= len(self.lines) - 1:
						break
					self.index = end + 1
					moved += 1
				else:
					start = self._paragraphStart(self.index)
					if start <= 0:
						break
					self.index = self._paragraphStart(start - 1)
					moved -= 1
			return moved
		target = self.index + count
		clamped = max(0, min(len(self.lines) - 1, target))
		moved = clamped - self.index
		self.index = clamped
		return moved


class NoBookmarkTextInfo(FakeTextInfo):
	"""A position in a document that cannot mark one, as Chromium's editable text is.

	NVDA's `TextInfo.bookmark` raises where the underlying text API has nothing to mark a
	position with, and a bare `TextInfo` defines no `__eq__` — so it compares by identity,
	and every reading of one line is a different block. That is not a corner: it is what a
	comment box on a web page does, and the flow above recognises nothing without an answer
	for it.
	"""

	@property
	def bookmark(self):
		raise NotImplementedError("this document has no bookmarks")


class FakeDocument:
	"""An object whose caret sits on one line of a fixed list."""

	def __init__(self, lines, caretIndex=0):
		self.lines = lines
		self.caretIndex = caretIndex
		self.caretOffset = 0

	def makeTextInfo(self, position):
		return FakeTextInfo(self.lines, self.caretIndex, self.caretOffset)


class CursorManager:
	"""Stands in for NVDA's `CursorManager`, which browse mode documents are.

	Only its identity matters here: it is what a flow checks to decide that an object reads
	through a cursor of its own rather than through a caret.
	"""


class OffsetTextInfo:
	"""A position that is an absolute offset into one string, as NVDA's really are.

	L{FakeTextInfo} holds a line index into a live list, so an edit that shifts every offset
	after it is invisible to a position taken before it — which makes a whole class of bug
	untestable: the flow caches positions, and after an edit those caches are stale in
	exactly the way an index cannot express. This is the faithful model. A position taken
	before an edit goes on meaning the offset it meant, which is what makes it wrong
	afterwards, and that is the point of it.
	"""

	def __init__(self, doc, start, end=None):
		self.doc = doc
		self.start = start
		self.end = start if end is None else end
		self.expanded = end is not None

	def copy(self):
		copied = OffsetTextInfo(self.doc, self.start, self.end)
		copied.expanded = self.expanded
		return copied

	@property
	def text(self):
		return self.doc.text[self.start : self.end]

	@property
	def offset(self):
		""":return: how far into its own line this position sits, which is what a cursor is."""
		return self.start - self._lineBounds(self.start)[0]

	def _lineBounds(self, offset):
		text = self.doc.text
		offset = max(0, min(offset, len(text)))
		begin = text.rfind(NEWLINE, 0, offset) + 1
		stop = text.find(NEWLINE, offset)
		return begin, (len(text) if stop < 0 else stop)

	def collapse(self, end=False):
		self.start = self.end if end else self.start
		self.end = self.start
		self.expanded = False

	def expand(self, unit):
		self.start, self.end = self._lineBounds(self.start)
		self.expanded = True

	def move(self, unit, count):
		moved = 0
		while moved != count:
			begin, stop = self._lineBounds(self.start)
			if count > 0:
				if stop >= len(self.doc.text):
					break
				self.start = self.end = stop + 1
				moved += 1
			else:
				if begin <= 0:
					break
				self.start = self.end = self._lineBounds(begin - 1)[0]
				moved -= 1
		self.expanded = False
		return moved

	def compareEndPoints(self, other, which="startToStart"):
		return (self.start > other.start) - (self.start < other.start)

	@property
	def bookmark(self):
		return ("offset", self.start)


class OffsetDocument(CursorManager):
	"""A document holding one string and a caret at an offset into it.

	Edited through L{edit}, which is what a real edit does to a real buffer: the text changes
	and every offset after the change moves. Positions handed out before it keep the numbers
	they were given, and are therefore wrong afterwards — which no index-based stand-in can
	express, and which is exactly what the flow's cached positions have to survive.
	"""

	def __init__(self, lines, caret=0):
		self.text = NEWLINE.join(lines)
		self.caret = caret
		self.isReady = True
		self.passThrough = False

	@property
	def lines(self):
		return self.text.split(NEWLINE)

	@property
	def selection(self):
		return OffsetTextInfo(self, self.caret)

	@selection.setter
	def selection(self, info):
		self.caret = info.start

	def makeTextInfo(self, position):
		return OffsetTextInfo(self, self.caret)

	def offsetOf(self, needle):
		""":return: where a piece of text starts, for a test putting the caret there."""
		return self.text.index(needle)

	def lineEndFrom(self, offset):
		""":return: where the line holding an offset ends, which is where its break sits."""
		stop = self.text.find(NEWLINE, offset)
		return len(self.text) if stop < 0 else stop

	def edit(self, at, removeChars=0, insert=""):
		"""Change the text, shifting every offset after the change, as an edit does.

		:param at: where the change begins.
		:param removeChars: how many characters go.
		:param insert: what takes their place.
		"""
		self.text = self.text[:at] + insert + self.text[at + removeChars :]


class FieldCommand:
	"""NVDA's `textInfos.FieldCommand`: a marker in a run of text saying a field starts or ends.

	Here because `flowForms` recognises a form control from the fields of the text itself,
	and does so with an `isinstance` check, so a stand-in has to be the type the code tests
	against rather than something shaped like it.

	**It refuses what NVDA's refuses**, and that is not decoration. NVDA's raises
	`ValueError("command: controlStart needs a controlField")` for a plain dictionary, and
	this one used to take anything. So the object tables built their cells' fields out of a
	dictionary, every call raised in NVDA and nowhere else, the caller read the raise as "this
	cell declares no header" — and every list view silently had no headings at all, for months,
	with a green test suite. A stand-in that is more forgiving than the thing it stands in for
	is a test that cannot fail.
	"""

	def __init__(self, command, field=None):
		if command not in ("controlStart", "controlEnd", "formatChange"):
			raise ValueError(f"Unknown command: {command}")
		if command == "controlStart" and not isinstance(field, ControlField):
			raise ValueError(f"command: {command} needs a controlField")
		if command == "formatChange" and not isinstance(field, FormatField):
			raise ValueError(f"command: {command} needs a formatField")
		self.command = command
		self.field = field


class Field(dict):
	"""NVDA's `textInfos.Field`, which is a dictionary and the base of the two below."""


class ControlField(Field):
	"""NVDA's `textInfos.ControlField`, which is a dictionary carrying a role and more."""


class FormatField(Field):
	"""NVDA's `textInfos.FormatField`, which carries what the text looks like."""


class FakeTreeInterceptor(CursorManager):
	"""A browse mode document, which reads through a cursor of its own rather than a caret."""

	def __init__(
		self,
		lines,
		caretIndex=0,
		isReady=True,
		passThrough=False,
		bookmarks=True,
		expandsBackAt=None,
		paragraphBreaks=None,
		expandsOnAt=None,
	):
		self.lines = lines
		self.expandsBackAt = expandsBackAt
		"""Which unit of this document reaches back when expanded. See `FakeTextInfo`."""

		self.expandsOnAt = expandsOnAt
		"""Which unit of this document reaches on over the next when expanded. See
		`FakeTextInfo`."""

		self.paragraphBreaks = paragraphBreaks
		"""Which lines end a paragraph, for a document whose paragraphs wrap. See `FakeTextInfo`."""

		self.positionType = FakeTextInfo if bookmarks else NoBookmarkTextInfo
		"""Which kind of position this document hands out. See `NoBookmarkTextInfo`."""

		self.caretIndex = caretIndex
		self.caretOffset = 0
		"""Where in its line the browse mode cursor sits.

		A virtual buffer's cursor moves within a line as well as between them — by the
		character arrows, and by every keystroke into a form field the reader has entered —
		and a document that could only be on a line could not show a cursor that fails to
		follow it."""

		self.isReady = isReady
		self.passThrough = passThrough

	@property
	def selection(self):
		return self._position(self.caretIndex, self.caretOffset)

	def _position(self, index, offset=0):
		return self.positionType(
			self.lines,
			index,
			offset,
			self.expandsBackAt,
			paragraphBreaks=self.paragraphBreaks,
			expandsOnAt=self.expandsOnAt,
		)

	@selection.setter
	def selection(self, info):
		self.caretIndex = info.index
		self.caretOffset = info.offset

	def makeTextInfo(self, position):
		"""Build a position, as a tree interceptor does.

		A position may be an object rather than one of NVDA's position constants: that is how
		a virtual buffer is asked where something sits in its document, and it is what reads a
		form control at its own place rather than at a cursor that has gone inside it. An
		object this document cannot place raises `LookupError`, as NVDA's own does.
		"""
		if isinstance(position, str):
			return self._position(self.caretIndex, self.caretOffset)
		if position is getattr(self, "rootNVDAObject", None):
			# A virtual buffer can place the object it was built over, and places it at the
			# start of itself. Refusing to would be the kinder answer and the wrong one: it
			# is what let a band read "at" the page and land on its first line while the
			# reader was halfway down it.
			return self._position(0)
		index = getattr(position, "documentIndex", None)
		if index is None:
			raise LookupError(f"{position!r} is not in this document")
		return self._position(index)


class FakeFieldCommand:
	"""NVDA's `textInfos.FieldCommand`, in the two attributes anything here reads."""

	def __init__(self, command, field):
		self.command = command
		self.field = field


class FakeCellInfo:
	"""A `TextInfo` over one cell of a fake table."""

	def __init__(self, text, cell=None, fields=None):
		"""
		:param fields: the cell's control field, as a document that marks its table up hands
			one out. None is a table that declares nothing, which is most tables and is the
			case row one exists for.
		"""
		self.text = text
		self.cell = cell
		self.fields = fields
		self.isCollapsed = False
		self.caretAt = None

	def getTextWithFields(self, formatConfig=None):
		"""What NVDA returns for a range: the fields that enclose it, then its text.

		The cell's own field is innermost and comes last, after the table's, because that is
		the order a real one arrives in and the code under test takes the innermost.
		"""
		if self.fields is None:
			return [self.text]
		return [
			FakeFieldCommand("controlStart", {"role": "table"}),
			FakeFieldCommand("controlStart", dict(self.fields)),
			self.text,
		]

	def copy(self):
		other = FakeCellInfo(self.text, self.cell, self.fields)
		other.caretAt = self.caretAt
		return other

	def collapse(self, end=False):
		self.isCollapsed = True

	def updateCaret(self):
		if self.cell is not None:
			self.cell["caret"] = True


class Axis(str, enum.Enum):
	"""`documentBase._Axis`: which way a table movement runs."""

	ROW = "row"
	COLUMN = "column"


class Movement(str, enum.Enum):
	"""`documentBase._Movement`: which end of the axis a table movement is towards.

	Real enumerations, and mixed with `str` exactly as NVDA's are, because the difference
	shows: NVDA tests these with `movement in {_Movement.NEXT, _Movement.PREVIOUS}`, and a
	string that merely spells the same word hashes differently and is not in that set. A
	stand-in made of plain strings would have accepted what NVDA rejects.
	"""

	NEXT = "next"
	PREVIOUS = "previous"
	FIRST = "first"
	LAST = "last"


class FakeTableCell:
	"""What `_getTableCellCoords` answers with. NVDA's own is a dataclass of these fields."""

	def __init__(self, tableID, row, col, rowSpan=1, colSpan=1):
		self.tableID = tableID
		self.row = row
		self.col = col
		self.rowSpan = rowSpan
		self.colSpan = colSpan


class FakeTableDocument(FakeTreeInterceptor):
	"""A browse mode document with a table in it, and the three methods a table needs.

	**A document first.** A table is not a thing the reader is in *instead of* a page; it is
	part of one, and the caret walks in and out of it without the document changing. A
	stand-in that was only a table modelled leaving one as arriving at a different document,
	which is the one thing that never happens — and a band that could not tell a finished
	table from the page around it passed every test written against it.

	`inTable` is what the caret walking out looks like from here: the same document, still
	perfectly readable, answering no when asked which cell the caret is in.

	The holes matter as much as the content: a coordinate whose text is None is a cell the
	table has not got, which is what a merged cell looks like from the outside and what
	`_getTableCellAt` says by raising.
	"""

	def __init__(self, rows, tableID=1, row=1, col=1, lines=None, inTable=True, columnHeaders=None):
		"""
		:param rows: a list of rows, each a list of cell texts. None for a missing cell.
		:param lines: the document's own lines, for reading it as a page.
		:param inTable: whether the caret is in the table.
		:param columnHeaders: what each column declares as its header, by column number. This
			is what `<th>`, `scope=` and `headers=` come out as by the time NVDA has resolved
			them, and it is a property of the column rather than of any row. None is a table
			that marks nothing up.
		"""
		super().__init__(lines or ["a heading", "some prose", "more prose"])
		self.rows = rows
		self.tableID = tableID
		self.row = row
		self.col = col
		self.inTable = inTable
		self.columnHeaders = dict(columnHeaders or {})
		self.reads = []
		self.carets = []
		self.reach = 0
		"""How far the reader can go beyond what is written in, which is what a spreadsheet
		does: the table reaches at least as far as the cell they are standing in, or a reader
		arrowing below the data is standing outside their own table. Zero for a document,
		where a table is as big as it is."""

	@property
	def numRows(self):
		return max(len(self.rows), self.reach)

	@property
	def contentRows(self):
		""":return: how far this table is written in, which is not how far it reaches.

		The number that only moves when the table does. See
		`flowObjectTable.SheetTable.contentRows`."""
		return len(self.rows)

	@property
	def numCols(self):
		return max((len(row) for row in self.rows), default=0)

	def _getTableCellCoords(self, info):
		if not self.rows or not self.inTable:
			raise LookupError("Not in a table cell")
		return FakeTableCell(self.tableID, self.row, self.col)

	def _getTableDimensions(self, info):
		return (self.numRows, self.numCols)

	def _getTableCellAt(self, tableID, startPos, row, column):
		if tableID != self.tableID:
			raise LookupError("Wrong table")
		self.reads.append((row, column))
		if not 1 <= row <= self.numRows:
			raise LookupError("No such row")
		cells = self.rows[row - 1]
		if not 1 <= column <= len(cells) or cells[column - 1] is None:
			raise LookupError("No such cell")
		holder = {"caret": False}
		self.carets.append(((row, column), holder))
		fields = None
		if self.columnHeaders:
			fields = {"table-id": self.tableID, "table-columnnumber": column}
			said = self.columnHeaders.get(column)
			if said:
				fields["table-columnheadertext"] = said
		return FakeCellInfo(cells[column - 1], holder, fields)


class NoTableDocument(FakeTableDocument):
	"""A document that navigates tables but is not in one, which is most of a web page."""

	def __init__(self, rows=None, **kwargs):
		super().__init__(rows or [], **kwargs)

	def _getTableCellCoords(self, info):
		raise LookupError("Not in a table cell")


class TextInfoRegion(Region):
	"""Renders whatever line its object's caret is on, and moves that caret when panned.

	Faithful about moving the caret because that is the behaviour a pinned region has to
	override: a test that panned a pin and found the object's caret unmoved would prove
	nothing against a stub that never moved it in the first place.
	"""

	def __init__(self, obj):
		super().__init__("")
		self.obj = obj
		self._readingInfo = None

	def _getSelection(self):
		return FakeTextInfo(self.obj.lines, self.obj.caretIndex, getattr(self.obj, "caretOffset", 0))

	def _setCursor(self, info):
		self.obj.caretIndex = info.index
		self.obj.caretOffset = info.offset

	def _getReadingUnit(self):
		return UNIT_LINE

	def _getDefaultRegionLanguage(self):
		return "en"

	def update(self):
		info = self._readingInfo = self._getSelection()
		# Expanded to the reading unit, as NVDA's `TextInfoRegion.update` expands it, so
		# that a region asked to render a paragraph renders the paragraph and not the line
		# at its start. The distinction only exists for a document that declares its
		# paragraph breaks; everywhere else the two units are the same range and this is
		# the line it always was.
		reading = info.copy()
		reading.expand(self._getReadingUnit())
		self.rawText = reading.text
		# **Before the translation, which is the order NVDA does it in and the order that
		# matters.** `TextInfoRegion.update` works the cursor out and then calls
		# `Region.update`, which decides from it whether to ask liblouis for computer braille
		# at the cursor. Set afterwards — as this stub used to — a region could clear its
		# cursor after being translated and the test could not see that the cells had already
		# been made with one. That is exactly the fault the reader reported: every row of the
		# band with its first word expanded. See `Region.expandedAtCursor`.
		#
		# A collapsed position is a cursor, as it is in NVDA. From the position this region
		# reads, and not from the object's caret: NVDA's `TextInfoRegion.update` calls
		# `_getSelection` once and lays the block out around what it returns, so a region
		# answering that call with a position of its own has a cursor at that position and
		# nowhere else. Reading the caret directly here made every flow block track it, which
		# is neither what a pinned block does nor what the hardware saw: an edit field whose
		# cursor sat on the first cell and stayed there.
		#
		# Clamped to a cell that exists, as NVDA clamps it: there the reading unit gains a
		# trailing space so that a caret at its end has somewhere to be, and the cursor is
		# then held inside the text. A cursor past the last cell is not a state a region
		# ever reaches, so it is not one a test should be able to produce.
		self.cursorPos = min(info.offset, max(0, len(self.rawText) - 1))
		Region.update(self)
		self.brailleCursorPos = self.cursorPos

	def routeTo(self, pos):
		self.routedTo = pos

	def getTextInfoForBraillePos(self, braillePos):
		"""Where a cell of this region points, which routing asks for before acting."""
		return self._getSelection()

	def _moveLine(self, count):
		dest = self._readingInfo.copy()
		dest.collapse()
		dest.move(UNIT_LINE, count)
		self._setCursor(dest)

	def nextLine(self):
		self.panned = "next"
		self._moveLine(1)

	def previousLine(self, start=False):
		self.panned = "previous"
		self._moveLine(-1)


class CursorManagerRegion(TextInfoRegion):
	"""Reads and writes the browse mode cursor rather than a caret, as NVDA's does."""

	def _getSelection(self):
		return self.obj.selection

	def _setCursor(self, info):
		self.obj.selection = info


class FakeHandler:
	"""Stands in for `BrailleHandler` well enough to build a container against.

	Also answers what `patches._doNewObjectMultiSegment` asks of a handler, so that the
	patch can be driven against a real container rather than only against
	L{FakeBrailleHandler}, which exists to be patched rather than to be run.
	"""

	def __init__(self, numRows=8, numCols=32):
		self.displayDimensions = DisplayDimensions(numRows, numCols)
		self.buffer = None
		self.mainBuffer = None
		self.messageBuffer = None
		self.updateCount = 0
		self.display = types.SimpleNamespace(name="stub")
		self._regionsPendingUpdate = set()
		self._keyCountForLastMessage = 0
		self.tether = "focus"
		self.autoScrollEnabled = None
		self.scrolledTo = []
		self._messageCallLater = None
		self.gainedFocus = []

	@property
	def displaySize(self):
		return self.displayDimensions.displaySize

	def update(self):
		self.updateCount += 1

	def autoScroll(self, enable=False):
		self.autoScrollEnabled = enable

	def getTether(self):
		return self.tether

	def scrollToCursorOrSelection(self, region):
		self.scrolledTo.append(region)

	def handleGainFocus(self, obj):
		"""Records the request rather than redrawing.

		NVDA's redraws the display from the focus, and reproducing that would mean reproducing
		region building. Every test that cares about this asks only whether the add-on asked for
		a redraw at all — the case being that handing a message back has to leave braille
		showing something without waiting for the user's next keystroke.
		"""
		self.gainedFocus.append(obj)

	def handleCaretMove(self, obj):
		"""Queue exactly the region NVDA queues for a caret event.

		The object comparison is the important part: a focus-mode edit reports the control,
		not its browse-mode tree interceptor. A test cannot prove that cursor tracking works
		unless its handler enforces that same ownership rule.
		"""
		region = self.mainBuffer.regions[-1] if self.mainBuffer and self.mainBuffer.regions else None
		if region is None or getattr(region, "obj", None) != obj:
			return
		region.pendingCaretUpdate = True
		self._regionsPendingUpdate.add(region)

	def _handlePendingUpdate(self):
		"""Run the reduced pending-update cycle used by the caret tracking tests."""
		try:
			for region in self._regionsPendingUpdate:
				region.update()
				region.pendingCaretUpdate = False
			if self.mainBuffer is not None:
				self.mainBuffer.update()
		finally:
			self._regionsPendingUpdate.clear()

	def _dismissMessage(self, shouldUpdate: bool = True):
		"""Transcribed from `BrailleHandler._dismissMessage`, all four steps of it.

		Written out rather than reduced to a flag because the add-on hands a showing message
		back to NVDA through this, and each step is one the add-on was found not to be doing:
		the message is cleared, the display returns to the main buffer, and the timeout stops.
		A stub that only recorded the call would let all three go on being missed.
		"""
		self.buffer.clear()
		self.buffer = self.mainBuffer
		if self._messageCallLater:
			self._messageCallLater.Stop()
			self._messageCallLater = None
		if shouldUpdate:
			self.update()


def fakeVirtualDisplay(*bands, configured=None, drivers=None):
	"""A stand-in for the add-on's own braille display driver, which is several displays.

	Shaped the way `devices` reads it — a name, the members it was opened for, and a slot per
	member carrying its driver name, its band and whether it has been given up on — and no more
	than that, because nothing above the driver has any business reading more. A test wanting a
	member that has stopped responding sets `display.slots[n].failed`, as `DeviceSlot.fail` does
	when a write raises.

	:param bands: each member's (driverName, rowStart, numRows, numCols), top first.
	:param configured: the members it was opened for, or None for exactly the bands given. A
		display switched off before braille started has no band and is still configured, which
		is the case that separates "a display is away" from "the list has been edited".
	:param drivers: the live driver object for each member, by driver name. `devices` never
		reads these, but `graphics` does: finding the member that can draw means looking at
		the drivers themselves, since the composite does not forward their methods.
	:return: the stand-in display.
	"""
	drivers = drivers or {}
	return types.SimpleNamespace(
		name="brlMultilineVirtual",
		configuredMembers=tuple(
			configured if configured is not None else (driverName for driverName, *_rest in bands),
		),
		slots=tuple(
			types.SimpleNamespace(
				driverName=driverName,
				band=types.SimpleNamespace(rowStart=rowStart, numRows=numRows, numCols=numCols),
				failed=False,
				driver=drivers.get(driverName),
			)
			for driverName, rowStart, numRows, numCols in bands
		),
	)


class ConfigManager:
	"""Stands in for NVDA's `config.ConfigManager`, for the one class attribute add-ons touch.

	`BASE_ONLY_SECTIONS` is documented upstream as extensible by add-ons, and it is a class
	attribute mutated in place — so a stub has to expose the same shape or the code that
	extends it cannot be tested at all.
	"""

	BASE_ONLY_SECTIONS = {"general", "update", "development", "addonStore"}


class FakeConf(dict):
	"""NVDA's configuration object: profile aware, and carrying the registered specs.

	Faithful about the three behaviours that decide where a setting ends up, because the
	add-on now depends on all three:

	1. A section named in `BASE_ONLY_SECTIONS` is read straight out of the base profile,
	   whatever profile is active. `ConfigManager.__getitem__` does exactly this.
	2. Anything else is read from the active profile first, falling back to base.
	3. Writes go to the active profile when there is one. Upstream: "Changed settings are
	   written to the most recently activated profile."

	And about one thing that is not a behaviour but a distinction: `profiles` is the *active*
	stack, while L{listProfiles} names every profile saved on disk. A saved profile that is not
	triggered right now appears only in the second, which is where a device list can hide.
	"""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.spec = {}
		self.profiles = [dict(self)]
		"""Profile 0 is the base configuration, as in NVDA. Later entries are activated ones."""
		self.savedProfiles = {}
		"""The profile files on disk, by name, whether or not they are active."""

	@property
	def activeProfile(self):
		""":return: the most recently activated profile, or None when only base is in force."""
		return self.profiles[-1] if len(self.profiles) > 1 else None

	def listProfiles(self):
		""":return: the names of the saved profiles, as NVDA's reads them off disk."""
		return list(self.savedProfiles)

	def _getProfile(self, name, load=True):
		"""Load a saved profile. The private name is deliberate: it is the one that loads."""
		return self.savedProfiles[name]

	def getProfile(self, name):
		"""NVDA's public one, which refuses to load. Here to keep the distinction visible."""
		raise KeyError(name)

	def saveProfile(self, name, values=None):
		"""Write a profile to disk without activating it, which NVDA does when one is edited.

		:param name: the profile's name.
		:param values: what it overrides, as section name to mapping.
		:return: the profile.
		"""
		self.savedProfiles[name] = dict(values or {})
		return self.savedProfiles[name]

	def activateProfile(self, values=None, name=None):
		"""Push a configuration profile, as a profile trigger does.

		:param values: what the profile overrides, as section name to mapping.
		:param name: the name to save it under, since a profile that can be activated is one
			that exists on disk. Omit for a profile that has not been saved.
		:return: the profile.
		"""
		profile = dict(values or {})
		if name is not None:
			self.savedProfiles[name] = profile
		self.profiles.append(profile)
		return profile

	def deactivateProfiles(self):
		"""Drop every profile, leaving the base configuration."""
		del self.profiles[1:]

	def __getitem__(self, key):
		if key in ConfigManager.BASE_ONLY_SECTIONS:
			return self.profiles[0][key]
		profile = self.activeProfile
		if profile is not None and key in profile:
			return profile[key]
		return super().__getitem__(key)

	def __setitem__(self, key, value):
		profile = self.activeProfile
		if profile is not None and key not in ConfigManager.BASE_ONLY_SECTIONS:
			profile[key] = value
			return
		super().__setitem__(key, value)


class Decider:
	"""NVDA's `extensionPoints.Decider`, including the two behaviours that matter.

	Registration order is preserved, and `decide` stops at the first handler returning False.
	Together those are why the virtual display puts its own handler at the front: NVDA Remote
	registers on this same extension point and returns False for every braille gesture it
	forwards.

	`moveToEnd` matches NVDA's signature, where `last=False` means move to the front.

	Lives here rather than beside the driver stubs because the global plugin registers on
	`decide_executeGesture` too, to follow which display is being used. A stub only the driver
	tests installed left the plugin tests passing or failing by import order.
	"""

	def __init__(self):
		self.handlers = []

	def register(self, handler):
		self.handlers.append(handler)

	def unregister(self, handler):
		if handler in self.handlers:
			self.handlers.remove(handler)

	def moveToEnd(self, handler, last: bool = True) -> bool:
		if handler not in self.handlers:
			return False
		self.handlers.remove(handler)
		if last:
			self.handlers.append(handler)
		else:
			self.handlers.insert(0, handler)
		return True

	def decide(self, **kwargs) -> bool:
		for handler in list(self.handlers):
			if handler(**kwargs) is False:
				return False
		return True


decide_executeGesture = Decider()
"""The one extension point both the driver and the plugin register on."""


class BrailleDisplayGesture:
	"""The base every braille gesture is tested against with `isinstance`."""

	source = ""


class Action:
	"""Stands in for an NVDA extension point, recording who is listening."""

	def __init__(self):
		self.handlers = []

	def register(self, handler):
		self.handlers.append(handler)

	def unregister(self, handler):
		if handler in self.handlers:
			self.handlers.remove(handler)

	def notify(self, **kwargs):
		for handler in list(self.handlers):
			handler(**kwargs)


def callWithSupportedKwargs(func, **kwargs):
	"""Pass a callable only the keyword arguments it accepts, as NVDA's extension points do.

	Used both by L{FakeBrailleHandler._switchDisplay} and, through `_virtualStubs`, as the
	stand-in for `extensionPoints.callWithSupportedKwargs` itself, so that the two cannot
	disagree about what a driver constructor is given.
	"""
	import inspect

	parameters = inspect.signature(func).parameters
	if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
		return func(**kwargs)
	return func(**{name: value for name, value in kwargs.items() if name in parameters})


class FakeBrailleHandler:
	"""The class `patches` and `handover` install onto, and enough handler to be switched.

	Most of it exists only to be replaced. `_switchDisplay` is the exception: it reproduces
	NVDA's own ordering, in which the incoming driver is constructed *before* the outgoing
	one is terminated. That ordering is the whole reason `handover` exists, so a stub that
	quietly got it the sensible way round would test nothing.
	"""

	display = None

	def __init__(self):
		self._displayDimensions = DisplayDimensions(1, 0)

	@property
	def displayDimensions(self):
		"""Transcribed from `BrailleHandler._get_displayDimensions`, notification included.

		Recomputed on every read rather than cached, and `displaySizeChanged` raised when the
		answer differs from the previous one. That is the whole of how a display tells NVDA it
		has changed size — the virtual display, losing a member, changes `numRows` and reads
		this once — so a stub that cached, or that skipped the notification, would hide the
		only mechanism there is.
		"""
		display = self.display
		if display is None:
			numRows = numCols = 0
		else:
			numRows = display.numRows
			numCols = display.numCols if numRows > 1 else display.numCells
		dimensions = DisplayDimensions(numRows=numRows, numCols=numCols)
		if self._displayDimensions != dimensions:
			displaySizeChanged.notify(
				displaySize=dimensions.displaySize,
				numRows=numRows,
				numCols=numCols,
			)
		self._displayDimensions = dimensions
		return dimensions

	def update(self):
		"""Write what the buffer holds to the display, as `BrailleHandler.update` does.

		Reduced to the one step that matters outside NVDA — the cursor and the pending update
		bookkeeping are not modelled — because this is how cells reach a display, and a display
		that has just been opened receiving the current braille is a thing worth asserting.
		"""
		display = self.display
		buffer = getattr(self, "buffer", None)
		if display is None or buffer is None:
			return
		display.display(list(buffer.windowBrailleCells))

	def _doNewObject(self, regions):
		self.lastNewObject = list(regions)

	def scrollForward(self):
		# Transcribed from NVDA's, which scrolls whatever buffer the handler is showing.
		# The add-on patches this method and falls back to it whenever a scroll has no
		# display behind it, so a stub that only set a flag would leave that path untested.
		self.scrolledForward = True
		self.buffer.scrollForward()

	def scrollBack(self):
		self.scrolledBack = True
		self.buffer.scrollBack()

	def _handlePendingUpdate(self):
		self._regionsPendingUpdate = set()

	def _switchDisplay(self, oldDisplay, newDisplayClass, **kwargs):
		"""Transcribed from `BrailleHandler._switchDisplay`, including the ordering."""
		sameDisplayReInit = oldDisplay is not None and newDisplayClass == oldDisplay.__class__
		if sameDisplayReInit:
			oldDisplay.terminate()
			newDisplay = oldDisplay
		else:
			newDisplay = newDisplayClass.__new__(newDisplayClass)
		callWithSupportedKwargs(newDisplay.__init__, **kwargs)
		if not sameDisplayReInit and oldDisplay:
			oldDisplay.terminate()
		newDisplay.initSettings()
		return newDisplay

	def setDisplay(self, newDisplayClass, **kwargs):
		"""The part of `_setDisplay` that matters here: the new display is assigned last."""
		newDisplay = self._switchDisplay(self.display, newDisplayClass, **kwargs)
		self.display = newDisplay
		return newDisplay


class GlobalCommands:
	"""NVDA's `globalCommands.GlobalCommands`, with the two commands the add-on wraps.

	A real class with real methods, because the add-on replaces class attributes and NVDA
	resolves a gesture's script by `getattr` on the instance holding it. A stub keeping
	callables in a dictionary would let a wrapper be installed that nothing could ever reach,
	and every test of it would pass while testing nothing.

	The bodies are NVDA's own: one line each, panning the handler.
	"""

	def script_braille_scrollForward(self, gesture):
		"""Pans the braille display forward."""
		import braille

		braille.handler.scrollForward()

	def script_braille_scrollBack(self, gesture):
		"""Pans the braille display back."""
		import braille

		braille.handler.scrollBack()

	def script_braille_routeTo(self, gesture):
		"""Routes the cursor to or activates the object under this braille cell."""


class InputManager:
	"""NVDA's `inputCore.manager`, in the order that decides where a gesture's display goes.

	Transcribed for the ordering rather than the behaviour, because the ordering is the whole
	reason the add-on stopped recording a gesture's display when the gesture passed:

	1. `decide_executeGesture` runs first, on the thread the driver dispatched from.
	2. The capture function runs *after* it. Input help's returns False for a gesture it only
	   describes, so the script never runs at all.
	3. The script is then queued rather than called — `scriptHandler.queueScript` puts it on
	   the main thread's queue — so any number of gestures can get through steps 1 and 2
	   before the first script runs.

	L{runQueued} stands in for the main thread's event queue, which is what lets a test hold
	two displays' gestures and then run them.
	"""

	def __init__(self):
		self.captureFunc = None
		"""Set to a callable to stand in for input help, which returns False."""
		self.queue = []

	def executeGesture(self, gesture):
		if not decide_executeGesture.decide(gesture=gesture):
			return
		if self.captureFunc is not None and self.captureFunc(gesture) is False:
			return
		script = gesture.script
		if script is None:
			return
		self.queue.append((script, gesture))

	def runQueued(self):
		""":return: how many queued scripts ran."""
		queued, self.queue = self.queue, []
		for script, gesture in queued:
			script(gesture)
		return len(queued)


class FakeCallLater:
	"""A stand-in for the `wx.CallLater` NVDA holds a message's timeout in.

	Only `Stop` is used from it, and whether it was called is the whole question: a timeout
	left running fires into `_dismissMessage`, whose precondition is that a message is showing.
	"""

	def __init__(self):
		self.stopped = False

	def Stop(self):  # noqa: N802 - wx's own spelling.
		self.stopped = True


class CallLaterQueue:
	"""Collects `wx.CallLater` timers so a test can fire them when it chooses.

	A timer that re-arms itself is the ordinary case here — the virtual display's poll does —
	so `fire` runs what was pending when it was called and not whatever that produces, or a
	test would never get its thread back.
	"""

	def __init__(self):
		self.pending = []

	def callLater(self, milliseconds, callable, *args, **kwargs):
		timer = FakeTimer(self, milliseconds, callable, args, kwargs)
		self.pending.append(timer)
		return timer

	def fire(self):
		""":return: how many timers ran."""
		due, self.pending = self.pending, []
		for timer in due:
			timer.run()
		return len(due)


class FakeTimer:
	"""One `wx.CallLater`, as far as anything in this add-on uses one."""

	def __init__(self, queue, milliseconds, callable, args=(), kwargs=None):
		self.queue = queue
		self.milliseconds = milliseconds
		self.callable = callable
		self.args = args
		self.kwargs = kwargs or {}
		self.stopped = False

	def Stop(self):  # noqa: N802 - wx's own spelling.
		self.stopped = True
		if self in self.queue.pending:
			self.queue.pending.remove(self)

	def run(self):
		if not self.stopped:
			self.callable(*self.args, **self.kwargs)


class CallAfterQueue:
	"""Collects `wx.CallAfter` calls so a test can decide when, or whether, they run."""

	def __init__(self):
		self.pending = []

	def callAfter(self, callable, *args, **kwargs):
		self.pending.append((callable, args, kwargs))

	def flush(self):
		""":return: how many queued calls were run."""
		queued, self.pending = self.pending, []
		for callable, args, kwargs in queued:
			callable(*args, **kwargs)
		return len(queued)

	def discard(self):
		self.pending = []


callAfterQueue = CallAfterQueue()
"""The queue `wx.CallAfter` writes into. Tests flush it to run a deferred rebuild."""


ID_OK = 5100
ID_CANCEL = 5101
YES = 5103
NO = 5104
YES_NO = 0x0A
ICON_WARNING = 0x100
ICON_QUESTION = 0x200
"""wx's answers and flags, as numbers, since the tests compare them and nothing else."""


class DialogAnswers:
	"""What the reader will answer a dialog, and what they were shown.

	wx has nowhere to open a window in a test, so the dialogs the plugin puts up are stubs.
	A test says what will be answered before pressing the command and reads `shown`
	afterwards, which keeps the decision behind the dialog testable without the dialog
	itself being the thing under test.

	Nothing is agreed to by default: an unset answer cancels, so a test that forgets to say
	what the reader did gets the reading where they backed out rather than the one where
	they consented to everything.
	"""

	def __init__(self):
		self.reset()

	def reset(self):
		self.shown = []
		self.answer = ID_CANCEL
		self.select = None
		self.messageAnswer = NO

	def record(self, kind, message, caption, choices):
		self.shown.append(
			types.SimpleNamespace(kind=kind, message=message, caption=caption, choices=list(choices))
		)


dialogAnswers = DialogAnswers()
"""The one place a test says what a dialog was answered. Reset between tests."""


class FakeChoiceDialog:
	"""The shape wx's choice dialogs present: made, selected in, shown, destroyed."""

	kind = "choice"

	def __init__(self, parent, message, caption, choices):
		self.parent = parent
		self.choices = list(choices)
		self.destroyed = False
		dialogAnswers.record(self.kind, message, caption, choices)

	def ShowModal(self):
		return dialogAnswers.answer

	def Destroy(self):
		self.destroyed = True


class FakeSingleChoiceDialog(FakeChoiceDialog):
	kind = "single"

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.selection = 0

	def SetSelection(self, index):
		self.selection = index

	def GetSelection(self):
		return self.selection if dialogAnswers.select is None else dialogAnswers.select


class FakeMultiChoiceDialog(FakeChoiceDialog):
	kind = "multi"

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.selections = []

	def SetSelections(self, indexes):
		self.selections = list(indexes)

	def GetSelections(self):
		return self.selections if dialogAnswers.select is None else list(dialogAnswers.select)


class FakeMainFrame:
	"""NVDA's main window, which dialogs are raised in front of.

	`prePopup` and `postPopup` are counted rather than ignored: a dialog that forgets the
	second one leaves NVDA's window in the state it was put in for the first.
	"""

	def __init__(self):
		self.prePopups = 0
		self.postPopups = 0
		self.settingsDialogs = []

	def prePopup(self):
		self.prePopups += 1

	def postPopup(self):
		self.postPopups += 1

	def popupSettingsDialog(self, dialogClass, panel=None):
		self.settingsDialogs.append((dialogClass, panel))


mainFrame = FakeMainFrame()
"""Stands in for `gui.mainFrame`, so the dialog paths can be driven."""


def _messageBox(message, caption="", flags=0, *args, **kwargs):
	dialogAnswers.record("message", message, caption, [])
	return dialogAnswers.messageAnswer


callLaterQueue = CallLaterQueue()
"""The timers `wx.CallLater` creates. Tests fire them to run the display's reconnect poll."""

displayChanged = Action()
displaySizeChanged = Action()
post_configProfileSwitch = Action()

clipboard = []
"""What was copied to the clipboard, most recent last. Tests read and clear it.

A list rather than a single value because a command may copy more than once, and a test
that only ever sees the last one cannot tell one copy from three.
"""


def _copyToClip(text, notify=False):
	"""Stand in for `api.copyToClip`, which needs a real window handle.

	Answers the way NVDA's does — False for anything that is not a non-empty string — so
	that a caller's handling of a refusal is testable without a clipboard to break.
	"""
	if not isinstance(text, str) or not text:
		return False
	clipboard.append(text)
	return True


spokenMessages: list[str] = []
"""Everything the reader was told, however it reached them."""

spokenPositions: list[tuple] = []
"""Every `speech.speakTextInfo` call, as (info, formatConfig, reason).

What NVDA speaks a table cell with when the reader moves onto it. Recorded rather than
rendered: what matters to the add-on is which position was spoken and under what formatting
settings, and turning a position into words is NVDA's work."""

SCRIPT_STATE = {"waiting": False, "sayAllResuming": False}
"""What `scriptHandler` says about the moment a script is running in.

`isScriptWaiting` is true when keypresses have backed up, which is NVDA's signal to move
without reporting or not to move at all. `willSayAllResume` is true when the key that ran
this script is one that resumes an interrupted say all, in which case the key is not a
request to move at all."""


def _speakTextInfo(info, formatConfig=None, reason=None, **kwargs):
	spokenPositions.append((info, formatConfig, reason))

flashedMessages: list[str] = []
"""What was also written to the display, which is what `ui.message` does and what a message
about a table must not do: the display is showing the table, and a flash sits over it until
the message times out. See `GlobalPlugin.reportAboutTheDisplay`."""


queuedFunctions: list = []
"""What was queued onto NVDA's event queue, so a test can run it when it chooses.

Held rather than run, because *when* something runs is the point wherever this is used.
A message raised from inside a cursor routing press has to land after the press, or
`BrailleHandler.routeTo` dismisses it as the press's own dismissal of a message — which
is a real fault that ran correctly in every test until the queue was modelled.
"""


def _queueFunction(queue, func, *args, **kwargs):
	"""What `queueHandler.queueFunction` does: hold it for the main loop."""
	queuedFunctions.append((func, args, kwargs))


def runQueuedFunctions():
	"""Run what was queued, as NVDA's main loop would.

	:return: how many ran.
	"""
	pending, queuedFunctions[:] = list(queuedFunctions), []
	for func, args, kwargs in pending:
		func(*args, **kwargs)
	return len(pending)


def _flash(text):
	"""What `ui.message` does: speak it and write it to the display."""
	flashedMessages.append(text)
	spokenMessages.append(text)
"""Everything `ui.message` was given, so a test can assert what the user was told."""


class FakeNavigatorObject:
	"""An object that can be pinned to a segment."""

	def __init__(
		self,
		name="an object",
		role="button",
		lines=None,
		treeInterceptor=None,
		documentIndex=None,
	):
		self.name = name
		self.role = role
		self.lines = lines
		self.caretIndex = 0
		self.caretOffset = 0
		self.treeInterceptor = treeInterceptor
		if treeInterceptor is not None and getattr(treeInterceptor, "rootNVDAObject", None) is None:
			# What NVDA does: a tree interceptor is built *over* an object and keeps it. The
			# difference between belonging to a document and being inside one rests on this,
			# and a stand-in without it could not tell a page from something on the page.
			treeInterceptor.rootNVDAObject = self
		self.isFocusable = False
		self.hasFocus = False
		self.focused = False
		"""Set by L{setFocus}, so a test can see that something asked for the focus."""

		self.parent = None
		self.firstChild = None
		self.next = None
		self.previous = None
		self.states = set()
		self.actionCount = 0
		"""The accessibility tree an object flow walks. Present on every object, because a
		run is defined by what shares a parent and the walk goes through `firstChild` and
		`next` rather than through a list of children built all at once."""

		self.positionInfo = {}
		"""Where this object sits among its peers, as NVDA reports it.

		Empty by default, which is what an ordinary list item answers: a level is reported by
		the controls that have a structure — a tree view, a nested list — and by nothing else.
		A flat run must therefore keep reading flat, which is what an empty one tests.
		"""

		self.documentIndex = documentIndex
		"""Which line of its document this object sits on, or None if it cannot be placed.

		A virtual buffer can be asked where an object is — that is how a control is read at
		its own place rather than at a cursor that has gone inside it. An object the buffer
		cannot place raises, which is the ordinary answer for one that is somewhere else.
		"""

	def makeTextInfo(self, position):
		"""Build a position, as an object with text does.

		:raises NotImplementedError: for an object with no text, as NVDA's base does.
		"""
		if not self.lines:
			raise NotImplementedError(f"{self.name!r} has no text")
		return FakeTextInfo(self.lines, self.caretIndex, self.caretOffset)

	def setFocus(self):
		"""Take the system focus, as NVDA asks an object to."""
		self.focused = True
		self.hasFocus = True


class FakeColumnRect:
	"""Where a column of a list view sits on the screen, in what NVDA's own reading reads.

	The width is the whole of it here: a column the reader has hidden keeps its place in the
	list's column count and has no width, and that is how `sysListView32` tells the two apart.
	"""

	def __init__(self, width=100):
		self.left = 0
		self.right = width
		self.width = width


class FakeListItem(FakeNavigatorObject):
	"""A row of a list view, in the shape `NVDAObjects.behaviors.RowWithoutCellObjects` gives.

	Its cells are not objects: they are answered a column at a time by the row itself, which
	is what a list view can do and why NVDA wrote that class. Which row it is comes from
	`positionInfo`, since the platform numbers items rather than table rows.
	"""

	def __init__(self, cells, position, table=None):
		super().__init__(name="; ".join(text for text in cells if text), role="LISTITEM")
		self.cells = list(cells)
		self.positionInfo = {"indexInGroup": position, "similarItemsInGroup": None}
		"""Where NVDA puts an item's number. Not `rowNumber`, which a list view has no answer
		for: the platform numbers items rather than table rows."""

		self.parent = table
		self.reads = []
		"""Which columns were asked for, so a test can say what was *not* read."""

	def _getColumnContent(self, column):
		self.reads.append(column)
		if not 1 <= column <= len(self.cells):
			return None
		return self.cells[column - 1]

	def _getColumnHeader(self, column):
		table = self.parent
		headers = getattr(table, "headers", None) or []
		if not 1 <= column <= len(headers):
			return None
		return headers[column - 1]

	def _getColumnLocation(self, column):
		table = self.parent
		widths = getattr(table, "columnWidths", None)
		if widths is None:
			return None
		if not 1 <= column <= len(widths):
			return None
		return FakeColumnRect(widths[column - 1])

	def getChild(self, index):
		""":return: the cell object for one column, as `RowWithoutCellObjects.getChild` does.

		None outside the row, which is what NVDA's `_makeCell` answers for a column the row
		has not got.
		"""
		column = index + 1
		if not 1 <= column <= self.childCount:
			return None
		return FakeRowCell(self, column)

	@property
	def childCount(self):
		""":return: how many columns the table has, which is what such a row answers.

		`RowWithoutCellObjects._get_childCount` is `self.parent.columnCount` exactly.
		"""
		table = self.parent
		return int(getattr(table, "columnCount", 0) or 0) if table is not None else 0


class FakeRowCell(FakeNavigatorObject):
	"""The cell object a row makes for one of its columns: NVDA's `behaviors._FakeTableCell`.

	Not a control the platform has: `RowWithoutCellObjects.getChild` makes one on demand and
	it answers `name`, `columnHeaderText` and `location` by asking the row the underscored
	questions itself. That indirection is the whole point of it — it is the public way to a
	column of a classic list view — so the stub has it too, and a test that counts what was
	read through the row still sees the reads.

	Not focusable, as NVDA's is not: a column of a list view is a rectangle on the screen
	rather than somewhere the keyboard can be put.
	"""

	def __init__(self, row, column):
		super().__init__(name="", role="TABLECELL")
		self.row = row
		self.columnNumber = column
		self.parent = row
		self.isFocusable = False

	@property
	def name(self):
		return self.row._getColumnContent(self.columnNumber)

	@name.setter
	def name(self, value):
		"""Ignored. `FakeNavigatorObject` sets a name in its constructor and this one is
		answered by the row, which is exactly what NVDA's does."""

	@property
	def columnHeaderText(self):
		return self.row._getColumnHeader(self.columnNumber)

	@property
	def location(self):
		return self.row._getColumnLocation(self.columnNumber)


def _chain(objects):
	"""Link a run of objects by `previous` and `next`, the way a real tree is walked."""
	for index, item in enumerate(objects):
		item.previous = objects[index - 1] if index else None
		item.next = objects[index + 1] if index + 1 < len(objects) else None


class FakeListView(FakeNavigatorObject):
	"""A list view in report mode: rows of cells, and a header control saying what they are.

	`rowCount` and `columnCount` are what NVDA's own list view answers with, each from one
	window message, which is why they are asked for rather than the children being counted.
	"""

	def __init__(self, rows, headers=None, columnWidths=None, name="a list"):
		super().__init__(name=name, role="LIST")
		self.headers = list(headers or [])
		self.columnWidths = columnWidths
		self.items = [FakeListItem(cells, index + 1, table=self) for index, cells in enumerate(rows)]
		_chain(self.items)
		self.firstChild = self.items[0] if self.items else None
		self.builds = 0
		"""How many times a row was fetched by number, so a test can see the caching work."""

	@property
	def rowCount(self):
		return len(self.items)

	@property
	def columnCount(self):
		return len(self.headers) or max((len(item.cells) for item in self.items), default=0)

	@property
	def children(self):
		return list(self.items)

	def getChild(self, index):
		self.builds += 1
		return self.items[index]

	def item(self, row):
		""":return: one row of the list, by its one based number."""
		return self.items[row - 1]


class FakeGridCell(FakeNavigatorObject):
	"""One cell of a grid whose cells are objects, in what NVDA fills in for such a thing.

	`columnNumber` comes from UIA's `GridItemPattern` and `columnHeaderText` from its
	`TableItemPattern`; an IAccessible2 grid fills in the same two from its table cell
	interface. Both are NVDA properties by the time anything here sees them.
	"""

	def __init__(self, text, column, header=None, value=None, focusable=True):
		super().__init__(name=text, role="TABLECELL")
		self.columnNumber = column
		self.columnHeaderText = header
		self.value = value
		self.isFocusable = focusable
		"""Whether the keyboard can be put on this cell, which the two shapes of grid differ
		on. File Explorer's Details view cells are `explorer.UIProperty` and the focus lands
		on one of them when the reader arrows across a file; Outlook's message list rows are
		the focusable thing and their children are text elements that are not."""


class FakeGridRow(FakeNavigatorObject):
	"""A row whose cells are objects, which is `NVDAObjects.behaviors.RowWithFakeNavigation`.

	Outlook's message list rows are this, and so is a row of File Explorer's file list. The
	cells are the children and they carry the table cell properties.
	"""

	def __init__(self, cells, position, table=None, among=None):
		super().__init__(name="; ".join(cell.name for cell in cells if cell.name), role="LISTITEM")
		self.cellObjects = list(cells)
		for cell in self.cellObjects:
			cell.parent = self
		self.positionInfo = {"indexInGroup": position, "similarItemsInGroup": among}
		"""Where NVDA puts "fifty of seventy-nine". The second half is the whole list rather
		than the part of it that has been built, which is the number a virtualised list has to
		be measured by."""

		self.parent = table
		self.builds = 0
		"""How many times this row's cells were walked, so a test can see the caching work."""

	@property
	def children(self):
		self.builds += 1
		return list(self.cellObjects)

	@property
	def childCount(self):
		return len(self.cellObjects)


class FakeGrid(FakeNavigatorObject):
	"""A grid whose cells are objects: File Explorer's file list, Outlook's message list."""

	def __init__(
		self,
		rows,
		headers=None,
		name="a grid",
		columnCount=None,
		realized=None,
		decorations=(),
	):
		"""
		:param rows: a list of rows, each a list of cell texts, or of (name, value) pairs for
			a cell whose name is its column's header — which is how File Explorer presents a
			property of a file.
		:param headers: what each column's header is, as its cells report it.
		:param columnCount: what the grid says its width is. None is a grid that will not say,
			which is the case the row has to be counted for.
		:param realized: how many of the items the platform has actually built. None is a list
			that is all there. A number models File Explorer's, which builds the ones on
			screen and no more: `childCount` answers that number while each item still says
			it is one of the whole list, and asking for an item beyond it answers nothing.
		:param decorations: (name, role) pairs for what a real list holds *before* its rows.
			File Explorer's Details view holds a pane, a horizontal scrollbar and the column
			header; Outlook's message list holds a pane called "Vertical". A stand-in with
			none of those made a row fetched by index look like the row that was asked for,
			which on hardware it was not.
		"""
		super().__init__(name=name, role="LIST")
		self.headers = list(headers or [])
		self.said = columnCount
		self.realized = realized
		self.items = []
		for index, cells in enumerate(rows):
			built = []
			for column, cell in enumerate(cells, start=1):
				header = self.headers[column - 1] if column <= len(self.headers) else None
				if isinstance(cell, tuple):
					built.append(FakeGridCell(cell[0], column, header=header, value=cell[1]))
				else:
					built.append(FakeGridCell(cell, column, header=header))
			self.items.append(FakeGridRow(built, index + 1, table=self, among=len(rows)))
		self.decorations = []
		for label, role in decorations:
			thing = FakeNavigatorObject(name=label, role=role)
			thing.parent = self
			self.decorations.append(thing)
		_chain(self.decorations + self.items)
		# Where a walk of the tree starts, which is how a row is reached without asking the
		# list to build every item. The base sets it to None; a container that holds things
		# says which thing is first.
		self.firstChild = (self.decorations + self.items or [None])[0]
		self.builds = 0
		self.walks = 0
		"""How many times every child was read, so a test can see a bound hold."""

	rowCountSays = None
	"""What this grid answers for `rowCount`, when that is not simply how many rows it has.

	File Explorer's file list answered fourteen while the reader stood on item fifty-two of
	seventy-nine: it counts what it has drawn. A test sets this to model a count that is
	wrong rather than absent, which is the harder of the two to notice."""

	@property
	def rowCount(self):
		if self.rowCountSays is not None:
			return self.rowCountSays
		return None if self.realized is not None else len(self.items)

	@property
	def childCount(self):
		"""How many children there are, without building any of them.

		Counted rather than measured off `children`, because that is what it costs in NVDA: a
		UIA object answers this from its cached children array and builds an object for none
		of them. A stand-in that walked the list to answer would make a bound written against
		the cheap question look like it was paying the dear one.
		"""
		return len(self.decorations) + (len(self.items) if self.realized is None else self.realized)

	@property
	def children(self):
		"""What the grid holds: its decorations, and then such rows as have been built.

		Faithful and load bearing twice over. A stand-in whose children were missing could not
		be mistaken for a row of cells, and being mistaken for one is exactly what a real
		message list did to the reader; a stand-in holding nothing but rows could not hand
		back a scrollbar for row one, which is what a real file list does.

		Counted in `walks`, because reading them all is a call into the application per child
		and a test says where that is worth paying.
		"""
		self.walks += 1
		built = self.items if self.realized is None else self.items[: self.realized]
		return self.decorations + list(built)

	@property
	def columnCount(self):
		return self.said

	def getChild(self, index):
		"""One child by its position, decorations included — which is the point of them.

		A real list hands back what is *at* that index, and what is at index nought is a pane.
		Nothing here rearranges that for the caller's convenience.
		"""
		self.builds += 1
		children = self.children
		if index >= len(children):
			raise LookupError("That item has not been built")
		return children[index]

	def item(self, row):
		""":return: one row of the grid, by its one based number."""
		return self.items[row - 1]


class NVDAObjectRegion(Region):
	"""NVDA's presentation of one object as braille: its name and its role.

	The real one assembles name, role, value, states and position information through
	`getPropertiesBraille`. What matters to the tests above it is that a block of an object
	flow says what NVDA says about that object and nothing of the add-on's own, so the stub
	is that shape reduced to two properties.
	"""

	def __init__(self, obj, appendText=""):
		super().__init__("")
		self.obj = obj
		self.appendText = appendText
		self.cursorPos = None
		self.brailleCursorPos = None
		self.acted = False
		"""Whether a routing press acted on the object, which is NVDA's own behaviour here."""

	def update(self):
		name = getattr(self.obj, "name", "") or ""
		role = getattr(self.obj, "role", "") or ""
		said = f"{name} {role}".strip()
		# Where a table cell is, which `getPropertiesBraille` puts **last** — after
		# everything else it was given, and governed by the reader's own coordinates
		# setting. Reproduced because that position is the whole question the Excel module's
		# header answers: a stub that joined some strings in some order would have agreed
		# with any answer at all. See `appModules.excel.CellHeaders`.
		coords = getattr(self.obj, "cellCoordsText", None)
		if coords and FORMAT_CONFIG.get("reportTableCellCoords"):
			said = f"{said} {coords}".strip()
		# Concatenated, not joined: NVDA appends this to the finished translation, which is
		# what lets a caller put something of its own at the end of the line.
		self.rawText = said + self.appendText
		super().update()
		# One cell per character in this harness, so a text position is a braille position.
		self.brailleCursorPos = self.cursorPos


	def routeTo(self, pos):
		self.acted = True


class ReviewNVDAObjectRegion(NVDAObjectRegion):
	"""The same region when braille follows the review cursor rather than the focus.

	NVDA's own differs in one thing — a routing key focuses the object before acting on it —
	which is nothing this add-on changes. What matters here is that it is a distinct class,
	so that a module choosing between the two can be held to choosing.
	"""


def fakeRun(names, role="LISTITEM", parent=None, selected=0, levels=None):
	"""Build a run of sibling objects, as the items of a list box are.

	A container is made for them when none is given. Every real object has a parent, and a
	run is defined by what shares one: items with no parent at all belong to no run, which
	is right and would make every test here a test of that one fact.

	:param names: what each item is called.
	:param role: the role every item has.
	:param parent: the container to hang them under, which is what a combo box's choices need.
	:param selected: which of them the reader is on.
	:param levels: one-based depth per item, as `positionInfo["level"]` reports it, or None
		for a flat run. A tree view read in visible order is a run of siblings whose levels
		rise and fall, which is what this expresses without building a real tree.
	:return: the items, in order.
	"""
	if parent is None:
		parent = FakeNavigatorObject("a container", role="LIST")
	items = [FakeNavigatorObject(name, role=role) for name in names]
	for index, item in enumerate(items):
		item.next = items[index + 1] if index + 1 < len(items) else None
		item.previous = items[index - 1] if index else None
		item.parent = parent
		item.states = {"SELECTED"} if index == selected else set()
		if levels is not None and index < len(levels):
			item.positionInfo = {"level": levels[index]}
	parent.children = items
	parent.firstChild = items[0] if items else None
	return items


def fakeGroupedList(groups, role="LISTITEM", containerRole="LIST", collapsed=()):
	"""Build a list whose items are grouped, as Outlook's message list is by day.

	The shape is what makes the ordinary sibling walk stop: each group is a `GROUPING` holding
	that day's messages, so the first message of one day and the last of the day before have
	different parents, and between them sits a heading that is not a list item at all.

	:param groups: pairs of (heading, [item names]).
	:param role: the role each item has.
	:param containerRole: what the list calls itself.
	:param collapsed: the headings whose groups are closed, so their items are not rows.
	:return: the list, a dict of heading name to group, and a dict of item name to item.
	"""
	container = FakeNavigatorObject("a list", role=containerRole)
	headings = {}
	items = {}
	built = []
	for heading, names in groups:
		group = FakeNavigatorObject(heading, role="GROUPING")
		group.parent = container
		group.states = {"COLLAPSED"} if heading in collapsed else {"EXPANDED"}
		inside = []
		for name in names:
			item = FakeNavigatorObject(name, role=role)
			item.parent = group
			items[name] = item
			inside.append(item)
		for position, item in enumerate(inside):
			item.next = inside[position + 1] if position + 1 < len(inside) else None
			item.previous = inside[position - 1] if position else None
		group.children = inside
		group.firstChild = inside[0] if inside else None
		headings[heading] = group
		built.append(group)
	for position, group in enumerate(built):
		group.next = built[position + 1] if position + 1 < len(built) else None
		group.previous = built[position - 1] if position else None
	container.children = built
	container.firstChild = built[0] if built else None
	return container, headings, items


def fakeTree(spec, role="TREEVIEWITEM", containerRole="TREEVIEW"):
	"""Build a nested tree of objects, wired the way a real tree control exposes one.

	The point of the wiring is that it is honest about the thing the sibling walk got wrong:
	a node's `next` is its next *sibling*, not the next row on the screen, and its children
	hang off `firstChild` with a different parent. A walk that reads the screen has to put
	those together itself, and a stub that flattened them would test nothing.

	Children are hung off every node that has them, expanded or not, because that is what a
	real tree control does — `sysTreeView32` builds them from the window's item handles,
	which do not care what is on screen. Whether they are *rows* is the state's business.

	:param spec: nested `(name, expanded, children)` triples.
	:param role: the role each item has.
	:param containerRole: what the tree control calls itself.
	:return: the tree control and a dict of name to object.
	"""
	tree = FakeNavigatorObject("a tree", role=containerRole)
	index = {}

	def build(items, parent, level):
		built = []
		for name, expanded, children in items:
			node = FakeNavigatorObject(name, role=role)
			node.parent = parent
			node.positionInfo = {"level": level}
			if children:
				node.states = {"EXPANDED"} if expanded else {"COLLAPSED"}
			index[name] = node
			built.append(node)
			kids = build(children, node, level + 1)
			node.firstChild = kids[0] if kids else None
			node.children = kids
		for position, node in enumerate(built):
			node.next = built[position + 1] if position + 1 < len(built) else None
			node.previous = built[position - 1] if position else None
		return built

	roots = build(spec, tree, 1)
	tree.firstChild = roots[0] if roots else None
	tree.children = roots
	return tree, index


def wrapChildren(node, role="GROUPING"):
	"""Put a wrapper between a node and its children, the way some providers do.

	A tree item's children are not always its direct children: UIA hangs them off a grouping
	in between, and so do some IA2 implementations. To a walk that reads `firstChild` and
	expects another tree item, that grouping is the end of the tree.

	:param node: the node whose children to wrap.
	:param role: what the wrapper calls itself.
	:return: the wrapper.
	"""
	kids = list(node.children or ())
	group = FakeNavigatorObject("a group", role=role)
	group.parent = node
	group.children = kids
	group.firstChild = kids[0] if kids else None
	for kid in kids:
		kid.parent = group
	node.children = [group]
	node.firstChild = group
	return group


def fakeSeparator(after, name="-----", role="SEPARATOR"):
	"""Put a separator into a run, between an item and whatever followed it.

	:param after: the item to place it after.
	:param name: what the toolkit calls it, which for a line is usually its dashes.
	:param role: its role, so that a toolkit exposing the line as a disabled menu item can
		be tested too.
	:return: the separator.
	"""
	separator = FakeNavigatorObject(name, role=role)
	separator.parent = after.parent
	separator.previous = after
	separator.next = after.next
	if after.next is not None:
		after.next.previous = separator
	after.next = separator
	return separator


def fakeGetFocusRegions(obj, review=False):
	"""Stand in for NVDA's `getFocusRegions`, with the same shape.

	A label region always, and a text region after it when the object has text to read —
	a cursor managed one for a tree interceptor, as NVDA produces for browse mode.
	"""
	regions = [Region(str(getattr(obj, "name", None) or "document"))]
	if isinstance(obj, FakeTreeInterceptor):
		regions.append(CursorManagerRegion(obj))
	elif getattr(obj, "lines", None):
		regions.append(TextInfoRegion(obj))
	for region in regions:
		region.update()
	return regions


class FakeVirtualBufferClass:
	"""NVDA's `VirtualBuffer`, as far as the patches touch it.

	Only `_handleUpdate` matters: it is where NVDA learns that a browse mode document changed,
	and the add-on wraps it to hear the same news. A class rather than a stand-in object because
	the patch is installed on the class, which is the thing being tested.
	"""

	def __init__(self):
		self.updates = 0

	def _handleUpdate(self):
		self.updates += 1


def _module(name, **attributes):
	module = types.ModuleType(name)
	for key, value in attributes.items():
		setattr(module, key, value)
	sys.modules[name] = module
	return module


navigatedTo = []
"""What `api.setNavigatorObject` was called with, newest last.

NVDA's own way of taking the reader to a cell that cannot be focused: `RowWithFakeNavigation`
focuses the row and moves the navigator object the rest of the way. See
`flowObjectTable.ObjectCellInfo.updateCaret`.
"""


def _setNavigatorObject(obj, *args, **kwargs) -> bool:
	navigatedTo.append(obj)
	return True


def _installPluginStubs() -> None:
	"""Register the modules the global plugin and its settings panel reach for.

	These are heavier than the braille stubs and exist only so that `__init__.py` can be
	imported and driven. Nothing here models NVDA's behaviour; it models its shape.
	"""
	import builtins

	# addonHandler.initTranslation installs the gettext underscore into builtins.
	builtins._ = lambda message: message  # type: ignore[attr-defined]
	_module("addonHandler", initTranslation=lambda: None)
	_module(
		"api",
		getFocusObject=lambda: FakeNavigatorObject("the focus"),
		getNavigatorObject=lambda: FakeNavigatorObject("the navigator object"),
		setNavigatorObject=_setNavigatorObject,
		copyToClip=_copyToClip,
	)
	_module("ui", message=_flash)
	_module("queueHandler", eventQueue=object(), queueFunction=_queueFunction)
	_module("speech", speakMessage=spokenMessages.append, speakTextInfo=_speakTextInfo)
	_module("virtualBuffers", VirtualBuffer=FakeVirtualBufferClass)
	_module(
		"wx",
		CallAfter=callAfterQueue.callAfter,
		CallLater=callLaterQueue.callLater,
		SpinCtrl=object,
		TextCtrl=object,
		CheckBox=object,
		StaticText=object,
		Window=object,
		SingleChoiceDialog=FakeSingleChoiceDialog,
		MultiChoiceDialog=FakeMultiChoiceDialog,
		OK=1,
		ICON_ERROR=2,
		ID_OK=ID_OK,
		ID_CANCEL=ID_CANCEL,
		YES=YES,
		NO=NO,
		YES_NO=YES_NO,
		ICON_WARNING=ICON_WARNING,
		ICON_QUESTION=ICON_QUESTION,
	)

	class GlobalPlugin:
		def __init__(self):
			pass

		def terminate(self):
			pass

	_module("globalPluginHandler", GlobalPlugin=GlobalPlugin)

	class SettingsPanel:
		pass

	class NVDASettingsDialog:
		categoryClasses = []

	class BlockAction:
		class Context:
			MODAL_DIALOG_OPEN = "modal"

		def when(self, *args, **kwargs):
			return lambda function: function

	settingsDialogs = types.SimpleNamespace(
		SettingsPanel=SettingsPanel,
		NVDASettingsDialog=NVDASettingsDialog,
	)
	_module(
		"gui",
		settingsDialogs=settingsDialogs,
		blockAction=BlockAction(),
		mainFrame=mainFrame,
		messageBox=_messageBox,
		guiHelper=types.SimpleNamespace(BoxSizerHelper=object),
	)
	_module("gui.guiHelper", BoxSizerHelper=object)
	_module(
		"scriptHandler",
		script=scriptDecorator,
		isScriptWaiting=lambda: SCRIPT_STATE["waiting"],
		willSayAllResume=lambda gesture: SCRIPT_STATE["sayAllResuming"],
	)
	_module("keyboardHandler", keyCounter=0)
	_module(
		"controlTypes",
		Role=lambda role: types.SimpleNamespace(displayString=str(role)),
		OutputReason=types.SimpleNamespace(CARET="caret", FOCUS="focus", QUERY="query"),
	)
	_module("braille.extensions", displayChanged=displayChanged, displaySizeChanged=displaySizeChanged)
	_module("braille.brailleHandler", BrailleHandler=FakeBrailleHandler)
	_module(
		"braille.constants",
		CONTEXTPRES_CHANGEDCONTEXT="changedContext",
		CONTINUATION_SHAPE=0xC0,
		TEXT_SEPARATOR=TEXT_SEPARATOR,
	)
	_module("braille.regions.focus", getFocusRegions=fakeGetFocusRegions)
	_module(
		"braille.labels",
		roleLabels=ROLE_LABELS,
		positiveStateLabels=POSITIVE_STATE_LABELS,
		negativeStateLabels=NEGATIVE_STATE_LABELS,
	)
	_module("cursorManager", CursorManager=CursorManager)

	class EditableText:
		"""NVDA's editable text behaviour, which `flowForms` recognises by isinstance.

		Only its identity matters: it is how NVDA records "this object is typed into" on
		controls whose role and states fail to say so, Windows 11 Notepad's editor being
		the one the hardware found.
		"""

	_module("editableText", EditableText=EditableText)
	_module(
		"braille.regions.NVDAObject",
		NVDAObjectRegion=NVDAObjectRegion,
		ReviewNVDAObjectRegion=ReviewNVDAObjectRegion,
	)
	# The braille display driver package, as a path with no code, so that the settings panel's
	# `from brailleDisplayDrivers.brlMultilineVirtual import vdConfig` resolves to the real
	# module without running the driver's `__init__`, which wants `hwIo` and `inputCore`.
	# `vdConfig` and `virtualLayout` reach for nothing beyond the configuration and the log,
	# so the panel is tested against the same device list the driver actually reads.
	drivers = types.ModuleType("brailleDisplayDrivers")
	drivers.__path__ = []
	sys.modules["brailleDisplayDrivers"] = drivers
	virtualDriver = types.ModuleType("brailleDisplayDrivers.brlMultilineVirtual")
	virtualDriver.__path__ = [DRIVER_DIR]
	sys.modules["brailleDisplayDrivers.brlMultilineVirtual"] = virtualDriver
	drivers.brlMultilineVirtual = virtualDriver
	_module(
		"config.configFlags",
		BrailleMode=types.SimpleNamespace(SPEECH_OUTPUT=types.SimpleNamespace(value="speechOutput")),
		TetherTo=types.SimpleNamespace(FOCUS=types.SimpleNamespace(value="focus")),
		ReportTableHeaders=ReportTableHeaders,
	)


def scriptDecorator(**kwargs):
	"""NVDA's `scriptHandler.script`, in what it leaves behind.

	A passthrough would be simpler and would hide the mistake this exists to catch: a script
	whose decorator has gone astray still runs perfectly and simply never appears in Input
	Gestures, so there is nothing to notice until a reader goes looking for the command and
	cannot find it. NVDA records the description as the function's docstring and the category
	beside it, so that is what this records.
	"""

	def decorate(function):
		function.__doc__ = kwargs.get("description")
		function.category = kwargs.get("category")
		function.gestures = list(kwargs.get("gestures") or ())
		return function

	return decorate


class CallCancelled(Exception):
	"""NVDA's own, for the modules that catch it.

	Defined here rather than imported because NVDA is not present: what matters is that one
	class stands for it everywhere in a test run, so that a raise in a stand-in and a catch in
	the add-on are talking about the same thing.
	"""


def installStubs() -> None:
	"""Register the stand-in modules. Safe to call more than once."""
	if PACKAGE in sys.modules:
		return
	_module("logHandler", log=log)
	# NVDA's own, and the real class rather than the add-on's fallback for it: a test that
	# raises this is testing what the watchdog does to a COM call, and it only tests it if
	# what the add-on catches is what the test raised. See `flow.CallCancelled`.
	_module("exceptions", CallCancelled=CallCancelled)
	# Registered here rather than only beside the driver stubs: the plugin reaches for the
	# composite display's own notification, and which stub module a test installed should not
	# decide whether it finds one.
	_module("extensionPoints", Action=Action, callWithSupportedKwargs=callWithSupportedKwargs)
	_module("inputCore", decide_executeGesture=decide_executeGesture, manager=InputManager())
	# The two panning commands the add-on wraps, on the class it wraps them on.
	_module("globalCommands", GlobalCommands=GlobalCommands, commands=GlobalCommands())
	_module("baseObject", AutoPropertyObject=AutoPropertyObject, ScriptableObject=ScriptableObject)
	_module(
		"config",
		conf=FakeConf(
			{
				"BrlMultiline": {"displays": DisplaysSection()},
				"BrlMultilineVirtualDisplay": {"devices": []},
				"braille": BRAILLE_CONFIG,
				"documentFormatting": FORMAT_CONFIG,
			},
		),
		post_configProfileSwitch=post_configProfileSwitch,
		ConfigManager=ConfigManager,
	)
	braille = _module("braille", handler=None)
	buffers = _module("braille.buffers", BrailleBuffer=BrailleBuffer, _WindowRowPositions=WindowRowPositions)
	display = _module("braille.display", DisplayDimensions=DisplayDimensions)
	gestureModule = _module("braille.display.gesture", BrailleDisplayGesture=BrailleDisplayGesture)
	regions = _module("braille.regions")
	regionsBase = _module("braille.regions.base", Region=Region, TextRegion=TextRegion)
	_module(
		"documentBase",
		DocumentWithTableNavigation=FakeTableDocument,
		_Axis=Axis,
		_Movement=Movement,
	)
	regionsTextInfo = _module(
		"braille.regions.textInfo",
		TextInfoRegion=TextInfoRegion,
		CursorManagerRegion=CursorManagerRegion,
	)
	_module(
		"textInfos",
		UNIT_LINE=UNIT_LINE,
		UNIT_PARAGRAPH=UNIT_PARAGRAPH,
		POSITION_SELECTION=POSITION_SELECTION,
		POSITION_FIRST=POSITION_FIRST,
		TextInfo=FakeTextInfo,
		FieldCommand=FieldCommand,
		ControlField=ControlField,
		Field=Field,
		FormatField=FormatField,
	)
	braille.buffers = buffers
	braille.display = display
	display.gesture = gestureModule
	braille.regions = regions
	regions.base = regionsBase
	regions.textInfo = regionsTextInfo
	_installPluginStubs()
	# A package object with a path but no code, so that the add-on's relative imports
	# resolve without running its plugin entry point, which needs a great deal more of NVDA.
	package = types.ModuleType(PACKAGE)
	package.__path__ = [ADDON_DIR]
	sys.modules[PACKAGE] = package
	# The real bmConfig reads NVDA's configuration through several layers. Point the two
	# functions the view layer uses at the dictionary above instead.
	import importlib

	bmConfig = importlib.import_module(f"{PACKAGE}.bmConfig")
	# Kept before the replacements below, so that the tests of bmConfig itself can reach the
	# real ones. The functions not replaced are kept too, so those tests have one place to
	# read all of them from.
	realBmConfig.update(
		{
			name: getattr(bmConfig, name)
			for name in (
				"getDisplayKey",
				"getDisplayConfig",
				"getLayout",
				"getFocusSegment",
				"setFocusSegment",
				"shouldShowDocumentLines",
				"shouldReverseScrollButtons",
				"areSegmentsEnabled",
				"setLayout",
				"setSegmentsEnabled",
			)
		},
	)

	def setSegmentsEnabled(enabled, displayKey=None):
		_sectionFor(displayKey)["segmentsEnabled"] = bool(enabled)

	def getLayout(displayKey=None):
		section = _sectionFor(displayKey)
		return section["segmentSizes"] or section["segmentCount"]

	def setFocusSegment(number, displayKey=None):
		_sectionFor(displayKey)["focusSegment"] = int(number)

	bmConfig.getLayout = getLayout
	bmConfig.getFocusSegment = lambda displayKey=None: _sectionFor(displayKey)["focusSegment"]
	bmConfig.setFocusSegment = setFocusSegment
	bmConfig.shouldShowDocumentLines = lambda displayKey=None: _sectionFor(displayKey)["showDocumentLines"]
	bmConfig.shouldReverseScrollButtons = lambda displayKey=None: _sectionFor(displayKey)["reverseScrollBtns"]
	bmConfig.areSegmentsEnabled = lambda displayKey=None: _sectionFor(displayKey)["segmentsEnabled"]
	bmConfig.setSegmentsEnabled = setSegmentsEnabled

	def setTableLayouts(said):
		CONFIG["tableLayouts"] = said

	# Saved table layouts live in the add-on's own section rather than under a display — they
	# are choices about a table and not about hardware — so they are read from L{CONFIG}
	# directly, and `resetConfig` empties them between tests as it empties everything else.
	bmConfig.tableLayouts = lambda: CONFIG["tableLayouts"]
	bmConfig.setTableLayouts = setTableLayouts


def loadPlugin():
	"""Import the add-on's `__init__.py` as a module, and return it.

	The package stand-in registered by L{installStubs} has a path but no code, precisely so
	that importing `brlMultiline.layout` does not drag in the global plugin. Testing the
	plugin means loading that code deliberately.

	It is loaded under the name `brlMultiline.plugin`, which puts its package at
	`brlMultiline` so its relative imports resolve. Its module level names are then copied
	onto the package stand-in, because `patches` reaches back for `getPlugin` with
	`from . import getPlugin`, and in a real installation the package and the plugin module
	are the same object.

	:return: the loaded module.
	"""
	import importlib.util

	name = f"{PACKAGE}.plugin"
	if name in sys.modules:
		return sys.modules[name]
	spec = importlib.util.spec_from_file_location(name, os.path.join(ADDON_DIR, "__init__.py"))
	assert spec is not None and spec.loader is not None
	# The file is named __init__.py, so importlib would otherwise treat it as a package in
	# its own right, and its relative imports would resolve to `brlMultiline.plugin.layout`
	# rather than `brlMultiline.layout` — a second copy of every module, whose classes fail
	# every isinstance check against the first.
	spec.submodule_search_locations = None
	module = importlib.util.module_from_spec(spec)
	module.__package__ = PACKAGE
	sys.modules[name] = module
	spec.loader.exec_module(module)
	package = sys.modules[PACKAGE]
	for attribute in ("getPlugin", "GlobalPlugin", "SCRIPT_CATEGORY"):
		setattr(package, attribute, getattr(module, attribute))
	return module


def resetConfig() -> None:
	"""Put the stub configuration back to its defaults, for a test that changed it."""
	CONFIG.clear()
	CONFIG.profiles = None
	CONFIG.update(
		segmentsEnabled=True,
		segmentCount=4,
		segmentSizes=[],
		focusSegment=-1,
		reverseScrollBtns=False,
		showDocumentLines=False,
		flowEnabled=False,
		flowBrowseMode=True,
		flowObjects=False,
		flowEditableText=False,
		flowRows=0,
		flowDisplay="",
		flowGroundOnQuickNav=True,
		flowWriteByParagraph=True,
		flowScrollToNewContent=True,
		flowIndentStyle="twoSpaces",
		# The two that may defer to NVDA. Spelled out rather than left missing, because
		# missing reads as "could not be read" and that is a different path.
		flowTableHeadersMode="follow",
		flowTablePinKeyMode="follow",
		# Saved table layouts. Empty, so a test that wants one saves it as a reader would.
		tableLayouts="",
	)
	BAND_CONFIG.clear()
	# Both are filled in by the real `getDisplayConfig` as displays are met, so a test that
	# met one starts the next test with a display already known to the configuration.
	CONFIG.spec.clear()
	if "config" in sys.modules:
		conf = sys.modules["config"].conf
		# Every profile but the base one, or one test's profile is the next test's surprise.
		# Saved as well as active: a profile on disk outlives being deactivated.
		conf.deactivateProfiles()
		conf.savedProfiles.clear()
		conf["BrlMultiline"]["displays"].clear()
		# The combined display's member list, which the settings panel and the plugin both
		# read through the driver's own `vdConfig`. Cleared in both places it can live, since
		# whether the section is base only depends on whether `vdConfig` has been imported.
		conf.profiles[0].setdefault("BrlMultilineVirtualDisplay", {})["devices"] = []
		dict.__getitem__(conf, "BrlMultilineVirtualDisplay")["devices"] = []
	setSpeechOutputMode(False)
	FORMAT_CONFIG.update(
		reportTables=True,
		includeLayoutTables=False,
		reportTableHeaders=ReportTableHeaders.ROWS_AND_COLUMNS.value,
		reportTableCellCoords=True,
	)


def resetPluginState() -> None:
	"""Clear everything the plugin stubs accumulate between tests."""
	resetConfig()
	callAfterQueue.discard()
	callLaterQueue.pending.clear()
	dialogAnswers.reset()
	mainFrame.prePopups = mainFrame.postPopups = 0
	mainFrame.settingsDialogs.clear()
	spokenMessages.clear()
	flashedMessages.clear()
	log.messages.clear()
	displayChanged.handlers.clear()
	displaySizeChanged.handlers.clear()
	post_configProfileSwitch.handlers.clear()
	decide_executeGesture.handlers.clear()
	sys.modules["gui"].settingsDialogs.NVDASettingsDialog.categoryClasses.clear()
