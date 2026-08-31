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

WALK_LIMIT = 64
"""How far this will step through a list to reach a row it has not got.

Far enough for the window to reach past what it is holding, and not so far that a jump to the
other end of a mailbox walks it item by item. Past this the table is asked to hand the row
over by number instead, which works for a list that has built all its items.
"""

MAX_STEPS_PER_ROW = 4
"""How many siblings may be stepped over for each row a walk is asked to cover.

A walk counts rows and steps over everything else — a group's heading, a scrollbar that sits
between them — so the two numbers are not the same and the physical one has to be bounded on
its own. Four to one is room for a heading before every row and a little more, and it keeps a
walk of five rows from becoming a walk of a mailbox where nothing that follows is a row at all.
"""

MAX_DECORATIONS = 8
"""How many children at the front of a list are looked at before its rows are assumed to start.

The panes, scrollbars and header a list holds before its first row — three in File Explorer's
Details view, one in Outlook's message list. Looked at once per table and never again, and
kept small because what is being looked for is at the front by definition: a list whose first
eight children are all decoration is a list this cannot count rows off by index anyway.
"""

MAX_ROW_CELLS = 64
"""The most children an object can have and still be read as one row of a table.

Far more columns than any list view shows and far fewer than a list holds. It is a bound on
a call into the application rather than a judgement about tables: what is being asked is
whether an object is a row, the object may turn out to be the whole list, and reading every
child of a message list to find that out is how a keypress becomes a freeze.
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

NVDA's own answer to "what is this", used for the one question the properties could not
settle: Outlook's message list carries the same table cell properties its rows do, so by
those alone it looked like a row of the pane above it. It calls itself a table, and a table
is not a row of anything. See `tableFor`.

By name rather than by importing `controlTypes`, so that nothing here depends on the numbers
NVDA gives its roles, and so a role this add-on has never heard of is simply not one of these.
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

	Three ways to be sure, and a row needs one of them. **Its children say which column they
	are in**, which is `GridItemPattern` for UIA and the table cell interface for IAccessible2.
	Or **the table it is in says how many columns it has**, and the row has that many children
	to fill them. Or **it says it is in a grid, and the thing it is in says it holds rows** —
	then its children are its fields, whatever they do or do not answer about themselves.

	The second was added for Outlook's message list. Its rows are `outlook.UIAGridRow` — a
	`RowWithFakeNavigation`, whose contract says outright that "the cells must be exposed as
	children" — and the row itself carries `GridItemPattern`, but its children are plain text
	elements that carry nothing. Asking only the children said "not a table" about a table
	whose own row had just said it was one.

	Counting rather than trusting the first child either way: a tree view item has children
	too, and they are items rather than cells.

	**The columns have to be different columns**, which is the reader's Outlook report. Every
	row of a grid carries `GridItemPattern` and every one of them is in column one, so a
	container of rows answered this exactly as a row of cells does: several children, all of
	them numbered. The message list was then read as one row, its rows as that row's cells,
	and the pane above it as the table — a shape in which nothing can say which row the reader
	is on, so the command said they were not in a table while standing in the one they meant.
	Cells of a row are in different columns of it. Children that all name the same column are
	naming their container's column, and that makes them rows.

	**The third way is what is left when a grid will not say how wide it is**, and it is the
	same report: a message row whose children carry nothing, in a list that answers no
	`columnCount`, has only its own word for it — and its own word is that it holds a place in
	a grid, said by the pattern this whole module recognises tables through. What it is in
	says the rest: a table holds rows, so a thing in a table that has children has fields.
	Tight on purpose. A cell of File Explorer's Details view claims a grid place too, and the
	thing above *it* is one file rather than the list, so this cannot mistake a cell for a row.

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

	Bounded, because this is a call into the application on the path that decides whether the
	reader is in a table at all, and the object it is asked about is not always a row: the
	misreading above asked it of the message list itself, whose children are every message
	NVDA has built rather than the handful of fields a row holds. NVDA's watchdog calls half a
	second a freeze, and a reader who has to wait for one to find out they are not in a table
	has been charged twice.

	`childCount` first, since a container that will say how many it holds says so without
	building any of them. Where it will not say, the children are read and then counted, which
	is the price of the answer.

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

	The name rather than the number, so that nothing here depends on `controlTypes` and a
	stand-in in a test can answer a string. See `isAContainerOfRows` and
	`ObjectTable.looksLikeARow`, which are the two questions asked of it.
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

	One property read, and it settles what the cell properties could not. See
	`CONTAINER_ROLES`, and `tableFor`, which is the caller.

	An object that will not say its role is not one of these: refusing on silence would refuse
	every implementation that answers nothing, and those are the ones this module exists to
	read.
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

	`isFocusable`, which is NVDA's own property and is the `FOCUSABLE` state read for it. A
	cell that has it is one focusing will move; a cell without it is a text element that a
	`setFocus` would leave exactly where it was. See `ObjectCellInfo.updateCaret`.

	An object that will not answer is treated as focusable, because the alternative is to
	route every one of them to its row and that is the worse mistake: the reader would lose
	the column they had their finger on with nothing having gone wrong.
	"""
	try:
		said = getattr(obj, "isFocusable", None)
	except Exception:
		log.debugWarning("Could not ask whether a cell can take the focus", exc_info=True)
		return True
	return True if said is None else bool(said)


