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

It has since grown two further parts, each with its own section below. A document can be read
as one continuous flow across the rows of a display instead of one line at a time, and a
display that can raise pins one at a time can be given a tactile drawing: a chart from a
spreadsheet selection, or a picture copied off the screen.

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

**Draw roles and states as shapes instead of words.** Off by default, and only offered on a
display that can raise pins one at a time, such as a Monarch. NVDA writes short words in
braille to say what a thing is: `btn` before a button's name, `cbo` before a combo box, and
three cells of braille patterns for a checked box. Those are abbreviations because a braille
line has nothing else to spend — and a pin display does. The cell one letter sits in is three
pin columns by four, and a shape drawn in those twelve pins can say "button" in one cell
instead of three.

Turned on, each of those words becomes a shape and the cells it was using go back to the
line, so more of the object's own name fits on a row. It applies to everything the display
shows — a menu bar in File Explorer as much as a web page — and it does not care which side
of the name NVDA puts the word on, which differs between browse mode and an ordinary window.
Nothing else changes: the shape sits where the word was, a routing key on it reaches the same
place, and turning the setting off brings the words straight back. A display that cannot draw
a shape shows the words anyway, which is also what happens for anything the vocabulary has no
shape for.

There are twenty-six shapes: buttons, toggle and radio buttons, edit and password fields,
combo boxes, lists, menu items, tables, graphics, progress bars, separators, submenu markers,
links, visited links, headings at the first three levels, and the checked, half checked and
pressed states with their absences. Between them they give sixty-five cells back. A heading
below level three keeps its word, since a shape you rarely meet is worse than a word you can
read.

It is off by default because the words are a notation you already know and the shapes are
this add-on's own. To learn them, assign a gesture to "Graphics: Show the glyph catalogue" in
NVDA's Input Gestures — it is unbound, being a tool for learning the shapes rather than one
used while reading. It draws every symbol with the braille it replaces beside it, so a hand
running along the panel reads the shape and then the word it stands for.

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

**In a multi-line edit you are typing in, read a paragraph at a time.** On by default. In an
edit box you are writing in, a paragraph is what you typed and a line is what the box's own
wrapping made of it, and while you are writing you are thinking about the first. Turn this
off and an edit box is cut up the same way a page is, by NVDA's own read by paragraph
setting in its braille settings.

The default comes from one rich editor, where asking what the line at the caret is gives a
single character for a moment after each Enter, and asking for the paragraph gives the line
you are on every time. That is the right answer there and may not be everywhere, which is
why it is a switch: if an editor reads oddly, try it off before reporting it.

**Scroll new content into view while you are at the end.** On by default. For something
pinned to a segment that is still being written — a chat, a log, a build — a new message
scrolls onto a full display and the oldest row moves off, the way a terminal follows a log.
Turn it off and what arrives waits below the display until you pan to it, which is what
everything else in a flow does: the window is yours and nothing moves it.

Two things are not settings, because they are what make this bearable. Nothing moves while
you have panned back into the history, and reaching the end again resumes it. And nothing
follows something that is merely longer than the display: the end has to have been reached
once, so a pinned page never walks itself through a document while your hands are on it.

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
  a field is separated from the next prompt by a blank row. A field you tab to is shown the
  way browse mode shows it, with its name, its role and its state, whether that name comes
  from a visible label or from the page's markup. Once you are typing in it — once browse
  mode has stood aside — the active row comes from the edit itself and follows its real
  caret, while the label above it and the content after it still come from the page.
- A multi line edit you are writing in — a comment box, a message, a post — is read as a
  document of its own, so the lines around the one you are on are on the display and the
  cursor follows what you type. Leaving it gives the page back.
- Quick navigation keys all move the display. A heading, a table, a landmark or a list puts
  what you jumped to on the top row with the document running on from it; everything else —
  a field, a button, a link — brings it onto the display without disturbing the rest of what
  is under your hands.
- A list, a menu or a combo box's choices can be read the same way, showing the items
  around the one you are on. Turn "Lists and menus" on to try it.
- A pinned list, menu or chat needs only one row of the display: one row is one item, and
  panning moves to the next, which makes a single row display a usable monitor for a
  conversation. A pinned document or table needs two, since what those are for is the shape
  they make across rows.
- An editor outside browse mode can be read as a flow too. Turn "Editable text" on to use
  this in applications such as Notepad; it is separate so enabling page flows does not
  unexpectedly change every editor.
- A wide table is dealt into pages of columns, and the commands for the next and previous
  page move across it, without moving the cursor. The page you turn to stays there while you
  read down the rows: with the first column repeated at the left of every page, the cell you
  are in is still on the display, so nothing pulls the page back. Move to a column the page
  does not show and the display comes back to your cursor, the way braille tethering brings
  it back to the focus.
