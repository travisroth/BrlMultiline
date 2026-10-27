# BrlMultiline: reading a flow into the log instead of onto a display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Writing what a flow would show to the log, and what one has cost.

Runs the whole stack over the document the reader is in and reports what the band would
hold, claiming nothing and writing nothing to the hardware. A viewer, so it never moves the
reader's place. Three windows are reported: arrival, panned forward, and panned back, which
should equal the first. Built by `flowBuild`, as the live band is.
"""

import time
from typing import TYPE_CHECKING

import api
import braille
import textInfos
from logHandler import log

from . import bmConfig, flowForms
from .flowBuild import bandSize, buildController, describeObject
from .flowControl import FlowController
from . import flowTable
from .flowIndent import FOCUS_CELL, FOCUS_WIDTH

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject


def _focusObject():
	""":return: where the system focus is, or None if it cannot be read."""
	try:
		return api.getFocusObject()
	except Exception:
		log.debugWarning("Could not read the focus object", exc_info=True)
		return None


def howTheLayoutWasMade(band) -> list[str]:
	""":return: what `buildTableController` said as it made the layout on the band.

	Shown even when the layout succeeded, since a wrong layout is not a failed one.

	:param band: the live band.
	"""
	notes = list(getattr(band, "tableNotes", None) or ())
	if not notes:
		return []
	return ["Band layout, as it was made:", *(f"  {note}" for note in notes)]


def liveReport(band) -> list[str]:
	"""What the band is showing at this moment, as opposed to what a fresh flow would show.

	A dry run's own flow says nothing about the reader's window. This gives the live band's
	window, its anchor and entry edge, and each row. The entry matters for scrolling: `top`
	filled downward from the anchored block, `bottom` filled upward behind it.

	:param band: the live band, or None.
	:return: the lines of the account.
	"""
	control = getattr(band, "controller", None) if band is not None else None
	if control is None:
		# The pins still matter here, and this is the case a reader most often asks about:
		# a pin is set from a display with no flow on it.
		return ["The band is not showing a flow, so there is nothing it is holding.", *describePins()]
	lines = [f"Band flow: {control!r}", f"Band source: {control.source!r}"]
	anchor = getattr(control.window, "anchor", None)
	if anchor is not None:
		entry = getattr(anchor.entry, "value", anchor.entry)
		lines.append(f"Band anchor: entered from the {entry}, at row {anchor.rowIndex} of its block")
	lines.append(f"Band indent: {describeIndent(control)}")
	lines.append(f"Band line focus: {describeLineFocus(control)}")
	lines.append(f"Band live updates: {describeLiveUpdates(band)}")
	lines.append(f"Band edges: {describeEdges(control)}")
	onShow = describeRowsOnShow(control)
	if onShow is not None:
		lines.append(f"Band rows: {onShow}")
	# The band's own numbers. Everything under `Cost` below belongs to the controller this
	# command builds to answer with, which reads the same document and has a budget of its
	# own — so a reader diagnosing a band that keeps saying "more, not fetched" was being
	# shown the cost of something else entirely.
	lines.extend(f"  Band cost, {line}" for line in describeCost(control))
	lines.extend(describePins())
	plan = getattr(getattr(control, "renderer", None), "columnPlan", None)
	if plan is not None and not plan.isEmpty:
		lines.append(f"Band columns: {flowTable.describe(plan)}")
	lines.extend(howTheLayoutWasMade(band))
	lines.extend(describeObjectTable(control))
	lines.append(f"Band direction: {getattr(control, 'lastDirection', 'unknown')}")
	# Every move, in order, because the direction test's verdict on its own was misleading:
	# on the report that located the last bug the verdict was right while the placement was
	# wrong, because something else had moved the window before the verdict was used.
	moves = getattr(control, "placements", None) or ["nothing has moved the band yet"]
	lines.append("Band moves, oldest first:")
	lines.extend(f"  {move}" for move in moves)
	active = control.activeBlockId
	lines.append(f"Band active block: {'none' if active is None else active.bookmark!r}")
	try:
		lines.extend(f"  {line}" for line in control.describeRows())
	except Exception as error:
		log.debugWarning("Could not describe what the band is holding", exc_info=True)
		lines.append(f"  could not be described: {error!r}")
	lines.extend(whatTheDocumentItselfSays(control))
	return lines


DOCUMENT_LINES = 10
"""How many of the document's own lines the report reads around the caret.

