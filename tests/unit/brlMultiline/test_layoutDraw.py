# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for drawing a page's areas as boxes on the pins, and for gathering a page live.

The drawing tests run the saved pages from `tests/fixtures/layout` through the region rules
and onto a 96 by 40 pin buffer, the Monarch's panel, and check what a hand would check: the
boxes are in the right order, none is too small to find or to label, a press names the area
under it, and an area whose insides will not fit is drawn without them rather than with
slivers. The collector tests drive `layoutCollect.Collector` over a fake browse mode document,
to check that it works in batches, finds what the rules need, and stops when cancelled.
"""

import json
import os
import sys
import unittest

from . import _stubs
from ._stubs import installStubs

installStubs()

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

from brlMultiline.layoutCollect import Collector  # noqa: E402
from brlMultiline.layoutDraw import MIN_BOX, _Ruler, compose  # noqa: E402
from brlMultiline.layoutRegions import Box, Region, findRegions, fromProbe  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "layout")
WIDTH, HEIGHT = 96, 40
FULL_CELL = 0xFF


def translate(word: str) -> list[int]:
	"""One solid cell per letter: what matters here is how many cells, not which."""
	return [FULL_CELL] * len(word)


def draw(name: str, view: str = "screen", depth: int = 2):
	with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
		data = json.load(f)
	root, viewport = fromProbe(data)
	page = findRegions(root, viewport)
	return compose(PinBuffer, page, WIDTH, HEIGHT, view, viewport, translate, depth)


class TestRuler(unittest.TestCase):
	def test_proportionalWhenThereIsRoom(self):
		ruler = _Ruler([(0, 500, 1), (500, 1000, 1)], 0, 1000, 40)
		self.assertEqual(ruler.span(0, 500), (0, 20))
		self.assertEqual(ruler.span(500, 1000), (20, 40))

	def test_aThinBandIsGrownAndOrderIsKept(self):
		"""A banner a tenth of a screen tall still gets the pins a label needs."""
		ruler = _Ruler([(0, 90, 8), (90, 900, 8), (900, 1000, 8)], 0, 1000, 40)
		top, middle, bottom = ruler.span(0, 90), ruler.span(90, 900), ruler.span(900, 1000)
		self.assertGreaterEqual(top[1] - top[0], 8)
		self.assertGreaterEqual(bottom[1] - bottom[0], 8)
		self.assertEqual(top[1], middle[0])
		self.assertEqual(middle[1], bottom[0])


class TestRealPages(unittest.TestCase):
	def assertNoBoxTooSmall(self, overview):
		for box in overview.walk():
			self.assertGreaterEqual(box.rect.width, MIN_BOX[0], box.region.title)
			self.assertGreaterEqual(box.rect.height, MIN_BOX[1], box.region.title)

	def test_hackerNewsTopToBottom(self):
		overview = draw("hackernews.json", "page")
		kinds = [b.region.kind for b in overview.boxes]
		self.assertEqual(kinds, ["header", "main", "footer"])
		tops = [b.rect.top for b in overview.boxes]
		self.assertEqual(tops, sorted(tops))
		self.assertNoBoxTooSmall(overview)

	def test_seekingAlphaSidebarAtTheLeft(self):
		overview = draw("seekingalpha.json")
		aside = [b for b in overview.boxes if b.region.kind == "complementary"][0]
		banner = [b for b in overview.boxes if b.region.kind == "banner"][0]
		self.assertEqual(aside.rect.left, 0)
		self.assertGreaterEqual(aside.rect.height, HEIGHT - 1, "the full height of the panel")
		self.assertGreaterEqual(banner.rect.left, aside.rect.right - 1)
		self.assertTrue([c for c in banner.children if c.region.kind == "search"])
		self.assertNoBoxTooSmall(overview)

	def test_pressNamesTheAreaUnderIt(self):
		overview = draw("seekingalpha.json")
		aside = [b for b in overview.boxes if b.region.kind == "complementary"][0]
		said = overview.describeAt(aside.rect.left + 1, aside.rect.top + 1)
		self.assertTrue(said.startswith("complementary, down the left side"), said)
		self.assertIsNone(overview.describeAt(WIDTH + 5, 0))

	def test_crampedInsidesAreLeftForADrillIn(self):
		"""Fabula's whole page: main's five bands cannot each get a readable box."""
		overview = draw("fabula-chrome-scrolled.json", "page")
		main = [b for b in overview.boxes if b.region.kind == "main"][0]
		self.assertEqual(main.children, [])
		self.assertEqual([b.region.kind for b in overview.boxes], ["region", "banner", "main", "contentinfo"])
		self.assertNoBoxTooSmall(overview)

	def test_everyPageDrawsBoxesBigEnoughToFind(self):
		for name in sorted(os.listdir(FIXTURES)):
			for view in ("screen", "page"):
				with self.subTest(page=name, view=view):
					overview = draw(name, view)
					self.assertTrue(overview.boxes)
					self.assertNoBoxTooSmall(overview)

	def test_labelIsDrawnInsideItsBox(self):
		overview = draw("hackernews.json", "page")
		header = overview.boxes[0]
		self.assertTrue(header.label)
		# "head" is four cells; the first cell's top left dot sits two pins in from the corner.
		self.assertTrue(overview.buffer.getDot(header.rect.left + 2, header.rect.top + 2))


class TestScreenView(unittest.TestCase):
	def test_stickyHeaderIsDrawnAtTheTopOfTheScreen(self):
		"""Fabula scrolled a screen down: on the page the banner is near the top, off screen; on
		the screen it is pinned at the top, over the main content."""
		overview = draw("fabula-chrome-scrolled.json", "screen")
		kinds = [b.region.kind for b in overview.boxes]
		self.assertEqual(kinds[0], "banner", kinds)
		banner, main = overview.boxes[0], [b for b in overview.boxes if b.region.kind == "main"][0]
		self.assertEqual(banner.rect.top, 0)
		self.assertGreaterEqual(main.rect.top, banner.rect.bottom - 1, "main is drawn below it, not under it")
		self.assertTrue([c for c in banner.children if c.region.kind == "navigation"])


def loadPage(name: str):
	with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
		root, viewport = fromProbe(json.load(f))
	return findRegions(root, viewport), viewport


class TestTallPageAndItsWindows(unittest.TestCase):
	"""The whole page drawn a screen to a panel, and the views of it zooming and moving compose."""

	def test_aTallPageKeepsTheAreasInsideMain(self):
		"""Squeezed onto one panel Fabula's main lost its five bands; a screen to a panel keeps them."""
		page, viewport = loadPage("fabula-chrome-scrolled.json")
		screens = page.box.height / viewport.height
		overview = compose(PinBuffer, page, WIDTH, round(HEIGHT * screens), "page", viewport, translate)
		main = [b for b in overview.boxes if b.region.kind == "main"][0]
		self.assertGreaterEqual(len(main.children), 4)

	def test_aWindowShowsWhatWasUnderIt(self):
		page, viewport = loadPage("fabula-chrome-scrolled.json")
		tall = compose(PinBuffer, page, WIDTH, HEIGHT * 9, "page", viewport, translate)
		banner = [b for b in tall.boxes if b.region.kind == "banner"][0]
		top = banner.rect.top / tall.buffer.height
		down = banner.rect.height / tall.buffer.height
		window = tall.windowAt(0.0, 1.0, top, down)
		self.assertLessEqual(abs(window.top - banner.region.box.top), 20)
		self.assertLessEqual(abs(window.bottom - banner.region.box.bottom), 20)

	def test_magnifiedLabelsAreBrailleAtItsOwnSize(self):
		"""Zoomed in, a label is composed again, three pins to a cell, not blown up."""
		page, viewport = loadPage("seekingalpha.json")
		part = compose(
			PinBuffer, page, WIDTH, HEIGHT, "screen", viewport, translate, window=Box(0, 0, 960, 455)
		)
		labelled = [b for b in part.walk() if b.label]
		self.assertTrue(labelled)
		box = labelled[0]
		row = box.rect.top + 2
		dots = [x for x in range(box.rect.left + 1, box.rect.right - 1) if part.buffer.getDot(x, row)]
		self.assertTrue(dots)
		# Solid test cells raise both columns of each cell, then a gap: pairs three pins apart.
		self.assertEqual(dots[1] - dots[0], 1)

	def test_aKeyLetterIsTheSameInEveryView(self):
		page = Region("page", "", Box(0, 0, 1000, 2000), "page")
		narrow = Region("region", "Announcements", Box(0, 0, 60, 2000), "landmark")
		wide = Region("main", "", Box(60, 0, 940, 2000), "landmark")
		page.children = [narrow, wide]
		keys: dict = {}
		whole = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1, keys=keys)
		part = compose(
			PinBuffer,
			page,
			WIDTH,
			HEIGHT,
			"page",
			page.box,
			translate,
			1,
			window=Box(0, 1000, 1000, 1000),
			keys=keys,
		)
		self.assertEqual([b.key for b in whole.keyed], ["a"])
		self.assertEqual([b.key for b in part.keyed], ["a"])
		self.assertIs(part.keyed[0].region, narrow)

	def test_aPressInAWindowSaysWhereOnTheWholePage(self):
		page, viewport = loadPage("seekingalpha.json")
		part = compose(
			PinBuffer, page, WIDTH, HEIGHT, "screen", viewport, translate, window=Box(0, 0, 960, 455)
		)
		aside = [b for b in part.boxes if b.region.kind == "complementary"][0]
		said = part.describeAt(aside.rect.left + 1, aside.rect.top + 1)
		self.assertIn("down the left side", said)


