# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the global plugin: the lifetime of the container, and the claims on it.

This is the layer that was left uncovered when panels were built, and the layer where the
interesting failures live. Nothing here happens inside a single container: every case is
about what survives one being replaced, or about work queued against a container that no
longer exists.

`wx.CallAfter` is a queue the tests flush by hand, so a deferred rebuild can be inspected
before, after, or instead of running.
"""

import contextlib
import os
import sys
import types
import unittest

from ._stubs import (
	CONFIG,
	ID_CANCEL,
	ID_OK,
	NO,
	YES,
	FakeCallLater,
	FakeGridCell,
	FakeHandler,
	FakeNavigatorObject,
	FakeTreeInterceptor,
	Region,
	callAfterQueue,
	callLaterQueue,
	dialogAnswers,
	mainFrame,
	displayChanged,
	displaySizeChanged,
	fakeGetFocusRegions,
	fakeVirtualDisplay,
	flashedMessages,
	installStubs,
	log,
	loadPlugin,
	navigatedTo,
	post_configProfileSwitch,
	resetPluginState,
	setBandConfig,
	setSpeechOutputMode,
	spokenMessages,
)

installStubs()
plugin = loadPlugin()
GlobalPlugin = plugin.GlobalPlugin

import braille  # noqa: E402
import config  # noqa: E402
from braille.brailleHandler import BrailleHandler  # noqa: E402

from brlMultiline import patches  # noqa: E402
from brlMultiline import panning  # noqa: E402
from brlMultiline import bmConfig  # noqa: E402
from brlMultiline import imagePins, imageSource  # noqa: E402
from brlMultiline import focusDisplays  # noqa: E402
from brlMultiline import pictureCommands  # noqa: E402
from brlMultiline import flowObjects  # noqa: E402
from brlMultiline.container import PLACEMENT_KEY_ATTRIBUTE, DisplayContainer  # noqa: E402
from brlMultiline.layout import SegmentRect  # noqa: E402
from brlMultiline.messages import MessageBuffer  # noqa: E402
from brlMultiline.panels import (  # noqa: E402
	BlankPanel,
	FlowPanel,
	GraphicsPanel,
	GridPanel,
	SinglePanel,
)
from brlMultiline.devices import deviceMap  # noqa: E402
from brlMultiline.views import (  # noqa: E402
	SegmentView,
	driverNameForSegmentKey,
	validateAgainstHardware,
)

MONARCH_ROWS = 8
MONARCH_COLS = 32


class TestEveryCommandCanBeAssignedAKey(unittest.TestCase):
	"""A command NVDA has not been told about is a command with no way to reach it.

	It is not a runtime failure and nothing logs it: the method is there, it works, and it
	simply never appears in Input Gestures, so the first sign is a reader going to assign the
	key and finding nothing. This has happened once already — an edit anchored on a `def`
	line landed between the command below it and its own decorator, which took the decorator
	off one command and gave a second one to another.
	"""

	def _scripts(self):
		return {
			name: getattr(GlobalPlugin, name)
			for name in dir(GlobalPlugin)
			if name.startswith("script_") and callable(getattr(GlobalPlugin, name, None))
		}

	def test_thereAreSomeToCheck(self):
		self.assertGreater(len(self._scripts()), 4)

	def test_everyCommandIsInACategory(self):
		"""Which is what puts it in the list under a heading the reader can find."""
		for name, script in self._scripts().items():
			with self.subTest(script=name):
				self.assertEqual(getattr(script, "category", None), plugin.SCRIPT_CATEGORY)

	def test_everyCommandSaysWhatItDoes(self):
		"""NVDA shows the description as the command's name in Input Gestures, so a command
		without one is an unlabelled row."""
		for name, script in self._scripts().items():
			with self.subTest(script=name):
				self.assertTrue((script.__doc__ or "").strip(), f"{name} has no description")

	def test_noTwoCommandsShareADescription(self):
		"""Two rows with the same name in the list is the shape the lost decorator made, and
		it is indistinguishable from a duplicate command."""
		described = [(script.__doc__ or "").strip() for script in self._scripts().values()]
		self.assertEqual(len(set(described)), len(described))


class TestDrawingThePictureHere(unittest.TestCase):
	"""The two commands that put a picture from the screen on the pins.

	Only one decision lives at this level and it is worth a test of its own: changing how a
	picture is drawn must not copy the screen again. By the time a reader presses the style key
	the page may have scrolled and the focus may have moved, so a fresh capture would be a
	capture of something else — and the command that said it was changing the style would
	quietly have changed the picture too, which the reader would experience as the display
	losing what they were reading.
	"""

	class Dots:
		"""A drawing surface, reduced to what a rendering stamps onto."""

		def __init__(self, width, height):
			self.width = width
			self.height = height
			self.raised = set()

		def setDot(self, x, y):
			self.raised.add((x, y))

		def rows(self):
			return tuple(sorted(self.raised))

	class FakeMode:
		"""The graphics mode, reduced to what `PictureCommands._draw` asks of it.

		Models the three things the commands read back — whether it is active, what it is
		showing, and how much braille is kept beside it — because all three are questions the
		picture commands have to ask and getting any of them wrong is invisible from inside
		the plugin.
		"""

		def __init__(self, size=(48, 20)):
			self.size = size
			self.shown = []
			self.lastError = ""
			self.active = False
			self.source = None
			self.textLines = 1
			self.askedFor = []
			"""Every text line count `drawingSize` was asked about."""
			self.refuseReplace = False
			"""Whether a style change should be refused, as one that will not compose the
			window the reader is on would be."""

		def drawingSize(self, textLines=None):
			self.askedFor.append(textLines)
			if self.size is None:
				return None
			# A line of braille costs the drawing rows, which is the whole reason the count
			# has to be carried through a style change.
			return (self.size[0], self.size[1] - 5 * (textLines or 0))

		def newBuffer(self, width, height):
			return TestDrawingThePictureHere.Dots(width, height)

		def enter(self, drawing=None, textLines=None):
			self.shown.append(drawing)
			self.source = drawing
			self.active = True
			self.textLines = textLines if textLines is not None else 1
			return True

		def replaceSource(self, drawing):
			"""Swap the figure without touching the view, which is what a style change does.

			Modelled as its own call rather than as another `enter`, because the difference is
			the whole point: `enter` resets the zoom and both origins, so a reader comparing
			two styles of the same diagram would be moved off the part they were comparing.
			"""
			if not self.active or self.refuseReplace:
				return False
			self.shown.append(drawing)
			self.source = drawing
			return True

		def leave(self):
			self.active = False
			self.source = None

		def describe(self):
			return self.shown[-1].name if self.shown else ""

	def test_aPictureThatWillNotGoUpDoesNotReplaceTheOneThatIs(self):
		"""The cached pixels and the drawing made from them have to be one thing.

		Assigned separately, capturing B and then failing to show it left the pixels as B while
		the drawing and the mode still held A. The style key checks that the drawing on the
		display is the last picture drawing -- which it was -- so the next press composed B
		from the cached pixels and put it over A, on a command that says it changes the style.
		"""
		self.plugin.script_drawPicture(None)
		first = self.plugin.pictures.picture
		self.assertIsNotNone(first)
		self.mode.enter = lambda drawing=None, textLines=None: False
		self.plugin.script_drawPicture(None)
		self.assertIs(self.plugin.pictures.picture, first)

	def test_andTheStyleKeyStillDrawsTheOneOnTheDisplay(self):
		"""What the divergence actually cost: the next style press drew the wrong picture."""
		self.plugin.script_drawPicture(None)
		wasShowing = self.mode.source
		self.mode.enter = lambda drawing=None, textLines=None: False
		self.plugin.script_drawPicture(None)
		self.plugin.script_pictureStyle(None)
		self.assertIs(self.plugin.pictures.drawing, self.mode.source)
		self.assertIsNot(self.mode.source, wasShowing)

	def test_changingTheStyleKeepsTheReaderWhereTheyWere(self):
		"""It goes through `replaceSource` rather than `enter`, because `enter` resets the zoom
		and both origins -- and comparing two styles means feeling the same part in both."""
		self.plugin.script_drawPicture(None)
		asked = len(self.mode.askedFor)
		self.plugin.script_pictureStyle(None)
		self.assertEqual(len(self.mode.shown), 2)
		# The first drawing leaves the line to the mode; a style change carries the reader's.
		self.assertNotIn(None, self.mode.askedFor[asked:])

	def test_aStyleThatWillNotDrawTheCurrentWindowSaysSo(self):
		self.plugin.script_drawPicture(None)
		showing = self.mode.source
		self.mode.refuseReplace = True
		self.plugin.script_pictureStyle(None)
		self.assertIs(self.mode.source, showing)
		self.assertTrue(any("style" in said for said in flashedMessages))

	def test_andTheStyleOnTheDisplayIsStillTheOneChosen(self):
		"""Found by review. The style was set before drawing, so after a refusal the old
		drawing was on the display and the next press moved on from the one that failed."""
		from brlMultiline import image as imageFigure

		self.plugin.script_drawPicture(None)
		self.mode.refuseReplace = True
		self.plugin.script_pictureStyle(None)
		self.assertEqual(self.plugin.pictures.style, imageFigure.OUTLINES)
		self.mode.refuseReplace = False
		self.plugin.script_pictureStyle(None)
		self.assertEqual(
			self.plugin.pictures.style, imageFigure.LINES, "the cycle resumes from what was shown",
		)

	def test_aStyleThePictureWillNotDrawInLeavesTheStyleAlone(self):
		"""The other way a style is refused: composing it fails, before the display is asked."""
		from brlMultiline import image as imageFigure

		self.plugin.script_drawPicture(None)
		realFigure = imageFigure.figureFor

		def refuseSilhouettes(newBuffer, picture, width, height, mode, invert=False):
			if mode == imagePins.BRIGHTNESS:
				raise imagePins.ImageRefused("There is too little in this picture to feel")
			return realFigure(newBuffer, picture, width, height, mode, invert)

		imageFigure.figureFor = refuseSilhouettes
		self.addCleanup(setattr, imageFigure, "figureFor", realFigure)
		self.plugin.script_pictureBrightness(None)
		self.assertIn("There is too little in this picture to feel", flashedMessages)
		self.assertEqual(self.plugin.pictures.style, imageFigure.OUTLINES)
		self.assertEqual(len(self.mode.shown), 1)

	def setUp(self):
		resetPluginState()
		self.handler = FakeHandler(MONARCH_ROWS, MONARCH_COLS)
		braille.handler = self.handler
		self.plugin = GlobalPlugin()
		self.addCleanup(self.tidy)
		self.mode = self.FakeMode()
		self.plugin.graphicsMode = self.mode
		self.captures = []
		self._realCapture = imageSource.captureNavigator
		imageSource.captureNavigator = self._capture
		flashedMessages.clear()

	def tidy(self):
		imageSource.captureNavigator = self._realCapture
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		except Exception:
			pass

	def _capture(self):
		"""A dark square on a light field, and a note that the screen was read."""
		self.captures.append(True)
		width = height = 90
		greys = bytearray()
		for y in range(height):
			for x in range(width):
				greys.append(0 if (30 <= x < 60 and 30 <= y < 60) else 255)
		return imagePins.Picture(greys, width, height, "a picture")

	def test_theFirstPressCopiesTheScreenAndShowsIt(self):
		self.plugin.script_drawPicture(None)
		self.assertEqual(len(self.captures), 1)
		self.assertEqual(len(self.mode.shown), 1)

	def test_changingTheStyleDrawsTheSamePixelsAgain(self):
		self.plugin.script_drawPicture(None)
		self.plugin.script_pictureStyle(None)
		self.assertEqual(len(self.captures), 1)
		self.assertEqual(len(self.mode.shown), 2)
		self.assertNotEqual(self.mode.shown[0].buffer.rows(), self.mode.shown[1].buffer.rows())

	def test_drawingAgainDoesCopyTheScreen(self):
		"""The other half of the rule: the picture command is always a fresh look."""
		self.plugin.script_drawPicture(None)
		self.plugin.script_drawPicture(None)
		self.assertEqual(len(self.captures), 2)

	def test_theStyleKeyWithNoPictureSaysSoRatherThanDoingNothing(self):
		self.plugin.script_pictureStyle(None)
		self.assertFalse(self.mode.shown)
		self.assertTrue(flashedMessages)

	def test_theStyleIsKeptFromOnePictureToTheNext(self):
		"""A reader who has found that brightness works for the diagrams on a page is reading
		a page of diagrams."""
		self.plugin.script_drawPicture(None)
		self.plugin.script_pictureStyle(None)
		chosen = self.plugin.pictures.style
		self.plugin.script_drawPicture(None)
		self.assertEqual(self.plugin.pictures.style, chosen)

	def test_aRefusalReachesTheReaderAsWords(self):
		"""Every refusal names what went wrong, because a reader told only that it failed
		cannot tell whether to move, change the style, or give up."""

		def refuse():
			raise imagePins.ImageRefused("too small to draw")

		imageSource.captureNavigator = refuse
		self.plugin.script_drawPicture(None)
		self.assertFalse(self.mode.shown)
		self.assertIn("too small to draw", flashedMessages)

	def test_aDisplayThatCannotDrawSaysSo(self):
		self.mode.size = None
		self.plugin.script_drawPicture(None)
		self.assertFalse(self.captures)
		self.assertTrue(flashedMessages)

	def test_theStyleKeyDoesNothingOnceTheDrawingHasGone(self):
		"""The pixels outlive the figure. A reader who drew a picture and then left the
		drawing must not have it put back by a key that says it changes how something is
		drawn."""
		self.plugin.script_drawPicture(None)
		self.mode.leave()
		self.plugin.script_pictureStyle(None)
		self.assertEqual(len(self.mode.shown), 1)

	def test_theStyleKeyDoesNothingOnceSomethingElseIsUp(self):
		"""A chart, or the glyph catalogue. Same fault, and this is the way round a reader
		would actually meet it."""
		self.plugin.script_drawPicture(None)
		self.mode.enter(types.SimpleNamespace(name="a chart", buffer=None))
		self.plugin.script_pictureStyle(None)
		self.assertEqual(self.mode.source.name, "a chart")

	def test_givingTheWholePanelToAPictureSurvivesAStyleChange(self):
		"""Both the size asked for and the size entered with. Asking for the default instead
		would put the braille line back and shrink the drawing, while claiming only to have
		changed its style."""
		self.plugin.script_drawPicture(None)
		self.mode.textLines = 0
		self.mode.askedFor = []
		self.plugin.script_pictureStyle(None)
		self.assertEqual(self.mode.askedFor, [0])
		self.assertEqual(self.mode.textLines, 0)
		self.assertEqual(self.mode.shown[-1].width, 48)
		self.assertEqual(self.mode.shown[-1].height, 20)

	def test_eachStyleHasAKeyOfItsOwn(self):
		"""Once a reader knows brightness reads a kind of picture, cycling through outlines to
		reach it is a panel of something they already know they do not want."""
		from brlMultiline import image as imageFigure

		self.plugin.script_drawPicture(None)
		self.plugin.script_pictureReversed(None)
		self.assertEqual(self.plugin.pictures.style, imageFigure.REVERSED)
		self.plugin.script_pictureBrightness(None)
		self.assertEqual(self.plugin.pictures.style, imageFigure.SILHOUETTE)
		self.plugin.script_pictureLines(None)
		self.assertEqual(self.plugin.pictures.style, imageFigure.LINES)
		self.plugin.script_pictureOutlines(None)
		self.assertEqual(self.plugin.pictures.style, imageFigure.OUTLINES)
		self.assertEqual(len(self.mode.shown), 5)
		self.assertEqual(len(self.captures), 1, "a change of style copies nothing off the screen")

	def test_theStyleAlreadyUpIsNotDrawnAgain(self):
		self.plugin.script_drawPicture(None)
		self.plugin.script_pictureOutlines(None)
		self.assertEqual(len(self.mode.shown), 1)

	def test_aStyleKeyWithNoPictureUpSaysSo(self):
		self.plugin.script_pictureBrightness(None)
		self.assertIn("There is no picture to change", flashedMessages)

	def test_aNewPictureKeepsThePanelTheReaderAskedFor(self):
		"""Same argument. Drawing a second picture is not a decision about the braille line."""
		self.plugin.script_drawPicture(None)
		self.mode.textLines = 0
		self.plugin.script_drawPicture(None)
		self.assertEqual(self.mode.textLines, 0)


class TestTheChartKeys(unittest.TestCase):
	"""The zoom to one pin per point and the view keys, which say why whenever they do nothing.

	A key that does nothing and says nothing is indistinguishable from one that is not bound.
	"""

	class FakeMode:
		def __init__(self, active=True, hasViews=True, pointRefusal="", zooms=True, changes=True):
			self.active = active
			self.hasViews = hasViews
			self.pointRefusal = pointRefusal
			self.zooms = zooms
			self.changes = changes
			self.directions = []
			self.markedPoint = None

		def pointZoomRefusal(self):
			return self.pointRefusal

		def zoomToPoints(self):
			return self.zooms

		def changeView(self, direction):
			self.directions.append(direction)
			return self.changes

		def describe(self):
			return "the chart now"

		def drawingSize(self):
			return (96, 35)

		def newBuffer(self, width, height):
			return None

		def enter(self, drawing):
			return True

	def setUp(self):
		resetPluginState()
		braille.handler = FakeHandler(MONARCH_ROWS, MONARCH_COLS)
		self.plugin = GlobalPlugin()
		self.addCleanup(self.tidy)
		flashedMessages.clear()

	def tidy(self):
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		except Exception:
			pass

	def use(self, **kwargs):
		self.plugin.graphicsMode = self.FakeMode(**kwargs)
		return self.plugin.graphicsMode

	def test_aZoomToOnePinPerPointSaysWhereItNowIs(self):
		self.use()
		self.plugin.script_graphicsZoomToPoints(None)
		self.assertEqual(flashedMessages[-1], "the chart now")

	def test_aZoomThatCannotHappenSaysWhy(self):
		self.use(pointRefusal="every point already has its own pin")
		self.plugin.script_graphicsZoomToPoints(None)
		self.assertEqual(flashedMessages[-1], "every point already has its own pin")

	def test_aZoomThatWillNotDrawSaysSo(self):
		self.use(zooms=False)
		self.plugin.script_graphicsZoomToPoints(None)
		self.assertIn("will not draw", flashedMessages[-1])

	def test_theViewKeysGoBothWays(self):
		mode = self.use()
		self.plugin.script_graphicsNextView(None)
		self.plugin.script_graphicsPreviousView(None)
		self.assertEqual(mode.directions, [1, -1])
		self.assertEqual(flashedMessages[-1], "the chart now")

	def test_aDrawingWithOneViewSaysSo(self):
		mode = self.use(hasViews=False)
		self.plugin.script_graphicsNextView(None)
		self.assertEqual(mode.directions, [])
		self.assertIn("only one view", flashedMessages[-1])

	def test_aViewThatWillNotDrawSaysSo(self):
		self.use(changes=False)
		self.plugin.script_graphicsNextView(None)
		self.assertIn("will not draw", flashedMessages[-1])

	def refused(self):
		from brlMultiline import chartDraw, chartMenu

		def draw(newBuffer, width, height, translate):
			raise chartDraw.ChartRefused("277 periods will not fit; this display holds 24")

		return chartMenu.Offer(key=chartMenu.OHLC, label="Open, high, low, close bars", draw=draw)

	def test_aChartChosenInTheDialogIsAnsweredAfterTheFocusComesBack(self):
		"""Found on hardware: a refused price chart was said as the dialog closed, cut off by
		the focus returning to the spreadsheet, and heard as nothing happening."""
		self.use()
		callLaterQueue.pending.clear()
		self.addCleanup(callLaterQueue.pending.clear)
		self.plugin.pictures.drawChart(self.refused(), say=pictureCommands.sayAfterDialog)
		self.assertNotIn("277 periods will not fit; this display holds 24", flashedMessages)
		callLaterQueue.fire()
		self.assertEqual(flashedMessages[-1], "277 periods will not fit; this display holds 24")

	def test_aRefusedChartIsLogged(self):
		"""The log had nothing to show for a refusal, which made a lost one look like no answer."""
		self.use()
		log.messages.clear()
		self.plugin.pictures.drawChart(self.refused())
		self.assertEqual(flashedMessages[-1], "277 periods will not fit; this display holds 24")
		self.assertTrue(any("ohlc chart refused" in message for _level, message in log.messages))

	def test_withNoDrawingTheKeysSaySo(self):
		self.use(active=False)
		self.plugin.script_graphicsZoomToPoints(None)
		self.plugin.script_graphicsNextView(None)
		self.assertEqual(flashedMessages[-2:], ["No drawing", "No drawing"])


class TestFollowingAnApplicationsChart(unittest.TestCase):
	"""The plugin's half of `docs/design/excel-chart-plan.md`: drawing the chart the reader is on,
	marking the point the screen reader's chart navigation arrives at, and moving that
	navigation when the reader steps on the pins."""

	KEY = ("C:/charting.xlsx", "Prices Chart 1")

	class FakeMode:
		def __init__(self, chartKey=None):
			self.active = chartKey is not None
			self.source = types.SimpleNamespace(chartKey=chartKey)
			self.marks = []
			self.entered = []

		def markPoint(self, index, levelName=None, levelKey=None):
			self.marks.append((index, levelName, levelKey))
			return True

		def drawingSize(self, textLines=None):
			return (96, 35)

		def newBuffer(self, width, height):
			# The Monarch driver's own buffer, so a chart is really drawn.
			driver = os.path.join(
				os.path.dirname(__file__), "..", "..", "..", "addon", "brailleDisplayDrivers", "brlMultilineMonarch",
			)
			if driver not in sys.path:
				sys.path.insert(0, driver)
			from pinBuffer import PinBuffer

			return PinBuffer(width, height)

		def enter(self, drawing):
			self.entered.append(drawing)
			return True

		def describe(self):
			return "the chart now"

	class FakeChart:
		def __init__(self, key, definition=None):
			self._key = key
			self._definition = definition

		def key(self):
			return self._key

		def definition(self):
			return self._definition

	class FakePart:
		"""One of NVDA's chart objects, wearing the Excel module's overlay."""

		def __init__(self, test, key, point=None, seriesName="Close"):
			self.test = test
			self.key = key
			self.point = point
			self.seriesName = seriesName
			self.led = []

		def brlMultilineChart(self):
			return TestFollowingAnApplicationsChart.FakeChart(self.key)

		def brlMultilinePoint(self):
			if self.point is None:
				return None
			return types.SimpleNamespace(key=self.key, index=self.point, seriesName=self.seriesName, series=2)

		def brlMultilineLeadTo(self, index):
			self.led.append(index)
			return True

	def setUp(self):
		resetPluginState()
		braille.handler = FakeHandler(MONARCH_ROWS, MONARCH_COLS)
		self.plugin = GlobalPlugin()
		self.addCleanup(self.tidy)
		flashedMessages.clear()
		import api

		self.addCleanup(setattr, api, "getFocusObject", api.getFocusObject)
		self.api = api

	def tidy(self):
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		except Exception:
			pass

	def use(self, chartKey=KEY):
		self.plugin.graphicsMode = self.FakeMode(chartKey)
		return self.plugin.graphicsMode

	def focusOn(self, obj):
		self.api.getFocusObject = lambda: obj

	def test_aPointOfTheDrawnChartIsMarked(self):
		mode = self.use()
		self.plugin._followChart(self.FakePart(self, self.KEY, point=7, seriesName="Average"))
		self.assertEqual(mode.marks, [(7, "Average", 2)])

	def test_aPointOfAnotherChartIsNot(self):
		mode = self.use()
		self.plugin._followChart(self.FakePart(self, ("C:/charting.xlsx", "Other Chart 2"), point=7))
		self.assertEqual(mode.marks, [])

	def test_aPartThatIsNotAPointIsNot(self):
		mode = self.use()
		self.plugin._followChart(self.FakePart(self, self.KEY))
		self.assertEqual(mode.marks, [])

	def test_aDrawingNotFromAChartIsLeftAlone(self):
		mode = self.use()
		mode.source = types.SimpleNamespace()
		self.plugin._followChart(self.FakePart(self, self.KEY, point=7))
		self.assertEqual(mode.marks, [])

	def test_followingRunsOnEveryFocusChange(self):
		mode = self.use()
		self.plugin.event_gainFocus(self.FakePart(self, self.KEY, point=2), lambda: None)
		self.assertEqual(mode.marks, [(2, "Close", 2)])

	def test_aStepOnThePinsMovesTheScreenReadersPlace(self):
		self.use()
		focus = self.FakePart(self, self.KEY, point=1)
		self.focusOn(focus)
		self.plugin.onPointMarked(5)
		self.assertEqual(focus.led, [5])

	def test_notWhenTheReaderHasGoneBackToTheCells(self):
		self.use()
		self.focusOn(FakeNavigatorObject("a cell"))
		self.plugin.onPointMarked(5)

	def test_notWhenTheFocusIsInAnotherChart(self):
		self.use()
		focus = self.FakePart(self, ("C:/charting.xlsx", "Other Chart 2"), point=1)
		self.focusOn(focus)
		self.plugin.onPointMarked(5)
		self.assertEqual(focus.led, [])

	def test_theChartCommandOnAChartDrawsItWithoutAsking(self):
		from brlMultiline import chartSource

		mode = self.use()
		definition = chartSource.ChartDefinition(
			key=self.KEY,
			title="Prices",
			kind="line",
			typeName="Line",
			categories=["d0", "d1", "d2"],
			series=[chartSource.ChartSeries("Close", [1, 2, 3])],
		)
		part = self.FakePart(self, self.KEY)
		part.brlMultilineChart = lambda: self.FakeChart(self.KEY, definition)
		self.focusOn(part)
		self.assertTrue(self.plugin.pictures.drawFocusedChart())
		self.assertEqual(len(mode.entered), 1)
		self.assertEqual(mode.entered[0].chartKey, self.KEY)

	def test_aChartThatCannotBeDrawnSaysWhy(self):
		from brlMultiline import chartSource

		self.use()
		definition = chartSource.ChartDefinition(
			key=self.KEY,
			title="Prices",
			kind="",
			typeName="Scatter",
			categories=[],
			series=[],
		)
		part = self.FakePart(self, self.KEY)
		part.brlMultilineChart = lambda: self.FakeChart(self.KEY, definition)
		self.focusOn(part)
		self.assertTrue(self.plugin.pictures.drawFocusedChart())
		self.assertEqual(flashedMessages[-1], "Scatter charts cannot be drawn yet")

	def test_offAChartTheSelectionIsChartedAsBefore(self):
		self.use()
		self.focusOn(FakeNavigatorObject("a cell"))
		self.assertFalse(self.plugin.pictures.drawFocusedChart())


class PluginTestCase(unittest.TestCase):
	segmentCount = 4

	def makeHandler(self):
		""":return: the handler the plugin is built against. Overridden to change the display."""
		return FakeHandler(MONARCH_ROWS, MONARCH_COLS)

	def setUp(self):
		resetPluginState()
		CONFIG["segmentCount"] = self.segmentCount
		self.handler = self.makeHandler()
		self.originalBuffer = object()
		self.handler.mainBuffer = self.handler.buffer = self.originalBuffer
		braille.handler = self.handler
		self.plugin = GlobalPlugin()
		self.addCleanup(self.cleanUp)

	def cleanUp(self):
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		finally:
			patches.remove()
			resetPluginState()

	@property
	def container(self):
		return self.plugin.container

	def pin(self, segmentNumber, name="pinned thing"):
		"""Pin an object to a segment, as the per segment command does."""
		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject(name)
		self.plugin.startMonitoring(segmentNumber)


class TestInstallation(PluginTestCase):
	def test_aContainerIsInstalled(self):
		self.assertIsInstance(self.handler.mainBuffer, DisplayContainer)
		self.assertIs(self.handler.buffer, self.handler.mainBuffer)

	def test_theConfiguredLayoutIsUsed(self):
		self.assertEqual(self.container.numSegments, 4)

	def test_terminateHandsTheBufferBack(self):
		self.plugin.terminate()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		self.assertIs(self.handler.buffer, self.originalBuffer)

	def test_terminateIsRecorded(self):
		self.plugin.terminate()
		self.assertTrue(self.plugin._terminated)

	def test_aFailingTeardownStepDoesNotStopTheRest(self):
		import gui
		from brlMultiline import container as containerModule
		from brlMultiline.settingsPanel import VirtualDisplaySettingsPanel

		def fail():
			raise RuntimeError("a teardown step")

		self.plugin.graphicsMode.onTerminate = fail
		self.plugin.terminate()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		self.assertEqual(patches._installedMethods, {})
		self.assertIsNone(containerModule.segmentRouter)
		self.assertNotIn(VirtualDisplaySettingsPanel, gui.settingsDialogs.NVDASettingsDialog.categoryClasses)

	def test_aFailedStartTakesOutWhatWentIn(self):
		from unittest import mock

		import gui
		from brlMultiline import container as containerModule
		from brlMultiline import tableArrows

		self.plugin.terminate()
		with mock.patch.object(tableArrows, "install", side_effect=RuntimeError("install")):
			with self.assertRaises(RuntimeError):
				GlobalPlugin()
		self.assertEqual(patches._installedMethods, {})
		self.assertIsNone(containerModule.segmentRouter)
		self.assertEqual(gui.settingsDialogs.NVDASettingsDialog.categoryClasses, [])

	def failToStart(self, queueRebuild=False):
		""":return: a plugin whose first rebuild raised, after queueing another if asked."""
		from unittest import mock

		self.plugin.terminate()
		callAfterQueue.discard()
		started = []

		def rebuild(instance):
			started.append(instance)
			if queueRebuild:
				instance._scheduleRebuild()
			raise RuntimeError("rebuild")

		with mock.patch.object(GlobalPlugin, "rebuildBuffer", rebuild):
			with self.assertRaises(RuntimeError):
				GlobalPlugin()
		self.addCleanup(started[0].terminate)
		return started[0]

	def test_aFailedStartIsTerminatedAndGone(self):
		failed = self.failToStart()
		self.assertTrue(failed.isTerminated)
		self.assertIsNone(plugin.getPlugin())

	def test_aRebuildQueuedByAFailedStartDoesNothing(self):
		self.failToStart(queueRebuild=True)
		self.assertEqual(callAfterQueue.flush(), 1)
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)

	def test_anInstallerThatFailsPartWayIsUndone(self):
		from unittest import mock

		self.plugin.terminate()
		attributeFor = patches._attributeFor

		def failOnScrollForward(name):
			if name == "scrollForward":
				raise RuntimeError("attribute")
			return attributeFor(name)

		with mock.patch.object(patches, "_attributeFor", failOnScrollForward):
			with self.assertRaises(RuntimeError):
				GlobalPlugin()
		self.assertEqual(patches._installedMethods, {})

	def test_terminatingAnOldInstanceLeavesTheNewOneRegistered(self):
		self.plugin.terminate()
		newer = GlobalPlugin()
		self.addCleanup(newer.terminate)
		self.plugin.terminate()
		self.assertIs(plugin.getPlugin(), newer)


class TestMonitorCarryOver(PluginTestCase):
	segmentCount = 8

	def test_aPinSurvivesAnUnrelatedRebuild(self):
		self.pin(2)
		self.assertEqual(self.plugin.monitoredKeys, {"display.2"})
		self.plugin.rebuildBuffer()
		self.assertEqual(self.plugin.monitoredKeys, {"display.2"})

	def test_aPinSurvivesAClaimElsewhere(self):
		"""The whole point of composition: the claim did not touch the pinned segment."""
		self.pin(0)
		self.plugin.activatePanel(
			GridPanel(
				"table", SegmentRect(2, 0, 6, MONARCH_COLS), rowBands=[2, 2, 2], colWidths=[10, 10, 10],
			),
		)
		self.assertEqual(self.plugin.monitoredKeys, {"display.0"})
		self.assertTrue(self.container.hasKey("display.0"))

	def test_aPinIsReleasedWhenItsSegmentGoes(self):
		self.pin(5)
		self.plugin.activatePanel(
			SinglePanel("strip", SegmentRect(0, 0, 8, MONARCH_COLS), focusSegmentKey="strip"),
		)
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_aPinIsReleasedWhenAClaimTakesItsKey(self):
		"""A claim reusing the key would redraw over the pin, and the two would fight."""
		self.pin(1)
		self.plugin.activatePanel(SinglePanel("display.1", SegmentRect(1, 0, 1, MONARCH_COLS)))
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_aPinIsReleasedWhenItsSegmentBecomesTheFocus(self):
		"""The regression: settings moved the focus onto a segment that was already pinned.

		Keeping the pin would let `refreshMonitors` clear the freshly drawn focus content
		and write the pinned object over it.
		"""
		self.pin(2)
		self.assertEqual(self.plugin.monitoredKeys, {"display.2"})
		CONFIG["focusSegment"] = 2
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.focusSegmentKey, "display.2")
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_pinningTheFocusSegmentIsRefused(self):
		self.pin(self.container.focusSegmentNumber)
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertTrue(any("follows the focus" in message for message in spokenMessages))

	def test_pinningAClaimedSegmentIsRefused(self):
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 1, MONARCH_COLS)))
		self.pin(self.container.numberForKey("table"))
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertTrue(any("in use by" in message for message in spokenMessages))

	def test_monitoredSegmentsResolvesToCurrentIndices(self):
		self.pin(6)
		self.assertEqual(self.plugin.monitoredSegments, {6})
		# A claim above it renumbers the display without disturbing the pin.
		self.plugin.activatePanel(
			GridPanel("t", SegmentRect(0, 0, 4, MONARCH_COLS), rowBands=[2, 2], colWidths=[10, 10, 10]),
		)
		self.assertEqual(self.plugin.monitoredKeys, {"display.6"})
		self.assertEqual(self.plugin.monitoredSegments, {self.container.numberForKey("display.6")})

	def test_stopMonitoringClearsTheSegment(self):
		self.pin(3)
		self.container.segmentForKey("display.3").append(Region("stuff"))
		self.plugin.stopMonitoring(3)
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertEqual(self.container.segmentForKey("display.3").regions, [])


class TestDeferredRebuild(PluginTestCase):
	def test_aDisplayEventQueuesOneRebuild(self):
		displayChanged.notify()
		displaySizeChanged.notify()
		self.assertEqual(len(callAfterQueue.pending), 1)

	def test_theQueuedRebuildRuns(self):
		CONFIG["segmentCount"] = 6
		displayChanged.notify()
		callAfterQueue.flush()
		self.assertEqual(self.container.numSegments, 6)

	def test_aRebuildQueuedBeforeTerminationDoesNotRun(self):
		"""The race: the callback would install a container over the restored buffer."""
		displayChanged.notify()
		self.assertEqual(len(callAfterQueue.pending), 1)
		self.plugin.terminate()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		callAfterQueue.flush()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)

	def test_nothingIsQueuedAfterTermination(self):
		self.plugin.terminate()
		displayChanged.notify()
		self.assertEqual(callAfterQueue.pending, [])

	def test_terminationUnregistersTheHandlers(self):
		self.plugin.terminate()
		self.assertEqual(displayChanged.handlers, [])
		self.assertEqual(displaySizeChanged.handlers, [])
		self.assertEqual(config.post_configProfileSwitch.handlers, [])


class TestProfileSwitch(PluginTestCase):
	def test_aProfileSwitchQueuesARebuild(self):
		"""Settings live in config.conf, which is profile aware, so nothing else announces it."""
		self.assertEqual(len(post_configProfileSwitch.handlers), 1)
		post_configProfileSwitch.notify()
		self.assertEqual(len(callAfterQueue.pending), 1)

	def test_theNewProfilesLayoutIsApplied(self):
		self.assertEqual(self.container.numSegments, 4)
		CONFIG["segmentCount"] = 2
		post_configProfileSwitch.notify()
		callAfterQueue.flush()
		self.assertEqual(self.container.numSegments, 2)

	def test_aProfileSwitchAfterTerminationIsIgnored(self):
		self.plugin.terminate()
		post_configProfileSwitch.notify()
		self.assertEqual(callAfterQueue.pending, [])


class TestPanelClaims(PluginTestCase):
	segmentCount = 8

	def claim(self, name="table"):
		return GridPanel(
			name,
			SegmentRect(2, 0, 6, MONARCH_COLS),
			rowBands=[2, 2, 2],
			colWidths=[10, 10, 10],
		)

	def test_activatingAPanelClaimsItsCells(self):
		self.plugin.activatePanel(self.claim())
		self.assertEqual(len(self.container.reservedKeys), 9)
		self.assertEqual(self.container.focusSegmentKey, "table.r0c0")

	def test_untouchedSegmentsSurvive(self):
		self.plugin.activatePanel(self.claim())
		self.assertTrue(self.container.hasKey("display.0"))
		self.assertTrue(self.container.hasKey("display.1"))

	def test_aClaimSurvivesASettingsRebuild(self):
		"""Claims are re-composed each time rather than baked into a stored view."""
		self.plugin.activatePanel(self.claim())
		self.plugin.rebuildBuffer()
		self.assertTrue(self.container.hasKey("table.r0c0"))

	def test_deactivatingGivesTheCellsBack(self):
		self.plugin.activatePanel(self.claim())
		self.plugin.deactivatePanel("table")
		self.assertFalse(any(key.startswith("table.") for key in self.container._byKey))
		self.assertEqual(self.container.numSegments, 8)

	def test_aClaimOfTheSameNameReplacesIt(self):
		self.plugin.activatePanel(self.claim())
		self.plugin.activatePanel(self.claim())
		self.assertEqual(sum(1 for panel in self.container.panels if panel.name == "table"), 1)

	def test_aClaimThatCannotBeHonouredLeavesTheDisplayAlone(self):
		before = self.container
		with self.assertRaises(ValueError):
			self.plugin.activatePanel(SinglePanel("oops", SegmentRect(6, 0, 4, MONARCH_COLS)))
		self.assertIs(self.container, before)

	def test_aClaimTakingTheFocusWithoutAReplacementIsRefused(self):
		before = self.container
		with self.assertRaises(LookupError):
			self.plugin.activatePanel(SinglePanel("status", SegmentRect(7, 0, 1, MONARCH_COLS)))
		self.assertIs(self.container, before)

	def test_aClaimThatNoLongerFitsIsDropped(self):
		"""A display swap can leave a claim that cannot be placed on the new geometry."""
		self.plugin.activatePanel(self.claim())
		self.handler.displayDimensions.numRows = 4
		self.plugin.rebuildBuffer()
		self.assertFalse(any(key.startswith("table.") for key in self.container._byKey))
		self.assertEqual(self.plugin._activePanels, [])

	def test_restoreConfiguredViewDropsEveryClaim(self):
		self.plugin.activatePanel(self.claim())
		self.plugin.restoreConfiguredView()
		self.assertEqual(self.plugin._activePanels, [])
		self.assertEqual(self.container.numSegments, 8)


class FakeFlowBand:
	"""Enough of a flow band to exercise the plugin's three answers to a rebuild.

	The real one needs a document, a controller and a browse mode tree to say anything at
	all, none of which this is about: what is being tested is which of `onRebuilt`,
	`onSuspended` and `onEvicted` the plugin picks, and whether the band survives.
	"""

	def __init__(self, segment=object()):
		self._segment = segment
		self.rebuilt = False
		self.suspended = False
		self.evicted = False
		self.stopped = False

	def segment(self):
		return self._segment

	def onRebuilt(self, keys=frozenset()):
		self.rebuilt = True

	def onSuspended(self):
		self.suspended = True

	def onEvicted(self, keys=frozenset()):
		self.evicted = True

	def onTerminate(self):
		self.stopped = True

	def stop(self):
		self.stopped = True


class TestClaimPriority(PluginTestCase):
	"""A drawing outranks the flow, which outranks an ordinary claim.

	The flow is on most of the time — it is the add-on's default and it is wanted in Excel
	and on the web — so without an order, showing a drawing was a fight with it: whichever
	claim was made most recently took the rows, and a profile switch or a focus change
	could take a figure off the display mid-read.
	"""

	segmentCount = 8

	@contextlib.contextmanager
	def drawingUp(self):
		"""Pretend a figure is on the display, without needing one.

		The mode answers `active` from whether it holds a drawing, and every rule about
		priority keys on that one fact.
		"""
		self.plugin.graphicsMode._drawing = object()
		try:
			yield
		finally:
			self.plugin.graphicsMode._drawing = None

	def test_theHighestRankIsLaidLast(self):
		rect = SegmentRect(0, 0, 4, MONARCH_COLS)
		ordered = plugin._byPriority(
			[GraphicsPanel("g", rect), SinglePanel("s", rect), FlowPanel("f", rect)],
		)
		self.assertEqual([panel.name for panel in ordered], ["s", "f", "g"])

	def test_equalRanksKeepTheOrderTheyWereClaimedIn(self):
		rect = SegmentRect(0, 0, 4, MONARCH_COLS)
		ordered = plugin._byPriority([SinglePanel("first", rect), SinglePanel("second", rect)])
		self.assertEqual([panel.name for panel in ordered], ["first", "second"])

	def test_aDrawingKeepsItsRowsAgainstAClaimMadeAfterIt(self):
		"""The whole point. Before the ladder, the later claim simply took them.

		The latecomer takes rows 2 to 4 rather than the whole display, because a claim over the
		base view's own focus segment is refused whatever its rank — a rule that predates the
		ladder and is nothing to do with it.
		"""
		self.plugin.activatePanel(
			GraphicsPanel("graphics", SegmentRect(0, 0, 8, MONARCH_COLS), textRows=1),
		)
		self.plugin.activatePanel(SinglePanel("latecomer", SegmentRect(1, 0, 3, MONARCH_COLS)))
		self.assertTrue(self.container.hasKey("graphics.drawing"))
		self.assertFalse(self.container.hasKey("latecomer"))

	def test_anOrdinaryClaimStillWinsWhereNoDrawingWantsTheRows(self):
		"""The ladder must not turn into graphics owning the display permanently."""
		self.plugin.activatePanel(SinglePanel("mine", SegmentRect(0, 0, 4, MONARCH_COLS)))
		self.assertTrue(self.container.hasKey("mine"))

	def test_theFlowDoesNotClaimWhileADrawingIsUp(self):
		"""Letting it claim rows it is going to lose would cost a claim, an eviction and
		a teardown on every rebuild, and the flow is on for most rebuilds.
		"""
		self.assertFalse(self.plugin.graphicsMode.active)
		with self.drawingUp():
			self.plugin._applyFlow()
			self.assertIsNone(self.plugin.flowBand)

	def test_aFlowAlreadyRunningIsNotStoppedWhenADrawingArrives(self):
		"""Stopping it would take its controller, and the controller is where the
		reader's place in the document lives. Reported from hardware: the flow came back
		after a drawing and was at the top of the page rather than where they had got to.
		"""
		band = FakeFlowBand()
		self.plugin.flowBand = band
		try:
			with self.drawingUp():
				self.plugin._applyFlow()
				self.assertIs(self.plugin.flowBand, band)
				self.assertFalse(band.stopped)
		finally:
			self.plugin.flowBand = None

	def test_aBandWithoutItsRowsIsSuspendedWhileADrawingIsUp(self):
		band = FakeFlowBand(segment=None)
		self.plugin.flowBand = band
		try:
			with self.drawingUp():
				self.plugin._carryOverFlow(self.container)
			self.assertTrue(band.suspended)
			self.assertFalse(band.evicted)
			self.assertIs(self.plugin.flowBand, band)
		finally:
			self.plugin.flowBand = None

	def test_aBandWithoutItsRowsAndNoDrawingIsEvictedAsBefore(self):
		"""The suspension must not swallow a real eviction: rows gone with nothing to
		give them back means the flow is over.
		"""
		band = FakeFlowBand(segment=None)
		self.plugin.flowBand = band
		try:
			self.plugin._carryOverFlow(self.container)
			self.assertTrue(band.evicted)
			self.assertFalse(band.suspended)
			self.assertIsNone(self.plugin.flowBand)
		finally:
			self.plugin.flowBand = None

	def test_aBandThatKeptItsRowsIsSimplyRedrawn(self):
		band = FakeFlowBand()
		self.plugin.flowBand = band
		try:
			self.plugin._carryOverFlow(self.container)
			self.assertTrue(band.rebuilt)
			self.assertFalse(band.suspended)
			self.assertFalse(band.evicted)
		finally:
			self.plugin.flowBand = None


class TestLayoutReport(PluginTestCase):
	"""The report has to say who took the rows, not only how many are left.

	A reader whose display had stopped dividing was told "devices+flow view, 3 panels, 2
	segments". That contains the answer — the `+` means a claim was laid over the
	configured view, and the flow had taken the rows the segments were meant to occupy —
	but only to someone who knows how `SegmentView.withPanel` names a composed view. These
	tests are that the name and the rows are said outright.
	"""

	segmentCount = 8

	def report(self):
		":return: what the reader was told."
		spokenMessages.clear()
		self.plugin.script_reportLayout(None)
		return spokenMessages[-1]

	def claim(self):
		return GridPanel(
			"table",
			SegmentRect(2, 0, 6, MONARCH_COLS),
			rowBands=[2, 2, 2],
			colWidths=[10, 10, 10],
		)

	def test_theConfiguredViewSaysSoAndNamesNoClaim(self):
		message = self.report()
		self.assertIn("8 segments", message)
		self.assertNotIn("Claimed", message)

	def test_aClaimIsNamedWithTheRowsItHolds(self):
		"""The fact that was missing on hardware."""
		self.plugin.activatePanel(self.claim())
		message = self.report()
		self.assertIn("Claimed", message)
		self.assertIn("table", message)
		# Rows counted from 1, as the settings dialog counts them: a claim at row 2 spanning
		# 6 rows is rows 3 to 8.
		self.assertIn("rows 3 to 8", message)

	def test_theBreakdownNamesEveryPanelAndWhichOneWasClaimed(self):
		self.plugin.activatePanel(self.claim())
		lines = plugin._layoutLines(self.container, self.plugin._activePanels)
		text = "\n".join(lines)
		self.assertIn("claimed by code", text)
		self.assertIn("table", text)
		self.assertIn("focus segment:", text)
		# One line per panel and one per segment, so a display that has lost rows can be
		# read off rather than deduced.
		for panel in self.container.panels:
			self.assertIn(panel.name, text)
		for spec in self.container.specs:
			self.assertIn(spec.key, text)

	def test_theBreakdownSaysWhenNothingHasClaimedAnything(self):
		text = "\n".join(plugin._layoutLines(self.container, []))
		self.assertIn("no claims", text)

	def test_reservedAndFocusSegmentsAreMarked(self):
		self.plugin.activatePanel(self.claim())
		text = "\n".join(plugin._layoutLines(self.container, self.plugin._activePanels))
		self.assertIn("reserved by", text)
		self.assertIn("[focus", text)


class TestSpeechOutputMode(PluginTestCase):
	"""What happens to claimed content when NVDA shows speech in braille.

	Entering the mode is two things at once: NVDA's braille mode setting changes, and NVDA
	assigns a fresh list to `container.regions`, which empties every segment. That emptying
	is deliberate: in this mode NVDA stops handling focus, caret and review moves, so
	anything left elsewhere would sit under the reader's fingers with nothing to refresh or
	remove it. For the same reason the add-on draws nothing of its own while the mode is on.

	The gap is what happens on the way back. A pin's registration survives, but its content
	does not return until something redraws it, and nothing does so automatically; the tests
	below that leave the mode record that rather than desired behaviour. A panel owner has
	no recovery path at all, which is the strongest argument for giving panels a render
	callback.
	"""

	segmentCount = 8

	def enterSpeechMode(self):
		"""Do both halves of what NVDA does when the display starts showing speech."""
		setSpeechOutputMode(True)
		self.container.regions = [Region("spoken text")]

	def leaveSpeechMode(self):
		"""Only the mode changes: NVDA puts nothing of the add-on's back on the way out."""
		setSpeechOutputMode(False)

	def contentOf(self, key):
		return self.container.segmentForKey(key).regions

	def test_speechOutputEmptiesEverySegmentButTheFocus(self):
		self.pin(1)
		self.assertNotEqual(self.contentOf("display.1"), [])
		self.enterSpeechMode()
		self.assertEqual(self.contentOf("display.1"), [])
		self.assertEqual(len(self.container.focusSegment.regions), 1)

	def test_thePinRegistrationSurvivesSpeechOutput(self):
		self.pin(1)
		self.enterSpeechMode()
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})

	def test_nothingIsDrawnWhileTheDisplayShowsSpeech(self):
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.plugin.refreshMonitors()
		self.assertEqual(self.contentOf("display.1"), [])

	def test_aRebuildDuringSpeechOutputDoesNotResurrectAPin(self):
		"""The regression: a rebuild redrew every pin whatever the display was showing.

		NVDA draws no focus content in this mode, so the pin would arrive beside the speech
		on a display that nothing else is going to touch.
		"""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.plugin.rebuildBuffer()
		self.assertEqual(self.contentOf("display.1"), [])
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})

	def test_aProfileSwitchDuringSpeechOutputDoesNotResurrectAPin(self):
		"""The same rebuild, reached the way a user is most likely to reach it."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		post_configProfileSwitch.notify()
		callAfterQueue.flush()
		self.assertEqual(self.contentOf("display.1"), [])

	def test_pinningDuringSpeechOutputRegistersWithoutDrawing(self):
		self.enterSpeechMode()
		self.pin(1, name="watch me")
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})
		self.assertEqual(self.contentOf("display.1"), [])

	def test_pinnedContentDoesNotComeBackWhenSpeechOutputEnds(self):
		"""Documents the gap: leaving the mode redraws nothing on its own."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.leaveSpeechMode()
		self.assertEqual(self.contentOf("display.1"), [])

	def test_refreshMonitorsIsTheRecoveryPath(self):
		"""It exists and works; what is missing is anything that calls it at the right time."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.leaveSpeechMode()
		self.plugin.refreshMonitors()
		self.assertNotEqual(self.contentOf("display.1"), [])

	def test_aRebuildAfterSpeechOutputEndsRestoresThePin(self):
		"""Which is why the registration is kept while the mode refuses to draw it."""
		self.pin(1, name="watch me")
		self.enterSpeechMode()
		self.leaveSpeechMode()
		self.plugin.rebuildBuffer()
		self.assertNotEqual(self.contentOf("display.1"), [])

	def test_aClaimedPanelHasNoRecoveryPathAtAll(self):
		"""The panel keeps its cells, but nothing can put its content back."""
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 1, MONARCH_COLS)))
		self.container.segmentForKey("table").append(Region("owned content"))
		self.enterSpeechMode()
		self.leaveSpeechMode()
		self.assertTrue(self.container.hasKey("table"))
		self.assertEqual(self.contentOf("table"), [])


class TestDoNewObjectPatch(PluginTestCase):
	"""The installed replacement for `BrailleHandler._doNewObject`, driven end to end.

	This is the main path: every focus change NVDA reports arrives here, and it is where
	the targeting rules have to hold. Reached through the handler class rather than through
	the function, so that a patch which was never installed fails the test too.
	"""

	segmentCount = 8

	def doNewObject(self, *regions):
		BrailleHandler._doNewObject(self.handler, list(regions))

	def test_thePatchIsInstalled(self):
		self.assertIs(BrailleHandler._doNewObject, patches._doNewObjectMultiSegment)

	def test_untargetedRegionsGoToTheFocusSegment(self):
		region = Region("focus content")
		self.doNewObject(region)
		self.assertEqual(self.container.focusSegment.regions, [region])

	def test_aTargetedRegionGoesToItsOwnSegment(self):
		region = Region("pinned content")
		region.targetSegment = "display.1"
		self.doNewObject(region)
		self.assertEqual(self.container.segmentForKey("display.1").regions, [region])
		self.assertEqual(self.container.focusSegment.regions, [])

	def test_aRegionForADepartedSegmentIsDropped(self):
		"""The regression behind the targeting correction: it must not fall back to focus.

		Showing a departed panel's content in the focus segment would write it over the
		user's ordinary braille.
		"""
		region = Region("orphaned content")
		region.targetSegment = "table.r0c0"
		self.doNewObject(region)
		self.assertEqual(self.container.focusSegment.regions, [])
		self.assertFalse(any(segment.regions for segment in self.container.segments))

	def test_aSegmentReceivingNothingKeepsWhatItHad(self):
		"""What makes a pin survive a focus change without anything intervening."""
		kept = Region("pinned thing")
		self.container.segmentForKey("display.1").append(kept)
		self.doNewObject(Region("new focus content"))
		self.assertEqual(self.container.segmentForKey("display.1").regions, [kept])

	def test_aSegmentReceivingRegionsIsClearedFirst(self):
		self.container.focusSegment.append(Region("stale"))
		fresh = Region("fresh")
		self.doNewObject(fresh)
		self.assertEqual(self.container.focusSegment.regions, [fresh])

	def test_wherePlacementWentIsRecordedWithoutBecomingAClaim(self):
		region = Region("focus content")
		self.doNewObject(region)
		self.assertEqual(getattr(region, PLACEMENT_KEY_ATTRIBUTE), self.container.focusSegmentKey)
		self.assertIsNone(getattr(region, "targetSegment", None))

	def test_eachSegmentGetsItsOwnHardLeftAnchor(self):
		"""The rule is about a region's place in its own group, not on the display."""
		first = Region("focus content")
		second = Region("pinned content")
		second.targetSegment = "display.1"
		self.doNewObject(first, second)
		self.assertTrue(first.focusToHardLeft)
		self.assertTrue(second.focusToHardLeft)

	def test_theLastRegionOfEachGroupIsScrolledTo(self):
		"""Every segment that received content is scrolled, not only the focus segment."""
		first = Region("focus content")
		second = Region("pinned content")
		second.targetSegment = "display.1"
		self.doNewObject(first, second)
		self.assertCountEqual(self.handler.scrolledTo, [first, second])


