# A Monarch braille display driver

Plan for `brlMultilineMonarch`, a braille display driver that drives the Humanware Monarch
through its pin report rather than its braille cell reports, so that graphics, braille and a
choice of line pitch all come from one surface.

Read [tactile-graphics-plan.md](tactile-graphics-plan.md) first: it establishes what the
hardware does and why the pin report is the path. This document is about the driver that
uses it. Read [virtual-display-plan.md](virtual-display-plan.md) too, because the
reconnection machinery here is lifted from that driver.

## Verdict

Worth doing, and not a large lift. `hidBrailleStandard.HidBrailleDriver` already finds the
device, opens it over USB or Bluetooth, reads its report descriptor, decodes its keys and
routing, and publishes a gesture map. All of that is inherited unchanged. What the subclass
replaces is one method — `display` — plus additions for graphics, touch and reconnection.

There are four reasons to own the driver rather than patch the standard one, and only the
first is about graphics:

1. **Report 0x21 is unreachable otherwise.** Report selection is not in the payload, so no
   cell values fed to `hidBrailleStandard` can reach the pin array. Some code has to choose
   the report, and that code is a driver.
2. **How many lines becomes a choice.** A cell is four dot rows whatever else changes; what
   varies is the blank row after it. One blank row gives **8 rows** on a 40 row grid, none
   gives **10**, which is what the Monarch's own software offers. Terminal mode gives 8 and
   cannot give 10. All eight dots are drawn at either setting.
3. **Bluetooth recovery.** `hidBrailleStandard` gives up at the first dropout and needs NVDA
   restarted. `brlMultilineVirtual` already solves this for its members and the same
   machinery works here.
4. **Indicators stop being cursors.** Native Monarch marks a focused list line with a block
   in the margin rather than a caret. Once we own the pins that is expressible; through cell
   values at a 3 pin pitch it is not.

## Staying honest about DotPad X

The end goal is that BrlMultiline's graphics support is not Monarch specific. Only one
thing in this design is genuinely device bound, and it is isolated:

1. `pinBuffer.py` — a plain dot canvas. Width, height, `setDot`, and drawing primitives.
   Knows nothing about any device, imports nothing from NVDA, and is unit tested on its own.
2. `monarch.py` — the Monarch's geometry and **pin order**: 96 by 40, 2 by 4 blocks in
   reading order packed as braille dots, output report 0x21, and the touch decode. Also pure
   Python and unit tested.
3. `__init__.py` — the driver, which is the only file that talks to NVDA or to hardware.

A DotPad X driver would supply its own equivalent of `monarch.py` and reuse the rest. The
seam is deliberately "everything except how the display writes its final pin order", which
is what we can see from one device without guessing at the other.

The add-on above talks to whichever driver is loaded by duck typing, the way `devices.py`
already reads the live driver object rather than importing the driver. A display that can do
graphics publishes `graphicsSize`, `setGraphicsOverlay`, `clearGraphicsOverlay` and
`lastTouch`; one that cannot, does not, and the add-on falls back to text.

## What the subclass changes

### Detection, which is the one real gotcha

`BrailleDisplayDriver._getAutoPorts` asks `bdDetect` for devices registered to `cls.name`.
HID braille has no such registration — `HidBrailleDriver.registerAutomaticDetection` is
explicitly a no-op, because matching is special cased inside `bdDetect` on usage page 0x41.
So a subclass with a new name finds **nothing**, over either transport, and fails to open.

The fix is to override `_getAutoPorts` and reuse NVDA's own special case:
`getDriversForConnectedUsbDevices` and `getDriversForPossibleBluetoothDevices` both yield
`(driverName, match)` pairs and already apply the HID braille rule, so keeping the pairs
whose driver is `_getStandardHidDriverName()` gives exactly the devices `hidBrailleStandard`
would have found. **Both transports, deliberately** — the Monarch is used over USB as well as
Bluetooth and the standard driver carries both, so dropping either would be a regression.

`supportsAutomaticDetection` stays `False`. Automatic detection would put this driver in
competition with `hidBrailleStandard` for the same device, and the user selects a driver
here anyway.

### Being listed at all, which is the second gotcha and follows from the first

`getDisplayList` drops any driver whose `check` returns False, and the base `check` has two
ways to say yes. The first needs `supportsAutomaticDetection`, which we have just turned off.
The second needs `getManualPorts`, which is for serial ports and raises `NotImplementedError`
on a HID driver. So the base implementation answers False and the driver is simply absent
from NVDA's braille settings, with nothing in the log to say why.

It still appeared in `brlMultilineVirtual`'s member list, because that enumerates the drivers
themselves rather than going through `getDisplayList` — which made the absence look like a
settings bug rather than a driver one.

