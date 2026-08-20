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

	def __missing__(self, key):
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
		"flowRows": 0,
		"flowDisplay": "",
		"flowGroundOnQuickNav": True,
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
			"flowRows": 0,
			"flowDisplay": "",
			"flowGroundOnQuickNav": True,
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
}
"""NVDA's own braille settings, which the add-on reads through the real `bmConfig`.

Unlike L{CONFIG} these are not redirected, so a test changing the mode here changes what
`bmConfig.isSpeechOutputMode` answers. Use L{setSpeechOutputMode} rather than writing to it.
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
	"""A region whose braille is simply its text, one cell per character."""

	def __init__(self, text=""):
		self.rawText = text
		self.brailleCells = [ord(character) & 0xFF for character in text]
		self.hidden = False
		self.obj = None
		# NVDA's own Region carries this, and `_doNewObject` reads and writes it.
		self.focusToHardLeft = False

	def update(self):
		self.brailleCells = [ord(character) & 0xFF for character in self.rawText]

	def routeTo(self, pos):
		self.routedTo = pos

	def __repr__(self):
		return f"<Region {self.rawText!r}>"


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
		return [region for region in self.regions if not region.hidden]

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
POSITION_SELECTION = "selection"
POSITION_FIRST = "first"


@dataclasses.dataclass
class FakeBookmark:
	"""A mark for a position, shaped like NVDA's: comparable, and not hashable."""

	story: int
	index: int


class FakeTextInfo:
	"""A caret in a list of lines, moving by whole lines and stopping at the ends."""

	def __init__(self, lines, index):
		self.lines = lines
		self.index = index

	@property
	def text(self):
		return self.lines[self.index] if 0 <= self.index < len(self.lines) else ""

	def copy(self):
		return FakeTextInfo(self.lines, self.index)

	@property
	def bookmark(self):
		"""A comparable mark for this position, as NVDA's TextInfo carries.

		Unhashable on purpose. NVDA's own bookmark for a virtual buffer is
		`textInfos.offsets.Offsets`, a plain dataclass, so it defines `__eq__` and Python
		sets its `__hash__` to None. Anything keying a dictionary by a bookmark works
		against a tuple and raises against the real thing, which is a failure worth having
		in the tests rather than on a display.
		"""
		return FakeBookmark(id(self.lines), self.index)

	def collapse(self, end=False):
		pass

	def expand(self, unit):
		"""Cover the whole unit, which this info already does: its text is a whole line."""

	def move(self, unit, count):
		"""Move by whole lines, reporting how far it actually got, as NVDA's TextInfo does."""
		target = self.index + count
		clamped = max(0, min(len(self.lines) - 1, target))
		moved = clamped - self.index
		self.index = clamped
		return moved


class FakeDocument:
	"""An object whose caret sits on one line of a fixed list."""

	def __init__(self, lines, caretIndex=0):
		self.lines = lines
		self.caretIndex = caretIndex

	def makeTextInfo(self, position):
		return FakeTextInfo(self.lines, self.caretIndex)


class CursorManager:
	"""Stands in for NVDA's `CursorManager`, which browse mode documents are.

	Only its identity matters here: it is what a flow checks to decide that an object reads
	through a cursor of its own rather than through a caret.
	"""


class FieldCommand:
	"""NVDA's `textInfos.FieldCommand`: a marker in a run of text saying a field starts or ends.

	Here because `flowForms` recognises a form control from the fields of the text itself,
	and does so with an `isinstance` check, so a stand-in has to be the type the code tests
	against rather than something shaped like it.
	"""

	def __init__(self, command, field=None):
		self.command = command
		self.field = field


class ControlField(dict):
	"""NVDA's `textInfos.ControlField`, which is a dictionary carrying a role and more."""


class FakeTreeInterceptor(CursorManager):
	"""A browse mode document, which reads through a cursor of its own rather than a caret."""

	def __init__(self, lines, caretIndex=0, isReady=True, passThrough=False):
		self.lines = lines
		self.caretIndex = caretIndex
		self.isReady = isReady
		self.passThrough = passThrough

	@property
	def selection(self):
		return FakeTextInfo(self.lines, self.caretIndex)

	@selection.setter
	def selection(self, info):
		self.caretIndex = info.index

	def makeTextInfo(self, position):
		"""Build a position, as a tree interceptor does.

		A position may be an object rather than one of NVDA's position constants: that is how
		a virtual buffer is asked where something sits in its document, and it is what reads a
		form control at its own place rather than at a cursor that has gone inside it. An
		object this document cannot place raises `LookupError`, as NVDA's own does.
		"""
		if isinstance(position, str):
			return FakeTextInfo(self.lines, self.caretIndex)
		index = getattr(position, "documentIndex", None)
		if index is None:
			raise LookupError(f"{position!r} is not in this document")
		return FakeTextInfo(self.lines, index)


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
		return FakeTextInfo(self.obj.lines, self.obj.caretIndex)

	def _setCursor(self, info):
		self.obj.caretIndex = info.index

	def _getDefaultRegionLanguage(self):
		return "en"

	def update(self):
		info = self._readingInfo = self._getSelection()
		self.rawText = info.text
		Region.update(self)
		# A collapsed position is a cursor, as it is in NVDA. Regions that must not show one
		# clear it after calling this, which is the behaviour worth being able to test.
		# Where in the line it sits is the object's business: a caret at the end of what has
		# just been typed is what makes a growing edit field testable, and the default of
		# nought is where every other test leaves it.
		# Clamped to a cell that exists, as NVDA clamps it: there the reading unit gains a
		# trailing space so that a caret at its end has somewhere to be, and the cursor is
		# then held inside the text. A cursor past the last cell is not a state a region
		# ever reaches, so it is not one a test should be able to produce.
		self.cursorPos = min(getattr(self.obj, "caretOffset", 0), max(0, len(self.rawText) - 1))
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


