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

**Segment that flash messages appear in.** Where NVDA's brief messages go — the time, a
setting you just changed, "selected". Enter -1, the default, to put them wherever the focus
is, which is where they appear on an undivided display. Choosing a segment is most useful
with two displays combined, where you can keep messages off the display you are reading.

**Reverse the panning keys on this display.** Swaps the effect of the panning keys, so the
key that normally scrolls back scrolls forward instead. This is stored per display, which
is the point of it: on a Focus 80 the left hand key is the comfortable one for moving
forward, while on a Monarch you may want the normal arrangement. NVDA itself has no such
setting.

With two displays combined, this applies to whichever display you are using: it belongs to
the keys rather than to what is on the display, so pressing the Monarch's panning key uses
the Monarch's setting even when the segment that moves is on the other display. Use the
"Segment settings for" box to set it for each. If you had reversed the keys for a combined
display in an earlier version, when it was one setting for the pair, that choice is carried
onto each display you have not already set it for yourself.

**Show the document lines around the caret in the other segments.** When you are in a
document, fills the segments around the focus segment with the lines above and below the
line the caret is on. The segment immediately above the focus segment shows the previous
line, the one above that shows the line before it, and so on. Segments holding a pinned
object are left alone. Segments run past the start or end of the document show as blank.

## Reading as a flow

This is the newest part of the add-on, and the part that most needs your report of how it
behaves. Treat it as experimental.

Ordinarily a braille display shows one thing: the line the cursor is in, or the object with
focus. On a display with several rows that leaves a heading sitting alone on the top row
with the rest of the display blank, and it makes a long paragraph pan a line at a time
through rows that could have held all of it.

Reading as a flow presents a document as one continuous piece instead. A heading is
followed by whatever comes after it in the page, a paragraph runs on across as many rows as
it needs, and panning moves the whole display's worth at a time — so panning back puts you
where panning forward brought you from, which reading a line at a time never quite managed.
Moving the cursor within what is already on the display moves nothing, so you keep your
place while you read; pressing a quick navigation key such as `h` for the next heading sets
you down at what it found, with the document running on from there.

Set it up under NVDA menu, Preferences, Settings, BrlMultiline flow.

**Read this display as a flowing document.** The switch. Off until you turn it on, so
installing the add-on changes nothing.

**Use it for.** Which kinds of content are read this way.

*Browse mode* — web pages, and documents NVDA reads like one. On by default once the flow
is on.

*Lists and menus* — a list box, a menu, a tree, the choices of a combo box you have opened:
the items around the one you are on, instead of that one item alone. Off by default, because
it is the newest part and it changes how dialogs read. Nothing in it ever moves your
selection: the display shows the items around you and the arrow keys still decide where you
are. A routing key on the item you are already on does what it always did; on any other item
it moves you there rather than activating it.

*Editable text* — editors and fields outside browse mode, including ordinary Notepad-style
documents. The lines around the caret use the same spatial flow as a page, while the caret
remains owned by the real edit control so typing, selection and routing continue through
NVDA. Off by default, separately from lists and menus.

Everything else goes on being presented by NVDA as it always has, in the same band, so
nothing disappears when you leave a page.

**Display the flow appears on.** Only shown when several displays are driven as one. A flow
must sit on one piece of hardware, so this says which. Left on the automatic setting it goes
to the display with the most rows, since rows are what a flow has to spend.

**Rows it uses.** How many rows of that display the flow takes, counted from the top. Leave
it at 0 for all of them. Giving it four rows of a Monarch leaves the other four to your
segment layout, so you can read a page as a flow and keep an eye on something else below it.

**Start reading afresh from what a quick navigation key found.** On by default. When you
press a quick navigation key that skips a section — a heading, a table, a landmark, a list —
the thing it found is put on the top row and the document runs on from there. Turn this off
if you would rather the display always kept the window you had and simply followed the
cursor.

These settings are part of the configuration profile you are editing. That is the useful
part: make a profile for the browser you want to read this way, turn the flow on in it, and
leave the normal configuration alone. The flow then comes and goes as you switch between
applications, with nothing to press.

Things worth knowing:

- The band is claimed for as long as the flow is turned on, and it takes those rows from
  your segment layout. Where there is nothing to read as a flow — a dialog, an application
  the flow does not know yet — the band presents the focus exactly as an undivided display
  would, and starts flowing again by itself when you reach a document.
- Panning moves the browse mode cursor to what is now at the top of the band, so speech and
  braille stay together and your arrow keys carry on from what is under your hands.
- A control you have entered inside a page still reads as part of that page. Its active row
  comes from the real edit control, so the caret and newly typed value stay live while the
  label and the content after it continue to come from the page.
- On a form, the page context before the field is put above it rather than left behind, and
  a field is separated from the next prompt by a blank row. The active row comes from the
  edit itself and follows its real caret as you type, while the surrounding rows continue
  to come from the page.
- A list, a menu or a combo box's choices can be read the same way, showing the items
  around the one you are on. Turn "Lists and menus" on to try it.
- An editor outside browse mode can be read as a flow too. Turn "Editable text" on to use
  this in applications such as Notepad; it is separate so enabling page flows does not
  unexpectedly change every editor.

## Using two displays at once

The add-on can drive several braille displays as one, stacked one above another, so that a
second display becomes segments of its own. NVDA goes on believing it has a single display,
so everything else about it works as usual, including the keys on both displays.

