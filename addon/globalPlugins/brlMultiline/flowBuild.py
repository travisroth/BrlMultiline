# BrlMultiline: building a flow over what the reader is in.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Turning "the reader is on this object" into a flow that can be shown.

Everything here answers one question in stages: what should be read, how should it be cut
into blocks, which region class reads it, and which of those blocks the reader has arrived
at. L{buildController} is the whole of that assembled, and it is what the live band calls
for every document a reader enters — see `flowBand.refresh`.

The same construction serves both flavours a flow comes in. A live flow is the reader's
own: it shows a cursor and moves the browse mode cursor when it pans. A viewer moves
nothing outside itself, and is what `flowDryRun` builds to report what a band *would* hold
without disturbing the page it is reporting on. The difference is one `live` flag threaded
through to the region factories; nothing else here knows which it is making.

This code lived in `flowDryRun` until it was split out, because the dry run was written
first and had to construct a flow in order to log one. Importing it there was better than
duplicating it, but it left the live path's whole front end in a file named for a
diagnostic. Nothing moved in the split but the functions themselves.
"""

import dataclasses
from typing import TYPE_CHECKING, Optional

import api
import config
import textInfos
from braille.regions.focus import getFocusRegions
from braille.regions.textInfo import TextInfoRegion
from logHandler import log

from . import bmConfig, flowForms, flowObjects, flowTable, flowTableLayouts, flowTableSource
from .flow import CallCancelled
from .flowControl import FlowController
from .flowRender import FlowRenderer
from .flowSources import (
	DocumentFlowSource,
	budgetForBand,
	documentFor,
	regionFactoryFor,
	regionFactoryForObject,
)

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject

DEFAULT_ROWS = 8
DEFAULT_COLS = 32


def readingUnit() -> str:
	""":return: the unit a block is, following NVDA's read by paragraph setting."""
	try:
		byParagraph = config.conf["braille"]["readByParagraph"]
	except Exception:
		byParagraph = False
	return textInfos.UNIT_PARAGRAPH if byParagraph else textInfos.UNIT_LINE


def readingUnitFor(target) -> str:
	"""What one block of a document is.

	The reader's own setting, except in a multi line edit they are writing in, where the
	add-on's own `flowWriteByParagraph` setting decides. On, which it is by default, such an
	edit is cut into paragraphs whatever NVDA's read by paragraph setting says: a paragraph
	is what the writer typed and a line is what the control's wrapping made of it, and the
	writer thinks in the first.

	The evidence is stronger than the argument. Asked what the line at the caret is, a rich
	editor answers with a single character for a moment after each return, and with
	everything from the start of the document up to the caret when it is asked through a
	braille region — NVDA's own region shows that too. Asked what the *paragraph* is, the
	same control at the same moment answers with exactly the line the reader is on, every
	time. A unit that is only sometimes a unit cannot be a block.

	That evidence comes from one editor, though, which is why it is a setting rather than a
	rule: turning it off puts an edit back on the same footing as a page, so the difference
	can be tried in a control the default was never chosen from.

	:param target: the document being read.
	:return: the reading unit to cut it into blocks by.
	"""
	if (
		bmConfig.shouldWriteByParagraph()
		and isBeingWrittenIn(target, target)
		and flowForms.isMultilineEditable(target)
	):
		return textInfos.UNIT_PARAGRAPH
	return readingUnit()


def bandSize(handler) -> tuple[int, int]:
	""":return: the rows and columns to lay a flow out in, from the whole display.

	The last resort, and worth knowing as such. On a composite display this is the whole
	rectangle — nine rows of eighty, for a Monarch with a Focus 80 under it — and a band
	never gets that: it must lie inside one physical display's live cells, so it is the
	Monarch's eight rows of thirty two. A diagnostic laid out at the whole rectangle is
	measuring a display nobody has.

	It matters most for indent, which is a function of the width from end to end: at eighty
	cells seven levels of true depth cost twelve and are drawn, at thirty two they cost more
	than the share allows and the band rebases. Reported at the wrong width, the diagnostic
	answers a question the reader was not asking. So callers that can name the real band pass
	it — see `flowDryRun.dryRun` — and this stands in only when there is no band to ask.
	"""
	dimensions = getattr(handler, "displayDimensions", None)
	numRows = getattr(dimensions, "numRows", 0) or DEFAULT_ROWS
	numCols = getattr(dimensions, "numCols", 0) or DEFAULT_COLS
	return numRows, numCols