- The columns of a table can be **arranged**. Three commands work on the column your cursor is
  in, for the first minute in a table nobody has arranged: hide it (or show it again), change
  what happens when its values do not fit — wrapped, cut keeping the start, cut keeping the end
  — and give the table back as it comes if an experiment goes wrong. A fourth opens a dialog
  for a table you come back to, where the columns are a checked list — checked means drawn —
  that you move up and down, and each one
  can be given a name of your own, a floor and a ceiling on how many cells it may have, which
  end of its heading survives, whether a page of columns begins at it, and whether it is
  drawn without capital signs. One column can be the
  one repeated on every page, and "the first column shown" is one of the answers, since that
  is what a table does when you have named none — as is "no column repeated", which is this
  table's own decision rather than the setting that turns the repeat off for all of them. Each per-column question also has "follow the
  table setting", which names what the table is currently doing, so nothing in the dialog
  claims a decision you did not make. Checking "remember this arrangement" saves it against the
  table; unchecking it on a table you had remembered deletes what was saved. Cutting from the
  end is for a column whose values begin with something nobody wrote for reading, and a name
  of your own is for a heading that does the same.

- NVDA's **"expand to computer braille for the word at the cursor"** applies to the row your
  cursor is on and to no other. The setting is what makes routing into a word to edit it line
  up, and it is worth having; applied to every row of a band it wrote out the first word of
  every line uncontracted, which is eight expansions where one was wanted. Everything but your
  own row is drawn in whatever table and contraction you chose.

- A column can be drawn **without capital signs**. In a six dot table an all-capitals word
  carries the capitals-word indicator in front of it, so `AAPL` takes six cells and `aapl`
  takes four; `BRK.B` takes eight against five. On a column sized for a stock symbol that is
  the difference between the value fitting and being cut. It applies to the column's heading
  as well as its values, since both are cut to the same width. Nothing else changes: the
  contractions are the same ones, and a column set this way is measured the way it is drawn,
  so the cells it saves are cells you get back. There is nothing to gain from it on an eight
  dot computer braille table, where a capital is a dot inside the cell and costs nothing.

- An **Excel worksheet** reads in columns like any other table. The sheet's extent is
  whatever Excel considers used, and the row and column numbers are Excel's own, so what the
  display says a cell is matches what Excel says it is. A spreadsheet has no headings of its
  own, so what is pinned above the columns is the header row or column you marked with NVDA's
  own commands, and nothing is pinned until you mark one. This uses NVDA's default way of
  reaching Excel; if you have turned on "use UI Automation to access Microsoft Excel
  spreadsheet controls when available" in NVDA's advanced settings, cells are read the
  ordinary way instead.

- On an Excel cell, **the header you marked is shown in NVDA's ordinary one line braille**,
  after the cell's coordinates. NVDA speaks it there and did not braille it, which left the
  braille reader with the cell reference and no column name. It follows NVDA's own "report
  table headers" setting, so turning headers off in Document Formatting turns this off too,
  and it stays on the display for every cell of the column rather than being announced once.
  This is nothing to do with reading the sheet in columns; it applies to any cell you land on.

- A table can be laid out in columns, with a command, and the layout can be **remembered**:
  press the remember command while a table is on the display and that table comes up laid
  out the next time you are in it, on the same page or in the same list — including when you
  arrow out of the table and back into it. What is remembered is that this table is read in
  columns; which columns fit and how wide they are is worked out afresh for whatever display
  you are on, so one memory serves a Monarch and a Focus 80 — and a column that was empty
  when you saved comes back when it has something in it. Turning the columns off leaves the
  memory alone until you
  come back; the forget command drops it for good. Tables on web pages are recognized by the
  page's address together with what their columns are called, so a watchlist that is
  regenerated is still the same table; a list view is recognized by the application and
  window it belongs to, which means File Explorer's Details view is one table whichever
  folder is open.

## Arrow keys in a table

Inside a browse mode table — a web page, a message in your mail, anything NVDA reads in
browse mode — the arrow keys read the table itself:

- Up and down move a row and stay in the column, so you can read down a column of figures
  without reading every row on the way.
- Left and right move a cell and stay in the row.
- Home and end move to the first and last cell of the row.

There is nothing to turn on and nothing to turn off. Being in a table is the whole of the
state, and no key is ever swallowed: when there is no cell in the direction you pressed, the
key does exactly what it does anywhere else on the page. The down arrow on the last row
leaves the table, the up arrow on the first row leaves it upward, and the right arrow in
the last cell of a row carries on into whatever follows the table. Nothing announces an edge,
because from where you are sitting there is not one.

