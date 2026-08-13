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
returns. So we can find it and close it ourselves. Its `terminate` is then replaced with a
no-op on that instance, so that NVDA closing it again a moment later does nothing rather
than failing against a closed device.

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
"""`BrailleHandler._switchDisplay` as it was before the patch, or None when not patched."""


def _closeAndNeuter(display) -> None:
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


def releaseConflictingDisplay(driverNames: Iterable[str]) -> str | None:
	"""Close the display NVDA is switching away from, if we are about to open it.

	Called from the virtual driver's constructor, where `braille.handler.display` is still
	the outgoing driver.

	:param driverNames: the drivers this virtual display is about to open.
	:return: the name of the display released, or None if there was no conflict.
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
	_closeAndNeuter(display)
	return name


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
		oldDisplay is not None
		and _isVirtualDisplay(oldDisplay)
		and newDisplayClass is not oldDisplay.__class__
	):
		# Releases the member displays, so that the driver about to be constructed can have
		# whichever of them it wants.
		_closeAndNeuter(oldDisplay)
	return original(self, oldDisplay, newDisplayClass, **kwargs)


def _isVirtualDisplay(display) -> bool:
	"""Whether a driver instance is this add-on's virtual display.

	Compares the driver name rather than the class, to avoid importing the driver module
	from here — it imports this one.
	"""
	from .virtualLayout import VIRTUAL_DRIVER_NAME

	return getattr(display, "name", None) == VIRTUAL_DRIVER_NAME


def installSwitchPatch() -> None:
	"""Install the switch patch. Safe to call when it is already installed."""
	global _originalSwitchDisplay
	if _originalSwitchDisplay is not None:
		return
	_originalSwitchDisplay = BrailleHandler._switchDisplay
	BrailleHandler._switchDisplay = _switchDisplayReleasingVirtual
	log.debug("BrlMultiline: display switch patch installed")


def removeSwitchPatch() -> None:
	"""Restore NVDA's own method. Safe to call when nothing is installed."""
	global _originalSwitchDisplay
	if _originalSwitchDisplay is None:
		return
	BrailleHandler._switchDisplay = _originalSwitchDisplay
	_originalSwitchDisplay = None
	log.debug("BrlMultiline: display switch patch removed")
