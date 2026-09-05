# Changelog

## 0.1.0, in development

First version built against NVDA 2026.3, replacing an earlier prototype written for
NVDA 2023. 2026.3 is the first release to carry the `braille` package that this add-on is
written against, so until it ships, installing and testing means running an alpha snapshot.

Added:

- Divides a braille display into independent segments, each with its own content and its
  own scrolling. Segments are groups of whole rows on a multi row display, and column
  slices on a single row display.
- Per display configuration: whether to divide the display at all, segment count or
  explicit segment sizes, and which segment follows the focus.
- A switch, per display, for turning the configured layout off and on without disturbing
  it, as a setting and as a command. While it is off the display behaves as it does without
  the add-on, and objects cannot be pinned. A view or panel activated by code is unaffected,
  since that is a claim about what the display is being used for rather than a preference.
- Reversed panning keys, stored per display. NVDA has no such setting of its own.
- A command that moves the focus onto another of several displays driven as one: a toggle
  when there are two, and a list to choose from when there are more. It keeps how far down
  its display the focus segment was, and stores the answer in the profile in force. A pinned
  object sitting where the focus is going trades places with it rather than being released,
  since pressing a key to move the focus is a decision about the focus and not about the
  pin. The segment the focus is leaving counts as somewhere to put it even when the flow
  band has claimed it, since the band follows the focus and gives those rows back, and a pin
  carried onto a segment the rebuilding dissolves is re-homed rather than released. Where
  there is nowhere to put what is displaced, or more than one thing is, the reader chooses
  what to keep or cancels the move. What became of a carried pin is reported after the move
  rather than promised before it, including when it lands somewhere with too few rows to
  read it as a flow — which a run of objects no longer needs, since one row of a chat, a
  list or a menu is one message, one item, one entry, with panning moving to the next. A
  document or a table still needs two.
- Per segment panning commands, unbound by default.
- Pinning the navigator object to a segment, so it stays visible while the focus moves. A
  pin survives a layout change as long as its segment does. A pinned segment can be panned
  line by line through the object, and does so without moving the caret, the browse mode
  cursor or the focus. A browse mode document is pinned through its tree interceptor, so a
  pinned web page covers the page rather than the one element the cursor was in. Pins are
  re-read several times a second, so a pinned status line or clock keeps up, and re-reading
  neither moves the reading position nor disturbs the window the user panned to.
- Optionally filling the segments around the focus segment with the document lines above
  and below the caret.

- Reading a document as a flow, on a band of the display: a heading followed by what comes
  after it, a paragraph running on across the rows, panning that moves a whole display's
  worth and is reversible, and a cursor move within what is shown moving nothing. Tabbing
  to something already on the display leaves the display where it is; only a jump by
  structure rehomes it. In browse mode, a control's prompt is placed above it, a field is
  separated from the next prompt, and the real edit control owns the active row so its
  caret and typed value stay live without losing the page around it. Standalone editable
  documents such as Notepad can use the same flow through a separate opt-in setting. Long
  edits keep the caret visible even beyond the renderer's 64-row working chunk. A multi line
  edit you are writing in is read as a document of its own, so the lines around the one you
  are on are on the display; leaving it gives the page back. Such an edit is cut into
  paragraphs rather than into wrapped lines, which is now a setting of its own — on by
  default, and worth turning off to try an editor that reads oddly. Every block is rendered
  by the same unit it was cut by, a wrapped paragraph whole rather than its first line; a
  line that transiently swallowed its successor stops truncating the edit once it reads
  cleanly again; and the reading allowance funds the full re-read a keystroke costs, so
  typing no longer fills the band with the marker meaning there is more unread. A walked
  block whose unit does not start where the walk landed is refused in both directions while
  writing, and a re-read that cannot restore the band's old top row reaches back for
  context above the caret instead of pinning the typed line to the top row. Editors NVDA
  knows are editable but whose roles and states do not say so — Windows 11 Notepad — are
  recognised by NVDA's own behaviour class, and an editable document counts as multi line
  without the state. The band's top row is only trusted when it is not the caret's own
  block, so a rich editor's transient merged answer at a return cannot become the window
  the reader is put back to; and a keystroke into an edit earns a settle pass a moment
  later, so a reader who pauses right after pressing return is not the one who has to
  supply the re-read that heals the display. Quick navigation keys all move
  the display: structural ones ground, and everything else brings what you jumped to into
  view. Off until it is turned on, per display and per configuration profile. Experimental.
