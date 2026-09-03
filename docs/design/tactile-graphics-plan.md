# Tactile graphics over HID braille

Feasibility findings and a plan for drawing on the Humanware Monarch through the connection
the add-on already has: NVDA's `hidBrailleStandard` driver, with the Monarch in terminal
mode.

Read [nvda-api-notes.md](nvda-api-notes.md) and [architecture.md](architecture.md) first.
This document adds findings from NVDA master (`C:\code\nvda`, reporting 2026.3.0dev), from
the published HID Braille Display usage page, and from a first hardware run of
`spikes/tactileGraphicsSpike.py` against a Monarch over Bluetooth.

## Verdict

Drawing is possible, mixing braille and drawing is nearly free, and the first hardware run
found a great deal more than the standard promises.

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

**But the Monarch appears to expose its whole pin grid on the same interface.** The first
hardware run of `hunt` reported an output report byte length of **481**. Eight rows of
thirty-two cells need 33. Four hundred and eighty payload bytes is 3,840 bits, which is
exactly the Monarch's pin count, and exactly 48 cell columns by 10 cell rows of the full 96
by 40 panel. Every output *value* cap is accounted for by the eight braille rows, and the
device declares exactly one output *button* cap — which is how a pin matrix is declared in
HID, one bit per pin. The input side corroborates it: value cap usage 0x401 has a logical
range of 0 to 3840.

NVDA cannot see any of this. `hidBrailleStandard._findCellValueCaps` keeps only value caps
whose link usage is `BRAILLE_ROW`, and NVDA reads button caps for input only. An output
button array is invisible to it and would never appear in an NVDA log.

So the 47 percent this plan originally wrote off is very likely already reachable, on the
interface NVDA has open, today. Confirming that is now the first thing phase 0 does.

**Mixing is free either way.** One write path, one array. A cell holding a translated
character and a cell holding a hand set dot pattern are the same kind of thing, so mixing is
a matter of deciding which rectangle renders which way — exactly what this add-on's panels
and segments already do. A graphics panel is a new panel type, not a new subsystem.

**The DotPad relationship is inverted, and that is the useful insight.** The DotPad has a
separate physical graphics area with no text mode, so NVDA renders characters *down* into a
pixel buffer with `tactile.braille.drawBrailleCells`. Here the transport is cells, so we
render pixels *up* into cells. Reuse `TactileGraphicsBuffer`, supply our own flush, and do
not copy the DotPad's bit packing. See "Bit order" below.

## What the hardware actually reported

From the first run, Monarch over Bluetooth, terminal mode:

1. Top level usage page 0x0041, usage 0x1. Geometry 8 rows of 32, 256 cells.
2. Report byte lengths: input 33, output **481**, feature 17.
3. Eight output value caps, report IDs 0x31 to 0x38, each 32 eight dot cells, each in its
   own `BRAILLE_ROW` collection. NVDA writes eight separate reports per refresh.
4. Counts include `NumberOutputButtonCaps: 1`. That single cap is the unexplained 480 bytes.
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

A boolean control sitting in the vendor collection that owns the display area has the shape
of a mode switch, and it may be what gates the pin report. That is a hypothesis, not a
finding, and it is not one to test by writing.

## Geometry

Published figures, now partly confirmed:

1. The panel is approximately **96 by 40 equidistant pins**, 3,840 in total. Equidistant is
   the important word: a uniform grid, not clusters.
2. Braille layout spends **three pin columns per cell**, two dot columns and one spacer, so
   32 cells consume all 96 columns and 32 of those columns carry nothing. Vertically, 10
   lines of 4 pin rows fill all 40 rows with no spacer row.
3. **Terminal mode gives 8 rows of 32**, confirmed. Two of the ten lines are not offered.

Which gives two very different canvases:

- **Through cells:** 64 by 32 dots, 2,048 pins, contiguous vertically and gapped every third
  column horizontally. Vertical lines solid, horizontal lines dashed, fills striped.
- **Through the pin report, if it is live:** 96 by 40, 3,840 pins, no gaps, and the two
  extra lines back. Everything reads cleanly.

### Bit order, and why not to copy the DotPad buffer

