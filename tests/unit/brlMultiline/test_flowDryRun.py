# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the flow diagnostics.

These matter more than their size suggests. Every hardware run this project depends on ends
with a report being read back, and a report that is hard to get at is a report that does not
get read. The clipboard is the whole point of `toClipboard`, and the two ways an indent goes
wrong looking alike is the whole point of `describeIndent`.
"""

import unittest

from ._stubs import Region, clipboard, installStubs

installStubs()

from brlMultiline.flow import BlockId, SourceBlock  # noqa: E402
from brlMultiline.flowDryRun import CRLF, describeIndent, toClipboard  # noqa: E402
from brlMultiline.flowIndent import DOTS_78, TWO_SPACES, planFor  # noqa: E402
from brlMultiline.flowRender import FlowRenderer  # noqa: E402


class FakeHandler:
	def __init__(self):
		self.buffer = None


class FakeControl:
	"""A controller reduced to what a diagnostic asks of one."""

	def __init__(self, depths, numCols=32, plan=None):
		self.renderer = FlowRenderer(FakeHandler(), numCols=numCols, fillRows=True)
		if plan is not None:
			self.renderer.indentPlan = plan
		self.window = type("Window", (), {"blocks": self._blocks(depths)})()

	def _blocks(self, depths):
		return [
			SourceBlock(
				blockId=BlockId(generation=1, bookmark=index, unit="object"),
				region=Region("item"),
				depth=depth,
			)
			for index, depth in enumerate(depths)
		]


class TestCopyingAReport(unittest.TestCase):
	def setUp(self):
		clipboard.clear()

	def test_theReportReachesTheClipboard(self):
		self.assertTrue(toClipboard("A report", ["one", "two"]))
		self.assertIn("one", clipboard[-1])
		self.assertIn("two", clipboard[-1])

	def test_rowsAreSeparatedTheWayWindowsExpects(self):
		"""A bare line feed pastes into Notepad as one very long line."""
		toClipboard("A report", ["one", "two"])
		self.assertIn("one" + CRLF + "two", clipboard[-1])

	def test_theReportSaysWhatItIs(self):
		"""Two reports pasted one after the other must be two reports."""
		toClipboard("A report", ["one"])
		self.assertTrue(clipboard[-1].startswith("A report — "))

	def test_anEmptyReportStillCopiesItsHeading(self):
		self.assertTrue(toClipboard("A report", []))
		self.assertIn("A report", clipboard[-1])

	def test_aRefusedClipboardIsReportedRatherThanRaised(self):
		"""The caller says so out loud; it must not lose the command to an exception."""
		import api

		original = api.copyToClip
		api.copyToClip = lambda text, notify=False: (_ for _ in ()).throw(RuntimeError("busy"))
		try:
			self.assertFalse(toClipboard("A report", ["one"]))
		finally:
			api.copyToClip = original


class TestDescribingTheIndent(unittest.TestCase):
	"""The two ways an indent is wrong feel identical under the fingers."""

	def test_prosePlainlySaysThereIsNoDepth(self):
		said = describeIndent(FakeControl([None, None]))
		self.assertIn("nothing on the band reports a depth", said)

	def test_depthPresentButUndrawnSaysSo(self):
		"""The case that would otherwise need the report running twice to diagnose."""
		said = describeIndent(FakeControl([3, 5]))
		self.assertIn("none drawn", said)
		self.assertIn("3", said)
		self.assertIn("5", said)

	def test_aDrawnIndentNamesItsBaselineAndStyle(self):
		control = FakeControl([1, 2, 3], plan=planFor([1, 2, 3], 32, style=TWO_SPACES))
		said = describeIndent(control)
		self.assertIn(TWO_SPACES, said)
		self.assertIn("level 1 at the margin", said)

	def test_aRebasedIndentSaysWhatTheMarginStandsFor(self):
		control = FakeControl([8, 9, 10], plan=planFor([8, 9, 10], 32, style=DOTS_78))
		said = describeIndent(control)
		self.assertIn("margin stands for level 8", said)

	def test_anUnrebasedIndentDoesNotClaimAMargin(self):
		control = FakeControl([1, 2], plan=planFor([1, 2], 32, style=TWO_SPACES))
		self.assertNotIn("margin stands for", describeIndent(control))


if __name__ == "__main__":
	unittest.main()
