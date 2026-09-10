# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a tree down the screen rather than along one generation of it.

The failure these exist for was reported from hardware: in Outlook's Go To Folder dialog an
expanded folder's contents were simply absent from the display. A tree item's `next` is its
next sibling, so a run built from it steps over everything inside an open node, and the
same-parent test would have thrown those rows out even if the walk had reached them.

The tree used throughout is the shape that catches both halves of that. Reading it down the
screen gives:

	Inbox
	  Work
	    Urgent
	  Personal
	Archive

`Work` is open and `Personal` is not, so `Personal`'s own children are not rows. Getting
from `Urgent` to `Personal` is a step the sibling walk cannot make in either direction, and
getting from `Personal` to `Archive` needs a climb out of the subtree.
"""

import dataclasses
import unittest

from ._stubs import (
	CONFIG,
	FakeNavigatorObject,
	fakeRun,
	fakeTree,
	installStubs,
	resetConfig,
	wrapChildren,
)

installStubs()

from brlMultiline import flowObjects  # noqa: E402
from brlMultiline.flowIndent import FOCUS_CELL  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402

from .test_flowSegment import (  # noqa: E402
	FakeHandler as BandHandler,
	FakePlugin,
	containerWithBand,
)

NUM_COLS = 24

TREE = [
	(
		"Inbox",
		True,
		[
			("Work", True, [("Urgent", False, [])]),
			("Personal", False, [("Hidden", False, [])]),
		],
	),
	("Archive", False, []),
]
"""The tree above. `Personal` is closed, so `Hidden` is not a row."""

VISIBLE = ["Inbox", "Work", "Urgent", "Personal", "Archive"]
"""What is on the screen, top to bottom."""


class FakeHandler:
	def __init__(self):
		self.buffer = None


def tree():
	return fakeTree(TREE)


def walkFrom(start, forward=True, limit=20):
	""":return: the names a walk visits, starting at one node."""
	step = flowObjects.VISIBLE_TREE.nextOf if forward else flowObjects.VISIBLE_TREE.previousOf
	names = [start.name]
	node = start
	for _ in range(limit):
		node = step(node)
		if node is None:
			break
		names.append(node.name)
	return names


class TestWhichAdapterReadsATree(unittest.TestCase):
	def test_aTreeItemIsReadAsATree(self):
		_control, index = tree()
		self.assertIs(flowObjects.adapterFor(index["Inbox"]), flowObjects.VISIBLE_TREE)

	def test_aListItemIsStillReadAsSiblings(self):
		"""The baseline that must not move: a flat list reads exactly as it did."""
		self.assertIs(flowObjects.adapterFor(fakeRun(["Apple"])[0]), flowObjects.SIBLING_RUN)

	def test_aMenuItemIsStillReadAsSiblings(self):
		items = fakeRun(["Open", "Close"], role="MENUITEM")
		self.assertIs(flowObjects.adapterFor(items[0]), flowObjects.SIBLING_RUN)


class TestWalkingDownTheScreen(unittest.TestCase):
	def test_theWholeVisibleTreeIsReached(self):
		_control, index = tree()
		self.assertEqual(walkFrom(index["Inbox"]), VISIBLE)

	def test_anOpenNodeIsFollowedByItsChild(self):
		"""The step the sibling walk could not make, and the reported failure."""
		_control, index = tree()
		self.assertEqual(flowObjects.VISIBLE_TREE.nextOf(index["Inbox"]).name, "Work")

	def test_aClosedNodeIsFollowedByWhatComesAfterIt(self):
		"""A tree control hands back a closed node's children happily. They are not rows."""
		_control, index = tree()
		self.assertEqual(flowObjects.VISIBLE_TREE.nextOf(index["Personal"]).name, "Archive")

	def test_leavingASubtreeClimbsToTheNextThingAfterIt(self):
		_control, index = tree()
		self.assertEqual(flowObjects.VISIBLE_TREE.nextOf(index["Urgent"]).name, "Personal")

	def test_theLastRowHasNothingAfterIt(self):
		_control, index = tree()
		self.assertIsNone(flowObjects.VISIBLE_TREE.nextOf(index["Archive"]))


