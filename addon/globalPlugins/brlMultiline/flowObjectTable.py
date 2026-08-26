# BrlMultiline: reading a table that is made of objects.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A list view read as a table, through NVDA's own answers about it.

The message list in Outlook, the file list in Explorer, and most report-view lists in
Windows dialogs. They are tables — a row per item, a column per header — and NVDA has known
how to read them a column at a time for years. What it has never had is anywhere to *put*
them: on one row of braille a list item is a run of fields separated by semicolons, which is
why the reader turned the headers off in Outlook. Eight rows of thirty-two cells is somewhere
to put them.

**Nothing here reads an accessibility API.** NVDA has two shapes of table object and this
module reads both through NVDA's own properties.

`NVDAObjects.behaviors.RowWithoutCellObjects` is the first: a row whose cells are *not*
objects, answering `_getColumnContent`, `_getColumnHeader` and `_getColumnLocation`.
`sysListView32` implements all three through NVDA's in-process helper. That is the classic
Win32 list view.

`NVDAObjects.behaviors.RowWithFakeNavigation` is the second, and on Windows 11 it is the one
that turns up: a row whose cells *are* objects, each answering `columnNumber` and
`columnHeaderText`. Outlook's message list is `outlook.UIAGridRow`, which is exactly that, and
File Explorer's file list is a UIA grid whose cells carry `GridItemPattern` and
`TableItemPattern`. The first version of this module knew only the first shape and told the
reader "not in a table" in both of the places they tried it.

Which shape a table is comes from asking, not from guessing at a platform.

**The seam is `documentBase.DocumentWithTableNavigation`.** `flowTableSource` walks that
interface — which cell is the cursor in, how big is the table, give me the cell at these
coordinates — and it is already the whole of what reading a table needs. A list view is not
one of those, so this module presents one as one. Everything above the seam is then the same
code for a web page and for the message list, which is what `tableDocumentFor` said it was
betting on before there was anything to bet.

**A cell here is text, and its position is its row.** A document cell can be pointed at; a
list view cell cannot, because the only thing the reader can go to is the item. So routing
into a cell focuses the row, which is what clicking one does and what the reader means by
"take me to this one".
"""

from typing import Any, Optional

from logHandler import log

TABLE_ID = "objectTable"
"""What this stands in for NVDA's table identifier with.

A table this module presents holds exactly one table, so there is nothing for an identifier
to tell apart. What tells one list from another is the object, which is the other half of
`flowTableSource.TableHandle.key` and the half that was already doing the work.
"""

MIN_COLUMNS = 2
"""How many columns a list needs before it is worth laying out as a table.

