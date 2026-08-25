# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the command that lays a table out on the band, and takes the layout away.

What is tested here is the *state*, which is the part a reader cannot see and the part that
goes wrong: a layout that outlives the table it was made for is a layout of something that is
not there, and the reader has no way to tell that is what they are feeling. Easy Table
Navigator clears its key bindings on every focus change for exactly this reason, and this is
the same discipline in the same place.
"""

import unittest
from types import SimpleNamespace

from ._stubs import (
	CONFIG,
	FakeNavigatorObject,
	callLaterQueue,
	FakeTableDocument,
	NoTableDocument,
	installStubs,
	resetConfig,
	spokenMessages,
)

installStubs()

from brlMultiline.flowBand import LIVE_SETTLE_MILLIS  # noqa: E402
from brlMultiline.flowTableSource import TableFlowSource  # noqa: E402

from .test_flowSegment import FakeHandler, FakePlugin, containerWithBand  # noqa: E402

ROWS = 8
COLS = 32

WATCHLIST = [
	["Symbol", "Last", "Change", "%Chg"],
	["AAPL", "182.50", "+1.25", "+0.7%"],
	["F", "9.10", "-0.05", "-0.5%"],
	["BRK.B", "402.15", "+3.40", "+0.9%"],
]


class TableBandTestCase(unittest.TestCase):
	"""The harness. No tests of its own; the classes below are the tests."""

	def setUp(self):
		import api
		import braille

		from brlMultiline.flowBand import FlowBand

		resetConfig()
		self.addCleanup(resetConfig)
		CONFIG["flowEnabled"] = True
		self.handler = FakeHandler(ROWS, COLS)
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = self.handler
		self.container = containerWithBand(self.handler, numRows=ROWS, numCols=COLS)
		self.handler.mainBuffer = self.handler.buffer = self.container
		self.band = FlowBand(FakePlugin(self.container))
		self.api = api
		self.addCleanup(setattr, api, "getFocusObject", api.getFocusObject)

	def _inTable(self, rows=None, tableID=1, row=2, col=1, lines=None, caretIndex=0):
		document = FakeTableDocument(
			[list(line) for line in (rows or WATCHLIST)],
			tableID=tableID,
			row=row,
			col=col,
			lines=lines,
		)
		document.caretIndex = caretIndex
		obj = FakeNavigatorObject("a page", treeInterceptor=document)
		self.api.getFocusObject = lambda: obj
		self.band._follow()
		return obj, document

	def _elsewhere(self):
		"""A different page altogether, which is the easy case."""
		obj = FakeNavigatorObject("a page", treeInterceptor=NoTableDocument())
		self.api.getFocusObject = lambda: obj
		return obj

	def _readingADocument(self):
		from brlMultiline.flowSources import DocumentFlowSource

		return isinstance(getattr(self.band.controller, "source", None), DocumentFlowSource)

	def _readingATable(self):
		source = getattr(self.band.controller, "source", None)
		return isinstance(source, TableFlowSource)


class TestTheCommandItself(TableBandTestCase):
	"""The script, run rather than reasoned about.

	Everything below this class tests the band. Nothing tested the twenty lines between the
	reader's key press and the band, and those twenty lines shipped twice with a fault in
	them: once with a decorator that belonged to the command below it, and once calling a
	property as though it were a method. Both are the kind of mistake that reading the code
	does not catch and running it does.
	"""

	def _press(self):
		"""Run the command the way NVDA does, on a plugin that has this band."""
		from brlMultiline import GlobalPlugin

		spokenMessages.clear()
		holder = SimpleNamespace(flowBand=self.band)
		GlobalPlugin.script_flowTableColumns(holder, None)
		return list(spokenMessages)

	def test_pressingItInATableLaysItOut(self):
		self._inTable()
		said = self._press()
		self.assertTrue(self._readingATable())
		self.assertTrue(any("columns" in message for message in said))

	def test_pressingItAgainTakesTheLayoutAway(self):
		self._inTable()
		self._press()
		said = self._press()
		self.assertFalse(self._readingATable())
		self.assertTrue(any("off" in message for message in said))

	def test_pressingItOutsideATableSaysSo(self):
		self._elsewhere()
		said = self._press()
		self.assertIn("Not in a table", said)

	def test_pressingItWithNoBandSaysSo(self):
		from brlMultiline import GlobalPlugin

		spokenMessages.clear()
		GlobalPlugin.script_flowTableColumns(SimpleNamespace(flowBand=None), None)
		self.assertTrue(spokenMessages)

	def test_theBandNeedsSomewhereToDrawRatherThanAFlowAlreadyOnIt(self):
		"""A band with no flow on it is the case the reader wants this for: a table in a
		document whose kind of content the flow settings have turned off is still a table
		they can ask for by name."""
		self._inTable()
		self.band.controller = None
		self.assertFalse(self.band.isShowing)
		self.assertTrue(self.band.isClaimed)
		self._press()
		self.assertTrue(self._readingATable())


class TestTurningItOn(TableBandTestCase):
	def test_aTableIsLaidOutWhenAskedFor(self):
		self._inTable()
		self.assertTrue(self.band.layOutTable())
		self.assertTrue(self._readingATable())

	def test_theColumnsAreThePlanTheTableAskedFor(self):
		self._inTable()
		self.band.layOutTable()
		plan = self.band.controller.renderer.columnPlan
		self.assertEqual([column.index for column in plan.columns], [1, 2, 3, 4])

	def test_theRowTheReaderIsOnIsTheFirstBlock(self):
		self._inTable(row=3)
		self.band.layOutTable()
		self.assertEqual(self.band.controller.window.blocks[0].blockId.bookmark, 3)

	def test_askingOutsideATableDoesNothing(self):
		self._elsewhere()
		self.assertFalse(self.band.layOutTable())
		self.assertFalse(self._readingATable())

	def test_askingOutsideATableLeavesNoRequestBehind(self):
		"""A request that survived a refusal would lay out the next table the reader walked
		into, which they never asked for."""
		self._elsewhere()
		self.band.layOutTable()
		self.assertIsNone(self.band.tableWanted)


class TestTurningItOff(TableBandTestCase):
	def test_theLayoutIsGivenBack(self):
		self._inTable()
		self.band.layOutTable()
		self.assertTrue(self.band.clearTable())
		self.assertFalse(self._readingATable())

	def test_clearingWhenNothingIsLaidOutSaysSo(self):
		self._inTable()
		self.assertFalse(self.band.clearTable())


class TestNvdaCanTellWhoTheRowBelongsTo(TableBandTestCase):
	"""`BrailleHandler.handleCaretMove` looks at the last region in the buffer and does
	nothing at all unless its `obj` equals the object whose caret moved.

	That one comparison is the whole of how a flow hears about a caret. A row that did not
	know its document was a row NVDA never queued: the caret moved, no redraw followed, and
	with no redraw there was no `recheck` either — so the band could not notice the reader
	had walked out of the table, and sat on the first table it was given.

	Two faults made it invisible. Nothing asserted the region's `obj`, and `attach` took the
	object being read as a parameter, documented as being for exactly this, and did nothing
	with it. Reading the code, the question looked answered.
	"""

	def test_theRowKnowsWhichDocumentItCameFrom(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		self.assertIs(self.band.controller.activeRegion().obj, document)

	def test_aCaretMoveInThatDocumentWouldReachTheRow(self):
		"""NVDA's condition, written out."""
		obj, document = self._inTable()
		self.band.layOutTable()
		segment = self.band.segment()
		segment.update()
		self.assertTrue(segment.regions)
		self.assertEqual(segment.regions[-1].obj, document)

	def test_everyRowOfTheTableKnowsIt(self):
		"""Not only the one the reader arrived on: any of them can become the active block
		by panning, and the active block is the one NVDA is shown."""
		obj, document = self._inTable()
		self.band.layOutTable()
		for block in self.band.controller.window.blocks:
			region = self.band.controller.regionFor(block.blockId)
			self.assertIs(region.obj, document)


