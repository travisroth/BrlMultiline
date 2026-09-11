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
		self.assertIn("across", figure.describeAt(0, 0))


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
