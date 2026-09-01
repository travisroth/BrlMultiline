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

from ._stubs import (
	FORMAT_CONFIG,
	FakeGrid,
	FakeGridCell,
	FakeGridRow,
	FakeListView,
	FakeNavigatorObject,
	FakeTableDocument,
	ReportTableHeaders,
	installStubs,
	navigatedTo,
	resetConfig,
)

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
		"""However the row is reached — stepped to from the row in hand, or handed over by
		number — it is reached once and then remembered, so a page of six columns does not
		cost six searches of the list."""
		view, source = self.source()
		table = source.handle.document
		view.builds = 0
		source.blockAt(flowTableSource.BlockId(generation=0, bookmark=2, unit="row"))
		# Stepped from the row in hand, which is the neighbour it is: asking the list to hand
		# over its second child would be a search to reach what `next` already points at.
		self.assertEqual(view.builds, 0)
		self.assertIs(table._rows.get(2), view.item(2))
		held = dict(table._rows)
		source.blockAt(flowTableSource.BlockId(generation=0, bookmark=2, unit="row"))
		self.assertEqual(table._rows, held)


class TestTheHeaderReachingTheCodeThatReadsIt(unittest.TestCase):
	"""The heading a cell carries has to arrive as the attribute a browse mode document puts on
	a cell, because that is what everything above the seam reads. It did not, and nothing said
	so.

	`textInfos.FieldCommand` checks the type of what it is given: `"controlStart"` with
	anything that is not a `ControlField` raises. The cells' fields were built out of a plain
	dictionary, so the call raised every time in NVDA and never once in a test — the stand-in
	took anything — and the caller reads a cell that cannot answer as a cell that declares
	nothing. So **every object table silently declared no headers**: no header row was ever
	pinned over one, and the paging command named the columns after the reader's first row and
	then by number. The reader's own report had the cells showing their headings on the very
	same line as "this table declares no headers".
	"""

	FILES = [
		[("Name", "report.docx"), ("Date modified", "8/28/2026 9:41 AM")],
		[("Name", "notes.md"), ("Date modified", "8/27/2026 4:02 PM")],
	]

	def cell(self, column=1):
		view = FakeGrid(self.FILES)
		table = flowObjectTable.tableFor(view.item(1))
		return table.cellOf(view.item(1), column, 1)

	def test_theHeadingArrivesAsTheAttributeTheRestOfTheAddOnReads(self):
		self.assertEqual(flowTableSource.declaredHeader(self.cell()), "Name")
		self.assertEqual(flowTableSource.declaredHeader(self.cell(2)), "Date modified")

	def test_theFieldIsTheTypeNvdaDemands(self):
		"""Asserted directly, because the way this failed was a raise that something else
		caught: the header came back empty and every layer above read that as "no header"."""
		import textInfos

		fields = self.cell().getTextWithFields()
		self.assertIsInstance(fields[0].field, textInfos.ControlField)

	def test_soTheColumnsAreNamedByTheirHeadings(self):
		view = FakeGrid(self.FILES)
		handle = flowTableSource.tableAt(view.item(1))
		self.assertEqual(
			[found.label for found in flowTableSource.measure(handle)],
			["Name", "Date modified"],
		)

	def test_andAHeaderRowCanBePinnedOverThem(self):
		"""The other half of the same silence: a list view declares no header *row*, so the
		headings its cells carry are the only ones it will ever have."""
		view = FakeGrid(self.FILES)
		handle = flowTableSource.tableAt(view.item(1))
		source = flowTableSource.TableFlowSource(handle, (1, 2), pinHeaders=True)
		pinned = source.headerBlock()
		self.assertIsNotNone(pinned)
		self.assertIn("Date modified", pinned.region.rawText)


def putAfter(first, *inserted):
	"""Put objects into the sibling chain, straight after one that is already in it.

	For a group's heading between two rows, which is what a grouped list holds and what a walk
	has to step over without counting it as a row.
	"""
	after = first.next
	chain = [first, *inserted]
	for index, item in enumerate(chain[:-1]):
		item.next = chain[index + 1]
		chain[index + 1].previous = item
	chain[-1].next = after
	if after is not None:
		after.previous = chain[-1]


