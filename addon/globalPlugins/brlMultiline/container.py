# BrlMultiline: the whole braille display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A stand-in for NVDA's single braille buffer that fans out to several segments.

`BrailleHandler` owns exactly one `mainBuffer` and treats the display as one window onto
it. `DisplayContainer` takes that buffer's place, presents the combined result of several
segments as though it were one buffer, and dispatches individual operations to the segment
they belong to.

The container's own members are flat. A view composes panels, but `SegmentView.flatten`
dissolves them into an ordered list of segment specifications before the container is
built, so nothing here walks a tree: every operation iterates segments in display order.
The view is kept so that the next view can be composed from it, and so that the layout can
be reported, but no operation below consults it except to reach the panels for reporting.
"""

from collections.abc import Iterator, MutableSequence
from typing import TYPE_CHECKING, Any

import baseObject
from logHandler import log

from .layout import SegmentRect, findSegmentAtWindowPos, segmentPosToWindowPos
from .pinnedRegions import PinnedRegion
from .segments import BrailleBufferSegment

if TYPE_CHECKING:
	import textInfos
	from braille.brailleHandler import BrailleHandler
	from braille.regions.base import Region

	from .panels import SegmentSpec
	from .views import SegmentView

PLACEMENT_KEY_ATTRIBUTE = "_brlMultilineSegmentKey"
"""Attribute this add-on writes on a region to note which segment it was placed in.

