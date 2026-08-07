# BrlMultiline: pinning an object to a segment.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Keeps an object visible in a segment while the focus moves elsewhere.

Regions are generated for the object in the ordinary way and marked for the segment they
belong to. The replacement for `BrailleHandler._doNewObject` clears only the segments
that are receiving new regions, so a pinned object survives focus changes without any
further intervention.
"""

from typing import TYPE_CHECKING, Iterator

import braille
import controlTypes
from braille.regions.focus import getFocusRegions
from logHandler import log

from .container import BrailleBufferContainer

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject
	from braille.regions.base import Region


class ObjectMonitor:
	"""An object pinned to one segment of the braille display."""

	def __init__(self, obj: "NVDAObject", segmentNumber: int) -> None:
		"""
		:param obj: the object to keep visible.
		:param segmentNumber: the segment to show it in.
		"""
		self.obj = obj
		self.segmentNumber = segmentNumber
		self.name = self._describe(obj)
		log.debug(f"Monitoring {self.name!r} in segment {segmentNumber}")

	@staticmethod
	def _describe(obj: "NVDAObject") -> str:
		""":return: a short name for an object, for announcing what is being monitored."""
		if obj.name:
			return obj.name
		try:
			return controlTypes.Role(obj.role).displayString
		except (ValueError, AttributeError):
			return str(obj.role)

	def getRegions(self) -> Iterator["Region"]:
		"""Generate braille regions for the monitored object, marked for its segment."""
		for region in getFocusRegions(self.obj, review=False):
			region.targetSegment = self.segmentNumber
			yield region

	def refresh(self) -> None:
		"""Redraw the monitored object in its segment."""
		container = braille.handler.mainBuffer if braille.handler else None
		if not isinstance(container, BrailleBufferContainer):
			return
		try:
			segment = container.segments[container.resolveSegmentNumber(self.segmentNumber)]
		except LookupError:
			log.debugWarning(f"Segment {self.segmentNumber} no longer exists")
			return
		try:
			regions = list(self.getRegions())
		except Exception:
			# The object may have died, which is not worth an error in the log.
			log.debugWarning(f"Could not generate regions for {self.name!r}", exc_info=True)
			return
		segment.clear()
		for region in regions:
			segment.append(region)
		container.update()
		if segment.regions:
			container.focus(segment.regions[-1])
		container.updateDisplay()

	def __repr__(self) -> str:
		return f"<ObjectMonitor {self.name!r} in segment {self.segmentNumber}>"
