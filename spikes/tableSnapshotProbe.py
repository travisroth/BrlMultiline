# BrlMultiline: spike for measuring a browse mode table from one read.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

r"""Console spike: can a browse mode table be measured from one read of the document?

Phase 0 of docs/design/table-snapshot-plan.md. It measures the table under the caret twice,
cell by cell as `flowTableSource.measure` does now and from a snapshot, and reports where the
two disagree and what each cost. It answers the plan's open questions:

1. Is a cell's text in the snapshot exactly `info.text`, as `_textOf` strips it?
2. Does the held table's `cellAt` answer every position a merged cell covers?
3. Does an `aria-hidden` cell appear in the buffer, marked `isHidden`, or not at all?
4. Which finds a row's first cell more cheaply: the held table or the buffer's own search?
5. How much text does the snapshot read, and how long does that take?

Open a table in Chrome, Edge or Firefox in browse mode, put the caret in a cell, open the NVDA
Python console (NVDA+control+z), and run:

	import sys; sys.path.insert(0, r"C:\code\BrlMultiline\spikes")
	import tableSnapshotProbe as probe
	probe.compare()     # both ways, the differences, and the times
	probe.save()        # the same, into a text file in spikes\tableSnapshotOut
	probe.cells()       # every sampled cell's text both ways, side by side

Each takes the object to start from as its first argument, by default what had focus when the
console opened. `compare(plain=True)` measures every column as drawn without capitals, which
is the drop capitals setting, to check that path too.

After editing this file, reload it:

	import importlib; probe = importlib.reload(probe)

Nothing here moves the caret or changes the page. It only reads.
"""

from __future__ import annotations

import dataclasses
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import api

MEASUREMENT_FIELDS = ("width", "typicalWidth", "label", "declared", "labelWidth", "hidden")
"""What `compare` checks of each column's measurement."""

TOO_MUCH_TEXT = 200_000
"""A range longer than this is not read, and the snapshot gives up. Question 5 tunes it."""


# Finding things


def _source():
	"""The add-on's table module, as NVDA loaded it."""
	from globalPlugins.brlMultiline import flowTableSource

	return flowTableSource


def _focusSnapshot():
	"""What had focus when the console opened, falling back to the live focus."""
	try:
		import pythonConsole

		snapshot = pythonConsole.consoleUI.console._namespaceSnapshotVars
		if snapshot and snapshot.get("focus") is not None:
			return snapshot["focus"]
	except Exception:
		pass
	return api.getFocusObject()


def _handle(obj=None):
	""":return: the table under the caret, or None with a message printed."""
	obj = obj or _focusSnapshot()
	document = getattr(obj, "treeInterceptor", None)
	if document is None or not hasattr(document, "VBufHandle"):
		print("No browse mode document under focus. Click into the page and reopen the console.")
		return None
	from virtualBuffers.gecko_ia2 import Gecko_ia2

	if not isinstance(document, Gecko_ia2):
		print(
			f"This is a {type(document).__name__}, not Firefox or Chromium. The snapshot is only for those.",
		)
		return None
	handle = _source().tableAt(obj)
	if handle is None:
		print("The caret is not in a table. Put it in a cell and reopen the console.")
	return handle


def _columns(handle) -> list[int]:
	"""The columns `measure` measures across. Browse mode hides none."""
	return list(range(1, handle.numCols + 1))


@contextmanager
def _timed(times: dict, name: str) -> Iterator[None]:
	"""Add how long the block took to `times[name]`, in seconds."""
	start = time.perf_counter()
	try:
		yield
	finally:
		times[name] = times.get(name, 0.0) + time.perf_counter() - start


# The snapshot


@dataclasses.dataclass
class SnapshotCell:
	"""One cell as the snapshot read it."""

	row: int
	column: int
	text: str
	header: str = ""
	rowSpan: int = 1
	columnSpan: int = 1
	hidden: bool = False