One column is a list, and NVDA reads a list well already. The layout has something to offer
from the second column, which is the first point at which a reader has to remember what an
unlabelled value was.
"""


def rowsTable(obj) -> Optional[Any]:
	""":return: the table an object is a row of, or None if its cells are not answered by it.

	The contract is `RowWithoutCellObjects`, asked for rather than tested with `isinstance`:
	an application module may implement it on a class of its own without inheriting, and what
	matters is that the answers are there. `columnCount` is the table's own — NVDA's list view
	reads it from the header control — and it is what the row divides itself into.

	:param obj: the object the reader is on.
	"""
	if obj is None or not callable(getattr(obj, "_getColumnContent", None)):
		return None
	try:
		table = obj.parent
		if table is None:
			return None
		columns = int(getattr(table, "columnCount", 0) or 0)
	except Exception:
		log.debugWarning("Could not ask whether this object is a row of a table", exc_info=True)
		return None
	return table if columns >= MIN_COLUMNS else None


def cellsRow(obj) -> Optional[Any]:
	""":return: the row an object is a cell of, or None if it is not one.

	A cell says which column it is in, and that is the whole test: `columnNumber` is filled in
	from `GridItemPattern` for UIA and from the table cell interface for IAccessible2, and an
	object that answers it is a cell of something. What it is a cell *of* is its parent.

	The reader's own report is why this exists. In File Explorer the focus lands on a
	*property* of a file rather than on the file — `appModules.explorer.UIProperty`, carrying
	`GridItemPattern` and `TableItemPattern` — so a test that only knew how to recognise rows
	said "not in a table" while standing in one.

	:param obj: the object the reader is on.
	"""
	if obj is None or columnNumberOf(obj) is None:
		return None
	try:
		return obj.parent
	except Exception:
		log.debugWarning("Could not ask what an object is a cell of", exc_info=True)
		return None


def cellsOfARow(obj) -> list:
	""":return: the cell objects of a row, or an empty list if this object is not such a row.

	A row whose cells are objects is one whose children say which column they are in.
	Counting how many say so rather than trusting the first is deliberate: a row of a tree
	view has children too, and they are items rather than cells.

	:param obj: the object the reader is on.
	"""
	if obj is None:
		return []
	try:
		children = list(obj.children or [])
	except Exception:
		log.debugWarning("Could not read an object's children", exc_info=True)
		return []
	numbered = [child for child in children if columnNumberOf(child) is not None]
	return children if len(numbered) >= MIN_COLUMNS else []


class ObjectCellInfo:
	"""One cell of an object table, in the shape the rest of the add-on already reads.

	A document cell arrives as a `TextInfo`, and three things are asked of it: what it says,
	how to put the caret in it, and what its column's header is. This answers those three and
	claims to be nothing else.
	"""

	def __init__(self, text: str, target=None, header: str = "") -> None:
		"""
		:param text: the cell's content.
		:param target: the object to go to when the reader routes into this cell. The cell
			itself where cells are objects, and the row where they are not — see
			L{updateCaret}.
		:param header: what the table says this column's header is, or "" if it says nothing.
		"""
		self.text = text
		self.target = target
		self.header = header
		self.isCollapsed = True

	def copy(self) -> "ObjectCellInfo":
		return ObjectCellInfo(self.text, self.target, self.header)

	def collapse(self, end: bool = False) -> None:
		"""A cell is already a place rather than a range. Kept for the shape of a position."""

	def updateCaret(self) -> None:
		"""Go to this cell, by focusing whatever of it can be focused.

		Where the cells are objects that is the cell, which is what arrowing across a grid
		does. Where they are not there is nothing but the row: a column of a classic list view
		is a rectangle on the screen and not a place the keyboard can be put, and focusing the
		row is what clicking one does. Either way it is what the reader means by routing into
		a value.
		"""
		if self.target is None:
			return
		try:
			self.target.setFocus()
		except Exception:
			log.debugWarning("Could not go to a table cell", exc_info=True)

	def getTextWithFields(self, formatConfig=None) -> list:
		""":return: this cell's control field, carrying the header its column declares.

		The same attribute a browse mode document puts there, so that
		`flowTableSource.declaredHeader` needs to know nothing about where a table came from.
		A list view's headers are declared in exactly the sense that matters: NVDA asks the
		header control for them rather than guessing at a first row.
		"""
		from textInfos import FieldCommand

		field = {"table-id": TABLE_ID}
		if self.header:
			field["table-columnheadertext"] = self.header
		return [FieldCommand("controlStart", field), self.text]

	def __repr__(self) -> str:
		return f"<ObjectCellInfo {self.text!r}>"


class ObjectCell:
	"""What `_getTableCellCoords` answers with, in the three fields anything here reads."""

	def __init__(self, tableID, row: int, col: int) -> None:
		self.tableID = tableID
		self.row = row
		self.col = col
		self.rowSpan = 1
		self.colSpan = 1


class ObjectTable:
	"""NVDA's table navigation interface, over a run of row objects.

	Presented rather than implemented in NVDA's own class hierarchy, because what is wanted
	is the three methods and not the scripts, the text container or the browse mode
	behaviour that `DocumentWithTableNavigation` also brings. Those would be a claim to be a
	document, and a list view is not one.

	Everything about *finding* the rows is here; everything about reading a cell out of one is
	in a subclass, because that is the only thing the two shapes of table object disagree
	about. See the module docstring.
	"""

	def __init__(self, table, row=None, column: int = 1) -> None:
		"""
		:param table: the object holding the rows. It answers `rowCount` and `columnCount`.
		:param row: the row object the reader is on, if it is known.
		:param column: which column of that row they are on. One unless the focus was on a
			cell and said which — see `tableFor`.
		"""
		self.obj = table
		"""The real object, which is what NVDA compares a region against. See `TableRow`."""

		self.table = table
		self.focused = row
		self.column = max(1, int(column or 1))
		"""Which column the reader is on, for the cursor. See `_getTableCellCoords`."""

		self._rows: dict = {}
		"""Row objects by row number, so that a bandful of cells costs a bandful of rows."""

	# The three questions `flowTableSource` asks of a document.

	@property
	def selection(self):
		""":return: where the reader is, which for an object table is which row they are on."""
		return ObjectCellInfo("", target=self.focused)

	def _getTableCellCoords(self, info) -> ObjectCell:
		""":return: which cell of the table the reader is in.

		:raises LookupError: if they are not in one, which is how `tableAt` hears "no".
		"""
		row = self.rowNumberOf(self.focused)
		if row is None:
			raise LookupError("Not on a row of this table")
		return ObjectCell(TABLE_ID, row, self.column)

	def _getTableDimensions(self, info) -> tuple:
		""":return: how many rows and columns the table has, as the table itself says.

		`rowCount` rather than `childCount` where the object offers it: a list view answers
		the first with one window message and the second by building every item.
		"""
		return (self.numRows, self.numCols)

	def _getTableCellAt(self, tableID, startPos, row: int, column: int) -> ObjectCellInfo:
		""":return: the cell at one coordinate.

		:raises LookupError: for a coordinate the table has not got, and for a column it is
			not showing. Both are ordinary rather than exceptional, and both are how the rest
			of the add-on already hears "there is nothing here" — see `flowTableSource`.
		"""
		if tableID != TABLE_ID:
			raise LookupError("Wrong table")
		item = self.rowObject(row)
		if item is None:
			raise LookupError(f"No row {row}")
		if not 1 <= column <= self.numCols:
			raise LookupError(f"No column {column}")
		return self.cellOf(item, column, row)

	def cellOf(self, item, column: int, row: int) -> ObjectCellInfo:
		"""Read one cell out of one row.

		The one thing the two shapes of table object disagree about, and the only thing a
		subclass has to answer.

		:param item: the row object.
		:param column: which column of it, one based.
		:param row: which row it is, for the log.
		:raises LookupError: for a cell the row has not got or is not showing.
		"""
		raise NotImplementedError

	# What the answers are made of.

	@property
	def numRows(self) -> int:
		""":return: how many rows the table has."""
		for name in ("rowCount", "childCount"):
			try:
				count = getattr(self.table, name, None)
				if count:
					return int(count)
			except Exception:
				log.debugWarning(f"Could not read a table's {name}", exc_info=True)
		return 0

	@property
	def numCols(self) -> int:
		""":return: how many columns the table has.

		The table's own answer, and where it has none the row's: a grid that does not carry a
		column count still has a row whose cells can be counted, and a row of a table is the
		width of the table by definition.
		"""
		try:
			count = int(getattr(self.table, "columnCount", 0) or 0)
		except Exception:
			log.debugWarning("Could not read a table's columnCount", exc_info=True)
			count = 0
		return count or self.columnsOfARow()

	def columnsOfARow(self) -> int:
		""":return: how wide one row of this table is, for a table that cannot say. Zero if it
		cannot be worked out either."""
		return 0

	def rowNumberOf(self, item) -> Optional[int]:
		""":return: which row of the table an object is, one based, or None.

		NVDA's own answer where the object has one. `rowNumber` is the table property and is
		what an implementation with real coordinates fills in; `positionInSet` is what a list
		item has when the platform numbers items rather than rows. Neither is worked out by
		counting, which on a list of ten thousand messages is the difference between a
		keypress and a pause.
		"""
		if item is None:
			return None
		try:
			found = getattr(item, "rowNumber", None)
			if found:
				return int(found)
		except Exception:
			log.debugWarning("Could not read an object's rowNumber", exc_info=True)
		try:
			where = getattr(item, "positionInfo", None) or {}
			index = where.get("indexInGroup")
		except Exception:
			log.debugWarning("Could not read an object's position", exc_info=True)
			return None
		return int(index) if index else None

	def rowObject(self, row: int):
		""":return: the object for one row of the table, or None if it has not got that row.

		Remembered, because a bandful of rows is asked for a cell at a time and each column
		of a row would otherwise fetch the row again. Dropped whenever the table is read
		afresh, which is what `forget` is for.
		"""
		if row in self._rows:
			return self._rows[row]
		found = None
		if self.focused is not None and self.rowNumberOf(self.focused) == row:
			# The one row already in hand. Fetching it again would cost a search to arrive
			# back at the object the focus event just handed over.
			found = self.focused
		elif 1 <= row <= self.numRows:
			try:
				found = self.table.getChild(row - 1)
			except Exception:
				log.debugWarning(f"Could not reach row {row} of a table", exc_info=True)
				found = None
		self._rows[row] = found
		return found

	def sameAs(self, other) -> bool:
		""":return: whether another stand-in is presenting the same control as this one.

		Asked by `flowTableSource.sameTable`, and it is why this exists: a stand-in is built
		afresh every time the reader is asked where they are, and NVDA builds a fresh
		`NVDAObject` for the same control whenever it is asked for one. Identity therefore
		says "somewhere else" on every arrow key, and the reader's layout would be taken away
		each time they moved to the next message. NVDA's own answer to "is this the same
		control" is equality, so that is the answer used.
		"""
		if not isinstance(other, ObjectTable):
			return False
		try:
			return bool(self.table == other.table)
		except Exception:
			log.debugWarning("Could not compare two object tables", exc_info=True)
			return False

	def moveTo(self, row) -> None:
		"""Say which row the reader is on now.

		:param row: the row object they have moved to.
		"""
		self.focused = row

	def forget(self) -> None:
		"""Drop the remembered row objects, so the next read builds them again."""
		self._rows.clear()

	def __repr__(self) -> str:
		return f"<ObjectTable {self.numRows}x{self.numCols} over {self.table!r}>"


class RowCellTable(ObjectTable):
	"""A table whose cells are not objects: the classic Win32 list view.

	`NVDAObjects.behaviors.RowWithoutCellObjects` is the contract and `sysListView32`
	implements it through NVDA's in-process helper, so a cell, its header and its rectangle
	are three questions asked of the row.
	"""

	def cellOf(self, item, column: int, row: int) -> ObjectCellInfo:
		""":return: one cell, read out of its row. See `ObjectTable.cellOf`."""
		if not self._isShowing(item, column):
			raise LookupError(f"Column {column} is not showing")
		try:
			text = item._getColumnContent(column)
		except Exception:
			log.debugWarning(f"Could not read column {column} of row {row}", exc_info=True)
			raise LookupError(f"Column {column} of row {row} could not be read")
		# The row rather than the cell: a column of a classic list view is a rectangle on the
		# screen, and the item is the only thing the keyboard can be put on.
		return ObjectCellInfo(text or "", target=item, header=self.headerOf(item, column))

	def columnsOfARow(self) -> int:
		""":return: how wide one row is, which such a row already answers as its child count."""
		try:
			return int(getattr(self.focused, "childCount", 0) or 0)
		except Exception:
			log.debugWarning("Could not count a row's columns", exc_info=True)
			return 0

	def headerOf(self, item, column: int) -> str:
		""":return: what the table says a column's header is, or "" if it says nothing.

		Asked of the row, because that is where NVDA put the question:
		`RowWithoutCellObjects._getColumnHeader` is answered by `sysListView32` out of the
		list's own header control. It is a property of the column, so any row answers.
		"""
		try:
			return (item._getColumnHeader(column) or "").strip()
		except Exception:
			log.debugWarning(f"Could not read the header of column {column}", exc_info=True)
			return ""

	def _isShowing(self, item, column: int) -> bool:
		""":return: whether a column is one the table is actually showing.

		A list view keeps columns the reader has hidden, and reports them in its column count;
		what says they are hidden is that their rectangle has no width. `sysListView32` skips
		them on exactly that test when it builds an item's name, and this is the same test in
		the same place — which is better than the browse mode source's, where a column with no
		width has to be *inferred* from every cell in a sample being empty.

		A row that cannot answer is believed rather than doubted: a column is showing unless
		something says otherwise.
		"""
		try:
			where = item._getColumnLocation(column)
		except Exception:
			log.debugWarning(f"Could not locate column {column}", exc_info=True)
			return True
		if where is None:
			return True
		width = getattr(where, "width", None)
		if width is None:
			try:
				width = where.right - where.left
			except Exception:
				return True
		return width > 0


class CellObjectTable(ObjectTable):
	"""A table whose cells are objects: what Windows 11 turns out to have.

	`NVDAObjects.behaviors.RowWithFakeNavigation` is the contract — "the cells must be exposed
	as children and they must support the table cell properties" — and both places the reader
	tried are that. Outlook's message list rows are `outlook.UIAGridRow`, whose children are
	the fields; File Explorer's file list is a UIA grid whose cells carry `GridItemPattern` and
	`TableItemPattern`, which is where `columnNumber` and `columnHeaderText` come from.
	"""

	def cellOf(self, item, column: int, row: int) -> ObjectCellInfo:
		""":return: one cell, which here is an object. See `ObjectTable.cellOf`."""
		cell = self.cellObject(item, column)
		if cell is None:
			raise LookupError(f"Row {row} has no column {column}")
		header = headerTextOf(cell)
		return ObjectCellInfo(cellText(cell, header), target=cell, header=header)

	def columnsOfARow(self) -> int:
		""":return: how wide one row is, counted from the cells of the row in hand."""
		return len(self.cellsOf(self.focused))

	def cellObject(self, item, column: int):
		""":return: the object for one column of a row, or None if the row has not got it.

		By the cell's own `columnNumber` where it has one, which is the only answer that
		survives a row with a merged or missing cell in it; by position otherwise, which is
		what a row of plain children amounts to.
		"""
		cells = self.cellsOf(item)
		for cell in cells:
			if columnNumberOf(cell) == column:
				return cell
		if any(columnNumberOf(cell) is not None for cell in cells):
			# The row numbers its cells and none of them is this one, so the cell is genuinely
			# missing rather than merely unnumbered. Falling through to position here would
			# hand back a neighbour under this column's name.
			return None
		return cells[column - 1] if 1 <= column <= len(cells) else None

	def cellsOf(self, item) -> list:
		""":return: the cell objects of one row, remembered.

		One walk of the row's children per row rather than per cell: a page of six columns
		would otherwise walk the row six times, and NVDA builds an object for every child each
		time it is asked.
		"""
		if item is None:
			return []
		key = id(item)
		found = self._cells.get(key)
		if found is None:
			try:
				found = list(item.children or [])
			except Exception:
				log.debugWarning("Could not read a table row's cells", exc_info=True)
				found = []
			self._cells[key] = found
		return found

	def forget(self) -> None:
		super().forget()
		self._cells.clear()

	@property
	def _cells(self) -> dict:
		if not hasattr(self, "_cellsByRow"):
			self._cellsByRow: dict = {}
		return self._cellsByRow


def columnNumberOf(cell) -> Optional[int]:
	""":return: which column an object says it is in, or None if it does not say.

	`columnNumber` is NVDA's, filled in from `GridItemPattern` for UIA and from the table cell
	interface for IAccessible2. A cell that raises `NotImplementedError` is one whose platform
	does not number its cells, which is an answer rather than a failure.
	"""
	try:
		found = getattr(cell, "columnNumber", None)
	except Exception:
		return None
	try:
		return int(found) if found else None
	except Exception:
		return None


def headerTextOf(cell) -> str:
	""":return: what a cell says its column's header is, or "" if it says nothing.

	`columnHeaderText` is NVDA's, and for UIA it resolves `TableItemColumnHeaderItemsPropertyId`
	to the header elements' text — the same work `appModules/outlook.py` does by hand when it
	builds a row's name for speech. Written as one line, since several header cells come back
	joined and a pinned header row is one row.
	"""
	try:
		said = getattr(cell, "columnHeaderText", None)
	except Exception:
		log.debugWarning("Could not read a cell's column header", exc_info=True)
		return ""
	return " ".join(said.split()) if said else ""


def cellText(cell, header: str = "") -> str:
	""":return: what one cell of an object table says.

	The name, which is what NVDA speaks for such a cell and what `outlook.UIAGridRow` joins
	together to name a whole row.

	**Unless the name is the column's header**, which is how File Explorer presents a
	property cell: its name is "Status" and its value is "Always available on this device".
	A column already says what it is — that is the whole argument for laying a table out
	spatially — so a name that only repeats the header is a label, and the value is the
	content. The two are told apart by asking, not by knowing about File Explorer.
	"""
	try:
		name = (getattr(cell, "name", None) or "").strip()
	except Exception:
		log.debugWarning("Could not read a cell's name", exc_info=True)
		name = ""
	try:
		value = (getattr(cell, "value", None) or "").strip()
	except Exception:
		log.debugWarning("Could not read a cell's value", exc_info=True)
		value = ""
	if name and header and name == header:
		return value or name
	return name or value


def tableFor(obj) -> Optional[ObjectTable]:
	""":return: the object table the reader is in, or None if they are not in one.

	**Whatever the focus is on.** Where it lands differs by application and there is no
	arranging that: Outlook's message list focuses the row, File Explorer's file list focuses
	one property of the file. So this asks three questions in turn — is this a row whose cells
	it answers for, is it a cell, is it a row whose cells are objects — and the first that says
	yes decides both the row and the shape.

	:param obj: the object the reader is on.
	"""
	if obj is None:
		return None
	table = rowsTable(obj)
	if table is not None:
		return RowCellTable(table, row=obj)
	row = cellsRow(obj)
	if row is not None and cellsOfARow(row):
		try:
			# The focus was on a cell, so it says which column the reader is on as well as
			# which row. Where it was on the row instead there is nothing to say, and the
			# cursor goes on the first column of it.
			return CellObjectTable(row.parent, row=row, column=columnNumberOf(obj) or 1)
		except Exception:
			log.debugWarning("Could not reach the table a cell is in", exc_info=True)
			return None
	if cellsOfARow(obj):
		try:
			return CellObjectTable(obj.parent, row=obj)
		except Exception:
			log.debugWarning("Could not reach the table a row is in", exc_info=True)
			return None
	return None
