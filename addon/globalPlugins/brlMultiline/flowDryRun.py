# BrlMultiline: reading a flow into the log instead of onto a display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Writing what a flow would show to the log, and what one has cost.

There is a gap between the flow being unit tested and the flow being on a display: the
tests know what blocks a document has, and a real web page does not agree to be simple.
This runs the whole stack — source, renderer, window — over the document the reader is
actually in, and reports what the band would hold. No panel is claimed, no segment is
written to, and nothing reaches the hardware.

It is a viewer, deliberately. A live flow would move the browse mode cursor, and a
diagnostic must not move the reader's place in the page it is diagnosing.

The report covers three windows rather than one: where it arrived, where panning forward
took it, and where panning back brought it. The third should be the first again, which is
the property the whole anchor design exists for and the one hardest to be sure of by
reading code.

The flow itself is built by `flowBuild`, which is what the live band uses too — so what is
reported here is the same construction the reader gets, rather than a second one that
could drift from it.
"""

import time
from typing import TYPE_CHECKING, Optional

import api
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


def liveReport(band) -> list[str]:
	"""What the band is showing at this moment, as opposed to what a fresh flow would show.

	The gap this fills was found by trying to diagnose a hardware report without it. A dry
	run builds its own flow, arrives, pans forward and pans back — so it reports arrival and
	panning, and it can say nothing whatever about what the band did when the reader moved
	the focus, because the reader's own window is not the one it built. Asked to explain a
	display that had scrolled the wrong way on a focus move, the report had no line in it
	that was about the display that scrolled.

	So the report now begins with the live band: its window, where its anchor sits and which
	edge that anchor was entered from, and what each row is holding. The entry is the line
	that matters most for a scrolling complaint — `top` means the anchored block was placed
	at the top of the band and the rest filled downward, `bottom` means it was placed at the
	bottom and the rows above it filled in behind, and which of those happened is exactly
	what a reader cannot tell from feeling the result.

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
	# The band's own numbers. Everything under `Cost` below belongs to the controller this
	# command builds to answer with, which reads the same document and has a budget of its
	# own — so a reader diagnosing a band that keeps saying "more, not fetched" was being
	# shown the cost of something else entirely.
	lines.extend(f"  Band cost, {line}" for line in describeCost(control))
	lines.extend(describePins())
	plan = getattr(getattr(control, "renderer", None), "columnPlan", None)
	if plan is not None and not plan.isEmpty:
		lines.append(f"Band columns: {flowTable.describe(plan)}")
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
	return lines


def describeObjectTable(control) -> list:
	""":return: what a table made of objects found, or nothing if this is not one.

	A table read out of a document is NVDA's own answer throughout and there is nothing to
	report about how it was arrived at. A table made of objects is this add-on presenting one
	as though it were a document, and every one of its answers is a choice between two or
	three places to ask — which is exactly what a report has to be able to settle. See
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


def describeEdges(control) -> str:
	""":return: what the source said about each end of what it is reading, and why.

	The reader's question when the band stops short: NVDA pans on through the document and
	the band says there is no more. Which of the several ways a walk has of concluding it has
	finished was taken is the whole of the answer, and "(end of content)" on a row is the
	claim rather than the reason for it.
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

	The one thing about live updating that cannot be felt. A reader whose prices sit still
	needs to know which half is not working: whether the news is reaching the band at all,
	whether it is reading when it does, and whether what it reads is any different.
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

	A pin that stops moving has stopped somewhere, and there are three candidates: this
	add-on stopped asking, the page stopped changing, or something between them stopped
	delivering. Only the first is ours, and only these numbers tell them apart — reads
	climbing while changes sit still means the asking is fine and the answer is always the
	same, which is the page and not the pin.

	:return: one line per pin, or a line saying there are none.
	"""
	from . import getPlugin

	plugin = getPlugin()
	monitors = dict(getattr(plugin, "_monitors", {}) or {}) if plugin is not None else {}
	if not monitors:
		return ["Pinned objects: none."]
	lines = ["Pinned objects:"]
	for key, monitor in monitors.items():
		reads, changes = getattr(monitor, "counts", (0, 0))
		lines.append(f"  {monitor.name!r} in {key}: {reads} reads, {changes} of them different.")
	return lines


def describeLineFocus(control) -> str:
	"""Whether the row the focus is on is marked, and which row got the mark.

	Three answers rather than two, for the same reason `describeIndent` gives three: a band
	with no mark on it is either a band with the setting off or a band whose focused row had
	no indent to draw the mark into, and under the fingers those are the same nothing.

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
	wanted = [FOCUS_CELL] * FOCUS_WIDTH
	marked = [row for row in range(len(cells) // numCols) if cells[row * numCols :][:FOCUS_WIDTH] == wanted]
	if marked:
		return f"on, marked on {'rows' if len(marked) > 1 else 'row'} {', '.join(map(str, marked))}."
	return "on, but nothing is marked: the focused row is at the margin, with no room for it."


def _bandGeometry(handler, band) -> tuple[int, int, str]:
	"""How big a band to lay out in, and where the answer came from.

	Said out loud in the report because a diagnostic measured at the wrong width answers a
	different question from the display, and there is nothing in its output to show that it
	did. Indent is the case that made this worth fixing: the same tree reads as its true
	depth on eighty cells and as a rebased margin on thirty two.

	The claimed segment first, which is the band the reader is feeling. Then what the band
	would claim if it were turned on, so that a reader diagnosing with the flow off still
	gets their own geometry. Then the whole display, which is a guess and is labelled as one.

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

A carriage return and a line feed rather than a bare line feed, so that what is pasted
into Notepad is the report rather than one very long line. Named because a bare escape
in the middle of a join reads as a typo the next time somebody passes it.
"""


def toClipboard(title: str, lines: list[str]) -> bool:
	"""Put a diagnostic where the reader can actually get at it.

	A report that only reaches the log is a report somebody has to go fishing for, in a file
	that is also holding everything else NVDA said while they were reproducing the problem.
	The hardware runs this project depends on are slow enough already.

	Rows are joined with a carriage return and a line feed rather than a bare line feed, so
	that what is pasted into Notepad is the report rather than one very long line. A heading
	and the time go on the front because a reader comparing two runs pastes them one after
	the other, and two reports with nothing between them are one report.

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

	Worth a line of every report because indent is the first thing that is wrong when a tree
	reads oddly, and the two ways it goes wrong look the same on the display: the content has
	no depth to draw, and the band decided not to draw the depth it has. Naming both keeps a
	report from having to be re-run to tell them apart.

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

	The measurement milestone 7 is: a budget that is never compared with what reading
	actually costs is a number somebody guessed, and this project has twice had a guessed
	number turn out to be the fault. What is wanted back from a hardware run is not "it felt
	slow" but the slowest block, the slowest operation, and how often the budget was reached.

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


def dryRun(handler=None, obj: Optional["NVDAObject"] = None, band=None) -> list[str]:
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
		# Every step is reported, because "nothing here can be flowed" was one message for
		# four different failures and said nothing about which had happened.
		lines = ["Flow dry run: nothing here can be flowed. What happened:"]
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