@dataclasses.dataclass
class Snapshot:
	"""What one read of a table's rows found."""

	cells: list[SnapshotCell]
	rowsSeen: set[int]
	characters: int = 0
	commands: int = 0
	ranges: list[tuple[int, int]] = dataclasses.field(default_factory=list)

	def textAt(self, row: int, column: int) -> str | None:
		""":return: the text at a position, from the cell covering it, or None for no cell."""
		cell = self.cellAt(row, column)
		return None if cell is None else cell.text

	def cellAt(self, row: int, column: int) -> SnapshotCell | None:
		""":return: the visible cell covering a position, spans included, or None."""
		for cell in self.cells:
			if cell.hidden:
				continue
			if (
				cell.row <= row < cell.row + cell.rowSpan
				and cell.column <= column < cell.column + cell.columnSpan
			):
				return cell
		return None

	def headers(self) -> dict[int, str]:
		""":return: the first declared header found for each column, by the cell's own column."""
		found: dict[int, str] = {}
		for cell in self.cells:
			if cell.header and not cell.hidden and cell.column not in found:
				found[cell.column] = cell.header
		return found


def _isCellOf(field, tableID) -> bool:
	""":return: whether a control field is a cell of the table named."""
	return field is not None and field.get("table-id") == tableID and "table-rownumber" in field


def _toInt(value, default: int = 1) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def walk(commands, tableID) -> tuple[list[SnapshotCell], set[int]]:
	"""Find the cells of one table in what `getTextWithFields` returned.

	The outermost cell of this table is the current one; a nested table's cells have another
	`table-id`, and their text goes to the cell they are in.

	:param commands: strings and field commands, in order.
	:param tableID: the table's identifier, as the fields carry it.
	:return: the cells, and every row number a cell was seen in.
	"""
	cells: list[SnapshotCell] = []
	rows: set[int] = set()
	stack: list[Any] = []
	current: SnapshotCell | None = None
	currentDepth = 0
	parts: list[str] = []
	for item in commands:
		if isinstance(item, str):
			if current is not None:
				parts.append(item)
			continue
		command = getattr(item, "command", None)
		if command == "controlStart":
			field = getattr(item, "field", None)
			stack.append(field)
			if current is None and _isCellOf(field, tableID):
				current = SnapshotCell(
					row=_toInt(field.get("table-rownumber")),
					column=_toInt(field.get("table-columnnumber")),
					text="",
					header=" ".join((field.get("table-columnheadertext") or "").split()),
					rowSpan=_toInt(field.get("table-rowsspanned")),
					columnSpan=_toInt(field.get("table-columnsspanned")),
					hidden=bool(field.get("isHidden")),
				)
				currentDepth = len(stack)
				parts = []
		elif command == "controlEnd":
			if stack:
				stack.pop()
			if current is not None and len(stack) < currentDepth:
				current.text = "".join(parts).strip()
				cells.append(current)
				rows.add(current.row)
				current = None
	if current is not None:
		# A range that ended inside a cell. Kept, since its text up to there was read, but its
		# row is not counted as read.
		current.text = "".join(parts).strip()
		cells.append(current)
	return cells, rows


def _rowStartByHeld(handle, row: int) -> int | None:
	""":return: where a row's first cell starts, from the held table, or None."""
	try:
		info = _source()._cellInfoAt(handle, row, 1)
	except LookupError:
		return None
	return getattr(info, "_startOffset", None)


def _rowStartBySearch(handle, row: int) -> int | None:
	""":return: where a row's first cell starts, from the buffer's attribute search, or None.

	The document handle is not searchable: the buffer writes it into the markup only, so the
	search is by the raw table id, as NVDA's own `_iterTableCells` searches.
	"""
	_docHandle, rawID = handle.tableID
	attribs = {
		"table-id": [str(rawID)],
		"table-rownumber": [str(row)],
	}
	for item in handle.document._iterNodesByAttribs(attribs):
		return item.textInfo._startOffset
	return None


def _tableEnd(handle) -> int:
	""":return: where the table ends in the buffer."""
	import textInfos

	info = handle.document.makeTextInfo(textInfos.POSITION_FIRST)
	return info._getOffsetsFromFieldIdentifier(*handle.tableID)[1]


def _runs(rows: list[int]) -> list[tuple[int, int]]:
	""":return: the sampled rows as runs of consecutive rows, first and last."""
	runs: list[tuple[int, int]] = []
	for row in sorted(set(rows)):
		if runs and row == runs[-1][1] + 1:
			runs[-1] = (runs[-1][0], row)
		else:
			runs.append((row, row))
	return runs


