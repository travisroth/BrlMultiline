# Copyright (C) 2026 Travis Roth
# This file is covered by the GNU General Public License version 2.

"""Enough of NVDA, and enough of a Monarch, to import and drive the Monarch display driver.

`monarch.py` and `pinBuffer.py` import nothing from NVDA and are tested directly, in
`test_monarchPins`. This file exists for the other half — the driver class — where the
behaviour that has actually gone wrong lives: reconnection, termination racing it, what
happens to input state across a reopen, whether a routing press at the wrong pitch does
something or nothing. Every one of those bugs was invisible to the arithmetic tests.

What is modelled is the *shape* of a HID device: capability structures with the fields the
driver reads, a handle that can be written to, closed, and made to fail. What is not modelled
is Windows. `_findPinCap` walks `hidpi`, `winBindings.hid` and `ctypes` to enumerate output
button caps, and reproducing that would be reproducing the operating system; the decision it
reaches is `_isPinCap`, which takes one capability and is a pure function, so that is tested
directly and the enumeration around it is stubbed out per test.

Call L{installMonarchStubs} before importing anything under `brlMultilineMonarch`. It is
idempotent, so every test module may call it.
"""

import os
import sys
import types

from ._stubs import _AutoPropertyMeta, log
from ._virtualStubs import (
	StubBrailleDisplayDriver,
	installVirtualStubs,
)

DRIVER_DIR = os.path.abspath(
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"..",
		"..",
		"addon",
		"brailleDisplayDrivers",
		"brlMultilineMonarch",
	),
)

PACKAGE = "brlMultilineMonarch"

HID_USAGE_PAGE_BRAILLE = 0x41

PIN_REPORT_ID = 0x21
PIN_USAGE = 0x301
PIN_COUNT = 3840
OUTPUT_REPORT_BYTES = 481
"""The Monarch's own numbers, repeated here so a stub that drifts from the driver is caught
by the tests rather than by the hardware."""


class ProtocolType:
	"""`bdDetect.ProtocolType`, of which only the HID member is ever compared."""

	HID = "hid"
	SERIAL = "serial"


class NotRange:
	def __init__(self, usage: int = 0, dataIndex: int = 0):
		self.Usage = usage
		self.DataIndex = dataIndex


class Range:
	def __init__(self, usageMin: int = 0, usageMax: int = 0, dataIndexMin: int = 0, dataIndexMax: int = 0):
		self.UsageMin = usageMin
		self.UsageMax = usageMax
		self.DataIndexMin = dataIndexMin
		self.DataIndexMax = dataIndexMax


class CapUnion:
	"""The `u1` union both capability structures carry.

	Both members exist at once here, where in ctypes they overlay each other. That is a
	safe simplification only because everything under test reads whichever one `IsRange`
	says is live, and a test that read the wrong one would be testing the stub.
	"""

	def __init__(self, notRange=None, valueRange=None):
		self.NotRange = notRange or NotRange()
		self.Range = valueRange or Range()


class FakeButtonCap:
	"""`hidpi.HIDP_BUTTON_CAPS`, with every field `_isPinCap` reads.

	Defaults describe the Monarch's real pin array as the hardware reported it: one usage,
	0x301, repeated 3,840 times on output report 0x21 of the braille page. Tests change one
	field at a time to make a near miss, which is the case that matters — a display this
	nearly matches is one the driver must refuse rather than write 480 raw bytes to.
	"""

	def __init__(
		self,
		usagePage: int = HID_USAGE_PAGE_BRAILLE,
		reportID: int = PIN_REPORT_ID,
		usage: int = PIN_USAGE,
		reportCount: int = PIN_COUNT,
		isRange: bool = False,
		usageMax: int | None = None,
		isAlias: bool = False,
		linkCollection: int = 1,
		linkUsage: int = 0x300,
		linkUsagePage: int = HID_USAGE_PAGE_BRAILLE,
		bitField: int = 0x02,
		dataIndex: int = 0,
	):
		self.UsagePage = usagePage
		self.ReportID = reportID
		self.IsAlias = isAlias
		self.BitField = bitField
		self.LinkCollection = linkCollection
		self.LinkUsage = linkUsage
		self.LinkUsagePage = linkUsagePage
		self.IsRange = isRange
		self.ReportCount = reportCount
		if usageMax is None:
			usageMax = usage + PIN_COUNT - 1
		self.u1 = CapUnion(
			notRange=NotRange(usage, dataIndex),
			valueRange=Range(usage, usageMax, dataIndex, dataIndex),
		)


