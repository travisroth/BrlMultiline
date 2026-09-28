# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for what Pillow added beyond speed: trimming, the clipboard, and colour.

Three features, one reason. Each is something a screen reader user could not get from a grey
screen capture: a picture that fills the panel instead of its blank margins, a picture that did
not have to be visible to be read, and the colours a sighted reader tells things apart by.

Trimming works without Pillow as well and is tested both ways. The clipboard and colour need
Pillow, and the tests for them are skipped where it is missing, apart from the ones that check
each says so rather than failing.
"""

import io
import os
import sys
import tempfile
import unittest

from ._stubs import FakeHandler, flashedMessages, installStubs, resetPluginState

installStubs()

from . import test_plugin  # noqa: E402

from brlMultiline import image as imageFigure, imagePins, imageSource  # noqa: E402

needsPillow = unittest.skipIf(imagePins._Image is None, "Pillow is not installed")

if imagePins._Image is not None:
	from PIL import Image, ImageDraw
else:
	Image = ImageDraw = None


def greyPicture(width, height, inked):
	""":return: a grey picture, black where `inked(x, y)` and white elsewhere."""
	greys = bytearray(0 if inked(x, y) else 255 for y in range(height) for x in range(width))
	return imagePins.Picture(greys, width, height, "drawing")


def raised(rendering):
	return {(at % rendering.width, at // rendering.width) for at, pin in enumerate(rendering.pins) if pin}


def runs(cells):
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
	def setUp(self):
		saved = imagePins._Image
		imagePins._Image = None
		self.addCleanup(setattr, imagePins, "_Image", saved)
		super().setUp()


class TestTrimming(unittest.TestCase):
	"""Blank margins cost the reader pins, so the drawing starts from what is in the picture."""

	def smallSquare(self):
		# A 40 pixel square in a 400 by 300 picture: a tenth of the width.
		return greyPicture(400, 300, lambda x, y: 180 <= x < 220 and 130 <= y < 170)

	def test_theBoxIsCutDownToTheContent(self):
		left, top, width, height = self.smallSquare().content
		self.assertLessEqual(left, 180)
		self.assertLessEqual(top, 130)
		self.assertGreaterEqual(left + width, 220)
		self.assertGreaterEqual(top + height, 170)
		self.assertLess(width, 100)

	def test_withRoomLeftRoundIt(self):
		"""Outlines never raise the outermost ring, so content trimmed flush would lose its edge."""
		left, top, width, height = self.smallSquare().content
		self.assertLess(left, 180)
		self.assertGreater(left + width, 220)

	def test_theDrawingIsLargerForIt(self):
		picture = self.smallSquare()
		trimmed = imageFigure.figureFor(test_plugin.TestDrawingThePictureHere.Dots, picture, 96, 40)
		across = {x for x, _y in trimmed.buffer.raised}
		self.assertGreater(max(across) - min(across), 20)

	def test_andTheOutlineIsStillClosed(self):
		picture = self.smallSquare()
		spot = imagePins.place(picture, picture.content, 96, 40)
		outline = imagePins.renderAt(picture, spot, imagePins.EDGES)
		middle = outline.height // 2
		self.assertEqual(len(runs(x for x, y in raised(outline) if y == middle)), 2)

	def test_aPictureThatIsMostlyContentIsLeftWhole(self):
		picture = greyPicture(100, 100, lambda x, y: 3 <= x < 97 and 3 <= y < 97)
		self.assertEqual(picture.content, (0, 0, 100, 100))

	def test_aFlatPictureIsLeftWhole(self):
		self.assertEqual(greyPicture(50, 50, lambda x, y: False).content, (0, 0, 50, 50))

	def test_faintTextureIsNotContent(self):
		"""Background texture within a few levels of the paper is not something to keep."""
		greys = bytearray(
			(0 if 45 <= x < 55 and 45 <= y < 55 else (250 if (x + y) % 4 == 0 else 255))
			for y in range(100)
			for x in range(100)
		)
		left, _top, width, _height = imagePins.Picture(greys, 100, 100).content
		self.assertGreater(left, 30)
		self.assertLess(width, 40)

	def test_aPressStillSaysWhereItIsInTheWholePicture(self):
		picture = self.smallSquare()
		figure = imageFigure.figureFor(test_plugin.TestDrawingThePictureHere.Dots, picture, 96, 40)
		said = figure.describeAt(48, 20)
		self.assertIn("50 across", said)


class TestTrimmingWithoutPillow(WithoutPillow, TestTrimming):
	pass


@needsPillow
class TestTheTwoWaysOfTrimmingAgree(unittest.TestCase):
	def test_theSameBox(self):
		picture = greyPicture(300, 200, lambda x, y: (x - 90) ** 2 + (y - 120) ** 2 < 400)
		withPillow = picture.content
		saved = imagePins._Image
		imagePins._Image = None
		try:
			withPython = greyPicture(300, 200, lambda x, y: (x - 90) ** 2 + (y - 120) ** 2 < 400).content
		finally:
			imagePins._Image = saved
		self.assertEqual(withPillow, withPython)


class TestHowMuchIsKept(unittest.TestCase):
	@needsPillow
	def test_moreIsKeptWithPillow(self):
		self.assertEqual(imageSource.keepPixels(), imageSource.PILLOW_PIXELS)
		self.assertGreater(imageSource.PILLOW_PIXELS, imageSource.MAX_PIXELS)

	def test_theOldLimitWithout(self):
		saved = imagePins._Image
		imagePins._Image = None
		try:
			self.assertEqual(imageSource.keepPixels(), imageSource.MAX_PIXELS)
		finally:
			imagePins._Image = saved


@needsPillow
class TestPicturesFromFiles(unittest.TestCase):
	"""What `pictureFromImage` makes of an image that did not come off the screen."""

	def test_transparencyIsLaidOnWhite(self):
		"""A black logo on a transparent ground stores black behind the transparency too."""
		image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
		ImageDraw.Draw(image).rectangle((10, 10, 29, 29), fill=(0, 0, 0, 255))
		picture = imagePins.pictureFromImage(image, 40 * 40)
		self.assertEqual(picture.greys[0], 255)
		self.assertEqual(picture.greys[20 * 40 + 20], 0)

	def test_aPaletteImageWithATransparentColourToo(self):
		image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
		ImageDraw.Draw(image).rectangle((10, 10, 29, 29), fill=(0, 0, 255, 255))
		palette = image.convert("P")
		palette.info["transparency"] = palette.getpixel((0, 0))
		picture = imagePins.pictureFromImage(palette, 40 * 40)
		self.assertEqual(picture.greys[0], 255)

	def test_aLargeImageIsShrunkToWhatIsKeptAndKeepsItsColour(self):
		image = Image.new("RGB", (800, 400), (255, 0, 0))
		picture = imagePins.pictureFromImage(image, 400 * 200)
		self.assertEqual((picture.width, picture.height), (400, 200))
		self.assertEqual(picture.colour.size, (400, 200))
		self.assertEqual(picture.colour.getpixel((10, 10)), (255, 0, 0))

	def test_aPhotographIsTurnedTheWayItWasTaken(self):
		"""Orientation 6 means the camera stored it turned a quarter; wider stored, taller shown."""
		picture = imagePins.pictureFromImage(sideways(60, 30), 60 * 30)
		self.assertEqual((picture.width, picture.height), (30, 60))

	def test_andIsShrunkTheWayItIsShown(self):
		"""The size kept is chosen after turning. Chosen before, a photo too large to keep was
		turned and then squeezed back into the stored shape."""
		picture = imagePins.pictureFromImage(sideways(400, 200), 100 * 50)
		self.assertEqual((picture.width, picture.height), (50, 100))


def sideways(width, height):
	""":return: a JPEG stored `width` by `height` and marked to be turned a quarter to show.

	A dark bar across the top as stored, so which way it went is also in the pixels.
	"""
	image = Image.new("RGB", (width, height), (255, 255, 255))
	ImageDraw.Draw(image).rectangle((0, 0, width - 1, height // 5), fill=(0, 0, 0))
	exif = image.getexif()
	exif[0x0112] = 6
	stored = io.BytesIO()
	image.save(stored, "JPEG", exif=exif)
	stored.seek(0)
	return Image.open(stored)


class FakeGrab:
	"""`PIL.ImageGrab`, with the clipboard's contents chosen by the test."""

	def __init__(self, found):
		self.found = found

	def grabclipboard(self):
		if isinstance(self.found, Exception):
			raise self.found
		return self.found


