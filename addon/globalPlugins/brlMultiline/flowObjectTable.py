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

from .flow import CallCancelled

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

	def __init__(self, text: str, target=None, header: str = "", row=None, fetch=None) -> None:
		"""
		:param text: the cell's content.
		:param target: the object the reader means when they route into this cell. The cell
			itself where cells are objects, and the row where they are not — see
			L{updateCaret}.
		:param header: what the table says this column's header is, or "" if it says nothing.
		:param row: the row the cell is in, which is the thing that can take the focus when
			the cell cannot. The target itself when there is nothing else.
		:param fetch: how to get the object, for a cell whose text arrived without one.

			**Because reading and going to are different questions.** A grid can hand over a
			row's text in one call; building the object for each cell of it costs a coordinate
			lookup and an `NVDAObject` with its overlay classes chosen, and on a worksheet a
			bandful of that is what stopped the display. Nothing needs the object until a
			routing key lands on the cell, which is one cell, once, at a moment the reader is
			waiting for something to happen anyway.
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

	@property
	def contentRows(self) -> int:
		""":return: how many rows this table has content in, for noticing that it changed.

		The row count itself, for everything but a grid: a list has as many rows as it has, and
		nothing stretches it. See `SheetTable.contentRows`, and `rowCountIsExact`, which is
		what decides whether the number is worth comparing at all.
		"""
		return self.numRows

	rowCountIsExact = False
	"""Whether this table's count of its rows is a fact rather than what has been built so far.

	False for a list, because it is not: File Explorer's file list answered fourteen to
	`rowCount` and seventeen to `childCount` while the reader stood on item fifty two of
	seventy nine, and a list that grows as the platform builds it is not a list that changed.
	Nothing can be concluded from the number moving.

	True for a grid, which knows how far it is used and says so in one call. There the number
	moving means the sheet gained or lost rows — a formula filling down, an external query
	refreshing — and a stream that keeps the old count cannot be panned into the new rows at
	all. See `flowBand.FlowBand._tableChangedShape`.
	"""

	emptyColumnsArePlaces = False
	"""Whether a column of this table that holds nothing is still somewhere the reader goes.

	False for a list, and that is what keeps a watchlist's column of unreadable icons off the
	band: there is a cell in it on every row, it is simply empty, and four cells of a thirty
	two cell band spent on it are four the reader never gets back. A column of a list is the
	application's to show, and one showing nothing is not something they are missing.

	True for a grid, where it is the opposite. A spreadsheet is not a table with a fixed set
	of columns; it is a plane, and the column beside the data is where the reader goes to
	write the next one. Left out for holding nothing, it took their cursor with it — the row
	on the band has no cell there, so there is nothing to draw a cursor in and nothing for a
	routing key to reach — and a blank sheet with the active cell at D20 could not be laid
	out at all. Only the column they are *in* is kept, so a sheet twenty one columns wide
	still spends nothing on the empty ones they are not.

	See `flowTableSource.measure`, which is the only thing that reads this.
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


ROW_SEPARATOR = ", "
"""What joins one cell of a row to the next when the row is read as a single line.

A comma and a space, which is what `appModules/outlook.py` joins a message row's fields with
and what a reader of that list is used to hearing.
"""