class TestFlowSwitch(PluginTestCase):
	"""Claiming and giving back the flow band, from the setting and from the command.

	The setting is read on every rebuild, which is what makes it answer a configuration
	profile: NVDA switches profile when the foreground application changes, that rebuilds
	the display, and the rebuild is where the band is claimed or given back. Before this the
	flow was a command and nothing else, so a reader who wanted it had to press it again
	after every profile switch, display change and restart.
	"""

	segmentCount = 4

	def toggle(self):
		self.plugin.script_toggleFlow(None)

	def test_theBandIsNotClaimedUntilItIsAskedFor(self):
		"""Installing the add-on must not change how anything reads."""
		self.assertIsNone(self.plugin.flowBand)
		self.assertEqual(self.container.numSegments, 4)

	def test_aRebuildClaimsTheBandWhenTheSettingSaysSo(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		self.assertIsNotNone(self.plugin.flowBand)
		self.assertTrue(self.plugin.flowBand.isClaimed)

	def test_aProfileThatTurnsItOffGivesTheBandBack(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		CONFIG["flowEnabled"] = False
		post_configProfileSwitch.notify()
		callAfterQueue.flush()
		self.assertIsNone(self.plugin.flowBand)
		self.assertEqual(self.container.numSegments, 4)

	def test_theBandIsClaimedEvenWithNothingToFlowHere(self):
		"""It presents the focus as an undivided display would, and lights up at a document.

		Giving it back would mean asking for it again after every dialog.
		"""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		self.assertTrue(self.plugin.flowBand.isClaimed)
		self.assertFalse(self.plugin.flowBand.isShowing)

	def test_theCommandRemembersItsAnswer(self):
		self.toggle()
		self.assertTrue(self.plugin.flowBand.isClaimed)
		from brlMultiline import bmConfig

		self.assertTrue(bmConfig.isFlowEnabled())

	def test_theCommandTurnsItOffAgain(self):
		self.toggle()
		self.toggle()
		self.assertIsNone(self.plugin.flowBand)
		self.assertEqual(self.container.numSegments, 4)

	def test_theCommandSaysWhichWayItWent(self):
		self.toggle()
		self.assertIn("Flow on", spokenMessages)
		self.toggle()
		self.assertIn("Flow off", spokenMessages)

	def test_turningItOnWithNothingSetToFlowSaysSo(self):
		"""A band that could never show anything is not claimed, and the reader is told why."""
		CONFIG["flowBrowseMode"] = False
		self.toggle()
		self.assertIsNone(self.plugin.flowBand)
		self.assertTrue(any("nothing is set to flow" in message for message in spokenMessages))

	def test_claimingTheBandDoesNotClaimItAgain(self):
		"""Claiming rebuilds the display, and the rebuild is where claiming happens."""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		first = self.plugin.flowBand
		self.plugin.rebuildBuffer()
		self.assertIs(self.plugin.flowBand, first)

	def test_anInactiveBandStillFollowsFocusAfterARebuild(self):
		"""A new segment needs the owner's callback even when nothing was flowing yet."""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		band = self.plugin.flowBand
		self.assertIsNone(band.controller)
		self.plugin.rebuildBuffer()
		segment = band.segment()
		self.assertIsNotNone(segment.onFocusRegions)
		page = FakeTreeInterceptor(["a page"])
		self.assertTrue(segment.acceptFocusRegions(fakeGetFocusRegions(page)))
		self.assertTrue(band.isShowing)

	def test_changingRowsReclaimsAnEnabledBand(self):
		"""Re-composing a panel preserves its old rectangle; settings must replace it."""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		first = self.plugin.flowBand
		self.assertEqual(first.segment().rect.numRows, MONARCH_ROWS)
		CONFIG["flowRows"] = 3
		self.plugin.rebuildBuffer()
		self.assertIsNot(self.plugin.flowBand, first)
		self.assertEqual(self.plugin.flowBand.segment().rect.numRows, 3)

	def test_anEnabledProfileMayChangeTheBandsRows(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		CONFIG["flowRows"] = 2
		post_configProfileSwitch.notify()
		callAfterQueue.flush()
		self.assertEqual(self.plugin.flowBand.segment().rect.numRows, 2)

	def test_terminatingWithABandIsClean(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		self.plugin.terminate()
		self.assertIsNone(self.plugin.flowBand)


class TestFlowTargetSwitch(PluginTestCase):
	"""An enabled flow moves when its chosen physical display changes."""

	def makeHandler(self):
		handler = FakeHandler(12, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 4, 80),
		)
		return handler

	def test_changingTheDisplayReclaimsTheBandThere(self):
		CONFIG["flowEnabled"] = True
		CONFIG["flowDisplay"] = "hidBrailleStandard"
		self.plugin.rebuildBuffer()
		first = self.plugin.flowBand
		self.assertEqual(first.segment().rect, SegmentRect(0, 0, 8, 32))
		CONFIG["flowDisplay"] = "freedomScientific"
		self.plugin.rebuildBuffer()
		self.assertIsNot(self.plugin.flowBand, first)
		self.assertEqual(self.plugin.flowBand.segment().rect, SegmentRect(8, 0, 4, 80))


class TestASingleRowDisplayIsLeftToNVDA(PluginTestCase):
	"""Everything a flow is for needs a second row to exist at all, and on one row it takes a
	display NVDA was already doing the same job on — and adds a focus mark on every line and
	an indent whose shape cannot be seen. The reader had this by accident until the band began
	following the focus: it used to take the tallest display and never landed on one row."""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def test_theBandIsNotClaimedThere(self):
		CONFIG["flowEnabled"] = True
		CONFIG["flowDisplay"] = "freedomScientific"
		self.plugin.rebuildBuffer()
		self.assertIsNone(self.plugin.flowBand)

	def test_aTallerDisplayStillGetsOne(self):
		CONFIG["flowEnabled"] = True
		CONFIG["flowDisplay"] = "hidBrailleStandard"
		self.plugin.rebuildBuffer()
		self.assertIsNotNone(self.plugin.flowBand)


class TestFlowCostCommand(PluginTestCase):
	"""What a reader on a heavy page can press instead of saying "it felt slow"."""

	def test_itSaysSoWhenThereIsNoFlow(self):
		self.plugin.script_flowCost(None)
		self.assertIn("No flow is showing", spokenMessages)

	def test_itReportsTheNumbersWhenThereIs(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		page = FakeNavigatorObject("a page", treeInterceptor=FakeTreeInterceptor(["a line"]))
		import api

		self.addCleanup(setattr, api, "getFocusObject", api.getFocusObject)
		api.getFocusObject = lambda: page
		self.plugin.flowBand.refresh(force=True)
		self.plugin.script_flowCost(None)
		self.assertTrue(
			any("operations" in message for message in spokenMessages),
			spokenMessages,
		)


class TestSegmentsSwitch(PluginTestCase):
	"""Turning the configured layout off and on, from the command and from the settings.

	The switch is over the user's own layout. A view or a panel activated by code says what
	the display is being used for rather than what the user prefers, so it is not overridden;
	the alternative would be a table reader silently losing its grid to a setting.
	"""

	segmentCount = 4

	def toggle(self):
		self.plugin.script_toggleSegments(None)

	def test_theCommandUndividesTheDisplay(self):
		self.assertEqual(self.container.numSegments, 4)
		self.toggle()
		self.assertEqual(self.container.numSegments, 1)

	def test_theCommandDividesItAgain(self):
		self.toggle()
		self.toggle()
		self.assertEqual(self.container.numSegments, 4)

	def test_theCommandSaysWhichWayItWent(self):
		self.toggle()
		self.assertIn("Segments off", spokenMessages)
		self.toggle()
		self.assertIn("Segments on", spokenMessages)

	def test_theLayoutIsNotDisturbed(self):
		CONFIG["segmentSizes"] = [1, 5, 2]
		self.plugin.rebuildBuffer()
		self.toggle()
		self.toggle()
		self.assertEqual(self.container.numSegments, 3)

	def test_pinsAreReleasedWhenTheDisplayIsUndivided(self):
		"""There is one segment and it follows the focus, so no pin can be kept."""
		self.pin(1)
		self.assertEqual(self.plugin.monitoredKeys, {"display.1"})
		self.toggle()
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_pinningIsRefusedWhileUndivided(self):
		self.toggle()
		spokenMessages.clear()
		self.pin(0)
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertTrue(any("not divided into segments" in message for message in spokenMessages))

	def test_anActivatedViewIsNotOverridden(self):
		"""A claim by code is not a preference, so the user's switch does not answer it."""
		self.toggle()
		self.plugin.activateView(
			SegmentView(
				"table",
				[
					SinglePanel("top", SegmentRect(0, 0, 4, MONARCH_COLS)),
					SinglePanel("bottom", SegmentRect(4, 0, 4, MONARCH_COLS)),
				],
			),
		)
		self.assertEqual(self.container.numSegments, 2)

	def test_anActivatedPanelIsComposedOverTheUndividedDisplay(self):
		self.toggle()
		self.plugin.activatePanel(
			SinglePanel("table", SegmentRect(0, 0, 2, MONARCH_COLS), focusSegmentKey="table"),
		)
		self.assertTrue(self.container.hasKey("table"))

	def test_aClaimOnTheUndividedDisplayMustOfferAFocusSegment(self):
		"""The one segment holds the focus, so every claim touching it evicts the focus.

		Divided, a claim over the top rows leaves the focus segment further down alone and
		need offer nothing. Undivided there is nowhere else for it to be, so the rule that
		applies to any claim evicting the focus applies to every claim.
		"""
		self.toggle()
		with self.assertRaises(LookupError):
			self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 2, MONARCH_COLS)))

	def test_aClaimThatCannotSurviveUndividingIsDropped(self):
		"""Reported rather than raised: the rebuild was asked for by a setting, not a claim."""
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 2, MONARCH_COLS)))
		self.assertTrue(self.container.hasKey("table"))
		self.toggle()
		self.assertFalse(self.container.hasKey("table"))
		self.assertTrue(any("cannot be shown" in message for level, message in log.messages))


