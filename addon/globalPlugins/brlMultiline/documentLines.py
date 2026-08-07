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

	from .container import BrailleBufferContainer


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
		if not info.move(textInfos.UNIT_LINE, self.lineOffset):
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


def populate(
	container: "BrailleBufferContainer",
	excludedSegments: set[int],
) -> list[TextInfoPositionRegion]:
	"""Fill the free segments with the lines around the caret.

	:param container: the multi segment buffer.
	:param excludedSegments: segments that must be left alone, such as those holding a
		pinned object.
	:return: the regions created, so that they can be refreshed as the caret moves.
	"""
	focusNumber = container.focusSegmentNumber
	focusSegment = container.segments[focusNumber]
	if not focusSegment.regions:
		return []
	source = focusSegment.regions[-1]
	if not isDocumentRegion(source):
		# The focus is not in something with readable lines, so there is nothing to show.
		return []
	created: list[TextInfoPositionRegion] = []
	for index, segment in enumerate(container.segments):
		if index == focusNumber or index in excludedSegments:
			continue
		region = TextInfoPositionRegion(source.obj, lineOffset=index - focusNumber)
		region.targetSegment = index
		try:
			region.update()
		except Exception:
			log.debugWarning(f"Could not build a line region for segment {index}", exc_info=True)
			continue
		segment.clear()
		segment.append(region)
		created.append(region)
	return created
