# BrlMultiline: the reader's personal braille tables, and listing them for NVDA.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Any of NVDA's own braille tables, with the reader's substitutions in front of it.

BrlMultiline's own tables are English. A reader of Spanish or UK braille makes a personal table
from the table they read, in the substitutions dialog, and their list is used with it too. Each is a
two line table, `<table file>-brlMultiline-personal.utb`, such as `es-g1.ctb-brlMultiline-personal.utb`, that includes the substitutions and then the
NVDA table, beside the add-on's own tables. See `brailleSubstitutions.PersonalTable`.

**Listed in the add-on's manifest, and seen after NVDA restarts.** NVDA chooses the braille table
before any add-on's code runs, from the tables it knows then: its own, those in add-on manifests,
and the developer scratchpad. A table registered later is not there when NVDA reads the reader's
choice, and NVDA falls back to its default until restarted. So a personal table goes in the
manifest, as installing or enabling an add-on is seen only after a restart.

**The manifest is NVDA's to read and must not be broken by this.** A manifest NVDA cannot read
disables the whole add-on, which happened once from a table name with a comma in it. So the
manifest is read and written with `configobj`, which quotes what needs quoting; the result is read
back with NVDA's own `AddonManifest` and refused if NVDA would find anything wrong in it, or if
anything but the personal tables differs; and it replaces the old one in one step, which is kept as
`manifest.ini.bak`. A failure leaves the manifest as it was.

**Made again from the reader's list whenever it might be missing**: at startup, and when the
add-on is installed or updated, since a new copy of the add-on brings its own manifest with no
personal tables in it. See `installTasks.py`.

**Nothing here imports the rest of the add-on**, so `installTasks.py` can load this file on its own
while the add-on is being installed.
"""

import os
from typing import Callable, Iterable, Optional

WRAPPER_SUFFIX = "-brlMultiline-personal.utb"
RULES_FILE = "brlMultiline-user.cti"
MANIFEST_FILE = "manifest.ini"
SECTION = "brailleTables"
NAME_SUFFIX = " (BrlMultiline personal)"


def wrapperName(base: str) -> str:
	""":return: the file name of the personal table made from an NVDA table: `es-g1.ctb` gives
	`es-g1.ctb-brlMultiline-personal.utb`.

	The whole of the table's file name, extension and all: NVDA has `bg.ctb` and `bg.utb`, Bulgarian
	computer braille and Bulgarian grade 1, and named without the extension both were one file and one
	manifest entry. A personal table named the earlier way is not wanted under either name, so it is
	removed like any other, once NVDA no longer has it in use.
	"""
	return base + WRAPPER_SUFFIX


def wrapperText(base: str) -> str:
	""":return: the personal table: the substitutions, then the NVDA table.

	The NVDA table is included by name. NVDA's table resolver finds it among its own tables, which
	are the only ones offered: another add-on's table would not be found from here.
	"""
	return (
		f"# {base} with the reader's BrlMultiline braille substitutions in front of it.\n"
		"# Written by BrlMultiline; made again from the reader's list, so not to be edited.\n\n"
		f"include {RULES_FILE}\ninclude {base}\n"
	)


def manifestEntries(tables: Iterable) -> dict:
	""":return: the manifest's entries for the personal tables, by file name, as configobj writes them.

	:param tables: `PersonalTable`s, or anything with `base`, `displayName` and `contracted`.
	"""
	return {
		wrapperName(table.base): {
			"displayName": f"{table.displayName}{NAME_SUFFIX}",
			"contracted": str(bool(table.contracted)),
			"output": "True",
			# Output only: braille input is the NVDA table's own business.
			"input": "False",
		}
		for table in tables
	}


def _isPersonal(fileName: str) -> bool:
	return fileName.endswith(WRAPPER_SUFFIX)


def _asStrings(section) -> dict:
	return {key: {name: str(value) for name, value in entry.items()} for key, entry in section.items()}


class NotListed(Exception):
	"""The manifest could not be changed. It is as it was."""


def nvdaValidation(path: str) -> Optional[str]:
	""":return: what NVDA's own manifest reader finds wrong with a manifest, or None."""
	import addonHandler

	with open(path, "rb") as file:
		errors = addonHandler.AddonManifest(file).errors
	return None if errors is None else str(errors)


