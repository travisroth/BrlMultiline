# Tactile graphics over HID braille

Feasibility findings and a plan for drawing on the Humanware Monarch through the connection
the add-on already has: NVDA's `hidBrailleStandard` driver, with the Monarch in terminal
mode.

Read [nvda-api-notes.md](nvda-api-notes.md) and [architecture.md](architecture.md) first.
This document adds findings from NVDA master (`C:\code\nvda`, reporting 2026.3.0dev), from
the published HID Braille Display usage page, and from a first hardware run of
`spikes/tactileGraphicsSpike.py` against a Monarch over Bluetooth.

## Verdict

Drawing is possible at the Monarch's full pin resolution, braille and drawing can share the
panel, and hardware runs found a great deal more than the standard promises — along with one
constraint that shapes everything: the panel refreshes as a whole, and slowly.

**The standard offers no graphics mode.** Usage page 0x41, created by HUTRR78 and unchanged
through HID Usage Tables 1.6, has seven output usages: the display collection, braille row,
six and eight dot cells, number of cells, and the two screen reader control usages. No
bitmap, no pixel, no raw pin, no mode switch. Anyone who says HID braille has a graphics
mode is, on the published specification, mistaken.

**The cell array is a canvas anyway.** A cell value is a raw eight dot bitmap, not a
character code, and NVDA's HID driver does bytes in, bytes out with no table lookup and no
validation. Writing arbitrary cell bytes to the 8 by 32 array is drawing on a 64 by 32 dot
canvas. That needs no new driver and no cooperation from anyone. Its limit is the lattice:
the Monarch spends three pin columns per cell, two dots and a spacer, so a third of its 96
columns are unreachable this way and horizontal runs come out dashed.

**But the Monarch exposes its whole pin grid on the same interface, and it works.** Hardware
runs of `hunt` found an output report byte length of **481**, where eight rows of thirty-two
cells need 33, and the extra is a single output *button* cap:

	report 0x21, usage 0x301, collection 4 (link usage 0x300), ReportCount 3840

Three thousand eight hundred and forty one bit fields is 480 bytes, which is the whole
report, and 3,840 is exactly the Monarch's pin count. It sits in the same vendor collection,
usage 0x300, that owns the eight braille rows. The input side corroborates it: value cap
usage 0x401 has a logical range of 0 to 3840.

**Confirmed by writing to it.** With the handler held, an all zero payload to report 0x21
lowered the whole panel and an all ones payload raised it fully solid. The report is live in
terminal mode with no mode switch, no feature write, and no vendor cooperation. Nothing
gates it.

This is the finding the rest of the document is organised around, and it changes the shape of
the work: the graphics path is not the cell array with its lattice compromise, it is a
96 by 40 bitmap with no unreachable columns anywhere.

Note the two ways a descriptor can declare a block of bits, because reading only one of them
gets the wrong answer. A *usage range* gives one usage per bit and leaves `ReportCount` at
1 — the Monarch's routing keys are usages 0x402 to 0x501, 256 bits, 32 bytes, exactly its
33 byte input report. A *repeated single usage* gives one usage and puts the count in
`ReportCount` — which is how the pin array is declared. Note also that output report 0x21
and feature report 0x21 are different reports; HID report IDs are namespaced per report
type.

NVDA cannot see any of this. `hidBrailleStandard._findCellValueCaps` keeps only value caps
whose link usage is `BRAILLE_ROW`, and NVDA reads button caps for input only. An output
button array is invisible to it and would never appear in an NVDA log.

So the 47 percent this plan originally wrote off is reachable, on the interface NVDA has
open, today. Phase 0 confirmed it end to end: a full width horizontal line across all 96
pins comes out solid and unbroken, which is precisely the figure the cell path cannot draw.

**Mixing is free within one write, and impossible across two.** This was written down as
simply free, and that was wrong. The Monarch treats **every write as a full display
refresh**: with a drawing up, NVDA's next ordinary braille write wiped the whole panel, not
just the rows it addressed. Pin writes and cell writes replace one another rather than
composing.