def _positionOf(obj) -> dict:
	""":return: an object's `positionInfo`, with the three numbers read as numbers.

	One reader for it because three questions ask it — which row this is, how many rows there
	are, and whether either means what it says — and a `positionInfo` that raises or holds
	something unreadable has to answer all three the same way, which is "it does not say".
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

`setFocus` is a request rather than a move: the focus arrives later, through an event, and
NVDA sets the navigator object to whatever has just taken it whenever the review cursor is
following the focus. So a column asked for and set immediately was set and then undone, and
the reader who routed onto a subject line landed on the row.

NVDA's own `RowWithFakeNavigation` has the same problem and answers it the same way: it
remembers the column, asks for the row, and puts the column back once the focus has actually
arrived. One request at a time, consumed by the next focus event whatever that event is,
which means a request the focus never answers cannot fire later against something else.
"""


def askForColumnAfterFocus(row, cell) -> None:
	"""Remember the column to go to once the row taking the focus has taken it.

	**The column, and not the cell.** NVDA hands out a fresh wrapper for a row when the focus
	arrives, and the cells of that wrapper are fresh objects too — so the cell a routing key
	was holding belongs to the row as it was *before* the move. Taking the navigator object to
	it puts the reader on an object NVDA has already replaced, and a review reproduced exactly
	that with two equal wrappers for one row. NVDA's own `RowWithFakeNavigation` stores
	`_savedColumnNumber` for the same reason and resolves a child of the focused row after the
	event; this does the same. See `columnWantedAfterFocus`.

	:param row: the object being focused.
	:param cell: the cell the reader routed into, for the column it is in.
	"""
	global _pendingColumn
	_pendingColumn = (row, columnNumberOf(cell) or 1)


def columnWantedAfterFocus(obj) -> bool:
	"""Put the navigator object on the cell a routing key asked for, the focus having arrived.

	Called for every focus change, so it does nothing at all in the ordinary case: a module
	level `None` and a return.

	The cell is resolved **against the row that has just taken the focus**, rather than being
	the one the routing key held: that row is a fresh object and so are its cells. See
	`askForColumnAfterFocus`.

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

	By the cell's own `columnNumber` where its cells have one, which is the only answer that
	survives a row with a merged or missing cell; by position otherwise, which is what a row of
	plain children amounts to. `CellObjectTable.cellObject` is the same question asked of a row
	the table is holding, and answers it the same way over cells it has remembered.

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

	Identity is not enough: NVDA makes a new wrapper for an object every time it is fetched,
	so the row that takes the focus is rarely the very object the routing key had. `__eq__`
	is what knows they are the same underlying element, and it can raise on an object that
	has gone away between the request and the event.
	"""
	if obj is other:
		return True
	try:
		return bool(obj == other)
	except Exception:
		return False


