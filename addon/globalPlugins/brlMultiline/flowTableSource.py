# BrlMultiline: reading a table as rows of cells.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Blocks that are table rows, read cell by cell out of a browse mode document.

Everything about finding the cells is NVDA's. `documentBase.DocumentWithTableNavigation`
already knows how to say which cell a position is in, how big the table is, and how to fetch
the cell at any coordinate — it is what NVDA's own control+alt+arrows move through, and what
the Easy Table Navigator add-on binds the plain arrow keys to. This module walks that API and
turns its answers into blocks. It reimplements none of it, and the one place it was tempted
to — deriving the table's shape by reading fields itself — is exactly what `_getTableDimensions`
already does better.

**A block is a row.** Not a cell: a cell on its own is what a single line display shows and
what the reader is trying to get away from. Not the whole table either, because a table is
longer than any band and the window's whole design is a stream of blocks. One row, holding
its cells, drawn at the column plan's offsets by `FlowRenderer._asColumns`.

**A cell's braille is its text.** The cells are read as plain text regions rather than as
text-info regions over the document. That is deliberate and it is the difference between a
cell and a line: a text-info region reads a *reading unit* around a position, so a cell in a
one-line row would come back as the whole row, and a cell in a wrapped row as whatever
fraction of it the unit happened to cover. What a column holds is the cell's own text, so the
cell's own text is what is translated.

Routing keeps the connection to the document that plain text would lose: a routing key over a
cell puts the caret in that cell, which is where the reader wants to be if they are about to
do anything with it.

**A row that is missing a cell is normal.** `_getTableCellAt` raises `LookupError` for a
coordinate with no cell, which is what a merged cell looks like from the outside and what a
half-built row looks like from anywhere. The row is built without it, that column is left
blank, and every other column stays where the reader's finger left it.
"""

import dataclasses
from typing import Any, Optional

import documentBase
from braille.regions.base import TextRegion
from logHandler import log

from .flow import BlockId, FetchResult, SourceBlock
from .flowSources import FetchBudget
from .flowTable import Measurement, RowCell, TableRow

UNIT = "row"
"""What a block is here, for the log and for the dry run's report."""

MEASURE_ROWS = 8
"""How many rows are read to decide the column widths.

A band's worth. The same principle the indent plan uses — plan from what the reader is about
to feel — and the same reason not to read more: a table of four thousand rows would cost four
thousand reads to answer a question a bandful answers well enough. Where it is wrong it is
wrong by a cell or two and truncation covers it, which is what truncation is for.
"""


def tableDocumentFor(obj) -> Optional[Any]:
	"""What can navigate the table this object is in.

	The tree interceptor where there is one and browse mode is presenting the page, and the
	object itself otherwise. Taken from Easy Table Navigator, which uses the same dispatch for
	the same reason: it is what makes Excel and a list view the same code as a web page when
	that milestone comes, because both are `DocumentWithTableNavigation` too.

	:param obj: the object the reader is on.
	:return: the document, or None if nothing here navigates tables.
	"""
	if obj is None:
		return None
	try:
		interceptor = getattr(obj, "treeInterceptor", None)
		if isinstance(interceptor, documentBase.DocumentWithTableNavigation):
			# `passThrough` is browse mode standing aside for a control the reader is working
			# in. The page's table navigation is not what is wanted while they are typing in a
			# text box inside one of its cells.
			return None if getattr(interceptor, "passThrough", False) else interceptor
		if isinstance(obj, documentBase.DocumentWithTableNavigation):
			return obj
	except Exception:
		log.debugWarning("Could not tell whether this document navigates tables", exc_info=True)
	return None


@dataclasses.dataclass(frozen=True)
class TableHandle:
	"""A table the reader is in, and where in it they are."""

	document: Any
	"""What navigates it. See `tableDocumentFor`."""

	tableID: Any
	"""NVDA's identifier for this table within its document."""

	numRows: int
	numCols: int
	row: int
	"""The row the caret is in, one based, as the table numbers it."""

	col: int
	"""The column the caret is in, one based."""

	def __repr__(self) -> str:
		return f"<TableHandle {self.numRows}x{self.numCols} at row {self.row} column {self.col}>"


def tableAt(obj) -> Optional[TableHandle]:
	"""Whether the reader is in a table, and which one.

	The test Easy Table Navigator uses, for the reason it uses it: `_getTableCellCoords`
	answers the question by doing the work, so a test that agreed with it would be a second
	implementation to keep in step and a test that disagreed would offer a layout the fetches
	then could not fill.

	**A layout table answers no, and that is NVDA's doing rather than ours.**
	`_getTableCellCoords` consults `_maybeGetLayoutTableIds` and skips any table marked as
	layout unless the reader has turned NVDA's own "include layout tables" setting on. Most
	tables on a web page are page scaffolding, and this is what stops a column layout being
	aimed at one by accident.

	:param obj: the object the reader is on.
	:return: the table, or None if they are not in one.
	"""
	document = tableDocumentFor(obj)
	if document is None:
		return None
	try:
		where = document.selection
		cell = document._getTableCellCoords(where)
		numRows, numCols = document._getTableDimensions(where)
	except (LookupError, OSError):
		# Not in a table, or the document could not be asked. `OSError` covers the
		# `WindowsError` that a document going away underneath the question raises, which
		# Easy Table Navigator catches for the same reason.
		return None
	except Exception:
		log.debugWarning("Could not read the table at the cursor", exc_info=True)
		return None
	if numRows < 1 or numCols < 1:
		return None
	return TableHandle(
		document=document,
		tableID=cell.tableID,
		numRows=numRows,
		numCols=numCols,
		row=cell.row,
		col=cell.col,
	)


