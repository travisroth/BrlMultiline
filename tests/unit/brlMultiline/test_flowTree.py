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

import unittest

from ._stubs import FakeNavigatorObject, fakeRun, fakeTree, installStubs

installStubs()

from brlMultiline import flowObjects  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402

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
			flowObjects.VISIBLE_TREE.depthOf(index[name])
			for name in ("Inbox", "Work", "Urgent", "Personal")
		]
		self.assertEqual(depths, [1, 2, 3, 2])


class TestATreeOnTheBand(unittest.TestCase):
	"""What the reader actually feels, which is the point of all of the above."""

	def _controller(self, index, at="Inbox", numRows=5):
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
		)
		control.enterAtCursor()
		return control

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


if __name__ == "__main__":
	unittest.main()
