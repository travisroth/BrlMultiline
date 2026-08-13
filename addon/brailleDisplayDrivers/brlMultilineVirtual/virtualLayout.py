# BrlMultiline: geometry and configuration parsing for the virtual display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""How several physical displays become one rectangle, and back again.

Imports nothing from NVDA, so it can be unit tested outside a running screen reader, in
the same spirit as the add-on's `layout` module.

Devices stack vertically in configured order. The virtual display has as many rows as the
members have between them, and is as wide as the widest of them. Each device owns a
contiguous band of virtual rows, and within its band the columns past its own width are
dead: nothing ever writes them to hardware.

Dead columns are the hazard this module exists to make visible. NVDA flowing one buffer
across the whole rectangle would put text into cells no hardware receives, and it would
simply vanish. `deadColumnCount` is what lets the driver say so at startup, and the base
view built in a later phase is what actually masks them.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Sequence

DEVICE_FIELD_SEPARATOR = "|"
"""Separates a driver name from its port in a stored device entry.

Not a colon and not a comma: configobj splits list items on commas, and a port may be a
device path full of colons and backslashes.
"""

DEFAULT_PORT = "auto"
"""The port used when an entry names only a driver.

Deliberately not the port NVDA has stored for that driver. Phase 0 found a configured port
outliving the connection it described, so that the driver reported finding no display while
the hardware was plainly present. Detecting afresh is the behaviour that works.
"""


class DeviceSpec(NamedTuple):
	"""One member of the virtual display, as configured."""

	driverName: str
	port: str = DEFAULT_PORT


class DeviceBand(NamedTuple):
	"""The rows of the virtual display one device occupies."""

	rowStart: int
	numRows: int
	numCols: int

	@property
	def rowEnd(self) -> int:
		"""One past the last row of this band."""
		return self.rowStart + self.numRows

	@property
	def numCells(self) -> int:
		"""How many cells the device itself has."""
		return self.numRows * self.numCols


class VirtualGeometry(NamedTuple):
	"""The composite rectangle, and where each device sits in it."""

	numRows: int
	numCols: int
	bands: tuple[DeviceBand, ...]

	@property
	def displaySize(self) -> int:
		"""How many cells the composite has, including dead ones."""
		return self.numRows * self.numCols


def parseDeviceSpec(text: str) -> DeviceSpec:
	"""Read one stored device entry.

	Accepts `driverName` or `driverName|port`. A missing port means `DEFAULT_PORT` rather
	than whatever NVDA has stored for that driver.

	:param text: the stored entry.
	:return: the parsed specification.
	:raise ValueError: if the entry names no driver, or carries more than one separator.
	"""
	parts = text.strip().split(DEVICE_FIELD_SEPARATOR)
	if len(parts) > 2:
		raise ValueError(f"Device entry {text!r} has more than one {DEVICE_FIELD_SEPARATOR!r}")
	driverName = parts[0].strip()
	if not driverName:
		raise ValueError(f"Device entry {text!r} names no driver")
	port = parts[1].strip() if len(parts) == 2 else ""
	return DeviceSpec(driverName, port or DEFAULT_PORT)


def formatDeviceSpec(spec: DeviceSpec) -> str:
	"""Render a device specification for storage.

	:param spec: the specification.
	:return: an entry `parseDeviceSpec` will read back to an equal specification.
	"""
	if spec.port == DEFAULT_PORT:
		return spec.driverName
	return f"{spec.driverName}{DEVICE_FIELD_SEPARATOR}{spec.port}"


