# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a Gecko or Chromium table with the table found once.

The stand-ins answer the calls NVDA's own `Gecko_ia2._getTableCellAt` makes, and record them, so
a test can say what was not asked as well as what was. What matters is that the table is found
once, and that every way a cell can fail ends where the ordinary read would have ended.
"""

import sys
import types
import unittest
from unittest import mock

from ._stubs import FakeTableDocument, installStubs

installStubs()

from brlMultiline import flowTableGecko, flowTableSource  # noqa: E402
from brlMultiline.flow import CallCancelled  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowTableGecko import HeldTable, HeldTableFailed, holdTable  # noqa: E402
from brlMultiline.flowTableSource import (  # noqa: E402
	TableFlowSource,
	TableHandle,
	cellRegion,
	tableHeld,
)


COMError = flowTableGecko.COMError
"""The class the module catches, which is whichever stand-in was installed when it was imported."""


class FakeIA2Cell:
	"""What a table hands back for a cell."""

	def __init__(self, text, hidden=False):
		self.text = text
		self.hidden = hidden

	def QueryInterface(self, interface):
		self.interface = interface
		return self


class FakeCellObject:
	"""An NVDA object for a cell."""

	def __init__(self, IAccessibleObject, IAccessibleChildID):
		self.cell = IAccessibleObject
		self.childID = IAccessibleChildID
		self.IA2Attributes = {"hidden": "true"} if IAccessibleObject.hidden else {}


class FakeTable2:
	"""`IAccessibleTable2`, as a table object carries it."""

	def __init__(self, answer):
		self.answer = answer
		self.asked = []

	def cellAt(self, row, column):
		self.asked.append((row, column))
		return self.answer(row, column)


class FakeTable1:
	"""`IAccessibleTable`, for a table without the newer interface."""

	def __init__(self, answer):
		self.answer = answer
		self.asked = []

	def accessibleAt(self, row, column):
		self.asked.append((row, column))
		return self.answer(row, column)


class FakeInfo:
	def __init__(self, text):
		self.text = text


class FakeGecko_ia2(FakeTableDocument):
	"""Stands for NVDA's `Gecko_ia2`, whose `_getTableCellAt` is what `holdTable` looks for."""

	def __init__(self, rows, table=None, **kwargs):
		super().__init__(rows, tableID=(1, 99), **kwargs)
		self.table = table
		self.found = 0

	def getNVDAObjectFromIdentifier(self, docHandle, ID):
		self.found += 1
		return self.table

	def makeTextInfo(self, cell):
		return FakeInfo(cell.cell.text)


class FakeChromeVBuf(FakeGecko_ia2):
	"""Stands for `ChromeVBuf`, which inherits the method."""


class CustomVBuf(FakeGecko_ia2):
	"""A document with its own way of finding cells, which is not this module's business."""

	def _getTableCellAt(self, tableID, startPos, row, column):
		return super()._getTableCellAt(tableID, startPos, row, column)


def tableObject(interface=None, older=None):
	return types.SimpleNamespace(
		**({"IAccessibleTable2Object": interface} if interface else {}),
		**({"IAccessibleTableObject": older} if older else {}),
	)


def handleFor(document):
	return TableHandle(document, document.tableID, 2, 2, 1, 1)


def text(row, column):
	return FakeIA2Cell(f"held {row} {column}")


class HeldTableTestCase(unittest.TestCase):
	"""Runs with NVDA's three modules standing where `holdTable` looks for them."""

	def setUp(self):
		modules = {
			"comInterfaces.IAccessible2Lib": types.SimpleNamespace(IAccessible2="IAccessible2"),
			"NVDAObjects.IAccessible": types.SimpleNamespace(IAccessible=FakeCellObject),
			"virtualBuffers.gecko_ia2": types.SimpleNamespace(Gecko_ia2=FakeGecko_ia2),
		}
		patcher = mock.patch.dict(sys.modules, modules)
		patcher.start()
		self.addCleanup(patcher.stop)

	def document(self, answer=text, kind=FakeChromeVBuf):
		self.table2 = FakeTable2(answer)
		return kind([["a", "b"], ["c", "d"]], table=tableObject(self.table2))