def rowTextOf(row, withHeaders: Optional[bool] = None, headers: Optional[dict] = None) -> str:
	""":return: what a row says about *itself*, built from its own cells, or "" if it has none.

	**The same cell reader the spatial layout uses, serving a row that is one line.** Nothing
	here is new: `_childrenOf` finds the cells, `cellText` takes each one's value or name, and
	`headerTextOf` names its column, exactly as they do when the same list is laid out in
	columns. What is new is joining them, because a run of objects shows a row as one line and
	until now that line could only be the row's own `name`.

	**Which matters because a name is not always about the row.** NVDA's Outlook module builds
	a message row's name partly from `activeExplorer().selection` — the unread flag, the
	executed verb, the attachment flag, the importance — so a row read while another message is
	selected carries *that* message's flags. On hardware that put "replied" and "forwarded" on
	messages that were neither, which is not merely wrong but alarming: a reader cannot tell it
	from the truth. A row's cells are the row's own, whatever is selected.

	**What it costs, and what it does not give.** One call into the application for the
	children and a read per cell, bounded by `MAX_ROW_CELLS`, against one property read for the
	name. And a state that lives nowhere but the object model — "unread" is not a cell — cannot
	be built out of cells at all: that is why the row the reader is *on* keeps NVDA's own name,
	where the selection and the row are the same message and every word of it is true. See
	`flowObjects.namedFromTheSelection`.

	It may also be more verbose than NVDA. NVDA leaves out an unflagged flag column by asking
	the selection whether it is flagged — the very question this refuses to trust — so a column
	that names itself on every row is kept here. A cell that only repeats its own header is
	dropped, which is the part of that noise this can be sure about.

	:param row: the row object.
	:param withHeaders: whether to put each column's name before its value, or None to follow
		NVDA's own table header setting, which is what its Outlook module follows.
	:param headers: a place to remember what each column is called, so that a band of eight
		rows asks the platform once per column rather than once per cell. A header belongs to
		the column and not to the row, so this is a cache of a fact rather than a guess. Pass
		the same dictionary for every row of one list; None asks afresh each time.
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

	An icon column with nothing in it comes back named after itself, and drawing that on every
	row says "Flag" beside every message. **The test is the value, not the text.** A review
	found the first cut of this dropping real data: a file named "Name" in the Name column has
	a value that happens to equal its header, and it went off the display. A cell with a value
	has said something; only a cell with no value at all, whose name merely repeats its column,
	is the label being drawn as content.

	:param cell: the cell.
	:param header: what its column is called, already resolved.
	"""
	if not header:
		return False
	name, value = nameAndValueOf(cell)
	return not value and name == header


def _headerFor(cell, index: int, headers: Optional[dict]) -> str:
	""":return: what a cell's column is called, asking the platform once per column.

	Resolved whether or not the headers are being drawn, because it decides more than
	presentation: a cell that holds nothing but its own column's name is left out, and that
	cannot be told without the name. One fetch per column of a list, cached, either way.

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

More than one, because the cell that happens to be read first is not always a witness. Three
kinds of bad witness have been met: a half-built row of a virtualised list, a merged cell
where its neighbours hold real ones, and — the one that took an Excel worksheet's header row
off the display — a cell *in the header row itself*, which has no header above it and says so
for every column at once when that is where the reader was standing.

Few, because a table that declares nothing pays this on every column, and on a spreadsheet a
cell's header is resolved by walking the worksheet's marked ranges.
"""

SHEET = "brlMultilineSheet"
"""What an object offers when it can hand over a grid to be read by coordinate.

**The seam an application module joins the flow at**, and the whole of what the add-on knows
about any particular application. An object that answers to this name is asked once, by
`sheetOf`, and what comes back answers `Sheet`'s questions. Nothing here imports an
application's module, reads its object model, or knows that Excel exists; the code that does
lives in `appModules/excel.py` and is loaded only while Excel is running.

The same seam now serves the graphics side. A chart is drawn from `selectedValues`, which is
one more question asked of the same object — so charting a spreadsheet selection needed no
second seam, and a second application that offers one gets charts for free.

Asked as one `getattr` of every object the band meets, which is what makes it affordable to
ask at all — an object that does not answer costs one attribute lookup that fails.
"""


