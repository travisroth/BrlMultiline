# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for arranging a display that is several physical displays combined.

The scenario throughout is the one the virtual driver was built for: a Monarch of 8 rows of
32 above a Focus 80 of one row, making a 9 by 80 composite in which 48 cells of every
Monarch row reach no hardware at all.

Those dead cells are what these tests are really about. Nothing NVDA does knows they are
different from any other cell, so text flowed into them is lost with nothing to say where it
went. The view has to claim them, and the claim has to hold whatever the user has configured.
"""

import types
import unittest

from ._stubs import (
	BAND_CONFIG,
	CONFIG,
	fakeVirtualDisplay,
	installStubs,
	log,
	resetConfig,
	setBandConfig,
)

installStubs()

import braille  # noqa: E402

from brlMultiline.devices import DeviceInfo, deviceMap, isVirtualDisplay  # noqa: E402
from brlMultiline.layout import SegmentRect, deviceBandRects  # noqa: E402
from brlMultiline.panels import BlankPanel  # noqa: E402
from brlMultiline.views import deviceSegmentKey, deviceView  # noqa: E402

MONARCH = DeviceInfo(driverName="hidBrailleStandard", rowStart=0, numRows=8, numCols=32)
FOCUS = DeviceInfo(driverName="freedomScientific", rowStart=8, numRows=1, numCols=80)
COMPOSITE_ROWS = 9
COMPOSITE_COLS = 80

MONARCH_KEY = "hidBrailleStandard_8x32"
FOCUS_KEY = "freedomScientific_1x80"


class DeviceViewTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		# One segment each unless a test says otherwise, so that a test about dead columns is
		# not also a test about how many segments the default happens to be.
		setBandConfig(MONARCH_KEY, segmentCount=1)
		setBandConfig(FOCUS_KEY, segmentCount=1)

	def tearDown(self):
		resetConfig()

	def build(self, devices=(MONARCH, FOCUS), numRows=COMPOSITE_ROWS, numCols=COMPOSITE_COLS):
		view = deviceView(numRows, numCols, devices)
		view.validate(numRows, numCols)
		return view

	def keysOf(self, view):
		return [spec.key for spec in view.flatten()]


class TestBandRects(unittest.TestCase):
	"""The arithmetic, on its own."""

	def test_aNarrowerDisplayHasDeadColumnsToItsRight(self):
		bands = deviceBandRects([MONARCH.band, FOCUS.band], COMPOSITE_COLS)
		self.assertEqual(bands[0].live, SegmentRect(row=0, col=0, numRows=8, numCols=32))
		self.assertEqual(bands[0].dead, SegmentRect(row=0, col=32, numRows=8, numCols=48))

	def test_theWidestDisplayHasNone(self):
		bands = deviceBandRects([MONARCH.band, FOCUS.band], COMPOSITE_COLS)
		self.assertEqual(bands[1].live, SegmentRect(row=8, col=0, numRows=1, numCols=80))
		self.assertIsNone(bands[1].dead)

	def test_displaysOfEqualWidthHaveNoDeadColumnsAtAll(self):
		bands = deviceBandRects([(0, 1, 40), (1, 1, 40)], 40)
		self.assertEqual([band.dead for band in bands], [None, None])

	def test_liveAndDeadCoverEveryCell(self):
		"""The two halves of a band have to be the whole band, or the view cannot tile."""
		covered = 0
		for band in deviceBandRects([MONARCH.band, FOCUS.band], COMPOSITE_COLS):
			covered += band.live.displaySize
			if band.dead is not None:
				covered += band.dead.displaySize
		self.assertEqual(covered, COMPOSITE_ROWS * COMPOSITE_COLS)

	def test_aGapBetweenBandsIsRefused(self):
		"""Reported here, where the display that should have owned the rows can be named."""
		with self.assertRaises(ValueError) as caught:
			deviceBandRects([(0, 8, 32), (9, 1, 80)], COMPOSITE_COLS)
		self.assertIn("row 9", str(caught.exception))

	def test_aDisplayWiderThanTheCompositeIsRefused(self):
		with self.assertRaises(ValueError):
			deviceBandRects([(0, 1, 80)], 40)

	def test_aDisplayWithNoCellsIsRefused(self):
		with self.assertRaises(ValueError):
			deviceBandRects([(0, 1, 0)], 40)


class TestDeadColumns(DeviceViewTestCase):
	def test_theyAreClaimedAndLeftBlank(self):
		view = self.build()
		blanks = [panel for panel in view.panels if isinstance(panel, BlankPanel)]
		self.assertEqual(len(blanks), 1)
		self.assertEqual(blanks[0].rect, SegmentRect(row=0, col=32, numRows=8, numCols=48))
		self.assertEqual(blanks[0].segments(), [])

	def test_noSegmentReachesIntoThem(self):
		"""The failure this whole view exists to prevent: text written where no hardware is."""
		for spec in self.build().flatten():
			if spec.rect.row < 8:
				self.assertLessEqual(spec.rect.endCol, 32, f"{spec.key} runs past the Monarch")

	def test_displaysOfEqualWidthNeedNoBlanks(self):
		view = self.build(
			devices=(
				DeviceInfo("alva", 0, 1, 40),
				DeviceInfo("handyTech", 1, 1, 40),
			),
			numRows=2,
			numCols=40,
		)
		self.assertEqual([panel for panel in view.panels if isinstance(panel, BlankPanel)], [])


class TestSegmentsPerDisplay(DeviceViewTestCase):
	def test_oneSegmentEachByDefault(self):
		self.assertEqual(
			self.keysOf(self.build()),
			[deviceSegmentKey("hidBrailleStandard", 0), deviceSegmentKey("freedomScientific", 0)],
		)

	def test_eachDisplayKeepsItsOwnLayout(self):
		"""The point of keying on the member's own display key: nothing to set up."""
		setBandConfig(MONARCH_KEY, segmentCount=4)
		setBandConfig(FOCUS_KEY, segmentCount=2)
		view = self.build()
		monarch = [spec for spec in view.flatten() if spec.rect.row < 8]
		focus = [spec for spec in view.flatten() if spec.rect.row == 8]
		self.assertEqual(len(monarch), 4)
		self.assertEqual(len(focus), 2)

	def test_aMultiRowDisplayIsDividedByRows(self):
		setBandConfig(MONARCH_KEY, segmentCount=4)
		monarch = [spec for spec in self.build().flatten() if spec.rect.row < 8]
		self.assertEqual([spec.rect.numRows for spec in monarch], [2, 2, 2, 2])
		self.assertEqual({spec.rect.numCols for spec in monarch}, {32})

	def test_aSingleRowDisplayIsDividedByColumns(self):
		"""As it is when NVDA drives it on its own, which is what makes the settings carry over."""
		setBandConfig(FOCUS_KEY, segmentCount=2)
		focus = [spec for spec in self.build().flatten() if spec.rect.row == 8]
		self.assertEqual([spec.rect.numCols for spec in focus], [40, 40])
		self.assertEqual([spec.rect.col for spec in focus], [0, 40])

	def test_aDisplayWithDivisionTurnedOffIsOneSegment(self):
		setBandConfig(MONARCH_KEY, segmentCount=4)
		setBandConfig(FOCUS_KEY, segmentsEnabled=False, segmentCount=4)
		view = self.build()
		self.assertEqual(len([spec for spec in view.flatten() if spec.rect.row == 8]), 1)
		self.assertEqual(len([spec for spec in view.flatten() if spec.rect.row < 8]), 4)

	def test_theCompositeIsAlwaysDividedAtTheDisplays(self):
		"""No composite level switch can join two displays into one segment.

		A segment straddling the boundary would have half its cells on hardware that is not
		as wide as the other half, which is the dead column trap wearing a different hat.
		"""
		CONFIG["segmentsEnabled"] = False
		self.assertEqual(len(self.build().flatten()), 2)

	def test_aLayoutThatNoLongerFitsLeavesThatDisplayWhole(self):
		setBandConfig(FOCUS_KEY, segmentSizes=[30, 30])
		view = self.build()
		self.assertEqual(len([spec for spec in view.flatten() if spec.rect.row == 8]), 1)
		self.assertTrue(
			any(level == "error" and FOCUS_KEY in message for level, message in log.messages),
			log.messages,
		)

	def test_theOtherDisplayIsUnaffectedByThat(self):
		setBandConfig(MONARCH_KEY, segmentCount=4)
		setBandConfig(FOCUS_KEY, segmentSizes=[30, 30])
		self.assertEqual(len([spec for spec in self.build().flatten() if spec.rect.row < 8]), 4)


