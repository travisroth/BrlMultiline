# BrlMultiline: a braille display driver for the Humanware Monarch.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Drive the Monarch through its pin report rather than its braille cell reports.

Read `docs/design/monarch-driver-plan.md` before changing anything here, and
`docs/design/tactile-graphics-plan.md` for how the hardware was established.

The short of it. The Monarch declares an output report, 0x21, of 3,840 one bit fields — one
per pin of its 96 by 40 panel. NVDA cannot see it, because `hidBrailleStandard` reads output
*value* caps and reads button caps only for input, and this is an output button array. Every
NVDA braille write goes to the eight cell reports instead, which reach only 2,048 of the pins
and leave a spacer column between every pair.

This driver inherits all of `hidBrailleStandard` — device discovery over USB and Bluetooth,
the report descriptor, key decoding, routing, the gesture map — and replaces one thing: where
the cells go. They are drawn into a pin buffer and the whole panel is written as one report.

What that buys, beyond graphics:

- A choice of how many braille lines: 8 rows with a blank row between them, or 10 without.
  All eight dots are drawn either way, so a caret and an eight dot table work in both.
- Graphics and text on one surface, composed here, with no mode switch and nothing suspended.
- Touch at pin resolution, which NVDA discards, so a drawing can be pointed at.
- Bluetooth that survives a dropout, using `brlMultilineVirtual`'s approach.
"""

import collections
import threading
import time
from typing import TYPE_CHECKING, Optional

import addonHandler
from autoSettingsUtils.driverSetting import DriverSetting
from autoSettingsUtils.utils import StringParameterInfo
import bdDetect
import braille
import braille.display.driver
import hwIo.hid
import inputCore
from braille.constants import AUTOMATIC_PORT, BLUETOOTH_PORT, USB_PORT
from brailleDisplayDrivers.brlMultilineVirtual import handover
from brailleDisplayDrivers.hidBrailleStandard import (
	BraillePageUsageID,
	HidBrailleDriver,
	InputGesture as HidInputGesture,
)
from logHandler import log

from . import hidWrite, monarch
from .pinBuffer import PinBuffer

if TYPE_CHECKING:
	from collections import OrderedDict

try:
	addonHandler.initTranslation()
except Exception:
	# Translation is a nicety; failing to set it up must not cost the user their braille.
	log.debugWarning("BrlMultiline: could not initialise Monarch driver translations", exc_info=True)


DRIVER_NAME = "brlMultilineMonarch"
"""The driver name, shared with the gesture so the two cannot drift apart."""

OPEN_ATTEMPTS = 3
OPEN_RETRY_DELAY = 0.3
"""A device Windows lists is not always openable, especially just after a release.

Both numbers are `brlMultilineVirtual`'s, established on hardware there. See its plan.
"""

POLL_INTERVAL = 5.0
POLL_BACKOFF_LIMIT = 60.0
"""How often to look for a device that has dropped, and how far apart that may grow."""

WRITE_TIMEOUT = 3.0
"""Seconds the Monarch has to take a pin report before it is treated as gone.

A report is 481 bytes on a USB link, which takes milliseconds; the pins move after the device
has taken it. So this is not a latency budget. It is the longest a stuck device may hold
NVDA's I/O thread, and through it every other display's writes. See `hidWrite`.
"""

ABANDONED_WRITE_LIMIT = 3
"""How many writes Windows may be holding past their cancellation before reconnecting stops.

