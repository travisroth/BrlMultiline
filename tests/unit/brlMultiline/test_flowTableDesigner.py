# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for arranging a table's columns.

The dialog is a shell; everything that decides anything is `Arrangement`, which knows nothing
about wx. That split is what makes this file possible at all — a test run has no display —
and it is the same split `flowTable` makes against `flowRender`.
"""

import unittest

from ._stubs import (
	FakeNavigatorObject,
	FakeTableDocument,
	callAfterQueue,
	installStubs,
	mainFrame,
	resetConfig,
	spokenMessages,
)

installStubs()

from brlMultiline import (  # noqa: E402
	flowTable,
	flowTableDesigner,
	flowTableLayouts,
	flowTableSource,
)

WATCHLIST = (
	("Symbol", "Last", "Change", "%Chg"),
	("AAPL", "189.95", "+1.20", "+0.64%"),
)


def plan(columns=(1, 2, 3, 4), labels=None, excluded=(), omitted=(), keyColumn="first"):
	""":return: a column plan of the shape the designer is opened over."""
	labels = labels or {1: "Symbol", 2: "Last", 3: "Change", 4: "%Chg"}
	return flowTable.ColumnPlan(
		columns=tuple(
			flowTable.Column(index=index, width=6, label=labels.get(index, ""))
			for index in columns
		),
		numCols=32,
		excluded=tuple(excluded),
		omitted=tuple(omitted),
		keyColumn=(columns[0] if columns else None) if keyColumn == "first" else keyColumn,
	)


def aTable(url="https://example.com/watchlist"):
	""":return: a table the store can name, for the tests about saving."""
	document = FakeTableDocument([list(row) for row in WATCHLIST])
	document.documentConstantIdentifier = url
	return flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))


class TestWhatTheReaderOpens(unittest.TestCase):
	"""What the dialog shows is what they are feeling: the columns they can see, in the order
	they see them, and then the ones their layout leaves out."""

	def test_theColumnsAreTheOnesOnTheDisplay(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		self.assertEqual([column.index for column in arrangement.columns], [1, 2, 3, 4])

	def test_inTheOrderTheyAreDrawn(self):
		arrangement = flowTableDesigner.Arrangement.of(plan(columns=(3, 1, 2)))
		self.assertEqual([column.index for column in arrangement.columns], [3, 1, 2])

	def test_andTheHiddenOnesAfterThem(self):
		"""So that a column the reader dropped can be put back, which is the whole reason to
		show it at all."""
		arrangement = flowTableDesigner.Arrangement.of(plan(columns=(1, 2), excluded=(3, 4)))
		self.assertEqual([column.index for column in arrangement.columns], [1, 2, 3, 4])
		self.assertFalse(arrangement.at(2).shown)

	def test_aColumnIsCalledWhatItIsCalled(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		self.assertEqual(arrangement.at(0).name, "Symbol")

	def test_andByItsNumberWhereItHasNoHeading(self):
		"""A message list declares none, and "6" on its own is not a name."""
		arrangement = flowTableDesigner.Arrangement.of(plan(columns=(6,), labels={}))
		self.assertIn("6", arrangement.at(0).name)

	def test_whatWasDecidedBeforeIsThere(self):
		layout = flowTableLayouts.TableLayout(
			perColumn={2: flowTable.ColumnChoice(label="Price", keep=flowTable.KEEP_END)},
		)
		arrangement = flowTableDesigner.Arrangement.of(plan(), layout)
		self.assertEqual(arrangement.at(1).choice.label, "Price")


class TestTheLineForOneColumn(unittest.TestCase):
	"""Every column is one line that says everything decided about it. A list that said only
	the name would mean opening each column in turn to find the one that was changed."""

	def line(self, **changes):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.decide(0, **changes)
		return arrangement.at(0).describe()

	def test_theNameIsEnoughForAColumnNobodyHasTouched(self):
		self.assertEqual(flowTableDesigner.Arrangement.of(plan()).at(0).describe(), "Symbol")

	def test_aHiddenColumnSaysSo(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.toggle(0)
		self.assertIn("hidden", arrangement.at(0).describe())

	def test_soDoesOneCutAtItsEnd(self):
		said = self.line(overflow=flowTable.TRUNCATE, keep=flowTable.KEEP_END)
		self.assertIn("keeping the end", said)

	def test_andTheReadersOwnNameForIt(self):
		self.assertIn("called Ticker", self.line(label="Ticker"))

	def test_andTheRoomItWasGiven(self):
		self.assertIn("at least 12 cells", self.line(minWidth=12))

	def test_andWhereAPageBegins(self):
		self.assertIn("starts a page", self.line(startsAPage=True))


class TestArrangingIt(unittest.TestCase):
	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		self.arrangement = flowTableDesigner.Arrangement.of(plan())

	def test_aColumnCanBeMovedEarlier(self):
		self.assertEqual(self.arrangement.move(2, -1), 1)
		self.assertEqual([column.index for column in self.arrangement.columns], [1, 3, 2, 4])

	def test_andNotPastTheEnds(self):
		self.assertEqual(self.arrangement.move(0, -1), 0)
		self.assertEqual(self.arrangement.move(3, 1), 3)
		self.assertEqual([column.index for column in self.arrangement.columns], [1, 2, 3, 4])

	def test_aColumnCanBeHiddenAndShown(self):
		self.assertFalse(self.arrangement.toggle(1))
		self.assertTrue(self.arrangement.toggle(1))

	def test_butNotTheLastOneShowing(self):
		"""A table of no columns is not a layout, it is a blank display."""
		for position in (1, 2, 3):
			self.arrangement.toggle(position)
		self.assertTrue(self.arrangement.toggle(0))

	def test_cuttingIsOneQuestionRatherThanTwo(self):
		"""A reader looking at a column asks "what happens when it does not fit", and which
		end only means anything under the answer to that."""
		self.arrangement.setCutting(0, flowTableDesigner.CUT_END)
		self.assertEqual(self.arrangement.at(0).choice.overflow, flowTable.TRUNCATE)
		self.assertEqual(self.arrangement.at(0).choice.keep, flowTable.KEEP_END)
		self.arrangement.setCutting(0, flowTableDesigner.WRAPPED)
		self.assertEqual(self.arrangement.at(0).choice.overflow, flowTable.WRAP)
		self.assertEqual(self.arrangement.at(0).choice.keep, "")

	def test_andComesBackOutAsTheReaderPutItIn(self):
		self.arrangement.setCutting(1, flowTableDesigner.CUT_START)
		self.assertEqual(self.arrangement.at(1).cutting, flowTableDesigner.CUT_START)


class TestWhatLeavesTheDialog(unittest.TestCase):
	"""A `TableLayout`, holding only what was decided."""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		self.arrangement = flowTableDesigner.Arrangement.of(plan())

	def test_lookingAndPressingOkChangesNothing(self):
		"""Which is what pressing OK on a dialog nobody touched should mean."""
		self.assertTrue(self.arrangement.asLayout().isEmpty)

	def test_hidingAColumnNamesTheOnesThatStay(self):
		self.arrangement.toggle(1)
		self.assertEqual(self.arrangement.asLayout().columns, (1, 3, 4))

	def test_reorderingThemIsWrittenDownToo(self):
		self.arrangement.move(0, 1)
		self.assertEqual(self.arrangement.asLayout().columns, (2, 1, 3, 4))

	def test_aColumnsOwnDecisionsAreKeptByItsNumber(self):
		self.arrangement.decide(2, label="Delta", maxWidth=8)
		layout = self.arrangement.asLayout()
		self.assertEqual(layout.perColumn[3].label, "Delta")
		self.assertEqual(layout.perColumn[3].maxWidth, 8)

	def test_aColumnNobodyTouchedIsNotWrittenDown(self):
		self.arrangement.decide(0, label="Ticker")
		self.assertEqual(set(self.arrangement.asLayout().perColumn), {1})

	def test_theKeyColumnLeavesWithIt(self):
		self.arrangement.keyColumn = 3
		self.assertEqual(self.arrangement.asLayout().keyColumn, 3)

	def test_andTheWholeThingSurvivesTheStore(self):
		"""The record is what the layout is for: saved, read back, and the same."""
		self.arrangement.decide(1, label="Price", keep=flowTable.KEEP_END, minWidth=9)
		self.arrangement.toggle(3)
		read = flowTableLayouts.fromRecord(self.arrangement.asLayout().asRecord())
		self.assertEqual(read.columns, (1, 2, 3))
		self.assertEqual(read.perColumn[2].label, "Price")
		self.assertEqual(read.perColumn[2].keep, flowTable.KEEP_END)
		self.assertEqual(read.perColumn[2].minWidth, 9)


class TestWhatTheDialogSaysIsBeingDone(unittest.TestCase):
	"""A review found the controls describing something other than the display: an untouched
	column read "Wrapped" in a table that was cutting, nothing was ticked for the column being
	repeated, and the line for a column left out two of the decisions it claimed to hold."""

	def test_anUntouchedColumnFollowsTheTable(self):
		"""Rather than holding the first of the answers, which was a decision nobody made."""
		arrangement = flowTableDesigner.Arrangement.of(plan())
		self.assertEqual(arrangement.at(0).cutting, flowTableDesigner.FOLLOW)
		self.assertEqual(arrangement.at(0).headerEnd, flowTableDesigner.FOLLOW)

	def test_andSaysNothingAboutCuttingOnItsLine(self):
		self.assertEqual(flowTableDesigner.Arrangement.of(plan()).describe(1), "Last")

	def test_aTableThatRepeatsNoColumnNamesNone(self):
		"""The pinning is a setting and it can be off, and then there is no such column."""
		arrangement = flowTableDesigner.Arrangement.of(plan(keyColumn=None))
		self.assertEqual(arrangement.repeatedColumn, 0)
		self.assertEqual(arrangement.describe(0), "Symbol")

	def test_aColumnCanBeGivenBackToTheTable(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.setCutting(0, flowTableDesigner.CUT_END)
		arrangement.setCutting(0, flowTableDesigner.FOLLOW)
		self.assertEqual(arrangement.at(0).cutting, flowTableDesigner.FOLLOW)
		self.assertTrue(arrangement.asLayout().isEmpty)

	def test_whatTheTableItselfDoesIsCarried(self):
		"""So the following option can name it instead of leaving the reader to guess."""
		arrangement = flowTableDesigner.Arrangement.of(
			plan(),
			following=flowTableDesigner.CUT_START,
		)
		self.assertEqual(arrangement.following, flowTableDesigner.CUT_START)

	def test_theRepeatedColumnIsTheFirstShownWhenNobodyNamedOne(self):
		"""Zero in the record and a column on the display. A box on each column had nothing
		to show for that: the reader felt one repeated and saw none ticked."""
		arrangement = flowTableDesigner.Arrangement.of(plan())
		self.assertEqual(arrangement.repeatedColumn, 1)
		self.assertIn("repeated on every page", arrangement.describe(0))

	def test_andItMovesWhenTheFirstOneIsHidden(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.toggle(0)
		self.assertEqual(arrangement.repeatedColumn, 2)

	def test_andIsWhicheverColumnTheReaderNamed(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.keyColumn = 3
		self.assertNotIn("repeated on every page", arrangement.describe(0))
		self.assertIn("repeated on every page", arrangement.describe(2))

	def test_theLineSaysWhichEndTheHeadingKeeps(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.decide(0, headerKeep=flowTable.KEEP_END)
		self.assertIn("heading keeps its end", arrangement.describe(0))

	def test_aWidthFloorAboveItsCeilingIsRefused(self):
		"""Said rather than normalised: which of the two numbers they meant is not ours to
		guess, and closing on it would draw the column to neither."""
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.decide(1, minWidth=20, maxWidth=10)
		self.assertIn("Last", arrangement.complaint())

	def test_andOneOfThemOnItsOwnIsNot(self):
		arrangement = flowTableDesigner.Arrangement.of(plan())
		arrangement.decide(1, minWidth=20)
		self.assertEqual(arrangement.complaint(), "")


class TestAColumnTheMeasurementFoundNothingIn(unittest.TestCase):
	"""Measured and not drawn is not the same as hidden, and must not quietly become it. The
	reader who reported this had configured nothing at all and found columns missing from
	every later reading of their watchlist."""

	def test_itIsInTheListAndSaysWhyItIsNotOnTheDisplay(self):
		arrangement = flowTableDesigner.Arrangement.of(plan(columns=(1, 2), omitted=(3,)))
		self.assertEqual([column.index for column in arrangement.columns], [1, 2, 3])
		self.assertTrue(arrangement.at(2).shown)
		self.assertIn("empty here", arrangement.describe(2))

	def test_andHidingAnotherColumnDoesNotFreezeItOut(self):
		arrangement = flowTableDesigner.Arrangement.of(plan(columns=(1, 2), omitted=(3,)))
		arrangement.toggle(1)
		self.assertEqual(arrangement.asLayout().columns, (1, 3))


class TestOpeningATableThatIsAlreadyRemembered(unittest.TestCase):
	"""The dialog is opened over what the table is *being read with*. Given only the transient
	arrangement, a remembered table opened on an empty dialog with "remember this" already
	ticked — so pressing OK wrote that emptiness over the record they came back for."""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)

	def _saved(self):
		return flowTableLayouts.TableLayout(
			columns=(1, 3, 4),
			rowHeight=2,
			truncate=flowTableLayouts.YES,
			pinKey=flowTableLayouts.NO,
			headers=flowTableLayouts.YES,
			keyColumn=3,
			perColumn={3: flowTable.ColumnChoice(label="Delta", keep=flowTable.KEEP_END)},
		)

	def test_lookingAndPressingOkChangesNothingAtAll(self):
		"""Every field of the record, out the way it went in. Four of them were being lost:
		the row height, the table-wide cutting, and whether the key column and the headers are
		pinned — none of which the dialog asks about, and none of which it may decide."""
		saved = self._saved()
		arrangement = flowTableDesigner.Arrangement.of(
			plan(columns=(1, 3, 4), excluded=(2,)),
			saved,
			remembered=True,
		)
		self.assertEqual(arrangement.asLayout().asRecord(), saved.asRecord())

	def test_andWhatWasSavedIsWhatTheReaderIsShown(self):
		arrangement = flowTableDesigner.Arrangement.of(
			plan(columns=(1, 3, 4), excluded=(2,)),
			self._saved(),
			remembered=True,
		)
		self.assertFalse(arrangement.at(3).shown)
		self.assertEqual(arrangement.repeatedColumn, 3)
		self.assertIn("called Delta", arrangement.describe(1))


class TestKeepingOrDroppingWhatWasArranged(unittest.TestCase):
	"""The checkbox is a state and not a button. Unticking one that was ticked means the
	reader is finished with this table being remembered, and skipping the save on an untick
	left the old record in place — so the table came back laid out by a layout they had just
	said they were done with."""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		spokenMessages.clear()
		self.addCleanup(spokenMessages.clear)
		self.layout = flowTableLayouts.TableLayout(columns=(1, 3))

	def test_tickedFromUntickedSavesIt(self):
		handle = aTable()
		flowTableDesigner._keepOrDrop(handle, self.layout, remembered=False, keepIt=True)
		self.assertEqual(flowTableLayouts.layoutFor(handle).columns, (1, 3))

	def test_tickedFromTickedSavesItAgain(self):
		handle = aTable()
		flowTableLayouts.remember(handle, flowTableLayouts.TableLayout(columns=(2,)))
		flowTableDesigner._keepOrDrop(handle, self.layout, remembered=True, keepIt=True)
		self.assertEqual(flowTableLayouts.layoutFor(handle).columns, (1, 3))

	def test_untickedFromTickedDropsWhatWasSaved(self):
		handle = aTable()
		flowTableLayouts.remember(handle, flowTableLayouts.TableLayout(columns=(2,)))
		flowTableDesigner._keepOrDrop(handle, self.layout, remembered=True, keepIt=False)
		self.assertIsNone(flowTableLayouts.layoutFor(handle))
		self.assertTrue(any("deleted" in message for message in spokenMessages))

	def test_untickedFromUntickedSavesNothingAndSaysNothing(self):
		handle = aTable()
		flowTableDesigner._keepOrDrop(handle, self.layout, remembered=False, keepIt=False)
		self.assertIsNone(flowTableLayouts.layoutFor(handle))
		self.assertEqual(spokenMessages, [])

	def test_aTableNothingCanNameSaysSoRatherThanFailingQuietly(self):
		"""`remember` answers no for a table with nothing stable to find it by, and that
		answer was being thrown away: the reader was told their arrangement was kept."""
		flowTableDesigner._keepOrDrop(aTable(url=None), self.layout, remembered=False, keepIt=True)
		self.assertTrue(any("cannot be recognised" in message for message in spokenMessages))


class FakeDialog:
	"""The designer without a window: it records that it was opened, lets the test say what
	the reader did while it was up, and answers what it is told to answer."""

	answer = 0
	change = None
	opened: list = []

	def __init__(self, parent, arrangement):
		self.arrangement = arrangement
		self.keepIt = arrangement.remembered
		self.destroyed = False
		FakeDialog.opened.append(self)

	def ShowModal(self):
		if FakeDialog.change is not None:
			FakeDialog.change(self)
		return FakeDialog.answer

	def Destroy(self):
		self.destroyed = True


class FakeBand:
	"""Just enough band for the designer: a plan, a table, and somewhere to put a layout."""

	def __init__(self, handle):
		self.handle = handle
		self.here = handle
		self.plan = plan()
		self.tableLayoutInForce = None
		self.applied = []

	def columnPlan(self):
		return self.plan

	def _tableOnTheBand(self):
		return self.here

	def arrangeTable(self, layout):
		self.applied.append(layout)
		return True


class TestOpeningTheDesignerWithoutStoppingNvda(unittest.TestCase):
	"""Reported from hardware as a freeze and then a crash. The display stopped, the watchdog
	logged "Core frozen in stack!" every fifteen seconds, and the stack showed `ShowModal`
	called from inside `queueHandler.pumpAll`.

	A script runs inside that pump, and a modal dialog opened there never gives it back — so
	NVDA's core stops turning for as long as the dialog is up, which is to say until the reader
	kills the process. The dialog goes on the event loop instead, which is what NVDA's own
	commands do for every settings dialog they open.
	"""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)
		callAfterQueue.discard()
		self.addCleanup(callAfterQueue.discard)
		FakeDialog.opened = []
		FakeDialog.answer = flowTableDesigner.wx.ID_OK
		FakeDialog.change = None
		self.addCleanup(setattr, FakeDialog, "change", None)
		spokenMessages.clear()
		self.addCleanup(spokenMessages.clear)
		for name, value in (("CAN_DRAW", True), ("TableDesignerDialog", FakeDialog)):
			self.addCleanup(setattr, flowTableDesigner, name, getattr(flowTableDesigner, name))
			setattr(flowTableDesigner, name, value)
		self.band = FakeBand(aTable())

	def _hidesAColumn(self, dialog):
		dialog.arrangement.toggle(1)

	def test_theScriptReturnsWithNoDialogShown(self):
		"""The whole of the fault: anything shown here is shown inside the pump."""
		self.assertTrue(flowTableDesigner.arrangeTheTable(self.band))
		self.assertEqual(FakeDialog.opened, [])
		self.assertEqual(len(callAfterQueue.pending), 1)

	def test_andItIsShownOnTheNextTurnOfTheEventLoop(self):
		flowTableDesigner.arrangeTheTable(self.band)
		self.assertEqual(callAfterQueue.flush(), 1)
		self.assertEqual(len(FakeDialog.opened), 1)
		self.assertTrue(FakeDialog.opened[0].destroyed)

	def test_andTheMainWindowIsPutInFrontAndBackAgainAroundIt(self):
		"""NVDA restores the previous focus and the foreground around a popup, and only knows
		to if it is told."""
		before = (mainFrame.prePopups, mainFrame.postPopups)
		flowTableDesigner.arrangeTheTable(self.band)
		callAfterQueue.flush()
		self.assertEqual(
			(mainFrame.prePopups - before[0], mainFrame.postPopups - before[1]),
			(1, 1),
		)

	def test_whatWasArrangedReachesTheBand(self):
		FakeDialog.change = self._hidesAColumn
		flowTableDesigner.arrangeTheTable(self.band)
		callAfterQueue.flush()
		self.assertEqual([layout.columns for layout in self.band.applied], [(1, 3, 4)])

	def test_pressingOkOnADialogNobodyTouchedGivesTheTableBackAsItComes(self):
		flowTableDesigner.arrangeTheTable(self.band)
		callAfterQueue.flush()
		self.assertEqual(self.band.applied, [None])

	def test_cancellingChangesNothing(self):
		FakeDialog.answer = flowTableDesigner.wx.ID_CANCEL
		FakeDialog.change = self._hidesAColumn
		flowTableDesigner.arrangeTheTable(self.band)
		callAfterQueue.flush()
		self.assertEqual(self.band.applied, [])

	def test_nothingIsAppliedToATableTheReaderHasSinceLeft(self):
		"""The dialog stays up as long as they want it. Applying a watchlist's columns to
		whatever they walked into meanwhile is the wrongness the layout in force is keyed
		against, arriving by another road."""
		FakeDialog.change = self._hidesAColumn
		flowTableDesigner.arrangeTheTable(self.band)
		self.band.here = None
		callAfterQueue.flush()
		self.assertEqual(self.band.applied, [])

	def test_butWhatWasRememberedIsSavedAgainstItsOwnTable(self):
		"""Wherever the reader is by then, the record belongs to the table it was read from."""

		def hideAndKeep(dialog):
			self._hidesAColumn(dialog)
			dialog.keepIt = True

		FakeDialog.change = hideAndKeep
		flowTableDesigner.arrangeTheTable(self.band)
		self.band.here = None
		callAfterQueue.flush()
		self.assertEqual(flowTableLayouts.layoutFor(self.band.handle).columns, (1, 3, 4))

	def test_aBandWithNoTableOnItOpensNothing(self):
		self.band.plan = None
		self.assertFalse(flowTableDesigner.arrangeTheTable(self.band))
		self.assertEqual(callAfterQueue.pending, [])


if __name__ == "__main__":
	unittest.main()
