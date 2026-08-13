# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the virtual display's geometry and configuration parsing.

Written against the two displays the project targets: a Humanware Monarch (8 rows of 32)
and a Freedom Scientific Focus 80 (1 row of 80). Stacked, those give a 9 by 80 composite in
which the Monarch's band has 48 dead columns per row — the case the whole module exists to
describe honestly.
"""

import unittest

from ._virtualStubs import installVirtualStubs

installVirtualStubs()

from brlMultilineVirtual.virtualLayout import (  # noqa: E402
	DEFAULT_PORT,
	DeviceBand,
	DeviceSpec,
	deadColumnCount,
	deviceCellIndexToVirtual,
	formatDeviceSpec,
	parseDeviceSpec,
	parseDeviceSpecs,
	sliceBandCells,
	stackDevices,
)

MONARCH = (8, 32)
FOCUS = (1, 80)


class TestParseDeviceSpec(unittest.TestCase):
	def test_driverNameAloneDetectsAfresh(self):
		self.assertEqual(parseDeviceSpec("freedomScientific"), DeviceSpec("freedomScientific", DEFAULT_PORT))

	def test_explicitPort(self):
		self.assertEqual(parseDeviceSpec("hims|COM3"), DeviceSpec("hims", "COM3"))

	def test_surroundingSpaceIsIgnored(self):
		self.assertEqual(parseDeviceSpec("  hims | COM3 "), DeviceSpec("hims", "COM3"))

	def test_emptyPortFallsBackToDefault(self):
		self.assertEqual(parseDeviceSpec("hims|"), DeviceSpec("hims", DEFAULT_PORT))

	def test_rejectsMissingDriverName(self):
		with self.assertRaises(ValueError):
			parseDeviceSpec("|COM3")

	def test_rejectsExtraSeparators(self):
		with self.assertRaises(ValueError):
			parseDeviceSpec("hims|COM3|extra")


class TestFormatDeviceSpec(unittest.TestCase):
	def test_defaultPortIsNotWritten(self):
		self.assertEqual(formatDeviceSpec(DeviceSpec("hims", DEFAULT_PORT)), "hims")

	def test_roundTrip(self):
		for spec in (DeviceSpec("hims", DEFAULT_PORT), DeviceSpec("hims", "COM3")):
			with self.subTest(spec=spec):
				self.assertEqual(parseDeviceSpec(formatDeviceSpec(spec)), spec)


class TestParseDeviceSpecs(unittest.TestCase):
	def test_ordersAsGiven(self):
		specs = parseDeviceSpecs(["hidBrailleStandard", "freedomScientific"])
		self.assertEqual([spec.driverName for spec in specs], ["hidBrailleStandard", "freedomScientific"])

	def test_blankEntriesAreSkipped(self):
		self.assertEqual(parseDeviceSpecs(["hims", "", "   "]), [DeviceSpec("hims", DEFAULT_PORT)])

	def test_emptyListIsAllowed(self):
		self.assertEqual(parseDeviceSpecs([]), [])

	def test_rejectsTwoDisplaysOfOneDriver(self):
		"""Confirmed unworkable on hardware in Phase 0, so refused where it is written."""
		with self.assertRaises(ValueError) as caught:
			parseDeviceSpecs(["freedomScientific", "freedomScientific|COM3"])
		self.assertIn("freedomScientific", str(caught.exception))

	def test_rejectsMalformedEntry(self):
		with self.assertRaises(ValueError):
			parseDeviceSpecs(["hims", "|COM3"])


class TestStackDevices(unittest.TestCase):
	def test_monarchAboveFocus(self):
		geometry = stackDevices([MONARCH, FOCUS])
		self.assertEqual((geometry.numRows, geometry.numCols), (9, 80))
		self.assertEqual(
			geometry.bands,
			(
				DeviceBand(rowStart=0, numRows=8, numCols=32),
				DeviceBand(rowStart=8, numRows=1, numCols=80),
			),
		)

	def test_focusAboveMonarch(self):
		geometry = stackDevices([FOCUS, MONARCH])
		self.assertEqual((geometry.numRows, geometry.numCols), (9, 80))
		self.assertEqual(geometry.bands[1], DeviceBand(rowStart=1, numRows=8, numCols=32))

	def test_singleDeviceIsUnchanged(self):
		geometry = stackDevices([MONARCH])
		self.assertEqual((geometry.numRows, geometry.numCols), (8, 32))
		self.assertEqual(geometry.displaySize, 256)

	def test_equalWidthsStackCleanly(self):
		geometry = stackDevices([(1, 40), (1, 40)])
		self.assertEqual((geometry.numRows, geometry.numCols), (2, 40))
		self.assertEqual(deadColumnCount(geometry), 0)

	def test_rejectsNoDevices(self):
		with self.assertRaises(ValueError):
			stackDevices([])

	def test_rejectsDeviceWithNoCells(self):
		with self.assertRaises(ValueError):
			stackDevices([MONARCH, (1, 0)])


class TestDeadColumnCount(unittest.TestCase):
	def test_narrowBandLosesItsRemainder(self):
		# The Monarch's 8 rows are 48 columns short of the Focus's 80.
		self.assertEqual(deadColumnCount(stackDevices([MONARCH, FOCUS])), 8 * 48)

	def test_noneWhenWidthsMatch(self):
		self.assertEqual(deadColumnCount(stackDevices([(4, 32), (2, 32)])), 0)


class TestSliceBandCells(unittest.TestCase):
	"""A composite whose every cell holds its own index, so a slice is self describing."""

	def setUp(self):
		self.geometry = stackDevices([MONARCH, FOCUS])
		self.cells = list(range(self.geometry.displaySize))

	def test_wideBandTakesWholeRows(self):
		focusBand = self.geometry.bands[1]
		cells = sliceBandCells(self.cells, self.geometry.numCols, focusBand)
		self.assertEqual(len(cells), 80)
		# The Focus is the ninth row, so its cells run from 8 * 80.
		self.assertEqual(cells, list(range(640, 720)))

	def test_narrowBandDropsTheDeadColumns(self):
		monarchBand = self.geometry.bands[0]
		cells = sliceBandCells(self.cells, self.geometry.numCols, monarchBand)
		self.assertEqual(len(cells), 256)
		# Row 0 takes the first 32 of 80, row 1 restarts at 80, and so on.
		self.assertEqual(cells[:32], list(range(0, 32)))
		self.assertEqual(cells[32:64], list(range(80, 112)))
		self.assertEqual(cells[-32:], list(range(560, 592)))

	def test_singleDeviceIsAStraightCopy(self):
		geometry = stackDevices([MONARCH])
		cells = list(range(geometry.displaySize))
		self.assertEqual(sliceBandCells(cells, geometry.numCols, geometry.bands[0]), cells)

	def test_rejectsShortCompositeArray(self):
		with self.assertRaises(ValueError):
			sliceBandCells(self.cells[:-1], self.geometry.numCols, self.geometry.bands[1])

	def test_rejectsBandWiderThanTheComposite(self):
		with self.assertRaises(ValueError):
			sliceBandCells(self.cells, 32, DeviceBand(rowStart=0, numRows=1, numCols=80))


class TestDeviceCellIndexToVirtual(unittest.TestCase):
	"""Checked against the cell indexes a real Monarch reported during the Phase 0 spike."""

	def setUp(self):
		self.geometry = stackDevices([MONARCH, FOCUS])
		self.monarch = self.geometry.bands[0]
		self.focus = self.geometry.bands[1]

	def translate(self, index, band):
		return deviceCellIndexToVirtual(index, band, self.geometry.numCols)

	def test_phase0Indexes(self):
		# 2 is row 0 column 2; 39 is row 1 column 7; 102 is row 3 column 6.
		self.assertEqual(self.translate(2, self.monarch), 2)
		self.assertEqual(self.translate(39, self.monarch), 1 * 80 + 7)
		self.assertEqual(self.translate(102, self.monarch), 3 * 80 + 6)

	def test_bandOffsetIsApplied(self):
		geometry = stackDevices([FOCUS, MONARCH])
		monarch = geometry.bands[1]
		# The same press, one row lower because the Focus is above it now.
		self.assertEqual(deviceCellIndexToVirtual(102, monarch, geometry.numCols), 4 * 80 + 6)

	def test_singleRowBandIsAnOffset(self):
		self.assertEqual(self.translate(0, self.focus), 8 * 80)
		self.assertEqual(self.translate(79, self.focus), 8 * 80 + 79)

	def test_rejectsIndexOutsideTheDevice(self):
		for index in (-1, 256):
			with self.subTest(index=index), self.assertRaises(ValueError):
				self.translate(index, self.monarch)


if __name__ == "__main__":
	unittest.main()
