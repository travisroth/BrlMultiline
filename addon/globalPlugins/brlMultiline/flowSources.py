# BrlMultiline: finding the blocks a flow shows.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where a flow's content comes from.

A source answers three questions and nothing else: the block at the cursor, the block
after this one, and the block before it. Each answer is a `FetchResult`, because a source
may also have to say that there is no more, that it ran out of budget, or that it failed —
and telling those apart is what stops a slow page reading as a finished one.

Two flavours, and the difference is only whether a move is written back:

1. **Live**, for a flow that is the focus segment. Panning and routing move the browse mode
	cursor, which is what NVDA's braille panning already does, and is what keeps speech and
	braille together and leaves routing able to activate a link.
2. **Viewer**, for a band the reader is not working in. It keeps a position of its own and
	moves nothing outside itself.

Both are position bound, which is not optional. Every `CursorManagerRegion` over one
document answers `_getSelection` from the same live `obj.selection`, so a run of stock
regions would all render the cursor's block rather than the blocks around it. The regions
here answer from the position they were built with.

Only the block the cursor is in exposes a braille cursor. `BrailleBuffer.update` assigns
the buffer's cursor from every region that reports one, last wins, so a collapsed position
in each block would otherwise put the cursor in whichever block happened to be last. See
[flow-last-region-audit.md](../../../docs/design/flow-last-region-audit.md).
"""

import time
from typing import Callable, Optional

import textInfos
from braille.regions.textInfo import CursorManagerRegion, TextInfoRegion
from logHandler import log

from . import flowForms
from .flow import BlockId, ByIdentity, FetchResult, SourceBlock

DEFAULT_MAX_BLOCKS = 12
"""How many blocks one operation may read before giving up, on a band of unstated height.

See L{budgetForBand}, which is what anything with a band to fill should use instead.
"""

DEFAULT_MAX_SECONDS = 0.12
"""How long one operation may take before showing what it has.

A budget between blocks cannot make a single slow read fast — see `FetchBudget.spend` — so
this bounds the number of slow reads, not the worst one.

