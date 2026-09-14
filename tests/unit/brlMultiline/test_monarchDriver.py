# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The Monarch driver class: its lifecycle, its recovery, and what it does with a touch.

`test_monarchPins` covers the arithmetic — where a dot goes, which cell a pin belongs to —
and every one of those tests passed while the driver was failing to load, failing to release
its device, and routing to nothing at ten rows. That is the gap this file exists to close:
the consequential behaviour is in the class, not in the mapper.

What is exercised here, in order: opening and refusing to open, the descriptor checks that
authorise a raw report, composing a frame from state several threads write, glyph lifetime,
routing at a pitch the device does not know about, losing the device and getting it back,
and termination racing that recovery.

See `_monarchStubs` for what stands in for Windows, and for the one method that cannot run
without it.
"""

import unittest

from ._monarchStubs import (
	FakeButtonCap,
	FakeDataItem,
	FakeHid,
	FakeValueCap,
	bluetoothDrivers,
	detectionFails,
	hidDevices,
	hidOpenAttempts,
	installMonarchStubs,
	loadDriver,
	makeDriverClass,
	resetMonarchStubs,
	scheduling,
	tryPorts,
	usbDrivers,
)
from ._stubs import callAfterQueue, callLaterQueue, log
from ._virtualStubs import bgThread

installMonarchStubs()

import braille  # noqa: E402
from braille.brailleHandler import BrailleHandler  # noqa: E402
from brlMultilineMonarch import monarch  # noqa: E402
from brlMultilineVirtual import handover  # noqa: E402

driverModule = loadDriver()
MonarchDriver = makeDriverClass(driverModule)
CellGlyph = driverModule.CellGlyph
InputGesture = driverModule.InputGesture
PinBuffer = driverModule.PinBuffer


class MonarchTestCase(unittest.TestCase):
	"""Shared setup: fresh stubs, and a driver over a fake Monarch when one is wanted."""

	def setUp(self):
		resetMonarchStubs()
		self.drivers: list = []

	def tearDown(self):
		for driver in self.drivers:
			try:
				driver.terminate()
			except Exception:
				pass
		resetMonarchStubs()

	def makeDriver(self, device: FakeHid | None = None, port="auto") -> object:
		"""Construct a driver over one fake device.

		:param device: the device it will find, or None for a plain Monarch.
		:param port: as NVDA would pass it.
		:return: the driver, registered for teardown.
		"""
		hidDevices.append(device if device is not None else FakeHid())
		driver = MonarchDriver(port)
		self.drivers.append(driver)
		return driver

	def lastWrite(self, driver) -> bytes:
		""":return: the payload of the last pin report, without the report ID."""
		self.assertTrue(driver._dev.written, "nothing was written to the device")
		return driver._dev.written[-1][1:]

	def dotsOn(self, payload: bytes) -> set:
		""":return: every pin coordinate the payload raises."""
		on = set()
		for y in range(monarch.PIN_HEIGHT):
			for x in range(monarch.PIN_WIDTH):
				index = monarch.pinBitIndex(x, y)
				if payload[index // 8] & (1 << (index % 8)):
					on.add((x, y))
		return on


class TestOpening(MonarchTestCase):
	"""Construction, and what it does with a device it should not keep."""

	def test_opensAndTakesTheMonarchsShape(self):
		driver = self.makeDriver()
		self.assertEqual(8, driver.numRows)
		self.assertEqual(32, driver.numCols)
		self.assertEqual(256, driver.numCells)
		self.assertEqual("8row", driver.pitch)

	def test_registersForReleaseOnSwitch(self):
		"""NVDA builds the incoming driver first, so this one has to be let go of early."""
		driver = self.makeDriver()
		self.assertIn(driver.name, handover._releasingNames)
		driver.terminate()
		self.assertNotIn(driver.name, handover._releasingNames)

	def test_refusesAHidBrailleDisplayWithNoPinReport(self):
		"""A display listed by `check` but not a Monarch is refused, with a usable message."""
		with self.assertRaises(RuntimeError) as caught:
			self.makeDriver(FakeHid(pinCap=False))
		self.assertIn("no pin report", str(caught.exception))

	def test_letsGoOfTheDeviceWhenConstructionFails(self):
		"""The failure that used to cost every driver the display until NVDA restarted.

		Nothing can reach an instance whose constructor raised, so a handle it is still
		holding is held exclusively and forever.
		"""
		device = FakeHid(pinCap=False)
		with self.assertRaises(RuntimeError):
			self.makeDriver(device)
		self.assertEqual(1, device.closed)

	def test_retriesADeviceThatIsPresentButWillNotOpen(self):
		"""A display NVDA released moments ago can refuse to open and then open normally."""
		device = FakeHid()
		hidDevices.append(device)
		# Two ports, the first of which has no device waiting behind it.
		tryPorts[:] = [
			("serial", "com", "COM3", {}),
			(driverModule.bdDetect.ProtocolType.HID, "hid", r"\\?\hid#monarch", {}),
		]
		driver = MonarchDriver("auto")
		self.drivers.append(driver)
		self.assertIs(device, driver._dev)

	def test_neverWritesTheCellReports(self):
		"""The whole point: cells go into the pin buffer, not into the eight cell reports."""
		driver = self.makeDriver()
		driver.display([0x3F] * 256)
		self.assertEqual(0, getattr(driver, "cellReportsWritten", 0))
		self.assertEqual(monarch.PIN_REPORT_ID, driver._dev.written[-1][0])


class TestDescriptorChecks(MonarchTestCase):
	"""`_isPinCap`: what authorises writing 480 raw bytes to output report 0x21.

	Tested directly because the enumeration around it is Windows. Each case changes one
	field of a capability that would otherwise match, which is the dangerous shape: a
	display that nearly matches is one the driver must refuse rather than write to.
	"""

	def setUp(self):
		super().setUp()
		self.driver = self.makeDriver()

	def accepts(self, **fields) -> bool:
		return self.driver._isPinCap(FakeButtonCap(**fields), 481)

	def test_acceptsTheMonarchsOwnDeclaration(self):
		self.assertTrue(self.accepts())

	def test_acceptsTheSameArrayDeclaredAsAUsageRange(self):
		"""Both forms put the same bits in the same order, so either is usable."""
		self.assertTrue(self.accepts(isRange=True, reportCount=1))

	def test_refusesTheWrongUsage(self):
		"""A large output button array is not by itself a pin matrix."""
		self.assertFalse(self.accepts(usage=0x200))

	def test_refusesTheWrongReport(self):
		"""Writing a Monarch payload to whichever report happened to match is not a fix."""
		self.assertFalse(self.accepts(reportID=0x22))

	def test_refusesAnotherUsagePage(self):
		self.assertFalse(self.accepts(usagePage=0x0C))

	def test_refusesTheWrongNumberOfPins(self):
		self.assertFalse(self.accepts(reportCount=2048))

	def test_refusesAnAliasEntry(self):
		"""An alias re-describes fields another capability already covers."""
		self.assertFalse(self.accepts(isAlias=True))

	def test_refusesAReportTooSmallToHoldThePayload(self):
		self.assertFalse(self.driver._isPinCap(FakeButtonCap(), 64))

	def test_saysWhyItRefusedAPinSizedArray(self):
		"""A near miss is worth a note: it is what a bug report from real hardware needs."""
		log.messages.clear()
		self.accepts(usage=0x200)
		self.assertTrue(any("0x0200" in str(message) for message in log.messages))


class TestComposition(MonarchTestCase):
	"""Drawing a frame, and doing it from one consistent set of state."""

	def setUp(self):
		super().setUp()
		self.driver = self.makeDriver()

	def test_drawsCellsAtTheCurrentPitch(self):
		cells = [0] * 256
		cells[0] = 0x01  # dot 1
		cells[32] = 0x01  # dot 1, second line
		self.driver.display(cells)
		on = self.dotsOn(self.lastWrite(self.driver))
		self.assertIn((0, 0), on)
		# 8 rows: four dot rows and a blank one, so the next line starts five pins down.
		self.assertIn((0, 5), on)

	def test_tenRowsPacksTheLinesFourApart(self):
		self.driver.pitch = "10row"
		cells = [0] * 320
		cells[0] = 0x01
		cells[32] = 0x01
		self.driver.display(cells)
		on = self.dotsOn(self.lastWrite(self.driver))
		self.assertIn((0, 0), on)
		self.assertIn((0, 4), on)

	def test_tenRowsStillDrawsDotsSevenAndEight(self):
		"""Nothing is masked at any pitch: a caret has to show and an 8 dot table has to read."""
		self.driver.pitch = "10row"
		cells = [0] * 320
		cells[0] = 0xFF
		self.driver.display(cells)
		on = self.dotsOn(self.lastWrite(self.driver))
		self.assertIn((0, 3), on)
		self.assertIn((1, 3), on)

	def test_changingPitchTellsNvdaTheDisplayChangedShape(self):
		"""One read of `displayDimensions` is the whole mechanism. It used to call a method
		that does not exist, and the swallowed error left NVDA at 256 cells while we routed
		against 320."""
		handler = BrailleHandler()
		handler.display = self.driver
		braille.handler = handler
		self.driver.pitch = "10row"
		self.assertEqual(10, handler.displayDimensions.numRows)
		self.assertEqual(320, self.driver.numCells)

	def test_changingPitchDropsCellsLaidOutForTheOldOne(self):
		self.driver.display([0xFF] * 256)
		self.driver.pitch = "10row"
		self.assertEqual([], self.driver._lastCells)

	def test_overlaysAreDrawnOverTheText(self):
		overlay = PinBuffer(4, 4)
		overlay.rect(0, 0, 4, 4, filled=True)
		self.driver.display([0] * 256)
		self.driver.setGraphicsOverlay("chart", 40, 8, overlay)
		callAfterQueue.flush()
		bgThread.flush()
		on = self.dotsOn(self.lastWrite(self.driver))
		self.assertIn((40, 8), on)
		self.assertIn((43, 11), on)

	def test_severalOverlaysMakeOneWrite(self):
		"""Each write clatters and needs a hand lifted off, so a batch must not be a flicker."""
		self.driver.display([0] * 256)
		before = len(self.driver._dev.written)
		for index in range(4):
			self.driver.setGraphicsOverlay(f"o{index}", index * 8, 0, PinBuffer(2, 2))
		self.assertEqual(before, len(self.driver._dev.written), "an overlay wrote immediately")
		self.assertEqual(1, callAfterQueue.flush())
		self.assertEqual(1, bgThread.flush())
		self.assertEqual(before + 1, len(self.driver._dev.written))

	def test_aRepaintIsNotWrittenOnTheMainThread(self):
		"""A main thread waiting on the device is a frozen NVDA. On 2026-09-14 a hung Monarch
		held the write lock from the I/O thread and the main thread sat behind it in a repaint,
		so NVDA started and never spoke."""
		self.driver.display([0] * 256)
		before = len(self.driver._dev.written)
		self.driver.setGraphicsOverlay("chart", 0, 0, PinBuffer(2, 2))
		callAfterQueue.flush()
		self.assertEqual(before, len(self.driver._dev.written), "the main thread wrote")
		self.assertEqual(1, len(bgThread.queued))

	def test_aFramePublishesWhateverWasPending(self):
		"""A frame is about to be drawn anyway, so the deferred write would be a second one."""
		self.driver.setGraphicsOverlay("chart", 0, 0, PinBuffer(2, 2))
		self.driver.display([0] * 256)
		writes = len(self.driver._dev.written)
		callAfterQueue.flush()
		bgThread.flush()
		self.assertEqual(writes, len(self.driver._dev.written))

	def test_composingSurvivesAnOverlayAppearingMidFrame(self):
		"""Iterating the overlays while another thread inserts one used to raise
		`dictionary changed size during iteration`, and the panel simply would not update."""
		snapshot = self.driver._snapshot()
		self.driver._overlays["late"] = (0, 0, PinBuffer(2, 2))
		self.driver._compose(snapshot)  # must not raise

	def test_aSnapshotIsNotChangedByLaterWrites(self):
		self.driver.display([0x01] * 256)
		snapshot = self.driver._snapshot()
		self.driver.display([0xFF] * 256)
		pitch, cells, glyphs, overlays = snapshot
		self.assertEqual([0x01] * 256, cells)


class TestGlyphsOverARunOfCells(MonarchTestCase):
	"""A glyph is as wide as the text it stands in for.

	NVDA already writes short strings for roles and states — "btn", "cbo", three cells for a
	checkbox — and the useful thing a pin display can do with them is draw a symbol instead of
	spelling one. Replacing three cells with one would shift everything after it and break
	routing; replacing three cells with a nine by four drawing changes nothing but what those
	pins say.
	"""

	def setUp(self):
		super().setUp()
		self.driver = self.makeDriver()

	def wide(self, cells=("b", "t", "n")):
		""":return: a three cell glyph over three given cell values, drawn as a solid slab."""
		fallback = [0x03, 0x1E, 0x1D]
		return self.driver.newGlyph(["#########"] * 4, fallback), fallback

	def frame(self, fallback, at=0):
		""":return: a frame carrying a run of cells at an index, blank elsewhere."""
		cells = [0] * 256
		cells[at : at + len(fallback)] = fallback
		return cells

	def test_aRunOfCellsIsReplacedByOneShape(self):
		glyph, fallback = self.wide()
		self.driver.setCellGlyphs({0: glyph})
		self.driver.display(self.frame(fallback))
		on = self.dotsOn(self.lastWrite(self.driver))
		# Three slots of three columns, every pin of them, and nothing in the fourth slot.
		for column in range(9):
			self.assertIn((column, 0), on)
		self.assertNotIn((9, 0), on)

	def test_theCellAfterTheRunStartsWhereItAlwaysDid(self):
		"""The whole reason a glyph is the width of its text rather than narrower."""
		glyph, fallback = self.wide()
		cells = self.frame(fallback)
		cells[3] = 0x01
		self.driver.setCellGlyphs({0: glyph})
		self.driver.display(cells)
		self.assertIn((9, 0), self.dotsOn(self.lastWrite(self.driver)))

	def test_aRunIsRetiredWhenAnyCellOfItChanges(self):
		"""Not only the first. A caret or-ed into the last cell of "btn" means the reader is on
		it and needs the letters, and a symbol that ignored that would be hiding the caret."""
		glyph, fallback = self.wide()
		self.driver.setCellGlyphs({0: glyph})
		self.driver.display(self.frame(fallback))
		self.assertIn(0, self.driver._glyphs)
		changed = self.frame(fallback)
		changed[2] |= 0xC0
		self.driver.display(changed)
		self.assertEqual({}, self.driver._glyphs)

	def test_aRunSplitAcrossTheEndOfALineIsNotDrawn(self):
		"""The cells are still there and still say what they say, so the reader gets the text
		wrapped — which is what they would have got without glyphs at all. Drawing it anyway
		would paint one shape across two lines, which is the only bad answer available."""
		glyph, fallback = self.wide()
		self.driver.setCellGlyphs({30: glyph})
		self.driver.display(self.frame(fallback, at=30))
		on = self.dotsOn(self.lastWrite(self.driver))
		# The third column of the slot is the glyph's alone, and braille never reaches it.
		self.assertNotIn((30 * 3 + 2, 0), on)

	def test_glyphsThatWouldTreadOnEachOtherAreNotBothKept(self):
		"""Two symbols sharing a cell is a caller's mistake with no sensible rendering."""
		first, _fallback = self.wide()
		second, _also = self.wide()
		self.driver.setCellGlyphs({0: first, 1: second})
		self.assertEqual([0], list(self.driver._glyphs))

	def test_aRunThatDoesNotOverlapIsKept(self):
		first, fallback = self.wide()
		second, _also = self.wide()
		self.driver.setCellGlyphs({0: first, 3: second})
		self.assertEqual([0, 3], sorted(self.driver._glyphs))

	def test_aSingleValueIsStillAOneCellGlyph(self):
		"""The shape the driver was built with, and callers written against it keep working."""
		glyph = self.driver.newGlyph(["###", "###", "###", "..."], 0x3F)
		self.assertEqual(1, glyph.cells)
		self.assertEqual([0x3F], glyph.fallback)

