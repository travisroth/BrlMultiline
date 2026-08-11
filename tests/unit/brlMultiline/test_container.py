# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the display container: the stand-in for NVDA's single braille buffer.

These run against the stub `BrailleBuffer` in `_stubs`, which is crude but honest: regions
concatenate, the window is a slice, and panning moves it by one display. That is enough to
check the wiring the container is responsible for, which is how segments are addressed,
how their cells are composited, and where a key press is sent.
"""

import unittest

from ._stubs import CONFIG, FakeHandler, Region, installStubs, resetConfig

installStubs()

from brlMultiline.container import DisplayContainer  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import GridPanel, SinglePanel  # noqa: E402
from brlMultiline.routing import RoutingPolicy  # noqa: E402
from brlMultiline.views import viewFromConfig  # noqa: E402

MONARCH_ROWS = 8
MONARCH_COLS = 32


class ContainerTestCase(unittest.TestCase):
	segmentCount = 4

	def setUp(self):
		resetConfig()
		CONFIG["segmentCount"] = self.segmentCount
		self.handler = FakeHandler(MONARCH_ROWS, MONARCH_COLS)
		self.view = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		self.container = DisplayContainer(self.handler, self.view)
		self.handler.mainBuffer = self.handler.buffer = self.container

	def tearDown(self):
		resetConfig()

	def install(self, view):
		""":return: a container for a view, installed as the handler's buffer."""
		container = DisplayContainer(self.handler, view)
		self.handler.mainBuffer = self.handler.buffer = container
		return container


class TestConstruction(ContainerTestCase):
	def test_oneSegmentPerSpec(self):
		self.assertEqual(self.container.numSegments, 4)
		self.assertEqual(len(self.container.specs), 4)

	def test_rectsMirrorTheSpecs(self):
		self.assertEqual(self.container.rects, [spec.rect for spec in self.container.specs])

	def test_focusResolvedFromTheKey(self):
		self.assertEqual(self.container.focusSegmentNumber, 3)
		self.assertEqual(self.container.focusSegmentKey, "display.3")
		self.assertTrue(self.container.segments[3].isFocusBuffer)

	def test_panelsAreReachable(self):
		self.assertEqual(len(self.container.panels), 4)

	def test_aConfiguredViewReservesNothing(self):
		self.assertEqual(self.container.reservedKeys, set())

	def test_rejectsAViewThatDoesNotTile(self):
		from brlMultiline.views import SegmentView

		bad = SegmentView("partial", [SinglePanel("a", SegmentRect(0, 0, 4, MONARCH_COLS))])
		with self.assertRaises(ValueError):
			DisplayContainer(self.handler, bad)


class TestIdentity(ContainerTestCase):
	def test_numberForKey(self):
		self.assertEqual(self.container.numberForKey("display.2"), 2)

	def test_segmentForKey(self):
		self.assertIs(self.container.segmentForKey("display.2"), self.container.segments[2])

	def test_hasKey(self):
		self.assertTrue(self.container.hasKey("display.0"))
		self.assertFalse(self.container.hasKey("table.r0c0"))

	def test_unknownKeyRaises(self):
		with self.assertRaises(LookupError):
			self.container.numberForKey("nope")

	def test_segmentsCarryTheirSpec(self):
		segment = self.container.segments[1]
		self.assertEqual(segment.key, "display.1")
		self.assertIsNone(segment.owner)
		self.assertFalse(segment.isReserved)

	def test_resolveSegmentNumber(self):
		self.assertEqual(self.container.resolveSegmentNumber(None), 3)
		self.assertEqual(self.container.resolveSegmentNumber(-1), 3)
		self.assertEqual(self.container.resolveSegmentNumber(1), 1)
		with self.assertRaises(LookupError):
			self.container.resolveSegmentNumber(9)


