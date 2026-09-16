# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for unwrapped lines: every line on one row, and the band panned across by its width.

For code on a Monarch. Wrapped, a long line takes several rows and the indent at the left of
the lines below it moves down with it, so the left margin stops being a place to feel the
structure. Unwrapped, each line of the document is one row of the band, and what is past the
band's width is a page across.

One cell per character, which is what the harness's regions produce, so a row reads back as
the characters it holds.
"""

import unittest

from ._stubs import CursorManagerRegion, FakeTreeInterceptor, installStubs

installStubs()

from brlMultiline import keyLayers  # noqa: E402
from brlMultiline.flow import NO_POSITION, BlockId, SourceBlock  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402
from brlMultiline.flowSources import DocumentFlowSource, regionFactoryFor  # noqa: E402

from ._stubs import Region  # noqa: E402

COLS = 8


class FakeHandler:
	def __init__(self):
		self.buffer = None


def textOf(row) -> str:
	return "".join(" " if cell == 0 else chr(cell) for cell in row)


def block(text: str, name="b") -> SourceBlock:
	return SourceBlock(blockId=BlockId(generation=1, bookmark=name, unit="line"), region=Region(text))


def unwrappedRenderer(page=0, numCols=COLS) -> FlowRenderer:
	renderer = FlowRenderer(FakeHandler(), numCols=numCols, fillRows=False, markCuts=True)
	renderer.unwrappedPage = page
	return renderer


def controllerOver(lines, caretIndex=0, numRows=4, numCols=COLS, live=True):
	interceptor = FakeTreeInterceptor(lines, caretIndex=caretIndex)
	source = DocumentFlowSource(
		interceptor,
		regionFactoryFor(CursorManagerRegion(interceptor), live=live),
		generation=1,
	)
	renderer = FlowRenderer(FakeHandler(), numCols=numCols, fillRows=True)
	control = FlowController(source, renderer, numRows=numRows, live=live)
	control.enterAtCursor()
	return control


def rowTexts(control) -> list[str]:
	numCols = control.renderer.numCols
	cells = control.cells()
	return [
		"".join(chr(cell) if cell else " " for cell in cells[index * numCols : (index + 1) * numCols])
		for index in range(control.window.numRows)
	]


CODE = [
	"def f():",
	"    return compute(first, second, third)",
	"x = 1",
]
"""A short line, a line three pages long on an eight cell band, and another short one."""


class TestRenderingOneLine(unittest.TestCase):
	def test_aLongLineIsOneRowOfTheFirstPage(self):
		rendered = unwrappedRenderer().render(block("abcdefghijklmnopqrst"))
		self.assertEqual([textOf(row) for row in rendered.rows], ["abcdefgh"])
		self.assertEqual(rendered.lineCells, 20)

	def test_theNextPageIsTheNextWidthOfTheLine(self):
		rendered = unwrappedRenderer(page=1).render(block("abcdefghijklmnopqrst"))
		self.assertEqual([textOf(row) for row in rendered.rows], ["ijklmnop"])

	def test_aPageIsCutAtTheBandsWidthWhateverTheWrappingRule(self):
		"""The renderer here wraps at words and marks cuts, and neither may move where a page
		starts: page two of every line has to begin at the same cell of the line."""
		rendered = unwrappedRenderer(page=1).render(block("ab cd ef gh ij kl"))
		self.assertEqual(textOf(rendered.rows[0]), " gh ij k")

	def test_aLineTooShortForThePageIsABlankRowOfItsOwn(self):
		rendered = unwrappedRenderer(page=1).render(block("abc"))
		self.assertEqual(rendered.numRows, 1)
		self.assertEqual(rendered.rows[0], ())

	def test_eachCellStillSaysWhereInTheLineItCameFrom(self):
		"""Or a routing key on page two would put the caret on page one."""
		rendered = unwrappedRenderer(page=1).render(block("abcdefghijklmnopqrst"))
		self.assertEqual(rendered.positions[0], tuple(range(8, 16)))

	def test_thePageIsPartOfTheRenderKey(self):
		first = unwrappedRenderer(page=0).render(block("abcdefghijklmnop"))
		second = unwrappedRenderer(page=1).render(block("abcdefghijklmnop"))
		self.assertNotEqual(first.renderKey, second.renderKey)

	def test_wrappingIsUnchangedWhenNotUnwrapped(self):
		renderer = FlowRenderer(FakeHandler(), numCols=COLS, fillRows=True)
		rendered = renderer.render(block("abcdefghijklmnopqrst"))
		self.assertEqual(rendered.numRows, 3)
		self.assertEqual(rendered.lineCells, 0)
		self.assertNotIn(NO_POSITION, rendered.positions[0])


class TestTheBand(unittest.TestCase):
	def test_unwrappingPutsEveryLineOnARowOfItsOwn(self):
		control = controllerOver(CODE)
		self.assertNotEqual(rowTexts(control)[2], "x = 1   ")
		self.assertTrue(control.setUnwrapped(True))
		self.assertEqual(rowTexts(control)[:3], ["def f():", "    retu", "x = 1   "])

	def test_turningItOnTwiceChangesNothing(self):
		control = controllerOver(CODE)
		control.setUnwrapped(True)
		self.assertFalse(control.setUnwrapped(True))

	def test_wrappingAgainPutsTheLongLineBackOnSeveralRows(self):
		control = controllerOver(CODE)
		wrapped = rowTexts(control)
		control.setUnwrapped(True)
		self.assertTrue(control.setUnwrapped(False))
		self.assertEqual(rowTexts(control), wrapped)
		self.assertIsNone(control.pageAcross)

	def test_panningAcrossMovesEveryLineByTheBandsWidth(self):
		control = controllerOver(CODE)
		control.setUnwrapped(True)
		self.assertTrue(control.panAcross(1))
		rows = rowTexts(control)
		self.assertEqual(rows[1], "rn compu")
		# The short lines have nothing that far across, and each still has its own row.
		self.assertEqual(rows[0].strip(), "")
		self.assertEqual(rows[2].strip(), "")

	def test_panningAcrossStopsAtTheEndOfTheLongestLineOnTheBand(self):
		control = controllerOver(CODE)
		control.setUnwrapped(True)
		longest = len(CODE[1])
		pages = 0
		while control.panAcross(1):
			pages += 1
			self.assertLess(pages, 10)
		self.assertEqual(control.pageAcross, (longest - 1) // COLS)
		self.assertNotEqual(rowTexts(control)[1].strip(), "")

	def test_panningLeftStopsAtTheStartOfTheLines(self):
		control = controllerOver(CODE)
		control.setUnwrapped(True)
		self.assertFalse(control.panAcross(-1))
		control.panAcross(1)
		self.assertTrue(control.panAcross(-1))
		self.assertEqual(control.pageAcross, 0)

	def test_panningAcrossLeavesTheCaretAndTheRowsAlone(self):
		control = controllerOver(CODE, caretIndex=1)
		control.setUnwrapped(True)
		before = (control.source.obj.caretIndex, control.window.topBlockId())
		control.panAcross(1)
		self.assertEqual(before, (control.source.obj.caretIndex, control.window.topBlockId()))

	def test_panningAcrossDoesNothingWhileLinesWrap(self):
		control = controllerOver(CODE)
		self.assertFalse(control.panAcross(1))

	def test_aRoutingKeyOnTheSecondPageReachesThatPartOfTheLine(self):
		control = controllerOver(CODE, caretIndex=1)
		control.setUnwrapped(True)
		control.panAcross(1)
		self.assertEqual(rowTexts(control)[0], "rn compu")
		# The third cell of the second page of the line on the top row.
		self.assertTrue(control.routeTo(2))
		self.assertEqual(control.regionFor(control.window.blocks[0].blockId).routedTo, COLS + 2)

	def test_theWholeBandIsDescribedForTheReport(self):
		control = controllerOver(CODE)
		control.setUnwrapped(True)
		control.panAcross(1)
		self.assertEqual(control.describeAcross(), (9, 16, len(CODE[1])))


class TestTheCaretAcross(unittest.TestCase):
	def _caretAt(self, control, index, offset):
		interceptor = control.source.obj
		interceptor.caretIndex = index
		interceptor.caretOffset = offset
		control.followCursor()

	def test_unwrappingStartsAtThePageTheCaretIsOn(self):
		control = controllerOver(CODE, caretIndex=1)
		control.source.obj.caretOffset = 20
		control.followCursor()
		control.setUnwrapped(True)
		self.assertEqual(control.pageAcross, 2)

	def test_aCaretThatMovesOffThePageBringsTheBandToIt(self):
		control = controllerOver(CODE, caretIndex=1)
		control.setUnwrapped(True)
		self._caretAt(control, 1, 12)
		self.assertTrue(control.followCaretAcross())
		self.assertEqual(control.pageAcross, 1)
		self.assertIsNotNone(control.cursorCell())

	def test_aCaretAlreadyOnThePageMovesNothing(self):
		control = controllerOver(CODE, caretIndex=1)
		control.setUnwrapped(True)
		self._caretAt(control, 1, 3)
		self.assertFalse(control.followCaretAcross())
		self.assertEqual(control.pageAcross, 0)

	def test_aPanIsTheReadersUntilTheCaretMoves(self):
		"""Reading the rest of a line must not be undone by the next redraw."""
		control = controllerOver(CODE, caretIndex=1)
		control.setUnwrapped(True)
		control.panAcross(1)
		self.assertFalse(control.followCaretAcross())
		self.assertEqual(control.pageAcross, 1)
		self._caretAt(control, 2, 0)
		self.assertTrue(control.followCaretAcross())
		self.assertEqual(control.pageAcross, 0)

	def test_theCaretIsNotDrawnOnAPageItIsNotOn(self):
		control = controllerOver(CODE, caretIndex=1)
		control.setUnwrapped(True)
		control.panAcross(1)
		self.assertIsNone(control.cursorCell())


class TestTheBandsChoice(unittest.TestCase):
	"""The setting, and the command that turns it over for the time being."""

	def setUp(self):
		import braille
		from brlMultiline.flowBand import FlowBand

		from ._stubs import CONFIG, FakeHandler, resetConfig

		resetConfig()
		self.addCleanup(resetConfig)
		# The setting is read against the display the band is on, so there has to be one.
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = FakeHandler(4, COLS)
		self.config = CONFIG
		self.band = FlowBand(plugin=None)

	def test_theSettingDecidesUntilTheReaderTurnsItOver(self):
		self.assertFalse(self.band.unwrapLines())
		self.config["flowUnwrapLines"] = True
		self.assertTrue(self.band.unwrapLines())

	def test_turningItOverOverridesTheSetting(self):
		self.assertTrue(self.band.toggleUnwrapLines())
		self.assertTrue(self.band.unwrapLines())
		self.assertFalse(self.band.toggleUnwrapLines())
		self.assertFalse(self.band.unwrapLines())

	def test_theReadersChoiceLastsUntilTheSettingItselfChanges(self):
		"""A profile switch that changes nothing — out to NVDA's menu and back — keeps it; a
		profile or a settings change that does change it takes over."""
		self.config["flowUnwrapLines"] = True
		self.band.toggleUnwrapLines()
		self.assertFalse(self.band.unwrapLines())
		self.config["flowUnwrapLines"] = True
		self.assertFalse(self.band.unwrapLines())
		self.config["flowUnwrapLines"] = False
		self.assertFalse(self.band.unwrapLines())
		self.config["flowUnwrapLines"] = True
		self.assertTrue(self.band.unwrapLines())


class TestTheKeys(unittest.TestCase):
	PLUGIN = "globalPlugins.brlMultiline"

	def _monarch(self, key):
		return keyLayers.normalize(f"br({keyLayers.MONARCH}):{key}")

	def test_theMonarchPansAcrossWithTheZoomKeysWhileLinesAreUnwrapped(self):
		layers = keyLayers.LayerSet(
			keyLayers.MONARCH, tuple(keyLayers.defaultLayers(keyLayers.MONARCH, self.PLUGIN))
		)
		unwrapped = layers.get("unwrapped")
		self.assertEqual(("unwrapped", True), (unwrapped.context, unwrapped.autoEnable))
		self.assertEqual("flowPanLinesRight", unwrapped.bindings[self._monarch("zoomIn")].scriptName)
		self.assertEqual("flowPanLinesLeft", unwrapped.bindings[self._monarch("zoomOut")].scriptName)
		self.assertEqual(2, len(unwrapped.bindings))

	def test_layersSavedBeforeItAreGivenIt(self):
		factory = keyLayers.defaultLayers(keyLayers.MONARCH, self.PLUGIN)
		saved = keyLayers.LayerSet(
			keyLayers.MONARCH, tuple(layer for layer in factory if layer.id != "unwrapped")
		)
		read, added = keyLayers.withShippedAdditions(saved, factory, 2)
		self.assertIsNotNone(read.get("unwrapped"))
		self.assertTrue(added)

	def test_itIsTheLeastSpecificContext(self):
		self.assertEqual("unwrapped", keyLayers.CONTEXTS[-1])

	def test_theContextIsPresentWhileTheBandUnwraps(self):
		from brlMultiline import keyLayerContexts

		class Band:
			def __init__(self, showing):
				self.showing = showing

			def isShowingUnwrapped(self):
				return self.showing

		class Plugin:
			graphicsMode = None
			flowBand = Band(True)

		self.assertIn("unwrapped", keyLayerContexts.presentContexts(Plugin(), tables=False))
		Plugin.flowBand = Band(False)
		self.assertNotIn("unwrapped", keyLayerContexts.presentContexts(Plugin(), tables=False))
		self.assertIn("unwrapped", keyLayerContexts.DETECTED)


if __name__ == "__main__":
	unittest.main()
