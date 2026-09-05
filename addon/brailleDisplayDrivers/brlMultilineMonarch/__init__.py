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

- A choice of line pitch. 40 rows is 8 lines of eight dot braille or 10 lines of six dot.
- Graphics and text on one surface, composed here, with no mode switch and nothing suspended.
- Touch at pin resolution, which NVDA discards, so a drawing can be pointed at.
- Bluetooth that survives a dropout, using `brlMultilineVirtual`'s approach.
"""

import threading
import time
from typing import Optional

import addonHandler
import bdDetect
import braille
import braille.display.driver
import hwIo.hid
from logHandler import log

from brailleDisplayDrivers.hidBrailleStandard import BraillePageUsageID, HidBrailleDriver

from . import monarch
from .pinBuffer import PinBuffer

try:
	addonHandler.initTranslation()
except Exception:
	# Translation is a nicety; failing to set it up must not cost the user their braille.
	log.debugWarning("BrlMultiline: could not initialise Monarch driver translations", exc_info=True)

try:
	from autoSettingsUtils.driverSetting import DriverSetting
	from autoSettingsUtils.utils import StringParameterInfo
except ImportError:  # pragma: no cover - only in a stripped test environment.
	DriverSetting = None
	StringParameterInfo = None

OPEN_ATTEMPTS = 3
OPEN_RETRY_DELAY = 0.3
"""A device Windows lists is not always openable, especially just after a release.

