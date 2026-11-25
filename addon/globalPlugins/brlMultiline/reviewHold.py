# BrlMultiline: holding the reader's place in a terminal while they review its output.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Holding the reader's place in a terminal against output while they review it.

A terminal moves its caret for every line of output, and NVDA answers each caret move by taking an
automatic tether back to focus and, with "review cursor follows system caret" on, by moving the
review cursor to the caret. So a reader reviewing output loses their place once a line. The hold
stops both while the reader is reading:

1. A review move the reader makes in a terminal the flow band is showing starts it: a review
   command, a pan, a routing press. `noteReaderReviewMove` hears these from `handleReviewMove`.
2. While it holds, `patches` keeps a caret move in the held terminal from changing the tether or
   moving the review cursor. See `isHolding`.
3. Any gesture that is not reading ends it, so typing, or any other command, gives NVDA its caret
   back. `keepsTheHold` says which are reading. A change of tether to focus ends it too.

See docs/design/review-flow-plan.md, decision 8.
"""

from logHandler import log

from . import flowCursor

READING_PREFIXES = ("review_", "navigatorObject_", "scroll", "flowPanLines")
"""What a reading command's script is named, without `script_`: NVDA's review and object navigation
commands, and the add-on's own segment scrolling and line panning."""

READING_BRAILLE = frozenset(
	{
		"braille_scrollBack",
		"braille_scrollForward",
		"braille_routeTo",
		"braille_previousLine",
		"braille_nextLine",
		"braille_reportFormatting",
		"braille_toggleTether",
	},
)
"""NVDA's braille commands that read the display. The rest of its braille commands are typing on a
braille keyboard, settings, or `braille_toFocus`, which takes the reader back to the caret. A
change of tether to focus ends the hold by itself."""

_held = None
"""The terminal whose review position is held, or None."""


def isHolding(obj) -> bool:
	""":return: whether the hold is on, over this object."""
	held = _held
	if held is None or obj is None:
		return False
	if obj is held:
		return True
	try:
		return bool(obj == held)
	except Exception:
		return False


def held():
	""":return: the terminal being held, or None."""
	return _held


def end() -> None:
	"""End the hold, giving NVDA its caret back."""
	global _held
	if _held is not None:
		log.debug("BrlMultiline: the review hold ends")
	_held = None


def noteReaderReviewMove() -> None:
	"""Answer a review move the reader made, holding their place if it is in a terminal the band shows.

	A move anywhere else ends the hold.
	"""
	global _held
	obj = flowCursor.reviewObject()
	if obj is not None and isTerminal(obj) and _isFlowing(obj):
		if _held is None:
			log.debug("BrlMultiline: holding the review position against output")
		_held = obj
	else:
		end()


def isTerminal(obj) -> bool:
	""":return: whether an object is a terminal, by NVDA's own `Terminal` behaviour."""
	try:
		from NVDAObjects.behaviors import Terminal
	except ImportError:
		return False
	return isinstance(obj, Terminal)


def _isFlowing(obj) -> bool:
	""":return: whether the flow band is showing this object."""
	from . import getPlugin

	plugin = getPlugin()
	band = getattr(plugin, "flowBand", None) if plugin is not None else None
	control = getattr(band, "controller", None)
	source = getattr(control, "source", None)
	if source is None:
		return False
	read = getattr(source, "obj", None)
	if read is obj:
		return True
	try:
		return bool(read == obj)
	except Exception:
		return False


def keepsTheHold(gesture) -> bool:
	"""Whether a gesture is the reader reading, which keeps the hold on.

	:param gesture: the gesture NVDA is about to execute.
	:return: whether its command is a reading command. A gesture with no command, such as typing,
		is not.
	"""
	try:
		script = gesture.script
	except Exception:
		log.debugWarning("BrlMultiline: could not find a gesture's command", exc_info=True)
		return False
	name = getattr(script, "__name__", "") if script is not None else ""
	name = name.removeprefix("script_")
	return bool(name) and (name in READING_BRAILLE or name.startswith(READING_PREFIXES))


def decideExecuteGesture(gesture) -> bool:
	"""NVDA's `decide_executeGesture` handler: end the hold on any gesture that is not reading.

	:return: True always, since the gesture is never stopped, only noticed.
	"""
	if _held is not None:
		try:
			if not keepsTheHold(gesture):
				end()
		except Exception:
			log.debugWarning("BrlMultiline: could not check a gesture against the review hold", exc_info=True)
	return True


def install() -> None:
	"""Start hearing gestures. Safe to call more than once."""
	try:
		import inputCore
	except ImportError:
		return
	decider = getattr(inputCore, "decide_executeGesture", None)
	if decider is not None:
		decider.register(decideExecuteGesture)


def remove() -> None:
	"""Stop hearing gestures and end the hold."""
	end()
	try:
		import inputCore
	except ImportError:
		return
	decider = getattr(inputCore, "decide_executeGesture", None)
	if decider is not None:
		try:
			decider.unregister(decideExecuteGesture)
		except Exception:
			log.debugWarning("BrlMultiline: could not stop hearing gestures", exc_info=True)