def take(handle, rows: list[int], rowStart: Callable, times: dict) -> Snapshot | None:
	"""Read the sampled rows of a table in as few reads as the runs of rows allow.

	:param handle: the table.
	:param rows: the sampled rows.
	:param rowStart: how to find a row's first cell, `_rowStartByHeld` or `_rowStartBySearch`.
	:param times: where to add the time of each step.
	:return: the snapshot, or None where a range could not be found or was too long.
	"""
	import textInfos

	document = handle.document
	snapshot = Snapshot(cells=[], rowsSeen=set())
	for first, last in _runs(rows):
		with _timed(times, "find rows"):
			start = rowStart(handle, first)
			end = rowStart(handle, last + 1) if last < handle.numRows else None
			if end is None:
				end = _tableEnd(handle)
		if start is None or end <= start:
			print(f"Could not find rows {first} to {last} in the buffer.")
			return None
		if end - start > TOO_MUCH_TEXT:
			print(f"Rows {first} to {last} are {end - start} characters, past {TOO_MUCH_TEXT}.")
			return None
		with _timed(times, "read"):
			info = document.makeTextInfo(textInfos.offsets.Offsets(start, end))
			commands = info.getTextWithFields()
		with _timed(times, "walk"):
			cells, seen = walk(commands, handle.tableID)
		snapshot.ranges.append((start, end))
		snapshot.characters += end - start
		snapshot.commands += len(commands)
		snapshot.cells.extend(
			cell for cell in cells if cell.row in range(first, last + 1) or cell.rowSpan > 1
		)
		snapshot.rowsSeen |= {row for row in seen if first <= row <= last}
	return snapshot


# Measuring


def _headersAsMeasureAsks(handle, snapshot: Snapshot, rows: list[int]) -> dict[int, str]:
	"""The declared headers `measure` would find reading cell by cell, in its order.

	Row by row, up to `HEADER_TRIES` cells per column, stopping at the first answer, and
	stopping too where row one is borrowed as the header because nothing was declared by then.
	"""
	source = _source()
	borrowRowOne = source.firstRowIsHeadings(handle.document)
	found: dict[int, str] = {}
	for column in _columns(handle):
		tries = 0
		for row in rows:
			cell = snapshot.cellAt(row, column)
			if cell is None:
				continue
			if tries < source.HEADER_TRIES:
				tries += 1
				if cell.header:
					found[column] = cell.header
					break
			if row == source.HEADER_ROW and borrowRowOne:
				break
	return found


@contextmanager
def _measuredFrom(handle, snapshot: Snapshot, rows: list[int]) -> Iterator[None]:
	"""Have `measure` read the snapshot's rows as a grid's bulk rows.

	Its headers are the ones the cell by cell read would have found; see
	`_headersAsMeasureAsks`. A position with no cell is given as "", since the bulk row seam
	has no way to say "no cell". That makes such a column found and empty rather than not
	found, which can move its typical width; `compare` says where.
	"""
	source = _source()
	ownRowText, ownHeaders = source.rowTextOf, source.columnHeadersOf
	asMeasureAsks = _headersAsMeasureAsks(handle, snapshot, rows)

	def rowTextOf(handle, row, first, last):
		if row not in snapshot.rowsSeen:
			return None
		return [snapshot.textAt(row, column) or "" for column in range(first, last + 1)]

	def columnHeadersOf(handle, first, last):
		return {column: said for column, said in asMeasureAsks.items() if first <= column <= last}

	source.rowTextOf, source.columnHeadersOf = rowTextOf, columnHeadersOf
	try:
		yield
	finally:
		source.rowTextOf, source.columnHeadersOf = ownRowText, ownHeaders


def _measure(handle, plain: bool) -> list:
	source = _source()
	plainCase = _columns(handle) if plain else ()
	return source.measure(handle, plainCase=plainCase)


def _cellByCell(handle, rows: list[int], times: dict) -> dict:
	""":return: each sampled position's text, header and where its cell starts, read one by one."""
	source = _source()
	found: dict = {}
	for row in rows:
		for column in _columns(handle):
			with _timed(times, "cell lookups"):
				try:
					info = source._cellInfoAt(handle, row, column)
				except LookupError:
					info = None
			if info is None:
				found[(row, column)] = None
				continue
			with _timed(times, "cell text"):
				text = source._textOf(info)
			with _timed(times, "cell headers"):
				header = source.declaredHeader(info)
			found[(row, column)] = (text, header, getattr(info, "_startOffset", None))
	return found