class TestWalkingBackUpTheScreen(unittest.TestCase):
	"""Reading backward is not the mirror of reading forward, and that is where it breaks."""

	def test_theWholeVisibleTreeIsReached(self):
		_control, index = tree()
		self.assertEqual(walkFrom(index["Archive"], forward=False), list(reversed(VISIBLE)))

	def test_aNodeIsPrecededByTheDeepestRowAboveIt(self):
		"""Not by its previous sibling, which is several rows further up the screen."""
		_control, index = tree()
		self.assertEqual(flowObjects.VISIBLE_TREE.previousOf(index["Personal"]).name, "Urgent")

	def test_aFirstChildIsPrecededByItsParent(self):
		_control, index = tree()
		self.assertEqual(flowObjects.VISIBLE_TREE.previousOf(index["Work"]).name, "Inbox")

	def test_aClosedNodesChildrenAreNotWalkedBackInto(self):
		_control, index = tree()
		self.assertEqual(flowObjects.VISIBLE_TREE.previousOf(index["Archive"]).name, "Personal")

	def test_theTopRowHasNothingBeforeIt(self):
		_control, index = tree()
		self.assertIsNone(flowObjects.VISIBLE_TREE.previousOf(index["Inbox"]))

	def test_theWalkIsReversible(self):
		"""Panning back must land where panning forward started. The anchor design rests on
		it, and a backward walk that mirrored the forward one would fail here."""
		_control, index = tree()
		for name in VISIBLE:
			with self.subTest(name=name):
				after = flowObjects.VISIBLE_TREE.nextOf(index[name])
				if after is None:
					continue
				self.assertIs(flowObjects.VISIBLE_TREE.previousOf(after), index[name])


class TestWhatBelongsToTheRun(unittest.TestCase):
	def test_aChildBelongsToTheRunItsParentIsIn(self):
		"""`_sameParent` said no, which is what discarded an open folder's contents."""
		_control, index = tree()
		self.assertTrue(flowObjects.VISIBLE_TREE.admits(index["Inbox"], index["Urgent"]))

	def test_soDoesSomethingInAnotherBranch(self):
		_control, index = tree()
		self.assertTrue(flowObjects.VISIBLE_TREE.admits(index["Urgent"], index["Archive"]))

	def test_anItemOfAnotherTreeDoesNot(self):
		_control, index = tree()
		_other, elsewhere = tree()
		self.assertFalse(flowObjects.VISIBLE_TREE.admits(index["Inbox"], elsewhere["Archive"]))

	def test_somethingThatIsNotATreeItemDoesNot(self):
		"""A button under the tree, which the run must end at rather than read."""
		_control, index = tree()
		button = FakeNavigatorObject("OK", role="BUTTON")
		button.parent = index["Inbox"].parent
		self.assertFalse(flowObjects.VISIBLE_TREE.admits(index["Inbox"], button))


