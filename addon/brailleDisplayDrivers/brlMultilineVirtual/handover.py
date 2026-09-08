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

For every driver in NVDA that ordering is harmless, because two different drivers do not
normally want the same hardware. For this one it is the ordinary case: the display the user
is switching away from is very often one of the displays this driver is being asked to open.

Switching from a Focus to the virtual display therefore has the virtual display try to open
the Focus while NVDA still holds it. Every retry is spent before the old driver is released,
so the member is dropped and the virtual display comes up missing the display the user was
just using. Phase 0 already showed that a display released moments earlier can be slow to
reopen; here it has not been released at all.

Both directions need answering, and they need different answers because in one of them this
driver is being constructed and in the other it is being destroyed.

**Into the virtual display.** `braille.handler.display` still points at the old driver while
our `__init__` runs, because `_setDisplay` assigns the new one only after `_switchDisplay`
returns. So we can reach it — and rather than closing it and opening our own, we *adopt* it:
the live, already initialised, already dispatching driver becomes our member as it stands.
Its `terminate` is replaced by a one-shot no-op so that NVDA closing it a moment later does
nothing, after which the real method is back and ours to call.

Adopting rather than reopening was not the first design, and the reason it had to become one
is worth writing down, because it is a defect in NVDA rather than in this add-on.

`hwIo.base.IoBase.close` closes the device handle but never clears `self._file`, and
`IoBase.__del__` calls `close` again. So once a driver has been terminated, finalising it
closes its handle a *second* time — and by then Windows may have handed that handle value to
something else. Closing a display and immediately opening it again therefore works, right up
until the old driver object is garbage collected, at which point the new device's handle is
pulled out from under it.

That is exactly what a Phase 1 hardware run showed. The Focus was released and reopened
successfully, `handler.display` was then assigned, dropping the last reference to the old
driver, and the very next write failed with "the handle is invalid" nine milliseconds later.
With two members it was worse: the Monarch, being opened first after the release, received
the recycled handle value and lost it instead.

The same log shows NVDA hitting this on its own, with no add-on involved: reinitialising the
Focus from the braille settings failed identically, because `_switchDisplay` terminates and
reconstructs the same driver instance. So this is upstream's to fix. What this module can do
is stop provoking it — hence adoption, and hence `_retireDriver` in the driver module, which
keeps a terminated driver alive so its finaliser never runs mid session.

**Out of the virtual display.** Here we are the old driver, and the new one is constructed
before we are told anything at all, so there is no method of ours to intervene in. That
needs `_switchDisplay` itself, patched to close a departing virtual display before building
its replacement. The patch is installed only while a virtual display is live and is narrow
enough to state in one sentence: if the display being replaced is this driver, terminate it
first.

Patching NVDA is not free and the rest of this driver deliberately avoids it. The
justification is that no extension point covers this, the alternative is telling users to
select "no braille" between every display change, and the patched behaviour is what the
unpatched code already does a few lines later.
"""

from __future__ import annotations

from typing import Iterable

import braille
from braille.brailleHandler import BrailleHandler
from logHandler import log

_originalSwitchDisplay = None
"""`BrailleHandler._switchDisplay` as it was before the patch, or None when not in the chain."""

_releasingNames: set[str] = set()
"""The drivers this patch releases before their replacement is built.

A set rather than the one virtual display it began as, because the add-on has a second
driver with the same need: `brlMultilineMonarch` holds a HID device exclusively, so switching
away from it left `hidBrailleStandard` unable to find the display it was being asked to open.
The problem is not particular to either driver — it belongs to any driver holding hardware
that the next one may want — so membership is registered rather than hard coded.

Also what decides whether the patch is still wanted. It is removed when the last driver in it
unregisters, so one driver leaving cannot pull the patch out from under another.
"""

_active = False
"""Whether the patch should do anything.

Separate from being installed, because the two can come apart. If another add-on wraps
`_switchDisplay` after this patch is installed, then restoring the saved method on the way
out would erase that add-on's wrapper. So removal restores the attribute only when this
patch is still the outermost one, and otherwise leaves it in place and inert — still called,
still delegating, doing nothing of its own.
"""


def releaseDisplayNow(display) -> None:
	"""Terminate a display now, and make terminating it again do nothing.

	NVDA will call `terminate` on this instance itself once the switch completes. Letting
	that reach a closed device would at best log an error and at worst raise out of the
	switch, so the second call is disarmed. An instance attribute is enough: `terminate` is
	an ordinary method, so assigning over it on one instance shadows the class's.

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

	NVDA calls `terminate` on the outgoing display exactly once, immediately after the
	incoming driver is constructed. An adopted display must survive that call, but it must
	still be closable afterwards by whoever now owns it — so the no-op removes itself,
	uncovering the class's own method again.

	:param display: the driver to protect for one call.
	"""

	def terminateOnceIgnored() -> None:
		del display.terminate

	display.terminate = terminateOnceIgnored


def adoptConflictingDisplay(driverNames: Iterable[str]):
	"""Take over the display NVDA is switching away from, if it is to be one of our members.

	Called from the virtual driver's constructor, where `braille.handler.display` is still
	the outgoing driver. Returns it live rather than closing it, because closing and
	reopening the same device provokes the `hwIo` finaliser bug described at the top of this
	module, and because a driver that is already open is the fastest possible way to open it.

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

	For the path where the virtual display fails to construct after adopting something. Left
	as it was, the one-shot would swallow NVDA's terminate and leave the device open with
	nothing owning it.

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

	Safe to call when nothing is installed. Note that this no longer necessarily removes the
	patch: another driver may have registered, and pulling the patch out from under it would
	reintroduce for that driver exactly the failure this module exists to prevent.
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

	Restores NVDA's own method only when this patch is still the outermost one. If something
	else has wrapped `_switchDisplay` since, restoring would throw that wrapper away, so the
	patch stays in the chain and simply stops acting.
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
