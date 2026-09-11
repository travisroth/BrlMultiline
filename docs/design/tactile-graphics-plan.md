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

Status: **built, and largely confirmed on hardware.** All nine pieces below exist, 80 tests
cover them, and the modules import and answer correctly inside a real NVDA.

Confirmed by a finger on a Monarch: a figure shows in a claimed rectangle with a live braille
line beside it; a routing press inside the figure reports what is under the finger, correctly,
after two attempts at the geometry; and the braille line toggles away and back for a full
panel drawing.

Not yet confirmed, all of it built after the last hardware session: the priority ladder and
the flow standing down while a figure is up, the fit-first zoom with its compression, and the
new position wording. Nor has anyone yet checked the two quiet ones — that the reserved rows
stay free of text, and that leaving restores ordinary braille exactly.

The gate it was is open: `graphics.py` and `graphicsMode.py` consume `newGraphicsBuffer`,
`setGraphicsOverlay`, `clearGraphicsOverlay` and `lastTouch`, so the driver's mechanism has a
caller at last.

`newGlyph` and `setCellGlyphs` now have one too, and it is not a figure. **The glyph phase is
complete and on hardware**: the shapes are read under a finger in browse mode and in ordinary
application windows, and the cells they give back are used by the wrapping. It is
`glyphs.py`, which holds the shapes and the notation, and `glyphFlow.py`, which decides where
one is drawn: over the short words NVDA already writes for a role or a state, and nowhere else.
A word is a role because of where it came from — `TextInfoRegion._addFieldText` for a document,
the object's own name and value and description for an object — never because of what it says.
`segments.BrailleBufferSegment` compresses each region before the buffer cuts it into rows, so
the cells a shape gives back are cells the wrapping can use, and `container` registers what
landed where on each frame. Off by default, per display, under "Draw roles and states as shapes
instead of words".

Two limits worth naming. **A cell of a table row keeps its words**: those positions are packed
with the column they came from — see `flowTable.cellPosition` — so a mark naming one would
never be found again. And **a role NVDA composes rather than looks up is missed**: a list with a
child count is written "lst5", and the matcher wants the bare label. A missing shape is the safe
failure of the two.

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

Two shapes were considered:

1. **The mode is panel aware.** It is a view like any other, and one of its panels is a
   graphics panel that owns a rectangle and holds the zoom state. The other panels are
   ordinary and keep flowing text.
2. **Panels belong to the mode.** Entering saves the active view, replaces it with a
   graphics arrangement the mode owns, and leaving restores it. This keeps graphics state
   out of `views.py` entirely, at the cost of a second way of arranging the display.

**Decided: shape one.** The claiming machinery already exists and does exactly what is
needed — `BlankPanel` reserves cells and emits no segments, `SegmentView.withPanel` lays a
claim over the active view without disturbing the rest, and the plugin re-composes
`_activePanels` on every rebuild. The driver already composites overlays over text in one
write. So a graphics rectangle beside a live braille line costs nothing structurally, and
shape two would be a second arrangement mechanism bought for nothing.

Shape two is not ruled out forever. What would earn it is zoom turning out to need its own
gestures, settings and status line rather than living on a panel owner — and that will be
known by the end of piece 6 below rather than by arguing about it now.

#### What phase 2 builds

Nine pieces, in build order. All nine are built; where the answer differed from the plan it
is recorded under the piece.

1. **`graphics.py`, the capability seam.** Built. A new module in `globalPlugins/brlMultiline`
   following the rule `devices.py` already states: read the live display object, duck type
   it, never import the driver. It answers whether there is a drawable surface, its pin size,
   its cell and glyph sizes, and hands out a buffer through `newGraphicsBuffer`. Where there
   is no such surface it says so and everything above degrades to text.

2. **Reaching the display through the composite.** Built, and it was the risk it was
   expected to be. This is the piece with real risk and it
   comes second because the ordinary configuration hits it. With `brlMultilineVirtual` as the
   display, `braille.handler.display` is the composite, and its `__getattr__` proxies driver
   *settings* names only — `setGraphicsOverlay` is not reachable through it. So `graphics.py`
   finds the member that can draw, the way `devices.py` already reads `display.slots`, takes
   that slot's driver object directly, and carries `slot.band` as the offset between composite
   rows and that member's own rows. Overlay coordinates are member local, so the band is
   subtracted, not added.

3. **`GraphicsPanel`, in `panels.py`.** Built. It claims a cell rectangle and holds **one
   blank reserved segment**, which was not the original design: the plan said no segments at
   all, so that nothing writes text under the drawing. Nothing does — but a routing press
   reaches the add-on only through the segment it lands in, and `DisplayContainer.routeTo`
   drops a press falling in no segment with a debug line. The segment exists to carry a
   routing policy and for no other reason. See piece 7. A claim on a cell rectangle that produces no segments,
   so nothing writes text under the drawing. `BlankPanel` is already three quarters of this;
   what is new is that the panel knows the pin rectangle its cells correspond to and has an
   owner. Modelled on `FlowPanel`, which claims a band and leaves the content to a controller.

4. **Cell rectangle to pin rectangle, and back.** Built as `GraphicsSurface.pinRectForCells`
   and `cellForPin`, and it needed neither the pitch nor the Monarch: `pinsPerRow` is the pin
   height divided by the cell rows and `pinsPerCol` the width divided by the columns, which
   gives 5 and 3 at the 8 row pitch and 4 and 3 at 10 without either number being written down. Panels speak in display rows and columns;
   the driver speaks in pins. A braille line is 5 pin rows at the 8 row pitch and 4 at the 10
   row pitch, a column is 3 pins wide, and the composite band offsets the whole thing. One
   function, both directions, unit tested on its own. This is where the arithmetic mistakes
   will be, so it is isolated and tested before anything depends on it.

5. **The mode object, a `PanelOwner` that holds the drawing.** Built as `GraphicsMode`.
   The plugin calls `_carryOverGraphics` beside `_carryOverFlow` after each rebuild; a graphics
   claim has no segments to look for, so the test is whether its panel survived the rebuild. It owns the source drawing at
   its natural size, the current scale and origin, the panel claim, and the enter and leave.
   Enter composes the claim onto the active view through the existing path, renders, and
   pushes one overlay. Leave clears the overlay, drops the claim, and rebuilds. `onEvicted`
   and `onTerminate` clear the overlay too, or a drawing outlives its claim and sits on the
   panel with nothing owning it.

6. **Zoom and pan.** Built, integer levels 1 to 8, zooming about the centre of the claim so
   that what is under the reader's hand stays under it. Scale and origin applied over the source, re-rendered into a fresh
   `PinBuffer`, one overlay write per change. This is the state that justifies the mode
   existing rather than being a panel that tiles a rectangle. It also sets the redraw
   discipline: deliberate events only — a command, a zoom step, a new object — never caret
   movement.