class TestHowDeepATreeItemSits(unittest.TestCase):
	def test_theLevelIsUsedWhereThereIsOne(self):
		_control, index = tree()
		self.assertEqual(flowObjects.VISIBLE_TREE.depthOf(index["Urgent"]), 3)

	def test_depthIsCountedWhereTheTreeWillNotSay(self):
		"""A tree reporting no level is the one whose depth the reader cannot otherwise learn."""
		_control, index = tree()
		for node in index.values():
			node.positionInfo = {}
		self.assertEqual(flowObjects.VISIBLE_TREE.depthOf(index["Urgent"]), 3)
		self.assertEqual(flowObjects.VISIBLE_TREE.depthOf(index["Inbox"]), 1)

	def test_theStructureBeatsALevelThatContradictsIt(self):
		"""Outlook's folder pane, from a hardware report. Its account row and the Inbox
		beneath it both report level 1, so the account's whole contents drew flush with the
		account — while the walk had descended into the account's own first child to reach
		them. A walk that steps into a child and draws it at its parent's indent is telling
		the reader two different things about one tree."""
		_control, index = tree()
		for name in ("Inbox", "Work", "Personal"):
			index[name].positionInfo = {"level": 1}
		self.assertEqual(flowObjects.VISIBLE_TREE.depthOf(index["Inbox"]), 1)
		self.assertEqual(flowObjects.VISIBLE_TREE.depthOf(index["Work"]), 2)
		self.assertEqual(flowObjects.VISIBLE_TREE.depthOf(index["Personal"]), 2)

	def test_aLevelIsBelievedWhereThereIsNoStructureToCount(self):
		"""An outline whose items all hang directly off the control is a real shape, and the
		attribute is the only thing that knows its depth."""
		flat = FakeNavigatorObject("an outline", role="TREEVIEW")
		item = FakeNavigatorObject("deep thing", role="TREEVIEWITEM")
		item.parent = flat
		item.positionInfo = {"level": 5}
		self.assertEqual(flowObjects.VISIBLE_TREE.depthOf(item), 5)

	def test_anItemWithNeitherReadsAsTheTop(self):
		flat = FakeNavigatorObject("an outline", role="TREEVIEW")
		item = FakeNavigatorObject("a thing", role="TREEVIEWITEM")
		item.parent = flat
		self.assertEqual(flowObjects.VISIBLE_TREE.depthOf(item), 1)

	def test_theBandDrawsTheStructureRatherThanTheLabel(self):
		"""The reported failure at the level the reader met it: an account row and its
		folders all flush against the left margin."""
		_control, index = tree()
		for name in ("Inbox", "Work", "Personal"):
			index[name].positionInfo = {"level": 1}
		depths = [
			flowObjects.VISIBLE_TREE.depthOf(index[name]) for name in ("Inbox", "Work", "Urgent", "Personal")
		]
		self.assertEqual(depths, [1, 2, 3, 2])


def bandOver(index, at="Inbox", numRows=5, lineFocus=False):
	""":return: a band reading a tree, with the reader on one of its items.

	The focus mark is off unless a test asks for it, so that a test about indent or about
	what is on the display is reading the cells the renderer produced and nothing else.
	"""
	adapter = flowObjects.VISIBLE_TREE
	source = flowObjects.ObjectFlowSource(
		index[at],
		adapter,
		flowObjects.regionFactory(live=True, adapter=adapter),
		generation=1,
	)
	control = FlowController(
		source,
		FlowRenderer(FakeHandler(), numCols=NUM_COLS, fillRows=True),
		numRows=numRows,
		live=True,
		movesCursor=False,
		lineFocus=lineFocus,
	)
	control.enterAtCursor()
	return control


class TestATreeOnTheBand(unittest.TestCase):
	"""What the reader actually feels, which is the point of all of the above."""

	def _controller(self, index, at="Inbox", numRows=5, lineFocus=False):
		return bandOver(index, at=at, numRows=numRows, lineFocus=lineFocus)

	def _rows(self, control):
		cells = control.cells()
		return [
			"".join(" " if cell == 0 else chr(cell) for cell in cells[start : start + NUM_COLS])
			for start in range(0, len(cells), NUM_COLS)
		]

	def _indent(self, text):
		return len(text) - len(text.lstrip())

	def test_theOpenFoldersContentsAreOnTheDisplay(self):
		"""The reported failure, at the level the reader met it."""
		_control, index = tree()
		rows = self._rows(self._controller(index))
		self.assertIn("Work", rows[1])
		self.assertIn("Urgent", rows[2])

	def test_theRowsAreIndentedByTheirDepth(self):
		"""And now there is more than one depth on the band to indent, which is what the
		flat sibling run could never produce."""
		_control, index = tree()
		rows = self._rows(self._controller(index))
		self.assertEqual([self._indent(row) for row in rows[:5]], [0, 2, 4, 2, 0])

	def test_aClosedFoldersContentsAreNotOnTheDisplay(self):
		_control, index = tree()
		rows = self._rows(self._controller(index))
		self.assertNotIn("Hidden", "".join(rows))