class TestLeavingTheTableWithoutLeavingThePage(TableBandTestCase):
	"""The case that actually happens, and the one the fixtures could not express.

	A table is part of a page, not a place the reader goes instead of one. The caret walks
	out of it and the document does not change — so every "is this the same document" test
	the band has says yes, because a table flow's source *is* reading that document. The band
	took the same-document path, called `arriveAt` on the table controller, and kept the
	columns for the rest of the page.

	On hardware that was a band stuck on the first table it was given and a toggle that
	appeared to do nothing. Every test written before this one modelled leaving a table as
	arriving at a different document, which is the one thing that never happens, so they all
	passed.
	"""

	def test_theCaretLeavingGivesTheOrdinaryReadingBack(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		document.inTable = False
		self.band.recheck()
		self.assertFalse(self._readingATable())
		self.assertTrue(self._readingADocument())

	def test_theRequestGoesWithIt(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		document.inTable = False
		self.band.recheck()
		self.assertIsNone(self.band.tableWanted)

	def test_turningItOffGivesTheOrdinaryReadingBack(self):
		"""The reader is still in the table and has asked for it to stop, which is the same
		problem seen from the other side."""
		self._inTable()
		self.band.layOutTable()
		self.band.clearTable()
		self.assertFalse(self._readingATable())
		self.assertTrue(self._readingADocument())

	def test_theCommandTurnsItOffToo(self):
		self._inTable()
		self.band.layOutTable()
		self.band.controller.renderer.columnPlan
		self.assertTrue(self.band.tableWanted is not None)
		self.band.clearTable()
		self.assertIsNone(self.band.tableWanted)
		self.assertTrue(self._readingADocument())

	def test_walkingBackInLaysItOutAgainOnlyWhenAsked(self):
		"""The request went with the caret, so returning to the table reads it as the page
		does until the reader says otherwise."""
		obj, document = self._inTable()
		self.band.layOutTable()
		document.inTable = False
		self.band.recheck()
		document.inTable = True
		self.band.showObject(obj)
		self.assertFalse(self._readingATable())


WIDE = [
	["Symbol", "Latest", "Change", "%Change", "Open", "High", "Low", "Volume", "Bid", "Ask"],
	["AAPL", "310.34", "+0.99", "+0.32%", "311.47", "313.36", "309.97", "1,234,567", "310.30", "310.40"],
	["MSFT", "412.10", "-1.02", "-0.25%", "413.00", "414.20", "410.80", "987,654", "412.05", "412.15"],
]


class TestATableWiderThanTheBand(TableBandTestCase):
	"""Twenty-nine columns is an ordinary watchlist and thirty-two cells is an ordinary
	display. The columns are dealt into pages and the reader moves between them, which is
	what the window does for rows one axis over."""

	def _wide(self, row=2, col=1):
		return self._inTable(rows=WIDE, row=row, col=col)

	def test_theValuesAreReadableRatherThanComplete(self):
		"""The first attempt fitted every column and gave three cells of each. A price of
		"310.34" in three cells is a digit at a time."""
		self._wide()
		self.band.layOutTable()
		for column in self.band.columnPlan().columns:
			self.assertGreaterEqual(column.width, 6)

	def test_theTableIsSeveralPages(self):
		self._wide()
		self.band.layOutTable()
		self.assertGreater(self.band.columnPlan().numPages, 1)

	def test_turningThePageMovesTheBand(self):
		self._wide()
		self.band.layOutTable()
		before = [place.column.index for place in self.band.columnPlan().placements()]
		self.assertTrue(self.band.turnColumnPage(1))
		after = [place.column.index for place in self.band.columnPlan().placements()]
		self.assertNotEqual(before, after)

	def test_turningPastTheEndDoesNothing(self):
		self._wide()
		self.band.layOutTable()
		while self.band.turnColumnPage(1):
			pass
		self.assertFalse(self.band.turnColumnPage(1))

	def test_turningThePageLeavesTheCaretWhereItIs(self):
		"""Looking around rather than going somewhere."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		self.assertEqual(document.col, 1)

	def test_theCaretMovingBringsItsColumnOntoTheBand(self):
		"""The column axis of what the window does for rows. A table wide enough to need
		pages is one where the caret goes off the band on nearly every keystroke."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.assertEqual(self.band.columnPlan().page, 0)
		document.col = 8
		self.band.recheck()
		plan = self.band.columnPlan()
		self.assertEqual(plan.page, plan.pageOf(8))
		self.assertNotEqual(plan.page, 0)

	def test_theCursorGoesWithIt(self):
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		document.col = 8
		self.band.recheck()
		self.assertIsNotNone(self.band.controller.cursorCell())

	def test_aCaretMoveUndoesAPageTurn(self):
		"""Which is the right way round: the page follows the reader, and turning it by hand
		is a look rather than a move."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		document.row = 3
		self.band.recheck()
		self.assertEqual(self.band.columnPlan().page, 0)

	def test_nothingIsLost(self):
		self._wide()
		self.band.layOutTable()
		plan = self.band.columnPlan()
		drawn = [place.column.index for page in plan.pages() for place in page if not place.column.pinned]
		self.assertEqual(drawn, list(range(1, len(WIDE[0]) + 1)))


class TestTheSymbolStaysUnderTheHand(TableBandTestCase):
	"""Six columns into a watchlist the reader is feeling four numbers with nothing to say
	whose numbers they are. The first column is repeated at the left of every later page."""

	def _wide(self, row=2, col=1):
		return self._inTable(rows=WIDE, row=row, col=col)

	def test_theRepeatedColumnIsReadForALaterPage(self):
		"""It is a copy in the plan and not in the table: its cells have to be fetched like
		any other, or the pin is blank."""
		obj, document = self._wide()
		self.band.layOutTable()
		document.reads.clear()
		self.band.turnColumnPage(1)
		self.assertIn(1, {column for _row, column in document.reads})

	def test_theSymbolIsOnTheBandOnALaterPage(self):
		"""What the reader actually feels, read back off the band."""
		self._wide()
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		drawn = " ".join(self.band.controller.describeRows())
		self.assertIn("AAPL", drawn)

	def test_itIsAtTheLeftOfTheRow(self):
		self._wide()
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		first = self.band.columnPlan().placements()[0]
		self.assertEqual((first.column.index, first.row, first.offset), (1, 0, 0))

	def test_aFingerPressOverItGoesToTheRealCell(self):
		"""The copy carries the table's own column number, so routing is not confused by it."""
		obj, document = self._wide()
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		plan = self.band.columnPlan()
		self.assertIsNotNone(plan.columnAt(0, 0))
		self.assertEqual(plan.columnAt(0, 0).index, 1)


class TestTheHeaderRowStaysOnTheDisplay(TableBandTestCase):
	"""The window starts where the reader is, so a layout turned on from the middle of a table
	showed the columns and never said what any of them was. Scrolling back up to look is not
	an answer either, because the answer is wanted while reading somewhere else."""

	def _midTable(self, row=3):
		obj, document = self._inTable(row=row)
		self.band.layOutTable()
		return document

	def _drawn(self):
		return self.band.controller.describeRows()

	def _routed(self, document):
		""":return: the cells a routing press actually moved the caret into."""
		return [where for where, holder in document.carets if holder["caret"]]

	def test_theHeadersAreThereFromTheMiddleOfATable(self):
		self._midTable()
		self.assertIn("Symbol", self._drawn()[0])

	def test_theyAreOnTheTopRow(self):
		self._midTable()
		cells = self.band.controller.cells()
		row = cells[: self.band.controller.renderer.numCols]
		self.assertEqual(bytes(row).decode("latin-1").rstrip("\0")[:6], "Symbol")

	def test_theBandIsStillItsFullHeight(self):
		"""The pinned row is part of the band, not something added to it."""
		self._midTable()
		self.assertEqual(len(self.band.controller.cells()), ROWS * COLS)

	def test_theWindowGivesUpTheRow(self):
		self._midTable()
		self.assertEqual(self.band.controller.window.numRows, ROWS - 1)

	def test_theHeaderIsNotAlsoDrawnAsContent(self):
		"""Serving it as content as well would draw it twice at the top of the table and not
		at all anywhere else. The reader standing in the header row is where that shows."""
		self._inTable(row=1)
		self.band.layOutTable()
		content = " ".join(self._drawn()[1:])
		self.assertNotIn("Symbol", content)

	def test_theReaderInTheHeaderRowStillSeesTheRowsBelow(self):
		"""Keeping row one back must not leave the band with nothing when the caret is in it."""
		self._inTable(row=1)
		self.band.layOutTable()
		self.assertIn("AAPL", " ".join(self._drawn()[1:]))

	def test_turningThePageRedrawsIt(self):
		"""It holds the old page's cells at the old page's offsets, exactly as the rows did."""
		self._inTable(rows=WIDE, row=2)
		self.band.layOutTable()
		before = self._drawn()[0]
		self.band.turnColumnPage(1)
		self.assertNotEqual(self._drawn()[0], before)

	def test_theNewPagesHeadersAreTheOnesShown(self):
		self._inTable(rows=WIDE, row=2)
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		drawn = self._drawn()[0]
		shown = [place.column.index for place in self.band.columnPlan().placements()]
		self.assertIn(WIDE[0][shown[-1] - 1][:4], drawn)

	def test_aFingerPressOnItReachesTheHeaderCell(self):
		"""A pinned row is not in the window, so routing had to learn about it separately."""
		document = self._midTable()
		self.assertTrue(self.band.controller.routeTo(0))
		self.assertEqual(self._routed(document)[-1], (1, 1))

	def test_aFingerPressPastItStillReachesTheRowUnderIt(self):
		"""Everything the window says about rows is worked out first and moved down by one.
		Off by that row, every routing press in the table went one row too high."""
		document = self._midTable()
		topRow = self.band.controller.window.visibleRows()[0].blockId.bookmark
		self.assertTrue(self.band.controller.routeTo(COLS))
		self.assertEqual(self._routed(document)[-1], (topRow, 1))

	def test_theReaderCanTurnItOff(self):
		CONFIG["flowTableHeaders"] = False
		self._midTable()
		self.assertIsNone(self.band.controller.pinned)
		self.assertEqual(self.band.controller.window.numRows, ROWS)

	def test_aTableOfNothingButAHeaderPinsNothing(self):
		"""There would be no content left to read under it, so the header is content."""
		self._inTable(rows=[WATCHLIST[0]], row=1)
		self.band.layOutTable()
		self.assertIsNone(self.band.controller.pinned)
		self.assertIn("Symbol", " ".join(self.band.controller.describeRows()))


class TestATableWhoseValuesChange(TableBandTestCase):
	"""A watchlist during market hours changes under the reader's hand and nothing tells the
	band. NVDA reports a cell's new value only while the browse mode caret is in that cell,
	which is the right answer for speech and for a display showing one cell at a time, and no
	answer at all for a display showing a page of a table at once."""

	def _watching(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		callLaterQueue.pending.clear()
		return document

	def _toldAboutChanges(self, installed):
		"""Say whether the document-change patch is in place, which decides whether the band
		falls back to a clock."""
		import brlMultiline.patches as patches

		original = patches.liveUpdatesInstalled
		patches.liveUpdatesInstalled = lambda: installed
		self.addCleanup(setattr, patches, "liveUpdatesInstalled", original)

	def _counted(self):
		""":return: a list that grows each time the display is written."""
		segment = self.band.segment()
		written = []
		original = segment.refresh
		segment.refresh = lambda *args, **kwargs: (written.append(1), original(*args, **kwargs))[1]
		self.addCleanup(setattr, segment, "refresh", original)
		return written

	def test_aNewPriceReachesTheBand(self):
		document = self._watching()
		before = self.band.controller.cells()
		document.rows[1][1] = "999.99"
		self.band._refreshLiveContent()
		self.assertNotEqual(self.band.controller.cells(), before)

	def test_itIsTheNewPriceThatIsThere(self):
		document = self._watching()
		document.rows[1][1] = "999.99"
		self.band._refreshLiveContent()
		self.assertIn("999.99", " ".join(self.band.controller.describeRows()))

	def test_theReaderKeepsTheirPlace(self):
		"""The difference between this and rebuilding. A reader whose hand is on a row wants
		that row to hold this second's price, not to be moved while the band starts again."""
		document = self._watching()
		before = self.band.controller.window.anchor
		document.rows[1][1] = "999.99"
		self.band._refreshLiveContent()
		self.assertEqual(self.band.controller.window.anchor.blockId, before.blockId)

	def test_aTableThatDidNotChangeIsNotWritten(self):
		"""A display rewritten with identical content under a reading hand is a display that
		flickers for nothing."""
		self._watching()
		written = self._counted()
		self.band._refreshLiveContent()
		self.assertEqual(written, [])

	def test_aTableThatDidChangeIsWritten(self):
		document = self._watching()
		written = self._counted()
		document.rows[1][1] = "999.99"
		self.band._refreshLiveContent()
		self.assertEqual(len(written), 1)

	def test_eachPassAsksForTheNext(self):
		self._watching()
		self.band._refreshLiveContent()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_theChainEndsWhenThereIsNothingToReadAgain(self):
		"""It ends by itself rather than being stopped from somewhere, because nothing else
		knows when the band stopped showing something that can be re-read."""
		self._watching()
		self.band.controller = None
		callLaterQueue.pending.clear()
		self.band._refreshLiveContent()
		self.assertEqual(callLaterQueue.pending, [])

	def test_leavingTheTableDoesNotEndIt(self):
		"""The page around a table changes too, and the band is still reading the page."""
		self._watching()
		self.band.clearTable()
		callLaterQueue.pending.clear()
		self.band._refreshLiveContent()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_nothingIsReadOnAClockWhenSomethingWillSaySo(self):
		"""The point of the whole exercise: a table nobody is changing costs nothing between
		the reader's own keystrokes."""
		self._watching()
		self._toldAboutChanges(True)
		CONFIG["flowLiveSeconds"] = 0
		self.band._refreshLiveContent()
		self.assertEqual(callLaterQueue.pending, [])

	def test_aClockIsUsedWhenNothingWill(self):
		"""Zero means "you decide", not "never": a reader whose NVDA cannot be patched must
		not silently lose the updates they had."""
		self._watching()
		self._toldAboutChanges(False)
		CONFIG["flowLiveSeconds"] = 0
		self.band._refreshLiveContent()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_theReadersOwnIntervalWinsEitherWay(self):
		self._watching()
		self._toldAboutChanges(True)
		CONFIG["flowLiveSeconds"] = 5
		self.band._refreshLiveContent()
		self.assertEqual([timer.milliseconds for timer in callLaterQueue.pending], [5000])

	def test_layingATableOutStartsTheChain(self):
		callLaterQueue.pending.clear()
		self._inTable()
		self.band.layOutTable()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_onlyOnePassIsEverPending(self):
		"""Scheduling from two places at once would double the reading for nothing."""
		self._watching()
		self.band._cancelLiveRead()
		callLaterQueue.pending.clear()
		self.band._scheduleLiveRead()
		self.band._scheduleLiveRead()
		self.assertEqual(len(callLaterQueue.pending), 1)


class TestBeingToldThatTheDocumentChanged(TableBandTestCase):
	"""NVDA's virtual buffer says when its content moved, for any accessibility event the
	backend acted on and not only for a live region. That is a far better signal than a clock:
	a page that says nothing costs nothing at all."""

	def _watching(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		callLaterQueue.pending.clear()
		self.band._cancelLiveRead()
		return document

	def test_aChangeInThisDocumentAsksForAPass(self):
		document = self._watching()
		self.band.documentChanged(document)
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_itIsAskedForSoonerThanAPollWouldBe(self):
		document = self._watching()
		self.band.documentChanged(document)
		self.assertEqual(callLaterQueue.pending[0].milliseconds, LIVE_SETTLE_MILLIS)

	def test_aChangeInAnotherDocumentIsNotOurs(self):
		"""A browser holds a buffer per document, and the reader has other tabs open."""
		self._watching()
		self.band.documentChanged(FakeTableDocument([["a"], ["b"]]))
		self.assertEqual(callLaterQueue.pending, [])

	def test_aBandReadingThePageAnswersItToo(self):
		"""The reader's own finding: read as a table the prices moved and read as ordinary
		browse mode they sat still, and there is nothing about a table that makes it the
		dynamic one."""
		document = self._watching()
		self.band.clearTable()
		self.band._cancelLiveRead()
		callLaterQueue.pending.clear()
		self.band.documentChanged(document)
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_aBandWithNoFlowIgnoresIt(self):
		document = self._watching()
		self.band.controller = None
		callLaterQueue.pending.clear()
		self.band.documentChanged(document)
		self.assertEqual(callLaterQueue.pending, [])

	def test_theReaderCanTurnFollowingOff(self):
		document = self._watching()
		CONFIG["flowLiveUpdates"] = False
		self.band.documentChanged(document)
		self.assertEqual(callLaterQueue.pending, [])

	def test_aBurstIsOnePass(self):
		"""A page repricing thirty rows sends an event per region it touched, and that must
		not become thirty reads of the same band."""
		document = self._watching()
		for _ in range(30):
			self.band.documentChanged(document)
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_aBurstDoesNotPushThePassFurtherOut(self):
		"""Restarting the wait on every event would starve a page that never stops changing."""
		document = self._watching()
		self.band.documentChanged(document)
		first = callLaterQueue.pending[0]
		self.band.documentChanged(document)
		self.assertIs(callLaterQueue.pending[0], first)

	def test_newsBringsAPendingPollForward(self):
		"""A pass two seconds away must not keep a quarter second one waiting behind it."""
		document = self._watching()
		CONFIG["flowLiveSeconds"] = 5
		self.band._scheduleLiveRead()
		self.band.documentChanged(document)
		self.assertEqual(
			[timer.milliseconds for timer in callLaterQueue.pending],
			[
				LIVE_SETTLE_MILLIS,
			],
		)

	def test_thePassItAsksForShowsTheNewValue(self):
		document = self._watching()
		document.rows[1][1] = "999.99"
		self.band.documentChanged(document)
		callLaterQueue.pending[0].run()
		self.assertIn("999.99", " ".join(self.band.controller.describeRows()))

	def test_whatArrivedIsCounted(self):
		"""Whether the event reaches us at all is the one thing about this that cannot be
		felt, and a reader whose prices sit still needs to know which half is not working."""
		document = self._watching()
		document.rows[1][1] = "999.99"
		self.band.documentChanged(document)
		callLaterQueue.pending[0].run()
		self.assertEqual(self.band.liveCounts, [1, 1, 1])


class TestWhatAWideTableCostsToRead(TableBandTestCase):
	"""Every cell is a search of the document. A twenty-nine column table read four columns
	to a page was searching for twenty-five columns nobody was looking at, per row, forever."""

	def _wideTable(self, columns=29, rows=10):
		headers = [f"Col{n}" for n in range(1, columns + 1)]
		body = [[f"r{r}c{c}" for c in range(1, columns + 1)] for r in range(1, rows + 1)]
		return self._inTable(rows=[headers, *body], row=2, col=1)

	def test_onlyThePagesColumnsAreReadForAFreshRow(self):
		obj, document = self._wideTable()
		self.band.layOutTable()
		onPage = {place.column.index for place in self.band.columnPlan().placements()}
		document.reads.clear()
		self.band.controller.source.blockAtCursor()
		self.assertEqual({column for _row, column in document.reads}, onPage)

	def test_turningThePageReadsTheNewPagesColumns(self):
		obj, document = self._wideTable()
		self.band.layOutTable()
		document.reads.clear()
		self.band.turnColumnPage(1)
		onPage = {place.column.index for place in self.band.columnPlan().placements()}
		self.assertEqual({column for _row, column in document.reads}, onPage)

	def test_turningThePageShowsTheNewPagesValues(self):
		"""The rows read for the old page hold the wrong cells, so they are read again. Drawn
		as they were, the old page's values would appear at the new page's offsets."""
		obj, document = self._wideTable()
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		first = self.band.columnPlan().placements()[0].column.index
		control = self.band.controller
		rendered = control.window.blocks[0]
		region = control.regionFor(rendered.blockId)
		self.assertIn(first, [cell.index for cell in region.cells])


class TestTheCaretInAColumnThatIsNotDrawn(TableBandTestCase):
	"""Quick navigation to a table lands the caret in its first cell, and on the reader's own
	watchlist the first cell is an unreadable icon. The band read that as a table that had
	changed under it, rebuilt the layout on every redraw, and panning did nothing at all: each
	pan was undone by the rebuild that followed it."""

	def _withAnIconColumn(self, col=1, rows=20):
		"""A table of more rows than the band holds, whose first column is unreadable icons."""
		body = [["", f"{n}.00", f"+{n}", f"+{n}%"] for n in range(1, rows + 1)]
		return self._inTable(rows=[["", "Last", "Change", "%Chg"], *body], row=1, col=col)

	def test_theLayoutIsNotRebuiltOnEveryRedraw(self):
		self._withAnIconColumn()
		self.band.layOutTable()
		plan = self.band.columnPlan()
		self.band.recheck()
		self.band.recheck()
		self.assertIs(self.band.columnPlan(), plan)

	def test_theBandCanStillPan(self):
		"""The symptom the reader met: stuck until the caret was moved with the arrow keys."""
		self._withAnIconColumn()
		self.band.layOutTable()
		before = self.band.controller.describeRows()
		self.band.controller.panForward()
		self.band.recheck()
		self.assertNotEqual(self.band.controller.describeRows(), before)

	def test_theReadingIsNotStartedAgain(self):
		"""A rebuild is a fresh generation and a fresh measurement of every column, which is
		the expensive half of what the pan was losing."""
		self._withAnIconColumn()
		self.band.layOutTable()
		generation = self.band.controller.source.generation
		self.band.recheck()
		self.assertEqual(self.band.controller.source.generation, generation)

	def test_aColumnTheTableNeverHadStillCountsAsAChange(self):
		"""The check must still do its job: a caret in a column the plan never measured is
		proof of a change the column count cannot see."""
		obj, document = self._withAnIconColumn()
		self.band.layOutTable()
		plan = self.band.columnPlan()
		document.col = document.numCols + 1
		self.band.recheck()
		self.assertIsNot(self.band.columnPlan(), plan)


class TestATableThatChangesShape(TableBandTestCase):
	"""A plan is of a table. When the table is not that table any more the plan is of nothing:
	its widths were measured from columns that have gone, and it has no page for one that has
	arrived, so the band cannot follow the caret there."""

	def test_aColumnAppearingIsNoticed(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		before = len(self.band.columnPlan().columns)
		for line in document.rows:
			line.append("new")
		self.band.recheck()
		self.assertEqual(len(self.band.columnPlan().columns), before + 1)

	def test_theCaretCanFollowIntoTheNewColumn(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		for line in document.rows:
			line.append("new")
		document.col = len(document.rows[0])
		self.band.recheck()
		plan = self.band.columnPlan()
		self.assertIsNotNone(plan.pageOf(document.col))

	def test_aColumnGoingAwayIsNoticedToo(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		before = len(self.band.columnPlan().columns)
		for line in document.rows:
			line.pop()
		self.band.recheck()
		self.assertEqual(len(self.band.columnPlan().columns), before - 1)

	def test_aTableThatHasNotChangedIsNotRebuilt(self):
		"""This is asked on every redraw, so it must be cheap and it must be quiet."""
		self._inTable()
		self.band.layOutTable()
		plan = self.band.columnPlan()
		self.band.recheck()
		self.assertIs(self.band.columnPlan(), plan)


class TestTheCursorSaysWhichCell(TableBandTestCase):
	"""Speech says which cell the reader is in. Braille had stopped saying it at all.

	The cursor is the whole of the answer here: the columns say where a value sits and the
	cursor says which of them the reader is standing in. Without it a table read beautifully
	and gave no way to tell, so every check of "where am I" went back to speech — which is
	the thing reading spatially exists to make unnecessary.
	"""

	def test_theCursorIsOnTheCellTheCaretIsIn(self):
		"""Counted from the top of the band, so the pinned header row is part of the answer:
		the window's own rows start under it."""
		self._inTable(row=2, col=1)
		self.band.layOutTable()
		control = self.band.controller
		plan = control.renderer.columnPlan
		self.assertEqual(control.cursorCell(), control.pinnedCells + plan.placements()[0].offset)

	def test_itMovesAlongTheRow(self):
		"""What the arrow keys do inside a table, and what watching only the row missed."""
		obj, document = self._inTable(row=2, col=1)
		self.band.layOutTable()
		document.col = 3
		self.band.recheck()
		control = self.band.controller
		plan = control.renderer.columnPlan
		self.assertEqual(control.cursorCell(), control.pinnedCells + plan.placements()[2].offset)

	def test_itMovesDownTheColumn(self):
		obj, document = self._inTable(row=2, col=2)
		self.band.layOutTable()
		before = self.band.controller.cursorCell()
		document.row = 3
		self.band.recheck()
		after = self.band.controller.cursorCell()
		self.assertIsNotNone(after)
		self.assertNotEqual(after, before)

	def test_itIsOnTheRowTheCaretIsOnAndNoOther(self):
		"""Each row of a bandful would otherwise claim the cursor."""
		obj, document = self._inTable(row=2, col=1)
		self.band.layOutTable()
		control = self.band.controller
		cursors = []
		for block in control.window.blocks:
			region = control.regionFor(block.blockId)
			cursors.append(region.brailleCursorPos)
		self.assertEqual(len([at for at in cursors if at is not None]), 1)

	def test_thereIsACursorAtAll(self):
		"""The report, in one assertion: braille had no indicator of the cell."""
		self._inTable()
		self.band.layOutTable()
		self.assertIsNotNone(self.band.controller.cursorCell())


class TestWhereTheBandLandsWhenTheTableIsDone(TableBandTestCase):
	"""At the reader, and it was landing at the top of the page.

	`_arrival` reads at an object's own place in the document rather than at the cursor,
	which is right for a link or a field the reader has tabbed to. In browse mode the focus
	object *is* the document for as long as the reader is not on a control, which is most of
	the time — and the document's own place in the document is offset zero. So the reading
	that replaced the table began at the top of the page, and the next arrow key put it
	right, which is what "the display falls behind" looks like from the outside.
	"""

	PAGE = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]

	def test_theBandLandsWhereTheReaderIs(self):
		obj, document = self._inTable(lines=self.PAGE, caretIndex=6)
		self.band.layOutTable()
		document.inTable = False
		self.band.recheck()
		self.assertEqual(self.band.controller.window.blocks[0].rawText, "seven")

	def test_notAtTheTopOfThePage(self):
		"""The symptom as the reader met it: the band showed the table above the one they
		had just walked out of."""
		obj, document = self._inTable(lines=self.PAGE, caretIndex=6)
		self.band.layOutTable()
		document.inTable = False
		self.band.recheck()
		shown = [block.rawText for block in self.band.controller.window.blocks]
		self.assertNotIn("one", shown)

	def test_thePageItselfIsNotSomethingOnThePage(self):
		"""The rule underneath, stated once: a tree interceptor is built over an object, and
		that object is the document rather than a thing inside it."""
		obj, document = self._inTable()
		self.assertFalse(self.band._isIn(obj, document))

	def test_somethingActuallyOnThePageStillIs(self):
		obj, document = self._inTable()
		field = FakeNavigatorObject("an edit", treeInterceptor=document)
		self.assertTrue(self.band._isIn(field, document))


class TestWalkingOutOfTheTable(TableBandTestCase):
	"""In browse mode the caret moves without the focus moving.

	The focus object stays the document, so no focus change is reported and `showObject` is
	never called. On hardware the reader read a table, navigated up to a heading well above
	it, and the band went on showing the table — because `recheck`, the one thing called
	before every redraw, had been told to leave tables alone.

	It was right that `recheck`'s own comparison cannot answer a question about a table. It
	was wrong to stop there rather than ask the question a table *can* answer.
	"""

	def test_theCaretLeavingTheTableGivesTheLayoutBack(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		document.inTable = False
		self.band.recheck()
		self.assertIsNone(self.band.tableWanted)
		self.assertFalse(self._readingATable())

	def test_theCaretMovingWithinTheTableIsFollowed(self):
		"""And the other half: nothing else follows a caret through a table, so if `recheck`
		does not, the band shows the row the reader entered on for as long as they stay."""
		obj, document = self._inTable(row=2)
		self.band.layOutTable()
		document.row = 4
		self.band.recheck()
		self.assertEqual(self.band.controller.source.row, 4)

	def test_stayingStillCostsNoRedraw(self):
		"""`recheck` runs before every redraw. Following a caret that has not moved would
		redraw the band on every one of them."""
		obj, document = self._inTable(row=2)
		self.band.layOutTable()
		before = self.band.controller.window.anchor
		self.band.recheck()
		self.assertIs(self.band.controller.window.anchor, before)

	def test_thePlanSurvivesTheCaretMoving(self):
		obj, document = self._inTable(row=2)
		self.band.layOutTable()
		plan = self.band.controller.renderer.columnPlan
		document.row = 3
		self.band.recheck()
		self.assertIs(self.band.controller.renderer.columnPlan, plan)


class TestTheLayoutDoesNotOutliveTheTable(TableBandTestCase):
	"""The state a reader cannot see, and the one that goes wrong."""

	def test_movingWithinTheTableKeepsTheLayout(self):
		obj, document = self._inTable(row=2)
		self.band.layOutTable()
		document.row = 3
		self.band.showObject(obj)
		self.assertTrue(self._readingATable())
		self.assertEqual(self.band.controller.source.row, 3)

	def test_movingWithinTheTableDoesNotRebuildThePlan(self):
		"""A column that moves is a column the reader has to find again, and they would move
		on every keystroke if the plan were remade on each one."""
		obj, document = self._inTable(row=2)
		self.band.layOutTable()
		plan = self.band.controller.renderer.columnPlan
		document.row = 3
		self.band.showObject(obj)
		self.assertIs(self.band.controller.renderer.columnPlan, plan)

	def test_leavingTheTableDropsTheLayout(self):
		self._inTable()
		self.band.layOutTable()
		self.band.showObject(self._elsewhere())
		self.assertIsNone(self.band.tableWanted)
		self.assertFalse(self._readingATable())

	def test_anotherTableIsNotTheOneThatWasAskedFor(self):
		"""Two tables on one page. The reader asked for this one."""
		self._inTable(tableID=1)
		self.band.layOutTable()
		other = FakeNavigatorObject(
			"a page",
			treeInterceptor=FakeTableDocument(
				[list(line) for line in WATCHLIST],
				tableID=2,
			),
		)
		self.api.getFocusObject = lambda: other
		self.band.showObject(other)
		self.assertIsNone(self.band.tableWanted)
		self.assertFalse(self._readingATable())