This is independent of laying a table out in columns on the display; it applies whether or
not the table is showing as a flow.

Shift with the arrows still selects, and control with the arrows still moves by word and by
paragraph, so a long cell can still be read a word at a time and say all still reads
everything. What you give up is moving line by line *within* a tall cell with the down arrow,
which is the trade a table reading mode is.

The movement itself is NVDA's own, the same one control+alt+arrow uses, so merged cells and
your layout table setting behave exactly as they do with NVDA's table commands, and what is
announced when you arrive in a cell is what NVDA announces. If you have set "report tables"
to off in NVDA's Document Formatting settings, the arrow keys are left alone entirely.

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

## Drawings and charts

Some braille displays can raise pins one at a time rather than only whole braille cells. On
one of those, this add-on can put a tactile figure on the display: a chart drawn from the
cells you have selected in a spreadsheet, or a picture copied off the screen. The figure and
your braille line share the panel, with no mode to switch into and nothing suspended, and a
routing press on the figure says what is under your finger.

This is the newest part of the add-on and the part with the least hardware time behind it.
Treat it as experimental. What it does well and badly is stated below rather than left for
you to discover, because a drawing is the one thing on a braille display you cannot check
against anything.

### A display that can draw

The add-on ships its own driver for the Humanware Monarch, **"BrlMultiline: Humanware
Monarch (pin mode)"**, and you have to choose it in NVDA's Braille settings to get any of
this. NVDA's own Monarch support writes through the display's braille cell reports, which
reach about half the panel's pins and leave a blank column between every pair of them. This
driver writes the whole 96 by 40 pin panel as one report instead, which is what makes a
drawing possible.

Three other things come with it, and they are worth having whether or not you ever draw
anything:

- **A choice of how many braille rows.** A "Braille rows" setting appears with the driver's
  other settings in NVDA's Braille settings: 8 rows with a blank row between them, which is
  the default and what the Monarch normally gives you, or 10 rows without. All eight dots are
  drawn either way, so a cursor and an eight dot table read correctly in both.
- **Touch at pin resolution.** The Monarch reports which pin a finger landed on, and NVDA
  discards that. This driver keeps it, which is what lets you point at part of a drawing.
- **Bluetooth that survives a dropout**, the same recovery the combined display uses.
- **Keys with names of their own.** NVDA calls both of the Monarch's d-pads `dpadUp`,
  `dpadDown` and so on, and calls its zoom keys `brailleUsage544` and `brailleUsage545`. This
  driver adds `leftDpadUp` and `rightDpadUp` and the rest for the two pads, and `zoomIn` and
  `zoomOut` for the zoom keys. NVDA's names still work, so both pads are still the arrow keys
  until you bind one of them to something else. When you press a pad in Input Gestures, NVDA
  asks which name to bind: choose the left or right name to bind that pad alone, or the plain
  `dpadUp` form to bind both.

Nothing above the driver knows that a Monarch is what it is talking to. The add-on asks the
braille display driver in use whether it can raise pins and draw on them, so another display
whose driver offers the same thing would work without a change here — but the add-on's Monarch
driver is the only one that offers it today. A display that cannot draw says so when you press
one of these commands, rather than doing nothing, since "nothing happened" is the one answer
you cannot act on.

### Charting a spreadsheet selection

**Graphics: Chart the selected cells** draws what you have selected in a spreadsheet as a
tactile chart. This is the thing the whole drawing path was built for: a chart from live
application data, on a display you can point at. Excel is the application supported today.

Four kinds of chart can be drawn:

- A **bar chart** — one column of numbers standing on a baseline.
- A **line chart** — up to four columns over a shared period, each with its own texture:
  solid, dashed, dotted, dash dot. The textures are read out in speech rather than drawn as a
  key, since a key on the panel would cost a quarter of the drawing to say what one sentence
  says once. With three or four lines the first one is drawn two pins thick, because it is the
  one you came for and the one you lose first where the textures cross.
- **Open, high, low, close bars** — four columns as price bars.
- **Candlesticks** — the same four columns drawn the other way.

**It asks which one rather than guessing.** Four columns of numbers with dates down the side
are four measurements over time if they are measurements and one instrument's trading if they
are prices, and nothing in the cells tells them apart. So a "Which chart?" dialog offers what
can actually be drawn from this selection, with what you chose last time selected for you. A
selection that can only be one thing is drawn without asking, because the question exists for
selections that are ambiguous and not as a step on the way to every chart.

A chart is composed for the space it is going into rather than shrunk to fit it, so the
whole-drawing view is the natural one. Magnifying a chart composes it again with fewer bars,
each wide enough to be named, rather than enlarging the dots it already drew.