class TestMarkingTheRowTheFocusIsOn(unittest.TestCase):
	"""Which of eight rows has the focus, answered by one pass of the hand.

	The cursor already says it, and it is not enough across rows: by default it is dots 7 and
	8 under the text, so it is found by reading the row it is under, and a reader running a
	hand down a folder tree to see where they are has to read every row. The mark is in the
	same column on every row.

	It is drawn into the row's own indent, so it costs no cell of text and moves nothing.
	That is also its limit: a row at the left margin has nowhere to put it.
	"""

	def _controller(self, index, at, numRows=5, lineFocus=True):
		return bandOver(index, at=at, numRows=numRows, lineFocus=lineFocus)

	def _rowCells(self, control, row):
		cells = control.cells()
		return cells[row * NUM_COLS : (row + 1) * NUM_COLS]

	def test_theFocusedRowIsMarkedAtTheLeft(self):
		_control, index = tree()
		control = self._controller(index, at="Work")
		marked = [row for row in range(5) if self._rowCells(control, row)[:2] == [FOCUS_CELL] * 2]
		self.assertEqual(marked, [0])

	def test_noOtherRowIsMarked(self):
		"""Two marks would answer the question the mark exists to answer with "either"."""
		_control, index = tree()
		control = self._controller(index, at="Work")
		for row in range(1, 5):
			self.assertNotIn(FOCUS_CELL, self._rowCells(control, row)[:2])

	def test_theMarkCostsTheRowNothing(self):
		"""The whole reason it is drawn into the indent rather than in front of it."""
		_control, index = tree()
		marked = self._controller(index, at="Work").cells()
		plain = self._controller(index, at="Work", lineFocus=False).cells()
		self.assertEqual(marked[2:], plain[2:])

	def test_anItemAtTheLeftMarginIsNotMarked(self):
		"""There is nowhere to draw it that would not push the row's own content sideways."""
		_control, index = tree()
		control = self._controller(index, at="Inbox")
		plain = self._controller(index, at="Inbox", lineFocus=False)
		self.assertEqual(control.cells(), plain.cells())

	def test_theMarkCanBeTurnedOff(self):
		_control, index = tree()
		control = self._controller(index, at="Work", lineFocus=False)
		self.assertNotIn(FOCUS_CELL, control.cells())

	def test_everyRowOfAWrappedItemIsMarked(self):
		"""A hand running down the left margin should feel the whole of what has the focus,
		not its first row and then a gap."""
		_control, index = tree()
		index["Work"].name = "Work in progress"
		control = self._controller(index, at="Work")
		marked = [row for row in range(5) if self._rowCells(control, row)[:2] == [FOCUS_CELL] * 2]
		self.assertEqual(marked, [0, 1])


def wrappedTree():
	""":return: the same tree with a grouping between every node and its children."""
	control, index = tree()
	for name in ("Inbox", "Work", "Personal"):
		wrapChildren(index[name])
	return control, index


def tellingItsLastChild(control, index):
	"""Let every container answer `lastChild`, as a real provider does.

	The walk to the end of a branch asks before it walks, because asking is one call whatever
	the branch holds. The stubs answer nothing by default, so the walk is what the other
	tests here exercise; this is the other path.
	"""
	seen = [control, *index.values()]
	for node in list(seen):
		for child in list(getattr(node, "children", None) or ()):
			if child not in seen:
				seen.append(child)
	for node in seen:
		kids = list(getattr(node, "children", None) or ())
		node.lastChild = kids[-1] if kids else None
	return control, index