class TestKeyLetters(unittest.TestCase):
	def test_aWordTooLongForItsBoxGetsALetter(self):
		"""A narrow box is widened for a short word, but never past four cells' worth."""
		page = Region("page", "", Box(0, 0, 1000, 1000), "page")
		narrow = Region("region", "Announcements", Box(0, 0, 60, 1000), "landmark")
		wide = Region("main", "", Box(60, 0, 940, 1000), "landmark")
		page.children = [narrow, wide]
		overview = compose(PinBuffer, page, WIDTH, HEIGHT, "page", page.box, translate, 1)
		boxes = {b.region.kind: b for b in overview.boxes}
		self.assertEqual(boxes["main"].label, "word")
		self.assertEqual(boxes["region"].key, "a")
		self.assertEqual(overview.keyText(), "a, region, Announcements")


class FakeInfo:
	def __init__(self, offsets):
		self.offsets = offsets
		self.unit = None

	def copy(self):
		return FakeInfo(self.offsets)

	def expand(self, unit):
		self.unit = unit


class FakeBrowseDocument:
	"""A browse mode document as far as moving to an area uses one."""

	isAlive = True
	passThrough = False

	def __init__(self):
		self.selections = []

	def makeTextInfo(self, offsets):
		return FakeInfo(offsets)

	def _set_selection(self, info, reason=None):
		self.selections.append((info.offsets, reason))


