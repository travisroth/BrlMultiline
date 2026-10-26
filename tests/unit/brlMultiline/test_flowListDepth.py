# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for how deep in a document's lists a browse mode line sits.

Three layers, each tested where it is: the count over a field list (`flowListDepth`), the
region noting it as NVDA reads the line (`FlowRegion._addTextWithFields`), and the source
putting it on the block (`DocumentFlowSource._buildBlock`), which is all the renderer needs to
draw the indent it already draws for a tree.
"""

import types
import unittest

from ._stubs import (
	ControlField,
	CursorManagerRegion,
	FakeTreeInterceptor,
	FieldCommand,
	installStubs,
)

installStubs()

from brlMultiline import flowListDepth  # noqa: E402
from brlMultiline.flowSources import DocumentFlowSource, FlowRegion  # noqa: E402


def start(role, states=()) -> FieldCommand:
	return FieldCommand("controlStart", ControlField(role=role, states=tuple(states)))


def end() -> FieldCommand:
	return FieldCommand("controlEnd")


def nested(lists: int, text: str = "item") -> list:
	""":return: the commands a virtual buffer gives for a line inside this many lists."""
	commands = [start("DOCUMENT")]
	for _ in range(lists):
		commands += [start("LIST"), start("LISTITEM")]
	return commands + [text]


def item(startOfNode) -> FieldCommand:
	""":return: a list item's start, saying whether the range begins where the item does."""
	return FieldCommand("controlStart", ControlField(role="LISTITEM", _startOfNode=startOfNode))


class TestAnItemCarriedOn(unittest.TestCase):
	"""A long item is several browse mode lines; the later ones carry it on."""

	def place(self, *commands):
		return flowListDepth.placeBeforeText(list(commands))

	def test_theLineAnItemBeginsOnDoesNotCarryItOn(self):
		place = self.place(start("DOCUMENT"), start("LIST"), item(True), "first words")
		self.assertEqual(place, flowListDepth.ListPlace(levels=1, continues=False))

	def test_aLaterLineOfTheItemCarriesItOn(self):
		place = self.place(start("DOCUMENT"), start("LIST"), item(False), "more words")
		self.assertEqual(place, flowListDepth.ListPlace(levels=1, continues=True))

	def test_theInnermostItemDecides(self):
		# The outer item began long ago; the sub item begins here.
		place = self.place(start("LIST"), item(False), start("LIST"), item(True), "sub item")
		self.assertEqual(place, flowListDepth.ListPlace(levels=2, continues=False))

	def test_anItemThatDoesNotSayIsItsOwnStart(self):
		place = self.place(start("LIST"), start("LISTITEM"), "x")
		self.assertFalse(place.continues)

	def test_proseCarriesNothingOn(self):
		self.assertFalse(self.place(start("PARAGRAPH"), "words").continues)

	def test_theOptionsOfAListBoxCarryNothingOn(self):
		commands = [start("LIST"), item(True), start("LIST", states=["FOCUSABLE"]), item(False), "option"]
		self.assertFalse(self.place(*commands).continues)

	def test_wordsShapeToo(self):
		# Word gives items and no list, and marks the start of each the same way.
		top = [start("EDITABLETEXT"), item(False)]
		self.assertEqual(self.place(*top, "rest of a long item"), flowListDepth.ListPlace(1, True))


