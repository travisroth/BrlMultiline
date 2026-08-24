# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for reading a run of objects as a flow.

A list box on a single line display is one item at a time; on eight rows it can be eight
items with the focused one among them. That is the same win as spatial reading in a
document, in the place it is easiest to feel, and it is a different source underneath
because an object is not a line of text.

Three things are tested harder than the rest, because all three are decisions rather than
arithmetic: that nothing here moves the reader's selection, that the focused item is still
identifiable, and that the run is walked lazily — each step being a call into the
application is the whole reason the budget exists.
"""

import dataclasses
import unittest

from ._stubs import (
	FakeNavigatorObject,
	FakeTreeInterceptor,
	fakeRun,
	fakeSeparator,
	installStubs,
)

installStubs()

from brlMultiline import flowObjects  # noqa: E402
from brlMultiline.flow import Edge, ResultKind  # noqa: E402
from brlMultiline.flowIndent import FOCUS_CELL  # noqa: E402
from brlMultiline.flowControl import FlowController  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402
from brlMultiline.flowSources import FetchBudget  # noqa: E402

NUM_COLS = 20
"""Wide enough that a stub block — "Banana LISTITEM" — is one row."""


def sourceOver(items, at=0, live=True, budget=None, adapter=None) -> flowObjects.ObjectFlowSource:
	"""Build a source over a run of objects, with the reader on one of them."""
	adapter = flowObjects.SIBLING_RUN if adapter is None else adapter
	return flowObjects.ObjectFlowSource(
		items[at],
		adapter,
		flowObjects.regionFactory(live=live, adapter=adapter),
		generation=1,
		budget=budget,
	)


class FakeHandler:
	"""Enough of the braille handler for a layout buffer to read its settings through."""

	def __init__(self):
		self.buffer = None


def controllerOver(
	items,
	at=0,
	numRows=4,
	adapter=None,
	budget=None,
	lineFocus=False,
) -> FlowController:
	"""Build a flow over a run of objects, wired the way the band wires one.

	Live, so the focused item shows a cursor, and moving nothing: the reading position here
	is a selection, and only a focus event may move it.
	"""
	source = sourceOver(items, at=at, budget=budget, adapter=adapter)
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


def objectAt(control: FlowController, blockId):
	""":return: the object a block of a run was built for."""
	return getattr(control.regionFor(blockId), "obj", None)


class TestWhatNvdaSaysAboutDepth(unittest.TestCase):
	"""What the adapter reads, which is `positionInfo["level"]` and nothing invented."""

	def test_aLevelIsReadFromPositionInfo(self):
		items = fakeRun(["Inbox", "Archive"], role="TREEVIEWITEM", levels=[1, 2])
		self.assertEqual(flowObjects.SIBLING_RUN.depthOf(items[1]), 2)

	def test_aFlatRunIsToldNothing(self):
		"""An ordinary list box reports `indexInGroup` and no level at all."""
		self.assertIsNone(flowObjects.SIBLING_RUN.depthOf(fakeRun(["Apple"])[0]))

	def test_aLevelOfZeroIsNotALevel(self):
		"""Some providers use 0 for "no level" instead of leaving the key out. Drawing that
		as a depth would push a whole run in for nothing."""
		items = fakeRun(["Inbox"], role="TREEVIEWITEM", levels=[0])
		self.assertIsNone(flowObjects.SIBLING_RUN.depthOf(items[0]))

	def test_anUnreadableLevelIsNotALevel(self):
		items = fakeRun(["Inbox"], role="TREEVIEWITEM")
		items[0].positionInfo = {"level": "deep"}
		self.assertIsNone(flowObjects.SIBLING_RUN.depthOf(items[0]))

	def test_anObjectThatWillNotSayIsNotALevel(self):
		items = fakeRun(["Inbox"], role="TREEVIEWITEM")

		class Refuses:
			def __get__(self, obj, kind):
				raise RuntimeError("no")

		type(items[0]).positionInfo = Refuses()
		try:
			self.assertIsNone(flowObjects.SIBLING_RUN.depthOf(items[0]))
		finally:
			del type(items[0]).positionInfo


class TestHowDeepAnObjectSits(unittest.TestCase):
	"""What a run makes of NVDA's answer.

	An item is in a structure by being in a run, so where NVDA reports no level the run says
	the first one rather than none. It costs the display nothing — level 1 is the baseline
	and the baseline is the left margin — and it buys the hanging indent on a wrapped row,
	which the plan can only offer where there is a depth to hang relative to.
	"""

	def test_aLevelIsCarriedThrough(self):
		items = fakeRun(["Inbox", "Archive"], role="TREEVIEWITEM", levels=[1, 2])
		self.assertEqual(sourceOver(items, at=1).blockAtCursor().block.depth, 2)

	def test_aFlatRunSitsAtTheFirstLevel(self):
		items = fakeRun(["Apple", "Banana"])
		self.assertEqual(sourceOver(items).blockAtCursor().block.depth, flowObjects.FIRST_LEVEL)

	def test_soDoesOneWhoseLevelCannotBeRead(self):
		"""Whether a control answers must not decide how its items are laid out: in one
		Outlook message list one message wrapped with the hanging indent and the next did
		not, because one of them had a level and the other had none."""
		items = fakeRun(["Inbox"], role="TREEVIEWITEM", levels=[0])
		self.assertEqual(sourceOver(items).blockAtCursor().block.depth, flowObjects.FIRST_LEVEL)

	def test_anAdapterMaySayItselfHowDeepSomethingSits(self):
		"""The reason `depthOf` is on the adapter: a control that knows its own shape better
		than NVDA does is exactly what an add-on registers one for."""
		adapter = dataclasses.replace(flowObjects.SIBLING_RUN, depthOf=lambda obj: 7)
		items = fakeRun(["Inbox"], role="TREEVIEWITEM")
		self.assertEqual(sourceOver(items, adapter=adapter).blockAtCursor().block.depth, 7)

	def test_anAdapterThatRaisesCostsTheDepthAndNothingElse(self):
		def explode(obj):
			raise RuntimeError("no")

		adapter = dataclasses.replace(flowObjects.SIBLING_RUN, depthOf=explode)
		items = fakeRun(["Inbox"], role="TREEVIEWITEM", levels=[2])
		block = sourceOver(items, adapter=adapter).blockAtCursor().block
		self.assertEqual(block.depth, flowObjects.FIRST_LEVEL)
		self.assertIn("Inbox", block.region.rawText)


LONG_ITEM = "an item with a name far too long to fit on one row of this narrow band"


class TestAWrappedItemInAFlatRun(unittest.TestCase):
	"""A long item in an ordinary list, which is most of what a reader meets.

	Both halves of this were reported from hardware in Outlook's message list, and both come
	back to the same thing: whether the control happened to report `positionInfo["level"]`.
	"""

	def _control(self, numRows=4, lineFocus=False):
		return controllerOver(fakeRun([LONG_ITEM]), numRows=numRows, lineFocus=lineFocus)

	def _row(self, control, index):
		cells = control.cells()
		return cells[index * NUM_COLS : (index + 1) * NUM_COLS]

	def test_theItemItselfStartsAtTheMargin(self):
		"""A flat list is drawn flush left, exactly as it was before it had a depth at all."""
		self.assertNotEqual(self._row(self._control(), 0)[0], 0)

	def test_aWrappedRowHangsFurtherIn(self):
		"""What was lost: with no level reported there was no depth, with no depth there was
		no plan, and with no plan a continuation was drawn flush against the item above it.
		In one message list one message hung its wrapped rows and the next did not."""
		row = self._row(self._control(), 1)
		self.assertEqual(row[:3], [0, 0, 0])
		self.assertNotEqual(row[3], 0)

	def test_theFocusMarkStaysOffTheHangingRows(self):
		"""An item at the margin carries no mark, and its wrapped rows are not a second
		chance at one. On hardware that was seven rows of one message with six marked — the
		six being exactly the rows that were not the message's own first row."""
		self.assertNotIn(FOCUS_CELL, self._control(lineFocus=True).cells())


