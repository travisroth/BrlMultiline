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
				self.assertEqual(len(rows[0]), glyphs.CELL_WIDTH * glyph.width)

	def test_noShapeOverDotsIsWiderThanWhatItStandsOver(self):
		"""A shape needs the cells it is drawn in, and taking one it was not given would
		paint over whatever came next. Checkable here, unlike a shape over a wording, whose
		width depends on the reader's table and is checked when it is fitted.
		"""
		for name, glyph in glyphs.VOCABULARY.items():
			if not glyph.fallbackDots:
				continue
			with self.subTest(name):
				over = len(glyph.fallbackDots.split(glyphs.GROUP))
				self.assertLessEqual(glyph.width, over)

	def test_theStatePatternsAreNvdasOwnDotsAndNotAGuessAtThem(self):
		"""NVDA writes a checkbox state as three literal braille patterns rather than as an
		abbreviation — a box drawn in dots 1 to 8, which is the same idea a glyph is, done
		with what a braille line has. An earlier version of this file guessed at "(x)" and
		"( )", which would simply never have matched and would have failed in silence.
		"""
		checked = [glyphs.cellValue(group) for group in glyphs.CHECKED.fallbackDots.split(glyphs.GROUP)]
		self.assertEqual(checked, [ord(character) - 0x2800 for character in "⣏⣿⣹"])
		unchecked = [
			glyphs.cellValue(group) for group in glyphs.UNCHECKED.fallbackDots.split(glyphs.GROUP)
		]
		self.assertEqual(unchecked, [ord(character) - 0x2800 for character in "⣏⣀⣹"])

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
		self.assertEqual(checked[1], "OOO")
		self.assertEqual(unchecked[1], "O.O")
		self.assertEqual(checked[0], unchecked[0])


class TestFittingOneToADisplay(unittest.TestCase):
	def setUp(self):
		self.driver = FakeDriver()

	def test_aShapeNarrowerThanItsTextGivesTheRestOfTheLineBack(self):
		"""The whole reason to have pins. A Monarch line is 32 cells and a DotPad's is 20, so
		three of them spent saying "btn" is a tenth of the line gone before the button has a
		name."""
		fitted = glyphs.fittedGlyph(self.driver, glyphs.BUTTON, spell)
		self.assertEqual(fitted.replaces, 3)
		self.assertEqual(len(fitted.cells), 1)
		self.assertEqual(fitted.saved, 2)

	def test_theCellItLeavesBehindIsTheFirstOfTheTextItReplaced(self):
		"""So that "btn" becomes "b" if the drawing ever fails to happen, which is a great deal
		better than something arbitrary in the same place."""
		fitted = glyphs.fittedGlyph(self.driver, glyphs.BUTTON, spell)
		self.assertEqual(fitted.cells, spell("b"))

	def test_anEntryMayChooseADifferentCellToLeaveBehind(self):
		chooses = glyphs.Glyph(dots="1", says="btn", carries="1,2,3,4,5,6")
		fitted = glyphs.fittedGlyph(self.driver, chooses, spell)
		self.assertEqual(fitted.cells, [0x3F])

	def test_aShapeAsWideAsItsTextIsDrawnInPlace(self):
		"""Which changes no layout at all, and buys legibility rather than room. Which of the
		two happens is decided by how wide the shape is drawn and nothing else."""
		fitted = glyphs.fittedGlyph(self.driver, glyphs.WIDE_BUTTON, spell)
		self.assertEqual(fitted.cells, spell("btn"))
		self.assertEqual(fitted.saved, 0)

	def test_theDriverMatchesOnWhatWasActuallyWritten(self):
		"""Not on the text that used to be there. The cells in the buffer are the one carrier,
		so that is what the glyph has to be registered against or it would never draw."""
		fitted = glyphs.fittedGlyph(self.driver, glyphs.BUTTON, spell)
		_rows, fallback = self.driver.built[0]
		self.assertEqual(fallback, fitted.cells)

	def test_aWordingBecomesTheCellsTheReadersTableWrites(self):
		"""Not cells decided when the vocabulary was written: "btn" is three cells in one table
		and might be two in another."""
		fitted = glyphs.fittedGlyph(self.driver, glyphs.WIDE_BUTTON, spell)
		self.assertEqual(fitted.replaces, len(spell("btn")))

	def test_aShapeIsBuiltAtTheWidthItIsDrawnIn(self):
		glyphs.fittedGlyph(self.driver, glyphs.WIDE_BUTTON, spell)
		rows, _fallback = self.driver.built[0]
		self.assertEqual(len(rows[0]), 9)

	def test_aFallbackWrittenAsDotsNeedsNoTable(self):
		fitted = glyphs.fittedGlyph(self.driver, glyphs.FOCUS, spell)
		self.assertEqual(fitted.cells, [0x3F])
		self.assertEqual(fitted.saved, 0)

	def test_aShapeWiderThanItsTextIsRefused(self):
		"""A shape needs the cells it is drawn in, and taking one it was not given would paint
		over whatever came next — a symbol and half a letter, which is neither."""

		def contracted(text):
			return [0b00000001, 0b00000010]

		self.assertIsNone(glyphs.fittedGlyph(self.driver, glyphs.WIDE_BUTTON, contracted))
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