Enough to see a pattern — a blank between every cell shows in four — and bounded because
this runs while the reader waits, on a page that may be large.
"""


def whatTheDocumentItselfSays(control) -> list:
	""":return: the document's own lines around the caret, read straight from NVDA.

	Fetched as NVDA's own line movement does, so a blank row can be told to be the document's
	or the flow's. Each line has its offsets, what
	`documentBase.DocumentWithTableNavigation._inTable` says of it, and its text. Walks a copy
	of the caret's position; nothing moves.

	:param control: the flow whose document to ask.
	"""
	document = getattr(getattr(control, "source", None), "obj", None)
	if document is None or not hasattr(document, "makeTextInfo"):
		return []
	try:
		here = document.makeTextInfo(textInfos.POSITION_SELECTION)
	except Exception as error:
		log.debugWarning("Could not read the document's own caret line", exc_info=True)
		return [f"What the document itself says: it would not give its caret position ({error!r})."]
	found = ["What the document itself says, from the caret onward:"]
	for number in range(DOCUMENT_LINES):
		try:
			here.expand(textInfos.UNIT_LINE)
			found.append(f"  {number}: {_describeDocumentLine(document, here)}")
			here.collapse()
			if here.move(textInfos.UNIT_LINE, 1) != 1:
				found.append("  (the document ends here)")
				break
		except Exception as error:
			log.debugWarning("Could not walk the document's own lines", exc_info=True)
			found.append(f"  (the walk stopped: {error!r})")
			break
	return found


def _describeDocumentLine(document, info) -> str:
	""":return: one line of a document, as NVDA itself holds it.

	:param document: the tree interceptor or object being read.
	:param info: the position, expanded to the line.
	"""
	try:
		text = info.text
	except Exception:
		text = "<could not be read>"
	where = ""
	asked = getattr(document, "_getTableCellCoords", None)
	if asked is not None:
		try:
			cell = asked(info)
			where = f" table {cell.tableID} row {cell.row} column {cell.col}"
		except LookupError:
			where = " not in a table cell"
		except Exception:
			where = " could not be asked about a table cell"
	return f"{_offsetsOf(info)}{where}: {text!r}"


def _offsetsOf(info) -> str:
	""":return: where a position is, by whatever the document identifies positions with."""
	try:
		return f"{info.bookmark!r}"
	except Exception:
		return "<no bookmark>"


def describeObjectTable(control) -> list:
	""":return: what a table made of objects found, or nothing if this is not one.

	Each of its answers is a choice of where to ask, which a report must show. See
	`flowObjectTable.ObjectTable.describe`.
	"""
	source = getattr(control, "source", None)
	document = getattr(source, "handle", None)
	describe = getattr(getattr(document, "document", None), "describe", None)
	if not callable(describe):
		return []
	try:
		lines = ["Band object table:", *describe()]
	except Exception as error:
		log.debugWarning("Could not describe an object table", exc_info=True)
		lines = [f"Band object table: could not be described: {error!r}"]
	header = getattr(source, "describeHeader", None)
	if callable(header):
		try:
			lines.append(f"  pinned header: {header()}")
		except Exception as error:
			lines.append(f"  pinned header: could not be described: {error!r}")
	return lines


def describeRowsOnShow(control):
	""":return: whether the band is holding a row the table has stopped showing, or None.

	Such as a filtered worksheet row. None for anything that cannot hide a row, every table but
	a grid.
	"""
	asked = getattr(control.source, "rowNoLongerShown", None)
	if asked is None:
		return None
	try:
		gone = asked(block.blockId.bookmark for block in control.window.blocks)
	except Exception as error:
		log.debugWarning("Could not ask which rows the table is showing", exc_info=True)
		return f"could not be told which of them the table is showing: {error!r}"
	if gone is None:
		return "every row on the band is one the table is still showing."
	return f"row {gone} is on the band and the table has stopped showing it."


def describeEdges(control) -> str:
	""":return: what the source said about each end of what it is reading, and why.

	For a band that stops short. See `flow.FetchResult.message`.
	"""
	from .flow import Edge, EdgeState

	reasons = getattr(control, "edgeReasons", None) or {}
	edges = getattr(getattr(control, "window", None), "edges", None)
	if edges is None:
		return "not known."
	said = []
	for edge in (Edge.BEFORE, Edge.AFTER):
		state = edges.get(edge, EdgeState.OPEN)
		where = "before" if edge is Edge.BEFORE else "after"
		if state is EdgeState.OPEN:
			said.append(f"more {where}")
			continue
		why = reasons.get(edge)
		said.append(f"{state.value} {where}" + (f" — {why}" if why else ""))
	return "; ".join(said) + "."


def describeLiveUpdates(band) -> str:
	""":return: whether the band is being told about changes, and what has arrived.

	Whether news reaches the band, whether it reads, and whether what it reads differs.
	"""
	from . import patches

	counts = getattr(band, "liveCounts", None)
	if counts is None:
		return "not reading a table."
	told = "on" if patches.liveUpdatesInstalled() else "OFF, falling back to a timer"
	heard, passes, redrawn = counts
	timer = bmConfig.liveReadSeconds()
	clock = f"every {timer}s as well" if timer else "no timer"
	return f"document change notices {told}, {clock}; {heard} heard, {passes} read, {redrawn} redrew."


def describePins() -> list[str]:
	"""What each pinned object has been doing, for the log.

	See `ObjectMonitor.counts`.

	:return: one line per pin, or a line saying there are none.
	"""
	from . import getPlugin

	plugin = getPlugin()
	monitors = dict(getattr(plugin, "_monitors", {}) or {}) if plugin is not None else {}
	if not monitors:
		return ["Pinned objects: none.", *describeDivision()]
	lines = [*describeDivision(), "Pinned objects:"]
	for key, monitor in monitors.items():
		reads, changes = getattr(monitor, "counts", (0, 0))
		lines.append(f"  {monitor.name!r} in {key}: {reads} reads, {changes} of them different.")
		lines.extend(f"    {line}" for line in describePinReading(monitor))
	return lines


def describeDivision() -> list[str]:
	"""How the display is divided right now, and the settings each physical display divides by.

	Each link of the chain a segment's height comes from: the composite's bands, each display's
	segment settings, and any claim over them.

	:return: the lines of the account.
	"""
	from .devices import deviceMap

	lines = ["Display division:"]
	container = braille.handler.mainBuffer if braille.handler else None
	for number, segment in enumerate(getattr(container, "segments", None) or ()):
		focus = " (follows the focus)" if number == getattr(container, "focusSegmentNumber", None) else ""
		lines.append(f"  segment {number}: {segment.key!r} {segment.rect}{focus}")
	try:
		devices = deviceMap()
	except Exception as error:
		return [*lines, f"  physical displays: could not be read: {error!r}"]
	for device in devices:
		try:
			key = device.displayKey
			layout = (
				f"segments {'on' if bmConfig.areSegmentsEnabled(key) else 'off'}, "
				f"layout {bmConfig.getLayout(key)!r}, focus segment {bmConfig.getFocusSegment(key)}"
			)
		except Exception as error:
			key, layout = "?", f"settings could not be read: {error!r}"
		lines.append(
			f"  {device.driverName}: rows {device.rowStart} to {device.rowEnd - 1}, "
			f"{device.numCols} cells, settings {key!r}: {layout}",
		)
	return lines


def describePinReading(monitor) -> list[str]:
	"""Whether a pin is being read as a flow, and if it is not, why not.

	Names which: the segment too short, unable to hold a flow, or the flow would not build, in
	which case it is built again with notes.

	:param monitor: the pin.
	:return: the lines of the account.
	"""
	lines = [f"pinned object: {describeObject(getattr(monitor, 'pinned', None))}"]
	segment = monitor._segment()
	if segment is None:
		return [*lines, "segment: gone"]
	rows, cols = segment.rect.numRows, segment.rect.numCols
	lines.append(f"segment: {segment!r}, {rows} rows by {cols} cells")
	control = getattr(monitor, "controller", None)
	if control is not None:
		attached = getattr(segment, "controller", None) is control
		lines.append(f"read as a flow: yes, {'attached to' if attached else 'NOT attached to'} the segment")
		lines.append(f"flow: {control!r}")
		return lines
	lines.append("read as a flow: no, reading through NVDA's regions")
	if not hasattr(segment, "attach"):
		return [*lines, "why: the segment cannot hold a flow"]
	try:
		needed = monitor._minimumRows(rows)
	except Exception as error:
		needed = None
		lines.append(f"why: could not ask how many rows it needs: {error!r}")
	if needed is not None and rows < needed:
		return [*lines, f"why: the segment has {rows} rows and this pin needs {needed}"]
	notes: list = []
	try:
		built = buildController(
			obj=monitor.pinned,
			numRows=rows,
			numCols=cols,
			handler=braille.handler,
			notes=notes,
		)
	except Exception as error:
		built = None
		notes.append(f"raised {error!r}")
	lines.append(f"building it now: {'succeeded' if built is not None else 'failed'}")
	lines.extend(f"  {note}" for note in notes)
	return lines


def describeLineFocus(control) -> str:
	"""Whether the row the focus is on is marked, and which row got the mark.

	Three answers, as `describeIndent` gives: on, off, or no indent to draw the mark into.

	:param control: the live controller.
	:return: one sentence.
	"""
	if not getattr(control, "lineFocus", False):
		return "off, so the cursor is the only thing saying which row has the focus."
	try:
		cells = control.cells()
		numCols = control.renderer.numCols
	except Exception:
		log.debugWarning("Could not read the band to look for the focus mark", exc_info=True)
		return "on, but the band could not be read."
	# Where the band draws glyphs the mark is the square's cell and then a blank, as Monarch
	# draws its own list focus. See `FlowController._markLineFocus`.
	wanted = ([FOCUS_CELL] * FOCUS_WIDTH, [FOCUS_CELL] + [0] * (FOCUS_WIDTH - 1))
	marked = [row for row in range(len(cells) // numCols) if cells[row * numCols :][:FOCUS_WIDTH] in wanted]
	if marked:
		return f"on, marked on {'rows' if len(marked) > 1 else 'row'} {', '.join(map(str, marked))}."
	return "on, but nothing is marked: the focused row is at the margin, with no room for it."


def _bandGeometry(handler, band) -> tuple[int, int, str]:
	"""How big a band to lay out in, and where the answer came from.

	Reported, since the wrong width changes the answer. The claimed segment, else what the band
	would claim, else the whole display, labelled as a guess.

	:param handler: the braille handler.
	:param band: the live band, or None.
	:return: the rows, the columns, and a phrase naming which of the three was used.
	"""
	segment = None
	try:
		segment = band.segment() if band is not None else None
	except Exception:
		log.debugWarning("Could not ask the band for its segment", exc_info=True)
	rect = getattr(segment, "rect", None)
	if rect is not None:
		return rect.numRows, rect.numCols, f"{rect.numRows} rows of {rect.numCols}, the band on the display"
	if band is not None:
		try:
			rect = band.bandRect()
		except Exception:
			log.debugWarning("Could not ask the band how big it would be", exc_info=True)
			rect = None
		if rect is not None:
			return (
				rect.numRows,
				rect.numCols,
				f"{rect.numRows} rows of {rect.numCols}, what the band would claim",
			)
	numRows, numCols = bandSize(handler)
	return numRows, numCols, f"{numRows} rows of {numCols}, the whole display — no band to ask"


def report(control: FlowController) -> list[str]:
	"""Walk a flow forward and back, describing each window it rests in.

	:param control: the flow to walk.
	:return: the lines of the report.
	"""
	lines = [f"Flow dry run: {control!r}", f"Source: {control.source!r}", f"Renderer: {control.renderer!r}"]
	arrival = control.describeRows()
	lines.append("On arrival:")
	lines.extend(f"  {line}" for line in arrival)
	moved = control.panForward()
	lines.append(f"After panning forward (moved: {moved}):")
	lines.extend(f"  {line}" for line in control.describeRows())
	back = control.panBack()
	lines.append(f"After panning back (moved: {back}):")
	returned = control.describeRows()
	lines.extend(f"  {line}" for line in returned)
	if not moved:
		lines.append("Reversibility: not tested, the document did not fill a second window.")
	elif returned == arrival:
		lines.append("Reversibility: panning back returned to the arrival rows.")
	else:
		lines.append("Reversibility: FAILED, panning back did not return to the arrival rows.")
	used, total = control.fillStats()
	percent = (100 * used / total) if total else 0
	lines.append(f"Band fill: {used} of {total} cells ({percent:.0f}%) on the last window shown.")
	lines.append(f"Blocks held: {len(control.window.blocks)}")
	lines.append(f"Indent: {describeIndent(control)}")
	lines.extend(f"Cost, {line}" for line in describeCost(control))
	return lines


CRLF = "\r\n"
"""How a report's rows are joined for the clipboard.

