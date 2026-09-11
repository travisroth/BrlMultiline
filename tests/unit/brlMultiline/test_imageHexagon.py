# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""The hexagon, which is where every fault in the image pipeline was actually found.

A reader loaded a hexagon into a browser, pointed NVDA at it, and reported what the panel felt
like: two vertical lines with ticks at the sides, and in the middle a hexagon whose outline was
four dots wide all round. Every part of that report was true, and none of it was in the picture.

So this file is that picture, and the assertions are the report turned round. It is a real
fixture rather than a generated shape on purpose: the faults came from the interaction between
a real background texture, a real letterbox and a real coverage rule, and a clean synthetic
hexagon has no faint diagonal hatch running out to its own boundary to catch them with.

What it pins down, in the order the reader met them:

1. The two vertical lines were the seam between the picture and the margin the add-on invented,
   promoted by a coverage quota that had run out of real edges. Nothing is detected in a margin
   now, so the seam does not exist to be found.
2. The four dot outline was the same quota spending itself on the only shape in the picture.
   The cut comes from the gradients now and the coverage only ever removes.
3. The stipple that appeared when zooming was the same quota again, in a window with nothing in
   it at all. A window is judged against the picture it came from now, and an empty one is
   refused in words.
"""

import os
import unittest

from ._png import greysOf
from ._stubs import installStubs

installStubs()

from brlMultiline import imagePins  # noqa: E402

PANEL = (96, 40)
"""The Monarch, when the whole of it is given to a picture."""

HEXAGON = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "hexagon.png")


def hexagon():
	""":return: the fixture, as a `Picture`."""
	greys, width, height = greysOf(HEXAGON)
	return imagePins.Picture(greys, width, height, "hexagon")


class HexagonTestCase(unittest.TestCase):
	"""Shared machinery: draw the fixture and say where its pins landed on the panel."""

	def setUp(self):
		self.picture = hexagon()
		self.whole = (0, 0, self.picture.width, self.picture.height)
		self.spot = imagePins.place(self.picture, self.whole, *PANEL)

	def drawn(self, mode=imagePins.BRIGHTNESS, invert=False):
		""":return: the raised pins in panel coordinates."""
		found = imagePins.renderAt(self.picture, self.spot, mode, invert)
		return {
			(found.left + at % found.width, found.top + at // found.width)
			for at, pin in enumerate(found.pins)
			if pin
		}

	def window(self, fraction):
		""":return: a centred window covering this fraction of the picture each way."""
		edge = (1 - fraction) / 2
		return imagePins.windowOf(self.picture, self.whole, edge, edge, fraction, fraction)


class TestTheFixtureItself(HexagonTestCase):
	"""What is in the file, asserted so that a different file cannot quietly pass."""

	def test_itIsSquare(self):
		self.assertEqual(self.picture.width, self.picture.height)

	def test_theBackgroundIsTexturedRatherThanFlat(self):
		"""The fault needs this. A picture on a perfectly uniform field meets its margin with
		no gradient at all, and the seam that produced the two vertical lines cannot happen."""
		edge = [self.picture.greys[y * self.picture.width] for y in range(self.picture.height)]
		self.assertGreater(max(edge) - min(edge), 5)
		self.assertLess(max(edge) - min(edge), 40)

	def test_theTextureIsFaintBesideTheInk(self):
		"""Two orders of magnitude apart, which is what makes the failure so plainly a failure
		of the rule rather than of the picture: nothing here is hard to tell apart."""
		self.assertGreater(self.picture.strength.gradient, 500)


class TestWhereThePictureLands(HexagonTestCase):
	"""A square picture on a panel two and a half times as wide as it is tall."""

	def test_theContentIsSquare(self):
		self.assertEqual((self.spot.width, self.spot.height), (40, 40))

	def test_itIsCentredAcrossThePanel(self):
		self.assertEqual((self.spot.left, self.spot.top), (28, 0))

	def test_theSourceIsTheWholePictureAndNoMore(self):
		self.assertEqual(self.spot.source, self.whole)

	def test_theShapeIsAsWideAsItIsTall(self):
		raised = self.drawn()
		columns = [x for x, _y in raised]
		rows = [y for _x, y in raised]
		across = max(columns) - min(columns) + 1
		down = max(rows) - min(rows) + 1
		self.assertAlmostEqual(across / down, 1.0, delta=0.15)


class TestTheTwoVerticalLines(HexagonTestCase):
	"""The reader first sentence. There were no vertical lines in the picture."""

	def tallColumns(self, mode):
		""":return: panel columns raised down nearly the whole height."""
		raised = self.drawn(mode)
		columns = [x for x, _y in raised]
		return sorted({x for x in columns if columns.count(x) >= 30})

	def test_outlinesDrawNoFullHeightColumns(self):
		self.assertEqual(self.tallColumns(imagePins.EDGES), [])

	def test_brightnessDrawsNoneEither(self):
		self.assertEqual(self.tallColumns(imagePins.BRIGHTNESS), [])

	def test_nothingIsDrawnInTheMarginAtAll(self):
		"""The stronger statement, and the one that makes the two above true by construction
		rather than by a threshold that could drift."""
		inside = {
			(x, y)
			for x in range(self.spot.left, self.spot.left + self.spot.width)
			for y in range(self.spot.top, self.spot.top + self.spot.height)
		}
		for mode in (imagePins.EDGES, imagePins.BRIGHTNESS):
			with self.subTest(mode=mode):
				self.assertFalse(self.drawn(mode) - inside)


class TestTheFourDotOutline(HexagonTestCase):
	"""The reader second sentence. The stroke is about one pin wide at this reduction."""

	def strokeWidths(self, mode, row):
		""":return: the runs of raised pins along one row of the panel."""
		raised = self.drawn(mode)
		runs = []
		run = 0
		for x in range(PANEL[0]):
			if (x, row) in raised:
				run += 1
			elif run:
				runs.append(run)
				run = 0
		if run:
			runs.append(run)
		return runs

	def test_brightnessCrossesTheOutlineTwiceAcrossTheMiddle(self):
		"""Twice, because the middle of a hexagon is empty. Ten raised pins across that row is
		what the quota used to give, and a reader feeling that cannot find the sides."""
		self.assertEqual(len(self.strokeWidths(imagePins.BRIGHTNESS, 20)), 2)

	def test_andEachCrossingIsThin(self):
		self.assertTrue(all(run <= 3 for run in self.strokeWidths(imagePins.BRIGHTNESS, 20)))

	def test_outlinesCrossItTwiceToo(self):
		self.assertEqual(len(self.strokeWidths(imagePins.EDGES, 20)), 2)

	def test_outlinesStayUnderTheirCeiling(self):
		"""Of the content rather than of the panel. Sixteen per cent of forty by forty is 256
		pins, not the 614 that a sixth of the whole panel would have allowed."""
		found = imagePins.renderAt(self.picture, self.spot, imagePins.EDGES)
		self.assertLessEqual(found.raised, round(self.spot.area * imagePins.EDGE_COVERAGE))

	def test_brightnessStaysUnderItsOwn(self):
		found = imagePins.renderAt(self.picture, self.spot, imagePins.BRIGHTNESS)
		self.assertLessEqual(found.raised, round(self.spot.area * imagePins.BRIGHTNESS_MOST))


class TestTheSidesSurvive(HexagonTestCase):
	"""Thinning a contour is only an improvement if the contour is still there afterwards.

	A break is the one artefact a hand cannot work around: a reader following a line to a
	corner and finding it stop has been told something false about the shape and has nowhere to
	pick it up again. So the outline has to come back as a single closed run of pins.
	"""

	def islands(self, raised):
		""":return: the connected groups among the raised pins, touching counted diagonally."""
		left = set(raised)
		groups = []
		while left:
			frontier = [left.pop()]
			group = set(frontier)
			while frontier:
				x, y = frontier.pop()
				for stepY in (-1, 0, 1):
					for stepX in (-1, 0, 1):
						near = (x + stepX, y + stepY)
						if near in left:
							left.remove(near)
							group.add(near)
							frontier.append(near)
			groups.append(group)
		return groups

	def test_brightnessDrawsOneUnbrokenOutline(self):
		groups = self.islands(self.drawn(imagePins.BRIGHTNESS))
		self.assertEqual(len(groups), 1, f"the outline came back in {len(groups)} pieces")

	def test_itIsAnOutlineRatherThanASolid(self):
		"""A hexagon that filled in would also be one connected group, and would be a picture
		of something else."""
		raised = self.drawn(imagePins.BRIGHTNESS)
		self.assertLess(len(raised), self.spot.area * 0.25)

	def test_theFlatTopIsDrawnFlat(self):
		"""This hexagon has a horizontal top edge, so some row near the top has to come back as
		one long run. A shape whose corners had rounded off would not."""
		raised = self.drawn(imagePins.BRIGHTNESS)
		longest = 0
		for row in range(6):
			run = 0
			for x in range(PANEL[0]):
				run = run + 1 if (x, row) in raised else 0
				longest = max(longest, run)
		self.assertGreater(longest, 15)


class TestZoomingIntoNothing(HexagonTestCase):
	"""The reader third report: stripes appearing in the middle of the hexagon on zoom.

	The middle of this hexagon is empty. What the reader was feeling was the background hatch,
	resolved by the reduction as the window narrowed and then promoted to fill a quota. The
	honest answer is that there is nothing in this part of the picture, and now that is what is
	said.
	"""

	def test_theMiddleOfTheHexagonHoldsNothingToDraw(self):
		for fraction in (0.5, 0.25):
			for mode in (imagePins.EDGES, imagePins.BRIGHTNESS):
				with self.subTest(fraction=fraction, mode=mode):
					with self.assertRaises(imagePins.ImageRefused):
						imagePins.render(self.picture, self.window(fraction), *PANEL, mode)

	def test_theRefusalSaysWhatIsWrong(self):
		"""A reader who has just zoomed needs to know the picture has not gone."""
		with self.assertRaises(imagePins.ImageRefused) as refused:
			imagePins.render(self.picture, self.window(0.25), *PANEL, imagePins.EDGES)
		self.assertIn("background", str(refused.exception))

	def test_theWholePictureIsStillDrawnHappily(self):
		"""The refusal has to be about the window and not about the picture, or the test above
		would pass on a pipeline that had simply stopped working."""
		self.assertTrue(self.drawn(imagePins.EDGES))

	def test_aWindowWithAnEdgeInItIsStillDrawn(self):
		"""Off to one side, where a side of the hexagon runs. Refusing this would be the other
		failure: a reader who zoomed towards something real and was told there was nothing."""
		box = imagePins.windowOf(self.picture, self.whole, 0.0, 0.25, 0.5, 0.5)
		self.assertTrue(imagePins.render(self.picture, box, *PANEL, imagePins.EDGES).raised)