class TestDepthOnTheBand(unittest.TestCase):
	"""What a tree actually feels like across eight rows."""

	def _rows(self, control):
		""":return: the band as text, one string per row, blanks as spaces."""
		cells = control.cells()
		return [
			"".join(" " if cell == 0 else chr(cell) for cell in cells[start : start + NUM_COLS])
			for start in range(0, len(cells), NUM_COLS)
		]

	def _indent(self, text):
		return len(text) - len(text.lstrip())

	def test_aTreeIsDrawnAtItsDepths(self):
		items = fakeRun(["Inbox", "Sub", "Deeper"], role="TREEVIEWITEM", levels=[1, 2, 3])
		rows = self._rows(controllerOver(items, numRows=3))
		self.assertEqual([self._indent(row) for row in rows[:3]], [0, 2, 4])

	def test_aFlatRunIsStillFlat(self):
		"""The baseline this milestone must not move."""
		rows = self._rows(controllerOver(fakeRun(["Apple", "Banana"]), numRows=2))
		self.assertEqual([self._indent(row) for row in rows[:2]], [0, 0])

	def test_aDeepTreeIsRebasedToWhatIsOnTheBand(self):
		"""Nine levels of true indent would be most of a 20 cell row."""
		items = fakeRun(["a", "b", "c"], role="TREEVIEWITEM", levels=[8, 9, 10])
		rows = self._rows(controllerOver(items, numRows=3))
		self.assertEqual([self._indent(row) for row in rows[:3]], [0, 2, 4])

	def test_theBandSaysWhatItsMarginStandsFor(self):
		items = fakeRun(["a", "b", "c"], role="TREEVIEWITEM", levels=[8, 9, 10])
		control = controllerOver(items, numRows=3)
		self.assertEqual(control.renderer.indentPlan.noteLevel, 8)

	def test_aRunOfOneDepthDoesNotRebaseWhilePanning(self):
		"""The margin must not twitch while the reader arrows through items that are all
		at the same level."""
		items = fakeRun([str(n) for n in range(12)], role="TREEVIEWITEM", levels=[3] * 12)
		control = controllerOver(items, numRows=3)
		before = control.renderer.indentPlan
		control.panForward()
		self.assertIs(control.renderer.indentPlan, before)

	def test_comingBackOutOfASubtreeRebases(self):
		"""Visible order leaves a subtree, so an item shallower than the margin arrives."""
		items = fakeRun(["a", "b", "c", "d"], role="TREEVIEWITEM", levels=[9, 10, 11, 4])
		control = controllerOver(items, numRows=2)
		control.panForward()
		control.panForward()
		self.assertLessEqual(control.renderer.indentPlan.baseline or 0, 4)