class TestAListWhoseChildrenAreNotAllRows(unittest.TestCase):
	"""File Explorer's file list holds a pane, a horizontal scrollbar and the column header
	before its first file; Outlook's message list holds a pane called "Vertical". Reaching for
	the table's nth child as though it were the nth row therefore handed back a scrollbar for
	row one, and every row fetched that way was three files late.

	Stepping from the row in hand is what reaches a row of a virtualised list and is unchanged.
	This is about the other route: the one call that a list which has built all its items can
	answer.
	"""

	FILES = [[f"file{number}.py", "Available"] for number in range(1, 9)]
	CLUTTER = (("", "PANE"), ("Horizontal", "SCROLLBAR"), ("Header", "HEADER"))

	def explorer(self):
		view = FakeGrid(self.FILES, decorations=self.CLUTTER)
		return view, flowObjectTable.tableFor(view.item(1))

	def test_theDecorationsAreCountedOut(self):
		view, table = self.explorer()
		self.assertEqual(table.rowsBeginAt(), len(self.CLUTTER))

	def test_soARowFetchedByNumberIsThatRow(self):
		"""A jump too far to step, which is the only time this route is taken."""
		view, table = self.explorer()
		table.focused = None
		self.assertIs(table.rowObject(2), view.item(2))

	def test_aListWithNoDecorationsIsUnchanged(self):
		view = FakeGrid(self.FILES)
		table = flowObjectTable.tableFor(view.item(1))
		table.focused = None
		self.assertEqual(table.rowsBeginAt(), 0)
		self.assertIs(table.rowObject(3), view.item(3))

	def test_aSmallFolderIsStillATable(self):
		"""The decorations are children and are not rows, so counting them as rows made a
		folder of two files admit to five — which is room for another whole group, and the
		grouping test then took the whole feature away. A fault that only appears below a
		threshold nobody chose."""
		view = FakeGrid(self.FILES[:2], decorations=self.CLUTTER)
		table = flowObjectTable.tableFor(view.item(1))
		self.assertEqual(table.childrenAdmittedTo(), 2)
		self.assertTrue(table.positionNumbersTheTable())
		self.assertIsNotNone(flowTableSource.tableAt(view.item(1)))

	def test_aWalkStepsOverWhatIsNotARow(self):
		"""A review's case: a heading between two rows was handed back as row two, because the
		walk counted every sibling as another row. The rows number the rows."""
		view = FakeGrid(self.FILES, decorations=self.CLUTTER)
		heading = FakeNavigatorObject(name="Yesterday", role="GROUPING")
		heading.parent = view
		putAfter(view.item(1), heading)
		table = flowObjectTable.tableFor(view.item(1))
		self.assertIs(table.walkTo(2), view.item(2))
		self.assertIs(table.rowObject(3), view.item(3))

	def test_andGivesUpRatherThanWalkingAListOfThem(self):
		"""The physical steps are bounded on their own, so a stretch of things that are not
		rows cannot turn a walk of one row into a walk of the list."""
		view = FakeGrid(self.FILES, decorations=self.CLUTTER)
		fillers = [FakeNavigatorObject(name=f"filler {number}", role="GROUPING") for number in range(12)]
		for filler in fillers:
			filler.parent = view
		putAfter(view.item(1), *fillers)
		table = flowObjectTable.tableFor(view.item(1))
		self.assertIsNone(table.walkTo(2))
		# And the row is still reached, by the one call a list that has built its items can
		# answer. What the bound refuses is the walk, not the row.
		self.assertIs(table.rowObject(2), view.item(2))

	def test_aChildThatIsNoRowIsNotReadAsOne(self):
		"""The check that catches a list whose clutter is not all at the front."""
		view, table = self.explorer()
		self.assertFalse(table.looksLikeARow(view.decorations[1]))
		self.assertTrue(table.looksLikeARow(view.item(1)))


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



class TestARowReadAsOneLine(unittest.TestCase):
	"""The same cell reader, serving a list that is *not* laid out in columns.

	A run of objects shows a row as one line, and until now that line could only be the row's
	own name. Outlook builds part of that name from `activeExplorer().selection`, so a row read
	while another message is selected carried that message's flags — "replied", "forwarded",
	"unread" — which is false and, on those words, alarming. The cells are the row's own
	whatever is selected, and they are already being read for the spatial layout.
	"""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)

	def row(self, *cells):
		return FakeGridRow([FakeGridCell(text, index + 1, header=header) for index, (header, text) in enumerate(cells)], position=1)

	def test_theCellsAreJoinedInOrder(self):
		said = flowObjectTable.rowTextOf(self.row(("From", "Alice"), ("Subject", "Lunch")))
		self.assertEqual(said, "From Alice, Subject Lunch")

	def test_theHeadersAreLeftOutWhenTheReaderDoesNotWantThem(self):
		"""NVDA's own setting, which is what its Outlook module follows for the same text."""
		said = flowObjectTable.rowTextOf(self.row(("From", "Alice"), ("Subject", "Lunch")), withHeaders=False)
		self.assertEqual(said, "Alice, Lunch")

	def test_anEmptyCellIsLeftOut(self):
		said = flowObjectTable.rowTextOf(self.row(("From", "Alice"), ("Size", "")), withHeaders=False)
		self.assertEqual(said, "Alice")

	def test_aCellThatOnlyRepeatsItsHeaderIsLeftOut(self):
		"""An icon column with nothing in it comes back named after itself. NVDA leaves those
		out by asking the selection whether it is flagged, which is the question this whole
		reading exists to stop trusting; a column that says nothing but its own name is the
		part of that noise this can be sure about."""
		said = flowObjectTable.rowTextOf(self.row(("From", "Alice"), ("Flag", "Flag")))
		self.assertEqual(said, "From Alice")

	def test_aColumnIsAskedItsNameOnceForTheWholeList(self):
		"""A header belongs to the column, not the row. Asking per cell of a full band is
		eight times the platform calls for one answer, and these calls are the expensive kind."""
		asked = []
		real = flowObjectTable.headerTextOf
		flowObjectTable.headerTextOf = lambda cell: asked.append(cell) or real(cell)
		self.addCleanup(setattr, flowObjectTable, "headerTextOf", real)
		headers = {}
		for _ in range(4):
			flowObjectTable.rowTextOf(self.row(("From", "Alice"), ("Subject", "Lunch")), headers=headers)
		self.assertEqual(len(asked), 2)

	def test_somethingWithNoCellsSaysNothing(self):
		"""So the caller reads it the ordinary way rather than showing an empty row."""
		self.assertEqual(flowObjectTable.rowTextOf(FakeNavigatorObject("a button")), "")

	def test_theReadingFollowsNvdasHeaderSetting(self):
		"""The one its Outlook module follows for the same text."""
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.ROWS_AND_COLUMNS.value
		self.assertIn("From", flowObjectTable.rowTextOf(self.row(("From", "Alice"))))
		FORMAT_CONFIG["reportTableHeaders"] = ReportTableHeaders.OFF.value
		self.assertEqual(flowObjectTable.rowTextOf(self.row(("From", "Alice"))), "Alice")


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


