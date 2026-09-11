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
		"""Nothing asks for this any more, since `place` keeps the source inside the picture,
		but the padding behaviour is kept: a caller that does reach outside gets background
		rather than a stretched edge."""
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

	def test_aSquarePictureOnAWidePanelIsGivenASquareOfIt(self):
		spot = imagePins.place(flat(100, 100), (0, 0, 100, 100), 96, 40)
		self.assertEqual((spot.width, spot.height), (40, 40))

	def test_andIsCentredInIt(self):
		spot = imagePins.place(flat(100, 100), (0, 0, 100, 100), 96, 40)
		self.assertEqual((spot.left, spot.top), (28, 0))

	def test_theSourceStaysInsideThePicture(self):
		"""The whole point of the two rectangles: the detector is never handed a pixel the
		add-on invented. See `Placement`."""
		spot = imagePins.place(flat(100, 100), (0, 0, 100, 100), 96, 40)
		self.assertEqual(spot.source, (0, 0, 100, 100))

	def test_aWidePictureGetsTheFullWidthAndMarginAboveAndBelow(self):
		spot = imagePins.place(flat(400, 50), (0, 0, 400, 50), 96, 40)
		self.assertEqual((spot.left, spot.width), (0, 96))
		self.assertEqual(spot.height, 12)
		self.assertEqual(spot.top, 14)

	def test_theProportionsCameOutRight(self):
		spot = imagePins.place(flat(100, 100), (0, 0, 100, 100), 96, 40)
		self.assertAlmostEqual(spot.width / spot.height, 1.0, places=1)

	def test_aPictureAlreadyThePanelShapeGetsTheWholePanel(self):
		spot = imagePins.place(flat(240, 100), (0, 0, 240, 100), 96, 40)
		self.assertEqual((spot.left, spot.top, spot.width, spot.height), (0, 0, 96, 40))

	def test_theContentAreaIsWhatACoverageLimitIsAFractionOf(self):
		"""Not the panel. A square picture that raised every pin it owns would otherwise be
		under a sixth of the panel and so never reach any ceiling at all."""
		self.assertEqual(imagePins.place(flat(100, 100), (0, 0, 100, 100), 96, 40).area, 1600)

	def test_nothingToFitIsNotAnError(self):
		spot = imagePins.place(picture([]), (0, 0, 0, 0), 96, 40)
		self.assertEqual((spot.width, spot.height), (0, 0))


class TestWhatAWidePictureIsMeasuredAgainst(unittest.TestCase):
	"""A toolbar is nine hundred pixels by thirty-six, and that broke the yardstick.

	The reference grid kept the picture proportions and put 48 cells on the long side, which
	left two rows on the short one. A Sobel needs three. So the strongest gradient in the
	picture came back as nought, every window compared itself against nought and passed, and
	the check meant to catch a window of pure background was off for every wide picture on the
	screen -- a toolbar, a menu bar, a tab strip, most of what a reader points at.

	It went unnoticed because its failure mode is to permit. Nothing looked wrong; the check
	simply never said no.
	"""

	def bar(self, width=900, height=36):
		""":return: a strip of small dark icons on a plain bar."""
		greys = bytearray([235]) * (width * height)
		for n in range(12):
			middle = 40 + n * 70
			for y in range(10, 26):
				for x in range(middle - 8, middle + 8):
					if 0 <= x < width and abs(x - middle) + abs(y - 18) < 9:
						greys[y * width + x] = 30
		return imagePins.Picture(greys, width, height, "toolbar")

	def test_theReferenceIsMeasurableAtAll(self):
		self.assertGreater(self.bar().strength.gradient, 0)

	def test_soAWindowOfBareBarIsRecognisedAsBackground(self):
		"""The whole point of measuring it. Off the end of the icons there is nothing, and
		without a reference to compare against nothing is what gets drawn as something."""
		picture = self.bar()
		whole = (0, 0, picture.width, picture.height)
		window = imagePins.windowOf(picture, whole, 0.93, 0.0, 0.07, 1.0)
		found = imagePins.render(picture, window, 96, 40, imagePins.EDGES, False, False)
		self.assertTrue(found.barren)

	def test_andAWindowOnTheIconsIsNot(self):
		"""The other half, or the test above would pass on a pipeline that refused
		everything."""
		picture = self.bar()
		whole = (0, 0, picture.width, picture.height)
		window = imagePins.windowOf(picture, whole, 0.0, 0.0, 0.2, 1.0)
		found = imagePins.render(picture, window, 96, 40, imagePins.EDGES, False, False)
		self.assertFalse(found.barren)
		self.assertTrue(found.raised)

	def test_aSquarePictureIsUnaffected(self):
		"""The floor must not distort the ordinary case."""
		greys = bytearray(
			0 if 30 <= x < 70 and 30 <= y < 70 else 255 for y in range(100) for x in range(100)
		)
		self.assertGreater(imagePins.Picture(greys, 100, 100, "square").strength.gradient, 0)

	def test_somethingTooThinToDrawSaysThatRatherThanSomethingElse(self):
		"""Sixteen hundred by twenty fits onto the panel as a strip one pin deep. Nothing can
		be drawn in that, and the reader should hear what is wrong with the shape rather than
		a count of how few pins came out."""
		greys = bytearray([240]) * (1600 * 20)
		for y in range(6, 14):
			for x in range(100, 140):
				greys[y * 1600 + x] = 20
		picture = imagePins.Picture(greys, 1600, 20, "a rule")
		with self.assertRaises(imagePins.ImageRefused) as refused:
			imagePins.render(picture, (0, 0, 1600, 20), 96, 40, imagePins.EDGES)
		self.assertIn("thin", str(refused.exception))


