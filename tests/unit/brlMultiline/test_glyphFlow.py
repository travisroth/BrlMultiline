# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for putting the glyph vocabulary onto the line NVDA wrote.

The interesting property of this wiring is that it never decides what to say. NVDA already
writes "btn" before a button's name; this replaces those three cells with one shape and gives
the other two back to the line. So almost every test here is a statement about *not* changing
something: not the words when the setting is off, not the words when the display cannot draw,
not a routing press, not the cursor, and never a word that NVDA did not write as a word of its
own.

The one thing that does change is how much fits on a row, and that is the point of doing it
before the line is cut rather than after.
"""

import unittest

from ._stubs import Region, installStubs

installStubs()

from brlMultiline import bmConfig, glyphFlow, glyphs  # noqa: E402


class FakeDisplay:
	"""A display that draws glyphs, as much of one as this module touches."""

	name = "fake"

	def __init__(self, numRows=8, numCols=32, glyphSize=(3, 4), cellSize=(2, 4)):
		self.numRows = numRows
		self.numCols = numCols
		self.glyphSize = glyphSize
		self.cellSize = cellSize
		self.given = None

	def newGlyph(self, rows, fallback):
		return ("glyph", tuple(rows), tuple(fallback))

	def setCellGlyphs(self, found):
		self.given = dict(found)


class FakeBand:
	"""One member of a composite, with its band's first row."""

	def __init__(self, driver, rowStart, failed=False):
		self.driver = driver
		self.failed = failed
		self.band = type("Band", (), {"rowStart": rowStart})()


class FakeComposite:
	"""The add-on's own display, which is several displays."""

	name = "brlMultilineVirtual"

	def __init__(self, slots):
		self.slots = tuple(slots)


class GlyphTestCase(unittest.TestCase):
	"""A display that draws, and the setting turned on."""

	def setUp(self):
		import braille

		from ._stubs import FakeHandler

		glyphFlow.forget()
		self.addCleanup(glyphFlow.forget)
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = FakeHandler(8, 32)
		self.display = FakeDisplay()
		braille.handler.display = self.display
		self.section = bmConfig.getDisplayConfig()
		self.section["drawGlyphs"] = True

	def attach(self, display):
		"""Put a display in place and ask for shapes on it.

		Settings are stored per display, so swapping one in lands on a different section and
		the answer to whether shapes are wanted has to be given again.
		"""
		braille().handler.display = display
		self.section = bmConfig.getDisplayConfig()
		self.section["drawGlyphs"] = True
		glyphFlow.forget()

	def target(self):
		""":return: the display a region under test is going to.

		Passed rather than looked up inside, because two displays driven as one are two
		pieces of hardware and only the caller knows which one a region is bound for.
		"""
		return glyphFlow.glyphTarget()

	def given(self, table):
		"""Put a table of made up labels in place of the one read out of NVDA."""
		glyphFlow._TOKENS = dict(table)
		glyphFlow._ORDERED = None

	def region(self, text):
		""":return: a region holding one line, one cell per character."""
		built = Region()
		built.rawText = text
		built.update()
		return built


class TestWhichWordsAShapeStandsOver(GlyphTestCase):
	"""Read out of NVDA rather than written down here.

	The abbreviations are translated strings. Keying the table on them would have meant a reader
	running NVDA in another language quietly getting nothing at all — a failure that looks
	exactly like the setting being off, which is the kind nobody reports.
	"""

	def test_aRoleNVDAWritesHasAShape(self):
		self.assertIs(glyphFlow.tokens()["btn"], glyphs.BUTTON)

	def test_aStateNVDAWritesAsBrailleHasAShape(self):
		"""NVDA writes a checked box as three literal braille pattern characters, not as a
		word, and the vocabulary records the same three cells in this add-on's dot notation."""
		self.assertIs(glyphFlow.tokens()["⣏⣿⣹"], glyphs.CHECKED)

	def test_theAbsenceOfAStateHasItsOwnShape(self):
		self.assertIs(glyphFlow.tokens()["⣏⣀⣹"], glyphs.UNCHECKED)

	def test_aSwitchThatIsOnIsTheSamePictureAsABoxThatIsChecked(self):
		"""Because NVDA writes them the same three cells. Inventing a difference here would be
		this add-on saying something NVDA does not."""
		self.assertIs(glyphFlow.tokens()["⣏⣿⣹"], glyphs.CHECKED)

	def test_aRoleWithNoShapeIsLeftAlone(self):
		self.assertNotIn("chk", glyphFlow.tokens())
		self.assertNotIn("hdng", glyphFlow.tokens())

	def test_aShapeThatWouldSaveNothingDoesNotClaimTheWord(self):
		"""The wide button is three cells of drawing over three cells of "btn", and exists to be
		compared with the narrow one rather than to be used. Letting it claim the word would
		have made which of the two got drawn depend on dictionary order."""
		self.assertIsNot(glyphFlow.tokens().get("btn"), glyphs.WIDE_BUTTON)