def fakeVirtualDisplay(*bands, configured=None):
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
	:return: the stand-in display.
	"""
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

callLaterQueue = CallLaterQueue()
"""The timers `wx.CallLater` creates. Tests fire them to run the display's reconnect poll."""

displayChanged = Action()
displaySizeChanged = Action()
post_configProfileSwitch = Action()

spokenMessages: list[str] = []
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
		self.treeInterceptor = treeInterceptor
		self.isFocusable = False
		self.hasFocus = False
		self.focused = False
		"""Set by L{setFocus}, so a test can see that something asked for the focus."""

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
		return FakeTextInfo(self.lines, self.caretIndex)

	def setFocus(self):
		"""Take the system focus, as NVDA asks an object to."""
		self.focused = True
		self.hasFocus = True


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
		self.rawText = f"{name} {role}".strip() + self.appendText
		super().update()
		# One cell per character in this harness, so a text position is a braille position.
		self.brailleCursorPos = self.cursorPos

	def routeTo(self, pos):
		self.acted = True


def fakeRun(names, role="LISTITEM", parent=None, selected=0):
	"""Build a run of sibling objects, as the items of a list box are.

	:param names: what each item is called.
	:param role: the role every item has.
	:param parent: the container to hang them under, which is what a combo box's choices need.
	:param selected: which of them the reader is on.
	:return: the items, in order.
	"""
	items = [FakeNavigatorObject(name, role=role) for name in names]
	for index, item in enumerate(items):
		item.next = items[index + 1] if index + 1 < len(items) else None
		item.previous = items[index - 1] if index else None
		item.parent = parent
		item.states = {"SELECTED"} if index == selected else set()
	if parent is not None:
		parent.children = items
	return items


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


def _module(name, **attributes):
	module = types.ModuleType(name)
	for key, value in attributes.items():
		setattr(module, key, value)
	sys.modules[name] = module
	return module


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
	)
	_module("ui", message=spokenMessages.append)
	_module(
		"wx",
		CallAfter=callAfterQueue.callAfter,
		CallLater=callLaterQueue.callLater,
		SpinCtrl=object,
		TextCtrl=object,
		CheckBox=object,
		StaticText=object,
		Window=object,
		OK=1,
		ICON_ERROR=2,
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
		mainFrame=None,
		messageBox=lambda *args, **kwargs: None,
		guiHelper=types.SimpleNamespace(BoxSizerHelper=object),
	)
	_module("gui.guiHelper", BoxSizerHelper=object)
	_module("scriptHandler", script=lambda **kwargs: (lambda function: function))
	_module("keyboardHandler", keyCounter=0)
	_module("controlTypes", Role=lambda role: types.SimpleNamespace(displayString=str(role)))
	_module("braille.extensions", displayChanged=displayChanged, displaySizeChanged=displaySizeChanged)
	_module("braille.brailleHandler", BrailleHandler=FakeBrailleHandler)
	_module(
		"braille.constants",
		CONTEXTPRES_CHANGEDCONTEXT="changedContext",
		CONTINUATION_SHAPE=0xC0,
	)
	_module("braille.regions.focus", getFocusRegions=fakeGetFocusRegions)
	_module("cursorManager", CursorManager=CursorManager)
	_module("braille.regions.NVDAObject", NVDAObjectRegion=NVDAObjectRegion)
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
	)


def installStubs() -> None:
	"""Register the stand-in modules. Safe to call more than once."""
	if PACKAGE in sys.modules:
		return
	_module("logHandler", log=log)
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
	regionsBase = _module("braille.regions.base", Region=Region)
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

	bmConfig.getLayout = getLayout
	bmConfig.getFocusSegment = lambda displayKey=None: _sectionFor(displayKey)["focusSegment"]
	bmConfig.shouldShowDocumentLines = lambda displayKey=None: _sectionFor(displayKey)["showDocumentLines"]
	bmConfig.shouldReverseScrollButtons = lambda displayKey=None: _sectionFor(displayKey)["reverseScrollBtns"]
	bmConfig.areSegmentsEnabled = lambda displayKey=None: _sectionFor(displayKey)["segmentsEnabled"]
	bmConfig.setSegmentsEnabled = setSegmentsEnabled


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
		flowRows=0,
		flowDisplay="",
		flowGroundOnQuickNav=True,
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


def resetPluginState() -> None:
	"""Clear everything the plugin stubs accumulate between tests."""
	resetConfig()
	callAfterQueue.discard()
	callLaterQueue.pending.clear()
	spokenMessages.clear()
	log.messages.clear()
	displayChanged.handlers.clear()
	displaySizeChanged.handlers.clear()
	post_configProfileSwitch.handlers.clear()
	decide_executeGesture.handlers.clear()
	sys.modules["gui"].settingsDialogs.NVDASettingsDialog.categoryClasses.clear()
