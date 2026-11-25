# BrlMultiline: which of NVDA's cursors the flow answers to.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which of NVDA's cursors the reader is following, and where it is: the caret, or the review cursor.

Braille tethered to review means the reader is following the review cursor. The flow band then
reads at the review position instead of the caret, and every part of the flow that asks where the
reader is asks here, so none follows the caret while another follows review. Where the review
cursor is somewhere a flow cannot read, such as screen review, the band stands down and NVDA
presents review itself. Only the band follows review: a pin reads from its own position. See
docs/design/review-flow-plan.md, decisions 1 to 4 and 9.
"""

import textInfos
from config.configFlags import TetherTo
from logHandler import log


def isReviewing(handler) -> bool:
	"""Whether braille is tethered to review, so the reader is following the review cursor.

	:param handler: NVDA's braille handler, or None when there is none.
	:return: whether it is tethered to review. False when it cannot be asked.
	"""
	if handler is None:
		return False
	try:
		return handler.getTether() == TetherTo.REVIEW.value
	except Exception:
		log.debugWarning("BrlMultiline: could not ask what braille is tethered to", exc_info=True)
		return False


def isReviewingNow() -> bool:
	""":return: `isReviewing` for NVDA's own braille handler."""
	import braille

	return isReviewing(getattr(braille, "handler", None))


def reviewPosition():
	""":return: NVDA's review position, or None when there is none or it cannot be read."""
	import api

	try:
		return api.getReviewPosition()
	except Exception:
		log.debugWarning("BrlMultiline: could not read the review position", exc_info=True)
		return None


def reviewObject():
	""":return: the object the review cursor is in, or None."""
	position = reviewPosition()
	return getattr(position, "obj", None) if position is not None else None


def _isSame(first, second) -> bool:
	if first is second:
		return True
	try:
		return bool(first == second)
	except Exception:
		return False


def reviewPositionIn(obj):
	"""Where the review cursor is, when it is in this object.

	:param obj: the object or tree interceptor being read.
	:return: a copy of the review position, or None when the review cursor is elsewhere.
	"""
	position = reviewPosition()
	if position is None or not _isSame(getattr(position, "obj", None), obj):
		return None
	try:
		return position.copy()
	except Exception:
		log.debugWarning("BrlMultiline: could not copy the review position", exc_info=True)
		return None


def readerPositionIn(obj, followsReview: bool):
	"""Where the reader's cursor is in an object.

	The review position under a review tether, for a flow that follows the reader, while the
	review cursor is in this object; otherwise the caret.

	:param obj: the object or tree interceptor being read.
	:param followsReview: whether the flow asking follows the reader, as the band does.
	:return: the position.
	:raises Exception: whatever reading the caret raises, as `makeTextInfo` does.
	"""
	if followsReview and isReviewingNow():
		review = reviewPositionIn(obj)
		if review is not None:
			return review
	return obj.makeTextInfo(textInfos.POSITION_SELECTION)


def moveReviewTo(info) -> bool:
	"""Put NVDA's review cursor at a position, as NVDA's own review region does.

	NVDA then calls `handleReviewMove`, which brings the band round to follow; the band finds the
	review cursor where it put it and moves nothing.

	:param info: the position to move to.
	:return: whether it moved.
	"""
	import api

	try:
		return bool(api.setReviewPosition(info.copy()))
	except Exception:
		log.debugWarning("BrlMultiline: could not move the review cursor", exc_info=True)
		return False


def shouldRoutingMoveCaret() -> bool:
	"""Whether a routing key under a review tether moves the caret too, by NVDA's own setting.

	:return: NVDA's answer, or False where it cannot be asked.
	"""
	try:
		from braille.regions._routing import _routingShouldMoveSystemCaret
	except ImportError:
		return False
	try:
		return bool(_routingShouldMoveSystemCaret())
	except Exception:
		log.debugWarning("BrlMultiline: could not ask whether routing moves the caret", exc_info=True)
		return False


def canFlowReview() -> bool:
	"""Whether the review cursor is somewhere a flow can read.

	Not in screen review, and not in a display model without a caret, which is the screen's text
	rather than an object's own. NVDA presents those itself.

	:return: whether the band can read at the review position.
	"""
	try:
		import review

		if review.getCurrentMode() == "screen":
			return False
	except Exception:
		log.debugWarning("BrlMultiline: could not ask the review mode", exc_info=True)
	position = reviewPosition()
	if position is None:
		return False
	try:
		from displayModel import DisplayModelTextInfo, EditableTextDisplayModelTextInfo
	except ImportError:
		return True
	return not (
		isinstance(position, DisplayModelTextInfo)
		and not isinstance(position, EditableTextDisplayModelTextInfo)
	)
