# BrlMultiline: where the time goes in the operations that can stall NVDA.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Timing for the operations that call into applications on NVDA's main thread.

Off unless `enabled` is set, and then every call here only passes through. When on, an
`operation` collects the `stage`s run inside it, by path, and logs them when it ends: at info
when it was slow, so a freeze explains itself at the default log level, and otherwise at debug.
This observes and changes nothing. See docs/design/structured-presentation-plan.md, Table timing.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import threading
import time
from collections.abc import Callable, Iterator

import api
import watchdog
from logHandler import log

enabled = False
"""Whether anything is timed. Read at the start of each operation, so it can be set while NVDA runs."""

SLOW_SECONDS = 0.25
"""How long an operation takes before it is logged at info. Half of watchdog's shortest wait."""

SEPARATOR = "/"
"""What joins a stage to the stage it ran inside."""

_clock: Callable[[], float] = time.perf_counter
"""What time is measured with, replaced by tests so they need not sleep."""


@dataclasses.dataclass
class StageTotal:
	"""What one stage has cost within one operation."""

	calls: int = 0
	seconds: float = 0.0
	slowest: float = 0.0
	raised: int = 0
	"""How many calls ended in an exception, which for a cell lookup includes merged cells."""

	def describe(self, path: str) -> str:
		""":return: this stage as one line of the report.

		:param path: the stage's path.
		"""
		said = f"{path}: {self.calls} calls, {self.seconds:.3f}s, slowest {self.slowest:.3f}s"
		return f"{said}, {self.raised} raised" if self.raised else said


class Timing:
	"""The stages of one operation, in the order they were first entered."""

	def __init__(self, name: str) -> None:
		"""
		:param name: what the operation is called in the log.
		"""
		self.name = name
		self.stages: dict[str, StageTotal] = {}
		self.path: list[str] = []
		self.notes: list[Callable[[], str]] = []
		self.duringRecovery = 0
		"""How many stages ended while watchdog was recovering from a freeze."""

	def report(self, elapsed: float) -> str:
		""":return: the whole operation, for the log.

		:param elapsed: how long it took, in seconds.
		"""
		lines = [f"BrlMultiline timing: {self.name} took {elapsed:.3f}s, focus in {_focusApp()}"]
		lines.extend(f"  {_said(note)}" for note in self.notes)
		lines.extend(f"  {total.describe(path)}" for path, total in self.stages.items())
		if self.duringRecovery:
			lines.append(f"  stages ended during freeze recovery: {self.duringRecovery}")
		return "\n".join(lines)


_running = threading.local()
"""The operation under way on this thread, in `timing`."""


def _focusApp() -> str:
	""":return: the name of the application with the focus, or "unknown"."""
	try:
		return api.getFocusObject().appModule.appName
	except Exception:
		return "unknown"


def _said(said: Callable[[], str]) -> str:
	""":return: what a note says, or that it could not say, since a report must never raise.

	:param said: the note.
	"""
	try:
		return said()
	except Exception:
		log.debugWarning("Could not write a note for a timing report", exc_info=True)
		return "(a note could not be written)"


def _current() -> Timing | None:
	""":return: the operation under way on this thread, or None."""
	return getattr(_running, "timing", None)


@contextlib.contextmanager
def stage(name: str) -> Iterator[None]:
	"""Count the time inside this block as one stage of the operation under way.

	:param name: what the stage is called, unique among those beside it.
	"""
	timing = _current()
	if timing is None:
		yield
		return
	timing.path.append(name)
	total = timing.stages.setdefault(SEPARATOR.join(timing.path), StageTotal())
	began = _clock()
	try:
		yield
	except BaseException:
		total.raised += 1
		raise
	finally:
		spent = _clock() - began
		timing.path.pop()
		total.calls += 1
		total.seconds += spent
		total.slowest = max(total.slowest, spent)
		if watchdog.isAttemptingRecovery:
			timing.duringRecovery += 1


@contextlib.contextmanager
def operation(name: str) -> Iterator[None]:
	"""Time everything in this block, and log it at the end.

	Inside another operation this is a stage of that one, so one stall is one report.

	:param name: what the operation is called in the log.
	"""
	if _current() is not None:
		with stage(name):
			yield
		return
	if not enabled:
		yield
		return
	timing = Timing(name)
	_running.timing = timing
	began = _clock()
	try:
		yield
	finally:
		_running.timing = None
		_finish(timing, _clock() - began)


def _finish(timing: Timing, elapsed: float) -> None:
	"""Log an operation, unless it was quick and ran no stages, as a caret move in a table is.

	:param timing: the operation.
	:param elapsed: how long it took, in seconds.
	"""
	if elapsed >= SLOW_SECONDS:
		log.info(timing.report(elapsed))
	elif timing.stages:
		log.debug(timing.report(elapsed))


def note(said: Callable[[], str]) -> None:
	"""Add a line to the report of the operation under way.

	:param said: asked for the line only when the report is written.
	"""
	timing = _current()
	if timing is not None:
		timing.notes.append(said)


@contextlib.contextmanager
def timedMethod(owner, methodName: str, name: str) -> Iterator[None]:
	"""Count one method of an object as a stage for the length of this block.

	For a step inside NVDA's code, which has no stage of its own. Shadows the method on the
	instance and takes the shadow away again. Does nothing where the object has no such method,
	already shadows it, or will not take the attribute.

	:param owner: the object.
	:param methodName: the method.
	:param name: what the stage is called.
	"""
	method = getattr(owner, methodName, None)
	if not enabled or not callable(method) or methodName in getattr(owner, "__dict__", {}):
		yield
		return

	@functools.wraps(method)
	def timed(*args, **kwargs):
		with stage(name):
			return method(*args, **kwargs)

	try:
		setattr(owner, methodName, timed)
	except AttributeError:
		yield
		return
	try:
		yield
	finally:
		delattr(owner, methodName)
