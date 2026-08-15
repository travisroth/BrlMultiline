# BrlMultiline: which display's panning keys were pressed.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Panning keys belong to the display they are on.

Two things follow from that, and both were found on hardware rather than reasoned out.

**A display's panning keys should pan that display.** NVDA's own panning commands end in
`BrailleHandler.scrollForward`, which scrolls the buffer's focus segment — right on one
display, and wrong the moment there are two, because the Monarch's keys then scroll whatever
the Focus is showing. Each display's keys now drive its own segment: the focus segment if
that display holds it, otherwise that display's first segment.

**Reversal belongs to the keys too.** Which of the two keys means "forward" is a fact about
how they sit on a piece of hardware, so the setting that applies is the pressed display's,
not that of the display holding the segment being scrolled.

Both need to know which display was pressed, and NVDA's hook does not say.
`BrailleHandler.scrollForward` takes no gesture, so the display has to be noted when the
gesture passes and read back when the scroll happens. That cannot be a thread local:
`decide_executeGesture` runs on the thread the driver dispatched from, while
`InputManager.executeGesture` ends in `scriptHandler.queueScript`, which runs the script on
the main thread.

So it is a module global — but a narrow one. Only gestures actually bound to NVDA's two
braille scrolling commands are recorded, and the record is consumed by the scroll that
follows. A routing key press can no longer leave a display behind for a later scroll to act
on, which matters much more now that the value picks a segment rather than only a direction.

Commands that already hold their gesture do not use the global at all; they pass it in.
"""

import inputCore
from logHandler import log

from . import bmConfig, devices

NATIVE_SCROLL_SCRIPTS = frozenset(
	{
		"script_braille_scrollForward",
		"script_braille_scrollBack",
	},
)
"""NVDA's own panning commands, by name.

Matched by name rather than by identity so that nothing here has to import `globalCommands`,
which is a large import to take at braille time for two strings. A user who has rebound their
panning keys to this add-on's own commands takes the other path entirely, since those receive
their gesture directly.
"""

_pendingSource: str | None = None
"""The display whose panning key was pressed and whose scroll has not happened yet."""

_installed = False


def _isNativeScroll(gesture) -> bool:
	""":return: whether a gesture is bound to one of NVDA's panning commands.

	Resolving `gesture.script` here is not the extra work it looks like: `executeGesture`
	reads the same property a few lines later, and gestures cache their properties, so the
	answer is computed once either way.
	"""
	script = gesture.script
	return script is not None and getattr(script, "__name__", None) in NATIVE_SCROLL_SCRIPTS


def _noteGestureSource(gesture=None, **kwargs) -> bool:
	"""Remember the display behind a panning key. Registered on `decide_executeGesture`.

	Never decides anything: it always returns True, and any error is swallowed, because a
	decider that raises would break input for the whole session and the worst this can get
	wrong is which segment a panning key moves.
	"""
	global _pendingSource
	try:
		from braille.display.gesture import BrailleDisplayGesture

		if isinstance(gesture, BrailleDisplayGesture) and _isNativeScroll(gesture):
			_pendingSource = gesture.source
	except Exception:
		log.debugWarning("BrlMultiline: could not read a gesture's display", exc_info=True)
	return True


def install() -> None:
	"""Start following which display's panning keys are being pressed."""
	global _installed
	if _installed:
		return
	inputCore.decide_executeGesture.register(_noteGestureSource)
	_installed = True


def remove() -> None:
	"""Stop. Safe to call when nothing is installed."""
	global _installed, _pendingSource
	if not _installed:
		return
	inputCore.decide_executeGesture.unregister(_noteGestureSource)
	_installed = False
	_pendingSource = None


def takePendingSource() -> str | None:
	""":return: the display whose panning key caused the scroll about to happen, or None.

	Consumed, so that one key press drives one scroll. Anything scrolling without a key press
	behind it — automatic scroll — gets None and the behaviour it had before any of this.
	"""
	global _pendingSource
	source, _pendingSource = _pendingSource, None
	return source


def displayKeyForSource(source: str | None) -> str | None:
	""":return: a member display's configuration key, or None if it is not one.

	None for an ordinary display, which needs no resolution: there is one display and its key
	is the current one.
	"""
	if source is None:
		return None
	for device in devices.deviceMap():
		if device.driverName == source:
			return device.displayKey
	return None


def shouldReverseForSource(source: str | None) -> bool:
	""":return: whether the panning keys are swapped on the display that was pressed.

	Falls back to the connected display's own setting when the display cannot be identified,
	so a single display behaves exactly as it did before any of this existed.
	"""
	return bmConfig.shouldReverseScrollButtons(displayKeyForSource(source))


def shouldReverseForGesture(gesture) -> bool:
	""":return: whether to swap the keys, for a command that holds its own gesture.

	:param gesture: the gesture that ran the command, or None if there is not one.
	"""
	return shouldReverseForSource(getattr(gesture, "source", None))


def nativeSegmentForSource(source: str | None, container) -> int | None:
	"""Find the segment a display's own panning keys should drive.

	The focus segment when that display holds it, which keeps NVDA's arrangement intact for
	the display the reader is following; otherwise that display's first segment, which is
	what makes a second display's keys useful rather than confusing.

	:param source: the display whose key was pressed, or None if unknown.
	:param container: the display container.
	:return: the segment's index, or None to leave the scroll to NVDA. None covers every case
		that is not a composite, so an ordinary display is untouched by any of this.
	"""
	if source is None or container is None:
		return None
	deviceMap = devices.deviceMap()
	for device in deviceMap:
		if device.driverName != source:
			continue
		segments = devices.segmentsForDevice(container.rects, device)
		if not segments:
			return None
		if container.focusSegmentNumber in segments:
			return container.focusSegmentNumber
		return segments[0]
	return None
