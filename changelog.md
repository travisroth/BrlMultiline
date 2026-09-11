# Changelog

## 0.1.0, in development

First version built against NVDA 2026.3, replacing an earlier prototype written for
NVDA 2023. 2026.3 is the first release to carry the `braille` package that this add-on is
written against, so until it ships, installing and testing means running an alpha snapshot.

Added:

- **Pictures from the screen, drawn on the pins.** Point at something — on a web page, the
  graphic NVDA just told you about — and press the key: its rectangle is copied off the screen
  and comes back as outlines or as a silhouette. Not restricted to things NVDA calls a graphic,
  since a diagram in a canvas, a map and a chart published as a picture all report roles that
  say nothing, and a reader who has pointed at something has said what they want more clearly
  than a role would. Good for line art, logos, diagrams and maps; adequate for high contrast
  photographs; poor for everything else, and it says so rather than being tuned forever.

- **One key cycles outlines, brightness, and brightness reversed.** None of the three can be
  chosen in advance — the same picture drawn two ways is two entirely different panels — so the
  answer is a key to press rather than a question nobody could answer. It re-draws the pixels
  already captured rather than copying the screen again, so a page scrolling underneath you
  does not silently become the picture you are reading.

- **How much of the panel a picture raises is decided by the hand, not by the pixels.** A fixed
  threshold raises everything on a high contrast logo and nothing on a soft photograph, and a
  reader cannot tell those two failures apart: both are a panel that says nothing. So the cut is
  taken wherever it leaves a readable fraction of the pins raised, whatever the picture's
  contrast. Which side of a silhouette is the subject is worked out rather than assumed, so a
  white logo on a dark header comes out as the logo and not as a hole.

- **A picture zoomed in is composed again from its pixels rather than magnified.** The capture
  is kept at its own resolution, so a quarter of a picture is that quarter reduced from its own
  pixels — genuinely more detail, as far as the capture goes and no further. It is the first
  drawing with an up and a down: a chart refits its value axis to whatever it is showing, so it
  never has anything above or below the panel, while half a photograph is still half a
  photograph.

- **A chart no longer takes a column of prices for a column of dates.** Excel stores a date as
  a serial number, so the way a date is told from data is that the cell shows something other
  than the number it holds — which is also true of every number the sheet has formatted. A
  first column shown as "$1,000", or as a percentage, or rounded to two decimals, was therefore
  read as labels and dropped out of the chart. A formatted number is still a number; a date is
  what does not read as a number at all.

- **A spreadsheet is asked whether the first row of a selection names the columns**, where it
  is in a structured table and can answer. Nothing in the values can: a table headed
  "Metric, 2025, 2026" over "Sales, 10, 20" is cell for cell the same shape as a table of years
  and figures, and guessed wrong it charts 2025 and 2026 as data points. For a plain range the
  guessing is unchanged.

- **A line that fills the band exactly no longer skips the row after it.** Every reading unit
  NVDA hands over ends with a space it adds on purpose, in case the caret is at the end of the
  line. It is a cell like any other, so a link whose text filled all thirty-two cells needed
  thirty-three, and the wrap gave that one blank cell a row to itself — an eighth of a Monarch
  spent on nothing, reading as a line skipped between two links as though the first were
  continuing. That row is now dropped, unless the caret is actually parked on it, which is the
  case the space was added for.

- **Roles and states drawn as shapes rather than written as words**, on a display that raises
  pins one at a time. NVDA writes `btn` before a button's name, `cbo` before a combo box and
  three cells of braille patterns for a checked box, because a braille line has nothing else to
  spend. The cell one letter sits in is three pin columns by four, so a shape drawn in those
  twelve pins says the same thing in one cell — and the two cells it gives back go to the line,
  so more of the object's own name fits on a row. The shape sits where the word was, a routing
  key on it reaches the same place, and turning it off brings the words straight back.

  It applies to everything the display shows, not only to a flow: a menu bar in File Explorer
  is full of buttons and never goes near browse mode. It also does not care which side of the
  name NVDA puts the word on, which differs between browse mode and an ordinary window.

  Twenty-six symbols, sixty-five cells given back between them. Buttons, toggle and radio
  buttons, edit and password fields, combo boxes, lists, menu items, tables, graphics, progress
  bars, separators, submenu markers, links, visited links, headings at the first three levels,
  and the checked, half checked and pressed states with their absences. The words NVDA writes
  for these are read out of NVDA at run time rather than written down here, so a reader running
  it in another language gets the shapes over their own abbreviations.

  Off by default: the words are a notation you already know and the shapes are this add-on's
  own. There is an unbound command that draws the whole vocabulary with the braille each shape
  replaces beside it, for learning them.

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