class TestATableWhoseOwnCountIsWrong(unittest.TestCase):
	"""The reader's file list answered **fourteen** to `rowCount` and seventeen to `childCount`
	while they stood on item fifty-two of seventy-nine. `rowCount` was tried first before that
	report, and it is what the report disproved: for a list the platform builds a few items at
	a time, the only count of the whole thing is the one the row carries."""

	FILES = [[f"file{number}.py", ("Status", "Available")] for number in range(1, 80)]

	def grid(self, said=14, focused=52):
		view = FakeGrid(self.FILES, headers=["Name", "Status"], realized=17)
		view.rowCountSays = said
		return view, view.item(focused)

	def test_theRowsAnswerWinsOverTheTables(self):
		view, item = self.grid()
		table = flowObjectTable.tableFor(item)
		self.assertEqual(view.rowCount, 14)
		self.assertEqual(table.numRows, 79)

	def test_aTableWithNoRowToAskIsStillCounted(self):
		"""Nothing is lost for a table that is all there: its own count is next in line."""
		view, item = self.grid()
		table = flowObjectTable.tableFor(item)
		table.focused.positionInfo = {}
		self.assertEqual(table.numRows, 14)

	def test_neverFewerThanTheRowTheReaderIsIn(self):
		"""A table cannot have fewer rows than the row somebody is standing in, and a count
		that says otherwise is counting something else."""
		view, item = self.grid()
		table = flowObjectTable.tableFor(item)
		table.focused.positionInfo = {"indexInGroup": 52, "similarItemsInGroup": None}
		self.assertEqual(table.numRows, 52)


class TestAListHasNoHeaderRow(unittest.TestCase):
	"""A web page's table puts its headings in a row of itself, and `HEADER_ROW` falls back to
	reading them there. A list view's first item is a file: pinning it would draw one of the
	reader's own rows above the rest and call it a heading."""

	def source(self, headers=None):
		view = FakeGrid(
			[["report.docx", "Word document"], ["notes.md", "Markdown"]],
			headers=headers,
		)
		handle = flowTableSource.tableAt(view.item(1))
		return view, flowTableSource.TableFlowSource(handle, (1, 2), pinHeaders=True)

	def test_aListThatDeclaresHeadersPinsThem(self):
		_view, source = self.source(headers=["Name", "Type"])
		self.assertIn("Name", source.headerBlock().region.rawText)

	def test_aListThatDeclaresNonePinsNothing(self):
		_view, source = self.source(headers=[])
		self.assertIsNone(source.headerBlock())

	def test_andNoItemOfItIsSkipped(self):
		_view, source = self.source(headers=[])
		self.assertEqual(source.firstRow, flowTableSource.HEADER_ROW)
		self.assertIn("report.docx", source.blockAtCursor().block.region.rawText)

	def test_aDocumentStillFallsBackToRowOne(self):
		"""Which is what NVDA's own table navigation assumes, and what the fallback is for."""
		document = FakeTableDocument([["Symbol", "Last"], ["AAPL", "182.50"]], row=2, col=1)
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		source = flowTableSource.TableFlowSource(handle, (1, 2), pinHeaders=True)
		self.assertIn("Symbol", source.headerBlock().region.rawText)

	def test_theReportSaysWhereThePinnedRowCameFrom(self):
		"""The line the last report needed and did not have: it showed a header reading "Column
		left  Position" over columns the same report said were headed "Name" and "Status"."""
		_view, declared = self.source(headers=["Name", "Type"])
		self.assertIn("declared by the table's own cells", declared.describeHeader())
		_other, bare = self.source(headers=[])
		self.assertIn("first row is not headings", bare.describeHeader())


