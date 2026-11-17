# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a browse mode table's sampled rows in one go.

`FakeBuffer` lays a table out as NVDA's Gecko buffer does: a control field per cell, carrying
its table, row, column, spans and declared header, with text between, at offsets. Reading a
range hands back the strings and field commands in it, as `getTextWithFields` does.
"""

import sys
import types
import unittest
from unittest import mock

from ._stubs import FakeFieldCommand, installStubs

installStubs()

from brlMultiline import flowTableSnapshot  # noqa: E402
from brlMultiline.flow import CallCancelled  # noqa: E402
from brlMultiline.flowTableSnapshot import Snapshot, SnapshotCell, runsOf, takeSnapshot, walk  # noqa: E402
from brlMultiline.flowTableSource import TableHandle  # noqa: E402

TABLE = (7, 42)
"""The table's identifier as NVDA normalises it, document handle and raw id."""


def cellField(row, column, header="", rowSpan=1, columnSpan=1, table=TABLE, hidden=False):
	field = {
		"table-id": table,
		"table-rownumber": str(row),
		"table-columnnumber": str(column),
		"table-rowsspanned": str(rowSpan),
		"table-columnsspanned": str(columnSpan),
	}
	if header:
		field["table-columnheadertext"] = header
	if hidden:
		field["isHidden"] = "1"
	return field


def start(field):
	return FakeFieldCommand("controlStart", field)


def end():
	return FakeFieldCommand("controlEnd", None)


class FakeBuffer:
	"""A table's part of a buffer: items at offsets, and the searches NVDA offers over them."""

	def __init__(self):
		self.items = []
		self.offset = 0

	def open(self, field):
		self.items.append((self.offset, start(field)))
		return self

	def close(self):
		self.items.append((self.offset, end()))
		return self

	def text(self, said):
		self.items.append((self.offset, said))
		self.offset += len(said)
		return self

	def cell(self, row, column, said, **kwargs):
		return self.open(cellField(row, column, **kwargs)).text(said).close()

	def read(self, startOffset, endOffset):
		found = []
		for offset, item in self.items:
			if isinstance(item, FakeFieldCommand) and item.command == "controlEnd":
				if startOffset < offset <= endOffset:
					found.append(item)
			elif startOffset <= offset < endOffset:
				found.append(item)
		return found

	def cellStart(self, rawID, row):
		for offset, item in self.items:
			field = getattr(item, "field", None) or {}
			if (
				getattr(item, "command", None) == "controlStart"
				and field.get("table-id", (None, None))[1] == rawID
				and field.get("table-rownumber") == str(row)
			):
				return offset
		return None


class FakeRange:
	def __init__(self, buffer, position):
		self.buffer = buffer
		self.position = position

	def getTextWithFields(self, formatConfig=None):
		self.buffer.reads.append((self.position.startOffset, self.position.endOffset))
		return self.buffer.read(self.position.startOffset, self.position.endOffset)

	def _getOffsetsFromFieldIdentifier(self, docHandle, ID):
		assert (docHandle, ID) == TABLE
		return (0, self.buffer.offset)


class FakeGecko_ia2:
	"""Stands for NVDA's `Gecko_ia2`, whose cell lookup is what the snapshot is gated on."""

	def __init__(self, buffer):
		self.buffer = buffer
		buffer.reads = []

	def _getTableCellAt(self, tableID, startPos, row, column):
		raise LookupError

	def _iterNodesByAttribs(self, attribs):
		(rawID,) = attribs["table-id"]
		(row,) = attribs["table-rownumber"]
		offset = self.buffer.cellStart(int(rawID), int(row))
		if offset is not None:
			yield types.SimpleNamespace(textInfo=types.SimpleNamespace(_startOffset=offset))

	def makeTextInfo(self, position):
		return FakeRange(self.buffer, position)


class OwnLookup(FakeGecko_ia2):
	def _getTableCellAt(self, tableID, startPos, row, column):
		raise LookupError


def grid(rows, headers=None):
	"""A buffer of plain cells, row by row; `headers` declared on every cell of each column."""
	buffer = FakeBuffer()
	for rowNumber, row in enumerate(rows, start=1):
		for columnNumber, said in enumerate(row, start=1):
			buffer.cell(rowNumber, columnNumber, said, header=(headers or {}).get(columnNumber, ""))
	return buffer


def handleFor(document, numRows, numCols):
	return TableHandle(document, TABLE, 1, 1, numRows, numCols)