So `check` is overridden to report whether `_getAutoPorts` finds anything. Two constraints
on it:

1. **It must not open the device.** `check` runs for every driver when the settings dialog is
   built, and `hwIo.hid.Hid` opens exclusively, so opening here would take the display away
   from whatever is currently driving it.
2. Which means it cannot confirm a pin report, so it answers True for any HID braille
   display. A non Monarch is then listed and refused at construction with a message saying
   so. That is the better failure: being wrongly offered is recoverable, being invisible on
   the device we do support is not.

### Rendering, which is always through pins

`display(cells)` never writes reports 0x31 to 0x38. It draws the cells into the pin buffer
with `drawBrailleCells` at the configured pitch, composites any registered graphics overlay,
and writes one 480 byte report 0x21.

That removes the need for anything like the spike's `hold()`. The handler goes on running
normally; the driver simply decides what reaches the panel, so an ordinary braille update
cannot wipe a drawing and nothing has to be suspended or monkey patched.

Two pitches, as a driver setting, named for what they give: **8 rows** and **10 rows**.

1. **8 rows of 32.** Four dot rows and a blank one. Identical to terminal mode, and the right
   default because it matches what every existing BrlMultiline layout assumes.
2. **10 rows of 32.** Four dot rows and no blank one. Denser, and the user's judgement is that
   it reads well for reading and is busy for editing.

**Every pitch draws all eight dots, and the driver never masks one.** Dots 7 and 8 live on
the fourth row of a cell, which at 10 rows is the row that would otherwise separate the
lines. So what 10 rows spends is the separation, not the dots: six dot content leaves that
row empty and the lines look separated anyway, while a caret or an eight dot table fills it
and the lines meet where it does.

That is the reader's trade to make by choosing the pitch. An earlier version of this driver
masked dots 7 and 8 at the denser pitch, on the reasoning that they had "nowhere to go". They
have somewhere to go, and silently discarding them would have cost the caret — which is
exactly the thing a reader most needs to see.

`numRows` and `numCols` are reported to NVDA accordingly, so the handler, the add-on's views
and the flow all size themselves correctly with no special cases above the driver.

### Cell glyphs, which are the gap column spent differently

A braille cell is two dot columns and a blank one. The blank exists so a reader can tell one
cell from the next, not because the hardware needs it — and in graphics nothing enforces it.
So a cell slot can carry a **3 by 4 glyph** instead of a 2 by 4 braille cell, and because the
slot is unchanged the next cell still starts exactly where it did. Layout, routing, wrapping
and scrolling are all untouched. 32 slots of 3 is 96, so even a glyph in the last cell fits.

A cell is one byte and twelve pins need twelve bits, so **the pattern cannot travel in the
cell array**. There is no spare value to use as an escape either: all 256 are legitimate
braille. The position comes from the cell grid and the pattern arrives alongside, through
`setCellGlyphs`, keyed by index into the flat cell array.

Each glyph carries the braille cell it stands in for, and is drawn only while the cell at
that index still reads it. One check, three properties:

1. A frame the add-on did not compose — a braille message — cannot get a glyph painted over
   unrelated content. It is skipped and the ordinary cell shows.
2. The fallback is what a display without glyphs renders anyway, so the add-on writes one
   buffer and every display does the best it can with it.
3. A caret wins. NVDA ors the cursor into the cell before the driver sees it, the cell stops
   matching, and the braille cell with its cursor is drawn instead.

The worked example is the list focus indicator. Monarch's own is a solid 3 by 3 with the
fourth row blank, followed by a space cell, and it is easy to find precisely because it is
square — which needs three columns. BrlMultiline currently approximates it with dots 3678
twice. As a glyph it is the real thing, with a fallback of dots 1 to 6 (0x3F, a solid 2 by 3)
that stays recognisable on a Focus.

**The vocabulary lives in the add-on, not here.** The driver is handed patterns and never
learns a symbol's name, so an Excel module wanting a formula marker and a Word module wanting
a checkmark do not touch this file. `newGlyph` is a factory on the driver so the add-on can
build one without importing the package, the way `devices.py` reads the live driver object
rather than importing it.

Single cell only. A glyph spanning several cells would need a region the flow must not break,
and the add-on has only wrap and no-wrap today; that is a larger change than the idea is
worth. An app module wanting a two cell symbol accepts that it can split, which readers of
refreshable braille are used to.

`glyphSize` and `cellSize` are both published so the add-on can tell whether a display has a
gap to reclaim. A display whose cells are already gapless reports the same for both and
glyphs gain it nothing, which is the honest answer for that hardware rather than a silent
degradation.