class TestNativePanning(PluginTestCase):
	"""NVDA's own panning keys, on a Monarch above a Focus 80.

	The Monarch is divided in two and the Focus is whole, so segments 0 and 1 are on the
	Monarch and segment 2 is on the Focus.
	"""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.rebuild()

	def rebuild(self):
		"""Rebuild and give every segment more content than it can show at once.

		The largest segment here is four rows of 32, so the text has to be longer than 128
		cells or a segment simply has nowhere to pan to and the test proves nothing.
		"""
		self.plugin.rebuildBuffer()
		self.handler.buffer = self.handler.mainBuffer
		for segment in self.container.segments:
			segment.append(Region("word " * 80))
		self.container.update()

	def pan(self, source, forward=True):
		"""Press a display's own panning key.

		The display is put in place around the call, which is what the wrapper around NVDA's
		panning command does for as long as the command runs. `test_panning` covers the wrapper
		and every way a gesture can fail to reach one; this covers what the handler does once a
		display is known.
		"""
		from brlMultiline import panning

		panning._activeSource = source
		try:
			if forward:
				BrailleHandler.scrollForward(self.handler)
			else:
				BrailleHandler.scrollBack(self.handler)
		finally:
			panning._activeSource = None

	def movedSegments(self):
		"""Which segments panned.

		Two ways to tell, because the container pans them two ways: the focus segment scrolls
		as an ordinary buffer does, and any other segment moves its own window instead, so
		that panning something the user is only reading cannot drag the caret.
		"""
		return [
			index
			for index, segment in enumerate(self.container.segments)
			if segment.scrolled is not None or segment.windowStartPos != 0
		]

	def test_aDisplaysKeysDriveItsOwnSegment(self):
		"""The hardware bug: the Monarch's keys were scrolling the Focus."""
		self.pan("hidBrailleStandard")
		self.assertEqual(self.movedSegments(), [0])

	def test_theOtherDisplaysKeysDriveTheFocusSegment(self):
		self.pan("freedomScientific")
		self.assertEqual(self.movedSegments(), [2])

	def test_aDisplayHoldingTheFocusDrivesTheFocusSegment(self):
		CONFIG["focusSegment"] = 1
		self.rebuild()
		self.pan("hidBrailleStandard")
		self.assertEqual(self.movedSegments(), [1])

	def test_scrollingWithNoKeyPressBehindItIsUnchanged(self):
		"""Automatic scroll, which must go on driving the focus segment."""
		self.pan(None)
		self.assertEqual(self.movedSegments(), [2])

	def test_theDirectionIsThePressedDisplays(self):
		"""The focus on the Monarch, so the direction is observable as a scroll either way."""
		CONFIG["focusSegment"] = 0
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2, reverseScrollBtns=True)
		self.rebuild()
		self.pan("hidBrailleStandard", forward=True)
		self.assertEqual(self.container.segments[0].scrolled, "back")

	def test_theOtherDisplayKeepsItsOwnDirection(self):
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2, reverseScrollBtns=True)
		self.rebuild()
		self.pan("freedomScientific", forward=True)
		self.assertEqual(self.container.segments[2].scrolled, "forward")

	def test_onePressDrivesOneScroll(self):
		"""The display lasts only as long as its command, so the next scroll starts clean."""
		self.pan("hidBrailleStandard")
		self.assertEqual(self.movedSegments(), [0])
		self.pan(None)
		self.assertEqual(self.movedSegments(), [0, 2])


