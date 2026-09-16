# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Tests for remembering how a reader wants a table laid out.

The command that lays a table out in columns has always said its end state is "a layout
remembered against the table so that a watchlist comes up laid out". Two things had to exist
first: a name for the table that survives the page being loaded again, and a record of what
the reader decided that is not a record of how wide the cells came out on the display they
decided it on.
"""

import dataclasses
import json
import unittest

from ._stubs import (
	CONFIG,
	FakeGrid,
	FakeNavigatorObject,
	FakeTableDocument,
	installStubs,
	log,
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

	def page(self, url="https://example.com/watchlist", headers=None, rows=None):
		""":return: a table on a web page, which knows its own URL as NVDA knows it."""
		document = FakeTableDocument(
			[list(row) for row in (rows or WATCHLIST)],
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


class TestWhatOnePressOfRememberSaves(LayoutTestCase):
	"""The reader who configured nothing, which is every reader today.

	The command wrote down the columns as they were drawn, and those are a measurement rather
	than a decision: a column blank in the bandful that was sampled is not drawn, so pressing
	one key froze it out of every later reading of that table. On hardware that was a watchlist
	whose first column vanished for good, and — because the plan then had never heard of it —
	a band that rebuilt itself on every redraw while the reader stood in it.
	"""

	class Plan:
		"""A column plan, in the one attribute the command reads off it."""

		def __init__(self, columns):
			self.columns = tuple(type("Column", (), {"index": index})() for index in columns)

	def test_theRecordNamesNoColumns(self):
		layout = flowTableLayouts.layoutFrom(self.Plan((2, 3, 4)))
		self.assertEqual(layout.columns, ())

	def test_norAnythingElseTheSettingsAlreadyDecide(self):
		"""Row height, cutting, the key column and the headers are settings, and a reader who
		has said nothing about this table has said nothing."""
		self.assertTrue(flowTableLayouts.layoutFrom(self.Plan((1,))).isEmpty)

	def test_butItIsStillSavedAndFoundAgain(self):
		"""Because the record *is* the request: this table comes up in columns."""
		handle = self.page()
		self.assertTrue(flowTableLayouts.remember(handle, flowTableLayouts.layoutFrom(self.Plan((2,)))))
		self.assertIsNotNone(flowTableLayouts.layoutFor(self.page()))

	def test_andARecordThatDoesNameColumnsIsStillHonoured(self):
		"""What an older build saved, and what a way of choosing columns will save."""
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		self.assertEqual(flowTableLayouts.layoutFor(self.page()).columns, (1, 3))


class TestAStoreSomebodyElseWrote(LayoutTestCase):
	"""Found by review, and the module had promised otherwise. `fromRecord` is careful about a
	record it cannot read, and everything above it took the file on trust — so a stored `1`, or
	a place holding a string, raised inside `layoutFor` while the reader walked into a table.
	That is a braille refresh that stops rather than an error anybody sees.
	"""

	def storedIs(self, said):
		CONFIG["tableLayouts"] = said

	def test_aScalarIsNotAStore(self):
		self.storedIs("1")
		self.assertEqual(flowTableLayouts.stored(), [])
		self.assertIsNone(flowTableLayouts.layoutFor(self.page()))

	def test_norIsAString(self):
		self.storedIs('"a layout"')
		self.assertIsNone(flowTableLayouts.layoutFor(self.page()))

	def test_norIsAList(self):
		self.storedIs("[1, 2, 3]")
		self.assertIsNone(flowTableLayouts.layoutFor(self.page()))

	def test_aPlaceThatHoldsSomethingElseIsSkipped(self):
		self.storedIs('{"https://example.com/watchlist": "not a record"}')
		self.assertIsNone(flowTableLayouts.layoutFor(self.page()))

	def test_andTheOtherPlacesInItSurvive(self):
		"""One bad entry costs that entry. Dropping the store would lose every layout the
		reader has."""
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		store = json.loads(CONFIG["tableLayouts"])
		store["https://example.com/other"] = "nonsense"
		CONFIG["tableLayouts"] = json.dumps(store)
		self.assertIsNotNone(flowTableLayouts.layoutFor(self.page()))

	def test_aRecordThatIsNotOneIsSkipped(self):
		self.storedIs('{"https://example.com/watchlist": {"": 7}}')
		self.assertIsNone(flowTableLayouts.layoutFor(self.page()))

	def test_columnsThatAreNotAListAreNotIterated(self):
		"""The other half of the same fault: a scalar there raised on iteration."""
		self.assertEqual(flowTableLayouts.fromRecord({"columns": 5}).columns, ())

	def test_andAColumnNumberThatMakesNoSenseIsDropped(self):
		self.assertEqual(flowTableLayouts.fromRecord({"columns": [1, 0, -2, "3"]}).columns, (1, 3))


class TestDrawingAColumnWithoutCapitalSigns(LayoutTestCase):
	"""One more per-column decision, and it goes into the record like the rest of them."""

	def choice(self, **fields):
		from brlMultiline import flowTable

		return flowTable.ColumnChoice(**fields)

	def test_itSurvivesTheStore(self):
		layout = flowTableLayouts.TableLayout(perColumn={1: self.choice(plainCase=True)})
		read = flowTableLayouts.fromRecord(layout.asRecord())
		self.assertTrue(read.perColumn[1].plainCase)

	def test_andIsADecisionLikeAnyOther(self):
		"""So a column with nothing else said about it is still written down."""
		self.assertFalse(self.choice(plainCase=True).isEmpty)

	def test_andAColumnNobodyAskedAboutIsStillNotWrittenDown(self):
		layout = flowTableLayouts.TableLayout(perColumn={1: self.choice()})
		self.assertEqual(layout.asRecord(), {})


class TestWhatIsDecidedAboutOneColumn(LayoutTestCase):
	"""M7's vocabulary in the record: the reader's own name for a column, which end of it to
	keep, how much room it may have, and where a page begins."""

	def choice(self, **fields):
		from brlMultiline import flowTable

		return flowTable.ColumnChoice(**fields)

	def test_aColumnTheReaderSaidNothingAboutIsNotWrittenDown(self):
		layout = flowTableLayouts.TableLayout(perColumn={2: self.choice()})
		self.assertEqual(layout.asRecord(), {})

	def test_whatTheySaidIsKeptByColumnNumber(self):
		"""By number rather than by position, so a table that gains or loses a column does not
		shift everybody's settings onto their neighbours."""
		layout = flowTableLayouts.TableLayout(perColumn={2: self.choice(label="Ticker")})
		self.assertEqual(layout.asRecord(), {"perColumn": {"2": {"label": "Ticker"}}})

	def test_andComesBackTheSame(self):
		layout = flowTableLayouts.TableLayout(
			perColumn={
				1: self.choice(label="Ticker", keep="end", minWidth=8),
				3: self.choice(startsAPage=True),
			},
		)
		read = flowTableLayouts.fromRecord(layout.asRecord())
		self.assertEqual(read.perColumn[1].label, "Ticker")
		self.assertEqual(read.perColumn[1].keep, "end")
		self.assertEqual(read.perColumn[1].minWidth, 8)
		self.assertTrue(read.perColumn[3].startsAPage)

	def test_theKeyColumnIsRemembered(self):
		layout = flowTableLayouts.TableLayout(keyColumn=2)
		self.assertEqual(layout.asRecord(), {"keyColumn": 2})
		self.assertEqual(flowTableLayouts.fromRecord(layout.asRecord()).keyColumn, 2)

	def test_nonsenseInARecordIsDroppedRatherThanRaised(self):
		"""The store is read from a file a reader may have edited and a record another version
		wrote, all the way down."""
		read = flowTableLayouts.fromRecord(
			{
				"perColumn": {
					"1": {"keep": "sideways", "minWidth": "wide", "label": 7},
					"nonsense": {"label": "gone"},
					"2": "not a record",
					"0": {"label": "no such column"},
					"3": {"label": "Kept"},
				},
			},
		)
		self.assertEqual(set(read.perColumn), {3})
		self.assertEqual(read.perColumn[3].label, "Kept")

	def test_aWholeTableSavedThisWayIsFoundAgain(self):
		flowTableLayouts.remember(
			self.page(),
			flowTableLayouts.TableLayout(perColumn={1: self.choice(label="Ticker")}),
		)
		found = flowTableLayouts.layoutFor(self.page())
		self.assertEqual(found.perColumn[1].label, "Ticker")


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

	def missesLogged(self):
		return [said for level, said in log.messages if "saved table layout not applied" in said]

	def test_aPageWhoseAddressChangedSaysSoInTheLog(self):
		"""A saved layout that stops applying was silent, so a watchlist that stopped coming up laid
		out left nothing to say whether the address or the headings had moved. Reported from hardware."""
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		flowTableLayouts._explained.clear()
		log.messages.clear()
		flowTableLayouts.layoutFor(self.page(url="https://example.com/my/watchlist"))
		self.assertEqual(1, len(self.missesLogged()))
		self.assertIn("https://example.com/my/watchlist", self.missesLogged()[0])

	def test_aTableWhoseHeadingsChangedSaysWhichHeadings(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		flowTableLayouts._explained.clear()
		log.messages.clear()
		flowTableLayouts.layoutFor(self.page(headers={1: "Symbol", 2: "Price", 3: "Change"}))
		self.assertEqual(1, len(self.missesLogged()))
		self.assertIn("Price", self.missesLogged()[0])

	def test_aMissIsSaidOnceNotOnEveryRedraw(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		flowTableLayouts._explained.clear()
		log.messages.clear()
		for _ in range(5):
			flowTableLayouts.layoutFor(self.page(url="https://example.com/moved"))
		self.assertEqual(1, len(self.missesLogged()))

	def test_aLayoutThatAppliesLogsNothing(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		log.messages.clear()
		self.assertIsNotNone(flowTableLayouts.layoutFor(self.page()))
		self.assertEqual([], self.missesLogged())

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
		self.assertLessEqual(len(flowTableLayouts.stored()), flowTableLayouts.MAX_SAVED)

	def test_whatIsStoredIsTextTheProfileCanHold(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		self.assertIsInstance(CONFIG["tableLayouts"], str)
		self.assertIn("columns", CONFIG["tableLayouts"])

	def test_theAddressAndHeadingsAreKeptReadably(self):
		"""Reversed on 16 September 2026. A store of digests could not say which saved layout had been
		a watchlist's once the site changed its address, nor be pointed at the new one, so the reader
		chose to keep the full address, knowing the file then carries it."""
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		self.assertIn("https://example.com/watchlist", CONFIG["tableLayouts"])
		self.assertIn("Symbol", CONFIG["tableLayouts"])

	def test_andItIsStillFoundAgain(self):
		"""Which is the only thing the key has to do."""
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		self.assertEqual(flowTableLayouts.layoutFor(self.page()).columns, (1, 3))

	def test_anEmptySignatureStaysEmpty(self):
		"""A list view that declares no headings has only its place to be known by, and
		`layoutFor` looks for that exact key."""
		self.assertEqual(flowTableLayouts.keyFor(""), "")
		self.assertNotEqual(flowTableLayouts.keyFor("a place"), "")


class TestLayoutsSavedBeforeNamesWereKept(LayoutTestCase):
	"""The first store kept digests. A reader's layouts in it must go on working, and become
	readable the first time their table is seen, which is the only time what they belong to is known."""

	def versionOne(self, url="https://example.com/watchlist", signature="Symbol␟Last␟Change", record=None):
		CONFIG["tableLayouts"] = json.dumps(
			{
				flowTableLayouts.keyFor(url): {
					flowTableLayouts.keyFor(signature): record or {"columns": [1, 3], "used": 1756800000},
				},
			},
		)

	def test_itStillApplies(self):
		self.versionOne()
		self.assertEqual(flowTableLayouts.layoutFor(self.page()).columns, (1, 3))

	def test_andIsWrittenDownReadablyWhenItDoes(self):
		self.versionOne()
		flowTableLayouts.layoutFor(self.page())
		saved = json.loads(CONFIG["tableLayouts"])
		self.assertEqual(flowTableLayouts.STORE_VERSION, saved["version"])
		(layout,) = saved["layouts"]
		self.assertEqual("https://example.com/watchlist", layout["where"])
		self.assertEqual(["Symbol", "Last", "Change"], layout["headings"])
		self.assertEqual(flowTableLayouts.MATCH_PATH, layout["match"])
		self.assertIn("example.com/watchlist", layout["name"])

	def test_oneNotSeenYetIsKeptAsItWas(self):
		"""Until its table is seen nothing can say what it belongs to, and dropping it would lose it."""
		self.versionOne(url="https://example.com/elsewhere")
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(2,)))
		layouts = flowTableLayouts.stored()
		self.assertEqual(2, len(layouts))
		self.assertEqual(1, sum(saved.isLegacy for saved in layouts))

	def test_itsSavedDateIsKept(self):
		self.versionOne()
		(saved,) = flowTableLayouts.stored()
		self.assertEqual(1756800000, saved.saved)


class TestHowStrictlyAnAddressMatches(LayoutTestCase):
	"""A site changes its query string and nothing else: a view renumbered, a parameter added. The
	barchart watchlist stopped coming up laid out when it did. So a web page matches by site and path
	by default, and the reader can make any one layout stricter or looser."""

	URL = "https://www.example.com/my/watchlist?viewName=1"

	def saveAt(self, url=URL, match=None):
		flowTableLayouts.remember(self.page(url=url), flowTableLayouts.TableLayout(columns=(1, 3)))
		if match is not None:
			(saved,) = flowTableLayouts.stored()
			flowTableLayouts.replaceAll([dataclasses.replace(saved, match=match)])

	def found(self, url):
		return flowTableLayouts.layoutFor(self.page(url=url)) is not None

	def test_aNewWebLayoutIgnoresTheQueryString(self):
		self.saveAt()
		self.assertTrue(self.found("https://www.example.com/my/watchlist?viewName=122002"))
		self.assertTrue(self.found("https://www.example.com/my/watchlist/"))

	def test_butNotAnotherPath(self):
		self.saveAt()
		self.assertFalse(self.found("https://www.example.com/my/portfolio"))

	def test_exactlyMeansExactly(self):
		self.saveAt(match=flowTableLayouts.MATCH_EXACT)
		self.assertTrue(self.found(self.URL))
		self.assertFalse(self.found("https://www.example.com/my/watchlist?viewName=2"))

	def test_anywhereOnTheSiteMeansAnyPathThere(self):
		self.saveAt(match=flowTableLayouts.MATCH_SITE)
		self.assertTrue(self.found("https://example.com/other/page"))
		self.assertFalse(self.found("https://www.example.org/my/watchlist"))

	def test_somethingThatIsNotAWebPageMatchesExactly(self):
		flowTableLayouts.remember(self.fileList(), flowTableLayouts.TableLayout(columns=(1,)))
		(saved,) = flowTableLayouts.stored()
		self.assertEqual(flowTableLayouts.MATCH_EXACT, saved.match)

	def test_theMostParticularLayoutWins(self):
		self.saveAt()
		(loose,) = flowTableLayouts.stored()
		exact = dataclasses.replace(
			loose, id="exact", match=flowTableLayouts.MATCH_EXACT, layout={"columns": [3]}
		)
		flowTableLayouts.replaceAll([loose, exact])
		self.assertEqual((3,), flowTableLayouts.layoutFor(self.page(url=self.URL)).columns)
		self.assertEqual((1, 3), flowTableLayouts.layoutFor(self.page(url=self.URL + "0")).columns)

	def test_savingAgainChangesTheLayoutThatAppliesRatherThanAddingOne(self):
		"""Or the second would compete with the first, and whichever won would look like a fault."""
		self.saveAt()
		(saved,) = flowTableLayouts.stored()
		flowTableLayouts.replaceAll([dataclasses.replace(saved, name="My watchlist")])
		flowTableLayouts.remember(
			self.page(url="https://www.example.com/my/watchlist?viewName=9"),
			flowTableLayouts.TableLayout(columns=(3,)),
		)
		(saved,) = flowTableLayouts.stored()
		self.assertEqual("My watchlist", saved.name)
		self.assertEqual(self.URL, saved.where)
		self.assertEqual({"columns": [3], "headings": {"3": "Change"}}, saved.layout)


WITH_A_NAME = [
	["Symbol", "Name", "Last", "Change"],
	["AAPL", "Apple", "182.50", "+1.25"],
	["F", "Ford", "9.10", "-0.05"],
]


class TestTheHeadingsATableIsKnownBy(LayoutTestCase):
	def inserted(self):
		return self.page(headers={1: "Symbol", 2: "Name", 3: "Last", 4: "Change"}, rows=WITH_A_NAME)

	def test_aLayoutCanBeToldNotToMindThem(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		(saved,) = flowTableLayouts.stored()
		flowTableLayouts.replaceAll([dataclasses.replace(saved, requireHeadings=False)])
		other = self.page(headers={1: "Criterion", 2: "Level", 3: "Remarks"})
		self.assertIsNotNone(flowTableLayouts.layoutFor(other))

	def test_aColumnInsertedAmongThemIsStillTheSameTable(self):
		"""The sequence changed, the table did not: every heading saved is still there."""
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1, 3)))
		self.assertIsNotNone(flowTableLayouts.layoutFor(self.inserted()))


