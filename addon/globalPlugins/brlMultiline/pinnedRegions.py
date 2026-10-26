# BrlMultiline: regions for content pinned to a segment.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Text regions that keep a reading position of their own.

NVDA's text regions read and write the object's cursor, which for a pin would make it jump
and would move a caret, and the focus, behind the user's back. So a pinned region keeps a copy
of its position and serves `_getSelection` and `_setCursor` from it, and NVDA's own `nextLine`
and `previousLine` then pan it line by line.
"""

from braille.regions.textInfo import CursorManagerRegion, TextInfoRegion
from logHandler import log


class CursorOnlyWhereTheReaderIs:
	"""Mixin: a region that has a cursor only where the reader actually is.

	`Region.update` asks liblouis for `COMPBRL_AT_CURSOR` whenever the region has a cursor, so a
	cursor on every block would expand a word on every row. The cursor is a property that reads
	as nothing where the reader is not, set before translation rather than cleared after.

	The region that needs this is a flow's block, which shows a place the reader is not
	standing in. See `flowSources.FlowRegion`.
	"""

	_cursorPos = None
	"""Where the cursor is, as the region worked it out. Read back only where it counts."""

	def holdsTheCursor(self) -> bool:
		""":return: whether this region is the one the reader is in.

		Answered by whoever mixes this in, because only they know: for a flow it is the active
		block of a live band.
		"""
		return True

	@property
	def cursorPos(self):
		""":return: where the cursor is, or None where the reader is somewhere else."""
		return self._cursorPos if self.holdsTheCursor() else None

	@cursorPos.setter
	def cursorPos(self, position) -> None:
		self._cursorPos = position


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


def pinnedCounterpart(region):
	"""Build the pinned equivalent of a region NVDA generated.

	So NVDA decides the kind of region and this only replaces the cursor policy.

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
