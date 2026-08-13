# BrlMultiline: configuration for the virtual display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which physical displays make up the virtual one.

Stored in a section of its own rather than under the add-on's `BrlMultiline` section, and
the reason is timing. NVDA initialises braille at `core.py` line 829 and global plugins at
line 928, so this driver reads its configuration a hundred lines before the plugin
registers `BrlMultiline`. `bmConfig.initialize` assigns that whole section's specification
at once, which would replace anything the driver had added to it. Two sections, and no
ordering hazard to remember.

The plugin may read this module freely once it is running; by then braille is long up.
"""

from __future__ import annotations

import config
from logHandler import log

from .virtualLayout import DeviceSpec, formatDeviceSpec, parseDeviceSpecs

CONFIG_SECTION = "BrlMultilineVirtualDisplay"

configSpec = {
	"devices": "string_list(default=list())",
}
"""Configuration specification, registered under `config.conf.spec[CONFIG_SECTION]`.

- `devices`: the physical displays making up the virtual one, in stacking order, top first.
	Each entry is a driver name, optionally followed by `|` and a port. A driver name alone
	means detect afresh, which is what `virtualLayout.DEFAULT_PORT` is about.
"""

_initialised = False


def initialize() -> None:
	"""Register the specification. Safe to call more than once.

	Called at import rather than at construction, because NVDA imports every display driver
	module whenever it builds the list of available displays, and the settings panel wants
	to read this without a virtual display being live.
	"""
	global _initialised
	if _initialised:
		return
	config.conf.spec[CONFIG_SECTION] = configSpec
	_initialised = True


def getDevices() -> list[DeviceSpec]:
	"""Read the configured member displays.

	:return: the specifications, in stacking order. Empty when none are configured.
	:raise ValueError: if the stored list is malformed or names a driver twice.
	"""
	initialize()
	return parseDeviceSpecs(config.conf[CONFIG_SECTION]["devices"])


def setDevices(specs: list[DeviceSpec]) -> None:
	"""Store the member displays.

	Validates before writing, so that a list which could never be opened is refused at the
	point it is set rather than at the next braille restart.

	:param specs: the specifications, in stacking order.
	:raise ValueError: if the list names a driver twice.
	"""
	initialize()
	entries = [formatDeviceSpec(spec) for spec in specs]
	# Round trip through the parser so that setting and loading cannot disagree.
	parseDeviceSpecs(entries)
	config.conf[CONFIG_SECTION]["devices"] = entries
	log.debug(f"BrlMultiline: virtual display devices set to {entries}")


initialize()
