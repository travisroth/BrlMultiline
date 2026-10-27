# BrlMultiline: reading a table that is made of objects.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A list view read as a table, through NVDA's own answers about it.

For example Outlook's message list and File Explorer's file list. Nothing here reads an
accessibility API directly; both of NVDA's shapes of table object are read through NVDA's
own properties, and which shape a table is comes from asking:

1. `NVDAObjects.behaviors.RowWithoutCellObjects`: cells are not objects, and the row answers
   `_getColumnContent`, `_getColumnHeader` and `_getColumnLocation`, as `sysListView32` does.
2. `NVDAObjects.behaviors.RowWithFakeNavigation`: cells are objects answering `columnNumber`
   and `columnHeaderText`, as Outlook's `UIAGridRow` and File Explorer's UIA grid do.

The seam is `documentBase.DocumentWithTableNavigation`, which `flowTableSource` walks. This
module presents a list view as one, so everything above the seam is the same code for a web
page and a list.

A list view cell cannot be pointed at, so routing into a cell focuses what can take the
focus. See `ObjectCellInfo.updateCaret`.
"""

from typing import Any, Optional

from logHandler import log

from .flow import CallCancelled

TABLE_ID = "objectTable"
"""What this stands in for NVDA's table identifier with.

Each presented table holds one table, so the object, the other half of
`flowTableSource.TableHandle.key`, is what tells them apart.
"""

WALK_LIMIT = 64
"""How far this will step through a list to reach a row it has not got.

Past this the table is asked for the row by number instead.
"""

MAX_STEPS_PER_ROW = 4
"""How many siblings may be stepped over for each row a walk is asked to cover.

A walk steps over non-rows such as group headings, so the number of siblings needs its own
bound.
"""

MAX_DECORATIONS = 8
"""How many children at the front of a list are looked at before its rows are assumed to start.

The panes, scrollbars and header before the first row. Looked at once per table.
"""

MAX_ROW_CELLS = 64
"""The most children an object can have and still be read as one row of a table.

A bound on a call into the application, since the object asked about may be the whole list.
"""

CONTAINER_ROLES = frozenset(
	{
		"TABLE",
		"TABLEBODY",
		"DATAGRID",
		"LIST",
		"LISTBOX",
		"TREEVIEW",
		"GROUPING",
		"PANE",
		"WINDOW",
		"FRAME",
		"DOCUMENT",
		"DIALOG",
		"PROPERTYPAGE",
		"APPLICATION",
	},
)
"""Roles that hold rows rather than being one.

For containers, such as Outlook's message list, that carry the same cell properties as their
rows. See `tableFor`. By name, so nothing depends on `controlTypes` numbering.
"""

MIN_COLUMNS = 2
"""How many columns a list needs before it is worth laying out as a table.

One column is a list, which NVDA reads well already.
"""


def rowsTable(obj) -> Optional[Any]:
	""":return: the table an object is a row of, or None if its cells are not answered by it.

	The `RowWithoutCellObjects` contract, asked for rather than tested with `isinstance`, since
	an app module may implement it without inheriting. `columnCount` is the table's own.

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

	An object answering `columnNumber` is a cell, and its parent is the row. File Explorer puts
	the focus on such a cell (`appModules.explorer.UIProperty`) rather than on the row.

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

	A row needs one of three signs:

	1. Its children say which column they are in, and they name different columns. Children
	   all in the same column are rows of a container.
	2. The table says how many columns it has, and the row has that many children. Outlook's
	   `UIAGridRow` children are plain text elements, so this is needed there.
	3. It says it holds a grid place, and its parent says it holds rows. Tight on purpose, so
	   a cell of File Explorer's Details view, whose parent is one file, is not read as a row.

	Counted rather than trusting the first child, since tree items have children too.

	:param obj: the object the reader is on.
	"""
	if obj is None:
		return []
	children = _childrenOf(obj)
	if len(children) < MIN_COLUMNS:
		return []
	numbered = [found for found in (columnNumberOf(child) for child in children) if found]
	if len(set(numbered)) >= MIN_COLUMNS:
		return children
	if len(numbered) >= MIN_COLUMNS:
		# They say which column they are in and it is the same column. See above.
		return []
	if _columnsOfTheTableAbove(obj) >= MIN_COLUMNS:
		return children
	return children if _holdsAPlaceInAGrid(obj) else []


def _holdsAPlaceInAGrid(obj) -> bool:
	""":return: whether an object says it is a row of something that says it holds rows.

	The third way of being sure, for a grid that will not say how wide it is. See
	`cellsOfARow`, which is the only caller and carries the argument.
	"""
	if columnNumberOf(obj) is None:
		return False
	try:
		return isAContainerOfRows(obj.parent)
	except Exception:
		log.debugWarning("Could not ask what an object holding a grid place is in", exc_info=True)
		return False


def _childrenOf(obj, limit: int = MAX_ROW_CELLS) -> list:
	""":return: an object's children, or nothing if it holds more than a row could.

	Bounded, because the object may be a whole list rather than a row. `childCount` first,
	which answers without building the children; otherwise they are read and counted.

	:param obj: the object to look inside.
	:param limit: the most children a row of a table can have and still be one.
	"""
	try:
		count = getattr(obj, "childCount", None)
		if count is not None and int(count) > limit:
			return []
	except (TypeError, ValueError):
		pass
	except Exception:
		log.debugWarning("Could not ask how many children an object has", exc_info=True)
	try:
		children = list(obj.children or [])
	except Exception:
		log.debugWarning("Could not read an object's children", exc_info=True)
		return []
	return children if len(children) <= limit else []


def roleNameOf(obj) -> str:
	""":return: what NVDA calls an object's role, as a name, or "" where it will not say.

	The name, so nothing depends on `controlTypes` and test stand-ins can answer a string.
	"""
	try:
		role = getattr(obj, "role", None)
	except Exception:
		log.debugWarning("Could not ask an object what it is", exc_info=True)
		return ""
	if role is None:
		return ""
	return (getattr(role, "name", None) or str(role)).upper()


def isAContainerOfRows(obj) -> bool:
	""":return: whether an object says it holds rows, rather than being one.

	See `CONTAINER_ROLES`. An object that will not say its role is not one.
	"""
	return roleNameOf(obj) in CONTAINER_ROLES


def _columnsOfTheTableAbove(row) -> int:
	""":return: how many columns the table holding a row says it has, or zero if it does not say."""
	try:
		table = row.parent
		return int(getattr(table, "columnCount", 0) or 0) if table is not None else 0
	except Exception:
		log.debugWarning("Could not ask a table how many columns it has", exc_info=True)
		return 0


def _canTakeFocus(obj) -> bool:
	""":return: whether an object is one the keyboard can be put on.

	NVDA's `isFocusable`. See `ObjectCellInfo.updateCaret`. An object that will not answer
	counts as focusable, since routing it to its row would lose the column.
	"""
	try:
		said = getattr(obj, "isFocusable", None)
	except Exception:
		log.debugWarning("Could not ask whether a cell can take the focus", exc_info=True)
		return True
	return True if said is None else bool(said)


def _positionOf(obj) -> dict:
	""":return: an object's `positionInfo`, with the three numbers read as numbers.

	One reader for it, so a `positionInfo` that raises or is unreadable answers every question
	the same way: it does not say.
	"""
	try:
		where = getattr(obj, "positionInfo", None) or {}
	except Exception:
		log.debugWarning("Could not read an object's position", exc_info=True)
		return {}
	found = {}
	for name in ("indexInGroup", "similarItemsInGroup", "level"):
		try:
			said = where.get(name)
		except Exception:
			return {}
		try:
			found[name] = int(said) if said else 0
		except (TypeError, ValueError):
			found[name] = 0
	return found


def _ask(thing, name: str):
	""":return: what an object answers for one property, or why it did not, for the log.

	Every one of these raises `NotImplementedError` on an object whose platform has no answer,
	which is an answer and is worth writing down as one.
	"""
	try:
		return getattr(thing, name, None)
	except Exception as error:
		return f"refused ({type(error).__name__})"


def describeThing(obj) -> str:
	""":return: an object in the few words that tell one apart from another, for the log."""
	if obj is None:
		return "none"
	role = _ask(obj, "role")
	role = getattr(role, "name", role)
	return f"{type(obj).__name__} role={role} name={_ask(obj, 'name')!r}"


_pendingColumn = None
"""The cell a routing key asked for, waiting for the row it is in to take the focus.

