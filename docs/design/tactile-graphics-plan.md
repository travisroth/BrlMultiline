# Tactile graphics over HID braille

Feasibility findings and a plan for drawing on the Humanware Monarch through the
connection the add-on already has: NVDA's `hidBrailleStandard` driver, with the Monarch
in terminal mode.

Read [nvda-api-notes.md](nvda-api-notes.md) and [architecture.md](architecture.md) first.
This document adds findings from NVDA master (`C:\code\nvda`, reporting 2026.3.0dev) and
from the published HID Braille Display usage page. Line numbers are from that tree and
will drift.

## Verdict

Three questions were asked. The answers are different for each.

**Can we draw at all?** Yes. Not through a graphics mode, because no such thing exists in
the HID standard, but because a braille cell value *is* a raw eight dot bitmap and nothing
between us and the pins interprets it as a character. Writing arbitrary cell bytes to an
eight by thirty-two cell array is drawing on a sixty-four by thirty-two dot canvas. This
needs no new driver, no new protocol, and no cooperation from Humanware.

**Can we mix braille characters and drawing?** Yes, and it is nearly free. There is one
cell array and one write path. A cell holding a translated character and a cell holding a
hand-set dot pattern are the same kind of thing. Mixing is a matter of deciding which
rectangle of the display is rendered which way, which is exactly what this add-on's panels
and segments already do. A graphics panel is a new panel type, not a new subsystem.

**Do we need to draw braille the way the DotPad package does?** No. That relationship is
inverted, and the inversion is the useful insight. The DotPad has a *separate physical
graphics area* with no text mode of its own, so NVDA renders braille characters down into
a pixel buffer using `tactile.braille.drawBrailleCells`. The Monarch over terminal mode
HID has the opposite shape: the transport is already cells, so we render pixels *up* into
cells. We reuse NVDA's `TactileGraphicsBuffer` abstraction but supply our own flush, and
we must **not** copy the DotPad's bit packing. See "Bit order" below; it is wrong for us.

The cost is resolution and geometry, not capability. Through cell mode we reach 2,048 of
the Monarch's 3,840 pins, and they sit on a lattice with a one pin horizontal gap every
two columns. Reaching the other 47 percent is not possible over the standard. It needs
either an undocumented vendor interface or a change to the HID specification. Both are
tracked below as long game, and neither blocks the useful work.

## What the HID standard actually says