7. **Touch read back, which had to become a routing press.** The plan had this as a command
   reading `lastTouch`, and that cannot work — twice over, and the design is better for both.

   The first reason is the reader. On this hardware you point by putting a finger on the
   panel and pressing a routing key there. That is one hand doing one thing. A separate
   keyboard command needs the hand off the panel, and a reader has no third hand to hold the
   position with.

   The second is the protocol, and it is fatal on its own. The panel reports the touched pin
   as zero the instant the finger lifts, and NVDA runs a gesture's script from a queue rather
   than during dispatch — so even the press's *own* live touch has gone by the time any
   script sees it. A command reading `lastTouch` would have answered nothing for every press
   ever made.

   So the driver publishes `lastRoutingPin`, the pin under the finger when the routing key
   went down, set at gesture construction and left standing until the next press. The claim's
   segment carries a `GraphicsRoutingPolicy`, and a press inside the drawing reports what is
   at that point of the source instead of routing a cursor — there being no text under a
   drawing to route into. Where a drawable display reports no pin, the middle of the pressed
   cell is used: coarse at 3 by 5 pins, and an answer rather than silence. Map `lastTouch` from a pin to a point in the source through the
   inverse of pieces 4 and 6, so pointing at the figure can say what is under the finger. The
   driver already publishes the pin and nothing consumes it. Cell scale accuracy is proven;
   sub-cell is not, so build the transform and find out rather than promising precision first.

8. **Two missing primitives in `pinBuffer.py`:**  Built, and they removed a duplicate rather
   than adding one: the braille dot table now lives in `pinBuffer.py`, with `monarch.py`
   re-exporting it under the name the packing is reasoned about by. They are: `marker`, a named small shape at a point, and
   `text(x, y, cells)` so a caption can be drawn inside the figure rather than only across the
   whole panel at the current pitch.

9. **Commands, and these ship bound.** Toggle, zoom in and out, pan in four directions, and
   report the drawing. Every other command in this add-on is unbound on purpose; these are
   not, because of where the reader's hands are. A keyboard command for zoom or pan means
   taking a hand off the figure at exactly the moment the reader is keeping track of where
   their finger was.

   They sit on the display's own keys, chosen to be collision free rather than mnemonic.
   `hidBrailleStandard`'s gesture map, which the Monarch driver inherits whole, uses no chord
   containing dot 7 or dot 8 anywhere, so all of this is in space the standard map left
   empty:

   - space+dot7+dot8 shows or hides the drawing
   - space+dot8 magnifies, space+dot7 shrinks
   - space+dot7 with dot 1, 4, 3 or 6 pans up, down, left or right — the arrow chords the
     standard map already defines, with dot 7 added

   Named for `brlMultilineMonarch` rather than `hidBrailleStandard`, deliberately: the
   gesture offers both identifiers, and binding the standard one would take these chords on
   every HID braille display, including ones that cannot draw. All rebindable in Input
   Gestures under BrlMultiline. Written out four times rather than generated, unlike the
   segment commands, because the metaclass collects a decorator's gestures from the class
   body and never sees a script attached afterwards.

#### What phase 2 does not build

The chart is phase 4 and image import is phase 5. Phase 2's content is a fixed test figure —
a box, a diagonal, a marker, a caption — because the point is the enter, the claim, the
transform and the leave, not the drawing. The glyph vocabulary stayed out of phase 2 as well;
it is a separate decision about what to draw over ordinary braille rather than over a panel,
and it is now built. See the note above.

#### What the first hardware run found

Two faults, both in pieces that the unit tests had agreed with because the tests had been
written from the same wrong assumption.

1. **The claim fought the focus segment, and the first fix for it was wrong too.** With the
   focus segment on the Monarch, showing a drawing failed with "the display could not give up
   those rows"; with it on the Focus 80 the figure appeared. `SegmentView.withPanel` refuses a
   claim that evicts the focus segment unless the new panel offers one in its place.

   The first fix left the focus segment's *rows* out of the claim, and was refused on hardware
   all the same: `LookupError: Panel 'graphics' would evict the focus segment
   'device.brlMultilineMonarch.0'`. **Panels are evicted whole.** A claim on part of a band
   takes the band's entire panel with it, focus segment included, however carefully the claim
   avoided that segment's own rows — and on a composite a member's rows are exactly one device
   panel. Leaving rows free is not the same as leaving a panel alone.

   So `GraphicsPanel` now claims the whole band and supplies the replacement braille line
   itself, as a second segment offered through `focusSegmentKey` — the mechanism `withPanel`
   provides for this, and the only way a claim may take a focus segment at all. The panel holds
   two segments: an ordinary text line at the top that hosts the focus and routes normally, and
   the reserved blank one below it that carries the graphics routing policy. With no text line
   the panel offers no focus segment, so such a claim is refused rather than quietly losing the
   focus under the drawing.

   That also gives the arrangement this plan wanted without asking for it: the live braille
   line beside the figure is the focus line, which is the one worth keeping.

2. **A fingertip is far wider than a pin.** Touching the caption, the diagonal and the top
   border all reported "blank", missing by 1, 2 and 3 pins. Reporting what is at the single pin
   the panel names makes a line one dot wide almost unfindable: the contact centre lands beside
   it nearly every time, because a finger pad rests below the fingertip tracing the ridge and
   the camera reports one point for the whole patch. The report now names the nearest raised
   dot within half a braille line, rounded up — 3 pins at the 8 row pitch, 2 at 10 — divided by
   the zoom, since magnification is the act of making a source dot bigger than a finger. Set
   from three deliberate touches, so it is a starting point from a small sample rather than a
   calibration.

#### The priority ladder, and the flow

The second hardware session turned up a design gap rather than a fault. The flow is the
add-on's default and is wanted in Excel and on the web, which is most of a working day — and
both the flow and a drawing want the same rows, because both default to the tallest display.
Whichever claim was made most recently took them, so showing a figure became a fight with the
flow, and a profile switch or a focus change could take a figure off the display mid-read.

So claims are ranked. `panels.py` carries the ladder — ordinary 0, flow 10, graphics 20 — and
the plugin composes them lowest first, each laid over what is already there, so the highest
number wins. Ties keep the order they were made in, which is what decided them before.

Graphics outranks the flow because it is deliberate and temporary: a reader turns a figure on,
reads it, and turns it off, while the flow is a standing preference that should resume by
itself afterwards, and does. Nothing else is ranked; a table claim and a pinned object have
never wanted the same rows as each other.

The ladder alone would still cost a claim, an eviction and a teardown on every rebuild, since
the flow would go on claiming rows it was going to lose. So while a figure is up the flow does
not claim at all, and leaving the figure rebuilds the display and brings it straight back.
That rule is deliberately not conditioned on the two wanting the same rows: in practice they
always do, and "while a drawing is up, the flow waits" is one a reader can hold in their head.

#### Fit first, and what zoom is for

The zoom model was wrong for real content and was corrected against the Monarch's own tactile
viewer, which is the right reference: **step 0 shows all of the drawing**, compressed as far
as it needs to be, and panning does not exist there because nothing is off the edge. Zoom is
what makes panning mean anything — past the point where the drawing no longer fits, moving the
window is the only way to reach the rest of it.

The first version made step 1 the natural size, one source dot per pin, which for anything
larger than the panel showed the top left corner and demanded panning to discover the rest.
That is backwards: a reader wants the shape of the thing first and the detail second.