`setFocus` is a request; when the focus arrives NVDA moves the navigator object to it, which
would undo a column set at once. As in NVDA's `RowWithFakeNavigation`, the column is
remembered and put back after the focus arrives. One request at a time, consumed by the next
focus event whatever it is.
"""


def askForColumnAfterFocus(row, cell) -> None:
	"""Remember the column to go to once the row taking the focus has taken it.

	The column, not the cell, because the focused row and its cells are fresh objects, as
	`RowWithFakeNavigation._savedColumnNumber` also allows for. See `columnWantedAfterFocus`.

	:param row: the object being focused.
	:param cell: the cell the reader routed into, for the column it is in.
	"""
	global _pendingColumn
	_pendingColumn = (row, columnNumberOf(cell) or 1)


def columnWantedAfterFocus(obj) -> bool:
	"""Put the navigator object on the cell a routing key asked for, the focus having arrived.

	Called for every focus change; with no request it is a `None` test. The cell is resolved
	against the row that just took the focus. See `askForColumnAfterFocus`.

	:param obj: what has just taken the focus.
	:return: whether a request was fulfilled by it.
	"""
	global _pendingColumn
	pending, _pendingColumn = _pendingColumn, None
	if pending is None:
		return False
	row, column = pending
	if not _isTheSame(obj, row):
		# Some other focus change got there first, so the request was about a move that did
		# not happen. Dropped rather than held: a stale request is one that fires against
		# whatever the reader does next.
		return False
	cell = cellObjectOf(obj, column)
	if cell is None:
		return False
	try:
		import api

		api.setNavigatorObject(cell)
	except Exception:
		log.debugWarning("Could not take the navigator object to a table cell", exc_info=True)
		return False
	return True


def cellObjectOf(row, column: int):
	""":return: the cell object for one column of a row, or None if it has not got it.

	By the cell's own `columnNumber` where it has one, which survives merged or missing cells,
	and by position otherwise. `CellObjectTable.cellObject` answers the same way.

	:param row: the row object.
	:param column: which column of it, one based.
	"""
	cells = _childrenOf(row)
	for cell in cells:
		if columnNumberOf(cell) == column:
			return cell
	if any(columnNumberOf(cell) is not None for cell in cells):
		# The row numbers its cells and none of them is this one, so the cell is genuinely
		# missing rather than merely unnumbered.
		return None
	return cells[column - 1] if 1 <= column <= len(cells) else None


def _isTheSame(obj, other) -> bool:
	""":return: whether two objects are the same one, asking NVDA before trusting identity.

	NVDA makes a new wrapper on every fetch, so `__eq__` is asked, which can raise for an
	object that has gone.
	"""
	if obj is other:
		return True
	try:
		return bool(obj == other)
	except Exception:
		return False


class ObjectCellInfo:
	"""One cell of an object table, in the shape the rest of the add-on already reads.

	Answers the three things asked of a document cell's `TextInfo`: its text, how to put the
	caret in it, and its column header.
	"""

	def __init__(self, text: str, target=None, header: str = "", row=None, fetch=None) -> None:
		"""
		:param text: the cell's content.
		:param target: the object the reader means when they route into this cell. The cell
			itself where cells are objects, and the row where they are not — see
			L{updateCaret}.
		:param header: what the table says this column's header is, or "" if it says nothing.
		:param row: the row the cell is in, which is the thing that can take the focus when
			the cell cannot. The target itself when there is nothing else.
		:param fetch: how to get the object, for a cell whose text arrived without one. Built
			only when a routing key lands on the cell, since building one per cell is slow.
		"""
		self.text = text
		self._target = target
		self._fetch = fetch
		self.header = header
		self._row = row
		self.isCollapsed = True

	@property
	def target(self):
		""":return: the object this cell stands for, fetched now if it was not to hand."""
		if self._target is None and self._fetch is not None:
			try:
				self._target = self._fetch()
			except Exception:
				log.debugWarning("Could not fetch the object behind a table cell", exc_info=True)
			finally:
				# Once, whatever came back. A cell that cannot be reached is not going to be
				# reachable on the next routing key either, and this is the reader pressing.
				self._fetch = None
		return self._target

	@property
	def row(self):
		""":return: the row this cell is in, which for a grid is the cell itself."""
		return self._row if self._row is not None else self.target

	def copy(self) -> "ObjectCellInfo":
		return ObjectCellInfo(self.text, self._target, self.header, self._row, self._fetch)

	def collapse(self, end: bool = False) -> None:
		"""A cell is already a place rather than a range. Kept for the shape of a position."""

	def updateCaret(self) -> None:
		"""Go to this cell, the way NVDA itself goes to one.

		1. A cell that can take the focus, as in File Explorer, is focused.
		2. Otherwise the row is focused and the navigator object taken to the cell, as
		   `RowWithFakeNavigation._moveToColumn` does for Outlook.
		3. Where cells are not objects, the row is focused, as clicking it does.
		"""
		target = self.target if self.target is not None else self.row
		if target is None:
			return
		focus = target if _canTakeFocus(target) else self.row
		if focus is not target:
			# Before asking for the focus rather than after, so that an event which arrives
			# while `setFocus` is still running finds the request already made.
			askForColumnAfterFocus(focus, target)
		try:
			if focus is not None:
				focus.setFocus()
		except Exception:
			log.debugWarning("Could not go to a table cell", exc_info=True)
		if focus is target:
			return
		try:
			import api

			# Set now as well as after the focus event. The row may have the focus already,
			# in which case asking for it changes nothing and no event follows; the request
			# above is then consumed by whatever the reader focuses next, and does nothing
			# because that is not this row.
			api.setNavigatorObject(target)
		except Exception:
			log.debugWarning("Could not take the navigator object to a table cell", exc_info=True)

	def getTextWithFields(self, formatConfig=None) -> list:
		""":return: this cell's control field, carrying the header its column declares.

		The same attribute a browse mode document gives, so `flowTableSource.declaredHeader`
		need not know where a table came from. A real `ControlField`, because NVDA's
		`FieldCommand` raises for a plain dictionary.
		"""
		from textInfos import ControlField, FieldCommand

		field = ControlField()
		field["table-id"] = TABLE_ID
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

	Presents the three methods without subclassing `DocumentWithTableNavigation`, which would
	bring scripts and browse mode behaviour. Finding rows is here; reading a cell is in a
	subclass per shape. See the module docstring.
	"""

	hasHeaderRow = False
	"""Whether the first row of this table is its headings.

	False: a list's first item is content, and its headings, if any, are declared by its cells.
	"""

	@property
	def contentRows(self) -> int:
		""":return: how many rows this table has content in, for noticing that it changed.

		The row count, for anything but a grid. See `SheetTable.contentRows` and
		`rowCountIsExact`.
		"""
		return self.numRows

	rowCountIsExact = False
	"""Whether this table's count of its rows is a fact rather than what has been built so far.

	False for a list, whose count grows as the platform builds items. True for a grid, where a
	change means rows were added or removed. See `flowBand.FlowBand._tableChangedShape`.
	"""

	emptyColumnsArePlaces = False
	"""Whether a column of this table that holds nothing is still somewhere the reader goes.

	False for a list, so an empty column is left off the band. True for a grid, where the
	reader writes into empty columns, so the empty column they are in is kept. See
	`flowTableSource.measure`.
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

		self._rowsBegin: Optional[int] = None
		"""How many children come before the rows, once something has looked. See
		`rowsBeginAt`."""

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

		:raises LookupError: for a coordinate the table has not got, or a column it is not
			showing, which is how `flowTableSource` hears there is nothing there.
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

		The only thing a subclass has to answer.

		:param item: the row object.
		:param column: which column of it, one based.
		:param row: which row it is, for the log.
		:raises LookupError: for a cell the row has not got or is not showing.
		"""
		raise NotImplementedError

	# What the answers are made of.

	@property
	def numRows(self) -> int:
		""":return: how many rows the table has.

		In order: the row's own `positionInfo` count, which covers the whole list; then
		`rowCount`; then `childCount`. The last two can count only the items built so far.
		Never fewer than the row the reader is on.
		"""
		return max(self._counted(), self.rowNumberOf(self.focused) or 0)

	def _counted(self) -> int:
		""":return: how many rows something says there are, by the first that says. See `numRows`."""
		among = self.itemsAmong(self.focused)
		if among:
			return among
		for name in ("rowCount", "childCount"):
			try:
				count = getattr(self.table, name, None)
			except Exception:
				log.debugWarning(f"Could not read a table's {name}", exc_info=True)
				continue
			if count:
				return int(count)
		return 0

	def itemsAmong(self, item) -> int:
		""":return: how many rows the row in hand says it is one of, or zero if it does not say.

		`positionInfo["similarItemsInGroup"]`. Zero where rows are counted per group. See
		`positionNumbersTheTable`.
		"""
		if item is None or not self.positionNumbersTheTable():
			return 0
		return _positionOf(item).get("similarItemsInGroup") or 0

	def positionNumbersTheTable(self) -> bool:
		""":return: whether `positionInfo` numbers this table's rows, rather than a group of them.

		`indexInGroup` restarts in every group of a grouped list, such as File Explorer grouped
		by type. Either sign refuses:

		1. A level below the first.
		2. The table admits to more rows than the group holds. Strict: one more counts. Fewer
		   is only a list not fully built. See `childrenAdmittedTo`, which removes decorations.

		Refusing leaves the control to NVDA's ordinary reading, and the sign is recorded for
		the report. See `whyItNumbersAGroup` and `describe`.
		"""
		return self.whyItNumbersAGroup() is None

	def whyItNumbersAGroup(self) -> Optional[str]:
		""":return: which sign said the numbers count a group, or None if none of them did.

		The reader hears only "not in a table", so the reason goes in the report. See `describe`.
		"""
		where = _positionOf(self.focused)
		if not where.get("indexInGroup"):
			return "the row does not say where in a group it is"
		level = where.get("level") or 0
		if level > 1:
			return f"the row says it is at level {level}, so it is in a branch rather than the table"
		among = where.get("similarItemsInGroup") or 0
		admitted = self.childrenAdmittedTo()
		if among and admitted > among:
			return f"the table admits to {admitted} rows while the row says its group holds {among}"
		return None

	def childrenAdmittedTo(self) -> int:
		""":return: the most rows this table admits to holding, or zero if it will not say.

		The larger of `rowCount` and `childCount`, read as a floor by `positionNumbersTheTable`.
		Decorations such as panes, scrollbars and the header are taken off `childCount`.
		"""
		found = 0
		for name, decorated in (("rowCount", False), ("childCount", True)):
			try:
				count = getattr(self.table, name, None)
			except Exception:
				log.debugWarning(f"Could not read a table's {name}", exc_info=True)
				continue
			try:
				said = int(count or 0)
			except Exception:
				continue
			found = max(found, said - self.rowsBeginAt() if decorated else said)
		return max(found, 0)

	@property
	def numCols(self) -> int:
		""":return: how many columns the table has.

		The table's own answer, or else the row's cell count.
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

		`rowNumber`, or `indexInGroup` where the group is the whole table (see
		`positionNumbersTheTable`). Never counted, which would be slow on a long list.
		"""
		if item is None:
			return None
		try:
			found = getattr(item, "rowNumber", None)
			if found:
				return int(found)
		except Exception:
			log.debugWarning("Could not read an object's rowNumber", exc_info=True)
		if not self.positionNumbersTheTable():
			return None
		return _positionOf(item).get("indexInGroup") or None

	def rowObject(self, row: int):
		""":return: the object for one row of the table, or None if it has not got that row.

		Remembered, since each cell of a row asks for it; `forget` drops them.
		"""
		if row in self._rows:
			return self._rows[row]
		found = None
		if self.focused is not None and self.rowNumberOf(self.focused) == row:
			# The one row already in hand. Fetching it again would cost a search to arrive
			# back at the object the focus event just handed over.
			found = self.focused
		elif 1 <= row <= self.numRows:
			found = self.walkTo(row) or self.childAt(row - 1 + self.rowsBeginAt())
		self._rows[row] = found
		return found

	def childAt(self, index: int):
		""":return: one child of the table by its position, or None if it has not got it.

		For a list that has built all its items. See `rowsBeginAt` and `looksLikeARow`.

		:param index: which child, zero based.
		"""
		try:
			found = self.table.getChild(index)
		except Exception:
			log.debugWarning(f"Could not reach child {index} of a table", exc_info=True)
			return None
		return found if found is not None and self.looksLikeARow(found) else None

	def rowsBeginAt(self) -> int:
		""":return: how many of the table's first children come before its rows.

		A list's first children may be panes, scrollbars or a header. Counted once, by stepping
		over the front until something looks like a row. None within `MAX_DECORATIONS` means
		no offset.
		"""
		if self._rowsBegin is None:
			self._rowsBegin = 0
			child = _firstChildOf(self.table)
			for seen in range(MAX_DECORATIONS):
				if child is None:
					break
				if self.looksLikeARow(child):
					self._rowsBegin = seen
					break
				child = _nextOf(child)
		return self._rowsBegin

	def looksLikeARow(self, obj) -> bool:
		""":return: whether one child of the table is one of its rows.

		By the role of the row in hand, so this agrees with `tableFor`. With no row in hand, a
		child with cells is a row.
		"""
		template = self.focused
		if template is None:
			return bool(cellsOfARow(obj))
		return roleNameOf(obj) == roleNameOf(template)

	def walkTo(self, row: int):
		""":return: a row reached by stepping from one already in hand, or None.

		Stepping rather than indexing, because a virtualised list builds only the items on
		screen. Usually one step from a remembered row. Siblings that are not rows are stepped
		over without counting, within `MAX_STEPS_PER_ROW`.

		:param row: which row to reach, one based.
		"""
		known = [number for number in self._rows if self._rows[number] is not None]
		if self.focused is not None:
			here = self.rowNumberOf(self.focused)
			if here:
				known.append(here)
				self._rows.setdefault(here, self.focused)
		if not known:
			return None
		from_ = min(known, key=lambda number: abs(number - row))
		steps = abs(row - from_)
		if steps > WALK_LIMIT:
			# Further than stepping is worth. A jump that long is not a reader reading on.
			return None
		item = self._rows.get(from_)
		forward = row > from_
		reached = from_
		for _ in range(steps * MAX_STEPS_PER_ROW):
			try:
				item = item.next if forward else item.previous
			except Exception:
				log.debugWarning("Could not step to the next row of a table", exc_info=True)
				return None
			if item is None:
				return None
			if not self.looksLikeARow(item):
				# A heading between two groups, or the header at the top of the list. It sits
				# between the rows and is not one of them, so the count does not move.
				continue
			reached += 1 if forward else -1
			# Remembered on the way past, so a walk of five rows is five steps rather than
			# five walks. The window asks for them in order, which is what makes this pay.
			self._rows[reached] = item
			if reached == row:
				return item
		return None

	def describe(self) -> list:
		"""What this stand-in found, for the log.

		Every answer the table was built from, since they are cheap to ask for.

		:return: one line per thing worth knowing, no line of it needed to draw anything.
		"""
		group = self.whyItNumbersAGroup()
		lines = [
			f"  shape: {self.numRows} rows by {self.numCols} columns, as {self!r}",
			f"  table object: {describeThing(self.table)}",
			f"  row in hand: {describeThing(self.focused)} at row {self.rowNumberOf(self.focused)}",
			f"  the row's own rowNumber: {_ask(self.focused, 'rowNumber')}",
			f"  the row says it is one of {self.itemsAmong(self.focused) or 'it does not say'}",
			f"  its position numbers {'a group of it' if group else 'the table'}"
			f", from {_positionOf(self.focused)}" + (f", because {group}" if group else ""),
			f"  the table's own rowCount: {_ask(self.table, 'rowCount')}"
			f", childCount: {_ask(self.table, 'childCount')}"
			f", columnCount: {_ask(self.table, 'columnCount')}",
		]
		lines.extend(self.describeAroundTheTable())
		lines.extend(self.describeCells())
		return lines

	def describeAroundTheTable(self) -> list:
		""":return: the first few things inside the table and beside it, for the log.

		Where a header control would be. It is a child of the list or a sibling depending on
		the application, so the first few of both are described.
		"""
		lines = []
		for what, obj in (
			("inside the table", self.table),
			("beside the table", _parentOf(self.table)),
		):
			if obj is None:
				continue
			lines.append(f"  {what}, first few: {'; '.join(describeFirstFew(obj)) or 'nothing'}")
		return lines

	def describeCells(self) -> list:
		""":return: what each column of the row in hand holds, and where it came from.

		Each source `headerTextOf` asks, separately, beside what it made of them.
		"""
		item = self.focused
		if item is None:
			return ["  no row in hand, so there are no cells to describe"]
		found = []
		for column in range(1, min(self.numCols, 12) + 1):
			try:
				cell = self.cellOf(item, column, self.rowNumberOf(item) or 0)
			except LookupError as error:
				found.append(f"  column {column}: nothing — {error}")
				continue
			except Exception as error:
				found.append(f"  column {column}: could not be read — {error!r}")
				continue
			found.append(f"  column {column}: text {cell.text!r} under header {cell.header!r}")
			found.append(f"    the cell it came from: {self.describeCellSources(item, column)}")
		return found or ["  the row in hand has no columns"]

	def describeCellSources(self, item, column: int) -> str:
		""":return: what the object behind one cell answers to each header question.

		A stand-in whose cells are not objects has no such object and says so; there the
		header is the row's own answer for that column and there is nowhere else it could have
		come from.
		"""
		cell = self.cellObject(item, column) if hasattr(self, "cellObject") else None
		if cell is None:
			return "the row answers for its own columns, so there is no cell object to ask"
		name, value = nameAndValueOf(cell)
		return (
			f"{describeThing(cell)}, name {name!r}, value {value!r}"
			f", columnHeaderText {_ask(cell, 'columnHeaderText')}"
		)

	def sameAs(self, other) -> bool:
		""":return: whether another stand-in is presenting the same control as this one.

		For `flowTableSource.sameTable`. Stand-ins and NVDA objects are built afresh on every
		ask, so identity always differs; NVDA's equality is used instead.
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
		# The class name rather than a fixed one: which of the two shapes a table was read
		# as is the first thing a report about it has to settle.
		return f"<{type(self).__name__} {self.numRows}x{self.numCols} over {self.table!r}>"


class RowCellTable(ObjectTable):
	"""A table whose cells are not objects: the classic Win32 list view.

	`NVDAObjects.behaviors.RowWithoutCellObjects` is the contract and `sysListView32`
	implements it through NVDA's in-process helper, so a cell, its header and its rectangle
	are three questions asked of the row.
	"""

	def cellOf(self, item, column: int, row: int) -> ObjectCellInfo:
		""":return: one cell, read out of its row. See `ObjectTable.cellOf`."""
		cell = self.cellObject(item, column)
		if not self._isShowing(item, column, cell):
			raise LookupError(f"Column {column} is not showing")
		text = self.contentOf(item, column, cell)
		if text is None:
			raise LookupError(f"Column {column} of row {row} could not be read")
		# The row rather than the cell: a column of a classic list view is a rectangle on the
		# screen, and the item is the only thing the keyboard can be put on. NVDA's own cell
		# object for it is not focusable and says so, so `updateCaret` reaches the same answer
		# on its own; naming the row here says it rather than leaving it to be worked out.
		return ObjectCellInfo(text, target=item, header=self.headerOf(item, column, cell), row=item)

	def cellObject(self, item, column: int):
		""":return: NVDA's own cell object for one column of a row, or None where there is none.

		Through `getChild`, which keeps off NVDA's private API: `RowWithoutCellObjects` returns a
		`_FakeTableCell` answering `name`, `columnHeaderText` and `location`. The column number is
		checked, since every object answers `getChild`. A row that makes no such cell is asked
		directly; see `contentOf`.
		"""
		try:
			cell = item.getChild(column - 1)
		except Exception:
			log.debugWarning(f"Could not reach the cell at column {column}", exc_info=True)
			return None
		return cell if cell is not None and columnNumberOf(cell) == column else None

	def columnsOfARow(self) -> int:
		""":return: how wide one row is, which such a row already answers as its child count."""
		try:
			return int(getattr(self.focused, "childCount", 0) or 0)
		except Exception:
			log.debugWarning("Could not count a row's columns", exc_info=True)
			return 0

	def contentOf(self, item, column: int, cell) -> Optional[str]:
		""":return: what one column of a row says, or None if it could not be read.

		The cell's `name`, which is where `RowWithoutCellObjects` puts a column's content.
		Failing that the row's own answer, for a row that makes no cells. See L{cellObject}.
		"""
		try:
			if cell is not None:
				return getattr(cell, "name", None) or ""
			return item._getColumnContent(column) or ""
		except Exception:
			log.debugWarning(f"Could not read column {column}", exc_info=True)
			return None

	def headerOf(self, item, column: int, cell=None) -> str:
		""":return: what the table says a column's header is, or "" if it says nothing.

		`columnHeaderText` on the cell, which `sysListView32` answers out of the list's own
		header control. It is a property of the column, so any row's cell answers.
		"""
		try:
			said = getattr(cell, "columnHeaderText", None) if cell is not None else None
			if said is None:
				said = item._getColumnHeader(column)
			return (said or "").strip()
		except Exception:
			log.debugWarning(f"Could not read the header of column {column}", exc_info=True)
			return ""

	def _isShowing(self, item, column: int, cell=None) -> bool:
		""":return: whether a column is one the table is actually showing.

		A hidden column has a rectangle with no width, the test `sysListView32` uses. Asked of
		the cell's `location` where there is a cell. A row that cannot answer counts as
		showing.
		"""
		try:
			where = cell.location if cell is not None else item._getColumnLocation(column)
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

	The `NVDAObjects.behaviors.RowWithFakeNavigation` contract, as in Outlook's message list and
	File Explorer's file list.
	"""

	def cellOf(self, item, column: int, row: int) -> ObjectCellInfo:
		""":return: one cell, which here is an object. See `ObjectTable.cellOf`."""
		cell = self.cellObject(item, column)
		if cell is None:
			raise LookupError(f"Row {row} has no column {column}")
		header = headerTextOf(cell)
		# The row as well as the cell: which of the two a routing key can put the focus on
		# differs between the two applications this was built for. See `updateCaret`.
		return ObjectCellInfo(cellText(cell, header), target=cell, header=header, row=item)

	def columnsOfARow(self) -> int:
		""":return: how wide one row is, counted from the cells of the row in hand."""
		return len(self.cellsOf(self.focused))

	def cellObject(self, item, column: int):
		""":return: the object for one column of a row, or None if the row has not got it.

		As `cellObjectOf`.
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

		One walk of the row's children per row rather than per cell.
		"""
		if item is None:
			return []
		key = id(item)
		found = self._cells.get(key)
		if found is None:
			# Through the same bound the recognition used, so that a row this could never
			# have been recognised by cannot be read a cell at a time either. See
			# `_childrenOf`: the object in hand is only a row because something said so, and
			# what said so can be wrong.
			found = _childrenOf(item)
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

	NVDA's `columnNumber`. `NotImplementedError` means the platform does not number cells.
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

	A cell with both a name and a value has named its own column, which is preferred over
	`columnHeaderText`, since that can resolve to elements that are not headings. Otherwise
	`columnHeaderText`. Always one line.
	"""
	name, value = nameAndValueOf(cell)
	if name and value and value != name:
		return name
	try:
		said = getattr(cell, "columnHeaderText", None)
	except Exception:
		log.debugWarning("Could not read a cell's column header", exc_info=True)
		return ""
	return " ".join(said.split()) if said else ""


def nameAndValueOf(cell) -> tuple:
	""":return: a cell's name and value, each stripped, each "" where it has none."""
	found = []
	for what in ("name", "value"):
		try:
			said = getattr(cell, what, None)
		except Exception:
			log.debugWarning(f"Could not read a cell's {what}", exc_info=True)
			said = None
		found.append((said or "").strip())
	return tuple(found)