A zoomed line chart spaces its points a whole number of pins apart: one, two or three. Forty
points spread exactly across 96 pins would sit 2.4 pins apart and come out as uneven gaps a
finger feels as a wobble, so the window is widened or narrowed slightly until the spacing is
even. The whole chart is never adjusted, since it has to show every point. The chart's name
says how the points sit on the pins: "2.6 points per pin" when some share a column, "1 pin per
point" when each has its own.

**Graphics: Zoom a chart to one pin per point** goes straight to the view where every point has
its own pin column, about the middle of what you are reading. With 250 points on a 96 pin panel
that is 2.6 times, which no doubling reaches. Magnify and shrink from there go to the doublings
either side of it.

**Graphics: Show the next set of lines on a chart** and **Graphics: Show the previous set of
lines on a chart** step through the whole chart, each line alone, and then, with three or more
lines, the first line with each of the others. The zoom and your place are kept, every line
keeps its texture, and the lines put aside still set the scale, so a line alone sits at exactly
the height it had with the others.

Pressing a routing key on a line chart answers for the line as drawn, anywhere within two
columns and three rows of the press, which is about the pad of a finger. Every line inside that
area is read, nearest first, at the one point under your finger: "Close 96.3, Lower 98.5, day
149". Away from every line, it reads the height as a value instead.

Every way this can fail says which way: not a spreadsheet, nothing selected, no numbers in
the selection, a value that is not a number, more bars or periods than the display can hold,
more series than can be told apart, or four columns that turn out not to be prices. A command
that only said it had failed would leave you to guess at which.

### Drawing a picture off the screen

**Graphics: Draw the picture here** copies whatever you are pointing at off the screen and
puts it on the pins. On a web page that is the graphic NVDA just told you about: arrow onto
it and press this, with nothing to aim first.

It is deliberately not restricted to things NVDA calls a graphic. A diagram in a canvas, a
map, a floor plan and a chart somebody published as a picture are all worth a hand, and half
of them report a role that says nothing useful.

Expect it to be good for line art, logos, diagrams and maps, adequate for high contrast
photographs, and poor for everything else. It draws what is in the picture; it does not
decide what in the picture matters, so a photograph of somebody standing in front of a
bookcase comes out as a person and a bookcase.

**Graphics: Change how a picture is drawn** cycles three styles and says which you are on:

- **Outlines** — the edges the picture holds, thinned to one dot where the picture has one
  edge. A thick stroke has two real edges, an outer and an inner, and comes out as a double
  line; that is the picture being reported accurately rather than a fault, and no amount of
  tuning will merge them.
- **Brightness** — the dark parts of the picture raised. This is the style that draws the ink
  itself rather than its edges, so it is the one to try on a shape whose inside matters.
- **Brightness reversed** — the light parts raised instead. Worth pressing on anything drawn
  light on dark.

None of the three can be chosen in advance, because the same picture drawn two ways gives two
entirely different panels. Press it and feel. It redraws the pixels it already has rather than
copying the screen again, so the page moving underneath you cannot change the picture you are
reading.

A picture that cannot be drawn tells you why: the screen curtain is on, there is nothing
there, it is not showing on the screen, it is not in view and wants scrolling to, it is too
small on screen to be a picture, or it could not be copied off the screen at all.

### Reading a drawing with your hands

Press a routing key where your finger already is. The display reports which pin you touched
and the add-on says what is there — that is the whole gesture, with no second command and
nothing to hold.

- On a **chart**, it names what is under you: which bar and what it is worth, which series at
  which date, or a whole period's open, high, low and close.
- On a **picture**, it says where you are as a percentage across and down, which is the fact
  that stays true whatever the picture happens to be.
- Anywhere else on a figure, it says "raised at" or "blank at" with the position inside the
  drawing, counted from its top left corner.

It reports the **nearest raised dot** rather than the exact pin you pressed, within about half
a braille line. A fingertip is far wider than a pin, and a border one dot wide touched
squarely reads as blank whenever the display's idea of the contact center lands a pin to
either side of the ridge your finger is actually on — which is most of the time. That search
distance follows the magnification: it grows as a drawing is compressed, where one pin stands
for several dots of the original, and falls away as you magnify, where a finger can be placed
exactly.

A press beside the figure rather than on it says "outside the picture", so a press that found
nothing is never confused with a part of the drawing that holds nothing.

### Magnifying and moving

**Graphics: Magnify the drawing** and **Graphics: Shrink the drawing** work a ladder of
doublings, and **Graphics: Move the drawing view** up, down, left and right move what is
visible once you are past the bottom of it.

