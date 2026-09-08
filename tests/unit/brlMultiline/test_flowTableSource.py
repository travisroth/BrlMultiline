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

from ._stubs import (
	FakeFieldCommand,
	FakeNavigatorObject,
	FakeTableDocument,
	NoTableDocument,
	installStubs,
)

installStubs()

from brlMultiline import flowTableSource  # noqa: E402
from brlMultiline.flow import BlockId, CallCancelled, ResultKind  # noqa: E402
from brlMultiline.flowTable import rowCellsOf  # noqa: E402
from brlMultiline.flowTableSource import (  # noqa: E402
	TableFlowSource,
	cellRegion,
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

	def test_theSameTableMeasuresTheSameFromEitherEndOfIt(self):
		"""**The reader's question: why does the arithmetic differ when the data has not?**

		The sample reads forward from the caret, so a reader standing on the blank row under
		a nine row worksheet sampled two rows — the header, and their own empty one. Every
		column was then sized to its heading alone and every value in the table wrapped, while
		the same sheet laid out from the top came out right.
		"""
		# A short last row, so that measuring from it alone is visibly not measuring the
		# table — which is exactly what the reader's blank row under the data was.
		rows = [["Symbol", "Last"]] + [[f"SYMBOL{n}", f"{n}000.00"] for n in range(8)] + [["Z", "1"]]
		fromTheTop = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=1))))
		fromTheEnd = measure(tableAt(FakeFocus(FakeTableDocument(rows, row=len(rows)))))
		self.assertEqual(
			[(item.width, item.typicalWidth) for item in fromTheEnd],
			[(item.width, item.typicalWidth) for item in fromTheTop],
		)

	def test_soTheRowsBehindAreReadWhenThereAreNoneAhead(self):
		"""Backwards from the caret, and no more rows than reading forward would have."""
		rows = [["Symbol", "Last"]] + [[f"SYM{n}", f"{n}.00"] for n in range(20)]
		document = FakeTableDocument(rows, row=len(rows))
		document.reads.clear()
		measure(tableAt(FakeFocus(document)), sample=8)
		read = {row for row, _column in document.reads}
		# The sample, and the header row that is always read beside it.
		self.assertLessEqual(len(read), 9)
		self.assertIn(len(rows), read)
		self.assertIn(len(rows) - 1, read)

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


class TestAReadThatWasCancelledIsNotAnEmptyCell(unittest.TestCase):
	"""When NVDA's watchdog decides the core has frozen it cancels every COM call the main
	thread makes, and NVDA turns that into `CallCancelled` — not a `COMError`, not an
	`OSError`.

	This module used to catch `LookupError` and `OSError` as "there is no cell at this
	coordinate", which is what a merged cell looks like, and everything else as a cell that
	could not be read. A cancellation fell into the second, was logged at debug level and
	became an empty column; on an Excel sheet all twenty-one columns went that way at once,
	the plan came out empty, and the reader was told the table would not lay out in columns.
	"""

	def _cancelling(self):
		document = watchlist()
		real = document._getTableCellAt

		def cancelled(*args, **kwargs):
			raise CallCancelled("COM call cancelled")

		document._getTableCellAt = cancelled
		self.addCleanup(setattr, document, "_getTableCellAt", real)
		return tableAt(FakeFocus(document))

	def test_theCancellationGoesUpToWhoeverAskedForTheReading(self):
		handle = self._cancelling()
		with self.assertRaises(CallCancelled):
			cellRegion(handle, 2, 1)

	def test_andOutOfTheMeasurementWithIt(self):
		"""Which is the one place that can tell the difference between a table with nothing
		in it and a table nothing could be got out of."""
		handle = self._cancelling()
		with self.assertRaises(CallCancelled):
			measure(handle)

	def test_aCellTheTableHasNotGotIsStillJustAHole(self):
		"""The merged cell case, which is what the broad catch was there for."""
		handle = tableAt(FakeFocus(FakeTableDocument([["one", None], ["two", "three"]], row=1)))
		self.assertIsNone(cellRegion(handle, 1, 2))
		self.assertIsNotNone(cellRegion(handle, 2, 2))


