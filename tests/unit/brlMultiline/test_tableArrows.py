# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a browse mode table with the arrow keys.

Two things are held to here, and the second matters more than the first. The first is that
the keys move the way a spreadsheet moves: down a row, right a cell, home to the start of the
row. The second is that **no key is ever swallowed** — where there is no cell that way the
key goes to NVDA and does what it does on any other page, which is what lets the reader walk
out of a table without ever having known they were in a mode. Most of what follows is about
the second.
"""

import sys
import types
import unittest

from ._stubs import (
	FORMAT_CONFIG,
	SCRIPT_STATE,
	Axis,
	FakeTableDocument,
	Movement,
	installStubs,
	log,
	resetConfig,
	spokenPositions,
)

installStubs()

from brlMultiline import tableArrows  # noqa: E402

GRID = [
	["Region", "Q1", "Q2"],
	["North", "1200", "1310"],
	["South", "980", "1024"],
]
"""Three rows by three columns, so a caret in the middle of it has all four ways open."""


class Caret:
	"""What the document answers when asked where the cursor is.

	The only thing this module asks of a selection is whether anything is selected under it.
	*Where* the cursor is comes from `_getTableCellCoords`, which the shared stand-in answers
	out of the document's own row and column, so a position here needs nothing else.
	"""

	def __init__(self, isCollapsed=True):
		self.isCollapsed = isCollapsed


class FakeTableCellAt:
	"""What `_tableFindNewCell` answers with: which cell the cursor has ended up in."""

	def __init__(self, tableID, row, col):
		self.tableID = tableID
		self.row = row
		self.col = col


class BrowsingATable(FakeTableDocument):
	"""A browse mode document with the table navigation seam this module leans on.

	`_tableFindNewCell` is NVDA's own, and what is reproduced here is its **contract** rather
	than its arithmetic: asked with `raiseOnEdge` it raises `LookupError` where there is no
	cell that way, and otherwise answers with the cell it found, a position inside it, and
	the record NVDA keeps for carrying a column through merged cells. Skipping over missing
	cells and that record's own rules are NVDA's to get right, are shared with
	control+alt+arrow, and are deliberately not imitated — a stand-in that reimplemented them
	would be answering this module's questions with this module's own reasoning.

	The edges are real, though: a destination is looked up through `_getTableCellAt` on the
	shared stand-in, so "there is no cell that way" comes from the table's own shape.
	"""

	def __init__(self, rows=None, row=2, col=2, **kwargs):
		super().__init__(GRID if rows is None else rows, row=row, col=col, **kwargs)
		self.passThrough = False
		self._lastTableSelection = None
		self.selectionIsCollapsed = True
		self.refuses = False
		"""Whether the document raises when asked where the cursor is, as one being rebuilt
		under the reader does."""

		self.positions = []
		"""Every position the cursor was set to, newest last."""

		self.searches = []
		"""Every table movement asked for, as (movement, axis)."""

		self.spokenWhenMoved = None
		"""How much had been spoken by the time the cursor was moved. See the test."""

	@property
	def movedTo(self):
		""":return: every cell the cursor was put in, as (row, column)."""
		return [position.at for position in self.positions]

	@property
	def selection(self):
		if self.refuses:
			raise RuntimeError("this document will not say where the cursor is")
		return Caret(self.selectionIsCollapsed)

	@selection.setter
	def selection(self, info):
		self.positions.append(info)
		self.spokenWhenMoved = len(spokenPositions)
		self.row, self.col = info.at

	def _tableFindNewCell(self, movement, axis, selection=None, raiseOnEdge=False):
		if not raiseOnEdge:
			raise NotImplementedError(
				"this stand-in is only ever asked for the raising kind, "
				"since announcing the edge is the thing this module exists not to do",
			)
		self.searches.append((movement, axis))
		here = self._getTableCellCoords(selection or self.selection)
		row, column = here.row, here.col
		if axis == Axis.ROW:
			row = self._destination(row, self.numRows, movement)
		else:
			column = self._destination(column, self.numCols, movement)
		info = self._getTableCellAt(self.tableID, None, row, column)
		info.at = (row, column)
		return FakeTableCellAt(self.tableID, row, column), info, ("a record", row, column)

	@staticmethod
	def _destination(at, count, movement):
		if movement == Movement.NEXT:
			return at + 1
		if movement == Movement.PREVIOUS:
			return at - 1
		return 1 if movement == Movement.FIRST else count


class NotADocument:
	"""Something with a cursor that knows nothing of tables, as a review cursor is."""

	passThrough = False
	selection = Caret()


class ArrowTestCase(unittest.TestCase):
	"""Shared setup: NVDA's settings and the state of its script queue, put back afterwards."""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		spokenPositions.clear()
		self.addCleanup(spokenPositions.clear)
		SCRIPT_STATE.update(waiting=False, sayAllResuming=False)
		self.addCleanup(SCRIPT_STATE.update, waiting=False, sayAllResuming=False)
		log.messages.clear()
		self.addCleanup(log.messages.clear)
		self.gesture = object()

	def move(self, document, movement, axis):
		return tableArrows.moveToCell(document, self.gesture, movement, axis)

	def down(self, document):
		return self.move(document, Movement.NEXT, Axis.ROW)

	def up(self, document):
		return self.move(document, Movement.PREVIOUS, Axis.ROW)

	def right(self, document):
		return self.move(document, Movement.NEXT, Axis.COLUMN)

	def left(self, document):
		return self.move(document, Movement.PREVIOUS, Axis.COLUMN)

	def home(self, document):
		return self.move(document, Movement.FIRST, Axis.COLUMN)

	def end(self, document):
		return self.move(document, Movement.LAST, Axis.COLUMN)


