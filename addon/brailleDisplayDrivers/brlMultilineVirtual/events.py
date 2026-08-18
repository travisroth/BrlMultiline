# BrlMultiline: what the virtual display announces about itself.
# Part of the BrlMultiline add-on for NVDA.
# Copyright (C) 2026 Travis Roth <travis@travisroth.com>
# This file is covered by the GNU General Public License version 2.

"""Notifications the composite display raises, for the add-on's own plugin to hear.

One notification, and it exists because NVDA has nothing that means what it means.

When a member is lost or comes back, the arrangement above the driver has to be rebuilt: its
segments are named after the physical displays, so which displays there are decides what
segments exist, whatever size the result happens to be. `displaySizeChanged` covers only the
case where the size differs, and it often does not — a single display lost and reconnected
leaves the composite exactly as big as it was, and the composite deliberately keeps the last
geometry while no member at all is there. `displayChanged` means NVDA has swapped the display
for another one, which is a thing other add-ons act on and is not what has happened here.

So the driver says the true thing, and the plugin listens for it. Nothing outside this add-on
is expected to.
"""

import extensionPoints

membersChanged = extensionPoints.Action()
"""Raised when the set of physical displays the composite is driving has changed.

Handlers are called with `display`, the composite driver. Raised on the main thread, after the
geometry has been settled, so a handler may read `display.slots` and NVDA's display dimensions
and get answers that agree with each other.
"""