class TestGlyphs(MonarchTestCase):
	"""A glyph fills the cell's gap column, and lives exactly as long as its content."""

	def setUp(self):
		super().setUp()
		self.driver = self.makeDriver()

	def makeGlyph(self, fallbackCell: int = 0x3F):
		return self.driver.newGlyph(["###", "###", "###", "..."], fallbackCell)

	def test_aGlyphFillsThreeColumnsWhereACellFillsTwo(self):
		self.assertEqual((3, 4), self.driver.glyphSize)
		self.assertEqual((2, 4), self.driver.cellSize)

	def test_aGlyphReplacesItsCell(self):
		cells = [0] * 256
		cells[0] = 0x3F
		self.driver.setCellGlyphs({0: self.makeGlyph()})
		self.driver.display(cells)
		on = self.dotsOn(self.lastWrite(self.driver))
		# The third column of the slot is the glyph's, and braille never reaches it.
		self.assertIn((2, 0), on)
		# The fourth row is the glyph's own blank, not the cell's dots 7 and 8.
		self.assertNotIn((0, 3), on)

	def test_aGlyphIsSkippedWhenTheCellNoLongerReadsIt(self):
		"""A caret or-ed in by NVDA means the reader needs the cell, not the symbol."""
		cells = [0] * 256
		cells[0] = 0x3F | 0xC0
		self.driver.setCellGlyphs({0: self.makeGlyph()})
		self.driver.display(cells)
		on = self.dotsOn(self.lastWrite(self.driver))
		self.assertNotIn((2, 0), on)
		self.assertIn((0, 3), on)

	def test_aFrameThatDoesNotMatchRetiresTheGlyph(self):
		"""Skipping alone left the registration standing, and some later frame with the same
		byte at the same index would be painted with a symbol from content long scrolled away."""
		matching = [0] * 256
		matching[0] = 0x3F
		self.driver.setCellGlyphs({0: self.makeGlyph()})
		self.driver.display(matching)
		self.assertIn(0, self.driver._glyphs)
		self.driver.display([0] * 256)
		self.assertEqual({}, self.driver._glyphs)
		# The coincidence that used to bring it back.
		self.driver.display(matching)
		on = self.dotsOn(self.lastWrite(self.driver))
		self.assertNotIn((2, 0), on)

	def test_aGlyphSurvivesRedrawsOfItsOwnFrame(self):
		cells = [0] * 256
		cells[0] = 0x3F
		self.driver.setCellGlyphs({0: self.makeGlyph()})
		self.driver.display(cells)
		self.driver.display(cells)
		self.assertIn(0, self.driver._glyphs)

	def test_changingPitchDropsGlyphs(self):
		"""Cell indexes name a different place once there are 320 of them instead of 256."""
		self.driver.setCellGlyphs({0: self.makeGlyph()})
		self.driver.pitch = "10row"
		self.assertEqual({}, self.driver._glyphs)