@needsPillow
class TestTheClipboard(unittest.TestCase):
	def setUp(self):
		self.real = sys.modules.get("PIL.ImageGrab")
		self.addCleanup(self.restore)

	def restore(self):
		import PIL

		if self.real is not None:
			sys.modules["PIL.ImageGrab"] = self.real
			PIL.ImageGrab = self.real

	def clipboard(self, found):
		import PIL

		fake = FakeGrab(found)
		sys.modules["PIL.ImageGrab"] = fake
		PIL.ImageGrab = fake

	def test_aCopiedImageIsDrawn(self):
		image = Image.new("RGB", (120, 80), (255, 255, 255))
		ImageDraw.Draw(image).rectangle((40, 20, 79, 59), fill=(0, 0, 0))
		self.clipboard(image)
		picture = imageSource.captureClipboard()
		self.assertEqual((picture.width, picture.height), (120, 80))
		self.assertEqual(picture.name, "clipboard picture")
		self.assertIsNotNone(picture.colour)

	def test_aCopiedFileIsOpenedAndNamed(self):
		folder = tempfile.mkdtemp()
		path = os.path.join(folder, "floor plan.png")
		Image.new("RGB", (64, 64), (0, 0, 255)).save(path)
		self.addCleanup(os.remove, path)
		self.clipboard([os.path.join(folder, "notes.txt"), path])
		picture = imageSource.captureClipboard()
		self.assertEqual(picture.name, "floor plan.png")

	def test_copiedFilesThatAreNotPicturesSaySo(self):
		folder = tempfile.mkdtemp()
		path = os.path.join(folder, "notes.txt")
		with open(path, "w") as notes:
			notes.write("not a picture")
		self.addCleanup(os.remove, path)
		self.clipboard([path])
		with self.assertRaisesRegex(imagePins.ImageRefused, "None of the copied files"):
			imageSource.captureClipboard()

	def test_anEmptyClipboardSaysHowToFillIt(self):
		self.clipboard(None)
		with self.assertRaisesRegex(imagePins.ImageRefused, "copy an image"):
			imageSource.captureClipboard()

	def test_aTinyPictureIsRefusedWithItsSize(self):
		self.clipboard(Image.new("RGB", (8, 8)))
		with self.assertRaisesRegex(imagePins.ImageRefused, "8 by 8"):
			imageSource.captureClipboard()

	def test_aClipboardThatWillNotBeReadSaysSo(self):
		self.clipboard(OSError("busy"))
		with self.assertRaisesRegex(imagePins.ImageRefused, "could not be read"):
			imageSource.captureClipboard()

	def test_aTurnedPhotographKeepsItsShapeThroughTheClipboard(self):
		"""The reviewer's probe: a 60 by 30 JPEG marked for a quarter turn came back 60 by 30,
		because the clipboard chose the size from the stored image before it was turned."""
		self.clipboard(sideways(60, 30))
		picture = imageSource.captureClipboard()
		self.assertEqual((picture.width, picture.height), (30, 60))

	def test_andALargeOneToo(self):
		self.clipboard(sideways(3000, 1500))
		picture = imageSource.captureClipboard()
		self.assertLess(picture.width, picture.height)
		self.assertAlmostEqual(picture.height / picture.width, 2.0, places=1)

	def test_aHugeImageIsKeptAtTheLimit(self):
		self.clipboard(Image.new("RGB", (3000, 2000), (255, 255, 255)))
		picture = imageSource.captureClipboard()
		self.assertLessEqual(picture.width * picture.height, imageSource.keepPixels())
		self.assertAlmostEqual(picture.width / picture.height, 1.5, places=1)