def cellText(cell, header: str = "") -> str:
	""":return: what one cell of an object table says.

	The value where there is one, else the name. A cell's name is its label, which the column
	already shows. Outlook's cells have only names.

	:param header: unused, kept for the callers' signature.
	"""
	name, value = nameAndValueOf(cell)
	if value and value != name:
		return value
	return name or value


ROW_SEPARATOR = ", "
"""What joins one cell of a row to the next when the row is read as a single line.

As `appModules/outlook.py` joins a message row's fields.
"""


def rowTextOf(row, withHeaders: Optional[bool] = None, headers: Optional[dict] = None) -> str:
	""":return: what a row says about *itself*, built from its own cells, or "" if it has none.

	The cell readers the column layout uses, joined into one line. Used because NVDA's Outlook
	module builds a row's name partly from the current selection, so another row's name can
	carry the selected message's flags. Bounded by `MAX_ROW_CELLS`. States held only in the
	object model, such as unread, are not cells, so the row the reader is on keeps NVDA's name.
	See `flowObjects.namedFromTheSelection`. A cell that only repeats its own header is
	dropped.

	:param row: the row object.
	:param withHeaders: whether to put each column's name before its value, or None to follow
		NVDA's own table header setting, which is what its Outlook module follows.
	:param headers: a cache of column names, shared across the rows of one list, so the
		platform is asked once per column. None asks afresh each time.
	"""
	cells = _childrenOf(row)
	if not cells:
		return ""
	if withHeaders is None:
		from . import bmConfig

		withHeaders = bmConfig.wantsColumnHeaders()
	said = []
	for index, cell in enumerate(cells, start=1):
		text = cellText(cell)
		if not text:
			continue
		header = _headerFor(cell, index, headers)
		if _isNothingButItsOwnHeader(cell, header):
			continue
		if withHeaders and header and header != text:
			text = f"{header} {text}"
		said.append(text)
	return ROW_SEPARATOR.join(said)


