# BrlMultiline: keeping member acknowledgements out of the braille handler.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""One patch, and a careful statement of what it is and is not for.

`BrailleDisplayDriver._handleAck` cancels `braille.handler.ackTimerHandle` and calls
`braille.handler._writeCellsInBackground()`. Read cold that looks like a collision waiting
to happen between two acknowledging members.

It is not. `_awaitingAck` is set to True in exactly one place in NVDA,
`BrailleHandler._bgThreadExecutor`, and only ever for `self.display`. Under the virtual
driver `self.display` is the virtual driver, which reports `receivesAckPackets = False`, so
the ack timer is never armed and a member cancelling it cancels nothing.

The patch is for the other half: every acknowledged packet otherwise queues a
`_writeCellsInBackground()` with nothing to write, noise on the shared I/O thread.

The base class is patched, since overriding drivers such as `freedomScientific` reach it
through `super()`.

Three drivers in NVDA are acknowledgement driven and so reach this at all:
`freedomScientific`, `handyTech`, and `eurobraille` at firmware 3.0 and above.
"""

from __future__ import annotations

import braille
from braille.display.driver import BrailleDisplayDriver
from logHandler import log

_originalHandleAck = None
"""`BrailleDisplayDriver._handleAck` as it was before the patch, or None when not in the chain."""

_active = False
"""Whether the patch should do anything, as distinct from being installed.

Removal restores the method only when this patch is still outermost, and otherwise leaves it
in place and inert, as `handover` does.
"""


def _handleAckForMembers(self: BrailleDisplayDriver) -> None:
	"""Replacement for `BrailleDisplayDriver._handleAck`.

	The display NVDA owns keeps NVDA's own behaviour. Anything else is a member of a virtual
	display, or some other driver running alongside, and only needs its own flag cleared.
	"""
	original = _originalHandleAck
	if not _active:
		return original(self)
	if not self.receivesAckPackets:
		raise NotImplementedError("This display driver does not support ACK packet handling")
	if braille.handler is not None and self is braille.handler.display:
		return original(self)
	self._awaitingAck = False
	return None


def install() -> None:
	"""Install the patch, or reactivate it. Safe to call when it is already installed."""
	global _originalHandleAck, _active
	_active = True
	if _originalHandleAck is not None:
		return
	_originalHandleAck = BrailleDisplayDriver._handleAck
	BrailleDisplayDriver._handleAck = _handleAckForMembers
	log.debug("BrlMultiline: member acknowledgement patch installed")


def remove() -> None:
	"""Stand down. Safe to call when nothing is installed.

	Restores NVDA's own method only when this patch is still the outermost one, so that an
	add-on which wrapped `_handleAck` later is not discarded.
	"""
	global _originalHandleAck, _active
	if _originalHandleAck is None:
		return
	_active = False
	if BrailleDisplayDriver._handleAck is not _handleAckForMembers:
		log.debugWarning(
			"BrlMultiline: something else has patched _handleAck since; "
			"leaving this patch in place but inert rather than discarding theirs",
		)
		return
	BrailleDisplayDriver._handleAck = _originalHandleAck
	_originalHandleAck = None
	log.debug("BrlMultiline: member acknowledgement patch removed")