class TestDisplayRelativeCommands(PluginTestCase):
	"""Commands that name a segment by which display it is on."""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.plugin.rebuildBuffer()

	def test_theSecondDisplaysFirstSegmentIsResolved(self):
		self.assertEqual(self.plugin.displaySegmentNumber(1, 0), 2)

	def test_theFirstDisplaysSegmentsAreResolved(self):
		self.assertEqual(self.plugin.displaySegmentNumber(0, 0), 0)
		self.assertEqual(self.plugin.displaySegmentNumber(0, 1), 1)

	def test_theBindingSurvivesARearrangement(self):
		"""What the whole family is for: the index moved and the command did not."""
		self.assertEqual(self.plugin.displaySegmentNumber(1, 0), 2)
		setBandConfig("hidBrailleStandard_8x32", segmentCount=4)
		self.plugin.rebuildBuffer()
		self.assertEqual(self.plugin.displaySegmentNumber(1, 0), 4)

	def test_aSegmentThatIsNotThereIsReported(self):
		self.assertIsNone(self.plugin.displaySegmentNumber(1, 3))
		self.assertTrue(any("No segment" in message for message in spokenMessages), spokenMessages)

	def test_theScrollScriptsExist(self):
		self.assertTrue(hasattr(self.plugin, "script_scrollDisplay1Segment0Forward"))
		self.assertTrue(hasattr(self.plugin, "script_scrollDisplay2Segment3Back"))

	def test_theMonitorScriptsExist(self):
		self.assertTrue(hasattr(self.plugin, "script_monitorObjectInDisplay1Segment0"))
		self.assertTrue(hasattr(self.plugin, "script_stopMonitoringDisplay0Segment1"))

	def test_aScrollScriptScrollsTheRightSegment(self):
		for segment in self.container.segments:
			segment.append(Region("some words to pan through"))
		self.container.update()
		self.plugin.script_scrollDisplay1Segment0Forward(None)
		self.assertEqual(self.container.segments[2].scrolled, "forward")

	def test_aMonitorScriptPinsTheRightSegment(self):
		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject("a pinned thing")
		self.plugin.script_monitorObjectInDisplay0Segment0(None)
		self.assertIn("device.hidBrailleStandard.0", self.plugin.monitoredKeys)

	def test_aMonitorScriptOnAMissingSegmentPinsNothing(self):
		import api

		api.getNavigatorObject = lambda: FakeNavigatorObject("a pinned thing")
		self.plugin.script_monitorObjectInDisplay1Segment2(None)
		self.assertEqual(self.plugin.monitoredKeys, set())


class TestPatchOwnership(PluginTestCase):
	"""`BrailleHandler` is a class every add-on can reach, and several of them do.

	Installing over someone else's method and restoring over it are the two ways a monkey patch
	damages something other than itself. The second is the worse one, because it happens while
	this add-on is being disabled and looks like the other add-on breaking.
	"""

	def restore(self, name, original):
		setattr(patches._owners.get(name, BrailleHandler), name, original)
		patches._originals.pop(name, None)
		patches._installedMethods.pop(name, None)
		patches._owners.pop(name, None)

	def withBand(self, onChange):
		"""Put a stand-in band on the plugin, so the patch has somewhere to deliver to."""
		import brlMultiline

		class Band:
			def documentChanged(self, document):
				onChange(document)

		plugin = brlMultiline.getPlugin()
		if plugin is None:
			plugin = types.SimpleNamespace(flowBand=None)
			brlMultiline._plugin = plugin
			self.addCleanup(setattr, brlMultiline, "_plugin", None)
		previous = getattr(plugin, "flowBand", None)
		plugin.flowBand = Band()
		self.addCleanup(setattr, plugin, "flowBand", previous)

	def somebodyElseTakesOver(self, name):
		"""Another add-on replaces one of the patched methods after this one did."""

		def theirs(handler, *args, **kwargs):
			pass

		owner = patches._owners.get(name, BrailleHandler)
		self.addCleanup(self.restore, name, patches._originals[name])
		setattr(owner, name, theirs)
		return theirs

	def test_theDocumentChangePatchTellsTheBand(self):
		"""The signal that replaced a clock: NVDA's virtual buffer says when its content moved,
		for any accessibility event its backend acted on and not only for a live region."""
		import virtualBuffers

		told = []
		buffer = virtualBuffers.VirtualBuffer()
		self.withBand(lambda document: told.append(document))
		buffer._handleUpdate()
		self.assertEqual(told, [buffer])

	def test_nvdaStillGetsItsOwnUpdate(self):
		"""A failure of ours must not cost NVDA the update it was told about."""
		import virtualBuffers

		buffer = virtualBuffers.VirtualBuffer()
		self.withBand(lambda document: (_ for _ in ()).throw(RuntimeError("no")))
		buffer._handleUpdate()
		self.assertEqual(buffer.updates, 1)

	def test_noBandIsNotAnError(self):
		import virtualBuffers

		buffer = virtualBuffers.VirtualBuffer()
		buffer._handleUpdate()
		self.assertEqual(buffer.updates, 1)

	def test_liveUpdatesReportThemselvesAsInstalled(self):
		self.assertTrue(patches.liveUpdatesInstalled())

	def test_aDocumentOfThePatchedKindIsCovered(self):
		import virtualBuffers

		self.assertTrue(patches.liveUpdatesInstalled(virtualBuffers.VirtualBuffer()))

	def test_aDocumentOfAnotherKindIsNot(self):
		"""Only `VirtualBuffer` is patched, and it is not the only kind of browse mode there
		is. Saying yes for a UIA document would cost that reader their updates and say nothing
		about it, because the band polls only when the answer is no."""
		self.assertFalse(patches.liveUpdatesInstalled(object()))

	def test_aMethodThatIsNoLongerOursIsNotCovered(self):
		"""Another add-on may have replaced it since, and one that replaced rather than
		wrapped it does not delegate. Being in `_originals` only says this module once put
		something there."""
		self.somebodyElseTakesOver("_handleUpdate")
		self.assertFalse(patches.liveUpdatesInstalled())

	def test_everyPatchIsInstalled(self):
		for name, (owner, replacement) in patches._replacements().items():
			self.assertIs(getattr(owner, patches._attributeFor(name)), replacement)

	def test_anEntryMayPatchAnAttributeOfAnotherName(self):
		"""Two classes can have a method of the same name, and one of them is `update`, which
		is about as common a name as there is. The key stays unique and says what it patches.
		"""
		self.assertEqual(patches._attributeFor("textInfoUpdate"), "update")
		self.assertEqual(patches._attributeFor("scrollForward"), "scrollForward")

	def test_theyAreNotAllOnTheSameClass(self):
		"""The document change patch is on NVDA's virtual buffer, not on its braille handler,
		which is what made the owner part of what is remembered."""
		owners = {owner for owner, _replacement in patches._replacements().values()}
		self.assertGreater(len(owners), 1)

	def test_removingPutsNVDAsOwnMethodsBack(self):
		originals = dict(patches._originals)
		patches.remove()
		for name, original in originals.items():
			owner = patches._replacements()[name][0]
			self.assertIs(getattr(owner, patches._attributeFor(name)), original)

	def test_removingLeavesAnotherAddOnsMethodAlone(self):
		theirs = self.somebodyElseTakesOver("scrollForward")
		patches.remove()
		self.assertIs(BrailleHandler.scrollForward, theirs)

	def test_theOtherPatchesAreStillTakenBack(self):
		"""One method being someone else's is no reason to leave the rest patched."""
		original = patches._originals["scrollBack"]
		self.somebodyElseTakesOver("scrollForward")
		patches.remove()
		self.assertIs(BrailleHandler.scrollBack, original)

	def test_aLaterInstallDoesNotStackOnTopOfTheirs(self):
		"""And this is the one that would hang NVDA rather than merely misbehave.

		If they wrapped this add-on's method rather than replacing it, installing again would
		put this one over a wrapper that calls it, and the two would call each other until the
		stack ran out.
		"""
		theirs = self.somebodyElseTakesOver("scrollForward")
		patches.remove()
		patches.install()
		self.assertIs(BrailleHandler.scrollForward, theirs)

	def test_theRestAreInstalledAgainAfterwards(self):
		self.somebodyElseTakesOver("scrollForward")
		patches.remove()
		patches.install()
		self.assertIs(BrailleHandler.scrollBack, patches._scrollBackMaybeReversed)


class TestLosingADisplay(PluginTestCase):
	"""What the reader is left with when one of two displays goes.

	The rule, in the author's words: the focus has to end up on a display that is still there.
	If what is left has room, recover what the lost display was showing into it. If what is
	left is a single segment, do not — that segment follows the focus, and taking it for a
	pinned object would leave the reader with neither.
	"""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.plugin.rebuildBuffer()

	def loseTheMonarch(self, focusSegments=1):
		"""Let the Monarch go, as the driver's own `_relayout` leaves things.

		Three parts, and all three are the driver's doing: the slot is marked failed, the
		display that is left moves up to row 0, and the composite is one row of 80.

		:param focusSegments: how many segments the Focus is divided into afterwards.
		"""
		self.handler.display.slots[0].failed = True
		self.handler.display.slots[1].band.rowStart = 0
		self.handler.displayDimensions.numRows = 1
		self.handler.displayDimensions.numCols = 80
		setBandConfig("freedomScientific_1x80", segmentCount=focusSegments)
		self.plugin.rebuildBuffer()

	def test_whatIsLeftIsArrangedByItself(self):
		self.loseTheMonarch()
		self.assertEqual(self.container.numSegments, 1)
		self.assertEqual(self.container.rects[0], SegmentRect(row=0, col=0, numRows=1, numCols=80))

	def test_theFocusIsOnTheDisplayThatIsStillThere(self):
		self.loseTheMonarch()
		self.assertEqual(self.container.focusSegmentKey, "device.freedomScientific.0")

	def test_aPinOnTheLostDisplayMovesToASegmentThatIsFree(self):
		"""The Focus in two: one follows the focus, and the other can take the orphan."""
		self.pin(0)
		self.assertEqual(self.plugin.monitoredKeys, {"device.hidBrailleStandard.0"})
		self.loseTheMonarch(focusSegments=2)
		self.assertEqual(self.plugin.monitoredKeys, {"device.freedomScientific.0"})

	def test_aPinIsReleasedWhenTheOneSegmentLeftFollowsTheFocus(self):
		"""Taking it would leave the reader with something they did not ask to be reading."""
		self.pin(0)
		self.loseTheMonarch(focusSegments=1)
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_theMovedPinActuallyDrawsInItsNewHome(self):
		"""Filing it under a new key moves nothing.

		The monitor looks its segment up by the key it holds, and the regions it has already
		built carry `targetSegment`, which is what decides where a region is placed. A pin
		moved by the dictionary alone looks moved and draws nowhere.
		"""
		self.pin(0)
		monitor = self.plugin._monitors["device.hidBrailleStandard.0"]
		self.plugin.refreshMonitors()
		self.loseTheMonarch(focusSegments=2)
		self.assertEqual(monitor.segmentKey, "device.freedomScientific.0")
		self.assertTrue(monitor.regions)
		for region in monitor.regions:
			self.assertEqual(region.targetSegment, "device.freedomScientific.0")
		self.plugin.refreshMonitors()
		self.assertEqual(
			self.container.segmentForKey("device.freedomScientific.0").regions,
			monitor.regions,
		)

	def test_theOldSegmentIsNotLeftHoldingIt(self):
		"""There is no old segment; what matters is that nothing else claims to hold the pin."""
		self.pin(0)
		self.plugin.refreshMonitors()
		self.loseTheMonarch(focusSegments=2)
		self.plugin.refreshMonitors()
		monitor = self.plugin._monitors["device.freedomScientific.0"]
		for index, segment in enumerate(self.container.segments):
			if self.container.specs[index].key == "device.freedomScientific.0":
				continue
			self.assertNotEqual(segment.regions, monitor.regions)

	def test_theMoveIsReported(self):
		self.pin(0)
		self.loseTheMonarch(focusSegments=2)
		self.assertTrue(
			any("has gone" in message for level, message in log.messages if level == "info"),
			log.messages,
		)

	def test_twoPinsCompeteForOneFreeSegment(self):
		"""Only one can have it, and the other is released rather than sharing."""
		setBandConfig("hidBrailleStandard_8x32", segmentCount=3)
		self.plugin.rebuildBuffer()
		self.pin(0)
		self.pin(1)
		self.loseTheMonarch(focusSegments=2)
		self.assertEqual(len(self.plugin.monitoredKeys), 1)

	def test_aPinOnTheSurvivingDisplayIsNotDisturbed(self):
		"""It has not lost anything, so nothing about it should move.

		The Focus in two throughout: its second segment follows the focus, and its first is
		pinned before and after.
		"""
		setBandConfig("freedomScientific_1x80", segmentCount=2)
		self.plugin.rebuildBuffer()
		self.pin(2)
		self.assertEqual(self.plugin.monitoredKeys, {"device.freedomScientific.0"})
		self.loseTheMonarch(focusSegments=2)
		self.assertEqual(self.plugin.monitoredKeys, {"device.freedomScientific.0"})

	def test_nothingIsLaidOverTheDisplayThatHasGone(self):
		self.loseTheMonarch()
		for rect in self.container.rects:
			self.assertLess(rect.row, 1, f"{rect} is on rows the display no longer has")


class TestMembersChangedSubscription(PluginTestCase):
	"""That the plugin hears the composite's own notification, through the real one.

	Worth a class of its own because the driver tests prove the event is raised and the plugin
	tests proved a rebuild happens when *something* fires — and neither shows the two are the
	same event. Replacing the lookup with None left every plugin test passing.
	"""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def membersChanged(self):
		"""The canonical action, imported the way the plugin imports it."""
		from brailleDisplayDrivers.brlMultilineVirtual.events import membersChanged

		return membersChanged

	def test_thePluginIsSubscribedToTheCanonicalAction(self):
		self.assertIn(self.plugin._handleDisplayChanged, self.membersChanged().handlers)

	def test_theMembersChangingRebuildsFromTheNewDeviceMap(self):
		"""The same size case: nothing about the display's dimensions has changed."""
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.numSegments, 3)
		# One display goes, and the driver lays the rest out again before it says so.
		self.handler.display.slots[0].failed = True
		self.handler.display.slots[1].band.rowStart = 0
		self.handler.displayDimensions.numRows = 1
		self.membersChanged().notify(display=self.handler.display)
		callAfterQueue.flush()
		self.assertEqual(self.container.numSegments, 1)
		self.assertTrue(self.container.hasKey("device.freedomScientific.0"))

	def test_nothingHappensAfterThePluginHasGone(self):
		self.plugin.terminate()
		self.assertNotIn(self.plugin._handleDisplayChanged, self.membersChanged().handlers)
		self.membersChanged().notify(display=self.handler.display)
		self.assertEqual(callAfterQueue.pending, [])
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)


class NvdaMessageBuffer:
	"""A stand-in for the message buffer NVDA builds for itself, with nothing of ours in it."""

	def __init__(self):
		self.cleared = False

	def clear(self):
		self.cleared = True


class TestMessageBuffer(PluginTestCase):
	"""Flash messages go into a segment rather than across the whole display."""

	segmentCount = 4

	def messageBuffer(self):
		return self.handler.messageBuffer

	def test_nvdasOwnMessageBufferIsReplaced(self):
		self.assertIsInstance(self.messageBuffer(), MessageBuffer)

	def test_messagesFollowTheFocusSegmentByDefault(self):
		"""Which is where a message lands on an undivided display."""
		self.assertEqual(self.messageBuffer().rect, self.container.focusSegment.rect)

	def test_aChosenSegmentIsUsedInstead(self):
		CONFIG["messageSegment"] = 0
		self.plugin.rebuildBuffer()
		self.assertEqual(self.messageBuffer().rect, self.container.segments[0].rect)

	def test_aSegmentThatDoesNotExistFallsBackToTheFocus(self):
		CONFIG["messageSegment"] = 7
		self.plugin.rebuildBuffer()
		self.assertEqual(self.messageBuffer().rect, self.container.focusSegment.rect)
		self.assertTrue(
			any("for messages" in message for level, message in log.messages),
			log.messages,
		)

	def test_theSameObjectIsKeptAcrossRebuilds(self):
		"""The handler decides a message is showing by comparing identity with this object."""
		before = self.messageBuffer()
		CONFIG["messageSegment"] = 1
		self.plugin.rebuildBuffer()
		self.assertIs(self.messageBuffer(), before)
		self.assertEqual(self.messageBuffer().rect, self.container.segments[1].rect)

	def test_aShowingMessageSurvivesARebuild(self):
		"""Replacing the object under a live message would leave one nothing could dismiss."""
		self.handler.buffer = self.messageBuffer()
		self.plugin.rebuildBuffer()
		self.assertIs(self.handler.buffer, self.handler.messageBuffer)

	def test_terminateGivesNVDAItsOwnBufferBack(self):
		original = self.plugin._originalMessageBuffer
		self.plugin.terminate()
		self.assertIs(self.handler.messageBuffer, original)

	def showAMessage(self):
		"""Put a message up, as `BrailleHandler.message` does, timeout and all."""
		buffer = self.messageBuffer()
		buffer.clear()
		region = Region("a message")
		region.update()
		buffer.regions.append(region)
		buffer.update()
		self.handler.buffer = buffer
		self.handler._messageCallLater = FakeCallLater()
		return buffer

	def test_terminateWhileAMessageIsShowingHandsEveryBufferBack(self):
		"""Both buffers, and the display showing the one NVDA expects to be showing.

		The message cannot come with us: its regions are in a buffer that stops being the
		handler's. So it is dismissed the way NVDA dismisses one, which is the part that was
		missing — leaving the display on an emptied buffer that was no longer anybody's, with
		nothing scheduled to move it on if messages are shown indefinitely.
		"""
		originalMessageBuffer = self.plugin._originalMessageBuffer
		self.showAMessage()
		self.plugin.terminate()
		self.assertIs(self.handler.messageBuffer, originalMessageBuffer)
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		self.assertIs(self.handler.buffer, self.handler.mainBuffer)

	def test_terminateWhileAMessageIsShowingStopsItsTimeout(self):
		"""Left running it fires into `_dismissMessage`, which would clear the main buffer."""
		self.showAMessage()
		timer = self.handler._messageCallLater
		self.plugin.terminate()
		self.assertTrue(timer.stopped)
		self.assertIsNone(self.handler._messageCallLater)

	def test_theDisplayIsRedrawnAfterAMessageIsHandedBack(self):
		"""So that braille shows something again without waiting for the next event."""
		self.showAMessage()
		self.handler.gainedFocus.clear()
		self.plugin.terminate()
		self.assertTrue(self.handler.gainedFocus)

	def test_aMessageInAReplacedBufferIsDismissedRatherThanLeftShowing(self):
		"""Something else has put its own message buffer there — another add-on, most likely.

		Not NVDA: `messageBuffer` is assigned once, in `BrailleHandler.__init__`, so a rebuilt
		one arrives with a whole new handler. The message in the replacement belongs to it, so
		taking over cannot carry it along; what it must not do is leave the display on a buffer
		nothing will write to again.
		"""
		replacement = NvdaMessageBuffer()
		self.handler.messageBuffer = self.handler.buffer = replacement
		timer = self.handler._messageCallLater = FakeCallLater()
		self.plugin.rebuildBuffer()
		self.assertTrue(replacement.cleared)
		self.assertIs(self.handler.messageBuffer, self.plugin._messageBuffer)
		self.assertIs(self.handler.buffer, self.handler.mainBuffer)
		self.assertTrue(timer.stopped)

	def test_aReplacedBufferIsTheOneGivenBack(self):
		"""Whatever was there when we took over is what termination owes, not what was there
		at startup. Restoring the older one would throw away another add-on's buffer."""
		replacement = NvdaMessageBuffer()
		self.handler.messageBuffer = replacement
		self.plugin.rebuildBuffer()
		self.plugin.terminate()
		self.assertIs(self.handler.messageBuffer, replacement)

	def test_losingTheDisplayGivesNVDAItsOwnBufferBack(self):
		original = self.plugin._originalMessageBuffer
		self.handler.displayDimensions.numRows = 0
		self.handler.displayDimensions.numCols = 0
		self.plugin.rebuildBuffer()
		self.assertIs(self.handler.messageBuffer, original)


class TestNoDisplay(PluginTestCase):
	def test_aRebuildWithNoDisplayHandsTheBufferBack(self):
		self.handler.displayDimensions.numRows = 0
		self.handler.displayDimensions.numCols = 0
		self.plugin.rebuildBuffer()
		self.assertIs(self.handler.mainBuffer, self.originalBuffer)
		self.assertIsNone(self.plugin.container)


