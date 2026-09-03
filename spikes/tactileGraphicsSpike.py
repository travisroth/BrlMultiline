# BrlMultiline: phase 0 spike for tactile graphics over HID braille.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

r"""Console spike: how much of the Monarch's pin grid can we actually reach?

Everything in `docs/design/tactile-graphics-plan.md` rests on questions that cannot be
settled by reading source. The HID Braille Display usage page has no graphics usage, so the
only canvas we know about is the cell array, and the cell array is laid out for braille:
the Monarch spends three pin columns per cell, two dot columns and one spacer, so a third
of its 96 columns may be unreachable. Whether they really are unreachable, and whether some
other path exists, is what this module is for.

It is not part of the add-on and is not packaged: `spikes/` sits outside `addon/`, so the
build never sees it. Import it from the NVDA Python console (NVDA+control+z) with a Monarch
connected in terminal mode and selected as NVDA's braille display.

	import sys; sys.path.insert(0, r"C:\code\BrlMultiline\spikes")
	import tactileGraphicsSpike as spike
	spike.geometry()
	spike.hunt()

`hunt` is the descriptor forensics: it answers question 4 without touching the pins, and it
is the only part of this file that could find a way to the other 47 percent. Read its output
before doing anything else, because if it turns up a vendor collection the rest of the plan
changes shape.

Then the pins. **`hold` freezes NVDA's braille output** so that a pattern stays up long
enough to feel. Nothing NVDA wants to show reaches the display until `release`.

	spike.hold()
	spike.dotOrder()          # question 1, and confirms the bit numbering
	spike.spacers()           # question 2, the important one
	spike.checker()
	spike.hLine(8); spike.vLine(16); spike.box()
	spike.timing()
	spike.mixed()
	spike.release()

`release` is safe to call at any point and safe to call twice. Call it before closing the
console, and call it if anything raises; a held handler is a display that has stopped
updating.

What each phase 0 question looks like when answered:

- **Does it render arbitrary patterns?** `dotOrder` puts one dot in each of the first eight
  cells of row 0, in dot number order. If you feel dot 1 in cell 0, dot 2 in cell 1, and so
  on through dot 8, then arbitrary bytes reach the pins unfiltered and the bit numbering is
  standard braille. Anything else, and the plan stops here.
- **Are the spacer columns filled?** `spacers`. See its own docstring; it is a three step
  procedure with a different thing to feel at each step, and it is the single answer the
  rest of the design depends on.
- **What is the real geometry?** `geometry`, and `lattice` for what that geometry means in
  pins.
- **What does the descriptor contain?** `hunt`.

A note on what this file will not do. It reads feature reports, which is safe, and it never
writes one, and it never writes to a usage or collection outside the braille page. If
`hunt` finds a vendor collection, the next step is a conversation with Humanware about what
it is for, not a blind write to an eighteen thousand dollar device. Fuzzing an actuator
array is not research.
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
from hwIo.hid import check_HidP_status
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

Derived from NVDA's own table rather than transcribed, so that it cannot drift from core.
Note that this is **not** the packing `dotPad.driver.DpTactileGraphicsBuffer` uses, which
puts the left column in bits 0 to 3 and the right column in bits 4 to 7. That layout is not
braille dot numbering and would render scrambled through a HID `8 Dot Braille Cell` value.
`dotOrder` is the hardware check that this table is the right one.
"""

CELL_WIDTH = 2
CELL_HEIGHT = 4
PIN_COLUMNS_PER_CELL = 3
"""Two dot columns and one spacer, per the Monarch's published 96 by 40 pin geometry.

The spacer is the whole question. `spacers` is the test.
"""

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


