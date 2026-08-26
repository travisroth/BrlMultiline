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

from ._stubs import FakeGrid, FakeGridCell, FakeListView, FakeNavigatorObject, installStubs

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
		self.item.positionInfo = {}
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


class TestATableWhoseCellsAreObjects(unittest.TestCase):
	"""The shape the reader found. Both places they tried said "not in a table", and both are
	`NVDAObjects.behaviors.RowWithFakeNavigation`: Outlook's message list rows are
	`outlook.UIAGridRow`, whose children are the fields, and File Explorer's file list is a UIA
	grid whose cells carry `GridItemPattern` and `TableItemPattern`.

	Where the focus lands differs between the two and there is no arranging that — Outlook
	focuses the row, Explorer focuses one property of the file — so both are tested from the
	object the reader is actually on."""

	FILES = [
		["report.docx", "26/08/2026 09:14", "Word document", ("Status", "Always available")],
		["notes.md", "26/08/2026 11:02", "Markdown", ("Status", "Available offline")],
	]

	COLUMNS = ["Name", "Date modified", "Type", "Status"]

	def grid(self, rows=None, headers=None, columnCount=None):
		return FakeGrid(rows or self.FILES, headers=headers or self.COLUMNS, columnCount=columnCount)

	def test_aRowWhoseChildrenAreCellsIsATable(self):
		"""Outlook, where the focus is on the message rather than on one of its fields."""
		grid = self.grid()
		table = flowObjectTable.tableFor(grid.item(1))
		self.assertIsInstance(table, flowObjectTable.CellObjectTable)
		self.assertEqual(table._getTableDimensions(None), (2, 4))

	def test_aCellIsATableToo(self):
		"""File Explorer, where the focus lands on a property of the file. A test that only
		knew how to recognise rows said "not in a table" while standing in one."""
		grid = self.grid()
		cell = grid.item(2).cellObjects[2]
		table = flowObjectTable.tableFor(cell)
		self.assertIsInstance(table, flowObjectTable.CellObjectTable)
		where = table._getTableCellCoords(table.selection)
		self.assertEqual((where.row, where.col), (2, 3))

	def test_aRowInHandSaysNothingAboutTheColumn(self):
		"""Outlook focuses the message rather than one of its fields, and then the cursor goes
		on the first column of it."""
		grid = self.grid()
		table = flowObjectTable.tableFor(grid.item(1))
		self.assertEqual(table._getTableCellCoords(table.selection).col, 1)

	def test_aRunOfOrdinaryChildrenIsNotATable(self):
		"""A tree view item has children too, and they are items rather than cells. What tells
		them apart is that a cell says which column it is in."""
		plain = FakeNavigatorObject("a tree item")
		plain.children = [FakeNavigatorObject("a child"), FakeNavigatorObject("another")]
		self.assertIsNone(flowObjectTable.tableFor(plain))

	def test_aCellIsFoundByItsOwnColumnNumber(self):
		"""Rather than by position, which is the only answer that survives a row with a missing
		cell in it."""
		grid = self.grid()
		table = flowObjectTable.tableFor(grid.item(1))
		cell = table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, 3)
		self.assertEqual(cell.text, "Word document")

	def test_aMissingColumnIsMissingRatherThanItsNeighbour(self):
		grid = self.grid()
		row = grid.item(1)
		row.cellObjects = [cell for cell in row.cellObjects if cell.columnNumber != 2]
		table = flowObjectTable.tableFor(row)
		with self.assertRaises(LookupError):
			table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, 2)

	def test_aRowThatNumbersNothingIsReadByPosition(self):
		"""A row of plain children amounts to a row read left to right."""
		grid = self.grid()
		row = grid.item(1)
		for cell in row.cellObjects:
			cell.columnNumber = None
		table = flowObjectTable.CellObjectTable(grid, row=row)
		self.assertEqual(table.cellObject(row, 3).name, "Word document")

	def test_theHeaderComesFromTheCell(self):
		grid = self.grid()
		table = flowObjectTable.tableFor(grid.item(1))
		cell = table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, 2)
		self.assertEqual(flowTableSource.declaredHeader(cell), "Date modified")

	def test_aWidthTheGridWillNotSayIsCountedFromARow(self):
		"""A grid that does not carry a column count still has a row whose cells can be
		counted, and a row of a table is the width of the table by definition."""
		grid = self.grid(columnCount=None)
		table = flowObjectTable.tableFor(grid.item(1))
		self.assertEqual(table.numCols, 4)

	def test_theGridsOwnAnswerIsPreferred(self):
		grid = self.grid(columnCount=9)
		table = flowObjectTable.tableFor(grid.item(1))
		self.assertEqual(table.numCols, 9)

	def test_aRowsCellsAreWalkedOnce(self):
		"""A page of four columns would otherwise walk the row four times, and NVDA builds an
		object for every child each time it is asked."""
		grid = self.grid()
		row = grid.item(1)
		table = flowObjectTable.tableFor(row)
		row.builds = 0
		for column in (1, 2, 3, 4):
			table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, column)
		self.assertEqual(row.builds, 1)

	def test_routingIntoACellGoesToTheCell(self):
		"""Where the cells are objects there is something to go to, which is what arrowing
		across a grid does."""
		grid = self.grid()
		table = flowObjectTable.tableFor(grid.item(1))
		table._getTableCellAt(flowObjectTable.TABLE_ID, None, 2, 2).updateCaret()
		self.assertTrue(grid.item(2).cellObjects[1].focused)


