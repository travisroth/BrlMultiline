# BrlMultiline: patches to NVDA's braille handler.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""The places NVDA's braille handler has to be adjusted.

1. `_doNewObject` clears the whole buffer and appends every region to it. With several
	segments the regions have to be sorted first, and only the segments actually
	receiving regions may be cleared.
2. `scrollForward` and `scrollBack` pan the display whose panning key was pressed, in the
	direction that display is configured for.
3. `_handlePendingUpdate` refreshes the document line regions and the pinned objects, neither
	of which NVDA has any reason to mark as needing an update.

All are installed and removed symmetrically, so that disabling the add-on restores NVDA's own
behaviour without a restart. Symmetrically, but not unconditionally: these are attributes of a
class anyone can reach, and a method that is no longer the one this module installed belongs
to whoever put it there. See L{remove}.
"""

import time

import config
import keyboardHandler
from braille.brailleHandler import BrailleHandler
from braille.constants import CONTEXTPRES_CHANGEDCONTEXT
from config.configFlags import TetherTo
from logHandler import log

from . import bmConfig, documentLines, panning
from .container import DisplayContainer

_originals: dict[str, object] = {}
"""NVDA's own methods, by the name each one is patched under.

An entry lives as long as this module's replacement is in place, and outlives L{remove} when
the replacement could not be taken back — because in that case it is still being delegated to.
"""

_installedMethods: dict[str, object] = {}
"""What was put in their place, so that L{remove} can tell whether it is still there."""

MONITOR_REFRESH_INTERVAL = 0.4
"""Seconds between re-reads of a pinned object.

