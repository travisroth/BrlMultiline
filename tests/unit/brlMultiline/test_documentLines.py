# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for filling the free segments with the lines around the caret.

The rule these are about is which segments count as free. Four things put a segment out of
bounds, and they arrive by different routes: it follows the focus, a panel reserved it when
the view was composed, something claimed it at runtime by pinning an object there, or it has
no place in the document reading order at all.
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
LINES = [f"line {index}" for index in range(40)]


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

	def test_offsetsFollowTheReadingOrder(self):
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


class TestDocumentContext(DocumentLinesTestCase):
	"""Offsets follow the stated reading order, not the display order of segments.

	A panel may put several segments on one physical row. A three column grid consumes
	three indices per row band, so anything derived from display order would read far too
	many lines away.
	"""

	def gridView(self, claimRect=SegmentRect(1, 0, 6, MONARCH_COLS)):
		from brlMultiline.panels import GridPanel

		grid = GridPanel("table", claimRect, rowBands=[2, 2, 2], colWidths=[10, 10, 10])
		return self.view.withPanel(grid, MONARCH_ROWS, MONARCH_COLS)

	def test_configuredSegmentsAreNumberedConsecutively(self):
		self.assertEqual(
			[spec.documentContextIndex for spec in self.view.flatten()],
			list(range(8)),
		)

	def test_claimedSegmentsHaveNoContext(self):
		container = self.install(self.gridView())
		for spec in container.specs:
			if spec.key.startswith("table."):
				self.assertIsNone(spec.documentContextIndex)
				self.assertFalse(spec.hasDocumentContext)

	def test_aGridDoesNotInflateTheOffsets(self):
		"""The regression: nine cells on three rows consumed nine display indices."""
		container = self.install(self.gridView())
		# display.0 is on row 0 and the focus is display.7 on row 7, so seven lines back.
		self.putCaretInADocument(container, caretIndex=20)
		documentLines.populate(container)
		self.assertEqual(self.textIn(container, "display.0"), "line 13")

	def test_theOffsetMatchesTheRowDistanceForRowShapedSegments(self):
		container = self.install(self.gridView())
		self.putCaretInADocument(container, caretIndex=20)
		documentLines.populate(container)
		focusRow = container.focusSegment.rect.row
		for segment in container.segments:
			if segment.regions and segment is not container.focusSegment:
				expected = f"line {20 + (segment.rect.row - focusRow)}"
				self.assertEqual("".join(r.rawText for r in segment.regions), expected)

	def test_aFocusWithoutContextSuspendsDocumentLines(self):
		"""The focus moved into a table cell, so there is no caret line to be relative to."""
		container = self.install(self.gridView())
		self.putCaretInADocument(container, caretIndex=20)
		documentLines.populate(container)
		self.assertNotEqual(self.textIn(container, "display.0"), "")

		# Now compose a claim that takes the focus into the grid.
		from brlMultiline.panels import GridPanel

		grid = GridPanel("table", SegmentRect(1, 0, 7, MONARCH_COLS), rowBands=[7], colWidths=[10, 10, 10])
		moved = self.install(self.view.withPanel(grid, MONARCH_ROWS, MONARCH_COLS))
		self.assertEqual(moved.focusSegmentKey, "table.r0c0")
		self.putCaretInADocument(moved, caretIndex=20)
		documentLines.populate(moved)
		self.assertEqual(self.textIn(moved, "display.0"), "")

	def test_aSingleRowDisplayStillReadsConsecutively(self):
		"""Every column segment is on row 0, so row distance would give them all offset 0."""
		handler = FakeHandler(1, 80)
		CONFIG["segmentCount"] = 2
		view = viewFromConfig(1, 80)
		container = DisplayContainer(handler, view)
		handler.mainBuffer = handler.buffer = container
		self.putCaretInADocument(container, caretIndex=10)
		documentLines.populate(container)
		self.assertEqual(self.textIn(container, "display.0"), "line 9")


# Last in the file, so that running this module directly runs every class above it rather
# than stopping wherever the block happens to sit.
if __name__ == "__main__":
	unittest.main()