class TestARowWhoseCellsCarryNothing(unittest.TestCase):
	"""Outlook's message list. Its rows are `outlook.UIAGridRow` — a `RowWithFakeNavigation`,
	whose contract says outright that "the cells must be exposed as children" — and the row
	itself carries `GridItemPattern`, but its children are plain text elements that carry
	nothing. Asking only the children said "not a table" about a table whose own row had just
	said it was one."""

	def messages(self, columnCount=3):
		view = FakeGrid(
			[
				["Sharon Rosenblatt", "SOW for Itemize", "4:12 PM"],
				["A teammate", "Re: the watchlist", "9:41 AM"],
			],
			headers=[],
			columnCount=columnCount,
		)
		for row in view.items:
			for cell in row.cellObjects:
				cell.columnNumber = None
		return view

	def test_theTableSayingHowWideItIsIsEnough(self):
		view = self.messages()
		table = flowObjectTable.tableFor(view.item(1))
		self.assertIsInstance(table, flowObjectTable.CellObjectTable)
		self.assertEqual(table.numCols, 3)

	def test_theCellsAreThenReadInOrder(self):
		view = self.messages()
		table = flowObjectTable.tableFor(view.item(1))
		cell = table._getTableCellAt(flowObjectTable.TABLE_ID, None, 1, 2)
		self.assertEqual(cell.text, "SOW for Itemize")

	def test_aListWhoseTableSaysNothingIsStillLeftAlone(self):
		"""The guard that stops this claiming ordinary lists: a run of items whose parent has no
		column count is a list, and NVDA reads a list well already."""
		view = self.messages(columnCount=None)
		self.assertIsNone(flowObjectTable.tableFor(view.item(1)))

	def test_aRowWithTooFewChildrenIsNotATableEither(self):
		view = self.messages()
		row = view.item(1)
		row.cellObjects = row.cellObjects[:1]
		self.assertIsNone(flowObjectTable.tableFor(row))


class TestAListArrangedInGroups(unittest.TestCase):
	"""File Explorer grouped by type, Outlook grouped by date. NVDA's `indexInGroup` is an
	index **within a group**, and the whole table is only one of the things a group can be:
	in a grouped list it restarts at one in every group. Read as a row number it names two
	rows the same, and read as a count it ends the table at the bottom of whichever group the
	reader happens to be standing in.
	"""

	ROWS = [["one", "first"], ["two", "second"], ["three", "third"], ["four", "fourth"]]

	def grouped(self, focused=3, index=1, among=2, level=0):
		""":return: a four row list whose rows are numbered two at a time."""
		view = FakeGrid(self.ROWS, headers=["Name", "Rank"])
		for number, row in enumerate(view.items, start=1):
			row.positionInfo = {
				"indexInGroup": (number - 1) % 2 + 1,
				"similarItemsInGroup": 2,
				"level": level,
			}
		item = view.item(focused)
		item.positionInfo = {"indexInGroup": index, "similarItemsInGroup": among, "level": level}
		return view, item

	def test_aTableHoldingMoreThanTheGroupIsNotOneGroup(self):
		view, item = self.grouped()
		table = flowObjectTable.CellObjectTable(view, row=item)
		self.assertEqual(view.childCount, 4)
		self.assertFalse(table.positionNumbersTheTable())

	def test_soTheReaderIsNotToldTheyAreInATable(self):
		"""Which leaves them NVDA's ordinary reading of the control. Better than a layout drawn
		confidently from numbers that mean something else."""
		_view, item = self.grouped()
		self.assertIsNotNone(flowObjectTable.tableFor(item))
		self.assertIsNone(flowTableSource.tableAt(item))

	def test_norIsTheTableTwoRowsLong(self):
		view, item = self.grouped()
		table = flowObjectTable.CellObjectTable(view, row=item)
		self.assertEqual(table.itemsAmong(item), 0)
		self.assertNotEqual(table.numRows, 2)

	def test_aRowAtALevelBelowTheFirstIsInABranch(self):
		"""NVDA reports a level for the controls that have a structure and for no others."""
		view, item = self.grouped(among=4, level=2)
		table = flowObjectTable.CellObjectTable(view, row=item)
		self.assertFalse(table.positionNumbersTheTable())

	def test_aRealRowNumberIsStillBelieved(self):
		"""A grouped table with true coordinates is still a table. `rowNumber` is the table
		property and says which row of the *table* this is, groups or no groups."""
		view, item = self.grouped()
		item.rowNumber = 3
		table = flowObjectTable.CellObjectTable(view, row=item)
		self.assertEqual(table.rowNumberOf(item), 3)

	def test_aVirtualisedListIsStillMeasuredByWhatTheRowSays(self):
		"""The one sided test, and the case this module was built for: File Explorer's file
		list admitted to fourteen children while the reader stood on item fifty-two of
		seventy-nine. Fewer is a list that has not been built; more is a list that has been
		grouped."""
		view = FakeGrid([[f"file{number}.py", "Available"] for number in range(1, 80)], realized=17)
		table = flowObjectTable.tableFor(view.item(52))
		self.assertTrue(table.positionNumbersTheTable())
		self.assertEqual(table.numRows, 79)

	def test_theReportSaysWhichItRead(self):
		"""Because the difference between the two is invisible in everything else the report
		holds: both answer a row number and both answer a count."""
		view, item = self.grouped()
		grouped = flowObjectTable.CellObjectTable(view, row=item)
		self.assertIn("numbers a group of it", " ".join(grouped.describe()))
		flat = flowObjectTable.tableFor(FakeGrid(self.ROWS, headers=["Name", "Rank"]).item(1))
		self.assertIn("numbers the table", " ".join(flat.describe()))

	def test_theReportSaysWhichSignSaidSo(self):
		"""The verdict reaches the reader as "not in a table", said about a control that is
		plainly one, so the numbers behind it are the whole of what a report has to carry."""
		view, item = self.grouped()
		grouped = flowObjectTable.CellObjectTable(view, row=item)
		self.assertIn("its group holds", grouped.whyItNumbersAGroup())
		self.assertIn("its group holds", " ".join(grouped.describe()))
		self.assertIn("at level 2", self.branch().whyItNumbersAGroup())

	def branch(self):
		""":return: a table whose row says it is one of a branch rather than of the table."""
		view, item = self.grouped(among=4, level=2)
		return flowObjectTable.CellObjectTable(view, row=item)


