# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for remembering how a reader wants a table laid out.

The command that lays a table out in columns has always said its end state is "a layout
remembered against the table so that a watchlist comes up laid out". Two things had to exist
first: a name for the table that survives the page being loaded again, and a record of what
the reader decided that is not a record of how wide the cells came out on the display they
decided it on.
"""

import unittest

from ._stubs import (
	CONFIG,
	FakeGrid,
	FakeNavigatorObject,
	FakeTableDocument,
	installStubs,
	resetConfig,
)

installStubs()

from brlMultiline import flowTableIdentity, flowTableLayouts, flowTableSource  # noqa: E402

WATCHLIST = [
	["Symbol", "Last", "Change"],
	["AAPL", "182.50", "+1.25"],
	["F", "9.10", "-0.05"],
]

FILES = [
	[("Name", "report.docx"), ("Date modified", "8/28/2026 9:41 AM")],
	[("Name", "notes.md"), ("Date modified", "8/27/2026 4:02 PM")],
]


class LayoutTestCase(unittest.TestCase):
	"""A store that starts empty, as a reader who has saved nothing has."""

	def setUp(self):
		resetConfig()
		self.addCleanup(resetConfig)

	def page(self, url="https://example.com/watchlist", headers=None):
		""":return: a table on a web page, which knows its own URL as NVDA knows it."""
		document = FakeTableDocument(
			[list(row) for row in WATCHLIST],
			columnHeaders=headers or {1: "Symbol", 2: "Last", 3: "Change"},
		)
		document.documentConstantIdentifier = url
		obj = FakeNavigatorObject("a page", treeInterceptor=document)
		return flowTableSource.tableAt(obj)

	def fileList(self, appName="explorer", windowClassName="DirectUIHWND"):
		""":return: File Explorer's Details view, which has no URL and is a control."""
		view = FakeGrid(FILES, name="Items View")
		view.appModule = FakeNavigatorObject(appName)
		view.appModule.appName = appName
		view.windowClassName = windowClassName
		for item in view.items:
			item.appModule = view.appModule
			item.windowClassName = windowClassName
		return flowTableSource.tableAt(view.item(1))


class TestNamingATable(LayoutTestCase):
	"""Two parts, and they cost very different amounts: where the table is, which is one
	attribute, and what its columns are called, which is a read apiece."""

	def test_aPageIsNamedByItsUrl(self):
		"""NVDA's own `documentConstantIdentifier`, which is what it remembers a caret
		position against across loads — the same kind of memory as a saved layout."""
		self.assertEqual(flowTableIdentity.whereOf(self.page()), "https://example.com/watchlist")

	def test_aListViewIsNamedByItsApplicationAndWindow(self):
		"""Either alone names too much: a window class of "DirectUIHWND" is half of Windows,
		and an application has many lists in it."""
		self.assertEqual(flowTableIdentity.whereOf(self.fileList()), "explorer/DirectUIHWND")

	def test_somethingThatWillNotSayIsNotNamed(self):
		view = FakeGrid(FILES)
		handle = flowTableSource.tableAt(view.item(1))
		self.assertEqual(flowTableIdentity.whereOf(handle), "")
		self.assertFalse(flowTableIdentity.identityOf(handle).isKnown)

	def test_theSignatureIsWhatTheColumnsAreCalled(self):
		"""The half that survives a page being regenerated: identifiers change with the
		markup and the headings are what the table is about."""
		said = flowTableIdentity.signatureOf(self.page())
		self.assertIn("Symbol", said)
		self.assertIn("Change", said)

	def test_aTableThatDeclaresNothingHasNoSignature(self):
		view = FakeGrid([["a", "b"], ["c", "d"]])
		self.assertEqual(flowTableIdentity.signatureOf(flowTableSource.tableAt(view.item(1))), "")

	def test_theSameTableOnAnotherLoadIsTheSameName(self):
		self.assertEqual(flowTableIdentity.identityOf(self.page()), flowTableIdentity.identityOf(self.page()))

	def test_aDifferentTableOnTheSamePageIsNot(self):
		"""Which is what the signature is for."""
		here = flowTableIdentity.identityOf(self.page())
		there = flowTableIdentity.identityOf(self.page(headers={1: "Criterion", 2: "Level", 3: "Remarks"}))
		self.assertEqual(here.where, there.where)
		self.assertNotEqual(here.what, there.what)

	def test_theSignatureIsBounded(self):
		"""A read per column, made where the reader is waiting. Eight headings tell any two
		tables apart that one reader has both of."""
		wide = [[f"column {number}" for number in range(20)] for _row in range(3)]
		document = FakeTableDocument(wide, columnHeaders={n: f"h{n}" for n in range(1, 21)})
		document.documentConstantIdentifier = "https://example.com/wide"
		handle = flowTableSource.tableAt(FakeNavigatorObject("a page", treeInterceptor=document))
		said = flowTableIdentity.signatureOf(handle)
		self.assertEqual(len(said.split(flowTableIdentity.SEPARATOR)), flowTableIdentity.SIGNATURE_COLUMNS)


