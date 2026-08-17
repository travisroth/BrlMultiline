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

Both need to know which display was pressed, and the method that does the scrolling does not
say: `BrailleHandler.scrollForward` takes no gesture. What does have the gesture is the
command that calls it — NVDA's own `script_braille_scrollForward` and `script_braille_scrollBack`
— so those two are wrapped, and each records the display it was given for as long as it runs.
`_nativeScroll` in `patches` reads it from inside that call.

**Why not the gesture itself, when it passes.** The first version of this registered on
`inputCore.decide_executeGesture` and remembered the display there. It looks like the natural
hook and it is the wrong one, for two reasons that both come from what `executeGesture` does
after deciding. It does not run the script: it calls `scriptHandler.queueScript`, so the
script runs later, on the main thread, while the decider ran on the thread the driver
dispatched from. Two displays are two threads, so a pan on each could be decided before
either script ran and the second would overwrite the first, leaving one scroll acting on the
wrong display and the other on none. And several paths between the decider and the script
abandon the gesture entirely — input help captures it, sleep mode refuses it, a modifier
raises — each leaving a display recorded that no scroll would ever collect, for the next
scroll with no gesture behind it, such as automatic scroll, to pick up instead.

Wrapping the script puts the record and its only reader in one synchronous call on one
thread, which is why what follows can be a plain module global with no locking and no
consuming: it is set on entry and restored on exit, and nothing else can be running in
between.

Commands of this add-on's own already hold their gesture and never touch the global; they
pass it in.
"""

import functools

from logHandler import log

from . import bmConfig, devices

NATIVE_SCROLL_SCRIPTS = (
	"script_braille_scrollForward",
	"script_braille_scrollBack",
)
"""NVDA's own panning commands, by attribute name on `globalCommands.GlobalCommands`.

Those two names are what every braille display driver's own gesture map binds its panning
keys to, through the `braille_scrollForward` and `braille_scrollBack` script names, so
wrapping them catches the keys on every member of a composite without knowing anything about
the hardware. A user who has rebound their panning keys to this add-on's own commands takes
the other path entirely, since those receive their gesture directly.
"""

_activeSource: str | None = None
"""The display whose panning key is running the scroll that is happening now."""

_originals: dict[str, object] = {}
"""NVDA's own commands, kept to be put back."""

_installed = False


def _withSource(original):
	"""Wrap one of NVDA's panning commands so that it says which display ran it.

	:param original: the command as NVDA defines it.
	:return: the replacement, indistinguishable from it to everything that inspects a script.
	"""

	@functools.wraps(original)
	def scrollWithSource(commands, gesture):
		global _activeSource
		# Restored rather than cleared, because a script that sends its gesture on can end up
		# running another script inside this one.
		previous = _activeSource
		_activeSource = getattr(gesture, "source", None)
		try:
			return original(commands, gesture)
		finally:
			_activeSource = previous

	return scrollWithSource


def install() -> None:
	"""Start following which display's panning keys are being pressed."""
	global _installed
	if _installed:
		return
	try:
		import globalCommands

		commands = globalCommands.GlobalCommands
		# Built before anything is replaced, so a failure leaves NVDA's commands untouched.
		wrapped = {name: _withSource(getattr(commands, name)) for name in NATIVE_SCROLL_SCRIPTS}
	except Exception:
		# Panning then keeps NVDA's own behaviour: every scroll reports no display, which is
		# the answer for a single display and the answer this add-on gave before any of this.
		log.error(
			"BrlMultiline: could not follow which display's panning keys are pressed; "
			"panning keys will pan the segment following the focus, whichever display they are on",
			exc_info=True,
		)
		return
	for name, wrapper in wrapped.items():
		_originals[name] = getattr(commands, name)
		setattr(commands, name, wrapper)
	_installed = True


def remove() -> None:
	"""Stop. Safe to call when nothing is installed."""
	global _installed, _activeSource
	if not _installed:
		return
	try:
		import globalCommands

		for name, original in _originals.items():
			setattr(globalCommands.GlobalCommands, name, original)
	except Exception:
		log.error("BrlMultiline: could not restore NVDA's panning commands", exc_info=True)
	finally:
		# Whatever happened above, this module is no longer following anything, and saying so
		# is what lets a later install try again.
		_originals.clear()
		_installed = False
		_activeSource = None


def sourceForNativeScroll() -> str | None:
	""":return: the display whose panning key is driving the scroll happening now, or None.

	None whenever the scroll has no key press behind it — automatic scroll, or anything else
	calling the handler directly — and that answer means NVDA's own behaviour, unchanged.
	"""
	return _activeSource


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
