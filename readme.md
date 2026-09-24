# BrlMultiline

* Author: Travis Roth
* NVDA compatibility: 2026.3 and later. 2026.3 is not released yet, so for now this means
  an NVDA alpha snapshot. The add-on uses NVDA's `braille` package, which replaced the old
  single `braille.py` module after 2026.2 branched.
* Download: development version

BrlMultiline is an NVDA add-on for getting more out of a multi-line braille display, or out
of two or more displays used together.

NVDA was designed around single-line displays: it shows one thing at a time, and that thing
follows the focus. NVDA 2026.1 introduced flowing that one thing across the rows of a
multi-line display, but it is still one thing. This add-on uses the extra space in two ways:

1. **Reading as a flow.** A web page, a document, a list, or a chat fills the display the way
   a printed page would: a heading with the text that follows it, a paragraph across as many
   rows as it needs, the items around the one you are on. Tables can be laid out in columns.
2. **Showing different things in different places.** A display, or several displays combined,
   can be divided into segments. One segment follows the focus as usual, while another keeps
   something in view, such as a chat or a status line.

There is also an experimental part for displays that can raise individual pins, such as the
Humanware Monarch: tactile charts drawn from a spreadsheet selection, and pictures copied off
the screen.

The add-on works with:

1. A multi-line display such as the Humanware Monarch.
2. A large single-line display such as a Focus 80, which can be split into two 40-cell areas.
3. Two or three displays combined, such as a Monarch with a Focus 80 below it.

This is development software. It patches parts of NVDA's braille handling.

### Why I built this

The main goal of this add-on is to give braille readers a multi-line, spatial experience. A
picture is worth a thousand words, and having something real to try may help people imagine
what is possible. I hope it sparks more ideas about how spatial information can make braille
more efficient, more useful, and more pleasant to use.

I started out wanting a prototype that showed what multi-line braille could be like: a list
that shows more than the focused item, a web page that shows more than one link, a heading
with the text after it for context. Tables are where I think multi-line braille can be a real
asset, showing several rows and columns at once. That part took on a life of its own. Even on
a multi-line display, space is at a premium, so the add-on gained ways to lay out a table the
way you want it: show these columns, pan by columns, and keep the headers in view so you know
where you are.

I don't claim any of this is the best way to present information. It is one way. Likewise,
the add-on's code is meant to show what is possible, and may or may not be the best way to
build this into NVDA itself.

## Getting started

Installing the add-on changes nothing by itself. Each feature is off until you turn it on,
and settings are kept per display, so a display you haven't set up behaves the way it always
has.

The settings are in three panels under NVDA menu, Preferences, Settings:

1. **BrlMultiline flow**: reading as a flow.
2. **BrlMultiline displays**: combining two or more displays.
3. **BrlMultiline**: dividing a display into segments, and a few per-display options.

Most commands have no key assigned. You'll find them all in NVDA menu, Preferences, Input
Gestures, in the BrlMultiline category.

### Step 1: Turn on reading as a flow

Flow is the main feature of the add-on and the best place to start.

1. Connect your multi-line display.
2. Optional: create an NVDA configuration profile for your web browser (NVDA menu,
   Configuration profiles), and make sure you are editing it before the next step. Flow
   settings are saved in whichever profile you are editing, so the flow will then turn on and
   off by itself as you move between your browser and other applications.
3. Open NVDA menu, Preferences, Settings, BrlMultiline flow.
4. Check "Read this display as a flowing document."
5. Under "Use it for," Browse mode is already checked. Check "Lists and menus" and "Editable
   text" as well if you want those read as a flow. (These work outside of browse mode.)
6. Press OK and open a web page.

The page now fills the display. Here is what to expect:

- Panning moves a full display at a time, and panning back returns you to exactly where you
  were.
- Moving the cursor within what is already on the display does not move the display, so you
  keep your place while you read.
- Quick navigation keys, such as h for the next heading, put what they found on the top row,
  with the page continuing below it.
- In a dialog, or in an application the flow doesn't handle, the display shows the focus the
  same way NVDA normally does. The flow picks up again when you return to a document.
- A flow needs at least two rows. On a single-line display, braille works the way NVDA
  normally shows it, with the flow on or off.

To turn the flow on and off without opening settings, assign a key to "Toggle reading the
whole display as one flowing document" in Input Gestures.

