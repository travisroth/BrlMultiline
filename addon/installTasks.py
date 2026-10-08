# BrlMultiline: what installing the add-on does beyond copying it.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Give a newly installed copy of the add-on the reader's braille substitutions and personal tables.

**The substitutions file.** Every BrlMultiline braille table includes `brlMultiline-user.cti`, which
the add-on writes while it runs (see `globalPlugins/brlMultiline/userBrailleTable.py`), so it is not
in the package. A table that includes a file that is not there does not compile, and NVDA can
translate before the add-on's plugin has run, so installing leaves one in its place. The plugin
writes the reader's rules over it when it starts.

**The personal tables.** A new copy of the add-on brings its own manifest, with none of the reader's
personal tables in it, and NVDA reads which tables there are only when it starts, before the plugin
runs. Made again here, into the copy being installed, they are there when NVDA restarts into it.
See `globalPlugins/brlMultiline/personalTables.py`.

The two modules are loaded from their files rather than imported, since the add-on is not running
yet, and neither needs the rest of it.
"""

import importlib.util
import os

RULES_FILE = "brlMultiline-user.cti"
HERE = os.path.dirname(__file__)


def _load(name: str):
	path = os.path.join(HERE, "globalPlugins", "brlMultiline", f"{name}.py")
	spec = importlib.util.spec_from_file_location(f"brlMultilineInstall_{name}", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def onInstall() -> None:
	path = os.path.join(HERE, "brailleTables", RULES_FILE)
	if not os.path.exists(path):
		with open(path, "w", encoding="ascii", newline="\n") as file:
			file.write("# No substitutions yet. BrlMultiline writes the reader's here when it starts.\n")
	try:
		_restorePersonalTables()
	except Exception:
		# Never a reason to refuse the install. The plugin makes them again when it starts, and says
		# NVDA has to restart for them.
		from logHandler import log

		log.error("BrlMultiline: could not make the personal braille tables while installing", exc_info=True)


def _restorePersonalTables() -> None:
	import NVDAState

	store = os.path.join(NVDAState.WritePaths.configDir, "brlMultiline", "brailleSubstitutions.json")
	if not os.path.isfile(store):
		return
	substitutions = _load("brailleSubstitutions")
	personal = _load("personalTables")
	with open(store, encoding="utf-8") as file:
		tables = substitutions.fromText(file.read()).tables
	if tables:
		personal.sync(HERE, tables)