`DpTactileGraphicsBuffer.setDot` in `brailleDisplayDrivers/dotPad/driver.py:56` packs a
pixel as `bit = (y % cellHeight) + ((x % cellWidth) * cellHeight)`, putting the left column
in bits 0 to 3 and the right column in bits 4 to 7. That is a column major nibble layout,
**not braille dot numbering**, and a cell packed that way sent to a HID `8 Dot Braille Cell`
value renders scrambled. Whether the DotPad's graphic area genuinely wants that or whether
it is an upstream bug is not ours to settle; we must simply not inherit it.

The correct mapping is the inverse of `tactile/braille.py`, list `_brailleDotCoords`: dot 1
is bit 0 at x 0 y 0, dot 2 bit 1 at x 0 y 1, dot 3 bit 2 at x 0 y 2, dot 4 bit 3 at x 1 y 0,
dot 5 bit 4 at x 1 y 1, dot 6 bit 5 at x 1 y 2, dot 7 bit 6 at x 0 y 3, dot 8 bit 7 at x 1
y 3. Derive it from `_brailleDotCoords` at import time so it cannot drift from core.

The pin report's bit order is a separate and entirely unknown question. A bit index is not a
coordinate until the hardware says which way it runs, and `pinBit` exists to find out by
touch rather than by assumption.

## Where this lands in the add-on

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
4. Its **routing policy** needs to report a pixel coordinate rather than a text offset.
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

## Plan

### Phase 0, hardware truth

Status: first run done. The descriptor questions are answered; the pin questions are not.

Answered:

1. Geometry is 8 rows of 32, 256 cells.
2. The descriptor holds a 480 byte output report and a single output button cap, plus a
   vendor collection at 0x300 and feature reports 0x21 and 0x22.

Still open, in priority order:

1. **Is the pin report live?** `spike.pins()` to see the cap, then `hold()`, `pinClear()`,
   `pinFill()`. If the panel goes flat and then fully solid — including the columns between
   braille cells — the full grid is ours and most of the rest of this document's pessimism
   can be deleted.
2. **What is its bit order?** `pinBit(0)`, `pinBit(1)`, `pinBit(96)`, `pinBit(40)`,
   `pinWalk()`. Row major, column major, or 480 braille cells; feel where they land.
3. **Does the cell path render arbitrary patterns?** `dotOrder()`. Still worth knowing, and
   it confirms the braille bit numbering.
4. **Are the spacer columns filled on the cell path?** `spacers(1)`, `(2)`, `(3)`. Matters
   only if the pin report is inert.
5. **Mechanical cost.** `timing()`, plus your own ear.

Not to be done: writing feature 0x21. If the pin report is inert, the next step is asking
Humanware what 0x300, 0x301 and the pin report are for, not guessing at a mode flag.

### Phase 1, the buffer and the region

Status: not started.

`CellTactileGraphicsBuffer(TactileGraphicsBuffer)` with the braille dot order derived from
core, `GraphicsRegion`, and unit tests for the dot mapping, bounds clipping and packing. No
hardware needed. It does not start from nothing: `CellCanvas` in the spike is this buffer
prototyped, with the dot order confirmed by touch in phase 0.

If the pin report is live, add a `PinTactileGraphicsBuffer` beside it with the layout phase
0 established, and let the panel choose.

### Phase 2, the panel, and mixing

Status: not started.

`GraphicsPanel`, reserved, `fillRows`, cursor suppressed; a view mixing a graphic with
ordinary flow; a test command drawing a border, a diagonal and a filled block. Exit
criterion: on hardware, a drawing and readable braille visible at once, with the text still
scrolling and routing normally.

### Phase 3, a drawing API

Status: not started.

`line`, `rect`, `fillRect`, `polyline`, `marker`, and `text(x, y, cells)` wrapping
`drawBrailleCells`. Bresenham is enough. If the cell path is what we end up with and phase 0
showed striping, this is where lattice compensation lives.

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

1. Is the 480 byte output report the pin matrix, and is it live in terminal mode?
2. What is its bit order?
3. What is feature usage 0x301, currently reading 1, and does it gate anything?
4. What are 0x302 = 32 and 0x304 = 80? The 32 is cells per row; the 80 is unexplained.
5. What is input usage 0x401 with its logical range of 0 to 3840? A touched pin index would
   be extremely useful for interaction with a graphic.
6. Can the two unavailable braille lines be recovered in terminal mode?
7. What is the mechanical cost of frequent full redraws?
8. Is the DotPad bit order an upstream bug?

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