class TestHolding(HeldTableTestCase):
	def test_chromeInheritsTheMethodAndIsHeld(self):
		self.assertIsNotNone(holdTable(handleFor(self.document())))

	def test_geckoItselfIsHeld(self):
		self.assertIsNotNone(holdTable(handleFor(self.document(kind=FakeGecko_ia2))))

	def test_aDocumentWithItsOwnLookupIsNotHeld(self):
		document = self.document(kind=CustomVBuf)
		self.assertIsNone(holdTable(handleFor(document)))
		self.assertEqual(document.found, 0)

	def test_anotherKindOfDocumentIsNotHeld(self):
		self.assertIsNone(holdTable(handleFor(FakeTableDocument([["a"]]))))

	def test_withoutNVDAsModulesNothingIsHeld(self):
		with mock.patch.dict(sys.modules, {"virtualBuffers.gecko_ia2": None}):
			self.assertIsNone(holdTable(handleFor(self.document())))

	def test_aTableThatCannotBeFoundIsNotHeld(self):
		document = FakeChromeVBuf([["a"]], table=None)
		self.assertIsNone(holdTable(handleFor(document)))

	def test_aTableWithNeitherInterfaceIsNotHeld(self):
		document = FakeChromeVBuf([["a"]], table=tableObject())
		self.assertIsNone(holdTable(handleFor(document)))

	def test_aFailureFindingTheTableIsNotHeld(self):
		document = self.document()
		with mock.patch.object(document, "getNVDAObjectFromIdentifier", side_effect=COMError(1, "", ())):
			self.assertIsNone(holdTable(handleFor(document)))

	def test_aCancelledCallIsRaisedRatherThanTriedAnotherWay(self):
		document = self.document()
		with mock.patch.object(document, "getNVDAObjectFromIdentifier", side_effect=CallCancelled):
			with self.assertRaises(CallCancelled):
				holdTable(handleFor(document))


class TestReadingACell(HeldTableTestCase):
	def held(self, document):
		return holdTable(handleFor(document))

	def test_rowsAndColumnsAreAskedForFromZero(self):
		document = self.document()
		info = self.held(document).cellAt(2, 1)
		self.assertEqual(self.table2.asked, [(1, 0)])
		self.assertEqual(info.text, "held 1 0")

	def test_aCellIsMadeAsNVDAMakesIt(self):
		document = self.document()
		made = []
		original = document.makeTextInfo
		document.makeTextInfo = lambda cell: made.append(cell) or original(cell)
		self.held(document).cellAt(1, 1)
		self.assertEqual(made[0].childID, 0)
		self.assertEqual(made[0].cell.interface, "IAccessible2")

	def test_theOlderInterfaceIsUsedWhereTheNewerIsMissing(self):
		older = FakeTable1(text)
		document = FakeChromeVBuf([["a"]], table=tableObject(older=older))
		self.assertEqual(self.held(document).cellAt(1, 1).text, "held 0 0")
		self.assertEqual(older.asked, [(0, 0)])

	def test_theNewerInterfaceIsPreferred(self):
		older = FakeTable1(text)
		newer = FakeTable2(text)
		document = FakeChromeVBuf([["a"]], table=tableObject(newer, older))
		self.held(document).cellAt(1, 1)
		self.assertEqual(older.asked, [])

	def test_aHiddenCellIsNoCell(self):
		document = self.document(lambda row, column: FakeIA2Cell("x", hidden=True))
		with self.assertRaises(LookupError):
			self.held(document).cellAt(1, 1)

	def test_aCellNotInTheDocumentIsNoCell(self):
		document = self.document()
		document.makeTextInfo = mock.Mock(side_effect=LookupError)
		with self.assertRaises(LookupError):
			self.held(document).cellAt(1, 1)

	def test_aCOMErrorSaysTheTableFailed(self):
		def fails(row, column):
			raise COMError(1, "", ())

		with self.assertRaises(HeldTableFailed):
			self.held(self.document(fails)).cellAt(1, 1)

	def test_aRuntimeErrorSaysTheTableFailed(self):
		document = self.document()
		document.makeTextInfo = mock.Mock(side_effect=RuntimeError)
		with self.assertRaises(HeldTableFailed):
			self.held(document).cellAt(1, 1)