class TestShorteningALine(unittest.TestCase):
	"""A braille line is three lists that have to agree, and this is where they are kept honest.

	The cells are what the display shows. `brailleToRaw` says which character each cell came
	from and is what a routing press is answered through. `rawToBraille` says where each
	character went and is what puts the cursor in the right place. Shorten the cells without
	moving the other two and a routing key in the second half of the line reaches the wrong
	word — a fault a reader meets long after the change that caused it and cannot attribute.
	"""

	def setUp(self):
		self.raw = "btn Search"
		self.cells = spell(self.raw)
		self.rawToBraille = list(range(len(self.raw)))
		self.brailleToRaw = list(range(len(self.raw)))
		self.fitted = glyphs.fittedGlyph(FakeDriver(), glyphs.BUTTON, spell)

	def shortened(self, at=0):
		""":return: the three lists after the glyph has gone in."""
		return glyphs.compress(self.cells, self.rawToBraille, self.brailleToRaw, at, self.fitted)

	def test_theLineGetsShorter(self):
		cells, _rawToBraille, _brailleToRaw = self.shortened()
		self.assertEqual(len(cells), len(self.cells) - 2)

	def test_andTheRestOfItIsUntouched(self):
		cells, _rawToBraille, _brailleToRaw = self.shortened()
		self.assertEqual(cells[1:], self.cells[3:])

	def test_aPressOnTheSymbolReachesWhatTheTextReached(self):
		"""The same thing NVDA already does for the abbreviation this replaces: a routing key on
		any cell of "btn Search" arrives at the button."""
		_cells, _rawToBraille, brailleToRaw = self.shortened()
		self.assertEqual(brailleToRaw[0], 0)

	def test_everyCellAfterItPointsWhereItAlwaysDid(self):
		_cells, _rawToBraille, brailleToRaw = self.shortened()
		self.assertEqual(brailleToRaw[1:], self.brailleToRaw[3:])

	def test_theCharactersAfterItMoveLeftByWhatWasSaved(self):
		_cells, rawToBraille, _brailleToRaw = self.shortened()
		self.assertEqual(rawToBraille[3], 1)
		self.assertEqual(rawToBraille[-1], len(self.raw) - 1 - 2)

	def test_theCharactersInsideItAllPointAtTheSymbol(self):
		_cells, rawToBraille, _brailleToRaw = self.shortened()
		self.assertEqual(rawToBraille[:3], [0, 0, 0])

	def test_theMapsStayAsLongAsWhatTheyMap(self):
		cells, rawToBraille, brailleToRaw = self.shortened()
		self.assertEqual(len(brailleToRaw), len(cells))
		self.assertEqual(len(rawToBraille), len(self.raw))

	def test_aSymbolInTheMiddleOfALineMovesOnlyWhatFollowsIt(self):
		cells, rawToBraille, brailleToRaw = self.shortened(at=4)
		self.assertEqual(cells[:4], self.cells[:4])
		self.assertEqual(rawToBraille[:4], [0, 1, 2, 3])
		self.assertEqual(brailleToRaw[4], 4)

	def test_aCursorAfterItComesBackWithTheLine(self):
		"""Held separately by the caller, and it would otherwise be left pointing past the end
		of a line that just got shorter."""
		self.assertEqual(glyphs.movedPosition(9, at=0, replaces=3, into=1), 7)

	def test_aCursorBeforeItDoesNotMove(self):
		self.assertEqual(glyphs.movedPosition(2, at=4, replaces=3, into=1), 2)

	def test_aCursorInsideItLandsOnIt(self):
		self.assertEqual(glyphs.movedPosition(5, at=4, replaces=3, into=1), 4)

	def test_aShapeDrawnInPlaceMovesNothing(self):
		"""The other half of the rule: the same call, and the line comes back the length it
		was."""
		fitted = glyphs.fittedGlyph(FakeDriver(), glyphs.WIDE_BUTTON, spell)
		cells, rawToBraille, brailleToRaw = glyphs.compress(
			self.cells,
			self.rawToBraille,
			self.brailleToRaw,
			0,
			fitted,
		)
		self.assertEqual(len(cells), len(self.cells))
		self.assertEqual(rawToBraille[3:], self.rawToBraille[3:])
		self.assertEqual(brailleToRaw[:3], [0, 0, 0])