class TestTheWordsNVDABuildsRatherThanLooksUp(GlyphTestCase):
	"""A visited link and a heading are not in `braille.labels`.

	`getPropertiesBraille` composes them where it meets the role — a link carrying
	`State.VISITED`, a heading with a level — so there is no dictionary to read them out of and
	the message id goes through NVDA's own catalogue instead.
	"""

	def test_aVisitedLinkHasItsOwnShape(self):
		self.assertIs(glyphFlow.tokens()["vlnk"], glyphs.VISITED_LINK)

	def test_itIsNotTheShapeForALinkNotYetFollowed(self):
		"""The mirror of it. The rising stroke is going somewhere and the falling one is coming
		back, which is about the largest difference two shapes this sparse can carry."""
		self.assertIsNot(glyphFlow.tokens()["vlnk"], glyphFlow.tokens()["lnk"])

	def test_theFirstThreeHeadingLevelsHaveShapes(self):
		table = glyphFlow.tokens()
		self.assertIs(table["h1"], glyphs.HEADING_1)
		self.assertIs(table["h2"], glyphs.HEADING_2)
		self.assertIs(table["h3"], glyphs.HEADING_3)

	def test_aDeeperHeadingKeepsItsWord(self):
		"""Level four is rare, and a shape nobody meets often enough to learn is worse than
		three cells that spell it."""
		self.assertNotIn("h4", glyphFlow.tokens())

	def test_aLevelIsNotFoundInsideALongerNumber(self):
		self.assertEqual(glyphFlow.marksIn("h10 Something"), [])
		found = glyphFlow.marksIn("h1 Favorites")
		self.assertEqual([(start, end) for start, end, _glyph in found], [(0, 2)])


class TestFindingThemInALine(GlyphTestCase):
	"""Whole words only.

	NVDA joins the things it says about an object with a single space, so a role is always a
	token of its own. Matching anywhere in the line would have put a button in the middle of
	somebody's word, which reads as a shape that means nothing and cannot be traced.
	"""

	def test_aRoleAtTheStartIsFound(self):
		found = glyphFlow.marksIn("btn Search")
		self.assertEqual([(start, end) for start, end, _glyph in found], [(0, 3)])

	def test_aRoleInTheMiddleIsFound(self):
		found = glyphFlow.marksIn("Search btn now")
		self.assertEqual([(start, end) for start, end, _glyph in found], [(7, 10)])

	def test_eitherSideOfTheNameIsFound(self):
		"""NVDA is not consistent about this: browse mode writes "btn Search" and an ordinary
		window writes it the other way about. Matching whole words wherever they fall is what
		makes that somebody else's problem rather than this module's.
		"""
		before = glyphFlow.marksIn("btn Search")
		after = glyphFlow.marksIn("Search btn")
		self.assertEqual(len(before), 1)
		self.assertEqual(len(after), 1)
		self.assertIs(before[0][2], after[0][2])

	def test_aWordThatMerelyContainsOneIsNot(self):
		self.assertEqual(glyphFlow.marksIn("obtnl"), [])
		self.assertEqual(glyphFlow.marksIn("btns"), [])

	def test_severalAreFoundLeftToRight(self):
		found = glyphFlow.marksIn("⣏⣿⣹ edt Name")
		self.assertEqual([start for start, _end, _glyph in found], [0, 4])

	def test_aLabelOfSeveralWordsIsMatchedWhole(self):
		"""The labels are NVDA's translated ones, and nothing says a language has to render
		a role in one word — NVDA's own "sorted asc" already does not. Splitting the line
		into tokens and looking each one up would have found no such label at all.
		"""
		self.given({"two words": glyphs.BUTTON})
		found = glyphFlow.marksIn("Search two words now")
		self.assertEqual([(start, end) for start, end, _glyph in found], [(7, 16)])

	def test_aLongerLabelWinsOverOneItBeginsWith(self):
		self.given({"a": glyphs.BUTTON, "a b": glyphs.CHECKED})
		found = glyphFlow.marksIn("a b")
		self.assertEqual(len(found), 1)
		self.assertIs(found[0][2], glyphs.CHECKED)

	def test_aLineWithNothingInItFindsNothing(self):
		self.assertEqual(glyphFlow.marksIn(""), [])
		self.assertEqual(glyphFlow.marksIn("Search now"), [])