class TableCellRegion(TextRegion):
	"""One cell of a table, translated as the text it holds.

	A `TextRegion` rather than a `TextInfoRegion`, which is the difference between a cell and
	a line — see the module docstring. What it adds to a plain one is the way back: it keeps
	the cell's position, so a routing key over the cell can put the caret in it.
	"""

	def __init__(self, document, info, live: bool = False) -> None:
		"""
		:param document: what navigates the table, for moving the caret.
		:param info: the cell's position, as `_getTableCellAt` returned it.
		:param live: whether this flow moves the reader's own cursor. A viewer routes
			nowhere, exactly as it reads nothing else into the document.
		"""
		super().__init__(_textOf(info))
		self.document = document
		self.info = info
		self.live = live
		self.obj = document

	def routeTo(self, braillePos: int) -> None:
		"""Put the caret in this cell.

		In the cell rather than at the character the finger landed on. A table cell is a place
		in the document and its text is a rendering of what is there; the offsets inside it are
		this region's, not the document's, and pretending otherwise would put the caret
		somewhere arbitrary in a merged or reformatted cell. What the reader means by routing
		into a column is "take me to this value".

		:param braillePos: where in this region's cells the finger landed. Not used, for the
			reason above; taken so that this is the region interface everything else expects.
		"""
		if not self.live:
			return
		try:
			where = self.info.copy()
			where.collapse()
			where.updateCaret()
		except Exception:
			log.debugWarning("Could not put the caret in a table cell", exc_info=True)

	def __repr__(self) -> str:
		return f"<TableCellRegion {self.rawText!r}>"


def _textOf(info) -> str:
	""":return: a cell's text, stripped of the whitespace the document pads it with.

	Stripped because a cell's leading and trailing space is layout rather than content, and a
	column that spends two of its six cells on the space a page put around a ticker is a
	column two cells narrower than it looks.
	"""
	try:
		return (info.text or "").strip()
	except Exception:
		log.debugWarning("Could not read a table cell", exc_info=True)
		return ""


def cellRegion(handle: TableHandle, row: int, column: int, live: bool = False):
	""":return: a region for one cell of a table, or None where there is no such cell.

	None is ordinary rather than exceptional: a merged cell occupies one coordinate and leaves
	the others empty, and `_getTableCellAt` says so by raising.
	"""
	try:
		info = handle.document._getTableCellAt(handle.tableID, handle.document.selection, row, column)
	except (LookupError, OSError):
		return None
	except Exception:
		log.debugWarning(f"Could not read row {row} column {column}", exc_info=True)
		return None
	if info is None:
		return None
	region = TableCellRegion(handle.document, info, live=live)
	region.update()
	return region


def measure(handle: TableHandle, live: bool = False, sample: int = MEASURE_ROWS) -> list[Measurement]:
	"""Read a bandful of rows and find out how wide each column needs to be.

	In cells, by translating, because that is the only honest measurement — see `flowTable`.
	The sample starts at the caret's own row and reads forward, so that what the widths are
	decided from is what the reader is about to feel, and the header row is read whatever the
	caret's row is because it is the widest thing in many a column and it is what a pinned
	header will have to fit.

	:param handle: the table.
	:param live: whether the regions built here move the reader's cursor. They do not, and
		they are thrown away; the parameter is here so that measuring cannot quietly differ
		from reading.
	:param sample: how many rows to read.
	:return: one measurement per column of the table.
	"""
	widths: dict[int, int] = {column: 0 for column in range(1, handle.numCols + 1)}
	labels: dict[int, str] = {column: "" for column in range(1, handle.numCols + 1)}
	rows = _sampleRows(handle, sample)
	for row in rows:
		for column in range(1, handle.numCols + 1):
			region = cellRegion(handle, row, column, live=live)
			if region is None:
				continue
			widths[column] = max(widths[column], len(region.brailleCells))
			if row == 1 and not labels[column]:
				labels[column] = region.rawText
	return [
		Measurement(index=column, width=widths[column], label=labels[column])
		for column in range(1, handle.numCols + 1)
	]


def _sampleRows(handle: TableHandle, sample: int) -> list[int]:
	""":return: which rows to measure: the header, then a bandful from the caret."""
	first = max(1, min(handle.row, max(1, handle.numRows)))
	rows = list(range(first, min(handle.numRows, first + sample - 1) + 1))
	if 1 not in rows:
		rows.insert(0, 1)
	return rows


