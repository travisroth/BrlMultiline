# BrlMultiline: hearing a browse mode quick navigation key.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Telling a jump by structure apart from a move by reading.

A flow keeps the reader's place: when the cursor moves onto something already on the
display, nothing moves, and when it goes past the edge the window scrolls by the smallest
amount that brings it back. That is right for arrowing and for tabbing, where the reader
is working through what is in front of them.

It is wrong for a quick navigation key. Pressing `h` for the next heading skips a section
the reader has decided they are done with, and what they want then is to be put down at
the new heading with the document running on from it — a fresh place to start reading,
not their old window nudged along. So a grounding move puts its target at the top of the
band and flows from there.

Not every quick navigation key means that, which is the point of the policy below. `l`
moves to the next list, which is a change of section; `i` moves to the next list item,
which is a step within one and reads like arrowing. The difference is not something the
code can infer, so it is stated: structural jumps ground, everything else keeps the
reader's window.

NVDA gives this a single seam. Every quick navigation script — there are around forty of
them, generated at import time — calls `BrowseModeTreeInterceptor._quickNavScript`, so
that one method is the only thing to patch, and it carries the item type as an argument.
Patched only while a flow is showing, and taken back when it stops.
"""

import time

from logHandler import log

GROUNDING_TYPES = frozenset(
	{
		"table",
		"list",
		"landmark",
		"frame",
		"article",
		"grouping",
		"blockQuote",
		"separator",
		"figure",
		"notLinkBlock",
	},
)
"""Which quick navigation moves put their target at the top of the band.

Structural jumps: the reader has left a section behind and wants to be set down in the
next one. Anything named `heading` also grounds, including the numbered levels, which is
handled by prefix rather than by listing `heading1` to `heading9`.

Everything else keeps the reader's window and merely tracks the cursor. A list item, a
link, a form field, a button, a paragraph: those are moves within what is being read, and
a display that jumped for each of them would be unusable to read by.
"""

GROUNDING_WINDOW = 1.0
"""How long a quick navigation key stays answerable, in seconds.

The caret move it causes arrives a moment later, on NVDA's own cycle. Anything older than
this belongs to a keypress whose caret move has already been dealt with, and must not
ground the next one — a stale note would jump the display on an ordinary arrow key.
"""

_original = None
"""NVDA's own `_quickNavScript`, while this module's replacement is in place."""

_pending: tuple[str, float] | None = None
"""The last grounding move: its item type and when it happened, until it is taken."""


def groundsOn(itemType: str) -> bool:
	"""Whether a quick navigation move should re-ground the display.

	:param itemType: NVDA's name for what was moved to.
	:return: whether to put it at the top of the band.
	"""
	if not itemType:
		return False
	if itemType.startswith("heading"):
		return True
	return itemType in GROUNDING_TYPES


def note(itemType: str) -> None:
	"""Record that a quick navigation move happened, for the caret move it will cause."""
	global _pending
	if not groundsOn(itemType):
		# A move within what is being read. The window stays where the reader put it, and
		# any older note is dropped so that it cannot ground this move's caret event.
		_pending = None
		return
	_pending = (itemType, time.monotonic())


def takeGrounding() -> bool:
	"""Answer, once, whether the caret move now arriving came from a grounding jump.

	Consumed rather than read, because one keypress causes one caret move: leaving the note
	in place would ground every arrow key that followed until something else replaced it.

	:return: whether to put the cursor's block at the top of the band.
	"""
	global _pending
	if _pending is None:
		return False
	itemType, when = _pending
	_pending = None
	if time.monotonic() - when > GROUNDING_WINDOW:
		log.debug(f"Ignoring a stale {itemType} jump")
		return False
	return True


def forget() -> None:
	"""Drop any pending note, so that a flow starting now does not act on an old keypress."""
	global _pending
	_pending = None


def install() -> None:
	"""Start listening for quick navigation keys.

	Installed when a flow starts rather than when the add-on loads: this patches browse
	mode itself, and a reader with no flow on the display has no use for it.
	"""
	global _original
	if _original is not None:
		return
	try:
		from browseMode import BrowseModeTreeInterceptor
	except ImportError:
		log.debugWarning("Browse mode is not available, so quick navigation cannot be heard")
		return
	_original = BrowseModeTreeInterceptor._quickNavScript

	def _quickNavScriptNotingType(self, gesture, itemType, direction, errorMessage, readUnit):
		note(itemType)
		return _original(self, gesture, itemType, direction, errorMessage, readUnit)

	_quickNavScriptNotingType.__name__ = "_quickNavScript"
	BrowseModeTreeInterceptor._quickNavScript = _quickNavScriptNotingType


def remove() -> None:
	"""Stop listening, putting browse mode back as it was.

	Taken back only where the method is still the one installed here. Anything else belongs
	to whoever put it there, and is still delegating to the original.
	"""
	global _original
	forget()
	if _original is None:
		return
	try:
		from browseMode import BrowseModeTreeInterceptor

		if BrowseModeTreeInterceptor._quickNavScript.__module__ == __name__:
			BrowseModeTreeInterceptor._quickNavScript = _original
	except Exception:
		log.debugWarning("Could not stop listening for quick navigation", exc_info=True)
	_original = None