- **A filtered worksheet is read as the filter left it.** Panning off the end of a filtered
  block carried on into the rows the filter had taken away, at both ends, because a filter
  hides rows rather than removing them: ask Excel for row 40 of a sheet filtered down to nine
  rows and it hands over row 40 as if nothing were hidden. Excel is now asked which rows it is
  showing — once per reading, in one call — and the band steps between those, so the reading
  ends where the filtered block does. A sheet with no filter costs that one question and is
  walked exactly as before, and a sheet whose answer cannot be had is too, rather than losing
  you the rest of it.

  **And a filter applied while the band is already reading takes its rows off the display.**
  Stepping between the rows on show settles what is fetched from that moment on and says
  nothing about the rows fetched before you filtered: those stayed in the band's cache, were
  re-read by their own row numbers, and were drawn above and below the one row you had
  filtered down to. Every move you make now checks the rows on the band against the rows the
  sheet is showing, and a reading holding one it has stopped showing is made again — with the
  columns measured afresh from rows that are actually on show, and the page of columns you had
  turned to kept. The report says so too, so a band holding a row that is no longer there can
  be told from one that is working.

- **A hidden column is not drawn, counted or named.** Hiding column 5 in Excel took it out of
  what the arrow keys reach and left it in the layout: it had a column of the display, its
  heading sat in the pinned row, and it was counted in "columns five to six of six" — a column
  of braille spent on something you had said once you did not want. Columns Excel is hiding are
  now left out before anything is measured, which is what takes them out of all three. The one
  exception is the column you are standing in, which is kept whatever the sheet says, since a
  column left out has nowhere to put your cursor and nothing for a routing key to reach.

  The rows and the columns come out of the same single question, so a sheet that is both
  filtered and hiding a column costs no more to read than either.

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

- **An error NVDA logs when you close a workbook no longer arrives under this add-on's name.**
  Close a workbook with control+W and NVDA is still holding the cell that had the focus while
  it reports the keystroke — but the workbook is gone, so asking Excel about that cell fails
  and NVDA writes a traceback into the log. The traceback is made entirely of NVDA's own
  frames, and the add-on is not doing anything at that moment; the object named at the top of
  it, though, is one NVDA composed out of this add-on's classes, so it reads exactly like a
  crash in the add-on. A cell whose workbook has closed now says it has nothing to report,
  which is an answer NVDA's own code already expects from it, and the log stays clean.

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

- **A line is no longer drawn twice at the bottom of a multi-line edit.** Two rows are two
  blocks and two blocks must be two different pieces of the document; a rich editor answering
  during a keystroke can expand the line above the caret over the line the caret is on, so the
  band drew that line once above and once under your fingers. A block whose range runs into the
  block it was walked from is now refused while you are writing, the same way a block that
  reaches backwards already was, and the next reading — a moment later, or your next keystroke
  — draws it properly.

- **The second look after a keystroke can no longer be postponed for ever.** The pass that
  repairs a transiently wrong reading was restarted every time you typed, so steady typing
  pushed it out again and again and it never ran — and steady typing into an editor that
  answers wrongly while you type is exactly when it is needed. Typing still puts the pass off,
  since a pass that lands mid-word reads the very thing it is there to clear, but no longer
  than half a second: you now get one either just after you pause or, in a long burst, at that
  interval. A pass that finds nothing to mend also takes back its own claim on the next one,
  rather than leaving it for some later redraw to spend a band's worth of reading on.

