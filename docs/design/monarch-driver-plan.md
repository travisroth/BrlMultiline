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

## Status, as confirmed on hardware

Built, and run on a Monarch on 8 September 2026. The driver opens the device, reports
`Monarch on 96 by 40 pins, 8 rows of 32 at pitch 8row, writing output report 0x21`, and
drives the panel through the pin report for ordinary braille. Everything in the v1 scope
below is implemented, with 126 driver and pin unit tests inside a suite of 3,060.

What the hardware settled, so it need not be argued again:

1. **Routing works at both pitches.** At the native 8 rows the device's own routing cell is
   used unchanged. At the custom 10 rows the pin derived correction is good: routing into
   list items and edit fields lands where the finger is. Whether a pin derived index would
   beat the device's own at the native pitch is not an interesting question — at 8 rows we
   defer to the Monarch's calibration deliberately, and there is no reason to second guess it.
2. **A 480 byte pin write costs no more than the eight cell reports it replaces.** Write
   speed is indistinguishable in use, and at 10 rows it buys two extra lines of content for
   that same cost. No ill effect has been seen driving either 8 or 10 rows through the
   96 by 40 pin record.
3. **The 10 row pitch stays a feature.** An eight dot table at 10 rows is crowded, because
   dots 7 and 8 land where the line separation would otherwise be. That is the expected
   trade and it is the user's to make, which is why it is a setting rather than a default.

Not yet exercised: the driver's **own in place reopen**. The reconnection seen on hardware
was `brlMultilineVirtual` replacing a failed member, which constructs a fresh driver. The
path where this driver keeps its instance and reopens its own handle has run only in tests.

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

### The port list, which is the third of the same gotcha

`getPossiblePorts` asks `bdDetect.getConnectedUsbDevicesForDriver(cls.name)` and its Bluetooth
twin. Both raise `LookupError` for a driver with no detection data, so the base concludes
neither transport exists, returns an empty mapping, and the settings dialog shows no port
control at all — while `hidBrailleStandard` beside it offers Automatic, USB and Bluetooth.

The plumbing behind the choice already worked: `_getTryPorts` passes the usb and bluetooth
flags through to `_getAutoPorts`, so picking USB really did restrict to USB. Only the
advertisement was missing. Overridden to answer from `_getAutoPorts` per transport.

That is three things a HID braille subclass silently loses because they are keyed on
`cls.name`: `_getAutoPorts`, `check`, and `getPossiblePorts`. Anything else consulting
`bdDetect` by driver name will need the same treatment, and none of them fail loudly.

### Letting go of the device when the constructor fails

`__init__` can raise after the device is open — a firmware without the pin report, or a
plain bug. Nothing else can reach the instance once it throws, so it has to close the handle
itself. `hwIo.hid.Hid` opens exclusively, so a leaked handle takes the display away from
every other driver until NVDA restarts.

This is not hypothetical: the first hardware run set `numCells`, whose setter *raises* on a
multi line display rather than being merely redundant, and the resulting failure left the
USB interface held. Set `_suppressDisplayClear` before terminating, because a half built
driver may not be able to blank a display.

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

Each glyph carries the braille cells it stands in for, and is drawn only while the cells at
that index still read them. One check, three properties:

1. A frame the add-on did not compose — a braille message — cannot get a glyph painted over
   unrelated content. It is skipped and the ordinary cell shows.
2. The fallback is what a display without glyphs renders anyway, so the add-on writes one
   buffer and every display does the best it can with it.
3. A caret wins. NVDA ors the cursor into the cell before the driver sees it, the cell stops
   matching, and the braille cell with its cursor is drawn instead.

The match alone is not enough, and the difference is worth spelling out. Skipping a glyph
leaves it registered, so a later unrelated frame carrying the same byte at the same index gets
painted with a symbol belonging to content that scrolled away minutes ago: the check answers
"this byte looks familiar" when the question is "does this glyph belong to this content". So a
frame that does not match **retires** the glyph rather than skipping it. A glyph lives exactly
as long as the run of frames that carry its cell.

The contract that follows: **the add-on re-registers on every recompose.** A braille message
flashing over the content ends the registration, and the redraw afterwards is where the glyph
comes back. `setCellGlyphs` replaces the whole set, so re-registering is the ordinary call and
nothing has to be cleared individually.

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

