# BrlMultiline: configuration for the virtual display.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which physical displays make up the virtual one.

A section of its own, since braille starts before the global plugin, whose
`bmConfig.initialize` replaces its whole section's specification.

Base only: it is which hardware is wired together, and NVDA does not reopen the display when a
profile changes it. NVDA's base only handling happens at startup, before this registers; see
`_makeBaseOnly`, `_baseSection` and `_migrateFromProfiles`.
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
	means detect afresh, which is what `virtualLayout.DEFAULT_PORT` is about. A third field,
	after a second `|`, is the rows a display reporting one row physically has, as in
	`hidBrailleStandard||3`; see `virtualLayout.memberShape`.

Registered, but never depended on, since it may not have been applied. See `_readEntries`.
"""

_initialised = False


def initialize() -> None:
	"""Register the specification and settle where the list lives. Safe to call repeatedly.

	At import, so the settings panel can read it without a virtual display live.
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

	Through `ConfigManager.BASE_ONLY_SECTIONS`, extensible by add-ons, every session; not
	`config.configSections.registerSection`, which persists and acts a session late. Best
	effort, since `_baseSection` reads the base profile directly anyway.
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

	Not `config.conf[CONFIG_SECTION]`, which raises `KeyError`, since `_initBaseConf` ran before
	this registered. Also writes to base whether or not `_makeBaseOnly` succeeded.

	:return: the section, which may be empty but is never missing.
	"""
	base = config.conf.profiles[0]
	if CONFIG_SECTION not in base:
		base[CONFIG_SECTION] = {}
	return base[CONFIG_SECTION]


def _coerceEntries(entries) -> list[str] | None:
	"""Make a list of driver names out of whatever was stored.

	Unvalidated, configobj gives a one entry list as a bare string.

	:param entries: the stored value.
	:return: the entries, or None if the value is not something a list could be made of.
	"""
	if isinstance(entries, str):
		return [entries] if entries.strip() else []
	try:
		return [str(entry) for entry in entries]
	except TypeError:
		return None


def _readEntries() -> list[str]:
	"""Read the stored entries, tolerating everything an unvalidated section can be.

	Never raises for a missing section or key, or the bare string `_coerceEntries` handles.

	:return: the entries, or an empty list if there is nothing readable there.
	"""
	try:
		stored = _baseSection().get("devices", [])
	except Exception:
		log.error(f"BrlMultiline: could not read {CONFIG_SECTION}", exc_info=True)
		return []
	entries = _coerceEntries(stored)
	if entries is None:
		log.error(f"BrlMultiline: {CONFIG_SECTION} devices is {stored!r}, which is not a list")
		return []
	return entries


def _eachProfile():
	"""Yield every configuration profile a stored device list could be hiding in.

	The active stack, for unsaved profiles, and `listProfiles`, for saved ones not active.
	`_getProfile`, since the public `getProfile` returns only loaded profiles.

	:return: pairs of a name to report and the profile, skipping any that cannot be read.
	"""
	for index, profile in enumerate(config.conf.profiles[1:], start=1):
		yield getattr(profile, "name", None) or f"active profile {index}", profile
	try:
		names = list(config.conf.listProfiles())
	except Exception:
		log.debugWarning("BrlMultiline: could not list the configuration profiles", exc_info=True)
		return
	for name in names:
		try:
			yield name, config.conf._getProfile(name)
		except Exception:
			# One unreadable profile must not stop the others being looked at.
			log.debugWarning(f"BrlMultiline: could not read configuration profile {name!r}", exc_info=True)


def _devicesInProfiles() -> dict[str, list[str]]:
	""":return: every non-empty device list stored in a profile, by the profile's name."""
	found: dict[str, list[str]] = {}
	for name, profile in _eachProfile():
		if name in found:
			# The same profile reached twice: active, and saved on disk.
			continue
		try:
			section = profile.get(CONFIG_SECTION)
			stored = section.get("devices") if section is not None else None
		except Exception:
			log.debugWarning(f"BrlMultiline: could not read {CONFIG_SECTION} from {name!r}", exc_info=True)
			continue
		entries = _coerceEntries(stored) if stored else None
		if entries:
			found[name] = entries
	return found


def _migrateFromProfiles() -> None:
	"""Copy a list stored under a configuration profile into the base configuration.

	For a list written to a profile before the section was base only. Never overwrites base,
	leaves the profile's copy in place and logs it, and copies nothing when profiles disagree.
	"""
	try:
		base = _baseSection()
		found = _devicesInProfiles()
		if "devices" in base:
			# Already where it belongs. Say so if a profile still carries one, since that copy
			# is about to stop having any effect.
			if found:
				log.info(
					f"BrlMultiline: configuration profiles {sorted(found)} also hold "
					f"{CONFIG_SECTION}. The base configuration is used; their copies are ignored.",
				)
			return
		if not found:
			return
		distinct = {tuple(entries) for entries in found.values()}
		if len(distinct) > 1:
			log.warning(
				f"BrlMultiline: configuration profiles hold different lists of displays to "
				f"combine ({found}), and this is now one setting for all of them. None has been "
				f"used; choose the displays again in the BrlMultiline displays settings.",
			)
			return
		base["devices"] = list(next(iter(distinct)))
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
	:raise ValueError: if the stored list names a driver twice or names this driver.
	"""
	initialize()
	return parseDeviceSpecs(_readEntries())


def setDevices(specs: list[DeviceSpec]) -> None:
	"""Store the member displays in the base configuration.

	Validates before writing.

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