class TestTouchAndRouting(MonarchTestCase):
	"""What the panel reports when it is touched, and what a routing press then means."""

	TOUCH_INDEX = 5
	ROUTING_INDEX = 6

	LOW_PIN = 20 * monarch.PIN_WIDTH + 63 + 1
	"""A touch at pin (63, 20), one based as the panel reports it."""

	LOW_NATIVE_CELL = 4 * 32 + 21
	LOW_TEN_ROW_CELL = 5 * 32 + 21
	"""What that pin is on the device's own 8 by 32 grid, and what it is at ten rows. They
	differ, which is the whole reason routing needs correcting; a pin where they agreed
	would make every assertion below pass for the wrong reason."""

	def setUp(self):
		super().setUp()
		device = FakeHid(
			inputValueCaps=[
				FakeValueCap(monarch.TOUCH_PIN_USAGE, self.TOUCH_INDEX),
				FakeValueCap(monarch.ROUTING_USAGE_MIN + 33, self.ROUTING_INDEX),
			],
		)
		self.driver = self.makeDriver(device)

	def touch(self, pinValue: int, routingCell: int | None = None):
		"""Feed the driver the reports the panel actually sends, in the order it sends them.

		The pin arrives first, then the routing cell, then the pin again as zero on release.
		That order is why the pin has to be snapshotted at the press: by gesture time the
		live one has been cleared.
		"""
		items = [FakeDataItem(self.TOUCH_INDEX, rawValue=pinValue)]
		if routingCell is not None:
			self.driver._dev.inputValueCaps[1] = FakeValueCap(
				monarch.ROUTING_USAGE_MIN + routingCell,
				self.ROUTING_INDEX,
			)
			self.driver._inputUsages = None
			items.append(FakeDataItem(self.ROUTING_INDEX, on=True))
		self.driver._readTouch(items)

	def test_readsThePinNvdaDiscards(self):
		"""One of the six real touches, from the hardware session that established the decode.

		The device reported cell 85 alongside pin value 1120, and 1120 decodes to pin (63, 11)
		only under the plain row major reading — decoding it with the output report's block
		packing gives plausible nonsense, which is how the first version of this was wrong.
		"""
		self.touch(1120)
		self.assertEqual((63, 11), self.driver.lastTouch)
		self.assertEqual(85, monarch.nativeRoutingCell(63, 11))

	def test_aReleaseClearsTheTouch(self):
		self.touch(1120)
		self.touch(0)
		self.assertIsNone(self.driver.lastTouch)

	def test_prefersTheDevicesOwnCellAtTheNativePitch(self):
		"""Its algorithm knows more about which cell a fingertip meant than we do."""
		self.touch(1120, routingCell=85)
		self.assertEqual((2, 21), self.driver.lastTouchCell)

	def test_derivesTheCellFromThePinAtTenRows(self):
		"""Pin (63, 20): line 4 at eight rows, line 5 at ten. The device says the first
		whatever we are drawing, which is exactly the discrepancy that has to be corrected."""
		self.driver.pitch = "10row"
		self.touch(self.LOW_PIN, routingCell=self.LOW_NATIVE_CELL)
		self.assertEqual((5, 21), self.driver.lastTouchCell)

	def makeGesture(self, cellIndexes):
		self.driver.pendingCellIndexes = list(cellIndexes)
		return InputGesture(self.driver, [self.ROUTING_INDEX])

	def test_leavesRoutingAloneAtTheNativePitch(self):
		self.touch(1120, routingCell=85)
		gesture = self.makeGesture([85])
		self.assertFalse(gesture.cancelled)
		self.assertEqual([85], gesture.cellIndexes)

	def test_correctsRoutingForTenRows(self):
		"""The device names cells on its native 8 by 32 grid whatever we draw, so at ten rows
		it can only ever name 256 of 320 and the ones it names sit on the wrong lines."""
		self.driver.pitch = "10row"
		self.touch(self.LOW_PIN, routingCell=self.LOW_NATIVE_CELL)
		gesture = self.makeGesture([self.LOW_NATIVE_CELL])
		self.assertFalse(gesture.cancelled)
		self.assertEqual([self.LOW_TEN_ROW_CELL], gesture.cellIndexes)

	def test_cancelsRoutingAtTenRowsWithNoPin(self):
		"""Passing the device's index through was activating a cell chosen on a layout we are
		not drawing, which reads to a user as the display acting at random."""
		self.driver.pitch = "10row"
		self.driver._pinAtRouting = None
		gesture = self.makeGesture([85])
		self.assertTrue(gesture.cancelled)

	def test_cancelsARangeSelectionAtTenRows(self):
		"""One touched pin cannot re-base two endpoints."""
		self.driver.pitch = "10row"
		self.touch(self.LOW_PIN, routingCell=self.LOW_NATIVE_CELL)
		gesture = self.makeGesture([85, 86, 87])
		self.assertTrue(gesture.cancelled)

	def test_keepsARangeSelectionAtTheNativePitch(self):
		self.touch(1120, routingCell=85)
		gesture = self.makeGesture([85, 86, 87])
		self.assertFalse(gesture.cancelled)
		self.assertEqual([85, 86, 87], gesture.cellIndexes)

	def test_aCancelledGestureIsNotDispatched(self):
		self.driver.pitch = "10row"
		self.driver._pinAtRouting = None
		self.driver.pendingCellIndexes = [85]
		self.driver._keysDown = [self.ROUTING_INDEX]
		self.driver._ignoreKeyReleases = False
		import inputCore

		dispatched = []
		original = inputCore.manager.executeGesture
		inputCore.manager.executeGesture = dispatched.append
		try:
			self.driver._handleKeyRelease()
		finally:
			inputCore.manager.executeGesture = original
		self.assertEqual([], dispatched)

	def test_recordsTheDevicesOwnCellsRatherThanItsOwnAnswer(self):
		"""`cellIndexes` used to be recorded after the correction, so the field labelled
		"from the device" was the driver's answer read back to itself."""
		self.driver.pitch = "10row"
		self.touch(self.LOW_PIN, routingCell=self.LOW_NATIVE_CELL)
		self.makeGesture([self.LOW_NATIVE_CELL])
		recorded = self.driver.lastRouting
		self.assertEqual([self.LOW_NATIVE_CELL], recorded["cellsFromDevice"])
		self.assertEqual([self.LOW_TEN_ROW_CELL], recorded["dispatched"])
		self.assertEqual("replace", recorded["action"])

	def test_recordsACancelAsDispatchingNothing(self):
		self.driver.pitch = "10row"
		self.driver._pinAtRouting = None
		self.makeGesture([85])
		self.assertEqual("cancel", self.driver.lastRouting["action"])
		self.assertIsNone(self.driver.lastRouting["dispatched"])

	def test_theGestureNamesThisDriverAndKeepsTheStandardAliases(self):
		"""A Monarch reporting `hidBrailleStandard` matches no member of a virtual display,
		so routing indexes are never rebased and a pan can scroll the wrong segment."""
		gesture = self.makeGesture([85])
		identifiers = gesture._get_identifiers()
		self.assertTrue(identifiers[0].startswith("br(brlMultilineMonarch):"))
		self.assertTrue(any(i.startswith("br(hidBrailleStandard):") for i in identifiers))


