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

**The section is base only.** This list is which pieces of hardware are wired together, not
a per-application preference, and NVDA cannot honour a per-profile answer anyway:
`_switchDisplay` is not called when the driver name has not changed, so the composite goes on
driving the members it opened while the configuration describes a different set. Honouring it
would mean closing and reopening two Bluetooth displays every time a profile triggered, which
is a worse answer than the problem.

Getting there needs more than adding a name to a set, because everything NVDA does for a
base only section happens at startup and this driver registers long afterwards. See
L{_makeBaseOnly} and L{_baseSection} for the three things that follow from that, and
L{_migrateFromProfiles} for not losing a list that was stored under a profile before this
was so.
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

Registered so that NVDA validates the section wherever it can, but never depended on: this
driver reads the section long after the base configuration was validated, so nothing may
assume the specification has been applied. See L{_readEntries}.
"""

_initialised = False


def initialize() -> None:
	"""Register the specification and settle where the list lives. Safe to call repeatedly.

	Called at import rather than at construction, because NVDA imports every display driver
	module whenever it builds the list of available displays, and the settings panel wants
	to read this without a virtual display being live.
	"""
	global _initialised
	if _initialised:
		return
	config.conf.spec[CONFIG_SECTION] = configSpec
	# Before `_makeBaseOnly`, and that order is the whole of it: once the section is base
	# only, `config.conf[CONFIG_SECTION]` no longer reports what a profile holds, so a list
	# stored under one could never be found again.
	_migrateFromProfiles()
	_makeBaseOnly()
	_initialised = True


def _makeBaseOnly() -> None:
	"""Ask NVDA to read this section from the base configuration only.

	`ConfigManager.BASE_ONLY_SECTIONS` is documented as extensible by add-ons and is mutated
	in place, so adding to it is the sanctioned move. It is deliberately not done through
	`config.configSections.registerSection`, which persists the registration to a file that
	`_loadCustomSections` reads at the *next* start: that would take effect a session late and
	would outlive the add-on, needing an uninstall hook to clean up. This module registers
	itself every session instead, which needs no cleaning up.

	Best effort. If the attribute is ever renamed, everything here still works — the list is
	read and written through `_baseSection`, which goes to the base profile directly. What
	would be lost is only that some other reader of `config.conf[CONFIG_SECTION]` might see a
	profile's copy, and nothing else reads it.
	"""
	try:
		config.ConfigManager.BASE_ONLY_SECTIONS.add(CONFIG_SECTION)
	except Exception:
		log.debugWarning(
			f"BrlMultiline: could not make {CONFIG_SECTION} base only; "
			"the device list is still read from the base configuration",
			exc_info=True,
		)


def _baseSection():
	"""Get the section out of the base configuration, creating it if it is not there.

	Not `config.conf[CONFIG_SECTION]`, and the difference matters twice. NVDA materialises and
	validates a base only section in `_initBaseConf`, which loops over the sections registered
	at that point — long before this driver exists — so ours is neither created nor validated
	and reading it through the manager would raise `KeyError`. Going to the base profile
	directly also means the list is written to the base configuration whether or not
	L{_makeBaseOnly} succeeded.

	:return: the section, which may be empty but is never missing.
	"""
	base = config.conf.profiles[0]
	if CONFIG_SECTION not in base:
		base[CONFIG_SECTION] = {}
	return base[CONFIG_SECTION]


def _readEntries() -> list[str]:
	"""Read the stored entries, tolerating everything an unvalidated section can be.

	Three things can go wrong here and none of them is the user's fault, so none of them
	raises. The section may be missing, because nothing validated it into existence. The key
	may be missing, because the specification's default was never applied. And a list of
	exactly one entry comes back from configobj as a bare string when it has not been
	validated — so a single member composite would otherwise be read as one driver name per
	character.

	:return: the entries, or an empty list if there is nothing readable there.
	"""
	try:
		entries = _baseSection().get("devices", [])
	except Exception:
		log.error(f"BrlMultiline: could not read {CONFIG_SECTION}", exc_info=True)
		return []
	if isinstance(entries, str):
		# configobj gives a string rather than a one item list when nothing validated it.
		return [entries] if entries.strip() else []
	try:
		return [str(entry) for entry in entries]
	except TypeError:
		log.error(f"BrlMultiline: {CONFIG_SECTION} devices is {entries!r}, which is not a list")
		return []


def _migrateFromProfiles() -> None:
	"""Copy a list stored under a configuration profile into the base configuration.

	Until this section was base only, NVDA wrote it to whichever profile was active when the
	user changed it. Anyone who edited the member list with a profile active has their list
	there, and it would silently become invisible the moment reads were redirected to base.

	Only ever copies up, never down, and never overwrites: a list already in the base
	configuration is the authority. A profile's leftover copy is left in place rather than
	deleted, because deleting from someone's saved profile is not this driver's business, but
	it is reported so that a puzzling `.ini` file has an explanation.
	"""
	try:
		base = _baseSection()
		if "devices" in base:
			# Already where it belongs. Say so if a profile still carries one, since that copy
			# is about to stop having any effect.
			if any(CONFIG_SECTION in profile for profile in config.conf.profiles[1:]):
				log.info(
					f"BrlMultiline: a configuration profile also holds {CONFIG_SECTION}. "
					"The base configuration is used; the profile's copy is ignored.",
				)
			return
		stored = config.conf[CONFIG_SECTION].get("devices")
		if not stored:
			return
		base["devices"] = list(stored) if not isinstance(stored, str) else [stored]
		log.info(
			f"BrlMultiline: moved the combined display's device list into the base "
			f"configuration, where profiles cannot change it: {base['devices']}",
		)
	except Exception:
		# A failed migration costs the user their list, which is a bad outcome but a
		# recoverable one; a raise here would cost them braille entirely.
		log.error("BrlMultiline: could not migrate the device list", exc_info=True)


def getDevices() -> list[DeviceSpec]:
	"""Read the configured member displays.

	:return: the specifications, in stacking order. Empty when none are configured, and empty
		rather than raising when the section itself cannot be read.
	:raise ValueError: if the stored list names a driver twice or names this driver. Those are
		the user's own configuration errors and are worth reporting rather than silently
		reducing to no display.
	"""
	initialize()
	return parseDeviceSpecs(_readEntries())


def setDevices(specs: list[DeviceSpec]) -> None:
	"""Store the member displays in the base configuration.

	Validates before writing, so that a list which could never be opened is refused at the
	point it is set rather than at the next braille restart.

	:param specs: the specifications, in stacking order.
	:raise ValueError: if the list names a driver twice.
	"""
	initialize()
	entries = [formatDeviceSpec(spec) for spec in specs]
	# Round trip through the parser so that setting and loading cannot disagree.
	parseDeviceSpecs(entries)
	_baseSection()["devices"] = entries
	log.debug(f"BrlMultiline: virtual display devices set to {entries}")


initialize()