class ObjectCellInfo:
	"""One cell of an object table, in the shape the rest of the add-on already reads.

	A document cell arrives as a `TextInfo`, and three things are asked of it: what it says,
	how to put the caret in it, and what its column's header is. This answers those three and
	claims to be nothing else.
	"""

	def __init__(self, text: str, target=None, header: str = "", row=None) -> None:
		"""
		:param text: the cell's content.
		:param target: the object the reader means when they route into this cell. The cell
			itself where cells are objects, and the row where they are not — see
			L{updateCaret}.
		:param header: what the table says this column's header is, or "" if it says nothing.
		:param row: the row the cell is in, which is the thing that can take the focus when
			the cell cannot. The target itself when there is nothing else.
		"""
		self.text = text
		self.target = target
		self.header = header
		self.row = row if row is not None else target
		self.isCollapsed = True

	def copy(self) -> "ObjectCellInfo":
		return ObjectCellInfo(self.text, self.target, self.header, self.row)

	def collapse(self, end: bool = False) -> None:
		"""A cell is already a place rather than a range. Kept for the shape of a position."""

	def updateCaret(self) -> None:
		"""Go to this cell, the way NVDA itself goes to one.

		        **Focus what can be focused, and take the navigator object the rest of the way.** That
		        is `RowWithFakeNavigation._moveToColumn` exactly: it focuses the row and calls
		        `api.setNavigatorObject` on the cell, because in Outlook's message list the row is the
		        focusable thing and its cells are text elements that are not. Focusing one of those
		        does nothing at all, so a routing key over a subject line would have moved nothing and
		said nothing.

		        Where the cell *can* take the focus it is focused directly, which is File Explorer's
		        Details view — its cells are `explorer.UIProperty` and the focus lands on one of them
		        when the reader arrows across a file. Going to the row there would move the reader off
		        the column they had their finger on.

		        Where the cells are not objects at all there is only ever the row: a column of a
		        classic list view is a rectangle on the screen rather than a place the keyboard can be
		        put, and focusing the row is what clicking one does.
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

		The same attribute a browse mode document puts there, so that
		`flowTableSource.declaredHeader` needs to know nothing about where a table came from.
		A list view's headers are declared in exactly the sense that matters: NVDA asks the
		header control for them rather than guessing at a first row.

		**A real `ControlField`, and not a dictionary that looks like one.** NVDA's
		`FieldCommand` checks the type — `"controlStart" and not isinstance(field,
		ControlField)` raises — so a plain dictionary made this raise *every single time*, and
		the caller, which treats a cell that cannot answer as a cell that declares nothing,
		wrote a debug line and moved on. Every object table therefore declared no headers at
		all: no header row was ever pinned over one, and the paging command named the columns
		after the reader's first row and then by number. The cells had the headings the whole
		time and the report said so, which is what made it look like a reading problem.

		A `ControlField` is a `dict` subclass, so this is the same mapping with the type NVDA
		asked for.
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

	Presented rather than implemented in NVDA's own class hierarchy, because what is wanted
	is the three methods and not the scripts, the text container or the browse mode
	behaviour that `DocumentWithTableNavigation` also brings. Those would be a claim to be a
	document, and a list view is not one.

	Everything about *finding* the rows is here; everything about reading a cell out of one is
	in a subclass, because that is the only thing the two shapes of table object disagree
	about. See the module docstring.
	"""

	hasHeaderRow = False
	"""Whether the first row of this table is its headings.

	False, and that is the difference between a list and a table written down as text. A web
	page's table puts its headings in a row of itself and `flowTableSource.HEADER_ROW` falls
	back to reading them there. A list view's first item is a file: pinning it would draw one
	of the reader's own rows above the rest and call it a heading. Where a list has headings
	they are the ones its cells declare, and where it declares none there is nothing to pin.
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
		""":return: how many rows the table has.

		Three answers, and **the row's own comes first**. How many items it says it is one of
		is `positionInfo` and is what NVDA speaks as "fifty-two of seventy-nine"; NVDA fills it
		in from the selection container, which is the whole list rather than the part of it
		that has been built.

		The table's `rowCount` after that, and its `childCount` last. Neither is safe on its
		own for a list the platform builds a few items at a time: File Explorer's file list
		answered **fourteen** to `rowCount` and seventeen to `childCount` while the reader
		stood on item fifty-two of seventy-nine, and the band showed one row and said the table
		had ended. `rowCount` was tried first before that report and it is what the report
		disproved.

		Never fewer than the row the reader is standing on, whichever answered. A table cannot
		have fewer rows than the row somebody is in, and a count that says otherwise is
		counting something else.
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

		`positionInfo["similarItemsInGroup"]`, which for a UIA list item NVDA fills in from the
		selection container's item count — the whole list, not the part of it that has been
		built.

		Zero for a table whose rows are counted a group at a time, because then the number is
		the size of one group. See L{positionNumbersTheTable}.
		"""
		if item is None or not self.positionNumbersTheTable():
			return 0
		return _positionOf(item).get("similarItemsInGroup") or 0

	def positionNumbersTheTable(self) -> bool:
		""":return: whether `positionInfo` numbers this table's rows, rather than a group of them.

		**NVDA's `indexInGroup` is an index within a group**, and the whole table is only one
		of the things a group can be. It is the count for a flat list — a UIA list item's is
		filled in from the selection container, which is the list — and it is what NVDA speaks
		as "fifty-two of seventy-nine". In a list arranged in groups, which is File Explorer
		grouped by type and Outlook grouped by date, it restarts at one in every group. Read
		as a row number it would name two different rows the same, and read as a count it
		would end the table at the bottom of the group the reader happens to be in.

		Two things say so, and either is enough to refuse:

		**A level below the first.** NVDA reports a level for the controls that have a
		structure and for no others, so a row that says it is at level two is a row of a
		branch rather than of the table.

		**A table holding more rows than the group does.** A group is a part of the table, so a
		table admitting to more rows than the row says its group holds has something else in it
		as well. The test is one sided on purpose: a virtualised list admits to *fewer* children
		than it holds — File Explorer's answered fourteen while the reader stood on item
		fifty-two of seventy-nine — and that is the case this module was built for. Fewer is a
		list that has not been built.

		**Strict, and it was briefly not.** A list that admitted to one row more than its group
		holds was being read as flat, on the argument that an off-by-one is a control counting
		something unexpected rather than a shape. It was: File Explorer's file list counts a
		pane, a scrollbar and its column header among its children, and a folder of two files
		therefore admitted to five. But the fix for that belongs where the miscount is — see
		`childrenAdmittedTo`, which takes the decorations off — and buying it here cost the
		test its meaning: a review found a list of ten arranged in groups of six and four read
		as a flat table of six, so four rows of the reader's own list were behind an end that
		was not there. A count that disagrees is ambiguous, and an ambiguous table is refused
		rather than half drawn.

		Refusing means `rowNumberOf` has no answer, which is `_getTableCellCoords` raising and
		`tableAt` saying the reader is not in a table. That is the right outcome where the
		numbers really do count a group: the reader keeps NVDA's ordinary reading of the
		control instead of a layout confidently drawn from numbers that mean something else.

		**It is also the most expensive answer in the module**, since it is the whole feature
		disappearing from the control the reader asked about, and the reader is told only that
		they are not in a table. So the sign that produced it is written down. See
		L{whyItNumbersAGroup} and `describe`.
		"""
		return self.whyItNumbersAGroup() is None

	def whyItNumbersAGroup(self) -> Optional[str]:
		""":return: which sign said the numbers count a group, or None if none of them did.

		The reason as well as the verdict, because the verdict is invisible from outside: it
		reaches the reader as "not in a table", said about a control that plainly is one, and
		the numbers behind it are the only way to tell a grouped list from a table whose
		platform counts something this did not expect. See `describe`, which is where it goes.
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
			return (
				f"the table admits to {admitted} rows while the row says its group holds {among}"
			)
		return None

	def childrenAdmittedTo(self) -> int:
		""":return: the most rows this table admits to holding, or zero if it will not say.

		The larger of the two answers, because either may be the one that has been counted and
		neither is ever an overstatement. See L{positionNumbersTheTable}, which is the only
		caller and which reads it as a floor rather than as a measurement.

		**The decorations are taken off `childCount`**, since they are children and are not
		rows: a pane, a scrollbar and a column header count three, and a folder holding two
		files then admitted to five. Read as evidence of grouping that is room for another
		whole group, so the whole feature refused a small folder — while the same three
		children left a folder of eighty well clear of the test. A bug that appears below a
		threshold nobody chose is the kind this test exists to avoid.
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
		what an implementation with real coordinates fills in; `indexInGroup` is what a list
		item has when the platform numbers items rather than rows. Neither is worked out by
		counting, which on a list of ten thousand messages is the difference between a
		keypress and a pause.

		`indexInGroup` only where the group is the table, since otherwise it numbers the rows
		of one group and every group has a row one. See L{positionNumbersTheTable}.
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
			found = self.walkTo(row) or self.childAt(row - 1 + self.rowsBeginAt())
		self._rows[row] = found
		return found

	def childAt(self, index: int):
		""":return: one child of the table by its position, or None if it has not got it.

		The one-call way to a row, for a list that has built all its items. See `rowsBeginAt`
		for why the index is not the row number, and `looksLikeARow` for the check that
		catches a table whose decorations are not all at the front.

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

		**The children of a list are not all rows**, which the reader's own report showed and
		which reaching for `getChild(row - 1)` assumed away. Inside File Explorer's file list,
		in order: a pane, a horizontal scrollbar, the column header, and only then the files.
		Inside Outlook's message list the first child is a pane called "Vertical". So row one
		was a scrollbar and every row fetched by index was three files late.

		Counted once and remembered, by stepping over the front of the list until something
		that looks like a row turns up — a few children, not the list. Nothing that looks like
		a row within `MAX_DECORATIONS` means no offset rather than a guess at one: an index
		that is merely wrong is worse than an index that is plainly the caller's own.
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

		By the role of the row in hand, which is a row of this table that something else
		already decided was one — so this cannot disagree with `tableFor` about what a row of
		it is. With no row in hand, a child with cells is a row and the decorations around the
		list have none.
		"""
		template = self.focused
		if template is None:
			return bool(cellsOfARow(obj))
		return roleNameOf(obj) == roleNameOf(template)

	def walkTo(self, row: int):
		""":return: a row reached by stepping from one already in hand, or None.

		**Stepping rather than indexing, because a list may not have built its items yet.**
		File Explorer's file list has seventy-nine files and fourteen children: the platform
		makes the ones on screen and no more, so asking for the sixtieth child of it answers
		nothing at all. Stepping is what NVDA's own object navigation does and what this
		add-on's run-of-objects flow already does successfully in the very same list.

		Cheap in the case that happens: the window asks for the row after the last one it
		holds, so the walk is one step from something remembered.

		**What is stepped over is not always a row.** A list holds a pane, a scrollbar and its
		column header among its children, and a grouped one holds a heading between its groups;
		a review found row two coming back as the header that sat between rows one and two. So
		a sibling that is not a row is stepped over without counting: the rows are numbered by
		the rows, and the physical steps are bounded separately, because a stretch of things
		that are not rows must not turn a walk of five into a walk of a mailbox.

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

		Written because a report could not be read without it. A file list came back as
		fourteen rows by five columns with "Column left" pinned over a column whose every row
		said "Name", and settling *why* took a guess at which of four answers was the wrong
		one. The answers are all cheap to ask for, so they are asked for and written down.

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
			f", from {_positionOf(self.focused)}"
			+ (f", because {group}" if group else ""),
			f"  the table's own rowCount: {_ask(self.table, 'rowCount')}"
			f", childCount: {_ask(self.table, 'childCount')}"
			f", columnCount: {_ask(self.table, 'columnCount')}",
		]
		lines.extend(self.describeAroundTheTable())
		lines.extend(self.describeCells())
		return lines

	def describeAroundTheTable(self) -> list:
		""":return: the first few things inside the table and beside it, for the log.

		Where a header control would be if there is one. A list view's column headings are a
		control of their own — that is what the reader sees across the top of File Explorer's
		Details view — and where the cells will not name their columns it is the only place
		left to ask. Whether it is a child of the list or a sibling of it differs by
		application, so both are looked at, and only the first few of each: this is a report,
		and a report that walks a mailbox is a report nobody can afford to ask for.
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

		**The three places a header can come from, separately**, because "under header ''" was
		a report of a failure with no account of it: a reader paging File Explorer's Details
		view heard their own first row read back as the names of the columns, and which of the
		three had come back empty — the cell's name beside its value, or `columnHeaderText` —
		could not be told from the one line that said the answer was nothing. They are what
		`headerTextOf` asks and they are cheap, so they are written down beside what it made
		of them.
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

		**`getChild` is the public way in.** `RowWithoutCellObjects` makes a `_FakeTableCell`
		for each column, and that object answers `name`, `columnHeaderText` and `location` —
		the three questions this class asks — by calling the underscored methods itself. Asking
		it rather than calling those methods keeps this add-on off NVDA's private API, which
		the developer guide says outright is not one to depend on.

		The three are still called directly where a row makes no such cell, which is an
		application module implementing the contract on a class of its own rather than by
		inheriting `RowWithoutCellObjects` — the case `rowsTable` is deliberately written to
		accept. See L{contentOf}.

		The column number is checked rather than assumed, since `getChild` is a question every
		object answers and only this kind of row answers it with a cell.
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

		A list view keeps columns the reader has hidden, and reports them in its column count;
		what says they are hidden is that their rectangle has no width. `sysListView32` skips
		them on exactly that test when it builds an item's name, and this is the same test in
		the same place — which is better than the browse mode source's, where a column with no
		width has to be *inferred* from every cell in a sample being empty.

		The cell's own `location` where there is a cell, which is NVDA's `_FakeTableCell`
		asking the row the same question and swallowing a row that has no answer.

		A row that cannot answer is believed rather than doubted: a column is showing unless
		something says otherwise.
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
		# The row as well as the cell: which of the two a routing key can put the focus on
		# differs between the two applications this was built for. See `updateCaret`.
		return ObjectCellInfo(cellText(cell, header), target=cell, header=header, row=item)

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

	**A cell that carries both a name and a value has named its own column**, and that is a
	first-hand answer: the name is the label and the value is the data, which is what File
	Explorer's Details view cells are. Preferred over `columnHeaderText`, which is a
	*resolution* of whatever elements the platform points at as headers and can come back as
	something nobody would call a heading — the reader's log pinned "Column left" and
	"Position" over the Name and Status columns of a file list.

	`columnHeaderText` otherwise, which is NVDA's and for UIA resolves
	`TableItemColumnHeaderItemsPropertyId` to the header elements' text — the same work
	`appModules/outlook.py` does by hand when it builds a row's name for speech.

	Written as one line either way, since several header cells come back joined and a pinned
	header row is one row.
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

	**The value where there is one, and the name otherwise.** A table cell's name is its
	label and its value is its data, in every one of the APIs underneath: File Explorer's
	Details view cells are `explorer.UIProperty`, whose docstring says outright that they are
	"used for columns in Windows Explorer Details view", and one of them is named "Status"
	with the value "Always available on this device". NVDA speaks both, because on one line
	the label is what tells you what you are hearing. A column has already said that — it is
	the whole argument for laying a table out spatially — so the label is the half to drop.

	Where a cell has no value the name is all there is, which is Outlook's message list:
	`outlook.UIAGridRow`'s children are text elements and it is their names that it joins
	together to speak a whole message.

	:param header: unused, and kept because the two used to be told apart by comparing them.
		They are not: the reader's log had a header of "Column left" against a cell named
		"Name", so the comparison said "not a label" about a label and drew the column's own
		heading as its content on every row.
	"""
	name, value = nameAndValueOf(cell)
	if value and value != name:
		return value
	return name or value