class TestKeyNames(MonarchTestCase):
	"""The two d-pads told apart, and the zoom keys named, without losing a binding.

	Declared as the hardware reported them on 14 September 2026: up on the left pad is data
	index 18 under left controls, up on the right pad is data index 14 under right controls.
	"""

	LEFT_UP = 18
	RIGHT_UP = 14
	ZOOM_IN = 30
	SPACE = 9

	def setUp(self):
		super().setUp()
		caps = [
			self.cap(0x216, self.LEFT_UP, 3, 0x20D),
			self.cap(0x216, self.RIGHT_UP, 2, 0x20E),
			self.cap(0x209, self.SPACE, 4, 0x200),
			self.cap(0x220, self.ZOOM_IN, 5, 0x20F),
		]
		self.driver = self.makeDriver(FakeHid(inputButtonCaps=caps))

	@staticmethod
	def cap(usage, dataIndex, linkCollection, linkUsage):
		return FakeButtonCap(
			usage=usage,
			dataIndex=dataIndex,
			linkCollection=linkCollection,
			linkUsage=linkUsage,
			reportCount=1,
		)

	def press(self, dataIndices, nvdaNames):
		self.driver.pendingCellIndexes = []
		self.driver.pendingKeyNames = list(nvdaNames)
		return InputGesture(self.driver, dataIndices)

	def test_thePadsHaveTheirOwnNamesFirstAndNvdasAfter(self):
		gesture = self.press([self.RIGHT_UP], ["dpadUp"])
		self.assertEqual(
			[
				"br(brlMultilineMonarch):rightDpadUp",
				"br(brlMultilineMonarch):dpadUp",
				"br(hidBrailleStandard):dpadUp",
			],
			gesture._get_identifiers(),
		)
		leftUp = self.press([self.LEFT_UP], ["dpadUp"])._get_identifiers()
		self.assertEqual("br(brlMultilineMonarch):leftDpadUp", leftUp[0])

	def test_noStandardFormOfANameOfOurOwn(self):
		"""`hidBrailleStandard` never produces one, so it could only be a binding nobody made."""
		identifiers = self.press([self.RIGHT_UP], ["dpadUp"])._get_identifiers()
		self.assertNotIn("br(hidBrailleStandard):rightDpadUp", identifiers)

	def test_aChordKeepsBothForms(self):
		identifiers = self.press([self.SPACE, self.RIGHT_UP], ["space", "dpadUp"])._get_identifiers()
		self.assertEqual("br(brlMultilineMonarch):space+rightDpadUp", identifiers[0])
		self.assertIn("br(brlMultilineMonarch):space+dpadUp", identifiers)
		self.assertIn("br(hidBrailleStandard):space+dpadUp", identifiers)

	def test_theZoomKeyKeepsItsNumberForExistingBindings(self):
		"""The author's own gesture map pages table columns with `brailleusage544`."""
		identifiers = self.press([self.ZOOM_IN], ["brailleUsage544"])._get_identifiers()
		self.assertEqual("br(brlMultilineMonarch):zoomIn", identifiers[0])
		self.assertIn("br(hidBrailleStandard):brailleUsage544", identifiers)

	def test_aGestureWithNothingToNameIsAsBefore(self):
		identifiers = self.press([self.SPACE], ["space"])._get_identifiers()
		self.assertEqual(["br(brlMultilineMonarch):space", "br(hidBrailleStandard):space"], identifiers)

	def test_namesThatDoNotLineUpRenameNothing(self):
		"""If NVDA's constructor ever names keys differently, the key keeps NVDA's name alone."""
		gesture = self.press([self.RIGHT_UP], ["somethingElse"])
		self.assertIsNone(gesture._nvdaId)
		self.assertEqual("br(brlMultilineMonarch):somethingElse", gesture._get_identifiers()[0])

	def test_aReconnectNamesTheNewDevicesKeys(self):
		"""Data indexes belong to a descriptor; names from the old device would be the old numbering."""
		self.assertIn(self.RIGHT_UP, self.driver._keyNames)
		hidDevices.append(FakeHid(inputButtonCaps=[]))
		self.driver._reopenDevice()
		self.assertEqual({}, self.driver._keyNames)

	def test_aNamingFailureCostsNamesNotKeys(self):
		original = driverModule.keyNames.declarationsFromCaps

		def broken(caps):
			raise ValueError("an unreadable descriptor")

		driverModule.keyNames.declarationsFromCaps = broken
		try:
			buttons = self.driver._collectInputButtonCapsByDataIndex()
		finally:
			driverModule.keyNames.declarationsFromCaps = original
		self.assertIn(self.RIGHT_UP, buttons)
		self.assertEqual({}, self.driver._keyNames)
		errors = [message for level, message in log.messages if level == "error"]
		self.assertTrue(any("could not name" in message for message in errors))

	def test_aDescriptorThatCannotBeReadLeavesNoOldNames(self):
		"""The keys of the previous device must not keep names on a device whose keys are unknown."""
		self.driver._dev.inputButtonCaps = [object()]
		self.driver._resetInputSession()
		self.assertEqual({}, self.driver._keyNames)