class TestCompositeDisplay(PluginTestCase):
	"""A display that is a Monarch above a Focus 80, driven through the add-on's own driver."""

	def makeHandler(self):
		handler = FakeHandler(9, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 32),
			("freedomScientific", 8, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		# After the plugin is up, since building it resets the stub configuration.
		setBandConfig("hidBrailleStandard_8x32", segmentCount=2)
		setBandConfig("freedomScientific_1x80", segmentCount=1)
		self.plugin.rebuildBuffer()

	def test_theDisplayIsArrangedByItsPhysicalDisplays(self):
		self.assertEqual(self.plugin.currentView.name, "devices")
		self.assertEqual(self.container.numSegments, 3)

	def test_theDeadColumnsAreClaimedByNothing(self):
		"""Nothing may be written to cells that reach no hardware."""
		for rect in self.container.rects:
			if rect.row < 8:
				self.assertLessEqual(rect.endCol, 32)

	def test_theCompositeSegmentCountIsNotUsed(self):
		"""It has none of its own: the displays behind it each answer for their own rows."""
		CONFIG["segmentCount"] = 8
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.numSegments, 3)

	def test_aClaimStillComposesOverIt(self):
		"""The claim takes the Monarch's rows; the Focus keeps its segment and its key."""
		self.plugin.activatePanel(
			SinglePanel("reader", SegmentRect(row=0, col=0, numRows=8, numCols=32)),
		)
		self.assertTrue(self.container.hasKey("reader"))
		self.assertTrue(self.container.hasKey("device.freedomScientific.0"))

	def test_aPinOnTheSecondDisplaySurvivesTheOtherBeingRearranged(self):
		"""What the whole phase is for: a pinned object held on the display beside the focus."""
		# The focus onto the Monarch, so that the Focus 80 is free to be pinned: a pin is
		# refused on the segment following the focus, which would otherwise redraw over it.
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()
		self.pin(2)
		setBandConfig("hidBrailleStandard_8x32", segmentCount=4)
		self.plugin.rebuildBuffer()
		self.assertIn("device.freedomScientific.0", self.plugin._monitors)

	def claimAcrossTheMonarch(self, numCols):
		"""A claim over the Monarch's rows, of a given width."""
		return SinglePanel("reader", SegmentRect(row=0, col=0, numRows=8, numCols=numCols))

	def test_aClaimWiderThanItsDisplayIsRefused(self):
		"""The mask is an ordinary panel, so a wide claim evicts it and takes the cells.

		Cells 32 to 79 of those rows reach no hardware. A segment over them would be filled
		by NVDA and thrown away by the driver's slice, with nothing raised and nothing logged.
		The caller is told at once, as it is for a claim that does not fit.
		"""
		with self.assertRaises(ValueError):
			self.plugin.activatePanel(self.claimAcrossTheMonarch(80))
		self.assertFalse(self.container.hasKey("reader"))

	def test_aRefusedClaimLeavesTheDisplayAsItWas(self):
		with self.assertRaises(ValueError):
			self.plugin.activatePanel(self.claimAcrossTheMonarch(80))
		for rect in self.container.rects:
			if rect.row < 8:
				self.assertLessEqual(rect.endCol, 32)

	def test_aClaimIsDroppedWhenItsDisplayIsReplacedByANarrowerOne(self):
		"""Accepted when it was made, and no longer possible. Dropped rather than refused."""
		self.plugin.activatePanel(self.claimAcrossTheMonarch(32))
		self.assertTrue(self.container.hasKey("reader"))
		self.handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, 8, 16),
			("freedomScientific", 8, 1, 80),
		)
		setBandConfig("hidBrailleStandard_8x16", segmentCount=1)
		self.plugin.rebuildBuffer()
		self.assertFalse(self.container.hasKey("reader"))
		self.assertTrue(
			any("cannot be shown" in message for level, message in log.messages),
			log.messages,
		)

	def test_aClaimWithinItsDisplayIsKept(self):
		self.plugin.activatePanel(self.claimAcrossTheMonarch(32))
		self.assertTrue(self.container.hasKey("reader"))

	def test_anActivatedViewReachingDeadCellsIsRefused(self):
		"""A view arrives whole, so it never passes through the composite view builder."""
		view = SegmentView(
			"wide",
			[SinglePanel("all", SegmentRect(row=0, col=0, numRows=9, numCols=80))],
		)
		with self.assertRaises(ValueError):
			self.plugin.activateView(view)
		self.assertEqual(self.plugin.currentView.name, "devices")

	def test_anActivatedViewWithinTheDisplaysIsAllowed(self):
		view = SegmentView(
			"narrow",
			[
				SinglePanel("top", SegmentRect(row=0, col=0, numRows=8, numCols=32)),
				BlankPanel(SegmentRect(row=0, col=32, numRows=8, numCols=48)),
				SinglePanel("bottom", SegmentRect(row=8, col=0, numRows=1, numCols=80)),
			],
		)
		self.plugin.activateView(view)
		self.assertEqual(self.plugin.currentView.name, "narrow")

	def test_aClaimSpanningTwoDisplaysIsRefused(self):
		"""The Monarch's last row and the Focus's row. Every cell is live, and it is still wrong.

		Nothing is lost here, which is what makes it worth its own test: the cells all reach
		hardware, so the only symptom is that the segment belongs to no display, and everything
		that addresses a segment through its display stops being able to find it.
		"""
		self.focusOntoTheMonarch()
		with self.assertRaises(ValueError):
			self.plugin.activatePanel(
				SinglePanel("reader", SegmentRect(row=7, col=0, numRows=2, numCols=32)),
			)
		self.assertFalse(self.container.hasKey("reader"))
		self.assertEqual(self.container.numSegments, 3)

	def test_anActivatedViewSpanningTwoDisplaysIsRefused(self):
		view = SegmentView(
			"across",
			[
				BlankPanel(SegmentRect(row=0, col=0, numRows=7, numCols=32)),
				SinglePanel("across", SegmentRect(row=7, col=0, numRows=2, numCols=32)),
				BlankPanel(SegmentRect(row=0, col=32, numRows=8, numCols=48)),
				BlankPanel(SegmentRect(row=8, col=32, numRows=1, numCols=48)),
			],
		)
		with self.assertRaises(ValueError):
			self.plugin.activateView(view)
		self.assertEqual(self.plugin.currentView.name, "devices")

	def focusOntoTheMonarch(self):
		"""Put the system focus on the Monarch's first segment.

		So that a claim over the boundary between the two displays is refused for spanning it
		rather than for evicting the focus segment, which by default is the Focus's.
		"""
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()

	def test_aSegmentWithinOneDisplayIsStillAllowedAtEitherEdge(self):
		"""Both sides of the boundary, so that the check is about spanning it and not about it."""
		self.focusOntoTheMonarch()
		self.plugin.activatePanel(
			SinglePanel("last", SegmentRect(row=7, col=0, numRows=1, numCols=32)),
		)
		self.assertTrue(self.container.hasKey("last"))
		self.plugin.activatePanel(
			SinglePanel("beside", SegmentRect(row=8, col=0, numRows=1, numCols=80)),
		)
		self.assertTrue(self.container.hasKey("beside"))

	def failTheFirstContainer(self):
		"""Make the first container built raise, as one built from an impossible view does.

		The fallback is otherwise unreachable from a test, and it is the one arrangement that
		gets installed without ever having been through `validateAgainstHardware`.
		"""
		real = DisplayContainer.__init__
		built = []

		def failOnce(container, handler, view):
			built.append(view)
			if len(built) == 1:
				raise ValueError("this view cannot be built")
			real(container, handler, view)

		# The constructor rather than the class, so that the class stays the class: the plugin
		# tests `isinstance(buffer, DisplayContainer)` to find its own container.
		DisplayContainer.__init__ = failOnce
		self.addCleanup(setattr, DisplayContainer, "__init__", real)
		return built

	def test_theFallbackForACompositeIsOneSegmentPerDisplay(self):
		self.failTheFirstContainer()
		self.plugin.rebuildBuffer()
		self.assertEqual(self.plugin.currentView.name, "devices.fallback")
		self.assertEqual(self.container.numSegments, 2)

	def test_theFallbackObeysTheHardware(self):
		"""What the old one did not: a single segment over a composite covers dead columns.

		Cells 32 to 79 of the Monarch's rows reach nothing, so an emergency arrangement across
		the whole display starts losing text — silently, and at the worst possible moment.
		"""
		self.failTheFirstContainer()
		self.plugin.rebuildBuffer()
		validateAgainstHardware(self.plugin.currentView, deviceMap(), 80)
		for rect in self.container.rects:
			if rect.row < 8:
				self.assertLessEqual(rect.endCol, 32, f"{rect} runs past the Monarch")

	def test_theFallbackReadsNoSettings(self):
		"""A setting is the likeliest reason the other view could not be built."""
		setBandConfig("hidBrailleStandard_8x32", segmentCount=4)
		self.failTheFirstContainer()
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.numSegments, 2)

	def test_aPinOnADisplaySurvivesTheFallback(self):
		"""Its keys are the ones a display's first segment has either way.

		The Monarch's first segment, since the fallback leaves the focus on the last segment
		and a pin there is released whatever else happens.
		"""
		self.pin(0)
		self.failTheFirstContainer()
		self.plugin.rebuildBuffer()
		self.assertIn("device.hidBrailleStandard.0", self.plugin.monitoredKeys)

	def test_aMessageDoesNotChangeTheOtherDisplaysCells(self):
		"""Which is what stops the other display being written to at all.

		`DeviceSlot.write` compares each member's cells against what it last sent and skips the
		member if they match, so a message in the Focus's segment costs the Monarch nothing —
		neither its content nor the time it takes to redraw it.
		"""
		for segment in self.container.segments:
			segment.append(Region("some content"))
		self.container.update()
		before = list(self.container.windowBrailleCells)
		buffer = self.handler.messageBuffer
		buffer.clear()
		region = Region("a message")
		region.update()
		buffer.regions.append(region)
		buffer.update()
		self.handler.buffer = buffer
		monarch = slice(0, 8 * 80)
		self.assertEqual(list(buffer.windowBrailleCells)[monarch], before[monarch])

	def test_arrangingACompositeCarriesOverAnOldReversalSetting(self):
		"""Where the carry over is hooked in: the one place both keys are known at once.

		Asserted against the configuration section rather than through
		`bmConfig.shouldReverseScrollButtons`, which the rest of the suite reads through a
		stand-in. Which section the value is in is the whole question here.
		"""
		from brlMultiline import bmConfig

		composite = bmConfig.getDisplayConfig("brlMultilineVirtual_9x80")
		composite["reverseScrollBtns"] = True
		# As it stands the first time this version runs: setUp has already built the display.
		composite["reverseScrollBtnsMigrated"] = False
		self.plugin.rebuildBuffer()
		self.assertTrue(bmConfig.getDisplayConfig("hidBrailleStandard_8x32")["reverseScrollBtns"])
		self.assertTrue(bmConfig.getDisplayConfig("freedomScientific_1x80")["reverseScrollBtns"])

	def _configureBothDisplays(self) -> None:
		"""Store the member list this class's composite was opened for, and clear the log."""
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices(
			[DeviceSpec("hidBrailleStandard"), DeviceSpec("freedomScientific")],
		)
		log.messages.clear()

	def test_aProfileListingDifferentDisplaysIsReported(self):
		"""The list is not applied until the composite is reopened, so say so rather than not.

		A profile can hold a member list of its own, and NVDA does not reinitialise a display
		whose driver name has not changed. The configuration and the hardware then disagree
		with nothing to announce it.
		"""
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices([DeviceSpec("freedomScientific")])
		post_configProfileSwitch.notify()
		self.assertTrue(
			any(level == "warning" and "was opened for" in message for level, message in log.messages),
			log.messages,
		)

	def test_aDisplayThatIsMerelyAwayIsNotReported(self):
		"""The complaint is about the list, and a display being switched off is not about the list.

		The composite drops a member that has gone and goes on with the rest, so the members it
		is driving are not the members it was opened for. Comparing against those reported every
		disconnection as a configuration problem — on every profile switch, for as long as the
		display stayed away, which on a machine with two profiles is a log full of it.
		"""
		self._configureBothDisplays()
		self.handler.display.slots[0].failed = True
		post_configProfileSwitch.notify()
		self.assertFalse(
			[message for level, message in log.messages if level == "warning"],
			log.messages,
		)

	def test_aDisplayNeverOpenedAtAllIsNotReported(self):
		"""The same, for a display that was already switched off when braille started.

		It has no slot rather than a failed one, so nothing below the driver can tell it from a
		display that was never configured. The composite's own record of what it was opened for
		is the only thing that can.
		"""
		self._configureBothDisplays()
		self.handler.display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			configured=("hidBrailleStandard", "freedomScientific"),
		)
		post_configProfileSwitch.notify()
		self.assertFalse(
			[message for level, message in log.messages if level == "warning"],
			log.messages,
		)

	def test_theSameDivergenceIsReportedOnce(self):
		"""A profile switch is a cheap moment to look, which makes it a cheap moment to repeat."""
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices([DeviceSpec("freedomScientific")])
		post_configProfileSwitch.notify()
		log.messages.clear()
		post_configProfileSwitch.notify()
		post_configProfileSwitch.notify()
		self.assertFalse(
			[message for level, message in log.messages if level == "warning"],
			log.messages,
		)

	def test_aDivergenceIsReportedAgainOnceItHasChanged(self):
		"""Said once is not said and forgotten: a different mismatch is a different thing to say."""
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices([DeviceSpec("freedomScientific")])
		post_configProfileSwitch.notify()
		log.messages.clear()
		vdConfig.setDevices([DeviceSpec("hidBrailleStandard")])
		post_configProfileSwitch.notify()
		self.assertTrue(
			any(level == "warning" and "was opened for" in message for level, message in log.messages),
			log.messages,
		)

	def test_aProfileListingTheSameDisplaysIsQuiet(self):
		from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
		from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec

		vdConfig.setDevices(
			[DeviceSpec("hidBrailleStandard"), DeviceSpec("freedomScientific")],
		)
		log.messages.clear()
		post_configProfileSwitch.notify()
		self.assertFalse([message for level, message in log.messages if level == "warning"])

	def test_anOrdinaryDisplayIsUnaffected(self):
		"""The device view is reached only through the driver, never by an ordinary display."""
		self.handler.display = types.SimpleNamespace(name="freedomScientific")
		self.handler.displayDimensions.numRows = MONARCH_ROWS
		self.handler.displayDimensions.numCols = MONARCH_COLS
		self.plugin.rebuildBuffer()
		self.assertEqual(self.plugin.currentView.name, "configured")


class FocusTrackingDisplayTestCase(PluginTestCase):
	"""Moving the segment that follows the focus from one combined display to another.

	The command exists because that is a setting with two answers on a display made of two,
	and typing a segment number into the dialog to change it means working out which number
	the other display's segments have this week.
	"""

	displays = (
		("hidBrailleStandard", 0, 8, 32),
		("freedomScientific", 8, 1, 80),
	)
	segmentCounts = {"hidBrailleStandard_8x32": 2, "freedomScientific_1x80": 1}
	rows = 9
	cols = 80

	def makeHandler(self):
		handler = FakeHandler(self.rows, self.cols)
		handler.display = fakeVirtualDisplay(*self.displays)
		return handler

	def setUp(self):
		super().setUp()
		for displayKey, count in self.segmentCounts.items():
			setBandConfig(displayKey, segmentCount=count)
		self.plugin.rebuildBuffer()

	def press(self):
		self.plugin.script_changeFocusTrackingDisplay(None)

	@property
	def focusDriver(self):
		""":return: the driver of the display the focus segment is actually on."""
		return driverNameForSegmentKey(self.container.focusSegmentKey)


class TestTwoDisplays(FocusTrackingDisplayTestCase):
	def test_thereAreTwoToChooseBetween(self):
		targets = self.plugin.focusMoves.targets()
		self.assertEqual(
			[target.driverName for target in targets], ["hidBrailleStandard", "freedomScientific"],
		)
		self.assertEqual([target.segments for target in targets], [[0, 1], [2]])

	def test_theFocusStartsOnTheLastSegment(self):
		"""Which is the default, and is on the second display."""
		targets = self.plugin.focusMoves.targets()
		self.assertEqual([target.holdsFocus for target in targets], [False, True])

	def test_pressingItMovesTheFocusToTheOtherDisplay(self):
		self.press()
		self.assertEqual(self.focusDriver, "hidBrailleStandard")

	def test_pressingItAgainBringsItBack(self):
		self.press()
		self.press()
		self.assertEqual(self.focusDriver, "freedomScientific")

	def test_theAnswerIsStored(self):
		"""So it survives a rebuild, a profile switch, and the next session."""
		self.press()
		self.assertEqual(CONFIG["focusSegment"], 0)
		self.plugin.rebuildBuffer()
		self.assertEqual(self.focusDriver, "hidBrailleStandard")

	def test_itIsAnnounced(self):
		spokenMessages.clear()
		self.press()
		self.assertTrue(spokenMessages, "the command said nothing")

	def test_aClaimOverTheDisplayDoesNotRenumberIt(self):
		"""The trap the numbering exists for: a claim renumbers the display and not the setting.

		A panel laid over the Monarch's rows evicts the segments it covers, so what the
		container holds is no longer what the configuration counts. The number stored has to
		be the configuration's, or giving the claim back would leave the focus somewhere
		nobody asked for.
		"""
		self.plugin.activatePanel(
			SinglePanel("reader", SegmentRect(row=0, col=0, numRows=8, numCols=32)),
		)
		self.press()
		self.assertEqual(CONFIG["focusSegment"], 0)
		self.plugin.deactivatePanel("reader")
		self.assertEqual(self.focusDriver, "hidBrailleStandard")


class TestMovingTheFocusOverAPin(FocusTrackingDisplayTestCase):
	"""A pin and the focus cannot share a segment, so moving the focus can evict one.

	Released quietly until now, and the reader's account is why that was wrong: rearranging
	the layout in the settings is a decision made while looking at the layout, and pressing a
	key to move the focus is a decision about the focus. The pin is not in their head at that
	moment, so losing it is a surprise rather than a choice.
	"""

	segmentCounts = {"hidBrailleStandard_8x32": 2, "freedomScientific_1x80": 1}

	def setUp(self):
		super().setUp()
		self.asked = []
		self.plugin.focusMoves.askAboutDisplacedPins = lambda *args: self.asked.append(args)

	def moveToTheMonarch(self):
		"""Move the focus from the Focus 80 onto the Monarch, where the pins are."""
		targets = self.plugin.focusMoves.targets()
		monarch = next(t for t in targets if t.driverName == "hidBrailleStandard")
		self.plugin.focusMoves.moveWithPins(monarch, position=0)

	def test_aFocusMoveWithNothingInItsWayJustMoves(self):
		self.moveToTheMonarch()
		self.assertEqual(self.focusDriver, "hidBrailleStandard")
		self.assertEqual(self.asked, [])

	def test_thePinInTheWayIsSeen(self):
		self.pin(0)
		self.assertEqual(self.plugin.focusMoves.pinsDisplacedBy(0), ["device.hidBrailleStandard.0"])

	def test_aPinElsewhereIsNotInTheWay(self):
		self.pin(1)
		self.assertEqual(self.plugin.focusMoves.pinsDisplacedBy(0), [])

	def test_theSwapNeedsNoDialog(self):
		"""One pin and somewhere to put it is what the reader meant; asking would be noise."""
		self.pin(0)
		self.moveToTheMonarch()
		self.assertEqual(self.asked, [])
		self.assertEqual(self.focusDriver, "hidBrailleStandard")

	def test_andThePinSurvivesIt(self):
		self.pin(0)
		self.moveToTheMonarch()
		self.assertEqual(len(self.plugin._monitors), 1)

	def test_thePinGoesWhereTheFocusCameFrom(self):
		"""They trade places, so moving the focus back trades them back."""
		self.pin(0)
		self.moveToTheMonarch()
		self.assertIn("device.freedomScientific.0", self.plugin._monitors)

	def test_theSegmentTheFocusIsLeavingIsOfferedFirst(self):
		self.pin(0)
		self.assertEqual(self.plugin.focusMoves.homesForDisplacedPins(0)[0], "device.freedomScientific.0")

	def test_aSegmentAlreadyPinnedIsNotOfferedAsAHome(self):
		self.pin(0)
		self.pin(1)
		self.assertNotIn("device.hidBrailleStandard.1", self.plugin.focusMoves.homesForDisplacedPins(0))

	def test_aPinBesideItDoesNotCostItItsHome(self):
		"""The segment the focus vacates is free whatever else is pinned, so the swap holds."""
		self.pin(0)
		self.pin(1)
		self.moveToTheMonarch()
		self.assertEqual(self.asked, [])
		self.assertIn("device.freedomScientific.0", self.plugin._monitors)
		self.assertIn("device.hidBrailleStandard.1", self.plugin._monitors)

	def noRoomAnywhere(self):
		"""Leave the move with nowhere to put what it displaces.

		Every free segment reserved by a claim is the real way this happens, and building one
		here would be building a claim to test a rule about pins. The rule is "no home means
		ask", so it is told no home.
		"""
		self.plugin.focusMoves.homesForDisplacedPins = lambda number: []

	def test_withNowhereToPutItTheReaderIsAsked(self):
		"""Rather than losing it: forgetting a pin is exactly how this command lost one."""
		self.pin(0)
		self.noRoomAnywhere()
		self.moveToTheMonarch()
		self.assertEqual(len(self.asked), 1)
		_target, _position, displaced, homes = self.asked[0]
		self.assertEqual(displaced, ["device.hidBrailleStandard.0"])
		self.assertEqual(homes, [])

	def test_nothingMovesUntilTheyAnswer(self):
		self.pin(0)
		self.noRoomAnywhere()
		before = self.focusDriver
		self.moveToTheMonarch()
		self.assertEqual(self.focusDriver, before)
		self.assertEqual(len(self.plugin._monitors), 1)

	def test_answeringToKeepItMovesItAndTheFocus(self):
		self.pin(0)
		self.noRoomAnywhere()
		self.moveToTheMonarch()
		target, position, displaced, _homes = self.asked[0]
		# The reader divides something further, and there is room after all.
		self.plugin.focusMoves.moveTo(target, position, keep={displaced[0]: "device.freedomScientific.0"})
		self.assertEqual(self.focusDriver, "hidBrailleStandard")
		self.assertIn("device.freedomScientific.0", self.plugin._monitors)

	def test_answeringToLetItGoMovesTheFocusAlone(self):
		self.pin(0)
		self.noRoomAnywhere()
		self.moveToTheMonarch()
		target, position, _displaced, _homes = self.asked[0]
		self.plugin.focusMoves.moveTo(target, position)
		self.assertEqual(self.focusDriver, "hidBrailleStandard")
		self.assertNotIn("device.hidBrailleStandard.0", self.plugin._monitors)

	def test_aPinNotInTheWayIsUndisturbed(self):
		self.pin(1)
		self.moveToTheMonarch()
		self.assertIn("device.hidBrailleStandard.1", self.plugin._monitors)