Three things follow, and each is a decision rather than an implementation detail:

1. **Fitting only ever shrinks.** A drawing smaller than the panel comes out at the size it
   was drawn rather than blown up to fill it, so a chart authored at the panel's own size is
   shown as authored. Magnification is always something the reader asked for.
2. **A pin is raised if *any* source dot it covers is raised**, rather than sampling the middle
   one. Compression is where a tactile drawing is most easily ruined: a line one dot wide
   reduced to a quarter is missed by three sample points out of four and comes out dashed or
   gone. Taking any dot keeps every line the drawing had, at the cost of thickening a dense
   area into a solid one — which is the right way round, because a reader can feel that a
   region is busy and cannot feel a line that is not there.
3. **The touch search radius follows the scale in both directions.** It is a fingertip
   measured in source dots, so it grows as the drawing is compressed — one pin then stands for
   several source dots — and falls to nothing as it is magnified, where a finger can be placed
   exactly.

The ladder is five doublings from fit, capped at 8 pins per source dot, beyond which a single
source dot is wider than a braille cell and the reader is feeling the magnification rather
than the figure. Panning at fit reports that the whole drawing is shown rather than claiming
an edge that does not exist.

**Where the window is, is said as a fraction of how far it can move.** The origin is held as a
source dot, which is the right thing to compute with and the wrong thing to say: "at 48, 18"
is a fact about how large the drawing happens to be in dots, not about what the reader is
feeling, and panning by a quarter of the view gave a fresh pair of numbers each time with
nothing to measure them against. So it is reported as a percentage of the pannable range — 0
hard against one edge, 100 hard against the other — with the ends named rather than numbered,
because reaching an edge is worth hearing as an edge. An axis that cannot move is left out
rather than reported as a meaningless zero, so a drawing wider than the display but not taller
says only how far across it is. Panning reads "25 across, 51 down", then "left edge, 51 down".

Touches keep reporting source dots, deliberately: "raised at 26, 9" is a position *within the
drawing*, which is a fact about the drawing and stays true however the window moves.

#### Giving the whole panel to the figure

A figure on the Monarch is 96 by 35 pins with a braille line kept beside it and 96 by 40
without — a seventh more, across the middle of the panel where the reader's hands already are.
`GraphicsMode.setTextLines` changes it without leaving the figure, so zoom and origin survive,
which is the point of being able to change it mid-read at all.

At zero the drawing segment hosts the focus itself and is `exclusive`, so NVDA's focus regions
are handed to the owner and dropped rather than written into cells the figure is composited
over. That is a real cost, it is stated when it happens, and it is undone by the same command.
A reader with a second display does not pay it: their focus segment is over there, untouched,
which is the arrangement to prefer where the hardware allows it.

#### Exit criterion

**Met.** The hardware pass found:

1. The flow gives its rows to a figure and comes back by itself on leaving.
2. Fit shows the whole drawing with its thin border intact, and zoom reads correctly in and out.
3. The rows under a figure carry no text, and the focus line is kept where it is wanted.

One fault came out of it — the flow came back at the top of the document rather than where the
reader had been — and the fix has since been read on hardware. See "suspended, not stopped"
above. **It is better rather than perfect**, and deliberately left there: where the reader comes
back to depends on what the application did with the focus while the figure was up, which is not
wholly ours to decide. Good enough to close the phase on, and worth revisiting only if a
particular application is found to lose the place badly.

The Monarch showing a figure in a claimed rectangle with a live NVDA braille line underneath
it, entered and left by command, ordinary braille intact on both sides, the reserved rows
genuinely holding no text, zoom changing the figure and nothing else, and a routing press
inside the figure reporting the point under the finger. Under the composite as well as with the Monarch driven
directly, because that is the configuration actually in use.

### Phase 3, a drawing API

Status: **delivered**, in `pinBuffer.py`, because the driver needed most of it first and
phase 2 needed the rest.

`setDot`, `clearDot`, `getDot`, `line` (Bresenham), `rect` outline and filled, `polyline`,
`blit`, `clearRect`, `contains`, `fromRows` and `rows` came with the driver. `marker`, which
stamps one of a few named shapes centred on a point, and `text`, which draws braille cells
into a buffer at an arbitrary position and stride, came with phase 2. All unit tested.

Lattice compensation is not needed and never will be on this path: the pin report has no
lattice. It belongs with the cell path fallback if that is ever built.

### Phase 4, first real content

Status: **on hardware, and read.** Bar charts from an Excel selection, in `chart.py` and
`chartSource.py`, with 30 tests. One source, built properly, as the plan asked.

`chart.py` knows nothing about NVDA or Excel: labels, numbers, and a way to make a buffer in,
a drawing out. `chartSource.py` is the Excel half and is the only thing that would be written
again for a second application.

Four decisions worth keeping:

1. **Stored values for the bars, displayed text for the labels.** A percentage stored as 0.25
   and shown as "25%" has to be charted as 0.25 or the bars are in the wrong proportion to each
   other, and has to be *called* what the reader sees in the sheet. Getting that backwards
   would produce a chart that is entirely plausible and entirely wrong.
2. **A chart is drawn at the size of the space it is going into**, so the fitted view is its
   natural one and zoom is for looking closer at a bar rather than for discovering what the
   drawing was. This is what `fitScale` never magnifying is for.
3. **A bar answers for itself.** `Drawing` gained an optional `describeAt`, and the graphics
   mode asks it before looking for the nearest raised dot — so a routing press on a bar says
   which bar and what it is worth, and the space above a short bar still belongs to that bar.
   Without it a reader learns that one bar is taller than another and never learns what either
   of them is, which would have made the whole path a demonstration rather than a tool.
4. **Refusals carry a reason.** Not a spreadsheet, nothing numeric selected, more bars than the
   panel holds — each is something the reader can act on, and a command that only said it had
   failed would leave them guessing at which. Charting the first twenty of sixty values would
   be a different chart drawn silently, which is worse than saying it does not fit.

Two smaller ones that the tests exist to hold: a value too small to round to a pin still gets
one **clear of the baseline**, because bare floor reads as a missing value rather than a small
one; and a bar of zero does not punch a hole in the baseline, because a reader sweeping the
floor should feel it continuous with bars standing on it.

**What the first hardware press found: selecting cells takes the seam away.** Charting failed
with "charts need a spreadsheet cell" while the reader was in a spreadsheet with cells
selected, which is the most confusing form a refusal can take. NVDA builds an `ExcelSelection`
rather than an `ExcelCell` for as long as more than one cell is selected — a different class on
a different branch — so the cell overlay does not apply to it and `brlMultilineSheet` was not
there. The seam vanished at exactly the moment a reader had selected something to do with.

`SpreadsheetSelection` now offers it. The selection object can answer: NVDA gives it
`rowNumber`, `columnNumber` and a worksheet parent, which is all `ExcelSheet` reads, plus
`excelRangeObject` — the selection itself, and a better answer than asking Excel what is
selected now. It offers the seam and not `setFocus`, because going to a selection is not a
thing; a routing key lands on a cell.

