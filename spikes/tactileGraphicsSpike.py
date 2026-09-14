# BrlMultiline: phase 0 spike for tactile graphics over HID braille.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

r"""Console spike: how much of the Monarch's pin grid can we actually reach?

Everything in `docs/design/tactile-graphics-plan.md` rests on questions that cannot be
settled by reading source. Import this from the NVDA Python console (NVDA+control+z) with a
Monarch connected in terminal mode and selected as NVDA's braille display.

	import sys; sys.path.insert(0, r"C:\code\BrlMultiline\spikes")
	import tactileGraphicsSpike as spike
	spike.hunt()

After editing this file, the console will keep running the version it first imported, because
`import` is a no op once a module is in `sys.modules`. Reload it:

	import importlib; spike = importlib.reload(spike)

That is safe to do while the display is held. `hold` shadows the handler's `_writeCells`
with an instance attribute and `release` deletes it again, so neither depends on module
state that a reload would discard. `isHeld` reads the handler rather than a flag.

Hardware runs have now confirmed what the plan did not expect. The Monarch declares an
output report of 481 bytes, where eight rows of thirty-two cells need only 33, and the
extra is a single output *button* cap:

	report 0x21, usage 0x301, collection 4 (link usage 0x300), ReportCount 3840

Three thousand eight hundred and forty one bit fields is 480 bytes, which is the whole 481
byte report, and 3,840 is exactly the Monarch's pin count. It is declared in the same
vendor collection, usage 0x300, that owns the eight braille rows.

That is the entire pin grid, on the interface NVDA already has open. NVDA cannot see it:
`hidBrailleStandard._findCellValueCaps` keeps only value caps whose link usage is
`BRAILLE_ROW`, and NVDA reads button caps for input only.

**It is confirmed working.** With the handler held, `pinClear` lowered the whole panel and
`pinFill` raised it fully solid. No mode switch, no feature write. What remains is the bit
order, which `pinBit` and `pinWalk` exist to establish by touch.

Note that output report 0x21 and *feature* report 0x21 are different reports — HID report
IDs are namespaced per report type. The feature one carries usage 0x301 again as a 0 or 1
value, alongside 0x302 and 0x304, and reads `21 01 50 20`.

So the run order is now:

	spike.hunt()          # read only. Look at the OUTPUT button caps section.
	spike.reports()       # which report ID is the big one, and how big
	spike.pins()          # what the pin report looks like, still without writing

Then, if `pins` reports a plausible pin array, the layout has to be discovered by touch,
because a bit index is not a coordinate until something says which way it runs:

	spike.hold()
	spike.pinClear()
	spike.pinBit(0)       # feel where bit 0 lands
	spike.pinBit(1)
	spike.pinBit(96)      # row major would put this directly below bit 0
	spike.pinBit(40)      # column major would put this directly right of bit 0
	spike.pinWalk(0, 12)  # first twelve bits in sequence, pausing between
	spike.release()

**`hold` freezes NVDA's braille output** so a pattern stays up long enough to feel.
`release` gives it back and is safe to call twice. Call it before closing the console.

What this module will not do: write a feature report. Feature 0x21 carries a boolean at
usage 0x301 inside the vendor collection that owns the display rows, which has the shape of
a mode switch, and it may well be what gates the pin report. Flipping an unknown vendor
mode flag on an actuator array is not research. If the pin report turns out to be inert,
that is a question for Humanware, not a thing to poke.
"""

from __future__ import annotations

import ctypes
import time
from collections import deque
from ctypes import byref
from ctypes.wintypes import ULONG, USHORT
from typing import Any, Optional

import braille
import core
import hidpi
import hwPortUtils
import winBindings.hid
from braille.regions.base import TextRegion
from hwIo.hid import HidInputReport, HidOutputReport, check_HidP_status
from logHandler import log

# --- Dot numbering -------------------------------------------------------------------

try:
	from tactile.braille import _brailleDotCoords
except ImportError:  # NVDA older than 2025.1 has no tactile package.
	_brailleDotCoords = [
		(0, 0),  # dot1
		(0, 1),  # dot2
		(0, 2),  # dot3
		(1, 0),  # dot4
		(1, 1),  # dot5
		(1, 2),  # dot6
		(0, 3),  # dot7
		(1, 3),  # dot8
	]

BIT_FOR_CELL_POSITION: dict[tuple[int, int], int] = {
	coords: bit for bit, coords in enumerate(_brailleDotCoords)
}
"""Which bit of a cell byte raises the pin at a position within that cell.

Derived from NVDA's own table rather than transcribed, so it cannot drift from core. Note
this is **not** the packing `dotPad.driver.DpTactileGraphicsBuffer` uses, which puts the
left column in bits 0 to 3 and the right column in bits 4 to 7. That is not braille dot
numbering and would render scrambled through a HID `8 Dot Braille Cell` value. `dotOrder`
is the hardware check that this table is the right one, and `pinVLine` turned out to be a
second one: the pin path had copied the DotPad packing and drew a column of p.
"""

POSITION_FOR_CELL_BIT: tuple = tuple(tuple(coords) for coords in _brailleDotCoords)
"""Where in a 2 by 4 block each bit of a cell byte raises its pin.

The same table read the other way, kept beside it so an index and a position cannot come
from two different beliefs about the packing.
"""

try:
	from tactile import TactileGraphicsBuffer as _BufferBase
except ImportError:  # NVDA older than 2025.1.

	class _BufferBase:  # type: ignore[no-redef]
		def __init__(self, width: int, height: int):
			self.width = width
			self.height = height


"""The seam NVDA core offers for tactile graphics, present since 2025.1.

Defined here, above both canvases, because `PinCanvas` and `CellCanvas` both subclass it.
"""


CELL_WIDTH = 2
CELL_HEIGHT = 4
PIN_COLUMNS_PER_CELL = 3
"""Two dot columns and one spacer, per the Monarch's published 96 by 40 pin geometry."""

MONARCH_PINS = 3840
"""The published pin count, and the number this device's descriptor keeps referring to."""

BRAILLE_USAGE_PAGE = 0x41


def _say(message: str) -> None:
	"""Print for the console and log, so a session leaves a trace in the NVDA log."""
	print(message)
	log.info(f"tactileGraphicsSpike: {message}")


def _display() -> Optional[Any]:
	""":return: the driver NVDA is currently using, or None with a complaint printed."""
	handler = braille.handler
	if handler is None:
		_say("No braille handler. NVDA is not far enough through startup.")
		return None
	display = handler.display
	if display is None or not display.numCells:
		_say("The braille handler has no display with cells.")
		return None
	return display


def _hidDriver() -> Optional[Any]:
	""":return: the HID braille driver to inspect, or None with a complaint printed.

	The display itself when NVDA drives it directly. Through `brlMultilineVirtual` the display
	is the composite, which has no device of its own, so the HID member behind it is used.
	"""
	display = _display()
	if display is None:
		return None
	if hasattr(getattr(display, "_dev", None), "caps"):
		return display
	for slot in getattr(display, "slots", ()):
		if hasattr(getattr(slot.driver, "_dev", None), "caps"):
			_say(f"Using {slot.driverName}, a member of {display.name}.")
			return slot.driver
	_say(f"{display.name} is not a HID driver, and has no HID member.")
	return None


def _device() -> Optional[Any]:
	""":return: the `hwIo.hid.Hid` the current driver opened, or None."""
	driver = _hidDriver()
	return None if driver is None else driver._dev


def _usagePageName(page: int) -> str:
	""":return: a readable name for a usage page, so anything unexpected stands out."""
	known = {
		0x01: "Generic Desktop",
		0x07: "Keyboard",
		0x08: "LED",
		0x0C: "Consumer",
		0x41: "Braille Display",
	}
	if page in known:
		return known[page]
	if 0xFF00 <= page <= 0xFFFF:
		return "VENDOR DEFINED"
	return "unrecognised"


def _brailleUsageName(usage: int) -> str:
	""":return: the name of a braille page usage, or a marker that it is not a known one."""
	try:
		from brailleDisplayDrivers.hidBrailleStandard import BraillePageUsageID

		return BraillePageUsageID(usage).name
	except (ImportError, ValueError):
		return "NOT A STANDARD BRAILLE USAGE"


# --- Question 3: geometry -------------------------------------------------------------


def geometry() -> None:
	"""Report what NVDA thinks the display is, and what that means as a canvas."""
	display = _display()
	if display is None:
		return
	dimensions = braille.handler.displayDimensions
	_say(f"Driver: {display.name} ({display.description})")
	_say(f"Driver geometry: {display.numRows} rows of {display.numCols}, {display.numCells} cells")
	_say(f"Handler geometry: {dimensions.numRows} rows of {dimensions.numCols}")
	_say(f"Thread safe: {display.isThreadSafe}. Wants acknowledgements: {display.receivesAckPackets}")
	width = display.numCols * CELL_WIDTH
	height = display.numRows * CELL_HEIGHT
	_say(f"Canvas through cells: {width} by {height} dots, {width * height} pins addressable")