def describeObject(obj) -> str:
	""":return: enough about an object to tell one failure from another in a log."""
	role = getattr(obj, "role", None)
	name = getattr(obj, "name", None)
	return f"{type(obj).__name__} role={role} name={name!r}"


def templateRegion(obj, notes: Optional[list] = None) -> Optional[TextInfoRegion]:
	"""Ask NVDA which kind of region this object wants.

	Taken from NVDA rather than decided here, so that the dry run flows what a flow would
	flow. An object NVDA presents without a text region has no lines to read.

	:param obj: the object or tree interceptor to present.
	:param notes: a list to record what happened in, for the report.
	:return: the text region NVDA built, or None if it built none.
	"""
	found = None
	seen = []
	try:
		for region in getFocusRegions(obj, review=False):
			seen.append(type(region).__name__)
			if isinstance(region, TextInfoRegion):
				found = region
	except Exception as error:
		log.debugWarning("Could not ask NVDA for regions", exc_info=True)
		if notes is not None:
			notes.append(f"getFocusRegions raised {error!r}")
	if notes is not None:
		notes.append(f"getFocusRegions gave {seen or 'nothing'}")
	return found


def isBeingWrittenIn(target, obj) -> bool:
	"""Whether the reader is working inside this content rather than reading it.

	Decides whether a run of blank lines collapses to one row or keeps every one, which is
	decision 6: blank lines are layout while reading and are the document while writing.
	The test is interaction, not role — the same textarea is both at different moments —
	and interaction shows up here as browse mode having stood aside, either because there
	is no tree interceptor or because it has gone to focus mode for this control.

	:param target: what will be read.
	:param obj: the object the reader is on.
	:return: whether blank lines should be kept.
	"""
	interceptor = getattr(obj, "treeInterceptor", None)
	editable = flowForms.isEditableObject(obj)
	if interceptor is not None:
		# The page is what is flowed either way — see `flowSources.documentFor` — so which
		# it is cannot be read off the target any more. Browse mode standing aside for an
		# editable control is the reader writing in it, and browse mode presenting the page
		# is the reader reading it.
		try:
			return bool(interceptor.passThrough) and editable
		except Exception:
			log.debugWarning("Could not tell whether browse mode has stood aside", exc_info=True)
			return False
	return editable


def interactiveRegionFactory(target, obj, live: bool, template=None, notes: Optional[list] = None):
	"""Build the region factory for an edit inside a browse-mode document.

	The document continues to own placement and neighbouring blocks. The focused edit owns
	only the active block's text and cursor, which makes the region's ``obj`` match the object
	NVDA names in caret and value-change events.

	:param target: the surrounding document being flowed.
	:param obj: the focused object.
	:param live: whether the resulting region may move the real caret.
	:param template: the text region NVDA already built for the edit, when available.
	:param notes: diagnostic notes for the dry run.
	:return: a flow-region factory, or None when this is not an active embedded edit.
	"""
	if obj is None or target is obj or not isBeingWrittenIn(target, obj):
		return None
	if template is None:
		template = templateRegion(obj, notes)
	try:
		return (
			regionFactoryFor(template, live=live)
			if template is not None
			else regionFactoryForObject(obj, live)
		)
	except TypeError as error:
		if notes is not None:
			notes.append(f"The focused edit has no text region: {error}")
		return None


def objectAdapterFor(obj, target=None):
	"""Whether an object is read as a run of objects rather than as a document.

	A document wins wherever there is one. A list item inside a web page is part of that
	page, and the page has the more useful context: its heading, what came before the list,
	what follows it. A run of objects is for what has no document behind it at all — a list
	box in a dialog, a menu, the choices of a combo box the reader has opened.

	:param obj: what the reader is on.
	:param target: the document it resolved to, read here when not given.
	:return: the adapter, or None to read a document or nothing.
	"""
	target = documentFor(obj) if target is None else target
	if target is not obj:
		return None
	if isTreeInterceptor(obj):
		return None
	return flowObjects.adapterFor(obj)


def isTreeInterceptor(obj) -> bool:
	""":return: whether an object is a browse mode document rather than something in one."""
	try:
		from treeInterceptorHandler import TreeInterceptor

		return isinstance(obj, TreeInterceptor)
	except ImportError:
		# Outside a running NVDA. A tree interceptor is the thing that can stand aside for the
		# control the reader has entered, which is what `passThrough` says.
		return hasattr(obj, "passThrough")


