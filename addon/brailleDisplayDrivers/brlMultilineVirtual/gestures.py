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

`BrailleDisplayGesture._get_script` asks the display for modifier gestures without saying
which display the gesture came from, and the answer differs per member. The source is
therefore noted as the gesture passes through the decider and read back a few lines later in
the same `executeGesture` call, on the same thread — braille gestures are dispatched from
their driver's read loop, and `executeGesture` is synchronous, so there is nothing to
interleave between the two.

Recorded for every braille gesture, including ones from displays that are not members, so
that a gesture from elsewhere cannot be answered with the last member's modifiers.
"""


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


def _rebaseCellIndexes(gesture: BrailleDisplayGesture, slot) -> bool:
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

	Installed from the constructor, this module goes live some way before NVDA agrees that
	the virtual display exists: `_setDisplay` assigns `braille.handler.display` only after
	the constructor has returned, the outgoing display has been terminated and `initSettings`
	has run. Throughout that window the *outgoing* display is still NVDA's, still dispatching
	input, and still being routed against — and when it was adopted as a member, it is the
	very display whose indexes would be rebased.

	A routing key pressed in that window would therefore be rebased onto a composite that
	nothing is yet reading, and routed against a buffer still sized for the display the user
	is leaving. Small window; wrong answer; cheap to exclude.

	Identity alone does not answer it, and the case that proves it is NVDA reselecting a
	display that is already in use — which is what reopening the composite after its member
	list is edited does. `_switchDisplay` takes its `sameDisplayReInit` path there and
	reconstructs *this same instance*, so `handler.display is _driver` never stops being true
	while the members, the bands and the geometry are all replaced. The driver's own
	`_installed`, set in `initSettings` and cleared in `terminate`, is what marks the interval.

	:return: True once NVDA is driving this virtual display.
	"""
	handler = braille.handler
	if handler is None or handler.display is not _driver:
		return False
	return bool(getattr(_driver, "_installed", False))


def _translateCellIndexes(gesture=None, **kwargs) -> bool:
	"""Rebase a member's routing keys. Registered on `inputCore.decide_executeGesture`.

	That extension point is the right hook because it runs before `gesture.script` is
	resolved and before any identifier is consumed, and because NVDA's own remote client
	already uses it to intercept braille gestures, so it is a supported pattern rather than a
	trick.

	It cancels exactly one thing: a member's cell addressed gesture whose cells cannot be
	rebased, for the reason given in `_rebaseCellIndexes`. Everything else passes through.
	It must never raise, since a decider that throws would break input for the whole session,
	so an unexpected error lets the gesture through unchanged.
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
	# `Decider` stops at the first handler returning False, and NVDA Remote's handler on this
	# same extension point returns False for every braille gesture once it has forwarded it.
	# Registered after Remote, this would never run, and a routing key on the second display
	# would be sent as a cell index belonging to the first. Going first is also the right
	# order on its own terms: Remote should forward the composite position, since the
	# composite is the display it has told the other machine about.
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

	Only that member's, which is the correction. Chaining every member looked right — each
	one's `_getModifierGestures` filters its own map by its own `br(name):` prefix — but what
	it yields is stripped of that namespace: bare sets of key names and modifier names. The
	caller then matches on key names alone::

		for keys, modifiers in braille.handler.display._getModifierGestures(self.model):
			if keys < gestureKeys:
				gestureModifiers |= modifiers

	So a Monarch mapping and a Focus gesture sharing a key name would combine, and a press on
	one display would pick up a modifier defined for the other — silently executing a
	keyboard shortcut nobody asked for. The default maps happen not to collide, but a user's
	own mappings easily could, and other display combinations more so.

	When the source is unknown, nothing is yielded rather than everything. Modifier emulation
	then simply does not resolve, which is a gesture that does nothing — much better than a
	gesture that does something else.

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

	This mirrors `scriptHandler._getObjScript` with the member standing in for the object
	NVDA would have offered, rather than only calling `getScript`. The difference is user
	bindings: `_getObjScript` first walks the global maps for an entry whose class the object
	is an instance of, and a user who has bound a key to a member driver's own script has
	written exactly such an entry. Tested against the virtual display, `isinstance` fails and
	the binding is lost; tested against the member, it works as it always did.

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
