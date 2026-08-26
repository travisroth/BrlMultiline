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

**Nothing here reads an accessibility API.** `NVDAObjects.behaviors.RowWithoutCellObjects` is
the contract: a row whose cells are not objects answers `_getColumnContent`,
`_getColumnHeader` and `_getColumnLocation`, and `sysListView32` implements all three through
NVDA's own in-process helper. Reaching past that to the list view messages ourselves would be
a second implementation of something already written, tested and optimised.

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
	""":return: the table an object is a row of, or None if it is not one.

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


class ObjectCellInfo:
	"""One cell of an object table, in the shape the rest of the add-on already reads.

	A document cell arrives as a `TextInfo`, and three things are asked of it: what it says,
	how to put the caret in it, and what its column's header is. This answers those three and
	claims to be nothing else.
	"""

	def __init__(self, text: str, row=None, header: str = "") -> None:
		"""
		:param text: the cell's content.
		:param row: the object the cell belongs to, for routing into it.
		:param header: what the table says this column's header is, or "" if it says nothing.
		"""
		self.text = text
		self.row = row
		self.header = header
		self.isCollapsed = True

	def copy(self) -> "ObjectCellInfo":
		return ObjectCellInfo(self.text, self.row, self.header)

	def collapse(self, end: bool = False) -> None:
		"""A cell is already a place rather than a range. Kept for the shape of a position."""

	def updateCaret(self) -> None:
		"""Go to this cell, which for a list view means focusing its row.

		The item is the only thing there is to go to: a column of a list view is a rectangle
		on the screen and not a place the keyboard can be put. Focusing the row is what
		clicking one does, and it is what the reader means by routing into a value.
		"""
		if self.row is None:
			return
		try:
			self.row.setFocus()
		except Exception:
			log.debugWarning("Could not focus a table row", exc_info=True)

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
	"""

	def __init__(self, table, row=None) -> None:
		"""
		:param table: the object holding the rows. It answers `rowCount` and `columnCount`.
		:param row: the row object the reader is on, if it is known.
		"""
		self.obj = table
		"""The real object, which is what NVDA compares a region against. See `TableRow`."""

		self.table = table
		self.focused = row
		self._rows: dict = {}
		"""Row objects by row number, so that a bandful of cells costs a bandful of rows."""

	# The three questions `flowTableSource` asks of a document.

	@property
	def selection(self):
		""":return: where the reader is, which for an object table is which row they are on."""
		return ObjectCellInfo("", row=self.focused)

	def _getTableCellCoords(self, info) -> ObjectCell:
		""":return: which cell of the table the reader is in.

		:raises LookupError: if they are not in one, which is how `tableAt` hears "no".
		"""
		row = self.rowNumberOf(self.focused)
		if row is None:
			raise LookupError("Not on a row of this table")
		return ObjectCell(TABLE_ID, row, 1)

	def _getTableDimensions(self, info) -> tuple:
		""":return: how many rows and columns the table has, as the table itself says.

		`rowCount` rather than `childCount` where the object offers it: a list view answers
		the first with one window message and the second by building every item.
		"""
		return (self.numRows, self.numCols)

	def _getTableCellAt(self, tableID, startPos, row: int, column: int) -> ObjectCellInfo:
		""":return: the cell at one coordinate.

		:raises LookupError: for a coordinate the table has not got, and for a column it is
			not showing. A list view keeps columns of zero width — NVDA's own reading of one
			skips them for the same reason — and a column nobody can see is a column whose
			width would be spent on nothing.
		"""
		if tableID != TABLE_ID:
			raise LookupError("Wrong table")
		item = self.rowObject(row)
		if item is None:
			raise LookupError(f"No row {row}")
		if not 1 <= column <= self.numCols:
			raise LookupError(f"No column {column}")
		if not self._isShowing(item, column):
			raise LookupError(f"Column {column} is not showing")
		try:
			text = item._getColumnContent(column)
		except Exception:
			log.debugWarning(f"Could not read column {column} of row {row}", exc_info=True)
			raise LookupError(f"Column {column} of row {row} could not be read")
		return ObjectCellInfo(text or "", row=item, header=self.headerOf(item, column))

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
		""":return: how many columns the table has."""
		try:
			return int(getattr(self.table, "columnCount", 0) or 0)
		except Exception:
			log.debugWarning("Could not read a table's columnCount", exc_info=True)
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
		for name in ("rowNumber", "positionInSet"):
			try:
				found = getattr(item, name, None)
			except Exception:
				log.debugWarning(f"Could not read an object's {name}", exc_info=True)
				continue
			if found:
				return int(found)
		return None

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


def tableFor(obj) -> Optional[ObjectTable]:
	""":return: the object table the reader is in, or None if they are not in one.

	:param obj: the object the reader is on. A row of the table, which is what the focus is
		on in a list view: the cells are not objects and there is nothing else to be on.
	"""
	table = rowsTable(obj)
	if table is None:
		return None
	return ObjectTable(table, row=obj)