- Reading a list, a menu, a tree or a combo box's choices the same way: the items around
  the one you are on, rather than that one alone. It never moves your selection — panning
  reads past where you are, and the cursor stays on the item the arrow keys will act on. A
  routing key does what a click would: it invokes a menu item, chooses a tab, and moves the
  focus to a list or tree item without selecting it. A menu's separators are drawn as a line
  of dots 7 and 8 rather than read out as unavailable commands. Off until it is turned on,
  separately from browse mode. Experimental.

- Tables in columns, with the layout remembered against the table. A command lays the table
  you are in out across the display, another moves through the pages of a wide one, and the
  first column can be repeated on every page so you know whose row you are reading. The page
  you turn to stays while the cell your cursor is in is on it, and the display comes back to
  your cursor when it moves somewhere the page does not show. What each
  column is called is taken from the table itself and can be held on a row above the band.
  Two more commands remember the layout for this table and forget it again: a remembered
  table comes up laid out with no keystroke at all, and turning the columns off leaves the
  memory alone until you come back to it. What is remembered is that this table is read in
  columns; which columns fit and how wide they are is worked out afresh for the display you
  are on. Web pages, File
  Explorer's Details view and Outlook's message list are all read this way.

- The columns of a table can be arranged, from the band or from a dialog. On the band: hide the
  column you are in, cycle it between wrapped, cut keeping the start and cut keeping the end,
  and give the table back as it comes. In the dialog: the columns as a checked list view to
  reorder, where the tick on a line is whether that column is drawn, a name
  of your own for any of them, a floor and a ceiling on its width, which end of its heading
  survives, whether it is drawn without braille capital signs — `AAPL` is six cells in a six
  dot table and `aapl` is four, which on a seven cell column decides whether the value fits —
  where a page of columns begins, and which column is repeated on every page — with
  "the first column shown" as its own answer, since that is what a table does when you have
  named none. Every per-column question also has an option meaning "I have not
  decided", which says both where its answer comes from and what that currently is: the
  cutting follows the "cut table cells that are too long" setting (or this table's own saved
  record, when it has one), and the heading's end follows nothing at all and is called a
  default, since there is no setting for it. What you arrange is what the remember command writes down, and
  the box that remembers it is a state rather than a button: unticking it on a table you had
  remembered deletes what was saved. The dialog opens on NVDA's event loop rather than inside
  the command, which is what stopped NVDA responding the first time it was opened.

- The cursor stays on the row you are on in a list. The live re-read replaces a row's
  region every couple of seconds, and a fresh region does not know it is the one you are on —
  so the cursor quietly disappeared from File Explorer and from the add-on's own dialogs a
  moment after you arrived. Worst on a flat list, where no indent marker is left to say which
  row is which.

- A list whose items have checkboxes flows like any other list. A list view with checkboxes
  reports every item as a checkbox rather than as a list item — that is Windows' answer, not
  NVDA's, and it is the right one for saying "ticked" — and a run found only by what an object
  calls itself found no run at all: the band showed the one item you were on and panning did
  nothing. What holds an object now answers where its own role does not. Noticed in this
  add-on's own table designer, and it applies to File Explorer's checkbox mode too.

