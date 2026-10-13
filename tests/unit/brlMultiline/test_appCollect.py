# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Gathering an application window's layout live, and showing it, against a fake UI Automation."""

import json
import os
import sys
import types
import unittest
from unittest import mock

from . import _stubs
from ._stubs import installStubs

installStubs()

from brlMultiline import appCollect  # noqa: E402
from brlMultiline.appCollect import AppCollector  # noqa: E402
from brlMultiline.appRegions import findAppRegions  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "appLayout")

#: Property and control type ids for the fake, in the names `UIAHandler` gives them.
IDS = {
	"UIA_ControlTypePropertyId": 1,
	"UIA_NamePropertyId": 2,
	"UIA_AutomationIdPropertyId": 3,
	"UIA_ClassNamePropertyId": 4,
	"UIA_FrameworkIdPropertyId": 5,
	"UIA_BoundingRectanglePropertyId": 6,
	"UIA_IsOffscreenPropertyId": 7,
	"UIA_HasKeyboardFocusPropertyId": 8,
	"UIA_LandmarkTypePropertyId": 9,
	"UIA_RuntimeIdPropertyId": 10,
	"UIA_NativeWindowHandlePropertyId": 11,
	"UIA_WindowControlTypeId": 50032,
	"UIA_PaneControlTypeId": 50033,
	"UIA_ButtonControlTypeId": 50000,
	"UIA_TreeControlTypeId": 50023,
	"UIA_TreeItemControlTypeId": 50024,
	"UIA_ListControlTypeId": 50008,
	"UIA_ListItemControlTypeId": 50007,
	"UIA_StatusBarControlTypeId": 50017,
	"UIA_TextControlTypeId": 50020,
	"UIA_NavigationLandmarkTypeId": 80003,
	"TreeScope_Element": 1,
	"TreeScope_Children": 2,
}
TYPES = {name[4:-13]: value for name, value in IDS.items() if name.endswith("ControlTypeId")}


class FakeArray:
	def __init__(self, items):
		self.items = items

	@property
	def Length(self):  # noqa: N802 - UI Automation's spelling.
		return len(self.items)

	def GetElement(self, index):  # noqa: N802
		return self.items[index]


class FakeElement:
	"""An element whose properties are all cached, and which counts the calls asking for children."""

	asked: list = []

	def __init__(self, kind, rect=None, name="", children=(), runtimeId=None, landmark=None):
		self.props = {1: TYPES[kind], 2: name, 6: tuple(float(v) for v in rect) if rect else None}
		if landmark:
			self.props[9] = IDS[f"UIA_{landmark}LandmarkTypeId"]
		self.props[10] = tuple(runtimeId) if runtimeId else None
		self.kids = list(children)
		self.parent = None
		for child in self.kids:
			child.parent = self

	def GetCachedPropertyValue(self, propertyId):  # noqa: N802
		return self.props.get(propertyId)

	def BuildUpdatedCache(self, request):  # noqa: N802
		FakeElement.asked.append(self.props[2] or self.props[1])
		return self

	def GetCachedChildren(self):  # noqa: N802
		return FakeArray(self.kids) if self.kids else None

	def GetRuntimeId(self):  # noqa: N802
		return self.props[10]


class FakeWalker:
	def GetParentElement(self, element):  # noqa: N802
		return element.parent


class FakeRequest:
	def addProperty(self, propertyId):  # noqa: N802
		pass


class FakeClient:
	ControlViewCondition = None
	ControlViewWalker = FakeWalker()

	def __init__(self, root):
		self.root = root

	def CreateCacheRequest(self):  # noqa: N802
		return FakeRequest()

	def ElementFromHandleBuildCache(self, handle, request):  # noqa: N802
		return self.root


def fakeUIA(root):
	module = types.ModuleType("UIAHandler")
	module.__dict__.update(IDS)
	module.handler = types.SimpleNamespace(clientObject=FakeClient(root))
	return module


