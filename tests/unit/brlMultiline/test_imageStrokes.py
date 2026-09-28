# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the Pillow pixel path and the single lines style.

Two changes that belong together. Pillow does the work that touches every pixel, and the first
thing that bought was a way to shrink a picture without losing its thin lines -- which is what
single lines needs, since a stroke averaged away before it is found cannot be drawn down its
middle.

The Pillow tests are about agreement. The Python path is still what an NVDA without Pillow
runs, so the two must give the same picture: grey for grey, and to within a level wherever a
cell is a whole number of pixels. Where cells split pixels the two filters share them out
differently, so there the promise is the drawing rather than every cell. `test_imageWithoutPillow` runs the older suites the other way
round.
"""

import ctypes
import os
import random
import sys
import types
import unittest

from ._png import greysOf
from ._stubs import installStubs

installStubs()

from brlMultiline import image as imageFigure, imagePins, imageSource  # noqa: E402

needsPillow = unittest.skipIf(imagePins._Image is None, "Pillow is not installed")

HEXAGON = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "hexagon.png")


class RGBQUAD(ctypes.Structure):
	_fields_ = [
		("rgbBlue", ctypes.c_ubyte),
		("rgbGreen", ctypes.c_ubyte),
		("rgbRed", ctypes.c_ubyte),
		("rgbReserved", ctypes.c_ubyte),
	]


def capture(raw, width, height):
	""":return: raw bytes as the ctypes array `ScreenBitmap.captureImage` returns."""
	pixels = (RGBQUAD * width * height)()
	ctypes.memmove(pixels, raw, len(raw))
	return pixels


def noise(width, height, seed=1):
	""":return: random colour pixels as raw BGRX bytes."""
	chance = random.Random(seed)
	return bytes(chance.getrandbits(8) for _ in range(width * height * 4))


def drawn(width, height, inked):
	""":return: a picture, black where `inked(x, y)` and white elsewhere."""
	greys = bytearray()
	for y in range(height):
		for x in range(width):
			greys.append(0 if inked(x, y) else 255)
	return imagePins.Picture(greys, width, height, "drawing")


def squareOutline(size=200, margin=40, pen=16):
	""":return: the outline of a square drawn with a thick pen."""

	def inked(x, y):
		inside = margin <= x < size - margin and margin <= y < size - margin
		hollow = margin + pen <= x < size - margin - pen and margin + pen <= y < size - margin - pen
		return inside and not hollow

	return drawn(size, size, inked)


def raised(rendering):
	""":return: the raised pins as a set of (x, y) in the rendering's own coordinates."""
	return {(at % rendering.width, at // rendering.width) for at, pin in enumerate(rendering.pins) if pin}


def runs(cells):
	""":return: the runs of consecutive numbers in a collection, as lengths."""
	lengths = []
	previous = None
	for value in sorted(cells):
		if previous is not None and value == previous + 1:
			lengths[-1] += 1
		else:
			lengths.append(1)
		previous = value
	return lengths


class WithoutPillow:
	"""Mixin: run a test with Pillow switched off, as on an NVDA whose build leaves it out."""

	def setUp(self):
		saved = imagePins._Image
		imagePins._Image = None
		self.addCleanup(setattr, imagePins, "_Image", saved)
		super().setUp()


@needsPillow
class TestTheTwoPathsAgree(unittest.TestCase):
	"""Pillow does the pixels when it is there. It must not change the picture."""

	def test_colourToGreyIsIdenticalToTheLevel(self):
		"""Same integer formula on both paths, so a threshold on any level raises the same pins
		whichever NVDA the reader has."""
		width, height = 64, 48
		raw = noise(width, height)
		pixels = capture(raw, width, height)
		python = imagePins.greysFromPixels(pixels, width, height)
		pillow = imagePins.pictureFromBgrx(raw, width, height, (width, height))
		self.assertEqual(bytes(python), bytes(pillow.greys))

	def test_theWeightsAreStillRec601NotNvdas(self):
		"""The reason `greysFromPixels` exists, which must survive the change of path: red is
		heavier than blue."""
		red = bytes((0, 0, 255, 0))
		blue = bytes((255, 0, 0, 0))
		picture = imagePins.pictureFromBgrx(red + blue, 2, 1, (2, 1))
		self.assertGreater(picture.greys[0], picture.greys[1])

	def reducedBothWays(self, greys, width, height, box, outWidth, outHeight):
		""":return: the difference per cell between the two paths."""
		withPillow = imagePins.Picture(greys, width, height).reduce(box, outWidth, outHeight)
		saved = imagePins._Image
		imagePins._Image = None
		try:
			withPython = imagePins.Picture(greys, width, height).reduce(box, outWidth, outHeight)
		finally:
			imagePins._Image = saved
		return [abs(a - b) for a, b in zip(withPillow, withPython)]

	def test_aWholePixelReductionAgreesToALevel(self):
		"""Four pixels to a cell. Only Pillow's fixed point rounding is between them."""
		greys = bytearray(noise(96, 96)[: 96 * 96])
		self.assertLessEqual(max(self.reducedBothWays(greys, 96, 96, (0, 0, 96, 96), 24, 24)), 1)

	def test_aWholePixelWindowAgreesToALevel(self):
		greys, width, height = greysOf(HEXAGON)
		self.assertLessEqual(max(self.reducedBothWays(greys, width, height, (30, 40, 120, 90), 40, 30)), 1)

	def test_aFractionalReductionIsCloseOnAverage(self):
		"""Pillow shares a straddling pixel between the cells it straddles and Python gives it
		to one, so a thin line on a boundary can move a single cell a long way. On average,
		over the drawing every image fault was found on, they are a few levels apart."""
		greys, width, height = greysOf(HEXAGON)
		apart = self.reducedBothWays(greys, width, height, (0, 0, width, height), 40, 40)
		self.assertLess(sum(apart) / len(apart), 6)

	def test_aCaptureIsShrunkToWhatIsKeptWithItsShape(self):
		width, height = 400, 200
		picture = imagePins.pictureFromBgrx(noise(width, height), width, height, (200, 100))
		self.assertEqual((picture.width, picture.height), (200, 100))
		self.assertEqual(len(picture.greys), 200 * 100)

	def test_bytesOfTheWrongLengthAreLeftToPython(self):
		self.assertIsNone(imagePins.pictureFromBgrx(b"\0" * 7, 2, 1, (2, 1)))


class TestWithoutPillowThereIsNoPillowPath(WithoutPillow, unittest.TestCase):
	def test_itSaysSo(self):
		self.assertFalse(imagePins.hasPillow())

	def test_andARawCaptureIsLeftToPython(self):
		self.assertIsNone(imagePins.pictureFromBgrx(noise(4, 4), 4, 4, (4, 4)))


class FakeScreenBitmap:
	"""Enough of `ScreenBitmap` to see what size was grabbed."""

	asked = []

	def __init__(self, width, height):
		self.width = width
		self.height = height
		FakeScreenBitmap.asked.append((width, height))

	def captureImage(self, x, y, w, h):
		return capture(bytes(self.width * self.height * 4), self.width, self.height)


class CaptureTestCase(unittest.TestCase):
	def setUp(self):
		FakeScreenBitmap.asked = []
		sys.modules["screenBitmap"] = types.SimpleNamespace(ScreenBitmap=FakeScreenBitmap)
		self.addCleanup(sys.modules.pop, "screenBitmap", None)


@needsPillow
class TestGrabbingWithPillow(CaptureTestCase):
	"""With Pillow, more is copied than kept, so Pillow rather than Windows does the shrink."""

	def test_aLargeThingIsCopiedLargerThanItIsKept(self):
		picture = imageSource.capture(0, 0, 1600, 1000)
		grabbed = FakeScreenBitmap.asked[0]
		self.assertEqual(grabbed, (1600, 1000))
		self.assertLessEqual(picture.width * picture.height, imageSource.keepPixels())
		self.assertAlmostEqual(picture.width / picture.height, 1.6, places=1)

	def test_somethingHugeIsCopiedNoLargerThanTheGrabLimit(self):
		imageSource.capture(0, 0, 4000, 3000)
		width, height = FakeScreenBitmap.asked[0]
		self.assertLessEqual(width * height, imageSource.GRAB_PIXELS)

	def test_aCaptureNotInTheShapeExpectedStillComesBack(self):
		"""A future NVDA returning something else is read the Python way, and shrunk there."""

		class Rows(FakeScreenBitmap):
			def captureImage(self, x, y, w, h):
				pixel = types.SimpleNamespace(rgbRed=10, rgbGreen=10, rgbBlue=10)
				return [[pixel] * self.width for _ in range(self.height)]

		sys.modules["screenBitmap"] = types.SimpleNamespace(ScreenBitmap=Rows)
		picture = imageSource.capture(0, 0, 900, 500)
		self.assertLessEqual(picture.width * picture.height, imageSource.keepPixels())
		self.assertEqual(picture.greys[0], 10)


class TestGrabbingWithoutPillow(WithoutPillow, CaptureTestCase):
	"""Without it, what was always done: Windows shrinks during the copy."""

	def test_whatIsCopiedIsWhatIsKept(self):
		picture = imageSource.capture(0, 0, 1600, 1000)
		self.assertEqual(FakeScreenBitmap.asked[0], (picture.width, picture.height))
		self.assertLessEqual(picture.width * picture.height, imageSource.keepPixels())


class TestKeepingThinLines(unittest.TestCase):
	"""What `reduceInk` is for: a fine pen line survives being shrunk to a pin."""

	def setUp(self):
		# A one pixel line across a 600 pixel picture, headed for 40 pins: fifteen to a pin.
		self.picture = drawn(600, 600, lambda x, y: y == 301)

	def test_averagingLosesIt(self):
		"""The problem, stated: the line is a fifteenth of every cell it crosses."""
		plain = self.picture.reduce((0, 0, 600, 600), 40, 40)
		self.assertGreater(min(plain), 200)

	def test_theInkReductionKeepsItDark(self):
		inked = self.picture.reduceInk((0, 0, 600, 600), 40, 40, dark=True)
		row = min(range(40), key=lambda y: sum(inked[y * 40 : y * 40 + 40]))
		self.assertLess(max(inked[row * 40 : row * 40 + 40]), 128)

	def test_andNowhereElse(self):
		"""Thickened, not smeared: well away from the line is still paper."""
		inked = self.picture.reduceInk((0, 0, 600, 600), 40, 40, dark=True)
		self.assertEqual(min(inked[0 : 40 * 10]), 255)
		self.assertEqual(min(inked[40 * 30 :]), 255)

	def test_lightInkIsKeptToo(self):
		light = drawn(600, 600, lambda x, y: y != 301)
		inked = light.reduceInk((0, 0, 600, 600), 40, 40, dark=False)
		self.assertGreater(max(inked), 128)


class TestKeepingThinLinesWithoutPillow(WithoutPillow, TestKeepingThinLines):
	pass


class TestTheSkeleton(unittest.TestCase):
	def test_aThickBarBecomesOneLine(self):
		width, height = 20, 9
		mask = bytearray(1 if 3 <= y <= 5 and 2 <= x < 18 else 0 for y in range(height) for x in range(width))
		lines = imagePins._skeleton(mask, width, height)
		for x in range(5, 15):
			column = [y for y in range(height) if lines[y * width + x]]
			self.assertEqual(len(column), 1, f"column {x} is {column}")

	def test_aDotOfInkIsNotLost(self):
		"""Zhang and Suen take a 2 by 2 block away entirely. Mended: a full stop stays."""
		width = height = 6
		mask = bytearray(1 if 2 <= x <= 3 and 2 <= y <= 3 else 0 for y in range(height) for x in range(width))
		self.assertEqual(sum(imagePins._skeleton(mask, width, height)), 1)

	def test_aLineAlreadyThinIsLeftAlone(self):
		width, height = 12, 5
		mask = bytearray(1 if y == 2 and 1 <= x <= 10 else 0 for y in range(height) for x in range(width))
		self.assertEqual(bytes(imagePins._skeleton(mask, width, height)), bytes(mask))


class TestSingleLines(unittest.TestCase):
	"""One line per stroke, where outlines give two."""

	def render(self, picture, mode):
		spot = imagePins.place(picture, (0, 0, picture.width, picture.height), 40, 40)
		return imagePins.renderAt(picture, spot, mode)

	def crossings(self, rendering, row):
		return runs(x for x, y in raised(rendering) if y == row)

	def test_aThickPenOutlineIsCrossedTwiceNotFourTimes(self):
		picture = squareOutline()
		lines = self.render(picture, imagePins.STROKES)
		outlines = self.render(picture, imagePins.EDGES)
		self.assertEqual(len(self.crossings(outlines, 20)), 4, "outlines give each stroke two rails")
		crossed = self.crossings(lines, 20)
		self.assertEqual(len(crossed), 2)
		self.assertTrue(all(width == 1 for width in crossed), crossed)

	def test_itIsOneUnbrokenLine(self):
		lines = self.render(squareOutline(), imagePins.STROKES)
		self.assertEqual(len(imagePins._components([y * 40 + x for x, y in raised(lines)], 40, 40)), 1)

	def test_theLineRunsDownTheMiddleOfThePen(self):
		"""The pen covers pixels 40 to 55 on the left, which is pins 8 to 11. The skeleton sits
		inside it rather than against one side of it."""
		lines = self.render(squareOutline(), imagePins.STROKES)
		left = min(x for x, y in raised(lines) if y == 20)
		self.assertIn(left, (9, 10))

	def test_aFilledShapeIsDrawnRoundNotThrough(self):
		"""Its skeleton is a dot. Its boundary is what a finger wants."""
		solid = drawn(200, 200, lambda x, y: 50 <= x < 150 and 50 <= y < 150)
		lines = self.render(solid, imagePins.STROKES)
		pins = raised(lines)
		self.assertGreater(len(pins), 50)
		self.assertNotIn((20, 20), pins, "the inside is left down")
		self.assertEqual(len(self.crossings(lines, 20)), 2)

	def test_lightLinesOnDarkAreFoundToo(self):
		"""The ink is the smaller class, whichever tone it is."""
		light = squareOutline()
		light.greys = bytearray(255 - grey for grey in light.greys)
		lines = self.render(light, imagePins.STROKES)
		self.assertEqual(len(self.crossings(lines, 20)), 2)

	def test_aFlatPictureIsStillRefused(self):
		flat = drawn(100, 100, lambda x, y: False)
		with self.assertRaises(imagePins.ImageRefused):
			self.render(flat, imagePins.STROKES)

	def test_tooMuchInkIsFittedWholeStrokesAtATimeAndSaid(self):
		"""Ceilings remove whole strokes, largest kept, and say so."""
		width, height = 40, 40
		inked = bytearray(
			0 if (x % 3 == 0 or y % 3 == 0) else 255 for y in range(height) for x in range(width)
		)
		rendering = imagePins.strokePins(inked, width, height, dark=True, coverage=0.05)
		self.assertLessEqual(rendering.raised, round(width * height * 0.05))
		self.assertTrue(rendering.crowded)


class TestSingleLinesWithoutPillow(WithoutPillow, TestSingleLines):
	pass


class TestTheHexagon(unittest.TestCase):
	"""The fixture every other fault was found on, in the new style."""

	def setUp(self):
		greys, width, height = greysOf(HEXAGON)
		self.picture = imagePins.Picture(greys, width, height, "hexagon")
		spot = imagePins.place(self.picture, (0, 0, width, height), 96, 40)
		self.lines = imagePins.renderAt(self.picture, spot, imagePins.STROKES)

	def test_itIsOneUnbrokenLine(self):
		width = self.lines.width
		places = [at for at, pin in enumerate(self.lines.pins) if pin]
		self.assertEqual(len(imagePins._components(places, width, self.lines.height)), 1)

	def test_itCrossesTheMiddleTwiceAndThinly(self):
		crossed = runs(x for x, y in raised(self.lines) if y == self.lines.height // 2)
		self.assertEqual(len(crossed), 2)
		self.assertTrue(all(width <= 2 for width in crossed), crossed)

	def test_theMiddleIsEmpty(self):
		middle = {(x, y) for x, y in raised(self.lines) if 12 <= x < 28 and 12 <= y < 28}
		self.assertFalse(middle)


class TestTheHexagonWithoutPillow(WithoutPillow, TestTheHexagon):
	pass


class TestTheStyle(unittest.TestCase):
	def test_itIsSecondOnTheCycle(self):
		self.assertEqual(imageFigure.STYLES[1], imageFigure.LINES)
		self.assertEqual(imageFigure.nextStyle(*imageFigure.OUTLINES), imageFigure.LINES)

	def test_itIsNotCalledSomethingThatSoundsLikeOutlines(self):
		self.assertEqual(imageFigure.styleName(*imageFigure.LINES), "single lines")

	def test_itDrawsDifferentlyFromOutlines(self):
		def newBuffer(width, height):
			from pinBuffer import PinBuffer

			return PinBuffer(width, height)

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
		picture = squareOutline()
		lines = imageFigure.figureFor(newBuffer, picture, 48, 20, *imageFigure.LINES)
		outlines = imageFigure.figureFor(newBuffer, picture, 48, 20, *imageFigure.OUTLINES)
		self.assertNotEqual(lines.buffer.rows(), outlines.buffer.rows())
		self.assertIn("single lines", lines.name)
