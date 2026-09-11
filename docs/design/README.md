# BrlMultiline design documentation

This folder is the durable memory for the BrlMultiline add-on. It exists so that
the project can be picked up again after a long gap, by the author or by an assisting
agent, without re-deriving decisions from the source.

Read the files in this order:

1. [port-plan.md](port-plan.md) — what is being built, in what order, and what is done so far.
   Start here.
2. [nvda-api-notes.md](nvda-api-notes.md) — how NVDA's braille subsystem works as of
   NVDA 2026.3, and specifically the things that are easy to get wrong. This is the
   reference that keeps the add-on honest against a moving upstream.
3. [architecture.md](architecture.md) — the design of the add-on itself: segments, the
   container, the compositor, and why they are shaped the way they are.
4. [legacy-inventory.md](legacy-inventory.md) — an audit of the 2023 `brailleMultiline`
   prototype this work supersedes, including the specific defects found in it. Useful
   when deciding whether an old idea is worth revisiting.
5. [virtual-display-plan.md](virtual-display-plan.md) — feasibility findings and the plan
   for driving several physical displays as one, so that a second display can serve as a
   segment. Phase 0 passed on hardware; the driver, its gestures and the add-on's
   arrangement of a composite display are code complete and awaiting a hardware run. Read it
   before touching braille display drivers, and keep its phase markers current rather than
   repeating them here.
6. [spatial-reading-plan.md](spatial-reading-plan.md) — the design for presenting a document
   across the rows as one flow rather than a line per segment, and the open questions it
   still has. Every milestone in it is built and unit tested, and milestones 0 to 3 and 5
   to 7 have been confirmed on hardware; read it before touching the flow subsystem.
7. [structured-presentation-plan.md](structured-presentation-plan.md) — the design for
   showing content that has a shape: trees with depth, tables with columns, and the
   recognition step that decides how a particular thing is presented rather than merely
   whether it flows. Trees and tables are built and on hardware, and tables are feature
   complete for now — recognised, laid out in columns, paged, arranged, saved, and applied
   again by themselves. Read it before extending `flowObjects.py` or `flowRender.py`.
8. [tactile-graphics-plan.md](tactile-graphics-plan.md) — feasibility findings and the plan
   for drawing on the Monarch through the HID braille connection the add-on already has.
   Covers what the HID standard does and does not offer, the Monarch's pin geometry, why the
   DotPad's approach is the inverse of the one we need, and the hardware runs that found an
   undocumented 480 byte output report matching the Monarch's pin count exactly. Phases 0 to 4
   are on hardware: the pin report, the drawing API in `pinBuffer.py`, the graphics mode in
   `graphics.py` and `graphicsMode.py`, bar charts from an Excel selection, and the glyph
   phase that draws a role or a state as a shape in an ordinary braille line. Of phase 4b's
   other chart types only a single line series has been read; phase 5, image import, is not
   started. Read it before writing any code that sets dots rather than characters.
9. [monarch-driver-plan.md](monarch-driver-plan.md) — the design of `brlMultilineMonarch`, a
   braille display driver that subclasses NVDA's HID braille driver and drives the Monarch
   through its pin report instead of its cell reports. Gives graphics, a choice of 8 or 10
   line pitch, touch at pin resolution, and Bluetooth that survives a dropout. Built, and
   confirmed on hardware: it opens the device, draws through report 0x21, and routes
   correctly at both pitches. Read it before touching that driver package.

## Project background

NVDA's braille support was designed around a single line display. Content is written to
one flat buffer, and the display shows a window onto it, following the focus. On a
multi line display such as the Humanware Monarch, that model wastes the extra rows: NVDA
2026.1 will flow one buffer's text across the rows, but it is still one thing at a time.

This add-on divides the display into independent segments. Each segment holds its own
regions, scrolls on its own, and can be pointed at a different object. The goal is to
present information spatially — a list with several items visible at once, a document
with the lines around the caret visible, a monitored object pinned in place while focus
moves elsewhere.

## Target hardware

Development targets two displays with deliberately different geometry, to keep the
design from over-fitting to either:

1. Humanware Monarch — 8 rows of 32 cells. Segments are groups of whole rows.
2. Freedom Scientific Focus 80 — 1 row of 80 cells. Segments are column slices of that
   single row, which is genuinely useful at 80 cells (two 40 cell working areas).

Note that the Focus 80 cannot be used to simulate the Monarch. See the "Simulating a
multi row display" section of [nvda-api-notes.md](nvda-api-notes.md) for why.
