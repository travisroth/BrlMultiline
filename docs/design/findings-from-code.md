# Findings that used to live in code comments

The docstrings and comments were trimmed in October 2026 to hold the contract and the minimum
reason a change needs (see `CLAUDE.md`). Most of the history they carried was already in the
plans in this folder. What follows is the part that was not: hardware reports, review findings
and earlier designs that shaped a rule now stated in the code without its story.

Each entry names the module the note lived in and the commit that removed it, so the full
original wording can be read with `git show <commit> -- <path>`.

## Displays and drivers

**A composite's log line listed a display that had gone.** Describing every slot rather than
every member in the layout logged "1 rows of 80, 2 display(s)" with the display just lost still
shown on rows 0 to 7. Only members in the layout are described now, and those away get a line of
their own. `brlMultilineVirtual/__init__.py`, 883597b.

**A display switched off at startup put three tracebacks in the log.** A constructor that raised
had not reached the fields its own blank needs (`hidBrailleStandard` sets `_maxNumberOfCells`
only after reading the report descriptor), and the blank in `terminate` failed and logged its own
error. Such a driver is now terminated with NVDA's suppress-clear flag. Same module and commit.

**Changing the Monarch's pitch left NVDA believing the old shape.** The driver called
`handler.handleDisplaySizeChanged()`, which does not exist, and the exception was swallowed, so
NVDA kept 8 rows of 32 (256 cells) while the driver drew 10 rows and routed against 320. That is
the likely cause of routing at 10 rows doing nothing. One read of `handler.displayDimensions`
raises `displaySizeChanged` instead. `brlMultilineMonarch/__init__.py`, e02d6b0.

**The settings panel sounded empty.** A panel without a `panelDescription` announces only its
title, which a hardware run heard as an empty panel in the tab order. `settingsPanel.py`,
883597b.

**A changed member list was reported for every display that was merely off.** The check
compared the configuration against the members being driven, so a powered-down display looked
like a configuration change on every profile switch and filled the log. It compares against the
configured members now, and says each divergence once. `__init__.py`, f428cd9.

## Messages and panning

**Panning one display moved a message on the other.** While a flash message was up every
panning key went to the message, so panning a chat history on the Monarch scrolled the message
on the Focus 80 and reset its timeout. Only the display holding the message pans it now.
`patches.py`, 0baca4c.

**A flash message froze the other displays.** The container held its redraws back while it was
not the handler's buffer, so for as long as a message lasted a pinned chat history took no new
messages and would not pan. `segments.isShowing` lets the container draw around a message.
`segments.py`, 44ae4fc.

**After a profile switch only the focused line was drawn.** Paths that kept the flow controller
refreshed the segment, but a rebuild makes a new segment, which then held only NVDA's focus
regions. Reported from VS Code, which has a profile of its own: going out to NVDA's menu and back
switches profile twice and rebuilds the display. A browser without a profile never rebuilt, which
is why it looked like VS Code's fault. `flowBand.py`, c24440e.

## The flow and its band

**Which end of the band a block comes in at decided three hardware reports.** The direction test
is recorded for the dry run because it is invisible to the reader and is the difference between
a one row scroll and a display's worth of movement. `flowControl.py`, fceca35.

**A control's prompt cost a third of the budget.** An earlier version fetched half a band of rows
above a control to show its prompt, four blocks on a Monarch to use one, and the display filled
with the not-yet-read marker. One block is read backwards now. `flowControl.py`, fceca35.

**A pan was lost when the caret moved back to where it started.** The pan was given up only once
the caret left the page, so a caret moved onto the panned part and then back with Home matched
the old mark and the display stayed where the cursor was not. And the page is now brought back
when nothing on the band reaches it any more. Both found in review. `flowControl.py`, fceca35.

**A cursor vanished from a list two seconds after arriving.** A re-read replaced a block's
region, and regions are born inactive, so `cursorCell` found no active region. The reader's block
is made active again after a re-read. `flowControl.py`, fceca35.

**A re-read spent twenty times its allowance on a timer.** It fetched the whole window without
checking the budget between blocks; a table whose rows were slow did this between keystrokes. It
now stops at the allowance and carries on from there. `flowControl.py`, fceca35.

**Re-reading one block emptied the band.** `replaceBlocks` takes what it is given as the window,
and a caller that passed only the re-read block left the window holding one block: moving from a
folder to its first child emptied the band. The window is always replaced whole now.
`flowControl.py`, fceca35.

**A budget stop was diagnosed as the wrong cause.** Only the source's answers recorded a reason,
so a band stalled for time reported an earlier answer, "this block already holds the line that
follows it", and sent the search to the wrong module. `flowControl.py`, fceca35.

**A pinned chat stopped following after its first new message.** The tail watch first required
the edge to be END, but a fetch that finds something leaves it OPEN, so the first message to
arrive turned the watch off. It asks whether the reader can feel the tail of what has been read.
`flowControl.py`, fceca35.

**Writing in an edit met the fetch limit routinely.** A multi line edit is read through the real
control and a caret update re-reads the whole band, so the default allowance, sized for a
virtual buffer, was doubled for it. `flowSources.py`, bdc1fbf.

**The band stayed on a table after the caret walked out.** `recheck` returned at once for a
table, on the ground that its comparison cannot answer for one. On hardware the reader read a
table, moved up to a heading well above it, and the band went on showing the table. It now looks,
at the cost of one `_getTableCellCoords` per redraw. `flowBand.py`, c24440e.

**Pinned header rows went stale.** `rereadContent` walks only the window's blocks, so a header
renamed under the reader went on saying its old text, first on the band and later on pins.
`flowBand.py`, c24440e, and `objectMonitor.py`, 0baca4c.

