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

import os
import sys
import types

ADDON_DIR = os.path.abspath(
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "addon", "globalPlugins", "brlMultiline"),
)

PACKAGE = "brlMultiline"

CONFIG = {
	"segmentCount": 4,
	"segmentSizes": [],
	"focusSegment": -1,
	"reverseScrollBtns": False,
	"showDocumentLines": False,
}
"""The stub configuration the fake `bmConfig` reads. Tests mutate this directly."""


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


class DisplayDimensions:
	def __init__(self, numRows, numCols):
		self.numRows = numRows
		self.numCols = numCols

	@property
	def displaySize(self):
		return self.numRows * self.numCols


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

	def update(self):
		self.brailleCells = [ord(character) & 0xFF for character in self.rawText]

	def routeTo(self, pos):
		self.routedTo = pos

	def __repr__(self):
		return f"<Region {self.rawText!r}>"


class BrailleBuffer:
	"""The smallest buffer that answers everything the container asks of a segment."""

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

	@property
	def visibleRegions(self):
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

	@property
	def windowEndPos(self):
		return min(len(self.brailleCells), self.windowStartPos + self.handler.displaySize)

	def _set_windowEndPos(self, endPos):
		self.windowStartPos = max(0, endPos - self.handler.displaySize)

	def _calculateWindowRowBufferOffsets(self, pos):
		self._windowRowBufferOffsets = []

	def _isMidWordCut(self, end, bufferEnd):
		return False

	@property
	def windowBrailleCells(self):
		return self.brailleCells[self.windowStartPos : self.windowEndPos]

	@property
	def windowRawText(self):
		return self.rawText[self.windowStartPos : self.windowEndPos]

	@property
	def cursorWindowPos(self):
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

	def collapse(self, end=False):
		pass

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


class TextInfoRegion(Region):
	"""Renders whatever line its object's caret is on."""

	def __init__(self, obj):
		super().__init__("")
		self.obj = obj

	def _getSelection(self):
		return FakeTextInfo(self.obj.lines, self.obj.caretIndex)

	def _getDefaultRegionLanguage(self):
		return "en"

	def update(self):
		self.rawText = self._getSelection().text
		Region.update(self)

	def routeTo(self, pos):
		self.routedTo = pos

	def nextLine(self):
		self.panned = "next"

	def previousLine(self, start=False):
		self.panned = "previous"


class FakeHandler:
	"""Stands in for `BrailleHandler` well enough to build a container against."""

	def __init__(self, numRows=8, numCols=32):
		self.displayDimensions = DisplayDimensions(numRows, numCols)
		self.buffer = None
		self.mainBuffer = None
		self.messageBuffer = None
		self.updateCount = 0

	@property
	def displaySize(self):
		return self.displayDimensions.displaySize

	def update(self):
		self.updateCount += 1


def _module(name, **attributes):
	module = types.ModuleType(name)
	for key, value in attributes.items():
		setattr(module, key, value)
	sys.modules[name] = module
	return module


def installStubs() -> None:
	"""Register the stand-in modules. Safe to call more than once."""
	if PACKAGE in sys.modules:
		return
	_module("logHandler", log=log)
	_module("baseObject", AutoPropertyObject=AutoPropertyObject, ScriptableObject=object)
	_module("config", conf={"BrlMultiline": {"displays": {"stub": CONFIG}}})
	braille = _module("braille", handler=None)
	buffers = _module("braille.buffers", BrailleBuffer=BrailleBuffer, _WindowRowPositions=WindowRowPositions)
	display = _module("braille.display", DisplayDimensions=DisplayDimensions)
	regions = _module("braille.regions")
	regionsBase = _module("braille.regions.base", Region=Region)
	regionsTextInfo = _module("braille.regions.textInfo", TextInfoRegion=TextInfoRegion)
	_module("textInfos", UNIT_LINE=UNIT_LINE, TextInfo=FakeTextInfo)
	braille.buffers = buffers
	braille.display = display
	braille.regions = regions
	regions.base = regionsBase
	regions.textInfo = regionsTextInfo
	# A package object with a path but no code, so that the add-on's relative imports
	# resolve without running its plugin entry point, which needs a great deal more of NVDA.
	package = types.ModuleType(PACKAGE)
	package.__path__ = [ADDON_DIR]
	sys.modules[PACKAGE] = package
	# The real bmConfig reads NVDA's configuration through several layers. Point the two
	# functions the view layer uses at the dictionary above instead.
	import importlib

	bmConfig = importlib.import_module(f"{PACKAGE}.bmConfig")
	bmConfig.getLayout = lambda displayKey=None: CONFIG["segmentSizes"] or CONFIG["segmentCount"]
	bmConfig.getFocusSegment = lambda displayKey=None: CONFIG["focusSegment"]
	bmConfig.shouldShowDocumentLines = lambda displayKey=None: CONFIG["showDocumentLines"]
	bmConfig.shouldReverseScrollButtons = lambda displayKey=None: CONFIG["reverseScrollBtns"]


def resetConfig() -> None:
	"""Put the stub configuration back to its defaults, for a test that changed it."""
	CONFIG.update(
		segmentCount=4,
		segmentSizes=[],
		focusSegment=-1,
		reverseScrollBtns=False,
		showDocumentLines=False,
	)
