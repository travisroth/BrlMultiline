# BrlMultiline: what is on the display, as far as choosing a layer of keys is concerned.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which contexts are present, most specific first, for the context aware layer key.

The list is `keyLayers.CONTEXTS`: chart, picture, graphics, table. A chart is also graphics, so
both are present while one is on the display, and the layer key takes the most specific one the
device has a layer for.

**Phase 1 answers the three graphics contexts and not tables.** They are cheap attribute reads
on graphics mode, and without them a graphics layer could only be reached through a list. Tables
need recognising in the flow and in browse mode, and wait for phase 3 with automatic enabling.
See `docs/design/layered-keys-plan.md`.

Every test here is guarded: a context that cannot be read is absent, never an error, because the
layer key has to work whatever else on the display has gone wrong.
"""

from logHandler import log


def presentContexts(plugin) -> list:
	""":return: the contexts present now, most specific first.

	:param plugin: the running global plugin, or None.
	"""
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
	return present