It was found by a log line rather than by reasoning, which is what the INFO logging was added
for one press earlier:

	BrlMultiline: charting from ExcelSelection role=TABLECELL
	name='A1  June through B4  5000', grid=False

**Bars alone were not enough, and hardware said so first.** A chart of bare bars says which
is larger and nothing else: the reader can feel the shape of the data and has to point at every
bar to learn what any of it is. So the chart is written on — values across the top, labels
across the bottom, four pin rows each, which is one braille line. It reads in one pass now:
the bottom for the categories, the top for the numbers, the middle for the shape, and pointing
becomes the way to ask about one bar rather than the only way to read the chart at all.

Three decisions in that:

1. **Left aligned with the bar, not centred under it.** Centring is what a printed chart does
   and it is the wrong choice for a finger: a reader following a bar downwards hits its label
   where the bar's own left edge is, and a centred label would start somewhere that depends on
   how long it happens to be.
2. **A label that does not fit is cut; a value that does not fit is left out.** They are not
   the same kind of thing. "Wednes" is recognisably Wednesday and is worth having, whereas
   "330" for 33000 is a different number said with confidence — so a value is drawn whole or
   not at all, and pointing still gives it.
3. **A short chart is drawn bare.** Below about fourteen pin rows the writing would take so
   much of the chart that the bars stop being comparable, which is the one thing a bar chart
   is for.

**A message raised from a routing press dismisses itself.** Reported from hardware: pointing
at a bar spoke the answer and never showed it in braille, while ordinary flash messages worked
normally. `BrailleHandler.routeTo` runs the routing policy and then, if a message is up,
dismisses it — because a cursor routing key is how a reader dismisses a message, which NVDA's
own docstring says outright. A message raised from *inside* the policy arrives before that
check and is read as the press's own dismissal.

So the report is queued onto the event queue and lands after the press has finished, when it is
an ordinary message with an ordinary timeout. The tests now model the event queue rather than
running queued work immediately, because *when* something runs was the whole of the fault: it
passed every test until the queue was modelled, and no log would have shown it.

**What the hardware said, and it was not what the panel could hold.** A bar chart of this size
reads under a finger, and the useful number of bars per page is not set by the 48 the panel can
carry but by the labels — the same constraint a table has. About four named bars to a page is
what a reader can actually work with, and beyond that the answer is to turn the page rather than
to draw narrower bars. Which is what the redrawn zoom already does, and is the argument for it:
forty bars two pins wide with no room for a name is not a chart anyone can read, and ten bars of
nine pins each, every one named, is.

### Phase 4b, the other chart types, and asking which one

Status: **one series of a line chart is on hardware and reads; the rest awaits a run.** Line
charts of up to four series, open-high-low-close bars, and candlesticks, in `chartLine.py` and
`chartPrice.py`, with the
vocabulary they share with bars split out into `chartDraw.py` and the reading half extended in
`chartSource.py`. 113 more tests.

The case that drove it is a stock chart: a closing price with a moving average through it and
a pair of Bollinger bands around it, then the same instrument as OHLC bars. That one case
forced five decisions.

1. **One scale for every series on a line chart.** A band that is not on the same scale as the
   price it bands is not a band, and four series each fitted to their own range would draw four
   lines that all fill the panel and mean nothing against each other. This is the whole reason
   they are drawn together rather than as four charts.
2. **Lines are told apart by texture, not by weight.** There is no colour, no greyscale and no
   line width to spend: every line is one pin thick. So each series gets a pattern along its
   own path — solid, dashed, dotted, dash dot — which is what tactile graphics standards
   already do, and which a finger reads as a difference in surface rather than in position. The
   pattern runs along the path rather than across the drawing, so a steep line dashes at the
   same rate as a flat one, and its phase carries across the joins between points; restarted at
   each point, a chart with points three pins apart would draw the first dots of the pattern
   over and over and every series would look solid.
3. **Four series is the limit, and it is a real one.** It is how many textures can be told
   apart by touch on a path that is often diagonal. A fifth would have to repeat one, and two
   lines with the same texture crossing each other is a chart that lies. Refused with a count
   rather than drawn.
4. **A gap in a series is drawn as a gap.** A twenty day moving average has nineteen empty
   cells at the top of its column. Read as zeroes, the chart draws the average diving to the
   floor and climbing back out, which looks exactly like a crash and is entirely convincing —
   the kind of wrong a chart can be while looking right. So a run of missing values breaks the
   line, and the reader feels the average start where the data starts.
5. **The frame is the same on every chart that has an axis, and both lines ascend.** The top
   line is the value range, the bottom line is the period range, always — and each reads left
   to right in ascending order: lowest to highest, earliest to latest. A drawn axis with ticks and numbers up the side
   would cost a third of the width and say less, because at ninety-six pins a number beside a
   tick has nowhere to be; four corners say what the top is worth, what the bottom is worth,
   where the data starts and where it ends, and pointing covers everything in between.
   Consistency between chart types is worth more here than on a screen: there is no glance, so
   every convention the reader does not have to re-learn is time they get back.

The order took a hardware run to get right. The high was written at the left, on the reasoning
that the top of a plot is its high — which does not survive the two numbers being on the same
line, where up and down mean nothing and only left and right are left. It read backwards, and
the rule that replaced it is the one that does not have to be memorised: a range is said low to
high, and the dates underneath already run earliest to latest.

**Why the OHLC bar shape survives the translation to pins.** It is made of exactly the three
strokes this resolution can carry: a vertical stem for the day's range, a tick left for the
open, a tick right for the close. Three pins of bar and one of gap is the narrowest that can
carry a stem with something on each side of it, so a ninety-six pin panel holds twenty-four
periods — about a trading month. Nothing in it depends on colour or on line weight. The ticks
point the way they do for a reason a finger can use: sweeping left to right along the row of
stems, a tick met *before* its stem is an open and one met *after* it is a close.

Candlesticks are the same four numbers with a different bet. The body is a mass rather than a
stroke, which is easier to find and gives the size of the day's move directly; the cost is that
rising and falling have to be told apart by the body being hollow rather than filled, and at
three pins wide a hollow body is one column of gap. Whether that reads under a finger is a
hardware question, which is why both are offered and the reader decides — and it is the one
thing in this phase most likely to come back changed.

The wick is what threatens the hollow. Drawn up the whole range with the body over it, it fills
that one column and every candle reads as a falling one — a chart that is wrong about the
direction of every day in it, and wrong invisibly. So the wick is drawn only above and below
the body, which is how a candlestick is drawn anyway.

**The chart is asked for rather than guessed at.** Four columns of numbers with dates down the
side are four measurements over time if they are measurements and a candlestick chart if they
are one instrument's trading, and nothing in the cells distinguishes those. So the command puts
up a list of the charts that can actually be drawn from what is selected, each entry saying
what it would draw — "Line chart, Close, MA20, Upper, Lower, 30 points" — and the list opens on
whatever the reader picked last time, because charting a sheet is usually charting it several
times over. With only one chart possible there is nothing to choose and nothing is asked.

Two rules about the list itself, and they pull in opposite directions on purpose:

