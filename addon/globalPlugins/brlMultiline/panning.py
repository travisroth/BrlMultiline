# BrlMultiline: which display's panning keys were pressed.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Reversing the panning keys, per physical display.

Reversal exists because the two panning keys sit differently on different hardware: on a
Focus 80 the left hand key is the comfortable one for moving forward, on a Monarch the normal
arrangement suits. That is a fact about the *keys*, so when several displays are driven as
one, the setting that applies is the one belonging to the display whose key was pressed —
not the display holding the segment that ends up scrolling. Press the Monarch's panning key
while the focus segment is on the Focus and it is still the Monarch's keys under your fingers.

Finding out which display that was is the whole of this module, and it is more awkward than
it looks. `inputCore.decide_executeGesture` runs on the thread the driver dispatched from —
`hwIo.ioThread.IoThread` for both of the target displays — while
`InputManager.executeGesture` ends with `scriptHandler.queueScript(script, gesture)`, which
runs the script on the **main** thread some time later. So the gesture is known on one thread
and needed on another, and a thread local of the kind `brlMultilineVirtual.gestures` uses
would simply be empty by the time it was read.

Hence a plain module global holding the source of the most recent braille gesture. It is not
"the gesture being executed", and it is not pretending to be: it is the last display the user
touched. For panning that is exact, because panning is always a key press and the script runs
directly after its own gesture. It is inexact only for scrolling that no key caused, which
means automatic scroll, where the reversal has nothing to reverse.
"""

import braille
import inputCore
from logHandler import log

from . import bmConfig, devices

_lastSource: str | None = None
"""The driver name of the most recent braille gesture, or None if there has not been one.

Deliberately not a thread local; see the module docstring. Deliberately not cleared after a
gesture either, because there is nothing to clear it from: the value's meaning is "the display
last used", which stays true until another key is pressed.
"""

_installed = False


def _noteGestureSource(gesture=None, **kwargs) -> bool:
	"""Remember which display raised a gesture. Registered on `decide_executeGesture`.

	Never decides anything: it always returns True, and any error is swallowed, because a
	decider that raises would break input for the whole session and the worst this can get
	wrong is which way a panning key goes.
	"""
	global _lastSource
	try:
		from braille.display.gesture import BrailleDisplayGesture

		if isinstance(gesture, BrailleDisplayGesture):
			_lastSource = gesture.source
	except Exception:
		log.debugWarning("BrlMultiline: could not read a gesture's display", exc_info=True)
	return True


def install() -> None:
	"""Start following which display is being used."""
	global _installed
	if _installed:
		return
	inputCore.decide_executeGesture.register(_noteGestureSource)
	_installed = True


def remove() -> None:
	"""Stop. Safe to call when nothing is installed."""
	global _installed, _lastSource
	if not _installed:
		return
	inputCore.decide_executeGesture.unregister(_noteGestureSource)
	_installed = False
	_lastSource = None


def displayKeyForLastGesture() -> str | None:
	""":return: the configuration key of the display last used, or None if it is not a member.

	None for an ordinary display, which is the common case and needs no resolution: there is
	only one display, and its key is the current one.
	"""
	if _lastSource is None:
		return None
	for device in devices.deviceMap():
		if device.driverName == _lastSource:
			return device.displayKey
	return None


def shouldReverse() -> bool:
	""":return: whether the panning keys should be swapped for the display being used.

	Falls back to the connected display's own setting when the display cannot be identified,
	which is every case that is not a composite — so a single display behaves exactly as it
	did before this existed.
	"""
	return bmConfig.shouldReverseScrollButtons(displayKeyForLastGesture())


def describeCurrent() -> str:
	""":return: a short description of what reversal is in force, for reporting and the log."""
	displayKey = displayKeyForLastGesture()
	if displayKey is None:
		handler = braille.handler
		displayKey = bmConfig.getDisplayKey() if handler is not None else "no display"
	return f"{displayKey}: {'reversed' if shouldReverse() else 'normal'}"