class FakeValueCap:
	"""An input value cap, as `_buildInputUsageMap` reads one."""

	def __init__(self, usage: int, dataIndex: int):
		self.IsRange = False
		self.u1 = CapUnion(notRange=NotRange(usage, dataIndex))


class FakeCaps:
	"""`hwIo.hid.Hid.caps`, reduced to the three numbers anything here asks for."""

	def __init__(self, outputButtonCaps: int = 1, outputReportByteLength: int = OUTPUT_REPORT_BYTES):
		self.NumberOutputButtonCaps = outputButtonCaps
		self.OutputReportByteLength = outputReportByteLength
		self.InputReportByteLength = 64


class FakeHid:
	"""An open HID handle: writable, closable, and able to go away.

	Carries `pinCap`, the answer the enumeration would have reached for it, so a test can
	give one device a Monarch's pin array and the next one none at all without going near
	ctypes. See this module's docstring.
	"""

	def __init__(
		self,
		path: str = r"\\?\hid#monarch",
		usagePage: int = HID_USAGE_PAGE_BRAILLE,
		pinCap: object | None = None,
		writeFails: bool = False,
		inputValueCaps=None,
		inputButtonCaps=None,
		writeHangs: bool = False,
	):
		self.path = path
		self.usagePage = usagePage
		self.pinCap = FakeButtonCap() if pinCap is None else pinCap
		self.writeFails = writeFails
		self.writeHangs = writeHangs
		"""Never take a write, as the Monarch did on 2026-09-14. The bounded write gives up, so
		what the driver sees is `WriteTimedOut`."""
		self.caps = FakeCaps()
		self._pd = object()
		self.inputValueCaps = list(inputValueCaps or [])
		self.inputButtonCaps = list(inputButtonCaps or [])
		self._onReceive = None
		self._onReadError = None
		self.written: list[bytes] = []
		self.writeAttempts = 0
		"""Every write tried, including the ones that failed or hung."""
		self.closed = 0

	def write(self, data: bytes) -> None:
		self.writeAttempts += 1
		if self.writeHangs:
			from brlMultilineMonarch.hidWrite import WriteTimedOut

			raise WriteTimedOut("the device did not take a write")
		if self.writeFails:
			raise OSError(1167, "The device is not connected")
		self.written.append(bytes(data))

	def close(self) -> None:
		self.closed += 1

	def readFails(self, error: int = 1167) -> None:
		""":raises OSError: when nothing handled the error, as NVDA's I/O thread would see."""
		if not self._onReadError or not self._onReadError(error):
			raise OSError(error, "The device is not connected")


class FakeOutputReport:
	"""`hwIo.hid.HidOutputReport`: a zeroed buffer of the report's length, ID in byte zero."""

	def __init__(self, device, reportID: int):
		self.reportID = reportID
		self.data = bytes([reportID]) + bytes(device.caps.OutputReportByteLength - 1)


class FakeDataItem:
	"""One decoded field of an input report."""

	def __init__(self, dataIndex: int, rawValue: int = 0, on: bool = False):
		self.DataIndex = dataIndex
		self.u1 = types.SimpleNamespace(RawValue=rawValue, On=on)


class FakeInputReport:
	"""`hwIo.hid.HidInputReport`, carrying whatever items the test put in `data`.

	Reports here are the list of decoded items rather than bytes on the wire: what is under
	test is what the driver does with a touch, not Windows' report parser.
	"""

	def __init__(self, device, data):
		self._items = list(data) if data else []

	def getDataItems(self):
		return list(self._items)


hidDevices: list[FakeHid] = []
"""What `hwIo.hid.Hid` will hand out, in order. Tests fill it; an empty list means the
device cannot be opened, which is what a Bluetooth link that is still down looks like."""

hidOpenAttempts: list[str] = []
"""Every path `hwIo.hid.Hid` was asked for, so a test can see how hard the driver tried."""


def _openHid(path, onReceive=None, **kwargs):
	hidOpenAttempts.append(path)
	if not hidDevices:
		raise OSError(2, "The system cannot find the file specified")
	device = hidDevices.pop(0)
	device.path = path
	device._onReceive = onReceive
	return device


tryPorts: list[tuple] = []
"""What `_getTryPorts` yields: the (protocol, id, port, info) tuples NVDA's discovery
produces. Defaulted by L{resetMonarchStubs} to a single HID port."""


