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

Not grounding is not the same as doing nothing, and treating them as the same was a fault.
Every successful move is recorded here, structural or not, because braille has to answer all
of them and the caret move a jump causes is not something a flow reliably hears about: a key
that lands on something focusable moves the focus as well, and browse mode then reports
neither event to the region a band is holding. That is why `h` appeared to work — grounding
was the one path that did not need the region to have been re-read — while `e` for the next
edit field and `b` for the next button moved speech and left braille where it was.

NVDA gives this two useful seams. Every quick navigation script — there are around forty
of them, generated at import time — calls `BrowseModeTreeInterceptor._quickNavScript`,
which carries the item type. A search that actually found something then calls
`TextInfoQuickNavItem.moveTo`; hearing both is what keeps an unsuccessful search from
grounding the next ordinary caret move. Patched only while a flow is showing, and taken
back when it stops.
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

_originalMoveTo = None
"""NVDA's own `TextInfoQuickNavItem.moveTo`, while the listener is in place."""

_quickNavWrapper = None
_moveToWrapper = None
"""The exact functions installed here, so teardown never removes somebody else's patch."""

_activeItemType: str | None = None
"""The item type being searched for during the synchronous quick-navigation call."""

_pending: tuple[str, float, bool] | None = None
"""The last quick navigation move: its item type, when it happened, and whether it grounds.

Every successful move is recorded, not only the grounding ones. Braille has to answer all
of them — a jump to the next edit field must bring that field onto the display — and the
caret move it causes is not always one the flow hears about by itself. Whether to *ground*
is the second half of the note, and only the structural types say yes."""


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
	_pending = (itemType, time.monotonic(), groundsOn(itemType))


def take() -> bool | None:
	"""Answer, once, whether a quick navigation key is behind what is happening now.

	Consumed rather than read, because one keypress causes one move: leaving the note in
	place would answer every arrow key that followed until something else replaced it.

	Braille has to act on the answer whether or not it grounds. NVDA does not always tell a
	flow that a jump's caret has moved — a quick navigation key that lands on something
	focusable moves the focus too, and browse mode then reports neither the focus nor,
	reliably, the caret to the region a band is holding. The display sat still while speech
	announced the field the reader had jumped to. Hearing the keypress is what makes the
	answer independent of which of those NVDA happens to send.

	:return: None where no quick navigation move is waiting; otherwise whether to put its
		target at the top of the band rather than merely bringing it into view.
	"""
	global _pending
	if _pending is None:
		return None
	itemType, when, grounds = _pending
	_pending = None
	if time.monotonic() - when > GROUNDING_WINDOW:
		log.debug(f"Ignoring a stale {itemType} jump")
		return None
	return grounds


def forget() -> None:
	"""Drop any pending note, so that a flow starting now does not act on an old keypress."""
	global _pending
	_pending = None


def install() -> None:
	"""Start listening for quick navigation keys.

	Installed when a flow starts rather than when the add-on loads: this patches browse
	mode itself, and a reader with no flow on the display has no use for it.
	"""
	global _activeItemType, _moveToWrapper, _original, _originalMoveTo, _quickNavWrapper
	if _original is not None:
		return
	try:
		from browseMode import BrowseModeTreeInterceptor, TextInfoQuickNavItem
	except ImportError:
		log.debugWarning("Browse mode is not available, so quick navigation cannot be heard")
		return
	original = BrowseModeTreeInterceptor._quickNavScript
	originalMoveTo = TextInfoQuickNavItem.moveTo
	_original = original
	_originalMoveTo = originalMoveTo

	def _quickNavScriptNotingType(self, gesture, itemType, direction, errorMessage, readUnit):
		global _activeItemType
		# A failed search never reaches `TextInfoQuickNavItem.moveTo`. Clear an older note
		# before starting, then let that successful seam put the new one back.
		forget()
		previous = _activeItemType
		_activeItemType = itemType
		try:
			return original(self, gesture, itemType, direction, errorMessage, readUnit)
		finally:
			_activeItemType = previous

	def _moveToNotingSuccess(self):
		if _activeItemType is not None:
			# Recorded immediately before the move because moving a focusable item may queue
			# the focus event which consumes it. If the move itself fails, do not leave it for
			# the next ordinary caret change.
			note(_activeItemType)
		try:
			return originalMoveTo(self)
		except Exception:
			forget()
			raise

	_quickNavScriptNotingType.__name__ = "_quickNavScript"
	_moveToNotingSuccess.__name__ = "moveTo"
	_quickNavWrapper = _quickNavScriptNotingType
	_moveToWrapper = _moveToNotingSuccess
	BrowseModeTreeInterceptor._quickNavScript = _quickNavScriptNotingType
	TextInfoQuickNavItem.moveTo = _moveToNotingSuccess


def remove() -> None:
	"""Stop listening, putting browse mode back as it was.

	Taken back only where the method is still the one installed here. Anything else belongs
	to whoever put it there, and is still delegating to the original.
	"""
	global _activeItemType, _moveToWrapper, _original, _originalMoveTo, _quickNavWrapper
	forget()
	if _original is None:
		return
	try:
		from browseMode import BrowseModeTreeInterceptor, TextInfoQuickNavItem

		if BrowseModeTreeInterceptor._quickNavScript is _quickNavWrapper:
			BrowseModeTreeInterceptor._quickNavScript = _original
		if TextInfoQuickNavItem.moveTo is _moveToWrapper:
			TextInfoQuickNavItem.moveTo = _originalMoveTo
	except Exception:
		log.debugWarning("Could not stop listening for quick navigation", exc_info=True)
	_original = None
	_originalMoveTo = None
	_quickNavWrapper = None
	_moveToWrapper = None
	_activeItemType = None
