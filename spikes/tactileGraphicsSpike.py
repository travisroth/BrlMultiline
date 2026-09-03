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

The first run of `hunt` on real hardware found something the plan did not expect: the
device declares an output report of 481 bytes, when eight rows of thirty-two cells need
only 33. Four hundred and eighty payload bytes is 3,840 bits, which is exactly the
Monarch's pin count, and exactly 48 cell columns by 10 cell rows of the full 96 by 40
panel. The only output declaration not accounted for by the braille rows is a single output
*button* cap, which is how a pin matrix would be declared: one bit per pin.

That is the whole 47 percent the plan wrote off, sitting on the interface NVDA already has
open. NVDA cannot see it because `hidBrailleStandard._findCellValueCaps` keeps only value
caps whose link usage is `BRAILLE_ROW`, and NVDA reads button caps for input only.

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
from ctypes import byref
from ctypes.wintypes import ULONG, USHORT
from typing import Any, Optional

import braille
import hidpi
import hwPortUtils
import winBindings.hid
from braille.regions.base import TextRegion
from hwIo.hid import HidOutputReport, check_HidP_status
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
is the hardware check that this table is the right one.
"""

CELL_WIDTH = 2
CELL_HEIGHT = 4
PIN_COLUMNS_PER_CELL = 3
"""Two dot columns and one spacer, per the Monarch's published 96 by 40 pin geometry."""

MONARCH_PINS = 3840
"""The published pin count, and the number this device's descriptor keeps referring to."""

BRAILLE_USAGE_PAGE = 0x41

_held: bool = False
_originalWriteCells = None
"""`braille.handler._writeCells` as it was before `hold` replaced it."""


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


def _device() -> Optional[Any]:
	""":return: the `hwIo.hid.Hid` the current driver opened, or None."""
	display = _display()
	if display is None:
		return None
	device = getattr(display, "_dev", None)
	if device is None:
		_say(f"{display.name} is not a HID driver, or does not keep its device as _dev.")
		return None
	return device


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


def _capUsageCount(cap: Any) -> int:
	""":return: how many usages a cap covers, which for a button cap is how many bits."""
	if cap.IsRange:
		return cap.u1.Range.UsageMax - cap.u1.Range.UsageMin + 1
	return 1


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
	bits = _capUsageCount(cap)
	return (
		f"  report 0x{cap.ReportID:02X} page 0x{cap.UsagePage:04X} ({_usagePageName(cap.UsagePage)}) "
		f"{usageText} in collection {cap.LinkCollection} "
		f"(link page 0x{cap.LinkUsagePage:04X} usage 0x{cap.LinkUsage:X}), "
		f"{bits} usages = {bits} bits = {bits / 8:.0f} bytes, reportCount {cap.ReportCount}, {indexText}"
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
		bits = _capUsageCount(cap)
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
		bits = _capUsageCount(cap)
		if bits < 256:
			continue
		if best is None or bits > _capUsageCount(best):
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
	bits = _capUsageCount(cap)
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
	if not _held:
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
	bits = _capUsageCount(cap)
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
	bits = _capUsageCount(cap)
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
	bits = _capUsageCount(cap)
	payload = bytearray((bits + 7) // 8)
	for index in indices:
		if 0 <= index < bits:
			payload[index // 8] |= 1 << (index % 8)
	_pinWrite(bytes(payload))
	_say(f"Raised {len(indices)} bit(s).")


def pinWalk(start: int = 0, count: int = 12, seconds: float = 1.5) -> None:
	"""Raise single pins in sequence, pausing between, so the path can be felt.

	Keep a finger resting near the origin and feel which way the pin travels. That answers
	the bit order question faster than any number of single writes.

	:param start: first bit index.
	:param count: how many bits to walk through.
	:param seconds: how long to hold each one.
	"""
	cap = pinCap()
	if cap is None:
		_say("No pin report found.")
		return
	bits = _capUsageCount(cap)
	for index in range(start, min(start + count, bits)):
		payload = bytearray((bits + 7) // 8)
		payload[index // 8] = 1 << (index % 8)
		_pinWrite(bytes(payload))
		time.sleep(seconds)
	_say(f"Walked bits {start} to {min(start + count, bits) - 1}.")


def pinRow(index: int, width: int = 96) -> None:
	"""Raise one contiguous run of pins, on the assumption of a row major layout.

	Only meaningful once `pinBit` has established that the order really is row major. If it
	is, this draws a solid horizontal line, which is the thing the cell path cannot do.

	:param index: which row, from 0.
	:param width: pins per row, 96 on the Monarch.
	"""
	pinBits(list(range(index * width, (index + 1) * width)))
	_say(f"Raised row {index}, {width} pins. Is it a solid unbroken line?")


# --- Holding the display ------------------------------------------------------------------


def hold() -> None:
	"""Stop NVDA writing to the display, so a pattern stays up long enough to feel.

	`braille.handler._writeCells` is the single choke point: update, message and the cursor
	blink all reach the hardware through it. Safe to call twice. **Call `release` when
	finished**, or braille stays dead until NVDA restarts.
	"""
	global _held, _originalWriteCells
	handler = braille.handler
	if handler is None:
		_say("No braille handler.")
		return
	if _held:
		_say("Already held.")
		return
	_originalWriteCells = handler._writeCells
	handler._writeCells = lambda cells: None
	_held = True
	_say("Held. NVDA's braille output is suppressed until release() is called.")


def release() -> None:
	"""Give the display back to NVDA. Safe to call when nothing is held."""
	global _held, _originalWriteCells
	handler = braille.handler
	if not _held or handler is None:
		_say("Nothing held.")
		return
	handler._writeCells = _originalWriteCells
	_originalWriteCells = None
	_held = False
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
	if not _held:
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

try:
	from tactile import TactileGraphicsBuffer as _BufferBase
except ImportError:  # NVDA older than 2025.1.

	class _BufferBase:  # type: ignore[no-redef]
		def __init__(self, width: int, height: int):
			self.width = width
			self.height = height


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
	if not _held:
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
	"""Print the phase 0 questions and where each answer comes from."""
	_say("Read only: hunt(), which now includes button caps, reports() and pins().")
	_say("The pin report: pinClear(), pinFill(), pinBit(n), pinWalk(), pinRow(n).")
	_say("The cell path: dotOrder(), spacers(1..3), checker(), hLine(), vLine(), box().")
	_say("Mechanism: timing(). Mixing: mixed().")
	_say(f"Currently held: {_held}. Call release() before closing the console.")