class TestMovingBackToSomethingOffTheBand(unittest.TestCase):
	"""Moving the focus one item must not move the display a whole band.

	From hardware, in Outlook's folder pane: moving from a folder up to the account above it
	put the account on the *bottom* row and filled the rows above it with the folders that
	came before — a whole display of movement for a reader who had asked for one item.

	The cause is worth stating, because it is general and not about trees. `_arrive` asked
	which way the cursor had gone before fetching the block it had gone to, so the block was
	not in the window, there was nothing to compare it with, and the answer was the fallback:
	forward. Forward is the right guess for deciding which end to fetch from, and the wrong
	one for deciding where to put the window, because it places the block at the edge the
	reader is travelling toward. Placing by a guessed direction is placing by a coin toss
	whenever the cursor leaves the window.
	"""

	def _run(self, count=12, at=0, numRows=4):
		items = fakeRun([f"item {n}" for n in range(count)])
		return items, controllerOver(items, at=at, numRows=numRows)

	def _topBlockText(self, control):
		return control.window.blocks[control.window.blockIndex(control.window.anchor.blockId)].rawText

	def _visibleTexts(self, control):
		texts = []
		for row in control.window.visibleRows():
			if row.blockId is None:
				continue
			rendered = control.window.blocks[control.window.blockIndex(row.blockId)]
			if rendered.rawText not in texts:
				texts.append(rendered.rawText)
		return texts

	def test_movingBackPutsTheItemOnTheTopRow(self):
		"""Two blocks above the top row: a step off the edge, not a jump. A cursor that has
		gone further than a band is answered by placing the window afresh, which is a
		different rule and hides this one."""
		items, control = self._run(at=8)
		control.source.setCurrent(items[6])
		control.followCursor()
		self.assertIn("item 6", self._visibleTexts(control)[0])

	def test_movingBackDoesNotFillTheBandWithWhatCameBefore(self):
		"""The symptom as the reader met it: the whole display had scrolled past them."""
		items, control = self._run(at=8)
		control.source.setCurrent(items[6])
		control.followCursor()
		shown = " ".join(self._visibleTexts(control))
		self.assertNotIn("item 3", shown)
		self.assertNotIn("item 4", shown)

	def test_movingOnStillPutsTheItemOnTheBottomRow(self):
		"""The other half of the rule, which must not be broken to fix the first: reading on
		shows what was come from, so the new block arrives at the bottom."""
		items, control = self._run(at=0)
		control.source.setCurrent(items[5])
		control.followCursor()
		shown = self._visibleTexts(control)
		self.assertIn("item 5", shown[-1])

	def test_anItemAlreadyOnTheBandMovesNothing(self):
		"""Landing on something already under the reader's fingers must not jerk the band."""
		items, control = self._run(at=0)
		before = list(control.cells())
		control.source.setCurrent(items[1])
		control.followCursor()
		self.assertEqual(control.cells(), before)


class TestWhenTheRowChangesAsTheReaderArrivesOnIt(unittest.TestCase):
	"""Arriving on a row changes it, and re-rendering it used to move the whole band.

	From hardware, in Outlook's folder pane: after jumping into an account's folders,
	pressing the up arrow to reach the account put it on the *bottom* row with six other
	accounts filled in above it. A display's worth of movement for one keypress, and it
	survived two fixes to the direction test because the direction test was not what placed
	the window.

	`_arrive` re-renders the block the cursor is in before deciding anything, because a
	cached block can be holding yesterday's cursor position. A run of objects re-renders to
	something *different* on arrival, since an unfocused row carries "not selected" and the
	focused one does not — so `refreshActive` acts, and it ends by asking for the cursor to
	be shown, always forward, for the case it was written for: an edit growing downward as
	it is typed into. It ran first, planted the row at the bottom, and the considered answer
	that followed found the row already visible and let the guess stand.

	The fix is in `FlowWindow.ensureVisible`, which now brings a row on at the edge it is
	nearest rather than the edge a caller guessed at. See `_entryFor`.
	"""

	def _run(self, count=40, at=8, numRows=4):
		items = fakeRun([f"item {n}" for n in range(count)])
		return items, controllerOver(items, at=at, numRows=numRows)

	def _visibleTexts(self, control):
		texts = []
		for row in control.window.visibleRows():
			if row.blockId is None:
				continue
			rendered = control.window.blocks[control.window.blockIndex(row.blockId)]
			if rendered.rawText not in texts:
				texts.append(rendered.rawText)
		return texts

	def _cacheAbove(self, control, rows=4):
		"""Read what is above the window without showing it, as reaching for a block does."""
		for _ in range(rows):
			control._fetchOne(Edge.BEFORE)

	def _arriveAt(self, control, item):
		"""Move to an item whose row reads differently now that the reader is on it."""
		item.name = f"{item.name} arrived"
		control.source.setCurrent(item)
		control.followCursor()

	def test_arrivingOnARowAboveTheBandPutsItOnTheTopRow(self):
		items, control = self._run()
		self._cacheAbove(control)
		self._arriveAt(control, items[5])
		self.assertIn("item 5", self._visibleTexts(control)[0])

	def test_theBandIsNotFilledWithWhatCameBeforeIt(self):
		"""The symptom as the reader met it, rather than the reasoning."""
		items, control = self._run()
		self._cacheAbove(control)
		self._arriveAt(control, items[5])
		shown = " ".join(self._visibleTexts(control))
		self.assertNotIn("item 2", shown)
		self.assertNotIn("item 3", shown)

	def test_theReportNamesTheCallThatMovedTheBand(self):
		"""Three hardware reports have turned on which edge a row arrived at, and the first
		two were read off the direction test — which was answering correctly about a
		placement it had not made. The report has to name the call that made it."""
		items, control = self._run()
		self._cacheAbove(control)
		self._arriveAt(control, items[5])
		self.assertIn("re-render", control.placements[0])
		self.assertIn("brought on at the top", control.placements[0])
		self.assertIn("nothing moved", control.placements[-1])

	def test_arrivingOnARowBelowTheBandStillPutsItOnTheBottomRow(self):
		"""Reading on shows what was come from, and the fix must not cost that."""
		items, control = self._run(at=0)
		for _ in range(4):
			control._fetchOne(Edge.AFTER)
		self._arriveAt(control, items[6])
		self.assertIn("item 6", self._visibleTexts(control)[-1])