class TestColumnsFollowTheirHeadings(LayoutTestCase):
	"""A record names columns by number, so a site that inserts one moved every decision onto its
	neighbour, silently, while looking as though the layout had applied."""

	def setUp(self):
		super().setUp()
		from brlMultiline import flowTable

		self.choice = flowTable.ColumnChoice(plainCase=True)
		flowTableLayouts.remember(
			self.page(),
			flowTableLayouts.TableLayout(columns=(2, 3), keyColumn=1, perColumn={3: self.choice}),
		)
		log.messages.clear()
		flowTableLayouts._explained.clear()

	def inserted(self):
		return self.page(headers={1: "Symbol", 2: "Name", 3: "Last", 4: "Change"}, rows=WITH_A_NAME)

	def test_theHeadingsOfTheColumnsItNamesAreSaved(self):
		(saved,) = flowTableLayouts.stored()
		self.assertEqual({"1": "Symbol", "2": "Last", "3": "Change"}, saved.layout["headings"])

	def test_aColumnInsertedBeforeThemMovesEveryDecisionWithIt(self):
		layout = flowTableLayouts.layoutFor(self.inserted())
		self.assertEqual((3, 4), layout.columns)
		self.assertEqual(1, layout.keyColumn)
		self.assertEqual({4: self.choice}, layout.perColumn)

	def test_andSaysSoInTheLogOnce(self):
		inserted = self.inserted()
		for _ in range(3):
			flowTableLayouts.layoutFor(inserted)
		said = [message for _level, message in log.messages if "have moved" in message]
		self.assertEqual(1, len(said))
		self.assertIn("'Last' from column 2 to 3", said[0])

	def test_aColumnThatHasNotMovedIsNotReadAgainForEveryColumn(self):
		"""Asked on every redraw, so the table is only searched when a heading is not where it was."""
		from brlMultiline import flowTableSource

		page = self.page()
		asked = []
		real = flowTableSource.declaredHeaders
		flowTableSource.declaredHeaders = lambda handle, columns, *args, **kwargs: (
			asked.append(list(columns)) or real(handle, columns, *args, **kwargs)
		)
		try:
			flowTableLayouts.layoutFor(page)
			first = len(asked)
			flowTableLayouts.layoutFor(page)
		finally:
			flowTableSource.declaredHeaders = real
		# The second lookup reads the table's identity, as every lookup does, and nothing more.
		self.assertEqual(1, len(asked) - first)

	def test_aColumnWhoseHeadingIsNowhereKeepsItsNumber(self):
		"""Right for a column the site renamed, and logged either way."""
		renamed = self.page(headers={1: "Symbol", 2: "Last price", 3: "Change"})
		(saved,) = flowTableLayouts.stored()
		flowTableLayouts.replaceAll([dataclasses.replace(saved, requireHeadings=False)])
		self.assertEqual((2, 3), flowTableLayouts.layoutFor(renamed).columns)
		self.assertTrue(any("no longer found" in message for _level, message in log.messages))


