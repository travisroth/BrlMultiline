# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the notation, the vocabulary, and the fitting between them.

The notation is arithmetic and is tested as arithmetic: dot 1 is the top left pin and dot 12 is
the bottom right one, and a shape written for three cells is nine pins wide. Getting that wrong
produces a symbol that is a plausible shape in the wrong place, which is exactly the failure a
reader cannot diagnose from the panel.

The fitting is where the interesting rule is. A glyph stands over text NVDA already writes, and
how many cells that text takes depends on the reader's braille table — so a shape drawn for
three cells over a fallback that came out two cells long is refused rather than clipped. Half a
symbol, in the place a reader has learned to expect a whole one, is worse than the plain word.
"""

import unittest

from ._stubs import installStubs

installStubs()

from brlMultiline import glyphs  # noqa: E402


def spell(text):
	""":return: one cell per character, dot 1 raised.

	Stands in for a braille table. What matters here is how many cells a string becomes, which
	is what the fitting rule turns on.
	"""
	return [0b00000001 for _ in text]


class FakeDriver:
	"""A display that can draw glyphs, as much of one as this module touches."""

	def __init__(self, glyphSize=(3, 4), cellSize=(2, 4)):
		self.glyphSize = glyphSize
		self.cellSize = cellSize
		self.built = []

	def newGlyph(self, rows, fallback):
		self.built.append((rows, fallback))
		return (rows, fallback)


class TestTheNotation(unittest.TestCase):
	"""Dots 1 to 8 are braille's own; 9 to 12 continue the same rule into the column braille
	leaves blank. There is no standard for a three column cell to follow, so starting from the
	numbering every braille reader already has is the only sensible place to start."""

	def test_dotOneIsTheTopLeftPin(self):
		self.assertEqual(glyphs.patternRows("1"), ["O..", "...", "...", "..."])

	def test_theFirstColumnRunsOneTwoThreeSeven(self):
		self.assertEqual(glyphs.patternRows("1,2,3,7"), ["O..", "O..", "O..", "O.."])

	def test_theSecondColumnRunsFourFiveSixEight(self):
		self.assertEqual(glyphs.patternRows("4,5,6,8"), [".O.", ".O.", ".O.", ".O."])

	def test_theThirdColumnIsTheNewOne(self):
		"""The column braille leaves blank so a reader can tell one cell from the next. Nothing
		in the hardware requires it, and in a drawing nothing enforces it."""
		self.assertEqual(glyphs.patternRows("9,10,11,12"), ["..O", "..O", "..O", "..O"])

	def test_aFullCellIsTwelveDots(self):
		rows = glyphs.patternRows("1,2,3,4,5,6,7,8,9,10,11,12")
		self.assertEqual(rows, ["OOO"] * 4)

	def test_severalCellsAreSeparatedByABar(self):
		"""Written per cell rather than numbering a nine by four shape from 1 to 36, because a
		reader writing one of these is thinking one cell at a time."""
		self.assertEqual(glyphs.patternRows("1|1|1"), ["O..O..O..", "." * 9, "." * 9, "." * 9])

	def test_anEmptyGroupIsABlankCellOfTheRun(self):
		"""Which is how a shape says it wants the width without using it — a five pin box in a
		run of three cells leaves the last one clear."""
		self.assertEqual(glyphs.patternRows("1||1"), ["O.....O..", "." * 9, "." * 9, "." * 9])

	def test_aDotThatIsNotInACellIsRefused(self):
		with self.assertRaises(ValueError):
			glyphs.patternRows("13")

	def test_spacesAreToleratedBecausePeopleTypeThem(self):
		self.assertEqual(glyphs.patternRows("1, 2"), glyphs.patternRows("1,2"))


class TestAFallbackWrittenAsDots(unittest.TestCase):
	def test_dotsOneToSixAreASolidTwoByThree(self):
		self.assertEqual(glyphs.cellValue("1,2,3,4,5,6"), 0x3F)

	def test_dotsSevenAndEightAreTheFlowsPendingMark(self):
		"""The mark the flow already writes for a row it has not fetched, so adopting a glyph
		for it changes nothing on a display that cannot draw one."""
		self.assertEqual(glyphs.cellValue("7,8"), 0xC0)

	def test_nothingIsABlankCell(self):
		self.assertEqual(glyphs.cellValue(""), 0)

	def test_aBrailleCellHasNoNinthDot(self):
		"""A fallback is an ordinary braille cell, not a glyph slot, so the third column has
		nowhere to be."""
		with self.assertRaises(ValueError):
			glyphs.cellValue("9")


class TestTheVocabulary(unittest.TestCase):
	"""The naming has to happen somewhere: the driver is handed patterns and never learns a
	symbol's name, so that an Excel formula marker and a Word checkmark never touch it."""

	def test_everyEntryStandsOverSomething(self):
		for name, glyph in glyphs.VOCABULARY.items():
			with self.subTest(name):
				self.assertTrue(bool(glyph.says) != bool(glyph.fallbackDots), name)

	def test_everyShapeIsDrawable(self):
		for name, glyph in glyphs.VOCABULARY.items():
			with self.subTest(name):
				rows = glyphs.patternRows(glyph.dots)
				self.assertEqual(len(rows), glyphs.CELL_HEIGHT)
				self.assertEqual(len(rows[0]), glyphs.CELL_WIDTH * glyph.cells)

	def test_aShapeOverDotsCoversAsManyCellsAsItStandsOver(self):
		"""Checkable here, unlike a shape over a wording, which depends on the reader's table
		and is checked when it is fitted."""
		for name, glyph in glyphs.VOCABULARY.items():
			if not glyph.fallbackDots:
				continue
			with self.subTest(name):
				self.assertEqual(glyph.cells, len(glyph.fallbackDots.split(glyphs.GROUP)))

	def test_theFocusIndicatorIsMonarchsOwnSquare(self):
		"""Easy to find precisely because it is square, and square needs three columns — which
		is why the add-on's present approximation of dots 3678 twice does not read the same."""
		self.assertEqual(glyphs.patternRows(glyphs.FOCUS.dots), ["OOO", "OOO", "OOO", "..."])

	def test_theFocusFallbackIsTheShapeWithItsThirdColumnTakenAway(self):
		"""Which is what makes it recognisable on a display that cannot draw: a solid two by
		three is the same idea, smaller."""
		self.assertEqual(glyphs.cellValue(glyphs.FOCUS.fallbackDots), 0x3F)

	def test_aCheckboxIsToldFromAnEmptyOneByBeingSolid(self):
		"""Solid against hollow is the difference between a surface and an edge, which is the
		one distinction touch makes instantly. A tick drawn inside a five by four box would be
		three dots a finger cannot separate from the box around them."""
		checked = glyphs.patternRows(glyphs.CHECKED.dots)
		unchecked = glyphs.patternRows(glyphs.UNCHECKED.dots)
		self.assertEqual(checked[1].count("O"), 5)
		self.assertEqual(unchecked[1].count("O"), 2)
		self.assertEqual(checked[0], unchecked[0])