class TestWhenTheBlockLeftBehindIsGone(unittest.TestCase):
	"""The cursor's last block is not guaranteed to still be in the window.

	`_trim` keeps the window's list to the band and a margin either side. The block the
	reader has just left can be outside that: it stays in the controller's own cache, which
	is what the commands act through, but it leaves the window's list, which is what the
	direction test reads. The test then had nothing to compare with and answered "forward",
	and a cursor that had gone back was placed at the *bottom* of the band with everything
	before it filled in above.

	On hardware that was moving up to the account row in a folder tree and being given a
	whole display of other accounts. A display's worth of movement for one keypress.
	"""

	def _run(self, count=40, at=0, numRows=4):
		items = fakeRun([f"item {n}" for n in range(count)])
		return items, controllerOver(items, at=at, numRows=numRows)

	def _visibleTexts(self, control):
		texts = []
		for row in control.window.visibleRows():
			if row.blockId is None:
				continue
			rendered = control.window.blocks[control.window.blockIndex(row.blockId)]
			if rendered.rawText not in texts:
				texts.append(rendered.rawText)
		return texts

	def _walkAwayFrom(self, control, items, start, stop):
		"""Move the cursor block by block, so the window trims what is left far behind."""
		for index in range(start, stop):
			control.source.setCurrent(items[index])
			control.followCursor()

	def test_theDirectionIsStillKnownAfterTheOldBlockIsTrimmed(self):
		items, control = self._run(at=0)
		stale = control.activeBlockId
		self._walkAwayFrom(control, items, 1, 20)
		# What the reader started on is long out of the window's list by now, which is the
		# state the direction test used to have no answer for.
		self.assertIsNone(control._windowIndex(stale))
		control.activeBlockId = stale
		control.source.setCurrent(items[14])
		control.followCursor()
		self.assertIn("back", control.lastDirection)
		self.assertIn("anchor", control.lastDirection)

	def test_aTrimmedOldBlockNoLongerScrollsTheBandTheWrongWay(self):
		"""The symptom, rather than the reasoning: the band must not fill with what came
		before the block the reader asked for."""
		items, control = self._run(at=0)
		stale = control.activeBlockId
		self._walkAwayFrom(control, items, 1, 20)
		control.activeBlockId = stale
		control.source.setCurrent(items[14])
		control.followCursor()
		self.assertIn("item 14", self._visibleTexts(control)[0])

	def test_placementIsSaidOutLoudForTheDiagnostics(self):
		"""Which end a block arrives at is invisible to the reader and has now turned two
		hardware reports. It has to be something a report can say."""
		items, control = self._run(at=8)
		control.source.setCurrent(items[6])
		control.followCursor()
		self.assertIn("back", control.lastDirection)

	def test_movingOnStillSaysForward(self):
		items, control = self._run(at=0)
		control.source.setCurrent(items[5])
		control.followCursor()
		self.assertIn("forward", control.lastDirection)

	def test_theAnchorIsUsedWhenTheOldBlockHasGone(self):
		"""The anchor cannot be trimmed away: it is where the window is."""
		items, control = self._run(at=8)
		control.activeBlockId = None
		control.source.setCurrent(items[6])
		control.followCursor()
		self.assertIn("item 6", self._visibleTexts(control)[0])


class TestWhichObjectsAreRead(unittest.TestCase):
	"""A registry, because which controls read well this way is a judgement about controls."""

	def test_aListItemIsARun(self):
		items = fakeRun(["Apple", "Banana"])
		self.assertIs(flowObjects.adapterFor(items[0]), flowObjects.SIBLING_RUN)

	def test_soAreTreeAndMenuItems(self):
		for role in ("TREEVIEWITEM", "MENUITEM", "TAB"):
			with self.subTest(role=role):
				items = fakeRun(["one", "two"], role=role)
				self.assertIsNotNone(flowObjects.adapterFor(items[0]))

	def test_aButtonIsNot(self):
		# One object with no run to show. NVDA presents it well already.
		self.assertIsNone(flowObjects.adapterFor(FakeNavigatorObject("Save", role="BUTTON")))

	def test_anEditFieldIsNot(self):
		# Notepad's edit control, which a flow once followed the caret through, growing a row
		# per keystroke. The narrowness here is what stops that.
		self.assertIsNone(flowObjects.adapterFor(FakeNavigatorObject("a note", role="EDITABLETEXT")))

	def test_nothingIsNot(self):
		self.assertIsNone(flowObjects.adapterFor(None))

	def test_aComboBoxWithChoicesIsReadByItsChildren(self):
		box = FakeNavigatorObject("Country", role="COMBOBOX")
		fakeRun(["France", "Germany"], parent=box, selected=1)
		self.assertIs(flowObjects.adapterFor(box), flowObjects.CHOICES)

	def test_anEmptyComboBoxIsNot(self):
		"""Nothing to show, so NVDA's own presentation of it is the better one."""
		self.assertIsNone(flowObjects.adapterFor(FakeNavigatorObject("Country", role="COMBOBOX")))

	def test_theReaderStartsAtTheChosenChoice(self):
		box = FakeNavigatorObject("Country", role="COMBOBOX")
		items = fakeRun(["France", "Germany", "Spain"], parent=box, selected=1)
		self.assertIs(flowObjects.CHOICES.start(box, None), items[1])

	def test_aContainerThatSaysNothingStartsAtItsFirst(self):
		box = FakeNavigatorObject("Country", role="LISTBOX")
		items = fakeRun(["France", "Germany"], parent=box, selected=-1)
		self.assertIs(flowObjects.CHOICES.start(box, None), items[0])

	def test_onceTheFocusIsOnAChoiceThatIsWhereTheReaderIs(self):
		# The focus moves to the choice itself the moment the reader arrows in an open combo
		# box. Asking the *choice* for its own children finds nothing, which lost the run.
		box = FakeNavigatorObject("Country", role="COMBOBOX")
		items = fakeRun(["France", "Germany", "Spain"], parent=box, selected=0)
		self.assertIs(flowObjects.CHOICES.start(box, items[2]), items[2])

	def test_aChoiceOfSomeOtherContainerIsNotWhereTheReaderIs(self):
		box = FakeNavigatorObject("Country", role="COMBOBOX")
		items = fakeRun(["France", "Germany"], parent=box, selected=0)
		stranger = fakeRun(["Somewhere else"])[0]
		self.assertIs(flowObjects.CHOICES.start(box, stranger), items[0])


