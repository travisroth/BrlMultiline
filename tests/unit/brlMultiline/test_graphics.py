# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the graphics seam, the cell to pin transform, and the graphics mode.

Two things here are worth more than the rest, and they are the two the plan called the risk.

The first is the **composite**. `braille.handler.display` is not the display that can draw
when the add-on's own driver is in use, and the composite forwards driver settings and not
driver methods. So the drawable member has to be found among its slots and addressed
directly, and its rows offset by where its band sits. Every offset here is checked in both
directions, because a drawing placed one band too low lands on someone else's display and
looks exactly like a drawing that did not appear.

The second is the **pitch**. A braille line is five pin rows where a display leaves a blank
row between lines and four where it does not, and neither number is published. It is derived
from the pin grid divided by the cell grid, which is why both pitches are tested against the
same 96 by 40 panel rather than against a constant.
"""

import os
import sys
import types
import unittest

from ._stubs import (
	fakeVirtualDisplay,
	flashedMessages,
	installStubs,
	runQueuedFunctions,
)

installStubs()

import braille  # noqa: E402

sys.path.insert(
	0,
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineMonarch",
	),
)

from pinBuffer import PinBuffer  # noqa: E402

from brlMultiline.graphics import PinRect, findSurface  # noqa: E402
from brlMultiline.graphicsMode import (  # noqa: E402
	FIT,
	MAX_ZOOM_STEP,
	OVERLAY_KEY,
	PANEL_NAME,
	Drawing,
	GraphicsMode,
	GraphicsRoutingPolicy,
	testFigure,
)
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import (  # noqa: E402
	PRIORITY_FLOW,
	PRIORITY_GRAPHICS,
	PRIORITY_ORDINARY,
	FlowPanel,
	GraphicsPanel,
	RowsPanel,
	SinglePanel,
)
from brlMultiline.views import SegmentView  # noqa: E402

PIN_WIDTH = 96
PIN_HEIGHT = 40


class FakeDrawableDriver:
	"""A display that can raise individual pins, shaped the way `graphics` reads one.

	Only what the capability check looks for and what the transform needs. Deliberately not
	the Monarch driver: the point of the duck typing is that a second display would work, and
	a test that used the real driver would not show that.
	"""

	def __init__(self, name="fakeMonarch", numRows=8, numCols=32):
		self.name = name
		self.numRows = numRows
		self.numCols = numCols
		self.graphicsSize = (PIN_WIDTH, PIN_HEIGHT)
		self.lastTouch = None
		"""The live touch, which is zero the moment the finger lifts.

		Kept because the driver publishes it, and left unused by the mode for exactly the
		reason the mode's routing policy explains: nothing above the driver can ever read it
		in time.
		"""
		self.lastRoutingPin = None
		"""The pin under the finger at the last routing press. This is the one that lasts.
		"""
		self.overlays = {}
		"""What has been shown, by key, as (x, y, buffer)."""
		self.cleared = []
		"""Every key passed to `clearGraphicsOverlay`, None included."""

	def newGraphicsBuffer(self, width=None, height=None):
		return PinBuffer(
			PIN_WIDTH if width is None else width,
			PIN_HEIGHT if height is None else height,
		)

	def setGraphicsOverlay(self, key, x, y, buffer):
		self.overlays[key] = (x, y, buffer)

	def clearGraphicsOverlay(self, key=None):
		self.cleared.append(key)
		if key is None:
			self.overlays.clear()
		else:
			self.overlays.pop(key, None)


class FakePlainDriver:
	"""A display that cannot draw. The ordinary case, and it must not be mistaken for one."""

	def __init__(self, name="freedomScientific", numRows=1, numCols=80):
		self.name = name
		self.numRows = numRows
		self.numCols = numCols


class FakePlugin:
	"""Just enough plugin for the mode to claim and give back a rectangle."""

	def __init__(self, refuse=False):
		self.panels = []
		self.refuse = refuse
		"""Whether a claim is refused, as one the display cannot honour would be."""

	def activatePanel(self, panel):
		if self.refuse:
			raise LookupError("no focus segment offered")
		self.panels = [each for each in self.panels if each.name != panel.name]
		self.panels.append(panel)

	def deactivatePanel(self, name):
		self.panels = [each for each in self.panels if each.name != name]


def useDisplay(display):
	"""Put a display behind `braille.handler`, the way the add-on finds it."""
	braille.handler = types.SimpleNamespace(display=display)


class TestFindingTheSurface(unittest.TestCase):
	def setUp(self):
		useDisplay(None)

	def test_aDisplayDrivenDirectlyIsFoundWithNoOffset(self):
		driver = FakeDrawableDriver()
		useDisplay(driver)
		surface = findSurface()
		self.assertIsNotNone(surface)
		self.assertIs(surface.driver, driver)
		self.assertEqual(surface.rowStart, 0)
		self.assertEqual((surface.pinWidth, surface.pinHeight), (PIN_WIDTH, PIN_HEIGHT))

	def test_aDisplayThatCannotDrawIsNotASurface(self):
		useDisplay(FakePlainDriver())
		self.assertIsNone(findSurface())

	def test_noDisplayAtAllIsNotAnError(self):
		useDisplay(None)
		self.assertIsNone(findSurface())

	def test_theDrawableMemberOfACompositeIsFoundBehindIt(self):
		monarch = FakeDrawableDriver()
		focus = FakePlainDriver()
		display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("fakeMonarch", 1, 8, 32),
			drivers={"freedomScientific": focus, "fakeMonarch": monarch},
		)
		useDisplay(display)
		surface = findSurface()
		self.assertIsNotNone(surface)
		self.assertIs(surface.driver, monarch)

	def test_theMembersBandIsWhereItsRowsStart(self):
		monarch = FakeDrawableDriver()
		display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("fakeMonarch", 1, 8, 32),
			drivers={"freedomScientific": FakePlainDriver(), "fakeMonarch": monarch},
		)
		useDisplay(display)
		self.assertEqual(findSurface().rowStart, 1)

	def test_aMemberGivenUpOnIsSkipped(self):
		"""Its rows have been taken out of the composite, so a drawing against its band would
		land on rows that now belong to another display."""
		monarch = FakeDrawableDriver()
		display = fakeVirtualDisplay(
			("fakeMonarch", 0, 8, 32),
			drivers={"fakeMonarch": monarch},
		)
		display.slots[0].failed = True
		useDisplay(display)
		self.assertIsNone(findSurface())

	def test_aCompositeOfDisplaysThatCannotDrawIsNotASurface(self):
		display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			drivers={"freedomScientific": FakePlainDriver()},
		)
		useDisplay(display)
		self.assertIsNone(findSurface())

	def test_aDisplayNamingItselfOursButNotShapedLikeItIsRefusedQuietly(self):
		useDisplay(types.SimpleNamespace(name="brlMultilineVirtual"))
		self.assertIsNone(findSurface())

	def test_aDisplayThatWillNotSayItsSizeIsNotASurface(self):
		driver = FakeDrawableDriver()
		del driver.graphicsSize
		driver.graphicsSize = property(lambda self: 1 / 0)
		useDisplay(driver)
		# The attribute exists, so the capability check passes and reading it fails, which is
		# the shape of a driver part way through a reconnection.
		self.assertIsNone(findSurface())

	def test_aDisplayReportingNoRowsIsRefused(self):
		useDisplay(FakeDrawableDriver(numRows=0))
		self.assertIsNone(findSurface())


class TestThePitchIsDerived(unittest.TestCase):
	"""A braille line is five pin rows at the 8 row pitch and four at the 10 row pitch.

	Neither is published. Both fall out of the pin grid divided by the cell grid, which is
	what makes this work on a display nobody has written a special case for.
	"""

	def test_eightRowsGivesFivePinRowsPerLine(self):
		surface = findSurfaceFor(FakeDrawableDriver(numRows=8, numCols=32))
		self.assertEqual(surface.pinsPerRow, 5)
		self.assertEqual(surface.pinsPerCol, 3)

	def test_tenRowsGivesFourPinRowsPerLine(self):
		surface = findSurfaceFor(FakeDrawableDriver(numRows=10, numCols=32))
		self.assertEqual(surface.pinsPerRow, 4)
		self.assertEqual(surface.pinsPerCol, 3)


def findSurfaceFor(driver):
	""":return: the surface for a driver NVDA is driving directly."""
	useDisplay(driver)
	return findSurface()


class TestCellsToPins(unittest.TestCase):
	def test_aClaimBecomesTheWholeSlotIncludingTheGapRow(self):
		"""Reserving two lines and getting the dots but not the row between them would leave
		a stripe through every drawing."""
		surface = findSurfaceFor(FakeDrawableDriver(numRows=8, numCols=32))
		pins = surface.pinRectForCells(SegmentRect(row=0, col=0, numRows=2, numCols=32))
		self.assertEqual(pins, PinRect(x=0, y=0, width=96, height=10))

	def test_aClaimBelowTheTopStartsWhereItsRowsDo(self):
		surface = findSurfaceFor(FakeDrawableDriver(numRows=8, numCols=32))
		pins = surface.pinRectForCells(SegmentRect(row=1, col=0, numRows=7, numCols=32))
		self.assertEqual(pins, PinRect(x=0, y=5, width=96, height=35))

	def test_columnsBecomeThreePinsEach(self):
		surface = findSurfaceFor(FakeDrawableDriver(numRows=8, numCols=32))
		pins = surface.pinRectForCells(SegmentRect(row=0, col=4, numRows=1, numCols=8))
		self.assertEqual(pins, PinRect(x=12, y=0, width=24, height=5))

	def test_theBandIsSubtractedNotAdded(self):
		"""The overlay is placed in the member's own pins, and those start at its row 0
		however far down the composite the member sits."""
		monarch = FakeDrawableDriver(numRows=8, numCols=32)
		display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("fakeMonarch", 1, 8, 32),
			drivers={"freedomScientific": FakePlainDriver(), "fakeMonarch": monarch},
		)
		useDisplay(display)
		surface = findSurface()
		# Composite rows 1 to 8 are the Monarch's own rows 0 to 7.
		pins = surface.pinRectForCells(SegmentRect(row=1, col=0, numRows=8, numCols=32))
		self.assertEqual(pins, PinRect(x=0, y=0, width=96, height=40))

	def test_aClaimReachingPastTheMemberIsClipped(self):
		monarch = FakeDrawableDriver(numRows=8, numCols=32)
		display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("fakeMonarch", 1, 8, 32),
			drivers={"freedomScientific": FakePlainDriver(), "fakeMonarch": monarch},
		)
		useDisplay(display)
		surface = findSurface()
		# A claim across the whole 9 by 80 composite. Only the Monarch's part of it is pins.
		pins = surface.pinRectForCells(SegmentRect(row=0, col=0, numRows=9, numCols=80))
		self.assertEqual(pins, PinRect(x=0, y=0, width=96, height=40))

	def test_aClaimThatMissesTheMemberEntirelyIsEmpty(self):
		monarch = FakeDrawableDriver(numRows=8, numCols=32)
		display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("fakeMonarch", 1, 8, 32),
			drivers={"freedomScientific": FakePlainDriver(), "fakeMonarch": monarch},
		)
		useDisplay(display)
		pins = findSurface().pinRectForCells(SegmentRect(row=0, col=0, numRows=1, numCols=80))
		self.assertTrue(pins.isEmpty)

	def test_aPinConvertsBackToTheCompositesOwnRow(self):
		monarch = FakeDrawableDriver(numRows=8, numCols=32)
		display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("fakeMonarch", 1, 8, 32),
			drivers={"freedomScientific": FakePlainDriver(), "fakeMonarch": monarch},
		)
		useDisplay(display)
		surface = findSurface()
		# The Monarch's pin row 5 is its own line 1, which is the composite's row 2.
		self.assertEqual(surface.cellForPin(6, 5), (2, 2))

	def test_aPinOffTheDisplayHasNoCell(self):
		surface = findSurfaceFor(FakeDrawableDriver())
		self.assertIsNone(surface.cellForPin(-1, 0))
		self.assertIsNone(surface.cellForPin(0, PIN_HEIGHT))


class TestBufferPrimitives(unittest.TestCase):
	"""The two primitives phase 2 needed that the driver had not already wanted."""

	def test_aMarkerIsCentredOnItsPoint(self):
		buffer = PinBuffer(7, 7)
		buffer.marker(3, 3, "plus")
		self.assertEqual(
			buffer.rows(),
			[
				".......",
				".......",
				"...O...",
				"..OOO..",
				"...O...",
				".......",
				".......",
			],
		)

	def test_anUnknownMarkerIsStillAPoint(self):
		"""Losing a data point is worse than losing its shape."""
		buffer = PinBuffer(3, 3)
		buffer.marker(1, 1, "no such marker")
		self.assertTrue(buffer.getDot(1, 1))

	def test_aMarkerClipsAtTheEdgeRatherThanRaising(self):
		"""Centred on 0, 0, so its top and left thirds fall off the buffer and are dropped
		rather than raising or wrapping."""
		buffer = PinBuffer(3, 3)
		buffer.marker(0, 0, "square")
		self.assertEqual(buffer.rows(), [".O.", "OO.", "..."])

	def test_textDrawsBrailleDotsAtTheCellStride(self):
		"""Dots 1 and 4 are the two columns of a cell's top row, so they are adjacent pins.
		The stride is what puts the next cell three pins along, not two."""
		buffer = PinBuffer(9, 4)
		# Dots 1 and 4 in the first cell, dot 8 in the second.
		buffer.text(0, 0, [0b00001001, 0b10000000])
		self.assertEqual(
			buffer.rows(),
			[
				"OO.......",
				".........",
				".........",
				"....O....",
			],
		)

	def test_textIsAdditive(self):
		buffer = PinBuffer(6, 4)
		buffer.line(0, 3, 5, 3)
		buffer.text(0, 0, [0b00000001])
		self.assertTrue(buffer.getDot(0, 0))
		self.assertTrue(buffer.getDot(5, 3))

	def test_textCanBePackedWithoutAGapColumn(self):
		buffer = PinBuffer(4, 4)
		buffer.text(0, 0, [0b00000001, 0b00000001], cellStride=2)
		self.assertTrue(buffer.getDot(0, 0))
		self.assertTrue(buffer.getDot(2, 0))


class TestTheClaim(unittest.TestCase):
	"""The claim takes the drawable display's whole band and supplies its own text line.

	It has to. Panels are evicted whole, so a claim on part of a band takes the band's
	panel with it — focus segment included, however carefully the claim avoided the focus
	segment's own rows. The first attempt left those rows out and was refused on hardware
	regardless. `test_aClaimMayTakeTheFocusSegmentBecauseItReplacesIt` is that failure.
	"""

	def setUp(self):
		self.driver = FakeDrawableDriver(numRows=8, numCols=32)
		useDisplay(self.driver)
		self.plugin = FakePlugin()
		self.mode = GraphicsMode(self.plugin)

	def panel(self):
		":return: the claim the mode made."
		return self.plugin.panels[0]

	def test_theClaimIsTheWholeBandOfTheDrawableDisplay(self):
		self.assertTrue(self.mode.enter())
		self.assertIsInstance(self.panel(), GraphicsPanel)
		self.assertEqual(self.panel().name, PANEL_NAME)
		self.assertEqual(self.panel().rect, SegmentRect(row=0, col=0, numRows=8, numCols=32))

	def test_theBandIsTheMembersOwnRowsInsideAComposite(self):
		monarch = FakeDrawableDriver(numRows=8, numCols=32)
		useDisplay(
			fakeVirtualDisplay(
				("freedomScientific", 0, 1, 80),
				("fakeMonarch", 1, 8, 32),
				drivers={"freedomScientific": FakePlainDriver(), "fakeMonarch": monarch},
			),
		)
		mode = GraphicsMode(self.plugin)
		self.assertTrue(mode.enter())
		# Row 0 is the Focus 80 and is not the drawing's to take.
		self.assertEqual(self.panel().rect, SegmentRect(row=1, col=0, numRows=8, numCols=32))

	def test_theClaimSuppliesTheBrailleLineItself(self):
		self.mode.enter()
		segments = self.panel().segments()
		self.assertEqual(len(segments), 2)
		text, drawing = segments
		self.assertEqual(text.rect, SegmentRect(row=0, col=0, numRows=1, numCols=32))
		self.assertTrue(text.hostsSystemFocus)
		self.assertFalse(text.isReserved)
		self.assertEqual(drawing.rect, SegmentRect(row=1, col=0, numRows=7, numCols=32))
		self.assertTrue(drawing.isReserved)
		self.assertFalse(drawing.hostsSystemFocus)

	def test_onlyTheDrawingSegmentCarriesTheGraphicsPolicy(self):
		"""The braille line beside the figure has to route like any other line."""
		self.mode.enter()
		text, drawing = self.panel().segments()
		self.assertNotIsInstance(text.routingPolicy, GraphicsRoutingPolicy)
		self.assertIsInstance(drawing.routingPolicy, GraphicsRoutingPolicy)

	def test_aClaimMayTakeTheFocusSegmentBecauseItReplacesIt(self):
		"""The hardware failure, reproduced against the real composition.

		With the focus segment on the Monarch, showing a drawing failed with `LookupError:
		Panel 'graphics' would evict the focus segment 'device.brlMultilineMonarch.0'`, and
		moving the focus to the other display made it work. Leaving the focus segment's rows
		out of the claim did not help, because the whole band is one panel and panels are
		evicted whole. Offering a replacement is the only mechanism `withPanel` provides.
		"""
		band = SegmentRect(row=0, col=0, numRows=8, numCols=32)
		base = SegmentView(
			"device",
			[RowsPanel("device.fakeMonarch", band, 2)],
			focusSegmentKey="device.fakeMonarch.0",
		)
		claim = GraphicsPanel(PANEL_NAME, band, textRows=1)
		composed = base.withPanel(claim, 8, 32)
		self.assertEqual(composed.focusSegmentKey, f"{PANEL_NAME}.text")
		composed.validate(8, 32)

	def test_aClaimWithNoBrailleLineHostsTheFocusAndDrawsNoneOfIt(self):
		"""A reader may ask for the whole panel, and then the focus line goes with it.

		The drawing segment has to host the focus — a view must have somewhere for untargeted
		content to land — and is `exclusive` so that content is handed to the owner and dropped
		rather than written into cells the figure is composited over.
		"""
		band = SegmentRect(row=0, col=0, numRows=8, numCols=32)
		base = SegmentView(
			"device",
			[RowsPanel("device.fakeMonarch", band, 2)],
			focusSegmentKey="device.fakeMonarch.0",
		)
		claim = GraphicsPanel(PANEL_NAME, band, textRows=0)
		composed = base.withPanel(claim, 8, 32)
		self.assertEqual(composed.focusSegmentKey, f"{PANEL_NAME}.drawing")
		composed.validate(8, 32)
		drawing = claim.segments()[0]
		self.assertTrue(drawing.hostsSystemFocus)
		self.assertTrue(drawing.ownerDrawsFocus)

	def test_theDrawingGetsTheClaimLessItsTextLine(self):
		self.mode.enter(textLines=2)
		self.assertEqual(self.panel().rect, SegmentRect(row=0, col=0, numRows=8, numCols=32))
		self.assertEqual(
			self.panel().drawingRect,
			SegmentRect(row=2, col=0, numRows=6, numCols=32),
		)

	def test_noTextLineGivesTheWholeBandToTheDrawing(self):
		self.mode.enter(textLines=0)
		self.assertEqual(len(self.panel().segments()), 1)
		self.assertEqual(self.panel().focusSegmentKey, f"{PANEL_NAME}.drawing")
		self.assertEqual(self.panel().drawingRect, self.panel().rect)

	def test_theBrailleLineCanBeGivenUpAndTakenBackMidRead(self):
		"""The reader wants the whole panel *at times*, so leaving and coming back would
		cost them their zoom and their place in the figure — which is most of what they had.
		"""
		self.mode.enter()
		figure = self.mode.drawing
		self.mode.zoomBy(2)
		self.mode.panBy(4, 2)
		zoom, origin = self.mode.zoom, self.mode.origin
		self.assertTrue(self.mode.setTextLines(0))
		self.assertEqual(self.mode.textLines, 0)
		self.assertIs(self.mode.drawing, figure)
		self.assertEqual((self.mode.zoom, self.mode.origin), (zoom, origin))
		self.assertEqual(self.panel().drawingRect.numRows, 8)
		self.assertTrue(self.mode.setTextLines(1))
		self.assertEqual(self.panel().drawingRect.numRows, 7)

	def test_theFullPanelIsSaidSoTheCostIsNotSilent(self):
		self.mode.enter()
		self.assertNotIn("full panel", self.mode.describe())
		self.mode.setTextLines(0)
		self.assertIn("full panel", self.mode.describe())

	def test_changingTheLineWithNoDrawingUpDoesNothing(self):
		self.assertFalse(self.mode.setTextLines(0))

	def test_aClaimWithNoRoomLeftToDrawIsRefused(self):
		self.assertFalse(self.mode.enter(textLines=8))
		self.assertTrue(self.mode.lastError)
		self.assertEqual(self.plugin.panels, [])

	def test_aRefusedClaimLeavesNothingBehind(self):
		plugin = FakePlugin(refuse=True)
		mode = GraphicsMode(plugin)
		self.assertFalse(mode.enter())
		self.assertFalse(mode.active)
		self.assertEqual(self.driver.overlays, {})
		self.assertTrue(mode.lastError)

	def test_aDisplayThatCannotDrawSaysSoRatherThanDoingNothing(self):
		useDisplay(FakePlainDriver())
		mode = GraphicsMode(self.plugin)
		self.assertFalse(mode.enter())
		self.assertTrue(mode.lastError)
		self.assertEqual(self.plugin.panels, [])

	def test_leavingClearsTheDrawingAndGivesTheRowsBack(self):
		self.mode.enter()
		self.mode.leave()
		self.assertFalse(self.mode.active)
		self.assertEqual(self.plugin.panels, [])
		self.assertNotIn(OVERLAY_KEY, self.driver.overlays)
		self.assertIn(OVERLAY_KEY, self.driver.cleared)

	def test_leavingWhenNothingIsUpDoesNothing(self):
		self.mode.leave()
		self.assertEqual(self.driver.cleared, [])


class TestThePriorityLadder(unittest.TestCase):
	"""A drawing outranks a flow, and a flow outranks an ordinary claim.

	The flow is on most of the time — it is the add-on's default and it is wanted in Excel
	and on the web — so without an order, showing a drawing was a fight with it: whichever
	claim was made most recently took the rows, and a profile switch or a focus change
	could take a figure off the display mid-read.
	"""

	def test_graphicsOutranksTheFlowWhichOutranksAnOrdinaryClaim(self):
		self.assertGreater(PRIORITY_GRAPHICS, PRIORITY_FLOW)
		self.assertGreater(PRIORITY_FLOW, PRIORITY_ORDINARY)

	def test_thePanelsCarryThoseRanks(self):
		rect = SegmentRect(row=0, col=0, numRows=4, numCols=32)
		self.assertEqual(GraphicsPanel("g", rect).priority, PRIORITY_GRAPHICS)
		self.assertEqual(FlowPanel("f", rect).priority, PRIORITY_FLOW)
		self.assertEqual(SinglePanel("s", rect).priority, PRIORITY_ORDINARY)

	# How the ranks actually order a composition is tested in `test_plugin`, where the real
	# plugin module is loaded. Here `brlMultiline` is a package stand-in with no code in it.


class TestRendering(unittest.TestCase):
	def setUp(self):
		self.driver = FakeDrawableDriver(numRows=8, numCols=32)
		useDisplay(self.driver)
		self.plugin = FakePlugin()
		self.mode = GraphicsMode(self.plugin)

	def drawingOf(self, rows):
		""":return: a drawing built from `PinBuffer.fromRows` text."""
		return Drawing(PinBuffer.fromRows(rows), name="fixture")

	def shown(self):
		""":return: the overlay origin and buffer the display was given."""
		return self.driver.overlays[OVERLAY_KEY]

	def test_theOverlayGoesWhereTheClaimIsInTheDisplaysOwnPins(self):
		self.mode.enter()
		x, y, buffer = self.shown()
		# One line left to braille at the 8 row pitch is five pin rows.
		self.assertEqual((x, y), (0, 5))
		self.assertEqual((buffer.width, buffer.height), (96, 35))

	def test_atNaturalSizeASourceDotIsAPin(self):
		self.mode.enter(self.drawingOf(["O.O", ".O.", "O.O"]), textLines=7)
		_x, _y, buffer = self.shown()
		self.assertEqual(buffer.rows()[0][:3], "O.O")
		self.assertEqual(buffer.rows()[1][:3], ".O.")
		self.assertEqual(buffer.rows()[2][:3], "O.O")

	def test_theWholeDrawingIsShownAtFirst(self):
		"""What a tactile viewer is for: a hand on the panel and the shape of the thing,
		compressed as far as it needs to be. Not the top left corner of it at natural size,
		which is what an earlier version showed and which is useless for a real image.
		"""
		self.mode.enter()
		self.assertEqual(self.mode.zoom, FIT)
		_x, _y, buffer = self.shown()
		pins = PinRect(0, 0, buffer.width, buffer.height)
		self.assertTrue(self.mode.showsWholeDrawing(pins))

	def test_aDrawingLargerThanThePanelIsCompressedToFit(self):
		self.mode.enter()
		_x, _y, buffer = self.shown()
		pins = PinRect(0, 0, buffer.width, buffer.height)
		# The test figure is twice the claim in each direction.
		self.assertAlmostEqual(self.mode.scale(pins), 0.5)

	def test_aDrawingSmallerThanThePanelIsNotBlownUpToFillIt(self):
		"""A figure authored at the panel's own size should come out the size it was
		drawn. Fitting only ever shrinks; magnification is always asked for.
		"""
		self.mode.enter(self.drawingOf(["O.O", ".O.", "O.O"]), textLines=7)
		_x, _y, buffer = self.shown()
		pins = PinRect(0, 0, buffer.width, buffer.height)
		self.assertEqual(self.mode.scale(pins), 1.0)

	def test_compressionKeepsAThinLineRatherThanSamplingPastIt(self):
		"""The reason a pin asks whether *any* dot of the source square it covers is
		raised. A line one dot wide, reduced to a quarter, is missed by three sample points
		out of four and comes out dashed or gone — which for a tactile drawing is the worst
		thing compression can do.
		"""
		# A single horizontal line across a source four times the claim's width.
		rows = ["." * 384 for _ in range(70)]
		rows[33] = "O" * 384
		self.mode.enter(self.drawingOf(rows))
		_x, _y, buffer = self.shown()
		raised = [row for row in buffer.rows() if "O" in row]
		self.assertTrue(raised)
		# Unbroken: every pin of that row stands for a square the line passes through.
		self.assertTrue(all(char == "O" for char in raised[0]))

	def test_aZoomStepDoublesTheScale(self):
		self.mode.enter()
		_x, _y, buffer = self.shown()
		pins = PinRect(0, 0, buffer.width, buffer.height)
		before = self.mode.scale(pins)
		self.assertTrue(self.mode.zoomBy(1))
		self.assertEqual(self.mode.zoom, 1)
		self.assertAlmostEqual(self.mode.scale(pins), before * 2)

	def test_magnifyingASourceDotMakesItABlockOfPins(self):
		self.mode.enter(self.drawingOf(["O.", ".O"]), textLines=7)
		self.assertTrue(self.mode.zoomBy(1))
		_x, _y, buffer = self.shown()
		self.assertEqual(buffer.rows()[0][:4], "OO..")
		self.assertEqual(buffer.rows()[1][:4], "OO..")
		self.assertEqual(buffer.rows()[2][:4], "..OO")
		self.assertEqual(buffer.rows()[3][:4], "..OO")

	def test_zoomStopsAtBothEndsOfTheLadder(self):
		self.mode.enter()
		self.assertFalse(self.mode.zoomBy(-1))
		self.assertEqual(self.mode.zoom, FIT)
		for _step in range(MAX_ZOOM_STEP + 2):
			self.mode.zoomBy(1)
		self.assertLessEqual(self.mode.zoom, MAX_ZOOM_STEP)
		self.assertFalse(self.mode.zoomBy(1))

	def test_zoomingKeepsWhatIsUnderTheMiddleInTheMiddle(self):
		"""The reader's hand is on the figure; the thing they were feeling should still
		be under it afterwards.
		"""
		self.mode.enter()
		_x, _y, buffer = self.shown()
		pins = PinRect(0, 0, buffer.width, buffer.height)
		visibleX, visibleY = self.mode._visible(pins)
		centre = (self.mode.origin[0] + visibleX // 2, self.mode.origin[1] + visibleY // 2)
		self.mode.zoomBy(1)
		visibleX, visibleY = self.mode._visible(pins)
		after = (self.mode.origin[0] + visibleX // 2, self.mode.origin[1] + visibleY // 2)
		# Within a dot: halving an odd span cannot land in exactly the same place.
		self.assertLessEqual(abs(after[0] - centre[0]), 1)
		self.assertLessEqual(abs(after[1] - centre[1]), 1)

	def test_panningDoesNothingWhileTheWholeDrawingIsShown(self):
		"""Nothing is off the edge, so there is nowhere to pan to. The command says so
		rather than reporting an edge that does not exist.
		"""
		self.mode.enter()
		self.assertFalse(self.mode.panBy(4, 2))
		self.assertEqual(self.mode.origin, (0, 0))

	def test_panningWorksOnceMagnified(self):
		self.mode.enter()
		self.mode.zoomBy(2)
		before = self.mode.origin
		self.assertTrue(self.mode.panBy(4, 2))
		self.assertEqual(self.mode.origin, (before[0] + 4, before[1] + 2))

	def test_panningStopsAtTheEdgeRatherThanRunningOff(self):
		self.mode.enter()
		self.mode.zoomBy(2)
		self.mode.panBy(-1000, -1000)
		self.assertEqual(self.mode.origin, (0, 0))
		self.assertFalse(self.mode.panBy(-1, 0))

	def test_theStepIsAQuarterOfWhatIsVisible(self):
		self.mode.enter()
		self.mode.zoomBy(2)
		_x, _y, buffer = self.shown()
		pins = PinRect(0, 0, buffer.width, buffer.height)
		visibleX, visibleY = self.mode._visible(pins)
		across, down = self.mode.panStep()
		self.assertEqual(across, max(1, visibleX // 4))
		self.assertEqual(down, max(1, visibleY // 4))

	def test_theStepIsNeverZero(self):
		self.mode.enter(self.drawingOf(["O"]), textLines=7)
		self.mode.zoomBy(MAX_ZOOM_STEP)
		across, down = self.mode.panStep()
		self.assertGreaterEqual(across, 1)
		self.assertGreaterEqual(down, 1)

	def test_theTestFigureIsBiggerThanTheClaimSoThereIsSomewhereToZoomInto(self):
		self.mode.enter()
		_x, _y, buffer = self.shown()
		self.assertEqual(self.mode.drawing.width, buffer.width * 2)
		self.assertEqual(self.mode.drawing.height, buffer.height * 2)


class TestAFigureThatDrawsItselfAgain(unittest.TestCase):
	"""Zoom on a chart is not magnification, and the difference was reported from hardware.

	Magnifying is the only thing that can be done to a photograph: the dots are all there is.
	Done to a chart it makes the braille labels into smears of enlarged dots, and the dates
	written at the two ends go on naming the whole range while the reader is feeling a tenth of
	it — so the one piece of writing that says where they are is the piece that lies.

	A chart was composed for this panel out of numbers that are still to hand, so it can be
	composed again: the same panel, fewer periods, drawn at full resolution with their own
	dates. `Drawing.redraw` is how a figure says it can do that, asked of the figure rather
	than of its type, exactly as `describeAt` is.
	"""

	def setUp(self):
		self.driver = FakeDrawableDriver(numRows=8, numCols=32)
		useDisplay(self.driver)
		self.plugin = FakePlugin()
		self.mode = GraphicsMode(self.plugin)
		self.windows: list = []
		"""Every window the mode asked for, so a test can say what it asked rather than only
		what came back."""

	def figure(self, points=32):
		""":return: a drawing that redraws itself, standing in for a chart.

		Each window is drawn as a solid block whose height is how many points are in it, which
		is a shape a test can read back without a chart's arithmetic in the way.
		"""

		def redraw(offset, span, pinWidth, pinHeight):
			self.windows.append((offset, span))
			first = max(0, min(points - 1, int(round(offset * points))))
			count = max(1, min(points - first, int(round(span * points))))
			buffer = PinBuffer(pinWidth, pinHeight)
			buffer.rect(0, 0, pinWidth, count, filled=True)
			return Drawing(
				buffer,
				name=f"points {first} to {first + count - 1}",
				describeAt=lambda x, y: f"point {first + x * count // pinWidth}",
				redraw=redraw,
				points=points,
			)

		return redraw(0.0, 1.0, 96, 35)

	def shown(self):
		""":return: the buffer the display was given."""
		return self.driver.overlays[OVERLAY_KEY][2]

	def height(self):
		""":return: how many rows of the shown buffer are solid, which is the window's width in
		points."""
		return len([row for row in self.shown().rows() if row.count("O") == 96])

	def test_theWholeFigureIsShownAtFirst(self):
		self.mode.enter(self.figure())
		self.assertEqual(self.height(), 32)

	def test_zoomingInAsksForPartOfTheDataRatherThanMagnifying(self):
		"""The point of the whole thing. Half the periods, drawn at the panel's own size, not
		the same periods drawn twice as large."""
		self.mode.enter(self.figure())
		self.mode.zoomBy(1)
		self.assertEqual(self.height(), 16)

	def test_zoomingOutComesBackToTheWholeThing(self):
		"""Every window is taken from the figure as it was given, never from the last window,
		or zooming in and out again would drift."""
		self.mode.enter(self.figure())
		self.mode.zoomBy(2)
		self.mode.zoomBy(-2)
		self.assertEqual(self.height(), 32)

	def test_theFigureOnThePanelIsTheWindowAndNotTheWhole(self):
		"""And the window is taken about the middle, not the corner: the reader's hand is
		on the figure, and what they were feeling should still be under it afterwards. So
		half of thirty-two points is the middle sixteen.
		"""
		self.mode.enter(self.figure())
		self.mode.zoomBy(1)
		self.assertEqual(self.mode.drawing.name, "points 8 to 23")

	def test_panningMovesThroughTheDataAndNotOverTheDots(self):
		self.mode.enter(self.figure())
		self.mode.zoomBy(1)
		before = self.windows[-1][0]
		self.mode.panBy(24, 0)
		self.assertGreater(self.windows[-1][0], before)

	def test_panningStopsAtTheEndOfTheData(self):
		self.mode.enter(self.figure())
		self.mode.zoomBy(1)
		for _ in range(20):
			self.mode.panBy(24, 0)
		offset, span = self.windows[-1]
		self.assertLessEqual(offset + span, 1.0001)

	def test_thereIsNothingToPanToWhileTheWholeThingIsShown(self):
		self.mode.enter(self.figure())
		self.assertFalse(self.mode.panBy(24, 0))

	def test_thereIsNoUpAndDownToPanThrough(self):
		"""A redrawn chart fits its value axis to whatever it is showing, so there is never
		anything above or below the panel. Saying so is better than a command that moves
		nothing and does not explain itself."""
		self.mode.enter(self.figure())
		self.mode.zoomBy(1)
		self.assertFalse(self.mode.panBy(0, 8))

	def test_theWindowIsNotComposedAgainForTheSameWindow(self):
		"""An ordinary refresh — a rebuild, a settings change, the flow giving rows back —
		must not cost a redraw of the chart."""
		self.mode.enter(self.figure())
		asked = len(self.windows)
		self.mode.render()
		self.mode.render()
		self.assertEqual(len(self.windows), asked)

	def test_theZoomStopsWhereTheDataDoes(self):
		"""A chart of twenty periods magnified thirty-two times is a chart of half a period.
		Refused rather than clamped, so that the zoom the reader is told matches the one they
		are feeling."""
		self.mode.enter(self.figure(points=8))
		self.assertTrue(self.mode.zoomBy(1))
		self.assertFalse(self.mode.zoomBy(MAX_ZOOM_STEP))
		self.assertEqual(self.mode.zoom, 1)

	def test_aPressAnswersAboutTheWindowUnderTheFinger(self):
		"""The window is what is on the panel, so a pin is a dot of it. Putting the zoom and
		the origin through as well would apply the window twice and answer about a period the
		reader is not touching — worse than no answer, because it is a plausible one."""
		self.mode.enter(self.figure())
		self.mode.zoomBy(1)
		self.mode.panBy(24, 0)
		point = self.mode.pointForPin(0, 5)
		self.assertEqual(point, (0, 0))
		self.assertNotIn("point 0", self.mode.describePoint(point))

	def test_aFigureThatCannotComposeAWindowKeepsTheOneItHad(self):
		"""A blank panel for a window that happened to hold nothing chartable would be a worse
		answer than the view the reader already had."""

		def refuses(offset, span, pinWidth, pinHeight):
			return None

		buffer = PinBuffer(96, 35)
		buffer.rect(0, 0, 96, 4, filled=True)
		self.mode.enter(Drawing(buffer, name="fixture", redraw=refuses, points=32))
		self.mode.zoomBy(1)
		self.assertEqual(self.shown().rows()[0].count("O"), 96)

	def test_aFigureThatRaisesWhileComposingIsNotAllowedToTakeTheDisplayDown(self):
		def raises(offset, span, pinWidth, pinHeight):
			raise RuntimeError("no")

		buffer = PinBuffer(96, 35)
		buffer.rect(0, 0, 96, 4, filled=True)
		self.mode.enter(Drawing(buffer, name="fixture", redraw=raises, points=32))
		self.assertTrue(self.mode.render())

	def test_theWindowIsComposedForTheRectangleItIsGoingInto(self):
		"""The rectangle can change under a figure that is already up: giving the whole
		band to the drawing is a command, and one a reader uses mid-read. A window
		composed for the old rectangle would be blitted into the new one with a row of it
		cut off — the row its dates are written on.
		"""
		self.mode.enter(self.figure())
		self.mode.zoomBy(1)
		self.assertTrue(self.mode.setTextLines(0))
		shown = self.shown()
		self.assertEqual(
			(self.mode.drawing.buffer.width, self.mode.drawing.buffer.height),
			(shown.width, shown.height),
		)

	def test_aPictureIsStillMagnified(self):
		"""The other half of the rule. A drawing with no `redraw` keeps the sampling, which is
		the only thing that can be done to dots that are all there is."""
		self.mode.enter(Drawing(PinBuffer.fromRows(["O.O", ".O.", "O.O"]), name="picture"))
		self.mode.zoomBy(1)
		self.assertGreater(self.mode.zoom, FIT)
		self.assertIs(self.mode.drawing.name, "picture")


class TestPointing(unittest.TestCase):
	"""Pointing is a routing press, and these are about the two ways it can be answered.

	The pin is the good answer and comes from the driver. The cell is the fallback for a
	drawable display that reports no pin, and is coarse — a cell is 3 by 5 pins — but it is
	an answer rather than silence.
	"""

	def setUp(self):
		self.driver = FakeDrawableDriver(numRows=8, numCols=32)
		useDisplay(self.driver)
		self.mode = GraphicsMode(FakePlugin())
		flashedMessages.clear()

	def press(self, segmentPos=0):
		"""Make the press the way the container does, through the claim's policy.

		The queue is run afterwards because the press does not say anything itself — it queues
		the saying for after `BrailleHandler.routeTo` has returned. See `_sayAfterThePress`,
		and `test_theReportWaitsForThePressToFinish` for why that matters.

		:param segmentPos: the pressed cell within the claim.
		:return: what the reader was told.
		"""
		policy = GraphicsRoutingPolicy(self.mode)
		policy.route(container=None, segmentNumber=0, segmentPos=segmentPos)
		runQueuedFunctions()
		return flashedMessages[-1] if flashedMessages else None

	def test_theReportWaitsForThePressToFinish(self):
		"""The fault this was found by, and it is NVDA's own rule rather than a bug in it.

		`BrailleHandler.routeTo` runs the routing policy and then, if a message is up, dismisses
		it — because a cursor routing key is how a reader dismisses a message. A message raised
		from inside the policy arrives before that check and is read as the press's own
		dismissal, so it vanished the instant it appeared: spoken, never in braille, and
		invisible in any log.
		"""
		self.mode.enter(Drawing(PinBuffer.fromRows(["O.", ".."])), textLines=7)
		self.driver.lastRoutingPin = (0, 35)
		GraphicsRoutingPolicy(self.mode).route(container=None, segmentNumber=0, segmentPos=0)
		self.assertEqual(flashedMessages, [], "the press said something before it had finished")
		self.assertEqual(runQueuedFunctions(), 1)
		self.assertIn("Raised at 0, 0", flashedMessages[-1])

	def test_aPressInsideTheFigureBecomesASourceDot(self):
		self.mode.enter(Drawing(PinBuffer.fromRows(["O..", "...", "..O"])), textLines=7)
		# The claim starts at pin row 35, so its own dot 0, 0 is the display's pin 0, 35.
		self.driver.lastRoutingPin = (2, 37)
		self.assertEqual(self.mode.pointForPin(2, 37), (2, 2))

	def test_thePressIsWhatReportsIt(self):
		self.mode.enter(Drawing(PinBuffer.fromRows(["O.", ".."])), textLines=7)
		self.driver.lastRoutingPin = (0, 35)
		self.assertIn("Raised at 0, 0", self.press())

	def test_aPressBesideALineFindsTheLine(self):
		"""The bug the first hardware run found. A fingertip is far wider than a pin, so a
		line one dot wide reports blank whenever the contact centre lands a pin to either side
		of it — which on hardware was every feature of the test figure, every time.
		"""
		self.mode.enter(Drawing(PinBuffer.fromRows(["O...", "....", "....", "...."])), textLines=7)
		# Two pins to the right of the dot, and one row down from it.
		self.driver.lastRoutingPin = (2, 36)
		self.assertIn("Raised at 0, 0", self.press())

	def test_aPressFarFromAnythingIsStillBlank(self):
		"""Searching outward must not turn the whole drawing into one raised dot."""
		rows = ["O" + "." * 19] + ["." * 20 for _ in range(9)]
		self.mode.enter(Drawing(PinBuffer.fromRows(rows)))
		# A 20 by 10 drawing in a 96 by 35 claim fits at natural size, so the radius is 3
		# source dots. This press is 15 away from the only dot there is.
		self.driver.lastRoutingPin = (15, 13)
		self.assertEqual(self.mode.pointForPin(15, 13), (15, 8))
		self.assertIn("Blank", self.press())

	def test_theSearchFollowsTheScaleInBothDirections(self):
		"""The radius is a fingertip measured in *source* dots, so it grows as the drawing
		is compressed — one pin then stands for several source dots and a finger covers more of
		the drawing — and shrinks to nothing as it is magnified, where the reader can place a
		finger exactly and searching would answer about a dot they are not touching.
		"""
		self.mode.enter()
		compressed = self.mode.searchRadius()
		self.mode.zoomBy(1)
		natural = self.mode.searchRadius()
		self.mode.zoomBy(3)
		magnified = self.mode.searchRadius()
		# Half a braille line is 3 pins at the 8 row pitch; the test figure sits at half scale.
		self.assertEqual(compressed, 6)
		self.assertEqual(natural, 3)
		self.assertEqual(magnified, 0)

	def test_theNearestDotWinsWhenSeveralAreClose(self):
		self.mode.enter(Drawing(PinBuffer.fromRows(["O.O.", "....", "....", "...."])), textLines=7)
		# One pin right of the left dot, two left of the right one.
		self.driver.lastRoutingPin = (1, 35)
		self.assertIn("Raised at 0, 0", self.press())

	def test_theLiveTouchIsNotWhatIsRead(self):
		"""It is zero by the time any script runs, so reading it would report nothing for
		every press. This is the bug the routing policy exists to avoid.
		"""
		self.mode.enter(Drawing(PinBuffer.fromRows(["O.", ".."])), textLines=7)
		self.driver.lastTouch = None
		self.driver.lastRoutingPin = (0, 35)
		self.assertIn("Raised at 0, 0", self.press())

	def test_zoomIsUndoneOnTheWayBack(self):
		self.mode.enter(Drawing(PinBuffer.fromRows(["O.", ".O"])), textLines=7)
		self.mode.zoomBy(1)
		# At zoom 2 the source dot 0, 0 covers the claim's first two pins each way.
		self.assertEqual(self.mode.pointForPin(1, 36), (0, 0))

	def test_aPressOutsideTheFigureIsNotOnIt(self):
		self.mode.enter()
		self.assertIsNone(self.mode.pointForPin(0, 0))

	def test_aDisplayWithNoPinFallsBackToTheCell(self):
		"""Coarse, and better than silence. The middle of the pressed cell is the least
		wrong point in it.
		"""
		self.mode.enter(textLines=7)
		self.driver.lastRoutingPin = None
		# Cell 0 of a one line claim: its middle is pin 1 across, and 2 down within the line.
		self.assertEqual(self.mode.pointForCell(0), self.mode.pointForPin(1, 37))
		self.assertIsNotNone(self.press())

	def test_aPressWithNoFigureUpSaysNothing(self):
		policy = GraphicsRoutingPolicy(self.mode)
		policy.route(container=None, segmentNumber=0, segmentPos=0)
		self.assertEqual(runQueuedFunctions(), 0)
		self.assertEqual(flashedMessages, [])

	def test_aPressIsAnsweredEvenWhenThePinIsOutsideTheClaim(self):
		"""At the native pitch the index comes from the device's own cell, and the pin it
		reports need not agree with the claim. The cell is then the answer.
		"""
		self.mode.enter(textLines=7)
		self.driver.lastRoutingPin = (0, 0)
		self.assertIsNotNone(self.press())


class TestLifecycle(unittest.TestCase):
	def setUp(self):
		self.driver = FakeDrawableDriver(numRows=8, numCols=32)
		useDisplay(self.driver)
		self.plugin = FakePlugin()
		self.mode = GraphicsMode(self.plugin)

	def test_aRebuiltClaimIsDrawnAgain(self):
		self.mode.enter()
		self.driver.overlays.clear()
		self.mode.onRebuilt()
		self.assertIn(OVERLAY_KEY, self.driver.overlays)

	def test_anEvictedClaimTakesTheDrawingWithIt(self):
		self.mode.enter()
		self.mode.onEvicted()
		self.assertFalse(self.mode.active)
		self.assertNotIn(OVERLAY_KEY, self.driver.overlays)

	def test_aClaimThatNoLongerReachesTheDisplayIsTreatedAsGone(self):
		"""A pitch change from 10 rows to 8 takes two lines off the display, and the rows the
		drawing was on may no longer be there."""
		self.mode.enter()
		useDisplay(FakePlainDriver())
		self.mode.onRebuilt()
		self.assertFalse(self.mode.active)

	def test_terminatingLeavesNothingOnTheHardware(self):
		self.mode.enter()
		self.mode.onTerminate()
		self.assertFalse(self.mode.active)
		self.assertNotIn(OVERLAY_KEY, self.driver.overlays)

	def test_describingSaysWhatIsUp(self):
		self.assertTrue(self.mode.describe())
		self.mode.enter()
		description = self.mode.describe()
		self.assertIn("test figure", description)

	def test_aSecondEntryReplacesTheFirst(self):
		self.mode.enter()
		first = self.mode.drawing
		self.mode.enter(Drawing(PinBuffer.fromRows(["O"]), name="second"))
		self.assertIsNot(self.mode.drawing, first)
		self.assertEqual(len(self.plugin.panels), 1)


class TestTheTestFigure(unittest.TestCase):
	def test_itDrawsSomethingAtEveryEdge(self):
		useDisplay(FakeDrawableDriver())
		surface = findSurface()
		drawing = testFigure(surface, 20, 10)
		rows = drawing.buffer.rows()
		self.assertTrue(all(char == "O" for char in rows[0]))
		self.assertTrue(all(char == "O" for char in rows[-1]))
		self.assertTrue(all(row[0] == "O" for row in rows))
		self.assertTrue(all(row[-1] == "O" for row in rows))

	def test_itHasAMarkerInTheMiddle(self):
		useDisplay(FakeDrawableDriver())
		drawing = testFigure(findSurface(), 20, 10)
		self.assertTrue(drawing.buffer.getDot(drawing.width // 2, drawing.height // 2 - 1))


if __name__ == "__main__":
	unittest.main()
