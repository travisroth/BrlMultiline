# BrlMultiline: tests for quick navigation in a pinned document.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""A pinned document is searched from its own position and moved, and nothing else is."""

import builtins
import types
import unittest
from unittest import mock

from ._stubs import installStubs

installStubs()

from brlMultiline import pinQuickNav  # noqa: E402
from brlMultiline.pinQuickNav import TYPES, documentOf, move, positionOf  # noqa: E402

HEADING = TYPES[0]


class FakeInfo:
	def __init__(self, where):
		self.where = where
		self.collapsed = False

	def copy(self):
		copied = FakeInfo(self.where)
		copied.collapsed = self.collapsed
		return copied

	def collapse(self):
		self.collapsed = True


class FakeItem:
	def __init__(self, where):
		self.textInfo = FakeInfo(where)
		self.reported = False

	def report(self, readUnit=None):
		self.reported = True


class FakeDocument:
	"""A browse mode document with headings at the given places."""

	def __init__(self, headings=(), supported=("heading",)):
		self.headings = list(headings)
		self.supported = supported
		self.searches = []

	def _iterNodesByType(self, itemType, direction="next", pos=None):
		self.searches.append((itemType, direction, pos.where))
		if itemType not in self.supported:
			raise NotImplementedError
		if direction == "next":
			found = [where for where in self.headings if where > pos.where]
		else:
			found = [where for where in reversed(self.headings) if where < pos.where]
		return iter(FakeItem(where) for where in found)

	def makeTextInfo(self, position):
		return FakeInfo(0)


class FakeController:
	def __init__(self, document, top=0):
		self.source = types.SimpleNamespace(obj=document)
		self.top = top
		self.enteredAt = None

	def topPosition(self):
		return FakeInfo(self.top)

	def enterAtPosition(self, info):
		self.enteredAt = info
		return True


class FakeMonitor:
	def __init__(self, controller=None, obj=None, regions=None):
		self.controller = controller
		self.obj = obj
		self.regions = regions
		self.refreshes = []

	def refresh(self, reveal=False):
		self.refreshes.append(reveal)


class FakePinnedRegion:
	def __init__(self, where):
		self.position = FakeInfo(where)

	def _getSelection(self):
		return self.position.copy()

	def _setCursor(self, info):
		self.position = info


class TestWhatCanBeSearched(unittest.TestCase):
	def test_aFlowsDocument(self):
		document = FakeDocument()
		self.assertIs(documentOf(FakeMonitor(controller=FakeController(document))), document)

	def test_aPinReadAsRegions(self):
		document = FakeDocument()
		self.assertIs(documentOf(FakeMonitor(obj=document)), document)

	def test_aRunOfObjectsIsNot(self):
		run = types.SimpleNamespace()
		self.assertIsNone(documentOf(FakeMonitor(controller=FakeController(run))))

	def test_theSearchStartsAtTheTopOfTheWindow(self):
		document = FakeDocument()
		self.assertEqual(
			positionOf(FakeMonitor(controller=FakeController(document, top=7)), document).where, 7
		)

	def test_orAtAPinnedRegionsOwnPosition(self):
		document = FakeDocument()
		monitor = FakeMonitor(obj=document, regions=[FakePinnedRegion(5)])
		self.assertEqual(positionOf(monitor, document).where, 5)


class TestMoving(unittest.TestCase):
	def test_theNextHeadingGoesToTheTop(self):
		document = FakeDocument(headings=[3, 9, 20])
		control = FakeController(document, top=9)
		monitor = FakeMonitor(controller=control)
		self.assertIsNone(move(monitor, HEADING, forward=True))
		self.assertEqual(control.enteredAt.where, 20)
		self.assertTrue(control.enteredAt.collapsed)
		self.assertEqual(monitor.refreshes, [False])
		self.assertEqual(document.searches, [("heading", "next", 9)])

	def test_thePreviousHeading(self):
		document = FakeDocument(headings=[3, 9, 20])
		control = FakeController(document, top=9)
		move(FakeMonitor(controller=control), HEADING, forward=False)
		self.assertEqual(control.enteredAt.where, 3)

	def test_whatIsFoundIsSpokenAsNVDASpeaksIt(self):
		document = FakeDocument(headings=[4])
		found = []
		original = document._iterNodesByType

		def keeping(*args, **kwargs):
			items = list(original(*args, **kwargs))
			found.extend(items)
			return iter(items)

		document._iterNodesByType = keeping
		move(FakeMonitor(controller=FakeController(document)), HEADING, forward=True)
		self.assertTrue(found[0].reported)

	def test_noneFoundSaysNVDAsOwnMessage(self):
		document = FakeDocument(headings=[1])
		control = FakeController(document, top=5)
		self.assertEqual(move(FakeMonitor(controller=control), HEADING, forward=True), "no next heading")
		self.assertIsNone(control.enteredAt)

	def test_aTypeTheDocumentCannotSearch(self):
		document = FakeDocument(supported=())
		problem = move(FakeMonitor(controller=FakeController(document)), HEADING, forward=True)
		self.assertEqual(problem, "Not supported in this document")

	def test_aPinThatIsNotADocument(self):
		problem = move(FakeMonitor(controller=FakeController(types.SimpleNamespace())), HEADING, forward=True)
		self.assertEqual(problem, "Not supported in this document")

	def test_aPinReadAsRegionsHasItsOwnPositionMoved(self):
		document = FakeDocument(headings=[8])
		region = FakePinnedRegion(2)
		monitor = FakeMonitor(obj=document, regions=[region])
		self.assertIsNone(move(monitor, HEADING, forward=True))
		self.assertEqual(region.position.where, 8)
		self.assertEqual(monitor.refreshes, [True])