def _isNothingButItsOwnHeader(cell, header: str) -> bool:
	""":return: whether a cell holds nothing but the name of its own column.

	An empty icon column comes back named after itself. The test is that there is no value,
	not that the text matches, so a file named "Name" in the Name column is kept.

	:param cell: the cell.
	:param header: what its column is called, already resolved.
	"""
	if not header:
		return False
	name, value = nameAndValueOf(cell)
	return not value and name == header


def _headerFor(cell, index: int, headers: Optional[dict]) -> str:
	""":return: what a cell's column is called, asking the platform once per column.

	Resolved even when headers are not drawn, since `_isNothingButItsOwnHeader` needs it.

	:param cell: the cell.
	:param index: its place in the row, for a cell that does not number itself.
	:param headers: the cache, or None to ask every time.
	"""
	if headers is None:
		return headerTextOf(cell)
	column = columnNumberOf(cell) or index
	if column not in headers:
		headers[column] = headerTextOf(cell)
	return headers[column]


HEADER_TRIES = 3
"""How many of a column's cells are asked what its header is, before the column has none.

More than one, because a single cell can be a bad witness: a half-built row, a merged cell,
or a cell in the header row itself. Few, because a table that declares nothing pays this on
every column.
"""

SHEET = "brlMultilineSheet"
"""What an object offers when it can hand over a grid to be read by coordinate.

The seam an application module joins the flow at. `sheetOf` asks for it once, and the answer
implements `Sheet`. Nothing here knows about Excel; that is in `appModules/excel.py`. Charts
use the same seam through `selectedValues`. One `getattr` per object the band meets.
"""