class TestWhatACellSays(unittest.TestCase):
	"""The name, which is what NVDA speaks for such a cell and what `outlook.UIAGridRow` joins
	together to name a whole row — unless the name is only the column's header."""

	def test_theNameIsTheContent(self):
		cell = FakeGridCell("report.docx", 1, header="Name")
		self.assertEqual(flowObjectTable.cellText(cell, "Name"), "report.docx")

	def test_aNameThatIsOnlyTheHeaderIsALabel(self):
		"""File Explorer's property cells: the name is "Status" and the value is "Always
		available on this device". A column already says what it is — that is the whole
		argument for laying a table out spatially — so the value is the content."""
		cell = FakeGridCell("Status", 4, header="Status", value="Always available on this device")
		self.assertEqual(
			flowObjectTable.cellText(cell, "Status"),
			"Always available on this device",
		)

	def test_aLabelWithNothingBehindItKeepsItsName(self):
		cell = FakeGridCell("Status", 4, header="Status", value=None)
		self.assertEqual(flowObjectTable.cellText(cell, "Status"), "Status")

	def test_aCellWithNoNameFallsBackToItsValue(self):
		cell = FakeGridCell("", 2, header="Size", value="4 KB")
		self.assertEqual(flowObjectTable.cellText(cell, "Size"), "4 KB")

	def test_theHeaderIsWrittenAsOneLine(self):
		"""Several header cells come back joined, and a pinned header row is one row."""
		cell = FakeGridCell("x", 1, header="Quarter\nEnding")
		self.assertEqual(flowObjectTable.headerTextOf(cell), "Quarter Ending")


class TestReadingAGridThroughTheBand(unittest.TestCase):
	"""The same proof the list view got: everything above the seam is the code a web page's
	table goes through, and none of it was told that a grid exists."""

	FILES = [
		["report.docx", "Word document", ("Status", "Always available")],
		["notes.md", "Markdown", ("Status", "Available offline")],
		["budget.xlsx", "Workbook", ("Status", "Available offline")],
	]

	def source(self, columns=(1, 2, 3)):
		grid = FakeGrid(self.FILES, headers=["Name", "Type", "Status"])
		handle = flowTableSource.tableAt(grid.item(1))
		self.assertIsNotNone(handle)
		return grid, flowTableSource.TableFlowSource(handle, columns)

	def test_aRowIsABlockOfCells(self):
		_grid, source = self.source()
		block = source.blockAtCursor().block
		self.assertEqual(block.region.cellFor(1).region.rawText, "report.docx")
		self.assertEqual(block.region.cellFor(3).region.rawText, "Always available")

	def test_theHeadersReachTheMeasurement(self):
		grid = FakeGrid(self.FILES, headers=["Name", "Type", "Status"])
		handle = flowTableSource.tableAt(grid.item(1))
		measured = {found.index: found.label for found in flowTableSource.measure(handle)}
		self.assertEqual(measured, {1: "Name", 2: "Type", 3: "Status"})

	def test_walkingDownReadsTheNextRow(self):
		_grid, source = self.source()
		first = source.blockAtCursor().block
		self.assertIn("notes.md", source.blockAfter(first.blockId).block.region.rawText)

	def test_twoReadsOfTheSameGridAreTheSameTable(self):
		grid = FakeGrid(self.FILES, headers=["Name", "Type", "Status"])
		first = flowTableSource.tableAt(grid.item(1))
		second = flowTableSource.tableAt(grid.item(2).cellObjects[0])
		self.assertTrue(flowTableSource.sameTable(first.key, second.key))