class TestMovingByCell(ArrowTestCase):
	"""The reading itself: the keys move the way they move in a spreadsheet."""

	def test_downMovesARowAndKeepsTheColumn(self):
		document = BrowsingATable()
		self.assertTrue(self.down(document))
		self.assertEqual((document.row, document.col), (3, 2))

	def test_upMovesARowBack(self):
		document = BrowsingATable(row=3)
		self.assertTrue(self.up(document))
		self.assertEqual((document.row, document.col), (2, 2))

	def test_rightMovesACellAndKeepsTheRow(self):
		document = BrowsingATable()
		self.assertTrue(self.right(document))
		self.assertEqual((document.row, document.col), (2, 3))

	def test_leftMovesACellBack(self):
		document = BrowsingATable()
		self.assertTrue(self.left(document))
		self.assertEqual((document.row, document.col), (2, 1))

	def test_homeGoesToTheFirstCellOfTheRow(self):
		document = BrowsingATable(col=3)
		self.assertTrue(self.home(document))
		self.assertEqual((document.row, document.col), (2, 1))

	def test_endGoesToTheLastCellOfTheRow(self):
		document = BrowsingATable(col=1)
		self.assertTrue(self.end(document))
		self.assertEqual((document.row, document.col), (2, 3))

	def test_andBothWorkAlongTheRowRatherThanTheColumn(self):
		"""Which is what home and end have always meant. The axis is what says so."""
		document = BrowsingATable(col=3)
		self.home(document)
		self.assertEqual(document.searches, [(Movement.FIRST, Axis.COLUMN)])

	def test_theCellArrivedAtIsSpoken(self):
		document = BrowsingATable()
		self.down(document)
		self.assertEqual(len(spokenPositions), 1)
		info, _formatConfig, reason = spokenPositions[0]
		self.assertEqual(info.text, "980")
		self.assertEqual(reason, "caret")

	def test_andIsSpokenAsATableCell(self):
		"""NVDA's own table movement forces this: having been moved by cell, the reader is
		told which cell. Their own settings decide everything else, and are not written to."""
		document = BrowsingATable()
		self.down(document)
		_info, formatConfig, _reason = spokenPositions[0]
		self.assertTrue(formatConfig["reportTables"])
		self.assertIsNot(formatConfig, FORMAT_CONFIG, "NVDA's own settings were written to")

	def test_andIsSpokenBeforeTheCursorIsMovedThere(self):
		"""NVDA's own order, and not decoration: setting the selection can move the focus,
		which can rebuild the document under the position just found."""
		document = BrowsingATable()
		self.down(document)
		self.assertEqual(document.spokenWhenMoved, 1)

	def test_thePositionIsCollapsedBeforeItBecomesTheCursor(self):
		"""A cell is a range; a cursor is a point in it."""
		document = BrowsingATable()
		self.down(document)
		self.assertTrue(document.positions[0].isCollapsed)

	def test_andNvdasRecordOfTheMoveIsKept(self):
		"""What carries a column through merged cells, and what makes a run of these keys
		behave as a run of control+alt+arrows does."""
		document = BrowsingATable()
		self.down(document)
		self.assertEqual(document._lastTableSelection, ("a record", 3, 2))

	def test_severalKeysWalkTheTable(self):
		document = BrowsingATable(row=1, col=1)
		self.down(document)
		self.down(document)
		self.right(document)
		self.assertEqual((document.row, document.col), (3, 2))
		self.assertEqual(document.movedTo, [(2, 1), (3, 1), (3, 2)])


