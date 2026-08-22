# BrlMultiline: driving a flow.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""What holds a window, a source and a renderer together.

The three pieces below it each know one thing and nothing about each other: `flow` knows
how rows are packed and where the window sits, `flowSources` knows how to find blocks and
read them, `flowRender` knows how to cut one block into rows. The controller is the only
place that knows all three, and it is deliberately the only place with a fetch loop in it.

Fetching is on demand and nowhere else. `FlowWindow` raises `ContentNeeded` when a move
would run off the cached rows, the controller asks the source for one more block, renders
it, and tries again — stopping when the window is full, when the source says the stream
ends, or when the budget says to show what we have. Nothing reads ahead speculatively,
which is what keeps a page with expensive accessibility calls from being walked further
than the display can show.

The controller also owns the two things a flow must be exact about:

1. **One active block.** The block holding the cursor is the only one that exposes a
	braille cursor, and the only one NVDA's commands may act on. See
	[flow-last-region-audit.md](../../../docs/design/flow-last-region-audit.md).
2. **Who moves.** In a live flow, panning moves the browse mode cursor to the top block of
	the new window, so speech and braille agree and routing can still activate a link. A
	viewer moves nothing outside its own band.
"""

import contextlib
from typing import TYPE_CHECKING, Optional

from logHandler import log

from . import flowForms, flowIndent
from .flow import (
	NO_POSITION,
	ByIdentity,
	ContentNeeded,
	Edge,
	EdgeState,
	FlowWindow,
	ResultKind,
	RowKind,
	assembleCells,
	cellSource,
)
from .panels import PanelOwner

if TYPE_CHECKING:
	from .flow import BlockId
	from .flowRender import FlowRenderer
	from .flowSources import DocumentFlowSource

MAX_FETCHES = 24
"""How many blocks one operation may add before giving up.

