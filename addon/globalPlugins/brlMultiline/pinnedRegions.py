# BrlMultiline: regions for content pinned to a segment.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Text regions that keep a reading position of their own.

NVDA's text regions are tied to a cursor at both ends. They render whatever the caret, or
the browse mode cursor, is currently on, and panning off the end of the line writes a new
position back to it. Neither half is right for a pinned region, because what it shows
belongs to a window the user is not working in:

1. Reading the object's cursor would make the pin jump about whenever anything else moved
   it, so the segment would stop showing the place the user chose to pin.
2. Writing to the object's cursor would move a caret behind the user's back, and in an
   editable control that drags the system focus with it. That is exactly why
   `DisplayContainer._scroll` refuses to let an ordinary region change lines in a segment
   that does not hold focus.

So a pinned region takes a copy of the position it starts at and answers both directions
from that copy. NVDA's own `nextLine` and `previousLine` then work unaltered — they read
through `_getSelection` and hand the new position to `_setCursor`, and both are served by
the copy — which is what lets a pinned segment be panned line by line through a document
the user is not in. Without it a pin can never leave the line it was made on, and a pinned
document is unreadable past its first line.

The same trick, from the other end, is what `documentLines.TextInfoPositionRegion` does:
there the private position is derived from the caret on every update and panning is
refused, because those regions are meant to track the caret. Here it is fixed at pin time
and panning is the whole point.
"""

from braille.regions.textInfo import CursorManagerRegion, TextInfoRegion
from logHandler import log


class PinnedRegion:
	"""Mixin giving a text region a reading position of its own.

	Mixed in ahead of the region class whose cursor policy it replaces, so that
	`super()._getSelection` still reaches the right one: `TextInfoRegion` reads the object's
	own selection, while `CursorManagerRegion` reads the browse mode cursor.
	"""

	def __init__(self, obj) -> None:
		"""
		:param obj: the object or tree interceptor to read from.
		"""
		super().__init__(obj)
		self._position = None
		"""This region's own place in the document, or None until it is first rendered."""

	def _getSelection(self):
		if self._position is None:
			# The first render adopts whatever position the object is at, which is where the
			# user was standing when they pinned it. Everything after that is this region's
			# own.
			self._position = super()._getSelection().copy()
		return self._position.copy()

	def _setCursor(self, info) -> None:
		# Deliberately not passed on. This is where NVDA would move the caret.
		self._position = info.copy()

	def _bookmark(self):
		""":return: a comparable mark for the current position, or None if unavailable."""
		try:
			return self._position.bookmark
		except (AttributeError, NotImplementedError):
			return None

	def panUnit(self, unit: str, forward: bool) -> bool:
		"""Move this region's position by one unit of the given granularity and re-render.

		Behaves like L{panLine} but the movement granularity is supplied by the caller.
		Pass a ``textInfos`` unit constant such as ``textInfos.UNIT_PARAGRAPH``.

		:param unit: a ``textInfos`` unit constant.
		:param forward: True for the next unit, False for the previous.
		:return: whether the position actually moved.
		"""
		if getattr(self, "_readingInfo", None) is None:
			return False
		before = self._bookmark()
		try:
			info = self._position.copy()
			info.collapse()
			direction = 1 if forward else -1
			if info.move(unit, direction) == 0:
				return False
			self._position = info
			self.update()
		except Exception:
			log.debugWarning(f"Could not move {self!r} by unit {unit!r}", exc_info=True)
			return False
		after = self._bookmark()
		if before is not None and after is not None and before == after:
			return False
		return True

	def panLine(self, forward: bool) -> bool:
		"""Move this region's position one line and re-render it.

		:param forward: True for the next line, False for the previous.
		:return: whether the position actually moved, so the caller knows whether to
			reposition the window.
		"""
		if getattr(self, "_readingInfo", None) is None:
			# Never rendered, or rendered blank. There is no line to move from.
			return False
		before = self._bookmark()
		try:
			if forward:
				self.nextLine()
			else:
				# Asking for the start of the previous line rather than its end, so that the
				# position is somewhere definite whether or not the window is showing the
				# start of the current one.
				self.previousLine(start=True)
			self.update()
		except Exception:
			log.debugWarning(f"Could not pan {self!r} by a line", exc_info=True)
			return False
		after = self._bookmark()
		if before is not None and after is not None and before == after:
			# The end of the document. `nextLine` collapses to the end of the last line
			# rather than reporting that it could not move, so standing still is recognised
			# by comparing positions rather than by a return value.
			return False
		return True

	def __repr__(self) -> str:
		return f"<{type(self).__name__} {getattr(self, 'rawText', '')!r}>"


class PinnedTextInfoRegion(PinnedRegion, TextInfoRegion):
	"""A pinned region over an object with navigable text: an edit field, a terminal."""


class PinnedCursorManagerRegion(PinnedRegion, CursorManagerRegion):
	"""A pinned region over a browse mode document or anything else cursor managed."""

	def panHeading(self, forward: bool) -> bool:
		"""Move to the next or previous heading in a browse mode document.

		Returns ``True`` if a heading was found and the position moved.  Returns
		``False`` if heading iteration is unavailable or there is no heading in
		the requested direction.  Does **not** fall back internally — fallback to
		paragraph or line is handled by the caller (typically
		:class:`~brlMultiline.semanticNav.SemanticNavigator`).

		:param forward: ``True`` for the next heading, ``False`` for the previous.
		:return: whether the position moved to a heading.
		"""
		iterNodes = getattr(self.obj, "_iterNodesByType", None)
		if iterNodes is None:
			return False
		try:
			direction = "next" if forward else "previous"
			it = iterNodes("heading", direction=direction, pos=self._position)
			item = next(it, None)
			if item is None:
				return False
			info = item.textInfo
			info.collapse()
			self._position = info
			self.update()
			return True
		except Exception:
			log.debugWarning("Heading node iteration failed", exc_info=True)
			return False


def pinnedCounterpart(region):
	"""Build the pinned equivalent of a region NVDA generated.

	Used rather than deciding for ourselves which kind of region an object wants, so that
	NVDA keeps that decision and this only replaces the cursor policy.

	:param region: a region from `getFocusRegions`.
	:return: a pinned region over the same object, or None if this kind of region has no
		cursor to replace and can be used as it is.
	"""
	# Ordered subclass first: `CursorManagerRegion` is a `TextInfoRegion`.
	if isinstance(region, CursorManagerRegion):
		return PinnedCursorManagerRegion(region.obj)
	if isinstance(region, TextInfoRegion):
		return PinnedTextInfoRegion(region.obj)
	return None