## Tables and saved layouts

**A table's columns scroll rather than turn.** The first attempt squeezed what it could onto one
band and dropped the rest, giving three cells each of eight columns and eleven columns that did
not exist. The second showed one fixed page at a time, so a caret one column past the edge
replaced everything under the reader's hands. The reader asked for the row axis's behaviour: one
column on at the right, one off at the left. `flowTable.py`, bdc1fbf, and `flowBand.py`,
c24440e.

**A layout saved for a table that later stops applying used to be silent.** A site that adds a
query string or renames a column changes what a layout matches on; reported from hardware as a
watchlist that stopped coming up laid out with nothing in the log. The log now says which half
missed. `flowTableLayouts.py`, 3756407.

**Changes reported as saved were not written.** The store logged and swallowed configuration
failures, so the reader was told "saved" and "deleted" of changes that were never written.
`LayoutsNotSaved` is raised instead. Found in review. `flowTableLayouts.py`, 3756407.

**Following columns by heading had two traps.** A remembered following carried to the next page
load went stale when later columns moved again, so following is done once per table built and
not kept. And two saved columns both called "Value" were followed onto the table's one "Value",
one decision silently overwriting the other; a column is followed only where it is the one place
it can have gone. Both found in review. `flowTableLayouts.py`, 3756407.

**Moving layouts out of profiles was written and then taken out.** When the layout store moved
to the base configuration (decided with the reader on 16 September 2026), a migration of layouts
left in profiles was written and removed on review: the reader's profiles held none, and the
merge could lose a newer copy to an older one or clear a profile before the base was written.
`bmConfig.py`, 14d8b18.

**Two Excel workbooks shared one identity.** A control's identity is its application and window
class, and every sheet of every workbook is "excel" and "EXCEL7", so two sheets with the same
headings were each other's saved layout. A sheet now names its workbook and sheet, as a digest.
Found in review. `flowTableIdentity.py`, 1c2855a, and `appModules/excel.py`, 2c46eda.

**A list header was read as row two.** Stepping through a list's children met the header that
sits between rows, and it came back as a row; anything that is not a row is now stepped over
without counting. Found in review. `flowObjectTable.py`, c24440e.

## Objects and lists

**One message wrapped with a hanging indent and the next did not.** Whether a long item's
continuations were indented depended on whether the control reported `positionInfo["level"]`,
and in one Outlook message list it varied between messages. An item in a run now has at least the
run's first level. `flowObjects.py`, e02d6b0.

## Excel

**A refused selection left NVDA's next arrow in the wrong place.** Moving to a cell before
selecting it meant a selection Excel refused left NVDA going on from a point Excel never went to.
It is selected first and moved to after. Found in review. `appModules/excel.py`, 2c46eda.

**One move between cells read the used range eight times.** Recognising the table asked the
shape twice on its own, and other lookups repeated it; a review counted eight used range reads
for one move. The shape is cached per adapter, while where the reader is is asked afresh.
`appModules/excel.py`, 2c46eda. A related review counted the table resolved four times for one
move, now found once and carried. `flowBand.py` and `flowTableSource.py`, c24440e.

## Graphics, charts and pictures

**A bar chart drew two scales.** Zero was placed by the low-to-high range and each bar drawn as
a fraction of the largest magnitude, so a chart of +25 and -75 put zero a quarter of the way
down and drew the +25 bar a third of that quarter: a bar an eighth of its neighbour's height
where the numbers say a third. One scale holds the bars and zero now. `chart.py`, 986faae.

**A line under a fingertip answered with the height.** A press was checked only against the
nearest data point, so a steep stretch a column or two over, or points sharing a column, were
never looked at. Asked for from hardware; `NEAR_COLUMNS` and `NEAR_ROWS` measure against the line
as drawn. `chartDraw.py`, 0baca4c.

**The last close of a year of prices could not be reached.** Rounding a window's start and width
separately, and respacing a window about its middle, both left a window against the end of the
data one day short, so panning right stopped before the data did. Found on hardware at one pin
per point. `chartDraw.py`, 0baca4c, and `chartLine.py`, 3756407.

**A heading row looks like data.** A table headed "Metric, 2025, 2026" over "Sales, 10, 20" is
cell for cell the same as a table of years, and guessed wrong it charts 2025 and 2026 as points.
Where the spreadsheet declares a header row, that is taken at its word. `chartSource.py`,
3756407, and `flowObjectTable.py`, c24440e.

**A small image file can declare an enormous picture.** A few kilobytes can declare a hundred
million pixels, and decoding takes gigabytes before anything shrinks it, so the declared size is
checked before decoding (`DECODE_PIXELS`, twenty-five million). Found in review.
`imageSource.py`, f402e10.

**A page turn on a chart wrote the panel twice.** On a 200 point chart some page turns wrote a
window one point short and then the right one; each write is a mechanical refresh a reader
feels. Candidates are composed and checked before anything is written. Found in review.
`graphicsMode.py`, 2c46eda.

## Glyphs, key layers and dialogs

**Shipped layer names were spoken in English.** In a translated dialog the shipped layers' names
stayed English; a shipped layer still carrying its shipped name is now said in the reader's
language. Found in review. `keyLayerDispatch.py`, fc30d49.

**The glyph listing once hid the half that mattered.** It printed a shape and the braille it
replaces one after the other, and nothing at all for a wording, so the comparison a reader needs
could not be made. Both are printed on the same lines now. `glyphs.py`, 14d8b18.

**The column list in the table designer left two things out.** A review found each line missing
which end of the heading survives and whether the column is the one repeated on every page.
Whether it is shown is left to the line's checkbox, so "hidden" is not said twice.
`flowTableDesigner.py`, 14d8b18.