def explorerLike():
	"""A window with a navigation tree, a list holding the focus, and a status bar."""
	focused = FakeElement("ListItem", (300, 200, 400, 20), "a file", runtimeId=(9, 9))
	items = FakeElement(
		"List",
		(260, 100, 740, 560),
		"Items View",
		[focused] + [FakeElement("ListItem", (300, 230 + i * 20, 400, 20)) for i in range(5)],
		runtimeId=(9, 2),
	)
	tree = FakeElement(
		"Tree",
		(0, -2000, 250, 9000),
		"Navigation Pane",
		[FakeElement("TreeItem", (0, 0, 250, 20)) for _ in range(300)],
	)
	status = FakeElement(
		"StatusBar", (0, 660, 1000, 30), "Status bar", [FakeElement("Text", (0, 660, 100, 30), "6 items")]
	)
	view = FakeElement("Pane", (0, 100, 1000, 590), "", [tree, items, status], runtimeId=(9, 1))
	crumb = FakeElement(
		"Pane",
		(300, 20, 400, 40),
		"",
		[FakeElement("Button", (300, 20, 40, 40), "Back")],
		landmark="Navigation",
	)
	root = FakeElement("Window", (0, 0, 1000, 700), "Folder - File Explorer", [crumb, view], runtimeId=(9, 0))
	return root, focused


def run(collector) -> int:
	collector.start()
	rounds = 0
	while _stubs.callAfterQueue.pending:
		_stubs.callAfterQueue.flush()
		rounds += 1
	return rounds


class TestGathering(unittest.TestCase):
	def setUp(self):
		_stubs.callAfterQueue.discard()
		FakeElement.asked = []
		self.root, focused = explorerLike()
		self.focus = types.SimpleNamespace(UIAElement=focused, windowHandle=5)
		self.window = types.SimpleNamespace(windowHandle=5, location=(0, 0, 1000, 700))
		patcher = mock.patch.dict(sys.modules, {"UIAHandler": fakeUIA(self.root)})
		patcher.start()
		self.addCleanup(patcher.stop)
		self.results = []

	def gather(self):
		collector = AppCollector(
			self.window, self.focus, lambda payload, why: self.results.append((payload, why))
		)
		rounds = run(collector)
		self.assertEqual(len(self.results), 1)
		return self.results[0][0], rounds

	def test_theShapeTheRulesRead(self):
		payload, _rounds = self.gather()
		self.assertEqual(payload["kind"], "uia")
		self.assertEqual(payload["windowRect"], [0, 0, 1000, 700])
		self.assertEqual(payload["window"]["type"], "Window")
		self.assertEqual(payload["incomplete"], [])

	def test_rectanglesAreRelativeToTheWindowAndClipped(self):
		payload, _rounds = self.gather()
		view = payload["window"]["children"][1]
		self.assertEqual(view["rect"], [0, 100, 1000, 590])
		tree = view["children"][0]
		self.assertEqual(tree["rect"][1], 100, "cut to the view holding it")
		self.assertIn("fullRect", tree)

	def test_itemsAreCountedNotWalked(self):
		payload, _rounds = self.gather()
		tree = payload["window"]["children"][1]["children"][0]
		self.assertEqual(tree["items"], {"TreeItem": 300})
		self.assertNotIn("children", tree)

	def test_leavesAreNotAskedForChildren(self):
		self.gather()
		self.assertNotIn("Back", FakeElement.asked)
		self.assertNotIn("6 items", FakeElement.asked)

	def test_theFocusPath(self):
		payload, _rounds = self.gather()
		self.assertEqual(payload["focus"]["path"], [[9, 9], [9, 2], [9, 1], [9, 0]])

	def test_inBatchesWithNVDAFreeBetween(self):
		_payload, rounds = self.gather()
		self.assertGreater(rounds, 2)

	def test_whatIsGatheredIsWhatTheRulesFindAreasIn(self):
		payload, _rounds = self.gather()
		page = findAppRegions(payload["window"], payload["windowRect"], payload["focus"]["path"])
		self.assertEqual([r.kind for r in page.children], ["navigation", "tree", "list", "statusbar"])
		self.assertTrue([r for r in page.walk() if r.kind == "list"][0].hasFocus)

	def test_aTimeLimitStopsItAndSaysSo(self):
		with mock.patch.object(appCollect, "TIME_LIMIT", -1):
			payload, _rounds = self.gather()
		self.assertIn("time", payload["incomplete"])
		self.assertEqual(payload["window"]["type"], "Window")

	def test_cancelledItNeverAnswers(self):
		collector = AppCollector(self.window, self.focus, lambda payload, why: self.results.append(payload))
		collector.start()
		collector.cancel()
		while _stubs.callAfterQueue.pending:
			_stubs.callAfterQueue.flush()
		self.assertEqual(self.results, [])

	def test_noWindowSaysSo(self):
		self.window = types.SimpleNamespace(windowHandle=0, location=None)
		run(AppCollector(self.window, self.focus, lambda payload, why: self.results.append((payload, why))))
		self.assertEqual(self.results, [(None, "no window")])


