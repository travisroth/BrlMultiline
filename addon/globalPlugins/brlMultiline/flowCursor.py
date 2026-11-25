# BrlMultiline: which of NVDA's cursors the flow answers to.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Which of NVDA's cursors the reader is following: the caret, or the review cursor.

Braille tethered to review means the reader is following the review cursor, and the flow reads
only by the caret, so while braille is tethered to review the band stands down and NVDA presents
review itself. See docs/design/review-flow-plan.md, decision 9 and phase 1.
"""

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