Was fifty milliseconds, which was measured against nothing: the hardware run that produced
a number put the slowest block on a Chromium buffer at two milliseconds, so eight rows of a
Monarch cost around twenty. That left almost no margin, and an arrival that did any extra
reading — a control's prompt, a run of blank lines — spent it. A budget the ordinary case
trips over is not a safety limit, it is a fault.
"""


def documentFor(obj):
	"""Which object a flow reads a document from.

	The tree interceptor when there is a ready one, whether or not browse mode is presenting
	it at this moment. That last part is the whole difference from
	`objectMonitor.resolveTarget`, and it is deliberate.

	`resolveTarget` answers a different question — what to pin — and gives back the object
	itself when browse mode has stood aside for a control the reader has entered. Reading a
	flow that way builds it over one control's own text, so tabbing into a single line edit
	produces a flow of one line, and an empty one produces a flow of nothing: the blank row
	where a tabbed-to field should have been. A form field the reader has entered inside a
	page is still that page, and the page is what has context to show.

	:param obj: the object the reader is on.
	:return: the document to read, or the object unchanged when there is none.
	"""
	interceptor = getattr(obj, "treeInterceptor", None)
	if interceptor is None:
		return obj
	try:
		if interceptor.isReady:
			return interceptor
	except Exception:
		# An interceptor part way through being torn down. The object itself still reads.
		log.debugWarning("Could not consult a tree interceptor", exc_info=True)
	return obj


def budgetForBand(numRows: int) -> "FetchBudget":
	"""How much work one operation may do to fill a band of a given height.

	The budget exists to stop a heavy page being walked further than the display can show,
	*not* to stop the display being filled. Filling a band of eight rows costs at least
	eight blocks, and more where blank lines are collapsed or a prompt is read above a
	control, so a limit near the band's own height is one the reader meets constantly — and
	meeting it looks like a display of markers saying there is more we have not read.

	Twice the band plus a margin. Reaching that means something is genuinely wrong with the
	page rather than tall with the display.

	:param numRows: the height of the band.
	:return: a budget for it.
	"""
	return FetchBudget(maxBlocks=max(DEFAULT_MAX_BLOCKS, numRows * 2 + 6))


class FlowRegion:
	"""Mixin giving a region a fixed position, and a cursor only when it is the active one.

	Mixed in ahead of the region class whose cursor policy it adjusts, so that
	`super()._getSelection` still reaches the right one. The same arrangement
	`pinnedRegions` uses, for the same reason.
	"""

	def __init__(self, obj, info=None, live: bool = False) -> None:
		"""
		:param obj: the object or tree interceptor to read from.
		:param info: the position this block starts at. None adopts the object's own.
		:param live: whether moving this region moves the real cursor.
		"""
		super().__init__(obj)
		self._position = info.copy() if info is not None else None
		self.live = live
		self.isActive = False
		"""Whether this is the block the cursor is in. Only the active block shows one."""

		self.dirty = False
		"""Set when this region was re-read, so the band knows to lay it out again.

		NVDA re-reads the region it has queued and then updates the buffer, which is where
		a flow hears that the block under the cursor has changed — by braille input, by an
		application rewriting it, or by the caret moving within it."""

		self.onMoved = None
		"""Called when something moved this region's position, with the region.

		NVDA's own commands reach the last region directly: the line commands move it a
		reading unit, and routing moves it to a cell. In a live flow those move the browse
		mode cursor, and the band has to follow. Set by the controller."""

	@property
	def position(self):
		""":return: this block's own position, or None if it has never been read."""
		return self._position

	def _getSelection(self):
		if self._position is None:
			self._position = super()._getSelection().copy()
		return self._position.copy()

	def _setCursor(self, info) -> None:
		self._position = info.copy()
		if self.live:
			# A live flow is the focus segment, so this is the browse mode cursor moving,
			# which is what keeps routing able to activate what it lands on.
			super()._setCursor(info)
		if self.onMoved is None:
			return
		try:
			self.onMoved(self)
		except Exception:
			log.debugWarning(f"Could not follow {self!r} after it moved", exc_info=True)

	def update(self) -> None:
		super().update()
		self.dirty = True
		# Set by TextInfoRegion.update for any block that is not at the start of its object,
		# which is nearly all of them. It tells NVDA's buffer to show the last region alone,
		# and a flow assembles its own rows, so it is never wanted here.
		self.hidePreviousRegions = False
		if not (self.isActive and self.live):
			# Only the active block of a live flow shows a cursor. A viewer band shows none
			# at all: there is one cursor on the display and it belongs to the focus.
			self.cursorPos = None
			self.brailleCursorPos = None

	def takeCursor(self) -> bool:
		"""Move the real cursor to this block, for a live flow.

		What panning does in a live flow: the browse mode cursor follows the window to the
		top block of what is now shown, so speech and braille agree and the arrow keys
		carry on from what is under the reader's hands. A viewer moves nothing.

		:return: whether the cursor was moved.
		"""
		if not self.live or self._position is None:
			return False
		try:
			# Deliberately the base class's, not this one's: the controller is moving the
			# cursor to a block it has already chosen, so telling it that the region moved
			# would send it round the same decision again.
			super()._setCursor(self._position.copy())
		except Exception:
			log.debugWarning(f"Could not move the cursor to {self!r}", exc_info=True)
			return False
		return True

	def routeTo(self, braillePos: int) -> None:
		if self.live:
			super().routeTo(braillePos)
			return
		# A viewer must not activate anything. NVDA's routing activates the position when
		# the key falls where the region already thinks its cursor is, and a private
		# position is collapsed, so it always thinks it has one: inheriting this would let a
		# routing press follow a link in a document the reader is only watching.
		try:
			dest = self.getTextInfoForBraillePos(braillePos)
		except (LookupError, NotImplementedError, RuntimeError):
			log.debugWarning(f"Could not route within {self!r}", exc_info=True)
			return
		self._setCursor(dest)

	def __repr__(self) -> str:
		flavour = "live" if self.live else "viewer"
		return f"<{type(self).__name__} {flavour} {getattr(self, 'rawText', '')!r}>"


class FlowTextInfoRegion(FlowRegion, TextInfoRegion):
	"""A flow block over an object with navigable text: an edit field, a terminal."""


class FlowCursorManagerRegion(FlowRegion, CursorManagerRegion):
	"""A flow block over a browse mode document or anything else cursor managed."""


def regionFactoryFor(template, live: bool) -> Callable:
	"""Choose the region class for a document, from one NVDA built for it.

	Taken from NVDA's own decision rather than made here, so that which kind of region an
	object wants stays NVDA's judgement and only the cursor policy is ours. The same
	reasoning as `pinnedRegions.pinnedCounterpart`.

	:param template: a region from `getFocusRegions` for the object in question.
	:param live: whether the flow moves the real cursor.
	:return: a callable taking the object and a position, returning a region.
	:raises TypeError: if the template is not a text region, so has no lines to flow.
	"""
	# Ordered subclass first: CursorManagerRegion is a TextInfoRegion.
	if isinstance(template, CursorManagerRegion):
		built = FlowCursorManagerRegion
	elif isinstance(template, TextInfoRegion):
		built = FlowTextInfoRegion
	else:
		raise TypeError(f"{template!r} reads no text, so it cannot be flowed")

	def factory(obj, info):
		return built(obj, info, live=live)

	return factory