class TestAskingAContainerWhereItsRowsEnd(unittest.TestCase):
	"""Reading backward into a branch means finding the deepest last thing under it.

	Walked from the branch's first child that is one call into the application per row, all
	of them inside one step. A container knows its own last child, so it is asked — and
	asked of the container that actually holds the rows, which behind a wrapper is the
	wrapper rather than the node. Asking the node there gives back the wrapper itself, which
	is scenery and not a row the reader can be put on.
	"""

	def test_theWalkBackIsUnchangedWhenTheTreeAnswers(self):
		_control, index = tellingItsLastChild(*tree())
		self.assertEqual(walkFrom(index["Archive"], forward=False), list(reversed(VISIBLE)))

	def test_andBehindAWrapperToo(self):
		_control, index = tellingItsLastChild(*wrappedTree())
		self.assertEqual(walkFrom(index["Archive"], forward=False), list(reversed(VISIBLE)))

	def test_andTheRowAboveASiblingIsStillTheDeepestThingUnderTheOneBeforeIt(self):
		_control, index = tellingItsLastChild(*wrappedTree())
		self.assertEqual(flowObjects.VISIBLE_TREE.previousOf(index["Personal"]).name, "Urgent")


class TestASubtreeBehindAWrapper(unittest.TestCase):
	"""A tree item's children are not always its direct children.

	UIA hangs them off a grouping in between, and so do some IA2 implementations. A walk that
	read `firstChild` and expected another tree item offered the grouping as the next row,
	the run refused it as something outside itself, and the reader was shown a parent whose
	contents had vanished — the very failure the visible-tree walk exists to fix, in a
	different control.

	The wrapper is scenery: stepped over in both directions, and not a level of depth.
	"""

	def test_theWholeVisibleTreeIsStillReachedForward(self):
		_control, index = wrappedTree()
		self.assertEqual(walkFrom(index["Inbox"]), VISIBLE)

	def test_theWholeVisibleTreeIsStillReachedBackward(self):
		_control, index = wrappedTree()
		self.assertEqual(walkFrom(index["Archive"], forward=False), list(reversed(VISIBLE)))

	def test_anOpenNodeIsFollowedByItsChildThroughTheWrapper(self):
		"""The reported failure: the grouping was offered as the next row."""
		_control, index = wrappedTree()
		self.assertEqual(flowObjects.VISIBLE_TREE.nextOf(index["Inbox"]).name, "Work")

	def test_aFirstChildIsStillPrecededByItsParent(self):
		_control, index = wrappedTree()
		self.assertEqual(flowObjects.VISIBLE_TREE.previousOf(index["Work"]).name, "Inbox")

	def test_leavingASubtreeStillClimbsPastTheWrapper(self):
		_control, index = wrappedTree()
		self.assertEqual(flowObjects.VISIBLE_TREE.nextOf(index["Urgent"]).name, "Personal")

	def test_aWrappedChildIsStillPartOfTheRun(self):
		"""Membership is what threw the rows away even where the walk reached them."""
		_control, index = wrappedTree()
		self.assertTrue(flowObjects.VISIBLE_TREE.admits(index["Inbox"], index["Urgent"]))

	def test_aWrapperIsNotALevelOfDepth(self):
		"""Counting it would draw the whole subtree a step further in than the tree it is
		in, which is the wrong picture the counted depth exists to avoid."""
		_control, index = wrappedTree()
		# No levels reported, so counting is the only thing that can answer and the wrapper
		# is squarely in its way. With the level left in, `positionInfo` covers for a count
		# that stopped at the grouping and the mistake never shows.
		for name in VISIBLE:
			index[name].positionInfo = {}
		depths = [flowObjects.VISIBLE_TREE.depthOf(index[name]) for name in VISIBLE]
		self.assertEqual(depths, [1, 2, 3, 2, 1])

	def test_aClosedNodeBehindAWrapperIsStillClosed(self):
		_control, index = wrappedTree()
		self.assertEqual(flowObjects.VISIBLE_TREE.nextOf(index["Personal"]).name, "Archive")

	def test_somethingThatIsNotSceneryIsNotWalkedInto(self):
		"""The bound on the idea. The thing past a *real* boundary — the tree control, a
		toolbar beside it — is also not a tree item, and treating those as scenery would walk
		the reader straight out of the control they are in. So only the named roles are
		stepped through, and anything else ends the descent: the node reads as having no
		visible children, and the next row is its sibling."""
		_control, index = tree()
		index["Inbox"].firstChild = FakeNavigatorObject("a toolbar", role="TOOLBAR")
		self.assertEqual(flowObjects.VISIBLE_TREE.nextOf(index["Inbox"]).name, "Archive")