class TestAListThatCountsMoreThanTheGroup(unittest.TestCase):
	"""A table admitting to more rows than the row says its group holds is refused, and the
	strictness has a history worth keeping.

	It was relaxed once — to twice the group, on the argument that an off-by-one is a control
	miscounting rather than a shape — because File Explorer's file list counts a pane, a
	scrollbar and its column header among its children, so a folder of two files admitted to
	five and the whole feature vanished in small folders. That fix belonged where the miscount
	was, and `childrenAdmittedTo` takes the decorations off now.

	Relaxing it here cost the test its meaning instead: a review found a list of ten arranged
	in groups of six and four read as a flat table of six, so four of the reader's own rows sat
	behind an end that was not there. A count that disagrees is ambiguous, and an ambiguous
	table is refused rather than half drawn.
	"""

	ROWS = [[f"file{number}.py", "Available"] for number in range(1, 11)]

	def listCounting(self, says):
		""":return: a ten row file list whose own `rowCount` says something else."""
		view = FakeGrid(self.ROWS, headers=["Name", "Status"])
		view.rowCountSays = says
		return view

	def test_oneRowMoreIsAmbiguousAndRefused(self):
		view = self.listCounting(len(self.ROWS) + 1)
		table = flowObjectTable.tableFor(view.item(3))
		self.assertFalse(table.positionNumbersTheTable())
		self.assertIsNone(flowTableSource.tableAt(view.item(3)))

	def test_groupsOfSixAndFourAreNotAFlatTableOfSix(self):
		"""The review's own case. Six rows drawn out of ten, with the other four behind an end
		that is not there, is the outcome the refusal exists to avoid."""
		view = FakeGrid(self.ROWS)
		for number, item in enumerate(view.items, start=1):
			group = 6 if number <= 6 else 4
			item.positionInfo = {
				"indexInGroup": number if number <= 6 else number - 6,
				"similarItemsInGroup": group,
				"level": 0,
			}
		table = flowObjectTable.tableFor(view.item(3))
		self.assertFalse(table.positionNumbersTheTable())
		self.assertIsNone(flowTableSource.tableAt(view.item(3)))

	def test_aCountThatAgreesIsStillATable(self):
		view = self.listCounting(len(self.ROWS))
		table = flowObjectTable.tableFor(view.item(3))
		self.assertTrue(table.positionNumbersTheTable())
		self.assertEqual(table.numRows, len(self.ROWS))


class TestGoingToACellNobodyCanFocus(unittest.TestCase):
	"""Outlook's message list. `RowWithFakeNavigation` keeps the focus on the row and moves
	the navigator object to the cell, because the row is the focusable thing and its children
	are text elements that are not. Focusing one of those does nothing at all, so a routing
	key over a subject line would have moved nothing and said nothing.
	"""

	def setUp(self):
		navigatedTo.clear()

	def messages(self, focusable=False):
		view = FakeGrid([["A teammate", "Re: the watchlist"], ["Someone else", "Lunch"]])
		for row in view.items:
			row.isFocusable = True
			for cell in row.cellObjects:
				cell.isFocusable = focusable
		return view

	def test_theRowTakesTheFocus(self):
		view = self.messages()
		table = flowObjectTable.tableFor(view.item(1))
		table._getTableCellAt(flowObjectTable.TABLE_ID, None, 2, 2).updateCaret()
		self.assertTrue(view.item(2).focused)

	def test_andTheNavigatorObjectGoesTheRestOfTheWay(self):
		view = self.messages()
		table = flowObjectTable.tableFor(view.item(1))
		table._getTableCellAt(flowObjectTable.TABLE_ID, None, 2, 2).updateCaret()
		self.assertIs(navigatedTo[-1], view.item(2).cellObjects[1])

	def test_aCellThatCanBeFocusedStillIsFocused(self):
		"""File Explorer's Details view, where the focus lands on the property itself. Going to
		the row there would move the reader off the column they had their finger on."""
		view = self.messages(focusable=True)
		table = flowObjectTable.tableFor(view.item(1))
		table._getTableCellAt(flowObjectTable.TABLE_ID, None, 2, 2).updateCaret()
		self.assertTrue(view.item(2).cellObjects[1].focused)
		self.assertFalse(view.item(2).focused)
		self.assertEqual(navigatedTo, [])