class TestACancelledReadFromAnyStageSaysSo(unittest.TestCase):
	"""**Not "there is no table here".** When NVDA's watchdog decides the core has frozen it
	cancels every COM call the main thread makes, and every stage of building a table flow is
	one: recognising it, asking a worksheet how far it goes, finding what its columns are
	called, reading its first row.

	A review found each of those swallowed by a broad catch somewhere below and reported as
	something else — "not a table", "no headers", a row that could not be read. They are all
	the same thing, the core was busy, and the reader can act on it: ask again in a moment.
	"""

	def _cancelling(self, name):
		"""Make one stage of the reading raise what a cancelled COM call raises."""
		real = getattr(flowTableSource, name)

		def stop(*args, **kwargs):
			raise CallCancelled("NVDA stopped waiting")

		setattr(flowTableSource, name, stop)
		self.addCleanup(setattr, flowTableSource, name, real)

	def test_recognisingTheTableIsNotSwallowed(self):
		document = FakeTableDocument(WATCHLIST, row=2, col=1)
		obj = FakeNavigatorObject("a page", treeInterceptor=document)
		document._getTableCellCoords = _raisesCancelled
		with self.assertRaises(CallCancelled):
			flowTableSource.tableAt(obj)

	def test_namingTheTableIsNotSwallowedEither(self):
		"""A table named from reads that never happened is a table matched against somebody
		else's saved layout — or against nothing, so the reader's own is not found."""
		from brlMultiline import flowTableIdentity

		document = FakeTableDocument(WATCHLIST, row=2, col=1)
		document.whereIsIt = _raisesCancelled
		handle = flowTableSource.tableAt(
			FakeNavigatorObject("a page", treeInterceptor=document),
		)
		with self.assertRaises(CallCancelled):
			flowTableIdentity.whereOf(handle)

	def test_andTheBuildSaysTheTableCouldNotBeRead(self):
		from brlMultiline import flowBuild

		document = FakeTableDocument(WATCHLIST, row=2, col=1)
		obj = FakeNavigatorObject("a page", treeInterceptor=document)
		self._cancelling("measure")
		notes = []
		self.assertIsNone(flowBuild.buildTableController(obj=obj, notes=notes))
		self.assertTrue(any(isinstance(note, flowBuild.Unreadable) for note in notes))

	def test_andSoDoesOneFromAnyOtherStage(self):
		"""The catch is around the whole of it rather than around the measuring alone."""
		from brlMultiline import flowBuild

		document = FakeTableDocument(WATCHLIST, row=2, col=1)
		obj = FakeNavigatorObject("a page", treeInterceptor=document)
		self._cancelling("declaredHeaders")
		notes = []
		self.assertIsNone(flowBuild.buildTableController(obj=obj, notes=notes))
		self.assertTrue(any(isinstance(note, flowBuild.Unreadable) for note in notes))


def _raisesCancelled(*args, **kwargs):
	raise CallCancelled("NVDA stopped waiting")