class Sheet:
	"""What an application module hands over: a grid addressed by coordinate.

	A protocol, not a base class. The third shape of table object: no row objects, and a cell
	is reached by coordinate, which is what `_getTableCellAt` asks. The cell at a coordinate
	is the one question NVDA has no generic answer for outside
	`DocumentWithTableNavigation`; everything else is asked of the cell's `NVDAObject`.
	"""

	obj = None
	"""The `NVDAObject` that *is* the grid, for identity. See `ObjectTable.obj`."""

	def shape(self) -> tuple:
		""":return: how many rows and how many columns, as (rows, columns)."""
		raise NotImplementedError

	def where(self) -> tuple:
		""":return: the row and column the reader is on, as (row, column), one based."""
		raise NotImplementedError

	def cellAt(self, row: int, column: int):
		""":return: the cell at one coordinate as an `NVDAObject`, or None if there is none.

		An object, since NVDA already answers the flow's questions about one.
		"""
		raise NotImplementedError

	# The two below are optional. An adapter that does not offer them is asked cell by cell
	# instead and answers everything above; they exist because *measuring* asks a different
	# question from *reading*, and a grid can often answer it far more cheaply.

	def usedShape(self) -> Optional[tuple]:
		""":return: how far this grid is written in, as (rows, columns), or None if it cannot say.

		Optional. Unlike `shape`, which reaches as far as the reader, this changes only when the
		sheet itself grows or shrinks.
		"""
		return None

	def selectedHeadings(self) -> Optional[bool]:
		""":return: whether the first row of the last selection read is a row of headings.

		Optional. The values cannot settle it, but a structured table declares its header row.
		None means the grid cannot say, and the caller guesses from the values. Asked after
		`selectedValues`, about the same cells.
		"""
		return None

	def whereIsIt(self) -> Optional[str]:
		""":return: what names the place this grid is in, or None to be named the ordinary way.

		Optional. Saved layouts are keyed by place (see `flowTableIdentity`), and for a worksheet
		the ordinary place is the same for every workbook. Stored only as a digest, so a file
		path is fine. See `flowTableLayouts.keyFor`.
		"""
		return None

	def textRow(self, row: int, first: int, last: int):
		""":return: the text of one row across a span of columns, or None to be asked cell by cell.

		Optional, but needed for speed: measuring wants only text (see
		`flowTableSource.measure`), and building an object per cell through `cellAt` is far too
		slow on a worksheet. Excel answers through the batch fetch NVDA's quick navigation uses.

		:param row: the row to read, one based.
		:param first: the first column of the span, one based.
		:param last: the last column of the span, inclusive.
		:return: one string per column of the span, or None where this cannot be answered
			cheaply — the caller then reads cell by cell exactly as before.
		"""
		return None

	def rowAfter(self, row: int, by: int):
		""":return: the next row the reader may be shown, or None when there is no next row.

		Optional, for rows the grid hides: a filtered worksheet still answers for hidden rows.
		Without it the walk adds one. A grid that cannot say this time should answer `row + by`,
		since None ends the walk.

		:param row: the row walked from, one based.
		:param by: which way, as 1 or -1. One row at a time is all the walk ever asks for.
		"""
		return None

	def rowShowing(self, row: int):
		""":return: whether the grid is showing one row, or None where it cannot say.

		Optional. The counterpart of `rowAfter`, for rows already on the band when a filter
		changes. Asked when the reader moves. None counts as showing.

		:param row: the row asked about, one based.
		"""
		return None

	def selectedValues(self, maxRows: int = 0, maxColumns: int = 0):
		"""What the reader has selected, as values rather than as text.

		Optional, for charts. Values, not the displayed text that `textRow` gives: "25%" is
		stored as 0.25. Text and value come back together, in one call, so they cannot
		disagree. None means no charts here.

		A selection can be a whole column, so the caller passes limits, and the grid should
		also have a ceiling of its own.

		:param maxRows: rows the caller can use at most, or 0 to leave it to the grid.
		:param maxColumns: columns the caller can use at most, on the same terms.
		:return: rows of `(text, value)` per selected cell, where `value` is a number or
			None for a cell that holds no number; or None where this cannot be answered.
		"""
		return None

	def columnsShowing(self):
		""":return: the columns the grid is showing, or None where every column is showing.

		Optional. The column counterpart of `rowAfter`, since a worksheet still answers for
		hidden columns. Without it, or with None, every column is measured.

		:return: the column numbers on show, as a set or any collection `in` works on.
		"""
		return None

	def columnHeaders(self, first: int, last: int):
		""":return: what each column of a span declares as its header, or None to ask the cells.

		Optional, mostly to say "none" without building a cell per column. A worksheet knows
		whether any header row or column is marked.

		:param first: the first column of the span, one based.
		:param last: the last column of the span, inclusive.
		:return: the header of each column that declares one, by column number — an empty
			mapping meaning "asked, and no column declares one" — or None meaning "cannot
			say", which sends the caller back to asking the cells.
		"""
		return None