class Sheet:
	"""What an application module hands over: a grid addressed by coordinate.

	Not a class to inherit — it is here to say what the four names mean, and an adapter that
	answers them will do. A spreadsheet is the shape this exists for and it is the third shape
	of table object, different from the other two in the way that matters most:

	- A **row whose cells are objects** is walked, child by child (`CellObjectTable`).
	- A **row that answers for its own cells** is asked, column by column (`RowCellTable`).
	- A **sheet** is neither. There is no row object at all — a spreadsheet's rows are not
	  children of anything — and a cell is reached by saying which one you want. That is
	  exactly the question `_getTableCellAt` asks, so this shape is the closest of the three
	  to what the rest of the add-on already wanted, and the smallest.

	It is also the one place NVDA has no generic answer. Every other question a table is asked
	— what a cell says, which row and column it is, what its column's header is — is already
	on `NVDAObject`, implemented once per accessibility API and tuned per application, and is
	asked of the object exactly as it is everywhere else in this module. *The cell at (row,
	column)* is the one NVDA does not offer outside `DocumentWithTableNavigation`, and
	supplying it, per application, is the whole of what an adapter is for.
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

		An object, not a string, because everything the flow then asks of it — its text, its
		column's header, where the caret goes when a routing key lands on it — is a question
		NVDA already answers about an object.
		"""
		raise NotImplementedError

	# The two below are optional. An adapter that does not offer them is asked cell by cell
	# instead and answers everything above; they exist because *measuring* asks a different
	# question from *reading*, and a grid can often answer it far more cheaply.

	def usedShape(self) -> Optional[tuple]:
		""":return: how far this grid is written in, as (rows, columns), or None if it cannot say.

		Optional, and told apart from `shape` on purpose: `shape` reaches at least as far as
		the reader, so that the column and row they are standing in are part of the table. It
		therefore changes when they move about the empty part of a sheet, and something has to
		notice a table that has actually *grown* — a formula filling down, a query refreshing —
		without mistaking their arrow keys for it.
		"""
		return None

	def selectedHeadings(self) -> Optional[bool]:
		""":return: whether the first row of the last selection read is a row of headings.

		Optional, and the answer is a fact about the application rather than about the numbers.
		Nothing in a grid of values can settle it: a table headed "Metric, 2025, 2026" over
		"Sales, 10, 20" is, cell for cell, indistinguishable from a table of years and figures
		with no headings at all, and a reader charting one of them gets 2025 and 2026 drawn as
		data points. A spreadsheet knows, because a structured table declares its header row.

		True means the first row names the columns, False means it does not, and None means the
		grid cannot say — in which case whoever asked falls back to guessing from the values,
		which is what it did before this existed. Asked after `selectedValues` and about the
		same cells, since that is what clipped the selection to the used range.
		"""
		return None

	def whereIsIt(self) -> Optional[str]:
		""":return: what names the place this grid is in, or None to be named the ordinary way.

		Optional, and for a grid that is neither a web page nor one control of an application.
		A layout the reader saves is remembered against where the table is and what its
		columns are called — see `flowTableIdentity` — and for a worksheet the ordinary answer
		to *where* is the application and the window class, which is "excel" and "EXCEL7" for
		every sheet of every workbook they will ever open. Two workbooks with the same
		headings were then each other's saved layout.

		What is answered here is written down as a digest and never in full, so a file path is
		a reasonable thing to give. See `flowTableLayouts.keyFor`.
		"""
		return None

	def textRow(self, row: int, first: int, last: int):
		""":return: the text of one row across a span of columns, or None to be asked cell by cell.

		**Optional, and the difference between a readable sheet and a stopped display.**
		Measuring reads a bandful of rows across every column and throws all of it away except
		the widths — see `flowTableSource.measure` — so it wants text and nothing else. Asked
		through `cellAt`, one cell of an Excel sheet costs a coordinate lookup, an
		`NVDAObject` built with its overlay classes chosen, and a cross-process fetch of that
		cell's text, address, states, comments and formula. Twenty-one columns of two rows
		came to ten seconds on hardware, which is past the watchdog's patience: the reads that
		followed were cancelled and the sheet then measured empty.

		A grid that can hand over a row's text in one call should. Excel can, through the same
		batch fetch NVDA's own quick navigation uses. Objects are still built for the cells
		that are drawn and routed into, which is where an object is needed.

		:param row: the row to read, one based.
		:param first: the first column of the span, one based.
		:param last: the last column of the span, inclusive.
		:return: one string per column of the span, or None where this cannot be answered
			cheaply — the caller then reads cell by cell exactly as before.
		"""
		return None

	def rowAfter(self, row: int, by: int):
		""":return: the next row the reader may be shown, or None when there is no next row.

		**Optional, and about rows the grid is hiding rather than rows it has not got.** A
		filtered worksheet still answers for every row of its used range: ask for row 40 of a
		sheet filtered down to nine rows and Excel hands over row 40's cells, so a band that
		walks by adding one to a row number reads straight out of what the reader filtered to
		and into what they filtered away. Which is what the reader met: panning off the end of
		a filtered block onto the rows either side of it.

		Only a grid can answer this — a list has no hidden items and a document's table has no
		filter — so it is optional, and a grid that does not offer it is walked by adding one
		exactly as before. A grid that offers it and cannot say *this time* should answer
		`row + by` rather than None: None ends the walk, and ending it on a failure to ask
		would cost the reader the rest of the sheet.

		:param row: the row walked from, one based.
		:param by: which way, as 1 or -1. One row at a time is all the walk ever asks for.
		"""
		return None

	def rowShowing(self, row: int):
		""":return: whether the grid is showing one row, or None where it cannot say.

		**The other half of `rowAfter`, and about rows the band is already holding.** Walking
		by `rowAfter` means no hidden row is ever fetched, and says nothing about the rows
		fetched before the reader hid them: those sit in the band's cache, answer every
		question put to them, and are drawn over the row the reader filtered down to. Asked of
		the rows on the band when the reader moves, which is when a filter changes under one.

		Optional for the same reason `rowAfter` is, and None means the same thing — "I cannot
		say" — which is read as showing, since a row nothing objects to is a row that stays.

		:param row: the row asked about, one based.
		"""
		return None

	def selectedValues(self, maxRows: int = 0, maxColumns: int = 0):
		"""What the reader has selected, as values rather than as text.

		**Optional, and the only question here that is not about reading.** A chart needs
		numbers, and numbers are the one thing the ordinary reading path deliberately does
		not give: `textRow` answers what is *displayed*, because a column sized from a stored
		value is a column sized for something the reader will never feel. A percentage shown
		as "25%" is stored as 0.25 — the right number to chart and the wrong string to size a
		column by — so the two answers are different questions and this is the second one.

		Both halves come back together because a chart needs both: the value decides how tall
		a bar is and the text decides what it is called. Splitting them into two calls would
		mean two trips across the process boundary for one answer, and would let them
		disagree if the selection moved between the two.

		A grid that has no notion of a selection, or cannot read one, answers None, and the
		reader is told charts are not available there rather than being given a wrong one.

		**A selection can be larger than anything worth reading**, and a grid that can be
		asked for one has to be told where to stop. Ctrl+Space in a spreadsheet selects a
		whole column — over a million cells — which is what a reader should press rather
		than arrowing down forty-eight rows, and reading it whole froze NVDA for ten
		seconds on hardware. So the caller says how much it can use, and a grid is
		expected to have a ceiling of its own besides: a limit the caller can forget to
		pass is not a limit.

		:param maxRows: rows the caller can use at most, or 0 to leave it to the grid.
		:param maxColumns: columns the caller can use at most, on the same terms.
		:return: rows of `(text, value)` per selected cell, where `value` is a number or
			None for a cell that holds no number; or None where this cannot be answered.
		"""
		return None

	def columnsShowing(self):
		""":return: the columns the grid is showing, or None where every column is showing.

		**The other axis of `rowAfter`, and the same fact about a grid.** A hidden column is
		still a column: ask a worksheet for column 5 with column 5 hidden and it hands over
		column 5's cells, so a layout built from every column the sheet has draws one the
		reader cannot arrow to, gives it a place in the count of columns, and puts its heading
		in the pinned row. Which is a column of the display spent on something that is not
		there.

		Only a grid can hide a column — a list's columns are what its items have, and a
		document's table has no such thing — so it is optional, and a grid that does not
		offer it has every column measured exactly as before. So does one that answers None,
		which is what "I cannot say" means here: there is no ambiguity to guard against,
		since a grid showing no columns at all is not a grid anybody is reading.

		:return: the column numbers on show, as a set or any collection `in` works on.
		"""
		return None

	def columnHeaders(self, first: int, last: int):
		""":return: what each column of a span declares as its header, or None to ask the cells.

		**Optional, and mostly a way to say "none of them do" without touching a cell.** A
		column's header is a property of the column, so one cell of it answers for the whole
		column — but a grid that declares no headers at all makes the flow build a cell per
		column to be told nothing, which on a spreadsheet is the entire cost of the answer.
		A worksheet knows outright whether a reader has marked any header row or column.

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

	Everything `ObjectTable` works out by walking is answered outright here, so most of what
	that class does is overridden away rather than reused: there is no run of children to
	count, no decorations to skip past, and no `positionInfo` to decide whether it numbers the
	table or a group of it. A spreadsheet says which row and column a cell is, and means it.

	A row number stands in for the row object, which is honest rather than a trick: the rest
	of this module holds "the row" only to ask it for cells, and here the coordinate is what
	the cells are asked for by.
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

		**None left a report unable to describe a single cell.** `describeCells` asks for the
		row in hand, and for every sheet it said "no row in hand, so there are no cells to
		describe" — so the one report written to explain why an Excel sheet would not lay out
		explained nothing, and the reason had to be reasoned out from the shape of the failure
		instead. Everything the base class does with this either takes a row number already or
		is overridden here."""

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

		**No cell is fetched for it**, and that halved the cost of reading a sheet. This is
		passed to `_getTableCellAt` as the position to read from, which for every shape here
		is ignored — the coordinates are the arguments beside it — and `_getTableCellCoords`
		answers from `row` and `column` without looking either. So building a cell to put in
		it meant every read of a cell built two: on a worksheet, two coordinate lookups and
		two `NVDAObject`s with their overlay classes chosen, for one value.
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

		A declared header belongs to the column, so on a spreadsheet `columnHeaderText` is
		resolved by walking the worksheet's marked ranges with the cell's coordinates in hand
		— real work, and repeated for every cell of every band if nothing remembers it.

		**An answer is remembered; a silence is only counted.** That is the difference from
		remembering both, and it is what took the header row off an Excel worksheet. The
		layout was made with the reader standing on row one, which is the row they had marked
		as the header — and a header cell has no header above it, so every column's *first*
		witness answered nothing. Remembered as the answer, that settled all five columns
		before a single data cell was asked, and the table read as one that named no column at
		all. Its own report said so on one line and listed the headings on the next.

		So a silence is worth asking again about, from another cell, `HEADER_TRIES` times over
		— the same bounded strategy `flowTableSource.declaredHeaders` uses one level up, and
		for exactly the same reason: the first cell read is not always a good witness. A column
		that has said nothing that many times is left alone, which is what keeps a table
		nobody has marked up from costing a read per cell for ever.

		:param column: the table's own number for the column.
		:param cell: a cell of it, to ask where the sheet will not say.
		"""
		if self._headers is None:
			said = self.columnHeaders(1, self.numCols)
			# **Three answers, not two.** A mapping is what the columns are called; an empty
			# mapping is "this sheet declares none", which is worth acting on because it
			# saves a cell built and a header search per column; None is "I cannot say",
			# which is not the same thing and must send this to the cells. Read as two, the
			# empty answer took a worksheet's header row off the display — see
			# `flowObjectTable.Sheet.columnHeaders`, which is where the checking now is.
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

		The seam `flowTableSource` walks by, handed straight to the sheet. See
		`Sheet.rowAfter`, which is where the reason for it is.

		**The next row along where the sheet offers no opinion**, and not None: this class
		always answers, since the walker cannot tell a table that has no view on hidden rows
		from one saying the sheet ends here — and read as the second, every grid that hides
		nothing stopped at the row the reader was on.
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

		**The number that only moves when the sheet does.** `numRows` reaches at least as far
		as the reader, so that the row they are standing in is part of the table — and read as
		a count of the table it moves every time they press an arrow key below the data. A
		review caught the layout being rebuilt on each of those: measured again, planned again,
		and the reader's page and panned rows put back, for a sheet nothing had happened to.
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