class TestTheColumnSurvivingTheFocusEvent(unittest.TestCase):
	"""`setFocus` is a request, and the answer comes back as an event.

	NVDA moves the navigator object to whatever has just taken the focus, whenever the review
	cursor follows the focus — and that happens when the event arrives, which is after the
	routing key has finished. So the column asked for was set and then quietly undone, and the
	reader who routed onto a subject line was left on the row.

	The tests above cannot see it, because the focus in them is instantaneous. Here the event
	is separate, which is the only way this is visible at all.
	"""

	def setUp(self):
		navigatedTo.clear()
		flowObjectTable._pendingColumn = None
		self.addCleanup(setattr, flowObjectTable, "_pendingColumn", None)

	def messages(self, focusable=False):
		view = FakeGrid([["A teammate", "Re: the watchlist"], ["Someone else", "Lunch"]])
		for row in view.items:
			row.isFocusable = True
			for cell in row.cellObjects:
				cell.isFocusable = focusable
		return view

	def routeInto(self, view, row, column):
		table = flowObjectTable.tableFor(view.item(1))
		table._getTableCellAt(flowObjectTable.TABLE_ID, None, row, column).updateCaret()

	def focusArrives(self, obj) -> bool:
		"""NVDA processes the focus event, review following the focus as it does by default.

		:param obj: what took the focus.
		:return: whether the add-on had a column waiting for it.
		"""
		import api

		api.setNavigatorObject(obj)
		return flowObjectTable.columnWantedAfterFocus(obj)

	def test_theEventUndoesTheColumnOnItsOwn(self):
		"""The defect itself, so that the fix below is measured against something."""
		view = self.messages()
		self.routeInto(view, 2, 2)
		import api

		api.setNavigatorObject(view.item(2))
		self.assertIs(navigatedTo[-1], view.item(2))

	def test_soTheColumnIsAskedForAgainAfterwards(self):
		view = self.messages()
		self.routeInto(view, 2, 2)
		self.assertTrue(self.focusArrives(view.item(2)))
		self.assertIs(navigatedTo[-1], view.item(2).cellObjects[1])

	def test_theRequestIsUsedOnce(self):
		"""A later focus on the same row is the reader arrowing, not the routing key."""
		view = self.messages()
		self.routeInto(view, 2, 2)
		self.focusArrives(view.item(2))
		self.assertFalse(self.focusArrives(view.item(2)))
		self.assertIs(navigatedTo[-1], view.item(2))

	def test_someOtherFocusDropsIt(self):
		"""A request the focus never answered must not fire against whatever comes next."""
		view = self.messages()
		self.routeInto(view, 2, 2)
		self.assertFalse(self.focusArrives(view.item(1)))
		self.assertIs(navigatedTo[-1], view.item(1))
		self.assertFalse(self.focusArrives(view.item(2)))

	def test_aCellThatCanTakeTheFocusAsksForNothing(self):
		"""File Explorer's Details view: the focus lands on the column itself."""
		view = self.messages(focusable=True)
		self.routeInto(view, 2, 2)
		self.assertIsNone(flowObjectTable._pendingColumn)

	def test_aFocusChangeWithNothingPendingCostsNothing(self):
		view = self.messages()
		self.assertFalse(flowObjectTable.columnWantedAfterFocus(view.item(1)))


class TestReadingAListViewThroughItsOwnCells(unittest.TestCase):
	"""`RowWithoutCellObjects.getChild` makes a cell object per column, and that object answers
	`name`, `columnHeaderText` and `location` by asking the row the underscored questions
	itself. Asking it is the public way to a column of a classic list view; calling those
	methods from here is depending on API the NVDA developer guide says is private.
	"""

	def test_theCellIsWhereTheContentIsRead(self):
		view, item = listView()
		table = flowObjectTable.tableFor(item)
		cell = table.cellObject(item, 2)
		self.assertEqual(cell.columnNumber, 2)
		self.assertEqual(table.cellOf(item, 2, 1).text, cell.name)

	def test_andWhereTheHeaderIs(self):
		view, item = listView()
		table = flowObjectTable.tableFor(item)
		self.assertEqual(table.cellOf(item, 1, 1).header, "From")

	def test_aRowThatMakesNoCellIsStillRead(self):
		"""An application module may implement the contract on a class of its own rather than
		by inheriting NVDA's behaviour, which is what `rowsTable` is written to accept."""
		view, item = listView()
		for row in view.items:
			row.getChild = lambda index: None
		table = flowObjectTable.tableFor(item)
		self.assertEqual(table.cellOf(item, 1, 1).text, "Travis Roth")
		self.assertEqual(table.cellOf(item, 1, 1).header, "From")


