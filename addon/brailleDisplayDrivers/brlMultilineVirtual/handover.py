# BrlMultiline: taking a display over from NVDA, and giving it back.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""NVDA opens the new display before closing the old one, which this driver cannot survive.

`BrailleHandler._switchDisplay` runs the new driver's `__init__` first and only then calls
`oldDisplay.terminate()`::

	newDisplay = newDisplayClass.__new__(newDisplayClass)
	extensionPoints.callWithSupportedKwargs(newDisplay.__init__, **kwargs)
	if not sameDisplayReInit:
		if oldDisplay:
			oldDisplay.terminate()

For this driver the display being left is often one it must open, and NVDA still holds it.

**Into the virtual display.** `braille.handler.display` is still the old driver while our
`__init__` runs, so it is adopted as a member as it stands, with its `terminate` replaced by a
one-shot no-op. Not closed and reopened, because of an NVDA defect: `hwIo.base.IoBase.__del__`
closes a handle `close` already closed, which Windows may have reused. See `_retireDriver` in
the driver module.

**Out of the virtual display.** The new driver is built before we are told, so
`_switchDisplay` is patched, only while such a driver is live: if the display being replaced
is registered here, terminate it first. No extension point covers this.

See "The display handover problem" and "The `hwIo` finaliser bug" in
docs/design/virtual-display-plan.md.
"""

from __future__ import annotations

from collections.abc import Iterable

import braille
from braille.brailleHandler import BrailleHandler
from logHandler import log

_originalSwitchDisplay = None
"""`BrailleHandler._switchDisplay` as it was before the patch, or None when not in the chain."""

_releasingNames: set[str] = set()
"""The drivers this patch releases before their replacement is built.

Registered by any driver holding hardware the next may want, such as `brlMultilineMonarch`'s
exclusive HID device. The patch goes when the last one unregisters.
"""

_active = False
"""Whether the patch should do anything.

