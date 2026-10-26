# BrlMultiline: input from several displays at once.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Making keys on every member work, without disturbing what already works.

A member's keys reach NVDA on their own, as `br(<its driver name>):<key id>`, since drivers
call `inputCore.manager.executeGesture` from their own read loop. So every shipped gesture map
works unchanged; this module must not spoil that. See "Gestures already arrive without help"
in docs/design/virtual-display-plan.md.

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

import threading

import braille
import inputCore
from braille.display.gesture import BrailleDisplayGesture
from logHandler import log

from .virtualLayout import deviceCellIndexToVirtual

_driver = None
"""The live virtual display, or None when this module is not installed."""

_currentGesture = threading.local()
"""Which member raised the gesture currently being resolved.

`BrailleDisplayGesture._get_script` does not say which display a gesture came from, so the
source is noted in the decider and read back within the same synchronous `executeGesture`
call. Recorded for every braille gesture, members or not.
"""


class MemberGestureMap(inputCore.GlobalGestureMap):
	"""A gesture map that answers out of the members' own maps, as they are now.

	A live view, since `freedomScientific` rewrites its map when the wiz wheel action changes.
	A `GlobalGestureMap` subclass, for `isinstance` checks, with only the two lookups
	overridden. Identifiers are namespaced by driver, so chaining is the whole merge.
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


def _rebaseCellIndexes(gesture: BrailleDisplayGesture, slot) -> bool:
	"""Move a gesture's cell indexes from its own display onto the composite.

	Indexes arrive flat over the member's own rows. The identifier is frozen first, since
	`_get__cellIndexesStr` builds `routing40` and the like from `cellIndexes` and a key bound by
	number would match a different cell. An instance attribute shadows `baseObject.Getter`.

	:param gesture: the gesture to rebase, modified in place.
	:param slot: the member that produced it.
	:return: whether the gesture should go on to execute.
	"""
	# Read before anything moves, assign so that nothing recomputes it afterwards.
	frozenIndexesStr = gesture._cellIndexesStr
	try:
		rebased = [
			deviceCellIndexToVirtual(index, slot.band, _driver.numCols) for index in gesture.cellIndexes
		]
	except ValueError:
		# A cell the member does not have, so there is no honest composite position for it.
		# Leaving it alone is not the safe option it looks like: an out of range index on a
		# narrow member is still a perfectly valid index into the composite, so an untouched
		# gesture would route confidently to some other display's cell. Cancelling is the
		# only answer that cannot act on a cell the user did not press.
		log.warning(
			f"BrlMultiline: {slot.driverName} reported cells {gesture.cellIndexes}, which do not "
			f"fit its {slot.band.numCells} cells. Ignoring the press rather than routing elsewhere.",
		)
		return False
	gesture._cellIndexesStr = frozenIndexesStr
	gesture.cellIndexes = rebased
	# No cache to invalidate: `identifiers` may already have been built, but it was built
	# from the string just frozen, so it is the identifier that was wanted either way.
	return True


def _isDisplayInstalled() -> bool:
	"""Whether NVDA has actually put this virtual display in place yet.

	This module goes live in the constructor, while the outgoing display is still NVDA's and
	routed against. Identity cannot tell, since `sameDisplayReInit` reconstructs the same
	instance; the driver's `_installed`, set in `initSettings` and cleared in `terminate`, can.
	See `BrailleDisplayDriver.initSettings`.

	:return: True once NVDA is driving this virtual display.
	"""
	handler = braille.handler
	if handler is None or handler.display is not _driver:
		return False
	return bool(getattr(_driver, "_installed", False))


def _translateCellIndexes(gesture=None, **kwargs) -> bool:
	"""Rebase a member's routing keys. Registered on `inputCore.decide_executeGesture`.

	It runs before `gesture.script` is resolved, and NVDA's remote client uses it too. Cancels
	only a member's cell gesture that cannot be rebased. Never raises, since that would break
	input for the session.
	"""
	try:
		if _driver is None or not isinstance(gesture, BrailleDisplayGesture):
			return True
		if not _isDisplayInstalled():
			# Mid switch: the outgoing display is still NVDA's, and should go on interpreting
			# its own cell indexes and answering for its own modifiers.
			return True
		# Noted for every braille gesture, member or not: see `_currentGesture`.
		_currentGesture.source = gesture.source
		slot = _driver.slotForDriverName(gesture.source)
		if slot is None or not gesture.cellIndexes:
			return True
		return _rebaseCellIndexes(gesture, slot)
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
	# First, since `Decider` stops at the first False and NVDA Remote returns False for every
	# braille gesture it forwards; Remote should forward the composite position anyway.
	inputCore.decide_executeGesture.moveToEnd(_translateCellIndexes, last=False)
	log.debug("BrlMultiline: gesture translation installed, ahead of any other decider")


def remove() -> None:
	"""Stop. Safe to call when nothing is installed."""
	global _driver
	if _driver is None:
		return
	_driver = None
	inputCore.decide_executeGesture.unregister(_translateCellIndexes)
	log.debug("BrlMultiline: gesture translation removed")


def modifierGesturesForMembers(driver, model=None):
	"""Give the modifier gestures of the member that raised the gesture being resolved.

	Only that member's: what is yielded has lost its driver namespace and the caller matches on
	key names, so another member's modifiers could apply. With the source unknown, nothing is
	yielded, so the gesture does nothing rather than something else.

	:param driver: the virtual display.
	:param model: the optional display model, passed through unchanged.
	:return: generator of (key ids, modifier names), as NVDA's own does.
	"""
	source = getattr(_currentGesture, "source", None)
	slot = driver.slotForDriverName(source) if source else None
	if slot is None:
		log.debugWarning(f"BrlMultiline: no member for gesture source {source!r}, offering no modifiers")
		return
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

	Mirrors `scriptHandler._getObjScript` with the member as the object, so a user binding to a
	member driver's script still passes its `isinstance` check.

	:param driver: the virtual display.
	:param gesture: the gesture being resolved.
	:return: the bound script, or None.
	"""
	import baseObject
	import scriptHandler

	source = getattr(gesture, "source", None)
	if source is None:
		return None
	slot = driver.slotForDriverName(source)
	if slot is None or not isinstance(slot.driver, baseObject.ScriptableObject):
		return None
	member = slot.driver
	try:
		for cls, scriptName in scriptHandler.getGlobalMapScripts(gesture):
			if not isinstance(member, cls):
				continue
			if scriptName is None:
				# The user has explicitly unbound this gesture for this class.
				return None
			script = getattr(member, f"script_{scriptName}", None)
			if script is not None:
				return script
		return member.getScript(gesture)
	except Exception:
		log.error(f"BrlMultiline: error finding a script on {slot.driverName}", exc_info=True)
		return None