class TestCounting(unittest.TestCase):
	def test_proseIsInNoList(self):
		self.assertEqual(flowListDepth.depthBeforeText([start("DOCUMENT"), start("PARAGRAPH"), "words"]), 0)

	def test_eachEnclosingListIsALevel(self):
		for lists in (1, 2, 4):
			with self.subTest(lists=lists):
				self.assertEqual(flowListDepth.depthBeforeText(nested(lists)), lists)

	def test_aRangeWithNoTextSaysNothing(self):
		# Not 0. The collapsed part before a cursor at the start of the line has no commands,
		# and the part after it is the one that knows.
		self.assertIsNone(flowListDepth.depthBeforeText([]))
		self.assertIsNone(flowListDepth.depthBeforeText([start("LIST"), start("LISTITEM"), ""]))

	def test_theDepthIsWhereTheTextBegins(self):
		# A line that closes its sublist after its own words still belongs to the sublist.
		commands = nested(2, "deep") + [end(), end(), "after"]
		self.assertEqual(flowListDepth.depthBeforeText(commands), 2)

	def test_closedListsBeforeTheTextDoNotCount(self):
		commands = [start("DOCUMENT"), start("LIST"), start("LISTITEM"), end(), end(), "out"]
		self.assertEqual(flowListDepth.depthBeforeText(commands), 0)

	def test_aFocusableListIsAControlNotALevel(self):
		# A list box inside a list item: the line is in one list, not two.
		commands = nested(1, "") + [start("LIST", states=["FOCUSABLE"]), "option"]
		self.assertEqual(flowListDepth.depthBeforeText(commands), 1)

	def test_wordGivesItemsAndNoList(self):
		# The fields Word's UIA document gave on hardware, from the log of the Word session.
		top = [start("EDITABLETEXT"), start("EDITABLETEXT"), start("EDITABLETEXT"), start("LISTITEM")]
		self.assertEqual(flowListDepth.depthBeforeText(top + [start("LINK"), "Home and shared"]), 1)
		self.assertEqual(flowListDepth.depthBeforeText(top + [start("LISTITEM"), "Header, navigation"]), 2)

	def test_wordProseIsInNoList(self):
		commands = [start("EDITABLETEXT"), start("EDITABLETEXT"), "reviewed October 8"]
		self.assertEqual(flowListDepth.depthBeforeText(commands), 0)

	def test_aBrowserListIsNotCountedTwice(self):
		# A list and an item a level: either count is the depth, and the two are not added.
		self.assertEqual(flowListDepth.depthBeforeText(nested(3)), 3)

	def test_anItemWithNoListIsStillALevel(self):
		self.assertEqual(flowListDepth.depthBeforeText([start("DOCUMENT"), start("LISTITEM"), "x"]), 1)

	def test_theOptionsOfAFocusableListAreNotLevels(self):
		commands = nested(1, "") + [start("LIST", states=["FOCUSABLE"]), start("LISTITEM"), "option"]
		self.assertEqual(flowListDepth.depthBeforeText(commands), 1)

	def test_rolesAreComparedByName(self):
		# What NVDA's `controlTypes.Role` members look like: an enumeration with a name.
		role = types.SimpleNamespace(name="LIST")
		state = types.SimpleNamespace(name="FOCUSABLE")
		self.assertEqual(flowListDepth.depthBeforeText([start(role), "x"]), 1)
		self.assertEqual(flowListDepth.depthBeforeText([start(role, [state]), "x"]), 0)

	def test_formatChangesAreIgnored(self):
		commands = [start("LIST"), FieldCommand("formatChange", _formatField()), "x"]
		self.assertEqual(flowListDepth.depthBeforeText(commands), 1)

	def test_noListIsNoDepth(self):
		self.assertIsNone(flowListDepth.blockDepth(0))
		self.assertIsNone(flowListDepth.blockDepth(None))
		self.assertEqual(flowListDepth.blockDepth(3), 3)


def _formatField():
	from ._stubs import FormatField

	return FormatField(bold=True)


class FieldedInfo:
	"""One part of a line, answering `getTextWithFields` the way a virtual buffer does."""

	def __init__(self, commands):
		self.commands = commands
		self.fetches = 0

	def getTextWithFields(self, formatConfig=None):
		self.fetches += 1
		return list(self.commands)