def _hasText(obj) -> bool:
	"""Whether an object has text to read.

	Asked by reading a position out of it rather than by looking for `makeTextInfo`, which
	every `NVDAObject` has: the base raises `NotImplementedError` for the ones with no text,
	so the presence of the method says nothing. A button would otherwise be flowed, and
	would fail one step further on with a worse account of why.

	:param obj: the object to test.
	:return: whether it can be read.
	"""
	try:
		obj.makeTextInfo(textInfos.POSITION_FIRST)
	except (AttributeError, NotImplementedError, RuntimeError):
		return False
	except Exception:
		# Something else went wrong reading it, which is not the same as having no text.
		log.debugWarning(f"Could not tell whether {obj!r} has text", exc_info=True)
		return False
	return True


def regionFactoryForObject(obj, live: bool) -> Callable:
	"""Choose the region class from the object itself, when NVDA has offered no template.

	`regionFactoryFor` is preferred, because it keeps the choice NVDA's. This is the
	fallback for the case where `getFocusRegions` produced nothing usable, which should not
	happen for a browse mode document and is worth reading rather than giving up over.

	:param obj: the object or tree interceptor to read.
	:param live: whether the flow moves the real cursor.
	:return: a callable taking the object and a position, returning a region.
	:raises TypeError: if the object has no text to read.
	"""
	from cursorManager import CursorManager

	if isinstance(obj, CursorManager):
		built = FlowCursorManagerRegion
	elif _hasText(obj):
		built = FlowTextInfoRegion
	else:
		raise TypeError(f"{obj!r} reads no text, so it cannot be flowed")

	def factory(objArg, info):
		return built(objArg, info, live=live)

	return factory


