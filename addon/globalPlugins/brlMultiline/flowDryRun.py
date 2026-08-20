# BrlMultiline: reading a flow into the log instead of onto a display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Building a flow over what the reader is in, and writing what it would show to the log.

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
"""

from typing import TYPE_CHECKING, Optional

import api
import config
import textInfos
from braille.regions.focus import getFocusRegions
from braille.regions.textInfo import TextInfoRegion
from logHandler import log

from . import flowForms, flowObjects
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


def bandSize(handler) -> tuple[int, int]:
	""":return: the rows and columns to lay a dry run out in, from the display if there is one."""
	dimensions = getattr(handler, "displayDimensions", None)
	numRows = getattr(dimensions, "numRows", 0) or DEFAULT_ROWS
	numCols = getattr(dimensions, "numCols", 0) or DEFAULT_COLS
	return numRows, numCols


def _focusObject():
	""":return: where the system focus is, or None if it cannot be read."""
	try:
		return api.getFocusObject()
	except Exception:
		log.debugWarning("Could not read the focus object", exc_info=True)
		return None


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
	try:
		import controlTypes

		editable = controlTypes.State.EDITABLE in obj.states
	except Exception:
		editable = False
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
	if _isTreeInterceptor(obj):
		return None
	return flowObjects.adapterFor(obj)


def _isTreeInterceptor(obj) -> bool:
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
		flowObjects.regionFactory(live=live),
		generation=generation,
		budget=budgetForBand(numRows),
	)
	notes.append(
		f"Reading objects, band {numRows} rows of {numCols} cells, budget {source.budget.maxBlocks} blocks.",
	)
	renderer = FlowRenderer(handler, numCols=numCols, fillRows=False)
	# Never live in the sense that matters: an object flow moves the window and nothing else,
	# because the equivalent of a reading position here is a selection, and a selection is
	# application state. `live` decides only whether the focused block shows a cursor.
	control = FlowController(source, renderer, numRows=numRows, live=False)
	if not control.enterAtCursor():
		result = control.lastResult
		kind = getattr(getattr(result, "kind", None), "value", "no answer")
		notes.append(f"The run gave nothing to read: {kind} {getattr(result, 'message', '')}")
		return None
	return control


def buildController(
	obj: Optional["NVDAObject"] = None,
	numRows: int = DEFAULT_ROWS,
	numCols: int = DEFAULT_COLS,
	handler=None,
	live: bool = False,
	generation: int = 0,
	notes: Optional[list] = None,
	atObject: Optional["NVDAObject"] = None,
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
		unit=readingUnit(),
		generation=generation,
		interactive=isBeingWrittenIn(target, obj),
		# Sized to the band: filling eight rows costs at least eight blocks, so a budget near
		# the band's own height is one the reader meets on every arrival. See `budgetForBand`.
		budget=budgetForBand(numRows),
	)
	notes.append(
		f"Reading by {source.unit}, band {numRows} rows of {numCols} cells, "
		f"budget {source.budget.maxBlocks} blocks.",
	)
	if atObject is not None:
		notes.append(f"Arrived at a control: {describeObject(atObject)}")
	renderer = FlowRenderer(handler, numCols=numCols, fillRows=False)
	control = FlowController(source, renderer, numRows=numRows, live=live)
	if not control.enterAtCursor(atObject=atObject):
		result = control.lastResult
		kind = getattr(getattr(result, "kind", None), "value", "no answer")
		notes.append(
			f"The source could not give the block at the cursor: {kind} {getattr(result, 'message', '')}"
		)
		return None
	return control


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
	lines.extend(f"Cost, {line}" for line in describeCost(control))
	return lines


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


def dryRun(handler=None, obj: Optional["NVDAObject"] = None) -> list[str]:
	"""Run a flow over what the reader is in and write the result to the log.

	:param handler: the braille handler, for the band size and the layout settings.
	:param obj: what to read. Defaults to the navigator object.
	:return: the lines written, so a caller can summarise them.
	"""
	numRows, numCols = bandSize(handler)
	notes: list[str] = []
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
	log.info("\n".join(lines))
	return lines