class TestShorteningALine(GlyphTestCase):
	"""What the reader gets: two cells back, and everything else where it was."""

	def test_theWordBecomesOneCell(self):
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(len(region.brailleCells), len("btn Search") - 2)

	def test_theCellLeftBehindIsTheFirstOfTheWord(self):
		"""So that a shape that fails to be drawn leaves "b" where "btn" was, rather than
		something meaningless."""
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(region.brailleCells[0], ord("b"))

	def test_theTextAfterItIsUnchanged(self):
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(bytes(region.brailleCells[1:]).decode(), " Search")

	def test_aRoutingPressAnywhereOnTheShapeReachesTheObject(self):
		"""Which is what NVDA already does: a press on any cell of "btn Search" arrives at the
		button. The shape is one cell, so there is one place to press and it is that one."""
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(region.brailleToRawPos[0], 0)

	def test_aRoutingPressAfterItReachesTheSameLetterAsBefore(self):
		"""The fault this guards against is met long after the change that caused it and cannot
		possibly be attributed: a press in the second half of the line reaching the wrong word."""
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		at = region.brailleCells.index(ord("S"))
		self.assertEqual(region.rawText[region.brailleToRawPos[at]], "S")

	def test_theCursorMovesWithTheCells(self):
		region = self.region("btn Search")
		region.brailleCursorPos = region.rawToBraillePos[4]
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(region.brailleCursorPos, region.rawToBraillePos[4])
		self.assertEqual(region.brailleCells[region.brailleCursorPos], ord("S"))

	def test_severalShapesOnOneLineAllLandRight(self):
		region = self.region("⣏⣿⣹ edt Name")
		marks = glyphFlow.compressRegion(region, self.target())
		self.assertEqual(sorted(marks), [0, 2])
		self.assertEqual(bytes(region.brailleCells[2:]).decode(), "e Name")

	def test_theMarkNamesTheCellTheShapeStartsOn(self):
		region = self.region("btn Search")
		marks = glyphFlow.compressRegion(region, self.target())
		self.assertEqual(list(marks), [0])
		self.assertEqual(marks[0].cells, [ord("b")])
		self.assertEqual(marks[0].saved, 2)

	def test_theDriverIsAskedToBuildTheShape(self):
		region = self.region("btn Search")
		marks = glyphFlow.compressRegion(region, self.target())
		self.assertEqual(marks[0].drawn[0], "glyph")
		self.assertEqual(marks[0].drawn[1], tuple(glyphs.patternRows(glyphs.BUTTON.dots)))


