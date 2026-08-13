# BrlMultiline: input from several displays at once.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Making keys on every member work, without disturbing what already works.

Phase 0 established the good news on hardware: a member's keys reach NVDA on their own.
Drivers dispatch input by constructing a gesture and calling
`inputCore.manager.executeGesture` from their own read loop, and nothing in that path
consults `braille.handler.display`. Every member's gestures therefore arrive already
carrying `br(<its own driver name>):<its own key id>`, which means **every default gesture
map NVDA ships keeps working on both displays with no mapping of any kind**. That is the
single most valuable finding of the whole spike, and this module exists to avoid spoiling it.

Three things do consult `braille.handler.display` while a gesture is resolved, and each is
answered here:

1. `scriptHandler.getGlobalMapScripts` and `inputCore._AllGestureMappingsRetriever` read
   `braille.handler.display.gestureMap` — one map, where there should be all the members'.
   :class:`MemberGestureMap` answers out of them live.
2. `scriptHandler._yieldObjectsForFindScript` offers `braille.handler.display` for a driver's
   own scripts. That is now this driver, so `getScript` has to hand the gesture to the member
   that raised it. The Focus is a `ScriptableObject`; the Monarch is not.
3. `BrailleDisplayGesture._get_script` calls
   `braille.handler.display._getModifierGestures(model)` when turning a multi key gesture
   into an emulated keyboard shortcut.

And one thing needs correcting rather than delegating: routing keys carry a cell index
relative to the display that produced them, which has to be rebased onto the composite
before `globalCommands.script_braille_routeTo` passes it to `braille.handler.routeTo`.
"""

from __future__ import annotations

import inputCore
from braille.display.gesture import BrailleDisplayGesture
from logHandler import log

from .virtualLayout import deviceCellIndexToVirtual

_driver = None
"""The live virtual display, or None when this module is not installed."""


class MemberGestureMap(inputCore.GlobalGestureMap):
	"""A gesture map that answers out of the members' own maps, as they are now.

	Deliberately a live view rather than a merged copy. `freedomScientific` rewrites its own
	map whenever the user cycles what the wiz wheels do — `gestureMap.add(..., replace=True)`
	in `script_toggleLeftWizWheelAction` — so a snapshot taken when the display was opened
	would be wrong the first time that command was used.

	Subclassing `GlobalGestureMap` rather than duck typing it keeps any `isinstance` check in
	NVDA or another add-on honest. Its own map stays empty; only the two lookups are
	overridden.

	Members cannot collide, because every identifier is namespaced by its driver's name, so
	chaining them is the whole of the merge.
	"""

	def __init__(self, driver):
		"""
		:param driver: the virtual display whose members should be consulted.
		"""
		super().__init__()
		self._driver = driver

	def _memberMaps(self):
		"""Yield each member's gesture map, skipping members that have none."""
		for slot in self._driver.slots:
			gestureMap = getattr(slot.driver, "gestureMap", None)
			if gestureMap is not None:
				yield gestureMap

	def getScriptsForGesture(self, gesture: str):
		for gestureMap in self._memberMaps():
			yield from gestureMap.getScriptsForGesture(gesture)

	def getScriptsForAllGestures(self):
		for gestureMap in self._memberMaps():
			yield from gestureMap.getScriptsForAllGestures()