class FakeProxyInfo:
	"""A browse mode document's position, wrapping the document object's own."""

	def __init__(self, inner):
		self.innerTextInfo = inner
		self.where = inner.where

	def copy(self):
		return FakeProxyInfo(self.innerTextInfo.copy())

	def collapse(self):
		self.innerTextInfo.collapse()


class UIABrowseModeDocument:
	"""Named as NVDA's own is, which is one of the kinds `pinQuickNav` may build to search."""


class FakeBrowseMode(FakeDocument, UIABrowseModeDocument):
	"""What NVDA+Space would make of a document object: it searches, and wraps its positions."""

	made = []

	def __init__(self, rootNVDAObject):
		super().__init__(headings=rootNVDAObject.headings)
		self.rootNVDAObject = rootNVDAObject
		FakeBrowseMode.made.append(self)

	def makeTextInfo(self, position):
		return FakeProxyInfo(position)

	def _iterNodesByType(self, itemType, direction="next", pos=None):
		assert isinstance(pos, FakeProxyInfo), "the search takes the browse mode document's own positions"
		return iter(
			types.SimpleNamespace(textInfo=FakeProxyInfo(item.textInfo), report=lambda readUnit=None: None)
			for item in super()._iterNodesByType(itemType, direction, pos)
		)


class FakeWordDocument:
	"""A document object with no browse mode document yet, as the Outlook reading pane is."""

	treeInterceptorClass = FakeBrowseMode

	def __init__(self, headings):
		self.headings = headings


class TestADocumentObjectWithoutBrowseMode(unittest.TestCase):
	"""Found on hardware: the Outlook reading pane is read from its Word document object."""

	def setUp(self):
		FakeBrowseMode.made.clear()

	def test_itCanBeSearched(self):
		document = FakeWordDocument(headings=[4])
		self.assertIs(documentOf(FakeMonitor(controller=FakeController(document))), document)

	def test_anObjectWithNoBrowseModeCannot(self):
		class Plain:
			@property
			def treeInterceptorClass(self):
				raise NotImplementedError

		self.assertIsNone(documentOf(FakeMonitor(controller=FakeController(Plain()))))

	def test_itIsSearchedThroughABrowseModeDocumentMadeForIt(self):
		document = FakeWordDocument(headings=[4, 12])
		control = FakeController(document, top=4)
		self.assertIsNone(move(FakeMonitor(controller=control), HEADING, forward=True))
		self.assertEqual(len(FakeBrowseMode.made), 1)
		self.assertIs(FakeBrowseMode.made[0].rootNVDAObject, document)

	def test_thePinIsGivenThePositionAsTheObjectReadsIt(self):
		document = FakeWordDocument(headings=[4, 12])
		control = FakeController(document, top=4)
		move(FakeMonitor(controller=control), HEADING, forward=True)
		self.assertIsInstance(control.enteredAt, FakeInfo)
		self.assertEqual(control.enteredAt.where, 12)