- **Only what will work is offered.** A column of sales figures offers bars and a line and
  nothing else, so the list is short and every entry in it is a chart the reader can have. A
  chart type *setting* would have offered candlesticks over a column of sales and failed
  afterwards, which is the same failure moved somewhere less useful.
- **The refusal still arrives when it is useful.** A price chart is offered whenever there are
  four numeric columns, and the check that they really are open, high, low and close happens
  when it is drawn. Offered and then explained beats not offered and unexplained: a reader who
  selected their columns in the wrong order is told which period gave it away, which is a thing
  they can go and fix. A line chart of more than four columns goes the same way, and it is the
  common case for a stock sheet — date, open, high, low, close and a moving average is five
  series — so the reader picks the line chart, is told that five cannot be told apart and that
  four fit, and selects the columns they actually wanted plotted. Worth watching on hardware:
  if that costs a round trip often enough to grate, the answer is a shorter selection rather
  than a chart that silently drops a series.

**Two traps in reading the columns, both of which draw a convincing wrong chart.**

The first is the date column. Excel stores 3 June as 45806, so a column of dates is numeric to
anything that only looks at what is *stored* — and a stock chart whose first series is the
dates would be a straight line climbing off the top of the panel, drawn with complete
confidence. What tells them apart is that a date does not *display* as the number it holds, and
the grid already carries both halves, so the test costs nothing. It is the same distinction
that made `Value2` the right answer for a bar's height and the displayed text the right answer
for its name; this is that decision paying for itself a second time.

The second is the heading row. A row with no numbers in it is a heading row, which is right
about every table a reader would think to select — except a first row whose only numeric column
happens to be empty, which is data with a hole in it and looks identical. So a heading row must
also *name* every column that has numbers under it: a column with numbers below and nothing
above says the row was data, and taking it away would have dropped a point off the chart and
said nothing.

#### What the second hardware run found: Ctrl+Space froze NVDA

Reported from a sheet built with `STOCKHISTORY`, charted after selecting a column with
Ctrl+Space — which is the right key to press, and is what a reader should be pressing rather
than arrowing down forty-eight rows. The log:

	BrlMultiline: charting from ExcelSelection name='A1  4/1/2026 through B1048576  '
	Starting freeze recovery after 10.045 seconds
	exceptions.CallCancelled: COM call cancelled

Ctrl+Space selects the whole column: 1,048,576 rows by two columns, two million cells. The
read asked Excel for all of their values, got them, built a million Python rows out of them,
and then began fetching the displayed text one row at a time — a COM call per row, a million of
them. NVDA's watchdog cancelled the call ten seconds in and the command reported "nothing is
selected to chart", which was true of nothing.

Three fixes, and the first of them turns the fault into the feature that was asked for.

1. **The selection is clipped to the used range.** Excel's used range is one call and says
   where the sheet stops, so the intersection of it with the selection is the data the reader
   was pointing at: a whole column selection becomes the seventy-three rows that have prices in
   them. Both corners, not just the far one — a table starting at row 5 has four empty rows
   above it, and clipping only the bottom would put four blank points in front of the data.
   This is what makes Ctrl+Space the right key rather than a trap.
2. **Ceilings behind it, which hold whether or not the clip worked.** Five hundred rows, sixty
   four columns, and four thousand cells over both — because rows and columns capped separately
   still multiply, and a wide selection is not far-fetched: Excel's used range grows to whatever
   has ever been *formatted* and does not shrink, so a sheet with a fill once applied across it
   reports itself sixteen thousand columns wide and the clip gives nothing back. Rows come down
   to meet the cell budget and columns never do: the columns are which series there are, and a
   chart missing one is a different chart drawn silently. The caller says how much it can use
   and the ceiling applies either way, because a limit a caller can forget to pass is not a
   limit — and what is being prevented is a screen reader that stops.
3. **The text of a selection is read as one rectangle.** `textRow` is a call per row, which is
   the right shape for reading a table a band at a time: a few rows, every column. A chart is
   the other shape — a few columns, hundreds of rows — so a call per row is a call per *point*,
   and ten milliseconds a row was measured on hardware. The helper takes an address and a count
   and does not care that the range is more than one row high, so the whole selection is one
   call. The cells are placed by the coordinates the helper reports and never by their position
   in the answer: a row's worth can be counted along because there is only one row for them to
   be in, a rectangle cannot, and a range walked in a different order than assumed would put
   every value under the wrong label while looking entirely correct. A block that comes back
   without coordinates is refused and read a row at a time instead — with a row budget of its
   own, because that path is slow enough that the number of rows has to come down with it.

**And a cancelled call is now told apart from an empty selection.** They arrived at the reader
as the same sentence, and they are different things to do about: press again, against go and
select something. NVDA giving up on Excel is not Excel having nothing to say.

#### Zoom on a chart is not magnification

The third hardware run, and the question it raised is the interesting one: *"do we need to make
zoom able to distinguish an image versus provided data so it knows, or somehow can we use the
data to our advantage?"*

What was reported: zooming a line chart narrowed the date range as expected, and the dates at
the bottom of the panel stopped corresponding to what was on it. They could not have done
anything else. Zoom was a raster operation — the drawing was sampled through a scale and an
origin — so the writing was magnified along with everything else, which turns braille cells
into smears of enlarged dots, and the two dates went on naming the whole range while the reader
was feeling a tenth of it. The one piece of writing that says where you are was the piece that
lied.

**Magnification is the only thing that can be done to a photograph.** The dots are all there
is; there is nothing else to consult. A chart is not like that. It was composed for this panel
out of numbers that are still to hand, so a narrower window can be *composed again*: the same
panel, fewer periods, drawn at full resolution, with their own two dates written under them and
the value range refitted to what is actually showing.

**The distinction is asked of the figure, not of its type.** `Drawing` gained a `redraw`
alongside the `describeAt` it already had, and both are answers to the same shape of question:
a drawing that knows what it is made of can do something a bag of dots cannot. Nothing checks
what kind of figure it is, nothing branches on a chart type, and phase 5's imported images will
simply not supply one and keep the sampling. A chart supplies both; a scanned picture supplies
neither.

A captured picture supplies one too, and the plan was wrong to say it would not. See phase 5:
its source is the pixels rather than the pins, so a window is reduced from its own pixels at
full detail. What has no `redraw` is a drawing whose dots really are all there is.

The mechanism turned out to need almost nothing new, which is the sign the earlier design was
holding up. The mode already tracks the zoom and origin as a window over the source in dots,
already clamps it, already centres a zoom on what is under the reader's hand. A chart's source
is exactly panel sized, so that window *as a fraction of the whole* is the part of the data
being looked at — and handing over a fraction is what keeps the mode from learning that the
data is periods, or prices, or anything at all. All that changed is what happens at the end:
where a picture is sampled, a chart is asked to compose itself for that fraction and blitted
one dot to one pin.

Four consequences worth writing down:

1. **A press answers about the window, not about the whole.** What is on the panel *is* the
   drawing, so a pin is a dot of it, and putting the zoom and origin through the press as well
   would apply the window twice and name a period the reader is not touching. Wrong in the
   worst way available: plausibly.