class TestWhenALayoutWasLastUsed(LayoutTestCase):
	"""What tells a layout still in use from one whose page has gone."""

	def test_applyingItWritesTheDateAtMostOnceADay(self):
		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1,)))
		real = flowTableLayouts._now
		try:
			start = real()
			flowTableLayouts._now = lambda: start + 60
			flowTableLayouts.layoutFor(self.page())
			self.assertEqual(start, flowTableLayouts.stored()[0].used)
			flowTableLayouts._now = lambda: start + flowTableLayouts.USED_EVERY + 60
			flowTableLayouts.layoutFor(self.page())
			self.assertEqual(start + flowTableLayouts.USED_EVERY + 60, flowTableLayouts.stored()[0].used)
		finally:
			flowTableLayouts._now = real

	def test_theLeastRecentlyUsedGoFirst(self):
		layouts = [
			flowTableLayouts.SavedLayout(id=str(number), where=f"https://example.com/{number}", used=number)
			for number in range(flowTableLayouts.MAX_SAVED + 5)
		]
		flowTableLayouts.replaceAll(layouts)
		kept = {saved.id for saved in flowTableLayouts.stored()}
		self.assertNotIn("0", kept)
		self.assertIn(str(flowTableLayouts.MAX_SAVED + 4), kept)


class TestLayoutsAProfileHeld(LayoutTestCase):
	"""Written through NVDA's configuration, a layout saved while an application's profile was on went
	into that profile and hid every other layout while it was on. They are moved into the one store."""

	def test_theyAreMovedIntoTheStoreAndOutOfTheProfile(self):
		from ._stubs import PROFILE_TABLE_LAYOUTS

		flowTableLayouts.remember(self.page(), flowTableLayouts.TableLayout(columns=(1,)))
		held = flowTableLayouts.SavedLayout(id="outlook", where="outlook/SUPERGRID", layout={"columns": [2]})
		PROFILE_TABLE_LAYOUTS["outlook"] = json.dumps({"version": 2, "layouts": [held.asStored()]})
		self.assertEqual(2, len(flowTableLayouts.stored()))
		self.assertEqual({}, PROFILE_TABLE_LAYOUTS)
		self.assertIn("outlook/SUPERGRID", CONFIG["tableLayouts"])
