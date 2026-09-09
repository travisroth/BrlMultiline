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

from ._stubs import (
	CONFIG,
	FORMAT_CONFIG,
	FakeGrid,
	FakeNavigatorObject,
	ReportTableHeaders,
	callLaterQueue,
	FakeTableDocument,
	NoTableDocument,
	flashedMessages,
	installStubs,
	resetConfig,
	spokenMessages,
)

installStubs()

from brlMultiline import bmConfig  # noqa: E402
from brlMultiline.flow import CallCancelled, Edge, EdgeState  # noqa: E402
from brlMultiline.flowBand import LIVE_SETTLE_MILLIS, NO_TABLE, NOT_READ  # noqa: E402
from brlMultiline.flowBuild import Unreadable  # noqa: E402
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


class CommandHolder:
	"""Enough of the plugin for the table command to be run against a band.

	The request to see a table in columns is the plugin's, not the band's, so this shares the
	band's own — two stores would let the command and the band disagree about what was asked
	for, which is the state the command exists to change.
	"""

	def __init__(self, band, withBand=True):
		from brlMultiline import GlobalPlugin

		self.band = band
		self.flowBand = band if withBand else None
		self._monitors = {}
		self.layOutPinnedTables = GlobalPlugin.layOutPinnedTables.__get__(self)
		self._wantTableHere = GlobalPlugin._wantTableHere.__get__(self)
		self.reportAboutTheDisplay = GlobalPlugin.reportAboutTheDisplay.__get__(self)
		self._tableHere = GlobalPlugin._tableHere.__get__(self)
		# The paging commands go through the plugin to find which table to move, so the
		# stand-in has to be able to answer that too: what is being tested is the command.
		self._tableToPage = GlobalPlugin._tableToPage.__get__(self)
		self.tablesInColumns = GlobalPlugin.tablesInColumns.__get__(self)
		self.container = band.plugin.container
		self._segmentKeysOn = GlobalPlugin._segmentKeysOn.__get__(self)
		self._inAnotherTable = GlobalPlugin._inAnotherTable.__get__(self)
		# The band commands ask which column the reader is in and how to name it back to them.
		self._columnUnderTheCursor = GlobalPlugin._columnUnderTheCursor.__get__(self)
		self._sayAboutTheColumn = GlobalPlugin._sayAboutTheColumn.__get__(self)

	@property
	def tableWanted(self):
		return self.band.plugin.tableWanted

	@tableWanted.setter
	def tableWanted(self, key):
		self.band.plugin.tableWanted = key

	@property
	def tableRefused(self):
		"""Shared with the band for the reason `tableWanted` is: in a real installation the
		band's plugin and the plugin running the command are one object, and two stores would
		let the command's "off" and the band's "the store says on" disagree."""
		return getattr(self.band.plugin, "tableRefused", None)

	@tableRefused.setter
	def tableRefused(self, key):
		self.band.plugin.tableRefused = key

	def refreshMonitors(self, reveal=None):
		pass


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

	def _inTable(
		self,
		rows=None,
		tableID=1,
		row=2,
		col=1,
		lines=None,
		caretIndex=0,
		columnHeaders=None,
	):
		document = FakeTableDocument(
			[list(line) for line in (rows or WATCHLIST)],
			tableID=tableID,
			row=row,
			col=col,
			lines=lines,
			columnHeaders=columnHeaders,
		)
		document.caretIndex = caretIndex
		obj = FakeNavigatorObject("a page", treeInterceptor=document)
		self.api.getFocusObject = lambda: obj
		# And the navigator object, which follows the focus in browse mode. A stub that set
		# only the focus was less faithful than NVDA and hid a command reading the other one.
		self.api.getNavigatorObject = lambda: obj
		self.addCleanup(setattr, self.api, "getNavigatorObject", self.api.getNavigatorObject)
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
		# The pins as well as the band: the command carries a change of mind to the pins on
		# the same table, and a stand-in without them would not notice if it stopped.
		GlobalPlugin.script_flowTableColumns(CommandHolder(self.band), None)
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

	def test_pressingItWithNoBandStillRecordsTheRequest(self):
		"""On a one row display there is no band to claim and the reader still wants to say
		"this table, in columns", so that pinning it to a display with room shows it that
		way. Refusing here was refusing the only route they have."""
		from brlMultiline import GlobalPlugin

		obj, _document = self._inTable()
		holder = CommandHolder(self.band, withBand=False)
		spokenMessages.clear()
		GlobalPlugin.script_flowTableColumns(holder, None)
		self.assertIsNotNone(holder.tableWanted)
		self.assertTrue(any("columns on" in message for message in spokenMessages))

	def test_pressingItInADifferentTableLaysThatOneOut(self):
		"""With no band nothing drops the old request, so the press that should have laid out
		the second table was turning the first one off instead."""
		from brlMultiline import GlobalPlugin

		self._inTable()
		holder = CommandHolder(self.band, withBand=False)
		holder.tableWanted = ("another document", 9)
		spokenMessages.clear()
		GlobalPlugin.script_flowTableColumns(holder, None)
		self.assertTrue(any("columns on" in message for message in spokenMessages))
		self.assertNotEqual(holder.tableWanted, ("another document", 9))

	def test_pressingItOutsideAnyTableClearsAStaleRequest(self):
		"""Which is how a request left behind is got rid of, with no band to drop it."""
		from brlMultiline import GlobalPlugin

		obj = FakeNavigatorObject("a page", treeInterceptor=NoTableDocument())
		self.api.getFocusObject = lambda: obj
		self.api.getNavigatorObject = lambda: obj
		holder = CommandHolder(self.band, withBand=False)
		holder.tableWanted = ("another document", 9)
		spokenMessages.clear()
		GlobalPlugin.script_flowTableColumns(holder, None)
		self.assertIsNone(holder.tableWanted)
		self.assertIn("Table columns off", spokenMessages)

	def test_pressingItWithNoBandAndNoTableSaysSo(self):
		from brlMultiline import GlobalPlugin

		obj = FakeNavigatorObject("a page", treeInterceptor=NoTableDocument())
		self.api.getFocusObject = lambda: obj
		self.api.getNavigatorObject = lambda: obj
		holder = CommandHolder(self.band, withBand=False)
		spokenMessages.clear()
		GlobalPlugin.script_flowTableColumns(holder, None)
		self.assertIn("Not in a table", spokenMessages)

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
		self.assertEqual(self.band.columnPlan().at, 0)
		document.col = 8
		self.band.recheck()
		plan = self.band.columnPlan()
		self.assertTrue(plan.showing(8))
		self.assertNotEqual(plan.at, 0)

	def test_theCursorGoesWithIt(self):
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		document.col = 8
		self.band.recheck()
		self.assertIsNotNone(self.band.controller.cursorCell())

	def test_aPageTheReaderTurnedToStaysTurnedWhileTheirColumnIsOnIt(self):
		"""Reported from hardware. The band followed the caret's own page on every move, so
		one press of down arrow put a reader on any page past the first back onto page one —
		the caret is still in column one, because table navigation keeps the column while
		moving the row. It stays because the pinned key column *is* column one, drawn at the
		left of this page: the reader can feel where they are."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		self.assertTrue(self.band.columnPlan().showing(1))
		where = self.band.columnPlan().at
		document.row = 3
		self.band.recheck()
		self.assertEqual(self.band.columnPlan().at, where)

	def test_andWhenTheCaretMovesToAnotherColumnOfTheSamePage(self):
		"""Reading across what they turned to, which is what they turned to it for."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		drawn = [place.column.index for place in self.band.columnPlan().placements()]
		where = self.band.columnPlan().at
		document.col = drawn[-1]
		self.band.recheck()
		self.assertEqual(self.band.columnPlan().at, where)

	def test_butACaretThatLeavesThePageBringsTheBandBack(self):
		"""The other half, and the one holding the page against every move cost: the caret
		walked off the display and nothing brought the display to it. Braille may travel; the
		cursor moving is what tethers it back."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		plan = self.band.columnPlan()
		away = next(
			column for column in range(1, len(WIDE[0]) + 1) if not plan.showing(column)
		)
		document.col = away
		self.band.recheck()
		self.assertTrue(self.band.columnPlan().showing(away))

	def test_andSoDoesOneThatWalksTheColumnsPastIt(self):
		"""Without table navigation, down arrow is a move to the next *cell*: the caret walks
		the columns of the row, and the display goes on being where the caret is."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		for column in range(1, len(WIDE[0]) + 1):
			document.col = column
			self.band.recheck()
			plan = self.band.columnPlan()
			self.assertTrue(plan.showing(column), f"column {column} is not on the display")

	def test_theBandFollowsTheCaretBeforeAnyPageIsTurned(self):
		"""A reader who has said nothing about which columns they want arrows into a wide
		table and the display shows where they are."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.assertEqual(self.band.columnPlan().at, 0)
		document.col = 8
		self.band.recheck()
		self.assertNotEqual(self.band.columnPlan().at, 0)

	def test_turningAnotherPageStillWorks(self):
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		self.band.turnColumnPage(1)
		self.assertTrue(self.band.turnColumnPage(1))
		self.assertEqual(self.band.columnPlan().page, 2)

	def test_aCaretOneColumnPastTheBandScrollsByOneColumn(self):
		"""Not a page. The reader asked for this: working column by column, a whole page turn
		replaces everything under their hands for a move of one cell."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		before = [place.column.index for place in self.band.columnPlan().placements()]
		document.col = before[-1] + 1
		self.band.recheck()
		after = [place.column.index for place in self.band.columnPlan().placements()]
		self.assertIn(before[-1] + 1, after)
		self.assertIn(before[-1], after)

	def test_andThePageTurnStillMovesAWholeBandful(self):
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		plan = self.band.columnPlan()
		self.band.turnColumnPage(1)
		self.assertEqual(self.band.columnPlan().at, plan.at + plan.shown)

	def test_andItTurnsFromWhereverTheScrollingLeftThem(self):
		"""The two cannot get out of step, because there is only one number to be in."""
		obj, document = self._wide(col=1)
		self.band.layOutTable()
		document.col = self.band.columnPlan().shown + 1
		self.band.recheck()
		scrolled = self.band.columnPlan()
		self.band.turnColumnPage(1)
		self.assertEqual(self.band.columnPlan().at, scrolled.at + scrolled.shown)

	def test_nothingIsLost(self):
		self._wide()
		self.band.layOutTable()
		plan = self.band.columnPlan()
		drawn = [place.column.index for page in plan.pages() for place in page if not place.column.pinned]
		self.assertEqual(drawn, list(range(1, len(WIDE[0]) + 1)))