class TestChildrenThatArriveLate(unittest.TestCase):
	"""A provider that fetches children on demand reports a node open before it has any.

	The state is settled from the first moment and the rows appear later, so a band watching
	only the state went on showing an open folder with nothing in it.
	"""

	def _source(self, index, at):
		adapter = flowObjects.VISIBLE_TREE
		return flowObjects.ObjectFlowSource(
			index[at],
			adapter,
			flowObjects.regionFactory(live=True, adapter=adapter),
			generation=1,
		)

	def _emptied(self, index, name="Personal"):
		"""Open, and showing nothing yet. What a lazily filled node looks like on arrival."""
		node = index[name]
		node.states = {"EXPANDED"}
		node.firstChild = None
		node.children = []
		return node

	def test_anOpenNodeThatIsStillEmptyIsNotAChange(self):
		_control, index = tree()
		self._emptied(index)
		source = self._source(index, "Personal")
		source.blockAtCursor()
		self.assertFalse(source.shapeChanged())
		self.assertFalse(source.shapeChanged())

	def test_childrenArrivingUnderAnOpenNodeIsAChange(self):
		_control, index = tree()
		node = self._emptied(index)
		source = self._source(index, "Personal")
		source.blockAtCursor()
		self.assertFalse(source.shapeChanged())
		node.firstChild = index["Hidden"]
		node.children = [index["Hidden"]]
		self.assertTrue(source.shapeChanged())

	def test_childrenGoingAwayUnderAnOpenNodeIsAChangeToo(self):
		_control, index = tree()
		source = self._source(index, "Work")
		source.blockAtCursor()
		index["Work"].firstChild = None
		index["Work"].children = []
		self.assertTrue(source.shapeChanged())

	def test_aClosedNodeIsNotAskedWhatItIsHolding(self):
		"""A closed node cannot be showing children whatever it holds, and asking would be a
		call into the application on every redraw for every reader in a list or a menu."""
		_control, index = tree()
		asked = []
		adapter = dataclasses.replace(
			flowObjects.VISIBLE_TREE,
			openChildOf=lambda obj: asked.append(obj),
		)
		source = flowObjects.ObjectFlowSource(
			index["Personal"],
			adapter,
			flowObjects.regionFactory(live=True, adapter=adapter),
			generation=1,
		)
		source.blockAtCursor()
		source.shapeChanged()
		self.assertEqual(asked, [])


class TestNoticingANodeBeingOpened(unittest.TestCase):
	"""Expanding a node is not a focus change, and NVDA reports it through no event."""

	def _source(self, index, at="Personal"):
		adapter = flowObjects.VISIBLE_TREE
		return flowObjects.ObjectFlowSource(
			index[at],
			adapter,
			flowObjects.regionFactory(live=True, adapter=adapter),
			generation=1,
		)

	def test_nothingHasChangedWhenNothingHasChanged(self):
		_control, index = tree()
		source = self._source(index)
		source.blockAtCursor()
		self.assertFalse(source.shapeChanged())

	def test_openingTheNodeTheReaderIsOnIsNoticed(self):
		_control, index = tree()
		source = self._source(index)
		source.blockAtCursor()
		index["Personal"].states = {"EXPANDED"}
		self.assertTrue(source.shapeChanged())

	def test_closingItIsNoticedToo(self):
		_control, index = tree()
		source = self._source(index, at="Work")
		source.blockAtCursor()
		index["Work"].states = {"COLLAPSED"}
		self.assertTrue(source.shapeChanged())

	def test_theChangeIsReportedOnceRatherThanForever(self):
		"""Asked before every redraw, so a change that stayed true would rebuild the band
		over and over while the reader sat still."""
		_control, index = tree()
		source = self._source(index)
		source.blockAtCursor()
		index["Personal"].states = {"EXPANDED"}
		self.assertTrue(source.shapeChanged())
		self.assertFalse(source.shapeChanged())

	def test_arrivingSomewhereAlreadyOpenIsNotAChange(self):
		"""Or every focus move onto an open folder would read as one having just opened."""
		_control, index = tree()
		source = self._source(index)
		source.blockAtCursor()
		source.setCurrent(index["Work"])
		self.assertFalse(source.shapeChanged())

	def test_aLeafNeverReportsAChange(self):
		"""A leaf has neither state, and reading that as closed would make every arrival at
		one look like a folder having shut."""
		_control, index = tree()
		source = self._source(index, at="Urgent")
		source.blockAtCursor()
		self.assertFalse(source.shapeChanged())

	def test_aFlatRunHasNoSuchNotion(self):
		items = fakeRun(["Apple", "Banana"])
		source = flowObjects.ObjectFlowSource(
			items[0],
			flowObjects.SIBLING_RUN,
			flowObjects.regionFactory(live=True, adapter=flowObjects.SIBLING_RUN),
			generation=1,
		)
		source.blockAtCursor()
		self.assertFalse(source.shapeChanged())