class TableFlowSource:
	"""Blocks read out of a table, one row at a time.

	The same shape as every other source — `blockAtCursor`, `blockAfter`, `blockBefore`, a
	budget — so the window, the controller and the band do not know a table from a document.
	"""

	def __init__(
		self,
		handle: TableHandle,
		columns: tuple,
		generation: int = 0,
		budget: Optional[FetchBudget] = None,
		live: bool = False,
	) -> None:
		"""
		:param handle: the table, and where in it the reader was when it was recognised.
		:param columns: the table's own numbers for the columns to read, in drawing order.
			Only these are fetched: reading a column the plan has dropped is a call into the
			document for something nobody will feel.
		:param generation: bumped by the caller when the run is replaced, so that a row
			number from one table can never match one from another.
		:param budget: how much work a fetch may do. One is made if none is given.
		:param live: whether this flow is the reader's own.
		"""
		self.handle = handle
		self.columns = tuple(columns)
		self.generation = generation
		self.budget = budget if budget is not None else FetchBudget()
		self.live = live
		self.unit = UNIT
		self.obj = handle.document

	@property
	def row(self) -> int:
		""":return: the row the reader is on, as this source last knew it."""
		return self.handle.row

	def setCurrent(self, obj) -> None:
		"""Say where the reader has moved to.

		Taken from the document rather than from the object, because in a table the object
		does not move: the caret moves between cells of the same page, and asking the document
		where its selection is now is the only thing that answers.
		"""
		found = tableAt(obj if obj is not None else self.obj)
		if found is not None and _sameTable(found.tableID, self.handle.tableID):
			self.handle = found

	def isStillHere(self, obj) -> bool:
		""":return: whether the reader is still in the table this source is reading.

		What the band asks before drawing again. A layout must not outlive its table: Easy
		Table Navigator clears its key bindings on every focus change for the same reason, and
		a column layout carried onto the next page is worse than no layout, because it is a
		layout of something that is not there.
		"""
		found = tableAt(obj)
		return found is not None and _sameTable(found.tableID, self.handle.tableID)

	# Reading.

	def blockAtCursor(self, atObject=None) -> FetchResult:
		""":return: the row the reader is on."""
		self.budget.startUnlessActive()
		return self._rowAt(self.handle.row)

	def blockAfter(self, blockId: BlockId) -> FetchResult:
		""":return: the row below one already fetched."""
		return self._step(blockId, 1)

	def blockBefore(self, blockId: BlockId) -> FetchResult:
		""":return: the row above one already fetched."""
		return self._step(blockId, -1)

	def _step(self, blockId: BlockId, by: int) -> FetchResult:
		"""Walk one row up or down.

		Bounded by the table's own dimensions rather than by trying and failing: a table knows
		how many rows it has, and walking off the end to find out costs a search of the whole
		buffer per column to learn something already known.
		"""
		self.budget.startUnlessActive()
		row = blockId.bookmark + by
		if not 1 <= row <= self.handle.numRows:
			return FetchResult.endOfStream()
		return self._rowAt(row)

	def _rowAt(self, row: int) -> FetchResult:
		""":return: a result carrying one row of the table."""
		began = self.budget.clock()
		try:
			return FetchResult.found(self._buildRow(row))
		except Exception as error:
			log.debugWarning(f"Could not read row {row}", exc_info=True)
			return FetchResult.failed(f"could not read a table row: {error!r}")
		finally:
			self.budget.observe(self.budget.clock() - began)

	def _buildRow(self, row: int) -> SourceBlock:
		"""Build one block from one row of the table.

		The row number is the bookmark, which is what makes a table cheap to walk: rows are
		numbered by the table, the numbering survives a re-read, and it is what
		`_getTableCellAt` takes. Nothing has to be remembered between fetches.
		"""
		cells = []
		for column in self.columns:
			region = cellRegion(self.handle, row, column, live=self.live)
			if region is not None:
				cells.append(RowCell(index=column, region=region))
		content = TableRow(cells)
		return SourceBlock(
			blockId=BlockId(generation=self.generation, bookmark=row, unit=self.unit),
			region=content,
			isBlank=not content.rawText.strip(),
		)

	def forget(self) -> None:
		"""Kept for the shape of a source. There is nothing cached here to drop."""

	def __repr__(self) -> str:
		return f"<TableFlowSource {self.handle!r} generation {self.generation}>"


def _sameTable(first, second) -> bool:
	""":return: whether two table identifiers name the same table.

	Compared rather than tested for identity: NVDA's table identifier is an integer for a
	virtual buffer and a tuple for UIA, and neither survives being the same object across two
	reads.
	"""
	try:
		return bool(first == second)
	except Exception:
		log.debugWarning("Could not compare two tables", exc_info=True)
		return False


__all__ = [
	"MEASURE_ROWS",
	"TableCellRegion",
	"TableFlowSource",
	"TableHandle",
	"cellRegion",
	"measure",
	"tableAt",
	"tableDocumentFor",
]
