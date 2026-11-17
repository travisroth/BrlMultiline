# Table snapshots

Plan for measuring a browse mode table's columns from one read of the document, rather than
from a read of every cell.

Status: phases 0 and 1 are done. The spike matched the widths on every table tried, with the
snapshot about ten times faster; see "Phase 0 results". `flowTableSnapshot` is built and unit
tested, and `measure` does not use it yet, which is phase 2. Builds on `flowTableSource.measure` and the held table in `flowTableGecko`; see
structured-presentation-plan.md, Table timing.

## The problem

`measure` decides how wide each column is. It reads a sample of rows, `MEASURE_ROWS` around
the caret plus the header row, and for every column shown it builds the cell's region and
counts its braille cells. On a 20 by 21 table in Chrome that is about 190 cells, and each one
costs several calls out of NVDA:

1. Finding the cell: `cellAt` on the table's IAccessibleTable2, wrapping the cell as an NVDA
   object, reading its IA2 attributes for `hidden`, and `makeTextInfo` for the cell.
2. Reading its text: `info.text`, one call into the buffer.
3. Asking its declared header, for up to `HEADER_TRIES` cells per column:
   `info.getTextWithFields`, another call into the buffer.

All of it on NVDA's main thread. Holding the table took `measure` on that table from 1.58 to
0.76 seconds, and what is left is still proportional to the number of cells.

## The idea

NVDA's buffer for Firefox and Chromium already holds the whole table: its text, and a control
field for every cell carrying `table-id`, `table-rownumber`, `table-columnnumber`,
`table-rowsspanned`, `table-columnsspanned` and, once NVDA has normalised it,
`table-columnheadertext`. One `getTextWithFields` over a range of rows returns all of that as
one list of strings and field commands. Walking that list in Python gives every sampled cell's
text and declared header, and `measure` goes on exactly as it does for a grid that reads a row
in one go.

The widths are still braille widths. A cell's text is measured with `widthOf`, which
translates it through liblouis with the reader's table as NVDA draws it, contracted or not,
and a column drawn without capitals is lowered with `withoutCapitals` first. That is the path
Excel's bulk rows already take in `measure`, so contraction and dropped capitals are handled
the same way on both paths.

It is a snapshot for measuring, not for drawing. The rows shown on the band are still built
from the cells, with their positions for routing.

## What a cell is measured from today

A cell is a `TableCellRegion`: a plain `TextRegion` of `_textOf(info)`, which is `info.text`
with the padding stripped. Its control fields are not drawn into it, so no "lnk" or heading
label counts toward a column's width. The snapshot therefore needs only the same text per
cell, not a rebuilt rendering of its fields.

Whether that is the right measure is a separate question. If cells are ever drawn with their
field labels, `measure` and the snapshot change together, and the snapshot already has the
fields to do it with, through `braille.getControlFieldBraille`.

## Reading the snapshot

### The range

The sample is a run of consecutive rows, since a browse mode table offers no `rowAfter`, plus
row 1 when the run does not include it. So the snapshot reads at most two ranges:

1. From the start of the first sampled row's first cell to the start of the first cell of the
   row after the last sampled row, or to the end of the table when the sample reaches its last
   row. Ending at the next row's start rather than at the last cell avoids asking for a cell
   that a merge may have left empty.
2. Row 1 on its own, the same way, when it is not in the run.

Finding a row's first cell is one lookup, from the held table or from the buffer's own search,
`_iterTableCells` with a row and no column. Phase 0 times both. The search is by the raw
`table-id` alone: `controlIdentifier_docHandle` is written into the field markup, not stored as
a searchable attribute, so a search that names it finds nothing. The table's end comes from
`_getOffsetsFromFieldIdentifier` on its identifier. So the whole snapshot is three to five
lookups and one or two reads, whatever the number of columns.

A range much longer than the sample should be, such as a cell holding a whole nested document,
is not read: past a set length of text the snapshot gives up and the table is measured cell by
cell. The length is decided in phase 0.

### Walking it

The walk keeps a stack of the control fields it is inside.

1. A `controlStart` whose `table-id` is this table's and which has a `table-rownumber` is a
   cell. The outermost such field is the current cell; a nested table's cells have another
   `table-id` and do not start a cell of this one.
2. Every string goes to the current cell, nested table text included, as `info.text` would
   read it.