- **The arrow keys read a browse mode table by cell.** Inside a table, up and down move a
  row and keep the column, left and right move a cell and keep the row, and home and end go
  to the first and last cell of the row — the way the same keys read a spreadsheet, and
  without a mode to turn on or a chord to hold down. It happens wherever the cursor is in a
  table, whether or not the table is laid out in columns on the display.

  **Nothing is trapped.** A key is only taken when there is a cell to move to: the down
  arrow on the last row leaves the table, the up arrow on the first row leaves it upwards,
  and the right arrow in the last cell of a row walks on into what follows, all exactly as
  they do on any other page. There is no edge message and nothing to switch off to get out.
  Shift and control with the arrows are untouched, so selecting text and moving by word
  inside a long cell work as they always did. The movement, and what is announced on
  arrival, are NVDA's own — the same ones control+alt+arrow uses — and the arrows are left
  alone entirely if you have turned tables off in NVDA's Document Formatting settings.

- **An Excel worksheet can be read in columns**, like a web page's table or File Explorer's
  Details view. A sheet is read by coordinate rather than by walking rows, its extent comes
  from Excel's used range, and its row and column numbers are Excel's own. Headers are the
  ones you mark yourself with NVDA's own header commands; a sheet declares none, so row one is
  read as data until you say otherwise. This needs NVDA's default way of reaching Excel: with
  "use UI Automation to access Microsoft Excel spreadsheet controls when available" turned on,
  a cell is left to NVDA's ordinary reading instead.

- **A cell's header is now shown in braille as well as spoken.** Land on a cell in a sheet
  whose header row you have marked and NVDA says "9/4/2026  A2  Date"; in braille the same
  cell was "9/4/2026  A2", and the only way to find out what the column was called was to
  leave the cell and come back. The header now follows the coordinates on the display, where
  speech puts it, and it obeys the same "report table headers" setting speech obeys — rows,
  columns, both or off. Unlike speech it does not go quiet on the next cell of the column:
  braille says where you are standing, not what has just changed. This is NVDA's ordinary one
  line reading of a cell, not the flow, so it applies whether or not the sheet is laid out in
  columns.

- The Excel support is an **application module**, so it is loaded only while Excel is running
  and nothing about Excel is asked of any other application. It extends NVDA's own Excel
  module rather than replacing it, so everything NVDA does there goes on happening. A sheet
  whose used range is enormous — Excel counts formatting as use, and never forgets it — is
  left to NVDA's ordinary reading rather than measured, since measuring it would stop the
  display for as long as it took.

- An Excel worksheet is **measured a row at a time rather than a cell at a time**, which is
  the difference between a layout arriving and NVDA stopping while it is worked out. Twenty
  one columns of a small sheet took over ten seconds and then failed; the same reading is now
  one request to Excel per row. What a column is called is asked once for the column instead
  of once for every cell of it, and on a sheet where you have marked no headers it is not
  asked of any cell at all.

- When nothing can be read out of a table, the command **says that** rather than saying it
  could not be laid out in columns. They are different things: one is about the display being
  too narrow for the columns, the other is often a moment that has passed and is worth asking
  for again. Reads that NVDA cancels because it has stopped waiting on an application are no
  longer mistaken for empty cells, which is what turned a slow Excel sheet into a sheet that
  looked blank.

- The log's account of a worksheet that would not lay out now says what each of its columns
  holds and where each heading came from, as it already did for a list. It used to say only
  that there was no row in hand. It also carries the steps the layout was made with — what was
  measured, which columns name themselves, whether a row was spent on a header — which used to
  be written down only when a layout failed outright.

- **A table laid out from its header row keeps its header row.** Asking for the columns while
  the cursor was on the headings themselves left the display with no heading row at all: a
  header cell has no header above it, and its answer was taken for the whole column. On a
  spreadsheet that is where the cursor usually is when you ask.

- **"Expand to computer braille for the word at the cursor" applies to the row you are on,
  and to no other.** NVDA's setting is applied wherever a braille region has a cursor, and
  every row of a flow reads a place of its own — so the band asked for computer braille on
  every one of them, and each line came out with its first word written out uncontracted. The
  cursor was being taken away afterwards, which is one translation too late. Every row but
  yours is now in whatever table and contraction you chose. The same fix applies to the
  document lines shown around the caret in other segments, where only the caret's own line
  expands.