def sheetOf(obj):
	""":return: the grid an object is a cell of, or None if it is not a cell of one.

	:param obj: the object the reader is on.
	"""
	if obj is None:
		return None
	offered = getattr(obj, SHEET, None)
	if offered is None:
		return None
	try:
		return offered()
	except CallCancelled:
		# Not "this is not a cell of a grid". See `flow.CallCancelled`.
		raise
	except Exception:
		log.debugWarning(f"Could not reach the sheet behind {describeThing(obj)}", exc_info=True)
		return None


class SheetTable(ObjectTable):
	"""A table addressed by coordinate, with no row objects in it at all. See `Sheet`.

	What `ObjectTable` works out by walking is answered outright, so most of it is overridden.
	A row number stands in for the row object, since the row is only held to ask for cells.
	"""

	rowCountIsExact = True
	"""A sheet knows how far it is used. See `ObjectTable.rowCountIsExact`."""

	emptyColumnsArePlaces = True
	"""A grid is a plane, and the empty column beside the data is a place. See
	`ObjectTable.emptyColumnsArePlaces`."""

	hasHeaderRow = False
	"""A spreadsheet's first row is data until somebody says otherwise, and in NVDA somebody
	does: a reader marks a header row or column themselves, and the cells then answer
	`columnHeaderText` for it. That reaches the pinned row through `headerTextOf` like any
	other declared header, so there is nothing to borrow from row one and nothing to guess."""

	def __init__(self, sheet) -> None:
		"""
		:param sheet: the grid, answering `Sheet`.
		"""
		row, column = sheet.where()
		super().__init__(sheet.obj, row=None, column=column)
		self.sheet = sheet
		self.row = max(1, int(row or 1))
		"""Which row the reader is on. Held rather than asked of a row object, since there is
		no row object to ask."""

		self.focused = self.row
		"""The row number, standing in for the row object as it does everywhere else here.

		Not None, so `describeCells` can describe the cells."""

		self._headers = None
		"""What each column is called, once something has asked. See `_headerOf`."""

		self._askEachCell = True
		"""Whether a column's header has to be got from one of its cells. False where the
		sheet answered for every column at once."""

		self._headerTries: dict = {}
		"""How many of a column's cells have been asked what it is called and said nothing.
		Bounded, so a table that names no column does not cost a read per cell for ever, and
		more than one, so the first cell asked is not the last word. See `_headerOf`."""

	@property
	def selection(self):
		""":return: where the reader is, which for a sheet is a coordinate and not a cell.

		No cell is fetched for it: `_getTableCellAt` ignores it and `_getTableCellCoords` answers
		from `row` and `column`, so a cell here would double the cost of every read.
		"""
		return ObjectCellInfo("")

	def _getTableCellCoords(self, info) -> ObjectCell:
		""":return: which cell the reader is in, which the sheet says outright."""
		return ObjectCell(TABLE_ID, self.row, self.column)

	def cellOf(self, item, column: int, row: int) -> ObjectCellInfo:
		""":return: one cell, fetched by coordinate. See `ObjectTable.cellOf`.

		:param item: the row number, which is what stands in for a row object here.
		"""
		cell = self.sheet.cellAt(row, column)
		if cell is None:
			raise LookupError(f"No cell at row {row} column {column}")
		header = self._headerOf(column, cell)
		return ObjectCellInfo(cellText(cell, header), target=cell, header=header, row=cell)

	def _headerOf(self, column: int, cell) -> str:
		""":return: what a column is called, asked of a few of its cells rather than all of them.

		On a spreadsheet `columnHeaderText` walks the marked ranges, so answers are remembered.
		A silence is only counted, and asked again from another cell up to `HEADER_TRIES` times,
		as `flowTableSource.declaredHeaders` does, since a header cell itself has no header.

		:param column: the table's own number for the column.
		:param cell: a cell of it, to ask where the sheet will not say.
		"""
		if self._headers is None:
			said = self.columnHeaders(1, self.numCols)
			# Three answers: a mapping of names; an empty mapping, meaning none are declared;
			# or None, meaning ask the cells. See `Sheet.columnHeaders`.
			self._askEachCell = said is None
			self._headers = dict(said or {})
		known = self._headers.get(column)
		if known or not self._askEachCell:
			return known or ""
		if self._headerTries.get(column, 0) >= HEADER_TRIES:
			return ""
		self._headerTries[column] = self._headerTries.get(column, 0) + 1
		found = headerTextOf(cell)
		if found:
			self._headers[column] = found
		return found

	def whereIsIt(self):
		""":return: what names the place this sheet is in, or None. See `Sheet.whereIsIt`."""
		offered = getattr(self.sheet, "whereIsIt", None)
		if offered is None:
			return None
		return offered()

	def rowText(self, row: int, first: int, last: int):
		""":return: one row's text across a span of columns in a single read, or None.

		The seam `flowTableSource.measure` looks for, handed straight to the sheet. See
		`Sheet.textRow` for what it is worth and why it is optional.
		"""
		offered = getattr(self.sheet, "textRow", None)
		if offered is None:
			return None
		return offered(row, first, last)

	def columnHeaders(self, first: int, last: int):
		""":return: what the columns of a span declare, or None to ask their cells.

		See `Sheet.columnHeaders`.
		"""
		offered = getattr(self.sheet, "columnHeaders", None)
		if offered is None:
			return None
		return offered(first, last)

	def columnsShowing(self):
		""":return: the columns the grid is showing, or None for all of them.

		Handed straight to the sheet. See `Sheet.columnsShowing`.
		"""
		offered = getattr(self.sheet, "columnsShowing", None)
		if offered is None:
			return None
		return offered()

	def rowShowing(self, row: int):
		""":return: whether the grid is still showing one row, or None where it cannot say.

		Handed straight to the sheet. See `Sheet.rowShowing`.
		"""
		offered = getattr(self.sheet, "rowShowing", None)
		if offered is None:
			return None
		return offered(row)

	def rowAfter(self, row: int, by: int):
		""":return: the next row to show, or None when the grid says there is none.

		See `Sheet.rowAfter`. Where the sheet has no opinion this answers the next row, not
		None, since None would end the walk.
		"""
		offered = getattr(self.sheet, "rowAfter", None)
		if offered is None:
			return row + by
		return offered(row, by)

	def cellObject(self, item, column: int):
		""":return: the object behind one cell, for the report. See `describeCellSources`."""
		return self.sheet.cellAt(self.rowNumberOf(item) or self.row, column)

	@property
	def contentRows(self) -> int:
		""":return: how far the sheet is written in, which is not how far the reader can go.

		Moves only when the sheet does, unlike `numRows`, which reaches as far as the reader.
		"""
		said = getattr(self.sheet, "usedShape", None)
		if said is None:
			return self.numRows
		try:
			return max(1, int(said()[0] or 1))
		except CallCancelled:
			raise
		except Exception:
			log.debugWarning("Could not ask a sheet how far it is written in", exc_info=True)
			return self.numRows

	def rowObject(self, row: int):
		""":return: the row number itself, which is what a cell is fetched by."""
		return row if 1 <= row <= self.numRows else None

	def rowNumberOf(self, item) -> Optional[int]:
		""":return: the row number, which here is the thing itself."""
		return int(item) if isinstance(item, int) and item > 0 else None

	@property
	def numRows(self) -> int:
		return max(1, int(self._shape()[0] or 1))

	@property
	def numCols(self) -> int:
		return max(1, int(self._shape()[1] or 1))

	def _shape(self) -> tuple:
		""":return: the grid's shape, asked once per reading rather than per cell."""
		if getattr(self, "_measured", None) is None:
			try:
				self._measured = tuple(self.sheet.shape())
			except CallCancelled:
				# Not a one by one sheet. See `flow.CallCancelled`.
				raise
			except Exception:
				log.debugWarning("Could not ask a sheet its shape", exc_info=True)
				self._measured = (1, 1)
		return self._measured

	def forget(self) -> None:
		super().forget()
		self._measured = None
		# The names as well as the shape: a sheet read afresh may be a sheet whose reader has
		# just marked a header row.
		self._headers = None
		self._askEachCell = True
		self._headerTries = {}

	def describe(self) -> list:
		""":return: what this found, for the dry run. See `ObjectTable.describe`."""
		return [
			f"  A sheet read by coordinate: {self.numRows} rows, {self.numCols} columns, "
			f"the reader at row {self.row} column {self.column}.",
			f"  The sheet is {describeThing(self.sheet.obj)}.",
			*self.describeCells(),
		]


