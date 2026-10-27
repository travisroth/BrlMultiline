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
from typing import TYPE_CHECKING

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

from . import hidWrite, keyNames, monarch
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

Not a latency budget: the longest a stuck device may hold NVDA's I/O thread. See `hidWrite`.
"""

ABANDONED_WRITE_LIMIT = 3
"""How many writes Windows may be holding past their cancellation before reconnecting stops.

Each holds a buffer and an event handle, possibly forever, and every reconnection probes with
a write. Past the limit the reader must reselect the display or restart NVDA.
"""


def _callLater(milliseconds: int, work, *args):
	"""Schedule something, from whichever thread this is.

	`core.callLater`, which marshals to the GUI thread, since this driver is `isThreadSafe` and
	a read error arrives on the I/O thread. Off the main thread it returns None, so a handle is
	no proof of scheduling; `_stopPolling` uses a generation number instead.

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

	Fills the gap column too, giving 3 by 4 pins per cell instead of 2 by 4, without moving
	anything. A run of cells, exactly as wide as the text it replaces, such as "btn", so
	routing and layout are untouched; the fallback is that text's cells. Built through
	`BrailleDisplayDriver.newGlyph`, so the add-on never imports this package.
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

	Overlap is a caller's mistake, so the later run is dropped, in index order.

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

	It would compete with `hidBrailleStandard` for a device `hwIo.hid.Hid` opens exclusively.
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
		self._lastTouchPin: tuple[int, int] | None = None
		self._lastTouchCell: int | None = None
		self._pinAtRouting: tuple[int, int] | None = None
		self._repaintPending = False
		# Lock order: `_stateLock`, then `_lifecycleLock`, then `_writeLock`, never the reverse.
		# Only `_reopenDevice` holds two at once; a failed write reports the device lost after
		# releasing `_writeLock`, and `terminate` releases `_lifecycleLock` before the base
		# class blanks the display.
		self._stateLock = threading.RLock()
		"""What the panel is made of: cells, glyphs, overlays, pitch, the pending flag."""
		self._writeLock = threading.Lock()
		"""One writer at a time on the wire, and the device is not swapped mid-report."""
		self._lifecycleLock = threading.RLock()
		"""The device handle, the termination flag, and the reconnect timer."""
		self._pinCap = None
		self._watched: tuple | None = None
		self._pollTimer = None
		self._pollScheduled = False
		self._pollGeneration = 0
		self._retryDelay = POLL_INTERVAL
		self._reopening = False
		self._writesSuspended = False
		"""Set, under `_writeLock`, by the write that finds the device lost.

		No write is attempted until a new handle is installed, so a hung device does not hold
		the I/O thread for every queued frame. Cells, glyphs and overlays are kept for the
		repaint after reconnecting.
		"""
		self._gaveUp = False
		"""Whether reconnection was stopped at `ABANDONED_WRITE_LIMIT`. Writes stay suspended."""
		self._terminated = False
		self._port = port

		self._openWithRetry(port)
		# From here on a failure must release the device itself, since a leaked exclusive
		# handle locks out every other driver until NVDA restarts.
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

		Sets NVDA's `_suppressDisplayClear` first, so a half built driver is not asked to blank
		the display. `terminate` is safe here, since all it touches is set before opening.
		"""
		self._suppressDisplayClear = True
		try:
			self.terminate()
		except Exception:
			log.debugWarning("BrlMultiline: Monarch raised while releasing a failed start", exc_info=True)

	def _openWithRetry(self, port) -> None:
		"""Run the inherited constructor, retrying a device that is present but not openable.

		See `_tryOpen`. If every attempt fails, the outgoing display is released and the
		attempts made again, since NVDA's `_switchDisplay` builds the new driver before
		terminating the old one, which may still hold the same Monarch.

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

	def _tryOpen(self, port) -> BaseException | None:
		"""Run the inherited constructor a few times.

		A display just released can refuse to open a couple of times. See
		docs/design/virtual-display-plan.md, phase 0.

		:param port: as NVDA passes it.
		:return: the last error, or None if the device opened.
		"""
		lastError: BaseException | None = None
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

		`braille.handler.display` is still the outgoing driver during this constructor.
		`releaseDisplayNow` also disarms its next `terminate`, which NVDA calls shortly. Only a
		driver holding a device is touched.

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

		An error after opening can leave `_dev` on an instance about to be thrown away.
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

		Like `check` and `_getAutoPorts`, the base version keys on `cls.name`, which has no
		detection data, so it would return nothing. Answered from `_getAutoPorts` per transport,
		so a choice is offered only when a device is reachable that way.

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

		`getDisplayList` drops a driver whose `check` is False, and neither of the base
		version's branches applies here. Presence only: this must not open the device, which
		`hwIo.hid.Hid` opens exclusively, so any HID braille display answers True and a non
		Monarch is refused at construction instead.

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

		HID braille matching is special cased in `bdDetect` on usage page 0x41, so a subclass
		with a new name finds nothing through the inherited version. This keeps the matches
		`bdDetect` files under the standard HID driver, over both transports.

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

		`monarch.py` carries its own copy of `tactile.braille._brailleDotCoords`, so it is
		compared once here. A warning, not a refusal.
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

		One usage repeated 3,840 times, so the count is in `ReportCount`, not the usage span.
		Takes the device, so a reconnection can ask it of a candidate handle.

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

		Checks every fact `_writePins` relies on, since a match authorises writing raw bytes to
		output report 0x21. A button cap is one bit per field by definition. The bit offset is
		not exposed, so the payload position relies on the pins filling the report. VID and PID
		are not checked, since none is confirmed across firmware; a rejected path is logged.

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

		Rows and columns only; `numCells` is computed, and its setter raises on a multi line
		display.
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

	So a failed import fails loudly at load instead of silently losing the setting. The id
	stays `pitch`, the config key behind `availablePitchs`; only the label speaks of rows.
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

		Reading `handler.displayDimensions` makes the handler raise `displaySizeChanged`, as in
		`brlMultilineVirtual._announceGeometry`. Queued, so it lands after what the notification
		rebuilds, and only when this is the display NVDA has.
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

		Writes must not run on the main thread, since one can hold `_writeLock` for
		`WRITE_TIMEOUT`. The thread holds bound methods weakly, so a gone driver writes nothing.

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

	def newGraphicsBuffer(self, width: int | None = None, height: int | None = None) -> PinBuffer:
		"""Make a blank buffer to draw into.

		The whole panel by default. A caller drawing into part of it asks for its rectangle's
		size, since an overlay is placed by its top left corner.

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

		3 by 4 on the Monarch, against a cell's 2 by 4; compare with `cellSize` to see whether
		glyphs gain anything. Per cell, so a three cell glyph is nine by four.

		:return: width and height in pins.
		"""
		return self._pitch.slotSize

	@property
	def cellSize(self) -> tuple[int, int]:
		""":return: the pins a braille cell uses, for comparison with `glyphSize`."""
		return self._pitch.cellCols, self._pitch.dotRows

	def newGlyph(self, rows: list[str], fallback) -> "CellGlyph":
		"""Build a glyph this driver can draw, without the caller importing anything.

		The add-on never imports this package, as in `devices.py`, so the factory is here.

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

		Keyed by index into the flat cell array, so a glyph scrolls with the text. A glyph is
		drawn only while the cells there still read its fallback, and a frame that does not
		match retires it (see `_expireGlyphs`). So:

		1. A frame the add-on did not compose, such as a message, is never drawn over.
		2. Displays without glyphs show the fallback cells from the same buffer.
		3. A caret wins, since NVDA ors it into the cell, which then no longer matches.

		The whole set is replaced each time, so the add-on must re-register on every
		recompose. Overlapping runs are dropped; see `_withoutOverlaps`.

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

		Retired rather than skipped, so a later frame that happens to carry the same cells at
		that index cannot bring an old glyph back.

		:param cells: the frame just handed to `display`.
		"""
		if not self._glyphs:
			return
		self._glyphs = {index: glyph for index, glyph in self._glyphs.items() if glyph.matches(cells, index)}

	# --- Graphics overlays ---------------------------------------------------------------

	def setGraphicsOverlay(self, key: str, x: int, y: int, buffer: PinBuffer) -> None:
		"""Put a drawing on the panel, composited over the text on the next write.

		Held rather than drawn now, since the panel is written whole and drawing now would cost
		an extra full refresh.

		:param key: a name, so the same caller can replace its own overlay.
		:param x: pin column of the drawing's left edge.
		:param y: pin row of its top edge.
		:param buffer: what to draw.
		"""
		with self._stateLock:
			self._overlays[key] = (x, y, buffer)
		self._scheduleRepaint()

	def clearGraphicsOverlay(self, key: str | None = None) -> None:
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
	def lastTouch(self) -> tuple[int, int] | None:
		""":return: the pin last touched, or None if the panel is not being touched."""
		return self._lastTouchPin

	@property
	def lastTouchCell(self) -> tuple[int, int] | None:
		"""The braille line and column last touched, at the pitch being rendered.

		At the native pitch the device's own calibrated routing cell; at another pitch the pin,
		converted, since the device's cell assumes the native layout.

		:return: braille row and column, or None if nothing is being touched.
		"""
		if self._lastTouchPin is None:
			return None
		if self._pitch is monarch.PITCH_8_ROW and self._lastTouchCell is not None:
			return divmod(self._lastTouchCell, monarch.NATIVE_ROUTING_COLS)
		return monarch.cellAtPin(self._lastTouchPin[0], self._lastTouchPin[1], self._pitch)

	def _hidOnReceive(self, data: bytes):
		"""Read the touch reports NVDA discards, then let the base class do its work.

		`hidBrailleStandard` drops usage 0x401, the touched pin. Both touch reports are kept;
		see `lastTouchCell`. A report arriving with no device, as during a reconnection before
		the new handle is installed, is dropped, since decoding against None would raise.

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

		The pin is snapshotted when a routing key goes down, since by the time NVDA raises the
		gesture the panel has reported it as zero on release.

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

	def _routingDecision(self, count: int) -> tuple[str, int | None]:
		"""Decide what a routing press means at the pitch being rendered.

		`KEEP` at the native pitch, where the device's calibrated cell indexes the grid drawn.
		At another pitch the device still reports on its native 8 by 32 grid, so the cell is
		derived from the pin. The pin covers one press only, so with no pin or several cells
		the press is cancelled rather than passed through to the wrong cell.

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

	def _recordRouting(self, deviceCells, dispatched, action: str, corrected: int | None) -> None:
		"""Keep what the last routing press decided, so it can be read back after the fact.

		Records the device's cell, the pin and the dispatched index separately, raw before any
		substitution. Kept on the driver as well as logged, so it can be read in the session
		where the press misbehaved::

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

	lastRouting: dict | None = None
	"""What the last routing press decided. See `_recordRouting`."""

	lastRoutingPin: tuple[int, int] | None = None
	"""The pin under the finger at the last routing press, or None if there was none.

	By script time `lastTouch` is already zero, so where a press landed at pin resolution is
	read from here. Set for every dispatched press at both pitches, and kept until the next,
	unlike `_pinAtRouting`, which guards the correction for one press only.
	"""

	def _handleKeyRelease(self):
		"""Raise the gesture, using ours so that routing can be corrected for the pitch.

		Mirrors the inherited method, which names its own `InputGesture` class directly. A
		cancelled gesture is not dispatched; see `_routingDecision`. The routing snapshot is
		always dropped afterwards.
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

	_keyNames: dict = {}
	"""Data index to the name a key gets beyond NVDA's. See `keyNames`. Empty until a device opens."""

	_routingDataIndices: frozenset = frozenset()
	"""The data indices NVDA folds into a routing name, needed to line names up with keys."""

	def _collectInputButtonCapsByDataIndex(self):
		"""Build NVDA's button map, and alongside it the names NVDA's map cannot give.

		Here, where both the constructor and a reconnect read the descriptor, so the names always
		match the keys.

		:return: the inherited map, unchanged.
		"""
		# Forgotten before anything is read, so that if reading this descriptor fails, the previous
		# device's names do not survive onto this one's keys.
		self._keyNames = {}
		self._routingDataIndices = frozenset()
		capsByDataIndex = super()._collectInputButtonCapsByDataIndex()
		try:
			declarations = keyNames.declarationsFromCaps(self._dev.inputButtonCaps)
			self._keyNames = keyNames.namesByDataIndex(declarations)
			self._routingDataIndices = keyNames.routingDataIndices(declarations)
		except Exception:
			# Names are a nicety on top of keys that already work. Losing them must not lose the keys.
			log.error("BrlMultiline: could not name the Monarch's keys", exc_info=True)
			self._keyNames = {}
			self._routingDataIndices = frozenset()
		if self._keyNames:
			log.debug(
				"BrlMultiline: Monarch keys named beyond NVDA's names: "
				+ ", ".join(
					f"{name.ours} (data index {index}, NVDA calls it {name.nvda})"
					for index, name in sorted(self._keyNames.items())
				),
			)
		return capsByDataIndex

	def _inputUsageForDataIndex(self, dataIndex: int) -> int | None:
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

		Never writes the cell reports, which reach only 2,048 of 3,840 pins. Since the driver
		owns the panel, a braille update cannot wipe a drawing.

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

		Cells come from the I/O thread and overlays and glyphs from the main thread, so the
		state is copied under the lock and drawn outside it, consistent and without iterating
		a dictionary another thread is changing.

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

		Reads only the snapshot, never the driver.

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

		Every write is a full mechanical refresh, so overlays set a few at a time are written
		once. The request goes to the main thread, whose turn comes after the caller has
		finished; the write then goes to the I/O thread (see `_onIoThread`). An ordinary
		`display` arriving first publishes the pending state too.
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

		NVDA's `drawBrailleCells`, as the DotPad driver uses, or a local copy without the
		tactile package. Cells pass through untouched, all eight dots at every pitch.

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

		The slot is cleared first, so a glyph replaces its cell rather than merging with it. See
		`setCellGlyphs` for the match.

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

		1. Serialised under `_writeLock`, which a reconnection also takes to swap the handle.
		2. A failure, including a `WRITE_TIMEOUT`, suspends writing before the lock is released
		   (see `_writesSuspended`), and is reported after, to keep the lock order.
		3. A write that gets through resets the reconnect wait; see `_poll`.

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

		Not `device.write`, which waits indefinitely. The buffer comes from the device's own
		`_prepareWriteBuffer`, as `write` would send it.

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

		A lost Bluetooth link fails the read at once with `WinError 1167`, which
		`hwIo.IoBase._ioDone` offers to `_onReadError`; a static display might not write for a
		long time. Chained rather than replaced, respecting a driver that says it handled it.
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

		If nothing could be scheduled, `_reopening` is cleared and writing resumed, so the next
		failed write tries again. Past `ABANDONED_WRITE_LIMIT`, reconnection is refused and
		writing stays suspended.

		:return: whether recovery is now running, which the read error hook reports upward to
			a virtual display.
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

		The backoff lives in the delay passed to each timer. Each attempt carries a generation
		number, which `_stopPolling` moves on, so a timer that could not be cancelled (see
		`_callLater`) returns harmlessly.

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
			`_stopPolling` is discarded.
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

		Only the device is replaced, unlike `brlMultilineVirtual`, so NVDA sees no display
		switch. The candidate is installed only after `_terminated` is checked again under the
		lock, since `terminate` may run concurrently and an unreachable exclusive handle would
		lock every driver out until NVDA restarts.

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

		Key state, touch state and the capability map all describe the old handle; data indexes
		belong to the new device's descriptor, and old key state could complete a stale
		combination.

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

		`_terminated` is set first, so no timer, read or blanking write starts a reconnection.
		With no device the inherited close of `_dev` is skipped and blanking suppressed. The
		flag is set under `_lifecycleLock`, which is released before the base class blanks the
		display, to keep the lock order.
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
				# A device a reconnection installed just before the flag is closed here.
				self._closeStrayDevice()

	gestureMap = HidBrailleDriver.gestureMap
	"""Inherited wholesale.

	The same hardware keys, so existing `hidBrailleStandard` gestures keep working; see
	`InputGesture`.
	"""