class StubHidBrailleDriver(StubBrailleDisplayDriver, metaclass=_AutoPropertyMeta):
	"""`hidBrailleStandard.HidBrailleDriver`, reduced to what the Monarch driver inherits.

	The metaclass is `AutoPropertyObject`'s, because NVDA's drivers are one and the Monarch's
	settings are declared in those terms: `pitch` is `_get_pitch` and `_set_pitch`, and
	without the metaclass assigning to it would set a plain attribute and change nothing.

	The parts kept are the parts the subclass leans on and could break: the constructor's
	contract that it either opens a device or raises `RuntimeError`, the key state it sets
	up, the button cap map built from the device, and a `terminate` that closes `_dev`
	unconditionally — which is precisely why the subclass must not take that path when the
	device has gone.
	"""

	name = "hidBrailleStandard"
	description = "Standard HID Braille Display"
	isThreadSafe = True
	supportsAutomaticDetection = True
	gestureMap = None
	_numberOfCellsValueCaps = None

	def __init__(self, port="auto"):
		self.numRows = 1
		self.numCols = 0
		for portType, portId, portPath, portInfo in self._getTryPorts(port):  # noqa: B007
			if portType != ProtocolType.HID:
				continue
			try:
				self._dev = _openHid(portPath, onReceive=self._hidOnReceive)
			except OSError:
				continue
			if self._dev.usagePage != HID_USAGE_PAGE_BRAILLE:
				self._dev.close()
				continue
			self.numRows = 8
			self.numCols = 32
			self._maxNumberOfCells = self.numCells
			break
		else:
			raise RuntimeError("No display found")
		self._inputButtonCapsByDataIndex = self._collectInputButtonCapsByDataIndex()
		self._keysDown = set()
		self._ignoreKeyReleases = False

	@classmethod
	def _getTryPorts(cls, port):
		yield from tryPorts

	def _collectInputButtonCapsByDataIndex(self):
		"""Keyed by data index, as NVDA's is. Identity of the *device* is what matters here.

		The value is the device the map was built from, so a test can assert that a reconnect
		rebuilt it rather than carrying the dead handle's numbering across.
		"""
		return {cap.u1.NotRange.DataIndex: self._dev for cap in self._dev.inputButtonCaps}

	def _hidOnReceive(self, data):
		report = FakeInputReport(self._dev, data)
		keys = []
		for item in report.getDataItems():
			if item.DataIndex in self._inputButtonCapsByDataIndex and item.u1.On:
				keys.append(item.DataIndex)
		if len(keys) > len(self._keysDown):
			self._ignoreKeyReleases = False
		elif len(keys) < len(self._keysDown):
			self._handleKeyRelease()
		self._keysDown = keys

	def _handleKeyRelease(self):
		if self._ignoreKeyReleases or not self._keysDown:
			return
		self._ignoreKeyReleases = True

	def display(self, cells):
		"""Never reached by the Monarch driver, which overrides it. Recorded if it ever is."""
		self.cellReportsWritten = getattr(self, "cellReportsWritten", 0) + 1

	def terminate(self):
		try:
			super().terminate()
		finally:
			self._dev.close()


class StubHidInputGesture:
	"""`hidBrailleStandard.InputGesture`, as far as the Monarch's subclass uses one.

	`cellIndexes` comes from the driver's `pendingCellIndexes`, which stands in for decoding
	routing keys out of the data indexes, and `keyNames` from `pendingKeyNames` likewise. What the subclass does with them afterwards is the
	thing under test.
	"""

	source = "hidBrailleStandard"
	id = "routing"

	def __init__(self, driver, dataIndices):
		self.driver = driver
		self.dataIndices = list(dataIndices)
		self.cellIndexes = list(getattr(driver, "pendingCellIndexes", []) or [])
		# What NVDA would have called the keys, set by the test the way `pendingCellIndexes` is.
		# The real constructor builds `id` by joining these, which is the contract the driver's
		# naming checks before it renames anything.
		self.keyNames = list(getattr(driver, "pendingKeyNames", []) or [])
		if self.keyNames:
			self.id = "+".join(self.keyNames)

	def _get_identifiers(self):
		ids = []
		if self.cellIndexes:
			cells = "+".join(str(index + 1) for index in self.cellIndexes)
			ids.append(f"br({self.source}):{self.id}{cells}")
		ids.append(f"br({self.source}):{self.id}")
		return ids


class BraillePageUsageID:
	"""The braille page's usages, of which the driver re-exports the enum and uses none."""

	BRAILLE_ROW = 0x2
	EIGHT_DOT_BRAILLE_CELL = 0x3


usbDrivers: list[tuple] = []
bluetoothDrivers: list[tuple] = []
"""What `bdDetect`'s two lookups return: (driver name, match) pairs. Tests fill them to
model a Monarch present over one transport, both, or neither."""