A backstop under the source's own budget, for the case where a source keeps answering with
blocks that add no rows. Never reached in ordinary reading: filling a Monarch from nothing
costs eight.
"""


def rowText(region, rendered, rowIndex: int) -> str:
	"""What one row of a rendered block actually reads.

	For the log and the dry run. The block's own text is not the answer: a paragraph
	occupying five rows would then be reported five times over, which reads as a repeated
	paragraph rather than as one paragraph laid out. The row's cells are mapped back through
	the region's `brailleToRawPos`, which is where contraction is accounted for.

	:param region: the region the block was read through.
	:param rendered: the rendering the row belongs to.
	:param rowIndex: which row of it.
	:return: the text on that row, empty if it cannot be worked out.
	"""
	raw = getattr(region, "rawText", "") or ""
	localRow = rowIndex - rendered.rowOffset
	if not raw or not 0 <= localRow < len(rendered.positions):
		return ""
	positions = [where for where in rendered.positions[localRow] if where != NO_POSITION]
	if not positions:
		return ""
	mapping = getattr(region, "brailleToRawPos", None)
	if not mapping:
		return raw[positions[0] : positions[-1] + 1]
	try:
		start = mapping[positions[0]]
		after = positions[-1] + 1
		end = mapping[after] if after < len(mapping) else len(raw)
	except IndexError:
		return raw
	return raw[start:end]


class FlowController(PanelOwner):
	"""Drives one band of flowing content."""

	def __init__(
		self,
		source: "DocumentFlowSource",
		renderer: "FlowRenderer",
		numRows: int,
		live: bool = False,
		movesCursor: Optional[bool] = None,
		indentStyle: str = flowIndent.DEFAULT_STYLE,
	) -> None:
		"""
		:param source: where the blocks come from.
		:param renderer: how a block becomes rows.
		:param numRows: the height of the band.
		:param live: whether this flow is the reader's own, and so shows them a cursor and
			writes their place back to NVDA.
		:param movesCursor: whether this flow's own reading position follows the window —
			panning puts it on the top block, routing puts it where the finger landed. True
			for a document, of either flavour. False for a run of objects, where the reading
			position is a selection: it is the application's to say, it changes only when a
			focus event says so, and panning past it is reading rather than moving. See
			`flowObjects`.
		:param indentStyle: how one level of depth is drawn, for content that has depth. See
			`flowIndent.INDENT_STYLES`.
		"""
		self.source = source
		self.renderer = renderer
		self.window = FlowWindow(numRows)
		self.live = live
		self.movesCursor = True if movesCursor is None else movesCursor
		self.indentStyle = indentStyle
		"""How one level of depth is drawn. Read once, since a setting changed mid reading
		rebuilds the band anyway."""

		self._following = False
		"""Guards against following a cursor move that this controller made itself."""

		self.activeBlockId: Optional["BlockId"] = None
		"""The block the cursor is in, and the only one that may show a cursor."""

		self.lastResult = None
		"""The source's last answer, so a caller can say why nothing appeared."""

		self._lastDirection = "not yet asked"
		"""Why the last placement went the way it did, for the diagnostics.

		Which end a block is brought onto the display at is the difference between a one row
		scroll and a whole display of movement, and it is decided by a comparison the reader
		cannot see and cannot feel the inputs to. Two hardware reports have now turned on it.
		"""

		self.onChanged = None
		"""Called when something moved the flow from underneath, so the band can redraw.

		Set by the segment showing this flow. NVDA's commands reach the active region
		directly, so a move can start below the controller rather than above it."""

		self.blocks = ByIdentity()
		"""The source block behind each rendering, by identity, for its region.

		Not a dictionary: a `BlockId` holds a bookmark, and a bookmark compares but does not
		hash. See `flow.ByIdentity`."""

		self._writingTop = None
		"""The last top block the reader was shown that was not the caret's own. See L{_stableTop}."""

		self.rereadWhileWriting = False
		"""Whether the last cursor move re-read the band as an edit being typed into.

		The segment consumes this to schedule a settle pass: a rich editor's answers at the
		moment of a keystroke can be transiently wrong — the probe caught a textarea
		answering the paragraph at the caret as everything before it — and every such state
		heals on the next re-read. A reader who pauses right after pressing return, which is
		exactly when they read the display, should not be the one to supply that re-read."""

	@contextlib.contextmanager
	def operation(self):
		"""One thing the reader asked for, sharing one budget.

		Arriving in a document, filling the band, panning it and following the cursor are
		each one operation. The budget is theirs rather than each source call's: reset per
		call it would bound nothing, since a band four rows tall makes four calls.
		"""
		budget = self.source.budget
		outer = budget.active
		if not outer:
			budget.start()
		try:
			yield budget
		finally:
			if not outer:
				try:
					self._rebaseIndent()
				finally:
					budget.finish()

	# Arriving.

	def enterAtCursor(self, contextRows: int = 0, atObject=None) -> bool:
		"""Place the window afresh at the cursor.

		Used on arrival and whenever the window has to be abandoned: a focus change, a
		jump, or a recovery that found nothing to hold on to.

		:param contextRows: how many rows of what precedes the cursor to show above it. A
			plain document asks for none, so the cursor's block sits at the top and the
			display fills downward.
		:param atObject: a form control the reader has arrived at, whose own place in the
			document is read rather than the cursor's, and whose prompt is then placed above
			it. See `flowForms` for why this is asked of the object rather than the text.
		:return: whether anything is now on the display.
		"""
		with self.operation():
			return self._enterAtCursor(contextRows, atObject)

	def _enterAtCursor(self, contextRows: int = 0, atObject=None) -> bool:
		""":return: whether anything is now on the display. See `enterAtCursor`."""
		result = self.source.blockAtCursor(atObject)
		self.lastResult = result
		if result.kind is not ResultKind.BLOCK or result.block is None:
			log.debugWarning(f"A flow could not enter at the cursor: {result.kind} {result.message}")
			return False
		self.window.blocks.clear()
		self.blocks.clear()
		self.window.setEdge(Edge.BEFORE, EdgeState.OPEN)
		self.window.setEdge(Edge.AFTER, EdgeState.OPEN)
		block = self._keep(result.block)
		self.window.appendBlock(self.renderer.render(block))
		self.window.enterAt(block.blockId)
		self._setActive(result.block.blockId)
		# The first rendering was made before this block was active, so it was made before
		# its live caret was known. Re-render now that the region can read the caret. This is
		# also what selects the right chunk of an edit taller than the rendering work set.
		self.refreshActive()
		if contextRows > 0:
			self._reachBack(contextRows)
		elif result.block.isControl:
			# A control's prompt is context, and context goes above it. What the reader has
			# arrived at is the field; what says what the field is for is behind them, and
			# on one row there has never been anywhere to put it.
			contextRows = self._labelContext(result.block.blockId)
		if contextRows > 0:
			self.window.enterAt(result.block.blockId, contextRows=contextRows)
		self.fill()
		return True

	def _labelContext(self, blockId: "BlockId") -> int:
		"""How far above a control to start the window, so that its prompt is on the display.

		One block is read backwards, and one is enough: the prompt is what immediately
		precedes the control, and how far above the window should start is decided from that
		block's own height. An earlier version asked for half a band of *rows* instead,
		which on an eight row Monarch fetched four blocks to use one and spent a third of
		the operation's budget doing it — the display then filled with the marker meaning
		there is more we have not read.

		:param blockId: the control's block.
		:return: how many rows above it the window should start, 0 to leave it at the top.
		"""
		if self.window.numRows <= 1:
			return 0
		try:
			index = self.window.blockIndex(blockId)
		except LookupError:
			return 0
		if index <= 0 and self._fetchOne(Edge.BEFORE):
			try:
				index = self.window.blockIndex(blockId)
			except LookupError:
				return 0
		if index <= 0:
			# Nothing above it: the control is the first thing in the document, or what is
			# above could not be read. It sits at the top, as any block does.
			return 0
		control = self.window.blocks[index]
		previous = self.window.blocks[index - 1]
		gapRows = (1 if previous.gapAfter else 0) + (1 if control.gapBefore else 0)
		return flowForms.contextRowsFor(previous.numRows, gapRows, self.window.numRows)

	def _reachBack(self, rows: int) -> None:
		"""Fetch rows above the window, which the window itself would never ask for.

		`_fill` makes up a shortfall, and a window anchored at its own top row has none: it
		is full, and there is nothing above it. Showing a control's label means reading what
		the window does not need, so this asks by row count instead.

		:param rows: how many rows are wanted above the top of the window.
		"""
		for _ in range(MAX_FETCHES):
			if self.window.rowsAbove() >= rows:
				return
			if self.source.budget.refuseIfExhausted():
				self.window.setEdge(Edge.BEFORE, EdgeState.DEFERRED)
				return
			if not self._fetchOne(Edge.BEFORE):
				return

	# Filling.

	def fill(self) -> None:
		"""Fetch whatever the window is short of, at both ends, and drop what is far away."""
		with self.operation():
			self._fillBothEnds()

	def _fillBothEnds(self) -> None:
		for edge in (Edge.AFTER, Edge.BEFORE):
			shortfall = self.window.shortfall(edge)
			if shortfall:
				self._fill(edge, shortfall)
		self._trim()

	def _rebaseIndent(self) -> None:
		"""Draw the band's depths again if the plan in force has stopped working.

		At the end of every operation rather than at any one of them, because arriving,
		filling, panning and following the cursor all change what is on the band, and the
		indent depends on nothing else. One pass, never a loop: laying the band out again
		changes how many rows each block takes, which can change which blocks are on the
		band, which could ask for a different plan again — and a display that settles only
		after several passes is a display that moves while the reader is reading it.

		`flowIndent.shouldRebase` is what keeps this quiet. It answers no while the plan can
		still draw what is there, so arrowing through a run of items at the same depth costs
		one comparison and nothing else, and prose costs the same comparison over a band of
		`None`.
		"""
		rendered = list(self.window.blocks)
		if not rendered:
			return
		depths = [block.depth for block in rendered]
		numCols = self.renderer.numCols
		if not flowIndent.shouldRebase(
			self.renderer.indentPlan,
			depths,
			numCols,
			style=self.indentStyle,
		):
			return
		plan = flowIndent.planFor(depths, numCols, style=self.indentStyle)
		if plan == self.renderer.indentPlan:
			return
		self.renderer.indentPlan = plan
		began = self.source.budget.clock()
		fresh = []
		for block in rendered:
			source = self.blocks.get(block.blockId)
			if source is None:
				# Rendered but no longer held, which `_trim` does not do to a window block.
				# Kept as it is rather than dropped: a row drawn at the old indent is wrong by
				# a few cells, and a row missing is wrong by a row.
				fresh.append(block)
				continue
			fresh.append(self.renderer.render(source, fromRow=block.rowOffset))
		# Laying the band out again is charged for, as a fetch and a chunk are. It is the one
		# piece of work here the reader did not ask for by name.
		self.source.budget.spend(self.source.budget.clock() - began)
		if not self.window.replaceBlocks(fresh):
			log.debug("BrlMultiline flow: the anchor did not survive a rebased indent")
			return
		# A shallower baseline makes blocks shorter, which can leave the band short of rows.
		# A deeper one only ever makes them taller, and the window trims its own surplus.
		self._fillBothEnds()

	def _trim(self) -> None:
		"""Keep the cache to the window and a margin either side.

		Without this a long reading session accumulates every block it has ever passed, and
		the margin rule — a window's worth either side, no more — is only a comment. The
		active block is kept whatever happens, since the commands act on it and it may sit
		outside the window while the reader pans away from their cursor.
		"""
		self.window.trim(marginRows=self.window.numRows)
		kept = ByIdentity()
		for block in self.blocks.values():
			isActive = self.activeBlockId is not None and block.blockId == self.activeBlockId
			if isActive or self.window.hasBlock(block.blockId):
				kept.set(block.blockId, block)
		self.blocks = kept

	def _fill(self, edge: Edge, rows: int) -> bool:
		"""Fetch until the window is no longer short at one end.

		:param edge: which end to fetch at.
		:param rows: how many rows are wanted.
		:return: whether the shortfall was made up.
		"""
		for _ in range(MAX_FETCHES):
			if self.window.shortfall(edge) <= 0:
				return True
			if self.source.budget.refuseIfExhausted():
				# Out of budget, not out of document. Said so on the display, so that the
				# rows the reader cannot see yet do not read as the end of the page.
				self.window.setEdge(edge, EdgeState.DEFERRED)
				return False
			if not self._fetchOne(edge):
				return False
		log.debugWarning(f"A flow stopped fetching {edge.value} after {MAX_FETCHES} blocks")
		return False

	def _fetchOne(self, edge: Edge) -> bool:
		"""Ask the source for one more block at one end.

		:param edge: which end to extend.
		:return: whether a block was added. False closes that end, with the reason recorded
			on the window so that the display can tell the end of a document from a budget
			stop.
		"""
		blocks = self.window.blocks
		if not blocks:
			return False
		if self.source.budget.refuseIfExhausted():
			# Every path that fetches — filling, panning, cursor reach and long-block
			# continuation — comes through here. Keeping the gate at that shared boundary
			# prevents a caller from accidentally turning a per-operation budget into a hint.
			self.window.setEdge(edge, EdgeState.DEFERRED)
			return False
		if self._continueBlock(edge):
			# The block at this end has more rows of its own. They come before the next
			# block does, or a long paragraph would be stepped over half read.
			return True
		anchorId = blocks[0].blockId if edge is Edge.BEFORE else blocks[-1].blockId
		result = (
			self.source.blockBefore(anchorId) if edge is Edge.BEFORE else self.source.blockAfter(anchorId)
		)
		if result.kind is not ResultKind.BLOCK or result.block is None:
			# END, DEFERRED and ERROR each mean something different on the display: the end
			# of the stream reads as blank rows, the other two as rows that say there is
			# more we have not got.
			self.window.setEdge(edge, result.edgeState)
			return False
		block = self._keep(result.block)
		began = self.source.budget.clock()
		rendered = self.renderer.render(block)
		# Laying a block out is charged for as well as reading it: both are work the reader
		# waits through, and on a heavy page either can be the slow one.
		self.source.budget.spend(self.source.budget.clock() - began)
		if edge is Edge.BEFORE:
			self.window.prependBlock(rendered)
		else:
			self.window.appendBlock(rendered)
		return True

	def _continueBlock(self, edge: Edge) -> bool:
		"""Render the next chunk of a block that is longer than one rendering.

		A very long paragraph is held a chunk at a time. The chunk that replaces this one
		overlaps it by a window, so the anchor is still inside it and the reader's place
		survives the change; rows are named by their place in the whole block, so the
		anchor means the same thing on either side of it.

		:param edge: which end of the stream is short.
		:return: whether a chunk was rendered.
		"""
		blocks = self.window.blocks
		if not blocks:
			return False
		forward = edge is Edge.AFTER
		rendered = blocks[-1] if forward else blocks[0]
		if forward and not rendered.moreRows:
			return False
		if not forward and rendered.rowOffset <= 0:
			return False
		block = self.blocks.get(rendered.blockId)
		if block is None:
			return False
		overlap = self.window.numRows
		if forward:
			fromRow = max(0, rendered.endRow - overlap)
		else:
			fromRow = max(0, rendered.rowOffset - self.renderer.maxRows + overlap)
		if fromRow == rendered.rowOffset:
			return False
		began = self.source.budget.clock()
		fresh = self.renderer.render(block, fromRow=fromRow)
		# A continuation is layout work just as a newly fetched block is. Without charging
		# it, one pathological paragraph can consume an unbounded operation one chunk at a
		# time while the block counter stays at zero.
		self.source.budget.spend(self.source.budget.clock() - began)
		if not fresh.rows or fresh.rowOffset == rendered.rowOffset:
			return False
		try:
			self.window.replaceBlock(fresh)
		except LookupError:
			return False
		return True

	def _keep(self, block):
		"""Remember a source block, so its region can be reached from its identity.

		A block already held keeps the region it was first read with, and the newly built
		one is discarded. Two things depend on that. NVDA queues a region object for update
		and comes back to it later, so swapping the object underneath would leave it
		holding one this flow no longer draws. And the region is where the block's own
		reading position lives, which is what a viewer pans within.

		:param block: the block just read.
		:return: the block to use, which may be one already held.
		"""
		existing = self.blocks.get(block.blockId)
		if existing is not None and self._sameRegionOwner(existing, block):
			return existing
		if existing is not None:
			oldRegion = getattr(existing, "region", None)
			if oldRegion is not None and hasattr(oldRegion, "onMoved"):
				oldRegion.onMoved = None
			if oldRegion is not None and hasattr(oldRegion, "onLine"):
				oldRegion.onLine = None
		self.blocks.set(block.blockId, block)
		region = getattr(block, "region", None)
		if region is not None and hasattr(region, "onMoved"):
			region.onMoved = self._regionMoved
		if region is not None and hasattr(region, "onLine"):
			region.onLine = self.shiftWindow
		if region is not None and hasattr(region, "dirty"):
			# Reading a block marks its region as read again, and this flow is the one that
			# asked. `dirty` means NVDA re-read the region, which is how a caret move inside
			# a document reaches a flow; a block the flow itself has just fetched would
			# otherwise read as a move the reader made, and send the window to the cursor
			# they are panning away from.
			region.dirty = False
		return block

	def _sameRegionOwner(self, existing, incoming) -> bool:
		"""Whether a freshly read block must keep the region already held for it.

		Ordinary re-reads keep the same region object because NVDA may have queued it for an
		update. Entering or leaving an edit in browse mode is different: the same document
		block changes between a document-owned region and a control-owned one. Two edit
		controls can also occupy the same document unit, so their actual objects are part of
		the answer.
		"""
		if bool(getattr(existing, "isInteractive", False)) != bool(getattr(incoming, "isInteractive", False)):
			return False
		if not getattr(incoming, "isInteractive", False):
			return True
		oldObj = getattr(getattr(existing, "region", None), "obj", None)
		newObj = getattr(getattr(incoming, "region", None), "obj", None)
		if oldObj is newObj:
			return True
		try:
			return bool(oldObj == newObj)
		except Exception:
			return False

	def shiftWindow(self, forward: bool) -> bool:
		"""Move the window by one block, taking nothing with it.

		What the line commands mean where the cursor must not move: a run of objects, whose
		cursor is a selection. Everywhere else `stepBlock` is the answer, and it takes the
		cursor because there the cursor is a reading position.

		:param forward: True to move on, False to move back.
		:return: whether the window moved.
		"""
		with self.operation():
			edge = Edge.AFTER if forward else Edge.BEFORE
			for _ in range(2):
				top = self.window.topBlockId()
				nextId = self.window.stepBlock(forward, fromBlockId=top) if top is not None else None
				if nextId is not None:
					self.window.enterAt(nextId)
					self.fill()
					self._redraw()
					return True
				if not self._fetchOne(edge):
					return False
			return False

	def _redraw(self) -> None:
		"""Tell whoever is showing this flow that it moved under them."""
		if self.onChanged is None:
			return
		try:
			self.onChanged()
		except Exception:
			log.debugWarning("A flow could not redraw after a move", exc_info=True)

	def _regionMoved(self, region) -> None:
		"""Answer a move that started in the region rather than here.

		NVDA's line commands and its routing act on the last region in the buffer, which in
		a flow is the block the cursor is in. When one of those moves the cursor, the window
		follows it exactly as it does for an arrow key.

		:param region: the region that moved.
		"""
		if self._following:
			return
		if self.live and self.movesCursor:
			# The real cursor moved, so the window follows it. Any other flow moved a
			# position of its own, which the object's cursor knows nothing about: asking
			# where the cursor is would send the band back to where the reader is not.
			self._following = True
			try:
				self.followCursor()
			finally:
				self._following = False
		if self.onChanged is not None:
			try:
				self.onChanged()
			except Exception:
				log.debugWarning("A flow could not redraw after a move", exc_info=True)

	def regionFor(self, blockId: "BlockId"):
		""":return: the region reading one block, or None if it is not held."""
		block = self.blocks.get(blockId)
		return getattr(block, "region", None)

	# Moving.

	def panForward(self) -> bool:
		"""Move the window on by a whole display, fetching if it has to."""
		return self._pan(forward=True)

	def panBack(self) -> bool:
		"""Move the window back by a whole display, fetching if it has to."""
		return self._pan(forward=False)

	def _pan(self, forward: bool) -> bool:
		"""Pan, answering `ContentNeeded` by fetching and trying again.

		:param forward: the direction to pan.
		:return: whether the window moved.
		"""
		with self.operation():
			return self._panWithin(forward)

	def _panWithin(self, forward: bool) -> bool:
		""":return: whether the window moved. See `_pan`."""
		for _ in range(MAX_FETCHES):
			try:
				moved = self.window.panForward() if forward else self.window.panBack()
			except ContentNeeded as needed:
				if self._fetchOne(needed.edge):
					continue
				# Nothing more that way: the window stays where it is rather than sliding
				# onto rows that are not there.
				return False
			if moved:
				self.fill()
				self._cursorToTop()
			return moved
		return False

	def stepBlock(self, forward: bool) -> bool:
		"""Move one block, as NVDA's next and previous line commands do.

		They move by a reading unit, which is a block here, and take the cursor with them.
		They also act on `regions[-1]`, which in a flow is the block at the bottom of the
		window rather than the one the cursor is in, so this steps from the active block
		instead.

		:param forward: True for the next block, False for the previous.
		:return: whether the cursor moved.
		"""
		with self.operation():
			return self._stepBlock(forward)

	def _stepBlock(self, forward: bool) -> bool:
		""":return: whether the cursor moved. See `stepBlock`."""
		if self.activeBlockId is None:
			return False
		nextId = self.window.stepBlock(forward, fromBlockId=self.activeBlockId)
		if nextId is None:
			edge = Edge.AFTER if forward else Edge.BEFORE
			if not self._fetchOne(edge):
				return False
			nextId = self.window.stepBlock(forward, fromBlockId=self.activeBlockId)
			if nextId is None:
				return False
		self._setActive(nextId)
		self._takeCursor(nextId)
		self.syncToCursor(forward=forward)
		return True

	def activeRegion(self):
		""":return: the region reading the block the cursor is in, or None.

		This is what NVDA's own commands must reach. `regions[-1]` is a real text region in
		a flow band for that reason: braille input only writes to the last region when it is
		a `TextInfoRegion`, and a stand-in would silently swallow untranslated dots and the
		erase that checks them.
		"""
		if self.activeBlockId is None:
			return None
		return self.regionFor(self.activeBlockId)

	def followCursor(self, ground: bool = False) -> bool:
		"""Answer a cursor move that was not a pan.

		Asks the source where the cursor is now, and moves the window by the smallest
		amount that brings that block into view — which is nothing at all when it is
		already there. A cursor that has gone somewhere the cache does not reach is a jump,
		and the window is placed afresh rather than scrolled to it.

		:param ground: put the cursor's block at the top of the band instead, with the
			document running on from it. What a jump by structure wants: the reader has
			left a section behind and is being set down in the next one, and a window
			nudged along from where they were says nothing about where they have arrived.
		:return: whether anything changed.
		"""
		with self.operation():
			return self._followCursor(ground=ground)

	def _followCursor(self, ground: bool = False) -> bool:
		""":return: whether anything changed. See `followCursor`."""
		return bool(self._arrive(ground=ground))

	def arriveAt(self, atObject=None, ground: bool = False) -> bool:
		"""Answer the reader arriving somewhere in the document already being read.

		Tab, the arrow keys and a quick navigation key all land here, and they do not mean
		the same thing. Tab is a move within a page the reader has their hands on: a target
		already on the display must not move it at all, and one just off it wants the least
		movement that shows it. Only a jump by structure grounds — there the reader has left
		a section behind and is being set down in the next one. Only a target the cache
		cannot reach is entered afresh.

		Placing the window afresh on every focus change was the earlier answer, and it moved
		the display on a Tab press to something the reader could already feel.

		:param atObject: what they arrived at, read at its own place in the document rather
			than at the cursor: a form control, a link, anything the document can locate.
			None reads at the cursor as ever.
		:param ground: put what they arrived at on the top row. See `followCursor`.
		:return: whether anything is on the display.
		"""
		with self.operation():
			return self._arrive(atObject=atObject, ground=ground) is not None

	def _arrive(self, atObject=None, ground: bool = False) -> Optional[bool]:
		"""Find where the reader is now and bring the window to it.

		:return: whether the window moved, or None if there was nothing to read.
		"""
		if self._writing():
			return self._readAgainWhileWriting(ground=ground)
		result = self.source.blockAtCursor(atObject)
		self.lastResult = result
		if result.kind is not ResultKind.BLOCK or result.block is None:
			return None
		blockId = result.block.blockId
		forward = self._isForward(blockId)
		if not self.window.hasBlock(blockId) and not self._reach(blockId, forward):
			return True if self._enterAtCursor(atObject=atObject) else None
		# Asked again, now that it can be answered. The first call was a guess: the block was
		# not in the window, so there was nothing to compare it with and `_isForward` says
		# "forward" when it cannot tell — which is the right guess for `_reach`, since reading
		# on is the commoner move, and the wrong one to place the window by. Fetching has
		# since put the block in the window, so the comparison is now a real one.
		#
		# On hardware this was moving from a folder up to the account above it: the account
		# was placed at the *bottom* of the band with its previous siblings filled in above,
		# a whole display of movement for a reader who had asked to move one item. Placing by
		# a guessed direction is placing by a coin toss whenever the cursor leaves the window.
		forward = self._isForward(blockId)
		self._keep(result.block)
		self._setActive(blockId)
		# The active region may have followed a caret within an edit, or replaced the
		# document-bound region with the edit's own. Lay that out before asking which cursor
		# row is visible; otherwise a cached block answers with yesterday's cursor position.
		self.refreshActive()
		moved = self.groundAt(blockId) if ground else self.syncToCursor(forward=forward)
		return moved

	def _writing(self) -> bool:
		""":return: whether the reader is typing into the document this flow reads."""
		return bool(self.live and getattr(self.source, "writing", False))

	def _readAgainWhileWriting(self, ground: bool = False) -> Optional[bool]:
		"""Read the whole band again from the caret, keeping it on the row it was on.

		A block's position is an offset into the document, and typing moves offsets. Insert a
		line and every position after it has moved; delete a selection and most of what the
		band was holding no longer exists. Re-rendering only the block the caret is in leaves
		the others reading at offsets that have gone, which on hardware was a line copied
		onto the row above it, and lines deleted by select-all and overtype still under the
		reader's fingers until the focus changed and forced a rebuild.

		So while the reader is writing, a caret update rebuilds the band rather than
		refreshing one block of it. It costs a band's worth of reads on each one, which is
		what the budget bounds and what the cost command measures; nothing cheaper is
		honest, because there is no way to know which of the other rows survived.

		The band is then put back where the reader had it, by the block that was on its top
		row. Typing must not scroll the display: what was above them stays above them, and
		adding a line moves the caret down a row rather than throwing the rest of the band
		upward. A top block that the edit removed cannot be gone back to, and there the
		caret's own block is the anchor — which is what select-all and overtype leaves.

		:param ground: put the caret's block on the top row instead.
		:return: whether anything is on the display, in the shape `_arrive` answers with.
		"""
		topId = self._stableTop()
		# Everything the source remembers about this document is an answer the edit may just
		# have changed: cached positions are offsets that typing moves, the exits of a
		# collapsed blank run point where the run was, and a block marked as having swallowed
		# the next line keeps truncating the stream after the editor has recovered. The
		# re-read exists because none of it can be trusted, so none of it is kept. The top
		# block is found again by value — a block id compares by where it starts, not by
		# which reading produced it — which is what the restore below has always relied on.
		self.source.forget()
		if not self._enterAtCursor():
			self._writingTop = None
			return None
		self.rereadWhileWriting = True
		if ground:
			self._writingTop = None
			return True
		if self._restoreTop(topId):
			self._writingTop = topId
		else:
			# The claim is kept: a restore that failed this moment — a transient refusal, a
			# top the edit removed — may succeed on the next keystroke, and dropping it here
			# would hand the next restore whatever block the heal below leaves on the top
			# row instead of the window the reader actually had.
			self._contextAboveTheCaret()
		self.fill()
		self.syncToCursor(forward=True)
		return True

	def _stableTop(self):
		"""The block the band's top row should be put back under, or None for no claim.

		The top row is only believed when it is not the caret's own block. At the moment a
		rich editor answers a return, the block built at the caret can be transiently merged
		— the probe caught a textarea answering the paragraph at the caret as everything
		before it — and that block takes the top row under an identity partway into the
		document. A restore that trusted the top row then anchored the band there on the
		next keystroke, and the reader's first lines were scrolled off the band by a block
		that never really existed. When the top row is the caret's, the claim used instead
		is the last top the reader was shown that was not — which after a transient is the
		window they actually had.
		"""
		top = self.window.topBlockId()
		if top is not None and (self.activeBlockId is None or top != self.activeBlockId):
			self._writingTop = top
			return top
		return self._writingTop

	def _restoreTop(self, topId) -> bool:
		"""Put the band back under the row it was showing before a writing re-read.

		:param topId: the block that was on the top row, or None for an empty band.
		:return: whether the band is anchored there again. False when the old top was the
			caret's own block, or reading back to it failed this moment — where the caller
			has a better answer than leaving the line being typed pinned to the top row.
		"""
		if topId is None or topId == self.activeBlockId:
			return False
		for _ in range(self.window.numRows):
			if self.window.hasBlock(topId):
				break
			if not self._fetchOne(Edge.BEFORE):
				break
		try:
			self.window.enterAt(topId)
		except LookupError:
			return False
		return True

	def _contextAboveTheCaret(self) -> None:
		"""Show the rows before the caret above it, as many as half the band.

		Reached when a writing re-read cannot put the band back by its old top row: the top
		was the caret's own block, or reading it back failed this moment. Anchoring the
		caret's line to the top row was the old answer, and it was sticky: the next
		keystroke found the caret's block already on the top row and kept it there, so one
		transient refusal in a rich editor left the reader typing on the top row with their
		earlier lines gone until the focus changed. Asking for the context afresh on every
		re-read heals the band the moment the editor answers again.

		Half the band, so the caret keeps rows below it for what follows. At the end of a
		document, where most writing happens, the rows above are the document and the rows
		below are blank either way, and a caret that has stopped moving row to row as lines
		are added is the sign the reader is at the half-way mark rather than lost.
		"""
		active = self.activeBlockId
		if active is None:
			return
		wanted = max(1, self.window.numRows // 2)
		self._reachBack(wanted)
		try:
			self.window.enterAt(active)
			rowsAbove = self.window.rowsAbove()
			if rowsAbove > 0:
				self.window.enterAt(active, contextRows=min(wanted, rowsAbove))
		except LookupError:
			log.debugWarning("Could not anchor the band at the caret", exc_info=True)

	def groundAt(self, blockId: "BlockId") -> bool:
		"""Put a block at the top of the band and let the document run on from it.

		:param blockId: the block to start from.
		:return: whether the window moved.
		"""
		try:
			self.window.enterAt(blockId)
		except LookupError:
			return self._enterAtCursor()
		self.fill()
		return True

	def _isForward(self, blockId: "BlockId") -> bool:
		"""Which way the cursor went, so that a scroll shows what it came from.

		Measured against the block the cursor was in, and against the anchor when that block
		is no longer one the window holds. The fallback is the point.

		The block the cursor was in is not guaranteed to survive: `_trim` keeps the cache to
		the window and a margin either side, and the block the reader has just left can be
		outside that — it stays in the controller's own cache, which is what the commands act
		through, but it goes from the window's list, which is what `blockIndex` reads. The
		comparison then raised, the answer was the fallback "forward", and a cursor that had
		gone *back* was placed at the bottom of the band with everything before it filled in
		above. On hardware that was moving up to the account row in a folder tree and getting
		a whole display of other accounts, which is a display's worth of movement for one
		keypress.

		The anchor cannot go the same way: it is where the window is, so the window holds it
		by definition. Comparing against it answers a slightly different question — is the
		cursor before or after where the display is sitting — and that is the question
		placement actually needs. Which is why it is the fallback and not the rule: while the
		cursor's own last block is still there, where the reader came *from* is the better
		account of which way they are travelling.

		:param blockId: where the cursor is now.
		:return: True when it moved on from what is shown, or when it cannot be told.
		"""
		now = self._windowIndex(blockId)
		if now is None:
			self._lastDirection = "forward: the cursor's block is not in the window"
			return True
		was = self._windowIndex(self.activeBlockId)
		if was is not None:
			forward = now >= was
			self._lastDirection = f"{'forward' if forward else 'back'}: measured from the block left behind"
			return forward
		anchor = getattr(self.window.anchor, "blockId", None)
		was = self._windowIndex(anchor)
		if was is None:
			self._lastDirection = "forward: nothing in the window to measure against"
			return True
		forward = now >= was
		self._lastDirection = (
			f"{'forward' if forward else 'back'}: measured from the anchor, "
			"the block left behind is no longer in the window"
		)
		return forward

	def _windowIndex(self, blockId: "Optional[BlockId]") -> Optional[int]:
		""":return: where a block sits in the window's list, or None if it is not in it."""
		if blockId is None:
			return None
		try:
			return self.window.blockIndex(blockId)
		except LookupError:
			return None

	def _reach(self, blockId: "BlockId", forward: bool) -> bool:
		"""Fetch towards a block the cache does not hold yet.

		Bounded: a cursor that has gone further than a few blocks is a jump rather than a
		step, and is answered by placing the window afresh.

		:param blockId: the block wanted.
		:param forward: which way to look for it.
		:return: whether it was reached.
		"""
		# Both ways, because which way the cursor went cannot always be told: the block it
		# is in is not in the window, so there is nothing to compare it with. Looking the
		# wrong way first costs a few reads; not looking the other way at all throws the
		# reader's window away and rebuilds it around the cursor.
		for edge in self._edgesToTry(forward):
			for _ in range(self.window.numRows):
				if not self._fetchOne(edge):
					break
				if self.window.hasBlock(blockId):
					return True
		return False

	def _edgesToTry(self, forward: bool) -> tuple:
		""":return: the ends to look for a block at, the likelier one first."""
		return (Edge.AFTER, Edge.BEFORE) if forward else (Edge.BEFORE, Edge.AFTER)

	def syncToCursor(self, forward: bool = True) -> bool:
		"""Bring the active block onto the display, moving as little as possible.

		If it is already there, nothing moves at all: landing on a heading that is already
		under the reader's fingers must not jerk the display.

		:param forward: the direction the reader is travelling.
		:return: whether the window moved.
		"""
		if self.activeBlockId is None:
			return False
		# The cursor's own row, not the block's first: a paragraph or an edit field taller
		# than the band would otherwise be brought on by its top while the reader is at the
		# bottom of it, which is the whole of what "the cursor's row must stay visible" is
		# about.
		row = self.cursorRow()
		try:
			moved = self.window.ensureVisible(
				self.activeBlockId,
				rowIndex=row if row is not None else 0,
				forward=forward,
			)
		except LookupError:
			return self.enterAtCursor()
		if moved:
			self.fill()
		return moved

	def _takeCursor(self, blockId: "BlockId") -> bool:
		"""Move the real cursor to a block, for a live flow.

		Selecting a block and moving the cursor to it are two things, and only the second
		is what makes speech follow, what the arrow keys carry on from, and what a routing
		key would activate. A viewer does neither.

		:param blockId: the block to move to.
		:return: whether the cursor was moved.
		"""
		if not (self.live and self.movesCursor):
			return False
		region = self.regionFor(blockId)
		takeCursor = getattr(region, "takeCursor", None)
		if takeCursor is None:
			return False
		return bool(takeCursor())

	def _cursorToTop(self) -> None:
		"""Put the cursor on the top block of the window, after a pan.

		Reading onward with the arrow keys then continues from the top of what is under the
		reader's hands, in both directions.

		Only where the flow owns the cursor. A run of objects does not: the reader's place
		is the focused item and panning is reading past it, so making the new top row active
		would claim they had moved. It did claim exactly that — and because the focus had of
		course not moved, the next refresh followed the real focus and dragged the display
		back to where the reader had panned away from.
		"""
		if not self.movesCursor:
			return
		topId = self.window.topBlockId()
		if topId is None:
			return
		self._setActive(topId)
		self._takeCursor(topId)

	def _setActive(self, blockId: "BlockId") -> None:
		"""Make one block the active one, and no other.

		Every block holds a collapsed position, so every block looks to NVDA like it holds
		a cursor. Only the active one is allowed to say so.
		"""
		self.activeBlockId = blockId
		for block in self.blocks.values():
			region = getattr(block, "region", None)
			if region is None:
				continue
			region.isActive = block.blockId == blockId
		self._giveTheActiveBlockItsCursor()

	def _giveTheActiveBlockItsCursor(self) -> None:
		"""Re-read the block that has just become active, so that it has a cursor.

		A block is read before anything knows whether it is the one the cursor is in, and a
		block that is not active suppresses its cursor — so the reading that built it left
		no cursor behind. Every path that ends by activating a block without reading it
		again would then show none, which is what happened after a jump backwards: the
		display moved to the right place with nothing on it to say where the cursor was.
		"""
		if not self.live:
			return
		region = self.activeRegion()
		if region is None or getattr(region, "brailleCursorPos", None) is not None:
			return
		try:
			region.update()
		except Exception:
			log.debugWarning("Could not read the active block for its cursor", exc_info=True)
			return
		# The flow asked for this reading, so it is not news to be acted on again.
		region.dirty = False

	def refreshActive(self) -> bool:
		"""Re-render the block the cursor is in, after it changed under the reader.

		Only that block, because that is the one NVDA marks as pending on the core cycle.

		:return: whether the rendering changed.
		"""
		if self.activeBlockId is None:
			return False
		block = self.blocks.get(self.activeBlockId)
		if block is None:
			return False
		try:
			block.region.update()
		except Exception:
			log.debugWarning(f"Could not refresh {self.activeBlockId}", exc_info=True)
			return False
		if hasattr(block.region, "dirty"):
			# This flow asked for the reading, so it is not news to be acted on again. See
			# `_keep`, where the same rule is applied to a block a fetch has just built.
			block.region.dirty = False
		try:
			before = self.window.blocks[self.window.blockIndex(self.activeBlockId)]
		except LookupError:
			return False
		rendered = self._renderActiveChunk(block, before)
		if (
			rendered.rows == before.rows
			and rendered.positions == before.positions
			and rendered.rowOffset == before.rowOffset
		):
			return False
		self.window.replaceBlock(rendered)
		# A multi line edit grows into the space as it is typed into, and what the reader
		# wants on the display is what they have just written rather than the top of the
		# field. The window follows the growing end down.
		self.syncToCursor(forward=True)
		self.fill()
		return True

	def _renderActiveChunk(self, block, before):
		"""Render the chunk which contains the active caret.

		A rendering is deliberately capped at 64 rows. That cap is a memory working set, not
		a text-length limit, so an edit's caret beyond it must select a later chunk. Keep the
		current chunk when it still contains the cursor; otherwise ask the renderer to locate
		the cursor without imposing a text-length cap.
		"""
		at = getattr(block.region, "brailleCursorPos", None)
		rendered = self.renderer.render(block, fromRow=before.rowOffset)
		if at is None or any(at in row for row in rendered.positions):
			return rendered
		return self.renderer.renderAround(
			block,
			at,
			contextRows=max(0, self.window.numRows - 1),
		)

	def cursorRow(self) -> Optional[int]:
		"""Which row of the active block the cursor is on, counted within the whole block.

		Not which row of the display: the point of asking is usually that it is not on the
		display and has to be brought back.

		:return: the row, or None if there is no cursor or the block is not cached.
		"""
		if self.activeBlockId is None:
			return None
		region = self.regionFor(self.activeBlockId)
		at = getattr(region, "brailleCursorPos", None)
		if at is None:
			return None
		try:
			rendered = self.window.blocks[self.window.blockIndex(self.activeBlockId)]
		except LookupError:
			return None
		highest = NO_POSITION
		for index, positions in enumerate(rendered.positions):
			if at in positions:
				return rendered.rowOffset + index
			for where in positions:
				highest = max(highest, where)
		# A cursor the chunk on the display cannot place. `refreshActive` normally replaces
		# that chunk with the one holding it, so this is the gap between the caret moving and
		# the flow noticing — and which way to guess depends on which way it went.
		if not rendered.rows or at <= highest:
			# Behind what is shown, or nowhere the rendering can account for. Naming a row
			# would move the window to the wrong end of a long edit on no evidence.
			return None
		# Past the end of what is shown, which is where a reader writing always is: the last
		# row is the honest guess, and the one that keeps what they have typed on the display.
		return rendered.endRow - 1

	# Showing.

	def cells(self) -> list[int]:
		"""The band's cells, row major, padded to the full width.

		The padding is the point: it is what stops the block after a short one sharing its
		row, which NVDA's own buffer cannot avoid.
		"""
		try:
			return assembleCells(self.window, self.renderer.numCols)
		except LookupError:
			return [0] * (self.window.numRows * self.renderer.numCols)

	def cursorCell(self) -> Optional[int]:
		"""Where the cursor is within the band, as a flat row major position.

		:return: the position, or None if the active block is not on the display or has no
			cursor in it.
		"""
		if self.activeBlockId is None:
			return None
		region = self.regionFor(self.activeBlockId)
		at = getattr(region, "brailleCursorPos", None)
		if at is None:
			return None
		numCols = self.renderer.numCols
		for position in range(self.window.numRows * numCols):
			source = cellSource(self.window, numCols, position)
			if source is not None and source[0] == self.activeBlockId and source[1] == at:
				return position
		return None

	def routeTo(self, position: int) -> bool:
		"""Act on a routing key press within the band.

		:param position: a flat row major position within the band.
		:return: whether the press reached content. Padding, gaps and blank rows reach
			nothing, and must do nothing: NVDA's habit of mapping trailing blank columns
			back to the last content cell would otherwise activate the end of a short field
			when the reader pressed a key in the space after it.
		"""
		source = cellSource(self.window, self.renderer.numCols, position)
		if source is None:
			return False
		blockId, at = source
		block = self.blocks.get(blockId)
		if block is not None and getattr(block, "isDecoration", False):
			# The blank row a menu separator stands for. There is nothing there to go to.
			return False
		region = self.regionFor(blockId)
		if region is None:
			return False
		try:
			# The region first, and the flow's own place afterwards. A region decides what a
			# press means from whether the reader was already on that block — an object's
			# does, because a press where they are acts and a press elsewhere goes — and
			# making the block active first told it they had been there all along, so a
			# routing key meant to reach a list item activated it.
			region.routeTo(at)
		except Exception:
			log.debugWarning(f"Could not route into {blockId}", exc_info=True)
			return False
		if self.movesCursor:
			self._setActive(blockId)
		return True

	def fillStats(self) -> tuple[int, int]:
		"""How much of the band is content rather than the space after a row's last cell.

		The measure that tells one reading unit apart from another on a given display: a
		block always starts a new row, so every block leaves the tail of its last row empty,
		and how much that costs depends on the block's length against the band's width.

		:return: the cells holding content, and the cells in the band.
		"""
		total = self.window.numRows * self.renderer.numCols
		used = 0
		try:
			visible = self.window.visibleRows()
		except LookupError:
			return 0, total
		for row in visible:
			if row.kind is not RowKind.CONTENT or row.blockId is None:
				continue
			try:
				rendered = self.window.blocks[self.window.blockIndex(row.blockId)]
			except LookupError:
				continue
			localRow = row.rowIndex - rendered.rowOffset
			if 0 <= localRow < len(rendered.rows):
				used += min(len(rendered.rows[localRow]), self.renderer.numCols)
		return used, total

	@property
	def lastDirection(self) -> str:
		""":return: why the last placement went the way it did. See `_lastDirection`."""
		return self._lastDirection

	def describeRows(self) -> list[str]:
		"""What each row of the band holds, in words rather than cells.

		For the dry run command and for the log: it shows the packing and the window
		without a display attached, which is where the design is easiest to get wrong.

		:return: one line per row of the band.
		"""
		numCols = self.renderer.numCols
		lines: list[str] = []
		try:
			visible = self.window.visibleRows()
		except LookupError:
			return ["(no anchor)"]
		for index, row in enumerate(visible):
			if row.kind is RowKind.BLANK:
				lines.append(f"{index}: (end of content)")
				continue
			if row.kind is RowKind.PENDING:
				lines.append(f"{index}: (more, not fetched)")
				continue
			if row.kind is RowKind.GAP or row.blockId is None:
				lines.append(f"{index}: (gap)")
				continue
			region = self.regionFor(row.blockId)
			rendered = self.window.blocks[self.window.blockIndex(row.blockId)]
			localRow = row.rowIndex - rendered.rowOffset
			cells = rendered.rows[localRow] if 0 <= localRow < len(rendered.rows) else ()
			active = " *" if row.blockId == self.activeBlockId else "  "
			block = self.blocks.get(row.blockId)
			# Said on every row of the block rather than only its first: the log is read a
			# row at a time, and a form is exactly where knowing which rows are the control
			# and which are its prompt is the thing being checked.
			kind = " control" if block is not None and block.isControl else ""
			# Said per row, because a block's first row and its wrapped rows are deliberately
			# indented by different amounts, and one number for the whole block could not
			# show that rule working.
			depth = ""
			if rendered.depth is not None:
				plan = self.renderer.indentPlan
				drawn = (
					plan.cellsFor(rendered.depth)
					if row.rowIndex == 0
					else plan.continuationCellsFor(rendered.depth)
				)
				depth = f" depth {rendered.depth} indent {drawn}"
			rowExtent = f"block row {row.rowIndex + 1}"
			if not rendered.moreRows:
				# The last chunk knows the complete extent. An earlier chunk does not, and
				# calling its working-set size the block total produced reports such as
				# "block row 13 of 8".
				rowExtent += f" of {rendered.endRow}"
			lines.append(
				f"{index}:{active}[{len(cells)}/{numCols} cells]{kind}{depth} "
				f"{rowExtent}: {rowText(region, rendered, row.rowIndex)!r}",
			)
		return lines

	def __repr__(self) -> str:
		flavour = "live" if self.live else "viewer"
		return f"<FlowController {flavour} {self.window!r}>"