The bottom of the ladder shows **all** of the drawing, compressed as far as it needs to be.
That is the view you want first: the shape of the thing, then the detail. Nothing is off the
edge there, so panning reports that the whole drawing is showing rather than claiming an edge
that is not there. Fitting only ever shrinks — a drawing smaller than the panel is shown at
the size it was drawn rather than blown up to fill the panel, so magnification is always
something you asked for.

Above that are five doublings, capped where a single dot of the original is wider than a
braille cell and you would be feeling the magnification rather than the figure. A picture goes
one step past the point where one pin stands on one captured pixel: no new detail appears,
because there is none left in what was captured, but the shape under your hand gets bigger,
and a pin is a small thing to read a shape with.

When a picture is compressed, **a pin is raised if any dot it covers is raised**, rather than
sampling the middle one. Compression is where a tactile drawing is most easily ruined: a line
one dot wide reduced to a quarter is missed by three sample points out of four and comes out
dashed or gone altogether. Taking any dot keeps every line the drawing had, at the cost of
thickening a busy area into a solid one — which is the right way round, because you can feel
that a region is busy and cannot feel a line that is not there.

**Where you are is said as a percentage of how far you can move**, with 0 hard against one
edge and 100 hard against the other, and the ends named rather than numbered: "25 across, 51
down", then "left edge, 51 down". An axis that cannot move is left out rather than reported as
a meaningless zero. The origin is held internally as a dot of the original, which is the right
thing to compute with and the wrong thing to say — how far across a picture you are is a fact
about what you are feeling, and "at 48, 18" is a fact about how large the picture happens to
be.

A zoom that does not move says why, rather than leaving you pressing a key that seems dead:
the closest view, no more detail to show, as large as the pins can show, or this part will not
draw. For a picture it adds the size it was captured at, which is the whole answer to the
commonest confusion about one — a toolbar with six icons plainly visible on screen that will
not magnify is a toolbar 160 pixels wide spread across 96 pins, already under two pixels to a
pin.

Two things a drawing may tell you about itself, and both are said every time you pan rather
than only on the command that caused them:

- **"Only background here"** — you have magnified into a part of the picture that holds
  nothing. The empty middle of a shape is a real answer, so it is drawn blank and named,
  rather than refused. Refusing it would strand you: a refusal is a move that does not
  happen, so you could never pan across an empty middle to reach the far rim.
- **"Too detailed to draw whole; magnify to read it"** — the picture holds more edge than the
  pins can carry. What gets drawn in that case is an even scattering, which is an honest
  account of a texture and a dishonest one of a drawing, and a hand cannot tell the two apart.
  So it is said rather than left to be discovered.

### The braille line beside it

**Graphics: Show or hide the braille line beside the drawing** decides how much of the panel
the figure gets. On a Monarch at the default 8 rows, a figure is 96 by 35 pins with a braille
line kept beside it and 96 by 40 without — a seventh more, across the middle of the panel where
your hands already are, which is often the difference between a shape a hand can follow and one
it cannot. The switch works without leaving the figure, so your magnification and position
survive it.

Giving the figure the whole panel has a real cost on a single display: the rows the focus was
using are handed over, so NVDA's focus output is dropped while the figure is there. That is
stated when it happens and undone by the same command. With two displays you do not pay it —
put the focus on the other display and give the whole panel to the drawing, which is the
arrangement to prefer where the hardware allows it.

### While a drawing is up

A drawing outranks everything else that wants those rows, including the flow. That is
deliberate: a drawing is something you turn on, read, and turn off, while the flow is a
standing preference that should come back by itself afterward, and does. While a figure is up
the flow does not claim rows at all, and leaving the figure brings it straight back.

**Graphics: Show or hide a drawing on the display** takes the figure off, and puts it back.
Pressed with nothing to show it puts up a test figure sized to the panel, which is how to
check that a display can draw at all.

**Graphics: Reports the drawing on the display** says what is there, how far it is magnified
and where in it you are, without changing anything.

### What a drawing can and cannot do

- A figure and your braille line are composed onto one surface. There is no mode to switch
  into, nothing is suspended, and the cursor goes on working in the braille line beside the
  drawing.
- The panel is a diagram surface, not a screen. The whole of it is 3,840 pins, which is fewer
  dots than any picture worth drawing has pixels, so a picture arrives having already lost most
  of itself before anything else is done to it. That is why line art works and photographs
  mostly do not.
- **Nothing can tell that a window was in the way.** A picture is copied from the screen, so
  what another window was covering is what gets drawn, and there is no way to detect that from
  the copy. If a drawing makes no sense, check that nothing is over it.
