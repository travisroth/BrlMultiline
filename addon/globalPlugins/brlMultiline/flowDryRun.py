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

from typing import TYPE_CHECKING, Optional

import api
from logHandler import log

from . import flowForms
from .flowBuild import bandSize, buildController, describeObject
from .flowControl import FlowController

if TYPE_CHECKING:
	from NVDAObjects import NVDAObject


def _focusObject():
	""":return: where the system focus is, or None if it cannot be read."""
	try:
		return api.getFocusObject()
	except Exception:
		log.debugWarning("Could not read the focus object", exc_info=True)
		return None


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