def collect(obj=None, plain: bool = False) -> dict | None:
	"""Measure the table both ways and gather everything the report needs."""
	handle = _handle(obj)
	if handle is None:
		return None
	source = _source()
	rows = source._sampleRows(handle, source.MEASURE_ROWS)
	data: dict[str, Any] = {"handle": repr(handle), "rows": rows, "plain": plain}
	with source.tableHeld(handle):
		start = time.perf_counter()
		ordinary = _measure(handle, plain)
		data["ordinaryTime"] = time.perf_counter() - start

		cellTimes: dict = {}
		data["cells"] = _cellByCell(handle, rows, cellTimes)
		data["cellTimes"] = cellTimes

		for name, rowStart in (("held", _rowStartByHeld), ("search", _rowStartBySearch)):
			times: dict = {}
			try:
				snapshot = take(handle, rows, rowStart, times)
			except Exception as error:
				print(f"The snapshot by {name} failed: {error!r}")
				snapshot = None
			data[f"{name}Times"] = times
			data[f"{name}Snapshot"] = snapshot
		snapshot = data["searchSnapshot"] or data["heldSnapshot"]
		data["snapshot"] = snapshot
		if snapshot is not None:
			start = time.perf_counter()
			with _measuredFrom(handle, snapshot, rows):
				fromSnapshot = _measure(handle, plain)
			data["snapshotMeasureTime"] = time.perf_counter() - start
		else:
			fromSnapshot = []
	data["ordinary"] = ordinary
	data["fromSnapshot"] = fromSnapshot
	return data


# Reporting


def _seconds(value: float) -> str:
	return f"{value:.3f}s"


def _measurementLines(data: dict) -> list[str]:
	ordinary = {each.index: each for each in data["ordinary"]}
	snapshotted = {each.index: each for each in data["fromSnapshot"]}
	lines = []
	for column in sorted(ordinary):
		mine, theirs = ordinary[column], snapshotted.get(column)
		if theirs is None:
			lines.append(f"  column {column}: not measured from the snapshot")
			continue
		for name in MEASUREMENT_FIELDS:
			before, after = getattr(mine, name), getattr(theirs, name)
			if before != after:
				lines.append(f"  column {column} {name}: cell by cell {before!r}, snapshot {after!r}")
	return lines or ["  none: every column measures the same"]


def _textLines(data: dict) -> list[str]:
	"""Question 1, and where the two reads find a cell or not."""
	snapshot: Snapshot = data["snapshot"]
	lines = []
	for (row, column), read in sorted(data["cells"].items()):
		if row not in snapshot.rowsSeen:
			continue
		said = snapshot.textAt(row, column)
		if read is None and said is None:
			continue
		if read is None:
			lines.append(f"  row {row} column {column}: no cell by cell, snapshot {said!r}")
		elif said is None:
			lines.append(f"  row {row} column {column}: cell by cell {read[0]!r}, no cell in the snapshot")
		elif read[0] != said:
			lines.append(f"  row {row} column {column}: cell by cell {read[0]!r}, snapshot {said!r}")
	return lines or ["  none: every sampled cell has the same text"]


def _headerLines(data: dict) -> list[str]:
	byCell: dict[int, str] = {}
	for (_row, column), read in sorted(data["cells"].items()):
		if read is not None and read[1] and column not in byCell:
			byCell[column] = read[1]
	bySnapshot = data["snapshot"].headers()
	lines = []
	for column in sorted(set(byCell) | set(bySnapshot)):
		if byCell.get(column, "") != bySnapshot.get(column, ""):
			lines.append(
				f"  column {column}: cell by cell {byCell.get(column, '')!r}, "
				f"snapshot {bySnapshot.get(column, '')!r}",
			)
	return lines or [f"  none: {len(bySnapshot)} columns declare the same header both ways"]


def _spanLines(data: dict) -> list[str]:
	"""Question 2: positions the held table answered with a cell that starts elsewhere."""
	snapshot: Snapshot = data["snapshot"]
	spans = [cell for cell in snapshot.cells if cell.rowSpan > 1 or cell.columnSpan > 1]
	lines = [f"  merged cells in the snapshot: {len(spans)}"]
	for cell in spans:
		covered = [
			(row, column)
			for row in range(cell.row, cell.row + cell.rowSpan)
			for column in range(cell.column, cell.column + cell.columnSpan)
		]
		starts = {
			data["cells"].get(where, (None, None, None))[2] for where in covered if where in data["cells"]
		}
		lines.append(
			f"  row {cell.row} column {cell.column}, {cell.rowSpan} by {cell.columnSpan}, {cell.text[:30]!r}: "
			f"the held table gave {len(starts)} different cell starts across the {len(covered)} positions "
			f"({'one cell for all of them' if len(starts) == 1 else 'not one cell'})",
		)
	return lines