class TestNotTrapping(ArrowTestCase):
	"""**The requirement the rest of it rests on.** A reader who cannot leave a table by
	arrowing out of it is in a mode, whatever it is called. The keys are handed back at every
	edge — not with a message, but by simply not being answered here."""

	def test_downOnTheLastRowIsNotAnswered(self):
		document = BrowsingATable(row=3)
		self.assertFalse(self.down(document))
		self.assertEqual((document.row, document.col), (3, 2))

	def test_upOnTheFirstRowIsNotAnswered(self):
		self.assertFalse(self.up(BrowsingATable(row=1)))

	def test_rightInTheLastCellOfARowIsNotAnswered(self):
		"""So the reader walks on into whatever follows, by character, exactly as they would
		anywhere else on the page."""
		self.assertFalse(self.right(BrowsingATable(col=3)))

	def test_leftInTheFirstCellOfARowIsNotAnswered(self):
		self.assertFalse(self.left(BrowsingATable(col=1)))

	def test_homeInTheFirstCellIsNotAnswered(self):
		"""The destination is where the reader already is. A key that would move nothing is
		not a key worth keeping: NVDA's own start of line is more use than nothing at all."""
		self.assertFalse(self.home(BrowsingATable(col=1)))

	def test_endInTheLastCellIsNotAnswered(self):
		self.assertFalse(self.end(BrowsingATable(col=3)))

	def test_andAKeyHandedBackSaysNothingAndMovesNothing(self):
		"""Not even an edge message. There is no edge to announce: from the reader's side the
		key did what that key does."""
		document = BrowsingATable(row=3)
		self.down(document)
		self.assertEqual(spokenPositions, [])
		self.assertEqual(document.movedTo, [])
		self.assertIsNone(document._lastTableSelection)

	def test_andReachingTheEdgeIsNotWrittenDownAsAFault(self):
		"""The bottom of a table is the ordinary case and is caught by name. Left to the
		broad catch it would still work, and would write a warning to the log on every arrow
		key at the bottom of every table the reader reads."""
		self.down(BrowsingATable(row=3))
		self.assertEqual(log.messages, [])


