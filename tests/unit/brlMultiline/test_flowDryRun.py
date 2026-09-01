# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the flow diagnostics.

These matter more than their size suggests. Every hardware run this project depends on ends
with a report being read back, and a report that is hard to get at is a report that does not
get read. The clipboard is the whole point of `toClipboard`, and the two ways an indent goes
wrong looking alike is the whole point of `describeIndent`.
"""

import unittest

from ._stubs import FakeTreeInterceptor, Region, clipboard, installStubs

installStubs()

from brlMultiline.flow import BlockId, SourceBlock  # noqa: E402
from brlMultiline.flowDryRun import (  # noqa: E402
	CRLF,
	_bandGeometry,
	describeIndent,
	describeLineFocus,
	liveReport,
	toClipboard,
)
from brlMultiline.flowIndent import DOTS_78, FOCUS_CELL, TWO_SPACES, planFor  # noqa: E402
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


class FakeRect:
	def __init__(self, numRows, numCols):
		self.numRows = numRows
		self.numCols = numCols


class FakeBand:
	"""A band reduced to the two questions a diagnostic asks it how big it is."""

	def __init__(self, claimed=None, wouldClaim=None, raises=False):
		self._claimed = claimed
		self._wouldClaim = wouldClaim
		self._raises = raises

	def segment(self):
		if self._raises:
			raise RuntimeError("no container")
		return type("Segment", (), {"rect": self._claimed})() if self._claimed else None

	def bandRect(self):
		if self._wouldClaim is None:
			raise RuntimeError("cannot say")
		return self._wouldClaim


class FakeDisplayHandler:
	"""A handler whose display is a composite: nine rows of eighty."""

	def __init__(self, numRows=9, numCols=80):
		self.buffer = None
		self.displayDimensions = FakeRect(numRows, numCols)


class TestWhichBandIsMeasured(unittest.TestCase):
	"""A report laid out at the wrong width answers a different question from the display.

	On a composite — a Monarch with a Focus 80 under it — the whole display is nine rows of
	eighty and the band is the Monarch's eight rows of thirty two, because a band must lie
	inside one physical display's live cells. Every width-dependent answer differs between
	the two, and indent most of all: seven levels of true depth cost twelve cells, which fit
	within eighty's share and do not within thirty two's. Measured wrongly, the report says
	the margin was not rebased about a display nobody has.
	"""

	def test_theClaimedBandWins(self):
		band = FakeBand(claimed=FakeRect(8, 32))
		rows, cols, said = _bandGeometry(FakeDisplayHandler(), band)
		self.assertEqual((rows, cols), (8, 32))
		self.assertIn("the band on the display", said)

	def test_whatTheBandWouldClaimIsNextBest(self):
		"""So that diagnosing with the flow turned off still uses the reader's geometry."""
		band = FakeBand(claimed=None, wouldClaim=FakeRect(8, 32))
		rows, cols, said = _bandGeometry(FakeDisplayHandler(), band)
		self.assertEqual((rows, cols), (8, 32))
		self.assertIn("would claim", said)

	def test_theWholeDisplayIsTheLastResortAndSaysSo(self):
		rows, cols, said = _bandGeometry(FakeDisplayHandler(), None)
		self.assertEqual((rows, cols), (9, 80))
		self.assertIn("no band to ask", said)

	def test_aBandThatCannotAnswerFallsBackRatherThanFailing(self):
		band = FakeBand(raises=True)
		rows, cols, said = _bandGeometry(FakeDisplayHandler(), band)
		self.assertEqual((rows, cols), (9, 80))
		self.assertIn("no band to ask", said)

	def test_theWidthActuallyChangesTheIndentAnswer(self):
		"""The reason any of this matters, stated as the arithmetic it turns on."""
		from brlMultiline.flowIndent import planFor

		wide = planFor([5, 6, 7], 80, style=TWO_SPACES)
		narrow = planFor([5, 6, 7], 32, style=TWO_SPACES)
		self.assertIsNone(wide.noteLevel)
		self.assertEqual(narrow.noteLevel, 5)


class FakeWindow:
	def __init__(self, blocks, anchor=None):
		self.blocks = blocks
		self.anchor = anchor


class FakeAnchor:
	def __init__(self, entry="top", rowIndex=0):
		self.entry = type("Entry", (), {"value": entry})()
		self.rowIndex = rowIndex


class FakeLiveControl(FakeControl):
	"""A controller that can also say what its rows hold, as the live one can."""

	def __init__(self, depths, rows=None, anchor=None, active=None, numCols=32, plan=None):
		super().__init__(depths, numCols=numCols, plan=plan)
		self.source = "a source"
		self.window = FakeWindow(self.window.blocks, anchor=anchor)
		self.activeBlockId = active
		self._rows = rows if rows is not None else ["0: a row"]
		self.placements = []

	def describeRows(self):
		return self._rows