class FetchBudget:
	"""How much work one fetch may do before showing what it has.

	Deliberately crude. It counts blocks and checks the clock between them, which bounds
	how many slow reads happen in a row but cannot shorten one slow read: a single
	`Region.update` on a heavy page is as long as it is. That is why per block latency is
	recorded from this milestone rather than only measured at the end.
	"""

	def __init__(
		self,
		maxBlocks: int = DEFAULT_MAX_BLOCKS,
		maxSeconds: float = DEFAULT_MAX_SECONDS,
		clock: Callable[[], float] = time.perf_counter,
	) -> None:
		"""
		:param maxBlocks: how many blocks one fetch may walk.
		:param maxSeconds: how long one fetch may take.
		:param clock: the clock to measure with, so tests need not sleep.
		"""
		self.maxBlocks = maxBlocks
		self.maxSeconds = maxSeconds
		self.clock = clock
		self.blocks = 0
		self.started = 0.0
		self.active = False
		"""Whether an operation is under way.

		A budget belongs to a whole operation — filling the band, panning it, arriving in a
		document — not to one call. Started per call it would reset on each of them and
		bound nothing: a four row band could read four blocks under a budget of one."""

		self.slowest = 0.0
		"""The longest a single block took, in seconds, since this budget was made.

		Kept across fetches rather than reset with each one, because the question it answers
		is whether this document has slow blocks in it at all."""

		self.operations = 0
		"""How many operations have finished: arrivals, fills, pans, cursor moves."""

		self.stops = 0
		"""How many operations were cut short, and so showed the reader less than the band
		could hold. The number that says whether the budget is sized right.

		Counted from a fetch actually refused, never from the allowance being used up. An
		operation whose last permitted block filled the display stopped nothing: there was
		nothing more it wanted."""

		self.stopsBy = {"blocks": 0, "time": 0}
		"""How many fetches were refused for each reason.

		Worth separating. Running out of blocks means the allowance is too small for the
		band; running out of time means the document is slow, and a larger allowance would
		only buy a longer wait."""

		self.stopped = False
		"""Whether the operation under way has had a fetch refused."""

		self.lastSeconds = 0.0
		self.lastBlocks = 0
		"""What the most recent operation cost, for a report taken straight after one."""

		self.worstSeconds = 0.0
		self.worstBlocks = 0
		self.totalSeconds = 0.0
		"""The worst and the sum, since a single slow arrival and a steadily slow document
		are different problems with different answers."""

	def start(self) -> None:
		"""Begin an operation, whatever was under way before."""
		self.blocks = 0
		self.started = self.clock()
		self.active = True
		self.stopped = False

	def finish(self) -> None:
		"""End the operation, and keep what it cost.

		The numbers kept here are the whole of the measurement this design promised itself
		when it decided to bound work rather than to trust it. A budget that is never
		compared with what reading actually costs is a number somebody guessed, and this
		project has now twice had a guessed number turn out to be the fault — see
		`budgetForBand`. What is wanted from a reader is not "it felt slow" but "the slowest
		block was two milliseconds and nine operations in a hundred stopped early".
		"""
		self.active = False
		elapsed = max(0.0, self.clock() - self.started)
		self.operations += 1
		self.lastSeconds = elapsed
		self.lastBlocks = self.blocks
		self.worstSeconds = max(self.worstSeconds, elapsed)
		self.worstBlocks = max(self.worstBlocks, self.blocks)
		self.totalSeconds += elapsed
		if self.stopped:
			self.stops += 1

	def startUnlessActive(self) -> None:
		"""Begin an operation only if the caller above has not already begun one."""
		if not self.active:
			self.start()

	@property
	def exhausted(self) -> bool:
		""":return: whether this operation has used everything it was given."""
		if self.blocks >= self.maxBlocks:
			return True
		return (self.clock() - self.started) >= self.maxSeconds

	def refuseIfExhausted(self) -> bool:
		"""Turn a wanted fetch away, if there is nothing left to make it with.

		Asked at the moment a fetch is wanted rather than at the end of the operation. The
		difference is the whole worth of the count: an operation that spent its last block
		filling the last row of the band wanted nothing more and stopped nothing, and
		counting it as a stop would have the reader chasing an allowance that fits.

		:return: whether the fetch is refused.
		"""
		if not self.exhausted:
			return False
		reason = "blocks" if self.blocks >= self.maxBlocks else "time"
		self.stopsBy[reason] = self.stopsBy.get(reason, 0) + 1
		self.stopped = True
		return True

	def observe(self, seconds: float) -> None:
		"""Record how long a block took without spending any of the budget on it.

		Reading one block is not optional, so it is measured rather than charged for. What
		the budget limits is how many more are read after it.

		:param seconds: how long that block took.
		"""
		self.slowest = max(self.slowest, seconds)

	def spend(self, seconds: float = 0.0) -> bool:
		"""Account for one block, and say whether there is room for another.

		Charged for reading a block and again for laying it out, since both are work the
		reader waits through and either can be the slow one.

		:param seconds: how long that block took, for the latency record.
		:return: whether the budget allows going on.
		"""
		self.blocks += 1
		self.slowest = max(self.slowest, seconds)
		return not self.exhausted

	def describe(self) -> list[str]:
		"""What this budget has seen, in words.

		:return: one line per fact, for the log and for a command to read out.
		"""
		average = (self.totalSeconds / self.operations) if self.operations else 0.0
		return [
			f"operations: {self.operations}, of which {self.stops} were cut short",
			f"fetches refused: {self.stopsBy.get('blocks', 0)} out of blocks, "
			f"{self.stopsBy.get('time', 0)} out of time",
			f"allowance: {self.maxBlocks} blocks or {self.maxSeconds * 1000:.0f} ms each",
			f"slowest single block: {self.slowest * 1000:.2f} ms",
			f"slowest operation: {self.worstSeconds * 1000:.2f} ms over {self.worstBlocks} blocks",
			f"average operation: {average * 1000:.2f} ms",
			f"most recent: {self.lastSeconds * 1000:.2f} ms over {self.lastBlocks} blocks",
		]

	def __repr__(self) -> str:
		return (
			f"<FetchBudget {self.maxBlocks} blocks, {self.operations} operations, "
			f"{self.stops} stopped early, slowest block {self.slowest * 1000:.2f} ms>"
		)