class TestReconnection(MonarchTestCase):
	"""Losing the device and getting it back, without NVDA ever being told."""

	def setUp(self):
		super().setUp()
		self.driver = self.makeDriver()

	def test_aFailedWriteStartsRecovery(self):
		self.driver._dev.writeFails = True
		self.driver.display([0] * 256)
		self.assertTrue(self.driver._reopening)
		self.assertEqual(1, len(callLaterQueue.pending))

	def test_aWriteTheDeviceNeverTakesStartsRecovery(self):
		"""Rather than waiting for it forever with the write lock held, which is what froze
		NVDA on 2026-09-14."""
		self.driver._dev.writeHangs = True
		self.driver.display([0] * 256)
		self.assertTrue(self.driver._reopening)
		self.assertEqual(1, len(callLaterQueue.pending))
		self.assertTrue(
			any("did not take a pin report" in message for level, message in log.messages if level == "warning"),
			log.messages,
		)

	def test_aDeviceThatReopensButTakesNoWritesIsTriedLessAndLessOften(self):
		"""A hung Monarch opens at once. Counting that as recovered reopened it every five
		seconds, each time holding the I/O thread for a whole write timeout."""
		self.driver._dev.writeHangs = True
		self.driver.display([0] * 256)
		delays = []
		for _ in range(5):
			delays.append(callLaterQueue.pending[0].milliseconds)
			hidDevices.append(FakeHid(writeHangs=True))
			callLaterQueue.fire()
			bgThread.flush()
		self.assertEqual([5000, 10000, 20000, 40000, 60000], delays)

	def test_nothingMoreIsWrittenToADeviceThatHasJustHung(self):
		"""Each frame and repaint already queued would hold the I/O thread for another whole
		timeout, and every other display with it."""
		device = self.driver._dev
		device.writeHangs = True
		self.driver.display([0] * 256)
		for index in range(3):
			self.driver.display([index + 1] * 256)
			self.driver.setGraphicsOverlay(f"o{index}", 0, 0, PinBuffer(2, 2))
			callAfterQueue.flush()
			bgThread.flush()
		self.assertEqual(1, device.writeAttempts)

	def test_whatWasAskedForWhileSuspendedIsDrawnOnReconnecting(self):
		"""Dropping the writes loses nothing: the state is kept, and the repaint draws it."""
		self.driver.display([0x3F] * 256)
		wanted = self.dotsOn(self.lastWrite(self.driver))
		self.driver.display([0] * 256)
		self.driver._dev.writeHangs = True
		self.driver.display([1] * 256)
		self.driver.display([0x3F] * 256)
		replacement = FakeHid()
		hidDevices.append(replacement)
		callLaterQueue.fire()
		bgThread.flush()
		self.assertEqual(1, len(replacement.written))
		self.assertEqual(wanted, self.dotsOn(replacement.written[-1][1:]))

	def test_onlyOneWriteProbesAReopenedDevice(self):
		self.driver._dev.writeHangs = True
		self.driver.display([0] * 256)
		replacement = FakeHid(writeHangs=True)
		hidDevices.append(replacement)
		callLaterQueue.fire()
		self.driver.display([1] * 256)
		self.driver.display([2] * 256)
		bgThread.flush()
		self.assertEqual(1, replacement.writeAttempts)

	def test_writingResumesWhenNothingCanReconnect(self):
		"""A suspension nothing will end would leave the panel dead for good. The next write
		failing is what tries to schedule recovery again."""
		scheduling.fails = True
		device = self.driver._dev
		device.writeFails = True
		self.driver.display([0] * 256)
		self.driver.display([1] * 256)
		self.assertEqual(2, device.writeAttempts)

	def test_reconnectingStopsWhenWindowsHoldsTooManyWrites(self):
		"""Every probe of a device that ignores cancellation can strand another write."""
		realOutstanding = driverModule.hidWrite.outstanding
		driverModule.hidWrite.outstanding = lambda: driverModule.ABANDONED_WRITE_LIMIT
		self.addCleanup(setattr, driverModule.hidWrite, "outstanding", realOutstanding)
		device = self.driver._dev
		device.writeHangs = True
		self.driver.display([0] * 256)
		self.assertFalse(self.driver._reopening)
		self.assertEqual([], callLaterQueue.pending)
		self.driver.display([1] * 256)
		self.assertEqual(1, device.writeAttempts, "a device past the limit was written to again")
		errors = [message for level, message in log.messages if level == "error"]
		self.assertTrue(any("Unplug it" in message for message in errors), errors)

	def test_aWriteThatGetsThroughMakesTheNextLossQuickToRecover(self):
		self.driver._dev._onReadError(1167)
		hidDevices.append(FakeHid())
		callLaterQueue.fire()
		bgThread.flush()
		self.driver._dev._onReadError(1167)
		self.assertEqual(5000, callLaterQueue.pending[0].milliseconds)

	def test_aReadErrorIsReportedAsHandledOnlyWhenSomethingIsRetrying(self):
		"""Inside a virtual display, False is taken at its word: the member is dropped and
		the in place reconnection this driver promises never gets its chance."""
		self.assertTrue(self.driver._dev._onReadError(1167))
		self.assertTrue(self.driver._reopening)

	def test_aReadErrorIsNotHandledWhenNothingCanBeScheduled(self):
		"""And `_reopening` is cleared again, or every later loss answers "handled" at once
		with nothing behind it."""
		scheduling.fails = True
		self.assertFalse(self.driver._dev._onReadError(1167))
		self.assertFalse(self.driver._reopening)

	def test_retriesWithAGrowingWait(self):
		self.driver._dev._onReadError(1167)
		delays = []
		for _ in range(4):
			delays.append(callLaterQueue.pending[0].milliseconds)
			callLaterQueue.fire()
		self.assertEqual([5000, 10000, 20000, 40000], delays)

	def test_theWaitStopsGrowingAtTheLimit(self):
		self.driver._dev._onReadError(1167)
		for _ in range(12):
			callLaterQueue.fire()
		self.assertEqual(60000, callLaterQueue.pending[0].milliseconds)

	def test_reopensAndRedrawsWhatWasOnThePanel(self):
		self.driver.display([0x3F] * 256)
		wanted = self.dotsOn(self.lastWrite(self.driver))
		self.driver._dev._onReadError(1167)
		replacement = FakeHid()
		hidDevices.append(replacement)
		callLaterQueue.fire()
		self.assertIs(replacement, self.driver._dev)
		self.assertFalse(self.driver._reopening)
		self.assertEqual([], replacement.written, "the reconnect timer wrote on the main thread")
		bgThread.flush()
		self.assertEqual(wanted, self.dotsOn(self.lastWrite(self.driver)))

	def test_closesTheDeadHandleBeforeOpeningAnother(self):
		old = self.driver._dev
		old._onReadError(1167)
		hidDevices.append(FakeHid())
		callLaterQueue.fire()
		self.assertEqual(1, old.closed)

	def test_refusesAReplacementWithNoPinReport(self):
		"""Some other HID braille display answering on the same path is not a Monarch."""
		self.driver._dev._onReadError(1167)
		wrong = FakeHid(pinCap=False)
		hidDevices.append(wrong)
		callLaterQueue.fire()
		self.assertEqual(1, wrong.closed)
		self.assertTrue(self.driver._reopening)

	def test_reconnectionIsANewInputSession(self):
		"""A release arriving afterwards would otherwise complete a combination begun on a
		device that is gone, and fire whatever it is bound to."""
		old = self.driver._dev
		self.driver._keysDown = [4]
		self.driver._ignoreKeyReleases = True
		self.driver._lastTouchPin = (10, 10)
		self.driver._lastTouchCell = 85
		self.driver._pinAtRouting = (10, 10)
		old._onReadError(1167)
		replacement = FakeHid(inputButtonCaps=[FakeButtonCap(dataIndex=9)])
		hidDevices.append(replacement)
		callLaterQueue.fire()
		self.assertEqual(set(), self.driver._keysDown)
		self.assertFalse(self.driver._ignoreKeyReleases)
		self.assertIsNone(self.driver._lastTouchPin)
		self.assertIsNone(self.driver._lastTouchCell)
		self.assertIsNone(self.driver._pinAtRouting)
		self.assertIsNone(self.driver._inputUsages)

	def test_rebuildsTheButtonMapFromTheNewDevice(self):
		"""Data indexes belong to a device's report descriptor. Keeping the old map means
		decoding the new device's reports with the old device's key numbering."""
		self.driver._dev._onReadError(1167)
		replacement = FakeHid(inputButtonCaps=[FakeButtonCap(dataIndex=9)])
		hidDevices.append(replacement)
		callLaterQueue.fire()
		self.assertEqual({9: replacement}, self.driver._inputButtonCapsByDataIndex)

	def test_dropsInputArrivingBeforeTheHandleIsInstalled(self):
		"""`hwIo.hid.Hid` starts reading the instant it is constructed, so a key pressed as
		the link comes back can arrive with no device to decode it against."""
		self.driver._dev = None
		self.driver._hidOnReceive(b"\x00")  # must not raise

	def test_aSecondLossWhileRecoveringChangesNothing(self):
		self.driver._dev._onReadError(1167)
		pending = len(callLaterQueue.pending)
		self.assertTrue(self.driver._onDeviceLost())
		self.assertEqual(pending, len(callLaterQueue.pending))

	def test_aStaleTimerCannotReopenTheDevice(self):
		"""Off the main thread `core.callLater` hands back nothing to cancel, so a tick from
		a previous disconnect can still arrive. It has to be discarded by generation."""
		scheduling.returnsNothing = True
		self.driver._dev._onReadError(1167)
		stale = callLaterQueue.pending[0]
		self.driver._stopPolling()
		self.driver._reopening = False
		hidDevices.append(FakeHid())
		stale.run()
		self.assertEqual([], hidOpenAttempts[1:])