def tableFor(obj) -> Optional[ObjectTable]:
	""":return: the object table the reader is in, or None if they are not in one.

	Where the focus lands differs by application, so three questions are asked in turn: a row
	answering for its cells, a cell, or a row of cell objects. The first yes decides the row
	and the shape. Something whose role says it is a table is never taken as a row. See
	`CONTAINER_ROLES`.

	:param obj: the object the reader is on.
	"""
	if obj is None:
		return None
	sheet = sheetOf(obj)
	if sheet is not None:
		# Asked first, because an application that hands over a grid has said something about
		# itself that no amount of looking at the object could work out — and because the
		# other three questions below are about shapes a spreadsheet does not have.
		return SheetTable(sheet)
	table = rowsTable(obj)
	if table is not None:
		return RowCellTable(table, row=obj)
	row = cellsRow(obj)
	if row is not None and not isAContainerOfRows(row) and cellsOfARow(row):
		try:
			# The focus was on a cell, so it says which column the reader is on as well as
			# which row. Where it was on the row instead there is nothing to say, and the
			# cursor goes on the first column of it.
			return CellObjectTable(row.parent, row=row, column=columnNumberOf(obj) or 1)
		except Exception:
			log.debugWarning("Could not reach the table a cell is in", exc_info=True)
			return None
	if not isAContainerOfRows(obj) and cellsOfARow(obj):
		try:
			return CellObjectTable(obj.parent, row=obj)
		except Exception:
			log.debugWarning("Could not reach the table a row is in", exc_info=True)
			return None
	return None