See [Reading as a flow](#reading-as-a-flow) for every setting, and for how tables work.

### Step 2: Two displays, one for reading and one for keeping watch

With two displays connected, you can use them together: read and work on one as usual, and
pin something to the other so it stays in view. A typical setup is a Monarch for reading,
with a single line below it showing a Microsoft Teams chat, so new messages appear there while
you work elsewhere.

**Combine the displays.**

1. Connect both displays.
2. Open NVDA menu, Preferences, Settings, BrlMultiline displays. Add the displays you want and
   put them in order, top first.
3. In NVDA's own Braille settings, choose "BrlMultiline: several displays as one" as the
   braille display.

NVDA now treats the two as one tall display. The keys on both displays work as usual.

**Choose which display follows the focus.** By default, the focus is shown in the last
segment, which is on the bottom display. With the Monarch on top, that puts the focus on the
single line display. To move it, assign a key to "Moves the focus onto another of the combined braille
displays" and press it; each press moves the focus to the other display.

If the flow is turned on, it goes on whichever display has the focus, and moves with the focus
when you switch displays. A flow needs at least two rows, so when the focus is on a single-line
display like the Focus 80, that display shows braille the way NVDA normally does. Move the
focus back to the Monarch and the flow returns. To keep the flow on the Monarch no matter where
the focus is, choose it in "Display the flow appears on" in the BrlMultiline flow settings.

**Pin something to the other display.**

1. In Input Gestures, under BrlMultiline, assign keys to "Monitoring: Shows the navigator
   object in segment 0 of the second display" and "Monitoring: Stops showing an object in
   segment 0 of the second display." Segments are numbered from 0 on each display. Use the
   "first display" versions instead if the display you want to pin to is on top.
2. Move to what you want to keep in view, such as the chat message list in Teams. NVDA's
   navigator object follows the focus, so moving the focus there is usually enough; object
   navigation works too.
3. Press your pin key.

What you pinned stays on that display while you go back to work on the other one. To pan it,
use that display's own panning keys, or assign keys to "Navigation: Scrolls segment 0 of the
second display forward" and "back."

A pinned chat, list, or menu needs only one row: each item is one line, and panning moves to
the next item. That makes a single-line display a good chat monitor. When you are at the end
of a pinned chat, new messages scroll into view as they arrive. If you have panned back to
read earlier messages, nothing moves until you pan to the end again.

For Teams specifically, install the
[Microsoft Teams add-on for NVDA](https://github.com/travisroth/ms-teams). It tells
BrlMultiline how Teams' chat history is put together, so a pinned chat starts at the newest
message and pans message by message. Without it, Teams' chat history cannot be read as a flow.

See [Using two displays at once](#using-two-displays-at-once) for more detail.

### Dividing a single display into segments

You can also divide one display into segments, without a second display. For example, the
bottom row of a Monarch can hold a pinned object while the rest follows the focus, or a Focus
80 can be split into two 40-cell halves. The settings are in NVDA menu, Preferences, Settings,
BrlMultiline. Connect the display you want to set up first: the panel shows which display its
settings apply to, and each display keeps its own.

By default a display is left as one segment.

**Number of segments.** How many pieces to divide the display into. The division is even,
with any remainder going to the earlier segments. On a display with more than one row,
segments are groups of whole rows: a Monarch with 8 rows divided into 4 gives four segments
of 2 rows each. On a single-row display, segments are slices of that row: a Focus 80 divided
into 2 gives two 40-cell halves.

**Segment sizes.** Leave this blank to divide evenly. To make segments of different sizes,
type them separated by commas. The units are rows on a multi-row display and cells on a
single-row display, and the sizes must add up to the whole display. For example, `1, 5, 2`
on a Monarch gives a one-row segment, then a five-row segment, then a two-row segment.

**Segment that follows the focus.** Which segment shows whatever has focus, the way NVDA
normally does. Segments are numbered from 0. Enter -1, the default, for the last segment.

**Segment that flash messages appear in.** Where NVDA's brief messages go, such as the time,
a setting you just changed, or "selected." Enter -1, the default, to show them wherever the
focus is. Choosing a specific segment is most useful with two displays combined, to keep
messages off the display you are reading.

**Reverse the panning keys on this display.** Swaps the panning keys, so the key that
normally scrolls back scrolls forward instead. Because it is stored per display, you can have
it reversed on a Focus 80, where the left-hand key is more comfortable for moving forward,
and normal on a Monarch. NVDA itself has no such setting.

With two displays combined, this follows the keys you press rather than what is on the
display: pressing the Monarch's panning key uses the Monarch's setting, even when the segment
that moves is on the other display. Use the "Segment settings for" box to set it for each. If
you had reversed the keys for a combined display in an earlier version, when it was one
setting for the pair, that choice is carried over to each display you have not set yourself.

**Draw roles and states as shapes instead of words.** Off by default, and only offered on a
display that can raise pins one at a time, such as a Monarch. Important: the display driver
has to support tactile drawing, and NVDA's built-in HID Standard driver does not. See
[A display that can draw](#a-display-that-can-draw).

NVDA writes short words in braille to say what something is: `btn` before a button's name,
`cbo` before a combo box, and three cells of braille for a checked box. On a pin display, the
space one braille cell takes up is a grid of three pins by four, and a shape drawn in those
twelve pins can say "button" in one cell instead of three.

With this on, each of those words becomes a shape, and the cells it was using go back to the
line, so more of the object's own name fits on a row. It applies everywhere, from a web page
to the menu bar in File Explorer. The shape sits where the word was, and a routing key on it
does the same thing. Turning the setting off brings the words back. Anything without a shape,
or a display that cannot draw, shows the words as usual.

There are twenty-six shapes: buttons, toggle and radio buttons, edit and password fields,
combo boxes, lists, menu items, tables, graphics, progress bars, separators, submenu markers,
links, visited links, headings at levels one through three, and the checked, half checked,
and pressed states and their opposites. Together they save sixty-five cells. Headings below
level three keep their word, since a shape you rarely meet is harder to recognize than a
word.

It is off by default because the words are a notation you already know, and the shapes are
new. To learn them, assign a key to "Graphics: Show the glyph catalogue" in Input Gestures. It
draws every shape with the braille it replaces beside it, so you can read the shape and then
the word it stands for.

<!-- (Flow does a better job than this so this may be removed)
**Show the document lines around the caret in the other segments.** When you are in a
document, fills the segments around the focus segment with the lines above and below the
line the caret is on. The segment immediately above the focus segment shows the previous
line, the one above that shows the line before it, and so on. Segments holding a pinned
object are left alone. Segments run past the start or end of the document show as blank.
-->

## Reading as a flow

Normally a braille display shows one thing: the line the cursor is on, or the object with
focus. On a display with several rows, that can leave a heading alone on the top row with the
rest of the display blank, or make a long paragraph pan one line at a time through rows that
could have held all of it.

Reading as a flow presents a document as one continuous piece instead. A heading is followed
by whatever comes after it on the page, a paragraph runs across as many rows as it needs, and
panning moves a full display at a time, so panning back puts you exactly where you were.
Moving the cursor within what is already on the display does not move the display, so you
keep your place while you read. Pressing a quick navigation key, such as `h` for the next
heading, puts what it found at the top, with the document continuing from there.

The settings are in NVDA menu, Preferences, Settings, BrlMultiline flow. They are saved in
the configuration profile you are editing, so you can make a profile for your browser, turn
the flow on there, and leave your normal configuration alone. The flow then turns on and off
as you switch applications, with nothing to press.

### Flow settings

**Read this display as a flowing document.** The main switch. Off by default.

**Use it for.** Which kinds of content are read as a flow.

- *Browse mode*: web pages, and documents NVDA reads like one. On by default once the flow is
  on.
- *Lists and menus*: a list box, a menu, a tree view, or the choices of an open combo box. The
  display shows the items around the one you are on, instead of that one item alone. Off by
  default, because it changes how dialogs read. It never moves your selection: the arrow keys
  still decide where you are. A routing key on the item you are already on does what it
  always did; on any other item, it moves you there without activating it.
- *Editable text*: editors and edit fields outside browse mode, such as Notepad. The lines
  around the caret are laid out the same way as a page, while typing, selecting, and cursor
  routing still go to the real edit control. Off by default, so turning on flow for web
  pages doesn't change how every editor reads.

Everything else is shown by NVDA as usual, in the same rows, so nothing disappears when you
leave a page.

**Display the flow appears on.** Only shown when several displays are combined. A flow has to
stay on one display, and this says which. The automatic choice, "Automatic: the display with
the focus," puts the flow on whichever display the focus is on, and names that display. When
you move the focus to another display, the flow goes with it. Choose a specific display to
keep the flow there instead.

**Rows it uses.** How many rows of that display the flow takes, counted from the top. Leave
it at 0 to use all of them. Giving it four rows of a Monarch leaves the other four for your
segments, so you can read a page as a flow and keep an eye on something else below it.

A flow needs at least two rows. If the display it would go on has only one row, or this is
set to 1, the flow isn't shown, and braille there works the way NVDA normally does. The note
under this setting says when that is the case.

**Start reading afresh from what a quick navigation key found.** On by default. When you press
a quick navigation key that jumps past a section, such as a heading, table, landmark, or
list, what it found goes on the top row with the document continuing below. Turn this off if
you would rather the display keep its current position and simply follow the cursor.

**In a multi-line edit you are typing in, read a paragraph at a time.** On by default. In an
edit box you are writing in, a paragraph is what you typed, and a line is how the box
happened to wrap it. Turn this off to split an edit box by line, following NVDA's own "read
by paragraph" braille setting.

The default was chosen because of one rich editor where asking for the line at the caret
briefly returns a single character after each Enter, while asking for the paragraph always
returns the right text. That may not hold everywhere, so if an editor reads oddly, try turning
this off before reporting it.

**Show every line on one row, unwrapped, and pan across it.** Off by default. Meant for code.
When lines wrap, a long line takes several rows, and the indentation down the left side no
longer lines up. Unwrapped, every line gets exactly one row, and anything past the width of
the display continues to the right. Pan right and every row moves across together, showing
the next part of each line; a line too short to reach that far is blank. Panning stops at the
end of the longest line on the display. Moving the caret to a part of a line that isn't
showing brings the display to it, but if you pan across without moving the caret, the display
stays where you put it.

There is also a command to turn unwrapped lines on or off for the moment without changing the
setting, since one file can hold both code and prose. What the command chose lasts until the
setting itself changes, so opening NVDA's menu and coming back keeps it.

**Show how deep an item sits with.** For tree views and other controls that report how deeply
their items are nested. Choose two spaces per level (the default), one space per level, or
dots 7 and 8 per level. Depth is shown relative to the shallowest item on the display, so a
deep tree still leaves room for the items themselves.

**Mark the item you are on with dots 3678 at the left of its row.** On by default. The mark is
in the same place on every row, so you can find your item by running a hand down the left
edge instead of reading each row. It is drawn in the item's indent, so it takes no extra
space. An item at the left margin has no indent, so it gets no mark.

**Rows of the display to spread a table's columns across.** How many display rows one table
row may use. The default is 1, which fits the most table rows on the display. Raise it to fit
more columns on a page at readable widths, at the cost of fewer table rows at once.

**Cut table cells that are too long, instead of wrapping them.** Off by default. Off, a table
row grows as tall as its longest cell needs, and every value is shown in full. On, each table
row stays within its rows and long values are cut. Useful for a table you know well, where
the first few characters of a value are enough to recognize it.

**Repeat the first column on every page of a wide table.** A table too wide for the display is
shown one page of columns at a time. With this on, the first column is repeated at the left of
every page, shortened to fit, so you can still tell which row you are on. Choose Always,
Never, or "As set in NVDA's Document Formatting," the default, which follows NVDA's setting for
reporting row headers.

**Keep a table's header row on the display.** With this on, the top row of the display holds
the table's column headers wherever you are in the table. It costs one row. Turn it off on a
short display, or for a table whose first row is not headers. The choices are the same as the
setting above, and the default follows NVDA's setting for reporting column headers.

**Follow a page that changes while you are reading it.** On by default. NVDA refreshes only the
line the cursor is on. This display shows several lines at once, so the rest are read again
when the page reports a change, such as a price on a watchlist, a score, or a status. Only
what is on the display is read again from the dynamic page to optimize API calls and keep the display feeling responsive.

**Also re-read every (seconds).** At 0, the default, the display is updated only when the page
reports a change. Set a number of seconds for a page that changes without reporting it. The
display is only rewritten when something actually changed.

**Scroll new content into view while you are at the end.** On by default. For something pinned
to a segment that is still being written, such as a chat, a log, or a build. A new message
scrolls onto a full display and the oldest row moves off. Turn it off and new content waits
below the display until you pan to it.

Two rules always apply. Nothing moves while you have panned back into earlier content, and
reaching the end again resumes following. And something that is merely longer than the
display does not scroll by itself: you have to have reached the end once, so a pinned page
never moves while you are reading it.

### What to expect

- The flow's rows are reserved for as long as the flow is on, and are taken from your segment
  layout. Where there is nothing to read as a flow, such as a dialog, those rows show the
  focus exactly as an undivided display would, and the flow starts again when you reach a
  document.
- Panning moves the browse mode cursor to what is at the top of the display, so speech and
  braille stay together, and the arrow keys continue from what is under your hands.
- A control inside a page is still read as part of that page. When you type into it, the
  row you are on comes from the real edit control, so the caret and the new text stay current,
  while the label and the content after it still come from the page.
- On a form, the page content before a field is shown above it, and a blank row separates a
  field from the next prompt. A field you tab to is shown the way browse mode shows it: its
  name, role, and state.
- A multi-line edit you are writing in, such as a comment box or a message, is read as a
  document of its own, so the lines around the one you are on are on the display and the
  cursor follows what you type. Leaving it brings the page back.
- Every quick navigation key moves the display. A heading, table, landmark, or list puts what
  you jumped to on the top row with the document continuing below it. A field, button, or link
  is brought onto the display without disturbing the rest.
- A pinned list, menu, or chat needs only one row: one row is one item, and panning moves to
  the next. A pinned document or table needs at least two rows, since its layout across rows
  is the point.
- NVDA's **"Expand to computer braille for the word at the cursor"** setting applies only to
  the row your cursor is on. Applied to every row, it would expand the first word of every
  line. All other rows use the braille table you chose.

### Tables in a flow

A table can be laid out in columns on the display. Use "Table: Toggle table columns on or off"
with the cursor in a table.

**Wide tables.** A table too wide for the display is split into pages of columns. "Table: Next
columns" and "Table: Previous columns" move across it without moving the cursor. The page you
turn to stays put while you read down the rows. With the first column repeated at the left of
every page, the cell you are in is still on the display. If you move to a column the current
page doesn't show, the display returns to your cursor.

**Arranging columns.** Three commands work on the column your cursor is in, for quick changes
while exploring a table:

1. "Table: Show/Hide the current column" hides the column, or shows it again.
2. "Table: Changes how the column you are in is trimmed when it does not fit" cycles between
   wrapped, cut keeping the start, and cut keeping the end. Cutting from the end is useful for
   a column whose values all begin with the same code or prefix.
3. "Table: Undo custom table layout" restores the table's original layout if an experiment
   goes wrong.

For a table you come back to, "Table: Open table layout designer for the current table" opens
a dialog. The columns are a checked list, where checked means shown, and you can move them up
and down. For each column you can set:

- A name of your own, useful when the table's own heading is unhelpful.
- The fewest and most cells it may use.
- Which end of its heading to keep when the heading is cut.
- Whether a new page of columns starts at it.
- Whether it is drawn without capital signs (see below).

One column can be chosen to repeat on every page. "The first column shown" is one choice, and
is what happens if you choose none. "No column repeated" turns the repeat off for this table
only. Each per-column option also has "follow the table setting," which shows what the table
is currently doing. Check "remember this arrangement" to save it for this table; unchecking it
on a table you had saved deletes the saved layout.

**Columns without capital signs.** In a six-dot braille table, an all-capitals word has a
capitals indicator in front of it, so `AAPL` takes six cells and `aapl` takes four; `BRK.B`
takes eight cells instead of five. In a column sized for stock symbols, that can decide whether
the value fits. It applies to the column's heading as well as its values. Contractions don't
change, and the column is measured the way it is drawn, so the cells saved are cells you get
back. It makes no difference on an eight-dot computer braille table, where a capital is a dot
inside the cell.

**Remembering a layout.** Press "Table: Save table layout" while a table is on the display, and
that table is laid out in columns the next time you are in it, on the same page or in the same
list, including when you arrow out of the table and back in. What is saved is the arrangement.
Which columns fit, and how wide they are, is worked out again for the display you are using, so
one saved layout works on both a Monarch and a DotPad. A column that was empty when you saved
comes back when it has something in it. Turning columns off leaves the saved layout alone;
"Table: Delete saved table layout" removes it.

Tables on web pages are recognized by the page's address together with the names of their
first columns, so a watchlist that is regenerated is still the same table. A list view is
recognized by its application and window, so File Explorer's Details view is one table no
matter which folder is open.

A web page matches by its site and path, ignoring anything after the question mark in its
address, since sites change that part often. A column the site adds or moves is tracked by its
heading, so your choices for it stay with it. Saved layouts are kept in your normal
configuration and apply in every NVDA profile. When a saved layout does not apply, the NVDA log
says why, once: no layout for this address, or none for these headings.

**Managing saved layouts.** "Table: Manage saved table layouts" lists every layout you have
saved, with the ones for the current table first. Each shows its name, where it applies, how
strictly its address matches, how many columns it shows, and when it was last used. Opened from
a table, it says which layout applies there, if any. For the selected layout you can:

- **Use for this table**, which points it at the table you are in. This fixes a layout after a
  site changes its address. If another layout matches this address more closely and would still
  apply, it tells you which one.
- **Also use for this table**, which copies it to the current table and leaves the original.
- Change **Match**: this page with any query, this exact address, or anywhere on this site.
- Uncheck **Only for a table with these headings**, for a site that renames a column.
- Rename it, edit its address, or delete it.

Nothing is saved until you press OK. 

**Excel.** An Excel worksheet reads in columns like any other table. The sheet's extent is
whatever Excel considers used, and the row and column numbers are Excel's own. A spreadsheet has
no headings of its own, so the headers shown above the columns are the header row or column you
marked with NVDA's own commands; nothing is shown until you mark one. This uses NVDA's default
way of accessing Excel. If you have turned on "Use UI Automation to access Microsoft Excel
spreadsheet controls when available" in NVDA's Advanced settings, cells are read the ordinary
way instead.

Separately from the flow: on any Excel cell, the header you marked is shown in NVDA's ordinary
braille line, after the cell's coordinates. NVDA speaks this header but did not show it in
braille. It follows NVDA's setting for reporting table headers in Document Formatting, and stays on the
display for every cell in the column rather than being shown once.

## Arrow keys in a table

Inside a browse mode table, such as on a web page or in an email message, the arrow keys move
by table cell:

- Up and down arrow move one row and stay in the same column, so you can read down a column of
  figures without reading every row along the way.
- Left and right arrow move one cell and stay in the same row.
- Home and End move to the first and last cell of the row.

There is nothing to turn on or off. When there is no cell in the direction you pressed, the key
does what it does anywhere else on the page: down arrow on the last row leaves the table, up
arrow on the first row leaves it upward, and right arrow in the last cell of a row continues
into whatever follows the table. No edge is announced.

This works whether or not the table is laid out in columns on the display.

Shift with the arrow keys still selects, and Control with the arrow keys still moves by word
and by paragraph, so a long cell can still be read a word at a time, and Say All still reads
everything. What you give up is moving line by line within a tall cell with the down arrow.

The movement is NVDA's own, the same as Control+Alt+arrow, so merged cells and NVDA's layout
table setting behave exactly as they do with NVDA's table commands, and what is announced in a
new cell is what NVDA normally announces. If "Tables" is turned off in NVDA's Document
Formatting settings, the arrow keys are left alone entirely.

## A table's rows on one line

On a single-row display, such as a Focus 80, there is no room to lay a table out in columns,
and NVDA shows a table one cell at a time. "Table: Toggle showing the table's rows on one line"
shows the whole row the cursor is in on the line instead, with the cells separated by bars:

    AAPL | 310.34 | +1.2 | 48,201,300

Press it with the cursor in a browse mode table. From then on, that table reads this way
whenever you are in it; the rest of the page reads as usual. Press it again in the table, or
anywhere outside a table, to go back to NVDA's normal line. The command has no key by default;
assign one in Input Gestures, under BrlMultiline, or in a key layer.

- The arrow keys move as described above. Up and down arrow change the row, and the display
  keeps its position along the line, so the column you were reading is still under your
  fingers.
- The line is as long as the row, and the panning keys move across it. Panning past the end of
  a row goes to the first cell of the next row, and panning back past the start goes to the
  last cell of the previous row. From the first or last row, panning continues out of the table.
- The cursor sits at the start of the cell you are in. A routing key over another cell moves you
  there; over the cell you are in, it activates it.
- An empty cell keeps its place between two bars, so you can count columns.
- If you have saved a layout for the table, the line shows the columns the layout shows, in its
  order.

This works in tables NVDA reads in browse mode. List views and Excel worksheets are not
affected.

## Using two displays at once

The add-on can combine two or three braille displays into one, stacked top to bottom, so that
each display becomes its own set of segments. NVDA still sees a single display, so everything
else works as usual, including the keys on both displays. See
[Step 2](#step-2-two-displays-one-for-reading-and-one-for-keeping-watch) in Getting started for
setup.

A Monarch above a Focus 80 makes a display of 9 rows: the Monarch's 8, then the Focus 80 as
the ninth. Each display is divided into segments by its own settings, the same ones it uses on
its own, so there is usually nothing more to set up. To change them, open the BrlMultiline
settings while the combined display is connected, and use the "Segment settings for" box to
choose which display you are changing.

Details:

- The displays don't have to be the same width. The combined display is as wide as the widest
  one, and cells past a narrower display's edge are left blank.
- One display per driver. Two displays of the same make can't be told apart, so only one of each
  can be used.
- No segment spans two displays, so a line of braille never continues from the bottom of one
  display onto the next.
- Each display's panning keys pan that display: the segment following the focus if that display
  has it, otherwise that display's first segment. So the Monarch's keys move what is on the
  Monarch while the Focus 80 keeps following the focus.
- Flash messages follow the focus by default. Set "Segment that flash messages appear in" to keep
  them off the display you are reading. A message changes only the segment it appears in, and a
  display it isn't on is not written to.
- A display that is off when you start is left out, and the others are used. When you turn it on
  or bring it back in range, it joins in the position you chose within a few seconds.
- A display that disconnects is dropped, and the rest carry on. The combined display shrinks to
  what is left, and the focus moves onto a display you still have. Anything pinned to the
  display that left moves to a free segment if there is one. If the only segment left is the one
  following the focus, the pin is released.
- While the combined display is in use, the list in "BrlMultiline displays" shows the status of
  each chosen display: in use, not connected, or not responding.
- NVDA's automatic braille display detection is off while the combined display is selected,
  since the displays have already been chosen.
- Settings that belong to one of the displays, such as dot firmness or a Focus's wiz wheel
  action, appear in NVDA's Braille settings labeled with the display they belong to. Changing
  one changes that display's own setting, so it is still there when you use the display on its
  own.
- A display driver's own commands still work on their default keys, but don't appear in Input
  Gestures while the combined display is in use, so they can't be reassigned there. Only three
  drivers are affected: Alva ("Toggles HID keyboard simulation"), HandyTech ("Toggle braille
  input"), and Eurobraille ("Toggle HID keyboard simulation"). A HandyTech used as part of the
  combined display keeps its braille input toggle on Space with dots 1, 3, and 4; you just can't
  change it. Focus and Monarch displays are not affected.
- The add-on has to be enabled. If it is disabled while the combined display is still selected,
  NVDA writes across the full width of every row, and anything past a narrower display's edge is
  lost.
- The list of combined displays is not part of a configuration profile. It describes your
  hardware, so it is stored once and applies in every profile.

## Drawings and charts

Some braille displays can raise pins one at a time rather than only whole braille cells. On one
of those, this add-on can put a tactile image on the display: a chart drawn from cells you have
selected in a spreadsheet, or a picture copied off the screen. The image and your braille line
share the display, with no separate mode to switch into, and pressing a routing key on the image
tells you what is under your finger.

This is the newest part of the add-on and has had the least testing on hardware. Treat it as
experimental. The sections below describe what it does well and what it doesn't.

### A display that can draw

The add-on includes its own driver for the Humanware Monarch, **"BrlMultiline: Humanware
Monarch (pin mode)"**. Choose it in NVDA's Braille settings to use any of the features in this
section. NVDA's built-in Monarch support writes braille cells, which reach only about half the
display's pins and leave a blank column between each pair of cells. This driver writes the whole
96 by 40 pin surface at once, which is what makes drawing possible.

The driver has other benefits, even if you never draw anything:

- **A choice of braille rows.** A "Braille rows" setting appears with the driver's other
  settings in NVDA's Braille settings: 8 rows with a blank row between them (the default, and
  what the Monarch normally gives you), or 10 rows without. All eight dots are shown either way,
  so the cursor and eight-dot braille read correctly in both.
- **Touch at pin resolution.** The Monarch reports which pin a finger touched, and NVDA
  discards that. This driver keeps it, which lets you point at part of a drawing.
- **Bluetooth that recovers from a dropout**, the same recovery the combined display uses.
- **Separate names for keys.** NVDA calls both of the Monarch's d-pads `dpadUp`, `dpadDown`, and
  so on, and calls its zoom keys `brailleUsage544` and `brailleUsage545`. This driver adds
  `leftDpadUp`, `rightDpadUp`, and so on for the two pads, and `zoomIn` and `zoomOut` for the
  zoom keys. NVDA's names still work, so both pads are still the arrow keys until you assign one
  of them to something else. When you press a pad in Input Gestures, NVDA asks which name to
  use: choose the left or right name to assign that pad alone, or plain `dpadUp` to assign both.

The add-on doesn't check for a Monarch specifically. It asks the braille display driver whether
it can raise individual pins, so another display whose driver offers this would work with no
changes here. Today the add-on's Monarch driver is the only one that does. On a display that
can't draw, these commands say so rather than doing nothing.

### Charting a spreadsheet selection

**Graphics: Chart the selected cells** draws your spreadsheet selection as a tactile chart.
Excel is supported today.

Four kinds of chart can be drawn:

- A **bar chart**: one column of numbers, as bars standing on a baseline.
- A **line chart**: up to four columns over a shared period, each with its own texture: solid,
  dashed, dotted, and dash-dot. The textures are announced in speech instead of drawn as a key,
  since a key would take up a quarter of the drawing. With three or four lines, the first line
  is drawn two pins thick so it stays easy to follow where lines cross.
- **Open, high, low, close bars**: four columns drawn as price bars.
- **Candlesticks**: the same four columns drawn as candlesticks.

**It asks which kind.** Four columns of numbers with dates down the side could be four separate
measurements or one stock's trading prices, and the cells alone don't say which. So a "Which
chart?" dialog offers the kinds that can be drawn from your selection, with your last choice
selected. If only one kind fits, it is drawn without asking.

A chart is laid out for the space available rather than shrunk to fit, so the whole-chart view
is the natural starting point. Magnifying a chart lays it out again with fewer bars, each wide
enough to identify, rather than enlarging the dots already drawn.

A zoomed line chart spaces its points a whole number of pins apart: one, two, or three. Forty
points spread across 96 pins would sit 2.4 pins apart, and the uneven gaps feel like a wobble,
so the view is widened or narrowed slightly until the spacing is even. The whole-chart view is
never adjusted, since it has to show every point. The chart's name says how points map to pins:
"2.6 points per pin" when some share a column, "1 pin per point" when each has its own.

**Graphics: Zoom a chart to one pin per point** goes straight to the view where every point has
its own pin column, centered on what you are reading. With 250 points on a 96-pin display, that
is 2.6 times, which doubling alone can't reach. Magnify and shrink then go to the doublings on
either side.

**Graphics: Show the next set of lines on a chart** and **Graphics: Show the previous set of
lines on a chart** step through the chart one line at a time, and then, with three or more
lines, the first line paired with each of the others. The zoom and your position are kept, each
line keeps its texture, and the hidden lines still set the scale, so a line shown alone is at
the same height it had with the others.

Pressing a routing key on a line chart reports any line within two columns and three rows of
the press, about the width of a fingertip. Every line in that area is read, nearest first, at
the point under your finger: "Close 96.3, Lower 98.5, day 149." Away from all lines, it reads
the height as a value instead.

If a chart can't be drawn, you are told why: not a spreadsheet, nothing selected, no numbers in
the selection, a value that isn't a number, more bars or periods than the display can hold,
more series than can be told apart, or four columns that aren't prices.

### Drawing a picture off the screen

**Graphics: Draw the picture here** copies whatever NVDA's navigator object is on from the
screen and puts it on the pins. On a web page, that is the graphic NVDA just announced: arrow to
it and press this command.

It works on anything, not just objects NVDA calls a graphic. A diagram in a canvas, a map, a
floor plan, or a chart published as an image may all be worth exploring, and many of them don't
report a useful role.

Expect good results for line art, logos, diagrams, and maps; fair results for high-contrast
photos; and poor results for everything else. It draws what is in the picture without deciding
what matters, so a photo of a person in front of a bookcase comes out as a person and a
bookcase.

**Graphics: Change how a picture is drawn** cycles through three styles and announces which one
you are on:

- **Outlines**: the edges in the picture, thinned to one dot. A thick stroke has two edges, an
  outer and an inner, so it shows as a double line.
- **Brightness**: the dark parts of the picture raised. This draws the ink itself rather than
  its edges, so try it on a shape whose inside matters.
- **Brightness reversed**: the light parts raised instead. Try it on anything drawn light on
  dark.

There's no way to know ahead of time which style works best for a picture, so try each one.
Changing styles redraws the image already captured rather than copying the screen again, so the
page changing underneath doesn't change the picture you are reading.

If a picture can't be drawn, you are told why: the screen curtain is on, there is nothing there,
it isn't visible on screen, it needs to be scrolled into view, it is too small to be a picture,
or it couldn't be copied from the screen.

### Exploring a drawing

Press the routing key where your finger already is. The display reports which pin you touched,
and the add-on tells you what is there. There is no second command and nothing to hold.

- On a **chart**, it names what is under your finger: which bar and its value, which series at
  which date, or a period's open, high, low, and close.
- On a **picture**, it tells you where you are as a percentage across and down.
- Anywhere else on an image, it says "raised at" or "blank at" with the position in the drawing,
  counted from its top left corner.

It reports the **nearest raised dot** within about half a braille line, rather than the exact pin
you pressed. A fingertip is much wider than a pin, and a line one dot wide often reads as blank
if the display's idea of the contact point lands a pin to either side. The search distance
follows the magnification: it grows when a drawing is compressed and shrinks as you magnify.

A press outside the image says "outside the picture," so it isn't confused with an empty part
of the drawing.

### Magnifying and moving

**Graphics: Magnify the drawing** and **Graphics: Shrink the drawing** zoom in and out in
doublings, and **Graphics: Move the drawing view** up, down, left, and right moves the view once
you have zoomed in.

The most zoomed-out view shows **all** of the drawing, compressed as much as needed. That is the
view to start with: the overall shape first, then the detail. Nothing is off the edge in that
view, so moving the view reports that the whole drawing is showing. A drawing smaller than the
display is shown at its own size rather than enlarged, so magnification only happens when you
ask for it.

Above that are five doublings, stopping where one dot of the original would be wider than a
braille cell. A picture can go one step past the point where one pin matches one captured
pixel. No new detail appears, but the shape gets bigger and easier to feel.

When a picture is compressed, **a pin is raised if any dot it covers is raised**. This keeps
thin lines from breaking up or disappearing, at the cost of making busy areas solid. You can
feel that an area is busy, but you can't feel a line that isn't there.

**Your position is given as a percentage of how far you can move**, with 0 at one edge and 100
at the other, and the edges named: "25 across, 51 down," then "left edge, 51 down." A direction
you can't move in is left out.

If a zoom doesn't change anything, you are told why: the most zoomed-out view, no more detail to
show, as large as the pins can show, or this part won't draw. For a picture, it also gives the
size it was captured at. For example, a toolbar that looks wide on screen may be only 160 pixels
wide, already less than two pixels per pin on a 96-pin display.

Two messages may be announced each time you move the view:

- **"Only background here"**: you have zoomed into an empty part of the picture. It is drawn
  blank rather than refused, so you can move across an empty middle to reach the far side.
- **"Too detailed to draw whole; magnify to read it"**: the picture has more edges than the pins
  can show, and what is drawn would feel like an even texture. Zoom in to read it.

### The braille line beside it

**Graphics: Show or hide the braille line beside the drawing** decides how much of the display
the image gets. On a Monarch at the default 8 rows, an image is 96 by 35 pins with a braille line
beside it, or 96 by 40 without. The switch keeps your magnification and position.

Giving the image the whole display has a cost on a single display: NVDA's focus is not shown
while the image is up. You are told when this happens, and the same command undoes it. With two
displays combined, you can put the focus on the other display and give the whole Monarch to the
drawing.

### While a drawing is up

A drawing takes priority over everything else that wants those rows, including the flow. The
flow steps aside while a drawing is showing and comes back by itself when the drawing is taken
down.

**Graphics: Show or hide a drawing on the display** takes the drawing off, and puts it back.
Pressed with nothing to show, it shows a test pattern sized to the display, which is how to
check that a display can draw at all.

**Graphics: Reports the drawing on the display** tells you what is showing, how far it is
magnified, and where you are in it, without changing anything.

### Limitations of drawings

- An image and your braille line are drawn together on one surface. There is no mode to switch
  into, and the cursor keeps working in the braille line beside the drawing.
- The whole Monarch surface is 3,840 pins, far fewer than the pixels in most pictures, so a
  picture loses most of its detail before anything else is done to it. That is why line art
  works and photos mostly don't.
- **A window in the way can't be detected.** A picture is copied from the screen, so if another
  window was covering it, that window is what gets drawn. If a drawing makes no sense, check
  that nothing is on top of it.
- The screen curtain and a drawing can't both be on, since there is nothing to copy with the
  curtain on.
- Touch precision is based on a small number of tests on hardware, not a calibration. If
  pointing at a bar consistently names the wrong one, please report it.
- No image library is needed. All image processing is done in plain Python inside the add-on,
  so there is nothing extra to install.

## Commands

Every command is in the **BrlMultiline** category in NVDA menu, Preferences, Input Gestures.
Most have no key assigned: choose the ones you want and assign keys, ideally on the display
itself. The drawing commands are the exception, and come with keys on the Monarch's keyboard.

Command names start with what they act on, such as Graphics, Table, Navigation, Monitoring,
Misc, or Debug, so related commands are grouped together. Include the prefix when searching in
Input Gestures.

### General

**Opens the BrlMultiline settings.** Goes straight to the BrlMultiline settings panel.

**Turns the BrlMultiline segment layout on or off.** Turns the division of the display into
segments off and on again, without opening settings.

**Misc: Reports the BrlMultiline segment layout.** Says how many segments there are, which one is
following the focus, and what is on the display. Useful for confirming a layout took effect, and
the first thing to check when the display isn't showing what you expect.

**Moves the focus onto another of the combined braille displays.** Only does something when
several displays are combined. With two displays, each press moves the segment that follows the
focus to the other display. With more than two, it opens a list to choose from. It keeps the
focus segment at the same position down its display, so on two displays divided the same way,
pressing it twice puts you back where you started. The choice is saved in the current profile,
so it survives a restart.

If something is pinned where the focus is going, the two trade places: the pinned object moves
to the segment the focus is leaving, and moving the focus back trades them back. You are told
when this happens. If there is nowhere to put the pinned object, or more than one is in the way,
you are asked which to keep, and you can cancel. To keep more pins than there is room for,
divide a display into more segments first.

### Panning segments

**Navigation: Scrolls segment N of the first / second / third display forward / back.** Pans a
segment identified by its display and its position on that display, counting from 0. These are
the recommended panning commands: a key assigned to one keeps panning the same segment even if
you change the layout. With one display connected, "the first display" is that display, so
these keep working when you unplug the second one.

**Navigation: Scrolls segment N forward / back.** Pans one segment, numbered across the whole
display. These can reach segments the display-specific commands can't, but the numbering changes
whenever the layout does.

A segment that isn't following the focus is panned within the content it already has. It won't
move to the next or previous line of a document, because that would move the caret, and the
focus with it, in something you are only reading.

### Keeping an object on the display

**Monitoring: Shows the navigator object in segment N of the first / second / third display.**
Pins the current navigator object to a segment identified by its display, so it stays there
while you work elsewhere. Like the panning commands above, a key assigned to one keeps pointing
at the same segment when the layout changes.

**Monitoring: Stops showing an object in segment N of the first / second / third display.**
Unpins and clears that segment.

**Shows the navigator object in segment N.** Pins the current navigator object to a segment
numbered across the whole display. You can't pin an object to the segment that follows the
focus.

**Stops showing an object in segment N.** Unpins and clears that segment.

### Reading as a flow

**Toggle reading the whole display as one flowing document.** Turns the flow on or off, and saves
the choice in the current profile. It is the same switch as the one in settings.

**Flow: Toggles showing every line on one row, unwrapped, and panning across it.** Turns
unwrapped lines on or off for now, without changing the setting. No key by default.

**Flow: Pans unwrapped lines right by the width of the display** and **Flow: Pans unwrapped lines
left by the width of the display.** Move every line across together without moving the caret, and
say which column is now at the left. On the Monarch, these are the zoom keys while lines are
unwrapped; see [Layered keys](#layered-keys).

### Tables

These act on the table your cursor is in. See [Tables in a flow](#tables-in-a-flow) for details.

**Table: Toggle table columns on or off.** Lays the table out in columns on the display, or
stops.

**Table: Next columns** and **Table: Previous columns.** Move across a table too wide to show at
once, without moving the cursor.

**Table: Show/Hide the current column.** Hides the column your cursor is in, or shows it again.

**Table: Changes how the column you are in is trimmed when it does not fit.** Cycles that column
between wrapped, cut keeping the start, and cut keeping the end.

**Table: Open table layout designer for the current table.** Opens the dialog where each column
can be named, sized, trimmed, and hidden.

**Table: Undo custom table layout.** Restores the table's original layout, without changing
what was saved for it.

**Table: Save table layout.** Saves this arrangement for this table, so it is laid out the same
way the next time you are in it.

**Table: Delete saved table layout.** Deletes the saved layout for this table.

**Table: Manage saved table layouts.** Lists every saved layout, to rename, delete, point at the
table you are in, or change how strictly its address matches.

**Table: Toggle showing the table's rows on one line.** See
[A table's rows on one line](#a-tables-rows-on-one-line).

### Drawings and charts

These need a display that can raise pins one at a time, and they are the only commands that come
with keys already assigned. The keys are on the Monarch's keyboard and come with the add-on's
Monarch driver; you can reassign them like any other command. Everything here is also announced
in speech, so you know when something changed.

See [Drawings and charts](#drawings-and-charts) for what each command does.

Only the commands that put a drawing up have keys of their own, so the Monarch's keyboard is
free when no drawing is showing:

- **Graphics: Draw the picture here**: Space with dots 1, 4, and 7.
- **Graphics: Chart the selected cells**: Space with dots 5 and 7.
- **Graphics: Show or hide a drawing on the display**: Space with dots 7 and 8.

Commands used while a drawing is up are in the Monarch's graphics, chart, and picture layers,
which turn on by themselves with the drawing; see [Layered keys](#layered-keys). With the layer
on:

- **Graphics: Magnify the drawing**: the zoom in key, or Space with dot 8.
- **Graphics: Shrink the drawing**: the zoom out key, or Space with dot 7.
- **Graphics: Move the drawing view up, down, left and right**: the left d-pad, or Space with
  dots 1, 4, 3, or 6, each with dot 7.
- **Graphics: Show or hide the braille line beside the drawing**: Space with dots 2 and 7.
- **Graphics: Zoom a chart to one pin per point**: o, on a chart.
- **Graphics: Show the next set of lines on a chart**: v, on a chart.
- **Graphics: Show the previous set of lines on a chart**: v with dot 7, on a chart.
- **Graphics: Draw the picture as outlines**: o, on a picture.
- **Graphics: Draw the picture by brightness**: b, on a picture.
- **Graphics: Draw the picture by brightness, reversed**: r, on a picture.
- **Graphics: Change how a picture is drawn**: Space with dots 1, 4, and 8, on a picture. Cycles
  through the three styles.

These two have no key:

- **Graphics: Reports the drawing on the display**: tells you what is showing, how far it is
  magnified, and where you are in it, without changing anything.
- **Graphics: Show the glyph catalogue**: draws every role and state shape with the braille it
  replaces beside it, for learning the shapes. See "Draw roles and states as shapes instead of
  words" in [Dividing a single display into segments](#dividing-a-single-display-into-segments).

### Layered keys

Experimental. A layer gives a display's keys, or the keyboard's, different commands while it is
on. For example, with a drawing up, the Monarch's left d-pad can move the drawing while the
right d-pad stays the arrow keys. The keyboard and each display have their own layers, and
turning one on doesn't affect the others. A key the layer doesn't use does what it always does,
so you can still type and read inside a layer.

- **Layered keys: Turns the layer on or off for the device it is pressed on**: Space with dots 1,
  2, 3, and 7 on the Monarch; NVDA+Control+Shift+L on the keyboard. With a drawing up, it turns on
  the chart, picture, or graphics layer, whichever fits the drawing; otherwise, the default layer.
  The default layer is one-shot: it turns off after the next key.
- **Layered keys: Chooses which layer is on for the device it is pressed on**: no key by default.
- **Layered keys: Reports which layers are on**: no key by default.
- **Layered keys: Turns every layer off**: no key by default.
- **Layered keys: Opens the layered keys dialog**: no key by default.
- **Layered keys: Turns the layer on or off for the first, second or third display**: no key by
  default. For turning on a display's layer from the keyboard.

Space with z, or Escape, turns a layer off.

The Monarch and the keyboard come with these layers:

- **Monarch, default layer.** Press the layer key, then the left d-pad: up reads the current
  line, down starts Say All, left reports the focus, right reports the window title.
- **Monarch, graphics layer.** Turns on by itself when a drawing goes up, and off when it comes
  down. The left d-pad moves the drawing, and the zoom keys magnify and shrink it. Space with dot
  7 and Space with dot 8 also shrink and magnify, and Space with dots 2 and 7 shows or hides the
  braille line. The right d-pad stays the arrow keys. If you turn the layer off with the layer
  key, it stays off until the drawing is taken down and put up again.
- **Monarch, chart layer.** Turns on instead of the graphics layer when the drawing is a chart,
  and includes all the graphics layer keys. Adds o to zoom to one pin per point, and v and v with
  dot 7 for the next and previous set of lines. Other letters still type.
- **Monarch, picture layer.** The same, for a picture from the screen. Adds o for outlines, b for
  brightness, r for brightness reversed, and Space with dots 1, 4, and 8 to cycle through them.
- **Monarch, table layer.** With the caret in a table on a web page, or in a table laid out in
  columns, press the layer key to turn it on. The left d-pad moves by table cell, and the zoom
  keys go to the next and previous page of columns. It stays on until you turn it off.
- **Monarch, unwrapped lines layer.** Turns on by itself while lines are shown unwrapped, and off
  when they wrap again. The zoom keys pan across the lines; every other key works as usual. If you
  turn it off with the layer key, it stays off until lines wrap again.
- **Keyboard, default layer.** Press the layer key, then numpad 5 reads the current line.
- **Keyboard, graphics layer.** The numpad arrows move the Monarch's drawing, numpad plus and
  minus magnify and shrink it, and numpad 5 reads the current line. Turn it on with the layer key.
  It doesn't turn on by itself, so the numpad stays the review cursor unless you change that in
  Properties.

Other displays start with an empty default layer.

Once you save a device's layers in the dialog, your saved layers replace the ones it came with,
as a whole. A later version of the add-on that improves the built-in layers won't change what you
changed. The exception is keys the add-on moved: when the graphics keys were moved out of the
global commands on September 15, 2026, layers saved before then were given the chart and picture
layers and the graphics layer's new key combinations, once, unless you already had your own
layer for that kind of drawing or had used that key for something else. Layers saved before
unwrapped lines existed are given the unwrapped lines layer the same way. If you remove one of
these and save, it stays removed. Use "Reset to factory defaults" in the dialog to restore
everything the add-on includes, which also removes your own changes for that device.

#### The layered keys dialog

Open it from NVDA menu, Preferences, BrlMultiline layered keys; from the Layered keys button in
the BrlMultiline settings panel; or with its command. It works like NVDA's Input Gestures
dialog: a filter, a tree of categories and commands with the keys under each, and Add, Change,
and Remove buttons. Above the tree, choose the device and the layer to edit. Changes are saved in
the configuration profile in use when you press OK.

- **Add** waits for a key on the device being edited. A key on a different display is refused
  with a message, and Escape on the keyboard stops waiting.
- If a key already does something else in the layer, you are asked before it is moved.
- The key that turns layers on and off, and a layer's exit keys, can't be assigned to a command.
- When you assign a keyboard key to a BrlMultiline command or a braille command, you are asked
  which display it acts on, so a numpad key can move the Monarch's drawing.
- **Does nothing in this layer**, under BrlMultiline, blocks a key while the layer is on.
- **New layer**, **Rename**, **Delete layer**, and **Properties** manage the layers. Properties
  sets what the layer is for, whether it turns on by itself when that thing appears (drawings
  only), whether it stays on or is one-shot, its exit keys, and which layer it gets keys from.
  Keys that come from another layer are listed under their commands as "from Graphics layer";
  Remove on one of them makes it do nothing in this layer, without removing it from the other.
- **Only show commands with keys in this layer** turns the tree into a list of what the layer
  does.
- **Clear this layer** removes its keys. **Reset to factory defaults** restores the layers the
  device came with.
- Commands are gathered from where you were when you opened the dialog, the same way Input
  Gestures does it, so open it from a web page to assign browse mode commands. A key for a
  command that isn't available from there is listed under "Unavailable from here," so you can
  still remove it.

### Diagnostics

**Debug: Reports what the flow on the display has cost, and copies the detail.** Says how long the
slowest part of reading took, and how often the flow ran out of time and showed less than the
display could hold, and writes the details to the log. If a page feels slow, or you notice the
marker meaning "there is more not yet read," press this and send the log with your report.

**Debug: Copies what a flowed reading of this object would show to the clipboard.** Reads the
navigator object as a flow and copies the result to the clipboard, without moving anything or
changing the display. Useful for reporting how the flow presented something.

## Notes and limitations

The number of segments is limited to 8, the default row count of a Monarch.

Cursor routing keys work per segment: pressing a routing key over a segment routes within that
segment's own content.

Only the segment that follows the focus shows a cursor, because NVDA tracks a single cursor
position.

A single-row display can't be made to act like a multi-row one. Telling NVDA that a Focus 80 has
two rows of 40 makes NVDA write only the first row and leave the rest blank, because it shapes
its output to the hardware's physical row count. Splitting a single row into side-by-side
segments, as this add-on does, is a different arrangement that does work.

## For add-on developers: making your application's content flow

The flow already knows how to read web pages, documents, editors, lists, menus, and tree views.
Some applications present content in a way it can't figure out on its own. A chat history is the
common case: the messages look like a list to a person, but the accessibility tree doesn't
connect them the way a list is connected.

An NVDA app module can tell BrlMultiline how to read such content by setting a few attributes on
an overlay class. There is nothing to import and nothing to register. BrlMultiline looks for these
names on the objects it reads; nothing in NVDA reads them. So your app module works exactly the
same whether BrlMultiline is installed, disabled, or an older version that doesn't know about
them.

### The attributes

On the class for each item (for example, one message):

1. `brlMultilineFlowRun = True`. Declares that this object is one item in a run that can be read
   as a flow. This is the only required attribute.
2. `brlMultilineFlowNext(self)`. Optional. Returns the next item in the run, or `None` at the end.
3. `brlMultilineFlowPrevious(self)`. Optional. Returns the previous item, or `None` at the start.
4. `brlMultilineFlowAdmits(self, other)`. Optional and rarely needed. Returns whether `other`
   belongs to the same run. By default, any object that also has `brlMultilineFlowRun` belongs.
   Only supply this if two different runs can sit next to each other.

If you don't supply `brlMultilineFlowNext` and `brlMultilineFlowPrevious`, BrlMultiline assumes
the items are siblings and steps with `next` and `previous`. If a step lands on something outside
the run, or your method raises an exception, the run simply ends there. You may get a shorter run
than expected, but never the wrong content.

On the class for the container that holds the run (for example, the message list), which is
needed for pinning:

1. `brlMultilineFlowRunContainer = True`. Declares that the run is somewhere inside this object.
   When a user pins the container, BrlMultiline reads the run instead of just the container.
2. `brlMultilineFlowRunStart(self)`. Returns the item to start reading at, or `None`. Recommended.
   Without it, BrlMultiline searches inside the container, within a limit, and starts at the first
   item it finds. That is right for a list, but for a chat you probably want the newest message.

When the user is already on an item in the run, reading starts there, no matter what
`brlMultilineFlowRunStart` returns. The container's answer is only used when nothing else says
where to start, such as when the user pins the list from somewhere else.

### Example: Microsoft Teams

The [Microsoft Teams add-on](https://github.com/travisroth/ms-teams) is a working example. In
Teams' chat history, each message has the role GROUPING and sits in its own wrapper element, next
to a timestamp and an unnamed element. No two messages are siblings, so stepping with `next`
doesn't reach the next message, and NVDA's `simpleNext` leaves the history entirely and lands on
the compose box. Only the app module knows how to get from one message to the next.

Here is a simplified version of what it adds. The helper functions `_isMessageObject`,
`_isMessageListObject`, and `_findMessageWithin` identify messages and the message list by their
DOM ids, and look a few levels down inside a wrapper for the message it holds. See
`addon/appModules/ms-teams.py` in that repository for the full code.

```python
import appModuleHandler
import controlTypes
from NVDAObjects import NVDAObject

# Keep each step cheap: these run while the user is panning.
_SIBLING_LIMIT = 12
_DESCENT_DEPTH = 3


class TeamsMessage(NVDAObject):
	"""One message in the chat history."""

	role = controlTypes.Role.LISTITEM

	# Declare that messages form a run BrlMultiline can flow across.
	brlMultilineFlowRun = True

	def _flowStep(self, forward: bool):
		# Messages are not siblings: climb to this message's wrapper, move to the
		# next or previous wrapper, and look inside it for a message. Skip wrappers
		# that hold no message, up to a limit.
		wrapper = self.parent
		for _ in range(_SIBLING_LIMIT):
			if wrapper is None:
				return None
			wrapper = wrapper.next if forward else wrapper.previous
			message = _findMessageWithin(wrapper, _DESCENT_DEPTH)
			if message is not None and message != self:
				return message
		return None

	def brlMultilineFlowNext(self):
		return self._flowStep(forward=True)

	def brlMultilineFlowPrevious(self):
		return self._flowStep(forward=False)


class TeamsMessageList(NVDAObject):
	"""The container holding the whole chat history."""

	# Declare that a run is inside, so the list can be pinned.
	brlMultilineFlowRunContainer = True

	def brlMultilineFlowRunStart(self):
		# Start at the newest message: walk back from the last wrapper.
		wrapper = self.lastChild
		for _ in range(_SIBLING_LIMIT):
			if wrapper is None:
				return None
			message = _findMessageWithin(wrapper, _DESCENT_DEPTH)
			if message is not None:
				return message
			wrapper = wrapper.previous
		return None


class AppModule(appModuleHandler.AppModule):
	def chooseNVDAObjectOverlayClasses(self, obj, clsList):
		if _isMessageObject(obj):
			clsList.insert(0, TeamsMessage)
		elif _isMessageListObject(obj):
			clsList.insert(0, TeamsMessageList)
```

The real add-on also wraps each property access in `try` and `except`, since a COM call into
Teams can fail at any time, and logs the failure instead of letting it escape.

### Tips

- Derive your overlay classes from `NVDAObject`, or from another NVDA class. NVDA only turns
  `_get_` methods into properties on classes built on its own base, and an overlay class with no
  NVDA base is silently ignored.
- Keep each step fast and bounded. `brlMultilineFlowNext` and `brlMultilineFlowPrevious` are
  called while the user pans, so they should never scan the whole document. Set a limit on how
  many siblings and how many levels deep you search, as the Teams example does.
- Never move the focus, change the selection, or otherwise change the application's state in
  these methods. BrlMultiline calls them just to read.
- Give items a sensible role and name. The flow shows each item the way NVDA would show it in
  braille, so an item with the right role and a good name reads well. In Teams, changing messages
  from GROUPING to LISTITEM also removed the role text NVDA put in front of every message.
- Content that is still being added to, such as a chat, is handled for you. When the user is at
  the end of a pinned run, BrlMultiline asks for the next item again as time passes, so new
  messages appear without any event from your code.
- To see what BrlMultiline makes of your objects, move the navigator object to one and use
  "Debug: Copies what a flowed reading of this object would show to the clipboard."

BrlMultiline also has a `register` function for code that wants to supply a complete adapter for
controls it doesn't own. That requires importing from this add-on, which creates a load-order
dependency, so the attributes above are the recommended approach for app modules.

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

The unit tests run against stubs, which is fast and needs no NVDA, but can't notice when NVDA's
own API changes underneath the add-on. `tests/nvdareal.py` covers that: it starts a real NVDA
from a source checkout, imports the add-on into it the way NVDA does at runtime, and checks that
what the add-on calls is still there and still behaves as expected. It is safe to run while NVDA
is running, touches no configuration or hardware, and needs NVDA's own Python interpreter rather
than this project's:

```bash
C:/code/nvda/.venv/Scripts/python.exe tests/nvdareal.py
```

Set `BRLMULTILINE_NVDA` if the NVDA source is somewhere other than `C:\code\nvda`. Add
`--displays` to see what braille hardware NVDA can find, or `--log` to read the end of the log
NVDA writes when run from source. Read the file's docstring before relying on its results; it
explains what it does and does not prove.

Design documentation, including notes on the parts of NVDA this add-on depends on, is in
`docs/design/`.

## License

GNU General Public License version 2.
