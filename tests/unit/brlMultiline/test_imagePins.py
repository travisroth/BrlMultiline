# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for turning captured pixels into raised pins.

This is the part of image import a reader cannot check. A chart that is wrong is wrong about
numbers somebody can look up; a picture that is wrong is a shape under a hand with nothing to
compare it against, and a reader feeling a smear has no way to tell whether the picture was
bad, the reduction lost it, or the threshold ate it.

So the tests here are mostly about the two failures that look identical to a finger and are
completely different faults: a panel with almost nothing raised, and a panel with almost
everything raised. Both feel like a broken display. Both have to be impossible by construction
rather than unlikely.
"""

import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline import imagePins  # noqa: E402


class FakePixel:
	"""One pixel as `screenBitmap` hands it over."""

	def __init__(self, red, green, blue):
		self.rgbRed = red
		self.rgbGreen = green
		self.rgbBlue = blue


def picture(rows, name=""):
	""":return: a `Picture` from rows of brightness values, 0 to 255."""
	height = len(rows)
	width = len(rows[0]) if rows else 0
	greys = bytearray()
	for row in rows:
		greys.extend(row)
	return imagePins.Picture(greys, width, height, name)


def flat(width, height, grey=128):
	""":return: a picture of one tone."""
	return picture([[grey] * width for _ in range(height)])


def stripes(width, height, period=4):
	""":return: a picture of vertical stripes, which has edges everywhere."""
	return picture([[0 if (x // period) % 2 else 255 for x in range(width)] for _ in range(height)])


def blob(width, height, size=None):
	"""A dark square on a light field, which is the shape every silhouette test wants."""
	size = size or min(width, height) // 3
	left = (width - size) // 2
	top = (height - size) // 2
	rows = []
	for y in range(height):
		rows.append(
			[0 if (left <= x < left + size and top <= y < top + size) else 255 for x in range(width)],
		)
	return picture(rows)


class TestReadingPixels(unittest.TestCase):
	"""Brightness out of colour, which has one thing in it worth a test."""

	def test_greyIsTheWeightedSum(self):
		rows = [[FakePixel(255, 255, 255), FakePixel(0, 0, 0)]]
		self.assertEqual(list(imagePins.greysFromPixels(rows, 2, 1)), [255, 0])

	def test_redAndBlueAreNotInterchangeable(self):
		"""NVDA's own `rgbPixelBrightness` weights blue at 0.3 and red at 0.11, which is the
		standard formula with those two terms exchanged. Red is the heavier of the two, and a
		red subject on a dark ground has to come out lighter than a blue one — otherwise the
		contrast of anything told apart by being red or blue arrives backwards."""
		rows = [[FakePixel(255, 0, 0), FakePixel(0, 0, 255)]]
		red, blue = imagePins.greysFromPixels(rows, 2, 1)
		self.assertGreater(red, blue)


class TestReducing(unittest.TestCase):
	"""Area average, and why it is not point sampling.

	At four pixels to a pin, sampling one of the four drops a one pixel line whenever it falls
	between the samples — so a diagram's strokes come and go along their own length, which is
	visibly wrong to a hand and wrong differently each time the window moves.
	"""

	def test_aUniformPictureReducesToItsOwnTone(self):
		reduced = flat(8, 8, 200).reduce((0, 0, 8, 8), 2, 2)
		self.assertEqual(list(reduced), [200, 200, 200, 200])

	def test_aThinLineSurvivesAsAGrey(self):
		"""The whole argument for averaging. One dark column in four averages to a quarter
		below white; point sampled it is either the whole cell or none of it."""
		rows = [[0 if x == 1 else 255 for x in range(4)] for _ in range(4)]
		reduced = picture(rows).reduce((0, 0, 4, 4), 1, 1)
		self.assertLess(reduced[0], 255)
		self.assertGreater(reduced[0], 0)

	def test_halvesAreAveragedSeparately(self):
		rows = [[0, 0, 255, 255] for _ in range(2)]
		self.assertEqual(list(picture(rows).reduce((0, 0, 4, 2), 2, 1)), [0, 255])

	def test_aBoxOutsideThePictureIsClipped(self):
		"""What `fitBox` hands over for a picture that is not the panel's shape. The extra is
		background rather than a stretched edge."""
		reduced = flat(4, 4, 100).reduce((-10, -10, 40, 40), 2, 2)
		self.assertEqual(len(reduced), 4)

	def test_askingForNothingGivesNothing(self):
		self.assertEqual(len(flat(4, 4).reduce((0, 0, 4, 4), 0, 0)), 0)


class TestOutlines(unittest.TestCase):
	"""Sobel, then a cut chosen by how much of the panel it leaves raised."""

	def test_aFlatFieldHasNoEdges(self):
		greys = flat(10, 10, 128).reduce((0, 0, 10, 10), 10, 10)
		self.assertEqual(imagePins.edgePins(greys, 10, 10).raised, 0)

	def test_anEdgeIsFoundWhereTheChangeIs(self):
		rows = [[0] * 5 + [255] * 5 for _ in range(10)]
		greys = picture(rows).reduce((0, 0, 10, 10), 10, 10)
		found = imagePins.edgePins(greys, 10, 10)
		self.assertTrue(found.raised)
		# Every raised pin is on one of the two columns either side of the step.
		for at, pin in enumerate(found.pins):
			if pin:
				self.assertIn(at % 10, (4, 5))

	def test_theBorderIsNeverRaised(self):
		"""A one pin frame that is really the edge of the capture reads as part of the
		picture, and a picture with a box round it is one the reader has to be told to
		ignore."""
		greys = stripes(20, 20, period=2).reduce((0, 0, 20, 20), 20, 20)
		found = imagePins.edgePins(greys, 20, 20)
		for at, pin in enumerate(found.pins):
			if pin:
				x, y = at % 20, at // 20
				self.assertNotIn(x, (0, 19))
				self.assertNotIn(y, (0, 19))

	def test_aBusyPictureStillComesBackReadable(self):
		"""The failure this design exists to make impossible. Stripes everywhere are edges
		everywhere, and a fixed threshold would raise the whole panel — which is not a picture,
		it is a texture, and a hand cannot tell it from a display stuck on."""
		greys = stripes(32, 24, period=2).reduce((0, 0, 32, 24), 32, 24)
		found = imagePins.edgePins(greys, 32, 24)
		self.assertLess(found.coverage, 0.35)

	def test_aFaintPictureStillComesBackReadable(self):
		"""And the mirror of it. A soft image has small gradients everywhere and a fixed
		threshold would raise none of them, which feels exactly like a display stuck off."""
		rows = [[120 if (x // 3) % 2 else 132 for x in range(32)] for _ in range(24)]
		greys = picture(rows).reduce((0, 0, 32, 24), 32, 24)
		found = imagePins.edgePins(greys, 32, 24)
		self.assertGreater(found.coverage, 0.05)

	def test_aPanelTooSmallToDifferentiateIsEmptyRatherThanWrong(self):
		self.assertEqual(imagePins.edgePins(bytearray([0, 255, 0, 255]), 2, 2).raised, 0)


class TestSilhouettes(unittest.TestCase):
	"""Which side of the picture is the subject, and how much of the panel it may have."""

	def test_theDarkSubjectIsRaised(self):
		greys = blob(24, 24).reduce((0, 0, 24, 24), 24, 24)
		found = imagePins.brightnessPins(greys, 24, 24)
		self.assertTrue(found.raised)
		# The middle is the blob and the corner is the field.
		self.assertTrue(found.pins[12 * 24 + 12])
		self.assertFalse(found.pins[0])

	def test_aLightSubjectOnADarkGroundIsAlsoRaised(self):
		"""The case a "raise the dark" rule gets backwards, and it is not exotic: a white
		logo on a dark header is as ordinary on a screen as black ink on a page. Raising the
		dark there hands the reader a solid panel with a hole in it."""
		rows = []
		for y in range(24):
			rows.append([255 if (8 <= x < 16 and 8 <= y < 16) else 0 for x in range(24)])
		greys = picture(rows).reduce((0, 0, 24, 24), 24, 24)
		found = imagePins.brightnessPins(greys, 24, 24)
		self.assertTrue(found.pins[12 * 24 + 12])
		self.assertFalse(found.pins[0])

	def test_invertingSwapsWhichSideIsRaised(self):
		greys = blob(24, 24).reduce((0, 0, 24, 24), 24, 24)
		plain = imagePins.brightnessPins(greys, 24, 24)
		other = imagePins.brightnessPins(greys, 24, 24, invert=True)
		self.assertTrue(plain.pins[12 * 24 + 12])
		self.assertFalse(other.pins[12 * 24 + 12])

	def test_theSubjectKeepsItsOwnSize(self):
		"""How much of the frame a subject fills is information: a face filling the picture
		and a bird in a wide sky should not arrive at the same size."""
		small = blob(24, 24, size=4)
		large = blob(24, 24, size=12)
		smaller = imagePins.brightnessPins(small.reduce((0, 0, 24, 24), 24, 24), 24, 24)
		larger = imagePins.brightnessPins(large.reduce((0, 0, 24, 24), 24, 24), 24, 24)
		self.assertLess(smaller.raised, larger.raised)

	def test_aVeryHighContrastPictureDoesNotFillThePanel(self):
		"""Half black and half white splits cleanly, and raising the whole of one half is
		half the panel. The budget is what stops a silhouette becoming a slab."""
		rows = [[0] * 12 + [255] * 12 for _ in range(24)]
		greys = picture(rows).reduce((0, 0, 24, 24), 24, 24)
		found = imagePins.brightnessPins(greys, 24, 24)
		self.assertLessEqual(found.coverage, imagePins.BRIGHTNESS_MOST + 0.01)


class TestWhatIsRefused(unittest.TestCase):
	"""The refusals, which are the reason a reader is never handed a blank panel silently."""

	def test_aFlatPictureIsRefused(self):
		with self.assertRaises(imagePins.ImageRefused):
			imagePins.render(flat(40, 40, 128), (0, 0, 40, 40), 20, 20)

	def test_aNearlyFlatPictureIsRefusedToo(self):
		"""Below `PLAIN` there is nothing to find, and every threshold would be arbitrary:
		Otsu on a histogram with no shape splits noise, and the reader feels the noise."""
		rows = [[128 + (x % 3) for x in range(40)] for _ in range(40)]
		with self.assertRaises(imagePins.ImageRefused):
			imagePins.render(picture(rows), (0, 0, 40, 40), 20, 20)

	def test_noRoomIsRefused(self):
		with self.assertRaises(imagePins.ImageRefused):
			imagePins.render(blob(40, 40), (0, 0, 40, 40), 0, 0)

	def test_aPictureWithSomethingInItIsNotRefused(self):
		found = imagePins.render(blob(40, 40), (0, 0, 40, 40), 20, 20)
		self.assertTrue(found.raised)

	def test_everyRefusalSaysSomething(self):
		"""A reader told only that it failed cannot tell whether to move, change the style,
		or give up."""
		try:
			imagePins.render(flat(40, 40), (0, 0, 40, 40), 20, 20)
		except imagePins.ImageRefused as refusal:
			self.assertTrue(str(refusal))


class TestDrawing(unittest.TestCase):
	"""Getting the pins onto a buffer."""

	class FakeBuffer:
		def __init__(self):
			self.dots = set()

		def setDot(self, x, y):
			self.dots.add((x, y))

	def test_raisedPinsBecomeDots(self):
		rendering = imagePins.Rendering(bytearray([1, 0, 0, 1]), 2, 2, 2)
		buffer = self.FakeBuffer()
		rendering.draw(buffer)
		self.assertEqual(buffer.dots, {(0, 0), (1, 1)})


class TestFittingToThePanel(unittest.TestCase):
	"""A picture squeezed to the panel's shape is a picture of something else.

	The panel is over two to one and most pictures are not, so this is the common case rather
	than an edge one — a face becomes a wide face, a circle on a map becomes an ellipse, and a
	reader has no way at all to know it happened.
	"""

	def test_aSquarePictureOnAWidePanelGrowsSideways(self):
		box = imagePins.fitBox(flat(100, 100), 96, 40)
		left, top, width, height = box
		self.assertEqual((top, height), (0, 100))
		self.assertGreater(width, 100)
		self.assertLess(left, 0)

	def test_aWidePictureOnAWidePanelGrowsDownwards(self):
		box = imagePins.fitBox(flat(400, 50), 96, 40)
		left, top, width, height = box
		self.assertEqual((left, width), (0, 400))
		self.assertGreater(height, 50)

	def test_theProportionsCameOutRight(self):
		_, _, width, height = imagePins.fitBox(flat(100, 100), 96, 40)
		self.assertAlmostEqual(width / height, 96 / 40, places=1)

	def test_nothingToFitIsNotAnError(self):
		self.assertEqual(imagePins.fitBox(flat(0, 0) if False else picture([]), 96, 40), (0, 0, 0, 0))


class TestWindowing(unittest.TestCase):
	"""Narrowing the source box to a fraction, which is what zoom hands over."""

	def test_aHalfWindowIsHalfTheBox(self):
		found = imagePins.windowOf(flat(100, 100), (0, 0, 100, 100), 0.25, 0.25, 0.5, 0.5)
		self.assertEqual(found, (25, 25, 50, 50))

	def test_theWholeWindowIsTheWholeBox(self):
		self.assertEqual(
			imagePins.windowOf(flat(80, 60), (0, 0, 80, 60), 0.0, 0.0, 1.0, 1.0),
			(0, 0, 80, 60),
		)

	def test_aWindowNeverNarrowsToNothing(self):
		left, top, width, height = imagePins.windowOf(flat(100, 100), (0, 0, 100, 100), 0.0, 0.0, 0.0, 0.0)
		self.assertEqual((width, height), (1, 1))

	def test_aWindowOfAnOffsetBoxKeepsTheOffset(self):
		"""`fitBox` can start above or left of the picture, and a window of it has to stay
		in the same coordinates or the reader's zoom would jump."""
		self.assertEqual(
			imagePins.windowOf(flat(100, 100), (-20, 10, 140, 60), 0.5, 0.0, 0.5, 1.0),
			(50, 10, 70, 60),
		)