class TestSayingWhyThereIsNoTable(unittest.TestCase):
	"""One sentence — "not in a table" — is what the reader hears for four different answers:
	refused as a row, refused as a cell, recognised and unable to say which row of what this
	is, or recognised and dropped further up. Each is a different bug, and until this none of
	them was written down anywhere.
	"""

	def test_aTableSaysWhereTheCursorIsInIt(self):
		view = FakeGrid([["a", "one"], ["b", "two"]], headers=["Name", "Rank"])
		said = " ".join(flowTableSource.explain(view.item(2)))
		self.assertIn("the cursor is in row 2", said)
		self.assertIn("numbers the table", said)

	def test_aRefusedRowSaysWhichQuestionRefusedIt(self):
		"""The numbers as well as the verdict, because the verdict is the thing that is
		already known: the reader pressed the command and was told no."""
		view = FakeGrid([[f"file{number}.py", "Available"] for number in range(1, 11)])
		view.rowCountSays = 40
		said = " ".join(flowTableSource.explain(view.item(3)))
		self.assertIn("the cursor is not in a cell of it", said)
		self.assertIn("numbers a group of it", said)
		self.assertIn("its group holds", said)

	def test_somethingThatIsNoTableAtAllSaysSo(self):
		"""And says what was asked of it, since "not a table" about an ordinary list item is
		the right answer and the report has to show that it is."""
		plain = FakeNavigatorObject(name="a button", role="BUTTON")
		said = " ".join(flowTableSource.explain(plain))
		self.assertIn("nothing here navigates a table", said)
		self.assertIn("a row that answers for its own cells: no", said)
		self.assertIn("nothing this module can present as a table", said)

	def test_nothingAtAllIsReportedRatherThanRaised(self):
		self.assertIn("none", " ".join(flowTableSource.explain(None)))


class TestAMessageListThatCallsItselfATable(unittest.TestCase):
	"""Outlook's inbox, in the shape the reader's log described.

	A pane holding a table called "Table View", holding the message rows, each holding its
	fields. Every row carries `GridItemPattern` — that is what made recognising the file list
	possible at all — and every row of a grid is in column one. So by the cell properties
	alone a row read as a cell of the message list, the message list read as a row of cells of
	the pane, and the answer was a table whose single row was the whole inbox and whose cells
	were its messages. Nothing in that shape can say which row the reader is on, so the
	command told them they were not in a table while they stood in the one they had asked
	about.

	Two things settle it, and either alone would have: the list says it is a table, and a
	table is not a row of anything; and cells of one row are in different columns of it, while
	those "cells" all claimed column one.
	"""

	def inbox(self, columnCount=3, rowColumn=1):
		""":return: the message list and the row the focus is on."""
		view = FakeGrid(MESSAGES, headers=HEADERS, name="Table View", columnCount=columnCount)
		view.role = "TABLE"
		view.parent = FakeNavigatorObject(name="Inbox - Outlook", role="PANE")
		for row in view.items:
			# What `outlook.UIAGridRow` carries: the row is the grid item, and its children
			# are text elements that carry nothing at all.
			row.columnNumber = rowColumn
			for cell in row.cellObjects:
				cell.columnNumber = None
		return view, view.item(2)

	def test_theTableIsTheMessageListAndTheRowIsTheMessage(self):
		view, row = self.inbox()
		table = flowObjectTable.tableFor(row)
		self.assertIs(table.table, view)
		self.assertIs(table.focused, row)

	def test_andTheReaderIsInIt(self):
		"""Which is the whole report: the command answered "not in a table" here."""
		view, row = self.inbox()
		handle = flowTableSource.tableAt(row)
		self.assertIsNotNone(handle)
		self.assertEqual((handle.numRows, handle.row), (len(MESSAGES), 2))

	def test_theCellsAreTheMessagesFields(self):
		view, row = self.inbox()
		table = flowObjectTable.tableFor(row)
		self.assertEqual(table.cellOf(row, 2, 2).text, MESSAGES[1][1])

	def test_aThingThatSaysItHoldsRowsIsNeverTheRow(self):
		view, _row = self.inbox()
		self.assertTrue(flowObjectTable.isAContainerOfRows(view))
		self.assertEqual(flowObjectTable.cellsOfARow(view), [])
		self.assertIsNone(flowTableSource.tableAt(view))

	def test_norIsOneWhoseChildrenAllClaimTheSameColumn(self):
		"""The second answer, and it holds where the role is one this has never heard of."""
		view, _row = self.inbox()
		view.role = "somethingNobodyHasHeardOf"
		self.assertEqual(flowObjectTable.cellsOfARow(view), [])

	def test_aGridThatWillNotSayHowWideItIsIsStillRead(self):
		"""The third way of being sure. The row's children carry nothing and the list answers
		no `columnCount`, so all that is left is the row's own word — which is that it holds a
		place in a grid, said by the pattern this module recognises tables through, and the
		thing above it saying it holds rows."""
		view, row = self.inbox(columnCount=None)
		table = flowObjectTable.tableFor(row)
		self.assertIs(table.table, view)
		self.assertEqual(table.cellOf(row, 1, 2).text, MESSAGES[1][0])

	def test_butACellOfAFileListIsNotARowByThatSign(self):
		"""It claims a grid place too. What it is in is one file rather than the list, and
		that is what keeps the two apart."""
		explorer = FakeGrid(MESSAGES, headers=HEADERS)
		cell = explorer.item(1).cellObjects[0]
		cell.firstChild = None
		self.assertFalse(flowObjectTable._holdsAPlaceInAGrid(cell))

	def test_theReportSaysWhichReadingWasTaken(self):
		view, row = self.inbox()
		said = " ".join(flowTableSource.explain(row))
		self.assertIn("a container of rows: yes", said)
		self.assertIn("Table View", said)


