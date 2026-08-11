# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for filling the free segments with the lines around the caret.

The rule these are about is which segments count as free. Three things put a segment out of
bounds, and they arrive by different routes: it follows the focus, a panel reserved it when
the view was composed, or something claimed it at runtime by pinning an object there.
"""

import unittest

from ._stubs import CONFIG, FakeDocument, FakeHandler, Region, installStubs, resetConfig

installStubs()

from brlMultiline import documentLines  # noqa: E402
from brlMultiline.container import DisplayContainer  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.panels import SinglePanel  # noqa: E402
from brlMultiline.views import viewFromConfig  # noqa: E402

MONARCH_ROWS = 8
MONARCH_COLS = 32
LINES = [f"line {index}" for index in range(20)]


class DocumentLinesTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		CONFIG["segmentCount"] = 8
		self.handler = FakeHandler(MONARCH_ROWS, MONARCH_COLS)
		self.view = viewFromConfig(MONARCH_ROWS, MONARCH_COLS)
		self.container = self.install(self.view)

	def tearDown(self):
		resetConfig()

	def install(self, view):
		container = DisplayContainer(self.handler, view)
		self.handler.mainBuffer = self.handler.buffer = container
		return container

	def putCaretInADocument(self, container, caretIndex=10):
		""":return: the document region placed in the focus segment."""
		document = FakeDocument(LINES, caretIndex)
		region = documentLines.TextInfoPositionRegion(document, lineOffset=0)
		region.update()
		container.focusSegment.append(region)
		return region

	def textIn(self, container, key):
		segment = container.segmentForKey(key)
		return "".join(region.rawText for region in segment.regions)


class TestIsFree(DocumentLinesTestCase):
	def test_theFocusSegmentIsNotFree(self):
		self.assertFalse(
			documentLines.isFree(self.container.focusSegment, 7, 7, set()),
		)

	def test_anOrdinarySegmentIsFree(self):
		self.assertTrue(documentLines.isFree(self.container.segments[0], 0, 7, set()))

	def test_aReservedSegmentIsNotFree(self):
		container = self.install(
			self.view.withPanel(
				SinglePanel("table", SegmentRect(0, 0, 2, MONARCH_COLS)),
				MONARCH_ROWS,
				MONARCH_COLS,
			),
		)
		reserved = container.segmentForKey("table")
		self.assertFalse(documentLines.isFree(reserved, container.numberForKey("table"), 7, set()))

	def test_aRuntimeClaimIsNotFree(self):
		"""Pinning happens long after the view was composed, so it arrives as a key set."""
		self.assertFalse(documentLines.isFree(self.container.segments[0], 0, 7, {"display.0"}))


class TestPopulate(DocumentLinesTestCase):
	def test_fillsTheFreeSegments(self):
		self.putCaretInADocument(self.container, caretIndex=10)
		self.assertTrue(documentLines.populate(self.container))
		self.assertEqual(self.textIn(self.container, "display.0"), "line 3")
		self.assertEqual(self.textIn(self.container, "display.6"), "line 9")

	def test_offsetsFollowDisplayOrder(self):
		"""The segment two above the focus segment shows the line two above the caret."""
		self.putCaretInADocument(self.container, caretIndex=10)
		documentLines.populate(self.container)
		for index in range(7):
			self.assertEqual(self.textIn(self.container, f"display.{index}"), f"line {3 + index}")

	def test_leavesTheFocusSegmentAlone(self):
		region = self.putCaretInADocument(self.container, caretIndex=10)
		documentLines.populate(self.container)
		self.assertEqual(self.container.focusSegment.regions, [region])

	def test_skipsAReservedSegment(self):
		container = self.install(
			self.view.withPanel(
				SinglePanel("table", SegmentRect(0, 0, 1, MONARCH_COLS)),
				MONARCH_ROWS,
				MONARCH_COLS,
			),
		)
		claimed = container.segmentForKey("table")
		claimed.append(Region("MINE"))
		self.putCaretInADocument(container, caretIndex=10)
		documentLines.populate(container)
		self.assertEqual(self.textIn(container, "table"), "MINE")
		self.assertEqual(self.textIn(container, "display.1"), "line 4")

	def test_skipsARuntimeClaim(self):
		pinned = self.container.segmentForKey("display.0")
		pinned.append(Region("PIN"))
		self.putCaretInADocument(self.container, caretIndex=10)
		documentLines.populate(self.container, {"display.0"})
		self.assertEqual(self.textIn(self.container, "display.0"), "PIN")

	def test_offsetsDoNotShiftAroundASkippedSegment(self):
		"""A reserved segment still counts, so the lines stay in step with the rows."""
		self.container.segmentForKey("display.2").append(Region("PIN"))
		self.putCaretInADocument(self.container, caretIndex=10)
		documentLines.populate(self.container, {"display.2"})
		self.assertEqual(self.textIn(self.container, "display.1"), "line 4")
		self.assertEqual(self.textIn(self.container, "display.3"), "line 6")

	def test_targetsRegionsByKey(self):
		self.putCaretInADocument(self.container, caretIndex=10)
		documentLines.populate(self.container)
		region = self.container.segmentForKey("display.0").regions[0]
		self.assertEqual(region.targetSegment, "display.0")

	def test_offsetsPastTheDocumentRenderBlank(self):
		"""A partial move would repeat the first line across the display."""
		self.putCaretInADocument(self.container, caretIndex=0)
		documentLines.populate(self.container)
		self.assertEqual(self.textIn(self.container, "display.0"), "")
		self.assertTrue(self.container.segmentForKey("display.0").regions[0].outOfRange)

	def test_aFocusOutsideADocumentClearsInstead(self):
		"""Lines from a document the focus has left would go on tracking a foreign caret."""
		self.putCaretInADocument(self.container, caretIndex=10)
		documentLines.populate(self.container)
		self.container.focusSegment.clear()
		self.container.focusSegment.append(Region("a button"))
		self.assertTrue(documentLines.populate(self.container))
		self.assertEqual(self.textIn(self.container, "display.0"), "")


class TestClearDocumentRegions(DocumentLinesTestCase):
	def test_takesBackOnlyItsOwnRegions(self):
		self.putCaretInADocument(self.container, caretIndex=10)
		documentLines.populate(self.container)
		self.assertTrue(documentLines.clearDocumentRegions(self.container))
		self.assertEqual(self.container.segmentForKey("display.0").regions, [])

	def test_leavesOtherContentAlone(self):
		self.container.segmentForKey("display.0").append(Region("NOT MINE"))
		self.assertFalse(documentLines.clearDocumentRegions(self.container))
		self.assertEqual(self.textIn(self.container, "display.0"), "NOT MINE")

	def test_leavesReservedSegmentsAlone(self):
		container = self.install(
			self.view.withPanel(
				SinglePanel("table", SegmentRect(0, 0, 1, MONARCH_COLS)),
				MONARCH_ROWS,
				MONARCH_COLS,
			),
		)
		claimed = container.segmentForKey("table")
		claimed.append(documentLines.TextInfoPositionRegion(FakeDocument(LINES, 3), lineOffset=1))
		documentLines.clearDocumentRegions(container)
		self.assertEqual(len(claimed.regions), 1)

	def test_reportsNothingToDo(self):
		self.assertFalse(documentLines.clearDocumentRegions(self.container))


class TestTextInfoPositionRegion(DocumentLinesTestCase):
	def test_offsetZeroReadsTheCaretLine(self):
		region = documentLines.TextInfoPositionRegion(FakeDocument(LINES, 5), lineOffset=0)
		region.update()
		self.assertEqual(region.rawText, "line 5")

	def test_positiveOffsetReadsForward(self):
		region = documentLines.TextInfoPositionRegion(FakeDocument(LINES, 5), lineOffset=2)
		region.update()
		self.assertEqual(region.rawText, "line 7")

	def test_negativeOffsetReadsBack(self):
		region = documentLines.TextInfoPositionRegion(FakeDocument(LINES, 5), lineOffset=-2)
		region.update()
		self.assertEqual(region.rawText, "line 3")

	def test_routingIsRefusedAwayFromTheCaretLine(self):
		"""Routing into a line the user is only reading would drag the focus with it."""
		region = documentLines.TextInfoPositionRegion(FakeDocument(LINES, 5), lineOffset=2)
		region.routeTo(3)
		self.assertFalse(hasattr(region, "routedTo"))

	def test_routingWorksOnTheCaretLine(self):
		region = documentLines.TextInfoPositionRegion(FakeDocument(LINES, 5), lineOffset=0)
		region.routeTo(3)
		self.assertEqual(region.routedTo, 3)

	def test_panningIsRefusedAwayFromTheCaretLine(self):
		region = documentLines.TextInfoPositionRegion(FakeDocument(LINES, 5), lineOffset=2)
		region.nextLine()
		region.previousLine()
		self.assertFalse(hasattr(region, "panned"))


if __name__ == "__main__":
	unittest.main()