3. A cell's declared header is its field's `table-columnheadertext`, written as one line as
   `declaredHeader` does.
4. A cell marked `isHidden` is not a cell, as the held table refuses a hidden cell.

Each cell's text is stripped as `_textOf` strips it.

### Spans

`cellAt` on IAccessibleTable2 answers for every coordinate a merged cell covers, so today a
cell spanning three columns is measured in all three. The buffer puts the cell once, at its
first row and column. For the same widths the snapshot gives the cell's text to every
coordinate its spans cover within the sample. Phase 0 confirms that this is what the cell by
cell path does; if it is not, the snapshot copies whatever it does.

### What counts as read

A row the walk saw at least one cell of is read. A position the snapshot has no cell for is not
known, rather than empty or missing, and `measure` asks the table for it as it does now. Two
things put a position there: a row the walk never saw, and a cell merged down into the sample
from a row above it, which starts outside the range read. Asking the table settles both, and
costs nothing on a table whose rows are full, which is most of them.

## Where it plugs in

A new module, `flowTableSnapshot`, with one function that takes the handle, the sampled rows
and the columns, and returns the snapshot or None. None for anything that is not a document
inheriting `Gecko_ia2._getTableCellAt`, which is the same gate as the held table, and None
for any failure, so the table is measured as it is now. `CallCancelled` is raised, not
swallowed; see `flow.CallCancelled`.

`measure` takes the snapshot before its loop over rows. For each row it uses, in order, the
snapshot's texts for that row, then `rowTextOf`, then the cell by cell read. The per-cell
header question is answered from the snapshot cell's declared header, at the same point in the
loop and under the same rule, so a column declares a header exactly when it would have read
cell by cell. Handing all the snapshot's headers over up front, as `columnHeadersOf` does, is
not the same: where row one is borrowed before any cell is asked, the column becomes declared
instead, row one is then counted as data, and its typical width moves. Everything after the
text is read, from the widths to the labels to which columns are hidden, is unchanged.

The snapshot is taken inside the `tableHeld` block `measure` already runs in, on the main
thread. It holds no COM pointer and nothing outlives the call.

## Phases

**Phase 0: the spike.** A script in `spikes/`, run from NVDA's Python console on an open table,
that measures the table both ways and logs:

1. Each column's width, typical width, label and hidden flag from each path, and every
   column where they differ.
2. The time each path took, with `stageTimes` on.

Tried on the watchlist, on the 20 by 21 table, on `testtables/basic-table.html`, and on pages
with merged cells, hidden columns, a nested table and header cells declared with `headers=`, in
Chrome and Firefox. It answers the questions below. The rest goes ahead only if the widths
match, or differ only where the difference is understood and accepted, and the snapshot is
clearly faster.

**Phase 1: the module.** `flowTableSnapshot` and its unit tests, with a fake document returning
field command lists: plain cells, spans, a nested table, a hidden cell, a missing row, a range
too long, a cancelled call.

**Phase 2: measuring with it.** `measure` uses the snapshot. Tests in `test_flowTableSource`
show that a snapshotted table and the same table read cell by cell give the same measurements,
including for a column drawn without capitals. A `stageTimes` stage, `snapshot`, so its cost
shows in the timing lines.

**Phase 3: in NVDA.** The same pages as phase 0, timed with `stageTimes`, and the layouts
checked on the Monarch.

## The time limit

The time limit on `measure` in structured-presentation-plan.md, Table timing, is still owed.
The snapshot makes it rarer for a browse mode table to reach the limit; it does not replace
it. UIA tables, list views and the message list have no read like this, and a snapshot that
falls back is measured cell by cell.

## Later, not in this plan

1. Building the rows shown on the band from the same kind of read. A page of columns reads each
   visible cell for drawing, and the same walk could give the text, but drawing needs each
   cell's position for routing, which a snapshot for measuring does not keep.
2. `cellHasContent` and `FlowBand._omittedColumnHasContent`, which read one cell at a time on
   purpose and are cheap enough.

## Open questions

1. Is a cell's text in the walk exactly `info.text`, including the spaces between nested
   elements and any embedded object characters?
2. Does `cellAt` answer every covered coordinate of a merged cell, as the span rule assumes?
3. Does an `aria-hidden` cell appear in the buffer at all, marked `isHidden`, or not at all?
   Both are treated as no cell; phase 0 says which happens.
