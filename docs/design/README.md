# BrlMultiline design documentation

This folder is the durable memory for the BrlMultiline add-on. It exists so that
the project can be picked up again after a long gap, by the author or by an assisting
agent, without re-deriving decisions from the source.

Read the files in this order:

1. [port-plan.md](port-plan.md) — what is being built, in what order, and what is done so far.
   Start here.
2. [nvda-api-notes.md](nvda-api-notes.md) — how NVDA's braille subsystem works as of
   NVDA 2026.1, and specifically the things that are easy to get wrong. This is the
   reference that keeps the add-on honest against a moving upstream.
3. [architecture.md](architecture.md) — the design of the add-on itself: segments, the
   container, the compositor, and why they are shaped the way they are.
4. [legacy-inventory.md](legacy-inventory.md) — an audit of the 2023 `brailleMultiline`
   prototype this work supersedes, including the specific defects found in it. Useful
   when deciding whether an old idea is worth revisiting.

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