class TestOneWholePicture(unittest.TestCase):
	"""End to end on the shapes a reader actually meets, which is the only check that the
	pieces above agree with each other about coordinates."""

	def test_aBlobDrawsAsABlob(self):
		found = imagePins.render(
			blob(96, 96), imagePins.fitBox(blob(96, 96), 48, 20), 48, 20, imagePins.BRIGHTNESS
		)
		self.assertTrue(found.raised)
		self.assertLess(found.coverage, 0.6)

	def test_theSameBlobHasOutlinesToo(self):
		source = blob(96, 96)
		found = imagePins.render(source, imagePins.fitBox(source, 48, 20), 48, 20, imagePins.EDGES)
		self.assertTrue(found.raised)
		self.assertLess(found.coverage, 0.35)

	def test_aWindowOfItIsStillReadable(self):
		"""The property that makes zoom worth having: a quarter of the picture reduced from
		its own pixels comes back with a readable density rather than four pins where one was."""
		source = blob(96, 96)
		box = imagePins.fitBox(source, 48, 20)
		window = imagePins.windowOf(source, box, 0.25, 0.25, 0.5, 0.5)
		found = imagePins.render(source, window, 48, 20, imagePins.EDGES)
		self.assertTrue(found.raised)
		self.assertLess(found.coverage, 0.35)