class TestACellThatCarriesItsOwnHeader(unittest.TestCase):
	"""An object table's cell holds its column's name as a string and then encodes it into a
	control field so that it looks like a document's cell to everything downstream.

	Reading it back out of that field is a round trip whose only purpose is uniformity, and a
	round trip is somewhere an answer can be lost: NVDA's `FieldCommand` and `ControlField`
	are particular about what they are made of, and the decoding is guarded so that a failure
	comes back as "no header" rather than as an error. Where the answer is in hand, it is
	taken.
	"""

	def _cell(self, header, fields=None):
		from brlMultiline.flowObjectTable import ObjectCellInfo

		cell = ObjectCellInfo("AAPL", header=header)
		if fields is not None:
			cell.getTextWithFields = fields
		return cell

	def test_theHeaderIsTakenFromTheCell(self):
		self.assertEqual(flowTableSource.declaredHeader(self._cell("Symbol")), "Symbol")

	def test_andNotThroughTheFieldItEncodesOneInto(self):
		def explode(formatConfig=None):
			raise RuntimeError("gone")

		self.assertEqual(
			flowTableSource.declaredHeader(self._cell("Symbol", fields=explode)),
			"Symbol",
		)

	def test_aCellThatNamesNothingStillNamesNothing(self):
		self.assertEqual(flowTableSource.declaredHeader(self._cell("")), "")

	def test_andItIsWrittenAsOneLine(self):
		"""Two header cells above one column come back separated, and a pinned row is one row."""
		self.assertEqual(flowTableSource.declaredHeader(self._cell("Last\nprice")), "Last price")

	def test_aDocumentsCellIsStillReadThroughItsFields(self):
		"""Which is where a browse mode document puts it, and the only place it is."""
		document = FakeTableDocument([["a", "b"], ["c", "d"]], columnHeaders={1: "Symbol"})
		handle = tableAt(FakeFocus(document))
		self.assertEqual(flowTableSource.declaredHeader(cellRegion(handle, 2, 1).info), "Symbol")

	def test_andARowHeaderIsStillAskedOfTheFields(self):
		"""A cell of an object table answers for its column and not for its row."""
		self.assertEqual(flowTableSource.declaredHeader(self._cell("Symbol"), axis="row"), "")


class TestTheCheapSeamsAreAskedOfOurOwnStandInsOnly(unittest.TestCase):
	"""The two optional ways a table can be measured more cheaply are looked up by name, and
	the other kind of table this reads is a browse mode document — a foreign object where a
	name means whatever the application that wrote it decided it means.

	Which is not hypothetical: the stand-in these tests use holds a table's column headers in
	an attribute called exactly `columnHeaders`, and calling that as a method would have
	written a debug warning on every measurement of every web page.
	"""

	def test_aBrowseModeDocumentIsNotAskedForARowAtOnce(self):
		handle = tableAt(FakeFocus(watchlist()))
		self.assertIsNone(flowTableSource.rowTextOf(handle, 1, 1, 3))

	def test_norForItsColumnsNamesEvenWhereItHasThatVeryAttribute(self):
		document = FakeTableDocument([["a", "b"]], columnHeaders={1: "Symbol"})
		handle = tableAt(FakeFocus(document))
		self.assertIsInstance(document.columnHeaders, dict)
		self.assertIsNone(flowTableSource.columnHeadersOf(handle, 1, 2))

	def test_andItsHeadersStillReachTheMeasurement(self):
		"""Through the cells, where a browse mode document declares them. Unchanged, and
		checked here because the gate above is what could have stopped it."""
		document = FakeTableDocument([["a", "b"], ["c", "d"]], columnHeaders={1: "Symbol"})
		measured = measure(tableAt(FakeFocus(document)))
		self.assertEqual(measured[0].label, "Symbol")
		self.assertTrue(measured[0].declared)


