# BrlMultiline: a scripted run through an edit field, with the flow's state at each step.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Type into the focused control by script, and record what the flow band did at each step.

A diagnostic, not part of the add-on. Copy it into NVDA's developer scratchpad:

	%APPDATA%\\nvda\\scratchpad\\globalPlugins\\brlMultilineFlowProbe.py

and turn on "Enable loading custom code from Developer Scratchpad Directory" in NVDA's
Advanced settings. Then focus the control to be tested and press NVDA+control+shift+p. The
report is written to the log and put on the clipboard when the run finishes.

Why a script rather than a description of what happened. Reading an edit field is a
conversation between four things that each move on their own schedule: the control's caret,
the events NVDA raises about it, the core cycle that drains those events, and the band that
answers them. A report of what the display showed cannot say which of the four was late,
and every guess made from one so far has been wrong in a different place. This records all
four at every step, so the answer is in the file rather than in an inference.

Two rules the run obeys, both because breaking either invalidates the result:

**Nothing here takes the focus.** Every step is a keystroke sent to whatever is focused, and
the steps are chained through `core.callLater` rather than run in a loop, so NVDA's main
thread is free between them. A loop with sleeps in it would hold the thread that has to
process the very events being measured, and the run would record its own blockage.

**Nothing here touches the flow.** The band is read, never driven. What is recorded is what
the add-on did on its own, in response to keystrokes a person could have typed.
"""

import ctypes
from ctypes import wintypes

import api
import core
import globalPluginHandler
import globalVars
import textInfos
import ui
from keyboardHandler import KeyboardInputGesture
from logHandler import log
from scriptHandler import script

STEP_SECONDS = 0.45
"""How long to leave between steps.

Long enough for the application to act on the keystroke, for NVDA to raise its events, and
for the core cycle to drain them into the display. Too short and the run records the state
before the step it has just taken, which is exactly the confusion it exists to remove.
"""

CATEGORY = "BrlMultiline"

STEPS = [
	("type", "This is line 1."),
	("key", "enter"),
	("type", "Now line 2."),
	("key", "enter"),
	("type", "And line 3."),
	("key", "upArrow"),
	("key", "upArrow"),
	("key", "upArrow"),
	("key", "downArrow"),
	("key", "control+a"),
	("type", "Hello"),
]
"""What the run does, in order.

The reader's own report, turned into keystrokes: three lines typed with a return between
them, then arrowing back up through them one at a time — which is where the cursor was seen
to answer a keypress late — then select all and overtype, which is where lines that no
longer exist were seen to stay on the display.

Edit this list to chase something else. A "type" step sends its text a character at a time;
a "key" step is anything `KeyboardInputGesture.fromName` accepts.
"""


# Typing.

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class _KeyboardInput(ctypes.Structure):
	_fields_ = [
		("wVk", wintypes.WORD),
		("wScan", wintypes.WORD),
		("dwFlags", wintypes.DWORD),
		("time", wintypes.DWORD),
		("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
	]


class _MouseInput(ctypes.Structure):
	_fields_ = [
		("dx", wintypes.LONG),
		("dy", wintypes.LONG),
		("mouseData", wintypes.DWORD),
		("dwFlags", wintypes.DWORD),
		("time", wintypes.DWORD),
		("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
	]


class _HardwareInput(ctypes.Structure):
	_fields_ = [
		("uMsg", wintypes.DWORD),
		("wParamL", wintypes.WORD),
		("wParamH", wintypes.WORD),
	]


class _InputUnion(ctypes.Union):
	_fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput), ("hi", _HardwareInput)]
	"""All three members, though only the keyboard one is ever filled in.

	`SendInput` is given the size of the structure and refuses anything that is not the size
	it knows. A union holding only the keyboard member is smaller than the real one — a
	mouse event is wider — so every call was rejected and the first run of this probe typed
	nothing at all while its named keys went through a different path and worked."""


class _Input(ctypes.Structure):
	_fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


_sendInput = ctypes.windll.user32.SendInput
_sendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
_sendInput.restype = wintypes.UINT
"""Declared rather than left to ctypes to guess, because the pointer and the count are
different widths on a 64 bit build and a guessed one is a crash rather than a wrong key."""


def sendCharacter(character: str) -> None:
	"""Type one character into whatever has the focus.

	Sent as a Unicode event rather than as a key, so that punctuation and capitals arrive as
	themselves whatever the keyboard layout is. `KeyboardInputGesture` is the right tool for
	a named key and the wrong one for text.

	:param character: the character to type.
	"""
	events = []
	for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
		event = _Input()
		event.type = INPUT_KEYBOARD
		event.union.ki = _KeyboardInput(0, ord(character), flags, 0, None)
		events.append(event)
	array = (_Input * len(events))(*events)
	sent = _sendInput(len(events), array, ctypes.sizeof(_Input))
	if sent != len(events):
		# Said out loud rather than left to be inferred from an empty field, which is how the
		# first run of this probe spent itself.
		log.error(f"The flow probe could not type {character!r}: {ctypes.WinError()}")


def sendKey(name: str) -> None:
	"""Press a named key, as the reader would.

	:param name: anything `KeyboardInputGesture.fromName` accepts: "enter", "upArrow",
		"control+a".
	"""
	KeyboardInputGesture.fromName(name).send()


# Reading what happened.


def flowBand():
	""":return: the add-on's flow band, or None if the add-on is not running one."""
	for plugin in globalPluginHandler.runningPlugins:
		band = getattr(plugin, "flowBand", None)
		if band is not None:
			return band
	return None


