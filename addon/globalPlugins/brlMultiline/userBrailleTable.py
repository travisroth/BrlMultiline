# BrlMultiline: storing the reader's braille substitutions and putting them in force.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Where the braille substitutions are kept, and how they reach liblouis. See `brailleSubstitutions`.

**The list is a file of its own** in NVDA's configuration folder, `brlMultiline\\brailleSubstitutions
.json`, saved the moment the reader presses OK, as NVDA saves its speech dictionaries. Not in
`config.conf`, which is written only when NVDA saves its configuration: a reader who had turned off
saving on exit would have typed a list in and lost it. One list for every profile; an entry names
the profile it is for, if it is for one. One list rather than one a profile because a value a profile
holds hides the base configuration's, which is what made the table layouts one store (see
`bmConfig.tableLayouts`).

**Written so that nothing is lost.** The new list goes to a temporary file that then takes the place
of the old one, so a crash part way through leaves one or the other whole, never half of each; and
the list it replaces is kept beside it as `.bak`. A list that cannot be read is set aside under a
name of its own and the backup read instead. Nothing a reader typed is deleted by this module.

**The rules file is in the add-on's own `brailleTables` folder**, because that is where liblouis
looks for a file a table includes: NVDA's table resolver searches the including table's folder and
then its own tables, nothing else. It is derived, written again whenever the list or the profile
changes, and the list is never read back out of it.

**Nothing reaches the rules file without compiling first.** The candidate rules are compiled with
every BrlMultiline table before the list is saved; if liblouis refuses them, nothing changes and the
reader is told which entry and what liblouis said. Compiled under names of their own beside the
real tables, since an absolute path in an `include` does not survive Windows' backslashes, and the
real tables are not touched.

**A reader's personal tables are kept in the same file**: NVDA tables they made a version of with
their substitutions in front, for languages BrlMultiline has no tables of its own for. Writing those
and listing them for NVDA is `personalTables`'s; NVDA sees a new one only after it restarts.

**liblouis keeps a table it has compiled**, and would go on translating with the old rules. After the
rules file changes every compiled table is freed, which liblouis then compiles again the next time
it translates, and the flow renderings are told through `brailleSubstitutions.rulesChanged`.
"""

import os
import re
import shutil
import time
from typing import Callable, Iterable, NamedTuple, Optional

import config
from logHandler import log

from . import brailleSubstitutions, personalTables
from .brailleSubstitutions import RULES_FILE, PersonalTable, Store, StoreError, Substitution

LOG_PREFIX = "BrlMultiline braille substitutions: "

STORE_FILE = "brailleSubstitutions.json"

CHECK_STEM = "brlMultiline-user.check"
"""The names candidate rules are compiled under. See `validate`."""

TABLE_SUFFIX = "-brlMultiline.utb"

_store: Optional[Store] = None
"""The list and the personal tables as last read or saved. Read once; the dialog edits a copy and
`save` replaces it."""

_loadProblem: Optional[str] = None
"""What went wrong reading the list, for the dialog to say. None when it was read as it was."""


def addonDir() -> str:
	""":return: the add-on's folder, which holds `manifest.ini`."""
	return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def tablesDir() -> str:
	""":return: the add-on's `brailleTables` folder."""
	return os.path.join(addonDir(), "brailleTables")


def storeDir() -> str:
	import NVDAState

	return os.path.join(NVDAState.WritePaths.configDir, "brlMultiline")


def storePath() -> str:
	return os.path.join(storeDir(), STORE_FILE)


def rulesPath() -> str:
	return os.path.join(tablesDir(), RULES_FILE)


def ourTables() -> list[str]:
	""":return: the file names of every BrlMultiline table, all of which include the rules file."""
	try:
		return sorted(
			name
			for name in os.listdir(tablesDir())
			# Not a candidate `validate` left behind, which ends the same way.
			if name.endswith(TABLE_SUFFIX) and not name.startswith(CHECK_STEM)
		)
	except OSError:
		return []


# Writing


def _writeReplacing(path: str, text: str, keepBackup: bool = False) -> None:
	"""Write a file so that a reader of it finds the old one or the new one, whole.

	:param keepBackup: keep what it replaces as `.bak` beside it.
	:raises OSError: if it could not be written. The old file is then as it was.
	"""
	temporary = f"{path}.tmp"
	with open(temporary, "w", encoding="utf-8", newline="\n") as file:
		file.write(text)
		file.flush()
		os.fsync(file.fileno())
	if keepBackup and os.path.isfile(path):
		shutil.copy2(path, f"{path}.bak")
	os.replace(temporary, path)


# The list


def _stored() -> Store:
	global _store
	if _store is None:
		_store = _read()
	return _store


def entries() -> list:
	""":return: the reader's list, read from the store the first time it is asked for."""
	return list(_stored().entries)


def tables() -> list:
	""":return: the reader's personal tables. See `personalTables`."""
	return list(_stored().tables)


def usesSubstitutions(tableFileName: str) -> bool:
	""":return: whether a braille table includes the reader's substitutions."""
	return tableFileName in ourTables() or tableFileName in {
		personalTables.wrapperName(table.base) for table in tables()
	}


def loadProblem() -> Optional[str]:
	""":return: what went wrong reading the list, in words for the reader, or None."""
	return _loadProblem


def _read() -> Store:
	global _loadProblem
	_loadProblem = None
	path = storePath()
	try:
		with open(path, encoding="utf-8") as file:
			text = file.read()
	except FileNotFoundError:
		return Store([], [])
	except OSError:
		log.error(f"{LOG_PREFIX}could not read {path}", exc_info=True)
		# Translators: said when the braille substitutions file exists but cannot be opened.
		_loadProblem = _("The braille substitutions could not be read. See NVDA's log.")
		return Store([], [])
	try:
		found = brailleSubstitutions.fromText(text)
	except StoreError as error:
		kept = _setAside(path)
		log.error(f"{LOG_PREFIX}{path} could not be read ({error}); kept as {kept}")
		recovered = _readBackup(path)
		if recovered is not None:
			# Put back where it is read from. Otherwise the next start finds no list at all, uses
			# none, and takes away the personal tables it names. The damaged file stays where it was
			# set aside, and the backup is left as it is.
			try:
				_writeReplacing(path, brailleSubstitutions.toText(recovered.entries, recovered.tables))
			except OSError:
				log.error(f"{LOG_PREFIX}could not put the recovered list back at {path}", exc_info=True)
			# Translators: said when the braille substitutions were damaged and the copy saved before
			# them was used. The placeholder is the file the damaged list was kept as.
			_loadProblem = _(
				"The braille substitutions file could not be read, so the copy saved before it was used. "
				"The damaged file was kept as {name}."
			).format(name=os.path.basename(kept or path))
			return recovered
		# Translators: said when the braille substitutions were damaged and there was no copy to use.
		# The placeholder is the file the damaged list was kept as.
		_loadProblem = _("The braille substitutions file could not be read. It was kept as {name}.").format(
			name=os.path.basename(kept or path),
		)
		return Store([], [])
	if found.leftOut:
		log.warning(f"{LOG_PREFIX}{found.leftOut} saved records could not be read and were left out")
	return found


def _readBackup(path: str) -> Optional[Store]:
	try:
		with open(f"{path}.bak", encoding="utf-8") as file:
			return brailleSubstitutions.fromText(file.read())
	except (OSError, StoreError):
		return None


def _setAside(path: str) -> Optional[str]:
	""":return: where a list that could not be read was moved to, or None if it could not be."""
	kept = f"{path}.damaged-{time.strftime('%Y%m%d-%H%M%S')}"
	try:
		os.replace(path, kept)
		return kept
	except OSError:
		log.error(f"{LOG_PREFIX}could not set {path} aside", exc_info=True)
		return None


class NotSaved(Exception):
	"""The list could not be saved. Nothing was changed."""


def save(newEntries: Iterable[Substitution], newTables: Optional[Iterable[PersonalTable]] = None) -> None:
	"""Store the list, and the personal tables.

	Validate first; this does not. See `validate`.

	:param newTables: the personal tables, or None to keep those stored.
	:raises NotSaved: if the list could not be written. The stored list and the rules are as they were.
	"""
	global _store, _loadProblem
	newEntries = list(newEntries)
	newTables = tables() if newTables is None else list(newTables)
	import globalVars

	if globalVars.appArgs.secure:
		raise NotSaved("secure mode")
	try:
		os.makedirs(storeDir(), exist_ok=True)
		_writeReplacing(storePath(), brailleSubstitutions.toText(newEntries, newTables), keepBackup=True)
	except OSError as error:
		log.error(f"{LOG_PREFIX}could not save", exc_info=True)
		raise NotSaved(str(error)) from error
	_store = Store(newEntries, newTables)
	_loadProblem = None


# Validation


class Refusal(NamedTuple):
	"""Something liblouis would not compile."""

	entry: Optional[int]
	"""Which entry it was in, from 0, or None if it could not be told."""

	message: str
	"""What liblouis said."""


_CHECK_LINE = re.compile(re.escape(CHECK_STEM) + r"\.cti:(\d+):\s*(.*)")
_ENTRY_COMMENT = re.compile(r"^# (\d+):")


def validate(candidate: Iterable[Substitution], personal: Iterable[PersonalTable] = ()) -> list:
	""":return: a `Refusal` for each thing liblouis would not compile, or nothing if it compiles.

	Every enabled entry is compiled, whatever profile it is for, with every BrlMultiline table and with
	the NVDA table each personal table is made from: a rule one language's table accepts, another's
	may not. Call on the main thread: it frees liblouis's compiled tables, which NVDA translates with
	there.
	"""
	import louis
	import louisHelper

	text = brailleSubstitutions.rulesText(candidate)
	folder = tablesDir()
	rules = os.path.join(folder, f"{CHECK_STEM}.cti")
	written = [rules]
	messages: list[str] = []

	@louis.LogCallback
	def heard(level, message):
		messages.append(message.decode("ascii", "replace") if isinstance(message, bytes) else str(message))

	refusals = []
	louis.registerLogCallback(heard)
	try:
		with open(rules, "w", encoding="ascii", newline="\n") as file:
			file.write(text)
		checks = []
		for table in ourTables():
			with open(os.path.join(folder, table), encoding="utf-8") as file:
				checks.append(
					(table, file.read().replace(f"include {RULES_FILE}", f"include {CHECK_STEM}.cti"))
				)
		for table in personal:
			name = personalTables.wrapperName(table.base)
			checks.append(
				(name, personalTables.wrapperText(table.base).replace(RULES_FILE, f"{CHECK_STEM}.cti"))
			)
		for table, source in checks:
			check = os.path.join(folder, f"{CHECK_STEM}.{table}")
			written.append(check)
			with open(check, "w", encoding="utf-8", newline="\n") as file:
				file.write(source)
			messages.clear()
			try:
				louis.checkTable([check])
			except RuntimeError:
				refusals += _refusalsFrom(messages, text) or [Refusal(None, "; ".join(messages[-3:]))]
				# The first table to refuse says what is wrong; the others would say it again.
				break
	except OSError as error:
		log.error(f"{LOG_PREFIX}could not write the rules to check them", exc_info=True)
		refusals.append(Refusal(None, str(error)))
	finally:
		louis.registerLogCallback(louisHelper.louis_log)
		for path in written:
			try:
				os.remove(path)
			except OSError:
				pass
		# The candidates are compiled and kept; the real tables are not affected, but there is no
		# freeing one table alone.
		_freeCompiledTables()
	return refusals


def _refusalsFrom(messages: list, text: str) -> list:
	""":return: the refusals in what liblouis said, each with the entry its line belongs to."""
	lines = text.split("\n")
	refusals = []
	for message in messages:
		found = _CHECK_LINE.search(message)
		if not found:
			continue
		lineNumber, said = int(found.group(1)), found.group(2)
		entry = None
		for line in reversed(lines[: max(0, lineNumber)]):
			comment = _ENTRY_COMMENT.match(line)
			if comment:
				entry = int(comment.group(1)) - 1
				break
		if not any(refusal.entry == entry for refusal in refusals):
			refusals.append(Refusal(entry, said))
	return refusals


# Putting the rules in force


def _freeCompiledTables() -> None:
	"""Make liblouis compile its tables again the next time it translates. See the module's notes."""
	import louis

	louis.liblouis.lou_free()


def activeProfiles() -> list[str]:
	""":return: the names of the configuration profiles in force, the base configuration left out."""
	try:
		return [profile.name for profile in config.conf.profiles[1:] if profile.name]
	except Exception:
		log.debugWarning(f"{LOG_PREFIX}could not tell which profiles are in force", exc_info=True)
		return []


class NotApplied(Exception):
	"""The rules file could not be written, so the rules in force are the ones before."""


def apply() -> bool:
	"""Write the rules for the profiles in force, if they are not what the rules file holds.

	:return: whether the rules changed. Translation then uses them from the next line on; what is on
		the display was translated before, and is for the caller to draw again.
	:raises NotApplied: if the file could not be written. It was once taken for "nothing changed",
		and the dialog closed on a list that was saved and not in use.
	"""
	text = brailleSubstitutions.rulesText(entries(), activeProfiles())
	path = rulesPath()
	try:
		with open(path, encoding="utf-8") as file:
			if file.read() == text:
				return False
	except OSError:
		pass
	try:
		_writeReplacing(path, text)
	except OSError as error:
		log.error(f"{LOG_PREFIX}could not write {path}", exc_info=True)
		raise NotApplied(str(error)) from error
	_freeCompiledTables()
	brailleSubstitutions.rulesChanged()
	log.debug(f"{LOG_PREFIX}rules written for profiles {activeProfiles()}")
	return True


def syncPersonalTables() -> bool:
	"""Make the personal tables and the manifest's list of them match the reader's list.

	:return: whether NVDA has to restart to have the personal tables the reader's list asks for:
		either they were just written, or they were written before and NVDA has not restarted since.
	:raises personalTables.NotListed: if the manifest could not be changed. It is as it was.
	"""
	import globalVars

	if globalVars.appArgs.secure:
		return False
	wanted = tables()
	personalTables.sync(addonDir(), wanted, inUse=_knownPersonalTables())
	return restartNeeded(wanted)


def _knownPersonalTables() -> set:
	""":return: the personal tables NVDA has now, by file name: those it read as it started."""
	import brailleTables

	return {
		table.fileName
		for table in brailleTables.listTables()
		if table.fileName.endswith(personalTables.WRAPPER_SUFFIX)
	}


def restartNeeded(wanted: Iterable[PersonalTable]) -> bool:
	""":return: whether the personal tables NVDA knows differ from those wanted."""
	return _knownPersonalTables() != {personalTables.wrapperName(table.base) for table in wanted}


_onChanged: Optional[Callable[[], None]] = None


def install(onChanged: Callable[[], None]) -> bool:
	"""Put the reader's rules in force, and again whenever the profile changes.

	:param onChanged: called when the rules in force change, to draw the display again.
	:return: whether NVDA has to restart for the reader's personal tables. Only so after the add-on
		was updated or its manifest made again, since saving the list asks then.
	"""
	global _onChanged
	_onChanged = onChanged
	try:
		if apply():
			onChanged()
	except NotApplied:
		# Logged; the rules from before stay in force, and the next profile switch or save tries again.
		pass
	config.post_configProfileSwitch.register(_handleProfileSwitch)
	try:
		return syncPersonalTables()
	except personalTables.NotListed:
		log.error(f"{LOG_PREFIX}could not list the personal tables in the manifest", exc_info=True)
		return False


def remove() -> None:
	global _onChanged
	config.post_configProfileSwitch.unregister(_handleProfileSwitch)
	_onChanged = None


def _handleProfileSwitch(**kwargs) -> None:
	try:
		changed = apply()
	except NotApplied:
		# Logged. The rules of the profile before stay in force until a switch or a save writes them.
		return
	if changed and _onChanged is not None:
		_onChanged()


class NotListed(Exception):
	"""The list was saved and is in force, but the personal tables could not be listed for NVDA."""


def commit(newEntries: Iterable[Substitution], newTables: Optional[Iterable[PersonalTable]] = None) -> bool:
	"""Save a list the dialog has validated, and put it in force.

	:param newTables: the personal tables, or None to keep those stored.
	:return: whether NVDA has to restart to show the personal tables as they now are.
	:raises NotSaved: if it could not be saved. Nothing was changed.
	:raises NotApplied: if it was saved but the rules could not be put in force. Committing again
		tries again.
	:raises NotListed: if it was saved but the manifest could not be changed.
	"""
	save(newEntries, newTables)
	if apply() and _onChanged is not None:
		_onChanged()
	try:
		return syncPersonalTables()
	except personalTables.NotListed as error:
		log.error(f"{LOG_PREFIX}could not list the personal tables in the manifest: {error}")
		raise NotListed(str(error)) from error
	except OSError as error:
		log.error(f"{LOG_PREFIX}could not write the personal tables", exc_info=True)
		raise NotListed(str(error)) from error