class TestWhenNothingShouldHappen(GlyphTestCase):
	"""Every path out of here that cannot draw leaves the line exactly as NVDA wrote it."""

	def assertUntouched(self, region):
		self.assertEqual(bytes(region.brailleCells).decode(), region.rawText)
		self.assertEqual(glyphFlow.marksOf(region), {})

	def test_theSettingIsOff(self):
		self.section["drawGlyphs"] = False
		region = self.region("btn Search")
		self.assertEqual(glyphFlow.compressRegion(region, self.target()), {})
		self.assertUntouched(region)

	def test_theDisplayCannotDrawOne(self):
		import braille

		braille.handler.display = FakeDisplay(glyphSize=(2, 4), cellSize=(2, 4))
		region = self.region("btn Search")
		self.assertEqual(glyphFlow.compressRegion(region, self.target()), {})
		self.assertUntouched(region)

	def test_thereIsNoDisplayAtAll(self):
		import braille

		braille.handler.display = None
		region = self.region("btn Search")
		self.assertEqual(glyphFlow.compressRegion(region, self.target()), {})
		self.assertUntouched(region)

	def test_theLineHasNoRoleInIt(self):
		region = self.region("Search now")
		self.assertEqual(glyphFlow.compressRegion(region, self.target()), {})
		self.assertUntouched(region)

	def test_thereIsNoRegion(self):
		self.assertEqual(glyphFlow.compressRegion(None, self.target()), {})

	def test_marksLeftByAnEarlierPassAreTakenAwayAgain(self):
		"""A region compressed while the setting was on and read again after it was turned off
		would otherwise carry marks naming cells that are no longer symbols. The region is read
		again here on purpose; `TestGivingTheWordsBack` is the case where it is not.
		"""
		region = self.region("btn Search")
		self.assertTrue(glyphFlow.compressRegion(region, self.target()))
		self.section["drawGlyphs"] = False
		region.update()
		self.assertEqual(glyphFlow.compressRegion(region, self.target()), {})
		self.assertEqual(glyphFlow.marksOf(region), {})
		self.assertEqual(bytes(region.brailleCells).decode(), "btn Search")


class TestGivingTheWordsBack(GlyphTestCase):
	"""Turning it off has to undo it, on the region as it stands.

	Compression rewrites the cells and both maps in place, and a region is not always read again
	before it is drawn again: a flow keeps the rendering it has when the text has not changed,
	and a display rebuild or a profile switch hands the same region back. Clearing the marks
	without putting the cells back left "b Search" on the line with nothing registered to draw
	over it, which is a cell that means nothing at all.
	"""

	def compressed(self, text="btn Search"):
		""":return: a region compressed while the setting was on."""
		region = self.region(text)
		self.assertTrue(glyphFlow.compressRegion(region, self.target()))
		return region

	def test_theWordComesBackWhenTheSettingIsTurnedOff(self):
		region = self.compressed()
		self.section["drawGlyphs"] = False
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(bytes(region.brailleCells).decode(), "btn Search")

	def test_theWordComesBackWhenTheDisplayGoesAway(self):
		import braille

		region = self.compressed()
		braille.handler.display = None
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(bytes(region.brailleCells).decode(), "btn Search")

	def test_routingIsTrueAgainAfterwards(self):
		region = self.compressed()
		self.section["drawGlyphs"] = False
		glyphFlow.compressRegion(region, self.target())
		at = region.brailleCells.index(ord("S"))
		self.assertEqual(region.rawText[region.brailleToRawPos[at]], "S")

	def test_theCursorComesBackWithIt(self):
		region = self.region("btn Search")
		region.brailleCursorPos = 4
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(region.brailleCursorPos, 2)
		self.section["drawGlyphs"] = False
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(region.brailleCursorPos, 4)

	def test_noMarksAreLeftBehind(self):
		region = self.compressed()
		self.section["drawGlyphs"] = False
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(glyphFlow.marksOf(region), {})

	def test_aRegionReadAgainSinceIsLeftAlone(self):
		"""A re-read rebuilds the cells and the maps from the text, so the snapshot describes
		something that no longer exists and writing it back would undo the reading."""
		region = self.compressed()
		region.rawText = "cbo Colour"
		region.update()
		self.section["drawGlyphs"] = False
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(bytes(region.brailleCells).decode(), "cbo Colour")

	def test_aSegmentPutsThemBackToo(self):
		"""The path a reader actually takes: the setting is turned off and the display redrawn,
		with no region read in between."""
		from brlMultiline.container import DisplayContainer
		from brlMultiline.views import viewFromConfig

		container = DisplayContainer(braille().handler, viewFromConfig(8, 32))
		braille().handler.mainBuffer = braille().handler.buffer = container
		segment = container.segments[0]
		segment.append(self.region("btn Search"))
		container.update()
		self.assertEqual(bytes(segment.brailleCells).decode(), "b Search")
		self.section["drawGlyphs"] = False
		container.update()
		self.assertEqual(bytes(segment.brailleCells).decode(), "btn Search")