def Band(control):
	""":return: enough of a band for the report to read a controller off."""
	return type("Band", (), {"controller": control})()



class TestWhatTheDocumentItselfSays(unittest.TestCase):
	"""The one question the rest of the report cannot answer.

	A reader feeling a blank row where they expected a value has two very different things in
	front of them: a line the document has, which their own arrow keys will land on too, or a
	row the flow invented. Everything else in the report is what the flow made of the document,
	so it cannot tell them apart. This reads the document the way NVDA's own down arrow reads
	it, with nothing of ours in between.
	"""

	def _band(self, lines, caretIndex=0, cells=None):
		document = FakeTreeInterceptor(lines, caretIndex=caretIndex)
		if cells is not None:
			document._getTableCellCoords = lambda info: _cellAt(cells, info)
		control = FakeLiveControl([1])
		control.source = type("Source", (), {"obj": document})()
		self.document = document
		return Band(control)

	def test_theDocumentsOwnLinesAreReported(self):
		said = " ".join(liveReport(self._band(["Symbol", "", "Latest"])))
		self.assertIn("What the document itself says", said)
		self.assertIn("'Symbol'", said)
		self.assertIn("'Latest'", said)

	def test_soTheBlanksBetweenThemAreVisibleInTheReport(self):
		"""Which is the whole point: this is where a blank row is shown to be the document's
		own, or shown not to be."""
		said = " ".join(liveReport(self._band(["Symbol", "", "Latest"])))
		self.assertIn("''", said)

	def test_theEndOfTheDocumentIsSaidRatherThanWalkedPast(self):
		self.assertIn("the document ends here", " ".join(liveReport(self._band(["only"]))))

	def test_whatNvdaCallsEachLineIsAskedOfNvda(self):
		"""`_getTableCellCoords` is what browse mode's own table navigation is decided by, so
		a line's cell — or its not being in one — is NVDA's answer and not a guess of ours."""
		said = " ".join(liveReport(self._band(["Symbol", "", "Latest"], cells={0: (1, 2), 2: (1, 3)})))
		self.assertIn("row 1 column 2", said)
		self.assertIn("not in a table cell", said)

	def test_nothingIsMoved(self):
		"""It reads. A diagnostic that moved the reader's cursor would be reporting on a
		document it had just changed."""
		band = self._band(["Symbol", "", "Latest"], caretIndex=0)
		liveReport(band)
		self.assertEqual(self.document.caretIndex, 0)

	def test_aSourceWithNoDocumentSaysNothingAtAll(self):
		"""A run of objects has no document to ask, and an absent section is better than a
		section saying it could not be filled in."""
		control = FakeLiveControl([1])
		control.source = type("Source", (), {"obj": object()})()
		self.assertNotIn("What the document itself says", " ".join(liveReport(Band(control))))


def _cellAt(cells, info):
	""":return: a stand-in for NVDA's `_TableCell`, or LookupError as NVDA's own raises."""
	found = cells.get(getattr(info, "index", None))
	if found is None:
		raise LookupError("Not in a table cell")
	row, column = found
	return type("Cell", (), {"tableID": 1, "row": row, "col": column})()


class TestReportingTheLiveBand(unittest.TestCase):
	"""A dry run builds its own flow, so it can say nothing about the reader's window.

	That gap was found trying to diagnose a display which had scrolled the wrong way on a
	focus move: the report described arrival and panning in a flow built fresh for the
	report, and had no line in it about the window that had actually scrolled.
	"""

	def test_aBandWithNoFlowSaysSoPlainly(self):
		self.assertIn("not showing a flow", liveReport(None)[0])

	def test_aBandWhoseControllerIsGoneSaysSoToo(self):
		band = type("Band", (), {"controller": None})()
		self.assertIn("not showing a flow", liveReport(band)[0])

	def test_theRowsAreReported(self):
		control = FakeLiveControl([1, 2], rows=["0: Inbox", "1: Drafts"])
		band = type("Band", (), {"controller": control})()
		said = " ".join(liveReport(band))
		self.assertIn("Inbox", said)
		self.assertIn("Drafts", said)

	def test_theEntryEdgeIsReported(self):
		"""The line that settles a scrolling complaint: placed at the top and filled down,
		or placed at the bottom with the rows above filled in behind."""
		control = FakeLiveControl([1], anchor=FakeAnchor(entry="bottom"))
		band = type("Band", (), {"controller": control})()
		self.assertIn("entered from the bottom", " ".join(liveReport(band)))

	def test_theIndentPlanIsReported(self):
		control = FakeLiveControl([8, 9], plan=planFor([8, 9], 32, style=TWO_SPACES))
		band = type("Band", (), {"controller": control})()
		self.assertIn("margin stands for level 8", " ".join(liveReport(band)))

	def test_everyMoveOfTheBandIsReportedInOrder(self):
		"""One reading of one decision was not enough, and said the wrong thing.

		On the report that located the last bug the direction test's verdict was correct —
		"back" — while the band had been placed forward, because a re-render of the row the
		reader arrived on had moved the window before the verdict was used, and a later
		no-op call had overwritten the note. A history cannot hide either of those.
		"""
		control = FakeLiveControl([1])
		control.placements = [
			"a re-render of the cursor's block: forward, brought on at the bottom",
			"the reader arriving: back, already on the band, nothing moved",
		]
		band = type("Band", (), {"controller": control})()
		said = liveReport(band)
		where = [line for line in said if "re-render" in line or "reader arriving" in line]
		self.assertEqual(len(where), 2)
		self.assertIn("re-render", where[0])

	def test_aBandThatHasNotMovedSaysSo(self):
		control = FakeLiveControl([1])
		self.assertIn("nothing has moved the band yet", " ".join(liveReport(band=Band(control))))

	def test_aBandThatCannotDescribeItselfStillReports(self):
		"""A diagnostic that raises tells the reader nothing at all, which is worse than a
		diagnostic that says one of its questions could not be answered."""

		class Broken(FakeLiveControl):
			def describeRows(self):
				raise RuntimeError("no anchor")

		band = type("Band", (), {"controller": Broken([1])})()
		said = liveReport(band)
		self.assertTrue(any("could not be described" in line for line in said))