def _rebaseCellIndexes(gesture: BrailleDisplayGesture, slot) -> None:
	"""Move a gesture's cell indexes from its own display onto the composite.

	Phase 0 confirmed the shape of what arrives: a Monarch reported 2, 39 and 102 for presses
	spread across its rows, flat over its own 8 by 32. Those have to become positions in the
	9 by 80 composite before anything routes with them.

	The identifier is frozen first. `_get__cellIndexesStr` builds `routing40` and the like out
	of `cellIndexes`, so rebasing without freezing would silently rewrite the identifier the
	user's own gesture map is keyed on — a routing key bound by number would stop matching,
	and worse, would start matching a different cell. `Getter` in `baseObject` defines only
	`__get__`, so it is a non data descriptor and an instance attribute shadows it.

	:param gesture: the gesture to rebase, modified in place.
	:param slot: the member that produced it.
	"""
	# Read before anything moves, assign so that nothing recomputes it afterwards.
	frozenIndexesStr = gesture._cellIndexesStr
	try:
		rebased = [
			deviceCellIndexToVirtual(index, slot.band, _driver.numCols) for index in gesture.cellIndexes
		]
	except ValueError:
		# A cell the member does not have. Not something to route with, and not something to
		# guess about, so the gesture is left exactly as it arrived.
		log.debugWarning(
			f"BrlMultiline: {slot.driverName} reported cells {gesture.cellIndexes}, "
			f"which do not fit its {slot.band.numCells} cells",
			exc_info=True,
		)
		return
	gesture._cellIndexesStr = frozenIndexesStr
	gesture.cellIndexes = rebased
	# No cache to invalidate: `identifiers` may already have been built, but it was built
	# from the string just frozen, so it is the identifier that was wanted either way.


def _translateCellIndexes(gesture=None, **kwargs) -> bool:
	"""Rebase a member's routing keys. Registered on `inputCore.decide_executeGesture`.

	That extension point is the right hook because it runs before `gesture.script` is
	resolved and before any identifier is consumed, and because NVDA's own remote client
	already uses it to intercept braille gestures, so it is a supported pattern rather than a
	trick.

	Always returns True: this corrects gestures, it never cancels them. It must also never
	raise, since a decider that throws would break input for the whole session.
	"""
	try:
		if _driver is None or not isinstance(gesture, BrailleDisplayGesture):
			return True
		if not gesture.cellIndexes:
			return True
		slot = _driver.slotForDriverName(gesture.source)
		if slot is not None:
			_rebaseCellIndexes(gesture, slot)
	except Exception:
		log.error("BrlMultiline: error rebasing a gesture's cell indexes", exc_info=True)
	return True


def install(driver) -> None:
	"""Start correcting and delegating input for this virtual display.

	:param driver: the live virtual display.
	"""
	global _driver
	if _driver is not None:
		remove()
	_driver = driver
	inputCore.decide_executeGesture.register(_translateCellIndexes)
	log.debug("BrlMultiline: gesture translation installed")


def remove() -> None:
	"""Stop. Safe to call when nothing is installed."""
	global _driver
	if _driver is None:
		return
	_driver = None
	inputCore.decide_executeGesture.unregister(_translateCellIndexes)
	log.debug("BrlMultiline: gesture translation removed")


def modifierGesturesForMembers(driver, model=None):
	"""Chain every member's modifier gestures.

	`BrailleDisplayDriver._getModifierGestures` is a classmethod that filters on `cls.name`,
	so each member sees only its own `br(name):` entries and the union is exactly right.

	:param driver: the virtual display.
	:param model: the optional display model, passed through unchanged.
	:return: generator of (key ids, modifier names), as NVDA's own does.
	"""
	for slot in driver.slots:
		try:
			yield from type(slot.driver)._getModifierGestures(model)
		except Exception:
			log.debugWarning(
				f"BrlMultiline: could not read modifier gestures from {slot.driverName}",
				exc_info=True,
			)


def scriptForMember(driver, gesture):
	"""Find a member's own script for a gesture it raised.

	NVDA offers `braille.handler.display` for display specific scripts, and that is now the
	virtual display rather than the member that has them. `freedomScientific` is such a
	member; `hidBrailleStandard` is not, and is skipped by the `ScriptableObject` check.

	:param driver: the virtual display.
	:param gesture: the gesture being resolved.
	:return: the bound script, or None.
	"""
	import baseObject

	source = getattr(gesture, "source", None)
	if source is None:
		return None
	slot = driver.slotForDriverName(source)
	if slot is None or not isinstance(slot.driver, baseObject.ScriptableObject):
		return None
	try:
		return slot.driver.getScript(gesture)
	except Exception:
		log.error(f"BrlMultiline: error finding a script on {slot.driverName}", exc_info=True)
		return None