class ReadsInParts(CursorManagerRegion):
	"""A region that reads its line in parts through `_addTextWithFields`, as NVDA's does.

	The stub's own `update` reads the line in one go and never asks for fields, so it could not
	show the tap working, or the first part of a line being empty.
	"""

	parts: list = []

	def _addTextWithFields(self, info, formatConfig, isSelection=False):
		self.seen = getattr(self, "seen", []) + [info.getTextWithFields(formatConfig=formatConfig)]

	def update(self):
		super().update()
		for part in self.parts:
			self._addTextWithFields(part, {})


class Tapped(FlowRegion, ReadsInParts):
	pass


def tapped(*parts) -> Tapped:
	region = Tapped(FakeTreeInterceptor(["item"]))
	region.parts = list(parts)
	return region


class TestTheRegionNotesItsDepth(unittest.TestCase):
	def test_theDepthIsReadFromTheFieldsNVDAFetched(self):
		part = FieldedInfo(nested(2))
		region = tapped(part)
		region.update()
		self.assertEqual(region.listDepth, 2)
		# Once: the depth rides on NVDA's own fetch rather than making another.
		self.assertEqual(part.fetches, 1)

	def test_whatNVDAReadsIsUnchanged(self):
		region = tapped(FieldedInfo(nested(1, "words")))
		region.update()
		self.assertEqual(region.seen[0][-1], "words")

	def test_anEmptyFirstPartLeavesItToTheNext(self):
		region = tapped(FieldedInfo([]), FieldedInfo(nested(3)))
		region.update()
		self.assertEqual(region.listDepth, 3)

	def test_onlyTheFirstPartWithTextDecides(self):
		region = tapped(FieldedInfo(nested(1)), FieldedInfo(nested(4)))
		region.update()
		self.assertEqual(region.listDepth, 1)

	def test_thePositionIsGivenBackItsOwnFetch(self):
		part = FieldedInfo(nested(1))
		tapped(part).update()
		self.assertNotIn("getTextWithFields", vars(part))

	def test_aReadingForgetsTheLastDepth(self):
		region = tapped(FieldedInfo(nested(2)))
		region.update()
		region.parts = [FieldedInfo([start("PARAGRAPH"), "prose"])]
		region.update()
		self.assertIsNone(region.listDepth)

	def test_aFailingCountStillReadsTheLine(self):
		region = tapped(FieldedInfo([start("LIST"), "x"]))
		original = flowListDepth.placeBeforeText
		flowListDepth.placeBeforeText = lambda commands: 1 / 0
		try:
			region.update()
		finally:
			flowListDepth.placeBeforeText = original
		self.assertIsNone(region.listDepth)
		self.assertEqual(region.seen[0][-1], "x")


class ListDocument(FakeTreeInterceptor):
	"""A browse mode document whose lines sit inside a given number of lists."""

	def __init__(self, lines, lists, caretIndex=0):
		super().__init__(lines, caretIndex=caretIndex)
		self.lists = lists


class ReadsItsLine(Tapped):
	"""A flow region whose one part is the line its position is on, with that line's fields."""

	def update(self):
		info = self._getSelection()
		self.parts = [FieldedInfo(nested(self.obj.lists[info.index], self.obj.lines[info.index]))]
		super().update()


def sourceOver(lines, lists, caretIndex=0) -> DocumentFlowSource:
	document = ListDocument(lines, lists, caretIndex=caretIndex)
	return DocumentFlowSource(document, lambda obj, info: ReadsItsLine(obj, info), generation=1)


class TestBlocksCarryTheirDepth(unittest.TestCase):
	def test_eachLineOfANestedListHasItsDepth(self):
		source = sourceOver(["intro", "one", "one a", "one a i", "two"], [0, 1, 2, 3, 1])
		block = source.blockAtCursor().block
		depths = [block.depth]
		for _ in range(4):
			block = source.blockAfter(block.blockId).block
			depths.append(block.depth)
		self.assertEqual(depths, [None, 1, 2, 3, 1])

	def test_aBlankLineInAListHasNoDepth(self):
		source = sourceOver(["one", ""], [1, 2])
		first = source.blockAtCursor().block
		blank = source.blockAfter(first.blockId).block
		self.assertTrue(blank.isBlank)
		self.assertIsNone(blank.depth)