class TestWhichPin(unittest.TestCase):
	"""Which pinned document a key is for: on the display pressed, the one last used there."""

	def setUp(self):
		from . import test_plugin as harness

		self.case = type(
			"Case", (harness.PluginTestCase,), {"segmentCount": 4, "runTest": lambda self: None}
		)()
		self.case.setUp()
		self.addCleanup(self.case.doCleanups)
		self.addCleanup(self.case.tearDown)
		self.plugin = self.case.plugin
		pluginModule = harness.plugin
		keyLayerDispatch = pluginModule.keyLayerDispatch
		from brlMultiline.devices import DeviceInfo

		def patch(target, name, value):
			patcher = mock.patch.object(target, name, value)
			patcher.start()
			self.addCleanup(patcher.stop)

		patch(pinQuickNav, "documentOf", lambda monitor: monitor if monitor.name.startswith("doc") else None)
		patch(keyLayerDispatch, "displayFor", lambda gesture: gesture)
		self.case.pin(0, "doc one")
		self.case.pin(1, "clock")
		self.case.pin(2, "doc two")
		self.keys = [segment.key for segment in self.plugin.container.segments]
		onMonarch = self.keys[0:3]
		patch(self.plugin, "_segmentKeysOn", lambda display: onMonarch if display == "monarch" else [])
		patch(pluginModule, "deviceMap", lambda: [DeviceInfo("monarch", 0, 100, 100)])

	def target(self, display="monarch"):
		return self.plugin.pinForQuickNav(display)[0]

	def test_theFirstPinnedDocumentOnTheDisplay(self):
		self.assertEqual(self.target(), self.keys[0])

	def test_thePinnedDocumentLastRoutedIn(self):
		segment = self.plugin.container.segments[2]
		self.plugin._noteSegmentUsed(segment.key, segment.rect)
		self.assertEqual(self.target(), self.keys[2])

	def test_aPinThatIsNotADocumentIsPassedOver(self):
		segment = self.plugin.container.segments[1]
		self.plugin._noteSegmentUsed(segment.key, segment.rect)
		self.assertEqual(self.target(), self.keys[0])

	def test_theNextPinnedDocumentIsChosenAndSaid(self):
		from ._stubs import spokenMessages

		spokenMessages.clear()
		self.plugin.script_pinNextDocument("monarch")
		self.assertEqual(self.target(), self.keys[2])
		self.assertEqual(spokenMessages[-1], "doc two")
		self.plugin.script_pinNextDocument("monarch")
		self.assertEqual(self.target(), self.keys[0])

	def test_aKeyWithNoDisplayTakesTheFirstInDisplayOrder(self):
		self.assertEqual(self.target(display=None), self.keys[0])

	def test_theLayerKeyFindsAPinnedDocumentOnlyOnItsDisplay(self):
		from brlMultiline import keyLayerContexts

		self.assertIn("pinned", keyLayerContexts.presentContexts(self.plugin, tables=False, device="monarch"))
		self.assertNotIn(
			"pinned", keyLayerContexts.presentContexts(self.plugin, tables=False, device="focus")
		)

	def test_nothingPinnedSaysSo(self):
		from ._stubs import spokenMessages

		for number in (0, 1, 2):
			self.plugin.stopMonitoring(number)
		spokenMessages.clear()
		self.plugin.quickNavInPin("monarch", HEADING, True)
		self.assertEqual(spokenMessages[-1], "No pinned document")


class TestTheLayer(unittest.TestCase):
	def layer(self):
		from brlMultiline import keyLayers

		layers = keyLayers.defaultLayers(keyLayers.MONARCH, "globalPlugins.brlMultiline")
		return keyLayers, next(layer for layer in layers if layer.id == "pinnedDocument")

	def test_theMonarchHasOneForThePinnedContext(self):
		keyLayers, layer = self.layer()
		self.assertEqual(layer.context, "pinned")
		self.assertEqual(layer.style, keyLayers.STAYS_ON)
		self.assertFalse(layer.autoEnable, "its letters stop typing, so it never comes on by itself")

	def test_hIsTheNextHeadingAndWithDot7ThePrevious(self):
		keyLayers, layer = self.layer()
		nextHeading = layer.bindings[keyLayers.normalize(f"br({keyLayers.MONARCH}):dot1+dot2+dot5")]
		previousHeading = layer.bindings[keyLayers.normalize(f"br({keyLayers.MONARCH}):dot1+dot2+dot5+dot7")]
		self.assertEqual(nextHeading.scriptName, "pinNextHeading")
		self.assertEqual(previousHeading.scriptName, "pinPreviousHeading")

	def test_everyBindingIsACommandThePluginHas(self):
		from . import test_plugin as harness

		_keyLayers, layer = self.layer()
		for target in layer.bindings.values():
			self.assertTrue(hasattr(harness.GlobalPlugin, f"script_{target.scriptName}"), target.scriptName)

	def test_anOlderSavedSetIsGivenIt(self):
		from brlMultiline import keyLayers

		self.assertIn("pinnedDocument", keyLayers.SHIPPED_ADDITIONS[keyLayers.SHIPPED]["layers"])


class TestNVDAsWords(unittest.TestCase):
	def test_messagesAreNVDAsTranslation(self):
		with mock.patch.object(builtins, "_", lambda text: f"<{text}>", create=True):
			self.assertEqual(pinQuickNav.nvdaMessage("no next heading"), "<no next heading>")

	def test_everyTypeHasACommandName(self):
		self.assertEqual(len({quickNavType.name for quickNavType in TYPES}), len(TYPES))


if __name__ == "__main__":
	unittest.main()