class TestBeingLaidOutMoreThanOnce(GlyphTestCase):
	"""A block is laid out once per width its indent leaves, and read far less often than that.

	The second pass would find nothing left to save — the line having already been compressed —
	and would have replaced a good set of marks with an empty one, which is a symbol that
	silently stops being drawn on an indented block.
	"""

	def test_theSecondPassKeepsTheMarks(self):
		region = self.region("btn Search")
		first = glyphFlow.compressRegion(region, self.target())
		second = glyphFlow.compressRegion(region, self.target())
		self.assertEqual(second, first)

	def test_theSecondPassDoesNotShortenItAgain(self):
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		cells = list(region.brailleCells)
		glyphFlow.compressRegion(region, self.target())
		self.assertEqual(region.brailleCells, cells)

	def test_readingItAgainWorksItOutAfresh(self):
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		region.rawText = "cbo Colour"
		region.update()
		marks = glyphFlow.compressRegion(region, self.target())
		self.assertEqual(list(marks), [0])
		self.assertEqual(bytes(region.brailleCells).decode(), "c Colour")


class TestFindingTheDisplayThatDraws(GlyphTestCase):
	"""Asked separately from the graphics surface.

	`graphics.findSurface` duck types the four methods a picture over the whole panel needs. A
	display that can draw a shape in a cell without holding a picture is a display that would
	have been skipped, and the two capabilities are not the same claim.
	"""

	def test_theDisplayItself(self):
		target = glyphFlow.glyphTarget()
		self.assertIsNotNone(target)
		self.assertIs(target.driver, self.display)
		self.assertEqual(target.rowStart, 0)

	def test_aDisplayWithGaplessCellsGainsNothing(self):
		self.assertIsNone(glyphFlow.glyphTarget(FakeDisplay(glyphSize=(2, 4), cellSize=(2, 4))))

	def test_aDisplayThatOffersNoGlyphsAtAll(self):
		self.assertIsNone(glyphFlow.glyphTarget(object()))

	def test_aMemberOfTheCompositeIsFound(self):
		drawing = FakeDisplay()
		composite = FakeComposite([FakeBand(object(), rowStart=0), FakeBand(drawing, rowStart=1)])
		target = glyphFlow.glyphTarget(composite)
		self.assertIs(target.driver, drawing)
		self.assertEqual(target.rowStart, 1)

	def test_aMemberTheCompositeHasGivenUpOnIsSkipped(self):
		"""Its rows have been taken out of the geometry, so a cell index against its band would
		name a cell that now belongs to someone else."""
		composite = FakeComposite([FakeBand(FakeDisplay(), rowStart=0, failed=True)])
		self.assertIsNone(glyphFlow.glyphTarget(composite))