Both numbers are `brlMultilineVirtual`'s, established on hardware there. See its plan.
"""

POLL_INTERVAL = 5.0
POLL_BACKOFF_LIMIT = 60.0
"""How often to look for a device that has dropped, and how far apart that may grow."""


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

	name = "brlMultilineMonarch"
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
		self._pitch = monarch.PITCHES[monarch.PITCH_EIGHT_DOT.name]
		self._overlays: dict[str, tuple[int, int, PinBuffer]] = {}
		self._lastCells: list[int] = []
		self._lastTouchPin: Optional[tuple[int, int]] = None
		self._lastTouchCell: Optional[int] = None
		self._writeLock = threading.Lock()
		self._pinCap = None
		self._watched: Optional[tuple] = None
		self._pollTimer = None
		self._retryDelay = POLL_INTERVAL
		self._nextAttempt = 0.0
		self._reopening = False
		self._port = port

		self._openWithRetry(port)
		self._verifyDotOrder()
		self._pinCap = self._findPinCap()
		if self._pinCap is None:
			# Not a Monarch, or a firmware without the pin array. Refuse rather than silently
			# behaving like the standard driver, because the user chose this one on purpose.
			super().terminate()
			raise RuntimeError(
				"This HID braille display has no pin report; use Standard HID Braille Display instead",
			)
		self._applyPitch()
		self._watchForDisconnect()
		log.info(
			f"BrlMultiline: Monarch on {monarch.PIN_WIDTH} by {monarch.PIN_HEIGHT} pins, "
			f"{self._pitch.numRows} rows of {self._pitch.numCols} at pitch {self._pitch.name}",
		)

	def _openWithRetry(self, port) -> None:
		"""Run the inherited constructor, retrying a device that is present but not openable.

		`hidBrailleStandard.__init__` raises `RuntimeError` when it finds nothing. Phase 0 of
		the virtual display plan established that a display NVDA released moments earlier can
		refuse to open twice and then open normally, so a single attempt is not enough when
		switching displays.

		:param port: as NVDA passes it.
		:raises RuntimeError: if every attempt failed.
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
				return
		raise RuntimeError(f"Monarch did not open after {OPEN_ATTEMPTS} attempts") from lastError

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

	def _findPinCap(self):
		"""Look for the output button array that is the pin matrix.

		Declared as one usage repeated 3,840 times rather than as a usage range, so the count
		is in `ReportCount` and not in the usage span. Reading only the usage span is how this
		report stayed hidden: it looks like a single bit.

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
		count = self._dev.caps.NumberOutputButtonCaps
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
				self._dev._pd,
			)
		except Exception:
			log.debugWarning("BrlMultiline: could not read output button caps", exc_info=True)
			return None
		for cap in capsList[: number.value]:
			usages = cap.u1.Range.UsageMax - cap.u1.Range.UsageMin + 1 if cap.IsRange else 1
			if max(usages, cap.ReportCount) >= monarch.PIN_COUNT:
				return cap
		return None

	# --- Geometry ----------------------------------------------------------------------

	def _applyPitch(self) -> None:
		"""Tell NVDA the shape this pitch gives, so everything above sizes itself correctly."""
		self.numRows = self._pitch.numRows
		self.numCols = self._pitch.numCols
		self.numCells = self._pitch.numRows * self._pitch.numCols

	if DriverSetting is not None:
		supportedSettings = [  # noqa: RUF012
			DriverSetting(
				"pitch",
				# Translators: label for the Monarch line pitch setting.
				_("&Line pitch"),
				useConfig=True,
			),
		]

	def _get_availablePitchs(self) -> dict:
		"""NVDA's naming: the choices for the `pitch` setting.

		:return: setting value to displayable choice.
		"""
		if StringParameterInfo is None:
			return {}
		return {
			monarch.PITCH_EIGHT_DOT.name: StringParameterInfo(
				monarch.PITCH_EIGHT_DOT.name,
				# Translators: a Monarch line pitch choice.
				_("8 rows, eight dot"),
			),
			monarch.PITCH_SIX_DOT.name: StringParameterInfo(
				monarch.PITCH_SIX_DOT.name,
				# Translators: a Monarch line pitch choice.
				_("10 rows, six dot"),
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
		if pitch is self._pitch:
			return
		self._pitch = pitch
		self._applyPitch()
		if pitch.dropsDots7And8:
			log.info("BrlMultiline: six dot pitch; dots 7 and 8 have no row and are not shown")
		handler = braille.handler
		if handler is not None:
			try:
				handler.handleDisplaySizeChanged()
			except Exception:
				log.debugWarning("BrlMultiline: could not announce the new Monarch shape", exc_info=True)

	@property
	def graphicsSize(self) -> tuple[int, int]:
		"""The published capability the add-on looks for.

		A display without this attribute cannot draw and the add-on falls back to text, which
		is how graphics support stays device agnostic.

		:return: the pin grid, width by height.
		"""
		return monarch.PIN_WIDTH, monarch.PIN_HEIGHT

	def newGraphicsBuffer(self) -> PinBuffer:
		""":return: a blank buffer the size of this display's pin grid."""
		return PinBuffer(monarch.PIN_WIDTH, monarch.PIN_HEIGHT)

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
		self._overlays[key] = (x, y, buffer)
		self._repaint()

	def clearGraphicsOverlay(self, key: Optional[str] = None) -> None:
		"""Remove one overlay, or all of them.

		:param key: which overlay, or None for every one.
		"""
		if key is None:
			self._overlays.clear()
		else:
			self._overlays.pop(key, None)
		self._repaint()

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
		if self._pitch is monarch.PITCH_EIGHT_DOT and self._lastTouchCell is not None:
			return divmod(self._lastTouchCell, monarch.NATIVE_ROUTING_COLS)
		return monarch.cellAtPin(self._lastTouchPin[0], self._lastTouchPin[1], self._pitch)

	def _hidOnReceive(self, data: bytes):
		"""Read the touch reports NVDA discards, then let the base class do its work.

		`hidBrailleStandard` inspects input values only to find `NUMBER_OF_BRAILLE_CELLS`, so
		usage 0x401 — the touched pin — is dropped on the floor and a touch on a graphic
		reaches the log as an event with no number attached.

		Both touch reports are kept. See `lastTouchCell` for why the cell one still earns its
		place now that the pin one exists.

		:param data: the raw input report.
		"""
		try:
			self._readTouch(data)
		except Exception:
			log.debugWarning("BrlMultiline: could not decode a Monarch touch report", exc_info=True)
		super()._hidOnReceive(data)

	def _readTouch(self, data: bytes) -> None:
		"""Decode a touch report, if this is one.

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
		self._lastCells = list(cells)
		self._repaint()

	def _repaint(self) -> None:
		"""Compose the panel from the last cells and the current overlays, and write it."""
		buffer = PinBuffer(monarch.PIN_WIDTH, monarch.PIN_HEIGHT)
		self._drawCells(buffer, self._lastCells)
		for x, y, overlay in self._overlays.values():
			buffer.blit(overlay, x, y)
		self._writePins(buffer)

	def _drawCells(self, buffer: PinBuffer, cells: list[int]) -> None:
		"""Draw braille cells into the buffer at the current pitch.

		Uses NVDA's own `drawBrailleCells` where it can, which is the same function the DotPad
		driver uses for the same reason: when the only surface is a pin grid, characters are
		drawn into it. Falls back to a local copy if that module is unavailable, so the driver
		still works on an NVDA without the tactile package.

		:param buffer: the panel buffer.
		:param cells: one dot pattern per cell, row major.
		"""
		pitch = self._pitch
		mask = 0x3F if pitch.dropsDots7And8 else 0xFF
		for row in range(pitch.numRows):
			start = row * pitch.numCols
			rowCells = [cell & mask for cell in cells[start : start + pitch.numCols]]
			if not rowCells:
				break
			self._drawRow(buffer, rowCells, row, pitch)

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
		garbled panel.

		:param buffer: the whole panel.
		"""
		if self._pinCap is None:
			return
		payload = monarch.packPins(buffer)
		with self._writeLock:
			try:
				report = hwIo.hid.HidOutputReport(self._dev, reportID=monarch.PIN_REPORT_ID)
				data = bytearray(report.data)
				room = len(data) - 1
				data[1 : 1 + min(room, len(payload))] = payload[:room]
				self._dev.write(bytes(data))
			except Exception:
				log.debugWarning("BrlMultiline: Monarch pin write failed", exc_info=True)
				self._onDeviceLost()

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
			if not handled:
				log.warning(f"BrlMultiline: Monarch stopped responding (error {error}); will retry")
				self._onDeviceLost()
			return handled

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

	def _onDeviceLost(self) -> None:
		"""Begin trying to get the device back. Safe to call more than once."""
		if self._reopening:
			return
		self._reopening = True
		self._retryDelay = POLL_INTERVAL
		self._nextAttempt = time.monotonic()
		self._startPolling()

	def _startPolling(self) -> None:
		"""Start the reconnect timer. Safe to call when one is already running."""
		if self._pollTimer is not None:
			return
		try:
			import wx

			self._pollTimer = wx.CallLater(int(POLL_INTERVAL * 1000), self._poll)
		except Exception:
			# No timer means no reconnection, and everything else goes on working. The one
			# place this happens is a test harness with no wx.
			log.debugWarning("BrlMultiline: could not start looking for the Monarch", exc_info=True)

	def _stopPolling(self) -> None:
		"""Stop the reconnect timer. Safe to call when nothing is running."""
		timer, self._pollTimer = self._pollTimer, None
		if timer is None:
			return
		try:
			timer.Stop()
		except Exception:
			log.debugWarning("BrlMultiline: could not stop looking for the Monarch", exc_info=True)

	def _poll(self) -> None:
		"""Try to reopen the device, and keep trying with a growing wait."""
		self._pollTimer = None
		if not self._reopening:
			return
		try:
			if time.monotonic() >= self._nextAttempt and self._reopenDevice():
				self._reopening = False
				self._retryDelay = POLL_INTERVAL
				log.info("BrlMultiline: the Monarch is back")
				self._repaint()
				return
		except Exception:
			log.error("BrlMultiline: error reopening the Monarch", exc_info=True)
		self._retryDelay = min(self._retryDelay * 2, POLL_BACKOFF_LIMIT)
		self._nextAttempt = time.monotonic() + self._retryDelay
		self._startPolling()

	def _reopenDevice(self) -> bool:
		"""Close the dead handle and open the device again, keeping this driver alive.

		The difference from `brlMultilineVirtual`, which replaces a whole failed member: here
		the driver stays and only its device is replaced. NVDA is never told the display went
		away, so there is no display switch, no fallback to no braille, and no restart.

		:return: whether the device is open again.
		"""
		self._stopWatching()
		old = getattr(self, "_dev", None)
		if old is not None:
			try:
				old.close()
			except Exception:
				log.debugWarning("BrlMultiline: could not close the old Monarch handle", exc_info=True)
		self._dev = None
		self._inputUsages = None
		for portType, portId, port, portInfo in self._getTryPorts(self._port):  # noqa: B007
			if portType != bdDetect.ProtocolType.HID:
				continue
			try:
				device = hwIo.hid.Hid(port, onReceive=self._hidOnReceive)
			except OSError:
				continue
			if device.usagePage != bdDetect.HID_USAGE_PAGE_BRAILLE:
				device.close()
				continue
			self._dev = device
			self._pinCap = self._findPinCap()
			if self._pinCap is None:
				device.close()
				self._dev = None
				continue
			self._watchForDisconnect()
			return True
		return False

	# --- Shutdown ------------------------------------------------------------------------

	def terminate(self):
		"""Stop polling and unhook before letting the base class close the device."""
		try:
			self._stopPolling()
			self._reopening = False
			self._stopWatching()
			self._overlays.clear()
		finally:
			super().terminate()

	gestureMap = HidBrailleDriver.gestureMap
	"""Inherited wholesale.

	The keys, routing and controls are the same hardware whichever report the pins arrive in,
	so a user's existing `hidBrailleStandard` gestures keep working. Note that the identifiers
	in the inherited map name that driver, which is what NVDA matches against the gesture's
	own source; see `InputGesture.source` in the base module.
	"""


# Re-exported so callers can name usages without importing NVDA's HID driver themselves.
__all__ = ["BrailleDisplayDriver", "BraillePageUsageID", "monarch"]
