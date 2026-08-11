# BrlMultiline: pinning an object to a segment.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Keeps an object visible in a segment while the focus moves elsewhere.

Regions are generated for the object in the ordinary way and marked for the segment they
belong to. The replacement for `BrailleHandler._doNewObject` clears only the segments
that are receiving new regions, so a pinned object survives focus changes without any
further intervention.

A monitor holds its segment's key rather than its number. Numbers are display order and are
reassigned whenever the view changes; a key is not, so a pin survives a rebuild as long as
the segment it names does. That is what lets a panel be laid over part of the display
without disturbing an object pinned outside the claim.
"""

from typing import TYPE_CHECKING, Iterator

import braille
import controlTypes
from braille.regions.focus import getFocusRegions
from logHandler import log

from .container import DisplayContainer

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject
	from braille.regions.base import Region


class ObjectMonitor:
	"""An object pinned to one segment of the braille display."""

	def __init__(self, obj: "NVDAObject", segmentKey: str) -> None:
		"""
		:param obj: the object to keep visible.
		:param segmentKey: the key of the segment to show it in.
		"""
		self.obj = obj
		self.segmentKey = segmentKey
		self.name = self._describe(obj)
		log.debug(f"Monitoring {self.name!r} in segment {segmentKey!r}")

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
			region.targetSegment = self.segmentKey
			yield region

	def refresh(self) -> None:
		"""Redraw the monitored object in its segment."""
		container = braille.handler.mainBuffer if braille.handler else None
		if not isinstance(container, DisplayContainer):
			return
		try:
			segment = container.segmentForKey(self.segmentKey)
		except LookupError:
			log.debugWarning(f"Segment {self.segmentKey!r} no longer exists")
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
		return f"<ObjectMonitor {self.name!r} in segment {self.segmentKey!r}>"
