# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for putting a picture from the screen on the display.

Two things are being checked here that the arithmetic tests cannot see.

The first is the seam: `imageSource` is the only module in the image path that knows NVDA
exists, and everything it refuses has to come back as words rather than as an exception nobody
catches — a reader who has pointed at a spacer image and pressed the key needs to hear that it
is too small, not to have the command do nothing.

The second is that a picture is the first figure with an up and a down. Every drawing before it
either magnified, or redrew itself for a range of periods whose value axis was refitted each
time, so the graphics mode had never had to window vertically at all. Half a photograph is
still half a photograph, and the other half is above or below it.
"""

import os
import re
import sys
import types
import unittest

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

import api  # noqa: E402

from brlMultiline import image as imageFigure, imagePins, imageSource  # noqa: E402


PANEL = (48, 20)


class FakePixel:
	def __init__(self, grey):
		self.rgbRed = grey
		self.rgbGreen = grey
		self.rgbBlue = grey


class FakeScreenBitmap:
	"""`screenBitmap.ScreenBitmap`, reduced to what the capture asks of it."""

	asked = []

	def __init__(self, width, height):
		self.width = width
		self.height = height

	def captureImage(self, x, y, w, h):
		FakeScreenBitmap.asked.append((x, y, w, h, self.width, self.height))
		# A dark square in the middle of a light field, so that whatever was asked for comes
		# back as something with a shape in it rather than as a refusal.
		rows = []
		for row in range(self.height):
			rows.append(
				[
					FakePixel(
						0
						if (
							self.width // 4 <= column < 3 * self.width // 4
							and self.height // 4 <= row < 3 * self.height // 4
						)
						else 255,
					)
					for column in range(self.width)
				],
			)
		return rows


class FakeObject:
	"""What the navigator object needs to be for a capture."""

	def __init__(self, location=(10, 20, 200, 100), name="", description="", role="graphic"):
		self.location = location
		self.name = name
		self.description = description
		self.role = role


def newBuffer(width, height):
	return PinBuffer(width, height)


def picture(width=96, height=96, name="a picture"):
	""":return: a capture of a dark square on a light field."""
	size = min(width, height) // 3
	left = (width - size) // 2
	top = (height - size) // 2
	greys = bytearray()
	for y in range(height):
		for x in range(width):
			greys.append(0 if (left <= x < left + size and top <= y < top + size) else 255)
	return imagePins.Picture(greys, width, height, name)


class ScreenCapture(unittest.TestCase):
	"""A base that puts a fake screen and a fake navigator object in place."""

	def setUp(self):
		FakeScreenBitmap.asked = []
		sys.modules["screenBitmap"] = types.SimpleNamespace(ScreenBitmap=FakeScreenBitmap)
		self.navigator = FakeObject()
		self._realNavigator = api.getNavigatorObject
		api.getNavigatorObject = lambda: self.navigator

	def tearDown(self):
		api.getNavigatorObject = self._realNavigator
		sys.modules.pop("screenBitmap", None)


class TestFindingSomethingToDraw(ScreenCapture):
	"""Which object, and what is refused rather than drawn."""

	def test_theNavigatorObjectIsWhatIsDrawn(self):
		"""The whole reason nothing has to be aimed: arrowing a web page onto a graphic is
		what sets the review position, which is what the navigator object comes back from."""
		found = imageSource.captureNavigator()
		self.assertEqual(FakeScreenBitmap.asked[0][:4], (10, 20, 200, 100))
		self.assertTrue(found.width)

	def test_somethingWithNoLocationIsRefusedWithAReason(self):
		self.navigator.location = None
		with self.assertRaises(imagePins.ImageRefused) as refused:
			imageSource.captureNavigator()
		self.assertIn("screen", str(refused.exception))

	def test_somethingTooSmallIsRefusedWithItsSize(self):
		"""A bullet, a spacer, a one pixel tracking image. Saying which is what lets the
		reader decide whether to move or to give up."""
		self.navigator.location = (0, 0, 4, 4)
		with self.assertRaises(imagePins.ImageRefused) as refused:
			imageSource.captureNavigator()
		self.assertIn("4", str(refused.exception))

	def test_nothingAtAllIsRefusedWithAReason(self):
		api.getNavigatorObject = lambda: None
		with self.assertRaises(imagePins.ImageRefused):
			imageSource.captureNavigator()

	def test_aScreenThatWillNotBeCopiedIsRefusedRatherThanRaised(self):
		class Broken:
			def __init__(self, width, height):
				raise OSError("no device context")

		sys.modules["screenBitmap"] = types.SimpleNamespace(ScreenBitmap=Broken)
		with self.assertRaises(imagePins.ImageRefused):
			imageSource.captureNavigator()

	def test_aRoleIsNotRestricted(self):
		"""A diagram in a canvas, a map, a floor plan. Half of them report a role that says
		nothing, and a reader who has pointed at one and pressed the key has said what they
		want more clearly than a role would."""
		self.navigator.role = "unknown"
		self.assertTrue(imageSource.captureNavigator().width)


class TestWhatIsActuallyOnTheScreen(ScreenCapture):
	"""An object's location is where it would be, not where it can be seen.

	A graphic scrolled off the bottom of a page keeps an ordinary rectangle with a y coordinate
	past the end of the screen, and copying that rectangle succeeds — it comes back as whatever
	the graphics card has there. The reader is then handed a picture that is not of the thing
	they pointed at, drawn as confidently as a real one. The size check alone does not catch it,
	because a large off-screen object is still large.
	"""

	def setUp(self):
		super().setUp()
		self._realScreen = imageSource._visibleScreen
		imageSource._visibleScreen = lambda: (0, 0, 1920, 1080)
		self.addCleanup(self.putTheScreenBack)

	def putTheScreenBack(self):
		imageSource._visibleScreen = self._realScreen

	def test_somethingEntirelyOffScreenIsRefused(self):
		self.navigator.location = (0, 4000, 400, 300)
		with self.assertRaises(imagePins.ImageRefused) as refused:
			imageSource.captureNavigator()
		self.assertIn("screen", str(refused.exception))

	def test_somethingPartlyOffScreenIsClippedToWhatShows(self):
		"""Part of the thing is worth having: it is what the reader can see."""
		self.navigator.location = (1800, 100, 400, 300)
		imageSource.captureNavigator()
		self.assertEqual(FakeScreenBitmap.asked[0][:4], (1800, 100, 120, 300))

	def test_aBigThingWithASliverShowingIsRefusedAsTooSmall(self):
		"""Measured after the clip, so it is refused as what it is rather than passed as what
		it would be if it were all there."""
		self.navigator.location = (1915, 100, 800, 600)
		with self.assertRaises(imagePins.ImageRefused) as refused:
			imageSource.captureNavigator()
		self.assertIn("small", str(refused.exception))

	def test_aScreenThatWillNotBeMeasuredDoesNotRefuseEverything(self):
		"""Not clipping is the safer failure: a wrong clip refuses pictures that would have
		worked, where no clip only fails to catch one that would not."""
		imageSource._visibleScreen = lambda: None
		self.navigator.location = (0, 4000, 400, 300)
		self.assertTrue(imageSource.captureNavigator().width)

	def test_somethingThatSaysItIsNotInViewIsRefused(self):
		"""The clip and the state catch different things. A control scrolled out of a pane
		keeps a location that is still over the window it is in, so the rectangle looks fine
		and the capture comes back as the rows that did scroll into view."""
		import controlTypes

		controlTypes.State = types.SimpleNamespace(OFFSCREEN="offscreen")
		self.addCleanup(lambda: delattr(controlTypes, "State"))
		self.navigator.states = {"offscreen"}
		with self.assertRaises(imagePins.ImageRefused) as refused:
			imageSource.captureNavigator()
		self.assertIn("view", str(refused.exception))

	def test_anObjectThatWillNotSayItsStatesIsDrawnAnyway(self):
		"""Refusing on a question nobody answered would refuse the ordinary case."""
		self.assertTrue(imageSource.captureNavigator().width)


class TestWhatThePictureIsCalled(ScreenCapture):
	"""The object's own words first, because that is what the reader was just told."""

	def test_altTextIsTheName(self):
		self.navigator.name = "Sales by region"
		self.assertEqual(imageSource.nameFor(self.navigator), "Sales by region")

	def test_aDescriptionWillDo(self):
		self.navigator.description = "a map of the site"
		self.assertEqual(imageSource.nameFor(self.navigator), "a map of the site")

	def test_theRoleIsTheFallbackRatherThanAPrefix(self):
		""""graphic, graphic" says nothing twice."""
		self.assertEqual(imageSource.nameFor(self.navigator), "graphic")

	def test_somethingThatWillNotSayStillHasAName(self):
		self.assertTrue(imageSource.nameFor(object()))


