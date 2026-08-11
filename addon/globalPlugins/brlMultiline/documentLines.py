# BrlMultiline: showing the lines around the caret.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Fills segments with the document lines above and below the caret.

The segment that follows the focus shows the caret's own line, as NVDA always does. The
segments around it show the lines at a fixed offset, so a multi row display presents a
window onto the document rather than a single line.

Line assignment is fixed: the segment two above the focus segment shows the line two
above the caret. Nothing shuffles as the caret moves; the offsets are constant and each
region simply re-reads its own line. The 2023 prototype's ScrollingManager, which tried
to rotate which region was current, is deliberately not reproduced here.
"""

from typing import TYPE_CHECKING, Optional

import textInfos
from braille.regions.base import Region
from braille.regions.textInfo import TextInfoRegion
from logHandler import log

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject

	from .container import DisplayContainer
	from .segments import BrailleBufferSegment


class TextInfoPositionRegion(TextInfoRegion):
	"""A text region showing the line a fixed number of lines from the caret.

	With an offset of zero this behaves as an ordinary `TextInfoRegion`.
	"""

	def __init__(self, obj: "NVDAObject", lineOffset: int = 0) -> None:
		"""
		:param obj: the object or tree interceptor to read from.
		:param lineOffset: how many lines away from the caret to read. Negative reads
			backwards, positive forwards.
		"""
		super().__init__(obj)
		self.lineOffset = lineOffset
		self._cachedInfo: Optional[textInfos.TextInfo] = None
		self.outOfRange = False
		"""True when the offset falls outside the document, so the region renders blank."""

	def _computeInfo(self) -> Optional[textInfos.TextInfo]:
		"""Work out the range this region should show.

		:return: the range, or None if the offset falls outside the document.
		"""
		info = TextInfoRegion._getSelection(self)
		if self.lineOffset == 0:
			return info
		info = info.copy()
		info.collapse(end=True)
		# `move` reports how many units it actually moved, and stops at the edge of the
		# document rather than failing. A partial move is no use here: a segment asked for
		# the line seven back would otherwise show the first line, as would every segment
		# above it, repeating one line across the display instead of going blank.
		if info.move(textInfos.UNIT_LINE, self.lineOffset) != self.lineOffset:
			# There is no such line: the caret is near the start or end of the document.
			return None
		return info

	def _getSelection(self):
		if self._cachedInfo is not None:
			return self._cachedInfo.copy()
		return TextInfoRegion._getSelection(self)

	def update(self):
		try:
			info = self._computeInfo()
		except Exception:
			log.debugWarning(f"Could not read the line at offset {self.lineOffset}", exc_info=True)
			info = None
		if info is None:
			self.outOfRange = True
			self._renderBlank()
			return
		self.outOfRange = False
		self._cachedInfo = info
		try:
			super().update()
		finally:
			self._cachedInfo = None

	def _renderBlank(self) -> None:
		"""Render nothing, for an offset that falls outside the document."""
		self.rawText = ""
		self.rawTextTypeforms = []
		self.cursorPos = None
		self.selectionStart = self.selectionEnd = None
		self._rawToContentPos = []
		self._currentContentPos = 0
		self._readingInfo = None
		self._isFormatFieldAtStart = True
		self._skipFieldsNotAtStartOfNode = False
		self._endsWithField = False
		try:
			self._languageIndexes = {0: self._getDefaultRegionLanguage()}
		except Exception:
			self._languageIndexes = {}
		Region.update(self)

	def routeTo(self, braillePos: int) -> None:
		# Routing within a line other than the caret's would move the caret into a line
		# the user is only reading, dragging the focus with it.
		if self.lineOffset != 0:
			return
		super().routeTo(braillePos)

	def nextLine(self):
		# Panning must not walk this region off the line it is pinned to.
		if self.lineOffset != 0:
			return
		super().nextLine()

	def previousLine(self, start=False):
		if self.lineOffset != 0:
			return
		super().previousLine(start)

	def __repr__(self) -> str:
		return f"<TextInfoPositionRegion offset={self.lineOffset} outOfRange={self.outOfRange}>"


def isDocumentRegion(region: Region) -> bool:
	""":return: whether a region reads text that has lines to show around it."""
	return isinstance(region, TextInfoRegion)


def isFree(
	segment: "BrailleBufferSegment",
	index: int,
	focusNumber: int,
	claimedKeys: frozenset[str] | set[str],
) -> bool:
	"""Decide whether this module may write into a segment.

	Three things put a segment out of bounds, and they arrive by different routes:

	1. It follows the system focus, so it already shows the caret's own line.
	2. Its specification reserves it for a panel, which is a claim made when the view was
		built. A table's grid cells are reserved this way.
	3. It has been claimed at runtime, by a pinned object. Those claims are not in the view
		because pinning happens long after it was composed.

	:param segment: the segment to test.
	:param index: its index in display order.
	:param focusNumber: the index of the focus segment.
	:param claimedKeys: keys claimed at runtime.
	:return: whether the segment is free to fill.
	"""
	return index != focusNumber and not segment.isReserved and segment.key not in claimedKeys


def clearDocumentRegions(
	container: "DisplayContainer",
	claimedKeys: frozenset[str] | set[str] = frozenset(),
) -> bool:
	"""Remove the line regions this module placed in the segments around the focus.

	Only free segments are touched, so this takes back what the add-on itself put on the
	display and nothing else.

	:param container: the whole display.
	:param claimedKeys: keys of segments claimed at runtime, such as those holding a pinned
		object.
	:return: whether anything was cleared, so the caller knows to recombine the display.
	"""
	focusNumber = container.focusSegmentNumber
	cleared = False
	for index, segment in enumerate(container.segments):
		if not isFree(segment, index, focusNumber, claimedKeys):
			continue
		if any(isinstance(region, TextInfoPositionRegion) for region in segment.regions):
			segment.clear()
			cleared = True
	return cleared


def populate(
	container: "DisplayContainer",
	claimedKeys: frozenset[str] | set[str] = frozenset(),
) -> bool:
	"""Fill the free segments with the lines around the caret.

	:param container: the whole display.
	:param claimedKeys: keys of segments claimed at runtime, such as those holding a pinned
		object.
	:return: whether any segment's contents changed, so the caller knows to recombine the
		display.
	"""
	focusNumber = container.focusSegmentNumber
	focusSegment = container.segments[focusNumber]
	source = focusSegment.regions[-1] if focusSegment.regions else None
	if source is None or not isDocumentRegion(source):
		# The focus is not in something with readable lines. Lines belonging to the
		# document the focus has just left would otherwise stay under the reader's
		# fingers, and would go on tracking a caret that is no longer theirs.
		return clearDocumentRegions(container, claimedKeys)
	changed = False
	for index, segment in enumerate(container.segments):
		if not isFree(segment, index, focusNumber, claimedKeys):
			continue
		# The offset follows display order, so the segment two above the focus segment
		# shows the line two above the caret. Reserved segments sitting between them are
		# still counted, so the lines stay in step with the rows they are printed on.
		region = TextInfoPositionRegion(source.obj, lineOffset=index - focusNumber)
		region.targetSegment = segment.key
		try:
			region.update()
		except Exception:
			log.debugWarning(f"Could not build a line region for segment {segment.key!r}", exc_info=True)
			continue
		segment.clear()
		segment.append(region)
		changed = True
	return changed