class TestSwappingWhileTheBandIsClaimed(FocusTrackingDisplayTestCase):
	"""The reported defect, and the layout it was reported on.

	A Monarch as one segment, a Focus 80 as one, the focus on the Focus 80 and a Teams chat
	pinned to the Monarch. Toggling swapped them, as it should. Toggling back asked whether
	to lose the pin, and losing it was the only answer offered.

	The reason is that the flow band claims *the display the focus is on* — this reader asked
	for that, so that the display the focus left could be used for something else. So after
	the first toggle the whole Monarch belonged to the band, the Focus 80 was wanted by the
	focus, and there was nowhere on the display for the pin. Nowhere yet: the band gives
	those rows back a moment later, in the same rebuild, because the focus is leaving.
	"""

	displays = (
		("hidBrailleStandard", 0, 8, 32),
		("freedomScientific", 8, 1, 80),
	)
	segmentCounts = {"hidBrailleStandard_8x32": 1, "freedomScientific_1x80": 1}
	rows = 9
	cols = 80

	def setUp(self):
		super().setUp()
		CONFIG["flowEnabled"] = True
		CONFIG["flowBrowseMode"] = True
		self.plugin.rebuildBuffer()
		self.asked = []
		self.plugin.focusMoves.askAboutDisplacedPins = lambda *args: self.asked.append(args)

	def pinTheChat(self, number=0):
		"""Pin something that flows, since what the reader lost was a flowing chat."""
		import api

		interceptor = FakeTreeInterceptor([f"message {index}" for index in range(30)], caretIndex=0)
		api.getNavigatorObject = lambda: FakeNavigatorObject("Teams chat", treeInterceptor=interceptor)
		self.plugin.startMonitoring(number)

	def keys(self):
		return [spec.key for spec in self.container.specs]

	def pin(self):
		""":return: the one monitor, or None once it has been released."""
		return next(iter(self.plugin._monitors.values()), None)

	def test_thePinFlowsWhereItWasMade(self):
		self.pinTheChat()
		self.assertIsNotNone(self.pin().controller)

	def test_theBandTakesTheDisplayTheFocusMovesTo(self):
		"""Which is what leaves nowhere for the pin, and is not itself a fault."""
		self.pinTheChat()
		self.press()
		self.assertIn("flow", self.keys())

	def test_thePinIsStillThereAfterTogglingBack(self):
		"""The defect. It was released, and the only offer was to release it."""
		self.pinTheChat()
		self.press()
		self.press()
		self.assertIsNotNone(self.pin())

	def test_andItIsBackOnTheDisplayItCameFrom(self):
		"""A round trip: the pin and the focus trade places and trade back."""
		self.pinTheChat()
		self.press()
		self.press()
		self.assertEqual(self.pin().segmentKey, "device.hidBrailleStandard.0")
		self.assertIsNotNone(self.pin().controller)

	def test_andNothingAsked(self):
		self.pinTheChat()
		self.press()
		self.press()
		self.assertEqual(self.asked, [])

	def test_theSegmentTheFocusIsLeavingIsOfferedThoughItIsClaimed(self):
		"""The band on it goes where the focus goes, so those rows are about to be free."""
		self.pinTheChat()
		self.press()
		self.assertEqual(self.plugin.focusMoves.homesForDisplacedPins(1), ["flow"])

	def test_aClaimTheFocusIsNotOnIsStillNotOffered(self):
		"""Only the focus's own claim is leaving. Anything else would be drawn over."""
		self.pinTheChat()
		self.press()
		claimed = self.container.specs[0]
		self.assertTrue(claimed.isReserved)
		self.plugin.container._focusSegmentNumber = 1
		self.assertEqual(self.plugin.focusMoves.homesForDisplacedPins(1), [])

	def test_theReaderIsToldWhenItLandsSomewhereTooShort(self):
		"""One row cannot hold a flow, so a chat that scrolled becomes a line that does not.

		Silently, until this: the reader's report was that panning had stopped working.
		"""
		self.pinTheChat()
		spokenMessages.clear()
		self.press()
		self.assertIsNone(self.pin().controller)
		self.assertTrue(
			any("flow" in message for message in spokenMessages),
			f"nothing said about the flow: {spokenMessages}",
		)

	def test_andNotToldWhenItLandsSomewhereItCanFlow(self):
		self.pinTheChat()
		self.press()
		spokenMessages.clear()
		self.press()
		self.assertIsNotNone(self.pin().controller)
		self.assertFalse(any("flow" in message for message in spokenMessages))

	def pinARun(self, number=0):
		"""Pin a run of objects, which is what the reader actually had: a chat history."""
		import api

		messages = []
		for index in range(8):
			message = FakeNavigatorObject(f"message {index}", role="LISTITEM")
			setattr(message, flowObjects.RUN_DECLARATION, True)
			setattr(
				message,
				flowObjects.RUN_NEXT,
				lambda index=index: messages[index + 1] if index + 1 < len(messages) else None,
			)
			setattr(
				message,
				flowObjects.RUN_PREVIOUS,
				lambda index=index: messages[index - 1] if index else None,
			)
			messages.append(message)
		api.getNavigatorObject = lambda: messages[0]
		self.plugin.startMonitoring(number)
		return messages

	def test_aChatCarriedToTheSingleRowDisplayIsStillAFlow(self):
		"""Which is the whole point of the swap for this reader: one row, one message."""
		self.pinARun()
		self.press()
		control = self.pin().controller
		self.assertIsNotNone(control)
		self.assertEqual(control.window.numRows, 1)
		self.assertTrue(control.panForward())

	def test_soNothingIsSaidAboutRowsForIt(self):
		self.pinARun()
		spokenMessages.clear()
		self.press()
		self.assertFalse(any("flow" in message for message in spokenMessages), spokenMessages)

	def test_andItComesBackToTheDisplayItCameFrom(self):
		self.pinARun()
		self.press()
		self.press()
		self.assertEqual(self.pin().segmentKey, "device.hidBrailleStandard.0")
		self.assertEqual(self.pin().controller.window.numRows, 8)

	def test_aPinWithNowhereAtAllIsReportedRatherThanVanishing(self):
		"""The other end of it. Told their object moved, the reader must not find it gone."""
		self.pinTheChat()
		monitor = self.pin()
		self.plugin._monitors.clear()
		messages = self.plugin.focusMoves.describeCarriedPins(
			[focusDisplays.CarriedPin(monitor=monitor, home="device.freedomScientific.0", wasFlowing=True)],
		)
		self.assertTrue(messages)
		self.assertIn("could not be kept", messages[-1])


class TestTheDialogAboutDisplacedPins(FocusTrackingDisplayTestCase):
	"""The other dialog on this path, driven rather than stood in for.

	The decision above it is tested with the dialog replaced, which is the right way round:
	the interesting rules are which pins are in the way and where they could go. What is left
	is the wiring — that answering yes moves the focus, that answering no moves nothing, and
	that a choice of which to keep becomes the mapping the move is given — and that wiring
	was written and never run.
	"""

	segmentCounts = {"hidBrailleStandard_8x32": 2, "freedomScientific_1x80": 1}

	def monarch(self):
		targets = self.plugin.focusMoves.targets()
		return next(target for target in targets if target.driverName == "hidBrailleStandard")

	def moveWithNoRoom(self, answer):
		"""Move the focus onto a pin with nowhere to put it, and answer the question."""
		self.plugin.focusMoves.homesForDisplacedPins = lambda number: []
		dialogAnswers.messageAnswer = answer
		self.plugin.focusMoves.moveWithPins(self.monarch(), position=0)
		callAfterQueue.flush()

	def test_theQuestionNamesWhatIsInTheWay(self):
		self.pin(0, name="the build log")
		self.moveWithNoRoom(NO)
		self.assertEqual(len(dialogAnswers.shown), 1)
		self.assertIn("the build log", dialogAnswers.shown[0].message)

	def test_sayingNoLeavesEverythingWhereItWas(self):
		self.pin(0)
		before = self.focusDriver
		self.moveWithNoRoom(NO)
		self.assertEqual(self.focusDriver, before)
		self.assertIn("device.hidBrailleStandard.0", self.plugin._monitors)

	def test_sayingYesMovesTheFocusAndLetsThePinGo(self):
		"""Which is the old behaviour, now as an answer the reader gave rather than a surprise."""
		self.pin(0)
		self.moveWithNoRoom(YES)
		self.assertEqual(self.focusDriver, "hidBrailleStandard")
		self.assertNotIn("device.hidBrailleStandard.0", self.plugin._monitors)

	def test_nvdaGetsItsWindowBack(self):
		self.pin(0)
		self.moveWithNoRoom(NO)
		self.assertEqual(mainFrame.prePopups, 1)
		self.assertEqual(mainFrame.postPopups, 1)

	def test_theOneChosenIsTheOneKept(self):
		"""Several pins in the way and room for one, which is a list rather than a question.

		Handed the pins directly. `pinsDisplacedBy` answers for one segment and so names one
		pin today, and the layout that would displace two is not one this display can be put
		into — but the reader asked for the general rule, and a rule nothing exercises is a
		rule nobody knows is broken.
		"""
		self.pin(0, name="first")
		self.pin(1, name="second")
		displaced = ["device.hidBrailleStandard.0", "device.hidBrailleStandard.1"]
		dialogAnswers.answer = ID_OK
		dialogAnswers.select = [1]
		self.plugin.focusMoves.askAboutDisplacedPins(self.monarch(), 0, displaced, ["device.freedomScientific.0"])
		callAfterQueue.flush()
		self.assertEqual(dialogAnswers.shown[0].kind, "multi")
		self.assertIn("device.freedomScientific.0", self.plugin._monitors)
		self.assertEqual(self.plugin._monitors["device.freedomScientific.0"].name, "second")

	def test_cancellingTheListMovesNothing(self):
		self.pin(0)
		self.pin(1)
		before = self.focusDriver
		dialogAnswers.answer = ID_CANCEL
		self.plugin.focusMoves.askAboutDisplacedPins(
			self.monarch(),
			0,
			["device.hidBrailleStandard.0", "device.hidBrailleStandard.1"],
			["device.freedomScientific.0"],
		)
		callAfterQueue.flush()
		self.assertEqual(self.focusDriver, before)
		self.assertIn("device.hidBrailleStandard.0", self.plugin._monitors)


class TestTwoDisplaysDividedAlike(FocusTrackingDisplayTestCase):
	"""Two eight row displays, each in two segments, so the position down one has a twin on the other."""

	displays = (
		("hidBrailleStandard", 0, 8, 32),
		("brailleNote", 8, 8, 32),
	)
	segmentCounts = {"hidBrailleStandard_8x32": 2, "brailleNote_8x32": 2}
	rows = 16
	cols = 32

	def test_thePositionDownTheDisplayIsKept(self):
		"""Otherwise moving back and forth walks towards the bottom instead of returning."""
		CONFIG["focusSegment"] = 3
		self.plugin.rebuildBuffer()
		self.press()
		self.assertEqual(CONFIG["focusSegment"], 1)
		self.press()
		self.assertEqual(CONFIG["focusSegment"], 3)


class TestMoreThanTwoDisplays(FocusTrackingDisplayTestCase):
	"""Three displays, where a command that toggled would be a command that walked round a ring."""

	displays = (
		("hidBrailleStandard", 0, 8, 32),
		("brailleNote", 8, 2, 32),
		("freedomScientific", 10, 1, 32),
	)
	segmentCounts = {
		"hidBrailleStandard_8x32": 2,
		"brailleNote_2x32": 1,
		"freedomScientific_1x32": 1,
	}
	rows = 11
	cols = 32

	def setUp(self):
		super().setUp()
		self.asked = []
		self.plugin.focusMoves.chooseDisplay = lambda targets, position: self.asked.append((targets, position))

	def test_theUserIsAsked(self):
		self.press()
		self.assertEqual(len(self.asked), 1)
		targets, _position = self.asked[0]
		self.assertEqual(len(targets), 3)

	def test_nothingMovesUntilTheyAnswer(self):
		before = CONFIG["focusSegment"]
		self.press()
		self.assertEqual(CONFIG["focusSegment"], before)

	def test_answeringMovesTheFocus(self):
		self.press()
		targets, position = self.asked[0]
		self.plugin.focusMoves.moveTo(targets[0], position)
		self.assertEqual(self.focusDriver, "hidBrailleStandard")


class TestTheListOfDisplays(FocusTrackingDisplayTestCase):
	"""The dialog itself, driven rather than stood in for.

	Worth running for real, because the bug it is here for lived in the stub's shadow: the
	toggle went through the pin-preserving move and the list did not, so choosing a display
	from it released a pin on that display without a word. Every test above this one
	replaced the chooser with a recorder and so could not see it.
	"""

	displays = (
		("hidBrailleStandard", 0, 8, 32),
		("brailleNote", 8, 2, 32),
		("freedomScientific", 10, 1, 32),
	)
	segmentCounts = {
		"hidBrailleStandard_8x32": 2,
		"brailleNote_2x32": 1,
		"freedomScientific_1x32": 1,
	}
	rows = 11
	cols = 32

	def choose(self, index, agree=True):
		"""Press the command and answer the list it puts up."""
		dialogAnswers.answer = ID_OK if agree else ID_CANCEL
		dialogAnswers.select = index
		self.press()
		callAfterQueue.flush()

	def test_theListNamesEveryDisplay(self):
		self.choose(0)
		self.assertEqual(len(dialogAnswers.shown), 1)
		self.assertEqual(dialogAnswers.shown[0].kind, "single")
		self.assertEqual(len(dialogAnswers.shown[0].choices), 3)

	def test_answeringMovesTheFocus(self):
		self.choose(0)
		self.assertEqual(self.focusDriver, "hidBrailleStandard")

	def test_cancellingMovesNothing(self):
		before = self.focusDriver
		self.choose(0, agree=False)
		self.assertEqual(self.focusDriver, before)

	def test_aPinOnTheChosenDisplayIsKept(self):
		"""The finding: this went through the plain move and lost it."""
		self.pin(0)
		self.choose(0)
		self.assertEqual(self.focusDriver, "hidBrailleStandard")
		self.assertEqual(len(self.plugin._monitors), 1)

	def test_andItTradesPlacesWithTheFocus(self):
		self.pin(0)
		self.choose(0)
		self.assertIn("device.freedomScientific.0", self.plugin._monitors)

	def test_aPinSomewhereElseIsUndisturbed(self):
		self.pin(1)
		self.choose(0)
		self.assertIn("device.hidBrailleStandard.1", self.plugin._monitors)

	def test_nvdaGetsItsWindowBack(self):
		"""Both halves of the popup, since the first without the second leaves it raised."""
		self.choose(0)
		self.assertEqual(mainFrame.prePopups, 1)
		self.assertEqual(mainFrame.postPopups, 1)


class TwinRow(FakeNavigatorObject):
	"""A row NVDA may hand out twice, as two objects for one message.

	Equal by what it is about rather than by identity, which is what NVDA's own objects do and
	what makes "is this the row I asked for" a question worth asking. Its cells are its
	children and carry their column numbers, as a grid row's do.
	"""

	def __init__(self, key, columns=2):
		super().__init__(name=key, role="LISTITEM")
		self.key = key
		self.cellObjects = [
			FakeGridCell(f"{key} column {number}", number) for number in range(1, columns + 1)
		]
		for cell in self.cellObjects:
			cell.parent = self

	@property
	def children(self):
		return list(self.cellObjects)

	@property
	def childCount(self):
		return len(self.cellObjects)

	def __eq__(self, other):
		return getattr(other, "key", None) == self.key

	def __hash__(self):
		return hash(self.key)


class TestTheFocusEvent(PluginTestCase):
	"""The one event this add-on handles, and it is here for the routing key over a column.

	A list view cell cannot take the focus, so its row is focused and the navigator object is
	taken the rest of the way — and NVDA moves the navigator object to whatever has just taken
	the focus, which happens after the routing key has finished. The column has to be asked
	for again once the focus has actually arrived.
	"""

	def setUp(self):
		super().setUp()
		from brlMultiline import flowObjectTable

		self.tables = flowObjectTable
		navigatedTo.clear()
		self.addCleanup(setattr, flowObjectTable, "_pendingColumn", None)

	def test_theEventIsPassedOn(self):
		"""Before anything else is done with it: this handler is a bystander."""
		passedOn = []
		self.plugin.event_gainFocus(FakeNavigatorObject("something"), lambda: passedOn.append(True))
		self.assertEqual(passedOn, [True])

	def twins(self):
		""":return: two objects for one message, as NVDA hands them out.

		A fresh wrapper for the row arrives with the focus event, and its cells are fresh
		objects too. The one the routing key was holding belongs to the row as it was before
		the move.
		"""
		return TwinRow("a message"), TwinRow("a message")

	def test_aColumnWaitingForTheFocusIsPutBack(self):
		asked, arrived = self.twins()
		self.tables.askForColumnAfterFocus(asked, asked.cellObjects[1])
		self.plugin.event_gainFocus(arrived, lambda: None)
		self.assertIs(navigatedTo[-1], arrived.cellObjects[1])

	def test_theCellIsTheFocusedRowsOwn(self):
		"""A review's case: the cell the routing key held belongs to the row NVDA has already
		replaced, so the reader was put on an object that is no longer in the tree."""
		asked, arrived = self.twins()
		self.tables.askForColumnAfterFocus(asked, asked.cellObjects[1])
		self.plugin.event_gainFocus(arrived, lambda: None)
		self.assertIsNot(navigatedTo[-1], asked.cellObjects[1])
		self.assertEqual(navigatedTo[-1].columnNumber, 2)

	def test_aRowWithoutThatColumnMovesNothing(self):
		asked, arrived = self.twins()
		arrived.cellObjects = arrived.cellObjects[:1]
		self.tables.askForColumnAfterFocus(asked, asked.cellObjects[1])
		self.plugin.event_gainFocus(arrived, lambda: None)
		self.assertEqual(navigatedTo, [])

	def test_anOrdinaryFocusChangeMovesNothing(self):
		self.plugin.event_gainFocus(FakeNavigatorObject("a button"), lambda: None)
		self.assertEqual(navigatedTo, [])

	def test_aFailureHereDoesNotStopTheFocus(self):
		"""It runs on every focus change, so it must not be able to break one."""

		def explode(obj):
			raise RuntimeError("no")

		original = self.tables.columnWantedAfterFocus
		self.addCleanup(setattr, self.tables, "columnWantedAfterFocus", original)
		self.tables.columnWantedAfterFocus = explode
		passedOn = []
		self.plugin.event_gainFocus(FakeNavigatorObject("something"), lambda: passedOn.append(True))
		self.assertEqual(passedOn, [True])


class TestOneDisplay(PluginTestCase):
	"""An ordinary display, where there is nowhere else for the focus to go."""

	def test_thereIsNothingToChooseBetween(self):
		self.assertEqual(self.plugin.focusMoves.targets(), [])

	def test_pressingItSaysSo(self):
		spokenMessages.clear()
		self.plugin.script_changeFocusTrackingDisplay(None)
		self.assertTrue(spokenMessages, "the command said nothing")
		self.assertEqual(CONFIG["focusSegment"], -1)


class TestWhichTableAPagingCommandMoves(PluginTestCase):
	"""A pinned table is put on the other display precisely so that it is not where the reader
	is working, so "the table in front of you" is the wrong answer for it. The answer is the
	table under the hand that pressed, which is the rule the panning keys already follow."""

	def keys(self):
		CONFIG["segmentCount"] = 3
		self.plugin.rebuildBuffer()
		return [segment.key for segment in self.plugin.container.segments]

	def showing(self, keys):
		"""Two tables laid out, in two segments, standing in for a band and a pin.

		Built in the reverse of display order on purpose: the fallback is meant to be the
		first *on the display*, not the first this dictionary happens to hold.
		"""
		return {keys[2]: "the third", keys[0]: "the first"}

	def test_thePressedDisplaysTableWins(self):
		keys = self.keys()
		self.plugin.tablesInColumns = lambda: self.showing(keys)
		self.plugin.segmentKeysOn = lambda driver: [keys[2]]
		self.assertEqual(self.plugin.tables.tableToPage(None), "the third")

	def test_withNoKeyBehindItTheFirstInDisplayOrderWins(self):
		"""An ordinary display, or a command run from the keyboard: there is nothing to
		choose between, and the first is the only one on a display showing one."""
		keys = self.keys()
		self.plugin.tablesInColumns = lambda: self.showing(keys)
		self.plugin.segmentKeysOn = lambda driver: []
		self.assertEqual(self.plugin.tables.tableToPage(None), "the first")

	def test_aDisplayWithNothingLaidOutFallsBackToDisplayOrder(self):
		keys = self.keys()
		self.plugin.tablesInColumns = lambda: self.showing(keys)
		self.plugin.segmentKeysOn = lambda driver: [keys[1]]
		self.assertEqual(self.plugin.tables.tableToPage(None), "the first")

	def test_nothingLaidOutAnywhereIsNothingToMove(self):
		self.keys()
		self.plugin.tablesInColumns = lambda: {}
		self.assertIsNone(self.plugin.tables.tableToPage(None))


class LayoutAppModule:
	"""An app module offering a chat layout: the messages above, the focus on the last row."""

	appName = "chatApp"

	def __init__(self, answer="chat"):
		self.answer = answer
		self.asked = []

	def brlMultilineLayout(self, focus, area):
		self.asked.append(area)
		if self.answer != "chat":
			return self.answer
		return {
			"name": "chat",
			"segments": [
				{"show": FakeNavigatorObject("Messages", role="list")},
				{"size": 1, "show": "focus"},
			],
		}


def focusIn(appModule):
	""":return: a focus whose app module is the one given."""
	focus = FakeNavigatorObject("Compose", role="editableText")
	focus.appModule = appModule
	return focus


class TestAppLayouts(PluginTestCase):
	"""Milestone 1 of `docs/design/app-layouts-plan.md`: the setting, the toggle, the room an
	app module is given and the report of what it would do with it. Nothing is shown yet."""

	def test_offByDefault(self):
		"""A privilege the reader grants, not something an app module takes."""
		self.assertFalse(bmConfig.shouldAllowAppLayouts())
		self.assertFalse(self.plugin.appLayoutsInForce())

	def test_allowedByTheSetting(self):
		CONFIG["appLayouts"] = True
		self.assertTrue(self.plugin.appLayoutsInForce())

	def test_theToggleSetsThemAsideAndBringsThemBack(self):
		CONFIG["appLayouts"] = True
		self.plugin.script_toggleAppLayouts(None)
		self.assertFalse(self.plugin.appLayoutsInForce())
		self.assertEqual(spokenMessages[-1], "App layouts off")
		self.plugin.script_toggleAppLayouts(None)
		self.assertTrue(self.plugin.appLayoutsInForce())
		self.assertEqual(spokenMessages[-1], "App layouts on")

	def test_theToggleSaysSoWhereTheyAreNotAllowed(self):
		"""Rather than flipping a state that would surprise the reader the day they allow it."""
		self.plugin.script_toggleAppLayouts(None)
		self.assertFalse(self.plugin._appLayoutsSuspended)
		self.assertIn("Allow it in the BrlMultiline settings", spokenMessages[-1])

	def test_theWholeDisplayIsFreeWithNothingPinned(self):
		area, bands, _within, _spread = self.plugin.appLayoutRoom()
		self.assertEqual(area, SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS))
		self.assertEqual(bands, [])

	def test_aPinnedSegmentIsNotFree(self):
		"""The reader's pins come first: the top segment's two rows are not offered."""
		self.pin(0)
		area, _bands, _within, _spread = self.plugin.appLayoutRoom()
		self.assertEqual(area, SegmentRect(2, 0, MONARCH_ROWS - 2, MONARCH_COLS))

	def test_aClaimIsNotFree(self):
		self.plugin.activatePanel(SinglePanel("table", SegmentRect(0, 0, 1, MONARCH_COLS)))
		area, _bands, _within, _spread = self.plugin.appLayoutRoom()
		self.assertEqual(area.row, 1)

	def test_theReportShowsThePlacedLayout(self):
		CONFIG["appLayouts"] = True
		appModule = LayoutAppModule()
		lines = self.plugin.appLayoutReport(focusIn(appModule))
		self.assertIn("App module: chatApp", lines)
		self.assertIn("App layouts: allowed", lines)
		self.assertIn(f"Offered: {MONARCH_ROWS} rows, {MONARCH_COLS} columns", lines)
		self.assertIn("Layout 'chat', 2 segments", lines)
		self.assertTrue(any("rows 0 to 6" in line and "'Messages'" in line for line in lines), lines)
		self.assertTrue(any("row 7" in line and "the focus" in line for line in lines), lines)

	def test_theReportAsksEvenWhereNotAllowed(self):
		"""An app module's author needs the answer before a reader has reason to allow it."""
		lines = self.plugin.appLayoutReport(focusIn(LayoutAppModule()))
		self.assertIn("App layouts: not allowed on this display", lines)
		self.assertIn("Layout 'chat', 2 segments", lines)

	def test_theReportSaysWhenSetAside(self):
		CONFIG["appLayouts"] = True
		self.plugin.script_toggleAppLayouts(None)
		lines = self.plugin.appLayoutReport(focusIn(LayoutAppModule()))
		self.assertIn("App layouts: allowed, and set aside for now", lines)

	def test_theAppModuleIsOfferedOnlyTheFreeRows(self):
		self.pin(0)
		appModule = LayoutAppModule()
		self.plugin.appLayoutReport(focusIn(appModule))
		self.assertEqual(appModule.asked[-1].rows, MONARCH_ROWS - 2)

	def test_noMethod(self):
		lines = self.plugin.appLayoutReport(focusIn(object()))
		self.assertTrue(any("has no brlMultilineLayout method" in line for line in lines), lines)

	def test_noLayoutHere(self):
		lines = self.plugin.appLayoutReport(focusIn(LayoutAppModule(answer=None)))
		self.assertIn("Layout: none here", lines)

	def test_aBadAnswerIsReportedNotRaised(self):
		lines = self.plugin.appLayoutReport(focusIn(LayoutAppModule(answer={"name": "chat"})))
		self.assertTrue(any(line.startswith("Layout refused:") for line in lines), lines)

	def test_sizesThatDoNotFitAreReported(self):
		answer = {"name": "big", "segments": [{"size": 20, "show": object()}, {"show": "focus"}]}
		lines = self.plugin.appLayoutReport(focusIn(LayoutAppModule(answer=answer)))
		self.assertTrue(any(line.startswith("Layout refused:") and "20" in line for line in lines), lines)

	def test_everythingPinned(self):
		for number in range(self.segmentCount):
			if number != self.container.focusSegmentNumber:
				self.pin(number)
		# The focus segment is never pinned, so two rows stay free.
		area, _bands, _within, _spread = self.plugin.appLayoutRoom()
		self.assertEqual(area, self.container.focusSegment.rect)


