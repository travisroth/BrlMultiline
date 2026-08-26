# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a list view as a table.

The list throughout is a message list, because it is the case the reader named: on one row of
braille an item is a run of fields run together, and turning the headers off in Outlook is
what a reader does about it. Eight rows of thirty-two cells is somewhere to put them instead.

What is tested here is the seam. Everything above it — the column plan, the window, the band —
is the same code a web page goes through, and the whole point of presenting a list view as
`documentBase.DocumentWithTableNavigation` is that none of it had to learn what a list is. So
these tests say what the stand-in answers, and one of them reads a whole band through it to
show that the code above really is unchanged.
"""

import unittest

from ._stubs import FakeListView, FakeNavigatorObject, installStubs

installStubs()

from brlMultiline import flowObjectTable, flowTableSource  # noqa: E402

MESSAGES = [
	["Travis Roth", "The watchlist columns", "09:14"],
	["A teammate", "Re: the watchlist columns", "09:41"],
	["A mailing list", "Digest for today", "11:02"],
]

HEADERS = ["From", "Subject", "Received"]


def listView(rows=None, headers=None, columnWidths=None):
	""":return: a list view and the item the focus is on."""
	view = FakeListView(rows or MESSAGES, headers=headers or HEADERS, columnWidths=columnWidths)
	return view, view.item(1)


class TestRecognisingOne(unittest.TestCase):
	"""A row whose cells are not objects, in a table that has more than one column. Asked for
	rather than tested with `isinstance`, because an application module may answer the same
	contract on a class of its own."""

	def test_aListItemIsARowOfItsList(self):
		view, item = listView()
		self.assertIs(flowObjectTable.rowsTable(item), view)

	def test_anOrdinaryObjectIsNot(self):
		self.assertIsNone(flowObjectTable.rowsTable(FakeNavigatorObject("a button")))

	def test_anObjectThatCannotAnswerForItsColumnsIsNot(self):
		"""The contract is what matters rather than where the object sits: a button inside a
		list view is not a row of it."""
		view, _item = listView()
		stranger = FakeNavigatorObject("a button")
		stranger.parent = view
		self.assertIsNone(flowObjectTable.rowsTable(stranger))

	def test_aListOfOneColumnIsALeftAsAList(self):
		"""NVDA reads a list well already. The layout has something to offer from the second
		column, which is where a reader starts having to remember what a value was."""
		view, item = listView(rows=[["only"], ["one"]], headers=["Name"])
		self.assertIsNone(flowObjectTable.rowsTable(item))

	def test_anItemWithNoTableAboveItIsNot(self):
		view, item = listView()
		item.parent = None
		self.assertIsNone(flowObjectTable.rowsTable(item))


class TestTheThreeQuestions(unittest.TestCase):
	"""`flowTableSource` asks a document which cell the cursor is in, how big the table is, and
	for the cell at a coordinate. A list view answers all three once something presents it as
	though it were a document."""

	def setUp(self):
		self.view, self.item = listView()
		self.table = flowObjectTable.tableFor(self.item)

	def test_theShapeIsTheTablesOwnAnswer(self):
		"""`rowCount` and `columnCount` rather than counting children: a list view answers the
		first with one window message and the second by building every item."""
		self.assertEqual(self.table._getTableDimensions(None), (3, 3))

	def test_whereTheReaderIsIsWhichRowTheyAreOn(self):
		cell = self.table._getTableCellCoords(self.table.selection)
		self.assertEqual((cell.row, cell.col), (1, 1))

	def test_movingToAnotherItemMovesTheRow(self):
		self.table.moveTo(self.view.item(3))
		self.assertEqual(self.table._getTableCellCoords(self.table.selection).row, 3)

	def test_anObjectThatCannotSayWhichRowItIsRefuses(self):
		"""Which is how `tableAt` hears no. Neither answer is worked out by counting, so a list
		of ten thousand messages costs the same as a list of three."""
		self.item.positionInSet = 0
		with self.assertRaises(LookupError):
			self.table._getTableCellCoords(self.table.selection)

	def test_aCellIsItsColumnsContent(self):
		cell = self.table._getTableCellAt(flowObjectTable.TABLE_ID, None, 2, 2)
		self.assertEqual(cell.text, "Re: the watchlist columns")

	def test_aCoordinateTheListHasNotGotRaises(self):
		for row, column in ((0, 1), (4, 1), (1, 0), (1, 4)):
			with self.assertRaises(LookupError):
				self.table._getTableCellAt(flowObjectTable.TABLE_ID, None, row, column)

	def test_anotherTablesIdentifierRaises(self):
		with self.assertRaises(LookupError):
			self.table._getTableCellAt("someone else's", None, 1, 1)


class TestTheHeadersTheListDeclares(unittest.TestCase):
	"""A list view's headers are declared in exactly the sense that matters: NVDA asks the
	header control for them. They arrive as the same control field attribute a browse mode
	document puts on a cell, so nothing above the seam knows where the table came from."""

	def setUp(self):
		self.view, self.item = listView()
		self.table = flowObjectTable.tableFor(self.item)

	def test_aCellCarriesItsColumnsHeader(self):
		cell = self.table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, 3)
		self.assertEqual(flowTableSource.declaredHeader(cell), "Received")

	def test_aListWithNoHeaderControlDeclaresNothing(self):
		view = FakeListView([["a", "b"], ["c", "d"]], headers=[])
		table = flowObjectTable.tableFor(view.item(1))
		cell = table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, 1)
		self.assertEqual(flowTableSource.declaredHeader(cell), "")

	def test_theyReachTheMeasurement(self):
		handle = flowTableSource.tableAt(self.item)
		measured = {item.index: item.label for item in flowTableSource.measure(handle)}
		self.assertEqual(measured, {1: "From", 2: "Subject", 3: "Received"})

	def test_andNoRowOfTheListIsSpentOnThem(self):
		"""The whole difference from a web page's table: a list view's headers are not one of
		its items, so every item is content and the first one is not a heading."""
		handle = flowTableSource.tableAt(self.item)
		source = flowTableSource.TableFlowSource(handle, (1, 2, 3), pinHeaders=True)
		self.assertEqual(source.firstRow, 1)
		self.assertIn("Travis Roth", source.blockAtCursor().block.region.rawText)


class TestAColumnTheReaderHasHidden(unittest.TestCase):
	"""A list view keeps columns the reader has hidden and counts them; what says they are
	hidden is that their rectangle has no width. `sysListView32` skips them on exactly that
	test, which is better than the browse mode source's — there a hidden column has to be
	*inferred* from every cell in a sample being empty."""

	def test_aColumnWithNoWidthIsNotRead(self):
		view, item = listView(columnWidths=[100, 0, 80])
		table = flowObjectTable.tableFor(item)
		with self.assertRaises(LookupError):
			table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, 2)

	def test_andIsLeftOutOfTheLayout(self):
		view, item = listView(columnWidths=[100, 0, 80])
		handle = flowTableSource.tableAt(item)
		hidden = [found.index for found in flowTableSource.measure(handle) if found.hidden]
		self.assertEqual(hidden, [2])

	def test_aListThatCannotSayIsBelieved(self):
		"""A column is showing unless something says otherwise. A row that answers nothing
		about where its columns are is not a row saying they are all hidden."""
		view, item = listView(columnWidths=None)
		table = flowObjectTable.tableFor(item)
		self.assertEqual(
			table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, 2).text, "The watchlist columns"
		)


class TestGoingToACell(unittest.TestCase):
	"""A document cell can be pointed at; a list view cell cannot, because the only thing the
	reader can go to is the item."""

	def test_routingIntoACellFocusesItsRow(self):
		view, item = listView()
		table = flowObjectTable.tableFor(item)
		cell = table._getTableCellAt(flowObjectTable.TABLE_ID, None, 3, 2)
		cell.updateCaret()
		self.assertTrue(view.item(3).focused)

	def test_aCellWithNoRowGoesNowhereRatherThanFailing(self):
		flowObjectTable.ObjectCellInfo("a value").updateCaret()


class TestTellingOneListFromAnother(unittest.TestCase):
	"""A stand-in is built afresh every time the reader is asked where they are, and NVDA
	builds a fresh object for the same control whenever it is asked. Identity therefore says
	"somewhere else" on every arrow key, and the reader's layout would be taken away each time
	they moved to the next message."""

	def test_twoReadsOfTheSameListAreTheSameTable(self):
		view, item = listView()
		first = flowTableSource.tableAt(item)
		second = flowTableSource.tableAt(view.item(2))
		self.assertIsNot(first.document, second.document)
		self.assertTrue(flowTableSource.sameTable(first.key, second.key))

	def test_anotherListIsNot(self):
		_view, item = listView()
		_other, elsewhere = listView(rows=[["x", "y"], ["z", "w"]], headers=["A", "B"])
		self.assertFalse(
			flowTableSource.sameTable(
				flowTableSource.tableAt(item).key,
				flowTableSource.tableAt(elsewhere).key,
			),
		)

	def test_theSourceKnowsTheReaderIsStillInIt(self):
		view, item = listView()
		source = flowTableSource.TableFlowSource(flowTableSource.tableAt(item), (1, 2, 3))
		self.assertTrue(source.isStillHere(view.item(3)))
		self.assertFalse(source.isStillHere(FakeNavigatorObject("a button")))


class TestReadingAWholeBandOfIt(unittest.TestCase):
	"""The point of the seam. Everything here is the code a web page's table goes through, and
	none of it was told that a list view exists."""

	def source(self, columns=(1, 2, 3)):
		view, item = listView()
		handle = flowTableSource.tableAt(item)
		self.assertIsNotNone(handle)
		return view, flowTableSource.TableFlowSource(handle, columns)

	def test_aRowIsABlockOfCells(self):
		_view, source = self.source()
		block = source.blockAtCursor().block
		self.assertEqual([cell.index for cell in block.region.cells], [1, 2, 3])
		self.assertEqual(block.region.cellFor(3).region.rawText, "09:14")

	def test_walkingDownTheListReadsTheNextItem(self):
		_view, source = self.source()
		first = source.blockAtCursor().block
		second = source.blockAfter(first.blockId).block
		self.assertIn("A teammate", second.region.rawText)

	def test_theEndOfTheListIsTheEndOfTheStream(self):
		_view, source = self.source()
		last = source.blockAt(flowTableSource.BlockId(generation=0, bookmark=3, unit="row"))
		self.assertIsNone(source.blockAfter(last.block.blockId).block)

	def test_onlyThePagesColumnsAreRead(self):
		"""Every cell is a call into the list, and a column the plan dropped is a call for
		something nobody will feel."""
		view, source = self.source(columns=(1, 3))
		view.item(1).reads.clear()
		source.blockAtCursor()
		self.assertEqual(view.item(1).reads, [1, 3])

	def test_theRowTheReaderIsOnIsNotFetchedAgain(self):
		"""The focus event has just handed that object over. Searching the list for it would be
		a search of the list to arrive back where we started."""
		view, source = self.source()
		view.builds = 0
		source.blockAtCursor()
		self.assertEqual(view.builds, 0)

	def test_aRowIsFetchedOnceHoweverManyColumnsItHas(self):
		view, source = self.source()
		view.builds = 0
		source.blockAt(flowTableSource.BlockId(generation=0, bookmark=2, unit="row"))
		self.assertEqual(view.builds, 1)


if __name__ == "__main__":
	unittest.main()