class TestNotReadingAWholeListToFindARow(unittest.TestCase):
	"""The question "is this object a row" is asked of whatever the reader is standing on, and
	the answer used to be read out of every child it had. On a message list that is every
	message NVDA has built, one call into the application each, on the path between a key
	press and what the display says — and NVDA's watchdog calls half a second a freeze.

	A row of a table has columns, not hundreds of them.
	"""

	def test_aListTooLongToBeARowIsNotOne(self):
		many = FakeGrid([[f"m{number}", "seen"] for number in range(flowObjectTable.MAX_ROW_CELLS + 5)])
		many.role = "somethingNobodyHasHeardOf"
		self.assertEqual(flowObjectTable.cellsOfARow(many), [])

	def test_andIsNotEvenBuiltToFindOut(self):
		"""`childCount` is answered without building a child, so the walk never happens."""
		many = FakeGrid([[f"m{number}", "seen"] for number in range(flowObjectTable.MAX_ROW_CELLS + 5)])
		many.role = "somethingNobodyHasHeardOf"
		flowObjectTable.cellsOfARow(many)
		self.assertEqual(many.walks, 0)

	def test_aRowOfOrdinaryWidthIsStillARow(self):
		view = FakeGrid(MESSAGES, headers=HEADERS, columnCount=3)
		self.assertEqual(len(flowObjectTable.cellsOfARow(view.item(1))), 3)


class TestAskingMoreThanOneCellForAHeader(unittest.TestCase):
	"""A declared header belongs to the column, so any cell of it answers — but only if the
	cell that was asked is one that answers at all. The first row read is not always a good
	witness: a row of a virtualised list may be half built, and a row of a document may hold a
	merged cell where its neighbours hold real ones. Asked once, such a cell was the whole of
	what the column had said, and the column then had no name.

	The names matter because they are what the paging command reads out: a column with no name
	is announced by its number, and the reader hears "six, seven" instead of "From, Subject".
	"""

	FILES = [
		[("Name", "report.docx"), ("Date modified", "8/28/2026 9:41 AM")],
		[("Name", "notes.md"), ("Date modified", "8/27/2026 4:02 PM")],
		[("Name", "budget.xlsx"), ("Date modified", "8/26/2026 8:15 AM")],
	]

	def explorer(self, silentRows=()):
		"""File Explorer's Details view, as its developer info describes it: each cell's name
		is the column's heading and its value is the content.

		:param silentRows: rows whose cells answer nothing about their names, which is what a
			row that has not finished being built looks like from here.
		"""
		view = FakeGrid(self.FILES)
		for number in silentRows:
			for cell in view.item(number).cellObjects:
				cell.name = ""
		return view

	def labels(self, view):
		handle = flowTableSource.tableAt(view.item(1))
		return [found.label for found in flowTableSource.measure(handle)]

	def test_theHeadingIsTheCellsOwnName(self):
		"""Which is what NVDA speaks when the reader arrows across the columns: it speaks the
		cell, and the cell is named after its column."""
		self.assertEqual(self.labels(self.explorer()), ["Name", "Date modified"])

	def test_aRowThatAnswersNothingDoesNotSpeakForTheColumn(self):
		self.assertEqual(self.labels(self.explorer(silentRows=(1,))), ["Name", "Date modified"])

	def test_norDoTwoOfThem(self):
		self.assertEqual(self.labels(self.explorer(silentRows=(1, 2))), ["Name", "Date modified"])

	def test_theHeaderRowIsPinnedFromTheSameReading(self):
		"""A review's case, and the other half of asking once: the measurement asked three
		rows and found "Name" and "Date modified", while the source asked the caret's row
		alone, got nothing, and pinned no header row at all. The columns were named and the
		names were nowhere above them."""
		view = self.explorer(silentRows=(1,))
		handle = flowTableSource.tableAt(view.item(1))
		measured = flowTableSource.measure(handle)
		source = flowTableSource.TableFlowSource(
			handle,
			(1, 2),
			declared={item.index: item.label for item in measured if item.declared},
			pinHeaders=True,
		)
		pinned = source.headerBlock()
		self.assertIsNotNone(pinned)
		self.assertIn("Date modified", pinned.region.rawText)

	def test_aTableThatDeclaresNothingPinsNothing(self):
		"""And the source is not sent looking for what the measurement already found absent."""
		view = FakeGrid([["a", "b"], ["c", "d"]])
		handle = flowTableSource.tableAt(view.item(1))
		source = flowTableSource.TableFlowSource(handle, (1, 2), declared={}, pinHeaders=True)
		self.assertIsNone(source.headerBlock())

	def test_aTableThatDeclaresNothingIsNotAskedForever(self):
		"""The bound is what keeps a table that declares nothing from paying a read of the
		fields on every cell of the sample."""
		view = FakeGrid([["a", "b"], ["c", "d"], ["e", "f"], ["g", "h"]])
		asked = []
		real = flowTableSource.declaredHeader

		def counted(info, axis="column"):
			asked.append(info)
			return real(info, axis)

		flowTableSource.declaredHeader = counted
		try:
			self.assertEqual(self.labels(view), ["", ""])
		finally:
			flowTableSource.declaredHeader = real
		self.assertEqual(len(asked), flowTableSource.HEADER_TRIES * 2)