class TestWhereTheKeysAreLeftAlone(ArrowTestCase):
	"""Every one of these is a case where moving by cell is the wrong answer rather than one
	where it would fail, and each is settled before the document is read at all."""

	def test_onAPageThatIsNotInATable(self):
		"""The same document with the caret out on the prose, which is what walking out of a
		table leaves: a page is not a table the reader is in or out of."""
		self.assertFalse(self.down(BrowsingATable(inTable=False)))

	def test_andNothingIsAnnouncedThere(self):
		"""`_tableFindNewCell` says "not in a table cell" out loud, which is right for a
		command the reader chose and very wrong for the down arrow on ordinary prose."""
		self.down(BrowsingATable(inTable=False))
		self.assertEqual(spokenPositions, [])
		self.assertEqual(log.messages, [], "ordinary prose was written down as a fault")

	def test_onSomethingThatIsNotATableNavigatingDocument(self):
		"""Recognised rather than merely survived, which is what the log is asserted on: ask
		such a thing for its cells and it raises, the broad catch turns that into no movement,
		and the reader gets the same arrow key by accident. It is only the same until the day
		something with a cursor answers the question wrongly instead of not at all."""
		self.assertFalse(self.down(NotADocument()))
		self.assertEqual(log.messages, [])

	def test_inFocusMode(self):
		"""Where the arrows belong to the control the reader is working in."""
		document = BrowsingATable()
		document.passThrough = True
		self.assertFalse(self.down(document))

	def test_whenTheKeyIsResumingSayAll(self):
		"""The arrow means "carry on reading from here", not "move a row"."""
		SCRIPT_STATE["sayAllResuming"] = True
		self.assertFalse(self.down(BrowsingATable()))

	def test_whenKeypressesHaveBackedUp(self):
		"""NVDA's own policy for both kinds of movement, kept rather than reinvented."""
		SCRIPT_STATE["waiting"] = True
		self.assertFalse(self.down(BrowsingATable()))

	def test_whenSomethingIsSelectedUnderTheKey(self):
		"""An arrow key over a selection is about the selection."""
		document = BrowsingATable()
		document.selectionIsCollapsed = False
		self.assertFalse(self.down(document))

	def test_whenTheReaderHasTurnedTablesOff(self):
		"""Taking the arrow keys over is something this add-on does on its own account, which
		is exactly what NVDA's "report tables" setting governs."""
		FORMAT_CONFIG["reportTables"] = False
		self.assertFalse(self.down(BrowsingATable()))

	def test_andADocumentThatRaisesCostsNothingButTheCellMove(self):
		"""A page rebuilding under the reader, most likely. The key goes to NVDA, which is the
		same answer as an edge, and is why nothing here re-raises."""
		document = BrowsingATable()
		document.refuses = True
		self.assertFalse(self.down(document))
		self.assertEqual(document.movedTo, [])
		self.assertTrue(
			[level for level, _message in log.messages if level == "debugWarning"],
			"a document that raised was passed over in silence",
		)