It now exists, in `globalPlugins/brlMultiline/glyphs.py`, and it is written in dot numbers
rather than in ASCII art: dots 1 to 8 as braille has always numbered them, then 9 to 12
continuing down the column braille leaves blank. Twelve dots is exactly one glyph slot at
either pitch. Cells of a wider shape are separated by a bar, so a three cell symbol is written
as three groups of the numbers a braille reader already thinks in, rather than as a nine by
four picture that has to be read four strings at a time and whose columns can be misaligned
invisibly. That is a notation chosen for the person maintaining it.

**A glyph spans a run of cells, and that turned out to be the whole point.** This said single
cell only, on the grounds that a wider one would need a region the flow must not break. It was
answered by the thing next to it: NVDA already writes short strings for roles and states —
"btn", "cbo", three cells for a checkbox — and drawing those as symbols is the most useful
thing a pin display can do with a braille line. Replacing three cells with one would shift
everything after it and break routing; replacing three cells with a nine by four drawing
changes nothing but what those pins say.

The region never had to be invented, because the fallback match already answers it. A run split
across the end of a line is a run whose cells no longer sit together, and the driver skips it —
so the reader gets the text wrapped, which is exactly what they would have got without glyphs
at all. Graceful, and decided in the driver rather than imposed on the flow.

Two smaller rules come with it: overlapping runs are a caller's mistake with no sensible
rendering, so the later one is dropped at registration; and every cell of the run must match,
so a caret or-ed into the last cell of "btn" retires the symbol and shows the letters, which is
what a reader sitting on it needs.

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

**A third thing is published, and it is the only one anything above the driver can use.**
`lastTouch` is the live touch, and the live touch is zero the moment the finger lifts. NVDA
compounds that by running a gesture's script from a queue rather than during dispatch, so by
the time a script asks what a press meant, even that press's own touch has gone. Anything
wanting to know *where* a press landed, at pin resolution rather than cell resolution, would
have got nothing every time.

So `lastRoutingPin` holds the pin that was under the finger when the routing key went down.
Set at gesture construction, for every dispatched press at both pitches, and left standing
until the next press replaces it — unlike `_pinAtRouting`, which guards the correction and
must not outlive its own press. This is what makes a drawing something to point at: the
add-on's graphics mode reads it when a press lands inside a figure.

The pin has to be **snapshotted when the routing key goes down**, because of the order the
panel speaks in: the pin arrives, then the routing cell, then the pin again as zero on
release, and only then does NVDA raise the gesture. By gesture time the live pin is gone.

Hardware settled this. At the 10 row pitch the pin derived index is accurate enough for
real work: routing into list items and edit fields lands on the cell under the finger. The
worry that an infrared camera might quantise below the cell, or that a fingertip covering
several pins would blur the answer, did not show up in use. At 8 rows the device's own cell
is used and the comparison does not arise.

Where the correction cannot be made, the press is **cancelled** rather than passed through.
There are two such cases at a non-native pitch: no pin was captured, and more than one routing
cell — a range selection, which one touched pin cannot re-base. Passing the device's own index
through in either case activates a cell chosen on a layout we are not drawing, which reads to
a user as the display acting at random. A press that does nothing is one they will simply make
again. At the native pitch nothing is cancelled, because nothing needs correcting.

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

Three things about that are not obvious, and each was a bug first.

**A reconnect is a new input session.** Everything the input path remembers describes the
handle that went away: which keys were down, whether releases were being ignored, where the
last touch was, and the capability structures every report is decoded against. Data indexes
belong to a device's report descriptor, so keeping the old map decodes the new device's
reports with the old device's key numbering. Carrying key state across is worse — a release
arriving after the reconnect completes a combination begun on a device that is gone, and fires
whatever it is bound to. All of it is reset once the new handle is installed.

**Scheduling goes through `core.callLater`, not `wx.CallLater`.** The driver declares
`isThreadSafe`, so NVDA may call into it from the I/O thread, and a read error — which is what
starts a reconnection — arrives there. `core.callLater` marshals timer creation to the GUI
thread, which is the reason NVDA offers it. The cost is that off the main thread it returns
nothing to cancel, so each attempt carries a generation number and a stale tick compares and
returns. If scheduling fails outright the recovering flag is cleared again: leaving it set
means every later loss answers "handled" with nothing behind it, and a virtual display holding
this member keeps a display that is never coming back.