class TestRegistering(unittest.TestCase):
	def setUp(self):
		self.adapter = flowObjects.ObjectAdapter(
			name="test",
			matches=lambda obj: getattr(obj, "name", "") == "special",
		)
		self.addCleanup(flowObjects.unregister, self.adapter)

	def test_anAddedAdapterIsAsked(self):
		flowObjects.register(self.adapter)
		self.assertIs(flowObjects.adapterFor(FakeNavigatorObject("special")), self.adapter)

	def test_itIsAskedBeforeTheBuiltInOnes(self):
		"""An add-on that owns a control knows better than a guess about every list item."""
		flowObjects.register(self.adapter)
		item = fakeRun(["special"])[0]
		self.assertIs(flowObjects.adapterFor(item), self.adapter)

	def test_takingItBackLeavesTheBuiltInAnswer(self):
		flowObjects.register(self.adapter)
		flowObjects.unregister(self.adapter)
		self.assertIsNone(flowObjects.adapterFor(FakeNavigatorObject("special")))

	def test_anAdapterThatRaisesIsSkipped(self):
		def difficult(obj):
			raise RuntimeError("cannot judge")

		broken = flowObjects.ObjectAdapter(name="broken", matches=difficult)
		flowObjects.register(broken)
		self.addCleanup(flowObjects.unregister, broken)
		self.assertIs(flowObjects.adapterFor(fakeRun(["Apple"])[0]), flowObjects.SIBLING_RUN)


class TestReadingTheRun(unittest.TestCase):
	def test_theBlockAtTheCursorIsTheObjectTheReaderIsOn(self):
		items = fakeRun(["Apple", "Banana", "Cherry"])
		block = sourceOver(items, at=1).blockAtCursor().block
		self.assertEqual(block.region.rawText, "Banana LISTITEM")

	def test_aBlockSaysWhatNVDASaysAboutTheObject(self):
		# Name and role, from NVDA's own region. Nothing here re-derives any of it.
		items = fakeRun(["Apple"])
		block = sourceOver(items).blockAtCursor().block
		self.assertIn("Apple", block.region.rawText)
		self.assertIn("LISTITEM", block.region.rawText)

	def test_walkingOnAndBack(self):
		items = fakeRun(["Apple", "Banana", "Cherry"])
		source = sourceOver(items, at=1)
		here = source.blockAtCursor().block
		self.assertEqual(source.blockAfter(here.blockId).block.region.rawText, "Cherry LISTITEM")
		self.assertEqual(source.blockBefore(here.blockId).block.region.rawText, "Apple LISTITEM")

	def test_theEndOfTheRunIsTheEndOfTheStream(self):
		# Not an error and not a budget stop: the display shows blank rows below, which is
		# what the end of a list should feel like.
		items = fakeRun(["Apple", "Banana"])
		source = sourceOver(items, at=1)
		here = source.blockAtCursor().block
		self.assertIs(source.blockAfter(here.blockId).kind, ResultKind.END_OF_STREAM)

	def test_theStartOfTheRunLikewise(self):
		items = fakeRun(["Apple", "Banana"])
		source = sourceOver(items, at=0)
		here = source.blockAtCursor().block
		self.assertIs(source.blockBefore(here.blockId).kind, ResultKind.END_OF_STREAM)

	def test_aBlockFromSomeOtherRunReachesNothing(self):
		# A block carries its own object, so there is no cache to miss. What there is to get
		# wrong is walking on from a block that was never this run's, and pulling another
		# list's items onto the display.
		source = sourceOver(fakeRun(["Apple"]))
		strangers = fakeRun(["Elsewhere", "And further"])
		from brlMultiline.flow import BlockId

		result = source.blockAfter(BlockId(generation=1, bookmark=strangers[0], unit="object"))
		self.assertIs(result.kind, ResultKind.END_OF_STREAM)

	def test_anObjectlessBlockIsAnErrorNotACrash(self):
		from brlMultiline.flow import BlockId

		source = sourceOver(fakeRun(["Apple"]))
		result = source.blockAfter(BlockId(generation=1, bookmark=None, unit="object"))
		self.assertIs(result.kind, ResultKind.ERROR)

	def test_nothingIsWalkedUntilItIsAskedFor(self):
		"""Each step is a call into the application, so nothing reads ahead."""
		walked = []

		def counting(obj):
			walked.append(obj)
			return getattr(obj, "next", None)

		items = fakeRun(["Apple", "Banana", "Cherry", "Date"])
		adapter = flowObjects.ObjectAdapter(
			name="counting",
			matches=lambda obj: True,
			nextOf=counting,
		)
		source = flowObjects.ObjectFlowSource(
			items[0],
			adapter,
			flowObjects.regionFactory(),
			generation=1,
		)
		here = source.blockAtCursor().block
		self.assertEqual(walked, [])
		source.blockAfter(here.blockId)
		self.assertEqual(len(walked), 1)

	def test_aRunThatCannotBeWalkedIsAnErrorNotACrash(self):
		def difficult(obj):
			raise RuntimeError("the application went away")

		items = fakeRun(["Apple"])
		adapter = flowObjects.ObjectAdapter(name="broken", matches=lambda obj: True, nextOf=difficult)
		source = flowObjects.ObjectFlowSource(
			items[0],
			adapter,
			flowObjects.regionFactory(),
			generation=1,
		)
		here = source.blockAtCursor().block
		self.assertIs(source.blockAfter(here.blockId).kind, ResultKind.ERROR)

	def test_howLongEachStepTookIsRecorded(self):
		"""The number the cost work is measured against, kept from the first reading."""
		budget = FetchBudget(clock=iter([0.0, 0.5, 1.0, 1.5]).__next__)
		items = fakeRun(["Apple", "Banana"])
		source = sourceOver(items, budget=budget)
		source.blockAtCursor()
		self.assertGreater(budget.slowest, 0)