class TestTheKeysThemselves(unittest.TestCase):
	"""What is wrapped, what is not, and that NVDA's own script is what runs otherwise."""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		spokenPositions.clear()
		self.addCleanup(spokenPositions.clear)
		SCRIPT_STATE.update(waiting=False, sayAllResuming=False)
		self.addCleanup(SCRIPT_STATE.update, waiting=False, sayAllResuming=False)
		self.ran = []
		self.manager = self.installFakeCursorManager()
		self.addCleanup(tableArrows.remove)

	def installFakeCursorManager(self):
		"""Register a cursor manager the patch can find, as NVDA's own would be.

		Put back afterwards: `flowSources` asks NVDA for this same class to decide that a
		document reads through a cursor of its own, and a stand-in left behind in `sys.modules`
		would be answering that question for every test that ran after this one.
		"""
		ran = self.ran

		class FakeCursorManager:
			"""NVDA's cursor manager, in the six scripts browse mode binds the reading keys
			to, plus two it does not. Each records that NVDA's own movement happened, which
			is what a key answered by a cell move must not do."""

			def script_moveByLine_back(self, gesture):
				ran.append("moveByLine_back")

			script_moveByLine_back.resumeSayAllMode = "caret"

			def script_moveByLine_forward(self, gesture):
				ran.append("moveByLine_forward")

			script_moveByLine_forward.resumeSayAllMode = "caret"

			def script_moveByCharacter_back(self, gesture):
				ran.append("moveByCharacter_back")

			def script_moveByCharacter_forward(self, gesture):
				ran.append("moveByCharacter_forward")

			def script_startOfLine(self, gesture):
				"""moves to the start of the line"""
				ran.append("startOfLine")

			def script_endOfLine(self, gesture):
				ran.append("endOfLine")

			def script_selectLine_forward(self, gesture):
				ran.append("selectLine_forward")

			def script_moveByWord_forward(self, gesture):
				ran.append("moveByWord_forward")

		original = sys.modules.get("cursorManager")
		module = types.ModuleType("cursorManager")
		module.CursorManager = FakeCursorManager
		sys.modules["cursorManager"] = module
		self.addCleanup(sys.modules.__setitem__, "cursorManager", original)
		return FakeCursorManager

	def press(self, name, document=None):
		"""Run one of the scripts as NVDA would, and answer with the document it ran on."""
		document = BrowsingATable() if document is None else document
		getattr(self.manager, name)(document, object())
		return document

	def test_eachKeyMovesTheWayItsNameSays(self):
		tableArrows.install()
		for name, expected in (
			("script_moveByLine_forward", (3, 2)),
			("script_moveByLine_back", (1, 2)),
			("script_moveByCharacter_forward", (2, 3)),
			("script_moveByCharacter_back", (2, 1)),
			("script_startOfLine", (2, 1)),
			("script_endOfLine", (2, 3)),
		):
			document = self.press(name)
			self.assertEqual((document.row, document.col), expected, name)
		self.assertEqual(self.ran, [], "NVDA's own movement ran as well as the cell move")

	def test_andNvdasOwnKeyRunsWhereThereIsNoCellThatWay(self):
		tableArrows.install()
		self.press("script_moveByLine_forward", BrowsingATable(row=3))
		self.assertEqual(self.ran, ["moveByLine_forward"])

	def test_andOnAPageWithNoTableInIt(self):
		tableArrows.install()
		self.press("script_moveByCharacter_forward", BrowsingATable(inTable=False))
		self.assertEqual(self.ran, ["moveByCharacter_forward"])

	def test_selectingAndMovingByWordAreNotTouched(self):
		"""Shift and control with the arrows are other scripts, so being in a table changes
		nothing about selecting text or reading a long cell a word at a time."""
		before = (
			self.manager.script_selectLine_forward,
			self.manager.script_moveByWord_forward,
		)
		tableArrows.install()
		self.assertEqual(
			before,
			(self.manager.script_selectLine_forward, self.manager.script_moveByWord_forward),
		)

	def test_aWrappedScriptIsStillTheScriptNvdaBuilt(self):
		"""Input help reads a description off the function and say all resumption reads a mark
		off it. Both would be lost by a wrapper that did not carry them over."""
		tableArrows.install()
		self.assertEqual(self.manager.script_moveByLine_forward.resumeSayAllMode, "caret")
		self.assertEqual(self.manager.script_startOfLine.__doc__, "moves to the start of the line")

	def test_installingTwiceChangesNothing(self):
		tableArrows.install()
		wrapped = self.manager.script_moveByLine_forward
		tableArrows.install()
		self.assertIs(self.manager.script_moveByLine_forward, wrapped)

	def test_removingPutsNvdasOwnScriptsBack(self):
		before = {name: getattr(self.manager, name) for name in tableArrows.MOVES}
		tableArrows.install()
		tableArrows.remove()
		for name, original in before.items():
			self.assertIs(getattr(self.manager, name), original, name)

	def test_andTheKeysAreNvdasAgainAfterwards(self):
		tableArrows.install()
		tableArrows.remove()
		self.press("script_moveByLine_forward")
		self.assertEqual(self.ran, ["moveByLine_forward"])

	def test_removingLeavesSomebodyElsesWrapperAlone(self):
		"""A class attribute anyone can reach. Putting NVDA's own back over another add-on's
		wrapper would silently undo their work, which is the more damaging half of a shared
		patch. See `panning.remove`."""
		tableArrows.install()

		def somebodyElse(document, gesture):
			self.ran.append("somebody else")

		self.manager.script_moveByLine_forward = somebodyElse
		tableArrows.remove()
		self.assertIs(self.manager.script_moveByLine_forward, somebodyElse)

	def test_removingWhenNothingIsInstalledIsSafe(self):
		tableArrows.remove()
		self.press("script_moveByLine_forward")
		self.assertEqual(self.ran, ["moveByLine_forward"])


if __name__ == "__main__":
	unittest.main()