def _usagePageName(page: int) -> str:
	""":return: a readable name for a usage page, so that anything unexpected stands out."""
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
	"""Report what NVDA thinks the display is, and what that means as a canvas.

	The plan assumes 8 rows of 32, 256 cells, from a panel that physically has 10 lines. If
	this reports 10 rows, terminal mode is giving us more than expected and the canvas is a
	quarter larger than the plan says. If it reports something else again, every dimension
	in the plan needs revisiting before anything is built.
	"""
	display = _display()
	if display is None:
		return
	handler = braille.handler
	dimensions = handler.displayDimensions
	_say(f"Driver: {display.name} ({display.description})")
	_say(f"Driver geometry: {display.numRows} rows of {display.numCols}, {display.numCells} cells")
	_say(f"Handler geometry: {dimensions.numRows} rows of {dimensions.numCols}")
	_say(f"Thread safe: {display.isThreadSafe}. Wants acknowledgements: {display.receivesAckPackets}")
	width = display.numCols * CELL_WIDTH
	height = display.numRows * CELL_HEIGHT
	_say(f"Canvas through cells: {width} by {height} dots, {width * height} pins addressable")
	if display.numRows > 1:
		_say("Multi row, so a flat cell array is a bitmap in scanline order.")
	else:
		_say("Single row. Drawing is possible but there is only one cell of height to draw in.")


