# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Enough of NVDA to import the virtual display driver's lower layers.

`virtualLayout` imports nothing from NVDA at all. `deviceSlot` imports two things: the log,
and the background I/O thread it queues writes onto. That is the whole reason the write
scheduling was kept in a module of its own — it is the part most likely to go quietly wrong,
and it can be tested for the price of two stand-ins.

The driver class itself is not covered here. It opens real hardware and terminates it, which
is not something to model; it is tested on displays.

Call L{installVirtualStubs} before importing anything under `brlMultilineVirtual`. It is
idempotent, so every test module may call it.
"""

import os
import sys
import types

from ._stubs import log

DRIVER_DIR = os.path.abspath(
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineVirtual",
	),
)

PACKAGE = "brlMultilineVirtual"


class FakeIoThread:
	"""Collects the calls a slot queues, so a test decides when they run.

	NVDA's real thread runs them as APCs on a background thread. Holding them here instead
	is what lets a test check that a write was queued exactly once, which is the property
	worth checking.
	"""

	def __init__(self):
		self.queued: list[tuple[object, int]] = []

	def queueAsApc(self, func, param: int = 0):
		self.queued.append((func, param))

	def flush(self) -> int:
		""":return: how many queued calls were run."""
		queued, self.queued = self.queued, []
		for func, param in queued:
			func(param)
		return len(queued)


bgThread = FakeIoThread()
"""The thread `deviceSlot` queues onto. Tests flush it to let a queued write happen."""


class FakeDriver:
	"""A braille display driver as far as a slot is concerned.

	Records what it was shown, so that a test can check both what reached it and how often.
	"""

	def __init__(self, isThreadSafe: bool = True, failOnDisplay: bool = False):
		self.isThreadSafe = isThreadSafe
		self.failOnDisplay = failOnDisplay
		self.written: list[list[int]] = []
		self.terminated = False
		self._suppressDisplayClear = False

	def display(self, cells):
		if self.failOnDisplay:
			raise OSError("the display has gone away")
		self.written.append(list(cells))

	def terminate(self):
		self.terminated = True


def resetStubs() -> None:
	"""Empty the recorded log and the queued writes. Call from `setUp`."""
	log.messages.clear()
	bgThread.queued.clear()


def _module(name, **attributes):
	module = types.ModuleType(name)
	for key, value in attributes.items():
		setattr(module, key, value)
	sys.modules[name] = module
	return module


def installVirtualStubs() -> None:
	"""Register the stand-in modules and the package stand-in. Safe to call more than once."""
	if PACKAGE in sys.modules:
		return
	if "logHandler" not in sys.modules:
		# The same log object `_stubs.installStubs` registers, so that whichever of the two
		# runs first, every test in the suite asserts against one recorder.
		_module("logHandler", log=log)
	_module("hwIo", bgThread=bgThread)
	# A package object with a path but no code, so that the driver's relative imports
	# resolve without running its `__init__.py`, which reaches for a great deal more of NVDA
	# and would try to open hardware.
	package = types.ModuleType(PACKAGE)
	package.__path__ = [DRIVER_DIR]
	sys.modules[PACKAGE] = package