class TestAppLayoutsOnCombinedDisplays(PluginTestCase):
	"""A Monarch above a Focus 80 is one tall display to an app module."""

	segmentCount = 1

	def makeHandler(self):
		handler = FakeHandler(MONARCH_ROWS + 1, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, MONARCH_ROWS, MONARCH_COLS),
			("freedomScientific", MONARCH_ROWS, 1, 80),
		)
		return handler

	def test_theDisplaysAreKnownToPlacementNotToTheAppModule(self):
		appModule = LayoutAppModule()
		lines = self.plugin.appLayoutReport(focusIn(appModule))
		self.assertEqual(appModule.asked[-1].rows, MONARCH_ROWS + 1)
		self.assertEqual(appModule.asked[-1].columns, MONARCH_COLS)
		self.assertTrue(any(line.startswith("Displays: ") for line in lines), lines)
		# The messages on the whole Monarch, the focus on the Focus 80.
		self.assertTrue(any(f"rows 0 to {MONARCH_ROWS - 1}" in line and "'Messages'" in line for line in lines), lines)
		self.assertTrue(any(f"row {MONARCH_ROWS}, cells 0 to 79" in line for line in lines), lines)

	def test_itIsShownWithEachSegmentOnItsOwnDisplay(self):
		"""Six rows and the rest would straddle the two; the messages take the whole Monarch
		and the focus is on the Focus 80, at its full width."""
		CONFIG["appLayouts"] = True
		answer = {"name": "chat", "segments": [{"size": 6, "show": FakeNavigatorObject("Messages")}, {"show": "focus"}]}
		focus = focusIn(LayoutAppModule(answer=answer))
		self.plugin.event_gainFocus(focus, lambda: None)
		callLaterQueue.fire()
		self.assertTrue(self.plugin.appLayouts.isUp)
		self.assertEqual(self.container.segmentForKey("appLayout.0").rect, SegmentRect(0, 0, MONARCH_ROWS, MONARCH_COLS))
		self.assertEqual(self.container.focusSegment.rect, SegmentRect(MONARCH_ROWS, 0, 1, 80))


class ScriptedAppModule:
	"""An app module whose layout is whatever the test says, for the focus it is given."""

	appName = "scripted"

	def __init__(self, answer):
		""":param answer: a function of the focus returning the layout, or the layout itself."""
		self.answer = answer
		self.asked = 0

	def brlMultilineLayout(self, focus, area):
		self.asked += 1
		return self.answer(focus) if callable(self.answer) else self.answer


def chatLayout(show, name="chat", focusRows=1):
	return {"name": name, "segments": [{"show": show}, {"size": focusRows, "show": "focus"}]}


class _ReusedWindow(FakeNavigatorObject):
	"""Equal to every other, as NVDA's objects for Outlook's one reading pane window are."""

	def __eq__(self, other):
		return isinstance(other, _ReusedWindow)

	__hash__ = object.__hash__


class TestShowingAppLayouts(PluginTestCase):
	"""Milestones 2 and 3 of `docs/design/app-layouts-plan.md`: the layout on the display,
	following the focus, and secondary segments kept current."""

	def setUp(self):
		super().setUp()
		CONFIG["appLayouts"] = True
		self.messages = FakeNavigatorObject("Messages", role="list")
		self.chat = ScriptedAppModule(chatLayout(self.messages))
		self.host = self.plugin.appLayouts

	def focus(self, appModule=None, name="Compose"):
		"""Move the focus, as NVDA does, into an application."""
		obj = FakeNavigatorObject(name, role="editableText")
		obj.appModule = appModule if appModule is not None else self.chat
		self.plugin.event_gainFocus(obj, lambda: None)
		return obj

	def settle(self):
		callLaterQueue.fire()

	def appKeys(self):
		return [spec.key for spec in self.container.specs if spec.key.startswith("appLayout.")]

	# Coming up.

	def test_nothingUntilTheFocusSettles(self):
		self.focus()
		self.assertFalse(self.host.isUp)
		self.settle()
		self.assertTrue(self.host.isUp)

	def test_theLayoutIsOnTheDisplay(self):
		"""The messages over seven rows of the Monarch, the focus on the last."""
		self.focus()
		self.settle()
		self.assertEqual(self.appKeys(), ["appLayout.0", "appLayout.1"])
		self.assertEqual(self.container.focusSegmentKey, "appLayout.1")
		self.assertEqual(self.container.segmentForKey("appLayout.0").rect, SegmentRect(0, 0, 7, MONARCH_COLS))
		self.assertEqual(self.container.focusSegment.rect, SegmentRect(7, 0, 1, MONARCH_COLS))

	def test_theSecondarySegmentShowsItsObject(self):
		self.focus()
		self.settle()
		monitor = self.host.liveMonitors()["appLayout.0"]
		self.assertIs(monitor.pinned, self.messages)
		self.assertIn("appLayout.0", self.plugin.everyMonitor())

	def test_theLayoutsSegmentsAreNotTheReadersPins(self):
		self.focus()
		self.settle()
		self.assertEqual(self.plugin.monitoredKeys, set())

	def test_nothingWhereTheSettingIsOff(self):
		CONFIG["appLayouts"] = False
		self.focus()
		self.settle()
		self.assertFalse(self.host.isUp)
		self.assertEqual(self.chat.asked, 0)

	def routedSegments(self):
		""":return: the keys of segments whose presses went to the object shown in them."""
		import brlMultiline.monitorRouting as monitorRouting

		routed = []
		original = monitorRouting.route
		monitorRouting.route = lambda segment, position: routed.append(segment.key) or True
		self.addCleanup(setattr, monitorRouting, "route", original)
		return routed

	def test_aPressInTheObjectsSegmentGoesToTheObject(self):
		self.focus()
		self.settle()
		routed = self.routedSegments()
		self.container.routeTo(0)
		self.assertEqual(routed, ["appLayout.0"])

	def test_aPressInTheFocusSegmentRoutesAsUsual(self):
		self.focus()
		self.settle()
		routed = self.routedSegments()
		focus = self.container.focusSegment.rect
		self.container.routeTo(focus.row * MONARCH_COLS)
		self.assertEqual(routed, [])

	def test_anApplicationWithoutAMethodCostsNothing(self):
		self.focus(appModule=object())
		self.assertEqual(callLaterQueue.pending, [])

	def test_aLayoutThatDoesNotFitIsNotShown(self):
		self.chat.answer = chatLayout(self.messages, focusRows=MONARCH_ROWS)
		self.focus()
		self.settle()
		self.assertFalse(self.host.isUp)
		self.assertEqual(self.container.numSegments, 4)

	def test_aBadAnswerIsNotShown(self):
		self.chat.answer = {"name": "chat"}
		self.focus()
		self.settle()
		self.assertFalse(self.host.isUp)

	# Following the focus.

	def test_theSameLayoutIsKeptAtOnce(self):
		"""Moving between the messages and the compose box rebuilds nothing."""
		self.focus()
		self.settle()
		panel = self.host.shown.panel
		container = self.container
		self.focus(name="A message")
		self.assertEqual(callLaterQueue.pending, [])
		self.assertIs(self.host.shown.panel, panel)
		self.assertIs(self.container, container)

	def test_aFocusWithNoLayoutTakesItDownOnceSettled(self):
		self.focus()
		self.settle()
		self.chat.answer = None
		self.focus(name="Toolbar")
		self.assertTrue(self.host.isUp)
		self.settle()
		self.assertFalse(self.host.isUp)
		self.assertEqual(self.appKeys(), [])
		self.assertEqual(self.container.numSegments, 4)

	def test_passingThroughSomewhereWithoutALayoutKeepsIt(self):
		"""Focus in a web application passes through containers on its way."""
		self.focus()
		self.settle()
		panel = self.host.shown.panel
		self.chat.answer = lambda focus: None if focus.name == "Container" else chatLayout(self.messages)
		self.focus(name="Container")
		self.focus(name="Compose")
		self.settle()
		self.assertIs(self.host.shown.panel, panel)

	def test_aDifferentLayoutReplacesIt(self):
		self.focus()
		self.settle()
		self.chat.answer = chatLayout(self.messages, name="channel", focusRows=2)
		self.focus(name="Channel post")
		self.settle()
		self.assertEqual(self.host.name, "channel")
		self.assertEqual(self.container.focusSegment.rect, SegmentRect(6, 0, 2, MONARCH_COLS))

	def test_leavingTheApplicationTakesItDownAtOnce(self):
		"""Without asking it and without waiting, so it cannot be left on another's display."""
		self.focus()
		self.settle()
		self.focus(appModule=object(), name="Elsewhere")
		self.assertFalse(self.host.isUp)
		self.assertEqual(self.container.numSegments, 4)

	def test_movingToAnotherApplicationWithALayoutShowsThatOne(self):
		self.focus()
		self.settle()
		other = ScriptedAppModule(chatLayout(FakeNavigatorObject("Inbox"), name="mail", focusRows=3))
		self.focus(appModule=other)
		self.assertFalse(self.host.isUp)
		self.settle()
		self.assertEqual(self.host.name, "mail")

	def test_turningTheSettingOffTakesItDown(self):
		self.focus()
		self.settle()
		CONFIG["appLayouts"] = False
		self.plugin.rebuildBuffer()
		callAfterQueue.flush()
		self.assertFalse(self.host.isUp)

	def test_theToggleTakesItDownAndBringsItBack(self):
		import api

		focus = self.focus()
		self.settle()
		self.plugin.script_toggleAppLayouts(None)
		self.assertFalse(self.host.isUp)
		original = api.getFocusObject
		api.getFocusObject = lambda: focus
		self.addCleanup(setattr, api, "getFocusObject", original)
		self.plugin.script_toggleAppLayouts(None)
		self.settle()
		self.assertTrue(self.host.isUp)

	def test_whileSetAsideTheFocusBringsNothing(self):
		self.plugin.script_toggleAppLayouts(None)
		self.focus()
		self.settle()
		self.assertFalse(self.host.isUp)

	# The reader's pins and the rest of the display.

	def test_aPinIsLeftWhereItIs(self):
		self.pin(0)
		self.focus()
		self.settle()
		self.assertEqual(self.plugin.monitoredKeys, {"display.0"})
		self.assertEqual(self.container.segmentForKey("display.0").rect, SegmentRect(0, 0, 2, MONARCH_COLS))
		self.assertEqual(self.container.segmentForKey("appLayout.0").rect, SegmentRect(2, 0, 5, MONARCH_COLS))

	def test_theLayoutsSegmentsCannotBePinned(self):
		self.focus()
		self.settle()
		self.pin(self.container.numberForKey("appLayout.0"))
		self.assertEqual(self.plugin.monitoredKeys, set())
		self.assertTrue(any("in use by" in message for message in spokenMessages))

	def test_theReaderGetsTheirArrangementBackWithThePin(self):
		self.pin(0)
		self.focus()
		self.settle()
		self.chat.answer = None
		self.focus()
		self.settle()
		self.assertEqual(self.container.numSegments, 4)
		self.assertEqual(self.plugin.monitoredKeys, {"display.0"})

	def test_theRoomIsTheSameWithTheLayoutUp(self):
		before = self.plugin.appLayoutRoom()
		self.focus()
		self.settle()
		after = self.plugin.appLayoutRoom()
		self.assertEqual(after, before)
		# The reader's own focus segment, not the layout's, which says the same display.

	def test_theFlowWaitsWithItsPlaceKept(self):
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		band = self.plugin.flowBand
		self.assertIsNotNone(band)
		self.focus()
		self.settle()
		self.assertTrue(self.host.isUp)
		self.assertIs(self.plugin.flowBand, band)
		self.assertIsNone(band.segment())
		self.chat.answer = None
		self.focus()
		self.settle()
		self.assertIs(self.plugin.flowBand, band)
		self.assertIsNotNone(band.segment())

	def flowingList(self, focusRows=3):
		""":return: the flow band, with a mail layout up whose focus asks for the flow."""
		CONFIG["flowEnabled"] = True
		self.plugin.rebuildBuffer()
		band = self.plugin.flowBand
		self.assertIsNotNone(band)
		self.chat.answer = {
			"name": "mail",
			"segments": [{"size": focusRows, "show": "focus", "flow": True}, {"show": self.messages}],
		}
		self.focus(name="Message list")
		self.settle()
		self.assertTrue(self.host.isUp)
		return band

	def test_theFlowDrawsInAFocusSegmentThatAsksForIt(self):
		band = self.flowingList()
		self.assertIs(self.plugin.flowBand, band)
		self.assertEqual(self.host.flowSegmentKey(), "appLayout.0")
		self.assertIs(band.segment(), self.container.segmentForKey("appLayout.0"))
		self.assertTrue(self.container.focusSegment.spec.ownerDrawsFocus)

	def test_theSameBandGoesBackToItsOwnSegment(self):
		band = self.flowingList()
		self.chat.answer = None
		self.focus()
		self.settle()
		self.assertFalse(self.host.isUp)
		self.assertIs(self.plugin.flowBand, band)
		self.assertEqual(band.segment().key, "flow")

	def test_oneRowLeavesTheFlowWaiting(self):
		band = self.flowingList(focusRows=1)
		self.assertIsNone(self.host.flowSegmentKey())
		self.assertIsNone(band.segment())

	def test_withoutTheFlowTheFocusSegmentIsWrittenAsUsual(self):
		self.chat.answer = {
			"name": "mail",
			"segments": [{"size": 3, "show": "focus", "flow": True}, {"show": self.messages}],
		}
		self.focus(name="Message list")
		self.settle()
		self.assertIsNone(self.plugin.flowBand)
		segment = self.container.segmentForKey("appLayout.0")
		self.assertFalse(segment.acceptFocusRegions([object()]))

	def test_aRebuildThatDropsTheClaimForgetsTheLayout(self):
		self.focus()
		self.settle()
		self.plugin._activePanels = []
		self.plugin.rebuildBuffer()
		self.assertIsNone(self.host.shown)

	def test_theReportSaysWhatIsUp(self):
		focus = self.focus()
		self.settle()
		self.assertIn("Shown now: 'chat'", self.plugin.appLayoutReport(focus))

	def test_terminatingWithALayoutUpIsClean(self):
		self.focus()
		self.settle()
		self.plugin.terminate()
		self.assertIsNone(self.host.shown)

	# Secondary segments kept current.

	def resolvedLayout(self, pane):
		""":return: a mail layout whose reading pane is asked for each time."""
		self.pane = pane
		self.chat.answer = {
			"name": "mail",
			"segments": [{"size": 5, "show": lambda: self.pane}, {"show": "focus"}],
		}

	def test_aCallableIsShown(self):
		first = FakeNavigatorObject("Message one", role="document")
		self.resolvedLayout(first)
		self.focus()
		self.settle()
		self.assertIs(self.host.liveMonitors()["appLayout.0"].pinned, first)

	def test_aNewAnswerReplacesItOnTheNextFocus(self):
		"""Selecting another message replaces the reading pane's document."""
		self.resolvedLayout(FakeNavigatorObject("Message one", role="document"))
		self.focus()
		self.settle()
		second = FakeNavigatorObject("Message two", role="document")
		self.pane = second
		self.focus(name="Message two in the list")
		self.assertIs(self.host.liveMonitors()["appLayout.0"].pinned, second)

	def test_theSameAnswerKeepsTheReadersPlace(self):
		self.resolvedLayout(FakeNavigatorObject("Message one", role="document"))
		self.focus()
		self.settle()
		monitor = self.host.liveMonitors()["appLayout.0"]
		self.focus(name="Compose")
		self.assertIs(self.host.liveMonitors()["appLayout.0"], monitor)

	def reusedPane(self, name: str, key):
		""":return: a reading pane that compares equal to every other, as Outlook's reused window does."""
		pane = _ReusedWindow(name, role="document")
		if key is not None:
			pane.brlMultilineShowKey = key
		return pane

	def test_aDifferentShowKeyReplacesAReusedWindow(self):
		"""Outlook keeps one document window for every message: the key tells them apart."""
		self.resolvedLayout(self.reusedPane("Message one", "row 1"))
		self.focus()
		self.settle()
		second = self.reusedPane("Message two", "row 2")
		self.pane = second
		self.focus(name="Message two in the list")
		self.assertIs(self.host.liveMonitors()["appLayout.0"].pinned, second)

	def test_theSameShowKeyKeepsTheReadersPlace(self):
		self.resolvedLayout(self.reusedPane("Message one", "row 1"))
		self.focus()
		self.settle()
		monitor = self.host.liveMonitors()["appLayout.0"]
		self.pane = self.reusedPane("Message one", "row 1")
		self.focus()
		self.assertIs(self.host.liveMonitors()["appLayout.0"], monitor)

	def test_aKeyOnOneAnswerOnlyIsIgnored(self):
		self.resolvedLayout(self.reusedPane("Message one", None))
		self.focus()
		self.settle()
		monitor = self.host.liveMonitors()["appLayout.0"]
		self.pane = self.reusedPane("Message one", "row 1")
		self.focus()
		self.assertIs(self.host.liveMonitors()["appLayout.0"], monitor)

	def test_theSameKeyDoesNotKeepAReplacedObject(self):
		import brlMultiline.appLayoutHost as host

		first = FakeNavigatorObject("Message one", role="document")
		second = FakeNavigatorObject("Message two", role="document")
		first.brlMultilineShowKey = second.brlMultilineShowKey = "row 1"
		self.assertFalse(host.sameAnswer(first, "row 1", second))

	def test_theKeyShownIsKeptRatherThanReadAgain(self):
		"""Review of 64197a4: one Python object, its key changed in place."""
		import brlMultiline.appLayoutHost as host

		pane = FakeNavigatorObject("Message one", role="document")
		pane.brlMultilineShowKey = "message B"
		self.assertFalse(host.sameAnswer(pane, "message A", pane))
		self.assertTrue(host.sameAnswer(pane, "message B", pane))

	def test_aNewAnswerIsPickedUpOnTheClock(self):
		"""The pane loads after the selection does, with no focus change to say so."""
		self.resolvedLayout(None)
		self.focus()
		self.settle()
		self.assertNotIn("appLayout.0", self.host.liveMonitors())
		loaded = FakeNavigatorObject("Message one", role="document")
		self.pane = loaded
		self.host._resolvedAt = 0.0
		self.plugin.refreshMonitorsInTurn(1.0)
		self.assertIs(self.host.liveMonitors()["appLayout.0"].pinned, loaded)

	def test_theClockAsksNoMoreOftenThanItShould(self):
		self.resolvedLayout(FakeNavigatorObject("Message one", role="document"))
		self.focus()
		self.settle()
		self.pane = FakeNavigatorObject("Message two", role="document")
		self.host.resolveIfDue()
		self.assertEqual(self.host.liveMonitors()["appLayout.0"].pinned.name, "Message one")

	def test_noneBlanksTheSegment(self):
		self.resolvedLayout(FakeNavigatorObject("Message one", role="document"))
		self.focus()
		self.settle()
		self.pane = None
		self.focus()
		self.assertNotIn("appLayout.0", self.host.liveMonitors())
		self.assertEqual(self.container.segmentForKey("appLayout.0").regions, [])

	def test_aCallableThatKeepsRaisingTakesTheLayoutDown(self):
		self.failing = False

		def pane():
			if self.failing:
				raise RuntimeError("COM failed")
			return FakeNavigatorObject("Message one", role="document")

		self.chat.answer = {"name": "mail", "segments": [{"size": 5, "show": pane}, {"show": "focus"}]}
		self.focus()
		self.settle()
		self.failing = True
		self.focus()
		self.assertTrue(self.host.isUp, "one failure keeps what was shown")
		self.focus()
		self.focus()
		self.assertFalse(self.host.isUp)


class TestAppLayoutsKeepTheFocusOnItsDisplay(PluginTestCase):
	"""A Focus 80 above a Monarch, the order the reader had when this was found."""

	segmentCount = 1

	def makeHandler(self):
		handler = FakeHandler(MONARCH_ROWS + 1, 80)
		handler.display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("hidBrailleStandard", 1, MONARCH_ROWS, MONARCH_COLS),
		)
		return handler

	def show(self):
		CONFIG["appLayouts"] = True
		answer = {
			"name": "chat",
			"segments": [{"show": FakeNavigatorObject("Messages")}, {"size": 2, "show": "focus"}],
		}
		self.plugin.event_gainFocus(focusIn(LayoutAppModule(answer=answer)), lambda: None)
		callLaterQueue.fire()
		self.assertTrue(self.plugin.appLayouts.isUp)

	def test_theFocusStaysOnTheFocus80(self):
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.focusSegment.rect, SegmentRect(0, 0, 1, 80))
		self.show()
		self.assertEqual(self.container.focusSegment.rect, SegmentRect(0, 0, 1, 80))
		self.assertEqual(
			self.container.segmentForKey("appLayout.0").rect,
			SegmentRect(1, 0, MONARCH_ROWS, MONARCH_COLS),
		)

	def test_aMonarchHoldingTheFocusLeavesTheFocus80Alone(self):
		CONFIG["focusSegment"] = -1
		self.plugin.rebuildBuffer()
		self.assertEqual(self.container.focusSegment.rect.row, 1)
		before = [spec.key for spec in self.container.specs if spec.rect.row == 0]
		self.show()
		self.assertEqual(
			self.container.segmentForKey("appLayout.0").rect,
			SegmentRect(1, 0, MONARCH_ROWS - 2, MONARCH_COLS),
		)
		self.assertEqual(self.container.focusSegment.rect, SegmentRect(MONARCH_ROWS - 1, 0, 2, MONARCH_COLS))
		self.assertEqual([spec.key for spec in self.container.specs if spec.rect.row == 0], before)