def lattice() -> None:
	"""Show which physical pin columns the *cell* canvas can and cannot reach.

	Arithmetic on the published three pin columns per cell, not a measurement. Only
	describes the cell path; if the pin report in `pins` works, none of this applies.
	"""
	display = _display()
	if display is None:
		return
	width = display.numCols * CELL_WIDTH
	reachable = [(x // CELL_WIDTH) * PIN_COLUMNS_PER_CELL + (x % CELL_WIDTH) for x in range(width)]
	physicalWidth = display.numCols * PIN_COLUMNS_PER_CELL
	unreachable = [p for p in range(physicalWidth) if p not in set(reachable)]
	_say(f"Physical columns if three pins per cell: {physicalWidth}")
	_say(f"Reachable through cells: {len(reachable)} columns, first twelve {reachable[:12]}")
	_say(f"Unreachable spacers: {len(unreachable)} columns, first twelve {unreachable[:12]}")


# --- Descriptor forensics ---------------------------------------------------------------


class _LinkCollectionNode(ctypes.Structure):
	"""`HIDP_LINK_COLLECTION_NODE`, which NVDA does not bind."""

	_fields_ = [
		("LinkUsage", USHORT),
		("LinkUsagePage", USHORT),
		("Parent", USHORT),
		("NumberOfChildren", USHORT),
		("NextSibling", USHORT),
		("FirstChild", USHORT),
		("_bits", ULONG),
		("UserContext", ctypes.c_void_p),
	]

	@property
	def collectionType(self) -> int:
		return self._bits & 0xFF


_REPORT_TYPES = (
	("OUTPUT", hidpi.HIDP_REPORT_TYPE.OUTPUT),
	("INPUT", hidpi.HIDP_REPORT_TYPE.INPUT),
	("FEATURE", hidpi.HIDP_REPORT_TYPE.FEATURE),
)


def _valueCaps(device: Any, reportType: hidpi.HIDP_REPORT_TYPE) -> list[Any]:
	"""Read every value cap of one report type, including types NVDA never asks for."""
	counts = {
		hidpi.HIDP_REPORT_TYPE.INPUT: device.caps.NumberInputValueCaps,
		hidpi.HIDP_REPORT_TYPE.OUTPUT: device.caps.NumberOutputValueCaps,
		hidpi.HIDP_REPORT_TYPE.FEATURE: device.caps.NumberFeatureValueCaps,
	}
	count = counts[reportType]
	if not count:
		return []
	capsList = (hidpi.HIDP_VALUE_CAPS * count)()
	number = ctypes.c_ushort(count)
	check_HidP_status(winBindings.hid.HidP_GetValueCaps, reportType, capsList, byref(number), device._pd)
	return list(capsList[: number.value])


def _buttonCaps(device: Any, reportType: hidpi.HIDP_REPORT_TYPE) -> list[Any]:
	"""Read every button cap of one report type.

	The one NVDA never reads is the output set, and on the Monarch that is where the pin
	array appears to live. A button is one bit; a button cap declaring a usage range of
	3,840 usages is a 480 byte report with one bit per pin.
	"""
	counts = {
		hidpi.HIDP_REPORT_TYPE.INPUT: device.caps.NumberInputButtonCaps,
		hidpi.HIDP_REPORT_TYPE.OUTPUT: device.caps.NumberOutputButtonCaps,
		hidpi.HIDP_REPORT_TYPE.FEATURE: device.caps.NumberFeatureButtonCaps,
	}
	count = counts[reportType]
	if not count:
		return []
	capsList = (hidpi.HIDP_BUTTON_CAPS * count)()
	number = ctypes.c_ushort(count)
	check_HidP_status(winBindings.hid.HidP_GetButtonCaps, reportType, capsList, byref(number), device._pd)
	return list(capsList[: number.value])


def _capBitCount(cap: Any) -> int:
	""":return: how many one bit fields a button cap occupies in its report.

	There are two ways a descriptor can declare a block of bits, and the Monarch uses both,
	so counting only one of them gets the wrong answer.

	A *usage range* declares one usage per bit and leaves `ReportCount` at 1: the Monarch's
	routing keys are `0x402` to `0x501`, 256 usages, 256 bits, 32 bytes, which is exactly its
	33 byte input report.

	A *repeated single usage* declares one usage and puts the count in `ReportCount`: the
	Monarch's output report 0x21 is usage `0x301` with `ReportCount` 3840. That is 3,840 one
	bit fields, 480 bytes, which is exactly its 481 byte output report. One bit per pin.

	The first version of this function returned the usage count alone, so it read the pin
	array as a single bit and `pinCap` dismissed it. Taking the larger of the two is right
	for both forms.
	"""
	usages = cap.u1.Range.UsageMax - cap.u1.Range.UsageMin + 1 if cap.IsRange else 1
	return max(usages, cap.ReportCount)


def _describeValueCap(cap: Any) -> str:
	""":return: one line describing a value cap."""
	if cap.IsRange:
		usageText = f"usages 0x{cap.u1.Range.UsageMin:X} to 0x{cap.u1.Range.UsageMax:X}"
		usageName = "(range)"
	else:
		usage = cap.u1.NotRange.Usage
		usageText = f"usage 0x{usage:X}"
		usageName = _brailleUsageName(usage) if cap.UsagePage == BRAILLE_USAGE_PAGE else ""
	return (
		f"  report 0x{cap.ReportID:02X} page 0x{cap.UsagePage:04X} ({_usagePageName(cap.UsagePage)}) "
		f"{usageText} {usageName} in collection {cap.LinkCollection} "
		f"(link page 0x{cap.LinkUsagePage:04X} usage 0x{cap.LinkUsage:X}), "
		f"count {cap.ReportCount} of {cap.BitSize} bits, logical {cap.LogiclMin} to {cap.LogicalMax}"
	)


def _describeButtonCap(cap: Any) -> str:
	""":return: one line describing a button cap, with its bit count made explicit."""
	if cap.IsRange:
		r = cap.u1.Range
		usageText = f"usages 0x{r.UsageMin:X} to 0x{r.UsageMax:X}"
		indexText = f"data indices {r.DataIndexMin} to {r.DataIndexMax}"
	else:
		nr = cap.u1.NotRange
		usageText = f"usage 0x{nr.Usage:X} {_brailleUsageName(nr.Usage) if cap.UsagePage == BRAILLE_USAGE_PAGE else ''}"
		indexText = f"data index {nr.DataIndex}"
	bits = _capBitCount(cap)
	# Say which of the two declaration forms this is, because they look alike in a dump and
	# reading the wrong one is exactly how the pin array got missed the first time.
	if cap.IsRange:
		shapeText = f"{bits} usages, one bit each"
	else:
		shapeText = f"one usage repeated {cap.ReportCount} times"
	return (
		f"  report 0x{cap.ReportID:02X} page 0x{cap.UsagePage:04X} ({_usagePageName(cap.UsagePage)}) "
		f"{usageText} in collection {cap.LinkCollection} "
		f"(link page 0x{cap.LinkUsagePage:04X} usage 0x{cap.LinkUsage:X}), "
		f"{shapeText} = {bits} bits = {bits / 8:.0f} bytes, reportCount {cap.ReportCount}, {indexText}"
	)


def descriptor() -> None:
	"""Dump every value cap **and every button cap** the device advertises.

	The button caps are the point. NVDA reads output value caps and input button caps, and
	never looks at output buttons at all, so an output button array is invisible to it and
	would never appear in any NVDA log. On the Monarch that is where the 480 byte report
	appears to be.
	"""
	device = _device()
	if device is None:
		return
	caps = device.caps
	_say("--- Device capabilities ---")
	_say(
		f"Top level usage page 0x{caps.UsagePage:04X} ({_usagePageName(caps.UsagePage)}), usage 0x{caps.Usage:X}"
	)
	_say(
		f"Report byte lengths: input {caps.InputReportByteLength}, "
		f"output {caps.OutputReportByteLength}, feature {caps.FeatureReportByteLength}",
	)
	_say(
		f"Counts: {caps.NumberLinkCollectionNodes} collections, "
		f"{caps.NumberInputValueCaps} input values, {caps.NumberOutputValueCaps} output values, "
		f"{caps.NumberFeatureValueCaps} feature values, "
		f"{caps.NumberInputButtonCaps} input buttons, {caps.NumberOutputButtonCaps} output buttons, "
		f"{caps.NumberFeatureButtonCaps} feature buttons",
	)

	foreign: list[str] = []
	for label, reportType in _REPORT_TYPES:
		valueCaps = _valueCaps(device, reportType)
		_say(f"--- {label} value caps ({len(valueCaps)}) ---")
		for cap in valueCaps:
			line = _describeValueCap(cap)
			_say(line)
			if cap.UsagePage != BRAILLE_USAGE_PAGE:
				foreign.append(f"{label} value: {line.strip()}")
		buttonCaps = _buttonCaps(device, reportType)
		_say(f"--- {label} button caps ({len(buttonCaps)}) ---")
		for cap in buttonCaps:
			_say(_describeButtonCap(cap))

	_say("--- Accounting ---")
	cellBytes = sum(
		cap.ReportCount * (cap.BitSize // 8 or 1)
		for cap in _valueCaps(device, hidpi.HIDP_REPORT_TYPE.OUTPUT)
		if cap.UsagePage == BRAILLE_USAGE_PAGE
	)
	_say(f"Braille cell payload across all output reports: {cellBytes} bytes")
	_say(f"Largest output report: {caps.OutputReportByteLength} bytes including the report ID")
	if foreign:
		_say(f"*** {len(foreign)} declaration(s) outside the braille page. ***")
		for line in foreign:
			_say(f"  {line}")


def reports() -> None:
	"""Group every output declaration by report ID and size each report.

	This is what identifies the big one. A braille row report is 33 bytes: one report ID and
	thirty-two cells. Anything materially larger is not braille.
	"""
	device = _device()
	if device is None:
		return
	sizes: dict[int, int] = {}
	owners: dict[int, list[str]] = {}
	for cap in _valueCaps(device, hidpi.HIDP_REPORT_TYPE.OUTPUT):
		bits = cap.ReportCount * cap.BitSize
		sizes[cap.ReportID] = sizes.get(cap.ReportID, 0) + bits
		owners.setdefault(cap.ReportID, []).append(
			f"value usage 0x{cap.u1.NotRange.Usage:X} count {cap.ReportCount}",
		)
	for cap in _buttonCaps(device, hidpi.HIDP_REPORT_TYPE.OUTPUT):
		bits = _capBitCount(cap)
		sizes[cap.ReportID] = sizes.get(cap.ReportID, 0) + bits
		owners.setdefault(cap.ReportID, []).append(f"button array of {bits} bits")
	_say("--- Output reports ---")
	for reportId in sorted(sizes):
		payload = (sizes[reportId] + 7) // 8
		marker = "  <-- THIS IS THE BIG ONE" if payload > 64 else ""
		_say(f"  report 0x{reportId:02X}: {sizes[reportId]} bits = {payload} bytes{marker}")
		for owner in owners[reportId]:
			_say(f"      {owner}")
	_say(f"Device declares its largest output report as {device.caps.OutputReportByteLength} bytes with ID.")


def collections() -> None:
	"""Print the link collection tree."""
	device = _device()
	if device is None:
		return
	count = device.caps.NumberLinkCollectionNodes
	if not count:
		_say("No link collection nodes declared.")
		return
	try:
		getNodes = winBindings.hid.dll.HidP_GetLinkCollectionNodes
	except AttributeError:
		_say("HidP_GetLinkCollectionNodes is not available on this system.")
		return
	nodes = (_LinkCollectionNode * count)()
	length = ULONG(count)
	check_HidP_status(getNodes, nodes, byref(length), device._pd)
	_say(f"--- Link collections ({length.value}) ---")
	for index in range(length.value):
		node = nodes[index]
		_say(
			f"  [{index}] page 0x{node.LinkUsagePage:04X} ({_usagePageName(node.LinkUsagePage)}) "
			f"usage 0x{node.LinkUsage:X} type {node.collectionType} "
			f"parent {node.Parent} children {node.NumberOfChildren}",
		)


def interfaces(match: str = "") -> None:
	"""List HID interfaces, to find ones NVDA never opens.

	:param match: case insensitive substring, matched against the whole record. Empty means
		the usual suspects. Pass "*" for everything on the machine.
	"""
	keywords = ["humanware", "monarch", "aph", "braille"] if not match else [match.lower()]
	byUsbId: dict[str, int] = {}
	shown = 0
	for info in hwPortUtils.listHidDevices():
		text = repr(info).lower()
		if match != "*" and not any(keyword in text for keyword in keywords):
			continue
		shown += 1
		usbId = info.get("usbID", "?")
		byUsbId[usbId] = byUsbId.get(usbId, 0) + 1
		_say(f"  {usbId}  {info.get('manufacturer', '')} {info.get('product', '')}")
		_say(f"    hardwareID {info.get('hardwareID', '')}")
	_say(f"--- {shown} HID interface(s) matched ---")
	for usbId, count in byUsbId.items():
		if count > 1:
			_say(f"*** {usbId} presents {count} HID interfaces. NVDA opens one of them. ***")


def features() -> None:
	"""Read every feature report the device declares. Read only, never written."""
	device = _device()
	if device is None:
		return
	reportIds = sorted({cap.ReportID for cap in _valueCaps(device, hidpi.HIDP_REPORT_TYPE.FEATURE)})
	if not reportIds:
		_say("No feature value caps declared.")
		return
	for reportId in reportIds:
		try:
			data = device.getFeature(bytes([reportId]))
			_say(f"  feature 0x{reportId:02X}: {data.hex(' ')}")
		except Exception as error:  # noqa: BLE001
			_say(f"  feature 0x{reportId:02X}: read failed ({error})")


def hunt() -> None:
	"""Run every read only investigation, in order. Touches no pins."""
	_say("=== Geometry ===")
	geometry()
	_say("=== Lattice of the cell path ===")
	lattice()
	_say("=== Descriptor ===")
	descriptor()
	_say("=== Output reports by size ===")
	reports()
	_say("=== Collections ===")
	collections()
	_say("=== HID interfaces ===")
	interfaces()
	_say("=== Feature reports ===")
	features()
	_say("=== Candidate pin report ===")
	pins()
	_say("=== End of hunt. Save this output. ===")


# --- The pin report ---------------------------------------------------------------------


def pinCap() -> Optional[Any]:
	""":return: the output button cap that looks like a pin array, or None.

	Looks for an output button array of many bits. Deliberately loose about the exact count:
	the published pin total is 3,840, but a device may pad to a byte boundary or reserve a
	few, and refusing to recognise it over a handful of bits would be silly.
	"""
	device = _device()
	if device is None:
		return None
	best = None
	for cap in _buttonCaps(device, hidpi.HIDP_REPORT_TYPE.OUTPUT):
		bits = _capBitCount(cap)
		if bits < 256:
			continue
		if best is None or bits > _capBitCount(best):
			best = cap
	return best


def pins() -> None:
	"""Report the candidate pin array without writing anything.

	What to look for in the output:

	- A bit count at or near 3,840. That is one bit per pin on the full 96 by 40 panel, and
	  it is the entire point of this investigation.
	- A usage page of 0x41. On the braille page it is part of the device's advertised
	  braille interface, which is ordinary to drive. On a vendor page it would still be
	  worth pursuing but with more caution.
	- A report ID distinct from the braille rows, 0x31 to 0x38.
	"""
	cap = pinCap()
	if cap is None:
		_say("No output button array large enough to be a pin matrix.")
		_say("If NumberOutputButtonCaps is non zero, run descriptor() and read the caps by hand.")
		return
	bits = _capBitCount(cap)
	_say(f"Candidate pin report 0x{cap.ReportID:02X}: {bits} bits = {(bits + 7) // 8} payload bytes")
	_say(_describeButtonCap(cap))
	if bits == MONARCH_PINS:
		_say(f"*** {bits} is exactly the Monarch's pin count. This is the full 96 by 40 panel. ***")
	elif abs(bits - MONARCH_PINS) < 64:
		_say(f"*** {bits} is within a byte or two of the Monarch's {MONARCH_PINS} pins. ***")
	_say("Bit order is not knowable from the descriptor. Use pinBit() and feel where bits land.")


def _pinWrite(payload: bytes) -> None:
	"""Send a raw payload to the pin report.

	Uses `HidOutputReport` so the buffer is exactly the size Windows demands, and writes the
	payload after the report ID. Deliberately does not go through `HidP_SetUsages`: a
	contiguous button array is a flat bitfield, and building it by hand is the only way to
	control the bit order we are trying to discover.

	:param payload: up to as many bytes as the report holds. Short payloads are zero padded,
		which lowers the remaining pins.
	"""
	cap = pinCap()
	device = _device()
	if cap is None or device is None:
		_say("No pin report to write to.")
		return
	if not isHeld():
		_say("Not held. NVDA's next braille write may land on top of this. Call hold() first.")
	report = HidOutputReport(device, reportID=cap.ReportID)
	size = len(report.data)
	room = size - 1
	data = bytes(payload[:room])
	buffer = bytearray(report.data)
	buffer[1 : 1 + len(data)] = data
	for index in range(1 + len(data), size):
		buffer[index] = 0
	try:
		device.write(bytes(buffer))
	except Exception:
		log.error("tactileGraphicsSpike: the pin report write failed", exc_info=True)
		_say("The pin report write raised. See the log.")


def pinClear() -> None:
	"""Lower every pin through the pin report.

	The safest possible first write: an all zero payload on an output report can only ever
	mean "nothing raised". If the panel goes flat and stays flat, the report is live and
	everything below is worth doing. If nothing happens, the report is either inert in
	terminal mode or gated behind the mode flag in feature 0x21.
	"""
	cap = pinCap()
	if cap is None:
		_say("No pin report found.")
		return
	_pinWrite(b"")
	_say(f"Wrote an all zero payload to report 0x{cap.ReportID:02X}. Is the panel flat?")


def pinFill() -> None:
	"""Raise every pin through the pin report.

	If this raises the whole panel *including* the columns between braille cells, the pin
	report addresses the full grid and the cell path's lattice problem is gone. Feel across
	the middle: solid means every pin, a two up one down rhythm means this is still going
	through cells somehow.
	"""
	cap = pinCap()
	if cap is None:
		_say("No pin report found.")
		return
	bits = _capBitCount(cap)
	_pinWrite(b"\xff" * ((bits + 7) // 8))
	_say(f"Raised all {bits} bits of report 0x{cap.ReportID:02X}. Solid, or ridged?")


def pinBit(index: int) -> None:
	"""Raise exactly one pin, by bit index, and lower everything else.

	This is how the bit order gets established, because a bit index is not a coordinate
	until the hardware says which way it runs. Feel where each of these lands:

	- Bit 0 should be a corner. Which corner tells you the origin.
	- Bit 1 next to it tells you whether the array runs along rows or down columns.
	- Bit 96 lands directly below bit 0 if the order is row major across a 96 pin wide
	  panel. Bit 40 lands directly right of bit 0 if it is column major down a 40 pin tall
	  one. Whichever of those two is true settles the layout.
	- If neither, try bit 4 and bit 8: an order of 480 braille cells rather than raw pins
	  would put bit 4 at the top of the second cell column.

	:param index: which bit to raise, from 0.
	"""
	cap = pinCap()
	if cap is None:
		_say("No pin report found.")
		return
	bits = _capBitCount(cap)
	if not 0 <= index < bits:
		_say(f"Bit index must be between 0 and {bits - 1}.")
		return
	payload = bytearray((bits + 7) // 8)
	payload[index // 8] = 1 << (index % 8)
	_pinWrite(bytes(payload))
	_say(f"Raised bit {index} alone. Where is it?")


def pinBits(indices: list[int]) -> None:
	"""Raise a set of pins by bit index, and lower everything else.

	:param indices: the bits to raise.
	"""
	cap = pinCap()
	if cap is None:
		_say("No pin report found.")
		return
	bits = _capBitCount(cap)
	payload = bytearray((bits + 7) // 8)
	for index in indices:
		if 0 <= index < bits:
			payload[index // 8] |= 1 << (index % 8)
	_pinWrite(bytes(payload))
	_say(f"Raised {len(indices)} bit(s).")


def pinWalk(start: int = 0, count: int = 12, seconds: float = 1.0, cumulative: bool = True) -> None:
	"""Raise pins in sequence so the bit order can be felt, without blocking NVDA.

	Two things were wrong with the first version of this, and both are worth recording
	because they are easy to repeat.

	It ran a loop with `time.sleep` in it. The console executes on NVDA's main thread, so
	that froze NVDA for the whole walk and made the run feel like a hang. Steps are now
	chained through `core.callLater`, which is the same rule `brlMultilineFlowProbe` states
	for the same reason: never hold the thread that has to do the work you are watching.

	It also raised one pin at a time against an otherwise flat panel, which is close to
	unreadable — a single pin is hard to find even when you know roughly where it is. The
	default is now cumulative, so bits accumulate into a growing line you can feel the shape
	and direction of.

	:param start: first bit index.
	:param count: how many bits to walk through.
	:param seconds: how long between steps.
	:param cumulative: leave earlier pins raised, so a line grows. False walks a single pin.
	"""
	cap = pinCap()
	if cap is None:
		_say("No pin report found.")
		return
	bits = _capBitCount(cap)
	end = min(start + count, bits)
	raised: list[int] = []

	def step(index: int) -> None:
		if index >= end:
			_say(f"Walked bits {start} to {end - 1}.")
			return
		if cumulative:
			raised.append(index)
			pinBits(raised)
		else:
			pinBits([index])
		core.callLater(int(seconds * 1000), step, index + 1)

	_say(f"Walking bits {start} to {end - 1}, {seconds} s apart. NVDA stays responsive.")
	step(start)


# --- The confirmed pin layout ---------------------------------------------------------------

PIN_WIDTH = 96
PIN_HEIGHT = 40
"""The Monarch's pin grid, established by touch through `pinBit`."""

PIN_BLOCK_COLS = PIN_WIDTH // CELL_WIDTH
PIN_BLOCK_ROWS = PIN_HEIGHT // CELL_HEIGHT
"""The grid measured in 2 by 4 blocks: 48 across, 10 down, 480 in all, one byte each."""


def pinIndexFor(x: int, y: int) -> int:
	"""Convert a pin coordinate to its bit index in report 0x21.

	One byte covers a 2 wide by 4 tall block and the blocks run in reading order, 48 across
	by 10 down. That much was established by touch and is unchanged. **Within a block the
	byte is an ordinary eight dot braille cell** — dots 1 to 8, `BIT_FOR_CELL_POSITION`,
	the same packing this file already uses for the cell path.

	It was column major here, left column in bits 0 to 3 and right column in bits 4 to 7,
	copied from `dotPad.driver.DpTactileGraphicsBuffer` on the belief that a pin report is a
	graphics buffer rather than a row of cells. Two drawings disproved it:

	- `pinVLine(0)` asks for the left column of every block, which is dots 1, 2, 3 and 7.
	  Column major wrote bits 0 to 3, and the panel raised dots 1, 2, 3 and 4 — a column of
	  p, with a bump to the right at the top of each cell and the bottom pin missing.
	- `pinHLine(0)` asks for the top pin of every block, which is dots 1 and 4. Column major
	  wrote bits 0 and 4, and the panel raised dots 1 and 5 — a row of e, a staircase rather
	  than a line, which is exactly the rhythm that function's docstring says to feel for.

	So the warning on `BIT_FOR_CELL_POSITION` applies to this path too. The DotPad's graphic
	packing is not braille dot numbering, and the Monarch's pin report wants braille dot
	numbering: the firmware raises a byte's pins by dot number whichever report it arrived
	in.

	:param x: pin column, 0 to 95.
	:param y: pin row, 0 to 39.
	:return: the bit index, 0 to 3839.
	"""
	blockIndex = (y // CELL_HEIGHT) * PIN_BLOCK_COLS + (x // CELL_WIDTH)
	return blockIndex * 8 + BIT_FOR_CELL_POSITION[(x % CELL_WIDTH, y % CELL_HEIGHT)]


def pinPositionFor(index: int) -> tuple[int, int]:
	"""Convert a bit index in report 0x21 back to a pin coordinate.

	The inverse of `pinIndexFor`, kept beside it so the two cannot drift apart.

	:param index: the bit index, 0 to 3839.
	:return: pin column and row.
	"""
	blockIndex, bitInBlock = divmod(index, 8)
	blockY, blockX = divmod(blockIndex, PIN_BLOCK_COLS)
	dotX, dotY = POSITION_FOR_CELL_BIT[bitInBlock]
	return (blockX * CELL_WIDTH + dotX, blockY * CELL_HEIGHT + dotY)


def pinLayout() -> None:
	"""Print the layout mapping's own predictions, so they can be checked against fingers.

	Cheap self consistency check as well: every index round trips through `pinIndexFor` and
	`pinPositionFor`, so a mistake in either shows up here rather than as a puzzling drawing.

	Self consistency is all it is, though, and that is worth saying plainly: this printed a
	clean round trip for a packing the panel disagreed with. **The whole first block is
	listed** because the eight bits of one cell are where the two candidate packings differ,
	and reading them out is what tells column major from dot numbering without touching
	anything. See `pinIndexFor`.
	"""
	broken = [i for i in range(PIN_WIDTH * PIN_HEIGHT) if pinIndexFor(*pinPositionFor(i)) != i]
	_say(f"Grid {PIN_WIDTH} by {PIN_HEIGHT}, {PIN_BLOCK_COLS} by {PIN_BLOCK_ROWS} blocks of 2 by 4.")
	for index in (0, 1, 2, 3, 4, 5, 6, 7, 8, 96, 3839):
		x, y = pinPositionFor(index)
		_say(f"  bit {index} -> x {x}, y {y}")
	_say(f"Round trip failures: {len(broken)}." if broken else "Round trip clean for all 3840 bits.")


class PinCanvas(_BufferBase):
	"""A 96 by 40 dot canvas that packs into the 480 byte pin report.

	The real graphics buffer, at full panel resolution, with none of the cell path's lattice
	gaps. Subclasses NVDA's `TactileGraphicsBuffer` so it presents the same seam the DotPad
	driver uses and can move into the add-on unchanged.
	"""

	def __init__(self) -> None:
		self.payload = bytearray(PIN_BLOCK_COLS * PIN_BLOCK_ROWS)
		super().__init__(PIN_WIDTH, PIN_HEIGHT)

	def clear(self) -> None:
		"""Lower every pin in the buffer, without writing to the device."""
		self.payload = bytearray(len(self.payload))

	def setDot(self, x: int, y: int) -> None:
		"""Raise the pin at a coordinate, ignoring anything off the panel."""
		if not (0 <= x < self.width) or not (0 <= y < self.height):
			return
		index = pinIndexFor(x, y)
		self.payload[index // 8] |= 1 << (index % 8)

	def line(self, x0: int, y0: int, x1: int, y1: int) -> None:
		"""Draw a straight line by Bresenham."""
		dx = abs(x1 - x0)
		dy = -abs(y1 - y0)
		stepX = 1 if x0 < x1 else -1
		stepY = 1 if y0 < y1 else -1
		error = dx + dy
		while True:
			self.setDot(x0, y0)
			if x0 == x1 and y0 == y1:
				return
			doubled = 2 * error
			if doubled >= dy:
				error += dy
				x0 += stepX
			if doubled <= dx:
				error += dx
				y0 += stepY

	def rect(self, x: int, y: int, width: int, height: int, filled: bool = False) -> None:
		"""Draw a rectangle, outline or filled."""
		if filled:
			for row in range(y, y + height):
				for col in range(x, x + width):
					self.setDot(col, row)
			return
		self.line(x, y, x + width - 1, y)
		self.line(x, y + height - 1, x + width - 1, y + height - 1)
		self.line(x, y, x, y + height - 1)
		self.line(x + width - 1, y, x + width - 1, y + height - 1)

	def show(self) -> None:
		"""Send the buffer to the pin report."""
		_pinWrite(bytes(self.payload))


def pinCanvas() -> PinCanvas:
	""":return: a blank full panel canvas."""
	return PinCanvas()


def pinHLine(y: int = 0) -> None:
	"""Draw one horizontal line across the full 96 pin width.

	The decisive confirmation of the layout, and of the whole exercise: this is the thing the
	cell path cannot do. If it feels like one unbroken line with no rhythm to it, the mapping
	is right and the spacer columns are genuinely ours.

	:param y: the pin row, 0 to 39.
	"""
	buffer = PinCanvas()
	buffer.line(0, y, PIN_WIDTH - 1, y)
	buffer.show()
	_say(f"Horizontal line at y={y}, all {PIN_WIDTH} pins. Unbroken?")


def pinVLine(x: int = 0) -> None:
	"""Draw one vertical line down the full 40 pin height.

	:param x: the pin column, 0 to 95.
	"""
	buffer = PinCanvas()
	buffer.line(x, 0, x, PIN_HEIGHT - 1)
	buffer.show()
	_say(f"Vertical line at x={x}, all {PIN_HEIGHT} pins.")


def pinBox() -> None:
	"""Border the whole panel with both diagonals through it.

	The general purpose legibility test, and the first thing worth showing anyone else. If
	the corners are square and the diagonals meet in the middle, the mapping is correct in
	both axes at once.
	"""
	buffer = PinCanvas()
	buffer.rect(0, 0, PIN_WIDTH, PIN_HEIGHT)
	buffer.line(0, 0, PIN_WIDTH - 1, PIN_HEIGHT - 1)
	buffer.line(PIN_WIDTH - 1, 0, 0, PIN_HEIGHT - 1)
	buffer.show()
	_say(f"Box with diagonals on the full {PIN_WIDTH} by {PIN_HEIGHT} panel.")


def pinBars(values: Optional[list[int]] = None) -> None:
	"""Draw a bar chart at full panel resolution, as a taste of phase 4.

	:param values: percentages, 0 to 100. A default set is used when omitted.
	"""
	buffer = PinCanvas()
	values = values if values is not None else [20, 55, 80, 35, 95, 60, 15, 70]
	barWidth = max(1, PIN_WIDTH // (len(values) * 2))
	x = 0
	for value in values:
		height = max(1, round(PIN_HEIGHT * min(100, max(0, value)) / 100))
		buffer.rect(x, PIN_HEIGHT - height, barWidth, height, filled=True)
		x += barWidth * 2
		if x >= PIN_WIDTH:
			break
	buffer.show()
	_say(f"Bar chart of {len(values)} values, bars {barWidth} pins wide, on the full panel.")


# --- Text drawn into the pin buffer ----------------------------------------------------------

TEXT_CELL_PITCH = CELL_WIDTH + 1
TEXT_LINE_PITCH = CELL_HEIGHT + 1
"""The pitch the Monarch's own braille layout uses, which the pin grid explains exactly.

96 pin columns is 32 cells at 3 columns each, two dots and a gap. 40 pin rows is 8 lines at
5 rows each, four dots and a gap. That is where terminal mode's 8 rows of 32 comes from: the
two "missing" lines are not withheld, they are the inter-line spacing. Drawing text into the
pin buffer at this pitch reproduces the native layout exactly; drawing at a tighter pitch
buys rows at the cost of legibility.
"""


def pinTextRows(lines: list[str], startRow: int = 0) -> PinCanvas:
	"""Render lines of text into a pin buffer at the native braille pitch.

	Uses NVDA's own `tactile.braille.drawBrailleCells`, which is exactly the DotPad's
	approach: when the only surface is a pin grid, characters are drawn into it rather than
	sent as cells. On the Monarch's pin path that is now the required model rather than a
	curiosity — see `pinMixed` for why.

	:param lines: text, one string per braille line, translated with the configured table.
	:param startRow: which braille line to start at, 0 to 7.
	:return: the buffer, not yet shown, so a caller can draw over it.
	"""
	from tactile.braille import drawBrailleCells

	buffer = PinCanvas()
	maxCells = PIN_WIDTH // TEXT_CELL_PITCH
	for offset, line in enumerate(lines):
		region = TextRegion(line)
		region.update()
		drawBrailleCells(
			buffer,
			0,
			(startRow + offset) * TEXT_LINE_PITCH,
			list(region.brailleCells)[:maxCells],
			hCellPadding=1,
		)
	return buffer


def pinText(*lines: str) -> None:
	"""Show lines of braille text rendered as pins, to check the pitch against the real thing.

	Compare with what NVDA puts up normally: it should be indistinguishable. If it is, the
	pin path can carry text as well as graphics, which is what makes a mixed panel possible
	given that the two report families cannot share the display.

	:param lines: the lines to show.
	"""
	text = list(lines) if lines else ["line one on pins", "line two", "line three"]
	pinTextRows(text).show()
	_say(f"Drew {len(text)} line(s) at {TEXT_CELL_PITCH} by {TEXT_LINE_PITCH} pin pitch.")


def pinMixed(caption: str = "profit by quarter") -> None:
	"""A caption in braille and a chart below it, in **one** pin report.

	This is the phase 2 question re-asked for the pin path, and the answer had to change.
	Releasing a hold with `pinBox` up showed NVDA's next braille write wiping the whole
	panel: the Monarch treats every write as a full display refresh, so pin writes and cell
	writes do not compose, they replace one another.

	So a graphics area cannot sit beside braille that NVDA is still driving. Whatever is on
	the panel has to be composed by us and sent as a single pin report — text drawn in as
	pins, graphics drawn in beside it. Which is precisely the DotPad's situation, and the
	reason NVDA core has `drawBrailleCells` at all.

	:param caption: the top line, translated with the configured table.
	"""
	buffer = pinTextRows([caption])
	values = [25, 45, 70, 90, 60, 80, 40, 55]
	top = TEXT_LINE_PITCH
	height = PIN_HEIGHT - top
	barWidth = max(1, PIN_WIDTH // (len(values) * 2))
	x = 0
	for value in values:
		barHeight = max(1, round(height * value / 100))
		buffer.rect(x, PIN_HEIGHT - barHeight, barWidth, barHeight, filled=True)
		x += barWidth * 2
		if x >= PIN_WIDTH:
			break
	buffer.show()
	_say(f"One report: {caption!r} on the top line, a chart on the {height} rows below.")


# --- Watching what the panel sends back ------------------------------------------------------

ROUTING_USAGE_MIN = 0x402
ROUTING_USAGE_MAX = 0x501
"""The Monarch's 256 routing usages, one per cell of the 8 by 32 braille layout."""

PIN_POSITION_USAGE = 0x401
"""Input usage 0x401, report 0x40, 16 bits, logical 0 to 3840: **the touched pin**.

Established from four touches, each of which came with a report 0x41 naming a routing cell.
The value is a **row major** pin index over the 96 by 40 grid, one based, with 0 meaning
released:

	index = value - 1;  x = index % 96;  y = index // 96

Every sample derived the cell the device itself reported in the paired 0x41, which is
`(y // 5) * 32 + (x // 3)`. Two consecutive braille rows differed by 480, which is 96 by 5,
one line band. And logical max 3840 is the pin count exactly, so 1 to 3840 covers every pin
with 0 left over for "none" — which is the reading that makes the range fit.

Note this is **not** the block packing `pinIndexFor` uses. Output pins are addressed as 2 by
4 blocks of braille dots; touch comes back as a plain raster index. Two different orders on
one device, and using the output one to decode the input gives plausible nonsense — it did
here before the samples were checked against the routing cells.

NVDA reads none of it: its HID driver inspects input values only to find
`NUMBER_OF_BRAILLE_CELLS`, so 0x401 is discarded. That is why a touch reached the log with
no number attached.
"""

TOUCH_CELL_COLS = PIN_WIDTH // TEXT_CELL_PITCH
"""32 touch columns, from the 3 pin cell pitch."""

_inputEvents: deque = deque(maxlen=500)
_emptyReports: int = 0
"""How many reports decoded to nothing and were dropped rather than recorded.

Two hundred empty 0x41s once evicted the touches around them from this log. That was read as
the panel streaming continuously; a later clean run recorded no empties at all, so it is
really hand contact that produces them, not idleness. Counted rather than kept either way.
"""


def _pristineOnReceive(display: Any) -> Optional[Any]:
	"""The callback the device should have when nothing of ours is installed.

	`hidBrailleStandard` constructs its device with `onReceive=self._hidOnReceive`, so the
	driver's own bound method is by definition the correct base. Deriving it rather than
	remembering it is what makes `watchInput` and `unwatchInput` safe to call in any state.

	This exists because the alternative was tried and did real damage. An earlier watcher
	saved whatever was installed and restored that. After a module reload the old wrapper was
	still on the device, untagged, so a second `watchInput` wrapped the wrapper — and because
	the old one called the module global `_originalOnReceive`, which the new one had just
	pointed back at the old one, every arriving report recursed until the stack blew. One
	report filled a 500 entry log with copies of itself, and NVDA's own `_hidOnReceive` was
	never reached at all, so routing keys and buttons silently stopped working.

	Never wrap whatever happens to be installed. Wrap the thing you can name.

	:param display: the braille display driver.
	:return: the driver's own receive callback, or None if it has none.
	"""
	return getattr(display, "_hidOnReceive", None)


def _inputUsageByDataIndex(device: Any) -> dict[int, tuple[int, int, str, str]]:
	"""Map every input data index to the usage it carries.

	Both buttons and values, because the question is which of the two the Monarch answers a
	finger with. Ranges are expanded, so a routing usage can be recovered from the index the
	report actually carries.

	Where each one sits is kept too: the data index and the link collection. NVDA names a key
	by its usage alone, so two keys declaring the same usage in different collections — the
	Monarch's two d-pads both arrive as `dpadUp` — can only be told apart here.

	:param device: the `hwIo.hid.Hid` the driver opened.
	:return: data index to usage page, usage, "button" or "value", and where it is declared.
	"""
	byIndex: dict[int, tuple[int, int, str, str]] = {}

	def where(cap: Any, index: int) -> str:
		return f"data index {index}, collection {cap.LinkCollection} (link usage 0x{cap.LinkUsage:X})"

	for cap in _buttonCaps(device, hidpi.HIDP_REPORT_TYPE.INPUT):
		if cap.IsRange:
			r = cap.u1.Range
			for index in range(r.DataIndexMin, r.DataIndexMax + 1):
				usage = r.UsageMin + (index - r.DataIndexMin)
				byIndex[index] = (cap.UsagePage, usage, "button", where(cap, index))
		else:
			index = cap.u1.NotRange.DataIndex
			byIndex[index] = (cap.UsagePage, cap.u1.NotRange.Usage, "button", where(cap, index))
	for cap in _valueCaps(device, hidpi.HIDP_REPORT_TYPE.INPUT):
		if cap.IsRange:
			r = cap.u1.Range
			for index in range(r.DataIndexMin, r.DataIndexMax + 1):
				usage = r.UsageMin + (index - r.DataIndexMin)
				byIndex[index] = (cap.UsagePage, usage, "value", where(cap, index))
		else:
			index = cap.u1.NotRange.DataIndex
			byIndex[index] = (cap.UsagePage, cap.u1.NotRange.Usage, "value", where(cap, index))
	return byIndex


def touchPositionFor(value: int) -> Optional[tuple[int, int]]:
	"""Convert a report 0x40 usage 0x401 value to the pin the reader touched.

	:param value: the raw value, 0 for released.
	:return: pin column and row, or None when nothing is touched or the value is out of range.
	"""
	if value <= 0:
		return None
	index = value - 1
	y, x = divmod(index, PIN_WIDTH)
	return (x, y) if y < PIN_HEIGHT else None


def touchCellFor(x: int, y: int) -> int:
	"""Convert a touched pin to the routing cell the device reports alongside it.

	The cross check that established the touch mapping: every sample's derived cell equalled
	the one the paired report 0x41 named.

	:param x: pin column.
	:param y: pin row.
	:return: routing cell index, 0 to 255.
	"""
	return (y // TEXT_LINE_PITCH) * TOUCH_CELL_COLS + (x // TEXT_CELL_PITCH)


def _describeInputUsage(usage: int, value: int) -> str:
	""":return: what one decoded input item means, in the terms this investigation cares about.

	Both touch reports are turned into pin coordinates, because when a graphic is on the
	panel a cell number means nothing but a position over the drawing means everything. The
	pin report's own derived cell is printed beside it so that any future disagreement
	between the two is visible immediately rather than assumed away.
	"""
	if ROUTING_USAGE_MIN <= usage <= ROUTING_USAGE_MAX:
		routingIndex = usage - ROUTING_USAGE_MIN
		row, col = divmod(routingIndex, TOUCH_CELL_COLS)
		x, y = col * TEXT_CELL_PITCH, row * TEXT_LINE_PITCH
		return (
			f"routing cell {routingIndex} (row {row}, col {col}) "
			f"-> pin band x {x} to {x + TEXT_CELL_PITCH - 1}, y {y} to {y + TEXT_LINE_PITCH - 1}"
		)
	if usage == PIN_POSITION_USAGE:
		position = touchPositionFor(value)
		if position is None:
			return (
				f"usage 0x401 = {value} -> released"
				if value == 0
				else f"usage 0x401 = {value} -> out of range"
			)
		x, y = position
		return (
			f"usage 0x401 = {value} -> TOUCHED PIN x {x}, y {y} (implies routing cell {touchCellFor(x, y)})"
		)
	return f"usage 0x{usage:X} = {value}"


def watchInput(recordEmpty: bool = False) -> None:
	"""Record the input reports that carry something, decoded, without disturbing NVDA.

	Wraps `device._onReceive` rather than the driver's `_hidOnReceive`, because the driver
	handed its bound method to the device at construction and replacing the attribute on the
	driver would not be seen. The original is always called, so NVDA's own routing, keys and
	gestures carry on exactly as before.

	Reports are recorded rather than printed: this runs on the I/O thread, and printing to
	the console from there interleaves badly. Call `inputReport` to read them.

	Reports that decode to nothing are counted rather than kept, so that a burst of empty
	0x41s cannot bury the touches around it.

	**Reloading this module does not reinstall the wrapper.** `importlib.reload` reuses the
	module object and rebinds its globals, but the function already sitting on the device is
	the old closure and keeps running. It resolves globals by name, so it goes on appending
	to whatever `_inputEvents` currently is, while none of the newer code runs — which reads
	as new behaviour silently not taking effect. So the wrapper is tagged, and this function
	replaces a stale one rather than refusing because a module flag says it is already
	watching. Same lesson as `hold` and `release`: never keep the only copy of recovery state
	in a module global.

	:param recordEmpty: keep the empty reports too, for judging how chatty the panel is.
	"""
	display = _hidDriver()
	device = None if display is None else display._dev
	if device is None or display is None:
		return
	original = _pristineOnReceive(display)
	if original is None:
		_say("This driver has no _hidOnReceive, so there is no known good callback to restore.")
		return
	if device._onReceive is not original:
		_say("Discarding a callback already installed on the device; see _pristineOnReceive.")
	byIndex = _inputUsageByDataIndex(device)

	def wrapped(data: bytes) -> None:
		global _emptyReports
		try:
			decoded = []
			report = HidInputReport(device, data)
			for item in report.getDataItems():
				page, usage, kind, place = byIndex.get(
					item.DataIndex,
					(0, 0, "unknown", f"data index {item.DataIndex}, undeclared"),
				)
				value = item.u1.On if kind == "button" else item.u1.RawValue
				if kind == "button" and not value:
					continue
				decoded.append(f"{_describeInputUsage(usage, int(value))}  [{place}]")
			if decoded or recordEmpty:
				_inputEvents.append(
					{
						"reportId": data[0] if data else None,
						"raw": data.hex(" "),
						"decoded": decoded,
					},
				)
			else:
				_emptyReports += 1
		except Exception:
			log.error("tactileGraphicsSpike: failed to decode an input report", exc_info=True)
		original(data)

	wrapped._tgSpikeWatcher = True
	wrapped._tgSpikeOriginal = original
	device._onReceive = wrapped
	_say("Watching input. Touch the panel, then call inputReport().")


def isWatchingInput() -> bool:
	""":return: whether a watcher is installed, read from the device rather than a flag."""
	device = _device()
	return device is not None and getattr(device._onReceive, "_tgSpikeWatcher", False)


def unwatchInput() -> None:
	"""Restore the driver's own receive callback. Always safe, whatever is installed.

	Sets the device back to `display._hidOnReceive` rather than to a remembered value, so it
	repairs a stacked or recursive chain as readily as it removes a clean wrapper.
	"""
	display = _hidDriver()
	device = None if display is None else display._dev
	if device is None or display is None:
		return
	original = _pristineOnReceive(display)
	if original is None:
		_say("This driver has no _hidOnReceive to restore.")
		return
	if device._onReceive is original:
		_say("Not watching input; the driver's own callback is already in place.")
		return
	device._onReceive = original
	_say("Restored the driver's own receive callback.")


def inputReport(count: int = 20) -> None:
	"""Print the input reports recorded so far, most recent last.

	Both touch reports appear, in pairs: a 0x40 carrying the touched pin, then a 0x41 carrying
	the routing cell that pin falls in, then both again as zero on release. The 0x40 is the
	one worth building on — it is finer, and the 0x41 is derivable from it.

	One thing not to read meaning into: a 0x40 report's bytes past the third are whatever the
	previous read left in the buffer, so a released 0x40 often carries the trailing bytes of
	the 0x41 before it. Only the declared field matters.

	:param count: how many of the most recent reports to print.
	"""
	if not _inputEvents:
		_say("No input carrying data recorded. Call watchInput() first, then touch the panel.")
		_say(f"{_emptyReports} empty report(s) seen, so the panel is talking; nothing was touched.")
		return
	for event in list(_inputEvents)[-count:]:
		reportId = event["reportId"]
		_say(f"report 0x{reportId:02X}: {event['raw']}" if reportId is not None else f"raw {event['raw']}")
		for line in event["decoded"]:
			_say(f"    {line}")
	_say(f"{len(_inputEvents)} report(s) with data recorded, {_emptyReports} empty one(s) dropped.")
	if not isWatchingInput():
		_say("No watcher is installed. These records are stale; call watchInput() again.")


def clearInput() -> None:
	"""Forget the recorded input reports, so the next touch is easy to find."""
	global _emptyReports
	_inputEvents.clear()
	_emptyReports = 0
	_say("Input log cleared.")


# --- Holding the display ------------------------------------------------------------------


def isHeld() -> bool:
	""":return: whether the handler is currently suppressed, read from the handler itself.

	The handler is the source of truth rather than a module flag, because this module gets
	reloaded during a session. `hold` shadows the bound method `_writeCells` with an instance
	attribute, so the presence of that attribute in the handler's own `__dict__` is the
	condition, and it survives a reload that would reset any flag we kept here.
	"""
	handler = braille.handler
	return handler is not None and "_writeCells" in handler.__dict__


def hold() -> None:
	"""Stop NVDA writing to the display, so a pattern stays up long enough to feel.

	`braille.handler._writeCells` is the single choke point: update, message and the cursor
	blink all reach the hardware through it. Safe to call twice. **Call `release` when
	finished**, or braille stays dead until NVDA restarts.
	"""
	handler = braille.handler
	if handler is None:
		_say("No braille handler.")
		return
	if isHeld():
		_say("Already held.")
		return
	handler._writeCells = lambda cells: None
	_say("Held. NVDA's braille output is suppressed until release() is called.")


def release() -> None:
	"""Give the display back to NVDA. Safe to call when nothing is held.

	Deletes the instance attribute rather than restoring a saved reference, which uncovers
	the bound method on the class. That makes this work even after the module has been
	reloaded, and even if `hold` was called by a previous incarnation of it: there is nothing
	to remember, so there is nothing a reload can lose.
	"""
	handler = braille.handler
	if handler is None:
		_say("No braille handler.")
		return
	if not isHeld():
		_say("Nothing held.")
		return
	del handler.__dict__["_writeCells"]
	try:
		handler.update()
	except Exception:
		log.error("tactileGraphicsSpike: the handler raised while resuming", exc_info=True)
	_say("Released. NVDA is driving the display again.")


# --- The cell path, which works whatever the pin report turns out to do --------------------


def raw(cells: list[int]) -> None:
	"""Write a raw cell array straight to the driver, padded or truncated to fit.

	Bypasses `braille.handler`, so no normalisation, no cursor and no translation happens
	between this list and the pins.

	:param cells: dot patterns, one per cell, row major.
	"""
	display = _display()
	if display is None:
		return
	if not isHeld():
		_say("Not held: NVDA will overwrite this almost immediately. Call hold() first.")
	total = display.numCells
	cells = list(cells[:total])
	cells.extend([0] * (total - len(cells)))
	try:
		display.display(cells)
	except Exception:
		log.error("tactileGraphicsSpike: the driver raised while displaying", exc_info=True)
		_say("The driver raised while displaying. See the log.")


def fill(value: int = 0xFF) -> None:
	"""Fill every cell with one byte.

	:param value: the dot pattern, 0 to 255.
	"""
	display = _display()
	if display is None:
		return
	raw([value & 0xFF] * display.numCells)
	_say(f"Filled {display.numCells} cells with 0x{value & 0xFF:02X}.")


def clear() -> None:
	"""Lower every pin the cell path can reach."""
	fill(0x00)


def dotOrder() -> None:
	"""Put one dot in each of the first eight cells, in dot number order.

	Cell 0 gets dot 1, cell 1 gets dot 2, on to cell 7 with dot 8, everything else blank.

	What to feel on the top row: cells 0, 1, 2 have a single dot descending the left column
	and cell 6 has the fourth below them, which is dots 1, 2, 3, 7. Cells 3, 4, 5 descend
	the right column with cell 7 below, which is dots 4, 5, 6, 8.

	If that is what is there, arbitrary bytes reach the pins unfiltered and
	`BIT_FOR_CELL_POSITION` is correct.
	"""
	display = _display()
	if display is None:
		return
	cells = [0] * display.numCells
	for bit in range(min(8, display.numCols)):
		cells[bit] = 1 << bit
	raw(cells)
	_say("Wrote dots 1 to 8 into cells 0 to 7 of row 0. Feel the staircase.")


def spacers(step: int = 0) -> None:
	"""Are the pin columns between cells reachable through the *cell* path?

	Call `spacers(1)`, `spacers(2)`, `spacers(3)`, feeling the panel between each.

	Step 1, whole panel at 0xFF. Uniformly solid means the Monarch fills its spacer columns
	when neighbouring dots are raised. A ridge and groove rhythm, two up and one down, means
	the spacers are dead on this path. If the pin report works, this stops mattering.

	Step 2, a four cell wide vertical bar. Feel its edges for bleed past the last cell.

	Step 3, alternating full and empty cells. How wide do the gaps feel, one column or
	three?

	:param step: 1, 2 or 3. Zero prints the procedure.
	"""
	display = _display()
	if display is None:
		return
	if step == 0:
		_say("Call spacers(1), then spacers(2), then spacers(3), feeling the panel between each.")
		return
	if step == 1:
		fill(0xFF)
		_say("Step 1: whole panel raised. Solid, or ridged two up one down?")
	elif step == 2:
		cells = [0] * display.numCells
		start = max(0, display.numCols // 2 - 2)
		for row in range(display.numRows):
			for col in range(start, min(start + 4, display.numCols)):
				cells[row * display.numCols + col] = 0xFF
		raw(cells)
		_say(f"Step 2: cells {start} to {start + 3} on every row. Feel the vertical edges.")
	elif step == 3:
		raw([0xFF if (index % display.numCols) % 2 == 0 else 0x00 for index in range(display.numCells)])
		_say("Step 3: alternating full and empty cells. How wide do the gaps feel?")
	else:
		_say("Step must be 1, 2 or 3.")


def checker() -> None:
	"""Alternate 0x55 and 0xAA across the panel, for feeling dot level addressing."""
	display = _display()
	if display is None:
		return
	raw([0x55 if index % 2 == 0 else 0xAA for index in range(display.numCells)])
	_say("Wrote an interlocking checker. It should feel like a fine regular texture.")


# --- A canvas over the cell path ------------------------------------------------------------


class CellCanvas(_BufferBase):
	"""A dot canvas that packs into braille cells in the correct dot order.

	This is `CellTactileGraphicsBuffer` from phase 1 of the plan, prototyped here so the
	packing can be confirmed by touch before it is committed and unit tested.
	"""

	def __init__(self, numRows: int, numCols: int):
		"""
		:param numRows: rows of cells.
		:param numCols: cells per row.
		"""
		self.numRows = numRows
		self.numCols = numCols
		self.cells = [0] * (numRows * numCols)
		super().__init__(numCols * CELL_WIDTH, numRows * CELL_HEIGHT)

	def setDot(self, x: int, y: int) -> None:
		"""Raise the pin at a canvas position, ignoring anything off the canvas."""
		if not (0 <= x < self.width) or not (0 <= y < self.height):
			return
		cellIndex = (y // CELL_HEIGHT) * self.numCols + (x // CELL_WIDTH)
		self.cells[cellIndex] |= 1 << BIT_FOR_CELL_POSITION[(x % CELL_WIDTH, y % CELL_HEIGHT)]

	def line(self, x0: int, y0: int, x1: int, y1: int) -> None:
		"""Draw a straight line by Bresenham."""
		dx = abs(x1 - x0)
		dy = -abs(y1 - y0)
		stepX = 1 if x0 < x1 else -1
		stepY = 1 if y0 < y1 else -1
		error = dx + dy
		while True:
			self.setDot(x0, y0)
			if x0 == x1 and y0 == y1:
				return
			doubled = 2 * error
			if doubled >= dy:
				error += dy
				x0 += stepX
			if doubled <= dx:
				error += dx
				y0 += stepY

	def rect(self, x: int, y: int, width: int, height: int, filled: bool = False) -> None:
		"""Draw a rectangle, outline or filled."""
		if filled:
			for row in range(y, y + height):
				for col in range(x, x + width):
					self.setDot(col, row)
			return
		self.line(x, y, x + width - 1, y)
		self.line(x, y + height - 1, x + width - 1, y + height - 1)
		self.line(x, y, x, y + height - 1)
		self.line(x + width - 1, y, x + width - 1, y + height - 1)

	def show(self) -> None:
		"""Send the canvas to the display."""
		raw(list(self.cells))


def canvas() -> Optional[CellCanvas]:
	""":return: a blank canvas the size of the current display, or None."""
	display = _display()
	if display is None:
		return None
	return CellCanvas(display.numRows, display.numCols)


def hLine(y: int = 4) -> None:
	"""Draw one horizontal line. Continuous, or broken every two dots?

	:param y: the canvas row.
	"""
	buffer = canvas()
	if buffer is None:
		return
	buffer.line(0, y, buffer.width - 1, y)
	buffer.show()
	_say(f"Horizontal line at y={y}. Continuous, or dashed two on one off?")


def vLine(x: int = 16) -> None:
	"""Draw one vertical line, which should be solid whatever the spacers do.

	:param x: the canvas column.
	"""
	buffer = canvas()
	if buffer is None:
		return
	buffer.line(x, 0, x, buffer.height - 1)
	buffer.show()
	_say(f"Vertical line at x={x}. This should feel solid.")


def box() -> None:
	"""Draw a border round the whole canvas with both diagonals through it."""
	buffer = canvas()
	if buffer is None:
		return
	buffer.rect(0, 0, buffer.width, buffer.height)
	buffer.line(0, 0, buffer.width - 1, buffer.height - 1)
	buffer.line(buffer.width - 1, 0, 0, buffer.height - 1)
	buffer.show()
	_say(f"Box with diagonals on a {buffer.width} by {buffer.height} canvas.")


def bars(values: Optional[list[int]] = None) -> None:
	"""Draw a bar chart, as a taste of what phase 4 would produce.

	:param values: percentages, 0 to 100. A default set is used when omitted.
	"""
	buffer = canvas()
	if buffer is None:
		return
	values = values if values is not None else [20, 55, 80, 35, 95, 60, 15, 70]
	barWidth = max(1, buffer.width // (len(values) * 2))
	x = 0
	for value in values:
		height = max(1, round(buffer.height * min(100, max(0, value)) / 100))
		buffer.rect(x, buffer.height - height, barWidth, height, filled=True)
		x += barWidth * 2
		if x >= buffer.width:
			break
	buffer.show()
	_say(f"Bar chart of {len(values)} values, bars {barWidth} dots wide.")


def mixed(text: str = "profit by quarter") -> None:
	"""Put translated braille on the top row and a drawing underneath, in one write.

	Phase 2's question answered at the cell level: one cell array, so a row of characters
	and a row of pixels cost the same and need no mode switch.

	:param text: the caption, translated with NVDA's configured output table.
	"""
	display = _display()
	if display is None:
		return
	if display.numRows < 2:
		_say("Needs at least two rows.")
		return
	region = TextRegion(text)
	region.update()
	caption = list(region.brailleCells)[: display.numCols]
	caption.extend([0] * (display.numCols - len(caption)))
	buffer = CellCanvas(display.numRows - 1, display.numCols)
	values = [25, 45, 70, 90, 60, 80, 40, 55]
	barWidth = max(1, buffer.width // (len(values) * 2))
	x = 0
	for value in values:
		height = max(1, round(buffer.height * value / 100))
		buffer.rect(x, buffer.height - height, barWidth, height, filled=True)
		x += barWidth * 2
		if x >= buffer.width:
			break
	raw(caption + list(buffer.cells))
	_say(f"Row 0 is the caption {text!r}. The rest is a drawing.")


# --- Timing -------------------------------------------------------------------------------


def timing(count: int = 10) -> None:
	"""Measure how long a full panel write takes, and judge the mechanism by ear.

	The number printed is the time to hand a report to the driver, not the time for pins to
	settle. The pins are the part that matters and the only instrument for them is your hand
	and your ear.

	:param count: how many alternating writes to make.
	"""
	display = _display()
	if display is None:
		return
	if not isHeld():
		_say("Call hold() first, or NVDA's own writes will be mixed into the measurement.")
		return
	patterns = ([0xFF] * display.numCells, [0x00] * display.numCells)
	times: list[float] = []
	for index in range(count):
		start = time.perf_counter()
		display.display(list(patterns[index % 2]))
		times.append(time.perf_counter() - start)
		time.sleep(0.5)
	mean = sum(times) / len(times)
	_say(f"{count} writes: mean {mean * 1000:.1f} ms, max {max(times) * 1000:.1f} ms to hand off.")
	_say("Now say what the mechanism sounded and felt like.")


def summary() -> None:
	"""Print what this module can do and where each answer comes from."""
	_say("Read only: hunt(), which includes button caps, reports() and pins().")
	_say("Pins, raw: pinClear(), pinFill(), pinBit(n), pinWalk(), pinLayout().")
	_say("Pins, drawing: pinHLine(), pinVLine(), pinBox(), pinBars().")
	_say("Pins, with text: pinText('a', 'b'), pinMixed('caption').")
	_say("Input: watchInput(), touch the panel, inputReport(), clearInput(), unwatchInput().")
	_say("The cell path fallback: dotOrder(), spacers(1..3), checker(), hLine(), vLine(), box().")
	_say("Mechanism: timing().")
	_say(f"Currently held: {isHeld()}. Call release() before closing the console.")
