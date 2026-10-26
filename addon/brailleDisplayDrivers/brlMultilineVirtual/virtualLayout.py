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

Text in dead columns vanishes. `deadColumnCount` lets the driver say so at startup; the global
plugin masks them.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Sequence

DEVICE_FIELD_SEPARATOR = "|"
"""Separates a driver name from its port in a stored device entry.

Not a colon and not a comma: configobj splits list items on commas, and a port may be a
device path full of colons and backslashes.
"""

VIRTUAL_DRIVER_NAME = "brlMultilineVirtual"
"""This driver's own name, which is the one name a member may not have.

A list naming it would recurse slowly, so it is refused where the list is read. Duplicated
from the driver class, which imports NVDA.
"""

DEFAULT_PORT = "auto"
"""The port used when an entry names only a driver.

Detect afresh, not NVDA's stored port, which can outlive its connection.
"""


class DeviceSpec(NamedTuple):
	"""One member of the virtual display, as configured."""

	driverName: str
	port: str = DEFAULT_PORT
	rows: int = 0
	"""How many rows the display physically has, where its driver reports one. 0 means as
	reported. See `memberShape`."""


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

	Accepts `driverName`, `driverName|port` or `driverName|port|rows`. A missing port means
	`DEFAULT_PORT` rather than whatever NVDA has stored for that driver, so the rows of a
	display detected afresh are written `driverName||rows`. Missing rows mean as reported.

	:param text: the stored entry.
	:return: the parsed specification.
	:raise ValueError: if the entry names no driver, carries more than two separators, or has
		rows that are not a number from 0 up.
	"""
	parts = text.strip().split(DEVICE_FIELD_SEPARATOR)
	if len(parts) > 3:
		raise ValueError(f"Device entry {text!r} has more than two {DEVICE_FIELD_SEPARATOR!r}")
	driverName = parts[0].strip()
	if not driverName:
		raise ValueError(f"Device entry {text!r} names no driver")
	port = parts[1].strip() if len(parts) >= 2 else ""
	rowsText = parts[2].strip() if len(parts) == 3 else ""
	try:
		rows = int(rowsText) if rowsText else 0
	except ValueError:
		raise ValueError(f"Device entry {text!r} gives rows that are not a number") from None
	if rows < 0:
		raise ValueError(f"Device entry {text!r} gives a negative number of rows")
	return DeviceSpec(driverName, port or DEFAULT_PORT, rows)


def formatDeviceSpec(spec: DeviceSpec) -> str:
	"""Render a device specification for storage.

	:param spec: the specification.
	:return: an entry `parseDeviceSpec` will read back to an equal specification.
	"""
	port = "" if spec.port == DEFAULT_PORT else spec.port
	if spec.rows:
		return DEVICE_FIELD_SEPARATOR.join((spec.driverName, port, str(spec.rows)))
	if not port:
		return spec.driverName
	return f"{spec.driverName}{DEVICE_FIELD_SEPARATOR}{port}"


def memberShape(numRows: int, numCols: int, rows: int = 0) -> tuple[int, int]:
	"""The shape a member display takes in the stack.

	Its driver's own, unless the driver reports one row and the reader says there are more,
	as on an Orbit Slate 340 under HID standard. Cells and routing stay flat across the device
	(see `sliceBandCells`). Rows that do not divide the cells evenly are ignored.

	:param numRows: the rows the driver reports.
	:param numCols: the columns the driver reports.
	:param rows: the rows the reader says the display has, or 0.
	:return: the (numRows, numCols) to stack it as.
	"""
	if numRows == 1 and rows > 1 and numCols > rows and numCols % rows == 0:
		return rows, numCols // rows
	return numRows, numCols


def parseDeviceSpecs(entries: Iterable[str]) -> list[DeviceSpec]:
	"""Read the configured device list, rejecting what cannot work.

	Duplicate driver names are refused, since their gestures and settings cannot be told
	apart. The virtual driver is refused; see `VIRTUAL_DRIVER_NAME`. Blank entries are skipped.

	:param entries: the stored entries.
	:return: the specifications, in stacking order.
	:raise ValueError: if an entry is malformed, names this driver, or names one driver twice.
	"""
	specs: list[DeviceSpec] = []
	seen: set[str] = set()
	for entry in entries:
		if not entry.strip():
			continue
		spec = parseDeviceSpec(entry)
		if spec.driverName == VIRTUAL_DRIVER_NAME:
			raise ValueError(f"{VIRTUAL_DRIVER_NAME!r} cannot be a display of its own")
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

	Devices report indexes flat across their own rows; this maps them to the composite width.

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