class TestTheFocusedObject(unittest.TestCase):
	"""Which of the eight items on the display the arrow keys will act on."""

	def _region(self, live=True, active=True):
		items = fakeRun(["Apple", "Banana"])
		block = sourceOver(items, live=live).blockAtCursor().block
		block.region.isActive = active
		block.region.update()
		return block.region

	def test_theActiveBlockShowsACursor(self):
		self.assertEqual(self._region().brailleCursorPos, 0)

	def test_theOthersDoNot(self):
		# One cursor on the display, and a run of eight equal things would tell the reader
		# nothing about where they are.
		self.assertIsNone(self._region(active=False).brailleCursorPos)

	def test_aBandTheReaderIsNotWorkingInShowsNone(self):
		self.assertIsNone(self._region(live=False).brailleCursorPos)


class TestRoutingMovesNothingByAccident(unittest.TestCase):
	"""A finger landing on an item the reader was reading past must not activate it."""

	def _blocks(self):
		items = fakeRun(["Apple", "Banana"])
		source = sourceOver(items)
		here = source.blockAtCursor().block
		there = source.blockAfter(here.blockId).block
		here.region.isActive = True
		return here, there, items

	def test_routingWhereTheReaderAlreadyIsActs(self):
		here, _there, _items = self._blocks()
		here.region.routeTo(0)
		self.assertTrue(here.region.acted)

	def test_routingElsewhereDoesNotAct(self):
		_here, there, _items = self._blocks()
		there.region.routeTo(0)
		self.assertFalse(there.region.acted)

	def test_routingElsewhereMovesTheFocusInstead(self):
		"""Which is what going to an item means, and is NVDA's own precedent in review."""
		_here, there, items = self._blocks()
		items[1].isFocusable = True
		items[1].hasFocus = False
		items[1].focused = False
		there.region.routeTo(0)
		self.assertTrue(getattr(items[1], "focused", False))

	def test_anObjectThatCannotTakeFocusIsLeftAlone(self):
		_here, there, _items = self._blocks()
		there.region.routeTo(0)
		self.assertFalse(there.region.acted)


class TestTheLineCommands(unittest.TestCase):
	"""They must reach something, and what they reach must not move the reader.

	`script_braille_nextLine` calls `regions[-1].nextLine()` with no check beyond the list
	being non-empty. A region without that method raises where the reader pressed a key, and
	an object region has none of its own — which is a fault waiting on any display whose
	line command is bound, and the reason these exist at all.
	"""

	def _region(self):
		items = fakeRun(["Apple", "Banana"])
		block = sourceOver(items).blockAtCursor().block
		return block.region

	def test_theyExistAtAll(self):
		region = self._region()
		self.assertTrue(callable(getattr(region, "nextLine", None)))
		self.assertTrue(callable(getattr(region, "previousLine", None)))

	def test_theyAskForTheWindowToMove(self):
		region = self._region()
		asked = []
		region.onLine = asked.append
		region.nextLine()
		region.previousLine()
		self.assertEqual(asked, [True, False])

	def test_theyDoNothingWhenNobodyIsListening(self):
		"""Which is a flow that has been detached, and is not a reason to raise."""
		region = self._region()
		region.nextLine()

	def test_aListenerThatRaisesIsNotTheReadersProblem(self):
		region = self._region()

		def difficult(forward):
			raise RuntimeError("no")

		region.onLine = difficult
		region.nextLine()


class TestStayingInTheSameRun(unittest.TestCase):
	"""In a list the focus changes on every arrow key, so this decides what gets rebuilt."""

	def test_anObjectAlreadyReadBelongs(self):
		items = fakeRun(["Apple", "Banana"])
		source = sourceOver(items)
		source.blockAtCursor()
		self.assertTrue(flowObjects.belongsTo(source, items[0]))

	def test_aSiblingBelongsBeforeItIsRead(self):
		# Otherwise arrowing past the end of what is cached throws the run away and walks it
		# again, which here is a call into the application per item passed.
		box = FakeNavigatorObject("Fruit", role="LISTBOX")
		items = fakeRun(["Apple", "Banana", "Cherry"], parent=box)
		source = sourceOver(items)
		source.blockAtCursor()
		self.assertTrue(flowObjects.belongsTo(source, items[2]))

	def test_aChoiceOfTheContainerBelongs(self):
		box = FakeNavigatorObject("Country", role="COMBOBOX")
		items = fakeRun(["France", "Germany"], parent=box)
		source = flowObjects.ObjectFlowSource(
			box,
			flowObjects.CHOICES,
			flowObjects.regionFactory(),
			generation=1,
		)
		self.assertTrue(flowObjects.belongsTo(source, items[1]))

	def test_somethingElseEntirelyDoesNot(self):
		items = fakeRun(["Apple"], parent=FakeNavigatorObject("Fruit", role="LISTBOX"))
		source = sourceOver(items)
		other = fakeRun(["Elsewhere"], parent=FakeNavigatorObject("Other", role="LISTBOX"))[0]
		self.assertFalse(flowObjects.belongsTo(source, other))

	def test_nothingDoesNot(self):
		self.assertFalse(flowObjects.belongsTo(sourceOver(fakeRun(["Apple"])), None))