Fast enough that a pinned clock or status line is not visibly stale, slow enough that
reading a document's text through a `TextInfo` is not being done on every core cycle.
"""

_lastMonitorRefresh = 0.0


def _applyFocusToHardLeft(handler: BrailleHandler, regions: list) -> None:
	"""Apply NVDA's focusToHardLeft rule to one group of regions.

	Taken from `BrailleHandler._doNewObject`. Applying it per segment rather than across
	the whole display is the point: the rule is about a region's position within the
	group it is displayed with.
	"""
	if not (
		handler.getTether() == TetherTo.FOCUS.value
		and config.conf["braille"]["focusContextPresentation"] == CONTEXTPRES_CHANGEDCONTEXT
	):
		return
	focusToHardLeftSet = False
	for region in regions:
		if region.focusToHardLeft:
			focusToHardLeftSet = True
		elif not focusToHardLeftSet and getattr(region, "_focusAncestorIndex", None) is None:
			# Displaying a new object with the same ancestry as the previous one, so
			# anchor this region at the left of its segment.
			region.focusToHardLeft = True
			focusToHardLeftSet = True


def _doNewObjectMultiSegment(self: BrailleHandler, regions) -> None:
	"""Replacement for `BrailleHandler._doNewObject` that knows about segments."""
	container = self.mainBuffer
	if not isinstance(container, DisplayContainer) or container.numSegments == 1:
		# Nothing to sort. Let NVDA do exactly what it normally does.
		return _originals["_doNewObject"](self, regions)
	self.autoScroll(enable=False)
	grouped: dict[int, list] = {index: [] for index in range(container.numSegments)}
	for region in regions:
		index = container.resolvePlacementTarget(region)
		if index is None:
			# The region names a segment that no longer exists, so the claim that owned it
			# has gone. Dropping it is the point: showing it in the focus segment would
			# write a departed panel's content over the user's ordinary braille.
			continue
		# Note where it went, so that later calls handed a region but no segment (focus,
		# scrollTo) can find their way back. Recorded separately from `targetSegment`,
		# which belongs to whoever claimed the region, not to this bookkeeping.
		container.recordPlacement(region, index)
		grouped[index].append(region)
	for index, group in grouped.items():
		if not group:
			# A segment receiving nothing keeps what it already had.
			continue
		if _ownerTookTheFocus(container.segments[index], group):
			# The segment's owner draws its own focus content, so NVDA's regions are handed
			# over rather than written in beside what the owner drew. Without this the two
			# producers fight, and this loop wins by clearing the segment on every focus
			# change. See `SegmentSpec.exclusive`.
			grouped[index] = []
			continue
		container.clear(index)
		_applyFocusToHardLeft(self, group)
		for region in group:
			container.segments[index].append(region)
	container.update()
	for index, group in grouped.items():
		if not group:
			continue
		# The last region of each group receives focus within its own segment.
		container.focus(group[-1])
		self.scrollToCursorOrSelection(group[-1])
	_populateDocumentLines(container)
	if self.buffer is self.mainBuffer:
		self.update()
	elif self.buffer is self.messageBuffer and keyboardHandler.keyCounter > self._keyCountForLastMessage:
		self._dismissMessage()


def _ownerTookTheFocus(segment, regions) -> bool:
	"""Offer a focus change to a segment whose owner draws its own content.

	:param segment: the segment the regions were destined for.
	:param regions: the regions NVDA built for the new focus.
	:return: whether the owner dealt with them.
	"""
	accept = getattr(segment, "acceptFocusRegions", None)
	if accept is None:
		return False
	try:
		return bool(accept(regions))
	except Exception:
		log.debugWarning(f"An owner could not take the focus in {segment!r}", exc_info=True)
		return False


def _getMonitoredKeys() -> set[str]:
	""":return: the keys of segments holding a pinned object, which must not be written over.

	This is the runtime half of the reservation picture. The other half, panels that
	reserved their segments when the view was composed, is carried on the segments
	themselves and does not have to be gathered here.
	"""
	from . import getPlugin

	plugin = getPlugin()
	if plugin is None:
		return set()
	return set(plugin.monitoredKeys)


def _populateDocumentLines(container: DisplayContainer) -> None:
	"""Fill the free segments with the document lines around the caret, if configured.

	Also takes back lines the add-on placed but should no longer be showing, whether
	because the focus has left the document or because the setting has been turned off.
	"""
	if container.numSegments == 1:
		return
	if bmConfig.isSpeechOutputMode():
		# The display is showing speech. Lines written now would stay under the reader's
		# fingers beside it, since nothing handles caret moves in this mode.
		return
	monitored = _getMonitoredKeys()
	try:
		if bmConfig.shouldShowDocumentLines():
			changed = documentLines.populate(container, monitored)
		else:
			changed = documentLines.clearDocumentRegions(container, monitored)
		if changed:
			container.update()
	except Exception:
		log.debugWarning("Could not show document lines", exc_info=True)


def _refreshPinnedObjects() -> None:
	"""Re-read pinned objects, so that a pin shows the object as it is now.

	A pin shows something in a window the user is not working in, and nothing NVDA does
	announces that it has changed: there is no focus or caret event to redraw from, and
	events in a background window are not delivered at all unless something has asked for
	them. So the objects are re-read on a timer, and this is the timer — `_handlePendingUpdate`
	already runs on every core cycle, which is far more often than any of this needs, hence
	the interval. `ObjectMonitor.refresh` writes nothing when nothing has changed, so the
	cost between changes is a read of each pinned object and no display traffic.
	"""
	global _lastMonitorRefresh
	from . import getPlugin

	plugin = getPlugin()
	if plugin is None or not plugin.monitoredKeys:
		return
	now = time.monotonic()
	if now - _lastMonitorRefresh < MONITOR_REFRESH_INTERVAL:
		return
	_lastMonitorRefresh = now
	try:
		plugin.refreshMonitors()
	except Exception:
		log.debugWarning("Could not refresh pinned objects", exc_info=True)


def _handlePendingUpdateWithDocumentLines(self: BrailleHandler) -> None:
	"""Refresh the document line regions after NVDA has handled its own pending updates.

	NVDA only marks the region belonging to the caret as needing an update, so the regions
	showing neighbouring lines have to be refreshed here.

	`_handlePendingUpdate` runs on every core cycle and clears its own pending set before
	returning, so whether there was anything to do has to be noted before delegating to it.
	Refreshing regardless would re-read the caret and retranslate every row many times a
	second while nothing at all was changing.

	Pinned objects are the exception, and are refreshed whether or not anything was pending:
	what they show is not driven by the caret, so a pending update is no signal at all for
	them.
	"""
	hadPendingUpdate = bool(self._regionsPendingUpdate)
	_originals["_handlePendingUpdate"](self)
	_refreshPinnedObjects()
	if not hadPendingUpdate:
		return
	container = self.mainBuffer
	if not isinstance(container, DisplayContainer) or container.numSegments == 1:
		return
	if not bmConfig.shouldShowDocumentLines():
		return
	if bmConfig.isSpeechOutputMode():
		# The display is showing speech, not the document the caret is in.
		return
	try:
		regions = [
			region
			for segment in container.segments
			for region in segment.regions
			if isinstance(region, documentLines.TextInfoPositionRegion) and region.lineOffset != 0
		]
		if not regions:
			return
		for region in regions:
			region.update()
		container.update()
		container.updateDisplay()
	except Exception:
		log.debugWarning("Could not refresh document lines", exc_info=True)


def _nativeScroll(handler: BrailleHandler, forward: bool) -> None:
	"""Pan for NVDA's own panning commands, on the display whose key was pressed.

	Two corrections to what NVDA would do, both from the keys belonging to one piece of
	hardware: the direction is the pressed display's, and so is the segment. Everything else
	is left exactly as it was, and deliberately so — while a message is showing, the handler's
	buffer is the message buffer rather than the container, and panning must go on scrolling
	the message and resetting its timeout.

	:param handler: the braille handler.
	:param forward: which of the two commands was run, before reversal.
	"""
	source = panning.sourceForNativeScroll()
	if panning.shouldReverseForSource(source):
		forward = not forward
	container = handler.buffer if isinstance(handler.buffer, DisplayContainer) else None
	segment = panning.nativeSegmentForSource(source, container)
	if segment is None:
		# No key press behind this scroll, an ordinary display, or a message showing. NVDA's
		# own behaviour, which for the container means the segment following the focus.
		return _originals["scrollForward" if forward else "scrollBack"](handler)
	if forward:
		container.scrollForward(segment)
	else:
		container.scrollBack(segment)


def _scrollForwardMaybeReversed(self: BrailleHandler) -> None:
	return _nativeScroll(self, forward=True)


def _scrollBackMaybeReversed(self: BrailleHandler) -> None:
	return _nativeScroll(self, forward=False)


def _replacements() -> dict[str, object]:
	""":return: the methods this module installs, by the name each one replaces.

	The same function objects every call, since identity is what L{remove} asks about.
	"""
	return {
		"_doNewObject": _doNewObjectMultiSegment,
		"scrollForward": _scrollForwardMaybeReversed,
		"scrollBack": _scrollBackMaybeReversed,
		"_handlePendingUpdate": _handlePendingUpdateWithDocumentLines,
	}


def install() -> None:
	"""Install the patches. Safe to call when they are already installed."""
	for name, replacement in _replacements().items():
		if name in _originals:
			# Already installed, or left in place by a removal that found something else on
			# top. Installing over that would either undo the other add-on or, if it wrapped
			# this module's method rather than replacing it, put this one on top of a wrapper
			# that calls it — a loop with no end.
			log.debug(f"BrlMultiline: {name} is already patched")
			continue
		_originals[name] = getattr(BrailleHandler, name)
		_installedMethods[name] = replacement
		setattr(BrailleHandler, name, replacement)
	log.debug("BrlMultiline patches installed")


def remove() -> None:
	"""Restore NVDA's own methods. Safe to call when nothing is installed.

	Each method goes back only if it is still the one this module installed. Another add-on may
	have replaced or wrapped it since — these are attributes of a shared class — and putting
	NVDA's own back over that would silently undo their work.

	A method left in place keeps its entry in L{_originals}, because this module's replacement
	is still there and still delegating to it. That entry is also what stops a later install
	putting a second copy on top.
	"""
	for name in list(_originals):
		if getattr(BrailleHandler, name, None) is not _installedMethods.get(name):
			log.debugWarning(
				f"BrlMultiline: {name} has been replaced since it was patched; "
				"leaving it as it is rather than undoing whatever replaced it",
			)
			continue
		setattr(BrailleHandler, name, _originals.pop(name))
		_installedMethods.pop(name, None)
	log.debug("BrlMultiline patches removed")
