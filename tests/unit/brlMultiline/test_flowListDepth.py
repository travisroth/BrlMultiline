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
		original = flowListDepth.depthBeforeText
		flowListDepth.depthBeforeText = lambda commands: 1 / 0
		try:
			region.update()
		finally:
			flowListDepth.depthBeforeText = original
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


if __name__ == "__main__":
	unittest.main()
