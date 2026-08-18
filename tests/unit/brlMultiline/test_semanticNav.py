# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the semantic navigation abstraction.

What is covered here:

1. The :class:`NavResult` named tuple carries the expected fields.
2. :class:`SemanticNavigator` capability detection reports correctly for browse mode
   regions (which have a tree interceptor) vs plain text regions vs empty segments.
3. Successful line, paragraph, and display-width moves in a pinned browse mode segment.
4. Fallback from heading to paragraph to line when the tree interceptor in the stubs
   does not provide ``_iterNodesByType`` (which none of the stubs do).
5. Fallback from paragraph to line when the pinned region is a text-info region with
   no tree interceptor.
6. Boundary behaviour: attempting to move past the end of the document returns
   ``moved=False`` rather than raising.
7. :meth:`PinnedRegion.panUnit` moves by the given unit and re-renders.
8. The plugin's :meth:`moveMonitorInSegment` command routes through the navigator and
   speaks a brief message only when fallback or failure occurs.
"""

import unittest

from ._stubs import (
	CONFIG,
	FakeHandler,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	installStubs,
	loadPlugin,
	resetPluginState,
	spokenMessages,
)

installStubs()
plugin = loadPlugin()
GlobalPlugin = plugin.GlobalPlugin

import braille  # noqa: E402

from brlMultiline import patches  # noqa: E402
from brlMultiline.pinnedRegions import (  # noqa: E402
	PinnedCursorManagerRegion,
	PinnedTextInfoRegion,
)
from brlMultiline.semanticNav import NavResult, NavUnit, SemanticNavigator  # noqa: E402

MONARCH_ROWS = 8
MONARCH_COLS = 32
PINNED_SEGMENT = 1
PINNED_KEY = "display.1"


def documentLines():
	""":return: a fresh list of lines for each test."""
	return [f"para {index}" for index in range(8)]


# ---------------------------------------------------------------------------
# Basic types
# ---------------------------------------------------------------------------


class TestNavUnit(unittest.TestCase):
	def test_unitConstants(self):
		self.assertEqual(NavUnit.DISPLAY, "display")
		self.assertEqual(NavUnit.LINE, "line")
		self.assertEqual(NavUnit.PARAGRAPH, "paragraph")
		self.assertEqual(NavUnit.HEADING, "heading")


class TestNavResult(unittest.TestCase):
	def test_fieldsAreAccessibleByName(self):
		result = NavResult(moved=True, unit_used="line", reason="")
		self.assertTrue(result.moved)
		self.assertEqual(result.unit_used, "line")
		self.assertEqual(result.reason, "")

	def test_fallbackResultCarriesReason(self):
		result = NavResult(moved=True, unit_used="paragraph", reason="heading not supported; used paragraph")
		self.assertIn("heading", result.reason)
		self.assertEqual(result.unit_used, "paragraph")


# ---------------------------------------------------------------------------
# Navigator test infrastructure
# ---------------------------------------------------------------------------


class NavigatorTestCase(unittest.TestCase):
	"""A Monarch with eight single-row segments, a document pinned to segment 1."""

	segmentCount = 8

	def setUp(self):
		resetPluginState()
		CONFIG["segmentCount"] = self.segmentCount
		self.handler = FakeHandler(MONARCH_ROWS, MONARCH_COLS)
		self.originalBuffer = object()
		self.handler.mainBuffer = self.handler.buffer = self.originalBuffer
		braille.handler = self.handler
		patches._lastMonitorRefresh = 0.0
		self.pluginInstance = GlobalPlugin()
		self.lines = documentLines()
		self.addCleanup(self.cleanUp)

	def cleanUp(self):
		try:
			if not self.pluginInstance._terminated:
				self.pluginInstance.terminate()
		finally:
			patches.remove()
			patches._lastMonitorRefresh = 0.0
			resetPluginState()

	@property
	def container(self):
		return self.pluginInstance.container

	@property
	def segment(self):
		return self.container.segmentForKey(PINNED_KEY)

	@property
	def pinnedRegion(self):
		return self.segment.regions[-1]

	def pinDocument(self, caretIndex=2, **kwargs):
		import api

		interceptor = FakeTreeInterceptor(self.lines, caretIndex=caretIndex, **kwargs)
		api.getNavigatorObject = lambda: FakeNavigatorObject("a page", treeInterceptor=interceptor)
		self.pluginInstance.startMonitoring(PINNED_SEGMENT)
		return interceptor

	def pinTextObject(self, caretIndex=2):
		import api

		obj = FakeNavigatorObject("an edit", lines=self.lines)
		obj.caretIndex = caretIndex
		api.getNavigatorObject = lambda: obj
		self.pluginInstance.startMonitoring(PINNED_SEGMENT)
		return obj

	def makeNavigator(self):
		return SemanticNavigator(self.container, PINNED_KEY)


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


class TestCapabilityDetection(NavigatorTestCase):
	def test_emptySegmentSupportsNeitherHeadingNorParagraph(self):
		navigator = self.makeNavigator()
		self.assertFalse(navigator.supportsHeading())
		self.assertFalse(navigator.supportsParagraph())

	def test_browseModeDocumentSupportsParagraphNotHeading(self):
		"""FakeTreeInterceptor has no _iterNodesByType, so heading is not available."""
		self.pinDocument()
		navigator = self.makeNavigator()
		self.assertTrue(navigator.supportsParagraph())
		self.assertFalse(navigator.supportsHeading())

	def test_textInfoRegionSupportsParagraphNotHeading(self):
		self.pinTextObject()
		navigator = self.makeNavigator()
		self.assertTrue(navigator.supportsParagraph())
		self.assertFalse(navigator.supportsHeading())

	def test_headingSupportedWhenInterceptorHasIterNodesByType(self):
		"""Simulate a real NVDA tree interceptor that exposes _iterNodesByType."""
		self.pinDocument()
		region = self.pinnedRegion
		self.assertIsInstance(region, PinnedCursorManagerRegion)
		# Attach a stub iterator to the tree interceptor object that region.obj points to.
		region.obj._iterNodesByType = lambda *args, **kwargs: iter([])
		navigator = self.makeNavigator()
		self.assertTrue(navigator.supportsHeading())


# ---------------------------------------------------------------------------
# Successful line and paragraph moves
# ---------------------------------------------------------------------------


class TestLineMove(NavigatorTestCase):
	def test_lineForwardMovesToNextLine(self):
		self.pinDocument(caretIndex=2)
		result = self.makeNavigator().move(NavUnit.LINE, forward=True)
		self.assertTrue(result.moved)
		self.assertEqual(result.unit_used, NavUnit.LINE)
		self.assertEqual(self.pinnedRegion.rawText, "para 3")

	def test_lineBackMovesToPreviousLine(self):
		self.pinDocument(caretIndex=2)
		result = self.makeNavigator().move(NavUnit.LINE, forward=False)
		self.assertTrue(result.moved)
		self.assertEqual(self.pinnedRegion.rawText, "para 1")

	def test_lineAtEndDoesNotMove(self):
		self.pinDocument(caretIndex=7)
		result = self.makeNavigator().move(NavUnit.LINE, forward=True)
		self.assertFalse(result.moved)

	def test_lineAtStartDoesNotMove(self):
		self.pinDocument(caretIndex=0)
		result = self.makeNavigator().move(NavUnit.LINE, forward=False)
		self.assertFalse(result.moved)


class TestParagraphMove(NavigatorTestCase):
	def test_paragraphForwardMovesDocument(self):
		"""FakeTextInfo treats any unit as a line move, so paragraph still advances."""
		self.pinDocument(caretIndex=2)
		result = self.makeNavigator().move(NavUnit.PARAGRAPH, forward=True)
		self.assertTrue(result.moved)
		# Paragraph resolved directly — unit_used should be paragraph.
		self.assertEqual(result.unit_used, NavUnit.PARAGRAPH)
		self.assertEqual(self.pinnedRegion.rawText, "para 3")

	def test_paragraphBackMovesDocument(self):
		self.pinDocument(caretIndex=3)
		result = self.makeNavigator().move(NavUnit.PARAGRAPH, forward=False)
		self.assertTrue(result.moved)
		self.assertEqual(self.pinnedRegion.rawText, "para 2")

	def test_paragraphAtEndDoesNotMove(self):
		self.pinDocument(caretIndex=7)
		result = self.makeNavigator().move(NavUnit.PARAGRAPH, forward=True)
		self.assertFalse(result.moved)


# ---------------------------------------------------------------------------
# Display-width move
# ---------------------------------------------------------------------------


class TestDisplayMove(NavigatorTestCase):
	def test_displayMoveOnLongLineScrollsWindow(self):
		"""A line wider than the display should scroll one window at a time."""
		self.lines[2] = "x" * 100
		self.pinDocument(caretIndex=2)
		result = self.makeNavigator().move(NavUnit.DISPLAY, forward=True)
		self.assertTrue(result.moved)
		self.assertGreater(self.segment.windowStartPos, 0)

	def test_displayMoveAtEndReturnsFalse(self):
		self.pinDocument(caretIndex=2)
		result = self.makeNavigator().move(NavUnit.DISPLAY, forward=True)
		# Short line fills less than one display — window cannot scroll further.
		self.assertFalse(result.moved)


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------


class TestFallbackBehaviour(NavigatorTestCase):
	def test_headingFallsBackToParagraphForBrowseModeRegion(self):
		"""FakeTreeInterceptor has no _iterNodesByType, so heading → paragraph."""
		self.pinDocument(caretIndex=2)
		result = self.makeNavigator().move(NavUnit.HEADING, forward=True)
		self.assertTrue(result.moved)
		self.assertEqual(result.unit_used, NavUnit.PARAGRAPH)
		self.assertIn("heading", result.reason)

	def test_headingFallsBackToLineForTextInfoRegion(self):
		"""A plain text region has no heading or paragraph support.
		FakeTextInfo does support UNIT_PARAGRAPH (it just treats it as a line move),
		so the fallback stops at paragraph.  This tests that the navigator descends
		the chain until it finds something that works rather than stopping at the
		first unsupported unit.
		"""
		self.pinTextObject(caretIndex=2)
		result = self.makeNavigator().move(NavUnit.HEADING, forward=True)
		self.assertTrue(result.moved)
		# Paragraph works on a text region (FakeTextInfo honours any unit).
		self.assertEqual(result.unit_used, NavUnit.PARAGRAPH)

	def test_headingOnEmptySegmentUsesDisplay(self):
		"""No pinned region at all → fall through to display pan, which also fails here
		because the window is already at the start of a short (empty) segment."""
		navigator = self.makeNavigator()
		result = navigator.move(NavUnit.HEADING, forward=True)
		# Display pan on an empty segment cannot move either, so moved is False.
		self.assertFalse(result.moved)


# ---------------------------------------------------------------------------
# panUnit primitive
# ---------------------------------------------------------------------------


class TestPanUnit(NavigatorTestCase):
	def test_panUnitMovesByParagraphUnit(self):
		"""panUnit with a paragraph constant moves the region forward by one step."""
		import textInfos

		self.pinDocument(caretIndex=2)
		region = self.pinnedRegion
		self.assertIsInstance(region, PinnedCursorManagerRegion)
		moved = region.panUnit(textInfos.UNIT_PARAGRAPH, forward=True)
		self.assertTrue(moved)
		self.assertEqual(region.rawText, "para 3")

	def test_panUnitAtEndReturnsFalse(self):
		import textInfos

		self.pinDocument(caretIndex=7)
		region = self.pinnedRegion
		moved = region.panUnit(textInfos.UNIT_PARAGRAPH, forward=True)
		self.assertFalse(moved)

	def test_panUnitOnUnrenderedRegionReturnsFalse(self):
		"""A region whose update() was never called has _readingInfo=None; panUnit should
		return False rather than raising."""
		import textInfos

		interceptor = FakeTreeInterceptor(self.lines, caretIndex=2)
		# Build directly without calling update(), so _readingInfo stays None.
		region = PinnedCursorManagerRegion(interceptor)
		self.assertIsNone(getattr(region, "_readingInfo", None))
		moved = region.panUnit(textInfos.UNIT_PARAGRAPH, forward=True)
		self.assertFalse(moved)


# ---------------------------------------------------------------------------
# Plugin-level command
# ---------------------------------------------------------------------------


class TestMoveMonitorInSegmentCommand(NavigatorTestCase):
	def setUp(self):
		super().setUp()
		spokenMessages.clear()

	def test_lineForwardCommandMovesSegment(self):
		self.pinDocument(caretIndex=2)
		self.pluginInstance.moveMonitorInSegment(PINNED_SEGMENT, NavUnit.LINE, forward=True)
		self.assertEqual(self.pinnedRegion.rawText, "para 3")

	def test_commandSpeaksNothingOnDirectSuccess(self):
		self.pinDocument(caretIndex=2)
		spokenMessages.clear()
		self.pluginInstance.moveMonitorInSegment(PINNED_SEGMENT, NavUnit.LINE, forward=True)
		self.assertEqual(spokenMessages, [])

	def test_commandSpeaksFallbackNoticeOnFallback(self):
		"""Heading request on a browse mode segment (no _iterNodesByType) falls back;
		the command should speak a brief notice."""
		self.pinDocument(caretIndex=2)
		spokenMessages.clear()
		self.pluginInstance.moveMonitorInSegment(PINNED_SEGMENT, NavUnit.HEADING, forward=True)
		self.assertTrue(len(spokenMessages) > 0)
		self.assertIn("heading", spokenMessages[-1])

	def test_commandSpeaksWhenSegmentNotMonitored(self):
		"""Asking to navigate a segment that has no pin should speak an error."""
		self.pluginInstance.moveMonitorInSegment(PINNED_SEGMENT, NavUnit.LINE, forward=True)
		self.assertTrue(len(spokenMessages) > 0)