class TestTwoDisplaysDrivenAsOne(GlyphTestCase):
	"""Two pieces of hardware, and only one of them may be able to draw.

	This is the arrangement the add-on exists for. Taking the first display that could draw and
	treating it as *the* display meant a region bound for a Focus was compressed because a
	Monarch was plugged in: the container then rightly refused to send the shape to a row
	outside the Monarch, and the reader was left with "b Search" — a word with its middle
	removed and nothing drawn over it.
	"""

	def stacked(self, plain=4, drawing=8):
		"""Put a display that cannot draw above one that can, and return the pair."""
		flat = FakeDisplay(numRows=plain, numCols=32, glyphSize=(2, 4), cellSize=(2, 4))
		pins = FakeDisplay(numRows=drawing, numCols=32)
		self.attach(FakeComposite([FakeBand(flat, rowStart=0), FakeBand(pins, rowStart=plain)]))
		return flat, pins

	def test_onlyTheOneThatCanDrawIsATarget(self):
		_flat, pins = self.stacked()
		self.assertEqual([target.driver for target in glyphFlow.glyphTargets()], [pins])

	def test_aRowOnTheDisplayThatCannotDrawHasNoTarget(self):
		self.stacked()
		self.assertIsNone(glyphFlow.targetForRows(0, 1))

	def test_aRowOnTheOneThatCanHasOne(self):
		_flat, pins = self.stacked()
		self.assertIs(glyphFlow.targetForRows(4, 4).driver, pins)

	def test_aRunStraddlingTheJoinHasNone(self):
		"""It is drawn partly on each, and a shape can only be registered against one of them.
		Compressing it would take cells out of the half that cannot draw."""
		self.stacked()
		self.assertIsNone(glyphFlow.targetForRows(3, 2))

	def test_aRegionBoundForTheDisplayThatCannotDrawKeepsItsWords(self):
		self.stacked()
		region = self.region("btn Search")
		self.assertEqual(glyphFlow.compressRegion(region, glyphFlow.targetForRows(0, 1)), {})
		self.assertEqual(bytes(region.brailleCells).decode(), "btn Search")

	def test_aRegionBoundForTheOneThatCanIsCompressed(self):
		self.stacked()
		region = self.region("btn Search")
		self.assertTrue(glyphFlow.compressRegion(region, glyphFlow.targetForRows(4, 1)))
		self.assertEqual(bytes(region.brailleCells).decode(), "b Search")

	def test_aSegmentAsksAboutItsOwnRows(self):
		from brlMultiline.layout import SegmentRect
		from brlMultiline.panels import SegmentSpec
		from brlMultiline.segments import BrailleBufferSegment

		self.stacked()
		handler = braille().handler
		held = object()
		above = BrailleBufferSegment(
			handler,
			held,
			SegmentSpec(rect=SegmentRect(row=0, col=0, numRows=2, numCols=32), key="above"),
		)
		below = BrailleBufferSegment(
			handler,
			held,
			SegmentSpec(rect=SegmentRect(row=6, col=0, numRows=2, numCols=32), key="below"),
		)
		self.assertIsNone(above.glyphTarget())
		self.assertIsNotNone(below.glyphTarget())

	def test_everyMemberThatDrawsIsToldEachFrame(self):
		"""Each keeps its own registrations, and a member left unaddressed would go on drawing
		whatever it was last given."""
		first = FakeDisplay(numRows=4, numCols=32)
		second = FakeDisplay(numRows=4, numCols=32)
		self.attach(FakeComposite([FakeBand(first, rowStart=0), FakeBand(second, rowStart=4)]))
		from brlMultiline.container import DisplayContainer
		from brlMultiline.views import viewFromConfig

		container = DisplayContainer(braille().handler, viewFromConfig(8, 32))
		braille().handler.mainBuffer = braille().handler.buffer = container
		container.windowBrailleCells
		self.assertEqual(first.given, {})
		self.assertEqual(second.given, {})


class TestWhereOnTheDisplayACellIs(unittest.TestCase):
	"""A member of a composite knows nothing about the rows above it."""

	def target(self, rowStart=0, numRows=8, numCols=32):
		return glyphFlow.Target(driver=None, rowStart=rowStart, numRows=numRows, numCols=numCols)

	def test_theFirstCellOfTheDisplay(self):
		self.assertEqual(self.target().indexFor(0, 0), 0)

	def test_aCellFurtherDown(self):
		self.assertEqual(self.target().indexFor(2, 5), 69)

	def test_aMemberCountsFromItsOwnFirstRow(self):
		self.assertEqual(self.target(rowStart=4).indexFor(4, 0), 0)
		self.assertEqual(self.target(rowStart=4).indexFor(5, 1), 33)

	def test_aRowAboveTheMemberIsNotOnIt(self):
		self.assertIsNone(self.target(rowStart=4).indexFor(3, 0))

	def test_aRowBelowTheMemberIsNotOnIt(self):
		self.assertIsNone(self.target(rowStart=4, numRows=2).indexFor(6, 0))

	def test_aColumnPastTheEndIsNotOnIt(self):
		self.assertIsNone(self.target().indexFor(0, 32))


