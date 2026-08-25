# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a table as a stream of rows.

The table throughout is the one the milestone is for — a stock watchlist — because its shape
is what makes the arithmetic and the fetching worth doing: a narrow first column that has to
stay readable, and numbers that only mean anything beside the ones above and below them.

Everything about finding cells is NVDA's, so what is tested here is the walking: which rows
become blocks, which cells are asked for, what happens at the ends of the table, and what a
hole in a row does. The stand-in document answers the three methods this add-on asks of a
real one and records what it was asked, so a test can say what was *not* read as well as what
was.
"""

import unittest

from ._stubs import FakeTableDocument, NoTableDocument, installStubs

installStubs()

from brlMultiline.flow import ResultKind  # noqa: E402
from brlMultiline.flowTable import rowCellsOf  # noqa: E402
from brlMultiline.flowTableSource import (  # noqa: E402
	TableFlowSource,
	measure,
	sameTable,
	tableAt,
	tableDocumentFor,
)

WATCHLIST = [
	["Symbol", "Last", "Change", "%Chg"],
	["AAPL", "182.50", "+1.25", "+0.7%"],
	["F", "9.10", "-0.05", "-0.5%"],
	["BRK.B", "402.15", "+3.40", "+0.9%"],
]

ALL_COLUMNS = (1, 2, 3, 4)


class FakeFocus:
	"""An object in a document, the way the band meets one."""

	def __init__(self, interceptor=None):
		self.treeInterceptor = interceptor


def watchlist(row=2, col=1) -> FakeTableDocument:
	return FakeTableDocument([list(line) for line in WATCHLIST], row=row, col=col)


def sourceOver(document, columns=ALL_COLUMNS, live=False) -> TableFlowSource:
	handle = tableAt(FakeFocus(document))
	return TableFlowSource(handle, columns=columns, generation=1, live=live)


def textsOf(result) -> list:
	""":return: the text of each cell of a fetched row."""
	return [cell.region.rawText for cell in rowCellsOf(result.block.region)]


class TestFindingATable(unittest.TestCase):
	"""The test Easy Table Navigator uses, for the reason it uses it: it answers by doing the
	work, so a cheaper test would either be a second implementation or would offer a layout
	the fetches could not fill."""

	def test_aDocumentInATableIsFound(self):
		self.assertIsNotNone(tableAt(FakeFocus(watchlist())))

	def test_theTablesShapeAndThePlaceInItAreReported(self):
		found = tableAt(FakeFocus(watchlist(row=3, col=2)))
		self.assertEqual((found.numRows, found.numCols), (4, 4))
		self.assertEqual((found.row, found.col), (3, 2))

	def test_aDocumentNotInATableIsNotFound(self):
		self.assertIsNone(tableAt(FakeFocus(NoTableDocument([["a"]]))))

	def test_somethingWithNoDocumentAtAllIsNotFound(self):
		self.assertIsNone(tableAt(FakeFocus()))
		self.assertIsNone(tableAt(None))

	def test_browseModeStandingAsideIsNotATable(self):
		"""`passThrough` is the reader typing into a control inside a cell. The page's table
		navigation is not what they are asking for while they do it."""
		document = watchlist()
		document.passThrough = True
		self.assertIsNone(tableDocumentFor(FakeFocus(document)))

	def test_anEmptyTableIsNotATable(self):
		self.assertIsNone(tableAt(FakeFocus(FakeTableDocument([]))))


class TestWhichTableThisIs(unittest.TestCase):
	"""NVDA's table identifier is only unique within a document. A virtual buffer numbers its
	tables from one, so the first table of every page is table 1."""

	def test_atableIsItselfAcrossTwoReads(self):
		document = watchlist()
		self.assertTrue(sameTable(tableAt(FakeFocus(document)).key, tableAt(FakeFocus(document)).key))

	def test_thesameIdentifierInAnotherDocumentIsAnotherTable(self):
		"""The reported failure: a focus change straight from one page's table into another
		page's table carried the reader's layout with it."""
		here = tableAt(FakeFocus(watchlist()))
		there = tableAt(FakeFocus(watchlist()))
		self.assertEqual(here.tableID, there.tableID)
		self.assertFalse(sameTable(here.key, there.key))

	def test_anotherTableInTheSameDocumentIsAnotherTable(self):
		document = watchlist()
		first = tableAt(FakeFocus(document)).key
		document.tableID = 2
		self.assertFalse(sameTable(first, tableAt(FakeFocus(document)).key))

	def test_theSourceKnowsWhenItIsSomewhereElse(self):
		source = sourceOver(watchlist())
		self.assertFalse(source.isStillHere(FakeFocus(watchlist())))

	def test_nothingIsNotATable(self):
		self.assertFalse(sameTable(None, None))
		self.assertFalse(sameTable(tableAt(FakeFocus(watchlist())).key, None))


class TestReadingRows(unittest.TestCase):
	def test_theRowTheReaderIsOnIsTheFirstBlock(self):
		source = sourceOver(watchlist(row=2))
		self.assertEqual(textsOf(source.blockAtCursor()), ["AAPL", "182.50", "+1.25", "+0.7%"])

	def test_readingOnGivesTheRowBelow(self):
		source = sourceOver(watchlist(row=2))
		first = source.blockAtCursor().block
		self.assertEqual(textsOf(source.blockAfter(first.blockId))[0], "F")

	def test_readingBackGivesTheRowAbove(self):
		source = sourceOver(watchlist(row=3))
		first = source.blockAtCursor().block
		self.assertEqual(textsOf(source.blockBefore(first.blockId))[0], "AAPL")

	def test_theRowNumberIsTheBookmark(self):
		"""What makes a table cheap to walk: rows are numbered by the table, the numbering
		survives a re-read, and it is what `_getTableCellAt` takes. Nothing is remembered
		between fetches."""
		source = sourceOver(watchlist(row=2))
		self.assertEqual(source.blockAtCursor().block.blockId.bookmark, 2)

	def test_theLastRowHasNothingAfterIt(self):
		source = sourceOver(watchlist(row=4))
		last = source.blockAtCursor().block
		self.assertIs(source.blockAfter(last.blockId).kind, ResultKind.END_OF_STREAM)

	def test_theFirstRowHasNothingBeforeIt(self):
		source = sourceOver(watchlist(row=1))
		first = source.blockAtCursor().block
		self.assertIs(source.blockBefore(first.blockId).kind, ResultKind.END_OF_STREAM)

	def test_walkingOffTheEndCostsNoReads(self):
		"""A table knows how many rows it has. Walking off it to find out costs a search of
		the whole buffer per column to learn something already known."""
		document = watchlist(row=4)
		source = sourceOver(document)
		last = source.blockAtCursor().block
		document.reads.clear()
		source.blockAfter(last.blockId)
		self.assertEqual(document.reads, [])

	def test_theWholeTableCanBeWalked(self):
		source = sourceOver(watchlist(row=1))
		found = [textsOf(source.blockAtCursor())[0]]
		blockId = source.blockAtCursor().block.blockId
		while True:
			result = source.blockAfter(blockId)
			if result.kind is not ResultKind.BLOCK:
				break
			found.append(textsOf(result)[0])
			blockId = result.block.blockId
		self.assertEqual(found, ["Symbol", "AAPL", "F", "BRK.B"])


class TestWhichCellsAreRead(unittest.TestCase):
	"""Each read is a search of the document. Nothing is read that will not be drawn."""

	def test_onlyThePlannedColumnsAreFetched(self):
		document = watchlist(row=2)
		source = TableFlowSource(tableAt(FakeFocus(document)), columns=(1, 3), generation=1)
		document.reads.clear()
		source.blockAtCursor()
		self.assertEqual(document.reads, [(2, 1), (2, 3)])

	def test_theCellsComeBackInTheOrderTheyWereAskedFor(self):
		document = watchlist(row=2)
		source = TableFlowSource(tableAt(FakeFocus(document)), columns=(3, 1), generation=1)
		self.assertEqual(textsOf(source.blockAtCursor()), ["+1.25", "AAPL"])


class TestARowWithAHoleInIt(unittest.TestCase):
	"""A merged cell occupies one coordinate and leaves the others empty, and a half-built row
	looks the same. The row is built without it and every other column stays put."""

	def _holed(self):
		rows = [list(line) for line in WATCHLIST]
		rows[2][1] = None
		return FakeTableDocument(rows, row=3)

	def test_theRowIsStillRead(self):
		source = sourceOver(self._holed())
		self.assertIs(source.blockAtCursor().kind, ResultKind.BLOCK)

	def test_theMissingCellIsSimplyAbsent(self):
		source = sourceOver(self._holed())
		cells = rowCellsOf(source.blockAtCursor().block.region)
		self.assertEqual([cell.index for cell in cells], [1, 3, 4])

	def test_theOtherCellsKeepTheirColumns(self):
		"""Which is the whole point: the renderer draws by column number, so a hole leaves a
		hole rather than shifting the rest of the row left."""
		source = sourceOver(self._holed())
		cells = rowCellsOf(source.blockAtCursor().block.region)
		byColumn = {cell.index: cell.region.rawText for cell in cells}
		self.assertEqual(byColumn[3], "-0.05")


class TestMeasuringTheColumns(unittest.TestCase):
	"""In cells, by translating, because that is the only honest measurement."""

	def test_everyColumnIsMeasured(self):
		measured = measure(tableAt(FakeFocus(watchlist())))
		self.assertEqual([item.index for item in measured], [1, 2, 3, 4])

	def test_aColumnIsAsWideAsItsWidestCell(self):
		measured = measure(tableAt(FakeFocus(watchlist())))
		self.assertEqual(measured[0].width, len("Symbol"))
		self.assertEqual(measured[1].width, len("182.50"))

	def test_theHeaderIsRememberedAsTheLabel(self):
		"""What a pinned header will hold, and what a saved layout matches a table by."""
		measured = measure(tableAt(FakeFocus(watchlist())))
		self.assertEqual([item.label for item in measured], ["Symbol", "Last", "Change", "%Chg"])

	def test_theHeaderIsReadEvenFromDeepInTheTable(self):
		"""It is the widest thing in many a column, and the caret is rarely on it."""
		measured = measure(tableAt(FakeFocus(watchlist(row=4))))
		self.assertEqual(measured[2].label, "Change")

	def test_onlyABandfulOfRowsIsRead(self):
		"""A table of four thousand rows would cost four thousand reads to answer a question
		a bandful answers well enough."""
		rows = [["Symbol", "Last"]] + [[f"S{n}", f"{n}.00"] for n in range(400)]
		document = FakeTableDocument(rows, row=1)
		document.reads.clear()
		measure(tableAt(FakeFocus(document)), sample=8)
		self.assertLessEqual(len({row for row, _column in document.reads}), 8)

	def test_aColumnNothingWasFoundInIsNotAColumn(self):
		"""Given a width it became a phantom: three cells of blank between two real columns,
		on every row, for something the table is not showing."""
		rows = [[line[0], None, line[2]] for line in WATCHLIST]
		measured = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		self.assertTrue(measured[1].hidden)
		self.assertFalse(measured[0].hidden)
		self.assertFalse(measured[2].hidden)

	def test_aColumnOfIconsIsNotAColumn(self):
		"""From the reader's own watchlist: the leftmost column is icons NVDA cannot read.
		There is a cell, it is simply empty, in the header and in every row — so it answers
		where a merged cell raises, and it was drawn at the minimum width anyway."""
		rows = [["", *line[1:]] for line in WATCHLIST]
		measured = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		self.assertTrue(measured[0].hidden)
		self.assertFalse(measured[1].hidden)

	def test_aColumnWithOneValueInItIsStillAColumn(self):
		"""Empty is not the same as mostly empty. A column of flags is blank on most rows."""
		rows = [["", *line[1:]] for line in WATCHLIST]
		rows[2][0] = "*"
		measured = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		self.assertFalse(measured[0].hidden)

	def test_aColumnWithAHeaderAndNoBodyIsStillAColumn(self):
		"""A spanning cell looks like a missing one from outside, so the header row is always
		read: a real column has a header even where its body is merged away."""
		rows = [list(WATCHLIST[0])] + [[line[0], None, line[2]] for line in WATCHLIST[1:]]
		measured = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		self.assertFalse(measured[1].hidden)

	def test_aTypicalCellIsMeasuredAsWellAsTheWidest(self):
		"""Planned from the widest, nine rows are laid out for the one that has a paragraph
		in it. The width is chosen for the typical cell and the outlier wraps taller."""
		rows = [["Remarks"], ["ok"], ["ok"], ["ok"], ["ok"], ["a very much longer remark indeed"]]
		measured = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		self.assertEqual(measured[0].width, len("a very much longer remark indeed"))
		self.assertLess(measured[0].typicalWidth, measured[0].width)

	def test_theHeaderIsNotCountedAsATypicalCell(self):
		"""It is often the widest thing in a column of numbers and it is drawn once, where the
		body is drawn on every row."""
		rows = [["An extremely long header"], ["1"], ["2"], ["3"], ["4"]]
		measured = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		self.assertEqual(measured[0].typicalWidth, 1)
		self.assertEqual(measured[0].labelWidth, len("An extremely long header"))

	def test_aColumnWithNoBodyRowsFallsBackToTheWidest(self):
		rows = [["Header"]]
		measured = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		self.assertEqual(measured[0].typical, measured[0].width)

	def test_theHeaderIsMeasuredInCellsOfItsOwn(self):
		measured = measure(tableAt(FakeFocus(watchlist())))
		self.assertEqual(measured[0].labelWidth, len("Symbol"))

	def test_aHoleDoesNotCountAsAWidth(self):
		rows = [list(line) for line in WATCHLIST]
		rows[1][1] = None
		measured = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		self.assertEqual(measured[1].width, len("402.15"))


class TestStayingWithTheTable(unittest.TestCase):
	"""A layout must not outlive its table. Easy Table Navigator clears its bindings on every
	focus change for the same reason, and a watchlist's columns carried onto the next page are
	worse than no columns, because they lay out something that is not there."""

	def test_stillInTheSameTable(self):
		document = watchlist()
		source = sourceOver(document)
		self.assertTrue(source.isStillHere(FakeFocus(document)))

	def test_aDifferentTableIsNotThisOne(self):
		source = sourceOver(watchlist())
		other = FakeTableDocument([list(line) for line in WATCHLIST], tableID=2)
		self.assertFalse(source.isStillHere(FakeFocus(other)))

	def test_leavingTheTableAltogether(self):
		source = sourceOver(watchlist())
		self.assertFalse(source.isStillHere(FakeFocus(NoTableDocument([["a"]]))))

	def test_movingWithinTheTableIsFollowed(self):
		"""In a table the object does not move: the caret moves between cells of the same
		page, so asking the document where its selection is now is the only thing that
		answers."""
		document = watchlist(row=2)
		source = sourceOver(document)
		document.row = 3
		source.setCurrent(FakeFocus(document))
		self.assertEqual(source.row, 3)
		self.assertEqual(textsOf(source.blockAtCursor())[0], "F")


class TestRoutingIntoTheDocument(unittest.TestCase):
	def test_aLiveFlowPutsTheCaretInTheCell(self):
		document = watchlist(row=2)
		source = sourceOver(document, live=True)
		cells = rowCellsOf(source.blockAtCursor().block.region)
		cells[1].region.routeTo(0)
		moved = [where for where, holder in document.carets if holder["caret"]]
		self.assertEqual(moved, [(2, 2)])

	def test_aViewerMovesNothing(self):
		"""A viewer reads nothing into the document, and routing is reading something in."""
		document = watchlist(row=2)
		source = sourceOver(document, live=False)
		cells = rowCellsOf(source.blockAtCursor().block.region)
		cells[1].region.routeTo(0)
		self.assertEqual([where for where, holder in document.carets if holder["caret"]], [])