class SnapshotTestCase(unittest.TestCase):
	def setUp(self):
		modules = {"virtualBuffers.gecko_ia2": types.SimpleNamespace(Gecko_ia2=FakeGecko_ia2)}
		patcher = mock.patch.dict(sys.modules, modules)
		patcher.start()
		self.addCleanup(patcher.stop)

	def take(self, buffer, rows, numRows, numCols, kind=FakeGecko_ia2):
		self.document = kind(buffer)
		return takeSnapshot(handleFor(self.document, numRows, numCols), rows)


class TestWalking(unittest.TestCase):
	def test_plainCellsAreReadWithTheirText(self):
		buffer = grid([["Symbol", "Last"], ["AAPL", "182.50"]])
		cells, rows = walk(buffer.read(0, buffer.offset), TABLE)
		self.assertEqual(
			[(c.row, c.column, c.text) for c in cells],
			[
				(1, 1, "Symbol"),
				(1, 2, "Last"),
				(2, 1, "AAPL"),
				(2, 2, "182.50"),
			],
		)
		self.assertEqual(rows, {1, 2})

	def test_aCellsTextIsStrippedAsTheCellByCellReadStripsIt(self):
		buffer = FakeBuffer().cell(1, 1, "  padded \n")
		cells, _rows = walk(buffer.read(0, buffer.offset), TABLE)
		self.assertEqual(cells[0].text, "padded")

	def test_aDeclaredHeaderIsOneLine(self):
		buffer = FakeBuffer().cell(1, 1, "x", header="Quarter\nEnding")
		cells, _rows = walk(buffer.read(0, buffer.offset), TABLE)
		self.assertEqual(cells[0].header, "Quarter Ending")

	def test_aNestedTablesTextBelongsToTheCellItIsIn(self):
		buffer = FakeBuffer().open(cellField(1, 1, header="Details"))
		buffer.text("Order ")
		buffer.open(cellField(1, 1, header="Item", table=(7, 99))).text("Pens").close()
		buffer.open(cellField(1, 2, header="Qty", table=(7, 99))).text("12").close()
		buffer.close()
		buffer.cell(1, 2, "24.50")
		cells, _rows = walk(buffer.read(0, buffer.offset), TABLE)
		self.assertEqual(
			[(c.column, c.text, c.header) for c in cells],
			[
				(1, "Order Pens12", "Details"),
				(2, "24.50", ""),
			],
		)

	def test_aHiddenCellIsNoCell(self):
		buffer = FakeBuffer().cell(1, 1, "a").cell(1, 2, "secret", hidden=True).cell(1, 3, "c")
		cells, rows = walk(buffer.read(0, buffer.offset), TABLE)
		self.assertEqual([c.column for c in cells], [1, 3])
		self.assertEqual(rows, {1})

	def test_aCellTheReadEndedInsideDoesNotCountItsRowAsRead(self):
		buffer = FakeBuffer().cell(1, 1, "a").open(cellField(2, 1)).text("cut")
		cells, rows = walk(buffer.read(0, buffer.offset), TABLE)
		self.assertEqual([c.text for c in cells], ["a", "cut"])
		self.assertEqual(rows, {1})

	def test_fieldsAroundTheCellsAreNotCells(self):
		row = {"table-id": TABLE, "role": "row"}
		buffer = FakeBuffer().open(row).cell(1, 1, "a").close()
		cells, _rows = walk(buffer.read(0, buffer.offset), TABLE)
		self.assertEqual([c.text for c in cells], ["a"])


class TestSpans(unittest.TestCase):
	def snapshot(self):
		return Snapshot(
			cells=[
				SnapshotCell(1, 1, "Region", "", 1, 1),
				SnapshotCell(1, 2, "Pending", "", 1, 3),
				SnapshotCell(2, 1, "East", "", 2, 1),
				SnapshotCell(2, 2, "Mill", "", 1, 1),
			],
			rowsRead={1, 2, 3},
		)

	def test_aCellMergedAcrossColumnsIsAtEveryColumnItCovers(self):
		snapshot = self.snapshot()
		self.assertEqual([snapshot.cellAt(1, column).text for column in (2, 3, 4)], ["Pending"] * 3)

	def test_aCellMergedDownIsAtEveryRowItCovers(self):
		self.assertEqual(self.snapshot().cellAt(3, 1).text, "East")

	def test_aPositionNoCellCoversIsNotKnown(self):
		self.assertIsNone(self.snapshot().cellAt(3, 2))

	def test_aRowNotReadIsNotKnown(self):
		self.assertIsNone(self.snapshot().cellAt(4, 1))