class TestTheClipboardWithoutPillow(WithoutPillow, unittest.TestCase):
	def test_itSaysWhatItNeeds(self):
		with self.assertRaisesRegex(imagePins.ImageRefused, "Pillow"):
			imageSource.captureClipboard()


def colourPicture(width, height, draw, ground=(255, 255, 255)):
	""":return: a picture with colour, drawn by `draw(ImageDraw)` on a plain ground."""
	image = Image.new("RGB", (width, height), ground)
	draw(ImageDraw.Draw(image))
	return imagePins.pictureFromImage(image, width * height, "chart")


def twoLines(draw):
	"""A red line across the top third and a blue one across the bottom third."""
	draw.line((10, 60, 390, 60), fill=(220, 0, 0), width=4)
	draw.line((10, 200, 390, 200), fill=(0, 0, 220), width=4)


@needsPillow
class TestTheColourUnderAFinger(unittest.TestCase):
	def test_theInkIsNamedNotTheAverage(self):
		"""A pin across a thin red line covers mostly white; the answer is still red."""
		picture = colourPicture(400, 300, twoLines)
		red, green, blue = picture.colourAt((100, 50, 20, 20))
		self.assertGreater(red, 180)
		self.assertLess(green, 40)
		self.assertLess(blue, 40)

	def test_paperIsNamedAsThePaper(self):
		picture = colourPicture(400, 300, twoLines, ground=(250, 245, 220))
		self.assertEqual(picture.colourAt((100, 120, 20, 20)), picture.paper)

	def closeSeries(self, draw):
		"""The reviewer's probe: a red line and a blue one with two pixels of paper between."""
		draw.line((10, 100, 390, 100), fill=(220, 0, 0), width=3)
		draw.line((10, 105, 390, 105), fill=(0, 0, 220), width=3)

	def near(self, found, wanted):
		self.assertIsNotNone(found)
		self.assertLessEqual(max(abs(a - b) for a, b in zip(found, wanted)), 12, found)

	def test_aPressOnTheRedLineOfTwoIsRed(self):
		"""The area a press covers holds both lines. It used to come back purple, which is
		neither, and drawing only that colour then drew nothing."""
		picture = colourPicture(400, 300, self.closeSeries)
		self.near(picture.colourAt((190, 90, 21, 21)), (220, 0, 0))

	def test_andOnTheBlueLineIsBlue(self):
		picture = colourPicture(400, 300, self.closeSeries)
		self.near(picture.colourAt((190, 95, 21, 21)), (0, 0, 220))

	def test_andTheColourFoundDrawsItsOwnLine(self):
		"""The whole of it: press, then draw only that colour, and one line comes back."""
		picture = colourPicture(400, 300, self.closeSeries)
		red = picture.ofOneColour(picture.colourAt((190, 90, 21, 21)))
		spot = imagePins.place(red, red.content, 96, 40)
		drawn = imagePins.renderAt(red, spot, imagePins.STROKES)
		self.assertTrue(drawn.raised)
		inked = sum(1 for grey in red.greys if grey < 128)
		self.assertLess(abs(inked - 381 * 3), 381, "about one three pixel line's worth of red")

	def test_aGreyLineBesideABlackOneIsGrey(self):
		"""Same hue, different darkness: a grid line beside a data line."""

		def lines(draw):
			draw.line((10, 100, 390, 100), fill=(0, 0, 0), width=3)
			draw.line((10, 105, 390, 105), fill=(170, 170, 170), width=3)

		picture = colourPicture(400, 300, lines)
		self.near(picture.colourAt((190, 95, 21, 21)), (170, 170, 170))

	def test_aPressOnTheFringeOfALineStillNamesTheLine(self):
		"""The nearest ink can be the anti-aliased edge, a mix with the paper. Its strongest
		neighbour of the same hue is the line itself."""

		def soft(draw):
			draw.line((10, 100, 390, 100), fill=(240, 120, 120), width=1)
			draw.line((10, 101, 390, 101), fill=(220, 0, 0), width=2)

		picture = colourPicture(400, 300, soft)
		self.near(picture.colourAt((190, 90, 21, 21)), (220, 0, 0))

	def test_aPictureWithoutColourHasNoAnswer(self):
		self.assertIsNone(greyPicture(20, 20, lambda x, y: x < 10).colourAt((0, 0, 5, 5)))

	def test_aPressSaysTheColourAndRemembersIt(self):
		picture = colourPicture(400, 300, twoLines)
		figure = imageFigure.figureFor(test_plugin.TestDrawingThePictureHere.Dots, picture, 96, 40)
		spot = imagePins.place(picture, picture.content, 96, 40)
		# The panel row the red line is on: 60 pixels down the picture, through the placement.
		left, top, _across, down = spot.source
		row = spot.top + int((60 - top) * spot.height / down)
		said = figure.describeAt(spot.left + spot.width // 2, row)
		self.assertTrue(said.startswith("red, "), said)
		self.assertIsNotNone(picture.touched)

	def test_aGreyPictureSaysOnlyWhere(self):
		picture = greyPicture(96, 40, lambda x, y: x == 0 or y == 0 or x == 95 or y == 39)
		figure = imageFigure.figureFor(test_plugin.TestDrawingThePictureHere.Dots, picture, 96, 40)
		self.assertRegex(figure.describeAt(48, 20), r"^\d+ across, \d+ down$")


@needsPillow
class TestOneColour(unittest.TestCase):
	def lines(self, picture):
		spot = imagePins.place(picture, picture.content, 96, 40)
		drawn = imagePins.renderAt(picture, spot, imagePins.STROKES)
		return sorted({y for _x, y in raised(drawn)})

	def test_bothLinesAreThereToBeginWith(self):
		self.assertEqual(len(runs(self.lines(colourPicture(400, 300, twoLines)))), 2)

	def test_onlyTheRedOneIsKept(self):
		picture = colourPicture(400, 300, twoLines)
		red = picture.ofOneColour((220, 0, 0), picture.name)
		kept = runs(self.lines(red))
		self.assertEqual(len(kept), 1)
		self.assertLess(self.lines(red)[0], 20, "the red line is the upper one")

	def test_itKeepsTheOriginalsBoxAndSize(self):
		picture = colourPicture(400, 300, twoLines)
		red = picture.ofOneColour((220, 0, 0))
		self.assertEqual(red.content, picture.content)
		self.assertEqual((red.width, red.height), (picture.width, picture.height))
		self.assertIs(red.derivedFrom, picture)

	def test_itIsCalledWhatItIs(self):
		picture = colourPicture(400, 300, twoLines)
		red = picture.ofOneColour((220, 0, 0), "chart")
		figure = imageFigure.figureFor(test_plugin.TestDrawingThePictureHere.Dots, red, 96, 40)
		self.assertIn("chart, only red", figure.name)

	def test_itsOutlinesAreOfTheColourNotOfEverything(self):
		"""It carries the original's colour for naming, not for finding edges."""
		picture = colourPicture(400, 300, twoLines)
		red = picture.ofOneColour((220, 0, 0))
		self.assertIsNone(red.channels((0, 0, 400, 300), 40, 30))

	def test_withoutColourThereIsNothingToChooseFrom(self):
		self.assertIsNone(greyPicture(20, 20, lambda x, y: x < 10).ofOneColour((0, 0, 0)))


@needsPillow
class TestEdgesInColour(unittest.TestCase):
	"""Red on green of the same brightness is one flat grey. In colour it is a square."""

	def picture(self):
		# Red at 255 and green at 130 both come to a grey of 76.
		return colourPicture(
			200, 200, lambda draw: draw.rectangle((60, 60, 139, 139), fill=(255, 0, 0)), ground=(0, 130, 0)
		)

	def test_inGreyThereIsNothingThere(self):
		picture = self.picture()
		self.assertLessEqual(picture.spread, 1)

	def test_inColourTheOutlineIsFound(self):
		picture = self.picture()
		spot = imagePins.place(picture, picture.content, 96, 40)
		outline = imagePins.renderAt(picture, spot, imagePins.EDGES)
		middle = spot.height // 2
		self.assertEqual(len(runs(x for x, y in raised(outline) if y == middle)), 2)

	def test_aGreyPictureDrawsExactlyAsItDidBefore(self):
		"""Grey is an average of the channels, so a grey picture's channels are its grey."""
		grey = greyPicture(200, 200, lambda x, y: 60 <= x < 140 and 60 <= y < 140)
		image = Image.new("RGB", (200, 200), (255, 255, 255))
		ImageDraw.Draw(image).rectangle((60, 60, 139, 139), fill=(0, 0, 0))
		coloured = imagePins.pictureFromImage(image, 200 * 200)
		spot = imagePins.place(grey, (0, 0, 200, 200), 96, 40)
		self.assertEqual(
			imagePins.renderAt(grey, spot, imagePins.EDGES).pins,
			imagePins.renderAt(coloured, spot, imagePins.EDGES).pins,
		)


class ColourPluginTestCase(unittest.TestCase):
	"""The plugin commands, with a picture that has its colours."""

	def setUp(self):
		resetPluginState()
		import braille

		self.handler = FakeHandler(test_plugin.MONARCH_ROWS, test_plugin.MONARCH_COLS)
		braille.handler = self.handler
		self.plugin = test_plugin.GlobalPlugin()
		self.addCleanup(self.tidy)
		self.mode = test_plugin.TestDrawingThePictureHere.FakeMode()
		self.plugin.graphicsMode = self.mode
		self._realCapture = imageSource.captureNavigator
		self._realClipboard = imageSource.captureClipboard
		imageSource.captureNavigator = self.capture
		flashedMessages.clear()

	def tidy(self):
		imageSource.captureNavigator = self._realCapture
		imageSource.captureClipboard = self._realClipboard
		try:
			if not self.plugin._terminated:
				self.plugin.terminate()
		except Exception:
			pass

	def capture(self):
		return colourPicture(400, 300, twoLines)


@needsPillow
class TestDrawingOnlyAColour(ColourPluginTestCase):
	def test_withNothingTouchedItSaysHowToChoose(self):
		self.plugin.script_drawPicture(None)
		self.plugin.script_pictureOnlyColour(None)
		self.assertIn("Press a routing key on the colour you want first", flashedMessages)
		self.assertEqual(len(self.mode.shown), 1)

	def test_theTouchedColourIsDrawnAlone(self):
		self.plugin.script_drawPicture(None)
		self.plugin._picture.touched = (220, 0, 0)
		self.plugin.script_pictureOnlyColour(None)
		self.assertEqual(len(self.mode.shown), 2)
		self.assertIn("only red", self.mode.source.name)
		self.assertIsNotNone(self.plugin._picture.derivedFrom)

	def test_itKeepsThePlaceAsAStyleChangeDoes(self):
		self.plugin.script_drawPicture(None)
		self.plugin._picture.touched = (220, 0, 0)
		self.mode.askedFor = []
		self.plugin.script_pictureOnlyColour(None)
		self.assertEqual(self.mode.askedFor.count(None), 0)

	def test_pressingAgainBringsEveryColourBack(self):
		self.plugin.script_drawPicture(None)
		original = self.plugin._picture
		original.touched = (220, 0, 0)
		self.plugin.script_pictureOnlyColour(None)
		self.plugin.script_pictureOnlyColour(None)
		self.assertIs(self.plugin._picture, original)
		self.assertNotIn("only", self.mode.source.name)

	def test_aStyleChangeKeepsTheColour(self):
		self.plugin.script_drawPicture(None)
		self.plugin._picture.touched = (220, 0, 0)
		self.plugin.script_pictureOnlyColour(None)
		self.plugin.script_pictureStyle(None)
		self.assertIn("only red", self.mode.source.name)

	def test_theBackgroundIsNotAColourToDraw(self):
		self.plugin.script_drawPicture(None)
		self.plugin._picture.touched = self.plugin._picture.paper
		self.plugin.script_pictureOnlyColour(None)
		self.assertTrue(any("background" in said for said in flashedMessages))
		self.assertEqual(len(self.mode.shown), 1)

	def test_aPictureWithoutColourSaysWhy(self):
		imageSource.captureNavigator = lambda: greyPicture(96, 40, lambda x, y: x < 30)
		self.plugin.script_drawPicture(None)
		self.plugin.script_pictureOnlyColour(None)
		self.assertTrue(any("no colours" in said for said in flashedMessages))

	def test_withNoPictureUpItSaysSo(self):
		self.plugin.script_pictureOnlyColour(None)
		self.assertIn("There is no picture to change", flashedMessages)


@needsPillow
class TestDrawingTheClipboard(ColourPluginTestCase):
	def test_theClipboardIsDrawnAndTheScreenIsNotRead(self):
		asked = []

		def fromClipboard():
			asked.append(True)
			return colourPicture(200, 100, twoLines)

		imageSource.captureClipboard = fromClipboard
		imageSource.captureNavigator = lambda: self.fail("the screen was read")
		self.plugin.script_drawClipboardPicture(None)
		self.assertEqual(asked, [True])
		self.assertEqual(len(self.mode.shown), 1)

	def test_aRefusalIsSaid(self):
		def refuse():
			raise imagePins.ImageRefused("There is no picture on the clipboard")

		imageSource.captureClipboard = refuse
		self.plugin.script_drawClipboardPicture(None)
		self.assertIn("There is no picture on the clipboard", flashedMessages)

	def test_aStyleChangeRedrawsTheClipboardPictureNotTheScreen(self):
		imageSource.captureClipboard = lambda: colourPicture(200, 100, twoLines)
		imageSource.captureNavigator = lambda: self.fail("the screen was read")
		self.plugin.script_drawClipboardPicture(None)
		self.plugin.script_pictureStyle(None)
		self.assertEqual(len(self.mode.shown), 2)


class TestWhatAPictureTooDetailedAdvises(unittest.TestCase):
	"""Found on a logo: "magnify to read it", then, on magnifying, "no more detail to show".

	Both were true. The advice now knows what the zoom key will do.
	"""

	def grid(self):
		""":return: a fine grid, one connected network too dense for the pins."""
		return greyPicture(192, 80, lambda x, y: x % 3 == 0 or y % 3 == 0)

	def figure(self, picture):
		return imageFigure.figureFor(test_plugin.TestDrawingThePictureHere.Dots, picture, 96, 40)

	def atTheLimit(self, picture):
		""":return: the whole picture composed as though no zoom were left."""
		return imageFigure._compose(
			test_plugin.TestDrawingThePictureHere.Dots,
			picture,
			picture.content,
			96,
			40,
			imagePins.EDGES,
			False,
			whole=True,
			canMagnify=False,
		)

	def test_theZoomLimitIsTheModesOwn(self):
		"""Fourteen points: two zooms leave three and a half, a third would leave under two."""
		self.assertTrue(imageFigure.canMagnify(14, 1.0))
		self.assertTrue(imageFigure.canMagnify(14, 0.5))
		self.assertFalse(imageFigure.canMagnify(14, 0.25))

	def test_andTheTopOfTheLadderToo(self):
		self.assertFalse(imageFigure.canMagnify(100000, 1 / 32))

	def test_whenMagnifyingWillHelpItSaysSo(self):
		self.assertIn("magnify", self.figure(self.grid()).note)

	def test_andAfterAZoomThatLeavesAnotherToo(self):
		"""Twelve points: at two times there are six left, so one more zoom is allowed."""
		window = self.figure(self.grid()).redraw(0.25, 0.5, 96, 40, top=0.25, down=0.5)
		self.assertIn("magnify", window.note)

	def test_whenItCannotItDoesNotSayMagnify(self):
		note = self.atTheLimit(self.grid()).note
		self.assertTrue(note)
		self.assertNotIn("magnify", note)

	def test_aScreenCaptureIsToldAboutTheClipboard(self):
		"""The clipboard has the image at its own size, often larger than it was shown."""
		picture = self.grid()
		picture.fromScreen = True
		self.assertIn("clipboard", self.atTheLimit(picture).note)

	def test_somethingAlreadyFromTheClipboardIsNot(self):
		self.assertNotIn("clipboard", self.atTheLimit(self.grid()).note)

	@needsPillow
	def test_oneColourOfAScreenCaptureIsStillAScreenCapture(self):
		"""So the advice about the clipboard still reaches a reader looking at one colour."""
		picture = colourPicture(400, 300, twoLines)
		picture.fromScreen = True
		self.assertTrue(picture.ofOneColour((220, 0, 0)).fromScreen)


@needsPillow
class TestSingleLinesInColour(unittest.TestCase):
	"""The reviewer's probe: outlines drew a red stroke on a green of the same brightness, and
	single lines said there was nothing to draw."""

	def stroke(self):
		# Red at 255 and green at 130 both come to a grey of 76.
		return colourPicture(
			200,
			200,
			lambda draw: draw.line((20, 100, 180, 100), fill=(255, 0, 0), width=6),
			ground=(0, 130, 0),
		)

	def test_itIsFlatInGrey(self):
		self.assertLessEqual(self.stroke().spread, 1)

	def test_singleLinesDrawsItAsOneLine(self):
		picture = self.stroke()
		spot = imagePins.place(picture, picture.content, 96, 40)
		drawn = imagePins.renderAt(picture, spot, imagePins.STROKES)
		rows = {y for _x, y in raised(drawn)}
		self.assertTrue(drawn.raised)
		self.assertLessEqual(len(rows), 2, "one line across, not a band or rails")

	def test_aBlackAndWhiteDrawingIsExactlyAsBefore(self):
		"""A grey g is 255 - g from white, so its distance from the paper is g again."""

		def square(draw):
			draw.rectangle((40, 40, 159, 159), outline=(0, 0, 0), width=8)
			draw.line((40, 100, 159, 100), fill=(128, 128, 128), width=5)

		coloured = colourPicture(200, 200, square)
		grey = imagePins.Picture(bytearray(coloured.greys), 200, 200)
		spot = imagePins.place(grey, grey.content, 96, 40)
		self.assertEqual(
			imagePins.renderAt(coloured, spot, imagePins.STROKES).pins,
			imagePins.renderAt(grey, spot, imagePins.STROKES).pins,
		)

	def test_aPictureWithoutColourIsItself(self):
		picture = greyPicture(20, 20, lambda x, y: x < 10)
		self.assertIs(picture.againstPaper(), picture)

	def test_oneColourOfAPictureIsItselfToo(self):
		"""Already dark ink on white; measuring it against the paper again would undo that."""
		picture = colourPicture(400, 300, twoLines)
		red = picture.ofOneColour((220, 0, 0))
		self.assertIs(red.againstPaper(), red)