def _objectController(
	obj,
	adapter,
	numRows: int,
	numCols: int,
	handler,
	live: bool,
	generation: int,
	notes: list,
) -> Optional[FlowController]:
	"""Build a flow over a run of objects. See `buildController`."""
	source = flowObjects.ObjectFlowSource(
		obj,
		adapter,
		flowObjects.regionFactory(live=live, adapter=adapter),
		generation=generation,
		budget=budgetForBand(numRows),
	)
	notes.append(
		f"Reading objects, band {numRows} rows of {numCols} cells, budget {source.budget.maxBlocks} blocks.",
	)
	renderer = FlowRenderer(handler, numCols=numCols, fillRows=False)
	# The reader's own flow, and still one that moves nothing: the equivalent of a reading
	# position here is a selection, and a selection is application state. `live` says the
	# focused block shows a cursor, so the reader can feel which of the eight the arrow keys
	# will act on; `movesCursor` says that panning past it does not claim they have moved.
	control = FlowController(
		source,
		renderer,
		numRows=numRows,
		live=live,
		movesCursor=False,
		indentStyle=bmConfig.flowIndentStyle(),
		lineFocus=bmConfig.shouldMarkLineFocus(),
	)
	if not control.enterAtCursor():
		result = control.lastResult
		kind = getattr(getattr(result, "kind", None), "value", "no answer")
		notes.append(f"The run gave nothing to read: {kind} {getattr(result, 'message', '')}")
		return None
	return control


class Unreadable(str):
	"""A note that also says the table could not be *read*, not that it would not lay out.

	**Two failures that reached the reader as one sentence.** "This table could not be laid
	out in columns" is true of a table whose columns will not fit a band, and it was also what
	was said about an Excel sheet where every single read had been cancelled by the watchdog:
	nothing had been measured, so nothing could be planned, so the plan came back empty and
	the arithmetic was blamed. They are different things to be told — one is about this
	display and is permanent until something changes, the other is a moment that has passed
	and is worth trying again — so the note that explains a failure says which it was.

	A string, because that is what a note is and what the log wants. The type is the whole of
	the extra meaning: `flowBand.layOutTable` looks for one of these among the notes.
	"""


