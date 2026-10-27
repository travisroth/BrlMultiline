# BrlMultiline: regressions from the review of 814bf39.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The five faults the review of 814bf39 found in quick navigation in a pinned document.

Taken from the reviewer's `review_pin_quick_nav_regressions_814bf39.py`, which failed on 814bf39
and passes with the fixes, with a few cases of the fixes' own. No NVDA or hardware. See "Review of
814bf39" in `docs/design/pinned-quick-nav-plan.md`.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from . import test_flowControl as flowHarness
from . import test_pinQuickNav as navHarness
from . import test_plugin as harness

from brlMultiline import keyLayerContexts, pinQuickNav  # noqa: E402

import textInfos  # noqa: E402

LINK = next(kind for kind in pinQuickNav.TYPES if kind.itemType == "link")


class TestTheExactPositionIsKept(unittest.TestCase):
	"""Finding 1: a search started from the top of the block, so it found the same link again."""

	def linksAt(self, control, offsets, reported):
		document = control.source.obj

		def search(itemType, direction="next", pos=None):
			if direction == "next":
				selected = [at for at in offsets if at > pos.offset]
			else:
				selected = [at for at in reversed(offsets) if at < pos.offset]
			return iter(
				SimpleNamespace(textInfo=document._position(0, at), report=lambda at=at: reported.append(at))
				for at in selected
			)

		document._iterNodesByType = search

	def test_repeatedLinksInOneLineAdvance(self):
		control = flowHarness.controllerOver(["abcdefghijklmnopqrstuvwx", "tail"], numCols=8, numRows=2)
		reported = []
		self.linksAt(control, [4, 12, 20], reported)
		monitor = navHarness.FakeMonitor(controller=control)
		self.assertIsNone(pinQuickNav.move(monitor, LINK, True))
		self.assertIsNone(pinQuickNav.move(monitor, LINK, True))
		self.assertEqual(reported, [4, 12])

	def test_theTopOfAWrappedBlockIsWhereTheReaderIs(self):
		control = flowHarness.controllerOver(["a" * 32, "tail"], numCols=8, numRows=2)
		self.assertTrue(control.panForward())
		self.assertEqual(control.window.anchor.rowIndex, 2)
		self.assertEqual(control.topPosition().offset, 16)

	def test_panningAfterAJumpSearchesFromWhatIsShown(self):
		control = flowHarness.controllerOver(["abcdefghijklmnopqrstuvwx", "tail"], numCols=8, numRows=2)
		reported = []
		self.linksAt(control, [4, 12, 20], reported)
		monitor = navHarness.FakeMonitor(controller=control)
		pinQuickNav.move(monitor, LINK, True)
		self.assertTrue(control.panForward())
		pinQuickNav.move(monitor, LINK, True)
		self.assertEqual(reported, [4, 20], "from the row panned to, at 16, not from the link found at 4")


class TestAnOrdinaryDisplay(harness.PluginTestCase):
	"""Finding 2: a display connected on its own has no map of members, and was taken to have none."""

	def setUp(self):
		super().setUp()
		self.pin(0, "doc one")
		self.pin(2, "doc two")
		self.display = self.handler.display.name
		patchers = [
			mock.patch.object(
				pinQuickNav,
				"documentOf",
				side_effect=lambda monitor: monitor if monitor.name.startswith("doc") else None,
			),
			mock.patch.object(
				harness.plugin.keyLayerDispatch, "displayFor", side_effect=lambda gesture: self.display,
			),
		]
		for patcher in patchers:
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_theLayerKeyFindsAPinnedDocument(self):
		self.assertIn(
			"pinned", keyLayerContexts.presentContexts(self.plugin, tables=False, device=self.display),
		)

	def test_panningAnotherPinChoosesIt(self):
		self.container.scrollForward(2)
		key, monitor = self.plugin.pinForQuickNav(object())
		self.assertEqual(key, "display.2")
		self.assertEqual(monitor.name, "doc two")


class TestWhatIsSpoken(unittest.TestCase):
	"""Finding 4: a table or a list was spoken whole, where NVDA speaks a line of it."""

	def unitSpokenFor(self, itemType):
		document = navHarness.FakeDocument(headings=[4], supported=(itemType,))
		spokenUnits = []
		document._iterNodesByType = lambda *args: iter(
			[
				SimpleNamespace(
					textInfo=navHarness.FakeInfo(4),
					report=lambda readUnit=None: spokenUnits.append(readUnit),
				),
			],
		)
		monitor = navHarness.FakeMonitor(controller=navHarness.FakeController(document))
		kind = next(kind for kind in pinQuickNav.TYPES if kind.itemType == itemType)
		self.assertIsNone(pinQuickNav.move(monitor, kind, True))
		return spokenUnits

	def test_aTableALine(self):
		self.assertEqual(self.unitSpokenFor("table"), [textInfos.UNIT_LINE])

	def test_aListALine(self):
		self.assertEqual(self.unitSpokenFor("list"), [textInfos.UNIT_LINE])


class TestAFailedMoveIsSaid(unittest.TestCase):
	"""Finding 5: a pin that could not be moved was reported as moved."""

	def test_aControllerThatCannotEnterThere(self):
		document = navHarness.FakeDocument(headings=[4])
		control = navHarness.FakeController(document)
		control.enterAtPosition = lambda info: False
		problem = pinQuickNav.move(navHarness.FakeMonitor(controller=control), pinQuickNav.TYPES[0], True)
		self.assertEqual(problem, "Cannot show that in this pin")

	def test_aControllerThatRaises(self):
		document = navHarness.FakeDocument(headings=[4])
		control = navHarness.FakeController(document)

		def broken(info):
			raise RuntimeError("gone")

		control.enterAtPosition = broken
		problem = pinQuickNav.move(navHarness.FakeMonitor(controller=control), pinQuickNav.TYPES[0], True)
		self.assertEqual(problem, "Cannot show that in this pin")


class TestTheSearcherIsSafe(unittest.TestCase):
	"""Finding 3: building any browse mode document to search could disturb NVDA's own."""

	def test_aLiveVirtualBufferIsNotReplaced(self):
		class BrowserBuffer:
			rootIdentifiers = {}

			def __init__(self, document):
				self.rootNVDAObject = document
				self.VBufHandle = None
				self.rootIdentifiers[document.identifier] = self

			def _iterNodesByType(self, *args):
				return iter(())

		document = SimpleNamespace(treeInterceptorClass=BrowserBuffer, identifier=(10, 20))
		live = BrowserBuffer(document)
		live.VBufHandle = object()
		live.isReady = True
		document.treeInterceptor = live
		pinQuickNav.searcherFor(document)
		self.assertIs(BrowserBuffer.rootIdentifiers[document.identifier], live)

	def test_NVDAsOwnReadyDocumentIsTheOneSearched(self):
		live = SimpleNamespace(isReady=True, _iterNodesByType=lambda *args: iter(()))
		document = SimpleNamespace(treeInterceptor=live, treeInterceptorClass=None)
		self.assertIs(pinQuickNav.searcherFor(document), live)

	def test_aKindNotKnownToBeSafeIsNotBuilt(self):
		built = []

		class VirtualBuffer:
			def __init__(self, document):
				built.append(self)

			def _iterNodesByType(self, *args):
				return iter(())

		document = SimpleNamespace(treeInterceptor=None, treeInterceptorClass=VirtualBuffer)
		self.assertIsNone(pinQuickNav.searcherFor(document))
		self.assertEqual(built, [])


if __name__ == "__main__":
	unittest.main()
