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

Both are position bound, since stock `CursorManagerRegion`s all answer `_getSelection` from
the live selection and would all render the cursor's block. Only the block the cursor is in
exposes a braille cursor, since `BrailleBuffer.update` takes the last region that reports
one. See docs/design/flow-last-region-audit.md.
"""

import time
from typing import Callable, Optional

import textInfos
from braille.regions.textInfo import CursorManagerRegion, TextInfoRegion
from logHandler import log

from . import flowForms, flowListDepth
from .flow import BlockId, ByIdentity, FetchResult, SourceBlock
from .pinnedRegions import CursorOnlyWhereTheReaderIs

DEFAULT_MAX_BLOCKS = 12
"""How many blocks one operation may read before giving up, on a band of unstated height.

See L{budgetForBand}, which is what anything with a band to fill should use instead.
"""

DEFAULT_MAX_SECONDS = 0.12
"""How long one operation may take before showing what it has.

Bounds the number of slow reads, not the worst one; see `FetchBudget.spend`. Sized with
margin over ordinary reading, so only a genuinely slow page meets it.
"""


def documentFor(obj):
	"""Which object a flow reads a document from.

	The ready tree interceptor, even while browse mode stands aside for a control, unlike
	`objectMonitor.resolveTarget`, so a field keeps its page's label and neighbours;
	`setInteractiveObject` reads its block through the edit. The exception is a multi line
	edit being written in, which is its own document; see `_isBeingEditedInside`.

	:param obj: the object the reader is on.
	:return: the document to read, or the object unchanged when there is none.
	"""
	interceptor = getattr(obj, "treeInterceptor", None)
	if interceptor is None:
		return obj
	try:
		if not interceptor.isReady:
			return obj
	except Exception:
		# An interceptor part way through being torn down. The object itself still reads.
		log.debugWarning("Could not consult a tree interceptor", exc_info=True)
		return obj
	if _isBeingEditedInside(obj, interceptor):
		return obj
	return interceptor


def _isBeingEditedInside(obj, interceptor) -> bool:
	"""Whether the reader is writing in a multi line edit inside a page.

	Then the edit is the document, since the buffer's own copy of the text does not follow
	the caret. Only a multi line edit; a single line field stays a line of the page through
	`setInteractiveObject`.

	:param obj: the object the reader is on.
	:param interceptor: the document it belongs to.
	:return: whether to read the edit rather than the page.
	"""
	try:
		if not interceptor.passThrough:
			return False
	except Exception:
		log.debugWarning("Could not tell whether browse mode has stood aside", exc_info=True)
		return False
	return flowForms.isMultilineEditable(obj)


BAND_MAX_SECONDS = 0.25
"""How long one operation over a band may take before showing what it has.