class TestFittingOneToADisplay(unittest.TestCase):
	def setUp(self):
		self.driver = FakeDriver()

	def test_aWordingBecomesTheCellsTheReadersTableWrites(self):
		"""Not cells decided when the vocabulary was written. The driver matches on the exact
		bytes the add-on wrote, so they have to come from the table in force."""
		glyphs.fittedGlyph(self.driver, glyphs.BUTTON, spell)
		_rows, fallback = self.driver.built[0]
		self.assertEqual(fallback, spell("btn"))

	def test_aShapeIsBuiltAtTheWidthOfWhatItStandsOver(self):
		glyphs.fittedGlyph(self.driver, glyphs.BUTTON, spell)
		rows, _fallback = self.driver.built[0]
		self.assertEqual(len(rows[0]), 9)

	def test_aFallbackWrittenAsDotsNeedsNoTable(self):
		glyphs.fittedGlyph(self.driver, glyphs.FOCUS, spell)
		_rows, fallback = self.driver.built[0]
		self.assertEqual(fallback, [0x3F])

	def test_aShapeThatDoesNotFitItsWordingIsRefused(self):
		"""A shape drawn for three cells over a fallback that came out two cells long would be
		clipped: half a symbol, where the reader has learned to expect a whole one. The plain
		word is a better answer, and it is what a display without glyphs was going to show."""

		def contracted(text):
			return [0b00000001, 0b00000010]

		self.assertIsNone(glyphs.fittedGlyph(self.driver, glyphs.BUTTON, contracted))
		self.assertEqual(self.driver.built, [])

	def test_anEntryThatGivesBothIsRefused(self):
		both = glyphs.Glyph(dots="1", says="btn", fallbackDots="1")
		self.assertIsNone(glyphs.fittedGlyph(self.driver, both, spell))

	def test_anEntryThatGivesNeitherIsRefused(self):
		self.assertIsNone(glyphs.fittedGlyph(self.driver, glyphs.Glyph(dots="1"), spell))

	def test_aTableThatWillNotTranslateCostsTheGlyphAndNotTheLine(self):
		def broken(text):
			raise RuntimeError("no tables")

		self.assertIsNone(glyphs.fittedGlyph(self.driver, glyphs.BUTTON, broken))

	def test_aDriverThatWillNotBuildOneIsNotAllowedToTakeTheLineDown(self):
		class Refuses(FakeDriver):
			def newGlyph(self, rows, fallback):
				raise RuntimeError("no")

		self.assertIsNone(glyphs.fittedGlyph(Refuses(), glyphs.FOCUS, spell))


class TestWhetherADisplayGainsAnything(unittest.TestCase):
	def test_aDisplayWithAGapColumnToReclaimDoes(self):
		self.assertTrue(glyphs.supported(FakeDriver(glyphSize=(3, 4), cellSize=(2, 4))))

	def test_aDisplayWhoseCellsAreAlreadyGaplessDoesNot(self):
		"""A glyph on it would be a braille cell drawn the long way round. Asked rather than
		assumed, so the answer for hardware nobody here has is the honest one."""
		self.assertFalse(glyphs.supported(FakeDriver(glyphSize=(2, 4), cellSize=(2, 4))))

	def test_aDisplayThatCannotSayDoesNot(self):
		self.assertFalse(glyphs.supported(object()))