class TestPagingFromAColumnTheLayoutLeftOut(TableBandTestCase):
	"""Reported from hardware. Quick navigation and the arrow keys land the caret in the first
	cell of a table, and on this reader's watchlist that cell is an icon column the layout
	leaves out. Paging then "tries, then just repeats the first columns": the reader turns a
	page and the display is home again before they feel it.

	The layout command does not show this, because it starts the reader on a cell with data.
	Nothing should have to: the cursor being somewhere undrawn is ordinary, and it must not
	cost the reader the ability to look around.
	"""

	WIDE = [
		[""] + [f"h{number}" for number in range(1, 12)],
		*(
			[""] + [f"r{row}c{number}" for number in range(1, 12)]
			for row in range(1, 21)
		),
	]
	"""Twenty rows, so that the band can be panned, and a blank first column: the reader's
	watchlist has one and quick navigation lands them in it."""

	def _here(self, col=1):
		obj, document = self._inTable(rows=self.WIDE, row=2, col=col)
		self.assertTrue(self.band.layOutTable())
		return obj, document

	def test_aColumnTheReaderExcludedIsStillKnown(self):
		"""The fault behind the reported loop. A saved layout that names columns drops the rest
		from the measurement, so the plan had never heard of them — and the caret sitting in
		one read as a column from another table, which is a rebuild on every redraw: a display
		that will not pan, and a page turn undone before the reader feels it."""
		from brlMultiline import flowTableLayouts, flowTableSource

		obj, document = self._inTable(rows=self.WIDE, row=2, col=1)
		document.documentConstantIdentifier = "https://example.com/wide"
		handle = flowTableSource.tableAt(obj)
		flowTableLayouts.remember(handle, flowTableLayouts.TableLayout(columns=(2, 3, 4)))
		self.band.clearTable()
		self.band.refresh(force=True)
		self.assertTrue(self._readingATable())
		plan = self.band.columnPlan()
		self.assertIn(1, plan.excluded)
		self.assertTrue(plan.knows(1))

	def test_soTheBandIsNotRebuiltWhileTheyStandInIt(self):
		obj, document = self._inTable(rows=self.WIDE, row=2, col=1)
		from brlMultiline import flowTableLayouts, flowTableSource

		document.documentConstantIdentifier = "https://example.com/wide"
		handle = flowTableSource.tableAt(obj)
		flowTableLayouts.remember(handle, flowTableLayouts.TableLayout(columns=(2, 3, 4)))
		self.band.clearTable()
		self.band.refresh(force=True)
		plan = self.band.columnPlan()
		for _ in range(4):
			self.band.recheck()
		self.assertIs(self.band.columnPlan(), plan, "the layout was made again")

	def test_theEmptyColumnIsLeftOutOfTheLayout(self):
		self._here()
		self.assertIn(1, self.band.columnPlan().omitted)

	def test_andTheReaderCanStillTurnThePage(self):
		self._here()
		self.assertTrue(self.band.turnColumnPage(1))
		self.assertEqual(self.band.columnPlan().page, 1)

	def test_andTheRedrawAfterItLeavesThePageAlone(self):
		"""The band follows a caret that has moved to a column it is not showing; a caret that
		has not moved says nothing at all."""
		self._here()
		self.band.turnColumnPage(1)
		self.band.recheck()
		self.assertEqual(self.band.columnPlan().page, 1)

	def _counting(self, answer):
		""":return: the cells the shape check looks into, with the answer it is given."""
		from brlMultiline import flowTableSource

		asked = []
		real = flowTableSource.cellHasContent
		flowTableSource.cellHasContent = lambda handle, row, column, live=False: (
			asked.append((row, column)) or answer
		)
		self.addCleanup(setattr, flowTableSource, "cellHasContent", real)
		return asked

	def test_theCellIsLookedIntoOnceWhateverItSays(self):
		"""The reader's own case, and the cost of it: looking into a cell is a search of the
		document, the dry run put it at 44 ms, and the page fired 169 change notices while
		they sat there. Asked once per cell it is a search; asked per redraw it is a stall."""
		obj, document = self._here()
		asked = self._counting(False)
		document.row = 3
		for _ in range(5):
			self.band.recheck()
		self.assertEqual(len(asked), 1, f"the same cell was looked into {len(asked)} times")

	def test_andOnceWhenItAnswersYesToo(self):
		"""A cell that answers sends the layout to be made again. Asking a second time would
		send it again on every live pass, which is the reader's page taken away each time they
		turn one."""
		obj, document = self._here()
		asked = self._counting(True)
		document.row = 3
		for _ in range(5):
			self.band.recheck()
		self.assertEqual(len(asked), 1, f"the same cell was looked into {len(asked)} times")

	def test_movingToAnotherCellAsksAgain(self):
		"""Which is how a reader asks for a second look, and the only thing given up by
		asking once: a value appearing in the cell while they stand on it."""
		obj, document = self._here()
		asked = self._counting(False)
		for row in (3, 4, 5):
			document.row = row
			self.band.recheck()
		self.assertEqual(len(asked), 3)

	def test_aTableThatGainedRowsIsReadAgainWhereItSaysSoExactly(self):
		"""**Rows appearing under a stationary reader.** A formula fills down, a query
		refreshes, and the source keeps the count it was made with — so the new rows cannot be
		panned into at all. The check compared columns and never rows.

		Only where the count is a fact: a list view's grows as the platform builds it and says
		nothing about the list changing. See `flowObjectTable.ObjectTable.rowCountIsExact`."""
		obj, document = self._here()
		plan = self.band.columnPlan()
		document.rowCountIsExact = True
		document.rows.append([f"r21c{n}" for n in range(1, len(document.rows[0]) + 1)])
		self.band.recheck()
		self.assertIsNot(self.band.columnPlan(), plan, "the layout was not made again")

	def test_butWalkingBelowTheDataIsNotTheTableChanging(self):
		"""**A review caught the layout being rebuilt on every arrow key.** A grid reaches at
		least as far as the reader, so that the row they are standing in is part of the table
		— and read as a count of the table, that moves each time they step below the data. On
		a blank sheet, D20 to D21 and back again was two rebuilds: measured again, planned
		again, and their page and panned rows put back, for a sheet nothing had happened to.
		"""
		obj, document = self._here()
		document.rowCountIsExact = True
		plan = self.band.columnPlan()
		control = self.band.controller
		for row in (len(document.rows) + 1, len(document.rows) + 2, len(document.rows) + 1):
			document.reach = row
			document.row = row
			self.band.recheck()
		self.assertIs(self.band.controller, control, "the flow was made again")
		self.assertIs(self.band.columnPlan(), plan, "the layout was made again")

	def test_butAListThatIsStillBeingBuiltIsLeftAlone(self):
		"""File Explorer answered fourteen rows while the reader stood on item fifty two of
		seventy nine. A count that moves as the platform builds is not a table that changed."""
		obj, document = self._here()
		plan = self.band.columnPlan()
		document.rows.append([f"r21c{n}" for n in range(1, len(document.rows[0]) + 1)])
		self.band.recheck()
		self.assertIs(self.band.columnPlan(), plan, "the layout was made again")

	def test_aRebuildUnderThemKeepsThePageTheyWereOn(self):
		"""A rebuild is not a decision the reader made: the table changed shape while they were
		reading page two, and page two is still where they were reading. A review measured what
		coming back on page one costs — two writes to the display for one rebuild, page one and
		then theirs, both sent to the driver."""
		obj, document = self._here()
		self.band.turnColumnPage(1)
		for row in document.rows:
			row.append("new")
		self.band.recheck()
		self.assertEqual(self.band.columnPlan().page, 1)

	def test_andTheDisplayIsWrittenOnceRatherThanTwice(self):
		"""A review measured the old repair: the band was built on page one, attached — which
		is a write the driver sends — and only then moved to the reader's page. On a Monarch
		that is the display visibly flicking home and back on every rebuild. The page and the
		rows are applied inside the build now, before anything is attached."""
		obj, document = self._here()
		self.band.turnColumnPage(1)
		onTheirPage = " ".join(self.band.controller.describeRows())
		seen = []
		real = self.handler.update
		self.handler.update = lambda: seen.append(
			" ".join(self.band.controller.describeRows()) if self.band.controller else "",
		) or real()
		self.addCleanup(setattr, self.handler, "update", real)
		for row in document.rows:
			row.append("new")
		self.band.recheck()
		self.assertTrue(seen, "nothing was written at all")
		for frame in seen:
			self.assertNotIn(
				"r1c3",
				frame,
				"page one reached the display on the way to the reader's page",
			)
		self.assertIn("r1c7", onTheirPage, "the reader was not on the second page to begin with")

	def test_andTheRowsTheyHadPannedTo(self):
		"""The other axis, and the one the reader felt as "cannot pan": a new controller enters
		at the caret, so a pan down was undone by the next rebuild."""
		obj, document = self._here()
		before = self.band.controller.window.topBlockId().bookmark
		self.assertTrue(self.band.controller.panForward())
		panned = self.band.controller.window.topBlockId().bookmark
		self.assertNotEqual(panned, before)
		for row in document.rows:
			row.append("new")
		self.band.recheck()
		self.assertEqual(self.band.controller.window.topBlockId().bookmark, panned)

	def test_butAValueFoundUnderThemTakesThemToIt(self):
		"""The one rebuild the reader did cause. They are standing in the cell that turned out
		to hold something, so the band is theirs to be shown."""
		obj, document = self._here()
		self.band.turnColumnPage(1)
		self._counting(True)
		document.row = 3
		self.band.recheck()
		self.assertEqual(self.band.columnPlan().page, 0)


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
		CONFIG["flowTableHeadersMode"] = bmConfig.NEVER
		self._midTable()
		self.assertIsNone(self.band.controller.pinned)
		self.assertEqual(self.band.controller.window.numRows, ROWS)

	def test_nvdaBeingToldNotToReportColumnHeadersTurnsItOffToo(self):
		"""The reader has already said what they want from a table's headers, in NVDA's own
		Document Formatting settings, and those apply outside browse mode as well."""
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS.value
		self._midTable()
		self.assertIsNone(self.band.controller.pinned)

	def test_andTheReaderCanStillAskForItInBraille(self):
		"""What is turned off for speech is usually the repetition — "From, Received" before
		every message — and a row drawn once at the top of the display costs none of that."""
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		CONFIG["flowTableHeadersMode"] = bmConfig.ALWAYS
		self._midTable()
		self.assertIsNotNone(self.band.controller.pinned)

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
		patches.liveUpdatesInstalled = lambda document=None: installed
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