class TestAColumnDrawnWithoutCapitalSigns(unittest.TestCase):
	"""A stock symbol is written in capitals, and in a six dot table an all-capitals word
	carries the capitals-word indicator in front of it — dot 6 twice. `AAPL` is six cells and
	`aapl` is four; `BRK.B` is eight against five. On a column sized for a symbol that is the
	difference between the value fitting and being cut.

	liblouis takes no mode for suppressing the indicator and the reader did not want a
	different braille table for one column, so the lever is the text handed to the translator.
	The regions here are one cell per character, so what these tests can say is that the text
	is lowered wherever it is read; how many cells that saves is liblouis's answer, and it was
	measured against the real tables before this was built.
	"""

	def _source(self, plainCase=(), columns=ALL_COLUMNS, live=False):
		handle = tableAt(FakeFocus(watchlist()))
		return TableFlowSource(
			handle,
			columns=columns,
			generation=1,
			live=live,
			plainCase=plainCase,
		)

	def test_theColumnTheReaderAskedForIsLowered(self):
		found = textsOf(self._source(plainCase=(1,)).blockAt(BlockId(1, 2, "row")))
		self.assertEqual(found[0], "aapl")

	def test_andNoOtherColumnIs(self):
		"""It is one column's decision, not the table's."""
		document = FakeTableDocument(
			[["Symbol", "Name"], ["AAPL", "Apple Inc"]],
			row=2,
			col=1,
		)
		handle = tableAt(FakeFocus(document))
		source = TableFlowSource(handle, columns=(1, 2), generation=1, plainCase=(1,))
		self.assertEqual(textsOf(source.blockAt(BlockId(1, 2, "row"))), ["aapl", "Apple Inc"])

	def test_andATableNobodyAskedAboutIsUntouched(self):
		found = textsOf(self._source().blockAt(BlockId(1, 2, "row")))
		self.assertEqual(found[0], "AAPL")

	def test_theWidthIsMeasuredTheWayItIsDrawn(self):
		"""Or the setting saves nothing: a column sized from text with the indicator in it is
		two cells wider than what will be drawn in it, which is the two cells the reader
		turned it on to get back."""
		handle = tableAt(FakeFocus(watchlist()))
		plain = {item.index: item.width for item in measure(handle, plainCase=(1,))}
		asIs = {item.index: item.width for item in measure(handle)}
		self.assertEqual(plain[1], asIs[1])
		labels = {item.index: item.label for item in measure(handle, plainCase=(1,))}
		self.assertEqual(labels[1], "symbol")

	def test_theHeadingGoesWithTheColumn(self):
		"""One column and one decision: the heading is cut to the same width, so the capitals
		cost it the same cells. The reader asked for it that way — "so it is just one column
		setting"."""
		document = FakeTableDocument(
			[list(line) for line in WATCHLIST],
			row=2,
			col=1,
			columnHeaders={1: "SYMBOL", 2: "LAST"},
		)
		handle = tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		source = TableFlowSource(
			handle,
			columns=(1, 2),
			generation=1,
			pinHeaders=True,
			plainCase=(1,),
		)
		found = [cell.region.rawText for cell in rowCellsOf(source.headerBlock().region)]
		self.assertEqual(found, ["symbol", "LAST"])

	def test_andARowOneHeadingBorrowedForOneGoesWithItToo(self):
		"""A table that declares nothing has its first row read as the heading, and that is
		read as a cell like any other."""
		source = self._source(plainCase=(1,), columns=(1, 2))
		found = textsOf(source.blockAt(BlockId(1, flowTableSource.HEADER_ROW, "row")))
		self.assertEqual(found, ["symbol", "Last"])

	def test_aRowReadAgainIsStillLowered(self):
		"""The question that decided where this goes. A live page is re-read every couple of
		seconds and each re-read builds the region afresh, so lowering at the moment the text
		becomes a region is applied every time — where a value lowered once and cached would
		have the capitals back the next time the poll came round."""
		source = self._source(plainCase=(1,))
		first = textsOf(source.blockAt(BlockId(1, 2, "row")))
		again = textsOf(source.blockAt(BlockId(1, 2, "row")))
		self.assertEqual(first, again)
		self.assertEqual(again[0], "aapl")

	def test_theTextItselfIsWhatChanges(self):
		self.assertEqual(flowTableSource.withoutCapitals("BRK.B"), "brk.b")


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