def buildTableController(
	obj: Optional["NVDAObject"] = None,
	numRows: int = DEFAULT_ROWS,
	numCols: int = DEFAULT_COLS,
	handler=None,
	live: bool = False,
	generation: int = 0,
	maxRows: Optional[int] = None,
	notes: Optional[list] = None,
	layout: Optional["flowTableLayouts.TableLayout"] = None,
	handle=None,
	atRow: Optional[int] = None,
	atPage: int = 0,
) -> Optional[FlowController]:
	"""Build a flow that reads the table the reader is in, laid out in columns.

	Separate from `buildController` rather than a branch inside it, because it answers a
	different question. `buildController` asks "what is here and how is it read"; this is
	asked only when the reader has said "read *this* in columns", and if they are not in a
	table the answer is no rather than some other reading — the caller has an ordinary flow
	to fall back to and does not want a second guess at it.

	The order matters and is the whole of the arrangement: recognise the table, measure a
	bandful of it, decide the columns from the measurements, and only then build a source
	that fetches exactly those columns. Measuring after planning would need the plan it is
	for, and fetching before planning would read columns the plan then dropped.

	:param obj: what the reader is on. Defaults to the focus.
	:param numRows: the height of the band.
	:param numCols: its width, which is what the columns are fitted into.
	:param handler: the braille handler the renderer lays out through.
	:param live: whether the flow may move the real cursor.
	:param generation: distinguishes this reading from an earlier one.
	:param maxRows: how many band rows one table row may use. The reader's setting.
	:param notes: a list to record each step in, so that a failure says which step failed.
	:param layout: what the reader saved for this table, or None to follow the settings.
	:param handle: the table, where the caller has already found it. Recognising one is a
		read of the document at the caret, and the band has just done it to decide whether to
		call this at all — a review counted the same question asked four times for one
		redraw. None asks for it here, which is what a caller with only an object has.
	:param atRow: which row of the table to place the window at, or None for the caret's. What
		a rebuild passes so the reader keeps the rows they had panned to: a new controller
		enters at the caret, and a rebuild is not something they asked for.
	:param atPage: which page of columns to show. Applied before the controller is handed
		back, so the band is written once — a review found a rebuild writing page one and then
		the reader's page, with the driver sending both.
	:return: the controller, or None if the reader is not in a table this can lay out.
	"""
	if notes is None:
		notes = []
	if obj is None:
		obj = api.getFocusObject()
	if handle is None:
		handle = flowTableSource.tableAt(obj)
	if handle is None:
		notes.append("Not in a table, or the table is one NVDA presents as page layout.")
		return None
	notes.append(f"Reading a table: {handle!r}")
	# What the reader decided about *this* table, where they decided anything. Every field of
	# it can say "not my business", and an absent record says that of all of them — so the
	# settings are what answer here exactly as they did before there were records at all. See
	# `flowTableLayouts`.
	saved = layout if layout is not None else flowTableLayouts.TableLayout()
	if layout is not None:
		notes.append(f"Using the layout saved for this table: {saved.asRecord()}")
	# The header row is held above the window, so the window is one row shorter. Decided here
	# and not later: it is the height everything below is planned against, and a band whose
	# height changed while it was being read would move every row the reader had found.
	headers = saved.headersOr(bmConfig.shouldPinTableHeaders()) and handle.numRows > 1 and numRows > 2
	# **Measured the way it will be drawn.** A column the reader has asked for without capital
	# signs is two cells narrower than its text says, and measuring it with them would size it
	# for the cells the setting exists to save. See `flowTableSource.withoutCapitals`.
	plainCase = frozenset(
		column for column, choice in (saved.perColumn or {}).items() if choice.plainCase
	)
	try:
		everything = flowTableSource.measure(handle, live=False, plainCase=plainCase)
	except CallCancelled:
		# NVDA stopped waiting on the application in the middle of measuring, so what came
		# back is silence rather than an empty table. Said as such, and not retried here: the
		# core is busy, and asking again from inside the same command is asking the thing that
		# is already too slow to do it twice. See `flow.CallCancelled`.
		notes.append(
			Unreadable(
				"NVDA cancelled the reads while measuring this table, because the core had "
				"stopped answering. Nothing was measured, so nothing could be laid out.",
			),
		)
		return None
	measured = _asTheReaderWantsThem(everything, saved, notes)
	# The columns the reader's own layout leaves out. Not drawn, and *known*: a column the
	# plan has never heard of is evidence the table changed under it, and hardware found the
	# band rebuilding on every redraw because the caret sat in a column the saved layout had
	# dropped. See `flowTable.ColumnPlan.excluded`.
	kept = {item.index for item in measured}
	excluded = tuple(item.index for item in everything if item.index not in kept)
	# Twice at most, and the second time only to give a row back. Whether there is a header to
	# pin cannot be known before the columns are chosen — a table's headers are declared by its
	# cells, and which cells are read is what the plan decides — so the row is reserved, the
	# header asked for, and the whole arrangement made again at full height if the answer is
	# that this table has no headings. A list view is the case: it declares none and its first
	# row is a file rather than a heading, so the band was a row shorter for nothing.
	for spendARowOnHeaders in (True, False) if headers else (False,):
		bandRows = numRows - 1 if spendARowOnHeaders else numRows
		plan = flowTable.planFor(
			measured,
			numCols,
			maxRows=saved.rowHeightOr(bmConfig.tableRowHeight()) if maxRows is None else maxRows,
			overflow=flowTable.TRUNCATE
			if saved.truncateOr(bmConfig.shouldTruncateTableCells())
			else flowTable.WRAP,
			# From the band's height, because what a row costs is only meaningful beside how
			# many of them there is room for. See `flowTable.targetHeightFor`.
			targetHeight=flowTable.targetHeightFor(bandRows),
			pinKey=saved.pinKeyOr(bmConfig.shouldPinKeyColumn()),
			excluded=excluded,
			# What the reader decided about individual columns: their own name for one, which
			# end of it to keep, how much room it may have, where a page begins. Empty for a
			# table nobody has arranged, which is every table until they do.
			choices=saved.perColumn,
			keyColumn=saved.keyColumn or None,
		)
		if plan.isEmpty:
			# **Which of the two happened**, because a plan is empty either when the columns
			# will not fit the band or when there were no columns to fit. The second is not an
			# arithmetic failure and telling the reader it was sends them to the layout
			# designer for a table nothing could be read out of. See `Unreadable`.
			if not any(item.wants for item in everything):
				notes.append(
					Unreadable(
						f"Nothing was read from any of this table's {handle.numCols} "
						"columns, so there was nothing to lay out. Either it holds nothing "
						"at the rows that were measured, or the reads failed.",
					),
				)
			else:
				notes.append("No column layout fits this table on this band.")
			return None
		source = flowTableSource.TableFlowSource(
			handle,
			# The first page only. Every cell is a search of the document, and the columns on
			# the other pages are ones nobody is looking at yet; `FlowBand._useColumnPage`
			# hands over the next page's when the reader gets there.
			columns=tuple(place.column.index for place in plan.placements()),
			# What the measurement already found, so that the header row is pinned from the
			# same reading the columns were named from. Asking again here read one cell per
			# column at the caret's row, and a table whose first row was half built came out
			# with headers in the layout and no header row above them.
			declared={item.index: item.label for item in measured if item.declared},
			generation=generation,
			budget=budgetForBand(bandRows),
			live=live,
			# The whole table's, not this page's: a page turn hands the source different
			# columns and the reader's decisions are about the table.
			plainCase=plainCase,
			# The header is drawn above the window when it is pinned, and the source decides
			# what that costs the stream: row one is skipped only where row one is what was
			# pinned. A table that declares its headers has not necessarily put them there.
			pinHeaders=spendARowOnHeaders,
		)
		pinned = source.headerBlock() if spendARowOnHeaders else None
		headers = pinned is not None
		if headers or not spendARowOnHeaders:
			break
		notes.append("This table has no header row, so the band keeps the row one would cost.")
	notes.append(f"Columns: {flowTable.describe(plan)}")
	renderer = FlowRenderer(handler, numCols=numCols, fillRows=True, columnPlan=plan)
	control = FlowController(
		source,
		renderer,
		numRows=bandRows,
		live=live,
		# A table row is a place, not a selection: the reader arrives at one by moving the
		# caret, and panning past it is reading rather than moving. The same answer a run of
		# objects gives, for the same reason.
		movesCursor=False,
		indentStyle=bmConfig.flowIndentStyle(),
		lineFocus=bmConfig.shouldMarkLineFocus(),
	)
	if headers:
		# Already built, above, since whether it exists is what decided the band's height.
		control.setPinned(pinned)
		notes.append("The header row is pinned above the band.")
	if not _entered(control, source, handle, atRow):
		notes.append("The table was recognised but its first row could not be read.")
		return None
	if atPage:
		# Before the caller attaches it, so the reader feels one display rather than two.
		control.useColumnPage(plan.onPage(atPage))
	return control