class TestFinishingAFillTheBudgetCutShort(TableBandTestCase):
	"""The budget exists so one keypress cannot block the reader for a second on a heavy page.
	A quarter second of Outlook's Word view buys about five blocks where the band wants eight,
	and the band showed two rows saying "more, not fetched" and kept showing them: the reader
	was told the content existed and given no way to reach it.

	So the answer to running out is to come back, not to raise the ceiling."""

	def _short(self):
		""":return: a band whose window is short of content the source still has."""
		self._inTable()
		self.band.layOutTable()
		control = self.band.controller
		control.window.setEdge(Edge.AFTER, EdgeState.DEFERRED)
		callLaterQueue.pending.clear()
		self.band._cancelFill()
		return control

	def test_aBandLeftShortAsksToTryAgain(self):
		self._short()
		self.band.recheck()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_aBandThatIsNotShortDoesNot(self):
		"""Asked on every redraw, so it has to cost one attribute and stay quiet."""
		self._inTable()
		self.band.layOutTable()
		self.band._cancelFill()
		callLaterQueue.pending.clear()
		self.band.recheck()
		self.assertEqual(callLaterQueue.pending, [])

	def test_theEndOfTheDocumentIsNotShort(self):
		"""The two look the same to a reader — rows with nothing in them — and are opposites:
		one is the document finishing, the other is this add-on giving up part way."""
		self._inTable()
		self.band.layOutTable()
		self.band.controller.window.setEdge(Edge.AFTER, EdgeState.END)
		self.assertFalse(self.band.controller.hasMoreToFetch)

	def test_onlyOnePassIsEverPending(self):
		self._short()
		self.band._scheduleFill()
		self.band._scheduleFill()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_aPassThatAddsNothingEndsTheChain(self):
		"""A document that will not answer is asked twice and then left alone."""
		control = self._short()
		control.fill = lambda: False
		self.band._fillMore()
		self.assertEqual(callLaterQueue.pending, [])

	def test_aPassThatAddsSomethingAsksForAnother(self):
		control = self._short()
		control.fill = lambda: control.window.setEdge(Edge.AFTER, EdgeState.DEFERRED) or True
		control.cells = iter([[1], [2], [2]]).__next__
		self.band._fillMore()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_aPassWhoseRowsLandWhereTheReaderCannotFeelThemAsksForAnother(self):
		"""**What was added, not what is showing.** The rows a cut-short band is missing are
		usually rows that do not show: content fetched above the window is what the reader
		scrolls up into, and it arrives off the top of the band by definition. Chaining on the
		display changing stopped on the first of those — on the one document slow enough to
		need the chain at all."""
		control = self._short()
		control.fill = lambda: control.window.setEdge(Edge.AFTER, EdgeState.DEFERRED) or True
		control.cells = lambda: [1]
		self.band._fillMore()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_butTheDisplayIsStillOnlyWrittenWhenItChanged(self):
		"""The bargain that keeps a reading hand still, and the reason the cells are compared
		at all."""
		control = self._short()
		control.fill = lambda: True
		control.cells = lambda: [1]
		refreshes = []
		spy = type("SpySegment", (), {"refresh": lambda inner: refreshes.append(True)})()
		self.band.segment = lambda: spy
		self.band._fillMore()
		self.assertEqual(refreshes, [])


class TestKeepingUpWithoutATable(TableBandTestCase):
	"""Every path that shows something starts the chain, not only the table path. A reader
	with a refresh interval set got one on a watchlist laid out in columns and none on the
	same page read by line, and the promised fallback went the same way."""

	def _readingThePage(self):
		"""Show the page the ordinary way, by line, with no column layout on it."""
		self._inTable()
		callLaterQueue.pending.clear()
		self.assertTrue(self.band.refresh(force=True))

	def test_anOrdinaryDocumentGetsItsTimer(self):
		CONFIG["flowLiveSeconds"] = 5
		self._readingThePage()
		self.assertEqual([timer.milliseconds for timer in callLaterQueue.pending], [5000])

	def test_theFallbackStartsForOneToo(self):
		"""Zero means wait to be told, and the fallback is what makes that safe."""
		CONFIG["flowLiveSeconds"] = 0
		self._readingThePage()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_showingSomethingElseDoesNotLeaveTheOldPassPending(self):
		"""Its delay was chosen for what was being read before, and its first act would be to
		read this instead."""
		self._readingThePage()
		self.assertEqual(len(callLaterQueue.pending), 1)
		self.band.layOutTable()
		self.assertEqual(len(callLaterQueue.pending), 1)

	def test_aDocumentBeingWrittenInIsNotAskedAgain(self):
		"""The one thing a live pass can never answer. Every position in a document being
		typed into moves on every keystroke, which is why `_rereadBlocks` refuses one
		outright — but the pass was still being scheduled, so a markdown file open in VSCode
		took the fallback poll and ran it a hundred and eighteen times in one sitting, each
		pass re-rendering the whole band to arrive at the same cells."""
		CONFIG["flowLiveSeconds"] = 0
		self._readingThePage()
		self.band._cancelLiveRead()
		self.band.controller.source.writing = True
		self.band._scheduleLiveRead()
		self.assertEqual(callLaterQueue.pending, [])

	def test_notEvenForAReaderWhoAskedForAnInterval(self):
		"""Their number decides how often, not whether there is anything to read."""
		CONFIG["flowLiveSeconds"] = 5
		self._readingThePage()
		self.band._cancelLiveRead()
		self.band.controller.source.writing = True
		self.band._scheduleLiveRead()
		self.assertEqual(callLaterQueue.pending, [])


