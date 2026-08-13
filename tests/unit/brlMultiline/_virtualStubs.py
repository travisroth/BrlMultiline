# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Enough of NVDA to import and drive the virtual display driver.

`virtualLayout` imports nothing from NVDA at all. `deviceSlot` imports two things: the log,
and the background I/O thread it queues writes onto. The driver class itself needs rather
more — a base class, a driver registry, a handler to be switched — but all of it is shape
rather than behaviour, and modelling it is what lets the orchestration be tested: which
members get opened, what happens to the ones that do not, and who is holding a display when.

What is *not* modelled is a real display's protocol. Whether a Focus answers a query packet
in time is not something a stub can tell you, and that is what the hardware tests are for.

This builds on `_stubs` rather than beside it: it calls `installStubs` first, so that
whichever test module is imported first, there is one `config`, one `braille` and one log
across the whole suite.

Call L{installVirtualStubs} before importing anything under `brlMultilineVirtual`. It is
idempotent, so every test module may call it.
"""

import os
import sys
import types

from ._stubs import callWithSupportedKwargs, installStubs, log

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

CONFIG_SECTION = "BrlMultilineVirtualDisplay"
"""The section `vdConfig` reads. Named here too, so the stub can create it before it is read."""


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


class StubBrailleDisplayDriver:
	"""The base class every braille display driver inherits, reduced to what is depended on.

	`terminate` reproduces NVDA's own, including that it blanks the display through
	`self.display` unless `_suppressDisplayClear` is set, and that it consumes that flag.
	The virtual driver delegates to this and relies on both.
	"""

	name = ""
	description = ""
	isThreadSafe = False
	receivesAckPackets = False
	timeout = 0.2
	numRows = 1
	numCols = 0
	gestureMap = None
	_awaitingAck = False
	_suppressDisplayClear = False

	def __init__(self, port=None):
		pass

	@property
	def numCells(self):
		return self.numRows * self.numCols

	def initSettings(self):
		pass

	def display(self, cells):
		pass

	def _handleAck(self):
		if not self.receivesAckPackets:
			raise NotImplementedError("This display driver does not support ACK packet handling")
		self.handlerAcksReceived = getattr(self, "handlerAcksReceived", 0) + 1
		self._awaitingAck = False

	def terminate(self):
		if getattr(self, "_suppressDisplayClear", False):
			self._suppressDisplayClear = False
			return
		self.display([0] * self.numCells)


class MemberDriver(StubBrailleDisplayDriver):
	"""A member of a virtual display: openable, writable, closable, and countable.

	Class attributes drive its behaviour so that `makeMemberDriver` can produce a variant
	per scenario, which is what `_openDriver` needs — it constructs from a class, as NVDA
	does, rather than being handed an instance.
	"""

	failedOpens = 0
	"""How many construction attempts to fail before succeeding. -1 never succeeds."""

	failInitSettings = False
	"""Whether `initSettings` raises, which happens after the device is open."""

	failOnTerminate = False
	instances: list["MemberDriver"] = []
	"""Instances that were constructed successfully."""

	attempted: list["MemberDriver"] = []
	"""Every instance, including those whose construction raised.

	Separate from L{instances} because a driver that raises part way through construction is
	exactly what the caller cannot reach, and whether it gets closed anyway is the thing
	worth asserting. A real driver has its device open by this point.
	"""

	openAttempts = 0

	def __init__(self, port=None):
		super().__init__(port)
		type(self).openAttempts += 1
		self.port = port
		self.written: list[list[int]] = []
		self.terminated = 0
		# Recorded before the failure, so a test can find an instance the caller never saw.
		type(self).attempted.append(self)
		failures = type(self).failedOpens
		if failures < 0 or type(self).openAttempts <= failures:
			raise RuntimeError(f"No {type(self).name} display found")
		type(self).instances.append(self)

	def initSettings(self):
		if type(self).failInitSettings:
			raise RuntimeError(f"{type(self).name} settings are unwell")

	def display(self, cells):
		self.written.append(list(cells))

	def terminate(self):
		self.terminated += 1
		if type(self).failOnTerminate:
			raise OSError("the display has gone away")
		super().terminate()


driverRegistry: dict[str, type] = {}
"""What `_getDisplayDriver` resolves. Tests fill it through L{makeMemberDriver}."""


def makeMemberDriver(name: str, numRows: int = 1, numCols: int = 40, **attributes) -> type:
	"""Register a member driver class, and return it.

	:param name: the driver name, as `_getDisplayDriver` will be asked for.
	:param numRows: rows the display reports.
	:param numCols: columns the display reports.
	:param attributes: any other class attribute, such as `failedOpens` or `isThreadSafe`.
	:return: the class.
	"""
	namespace = {
		"name": name,
		"description": f"{name} display",
		"numRows": numRows,
		"numCols": numCols,
		"isThreadSafe": True,
		"instances": [],
		"attempted": [],
		"openAttempts": 0,
		"failedOpens": 0,
		"failInitSettings": False,
		"failOnTerminate": False,
	}
	namespace.update(attributes)
	driverClass = type(f"MemberDriver_{name}", (MemberDriver,), namespace)
	driverRegistry[name] = driverClass
	return driverClass


def getDisplayDriver(name: str) -> type:
	"""Stand in for `braille.display._getDisplayDriver`."""
	try:
		return driverRegistry[name]
	except KeyError:
		raise ImportError(f"No module named 'brailleDisplayDrivers.{name}'") from None


def resetStubs() -> None:
	"""Empty everything a test could have dirtied. Call from `setUp`."""
	import braille
	import config

	log.messages.clear()
	bgThread.queued.clear()
	driverRegistry.clear()
	braille.handler = None
	config.conf[CONFIG_SECTION]["devices"] = []


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
	# One `config`, one `braille`, one log across the suite, whichever module is imported
	# first. `installStubs` is idempotent and returns early once it has run.
	installStubs()
	_module("hwIo", bgThread=bgThread)
	_module("extensionPoints", callWithSupportedKwargs=callWithSupportedKwargs)

	import braille
	import config

	driverModule = _module("braille.display.driver", BrailleDisplayDriver=StubBrailleDisplayDriver)
	braille.display.driver = driverModule
	braille.display._getDisplayDriver = getDisplayDriver
	config.conf[CONFIG_SECTION] = {"devices": []}

	# A package object with a path but no code, so that the driver's relative imports
	# resolve without running its `__init__.py`. Loading that deliberately is what
	# L{loadDriver} is for.
	package = types.ModuleType(PACKAGE)
	package.__path__ = [DRIVER_DIR]
	sys.modules[PACKAGE] = package


def loadDriver():
	"""Import the driver's `__init__.py` as a module, and return it.

	The same manoeuvre `_stubs.loadPlugin` performs, and for the same reason: the package
	stand-in has a path but no code, so that importing `brlMultilineVirtual.virtualLayout`
	does not construct hardware. Testing the driver class means loading that code on purpose.

	:return: the loaded module, whose `BrailleDisplayDriver` is the virtual driver.
	"""
	import importlib.util

	name = f"{PACKAGE}.driver"
	if name in sys.modules:
		return sys.modules[name]
	installVirtualStubs()
	spec = importlib.util.spec_from_file_location(name, os.path.join(DRIVER_DIR, "__init__.py"))
	assert spec is not None and spec.loader is not None
	# The file is named __init__.py, so importlib would otherwise treat it as a package of
	# its own and its relative imports would resolve to a second copy of every module.
	spec.submodule_search_locations = None
	module = importlib.util.module_from_spec(spec)
	module.__package__ = PACKAGE
	sys.modules[name] = module
	spec.loader.exec_module(module)
	return module