class TestAListThePlatformBuildsAFewItemsAtATime(unittest.TestCase):
	"""File Explorer's file list, which is what the reader's second report was. It has seventy
	nine files and fourteen children: the platform makes the ones on screen and no more. The
	band showed one row and said the table had ended, because the shape was taken from what had
	been built rather than from what is there."""

	FILES = [[f"file{number}.py", ("Status", f"state {number}")] for number in range(1, 80)]

	def grid(self, realized=14, focused=50):
		view = FakeGrid(self.FILES, headers=["Name", "Status"], realized=realized)
		return view, view.item(focused)

	def test_theShapeIsWhatTheRowSaysItIsOneOf(self):
		""" "Fifty of seventy-nine", which is what NVDA speaks and what `positionInfo` carries.
		Not `childCount`, which counts only the items that exist."""
		view, item = self.grid()
		table = flowObjectTable.tableFor(item)
		self.assertEqual(view.childCount, 14)
		self.assertEqual(table.numRows, 79)

	def test_aListThatIsAllThereIsStillCounted(self):
		view, item = self.grid(realized=None)
		self.assertEqual(flowObjectTable.tableFor(item).numRows, 79)

	def test_aRowIsReachedBySteppingRatherThanIndexing(self):
		"""Asking for the sixtieth child of a list that has built fourteen answers nothing at
		all. Stepping is what NVDA's own object navigation does, and what this add-on's
		run-of-objects flow already did successfully in the very same list."""
		view, item = self.grid()
		table = flowObjectTable.tableFor(item)
		view.builds = 0
		cell = table._getTableCellAt(flowObjectTable.TABLE_ID, None, 53, 1)
		self.assertEqual(cell.text, "file53.py")
		self.assertEqual(view.builds, 0)

	def test_theRowsSteppedPastAreRemembered(self):
		"""A walk of five rows is five steps rather than five walks. The window asks for them in
		order, which is what makes stepping pay."""
		view, item = self.grid()
		table = flowObjectTable.tableFor(item)
		table._getTableCellAt(flowObjectTable.TABLE_ID, None, 55, 1)
		self.assertEqual(
			sorted(number for number, found in table._rows.items() if found is not None),
			[50, 51, 52, 53, 54, 55],
		)

	def test_steppingGoesBackwardsToo(self):
		view, item = self.grid()
		table = flowObjectTable.tableFor(item)
		cell = table._getTableCellAt(flowObjectTable.TABLE_ID, None, 47, 1)
		self.assertEqual(cell.text, "file47.py")

	def test_aJumpTooFarToStepIsNotStepped(self):
		"""A jump the length of a mailbox is not a reader reading on, and walking it item by
		item would be a walk of the mailbox. The table is asked to hand the row over instead,
		which works for a list that has built all its items."""
		view, item = self.grid(realized=None, focused=1)
		table = flowObjectTable.tableFor(item)
		view.builds = 0
		cell = table._getTableCellAt(flowObjectTable.TABLE_ID, None, 79, 1)
		self.assertEqual(cell.text, "file79.py")
		self.assertEqual(view.builds, 1)

	def test_readingOnThroughTheBandWorks(self):
		"""What the reader saw instead: one row, and "end after — row 51 is outside this
		table"."""
		view, item = self.grid()
		source = flowTableSource.TableFlowSource(flowTableSource.tableAt(item), (1, 2))
		first = source.blockAtCursor().block
		self.assertIn("file50.py", first.region.rawText)
		second = source.blockAfter(first.blockId).block
		self.assertIn("file51.py", second.region.rawText)


