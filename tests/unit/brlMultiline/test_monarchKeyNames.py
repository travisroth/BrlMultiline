# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the names the Monarch driver gives keys NVDA names by usage alone.

The declarations in `MONARCH` are the two d-pad ups as the hardware reported them on
14 September 2026: the same usage under left controls at data index 18 and under right
controls at data index 14. The rest of the descriptor is a sketch of the shape that matters,
dots, space, routing and the zoom keys, not a transcript.
"""

import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineMonarch",
	),
)

import keyNames  # noqa: E402
from keyNames import ButtonDeclaration as Declared  # noqa: E402
from keyNames import KeyName  # noqa: E402

PAGE = keyNames.BRAILLE_PAGE
LEFT = 0x20D
RIGHT = 0x20E
ROUTER_SET_1 = 0xFA
DPAD_UP = 0x216
DPAD_DOWN = 0x217
DOT_1 = 0x201
SPACE = 0x209

MONARCH = [
	Declared(18, PAGE, DPAD_UP, 3, LEFT),
	Declared(19, PAGE, DPAD_DOWN, 3, LEFT),
	Declared(14, PAGE, DPAD_UP, 2, RIGHT),
	Declared(15, PAGE, DPAD_DOWN, 2, RIGHT),
	Declared(1, PAGE, DOT_1, 4, 0x200),
	Declared(9, PAGE, SPACE, 4, 0x200),
	Declared(30, PAGE, 0x220, 5, 0x20F),
	Declared(31, PAGE, 0x221, 5, 0x20F),
	Declared(40, PAGE, 0x100, 6, ROUTER_SET_1),
	Declared(41, PAGE, 0x100, 6, ROUTER_SET_1),
]


class TestNames(unittest.TestCase):
	def setUp(self):
		self.names = keyNames.namesByDataIndex(MONARCH)

	def test_eachPadIsNamedForItsSide(self):
		self.assertEqual(KeyName("dpadUp", "leftDpadUp"), self.names[18])
		self.assertEqual(KeyName("dpadUp", "rightDpadUp"), self.names[14])
		self.assertEqual("leftDpadDown", self.names[19].ours)
		self.assertEqual("rightDpadDown", self.names[15].ours)

	def test_theZoomKeysAreNamedAndSayWhatNvdaCallsThem(self):
		self.assertEqual(KeyName("brailleUsage544", "zoomIn"), self.names[30])
		self.assertEqual(KeyName("brailleUsage545", "zoomOut"), self.names[31])

	def test_dotsSpaceAndRoutingKeepNvdasNames(self):
		for index in (1, 9, 40, 41):
			self.assertNotIn(index, self.names)

	def test_aKeyDeclaredOnceIsNotRenamed(self):
		"""Only a duplicate needs telling apart. A single joystick keeps `joystickUp`."""
		names = keyNames.namesByDataIndex([Declared(5, PAGE, 0x211, 3, LEFT)])
		self.assertEqual({}, names)

	def test_twiceInOneCollectionIsNotTwoSides(self):
		names = keyNames.namesByDataIndex(
			[Declared(5, PAGE, DPAD_UP, 3, LEFT), Declared(6, PAGE, DPAD_UP, 3, LEFT)],
		)
		self.assertEqual({}, names)

	def test_aCollectionTheStandardDoesNotDescribeGivesNoSide(self):
		"""A word guessed for an unknown collection would be a name nobody could predict."""
		names = keyNames.namesByDataIndex(
			[Declared(5, PAGE, DPAD_UP, 3, 0x300), Declared(6, PAGE, DPAD_UP, 2, RIGHT)],
		)
		self.assertEqual({6: KeyName("dpadUp", "rightDpadUp")}, names)

	def test_aDuplicatedSpaceStillTypes(self):
		names = keyNames.namesByDataIndex(
			[Declared(9, PAGE, SPACE, 3, LEFT), Declared(10, PAGE, SPACE, 2, RIGHT)]
		)
		self.assertEqual({}, names)

	def test_otherPagesAreIgnored(self):
		names = keyNames.namesByDataIndex(
			[Declared(5, 0x09, DPAD_UP, 3, LEFT), Declared(6, 0x09, DPAD_UP, 2, RIGHT)],
		)
		self.assertEqual({}, names)

	def test_theStandardNamesAreNvdasSpelling(self):
		"""As `_usageIDToGestureName` builds them from `BraillePageUsageID`."""
		self.assertEqual("dpadUp", keyNames.standardName(DPAD_UP))
		self.assertEqual("joystickCenter", keyNames.standardName(0x210))
		self.assertEqual("rockerPress", keyNames.standardName(0x21E))

	def test_routingIsRouterSetOneOnly(self):
		"""NVDA folds set 1 into a routing name and names the keys of the other sets one by one."""
		declared = MONARCH + [Declared(50, PAGE, 0x100, 7, 0xFB)]
		self.assertEqual(frozenset({40, 41}), keyNames.routingDataIndices(declared))


class TestSpecificId(unittest.TestCase):
	def setUp(self):
		self.names = keyNames.namesByDataIndex(MONARCH)
		self.routing = keyNames.routingDataIndices(MONARCH)

	def build(self, nvdaNames, dataIndices):
		return keyNames.specificId(nvdaNames, dataIndices, self.routing, self.names)

	def test_aSinglePad(self):
		self.assertEqual("rightDpadUp", self.build(["dpadUp"], [14]))
		self.assertEqual("leftDpadUp", self.build(["dpadUp"], [18]))

	def test_aChordKeepsItsOtherKeys(self):
		self.assertEqual("space+rightDpadUp", self.build(["space", "dpadUp"], [9, 14]))

	def test_routingIsSkippedWhenLiningUp(self):
		"""The routing name comes last in NVDA's names, whatever order the indices arrived in."""
		self.assertEqual(
			"zoomIn+routerSet1_routerKey",
			self.build(["brailleUsage544", "routerSet1_routerKey"], [40, 30]),
		)

	def test_nothingToRenameIsNone(self):
		self.assertIsNone(self.build(["space", "dot1"], [9, 1]))

	def test_namesThatDoNotLineUpRenameNothing(self):
		"""If NVDA ever builds names differently, no name may land on the wrong key."""
		self.assertIsNone(self.build(["dot1", "space"], [14, 1]))
		self.assertIsNone(self.build(["space"], [9, 14]))


class TestFromCaps(unittest.TestCase):
	def test_rangesExpandToOneDeclarationPerIndex(self):
		class Cap:
			def __init__(self, isRange, usage, index, usageMax=None, indexMax=None):
				from types import SimpleNamespace

				self.IsRange = isRange
				self.UsagePage = PAGE
				self.LinkCollection = 3
				self.LinkUsage = LEFT
				self.u1 = SimpleNamespace(
					NotRange=SimpleNamespace(Usage=usage, DataIndex=index),
					Range=SimpleNamespace(
						UsageMin=usage,
						UsageMax=usageMax,
						DataIndexMin=index,
						DataIndexMax=indexMax,
					),
				)

		declared = keyNames.declarationsFromCaps([Cap(True, 0x215, 10, 0x219, 14), Cap(False, 0x220, 30)])
		self.assertEqual(
			[(10, 0x215), (11, 0x216), (12, 0x217), (13, 0x218), (14, 0x219), (30, 0x220)],
			[(d.dataIndex, d.usage) for d in declared],
		)


if __name__ == "__main__":
	unittest.main()