class TestADocumentWins(unittest.TestCase):
	"""A list inside a web page is part of that page, and the page has the better context."""

	def test_aListItemInsideAPageIsNotReadAsARun(self):
		from brlMultiline.flowBuild import objectAdapterFor

		interceptor = FakeTreeInterceptor(["a page"])
		item = FakeNavigatorObject("Apple", role="LISTITEM", treeInterceptor=interceptor)
		self.assertIsNone(objectAdapterFor(item))

	def test_aListItemWithNoPageBehindItIs(self):
		from brlMultiline.flowBuild import objectAdapterFor

		self.assertIsNotNone(objectAdapterFor(fakeRun(["Apple"])[0]))

	def test_aBrowseModeDocumentItselfIsNot(self):
		from brlMultiline.flowBuild import objectAdapterFor

		self.assertIsNone(objectAdapterFor(FakeTreeInterceptor(["a page"])))


class TestWhatTheRunAdmits(unittest.TestCase):
	"""Sharing a parent is not enough: a list box holds a button, a menu holds its lines."""

	def test_aButtonBesideAListIsNotOneOfItsItems(self):
		items = fakeRun(["Apple", "Banana"])
		button = FakeNavigatorObject("Clear", role="BUTTON")
		button.parent = items[0].parent
		self.assertFalse(flowObjects.belongsTo(sourceOver(items), button))

	def test_soTheRunEndsAtIt(self):
		items = fakeRun(["Apple", "Banana"])
		button = FakeNavigatorObject("Clear", role="BUTTON")
		button.parent = items[1].parent
		button.previous = items[1]
		items[1].next = button
		source = sourceOver(items, at=1)
		here = source.blockAtCursor().block
		self.assertIs(source.blockAfter(here.blockId).kind, ResultKind.END_OF_STREAM)

	def test_aCheckItemAndACommandAreOneMenu(self):
		items = fakeRun(["Open", "Close"], role="MENUITEM")
		checkable = FakeNavigatorObject("Word wrap", role="CHECKMENUITEM")
		checkable.parent = items[0].parent
		self.assertTrue(flowObjects.belongsTo(sourceOver(items), checkable))

	def test_aTabBesideAListItemIsNot(self):
		items = fakeRun(["Apple"], role="LISTITEM")
		tab = FakeNavigatorObject("General", role="TAB")
		tab.parent = items[0].parent
		self.assertFalse(flowObjects.belongsTo(sourceOver(items), tab))

	def test_anItemOfAnotherListIsNot(self):
		items = fakeRun(["Apple"])
		self.assertFalse(flowObjects.belongsTo(sourceOver(items), fakeRun(["Elsewhere"])[0]))

	def test_theChoiceIsFoundWithoutBuildingTheWholeChildList(self):
		# NVDA's own `children` walks every child before anything can count them, so a limit
		# applied to the list bounds this module's loop rather than the work.
		class Careful(FakeNavigatorObject):
			@property
			def children(self):
				raise AssertionError("the whole child list was built")

			@children.setter
			def children(self, value):
				pass

		box = Careful("Country", role="COMBOBOX")
		items = fakeRun(["France", "Germany", "Spain"], parent=box, selected=2)
		self.assertIs(flowObjects.CHOICES.start(box, None), items[2])


class TestASeparatorIsALineNotACommand(unittest.TestCase):
	"""The hardware read one out as "unavailable" followed by its dashes.

	True and useless: the reader was told a command was disabled where the menu's author had
	drawn a line between two groups. Disabled is never the test on its own — a command that
	is temporarily unavailable is content, and saying so is the whole point of the word.
	"""

	def _menu(self):
		items = fakeRun(["Open", "Close"], role="MENUITEM")
		return items, fakeSeparator(items[0])

	def test_itIsRecognisedByItsRole(self):
		self.assertTrue(flowObjects.isDecoration(FakeNavigatorObject("-----", role="SEPARATOR")))

	def test_aToolkitCallingItADisabledMenuItemIsRecognisedToo(self):
		self.assertTrue(flowObjects.isDecoration(FakeNavigatorObject("------", role="MENUITEM")))

	def test_howeverItDrawsItsLine(self):
		for name in ("───", "——", "____", "~~~~"):
			with self.subTest(name=name):
				self.assertTrue(flowObjects.isDecoration(FakeNavigatorObject(name, role="MENUITEM")))

	def test_aRealDisabledCommandIsNot(self):
		self.assertFalse(flowObjects.isDecoration(FakeNavigatorObject("Paste", role="MENUITEM")))

	def test_norIsOneTheReaderCanReach(self):
		line = FakeNavigatorObject("------", role="MENUITEM")
		line.isFocusable = True
		self.assertFalse(flowObjects.isDecoration(line))

	def test_norIsOneWithSomethingToDo(self):
		line = FakeNavigatorObject("------", role="MENUITEM")
		line.actionCount = 1
		self.assertFalse(flowObjects.isDecoration(line))

	def test_itReadsAsABlankRowRatherThanAsAnObject(self):
		items, _line = self._menu()
		source = sourceOver(items)
		here = source.blockAtCursor().block
		block = source.blockAfter(here.blockId).block
		self.assertTrue(block.isDecoration)
		self.assertTrue(block.isBlank)
		self.assertEqual(block.region.rawText, "")

	def test_routingOnItDoesNothing(self):
		items, _line = self._menu()
		control = controllerOver(items)
		self.assertFalse(control.routeTo(NUM_COLS))

	def test_itIsDrawnAsALineOfDotsSevenAndEight(self):
		# The reader asked for the line rather than the blank row this started as: braille
		# readers like decoration too, and the grouping is worth feeling rather than merely
		# not being lied to about.
		items, _line = self._menu()
		control = controllerOver(items)
		self.assertEqual(control.cells()[NUM_COLS : NUM_COLS * 2], [0xC0] * NUM_COLS)

	def test_theMenuCarriesOnPastIt(self):
		# The groups either side of a line are one menu. Ending the run at it would hide half
		# of what the reader opened.
		items, _line = self._menu()
		control = controllerOver(items)
		names = [
			getattr(objectAt(control, row.blockId), "name", None) for row in control.window.visibleRows()
		]
		self.assertEqual(names[:3], ["Open", "-----", "Close"])

	def test_walkingBackCrossesItToo(self):
		items, line = self._menu()
		source = sourceOver(items, at=1)
		here = source.blockAtCursor().block
		back = source.blockBefore(here.blockId).block
		self.assertIs(back.blockId.bookmark, line)
		self.assertIs(source.blockBefore(back.blockId).block.blockId.bookmark, items[0])

	def test_itCostsABlockLikeAnyOtherStep(self):
		# Skipped silently, a menu of separators would be walked further than the budget was
		# ever asked to allow.
		# Two fetches after the item entered at: the separator, then the item beyond it. A
		# separator skipped for free would leave the run finished and nothing refused.
		items, _line = self._menu()
		budget = FetchBudget(maxBlocks=2)
		control = controllerOver(items, budget=budget, numRows=4)
		self.assertEqual(len(control.window.blocks), 3)
		self.assertGreaterEqual(budget.stops, 1)