- The screen curtain and a drawing cannot both be on, because there is nothing to copy with
  the curtain on.
- Sub-cell touch precision is a starting point set from a handful of deliberate touches on
  hardware rather than a calibration. If naming a bar by pointing at it misses consistently,
  that number is the thing to report.
- No image library is used and none is needed. Everything a picture goes through — decoding,
  reduction, edge detection, thresholding — is plain Python in the add-on, so there is nothing
  to install and nothing that can fail to load.

## Commands

Every command is in the **BrlMultiline** category under NVDA menu, Preferences, Input
Gestures. Most are unassigned: pick the ones you want and give them keys, ideally keys on the
display itself. The drawing commands are the exception and arrive with keys on the Monarch's
own keyboard, because a drawing is something you reach for with your hands already on the
display.

The names are prefixed by what they act on — Graphics, Table, Navigation, Monitoring, Misc,
Debug — so that related commands sort together in a long list. The prefix is part of the name
you are looking for in Input Gestures.

### Getting around the add-on

**Opens the BrlMultiline settings.** Goes straight to the settings category.

**Turns the BrlMultiline segment layout on or off.** The whole division of the display, off
and on again, without going into the settings.

**Misc: Reports the BrlMultiline segment layout.** Says how many segments there are, which one
is following the focus, and what is on the display. Useful for confirming a layout took effect,
and the first thing to press when the display is not showing what you expect.

**Moves the focus onto another of the combined braille displays.** Only does anything when
several displays are driven as one. With two, pressing it moves the segment that follows the
system focus from the one to the other, so you can put the focus where your hands are without
working out a segment number. With more than two, it opens a list to choose from. It keeps how
far down its display the focus segment was, so on two displays divided alike, pressing it twice
puts you back where you started. The answer is stored in the profile in force, like every other
setting here, so it survives a rebuild and a restart.

If a pinned object is sitting where the focus is going, the two trade places: the object moves
to the segment the focus is leaving, and moving the focus back trades them back. You are told
it happened, and nothing asks you anything, because that is what you meant. If there is nowhere
to put what is in the way, or more than one thing is, you are asked which to keep — and you can
cancel, which is the answer when you had forgotten the pin was there. Keeping more than there
is room for is a matter of dividing a segment further first; even a single row display can be
divided, which gets crowded fast and is your call.

### Panning segments

**Navigation: Scrolls segment N of the first / second / third display forward / back.** Pans a
segment named by which display it is on and how far down that display it sits. Prefer these: a
segment's plain number counts across the whole display and moves whenever the layout changes,
so a key bound to "segment 5" quietly starts panning something else. "The second display's
first segment" does not move. On one display, the first display is that display, so these keep
working when you unplug the second one.

**Navigation: Scrolls segment N forward / back.** Pans one segment, whether or not it is the
segment following the focus. There is a pair of these for each segment. These count across the
whole display, so they can reach a segment the display relative commands cannot, at the cost of
moving when the layout does.

A segment that is not following the focus is panned within the content it already has. It will
not move to the next or previous line of a document, because doing so would move the caret in
something you are only reading, and drag the focus with it.

### Keeping an object on the display

**Monitoring: Shows the navigator object in segment N of the first / second / third display.**
Pins the current navigator object to a segment named by display, for the same reason as the
panning commands above.

**Monitoring: Stops showing an object in segment N of the first / second / third display.**
Unpins and clears that segment.

**Shows the navigator object in segment N.** Pins the current navigator object to that segment,
so it stays there while you move around elsewhere. There is one of these for each segment. You
cannot pin an object to the segment that follows the focus.

**Stops showing an object in segment N.** Unpins and clears that segment.

### Reading as a flow

**Toggle reading the whole display as one flowing document.** Turns the flow on or off, and
remembers the answer in the profile in force, so it is the same switch as the one in the
settings. See "Reading as a flow" above.

### Tables

These act on the table your cursor is in. See "Reading as a flow" above for what a table laid
out in columns is and what is remembered about one.

**Table: Toggle table columns on or off.** Lays the table out in columns on the display, or
stops.

**Table: Next columns** and **Table: Previous columns.** Move across a table too wide to show
at once, without moving the cursor.

**Table: Show/Hide the current column.** Takes the column your cursor is in out of the layout,
or puts it back. What you press while exploring a table nobody has arranged.

**Table: Changes how the column you are in is trimmed when it does not fit.** Cycles that one
column between wrapping, cut keeping the start, and cut keeping the end. Cutting from the end
is for a column whose values all begin with something nobody wrote for reading.

**Table: Open table layout designer for the current table.** The dialog for a table you come
back to, where each column can be named, sized, trimmed and hidden. See "Reading as a flow".

