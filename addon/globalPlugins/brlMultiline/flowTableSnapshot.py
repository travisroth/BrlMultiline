# BrlMultiline: a browse mode table's sampled rows, read in one go for measuring.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The text and declared header of every cell in a run of a table's rows, from one read.

NVDA's buffer for Gecko and Chromium puts a control field on every cell, carrying its table,
row, column, spans and declared header. One `getTextWithFields` over the sampled rows, walked in
Python, gives what `measure` would otherwise read one cell at a time. For measuring only: it
keeps no cell positions, so nothing is drawn or routed from it. See
docs/design/table-snapshot-plan.md.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any

from logHandler import log

from . import stageTimes
from .flow import CallCancelled
from .flowTableGecko import hasNvdaCellLookup

TOO_MUCH_TEXT = 200_000
"""The most characters one read may cover before the table is measured cell by cell instead.

Reading is cheap even near this; it guards against a range that holds a whole document, such
as a cell with a long article in it. See docs/design/table-snapshot-plan.md, Phase 0 results.
"""


@dataclasses.dataclass
class SnapshotCell:
	"""One cell of the table, where the buffer puts it: at its first row and column."""

	row: int
	column: int
	text: str
	header: str
	rowSpan: int = 1
	columnSpan: int = 1

	def covers(self, row: int, column: int) -> bool:
		""":return: whether this cell is the one at a position, its spans included."""
		return (
			self.row <= row < self.row + self.rowSpan
			and self.column <= column < self.column + self.columnSpan
		)


@dataclasses.dataclass
class Snapshot:
	"""The cells of a table's sampled rows."""

	cells: list[SnapshotCell] = dataclasses.field(default_factory=list)
	rowsRead: set[int] = dataclasses.field(default_factory=set)
	"""The rows the walk saw a cell of."""

	_index: dict | None = dataclasses.field(default=None, init=False, repr=False, compare=False)
	"""Each read position's cell, built on the first lookup. See `_positions`."""

	_indexedSize: tuple = dataclasses.field(default=(), init=False, repr=False, compare=False)

	def cellAt(self, row: int, column: int) -> SnapshotCell | None:
		""":return: the cell at a position, a merged cell at every position it covers, or None.

		None is not known, rather than no cell: a cell merged down into the sample from a row
		above it starts outside what was read, and a row the read never reached has nothing. A
		caller asks the table for such a position, as it would without a snapshot.
		"""
		if row not in self.rowsRead:
			return None
		return self._positions().get((row, column))

	def _positions(self) -> dict:
		""":return: every position in a read row, mapped to the first cell that covers it.

		The first, as a scan of `cells` in order finds it. Built again if cells or rows have been
		added since, so it never disagrees with them.
		"""
		size = (len(self.cells), len(self.rowsRead))
		if self._index is None or self._indexedSize != size:
			index: dict = {}
			for cell in self.cells:
				for row in range(cell.row, cell.row + cell.rowSpan):
					if row not in self.rowsRead:
						continue
					for column in range(cell.column, cell.column + cell.columnSpan):
						index.setdefault((row, column), cell)
			self._index = index
			self._indexedSize = size
		return self._index


def _isCellOf(field, tableID) -> bool:
	return bool(field) and field.get("table-id") == tableID and "table-rownumber" in field


def _number(value, default: int = 1) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def walk(commands: Iterable[Any], tableID) -> tuple[list[SnapshotCell], set[int]]:
	"""Find the cells of one table in what `getTextWithFields` returned.

	The outermost cell of this table is the current one. A nested table's cells have another
	`table-id`, so they start no cell of this one, and their text goes to the cell they are in,
	as `info.text` reads it. A cell marked `isHidden` is left out, as `_getTableCellAt` refuses
	one.

	:param commands: strings and field commands, in order.
	:param tableID: the table's identifier, as its fields carry it.
	:return: the cells, and every row a whole cell was seen in. A cell the read ended inside is
		kept, but its row is not counted as read on its account.
	"""
	cells: list[SnapshotCell] = []
	rows: set[int] = set()
	depth = 0
	current: SnapshotCell | None = None
	currentDepth = 0
	hidden = False
	parts: list[str] = []
	for item in commands:
		if isinstance(item, str):
			if current is not None:
				parts.append(item)
			continue
		command = getattr(item, "command", None)
		if command == "controlStart":
			depth += 1
			field = getattr(item, "field", None)
			if current is None and _isCellOf(field, tableID):
				current = SnapshotCell(
					row=_number(field.get("table-rownumber")),
					column=_number(field.get("table-columnnumber")),
					text="",
					header=" ".join((field.get("table-columnheadertext") or "").split()),
					rowSpan=_number(field.get("table-rowsspanned")),
					columnSpan=_number(field.get("table-columnsspanned")),
				)
				hidden = bool(field.get("isHidden"))
				currentDepth = depth
				parts = []
		elif command == "controlEnd":
			if current is not None and depth == currentDepth:
				if not hidden:
					current.text = "".join(parts).strip()
					cells.append(current)
				rows.add(current.row)
				current = None
			depth = max(0, depth - 1)
	if current is not None and not hidden:
		current.text = "".join(parts).strip()
		cells.append(current)
	return cells, rows