class TestNoticingAChangeThatWritesNothing(TableBandTestCase):
	"""The live pass writes the display only when the cells came out different, and a column
	appended beyond the page being drawn changes none of them — so a table that grew was
	invisible until something else caused a redraw."""

	def test_aColumnAppearingIsNoticedByALivePass(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		before = len(self.band.columnPlan().columns)
		for line in document.rows:
			line.append("new")
		self.band._refreshLiveContent()
		self.assertEqual(len(self.band.columnPlan().columns), before + 1)

	def test_aTableThatDidNotChangeIsNotRebuiltByOne(self):
		self._inTable()
		self.band.layOutTable()
		plan = self.band.columnPlan()
		self.band._refreshLiveContent()
		self.assertIs(self.band.columnPlan(), plan)


class TestThePinnedHeaderKeepsUpToo(TableBandTestCase):
	"""It is outside the window, so the re-read that walks the window's blocks never touched
	it: a header renamed while the reader watched went on saying what it used to."""

	def test_aRenamedHeaderReachesThePinnedRow(self):
		obj, document = self._inTable()
		self.band.layOutTable()
		self.assertIn("Symbol", self.band.controller.describeRows()[0])
		document.rows[0][0] = "Ticker"
		self.band._refreshLiveContent()
		self.assertIn("Ticker", self.band.controller.describeRows()[0])

	def test_anUnchangedHeaderIsNotWritten(self):
		self._inTable()
		self.band.layOutTable()
		segment = self.band.segment()
		written = []
		original = segment.refresh
		segment.refresh = lambda *args, **kwargs: (written.append(1), original(*args, **kwargs))[1]
		self.addCleanup(setattr, segment, "refresh", original)
		self.band._refreshLiveContent()
		self.assertEqual(written, [])


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


class TestAColumnThatIsEmptyOnlyInTheSample(TableBandTestCase):
	"""Measuring reads a bounded sample, so a column empty throughout it is only tentatively
	empty. Left as a decision, a value further down the column could never be reached: the
	column is not drawn, arriving at it changed nothing, and the cursor vanished because the
	row on the band had no cell there."""

	def _sparse(self, valueRow=11, col=1):
		"""A column blank in the header and in every sampled row, with a value further down."""
		header = ["", "Last", "Change", "%Chg"]
		body = [["", f"{n}.00", f"+{n}", f"+{n}%"] for n in range(1, 20)]
		body[valueRow - 2][0] = "IMPORTANT"
		return self._inTable(rows=[header, *body], row=1, col=col)

	def test_theSampleLeavesItOut(self):
		"""Which is right for what the sample saw, and is where the trouble starts."""
		self._sparse()
		self.band.layOutTable()
		self.assertIn(1, self.band.columnPlan().omitted)

	def test_reachingTheValueBringsTheColumnBack(self):
		obj, document = self._sparse()
		self.band.layOutTable()
		document.row, document.col = 11, 1
		self.band.recheck()
		self.assertIsNotNone(self.band.columnPlan().pageOf(1))

	def test_theValueIsThenOnTheBand(self):
		obj, document = self._sparse()
		self.band.layOutTable()
		document.row, document.col = 11, 1
		self.band.recheck()
		self.assertIn("IMPORTANT", " ".join(self.band.controller.describeRows()))

	def test_theCursorComesBackWithIt(self):
		"""The symptom that would have been felt: no cell there, so no cursor."""
		obj, document = self._sparse()
		self.band.layOutTable()
		document.row, document.col = 11, 1
		self.band.recheck()
		self.assertIsNotNone(self.band.controller.cursorCell())

	def test_anEmptyCellStillLeavesTheLayoutAlone(self):
		"""The column of unreadable icons the reader actually has. Rebuilding for it is what
		left the band unable to pan."""
		obj, document = self._sparse()
		self.band.layOutTable()
		plan = self.band.columnPlan()
		document.row, document.col = 4, 1
		self.band.recheck()
		self.assertIs(self.band.columnPlan(), plan)

	def test_aValueAppearingUnderAStillReaderIsNotChasedAnyMore(self):
		"""This used to be chased on every live pass, and hardware said what that costs. The
		reader's watchlist has a blank first column, quick navigation lands them in it, and the
		page fires change notices continuously: each pass spent a 44 ms search of the document
		on a cell that had not changed, so panning stalled and a page turn took five times as
		long as the same turn made from the column beside it. The cell is looked into once."""
		obj, document = self._sparse()
		self.band.layOutTable()
		document.row, document.col = 4, 1
		self.band.recheck()
		self.assertIsNone(self.band.columnPlan().pageOf(1))
		document.rows[3][0] = "NOW LIVE"
		self.band._refreshLiveContent()
		self.assertIsNone(self.band.columnPlan().pageOf(1))

	def test_andIsSeenAsSoonAsTheyMoveOffItAndBack(self):
		"""Which is what is given up by asking once, and how the reader asks again."""
		obj, document = self._sparse()
		self.band.layOutTable()
		document.row, document.col = 4, 1
		self.band.recheck()
		document.rows[3][0] = "NOW LIVE"
		document.row = 5
		self.band.recheck()
		document.row = 4
		self.band.recheck()
		self.assertIsNotNone(self.band.columnPlan().pageOf(1))

	def test_sittingInAnEmptyCellIsAskedAboutOnce(self):
		"""Asking again on every redraw would be a search of the document per draw for a
		column nobody can read."""
		obj, document = self._sparse()
		self.band.layOutTable()
		document.row, document.col = 4, 1
		self.band.recheck()
		document.reads.clear()
		self.band.recheck()
		self.band.recheck()
		self.assertEqual(document.reads, [])


class TestWhichTableTheBandIsLayingOut(TableBandTestCase):
	"""What a pin asks as it is made, so that pinning a table the reader is already reading in
	columns pins it in columns. The band's own request is dropped the moment they leave the
	table, and pinning it is often the prelude to leaving."""

	def test_itSaysSoForTheTableItIsLayingOut(self):
		obj, _document = self._inTable()
		self.band.layOutTable()
		self.assertTrue(self.band.wantsColumnsFor(obj))

	def test_notForATableItIsNot(self):
		obj, _document = self._inTable()
		self.assertFalse(self.band.wantsColumnsFor(obj))

	def test_notForADifferentTable(self):
		obj, _document = self._inTable()
		self.band.layOutTable()
		other = FakeNavigatorObject(
			"another page",
			treeInterceptor=FakeTableDocument(
				[list(line) for line in WATCHLIST],
				row=2,
			),
		)
		self.assertFalse(self.band.wantsColumnsFor(other))

	def test_notForSomethingThatIsNotATable(self):
		self._inTable()
		self.band.layOutTable()
		self.assertFalse(self.band.wantsColumnsFor(FakeNavigatorObject("a heading")))

	def test_turningItOffTakesTheAnswerWithIt(self):
		obj, _document = self._inTable()
		self.band.layOutTable()
		self.band.clearTable()
		self.assertFalse(self.band.wantsColumnsFor(obj))


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


class TestABandThatWouldPinNothing(TableBandTestCase):
	"""A list view declares no header row and its first item is a file rather than a heading,
	so there is nothing to pin. The row the header would have cost was being taken anyway: the
	band's height is decided before the header is asked for, because the height is what
	everything below is planned against.
	"""

	FILES = [
		["report.docx", "Word document", "12 KB"],
		["notes.md", "Markdown", "3 KB"],
		["powerpnt.py", "Python Source File", "59 KB"],
	]

	def _inList(self, headers=None, row=1):
		""":return: the file list, with the reader on one of its items."""
		view = FakeGrid(self.FILES, headers=headers or [])
		item = view.item(row)
		self.api.getFocusObject = lambda: item
		self.api.getNavigatorObject = lambda: item
		self.addCleanup(setattr, self.api, "getNavigatorObject", self.api.getNavigatorObject)
		self.band._follow()
		self.band.layOutTable()
		return view

	def test_theBandKeepsTheRowTheHeaderWouldHaveCost(self):
		self._inList(row=2)
		self.assertIsNone(self.band.controller.pinned)
		self.assertEqual(self.band.controller.window.numRows, ROWS)

	def test_andEveryRowOfTheListIsStillInTheStream(self):
		"""The other half of the same mistake: row one is skipped only where row one is what was
		pinned, so a list whose first item is a file would have lost that file."""
		self._inList()
		self.assertIn("report.docx", " ".join(self.band.controller.describeRows()))

	def test_aTableWithHeadersStillSpendsTheRow(self):
		self._inList(headers=["Name", "Type", "Size"])
		self.assertIsNotNone(self.band.controller.pinned)
		self.assertEqual(self.band.controller.window.numRows, ROWS - 1)


class TestWhatTheDryRunsTitleIsAbout(TableBandTestCase):
	"""The title is about the dry run's *own* flow, and the two part company exactly where it
	matters.

	In Excel the band reads the worksheet as a table and the ordinary reading of the same
	place is a tree interceptor with no text in it — so a report headed "nothing here can be
	flowed" sat on top of a full account of a table the reader could feel under their fingers.
	The reader read the title, and the title was talking about something else.
	"""

	def _onASheet(self):
		"""A band reading a grid by coordinate, over an object that has no text of its own.

		Which is the Excel shape and the shape that showed this: the band reads the worksheet
		as a table, and the ordinary reading of the same place has nothing in it at all.
		"""
		from .test_flowObjectTable import FakeSheet

		sheet = FakeSheet(
			rows=[["Date", "Al", "Deborah"]] + [[f"{n}/4", "84", "128"] for n in range(1, 6)],
			at=(2, 1),
			headers={1: "Date", 2: "Al", 3: "Deborah"},
		)
		cell = FakeNavigatorObject("a cell", role="TABLECELL")
		cell.rowNumber, cell.columnNumber = 2, 1
		cell.brlMultilineSheet = lambda: sheet
		self.api.getFocusObject = lambda: cell
		self.api.getNavigatorObject = lambda: cell
		self.band._follow()
		self.assertTrue(self.band.layOutTable())

	def _dryRun(self):
		from brlMultiline import flowDryRun

		return flowDryRun.dryRun(handler=self.handler, band=self.band)

	def test_theTitleSaysTheBandIsReadingATable(self):
		self._onASheet()
		said = self._dryRun()[0]
		self.assertIn("nothing here can be flowed on its own", said)
		self.assertIn("the band is reading a table in columns", said)

	def test_andWhichColumnsOfIt(self):
		self._onASheet()
		first, last, total = self.band.columnPlan().whereItIs
		self.assertIn(f"({first} to {last} of {total})", self._dryRun()[0])

	def test_andTheReportCarriesTheNotesTheLayoutWasMadeWith(self):
		"""**Only a failure used to show these**, which is a hole exactly where a hard fault
		lives: a layout that comes out *wrong* is not a layout that failed, so nothing printed
		the steps that made it. A worksheet losing its header row was diagnosed for three
		rounds without the one line saying whether the headings had been found."""
		self._onASheet()
		said = " ".join(self._dryRun())
		self.assertIn("Band layout, as it was made:", said)
		self.assertIn("Columns that name themselves:", said)

	def test_andSaysNothingOfTheKindWhereTheBandHasNothing(self):
		self._elsewhere()
		self.band.clearTable()
		self.assertNotIn("a table in columns", self._dryRun()[0])


class TestAPinnedHeaderThatCannotBeReadAgain(TableBandTestCase):
	"""A header is a property of the table, so "I could not read it just now" is not "this
	table has no headers".

	`headerBlock` answers None for both, and it swallows its own failures to do it — so one
	unlucky read took the header row off the display, and nothing put it back until the layout
	was made again. On a spreadsheet every read can fail: a cell fetch crosses a process
	boundary, and NVDA's watchdog cancels every one of them when the core is busy.
	"""

	def _reading(self):
		obj, document = self._inTable(rows=WIDE, row=2, col=1)
		self.assertTrue(self.band.layOutTable())
		self.assertIsNotNone(self.band.controller.pinnedBlock)
		return obj, document

	def test_theReportSaysWhichColumnsNameThemselves(self):
		"""Whether a table names its columns decides whether a row of the band is spent on a
		header, and when it came out wrong there was nothing in the log between "the cells
		know their headings" and "this table declares no headers"."""
		self._inTable(
			rows=[["Symbol", "Last"], ["AAPL", "182.50"]],
			row=2,
			col=1,
			columnHeaders={1: "Symbol", 2: "Last"},
		)
		self.band.layOutTable()
		said = " ".join(self.band.tableNotes)
		self.assertIn("Columns that name themselves:", said)
		self.assertIn("'Symbol'", said)

	def test_andSaysNoneWhereNoneDo(self):
		self._inTable(rows=[["a", "b"], ["c", "d"]], row=2, col=1)
		self.band.layOutTable()
		self.assertIn(
			"Columns that name themselves: none",
			" ".join(self.band.tableNotes),
		)

	def _sayNothing(self):
		"""Make the source answer None to every request for its header row."""
		source = self.band.controller.source
		real = source.headerBlock
		source.headerBlock = lambda: None
		self.addCleanup(setattr, source, "headerBlock", real)

	def test_theHeaderStandsWhenALiveReadCannotFindIt(self):
		self._reading()
		held = self.band.controller.pinnedBlock
		self._sayNothing()
		self.band._rereadPinnedRow()
		self.assertIs(self.band.controller.pinnedBlock, held)

	def test_andWhenTheHeaderReadRaises(self):
		self._reading()
		held = self.band.controller.pinnedBlock

		def explode():
			raise RuntimeError("gone")

		source = self.band.controller.source
		real = source.headerBlock
		source.headerBlock = explode
		self.addCleanup(setattr, source, "headerBlock", real)
		self.band._rereadPinnedRow()
		self.assertIs(self.band.controller.pinnedBlock, held)

	def test_andWhenAPageOfColumnsHasNoNamesOfItsOwn(self):
		"""A page of unnamed columns is not a table without headers. What stands is the row
		that was there, which is at worst out of date."""
		self._reading()
		held = self.band.controller.pinnedBlock
		self._sayNothing()
		self.band.turnColumnPage(1)
		self.assertIs(self.band.controller.pinnedBlock, held)

	def test_aHeaderThatDoesReadIsStillTakenUp(self):
		"""The whole point of asking again: a heading renamed while the reader watched."""
		obj, document = self._reading()
		document.rows[0][1] = "Newest"
		self.band._rereadPinnedRow()
		self.assertIn("Newest", " ".join(self.band.controller.describeRows()))

	def test_andTheReportSaysWhenOneIsHeldAndNotDrawn(self):
		"""A missing line is not evidence: a report that shows nothing where the header
		should be reads the same whether the band never had one or lost it."""
		self._reading()
		self.band.controller.pinned = None
		self.assertIn(
			"pinned: held but not drawn",
			" ".join(self.band.controller.describeRows()),
		)


class TestTheFocusMovingInsideATable(TableBandTestCase):
	"""In a spreadsheet or a list, every arrow key is a focus change.

	Which is the difference from browse mode, where the focus stays on the page while the
	caret walks it and a table was only ever redrawn through the live pass. A focus change
	arrives as `force` — "make this reading again" — and for a table that was exactly wrong:
	the layout was planned afresh on every keypress and the window was placed afresh with it,
	so the row the reader had just moved to went to the **top** of the band with the rest of
	the table below it.

	Reported from an Excel worksheet as a display that jumped a page at a time on every
	arrow, and, at the first row past the data, as a band that went blank but for its headers
	— a window entered afresh at the last row has nothing after it to fill with.
	"""

	TALL = [["Date", "Al", "Deborah", "Dan", "Sarah", "Tania", "Tonya"]] + [
		[f"{month} April 2026 x", "84", "1289999", "55.5", "171", "107", "99"]
		for month in range(1, 10)
	]

	def _walk(self, rows=None, first=2):
		""":return: a table laid out in columns, and a way to move the focus down it.

		The rows are wide enough that each takes two rows of the band, which is what makes
		the difference visible: a band of eight shows three and a half of them, so the reader
		runs out of band after three keypresses.
		"""
		obj, document = self._inTable(rows=rows or self.TALL, row=first)
		self.assertTrue(self.band.layOutTable())

		def moveTo(row, column=1):
			document.row = row
			document.col = column
			# A new object each time, as a new cell or a new row of a list is. What NVDA
			# hands the band is the regions it built for the new focus; `handleFocusRegions`
			# is where those arrive.
			moved = FakeNavigatorObject("a page", treeInterceptor=document)
			self.api.getFocusObject = lambda found=moved: found
			self.api.getNavigatorObject = lambda found=moved: found
			self.band.showObject(moved, force=True, focusMoved=True)
			return moved

		return document, moveTo

	def _holds(self):
		return [item.blockId.bookmark for item in self.band.controller.window.blocks]

	def _anchor(self):
		anchor = self.band.controller.window.anchor
		return (anchor.blockId.bookmark, anchor.entry.value)

	def test_aRowPastTheBandComesOnAtTheBottom(self):
		_document, moveTo = self._walk()
		for row in (3, 4, 5, 6):
			moveTo(row)
		self.assertEqual(self._anchor(), (6, "bottom"))

	def test_andWhatWasAboveThemStaysAbove(self):
		"""The whole complaint: the band jumped instead of scrolling."""
		_document, moveTo = self._walk()
		for row in (3, 4, 5, 6):
			moveTo(row)
		self.assertEqual(self._holds()[0], 2)

	def test_aRowStillOnTheBandMovesNothingAtAll(self):
		_document, moveTo = self._walk()
		before = self._anchor()
		moveTo(3)
		self.assertEqual(self._anchor(), before)

	def test_theLastRowLeavesTheRestOfTheTableOnTheBand(self):
		"""The blank row under a sheet's data was a blank band: a window placed afresh at the
		last row has nothing after it, so nothing filled in below and nothing was left above."""
		_document, moveTo = self._walk()
		for row in range(3, 11):
			moveTo(row)
		self.assertEqual(self.band.controller.activeBlockId.bookmark, 10)
		self.assertGreater(len(self._holds()), 1)

	def test_theLayoutIsNotPlannedAgainOnEveryKeypress(self):
		"""Which is what a rebuild costs: every column of a bandful of rows measured again,
		and on a spreadsheet that is a cross-process read per cell."""
		from brlMultiline import flowBuild

		_document, moveTo = self._walk()
		counted = []
		real = flowBuild.flowTableSource.measure
		flowBuild.flowTableSource.measure = lambda *args, **kwargs: (
			counted.append(1) or real(*args, **kwargs)
		)
		self.addCleanup(setattr, flowBuild.flowTableSource, "measure", real)
		for row in (3, 4, 5, 6):
			moveTo(row)
		self.assertEqual(counted, [])

	def test_butMovingIntoAnotherTableStillBuildsOne(self):
		"""The layout must not outlive its table, and `isStillHere` is what says so."""
		_document, moveTo = self._walk()
		moveTo(4)
		other = FakeTableDocument([["A", "B"], ["c", "d"]], tableID=2, row=2, col=1)
		arrived = FakeNavigatorObject("another page", treeInterceptor=other)
		self.api.getFocusObject = lambda: arrived
		self.api.getNavigatorObject = lambda: arrived
		self.band.showObject(arrived, force=True, focusMoved=True)
		self.assertFalse(self.band._readingATable())

	def test_theWholeOfANewRowComesOnAndNotJustItsFirstLine(self):
		"""A row that wraps is one row of the table and the reader is standing on all of it.
		Brought on by the line the cursor is on, a record two lines tall arrived with its
		first line on the bottom row and its second off the band — so the values in the
		columns that had wrapped were the ones they could not read."""
		_document, moveTo = self._walk()
		for row in (3, 4, 5, 6):
			moveTo(row)
		rows = [
			(row.blockId.bookmark, row.rowIndex)
			for row in self.band.controller.window.visibleRows()
			if getattr(row, "blockId", None) is not None
		]
		self.assertIn((6, 0), rows)
		self.assertIn((6, 1), rows)

	def test_andTheColumnIsFollowedTooWhenTheFocusIsWhatMoved(self):
		"""In browse mode a caret move reaches the live pass, which follows both axes. In a
		spreadsheet or a list it arrives as a focus change instead, and that path moved the
		source and then never asked which column the reader had gone to — so the band
		followed them down the rows and left them behind across the columns."""
		obj, document = self._inTable(rows=WIDE, row=2, col=1)
		self.assertTrue(self.band.layOutTable())
		beyond = self.band.columnPlan().shown + 1
		document.col = beyond
		moved = FakeNavigatorObject("a page", treeInterceptor=document)
		self.api.getFocusObject = lambda: moved
		self.api.getNavigatorObject = lambda: moved
		self.band.showObject(moved, force=True, focusMoved=True)
		self.assertTrue(self.band.columnPlan().showing(beyond))

	def test_andTheReaderKeepsACursorOnTheBlankRow(self):
		"""Every column of it marks a position, so the cursor says which one they are in.
		Without that the row was unmarked and they were left with speech."""
		rows = [line[:] for line in self.TALL] + [["", "", "", "", "", "", ""]]
		_document, moveTo = self._walk(rows=rows)
		moveTo(11, column=2)
		self.assertIsNotNone(self.band.controller.cursorCell())


class TestATableThatRepeatsNoColumn(TableBandTestCase):
	"""The reader's own ask: **per table, not per installation.**

	Whether a column is repeated at the left of every later page was a setting for all tables
	at once, and which column it is was decided in the designer per table. So a reader who
	wanted one table's rows to start at the left had to turn the repeat off everywhere. The
	record has always had room for the answer — `pinKey` — and the planner has always honoured
	it; nothing but the settings dialog wrote it.
	"""

	def setUp(self):
		super().setUp()
		from brlMultiline import flowTableLayouts

		self.layouts = flowTableLayouts

	def _wide(self):
		"""A table too wide for one page, so that there is a later page to repeat onto."""
		return [[f"r{row}c{column}" for column in range(1, 12)] for row in range(1, 6)]

	def _repeatsNothing(self):
		return self.layouts.TableLayout(pinKey=self.layouts.NO)

	def test_theBandRepeatsOneUntilTheReaderSaysOtherwise(self):
		self._inTable(rows=self._wide())
		self.assertTrue(self.band.layOutTable())
		self.assertIsNotNone(self.band.columnPlan().keyColumn)

	def test_andArrangingItAwayTakesItOffTheDisplay(self):
		"""Applied where they are standing, before anything is saved."""
		self._inTable(rows=self._wide())
		self.band.layOutTable()
		self.assertTrue(self.band.arrangeTable(self._repeatsNothing()))
		self.assertIsNone(self.band.columnPlan().keyColumn)

	def test_andTheOtherTablesAreNotToldAnything(self):
		"""Which is the whole difference from the setting: this is one table's decision."""
		self._inTable(rows=self._wide())
		self.band.layOutTable()
		self.band.arrangeTable(self._repeatsNothing())
		self.band.clearTable()
		self._inTable(rows=self._wide(), tableID=2)
		self.assertTrue(self.band.layOutTable())
		self.assertIsNotNone(self.band.columnPlan().keyColumn)


class TestACancelledReadingIsNotAnAbsentTable(TableBandTestCase):
	"""**Recognising the table happens before anything is built**, and it is a read of the
	document like any other. Cancelled, it used to answer "there is no table here" — which is
	the one thing the reader can see is untrue, and it sends them looking in the wrong place.

	A review found this after the boundary was put around the build: the build was never
	reached. The reader is told the table could not be *read*, which is a moment that has
	passed rather than a display too narrow, and asking again is the thing to do.
	"""

	def _cancelling(self):
		"""Make recognising the table raise what a cancelled COM call raises."""
		from brlMultiline import flowTableSource

		real = flowTableSource.tableAt

		def stop(obj):
			raise CallCancelled("NVDA stopped waiting")

		flowTableSource.tableAt = stop
		self.addCleanup(setattr, flowTableSource, "tableAt", real)

	def test_theCommandSaysTheTableCouldNotBeRead(self):
		self._inTable()
		self._cancelling()
		self.assertFalse(self.band.layOutTable())
		self.assertEqual(self.band.tableProblem, NOT_READ)

	def test_andNotThatThereIsNoTable(self):
		self._inTable()
		self._cancelling()
		self.band.layOutTable()
		self.assertNotEqual(self.band.tableProblem, NO_TABLE)

	def test_andARedrawThatIsCancelledDoesNotRaiseAtTheReader(self):
		"""The same reads happen on every redraw, where there is no command to report to and
		nothing to be done but leave the band as it was."""
		self._inTable()
		self.assertTrue(self.band.layOutTable())
		self._cancelling()
		self.band.refresh(force=True)
		self.assertTrue(
			any(isinstance(note, Unreadable) for note in self.band.tableNotes),
			"nothing was left saying why",
		)


class TestTellingTheThreeRefusalsApart(TableBandTestCase):
	"""A table that was not recognised and a table that was recognised and could not be laid
	out were the same sentence: "not in a table". They send the reader to look in two
	different places, and the one they were sent to — is this control a table at all — is the
	part that had worked.

	Reported from hardware, in File Explorer's Details view. The command refused and nothing
	anywhere said which of its four steps had done the refusing.

	**And then a third, from an Excel sheet.** "Could not be laid out in columns" is about
	arithmetic — these columns, this band, no arrangement fits — and it was what was said
	about a worksheet where every read had been cancelled before a single width was measured.
	Nothing was wrong with the arrangement, because there had been nothing to arrange, and the
	reader was sent to the layout designer for a table nothing could be read out of.
	"""

	def _press(self):
		"""The command as NVDA runs it, on a plugin that has this band."""
		from brlMultiline import GlobalPlugin

		spokenMessages.clear()
		GlobalPlugin.script_flowTableColumns(CommandHolder(self.band), None)
		return list(spokenMessages)

	def _unreadable(self):
		""":return: a table the reader is standing in whose every cell is a hole.

		Which is what a cancelled worksheet looks like from here: a table that says it has
		columns, and not one of them answering.
		"""
		return self._inTable(rows=[[None, None], [None, None]])

	def _unlayoutable(self):
		""":return: a table that reads perfectly onto a band with no room for a column.

		Two cells wide, which is under `flowTable.MIN_COLUMN_CELLS` — below that a column
		stops being a value and becomes a fragment of one, so the planner refuses rather than
		drawing one digit of each. Every cell here is readable, which is the whole difference
		from `_unreadable`: this is the arithmetic saying no.
		"""
		import braille

		from brlMultiline.flowBand import FlowBand

		self.handler = FakeHandler(ROWS, 2)
		braille.handler = self.handler
		self.container = containerWithBand(self.handler, numRows=ROWS, numCols=2)
		self.handler.mainBuffer = self.handler.buffer = self.container
		self.band = FlowBand(FakePlugin(self.container))
		return self._inTable(rows=[["AAPL", "182.50"], ["MSFT", "410.10"]])

	def test_aTableThatCannotBeLaidOutSaysThatInstead(self):
		self._unlayoutable()
		said = self._press()
		self.assertFalse(any("Not in a table" == message for message in said))
		self.assertTrue(any("could not be laid out" in message for message in said))

	def test_aTableThatCouldNotBeReadSaysThatInstead(self):
		"""And says it *apart* from the arithmetic, because it is a different thing to do
		about: often a moment that has passed and is worth asking for again."""
		self._unreadable()
		said = self._press()
		self.assertFalse(any("could not be laid out" in message for message in said))
		self.assertTrue(any("Nothing could be read" in message for message in said))

	def test_andTheBandSaysWhichRefusalItWas(self):
		from brlMultiline.flowBand import NO_LAYOUT, NO_TABLE, NOT_READ

		self._unlayoutable()
		self.assertFalse(self.band.layOutTable())
		self.assertEqual(self.band.tableProblem, NO_LAYOUT)
		self._unreadable()
		self.assertFalse(self.band.layOutTable())
		self.assertEqual(self.band.tableProblem, NOT_READ)
		self._elsewhere()
		self.assertFalse(self.band.layOutTable())
		self.assertEqual(self.band.tableProblem, NO_TABLE)

	def test_aLayoutThatWorkedLeavesNoProblemBehind(self):
		self._inTable()
		self.assertTrue(self.band.layOutTable())
		self.assertIsNone(self.band.tableProblem)

	def test_theStepThatStoppedItIsKept(self):
		"""`buildTableController` says which step it was and nothing outside the dry run had
		ever read it. The reader who is refused is the one person who needs it."""
		self._unlayoutable()
		self.band.layOutTable()
		self.assertTrue(any("column layout" in note for note in self.band.tableNotes))

	def test_andSaysSoWhenNothingCameBackAtAll(self):
		self._unreadable()
		self.band.layOutTable()
		self.assertTrue(any("Nothing was read" in note for note in self.band.tableNotes))

	def test_aReadCancelledMidMeasureIsNotAnEmptyTable(self):
		"""The Excel case exactly. Once the watchdog starts recovering a frozen core, every
		COM call raises this — so a measurement that swallowed it read twenty-one columns as
		empty and the arithmetic got the blame."""
		from brlMultiline import flowBuild
		from brlMultiline.flow import CallCancelled
		from brlMultiline.flowBand import NOT_READ

		self._inTable()

		def cancelled(*args, **kwargs):
			raise CallCancelled("COM call cancelled")

		real = flowBuild.flowTableSource.measure
		flowBuild.flowTableSource.measure = cancelled
		self.addCleanup(setattr, flowBuild.flowTableSource, "measure", real)
		self.assertFalse(self.band.layOutTable())
		self.assertEqual(self.band.tableProblem, NOT_READ)
		self.assertTrue(any("cancelled the reads" in note for note in self.band.tableNotes))


class TestSayingWhichColumnsAreShowing(TableBandTestCase):
	"""Turning to the next page of a wide table says what landed there, and the reader's report
	is about both halves of how it said it.

	**It named the columns after the first row.** A list view declares no header row — its
	first item is a file — and the measurement borrowed row one's text as each column's name
	anyway, so paging File Explorer's Details view read the reader's own first file back to
	them as the names of the columns. The layout already knew better: the source asks whether
	row one is headings before pinning one, and the measurement did not ask at all.

	**And it wrote itself to the display.** `ui.message` speaks *and* brailles, so the sentence
	describing the new page sat on top of the new page until the message timed out — the
	reader's hands were on the answer and were being shown a description of it instead.
	"""

	WIDE_FILES = [[f"file{number}.py", "Python", "12 KB", "Yesterday", "Available"] for number in range(1, 6)]

	def _pageOf(self, view, by=1):
		""":return: what the reader was told, having turned a page of columns."""
		from brlMultiline import GlobalPlugin

		item = view.item(1)
		self.api.getFocusObject = lambda: item
		self.api.getNavigatorObject = lambda: item
		self.addCleanup(setattr, self.api, "getNavigatorObject", self.api.getNavigatorObject)
		self.band._follow()
		self.band.layOutTable()
		holder = CommandHolder(self.band)
		spokenMessages.clear()
		flashedMessages.clear()
		GlobalPlugin._turnColumnPage.__get__(holder)(by)
		return list(spokenMessages)

	def test_aListsColumnsAreNotNamedAfterItsFirstRow(self):
		view = FakeGrid(self.WIDE_FILES, headers=[])
		said = " ".join(self._pageOf(view))
		self.assertNotIn("file1.py", said)

	def test_aColumnWithNoHeadingIsNamedAsAColumn(self):
		"""Rather than as a bare number. Paging Outlook's inbox said "six, seven"."""
		view = FakeGrid(self.WIDE_FILES, headers=[])
		said = " ".join(self._pageOf(view))
		self.assertIn("column", said)

	def test_aListWithHeadingsIsStillNamedByThem(self):
		view = FakeGrid(self.WIDE_FILES, headers=["Name", "Type", "Size", "Modified", "Status"])
		said = " ".join(self._pageOf(view))
		self.assertTrue(any(header in said for header in ("Name", "Type", "Size", "Modified", "Status")))

	def test_theDisplayIsNotWrittenOver(self):
		"""The whole point of the report is that the display is already showing the answer."""
		view = FakeGrid(self.WIDE_FILES, headers=["Name", "Type", "Size", "Modified", "Status"])
		said = self._pageOf(view)
		self.assertTrue(said)
		self.assertEqual(flashedMessages, [])

	def test_norWhenTheLayoutIsTurnedOnOrOff(self):
		"""The same argument: what the sentence describes is what the hands are on."""
		self._inTable()
		holder = CommandHolder(self.band)
		from brlMultiline import GlobalPlugin

		spokenMessages.clear()
		flashedMessages.clear()
		GlobalPlugin.script_flowTableColumns(holder, None)
		GlobalPlugin.script_flowTableColumns(holder, None)
		self.assertTrue(any("columns" in message for message in spokenMessages))
		self.assertEqual(flashedMessages, [])

	def test_butSomethingTheDisplayCannotShowIsStillWritten(self):
		"""A reader who is not listening has only the display, and "there is no table here" is
		not something the display is showing."""
		from brlMultiline import GlobalPlugin

		self._elsewhere()
		self.api.getNavigatorObject = self.api.getFocusObject
		self.addCleanup(setattr, self.api, "getNavigatorObject", self.api.getNavigatorObject)
		spokenMessages.clear()
		flashedMessages.clear()
		GlobalPlugin.script_flowTableColumns(CommandHolder(self.band), None)
		self.assertIn("Not in a table", flashedMessages)



class TestArrangingATableFromTheBand(TableBandTestCase):
	"""The commands a reader uses while exploring a table they have not arranged.

	Working out which columns are worth the display is what the first minute in a table is,
	and it should not need a dialog: hide the column of icons, cut the one whose cells begin
	with six lines of help text from the other end, and start again if it goes wrong.
	"""

	def _press(self, name, gesture=None):
		from brlMultiline import GlobalPlugin

		spokenMessages.clear()
		flashedMessages.clear()
		getattr(GlobalPlugin, name)(CommandHolder(self.band), gesture)
		return [*spokenMessages, *flashedMessages]

	def _laidOut(self, col=1):
		obj, document = self._inTable(row=2, col=col)
		self.assertTrue(self.band.layOutTable())
		return obj, document

	def _drawn(self):
		return [column.index for column in self.band.columnPlan().columns]

	def test_theColumnTheCursorIsInCanBeHidden(self):
		self._laidOut(col=2)
		said = self._press("script_flowTableToggleColumn")
		self.assertNotIn(2, self._drawn())
		self.assertTrue(any("hidden" in message for message in said))

	def test_andShownAgainInTheTablesOwnOrder(self):
		self._laidOut(col=2)
		self._press("script_flowTableToggleColumn")
		self._press("script_flowTableToggleColumn")
		self.assertEqual(self._drawn(), sorted(self._drawn()))
		self.assertIn(2, self._drawn())

	def test_theLastColumnIsNotHidden(self):
		"""A table of no columns is not a layout, it is a blank display."""
		self._laidOut(col=1)
		self.band.showTheseColumns([1])
		said = self._press("script_flowTableToggleColumn")
		self.assertEqual(self._drawn(), [1])
		self.assertTrue(any("only column" in message for message in said))

	def test_cuttingCyclesWrapStartAndEnd(self):
		"""The reported case in one keystroke: the thing that names the row is at the end."""
		from brlMultiline import flowTable

		self._laidOut(col=1)
		said = self._press("script_flowTableCutColumn")
		column = next(item for item in self.band.columnPlan().columns if item.index == 1)
		self.assertEqual(column.overflow, flowTable.TRUNCATE)
		self.assertEqual(column.keep, flowTable.KEEP_START)
		self.assertTrue(any("keeping the start" in message for message in said))
		said = self._press("script_flowTableCutColumn")
		column = next(item for item in self.band.columnPlan().columns if item.index == 1)
		self.assertEqual(column.keep, flowTable.KEEP_END)
		self.assertTrue(any("keeping the end" in message for message in said))
		self._press("script_flowTableCutColumn")
		column = next(item for item in self.band.columnPlan().columns if item.index == 1)
		self.assertEqual(column.overflow, flowTable.WRAP)

	def test_whatIsArrangedIsWhatRememberSaves(self):
		"""So that what the reader feels and what is written down are the same thing."""
		from brlMultiline import flowTableLayouts, flowTableSource

		obj, document = self._laidOut(col=2)
		document.documentConstantIdentifier = "https://example.com/watchlist"
		self._press("script_flowTableToggleColumn")
		self._press("script_rememberTableLayout")
		handle = flowTableSource.tableAt(self.api.getNavigatorObject())
		saved = flowTableLayouts.layoutFor(handle)
		self.assertIsNotNone(saved)
		self.assertNotIn(2, saved.columns)

	def test_andItSurvivesTheNextRedraw(self):
		"""A change to the plan alone would be undone by the next rebuild, which is what a
		live page does every few seconds."""
		self._laidOut(col=2)
		self._press("script_flowTableToggleColumn")
		self.band.refresh(force=True)
		self.assertNotIn(2, self._drawn())

	def test_startingAgainGivesTheTableBackAsItComes(self):
		self._laidOut(col=2)
		self._press("script_flowTableToggleColumn")
		said = self._press("script_flowTableResetLayout")
		self.assertIn(2, self._drawn())
		self.assertTrue(any("as it comes" in message for message in said))

	def test_andLeavesWhatWasSavedAlone(self):
		"""The way out of an experiment, not the way out of a memory."""
		from brlMultiline import flowTableLayouts, flowTableSource

		obj, document = self._laidOut(col=2)
		document.documentConstantIdentifier = "https://example.com/watchlist"
		handle = flowTableSource.tableAt(obj)
		flowTableLayouts.remember(handle, flowTableLayouts.TableLayout(columns=(1, 3)))
		self._press("script_flowTableToggleColumn")
		self._press("script_flowTableResetLayout")
		self.assertIsNotNone(flowTableLayouts.layoutFor(handle))

	def test_aColumnCanBeDrawnWithoutCapitalSigns(self):
		"""The whole way through, because the decision is read in three places: the widths are
		measured from it, the rows are built from it, and the record carries it. A stock
		symbol costs two cells of a seven cell column to the capitals-word indicator."""
		from brlMultiline import flowTable

		self._laidOut(col=1)
		self.band.arrangeColumn(1, plainCase=True)
		self.assertIn("aapl", " ".join(self.band.controller.describeRows()).lower())
		self.assertNotIn("AAPL", " ".join(self.band.controller.describeRows()))
		self.assertTrue(self.band.tableLayoutInForce.perColumn[1].plainCase)
		self.assertEqual(flowTable.ColumnChoice(plainCase=True).plainCase, True)
		# And the width was measured the way it is drawn. The measurement is what names a
		# column as well as sizing it, so the name it came back with says which text it read:
		# a column measured with the capitals still in it is sized for cells that will not be
		# drawn, which is exactly the two the reader turned this on to get back.
		drawn = next(item for item in self.band.columnPlan().columns if item.index == 1)
		self.assertEqual(drawn.label, "symbol")

	def test_andTheColumnsBesideItAreLeftAlone(self):
		"""It is one column's decision, not the table's."""
		self._laidOut(col=1)
		self.band.arrangeColumn(1, plainCase=True)
		self.assertIn("Change", " ".join(self.band.controller.describeRows()))

	def test_andItComesBackWithASavedLayout(self):
		from brlMultiline import flowTable, flowTableLayouts, flowTableSource

		obj, document = self._laidOut(col=1)
		document.documentConstantIdentifier = "https://example.com/watchlist"
		handle = flowTableSource.tableAt(obj)
		flowTableLayouts.remember(
			handle,
			flowTableLayouts.TableLayout(perColumn={1: flowTable.ColumnChoice(plainCase=True)}),
		)
		self.band.clearTable()
		self.band.refresh(force=True)
		self.assertTrue(self.band.layOutTable())
		self.assertIn("aapl", " ".join(self.band.controller.describeRows()).lower())

	def test_leavingTheTableGivesUpTheArrangement(self):
		"""An arrangement is about the table in front of them, exactly as the request is."""
		self._laidOut(col=2)
		self._press("script_flowTableToggleColumn")
		self.band.clearTable()
		self.assertIsNone(self.band.tableLayoutInForce)

	def test_andItDoesNotFollowThemIntoAnotherTable(self):
		"""Hidden columns and widths measured from a watchlist, applied to a message list,
		are worse than no columns: the reader has no way to feel that is what happened. A
		review found four paths that dropped the request and left the arrangement waiting for
		whatever table was laid out next, so the arrangement is kept with its own table's key
		and answered as nothing for any other."""
		self._laidOut(col=2)
		self._press("script_flowTableToggleColumn")
		self.assertNotIn(2, self._drawn())
		self._inTable(tableID=2, row=2, col=1)
		self.assertTrue(self.band.layOutTable())
		self.assertIsNone(self.band.tableLayoutInForce)
		self.assertIn(2, self._drawn())

	def test_theCommandsSayWhenThereIsNoTable(self):
		self._elsewhere()
		self.band.refresh(force=True)
		for name in (
			"script_flowTableToggleColumn",
			"script_flowTableCutColumn",
			"script_flowTableResetLayout",
		):
			said = self._press(name)
			self.assertTrue(any("No table columns" in message for message in said), name)

class TestATableThatLaysItselfOut(TableBandTestCase):
	"""What the column command was always pointed at: a watchlist that comes up laid out.

	A layout that has to be asked for by name every time is one the reader types out again on
	every page load. So a table the store knows becomes the table asked for, exactly as the
	command would have made it — and one keystroke still takes it away, without touching what
	was saved.
	"""

	def _watchlist(self, url="https://example.com/watchlist"):
		obj, document = self._inTable()
		document.columnHeaders = {1: "Symbol", 2: "Last", 3: "Change", 4: "%Chg"}
		document.documentConstantIdentifier = url
		return obj, document

	def _save(self, columns=(1, 2)):
		from brlMultiline import flowTableLayouts, flowTableSource

		handle = flowTableSource.tableAt(self.api.getNavigatorObject())
		flowTableLayouts.remember(handle, flowTableLayouts.TableLayout(columns=columns))

	def test_aTableWithNothingSavedIsLeftAlone(self):
		self._watchlist()
		self.band.refresh(force=True)
		self.assertFalse(self._readingATable())

	def test_aTableWithALayoutSavedComesUpLaidOut(self):
		self._watchlist()
		self._save()
		self.band.clearTable()
		self.band.refresh(force=True)
		self.assertTrue(self._readingATable())

	def test_andItIsTheColumnsThatWereSaved(self):
		self._watchlist()
		self._save(columns=(2, 4))
		self.band.clearTable()
		self.band.refresh(force=True)
		plan = self.band.columnPlan()
		self.assertEqual([column.index for column in plan.columns], [2, 4])

	def test_arrowingBackIntoTheTableLaysItOutAgain(self):
		"""Reported from hardware, and the half of a saved layout that was missing. In browse
		mode the caret walks in and out of a table without any event: the focus object is still
		the page and nothing was asking. So a reader who saved a layout, arrowed out and
		arrowed back got their ordinary reading — the "watchlist that comes up laid out"
		refused at the moment they would notice."""
		obj, document = self._watchlist()
		self._save()
		self.band.refresh(force=True)
		self.assertTrue(self._readingATable())
		# Out of the table: the same document, still readable, no longer in a cell.
		document.inTable = False
		self.band.recheck()
		self.assertFalse(self._readingATable())
		# And back into it, with nothing but a caret move to say so.
		document.inTable = True
		self.band.recheck()
		self.assertTrue(self._readingATable())

	def test_aTableWithNothingSavedIsNotLaidOutOnTheWayPast(self):
		"""The reader who has saved nothing walks through tables all day."""
		obj, document = self._watchlist()
		document.inTable = False
		self.band.refresh(force=True)
		document.inTable = True
		self.band.recheck()
		self.assertFalse(self._readingATable())

	def test_andAReaderWithAnEmptyStorePaysNothingForTheQuestion(self):
		"""It runs before every redraw, so it must cost nothing where nothing is saved."""
		from brlMultiline import flowTableSource

		obj, document = self._watchlist()
		document.inTable = False
		self.band.refresh(force=True)
		document.inTable = True
		asked = []
		real = flowTableSource.tableAt
		flowTableSource.tableAt = lambda obj: asked.append(obj) or real(obj)
		try:
			self.band.recheck()
		finally:
			flowTableSource.tableAt = real
		self.assertEqual(asked, [])

	def test_aTableTurnedOffStaysOffWhileTheyAreInIt(self):
		"""The command drops the request; this must not put it straight back on the next
		redraw, which would be a toggle that does nothing."""
		from brlMultiline import GlobalPlugin

		self._watchlist()
		self._save()
		self.band.refresh(force=True)
		GlobalPlugin.script_flowTableColumns(CommandHolder(self.band), None)
		self.band.recheck()
		self.band.recheck()
		self.assertFalse(self._readingATable())

	def test_oneKeystrokeStillTakesItAway(self):
		"""And it stays away: without that, the command drops the request and the next redraw
		puts it straight back, which is a toggle that does nothing."""
		from brlMultiline import GlobalPlugin

		self._watchlist()
		self._save()
		self.band.refresh(force=True)
		holder = CommandHolder(self.band)
		spokenMessages.clear()
		GlobalPlugin.script_flowTableColumns(holder, None)
		self.band.refresh(force=True)
		self.assertFalse(self._readingATable())

	def test_andComingBackToTheTableAsksForItAgain(self):
		from brlMultiline import GlobalPlugin

		obj, _document = self._watchlist()
		self._save()
		self.band.refresh(force=True)
		holder = CommandHolder(self.band)
		GlobalPlugin.script_flowTableColumns(holder, None)
		self.band.refresh(force=True)
		# Away from the table, which is what spends the refusal, and back again.
		self._elsewhere()
		self.band.refresh(force=True)
		self.api.getFocusObject = lambda: obj
		self.api.getNavigatorObject = lambda: obj
		self.band.refresh(force=True)
		self.assertTrue(self._readingATable())

	def test_theTableIsRecognisedOnceForOneLayout(self):
		"""Found by review: the table was resolved four times and the layout looked up twice
		for one automatic layout, each of them a read of the document at the caret. Both are
		found once now and carried through the build."""
		from brlMultiline import flowBand, flowTableLayouts, flowTableSource

		self._watchlist()
		self._save()
		self.band.clearTable()
		asked = []
		lookedUp = []
		realTable = flowTableSource.tableAt
		realLayout = flowTableLayouts.layoutFor
		flowTableSource.tableAt = lambda obj: asked.append(obj) or realTable(obj)
		flowBand.flowTableLayouts.layoutFor = lambda handle: lookedUp.append(handle) or realLayout(handle)
		try:
			# The build itself, rather than a whole redraw: a redraw also asks
			# `_recheckTable` whether the caret is still in the table, which is a different
			# question and is asked with or without a saved layout.
			shown = self.band._showTable(
				self.api.getNavigatorObject(),
				self.band.segment(),
				force=True,
			)
		finally:
			flowTableSource.tableAt = realTable
			flowBand.flowTableLayouts.layoutFor = realLayout
		self.assertTrue(shown)
		self.assertTrue(self._readingATable())
		# Two: the one that decides the build, and the one `_recheckTable` makes when the
		# band redraws afterwards to see whether the caret is still in this table. That second
		# question is asked on every redraw of every table, saved layout or not. Before this
		# was found the count was four, with the layout looked up twice.
		self.assertEqual(len(asked), 2, f"the table was resolved {len(asked)} times")
		self.assertEqual(len(lookedUp), 1, f"the layout was looked up {len(lookedUp)} times")

	def test_aSavedLayoutIsNotAskedAboutEveryPage(self):
		"""The lookup runs wherever the reader goes, so an empty store must cost nothing."""
		from brlMultiline import flowTableSource

		self._elsewhere()
		asked = []
		real = flowTableSource.tableAt
		flowTableSource.tableAt = lambda obj: asked.append(obj) or real(obj)
		try:
			self.band.refresh(force=True)
		finally:
			flowTableSource.tableAt = real
		self.assertEqual(asked, [])


class TestRememberingTheLayoutOnTheDisplay(TableBandTestCase):
	"""The command that saves one, and the one that drops it."""

	def _press(self, script):
		from brlMultiline import GlobalPlugin

		spokenMessages.clear()
		flashedMessages.clear()
		getattr(GlobalPlugin, script)(CommandHolder(self.band), None)
		return list(spokenMessages)

	def _watchlist(self):
		obj, document = self._inTable()
		document.columnHeaders = {1: "Symbol", 2: "Last", 3: "Change", 4: "%Chg"}
		document.documentConstantIdentifier = "https://example.com/watchlist"
		return obj, document

	def test_thereMustBeSomethingToRemember(self):
		self._watchlist()
		self.assertIn("No table columns are showing", self._press("script_rememberTableLayout"))

	def test_theTableIsSavedAndSaysSo(self):
		from brlMultiline import flowTableLayouts, flowTableSource

		self._watchlist()
		self.band.layOutTable()
		said = self._press("script_rememberTableLayout")
		self.assertTrue(any("display in columns" in message for message in said))
		handle = flowTableSource.tableAt(self.api.getNavigatorObject())
		self.assertIsNotNone(flowTableLayouts.layoutFor(handle))

	def test_andTheColumnsAreMeasuredAfreshEachTime(self):
		"""One press of remember is a request, not a choice of columns. The reader who found
		this had configured nothing: the columns as drawn were written into the record, so a
		column blank in the bandful that was sampled was frozen out of every later reading."""
		from brlMultiline import flowTableLayouts, flowTableSource

		self._watchlist()
		self.band.layOutTable()
		self._press("script_rememberTableLayout")
		handle = flowTableSource.tableAt(self.api.getNavigatorObject())
		self.assertEqual(flowTableLayouts.layoutFor(handle).columns, ())

	def test_andSayingSoDoesNotFlashOverIt(self):
		"""The display is showing the thing being talked about."""
		self._watchlist()
		self.band.layOutTable()
		self._press("script_rememberTableLayout")
		self.assertEqual(flashedMessages, [])

	def test_aTableNothingCanRecogniseSaysSo(self):
		obj, document = self._inTable()
		document.documentConstantIdentifier = None
		self.band.layOutTable()
		said = self._press("script_rememberTableLayout")
		self.assertTrue(any("cannot be recognised" in message for message in said))

	def test_forgettingDropsTheLayoutAndTheColumns(self):
		self._watchlist()
		self.band.layOutTable()
		self._press("script_rememberTableLayout")
		said = self._press("script_forgetTableLayout")
		self.assertIn("Table layout deleted", said)
		self.assertFalse(self._readingATable())
		self.band.refresh(force=True)
		self.assertFalse(self._readingATable())

	def test_forgettingWhatWasNeverSavedSaysSo(self):
		self._watchlist()
		said = self._press("script_forgetTableLayout")
		self.assertTrue(any("no saved layout" in message for message in said))

	def test_forgettingSomewhereElseSaysSo(self):
		self._elsewhere()
		self.api.getNavigatorObject = self.api.getFocusObject
		self.addCleanup(setattr, self.api, "getNavigatorObject", self.api.getNavigatorObject)
		self.assertIn("Not in a table", self._press("script_forgetTableLayout"))


class TestTheReaderFilteringTheSheetUnderTheBand(TableBandTestCase):
	"""**A filter hides rows; it does not remove them, and the band is already holding some.**

	The walk steps over the rows a filter took away from the moment it is applied — and the
	rows walked before that stay in the window, are re-read by their own row numbers, and are
	drawn above and below the row the reader filtered down to. Nothing else notices: every one
	of those rows was read correctly when it was read, and answers correctly still.

	Reported from a worksheet filtered down to a single row, which came up with the row above
	it — filtered away — on the display over it.
	"""

	def _sheet(self, showing, at=(9, 1)):
		from .test_flowObjectTable import FilteredSheet

		return FilteredSheet(
			rows=[[f"row {number}", "x" * 20, "y" * 20, "z" * 20] for number in range(1, 13)],
			at=at,
			showing=showing,
		)

	def _onASheet(self, sheet):
		"""Put the reader on a cell of a sheet and lay its columns out, as the command does."""
		cell = FakeNavigatorObject("a cell", role="TABLECELL")
		cell.brlMultilineSheet = lambda: sheet
		self.api.getFocusObject = lambda: cell
		self.api.getNavigatorObject = lambda: cell
		self.addCleanup(setattr, self.api, "getNavigatorObject", self.api.getNavigatorObject)
		self.band._follow()
		self.assertTrue(self.band.layOutTable())
		return cell

	def _held(self):
		return [block.blockId.bookmark for block in self.band.controller.window.blocks]

	def test_theRowsTheFilterTookAwayLeaveTheBand(self):
		sheet = self._sheet([(1, 3), (9, 12)])
		cell = self._onASheet(sheet)
		self.band.controller.panBack()
		self.assertIn(3, self._held())
		# What a filter leaves: the one row it was filtered down to, and the header row above
		# it, which Excel keeps on show.
		sheet.showing = [(1, 1), (9, 9)]
		sheet.at = (9, 2)
		self.band.showObject(cell, focusMoved=True)
		self.assertEqual(self._held(), [9])

	def test_andTheReaderKeepsThePageOfColumnsTheyHadTurnedTo(self):
		"""The rebuild is not something they asked for, and the page is their own choice. Only
		the rows are given up, because the row they were parked on may be one of the rows that
		went away."""
		sheet = self._sheet([(1, 3), (9, 12)])
		cell = self._onASheet(sheet)
		self.assertTrue(self.band.turnColumnPage(1))
		page = self.band.columnPlan().at
		self.band.controller.panBack()
		# What a filter leaves: the one row it was filtered down to, and the header row above
		# it, which Excel keeps on show.
		sheet.showing = [(1, 1), (9, 9)]
		sheet.at = (9, 2)
		self.band.showObject(cell, focusMoved=True)
		self.assertEqual(self.band.columnPlan().at, page)

	def test_aSheetWhoseFilterHasNotChangedIsNotReadAgain(self):
		"""Every move the reader makes asks this, and the answer is almost always that nothing
		has changed. A reading made again on each of them would measure the sheet on every
		arrow key and take the reader's window from them."""
		sheet = self._sheet([(1, 3), (9, 12)])
		cell = self._onASheet(sheet)
		self.band.controller.panBack()
		held = self._held()
		sheet.at = (9, 2)
		self.band.showObject(cell, focusMoved=True)
		self.assertEqual(self._held(), held)
