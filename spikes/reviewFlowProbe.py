# BrlMultiline: phase 0 probe for the flow and the review cursor.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Console probe: what reaches the flow when the review cursor moves, and what it does about it.

Phase 0 of `docs/design/review-flow-plan.md`. It records, with the add-on's flow running, every
call to NVDA's braille handler that concerns the review cursor, the caret, an update or the
tether, and every time the flow re-read a block or followed the cursor. Each record carries the
tether, the line under the review cursor, the line under the caret, and the band's top row before
and after, so a report says which step moved the display and which did not.

It is not part of the add-on and is not packaged. It is imported from the NVDA Python console
(NVDA+control+z). Nothing here drives the flow: the band is read, never moved, and every wrapped
method calls the one it wraps with the same arguments.

Run it like this, one line at a time::

	import sys; sys.path.insert(0, r"C:\\code\\BrlMultiline\\spikes")
	import importlib, reviewFlowProbe as probe; importlib.reload(probe)
	probe.start()

The reload matters: Python keeps a module it has imported once, so without it a console that
imported the probe before keeps running that older copy until NVDA restarts. `start` says which
version is running, and so does the report.

Close the console, then work through the scenarios, calling `probe.mark` from the console before
each one so the report is divided by them. The scenarios the plan asks about::

	probe.mark("terminal: review by line, word and character")
	probe.mark("terminal: object navigation out and back")
	probe.mark("terminal: output arriving while reviewing")  # e.g. a loop printing once a second
	probe.mark("terminal: NVDA+control+t to review, then back to focus")
	probe.mark("classic console: review by line")
	probe.mark("notepad: review by line")
	probe.mark("web page: review by line")

To time reading a band's worth of review lines in the terminal, run this in the console, then
close the console and put the review cursor in the terminal within five seconds. NVDA says the
result when it is done::

	probe.timeReviewLines(8)

Then::

	probe.report()            # to the log and the clipboard
	probe.report(r"C:\\code\\BrlMultiline\\spikes\\reviewProbeOut\\run1.txt")
	probe.stop()

`stop` is safe to call at any point and safe to call twice. What each question in the plan looks
like when answered:

- Does a review move reach the flow's active block? A `handleReviewMove` record with "last
  region is the active block: yes", followed by `_followIfRead` and `followCursor`.
- Is it answered from the caret? The review line differs from one `handleReviewMove` record to the
  next while the band's top row does not. Within one record the review line reads "unchanged",
  because NVDA moves the review position before it calls `handleReviewMove`.
- Does new output move the display? `handleUpdate` records for the terminal followed by
  `followCursor` with the top row changed back towards the caret.
- Does the container survive a change of tether? After `setTether`, "buffer after" still reads
  `DisplayContainer`.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

import api
import braille
import textInfos
from logHandler import log

VERSION = 2
"""Said by `start` and `report`, so a run shows which copy of the probe made it."""

MAX_EVENTS = 20000
"""The most records kept, oldest dropped first. Ordinary use makes about 80 a minute, so this
keeps a few hours; `report` says when the oldest have been dropped."""

TEXT_CHARS = 60
"""How much of a line a record keeps."""

_events: deque = deque(maxlen=MAX_EVENTS)
_recorded = 0
"""Every record made since `start`, kept or not, so a report can say how many were dropped."""
_installed: dict[str, tuple[type, str, Any]] = {}
_depth = 0
_started = 0.0


# What is installed.


def _append(entry: dict) -> None:
	global _recorded
	_recorded += 1
	_events.append(entry)


def _addonModule(name: str):
	""":return: one of the add-on's modules, imported the way NVDA loaded it."""
	import importlib

	return importlib.import_module(f"globalPlugins.brlMultiline.{name}")


def _targets() -> list[tuple[str, type, str]]:
	""":return: (label, class, method name) for everything the probe wraps."""
	handlerClass = type(braille.handler)
	targets = [
		("handleReviewMove", handlerClass, "handleReviewMove"),
		("handleCaretMove", handlerClass, "handleCaretMove"),
		("handleUpdate", handlerClass, "handleUpdate"),
		("handleGainFocus", handlerClass, "handleGainFocus"),
		("setTether", handlerClass, "setTether"),
		("_doNewObject", handlerClass, "_doNewObject"),
	]
	try:
		flowSegment = _addonModule("flowSegment")
		flowControl = _addonModule("flowControl")
		flowBand = _addonModule("flowBand")
	except Exception:
		log.error("reviewFlowProbe: the add-on's flow modules could not be imported", exc_info=True)
		return targets
	targets += [
		("_followIfRead", flowSegment.FlowBufferSegment, "_followIfRead"),
		("followCursor", flowControl.FlowController, "followCursor"),
		("handleFocusRegions", flowBand.FlowBand, "handleFocusRegions"),
	]
	return targets


