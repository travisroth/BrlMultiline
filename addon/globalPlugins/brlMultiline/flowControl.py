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
	BLANK_CELL,
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
	from .flow import BlockId, RenderedBlock, SourceBlock
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
	own = getattr(region, "textForPositions", None)
	if own is not None:
		# A region whose positions are not offsets into one run of text answers for itself.
		# A table row is the case: its cells come from different places, so its positions
		# carry which column as well as where in it, and read as offsets they are enormous
		# numbers that fall off the end of any map. This function then returned the row's
		# whole flat text for every row of it — so the report showed a reader the full
		# contents of columns their display was truncating, which is the opposite of what a
		# report is for.
		return own(positions)
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
		lineFocus: bool = True,
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
		:param lineFocus: whether to mark the left of the focused row where it is indented
			far enough to carry the mark. See `_markLineFocus`.
		"""
		self.source = source
		self.renderer = renderer
		self.window = FlowWindow(numRows)
		self.live = live
		self.movesCursor = True if movesCursor is None else movesCursor
		self.indentStyle = indentStyle
		"""How one level of depth is drawn. Read once, since a setting changed mid reading
		rebuilds the band anyway."""

		self.lineFocus = lineFocus
		"""Whether the focused row is marked at the left. See `_markLineFocus`."""

		self._following = False
		"""Guards against following a cursor move that this controller made itself."""

		self.activeBlockId: Optional["BlockId"] = None
		"""The block the cursor is in, and the only one that may show a cursor."""

		self.lastResult = None
		"""The source's last answer, so a caller can say why nothing appeared."""

		self._lastDirection = "not yet asked"
		"""What the direction test last concluded, for the diagnostics.

		Which end a block is brought onto the display at is the difference between a one row
		scroll and a whole display of movement, and it is decided by a comparison the reader
		cannot see and cannot feel the inputs to. Three hardware reports have now turned on
		it.
		"""

		self.hasBeenToTheEnd = False
		"""Whether the source has ever said there is no more after the last block read.

		What tells content that is *growing* from content that is merely longer than the
		band, and the difference decides whether anything is allowed to scroll on its own.
		A pinned page has more below it from the moment it is pinned, and following that
		would walk the display through the document a block at a time on a timer. A chat
		read to its end and then written in has the same open edge a moment later — the
		difference is that somebody once reached the end of it.

		Never unset. A reader who has been to the end of a thing has been there, and panning
		back into its history does not make what arrives afterwards new content of a
		different kind. What stops the display moving while they read back is that it only
		ever moves at the tail.
		"""

		self.edgeReasons: dict = {}
		"""Why each end of the stream was declared, by edge, for the report.

		"There is no more this way" is the one answer a reader disputes: they can see the
		document goes on and NVDA pans through it. A walk has several ways of concluding it
		has finished and they are different faults, so the report has to name which.
		"""

		self._placements: list[str] = []
		"""What has actually moved the band, most recent last. See `_note`."""

		self.onChanged = None
		"""Called when something moved the flow from underneath, so the band can redraw.

		Set by the segment showing this flow. NVDA's commands reach the active region
		directly, so a move can start below the controller rather than above it."""

		self.pinnedBlock: Optional["SourceBlock"] = None
		"""A block held above the window and drawn on the band's top row, or None.

		A table's header row is the case it exists for, and the reason it is not simply the
		first block of the stream is what the reader found: a layout turned on from the middle
		of a table never showed the headers at all, because the window starts where the reader
		is. A pinned row is outside the window, so where the reader is does not decide whether
		they can see what the columns are.

		The window is one row shorter when there is one. That is decided when the controller
		is built and does not change while it lives, so nothing under the pinned row ever
		moves because of it.
		"""

		self.pinned: Optional["RenderedBlock"] = None
		"""The pinned block as it is drawn. Redrawn on each `cells`; see `refreshPinned`."""

		self.blocks = ByIdentity()
		"""The source block behind each rendering, by identity, for its region.

		Not a dictionary: a `BlockId` holds a bookmark, and a bookmark compares but does not
		hash. See `flow.ByIdentity`."""

		self._pannedAt = None
		"""Where the caret was when the reader last panned, or None if they have not.

		What stops panning being undone by the cursor move panning itself caused. A live flow
		writes its reading position back as it pans, NVDA reports that position, and the band
		is asked to show the caret — whose row is the one that was just panned away from. On a
		band several rows tall the caret's row is usually still on it and nothing happens; on
		a band one row tall it is never on it, so every pan snapped straight back and the
		display flickered between the two. See `_panIsTheReadersChoice`.
		"""

		self._writingTop = None
		"""The last top block the reader was shown that was not the caret's own. See L{_stableTop}."""

		self._pannedCaret: tuple = ()
		"""Where the caret counts as being if nothing has been typed since the reader panned.

		The claim a written-in document's re-read has to respect. Not `_pannedAt`, which is
		the *active block* and the cursor within it: panning moves the active block itself —
		see `_cursorToTop` — so it answers "has the band moved", and what a re-read needs to
		know before it throws the band away is "has anything been typed".

		More than one position, because a caret moved by panning has two right answers while
		the application catches up. See `_caretsAfterAPan`."""

		self._owedAbove = 0
		"""How many rows of context above the caret a writing re-read could not afford.

		**The rows above are the ones that go missing, because they are read last.** A
		keystroke re-reads the band from the caret: the caret's own block, then the rows
		below it, and only then the walk back to what was above. On a tall display that walk
		is most of the work — nine rows of a Monarch against four of a Focus — and it is what
		the allowance runs out during, so the reader typing at the end of a document was left
		with their line and blank rows where everything they had written should be.

		Nothing healed it either. The band asks again whenever an end of it is short, and
		this end is not short: the window is anchored at the caret's block with nothing above
		it, so there is no shortfall to make up and the pass that comes back had nothing to
		ask for. So the debt is written down here and paid on that pass instead. See
		`_contextAboveTheCaret` and `_payWhatIsOwedAbove`."""

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
		# Whatever was owed was owed by the band being replaced here.
		self._owedAbove = 0
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

	@property
	def hasMoreToFetch(self) -> bool:
		""":return: whether an end of the band is short of content the source still has.

		Deferred, which is the budget having run out rather than the document having ended.
		The two look the same to a reader — rows with nothing in them — and are opposites:
		one is the document finishing and the other is this add-on giving up part way.
		"""
		return any(state is EdgeState.DEFERRED for state in self.window.edges.values())

	def fill(self) -> bool:
		"""Fetch whatever the window is short of, at both ends, and drop what is far away.

		:return: whether anything came back. **Not whether the band is full**: a fill cut
			short by the budget adds what it could and leaves the rest, and the caller that
			comes back for the rest — see `FlowBand._fillMore` — needs to know that asking
			again is worth it. A pass that adds nothing is a document that will not answer,
			and asking it again is how a refusal turns into a loop.
		"""
		with self.operation():
			return self._fillBothEnds()

	def _fillBothEnds(self) -> bool:
		""":return: whether anything came back at either end. See `fill`."""
		added = self._payWhatIsOwedAbove()
		for edge in (Edge.AFTER, Edge.BEFORE):
			shortfall = self.window.shortfall(edge)
			if shortfall:
				added = self._fill(edge, shortfall) or added
		self._trim()
		return added

	def _payWhatIsOwedAbove(self) -> bool:
		"""Fetch context above the caret that a writing re-read could not afford at the time.

		Asked on every fill, which is what the band's continuation pass calls: the debt is
		paid a fetch or two at a time, on a fresh allowance each pass, until the rows the
		reader had above them are back or the document says there are no more. Neither the
		reader's keystroke nor their next arrival waits for any of it.

		:return: whether anything came back, so that the pass knows to come again.
		"""
		wanted = self._owedAbove
		active = self.activeBlockId
		if not wanted or active is None:
			self._owedAbove = 0
			return False
		before = self.window.rowsAbove()
		self._reachBack(wanted)
		rowsAbove = self.window.rowsAbove()
		if rowsAbove < wanted and self.window.edges[Edge.BEFORE] is EdgeState.DEFERRED:
			# Short still, and still being refused rather than answered. The rows are kept and
			# the band is left exactly where it is: moving it up a row per pass would walk the
			# display under a reading hand three times to arrive where one move gets it.
			return rowsAbove > before
		# Paid, or there is nothing left to pay it with — the document starts here. Either way
		# the debt is over and what was reached goes above the caret.
		self._owedAbove = 0
		if rowsAbove <= 0:
			return False
		try:
			self.window.enterAt(active, contextRows=min(wanted, rowsAbove))
		except LookupError:
			log.debugWarning("Could not put back the rows above the caret", exc_info=True)
		return rowsAbove > before

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
		depths = self._visibleDepths(rendered)
		if not depths:
			# Nothing on the band to plan for. The plan in force is left alone rather than
			# reset: a band with no content rows says nothing about how deep anything is, and
			# answering "flat" to that would redraw the reader's indent on no evidence.
			return
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
		self._redrawBlocks(why="a rebased indent")

	def useColumnPage(self, plan) -> bool:
		"""Show a page of columns, and read what the page needs.

		Three things that have to happen together and in this order, which is why they are
		one method rather than a caller's job. The source is told which columns to read,
		because every cell is a search of the document and a column on another page is a
		search for something nobody will feel. The rows are read again, because the ones read
		for the old page hold the wrong cells and drawing them under the new plan would put
		the old page's values at the new page's offsets. And the pinned header is rebuilt,
		for the same reason and because it is outside the window that the re-read walks.

		It lived on the band until a pinned table could be laid out in columns too. A pin is
		a flow like any other and pages like one; what the band adds is only which flow the
		reader meant.

		:param plan: the layout, on the page wanted.
		:return: whether the page changed.
		"""
		if plan.at == self.renderer.columnPlan.at:
			return False
		setColumns = getattr(self.source, "setColumns", None)
		if setColumns is not None:
			setColumns(tuple(place.column.index for place in plan.placements()))
		changed = self.setColumnPlan(plan, reread=True)
		if self.pinnedBlock is not None:
			header = getattr(self.source, "headerBlock", None)
			if header is not None:
				try:
					said = header()
				except Exception:
					log.debugWarning("Could not read the header for a new page", exc_info=True)
					said = None
				if said is not None:
					self.setPinned(said)
				else:
					# The columns that have just come on may be ones the table names nothing
					# for, and a page of unnamed columns is not a table without headers. What
					# stands is the row that was there, which is at worst out of date and at
					# best exactly right; clearing it costs the reader the row itself, and
					# nothing asks again until the layout is made afresh.
					log.debugWarning("No header row for this page of columns, so the last one stands")
		return changed

	def setColumnPlan(self, plan, reread: bool = False) -> bool:
		"""Draw the band's table again with a different column layout.

		What moves the band across a table too wide for it: the plan holds every column and
		says which page each is on, and this is how a different page reaches the display.

		:param plan: the layout to draw with. See `flowTable.ColumnPlan`.
		:param reread: read the rows again before drawing them. Wanted when the source has
			been told to read different columns, since the rows it read before hold the cells
			of the page that has just been left.
		:return: whether anything changed.
		"""
		if plan == self.renderer.columnPlan and not reread:
			return False
		with self.operation():
			self.renderer.columnPlan = plan
			if reread:
				self._rereadBlocks()
			self._redrawBlocks(why="a different page of columns")
		return True

	def rereadContent(self) -> bool:
		"""Read the blocks on the band again, in place, and draw what comes back.

		The window keeps its place, the blocks keep their identities, and what changes is what
		the source now says is in them. That is the difference between this and rebuilding:
		a reader whose hand is on row four wants row four to hold this second's price, not to
		be moved somewhere while the band starts again.

		Only a source that can be asked for a block by its identity answers — a table can,
		because a row is named by its number — and everything else is left exactly as it was.

		:return: whether anything was re-read at all. Whether it *changed* is the caller's to
			decide, by looking at the cells before and after; see `FlowBand._refreshLiveContent`.
		"""
		if getattr(self.source, "blockAt", None) is None:
			return False
		with self.operation():
			self._rereadBlocks()
			self._redrawBlocks(why="the content changed under the band")
		return True

	def runStillHoldsTheBand(self) -> bool:
		""":return: whether the rows beside the reader are still the ones the run has.

		**Asked when the reader arrives, because that is when a run changes under them.** They
		delete a message and the focus moves to the next one; the row they deleted is still on
		the band, and nothing about the arrival says otherwise — the object they are on is in
		the run, the band is showing it, and every other block is one that was read correctly
		when it was read.

		Two questions, one either side: is the block above the arrival still what the run puts
		before it, and the block below still what it puts after. That is two steps in the run
		for a focus move, against the alternative of walking the whole run on every arrow key,
		and it catches the case that matters — the change is where the reader's hand is,
		because their hand is what made it.

		What it cannot catch is a row taken away at the far end of the band while the reader
		stands still. The live pass is what covers that, and a keystroke reads the band afresh.

		Only for a run of objects: a document's blocks are positions, and positions do not have
		neighbours to disagree with.

		:return: True when nothing has changed, and True when there is nothing to check.
		"""
		neighbour = getattr(self.source, "neighbourOf", None)
		matches = getattr(self.source, "isCurrentObject", None)
		same = getattr(self.source, "sameObject", None)
		if neighbour is None or matches is None or same is None:
			return True
		blocks = self.window.blocks
		try:
			here = next(
				(index for index, held in enumerate(blocks) if matches(held.blockId.bookmark)),
				None,
			)
		except Exception:
			log.debugWarning("Could not find the block the reader is on", exc_info=True)
			return True
		if here is None:
			# The reader is not on the band at all, so there is nothing to compare them with.
			# Placing them is `followCursor`'s work and it is about to happen.
			return True
		current = blocks[here].blockId.bookmark
		for offset, forward in ((-1, False), (1, True)):
			beside = here + offset
			if not 0 <= beside < len(blocks):
				continue
			if not same(neighbour(current, forward), blocks[beside].blockId.bookmark):
				self._note(
					f"the run changed beside the reader: what is {'after' if forward else 'before'} "
					"them is not what the band is holding",
				)
				return False
		return True

	def rereadArrival(self) -> bool:
		"""Read the block for the object the reader has just arrived on again.

		**The one moment an application answers about an object with certainty is while the
		reader is on it.** Outlook names a message row after the selection, so a row that was
		walked onto the band while a different message was selected carries that message's
		flags — and the reader arriving on an unread message was shown it as read, because that
		is what the row had said when it was walked.

		The live pass would ask again a tick later, and only for a reader who has live updates
		on. This asks now, and costs one call into the application for the one block the reader
		is about to put their hand on.

		Only for a run of objects: `ObjectFlowSource.isCurrentObject` is what says which block
		the arrival is, and it is the only source that can say.

		:return: whether anything was read again.
		"""
		matches = getattr(self.source, "isCurrentObject", None)
		fetch = getattr(self.source, "blockAt", None)
		if matches is None or fetch is None:
			return False
		try:
			wanted = [held for held in self.window.blocks if matches(held.blockId.bookmark)]
		except Exception:
			log.debugWarning("Could not find the block the reader arrived on", exc_info=True)
			return False
		if not wanted:
			return False
		with self.operation():
			changed = False
			for rendered in wanted:
				try:
					result = fetch(rendered.blockId)
				except Exception:
					log.debugWarning("Could not read the arrival again", exc_info=True)
					continue
				if result.kind is not ResultKind.BLOCK or result.block is None:
					continue
				if self._readsTheSame(self.blocks.get(rendered.blockId), result.block):
					# The ordinary case, and it must cost nothing: arrowing down a list asks
					# about each row as the reader lands on it and almost always gets the same
					# answer back. Keeping the block the flow already holds also keeps the
					# region NVDA may have queued for update — see `_keep`.
					continue
				self._keep(result.block, replace=True)
				changed = True
			if changed:
				self._redrawBlocks(why="the reader arrived on this object")
		return True

	def _readsTheSame(self, held, incoming) -> bool:
		""":return: whether a block read again says what the one already held says.

		Text alone, because that is what a re-read is for and what the reader would feel
		change. A block this cannot compare is treated as different, which costs a redraw and
		never costs the reader an answer.

		:param held: the block the flow is holding, or None if it holds none.
		:param incoming: what the source has just said.
		"""
		if held is None:
			return False
		try:
			return getattr(held.region, "rawText", None) == getattr(incoming.region, "rawText", object())
		except Exception:
			log.debugWarning("Could not compare a block with its re-reading", exc_info=True)
			return False

	def _rereadBlocks(self) -> None:
		"""Read every block the band is holding again, keeping its identity.

		Only sources that can be asked for a block by its identity answer this — a table by its
		row number, a document by the position the block was read from — and the rest are left
		alone. It is not a general re-read: the window keeps its place, the blocks keep their
		identities, and what changes is what the source now says is in them.

		A block the source will not vouch for is left as it was rather than dropped. A page
		that grew or shrank earlier in the document has moved every position after it, and the
		honest answer to "is this still the same block" is then no; showing yesterday's text
		is better than showing a neighbour's under this block's name, and the reader's next
		keystroke reads the document afresh either way.

		**The block the reader is on is made active again afterwards.** A re-read replaces a
		block's region, and a region is born inactive — every block holds a collapsed position
		and would otherwise all claim a cursor, so nothing is active until something says so.
		The controller went on knowing which block was active and the new region did not, and
		`cursorCell` asks the region. On hardware that was the cursor vanishing from a list
		about two seconds after arriving in it: nothing logged, nothing moved, and on a flat
		list there is no indent marker left to say which row is which. See `_setActive`.
		"""
		fetch = getattr(self.source, "blockAt", None)
		if fetch is None:
			return
		if getattr(self.source, "writing", False):
			# The reader is typing into this content. Everything moves on every keystroke and
			# the block they are in is the one being edited; re-reading under them would
			# fight the editor rather than follow it.
			return
		lostTheCursor = False
		# The window's blocks rather than the whole cache: they are what `_redrawBlocks` is
		# about to draw, and the cache is keyed by a bookmark that cannot be hashed or walked.
		for rendered in list(self.window.blocks):
			held = self.blocks.get(rendered.blockId)
			if held is not None and getattr(held, "isControl", False):
				# A control's block carries what it is as well as what it says — that it is a
				# control, and that a gap follows it — and a plain re-read of the position
				# would give back neither. Its text is also the one thing on the band NVDA
				# keeps fresh on its own account.
				continue
			try:
				result = fetch(rendered.blockId)
			except Exception:
				log.debugWarning(f"Could not read {rendered.blockId} again", exc_info=True)
				continue
			if result.kind is ResultKind.BLOCK and result.block is not None:
				self._keep(result.block, replace=True)
				if self.activeBlockId is not None and rendered.blockId == self.activeBlockId:
					lostTheCursor = True
		if lostTheCursor:
			# Only when the reader's own block was one of the ones replaced. Every other
			# re-read leaves the active region alone, and saying it again would re-read that
			# region for nothing on every tick of the live timer.
			self._setActive(self.activeBlockId)

	def _redrawBlocks(self, why: str) -> None:
		"""Lay every block on the band out again, under whatever the renderer says now.

		Shared by the things that change how the band is drawn without changing what it is
		reading — the indent rebasing, the column page, a re-read. All of them replace the
		whole window rather than the visible part of it, because a block in the margin drawn
		under the old layout is a block that is wrong the moment the reader scrolls to it.

		**The whole window, and it takes no list.** This used to be given the blocks to draw,
		and `window.replaceBlocks` takes what it is given as the window — so a caller that
		passed only the block it had just re-read left the window holding that one block. On a
		tree that was the reader moving from a folder to the first thing inside it: the band
		emptied, the child landed on the top row, and everything around it had to be fetched
		again. Every caller wanted every block, so there is nothing left to pass.

		:param why: what asked, for the log.
		"""
		rendered = list(self.window.blocks)
		if not rendered:
			return
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
			log.debug(f"BrlMultiline flow: the anchor did not survive {why}")
			return
		# A shallower baseline makes blocks shorter, which can leave the band short of rows.
		# A deeper one only ever makes them taller, and the window trims its own surplus.
		self._fillBothEnds()

	def _visibleDepths(self, rendered) -> list[Optional[int]]:
		"""The depths the reader can actually feel, in the order they are on the band.

		The band is the unit indent is relative to — `flowIndent` says so, and the whole
		point of a relative indent is that the shallowest thing *on the display* sits at the
		left margin. The cache is wider than the display: `_trim` keeps a window's worth
		either side, so a level 1 row that has scrolled off the top is still a block in the
		window's list. Planning from the list rather than from the rows kept that row's depth
		as the baseline after it was gone, which left a band of level 8 rows indented four
		cells from a margin standing for a level nothing on it had, and no note saying so.

		Each block once, however many rows it has: a block that wraps over three rows is one
		item at one depth, and counting it three times would say nothing different.

		:param rendered: the window's blocks, in reading order.
		:return: one depth per visible block, `None` for those with none.
		"""
		try:
			rows = self.window.visibleRows()
		except LookupError:
			return []
		depths: list[Optional[int]] = []
		seen: set[int] = set()
		for row in rows:
			if row.kind is not RowKind.CONTENT or row.blockId is None:
				continue
			try:
				index = self.window.blockIndex(row.blockId)
			except LookupError:
				continue
			if index in seen:
				continue
			seen.add(index)
			depths.append(rendered[index].depth)
		return depths

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
		:return: whether anything came back. **Not whether the shortfall was made up**: a
			fill cut short by the budget has answered the reader with what it got, and the
			pass that comes back for the rest needs to tell that from a document that will
			not answer at all. See `fill`.
		"""
		added = False
		for _ in range(MAX_FETCHES):
			if self.window.shortfall(edge) <= 0:
				return added
			if self.source.budget.refuseIfExhausted():
				# Out of budget, not out of document. Said so on the display, so that the
				# rows the reader cannot see yet do not read as the end of the page.
				self.window.setEdge(edge, EdgeState.DEFERRED)
				return added
			if not self._fetchOne(edge):
				return added
			added = True
		log.debugWarning(f"A flow stopped fetching {edge.value} after {MAX_FETCHES} blocks")
		return added

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
			self.edgeReasons[edge] = result.message or result.kind.value
			if edge is Edge.AFTER and result.edgeState is EdgeState.END:
				# Recorded here rather than at any one caller, because reaching the end is
				# something panning, filling and the tail watch all do. See
				# L{hasBeenToTheEnd}.
				self.hasBeenToTheEnd = True
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

	def _keep(self, block, replace: bool = False):
		"""Remember a source block, so its region can be reached from its identity.

		A block already held keeps the region it was first read with, and the newly built
		one is discarded. Two things depend on that. NVDA queues a region object for update
		and comes back to it later, so swapping the object underneath would leave it
		holding one this flow no longer draws. And the region is where the block's own
		reading position lives, which is what a viewer pans within.

		:param block: the block just read.
		:param replace: take the new reading even so. For a caller that read the block again
			*on purpose* and knows the old one is wrong — a table changing page reads
			different columns out of the same rows, so the region it kept is a region of the
			page just left. The regions NVDA may be holding are re-pointed on the next
			update by `flowSegment._syncRegions`, which is the same thing that happens when a
			block is replaced for any other reason.
		:return: the block to use, which may be one already held.
		"""
		existing = self.blocks.get(block.blockId)
		if existing is not None and not replace and self._sameRegionOwner(existing, block):
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

	@property
	def isShowingTheTail(self) -> bool:
		""":return: whether the band's last content row is the last row that has been read.

		What decides whether a live run is worth asking to grow. A reader who has panned
		back into the history, or who has not panned onto something that arrived while they
		were reading, is not waiting on what lies past it, and asking on their behalf would
		be a call into the application on every refresh tick to fetch something nobody is
		looking at.

		The tail of what has been read, not the end of the stream, and the difference is
		worth recording because the first version of this asked for the second. It required
		the edge to be END — but a fetch that finds something leaves the edge OPEN, so the
		first message ever to arrive turned this gate off and the pin watched nothing again.
		A band that is exactly full stops with the edge open as well, which the ordinary
		`fill` leaves behind every time. Whether the reader can feel everything that has been
		read is the question; the edge answers a different one.
		"""
		if self.window.edges[Edge.AFTER] in (EdgeState.DEFERRED, EdgeState.ERROR):
			# Out of budget, or a source that failed. Another fetch here is not the answer to
			# either: `hasMoreToFetch` and `fill` carry the deferred case on the same tick.
			return False
		# Measured in rows rather than against the last cached block, because `_trim` drops
		# what is far from the window: a reader who panned back would find the cache ending
		# just below them and look, wrongly, as though they were at the end of the run.
		return self.window.rowsBelow() == 0

	def reconsiderEnd(self, scrollIntoView: bool = False) -> bool:
		"""Ask the source again for whatever may have arrived past the tail.

		A stream that has ended stays ended: `_fetchOne` records `EdgeState.END` from the
		source's own answer, `shortfall` then reports nothing missing at that edge, and
		`panForward` refuses without consulting anybody. For a document that is right and
		cheap — the end of a page is the end of a page.

		For content that is still being written it is wrong, and the case is a pinned chat
		history. Messages arrive at the tail after the run has been read to its end, the
		walk offers them perfectly well — a held tail object's `brlMultilineFlowNext` returns
		the new message — but nothing ever asks again, so a pinned monitor stopped at
		whatever was there when it was pinned and could not be panned to anything newer.

		Self-correcting rather than optimistic: the edge is opened, one fetch is made, and if
		the stream really has ended `_fetchOne` records it as ended again. So a document that
		is not growing costs one refused fetch and returns to exactly the state it was in.

		Only the far end. Content arriving *before* the start is a different thing — a
		virtualized list materialising older rows as the reader travels — and re-asking there
		on a timer would churn a cache that `_trim` is already bounding. Panning back reaches
		it, because the edge before the start is only END once the walk has genuinely refused.

		Asked at an open edge as well as an ended one. A band that is exactly full stops
		fetching with its edge still open — `fill` asks only for the rows it is short of —
		and a run that grows into that band would otherwise sit unasked behind a gate that
		was watching for the wrong thing.

		:param scrollIntoView: bring what arrives onto the display, moving the window on. The
			caller's setting to make — see `bmConfig.shouldScrollToNewContent` — and it
			applies only where the reader was at the tail of something whose end has been
			reached before. A page that is simply longer than the band has more below it from
			the moment it is pinned, and following *that* would walk the display through the
			document a block at a time on a timer. See L{hasBeenToTheEnd}.
		:return: whether anything new was found.
		"""
		if self.window.edges[Edge.AFTER] not in (EdgeState.END, EdgeState.OPEN):
			# DEFERRED or ERROR. The budget and the failure paths own those.
			return False
		# Asked before the fetch, because fetching is what stops them being true: a message
		# added below a full band puts a row under the reader that they have not seen, and
		# the edge that said the stream had ended is about to be opened.
		wasAtTheTail = self.isShowingTheTail
		wasEnded = self.hasBeenToTheEnd
		with self.operation():
			self.window.setEdge(Edge.AFTER, EdgeState.OPEN)
			if not self._fetchOne(Edge.AFTER):
				return False
			# More than one may have arrived between two ticks, and the rows below the reader
			# may have been blank and waiting for them.
			self._fillBothEnds()
			if scrollIntoView and wasAtTheTail and wasEnded:
				self._showWhatArrived()
		return True

	def lookPastTheEnd(self) -> bool:
		""":return: whether the stream ends after the last block on the band, having asked.

		**A band that filled exactly has never asked.** Filling stops when the rows run out,
		so four messages on a four row band leave the edge open with nothing having been told
		that the run ends there — and `hasBeenToTheEnd`, which is what tells content that is
		*growing* from content that is merely longer than the band, stays false. A review found
		what that costs: the fifth message arrived below the display, nothing brought it on,
		and the tail watch then went quiet because a row it had not shown sat under the reader.

		One call, made when a pin is built, and what it fetches is thrown away. This is a
		question about the edge rather than a fetch for the window: a block that comes back
		says the run goes on, which is the answer, and the window will read it again when it
		has somewhere to put it.
		"""
		blocks = self.window.blocks
		if self.hasBeenToTheEnd:
			return True
		if not blocks:
			return False
		if self.window.edges[Edge.AFTER] is EdgeState.END:
			# Something already reached it — a run shorter than the band, most often.
			self.hasBeenToTheEnd = True
			return True
		if self.window.edges[Edge.AFTER] is not EdgeState.OPEN:
			# DEFERRED or ERROR: the budget and the failure paths own those, and a probe now
			# would ask the question they are already answering.
			return False
		# Inside an operation, because the source starts its budget on the first call and
		# something has to finish it. A probe that started a budget and left it running made
		# every later fetch part of one endless operation: once its allowance was spent, the
		# next item to arrive was refused and the edge went to DEFERRED with nothing to reset
		# it — a pin that stopped following its list some minutes after it was made.
		with self.operation():
			try:
				result = self.source.blockAfter(blocks[-1].blockId)
			except Exception:
				log.debugWarning("Could not look past the end of the stream", exc_info=True)
				return False
			if result.kind is not ResultKind.BLOCK and result.edgeState is EdgeState.END:
				self.window.setEdge(Edge.AFTER, EdgeState.END)
				self.edgeReasons[Edge.AFTER] = result.message or result.kind.value
				self.hasBeenToTheEnd = True
		return self.hasBeenToTheEnd

	def _showWhatArrived(self) -> None:
		"""Move the window on so that what has just arrived is on the display.

		By the smallest movement that shows it, which is what `ensureVisible` is: the newest
		row becomes the bottom row and the rest move up one, rather than the display jumping
		a page the way panning does. A band that had blank rows does not move at all, because
		what arrived is already visible in them.

		Its *first* row, not its last. A message longer than the band would otherwise be
		shown from its end, which is the one part of it the reader has no way to make sense
		of. Landing on its first row leaves the rest below, so the tail watch goes quiet —
		`isShowingTheTail` is false while anything read is unseen — until the reader has
		panned through what arrived. A long arrival therefore waits for them, and a chat's
		one line messages, which is what this is for, do not.
		"""
		blocks = self.window.blocks
		if not blocks:
			return
		try:
			self.window.ensureVisible(blocks[-1].blockId, rowIndex=0, forward=True)
		except LookupError:
			log.debugWarning("Could not show what arrived at the end of a flow", exc_info=True)

	def _pan(self, forward: bool) -> bool:
		"""Pan, answering `ContentNeeded` by fetching and trying again.

		Recorded in the move history like everything else that moves the band. It was not,
		and the omission cost a diagnosis: a reader reported panning that "falls back", the
		history showed six arrivals and no pans, and there was no way to tell whether the
		pans had never happened or had happened and been undone. A history that records only
		half of what moves the band cannot answer the question it exists for.

		:param forward: the direction to pan.
		:return: whether the window moved.
		"""
		with self.operation():
			moved = self._panWithin(forward)
		if not moved and forward and self.reconsiderEnd():
			# The stream had ended when it was last asked, and has since grown. Panning is
			# the reader saying they want what is past the end of what they can feel, so it
			# is the right moment to ask again, and it costs nothing until they do.
			with self.operation():
				moved = self._panWithin(forward)
		if moved:
			# After the pan, so that the caret this remembers is the one the pan left behind.
			self._pannedAt = self._caretMark()
			self._pannedCaret = self._caretsAfterAPan()
			# The window the reader had is the one they have just panned to, so the claim a
			# writing re-read would go back to is out of date. Kept, it drags the band back
			# to a window the reader left deliberately, the moment they type.
			self._writingTop = None
		entry = getattr(getattr(self.window.anchor, "entry", None), "value", "?")
		self._note(
			f"the reader panning: {'forward' if forward else 'back'}, "
			+ (f"moved, now anchored at the {entry}" if moved else "refused, nothing to pan to"),
		)
		return moved

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
		self.syncToCursor(forward=forward, why="a line command")
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
			return True if self._placeAfresh(atObject) else None
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
		moved = (
			self.groundAt(blockId)
			if ground
			else self.syncToCursor(forward=forward, why="the reader arriving")
		)
		return moved

	def _placeAfresh(self, atObject) -> bool:
		"""Put the band around the reader, after reaching for them failed.

		**With a fresh allowance where the reaching spent the old one.** Reaching and placing
		are two answers to one keypress and only the second is any use to the reader: on a
		slow application — Outlook's grouped inbox, where one step into the list took twice
		what a whole operation is given — the walk towards where they moved could spend the
		lot and arrive nowhere, and the placing that followed it then had nothing left to fill
		the band with. The reader who arrowed up one row got the row they were on and blank
		rows under it, and the band only came back when they left the folder and returned,
		which builds a controller with a budget of its own. That is the bug, and it is the
		budget rather than the fallback: the fallback was there and could not afford to run.

		Only here, and only after a refusal. An arrival costs two allowances at the very
		worst, never three, and an arrival that reached the reader the ordinary way costs one
		as it always did. See `FetchBudget.renew`.

		:param atObject: what the reader arrived at, if the document can place it.
		:return: whether anything is on the display.
		"""
		if self.source.budget.stopped:
			# Refused rather than answered. A reach that ended because the document ends
			# there has spent nothing worth giving back.
			self.source.budget.renew()
			self._note("the reach ran out of time, so placing the band afresh was given its own")
		return self._enterAtCursor(atObject=atObject)

	def _activeLength(self) -> Optional[int]:
		""":return: how many characters the block under the caret holds, or None if unreadable.

		The signal that an edit has moved everything after it. A block's position is an
		offset, so the positions of the blocks below shift exactly when this block's length
		changes — a rewrite of the same length leaves them all where they were, and a
		character added or taken away moves every one of them.

		Length rather than the text itself, because that is the invariant: comparing the text
		would also rebuild for a same-length rewrite, which has moved nothing and cost the
		reader their cheap path for no reason.

		This sees the caret's own row and no other. An application rewriting a *different*
		line while the caret sits still would shift offsets without changing this, and is not
		something a caret update can be asked about — it is what the band's ordinary re-read
		and a pin's refresh tick are for.
		"""
		region = self.activeRegion()
		if region is None:
			return None
		text = getattr(region, "rawText", None)
		return None if text is None else len(text)

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

		**Unless the reader panned and the caret has not moved.** Then the band stays where
		they put it and only the row they are standing on is read again. See
		`_caretIsWhereThePanLeftIt` for what asking this too late cost on hardware.

		:param ground: put the caret's block on the top row instead.
		:return: whether anything is on the display, in the shape `_arrive` answers with.
		"""
		if not ground and self._caretIsWhereThePanLeftIt():
			# Not nothing. Forward Delete changes the line without moving the caret off it,
			# and so does an editor rewriting it, so the caret's own block is read again — the
			# one block that can have changed under a caret that has not moved. The window is
			# not touched: the block is on the band by construction, being the one
			# `_cursorToTop` put the cursor on.
			was = self._activeLength()
			self.refreshActive()
			if was == self._activeLength():
				self._note(
					"a keystroke while writing: the caret is where the pan left it, "
					"so only its own row was read again"
				)
				return True
			# The row under the caret changed length, so every position after it has moved
			# and the rest of the band is reading at offsets that have gone. Falling through
			# is the whole re-read, which forgets those positions and puts the band back
			# under the row the reader had. It costs what a keystroke costs everywhere else,
			# and only where something actually changed: a caret settling after a pan, which
			# is what this branch was written for, still costs one block.
			#
			# The top row is the reader's own: they panned to it, which is what got us into
			# this branch at all. So it is recorded as the trustworthy top before falling
			# through, because `_stableTop` otherwise refuses a top that is the caret's own
			# block — a rule written against a *transient* merged block appearing at the top
			# while typing, which is not this. Without it the band jumped backwards by half
			# its height on the first forward Delete after a pan.
			self._note(
				"a keystroke while writing: the caret stayed put but its row changed length, "
				"so the band was read again"
			)
			return self._readAgainKeeping(self.window.topBlockId(), trusted=True)
		return self._readAgainKeeping(self._stableTop(), ground=ground)

	def _readAgainKeeping(self, topId, ground: bool = False, trusted: bool = False) -> Optional[bool]:
		"""Read the band again from the caret, and put it back under a chosen top row.

		:param topId: the block to anchor the band under again, or None to let the caret lead.
		:param ground: put the caret's block on the top row instead.
		:param trusted: whether that top may be the caret's own block. See `_restoreTop`.
		:return: whether anything is on the display, in the shape `_arrive` answers with.
		"""
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
			self._pannedCaret = ()
			return True
		if self._restoreTop(topId, trusted=trusted):
			self._writingTop = topId
			why = "a keystroke while writing, put back under the row the band had"
		else:
			# The claim is kept: a restore that failed this moment — a transient refusal, a
			# top the edit removed — may succeed on the next keystroke, and dropping it here
			# would hand the next restore whatever block the heal below leaves on the top
			# row instead of the window the reader actually had.
			self._contextAboveTheCaret()
			why = "a keystroke while writing, no old top to go back to so the caret leads"
		self.fill()
		# Which of the two branches ran, because the history could not say. It recorded
		# "nothing moved" — the verdict of the `syncToCursor` at the end — on a pass that had
		# already thrown the reader's window away and rebuilt it somewhere else, and a
		# history that names only the last of the things that moved the band cannot answer
		# the question it exists for.
		self.syncToCursor(forward=True, why=why)
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

	def _restoreTop(self, topId, trusted: bool = False) -> bool:
		"""Put the band back under the row it was showing before a writing re-read.

		:param topId: the block that was on the top row, or None for an empty band.
		:param trusted: whether the reader put the band there themselves. A top that is the
			caret's own block is otherwise refused, because a rich editor answering a return
			with a transiently merged block puts that block on the top row and a band anchored
			to it never recovers — but a reader who *panned* to their caret's row chose it,
			and refusing it throws their window away on the next forward Delete.
		:return: whether the band is anchored there again. False when reading back to it
			failed this moment — where the caller has a better answer than leaving the line
			being typed pinned to the top row.
		"""
		if topId is None or (not trusted and topId == self.activeBlockId):
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
			# What could not be afforded this time, for the pass that comes back with a fresh
			# allowance. The rows above are read last and are the first thing a slow editor
			# costs the reader; see `_owedAbove`.
			self._owedAbove = (
				wanted
				if rowsAbove < wanted and self.window.edges[Edge.BEFORE] is EdgeState.DEFERRED
				else 0
			)
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

	def _note(self, text: str) -> None:
		"""Record something that moved the band, for a report to be able to say what did.

		Kept as a short history rather than one line, because the line was misleading: the
		display's placement was read off the direction test's verdict, and on the report
		that finally located this bug the verdict was *correct* while the placement was
		wrong. Something else had moved the window first — `refreshActive`, re-rendering the
		row the reader had just arrived on — and then a second, no-op call had overwritten
		the note with a reading of a decision that changed nothing.

		A history says which call moved the band and which found there was nothing to do,
		in the order they happened, so neither can be mistaken for the other again.

		:param text: what happened, as one line.
		"""
		self._placements.append(text)
		del self._placements[:-8]

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

	def syncToCursor(self, forward: bool = True, why: str = "asked to") -> bool:
		"""Bring the active block onto the display, moving as little as possible.

		If it is already there, nothing moves at all: landing on a heading that is already
		under the reader's fingers must not jerk the display.

		:param forward: the direction the reader is travelling. `FlowWindow.ensureVisible`
			uses it only as a tiebreak: which edge a row comes on at is decided by which
			side of the window it is on, so a caller with no way of knowing may pass either.
		:param why: what is asking, for the report. Four things call this and they do not
			all know which way the reader went; when one of them moves the band unexpectedly
			the report has to be able to name it.
		:return: whether the window moved.
		"""
		if self.activeBlockId is None:
			return False
		if self._panIsTheReadersChoice():
			self._note(f"{why}: the reader panned here and has not moved, so nothing moved")
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
			moved = self._showTheWholeOfIt(self.activeBlockId) or moved
		except LookupError:
			self._note(f"{why}: the block is not in the window, so the band was entered afresh")
			return self.enterAtCursor()
		entry = getattr(getattr(self.window.anchor, "entry", None), "value", "?")
		self._note(
			f"{why}: {'forward' if forward else 'back'}, "
			+ (f"brought on at the {entry}" if moved else "already on the band, nothing moved"),
		)
		if moved:
			self.fill()
		return moved

	def _showTheWholeOfIt(self, blockId: "BlockId") -> bool:
		"""Bring the rest of the block the cursor is in onto the band, where it fits.

		**A row that wraps is one row of the table and the reader is standing on all of it.**
		Bringing a block on by the line the cursor is on is the right rule for keeping the
		cursor in view, and it is the wrong amount to show when the block has just become the
		reader's own: scrolling down onto a record two lines tall put its first line on the
		bottom row and left the second off the band, so the values in the columns that had
		wrapped were the ones the reader could not read. The same on a list of files whose
		names run to two lines.

		So the cursor's row is brought on first — that is what must be visible, and it is what
		decides which edge the block arrives at — and then the far end of the block is asked
		for as well. Where the whole block fits on the band that shows all of it; where the
		block is taller than the band it is refused, because the alternative is scrolling the
		cursor's own row off to show a part of the row nobody asked for. Panning through a
		block taller than the band is what panning is for.

		:param blockId: the block the cursor is in.
		:return: whether the window moved.
		:raises LookupError: if the block is not in the window, as `ensureVisible` does.
		"""
		rendered = self.window.blocks[self.window.blockIndex(blockId)]
		last = rendered.numRows - 1
		if last < 1 or rendered.numRows > self.window.numRows:
			return False
		if rendered.rowOffset or rendered.moreRows:
			# A chunk of a block rather than the whole of it, which is what a long edit is
			# rendered as: sixty-four rows is the renderer's working set and the reader may be
			# on row seventy-four of their own field. The far end of a chunk is not the far end
			# of anything the reader is standing on, and going to it takes their caret off the
			# band.
			return False
		if self.window.isVisible(blockId, last):
			return False
		return self.window.ensureVisible(blockId, rowIndex=last, forward=True)

	def _caretMark(self):
		""":return: where the caret is, as something two calls can compare.

		The active block and the position within it. Not the row, because the row is what the
		question is about: a caret that has not moved may be on a row the band no longer
		shows, and that is exactly the state panning leaves behind.
		"""
		if self.activeBlockId is None:
			return None
		region = self.regionFor(self.activeBlockId)
		return (self.activeBlockId, getattr(region, "brailleCursorPos", None))

	def _panIsTheReadersChoice(self) -> bool:
		"""Whether the window is where the reader panned it and the caret has not moved since.

		Panning within the block the caret is in is the reader deliberately looking at another
		part of what they are standing in — the rest of a long line, the tail of a paragraph —
		and following the cursor afterwards would take it back from them. The rule it suspends
		is right for a *caret move*: a caret at the bottom of a long paragraph must not be
		shown by the paragraph's top. It is wrong when the caret did not move at all.

		Only while the block is still on the band. Once the reader has left it there is
		nothing of theirs to protect and the ordinary rule applies again.

		:return: whether to leave the window where it is.
		"""
		if self._pannedAt is None:
			return False
		try:
			held = self._pannedAt == self._caretMark() and self.window.isVisible(self.activeBlockId)
		except LookupError:
			held = False
		if not held:
			# Forgotten rather than merely not matching. A reader who moves away and comes
			# back to the same place has not re-panned, and a rule that re-engaged on the
			# way back would leave the band stuck where it was minutes ago.
			self._pannedAt = None
		return held

	def _caretPosition(self):
		""":return: where the document's own caret is, or None if it cannot be asked.

		A position the document hands out, compared by value, and nothing more. What it is
		worth is that it comes from the document rather than from anything the flow has
		rendered — and the rendering is what a re-read is about to replace.
		"""
		where = getattr(self.source, "caretPosition", None)
		if where is None:
			return None
		try:
			return where()
		except Exception:
			log.debugWarning("Could not ask a source where its caret is", exc_info=True)
			return None

	def _caretIsWhereThePanLeftIt(self) -> bool:
		"""Whether the reader panned the band somewhere and the caret has not moved since.

		Not "has anything been typed", which was the first claim made here and is not the
		same question: forward Delete takes out the character *after* the caret and leaves it
		exactly where it was, and an editor rewriting the line under it moves nothing at all.
		What this answers is only that the reader has not gone anywhere — which is what makes
		the window theirs. What is on the row they are standing on is a separate question,
		and the caller reads that row again rather than trusting it.

		The question a written-in document's re-read has to ask before it does anything at
		all, because a pan cannot survive that re-read deciding where the band goes.

		Finding the caret is not the problem: panning a live document takes the cursor with
		it — see `_cursorToTop` — so entering at the caret does land on the row the reader
		panned to. What loses the pan is the anchoring afterwards, and both of its branches
		lose it. `_stableTop` can never accept the row the band is actually showing, because
		`_cursorToTop` has just made that block the caret's own and the top row is only
		believed when it is not; so the restore target is always a top row from before the
		pan. Reach that row and the band goes back to the window the pan left — the pan did
		nothing. Fail to reach it and `_contextAboveTheCaret` runs instead, which puts the
		caret half a band down from the top: the band lands half a display behind where the
		pan put it.

		On hardware this was a markdown file open in VSCode, a plain editable text with no
		browse mode behind it and so written in for as long as the reader is in it, where
		every display update brings a settle pass a quarter of a second later. The reader
		got both branches and named both: it "was panning by one line instead of whole
		display" — a pan of eight rows with four given back — "and at the point of this log
		it would not pan forward at all" — the stale top row within reach. They also said
		"pan back seems better", and it was: half a display of drift *backwards* adds to a
		backward pan and subtracts from a forward one.

		Answered from the document's own caret rather than from anything the flow rendered,
		which is what lets it be asked before the re-read rather than after it. Typing moves
		the caret, so a caret still where the pan left it is a caret nobody has touched — and
		then there is nothing to re-read at all, which is also the whole cost of the pass
		saved for as long as the reader is reading rather than writing.

		:return: whether to leave the band exactly where the reader panned it.
		"""
		if not self._pannedCaret:
			return False
		here = self._caretPosition()
		if here not in self._pannedCaret:
			# Forgotten rather than merely not matching, as `_panIsTheReadersChoice` forgets:
			# a reader who moves away and comes back has not re-panned.
			self._pannedCaret = ()
			return False
		# The answers this was waiting to move *past* stop counting once it has. They are
		# ordered oldest first, so seeing the settled one drops the stale read-back with it,
		# and a reader who then goes back to where they panned from — control+home, a routing
		# key onto the line they came from — is a reader who moved.
		self._pannedCaret = self._pannedCaret[self._pannedCaret.index(here) :]
		return True

	def _caretsAfterAPan(self) -> tuple:
		"""Where the caret may be found after a pan without the reader having typed.

		Two answers, because moving the caret is a request to the application and reading it
		back is a question to the application, and between one keystroke and the next those
		do not have to agree. Panning tells the editor to put its caret at the top of what
		is now shown; a read taken in the same breath can still be the caret the pan moved
		*from*, and the settled answer arrives with the caret event a moment later.

		Both count as "nothing was typed", and the pair is what made the difference between
		a pan that stuck and one that did not. Where the new top row continued the block the
		caret was already in, the cursor was asked to go where it already was, the read-back
		agreed, and the pan held. Where it began a new block the caret really moved, the
		read-back was stale, and the next caret event re-read the band and dragged it half a
		display back — a reader panning through a file got "the full display, to moving just
		a couple lines" depending on nothing they could see.

		A caret move lands on neither: it goes away from where the caret was *and* away from
		where the pan put it. An edit that moves no caret lands on one of them, and is caught
		by the caller reading that row again rather than by this.

		:return: the marks that mean the reader has not typed, newest first, possibly empty.
		"""
		marks = []
		for mark in (self._caretPosition(), self._cursorMark(self.window.topBlockId())):
			if mark is not None and mark not in marks:
				marks.append(mark)
		return tuple(marks)

	def _cursorMark(self, blockId):
		""":return: the mark for where this flow put the cursor in a block, or None.

		The position the flow *asked* for, taken from the block rather than from the
		document, which is the half of the answer a read of the document cannot give until
		the application has caught up.
		"""
		if blockId is None or not (self.live and self.movesCursor):
			return None
		return getattr(self.regionFor(blockId), "cursorMark", None)

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
		#
		# Forward is this caller's case and not always the reader's: this runs on arrival
		# too, before anything has worked out which way they went. It is safe because
		# `FlowWindow.ensureVisible` decides the edge from where the row is; it was not
		# before, and a row the reader had arrowed up to arrived at the bottom of the band.
		self.syncToCursor(forward=True, why="a re-render of the cursor's block")
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

	def setPinned(self, block: Optional["SourceBlock"]) -> None:
		"""Hold one block above the window, on the band's top row.

		:param block: the block to pin, or None to stop pinning one.
		"""
		self.pinnedBlock = block
		self.refreshPinned()

	def refreshPinned(self) -> None:
		"""Draw the pinned block again from what its region says now.

		Done on every `cells`, which is once per write to the display and is where it has to
		be: the row itself does not change, but whether the cursor is in it does, and a
		region works its cursor out when it is asked to update rather than when it was made.
		Nothing here reads the document — the cells were read when the block was built, and
		this re-translates them.
		"""
		if self.pinnedBlock is None:
			self.pinned = None
			return
		update = getattr(self.pinnedBlock.region, "update", None)
		if update is not None:
			try:
				update()
			except Exception:
				log.debugWarning("Could not update the pinned row", exc_info=True)
		try:
			self.pinned = self.renderer.renderPinned(self.pinnedBlock)
		except Exception:
			log.debugWarning("Could not draw the pinned row", exc_info=True)
			self.pinned = None

	@property
	def pinnedCells(self) -> int:
		""":return: how many cells of the band the pinned row takes, zero if there is none."""
		return self.renderer.numCols if self.pinned is not None else 0

	def _pinnedRow(self) -> list[int]:
		""":return: the pinned row's cells, padded to the band's width."""
		if self.pinned is None or not self.pinned.rows:
			return []
		numCols = self.renderer.numCols
		row = list(self.pinned.rows[0])[:numCols]
		return row + [BLANK_CELL] * (numCols - len(row))

	def cells(self) -> list[int]:
		"""The band's cells, row major, padded to the full width.

		The padding is the point: it is what stops the block after a short one sharing its
		row, which NVDA's own buffer cannot avoid.

		The pinned row goes on top and is not part of the window, so everything the window
		says about rows — the focus mark, the cursor, routing — is worked out first and then
		moved down by a row. See `pinnedBlock`.
		"""
		self.refreshPinned()
		try:
			cells = assembleCells(self.window, self.renderer.numCols)
		except LookupError:
			cells = [0] * (self.window.numRows * self.renderer.numCols)
		else:
			self._markLineFocus(cells)
		return self._pinnedRow() + cells

	def _markLineFocus(self, cells: list[int]) -> None:
		"""Mark the left of the rows the focus is on.

		Drawn here rather than rendered into the block, for the same reason the cursor is:
		the focus moves without the rows changing. Baking the mark in would mean re-rendering
		the block the reader left as well as the one they arrived at, and the one they left
		is often no longer in the window to re-render.

		Every row of the focused block is marked, not only its first. A wrapped item is two
		rows of the same item, and a hand running down the left margin should feel the whole
		of what has the focus rather than its first row and then a gap.

		Whether there is room is asked of the *item*, once, and not of each row. A wrapped row
		carries more indent than its item does — that is what says it is a continuation rather
		than a child — so asking per row marked the hanging rows of an item that sits at the
		margin and left its first row bare. On hardware that was a message in Outlook's list:
		seven rows of one item, six of them marked, and the marked ones were the six that were
		not the item's own first row.

		Rows too near the left margin to carry it are left alone. See `flowIndent.focusMark`.

		:param cells: the assembled band, modified in place.
		"""
		if not self.lineFocus or self.activeBlockId is None:
			return
		try:
			rows = self.window.visibleRows()
			block = self.window.blocks[self.window.blockIndex(self.activeBlockId)]
		except LookupError:
			return
		mark = flowIndent.focusMark(self.renderer.indentPlan.cellsFor(block.depth))
		if not mark:
			return
		numCols = self.renderer.numCols
		for index, row in enumerate(rows):
			if row.kind is not RowKind.CONTENT or row.blockId != self.activeBlockId:
				continue
			if self._indentOfRow(row) < len(mark):
				# Belt and braces rather than policy: the plan the mark was measured by is the
				# one the block was rendered under, so this cannot fire — and if it ever did,
				# the mark would be sitting on a cell of the reader's text.
				continue
			for offset, cell in enumerate(mark):
				cells[index * numCols + offset] = cell

	def _indentOfRow(self, row) -> int:
		"""How many cells of indent one row of the window was drawn with.

		Read off the rendering rather than worked out again from the plan: a rendered row
		says where each of its cells came from, and the indent is exactly the run at the
		front that came from nowhere. Asking the plan a second time would be a second answer
		to keep in step with the first.

		:param row: a content row of the window.
		:return: the number of indent cells, 0 if the row cannot be read.
		"""
		try:
			block = self.window.blocks[self.window.blockIndex(row.blockId)]
		except LookupError:
			return 0
		if not block.holdsRow(row.rowIndex):
			return 0
		indent = 0
		for where in block.positions[row.rowIndex - block.rowOffset]:
			if where != NO_POSITION:
				break
			indent += 1
		return indent

	def cursorCell(self) -> Optional[int]:
		"""Where the cursor is within the band, as a flat row major position.

		:return: the position, or None if the active block is not on the display or has no
			cursor in it.
		"""
		found = self._pinnedCursor()
		if found is not None:
			return found
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
				return position + self.pinnedCells
		return None

	def _pinnedCursor(self) -> Optional[int]:
		""":return: where the cursor is within the pinned row, or None if it is not in it.

		Asked of the pinned block's own region rather than of `activeBlockId`, because a
		pinned row is not in the window and so is never the active block. A table row says it
		holds the cursor when the caret is in one of its cells and says nothing on every other
		row, which is exactly the question here.
		"""
		if self.pinned is None or self.pinnedBlock is None or not self.pinned.positions:
			return None
		at = getattr(self.pinnedBlock.region, "brailleCursorPos", None)
		if at is None:
			return None
		for column, where in enumerate(self.pinned.positions[0]):
			if where != NO_POSITION and where == at:
				return column
		return None

	def _routePinned(self, position: int) -> bool:
		"""Act on a routing key press within the pinned row.

		:param position: which cell of it.
		:return: whether the press reached content.
		"""
		if self.pinned is None or self.pinnedBlock is None or not self.pinned.positions:
			return False
		marks = self.pinned.positions[0]
		if not 0 <= position < len(marks) or marks[position] == NO_POSITION:
			return False
		route = getattr(self.pinnedBlock.region, "routeTo", None)
		if route is None:
			return False
		try:
			route(marks[position])
		except Exception:
			log.debugWarning("Could not route into the pinned row", exc_info=True)
			return False
		return True

	def routeTo(self, position: int) -> bool:
		"""Act on a routing key press within the band.

		:param position: a flat row major position within the band.
		:return: whether the press reached content. Padding, gaps and blank rows reach
			nothing, and must do nothing: NVDA's habit of mapping trailing blank columns
			back to the last content cell would otherwise activate the end of a short field
			when the reader pressed a key in the space after it.
		"""
		if position < self.pinnedCells:
			return self._routePinned(position)
		source = cellSource(self.window, self.renderer.numCols, position - self.pinnedCells)
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
		""":return: what the direction test last concluded. See `_lastDirection`."""
		return self._lastDirection

	@property
	def placements(self) -> list[str]:
		""":return: what has moved the band lately, oldest first. See `_note`."""
		return list(self._placements)

	def describeRows(self) -> list[str]:
		"""What each row of the band holds, in words rather than cells.

		For the dry run command and for the log: it shows the packing and the window
		without a display attached, which is where the design is easiest to get wrong.

		:return: one line per row of the band.
		"""
		numCols = self.renderer.numCols
		lines: list[str] = []
		if self.pinned is not None:
			held = rowText(self.pinnedBlock.region, self.pinned, 0) if self.pinnedBlock else ""
			lines.append(f"pinned: {held!r}")
		elif self.pinnedBlock is not None:
			# Said rather than left out, and only in the case worth a line: a header row this
			# flow is holding and did not draw. A report that shows nothing where the header
			# should be reads the same whether the band never had one or lost it, and those
			# are different faults. A flow with no header at all says nothing here, as before.
			lines.append("pinned: held but not drawn")
		try:
			visible = self.window.visibleRows()
		except LookupError:
			return lines + ["(no anchor)"]
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
			if block is not None and block.isBlank:
				# The one row a reader cannot tell from a fault: an empty line the document gave
				# us reads exactly like a row we could not fill, and a report of "a blank between
				# every cell" cost a second log for want of this word. The count says how many
				# lines the row stands for, since a collapsed run is one row for many.
				kind += " blank" if block.collapsed < 2 else f" blank x{block.collapsed}"
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