Separate from being installed: if another add-on has wrapped `_switchDisplay` since, removal
leaves this patch in place and inert. See `_uninstallIfUnwanted`.
"""


def releaseDisplayNow(display) -> None:
	"""Terminate a display now, and make terminating it again do nothing.

	NVDA's own `terminate` after the switch is disarmed by an instance attribute.

	:param display: the driver to close.
	"""
	name = getattr(display, "name", "?")
	try:
		display.terminate()
		log.debug(f"BrlMultiline: released {name} ahead of the display switch")
	except Exception:
		log.error(f"BrlMultiline: error releasing {name} ahead of the display switch", exc_info=True)
	finally:
		display.terminate = lambda: None


def _suppressOneTerminate(display) -> None:
	"""Make the next `terminate` on this instance do nothing, and the ones after it normal.

	The no-op removes itself, so the new owner can still close the display.

	:param display: the driver to protect for one call.
	"""

	def terminateOnceIgnored() -> None:
		del display.terminate

	display.terminate = terminateOnceIgnored


def adoptConflictingDisplay(driverNames: Iterable[str]):
	"""Take over the display NVDA is switching away from, if it is to be one of our members.

	Called from the virtual driver's constructor. Returned live, not reopened; see the module
	docstring.

	:param driverNames: the drivers this virtual display is about to open.
	:return: a (name, driver) pair, or None if there was no conflict.
	"""
	handler = braille.handler
	if handler is None:
		return None
	display = handler.display
	if display is None:
		return None
	name = getattr(display, "name", None)
	if name is None or name not in set(driverNames):
		return None
	_suppressOneTerminate(display)
	log.debug(f"BrlMultiline: adopting the running {name} rather than reopening it")
	return name, display


def releaseAdoption(display) -> None:
	"""Hand an adopted display back, so that NVDA can close it in the ordinary way.

	For a construction that fails after adopting, so the device is not left open unowned.

	:param display: the driver to hand back.
	"""
	try:
		del display.terminate
	except AttributeError:
		# Already handed back, or never adopted.
		pass


def _switchDisplayReleasingVirtual(
	self: BrailleHandler,
	oldDisplay,
	newDisplayClass,
	**kwargs,
):
	"""Replacement for `BrailleHandler._switchDisplay` that closes a departing virtual display.

	Only the case this driver creates is changed. Everything else, including reinitialising
	the same driver, is left to NVDA's own method.
	"""
	# Taken before anything else, because terminating the virtual display below removes this
	# patch, and would otherwise clear the very reference this call is about to return
	# through. This function outlives its own installation by exactly one call.
	original = _originalSwitchDisplay
	if (
		_active
		and oldDisplay is not None
		and _shouldRelease(oldDisplay)
		and newDisplayClass is not oldDisplay.__class__
	):
		# Releases the member displays, so that the driver about to be constructed can have
		# whichever of them it wants.
		releaseDisplayNow(oldDisplay)
	return original(self, oldDisplay, newDisplayClass, **kwargs)


def _shouldRelease(display) -> bool:
	"""Whether this driver has asked to be released ahead of its replacement.

	Compares the driver name rather than the class, to avoid importing driver modules from
	here — they import this one.
	"""
	return getattr(display, "name", None) in _releasingNames


def releaseOnSwitch(name: str) -> None:
	"""Have a departing driver of this name closed before its replacement is constructed.

	Called by a driver that holds hardware another driver may immediately be asked to open,
	from the point where it is live. Safe to call more than once.

	:param name: the driver's `name`.
	"""
	_releasingNames.add(name)
	_ensureInstalled()


def stopReleasingOnSwitch(name: str) -> None:
	"""Stop releasing a driver of this name, and remove the patch if nothing else wants it.

	Safe to call when the name was never registered.

	:param name: the driver's `name`.
	"""
	_releasingNames.discard(name)
	_uninstallIfUnwanted()


def installSwitchPatch() -> None:
	"""Register the virtual display and install the patch. Safe to call repeatedly.

	Kept as the virtual driver's own entry point, which is what its call sites and tests use.
	It is `releaseOnSwitch` for this driver's name.
	"""
	from .virtualLayout import VIRTUAL_DRIVER_NAME

	releaseOnSwitch(VIRTUAL_DRIVER_NAME)


def removeSwitchPatch() -> None:
	"""Unregister the virtual display, and stand down if no other driver still wants this.

	Safe to call when nothing is installed. The patch stays while another driver is registered.
	"""
	from .virtualLayout import VIRTUAL_DRIVER_NAME

	stopReleasingOnSwitch(VIRTUAL_DRIVER_NAME)


def _ensureInstalled() -> None:
	"""Put the patch in the chain if it is not already there, and arm it."""
	global _originalSwitchDisplay, _active
	_active = True
	if _originalSwitchDisplay is not None:
		# Already in the chain, whether or not it was doing anything.
		return
	_originalSwitchDisplay = BrailleHandler._switchDisplay
	BrailleHandler._switchDisplay = _switchDisplayReleasingVirtual
	log.debug("BrlMultiline: display switch patch installed")


def _uninstallIfUnwanted() -> None:
	"""Take the patch out, unless a registered driver still needs it.

	Restores NVDA's method only when this patch is still outermost; otherwise it stays, inert.
	"""
	global _originalSwitchDisplay, _active
	if _releasingNames:
		log.debug(
			f"BrlMultiline: display switch patch still wanted by {sorted(_releasingNames)}; leaving it",
		)
		return
	if _originalSwitchDisplay is None:
		return
	_active = False
	if BrailleHandler._switchDisplay is not _switchDisplayReleasingVirtual:
		log.debugWarning(
			"BrlMultiline: something else has patched _switchDisplay since; "
			"leaving this patch in place but inert rather than discarding theirs",
		)
		return
	BrailleHandler._switchDisplay = _originalSwitchDisplay
	_originalSwitchDisplay = None
	log.debug("BrlMultiline: display switch patch removed")