This is new and has not yet been used for long. Treat it as experimental.

Set it up under NVDA menu, Preferences, Settings, BrlMultiline displays. Choose the displays
you want and put them in order, top first. Then, in NVDA's own Braille settings, choose
"BrlMultiline: several displays as one" as the braille display.

A Monarch above a Focus 80 gives a display of 9 rows: the Monarch's 8, then the Focus as the
ninth. Each display is divided into segments by its own settings, the ones it has when you
use it on its own, so there is usually nothing further to set up. To change them, open the
BrlMultiline settings while the combined display is connected and use the "Segment settings
for" box to choose which display you are changing.

Things worth knowing:

- The displays do not have to be the same width. The combined display is as wide as the
  widest one, and the cells past a narrower display's edge are kept blank so nothing is
  written where you could not read it.
- One display per driver. Two displays of the same make cannot be told apart, so only one of
  each can be used.
- No segment spans two displays. Each display is divided by its own settings, within its own
  rows, so a line of braille never continues from the bottom of one display onto another.
- Each display's own panning keys pan that display: the segment following the focus if that
  display holds it, otherwise that display's first segment. So the Monarch's keys move what
  is on the Monarch while the Focus goes on following the focus.
- Flash messages follow the focus by default, as they do on one display. "Segment that flash
  messages appear in" moves them, which is worth setting if you want the second display left
  alone while you read. A message changes only the segment it is in: everything else stays as
  it was, and a display the message is not on is not written to at all.
- Each display is detected when it is opened, so a display that is switched off is simply
  left out and the others are used. It is looked for while you work, and joins the combined
  display when it appears — switch it on, or bring it back into range, and it takes its place
  in the order you chose within a few seconds.
- A display that goes away is dropped and the rest carry on. The combined display shrinks to
  what is left, so the focus moves onto a display you still have rather than onto rows that
  are no longer there. An object you had pinned to the display that went moves to a free
  segment if there is one; if the only segment left is the one following the focus, the pin is
  released rather than taking the focus's place.
- While the combined display is in use, the list under "BrlMultiline displays" says which of
  the chosen displays it is driving: in use, not connected, or not responding.
- NVDA's automatic braille display detection is off while the combined display is selected,
  because it works by choosing the display for you and that choice is already made.
- Settings that belong to one of the displays themselves, such as dot firmness or a Focus's
  wiz wheel action, appear in NVDA's Braille settings named after the display they belong to.
  Changing one changes that display's own setting, so it is still there when you use that
  display on its own.
- A display driver's own commands still work on their default keys, but do not appear in the
  Input Gestures dialog while the combined display is in use, so they cannot be rebound there.
  Only three drivers are affected, because only three describe their commands: Alva ("Toggles
  HID keyboard simulation"), HandyTech ("Toggle braille input") and Eurobraille ("Toggle HID
  keyboard simulation"). A HandyTech used as one of the combined displays keeps its braille
  input toggle on space with dots 1, 3 and 4; you just cannot move it. Nothing on a Focus or a
  Monarch is affected, since neither describes any command of its own.
- The add-on has to be enabled. With it disabled and the combined display still selected,
  NVDA writes across the full width of every row, and anything landing past a narrower
  display's edge is lost.
- The list of combined displays is not part of a configuration profile. It describes which
  pieces of hardware are wired together, so it is stored once and applies everywhere,
  whichever profile is active. A list stored under a profile by an earlier version of the
  add-on is moved across the first time this one runs.
- A changed list takes effect when the combined display is opened again. Saving it in the
  settings does that for you; changing it from the Python console does not, and the log says
  so if the two disagree.

## Commands

None of the add-on's commands have a default key. Assign the ones you want under NVDA
menu, Preferences, Input Gestures, in the BrlMultiline category. They are most
useful assigned to keys on the display itself.

**Opens the BrlMultiline settings.** Goes straight to the settings category.

**Reads the whole display as one flowing document.** Turns the flow on or off, and
remembers the answer in the profile in force, so it is the same switch as the one in the
settings. See "Reading as a flow" above.

**Reports what the flow on the display has cost so far.** Says how long the slowest piece of
reading took and how often the flow ran out of its allowance and showed you less than the
display could hold. If a page feels slow, or you feel the marker meaning "there is more I
have not read", press this and send the log: it is a great deal more use than "it felt
slow".

**Reports the BrlMultiline segment layout.** Says how many segments there are and
which one is following the focus. Useful for confirming a layout took effect.

**Scrolls segment N of the first / second / third display forward / back.** Pans a segment named by which display
it is on and how far down that display it sits. Prefer these: a segment's plain number
counts across the whole display and moves whenever the layout changes, so a key bound to
"segment 5" quietly starts panning something else. "The second display's first segment"
does not move. On one display, the first display is that display, so these keep working
when you unplug the second one.

**Scrolls segment N forward / back.** Pans one segment, whether or not it is the segment
following the focus. There is a pair of these for each segment. These count across the
whole display, so they can reach a segment the display relative commands cannot, at the
cost of moving when the layout does.

A segment that is not following the focus is panned within the content it already has. It
will not move to the next or previous line of a document, because doing so would move the
caret in something you are only reading, and drag the focus with it.

**Shows the navigator object in segment N of the first / second / third display.** Pins the current navigator
object to a segment named by display, for the same reason as the panning commands above.

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