class TestShowingAnApplication(unittest.TestCase):
	"""The plugin draws a window's layout as it draws a page's, and lists it as text."""

	def setUp(self):
		from . import test_plugin
		from .test_layoutDraw import FakeDrawingMode

		test_plugin.resetPluginState()
		import braille

		braille.handler = test_plugin.FakeHandler(test_plugin.MONARCH_ROWS, test_plugin.MONARCH_COLS)
		self.plugin = test_plugin.GlobalPlugin()
		self.addCleanup(self.plugin.terminate)
		self.mode = FakeDrawingMode()
		self.plugin.graphicsMode = self.mode
		_stubs.flashedMessages.clear()
		with open(os.path.join(FIXTURES, "explorer.json"), encoding="utf-8") as f:
			self.payload = {"kind": "uia", **json.load(f)}

	def test_drawnAsTheWindow(self):
		self.plugin._showLayout(self.payload, "screen")
		self.assertEqual(self.mode.source.name, "layout of the window")
		self.assertIn("layout of the window", _stubs.flashedMessages[-1])
		self.assertTrue(self.plugin._layoutView.app)

	def test_aWindowHasNoPageSoThePageViewIsTheScreen(self):
		self.plugin._showLayout(self.payload, "page")
		self.assertEqual(self.plugin._layoutView.view, "screen")

	def test_everyPressDescribesSinceThereIsNothingToMoveTo(self):
		self.plugin._showLayout(self.payload, "screen")
		self.assertIsNone(self.mode.source.activateAt)
		said = self.mode.source.describeAt(2, 2)
		self.assertTrue(said and said.startswith("tabs"), said)

	def test_drillingWorksAsOnAPage(self):
		self.plugin._showLayout(self.payload, "screen")
		drawing = self.mode.source
		for y in range(drawing.buffer.height):
			for x in range(drawing.buffer.width):
				drawing.describeAt(x, y)
				pressed = self.plugin._layoutView.pressed
				if pressed is not None and pressed.kind == "area":
					break
			else:
				continue
			break
		self.plugin.script_layoutDrillIn(None)
		self.assertEqual(self.plugin._layoutView.focus.kind, "area")
		self.assertIn("layout of area", self.mode.source.name)

	def test_asText(self):
		shown = []
		ui = sys.modules["ui"]
		with mock.patch.object(
			ui, "browseableMessage", lambda text, title: shown.append((text, title)), create=True
		):
			self.plugin._showLayout(self.payload, "text")
		text, title = shown[0]
		self.assertEqual(title, "Window layout")
		self.assertIn("status bar", text)

	def test_outsideBrowseModeTheWindowIsRead(self):
		started = []
		api = sys.modules["api"]
		window = types.SimpleNamespace(windowHandle=5, location=(0, 0, 800, 600))
		with (
			mock.patch.object(api, "getForegroundObject", lambda: window, create=True),
			mock.patch.object(appCollect.AppCollector, "start", lambda collector: started.append(collector)),
		):
			self.plugin.script_layoutOverview(None)
		self.assertEqual(len(started), 1)
		self.assertIs(started[0].window, window)
		self.assertIn("Reading the window layout", _stubs.flashedMessages)