class TestHoldingForABlock(HeldTableTestCase):
	def test_theTableIsFoundOnceForEveryCell(self):
		document = self.document()
		handle = handleFor(document)
		with tableHeld(handle):
			said = [cellRegion(handle, row, column).rawText for row in (1, 2) for column in (1, 2)]
		self.assertEqual(said, ["held 0 0", "held 0 1", "held 1 0", "held 1 1"])
		self.assertEqual(document.found, 1)
		self.assertEqual(document.reads, [])

	def test_afterTheBlockCellsAreReadTheOrdinaryWay(self):
		document = self.document()
		handle = handleFor(document)
		with tableHeld(handle):
			pass
		self.assertEqual(cellRegion(handle, 1, 1).rawText, "a")
		self.assertEqual(document.reads, [(1, 1)])

	def test_theTableIsLetGoAfterAnException(self):
		document = self.document()
		handle = handleFor(document)
		with self.assertRaises(ValueError):
			with tableHeld(handle):
				raise ValueError
		cellRegion(handle, 1, 1)
		self.assertEqual(document.reads, [(1, 1)])

	def test_anotherTableIsNotAnsweredFromTheHeldOne(self):
		document = self.document()
		other = self.document()
		with tableHeld(handleFor(document)):
			self.assertEqual(cellRegion(handleFor(other), 1, 1).rawText, "a")
		self.assertEqual(other.reads, [(1, 1)])

	def test_aFailedCellIsReadTheOrdinaryWay(self):
		def fails(row, column):
			raise COMError(1, "", ())

		document = self.document(fails)
		handle = handleFor(document)
		with tableHeld(handle):
			self.assertEqual(cellRegion(handle, 2, 2).rawText, "d")
		self.assertEqual(document.reads, [(2, 2)])

	def test_aHiddenCellIsNotAskedForAgain(self):
		document = self.document(lambda row, column: FakeIA2Cell("x", hidden=True))
		handle = handleFor(document)
		with tableHeld(handle):
			self.assertIsNone(cellRegion(handle, 1, 1))
		self.assertEqual(document.reads, [])

	def test_aCancelledCellIsNotAskedForAgain(self):
		def cancelled(row, column):
			raise CallCancelled

		document = self.document(cancelled)
		handle = handleFor(document)
		with tableHeld(handle):
			with self.assertRaises(CallCancelled):
				cellRegion(handle, 1, 1)
		self.assertEqual(document.reads, [])

	def test_aDocumentNotHeldIsReadAsBefore(self):
		document = FakeTableDocument([["a"]])
		handle = TableHandle(document, document.tableID, 1, 1, 1, 1)
		with tableHeld(handle):
			self.assertEqual(cellRegion(handle, 1, 1).rawText, "a")
		self.assertEqual(document.reads, [(1, 1)])

	def test_theStagesOfACellAreTimed(self):
		document = self.document()
		handle = handleFor(document)
		from brlMultiline import stageTimes

		with (
			mock.patch.object(stageTimes, "enabled", True),
			stageTimes.operation("table show"),
			tableHeld(handle),
		):
			timing = stageTimes._current()
			cellRegion(handle, 1, 1)
			stages = set(timing.stages)
		for name in ("cell at", "cell object", "hidden check", "cell info"):
			self.assertIn(f"cell lookup/{name}", stages)


class FakePlan:
	"""A page of a table's columns."""

	def __init__(self, at, columns):
		self.at = at
		self.columns = columns

	def placements(self):
		return [types.SimpleNamespace(column=types.SimpleNamespace(index=column)) for column in self.columns]


class FakeController:
	"""What `FlowController.useColumnPage` asks of its controller, reading rows as it would."""

	useColumnPage = FlowController.useColumnPage

	def __init__(self, source, read):
		self.source = source
		self.read = read
		self.renderer = types.SimpleNamespace(columnPlan=FakePlan(0, ()))
		self.pinnedBlock = object()
		self.pinned = None

	def setColumnPlan(self, plan, reread=False):
		self.renderer.columnPlan = plan
		self.read()
		return True

	def setPinned(self, block):
		self.pinned = block


class TestTurningAPage(HeldTableTestCase):
	def controller(self, document):
		source = TableFlowSource(handleFor(document), columns=(1,), generation=1)
		source.headerBlock = lambda: cellRegion(source.handle, 1, 2)
		return FakeController(source, lambda: cellRegion(source.handle, 2, 2))

	def test_theTableIsFoundOnceForTheWholePage(self):
		document = self.document()
		self.assertTrue(self.controller(document).useColumnPage(FakePlan(1, (2,))))
		self.assertEqual(document.found, 1)
		self.assertEqual(document.reads, [])

	def test_theHeaderRowIsReadWithTheTableHeld(self):
		control = self.controller(self.document())
		control.useColumnPage(FakePlan(1, (2,)))
		self.assertEqual(control.pinned.rawText, "held 0 1")

	def test_thePageAlreadyShowingFindsNothing(self):
		document = self.document()
		self.assertFalse(self.controller(document).useColumnPage(FakePlan(0, (1,))))
		self.assertEqual(document.found, 0)

	def test_theTableIsLetGoAfterThePage(self):
		document = self.document()
		control = self.controller(document)
		control.useColumnPage(FakePlan(1, (2,)))
		cellRegion(control.source.handle, 1, 1)
		self.assertEqual(document.reads, [(1, 1)])

	def test_aSourceThatHoldsNothingStillTurns(self):
		source = types.SimpleNamespace(setColumns=mock.Mock())
		control = FakeController(source, lambda: None)
		self.assertTrue(control.useColumnPage(FakePlan(1, (2,))))
		source.setColumns.assert_called_once_with((2,))


class TestTheModule(unittest.TestCase):
	def test_aFailureIsNotALookupError(self):
		"""`cellRegion` reads a `LookupError` as no cell, which a failed table is not."""
		self.assertFalse(issubclass(flowTableGecko.HeldTableFailed, LookupError))

	def test_aHeldTableKnowsItsOwnHandle(self):
		document = FakeTableDocument([["a"]])
		held = HeldTable(document, document.tableID, text, "IAccessible2", FakeCellObject)
		same = TableHandle(document, document.tableID, 1, 1, 1, 1)
		self.assertTrue(held.holds(same))
		self.assertFalse(held.holds(TableHandle(document, 2, 1, 1, 1, 1)))
		self.assertTrue(flowTableSource.sameTable(same.key, same.key))


if __name__ == "__main__":
	unittest.main()