def lattice() -> None:
	"""Show which physical pin columns the cell canvas can and cannot reach.

	Assumes the published three pin columns per cell. Confirm the assumption with `spacers`
	before trusting this; it is arithmetic, not a measurement.
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
	percent = 100 * len(reachable) // physicalWidth
	_say(f"Horizontal reach: {percent} percent of columns, gapped every {CELL_WIDTH} dots.")


# --- Question 4: descriptor forensics -------------------------------------------------


class _LinkCollectionNode(ctypes.Structure):
	"""`HIDP_LINK_COLLECTION_NODE`, which NVDA does not bind.

	Bound here because the collection tree is where a vendor defined top level collection
	would be visible, and that is the one thing in this file that could change the plan.
	"""

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


def _valueCaps(device: Any, reportType: hidpi.HIDP_REPORT_TYPE) -> list[Any]:
	"""Read every value cap of one report type, including types NVDA never asks for.

	:param device: the `hwIo.hid.Hid` the driver opened.
	:param reportType: input, output or feature.
	:return: the caps, or an empty list if the device declares none.
	"""
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
	check_HidP_status(
		winBindings.hid.HidP_GetValueCaps,
		reportType,
		capsList,
		byref(number),
		device._pd,
	)
	return list(capsList[: number.value])


def _describeValueCap(cap: Any) -> str:
	""":return: one line describing a value cap, naming anything off the braille page."""
	if cap.IsRange:
		usageText = f"usages 0x{cap.u1.Range.UsageMin:X} to 0x{cap.u1.Range.UsageMax:X}"
		usageName = "(range)"
	else:
		usage = cap.u1.NotRange.Usage
		usageText = f"usage 0x{usage:X}"
		usageName = _brailleUsageName(usage) if cap.UsagePage == BRAILLE_USAGE_PAGE else ""
	return (
		f"  report 0x{cap.ReportID:02X} page 0x{cap.UsagePage:04X} ({_usagePageName(cap.UsagePage)}) "
		f"{usageText} {usageName} "
		f"in collection {cap.LinkCollection} "
		f"(link page 0x{cap.LinkUsagePage:04X} usage 0x{cap.LinkUsage:X}), "
		f"count {cap.ReportCount} of {cap.BitSize} bits, "
		f"logical {cap.LogiclMin} to {cap.LogicalMax}"
	)


def descriptor() -> None:
	"""Dump every value cap the device advertises, not only the ones NVDA uses.

	This is the most likely hiding place for a graphics path, and the reason is specific.
	`hidBrailleStandard._findCellValueCaps` keeps only output value caps whose *link usage*
	is `BRAILLE_ROW` and whose usage is a six or eight dot cell. A value array on a vendor
	page, or on the braille page under some other collection, is silently invisible to NVDA
	and would never appear in any log. It would appear here.

	Two things to look for in the output:

	1. Any line whose usage page is not 0x41. A vendor defined page carrying a large value
	   array is exactly what a pin matrix interface would look like.
	2. Any braille page output array that is not one of the rows the driver counted. In
	   particular, note that `hidBrailleStandard` refuses a device whose rows have differing
	   report counts, so a wide graphics row cannot coexist with the braille rows under the
	   same link usage. It could perfectly well exist under a different one.

	Also worth reading is the accounting at the end: if the output report is materially
	larger than the cells need, something else is meant to travel in it.
	"""
	display = _display()
	if display is None:
		return
	device = getattr(display, "_dev", None)
	if device is None:
		_say(f"{display.name} is not a HID driver, or does not keep its device as _dev.")
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
	for label, reportType in (
		("OUTPUT", hidpi.HIDP_REPORT_TYPE.OUTPUT),
		("INPUT", hidpi.HIDP_REPORT_TYPE.INPUT),
		("FEATURE", hidpi.HIDP_REPORT_TYPE.FEATURE),
	):
		valueCaps = _valueCaps(device, reportType)
		_say(f"--- {label} value caps ({len(valueCaps)}) ---")
		for cap in valueCaps:
			line = _describeValueCap(cap)
			_say(line)
			if cap.UsagePage != BRAILLE_USAGE_PAGE:
				foreign.append(f"{label}: {line.strip()}")

	outputCaps = _valueCaps(device, hidpi.HIDP_REPORT_TYPE.OUTPUT)
	cellBytes = sum(
		cap.ReportCount * (cap.BitSize // 8 or 1) for cap in outputCaps if cap.UsagePage == BRAILLE_USAGE_PAGE
	)
	_say("--- Accounting ---")
	_say(f"Bytes of braille cell payload across all output reports: {cellBytes}")
	_say(f"Output report byte length: {caps.OutputReportByteLength} (one of which is the report ID)")
	if foreign:
		_say(f"*** {len(foreign)} cap(s) outside the braille page. This is the interesting result. ***")
		for line in foreign:
			_say(f"  {line}")
	else:
		_say("Nothing outside the braille page on this interface. If there is a graphics path,")
		_say("it is not on the interface NVDA opened. Run `interfaces` next.")


def collections() -> None:
	"""Print the link collection tree, which shows top level collections NVDA ignores.

	A device may declare several application collections. NVDA opens one interface and reads
	the braille one; a sibling collection for something else would show here as a node whose
	parent is 0 and whose usage page is not 0x41.
	"""
	display = _display()
	if display is None:
		return
	device = getattr(display, "_dev", None)
	if device is None:
		_say(f"{display.name} is not a HID driver.")
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
		marker = (
			"  <-- TOP LEVEL, NOT BRAILLE"
			if node.Parent == 0 and node.LinkUsagePage != BRAILLE_USAGE_PAGE
			else ""
		)
		_say(
			f"  [{index}] page 0x{node.LinkUsagePage:04X} ({_usagePageName(node.LinkUsagePage)}) "
			f"usage 0x{node.LinkUsage:X} type {node.collectionType} "
			f"parent {node.Parent} children {node.NumberOfChildren}{marker}",
		)


def interfaces(match: str = "") -> None:
	"""List HID interfaces on the system, to find ones NVDA never opens.

	This is the strongest lead available without vendor cooperation. NVDA opens exactly one
	HID interface, the one whose top level usage page is 0x41. A composite device may
	present several, and a graphics interface would be a separate one carrying a vendor
	usage page. The Wing It application proves some full resolution path into this hardware
	exists; if it is USB HID rather than Bluetooth, it is visible here.

	:param match: a case insensitive substring to filter on, matched against the whole
		record. Empty means the usual suspects. Pass "*" for everything on the machine.
	"""
	keywords = ["humanware", "monarch", "aph", "braille"] if not match else [match.lower()]
	shown = 0
	byUsbId: dict[str, int] = {}
	for info in hwPortUtils.listHidDevices():
		text = repr(info).lower()
		if match != "*" and not any(keyword in text for keyword in keywords):
			continue
		shown += 1
		usbId = info.get("usbID", "?")
		byUsbId[usbId] = byUsbId.get(usbId, 0) + 1
		_say(f"  {usbId}  {info.get('manufacturer', '')} {info.get('product', '')}")
		_say(f"    hardwareID {info.get('hardwareID', '')}")
		_say(f"    path {info.get('devicePath', '')}")
	_say(f"--- {shown} HID interface(s) matched ---")
	for usbId, count in byUsbId.items():
		if count > 1:
			_say(f"*** {usbId} presents {count} HID interfaces. NVDA opens one of them. ***")


def serialPorts() -> None:
	"""List serial ports, in case the device also presents a CDC interface.

	The DotPad's graphics path is FTDI serial, so it is worth checking whether the Monarch
	offers anything similar alongside its HID interface.
	"""
	found = 0
	for port in hwPortUtils.listComPorts():
		text = repr(port).lower()
		if any(keyword in text for keyword in ("humanware", "monarch", "aph", "braille")):
			found += 1
			_say(f"  {port}")
	_say(f"--- {found} matching serial port(s) ---")


def features() -> None:
	"""Read every feature report the device declares. Read only, never written.

	A mode switch, if one exists, is most likely a feature report. Reading one is safe and
	tells us the report exists and what it currently holds. Writing one is not safe and this
	module will not do it: an unknown vendor feature on an actuator array is not something
	to guess at.
	"""
	display = _display()
	if display is None:
		return
	device = getattr(display, "_dev", None)
	if device is None:
		_say(f"{display.name} is not a HID driver.")
		return
	reportIds = sorted({cap.ReportID for cap in _valueCaps(device, hidpi.HIDP_REPORT_TYPE.FEATURE)})
	if not reportIds:
		_say("No feature value caps declared. Nothing to read.")
		return
	for reportId in reportIds:
		try:
			data = device.getFeature(bytes([reportId]))
			_say(f"  feature 0x{reportId:02X}: {data.hex(' ')}")
		except Exception as error:
			_say(f"  feature 0x{reportId:02X}: read failed ({error})")


def hunt() -> None:
	"""Run every read only investigation, in order. Touches no pins.

	This is question 4 of phase 0 in one call. Run it first, save the console output into
	`docs/design/`, and read it before deciding anything. If it finds a vendor collection or
	a second HID interface, the plan's "long game" section becomes the near game.
	"""
	_say("=== Geometry ===")
	geometry()
	_say("=== Lattice, assuming three pin columns per cell ===")
	lattice()
	_say("=== Descriptor ===")
	descriptor()
	_say("=== Collections ===")
	collections()
	_say("=== HID interfaces ===")
	interfaces()
	_say("=== Serial ports ===")
	serialPorts()
	_say("=== Feature reports ===")
	features()
	_say("=== End of hunt. Save this output. ===")


# --- Holding the display --------------------------------------------------------------


def hold() -> None:
	"""Stop NVDA writing to the display, so a pattern stays up long enough to feel.

	`braille.handler._writeCells` is the single choke point: `update`, `message` and the
	cursor blink all reach the hardware through it. Replacing it on the handler instance
	blocks all three without disturbing the driver, the buffers, or anything the add-on has
	arranged.

	Safe to call twice. **Call `release` when finished**, or braille stays dead until NVDA
	restarts.
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


