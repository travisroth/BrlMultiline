# BrlMultiline

* Author: Travis Roth
* NVDA compatibility: 2026.3 and later. 2026.3 is not yet released, so for now this means
  an NVDA alpha snapshot. The add-on uses NVDA's `braille` package, which replaced the old
  single `braille.py` module after 2026.2 branched.
* Download: development version

BrlMultiline divides a braille display into several independent segments. Each
segment holds its own content and scrolls on its own.

NVDA was designed around single line displays: one thing is shown at a time, following
the focus. NVDA 2026.1 can flow that one thing across the rows of a multi line display,
but it is still one thing. This add-on lets different parts of the display show different
things at once.

It is useful in two situations:

1. On a multi line display such as the Humanware Monarch, where the extra rows can show
   the lines around the caret, or hold an object you want to keep an eye on.
2. On a large single line display such as a Focus 80, where 80 cells is enough to be
   worth splitting into two 40 cell working areas.

This is development software. It patches parts of NVDA's braille handling, and has not
yet been through a full round of hardware testing.

## Getting started

Everything is configured per display, under NVDA menu, Preferences, Settings, Braille
Multiline. Connect the display you want to configure first: the panel shows which display
its settings apply to, and a different display keeps its own.

By default the display is left as one segment, so installing the add-on changes nothing
until you configure it.

### Settings

**Number of segments.** How many pieces to divide the display into. The division is even,
with any remainder going to the earlier segments. On a display with more than one row,
segments are groups of whole rows: a Monarch with 8 rows divided into 4 gives four
segments of 2 rows each. On a single row display, segments are slices of that row: a
Focus 80 divided into 2 gives two 40 cell halves.

**Segment sizes.** Leave this blank to divide evenly. To make segments of different sizes,
type them separated by commas. The units are rows on a multi row display and cells on a
single row display, and the sizes must add up to the whole display. For example, `1, 5, 2`
on a Monarch gives a one row segment, then a five row segment, then a two row segment.

**Segment that follows the focus.** Which segment behaves the way NVDA normally does,
showing whatever has focus. Segments are numbered from 0. Enter -1 for the last segment,
which is the default.

**Reverse the panning keys on this display.** Swaps the effect of the panning keys, so the
key that normally scrolls back scrolls forward instead. This is stored per display, which
is the point of it: on a Focus 80 the left hand key is the comfortable one for moving
forward, while on a Monarch you may want the normal arrangement. NVDA itself has no such
setting.

**Show the document lines around the caret in the other segments.** When you are in a
document, fills the segments around the focus segment with the lines above and below the
line the caret is on. The segment immediately above the focus segment shows the previous
line, the one above that shows the line before it, and so on. Segments holding a pinned
object are left alone. Segments run past the start or end of the document show as blank.

## Commands

None of the add-on's commands have a default key. Assign the ones you want under NVDA
menu, Preferences, Input Gestures, in the BrlMultiline category. They are most
useful assigned to keys on the display itself.

**Opens the BrlMultiline settings.** Goes straight to the settings category.

**Reports the BrlMultiline segment layout.** Says how many segments there are and
which one is following the focus. Useful for confirming a layout took effect.

**Scrolls segment N forward / back.** Pans one segment, whether or not it is the segment
following the focus. There is a pair of these for each segment.

A segment that is not following the focus is panned within the content it already has. It
will not move to the next or previous line of a document, because doing so would move the
caret in something you are only reading, and drag the focus with it.

**Shows the navigator object in segment N.** Pins the current navigator object to that
segment, so it stays there while you move around elsewhere. There is one of these for each
segment. You cannot pin an object to the segment that follows the focus.

**Stops showing an object in segment N.** Unpins and clears that segment.

## Notes and limitations

The number of segments is capped at 8, which is the row count of a Monarch.

Cursor routing keys work per segment: pressing a routing key over a segment routes within
that segment's own content.

Only the segment that follows the focus shows a cursor, because NVDA tracks a single
cursor position.

A single row display cannot be made to simulate a multi row one. Telling NVDA that a Focus
80 has two rows of 40 causes NVDA to write only the first row to the display and blank the
rest, because it reshapes its output to the physical row count of the hardware. Splitting a
single row into column segments, as this add-on does, is a different and working
arrangement.

## Building from source

This add-on uses the NVDA add-on template, which is a `uv` project.

```bash
python -m uv sync
```

Then build, lint, and test with:

```bash
python -m uv run scons
```

```bash
python -m uv run ruff check .
```

```bash
python -m uv run python -m unittest discover -s tests
```

Design documentation, including notes on the parts of NVDA this add-on depends on, is in
`docs/design/`.

## License

GNU General Public License version 2.