def _entered(control, source, handle, atRow: Optional[int]) -> bool:
	"""Place the window, at a given row where one was asked for.

	`enterAtCursor` reads the source's idea of where the reader is, which is the caret's row. A
	rebuild wants the row the *window* was on instead — the reader may have panned a long way
	from the caret, and having that undone by something they did not ask for is the fault this
	exists for. So the source is pointed at that row for the length of the placement and put
	back afterwards: the cursor still belongs to the caret, and only the window moves.

	:param control: the controller being built.
	:param source: its source.
	:param handle: the table, as the source knows it.
	:param atRow: the row to place at, or None for the caret's own.
	:return: whether anything is on the band.
	"""
	if atRow is None or handle is None or not 1 <= atRow <= handle.numRows:
		return control.enterAtCursor()
	try:
		source.moveTo(dataclasses.replace(handle, row=atRow))
		entered = control.enterAtCursor()
	except Exception:
		log.debugWarning("Could not place a rebuilt table where the reader had it", exc_info=True)
		entered = control.enterAtCursor()
	finally:
		source.moveTo(handle)
	return entered


def _asTheReaderWantsThem(measured, saved, notes: list):
	""":return: the measured columns, cut down and ordered as a saved layout asks.

	**The reader's order, and only the columns they kept.** A saved layout that names columns
	is a reader saying "these, like this" — the four of a watchlist they actually watch, in
	the order they read them — and everything below this is already written to draw the
	columns it is given in the order it is given them.

	A column the table no longer has is dropped rather than drawn empty: a page regenerated
	with one column fewer is the ordinary way a saved layout meets a table that has changed,
	and a layout that survives it minus a column is worth more than one that refuses.

	:param measured: the columns as `flowTableSource.measure` found them.
	:param saved: the layout the reader saved.
	:param notes: where to record what was dropped, for the report.
	"""
	if not saved.columns:
		return measured
	byIndex = {item.index: item for item in measured}
	wanted = [byIndex[index] for index in saved.columns if index in byIndex]
	if not wanted:
		notes.append("The saved layout names no column this table has, so all of them are drawn.")
		return measured
	missing = [index for index in saved.columns if index not in byIndex]
	if missing:
		notes.append(f"The saved layout names columns this table has not got: {missing}")
	return wanted