Such a write keeps its buffer and an event handle until it completes, which may be never. Each
automatic reconnection probes the device with a write, so without a limit a device that ignores
cancellation would leak one of each a minute for as long as NVDA ran. Past the limit the
reader has to reselect the display or restart NVDA, which is a retry a person chose to make.
"""


def _callLater(milliseconds: int, work, *args):
	"""Schedule something, from whichever thread this is.

	`core.callLater` rather than `wx.CallLater` because this driver declares `isThreadSafe`,
	so NVDA may call into it from the I/O thread — and a read error, which is exactly what
	starts a reconnection, arrives there. `wx.CallLater` constructs a timer on the calling
	thread; `core.callLater` marshals that to the GUI thread first, which is the entire reason
	NVDA offers it.

	It also means the return value cannot be relied on: off the main thread `core.callLater`
	returns whatever `wx.CallAfter` returned, which is None, and the timer is created later.
	So the caller must not treat a returned handle as proof anything is scheduled, and must
	not rely on being able to cancel it. `_stopPolling` covers that with a generation number.

	:param milliseconds: how long to wait.
	:param work: what to run.
	:param args: what to pass it.
	:return: a cancellable timer, or None when there is nothing to cancel.
	:raises Exception: if there is no wx, or NVDA is not initialised enough to schedule.
	"""
	try:
		import core

		return core.callLater(milliseconds, work, *args)
	except ImportError:
		import wx

		return wx.CallLater(milliseconds, work, *args)


def _stopTimer(timer) -> None:
	"""Cancel a timer, if there is one and it can be cancelled. Best effort.

	:param timer: what `_callLater` returned, which may be None.
	"""
	if timer is None:
		return
	try:
		timer.Stop()
	except Exception:
		log.debugWarning("BrlMultiline: could not stop a Monarch timer", exc_info=True)


class CellGlyph:
	"""A shape drawn in place of a run of braille cells, filling their whole slots.

	A braille cell is two dot columns and a blank one; the blank is there so a reader can tell
	one cell from the next, not because the hardware needs it. A glyph fills all three, giving
	3 by 4 instead of 2 by 4 per cell without moving anything: the cell after the run still
	starts where it did, so routing, layout and scrolling are untouched.

	**A run rather than a single cell, because that is the shape of what it replaces.** NVDA
	already writes short strings to stand for roles and states — "btn" for a button, "cbo" for
	a combo box, three cells for a checkbox — and the useful thing a pin display can do is draw
	those as a symbol instead of spelling them. Replacing three cells with one would shift
	everything after it and break routing; replacing three cells with a nine by four drawing
	changes nothing but what those pins say. So a glyph is exactly as wide as the text it
	stands in for, and the fallback is that text's own cells.

	Built through `BrailleDisplayDriver.newGlyph` rather than directly, so the add-on never
	imports this package.
	"""

	def __init__(self, pattern: PinBuffer, fallback):
		"""
		:param pattern: the shape, clipped to the run's slots.
		:param fallback: the braille cells this stands in for, which the add-on has written
			into the buffer at the same position — a list, or one value for a single cell. The
			glyph is drawn only while the cells there still read it, all of them.
		"""
		self.pattern = pattern
		self.fallback = [fallback] if isinstance(fallback, int) else list(fallback)

	@property
	def cells(self) -> int:
		""":return: how many cells the glyph occupies."""
		return len(self.fallback)

	def matches(self, cells: list[int], index: int) -> bool:
		"""Whether the frame still carries the text this glyph stands in for.

		:param cells: the frame.
		:param index: where the run starts.
		:return: whether every cell of the run reads what was registered.
		"""
		if index < 0 or index + self.cells > len(cells):
			return False
		return cells[index : index + self.cells] == self.fallback


def _withoutOverlaps(glyphs: dict) -> dict:
	""":return: the glyphs whose runs do not tread on one another.

	Two symbols sharing a cell is a caller's mistake with no sensible rendering — the second
	would be drawn over the first and neither would be readable — so the run that starts later
	is dropped. Taken in index order so the answer does not depend on how the caller happened
	to build its dictionary.

	:param glyphs: the index of each glyph's first cell, to the glyph.
	"""
	kept: dict = {}
	reach = -1
	for index in sorted(glyphs):
		glyph = glyphs[index]
		if index <= reach:
			log.debugWarning(
				f"BrlMultiline: a glyph at cell {index} overlaps the one before it and was dropped",
			)
			continue
		kept[index] = glyph
		reach = index + glyph.cells - 1
	return kept


def _standardHidDriverName() -> str:
	""":return: the name `bdDetect` files HID braille devices under.

	Asked of `bdDetect` rather than hard coded, because that is where the special case lives.
	"""
	try:
		return bdDetect._getStandardHidDriverName()
	except Exception:
		return "hidBrailleStandard"


class BrailleDisplayDriver(HidBrailleDriver):
	"""The Monarch, driven by its pins."""

	name = DRIVER_NAME
	# Translators: the name of the Monarch braille display driver, shown in NVDA's display list.
	description = _("BrlMultiline: Humanware Monarch (pin mode)")

	isThreadSafe = True
	supportsAutomaticDetection = False
	"""False deliberately.

	Automatic detection would put this driver in competition with `hidBrailleStandard` for the
	same device, and only one can hold it: `hwIo.hid.Hid` opens exclusively. The user chooses
	a display here, as they already do for `brlMultilineVirtual`.
	"""

	# --- Construction ------------------------------------------------------------------

	def __init__(self, port="auto"):
		"""
		:param port: as NVDA passes it; forwarded to the inherited discovery.
		:raises RuntimeError: if no HID braille device is there, or the one that is has no pin
			report and so is not a Monarch.
		"""
		self._pitch = monarch.PITCHES[monarch.PITCH_8_ROW.name]
		self._overlays: dict[str, tuple[int, int, PinBuffer]] = {}
		self._lastCells: list[int] = []
		self._glyphs: dict[int, CellGlyph] = {}
		self._lastTouchPin: Optional[tuple[int, int]] = None
		self._lastTouchCell: Optional[int] = None
		self._pinAtRouting: Optional[tuple[int, int]] = None
		self._repaintPending = False
		# Three locks, and the order between them is the whole of what keeps them from
		# deadlocking: `_stateLock`, then `_lifecycleLock`, then `_writeLock`, and never the
		# other way round. Only `_reopenDevice` ever holds two at once; everywhere else one is
		# released before the next is taken. In particular a failed write reports the device
		# lost *after* letting `_writeLock` go, and `terminate` sets its flag and releases
		# `_lifecycleLock` before the base class blanks the display through `display`.
		self._stateLock = threading.RLock()
		"""What the panel is made of: cells, glyphs, overlays, pitch, the pending flag."""
		self._writeLock = threading.Lock()
		"""One writer at a time on the wire, and the device is not swapped mid-report."""
		self._lifecycleLock = threading.RLock()
		"""The device handle, the termination flag, and the reconnect timer."""
		self._pinCap = None
		self._watched: Optional[tuple] = None
		self._pollTimer = None
		self._pollScheduled = False
		self._pollGeneration = 0
		self._retryDelay = POLL_INTERVAL
		self._reopening = False
		self._writesSuspended = False
		"""Set, under `_writeLock`, by the write that finds the device lost.

		Until a new handle is installed no write is attempted, because the handle that is there
		has just failed one and the next would fail the same way: a hung device would hold NVDA's
		I/O thread for another `WRITE_TIMEOUT` for every frame and repaint already queued. Nothing
		is lost by dropping them. The cells, glyphs and overlays are all kept, and the repaint
		after reconnecting draws from them.
		"""
		self._gaveUp = False
		"""Whether reconnection was stopped at `ABANDONED_WRITE_LIMIT`. Writes stay suspended."""
		self._terminated = False
		self._port = port

		self._openWithRetry(port)
		# Everything from here on can raise on a device that opened perfectly well, and the
		# handle is ours the moment it did. Nothing else can reach this instance once the
		# constructor throws, so it has to let go of the device itself — a leaked exclusive
		# handle takes the display away from every other driver until NVDA restarts, which is
		# exactly what a failure here used to do.
		try:
			self._verifyDotOrder()
			self._pinCap = self._findPinCap(self._dev)
			if self._pinCap is None:
				# Not a Monarch, or a firmware without the pin array. Refuse rather than
				# silently behaving like the standard driver: the user chose this one.
				raise RuntimeError(
					"This HID braille display has no pin report; use Standard HID Braille Display instead",
				)
			self._applyPitch()
			self._watchForDisconnect()
			# NVDA builds the incoming driver before terminating this one, so switching away
			# from here would otherwise hand the next driver a device we are still holding.
			handover.releaseOnSwitch(self.name)
		except Exception:
			self._releaseAfterFailedInit()
			raise
		log.info(
			f"BrlMultiline: Monarch on {monarch.PIN_WIDTH} by {monarch.PIN_HEIGHT} pins, "
			f"{self._pitch.numRows} rows of {self._pitch.numCols} at pitch {self._pitch.name}, "
			f"writing output report 0x{monarch.PIN_REPORT_ID:02X}. "
			f"Settings offered: {[setting.id for setting in self.supportedSettings]}",
		)

	def _releaseAfterFailedInit(self) -> None:
		"""Close the device after a constructor that got past opening it and then failed.

		`_suppressDisplayClear` first, because the base class blanks the display on the way
		out and a half built driver may not be able to: NVDA's own flag for "close this
		without writing to it". `terminate` is safe to call here because everything it touches
		is set before the device is opened.
		"""
		self._suppressDisplayClear = True
		try:
			self.terminate()
		except Exception:
			log.debugWarning("BrlMultiline: Monarch raised while releasing a failed start", exc_info=True)

	def _openWithRetry(self, port) -> None:
		"""Run the inherited constructor, retrying a device that is present but not openable.

		`hidBrailleStandard.__init__` raises `RuntimeError` when it finds nothing. Phase 0 of
		the virtual display plan established that a display NVDA released moments earlier can
		refuse to open twice and then open normally, so a single attempt is not enough when
		switching displays.

		If every attempt fails, the display being switched away from is released and the
		attempts are made again. `_switchDisplay` constructs the incoming driver *before*
		terminating the outgoing one, so switching to this driver from `hidBrailleStandard`
		on the same Monarch means racing a handle NVDA has not let go of yet — and no amount
		of retrying inside that window can win, because the release comes afterwards.

		:param port: as NVDA passes it.
		:raises RuntimeError: if every attempt failed, twice over.
		"""
		error = self._tryOpen(port)
		if error is None:
			return
		if self._releaseOutgoingDisplay():
			error = self._tryOpen(port)
			if error is None:
				return
		raise RuntimeError(f"Monarch did not open after {OPEN_ATTEMPTS} attempts") from error

	def _tryOpen(self, port) -> Optional[BaseException]:
		"""Run the inherited constructor a few times.

		Phase 0 of the virtual display plan established that a display NVDA released moments
		earlier can refuse to open twice and then open normally, so one attempt is not enough
		when switching displays.

		:param port: as NVDA passes it.
		:return: the last error, or None if the device opened.
		"""
		lastError: Optional[BaseException] = None
		for attempt in range(1, OPEN_ATTEMPTS + 1):
			try:
				super().__init__(port)
			except Exception as error:  # noqa: BLE001
				lastError = error
				# A constructor that raised may still have left a device object behind, and the
				# next attempt would then be competing with a handle this one is still holding.
				self._closeStrayDevice()
				if attempt == OPEN_ATTEMPTS:
					break
				log.debugWarning(
					f"BrlMultiline: Monarch did not open on attempt {attempt}, retrying",
					exc_info=True,
				)
				time.sleep(OPEN_RETRY_DELAY)
			else:
				return None
		return lastError

	def _releaseOutgoingDisplay(self) -> bool:
		"""Close the display being switched away from, if it is holding a device.

		`braille.handler.display` still points at the outgoing driver while this constructor
		runs, because `_setDisplay` assigns the new one only once `_switchDisplay` returns. So
		it can be reached, and closing it here is the only way to get a device it is holding
		before our own attempts are exhausted.

		`releaseDisplayNow` also disarms the driver's next `terminate`, because NVDA will call
		it a moment later and a second close would at best log an error.

		Only a driver with a device is touched: there is nothing to gain from terminating
		`noBraille` or a serial display, and every reason not to close something we did not
		need to.

		:return: whether a display was released, and so whether another attempt is worthwhile.
		"""
		handler = braille.handler
		display = getattr(handler, "display", None) if handler is not None else None
		if display is None or display is self or getattr(display, "_dev", None) is None:
			return False
		log.info(
			f"BrlMultiline: releasing {getattr(display, 'name', '?')} so the Monarch can be opened",
		)
		handover.releaseDisplayNow(display)
		return True

	def _closeStrayDevice(self) -> None:
		"""Release a device left behind by a constructor that raised. Best effort.

		`hidBrailleStandard.__init__` closes the devices it rejects, but an unexpected error
		between opening one and finishing can leave `_dev` set on an instance that is about to
		be thrown away, where nothing else can reach it.
		"""
		device = getattr(self, "_dev", None)
		if device is None:
			return
		try:
			device.close()
		except Exception:
			log.debugWarning("BrlMultiline: could not close a stray Monarch handle", exc_info=True)
		self._dev = None

	@classmethod
	def getPossiblePorts(cls) -> "OrderedDict[str, str]":
		"""Offer the automatic, USB and Bluetooth choices the settings dialog shows.

		The third thing keyed on `cls.name` that a subclass of a HID braille driver silently
		loses, after `check` and `_getAutoPorts`. The base implementation asks
		`bdDetect.getConnectedUsbDevicesForDriver(cls.name)` and its Bluetooth twin, both of
		which raise `LookupError` for a driver with no detection data, so it concludes neither
		transport exists and returns an empty mapping. The dialog then shows no port control
		at all, which is what made this driver look less capable than the standard one.

		Answered from `_getAutoPorts` per transport instead, so a choice is offered only when
		a device is actually reachable that way. The plumbing behind the choice already
		worked: `_getTryPorts` passes the flags straight through, so picking USB really did
		restrict to USB even while the dialog was refusing to say so.

		:return: port name to displayable description, in the order the dialog shows them.
		"""
		ports: "OrderedDict[str, str]" = collections.OrderedDict()
		try:
			usb = next(cls._getAutoPorts(usb=True, bluetooth=False), None) is not None
			bluetooth = next(cls._getAutoPorts(usb=False, bluetooth=True), None) is not None
		except Exception:
			log.debugWarning("BrlMultiline: could not list Monarch ports", exc_info=True)
			return ports
		if usb or bluetooth:
			ports.update((AUTOMATIC_PORT,))
			if usb:
				ports.update((USB_PORT,))
			if bluetooth:
				ports.update((BLUETOOTH_PORT,))
		return ports

	@classmethod
	def check(cls) -> bool:
		"""Say whether this driver is worth offering, which decides whether it is listed.

		`getDisplayList` drops any driver whose `check` is False, so without this the driver
		never appears in NVDA's braille settings — which is exactly what happened. The base
		implementation has two ways to say yes and we passed neither: its first branch needs
		`supportsAutomaticDetection`, which is deliberately False here so as not to compete
		with `hidBrailleStandard` for the device, and its second needs `getManualPorts`, which
		is for serial ports and raises `NotImplementedError` for a HID driver.

		`brlMultilineVirtual` lists its members from the drivers themselves rather than
		through `getDisplayList`, which is why the driver showed up there and nowhere else.

		Presence only, and deliberately not more. **This must not open the device**: `check`
		runs for every driver when the settings dialog is built, and `hwIo.hid.Hid` opens
		exclusively, so opening here would take the display away from whatever is driving it.
		That means we cannot confirm a pin report and will answer True for any HID braille
		display. A non Monarch is then listed and refused at construction with a message
		saying so, which is a better failure than being invisible on the device we do support.

		:return: whether a HID braille display is attached.
		"""
		try:
			return next(cls._getAutoPorts(), None) is not None
		except Exception:
			log.debugWarning("BrlMultiline: Monarch availability check failed", exc_info=True)
			return False

	@classmethod
	def _getAutoPorts(cls, usb=True, bluetooth=True):
		"""Find HID braille devices, over both transports.

		The inherited version asks `bdDetect` for devices registered against `cls.name`, and
		HID braille has no such registration: `HidBrailleDriver.registerAutomaticDetection` is
		a no-op because the matching is special cased inside `bdDetect` on usage page 0x41. A
		subclass with a new name therefore finds nothing at all and cannot open anything.

		So this asks the same questions `bdDetect` asks on NVDA's behalf and keeps the answers
		it files under the standard HID driver, which is exactly the set `hidBrailleStandard`
		would have found. Both transports, because the Monarch is used over USB as well as
		Bluetooth and dropping either would be a regression against the standard driver.

		:param usb: whether to look for USB devices.
		:param bluetooth: whether to look for Bluetooth devices.
		:return: the device matches to try, in order.
		"""
		hidName = _standardHidDriverName()
		lookups = []
		if usb:
			lookups.append(bdDetect.getDriversForConnectedUsbDevices)
		if bluetooth:
			lookups.append(bdDetect.getDriversForPossibleBluetoothDevices)
		for lookup in lookups:
			try:
				for driverName, match in lookup():
					if driverName == hidName:
						yield match
			except Exception:
				log.debugWarning("BrlMultiline: Monarch detection failed for one transport", exc_info=True)

	def _verifyDotOrder(self) -> None:
		"""Check our copy of the braille dot layout still matches NVDA's.

		`monarch.py` cannot import from NVDA, so it carries its own copy of
		`tactile.braille._brailleDotCoords`. A silent divergence would scramble every cell, so
		it is compared once here where the import is available. A warning rather than a
		refusal: a changed table upstream is a reason to look, not a reason to leave someone
		without braille.
		"""
		try:
			from tactile.braille import _brailleDotCoords
		except ImportError:
			return
		if list(_brailleDotCoords) != list(monarch._BRAILLE_DOT_COORDS):
			log.warning(
				"BrlMultiline: NVDA's braille dot layout no longer matches the Monarch driver's copy; "
				"cells may render incorrectly. See monarch.BIT_FOR_BLOCK_POSITION.",
			)

	def _findPinCap(self, device):
		"""Look for the output button array that is the pin matrix.

		Declared as one usage repeated 3,840 times rather than as a usage range, so the count
		is in `ReportCount` and not in the usage span. Reading only the usage span is how this
		report stayed hidden: it looks like a single bit.

		Takes the device rather than reading `self._dev`, because a reconnection has to ask
		this of a candidate handle before deciding to install it.

		:param device: the open `hwIo.hid.Hid` to interrogate.
		:return: the `HIDP_VALUE_CAPS`-shaped button cap, or None if this is not a Monarch.
		"""
		try:
			import ctypes

			import hidpi
			import winBindings.hid
			from hwIo.hid import check_HidP_status
		except Exception:
			log.error("BrlMultiline: cannot read HID button caps", exc_info=True)
			return None
		count = device.caps.NumberOutputButtonCaps
		if not count:
			return None
		capsList = (hidpi.HIDP_BUTTON_CAPS * count)()
		number = ctypes.c_ushort(count)
		try:
			check_HidP_status(
				winBindings.hid.HidP_GetButtonCaps,
				hidpi.HIDP_REPORT_TYPE.OUTPUT,
				capsList,
				ctypes.byref(number),
				device._pd,
			)
		except Exception:
			log.debugWarning("BrlMultiline: could not read output button caps", exc_info=True)
			return None
		outputLength = device.caps.OutputReportByteLength
		for cap in capsList[: number.value]:
			if self._isPinCap(cap, outputLength):
				return cap
		return None

	def _isPinCap(self, cap, outputLength: int) -> bool:
		"""Decide whether one output button cap really is the Monarch's pin array.

		Every fact `_writePins` depends on, checked before it is depended on, because a match
		here is what authorises writing 480 raw bytes to output report 0x21. An earlier version
		accepted any large output button array and wrote a Monarch shaped payload regardless of
		which report or usage the capability actually named, which on some other display with a
		big button array would be bytes sent somewhere they were never meant to go.

		The bit width needs no check: a *button* cap is one bit per field by definition, which
		is what distinguishes it from a value cap. What cannot be checked from the parsed caps
		is the report's bit offset — Windows does not expose it here — so the payload's position
		still rests on the pin array being the report's only content, which is what 3,840 bits
		in a 481 byte report leaves room for and nothing else.

		Neither is the VID and PID checked, deliberately: no Monarch identifier has been
		confirmed across firmware revisions, and hard coding a guessed one would refuse real
		hardware. The path is logged instead when a candidate is rejected, so a device that
		should have matched can be identified from a log rather than from a second session.

		:param cap: one `HIDP_BUTTON_CAPS`.
		:param outputLength: the device's output report byte length, including the report ID.
		:return: whether this is the pin array.
		"""
		if cap.IsAlias:
			# An alias entry re-describes fields another cap already covers.
			return False
		if cap.IsRange:
			r = cap.u1.Range
			bits = r.UsageMax - r.UsageMin + 1
			usage = r.UsageMin
		else:
			bits = cap.ReportCount
			usage = cap.u1.NotRange.Usage
		if bits != monarch.PIN_COUNT:
			return False
		# From here on the array is pin sized, so anything that fails is worth saying out loud:
		# it is a display that nearly matched, and the reason is what a bug report needs.
		if usage != monarch.PIN_USAGE:
			log.debugWarning(
				f"BrlMultiline: pin sized array under usage 0x{usage:04X}, "
				f"not the expected 0x{monarch.PIN_USAGE:04X}; ignoring it",
			)
			return False
		if cap.ReportID != monarch.PIN_REPORT_ID:
			log.debugWarning(
				f"BrlMultiline: pin sized array on report 0x{cap.ReportID:02X}, "
				f"not the expected 0x{monarch.PIN_REPORT_ID:02X}; ignoring it",
			)
			return False
		if cap.UsagePage != bdDetect.HID_USAGE_PAGE_BRAILLE:
			log.debugWarning(
				f"BrlMultiline: pin sized array on usage page 0x{cap.UsagePage:04X}, "
				"not the braille page; ignoring it",
			)
			return False
		if outputLength < monarch.PIN_REPORT_BYTES + 1:
			log.debugWarning(
				f"BrlMultiline: output reports are {outputLength} bytes, too small for the "
				f"{monarch.PIN_REPORT_BYTES} byte pin payload; ignoring it",
			)
			return False
		# Both declarations put the same bits in the same order, so either is usable. Which one
		# the firmware chose is worth knowing, because reading only the usage span is what hid
		# this report in the first place.
		log.debug(
			f"BrlMultiline: pin array found, declared as "
			f"{'a usage range' if cap.IsRange else f'usage 0x{usage:04X} repeated {bits} times'}, "
			f"link collection {cap.LinkCollection} under usage 0x{cap.LinkUsage:04X} "
			f"on page 0x{cap.LinkUsagePage:04X}, bit field 0x{cap.BitField:04X}",
		)
		return True

	# --- Geometry ----------------------------------------------------------------------

	def _applyPitch(self) -> None:
		"""Tell NVDA the shape this pitch gives, so everything above sizes itself correctly.

		Rows and columns only. `numCells` is a computed property — `numRows * numCols` — and
		its setter *raises* on a multi line display rather than being merely redundant, which
		is how an earlier version of this failed to load at all.
		"""
		self.numRows = self._pitch.numRows
		self.numCols = self._pitch.numCols

	supportedSettings = [  # noqa: RUF012
		DriverSetting(
			"pitch",
			# Translators: label for the setting choosing how many braille rows the Monarch shows.
			_("&Braille rows"),
			defaultVal=monarch.PITCH_8_ROW.name,
			useConfig=True,
		),
	]
	"""Deliberately not wrapped in a try for the import above.

	It was, and that was a silent failure waiting to happen: if the import had failed for any
	reason the class would simply have inherited an empty `supportedSettings` from the base
	and the control would have vanished from NVDA's braille settings with nothing but a
	swallowed ImportError to show for it. A driver that cannot build its settings should fail
	loudly at load rather than quietly lose them.

	The id stays `pitch` because it is the config key and the name behind `availablePitchs`;
	only the label speaks of rows, which is what a reader chooses between.
	"""

	def _get_availablePitchs(self) -> dict:
		"""NVDA's naming: the choices for the `pitch` setting.

		:return: setting value to displayable choice.
		"""
		return {
			monarch.PITCH_8_ROW.name: StringParameterInfo(
				monarch.PITCH_8_ROW.name,
				# Translators: a Monarch line pitch choice: 8 braille lines with a blank row between.
				_("8 rows"),
			),
			monarch.PITCH_10_ROW.name: StringParameterInfo(
				monarch.PITCH_10_ROW.name,
				# Translators: a Monarch line pitch choice: 10 braille lines, no blank row between.
				_("10 rows"),
			),
		}

	def _get_pitch(self) -> str:
		""":return: the current pitch setting."""
		return self._pitch.name

	def _set_pitch(self, value: str) -> None:
		"""Change the line pitch, and tell NVDA the display has changed shape.

		:param value: a key of `monarch.PITCHES`.
		"""
		pitch = monarch.PITCHES.get(value)
		if pitch is None:
			raise ValueError(f"Unsupported pitch {value}")
		with self._stateLock:
			if pitch is self._pitch:
				return
			self._pitch = pitch
			# The cells on the panel were laid out for the old pitch and there are now a
			# different number of them, so glyph indexes no longer name what they named and
			# the cells themselves are the wrong length. Both are dropped rather than
			# re-interpreted, and the update `_announceGeometry` queues brings a whole frame
			# at the new shape.
			self._lastCells = []
			self._glyphs = {}
			self._applyPitch()
		self._announceGeometry()

	def _announceGeometry(self) -> None:
		"""Let NVDA notice that this display is now a different size.

		One read of `handler.displayDimensions` does it: the handler recomputes it from the
		driver every time and raises `displaySizeChanged` itself when the value has changed.
		The same trick `brlMultilineVirtual._announceGeometry` uses, for the same reason.

		This used to call `handler.handleDisplaySizeChanged()`, which does not exist. The
		exception was swallowed, so changing the pitch quietly left NVDA believing the display
		was still the old shape — 8 rows of 32, 256 cells — while the driver drew 10 rows of
		32 and routed against 320. That is very likely why routing at 10 rows did nothing:
		half the indexes were past the end of a buffer NVDA had never resized.

		The update is queued rather than called, so it lands after whatever the notification
		causes to be rebuilt. Nothing is announced unless this is the display NVDA has, which
		keeps construction from announcing against the driver being replaced.
		"""
		handler = braille.handler
		if handler is None or handler.display is not self:
			return
		try:
			handler.displayDimensions  # noqa: B018 - read for its side effect.
		except Exception:
			log.error("BrlMultiline: could not tell NVDA the display had changed size", exc_info=True)
			return
		self._onMainThread(handler.update)

	@staticmethod
	def _onMainThread(work) -> None:
		"""Run something on the main thread, wherever this is called from.

		:param work: what to run, taking no arguments.
		"""
		try:
			import wx

			wx.CallAfter(work)
		except Exception:
			# Without wx there is no main thread to hand this to, which is the test harness
			# and nothing else.
			log.debugWarning("BrlMultiline: no main thread to defer to; working here", exc_info=True)
			work()

	@staticmethod
	def _onIoThread(work) -> None:
		"""Run something on NVDA's background I/O thread, where writes to the device belong.

		Nothing that writes may run on the main thread. A write can take up to
		`WRITE_TIMEOUT`, and one in progress holds `_writeLock` that long, so a main thread
		write is a frozen NVDA whenever the device is slow. That is how a hung Monarch stopped
		NVDA starting: the main thread sat in a repaint waiting for the lock. The handler
		already writes from this thread.

		The thread holds bound methods weakly, which is what should happen: a driver that has
		gone has nothing left to write.

		:param work: what to run, taking the APC's parameter.
		"""
		thread = getattr(hwIo, "bgThread", None)
		if thread is None:
			# Before NVDA has started its I/O thread, or after it has stopped it.
			work()
			return
		thread.queueAsApc(work)

	@property
	def graphicsSize(self) -> tuple[int, int]:
		"""The published capability the add-on looks for.

		A display without this attribute cannot draw and the add-on falls back to text, which
		is how graphics support stays device agnostic.

		:return: the pin grid, width by height.
		"""
		return monarch.PIN_WIDTH, monarch.PIN_HEIGHT

	def newGraphicsBuffer(self, width: Optional[int] = None, height: Optional[int] = None) -> PinBuffer:
		"""Make a blank buffer to draw into.

		Sized to the whole panel by default, because that was the only thing the driver itself
		needed. A caller drawing into part of the panel wants a buffer the size of its own
		rectangle instead: an overlay is placed by its top left corner, so a right sized buffer
		is positioned by where the rectangle starts and nothing has to be offset twice.

		:param width: dots across, or None for the full pin width.
		:param height: dots down, or None for the full pin height.
		:return: a blank buffer.
		"""
		return PinBuffer(
			monarch.PIN_WIDTH if width is None else max(0, width),
			monarch.PIN_HEIGHT if height is None else max(0, height),
		)

	# --- Cell glyphs -----------------------------------------------------------------------

	@property
	def glyphSize(self) -> tuple[int, int]:
		"""The pins a glyph may fill, which is one cell slot including its gap column.

		3 by 4 on the Monarch, against a braille cell's 2 by 4. A display whose cells have no
		gap to reclaim would report the same size as its cell and a glyph would gain nothing;
		the add-on can compare this with `cellSize` to find out whether glyphs are worth using.

		**Per cell.** A glyph standing in for three cells of text has three of these to draw
		in, so nine by four, and the pattern handed to `newGlyph` is that wide.

		:return: width and height in pins.
		"""
		return self._pitch.slotSize

	@property
	def cellSize(self) -> tuple[int, int]:
		""":return: the pins a braille cell uses, for comparison with `glyphSize`."""
		return self._pitch.cellCols, self._pitch.dotRows

	def newGlyph(self, rows: list[str], fallback) -> "CellGlyph":
		"""Build a glyph this driver can draw, without the caller importing anything.

		The add-on reaches the driver as a live object and does not import the package — the
		same rule `devices.py` follows — so the factory lives here rather than the class being
		imported there.

		:param rows: the shape, one string per row, as `PinBuffer.fromRows` takes it. Anything
			past the run's slots is clipped, so the shape for a three cell glyph is nine
			columns wide: `glyphSize` gives the width of one.
		:param fallback: the braille cells the add-on has put in the buffer at this position —
			a list of cell values, or one value for a single cell glyph. See `setCellGlyphs`.
		:return: the glyph.
		"""
		return CellGlyph(PinBuffer.fromRows(rows), fallback)

	def setCellGlyphs(self, glyphs: dict[int, "CellGlyph"]) -> None:
		"""Replace the set of cells that are drawn as glyphs rather than as braille.

		Keyed by index into the flat cell array the handler writes, so a glyph sits in the
		text and scrolls with it: it occupies exactly one cell slot and the next cell starts
		where it always would.

		Each glyph carries the braille cell it stands in for, and is drawn **only if the cell
		at that index still reads it**, on this frame or on frames redrawn from it. A frame
		that does not match retires the glyph outright rather than leaving it registered, so a
		later unrelated frame that happens to carry the same byte at the same index cannot
		bring an old symbol back. That is the difference between "this glyph belongs to this
		content" and "this byte looks familiar", and only the first is safe.

		The match does three things at once:

		1. A frame the add-on did not compose — a braille message, say — cannot get a glyph
		   painted over unrelated content. The glyph is skipped and the cell shows instead.
		2. The fallback is what a display without glyphs shows anyway, so the add-on writes
		   one buffer and every display renders the best it can. A solid dots 1 to 6 becomes a
		   true 3 by 3 square here and stays a recognisable block elsewhere.
		3. A caret wins. NVDA ors the cursor shape into the cell before the driver sees it, so
		   the cell no longer matches and the braille cell with its cursor is drawn. Deliberate:
		   the reader needs the caret more than the symbol.

		The whole set is replaced, so the add-on registers what it wants on each recompose and
		never has to clear individually. It **must** re-register on each recompose, because a
		glyph is retired by the first frame that does not carry its cell: a braille message
		flashing over the content ends the registration, and the redraw afterwards is where it
		comes back. That is the contract the add-on side is written to, and it is what stops a
		glyph outliving the content it belonged to.

		**A glyph occupies a run of cells and the runs may not overlap.** Two symbols
		sharing a cell is a caller's mistake with no sensible rendering, so the later of
		them is dropped here rather than being drawn over the earlier one.

		:param glyphs: the index of each glyph's first cell, to the glyph. Empty clears
			them.
		"""
		with self._stateLock:
			self._glyphs = _withoutOverlaps(glyphs)
		self._scheduleRepaint()

	def clearCellGlyphs(self) -> None:
		"""Draw every cell as braille again."""
		self.setCellGlyphs({})

	def _expireGlyphs(self, cells: list[int]) -> None:
		"""Drop glyphs the incoming frame does not claim. Call with `_stateLock` held.

		A glyph is registered against the cells the add-on put in the buffer for it, and lives
		exactly as long as a frame keeps carrying them at that index. The first frame that does
		not — different content, or a caret or-ed into any cell of the run — retires it.

		Retiring rather than merely skipping is the point. Skipping leaves the registration
		standing, and some later frame with an unrelated 0x3F at the same index would then be
		painted with a symbol belonging to content that scrolled away minutes ago. This bounds
		a glyph's life to the run of frames that actually contain it.

		:param cells: the frame just handed to `display`.
		"""
		if not self._glyphs:
			return
		self._glyphs = {
			index: glyph
			for index, glyph in self._glyphs.items()
			if glyph.matches(cells, index)
		}

	# --- Graphics overlays ---------------------------------------------------------------

	def setGraphicsOverlay(self, key: str, x: int, y: int, buffer: PinBuffer) -> None:
		"""Put a drawing on the panel, composited over the text on the next write.

		Overlays are held rather than drawn immediately, because the panel is written whole:
		drawing one now would mean writing the panel now, and the next ordinary braille update
		would then be a second full mechanical refresh a moment later.

		:param key: a name, so the same caller can replace its own overlay.
		:param x: pin column of the drawing's left edge.
		:param y: pin row of its top edge.
		:param buffer: what to draw.
		"""
		with self._stateLock:
			self._overlays[key] = (x, y, buffer)
		self._scheduleRepaint()

	def clearGraphicsOverlay(self, key: Optional[str] = None) -> None:
		"""Remove one overlay, or all of them.

		:param key: which overlay, or None for every one.
		"""
		with self._stateLock:
			if key is None:
				self._overlays.clear()
			else:
				self._overlays.pop(key, None)
		self._scheduleRepaint()

	# --- Touch ---------------------------------------------------------------------------

	@property
	def lastTouch(self) -> Optional[tuple[int, int]]:
		""":return: the pin last touched, or None if the panel is not being touched."""
		return self._lastTouchPin

	@property
	def lastTouchCell(self) -> Optional[tuple[int, int]]:
		"""The braille line and column last touched, at the pitch being rendered.

		At the native pitch this prefers the device's own routing cell, which is its
		calibrated answer and better than anything derived: a fingertip covers several pins.
		At any other pitch the device's cell assumes a layout we are not using, so the pin is
		converted instead.

		:return: braille row and column, or None if nothing is being touched.
		"""
		if self._lastTouchPin is None:
			return None
		if self._pitch is monarch.PITCH_8_ROW and self._lastTouchCell is not None:
			return divmod(self._lastTouchCell, monarch.NATIVE_ROUTING_COLS)
		return monarch.cellAtPin(self._lastTouchPin[0], self._lastTouchPin[1], self._pitch)

	def _hidOnReceive(self, data: bytes):
		"""Read the touch reports NVDA discards, then let the base class do its work.

		`hidBrailleStandard` inspects input values only to find `NUMBER_OF_BRAILLE_CELLS`, so
		usage 0x401 — the touched pin — is dropped on the floor and a touch on a graphic
		reaches the log as an event with no number attached.

		Both touch reports are kept. See `lastTouchCell` for why the cell one still earns its
		place now that the pin one exists.

		A report arriving while there is no device is dropped. That happens for a moment during
		a reconnection: `hwIo.hid.Hid` starts reading the instant it is constructed, and the
		callback it was given is this one, so a key pressed as the link comes back can arrive
		before the candidate handle has been installed. Both this method and the inherited one
		decode against `self._dev`, and against None the inherited one raises where nothing
		catches it.

		:param data: the raw input report.
		"""
		if getattr(self, "_dev", None) is None:
			return
		try:
			self._readTouch(data)
		except Exception:
			log.debugWarning("BrlMultiline: could not decode a Monarch touch report", exc_info=True)
		super()._hidOnReceive(data)

	def _readTouch(self, data: bytes) -> None:
		"""Decode a touch report, if this is one.

		The pin is also snapshotted the moment a routing key goes down, and that snapshot is
		what routing uses. It has to be, because of the order the panel speaks in: the pin
		arrives first, then the routing cell, then the pin again as zero on release, and only
		then does NVDA raise the gesture. By gesture time the live pin has been cleared, so
		reading it there would find nothing.

		:param data: the raw input report.
		"""
		report = hwIo.hid.HidInputReport(self._dev, data)
		for item in report.getDataItems():
			usage = self._inputUsageForDataIndex(item.DataIndex)
			if usage is None:
				continue
			if usage == monarch.TOUCH_PIN_USAGE:
				self._lastTouchPin = monarch.touchedPin(int(item.u1.RawValue))
				if self._lastTouchPin is None:
					self._lastTouchCell = None
			elif monarch.ROUTING_USAGE_MIN <= usage <= monarch.ROUTING_USAGE_MAX and item.u1.On:
				self._lastTouchCell = usage - monarch.ROUTING_USAGE_MIN
				self._pinAtRouting = self._lastTouchPin

	KEEP_ROUTING = "keep"
	REPLACE_ROUTING = "replace"
	CANCEL_ROUTING = "cancel"
	"""What to do with the cell indexes the device reported. See `_routingDecision`."""

	def _routingDecision(self, count: int) -> tuple[str, Optional[int]]:
		"""Decide what a routing press means at the pitch being rendered.

		`KEEP` at the native pitch, deliberately. There the device's own cell number is its
		calibrated answer — a fingertip covers several pins and its algorithm knows more about
		which one was meant than we do — and it already indexes the grid we are rendering, so
		there is nothing to correct.

		At any other pitch there is everything to correct. The device reports on its native
		8 by 32 grid whatever we draw, so at 10 rows it can name only 256 of the 320 cells on
		the panel and the ones it names sit on the wrong lines. That is why routing did not
		work at 10 rows: not a missing feature so much as an unfinished one.

		The correction needs the pin, and the pin is available for exactly one press: usage
		0x401 reports a single touched point, so a range selection arrives as several routing
		cells with one pin between them and there is no way to re-base its endpoints. Where the
		answer cannot be derived — no pin, or more cells than pins — the press is **cancelled**
		rather than passed through. Passing it through was activating a cell chosen on a layout
		we are not drawing, which reads to a user as the display doing something at random; a
		press that does nothing is a press they will simply make again.

		:param count: how many routing cells the device reported.
		:return: what to do, and the index to use when that is `REPLACE_ROUTING`.
		"""
		if self._pitch is monarch.PITCH_8_ROW:
			return self.KEEP_ROUTING, None
		if count != 1:
			log.debugWarning(
				f"BrlMultiline: {count} routing cells at pitch {self._pitch.name}. The device "
				"names them on its native 8 by 32 grid and reports only one touched pin, so the "
				"range cannot be re-based; cancelling rather than selecting the wrong span",
			)
			return self.CANCEL_ROUTING, None
		if self._pinAtRouting is None:
			log.debugWarning(
				f"BrlMultiline: routing at pitch {self._pitch.name} with no touched pin; "
				"cancelling rather than activating the cell the device named, which addresses "
				"a different layout",
			)
			return self.CANCEL_ROUTING, None
		index = monarch.routingIndexForPin(self._pinAtRouting[0], self._pinAtRouting[1], self._pitch)
		if index is None:
			log.debugWarning(
				f"BrlMultiline: routing pin {self._pinAtRouting} is off the panel; cancelling",
			)
			return self.CANCEL_ROUTING, None
		return self.REPLACE_ROUTING, index

	def _recordRouting(self, deviceCells, dispatched, action: str, corrected: Optional[int]) -> None:
		"""Keep what the last routing press decided, so it can be read back after the fact.

		Routing crosses three things that can each be wrong on their own — the device's cell,
		the pin, and whatever the add-on above does with the index — and a press that does
		nothing looks identical whichever it was. This records all of it in one place, and
		keeps the raw and the substituted separately: an earlier version recorded
		`cellIndexes` after it had already been replaced, so the field labelled "from the
		device" was the driver's own answer read back to itself, which is the one reading that
		can never disagree with anything.

		Kept on the driver as well as logged, because reproducing a routing press with debug
		logging on is a different session from the one where it misbehaved. Read it with::

			braille.handler.display.lastRouting

		:param deviceCells: the cell indexes the device itself reported, before any correction.
		:param dispatched: the indexes the gesture actually carried, or None if it was cancelled.
		:param action: `KEEP_ROUTING`, `REPLACE_ROUTING` or `CANCEL_ROUTING`.
		:param corrected: the index derived from the pin, when there was one.
		"""
		self.lastRoutingPin = self._pinAtRouting
		self.lastRouting = {
			"pitch": self._pitch.name,
			"rows": self._pitch.numRows,
			"cols": self._pitch.numCols,
			"cellsFromDevice": list(deviceCells) if deviceCells else None,
			"pinAtRouting": self._pinAtRouting,
			"action": action,
			"corrected": corrected,
			"dispatched": list(dispatched) if dispatched else None,
			"numCells": self.numCells,
		}
		log.debug(f"BrlMultiline: routing {self.lastRouting}")

	lastRouting: Optional[dict] = None
	"""What the last routing press decided. See `_recordRouting`."""

	lastRoutingPin: Optional[tuple[int, int]] = None
	"""The pin under the finger at the last routing press, or None if there was none.

	`lastTouch` cannot answer this and never will. The panel reports the touched pin as zero
	the moment the finger lifts, and NVDA runs a gesture's script from a queue rather than
	while the gesture is being dispatched — so by the time anything above the driver is asked
	what a press meant, the live touch is already gone. Anything that wants to know where a
	press landed, at pin resolution rather than cell resolution, has to read it from here.

	Set for every dispatched press at both pitches, including the native one where the index
	itself is the device's own. Deliberately *not* cleared when the press ends, unlike
	`_pinAtRouting`: that one guards the correction and must not outlive its press, while this
	one is the record of the last press and is replaced by the next.
	"""

	def _handleKeyRelease(self):
		"""Raise the gesture, using ours so that routing can be corrected for the pitch.

		Mirrors the inherited method, which names its own `InputGesture` class directly and so
		cannot be steered by overriding an attribute.

		A gesture that cancelled itself is not dispatched at all, rather than dispatched with
		nothing to route to. See `_routingDecision` for when that happens and why doing nothing
		is the right answer there.

		The routing snapshot is dropped afterwards whatever happened, so a later press that
		somehow arrives without a pin cannot be given this one's.
		"""
		if self._ignoreKeyReleases or not self._keysDown:
			return
		try:
			gesture = InputGesture(self, self._keysDown)
			if not gesture.cancelled:
				inputCore.manager.executeGesture(gesture)
		except inputCore.NoInputGestureAction:
			pass
		finally:
			self._pinAtRouting = None
		# Any further releases are just the rest of the keys in the combination being released,
		# so they should be ignored.
		self._ignoreKeyReleases = True

	def _inputUsageForDataIndex(self, dataIndex: int) -> Optional[int]:
		"""Map an input data index to its usage, building the map once.

		:param dataIndex: the index from a received data item.
		:return: the usage, or None if it is not one we know.
		"""
		cached = getattr(self, "_inputUsages", None)
		if cached is None:
			cached = self._buildInputUsageMap()
			self._inputUsages = cached
		return cached.get(dataIndex)

	def _buildInputUsageMap(self) -> dict[int, int]:
		"""Expand every input cap, ranges included, into data index to usage.

		:return: the map.
		"""
		usages: dict[int, int] = {}
		for cap in self._dev.inputValueCaps:
			if cap.IsRange:
				r = cap.u1.Range
				for index in range(r.DataIndexMin, r.DataIndexMax + 1):
					usages[index] = r.UsageMin + (index - r.DataIndexMin)
			else:
				usages[cap.u1.NotRange.DataIndex] = cap.u1.NotRange.Usage
		for cap in self._dev.inputButtonCaps:
			if cap.IsRange:
				r = cap.u1.Range
				for index in range(r.DataIndexMin, r.DataIndexMax + 1):
					usages[index] = r.UsageMin + (index - r.DataIndexMin)
			else:
				usages[cap.u1.NotRange.DataIndex] = cap.u1.NotRange.Usage
		return usages

	# --- Output ---------------------------------------------------------------------------

	def display(self, cells: list[int]):
		"""Draw the cells and any overlays into the pin buffer, and write the whole panel.

		Never writes the cell reports. That is the point: they reach 2,048 of 3,840 pins and
		leave a spacer column between every pair, while this reaches all of them.

		Because the driver owns the panel, an ordinary braille update cannot wipe a drawing
		and nothing above has to be suspended to keep one up.

		:param cells: one dot pattern per cell, row major, as the handler supplies them.
		"""
		with self._stateLock:
			frame = list(cells)
			self._lastCells = frame
			self._expireGlyphs(frame)
			# A frame is the natural publishing point for anything an overlay or glyph setter
			# had pending: it is about to be drawn anyway, so the deferred write would be a
			# second full mechanical refresh of a panel that already shows the answer.
			self._repaintPending = False
			snapshot = self._snapshot()
		self._writePins(self._compose(snapshot))

	def _snapshot(self) -> tuple:
		"""Freeze everything the panel is composed from. Call with `_stateLock` held.

		Composing reads four things that four different threads may be changing — the handler
		writes cells from its I/O thread while the add-on sets overlays and glyphs from the
		main one — and a frame built from a mixture of before and after is a frame that was
		never asked for. Worse, iterating the overlays while another thread inserts one raises
		`RuntimeError: dictionary changed size during iteration` and the panel simply does not
		update.

		So the state is copied under the lock and the drawing, which is the slow part, happens
		outside it against something nothing can change underneath it.

		:return: pitch, cells, glyphs and overlays, as of now.
		"""
		return (
			self._pitch,
			list(self._lastCells),
			dict(self._glyphs),
			list(self._overlays.values()),
		)

	def _compose(self, snapshot: tuple) -> PinBuffer:
		"""Draw a snapshot into a panel buffer.

		Reads nothing from the driver, deliberately: everything it needs was frozen by
		`_snapshot`, which is what makes the frame internally consistent.

		:param snapshot: what `_snapshot` returned.
		:return: the whole panel, ready to write.
		"""
		pitch, cells, glyphs, overlays = snapshot
		buffer = PinBuffer(monarch.PIN_WIDTH, monarch.PIN_HEIGHT)
		self._drawCells(buffer, cells, pitch, glyphs)
		for x, y, overlay in overlays:
			buffer.blit(overlay, x, y)
		return buffer

	def _repaint(self, param: int = 0) -> None:
		"""Compose the panel from the current state and write it, now.

		:param param: unused; there because this is queued as an APC.
		"""
		with self._stateLock:
			self._repaintPending = False
			snapshot = self._snapshot()
		self._writePins(self._compose(snapshot))

	def _scheduleRepaint(self) -> None:
		"""Ask for a repaint at the next opportunity, collapsing several requests into one.

		Overlays and glyphs are set a few at a time — a chart, then its axes, then a focus
		marker — and repainting on each would be several complete mechanical refreshes of a
		panel nobody has read yet. The Monarch is a page turn device: each write clatters, and
		the reader has to lift their hand for the pins to settle accurately. One write per
		batch is not an optimisation here, it is the difference between a page and a flicker.

		So the request is marked and handed to the main thread, and whichever comes first —
		that deferred flush or an ordinary `display` — publishes everything pending.

		Two hops, and both are needed. The main thread is what batches: its turn comes after
		the caller has finished setting overlays. The write then goes to the I/O thread,
		because a main thread waiting on the device is a frozen NVDA. See `_onIoThread`.
		"""
		with self._stateLock:
			if self._repaintPending:
				return
			self._repaintPending = True
		self._onMainThread(self._handOffRepaint)

	def _handOffRepaint(self) -> None:
		"""Pass a batched repaint from the main thread to the I/O thread to be written."""
		self._onIoThread(self._flushRepaint)

	def _flushRepaint(self, param: int = 0) -> None:
		"""Draw a repaint that `_scheduleRepaint` asked for, unless a frame got there first.

		:param param: unused; there because this is queued as an APC.
		"""
		with self._stateLock:
			if not self._repaintPending:
				return
			self._repaintPending = False
			snapshot = self._snapshot()
		self._writePins(self._compose(snapshot))

	def _drawCells(self, buffer: PinBuffer, cells: list[int], pitch, glyphs: dict) -> None:
		"""Draw braille cells into the buffer at the current pitch.

		Uses NVDA's own `drawBrailleCells` where it can, which is the same function the DotPad
		driver uses for the same reason: when the only surface is a pin grid, characters are
		drawn into it. Falls back to a local copy if that module is unavailable, so the driver
		still works on an NVDA without the tactile package.

		**Cells are passed through untouched.** All eight dots are drawn at every pitch, so a
		caret shows and an eight dot table reads. At 10 rows the fourth row of a cell is the
		one that would otherwise separate the lines, which is a density the reader chooses by
		choosing the pitch — it is not the driver's business to decide a dot should not appear.

		:param buffer: the panel buffer.
		:param cells: one dot pattern per cell, row major.
		:param pitch: the layout being rendered, from the snapshot.
		:param glyphs: cell index to glyph, from the snapshot.
		"""
		for row in range(pitch.numRows):
			start = row * pitch.numCols
			rowCells = cells[start : start + pitch.numCols]
			if not rowCells:
				break
			self._drawRow(buffer, list(rowCells), row, pitch)
			self._drawGlyphs(buffer, list(rowCells), row, start, pitch, glyphs)

	def _drawGlyphs(
		self,
		buffer: PinBuffer,
		rowCells: list[int],
		row: int,
		start: int,
		pitch,
		glyphs: dict,
	) -> None:
		"""Replace any cell in this row that has a glyph registered and still matches it.

		Drawn after the row rather than instead of it, and the slot is cleared first, so a
		glyph replaces its cell rather than merging with it. Merging would be worse than
		useless: a symbol or-ed with a letter is neither.

		The match against the registered cells is what makes a stale registration harmless.
		See `setCellGlyphs`.

		:param buffer: the panel buffer.
		:param rowCells: the line's cells.
		:param row: which braille line.
		:param start: the flat index of the first cell in this row.
		:param pitch: the layout being rendered.
		:param glyphs: cell index to glyph, from the snapshot.
		"""
		if not glyphs:
			return
		slotWidth, slotHeight = pitch.slotSize
		for col in range(len(rowCells)):
			glyph = glyphs.get(start + col)
			if glyph is None or not glyph.matches(rowCells, col):
				continue
			if col + glyph.cells > len(rowCells):
				# The run is split across the end of this line. The cells are still there
				# and still say what they say, so the reader gets the text wrapped — which
				# is what they would have got without glyphs at all. Drawing it anyway
				# would paint one shape across two lines, which is the only bad answer
				# available here.
				continue
			x, y = monarch.cellOrigin(row, col, pitch)
			width = slotWidth * glyph.cells
			buffer.clearRect(x, y, width, slotHeight)
			for dotY in range(min(slotHeight, glyph.pattern.height)):
				for dotX in range(min(width, glyph.pattern.width)):
					if glyph.pattern.getDot(dotX, dotY):
						buffer.setDot(x + dotX, y + dotY)

	def _drawRow(self, buffer: PinBuffer, rowCells: list[int], row: int, pitch) -> None:
		"""Draw one line of cells.

		:param buffer: the panel buffer.
		:param rowCells: the line's cells.
		:param row: which braille line.
		:param pitch: the layout being rendered.
		"""
		x, y = monarch.cellOrigin(row, 0, pitch)
		try:
			from tactile.braille import drawBrailleCells

			drawBrailleCells(buffer, x, y, rowCells, hCellPadding=pitch.gapCols)
			return
		except ImportError:
			pass
		for index, cell in enumerate(rowCells):
			originX = x + index * pitch.cellStride
			for bit in range(8):
				if cell & (1 << bit):
					dotX, dotY = monarch._BRAILLE_DOT_COORDS[bit]
					buffer.setDot(originX + dotX, y + dotY)

	def _writePins(self, buffer: PinBuffer) -> None:
		"""Send a panel buffer as output report 0x21.

		Serialised, because a repaint can come from the handler's I/O thread and from an
		overlay set on the main thread, and two half written reports interleaved would be a
		garbled panel. The same lock is what a reconnection takes to swap the handle, so a
		write cannot land on a device that is being closed underneath it.

		A failure is reported *after* the lock is released. `_onDeviceLost` takes the lifecycle
		lock, and reporting from in here would be `_writeLock` then `_lifecycleLock` while a
		reconnection holds them the other way round.

		A device that does not take the report within `WRITE_TIMEOUT` is a failure like any
		other. It used to be waited for without limit, holding `_writeLock` all the while.

		A write that gets through puts the reconnect wait back to its shortest. That is the
		only proof a device is really back: see `_poll`.

		A write that fails suspends writing before it lets the lock go, so a write already
		waiting for the lock finds the device lost and does not try it. See `_writesSuspended`.
		The first write after a reconnection is therefore the only one that probes the new
		handle: if it fails, writing is suspended again at once.

		:param buffer: the whole panel.
		"""
		if self._pinCap is None or self._terminated or self._writesSuspended:
			return
		payload = monarch.packPins(buffer)
		lost = False
		with self._writeLock:
			device = self._dev
			if device is None or self._terminated or self._pinCap is None or self._writesSuspended:
				return
			try:
				report = hwIo.hid.HidOutputReport(device, reportID=monarch.PIN_REPORT_ID)
				data = bytearray(report.data)
				room = len(data) - 1
				data[1 : 1 + min(room, len(payload))] = payload[:room]
				self._sendReport(device, bytes(data))
			except hidWrite.WriteTimedOut:
				stranded = hidWrite.outstanding()
				log.warning(
					f"BrlMultiline: the Monarch did not take a pin report within {WRITE_TIMEOUT} seconds; "
					"treating it as disconnected"
					+ (f". Writes Windows has not let go of: {stranded}" if stranded else ""),
				)
				lost = True
				self._writesSuspended = True
			except Exception:
				log.debugWarning("BrlMultiline: Monarch pin write failed", exc_info=True)
				lost = True
				self._writesSuspended = True
		if lost:
			self._onDeviceLost()
			return
		with self._lifecycleLock:
			self._retryDelay = POLL_INTERVAL

	def _sendReport(self, device, data: bytes) -> None:
		"""Write one output report, giving up if the device does not take it in time.

		Not `device.write`, which waits for the write to finish however long that takes.
		The buffer comes from the device's own `_prepareWriteBuffer`, so it is the size Windows
		expects for this device's output reports, exactly as `write` would have sent it.

		:param device: the open `hwIo.hid.Hid`.
		:param data: the report, ID in the first byte.
		:raises hidWrite.WriteTimedOut: if the device did not take it within `WRITE_TIMEOUT`.
		:raises OSError: if the write failed.
		"""
		size, buffer = device._prepareWriteBuffer(data)
		hidWrite.writeWithin(device._writeFile, buffer, size, WRITE_TIMEOUT)

	# --- Staying connected -------------------------------------------------------------

	def _watchForDisconnect(self) -> None:
		"""Learn about a dropped link from the read side, rather than waiting for a write.

		A Bluetooth display that goes away fails the overlapped read at once with
		`WinError 1167`, and `hwIo.IoBase._ioDone` offers that to `_onReadError` before
		raising. Chaining onto it turns a silent death into a notification. A display showing
		something static is not written to at all, so waiting for a write to fail can mean
		waiting a long time.

		Chained rather than replaced, and the driver's own answer is respected: a driver that
		says it handled the error is restarting, not dying.
		"""
		device = getattr(self, "_dev", None)
		if device is None or not hasattr(device, "_onReadError"):
			return
		original = device._onReadError

		def onReadError(error: int) -> bool:
			handled = False
			try:
				if original is not None:
					handled = bool(original(error))
			except Exception:
				log.error("BrlMultiline: Monarch raised handling a read error", exc_info=True)
			if handled:
				return True
			log.warning(f"BrlMultiline: Monarch stopped responding (error {error}); will retry")
			# Report that this is handled once recovery is actually running. Returning False
			# here said "this driver is finished", and inside a virtual display that is taken
			# at its word: the member is dropped and the in place reconnection this driver
			# promises never gets the chance to happen. False stays the answer when nothing
			# is retrying, because a member kept alive with no recovery behind it is worse.
			return self._onDeviceLost()

		device._onReadError = onReadError
		self._watched = (device, original, onReadError)

	def _stopWatching(self) -> None:
		"""Give the device its own read error hook back, if ours is still the one there."""
		if self._watched is None:
			return
		device, original, ours = self._watched
		self._watched = None
		try:
			if getattr(device, "_onReadError", None) is ours:
				device._onReadError = original
		except Exception:
			log.debugWarning("BrlMultiline: could not unhook the Monarch read error", exc_info=True)

	def _onDeviceLost(self) -> bool:
		"""Begin trying to get the device back. Safe to call more than once.

		`_reopening` is cleared again if nothing could be scheduled. It used to be set before
		the attempt and left set afterwards, so a failure to create a timer left the driver
		claiming to be recovering with nothing recovering: every later loss answered "handled"
		at once and returned, and a virtual display holding this member kept a display that was
		never coming back.

		When nothing could be scheduled, writing is resumed as well. Nothing is going to install
		a new handle, so a suspension would never end, and the next write failing is what tries
		to schedule recovery again.

		Reconnection is refused once Windows is holding `ABANDONED_WRITE_LIMIT` writes it never
		let go of, and then writing is *not* resumed: every probe of that device can strand
		another.

		:return: whether recovery is now running, which is what the read error hook reports
			upward. Saying True when nothing is actually retrying would have a virtual display
			keep a member that is never coming back.
		"""
		with self._lifecycleLock:
			if self._terminated:
				return False
			if self._reopening:
				return True
			if hidWrite.outstanding() >= ABANDONED_WRITE_LIMIT:
				if not self._gaveUp:
					log.error(
						f"BrlMultiline: the Monarch has {ABANDONED_WRITE_LIMIT} writes Windows will not let go "
						"of, so it is no longer being reconnected automatically. Unplug it and plug it back "
						"in, then select the braille display again or restart NVDA.",
					)
				self._gaveUp = True
				return False
			self._reopening = True
			# Not reset to the shortest wait: a device that reopened and failed again has not
			# been proven back, and `_poll` has already lengthened the wait for it.
			delay = self._retryDelay
		if self._startPolling(delay):
			return True
		with self._lifecycleLock:
			self._reopening = False
		with self._writeLock:
			self._writesSuspended = False
		return False

	def _startPolling(self, delay: float) -> bool:
		"""Start the reconnect timer. Safe to call when one is already running.

		The delay is passed in rather than fixed, which is the whole of a bug worth recording.
		This used to schedule every tick `POLL_INTERVAL` apart and separately track a deadline
		that each tick pushed further out — so after the first failure the deadline receded by
		twice the time that passed, and no second attempt was ever made. The backoff now lives
		in the timer itself and there is no deadline to outrun.

		Each attempt carries a generation number, and `_stopPolling` moves the generation on.
		That is what makes a timer we could not cancel harmless, and there will be such timers:
		`core.callLater` off the main thread creates the timer later and hands back nothing to
		cancel. A stale tick compares generations and returns.

		:param delay: seconds until the next attempt.
		:return: whether an attempt is scheduled.
		"""
		with self._lifecycleLock:
			if self._terminated:
				return False
			if self._pollScheduled:
				return True
			self._pollGeneration += 1
			generation = self._pollGeneration
			self._pollScheduled = True
		try:
			timer = _callLater(int(delay * 1000), self._poll, generation)
		except Exception:
			# No timer means no reconnection, and the caller has to know: it is what the read
			# error hook answers upward with. This happens in a test harness with no wx, and
			# in NVDA before the wx app exists.
			log.debugWarning("BrlMultiline: could not start looking for the Monarch", exc_info=True)
			with self._lifecycleLock:
				if self._pollGeneration == generation:
					self._pollScheduled = False
			return False
		with self._lifecycleLock:
			if self._pollGeneration == generation:
				self._pollTimer = timer
				return True
		# Stopped while the timer was being created. The generation check in `_poll` already
		# makes the tick harmless; cancelling saves a pointless wakeup.
		_stopTimer(timer)
		return False

	def _stopPolling(self) -> None:
		"""Stop the reconnect timer. Safe to call when nothing is running.

		Moving the generation on is the part that always works. Cancelling the timer is best
		effort, because there may be no timer to cancel yet — see `_startPolling`.
		"""
		with self._lifecycleLock:
			timer, self._pollTimer = self._pollTimer, None
			self._pollScheduled = False
			self._pollGeneration += 1
		_stopTimer(timer)

	def _poll(self, generation: int) -> None:
		"""Try to reopen the device, and keep trying with a growing wait.

		:param generation: which attempt this tick belongs to. A tick from before the last
			`_stopPolling` is discarded: termination and a fresh loss both move the generation
			on, and a timer that could not be cancelled would otherwise reopen a device this
			driver has finished with.
		"""
		with self._lifecycleLock:
			if generation != self._pollGeneration or self._terminated or not self._reopening:
				return
			self._pollTimer = None
			self._pollScheduled = False
		reopened = False
		try:
			reopened = self._reopenDevice()
		except Exception:
			log.error("BrlMultiline: error reopening the Monarch", exc_info=True)
		if reopened:
			with self._lifecycleLock:
				self._reopening = False
				# Opening is not proof the device works. A hung Monarch opens at once and then
				# takes no writes, and resetting the wait here would reopen it every few seconds,
				# each time holding the I/O thread for a whole `WRITE_TIMEOUT`. So the wait
				# keeps growing until a write gets through, which is what resets it.
				self._retryDelay = min(self._retryDelay * 2, POLL_BACKOFF_LIMIT)
			log.info("BrlMultiline: the Monarch is back")
			self._onIoThread(self._repaint)
			return
		with self._lifecycleLock:
			if self._terminated or not self._reopening:
				return
			self._retryDelay = min(self._retryDelay * 2, POLL_BACKOFF_LIMIT)
			delay = self._retryDelay
		if not self._startPolling(delay):
			with self._lifecycleLock:
				self._reopening = False
			log.warning("BrlMultiline: nothing is watching for the Monarch to come back")

	def _reopenDevice(self) -> bool:
		"""Close the dead handle and open the device again, keeping this driver alive.

		The difference from `brlMultilineVirtual`, which replaces a whole failed member: here
		the driver stays and only its device is replaced. NVDA is never told the display went
		away, so there is no display switch, no fallback to no braille, and no restart.

		The candidate is opened into a local and installed only after `_terminated` has been
		checked again under the lock. It has to be, because this runs concurrently with
		`terminate`: `terminate` can see no device, decide there is nothing to close, and
		finish, all while a handle is being opened here. Installing it then would leave an
		exclusive handle on the hardware that nothing can reach and nothing will close, and
		every later attempt to open the display — by any driver — fails until NVDA restarts.
		That is the one failure here bad enough to be worth a lock.

		:return: whether the device is open again.
		"""
		self._stopWatching()
		with self._lifecycleLock, self._writeLock:
			old, self._dev = getattr(self, "_dev", None), None
			self._pinCap = None
			self._inputUsages = None
		if old is not None:
			try:
				old.close()
			except Exception:
				log.debugWarning("BrlMultiline: could not close the old Monarch handle", exc_info=True)
		for portType, portId, port, portInfo in self._getTryPorts(self._port):  # noqa: B007
			if self._terminated:
				return False
			if portType != bdDetect.ProtocolType.HID:
				continue
			try:
				device = hwIo.hid.Hid(port, onReceive=self._hidOnReceive)
			except OSError:
				continue
			if device.usagePage != bdDetect.HID_USAGE_PAGE_BRAILLE:
				device.close()
				continue
			pinCap = self._findPinCap(device)
			if pinCap is None:
				device.close()
				continue
			with self._lifecycleLock, self._writeLock:
				stale = self._terminated
				if not stale:
					self._dev = device
					self._pinCap = pinCap
					self._writesSuspended = False
					self._resetInputSession()
			if stale:
				log.debug("BrlMultiline: the Monarch was terminated while reopening; letting go again")
				device.close()
				return False
			self._watchForDisconnect()
			return True
		return False

	def _resetInputSession(self) -> None:
		"""Start the input side over on a device that has just been opened.

		Everything the input path remembers describes the handle that went away: which keys
		were down, whether releases were being ignored, where the last touch was, and — the
		one that matters most — the capability structures every report is decoded against.
		Data indexes are a property of a device's report descriptor, so keeping the old map
		would mean decoding the new device's reports with the old device's key numbering.

		Carrying the key state across is its own bug: a release arriving after the reconnect
		would complete a combination begun on a device that is gone, and fire whatever that
		combination is bound to.

		Call with `_lifecycleLock` held, and only once `_dev` is the new device — the caps come
		from it.
		"""
		self._keysDown = set()
		self._ignoreKeyReleases = False
		self._pinAtRouting = None
		self._lastTouchPin = None
		self._lastTouchCell = None
		self._inputUsages = None
		try:
			self._inputButtonCapsByDataIndex = self._collectInputButtonCapsByDataIndex()
		except Exception:
			# Without this map no key decodes, which is worth an error rather than a note. An
			# empty one is still better than the previous device's: no gestures beats wrong ones.
			log.error("BrlMultiline: could not read the reconnected Monarch's buttons", exc_info=True)
			self._inputButtonCapsByDataIndex = {}

	# --- Shutdown ------------------------------------------------------------------------

	def terminate(self):
		"""Stop polling and unhook before letting the base class close the device.

		`_terminated` is set first and every other path checks it, because shutdown is exactly
		when a stale timer or a dying read can start work that outlives the driver. Blanking
		the display on the way out goes through `display`, so without the flag a device that
		had already gone would fail its write, be reported lost, and start a reconnection poll
		during termination.

		A driver whose device is gone must also not take the inherited path: it closes `_dev`
		unconditionally and would raise on None. The base class's own cleanup still runs, with
		the blanking suppressed, because there is nothing left to blank.

		The flag is set under the lifecycle lock and the lock is then let go, rather than held
		across the rest. Holding it would mean taking `_lifecycleLock` and then, through the
		base class blanking the display, `_stateLock` and `_writeLock` — the reverse of the
		order every other path takes them in, which is a deadlock waiting for a write to
		coincide with a shutdown.
		"""
		with self._lifecycleLock:
			self._terminated = True
		try:
			handover.stopReleasingOnSwitch(self.name)
			self._stopPolling()
			with self._lifecycleLock:
				self._reopening = False
			self._stopWatching()
			with self._stateLock:
				self._overlays.clear()
				self._glyphs.clear()
				self._repaintPending = False
		finally:
			try:
				if getattr(self, "_dev", None) is None:
					self._suppressDisplayClear = True
					braille.display.driver.BrailleDisplayDriver.terminate(self)
				else:
					super().terminate()
					self._dev = None
			finally:
				# A reconnection that was already past its `_terminated` check when the flag
				# went up closes its own candidate and stops. One that had installed a device
				# a moment before is closed here: an exclusive handle nothing can reach holds
				# the hardware away from every driver until NVDA restarts.
				self._closeStrayDevice()

	gestureMap = HidBrailleDriver.gestureMap
	"""Inherited wholesale.

	The keys, routing and controls are the same hardware whichever report the pins arrive in,
	so a user's existing `hidBrailleStandard` gestures keep working. The identifiers in this
	map name that driver, and they keep matching because `InputGesture` offers them as aliases
	after its own — see the source discussion there.
	"""


class InputGesture(HidInputGesture):
	"""A HID braille gesture that says which driver it came from, and routes for the pitch.

	**Source.** The inherited gesture reports `hidBrailleStandard`, because that is the driver
	the class belongs to. Standing alone that is harmless — the identifiers still match, and
	the driver's inherited `gestureMap` is written in those terms. Inside `brlMultilineVirtual`
	it is not harmless at all: the virtual display finds the member a gesture came from by
	driver name, so a Monarch reporting `hidBrailleStandard` matches no member. Routing indexes
	are not rebased into the composite, panning cannot tell which band to move, and a pan can
	end up scrolling a segment on the other display entirely.

	So the source is this driver, and the `hidBrailleStandard` identifiers are kept as aliases
	after it. Bindings a user already has for the standard driver keep working, the inherited
	`gestureMap` keeps matching, and the virtual display gets an unambiguous answer. Order
	matters: the specific identifier is offered first, so a Monarch-only binding can be made
	without disturbing the shared one.

	**Routing.** Only a single press can be corrected for the pitch, because the panel reports
	one touched pin. At a non native pitch a press that cannot be corrected is *cancelled*
	rather than passed through — see `BrailleDisplayDriver._routingDecision` — and `cancelled`
	is how this says so, since a gesture cannot decline to exist once it is constructed.
	"""

	source = DRIVER_NAME

	cancelled = False
	"""Whether this gesture should be dropped rather than dispatched."""

	def _get_identifiers(self):
		"""Our own identifiers, then the standard HID ones they replace.

		:return: identifiers most specific first.
		"""
		ids = super()._get_identifiers()
		ours = f"br({self.source}):"
		theirs = f"br({HidBrailleDriver.name}):"
		aliases = [identifier.replace(ours, theirs, 1) for identifier in ids if identifier.startswith(ours)]
		return ids + aliases

	def __init__(self, driver, dataIndices):
		"""
		:param driver: the Monarch driver.
		:param dataIndices: the data indices of the keys that were down.
		"""
		super().__init__(driver, dataIndices)
		if not self.cellIndexes:
			return
		# Kept before anything replaces it. Recording `cellIndexes` after the correction meant
		# the field labelled "from the device" was the driver's own answer read back to itself.
		deviceCells = list(self.cellIndexes)
		action, corrected = driver.KEEP_ROUTING, None
		try:
			action, corrected = driver._routingDecision(len(deviceCells))
			if action == driver.REPLACE_ROUTING:
				self.cellIndexes = [corrected]
			elif action == driver.CANCEL_ROUTING:
				self.cancelled = True
		except Exception:
			# A bug in the correction must not cost the user every gesture this display sends.
			# Keeping the device's own index is the safe failure for a *press* — at the native
			# pitch it is right, and at any other one the deliberate answer is a cancel that
			# `_routingDecision` would have returned had it not raised.
			log.error("BrlMultiline: could not decide the routing index", exc_info=True)
			action = driver.KEEP_ROUTING
		finally:
			try:
				driver._recordRouting(
					deviceCells,
					None if self.cancelled else self.cellIndexes,
					action,
					corrected,
				)
			except Exception:
				log.debugWarning("BrlMultiline: could not record the routing decision", exc_info=True)


# Re-exported so callers can name usages without importing NVDA's HID driver themselves.
__all__ = ["BrailleDisplayDriver", "BraillePageUsageID", "CellGlyph", "InputGesture", "monarch"]