def describePosition(info) -> str:
	""":return: where a position is, in whatever terms its document counts in."""
	if info is None:
		return "none"
	try:
		return repr(info.bookmark)
	except Exception:
		return repr(info)


def describeFocus(lines: list) -> None:
	"""Record what the focus is and where its caret is.

	The control's own account, taken directly rather than through the flow, so that a report
	can say whether the flow was wrong or was faithfully showing something that was already
	wrong before it got there.
	"""
	try:
		obj = api.getFocusObject()
	except Exception:
		lines.append("  focus: could not be read")
		return
	states = sorted(state.name for state in getattr(obj, "states", ()) or () if hasattr(state, "name"))
	interceptor = getattr(obj, "treeInterceptor", None)
	lines.append(f"  focus: {type(obj).__name__} role={getattr(obj, 'role', None)} name={obj.name!r}")
	lines.append(f"  states: {', '.join(states) or 'none'}")
	if interceptor is not None:
		lines.append(
			f"  treeInterceptor: {type(interceptor).__name__} "
			f"passThrough={getattr(interceptor, 'passThrough', None)}",
		)
	else:
		lines.append("  treeInterceptor: none")
	try:
		caret = obj.makeTextInfo(textInfos.POSITION_SELECTION)
		lines.append(f"  caret: {describePosition(caret)} collapsed={caret.isCollapsed}")
		try:
			caret.bookmark
			lines.append("  bookmarks: yes")
		except Exception as error:
			# Whether a position can be marked decides whether a block can be recognised
			# again. A document without them made every reading of one line a new block.
			lines.append(f"  bookmarks: no ({error!r})")
	except Exception as error:
		lines.append(f"  caret: unreadable ({error!r})")
	try:
		whole = obj.makeTextInfo(textInfos.POSITION_ALL)
		lines.append(f"  text: {whole.text!r}")
	except Exception as error:
		lines.append(f"  text: unreadable ({error!r})")


def describeUnits(lines: list) -> None:
	"""Record what the control calls a line, and what it calls a paragraph, at the caret.

	The question a row of merged lines raises and the flow cannot answer about itself. A
	block is a reading unit, and if the control answers "the line here" with two lines then
	the band is showing faithfully what it was told. Both units, because if one of them is
	sound the setting that chooses between them is a fix without any code.
	"""
	try:
		obj = api.getFocusObject()
	except Exception:
		return
	for name, unit in (("line", textInfos.UNIT_LINE), ("paragraph", textInfos.UNIT_PARAGRAPH)):
		try:
			info = obj.makeTextInfo(textInfos.POSITION_CARET)
			info.expand(unit)
			lines.append(f"  the {name} at the caret: {info.text!r}")
		except Exception as error:
			lines.append(f"  the {name} at the caret: unreadable ({error!r})")


def describeWhatNVDAWouldShow(lines: list) -> None:
	"""Record what NVDA would put on a display of its own, for the same object.

	The control against which every row of the band should be read. A band that shows
	something odd is either presenting the control wrongly or presenting faithfully what the
	control said, and those want opposite fixes.
	"""
	try:
		from braille.regions.focus import getFocusRegions

		obj = api.getFocusObject()
		for region in getFocusRegions(obj, review=False):
			region.update()
			lines.append(f"  NVDA would show: {type(region).__name__} {region.rawText!r}")
	except Exception as error:
		lines.append(f"  NVDA would show: unreadable ({error!r})")