Namespaced because it is set on NVDA's own `Region` objects. Deliberately not
`targetSegment`: that attribute is an owner's explicit destination, and conflating the two
would make this add-on's bookkeeping indistinguishable from a claim.
"""


class FakeRegionsList(MutableSequence):
	"""Stands in for a buffer's `regions` list, resolving to the focus segment.

	`BrailleHandler` appends to and indexes `mainBuffer.regions` directly rather than
	going through a method, so the container has to offer something list shaped. Every
	operation resolves the focus segment afresh, so that changing which segment holds
	focus does not leave this object pointing at the wrong list.
	"""

	def __init__(self, container: "DisplayContainer") -> None:
		self._container = container

	@property
	def _target(self) -> list["Region"]:
		return self._container.focusSegment.regions

	def __getitem__(self, index: Any) -> Any:
		return self._target[index]

	def __setitem__(self, index: Any, value: Any) -> None:
		self._target[index] = value

	def __delitem__(self, index: Any) -> None:
		del self._target[index]

	def __len__(self) -> int:
		return len(self._target)

	def insert(self, index: int, value: "Region") -> None:
		self._target.insert(index, value)

	def append(self, region: "Region") -> None:
		"""Add a region, honouring its `targetSegment` if it has one.

		Callers outside this add-on (NVDA's speech in braille, add-ons such as MathCAT)
		append to the focus segment simply by not setting the attribute.

		A region naming a segment that no longer exists is dropped rather than shown
		somewhere else. See L{DisplayContainer.resolvePlacementTarget}.
		"""
		index = self._container.resolvePlacementTarget(region)
		if index is None:
			return
		self._container.segments[index].append(region)

	def __repr__(self) -> str:
		return f"<FakeRegionsList {self._target!r}>"


class DisplayContainer(baseObject.AutoPropertyObject):
	"""The whole braille display, divided into independently scrolling segments.

	Presents the `BrailleBuffer` interface to `BrailleHandler`, without inheriting from
	it: almost every method would have to be overridden anyway, and inheriting would
	bring along a second set of unused buffer state.
	"""

	def __init__(self, handler: "BrailleHandler", view: "SegmentView") -> None:
		"""
		:param handler: the braille handler this container serves.
		:param view: the arrangement of the display to build. Everything is a view,
			including the plain single segment arrangement that matches NVDA's own
			behaviour.
		:raises ValueError: if the view's panels do not tile the display, or its segments
			overlap or share a key.
		:raises LookupError: if the view's focus segment does not exist.
		"""
		self.handler = handler
		dimensions = handler.displayDimensions
		self.numRows = dimensions.numRows
		self.numCols = dimensions.numCols
		view.validate(self.numRows, self.numCols)
		self.view = view
		self.specs: list["SegmentSpec"] = view.flatten()
		self.rects: list[SegmentRect] = [spec.rect for spec in self.specs]
		self.segments = [self._buildSegment(handler, spec) for spec in self.specs]
		self._byKey = {spec.key: index for index, spec in enumerate(self.specs)}
		self._focusSegmentNumber = self._byKey[view.focusSegmentKey]
		self.segments[self._focusSegmentNumber].isFocusBuffer = True
		self._regionsProxy = FakeRegionsList(self)
		# Informational copies of the combined buffer state, kept for code that reads
		# these attributes off a buffer directly. The handler itself uses the window
		# properties below.
		self.rawText = ""
		self.brailleCells: list[int] = []
		self.cursorPos: int | None = None
		log.debug(
			f"DisplayContainer: view {view.name!r}, {len(self.panels)} panels, "
			f"{len(self.segments)} segments on {self.numRows} rows by {self.numCols} columns",
		)

	def __repr__(self) -> str:
		return (
			f"<DisplayContainer view={self.view.name!r} {len(self.segments)} segments, "
			f"focus {self._focusSegmentNumber} ({self.focusSegmentKey!r}), "
			f"{self.numRows}x{self.numCols}>"
		)

	# Segment bookkeeping

	@property
	def numSegments(self) -> int:
		return len(self.segments)

	@property
	def panels(self) -> list:
		""":return: the panels the current view is composed of."""
		return self.view.panels

	@property
	def focusSegment(self) -> BrailleBufferSegment:
		"""The segment that tracks the system focus."""
		return self.segments[self._focusSegmentNumber]

	@property
	def focusSegmentKey(self) -> str:
		""":return: the key of the segment tracking the system focus."""
		return self.specs[self._focusSegmentNumber].key

	@property
	def reservedKeys(self) -> set[str]:
		""":return: the keys of segments claimed by a panel, which must be left alone."""
		return {spec.key for spec in self.specs if spec.isReserved}

	focusSegmentNumber: int
	"""Index of the segment tracking the system focus."""

	def _get_focusSegmentNumber(self) -> int:
		return self._focusSegmentNumber

	def _set_focusSegmentNumber(self, number: int) -> None:
		""":param number: index of the segment to give focus to. -1 means the last."""
		resolved = self.resolveSegmentNumber(number)
		self.segments[self._focusSegmentNumber].isFocusBuffer = False
		self._focusSegmentNumber = resolved
		self.segments[resolved].isFocusBuffer = True

	def resolveSegmentNumber(self, number: int | None) -> int:
		"""Turn a segment number from configuration or a script into a valid index.

		:param number: a segment index, -1 meaning the last segment, or None meaning the
			segment currently holding focus.
		:return: a valid index into L{segments}.
		:raises LookupError: if the number names no segment.
		"""
		if number is None:
			return self._focusSegmentNumber
		if number == -1:
			return len(self.segments) - 1
		if 0 <= number < len(self.segments):
			return number
		raise LookupError(f"No segment numbered {number}; there are {len(self.segments)}")

	def numberForKey(self, key: str) -> int:
		"""Turn a segment key into its index in display order.

		:param key: a segment key.
		:return: the index of that segment.
		:raises LookupError: if this view has no segment with that key.
		"""
		try:
			return self._byKey[key]
		except KeyError:
			raise LookupError(f"No segment keyed {key!r} in view {self.view.name!r}") from None

	def segmentForKey(self, key: str) -> BrailleBufferSegment:
		"""Find a segment by its stable key.

		:param key: a segment key.
		:return: that segment.
		:raises LookupError: if this view has no segment with that key.
		"""
		return self.segments[self.numberForKey(key)]

	def hasKey(self, key: str) -> bool:
		""":return: whether this view has a segment with a given key.

		Used after a rebuild to decide whether something holding a key, such as a pinned
		object, still has a segment to live in.
		"""
		return key in self._byKey

	def resolvePlacementTarget(self, region: "Region") -> int | None:
		"""Decide which segment a region should be written into.

		Two different things can be recorded on a region, and they are not
		interchangeable:

		1. `targetSegment` is an owner's explicit destination. It is a promise about where
			the content belongs, so a target that no longer exists means the owner's claim
			has gone and the content has nowhere legitimate to go. Delivery fails rather
			than falling back, because redirecting a table's cell content into the focus
			segment would write over the user's ordinary braille output.
		2. `PLACEMENT_KEY_ATTRIBUTE` is this add-on's own note of where an untargeted
			region was put. It is bookkeeping, never a destination, and is not consulted
			here at all. See L{findContainingSegment}.

		:param region: the region to place.
		:return: the index to write into, or None if the region cannot be delivered.
		"""
		target = getattr(region, "targetSegment", None)
		if target is None:
			# Untargeted content is NVDA's own, and belongs wherever the focus is.
			return self._focusSegmentNumber
		try:
			if isinstance(target, str):
				return self.numberForKey(target)
			return self.resolveSegmentNumber(target)
		except LookupError:
			log.debugWarning(
				f"Dropping a region targeted at segment {target!r}, which no longer exists; "
				f"the claim that owned it has gone",
			)
			return None

	def findContainingSegment(self, region: "Region") -> BrailleBufferSegment | None:
		"""Find the segment a region is already in.

		Used by the operations NVDA performs on a region it has handed over previously,
		where the question is not where the region belongs but where it actually is.
		Identity is checked first, because it is the only answer that cannot go stale: the
		recorded key is a note from a container that may since have been replaced.

		:param region: a region already written into a segment.
		:return: the segment holding it, or None if no segment does.
		"""
		for segment in self.segments:
			for candidate in segment.regions:
				if candidate is region:
					return segment
		# Not found by identity, so fall back to where it was last recorded as going. A
		# region can legitimately be absent, for instance after its segment was cleared.
		key = getattr(region, PLACEMENT_KEY_ATTRIBUTE, None)
		if isinstance(key, str) and self.hasKey(key):
			return self.segmentForKey(key)
		return None

	def recordPlacement(self, region: "Region", index: int) -> None:
		"""Note which segment a region was written into.

		Kept separate from `targetSegment` so that this add-on's bookkeeping can never be
		mistaken for an owner's explicit claim on a segment.

		:param region: the region that was placed.
		:param index: the segment it went into.
		"""
		setattr(region, PLACEMENT_KEY_ATTRIBUTE, self.specs[index].key)

	# Regions

	regions: Any
	"""The regions of the segment currently holding focus."""

	def _get_regions(self) -> FakeRegionsList:
		return self._regionsProxy

	def _set_regions(self, value: list["Region"]) -> None:
		# NVDA assigns a fresh list when showing speech output in braille, meaning the
		# assigned regions are now the whole of what the display shows. Write the contents
		# into the focus segment rather than losing the proxy, and empty every other
		# segment: in speech output mode NVDA stops handling focus, caret and review
		# moves, so document lines and pinned objects left elsewhere would stay under the
		# reader's fingers beside the speech, with nothing to ever refresh or remove them.
		for segment in self.segments:
			segment.clear()
		self.focusSegment.regions = list(value)

	visibleRegions: Any
	"""Every visible region across all segments."""

	def _get_visibleRegions(self) -> Iterator["Region"]:
		"""Yield the visible regions of every segment, the focus segment's last.

		The handler scans this to find the region belonging to an object that has
		updated. Covering all segments means a monitored object in a segment that does
		not hold focus still receives its updates.

		The handler scans in reverse and stops at the first region whose object matches,
		expecting the focus to be last. The regions showing the lines around the caret
		carry the focus region's own object, so the focus segment is yielded last whatever
		its position on the display; otherwise a layout whose focus segment is not the
		bottom one would update one of those lines and leave the focus region stale.
		"""
		for index, segment in enumerate(self.segments):
			if index != self._focusSegmentNumber:
				yield from segment.visibleRegions
		yield from self.focusSegment.visibleRegions

	# Whole display operations

	def clear(self, segment: int | str | None = None) -> None:
		"""Clear one segment, or the whole display.

		:param segment: index or key of the segment to clear, or None to clear every
			segment. NVDA calls this with no argument expecting the display to be emptied.
		"""
		if segment is None:
			for buffer in self.segments:
				buffer.clear()
			return
		if isinstance(segment, str):
			self.segmentForKey(segment).clear()
			return
		self.segments[self.resolveSegmentNumber(segment)].clear()

	def _buildSegment(self, handler, spec) -> BrailleBufferSegment:
		"""Build the segment one specification asks for.

		Every segment is one that *can* hold a flow, and until a controller is attached it
		behaves exactly as an ordinary segment does — every override in `FlowBufferSegment`
		falls back to its parent while there is nothing attached. That is what makes it
		reasonable to build them all this way, and what it buys is that a flow is no longer
		something only the band can have: a pinned object gets one in its own segment.

		The alternative was to decide the class from `ownerDrawsFocus`, which is a fact about
		the *band* — it hosts the focus and draws it — and had nothing to do with whether a
		flow could be shown. A pin hosts no focus and draws no focus, and was excluded by a
		question it was never being asked.

		:param handler: the real braille handler.
		:param spec: the specification to build.
		:return: the segment.
		"""
		# Imported here rather than at the top: `flowSegment` imports this module for its
		# type hints.
		from .flowSegment import FlowBufferSegment

		return FlowBufferSegment(handler, self, spec)

	def update(self) -> None:
		"""Update every segment, then recombine their state."""
		# The focus segment is updated first: regions elsewhere may depend on where the
		# caret ended up.
		self.focusSegment.update()
		for index, segment in enumerate(self.segments):
			if index != self._focusSegmentNumber:
				segment.update()
		self.rawText = "".join(segment.rawText for segment in self.segments)
		self.brailleCells = [cell for segment in self.segments for cell in segment.brailleCells]
		self.cursorPos = self.focusSegment.cursorPos

	def updateDisplay(self) -> None:
		if self is self.handler.buffer:
			self.handler.update()

	def saveWindow(self) -> None:
		"""Save every segment's window position so it can be restored after an update.

		A segment holding no regions cannot save a position. That is an ordinary state,
		not a failure, so it is recorded rather than raised.
		"""
		for segment in self.segments:
			try:
				segment.saveWindow()
				segment.hasSavedWindow = True
			except LookupError:
				segment.hasSavedWindow = False

	def restoreWindow(self) -> None:
		for segment in self.segments:
			if not segment.hasSavedWindow:
				continue
			try:
				segment.restoreWindow()
			except LookupError:
				# The regions the saved position referred to are gone.
				log.debug("Could not restore window position in a segment", exc_info=True)
				segment.hasSavedWindow = False

	# Combined output

	windowBrailleCells: Any
	"""The cells of the whole display, assembled from every segment."""

	def _get_windowBrailleCells(self) -> list[int]:
		"""Composite every segment's cells into one display sized array.

		NVDA's cell array is row major over the whole display, so a segment narrower
		than the display cannot simply be concatenated; each segment's rows are copied
		into place at its own origin. Cells no segment covers stay blank, which is how a
		panel that claims space in order to keep it empty gets its way.
		"""
		cells = [0] * (self.numRows * self.numCols)
		for segment in self.segments:
			rect = segment.rect
			segmentCells = segment.windowBrailleCells
			for position, cell in enumerate(segmentCells):
				if position >= rect.displaySize:
					# A segment should never produce more cells than it has, but never
					# let a miscount corrupt a neighbouring segment.
					log.debugWarning(f"Segment produced {len(segmentCells)} cells for {rect}")
					break
				row, col = divmod(position, rect.numCols)
				cells[(rect.row + row) * self.numCols + rect.col + col] = cell
		return cells

	windowRawText: Any
	"""The text of the whole display, in segment order."""

	def _get_windowRawText(self) -> str:
		return "".join(segment.windowRawText for segment in self.segments)

	cursorWindowPos: Any
	"""The cursor position on the display, taken from the focus segment."""

	def _get_cursorWindowPos(self) -> int | None:
		segment = self.focusSegment
		position = segment.cursorWindowPos
		if position is None:
			return None
		try:
			return segmentPosToWindowPos(segment.rect, position, self.numCols)
		except ValueError:
			log.debugWarning(f"Cursor position {position} is outside {segment.rect}", exc_info=True)
			return None

	# Region directed operations

	def focus(self, region: "Region") -> None:
		"""Bring a region into view within the segment holding it."""
		segment = self.findContainingSegment(region)
		if segment is None:
			log.debugWarning(f"Cannot focus region {region!r}; no segment holds it")
			return
		try:
			segment.focus(region)
		except LookupError:
			log.debugWarning(f"Cannot focus region {region!r}; it is not in {segment!r}", exc_info=True)

	def scrollTo(self, region: "Region", pos: int) -> None:
		"""Scroll the segment holding a region so that a position within it is visible."""
		segment = self.findContainingSegment(region)
		if segment is None:
			log.debugWarning(f"Cannot scroll to {pos}; no segment holds region {region!r}")
			return
		try:
			segment.scrollTo(region, pos)
		except LookupError:
			log.debugWarning(f"Cannot scroll to {pos} in region {region!r}", exc_info=True)

	# Position directed operations

	def routeTo(self, windowPos: int) -> None:
		"""Act on a cursor routing key press.

		The policy is the pressed segment's own, so a grid cell can treat a press as a
		selection while the segment beside it routes into text in the ordinary way.

		:param windowPos: position of the pressed key within the whole display window.
		"""
		try:
			index, segmentPos = findSegmentAtWindowPos(self.rects, windowPos, self.numCols)
		except LookupError:
			# The key was pressed in space a panel is keeping blank, which is not an error.
			log.debug(f"Routing key at {windowPos} is not in any segment")
			return
		self.segments[index].routingPolicy.route(self, index, segmentPos)

	def getTextInfoForWindowPos(self, windowPos: int) -> "textInfos.TextInfo | None":
		try:
			index, segmentPos = findSegmentAtWindowPos(self.rects, windowPos, self.numCols)
		except LookupError:
			return None
		return self.segments[index].getTextInfoForWindowPos(segmentPos)

	# Scrolling

	def scrollForward(self, segment: int | str | None = None) -> None:
		"""Scroll a segment forward.

		:param segment: index or key of the segment to scroll, or None for the focus
			segment. NVDA calls this with no argument.
		"""
		self._scroll(segment, forward=True)

	def scrollBack(self, segment: int | str | None = None) -> None:
		"""Scroll a segment back.

		:param segment: index or key of the segment to scroll, or None for the focus
			segment.
		"""
		self._scroll(segment, forward=False)

	def _scroll(self, segment: int | str | None, forward: bool) -> None:
		try:
			index = (
				self.numberForKey(segment) if isinstance(segment, str) else self.resolveSegmentNumber(segment)
			)
		except LookupError:
			log.debugWarning(f"Cannot scroll segment {segment!r}", exc_info=True)
			return
		buffer = self.segments[index]
		if index == self._focusSegmentNumber:
			# The focus segment scrolls exactly as an ordinary buffer does, including
			# moving to the next or previous line when the window cannot scroll further.
			if forward:
				buffer.scrollForward()
			else:
				buffer.scrollBack()
			return
		if getattr(buffer, "controller", None) is not None:
			# A flow reading in a segment of its own — a pinned document, list or table. Its
			# rows come from its controller rather than from this buffer's window, so moving
			# the window moves nothing the reader can feel: the cells are recomposed from the
			# controller on the next draw and come out exactly as they were. Panning belongs
			# to the controller, and asking the segment is how to reach it.
			#
			# Safe here for the same reason the fall-through below is not. A pinned flow is a
			# viewer: `FlowController.movesCursor` is false for a run of objects and the
			# source is built with `live=False` for a document, so panning it moves the
			# window and nothing else. What must never happen is the fall-through in NVDA's
			# own `scrollForward`, and that only happens when there is no controller.
			if forward:
				buffer.scrollForward()
			else:
				buffer.scrollBack()
			return
		# A segment that does not hold focus is panned within its own content only.
		# Falling through to NVDA's nextLine or previousLine would move the caret in an
		# object the user is not working in, which drags the system focus with it.
		moved = buffer._nextWindow() if forward else buffer._previousWindow()
		if moved:
			buffer.updateDisplay()
			return
		self._panPinnedContent(buffer, forward)

	def _panPinnedContent(self, buffer: BrailleBufferSegment, forward: bool) -> None:
		"""Move pinned content to the next or previous line, if it is content that may move.

		A segment holds one reading unit at a time, so a pin whose window has reached the end
		of its line has nothing further to pan into and would otherwise simply stop — which
		is the whole of a pinned document after its first line.

		The objection above does not apply to a region that keeps a reading position of its
		own: moving it changes nothing outside this segment, so there is no caret to drag the
		focus with. See `pinnedRegions`.

		:param buffer: the segment to pan.
		:param forward: True to move to the next line, False to the previous.
		"""
		region = buffer.regions[-1] if buffer.regions else None
		if not isinstance(region, PinnedRegion) or not region.panLine(forward):
			return
		buffer.update()
		try:
			cellCount = len(region.brailleCells)
			if forward or not cellCount:
				buffer.focus(region)
			else:
				# Panning back shows the end of the line moved onto, so that a document reads
				# the same in both directions rather than skipping every line's tail.
				buffer.windowEndPos = buffer.regionPosToBufferPos(region, cellCount - 1) + 1
		except LookupError:
			log.debugWarning("Could not place the window after panning a pinned segment", exc_info=True)
		buffer.updateDisplay()