### Touch, where both reports are kept

The panel answers a finger with two reports and the driver reads both:

1. `0x40`, usage `0x401` — the touched **pin**, row major over 96 by 40, one based, 0 on
   release. This is what makes routing work at any pitch, because a cell index from the
   device assumes the native 8 by 32 layout and stops meaning anything at a 4 row pitch.
2. `0x41`, usages `0x402` to `0x501` — the routing **cell**, the device's own answer on its
   native grid.

Keeping the cell report as well as the pin is deliberate. In 8 row mode it is the Monarch's
own calibrated answer and should be preferred over anything we derive. In 10 row mode it is
still useful evidence: a fingertip covers several pins, so the device's idea of which cell
was meant is a second opinion worth having when turning a pin into a line and column.

Both are published on the driver; NVDA's own routing gestures continue to work unchanged
because the inherited `_hidOnReceive` still runs.

### Reconnection, lifted from the virtual driver

`brlMultilineVirtual` already solved this and the pieces transfer directly:

1. **Retry on open.** A device present in the enumeration is not always openable, especially
   just after something else has released it. Three attempts, short delay.
2. **Watch the read side.** A dropped Bluetooth link fails the overlapped read immediately
   with `WinError 1167`, and `hwIo.IoBase._ioDone` offers that to `_onReadError` before
   raising. Chaining onto it turns a silent death into a notification, long before a write
   would have discovered it. Chain, never replace, and restore only if ours is still there.
3. **Poll with backoff.** Five seconds, doubling to a minute, with a `bdDetect` presence
   check first so a switched off display costs nothing.

The difference from the virtual driver is what gets reopened. There, a failed *member driver*
is replaced. Here the driver stays and reopens **its own device** in place: close the handle,
run detection again, open a new `hwIo.hid.Hid`, re-read the caps, re-hook the read error, and
repaint the last frame. NVDA is never told the display went away, so no display switch,
no fallback to no braille, and no restart.

### Indicators

The driver cannot know what is focused. `_displayWithCursor` ORs the cursor shape into the
cell before the driver sees it, so by the time cells arrive a cursor dot is indistinguishable
from content.

So the split is: the add-on decides what to indicate and where, and passes it down the same
out-of-band channel as graphics overlays; the driver renders it into pins. A margin block,
a full height bar, an underline in the gap row are all then possible. NVDA's own cursor
should be off when this is used, or it will be baked into the cells underneath.

Not in v1. The API for it is the overlay API, which is.

## Coexistence

Two drivers must never hold the Monarch at once. `hwIo.hid.Hid` opens exclusively, so the
second attempt fails rather than corrupting anything, but the failure is confusing.

The case that matters is `brlMultilineVirtual` configured with **both** `hidBrailleStandard`
and `brlMultilineMonarch` as members. It refuses two members with the same driver name, and
these are different names, so it would try both and one would fail and be retried forever by
the reconnect poll. Worth a check in the virtual driver's configuration, and worth saying
plainly in its documentation. Not v1.

## v1 scope

In:

1. Subclass, detection over USB and Bluetooth, refusal to load on a device with no pin report.
2. Always render through report 0x21.
3. Pitch setting: 8 rows or 10 rows, all eight dots drawn at both.
4. Graphics overlay API, composited with text.
5. Touch capture, both reports, published for the add-on.
6. Single cell glyphs: 3 by 4 shapes filling a cell slot, each with the braille fallback the
   add-on also writes into the buffer. The vocabulary stays in the add-on.
7. Reconnection: retry, read error watch, backoff poll, in place device reopen.

Out:

1. The glyph vocabulary itself, and deciding which app modules want which symbols. The driver
   supplies the mechanism; naming the shapes is the add-on's.
2. Multi cell glyphs, which would need a region the flow must not break.
3. A DotPad X driver. The seam is prepared, the device is not here.
4. Any change to the virtual driver, including the coexistence check.
5. Anything above the driver: panels, views and a graphics segment are the tactile graphics
   plan's phase 2 and follow separately.

## Open questions

1. Does a 480 byte pin write cost more or less, mechanically, than the eight cell reports it
   replaces? Every write is a full refresh either way, so it should be no worse, but it has
   not been measured.
2. At 10 rows, how legible is an eight dot table in practice, given that dots 7 and 8 land
   where the line separation would otherwise be? A question for fingers, not for the driver.
3. Is the device's routing cell better than a pin derived one at native pitch? The plan
   assumes yes and prefers it; a session comparing the two would settle it.
4. Does reopening the device in place recover a Bluetooth drop as reliably as the virtual
   driver's member replacement does? The failure modes may not be identical.