class TestTheHeaderTheDocumentDeclares(unittest.TestCase):
	"""Row one was always a guess, and browse mode never needed it guessed at. The virtual
	buffer backend asks IAccessible2 for a cell's header cells — which is what `<th>`,
	`scope=` and `headers=` produce — and `VirtualBuffer._normalizeControlField` resolves them
	to `table-columnheadertext` on the cell's own control field. The same attribute arrives
	from UIA and from Word by other routes."""

	MARKED = {1: "Ticker", 2: "Price", 3: "Move"}

	def document(self, headers=None, rows=None, row=2):
		document = FakeTableDocument(
			rows or [["Sym", "Last", "Chg"], ["AAPL", "182.50", "+1.25"], ["F", "9.10", "-0.05"]],
			row=row,
			col=1,
			columnHeaders=headers,
		)
		return document, flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))

	def test_aCellSaysWhatItsColumnsHeaderIs(self):
		document, handle = self.document(self.MARKED)
		region = flowTableSource.cellRegion(handle, 2, 2)
		self.assertEqual(flowTableSource.declaredHeader(region.info), "Price")

	def test_aTableThatMarksNothingUpSaysNothing(self):
		document, handle = self.document()
		region = flowTableSource.cellRegion(handle, 2, 2)
		self.assertEqual(flowTableSource.declaredHeader(region.info), "")

	def test_theInnermostFieldWins(self):
		"""A cell sits inside a row and a table, and only the cell carries the attribute — but
		taking the last one asked is the rule that stays right if that ever stops being true."""
		document, handle = self.document(self.MARKED)
		region = flowTableSource.cellRegion(handle, 2, 1)
		outer = FakeFieldCommand("controlStart", {"table-columnheadertext": "the table's"})
		inner = FakeFieldCommand("controlStart", {"table-columnheadertext": "the cell's"})
		region.info.getTextWithFields = lambda formatConfig=None: [outer, inner, "AAPL"]
		self.assertEqual(flowTableSource.declaredHeader(region.info), "the cell's")

	def test_severalHeaderCellsBecomeOneRow(self):
		"""NVDA joins a cell's header cells with newlines, and a pinned header row is one row."""
		document, handle = self.document({1: "Quarter\nEnding"})
		region = flowTableSource.cellRegion(handle, 2, 1)
		self.assertEqual(flowTableSource.declaredHeader(region.info), "Quarter Ending")

	def test_everyColumnIsAskedOnce(self):
		document, handle = self.document(self.MARKED)
		self.assertEqual(flowTableSource.declaredHeaders(handle, (1, 2, 3)), self.MARKED)

	def test_aSilentRowIsNotTheWholeAnswer(self):
		"""Found by review. A declared header belongs to the column, but only a cell that
		answers at all can say so: a half-built row of a virtualised list, or a merged cell,
		said nothing and the column then had no name. `measure` learnt that on hardware and
		takes a few rows; this took one, and it is what names a table for a saved layout."""
		document, handle = self.document(self.MARKED, row=2)
		real = flowTableSource.cellRegion

		def quietOnTheReadersRow(handle, row, column, live=False):
			region = real(handle, row, column, live=live)
			if row == 2 and region is not None:
				region.info.getTextWithFields = lambda formatConfig=None: ["quiet"]
			return region

		flowTableSource.cellRegion = quietOnTheReadersRow
		self.addCleanup(setattr, flowTableSource, "cellRegion", real)
		self.assertEqual(flowTableSource.declaredHeaders(handle, (1, 2)), {1: "Ticker", 2: "Price"})

	def test_aTableThatAnswersIsAskedOnlyOnce(self):
		"""The rows are tried in turn and the walk stops as soon as every column has spoken,
		which for a table that declares its headings is the first row asked."""
		document, handle = self.document(self.MARKED)
		asked = []
		real = flowTableSource.cellRegion
		flowTableSource.cellRegion = lambda handle, row, column, live=False: asked.append(
			(row, column),
		) or real(handle, row, column, live=live)
		self.addCleanup(setattr, flowTableSource, "cellRegion", real)
		flowTableSource.declaredHeaders(handle, (1, 2, 3))
		self.assertEqual(len(asked), 3)

	def test_andOneThatDeclaresNothingIsNotAskedForever(self):
		"""The cost of trying again falls on the table that will never answer, so it is
		bounded: a read per column per try and no more."""
		document, handle = self.document()
		asked = []
		real = flowTableSource.cellRegion
		flowTableSource.cellRegion = lambda handle, row, column, live=False: asked.append(
			(row, column),
		) or real(handle, row, column, live=live)
		self.addCleanup(setattr, flowTableSource, "cellRegion", real)
		flowTableSource.declaredHeaders(handle, (1, 2))
		self.assertLessEqual(len(asked), 2 * flowTableSource.HEADER_TRIES)
		self.assertEqual(len({row for row, _column in asked}), flowTableSource.HEADER_TRIES)

	def test_aColumnThatDeclaresNothingIsLeftOut(self):
		document, handle = self.document({2: "Price"})
		self.assertEqual(flowTableSource.declaredHeaders(handle, (1, 2, 3)), {2: "Price"})

	def test_theMeasurementTakesTheDeclaredHeaderOverRowOne(self):
		document, handle = self.document(self.MARKED)
		measured = {item.index: item.label for item in flowTableSource.measure(handle)}
		self.assertEqual(measured[1], "Ticker")
		self.assertEqual(measured[2], "Price")

	def test_andFallsBackToRowOneWithoutOne(self):
		document, handle = self.document()
		measured = {item.index: item.label for item in flowTableSource.measure(handle)}
		self.assertEqual(measured[1], "Sym")

	def test_rowOneCountsAsDataWhenTheHeaderWasDeclared(self):
		"""The typical width a column is planned for is measured from its data. Row one is data
		wherever the header came from somewhere else, and leaving it out would plan the column
		short by one row of the table."""
		document, handle = self.document(
			self.MARKED,
			rows=[["aVeryLongFirstCell", "x"], ["b", "y"], ["c", "z"]],
		)
		measured = {item.index: item for item in flowTableSource.measure(handle)}
		self.assertGreater(measured[1].typicalWidth, len("b"))
		_plain, plainHandle = self.document(rows=[["aVeryLongFirstCell", "x"], ["b", "y"], ["c", "z"]])
		borrowed = {item.index: item for item in flowTableSource.measure(plainHandle)}
		self.assertLess(borrowed[1].typicalWidth, measured[1].typicalWidth)

	def test_aDeclaredHeaderIsMeasuredByTranslatingIt(self):
		"""The only honest measurement, and the same one a cell gets. A declared header is a
		string rather than a cell of the table, so there is no region to ask."""
		document, handle = self.document(self.MARKED)
		measured = {item.index: item for item in flowTableSource.measure(handle)}
		self.assertEqual(measured[1].labelWidth, flowTableSource.headerWidth("Ticker"))
		self.assertGreater(measured[1].labelWidth, 0)