2. **There is no up and down.** A redrawn chart fits its value axis to whatever it is showing,
   so there is never anything above or below the panel. Vertical panning says so rather than
   moving nothing without explaining itself, and the position readout leaves the axis out
   rather than reporting a meaningless "top edge".
3. **The zoom stops where the data does.** A chart of twenty periods magnified thirty-two times
   is a chart of half a period. Refused at three points rather than clamped silently, so the
   zoom the reader is told matches the zoom they are feeling.
4. **The window carries the size it is for.** The rectangle can change under a figure that is
   already up — giving the whole band to the drawing is a command, and one a reader uses
   mid-read — and a window composed for the old rectangle would be blitted into the new one
   with a row cut off, which is the row the dates are written on.

The visible range is now in the chart's own name as well as on the panel, so entering a chart
or changing the zoom says it: "line chart, Close solid, 8 points, 14 Jun to 21 Jun, 4 times".
The braille shows it and the speech confirms it, which is the right division: the panel is what
the hand is reading and the speech is what tells the hand that something changed.

Bar charts get the same treatment, and it is worth more there than it looks. Forty bars are two
pins each with no room for a label; zoom in and it is ten bars of nine pins each, every one of
them named.

Left for hardware, and this is the whole point of building four types rather than one. A single
series reads, which settles the frame, the corners and the path drawing; every question below is
about what the type adds on top of that:

1. Whether a four series line chart is readable at all at this pitch, or whether the useful
   number is two or three. The textures are the variable to change if it is not. One series is
   read; two is the next thing to try, since it is where a texture first has to be told from
   another texture rather than from blank panel.
2. Whether a candlestick's hollow body reads as hollow at three pins wide, and whether the
   body is in fact easier to find than a stem with two ticks on it.
3. Whether twenty-four periods across the panel is too many to feel one at a time, and whether
   a week or a fortnight is the useful span.
4. Whether reading the height back as a value — pressing a blank part of a line chart to be
   told what price that row stands for — is as useful in the hand as it looks on paper. It and
   the redrawn zoom are the two things here that a printed tactile chart cannot do at all.

### Phase 5, images from the screen

Status: **5a built and unit tested, awaiting a hardware run.** `imageSource.py` finds the
object and copies its rectangle, `imagePins.py` is the arithmetic, `image.py` composes a
`Drawing`, and two commands drive it. Getting a picture off the screen and onto the pins, by
brightness and by edges. Everything cleverer than that — finding the subject, dropping the background, saying
what the picture is of — comes later and comes on top of this, not instead of it.

**The picture is the one NVDA just found.** The reader is arrowing a web page, NVDA says
"graphic", and that is the moment the command has to work in. So the source is the navigator
object, which is what every other capture in NVDA uses and which, in browse mode, is the
graphic itself: arrowing sets the review position, that clears the navigator object, and the
next read of it comes back from the review position as the object under the caret. Nothing has
to be aimed, and nothing new has to be taught to the reader.

Not restricted to things NVDA calls a graphic. A diagram drawn in a canvas, a map, a chart
somebody published as a picture, and a table of an image are all worth a hand, and half of them
report a role that says nothing. What is refused is what cannot work: an object with no
location, one clipped to nothing, and one too small on screen to hold a picture. Each refusal
says which, because "that did not work" leaves a reader guessing at whether to move and try
again.

**Captured larger than the panel, deliberately.** A screen grab straight down to 96 by 40 would
make the panel the whole of what was ever known about the picture, and zooming could then only
make dots bigger. So the object's own screen rectangle is captured near its natural size, held,
and reduced *for each view*. That is what lets an imported picture supply the `redraw` the plan
said it would not: the source is not the pins, it is the pixels, and a reader zooming to a
quarter of the picture gets that quarter reduced from its own pixels at full detail rather than
four pins where one was. The limit is the capture, not the panel — an image 800 pixels wide
holds about eight useful magnifications, and past that it is honestly refused.

#### Reduce first, then detect

The order is the whole of the image processing design, and it is not the obvious one.

Detecting edges at capture resolution and then shrinking the edge map is what suggests itself,
and it is wrong here: a 400 pixel wide picture reduced to 96 pins is four pixels to a pin, and
an edge map reduced by "was there an edge in these four" turns every textured region into solid
raised pins. Reduced by averaging instead, a one pixel line becomes a quarter-grey that then
fails any threshold. Either way the fine edges that were detected so carefully are destroyed by
the reduction that follows.

So: greyscale, **reduce by area average to exactly the size being drawn**, and detect on that.
Area averaging is a low-pass filter, which is precisely what belongs in front of a derivative,
and this is the textbook order rather than a compromise. The edges that come out are one pin
thick because the image they were found in was one pin per pixel. And it makes zoom uniform —
a window is a crop of the pixels, reduced to the panel, detected — so nothing in the pipeline
has to know whether it is drawing the whole picture or a tenth of it.

The cost is that detail below a pin is gone before the detector sees it. That is not a loss: it
was never going to survive onto a pin. It is the reason zoom redraws from pixels.

#### A coverage ceiling, not a quota — and it was a quota first

The question "which pixels are edge enough to raise a pin" has no answer in the pixels, and the
first answer here was to stop asking it. **A panel more than about a fifth raised stops being a
picture and becomes a texture**, and a panel with a dozen pins up says nothing at all — so no
cut was chosen as a magnitude. The gradients were sorted and the cut fell wherever it left a
sixth of the panel raised.

That reasoning is sound for a photograph. Nobody can know a photograph's edge density in
advance, a fixed threshold raises everything on a high contrast logo and nothing on a soft
picture, and a reader cannot tell those two failures apart by touch. It is wrong for everything
else, and wrong in a way that is specific and worth stating plainly: **a quota has to be met.**
A picture without that much in it gets the shortfall made up out of whatever came next.

A reader loaded a hexagon and reported what the panel felt like. Every part of the report was
true, and none of it was in the picture:

1. **Two vertical lines.** The seam where the picture met the letterbox the add-on had drawn
   around it. On a drawing whose faint background texture runs out to its own boundary, that
   seam measures about one per cent of a real edge — and the quota, having run out of real
   edges, promoted it to two full height lines. Reproduced from the file with no capture
   overshoot at all: columns 28 and 67, raised on 33 and 35 of the 40 rows.
2. **An outline four dots wide.** The hexagon has about 110 pins of perimeter. The quota wanted
   614. The difference went into widening the only shape in the picture until the corners
   rounded off and the sides met at the vertices — and counting the sides is the whole of what a
   hexagon has to say.
3. **Stipple appearing on zoom.** A window in the middle of the hexagon contains nothing: its
   strongest gradient is 68 against 1282 for ink. The quota filled the panel with the
   background hatch, resolved by the reduction as the window narrowed.

One cause, three shapes, and each drawn as confidently as something real.

So: the cut now comes from the data — Otsu over the gradient magnitudes for outlines, class
membership for brightness — and **the coverage may only ever remove pins.** What is lost is the
guarantee of a constant density. What is gained is that the density means something, because it
is now how much was found rather than how much was demanded.