def explain(obj) -> list:
	"""What was asked of an object on the way to reading it as a table, and what it answered.

	The reader hears "not in a table" for several different failures. This logs each question
	`tableFor` asks, in order, with its answer, then the stand-in's own numbers.

	:param obj: the object the reader is on.
	:return: one line per thing asked, for the log.
	"""
	lines = describeTree(obj)
	if obj is None:
		return lines
	found = tableFor(obj)
	if found is None:
		lines.append("  read as: nothing this module can present as a table")
		return lines
	lines.append(f"  read as: {found!r}")
	try:
		lines.extend(found.describe())
	except Exception as error:
		lines.append(f"  which could not describe itself: {error!r}")
	return lines


def _parentOf(obj):
	""":return: what an object is in, or None where it will not say."""
	try:
		return getattr(obj, "parent", None)
	except Exception:
		log.debugWarning("Could not ask what an object is in", exc_info=True)
		return None


def describeFirstFew(obj, howMany: int = 6) -> list:
	""":return: the first few children of an object, described, for the log.

	Stepped, and stopped after `howMany`, since the container may be a very long list.
	"""
	found = []
	child = _firstChildOf(obj)
	while child is not None and len(found) < howMany:
		found.append(describeThing(child))
		child = _nextOf(child)
	return found


def _firstChildOf(obj):
	""":return: an object's first child, or None where it has none or will not say."""
	try:
		return getattr(obj, "firstChild", None)
	except Exception:
		log.debugWarning("Could not read an object's first child", exc_info=True)
		return None


def _nextOf(obj):
	""":return: the object after one, or None where there is none or it will not say."""
	try:
		return getattr(obj, "next", None)
	except Exception:
		log.debugWarning("Could not read the object after one", exc_info=True)
		return None


def describeTree(obj) -> list:
	""":return: the questions `tableFor` asks about an object's place in the tree, answered.

	Separate from `explain`, so a stand-in that was built can add its own numbers.

	:param obj: the object the reader is on.
	"""
	lines = [f"  the object: {describeThing(obj)}"]
	if obj is None:
		return lines
	table = rowsTable(obj)
	lines.append(
		f"  a row that answers for its own cells: {describeThing(table) if table is not None else 'no'}",
	)
	row = cellsRow(obj)
	lines.append(
		f"  a cell of: {describeThing(row) if row is not None else 'no'}"
		f", its own column: {columnNumberOf(obj)}",
	)
	lines.extend(describeChildren("this object", obj))
	if row is not None:
		lines.extend(describeChildren("the thing above it", row))
	return lines


def describeChildren(what: str, obj) -> list:
	""":return: what an object holds and what its children say they are, for the log.

	One level of the tree and the column each child claims, which decide what is a row.

	:param what: how to name this object in the line.
	:param obj: the object to look inside.
	"""
	if obj is None:
		return []
	children = _childrenOf(obj)
	columns = [columnNumberOf(child) for child in children[: MIN_COLUMNS * 4]]
	return [
		f"  {what}: {describeThing(obj)}"
		f", holds {len(children) or 'nothing this can read as cells'}"
		f", its own columnCount {_ask(obj, 'columnCount')}"
		f", a container of rows: {'yes' if isAContainerOfRows(obj) else 'no'}",
		f"  the columns its first children claim: {columns}"
		f", so cells of a row: {'yes' if cellsOfARow(obj) else 'no'}",
	]
