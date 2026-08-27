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

	Two ways to be sure, and a row needs one of them. **Its children say which column they are
	in**, which is `GridItemPattern` for UIA and the table cell interface for IAccessible2.
	Or **the table it is in says how many columns it has**, and the row has that many children
	to fill them.

	The second was added for Outlook's message list. Its rows are `outlook.UIAGridRow` — a
	`RowWithFakeNavigation`, whose contract says outright that "the cells must be exposed as
	children" — and the row itself carries `GridItemPattern`, but its children are plain text
	elements that carry nothing. Asking only the children said "not a table" about a table
	whose own row had just said it was one.

	Counting rather than trusting the first child either way: a tree view item has children
	too, and they are items rather than cells.

	:param obj: the object the reader is on.
	"""
	if obj is None:
		return []
	try:
		children = list(obj.children or [])
	except Exception:
		log.debugWarning("Could not read an object's children", exc_info=True)
		return []
	if len(children) < MIN_COLUMNS:
		return []
	numbered = [child for child in children if columnNumberOf(child) is not None]
	if len(numbered) >= MIN_COLUMNS:
		return children
	return children if _columnsOfTheTableAbove(obj) >= MIN_COLUMNS else []


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
		try:
			if focus is not None:
				focus.setFocus()
		except Exception:
			log.debugWarning("Could not go to a table cell", exc_info=True)
		if focus is target:
			return
		try:
			import api

			api.setNavigatorObject(target)
		except Exception:
			log.debugWarning("Could not take the navigator object to a table cell", exc_info=True)

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

		**A table holding more than the group does.** A group inside a table is smaller than
		the table, so a table admitting to more children than the row says its group holds has
		more than one group in it. The test is one sided on purpose: a virtualised list admits
		to *fewer* children than it holds — File Explorer's answered fourteen while the reader
		stood on item fifty-two of seventy-nine — and that is the case this module was built
		for. Fewer is a list that has not been built; more is a list that has been grouped.

		Refusing means `rowNumberOf` has no answer, which is `_getTableCellCoords` raising and
		`tableAt` saying the reader is not in a table. That is the right outcome: they keep
		NVDA's ordinary reading of the control instead of a layout confidently drawn from
		numbers that mean something else.
		"""
		where = _positionOf(self.focused)
		if not where.get("indexInGroup"):
			return False
		if (where.get("level") or 0) > 1:
			return False
		among = where.get("similarItemsInGroup") or 0
		return not (among and self.childrenAdmittedTo() > among)

	def childrenAdmittedTo(self) -> int:
		""":return: the most rows this table admits to holding, or zero if it will not say.

		The larger of the two answers, because either may be the one that has been counted and
		neither is ever an overstatement. See L{positionNumbersTheTable}, which is the only
		caller and which reads it as a floor rather than as a measurement.
		"""
		found = 0
		for name in ("rowCount", "childCount"):
			try:
				count = getattr(self.table, name, None)
			except Exception:
				log.debugWarning(f"Could not read a table's {name}", exc_info=True)
				continue
			try:
				found = max(found, int(count or 0))
			except Exception:
				continue
		return found

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
			found = self.walkTo(row)
			if found is None:
				try:
					found = self.table.getChild(row - 1)
				except Exception:
					log.debugWarning(f"Could not reach row {row} of a table", exc_info=True)
					found = None
		self._rows[row] = found
		return found

	def walkTo(self, row: int):
		""":return: a row reached by stepping from one already in hand, or None.

		**Stepping rather than indexing, because a list may not have built its items yet.**
		File Explorer's file list has seventy-nine files and fourteen children: the platform
		makes the ones on screen and no more, so asking for the sixtieth child of it answers
		nothing at all. Stepping is what NVDA's own object navigation does and what this
		add-on's run-of-objects flow already does successfully in the very same list.

		Cheap in the case that happens: the window asks for the row after the last one it
		holds, so the walk is one step from something remembered.

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
		for number in range(steps):
			try:
				item = item.next if forward else item.previous
			except Exception:
				log.debugWarning("Could not step to the next row of a table", exc_info=True)
				return None
			if item is None:
				return None
			# Remembered on the way past, so a walk of five rows is five steps rather than
			# five walks. The window asks for them in order, which is what makes this pay.
			self._rows[from_ + (number + 1 if forward else -(number + 1))] = item
		return item

	def describe(self) -> list:
		"""What this stand-in found, for the log.

		Written because a report could not be read without it. A file list came back as
		fourteen rows by five columns with "Column left" pinned over a column whose every row
		said "Name", and settling *why* took a guess at which of four answers was the wrong
		one. The answers are all cheap to ask for, so they are asked for and written down.

		:return: one line per thing worth knowing, no line of it needed to draw anything.
		"""
		lines = [
			f"  shape: {self.numRows} rows by {self.numCols} columns, as {self!r}",
			f"  table object: {describeThing(self.table)}",
			f"  row in hand: {describeThing(self.focused)} at row {self.rowNumberOf(self.focused)}",
			f"  the row says it is one of {self.itemsAmong(self.focused) or 'it does not say'}",
			f"  its position numbers {'the table' if self.positionNumbersTheTable() else 'a group of it'}"
			f", from {_positionOf(self.focused)}",
			f"  the table's own rowCount: {_ask(self.table, 'rowCount')}"
			f", childCount: {_ask(self.table, 'childCount')}"
			f", columnCount: {_ask(self.table, 'columnCount')}",
		]
		lines.extend(self.describeCells())
		return lines

	def describeCells(self) -> list:
		""":return: what each column of the row in hand holds, and where it came from."""
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
		return found or ["  the row in hand has no columns"]

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