class TestRuns(unittest.TestCase):
	def test_consecutiveRowsAreOneRun(self):
		self.assertEqual(runsOf([3, 4, 5]), [(3, 5)])

	def test_rowOneApartIsARunOfItsOwn(self):
		self.assertEqual(runsOf([1, 6, 7, 8]), [(1, 1), (6, 8)])


class TestTakingASnapshot(SnapshotTestCase):
	ROWS = [["Symbol", "Last"], ["AAPL", "182.50"], ["F", "9.10"], ["BRK.B", "402.15"], ["T", "17.00"]]

	def test_aRunOfRowsIsOneRead(self):
		snapshot = self.take(grid(self.ROWS), [2, 3, 4], 5, 2)
		self.assertIsNotNone(snapshot)
		self.assertEqual(len(self.document.buffer.reads), 1)
		self.assertEqual(snapshot.rowsRead, {2, 3, 4})
		self.assertEqual(snapshot.cellAt(3, 2).text, "9.10")

	def test_theReadStopsAtTheNextRow(self):
		snapshot = self.take(grid(self.ROWS), [2, 3], 5, 2)
		self.assertIsNone(snapshot.cellAt(4, 1))
		self.assertNotIn(4, snapshot.rowsRead)

	def test_theLastRowIsReadToTheTablesEnd(self):
		snapshot = self.take(grid(self.ROWS), [4, 5], 5, 2)
		self.assertEqual(snapshot.cellAt(5, 2).text, "17.00")

	def test_rowOneApartFromTheRestIsASecondRead(self):
		snapshot = self.take(grid(self.ROWS), [1, 4, 5], 5, 2)
		self.assertEqual(len(self.document.buffer.reads), 2)
		self.assertEqual(snapshot.rowsRead, {1, 4, 5})
		self.assertIsNone(snapshot.cellAt(2, 1))

	def test_headersComeWithTheCells(self):
		snapshot = self.take(grid(self.ROWS, headers={2: "Last price"}), [2, 3], 5, 2)
		self.assertEqual(snapshot.cellAt(2, 2).header, "Last price")
		self.assertEqual(snapshot.cellAt(2, 1).header, "")

	def test_aRowWithNoCellOfItsOwnCannotBeFound(self):
		"""Every position of it covered from above, so the search finds nothing to start at."""
		buffer = FakeBuffer().cell(1, 1, "East", rowSpan=2).cell(1, 2, "a").cell(3, 1, "West")
		self.assertIsNone(self.take(buffer, [2, 3], 3, 2))

	def test_aRunPastTheLimitIsNotRead(self):
		buffer = grid([["Entry"], ["x" * (flowTableSnapshot.TOO_MUCH_TEXT + 1)], ["Three"]])
		self.assertIsNone(self.take(buffer, [1, 2], 3, 1))
		self.assertEqual(self.document.buffer.reads, [])

	def test_aDocumentWithItsOwnLookupIsLeftToIt(self):
		self.assertIsNone(self.take(grid(self.ROWS), [2, 3], 5, 2, kind=OwnLookup))

	def test_aLookupSetOnTheDocumentItselfIsLeftToIt(self):
		document = FakeGecko_ia2(grid(self.ROWS))
		document._getTableCellAt = mock.Mock()
		self.assertIsNone(takeSnapshot(handleFor(document, 5, 2), [2, 3]))

	def test_aTableIdentifierNotAsGeckoGivesItIsLeftAlone(self):
		document = FakeGecko_ia2(grid(self.ROWS))
		handle = TableHandle(document, 42, 1, 1, 5, 2)
		self.assertIsNone(takeSnapshot(handle, [2, 3]))

	def test_aFailedReadIsMeasuredCellByCell(self):
		document = FakeGecko_ia2(grid(self.ROWS))
		document.makeTextInfo = mock.Mock(side_effect=RuntimeError("gone"))
		self.assertIsNone(takeSnapshot(handleFor(document, 5, 2), [4, 5]))

	def test_aCancelledCallIsNotSwallowed(self):
		document = FakeGecko_ia2(grid(self.ROWS))
		document.makeTextInfo = mock.Mock(side_effect=CallCancelled("NVDA stopped waiting"))
		with self.assertRaises(CallCancelled):
			takeSnapshot(handleFor(document, 5, 2), [4, 5])

	def test_withoutNvdasBufferThereIsNoSnapshot(self):
		with mock.patch.dict(sys.modules, {"virtualBuffers.gecko_ia2": None}):
			self.assertIsNone(takeSnapshot(handleFor(FakeGecko_ia2(grid(self.ROWS)), 5, 2), [2]))


if __name__ == "__main__":
	unittest.main()