class FakeLeavingMode:
	def __init__(self, focusHere: bool = True):
		self.left = 0
		self.focusHere = focusHere

	def leave(self):
		self.left += 1

	def holdsFocus(self):
		return self.focusHere


class TestMovingToAnArea(unittest.TestCase):
	"""Pressing a box twice puts the browse mode cursor where the area starts."""

	def setUp(self):
		from . import test_plugin

		test_plugin.resetPluginState()
		import braille

		braille.handler = test_plugin.FakeHandler(test_plugin.MONARCH_ROWS, test_plugin.MONARCH_COLS)
		self.plugin = test_plugin.GlobalPlugin()
		self.addCleanup(self.plugin.terminate)
		self.plugin.graphicsMode = FakeLeavingMode()
		self.document = FakeBrowseDocument()
		api = sys.modules["api"]
		focus = type("Focus", (), {"treeInterceptor": self.document})()
		original = api.getFocusObject
		api.getFocusObject = lambda: focus
		self.addCleanup(setattr, api, "getFocusObject", original)
		_stubs.spokenMessages.clear()
		_stubs.spokenPositions.clear()
		_stubs.flashedMessages.clear()

	def area(self, start):
		return Region("navigation", "Primary", Box(0, 0, 100, 100), "landmark", start=start)

	def test_theCursorGoesToTheStartAndTheDrawingComesDown(self):
		self.plugin._moveToArea(self.document, self.area(120))
		self.assertEqual(self.document.selections, [((120, 120), "quicknav")])
		self.assertEqual(self.plugin.graphicsMode.left, 1)
		self.assertEqual(_stubs.spokenMessages[-1], "navigation, Primary")
		info, _format, reason = _stubs.spokenPositions[-1]
		self.assertEqual((info.unit, reason), ("line", "quicknav"))

	def test_withTheFocusOnAnotherDisplayTheDrawingStaysUp(self):
		"""A Monarch drawing beside a Focus 80 showing the focus: the line arrives there."""
		self.plugin.graphicsMode = FakeLeavingMode(focusHere=False)
		self.plugin._moveToArea(self.document, self.area(120))
		self.assertEqual(self.document.selections, [((120, 120), "quicknav")])
		self.assertEqual(self.plugin.graphicsMode.left, 0)

	def test_anAreaWithNoPlaceInTheTextSaysSo(self):
		self.plugin._moveToArea(self.document, self.area(None))
		self.assertEqual(self.document.selections, [])
		self.assertEqual(self.plugin.graphicsMode.left, 0)
		self.assertIn("No place in the page", _stubs.flashedMessages[-1])

	def test_theDrawingsSecondPressReachesTheAreaUnderIt(self):
		"""Seeking Alpha on screen: the bottom left pin is the sidebar, below its navigation."""
		self.plugin.graphicsMode.newBuffer = PinBuffer
		page, viewport = loadPage("seekingalpha.json")
		drawing = self.plugin._layoutDrawing(
			page, "screen", viewport, (WIDTH, HEIGHT), "layout", self.document
		)
		self.assertIn("complementary", drawing.describeAt(0, HEIGHT - 1))
		drawing.activateAt(0, HEIGHT - 1)
		aside = [r for r in page.children if r.kind == "complementary"][0]
		self.assertIsNotNone(aside.start)
		self.assertEqual(self.document.selections, [((aside.start, aside.start), "quicknav")])

	def test_aPageThatHasChangedIsNotMovedIn(self):
		self.plugin._moveToArea(FakeBrowseDocument(), self.area(120))
		self.assertEqual(self.document.selections, [])
		self.assertIn("page has changed", _stubs.flashedMessages[-1])


