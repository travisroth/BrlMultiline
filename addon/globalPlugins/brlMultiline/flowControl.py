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
import dataclasses
import enum
from typing import TYPE_CHECKING, Any

from logHandler import log

from . import brailleSubstitutions, flowForms, flowIndent, glyphFlow, glyphs
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

A backstop under the source's own budget, for a source that keeps answering with blocks that
add no rows.
"""


def rowText(region, rendered, rowIndex: int) -> str:
	"""What one row of a rendered block reads, for the log and the dry run.

	The row's cells are mapped back through the region's `brailleToRawPos`, which accounts
	for contraction, so a block spanning several rows is reported row by row.

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
		# A region whose positions are not offsets into one run of text, such as a table row
		# whose positions also carry the column, answers for itself.
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


class Arrival(enum.Enum):
	"""What `FlowController._arrive` found when it brought the window to the reader."""

	CHANGED = "changed"
	"""The window moved, or the band was read again."""

	UNCHANGED = "unchanged"
	"""The reader was already on the display, so nothing moved."""

	NOTHING = "nothing"
	"""There was nothing to read, so nothing is on the display."""


class FlowController(PanelOwner):
	"""Drives one band of flowing content."""

	def __init__(
		self,
		source: "DocumentFlowSource",
		renderer: "FlowRenderer",
		numRows: int,
		live: bool = False,
		movesCursor: bool | None = None,
		indentStyle: str = flowIndent.DEFAULT_STYLE,
		lineFocus: bool = True,
		caretByDisplay: bool = False,
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
		:param caretByDisplay: whether a caret that moves off the band brings on a whole
			display beyond it rather than the one row. See `syncToCursor`.
		"""
		self.source = source
		self.renderer = renderer
		self.window = FlowWindow(numRows)
		self.live = live
		self.movesCursor = True if movesCursor is None else movesCursor
		self.indentStyle = indentStyle
		"""How one level of depth is drawn. See `reconfigure` for a change of setting."""

		self.lineFocus = lineFocus
		"""Whether the focused row is marked at the left. See `_markLineFocus`."""

		self.caretByDisplay = caretByDisplay
		"""Whether following the caret off the band fills the display beyond it. Set again by
		the band when it keeps this controller across a profile switch."""

		self._presentation = self._presentationFor(indentStyle, lineFocus)
		"""The settings the band was last drawn under. See `reconfigure`."""

		self._focusFitted: tuple | None = None
		"""The driver the focus square was last fitted for, and the square. See `_focusGlyph`."""

		self._following = False
		"""Guards against following a cursor move that this controller made itself."""

		self.activeBlockId: "BlockId | None" = None
		"""The block the cursor is in, and the only one that may show a cursor."""

		self._activeIsFreshlyRead = False
		"""Whether the active block's region has been read since it became the active one, so
		`refreshActive` can skip reading the same line again."""

		self._rereadFrom = 0
		"""Which of the window's blocks the next live re-read carries on from, so a re-read cut
		short by the budget does not start at the top row every time. See `_rereadBlocks`."""

		self.lastResult = None
		"""The source's last answer, so a caller can say why nothing appeared."""

		self._lastDirection = "not yet asked"
		"""What the direction test last concluded, for the diagnostics. See `_isForward`."""

		self.hasBeenToTheEnd = False
		"""Whether the source has ever said there is no more after the last block read.

		Tells content that is growing, such as a chat, from content that is merely longer than
		the band: only content whose end has been reached may scroll on its own. Never unset;
		the display still only moves on its own at the tail. See `reconsiderEnd`.
		"""

		self.edgeReasons: dict = {}
		"""Why each end of the stream was declared, by edge, so the report can name which of
		the several ways a walk can conclude it has finished was the one taken."""

		self._placements: list[str] = []
		"""What has actually moved the band, most recent last. See `_note`."""

		self.onChanged = None
		"""Called when something moved the flow from underneath, so the band can redraw.

		Set by the segment showing this flow. NVDA's commands reach the active region
		directly, so a move can start below the controller rather than above it."""

		self.pinnedBlock: "SourceBlock | None" = None
		"""A block held above the window and drawn on the band's top row, or None.

		A table's header row, kept outside the window so it shows wherever in the table the
		reader starts. The window is one row shorter when there is one, decided when the
		controller is built.
		"""

		self.pinned: "RenderedBlock | None" = None
		"""The pinned block as it is drawn. Redrawn on each `cells`; see `refreshPinned`."""

		self.blocks = ByIdentity()
		"""The source block behind each rendering, by identity, for its region.

		Not a dictionary: a `BlockId` holds a bookmark, and a bookmark compares but does not
		hash. See `flow.ByIdentity`."""

		self._pannedAt = None
		"""Where the caret was when the reader last panned, or None if they have not.

		Stops a pan being undone by the cursor move the pan itself caused, which on a one row
		band would snap straight back. See `_panIsTheReadersChoice`.
		"""

		self._writingTop = None
		"""The last top block the reader was shown that was not the caret's own. See `_stableTop`."""

		self._acrossCaret = None
		"""Where the caret was when the reader last panned across unwrapped lines, or None.
		The across counterpart of `_pannedAt`. See `followCaretAcross`."""

		self._pannedCaret: tuple = ()
		"""Where the caret counts as being if nothing has been typed since the reader panned.

		Answers "has anything been typed", where `_pannedAt` answers "has the band moved".
		Several positions, because a caret moved by panning has two right answers while the
		application catches up. See `_caretsAfterAPan`."""

		self._owedAbove = 0
		"""Rows of context above the caret that a writing re-read could not afford.

		Those rows are read last, so they are the ones the budget cuts, and nothing else asks
		for them again because the window has no shortfall at its top. They are paid on the
		next pass. See `_contextAboveTheCaret` and `_payWhatIsOwedAbove`."""

		self.rereadWhileWriting = False
		"""Whether the last cursor move re-read the band as an edit being typed into.

		The segment consumes this to schedule a settle pass, since a rich editor can answer
		transiently wrong at the moment of a keystroke. See docs/design/spatial-reading-plan.md,
		Following the cursor."""

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

	def _enterAtCursor(self, contextRows: int = 0, atObject=None, fill: bool = True) -> bool:
		"""
		:param fill: whether to fill the band before returning. False for a caller that will
			move the window first, so rows are not read only to be pushed off. See
			`_readAgainKeeping`.
		:return: whether anything is now on the display. See `enterAtCursor`.
		"""
		# Whatever was owed was owed by the band being replaced here.
		self._owedAbove = 0
		result = self.source.blockAtCursor(atObject)
		self.lastResult = result
		if result.kind is not ResultKind.BLOCK or result.block is None:
			log.debugWarning(f"A flow could not enter at the cursor: {result.kind} {result.message}")
			return False
		return self._enterAtBlock(result, contextRows, fill)

	def topPosition(self):
		""":return: where the text on the window's top row begins, or None.

		A pin's reading position, which is where a search through the document starts. Read off
		the top row's first cell through the region it came from, so a block wrapped over several
		rows and panned part way through gives where the reader is, not where the block starts.
		Where that cannot be read, the start of the block at the top. None for a source that has
		no positions, such as a run of objects.
		"""
		positionOf = getattr(self.source, "positionOf", None)
		if positionOf is None:
			return None
		for column in range(self.renderer.numCols):
			found = self.regionAt(self.pinnedCells + column)
			if found is None:
				continue
			region, at = found
			getInfo = getattr(region, "getTextInfoForBraillePos", None)
			if getInfo is None:
				break
			try:
				return getInfo(at)
			except Exception:
				log.debugWarning("Could not read the position at the top of the window", exc_info=True)
				break
		blockId = getattr(self.window.anchor, "blockId", None)
		return positionOf(blockId) if blockId is not None else None

	def windowMark(self):
		""":return: something that changes whenever the window moves, to tell a pin's own position
		apart from one the reader has panned away from. See `pinQuickNav`."""
		anchor = self.window.anchor
		return None if anchor is None else (anchor.blockId, anchor.rowIndex)

	def enterAtPosition(self, info) -> bool:
		"""Place the window afresh with the block holding a position at its top.

		What a quick navigation key in a pin does with what it found: the place is set down at
		the top and the document runs on from it, as `groundAt` does for the band, without the
		cursor being involved. See `pinQuickNav`.

		:param info: a position in this flow's document.
		:return: whether anything is now on the display. False for a source that cannot read a
			block at a position.
		"""
		blockAtPosition = getattr(self.source, "blockAtPosition", None)
		if blockAtPosition is None:
			return False
		with self.operation():
			self._owedAbove = 0
			result = blockAtPosition(info)
			self.lastResult = result
			if result.kind is not ResultKind.BLOCK or result.block is None:
				log.debugWarning(f"A flow could not enter at a position: {result.kind} {result.message}")
				return False
			return self._enterAtBlock(result, 0, True)

	def _enterAtBlock(self, result, contextRows: int, fill: bool) -> bool:
		"""Start the window afresh from a block just read. See `_enterAtCursor`."""
		self.window.blocks.clear()
		self.blocks.clear()
		self._rereadFrom = 0
		self.window.setEdge(Edge.BEFORE, EdgeState.OPEN)
		self.window.setEdge(Edge.AFTER, EdgeState.OPEN)
		block = self._keep(result.block)
		# Activated before the first rendering, because whether a block holds the cursor
		# decides how it is translated (NVDA expands the word at the cursor).
		self._setActive(block.blockId, freshlyRead=block.region is result.block.region)
		self.window.appendBlock(self.renderer.render(block))
		self.window.enterAt(block.blockId)
		# A caret beyond the rendering work set belongs to a later chunk, which
		# `_renderActiveChunk` locates.
		self.refreshActive(reread=not self._activeIsFreshlyRead)
		if contextRows > 0:
			self._reachBack(contextRows)
		elif result.block.isControl:
			# A control's prompt goes above it.
			contextRows = self._labelContext(result.block.blockId)
		if contextRows > 0:
			self.window.enterAt(result.block.blockId, contextRows=contextRows)
		if fill:
			self.fill()
		return True

	def _labelContext(self, blockId: "BlockId") -> int:
		"""How far above a control to start the window, so that its prompt is on the display.

		One block is read backwards, since the prompt is what immediately precedes the control,
		and the offset is taken from that block's own height.

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
			# Nothing above it, or what is above could not be read.
			return 0
		control = self.window.blocks[index]
		previous = self.window.blocks[index - 1]
		gapRows = (1 if previous.gapAfter else 0) + (1 if control.gapBefore else 0)
		return flowForms.contextRowsFor(previous.numRows, gapRows, self.window.numRows)

	def _reachBack(self, rows: int) -> None:
		"""Fetch rows above the window, which the window itself would never ask for.

		`_fill` makes up a shortfall, and a window anchored at its own top row has none, so
		this asks by row count instead.

		:param rows: how many rows are wanted above the top of the window.
		"""
		for _ in range(MAX_FETCHES):
			if self.window.rowsAbove() >= rows:
				return
			if self.source.budget.refuseIfExhausted():
				self._deferEdge(Edge.BEFORE)
				return
			if not self._fetchOne(Edge.BEFORE):
				return

	# Filling.

	@property
	def hasMoreToFetch(self) -> bool:
		""":return: whether an end of the band is short of content the source still has.

		Deferred means the budget ran out, as opposed to the document ending.
		"""
		return any(state is EdgeState.DEFERRED for state in self.window.edges.values())

	def fill(self) -> bool:
		"""Fetch whatever the window is short of, at both ends, and drop what is far away.

		:return: whether anything came back, not whether the band is full. A caller that comes
			back for the rest, such as `FlowBand._fillMore`, stops when a pass adds nothing.
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

		Asked on every fill, so the band's continuation pass pays the debt a fetch or two at a
		time, on a fresh budget each pass, until the rows are back or the document starts.

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
			# Still refused. The band stays where it is until the debt is paid, so it moves
			# once rather than a row per pass.
			return rowsAbove > before
		# Paid, or the document starts here. What was reached goes above the caret.
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

		Run at the end of every operation, since every operation can change what is on the
		band. One pass, never a loop: laying the band out again can change which blocks are on
		it, and repeating would move the display while the reader reads it.
		`flowIndent.shouldRebase` keeps this to one comparison while the plan still fits.
		"""
		rendered = list(self.window.blocks)
		if not rendered:
			return
		depths = self._visibleDepths(rendered)
		if not depths:
			# Nothing on the band to plan for, so the plan in force stands.
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

	def drawGlyphsOn(self, target) -> None:
		"""Say which display the band is on, and lay it out again if that is news.

		A controller is entered before any band holds it, so the blocks already on the band were
		laid out with no display to draw glyphs for. A change of display, or of the glyph
		setting, lays them all out again. Inside an operation, because a shorter row can make
		the band fetch, and a fetch needs an operation's budget.

		:param target: the display the band is drawn on, as `glyphFlow.targetForRows` gives it.
		"""
		from . import glyphFlow

		wanted = target is not None and glyphFlow.enabled()
		if target == self.renderer.glyphTarget and wanted == self.renderer.glyphsWanted:
			return
		why = (
			"a different display to draw glyphs on"
			if target != self.renderer.glyphTarget
			else "glyphs turned on or off"
		)
		self.renderer.glyphTarget = target
		self.renderer.glyphsWanted = wanted
		with self.operation():
			self._redrawBlocks(why=why)

	def useColumnPage(self, plan) -> bool:
		"""Show a page of columns, and read what the page needs.

		Three steps, together and in this order: the source is told which columns to read, so
		cells on other pages are not searched for; the rows are read again, since they hold the
		old page's cells; and the pinned header, which is outside the window, is rebuilt.

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
					# The new page's columns may have no names. The old header row stands,
					# since clearing it would leave nothing to ask for it again.
					log.debugWarning("No header row for this page of columns, so the last one stands")
		return changed

	def setColumnPlan(self, plan, reread: bool = False) -> bool:
		"""Draw the band's table again with a different column layout.

		The plan holds every column and which page each is on, so this is how a different page
		of a wide table reaches the display.

		:param plan: the layout to draw with. See `flowTable.ColumnPlan`.
		:param reread: read the rows again before drawing them, for when the source has been
			told to read different columns.
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

	def _presentationFor(self, indentStyle: str, lineFocus: bool) -> tuple:
		""":return: the settings that change how the band is drawn and not what it reads."""
		setting = self.renderer._setting
		return (
			indentStyle,
			lineFocus,
			setting("translationTable"),
			# The reader's braille substitutions, which change the cells under the same table name.
			brailleSubstitutions.generation(),
			setting("textWrap"),
			setting("expandAtCursor"),
		)

	def reconfigure(self, indentStyle: str, lineFocus: bool) -> bool:
		"""Draw the band again under settings that may have changed while it was kept.

		The band keeps its controller across a profile switch so the reader keeps their place,
		but its renderings were made under the settings in force then. Only settings that
		change how blocks are drawn are handled here; a change to what a block is, such as the
		reading unit or the band's size, gets a new controller. See
		`FlowBand._keptControllerFits`.

		:param indentStyle: the indent style in force now.
		:param lineFocus: whether the focus mark is wanted now.
		:return: whether anything changed and the band was drawn again.
		"""
		presentation = self._presentationFor(indentStyle, lineFocus)
		if presentation == self._presentation:
			return False
		self._presentation = presentation
		self.lineFocus = lineFocus
		with self.operation():
			if indentStyle != self.indentStyle:
				self.indentStyle = indentStyle
				depths = self._visibleDepths(list(self.window.blocks))
				if depths:
					self.renderer.indentPlan = flowIndent.planFor(
						depths,
						self.renderer.numCols,
						style=indentStyle,
					)
			self._redrawBlocks(why="settings changed while the band was kept")
			self.syncToCursor(why="settings changed while the band was kept")
		return True

	# Unwrapped lines.

	@property
	def isUnwrapped(self) -> bool:
		""":return: whether every block is drawn as one row, panned across by pages."""
		return self.renderer.unwrappedPage is not None

	@property
	def pageAcross(self) -> int | None:
		""":return: which page across the band is at, 0 for the start of every line, or None
		when lines wrap."""
		return self.renderer.unwrappedPage

	def setUnwrapped(self, unwrapped: bool) -> bool:
		"""Draw each block as one row the band's width, or wrap them again.

		Turned on at the page the caret is in. Turned off, the page is forgotten, so the next
		unwrap starts from wherever the caret is then.

		:param unwrapped: True to unwrap, False to wrap.
		:return: whether anything changed.
		"""
		if unwrapped == self.isUnwrapped:
			return False
		with self.operation():
			self.renderer.unwrappedPage = 0 if unwrapped else None
			self._acrossCaret = None
			if unwrapped:
				page = self._caretPageAcross()
				if page:
					self.renderer.unwrappedPage = page
			self._redrawBlocks(why="lines unwrapped" if unwrapped else "lines wrapped again")
			# A block that was several rows tall is one row now, or the other way about, so the
			# caret's row may have moved off the band or onto it.
			self.syncToCursor(why="lines unwrapped or wrapped")
		self._note(f"lines {'unwrapped' if unwrapped else 'wrapped'}")
		return True

	def panAcross(self, by: int) -> bool:
		"""Move every line of the band across by pages the band's width, leaving the caret alone.

		The caret stays where it is; a caret that then moves off the page brings the band back
		to it. See `followCaretAcross`. Stops at the end of the longest line on the band, not
		the caret's line.

		:param by: how many pages to move, negative for back towards the start of the lines.
		:return: whether the band moved. False when lines are not unwrapped, and at either end.
		"""
		page = self.renderer.unwrappedPage
		if page is None or by == 0:
			return False
		wanted = max(0, page + by)
		if wanted == page:
			return False
		if wanted > page and wanted * self.renderer.numCols >= (self._widestVisibleLine() or 0):
			return False
		with self.operation():
			self.renderer.unwrappedPage = wanted
			self._redrawBlocks(why="a different page across")
		# After the move, so the caret this remembers is the one the reader left where it was.
		self._acrossCaret = self._caretMark()
		self._note(f"the reader panning across: now at page {wanted}")
		return True

	def followCaretAcross(self) -> bool:
		"""Bring the page across back to the caret, if the caret has moved somewhere it does not
		show.

		Nothing moves while the caret is still where it was when the reader panned away, as
		`_panIsTheReadersChoice` does for rows. Any caret move ends that, even a move back to
		the same place. The page is also brought back to the last one a line on the band
		reaches, since what is on the band can get shorter after a pan.

		:return: whether the page changed.
		"""
		page = self.renderer.unwrappedPage
		if page is None:
			return False
		if self._acrossCaret is not None:
			if self._acrossCaret == self._caretMark():
				return self._keepAPageSomethingReaches(page)
			self._acrossCaret = None
		wanted = self._caretPageAcross()
		if wanted is None or wanted == page:
			return self._keepAPageSomethingReaches(page)
		with self.operation():
			self.renderer.unwrappedPage = wanted
			self._redrawBlocks(why="the caret moved to another page across")
		self._note(f"the caret moving across: now at page {wanted}")
		return True

	def _keepAPageSomethingReaches(self, page: int) -> bool:
		""":return: whether the page was brought back to the last one a line on the band reaches.

		:param page: the page across the band is at.
		"""
		widest = self._widestVisibleLine()
		if widest is None or page == 0:
			return False
		last = max(0, widest - 1) // max(1, self.renderer.numCols)
		if page <= last:
			return False
		with self.operation():
			self.renderer.unwrappedPage = last
			self._redrawBlocks(why="no line on the band reached the page across")
		self._note(f"no line reaches that far across: now at page {last}")
		return True

	def describeAcross(self) -> tuple[int, int, int] | None:
		""":return: the first and last cells across the band shows, counted from 1, and how many
		cells the longest line on the band holds; or None when lines are not unwrapped."""
		page = self.renderer.unwrappedPage
		if page is None:
			return None
		first = page * self.renderer.numCols + 1
		return first, first + self.renderer.numCols - 1, self._widestVisibleLine() or 0

	def _caretPageAcross(self) -> int | None:
		""":return: which page across the caret's cell is on, or None if there is no caret on
		the band to ask about."""
		if self.activeBlockId is None:
			return None
		at = getattr(self.regionFor(self.activeBlockId), "brailleCursorPos", None)
		if at is None:
			return None
		try:
			rendered = self.window.blocks[self.window.blockIndex(self.activeBlockId)]
		except LookupError:
			return None
		indent = getattr(rendered.renderKey, "indent", 0)
		return max(0, at + indent) // max(1, self.renderer.numCols)

	def _widestVisibleLine(self) -> int | None:
		""":return: how many cells the longest line on the band holds, or None if the band cannot
		say what it is showing."""
		try:
			rows = self.window.visibleRows()
		except LookupError:
			return None
		widest = 0
		for row in rows:
			if row.kind is not RowKind.CONTENT or row.blockId is None:
				continue
			try:
				block = self.window.blocks[self.window.blockIndex(row.blockId)]
			except LookupError:
				continue
			widest = max(widest, block.lineCells)
		return widest

	def rereadContent(self) -> bool:
		"""Read the blocks on the band again, in place, and draw what comes back.

		The window keeps its place and the blocks keep their identities; only their content is
		read again. Only a source that can be asked for a block by its identity, such as a
		table, answers.

		:return: whether anything was re-read. Whether it changed is the caller's to decide.
			See `FlowBand._refreshLiveContent`.
		"""
		if getattr(self.source, "blockAt", None) is None:
			return False
		with self.operation():
			changed = self._rereadBlocks()
			if changed is None:
				self._readAgainWhereTheDocumentMoved(why="the content moved under the band")
			else:
				self._redrawBlocks(why="the content changed under the band", changed=changed)
		return True

	def _readAgainWhereTheDocumentMoved(self, why: str, atObject=None) -> bool:
		"""Read the band again from the caret, with the reader's row where it was.

		For when text earlier in the page grew or shrank, which moves the offset of every block
		on the band. The caret is the one position still right, so cached positions are
		forgotten, the band is entered at the caret, and the caret's block goes back on the row
		it had. Not done when the reader's row is off the band, since they panned away from the
		caret on purpose.

		:param why: what asked, for the report.
		:param atObject: what the reader is arriving at, for an arrival. See `_arrive`.
		:return: whether the band was read again.
		"""
		place = self._readersPlaceOnTheBand() if self.live else None
		if place is None:
			# A viewer does not follow the caret, so the caret says nothing about where it is.
			self._note(f"{why}: the document moved, and the reader's row is not on the band, so it was left")
			return False
		above, rowIndex = place
		self.source.forget()
		if self.source.budget.stopped:
			# The re-read that found the move may have spent the budget. See `_placeAfresh`.
			self.source.budget.renew()
		if not self._enterAtCursor(atObject=atObject, fill=False):
			return False
		active = self.activeBlockId
		try:
			if above > 0:
				self._reachBack(above)
				self.window.enterAt(active, contextRows=above)
			elif rowIndex > 0:
				self.window.enterAt(active, rowIndex=rowIndex)
		except LookupError:
			log.debugWarning("Could not put the reader's row back after the document moved", exc_info=True)
		self.fill()
		self._note(f"{why}: the document moved, so the band was read again with the reader's row kept")
		return True

	def _canReadAgainWhereItMoved(self) -> bool:
		""":return: whether `_readAgainWhereTheDocumentMoved` has a place to keep."""
		return self.live and self._readersPlaceOnTheBand() is not None

	def _readersPlaceOnTheBand(self) -> tuple[int, int] | None:
		"""Where the cursor's block sits on the display.

		:return: how many rows are above its first row, and which of its rows is the top row
			when its first row is above the display (rows above is then zero). None when no row
			of it is on the display.
		"""
		if self.activeBlockId is None:
			return None
		try:
			visible = self.window.visibleRows()
		except LookupError:
			return None
		for position, row in enumerate(visible):
			if row.kind is RowKind.CONTENT and row.blockId == self.activeBlockId:
				return max(0, position - row.rowIndex), max(0, row.rowIndex - position)
		return None

	def _documentMovedUnderTheBand(self) -> bool:
		""":return: whether the block the reader is on no longer begins where it was read.

		Asked on arrival, before anything walks from the cached positions, since an arrival
		can come before the band's live pass has noticed.
		"""
		moved = getattr(self.source, "hasMoved", None)
		if not self.live or moved is None or self.activeBlockId is None:
			return False
		try:
			return bool(moved(self.activeBlockId))
		except Exception:
			log.debugWarning("Could not tell whether the document moved", exc_info=True)
			return False

	def runStillHoldsTheBand(self) -> bool:
		""":return: whether the rows beside the reader are still the ones the run has.

		Asked on arrival, because that is when a run changes under the reader, for example
		when they delete a message. Checks only the blocks either side of the arrival: two
		steps in the run rather than a walk of the whole run. A change at the far end of the
		band is left to the live pass. Only for a run of objects.

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
			# The reader is not on the band. Placing them is `followCursor`'s work.
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

	def tableStillHoldsTheBand(self) -> bool:
		""":return: whether the rows on the band are still rows the table is showing.

		The table counterpart of `runStillHoldsTheBand`, asked at the same moment, for rows a
		filter has hidden. Every row on the band is checked, since a filter can hide any of
		them and the rows shown are one answer. See `TableFlowSource.rowNoLongerShown`. Only
		for a table that can hide rows.

		:return: True when nothing has changed, and True when there is nothing to check.
		"""
		asked = getattr(self.source, "rowNoLongerShown", None)
		if asked is None:
			return True
		try:
			gone = asked(block.blockId.bookmark for block in self.window.blocks)
		except Exception:
			log.debugWarning("Could not tell whether the band is holding a hidden row", exc_info=True)
			return True
		if gone is None:
			return True
		self._note(f"the table has stopped showing row {gone}, which the band is holding")
		return False

	def rereadArrival(self) -> bool:
		"""Read the block for the object the reader has just arrived on again.

		Some applications, such as Outlook, describe an object correctly only while it is
		selected, so a row read while another was selected can carry the wrong state. This
		costs one call for the one block the reader is on. Only for a run of objects, since
		`ObjectFlowSource.isCurrentObject` is what says which block the arrival is.

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
					# The usual case. Keeping the held block also keeps the region NVDA may
					# have queued for update. See `_keep`.
					continue
				self._keep(result.block, replace=True)
				changed = True
			if changed:
				self._redrawBlocks(why="the reader arrived on this object")
		return True

	def _readsTheSame(self, held, incoming) -> bool:
		""":return: whether a block read again says what the one already held says.

		Compares text only. A block that cannot be compared counts as different, which costs
		only a redraw.

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

	def _rereadOrder(self, count: int) -> list[int]:
		"""Which of the window's blocks to read again, and in what order.

		A new sweep begins at the reader's row, and a sweep cut short by the budget carries on
		from where it stopped, wrapping round. Either fixed starting point alone would leave
		some rows never refreshed on a slow source.

		:param count: how many blocks the window holds.
		:return: their indices, in the order to read them.
		"""
		if count <= 0:
			return []
		start = self._rereadFrom if 0 < self._rereadFrom < count else None
		if start is None:
			active = self._windowIndex(self.activeBlockId)
			start = active if active is not None and 0 <= active < count else 0
		return [(start + step) % count for step in range(count)]

	def _rereadBlocks(self) -> list:
		"""Read every block the band is holding again, keeping its identity.

		Only for sources that can be asked for a block by its identity, such as a table by row
		number or a document by position. The window keeps its place; only content changes.

		1. A block the source will not vouch for keeps its old text rather than being dropped.
		2. The reader's block is made active again afterwards, because a re-read replaces its
		   region and a new region is inactive. See `_setActive`.
		3. The budget is checked between blocks, and a pass cut short carries on from there
		   next time. See `_rereadOrder`.
		4. One displaced block means every offset after it moved, so the whole pass is thrown
		   away and the caller reads again from the caret. See
		   `_readAgainWhereTheDocumentMoved`. Only where the reader's row is on the band;
		   otherwise, as in a pin, the blocks that still answer are kept.

		:return: the ids of the blocks that came back saying something different, for the
			redraw that follows. A block nobody re-read does not have to be laid out again.
			None when the document moved under the band, and nothing was kept.
		"""
		fetch = getattr(self.source, "blockAt", None)
		if fetch is None:
			return []
		if getattr(self.source, "writing", False):
			# The reader is typing, and the keystroke path re-reads the band. See
			# `_readAgainWhileWriting`.
			return []
		# The window's blocks, which are what `_redrawBlocks` draws.
		blocks = list(self.window.blocks)
		lostTheCursor = False
		changed: list = []
		cutShort = False
		# Held back until every block has answered, so that a displacement found at the last
		# block can still refuse what the first one said.
		readings: list = []
		for index in self._rereadOrder(len(blocks)):
			rendered = blocks[index]
			held = self.blocks.get(rendered.blockId)
			if held is not None and getattr(held, "isControl", False):
				# A plain re-read would lose that it is a control, and NVDA keeps a control's
				# text fresh itself.
				continue
			if self.source.budget.refuseIfExhausted():
				self._rereadFrom = index
				cutShort = True
				break
			try:
				result = fetch(rendered.blockId)
			except Exception:
				log.debugWarning(f"Could not read {rendered.blockId} again", exc_info=True)
				continue
			if result.displaced and self._canReadAgainWhereItMoved():
				self._rereadFrom = 0
				return None
			if result.kind is ResultKind.BLOCK and result.block is not None:
				readings.append((rendered, held, result.block))
		for rendered, held, block in readings:
			if not self._readsTheSame(held, block):
				changed.append(rendered.blockId)
			self._keep(block, replace=True)
			if self.activeBlockId is not None and rendered.blockId == self.activeBlockId:
				lostTheCursor = True
		if not cutShort:
			self._rereadFrom = 0
		if lostTheCursor:
			# Only when the reader's block was replaced, to avoid re-reading it on every tick.
			self._setActive(self.activeBlockId)
		return changed

	def _renderingStands(self, block, source, changed) -> bool:
		""":return: whether a rendering already on the band is still the right one.

		A block that was not re-read and whose render key is unchanged would render the same.
		The cursor's block is always drawn again, because its cells depend on where the cursor
		is (NVDA expands the word at the cursor), which is not part of the key.

		:param block: the rendering the band is holding.
		:param source: the block behind it.
		:param changed: the ids of the blocks that were just re-read and said something new.
		"""
		if self.activeBlockId is not None and block.blockId == self.activeBlockId:
			return False
		if any(block.blockId == blockId for blockId in changed):
			return False
		if block.depth != source.depth:
			# The cells may match, but the indent is planned from the depth.
			return False
		try:
			return block.renderKey == self.renderer.keyFor(source)
		except Exception:
			log.debugWarning("Could not tell whether a rendering still stands", exc_info=True)
			return False

	def _redrawBlocks(self, why: str, changed=None) -> None:
		"""Lay every block on the band out again, under whatever the renderer says now.

		For changes to how the band is drawn rather than what it reads, such as a rebased
		indent, a column page or a re-read. Always the whole window, including the margin
		blocks off the display, since `window.replaceBlocks` takes what it is given as the
		whole window.

		:param why: what asked, for the log.
		:param changed: the ids of the blocks just re-read that came back different, so the
			rest keep their renderings. None when every rendering is stale, such as after a
			rebased indent or a different page of columns.
		"""
		rendered = list(self.window.blocks)
		if not rendered:
			return
		began = self.source.budget.clock()
		fresh = []
		for block in rendered:
			source = self.blocks.get(block.blockId)
			if source is None:
				# Rendered but no longer held. Kept as it is rather than dropped.
				fresh.append(block)
				continue
			if changed is not None and self._renderingStands(block, source, changed):
				fresh.append(block)
				continue
			fresh.append(self.renderer.render(source, fromRow=block.rowOffset))
		# Charged to the budget, as a fetch is.
		self.source.budget.spend(self.source.budget.clock() - began)
		if not self.window.replaceBlocks(fresh):
			log.debug(f"BrlMultiline flow: the anchor did not survive {why}")
			return
		# A shallower baseline makes blocks shorter, which can leave the band short of rows.
		# A deeper one only ever makes them taller, and the window trims its own surplus.
		self._fillBothEnds()

	def _visibleDepths(self, rendered) -> list[int | None]:
		"""The depths the reader can actually feel, in the order they are on the band.

		Indent is relative to the shallowest thing on the display, so this reads the visible
		rows rather than the window's blocks, which include a margin off the display. Each
		block is counted once, however many rows it has.

		:param rendered: the window's blocks, in reading order.
		:return: one depth per visible block, `None` for those with none.
		"""
		try:
			rows = self.window.visibleRows()
		except LookupError:
			return []
		depths: list[int | None] = []
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

		The margin is a window's worth either side. The active block is always kept, since
		commands act on it and it may be outside the window after a pan.
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
		:return: whether anything came back, not whether the shortfall was made up. See `fill`.
		"""
		added = False
		for _ in range(MAX_FETCHES):
			if self.window.shortfall(edge) <= 0:
				return added
			if self.source.budget.refuseIfExhausted():
				# Out of budget, not out of document, and the display says so.
				self._deferEdge(edge)
				return added
			if not self._fetchOne(edge):
				return added
			added = True
		log.debugWarning(f"A flow stopped fetching {edge.value} after {MAX_FETCHES} blocks")
		return added

	def _deferEdge(self, edge: Edge) -> None:
		"""Close one end of the band for want of budget, and say which budget.

		Records the reason as well as the state, so the report does not show an earlier
		answer's reason for a budget stop.

		:param edge: which end ran out.
		"""
		budget = self.source.budget
		self.window.setEdge(edge, EdgeState.DEFERRED)
		spent = "blocks" if budget.blocks >= budget.maxBlocks else "time"
		self.edgeReasons[edge] = f"the operation ran out of {spent}"

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
			# Every path that fetches comes through here, so the budget is enforced here.
			self._deferEdge(edge)
			return False
		if self._continueBlock(edge):
			# The block at this end has more rows of its own, which come before the next block.
			return True
		anchorId = blocks[0].blockId if edge is Edge.BEFORE else blocks[-1].blockId
		result = (
			self.source.blockBefore(anchorId) if edge is Edge.BEFORE else self.source.blockAfter(anchorId)
		)
		if result.kind is not ResultKind.BLOCK or result.block is None:
			# The end of the stream shows as blank rows; DEFERRED and ERROR show as more to come.
			self.window.setEdge(edge, result.edgeState)
			self.edgeReasons[edge] = result.message or result.kind.value
			if edge is Edge.AFTER and result.edgeState is EdgeState.END:
				# Here, because every path that reaches the end comes through here.
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
		# Charged to the budget, so one huge paragraph cannot run an operation unbounded.
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

		A block already held keeps the region it was first read with, because NVDA may have
		queued that region object for update, and the region holds the block's reading
		position.

		:param block: the block just read.
		:param replace: take the new reading anyway, for a caller that re-read the block on
			purpose, such as a table changing page. `flowSegment._syncRegions` re-points the
			regions NVDA holds on the next update.
		:return: the block to use, which may be one already held.
		"""
		existing = self.blocks.get(block.blockId)
		if existing is not None and not replace and self._sameRegionOwner(existing, block):
			if existing.depth != block.depth or existing.continuesItem != block.continuesItem:
				# Keep the region, take the new depth.
				existing = dataclasses.replace(existing, depth=block.depth, continuesItem=block.continuesItem)
				self.blocks.set(existing.blockId, existing)
			return existing
		if existing is not None and block.blockId is not existing.blockId:
			# One id object per block while it is held, so lookups by identity keep working
			# instead of comparing bookmarks. See `flow.findByIdentityFirst`.
			block = dataclasses.replace(block, blockId=existing.blockId)
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
			# `dirty` signals a caret move from NVDA. A block this flow fetched itself is not
			# one, so it must not send the window to the cursor.
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

		The line commands for a run of objects, whose cursor is a selection that must not
		move. Elsewhere `stepBlock` is used, which takes the cursor.

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
			# The real cursor moved, so the window follows it. Other flows moved a position
			# of their own, which the cursor knows nothing about.
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
		""":return: the region reading one block, or None if it is not held.

		Every use of a block's region comes through here, so this is where its glyphs are
		checked against its cells. See `glyphFlow.keepCompressed`.
		"""
		block = self.blocks.get(blockId)
		region = getattr(block, "region", None)
		glyphFlow.keepCompressed(region, self.renderer.glyphTarget)
		return region

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

		Decides whether a live run is worth asking to grow: only a reader who can feel
		everything read so far is waiting for more. This is the tail of what has been read,
		not the end of the stream, so it does not depend on the edge state.
		"""
		if self.window.edges[Edge.AFTER] in (EdgeState.DEFERRED, EdgeState.ERROR):
			# `hasMoreToFetch` and `fill` handle these.
			return False
		# Measured in rows, because `_trim` drops cached blocks far from the window.
		return self.window.rowsBelow() == 0

	def reconsiderEnd(self, scrollIntoView: bool = False) -> bool:
		"""Ask the source again for whatever may have arrived past the tail.

		An ended stream otherwise stays ended, which is wrong for content still being written,
		such as a pinned chat. The edge is opened and one fetch made; if the stream really has
		ended, `_fetchOne` records it again, so content that is not growing costs one refused
		fetch. Asked at an open edge too, since a band that is exactly full stops with its edge
		open. Only the far end: panning back reaches anything new before the start.

		:param scrollIntoView: bring what arrives onto the display. See
			`bmConfig.shouldScrollToNewContent`. Applies only where the reader was at the tail
			of something whose end has been reached before. See `hasBeenToTheEnd`.
		:return: whether anything new was found.
		"""
		if self.window.edges[Edge.AFTER] not in (EdgeState.END, EdgeState.OPEN):
			# DEFERRED or ERROR. The budget and the failure paths own those.
			return False
		# Asked before the fetch, which changes both answers.
		wasAtTheTail = self.isShowingTheTail
		wasEnded = self.hasBeenToTheEnd
		with self.operation():
			self.window.setEdge(Edge.AFTER, EdgeState.OPEN)
			if not self._fetchOne(Edge.AFTER):
				return False
			# More than one may have arrived.
			self._fillBothEnds()
			if scrollIntoView and wasAtTheTail and wasEnded:
				self._showWhatArrived()
		return True

	def lookPastTheEnd(self) -> bool:
		""":return: whether the stream ends after the last block on the band, having asked.

		A band that filled exactly never asked whether the run ends there, so
		`hasBeenToTheEnd` would stay false. One call, made when a pin is built; a block that
		comes back is discarded, since it only answers the question.
		"""
		blocks = self.window.blocks
		if self.hasBeenToTheEnd:
			return True
		if not blocks:
			return False
		if self.window.edges[Edge.AFTER] is EdgeState.END:
			# Something already reached it, usually a run shorter than the band.
			self.hasBeenToTheEnd = True
			return True
		if self.window.edges[Edge.AFTER] is not EdgeState.OPEN:
			# DEFERRED or ERROR. The budget and the failure paths own those.
			return False
		# Inside an operation, so the budget the source starts is also finished.
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

		By the smallest movement that shows its first row, through `ensureVisible`. An
		arrival longer than the band leaves the rest below, so `isShowingTheTail` is false and
		the tail watch waits until the reader has panned through it.
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

		Recorded in the move history, like everything else that moves the band.

		:param forward: the direction to pan.
		:return: whether the window moved.
		"""
		with self.operation():
			moved = self._panWithin(forward)
		if not moved and forward and self.reconsiderEnd():
			# The stream has grown since it ended.
			with self.operation():
				moved = self._panWithin(forward)
		if moved:
			# After the pan, so this remembers the caret the pan left behind.
			self._pannedAt = self._caretMark()
			self._pannedCaret = self._caretsAfterAPan()
			# The reader chose a new window, so a writing re-read must not go back to the old one.
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
				# Nothing more that way, so the window stays where it is.
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

		What NVDA's own commands must reach. See docs/design/flow-last-region-audit.md.
		"""
		if self.activeBlockId is None:
			return None
		return self.regionFor(self.activeBlockId)

	def followCursor(self, ground: bool = False) -> bool:
		"""Answer a cursor move that was not a pan.

		Moves the window by the smallest amount that brings the cursor's block into view,
		which is nothing when it is already there. A cursor the cache cannot reach is a jump,
		and the window is placed afresh.

		:param ground: put the cursor's block at the top of the band instead, for a jump by
			structure. See docs/design/spatial-reading-plan.md, Grounding a jump.
		:return: whether anything changed.
		"""
		with self.operation():
			return self._followCursor(ground=ground)

	def _followCursor(self, ground: bool = False) -> bool:
		""":return: whether anything changed. See `followCursor`."""
		return self._arrive(ground=ground) is Arrival.CHANGED

	def arriveAt(self, atObject=None, ground: bool = False) -> bool:
		"""Answer the reader arriving somewhere in the document already being read.

		Tab, the arrow keys and quick navigation keys all land here. A target already on the
		display moves nothing, and one just off it gets the least movement that shows it. Only
		a jump by structure grounds, and only a target the cache cannot reach is entered
		afresh.

		:param atObject: what they arrived at, read at its own place in the document rather
			than at the cursor, or None to read at the cursor.
		:param ground: put what they arrived at on the top row. See `followCursor`.
		:return: whether anything is on the display.
		"""
		with self.operation():
			return self._arrive(atObject=atObject, ground=ground) is not Arrival.NOTHING

	def _arrive(self, atObject=None, ground: bool = False) -> Arrival:
		"""Find where the reader is now and bring the window to it."""
		if self._writing():
			return self._readAgainWhileWriting(ground=ground)
		if self._documentMovedUnderTheBand():
			# Before the walk below, which would start from cached positions that are now wrong.
			if not self._readAgainWhereTheDocumentMoved(why="the reader arriving", atObject=atObject):
				# Their row was not on the band, so the band is placed afresh around them.
				self.source.forget()
				return Arrival.CHANGED if self._placeAfresh(atObject) else Arrival.NOTHING
		result = self.source.blockAtCursor(atObject)
		self.lastResult = result
		if result.kind is not ResultKind.BLOCK or result.block is None:
			return Arrival.NOTHING
		blockId = result.block.blockId
		forward = self._isForward(blockId)
		if not self.window.hasBlock(blockId) and not self._reach(blockId, forward):
			return Arrival.CHANGED if self._placeAfresh(atObject) else Arrival.NOTHING
		# Asked again. The first answer was a guess, because the block was not in the window
		# yet; now that it is, the direction is measured, and the window is placed by that.
		forward = self._isForward(blockId)
		held = self._keep(result.block)
		# The id held, not the one just read, so lookups by identity find it.
		blockId = held.blockId
		self._setActive(blockId, freshlyRead=held.region is result.block.region)
		# Lay out the active region before asking which cursor row is visible, so a cached
		# block does not answer with an old cursor position. Re-read unless `_keep` just took
		# the fresh reading.
		self.refreshActive(reread=not self._activeIsFreshlyRead)
		moved = (
			self.groundAt(blockId)
			if ground
			else self.syncToCursor(forward=forward, why="the reader arriving")
		)
		return Arrival.CHANGED if moved else Arrival.UNCHANGED

	def _placeAfresh(self, atObject) -> bool:
		"""Put the band around the reader, after reaching for them failed.

		Renews the budget if the failed reach spent it, so placing the band has a budget to
		fill with. An arrival therefore costs at most two budgets. See `FetchBudget.renew`.

		:param atObject: what the reader arrived at, if the document can place it.
		:return: whether anything is on the display.
		"""
		if self.source.budget.stopped:
			# Only when the reach was refused, not when it reached the end of the document.
			self.source.budget.renew()
			self._note("the reach ran out of time, so placing the band afresh was given its own")
		return self._enterAtCursor(atObject=atObject)

	def _activeLength(self) -> int | None:
		""":return: how many characters the block under the caret holds, or None if unreadable.

		The signal that an edit has moved everything after it: block positions are offsets,
		so they shift exactly when this block's length changes. Length rather than text, so a
		same-length rewrite keeps the cheap path. Changes on other lines are left to the
		band's ordinary re-read.
		"""
		region = self.activeRegion()
		if region is None:
			return None
		text = getattr(region, "rawText", None)
		return None if text is None else len(text)

	def _writing(self) -> bool:
		""":return: whether the reader is typing into the document this flow reads."""
		return bool(self.live and getattr(self.source, "writing", False))

	def _readAgainWhileWriting(self, ground: bool = False) -> Arrival:
		"""Read the whole band again from the caret, keeping it on the row it was on.

		Typing moves the offsets every block position is, so while the reader is writing a
		caret update re-reads the whole band, within the budget. The band is then put back
		under the block that was on its top row, so typing does not scroll the display; if
		the edit removed that block, the caret leads. When the reader has panned and the caret
		has not moved, only the caret's row is read again. See
		docs/design/spatial-reading-plan.md, Following the cursor.

		:param ground: put the caret's block on the top row instead.
		:return: CHANGED, or NOTHING if nothing could be read. See `Arrival`.
		"""
		if not ground and self._caretIsWhereThePanLeftIt():
			# The caret's line can still change, as with forward Delete, so its block is read
			# again. It is on the band, being the one `_cursorToTop` put the cursor on.
			was = self._activeLength()
			self.refreshActive()
			if was == self._activeLength():
				self._note(
					"a keystroke while writing: the caret is where the pan left it, "
					"so only its own row was read again",
				)
				return Arrival.CHANGED
			# The caret's row changed length, so every position after it moved and the whole
			# band is read again. The top row is the one the reader panned to, so it is
			# trusted even if it is the caret's own block. See `_restoreTop`.
			self._note(
				"a keystroke while writing: the caret stayed put but its row changed length, "
				"so the band was read again",
			)
			return self._readAgainKeeping(self.window.topBlockId(), trusted=True)
		return self._readAgainKeeping(self._stableTop(), ground=ground)

	def _readAgainKeeping(self, topId, ground: bool = False, trusted: bool = False) -> Arrival:
		"""Read the band again from the caret, and put it back under a chosen top row.

		:param topId: the block to anchor the band under again, or None to let the caret lead.
		:param ground: put the caret's block on the top row instead.
		:param trusted: whether that top may be the caret's own block. See `_restoreTop`.
		:return: CHANGED, or NOTHING if nothing could be read. See `Arrival`.
		"""
		# Nothing the source cached about this document can be trusted after an edit. The top
		# block is found again by value, since a block id compares by where it starts.
		self.source.forget()
		# Without filling, because the window is moved first and filled once at the end.
		if not self._enterAtCursor(fill=False):
			self._writingTop = None
			return Arrival.NOTHING
		self.rereadWhileWriting = True
		if ground:
			self._writingTop = None
			self._pannedCaret = ()
			return Arrival.CHANGED
		if self._restoreTop(topId, trusted=trusted):
			self._writingTop = topId
			why = "a keystroke while writing, put back under the row the band had"
		else:
			# `_writingTop` is kept, since the restore may succeed on the next keystroke.
			self._contextAboveTheCaret()
			why = "a keystroke while writing, no old top to go back to so the caret leads"
		self.fill()
		# `why` names which branch ran, for the move history.
		self.syncToCursor(forward=True, why=why)
		return Arrival.CHANGED

	def _stableTop(self):
		"""The block the band's top row should be put back under, or None for no claim.

		The top row is believed only when it is not the caret's own block, because a rich
		editor answering a return can briefly give a merged block at the caret. Otherwise the
		last top the reader was shown that was not the caret's is used.
		"""
		top = self.window.topBlockId()
		if top is not None and (self.activeBlockId is None or top != self.activeBlockId):
			self._writingTop = top
			return top
		return self._writingTop

	def _restoreTop(self, topId, trusted: bool = False) -> bool:
		"""Put the band back under the row it was showing before a writing re-read.

		:param topId: the block that was on the top row, or None for an empty band.
		:param trusted: whether the reader put the band there themselves, by panning. A top
			that is the caret's own block is otherwise refused. See `_stableTop`.
		:return: whether the band is anchored there again. False when reading back to it
			failed, so the caller can choose another anchor.
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

		For a writing re-read that cannot put the band back by its old top row. Asked afresh on
		every re-read, so the band recovers as soon as the editor answers again. Half the
		band, so the caret keeps rows below it for what follows.
		"""
		active = self.activeBlockId
		if active is None:
			return
		wanted = max(1, self.window.numRows // 2)
		self._reachBack(wanted)
		try:
			self.window.enterAt(active)
			rowsAbove = self.window.rowsAbove()
			# Short only when refused by the budget, not when the document starts here. See
			# `_owedAbove`.
			short = rowsAbove < wanted and self.window.edges[Edge.BEFORE] is EdgeState.DEFERRED
			# Nothing moves for a part payment, as in `_payWhatIsOwedAbove`.
			if rowsAbove > 0 and not short:
				self.window.enterAt(active, contextRows=min(wanted, rowsAbove))
			self._owedAbove = wanted if short else 0
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

		Measured against the block the cursor was in, which is the better account of which
		way the reader is travelling. That block can have been trimmed from the window, so
		the fallback is the window's anchor, which the window always holds.

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

		A short history of the last eight, in order, so a report shows which call moved the
		band and which found nothing to do.

		:param text: what happened, as one line.
		"""
		self._placements.append(text)
		del self._placements[:-8]

	def _windowIndex(self, blockId: "BlockId | None") -> int | None:
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
		# Both ways, because the direction is only a guess while the block is not in the window.
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

		If it is already there, nothing moves.

		:param forward: the direction the reader is travelling, used by
			`FlowWindow.ensureVisible` only as a tiebreak. A caller that cannot tell may pass
			either.
		:param why: what is asking, for the move history.
		:return: whether the window moved.
		"""
		if self.activeBlockId is None:
			return False
		if self._panIsTheReadersChoice():
			self._note(f"{why}: the reader panned here and has not moved, so nothing moved")
			return False
		# The cursor's own row, not the block's first, for a block taller than the band.
		row = self.cursorRow()
		try:
			moved = self.window.ensureVisible(
				self.activeBlockId,
				rowIndex=row if row is not None else 0,
				forward=forward,
				fullDisplay=self.caretByDisplay,
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

		The cursor's row is brought on first, then the far end of the block, so a wrapped table
		row or a two-line file name shows whole. A block taller than the band is left alone,
		since showing its far end would scroll the cursor's row off.

		:param blockId: the block the cursor is in.
		:return: whether the window moved.
		:raises LookupError: if the block is not in the window, as `ensureVisible` does.
		"""
		rendered = self.window.blocks[self.window.blockIndex(blockId)]
		last = rendered.numRows - 1
		if last < 1 or rendered.numRows > self.window.numRows:
			return False
		if rendered.rowOffset or rendered.moreRows:
			# A chunk of a long edit, whose far end is not the end of the block.
			return False
		if self.window.isVisible(blockId, last):
			return False
		return self.window.ensureVisible(blockId, rowIndex=last, forward=True)

	def _caretMark(self):
		""":return: where the caret is, as something two calls can compare.

		The active block and the position within it, not the row, since after a pan the
		caret's row may be off the band.
		"""
		if self.activeBlockId is None:
			return None
		region = self.regionFor(self.activeBlockId)
		return (self.activeBlockId, getattr(region, "brailleCursorPos", None))

	def _panIsTheReadersChoice(self) -> bool:
		"""Whether the window is where the reader panned it and the caret has not moved since.

		A pan within the caret's block is the reader looking at another part of it, so the
		cursor is not followed until the caret moves. Only while the block is still on the
		band.

		:return: whether to leave the window where it is.
		"""
		if self._pannedAt is None:
			return False
		try:
			held = self._pannedAt == self._caretMark() and self.window.isVisible(self.activeBlockId)
		except LookupError:
			held = False
		if not held:
			# Forgotten, because coming back to the same place is not panning again.
			self._pannedAt = None
		return held

	def _caretPosition(self):
		""":return: where the document's own caret is, or None if it cannot be asked.

		Compared by value. It comes from the document rather than from the rendering, which a
		re-read is about to replace.
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

		This says only that the reader has not moved, not that nothing was typed: forward
		Delete changes the line without moving the caret, so the caller still re-reads the
		caret's row.

		A writing re-read asks this first, because its anchoring would otherwise undo the pan:
		`_cursorToTop` makes the new top block the caret's own, which `_stableTop` refuses, so
		the re-read would go back to the window before the pan or put the caret half a band
		down. Answered from the document's own caret, so it can be asked before the re-read.

		:return: whether to leave the band exactly where the reader panned it.
		"""
		if not self._pannedCaret:
			return False
		here = self._caretPosition()
		if here not in self._pannedCaret:
			# Forgotten, as in `_panIsTheReadersChoice`.
			self._pannedCaret = ()
			return False
		# Drop the older answers once a newer one is seen, so going back to where the pan
		# started counts as a move.
		self._pannedCaret = self._pannedCaret[self._pannedCaret.index(here) :]
		return True

	def _caretsAfterAPan(self) -> tuple:
		"""Where the caret may be found after a pan without the reader having typed.

		Two answers: the caret read back straight after the pan can still be where it was
		before, and the settled position arrives with the caret event a moment later. Both
		mean the reader has not moved.

		:return: the marks that mean the reader has not typed, newest first, possibly empty.
		"""
		marks = []
		for mark in (self._caretPosition(), self._cursorMark(self.window.topBlockId())):
			if mark is not None and mark not in marks:
				marks.append(mark)
		return tuple(marks)

	def _cursorMark(self, blockId):
		""":return: the mark for where this flow put the cursor in a block, or None.

		The position the flow asked for, taken from the block, since the document may not
		have caught up yet.
		"""
		if blockId is None or not (self.live and self.movesCursor):
			return None
		return getattr(self.regionFor(blockId), "cursorMark", None)

	def _takeCursor(self, blockId: "BlockId") -> bool:
		"""Move the real cursor to a block, for a live flow.

		Moving the real cursor is what makes speech follow and the arrow keys carry on from
		there. A viewer does not.

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

		So the arrow keys continue from the top of what is shown. Only where the flow owns the
		cursor; in a run of objects the reader's place is the focused item and panning is
		reading past it.
		"""
		if not self.movesCursor:
			return
		topId = self.window.topBlockId()
		if topId is None:
			return
		self._setActive(topId)
		self._takeCursor(topId)

	def _setActive(self, blockId: "BlockId", freshlyRead: bool = False) -> None:
		"""Make one block the active one, and no other.

		Every block holds a collapsed position, so every block looks to NVDA like it holds
		a cursor. Only the active one is allowed to say so.

		:param freshlyRead: whether the region now held for this block is a reading just
			taken, so `refreshActive` can skip repeating it. The caller says, because `_keep`
			may have kept an older region.
		"""
		self._activeIsFreshlyRead = freshlyRead
		self.activeBlockId = blockId
		for block in self.blocks.values():
			region = getattr(block, "region", None)
			if region is None:
				continue
			region.isActive = block.blockId == blockId
		self._giveTheActiveBlockItsCursor()

	def _giveTheActiveBlockItsCursor(self) -> None:
		"""Re-read the block that has just become active, so that it has a cursor.

		An inactive block suppresses its cursor, so a block read before it became active has
		none until it is read again.
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
		# The flow asked for this reading, so it is not a caret move. See `_keep`.
		region.dirty = False
		self._activeIsFreshlyRead = True

	def _withCurrentDepth(self, block):
		"""Hold a block at the depth its region now reads, where its source can say.

		A block's depth is fixed when it is built, but NVDA re-reads its region on its own, so
		an item moved to another level is caught here.

		:param block: the block whose region has just been read.
		:return: the block to use, the one held if nothing changed.
		"""
		currentDepth = getattr(self.source, "currentDepth", None)
		if currentDepth is None:
			return block
		currentContinues = getattr(self.source, "currentContinuesItem", None)
		try:
			depth = currentDepth(block)
			continues = bool(currentContinues(block)) if currentContinues is not None else block.continuesItem
		except Exception:
			log.debugWarning("Could not read a block's depth again", exc_info=True)
			return block
		if depth == block.depth and continues == block.continuesItem:
			return block
		block = dataclasses.replace(block, depth=depth, continuesItem=continues)
		self.blocks.set(block.blockId, block)
		return block

	def refreshActive(self, reread: bool = True) -> bool:
		"""Re-render the block the cursor is in, after it changed under the reader.

		Only that block, because that is the one NVDA marks as pending on the core cycle.

		:param reread: whether to ask the application for the block again first. False where
			the region was read since it became active. The re-rendering still happens, since
			it picks the chunk of a long edit the caret is in.
		:return: whether the rendering changed.
		"""
		if self.activeBlockId is None:
			return False
		block = self.blocks.get(self.activeBlockId)
		if block is None:
			return False
		if reread:
			try:
				block.region.update()
			except Exception:
				log.debugWarning(f"Could not refresh {self.activeBlockId}", exc_info=True)
				return False
			if hasattr(block.region, "dirty"):
				# The flow asked for this reading, so it is not a caret move. See `_keep`.
				block.region.dirty = False
			self._activeIsFreshlyRead = True
			block = self._withCurrentDepth(block)
		try:
			before = self.window.blocks[self.window.blockIndex(self.activeBlockId)]
		except LookupError:
			return False
		rendered = self._renderActiveChunk(block, before)
		if (
			rendered.rows == before.rows
			and rendered.positions == before.positions
			and rendered.rowOffset == before.rowOffset
			and rendered.depth == before.depth
		):
			return False
		self.window.replaceBlock(rendered)
		# The window follows the growing end of an edit being typed into. Forward is only a
		# tiebreak; `FlowWindow.ensureVisible` decides the edge from where the row is.
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
		if at is None or self.isUnwrapped or any(at in row for row in rendered.positions):
			# Unwrapped, a caret off the row is on another page across. See `followCaretAcross`.
			return rendered
		return self.renderer.renderAround(
			block,
			at,
			contextRows=max(0, self.window.numRows - 1),
		)

	def cursorRow(self) -> int | None:
		"""Which row of the active block the cursor is on, counted within the whole block.

		Not the display row, since the usual reason to ask is that it is off the display.

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
		# The chunk does not hold the cursor, until `refreshActive` replaces it.
		if not rendered.rows or at <= highest:
			# Behind what is shown, or unaccounted for, so no guess.
			return None
		# Past the end, where a reader who is writing is: the last row.
		return rendered.endRow - 1

	# Showing.

	def setPinned(self, block: "SourceBlock | None") -> None:
		"""Hold one block above the window, on the band's top row.

		:param block: the block to pin, or None to stop pinning one.
		"""
		self.pinnedBlock = block
		self.refreshPinned()

	def refreshPinned(self) -> None:
		"""Draw the pinned block again from what its region says now.

		Done on every `cells`, because whether the cursor is in the row can change and a region
		works out its cursor when updated. This re-translates; it does not read the document.
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

		The padding keeps the block after a short one off its row. The pinned row goes on top
		and is not part of the window, so window positions are offset by it. See `pinnedBlock`.
		"""
		self.refreshPinned()
		try:
			cells = assembleCells(self.window, self.renderer.numCols)
		except LookupError:
			cells = [0] * (self.window.numRows * self.renderer.numCols)
		else:
			self._markLineFocus(cells)
		return self._pinnedRow() + cells

	def cellGlyphs(self) -> dict:
		"""Where every symbol on the band is now, for the driver to draw.

		Worked out afresh each frame, because the driver retires a glyph as soon as a frame
		arrives without it. Each cell is asked where it came from, as routing does.

		:return: `{band position: the fitted glyph}`, empty when there is nothing to draw.
		"""
		found: dict = {}
		self._pinnedGlyphs(found)
		self._focusGlyphs(found)
		numCols = self.renderer.numCols
		# Only the last block is remembered: bookmarks do not hash, and a block's cells are
		# consecutive.
		lastId = None
		marks: dict = {}
		rows = self.window.visibleRows()
		for position in range(self.window.numRows * numCols):
			source = cellSource(self.window, numCols, position, rows)
			if source is None:
				continue
			blockId, at = source
			if blockId is not lastId:
				lastId = blockId
				marks = glyphFlow.marksOf(self.regionFor(blockId))
			fitted = marks.get(at) if marks else None
			if fitted is not None:
				found[position + self.pinnedCells] = fitted
		return found

	def _pinnedGlyphs(self, found: dict) -> None:
		"""Add the pinned row's symbols, which are not in the window and so are not walked.

		See `_pinnedCursor`, which reads the same position map.

		:param found: the table being built, by band position.
		"""
		if self.pinned is None or self.pinnedBlock is None or not self.pinned.positions:
			return
		marks = glyphFlow.marksOf(self.pinnedBlock.region)
		if not marks:
			return
		for column, where in enumerate(self.pinned.positions[0]):
			fitted = marks.get(where)
			if fitted is not None:
				found[column] = fitted

	def displayFrame(self) -> tuple:
		""":return: everything a redraw would put on the display, for comparing two passes.

		The cells and the cursor, since a pass that only moved the cursor has still changed
		the display.
		"""
		return (tuple(self.cells()), self.cursorCell())

	def _markLineFocus(self, cells: list[int]) -> None:
		"""Mark the left of the rows the focus is on.

		Drawn here rather than rendered into the block, like the cursor, because the focus
		moves without the rows changing. Every row of the focused block is marked. Whether
		there is room is asked once of the item, not of each row, since wrapped rows carry
		extra indent. See `flowIndent.focusMark`.

		Where the band draws glyphs, the mark is the Monarch's own: the square on the first
		cell and the second left blank. See `_focusGlyph`.

		:param cells: the assembled band, modified in place.
		"""
		mark, marked = self._lineFocusRows()
		if not marked:
			return
		if self._focusGlyph() is not None:
			mark = (mark[0],) + (BLANK_CELL,) * (len(mark) - 1)
		numCols = self.renderer.numCols
		for index in marked:
			for offset, cell in enumerate(mark):
				cells[index * numCols + offset] = cell

	def _lineFocusRows(self) -> "tuple[tuple[int, ...], list[int]]":
		"""Which rows of the window carry the focus mark, and the mark they carry.

		One answer for the cells and the glyphs both, so the square can never be registered on a
		row that was not marked. See `_markLineFocus` for why the room is asked of the item.

		:return: the mark's cells, and the window rows it goes on; no rows where nothing is marked.
		"""
		if not self.lineFocus or self.activeBlockId is None:
			return (), []
		try:
			rows = self.window.visibleRows()
			block = self.window.blocks[self.window.blockIndex(self.activeBlockId)]
		except LookupError:
			return (), []
		mark = flowIndent.focusMark(self.renderer.indentPlan.cellsFor(block.depth))
		if not mark:
			return (), []
		marked = []
		for index, row in enumerate(rows):
			if row.kind is not RowKind.CONTENT or row.blockId != self.activeBlockId:
				continue
			if self._indentOfRow(row) < len(mark):
				# A safeguard so the mark never covers text. It should not fire.
				continue
			marked.append(index)
		return mark, marked

	def _focusGlyph(self) -> "glyphs.Fitted | None":
		""":return: the focus square fitted for the band's display, or None where none is drawn.

		Fitted over the one cell it stands on, so it is drawn in place and moves nothing. Kept
		per driver, because a shape belongs to the driver that built it and the band can move.
		"""
		target = self.renderer.glyphTarget
		if target is None or not self.renderer.glyphsWanted:
			return None
		held = self._focusFitted
		if held is not None and held[0] is target.driver:
			return held[1]
		fitted = glyphs.fittedOver(target.driver, glyphs.FOCUS, [flowIndent.FOCUS_CELL])
		self._focusFitted = (target.driver, fitted)
		return fitted

	def _focusGlyphs(self, found: dict) -> None:
		"""Add the focus square on the first cell of every marked row.

		:param found: the table being built, by band position.
		"""
		fitted = self._focusGlyph()
		if fitted is None:
			return
		_mark, marked = self._lineFocusRows()
		numCols = self.renderer.numCols
		for index in marked:
			found[index * numCols + self.pinnedCells] = fitted

	def _indentOfRow(self, row) -> int:
		"""How many cells of indent one row of the window was drawn with.

		Read off the rendering: the indent is the run of cells at the front with no position.

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

	def cursorCell(self) -> int | None:
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
		rows = self.window.visibleRows()
		active = self.activeBlockId
		# Asked once per block, and the position first, because comparing ids can call into the
		# application. See `flow.findByIdentityFirst`.
		lastId = None
		isActive = False
		for position in range(self.window.numRows * numCols):
			source = cellSource(self.window, numCols, position, rows)
			if source is None or source[1] != at:
				continue
			if source[0] is not lastId:
				lastId = source[0]
				isActive = lastId is active or lastId == active
			if isActive:
				return position + self.pinnedCells
		return None

	def _pinnedCursor(self) -> int | None:
		""":return: where the cursor is within the pinned row, or None if it is not in it.

		Asked of the pinned block's own region, since a pinned row is never the active block.
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

	def _pinnedAt(self, position: int) -> tuple[Any, int] | None:
		""":return: the pinned row's region and the position in it under a cell, or None.

		:param position: which cell of the pinned row.
		"""
		if self.pinned is None or self.pinnedBlock is None or not self.pinned.positions:
			return None
		marks = self.pinned.positions[0]
		if not 0 <= position < len(marks) or marks[position] == NO_POSITION:
			return None
		return self.pinnedBlock.region, marks[position]

	def _routePinned(self, position: int) -> bool:
		"""Act on a routing key press within the pinned row.

		:param position: which cell of it.
		:return: whether the press reached content.
		"""
		found = self._pinnedAt(position)
		if found is None:
			return False
		region, at = found
		route = getattr(region, "routeTo", None)
		if route is None:
			return False
		try:
			route(at)
		except Exception:
			log.debugWarning("Could not route into the pinned row", exc_info=True)
			return False
		return True

	def _blockAt(self, position: int) -> tuple[Any, Any, int] | None:
		""":return: the block id, its region and the position in it under a cell past the
			pinned row, or None for padding, a gap, a blank row or a separator.

		:param position: a flat row major position within the band.
		"""
		source = cellSource(self.window, self.renderer.numCols, position - self.pinnedCells)
		if source is None:
			return None
		blockId, at = source
		block = self.blocks.get(blockId)
		if block is not None and getattr(block, "isDecoration", False):
			# The blank row a menu separator stands for. There is nothing there to go to.
			return None
		region = self.regionFor(blockId)
		if region is None:
			return None
		return blockId, region, at

	def regionAt(self, position: int) -> tuple[Any, int] | None:
		"""What a routing key press at a cell would reach, without acting on it.

		:param position: a flat row major position within the band.
		:return: the region and the position in it, or None where the press reaches nothing.
		"""
		if position < self.pinnedCells:
			return self._pinnedAt(position)
		found = self._blockAt(position)
		if found is None:
			return None
		_blockId, region, at = found
		return region, at

	def routeTo(self, position: int) -> bool:
		"""Act on a routing key press within the band.

		:param position: a flat row major position within the band.
		:return: whether the press reached content. Padding, gaps and blank rows do nothing,
			unlike NVDA's own buffer, which maps trailing blanks to the last content cell.
		"""
		if position < self.pinnedCells:
			return self._routePinned(position)
		found = self._blockAt(position)
		if found is None:
			return False
		blockId, region, at = found
		try:
			# The region first, because it decides what a press means from whether the reader
			# was already on the block, and activating it first would say they were.
			region.routeTo(at)
		except Exception:
			log.debugWarning(f"Could not route into {blockId}", exc_info=True)
			return False
		if self.movesCursor:
			self._setActive(blockId)
		return True

	def fillStats(self) -> tuple[int, int]:
		"""How much of the band is content rather than the space after a row's last cell.

		Every block starts a new row, so this measures what a reading unit costs on a display.

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

		For the dry run command and the log, which show the packing without a display.

		:return: one line per row of the band.
		"""
		numCols = self.renderer.numCols
		lines: list[str] = []
		if self.pinned is not None:
			held = rowText(self.pinnedBlock.region, self.pinned, 0) if self.pinnedBlock else ""
			lines.append(f"pinned: {held!r}")
		elif self.pinnedBlock is not None:
			# Tells a header that failed to draw from no header at all.
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
			# On every row of the block, since the log is read a row at a time.
			kind = " control" if block is not None and block.isControl else ""
			if block is not None and block.isBlank:
				# Tells an empty line from a row that could not be filled. The count is how many
				# lines a collapsed run stands for.
				kind += " blank" if block.collapsed < 2 else f" blank x{block.collapsed}"
			# Per row, since first and wrapped rows are indented differently.
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
				# Only the last chunk knows the block's full extent.
				rowExtent += f" of {rendered.endRow}"
			lines.append(
				f"{index}:{active}[{len(cells)}/{numCols} cells]{kind}{depth} "
				f"{rowExtent}: {rowText(region, rendered, row.rowIndex)!r}",
			)
		return lines

	def __repr__(self) -> str:
		flavour = "live" if self.live else "viewer"
		return f"<FlowController {flavour} {self.window!r}>"
