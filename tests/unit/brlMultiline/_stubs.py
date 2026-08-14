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

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.spec = {}

	def __missing__(self, key):
		return _specDefault(self.spec[key])


class DisplaysSection(dict):
	"""The `__many__` section holding the settings of each display.

	NVDA does not materialise one of these subsections on demand: reading a display that
	has never been written raises `KeyError`, so anything wanting one has to create it
	first, and the stub insists on that too. Tests care about the settings rather than the
	key, so every display created resolves to the one stub section.
	"""

	def isSet(self, key) -> bool:
		return key in self

	def __setitem__(self, key, value):
		if not isinstance(value, dict):
			raise ValueError("Value must be a section")
		super().__setitem__(key, CONFIG)


CONFIG = DisplaySection(
	{
		"segmentsEnabled": True,
		"segmentCount": 4,
		"segmentSizes": [],
		"focusSegment": -1,
		"reverseScrollBtns": False,
		"showDocumentLines": False,
	},
)
"""The stub configuration the fake `bmConfig` reads. Tests mutate this directly."""

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

	@windowEndPos.setter
	def windowEndPos(self, endPos):
		# Through the method rather than assigning here, so that a subclass overriding
		# `_set_windowEndPos` is honoured, as NVDA's auto properties honour it.
		self._set_windowEndPos(endPos)

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
		"""A comparable mark for this position, as NVDA's TextInfo carries."""
		return (id(self.lines), self.index)

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


class FakeTreeInterceptor:
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

	def routeTo(self, pos):
		self.routedTo = pos

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


def fakeVirtualDisplay(*bands):
	"""A stand-in for the add-on's own braille display driver, which is several displays.

	Shaped the way `devices.deviceMap` reads it — a name, and a slot per member carrying its
	driver name and its band — and no more than that, because nothing above the driver has any
	business reading more.

	:param bands: each member's (driverName, rowStart, numRows, numCols), top first.
	:return: the stand-in display.
	"""
	return types.SimpleNamespace(
		name="brlMultilineVirtual",
		slots=tuple(
			types.SimpleNamespace(
				driverName=driverName,
				band=types.SimpleNamespace(rowStart=rowStart, numRows=numRows, numCols=numCols),
			)
			for driverName, rowStart, numRows, numCols in bands
		),
	)


class FakeConf(dict):
	"""NVDA's configuration object: a mapping that also carries the registered specs."""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.spec = {}


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

	def _doNewObject(self, regions):
		self.lastNewObject = list(regions)

	def scrollForward(self):
		self.scrolledForward = True

	def scrollBack(self):
		self.scrolledBack = True

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

displayChanged = Action()
displaySizeChanged = Action()
post_configProfileSwitch = Action()

spokenMessages: list[str] = []
"""Everything `ui.message` was given, so a test can assert what the user was told."""


class FakeNavigatorObject:
	"""An object that can be pinned to a segment."""

	def __init__(self, name="an object", role="button", lines=None, treeInterceptor=None):
		self.name = name
		self.role = role
		self.lines = lines
		self.caretIndex = 0
		self.treeInterceptor = treeInterceptor


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
	_module("braille.constants", CONTEXTPRES_CHANGEDCONTEXT="changedContext")
	_module("braille.regions.focus", getFocusRegions=fakeGetFocusRegions)
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
	)
	braille = _module("braille", handler=None)
	buffers = _module("braille.buffers", BrailleBuffer=BrailleBuffer, _WindowRowPositions=WindowRowPositions)
	display = _module("braille.display", DisplayDimensions=DisplayDimensions)
	regions = _module("braille.regions")
	regionsBase = _module("braille.regions.base", Region=Region)
	regionsTextInfo = _module(
		"braille.regions.textInfo",
		TextInfoRegion=TextInfoRegion,
		CursorManagerRegion=CursorManagerRegion,
	)
	_module("textInfos", UNIT_LINE=UNIT_LINE, TextInfo=FakeTextInfo)
	braille.buffers = buffers
	braille.display = display
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
	)
	BAND_CONFIG.clear()
	# Both are filled in by the real `getDisplayConfig` as displays are met, so a test that
	# met one starts the next test with a display already known to the configuration.
	CONFIG.spec.clear()
	if "config" in sys.modules:
		conf = sys.modules["config"].conf
		conf["BrlMultiline"]["displays"].clear()
		# The combined display's member list, which the settings panel and the plugin both
		# read through the driver's own `vdConfig`. One test's list must not be the next's.
		conf["BrlMultilineVirtualDisplay"]["devices"] = []
	setSpeechOutputMode(False)


def resetPluginState() -> None:
	"""Clear everything the plugin stubs accumulate between tests."""
	resetConfig()
	callAfterQueue.discard()
	spokenMessages.clear()
	log.messages.clear()
	displayChanged.handlers.clear()
	displaySizeChanged.handlers.clear()
	post_configProfileSwitch.handlers.clear()
	sys.modules["gui"].settingsDialogs.NVDASettingsDialog.categoryClasses.clear()
