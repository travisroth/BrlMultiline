# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for drawing one row of a table at its columns' offsets.

One cell per character, which is what the harness's regions produce, so a drawn row reads
back as the string it came from and a test can say what it expects to feel. That is also
what makes the point of this milestone visible in a test at all: the same column starts at
the same character on every row, and a test can assert that by slicing.

Fill mode throughout, so the row cutting is the add-on's own arithmetic rather than NVDA's
word wrapping, which is a hardware question.
"""

import unittest

from ._stubs import Region, installStubs

installStubs()

from brlMultiline.flow import NO_POSITION, BlockId, SourceBlock  # noqa: E402
from brlMultiline.flowIndent import planFor as indentPlanFor  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402
from brlMultiline.flowTable import (  # noqa: E402
	KEEP_END,
	ColumnChoice,
	MAX_TABLE_ROWS,
	READING_ORDER,
	TRUNCATE,
	Column,
	ColumnPlan,
	Measurement,
	RowCell,
	planFor,
	positionParts,
	rowCellsOf,
)
from brlMultiline.flowTableSource import TableRow  # noqa: E402

BAND = 32


class FakeHandler:
	def __init__(self):
		self.buffer = None


WATCHLIST = ["Symbol", "Last", "Change", "%Chg"]


def row(*values) -> TableRow:
	""":return: one row of the watchlist, one cell per value."""
	return TableRow([RowCell(index=n, region=Region(text)) for n, text in enumerate(values, 1)])


def block(rowRegion, name="r") -> SourceBlock:
	return SourceBlock(blockId=BlockId(generation=1, bookmark=name, unit="row"), region=rowRegion)


def watchlistPlan(numCols=BAND, maxRows=1, overflow=None) -> ColumnPlan:
	measured = [
		Measurement(index=1, width=6, label="Symbol"),
		Measurement(index=2, width=7, label="Last"),
		Measurement(index=3, width=7, label="Change"),
		Measurement(index=4, width=6, label="%Chg"),
	]
	if overflow is None:
		return planFor(measured, numCols, maxRows=maxRows)
	return planFor(measured, numCols, maxRows=maxRows, overflow=overflow)


def renderer(plan=None, numCols=BAND) -> FlowRenderer:
	return FlowRenderer(
		FakeHandler(),
		numCols=numCols,
		fillRows=True,
		indentPlan=indentPlanFor([], numCols),
		columnPlan=watchlistPlan(numCols) if plan is None else plan,
	)


def textOf(cells) -> str:
	return "".join(" " if cell == 0 else chr(cell) for cell in cells)


class TestARowIsRecognisedAsOne(unittest.TestCase):
	"""A row knows it is a row; everything else asks and gets None."""

	def test_aTableRowSaysWhatItsCellsAre(self):
		self.assertEqual(len(rowCellsOf(row("AAPL", "1.00"))), 2)

	def test_anOrdinaryRegionIsNotATableRow(self):
		self.assertIsNone(rowCellsOf(Region("just a line")))

	def test_anEmptyRowIsNotDrawnAsOne(self):
		self.assertIsNone(rowCellsOf(TableRow([])))

	def test_aRowStillReadsAsOneLine(self):
		"""The fallback that keeps a table readable by everything not taught about columns —
		the dry run, a log line, and reading order itself."""
		self.assertEqual(row("AAPL", "182.50").rawText, "AAPL  182.50")


class TestARowIsARegion(unittest.TestCase):
	"""A row does not only go to the code that draws columns. It goes into NVDA's own buffer.

	It began as a stand-in carrying the handful of attributes this add-on happens to ask a
	region for, which passed everything here and then raised inside a display update, on
	`hidePreviousRegions` — an attribute nothing in this add-on reads and NVDA's buffer reads
	first. The list of things a region has is NVDA's to know and it is longer than any guess,
	so a row is a `Region` and the guessing is over.
	"""

	def test_aRowIsARegion(self):
		from braille.regions.base import Region

		self.assertIsInstance(row("AAPL", "182.50"), Region)

	def test_aBufferCanHoldOne(self):
		"""The crash, reproduced: the buffer asks its last region whether to hide the ones
		before it, and a stand-in had no answer."""
		from braille.buffers import BrailleBuffer

		buffer = BrailleBuffer(FakeHandler())
		buffer.regions = [row("AAPL", "182.50")]
		self.assertEqual(len(list(buffer.visibleRegions)), 1)

	def test_aBufferCanReadItsTextBack(self):
		from braille.buffers import BrailleBuffer

		buffer = BrailleBuffer(FakeHandler())
		buffer.regions = [row("AAPL", "182.50")]
		buffer.update()
		self.assertEqual(buffer.rawText, "AAPL  182.50")

	def test_theMapsAreAsLongAsWhatTheyMap(self):
		"""NVDA turns a window of cells back into text through these, and reads them by
		index. A map of the wrong length maps to the wrong place rather than to nowhere."""
		table = row("AAPL", "182.50")
		self.assertEqual(len(table.rawToBraillePos), len(table.rawText))
		self.assertEqual(len(table.brailleToRawPos), len(table.brailleCells))

	def test_theMapsPointIntoTheRowRatherThanIntoOneCell(self):
		"""Concatenated with an offset, or every cell would map to the row's first few
		characters."""
		table = row("AAPL", "182.50")
		self.assertEqual(table.brailleToRawPos[-1], len(table.rawText) - 1)

	def test_readingItAgainKeepsItAsLongAsItShouldBe(self):
		table = row("AAPL", "182.50")
		table.update()
		self.assertEqual(len(table.brailleToRawPos), len(table.brailleCells))


class TestDrawingARowInColumns(unittest.TestCase):
	def test_eachColumnStartsWhereThePlanSaysItDoes(self):
		drawn = renderer().render(block(row("AAPL", "182.50", "+1.25", "+0.7%")))
		line = textOf(drawn.rows[0])
		self.assertEqual(line[0:4], "AAPL")
		self.assertEqual(line[7:13], "182.50")
		self.assertEqual(line[15:20], "+1.25")

	def test_theSameColumnIsAtTheSameOffsetOnEveryRow(self):
		"""The whole milestone in one assertion: this is what makes running a finger down a
		column mean anything, and it is what reading order cannot give."""
		render = renderer()
		first = textOf(render.render(block(row("AAPL", "182.50", "+1.25", "+0.7%"))).rows[0])
		second = textOf(render.render(block(row("F", "9.10", "-0.05", "-0.5%"), name="s")).rows[0])
		self.assertEqual(first[15], "+")
		self.assertEqual(second[15], "-")

	def test_aShortCellLeavesItsColumnPadded(self):
		"""Padding between columns is what puts the next one where the finger expects it, and
		it is the one place a rendering here is padded rather than `assembleCells` doing it."""
		drawn = renderer().render(block(row("F", "9.10", "-0.05", "-0.5%")))
		self.assertEqual(textOf(drawn.rows[0])[1:7], "      ")

	def test_aLongCellWrapsWithinItsColumn(self):
		"""The default. The table row grows and the value is all there."""
		drawn = renderer().render(block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")))
		# Six cells of column, two of them the continuation indent, so four of ticker a row.
		self.assertEqual(len(drawn.rows), 3)
		self.assertEqual(textOf(drawn.rows[0])[0:6], "BERK  ")
		self.assertEqual(textOf(drawn.rows[1])[0:6], "  SHIR")
		self.assertEqual(textOf(drawn.rows[2])[0:3], "  E")

	def test_aWrappedCellsContinuationIsIndented(self):
		"""Or the row below reads as the next value down rather than the same one going on."""
		drawn = renderer().render(block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")))
		self.assertEqual(textOf(drawn.rows[1])[0:2], "  ")

	def test_onlyTheRowThatNeedsItGrows(self):
		"""A row of short values takes one band row. Fixing every row at the tallest would
		spend the reader's band on blanks."""
		render = renderer()
		self.assertEqual(len(render.render(block(row("F", "9.10", "-0.05", "-0.5%"))).rows), 1)

	def test_theOtherColumnsStayWhereTheyAre(self):
		"""A cell growing must not move the columns beside it."""
		drawn = renderer().render(block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")))
		self.assertEqual(textOf(drawn.rows[0])[7:11], "1.00")

	def test_aLongCellIsCutWhenTheReaderAsksForThat(self):
		"""Truncation earns its place: an options watchlist has symbols long enough to push
		every other column into uselessness, and a reader who knows the table would rather
		have the whole of it on one row of the band."""
		drawn = renderer(plan=watchlistPlan(overflow=TRUNCATE)).render(
			block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")),
		)
		self.assertEqual(len(drawn.rows), 1)
		self.assertEqual(textOf(drawn.rows[0])[0:7], "BERKSH ")

	def test_aRowNeverGrowsPastTheCap(self):
		"""A table read taller than this is not being read as a table."""
		drawn = renderer().render(block(row("x" * 400, "1.00")))
		self.assertLessEqual(len(drawn.rows), MAX_TABLE_ROWS)

	def test_theRowIsTheFullWidthOfTheBand(self):
		drawn = renderer().render(block(row("F", "9.10", "-0.05", "-0.5%")))
		self.assertEqual(len(drawn.rows[0]), BAND)

	def test_aMissingCellLeavesItsColumnBlankAndMovesNothing(self):
		"""A merged cell, or a row still being built. Every other column stays where the
		reader left it, which is the only answer that keeps the table a table."""
		partial = TableRow(
			[RowCell(index=1, region=Region("AAPL")), RowCell(index=3, region=Region("+1.25"))]
		)
		line = textOf(renderer().render(block(partial)).rows[0])
		self.assertEqual(line[0:4], "AAPL")
		self.assertEqual(line[7:13], "      ")
		self.assertEqual(line[15:20], "+1.25")

	def test_columnsOnASecondRowAreDrawnThere(self):
		plan = watchlistPlan(numCols=16, maxRows=2)
		drawn = FlowRenderer(
			FakeHandler(),
			numCols=16,
			fillRows=True,
			columnPlan=plan,
		).render(block(row("AAPL", "182.50", "+1.25", "+0.7%")))
		self.assertEqual(len(drawn.rows), plan.numRows)
		self.assertGreater(plan.numRows, 1)


class TestWhichEndOfACutCellIsKept(unittest.TestCase):
	"""The reader's table puts six lines of help text at the front of a column, so the front of
	the cell is somebody else's advice and the end is the thing that names the row."""

	def _plan(self, choices):
		measured = [
			Measurement(index=1, width=6, label="Symbol"),
			Measurement(index=2, width=7, label="Last"),
			Measurement(index=3, width=7, label="Change"),
			Measurement(index=4, width=6, label="%Chg"),
		]
		return planFor(measured, BAND, maxRows=1, overflow=TRUNCATE, choices=choices)

	def test_theStartIsKeptByDefault(self):
		drawn = renderer(self._plan({})).render(block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")))
		self.assertEqual(textOf(drawn.rows[0])[0:4], "BERK")

	def test_andTheEndWhereTheReaderAskedForIt(self):
		drawn = renderer(self._plan({1: ColumnChoice(keep=KEEP_END)})).render(
			block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")),
		)
		self.assertIn("E", textOf(drawn.rows[0])[0:6])
		self.assertNotIn("BERK", textOf(drawn.rows[0])[0:6])

	def test_aCellThatFitsIsUntouchedEitherWay(self):
		"""Nothing to choose between when there is nothing to cut."""
		drawn = renderer(self._plan({1: ColumnChoice(keep=KEEP_END)})).render(
			block(row("AAPL", "1.00", "+0.01", "+0.1%")),
		)
		self.assertEqual(textOf(drawn.rows[0])[0:4], "AAPL")

	def test_andTheRowIsStillOneRowTall(self):
		"""Cutting is what keeps a table row one band row, whichever end it keeps."""
		drawn = renderer(self._plan({1: ColumnChoice(keep=KEEP_END)})).render(
			block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")),
		)
		self.assertEqual(len(drawn.rows), 1)


class TestLanesStackRatherThanOverlap(unittest.TestCase):
	"""A packing row is a lane, and a lane is as tall as the tallest cell in it.

	The plan may put columns on more than one row of the band. A cell in the first of those
	that wraps needs the rows underneath it, and those rows are not the second lane's to take
	— but adding the wrapped line's index to the lane number gave both of them the same row,
	and the later column won. A wrapped value came out with the next column written through
	the middle of it.
	"""

	def _twoLanes(self):
		"""Four columns on a sixteen cell band over two rows, so lanes 0 and 1 are both used."""
		measured = [Measurement(index=n, width=7, label="") for n in range(1, 5)]
		return planFor(measured, 16, maxRows=2)

	def test_theSecondLaneStartsBelowTheFirstOnesTallestCell(self):
		plan = self._twoLanes()
		self.assertEqual(sorted({place.row for place in plan.placements()}), [0, 1])
		drawn = renderer(plan=plan, numCols=16).render(
			block(row("FIRSTLONGVALUE", "b", "c", "d")),
		)
		# The first cell wraps over three rows, so the second lane starts on the fourth.
		self.assertGreaterEqual(len(drawn.rows), 4)

	def test_theSecondLaneDoesNotWriteThroughTheFirstOnesWrapping(self):
		"""The symptom: a wrapped value with the next column's cells inside it."""
		plan = self._twoLanes()
		drawn = renderer(plan=plan, numCols=16).render(
			block(row("FIRSTLONGVALUE", "b", "c", "d")),
		)
		# The first column wraps over three rows, so those three are lane zero's and the
		# whole of the value is in them, unbroken.
		lane = "".join(textOf(line)[0:7] for line in drawn.rows[:3]).replace(" ", "")
		self.assertEqual(lane, "FIRSTLONGVALUE")

	def test_theSecondLaneBeginsAfterTheFirstOneEnds(self):
		plan = self._twoLanes()
		drawn = renderer(plan=plan, numCols=16).render(
			block(row("FIRSTLONGVALUE", "b", "c", "d")),
		)
		self.assertEqual(len(drawn.rows), 4)
		self.assertEqual(textOf(drawn.rows[3])[0:1], "c")

	def test_aShortFirstLaneStillPutsTheSecondRightUnderIt(self):
		plan = self._twoLanes()
		drawn = renderer(plan=plan, numCols=16).render(block(row("a", "b", "c", "d")))
		self.assertEqual(len(drawn.rows), 2)
		self.assertEqual(textOf(drawn.rows[1])[0:1], "c")


class TestATableRowTallerThanTheBand(unittest.TestCase):
	"""Every value is promised. A row too tall is a block with more rows, which is what a
	paragraph longer than the band already is — not content quietly thrown away."""

	def _tall(self):
		return renderer().render(block(row("x" * 200, "1.00")))

	def test_itSaysThereIsMore(self):
		"""Saying there is not, while dropping the rest, is the one thing that must not
		happen: the reader is promised every value and cannot tell they are not getting one."""
		self.assertTrue(self._tall().moreRows)

	def test_theBandShowsWhatItCanHold(self):
		self.assertEqual(len(self._tall().rows), MAX_TABLE_ROWS)

	def test_theRestCanBeReached(self):
		later = renderer().render(block(row("x" * 200, "1.00")), fromRow=MAX_TABLE_ROWS)
		self.assertEqual(later.rowOffset, MAX_TABLE_ROWS)
		self.assertTrue(later.rows)

	def test_aRowThatFitsSaysThereIsNoMore(self):
		self.assertFalse(renderer().render(block(row("AAPL", "1.00"))).moreRows)


class TestARowWithNoPlan(unittest.TestCase):
	"""Reading order must survive, and it is what a table gets by default."""

	def test_readingOrderDrawsTheRowAsALine(self):
		drawn = renderer(plan=READING_ORDER).render(block(row("AAPL", "182.50")))
		self.assertEqual(textOf(drawn.rows[0]).strip(), "AAPL  182.50")

	def test_anOrdinaryBlockIsUnaffectedByAColumnPlan(self):
		"""A plan is in force for the table; the prose around it is drawn as it always was."""
		plain = SourceBlock(
			blockId=BlockId(generation=1, bookmark="p", unit="line"),
			region=Region("a paragraph"),
		)
		self.assertEqual(textOf(renderer().render(plain).rows[0]).strip(), "a paragraph")


class TestSayingWhatARowDraws(unittest.TestCase):
	"""The report has to show what the display shows, and it showed the opposite.

	A table row's positions carry which column as well as where in it, so read as offsets
	into one run of text they are enormous numbers that fall off the end of any map. The
	generic path then gave up and returned the row's whole flat text for every row of it —
	so a reader checking why their columns looked clipped was shown the full contents of the
	columns their display was clipping.
	"""

	def test_aRowSaysWhatItDraws(self):
		table = row("AAPL", "182.50", "+1.25", "+0.7%")
		drawn = renderer().render(block(table))
		positions = [where for where in drawn.positions[0] if where != NO_POSITION]
		self.assertEqual(table.textForPositions(positions), "AAPL  182.50  +1.25  +0.7%")

	def test_aTruncatedColumnSaysOnlyWhatIsOnTheDisplay(self):
		"""The whole point. A column drawn six cells wide holding nine characters of ticker
		is the reader's complaint, and a report that showed all nine could not check it."""
		table = row("BERKSHIRE", "1.00")
		drawn = renderer(plan=watchlistPlan(overflow=TRUNCATE)).render(block(table))
		positions = [where for where in drawn.positions[0] if where != NO_POSITION]
		self.assertEqual(table.textForPositions(positions), "BERKSH  1.00")

	def test_aWrappedColumnSaysWhatIsOnEachRow(self):
		table = row("BERKSHIRE", "1.00")
		drawn = renderer().render(block(table))
		said = []
		for line in drawn.positions:
			said.append(table.textForPositions([where for where in line if where != NO_POSITION]))
		self.assertEqual(said, ["BERK  1.00", "SHIR", "E"])

	def test_aRowWithAHoleSaysWhatIsThere(self):
		partial = TableRow(
			[RowCell(index=1, region=Region("AAPL")), RowCell(index=3, region=Region("+1.25"))],
		)
		drawn = renderer().render(block(partial))
		positions = [where for where in drawn.positions[0] if where != NO_POSITION]
		self.assertEqual(partial.textForPositions(positions), "AAPL  +1.25")

	def test_aPositionForAColumnThatIsNotThereIsIgnored(self):
		self.assertEqual(row("AAPL").textForPositions([1 << 20]), "")


class TestTheRowsOwnCursor(unittest.TestCase):
	"""In the row's own position space, which is the packed one — the same space its drawn
	positions and its routing are in, because that is the only space anything can match."""

	def test_noCaretMeansNoCursor(self):
		self.assertIsNone(row("AAPL", "182.50").brailleCursorPos)

	def test_theCursorIsPackedLikeAPosition(self):
		table = TableRow(
			[RowCell(index=1, region=Region("AAPL")), RowCell(index=2, region=Region("1.00"))],
			caretColumn=lambda: 2,
		)
		self.assertEqual(positionParts(table.brailleCursorPos), (2, 0))

	def test_aColumnTheRowHasNotGotHasNoCursor(self):
		"""A merged cell leaves a hole, and the caret cannot be in a cell that is not there."""
		table = TableRow([RowCell(index=1, region=Region("AAPL"))], caretColumn=lambda: 3)
		self.assertIsNone(table.brailleCursorPos)

	def test_itIsAskedAgainOnEveryUpdate(self):
		"""A row is re-read by NVDA at moments the source does not choose, so a cursor set
		once and remembered is a cursor left where the caret used to be."""
		where = [1]
		table = TableRow(
			[RowCell(index=1, region=Region("AAPL")), RowCell(index=2, region=Region("1.00"))],
			caretColumn=lambda: where[0],
		)
		self.assertEqual(positionParts(table.brailleCursorPos)[0], 1)
		where[0] = 2
		table.update()
		self.assertEqual(positionParts(table.brailleCursorPos)[0], 2)

	def test_aCaretThatCannotBeAskedIsNoCursor(self):
		def explode():
			raise RuntimeError("gone")

		table = TableRow([RowCell(index=1, region=Region("AAPL"))], caretColumn=explode)
		self.assertIsNone(table.brailleCursorPos)


class TestAnEmptyCellIsStillAPlace(unittest.TestCase):
	"""A cell with nothing in it draws nothing, and so left no position anywhere on the row.

	A position is what the cursor is found by and what a routing key is turned back into, so a
	reader standing in an empty cell had no cursor at all — no way to feel which column they
	were in — and no routing key would take them into one. Reported from an Excel worksheet on
	the first blank row under the data, where every cell is empty: the whole row was
	unreachable and unmarked, and the reader was left with speech.

	**The column's whole width, which was the second half of the same report.** Marking the
	column's first band cell alone gave the cursor somewhere to sit and still left routing
	broken: on a blank row nothing is drawn, so there is no telling which single cell of
	thirty-two is the live one, and every press either side of it reached nothing. The reader
	aims by the pinned header — press under the heading you want — and that only works if the
	whole of the column answers.
	"""

	def _drawn(self, *values):
		return renderer().render(block(row(*values)))

	def _startOf(self, column: int) -> int:
		""":return: the band cell the plan puts one column at."""
		return next(
			place.offset for place in watchlistPlan().placements() if place.column.index == column
		)

	def _placed(self, column: int):
		""":return: where the plan puts one column, as (start, width)."""
		place = next(
			place for place in watchlistPlan().placements() if place.column.index == column
		)
		return place.offset, place.column.width

	def test_theColumnOfAnEmptyCellIsMarkedAtItsOwnStart(self):
		drawn = self._drawn("AAPL", "", "+1.25", "+0.7%")
		self.assertEqual(positionParts(drawn.positions[0][self._startOf(2)]), (2, 0))

	def test_andTheCellItselfIsBlank(self):
		"""It says where it is, it does not draw anything."""
		drawn = self._drawn("AAPL", "", "+1.25", "+0.7%")
		self.assertEqual(drawn.rows[0][self._startOf(2)], 0)

	def test_everyColumnOfAWhollyEmptyRowIsThere(self):
		"""The blank row under a sheet's data, which is the reported case."""
		drawn = self._drawn("", "", "", "")
		found = {
			positionParts(position)[0]
			for position in drawn.positions[0]
			if position != NO_POSITION
		}
		self.assertEqual(sorted(found), [1, 2, 3, 4])

	def test_andItAnswersAcrossTheWholeOfItsColumn(self):
		"""Every band cell the column occupies, so a press anywhere over it arrives."""
		drawn = self._drawn("AAPL", "", "+1.25", "+0.7%")
		start, width = self._placed(2)
		self.assertEqual(
			[positionParts(where) for where in drawn.positions[0][start : start + width]],
			[(2, 0)] * width,
		)

	def test_andSaysNothingInTheSeparatorBesideIt(self):
		"""The gap between two columns is not either of them, and a press there does nothing."""
		drawn = self._drawn("AAPL", "", "+1.25", "+0.7%")
		start, width = self._placed(2)
		self.assertEqual(drawn.positions[0][start + width], NO_POSITION)

	def test_soAPressUnderAHeadingReachesTheCellBeneathIt(self):
		"""**How the reader aims**, and the whole of why one band cell was not enough: they
		feel the pinned header and press under the heading they want. Every cell the heading
		is written across must reach that heading's own column on the blank row below it."""
		heading = renderer().renderPinned(block(row(*WATCHLIST)))
		empty = self._drawn("", "", "", "")
		written = [at for at, where in enumerate(heading.positions[0]) if where != NO_POSITION]
		self.assertEqual(len(written), len("SymbolLastChange%Chg"))
		for at in written:
			self.assertNotEqual(empty.positions[0][at], NO_POSITION, f"nothing at band cell {at}")
			self.assertEqual(
				positionParts(empty.positions[0][at])[0],
				positionParts(heading.positions[0][at])[0],
				f"band cell {at} is under a different column from the heading on it",
			)

	def test_soTheCursorHasSomewhereToSit(self):
		table = TableRow(
			[RowCell(index=1, region=Region("AAPL")), RowCell(index=2, region=Region(""))],
			caretColumn=lambda: 2,
		)
		drawn = renderer().render(block(table))
		self.assertIn(table.brailleCursorPos, drawn.positions[0])

	def test_andARoutingKeyOverItReachesThatCell(self):
		routed = []
		empty = Region("")
		empty.routeTo = lambda offset: routed.append(offset)
		table = TableRow(
			[RowCell(index=1, region=Region("AAPL")), RowCell(index=2, region=empty)],
		)
		drawn = renderer().render(block(table))
		table.routeTo(drawn.positions[0][self._startOf(2)])
		self.assertEqual(routed, [0])

	def test_aColumnTheRowHasNotGotIsStillNotThere(self):
		"""An empty cell and a missing cell are different things: a merged cell leaves no
		cell at that coordinate at all, and there is nothing to route into or stand in."""
		table = TableRow([RowCell(index=1, region=Region("AAPL"))])
		drawn = renderer().render(block(table))
		found = {
			positionParts(position)[0]
			for position in drawn.positions[0]
			if position != NO_POSITION
		}
		self.assertEqual(found, {1})

	def test_theReportStillSaysWhatTheRowDraws(self):
		"""Every column marking a position must not turn a row with one value in it into a
		row of separators."""
		table = row("AAPL", "", "", "")
		drawn = renderer().render(block(table))
		positions = [position for position in drawn.positions[0] if position != NO_POSITION]
		self.assertEqual(table.textForPositions(positions), "AAPL")


class TestRoutingIntoAColumn(unittest.TestCase):
	"""A finger lands on the band and the answer has to be a cell of the table."""

	def test_aCellsPositionSaysWhichColumnAndWhereInIt(self):
		drawn = renderer().render(block(row("AAPL", "182.50", "+1.25", "+0.7%")))
		column, offset = positionParts(drawn.positions[0][8])
		self.assertEqual(column, 2)
		self.assertEqual(offset, 1)

	def test_aRowWithAHoleStillRoutesIntoTheRightColumn(self):
		"""The table's column number travels, not the place in the drawing order. They part
		on exactly this row: the plan draws four columns, the row holds three, and the third
		thing drawn is the row's second cell."""
		cells = [RowCell(index=1, region=Region("AAPL")), RowCell(index=3, region=Region("+1.25"))]
		partial = TableRow(cells)
		drawn = renderer().render(block(partial))
		partial.routeTo(drawn.positions[0][15])
		self.assertEqual(cells[1].region.routedTo, 0)
		self.assertFalse(hasattr(cells[0].region, "routedTo"))

	def test_aWrappedCellsIndentBelongsToNoPosition(self):
		"""It came from nowhere and has to stay from nowhere. Packing `NO_POSITION` packs a
		negative number, which comes back out as a real column one lower with an enormous
		offset — so one column's indent claimed to be the content of the column before it,
		and a routing key over it would have gone there."""
		drawn = renderer().render(block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")))
		self.assertEqual(drawn.positions[1][0], NO_POSITION)
		self.assertEqual(drawn.positions[1][1], NO_POSITION)

	def test_onlyRealColumnsAppearInARowsPositions(self):
		drawn = renderer().render(block(row("BERKSHIRE", "1.00", "+0.01", "+0.1%")))
		for line in drawn.positions:
			for mark in line:
				if mark != NO_POSITION:
					self.assertGreaterEqual(positionParts(mark)[0], 1)
					self.assertLessEqual(positionParts(mark)[0], 4)

	def test_aGapBelongsToNoPosition(self):
		"""Routing into padding must do nothing, which is what `NO_POSITION` says."""
		drawn = renderer().render(block(row("F", "9.10", "-0.05", "-0.5%")))
		self.assertEqual(drawn.positions[0][6], NO_POSITION)

	def test_theRowSendsARoutingPressToTheRightCell(self):
		cells = [RowCell(index=n, region=Region(text)) for n, text in enumerate(("AAPL", "182.50"), 1)]
		table = TableRow(cells)
		drawn = renderer().render(block(table))
		table.routeTo(drawn.positions[0][8])
		self.assertEqual(cells[1].region.routedTo, 1)
		self.assertFalse(hasattr(cells[0].region, "routedTo"))

	def test_aPositionForAColumnTheRowHasNotGotDoesNothing(self):
		table = row("AAPL")
		table.routeTo(1 << 20)


class TestThePlanIsPartOfTheKey(unittest.TestCase):
	"""A row drawn under one set of widths must never be served for one drawn under another,
	or the reader's finger finds the previous layout's columns."""

	def test_twoPlansGiveTwoKeys(self):
		wide = renderer().render(block(row("AAPL", "182.50"))).renderKey
		narrow = renderer(plan=watchlistPlan(numCols=24)).render(block(row("AAPL", "182.50"))).renderKey
		self.assertNotEqual(wide, narrow)

	def test_thesamePlanGivesTheSameKey(self):
		first = renderer().render(block(row("AAPL", "182.50"))).renderKey
		second = renderer().render(block(row("AAPL", "182.50"))).renderKey
		self.assertEqual(first, second)

	def test_aPlanBuiltByHandDrawsWhereItSays(self):
		plan = ColumnPlan(columns=(Column(index=1, width=4), Column(index=2, width=6)), numCols=BAND)
		drawn = renderer(plan=plan).render(block(row("AAPL", "182.50")))
		line = textOf(drawn.rows[0])
		self.assertEqual(line[0:4], "AAPL")
		self.assertEqual(line[5:11], "182.50")
