# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The areas of an application's window, found from UI Automation captures.

The fixtures are captures from `spikes/appLayoutProbe.py`, with personal names replaced by
`spikes/appProbeScrub.py`: File Explorer with three tabs open, Excel, Outlook, askSam, Settings
and Notepad.
"""

import json
import os
import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline.appRegions import findAppRegions  # noqa: E402
from brlMultiline.layoutDraw import composeFitting  # noqa: E402
from brlMultiline.layoutRegions import Box  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "appLayout")


def load(name: str):
	with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
		data = json.load(f)
	return findAppRegions(data["window"], data.get("windowRect"), (data.get("focus") or {}).get("path"))


def kinds(regions) -> list:
	return [r.kind for r in regions]


def find(page, kind: str, label: str = ""):
	found = [r for r in page.walk() if r.kind == kind and (not label or r.label == label)]
	return found[0] if found else None


def element(kind: str, rect, name: str = "", children=(), **more) -> dict:
	record = {"type": kind, "rect": list(rect), "children": list(children), **more}
	if name:
		record["name"] = name
	return record


def window(*children, size=(1000, 700)) -> dict:
	return element("Window", (0, 0, *size), "Some App", children)


class TestExplorer(unittest.TestCase):
	def setUp(self):
		self.page = load("explorer.json")

	def test_theTopBarsThenTheFolderView(self):
		self.assertEqual(
			kinds(self.page.children),
			["tabs", "toolbar", "search", "navigation", "toolbar", "toolbar", "area"],
		)

	def test_onlyTheActiveTabIsDrawn(self):
		"""The inactive tabs' panes are covered by the active one's, and left out."""
		views = [r for r in self.page.children if r.kind == "area"]
		self.assertEqual(len(views), 1)
		self.assertEqual(kinds(views[0].children), ["tree", "list", "statusbar"])

	def test_theTabStripIsOneAreaWithItsTabs(self):
		strip = self.page.children[0]
		self.assertEqual(strip.tabs, 3)
		self.assertEqual(strip.children, [], "its own list of tabs is not a second area")

	def test_theFocusIsPlaced(self):
		held = [r.title for r in self.page.walk() if r.hasFocus and r is not self.page]
		self.assertEqual(held, ["area, Name 2", "list, Items View"])
		self.assertTrue(self.page.hasFocus)

	def test_namesThatAreNotForReadersAreNotUsed(self):
		labels = {r.label for r in self.page.walk()}
		self.assertFalse(labels & {"Control Host", "Shell Folder View", "Property Field"})


class TestExcel(unittest.TestCase):
	def setUp(self):
		self.page = load("excel.json")

	def test_theRibbonWithItsNamedGroups(self):
		ribbon = self.page.children[0]
		self.assertEqual(ribbon.kind, "ribbon")
		self.assertEqual(kinds(ribbon.children), ["toolbar", "tabs", "area"])
		home = ribbon.children[2]
		self.assertEqual([g.label for g in home.children][:4], ["Clipboard", "Font", "Alignment", "Number"])

	def test_theWrappersAroundTheRibbonAndTheStatusBarCollapse(self):
		"""Office stacks a dock, a command bar and three panes on one rectangle."""
		self.assertEqual(self.page.children[0].kind, "ribbon")
		self.assertEqual(self.page.children[-1].kind, "statusbar")

	def test_theSheetTabsAndGrids(self):
		ribbon = self.page.children[0]
		sheets = [r for r in self.page.walk() if r.kind == "tabs" and r not in ribbon.walk()]
		self.assertEqual([r.tabs for r in sheets], [5])
		self.assertEqual(len([r for r in self.page.walk() if r.kind == "grid"]), 2)

	def test_scrollBarsAreNotAreas(self):
		labels = {r.label for r in self.page.walk()}
		self.assertFalse(labels & {"Vertical", "Horizontal"})


class TestOutlook(unittest.TestCase):
	def setUp(self):
		self.page = load("outlook.json")

	def test_itsAreas(self):
		self.assertEqual(
			[r.title for r in self.page.children],
			[
				"ribbon",
				"list",
				"area, Form Regions",
				"tree, Favorites",
				"grid, Table View",
				"tree, Mail Folders",
				"status bar",
			],
		)

	def test_theReadingPaneHoldsItsToolbarAndTheMessage(self):
		pane = find(self.page, "area", "Form Regions")
		self.assertEqual(kinds(pane.children), ["toolbar", "document"])

	def test_programmersNamesAreNotUsed(self):
		labels = {r.label for r in self.page.walk()}
		self.assertFalse(labels & {"NUIDocumentWindow", "Document12", "FolderBar", "DropShadowTop"})

	def test_aGroupNestedInItselfIsOne(self):
		self.assertEqual(len([r for r in self.page.walk() if r.label == "Quick Steps"]), 1)

	def test_anEmptyUnnamedStripIsLeftOut(self):
		lists = [r for r in self.page.children if r.kind == "list"]
		self.assertEqual([r.items for r in lists], [5])


class TestAskSam(unittest.TestCase):
	"""An old MFC application, through the MSAA bridge."""

	def setUp(self):
		self.page = load("asksam.json")

	def test_namedToolbarPanesAndTheFolders(self):
		self.assertEqual(
			[r.title for r in self.page.children][:4],
			["area, Standard", "area, Formatting", "area, Search Line", "area, Folders"],
		)
		folders = find(self.page, "area", "Folders")
		self.assertEqual([r.items for r in folders.children], [440, 5])

	def test_tinyPanesAreNotAreas(self):
		labels = {r.label for r in self.page.walk()}
		self.assertNotIn("View First", labels)

	def test_aStatusBarAtTheBottom(self):
		self.assertEqual(self.page.children[-1].kind, "statusbar")


class TestSettings(unittest.TestCase):
	def setUp(self):
		self.page = load("settings.json")

	def test_landmarksAndTheSearchBox(self):
		self.assertEqual(kinds(self.page.children)[:3], ["search", "navigation", "main"])

	def test_anUnnamedContainerOfItemsIsAList(self):
		"""Its navigation pane has no name or landmark; what it holds is a list."""
		lists = [r for r in self.page.children if r.kind == "list"]
		self.assertEqual([r.items for r in lists], [12])

	def test_itemsAreCountedNotMadeAreas(self):
		main = find(self.page, "main")
		bluetooth = find(main, "area", "Bluetooth devices")
		self.assertEqual(kinds(bluetooth.children), ["list", "area"])
		self.assertEqual(bluetooth.children[0].children, [])

	def test_namedCardsInMain(self):
		main = find(self.page, "main")
		self.assertIn("Recommended settings", [r.label for r in main.children])


class TestNotepad(unittest.TestCase):
	def test_tabsMenuDocumentAndAStatusStrip(self):
		page = load("notepad.json")
		self.assertEqual(kinds(page.children), ["tabs", "menubar", "document", "statusbar"])


class TestTheRules(unittest.TestCase):
	def test_whatCannotBeSeenGoes(self):
		page = findAppRegions(
			window(
				element("List", (0, 0, 300, 300), "Shown", items={"ListItem": 3}),
				element("List", (300, 0, 300, 300), "Covered", covered=True, items={"ListItem": 3}),
				element("List", (600, 0, 300, 300), "Offscreen", offscreen=True, items={"ListItem": 3}),
				element("List", (0, 0, 0, 0), "Empty rect", items={"ListItem": 3}),
			)
		)
		self.assertEqual([r.label for r in page.children], ["Shown"])

	def test_aNameMakesAPaneAnArea(self):
		page = findAppRegions(window(element("Pane", (0, 0, 300, 300), "Folders")))
		self.assertEqual([r.title for r in page.children], ["area, Folders"])

	def test_identifiersAreNotNames(self):
		for name in ("NUIDocumentWindow", "ASEditorWnd", "DropShadowTop", "Document12"):
			with self.subTest(name=name):
				page = findAppRegions(window(element("Pane", (0, 0, 300, 300), name)))
				self.assertEqual(page.children, [])

	def test_aWrapperIsGoneThrough(self):
		inner = element("ToolBar", (0, 0, 300, 50), "Standard", [element("Button", (0, 0, 40, 40))])
		page = findAppRegions(window(element("Pane", (0, 0, 1000, 60), children=[inner])))
		self.assertEqual([r.title for r in page.children], ["toolbar, Standard"])
		self.assertEqual(page.children[0].buttons, 1)

	def test_wrappersCollapseIntoTheMostTellingKind(self):
		status = element("StatusBar", (0, 600, 1000, 30), "Status Bar", [element("Text", (0, 600, 200, 30))])
		bar = element(
			"ToolBar",
			(0, 600, 1000, 30),
			children=[element("Pane", (0, 600, 1000, 30), "Status Bar", [status])],
		)
		page = findAppRegions(window(bar))
		self.assertEqual([r.kind for r in page.children], ["statusbar"])

	def test_landmarks(self):
		page = findAppRegions(
			window(
				element("Group", (0, 0, 300, 600), landmark="Navigation"),
				element("Group", (300, 0, 700, 600), landmark="Main"),
			)
		)
		self.assertEqual(kinds(page.children), ["navigation", "main"])

	def test_aSearchField(self):
		page = findAppRegions(window(element("Edit", (300, 10, 400, 30), "Search box, Find a setting")))
		self.assertEqual(kinds(page.children), ["search"])

	def test_countsInTheWholeOfAnArea(self):
		inner = element(
			"Group",
			(0, 0, 300, 300),
			children=[element("Edit", (0, 0, 100, 20)), element("Hyperlink", (0, 30, 100, 20))],
		)
		pane = element("Pane", (0, 0, 400, 400), "Options", [inner, element("Button", (0, 350, 60, 30))])
		page = findAppRegions(window(pane))
		self.assertEqual(page.children[0].counts, [("fields", 1), ("buttons", 1), ("links", 1)])

	def test_theWindowIsThePage(self):
		page = findAppRegions(window(size=(800, 600)), [100, 100, 800, 600])
		self.assertEqual(page.kind, "page")
		self.assertEqual(page.box, Box(0, 0, 800, 600))


class TestDrawingAnApplication(unittest.TestCase):
	"""The same drawing as a web page's, on the screen view of the window."""

	def test_everyCaptureDraws(self):
		from .test_layoutDraw import PinBuffer, translate

		for name in sorted(os.listdir(FIXTURES)):
			with self.subTest(app=name):
				page = load(name)
				overview = composeFitting(PinBuffer, page, 96, 40, "screen", page.box, translate, 3)
				self.assertIsNotNone(overview)
				self.assertTrue(overview.boxes)
				for box in overview.walk():
					self.assertIsNotNone(box.rect.intersection(Box(0, 0, 96, overview.buffer.height)))

	def test_aPressSaysTheArea(self):
		from .test_layoutDraw import PinBuffer, translate

		page = load("explorer.json")
		overview = composeFitting(PinBuffer, page, 96, 40, "screen", page.box, translate, 3)
		strip = overview.boxes[0]
		said = overview.describeAt(strip.rect.left + 1, strip.rect.top + 1)
		self.assertTrue(said.startswith("tabs, across the top"), said)
		self.assertIn("3 tabs", said)
