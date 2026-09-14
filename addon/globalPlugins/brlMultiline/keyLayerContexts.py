# BrlMultiline: what is on the display, as far as choosing a layer of keys is concerned.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which contexts are present, most specific first, for the context aware layer key.

The list is `keyLayers.CONTEXTS`: chart, picture, graphics, table. A chart is also graphics, so
both are present while one is on the display, and the layer key takes the most specific one the
device has a layer for.

**Graphics is followed; a table is only looked for.** The three graphics contexts are cheap
attribute reads on graphics mode, and graphics mode says when they change, so a layer for one comes
on by itself with its drawing and goes when the drawing does. A table is asked about only when the
reader presses the layer key: finding one means reading where the browse mode caret is, which is
not something to do on every change, and a table is somewhere the caret passes through, so keys
that changed meaning as it crossed a table's edge would be a mode the reader never asked for. That
is `tableArrows`' argument, and it holds here. See `docs/design/layered-keys-plan.md`.

Every test here is guarded: a context that cannot be read is absent, never an error, because the
layer key has to work whatever else on the display has gone wrong.
"""

from logHandler import log

DETECTED = ("chart", "picture", "graphics")
"""The contexts followed as they change. A layer for any other is never turned on or off for its
context arriving or going, since the change is not watched for."""


def presentContexts(plugin, tables: bool = True) -> list:
	""":return: the contexts present now, most specific first.

	:param plugin: the running global plugin, or None.
	:param tables: whether to look for a table too. False where only the followed contexts matter,
		which is every time graphics mode changes, so the caret is not read then.
	"""
	present = _graphics(plugin)
	if tables and _inTable(plugin):
		present.append("table")
	return present


def _graphics(plugin) -> list:
	present = []
	try:
		mode = getattr(plugin, "graphicsMode", None)
		if mode is None or not mode.active:
			return present
		source = mode.source
		picture = getattr(plugin, "_pictureDrawing", None)
		if source is not None and picture is not None and source is picture:
			present.append("picture")
		elif source is not None and getattr(source, "redraw", None) is not None:
			# Only a drawing composed from numbers can be composed again, and a chart is the one
			# kind that is. See `graphicsMode.Drawing`.
			present.append("chart")
		present.append("graphics")
	except Exception:
		log.debugWarning("BrlMultiline key layers: could not read the graphics context", exc_info=True)
		return []
	return present


def _inTable(plugin) -> bool:
	""":return: whether there is a table to read: laid out in columns on the display, or under the
	browse mode caret."""
	try:
		tablesInColumns = getattr(plugin, "tablesInColumns", None)
		if tablesInColumns is not None and tablesInColumns():
			return True
	except Exception:
		log.debugWarning("BrlMultiline key layers: could not ask for tables in columns", exc_info=True)
	try:
		return _caretInTable()
	except Exception:
		log.debugWarning(
			"BrlMultiline key layers: could not tell whether the caret is in a table", exc_info=True
		)
		return False


def _caretInTable() -> bool:
	"""Whether the browse mode caret is in a table cell, asked the way `tableArrows` asks it.

	Directly through `_getTableCellCoords`, which raises `LookupError` outside a table, rather than
	through NVDA's table movement, which says "not in a table cell" out loud.
	"""
	import api

	try:
		from documentBase import DocumentWithTableNavigation
	except ImportError:  # pragma: no cover - an NVDA whose table navigation has moved.
		return False
	focus = api.getFocusObject()
	document = getattr(focus, "treeInterceptor", None)
	if (
		document is None
		or not isinstance(document, DocumentWithTableNavigation)
		or not getattr(document, "isReady", False)
		or getattr(document, "passThrough", False)
	):
		return False
	try:
		document._getTableCellCoords(document.selection)
	except LookupError:
		return False
	return True