def sync(
	addonDir: str,
	tables: Iterable,
	validate: Callable[[str], Optional[str]] = nvdaValidation,
	inUse: Iterable[str] = (),
) -> bool:
	"""Make the personal tables, and the manifest's list of them, what the reader's list says.

	:param addonDir: the add-on's folder, holding `manifest.ini` and `brailleTables`.
	:param tables: `PersonalTable`s.
	:param validate: says what is wrong with a manifest, or None. NVDA's own reader by default.
	:param inUse: the personal tables NVDA has now, by file name. One of them no longer wanted is
		taken out of the manifest but its file is kept, since NVDA goes on translating with it until
		it restarts. See `_syncWrappers`.
	:return: whether anything changed, which NVDA sees only after it restarts.
	:raises NotListed: if the manifest could not be written. The tables written are harmless
		without it, and the manifest is as it was.
	"""
	tables = list(tables)
	changed = _syncWrappers(os.path.join(addonDir, "brailleTables"), tables, set(inUse))
	return _syncManifest(os.path.join(addonDir, MANIFEST_FILE), tables, validate) or changed


def _syncWrappers(folder: str, tables: list, inUse: set = frozenset()) -> bool:
	"""Write the personal tables wanted, and remove those not wanted that NVDA is not using.

	**A table NVDA has is kept until it restarts.** NVDA keeps the tables it read at startup, and the
	reader's output table may be one of them: removed, liblouis would have nothing to compile the
	next time it is asked to, which is the next time the substitutions change, and braille would stop.
	Kept, it goes on working; it is no longer in the manifest, so NVDA does not have it after the
	restart, and it is removed then.
	"""
	changed = False
	wanted = {wrapperName(table.base): wrapperText(table.base) for table in tables}
	for name, text in wanted.items():
		path = os.path.join(folder, name)
		try:
			with open(path, encoding="utf-8") as file:
				if file.read() == text:
					continue
		except OSError:
			pass
		with open(path, "w", encoding="ascii", newline="\n") as file:
			file.write(text)
		changed = True
	for name in os.listdir(folder):
		if _isPersonal(name) and name not in wanted and name not in inUse:
			os.remove(os.path.join(folder, name))
			changed = True
	return changed


def _syncManifest(path: str, tables: list, validate: Callable[[str], Optional[str]]) -> bool:
	from configobj import ConfigObj

	manifest = ConfigObj(path, encoding="utf-8", default_encoding="utf-8")
	wanted = manifestEntries(tables)
	section = manifest.get(SECTION, {})
	current = {key: entry for key, entry in _asStrings(section).items() if _isPersonal(key)}
	if current == wanted:
		return False
	if SECTION not in manifest:
		manifest[SECTION] = {}
	for key in list(manifest[SECTION]):
		if _isPersonal(key):
			del manifest[SECTION][key]
	for key, entry in wanted.items():
		manifest[SECTION][key] = entry
	temporary = f"{path}.tmp"
	manifest.filename = temporary
	try:
		manifest.write()
		problem = validate(temporary)
		if problem is None:
			problem = _changedBeyondPersonalTables(path, temporary)
		if problem is not None:
			raise NotListed(problem)
		with open(path, "rb") as source, open(f"{path}.bak", "wb") as backup:
			backup.write(source.read())
		os.replace(temporary, path)
	except OSError as error:
		raise NotListed(str(error)) from error
	finally:
		if os.path.exists(temporary):
			os.remove(temporary)
	return True


def _changedBeyondPersonalTables(oldPath: str, newPath: str) -> Optional[str]:
	""":return: what differs between two manifests besides the personal tables, or None."""
	from configobj import ConfigObj

	def withoutPersonal(path: str) -> dict:
		read = ConfigObj(path, encoding="utf-8", default_encoding="utf-8").dict()
		if SECTION in read:
			read[SECTION] = {key: entry for key, entry in read[SECTION].items() if not _isPersonal(key)}
			if not read[SECTION]:
				del read[SECTION]
		return read

	old, new = withoutPersonal(oldPath), withoutPersonal(newPath)
	if old == new:
		return None
	differing = sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key))
	return f"writing the manifest would also change {', '.join(differing)}"


def listed(addonDir: str) -> list:
	""":return: the file names of the personal tables the manifest lists now."""
	from configobj import ConfigObj

	try:
		manifest = ConfigObj(
			os.path.join(addonDir, MANIFEST_FILE), encoding="utf-8", default_encoding="utf-8"
		)
	except Exception:
		return []
	return sorted(key for key in manifest.get(SECTION, {}) if _isPersonal(key))