**Table: Undo custom table layout.** Gives the table back as it comes, without touching what
was saved for it. The way out of an experiment.

**Table: Save table layout.** Remembers this arrangement against this table, so it comes up
laid out the next time you are in it.

**Table: Delete saved table layout.** Drops what was remembered for this table, for good, so it
reads as the page around it again.

### Drawings and charts

These need a display that can raise pins one at a time, and they are the only commands in the
add-on that come with keys already assigned. The keys are on the Monarch's own keyboard and
arrive with the add-on's Monarch driver; they can be reassigned like anything else. Everything
here is reported in speech as well, since the panel is what your hands are reading and speech is
what tells you something changed.

See "Drawings and charts" above for what each of these does and what to expect of it.

Only the commands that put a drawing up have keys of their own, so the Monarch's keyboard is not
tied up while there is no drawing:

- **Graphics: Draw the picture here** — space with dots 1, 4 and 7.
- **Graphics: Chart the selected cells** — space with dots 5 and 7.
- **Graphics: Show or hide a drawing on the display** — space with dots 7 and 8.

Everything used while a drawing is up is in the Monarch's graphics, chart and picture layers,
which come on by themselves with the drawing; see "Layered keys" below. With the layer on:

- **Graphics: Magnify the drawing** — the zoom in key, or space with dot 8.
- **Graphics: Shrink the drawing** — the zoom out key, or space with dot 7.
- **Graphics: Move the drawing view up, down, left and right** — the left d-pad, or space with
  dots 1, 4, 3 or 6, each with dot 7.
- **Graphics: Show or hide the braille line beside the drawing** — space with dots 2 and 7.
- **Graphics: Zoom a chart to one pin per point** — o, on a chart.
- **Graphics: Show the next set of lines on a chart** — v, on a chart.
- **Graphics: Show the previous set of lines on a chart** — v with dot 7, on a chart.
- **Graphics: Draw the picture as outlines** — o, on a picture.
- **Graphics: Draw the picture by brightness** — b, on a picture.
- **Graphics: Draw the picture by brightness, reversed** — r, on a picture.
- **Graphics: Change how a picture is drawn** — space with dots 1, 4 and 8, on a picture, which
  cycles through the three.

These two have none:

- **Graphics: Reports the drawing on the display** — says what is on the display, how far it is
  magnified and where in it you are, without changing anything.
- **Graphics: Show the glyph catalogue** — see below.

**Graphics: Show the glyph catalogue** is unassigned on purpose: it is a tool for learning the
role and state shapes rather than one used while reading. It draws every symbol with the braille
it replaces beside it, so a hand running along the panel reads the shape and then the word it
stands for. See "Draw roles and states as shapes instead of words".

### Layered keys

Experimental. A layer gives a display's keys, or the
keyboard's, other commands while it is on: with a drawing up, the Monarch's left d-pad can pan it
while the right d-pad goes on being the arrow keys. Each display and the keyboard has its own
layers, and turning one on changes nothing on the others. A key a layer does not use does what it
always does, so typing and reading carry on inside a layer.

- **Layered keys: Turns the layer on or off for the device it is pressed on** — space with dots 1,
  2, 3 and 7 on the Monarch, NVDA+control+shift+l on the keyboard. With a drawing up it turns on
  the chart, picture or graphics layer, whichever fits the drawing, otherwise the default layer. The default layer is one shot: it is off again
  after the next key.
- **Layered keys: Chooses which layer is on for the device it is pressed on** — unassigned.
- **Layered keys: Reports which layers are on** — unassigned.
- **Layered keys: Turns every layer off** — unassigned.
- **Layered keys: Opens the layered keys dialog** — unassigned.
- **Layered keys: Turns the layer on or off for the first, second or third display** — unassigned,
  for turning a display's layer on from the keyboard.

Space with z, or escape, turns a layer off.

The Monarch and the keyboard come with layers already:

- **Monarch, default layer.** The layer key, then the left d-pad: up says the line, down says
  all, left reports the focus, right reports the window title.
- **Monarch, graphics layer.** Comes on by itself when a drawing goes up, and goes when it comes
  down. The left d-pad moves the drawing and the zoom keys magnify and shrink it, and the space
  with dot 7 and dot 8 chords do the same, along with space with dots 2 and 7 for the braille line.
  The right d-pad goes on being the arrow keys. Turn it off with the layer key and it stays off
  until the drawing is taken down and put up again.
- **Monarch, chart layer.** Comes on by itself instead of the graphics layer when the drawing is a
  chart, and gets every graphics layer key from it. Adds o to zoom to one pin per point, and v and
  v with dot 7 for the next and previous set of lines. Other letters still type.