class TestPanningARunMovesNothingButTheWindow(unittest.TestCase):
	"""The reading position here is a selection, so panning past it is reading, not moving.

	The hardware found the second pan bouncing back to the item the reader had started on.
	It was the first pan that crossed the end of what had been fetched: the new blocks came
	back freshly read, the flow took its own reading for a move the reader had made, and
	followed the focus — which was of course still where they had left it.
	"""

	def _items(self, count=12):
		return fakeRun([f"item {number}" for number in range(count)])

	def test_theWindowMovesOn(self):
		items = self._items()
		control = controllerOver(items, numRows=4)
		self.assertTrue(control.panForward())
		self.assertIs(objectAt(control, control.window.topBlockId()), items[4])

	def test_theReadersPlaceStaysWhereTheyLeftIt(self):
		items = self._items()
		control = controllerOver(items, numRows=4)
		control.panForward()
		self.assertIs(objectAt(control, control.activeBlockId), items[0])

	def test_aSecondPanKeepsGoing(self):
		items = self._items()
		control = controllerOver(items, numRows=4)
		control.panForward()
		control.panForward()
		self.assertIs(objectAt(control, control.window.topBlockId()), items[8])
		self.assertIs(objectAt(control, control.activeBlockId), items[0])

	def test_andPanningBackReturnsThem(self):
		items = self._items()
		control = controllerOver(items, numRows=4)
		control.panForward()
		control.panForward()
		control.panBack()
		self.assertIs(objectAt(control, control.window.topBlockId()), items[4])
		self.assertIs(objectAt(control, control.activeBlockId), items[0])

	def test_aDocumentStillTakesItsCursorWithIt(self):
		"""The rule is about what a run's reading position is, not about panning."""
		from brlMultiline.flowSources import DocumentFlowSource, regionFactoryFor

		from ._stubs import CursorManagerRegion

		interceptor = FakeTreeInterceptor([f"line {number}" for number in range(12)])
		source = DocumentFlowSource(
			interceptor,
			regionFactoryFor(CursorManagerRegion(interceptor), live=False),
			generation=1,
		)
		control = FlowController(
			source,
			FlowRenderer(FakeHandler(), numCols=NUM_COLS, fillRows=True),
			numRows=4,
		)
		control.enterAtCursor()
		control.panForward()
		self.assertEqual(control.activeBlockId, control.window.topBlockId())


class TestRoutingThroughTheBand(unittest.TestCase):
	"""The controller's own ordering, which testing the region alone cannot reach.

	Making the block active before telling the region told it the reader had been on that
	object all along, so a press meant to reach the second item of a list activated it.
	"""

	def _items(self, role="LISTITEM"):
		items = fakeRun(["Apple", "Banana", "Cherry"], role=role)
		for item in items:
			item.isFocusable = True
		return items

	def test_aPressElsewhereMovesTheFocus(self):
		items = self._items()
		control = controllerOver(items)
		self.assertTrue(control.routeTo(NUM_COLS))
		self.assertTrue(items[1].focused)

	def test_andDoesNotActOnIt(self):
		items = self._items()
		control = controllerOver(items)
		control.routeTo(NUM_COLS)
		self.assertFalse(control.regionFor(control.window.blocks[1].blockId).acted)

	def test_norClaimsTheReaderHasMoved(self):
		# The focus event that follows says that, and until it arrives the cursor belongs
		# where the reader left it.
		items = self._items()
		control = controllerOver(items)
		control.routeTo(NUM_COLS)
		self.assertIs(objectAt(control, control.activeBlockId), items[0])

	def test_aPressWhereTheReaderAlreadyIsActs(self):
		items = self._items()
		control = controllerOver(items)
		control.routeTo(0)
		self.assertTrue(control.regionFor(control.activeBlockId).acted)

	def test_aMenuItemIsInvokedRatherThanMerelyReached(self):
		# A routing key is a click, and clicking a menu item runs it. Taking the focus
		# instead put the menu highlight somewhere and did nothing the reader could see.
		items = self._items(role="MENUITEM")
		control = controllerOver(items)
		control.routeTo(NUM_COLS)
		self.assertTrue(control.regionFor(control.window.blocks[1].blockId).acted)

	def test_aListItemIsStillOnlyReached(self):
		"""Clicking a list item selects it and waits, which is the difference."""
		items = self._items()
		control = controllerOver(items)
		control.routeTo(NUM_COLS)
		self.assertFalse(control.regionFor(control.window.blocks[1].blockId).acted)

	def test_aTabIsChosenRatherThanMerelyReached(self):
		# Going to a tab is choosing it: one focused but not chosen shows nothing new, and a
		# routing key is the braille equivalent of clicking what is under your finger.
		tabs = self._items(role="TAB")
		control = controllerOver(tabs)
		control.routeTo(NUM_COLS)
		self.assertTrue(tabs[1].focused)
		self.assertTrue(control.regionFor(control.window.blocks[1].blockId).acted)


if __name__ == "__main__":
	unittest.main()
