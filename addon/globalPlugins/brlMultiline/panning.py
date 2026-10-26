# BrlMultiline: which display's panning keys were pressed.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Panning keys belong to the display they are on.

1. A display's panning keys pan that display: its focus segment if it holds it, else its first.
2. Reversal is the pressed display's setting.

`BrailleHandler.scrollForward` takes no gesture, so NVDA's `script_braille_scrollForward` and
`script_braille_scrollBack` are wrapped to record the display for as long as they run, and
`patches._nativeScroll` reads it. Not in `inputCore.decide_executeGesture`, which runs on the
driver's thread before a queued script, and whose gesture may be abandoned. One synchronous
call on one thread, so a plain module global, set on entry and restored on exit, is enough.

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

What every driver's gesture map binds its panning keys to, so every member is caught.
"""

_activeSource: str | None = None
"""The display whose panning key is running the scroll that is happening now."""

_originals: dict[str, object] = {}
"""NVDA's own commands, kept to be put back."""

_wrappers: dict[str, object] = {}
"""What was put in their place, so that L{remove} can tell whether it is still there."""

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
		# Through the layered keys helper, so a keyboard key a layer says acts for a display pans
		# that display. A display's own key is answered with its source, as before.
		_activeSource = _displayFor(gesture)
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
	# Set before anything is replaced, so `remove` puts back whatever went in if this stops part way.
	_installed = True
	for name, wrapper in wrapped.items():
		_originals[name] = getattr(commands, name)
		_wrappers[name] = wrapper
		setattr(commands, name, wrapper)


def remove() -> None:
	"""Stop. Safe to call when nothing is installed.

	Each command goes back only if it is still this module's. See `patches.remove`.
	"""
	global _installed, _activeSource
	if not _installed:
		return
	try:
		import globalCommands

		commands = globalCommands.GlobalCommands
		for name, original in _originals.items():
			if getattr(commands, name, None) is not _wrappers.get(name):
				log.debugWarning(
					f"BrlMultiline: {name} has been replaced since it was wrapped; "
					"leaving it as it is rather than undoing whatever replaced it",
				)
				continue
			setattr(commands, name, original)
	except Exception:
		log.error("BrlMultiline: could not restore NVDA's panning commands", exc_info=True)
	finally:
		# Whatever happened above, this module is no longer following anything, and saying so
		# is what lets a later install try again.
		_originals.clear()
		_wrappers.clear()
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
	return shouldReverseForSource(_displayFor(gesture))


def _displayFor(gesture) -> str | None:
	""":return: the display a gesture is for. See `keyLayerDispatch.displayFor`."""
	from . import keyLayerDispatch

	try:
		return keyLayerDispatch.displayFor(gesture)
	except Exception:
		log.debugWarning("BrlMultiline: could not tell which display a gesture is for", exc_info=True)
		return getattr(gesture, "source", None)


def sourceHolds(source: str | None, rect) -> bool:
	"""Whether a display's keys belong to the rectangle given, which is where a message is.

	:param source: the display whose key was pressed, or None if unknown.
	:param rect: a rectangle of the composite display.
	:return: True when that display holds the rectangle's first row, and True whenever it
		cannot be told, so that anything not plainly another display keeps NVDA's behaviour.
	"""
	if source is None:
		return True
	for device in devices.deviceMap():
		if device.driverName == source:
			return device.rowStart <= rect.row < device.rowStart + device.numRows
	return True


def nativeSegmentForSource(source: str | None, container) -> int | None:
	"""Find the segment a display's own panning keys should drive.

	The focus segment when that display holds it, else that display's first segment.

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