# --- Raw writes -----------------------------------------------------------------------


def raw(cells: list[int]) -> None:
	"""Write a raw cell array straight to the driver, padded or truncated to fit.

	Deliberately bypasses `braille.handler`, so no normalisation, no cursor, and no
	translation happens between this list and the pins.

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

	:param value: the dot pattern, 0 to 255. 0xFF raises all eight dots of every cell.
	"""
	display = _display()
	if display is None:
		return
	raw([value & 0xFF] * display.numCells)
	_say(f"Filled {display.numCells} cells with 0x{value & 0xFF:02X}.")


def clear() -> None:
	"""Lower every pin."""
	fill(0x00)


# --- Question 1: does it render arbitrary patterns, and in what bit order? -------------


def dotOrder() -> None:
	"""Put one dot in each of the first eight cells, in dot number order.

	Cell 0 gets dot 1, cell 1 gets dot 2, and so on to cell 7 with dot 8. Every other cell
	is blank, so the row reads as a staircase.

	What to feel, on the top row, in the first eight cells:

	- Cells 0, 1, 2 have a single dot descending the left column, and cell 6 has the fourth
	  one below them. That is dots 1, 2, 3, 7.
	- Cells 3, 4, 5 have a single dot descending the right column, and cell 7 has the fourth
	  one below them. That is dots 4, 5, 6, 8.

	If that is what is there, arbitrary bytes reach the pins unfiltered and
	`BIT_FOR_CELL_POSITION` is correct. If the dots appear in a different order, the device
	uses a different bit numbering and every packing constant in the plan needs changing. If
	some cells are blank or show more than one dot, something between us and the pins is
	interpreting cell values, and the whole approach is dead.
	"""
	display = _display()
	if display is None:
		return
	cells = [0] * display.numCells
	for bit in range(8):
		if bit < display.numCols:
			cells[bit] = 1 << bit
	raw(cells)
	_say("Wrote dots 1 to 8 into cells 0 to 7 of row 0. Feel the staircase; see the docstring.")


