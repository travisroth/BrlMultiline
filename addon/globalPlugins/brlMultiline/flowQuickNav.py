# BrlMultiline: hearing a browse mode quick navigation key.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Telling a jump by structure apart from a move by reading.

A structural jump, such as `h`, puts its target at the top of the band; every other move
keeps the reader's window. Which are structural is stated in `GROUNDING_TYPES`, since it cannot
be inferred. Every successful move is recorded, since a jump onto something focusable may never
reach the band's region. See "Grounding a jump" in docs/design/spatial-reading-plan.md.

Heard through `BrowseModeTreeInterceptor._quickNavScript`, which carries the item type, and
`TextInfoQuickNavItem.moveTo`, which says a search found something. Patched only while a flow
is showing.
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

Structural jumps, and anything named `heading`, by prefix. A list item, link, field, button or
paragraph is a move within what is read, and only tracks the cursor.
"""

GROUNDING_WINDOW = 1.0
"""How long a quick navigation key stays answerable, in seconds.

Its caret move arrives a moment later; an older note must not jump an ordinary arrow key.
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

Every successful move, grounding or not; see the module docstring."""


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

	Consumed, since one keypress causes one move. Braille acts on it whether or not it
	grounds; see the module docstring.

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
