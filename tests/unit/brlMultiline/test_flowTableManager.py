# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for the saved table layouts manager.

Written for a watchlist whose site changed its address overnight: the layout was still saved, nothing
could say it was the watchlist's, and nothing could point it at the new address. Every test here is
something that case, or an orphaned layout beside it, calls for.
"""

import json
import unittest

from ._stubs import (
	CONFIG,
	FakeNavigatorObject,
	FakeTableDocument,
	installStubs,
	resetConfig,
)

installStubs()

from brlMultiline import flowTableLayouts, flowTableManager, flowTableSource  # noqa: E402
from brlMultiline.flowTableLayouts import MATCH_EXACT, MATCH_PATH, MATCH_SITE, SavedLayout  # noqa: E402

WATCHLIST = [
	["Symbol", "Last", "Change"],
	["AAPL", "182.50", "+1.25"],
]

NEW_ADDRESS = "https://www.barchart.com/my/watchlist?viewName=122002"


def page(url=NEW_ADDRESS, headers=None):
	document = FakeTableDocument(
		[list(row) for row in WATCHLIST],
		columnHeaders=headers or {1: "Symbol", 2: "Last", 3: "Change"},
	)
	document.documentConstantIdentifier = url
	return flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))


def layout(id, where="", **fields):
	fields = {"layout": {"columns": [1, 3]}, "saved": 100, "used": 100, **fields}
	return SavedLayout(id=id, where=where, **fields)


class ManagerTestCase(unittest.TestCase):
	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)

	def manager(self, layouts, table=None):
		here = flowTableManager.hereFor(table) if table is not None else None
		return flowTableManager.LayoutManager(layouts, here)


class TestTheWatchlistWhoseAddressChanged(ManagerTestCase):
	"""The case the manager was built for."""

	def setUp(self):
		super().setUp()
		self.old = layout(
			"old",
			where="https://www.barchart.com/watchlist/main",
			match=MATCH_PATH,
			headings=("Symbol", "Last", "Change"),
			name="Watchlist",
		)

	def test_theTableItIsOpenedOverSaysNothingApplies(self):
		manager = self.manager([self.old], page())
		self.assertIsNone(manager.appliedId)
		self.assertIn("No saved layout applies", manager.hereWords())
		self.assertIn(NEW_ADDRESS, manager.hereWords())

	def test_useForThisTableMakesItApply(self):
		manager = self.manager([self.old], page())
		manager.useHere(manager.indexOf("old"))
		self.assertEqual("old", manager.appliedId)
		self.assertIn("applies here", manager.label(manager.indexOf("old")))
		self.assertEqual("Watchlist", manager.at(0).name)

	def test_andAfterOkTheTableComesUpLaidOut(self):
		manager = self.manager([self.old], page())
		manager.useHere(0)
		self.assertTrue(manager.commit())
		self.assertEqual((1, 3), flowTableLayouts.layoutFor(page()).columns)

	def test_alsoUseForThisTableLeavesTheOriginalWhereItWas(self):
		manager = self.manager([self.old], page())
		copied = manager.alsoUseHere(0)
		self.assertEqual(2, len(manager))
		self.assertEqual(NEW_ADDRESS, manager.at(copied).where)
		self.assertEqual("https://www.barchart.com/watchlist/main", manager.at(manager.indexOf("old")).where)
		self.assertEqual(manager.at(copied).id, manager.appliedId)

	def test_theLayoutsForThisSiteComeFirst(self):
		elsewhere = layout("elsewhere", where="https://example.com/table", used=999)
		manager = self.manager([elsewhere, self.old], page())
		self.assertEqual("old", manager.at(0).id)


class TestALayoutFromBeforeNamesWereKept(ManagerTestCase):
	def setUp(self):
		super().setUp()
		self.legacy = SavedLayout(
			id="legacy",
			layout={"columns": [2, 3], "headings": {"2": "Last", "3": "Change"}},
			saved=1756800000,
			used=1756800000,
			legacyWhere="0123456789abcdef",
			legacyWhat="fedcba9876543210",
		)

	def test_itIsNamedByTheDateItWasSaved(self):
		manager = self.manager([self.legacy])
		self.assertIn("Unnamed layout saved September", manager.label(0))
		self.assertIn("address not recorded yet", manager.label(0))

	def test_itsColumnsAreNamedByTheirHeadings(self):
		self.assertIn("Columns shown: Last, Change", self.manager([self.legacy]).details(0))

	def test_itCannotBeGivenAMatchUntilItHasAnAddress(self):
		self.assertEqual([], self.manager([self.legacy]).matchChoices(0))

	def test_usingItForThisTableGivesItAnAddressAndTheDefaultMatch(self):
		manager = self.manager([self.legacy], page())
		manager.useHere(0)
		saved = manager.at(0)
		self.assertFalse(saved.isLegacy)
		self.assertEqual(NEW_ADDRESS, saved.where)
		self.assertEqual(MATCH_PATH, saved.match)
		self.assertIn("barchart.com/my/watchlist", saved.name)

	def test_orTypingAnAddressForIt(self):
		manager = self.manager([self.legacy])
		self.assertEqual("", manager.setAddress(0, "https://example.com/list"))
		self.assertEqual(MATCH_PATH, manager.at(0).match)
		self.assertFalse(manager.at(0).isLegacy)


class TestEditingOne(ManagerTestCase):
	def test_renaming(self):
		manager = self.manager([layout("a", where="https://example.com/x")])
		manager.rename(0, "  My   list ")
		self.assertEqual("My list", manager.at(0).name)

	def test_anAddressCannotBeEmpty(self):
		manager = self.manager([layout("a", where="https://example.com/x")])
		self.assertTrue(manager.setAddress(0, "   "))
		self.assertEqual("https://example.com/x", manager.at(0).where)

	def test_aWebPageOffersEveryMatch(self):
		manager = self.manager([layout("a", where="https://example.com/x", match=MATCH_PATH)])
		self.assertEqual(
			[MATCH_PATH, MATCH_EXACT, MATCH_SITE], [match for match, _label in manager.matchChoices(0)]
		)
		manager.setMatch(0, MATCH_SITE)
		self.assertEqual(MATCH_SITE, manager.at(0).match)

	def test_anythingElseOffersOnlyExact(self):
		manager = self.manager([layout("a", where="excel/EXCEL7")])
		self.assertEqual([MATCH_EXACT], [match for match, _label in manager.matchChoices(0)])
		manager.setMatch(0, MATCH_SITE)
		self.assertEqual(MATCH_EXACT, manager.at(0).match)

	def test_movingAWebLayoutToSomethingElseMakesItExact(self):
		manager = self.manager([layout("a", where="https://example.com/x", match=MATCH_SITE)])
		manager.setAddress(0, "explorer/DirectUIHWND")
		self.assertEqual(MATCH_EXACT, manager.at(0).match)

	def test_lettingItApplyWhateverTheHeadings(self):
		saved = layout("a", where=NEW_ADDRESS, match=MATCH_PATH, headings=("Criterion", "Level"))
		manager = self.manager([saved], page())
		self.assertIsNone(manager.appliedId)
		manager.setRequireHeadings(0, False)
		self.assertEqual("a", manager.appliedId)

	def test_deleting(self):
		manager = self.manager(
			[layout("a", where="https://example.com/x"), layout("b", where="https://example.com/y")]
		)
		manager.delete(manager.indexOf("a"))
		self.assertEqual(["b"], [saved.id for saved in manager.layouts])


class TestWhatOkWrites(ManagerTestCase):
	def test_nothingWhenNothingChanged(self):
		flowTableLayouts.remember(page(), flowTableLayouts.TableLayout(columns=(1,)))
		before = CONFIG["tableLayouts"]
		manager = self.manager(flowTableLayouts.stored())
		self.assertFalse(manager.commit())
		self.assertEqual(before, CONFIG["tableLayouts"])

	def test_everythingItHoldsWhenSomethingDid(self):
		flowTableLayouts.remember(page(), flowTableLayouts.TableLayout(columns=(1,)))
		manager = self.manager(flowTableLayouts.stored())
		manager.rename(0, "Renamed")
		self.assertTrue(manager.commit())
		(saved,) = json.loads(CONFIG["tableLayouts"])["layouts"]
		self.assertEqual("Renamed", saved["name"])

	def test_outsideATableThereIsNoTableToUseALayoutFor(self):
		manager = self.manager([layout("a", where="https://example.com/x")])
		self.assertIsNone(manager.here)
		self.assertTrue(manager.useHere(0))
		self.assertEqual(-1, manager.alsoUseHere(0))
		self.assertIn("Not in a table", manager.hereWords())


if __name__ == "__main__":
	unittest.main()