class InputGesture(HidInputGesture):
	"""A HID braille gesture that says which driver it came from, and routes for the pitch.

	1. Source: this driver, so `brlMultilineVirtual` can find the member by driver name. The
	   `hidBrailleStandard` identifiers follow as aliases, so existing bindings keep working.
	2. Names: keys NVDA cannot tell apart or has no name for, such as the two d-pads and the
	   zoom keys, get names from `keyNames`, offered before NVDA's. See `_get_identifiers`.
	3. Routing: a press that cannot be corrected for the pitch sets `cancelled`. See
	   `BrailleDisplayDriver._routingDecision`.
	"""

	source = DRIVER_NAME

	cancelled = False
	"""Whether this gesture should be dropped rather than dispatched."""

	_nvdaId: str | None = None
	"""The id NVDA built, when `id` has been given names of our own. None when it has not."""

	def _get_identifiers(self):
		"""Our own identifiers, then NVDA's names for the same keys, then the standard HID ones.

		For the right d-pad's up key, in this order:

		1. `br(brlMultilineMonarch):rightDpadUp`
		2. `br(brlMultilineMonarch):dpadUp`
		3. `br(hidBrailleStandard):dpadUp`

		NVDA searches the user's gesture map with every identifier first, so a binding to one
		pad wins. No `hidBrailleStandard` form of our own names is offered, since that driver
		never produces one.

		:return: identifiers most specific first.
		"""
		ids = super()._get_identifiers()
		ours = f"br({self.source}):"
		theirs = f"br({HidBrailleDriver.name}):"
		mine = [identifier for identifier in ids if identifier.startswith(ours)]
		rest = [identifier for identifier in ids if not identifier.startswith(ours)]
		nvdaForms: list[str] = []
		if self._nvdaId is not None:
			nvdaForms = [
				identifier
				for identifier in (_withId(identifier, self.id, self._nvdaId) for identifier in mine)
				if identifier not in mine
			]
		aliases = [identifier.replace(ours, theirs, 1) for identifier in nvdaForms or mine]
		return mine + nvdaForms + rest + aliases

	def __init__(self, driver, dataIndices):
		"""
		:param driver: the Monarch driver.
		:param dataIndices: the data indices of the keys that were down.
		"""
		super().__init__(driver, dataIndices)
		self._nameKeys(driver, dataIndices)
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

	def _nameKeys(self, driver, dataIndices) -> None:
		"""Give the keys NVDA names poorly names of their own, keeping NVDA's id to offer after.

		:param driver: the Monarch driver, which read the names from the descriptor.
		:param dataIndices: the data indices, in the order the inherited constructor walked them.
		"""
		names = getattr(driver, "_keyNames", None)
		nvdaNames = getattr(self, "keyNames", None)
		if not names or not nvdaNames:
			return
		try:
			specific = keyNames.specificId(
				nvdaNames,
				dataIndices,
				getattr(driver, "_routingDataIndices", frozenset()),
				names,
			)
		except Exception:
			log.error("BrlMultiline: could not name a Monarch gesture's keys", exc_info=True)
			return
		# NVDA's id is its key names joined, the routing name among them. Anything else means the
		# inherited constructor has changed how it builds one, and a name attached on the old
		# understanding could land on the wrong key.
		if specific is None or self.id != "+".join(nvdaNames):
			return
		self._nvdaId = self.id
		self.id = specific


def _withId(identifier: str, current: str, replacement: str) -> str:
	""":return: an identifier with its gesture id swapped, leaving the source and any cell suffix.

	:param identifier: such as `br(brlMultilineMonarch):rightDpadUp`.
	:param current: the id it carries now.
	:param replacement: the id to carry instead.
	"""
	prefix, _, main = identifier.partition(":")
	if not main.startswith(current):
		return identifier
	return f"{prefix}:{replacement}{main[len(current) :]}"


# Re-exported so callers can name usages without importing NVDA's HID driver themselves.
__all__ = ["BrailleDisplayDriver", "BraillePageUsageID", "CellGlyph", "InputGesture", "monarch"]