class TestWhatIsRemembered(LayoutTestCase):
	"""The reader's choices, not the arithmetic. A plan holds widths in cells and cells belong
	to the display it was made on; the reader carries two."""

	def test_onlyWhatWasDecidedIsWrittenDown(self):
		layout = flowTableLayouts.TableLayout(columns=(1, 3))
		self.assertEqual(layout.asRecord(), {"columns": [1, 3]})

	def test_anUnsetFieldFollowsTheSettings(self):
		"""So that changing a setting still reaches a table that was saved before it."""
		layout = flowTableLayouts.TableLayout()
		self.assertTrue(layout.truncateOr(True))
		self.assertFalse(layout.truncateOr(False))
		self.assertEqual(layout.rowHeightOr(3), 3)

	def test_aFieldTheReaderSetOverridesThem(self):
		layout = flowTableLayouts.TableLayout(truncate=flowTableLayouts.NO, rowHeight=2)
		self.assertFalse(layout.truncateOr(True))
		self.assertEqual(layout.rowHeightOr(3), 2)

	def test_aRecordThatDecidesNothingIsNotWorthSaving(self):
		self.assertTrue(flowTableLayouts.TableLayout().isEmpty)

	def test_aRecordIsReadBackForgivingly(self):
		"""It reads a file a reader may have edited, and a record another version wrote."""
		read = flowTableLayouts.fromRecord({"columns": ["2", "nonsense", 4], "truncate": "maybe"})
		self.assertEqual(read.columns, (2, 4))
		self.assertEqual(read.truncate, flowTableLayouts.FOLLOW)
		self.assertEqual(flowTableLayouts.fromRecord("not a record"), flowTableLayouts.TableLayout())


class TestRememberingAndFindingOne(LayoutTestCase):
	"""The round trip, which is the feature: save it here, find it when the reader comes back."""

	def test_nothingIsSavedToBeginWith(self):
		self.assertIsNone(flowTableLayouts.layoutFor(self.page()))

	def test_aSavedLayoutComesBackForTheSameTable(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		found = flowTableLayouts.layoutFor(self.page())
		self.assertIsNotNone(found)
		self.assertEqual(found.columns, (1, 3))

	def test_andNotForADifferentTableOnTheSamePage(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		other = self.page(headers={1: "Criterion", 2: "Level", 3: "Remarks"})
		self.assertIsNone(flowTableLayouts.layoutFor(other))

	def test_norForAnotherPage(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		self.assertIsNone(flowTableLayouts.layoutFor(self.page(url="https://example.com/other")))

	def test_aListViewIsRememberedByItsControl(self):
		"""Which is what a reader means by laying out "the file list": one control, whichever
		folder is open."""
		flowTableLayouts.remember(self.fileList(), flowTableLayouts.TableLayout(columns=(1, 2)))
		self.assertIsNotNone(flowTableLayouts.layoutFor(self.fileList()))

	def test_aTableThatCannotBeNamedIsNotSaved(self):
		view = FakeGrid(FILES)
		handle = flowTableSource.tableAt(view.item(1))
		self.assertFalse(flowTableLayouts.remember(handle, flowTableLayouts.TableLayout(columns=(1,))))

	def test_forgettingDropsIt(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		self.assertTrue(flowTableLayouts.forget(self.page()))
		self.assertIsNone(flowTableLayouts.layoutFor(self.page()))

	def test_forgettingWhatWasNeverSavedSaysSo(self):
		self.assertFalse(flowTableLayouts.forget(self.page()))

	def test_aPlaceWithNothingSavedCostsNoReading(self):
		"""The lookup is made every time the reader walks into a table. Where nothing is
		saved — which is every table until they save one — it must not read the headings."""
		handle = self.page()
		asked = []
		real = flowTableSource.declaredHeaders
		flowTableSource.declaredHeaders = lambda *args, **kwargs: asked.append(args) or real(*args, **kwargs)
		try:
			self.assertIsNone(flowTableLayouts.layoutFor(handle))
			flowTableLayouts.remember(self.fileList(), flowTableLayouts.TableLayout(columns=(1,)))
			asked.clear()
			self.assertIsNone(flowTableLayouts.layoutFor(handle))
		finally:
			flowTableSource.declaredHeaders = real
		self.assertEqual(asked, [])

	def test_theStoreIsBounded(self):
		"""A configuration file that grows without limit is one that eventually cannot be
		written. The tables somebody reads are the ones that stay."""
		for number in range(flowTableLayouts.MAX_SAVED + 5):
			flowTableLayouts.remember(
				self.page(url=f"https://example.com/{number}"),
				flowTableLayouts.TableLayout(columns=(1,)),
			)
		stored = flowTableLayouts.stored()
		self.assertLessEqual(sum(len(inner) for inner in stored.values()), flowTableLayouts.MAX_SAVED)

	def test_whatIsStoredIsTextTheProfileCanHold(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		self.assertIsInstance(CONFIG["tableLayouts"], str)
		self.assertIn("example.com", CONFIG["tableLayouts"])