class TestABandThatDrawsThem(GlyphTestCase):
	"""The end of the wiring: a row that holds more because a word became a shape.

	This is the whole claim. A symbol that merely looked nicer in the same three cells would be
	a decoration; what makes it worth learning is that the cells it gives back are cells the
	wrapping can use, and the only way to show that is to cut a line into rows and count.
	"""

	def band(self, line, numCols=8):
		""":return: a controller over one line of a document, at a narrow band width.

		The band is what tells its renderer which display it is on; there is no band here, so
		the test says it instead.
		"""
		from .test_flowControl import controllerOver

		control = controllerOver([line], numCols=numCols, numRows=2, enter=False)
		control.renderer.glyphTarget = self.target()
		control.enterAtCursor()
		return control

	def rowOf(self, control, numCols=8):
		""":return: the band's first row as text."""
		cells = control.cells()
		return bytes(cells[:numCols]).decode()

	def test_theRowHoldsMoreOfTheLine(self):
		self.assertEqual(self.rowOf(self.band("btn Search now")), "b Search")

	def test_withTheSettingOffTheWordsAreBack(self):
		self.section["drawGlyphs"] = False
		self.assertEqual(self.rowOf(self.band("btn Search now")), "btn Sear")

	def test_theBandSaysWhereTheShapeIs(self):
		control = self.band("btn Search now")
		control.cells()
		found = control.cellGlyphs()
		self.assertEqual(list(found), [0])
		self.assertEqual(found[0].cells, [ord("b")])

	def test_aBandWithNoShapesOnItSaysSo(self):
		control = self.band("Search now")
		control.cells()
		self.assertEqual(control.cellGlyphs(), {})


class TestGivingThemToTheDisplay(GlyphTestCase):
	"""The last step: a place on a segment becomes a place on the display's own cells.

	Done as the frame is assembled, because a glyph has to be registered before the frame it
	belongs to arrives — the driver matches each one against the cells that come in and retires
	the ones that do not appear. That is what stops a symbol outliving the object it belonged
	to, and it is also why the same call with nothing in it is how the last frame's symbols are
	taken away.
	"""

	def setUp(self):
		super().setUp()
		from brlMultiline.container import DisplayContainer
		from brlMultiline.views import viewFromConfig

		self.container = DisplayContainer(braille().handler, viewFromConfig(8, 32))
		braille().handler.mainBuffer = braille().handler.buffer = self.container

	def markOn(self, segment, position):
		"""Put one made up symbol on a segment, at a position within it."""
		fitted = glyphs.fittedOver(self.display, glyphs.BUTTON, [1, 2, 3])
		segment.cellGlyphs = lambda: {position: fitted}
		return fitted

	def test_aSymbolLandsOnTheCellItIsDrawnOver(self):
		segment = self.container.segments[0]
		fitted = self.markOn(segment, 0)
		self.container.windowBrailleCells
		expected = segment.rect.row * 32 + segment.rect.col
		self.assertEqual(self.display.given, {expected: fitted.drawn})

	def test_aSymbolFurtherAlongASegmentIsPlacedFromTheSegmentsOwnCorner(self):
		segment = self.container.segments[-1]
		fitted = self.markOn(segment, segment.rect.numCols + 2)
		self.container.windowBrailleCells
		expected = (segment.rect.row + 1) * 32 + segment.rect.col + 2
		self.assertEqual(self.display.given, {expected: fitted.drawn})

	def test_aFrameWithNoSymbolsTakesTheLastOnesAway(self):
		self.container.windowBrailleCells
		self.assertEqual(self.display.given, {})

	def test_aBufferNVDAIsNotShowingRegistersNothing(self):
		"""A message being measured, a segment being laid out. A symbol registered from one of
		those would name cells on a frame nobody is going to send."""
		braille().handler.buffer = None
		self.markOn(self.container.segments[0], 0)
		self.container.windowBrailleCells
		self.assertIsNone(self.display.given)