class TestSegmentKeys(DeviceViewTestCase):
	def test_segmentsAreNamedAfterTheirDisplay(self):
		"""So that a pin on the secondary survives a settings change on the primary."""
		setBandConfig(MONARCH_KEY, segmentCount=2)
		self.assertEqual(
			self.keysOf(self.build())[:2],
			["device.hidBrailleStandard.0", "device.hidBrailleStandard.1"],
		)

	def test_segmentsAreFreeForDocumentLines(self):
		for spec in self.build().flatten():
			self.assertIsNone(spec.owner)

	def test_readingOrderRunsOnFromOneDisplayToTheNext(self):
		setBandConfig(MONARCH_KEY, segmentCount=4)
		setBandConfig(FOCUS_KEY, segmentCount=2)
		self.assertEqual(
			[spec.documentContextIndex for spec in self.build().flatten()],
			[0, 1, 2, 3, 4, 5],
		)

	def test_onePanelPerSegment(self):
		"""The finest grain there is, so a claim evicts only the segments it wants."""
		setBandConfig(MONARCH_KEY, segmentCount=4)
		view = self.build()
		# Four on the Monarch, one on the Focus, and one claiming the dead columns.
		self.assertEqual(len(view.panels), 6)


class TestFocusSegment(DeviceViewTestCase):
	def test_theLastSegmentFollowsTheFocusByDefault(self):
		setBandConfig(MONARCH_KEY, segmentCount=4)
		self.assertEqual(self.build().focusSegmentKey, deviceSegmentKey("freedomScientific", 0))

	def test_itIsCountedAcrossEveryDisplay(self):
		setBandConfig(MONARCH_KEY, segmentCount=4)
		CONFIG["focusSegment"] = 4
		self.assertEqual(self.build().focusSegmentKey, deviceSegmentKey("freedomScientific", 0))

	def test_aSegmentThatDoesNotExistFallsBackToTheLast(self):
		CONFIG["focusSegment"] = 6
		view = self.build()
		self.assertEqual(view.focusSegmentKey, deviceSegmentKey("freedomScientific", 0))
		self.assertTrue(
			any(level == "warning" and "no segment 6" in message for level, message in log.messages),
			log.messages,
		)