class TestHowManyPixelsAreKept(ScreenCapture):
	"""Held rather than reduced once, which is what makes zoom worth having."""

	def test_asmallPictureIsKeptAtItsOwnSize(self):
		self.navigator.location = (0, 0, 120, 80)
		found = imageSource.captureNavigator()
		self.assertEqual((found.width, found.height), (120, 80))

	def test_aHugePictureIsCappedButKeepsItsShape(self):
		"""A picture squeezed on one axis is a picture of something else, and nothing
		downstream could know it had happened."""
		self.navigator.location = (0, 0, 4000, 2000)
		found = imageSource.captureNavigator()
		self.assertLessEqual(found.width * found.height, imageSource.MAX_PIXELS)
		self.assertAlmostEqual(found.width / found.height, 2.0, places=1)

	def test_nothingIsEverEnlarged(self):
		"""Upscaling before reducing invents pixels and then hands them to the reader."""
		self.navigator.location = (0, 0, 20, 20)
		found = imageSource.captureNavigator()
		self.assertEqual((found.width, found.height), (20, 20))


class TestComposingAFigure(unittest.TestCase):
	"""Pixels to a `Drawing` the graphics mode can show."""

	def test_aPictureBecomesAFigureWithDotsInIt(self):
		figure = imageFigure.figureFor(newBuffer, picture(), *PANEL)
		self.assertEqual((figure.width, figure.height), PANEL)
		self.assertTrue(any(figure.buffer.getDot(x, y) for y in range(PANEL[1]) for x in range(PANEL[0])))

	def test_theNameSaysWhatItIsAndHowItIsDrawn(self):
		"""The style is the thing the reader has just changed and the one thing they cannot
		tell from the panel."""
		figure = imageFigure.figureFor(newBuffer, picture(name="a map"), *PANEL)
		self.assertIn("a map", figure.name)
		self.assertIn("outlines", figure.name)

	def test_aBlankPictureIsRefusedRatherThanShown(self):
		"""A reader running a hand over an empty panel cannot tell it from a broken display."""
		flat = imagePins.Picture(bytearray([128] * 400), 20, 20)
		with self.assertRaises(imagePins.ImageRefused):
			imageFigure.figureFor(newBuffer, flat, *PANEL)

	def test_aDisplayThatWillNotGiveASurfaceIsRefused(self):
		with self.assertRaises(imagePins.ImageRefused):
			imageFigure.figureFor(lambda width, height: None, picture(), *PANEL)

	def test_aPressSaysWhereTheFingerIs(self):
		"""A picture cannot yet say what is at a point — that is the machinery that comes
		after this — but where the point is, is the thing a reader loses first with both hands
		on a panel that has no edges to count from."""
		figure = imageFigure.figureFor(newBuffer, picture(), *PANEL)
		self.assertIn("across", figure.describeAt(PANEL[0] // 2, PANEL[1] // 2))


class TestWhereAPressSaysItIs(unittest.TestCase):
	"""In the picture, not on the panel.

	Read off the panel the left edge says nought across however far in the reader has zoomed —
	so the number is wrong exactly when they have most need of it, since zooming in is what
	somebody does when they have lost the place.

	The picture here is the panel's own shape, so there is no margin to reason about at the
	same time. `TestAPictureKeepsItsShape` covers the letterbox.
	"""

	def panelShaped(self):
		""":return: a picture with the panel's proportions, so there is no margin."""
		return picture(PANEL[0] * 5, PANEL[1] * 5)

	def percentages(self, said):
		""":return: the two numbers a press reported."""
		self.assertNotEqual(said, "outside the picture", "the press landed beside the picture")
		return [int(number) for number in re.findall(r"\d+", said)]

	def middleOf(self, figure):
		return self.percentages(figure.describeAt(PANEL[0] // 2, PANEL[1] // 2))

	def test_theMiddleOfThePanelIsTheMiddleOfThePicture(self):
		across, down = self.middleOf(imageFigure.figureFor(newBuffer, self.panelShaped(), *PANEL))
		self.assertAlmostEqual(across, 50, delta=3)
		self.assertAlmostEqual(down, 50, delta=3)

	def test_zoomingIntoTheRightHalfReportsTheRightHalf(self):
		"""The fault this is here for. The panel position is the same, the place in the
		picture is not, and the reader zoomed in because they had lost the place."""
		figure = imageFigure.figureFor(newBuffer, self.panelShaped(), *PANEL)
		window = figure.redraw(0.5, 0.5, PANEL[0], PANEL[1], top=0.0, down=1.0)
		across, _down = self.middleOf(window)
		self.assertAlmostEqual(across, 75, delta=4)

	def test_zoomingIntoTheBottomHalfReportsTheBottomHalf(self):
		figure = imageFigure.figureFor(newBuffer, self.panelShaped(), *PANEL)
		window = figure.redraw(0.0, 1.0, PANEL[0], PANEL[1], top=0.5, down=0.5)
		_across, down = self.middleOf(window)
		self.assertAlmostEqual(down, 75, delta=4)

	def test_theSamePinSaysSomethingElseAfterZooming(self):
		figure = imageFigure.figureFor(newBuffer, self.panelShaped(), *PANEL)
		window = figure.redraw(0.5, 0.5, PANEL[0], PANEL[1], top=0.0, down=1.0)
		self.assertNotEqual(self.middleOf(figure), self.middleOf(window))

	def test_theTwoEndsAreTheTwoEnds(self):
		figure = imageFigure.figureFor(newBuffer, self.panelShaped(), *PANEL)
		self.assertLess(self.percentages(figure.describeAt(0, 0))[0], 5)
		self.assertGreater(self.percentages(figure.describeAt(PANEL[0] - 1, 0))[0], 95)

	def test_aPressBesideThePictureSaysSo(self):
		"""A square picture on a panel over twice as wide has margin on a third of it, and
		there is nothing there. Reporting the nearest edge would be a fact about the letterbox
		rather than about the picture."""
		figure = imageFigure.figureFor(newBuffer, picture(96, 96), *PANEL)
		self.assertEqual(figure.describeAt(0, PANEL[1] // 2), "outside the picture")


class TestAPictureKeepsItsShape(unittest.TestCase):
	"""A square has to come out square, and no test of the fitting alone can see that it did.

	The fitting arithmetic was right and the reduction then undid it: the source box was grown
	on the short axis until it had the panel proportions, and `Picture.reduce` clipped the
	growth away again before reducing, so the whole picture was resized to the whole panel. A
	square feature came out 33 pins wide by 14 high, which makes a circle an ellipse and every
	geometric relationship in a diagram a lie, and nothing in the drawing says it happened.

	So the check is on the rendered pins rather than on any rectangle: draw a square, measure
	what was actually raised, and require it to still be square.
	"""

	def square(self, side=120, feature=40):
		""":return: a picture of a centred square on a plain field."""
		left = top = (side - feature) // 2
		greys = bytearray()
		for y in range(side):
			for x in range(side):
				greys.append(0 if (left <= x < left + feature and top <= y < top + feature) else 255)
		return imagePins.Picture(greys, side, side, "a square")

	def wide(self):
		""":return: a picture wider than the panel, with a feature twice as wide as it is
		tall."""
		side, feature = 240, 80
		greys = bytearray()
		for y in range(60):
			for x in range(side):
				greys.append(0 if (80 <= x < 80 + feature and 10 <= y < 10 + 40) else 255)
		return imagePins.Picture(greys, side, 60, "wide")

	def drawn(self, source, width, height, mode=imagePins.BRIGHTNESS):
		""":return: which panel pins were raised, as a set of coordinates.

		Panel coordinates rather than the rendering own, since what is being checked is where
		the picture landed on the display a hand is on.
		"""
		found = imagePins.render(source, (0, 0, source.width, source.height), width, height, mode)
		return {
			(found.left + at % found.width, found.top + at // found.width)
			for at, pin in enumerate(found.pins)
			if pin
		}

	def bounds(self, raised):
		""":return: the width and height of what was raised, in pins."""
		self.assertTrue(raised, "nothing was drawn, so there is nothing to measure")
		columns = [x for x, _y in raised]
		rows = [y for _x, y in raised]
		return max(columns) - min(columns) + 1, max(rows) - min(rows) + 1

	def test_aSquareIsStillSquareOnAPanelTwiceAsWideAsItIsTall(self):
		width, height = self.bounds(self.drawn(self.square(), 96, 40))
		self.assertAlmostEqual(width / height, 1.0, delta=0.2)

	def test_aSquareIsStillSquareInOutlinesToo(self):
		width, height = self.bounds(self.drawn(self.square(), 96, 40, imagePins.EDGES))
		self.assertAlmostEqual(width / height, 1.0, delta=0.2)

	def test_aWidePictureIsNotSquashedEither(self):
		"""The other direction: a picture wider than the panel gets margin above and below."""
		width, height = self.bounds(self.drawn(self.wide(), 96, 40))
		self.assertAlmostEqual(width / height, 2.0, delta=0.4)

	def test_nothingIsEverDrawnInTheMargin(self):
		"""The margin is not part of the picture, so nothing in it can be part of the answer.

		Guaranteed by construction now rather than by a threshold: the detector is only ever
		shown the content rectangle. Asserted anyway, because the whole fault this replaced
		was a detector being handed pins the add-on had invented and believing them.
		"""
		source = self.square()
		spot = imagePins.place(source, (0, 0, source.width, source.height), 96, 40)
		inside = {
			(x, y)
			for x in range(spot.left, spot.left + spot.width)
			for y in range(spot.top, spot.top + spot.height)
		}
		self.assertFalse(self.drawn(source, 96, 40, imagePins.EDGES) - inside)

	def test_theSeamBetweenPictureAndMarginIsNotDrawn(self):
		"""The two vertical lines, which were a real report from a real panel.

		A picture whose background texture runs out to its own boundary meets the letterbox
		along a seam measuring about one per cent of a real edge. Under a coverage quota that
		had run out of genuine edges, that seam was promoted to two full height vertical lines
		down a panel which had no such lines anywhere in it -- and a reader has no way to tell
		an invented line from a drawn one.
		"""
		side = 120
		greys = bytearray()
		for y in range(side):
			for x in range(side):
				if 40 <= x < 80 and 40 <= y < 80:
					greys.append(0)
				else:
					# A faint diagonal hatch, reaching the picture own edge.
					greys.append(243 if (x + y) % 7 < 2 else 255)
		source = imagePins.Picture(greys, side, side, "hatched")
		raised = self.drawn(source, 96, 40, imagePins.EDGES)
		columns = [x for x, _y in raised]
		tall = [x for x in set(columns) if columns.count(x) >= 30]
		self.assertFalse(tall, f"columns drawn nearly top to bottom: {sorted(tall)}")

	def test_aSubjectTouchingTheSourceEdgeIsKept(self):
		"""The reason no border is trimmed away. An object may legitimately reach its own
		boundary, and nothing can tell that apart from a capture that overshot."""
		side = 120
		greys = bytearray()
		for y in range(side):
			for x in range(side):
				greys.append(0 if y < 40 and x < 8 else 255)
		source = imagePins.Picture(greys, side, side, "touching the edge")
		spot = imagePins.place(source, (0, 0, side, side), 96, 40)
		raised = self.drawn(source, 96, 40, imagePins.BRIGHTNESS)
		self.assertTrue([x for x, _y in raised if x <= spot.left + 2])


class TestTheStyles(unittest.TestCase):
	"""Outlines, brightness, brightness reversed, on one key."""

	def test_theCycleReturnsToWhereItStarted(self):
		style = imageFigure.STYLES[0]
		seen = [style]
		for _step in range(len(imageFigure.STYLES)):
			style = imageFigure.nextStyle(*style)
			seen.append(style)
		self.assertEqual(seen[0], seen[-1])
		self.assertEqual(len(set(seen)), len(imageFigure.STYLES))

	def test_anUnknownStyleFallsBackRatherThanRaising(self):
		self.assertEqual(imageFigure.nextStyle("nonsense", False), imageFigure.STYLES[0])

	def test_eachStyleHasItsOwnName(self):
		names = {imageFigure.styleName(mode, invert) for mode, invert in imageFigure.STYLES}
		self.assertEqual(len(names), len(imageFigure.STYLES))

	def test_theTwoStylesDrawDifferently(self):
		"""Which one reads cannot be predicted, which is the argument for a key rather than a
		question: the whole cost of finding out is one press."""
		source = picture()
		outlines = imageFigure.figureFor(newBuffer, source, *PANEL, imagePins.EDGES)
		masses = imageFigure.figureFor(newBuffer, source, *PANEL, imagePins.BRIGHTNESS)
		self.assertNotEqual(outlines.buffer.rows(), masses.buffer.rows())

	def test_reversingASilhouetteSwapsIt(self):
		source = picture()
		plain = imageFigure.figureFor(newBuffer, source, *PANEL, imagePins.BRIGHTNESS)
		other = imageFigure.figureFor(newBuffer, source, *PANEL, imagePins.BRIGHTNESS, True)
		self.assertNotEqual(plain.buffer.rows(), other.buffer.rows())


class TestZoomingIntoTheCapture(unittest.TestCase):
	"""The reason the pixels are kept at all."""

	def test_aPictureRedrawsItselfRatherThanMagnifying(self):
		self.assertIsNotNone(imageFigure.figureFor(newBuffer, picture(), *PANEL).redraw)

	def test_aPictureHasAnUpAndADown(self):
		"""The first figure that does. A chart refits its value axis to whatever periods it
		is showing, so there is never anything above or below its panel."""
		self.assertTrue(imageFigure.figureFor(newBuffer, picture(), *PANEL).windowsVertically)

	def test_aWindowIsDrawnFromThePixelsAgain(self):
		figure = imageFigure.figureFor(newBuffer, picture(), *PANEL)
		window = figure.redraw(0.25, 0.5, PANEL[0], PANEL[1], top=0.25, down=0.5)
		self.assertIsNotNone(window)
		self.assertNotEqual(window.buffer.rows(), figure.buffer.rows())

	def test_aWindowIsNotAskedToWindowAgain(self):
		"""A window's own drawing carries no `redraw`: applying its box a second time would
		zoom into a zoom and name a part of the picture the reader is not touching."""
		figure = imageFigure.figureFor(newBuffer, picture(), *PANEL)
		self.assertIsNone(figure.redraw(0.25, 0.5, PANEL[0], PANEL[1], top=0.0, down=1.0).redraw)

	def test_aWindowWithNothingInItKeepsTheViewRatherThanBlanking(self):
		"""None comes back, and the mode keeps what the reader already had. A blank panel
		would say the picture had gone."""
		figure = imageFigure.figureFor(newBuffer, picture(), *PANEL)
		self.assertIsNone(figure.redraw(0.0, 0.02, PANEL[0], PANEL[1], top=0.0, down=0.02))

	def test_aBigCaptureHasFurtherToZoomThanASmallOne(self):
		big = imageFigure.zoomPoints(picture(960, 400), *PANEL)
		small = imageFigure.zoomPoints(picture(48, 20), *PANEL)
		self.assertGreater(big, small)

	def test_aCaptureNoBiggerThanThePanelStillGetsOneZoom(self):
		"""Refusing a picture its first zoom for having been captured small would be refusing
		the reader the only way they have of looking closer at anything."""
		self.assertGreaterEqual(
			imageFigure.zoomPoints(picture(48, 20), *PANEL),
			imageFigure.MIN_WINDOW_POINTS,
		)
