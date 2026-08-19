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

from .flowControl import FlowController
from .flowRender import FlowRenderer
from .flowSources import DocumentFlowSource, regionFactoryFor, regionFactoryForObject
from .objectMonitor import resolveTarget

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


def buildController(
	obj: Optional["NVDAObject"] = None,
	numRows: int = DEFAULT_ROWS,
	numCols: int = DEFAULT_COLS,
	handler=None,
	live: bool = False,
	notes: Optional[list] = None,
) -> Optional[FlowController]:
	"""Build a flow over an object, ready to be asked what it would show.

	:param obj: what to read. Defaults to the navigator object.
	:param numRows: the height of the band to lay out for.
	:param numCols: its width.
	:param handler: the braille handler the renderer lays out through.
	:param live: whether the flow may move the real cursor. False for a dry run.
	:param notes: a list to record each step in, so that a failure says which step failed.
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
	target = resolveTarget(obj)
	if target is obj:
		notes.append("No tree interceptor was substituted; reading the object itself.")
	else:
		notes.append(f"Reading through the tree interceptor: {describeObject(target)}")
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
		generation=0,
		# A dry run reads; it never writes. Blank lines collapse, as they do for a reader.
		interactive=False,
	)
	notes.append(f"Reading by {source.unit}, band {numRows} rows of {numCols} cells.")
	renderer = FlowRenderer(handler, numCols=numCols, fillRows=False)
	control = FlowController(source, renderer, numRows=numRows, live=live)
	if not control.enterAtCursor():
		result = control.lastResult
		kind = getattr(getattr(result, "kind", None), "value", "no answer")
		notes.append(f"The source could not give the block at the cursor: {kind} {getattr(result, 'message', '')}")
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
	slowest = getattr(control.source.budget, "slowest", None)
	if slowest:
		lines.append(f"Slowest block: {slowest * 1000:.1f} ms")
	lines.append(f"Blocks held: {len(control.window.blocks)}")
	return lines


def dryRun(handler=None, obj: Optional["NVDAObject"] = None) -> list[str]:
	"""Run a flow over what the reader is in and write the result to the log.

	:param handler: the braille handler, for the band size and the layout settings.
	:param obj: what to read. Defaults to the navigator object.
	:return: the lines written, so a caller can summarise them.
	"""
	numRows, numCols = bandSize(handler)
	notes: list[str] = []
	control = buildController(obj=obj, numRows=numRows, numCols=numCols, handler=handler, notes=notes)
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