The Braille Display usage page is `0x41`, created by
[HUTRR78](https://usb.org/sites/default/files/hutrr78_-_creation_of_a_braille_display_usage_page_0.pdf)
and carried unchanged into the HID Usage Tables through version 1.6. Its complete output
side is:

1. `0x01` Braille Display, an application collection.
2. `0x02` Braille Row, a named array.
3. `0x03` 8 Dot Braille Cell, a dynamic value.
4. `0x04` 6 Dot Braille Cell, a dynamic value.
5. `0x05` Number of Braille Cells, a dynamic value.
6. `0x06` Screen Reader Control, a named array.
7. `0x07` Screen Reader Identifier, a dynamic value.

Everything else on the page is input: router sets `0xFA` to `0xFC`, router keys `0x100`
and `0x101`, and the button and keyboard block `0x200` to `0x21E`. NVDA enumerates exactly
this set in `hidBrailleStandard.py:26`, class `BraillePageUsageID`.

There is **no** usage for a bitmap, a pixel, a raw pin, a graphics area, or a mode switch.
The ranges `0x08` to `0xF9`, `0xFD` to `0xFF` and `0x102` to `0x1FF` are reserved and
empty, so the page has room to grow, but nothing occupies it today and no review request
after HUTRR78 touches this page. Any claim that HID braille has a graphics mode is, as far
as the published specification goes, not correct. What is correct is that the cell array is
expressive enough to be used as one.

### Why the cell array is a canvas

NVDA's HID driver does no interpretation on the way out. From `hidBrailleStandard.py`,
method `display`:

```python
padded_cells = cells + [0] * (self._maxNumberOfCells - len(cells))
cellBytes = b"".join(intToByte(cell) for cell in padded_cells)
...
report.setUsageValueArray(HID_USAGE_PAGE_BRAILLE, valueCap.LinkCollection,
                          valueCap.u1.NotRange.Usage, rowCellBytes)
```

Bytes in, bytes out. There is no table lookup, no character validation, and no restriction
to patterns that happen to be letters in some braille code. Any of the 256 values reaches
the pins. Translation happens far above this, in `braille.regions.base.Region.update()`,
and a region that does not call the base implementation never touches liblouis at all.
That is the hook the whole plan hangs on.

## Monarch geometry, and the 47 percent we cannot reach

Numbers from Humanware and APH published material, to be confirmed against hardware:

1. The panel is approximately **96 by 40 equidistant pins**, 3,840 in total. Equidistant is
   the important word: this is a uniform grid, not clusters of six or eight.
2. When the device lays out braille it spends **three pin columns per cell**, two dot
   columns and one spacer, so 32 cells consume all 96 columns and 32 of those columns, the
   equivalent of 16 cells of width, carry nothing. Vertically, 10 lines of 4 pin rows fill
   all 40 rows with no spacer row between lines.
3. **Terminal mode reports 8 rows of 32**, which is the 256 cells NVDA is given and what
   `devices.py` already assumes throughout. Two of the ten lines are not offered to the
   terminal.

So the canvas available through cell mode is:

1. **64 by 32 dots addressable**, 2,048 pins, 53 percent of the panel.
2. The dot lattice is **contiguous vertically** and **gapped horizontally**. Reachable x
   positions are 0, 1, 3, 4, 6, 7, and so on: every third physical pin column is a spacer
   we cannot set.

What that means in practice. Vertical lines are solid. Horizontal lines come out dashed,
two pins on and one off. Diagonals are ragged in a regular, learnable way. Filled areas
read as vertical striping. Large simple shapes survive this well. Fine detail and text
rendered as pixels do not.

Whether the striping is even perceptible is the single most important unknown, and it is
cheap to settle. It is possible that the Monarch fills spacer pins itself when both
neighbouring dots are raised, exactly as it must when it draws its own image library
content. Phase 0 below is one test that answers it.

### Bit order, and why not to copy the DotPad buffer

`DpTactileGraphicsBuffer.setDot` in `brailleDisplayDrivers/dotPad/driver.py:56` packs a
pixel as:

```python
bit = (y % self.cellHeight) + ((x % self.cellWidth) * self.cellHeight)
```

That puts the left column into bits 0 to 3 and the right column into bits 4 to 7, a column
major nibble layout. **It is not braille dot numbering.** A cell packed that way and sent
to a HID `8 Dot Braille Cell` value will render as a scrambled pattern. Whether the
DotPad's own graphic area really wants column major nibbles, or whether this is an upstream
bug, is not ours to settle. We simply must not inherit it.

The correct mapping for HID cells is the inverse of `tactile/braille.py`, list
`_brailleDotCoords`, which is authoritative in NVDA core. Dot 1 is bit 0 at cell position
x 0, y 0. Dot 2 is bit 1 at x 0, y 1. Dot 3 is bit 2 at x 0, y 2. Dot 4 is bit 3 at x 1,
y 0. Dot 5 is bit 4 at x 1, y 1. Dot 6 is bit 5 at x 1, y 2. Dot 7 is bit 6 at x 0, y 3.
Dot 8 is bit 7 at x 1, y 3.

Derive the lookup from `_brailleDotCoords` at import time rather than transcribing it, so
that it cannot drift from core.

## How the DotPad driver informs this, and how it does not

Worth reading `brailleDisplayDrivers/dotPad/` in full once, because it is the only tactile
graphics implementation in NVDA core and it establishes the vocabulary.

What transfers:

1. `tactile.TactileGraphicsBuffer`, present since NVDA 2025.1, is a two line abstract
   class: `width`, `height`, and `setDot(x, y)`. That is the right seam and it is stable.
   Subclass it.
2. The idea of rendering text into the buffer with `tactile.braille.drawBrailleCells` is
   useful in reverse, for a caption placed *inside* a graphic at a pixel position rather
   than occupying whole cells. It costs 3 pixel columns per character and 4 rows, which on
   a 64 by 32 canvas means about 21 characters on a line. Rarely worth it. Keep captions in
   real cells.

What does not transfer:

1. The transport. DotPad is FTDI serial at 115200 with a sync framed, checksummed packet
   protocol in `defs.py`, a board information handshake advertising separate `text` and
   `graphic` display descriptors, and `REQ_DISPLAY_LINE` addressed to a row of one or the
   other. None of that exists over HID.
2. The destination setting. DotPad exposes a `brailleDestination` driver setting because it
   genuinely has two surfaces and only one can be active. We have one surface, so the
   choice is per rectangle rather than per device, which is strictly better, and is why
   mixing is free for us and impossible for them.
3. The packing, as above.

## Where this lands in the add-on

The add-on is already the right shape for this, which is the pleasant part.

`BrailleHandler` writes one flat row major cell array and knows nothing about graphics.
The add-on composes that array through views, panels and segments, and a segment's cells
come from the regions appended to it. So:

1. A **`GraphicsRegion`** overrides `Region.update()` and does not call `super()`. It sets
   `self.brailleCells` to the packed bitmap, sets `rawText` to a short description for
   speech and for the braille viewer, and leaves `brailleCursorPos` as `None`. Nothing
   above it can tell it apart from a translated region, because nothing above it looks.
2. A **`GraphicsPanel`** claims a rectangle in a view, alongside the existing
   `BraillePanel`, `GridPanel`, `RowsPanel` and `SinglePanel` in `panels.py`. It reserves
   its segment, so the flow never fills it.
3. The segment holding it runs with **`fillRows` set**, because `fillRows` already packs
   cells row major across the rectangle with no word wrapping, which is precisely bitmap
   scanline order. This is an existing behaviour being reused, not a new one.
4. Its **routing policy** must be a new one that reports a pixel coordinate rather than a
   text offset, so that a routing key press on a graphic can say "you touched here" instead
   of routing a caret into nothing.
5. The **cursor must be suppressed** in a graphics segment. NVDA blinks the cursor by
   or-ing dots 7 and 8 into a cell, which would put two stray pins in the middle of a
   drawing. Check what `container.py` does with `brailleCursorPos` before assuming the
   `None` above is sufficient.

Nothing here requires patching NVDA further than the add-on already patches it.

## What is worth drawing

Sixty-four by thirty-two dots, gapped, refreshing slowly, is not a screen. Treating it as
one, by screenshotting and dithering, produces noise. The honest framing is that this is a
*diagram* surface, and it should be fed from structure rather than from pixels.

Ranked by value against what this add-on already knows how to do:

1. **Charts from tabular data.** The Excel app module and the table machinery already have
   the numbers as numbers. A bar chart of a column, or a line of a series, drawn from
   values rather than from a rendered chart image, is legible at this resolution and is
   something no other tool on the Monarch does from live application data.
2. **Structure diagrams.** Boxes and connectors for a tree, an outline, or a focus
   hierarchy: the shape of a thing, with real braille labels in adjacent cells.
3. **Position and extent indicators.** A filled rectangle showing where the current window
   sits inside a document, or where a selection sits inside a table. Cheap, small, and
   genuinely useful next to text.
4. **Simple line art and shapes** from an application that has vector geometry to give.
5. **Screen capture, dithered.** Last, and mostly as a diagnostic. Worth building the path
   because it is how we test the pipeline, not because it is how anyone will read a screen.

## Getting the other 47 percent

Three routes, in increasing order of effort and decreasing order of certainty.

**Dump the Monarch's report descriptor.** Cheap and unambiguous. If Humanware exposes a
vendor defined top level collection or a second HID interface alongside the braille one, it
will be visible in the descriptor and we would know immediately what is on offer. Nothing
in NVDA's PR [#18248](https://github.com/nvaccess/nvda/pull/18248), which added Monarch
multiline routing and is the most recent close reading of this device by anyone upstream,
mentions graphics or any non braille usage, but that PR had no reason to look. Do this in
phase 0 regardless of the expected outcome. It costs one connected device and one script.

**Ask Humanware and APH.** The device demonstrably can do full resolution graphics from an
external source: the Wing It iOS app renders live finger drawing onto the panel over
Bluetooth, and the Monarch draws image library content and eBRF embedded graphics natively.
A graphics transport exists. It simply is not the terminal mode HID interface, and it is
not publicly documented. This is the only realistic route to 96 by 40 in the near term, and
it is a conversation rather than an engineering problem.

**Extend the usage page.** A review request adding graphics usages to page `0x41`, such as
a pin matrix collection, a row of raw pin bytes, and a resolution descriptor, would
standardise this for the Monarch, the DotPad, the Graphiti and whatever follows. The
reserved space is there. The constituency is NV Access, Humanware and APH, Dot Incorporated
and Orbit Research. This is a multi year effort and should not gate anything, but it is
worth raising with NV Access once we have a working cell mode implementation to argue
from, because a demonstration is a much better opening than a proposal.

## Plan

Phases are ordered so that each one either produces something usable or kills the phase
after it. Mark them here as they pass on hardware, in the style of
[virtual-display-plan.md](virtual-display-plan.md).

### Phase 0, hardware truth, before writing anything

Status: spike written, not yet run on hardware.

Four questions, all answered with a Monarch connected in terminal mode by
`spikes/tactileGraphicsSpike.py`, imported from the NVDA Python console. `spike.summary()`
prints which call answers which question, and `spike.hunt()` answers question 4 on its own
without touching the pins. Note that `spike.hold()` suppresses NVDA's braille output for the
duration, so that a pattern stays up long enough to feel; `spike.release()` gives it back.

1. **Does it render arbitrary patterns?** Write a full panel of `0xFF`, then a checkerboard
   of `0x55` and `0xAA`, then a walking single dot. Confirm by touch that what appears is
   what was sent, and that nothing is filtered or snapped to a character set.
2. **Are the spacer columns filled?** With the whole panel at `0xFF`, is the surface
   uniformly solid, or striped two on and one off? This decides whether horizontal lines
   are usable and therefore what the rendering layer must compensate for. Everything about
   drawing quality follows from this one answer.
3. **What is the real geometry?** Log `handler.display.numRows`, `numCols` and `numCells`.
   Confirm 8 by 32 and 256, and find out whether the two missing lines can be recovered by
   any setting on the device.
4. **What does the descriptor contain?** Dump every top level collection, usage page and
   value cap the Monarch advertises, not only the braille ones. Save the dump into this
   folder as evidence.

Also measure, while the device is there: full panel refresh time, and whether repeated
redraws at flow speed are acceptable mechanically and audibly. A tactile panel is a
mechanism, and a graphics panel that redraws on every caret move may be unpleasant to sit
next to even if it is fast enough.

Exit criterion: questions 1 and 3 answered yes and 8 by 32. If arbitrary patterns are
filtered, stop. Nothing below is possible over HID and the work becomes the Humanware
conversation instead.

### Phase 1, the buffer and the region

Status: not started.

1. `tactileBuffer.py`: a `CellTactileGraphicsBuffer(TactileGraphicsBuffer)` with the
   correct braille dot order derived from `tactile.braille._brailleDotCoords`, sized from a
   rectangle in cells, with `setDot`, `clear` and `getCells()` returning the flat row major
   array.
2. `GraphicsRegion`, as described above.
3. Unit tests: dot to bit mapping for all eight dots, bounds clipping, cell packing for a
   known small pattern, and a round trip through `getCells`.

No hardware needed. This phase is pure and fully testable, which is why it comes first, and
it does not start from nothing: `CellCanvas` in `spikes/tactileGraphicsSpike.py` is this
buffer prototyped, with the dot order already confirmed by touch in phase 0. Lift it,
rename it, and write the tests around it.

### Phase 2, the panel, and mixing

Status: not started.

1. `GraphicsPanel` in `panels.py`, reserved, `fillRows`, cursor suppressed.
2. A view that puts a graphic on some rows and ordinary flow on the rest, to prove mixing
   end to end.
3. A test command that draws a border, a diagonal and a filled block into a claimed panel.

Exit criterion: on hardware, a drawing and readable braille text visible at the same time,
with the text still scrolling and routing normally. This is the phase that answers the user
facing question, and it is worth stopping to actually read the result with fingers before
building anything on top.

### Phase 3, a drawing API

Status: not started.

Primitives on the buffer: `line`, `rect`, `fillRect`, `polyline`, `marker`, and
`text(x, y, cells)` wrapping `drawBrailleCells`. Bresenham is enough. There is no
antialiasing to be had with one bit pins.

If phase 0 showed striping, this is where compensation lives. A horizontal line drawn at
constant y is already as solid as the lattice allows, but the primitives should know the
lattice, so that a rectangle border chooses vertical sides landing on reachable columns and
a caller asking for a one pixel gap is told it is not available.

### Phase 4, first real content

Status: not started.

Pick exactly one of the sources listed under "What is worth drawing" and build it properly.
The recommendation is the bar chart from an Excel column range, because the data path
already exists in the Excel app module, the output is unambiguous to verify, and it
demonstrates something the Monarch cannot currently do from live application data.

Resist building three half sources. One that is good enough to use daily will teach more
about what the resolution can carry than three that are demonstrations.

### Phase 5, image import, and the honest limit

Status: not started.

A path from a bitmap or SVG to the buffer: threshold, edge detect, downsample to the
lattice. Useful for tactile image library content, for eBRF embedded graphics, and as the
diagnostic mentioned above. Expect it to be disappointing for photographs and adequate for
line art, and document that rather than tuning forever.

### Long game, not phases

1. Report the descriptor findings from phase 0 to NV Access, whatever they are. If there is
   a vendor collection, that is genuinely new information upstream.
2. Open the conversation with Humanware and APH about a documented full resolution path.
3. Once phases 1 to 4 are working, raise a graphics extension to usage page `0x41` with NV
   Access, with the implementation as the argument.

## Open questions

1. Does the Monarch fill spacer pins? Phase 0, question 2. Everything about drawing quality
   depends on it, and nothing else in this document should be trusted until it is answered.
2. Can the two unavailable lines be recovered in terminal mode, taking the canvas from 64
   by 32 to 64 by 40? A 25 percent gain for possibly a setting.
3. What is the mechanical cost of frequent full redraws, and does that force an explicit
   "draw now" model rather than a live updating panel?
4. Does `container.py` suppress the cursor for a region reporting `brailleCursorPos` of
   `None`, or does something above it place a cursor anyway?
5. Is the DotPad bit order an upstream bug? Worth checking against a DotPad if one is ever
   available, and reporting it if so.

## Sources

1. [HUTRR78, Creation of a Braille Display Usage Page](https://usb.org/sites/default/files/hutrr78_-_creation_of_a_braille_display_usage_page_0.pdf)
2. [Braille Display Page (0x41) usage table](https://www.usbzh.com/article/detail-989.html)
3. [HID Usage Tables version 1.6](https://www.usb.org/sites/default/files/hut1_6.pdf)
4. [NVDA PR 12523, HID braille standard support](https://github.com/nvaccess/nvda/pull/12523)
5. [NVDA PR 17007, native DotPad support](https://github.com/nvaccess/nvda/pull/17007)
6. [NVDA PR 18248, multiline braille routing on Monarch](https://github.com/nvaccess/nvda/pull/18248)
7. [Monarch technical specification](https://www.humanware.com/wp-content/uploads/Monarch-Technical-Specification-Document.pdf)
8. [Monarch product page, Humanware](https://www.humanware.com/microsite/monarch/)
9. [Monarch, American Printing House](https://www.aph.org/product/monarch/)
10. Local trees: `C:\code\nvda\source\brailleDisplayDrivers\hidBrailleStandard.py`,
    `C:\code\nvda\source\brailleDisplayDrivers\dotPad\`, `C:\code\nvda\source\tactile\`