class DocumentFlowSource:
	"""Blocks read out of a document, by reading unit.

	Cheap by construction: moving a `TextInfo` inside a browse mode document does not cross
	to the application, because the virtual buffer's text is already in NVDA's process.
	Walking `NVDAObject` relations is the expensive kind of reading and is not done here.
	"""

	def __init__(
		self,
		obj,
		regionFactory: Callable,
		unit: str = textInfos.UNIT_LINE,
		generation: int = 0,
		interactive: bool = False,
		budget: Optional[FetchBudget] = None,
	) -> None:
		"""
		:param obj: the object or tree interceptor to read.
		:param regionFactory: builds a region for one block. See `regionFactoryFor`.
		:param unit: the reading unit, following NVDA's read by paragraph setting.
		:param generation: bumped by the caller when the document is replaced, so that a
			bookmark from the old document can never match one from the new.
		:param interactive: whether the reader is working inside this content rather than
			reading it. Blank lines are the document when writing and are layout when
			reading, so this decides whether a run of them collapses.
		:param budget: how much work a fetch may do. One is made if none is given.
		"""
		self.obj = obj
		self.regionFactory = regionFactory
		self.unit = unit
		self.generation = generation
		self.interactive = interactive
		self.budget = budget if budget is not None else FetchBudget()
		self._positions = ByIdentity()
		"""The position each block starts at, by bookmark.

		Not a dictionary: a bookmark compares but does not hash. See `flow.ByIdentity`."""

		self._exits = ByIdentity()
		"""Where to carry on from when leaving a block, by block and direction.

		The same as the block's own position for an ordinary block, and the far end of the
		run for a collapsed set of blank lines."""

		self._resume = ByIdentity()
		"""Where a deferred walk through blank lines got to, by block and direction."""

	# Reading.

	def blockAtCursor(self, atObject=None) -> FetchResult:
		"""The block the cursor is in, or the one a given object starts.

		:param atObject: what the reader arrived at. Its own place in the document is read
			rather than the cursor's, because a focus event can land before the browse mode
			cursor has caught up — and because tabbing into an edit field or a combo box
			drops browse mode into focus mode and puts the cursor *inside* the control,
			where the line is the value being edited and the control's name, role and state
			are an enclosing field rather than this line's own. Starting at the object gives
			the line browse mode would show for it, which is the one carrying the name.

			Any focus target the document can place: a link is read at its own place for the
			first of those reasons, though none of the second applies to it.
		:return: the block, or an error if the document could not be read.
		"""
		self.budget.startUnlessActive()
		info = self._positionOf(atObject) if atObject is not None else None
		# Where the reader arrived and what kind of thing it is are two questions. A block is
		# a control's when it holds a control *and* was read at that control's own place: a
		# link starts a block like any other line, and a control this document cannot place
		# falls back to the cursor, where what is there is whatever the reader was last on
		# and has no prompt of its own to show.
		isControl = info is not None and flowForms.isControlObject(atObject)
		if info is None:
			try:
				info = self.obj.makeTextInfo(textInfos.POSITION_SELECTION)
			except Exception as error:
				log.debugWarning("Could not read the cursor position", exc_info=True)
				return FetchResult.failed(f"no cursor position: {error!r}")
		# A control asks for a blank row after it, so that a one row answer is separated from
		# the next prompt. Declared by the block, never produced by packing.
		return self._blockAt(info, isControl=isControl)

	def _positionOf(self, obj):
		"""Where an object starts in this document.

		:param obj: the object to find.
		:return: a position at its start, or None if this document cannot place it — which is
			the ordinary answer for anything that is not in it, and leaves the caller reading
			from the cursor as it always has.
		"""
		try:
			info = self.obj.makeTextInfo(obj)
		except (LookupError, NotImplementedError, RuntimeError, TypeError, ValueError):
			log.debug(f"This document cannot place {obj!r}")
			return None
		except Exception:
			log.debugWarning(f"Could not place {obj!r} in the document", exc_info=True)
			return None
		info.collapse()
		return info

	def blockAfter(self, blockId: BlockId) -> FetchResult:
		"""The block following one already fetched."""
		return self._step(blockId, forward=True)

	def blockBefore(self, blockId: BlockId) -> FetchResult:
		"""The block preceding one already fetched."""
		return self._step(blockId, forward=False)

	def _step(self, blockId: BlockId, forward: bool) -> FetchResult:
		"""Walk one block in a direction, collapsing blanks and honouring the budget."""
		self.budget.startUnlessActive()
		key = (blockId.bookmark, forward)
		pending = self._resume.pop(key)
		if pending is not None:
			# A previous fetch stopped part way through a run of blank lines. Carry on from
			# where it got to, keeping the run's start and count, so that a second pan makes
			# progress rather than deferring at the same place forever.
			info, runStart, count = pending
			return self._walkBlanks(info, runStart, count, forward, key)
		start = self._exits.get(key)
		if start is None:
			start = self._positions.get(blockId.bookmark)
		if start is None:
			return FetchResult.failed(f"no cached position for {blockId}")
		try:
			moved = self._move(start, forward)
		except Exception as error:
			log.debugWarning(f"Could not move {'forward' if forward else 'back'} a block", exc_info=True)
			return FetchResult.failed(f"could not move: {error!r}")
		if moved is None:
			return FetchResult.endOfStream()
		if self.interactive or not self._isBlank(moved):
			return self._blockAt(moved)
		# A blank block while reading: the run costs one row rather than a display.
		return self._walkBlanks(moved, moved, 1, forward, key)

	def _walkBlanks(self, info, runStart, count: int, forward: bool, key) -> FetchResult:
		"""Walk to the end of a run of blank blocks and produce the one block standing for it.

		:param info: the last blank reached so far.
		:param runStart: the first blank of the run, in the direction being walked.
		:param count: how many blanks have been counted.
		:param forward: the direction being walked.
		:param key: what to file a deferred fetch under, so it can be resumed.
		:return: the collapsed block, or a deferred result if the budget ran out.
		"""
		while True:
			began = self.budget.clock()
			try:
				moved = self._move(info, forward)
			except Exception:
				log.debugWarning("Could not walk a run of blank lines", exc_info=True)
				moved = None
			if moved is None or not self._isBlank(moved):
				break
			info = moved
			count += 1
			if not self.budget.spend(self.budget.clock() - began):
				self._resume.set(key, (info, runStart, count))
				return FetchResult.deferred(resume=info)
		# The run keeps the identity of its first member in reading order, which is the one
		# that owns the visible row whichever way it was walked into.
		earliest, latest = (runStart, info) if forward else (info, runStart)
		found = self._buildBlock(earliest)
		block = SourceBlock(
			blockId=found.blockId,
			region=found.region,
			isBlank=True,
			collapsed=count,
		)
		# Stepping out of a collapsed run must leave the whole run behind, or the next step
		# walks back into it and the reader never gets past the blank rows.
		self._exits.set((block.blockId.bookmark, True), latest)
		self._exits.set((block.blockId.bookmark, False), earliest)
		return FetchResult.found(block)

	def _blockAt(self, info, isControl: bool = False) -> FetchResult:
		""":return: a result carrying the block at a position."""
		began = self.budget.clock()
		try:
			return FetchResult.found(self._buildBlock(info, isControl=isControl))
		except Exception as error:
			log.debugWarning("Could not build a block", exc_info=True)
			return FetchResult.failed(f"could not build a block: {error!r}")
		finally:
			# Every block is timed, not only the ones walked while collapsing blanks, or a
			# slow page would be invisible in the numbers.
			self.budget.observe(self.budget.clock() - began)

	def _buildBlock(self, info, isControl: bool = False) -> SourceBlock:
		"""Build one block from a position, and remember where it starts."""
		start = info.copy()
		start.collapse()
		blockId = BlockId(generation=self.generation, bookmark=self._bookmark(start), unit=self.unit)
		self._positions.set(blockId.bookmark, start)
		region = self.regionFactory(self.obj, start)
		region.update()
		return SourceBlock(
			blockId=blockId,
			region=region,
			isBlank=not region.rawText.strip(),
			isControl=isControl,
			gapAfter=isControl,
		)

	# Positions.

	def _move(self, info, forward: bool):
		"""Move one reading unit.

		:return: the new position, or None if the document ends there. A partial move is
			no move: `TextInfo.move` stops at the edge of the document and reports how far
			it got, and a block that stopped short would repeat the first or last one.
		"""
		dest = info.copy()
		dest.collapse(end=not forward)
		if dest.move(self.unit, 1 if forward else -1) != (1 if forward else -1):
			return None
		return dest

	def _isBlank(self, info) -> bool:
		""":return: whether the unit at a position is empty."""
		probe = info.copy()
		probe.expand(self.unit)
		return not probe.text.strip()

	def _bookmark(self, info):
		""":return: something comparable that finds this position again.

		Falls back to the position object itself where bookmarks are not implemented, which
		still compares usefully within one document and simply makes recovery re-enter at
		the cursor more often.
		"""
		try:
			return info.bookmark
		except (AttributeError, NotImplementedError):
			return info

	def forget(self) -> None:
		"""Drop every cached position, after the document has been replaced."""
		self._positions.clear()
		self._exits.clear()
		self._resume.clear()

	def __repr__(self) -> str:
		return f"<DocumentFlowSource {self.obj!r} by {self.unit} generation {self.generation}>"
