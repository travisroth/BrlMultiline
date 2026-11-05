# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the timing of operations that can stall NVDA.

A false clock stands in for time, so a test says exactly how slow each stage was. What matters
is that a slow operation explains itself at info, that a quick one stays out of the way, and
that timing never changes what the code it surrounds does.
"""

import sys
import unittest
from unittest import mock

from ._stubs import FakeTableDocument, installStubs, log

installStubs()

from brlMultiline import flowTableSource, stageTimes  # noqa: E402
from brlMultiline.flowTableSource import measure, tableAt  # noqa: E402

from .test_flowTableSource import FakeFocus  # noqa: E402


class FalseClock:
	"""A clock that moves only when told to."""

	def __init__(self):
		self.now = 0.0

	def __call__(self):
		return self.now

	def spend(self, seconds):
		self.now += seconds


class StageTimesTestCase(unittest.TestCase):
	"""Runs on a false clock, with an empty log."""

	def setUp(self):
		self.clock = FalseClock()
		for name, value in (("_clock", self.clock), ("enabled", True)):
			patcher = mock.patch.object(stageTimes, name, value)
			patcher.start()
			self.addCleanup(patcher.stop)
		log.messages.clear()

	def logged(self, level):
		return [message for at, message in log.messages if at == level and "timing:" in message]


class TestReporting(StageTimesTestCase):
	def test_aSlowOperationIsLoggedAtInfo(self):
		with stageTimes.operation("table show"):
			with stageTimes.stage("measure"):
				self.clock.spend(stageTimes.SLOW_SECONDS)
		(report,) = self.logged("info")
		self.assertIn("table show took 0.250s", report)
		self.assertIn("measure: 1 calls, 0.250s, slowest 0.250s", report)

	def test_aQuickOperationWithStagesIsLoggedAtDebug(self):
		with stageTimes.operation("table show"):
			with stageTimes.stage("measure"):
				self.clock.spend(0.01)
		self.assertEqual(self.logged("info"), [])
		self.assertEqual(len(self.logged("debug")), 1)

	def test_aQuickOperationThatDidNothingIsNotLogged(self):
		with stageTimes.operation("table show"):
			pass
		self.assertEqual(log.messages, [])

	def test_stagesAreKeptByPathAndAddUp(self):
		with stageTimes.operation("table show"):
			with stageTimes.stage("measure"):
				for spent in (0.1, 0.3):
					with stageTimes.stage("cell lookup"):
						self.clock.spend(spent)
		(report,) = self.logged("info")
		self.assertIn("measure/cell lookup: 2 calls, 0.400s, slowest 0.300s", report)

	def test_anOperationInsideAnotherIsAStageOfIt(self):
		with stageTimes.operation("display rebuild"):
			with stageTimes.stage("apply flow"):
				with stageTimes.operation("table show"):
					self.clock.spend(1)
		(report,) = self.logged("info")
		self.assertIn("display rebuild took 1.000s", report)
		self.assertIn("apply flow/table show: 1 calls", report)

	def test_notesAreWrittenIntoTheReport(self):
		with stageTimes.operation("table show"):
			stageTimes.note(lambda: "a wide table")
			self.clock.spend(1)
		self.assertIn("  a wide table", self.logged("info")[0])

	def test_aNoteThatFailsDoesNotCostTheReport(self):
		with stageTimes.operation("table show"):
			stageTimes.note(lambda: 1 / 0)
			self.clock.spend(1)
		self.assertIn("could not be written", self.logged("info")[0])

	def test_stagesEndedDuringRecoveryAreCounted(self):
		with stageTimes.operation("table show"):
			with mock.patch.object(sys.modules["watchdog"], "isAttemptingRecovery", True):
				with stageTimes.stage("measure"):
					self.clock.spend(1)
		self.assertIn("during freeze recovery: 1", self.logged("info")[0])


class TestStayingOutOfTheWay(StageTimesTestCase):
	def test_aStageOutsideAnOperationRecordsNothing(self):
		with stageTimes.stage("measure"):
			self.clock.spend(10)
		stageTimes.note(lambda: "nobody is listening")
		self.assertEqual(log.messages, [])

	def test_anExceptionPassesThroughAndIsCounted(self):
		with self.assertRaises(LookupError):
			with stageTimes.operation("table show"):
				with stageTimes.stage("cell lookup"):
					self.clock.spend(1)
					raise LookupError
		self.assertIn("1 raised", self.logged("info")[0])

	def test_theNextOperationStartsCleanAfterAnException(self):
		with self.assertRaises(LookupError):
			with stageTimes.operation("first"):
				raise LookupError
		with stageTimes.operation("second"):
			self.clock.spend(1)
		self.assertEqual(len(self.logged("info")), 1)
		self.assertIn("second took", self.logged("info")[0])

	def test_aDecoratedFunctionIsTimedOnEveryCall(self):
		@stageTimes.operation("display rebuild")
		def rebuild():
			self.clock.spend(1)
			return "built"

		self.assertEqual(rebuild(), "built")
		self.assertEqual(rebuild(), "built")
		self.assertEqual(len(self.logged("info")), 2)


class TestTurnedOff(StageTimesTestCase):
	def setUp(self):
		super().setUp()
		patcher = mock.patch.object(stageTimes, "enabled", False)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_itIsOffUntilAskedFor(self):
		mock.patch.stopall()
		self.assertFalse(stageTimes.enabled)

	def test_aSlowOperationLogsNothing(self):
		with stageTimes.operation("table show"):
			with stageTimes.stage("measure"):
				self.clock.spend(10)
		self.assertEqual(log.messages, [])

	def test_aNoteIsNeverAskedFor(self):
		said = mock.Mock(return_value="a note")
		with stageTimes.operation("table show"):
			stageTimes.note(said)
			self.clock.spend(10)
		said.assert_not_called()

	def test_aMethodIsNotShadowed(self):
		document = Document()
		with stageTimes.timedMethod(document, "lookUp", "table lookup"):
			self.assertEqual(vars(document), {})

	def test_aDecoratedFunctionStillRuns(self):
		@stageTimes.operation("display rebuild")
		def rebuild():
			return "built"

		self.assertEqual(rebuild(), "built")
		self.assertEqual(log.messages, [])

	def test_itCanBeTurnedOnWhileRunning(self):
		stageTimes.enabled = True
		with stageTimes.operation("table show"):
			self.clock.spend(1)
		self.assertEqual(len(self.logged("info")), 1)


class Document:
	def lookUp(self, value):
		return value * 2


class TestTimedMethod(StageTimesTestCase):
	def test_theMethodIsTimedAndStillAnswers(self):
		document = Document()
		with stageTimes.operation("table show"):
			with stageTimes.timedMethod(document, "lookUp", "table lookup"):
				self.assertEqual(document.lookUp(2), 4)
				self.clock.spend(1)
		self.assertIn("table lookup: 1 calls", self.logged("info")[0])

	def test_theShadowIsTakenAwayEvenAfterAnException(self):
		document = Document()
		with self.assertRaises(LookupError):
			with stageTimes.timedMethod(document, "lookUp", "table lookup"):
				raise LookupError
		self.assertNotIn("lookUp", vars(document))

	def test_nestingLeavesTheOuterShadowInPlace(self):
		document = Document()
		with stageTimes.timedMethod(document, "lookUp", "table lookup"):
			with stageTimes.timedMethod(document, "lookUp", "table lookup"):
				pass
			self.assertIn("lookUp", vars(document))
		self.assertNotIn("lookUp", vars(document))

	def test_aDocumentWithoutTheMethodIsLeftAlone(self):
		document = Document()
		with stageTimes.timedMethod(document, "getNVDAObjectFromIdentifier", "table lookup"):
			pass
		self.assertEqual(vars(document), {})

	def test_anObjectThatRefusesAttributesIsLeftAlone(self):
		class Refusing:
			__slots__ = ()

			def lookUp(self):
				return "answered"

		refusing = Refusing()
		with stageTimes.timedMethod(refusing, "lookUp", "table lookup"):
			self.assertEqual(refusing.lookUp(), "answered")


class TestMeasuringATable(StageTimesTestCase):
	def handle(self):
		document = FakeTableDocument([["Symbol", "Last"], ["AAPL", "190"], ["MSFT", "410"]], row=2)
		return tableAt(FakeFocus(document))

	def test_everyCellReadIsCounted(self):
		handle = self.handle()
		with stageTimes.operation("table show"):
			measure(handle)
			self.clock.spend(1)
		report = self.logged("info")[0]
		self.assertIn("cell lookup: 6 calls", report)
		self.assertIn("cell text: 6 calls", report)
		self.assertIn("cell translate: 6 calls", report)

	def test_timingDoesNotChangeTheMeasurement(self):
		untimed = measure(self.handle())
		with stageTimes.operation("table show"):
			timed = measure(self.handle())
		self.assertEqual(timed, untimed)

	def test_describingATableNamesWhatReadsItAndWhy(self):
		said = flowTableSource.describeTable(self.handle(), "column 2 is new")
		self.assertIn("FakeTableDocument", said)
		self.assertIn("read again because column 2 is new", said)


if __name__ == "__main__":
	unittest.main()