def _hiddenLines(data: dict) -> list[str]:
	"""Question 3."""
	hidden = [cell for cell in data["snapshot"].cells if cell.hidden]
	refused = [where for where, read in data["cells"].items() if read is None]
	return [
		f"  cells marked isHidden in the snapshot: {[(cell.row, cell.column) for cell in hidden]}",
		f"  positions the cell by cell read found no cell at: {sorted(refused)}",
	]


def _timeLines(data: dict) -> list[str]:
	lines = [
		f"  measure, cell by cell: {_seconds(data['ordinaryTime'])}",
		f"  measure, from the snapshot, after it was taken: {_seconds(data.get('snapshotMeasureTime', 0.0))}",
		"  cell by cell reads alone: "
		+ ", ".join(f"{name} {_seconds(value)}" for name, value in data["cellTimes"].items()),
	]
	for name in ("held", "search"):
		snapshot = data[f"{name}Snapshot"]
		times = data[f"{name}Times"]
		total = sum(times.values())
		if snapshot is None:
			lines.append(f"  snapshot, rows found by {name}: failed")
			continue
		lines.append(
			f"  snapshot, rows found by {name}: {_seconds(total)} ("
			+ ", ".join(f"{step} {_seconds(value)}" for step, value in times.items())
			+ f"), {len(snapshot.ranges)} reads, {snapshot.characters} characters, "
			f"{snapshot.commands} commands",
		)
	return lines


def report(data: dict) -> str:
	""":return: the whole comparison as readable text."""
	lines = [
		f"Table snapshot probe, {time.strftime('%Y-%m-%d %H:%M:%S')}",
		f"Table: {data['handle']}",
		f"Rows sampled: {data['rows']}",
		f"Every column without capitals: {data['plain']}",
		"",
		"Time (question 4 and 5)",
		*_timeLines(data),
	]
	if data["snapshot"] is None:
		lines += ["", "No snapshot could be taken, so nothing else can be compared."]
		return "\n".join(lines)
	unseen = [row for row in data["rows"] if row not in data["snapshot"].rowsSeen]
	lines += [
		f"  rows the snapshot did not see, measured cell by cell: {unseen or 'none'}",
		"",
		"Measurements that differ",
		*_measurementLines(data),
		"",
		"Cell text that differs (question 1)",
		*_textLines(data),
		"",
		"Declared headers that differ",
		*_headerLines(data),
		"",
		"Merged cells (question 2)",
		*_spanLines(data),
		"",
		"Hidden cells (question 3)",
		*_hiddenLines(data),
	]
	return "\n".join(lines)


def compare(obj=None, plain: bool = False) -> None:
	"""Measure the table both ways and print the differences and the times."""
	data = collect(obj, plain)
	if data is not None:
		print(report(data))


def save(obj=None, plain: bool = False, folder: str | None = None) -> None:
	"""Run `compare` and write its report to a text file, named after the page and the time."""
	data = collect(obj, plain)
	if data is None:
		return
	folder = folder or os.path.join(os.path.dirname(os.path.abspath(__file__)), "tableSnapshotOut")
	os.makedirs(folder, exist_ok=True)
	title = ""
	try:
		title = _handle(obj).document.rootNVDAObject.name or ""
	except Exception:
		pass
	stem = re.sub(r"[^A-Za-z0-9.-]+", "_", title)[:40] or "table"
	path = os.path.join(folder, f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.txt")
	with open(path, "w", encoding="utf-8") as f:
		f.write(report(data))
	print(f"Saved {path}")


def cells(obj=None) -> None:
	"""Print every sampled cell's text, cell by cell and from the snapshot, side by side."""
	data = collect(obj)
	if data is None:
		return
	snapshot: Snapshot | None = data["snapshot"]
	for (row, column), read in sorted(data["cells"].items()):
		said = snapshot.textAt(row, column) if snapshot is not None else None
		mine = "no cell" if read is None else repr(read[0])
		theirs = "no cell" if said is None else repr(said)
		mark = (
			"" if (read is None and said is None) or (read is not None and read[0] == said) else "  DIFFERS"
		)
		print(f"row {row} column {column}: {mine} | {theirs}{mark}")