def start() -> None:
	"""Wrap the methods and start recording. Calling it again starts a fresh record."""
	global _started
	global _recorded
	stop()
	_events.clear()
	_recorded = 0
	_started = time.perf_counter()
	for label, cls, name in _targets():
		# Inherited methods are wrapped on this class and taken off again by deleting the wrapper.
		original = cls.__dict__.get(name) or getattr(cls, name, None)
		if original is None:
			log.warning(f"reviewFlowProbe: {cls.__name__} has no {name}, so it is not recorded")
			continue
		_installed[label] = (cls, name, cls.__dict__.get(name))
		setattr(cls, name, _wrap(label, original))
	mark("probe started")
	log.info(f"reviewFlowProbe {VERSION}: recording {', '.join(_installed)}")
	try:
		import ui

		ui.message(f"Review probe {VERSION} recording")
	except Exception:
		log.debugWarning("reviewFlowProbe: could not announce the start", exc_info=True)


def stop() -> None:
	"""Put back every method the probe wrapped, where it is still the probe's."""
	for label, (cls, name, original) in list(_installed.items()):
		current = cls.__dict__.get(name)
		if getattr(current, "_reviewFlowProbe", False):
			if original is None:
				delattr(cls, name)
			else:
				setattr(cls, name, original)
		else:
			log.warning(f"reviewFlowProbe: {label} was replaced by something else; left alone")
		del _installed[label]


def _wrap(label: str, original):
	def wrapper(*args, **kwargs):
		global _depth
		top = _depth == 0
		before = entry = None
		try:
			# The probe must never stop NVDA's own call from running.
			before = _snapshot() if top else None
			entry = _begin(label, args, kwargs)
		except Exception:
			log.debugWarning("reviewFlowProbe: could not record a call", exc_info=True)
		_depth += 1
		started = time.perf_counter()
		try:
			return original(*args, **kwargs)
		finally:
			_depth -= 1
			try:
				if entry is not None:
					_finish(entry, label, before, time.perf_counter() - started, top)
			except Exception:
				log.debugWarning("reviewFlowProbe: could not finish recording a call", exc_info=True)

	wrapper._reviewFlowProbe = True
	wrapper.__name__ = getattr(original, "__name__", label)
	wrapper.__doc__ = getattr(original, "__doc__", None)
	return wrapper


# What is recorded.


def mark(label: str) -> None:
	"""Divide the record here, before a scenario."""
	_append({"mark": label, "at": time.perf_counter() - _started})


def _lineAt(info) -> str:
	""":return: the line holding a position, cut to `TEXT_CHARS`, or why it could not be read."""
	if info is None:
		return "none"
	try:
		line = info.copy()
		line.expand(textInfos.UNIT_LINE)
		return repr(line.text[:TEXT_CHARS])
	except Exception as error:
		return f"unreadable ({type(error).__name__})"


def _reviewLine() -> str:
	try:
		review = api.getReviewPosition()
	except Exception as error:
		return f"unreadable ({type(error).__name__})"
	obj = getattr(review, "obj", None)
	return f"{type(obj).__name__}: {_lineAt(review)}"


def _caretLine() -> str:
	try:
		focus = api.getFocusObject()
		obj = getattr(focus, "treeInterceptor", None) or focus
		caret = obj.makeTextInfo(textInfos.POSITION_CARET)
	except Exception as error:
		return f"unreadable ({type(error).__name__})"
	return f"{type(obj).__name__}: {_lineAt(caret)}"


def _band():
	import globalPluginHandler

	for plugin in getattr(globalPluginHandler, "runningPlugins", ()):
		band = getattr(plugin, "flowBand", None)
		if band is not None:
			return band
	return None


def _topRow() -> str:
	band = _band()
	control = getattr(band, "controller", None)
	if control is None:
		return "no flow"
	try:
		rows = control.windowRowTexts()
	except Exception as error:
		return f"unreadable ({type(error).__name__})"
	return repr(rows[0][:TEXT_CHARS]) if rows else "empty"


def _activeRegion():
	control = getattr(_band(), "controller", None)
	if control is None:
		return None
	try:
		return control.activeRegion()
	except Exception:
		return None


def _tether() -> str:
	try:
		return f"{braille.handler.getTether()} (auto {braille.handler.shouldAutoTether})"
	except Exception as error:
		return f"unreadable ({type(error).__name__})"


def _snapshot() -> dict:
	return {"review": _reviewLine(), "caret": _caretLine(), "top": _topRow()}