# --- Question 2: are the spacer columns filled? ---------------------------------------


def spacers(step: int = 0) -> None:
	"""The decisive test. Are the pin columns between cells reachable, or filled, or dead?

	Call it three times, `spacers(1)`, `spacers(2)`, `spacers(3)`, feeling the panel between
	each. Every conclusion about drawing quality in the plan follows from what you feel.

	**Step 1, whole panel solid.** Every cell 0xFF. Run a finger horizontally across the
	middle of the panel.

	- Uniformly solid, no rhythm: the Monarch fills its spacer columns when the neighbouring
	  dots are raised. Horizontal lines will be solid, fills will be smooth, and the canvas
	  behaves like a real 64 by 32 bitmap. This is the good outcome.
	- A regular ridge and groove, two pins up and one down: the spacers are dead in terminal
	  mode. Horizontal lines will read dashed, fills will read striped. Usable for diagrams,
	  bad for anything fine, and phase 3 must know the lattice.

	**Step 2, one solid column of cells.** A vertical bar four cells wide, in the middle.
	Feel its left and right edges.

	- If step 1 felt solid, this tells you whether the fill is a property of the whole panel
	  being raised or of adjacent dots specifically. A clean edge with no bleed one column
	  past the last cell means the device fills between raised neighbours only, which is
	  exactly what we want and costs us nothing.
	- Bleed past the edge means the device is interpolating, which would blur small features
	  and needs to be known before drawing anything with one dot detail.

	**Step 3, alternating full and empty cells.** Full, empty, full, empty across the row.

	- If the gaps feel one dot column wide, the spacers stay down between raised cells and
	  step 1 was solid for some other reason. Suspicious; re-feel step 1.
	- If the gaps feel three columns wide, the geometry is as published and the spacers
	  simply follow their neighbours.

	:param step: 1, 2 or 3. Zero prints this procedure instead of writing anything.
	"""
	display = _display()
	if display is None:
		return
	if step == 0:
		_say("Call spacers(1), then spacers(2), then spacers(3), feeling the panel between each.")
		_say("See the docstring for what each step means. Step 1 is the one that matters most.")
		return
	if step == 1:
		fill(0xFF)
		_say("Step 1: whole panel raised. Solid, or ridged two up one down?")
		return
	if step == 2:
		cells = [0] * display.numCells
		start = max(0, display.numCols // 2 - 2)
		for row in range(display.numRows):
			for col in range(start, min(start + 4, display.numCols)):
				cells[row * display.numCols + col] = 0xFF
		raw(cells)
		_say(f"Step 2: cells {start} to {start + 3} raised on every row. Feel the two vertical edges.")
		return
	if step == 3:
		cells = [0xFF if (index % display.numCols) % 2 == 0 else 0x00 for index in range(display.numCells)]
		raw(cells)
		_say("Step 3: alternating full and empty cells. How wide do the gaps feel?")
		return
	_say("Step must be 1, 2 or 3.")


def checker() -> None:
	"""Alternate 0x55 and 0xAA across the panel, for feeling dot level addressing.

	0x55 is dots 1, 3, 5, 7 and 0xAA is dots 2, 4, 6, 8, so adjacent cells interlock. If the
	result feels like a regular fine texture rather than a smear, single dot addressing
	works and the mechanism resolves neighbouring pins.
	"""
	display = _display()
	if display is None:
		return
	cells = [0x55 if index % 2 == 0 else 0xAA for index in range(display.numCells)]
	raw(cells)
	_say("Wrote an interlocking checker. It should feel like a fine regular texture.")


# --- A canvas, prototyping phase 1 -----------------------------------------------------

try:
	from tactile import TactileGraphicsBuffer as _BufferBase
except ImportError:  # NVDA older than 2025.1.

	class _BufferBase:  # type: ignore[no-redef]
		def __init__(self, width: int, height: int):
			self.width = width
			self.height = height


class CellCanvas(_BufferBase):
	"""A dot canvas that packs into braille cells in the correct dot order.

	This is `CellTactileGraphicsBuffer` from phase 1 of the plan, prototyped here so that
	the packing can be confirmed by touch before it is committed to the add-on and unit
	tested. Subclasses NVDA's `TactileGraphicsBuffer` where that exists, so the seam is the
	same one the DotPad driver uses.
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
		"""Draw a straight line by Bresenham. One bit pins, so there is nothing to smooth."""
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
	"""Draw one horizontal line across the canvas.

	The reading that matters: is it continuous, or does it break every two dots? That is the
	same question as `spacers` step 1, asked in the form the drawing code will actually use.

	:param y: the canvas row, 0 to height minus 1.
	"""
	buffer = canvas()
	if buffer is None:
		return
	buffer.line(0, y, buffer.width - 1, y)
	buffer.show()
	_say(f"Horizontal line at y={y}. Continuous, or dashed two on one off?")


def vLine(x: int = 16) -> None:
	"""Draw one vertical line down the canvas.

	This one should be solid whatever the spacers do, because the lattice is contiguous
	vertically. If it is not solid, the vertical geometry is not what the plan assumes.

	:param x: the canvas column, 0 to width minus 1.
	"""
	buffer = canvas()
	if buffer is None:
		return
	buffer.line(x, 0, x, buffer.height - 1)
	buffer.show()
	_say(f"Vertical line at x={x}. This should feel solid.")


def box() -> None:
	"""Draw a border round the whole canvas with both diagonals through it.

	The general purpose "is this shape legible" test. Corners, straight edges and slopes all
	at once, and the first thing worth showing anyone else.
	"""
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


# --- Timing and mechanism --------------------------------------------------------------


def timing(count: int = 10) -> None:
	"""Measure how long a full panel write takes, and let the mechanism be judged by ear.

	The number this prints is the time to hand a report to the driver, not the time for the
	pins to settle. The pins are the part that matters and the only instrument for them is
	your hand and your ear: while this runs, listen to the panel and decide whether a
	graphics area redrawn on every caret movement would be tolerable to sit beside.

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
	_say(f"{count} full panel writes: mean {mean * 1000:.1f} ms, max {max(times) * 1000:.1f} ms to hand off.")
	_say("Now say what the mechanism sounded and felt like. That is the number that decides")
	_say("whether a graphics panel can update live or must be redrawn on request.")


# --- Mixing braille and drawing ---------------------------------------------------------


def mixed(text: str = "profit by quarter") -> None:
	"""Put translated braille on the top row and a drawing underneath, in one write.

	This is phase 2's question answered at the cell level, before any panel machinery exists:
	there is one cell array, so a row of characters and a row of pixels cost the same and
	need no mode switch. If this reads as a caption above a chart, mixing works and the rest
	of phase 2 is plumbing.

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
	_say(f"Row 0 is the caption {text!r}. Rows 1 to {display.numRows - 1} are a drawing.")


# --- Session ----------------------------------------------------------------------------


def summary() -> None:
	"""Print the four phase 0 questions and where each one's answer comes from."""
	_say("Phase 0, from docs/design/tactile-graphics-plan.md:")
	_say("1. Does it render arbitrary patterns?  dotOrder(), then checker().")
	_say("2. Are the spacer columns filled?      spacers(1), spacers(2), spacers(3). The big one.")
	_say("3. What is the real geometry?          geometry(), lattice().")
	_say("4. What does the descriptor contain?   hunt(). Save its output into docs/design/.")
	_say("Also: timing() for the mechanism, mixed() for the phase 2 question.")
	_say(f"Currently held: {_held}. Call release() before closing the console.")
