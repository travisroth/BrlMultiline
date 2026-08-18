# BrlMultiline: semantic navigation for pinned braille segments.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Semantic navigation abstraction for objects pinned to braille segments.

Movement by display-width is efficient for short content but slow for long documents.
This module provides finer-grained navigation—by line, paragraph, or heading—over the
same pinned regions, routing each request to the best mechanism the context supports and
falling back gracefully when a unit is not available.

Usage::

    from .semanticNav import NavUnit, NavResult, SemanticNavigator

    navigator = SemanticNavigator(container, segmentKey)
    result = navigator.move(NavUnit.HEADING, forward=True)
    if not result.moved:
        ui.message(result.reason or _("No further headings"))
    elif result.unit_used != NavUnit.HEADING:
        ui.message(result.reason)  # brief fallback notice

Fallback order, from most to least specific:

    heading → paragraph → line → display

Every level is tried in order until one succeeds or the chain is exhausted.
"""

from typing import TYPE_CHECKING, NamedTuple

from logHandler import log

from .pinnedRegions import PinnedCursorManagerRegion, PinnedRegion

if TYPE_CHECKING:
	from .container import DisplayContainer
	from .segments import BrailleBufferSegment


class NavUnit:
	"""Named unit constants for semantic navigation.

	Defined as a plain class with string attributes rather than an Enum so that
	callers can pass the plain strings (``"line"``, ``"paragraph"``, …) and
	comparisons with ``==`` work without importing the class first.
	"""

	DISPLAY = "display"
	LINE = "line"
	PARAGRAPH = "paragraph"
	HEADING = "heading"


class NavResult(NamedTuple):
	"""The outcome of a semantic navigation move."""

	moved: bool
	"""Whether the reading position changed."""
	unit_used: str
	"""The unit actually applied (may be coarser than requested if fallback occurred)."""
	reason: str
	"""Human-readable explanation when a fallback happened, or empty on direct success."""


_FALLBACK_CHAIN: dict[str, list[str]] = {
	NavUnit.HEADING: [NavUnit.HEADING, NavUnit.PARAGRAPH, NavUnit.LINE, NavUnit.DISPLAY],
	NavUnit.PARAGRAPH: [NavUnit.PARAGRAPH, NavUnit.LINE, NavUnit.DISPLAY],
	NavUnit.LINE: [NavUnit.LINE, NavUnit.DISPLAY],
	NavUnit.DISPLAY: [NavUnit.DISPLAY],
}
"""Units to try in order for each requested unit, from most to least specific."""


class SemanticNavigator:
	"""Routes semantic movement requests for one pinned braille segment.

	Capability is inferred from the type of the last region in the segment:

	- :class:`~brlMultiline.pinnedRegions.PinnedCursorManagerRegion` — browse mode
	  document.  Heading navigation is available when the tree interceptor exposes
	  ``_iterNodesByType``; paragraph and line are always available.
	- :class:`~brlMultiline.pinnedRegions.PinnedTextInfoRegion` — edit field or similar
	  text provider.  Paragraph and line are available; heading is not meaningful.
	- No pinned region — only display-width panning is safe.

	Every successful move updates the display.
	"""

	def __init__(self, container: "DisplayContainer", segmentKey: str) -> None:
		"""
		:param container: the display container.
		:param segmentKey: key of the segment holding the pinned content.
		"""
		self._container = container
		self._segmentKey = segmentKey

	@property
	def _segment(self) -> "BrailleBufferSegment":
		return self._container.segmentForKey(self._segmentKey)

	@property
	def _pinnedRegion(self) -> "PinnedRegion | None":
		"""The last region in the segment, if it is a pinned region."""
		segment = self._segment
		region = segment.regions[-1] if segment.regions else None
		return region if isinstance(region, PinnedRegion) else None

	def supportsHeading(self) -> bool:
		"""True if the pinned content can navigate by heading.

		Requires a browse-mode region whose tree interceptor exposes
		``_iterNodesByType``.
		"""
		region = self._pinnedRegion
		if not isinstance(region, PinnedCursorManagerRegion):
			return False
		return hasattr(region.obj, "_iterNodesByType")

	def supportsParagraph(self) -> bool:
		"""True if the pinned content can attempt paragraph-level movement.

		Any pinned region with a TextInfo position supports this in principle;
		whether the underlying text provider honours ``UNIT_PARAGRAPH`` depends on the
		control.
		"""
		return self._pinnedRegion is not None

	def move(self, unit: str, forward: bool) -> NavResult:
		"""Move the pinned segment by the requested unit, falling back if necessary.

		:param unit: a :class:`NavUnit` value.
		:param forward: ``True`` to move forward, ``False`` to move back.
		:return: the result of the move.
		"""
		chain = _FALLBACK_CHAIN.get(unit, [unit, NavUnit.DISPLAY])
		for candidate in chain:
			outcome = self._tryUnit(candidate, forward)
			if outcome is not None:
				if candidate != unit:
					# Translators: internal reason string for semantic nav fallback; not
					# shown directly—the caller formats the user-facing message.
					reason = f"{unit} not supported; used {candidate}"
					return NavResult(outcome, candidate, reason)
				return NavResult(outcome, unit, "")
		return NavResult(False, unit, "no movement method available")

	def _tryUnit(self, unit: str, forward: bool) -> "bool | None":
		"""Attempt one unit of movement.

		:return: ``True`` if the position moved, ``False`` if it hit a boundary,
		    or ``None`` if this unit is not supported in the current context.
		"""
		if unit == NavUnit.DISPLAY:
			return self._moveByDisplay(forward)
		region = self._pinnedRegion
		if region is None:
			return None
		if unit == NavUnit.LINE:
			return self._moveAndRefresh(region, lambda: region.panLine(forward))
		if unit == NavUnit.PARAGRAPH:
			try:
				import textInfos as _textInfos
				return self._moveAndRefresh(
					region,
					lambda: region.panUnit(_textInfos.UNIT_PARAGRAPH, forward),
				)
			except Exception:
				log.debugWarning("Paragraph movement setup failed", exc_info=True)
				return None
		if unit == NavUnit.HEADING:
			if not isinstance(region, PinnedCursorManagerRegion):
				return None
			# Heading navigation requires _iterNodesByType on the tree interceptor.
			if not hasattr(region.obj, "_iterNodesByType"):
				return None
			return self._moveAndRefresh(region, lambda: region.panHeading(forward))
		return None

	def _moveAndRefresh(self, region: "PinnedRegion", action) -> bool:
		"""Run a movement action and update the display.

		:param region: the pinned region being moved.
		:param action: callable that performs the move and returns ``True`` on success.
		:return: whether the position changed.
		"""
		try:
			segment = self._segment
			moved = action()
		except Exception:
			log.debugWarning("Could not execute semantic movement", exc_info=True)
			return False
		if not moved:
			return False
		segment.update()
		try:
			if region.brailleCells:
				# Show from the start of the newly navigated-to content.
				segment.focus(region)
		except LookupError:
			log.debugWarning("Could not focus region after semantic move", exc_info=True)
		segment.updateDisplay()
		return True

	def _moveByDisplay(self, forward: bool) -> bool:
		"""Pan the segment one display-width within its current content."""
		segment = self._segment
		moved = segment._nextWindow() if forward else segment._previousWindow()
		if moved:
			segment.updateDisplay()
		return moved