4. Which is cheaper for finding a row's first cell: the held table or the buffer's search?
5. How much text is too much for one read?

## Phase 0 results

**Barchart watchlist, Chrome, 22 by 21, rows 1 to 8.** One read of 816 characters and 900
field commands, rows found through the held table, took 0.049 seconds: 0.009 finding rows,
0.040 reading, 0.001 walking. `measure` cell by cell took 0.76 seconds, of which 0.69 was cell
lookups and 0.17 header questions. Every sampled cell's text was the same both ways (question
1, for a table of plain text and links). No merged or hidden cells, so questions 2 and 3 are
still open. The search by attribute failed on that run for the reason under "The range"; it
is fixed in the spike and not yet timed (question 4).

Every width matched. The labels, typical widths and declared flags that differed came from the
spike handing `measure` all the snapshot's headers up front, which is what led to the rule in
"Where it plugs in"; the spike now follows that rule.

**The same table again, with the spike fixed, rows 1 to 9.** Every column measured the same
both ways, with capitals and with every column drawn without them, and every cell's text and
declared header matched. `measure` cell by cell took 0.80 to 0.83 seconds; the snapshot took
0.050 to 0.055, one read of 961 characters and 1088 commands. Finding the rows took 0.008
seconds through the held table and 0.006 through the search by attribute, so for question 4
either will do: the search needs no held table and is the one to build on.

**A bank statement's transaction table, Chrome, 47 by 6, rows 1 to 9.** A messy table: three
rows that are one cell spanning five or six columns, section titles and a "nothing to show"
note among the data, header cells whose text runs into their sort buttons' labels, and
description cells holding a button. Every column measured the same both ways, with capitals
and without, and every cell's text matched, so question 1 holds for mixed content too: where
`info.text` runs a cell's text and its button's label together, so does the walk. For question
2, the held table gave one and the same cell for every position each merged cell covers, so
giving a merged cell's text to every position it covers is what the cell by cell read does.
`measure` cell by cell took 0.23 to 0.25 seconds, the snapshot 0.021 to 0.026, one read of 943
characters.

**`testtables/snapshot-cases.html`, Chrome, eight small tables.** Every table the snapshot
could read measured the same both ways, and every cell's text matched. The snapshot took 0.003
to 0.014 seconds against 0.036 to 0.079 cell by cell.

1. Question 3: Chrome leaves an `aria-hidden` cell out of the tree, so it is never in the
   buffer and `isHidden` never came up. A whole hidden column made the table a column
   narrower. A single hidden cell shifted the rest of its row left, so its row ended a column
   early: the cell by cell read found no cell at those positions, and neither did the
   snapshot. A `display: none` column behaved as if it were not there.
2. Question 2 again, for cells spanning rows: the held table gave one cell for every row each
   covers, as it did for spans across columns.
3. A nested table: the text matched, and the snapshot gave the outer cell's declared header
   where the cell by cell read gave the nested table's. See "Found along the way".
4. `headers=` with no header row: the same declared headers both ways.
5. Question 5: a cell of about 250,000 characters stopped the snapshot at the limit, as
   intended, and the table was measured cell by cell in 0.51 seconds. Almost all of that was
   translating the one long cell; reading it took a few hundredths. A cell of about 20,000
   characters was read in 0.006 seconds, and translating it took 0.04. So the read is not
   where long text costs, and 200,000 characters is a sound limit for it.
6. Question 4 again: the search by attribute found rows in 0.000 to 0.001 seconds on these
   small tables, against 0.003 through the held table.

## Found along the way

All three are in `measure` or what it calls, not in the snapshot.

1. Fixed: `declaredHeader` took the innermost control field carrying
   `table-columnheadertext`, which for a cell holding a nested table is a cell of the nested
   table. Given the table's identifier it now takes the header of that table's own cell, as
   the snapshot's walk does, and `measure` and `declaredHeaders` pass it.
2. Still open: a declared header in a column drawn without capitals is measured with its
   capitals, though `_declaredRow` draws it without, so its label width is a few cells too
   wide.
3. Fixed: a very long cell was translated in full to find its width. `measure` now translates
   at most `MEASURED_TEXT`, 160 characters, of a cell or a header, which even contracted is
   wider than any display; the cells drawn on the band keep all their text.