class Buffer:
	"""A dot grid, as much of one as the catalogue draws on."""

	def __init__(self, width, height):
		self.width = width
		self.height = height
		self.dots = set()

	def setDot(self, x, y):
		self.dots.add((x, y))

	def rows(self):
		return [
			"".join("O" if (x, y) in self.dots else "." for x in range(self.width))
			for y in range(self.height)
		]


class TestTheCatalogue(unittest.TestCase):
	"""Shapes cannot be chosen by looking at them.

	A drawing that is obvious on a screen can be a smudge under a fingertip, two that look quite
	different can feel the same, and there is no way to find that out except by putting them
	side by side and running a hand along them. So the vocabulary comes with a way to feel all
	of it at once.
	"""

	def laid(self, entries=None, width=96, height=35):
		""":return: the catalogue drawn on a panel of a given size."""
		return glyphs.catalogue(Buffer, width, height, entries)

	def test_everySymbolIsPlaced(self):
		_buffer, describeAt = self.laid()
		for name in glyphs.VOCABULARY:
			with self.subTest(name):
				self.assertTrue(
					any(
						describeAt(x, y) == name
						for y in range(0, 35, glyphs.LINE)
						for x in range(0, 96, glyphs.CELL_WIDTH)
					),
					name,
				)

	def test_noTwoShapesTouch(self):
		"""The question the catalogue answers is whether one shape is another, and shapes that
		touch each other answer it wrongly."""
		buffer, _describeAt = self.laid({"a": glyphs.CHECKED, "b": glyphs.CHECKED})
		self.assertEqual(buffer.rows()[0][:9], "OOO...OOO")

	def test_aPressSaysWhichSymbolItIs(self):
		"""Which is most of what makes it a catalogue rather than a row of shapes: counting
		along a line is holding the order in mind while judging the shapes."""
		_buffer, describeAt = self.laid({"first": glyphs.CHECKED, "second": glyphs.UNCHECKED})
		self.assertEqual(describeAt(0, 0), "first")
		self.assertEqual(describeAt(6, 0), "second")

	def test_aPressPastTheEndIsOnNothing(self):
		_buffer, describeAt = self.laid({"only": glyphs.CHECKED})
		self.assertIsNone(describeAt(50, 0))

	def test_aRowThatIsFullStartsAnother(self):
		entries = {str(index): glyphs.CHECKED for index in range(20)}
		_buffer, describeAt = self.laid(entries)
		self.assertEqual(describeAt(0, glyphs.LINE), "16")

	def test_aPanelThatRunsOutStopsRatherThanOverwritingItself(self):
		"""A catalogue that wrapped onto its own first row would read as a symbol nobody
		wrote."""
		entries = {str(index): glyphs.CHECKED for index in range(40)}
		_buffer, describeAt = self.laid(entries, height=glyphs.CELL_HEIGHT)
		self.assertIsNone(describeAt(0, glyphs.LINE))
		self.assertEqual(describeAt(0, 0), "0")

	def test_aWideSymbolTakesTheRoomItNeeds(self):
		_buffer, describeAt = self.laid({"wide": glyphs.WIDE_BUTTON, "after": glyphs.CHECKED})
		self.assertEqual(describeAt(6, 0), "wide")
		self.assertEqual(describeAt(12, 0), "after")

	def test_aDisplayThatWillNotProvideABufferIsNotACrash(self):
		buffer, describeAt = glyphs.catalogue(lambda width, height: None, 96, 35)
		self.assertIsNone(buffer)
		self.assertIsNone(describeAt)


class TestWhetherADisplayGainsAnything(unittest.TestCase):
	def test_aDisplayWithAGapColumnToReclaimDoes(self):
		self.assertTrue(glyphs.supported(FakeDriver(glyphSize=(3, 4), cellSize=(2, 4))))

	def test_aDisplayWhoseCellsAreAlreadyGaplessDoesNot(self):
		"""A glyph on it would be a braille cell drawn the long way round. Asked rather than
		assumed, so the answer for hardware nobody here has is the honest one."""
		self.assertFalse(glyphs.supported(FakeDriver(glyphSize=(2, 4), cellSize=(2, 4))))

	def test_aDisplayThatCannotSayDoesNot(self):
		self.assertFalse(glyphs.supported(object()))