class TestNothingHereAnswersTheSameWayEveryTime(unittest.TestCase):
	"""Three ways of finding nothing, and they used to disagree with each other.

	A window of flat tone raised one exception, a window below the strength floor came back
	blank and marked, and a window that detected too little raised a different exception. Two
	of those three stop the zoom, because a window that will not compose is a move that does
	not happen -- so which check happened to notice decided whether the reader could move.
	"""

	def picture(self):
		""":return: a small dark square on a large light field, with room around it."""
		greys = bytearray(
			0 if 10 <= x < 30 and 10 <= y < 30 else 250 for y in range(200) for x in range(200)
		)
		return imagePins.Picture(greys, 200, 200, "a corner mark")

	def emptyCorner(self, mode):
		""":return: a window on the far corner, where there is nothing at all."""
		picture = self.picture()
		window = imagePins.windowOf(picture, (0, 0, 200, 200), 0.6, 0.6, 0.3, 0.3)
		return imagePins.render(picture, window, 96, 40, mode, False, False)

	def test_aFlatWindowIsBlankRatherThanRefused(self):
		for mode in (imagePins.EDGES, imagePins.BRIGHTNESS):
			with self.subTest(mode=mode):
				found = self.emptyCorner(mode)
				self.assertTrue(found.barren)
				self.assertEqual(found.raised, 0)

	def test_butAFlatWholePictureIsStillRefused(self):
		flat = imagePins.Picture(bytearray([250] * (200 * 200)), 200, 200, "nothing")
		with self.assertRaises(imagePins.ImageRefused):
			imagePins.render(flat, (0, 0, 200, 200), 96, 40, imagePins.EDGES)

	def test_aWindowIsNeverRefusedForBeingSparse(self):
		"""A few true pins are worth more to a reader panning about than a refusal that stops
		them moving. The display has already proved itself by drawing the whole picture."""
		picture = self.picture()
		window = imagePins.windowOf(picture, (0, 0, 200, 200), 0.0, 0.0, 0.9, 0.9)
		found = imagePins.render(picture, window, 96, 40, imagePins.EDGES, False, False)
		self.assertTrue(found.raised)