- **The rows above the line you are typing come back on their own.** A keystroke re-reads the
  band from the caret — its own line, then the rows below it, and only then the walk back to
  what you had written above — so on a slow editor the rows above are what the allowance runs
  out during, and they are the ones you lose. On a ten row display that is most of the band:
  the line being typed, a marker saying there is more, and nothing you could read. What could
  not be afforded is now written down and finished by the pass that comes back a moment later,
  on a fresh allowance, without the keystroke waiting for any of it. The band moves once, when
  the rows are all there, rather than creeping up under your fingers a row at a time.

- In the table designer, a column chosen as the one repeated on every page can no longer be
  a hidden one. Choosing it and then hiding it used to leave the saved layout naming a column
  the display was not repeating.

- A list whose items are grouped reads as one run. Outlook's message list grouped by day
  used to end at each boundary, so panning stopped at the last message of the day; the walk
  now crosses into the next group, and the day heading is a row of its own. A closed group
  shows its heading and nothing under it.

- **Arrowing up in a slow list no longer leaves the display where it was.** Reading back into
  Outlook's grouped inbox needs a step or two into the application, and there a single step can
  take twice as long as a whole keypress is allowed. The band tried to reach back to where you
  had moved, spent the allowance on the reaching, and then had nothing left to draw the band
  it fell back to — so the row you moved to arrived with blank rows under it, and the reading
  only came back when you left the folder and returned. Placing the band around you is now
  given its own allowance, since it is the answer to the key you pressed and the reaching was
  not, and the second look that finishes a band the budget cut short now carries on for as long
  as the document is still answering. It used to stop the moment a fetch changed nothing on the
  display — which is exactly what fetching the rows *above* the window does, those being the
  rows you scroll up into.

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

Fixed, from a performance review of flow editing:

- A letter typed after a pause appeared only once the next one had been typed. The pass
  that re-reads an edit shortly after a keystroke rebuilds the band's blocks, and so
  replaces the region NVDA reaches for at `mainBuffer.regions[-1]` — but a pass that found
  the cells unchanged returned without telling the segment, so NVDA queued the retired
  region, re-read it, and the band was handed a replacement with nothing marked as read.
  Pointing NVDA at the block the band draws is now separate from deciding whether to write
  the display, and happens on every path; a reading queued on a region that is replaced
  before it is acted on moves with the block. The same fault, and the same repair, in the
  live-content pass. A pass also now compares the cursor as well as the cells, so a re-read
  that moved only the caret still redraws.
- One keystroke while writing cost a third more reads of the document than it needed. The
  caret's own line was fetched, read again for its cursor and then read a third time with
  nothing in between; and the band filled downward from the caret before being put back
  under the row the reader had, so on a ten row band half the rows fetched were pushed off
  the bottom before anything drew them. The window is chosen first and filled once, and the
  block is activated before its first translation rather than after it.
- Reading backward out of a day in a grouped list — Outlook's inbox — could land the reader
  short of the previous day's last message with nothing to say so, because the walk to the
  end of a group stopped at five hundred and offered whatever it had reached as the true
  end. The container is asked for its last row first, which is one call whatever the day
  holds, and where nothing can answer the walk says so rather than guessing. The same for
  the last visible row of a tree branch, including behind the grouping some providers put
  between a node and its rows.
- A live re-read of the band ignored its own allowance: the operation was entered and then
  every held block was fetched without a gate between them. Each block is now gated, a pass
  cut short carries on from where it stopped rather than starting again at the top, and a
  block nobody re-read keeps the rendering it already has.
- Reaching the tail of a very long paragraph cut every row in front of it and threw them
  away, on each keystroke. Where every row is the band's width — a narrow band, every table
  cell — the rows are now counted rather than walked. Word wrapping is unchanged, since
  there a row's end really does depend on the row before it.
- Pinned objects were all re-read on whichever core cycle their interval expired on,
  including the cycle carrying the reader's keystroke. A cycle NVDA has an update pending on
  is left to it, up to a maximum staleness, and a cycle that does refresh spends a small
  allowance and rotates fairly between the pins. A changed pin also published its display
  once rather than twice.
- The cost command reported only the second half of an arrival that had to be given a
  second allowance, so a slow reach in front of a placement was invisible in the very
  numbers the budget is judged by.

Verified on a Focus 80 so far: configuration, two segment layouts, focus and caret
tracking, cursor routing, reversed panning, and pinning. Everything else is unverified. See
`docs/design/port-plan.md`.