class TestRegionTargeting(ContainerTestCase):
	def test_targetByNumber(self):
		region = Region("a")
		region.targetSegment = 1
		self.assertEqual(self.container.getSegmentNumberForRegion(region), 1)

	def test_targetByKey(self):
		region = Region("a")
		region.targetSegment = "display.2"
		self.assertEqual(self.container.getSegmentNumberForRegion(region), 2)

	def test_untargetedGoesToTheFocus(self):
		self.assertEqual(self.container.getSegmentNumberForRegion(Region("a")), 3)

	def test_aStaleKeyFallsBackToTheFocus(self):
		"""Showing a region in the wrong segment beats losing it."""
		region = Region("a")
		region.targetSegment = "gone.0"
		self.assertEqual(self.container.getSegmentNumberForRegion(region), 3)

	def test_appendingHonoursTheKey(self):
		region = Region("a")
		region.targetSegment = "display.2"
		self.container.regions.append(region)
		self.assertEqual(self.container.segments[2].regions, [region])

	def test_appendingUntargetedLandsInTheFocus(self):
		region = Region("a")
		self.container.regions.append(region)
		self.assertEqual(self.container.segments[3].regions, [region])

	def test_regionsProxyReadsTheFocusSegment(self):
		region = Region("a")
		self.container.segments[3].append(region)
		self.assertEqual(len(self.container.regions), 1)
		self.assertIs(self.container.regions[-1], region)

	def test_assigningRegionsEmptiesEverySegment(self):
		"""NVDA assigns a fresh list when showing speech in braille."""
		self.container.segments[0].append(Region("old"))
		fresh = Region("speech")
		self.container.regions = [fresh]
		self.assertEqual(self.container.segments[0].regions, [])
		self.assertEqual(self.container.segments[3].regions, [fresh])


class TestClearing(ContainerTestCase):
	def test_clearByKey(self):
		self.container.segments[2].append(Region("a"))
		self.container.clear("display.2")
		self.assertEqual(self.container.segments[2].regions, [])

	def test_clearByNumber(self):
		self.container.segments[2].append(Region("a"))
		self.container.clear(2)
		self.assertEqual(self.container.segments[2].regions, [])

	def test_clearEverything(self):
		for segment in self.container.segments:
			segment.append(Region("a"))
		self.container.clear()
		self.assertTrue(all(not segment.regions for segment in self.container.segments))


class TestCompositing(ContainerTestCase):
	def test_cellArrayIsDisplaySized(self):
		self.container.update()
		self.assertEqual(len(self.container.windowBrailleCells), MONARCH_ROWS * MONARCH_COLS)

	def test_eachSegmentLandsAtItsOwnOrigin(self):
		self.container.segments[0].append(Region("AB"))
		self.container.segments[3].append(Region("CD"))
		self.container.update()
		cells = self.container.windowBrailleCells
		self.assertEqual(cells[0:2], [ord("A"), ord("B")])
		self.assertEqual(cells[6 * MONARCH_COLS : 6 * MONARCH_COLS + 2], [ord("C"), ord("D")])

	def test_cellsNoSegmentCoversStayBlank(self):
		self.container.segments[0].append(Region("AB"))
		self.container.update()
		self.assertEqual(sum(1 for cell in self.container.windowBrailleCells if cell), 2)

	def test_aNarrowSegmentDoesNotBleedIntoItsNeighbour(self):
		"""A grid cell 10 wide must not write into the cell to its right."""
		view = self.view.withPanel(
			GridPanel(
				"t", SegmentRect(0, 0, 8, 30), rowBands=[8], colWidths=[10, 10, 10], focusSegmentKey="t.r0c0"
			),
			MONARCH_ROWS,
			MONARCH_COLS,
		)
		container = self.install(view)
		container.segmentForKey("t.r0c0").append(Region("X" * 15))
		container.update()
		cells = container.windowBrailleCells
		self.assertEqual(cells[0:10], [ord("X")] * 10)
		self.assertEqual(cells[10:20], [0] * 10)

	def test_rawTextIsCombined(self):
		self.container.segments[0].append(Region("ab"))
		self.container.segments[3].append(Region("cd"))
		self.container.update()
		self.assertEqual(self.container.rawText, "abcd")