A carriage return and a line feed, so Notepad shows separate lines.
"""


def toClipboard(title: str, lines: list[str]) -> bool:
	"""Put a diagnostic where the reader can actually get at it.

	Joined by `CRLF`, with a heading and the time in front so two pasted reports
	stay apart.

	:param title: what this report is, for its heading.
	:param lines: the report.
	:return: whether the clipboard took it. False means the log is all there is, which the
		caller says out loud rather than leaving the reader to guess.
	"""
	stamp = time.strftime("%Y-%m-%d %H:%M:%S")
	text = CRLF.join([f"{title} — {stamp}", *lines, ""])
	try:
		return bool(api.copyToClip(text))
	except Exception:
		log.debugWarning("Could not copy a diagnostic to the clipboard", exc_info=True)
		return False


def describeIndent(control: FlowController) -> str:
	"""How the band is drawing the depth of what it holds, in words.

	Tells apart content with no depth from depth the band chose not to draw.

	:param control: the flow to ask.
	:return: one line describing the plan in force.
	"""
	plan = getattr(control.renderer, "indentPlan", None)
	if plan is None or plan.isFlat:
		depths = [block.depth for block in control.window.blocks if block.depth is not None]
		if not depths:
			return "none drawn; nothing on the band reports a depth."
		return f"none drawn, though depths {min(depths)} to {max(depths)} are on the band."
	note = f", margin stands for level {plan.noteLevel}" if plan.noteLevel else ""
	return f"{plan.style}, level {plan.baseline} at the margin, up to {plan.maxLevels} levels drawn{note}."


def describeCost(control: FlowController) -> list[str]:
	"""What a flow has cost so far, in words.

	The slowest block, the slowest operation, and how often the budget was reached, so the
	budget can be compared with real cost.

	:param control: the flow to ask.
	:return: the lines of the account, empty if it has no budget to ask.
	"""
	budget = getattr(control.source, "budget", None)
	if budget is None or not hasattr(budget, "describe"):
		return []
	lines = list(budget.describe())
	used, total = control.fillStats()
	if total:
		lines.append(f"band fill now: {used} of {total} cells ({100 * used / total:.0f}%)")
	lines.append(f"blocks held: {len(control.window.blocks)}")
	return lines


def _whatTheBandIsDoing(band) -> str:
	""":return: what the band has on it, for the title, or "nothing" if it has no flow.

	So the title says when the band and the dry run are about different things. See `dryRun`.

	:param band: the live band.
	"""
	control = getattr(band, "controller", None)
	if control is None:
		return "nothing"
	plan = getattr(getattr(control, "renderer", None), "columnPlan", None)
	if plan is not None and not plan.isEmpty:
		first, last, total = plan.whereItIs
		return f"a table in columns ({first} to {last} of {total})"
	return "a flow of its own"


def dryRun(handler=None, obj: "NVDAObject | None" = None, band=None) -> list[str]:
	"""Run a flow over what the reader is in and report the result.

	:param handler: the braille handler, for the layout settings and the fallback size.
	:param obj: what to read. Defaults to the navigator object.
	:param band: the live band, asked how big it is. Without it the whole display is
		measured, which on a composite is a rectangle no band ever gets.
	:return: the lines written, so a caller can summarise them.
	"""
	numRows, numCols, measured = _bandGeometry(handler, band)
	live = liveReport(band)
	notes: list[str] = [f"band: {measured}"]
	# The same question the band asks on a focus change, so that running this while standing
	# on a form field reports what the band would actually show there.
	focus = _focusObject()
	notes.append(f"focus: {describeObject(focus)}")
	atObject = focus if flowForms.isControlObject(focus) else None
	control = buildController(
		obj=obj,
		numRows=numRows,
		numCols=numCols,
		handler=handler,
		notes=notes,
		atObject=atObject,
	)
	if control is None:
		# Every step is reported, since one message covers several failures. It is about this
		# dry run's flow, not the band's, which can differ, as in Excel; see
		# `_whatTheBandIsDoing`.
		lines = [
			"Flow dry run: nothing here can be flowed on its own"
			+ (f", though the band is reading {_whatTheBandIsDoing(band)}" if band is not None else "")
			+ ". What happened:",
		]
		lines.extend(f"  {note}" for note in notes)
	else:
		lines = report(control)
		lines[1:1] = [f"  {note}" for note in notes]
	# Straight after the title, ahead of every detail of the dry run's own flow. The band is
	# what the reader is actually feeling and the dry run below it is a second flow built to
	# compare against; a report that buried the first invited every question to be answered
	# about the wrong window. The title keeps its place because it is what names the report
	# and, when nothing could be flowed at all, what says so.
	lines[1:1] = ["What the band is showing now:", *(f"  {line}" for line in live), ""]
	log.info("\n".join(lines))
	return lines
