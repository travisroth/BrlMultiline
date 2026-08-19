# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for telling a jump by structure apart from a move by reading.

A quick navigation key that skips a section should set the reader down at what it found,
with the document running on from there. An arrow key, a tab, or a move within a section
should keep the window the reader has and merely track the cursor. Which is which is a
stated policy rather than something inferred, so most of this is about that policy holding.
"""

import sys
import types
import unittest

from ._stubs import CursorManagerRegion, FakeHandler, FakeTreeInterceptor, installStubs

installStubs()

from brlMultiline import flowQuickNav  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402
from brlMultiline.flowSources import DocumentFlowSource, regionFactoryFor  # noqa: E402


class FakeBrowseMode:
	"""Enough of NVDA's browse mode to be patched and to call through."""

	calls = []
	findsItem = True

	def _quickNavScript(self, gesture, itemType, direction, errorMessage, readUnit):
		FakeBrowseMode.calls.append((itemType, direction))
		if self.findsItem:
			FakeQuickNavItem().moveTo()


class FakeQuickNavItem:
	"""The success seam NVDA reaches only after its quick-navigation search found an item."""

	moves = 0

	def moveTo(self):
		FakeQuickNavItem.moves += 1


ORIGINAL_QUICK_NAV = FakeBrowseMode._quickNavScript
ORIGINAL_MOVE_TO = FakeQuickNavItem.moveTo


def installFakeBrowseMode():
	"""Register a browse mode module the patch can find, as NVDA's own would be."""
	module = types.ModuleType("browseMode")
	module.BrowseModeTreeInterceptor = FakeBrowseMode
	module.TextInfoQuickNavItem = FakeQuickNavItem
	sys.modules["browseMode"] = module
	return module


class QuickNavTestCase(unittest.TestCase):
	def setUp(self):
		FakeBrowseMode.calls = []
		FakeBrowseMode.findsItem = True
		FakeQuickNavItem.moves = 0
		FakeBrowseMode._quickNavScript = ORIGINAL_QUICK_NAV
		FakeQuickNavItem.moveTo = ORIGINAL_MOVE_TO
		installFakeBrowseMode()
		flowQuickNav.forget()
		self.addCleanup(sys.modules.pop, "browseMode", None)
		# LIFO: restore both patched methods while their module is still importable, then
		# remove the fake module.
		self.addCleanup(flowQuickNav.remove)


class TestThePolicy(QuickNavTestCase):
	def test_headingsGroundIncludingTheNumberedLevels(self):
		self.assertTrue(flowQuickNav.groundsOn("heading"))
		self.assertTrue(flowQuickNav.groundsOn("heading1"))
		self.assertTrue(flowQuickNav.groundsOn("heading6"))

	def test_structuralJumpsGround(self):
		for itemType in ("table", "list", "landmark", "frame", "article", "blockQuote"):
			with self.subTest(itemType=itemType):
				self.assertTrue(flowQuickNav.groundsOn(itemType))

	def test_movesWithinASectionDoNot(self):
		# `l` moves to the next list, which is a change of section; `i` moves to the next
		# item, which is a step within one and reads like arrowing.
		for itemType in ("listItem", "link", "formField", "button", "edit", "checkBox"):
			with self.subTest(itemType=itemType):
				self.assertFalse(flowQuickNav.groundsOn(itemType))

	def test_readingMovesDoNot(self):
		for itemType in ("textParagraph", "verticalParagraph", "sameStyle", "differentStyle"):
			with self.subTest(itemType=itemType):
				self.assertFalse(flowQuickNav.groundsOn(itemType))