Twice the default, since a writing re-read reads the whole band through the real control,
where every position crosses the process.
"""


def budgetForBand(numRows: int) -> "FetchBudget":
	"""How much work one operation may do to fill a band of a given height.

	To stop a heavy page being walked past what the display shows, not to stop the band
	filling. Three passes over the band plus a margin, the cost of a writing re-read, the
	dearest ordinary operation.

	:param numRows: the height of the band.
	:return: a budget for it.
	"""
	return FetchBudget(maxBlocks=max(DEFAULT_MAX_BLOCKS, numRows * 3 + 8), maxSeconds=BAND_MAX_SECONDS)


class PositionMark:
	"""A block's identity where the document cannot mark a position.

	For a document whose `TextInfo.bookmark` raises, since a bare `TextInfo` has no equality
	and blocks must be recognised by comparison. Equal when `compareEndPoints` says their
	units start in the same place. Unhashable, like NVDA's bookmark; see `flow.ByIdentity`.
	"""

	__slots__ = ("info",)

	def __init__(self, info) -> None:
		"""
		:param info: the position to mark. Copied, because the caller goes on moving theirs.
		"""
		self.info = info.copy()

	def __eq__(self, other) -> bool:
		if not isinstance(other, PositionMark):
			return NotImplemented
		try:
			return self.info.compareEndPoints(other.info, "startToStart") == 0
		except Exception:
			# A document that will not compare two of its own positions cannot be recognised
			# at all, and saying so is better than saying two different lines are one.
			log.debugWarning("Could not compare two positions", exc_info=True)
			return self is other

	def __repr__(self) -> str:
		return f"<PositionMark {self.info!r}>"


class FlowRegion(CursorOnlyWhereTheReaderIs):
	"""Mixin giving a region a fixed position, and a cursor only when it is the active one.

	Mixed in ahead of the region class, as in `pinnedRegions`, so `super()._getSelection`
	reaches it.
	"""

	live = False
	isActive = False
	"""Defaults on the class, because the cursor is asked about while the region below is
	still being built. See `holdsTheCursor`."""

	listDepth: Optional[int] = None
	"""How deep in the document's lists this block's line starts, or None for none.

	Read while the line is, from the fields NVDA fetched to read it. See `_addTextWithFields`
	and `flowListDepth`. On the class so that a region never updated answers too."""

	listContinues = False
	"""Whether this block's line carries on a list item that began on an earlier line.

	Read with `listDepth`. See `flowListDepth.continuesItem`."""

	_listDepthRead = False
	"""Whether this reading has found the text `listDepth` is taken at."""

	def holdsTheCursor(self) -> bool:
		""":return: whether this is the block the reader is in.

		The active block, in a live flow only. Asked before translation, since a cursor
		cleared afterwards leaves words expanded to computer braille. See
		`pinnedRegions.CursorOnlyWhereTheReaderIs`.
		"""
		return bool(self.isActive and self.live)

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

		self.tracksLiveCursorAcrossUnits = False
		"""Whether the whole focused edit is one document block.

		The page places an edit as one block while its caret crosses many lines, so such a
		block follows the control's live reading unit.
		"""

		self.unit = None
		"""The reading unit this block is, when the source has chosen one. None keeps NVDA's.

		Set by the source, so the block's identity and text use the same unit even when it
		differs from NVDA's read by paragraph setting. See `_getReadingUnit`.
		"""

	@property
	def position(self):
		""":return: this block's own position, or None if it has never been read."""
		return self._position

	def _getReadingUnit(self):
		"""What one block of this flow is, which is the source's choice rather than NVDA's.

		Used by `update`, the line commands and `_liveCursorHere`, so all agree with the source.
		"""
		if self.unit is not None:
			return self.unit
		return super()._getReadingUnit()

	def _getSelection(self):
		"""Where this block is read from, which NVDA lays the whole block out around.

		`TextInfoRegion.update` asks once and expands the answer to the reading unit, so this
		decides both what the block says and where its cursor sits.
		"""
		if self._position is None:
			self._position = super()._getSelection().copy()
			return self._position.copy()
		live = self._liveCursorHere()
		return live if live is not None else self._position.copy()

	def _liveCursorHere(self):
		"""The reader's real cursor, when it is inside this block.

		Blocks read from the start of their unit, except the one the reader is in, which must
		follow the live caret. Only while the caret is still inside this block.

		:return: the live position, or None to read from this block's own start.
		"""
		if not (self.isActive and self.live):
			return None
		try:
			live = super()._getSelection()
			if self.tracksLiveCursorAcrossUnits:
				# Remember where the reader last was in the edit. Panning away and back to the
				# field should return there rather than to the line on which it was entered.
				self._position = live.copy()
				return live
			start = live.copy()
			start.collapse()
			start.expand(self._getReadingUnit())
			start.collapse()
			if start.compareEndPoints(self._position, "startToStart") == 0:
				return live
		except Exception:
			log.debugWarning(f"Could not read the cursor within {self!r}", exc_info=True)
		return None

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

	def _addTextWithFields(self, info, formatConfig, isSelection=False):
		"""Read one part of the line as NVDA does, and note its list depth on the way past.

		Each part `TextInfoRegion.update` reads opens with its enclosing fields, so the depth is
		counted from what NVDA receives rather than a second fetch; see `flowListDepth`.
		Wrapped on this part's own position copy, until a part with text has answered.
		"""
		original = None if self._listDepthRead else getattr(info, "getTextWithFields", None)
		if original is None:
			return super()._addTextWithFields(info, formatConfig, isSelection)

		def recording(*args, **kwargs):
			commands = original(*args, **kwargs)
			try:
				commands = list(commands)
				place = flowListDepth.placeBeforeText(commands)
				if place is not None:
					self.listDepth = flowListDepth.blockDepth(place.levels)
					self.listContinues = place.continues
					self._listDepthRead = True
			except Exception:
				log.debugWarning(f"Could not read the list depth of {self!r}", exc_info=True)
			return commands

		try:
			info.getTextWithFields = recording
		except Exception:
			# A position that will not take an attribute. The line reads as it always did,
			# flat, which is what it was before depth was read at all.
			return super()._addTextWithFields(info, formatConfig, isSelection)
		try:
			return super()._addTextWithFields(info, formatConfig, isSelection)
		finally:
			try:
				del info.getTextWithFields
			except Exception:
				pass

	def update(self) -> None:
		# Forgotten first, in case this reading finds no text.
		self.listDepth = None
		self.listContinues = False
		self._listDepthRead = False
		super().update()
		self.dirty = True
		# Set by TextInfoRegion.update for any block that is not at the start of its object,
		# which is nearly all of them. It tells NVDA's buffer to show the last region alone,
		# and a flow assembles its own rows, so it is never wanted here.
		self.hidePreviousRegions = False
		if not self.holdsTheCursor():
			# The cursor itself is refused before the translation rather than after it — see
			# `holdsTheCursor` — so this is only the drawn one, which `TextInfoRegion.update`
			# also sets outright while the reader is entering braille.
			self.brailleCursorPos = None

	def takeCursor(self) -> bool:
		"""Move the real cursor to this block, for a live flow.

		On a pan the cursor follows the window to its top block, so speech and braille agree.
		A viewer moves nothing.

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

	@property
	def cursorMark(self):
		""":return: a mark for where `takeCursor` would put the cursor, or None.

		Collapsed, as the caret will be, for comparing. See `FlowController._caretsAfterAPan`
		and `FlowController._cursorMark`.
		"""
		if self._position is None:
			return None
		try:
			here = self._position.copy()
			here.collapse()
			return here.bookmark
		except Exception:
			log.debugWarning(f"Could not mark the cursor position of {self!r}", exc_info=True)
			return None

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

	NVDA chooses the region class; only the cursor policy is ours, as in
	`pinnedRegions.pinnedCounterpart`.

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

	By reading a position, since every `NVDAObject` has `makeTextInfo` but some raise
	`NotImplementedError`.

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

	The fallback to `regionFactoryFor` when `getFocusRegions` produced nothing usable.

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

	Counts blocks and checks the clock between them, so it bounds how many slow reads happen
	but cannot shorten one. Per block latency is recorded too.
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
		self.operationStarted = 0.0
		self.operationBlocks = 0
		"""When the whole operation began, and what it had spent before its last renewal.

		Separate from `started`, which `renew` resets, since this is what the reader waited
		through. See `renew`."""

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

		Counted only when a fetch is actually refused. See `refuseIfExhausted`."""

		self.stopsBy = {"blocks": 0, "time": 0}
		"""How many fetches were refused for each reason.

		Worth separating. Running out of blocks means the allowance is too small for the
		band; running out of time means the document is slow, and a larger allowance would
		only buy a longer wait."""

		self.stopped = False
		"""Whether the operation under way has had a fetch refused."""

		self.renewals = 0
		"""How many operations were given a second allowance. See L{renew}."""

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
		self.operationStarted = self.started
		self.operationBlocks = 0
		self.active = True
		self.stopped = False

	def finish(self) -> None:
		"""End the operation, and keep what it cost.

		The numbers the budget is checked against; see `budgetForBand` and the flow cost command.
		"""
		self.active = False
		# The whole operation, renewals included. See L{renew}.
		elapsed = max(0.0, self.clock() - self.operationStarted)
		spent = self.operationBlocks + self.blocks
		self.operations += 1
		self.lastSeconds = elapsed
		self.lastBlocks = spent
		self.worstSeconds = max(self.worstSeconds, elapsed)
		self.worstBlocks = max(self.worstBlocks, spent)
		self.totalSeconds += elapsed
		if self.stopped:
			self.stops += 1

	def renew(self) -> None:
		"""Give the operation under way a fresh allowance, without ending it.

		Only for placing the band afresh after a reach spent the budget; see
		`FlowController._placeAfresh`. Called in one place, so an arrival costs at most two
		allowances. `stopped` is left set, so the count stays honest, and renewals are counted
		in `renewals`. The allowance is renewed; the operation's own measurement is not.
		"""
		self.renewals += 1
		self.operationBlocks += self.blocks
		self.blocks = 0
		self.started = self.clock()

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

		Asked when a fetch is wanted, so an operation that used its last block and wanted no more
		is not counted as stopped.

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

		For a block that must be read; the budget limits how many more follow.

		:param seconds: how long that block took.
		"""
		self.slowest = max(self.slowest, seconds)

	def spend(self, seconds: float = 0.0) -> bool:
		"""Account for one block, and say whether there is room for another.

		Charged for reading a block and again for laying it out.

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
			f"operations: {self.operations}, of which {self.stops} were cut short "
			f"and {self.renewals} were given a second allowance to place the reader",
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