- **"No column repeated" is one of the answers in the table designer.** Whether a column is
  drawn again at the left of every page was a setting for all your tables at once, while which
  column it is was decided per table — so a table whose rows should start at the left meant
  turning the repeat off everywhere. It is now the same question, asked once, and answered for
  the table in front of you: the first column shown, none at all, or one you name. Saving the
  arrangement keeps it with that table, and every other table goes on following the setting.

- **A worksheet is read a row at a time everywhere, not only while its columns are being
  measured.** The rows on the display were still built one cell at a time — a coordinate
  lookup and an object built with its overlay classes chosen, per column, per row — and the
  refresh did it again. The cell object behind a value is now fetched only when a routing key
  lands on that one cell.

- **What the columns of a sheet are called is asked once of the sheet** rather than of every
  cell. On a sheet nobody has marked up it costs no reads at all, where it used to build up to
  three cell objects per column before a row you can feel had been read; on a marked one it is
  a single read of the heading row. The record of what you marked is built afresh each time
  rather than read from the one NVDA is keeping, so "this sheet has no headings" is only said
  after a search that reached the end — and a sheet whose record NVDA left half built is mended
  rather than worked around.

- **The column you are standing in is drawn even when it is empty.** On a spreadsheet the
  column beside the data is where you go to write the next one, and an empty column was left
  out of the layout — so your cursor went with it, there was nothing to route into, and a blank
  sheet could not be laid out at all. A list is unchanged: a column of icons that reads as
  nothing still costs no cells.

- **A worksheet that will not say how far it goes is left to NVDA** rather than presented as a
  table one cell bigger than wherever you are standing.

- **A batch read that came back short is read again cell by cell.** Excel's own fetch stops at
  the first cell it cannot reach, and the rest used to be filled in with blanks — so columns
  with values in them measured as columns holding nothing.

- Merged cells read as their content under every column they span, rather than as one value
  and then blanks.

- Rows appearing in a sheet while you sit still — a formula filling down, a query refreshing —
  bring the layout up to date. The check compared columns and never rows, so the new rows could
  not be panned into at all.

- **A layout you save for one workbook stays with it.** Every worksheet of every workbook
  looked alike to the part that remembers layouts, so two workbooks with the same headings were
  each other's saved layout. A sheet is now known by its workbook and its own name, written
  down as a digest as every other identity is.

- When NVDA stops waiting on Excel part way through laying out a table, the command says the
  table could not be *read* — whichever step it happened in, including recognising the sheet
  and naming it, which happen before anything is built. It used to come out as "you are not in
  a table", "no headings", or a row that could not be read, depending on where it landed.

- Arrowing about the empty part of a sheet no longer rebuilds the layout on every keypress. A
  sheet reaches at least as far as the cell you are in, so that the row you are standing in is
  part of the table; what is watched for a table that has actually grown is how far it is
  written in, which only moves when the sheet does.

- **A pinned header row is not also drawn in the table.** Where a sheet's headings are its
  own first row — which is what you mark when you mark a header row — the row held above the
  band and the first row of the flow were the same row, and both were drawn: a doubled heading
  that appeared as soon as you arrowed up onto row one, and stayed. Row one is now served from
  the pinned row alone, and standing on it puts your cursor there. Where a table declares its
  headings somewhere else — two rows down, in a column, anywhere you cannot arrow to — row one
  is data and is still read as data.

- **A table measures the same from either end of it.** The widths were decided from a
  bandful of rows read forward from wherever the cursor was, so asking for the columns from
  the blank row under the data measured two rows — the heading row, and your own empty one.
  Every column came out sized to its heading, every value wrapped, and the same sheet laid
  out from the top came out right. When there is not a bandful ahead the rows behind are read
  instead, which costs nothing extra.

- **An empty cell is somewhere you can be, and somewhere you can route to.** A cell with
  nothing in it draws nothing, and so had no place on the display at all: standing in one gave
  no cursor, and no routing key would take you into one. On the blank row under a sheet's data
  every cell is empty, so the whole row was unreachable and you were left with speech. Each
  column now answers across its whole width on such a row — press under the heading you want
  and you arrive in that column, ready to type.