class TestARegionReadAgainUnderTheRendering(GlyphTestCase):
	"""NVDA re-reads the region a flow is drawing every time the caret moves.

	When the text has not changed the flow keeps the rendering it already has — the right thing
	and the cheap thing — but the re-read has rebuilt both position maps from the uncompressed
	text, and the rendering was cut from compressed cells. A routing press in the second half of
	the row was then answered through a map two cells out of step with what the finger was on,
	which is the kind of fault a reader meets long after its cause and cannot attribute.
	"""

	def test_theShapesGoBackOn(self):
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		region.update()
		self.assertEqual(bytes(region.brailleCells).decode(), "btn Search")
		glyphFlow.keepCompressed(region, self.target())
		self.assertEqual(bytes(region.brailleCells).decode(), "b Search")

	def test_aRoutingPressStillReachesTheSameLetter(self):
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		region.update()
		glyphFlow.keepCompressed(region, self.target())
		at = region.brailleCells.index(ord("S"))
		self.assertEqual(region.rawText[region.brailleToRawPos[at]], "S")

	def test_aRegionStillDescribedByItsMarksIsLeftAlone(self):
		region = self.region("btn Search")
		glyphFlow.compressRegion(region, self.target())
		cells = list(region.brailleCells)
		glyphFlow.keepCompressed(region, self.target())
		self.assertEqual(region.brailleCells, cells)

	def test_aRegionThatNeverCarriedAShapeIsNotTouched(self):
		region = self.region("Search now")
		glyphFlow.keepCompressed(region, self.target())
		self.assertEqual(bytes(region.brailleCells).decode(), "Search now")


class TestAnOrdinarySegment(GlyphTestCase):
	"""Not a flow feature. A feature of a display that can draw one.

	A menu bar in File Explorer is full of buttons and never goes near browse mode; the three
	cells NVDA spends saying "btn" cost the same there and the shape saves the same. So the
	substitution is made in the one place every ordinary segment passes through — reading its
	regions — rather than anywhere that knows what kind of content it is looking at.
	"""

	def setUp(self):
		super().setUp()
		from brlMultiline.container import DisplayContainer
		from brlMultiline.views import viewFromConfig

		self.container = DisplayContainer(braille().handler, viewFromConfig(8, 32))
		braille().handler.mainBuffer = braille().handler.buffer = self.container
		self.segment = self.container.segments[0]

	def showing(self, text):
		""":return: the segment's cells after it has read one region."""
		self.segment.append(self.region(text))
		self.container.update()
		return self.segment

	def test_theWordIsCompressedWithoutAFlowAnywhere(self):
		segment = self.showing("btn Search")
		self.assertEqual(bytes(segment.brailleCells).decode(), "b Search")

	def test_theSegmentSaysWhereTheShapeIs(self):
		segment = self.showing("btn Search")
		self.assertEqual(list(segment.cellGlyphs()), [0])

	def test_aShapeFurtherAlongTheLineIsPlacedRight(self):
		segment = self.showing("Search btn")
		found = segment.cellGlyphs()
		self.assertEqual(list(found), [7])
		self.assertEqual(segment.brailleCells[7], ord("b"))

	def test_theDisplayIsGivenIt(self):
		self.showing("btn Search")
		self.container.windowBrailleCells
		rect = self.segment.rect
		self.assertEqual(list(self.display.given), [rect.row * 32 + rect.col])

	def test_withTheSettingOffTheWordsStay(self):
		self.section["drawGlyphs"] = False
		segment = self.showing("btn Search")
		self.assertEqual(bytes(segment.brailleCells).decode(), "btn Search")
		self.assertEqual(segment.cellGlyphs(), {})

	def test_aBufferBuiltOnlyToLayTextOutDecidesNothingForItself(self):
		"""The renderer builds one of these to cut a block into rows in, and a cell of a table
		row is a place a shape must not go: its positions are packed with the column they came
		from, so a mark naming one would never be found again."""
		from brlMultiline.layout import SegmentRect
		from brlMultiline.panels import SegmentSpec
		from brlMultiline.segments import BrailleBufferSegment

		spec = SegmentSpec(rect=SegmentRect(row=0, col=0, numRows=1, numCols=32), key="layout")
		buffer = BrailleBufferSegment(braille().handler, None, spec)
		buffer.append(self.region("btn Search"))
		buffer.update()
		self.assertEqual(bytes(buffer.brailleCells).decode(), "btn Search")


def braille():
	""":return: NVDA's braille module, imported where it is used so the stubs are in place."""
	import braille as module

	return module