class RecordingPolicy(RoutingPolicy):
	"""A policy that notes it was consulted rather than acting."""

	def __init__(self):
		self.calls = []

	def route(self, container, segmentNumber, segmentPos):
		self.calls.append((segmentNumber, segmentPos))


class TestRouting(ContainerTestCase):
	def test_aPressUsesThePressedSegmentsPolicy(self):
		policy = RecordingPolicy()
		view = self.view.withPanel(
			SinglePanel("spy", SegmentRect(0, 0, 2, MONARCH_COLS), routingPolicy=policy),
			MONARCH_ROWS,
			MONARCH_COLS,
		)
		container = self.install(view)
		container.routeTo(0)
		self.assertEqual(policy.calls, [(container.numberForKey("spy"), 0)])

	def test_neighbouringSegmentsKeepTheDefaultPolicy(self):
		"""Two policies on one display at once, which is the whole point of per segment policy."""
		policy = RecordingPolicy()
		view = self.view.withPanel(
			SinglePanel("spy", SegmentRect(0, 0, 2, MONARCH_COLS), routingPolicy=policy),
			MONARCH_ROWS,
			MONARCH_COLS,
		)
		container = self.install(view)
		container.routeTo(6 * MONARCH_COLS + 5)
		self.assertEqual(container.segmentForKey("display.3").routedTo, 5)
		self.assertEqual(policy.calls, [])

	def test_aPressInBlankSpaceIsIgnored(self):
		from brlMultiline.views import SegmentView
		from brlMultiline.panels import BlankPanel

		view = SegmentView(
			"gapped",
			[
				SinglePanel("a", SegmentRect(0, 0, 4, MONARCH_COLS)),
				BlankPanel(SegmentRect(4, 0, 4, MONARCH_COLS)),
			],
			focusSegmentKey="a",
		)
		container = self.install(view)
		container.routeTo(7 * MONARCH_COLS)
		self.assertIsNone(container.segmentForKey("a").routedTo)

	def test_positionWithinTheSegmentIsReported(self):
		policy = RecordingPolicy()
		view = self.view.withPanel(
			SinglePanel("spy", SegmentRect(2, 0, 2, MONARCH_COLS), routingPolicy=policy),
			MONARCH_ROWS,
			MONARCH_COLS,
		)
		container = self.install(view)
		# Row 3, column 4: one row into the segment.
		container.routeTo(3 * MONARCH_COLS + 4)
		self.assertEqual(policy.calls, [(container.numberForKey("spy"), MONARCH_COLS + 4)])


class TestReservation(ContainerTestCase):
	def setUp(self):
		super().setUp()
		self.container = self.install(
			self.view.withPanel(
				SinglePanel("pinned", SegmentRect(0, 0, 2, MONARCH_COLS)),
				MONARCH_ROWS,
				MONARCH_COLS,
			),
		)

	def test_aClaimedSegmentIsReserved(self):
		self.assertTrue(self.container.segmentForKey("pinned").isReserved)
		self.assertEqual(self.container.segmentForKey("pinned").owner, "pinned")

	def test_reservedKeysReportsIt(self):
		self.assertEqual(self.container.reservedKeys, {"pinned"})

	def test_configuredSegmentsStayFree(self):
		self.assertFalse(self.container.segmentForKey("display.3").isReserved)