class CarriedDocument(ListDocument):
	"""A document whose lines may carry on the list item of the line before."""

	def __init__(self, lines, lists, carried, caretIndex=0):
		super().__init__(lines, lists, caretIndex=caretIndex)
		self.carried = carried


class ReadsACarriedLine(ReadsItsLine):
	def update(self):
		info = self._getSelection()
		document = self.obj
		commands = [start("DOCUMENT")]
		for level in range(document.lists[info.index]):
			innermost = level == document.lists[info.index] - 1
			commands += [start("LIST"), item(not (innermost and document.carried[info.index]))]
		self.parts = [FieldedInfo(commands + [document.lines[info.index]])]
		Tapped.update(self)


class TestBlocksSayTheyCarryAnItemOn(unittest.TestCase):
	def blocks(self):
		document = CarriedDocument(["one starts", "one goes on", "two"], [1, 1, 1], [False, True, False])
		source = DocumentFlowSource(document, lambda obj, info: ReadsACarriedLine(obj, info), generation=1)
		block = source.blockAtCursor().block
		found = [block]
		for _ in range(2):
			block = source.blockAfter(block.blockId).block
			found.append(block)
		return found

	def test_onlyTheLaterLineOfAnItemCarriesItOn(self):
		self.assertEqual([block.continuesItem for block in self.blocks()], [False, True, False])

	def test_itKeepsItsItemsDepth(self):
		self.assertEqual([block.depth for block in self.blocks()], [1, 1, 1])


class TestTheBandFollowsADepthThatChanges(unittest.TestCase):
	"""A held block's depth is read again whenever its region is.

	From the review of 786be5c: the depth was fixed when the block was built, and an item moved to
	another level without its text changing kept the indent it was built with.
	"""

	def flow(self, caretIndex=0):
		from brlMultiline.flowControl import FlowController

		from .test_flowControl import renderer

		source = sourceOver(["parent", "child", "next"], [1, 2, 1], caretIndex=caretIndex)
		control = FlowController(source, renderer(numCols=32), numRows=3, live=True)
		control.enterAtCursor()
		return control, source.obj

	def depths(self, control, blockId):
		held = control.blocks.get(blockId)
		drawn = control.window.blocks[control.window.blockIndex(blockId)]
		return held.depth, drawn.depth

	def test_readingTheActiveBlockAgainTakesItsNewDepth(self):
		control, document = self.flow(caretIndex=1)
		self.assertEqual(self.depths(control, control.activeBlockId), (2, 2))
		document.lists[1] = 3
		control.refreshActive()
		self.assertEqual(self.depths(control, control.activeBlockId), (3, 3))

	def test_arrivingAtAHeldItemTakesItsNewDepth(self):
		control, document = self.flow()
		document.lists[1] = 3
		document.caretIndex = 1
		control.followCursor()
		self.assertEqual(self.depths(control, control.activeBlockId), (3, 3))

	def test_aListThatBecameProseLosesItsDepthThoughItsCellsDoNot(self):
		# A top level item draws at the margin, exactly as prose does, so its cells are the same
		# either way and only the depth says which it is.
		control, document = self.flow()
		document.lists[:] = [0, 0, 0]
		control.rereadContent()
		self.assertTrue(all(block.depth is None for block in control.blocks.values()))
		self.assertEqual([block.depth for block in control.window.blocks], [None, None, None])

	def test_aBlankLineStillHasNoDepth(self):
		control, document = self.flow(caretIndex=1)
		document.lines[1] = ""
		control.refreshActive()
		self.assertIsNone(control.blocks.get(control.activeBlockId).depth)


if __name__ == "__main__":
	unittest.main()