LINE_BREAKS = "\n\r\v\f\x85\u2028\u2029"
"""The characters that genuinely end a line of a document.

Carriage return and line feed; the vertical tab and form feed, which is what Word puts in for
a manual line break and a page break; the Unicode next-line, line and paragraph separators.

Not the rest of what `str.splitlines` splits on (`0x1C` to `0x1E`), since Word uses `0x1E`
for a non-breaking hyphen. See `DocumentFlowSource._hasSwallowedWhatFollows`.
"""


class DocumentFlowSource:
	"""Blocks read out of a document, by reading unit.

	Cheap: a virtual buffer's text is already in NVDA's process.
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
		self.writing = interactive
		"""Whether the reader is typing into the document this source reads.

		Unlike `interactive`, about the document itself: every position can move on each
		keystroke."""
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

		self._swallowed = ByIdentity()
		"""Blocks that came back holding more than their own reading unit.

		Chromium's rich editor can briefly answer a line as everything before it, and walking on
		from such a block would show a line twice. See `_step`."""

		self._interactiveObject = None
		"""The focused edit whose caret owns the active block, or None."""

		self._interactiveFactory = None
		"""Builds a flow counterpart of that edit's own NVDA text region."""

		self._interactiveBlock: Optional[SourceBlock] = None
		"""The edit block, kept so NVDA can queue its region object for later updates."""

	def setInteractiveObject(self, obj, regionFactory: Optional[Callable] = None) -> bool:
		"""Read one document block through its focused editable object.

		The block keeps its place in the document; only its region changes, to one whose `obj`
		is the edit control, which NVDA names in caret and value events.

		:param obj: the focused edit, or None to return to ordinary document regions.
		:param regionFactory: builds the edit's flow region, chosen from NVDA's own region.
		:return: whether the active edit changed.
		"""
		same = obj is self._interactiveObject
		if not same and obj is not None and self._interactiveObject is not None:
			try:
				same = bool(obj == self._interactiveObject)
			except Exception:
				same = False
		if same:
			if self._interactiveFactory is None and regionFactory is not None:
				self._interactiveFactory = regionFactory
			return False
		self._interactiveObject = obj
		self._interactiveFactory = regionFactory
		self._interactiveBlock = None
		# Blank lines in an edit are content. Restore the document's own policy when the
		# focus leaves it rather than leaving every page permanently in editing mode.
		self.interactive = self.writing or obj is not None
		return True

	# Reading.

	def blockAtCursor(self, atObject=None) -> FetchResult:
		"""The block the cursor is in, or the one a given object starts.

		:param atObject: what the reader arrived at, read at its own place in the document,
			since the cursor can lag the focus event and, in focus mode, sits inside the
			control rather than on the line that carries its name.
		:return: the block, or an error if the document could not be read.
		"""
		self.budget.startUnlessActive()
		if self._interactiveOwnsTheCaret() and (
			atObject is None or self._sameObject(atObject, self._interactiveObject)
		):
			return self._interactiveBlockAtCursor()
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

	def blockAtPosition(self, info) -> FetchResult:
		""":return: the block holding a position, where the reader has not been, such as what a
		quick navigation key found in a pin. See `FlowController.enterAtPosition`."""
		self.budget.startUnlessActive()
		return self._blockAt(info)

	def positionOf(self, blockId: BlockId):
		""":return: a copy of where a block begins, or None if it is not one this source read."""
		start = self._positions.get(blockId.bookmark)
		if start is None:
			return None
		try:
			return start.copy()
		except Exception:
			return None

	def caretPosition(self):
		""":return: a mark for where this document's caret is, or None if it cannot be read.

		Compared by value. From the document, not the rendering. See
		`FlowController._caretIsWhereThePanLeftIt`.
		"""
		try:
			return self.obj.makeTextInfo(textInfos.POSITION_SELECTION).bookmark
		except Exception:
			log.debugWarning("Could not read the caret position", exc_info=True)
			return None

	def _interactiveBlockAtCursor(self) -> FetchResult:
		""":return: the document block read through the focused edit's own caret."""
		if self._interactiveBlock is not None:
			return FetchResult.found(self._interactiveBlock)
		obj = self._interactiveObject
		if obj is None:
			return FetchResult.failed("there is no focused edit")
		documentInfo = self._positionOf(obj)
		if documentInfo is None:
			return FetchResult.failed("the document cannot place its focused edit")
		try:
			editInfo = obj.makeTextInfo(textInfos.POSITION_SELECTION)
			editStart = self._startOfUnit(editInfo)
			factory = self._interactiveFactory or regionFactoryForObject(obj, live=True)
			region = factory(obj, editStart)
			region.tracksLiveCursorAcrossUnits = True
			region.unit = self.unit
			region.update()
		except Exception as error:
			log.debugWarning("Could not read the focused edit", exc_info=True)
			return FetchResult.failed(f"could not read the focused edit: {error!r}")
		start = self._startOfUnit(documentInfo)
		blockId = BlockId(generation=self.generation, bookmark=self._bookmark(start), unit=self.unit)
		self._positions.set(blockId.bookmark, start)
		self._interactiveBlock = SourceBlock(
			blockId=blockId,
			region=region,
			isBlank=not region.rawText.strip(),
			isControl=True,
			gapAfter=True,
			isInteractive=True,
		)
		return FetchResult.found(self._interactiveBlock)

	def _interactiveOwnsTheCaret(self) -> bool:
		"""Whether the focused edit is still the one the reader is typing in.

		Escape returns to browse mode without a focus change, giving the caret back to the
		document. Bypassed rather than cleared, since entering the field again need not be a
		focus change either.

		:return: whether to read the active block through the edit.
		"""
		obj = self._interactiveObject
		if obj is None:
			return False
		try:
			interceptor = getattr(obj, "treeInterceptor", None)
			if interceptor is None:
				return True
			return bool(getattr(interceptor, "passThrough", True))
		except Exception:
			log.debugWarning("Could not tell whether the focused edit has the caret", exc_info=True)
			return True

	def _sameObject(self, first, second) -> bool:
		""":return: whether two references name the same accessibility object."""
		if first is second:
			return True
		try:
			return bool(first == second)
		except Exception:
			return False

	def _positionOf(self, obj):
		"""Where an object starts in this document.

		:param obj: the object to find.
		:return: a position at its start, or None if this document cannot place it, and the
			caller reads from the cursor.
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

	def blockAt(self, blockId: BlockId) -> FetchResult:
		""":return: one block again, read afresh from the position it was read from.

		For live updates without moving the band. Given back only when the unit still begins
		where it did, since the bookmark is an offset and earlier text changes move it; the
		refusal is marked as a displacement (see `FetchResult.displaced`). Changes within a
		block, such as a price, move nothing.
		"""
		self.budget.startUnlessActive()
		start = self._positions.get(blockId.bookmark)
		if start is None:
			return FetchResult.failed(f"no cached position for {blockId}")
		try:
			here, found = self._unitAt(start)
		except Exception as error:
			log.debugWarning("Could not read a block again", exc_info=True)
			return FetchResult.failed(f"could not read a block again: {error!r}")
		if self._bookmark(found) != blockId.bookmark:
			return FetchResult.moved("the document moved under this block")
		return self._blockAt(here, start=found)

	def hasMoved(self, blockId: BlockId) -> bool:
		""":return: whether a block no longer begins where it was read from.

		`blockAt`'s check without building a region, asked on arrival before walking from cached
		positions. False when it cannot be told.
		"""
		start = self._positions.get(blockId.bookmark)
		if start is None:
			return False
		try:
			_here, found = self._unitAt(start)
		except Exception:
			log.debugWarning("Could not tell whether a block has moved", exc_info=True)
			return False
		return self._bookmark(found) != blockId.bookmark

	def _unitAt(self, start):
		""":return: the unit at a cached position, expanded, and where it begins now."""
		began = self.budget.clock()
		try:
			here = start.copy()
			here.expand(self.unit)
			return here, self._startOfUnit(here)
		finally:
			self.budget.observe(self.budget.clock() - began)

	def blockAfter(self, blockId: BlockId) -> FetchResult:
		"""The block following one already fetched."""
		return self._step(blockId, forward=True)

	def blockBefore(self, blockId: BlockId) -> FetchResult:
		"""The block preceding one already fetched."""
		return self._step(blockId, forward=False)

	def _step(self, blockId: BlockId, forward: bool) -> FetchResult:
		"""Walk one block in a direction, collapsing blanks and honouring the budget."""
		self.budget.startUnlessActive()
		if forward and self._swallowed.get(blockId.bookmark):
			# This block already holds the line that would come next, so fetching it would
			# put it on the display twice — which is what a comment box did for the moment
			# after each return. Ending the stream here shows blank rows below rather than a
			# repeat, and the reader's next keystroke reads the document afresh.
			return FetchResult.endOfStream("this block already holds the line that follows it")
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
			return FetchResult.endOfStream(f"the document would not move {'on' if forward else 'back'}")
		moved = self._pastTheBreak(moved, forward)
		startOfMoved = self._startOfUnit(moved)
		if not self._advanced(start, startOfMoved, forward):
			# The step went nowhere, or went back over the block it came from. A rich editor
			# does this at a position just past a break: asked to expand the unit there it
			# reaches back across it, so the block's start is the *previous* block's start
			# and the display showed the same text twice. Blank rows are the honest answer,
			# and the reader's next keystroke reads the document afresh.
			return FetchResult.endOfStream("the step went nowhere, or back over the block it came from")
		if self.writing and not self._startsWhereItLanded(moved, startOfMoved):
			return FetchResult.endOfStream("the unit found does not begin where the walk landed")
		if self.writing and self._overlapsWhereItCameFrom(startOfMoved, start, forward):
			return FetchResult.endOfStream("the unit found runs into the block it was walked from")
		if self.interactive or not self._isBlank(moved):
			return self._blockAt(moved, start=startOfMoved)
		# A blank block while reading: the run costs one row rather than a display.
		return self._walkBlanks(moved, moved, 1, forward, key)

	def _startsWhereItLanded(self, moved, start) -> bool:
		"""Whether the unit at a walked-to position starts at that position.

		A rich editor being typed into can briefly expand a unit back past the landing, which
		`_advanced` cannot catch when walking back. Asked only while writing, since each
		keystroke re-reads the band anyway.

		:param moved: where the walk landed.
		:param start: where the unit at that landing claims to begin.
		:return: whether to trust the step.
		"""
		try:
			landed = moved.copy()
			landed.collapse()
			return start.compareEndPoints(landed, "startToStart") == 0
		except Exception:
			log.debugWarning("Could not tell where a walked unit starts", exc_info=True)
			return True

	def _overlapsWhereItCameFrom(self, start, origin, forward: bool) -> bool:
		"""Whether a walked block covers text the block it came from already covers.

		Overlapping blocks draw their shared text twice. Neither `_advanced`, which compares
		starts, nor `_hasSwallowedWhatFollows`, which checks a line's text, catches it. The
		forward-reaching counterpart of `_startsWhereItLanded`, and asked only while writing,
		for the same reason.

		:param start: where the unit the walk found begins.
		:param origin: the position the walk set out from, which is the boundary the new
			block must stay on its own side of.
		:param forward: which way it was going.
		:return: whether the two overlap, and so whether to refuse the step.
		"""
		try:
			if forward:
				# The block behind, expanded, must stop at or before the new one starts.
				behind = origin.copy()
				behind.expand(self.unit)
				return behind.compareEndPoints(start, "endToStart") > 0
			# Only the backward branch reads the found block's own extent. Expanding it
			# before the branch expanded a unit the forward answer never looks at, and
			# expanding a range is a call into the document — one per step of a walk that
			# runs on every keystroke while writing.
			found = start.copy()
			found.expand(self.unit)
			return found.compareEndPoints(origin, "endToStart") > 0
		except Exception:
			log.debugWarning("Could not tell whether two blocks overlap", exc_info=True)
			return False

	def _advanced(self, origin, start, forward: bool) -> bool:
		"""Whether a walked block really is the next one along.

		The stream's invariant: forward lands after where it started, back lands before. A
		block that does not advance is already on the display.

		:param origin: where the block being walked from starts.
		:param start: where the block found starts.
		:param forward: which way the walk went.
		:return: whether to accept it. A document that will not compare its own positions is
			given the benefit of the doubt, since refusing every step would show nothing.
		"""
		try:
			order = start.compareEndPoints(origin, "startToStart")
		except Exception:
			log.debugWarning("Could not tell whether a step advanced", exc_info=True)
			return True
		return order > 0 if forward else order < 0

	def _pastTheBreak(self, info, forward: bool):
		"""Step over the empty unit a paragraph walk lands on between two paragraphs.

		In a rich editor a paragraph walk lands on the break itself. Exactly one is stepped
		over, so two returns still leave one blank paragraph. Paragraphs only; empty lines follow
		decision 6 of docs/design/spatial-reading-plan.md.

		:param info: where the walk landed.
		:param forward: which way it was going.
		:return: the position to read, which is the one given unless it was a break.
		"""
		if self.unit != textInfos.UNIT_PARAGRAPH or not self._isBlank(info):
			return info
		try:
			beyond = self._move(info, forward)
		except Exception:
			log.debugWarning("Could not step over a paragraph break", exc_info=True)
			return info
		# Nothing beyond it means the document ends here, and the reader is standing on the
		# break itself: that is a block, and the last one.
		return info if beyond is None else beyond

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

	def _blockAt(self, info, isControl: bool = False, start=None) -> FetchResult:
		""":return: a result carrying the block at a position."""
		began = self.budget.clock()
		try:
			return FetchResult.found(self._buildBlock(info, isControl=isControl, start=start))
		except Exception as error:
			log.debugWarning("Could not build a block", exc_info=True)
			return FetchResult.failed(f"could not build a block: {error!r}")
		finally:
			# Every block is timed, not only the ones walked while collapsing blanks, or a
			# slow page would be invisible in the numbers.
			self.budget.observe(self.budget.clock() - began)

	def _hasSwallowedWhatFollows(self, region) -> bool:
		"""Whether a block came back holding more than the reading unit it was asked for.

		NVDA replaces a line's terminator with a space, so a break inside a line means the
		document returned more than one. Lines only, since paragraphs may hold soft breaks. The
		break characters are listed explicitly; see `LINE_BREAKS`.

		:param region: the region the block was read through.
		:return: whether this block already contains what follows it.
		"""
		if self.unit != textInfos.UNIT_LINE:
			return False
		text = getattr(region, "rawText", "") or ""
		# A break at the very end separates this block from the next rather than sitting
		# inside it, which is what the old count of lines got right and a plain search would
		# not: "one line and a terminator" is one line.
		return any(character in LINE_BREAKS for character in text.rstrip(LINE_BREAKS))

	def _buildBlock(self, info, isControl: bool = False, start=None) -> SourceBlock:
		"""Build one block from a position, and remember where it starts.

		:param start: where the block begins, when the caller has already worked it out.
			Asking twice is not free and, worse, is not always the same answer: see
			`_startOfUnit`.
		"""
		start = self._startOfUnit(info) if start is None else start
		blockId = BlockId(generation=self.generation, bookmark=self._bookmark(start), unit=self.unit)
		self._positions.set(blockId.bookmark, start)
		region = self.regionFactory(self.obj, start)
		# Before the first update, which is what lays the block out by it. The region would
		# otherwise render by NVDA's read by paragraph setting while this source walks by its
		# own unit, and the two disagreeing showed a paragraph as its first line only.
		region.unit = self.unit
		region.update()
		if self._hasSwallowedWhatFollows(region):
			self._swallowed.set(blockId.bookmark, True)
		else:
			# The swallowing is transient — a moment after a return the editor answers
			# properly again — so a mark that outlived the text it was made from truncated
			# the document at this block forever. A clean read is the evidence it is over.
			self._swallowed.pop(blockId.bookmark)
		isBlank = not region.rawText.strip()
		return SourceBlock(
			blockId=blockId,
			region=region,
			isBlank=isBlank,
			isControl=isControl,
			gapAfter=isControl,
			depth=self._depthOfRegion(region),
			continuesItem=self._continuesItemOfRegion(region),
		)

	def currentDepth(self, block) -> Optional[int]:
		"""How deep a block sits, as its region reads now rather than when it was built.

		Asked by the controller after it reads a held block's region again, which is the one
		reading a block's depth can change on without the block being built again. See
		`FlowController._withCurrentDepth`.

		:param block: a block this source built.
		:return: its depth, by the same rules `_buildBlock` uses.
		"""
		if getattr(block, "isInteractive", False):
			# A focused edit is read through its own fields, which are not the page's.
			return None
		return self._depthOfRegion(block.region)

	def currentContinuesItem(self, block) -> bool:
		""":return: whether a block carries on a list item, as its region reads now. See `currentDepth`."""
		if getattr(block, "isInteractive", False):
			return False
		return self._continuesItemOfRegion(block.region)

	@classmethod
	def _continuesItemOfRegion(cls, region) -> bool:
		""":return: whether a document line read through this region carries on a list item.

		Only for a line with a depth: a continuation is drawn at its item's continuation indent,
		and a line with no depth has none.
		"""
		if cls._depthOfRegion(region) is None:
			return False
		return bool(getattr(region, "listContinues", False))

	@staticmethod
	def _depthOfRegion(region) -> Optional[int]:
		""":return: the depth a document line read through this region carries.

		A blank line has no depth even inside a list: it would draw the dots 7 and 8 style's
		marks on a row with nothing on it, and pull the band's baseline about for nothing.
		"""
		if not (getattr(region, "rawText", "") or "").strip():
			return None
		return getattr(region, "listDepth", None)

	# Positions.

	def _startOfUnit(self, info):
		"""Where the block holding a position begins.

		A block *is* a reading unit, so that is where it starts and what identifies it.
		Collapsing the position given instead put the cursor's own offset into the block's
		identity, and NVDA's bookmark for a virtual buffer is a pair of offsets rather than a
		line number — so the block held by the window and the block found from the cursor
		were different blocks for every character the reader passed. The window could not
		find what it was already showing, so an arrow key within a line rebuilt it; in a
		field being typed into, that is every keystroke.

		:param info: any position within the block.
		:return: a collapsed position at the start of its reading unit.
		"""
		start = info.copy()
		start.collapse()
		try:
			start.expand(self.unit)
			start.collapse()
		except Exception:
			# A position at an edge the document will not expand from. Its own offset still
			# identifies a block, and one block having an identity of its own is better than
			# no block at all.
			log.debugWarning("Could not find the start of a reading unit", exc_info=True)
			start = info.copy()
			start.collapse()
		return start

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

		Falls back to comparing the positions themselves where a document will not mark one.
		A bare `TextInfo` compares by identity, so a flow given one as a bookmark recognises
		nothing it has read. See `PositionMark`.
		"""
		try:
			return info.bookmark
		except Exception:
			log.debug("This document has no bookmarks; comparing positions instead")
			return PositionMark(info)

	def forget(self) -> None:
		"""Drop every cached position, after the document has been replaced."""
		self._positions.clear()
		self._exits.clear()
		self._resume.clear()
		self._swallowed.clear()
		self._interactiveBlock = None

	def __repr__(self) -> str:
		return f"<DocumentFlowSource {self.obj!r} by {self.unit} generation {self.generation}>"