def describeFlow(lines: list) -> None:
	"""Record what the band is reading, and every row and block of it."""
	band = flowBand()
	if band is None:
		lines.append("  flow: the add-on is not running a band")
		return
	control = getattr(band, "controller", None)
	if control is None:
		lines.append(f"  flow: claimed={band.isClaimed} but nothing is flowing")
		return
	source = control.source
	lines.append(
		f"  flow: reading {type(source.obj).__name__} by {getattr(source, 'unit', '?')}, "
		f"writing={getattr(source, 'writing', None)} interactive={getattr(source, 'interactive', None)}",
	)
	lines.append(f"  active block: {control.activeBlockId}")
	lines.append(f"  cursor cell: {control.cursorCell()}")
	try:
		for row in control.describeRows():
			lines.append(f"    row {row}")
	except Exception as error:
		lines.append(f"    rows unreadable ({error!r})")
	for index, rendered in enumerate(control.window.blocks):
		block = control.blocks.get(rendered.blockId)
		region = getattr(block, "region", None)
		lines.append(
			f"    block {index}: rows={rendered.numRows} offset={rendered.rowOffset} id={rendered.blockId}",
		)
		if region is None:
			lines.append("      region: none held")
			continue
		lines.append(
			f"      region: obj={type(getattr(region, 'obj', None)).__name__} "
			f"active={getattr(region, 'isActive', None)} dirty={getattr(region, 'dirty', None)} "
			f"cursor={getattr(region, 'brailleCursorPos', None)}",
		)
		lines.append(f"      pinned at: {describePosition(getattr(region, 'position', None))}")
		lines.append(f"      text: {getattr(region, 'rawText', '')!r}")


def describeCost(lines: list) -> None:
	""":return: what the reading has cost so far, from the budget's own record."""
	band = flowBand()
	control = getattr(band, "controller", None) if band is not None else None
	budget = getattr(getattr(control, "source", None), "budget", None)
	if budget is None:
		return
	for line in budget.describe():
		lines.append(f"  cost: {line}")


class Probe:
	"""One run through `STEPS`, recording the state after each of them."""

	def __init__(self) -> None:
		self.lines: list[str] = []
		self.index = 0

	def start(self) -> None:
		self.lines = ["BrlMultiline flow probe", ""]
		self.record("before anything")
		self.next()

	def record(self, what: str) -> None:
		"""Write down the whole state, under a heading saying what has just happened."""
		self.lines.append(f"=== {what}")
		try:
			describeFocus(self.lines)
			describeUnits(self.lines)
			describeWhatNVDAWouldShow(self.lines)
			describeFlow(self.lines)
		except Exception:
			log.error("The flow probe could not read the state", exc_info=True)
			self.lines.append("  (reading the state raised; see the log)")
		self.lines.append("")

	def next(self) -> None:
		"""Take the next step, then come back for the one after it.

		Chained rather than looped: the main thread has to be free between steps or the
		events this is measuring never arrive.
		"""
		if self.index >= len(STEPS):
			self.finish()
			return
		kind, value = STEPS[self.index]
		self.index += 1
		try:
			if kind == "type":
				for character in value:
					sendCharacter(character)
			else:
				sendKey(value)
		except Exception:
			log.error(f"The flow probe could not send {kind} {value!r}", exc_info=True)
			self.lines.append(f"=== {kind} {value!r} could not be sent; see the log")
		core.callLater(int(STEP_SECONDS * 1000), self.after, f"{kind} {value!r}")

	def after(self, what: str) -> None:
		self.record(what)
		self.next()

	def finish(self) -> None:
		describeCost(self.lines)
		report = "\n".join(self.lines)
		log.info(f"BrlMultiline flow probe:\n{report}")
		try:
			api.copyToClip(report)
			ui.message("Flow probe finished and copied to the clipboard")
		except Exception:
			log.error("The flow probe could not reach the clipboard", exc_info=True)
			ui.message("Flow probe finished; it is in the log")


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	"""The command that starts a run."""

	@script(
		description="Runs the BrlMultiline flow probe over the focused control",
		category=CATEGORY,
		gesture="kb:NVDA+control+shift+p",
	)
	def script_flowProbe(self, gesture):
		if globalVars.appArgs.secure:
			return
		ui.message("Flow probe starting, do not touch the keyboard")
		# Given a moment, so that the keystroke that started this is out of the way before
		# the first step is sent.
		core.callLater(int(STEP_SECONDS * 1000), Probe().start)
