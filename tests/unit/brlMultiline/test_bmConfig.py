# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the configuration layer, and for meeting a display for the first time.

Every display is a display the add-on has never met until something writes settings for it,
and NVDA does not create a `__many__` subsection just because one was read: it raises
`KeyError`. That is the whole of what these tests are about, because it is a failure the
add-on hits at startup rather than in some corner, and it takes the plugin down with it.

The rest of the suite reads its settings through stand-ins for these functions, so the real
ones are reached here through L{realBmConfig}.
"""

import unittest

from ._stubs import CONFIG, FakeHandler, installStubs, realBmConfig, resetConfig

installStubs()

import braille  # noqa: E402
import config  # noqa: E402

from brlMultiline import bmConfig  # noqa: E402

FOCUS = "freedomScientific_1x80"
MONARCH = "humanware_8x32"


def displaysSection():
	return config.conf[bmConfig.CONFIG_SECTION]["displays"]


class ConfigTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()

	def tearDown(self):
		resetConfig()
		braille.handler = None


class TestDisplaySections(ConfigTestCase):
	def test_aDisplayNeverSeenBeforeIsCreated(self):
		"""The failure that took the plugin down at startup: reading is not enough."""
		self.assertFalse(displaysSection().isSet(FOCUS))
		section = bmConfig.getDisplayConfig(FOCUS)
		self.assertIsNotNone(section)
		self.assertTrue(displaysSection().isSet(FOCUS))

	def test_settingsFallBackToTheirDefaults(self):
		"""A section NVDA creates carries no spec, so its settings have no defaults yet."""
		section = bmConfig.getDisplayConfig(FOCUS)
		del CONFIG["segmentCount"]
		del CONFIG["segmentSizes"]
		self.assertEqual(section["segmentCount"], 1)
		self.assertEqual(section["segmentSizes"], [])

	def test_storedSettingsWin(self):
		CONFIG["segmentCount"] = 3
		self.assertEqual(bmConfig.getDisplayConfig(FOCUS)["segmentCount"], 3)

	def test_eachDisplayIsCreatedAsItIsMet(self):
		"""Swapping displays must not raise, whichever display is met first."""
		bmConfig.getDisplayConfig(FOCUS)
		bmConfig.getDisplayConfig(MONARCH)
		self.assertTrue(displaysSection().isSet(FOCUS))
		self.assertTrue(displaysSection().isSet(MONARCH))

	def test_theCurrentDisplayIsUsedWhenNoKeyIsGiven(self):
		braille.handler = FakeHandler(numRows=1, numCols=80)
		bmConfig.getDisplayConfig()
		self.assertTrue(displaysSection().isSet("stub_1x80"))


class TestDisplayKey(ConfigTestCase):
	def test_keyIsDriverNameAndGeometry(self):
		braille.handler = FakeHandler(numRows=8, numCols=32)
		self.assertEqual(realBmConfig["getDisplayKey"](), "stub_8x32")

	def test_geometryDistinguishesTheSameDriver(self):
		braille.handler = FakeHandler(numRows=1, numCols=80)
		self.assertEqual(realBmConfig["getDisplayKey"](), "stub_1x80")

	def test_noDisplayHasAKeyOfItsOwn(self):
		braille.handler = FakeHandler(numRows=1, numCols=40)
		braille.handler.display = None
		self.assertEqual(realBmConfig["getDisplayKey"](), "noBraille_1x40")


class TestSettingsForANewDisplay(ConfigTestCase):
	"""Each accessor funnels through `getDisplayConfig`, so each one met the same failure."""

	def test_layoutIsASegmentCount(self):
		CONFIG["segmentCount"] = 4
		self.assertEqual(realBmConfig["getLayout"](FOCUS), 4)

	def test_explicitSizesWinOverTheCount(self):
		CONFIG["segmentSizes"] = [20, 60]
		self.assertEqual(realBmConfig["getLayout"](FOCUS), [20, 60])

	def test_focusSegment(self):
		CONFIG["focusSegment"] = 2
		self.assertEqual(realBmConfig["getFocusSegment"](FOCUS), 2)

	def test_reverseScrollButtons(self):
		CONFIG["reverseScrollBtns"] = True
		self.assertTrue(realBmConfig["shouldReverseScrollButtons"](FOCUS))

	def test_showDocumentLines(self):
		CONFIG["showDocumentLines"] = True
		self.assertTrue(realBmConfig["shouldShowDocumentLines"](FOCUS))

	def test_setLayoutStoresAgainstADisplayNeverSeenBefore(self):
		realBmConfig["setLayout"](2, [20, 60], FOCUS)
		section = bmConfig.getDisplayConfig(FOCUS)
		self.assertEqual(section["segmentCount"], 2)
		self.assertEqual(section["segmentSizes"], [20, 60])


if __name__ == "__main__":
	unittest.main()