**Polarity still follows from the split.** Whichever side is the minority is the subject and
gets raised, so a black logo on white and a white logo on black both come out as the logo,
without asking anybody. An image genuinely mostly subject reads inverted, which is what the
manual invert is for. What changed is that membership decides what is raised: an earlier version
counted the subject and then raised that many of the darkest cells, the same answer whenever the
split is clean and a different one when it is not — a window of faint background has a subject
side too, so the count came back positive and the darkest of the backdrop went up to meet it.

#### Two rectangles, so that no margin is ever detected

The fix for the vertical lines is structural rather than a better threshold, and it is worth
separating from the coverage change because it stands on its own.

Fitting a square picture onto a panel two and a half times as wide means a letterbox. That was
done by growing the *source* box outwards past the edge of the capture and filling the overhang
with the picture's own border tone. The arithmetic was right and the margin still ended up
somewhere it did not belong: **in front of the detector.** Matching the border tone makes the
seam faint; it does not make it zero, because a textured edge is not its own average.

So fitting is now two rectangles. The source rectangle always lies inside the capture. The
destination rectangle says where its reduction sits on the panel. The detector is shown the
content and nothing else, the margins are blitted blank afterwards, and **the coverage limit is
a fraction of the content area rather than of the panel** — sixteen per cent of 40 by 40 is 256
pins, not the 614 that a sixth of the whole panel allowed.

The alternative considered and rejected was trimming a uniform border off the capture. It does
not survive contact with the general case: an object may legitimately reach its own boundary,
nothing can tell that apart from a capture that overshot, and a tolerant trim would cut the
vertices off this very hexagon. If overshoot is ever demonstrated from a saved capture it gets
its own fix and its own evidence.

#### A window is judged against the picture, not against itself

Both detectors return a best answer for whatever they are handed, and blank paper has a best
answer too. That is the third symptom and neither of the changes above reaches it, because a
zoomed window has no margin to exclude and its own local contrast is real.

So each style is asked the question it can answer — how strong is the strongest edge here, how
far apart are the two tones here — and the answer is compared against the same measurement taken
once over the whole capture. Below a sixth of it, the window is refused in words. The gap that
has to be straddled is wide: on the hexagon, ink measures 1282 and the hatch 68.

The two mistakes do not cost the same. Refusing a window that had something in it costs a reader
one keypress and a sentence saying why. Drawing one that had nothing costs them a panel they
will read as the picture.

#### A blank window is drawn and announced, and refusing it was worse

The first version of that refusal refused the window too, and hardware found the flaw within
minutes. **Zoom keeps what is under the hand under the hand**, which is right in general. The
middle of an outlined shape is empty. So every zoom from fit landed on nothing and was refused,
and since a move that cannot be drawn is not a move, the zoom key did nothing at all — no zoom,
therefore no pan, therefore no way to reach the rim, which is the entire thing worth magnifying.
Measured on the hexagon: fit draws, and zoom steps 1 through 5 were all refused, while the same
window moved hard left holds 107 pins of real outline. Each refusal was individually correct and
collectively useless, and the reader reported it as zoom being broken.

So the rule is split by what the failure can mean:

- **The whole picture holds nothing:** refused. The reader pointed at something that is not a
  picture, and a blank panel would leave them unable to tell that from a display that had
  stopped working.
- **A window holds nothing:** drawn, blank, and announced. By then the whole picture has already
  drawn, so the display has demonstrated that it works, and the blank is a fact about the
  picture — the middle of a hexagon really is empty.

The rule it looks like it breaks is the one about blank panels, and it does not: that rule is
that a blank panel must never happen **silently**. So `Drawing` carries a note, the zoom
announcement appends it, and panning appends it too — because a reader panning across an empty
middle needs to hear why they are feeling nothing on every step, not only on the one that took
them there. Zoom in on the hexagon and the panel is blank and says "only background here"; pan
once and a side arrives with 34 pins; pan again and the vertex arrives with 116.

#### A second review, and the shape of what it found

Nine findings, and two things worth naming about the set of them rather than about any one.

The first is that most of these fail by **permitting** rather than by refusing, which is why
none of them announced itself. The reference a window is judged against collapsed on the short
axis for any wide picture, so the background check measured every window against nought and
passed it — on a toolbar, a menu bar, a tab strip, most of what a reader points at. The
strongest-gradient reference let one four-pixel speck of black set the yardstick for a whole
photograph, so genuine features softer than it were called background. Nothing looked wrong in
either case; the checks simply never said no.

The second is that **a refusal is a move that does not happen**, and that turns a local
judgement into a navigation barrier. Three separate paths refused a window that held nothing —
flat tone, below the strength floor, too sparse after detection — and which one noticed first
decided whether the reader could move at all. They now answer the same way, because the rule is
about who is asking rather than about which test fired: a whole picture with nothing in it is
refused, a window with nothing in it is drawn blank and announced.

The individual repairs:

- **Two rectangles, transactional.** `setTextLines` claimed the new shape, recorded it and
  returned success without looking at whether the figure had drawn — so a figure that refused
  the new size left the old overlay on the display while the mode reported a full panel. The
  same disagreement the zoom rollback was written to end, reached through a different door.
  A rebuild that cannot recompose now falls back to fit and, failing that, gives the drawing up
  rather than leaving an overlay composed for somewhere else.
- **The cached pixels and the drawing are committed together.** Capturing a new picture
  assigned the pixels before composition had succeeded, so a failed draw left the pixels as B
  while the display held A — and the style key, which checks that the drawing on the display is
  the last picture drawing, would then compose B from the cache and put it over A.
- **A style change keeps the reader's place.** It went through `enter`, which resets the zoom
  and both origins, so switching from outlines to brightness to compare them moved the reader
  off the part they were comparing. `replaceSource` swaps the figure and recomposes the current
  window, and refuses without moving anything if the new style will not draw it.
- **A ceiling may not take a contour to pieces.** Ranking every chosen cell and keeping the
  strongest fits a budget and undoes the hysteresis that just ran: one connected edge map came
  back as hundreds of fragments, and a hand following a line through that finds it stop and
  start again. Density is now bought by asking for fewer edges — raise the threshold, regrow
  the contours — and failing that by dropping whole contours strongest first. A contour is a
  thing; half of one is a lie.
- **A picture too dense to draw says so.** When neither of those fits, what is drawn is an even
  scattering, which is an honest account of a texture and a dishonest one of a drawing. So it
  is announced: too detailed to draw whole, magnify to read it.
- **Suppression answers once.** A cell that merely equalled its neighbour survived, so a single
  clean light-to-dark step came back as two columns. That is not the doubled contour of a thick
  stroke — it is one edge drawn twice.
- **Clipping cuts rather than slides.** `place` moved the near edge and then measured the width
  from there, so a box starting outside the picture came back covering more of it than the box
  did, and a box entirely past the end came back as a one-pixel strip.
- **The virtual screen is a bounding box, not a shape.** Two monitors that are not aligned leave
  ground inside the box that belongs to neither. The real monitors are enumerated now, and a
  rectangle keeps its full extent only when every pixel of it lands on one.