def tableFor(obj) -> Optional[ObjectTable]:
	""":return: the object table the reader is in, or None if they are not in one.

	**Whatever the focus is on.** Where it lands differs by application and there is no
	arranging that: Outlook's message list focuses the row, File Explorer's file list focuses
	one property of the file. So this asks three questions in turn — is this a row whose cells
	it answers for, is it a cell, is it a row whose cells are objects — and the first that says
	yes decides both the row and the shape.

	**A thing that calls itself a table is never the row**, which is the reader's Outlook
	report and the one thing the properties could not settle. A message row carries
	`GridItemPattern`, so it reads as a cell of the list above it; that list carries the same
	properties on every row it holds, so it read as a row of cells in turn. The answer was a
	table made of the pane, whose one row was the whole message list — and a shape in which
	nothing could say which row the reader was on, so the command told them they were not in a
	table while they stood in the one they had asked about. The list says it is a table. Asking
	it, once, costs one property and settles it.

	:param obj: the object the reader is on.
	"""
	if obj is None:
		return None
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

	Written because the reader hears one sentence for four different answers. "Not in a
	table", said while standing in File Explorer's Details view, is a report nobody can act
	on: the object may have been refused as a row, refused as a cell, recognised and then
	unable to say which row of what it is, or recognised and laid out and dropped somewhere
	further up. Each of those is a different bug and none of them was written down.

	The questions `tableFor` asks, in the order it asks them, each with the answer it got,
	and then the stand-in's own account of the numbers where there is one to have. All of it
	is what the reading itself already asks for, so nothing here can be true of the report and
	false of the table.

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

	Stepped rather than read all at once, and stopped after `howMany`: what this is for is
	seeing what *kind* of thing is at the top of a container, and the container may be a list
	of ten thousand messages.
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

	Separate from L{explain} because the two callers need different halves of it: nothing
	navigating a table at all needs the whole account, and a stand-in that *was* built needs
	this half followed by its own numbers rather than a second copy of them.

	:param obj: the object the reader is on.
	"""
	lines = [f"  the object: {describeThing(obj)}"]
	if obj is None:
		return lines
	table = rowsTable(obj)
	lines.append(
		"  a row that answers for its own cells: "
		f"{describeThing(table) if table is not None else 'no'}",
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

	The lines the Outlook report needed and did not have. It showed a table built out of the
	pane and a row that was the whole message list, and settling *why* meant reasoning about
	which of two readings the properties allowed — when what decides it is one level of the
	tree and the column each child claims, both of which are cheap to write down.

	:param what: how to name this object in the line.
	:param obj: the object to look inside.
	"""
	if obj is None:
		return []
	children = _childrenOf(obj)
	columns = [columnNumberOf(child) for child in children[:MIN_COLUMNS * 4]]
	return [
		f"  {what}: {describeThing(obj)}"
		f", holds {len(children) or 'nothing this can read as cells'}"
		f", its own columnCount {_ask(obj, 'columnCount')}"
		f", a container of rows: {'yes' if isAContainerOfRows(obj) else 'no'}",
		f"  the columns its first children claim: {columns}"
		f", so cells of a row: {'yes' if cellsOfARow(obj) else 'no'}",
	]