class TestTerminating(MonarchTestCase):
	"""Shutdown, including the interleavings that leak an exclusive handle."""

	def test_closesTheDevice(self):
		driver = self.makeDriver()
		device = driver._dev
		driver.terminate()
		self.assertEqual(1, device.closed)

	def test_terminatesCleanlyWhenTheDeviceHasAlreadyGone(self):
		"""The inherited path closes `_dev` unconditionally and would raise on None."""
		driver = self.makeDriver()
		driver._dev = None
		driver.terminate()  # must not raise

	def test_blankingTheDisplayDoesNotStartAReconnection(self):
		"""Shutdown is exactly when a stale timer can start work that outlives the driver."""
		driver = self.makeDriver()
		driver._dev.writeFails = True
		driver.terminate()
		self.assertFalse(driver._reopening)
		self.assertEqual([], callLaterQueue.pending)

	def test_stopsThePollTimer(self):
		driver = self.makeDriver()
		driver._dev._onReadError(1167)
		driver.terminate()
		self.assertEqual([], callLaterQueue.pending)

	def test_terminationDuringAReopenDoesNotLeakTheNewHandle(self):
		"""The race worth a lock. `_poll` checks the flag, `terminate` then runs and finds no
		device to close, and the reopen installs a handle nothing can ever reach — which
		holds the hardware away from every driver until NVDA restarts."""
		driver = self.makeDriver()
		driver._dev._onReadError(1167)
		replacement = FakeHid()

		# Terminate at the moment the candidate is open and about to be installed.
		original = driver._findPinCap

		def terminateMidOpen(device):
			cap = original(device)
			driver.terminate()
			return cap

		driver._findPinCap = terminateMidOpen
		hidDevices.append(replacement)
		self.assertFalse(driver._reopenDevice())
		self.assertEqual(1, replacement.closed)
		self.assertIsNone(driver._dev)

	def test_aReopenFinishingAfterTerminationIsClosedAnyway(self):
		"""The other order: the handle is installed a moment before the flag goes up."""
		driver = self.makeDriver()
		device = driver._dev
		driver._terminated = True
		driver.terminate()
		self.assertIsNone(driver._dev)
		self.assertGreaterEqual(device.closed, 1)

	def test_terminatingTwiceIsHarmless(self):
		driver = self.makeDriver()
		driver.terminate()
		driver.terminate()