- **A table's columns scroll one at a time** rather than turning a page at a time. Moving to
  the next column along brings that column on at the right and takes one off at the left,
  instead of replacing every column on the display at once. The command that moves across a
  table still moves a whole display's worth, from wherever the scrolling has left you, and
  what is reported is which columns are showing — "columns 6 to 9 of 21" — rather than a page
  number, since the display can now sit between pages.

- The display follows the caret across the columns in a spreadsheet or a list, as it already
  did on a web page. Moving to a column that is not showing brings it on.

- **A row that wraps onto more than one line arrives whole.** Scrolling down onto a record two
  lines tall used to show its first line and leave the second off the display, so the values
  in the columns that had wrapped were the ones you could not read. Where the whole of it fits
  it is all brought on; where it is taller than the display it is left to panning, as before.
  This is for lists and documents as much as tables.

- In the table designer, a column chosen as the one repeated on every page can no longer be
  a hidden one. Choosing it and then hiding it used to leave the saved layout naming a column
  the display was not repeating.

- A list whose items are grouped reads as one run. Outlook's message list grouped by day
  used to end at each boundary, so panning stopped at the last message of the day; the walk
  now crosses into the next group, and the day heading is a row of its own. A closed group
  shows its heading and nothing under it.

- A message row in Outlook's list says what is true of *it*. NVDA builds part of such a row's
  name from whichever message is selected, so rows on the display could carry another
  message's "unread", "replied" or "forwarded". Rows you are not on are now built from their
  own fields instead; the row you are on still says everything NVDA says about it, which is
  where those flags are true.

For add-on authors and component work:

- Panels. Code can claim part of the display, subdivide it as it likes, and hand it back,
  without disturbing the segments it did not claim. `plugin.activatePanel(panel)` and
  `plugin.deactivatePanel(name)`.
- An application can declare that its objects read as a run, so they fill the display the
  way a list does, by setting attributes on its NVDA overlay class: `brlMultilineFlowRun`,
  and optionally `brlMultilineFlowNext`, `brlMultilineFlowPrevious` and
  `brlMultilineFlowAdmits` where the run is not a plain row of siblings. No import of this
  add-on and no dependency on it: an app module that sets them behaves identically when
  BrlMultiline is absent or disabled. A container holding such a run declares itself with
  `brlMultilineFlowRunContainer` and says where to begin with `brlMultilineFlowRunStart`,
  which is what lets an object pinned to a segment be the list rather than one of its
  members. A pinned flow is panned by the command for its segment: its rows come from its
  controller rather than from the buffer's window, so moving the window moved nothing and
  a pinned document, list or table would not scroll. Content that arrives after a run has
  been read to its end — a message in a pinned chat history — is now found: panning forward
  asks the source once more before refusing, and a pin whose reader can feel the last row
  that has been read asks on its own refresh tick. The tail of what has been read rather
  than a declared end, since a band that has just taken an arrival is full and open rather
  than ended, and watching for the end alone stopped watching after the first message. What
  arrives scrolls onto a full display, the oldest row moving off, while the reader is at the
  end of something whose end they have reached before — a setting per display and per
  profile, on by default, so a chat can be followed where a page that rewrites itself is
  not. Panning back into the history stops it and reaching the end again resumes it, and
  content that is merely longer than the band is never followed, so a pinned page cannot
  walk itself through a document on a timer. Editing the row under a caret that has not moved since a
  pan — forward Delete, or an editor rewriting the line — reads the band again when that
  row changed length, since every position below it has shifted; the band stays on the row
  the reader panned to. For controls an author does not own,
  `flowObjects.register` still takes a whole adapter.
- Wrapping and cursor routing behaviour are per segment, so a claimed area can behave
  differently from the display around it. Neither is exposed in the settings dialog.

Verified on a Focus 80 so far: configuration, two segment layouts, focus and caret
tracking, cursor routing, reversed panning, and pinning. Everything else is unverified. See
`docs/design/port-plan.md`.