class FakeObject:
	def __init__(self, role="SECTION", location=(0, 0, 0, 0), name="", attributes=None, children=()):
		self.role = type("Role", (), {"name": role})()
		self.location = location
		self.name = name
		self.IA2Attributes = attributes or {}
		self.children = list(children)
		self.childCount = len(self.children)


class FakeItem:
	located = 0

	def __init__(self, ident, start, end, obj, label=""):
		self.vbufFieldIdentifier = ident
		self.textInfo = type("Info", (), {"_startOffset": start, "_endOffset": end})()
		self._obj = obj
		self.label = label

	@property
	def obj(self):
		FakeItem.located += 1
		return self._obj


class FakeDocument:
	documentConstantIdentifier = "https://example.com/"

	def __init__(self, items, tree=None):
		self.items = items
		self.rootNVDAObject = tree or FakeObject("DOCUMENT", (0, 100, 1920, 900))

	def _iterNodesByType(self, nodeType):
		return iter(self.items.get(nodeType, []))


def run(collector: Collector) -> int:
	collector.start()
	rounds = 0
	while _stubs.callAfterQueue.pending:
		_stubs.callAfterQueue.flush()
		rounds += 1
	return rounds


class TestCollector(unittest.TestCase):
	def setUp(self):
		_stubs.callAfterQueue.discard()
		FakeItem.located = 0

	def landmarkPage(self, links=60, buttons=()):
		banner = FakeItem(
			1,
			0,
			100,
			FakeObject("LANDMARK", (0, 100, 1920, 90), attributes={"xml-roles": "banner", "tag": "header"}),
		)
		main = FakeItem(
			2,
			100,
			1000,
			FakeObject("LANDMARK", (0, 190, 1920, 800), attributes={"xml-roles": "main", "tag": "main"}),
		)
		linkItems = [
			FakeItem(100 + i, 100 + i * 10, 101 + i * 10, FakeObject("LINK", (20, 200 + i * 20, 200, 18)))
			for i in range(links)
		]
		return FakeDocument({"landmark": [banner, main], "link": linkItems, "button": list(buttons)})

	def test_gathersInBatchesWhatTheRulesRead(self):
		got = []
		rounds = run(Collector(self.landmarkPage(), lambda payload, why: got.append((payload, why))))
		self.assertGreater(rounds, 2, "NVDA gets its message loop back between batches")
		payload, why = got[0]
		self.assertEqual(why, "")
		records = payload["candidates"]["records"]
		self.assertEqual([r["xmlRoles"] for r in records], ["banner", "main"])
		self.assertEqual(records[1]["links"], 60)
		self.assertEqual(records[0]["rect"], [0, 0, 1920, 90], "relative to the page's own corner")
		self.assertEqual(len(payload["leaves"]["leaves"]), 60)
		self.assertEqual(payload["tree"]["nodes"], [], "a page with landmarks needs no tree walk")
		page = findRegions(*fromProbe(payload))
		self.assertEqual([r.kind for r in page.children], ["banner", "main"])

	def test_onlySearchButtonsAreLocated(self):
		buttons = [
			FakeItem(500 + i, 50, 51, FakeObject("BUTTON", (0, 100, 30, 30), "Menu"), label="Menu")
			for i in range(20)
		]
		search = FakeItem(600, 60, 61, FakeObject("BUTTON", (1800, 120, 40, 30), "Search"), label="Search")
		got = []
		run(Collector(self.landmarkPage(links=0, buttons=[*buttons, search]), lambda p, w: got.append(p)))
		records = got[0]["candidates"]["records"]
		self.assertEqual([r["type"] for r in records].count("search button"), 1)
		self.assertEqual(FakeItem.located, 3, "two landmarks and the search button, not the menus")

	def test_leavesAreSampledFromEveryKindAndEveryPart(self):
		"""Hundreds of links must not crowd out the pictures, nor the bottom of the page."""
		links = [
			FakeItem(1000 + i, i, i + 1, FakeObject("LINK", (20, 100 + i * 10, 200, 8))) for i in range(900)
		]
		pictures = [
			FakeItem(3000 + i, i, i + 1, FakeObject("GRAPHIC", (600, 100 + i * 200, 300, 150)))
			for i in range(30)
		]
		got = []
		run(Collector(FakeDocument({"link": links, "graphic": pictures}), lambda p, w: got.append(p)))
		leaves = got[0]["leaves"]["leaves"]
		self.assertLessEqual(len(leaves), 300)
		self.assertEqual(sum(1 for leaf in leaves if leaf["type"] == "graphic"), 30)
		linkTops = [leaf["rect"][1] for leaf in leaves if leaf["type"] == "link"]
		self.assertGreater(max(linkTops), 8000, "links from the bottom of the page too")
		self.assertIn("sampled", got[0]["incomplete"])

	def test_cancelledNeverReports(self):
		got = []
		collector = Collector(self.landmarkPage(), lambda p, w: got.append(p))
		collector.start()
		_stubs.callAfterQueue.flush()
		collector.cancel()
		while _stubs.callAfterQueue.pending:
			_stubs.callAfterQueue.flush()
		self.assertEqual(got, [])

	def test_pageWithoutLandmarksWalksTheTree(self):
		cells = [
			FakeObject("TABLECELL", (100 + i * 400, 150, 380, 600), attributes={"tag": "td"})
			for i in range(3)
		]
		row = FakeObject("TABLEROW", (100, 150, 1180, 600), attributes={"tag": "tr"}, children=cells)
		table = FakeObject("TABLE", (100, 150, 1180, 600), attributes={"tag": "table"}, children=[row])
		root = FakeObject("DOCUMENT", (0, 100, 1920, 900), children=[table])
		got = []
		run(Collector(FakeDocument({}, root), lambda p, w: got.append(p)))
		nodes = got[0]["tree"]["nodes"]
		self.assertEqual(
			[n["role"] for n in nodes],
			["DOCUMENT", "TABLE", "TABLEROW", "TABLECELL", "TABLECELL", "TABLECELL"],
		)
		self.assertEqual([n["depth"] for n in nodes], [0, 1, 2, 3, 3, 3])
		self.assertEqual(nodes[3]["rect"], [100, 50, 380, 600])


if __name__ == "__main__":
	unittest.main()