**Termination races the reopen, and the loser leaks the hardware.** `_poll` can check the
termination flag, `terminate` can then run and find no device to close, and the reopen can
install a handle afterwards that nothing will ever reach. `hwIo.hid.Hid` opens exclusively, so
that handle holds the Monarch away from *every* driver until NVDA restarts. The candidate is
therefore opened into a local and installed only after the flag is re-checked under a lock,
and `terminate` closes anything that beat it to the install.

### Three locks, in one order

`_stateLock` for what the panel is made of, `_writeLock` for the wire, `_lifecycleLock` for
the device handle and the reconnect timer — and always in that order, never the reverse. Each
is held over a short section that calls nothing which could take an earlier one: a failed
write reports the device lost *after* releasing `_writeLock`, and `terminate` sets its flag
and lets `_lifecycleLock` go before the base class blanks the display through `display`.

`_stateLock` earns its place twice over. Composing reads cells, glyphs, overlays and pitch,
which four different threads write — the handler writes cells from its I/O thread while the
add-on sets overlays from the main one — and a frame built from a mixture of before and after
was never asked for. Worse, iterating the overlays while another thread inserts one raises
`dictionary changed size during iteration` and the panel simply does not update. So the state
is copied under the lock and the drawing, which is the slow part, happens outside it against a
snapshot nothing can change underneath.

Overlay and glyph changes are also **coalesced**. Setting three of them used to be three
complete mechanical refreshes; now the request is marked and handed to the main thread, and
whichever comes first — that flush or an ordinary `display` — publishes everything pending.
On a page turn device where each write clatters and the reader has to lift their hand, one
write per batch is not an optimisation.

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

Status: **delivered**, and confirmed on hardware. See "Status, as confirmed on hardware"
above for what the device settled.

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

1. Deciding which objects get which symbol — where the flow introduces a glyph. The driver
   supplies the mechanism and the add-on now has the vocabulary (`glyphs.py`); what remains is
   the question of who asks for one and when.
2. Nothing else. Multi cell glyphs were out and are now in; see above.
3. A DotPad X driver. The seam is prepared, the device is not here.
4. Any change to the virtual driver, including the coexistence check.
5. Anything above the driver: panels, views and a graphics segment are the tactile graphics
   plan's phase 2 and follow separately.

## Open questions

1. **Answered.** A 480 byte pin write costs no more, mechanically, than the eight cell
   reports it replaces. Write speed is indistinguishable in use, and at 10 rows the same
   write carries two more lines of content. Driving either pitch through the 96 by 40 pin
   record has shown no ill effect.
2. **Answered.** An eight dot table at 10 rows is crowded, and legible. Dots 7 and 8 land
   where the line separation would otherwise be, which is exactly the trade the pitch makes.
   It works as expected, it is the user's choice to make, and the 10 row pitch stays.
3. **Answered, and it was the wrong question.** At the native pitch we defer to the Monarch's
   own routing cell by design, because it is the device's calibrated answer on the layout it
   was built around. There is nothing to compare. The question that mattered was whether the
   pin derived index is good enough at 10 rows, and it is — see "Touch" above.
4. Does reopening the device in place recover a Bluetooth drop as reliably as the virtual
   driver's member replacement does? The failure modes may not be identical, and the hardware
   run so far exercised only the member replacement path — the driver's own in place reopen
   has run in tests and not yet on a real dropout.
5. What are the Monarch's USB vendor and product IDs, and are they stable across firmware
   revisions? A match on the pin capability is what authorises writing a raw 480 byte report,
   so a device identity check would be worth having on top of it. None is made today, and
   deliberately: no identifier has been confirmed, and hard coding a guessed one would refuse
   real hardware. A rejected near miss capability is logged with its descriptor shape, so the
   answer can come out of a log rather than out of a second hardware session.
6. What is the pin array's bit offset within report 0x21? Windows does not expose it through
   the parsed capabilities, so the payload's position rests on the pin array being the
   report's only content — which 3,840 bits in a 481 byte report leaves room for and nothing
   else. True on this firmware; not proven for the next one.