class TestSpreadingAppLayouts(TestAppLayoutsKeepTheFocusOnItsDisplay):
	"""With the Monarch holding the focus, spreading sends the history to the Focus 80."""

	def setUp(self):
		super().setUp()
		CONFIG["focusSegment"] = -1
		self.plugin.rebuildBuffer()

	def test_theSettingSpreadsIt(self):
		CONFIG["appLayoutSpread"] = True
		self.show()
		self.assertEqual(self.container.segmentForKey("appLayout.0").rect, SegmentRect(0, 0, 1, 80))
		self.assertEqual(self.container.focusSegment.rect, SegmentRect(1, 0, MONARCH_ROWS, MONARCH_COLS))

	def test_theCommandPlacesItAgainAtOnce(self):
		import api

		self.show()
		self.assertEqual(self.container.focusSegment.rect.numRows, 2)
		focus = focusIn(self.plugin.appLayouts.shown.appModule)
		original = api.getFocusObject
		api.getFocusObject = lambda: focus
		self.addCleanup(setattr, api, "getFocusObject", original)
		self.plugin.script_toggleAppLayoutSpread(None)
		self.assertEqual(spokenMessages[-1], "App layouts spread across displays")
		self.assertTrue(bmConfig.shouldSpreadAppLayouts())
		self.assertEqual(self.container.segmentForKey("appLayout.0").rect, SegmentRect(0, 0, 1, 80))
		self.plugin.script_toggleAppLayoutSpread(None)
		self.assertEqual(spokenMessages[-1], "App layouts kept on the focus display")
		self.assertEqual(self.container.focusSegment.rect, SegmentRect(MONARCH_ROWS - 1, 0, 2, MONARCH_COLS))

	def test_aSettingChangedElsewherePlacesItAgainAfterTheRebuild(self):
		import api

		self.show()
		focus = focusIn(self.plugin.appLayouts.shown.appModule)
		original = api.getFocusObject
		api.getFocusObject = lambda: focus
		self.addCleanup(setattr, api, "getFocusObject", original)
		CONFIG["appLayoutSpread"] = True
		self.plugin.rebuildBuffer()
		callAfterQueue.flush()
		self.assertEqual(self.container.segmentForKey("appLayout.0").rect, SegmentRect(0, 0, 1, 80))

	def test_theReportSaysSo(self):
		CONFIG["appLayoutSpread"] = True
		CONFIG["appLayouts"] = True
		lines = self.plugin.appLayoutReport(focusIn(LayoutAppModule()))
		self.assertIn("Spread across displays: yes", lines)
		self.assertTrue(any(line.startswith("Focus display: rows 1 to") for line in lines), lines)

	def test_theCommandWithNoLayoutUpOnlyChangesTheSetting(self):
		self.plugin.script_toggleAppLayoutSpread(None)
		self.assertTrue(bmConfig.shouldSpreadAppLayouts())
		self.assertFalse(self.plugin.appLayouts.isUp)


class TestAppLayoutReviewCa7f5fc(PluginTestCase):
	"""What review of ca7f5fc found, each reproduced before it was fixed."""

	def setUp(self):
		super().setUp()
		import api

		CONFIG["appLayouts"] = True
		self.messages = FakeNavigatorObject("Messages", role="list")
		self.app = ScriptedAppModule(chatLayout(self.messages))
		self.focus = focusIn(self.app)
		original = api.getFocusObject
		api.getFocusObject = lambda: self.focus
		self.addCleanup(setattr, api, "getFocusObject", original)
		self.addCleanup(setSpeechOutputMode, False)

	def offer(self):
		self.plugin.event_gainFocus(self.focus, lambda: None)

	def show(self):
		self.offer()
		callLaterQueue.fire()
		self.assertTrue(self.plugin.appLayouts.isUp)

	def test_aPinMadeWhileTheLayoutWaitsIsKept(self):
		"""The room is worked out when the focus settles, not when it arrived."""
		self.offer()
		self.pin(0, "Clock")
		pin = self.plugin._monitors["display.0"]
		callLaterQueue.fire()
		self.assertTrue(self.plugin.appLayouts.isUp)
		self.assertIs(self.plugin._monitors.get("display.0"), pin)
		self.assertEqual(self.container.segmentForKey("appLayout.0").rect.row, 2)

	def test_aClaimMadeWhileTheLayoutWaitsIsKept(self):
		self.offer()
		self.plugin.activatePanel(SinglePanel("readerPanel", SegmentRect(0, 0, 2, MONARCH_COLS)))
		callLaterQueue.fire()
		self.assertTrue(self.container.hasKey("readerPanel"))

	def test_anUnreadableReplacementIsTriedAgainAndThenGivenUp(self):
		import brlMultiline.appLayoutHost as host

		self.current = self.messages
		self.app.answer = chatLayout(lambda: self.current)
		self.show()

		class Vanished:
			@property
			def name(self):
				raise RuntimeError("UIA element is no longer available")

		self.current = Vanished()
		self.offer()
		self.assertTrue(self.plugin.appLayouts.isUp, "one failure keeps what was shown")
		self.assertIs(self.plugin.appLayouts.shown.monitors["appLayout.0"].pinned, self.messages)
		for _attempt in range(host.MAX_FAILURES):
			self.plugin.appLayouts.resolve()
		self.assertIsNone(self.plugin.appLayouts.shown)

	def test_anUnreadableReplacementThatRecoversIsShown(self):
		self.current = self.messages
		self.app.answer = chatLayout(lambda: self.current)
		self.show()

		class Vanished:
			@property
			def name(self):
				raise RuntimeError("gone")

		self.current = Vanished()
		self.offer()
		recovered = FakeNavigatorObject("New message", role="list")
		self.current = recovered
		self.offer()
		self.assertIs(self.plugin.appLayouts.shown.monitors["appLayout.0"].pinned, recovered)

	def test_startingALayoutDrawsNothingBesideSpeech(self):
		setSpeechOutputMode(True)
		self.offer()
		callLaterQueue.fire()
		self.assertEqual(self.container.segmentForKey("appLayout.0").regions, [])
		self.assertIn("appLayout.0", self.plugin.appLayouts.shown.monitors)

	def test_aReplacementDrawsNothingBesideSpeech(self):
		self.current = self.messages
		self.app.answer = chatLayout(lambda: self.current)
		self.show()
		setSpeechOutputMode(True)
		self.current = FakeNavigatorObject("New message", role="list")
		self.offer()
		self.assertEqual(self.container.segmentForKey("appLayout.0").regions, [])

	def test_itIsDrawnWhenBrailleIsBack(self):
		setSpeechOutputMode(True)
		self.show()
		setSpeechOutputMode(False)
		self.plugin.refreshMonitorsInTurn(1.0)
		self.assertNotEqual(self.container.segmentForKey("appLayout.0").regions, [])

	def test_askingTheApplicationSharesTheRefreshAllowance(self):
		from unittest import mock

		import brlMultiline.appLayoutHost as host

		clock = [100.0]
		calls = []
		armed = [False]

		def pane(index):
			if armed[0]:
				calls.append(index)
				clock[0] += 0.02
			return None

		self.app.answer = {
			"name": "three panes",
			"segments": [{"size": 1, "show": lambda index=index: pane(index)} for index in range(3)]
			+ [{"show": "focus"}],
		}
		self.show()
		armed[0] = True
		self.plugin.appLayouts._resolvedAt = 0.0
		with mock.patch.object(host.time, "monotonic", side_effect=lambda: clock[0]):
			self.assertTrue(self.plugin.refreshMonitorsInTurn(0.03))
			self.assertEqual(calls, [0, 1])
			self.assertFalse(self.plugin.refreshMonitorsInTurn(0.03))
		self.assertEqual(calls, [0, 1, 2], "the next pass carries on where the last stopped")


class TestTheFocusDisplayCommandMovesALayout(PluginTestCase):
	"""Moving the focus to another display with a layout up moves the layout's focus too."""

	segmentCount = 1

	def makeHandler(self):
		handler = FakeHandler(MONARCH_ROWS + 1, 80)
		handler.display = fakeVirtualDisplay(
			("freedomScientific", 0, 1, 80),
			("hidBrailleStandard", 1, MONARCH_ROWS, MONARCH_COLS),
		)
		return handler

	def test_theFocusGoesWhereTheCommandSays(self):
		import api

		CONFIG["appLayouts"] = True
		CONFIG["focusSegment"] = 0
		self.plugin.rebuildBuffer()
		focus = focusIn(ScriptedAppModule(chatLayout(FakeNavigatorObject("Messages"))))
		original = api.getFocusObject
		api.getFocusObject = lambda: focus
		self.addCleanup(setattr, api, "getFocusObject", original)
		self.plugin.event_gainFocus(focus, lambda: None)
		callLaterQueue.fire()
		self.assertEqual(self.container.focusSegment.rect.row, 0)
		self.plugin.script_changeFocusTrackingDisplay(None)
		callAfterQueue.flush()
		self.assertGreaterEqual(self.container.focusSegment.rect.row, 1)
		self.assertTrue(self.plugin.appLayouts.isUp)


class TestTellingTheLayoutItChanged(PluginTestCase):
	"""An app module saying its content changed, and the layout not waiting while the reader types."""

	def setUp(self):
		super().setUp()
		CONFIG["appLayouts"] = True
		self.chat = ScriptedAppModule(chatLayout(FakeNavigatorObject("Messages", role="list")))
		focus = FakeNavigatorObject("Compose", role="editableText")
		focus.appModule = self.chat
		self.plugin.event_gainFocus(focus, lambda: None)
		callLaterQueue.fire()
		self.assertTrue(self.plugin.appLayouts.isUp)
		self.reads = []
		monitor = self.plugin.appLayouts.liveMonitors()["appLayout.0"]
		original = monitor.refresh
		monitor.refresh = lambda reveal=False: (self.reads.append(reveal), original(reveal=reveal))

	def test_aChangeIsReadAMomentLater(self):
		self.plugin.brlMultilineContentChanged(self.chat)
		self.assertEqual(self.reads, [])
		callLaterQueue.fire()
		self.assertEqual(self.reads, [False])

	def test_aBurstIsReadOnce(self):
		for _ in range(5):
			self.plugin.brlMultilineContentChanged(self.chat)
		self.assertEqual(len(callLaterQueue.pending), 1)
		callLaterQueue.fire()
		self.assertEqual(len(self.reads), 1)

	def test_anotherApplicationIsIgnored(self):
		self.plugin.brlMultilineContentChanged(object())
		self.assertEqual(callLaterQueue.pending, [])

	def test_noneMeansWhicheverIsUp(self):
		self.plugin.brlMultilineContentChanged()
		callLaterQueue.fire()
		self.assertEqual(len(self.reads), 1)

	def test_nothingIsReadBesideSpeech(self):
		setSpeechOutputMode(True)
		self.addCleanup(setSpeechOutputMode, False)
		self.plugin.brlMultilineContentChanged(self.chat)
		callLaterQueue.fire()
		self.assertEqual(self.reads, [])

	def test_takingTheLayoutDownForgetsAWaitingChange(self):
		self.plugin.brlMultilineContentChanged(self.chat)
		self.plugin.appLayouts.takeDown("test")
		self.assertEqual(callLaterQueue.pending, [])

	def test_withNoLayoutUpItDoesNothing(self):
		self.plugin.appLayouts.takeDown("test")
		self.plugin.brlMultilineContentChanged(self.chat)
		self.assertEqual(callLaterQueue.pending, [])

	def test_theLayoutAloneIsRefreshedWhileTheReaderTypes(self):
		"""The reader's own pins wait for the keystroke; the layout's segments beside it do not."""
		self.plugin.appLayouts.takeDown("to pin first")
		self.pin(0)
		focus = FakeNavigatorObject("Compose", role="editableText")
		focus.appModule = self.chat
		self.plugin.event_gainFocus(focus, lambda: None)
		callLaterQueue.fire()
		layoutReads = []
		monitor = self.plugin.appLayouts.liveMonitors()["appLayout.0"]
		monitor.refresh = lambda reveal=False: layoutReads.append(reveal)
		pinReads = []
		self.plugin._monitors["display.0"].refresh = lambda reveal=False: pinReads.append(reveal)
		self.plugin.refreshAppLayoutInTurn(1.0)
		self.assertEqual(layoutReads, [False])
		self.assertEqual(pinReads, [])

	def test_theTimerRefreshesTheLayoutWhileTheReaderTypes(self):
		import time

		patches._lastMonitorRefresh = 0.0
		patches._monitorsUnfinished = False
		patches._monitorsRefreshedAt = time.monotonic()
		patches._layoutRefreshedAt = 0.0
		self.addCleanup(setattr, patches, "_monitorsRefreshedAt", 0.0)
		self.addCleanup(setattr, patches, "_layoutRefreshedAt", 0.0)
		patches._refreshPinnedObjects(busy=True)
		self.assertEqual(len(self.reads), 1)
		# And not on every keystroke after that: the ordinary interval still holds.
		patches._refreshPinnedObjects(busy=True)
		self.assertEqual(len(self.reads), 1)


class TestAppLayoutReviewBdd4c49(PluginTestCase):
	"""What review of bdd4c49 found: replacement that did not roll back, and refreshes that were
	not fair or not budgeted. Each reproduced before it was fixed."""

	def setUp(self):
		super().setUp()
		import api

		CONFIG["appLayouts"] = True
		self.messages = FakeNavigatorObject("Message A", role="list")
		self.app = ScriptedAppModule(chatLayout(self.messages))
		self.focus = focusIn(self.app)
		original = api.getFocusObject
		api.getFocusObject = lambda: self.focus
		self.addCleanup(setattr, api, "getFocusObject", original)
		self.host = self.plugin.appLayouts

	def show(self):
		self.plugin.event_gainFocus(self.focus, lambda: None)
		callLaterQueue.fire()
		self.assertTrue(self.host.isUp)

	def threePanes(self):
		self.app.answer = {
			"name": "three panes",
			"segments": [{"size": 1, "show": FakeNavigatorObject(f"Pane {index}")} for index in range(3)]
			+ [{"show": "focus"}],
		}
		self.show()

	def slowReads(self, clock, reads, seconds):
		for index, monitor in enumerate(self.host.liveMonitors().values()):

			def read(reveal=False, index=index):
				reads.append(index)
				clock[0] += seconds

			monitor.refresh = read

	def test_aFailedDrawPutsTheOldMonitorBack(self):
		from unittest import mock

		self.current = self.messages
		self.app.answer = chatLayout(lambda: self.current)
		self.show()
		before = self.host.shown.monitors["appLayout.0"]
		self.current = FakeNavigatorObject("Message B", role="list")
		with mock.patch.object(self.container, "update", side_effect=RuntimeError("display update failed")):
			self.host.resolve()
		self.assertTrue(self.host.isUp)
		self.assertIs(self.host.shown.monitors["appLayout.0"], before)
		self.assertIs(self.host.shown.objects["appLayout.0"], self.messages)
		self.current = self.messages
		for _attempt in range(4):
			self.host.resolve()
		self.assertIs(self.host.shown.monitors["appLayout.0"].pinned, self.messages)

	def test_aFailedDrawIsTriedAgain(self):
		from unittest import mock

		self.current = self.messages
		self.app.answer = chatLayout(lambda: self.current)
		self.show()
		second = FakeNavigatorObject("Message B", role="list")
		self.current = second
		with mock.patch.object(self.container, "update", side_effect=RuntimeError("display update failed")):
			self.host.resolve()
		self.host.resolve()
		self.assertIs(self.host.shown.monitors["appLayout.0"].pinned, second)

	def test_thePassWhileTypingTakesTheSegmentsInTurn(self):
		from unittest import mock

		import brlMultiline.appLayoutHost as host

		self.threePanes()
		clock = [1000.0]
		reads = []
		self.host._resolvedAt = clock[0]
		self.slowReads(clock, reads, 0.04)
		with mock.patch.object(host.time, "monotonic", side_effect=lambda: clock[0]):
			for _tick in range(3):
				self.plugin.refreshAppLayoutInTurn(0.03)
		self.assertEqual(reads, [0, 1, 2])

	def test_theTimerCarriesOnAnUnfinishedPassWhileTypingAtOnce(self):
		from unittest import mock

		import brlMultiline.appLayoutHost as host

		self.show()
		clock = [100.5]
		with (
			mock.patch.multiple(
				patches,
				_lastMonitorRefresh=0.0,
				_monitorsUnfinished=False,
				_monitorsRefreshedAt=100.0,
				_layoutRefreshedAt=0.0,
				_layoutUnfinished=False,
			),
			mock.patch.object(host.time, "monotonic", side_effect=lambda: clock[0]),
			mock.patch.object(self.plugin, "refreshAppLayoutInTurn", return_value=True) as refresh,
		):
			patches._refreshPinnedObjects(busy=True)
			clock[0] += 0.01
			patches._refreshPinnedObjects(busy=True)
			self.assertEqual(refresh.call_count, 2)

	def test_aReplacementIsReadOnceForOneChange(self):
		from unittest import mock

		import brlMultiline.appLayoutHost as host

		self.current = self.messages
		self.app.answer = chatLayout(lambda: self.current)
		self.show()
		self.current = FakeNavigatorObject("Message B", role="list")
		reads = []
		original = host.ObjectMonitor.refresh

		def refresh(monitor, reveal=False):
			if monitor.pinned is self.current:
				reads.append(reveal)
			return original(monitor, reveal=reveal)

		with mock.patch.object(host.ObjectMonitor, "refresh", new=refresh):
			self.plugin.brlMultilineContentChanged(self.app)
			callLaterQueue.fire()
		self.assertEqual(reads, [True])

	def test_aChangeIsReadWithinASliceAndCarriedOn(self):
		from unittest import mock

		import brlMultiline.appLayoutHost as host

		self.threePanes()
		clock = [1000.0]
		reads = []
		self.host._resolvedAt = clock[0]
		self.slowReads(clock, reads, 0.04)
		with mock.patch.object(host.time, "monotonic", side_effect=lambda: clock[0]):
			self.plugin.brlMultilineContentChanged(self.app)
			callLaterQueue.fire()
			self.assertEqual(reads, [0])
			callLaterQueue.fire()
			callLaterQueue.fire()
		self.assertEqual(reads, [0, 1, 2], "carried on promptly, one slice at a time")

	def test_aSliceTheCallablesSpentStartsNoRead(self):
		from unittest import mock

		import brlMultiline.appLayoutHost as host

		clock = [1000.0]
		armed = [False]
		reads = []

		def pane():
			if armed[0]:
				clock[0] += 0.04
			return self.messages

		self.app.answer = {"name": "focus then pane", "segments": [{"show": "focus"}, {"size": 2, "show": pane}]}
		self.show()
		self.host.liveMonitors()["appLayout.1"].refresh = lambda reveal=False: reads.append(True)
		self.host._resolvedAt = 0.0
		armed[0] = True
		with mock.patch.object(host.time, "monotonic", side_effect=lambda: clock[0]):
			self.assertTrue(self.plugin.refreshMonitorsInTurn(0.03))
			self.assertEqual(reads, [])
			self.assertFalse(self.plugin.refreshMonitorsInTurn(0.03))
		self.assertEqual(reads, [True], "read on the next pass")


class TestTheOtherDisplaysDuringAMessage(PluginTestCase):
	"""A flash message on the Focus 80 leaves the Monarch beside it live and pannable."""

	segmentCount = 1

	def makeHandler(self):
		handler = FakeHandler(MONARCH_ROWS + 1, 80)
		handler.display = fakeVirtualDisplay(
			("hidBrailleStandard", 0, MONARCH_ROWS, MONARCH_COLS),
			("freedomScientific", MONARCH_ROWS, 1, 80),
		)
		return handler

	def setUp(self):
		super().setUp()
		self.message = self.handler.messageBuffer
		self.assertEqual(self.message.rect.row, MONARCH_ROWS, "the message goes beside the focus")

	def showAMessage(self):
		self.message.clear()
		region = Region("a long message")
		region.update()
		self.message.regions.append(region)
		self.message.update()
		self.handler.buffer = self.message
		self.handler._messageCallLater = FakeCallLater()

	def monarchKey(self):
		return next(spec.key for spec in self.container.specs if spec.rect.row == 0)

	def panAsNVDA(self, source, forward=True):
		"""NVDA's own panning command, run from a key on one display."""
		from unittest import mock

		calls = []
		original = patches._originals["scrollForward" if forward else "scrollBack"]
		with mock.patch.dict(
			patches._originals,
			{"scrollForward" if forward else "scrollBack": lambda handler: calls.append("message")},
		):
			previous = panning._activeSource
			panning._activeSource = source
			try:
				patches._nativeScroll(self.handler, forward)
			finally:
				panning._activeSource = previous
		del original
		return calls

	def test_aRedrawOfTheContainerReachesTheDisplay(self):
		"""What a pin refresh or a new chat message on the Monarch ends in."""
		self.showAMessage()
		before = self.handler.updateCount
		self.container.updateDisplay()
		self.assertEqual(self.handler.updateCount, before + 1)

	def test_aSegmentRedrawReachesTheDisplay(self):
		self.showAMessage()
		segment = self.container.segmentForKey(self.monarchKey())
		self.assertIs(segment.handler.buffer, segment)
		before = self.handler.updateCount
		segment.updateDisplay()
		self.assertEqual(self.handler.updateCount, before + 1)

	def test_nvdasOwnMessageBufferStillHoldsRedrawsBack(self):
		"""It blanks everything around a message, so a redraw of the container would be lost."""
		self.handler.buffer = types.SimpleNamespace()
		before = self.handler.updateCount
		self.container.updateDisplay()
		self.assertEqual(self.handler.updateCount, before)

	def test_withNoMessageNothingChanges(self):
		before = self.handler.updateCount
		self.container.updateDisplay()
		self.assertEqual(self.handler.updateCount, before + 1)

	def test_theMonarchsKeysPanTheMonarch(self):
		from unittest import mock

		self.showAMessage()
		with mock.patch.object(self.container, "scrollForward") as scrolled:
			calls = self.panAsNVDA("hidBrailleStandard")
		self.assertEqual(calls, [], "the message is left alone, and so is its timeout")
		scrolled.assert_called_once_with(self.container.numberForKey(self.monarchKey()))

	def test_theFocus80sKeysPanTheMessage(self):
		from unittest import mock

		self.showAMessage()
		with mock.patch.object(self.container, "scrollForward") as scrolled:
			calls = self.panAsNVDA("freedomScientific")
		self.assertEqual(calls, ["message"])
		scrolled.assert_not_called()

	def test_aScrollWithNoKeyBehindItPansTheMessage(self):
		"""Automatic scroll, or anything calling the handler directly, is NVDA's as before."""
		self.showAMessage()
		self.assertEqual(self.panAsNVDA(None), ["message"])

	def test_aMessageOnTheMonarchIsPannedFromTheMonarch(self):
		from unittest import mock

		CONFIG["messageSegment"] = 0
		self.plugin.rebuildBuffer()
		self.assertEqual(self.message.rect.row, 0)
		self.showAMessage()
		with mock.patch.object(self.container, "scrollForward") as scrolled:
			self.assertEqual(self.panAsNVDA("hidBrailleStandard"), ["message"])
			self.assertEqual(self.panAsNVDA("freedomScientific"), [])
		scrolled.assert_called_once()


if __name__ == "__main__":
	unittest.main()