def runsOf(rows: Iterable[int]) -> list[tuple[int, int]]:
	""":return: rows as runs of consecutive rows, each its first and last."""
	runs: list[tuple[int, int]] = []
	for row in sorted(set(rows)):
		if runs and row == runs[-1][1] + 1:
			runs[-1] = (runs[-1][0], row)
		else:
			runs.append((row, row))
	return runs


def _rowStart(document, rawID, row: int) -> int | None:
	""":return: where a row's first cell starts in the buffer, or None where no cell starts in it.

	Searched by the raw `table-id`, as NVDA's own `_iterTableCells` searches. The document
	handle half of the identifier is written into the field markup only, so it cannot be
	searched for.
	"""
	attribs = {"table-id": [str(rawID)], "table-rownumber": [str(row)]}
	for item in document._iterNodesByAttribs(attribs):
		return item.textInfo._startOffset
	return None


def _tableEnd(document, tableID) -> int:
	""":return: where the table ends in the buffer."""
	import textInfos

	info = document.makeTextInfo(textInfos.POSITION_FIRST)
	return info._getOffsetsFromFieldIdentifier(*tableID)[1]


def _read(document, start: int, end: int) -> list:
	""":return: the strings and field commands between two buffer offsets."""
	from textInfos.offsets import Offsets

	return document.makeTextInfo(Offsets(start, end)).getTextWithFields()


def _readRun(
	document,
	tableID,
	first: int,
	last: int,
	lastRow: int,
) -> tuple[list[SnapshotCell], set[int]] | None:
	"""Read one run of rows, from its first cell to the first cell of the row after it.

	Ending at the next row's start rather than at this run's last cell avoids asking for a cell
	that a merge may have left out.

	:return: the run's cells and the rows read, or None where the run could not be found or is
		too long.
	"""
	_docHandle, rawID = tableID
	with stageTimes.stage("snapshot find"):
		start = _rowStart(document, rawID, first)
		end = _rowStart(document, rawID, last + 1) if last < lastRow else None
		if end is None:
			end = _tableEnd(document, tableID)
	if start is None or end <= start or end - start > TOO_MUCH_TEXT:
		return None
	with stageTimes.stage("snapshot read"):
		commands = _read(document, start, end)
	with stageTimes.stage("snapshot walk"):
		cells, seen = walk(commands, tableID)
	wanted = range(first, last + 1)
	return [cell for cell in cells if cell.row in wanted], {row for row in seen if row in wanted}


def takeSnapshot(handle, rows: Iterable[int]) -> Snapshot | None:
	"""Read the cells of a table's sampled rows in as few reads as their runs allow.

	:param handle: the table.
	:param rows: the rows to read.
	:return: the snapshot, or None to measure the table cell by cell: for a document that does
		not read cells as NVDA's `Gecko_ia2` does, a run that cannot be found or is past
		`TOO_MUCH_TEXT`, or any failure.
	:raises CallCancelled: where NVDA stopped waiting; see `flow.CallCancelled`.
	"""
	try:
		from virtualBuffers.gecko_ia2 import Gecko_ia2
	except ImportError:
		return None
	document = handle.document
	tableID = handle.tableID
	if not hasNvdaCellLookup(document, Gecko_ia2._getTableCellAt):
		return None
	if not (isinstance(tableID, tuple) and len(tableID) == 2):
		return None
	snapshot = Snapshot()
	try:
		for first, last in runsOf(rows):
			read = _readRun(document, tableID, first, last, handle.numRows)
			if read is None:
				return None
			cells, seen = read
			snapshot.cells.extend(cells)
			snapshot.rowsRead |= seen
	except CallCancelled:
		raise
	except Exception:
		log.debugWarning("Could not take a snapshot of a table", exc_info=True)
		return None
	return snapshot