So a graphics area cannot sit beside braille that NVDA is still driving. Whatever is on the
panel must be composed by us and sent as one pin report: text drawn in as pins, graphics
drawn in beside it. Mixing is then genuinely free, because it is one buffer — but the driver
has to own the whole panel to get there.

**The DotPad turns out to be the right model, by a route that took two wrong turns.** The
first reading was that the relationship is inverted: the DotPad has a separate graphics area
with no text mode, so NVDA renders characters *down* into a pixel buffer with
`tactile.braille.drawBrailleCells`, whereas the cell path would render pixels *up* into
cells. True of the cell path, and irrelevant, because the cell path is not what we are going
to use.

On the pin path we are in the DotPad's position exactly: one surface, full panel writes,
text drawn in as dots. `drawBrailleCells` is the function for it, and it exists in core
already.

What does **not** transfer is the DotPad's bit packing, on either path.
`DpTactileGraphicsBuffer` uses a column major nibble layout, and the Monarch wants braille
dot numbering whichever report a byte arrives in. This was got wrong here first and the
hardware refused it; see "The pin report's layout" below for what that looked like.

## What the hardware actually reported

From the first run, Monarch over Bluetooth, terminal mode:

1. Top level usage page 0x0041, usage 0x1. Geometry 8 rows of 32, 256 cells.
2. Report byte lengths: input 33, output **481**, feature 17.
3. Eight output value caps, report IDs 0x31 to 0x38, each 32 eight dot cells, each in its
   own `BRAILLE_ROW` collection. NVDA writes eight separate reports per refresh.
4. One output button cap: report 0x21, usage 0x301, in collection [4], `ReportCount` 3840.
   That is the pin array, 480 bytes, accounting for the whole 481 byte output report.
5. One input value cap, report 0x40, usage 0x401, 16 bits, **logical 0 to 3840**.
6. Fourteen link collections. Collection [4] is usage **0x300**, a vendor defined logical
   collection that parents all eight braille rows — the rows are not children of the
   application collection directly.
7. Feature report 0x21 lives inside collection [4] with usages 0x301 (logical 0 to 1),
   0x302 and 0x304 (each 0 to 255). It reads `21 01 50 20`. Taking declaration order as
   0x301, 0x304, 0x302, that is a flag set to 1, then 80, then 32. The 32 is cells per row.
   The 80 is not yet accounted for.
8. Feature report 0x22, usages 0x5F1 and 0x5F2 in the application collection, reads
   `22 70 08`. Not yet interpreted.
9. Feature 0x20 is `SCREEN_READER_IDENTIFIER` and refuses to be read.
10. One HID interface only, no serial ports. The transport was Bluetooth: the hardware ID
    carries the Bluetooth HID service UUID 0x1124.

A boolean control sitting in the vendor collection that owns the display area looked like it
might gate the pin report. It does not: the pin report works with nothing written to feature
0x21 at all. What usage 0x301 means as a feature is still unknown, but it is no longer on
the critical path, and there is now no reason to write it.

## Geometry

Published figures, now partly confirmed:

1. The panel is exactly **96 by 40 equidistant pins**, 3,840 in total. Equidistant is
   the important word: a uniform grid, not clusters.
2. Braille layout spends **three pin columns per cell**, two dot columns and one spacer, so
   32 cells consume all 96 columns and 32 of those columns carry nothing on the cell path.
3. **Terminal mode gives 8 rows of 32**, confirmed — and the pin grid explains it exactly.
   96 is 32 cells at 3 columns; 40 is 8 lines at **5 rows each**, four dots and a gap. The
   two "missing" lines are not withheld, they are the inter-line spacing. An earlier version
   of this document said 10 lines of 4 with no spacer row, which was wrong: 8 × 5 = 40, and
   that is the whole of it.

Which gives two very different canvases:

- **Through cells:** 64 by 32 dots, 2,048 pins, contiguous vertically and gapped every third
  column horizontally. Vertical lines solid, horizontal lines dashed, fills striped.