detectionFails: set[str] = set()
"""Transports whose lookup raises, which is a thing `bdDetect` does when nothing is
registered — and the reason the driver was invisible in NVDA's settings to begin with."""


def _getDriversForConnectedUsbDevices():
	if "usb" in detectionFails:
		raise LookupError("no USB detection data")
	yield from usbDrivers


def _getDriversForPossibleBluetoothDevices():
	if "bluetooth" in detectionFails:
		raise LookupError("no Bluetooth detection data")
	yield from bluetoothDrivers


def _forDriver(driverName: str):
	"""`bdDetect.getConnectedUsbDevicesForDriver` and its Bluetooth twin.

	Always `LookupError`, which is what `bdDetect` raises for a driver it has no detection
	data registered for. That is not an edge case here: it is the answer for HID braille,
	whose matching is special cased inside `bdDetect` rather than registered, and it is the
	reason the Monarch driver has to ask the questions the other way round. The virtual
	display reads the same refusal as "says nothing either way", so this keeps that path
	behaving as it did before this module existed and made `bdDetect` importable at all.
	"""
	raise LookupError(f"no detection data for {driverName}")


class Scheduling:
	"""How `core.callLater` behaves for the test that is running.

	`fails` models NVDA before its wx app exists, where `core.callLater` raises
	`NVDANotInitializedError`. `returnsNothing` models being called off the main thread,
	where it schedules the timer through `wx.CallAfter` and hands back None — so the driver
	gets no handle to cancel, which is the case the reconnect generation exists for.
	"""

	def __init__(self):
		self.fails = False
		self.returnsNothing = False


scheduling = Scheduling()


def _coreCallLater(milliseconds, work, *args, **kwargs):
	from ._stubs import callLaterQueue

	if scheduling.fails:
		raise RuntimeError("Cannot schedule callable, wx.App is not initialized")
	timer = callLaterQueue.callLater(milliseconds, work, *args, **kwargs)
	return None if scheduling.returnsNothing else timer


def resetMonarchStubs() -> None:
	"""Empty everything a test could have dirtied. Call from `setUp`."""
	import braille

	from ._stubs import callAfterQueue, callLaterQueue
	from ._virtualStubs import resetStubs

	resetStubs()
	log.messages.clear()
	callAfterQueue.discard()
	callLaterQueue.pending.clear()
	hidDevices.clear()
	hidOpenAttempts.clear()
	usbDrivers.clear()
	bluetoothDrivers.clear()
	detectionFails.clear()
	scheduling.fails = False
	scheduling.returnsNothing = False
	tryPorts[:] = [(ProtocolType.HID, "hid", r"\\?\hid#monarch", {})]
	braille.handler = None
	# The patch is process wide, so a driver that registered and did not terminate would
	# leave the next test's switch behaving unlike a fresh NVDA.
	from brlMultilineVirtual import handover

	handover._releasingNames.clear()
	handover._uninstallIfUnwanted()


def _module(name, **attributes):
	module = types.ModuleType(name)
	for key, value in attributes.items():
		setattr(module, key, value)
	sys.modules[name] = module
	return module