def buildController(
	obj: Optional["NVDAObject"] = None,
	numRows: int = DEFAULT_ROWS,
	numCols: int = DEFAULT_COLS,
	handler=None,
	live: bool = False,
	generation: int = 0,
	notes: Optional[list] = None,
	atObject: Optional["NVDAObject"] = None,
	atRegion=None,
) -> Optional[FlowController]:
	"""Build a flow over an object, ready to be asked what it would show.

	:param obj: what to read. Defaults to the navigator object.
	:param numRows: the height of the band to lay out for.
	:param numCols: its width.
	:param handler: the braille handler the renderer lays out through.
	:param live: whether the flow may move the real cursor. False for a dry run.
	:param generation: distinguishes this reading from an earlier one, so that a bookmark
		from a document that has been left can never match one in the document now being
		read. A caller that follows the focus from document to document must pass a new
		one each time.
	:param notes: a list to record each step in, so that a failure says which step failed.
	:param atObject: a form control the reader has arrived at, read at its own place in the
		document rather than at the cursor. See `flowForms`.
	:param atRegion: the text region NVDA built for that object, used while editing so caret
		events reach the active block.
	:return: the controller, or None if this object has nothing to flow.
	"""
	if notes is None:
		notes = []
	if obj is None:
		obj = api.getNavigatorObject()
		notes.append(f"navigator object: {describeObject(obj)}")
	if obj is None:
		notes.append("There is no navigator object.")
		return None
	target = documentFor(obj)
	if target is obj:
		notes.append("No tree interceptor was substituted; reading the object itself.")
	else:
		notes.append(f"Reading through the tree interceptor: {describeObject(target)}")
	adapter = objectAdapterFor(obj, target)
	if adapter is not None:
		notes.append(f"Reading a run of objects, by the {adapter.name} adapter.")
		return _objectController(
			obj=obj,
			adapter=adapter,
			numRows=numRows,
			numCols=numCols,
			handler=handler,
			live=live,
			generation=generation,
			notes=notes,
		)
	template = templateRegion(target, notes)
	try:
		if template is not None:
			factory = regionFactoryFor(template, live=live)
		else:
			# NVDA offered no text region. Worth reading anyway rather than giving up, and
			# worth saying so, since for a browse mode document it should not happen.
			notes.append("No text region from NVDA; choosing one from the object instead.")
			factory = regionFactoryForObject(target, live=live)
	except TypeError as error:
		notes.append(f"Nothing to flow: {error}")
		return None
	source = DocumentFlowSource(
		target,
		factory,
		unit=readingUnitFor(target),
		generation=generation,
		# The source's ordinary policy belongs to the document itself. An edit inside a
		# browse-mode document turns it on only while that edit is active, through
		# `setInteractiveObject`; otherwise leaving the first field would make the whole page
		# keep blank layout lines forever.
		interactive=isBeingWrittenIn(target, target),
		# Sized to the band: filling eight rows costs at least eight blocks, so a budget near
		# the band's own height is one the reader meets on every arrival. See `budgetForBand`.
		budget=budgetForBand(numRows),
	)
	interactiveFactory = interactiveRegionFactory(
		target,
		atObject,
		live=live,
		template=atRegion,
		notes=notes,
	)
	if interactiveFactory is not None:
		source.setInteractiveObject(atObject, interactiveFactory)
	notes.append(
		f"Reading by {source.unit}, band {numRows} rows of {numCols} cells, "
		f"budget {source.budget.maxBlocks} blocks.",
	)
	if atObject is not None:
		notes.append(f"Arrived at a control: {describeObject(atObject)}")
	renderer = FlowRenderer(handler, numCols=numCols, fillRows=False)
	control = FlowController(
		source,
		renderer,
		numRows=numRows,
		live=live,
		indentStyle=bmConfig.flowIndentStyle(),
		lineFocus=bmConfig.shouldMarkLineFocus(),
	)
	if not control.enterAtCursor(atObject=atObject):
		result = control.lastResult
		kind = getattr(getattr(result, "kind", None), "value", "no answer")
		notes.append(
			f"The source could not give the block at the cursor: {kind} {getattr(result, 'message', '')}"
		)
		return None
	return control