- **Through the pin report, confirmed live:** 96 by 40, 3,840 pins, no gaps anywhere. An all
  ones payload raises the panel fully solid and a full width horizontal line is unbroken.
  For *graphics* that is the whole 40 rows. For *text* drawn into it, the 5 row pitch still
  applies, so it is the same 8 lines of 32 — the gain there is not more rows, it is that
  text and graphics can share one surface.

The cell path is now the fallback rather than the plan. It stays documented because it is
what every other HID braille display can do, and because a `hidBrailleStandard` device that
is not a Monarch will have no pin report at all — but on this hardware there is no reason to
draw through cells.

### The pin report's layout, established on hardware

One byte covers a **2 wide by 4 tall block**, and the blocks run in reading order, 48 across
by 10 down, 480 bytes in all. Within a block the byte is **an ordinary eight dot braille
cell**: dots 1 to 8, the same packing as the cell path, `_brailleDotCoords` read backwards.

	blockIndex = (y // 4) * 48 + (x // 2)
	bitIndex   = blockIndex * 8 + BIT_FOR_CELL_POSITION[(x % 2, y % 4)]

**Column major was written down here first and it was wrong.** The reading was bit 0 top
left, bit 1 below it, bit 4 to its right — left column in bits 0 to 3, right column in bits
4 to 7, which is the packing `dotPad.driver.DpTactileGraphicsBuffer` uses. It came from
walking single pins with `pinBit`, and a single raised pin on a blank panel does not say how
far down it is: dot 5 is right *and* one row down from dot 1, and it was felt as "to the
right".

Two drawings settled it, and either would have on its own:

- `pinVLine(0)` should raise the left column of every block — dots 1, 2, 3 and 7. Column
  major wrote bits 0 to 3, which the panel raised as dots 1, 2, 3 and 4: a column of **p**,
  each cell bumping out to the right at the top and missing its bottom pin.
- `pinHLine(0)` should raise the top pin of every block — dots 1 and 4. Column major wrote
  bits 0 and 4, which came up as dots 1 and 5: a row of **e**, a staircase rather than a
  line. That is precisely the "rhythm" the horizontal line was written to detect, and it
  detected it.

So the warning below about not copying the DotPad's packing applies to **both** paths. Its
graphic buffer is a column major nibble layout; the Monarch's firmware raises a byte's pins
by dot number whichever report the byte arrived in. Whether the DotPad's own graphic area
really wants column major is still not ours to settle — but nothing of ours should inherit
it.

**A round trip test proves nothing about a packing.** `pinLayout()` reported "round trip
clean for all 3,840 bits" for the mapping that drew p's: an index and its inverse can agree
perfectly about a geometry the hardware does not share. Only fingers, or a shape whose
misreading is unmistakable, can check this. It now prints all eight bits of the first block,
because that one cell is where the two candidate packings differ.

### Bit order on both paths, and why not to copy the DotPad buffer

`DpTactileGraphicsBuffer.setDot` in `brailleDisplayDrivers/dotPad/driver.py:56` packs a
pixel as `bit = (y % cellHeight) + ((x % cellWidth) * cellHeight)`, putting the left column
in bits 0 to 3 and the right column in bits 4 to 7. That is a column major nibble layout,
**not braille dot numbering**, and a cell packed that way renders scrambled — through a HID
`8 Dot Braille Cell` value on the cell path, and through the Monarch's pin report on the pin
path, which is where we did inherit it and had to be told by a panel full of p's. Whether
the DotPad's graphic area genuinely wants that or whether it is an upstream bug is not ours
to settle; we must simply not inherit it anywhere.

The correct mapping is the inverse of `tactile/braille.py`, list `_brailleDotCoords`: dot 1
is bit 0 at x 0 y 0, dot 2 bit 1 at x 0 y 1, dot 3 bit 2 at x 0 y 2, dot 4 bit 3 at x 1 y 0,
dot 5 bit 4 at x 1 y 1, dot 6 bit 5 at x 1 y 2, dot 7 bit 6 at x 0 y 3, dot 8 bit 7 at x 1
y 3. Derive it from `_brailleDotCoords` at import time so it cannot drift from core.

The pin report's bit order was a separate question and has the same answer; `pinBit` and
`pinVLine` found it out by touch. See "The pin report's layout, established on hardware"
above, including what a single walked pin cannot tell you.

## Where this lands in the add-on

**Superseded, and kept for the reasoning.** The pin report turned out to be live, so the
answer is the last paragraph of this section: a Monarch specific driver. It is built — see
[monarch-driver-plan.md](monarch-driver-plan.md) — and the five numbered points below
describe the cell path through `braille.handler`, which the Monarch does not take. Read them
for what a cell path fallback would need, not for what exists.

`BrailleHandler` writes one flat row major cell array and knows nothing about graphics. The
add-on composes that array through views, panels and segments, and a segment's cells come
from its regions. So:

1. A **`GraphicsRegion`** overrides `Region.update()` without calling `super()`. It sets
   `brailleCells` to the packed bitmap, sets `rawText` to a short description for speech and
   the braille viewer, and leaves `brailleCursorPos` as `None`.
2. A **`GraphicsPanel`** claims a rectangle in a view, alongside `BraillePanel`,
   `GridPanel`, `RowsPanel` and `SinglePanel` in `panels.py`, and reserves its segment so
   the flow never fills it.
3. The segment runs with **`fillRows` set**, because that already packs cells row major
   across the rectangle with no word wrapping, which is bitmap scanline order.
4. Its **routing policy** needs to report a pin coordinate rather than a text offset, and can
   get a real one: report 0x40 gives the touched pin directly. That has to be read from the
   device ourselves, since NVDA discards it — see "Touch" above.
5. The **cursor must be suppressed**: `_displayWithCursor` ors the cursor shape into a cell,
   which would put stray pins in a drawing.

If the pin report turns out to be live, none of that changes shape — but it stops going
through `braille.handler` at all for the graphics rectangle, because the pin report is a
separate report the handler knows nothing about. That is a driver level concern and would
mean either a Monarch specific driver or an extension to `hidBrailleStandard`. Worth
raising with NV Access rather than solving alone.

## What is worth drawing

At 64 by 32 gapped, or even 96 by 40 clean, this is a diagram surface, not a screen. Feed it
from structure, not from pixels. Ranked against what the add-on already knows:

1. **Charts from tabular data.** The Excel app module and the table machinery already have
   the numbers as numbers. A bar chart drawn from values is legible here and is something no
   other tool does from live application data.
2. **Structure diagrams.** Boxes and connectors for a tree or an outline, with real braille
   labels in adjacent cells.
3. **Position and extent indicators.** Where the window sits in a document, where a
   selection sits in a table.
4. **Simple line art** from applications with vector geometry to give.
5. **Screen capture, dithered.** Last, and mostly as a diagnostic.
6. Images from screen capture or web pages processed into a simplified tactile view such as with edges algorithms, or better: machine learning classifier of objects to then use tactile friendly established images as baselines.

## Plan

### Phase 0, hardware truth

Status: **complete**. The pin report is found, confirmed working, its layout established,
and the two questions that shape the design are answered.

Answered:

1. Geometry is 8 rows of 32, 256 cells on the braille path.
2. Output report 0x21 is a 3,840 bit array of usage 0x301 in the vendor collection 0x300
   that also owns the braille rows. 480 bytes, one bit per pin, accounting for the whole
   481 byte output report.
3. **It is live.** All zeros lowers the panel, all ones raises it fully solid. No mode
   switch was needed and no feature report was written.

4. **The bit order**: blocks of 2 by 4 in reading order, 48 across by 10 down, each byte an
   ordinary eight dot braille cell. 
Read as column major first, so byte 0 is first cell, bite 1 moves one cell to right on top row, etc.
Cells are Braille reading order, see discussion above. SPike.pinHLine() and spike.pinVLine() have been set to draw straight lines accounting for this geometry.

5. **The layout holds across the whole panel.** `pinHLine(0)` is solid and unbroken end to
   end, `pinVLine(0)` is a clean column, and `pinBox()` gives square corners with both
   diagonals. The diagonals read as short horizontal dashes marching across, which is
   correct rather than a defect: a corner to corner slope of 40/96 steps about 2.4 pins
   sideways per pin down.
6. **Pin writes and cell writes do not compose.** With `pinBox()` up, releasing the hold let
   NVDA write braille normally and the entire box vanished. The Monarch takes every write as
   a full display refresh. See the verdict; this is the finding that decides the
   architecture.
7. **Mechanical cost**, which is the real limit. Hand off is 17 to 22 ms mean and up to
   110 ms worst case, so the transport is irrelevant. The mechanism is not: it clatters
   audibly, and because the pins are magnetically driven **the reader has to lift their hand
   for a refresh to settle correctly**. This is a page turning device, not an animating one.

   **The pin report costs no more than the cell reports.** Later hardware use of the driver
   settled the obvious follow up question: a single 480 byte write to report 0x21 is
   indistinguishable in speed from the eight cell reports it replaces, and at the 10 row
   pitch it carries two more lines of content for that same write. Driving either 8 or 10
   rows through the 96 by 40 pin record has shown no ill effect. So the page turning
   constraint is a property of the mechanism, not of the report we chose.

Still open, and neither blocks phase 1:

1. **Does the cell path render arbitrary patterns?** `dotOrder()`. Only relevant to non
   Monarch hardware and to the fallback now.
** partial answer** there is a library possibly on python's pipy that does ascii art by converting to braille cells.
But we are not interested in this for Monarch DotPad which have tactile displays.

2. **Are the spacer columns filled on the cell path?** `spacers(1)`, `(2)`, `(3)`. Same
   demotion; comparing `fill(0xFF)` with `pinFill()` answers it as a by-product.
**Answer** whe all cells are filled in terminal cell writing mode, spacing dots are not raised.

Not to be done: writing feature 0x21. It was never needed, which settles the question of
whether to risk it.

### Touch, and why a graphics view can be interactive

The panel answers a finger with **two** reports, in pairs, and NVDA reads neither in a way
that survives:

	report 0x40, usage 0x401, 16 bits, logical 0 to 3840  -> the touched pin
	report 0x41, usages 0x402 to 0x501, 256 bits          -> the routing cell it falls in

Then both again as zero on release. NVDA's HID driver inspects input values only to find
`NUMBER_OF_BRAILLE_CELLS`, so 0x401 is discarded — which is why a touch on a graphic reaches
the log as an event with no number attached.

A deliberate tap produces exactly **four** reports, and a clean run of two taps accounted for
every one of them:

	0x40 with the pin      touch
	0x41 with the cell     touch
	0x40 with zero         release
	0x41 with all zeros    release

The panel is quiet at rest: two taps produced six reports carrying data and exactly two empty
ones, which are the two release 0x41s. Nothing arrives when no one is touching it.

That corrects two earlier readings of this, both recorded here so neither is repeated. A
watcher once filled a 200 entry buffer with empty 0x41s, which was written down as the panel
streaming continuously — it was hand contact while *reading* the graphic, which generates far
more releases than a tap. A later run appeared to show 500 such reports, which were not
reports at all but one report duplicated by a recursive callback chain of the spike's own
making. The volume under a reading hand is still unmeasured; the volume at rest is zero.

Usage 0x401 is a **row major** pin index over the 96 by 40 grid, one based, 0 for released:

	index = value - 1;  x = index % 96;  y = index // 96

Established from four touches, each checked against the routing cell the device itself
reported in the paired 0x41, which is `(y // 5) * 32 + (x // 3)`. All four agreed. Two
consecutive braille rows differed by 480, which is 96 by 5 — one line band. And logical max
3840 is the pin count exactly, so 1 to 3840 covers every pin with 0 left for "none", which
is the reading that makes the range fit.

Note this is **not** the block packing the output uses. Pins are *written* as 2 by 4 blocks
of braille dots and *read back* as a plain raster index: two different orders on one device.
Decoding the input with the output's mapping produces plausible nonsense, which is exactly
what happened here until the samples were checked against the routing cells.

The consequence is the good one. A graphics view is not limited to 32 by 8 rectangles — the
device will say which pin was touched, so pointing at a bar in a chart can name that bar and
its value. That is real interaction with a drawing, and it costs nothing to read.

One thing to be sure of before relying on it: whether the camera genuinely resolves to a pin.
The y values across the samples varied within a line band, so it is finer than a cell in at
least that axis, but four samples is not a calibration.

**Since confirmed for text, and that is most of the worry gone.** The Monarch driver derives
routing from the touched pin at the 10 row pitch, where the device's own cell index is
meaningless, and in real use it lands correctly in list items and edit fields. So the pin
index is trustworthy at cell scale under an ordinary reading finger, not merely in careful
laboratory taps. What remains unmeasured is *sub-cell* precision, which is what naming a bar
in a chart by touch would lean on.

### What the mechanism forces on the design

The refresh constraint is not a performance detail, it is a design input, and every phase
below assumes it:

1. **No live updating graphics.** A panel redrawn on caret movement would clatter constantly
   and, worse, would refresh under the reader's hand where it cannot settle correctly. The
   graphics surface has to change on deliberate events — a command, a page turn, a new
   object — not on every braille update.
2. **The driver owns the panel while graphics are up.** Since writes replace rather than
   compose, NVDA's ordinary braille output has to be suppressed for as long as a drawing is
   displayed, and any text wanted alongside must be drawn into the same buffer.
3. **So a graphics view is closer to a mode than a panel.** The add-on's existing model is
   panels tiling one display that updates continuously. A Monarch graphics view is better
   thought of as: enter it, the panel shows a composed image with its own braille labels,
   ordinary braille is held, and leaving it restores normal operation. 

### Phase 1, the buffer and the region

Status: **delivered on the pin path**, as part of the Monarch driver rather than as a region.

The pin report was live, so the conditional half of this phase became the whole of it.
`pinBuffer.py` in the driver package is the buffer: a plain 96 by 40 dot canvas with the
layout phase 0 established, unit tested for dot mapping, bounds clipping and packing, and it
imports nothing from NVDA. The driver exposes it as `newGraphicsBuffer()` and composites it
with text through `setGraphicsOverlay`.

`GraphicsRegion` was never built and is not wanted on this device. It existed to push a
bitmap through `braille.handler` as cell values, and once the driver owns the whole panel
there is nothing for a region to do: the graphics never travel as cells at all.

What that leaves unbuilt is the **cell path**: `CellTactileGraphicsBuffer` and the region
that would feed it. It is not dead work, it is the fallback for hardware that has no pin
report — a DotPad, or a Monarch on a firmware that loses it — and `CellCanvas` in the spike
is it prototyped, with the dot order confirmed by touch. Build it when there is a second
device to justify it, not before.

### Phase 2, the graphics view

Status: not started, and it is the gate. Nothing above the driver calls `newGraphicsBuffer`,
`setGraphicsOverlay`, `newGlyph` or `lastTouch` — the mechanism is complete and has no
consumer. Phases 3 to 5 all sit behind this one.

**Rewritten twice.** The original sketch was a `GraphicsPanel` tiling the display alongside
ordinary flow, and full panel refresh ruled that out. The rewrite after phase 0 then said a
graphics view must own the *whole* panel. The driver has since made that too strong, and the
correction matters for the design.

#### What the driver already does, which changes the shape

`_compose` draws NVDA's cells across the whole panel first and then blits each overlay on
top, all into one buffer, sent as one write. So graphics and braille already coexist in a
single pin report at the driver level. Nothing needs suppressing to get a drawing onto the
panel beside text, and `spike.pinMixed()` is no longer the thing to prove — the driver
proves it on every repaint.

What the add-on still has to supply is the other half: **keeping its own content off the pin
rows a drawing occupies**. An overlay blits over whatever text was composed underneath, so
text there is not corrupted, merely wasted — and the reader loses rows they were using. The
segment machinery has to reserve the rectangle, not the driver.

#### It is a mode, and the reason is state

A drawing needs somewhere for its own state to live, and **zoom** is the case that settles
it: a drawing that can be zoomed has a scale, an origin, and a history of both, none of
which belongs to a panel that merely tiles a rectangle. Panning, layers, a selected element
and "what does this touch mean here" all want the same home. So there is a graphics mode,
entered and left deliberately, and it holds that state.

The rest of the mode's behaviour follows from the mechanism rather than from taste:

1. It redraws only on deliberate events — a command, a zoom, a page turn, a new object —
   never on caret movement, because of the refresh cost and because the panel must not
   change under the reader's hand.
2. Its content is a `PinBuffer` drawn with the phase 3 primitives, with any text drawn in as
   pins so that caption and drawing arrive in the same write.
3. Leaving it restores what was there before, in one write.

#### The open question: does the mode contain panels, or know about them?

The mode does **not** have to take the whole panel, because rectangles are ours to assign
anywhere. A worked example: give a drawing the top 96 by 32 pins and leave the bottom 8 pin
rows to an ordinary braille segment, so the reader keeps a live line of NVDA braille under
the figure. The split has to be expressed in **pin rows** and land on a line boundary for
whichever pitch is active — a braille line is 4 pin rows at the 10 row pitch and 5 at the 8
row pitch, so 8 spare rows is two lines at 10 rows and one line with three rows of slack at
8. The 10 row pitch divides more usefully here, which is a second argument for it.

That leaves two candidate shapes, and this is genuinely undecided:

1. **The mode is panel aware.** It is a view like any other, and one of its panels is a
   graphics panel that owns a rectangle and holds the zoom state. The other panels are
   ordinary and keep flowing text. This keeps one compositor and one mental model, and makes
   the "figure on top, braille line underneath" example fall out for free.
2. **Panels belong to the mode.** Entering saves the active view, replaces it with a
   graphics arrangement the mode owns, and leaving restores it. This keeps graphics state out
   of `views.py` entirely, at the cost of a second way of arranging the display.

The first looks better on the evidence so far, because the driver already composites and the
add-on already assigns rectangles, which is most of what shape one needs. The thing that
would decide it is zoom: if zoom turns out to want gestures, a settings ring entry and a
status line of its own, the mode is heavier than a panel and shape two earns its keep.

Exit criterion: a real enter and leave, a figure and a live braille line on the panel at the
same time from the add-on rather than from the driver, ordinary braille intact on both sides,
and the reserved rows genuinely reserved.

### Phase 3, a drawing API

Status: **mostly delivered**, in `pinBuffer.py`, because the driver needed it first.

Built and unit tested: `setDot`, `clearDot`, `getDot`, `line` (Bresenham), `rect` outline and
filled, `polyline`, `blit`, `clearRect`, `contains`, and `fromRows`/`rows` for building and
inspecting a shape from text.

Still to add, and both are phase 2's customers rather than the driver's:

1. `marker` — a named small shape at a point, for data points and callouts.
2. `text(x, y, cells)` — braille into an arbitrary buffer at an arbitrary pin position. The
   driver draws text into pins already in `_drawCells`, but only for the whole panel at the
   current pitch. A caption inside a figure needs the same thing scoped to a rectangle.

Lattice compensation is not needed and never will be on this path: the pin report has no
lattice. It belongs with the cell path fallback if that is ever built.

### Phase 4, first real content

Status: not started.

One source, built properly. The recommendation is bar charts from an Excel column range: the
data path exists, the output is unambiguous to verify, and it demonstrates something the
Monarch cannot currently do from live application data. Resist building three half sources.

### Phase 5, image import

Status: not started.

Threshold, edge detect, downsample. Useful for tactile image library content and eBRF
embedded graphics. Expect it to be poor for photographs and adequate for line art, and
document that rather than tuning forever.

### Long game

1. Report the descriptor findings to NV Access whatever happens. An output button array on a
   HID braille device that NVDA structurally cannot see is upstream news regardless of
   whether it turns out to be the pin matrix.
2. Ask Humanware and APH what collection 0x300, feature usages 0x301, 0x302, 0x304 and the
   480 byte report are for. The Wing It application proves a full resolution path exists;
   this may simply be it, undocumented.
3. If the pin report is real, a review request adding graphics usages to page 0x41 has a
   working implementation behind it rather than a proposal. The reserved ranges 0x08 to
   0xF9, 0xFD to 0xFF and 0x102 to 0x1FF are empty and available.

## Open questions

1. **Answered for routing, still open for graphics.** In use at the 10 row pitch the touched
   pin is accurate enough to route into list items and edit fields reliably, so the worry
   that the camera might quantise below the cell did not materialise for text. What that does
   not establish is how finely a *drawing* can be pointed at — reading a data point off a
   chart asks more of the same signal than picking a cell does. Worth measuring against known
   positions before a graphics view promises sub-cell precision, but it is no longer a risk
   to the driver.
2. How does a graphics mode coexist with `views.py` — is the mode panel aware, or do panels
   belong to the mode? No longer a question about a *whole panel* mode: the driver
   composites overlays over text in one write, so a drawing can take a rectangle and leave
   the remaining pin rows to an ordinary braille segment. Zoom is the thing that decides it.
   See phase 2.
3. What is feature usage 0x301, currently reading 1? It gates nothing, since the pin report
   works without touching it, but it plainly means something.
4. What are 0x302 = 32 and 0x304 = 80? The 32 is cells per row; the 80 is unexplained.
   40 pin rows × 2 is a coincidence worth checking rather than believing.
5. Can text be drawn at a tighter than native pitch and still read? 40 rows at a 4 row pitch
   is 10 lines instead of 8, at the cost of the inter-line gap.
**Answer** Yes, Monarch already does 10 lines natively on its platform. It gets crowded with a cursor, but long form reading with standard 6-dot braile and 10 lines is very useful.

6. Does a partial redraw exist? Every write seen so far refreshes the whole panel, which is
   what makes this a page turning device. If some report updates a region, the mechanical
   cost argument changes completely. Less pressing than it looked: a full 480 byte pin write
   turns out to cost no more than the eight cell reports it replaces, so nothing is being
   lost by writing the panel whole. What a partial redraw would buy is a quieter and less
   disruptive update under the reader's hand, not throughput.
7. Is the DotPad bit order an upstream bug? Now doubly worth asking, since the Monarch wants
   braille dot numbering on a report that is unambiguously a graphics buffer.

## Sources

1. [HUTRR78, Creation of a Braille Display Usage Page](https://usb.org/sites/default/files/hutrr78_-_creation_of_a_braille_display_usage_page_0.pdf)
2. [Braille Display Page (0x41) usage table](https://www.usbzh.com/article/detail-989.html)
3. [HID Usage Tables version 1.6](https://www.usb.org/sites/default/files/hut1_6.pdf)
4. [NVDA PR 12523, HID braille standard support](https://github.com/nvaccess/nvda/pull/12523)
5. [NVDA PR 17007, native DotPad support](https://github.com/nvaccess/nvda/pull/17007)
6. [NVDA PR 18248, multiline braille routing on Monarch](https://github.com/nvaccess/nvda/pull/18248)
7. [Monarch technical specification](https://www.humanware.com/wp-content/uploads/Monarch-Technical-Specification-Document.pdf)
8. [Monarch, American Printing House](https://www.aph.org/product/monarch/)
9. Local trees: `C:\code\nvda\source\brailleDisplayDrivers\hidBrailleStandard.py`,
   `C:\code\nvda\source\brailleDisplayDrivers\dotPad\`, `C:\code\nvda\source\tactile\`
10. First hardware run of `spikes/tactileGraphicsSpike.py`, Monarch over Bluetooth.