class TestPinningTheDeclaredHeader(unittest.TestCase):
	"""What the reader gets on the top row of the band, and what it costs the stream. Row one
	is skipped only where row one is what was pinned: a table that declares its headers may
	have them two rows deep, in a column rather than a row, or nowhere near the top — and then
	row one is data, and dropping it would lose a row to a guess the document contradicted."""

	ROWS = [["Sym", "Last"], ["AAPL", "182.50"], ["F", "9.10"]]

	def source(self, headers=None, pinHeaders=True, columns=(1, 2)):
		document = FakeTableDocument(self.ROWS, row=2, col=1, columnHeaders=headers)
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		return document, flowTableSource.TableFlowSource(handle, columns, pinHeaders=pinHeaders)

	def test_aMeasurementThatFoundNoneIsNotTheLastWord(self):
		"""The Excel case in one line. The measurement handed over nothing, and every cell of
		the table can name its column perfectly well; a source that took the empty hand as the
		answer never built a header row at all, and the band gave that row back to the table
		for as long as the layout lived."""
		document = FakeTableDocument(
			self.ROWS,
			row=2,
			col=1,
			columnHeaders={1: "Ticker", 2: "Price"},
		)
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		source = flowTableSource.TableFlowSource(handle, (1, 2), declared={}, pinHeaders=True)
		block = source.headerBlock()
		self.assertIsNotNone(block)
		self.assertIn("Ticker", block.region.rawText)
		self.assertIn("Price", block.region.rawText)

	def test_theDeclaredHeaderIsWhatIsPinned(self):
		_document, source = self.source({1: "Ticker", 2: "Price"})
		block = source.headerBlock()
		self.assertIn("Ticker", block.region.rawText)
		self.assertIn("Price", block.region.rawText)

	def test_rowOneIsStillReadAsContent(self):
		"""Because it is content: the headers are declared, so row one is a row of the table
		like any other."""
		_document, source = self.source({1: "Ticker", 2: "Price"})
		self.assertEqual(source.firstRow, flowTableSource.HEADER_ROW)
		result = source.blockAt(BlockId(generation=0, bookmark=1, unit="row"))
		self.assertIn("Sym", result.block.region.rawText)

	def test_withoutADeclarationRowOneIsPinnedAndSkipped(self):
		_document, source = self.source()
		block = source.headerBlock()
		self.assertIn("Sym", block.region.rawText)
		self.assertEqual(source.firstRow, flowTableSource.HEADER_ROW + 1)

	def test_aDeclaredHeaderThatIsRowOneIsNotDrawnTwice(self):
		"""**A worksheet with a marked header row**, which is what a reader marks: the row the
		table declares its headings from is row one itself, so the pinned row and the first
		row of the flow were the same row. It showed as a doubled heading the moment they
		arrowed up onto row one and the flow put that row on the band beside the copy above
		it, and it stayed there.
		"""
		document = FakeTableDocument(self.ROWS, row=1, col=1, columnHeaders={1: "Sym", 2: "Last"})
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		source = flowTableSource.TableFlowSource(handle, (1, 2), pinHeaders=True)
		self.assertTrue(source.pinnedIsRowOne)
		self.assertEqual(source.firstRow, flowTableSource.HEADER_ROW + 1)
		self.assertIn("Sym", source.headerBlock().region.rawText)

	def test_andTheReaderStandingOnItStillHasACursor(self):
		"""Once it is drawn above the band and no longer in it, the pinned row is the only
		place their cursor can be."""
		document = FakeTableDocument(self.ROWS, row=1, col=2, columnHeaders={1: "Sym", 2: "Last"})
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		source = flowTableSource.TableFlowSource(handle, (1, 2), pinHeaders=True)
		self.assertIsNotNone(source.headerBlock().region.brailleCursorPos)

	def test_butOneDeclaredElsewhereClaimsNoCursorAndKeepsRowOne(self):
		"""Row one is data then, and the reader standing on it is standing on the band."""
		_document, source = self.source({1: "Ticker", 2: "Price"})
		self.assertFalse(source.pinnedIsRowOne)
		self.assertEqual(source.firstRow, flowTableSource.HEADER_ROW)
		self.assertIsNone(source.headerBlock().region.brailleCursorPos)

	def test_aColumnWithDataAndNoHeadingKeepsRowOne(self):
		"""**A review found a row of the reader's data being dropped.** Two columns matching
		their declared headings was enough to call row one the header row, and the third
		column of that row held a value and declared no heading of its own — so the flow began
		at row two and "Important" was gone, with nothing saying so.

		A row is given up only when there is nothing in it but the headings themselves."""
		document = FakeTableDocument(
			[["Sym", "Last", "Important"], ["AAPL", "182.50", ""], ["F", "9.10", ""]],
			row=2,
			col=1,
			columnHeaders={1: "Sym", 2: "Last"},
		)
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		source = flowTableSource.TableFlowSource(handle, (1, 2, 3), pinHeaders=True)
		self.assertFalse(source.pinnedIsRowOne)
		self.assertEqual(source.firstRow, flowTableSource.HEADER_ROW)
		above = source.blockBefore(BlockId(generation=0, bookmark=2, unit="row"))
		self.assertIn("Important", above.block.region.rawText)

	def test_oneColumnDisagreeingIsEnoughToKeepRowOne(self):
		"""Dropping a row of data is a lost row; drawing a heading twice is a wasted one. The
		read that cannot decide keeps the row."""
		document = FakeTableDocument(self.ROWS, row=2, col=1, columnHeaders={1: "Sym", 2: "Price"})
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		source = flowTableSource.TableFlowSource(handle, (1, 2), pinHeaders=True)
		self.assertFalse(source.pinnedIsRowOne)
		self.assertEqual(source.firstRow, flowTableSource.HEADER_ROW)

	def test_andDecidingItReadsRowOneAndNothingElse(self):
		"""One row, and only the columns on the page. On a sheet that is a single call.

		Built the way the band builds it, with the measurement's headers handed over, so what
		is read here is the comparison and nothing else.
		"""
		document = FakeTableDocument(self.ROWS, row=2, col=1, columnHeaders={1: "Sym", 2: "Last"})
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		document.reads.clear()
		flowTableSource.TableFlowSource(
			handle,
			(1, 2),
			declared={1: "Sym", 2: "Last"},
			pinHeaders=True,
		)
		self.assertEqual(document.reads, [(flowTableSource.HEADER_ROW, 1), (flowTableSource.HEADER_ROW, 2)])

	def test_nothingIsPinnedWhenNothingIsAsking(self):
		"""A source with no pinned row does not look the headers up at all, and serves every
		row of the table."""
		document, source = self.source({1: "Ticker"}, pinHeaders=False)
		document.reads.clear()
		self.assertEqual(source.firstRow, flowTableSource.HEADER_ROW)
		self.assertEqual(source._declared, {})

	def test_aColumnThatDeclaresNothingIsBlankRatherThanGuessedAt(self):
		"""Mixing the two would put a guess beside an answer and give the reader no way to tell
		them apart."""
		_document, source = self.source({2: "Price"})
		block = source.headerBlock()
		self.assertIn("Price", block.region.rawText)
		self.assertNotIn("Sym", block.region.rawText)

	def test_turningThePageLooksUpTheNewColumns(self):
		"""The columns change and a header belongs to its column, so the new ones have to be
		asked about — and the old ones must not be asked about twice."""
		document, source = self.source({1: "Ticker", 2: "Price"}, columns=(1,))
		self.assertEqual(source.headerBlock().region.rawText.strip(), "Ticker")
		document.reads.clear()
		source.setColumns((2,))
		self.assertIn("Price", source.headerBlock().region.rawText)
		self.assertEqual([column for _row, column in document.reads], [2])

	def test_aTableDeclaringNothingIsStillAskedOnce(self):
		"""**A measurement that found nothing does not settle it.** This used to look nothing
		up at all once it started with no headers in hand, on the grounds that a table which
		declares none has nothing to look up — which made one empty answer permanent. An Excel
		worksheet whose every cell could name its column came out of the measurement with
		none, and no header row was ever built for it.

		So the columns are asked, and `_headersAsked` is what keeps it to once each."""
		document, source = self.source(columns=(1,))
		source.headerBlock()
		document.reads.clear()
		source.setColumns((2,))
		source.headerBlock()
		self.assertIn(2, [column for _row, column in document.reads])

	def test_andNotAskedAgainAfterThat(self):
		"""Which is what stops a question already answered "nothing" costing a search of the
		document on every redraw."""
		document, source = self.source(columns=(1,))
		source.setColumns((2,))
		source.headerBlock()
		document.reads.clear()
		source.headerBlock()
		# Row one, read as the fallback header. Nothing was looked up for the column again.
		self.assertEqual(document.reads, [(1, 2)])


class TestAColumnThatDeclaresNoHeader(unittest.TestCase):
	"""A column with no header leaves no trace in the answers, so reading the record of what
	has been asked off the answers asked such a column again on every refresh — a search of the
	document apiece, for a question already answered "nothing".
	"""

	def source(self, columns=(1, 2, 3)):
		document = FakeTableDocument(
			[["Symbol", "Last", ""], ["AAPL", "182.50", ""]],
			row=2,
			col=1,
			columnHeaders={1: "Symbol", 2: "Last"},
		)
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		return document, flowTableSource.TableFlowSource(handle, columns, pinHeaders=True)

	def test_aColumnWithNoHeaderIsNotAskedTwice(self):
		document, source = self.source()
		source.headerBlock()
		document.reads.clear()
		source.headerBlock()
		self.assertEqual(document.reads, [])

	def test_aColumnTheNextPageBringsIsStillAsked(self):
		"""Which is what the record is for: the columns change when the page turns, and their
		headers are a property of the columns."""
		document, source = self.source(columns=(1,))
		source.headerBlock()
		document.reads.clear()
		source.setColumns((2,))
		self.assertIn("Last", source.headerBlock().region.rawText)
		self.assertTrue(document.reads)