def _describeArgs(label: str, args, kwargs) -> str:
	shown = []
	for value in list(args[1:]) + list(kwargs.values()):
		if isinstance(value, (bool, int, str)) or value is None:
			shown.append(repr(value))
		elif isinstance(value, (list, tuple)):
			shown.append("[" + ", ".join(type(item).__name__ for item in value) + "]")
		else:
			shown.append(type(value).__name__)
	return ", ".join(shown)


def _begin(label: str, args, kwargs) -> dict:
	""":return: the record of a call, put in order as it starts, with what NVDA finds on entry."""
	entry = {
		"at": time.perf_counter() - _started,
		"call": label,
		"args": _describeArgs(label, args, kwargs),
		"depth": _depth,
		"tether": _tether(),
	}
	if label in ("handleReviewMove", "handleCaretMove", "handleUpdate"):
		regions = getattr(braille.handler.mainBuffer, "regions", None) or []
		last = regions[-1] if regions else None
		entry["last region"] = type(last).__name__ if last is not None else "none"
		entry["last region is the active block"] = (
			"yes" if last is not None and last is _activeRegion() else "no"
		)
	if label == "_followIfRead":
		entry["active block dirty"] = repr(getattr(_activeRegion(), "dirty", None))
	_append(entry)
	return entry


def _finish(entry: dict, label: str, before, seconds: float, top: bool) -> None:
	"""Add what the call took and what it changed to its record."""
	entry["ms"] = round(seconds * 1000, 1)
	if label == "setTether":
		entry["tether after"] = _tether()
		entry["buffer after"] = type(braille.handler.mainBuffer).__name__
	if top:
		after = _snapshot()
		for key in ("review", "caret", "top"):
			if before is not None and before[key] != after[key]:
				entry[key] = f"{before[key]} -> {after[key]}"
			else:
				entry[key] = f"{after[key]} (unchanged)"


# Timing.


def timeReviewLines(count: int = 8, delaySeconds: float = 5.0) -> None:
	"""Time reading `count` lines down from the review cursor, after a delay, without moving it.

	The delay is for leaving the console, whose own output is where the review cursor is while it
	is open. A copy of the review position is moved, so neither the review cursor nor the display
	moves. The result is recorded and spoken.

	:param count: how many lines to read, a band's worth by default.
	:param delaySeconds: how long to wait before reading.
	"""
	import core

	core.callLater(int(delaySeconds * 1000), _timeReviewLinesNow, count)


def _timeReviewLinesNow(count: int) -> None:
	try:
		info = api.getReviewPosition().copy()
	except Exception as error:
		result = f"no review position: {error!r}"
	else:
		started = time.perf_counter()
		read = 0
		for _ in range(count):
			line = info.copy()
			line.expand(textInfos.UNIT_LINE)
			_ = line.text
			read += 1
			if not info.move(textInfos.UNIT_LINE, 1):
				break
		ms = (time.perf_counter() - started) * 1000
		result = f"read {read} review lines in {ms:.1f} ms, in {type(info.obj).__name__}"
	_append({"at": time.perf_counter() - _started, "timing": result})
	try:
		import ui

		ui.message(result)
	except Exception:
		log.debugWarning("reviewFlowProbe: could not say the timing", exc_info=True)


# The report.


def report(path: str | None = None) -> str:
	"""Write the record to the log and the clipboard, and to a file if a path is given.

	:param path: a file to write the report to as well; its folder is made if missing.
	:return: the report.
	"""
	dropped = _recorded - len(_events)
	lines = [f"reviewFlowProbe {VERSION} report, {len(_events)} records"]
	if dropped:
		lines.append(f"The oldest {dropped} records were dropped; at most {MAX_EVENTS} are kept.")
	for event in _events:
		at = f"{event['at']:8.2f}s"
		if "mark" in event:
			lines.append("")
			lines.append(f"{at} == {event['mark']}")
			continue
		if "timing" in event:
			lines.append(f"{at}    timing: {event['timing']}")
			continue
		indent = "  " * event["depth"]
		lines.append(f"{at} {indent}{event['call']}({event['args']}) {event.get('ms', '?')} ms")
		for key, value in event.items():
			if key in ("at", "call", "args", "depth", "ms"):
				continue
			lines.append(f"{' ' * 10}{indent}  {key}: {value}")
	text = "\n".join(lines)
	log.info(text)
	try:
		api.copyToClip(text)
	except Exception:
		log.debugWarning("reviewFlowProbe: could not copy the report", exc_info=True)
	if path:
		import os

		os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
		with open(path, "w", encoding="utf-8") as file:
			file.write(text)
	return text