class TestDescribingTheLineFocus(unittest.TestCase):
	"""Three answers, because two of them feel identical: a band with no mark on it is
	either one with the setting off or one whose focused row had no indent to draw it in."""

	class Marked:
		def __init__(self, cells, lineFocus=True, numCols=4):
			self._cells = cells
			self.lineFocus = lineFocus
			self.renderer = type("R", (), {"numCols": numCols})()

		def cells(self):
			return self._cells

	def test_offSaysSo(self):
		said = describeLineFocus(self.Marked([0] * 8, lineFocus=False))
		self.assertIn("off", said)

	def test_theMarkedRowIsNamed(self):
		cells = [0, 0, 0, 0] + [FOCUS_CELL, FOCUS_CELL, 1, 2]
		self.assertIn("row 1", describeLineFocus(self.Marked(cells)))

	def test_onButUndrawnSaysWhy(self):
		"""The case that would otherwise read as the setting having failed to take."""
		said = describeLineFocus(self.Marked([1] * 8))
		self.assertIn("no room", said)

	def test_aBandThatCannotBeReadDoesNotRaise(self):
		class Broken(TestDescribingTheLineFocus.Marked):
			def cells(self):
				raise RuntimeError("no anchor")

		self.assertIn("could not be read", describeLineFocus(Broken([])))


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


class TestWhatAnObjectTableAddsToTheReport(unittest.TestCase):
	"""A table read out of a document is NVDA's own answer throughout, and there is nothing to
	report about how it was arrived at. A table made of objects is this add-on presenting one
	as though it were a document, and every one of its answers is a choice between two or three
	places to ask — which is exactly what a report has to be able to settle, and could not."""

	def band(self, document):
		control = FakeLiveControl([1])
		control.source = type("Source", (), {"handle": type("Handle", (), {"document": document})()})()
		return type("Band", (), {"controller": control})()

	def test_anObjectTablesAccountIsInTheReport(self):
		said = " ".join(liveReport(self.band(_SaysSomething())))
		self.assertIn("Band object table:", said)
		self.assertIn("what it found", said)

	def test_thePinnedHeaderSaysWhereItCameFrom(self):
		"""The line the report needed and did not have: it showed a header reading "Column left
		Position" over columns the same report said were headed "Name" and "Status"."""
		band = self.band(_SaysSomething())
		band.controller.source.describeHeader = lambda: "declared by the table's own cells"
		self.assertIn("pinned header: declared", " ".join(liveReport(band)))

	def test_aSourceWithNothingToSayAboutItAddsNoLine(self):
		self.assertNotIn("pinned header", " ".join(liveReport(self.band(_SaysSomething()))))

	def test_aDocumentTableAddsNothing(self):
		said = " ".join(liveReport(self.band(object())))
		self.assertNotIn("Band object table", said)

	def test_anAccountThatFailsDoesNotTakeTheReportWithIt(self):
		"""Everything in a report is asked of something that may be gone by the time it is
		asked. A diagnostic that raises is a diagnostic nobody gets."""
		said = " ".join(liveReport(self.band(_Refuses())))
		self.assertIn("could not be described", said)


class _SaysSomething:
	def describe(self):
		return ["  what it found"]


class _Refuses:
	def describe(self):
		raise RuntimeError("gone")