class TestScrolling(ContainerTestCase):
	def test_theFocusSegmentDelegatesToTheBuffer(self):
		"""It must still fall through to the next line when it cannot pan further."""
		self.container.scrollForward()
		self.assertEqual(self.container.focusSegment.scrolled, "forward")

	def test_anotherSegmentIsOnlyPanned(self):
		"""Falling through would move a caret the user is not working in."""
		segment = self.container.segments[0]
		segment.append(Region("y" * 500))
		self.container.update()
		self.container.scrollForward(0)
		self.assertIsNone(segment.scrolled)
		self.assertGreater(segment.windowStartPos, 0)

	def test_scrollByKey(self):
		segment = self.container.segmentForKey("display.1")
		segment.append(Region("y" * 500))
		self.container.update()
		self.container.scrollForward("display.1")
		self.assertGreater(segment.windowStartPos, 0)

	def test_scrollBackByKey(self):
		segment = self.container.segmentForKey("display.1")
		segment.append(Region("y" * 500))
		self.container.update()
		self.container.scrollForward("display.1")
		moved = segment.windowStartPos
		self.container.scrollBack("display.1")
		self.assertLess(segment.windowStartPos, moved)

	def test_anUnknownSegmentIsSurvivable(self):
		self.container.scrollForward("nosuch")
		self.container.scrollForward(99)


class TestWindowSaveRestore(ContainerTestCase):
	def test_anEmptySegmentIsNotAFailure(self):
		self.container.saveWindow()
		self.assertFalse(self.container.segments[0].hasSavedWindow)

	def test_savesAndRestores(self):
		segment = self.container.segments[0]
		segment.append(Region("y" * 500))
		self.container.update()
		self.container.scrollForward(0)
		saved = segment.windowStartPos
		self.container.saveWindow()
		self.assertTrue(segment.hasSavedWindow)
		segment.windowStartPos = 0
		self.container.restoreWindow()
		self.assertEqual(segment.windowStartPos, saved)


class TestVisibleRegions(ContainerTestCase):
	def test_theFocusSegmentComesLast(self):
		"""The handler scans in reverse and expects the focus region last."""
		first = Region("a")
		focused = Region("b")
		self.container.segments[0].append(first)
		self.container.segments[3].append(focused)
		self.assertEqual(list(self.container.visibleRegions)[-1], focused)

	def test_everySegmentIsCovered(self):
		"""So a pinned object away from the focus still receives its updates."""
		for segment in self.container.segments:
			segment.append(Region("a"))
		self.assertEqual(len(list(self.container.visibleRegions)), 4)


class TestTheMonitorAndTableScenario(ContainerTestCase):
	segmentCount = 8

	def test_thePinnedSegmentSurvivesTheClaim(self):
		pinned = Region("PIN")
		pinned.targetSegment = "display.0"
		self.container.regions.append(pinned)
		self.assertEqual(self.container.segmentForKey("display.0").regions, [pinned])

		table = GridPanel(
			"table",
			SegmentRect(2, 0, 6, MONARCH_COLS),
			rowBands=[2, 2, 2],
			colWidths=[10, 10, 10],
		)
		rebuilt = self.install(self.view.withPanel(table, MONARCH_ROWS, MONARCH_COLS))

		self.assertTrue(rebuilt.hasKey("display.0"))
		self.assertEqual(rebuilt.focusSegmentKey, "table.r0c0")
		self.assertEqual(len(rebuilt.reservedKeys), 9)
		# The pin's own region does not carry over, but its key resolves, so whatever holds
		# it can put it back. That is what the plugin's monitor carry over does.
		self.assertEqual(rebuilt.getSegmentNumberForRegion(pinned), rebuilt.numberForKey("display.0"))

	def test_gridCellGeometryReachesTheSegments(self):
		table = GridPanel(
			"table",
			SegmentRect(2, 0, 6, MONARCH_COLS),
			rowBands=[2, 2, 2],
			colWidths=[10, 10, 10],
		)
		rebuilt = self.install(self.view.withPanel(table, MONARCH_ROWS, MONARCH_COLS))
		cell = rebuilt.segmentForKey("table.r1c2")
		self.assertEqual(cell.rect, SegmentRect(4, 20, 2, 10))
		self.assertTrue(cell.fillRows)
		self.assertEqual(cell.handler.displayDimensions.numRows, 2)
		self.assertEqual(cell.handler.displayDimensions.numCols, 10)


if __name__ == "__main__":
	unittest.main()