class TestHearingTheKey(QuickNavTestCase):
	def test_theSeamIsPatchedAndCallsThrough(self):
		flowQuickNav.install()
		FakeBrowseMode()._quickNavScript(None, "heading", "next", "", None)
		self.assertEqual(FakeBrowseMode.calls, [("heading", "next")])
		self.assertEqual(FakeQuickNavItem.moves, 1)
		self.assertTrue(flowQuickNav.takeGrounding())

	def test_aSearchThatFindsNothingDoesNotGroundTheNextMove(self):
		flowQuickNav.install()
		FakeBrowseMode.findsItem = False
		FakeBrowseMode()._quickNavScript(None, "heading", "next", "no more headings", None)
		self.assertFalse(flowQuickNav.takeGrounding())

	def test_aFailedSearchClearsAnOlderPendingJump(self):
		flowQuickNav.note("heading")
		flowQuickNav.install()
		FakeBrowseMode.findsItem = False
		FakeBrowseMode()._quickNavScript(None, "heading", "next", "no more headings", None)
		self.assertFalse(flowQuickNav.takeGrounding())

	def test_theSeamIsGivenBack(self):
		flowQuickNav.install()
		flowQuickNav.remove()
		FakeBrowseMode()._quickNavScript(None, "heading", "next", "", None)
		self.assertFalse(flowQuickNav.takeGrounding())
		self.assertIs(FakeBrowseMode._quickNavScript, ORIGINAL_QUICK_NAV)
		self.assertIs(FakeQuickNavItem.moveTo, ORIGINAL_MOVE_TO)

	def test_installingTwiceKeepsOneOriginal(self):
		flowQuickNav.install()
		flowQuickNav.install()
		flowQuickNav.remove()
		self.assertIs(FakeBrowseMode._quickNavScript, ORIGINAL_QUICK_NAV)
		self.assertIs(FakeQuickNavItem.moveTo, ORIGINAL_MOVE_TO)

	def test_aGroundingMoveIsAnsweredOnlyOnce(self):
		# One keypress causes one caret move. A note left in place would ground every
		# arrow key after it.
		flowQuickNav.note("heading")
		self.assertTrue(flowQuickNav.takeGrounding())
		self.assertFalse(flowQuickNav.takeGrounding())

	def test_aMoveWithinASectionClearsAnOlderNote(self):
		flowQuickNav.note("heading")
		flowQuickNav.note("listItem")
		self.assertFalse(flowQuickNav.takeGrounding())

	def test_aStaleNoteIsNotActedOn(self):
		clock = [1000.0]
		original = flowQuickNav.time.monotonic
		flowQuickNav.time.monotonic = lambda: clock[0]
		self.addCleanup(setattr, flowQuickNav.time, "monotonic", original)
		flowQuickNav.note("heading")
		clock[0] += flowQuickNav.GROUNDING_WINDOW + 1
		self.assertFalse(flowQuickNav.takeGrounding())


class TestGrounding(unittest.TestCase):
	"""What the window does when a jump by structure is answered."""

	def _flow(self, caretIndex=0, numRows=4, numCols=8):
		lines = [f"line {number}" for number in range(40)]
		interceptor = FakeTreeInterceptor(lines, caretIndex=caretIndex)
		source = DocumentFlowSource(
			interceptor,
			regionFactoryFor(CursorManagerRegion(interceptor), live=True),
			generation=1,
		)
		render = FlowRenderer(FakeHandler(numRows, numCols), numCols=numCols, fillRows=True)
		control = FlowController(source, render, numRows=numRows, live=True)
		control.enterAtCursor()
		return control

	def _rows(self, control):
		numCols = control.renderer.numCols
		cells = control.cells()
		return [
			"".join(chr(cell) if cell else " " for cell in cells[index * numCols : (index + 1) * numCols])
			for index in range(control.window.numRows)
		]

	def test_groundingPutsTheTargetAtTheTop(self):
		control = self._flow()
		control.source.obj.caretIndex = 2
		control.followCursor(ground=True)
		self.assertEqual(self._rows(control)[0], "line 2  ")

	def test_followingWithoutGroundingKeepsTheWindow(self):
		# The same move, not grounded: the block is already on the display, so nothing
		# moves at all.
		control = self._flow()
		before = self._rows(control)
		control.source.obj.caretIndex = 2
		control.followCursor(ground=False)
		self.assertEqual(self._rows(control), before)

	def test_groundingFlowsOnFromTheTarget(self):
		control = self._flow()
		control.source.obj.caretIndex = 10
		control.followCursor(ground=True)
		self.assertEqual(self._rows(control), ["line 10 ", "line 11 ", "line 12 ", "line 13 "])

	def test_groundingBackwardsAlsoStartsAtTheTarget(self):
		control = self._flow(caretIndex=20)
		control.source.obj.caretIndex = 12
		control.followCursor(ground=True)
		self.assertEqual(self._rows(control)[0], "line 12 ")

	def test_theCursorIsStillShownAfterGrounding(self):
		control = self._flow()
		control.source.obj.caretIndex = 6
		control.followCursor(ground=True)
		self.assertIsNotNone(control.cursorCell())


if __name__ == "__main__":
	unittest.main()
