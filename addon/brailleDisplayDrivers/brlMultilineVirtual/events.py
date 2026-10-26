# BrlMultiline: what the virtual display announces about itself.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Notifications the composite display raises, for the add-on's own plugin to hear.

When a member is lost or returns, the segments, named after the displays, must be rebuilt.
`displaySizeChanged` misses it when the size is the same, and `displayChanged` means NVDA
swapped the display, which other add-ons act on. So the driver raises its own, for the plugin.
"""

import extensionPoints

membersChanged = extensionPoints.Action()
"""Raised when the set of physical displays the composite is driving has changed.

Handlers are called with `display`, the composite driver. Raised on the main thread, after the
geometry has been settled, so a handler may read `display.slots` and NVDA's display dimensions
and get answers that agree with each other.
"""