class TestBeingOffered(MonarchTestCase):
	"""Being listed at all, and being given a port to choose. Both were missing."""

	def test_isListedWhenAHidBrailleDisplayIsAttached(self):
		"""`getDisplayList` drops any driver whose `check` is False, which is why this driver
		appeared in the virtual display's member list and nowhere else."""
		usbDrivers.append(("hidBrailleStandard", object()))
		self.assertTrue(MonarchDriver.check())

	def test_isNotListedWithNothingAttached(self):
		self.assertFalse(MonarchDriver.check())

	def test_survivesDetectionThatRaises(self):
		"""Both lookups raise `LookupError` for a driver with no detection data, and HID
		braille has none: its matching is special cased inside `bdDetect`."""
		detectionFails.update({"usb", "bluetooth"})
		self.assertFalse(MonarchDriver.check())

	def test_offersBothTransports(self):
		"""The Monarch is used over USB as well as Bluetooth; dropping either would be a
		regression against the standard driver."""
		usbDrivers.append(("hidBrailleStandard", object()))
		bluetoothDrivers.append(("hidBrailleStandard", object()))
		ports = MonarchDriver.getPossiblePorts()
		self.assertEqual(["auto", "usb", "bluetooth"], list(ports))

	def test_offersOnlyTheTransportThatIsThere(self):
		bluetoothDrivers.append(("hidBrailleStandard", object()))
		self.assertEqual(["auto", "bluetooth"], list(MonarchDriver.getPossiblePorts()))

	def test_offersNothingWithNoDevice(self):
		self.assertEqual([], list(MonarchDriver.getPossiblePorts()))

	def test_ignoresDevicesFiledUnderOtherDrivers(self):
		usbDrivers.append(("brailleNote", object()))
		self.assertFalse(MonarchDriver.check())

	def test_offersTheRowSetting(self):
		"""It was wrapped in a `try` that could have swallowed the import and left the
		control silently absent from NVDA's braille settings."""
		driver = self.makeDriver()
		self.assertEqual(["pitch"], [setting.id for setting in driver.supportedSettings])
		self.assertEqual({"8row", "10row"}, set(driver.availablePitchs))


if __name__ == "__main__":
	unittest.main()