- **Monarch, picture layer.** The same for a picture from the screen. Adds o for outlines, b for
  brightness and r for brightness reversed, and space with dots 1, 4 and 8 to cycle through them.
- **Monarch, table layer.** With the caret in a table on a web page, or a table laid out in columns
  on the display, the layer key turns it on. The left d-pad moves by table cell, and the zoom keys
  turn to the next and previous page of columns. It stays on until you turn it off.
- **Keyboard, default layer.** The layer key, then keypad 5 says the line.
- **Keyboard, graphics layer.** The keypad's arrows move the Monarch's drawing, keypad plus and
  minus magnify and shrink it, and keypad 5 says the line. Turned on with the layer key; it does not
  come on by itself, so the keypad stays the review cursor, unless you set it to in Properties.

Other displays start with an empty default layer.

Once you save a device's layers in the dialog, your saved layers are used instead of the ones it
came with, as a whole. A later version of the add-on that improves the shipped layers does not
change what you changed. The exception is keys the add-on moved: when graphics keys came out of
the global gestures on 15 September 2026, layers saved before then are given the chart and picture
layers and the graphics layer's new chords, once, unless you already had a layer of your own for
that kind of drawing or had used that key for something else. Remove one of them and save, and it
stays removed. Use Reset to factory defaults in the dialog to take everything the add-on ships,
which also drops your own changes for that device.

#### The layered keys dialog

Open it from the NVDA menu, Preferences, BrlMultiline layered keys, from the Layered keys button
in BrlMultiline's settings, or with its command. It works like NVDA's Input Gestures dialog: a
filter, a tree of categories and commands with the keys under each, and Add, Change and Remove.
Above the tree, choose the device and the layer to edit. Changes are saved in the configuration
profile in use when you press OK.

- **Add** waits for a key on the device being edited. A key on another display is refused and
  said so, and escape on the keyboard stops waiting.
- A key that already does something else in the layer asks before it is moved.
- The key that turns layers on and off, and a layer's exit keys, cannot be given to a command.
- A keyboard key for one of BrlMultiline's commands, or a braille command, asks which display it
  acts for, so a keypad key can move the Monarch's drawing.
- **Does nothing in this layer**, under BrlMultiline, blocks a key while the layer is on.
- **New layer**, **Rename**, **Delete layer** and **Properties** manage the layers. Properties
  sets what the layer is for, whether it comes on by itself when that appears (drawings only),
  whether it stays on or is one shot, its exit keys, and which layer keys it does not have come
  from. Keys a layer gets from that layer are listed under their commands, "from Graphics layer";
  Remove on one makes it do nothing in this layer instead of removing it from the other.
- **Only show commands with keys in this layer** turns the tree into a list of what the layer does.
- **Clear this layer** removes its keys. **Reset to factory defaults** puts back the layers the
  device shipped with.
- Commands are gathered from where you were when you opened the dialog, as Input Gestures gathers
  them, so open it from a web page to bind browse mode commands. A key for a command not available
  from there is listed under "Unavailable from here", so it can still be removed.

### Diagnostics

**Debug: Reports what the flow on the display has cost, and copies the detail.** Says how long
the slowest piece of reading took and how often the flow ran out of its allowance and showed you
less than the display could hold, and writes the detail to the log. If a page feels slow, or you
feel the marker meaning "there is more I have not read", press this and send the log: it is a
great deal more use than "it felt slow".

**Debug: Copies what a flowed reading of this object would show to the clipboard.** Reads the
object under the navigator as a flow, onto the clipboard, without moving anything or putting
anything on the display. For reporting what a flow made of something.

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

### Checking against a real NVDA

The unit suite runs against stubs, which is fast and needs no NVDA, and which therefore
cannot notice when NVDA's own API moves underneath the add-on. `tests/nvdareal.py` is the
other half: it brings a real NVDA up from a source checkout, imports the add-on into it the
way NVDA does at runtime, and checks that what the add-on calls is still there and still
behaves as it thinks. It runs safely while NVDA is running, touches no configuration and no
hardware, and needs NVDA's own interpreter rather than this project's:

```bash
C:/code/nvda/.venv/Scripts/python.exe tests/nvdareal.py
```

Set `BRLMULTILINE_NVDA` if the NVDA source is somewhere other than `C:\code\nvda`. Add
`--displays` to see what braille hardware NVDA can find, or `--log` to read the tail of the
log NVDA writes when it is run from source. Read the file's own docstring before relying on
what it reports; it is explicit about what it does and does not prove.

Design documentation, including notes on the parts of NVDA this add-on depends on, is in
`docs/design/`.

## License

GNU General Public License version 2.