class TestMovingFromAParentToItsChild(unittest.TestCase):
	"""The whole band, not the walk: what happens to the rows around the reader when they
	step from a folder into the first thing inside it.

	Reported from hardware twice. The second time the cause was the re-read that arriving on
	an object now does: it drew the band again and handed the drawing only the block it had
	re-read, which is what the window then held. The rows above and below went, and the child
	the reader had just moved onto became the top row — a display's worth of movement for one
	arrow key.
	"""

	def setUp(self):
		import api
		import braille

		from brlMultiline.flowBand import FlowBand

		resetConfig()
		self.addCleanup(resetConfig)
		CONFIG["flowEnabled"] = True
		CONFIG["flowObjects"] = True
		handler = BandHandler(5, NUM_COLS)
		self.addCleanup(setattr, braille, "handler", braille.handler)
		braille.handler = handler
		container = containerWithBand(handler, numRows=5, numCols=NUM_COLS)
		handler.mainBuffer = handler.buffer = container
		self.band = FlowBand(FakePlugin(container))
		self.api = api
		self.addCleanup(setattr, api, "getFocusObject", api.getFocusObject)
		self.addCleanup(setattr, api, "getNavigatorObject", api.getNavigatorObject)
		_control, self.index = tree()

	def _show(self, name):
		obj = self.index[name]
		self.api.getFocusObject = lambda: obj
		self.api.getNavigatorObject = lambda: obj
		self.assertTrue(self.band.showObject(obj))

	def _rows(self):
		""":return: what the band is holding, as the dry run describes it."""
		return self.band.controller.describeRows()

	def _cursorRow(self):
		""":return: which row of the band the reader is on, by the mark the report puts there."""
		for index, line in enumerate(self._rows()):
			if line.startswith(f"{index}: *"):
				return index
		return None

	def _depths(self):
		""":return: the depth of each row, which is what tells these rows apart."""
		found = []
		for line in self._rows():
			mark = line.find("depth ")
			found.append(int(line[mark + 6]) if mark >= 0 else None)
		return found

	def test_theBandStaysWhereItIsAndTheCursorMoves(self):
		self._show("Inbox")
		self.assertEqual(self._cursorRow(), 0)
		self._show("Work")
		self.assertEqual(self._cursorRow(), 1)
		self._show("Urgent")
		self.assertEqual(self._cursorRow(), 2)

	def test_theRowsAroundThemAreStillThere(self):
		"""The folder they came out of, and everything under it."""
		self._show("Inbox")
		self._show("Work")
		self.assertEqual(self._depths(), [1, 2, 3, 2, 1])

	def test_andTheBandIsStillFull(self):
		"""A band left holding one block reads as a run that ends under the reader's hand."""
		self._show("Inbox")
		self._show("Urgent")
		self.assertEqual(len(self.band.controller.window.blocks), 5)


if __name__ == "__main__":

	unittest.main()