#### One step past a pixel a pin

The zoom ladder stopped exactly where a pin stood on a source pixel, on the grounds that past
that there is no more detail in the file and the reader would be feeling the magnification.
Correct about detail and wrong about hands: a pin is a small thing to read a shape with, and a
reader magnifying a toolbar that has run out of pixels is asking for the shape to get bigger,
not for new detail to appear. One step past gives them that and costs nothing true — the
picture is no longer gaining information, but it was not losing any either. Two steps would be
feeling the reduction rather than the picture, so it is one.

The refusal that follows says which of the three reasons it is: the ladder has run out, the
drawing is already as large as the pins can show, or there is no more detail to show. For a
picture it adds the capture size, because that is the whole answer to the commonest confusion
about one — a toolbar with six icons plainly visible on screen that will not magnify is a
toolbar 160 pixels wide across 96 pins, already under two pixels to a pin.

#### Thinning, and the limit of thinning

Non-maximum suppression reduces a broad Sobel response to the crest of its ridge, and hysteresis
grows the strong parts of a contour back along themselves through the weak ones. The second is
not optional with the first: a contour does not have one strength along its length, a single
threshold breaks it exactly where it fades, and **a break is the one artefact a hand cannot work
around.** A reader following a line to a corner and finding it stop has been told something
false about the shape and has nowhere to pick it up again. A thick line can be followed; a
broken one cannot.

What thinning does not do is merge two edges into one. A black stroke on a light page has two
genuine transitions, light to dark going in and dark to light coming out, with opposite
gradient. A reader feeling an outlined shape is feeling the outline of the outline. Getting a
single centreline would mean segmentation and skeletonisation, and it is probably not worth it,
because the style that draws the ink itself already exists: **brightness renders this hexagon as
one connected stroke of 195 pins**, and it is the better answer for dark-stroke-on-light
drawings.

That is a fact about this kind of drawing rather than about line art generally. Outlines remain
the right style for filled shapes, photographs, multitone diagrams, very light lines, and
anything whose internal boundaries matter. Neither can be chosen in advance, which is why the
style is a key that cycles rather than a question the reader has no way to answer.

#### What a review found, and the three shapes it was the same fault in

Each of these put a confident, wrong fact in front of a reader who had no way to check it, and
all three came of the same habit: a piece of the pipeline doing something reasonable in
isolation while quietly contradicting the piece before it.

**A picture was stretched to the panel.** `fitBox` grows the source box on the short axis until
it has the panel's proportions, which is right, and `Picture.reduce` clipped that growth away
again before reducing, which is also defensible on its own — so the whole picture was resized to
the whole panel. A square feature came out 33 pins wide by 14 high. A circle was an ellipse,
every geometric relationship in a diagram was a lie, and nothing in the drawing said so. Worth
noting how it survived a test suite: there *was* a test of the proportions, and it tested
`fitBox`, which was giving the right answer to a question the next stage declined to be asked.
The test now measures the bounding box of what was actually raised.

The margin is filled with the picture's own border tone rather than with a constant. Padding
with white puts a hard step all the way round a dark photograph, and the edge detector, asked
for the strongest sixth of the panel, would spend a good part of it drawing a frame that is not
in the picture.

**A refused window changed the reported state.** When a figure would not compose a window,
`_reframe` fell back to the whole figure and recorded the refused window as though it had been
drawn. The zoom said 1, the origin said 24 by 8, the position said "50 across, 47 down" — and
the whole picture was under the reader's hands. Every number agreed with every other number and
all of them were wrong. So a move that cannot be drawn is now not a move: `_reframe` answers
False, `render` writes nothing, and `zoomBy` and `panBy` put the state back and say no.

**A press reported where it was on the panel, not where it was in the picture.** So after
zooming into the right-hand quarter, the left edge said nought across — wrong exactly when the
reader most needs it, since zooming in is what somebody does when they have lost the place. The
pin now goes back through the box it was drawn from. A press on the margin says so rather than
reporting the nearest edge as though it were the picture.

Two smaller ones of the same kind. The style key was tied to having pixels in hand rather than
to the picture being what the display is showing, so leaving the drawing and pressing it put the
old picture back over a chart. And a style change asked for the default braille line rather than
the one in force, so a reader who had given the whole panel to a picture got the line back and a
smaller drawing, from a command that said it was changing the style.

#### Refusals a hand can act on

A blank panel is the one outcome that must never happen silently: a reader running a hand over
nothing cannot tell "captured nothing", "the picture is blank", and "the display is broken"
apart. So a result with almost no pins raised, or almost all of them, is refused with what it
was — not shown.

**An object's location is where it would be, not where it can be seen.** A graphic scrolled off
the page keeps an ordinary rectangle whose coordinates are simply past the edge of the screen,
and copying it succeeds: it comes back as whatever the graphics card has there. So the rectangle
is cut down to the part of it on a monitor and refused if none of it is, and an object that
reports itself off screen is refused before that — the two catch different things, since a
control scrolled out of a pane keeps a location still over the window it is in. A partly visible
object is clipped to what shows rather than refused, and the minimum size is measured after the
clip so that a large object with a sliver showing is refused as what it is.

Occlusion is the limit none of that reaches. A window over the thing being captured is copied
instead of it, because a screen grab is a grab of the screen and there is nothing else to ask.

#### Pure Python, and why it is worth it

No numpy, no Pillow.

**numpy imports fine in the NVDA Python console and is not there for anybody else**, which is
the trap worth writing down rather than merely avoiding. It is in the build environment as an
optional dependency of comtypes, so it is importable in any NVDA run from source — and
`source/setup.py` lists it under `excludes` with the comment "numpy is an optional dependency
of comtypes but we don't require it", so py2exe leaves it out of what is shipped. An add-on
that imported it would be tested by its author in a console that has it and would fail on
every installed NVDA, which is the worst shape a dependency can take: it works everywhere it
is checked and nowhere it is used. Pillow is not present on either path.

The arithmetic does not need either of them. It is one reduction over the captured pixels —
taken as slice sums, so the per-pixel work happens below Python — and a Sobel over a few
thousand cells, which is milliseconds on a capture of a third of a megapixel. What it buys
besides safety is that the module stays testable on a list of numbers with no NVDA in the room.

#### What this is not, and what comes after

This gets a picture onto the pins. It does not decide what in the picture matters. A photograph
of a person in front of a bookcase will come out as a person and a bookcase, and the bookcase
has more edges. That is the known and stated limit, and the next things on top of it are:

1. **Finding the subject** and drawing it alone — the background is the thing that makes a
   photograph unreadable, far more than the resolution is.
2. **Object identification**, which turns a picture into something that can also be *described*
   under the finger, the way a chart's bars answer for themselves through `describeAt`.
3. **Simplification** — straightening, closing and dropping strokes so that what is left is
   what a tactile graphics standard would have had a person draw by hand.

Each is a separate piece of machinery and each needs this one working first. Expect brightness
and edges to be good for line art, logos, diagrams and maps, adequate for high contrast
photographs, and poor for everything else — and say so rather than tuning forever.

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