def parseDeviceSpecs(entries: Iterable[str]) -> list[DeviceSpec]:
	"""Read the configured device list, rejecting what cannot work.

	Duplicate driver names are refused rather than merely warned about. Phase 0 confirmed
	on hardware that two displays sharing a driver cannot be told apart: their gestures
	carry identical sources, they arrive on the one shared I/O thread, and no gesture holds
	a reference to the instance that made it. They would also share one settings section.
	A configuration that cannot work should fail where it is written, not later and
	mysteriously.

	Blank entries are skipped, since an empty string is what an empty configured list looks
	like from some editors.

	:param entries: the stored entries.
	:return: the specifications, in stacking order.
	:raise ValueError: if an entry is malformed, or two entries name one driver.
	"""
	specs: list[DeviceSpec] = []
	seen: set[str] = set()
	for entry in entries:
		if not entry.strip():
			continue
		spec = parseDeviceSpec(entry)
		if spec.driverName in seen:
			raise ValueError(
				f"{spec.driverName!r} is listed more than once. "
				"Two displays using one driver cannot be told apart.",
			)
		seen.add(spec.driverName)
		specs.append(spec)
	return specs


def stackDevices(shapes: Sequence[tuple[int, int]]) -> VirtualGeometry:
	"""Lay devices out one above another.

	:param shapes: each device's (numRows, numCols), in stacking order.
	:return: the composite geometry.
	:raise ValueError: if there are no devices, or one has a non positive dimension.
	"""
	if not shapes:
		raise ValueError("A virtual display needs at least one device")
	bands: list[DeviceBand] = []
	rowStart = 0
	for index, (numRows, numCols) in enumerate(shapes):
		if numRows < 1 or numCols < 1:
			raise ValueError(f"Device {index} reports {numRows} rows of {numCols}, which cannot be shown")
		bands.append(DeviceBand(rowStart=rowStart, numRows=numRows, numCols=numCols))
		rowStart += numRows
	return VirtualGeometry(
		numRows=rowStart,
		numCols=max(band.numCols for band in bands),
		bands=tuple(bands),
	)


def sliceBandCells(cells: Sequence[int], virtualNumCols: int, band: DeviceBand) -> list[int]:
	"""Cut one device's own cell array out of the composite one.

	The composite is row major over the full rectangle, so a device narrower than the
	widest one takes the first `band.numCols` of each of its rows and leaves the rest.

	:param cells: the composite array, exactly `numRows * virtualNumCols` long.
	:param virtualNumCols: the composite width.
	:param band: the device's band.
	:return: the device's array, `band.numCells` long.
	:raise ValueError: if the composite array is not the length the band implies.
	"""
	if band.numCols > virtualNumCols:
		raise ValueError(f"Band of {band.numCols} columns does not fit a display {virtualNumCols} wide")
	required = band.rowEnd * virtualNumCols
	if len(cells) < required:
		raise ValueError(f"Composite array of {len(cells)} cells is too short; {required} needed")
	deviceCells: list[int] = []
	for row in range(band.rowStart, band.rowEnd):
		start = row * virtualNumCols
		deviceCells.extend(cells[start : start + band.numCols])
	return deviceCells


def deviceCellIndexToVirtual(index: int, band: DeviceBand, virtualNumCols: int) -> int:
	"""Translate a cell index reported by a device into one in the composite.

	Devices report cell indexes flat across their own rows, which Phase 0 confirmed on a
	Monarch: presses spread over its rows came back as 2, 39 and 102, the last being row 3
	column 6 of an 8 by 32 display. This undoes that against the composite width.

	Written now because it is geometry and belongs beside the rest of it. Nothing calls it
	until routing keys are translated, which is the next phase.

	:param index: the device's own flat cell index.
	:param band: the device's band.
	:param virtualNumCols: the composite width.
	:return: the flat cell index in the composite.
	:raise ValueError: if the index is not a cell of that device.
	"""
	if not 0 <= index < band.numCells:
		raise ValueError(f"Cell {index} is not one of the {band.numCells} cells of this device")
	row = band.rowStart + index // band.numCols
	col = index % band.numCols
	return row * virtualNumCols + col


def deadColumnCount(geometry: VirtualGeometry) -> int:
	"""Count the cells of the composite that no hardware will ever show.

	:param geometry: the composite geometry.
	:return: how many cells are dead. Zero when every device is the same width.
	"""
	return sum((geometry.numCols - band.numCols) * band.numRows for band in geometry.bands)
