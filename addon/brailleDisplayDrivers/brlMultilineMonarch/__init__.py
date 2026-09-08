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
from logHandler import log

from braille.constants import AUTOMATIC_PORT, BLUETOOTH_PORT, USB_PORT
from brailleDisplayDrivers.brlMultilineVirtual import handover
from brailleDisplayDrivers.hidBrailleStandard import BraillePageUsageID, HidBrailleDriver

from . import monarch
from .pinBuffer import PinBuffer

if TYPE_CHECKING:
	from collections import OrderedDict

try:
	addonHandler.initTranslation()
except Exception:
	# Translation is a nicety; failing to set it up must not cost the user their braille.
	log.debugWarning("BrlMultiline: could not initialise Monarch driver translations", exc_info=True)


OPEN_ATTEMPTS = 3
OPEN_RETRY_DELAY = 0.3
"""A device Windows lists is not always openable, especially just after a release.

Both numbers are `brlMultilineVirtual`'s, established on hardware there. See its plan.
"""

POLL_INTERVAL = 5.0
POLL_BACKOFF_LIMIT = 60.0
"""How often to look for a device that has dropped, and how far apart that may grow."""


class CellGlyph:
	"""A shape drawn in place of one braille cell, filling the cell's whole slot.

	A braille cell is two dot columns and a blank one; the blank is there so a reader can tell
	one cell from the next, not because the hardware needs it. A glyph fills all three, giving
	3 by 4 instead of 2 by 4 without moving anything: the next cell still starts where it did,
	so routing, layout and scrolling are untouched.

	Built through `BrailleDisplayDriver.newGlyph` rather than directly, so the add-on never
	imports this package.
	"""

	def __init__(self, pattern: PinBuffer, fallbackCell: int):
		"""
		:param pattern: the shape, clipped to the slot.
		:param fallbackCell: the braille cell this stands in for, which the add-on has written
			into the buffer at the same position. The glyph is drawn only while the cell there
			still reads it.
		"""
		self.pattern = pattern
		self.fallbackCell = fallbackCell


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
		self._pitch = monarch.PITCHES[monarch.PITCH_8_ROW.name]
		self._overlays: dict[str, tuple[int, int, PinBuffer]] = {}
		self._lastCells: list[int] = []
		self._glyphs: dict[int, CellGlyph] = {}
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
		# Everything from here on can raise on a device that opened perfectly well, and the
		# handle is ours the moment it did. Nothing else can reach this instance once the
		# constructor throws, so it has to let go of the device itself — a leaked exclusive
		# handle takes the display away from every other driver until NVDA restarts, which is
		# exactly what a failure here used to do.
		try:
			self._verifyDotOrder()
			self._pinCap = self._findPinCap()
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
		if pitch is self._pitch:
			return
		self._pitch = pitch
		self._applyPitch()
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

	# --- Cell glyphs -----------------------------------------------------------------------

	@property
	def glyphSize(self) -> tuple[int, int]:
		"""The pins a glyph may fill, which is one cell slot including its gap column.

		3 by 4 on the Monarch, against a braille cell's 2 by 4. A display whose cells have no
		gap to reclaim would report the same size as its cell and a glyph would gain nothing;
		the add-on can compare this with `cellSize` to find out whether glyphs are worth using.

		:return: width and height in pins.
		"""
		return self._pitch.slotSize

	@property
	def cellSize(self) -> tuple[int, int]:
		""":return: the pins a braille cell uses, for comparison with `glyphSize`."""
		return self._pitch.cellCols, self._pitch.dotRows

	def newGlyph(self, rows: list[str], fallbackCell: int) -> "CellGlyph":
		"""Build a glyph this driver can draw, without the caller importing anything.

		The add-on reaches the driver as a live object and does not import the package — the
		same rule `devices.py` follows — so the factory lives here rather than the class being
		imported there.

		:param rows: the shape, one string per row, as `PinBuffer.fromRows` takes it. Anything
			past the slot is clipped.
		:param fallbackCell: the braille cell the add-on has put in the buffer at this
			position. See `setCellGlyphs` for what it is for.
		:return: the glyph.
		"""
		return CellGlyph(PinBuffer.fromRows(rows), fallbackCell)

	def setCellGlyphs(self, glyphs: dict[int, "CellGlyph"]) -> None:
		"""Replace the set of cells that are drawn as glyphs rather than as braille.

		Keyed by index into the flat cell array the handler writes, so a glyph sits in the
		text and scrolls with it: it occupies exactly one cell slot and the next cell starts
		where it always would.

		Each glyph carries the braille cell it stands in for, and is drawn **only if the cell
		at that index still reads it**. That does three things at once:

		1. A frame the add-on did not compose — a braille message, say — cannot get a glyph
		   painted over unrelated content. The glyph is skipped and the cell shows instead.
		2. The fallback is what a display without glyphs shows anyway, so the add-on writes
		   one buffer and every display renders the best it can. A solid dots 1 to 6 becomes a
		   true 3 by 3 square here and stays a recognisable block elsewhere.
		3. A caret wins. NVDA ors the cursor shape into the cell before the driver sees it, so
		   the cell no longer matches and the braille cell with its cursor is drawn. Deliberate:
		   the reader needs the caret more than the symbol.

		The whole set is replaced, so the add-on registers what it wants on each recompose and
		never has to clear individually.

		:param glyphs: cell index to glyph. Empty clears them.
		"""
		self._glyphs = dict(glyphs)
		self._repaint()

	def clearCellGlyphs(self) -> None:
		"""Draw every cell as braille again."""
		self.setCellGlyphs({})

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

		**Cells are passed through untouched.** All eight dots are drawn at every pitch, so a
		caret shows and an eight dot table reads. At 10 rows the fourth row of a cell is the
		one that would otherwise separate the lines, which is a density the reader chooses by
		choosing the pitch — it is not the driver's business to decide a dot should not appear.

		:param buffer: the panel buffer.
		:param cells: one dot pattern per cell, row major.
		"""
		pitch = self._pitch
		for row in range(pitch.numRows):
			start = row * pitch.numCols
			rowCells = cells[start : start + pitch.numCols]
			if not rowCells:
				break
			self._drawRow(buffer, list(rowCells), row, pitch)
			self._drawGlyphs(buffer, list(rowCells), row, start, pitch)

	def _drawGlyphs(
		self,
		buffer: PinBuffer,
		rowCells: list[int],
		row: int,
		start: int,
		pitch,
	) -> None:
		"""Replace any cell in this row that has a glyph registered and still matches it.

		Drawn after the row rather than instead of it, and the slot is cleared first, so a
		glyph replaces its cell rather than merging with it. Merging would be worse than
		useless: a symbol or-ed with a letter is neither.

		The match against `fallbackCell` is what makes a stale registration harmless. See
		`setCellGlyphs`.

		:param buffer: the panel buffer.
		:param rowCells: the line's cells.
		:param row: which braille line.
		:param start: the flat index of the first cell in this row.
		:param pitch: the layout being rendered.
		"""
		if not self._glyphs:
			return
		slotWidth, slotHeight = pitch.slotSize
		for col, cell in enumerate(rowCells):
			glyph = self._glyphs.get(start + col)
			if glyph is None or cell != glyph.fallbackCell:
				continue
			x, y = monarch.cellOrigin(row, col, pitch)
			buffer.clearRect(x, y, slotWidth, slotHeight)
			for dotY in range(min(slotHeight, glyph.pattern.height)):
				for dotX in range(min(slotWidth, glyph.pattern.width)):
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
			handover.stopReleasingOnSwitch(self.name)
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
__all__ = ["BrailleDisplayDriver", "BraillePageUsageID", "CellGlyph", "monarch"]