class TestWhichHalfOfACellIsTheContent(unittest.TestCase):
	"""The reader's log drew "Name" as the content of the Name column on every row, and pinned
	"Column left" over it. Both halves were the wrong way round."""

	def test_theValueIsTheContentAndTheNameIsTheLabel(self):
		"""`explorer.UIProperty` is documented as "used for columns in Windows Explorer Details
		view", and one of them is named "Status" with the value "Always available on this
		device". NVDA speaks both because on one line the label says what you are hearing; a
		column has already said that."""
		cell = FakeGridCell("Status", 4, header="Column left", value="Always available")
		self.assertEqual(flowObjectTable.cellText(cell), "Always available")

	def test_andSuchACellHasNamedItsOwnColumn(self):
		"""A first-hand answer, and better than `columnHeaderText` — which is a resolution of
		whatever the platform points at as headers, and came back as "Column left" over the
		Name column of a file list."""
		cell = FakeGridCell("Status", 4, header="Column left", value="Always available")
		self.assertEqual(flowObjectTable.headerTextOf(cell), "Status")

	def test_aCellWithNoValueKeepsBothAnswersWhereTheyWere(self):
		"""Outlook's message list: `outlook.UIAGridRow`'s children are text elements, and it is
		their names that it joins together to speak a whole message."""
		cell = FakeGridCell("Re: the watchlist", 2, header="Subject")
		self.assertEqual(flowObjectTable.cellText(cell), "Re: the watchlist")
		self.assertEqual(flowObjectTable.headerTextOf(cell), "Subject")

	def test_aNameThatIsAlsoTheValueIsNotALabel(self):
		"""Some cells repeat themselves. Nothing is learnt from that, so nothing changes."""
		cell = FakeGridCell("report.docx", 1, header="Name", value="report.docx")
		self.assertEqual(flowObjectTable.cellText(cell), "report.docx")
		self.assertEqual(flowObjectTable.headerTextOf(cell), "Name")

	def test_aWholeRowReadsAsItsValues(self):
		view = FakeGrid(
			[["report.docx", ("Status", "Always available")]],
			headers=["Column left", "Position"],
		)
		table = flowObjectTable.tableFor(view.item(1))
		row = [table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, column) for column in (1, 2)]
		self.assertEqual([cell.text for cell in row], ["report.docx", "Always available"])
		self.assertEqual([cell.header for cell in row], ["Column left", "Status"])


class TestWhatItSaysAboutItself(unittest.TestCase):
	"""Written because a report could not be read without it. A file list came back as fourteen
	rows by five columns with "Column left" pinned over a column whose every row said "Name",
	and settling which of four answers was the wrong one took a guess. The answers are all
	cheap to ask for, so they are asked for and written down."""

	def table(self):
		view = FakeGrid(
			[["report.docx", ("Status", "Always available")], ["notes.md", ("Status", "Offline")]],
			headers=["Name", "Status"],
			realized=1,
		)
		return view, flowObjectTable.tableFor(view.item(1))

	def said(self):
		_view, table = self.table()
		return "\n".join(table.describe())

	def test_itSaysTheShapeAndWhereItCameFrom(self):
		said = self.said()
		self.assertIn("2 rows by 2 columns", said)
		self.assertIn("CellObjectTable", said)
		self.assertIn("the row says it is one of 2", said)
		self.assertIn("childCount: 1", said)

	def test_itSaysWhatEachColumnHoldsAndUnderWhatHeader(self):
		said = self.said()
		self.assertIn("column 1: text 'report.docx'", said)
		self.assertIn("column 2: text 'Always available' under header 'Status'", said)

	def test_aPropertyTheObjectRefusesIsSaidToHaveBeenRefused(self):
		"""Every one of these raises `NotImplementedError` on an object whose platform has no
		answer, which is an answer and is worth writing down as one."""

		class Refuses(FakeNavigatorObject):
			@property
			def rowCount(self):
				raise NotImplementedError

		view, table = self.table()
		table.table = Refuses("a grid")
		self.assertIn("rowCount: refused (NotImplementedError)", "\n".join(table.describe()))

	def test_aColumnThatCannotBeReadSaysWhyRatherThanFailing(self):
		view = FakeGrid(
			[["report.docx", "Word document", ("Status", "Always available")]],
			headers=["Name", "Type", "Status"],
			columnCount=3,
		)
		row = view.item(1)
		row.cellObjects = [cell for cell in row.cellObjects if cell.columnNumber != 2]
		table = flowObjectTable.tableFor(row)
		self.assertIn("column 2: nothing", "\n".join(table.describe()))

	def test_withNoRowInHandItSaysSo(self):
		_view, table = self.table()
		table.focused = None
		self.assertIn("no row in hand", "\n".join(table.describe()))