class TestDeviceViewRefusals(DeviceViewTestCase):
	def test_aCompositeOfNoDisplaysIsRefused(self):
		with self.assertRaises(ValueError):
			deviceView(1, 40, [])

	def test_bandsThatDoNotDescribeTheDisplayAreRefused(self):
		"""The driver and the handler disagreeing about the geometry, which must not pass."""
		with self.assertRaises(ValueError):
			deviceView(COMPOSITE_ROWS, COMPOSITE_COLS, [MONARCH])


class TestDeviceMap(unittest.TestCase):
	def setUp(self):
		self.originalHandler = braille.handler
		braille.handler = types.SimpleNamespace(display=None)

	def tearDown(self):
		braille.handler = self.originalHandler
		log.messages.clear()

	def test_anOrdinaryDisplayHasNoDeviceMap(self):
		braille.handler.display = types.SimpleNamespace(name="freedomScientific")
		self.assertFalse(isVirtualDisplay())
		self.assertEqual(deviceMap(), [])

	def test_noDisplayAtAllIsNotAnError(self):
		self.assertEqual(deviceMap(), [])
		self.assertEqual(log.messages, [])

	def test_theCompositeReportsItsDisplaysInOrder(self):
		braille.handler.display = fakeVirtualDisplay(tuple(MONARCH), tuple(FOCUS))
		self.assertTrue(isVirtualDisplay())
		self.assertEqual(deviceMap(), [MONARCH, FOCUS])

	def test_aDisplayKeyIsTheOneThatDisplayHasOnItsOwn(self):
		self.assertEqual(MONARCH.displayKey, MONARCH_KEY)
		self.assertEqual(FOCUS.displayKey, FOCUS_KEY)

	def test_somethingClaimingToBeTheCompositeButNotShapedLikeItIsReported(self):
		braille.handler.display = types.SimpleNamespace(name="brlMultilineVirtual", slots=[object()])
		self.assertEqual(deviceMap(), [])
		self.assertTrue(any(level == "error" for level, _message in log.messages), log.messages)


class TestBandConfigStub(unittest.TestCase):
	"""The stub itself, since these tests are worth nothing if it cannot tell displays apart."""

	def tearDown(self):
		resetConfig()

	def test_aDisplayWithoutItsOwnSettingsFallsBackToTheShared(self):
		from brlMultiline import bmConfig

		CONFIG["segmentCount"] = 3
		self.assertEqual(bmConfig.getLayout("something_1x40"), 3)

	def test_aDisplayWithItsOwnSettingsGetsThem(self):
		from brlMultiline import bmConfig

		CONFIG["segmentCount"] = 3
		setBandConfig(MONARCH_KEY, segmentCount=5)
		self.assertEqual(bmConfig.getLayout(MONARCH_KEY), 5)
		self.assertEqual(bmConfig.getLayout(FOCUS_KEY), 3)

	def test_resettingClearsThem(self):
		setBandConfig(MONARCH_KEY, segmentCount=5)
		resetConfig()
		self.assertEqual(BAND_CONFIG, {})