class TestThinningAStep(unittest.TestCase):
	"""One transition should answer once.

	Separate from the doubled contour of a thick stroke, which is two real edges. This is one
	edge coming back as two columns because the suppression kept a cell that merely equalled
	its neighbour, and a plateau of equals kept all of itself.
	"""

	def step(self, width=12, height=8, at=6):
		""":return: a picture that is light to the left of `at` and dark from there on."""
		greys = bytearray(255 if x < at else 0 for _y in range(height) for x in range(width))
		return greys, width, height

	def columns(self, greys, width, height):
		found = imagePins.edgePins(greys, width, height)
		return sorted({place % width for place, pin in enumerate(found.pins) if pin})

	def test_aSingleStepRaisesOneColumn(self):
		self.assertEqual(len(self.columns(*self.step())), 1)

	def test_theSameOnALargerGrid(self):
		self.assertEqual(len(self.columns(*self.step(40, 40, 20))), 1)

	def test_aHorizontalStepRaisesOneRow(self):
		width = height = 16
		greys = bytearray(255 if y < 8 else 0 for y in range(height) for _x in range(width))
		found = imagePins.edgePins(greys, width, height)
		rows = sorted({place // width for place, pin in enumerate(found.pins) if pin})
		self.assertEqual(len(rows), 1)


class TestKeepingContoursWhole(unittest.TestCase):
	"""A ceiling applied pin by pin undoes the hysteresis that just ran.

	Ranking every chosen cell and keeping the strongest is the obvious way to fit a budget and
	the one thing this must not do: a dense diagram came back as one connected edge map turned
	into hundreds of fragments. A hand following a line through that finds it stop, start again
	further on, and stop again, which says something about the picture that is not true.
	"""

	def drawn(self, greys, side=200, mode=imagePins.EDGES):
		picture = imagePins.Picture(greys, side, side, "dense")
		found = imagePins.render(picture, (0, 0, side, side), 96, 40, mode)
		places = {place for place, pin in enumerate(found.pins) if pin}
		return found, imagePins._components(places, found.width, found.height)

	def plan(self, side=200, every=100):
		""":return: walls on a plain ground.

		`every` decides whether the drawing fits the panel at all: walls every hundred pixels
		come to about 160 pins of contour against a ceiling of 256, and every fifty to well
		over it. Both are worth having, because they are the two answers this has to give.
		"""
		greys = bytearray([255]) * (side * side)
		for y in range(side):
			for x in range(side):
				if x % every == 0 or y % every == 0:
					greys[y * side + x] = 0
		return greys

	def test_theWallsComeBackAsWholeLines(self):
		found, groups = self.drawn(self.plan())
		self.assertLessEqual(found.raised, 256)
		self.assertFalse(found.crowded)
		biggest = max(len(group) for group in groups)
		self.assertGreater(biggest, 30, f"the longest contour is only {biggest} pins")

	def test_aPlanWithMoreWallsThanPinsSaysSoRatherThanFragmentingQuietly(self):
		"""The other answer. There is no honest way to draw more contour than the panel has
		pins, so what is drawn is a texture and the reader is told to magnify."""
		found, _groups = self.drawn(self.plan(every=50))
		self.assertTrue(found.crowded)

	def test_aPictureTooDenseToDrawSaysSo(self):
		"""When no threshold and no whole contour will fit, what is drawn is a texture. That
		is an honest account of a texture and a dishonest one of a drawing, and a hand cannot
		tell them apart -- so it is said rather than left to be discovered."""
		side = 200
		greys = bytearray([255]) * (side * side)
		for y in range(side):
			for x in range(side):
				if x % 4 == 0 or y % 4 == 0:
					greys[y * side + x] = 0
		found, _groups = self.drawn(greys)
		self.assertTrue(found.crowded)

	def test_anOrdinaryDrawingIsNotCalledCrowded(self):
		side = 200
		greys = bytearray([255]) * (side * side)
		for y in range(side):
			for x in range(side):
				if 40 <= x < 160 and 40 <= y < 160 and (x in (40, 159) or y in (40, 159)):
					greys[y * side + x] = 0
		found, groups = self.drawn(greys)
		self.assertFalse(found.crowded)
		self.assertLessEqual(len(groups), 4)


class TestOneDarkSpeckDoesNotSpoilThePicture(unittest.TestCase):
	"""The reference has to survive the rest of the picture.

	Taken as the single strongest gradient, one four pixel speck of pure black set the yardstick
	for everything else in the capture -- and a genuine feature twenty-five tones below its
	surroundings was then refused as background in both styles. Photographs, maps and charts
	all routinely hold one very dark thing and a lot of softer ones.
	"""

	def mixed(self, side=240, speck=True):
		""":return: a soft feature, with or without an unrelated very dark speck elsewhere."""
		greys = bytearray([200]) * (side * side)
		for y in range(120, 200):
			for x in range(120, 200):
				greys[y * side + x] = 175
		if speck:
			for y in range(10, 22):
				for x in range(10, 22):
					greys[y * side + x] = 0
		return imagePins.Picture(greys, side, side, "mixed")

	def softWindow(self, picture, mode):
		box = imagePins.windowOf(picture, (0, 0, picture.width, picture.height), 0.45, 0.45, 0.45, 0.45)
		return imagePins.render(picture, box, 96, 40, mode, False, False)

	def test_theSoftFeatureIsDrawnDespiteTheSpeck(self):
		for mode in (imagePins.EDGES, imagePins.BRIGHTNESS):
			with self.subTest(mode=mode):
				found = self.softWindow(self.mixed(), mode)
				self.assertFalse(found.barren)
				self.assertTrue(found.raised)

	def test_andIsDrawnWithoutItToo(self):
		"""The control. If this failed the test above would prove nothing about the speck."""
		found = self.softWindow(self.mixed(speck=False), imagePins.EDGES)
		self.assertFalse(found.barren)

	def test_realBackgroundIsStillRecognised(self):
		"""The floor must not let everything through. Flat ground is still flat ground."""
		picture = self.mixed()
		box = imagePins.windowOf(picture, (0, 0, 240, 240), 0.55, 0.05, 0.3, 0.3)
		self.assertTrue(imagePins.render(picture, box, 96, 40, imagePins.EDGES, False, False).barren)


class TestABoxThatReachesOutside(unittest.TestCase):
	"""Clipping has to cut a rectangle, not slide it.

	Moving the near edge and then measuring the size from there keeps the width, so a box
	starting outside the picture came back covering more of it than the box did.
	"""

	def test_aBoxStartingLeftOfThePictureKeepsOnlyTheOverlap(self):
		spot = imagePins.place(flat(10, 10), (-5, 0, 10, 10), 96, 40)
		self.assertEqual(spot.source, (0, 0, 5, 10))

	def test_aBoxRunningOffTheRightIsCutToo(self):
		spot = imagePins.place(flat(10, 10), (6, 0, 10, 10), 96, 40)
		self.assertEqual(spot.source, (6, 0, 4, 10))

	def test_aBoxEntirelyPastThePictureIsNothing(self):
		spot = imagePins.place(flat(10, 10), (40, 0, 10, 10), 96, 40)
		self.assertEqual((spot.width, spot.height), (0, 0))

	def test_aBoxCoveringEverythingIsTheWholePicture(self):
		spot = imagePins.place(flat(10, 10), (-20, -20, 100, 100), 96, 40)
		self.assertEqual(spot.source, (0, 0, 10, 10))


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
		"""A window has to stay in the same coordinates as the box it came from, or the
		reader zoom would jump."""
		self.assertEqual(
			imagePins.windowOf(flat(100, 100), (-20, 10, 140, 60), 0.5, 0.0, 0.5, 1.0),
			(50, 10, 70, 60),
		)


class TestOneWholePicture(unittest.TestCase):
	"""End to end on the shapes a reader actually meets, which is the only check that the
	pieces above agree with each other about coordinates."""

	def test_aBlobDrawsAsABlob(self):
		source = blob(96, 96)
		found = imagePins.render(source, (0, 0, 96, 96), 48, 20, imagePins.BRIGHTNESS)
		self.assertTrue(found.raised)
		self.assertLess(found.coverage, 0.6)

	def test_theSameBlobHasOutlinesToo(self):
		source = blob(96, 96)
		found = imagePins.render(source, (0, 0, 96, 96), 48, 20, imagePins.EDGES)
		self.assertTrue(found.raised)
		self.assertLess(found.coverage, 0.35)

	def test_aWindowOfItIsStillReadable(self):
		"""The property that makes zoom worth having: a quarter of the picture reduced from
		its own pixels comes back with a readable density rather than four pins where one
		was."""
		source = blob(96, 96)
		window = imagePins.windowOf(source, (0, 0, 96, 96), 0.2, 0.2, 0.6, 0.6)
		found = imagePins.render(source, window, 48, 20, imagePins.EDGES)
		self.assertTrue(found.raised)
		self.assertLess(found.coverage, 0.35)