def installMonarchStubs() -> None:
	"""Register the stand-in modules and the package stand-in. Safe to call more than once."""
	if PACKAGE in sys.modules:
		return
	# `_virtualStubs` brings the log, config, braille, the driver base class and the wx
	# queues, and registers `brlMultilineVirtual` — which this driver imports `handover`
	# from for real, because that is the code under test on the switching path.
	installVirtualStubs()

	# The port constants NVDA's dialog labels its choices with. Pairs, because the driver
	# builds its mapping with `ports.update((USB_PORT,))`.
	constants = sys.modules["braille.constants"]
	constants.AUTOMATIC_PORT = ("auto", "Automatic")
	constants.USB_PORT = ("usb", "USB")
	constants.BLUETOOTH_PORT = ("bluetooth", "Bluetooth")

	_module(
		"bdDetect",
		HID_USAGE_PAGE_BRAILLE=HID_USAGE_PAGE_BRAILLE,
		ProtocolType=ProtocolType,
		getDriversForConnectedUsbDevices=_getDriversForConnectedUsbDevices,
		getDriversForPossibleBluetoothDevices=_getDriversForPossibleBluetoothDevices,
		getConnectedUsbDevicesForDriver=_forDriver,
		getPossibleBluetoothDevicesForDriver=_forDriver,
		_getStandardHidDriverName=lambda: "hidBrailleStandard",
	)
	hidModule = _module(
		"hwIo.hid",
		Hid=_openHid,
		HidInputReport=FakeInputReport,
		HidOutputReport=FakeOutputReport,
	)
	import hwIo

	hwIo.hid = hidModule
	# `core.callLater` rather than `wx.CallLater`, because that is what the driver reaches
	# for and the difference is the point: NVDA's marshals timer creation to the GUI thread,
	# and off that thread it returns nothing to cancel. `scheduling.fails` makes the call
	# raise, which is how a driver that cannot schedule its own recovery is tested.
	_module("core", callLater=_coreCallLater)
	_module(
		"autoSettingsUtils",
	)
	_module("autoSettingsUtils.driverSetting", DriverSetting=_DriverSetting)
	_module("autoSettingsUtils.utils", StringParameterInfo=_StringParameterInfo)
	_module(
		"brailleDisplayDrivers.hidBrailleStandard",
		BraillePageUsageID=BraillePageUsageID,
		HidBrailleDriver=StubHidBrailleDriver,
		InputGesture=StubHidInputGesture,
	)
	# `brailleDisplayDrivers.brlMultilineVirtual` is the same object `_virtualStubs`
	# registered as `brlMultilineVirtual`, not a second copy: `handover` keeps its registry
	# in module globals, and two copies would mean the driver registering in one while the
	# tests inspect the other.
	drivers = _module(
		"brailleDisplayDrivers",
		brlMultilineVirtual=sys.modules["brlMultilineVirtual"],
		hidBrailleStandard=sys.modules["brailleDisplayDrivers.hidBrailleStandard"],
	)
	drivers.__path__ = []
	sys.modules["brailleDisplayDrivers.brlMultilineVirtual"] = sys.modules["brlMultilineVirtual"]

	package = types.ModuleType(PACKAGE)
	package.__path__ = [DRIVER_DIR]
	sys.modules[PACKAGE] = package


class _DriverSetting:
	"""`autoSettingsUtils.driverSetting.DriverSetting`, as far as the driver declares one."""

	def __init__(self, id, displayName, defaultVal=None, useConfig=True, availableInSettingsRing=False):
		self.id = id
		self.displayName = displayName
		self.defaultVal = defaultVal
		self.useConfig = useConfig
		self.availableInSettingsRing = availableInSettingsRing


class _StringParameterInfo:
	"""One choice of a string driver setting."""

	def __init__(self, id, displayName):
		self.id = id
		self.displayName = displayName


def loadDriver():
	"""Import the Monarch driver's `__init__.py` as a module, and return it.

	The same manoeuvre `_virtualStubs.loadDriver` performs, for the same reason: the package
	stand-in has a path but no code, so `brlMultilineMonarch.monarch` can be imported without
	running the driver's `__init__.py`. Testing the driver class means loading it on purpose.

	:return: the loaded module, whose `BrailleDisplayDriver` is the Monarch driver.
	"""
	import importlib.util

	name = f"{PACKAGE}.driver"
	if name in sys.modules:
		return sys.modules[name]
	installMonarchStubs()
	spec = importlib.util.spec_from_file_location(name, os.path.join(DRIVER_DIR, "__init__.py"))
	assert spec is not None and spec.loader is not None
	spec.submodule_search_locations = None
	module = importlib.util.module_from_spec(spec)
	module.__package__ = PACKAGE
	sys.modules[name] = module
	spec.loader.exec_module(module)
	return module


def makeDriverClass(driverModule):
	"""Subclass the driver so it can be constructed without Windows' HID capability API.

	`_findPinCap` is the one method that cannot run here — it walks `hidpi`, `winBindings.hid`
	and `ctypes` — so it is replaced by asking the fake device what it would have found. The
	decision that method reaches, `_isPinCap`, is a pure function of one capability and is
	tested directly rather than through this.

	`_sendReport` is the other. It hands a Windows handle to `hidWrite.writeWithin`, which is
	tested on a real handle in `test_hidWrite`; here the report goes to the fake device, whose
	`writeHangs` raises what `writeWithin` raises when a device never takes a write.

	Nothing else is overridden. The point of these tests is the driver's own code, so
	anything replaced here is a thing they no longer cover.

	:param driverModule: what L{loadDriver} returned.
	:return: a driver class that can be constructed against a L{FakeHid}.
	"""

	class TestableMonarchDriver(driverModule.BrailleDisplayDriver):
		def _findPinCap(self, device):
			# A falsy answer is None: a fake device saying it has no pin array must reach the
			# driver as "not a Monarch", not as a capability object that happens to be False.
			return getattr(device, "pinCap", None) or None

		def _sendReport(self, device, data):
			device.write(data)

	return TestableMonarchDriver
