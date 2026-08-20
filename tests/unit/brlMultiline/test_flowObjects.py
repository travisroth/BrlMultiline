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
from brlMultiline.flow import ResultKind  # noqa: E402
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


def controllerOver(items, at=0, numRows=4, adapter=None, budget=None) -> FlowController:
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
	)
	control.enterAtCursor()
	return control


def objectAt(control: FlowController, blockId):
	""":return: the object a block of a run was built for."""
	return getattr(control.regionFor(blockId), "obj", None)


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
		from brlMultiline.flowDryRun import objectAdapterFor

		interceptor = FakeTreeInterceptor(["a page"])
		item = FakeNavigatorObject("Apple", role="LISTITEM", treeInterceptor=interceptor)
		self.assertIsNone(objectAdapterFor(item))

	def test_aListItemWithNoPageBehindItIs(self):
		from brlMultiline.flowDryRun import objectAdapterFor

		self.assertIsNotNone(objectAdapterFor(fakeRun(["Apple"])[0]))

	def test_aBrowseModeDocumentItselfIsNot(self):
		from brlMultiline.flowDryRun import objectAdapterFor

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
