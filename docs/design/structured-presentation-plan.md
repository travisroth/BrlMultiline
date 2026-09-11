# Structured presentation: trees, tables, and how a thing is shown

This is the design for the second half of the flow. The first half answered *what to
read*: a document, a run of objects, an edit being written in. It reads everything as a
sequence of blocks and shows each block as rows of text. That is right for prose and it is
right for a flat list, and it is why a flat list works today.

It is not enough for anything with a shape. A tree has depth and the depth is information.
A table has columns and the columns are the whole point. A list view has columns the reader
wants some of and not others. None of that survives a pipeline whose only structural
vocabulary is "one block after another".

So this plan adds a second axis. Alongside "what to read" there is now "how this kind of
thing is shown", and the second is chosen from what the reader is actually looking at
rather than from a role alone.

**Status: tables are feature complete for now.** M0, M1, M2a, M3 (a to d), M4, M5, M6 and M7
are built and on hardware: a table is recognised, laid out in columns with its headings pinned,
paged, arranged by command or by dialog, saved, and applied again by itself when that table comes
back. All three shapes of source are read — browse mode, list views and the message list, and
Excel through the add-on's first application module. What is not built: M1's orientation note,
M2b (the presentation registry) and M6b (applying a saved layout by name), which is deferred
rather than pending — see its entry for why.
The milestones below say which,
and where the built shape differs from what was planned the decision records both. Read this
before extending `flowObjects.py` or `flowRender.py`.

## The problem, in three failures

**A tree flows as a flat list of its root's siblings.** In Outlook's Go To Folder dialog,
an expanded folder's children are skipped: the run walks on to the next sibling and steps
over the subtree entirely. This is `_isSameRun` in `flowObjects.py:173`, which ends in
`_sameParent`, over a traversal whose `nextOf` is `obj.next`. In NVDA's
`sysTreeView32`, tree items have genuine structural `parent`, `firstChild`, `next` and
`previous` (`NVDAObjects/IAccessible/sysTreeView32.py:106-144`), so `obj.next` on an
expanded node is its next *sibling*. The children have a different parent, so even if the
walk reached them, `admits` would throw them out.

**Depth is not shown.** There is no indent anywhere in the flow. A grep of the whole
subsystem for indent or level returns one hit, in a docstring about headings. `SourceBlock`
(`flow.py:199`) carries `gapBefore`, `gapAfter`, `isBlank`, `isControl`, `isDecoration`,
`isInteractive` and `collapsed` — spacing and identity, nothing about structure. Two items
at different depths are two rows of text that look alike under the fingers.

**A table has no presentation of its own at all.** In browse mode a table is read as the
document's text in reading order, which is what NVDA does and is worth keeping, but it is
the only thing available. There is no way to say "this table reads by column", or "give each
row two braille rows", or "these two columns are not worth the cells".

## What exists today that this builds on

The traversal half exists in embryo and should be recognised as such rather than replaced.
`ObjectAdapter` (`flowObjects.py:249`) is already a registry rather than a branch in a
condition: `matches`, `start`, `admits`, `activates`, `nextOf`, `previousOf`, with
`register`/`unregister` so that an add-on owning a control can supply a better judgement
than this module's guess. The shape is right. What is missing is that every adapter it can
express produces a flat run, and that it says nothing about display.

The renderer has one property that turns out to be load bearing. `FlowRenderer.render`
(`flowRender.py:118`) lays a single block out in a `BrailleBufferSegment` of the band's
width and returns, for every row, both the cells and a map from each cell back to the
position it came from. Cells that came from no position — the continuation mark, and the
padding after a short row — are already marked `NO_POSITION`, and routing into them already
does nothing. That is exactly the mechanism indentation needs, and it means indent costs no
arithmetic anywhere else in the system. See "How indent is actually drawn" below.

NVDA answers most of the structural questions already, and the plan leans on that rather
than deriving anything it does not have to:

- **Depth.** `obj.positionInfo["level"]` is populated by `sysTreeView32` (line 193), UIA
  (`UIA/__init__.py:2439`, `2621`), IA2 (`IAccessible/__init__.py:1786`), JAB, Excel's app
  module, and Outlook's own app module, which sets it explicitly
  (`appModules/outlook.py:667`). NVDA's braille already renders it as "lv 3"
  (`braille/regions/properties.py:171`).
- **Expanded and collapsed.** NVDA's state reporting already says so, and already reaches
  the display through the object's own region. This plan does not touch it.
- **Table coordinates in a document.** `documentBase.DocumentWithTableNavigation` gives
  `_getTableCellCoords`, `_getTableDimensions` and `_getTableCellAt(tableID, startPos, row,
  column)` (`documentBase.py:119`, `199`, `234`). That last one is random access into a
  browse mode table by coordinates, which is what any layout other than reading order
  needs. Browse mode tree interceptors inherit it.
- **Table coordinates on objects.** `behaviors.RowWithFakeNavigation`
  (`NVDAObjects/behaviors.py:765`) is NVDA's own answer for a list view with columns: the
  cells are the row's children and they carry the table cell properties. It also skips
  cells whose `location.width` is zero, which is how a hidden column reports itself — worth
  copying, since a column the application has hidden should not cost braille cells either.

## Decisions taken

These come out of the design conversation and are settled. They are recorded here so they
are not re-argued.

1. **A tree flows in visible order.** Expanded nodes' children appear inline; a collapsed
   node's children are absent. The run therefore changes shape when the reader expands or
   collapses something, which is correct: it is what the arrow keys do and what is on the
   screen.

2. **Where there is keyboard navigation, the flow follows it. Where there is not, the flow
   is free to be more general.** A tree has keyboard navigation, so visible order is both
   what the keys do and what we show. Table cells almost never have it, which is why browse
   mode invented its own; there the flow may show what no single keypress reaches.

3. **Collapsed and expanded are NVDA's business.** They already reach the display through
   the object's region, and they already work in the tree flow today. Nothing is added.

4. **Indent is relative, and the shallowest depth on the band is the baseline.** Absolute
   indent from the root spends cells a 32-column display does not have once the reader is
   six levels into a folder tree. The shallowest visible depth is therefore drawn at zero
   and everything else is indented from it. An application module may override this.

   *Built as the shallowest, not the top row's, which is what this decision first said.* In
   the common case — panning down into a subtree — they are the same number. They part when
   visible order comes back *out* of a subtree, which puts an item shallower than the top row
   at the bottom of the band and leaves nothing to the left of the margin to draw it at.
   Taking the minimum is the same single computation and cannot go negative.

   **On the band means on the band.** The cache is wider than the display — `_trim` keeps a
   window's worth either side — and planning from the cache kept a row's depth as the
   baseline after it had scrolled off. See `FlowController._visibleDepths`.

5. **The baseline is rebased whenever the top row changes, and items already on the display
   move with it.** Panning a whole display at a time rarely shows the same item twice, so
   there the question barely arises. Arrowing one item at a time is the case that matters:
   while the reader is in the middle of the band nothing shifts, and when a new item has to
   come in at either end the whole band rebases. An item that was already displayed does
   move. That is chosen deliberately, for two reasons — it leaves exactly one indent view to
   compute rather than one per item's history, and it is the version that keeps the display
   inside its cells.

6. **The orientation note shares the top row with its item**, rather than taking a row of
   its own: `lvl5 addons`. A whole row out of eight is too much for a label.

7. **Whether the orientation note is shown at all is computed, not fixed.** If the band's
   items still fit once the absolute depth has been drawn, there is no need to state the
   level and the note is not shown. It appears when the cells run out. Five levels is the
   fallback if computing it turns out to be more trouble than it is worth, but it should not
   be a constant if it can be avoided.

8. **A wrapped item is indented further than a child would be.** A long item that will not
   fit on one braille row must not have its continuation read as a child of itself. The
   continuation is indented past the child indent — half a level further at least — so that
   depth and continuation stay distinguishable under the fingers.

9. **How indent is drawn is configurable and may vary by application**: one space, two
   spaces, or a dots-78 marker per level.

10. **Indent is the renderer's doing, not the block's text.** Block identity, caching, the
    walk and the fetch budget never see it.

11. **Presentation is one object owning both traversal and layout.** For a tree they are the
    same decision, and splitting them would mean keeping two registries in step.

12. **Presentation is chosen from data, not from role alone.** Two objects of the same role
    are shown differently because of what they are showing. A recognition step therefore has
    to be able to name *this particular* thing — this application's folder tree, this
    website's watchlist table — and not merely its kind.

13. **Application modules are first-class participants.** An app module must be able to say
    "when table A is read, flow it by rows; for table B show one column at a time; in this
    list view ignore columns E and F". This is the same principle `ObjectAdapter.register`
    already established, extended to layout.

14. **Row versus column is a user setting, and a saved-and-restored one.** The valuable case
    is a table the reader uses repeatedly — a stock watchlist — where they have found a good
    layout once and want it back automatically.

15. **Sticky headers when reading by column, and disable-able.** A table read by column puts
    its headers at the top and keeps them there as the reader moves down. Some tables have
    no headers and some mark them wrongly, so this can always be turned off.

16. **Reading order stays.** A table read linearly, left to right and top to bottom with
    NVDA reproducing the header on each cell, is what NVDA does now, is sometimes what the
    reader wants, and comes for free. It must survive.

17. **Today's flat run is the baseline and does not move.** Nesting happens only where a
    presentation asks for it. Everything already tested on hardware keeps behaving as it
    was tested.

18. **The focused row is marked at the left, in indent that is already there.** Dots 3678 —
    the bottom four dots of the cell — in the leftmost two cells of every row of the focused block, on by default, and never where
    the row has fewer than two cells of indent to give. The cursor is not enough across
    rows: by default it is dots 7 and 8 under the text, so finding it means reading the row
    it is under, and a hand running down eight rows of a folder tree to see where it is has
    to read all eight. A mark at the left margin is in the same column on every row, so the
    same question is one pass. Costing the row no cell is what makes it safe to have on by
    default, and it is the reason it is drawn *into* the indent instead of in front of it.

19. **A column layout is a per-table setting, and a command is how it is reached first.**
    The end state is that a table the reader has laid out comes up laid out, remembered
    against that table's identity, and nothing has to be pressed. The command is what exists
    before that: it turns a column layout on for the table the reader is in and off again,
    and it stays afterwards as the escape hatch for a table recognition cannot name and as
    the way a layout gets attached to an identity in the first place — item 2 of *Saved
    layouts*, arriving early.

    Command first rather than automatic first, for a reason that has nothing to do with
    effort: most tables on a web page are not tables of data, and laying those out in columns
    would wreck pages that read perfectly well today. A command is also reversible in one
    keystroke, so a layout that comes out wrong costs the reader nothing to undo.

20. **A table's focus mark marks the cell, not the row.** In a run of objects the mark says
    which item has the focus. In a table the reader already knows which row they are on —
    they are reading along it — and the question they cannot answer by feel is *which
    column*. So in a table the mark goes at the left of the column the caret is in. It is the
    same mark, answering the question that is actually open.

21. **The flow band goes where the focus is.** On a composite display the band has to pick
    one physical display, and it used to take the tallest. The reader found what that costs:
    with the flow on the Monarch and the focus moved to the Focus 80, neither display was any
    use. The Monarch could hold nothing else, because the band owned it; and it was not
    showing what the reader was working in, because the focus had left. Two displays and
    neither of them doing a job.

    So the band follows the focus segment, which also settles what the other display is *for*:
    the one the focus is not on is where pinned objects live. Moving the focus moves both, in
    one keystroke, and the mechanism was already there — the command that moves the focus
    rebuilds the display, and the rebuild compares the band's rectangle against the claim in
    force.

    **And a display of one row is left to NVDA.** Everything a flow is for needs a second row
    to exist at all, and on one row it takes a display NVDA was already doing the same job on
    and adds two things that are wrong there: a focus mark at the left of the focused item,
    which on a single line is every line, and an indent spent on depth whose shape cannot be
    seen. The reader had this by accident until the band began following the focus — it used
    to take the tallest display and never landed on one row — and reported the focus mark
    appearing in a tree view the moment it did. `MIN_BAND_ROWS` is the floor, checked before
    the claim is made so that nothing is taken and given back.

    That moved the request to see a table in columns off the band and onto the plugin. With no
    band there is nothing to lay out, and the reader still wants to say "this table, in
    columns" so that pinning it to a display with room carries the layout — which is the whole
    arrangement they described. A request that lived on the band could not be made when there
    was no band, and refusing there was refusing the only route they had.

    Naming a display in the flow settings still wins, and now means what it says: keep the
    flow here whatever the focus does. Failing both, the tallest, because rows are what a flow
    spends.

22. **Running out of budget means come back, not stay short.** The budget exists so that one
    keypress cannot block the reader for a second on a heavy page, and that is worth keeping:
    a quarter second is already a long time to hold a hand still. But Outlook's Word view
    costs about fifty milliseconds a block eight pans into a long message, so a quarter second
    buys five blocks where the band wants eight — and the band showed two rows saying "more,
    not fetched" and went on showing them. The reader was told the content existed and given
    no way to reach it.

    So a band left short asks to be finished. Each pass gets a fresh allowance, the display is
    written only when a pass added something, and the chain ends the moment one adds nothing,
    which is what stops a document that will not answer being asked forever. Raising the
    ceiling was the alternative and it is the wrong trade: it buys the same rows at the price
    of a longer wait on every keystroke.

    The two kinds of empty row are opposites and must not be confused: `END` is the document
    finishing and `DEFERRED` is this add-on giving up part way. They read identically under
    the hand, which is why the report names which.

23. **Panning stands until the reader moves.** A live flow writes its reading position back
    as it pans, NVDA reports that position, and the band is asked to show the caret — whose
    row is the one that was just panned away from. On a band several rows tall the caret's row
    is usually still on it and nothing happens. On a band **one row** tall it never is, so
    every pan snapped straight back and the display flickered between the two. That case
    arrived with decision 21: the band had never been on a single line display before.

    So a pan is remembered, and following the cursor leaves the window alone while the caret
    has not moved and the block is still on the band. The rule it suspends is right for a
    *caret move* — a caret at the bottom of a long paragraph must not be shown by the
    paragraph's top — and wrong only when the caret did not move at all.

    The memory is **forgotten** rather than merely compared, on the first caret move or the
    block leaving the band. A reader who moves away and comes back to the same place has not
    re-panned, and a rule that re-engaged on the way back would leave the band stuck where it
    was minutes ago.

    Panning is also in the move history now. It was not, and the omission cost a diagnosis:
    the report showed six arrivals and no pans, and there was no way to tell whether the pans
    had never happened or had happened and been undone.

24. **A pinned object is read as a flow where its segment has room for one.** The other half
    of decision 21, and the reason the freed display is worth having. A pin is a document, a
    run of objects or a table just as much as the focus is, and it was being shown through
    NVDA's own regions — which for a browse mode document means the one element the cursor
    was in, one line, with the rest of the segment blank.

    Every segment is now a `FlowBufferSegment`, because until a controller is attached one
    behaves exactly as an ordinary segment does. The class used to be chosen from
    `ownerDrawsFocus`, which is a fact about the *band* — it hosts the focus and draws it —
    and had nothing to do with whether a flow could be shown. A pin hosts no focus and was
    excluded by a question it was never being asked.

    Two conditions and no more. The segment needs `MIN_FLOW_ROWS`, because a flow in one row
    is a row. And `buildController` has to find something to read, which is the same
    question, asked the same way, that decides whether the band lights up.

    **Not gated on the flow settings**, deliberately. The reader's own account is the
    argument: they turned the flow off in order to pin something at all. Those settings say
    what the band does with the focus; a pin is not the focus and is not automatic, and
    refusing to read it well because the band is reading something else badly would be the
    same frustration in a new place.

    The pin drives its flow with `rereadContent`, the same in-place re-read live updating
    uses, so a pinned watchlist keeps up and the reader's panning survives it. Three things
    the band learnt first and the pin had to learn separately, each because a pin is *not*
    where the reader is:

    - **A run of objects can be read again.** `rereadContent` leaves alone any source that
      cannot be asked for a block by its identity, and `ObjectFlowSource` could not be — so a
      pinned message list went on showing the subjects it had when it was pinned, and the
      counters reported reads that had read nothing. An object is its own bookmark, so this
      turned out to be four lines.
    - **The pinned header row is read again.** It is outside the window on purpose, so the
      re-read that walks the window's blocks never touched it.
    - **A pinned table that gains a column is built again.** The band's shape check compares
      the *caret's* position against the plan, and the caret is somewhere else entirely —
      being somewhere else is what a pin is for. So the pin asks the table instead: one cell
      read on the header row, at the column after the last one known.

    A pin that ran out of budget finishes on its own refresh tick, which is the band's
    continuation pass on a clock that was already ticking.

    **A command that moves a table means the one on the display it was pressed on.** Turning a
    page of columns belonged to the band, which was the only thing that could hold a table;
    now a pin can too, and "the table in front of you" is the wrong answer for a pin — the
    reader put it on the other display precisely so that it is not where they are working. So
    the answer is the table under the hand that pressed, which is the rule the panning keys
    already follow. Failing that, the first table in display order, which on a display showing
    one is the only one there is.

    `FlowController.useColumnPage` is what made that possible: the three things a page turn has
    to do together — tell the source which columns to read, read the rows again, rebuild the
    pinned header — moved off the band and onto the flow, because a pin is a flow like any
    other and pages like one. What the band adds is only which flow the reader meant.

    **A pinned table is read in columns if that is what the reader was already doing with
    it.** Reading order was what a pin got, which is what NVDA already gives; the shape is
    what the table was pinned for. The request is a flag of the pin's own rather than a
    reading of the band's, because the band drops its request the moment the reader leaves
    the table and pinning it is often the prelude to leaving. It is seeded from the band as
    the pin is made — `FlowBand.wantsColumnsFor` — and the command carries a change of mind to
    the pins on the same table, so nothing has to be unpinned to alter it.

    The column layout is tried and falls back rather than failing. What was asked for was
    *this table* in columns, and a table that will not lay out is still worth reading in
    order; the same fallback covers a pin outliving its table, where insisting on columns
    would leave the segment blank instead of showing what is there now.

25. **How many columns share a page is decided by how tall the row becomes, not by how many
    fit across the band.** These are different questions and the second has a much worse
    answer. A bank statement of four columns wanting 10, 8, 24 and 6 cells was laid out at
    7, 7, 7 and 8, which is exactly thirty-two with the gaps: a flawless fit on the axis
    nobody reads on, in which every cell wrapped to four rows underneath and two records
    reached an eight row display. The reason to lay a table out in columns at all is to run
    a finger *down* one, and a layout that leaves two rows on the band has spent the feature
    to buy nothing.

    So a page takes as many columns as it can without the row growing past `targetHeight`,
    which is a quarter of the band — two rows on an eight row display, four records under
    the hand. The page size is searched downwards: try every column left, work out the
    widths, predict the height, and on a miss try one column fewer, which gives the survivors
    more cells each and drops the height faster than linearly. **One column is the floor and
    it is returned without asking about the height**, which is the reader's own rule for the
    case they named: a VPAT remarks column cannot be read beside anything, so it gets a page
    to itself and the row pans. That is not a special case in the code — it is the search
    reaching one.

    **Height is counted the way the renderer stacks it.** A plan may pack its columns across
    more than one row of the band, and each of those lanes is as tall as the tallest cell in it —
    `flowRender._laidOut` stacks them by their own heights. Taking the tallest column overall
    under-counts by exactly the lanes it ignores: two lanes of two rows each is four rows on the
    band, reported as two, and the search then accepted a page it should have refused. `flowTable`
    walks the packing and sums the lanes, in `_stackedHeight` on the way in and `predictedHeight`
    on the way out, and the two are deliberately the same arithmetic.

    That also settles what `flowTableRowHeight` is. It is the number of *lanes*, not the height,
    and the settings label now says so; the height is `targetHeight`, derived from the band. Two
    numbers that sounded like one is how the miscount survived being read.

    Two supporting rules. The width is chosen for a **typical** cell rather than the widest,
    because nine remarks in ten are a few words and the tenth is a paragraph, and planning
    from the widest lays all ten out for the paragraph; the outlier then wraps taller than
    the target, which costs a keypress on the rows that earn it and nothing on the rest.
    And nothing is shrunk below `READABLE_CELLS`, which is what sends columns onto another
    page instead of onto the same page unreadably.

    The reader's own words for what this is for: "we need an algorithm that addresses
    readability", and "if a cell is still at 4+ after it is half the display, we should just
    go one column at a time".

26. **The first column is repeated at the left of every page after the first.** Six columns
    into a watchlist the reader is feeling four numbers with nothing to say whose numbers
    they are, and the symbol that would say so is two page turns back. The column that heads
    the row — the symbol, the criterion, the date — is drawn again at offset zero on each
    later page, always at the same width, so that every page answers "which row is this" on
    its own.

    **Cut rather than wrapped, and only on the later pages**, because it is doing a different
    job there. On its own page it is a column and the reader is reading it, so it is drawn
    whole like anything else; on a later page it is a label, and the first few cells of an
    identifier are a label. Nothing is lost by cutting the copy — the column itself is a page
    turn away, in full. It is capped at a third of the band for the same reason: a pin wide
    enough to hold the longest criterion would be spending on the label what the columns
    being labelled need.

    A table that fits on one page repeats nothing, so the cost is paid only where the problem
    exists. The caret entering the key column still turns back to the page the column lives
    on, because the caret being in a column means the reader is reading it and the copy is
    cut.

    On by default, as `flowTablePinKey`. In the end this is a per-table choice like the rest
    of decision 19 — which column heads the row is something the reader knows and recognition
    does not — and the same field the automatic answer is written into is the one a saved
    layout will write a chosen answer into.

## The four layers

### 1. Recognition — what is the reader looking at?

A function from what the reader is on to a small descriptor. Not a class hierarchy and not
a role lookup: a chain of recognisers, asked in order, first answer wins, with application
modules registering ahead of the built-ins exactly as `flowObjects.register(adapter,
first=True)` already allows.

The descriptor carries two things:

- **A kind** — `prose`, `list`, `tree`, `table`, `grid`, `choices`. Coarse, and enough on
  its own to pick a default presentation.
- **An identity** — a hashable key naming this particular instance, or `None` when there is
  nothing stable to name. This is what lets a saved layout come back. For a web table the
  candidates are the document's host, the table's `table-id`, and the tuple of its column
  header texts; the header signature is the one that survives a page being regenerated,
  which is the case that matters for a watchlist. For a list view, the application name plus
  the window class plus the header text tuple.

Identity is deliberately allowed to be a guess. A wrong match applies the wrong saved
layout, which the reader sees immediately and can change; a missing match applies the
default, which is where they started.

### 2. Presentation — how is that kind of thing shown?

Resolved from the recognition by a registry lookup, most specific first:

1. an exact match on identity — the reader's saved layout for this table;
2. a match on kind plus application;
3. the built-in default for the kind;
4. nothing, and the object is left to NVDA as it is today.

A `Presentation` holds a `Traversal`, a `Layout`, and the options each of them reads. It is
a data record, not a subclass tree, because the interesting variation is in the options and
because a table designer dialog has to be able to build one at runtime.

### 3. Traversal — what is the next item?

The generalisation of today's `ObjectAdapter` navigation. Same fields, with two changes.

`nextOf` and `previousOf` stop meaning "sibling" and start meaning "the next item of this
run in the order this presentation reads it". Sibling order remains the default and remains
what a flat list uses. Visible-tree order is a second implementation:

- forward: the first child if the node is expanded and has one; otherwise the next sibling;
  otherwise climb to the nearest ancestor with a next sibling, stopping at the run root.
- backward: the previous sibling's deepest last visible descendant; otherwise the parent.

`admits` stops meaning "shares a parent" and starts meaning "is inside this run" — for a
tree, a descendant test against the run root rather than `_sameParent`. This is the change
that fixes the Outlook Go To Folder case, and it is the only one that does.

A third field is added: `depthOf(item)`, answering `positionInfo["level"]` where the object
gives one and counting parents up to the run root where it does not.

### 4. Layout — how does an item become rows?

The new work. A layout answers, for one item:

- **An indent**, in cells, from a depth and a policy.
- **A column plan**, for anything with columns: which columns are shown, in what order, how
  many cells each gets, and whether an overlong cell wraps to another braille row or is
  truncated.
- **How many braille rows one item is entitled to.**
- **Pinned rows** — the header rows a column layout keeps at the top of the band.
- **An orientation note** for the top row, which is where the relative-indent depth is
  stated.

## Where each piece plugs into today's code

**`SourceBlock` grows one field.** A `shape` record holding depth, table coordinates, and
whether this block is a header. Optional and defaulting to nothing, so every existing
construction is unchanged. The source fills it; the renderer reads it; the packing
arithmetic in `flow.py` continues not to look inside blocks at all.

*Built as `depth` directly, not as a record.* One optional field for the one thing M1 needed,
because a record with a single member is a record in name only and every reader of it would
have had to reach through the wrapper for a year before the second member arrived. The
record is still the right shape for the table work, and the change when it comes is
mechanical: `block.depth` becomes `block.shape.depth` at the four places that read it. What
must not happen is a second loose field going in beside the first — the second member of the
shape is where the record earns itself.

**How indent is actually drawn.** `FlowRenderer.render` lays the block out at
`self.numCols - indentCells` instead of `self.numCols`, then prefixes each row of the result
with the indent cells and prefixes the position map with `NO_POSITION` for each of them.
Routing is correct with no further change, because positions are already per cell and
`NO_POSITION` already means "there is nothing here to aim at" — the same fact that makes the
continuation mark safe (`flowRender.py:280`). Because the layout width changed, the
`renderKey` must include the indent, or a block re-rendered at a different depth would be
served from cache at the wrong width.

**How columns are drawn.** The same trick, once per column. Each cell of a table row is laid
out in its own buffer at its own column width, and the resulting rows are concatenated
side by side, with `NO_POSITION` padding between them. That reuses the whole of NVDA's
translation, contraction and wrapping per cell, and keeps routing exact: a routing key in
column three aims at a position in column three's text. A cell that wraps produces a second
braille row for the whole item, which is what "each table row gets *x* braille rows" means.

**Sticky headers are the band's business, not the renderer's.** A pinned header is a
rendering the band holds outside the panning window: the window covers rows 1..n-1 and row 0
is drawn from the pinned rendering. This touches `FlowController`'s window arithmetic and
`flowBand`, and is the one part of this plan that reaches into code the hardware has already
validated — so it comes late, and behind its own setting.

**`isFlowable` keeps its shape.** `flowBand.py:485` continues to answer the three questions
it answers now, and the recognition step sits *after* it: it decides how something flows,
never whether it flows. The `browseMode`, `objects` and `editableText` settings keep their
present meanings.

## Trees, in particular

The tree case is small enough to be the first real target and has a live failing case to
test against, which is why it comes first.

What it needs: `depthOf` on the traversal, a depth on the block, indent in the renderer, an
orientation note on the top row, the visible-order walk, and a descendant-based `admits`.
It does not need columns, pinned rows, or persistence.

The one hazard worth naming in advance: the run changes shape under the reader when a node
is expanded. The flow already re-reads on focus change and already has `source.forget()` for
"every cached answer may be stale". An expand or collapse is not a focus change, so it will
need the same treatment, triggered from the state change. Expect this to be where the
hardware run finds something.

## Easy Table Navigator, and what it settles

The reader navigates their watchlist with **Easy Table Navigator** (Joseph Lee and Cyrille
Bougot, GPL 2), so that add-on is not a competitor to design around but the other half of
how this will be used. It is worth reading, and it is short.

**What it does.** Nothing about tables. It binds the arrow keys to NVDA's own table
navigation scripts and unbinds them when the focus leaves a table — `script_nextRow`,
`script_nextColumn`, `script_firstRow`, `script_speakRow`, `script_speakColumn`,
`script_sayAllRow`, `script_sayAllColumn`, all of them already on
`documentBase.DocumentWithTableNavigation`. Reading a whole row or a whole column, from the
current position or from the start, is NVDA's, not the add-on's. That is the lesson: the
reading is already there and a table add-on is a *layer*.

**What we take from it.**

- The test for being in a table, exactly: a tree interceptor that is a
  `DocumentWithTableNavigation`, `passThrough` false, and `_getTableCellCoords(selection)`
  not raising. `WindowsError` has to be caught beside `LookupError`.
- The dispatch: the tree interceptor's script where there is one, the focus object's where
  there is not. That is what will make M5 — Excel, the message list — the same code.
- The layer discipline. Its `event_gainFocus` rebinds or clears on every focus change, so
  the layer cannot outlive the table it was turned on for. A column layout has to drop the
  same way, or the reader carries a watchlist's columns into the next page.

**What it saves us reinventing.** `_getTableCellCoords` gives the cell as
`(tableID, row, col, rowSpan, colSpan)`. `_getTableDimensions` gives the table's height and
width. `_getTableCellAt(tableID, startPos, row, column)` is random access to any cell and
raises `LookupError` where there is no cell, which is exactly what a merged cell looks like
and is what a row with a hole in it is built from. None of that has to be written.

**And it answers the layout table worry for free.** `_getTableCellCoords` consults
`_maybeGetLayoutTableIds`, so a table marked as layout raises `LookupError` unless the
reader has turned NVDA's "include layout tables" on. The command cannot be pointed at a
page's layout scaffolding by accident, because NVDA already declines to call it a table.

**How the two compose.** Easy Table Navigator moves the caret cell by cell; this flow
follows the caret. So a reader arrowing down their watchlist gets the caret moved by one
add-on and the surrounding rows drawn in columns by the other, and the thing they cannot get
from either alone — where this value sits among its neighbours — is on the display. That is
also why decision 20 puts the focus mark on the *column*: with the arrow keys moving between
cells, which column the caret is in is the one thing the reader cannot feel.

## Tables, in two milestones

**Browse mode first.** The coordinates are already there, the random access is already there
(`_getTableCellAt`), and the reader can already move by cell with the keyboard, so a
mis-designed layout is recoverable. A browse mode table is also where the motivating case
lives — a watchlist on a web page.

**Objects second.** Excel, the Outlook message list, Explorer's details view. The cells are
children of the row and carry the table cell properties, as `RowWithFakeNavigation`
documents; the zero-width test it uses is how a hidden column is detected.

The layout vocabulary is the same for both, which is the point of doing them in this order:
milestone one proves the vocabulary against the easier source.

## Saved layouts

A layout profile is a named record: the identity it matches, the column plan, the
orientation, the row height, and whether headers are pinned. Stored per configuration
profile alongside the rest of `bmConfig`.

Three ways one is chosen, in the order tried:

1. **Automatic** — the recognition's identity matches a saved profile's. This is the case
   the reader actually wants: the watchlist comes up already laid out.
2. **Applied** — the reader picks a favourite from a short list while sitting in a table.
   This is the escape hatch for tables that cannot be recognised, and it is also how a
   profile gets attached to an identity in the first place.
3. **Default** — the built-in for the kind, which is reading order.

A table designer dialog builds one of these. It is deliberately last: the record has to
exist and be applied by hand before it is worth putting a dialog on top of it. The same
record is what an application module supplies, so the dialog and the app module API are one
mechanism, not two.

## Non-goals

- Rewriting `flowObjects.py`. The adapter registry is the right shape and grows rather than
  moves.
- Changing what flows. Recognition decides presentation, never eligibility.
- Per-sheet and per-table-within-a-page granularity in the first pass. The identity key can
  carry it later; designing for it now is how this becomes too hard to finish.
- Replacing NVDA's reading-order table presentation. It stays as the default.
- Any change to the fetch budget's shape. A column layout reads more per item and the budget
  will need re-measuring on hardware, but the mechanism is unchanged.

## Milestones

**M0 — baseline and the one untested case.** DONE, PASS. A nested `<ul>` in browse mode
flows every item; nothing is skipped, as expected, because a virtual buffer presents a
nested list as text in document order and depth there is a formatting attribute rather than
a walk.

What the run also showed is that NVDA does not indent a nested list either: it marks each
point where a list begins and leaves the reader to hold the depth in their head. So browse
mode is not merely safe for M1, it is a second customer for it — the depth is in the
document's own fields and nothing is drawing it.

That customer is **not** served by M1 as built, and the reason is worth writing down before
somebody tries it as a one line change. An object's depth costs one `positionInfo` read.
A document block's depth is in its control fields, which means `getTextWithFields` per block
— and that is precisely the mistake `flowForms` already records: reading fields on every
block of every document was a tax on all reading, to answer a question that mattered at one
block, and on an eight row band it pushed the fetch budget over on arrival. Browse mode depth
needs a cheaper mechanism than the obvious one, and it is a milestone of its own rather than
a corner of this one.

**M1 — depth and indent.** DONE, PASS, except the orientation note, which is not built.
Confirmed on a Monarch 8x32: a folder tree draws its depths, the band rebases when the true
depth will not fit, the margin does not twitch while arrowing through one level, and a
wrapped row reads as a continuation rather than as a child. What landed: `SourceBlock.depth` and `RenderedBlock.depth`, `RenderKey.indent`, `depthOf` on
`ObjectAdapter` reading `positionInfo["level"]`, the whole of `flowIndent.py`, indent drawn
in `FlowRenderer`, `FlowController._rebaseIndent` at the end of every operation, the
`flowIndentStyle` setting with its place in the dialog, and a line in the dry run report
saying which plan is in force. No traversal change, as planned: a flat sibling run of tree
items already has levels, and indenting it is visible on hardware on its own.

Two things about the shape it took that the plan did not predict:

The baseline is **the shallowest depth on the band**, not the top row's. Decision 4 says the
top row, and in the common case — panning down into a subtree — they are the same number.
They part when visible order comes back *out* of a subtree, which puts an item shallower than
the top row at the bottom of the band, and there is nothing to the left of the margin to draw
it at. Taking the minimum is the same single computation and cannot go negative.

The band does not rebase whenever the ideal plan changes, only when the plan in force stops
working — `flowIndent.shouldRebase`. Rebasing on every change would make the margin twitch
while the reader arrows through items at one depth, which is a display moving when nothing
moved. A plan is kept until it would need a negative indent, or has run out of levels, or is
for a different style or band width.

**M1's other half — the focus mark.** DONE, PASS. Dots 3678 across two cells, which is the
pattern hardware asked for after the first one was tried: the bottom four dots read as a bar
under a moving hand, and they contain the dots 78 of the level mark rather than resembling
it. `flowIndent.focusMark`, drawn by
`FlowController._markLineFocus` and settable through `flowLineFocus`. It is drawn at
assembly rather than rendered into the block, for the same reason the cursor is: the focus
moves without the rows changing, and baking it in would mean re-rendering the block the
reader left as well as the one they arrived at — and the one they left is often no longer in
the window to re-render. Which cells are indent is read off the rendering's own position map
rather than asked of the plan a second time.

Still owed: the orientation note. It is deferred rather than dropped — `IndentPlan.noteLevel`
already says when there is something to announce, and the dry run reports it; what is missing
is drawing it. It is the least settled part of the design (see open question 2) and it is the
only part that has to reach into assembled cells rather than a block's own rendering.

**M2a — the visible-tree walk.** DONE, PASS. Confirmed in Outlook's folder pane and in File
Explorer: an open folder's contents are on the display, expanding and collapsing are noticed
without an event, panning across a subtree boundary is reversible, and moving to a parent
that was not previously on the band re-plans the indent to include it.

Four defects were found on hardware after it was built, all of them about *where the band
went* rather than about the walk, and all fixed: a dry run measuring the whole composite
display instead of the band; Outlook labelling an account row and its children both level 1;
the travel direction being decided before the block had been fetched; and — the one that
took three attempts — `refreshActive` placing the window by a guessed direction before the
considered answer was ever used. That last one is why `FlowWindow.ensureVisible` now decides
the edge from where the row is rather than from what a caller passed.

Split out of M2 and done first,
because M1's indent could not be tested without it: a sibling walk only ever has one depth on
the band, so there was nothing to indent relative to anything.

`flowObjects.VISIBLE_TREE` is an `ObjectAdapter` registered ahead of `SIBLING_RUN` and
matching tree items only, so a list and a menu read exactly as they did. Forward, a node is
followed by its first visible child, else its next sibling, else the next sibling of the
nearest ancestor with one. Backward is deliberately *not* the mirror: a node is preceded by
the deepest last visible descendant of the sibling above it, because that is the row directly
above it on the screen. Getting that wrong would show up as panning back landing several rows
above where panning forward left, which is the reversibility property the anchor design rests
on, so it has a test of its own over every row of the fixture tree.

`admits` becomes a same-tree test rather than `_sameParent`, which is the change that stops
an open folder's contents being discarded. The tree is found by climbing until something is
not a tree item, rather than by looking for a container role: a tree whose container calls
itself a LIST or nothing at all is common, and a membership test that only worked for well
behaved controls would fall out of exactly the trees that need it.

Depth gains a fallback here and only here. `positionInfo["level"]` first as everywhere else,
but a tree that reports no level is the one whose depth the reader cannot otherwise learn,
and a tree walk already holds the ancestors in its hand.

**Two shapes the first cut of the walk could not read**, both found by review rather than by
hardware, and both about a tree that is not laid out the way Outlook's and Explorer's are.
Neither is confirmed on hardware, because neither has a control to hand that exhibits it —
they are tested against fixtures and should be re-checked the first time a tree that is not
Outlook's or Explorer's is read.

A tree item's children are not always its *direct* children: UIA hangs them off a grouping in
between, and so do some IA2 implementations. The walk read `firstChild`, got the grouping,
offered it as the next row, and the run refused it as something outside itself — the reader
saw a parent whose contents had vanished, which is the failure this walk exists to fix, in a
different control. `TRANSPARENT_ROLES` names the wrappers to step over, in both directions
and in the depth count. Named rather than "anything that is not a tree item", because the
thing past a *real* boundary is also not a tree item and treating those as scenery walks
straight out of the control.

And a provider that fetches children on demand reports a node open before it has any. The
state is settled from the first moment and the rows arrive later, so `shapeChanged` watching
only the state went on showing an open folder with nothing in it. It now watches two things:
the state, and whether an open node is showing anything. The second question is asked only of
a node that says it is open, so a reader in a list, a menu, or on a leaf pays nothing.

**Opening a node had to be noticed without an event.** Pressing right arrow on a folder
changes what the rows below it are, and NVDA reports it through nothing the band sees: the
focus does not move, no fresh focus regions are built, and the object being read is the one
already being read. `ObjectFlowSource.shapeChanged` answers it from one object — the one the
reader is on, since that is the only one they can have opened — and `FlowBand.recheck` asks
before each redraw, which is the same place and the same reason browse mode giving itself
back is already noticed.

That rebuild has to drop the controller first. `showObject` recognises the same run and takes
a fast path for it — set the current object, follow the cursor, redraw — which is right for
the case it was written for, where the focus changes on every arrow key, and exactly wrong
when the run itself is what changed. Without dropping it the display kept the closed folder
and moved its cursor about inside it.

**M2b — presentation registry and recognition.** Not started. The `Presentation` record, the
registry with its four-step lookup, identity-based recognition, and the application module
registration API. Deferred behind M2a because the registry's value is in telling two things
of the same role apart, which is a table problem before it is a tree problem — `ObjectAdapter`
plus `register` already carries the tree case. Outlook's Go To Folder is M2a's acceptance
test, not this one's.

**M3a — the column vocabulary.** BUILT, ON HARDWARE. Every table laid out on the Monarch
is this arithmetic, and the widths it chooses have been read for weeks. `flowTable.py`: `Column`,
`ColumnPlan`, `Measurement`, `planFor`, `shouldReplan`, and the packing across band rows.
Pure arithmetic, no NVDA, the way `flowIndent` is.

Two things it decided that the plan had left open. Open question 4 — the default column
width — is answered by measuring what is in the column and fitting from the widest down,
rather than by dividing the band evenly, because even division is wrong for exactly the
watchlist that motivated the milestone. And the plan is made once and **kept**, which is the
opposite of what indent does: a margin two cells out is a small wrong, while a column that
moves is a column the reader has to find again.

A number worth knowing: shrinking runs all the way to `MIN_COLUMN_CELLS` before anything is
dropped, so a 32 cell band holds eight columns before it loses one. Tables that overflow are
tables with many columns, not tables with wide ones.

**M3b — drawing a row in columns.** BUILT, ON HARDWARE, with routing into a cell confirmed. `TableRow`, `RowCell`, and
`FlowRenderer._asColumns`. Each cell is translated in a buffer of its own at its own
column's width, because translating the joined text would make the widths character counts,
and in a contracted table those are not where the columns are. Positions are packed — which
column, and where in it — so routing lands in a cell of the table.

**M3c — reading a browse mode table.** BUILT, ON HARDWARE. The source, the command, and
`flowTableSource`. `_getTableCellCoords` recognises, `_getTableCellAt` fetches, a block is a
row, and reading order remains the default until the command is pressed.

Five defects came out of the first hardware runs and every one was about the band's
*ownership* of the reading rather than about tables:

- A `TableRow` that carried the attributes this add-on asks a region for, rather than being
  a `Region`. NVDA's buffer asked it for `hidePreviousRegions` and raised mid update.
- A row with no `obj`, which `BrailleHandler.handleCaretMove` compares before it queues
  anything — so every caret move was dropped and the band never redrew at all.
- `_isCurrentDocument` answering yes to a table flow, because a table flow's source *is*
  reading the document. The band kept the columns for the rest of the page.
- `recheck` told to leave tables alone, when it is the only thing called often enough to
  notice a caret leaving one.
- `_isIn` calling the page a thing inside the page, so a rebuilt band read "at" the document
  and landed on its first line.

The pattern is worth keeping: **a table flow reads a document without being the document's
own reading**, and every fast path that recognises "the same document" had to be taught the
difference. Nothing in the list is about columns.

**A long cell wraps within its column, and cutting is a setting.** The first cut of this had
it the other way round and hardware said no: truncation keeps a beautiful shape down the band
and the reader cannot tell a cell that ended from a cell that was cut. Wrapped, the table row
grows as tall as its longest cell needs, the continuation is indented within the column — two
cells, less on a narrow one, and less than a list spends because a column has fewer cells to
give — and the cut is by cell rather than by word, because a six cell column has no room to
keep words whole.

Cutting stays, as `flowTableTruncate`, off by default. It is what a reader who knows the
table wants: an options watchlist has symbols long enough to push every other column into
uselessness, and somebody who can recognise a contract from its first few cells would rather
have the whole table under their hands. That is a judgement about a particular table, which
is what a setting is for and what a default must not assume.

No persistence and no pinned headers yet.

**M3d — readability, paging and the repeated column.** BUILT, ON HARDWARE. Paging has been
through several hardware rounds, and what those rounds were about was never the arithmetic:
they were about the band being rebuilt under the reader while they paged. The repeated key
column is in daily use and is what orients the reader on a later page.
Decisions 25 and 26, and the answer to what the reader saw on a 29 column watchlist: eight
columns of three cells where "310.34" came out as "3".

`rowsNeeded` is the arithmetic that was missing — how many band rows a cell of a given length
takes in a column of a given width, the continuation indent included, which is why it is not
a division. `planFor` searches the page size downwards against it, `Measurement.typicalWidth`
carries what a cell usually holds so that one long cell does not lay out the other nine, and
`ColumnPlan.assignment` records which columns landed on which page.

**The assignment is a field rather than a calculation, on purpose.** The custom layout of
decision 19 is "show these columns together and those apart", which is exactly this tuple
with different contents. Deriving it on every call would leave the reader's grouping nowhere
to live; making it a field now is small, and making it one later under a shipped feature is
not.

Two things this found that reasoning had not:

- A step written to hand a page's unclaimed cells to the column that would wrap turned out to
  be **unreachable**, and reverting it changed nothing across the whole suite. Every column
  already starts at what its widest cell asked for, capped at the band, so a set of columns
  whose wants fit has no column below what it asked for and there is nothing to hand out. The
  cap plus the shrink already fill the page. The code is gone and the reason is recorded where
  it was, because it is the kind of thing that gets written twice.
- The measured numbers for the reader's own tables, which is what the rule has to be judged
  by. The statement goes from four columns wrapping to four rows each, to three columns with
  the description at twelve cells and a row two band rows tall. The 29 column watchlist goes
  from eight columns of three cells to ten pages of the symbol plus three columns at seven,
  every row one band row tall.

**A page the reader turned to holds while they can feel where they are**, and it took two
hardware reports, one either side of the answer, to say what that means. The first rule
brought the page back to the caret's own column on every move: on any page past the first,
one press of down arrow put the reader back on page one, because table navigation keeps the
column while moving the row, so the caret was still in column one and the band went there.
The fix for that held the page against every caret move, and was worse the other way — the
caret walked off the page, nothing brought the display back to it, and a reader arrowing
across the columns had no idea where their cursor had gone.

What settles it is the rule braille tethering already follows, one axis over: the display
may travel, and a caret that moves somewhere it is not showing brings it back. So the
question asked on each caret move is not *whose page is this* but **is the cell they moved
to on the display**. With the key column pinned at the left of every page — which is the
default, and what a reader paging a watchlist has on — column one is on every page, so
reading down a column never disturbs the page they turned to. Move to a column this page
does not draw and the band comes to it.

`ColumnPlan.drawsOnThisPage` is where the distinction lives, and it is the one place the
pinned copy has to count as the column it copies: `pageOf` says where a column *lives*,
which for the key column is its own page and not the several it appears on. The band keeps
no state about pages at all now — the rule is a question about what is drawn, asked fresh.

What is still for hardware: whether ten page turns for a twenty-nine column watchlist is a
worse trade than a narrower symbol column would be.

**Paging must survive the cursor being somewhere undrawn.** Reported from hardware three
times, and it took all three to find the bottom of it. The layout command starts the reader on
a cell with data, so paging works; quick navigation and the arrow keys land them in the *first*
cell, which on this watchlist is a blank icon column — and from there the display "tries, then
just repeats the first columns", will not pan, and redraws continuously while the numbers sit
still.

**The bottom of it was a saved layout — and the layout had saved something nobody chose.**
The remember command wrote down the columns *as they were drawn*, and those are a measurement
rather than a decision: a column blank in the bandful that was sampled is not drawn, so one
press of one key froze the reader's first column out of every later reading of that table.
They had configured nothing at all. `_asTheReaderWantsThem` then cut the measurement down to
the named columns, so every other column was, to `ColumnPlan.knows`, a column from some other
table — which is the one thing that means "this layout is not of this table, read it again".
The caret sitting in one rebuilt the band on every redraw.

Two changes, and the first is the one that should have been true from the start:

- **A record saved by one press says only "lay this table out".** The columns are measured
  afresh every time, exactly as they are for a table with no record, so a column that fills in
  later comes back. The field is kept and still honoured, because a record that names columns
  is what an older build saved and what M7's designer or M6b's favourites will save — when
  there is a way for a reader to *choose* columns, there will be something to write there.
- **`knows` has two ways to know a column it does not draw**: `omitted`, which the measurement
  found empty and is worth one look, and `excluded`, which a layout names out and is worth
  nothing at all. Both stop the rebuild; the second is what makes a record from an older build
  harmless.

Three more, each found by the reader or the reviewer on the way down:

- **The same cell could ask for a rebuild forever.** A cell that answers "I hold something"
  and a bandful of rows that says the column is empty can disagree, because the sample is
  bounded. An undrawn cell is looked into **once**, whatever it answers — the flag that let a
  live pass ask again is gone, since asking cost a 44 ms search of the document against 169
  change notices while the reader sat still. What that gives up is a value appearing in the
  cell while they stand on it; moving off and back asks again.
- **The cache is keyed by the table as well as the cell.** Two tables have a row 1 column 1
  apiece, and a review watched the second inherit the first's answer.
- **A rebuild put the reader back on page one, at the caret's row.** They lost both axes of
  where they were reading, which is what "cannot pan" was: a pan down, then a rebuild, and the
  band at the caret again. The page and the top row are now carried *into* the build —
  `buildTableController` takes `atPage` and `atRow` — so what reaches the display is the
  reader's own window and not page one first. A review measured that as two writes for one
  rebuild, both sent to the driver.

The exception is the one rebuild the reader caused: a value found in the cell they are
standing in. There the band goes to them, because refusing would be hiding the thing that made
the rebuild worth doing. `_rebuildBecause` records which branch fired, with the table's shape,
the caret, the page and the columns left out — every rebuild is a normal branch, so nothing
raised and nothing appeared in NVDA's log while a reader watched their display rebuild itself
in a loop.

**A column can answer and hold nothing, and that is not the same as not being there.** The
first cut of the phantom-column test asked whether any sampled row had a cell at a
coordinate, which catches a merged cell — the coordinate raises — and misses the case that
turned up on the reader's own watchlist. Its leftmost column is icons NVDA cannot read: there
is a cell, it is simply empty, in the header and in every row. Measured it came to nothing,
was drawn at `MIN_COLUMN_CELLS` anyway, and cost four cells of a thirty-two cell band on
every row. Worse, being the *first* column it was what decision 26 pinned to every page, so
the thing repeated to say which row the reader was on was a blank.

**And empty in the sample is not the same as empty.** `measure` reads a bounded eight rows,
so a column blank throughout them is only *tentatively* empty: right for the column of
unreadable icons, wrong for a column that happens to be blank in the rows that were looked at
and holds a value further down. Left as a decision that value could never be reached — the
column is not drawn, arriving at it changes nothing, and the cursor vanishes because the row
on the band has no cell there.

Proving a column empty needs reading all of it, which a wide table cannot afford. So the band
asks about the one cell the reader has just arrived at, once, when the caret enters an omitted
column: a single search, and it answers the only case that matters, because a column nobody
visits does not need to be drawn. Asked once and not per redraw, which is what made the
identical-looking bug below so expensive.

Once, **and again on a live pass**. Standing still is what makes the check go quiet, so a page
that fills the cell the reader is standing in would otherwise never be noticed — the one place
where the value they are waiting for is the one they cannot see. A live pass is not every
redraw, which is what makes that affordable.

**Not drawn is not the same as not known.** Quick navigation to a table lands the caret in its
first cell, which on this watchlist is one of those icons, and `_tableChangedShape` asked
`pageOf` — where a column is *drawn*. An undrawn column read as a table that had changed under
the band, so the layout was rebuilt on every redraw and panning did nothing at all: each pan
was undone by the rebuild that followed it. `ColumnPlan.knows` is the honest question, and it
counts a column this plan measured and deliberately left out.

A column whose widest sampled cell is empty is now not drawn, in the measuring and again in
the arithmetic — the two agree on purpose, since `Measurement.hidden` is what a measurer says
about a table it can see and the second is `planFor` refusing to spend cells on a column with
nothing to put in them, whoever measured it. `ColumnPlan.omitted` carries the ones left out
and the report names them, because a missing column is the one kind of wrongness a reader
cannot feel: the band looks like a table with fewer columns in it and nothing says whether
that is the table or the layout.

**A table laid out in columns reads itself again while the reader watches it.** A watchlist
during market hours changes under the reader's hand and nothing tells the band, so a price
felt on arrival stayed felt. Half of that is NVDA's and is the right answer to a different
question: it reports a cell's new value only while the browse mode caret is in that cell,
which is correct for speech and for a display showing one cell at a time, and no answer at
all for a display showing a page of a table at once. Half is this add-on's, which held rows
it had read and had nothing that would read them again.

`FlowController.rereadContent` is the second half, and it is the page-turn machinery under a
different name: the window keeps its place, the blocks keep their identities, and what
changes is what the source now says is in them. A reader whose hand is on row four wants row
four to hold this second's price, not to be moved somewhere while the band starts again. It
writes the display only when the cells came out different — the `_settle` pattern, for the
same reason.

**What drives it is NVDA telling us, not a clock.** The first cut used a two second timer and
the reader asked the right question of it: how does NVDA know, and is it only ARIA? Reading
NVDA's own source settled it, and the answer is better than the assumption behind the timer.

- `nvdaHelper/vbufBackends/gecko_ia2/gecko_ia2.cpp` invalidates a subtree on
  `IA2_EVENT_TEXT_UPDATED`, `TEXT_INSERTED`, `TEXT_REMOVED`, `EVENT_OBJECT_REORDER`,
  `NAMECHANGE`, `VALUECHANGE`, `DESCRIPTIONCHANGE`, `STATECHANGE`, the selection events and
  more. **No live region test anywhere in that path.** `VBufBackend_t::update` re-renders the
  invalidated subtrees and splices them in, so the buffer is fresh whether or not the page
  uses ARIA correctly — which is why the timer worked at all.
- That splice calls `nvdaControllerInternal_vbufChangeNotify`, which reaches
  `VirtualBuffer.changeNotify` and then `braille.handler.handleUpdate`. So **NVDA already
  raises exactly the signal wanted**, per document and coalesced by the render thread.
- NVDA's own use of it is to mark the caret's region for update, and
  `TextInfoRegion.update` re-reads the reading unit *at the caret*. That is the whole of why
  a watchlist read in columns went stale while NVDA looked right: there was never anything on
  NVDA's display but the one line it was already refreshing.

So `patches` wraps `VirtualBuffer._handleUpdate`, tells the band, and lets NVDA have its
update. `FlowBand.documentChanged` answers it, and the answer is bounded twice: only a
document the band is reading a table out of, and only the rows on the band at the columns of
the page being drawn. A change three screens down the page costs the read of what the reader
is touching, which is the read that was happening anyway.

`LIVE_TABLE_SETTLE_MILLIS` is a quarter of a second and it is not a poll — it is how long the
news is allowed to settle, because a page repricing thirty rows sends an event per region it
touched and that must not become thirty reads of the same band. News arriving during a settle
does *not* push the pass further out; on a page that never stops changing, restarting the wait
would starve it entirely.

That wrapper is the first patch this add-on installs on a class other than `BrailleHandler`,
which is why `patches` now remembers an owner per name.

`flowLiveSeconds` is zero by default, and zero means wait to be told rather than never:
`FlowBand._pollMillis` falls back to a timer when the wrapper is not installed, so a build
where it cannot go in loses nothing silently. A number set by hand polls as well, for a page
that changes without saying so. `flowLiveUpdates` turns the whole thing off.

The dry run reports whether the notices are arriving, how many have, how many passes ran and
how many redrew — because whether the event reaches the band at all is the one thing about
this a reader cannot feel.

**And it is not a table feature.** The reader proved that on one page: read as a table the
prices moved, read as ordinary browse mode they sat still, and there is nothing about a table
that makes it the dynamic one. NVDA refreshes the caret's line because the caret's line is all
it shows; a band showing eight lines is showing seven that nobody refreshes. So
`DocumentFlowSource.blockAt` exists too, and the band answers a change in whatever document it
is reading.

Three rules keep that honest, and each is a decision:

- **A block is only given back when it still begins where it did.** A document bookmark is an
  offset, so text growing or shrinking *earlier* in the page moves every block after it: the
  cached position then lands inside some other unit, and expanding it would hand back a
  neighbour under this block's name. One line of the page drawn twice is worse than one line
  out of date, so the block is refused and the reader's next keystroke is the repair. Text
  changing *within* a block moves nothing, and that is the case this exists for.
- **A control's block is left alone.** It carries what it is as well as what it says — that it
  is a control, and that a gap follows it — and a plain re-read of the position gives back
  neither. Its text is also the one thing on the band NVDA keeps fresh on its own account.
- **Nothing is re-read while the reader is typing.** Everything moves on every keystroke and
  the block they are in is the one being edited; following that would fight the editor rather
  than follow it. `DocumentFlowSource.writing` already knew.

Four narrower things the same review found, each worth its own line because each is a
promise that was made and not kept:

- **Every path that shows something starts the chain.** The table path called
  `_scheduleLiveRead` and the ordinary document path did not, so a reader with a refresh
  interval set got one on a watchlist laid out in columns and none on the same page read by
  line. `FlowBand._attach` is now the single door, and it also drops the pass belonging to
  whatever was being read before.
- **`liveUpdatesInstalled` answers about a document, not about a dictionary.** The band polls
  only when the answer is no, so a yes it cannot back up costs the reader their updates
  silently. It now checks that the method in place is still the one this module installed, and
  that the document is of the class that was patched — a UIA browse mode document is not.
- **The pinned header row is read again on a live pass.** It is outside the window on purpose,
  so the re-read that walks the window's blocks never touched it and a renamed header went on
  saying what it used to.
- **The shape check runs on every live pass.** A column appended beyond the page being drawn
  changes none of the drawn cells, so the pass wrote nothing and the table that grew stayed
  invisible until something else caused a redraw.

An object run answers none of this and should not: an object has no such thing as "the same
block, read again" — the objects themselves are the identity, and NVDA's own events say when
one of them changed. That is `FlowBand._canReadAgain`, which asks the source rather than
asking what kind of thing is being read.

**A document being written in answers none of it either**, and the pass was still being
scheduled for one. `_rereadBlocks` refuses a written-in source outright — every position moves
on every keystroke — so each pass re-rendered the whole band to arrive at the same cells. A
markdown file open in VSCode has no change notices behind it, so it fell to the two second
fallback and ran a hundred and eighteen times in one sitting. `_canReadAgain` now asks the
same question `_rereadBlocks` does, before the timer rather than after it.

### A pan is not a keystroke

The same report, and the same root: **what a band is showing and where the reader is are two
questions, and a fast path keeps asking the nearer one.** VSCode's editor is a plain editable
text with no browse mode behind it, so a flow over it counts as written in for as long as the
reader is in it — and a written-in document re-reads the whole band from the caret on every
display update, through the settle pass. The reader's report: it "was panning by one line
instead of whole display and at the point of this log it would not pan forward at all. pan
back seems better."

Finding the caret was never the problem. Panning a live document takes the cursor with it, so
entering at the caret lands on the row the reader panned to. What lost the pan was the
anchoring afterwards, and **both of its branches lose it**:

- `_stableTop` can never accept the row the band is actually showing, because `_cursorToTop`
  has just made that block the caret's own and the top row is only believed when it is not. So
  the restore target is always a top row from *before* the pan. Reach it and the band goes back
  to the window the pan left: the pan did nothing at all.
- Fail to reach it — `_restoreTop` only ever fetches backwards — and `_contextAboveTheCaret`
  runs instead, putting the caret half a band down from the top. The band lands half a display
  behind where the pan put it: eight rows forward, four given back.

Which is both symptoms, and the asymmetry too: half a display of drift backwards adds to a
backward pan and subtracts from a forward one, so "pan back seems better" was the same bug
seen from the other side.

**A caret moved by panning has two right answers.** The first cut of this asked the document
where its caret was in the same breath as telling it where to put it, and those are a question
and a request: the read-back can still be the caret the pan moved *from*, and the settled
answer arrives with the caret event a moment later. Which meant the claim held only when the
pan happened not to move the caret at all — when the new top row continued the block the caret
was already in — and missed whenever the top row began a new block. The reader felt it as
panning that went "from moving the full display, to moving just a couple lines", varying on
nothing they could see.

So the claim is a pair: where the document says the caret is, and where the pan *asked* it to
go, which is taken from the block rather than from the document because that half is knowable
without waiting. A caret move lands on neither. `FlowController._caretsAfterAPan`.

The pair is trusted only while the application is between the two. A review found that both
stayed trusted for ever, so a reader who panned and then went straight back to where they came
from — control+home, a routing key onto the line they left — was still counted as not having
moved, and the band stayed panned. Seeing the caret at the settled answer now drops the one it
was catching up from.

The decision: **a pan the reader has not moved away from is not a keystroke, and costs the band
no reading at all.** `FlowController._caretIsWhereThePanLeftIt` is asked first, before the
re-read that would decide where the band goes, and it is answered from the document's own caret
— `DocumentFlowSource.caretPosition` — rather than from anything the flow rendered, which is
what lets it be asked that early.

**Not "has anything been typed", which is a different question.** The first cut of this said
typing moves the caret, and the same review named the counter-example: forward Delete takes out
the character *after* the caret and leaves it exactly where it was, as does an editor rewriting
the line under it. A caret that has not moved says the window is still the reader's; it says
nothing about the row they are standing on. So that row is read again — through its own region,
costing the source no fetch — and it is the only one that can be: after an edit every position
later in the document has moved, which is why `_rereadBlocks` refuses the rest.

Two smaller things fall out of it: a pan forgets `_writingTop`, because the window a re-read
would restore is one the reader has just left deliberately, and the move history now names
*which* branch of a writing re-read ran. It recorded "nothing moved" — the verdict of the
`syncToCursor` at the end — on a pass that had already thrown the reader's window away and
rebuilt it somewhere else, which is why four hardware reports were needed to find this.

**M4 — pinned headers.** BUILT, ON HARDWARE, for browse mode tables and for object tables
alike — a dry run of the reader's watchlist shows the header row held above the band, and File
Explorer's Details view pins the headings its cells declare. Brought
forward from last because the reader met the hole it fills: a column layout turned on from
the middle of a table showed the columns and never said what any of them was. The window
starts where the reader is, so the header row was on the band only if they happened to have
entered at the top, and scrolling back up to look is not an answer — the answer is wanted
while reading somewhere else.

**A pinned row is outside the window.** `FlowController.pinnedBlock` holds one block, drawn
on the band's top row, and the window is built one row shorter to make room. That is the
whole of the band change and it is why it turned out not to disturb the window arithmetic
after all: the window is simply smaller, decided once when the controller is built, so
nothing under the pinned row ever moves because of it. Everything the window answers about
rows — the focus mark, the cursor, routing — is worked out in the window's own coordinates
and then moved down by a row.

Three details that are decisions rather than mechanics:

- **The pinned header is cut, never wrapped, and is always one row.** A pinned row that
  changed height would move every row under it, which is the one thing a pinned row exists
  not to do. `ColumnPlan.cutting` is what it is drawn with. The same argument as the repeated
  key column: a header is orientation and not the data.
- **The source stops serving the header row.** With `firstRow` at two, row one is the pinned
  copy and nothing else, so it is not drawn twice at the top of the table and missing
  everywhere else. A reader whose caret is in the header row is looking at the pinned copy,
  and the cursor shows there — `_pinnedCursor` asks the pinned block's own region, because a
  pinned row is never the active block.
- **It is rebuilt when the page of columns turns**, for the same reason the rows are: it
  holds the old page's cells at the old page's offsets.

`flowTableHeaders`, on by default. Off is worth having on a short band, where a row is a
large share of what there is, and for a table whose first row is not headers — `HEADER_ROW`
is one, which is what NVDA's own table navigation assumes and what this does not try to
guess at.

**Object tables pin a header row too, since the `ControlField` fix** — the heading a list
view's cells carry now reaches `declaredHeader`, where before every one of them was thrown
away by a type check. A list view has no header *row* to borrow, so what is pinned is what
its cells declare, and a table that declares nothing pins nothing rather than pinning its
first file. Still not done: any notion of a header that is not row one and is not declared.

**M5 — tables as objects.** BUILT, ON HARDWARE, all three sources. Excel, list views, the
message list. Same vocabulary, second source. File Explorer's Details view and Outlook's inbox
both lay out in columns, with the headings their cells declare pinned above them. **Excel is on
hardware too** — a third shape of table object, read by coordinate, reached through the add-on's
first application module, and read again by `chartSource.py` when a chart is drawn from a
selection. Several sheets and a long debugging pass went into it; what they found is written up
below.

Getting there took five hardware readings and a review, and what they found was never the
column arithmetic: it was which object is the row (Outlook's rows carry the cell properties
their list does), which children are rows (a pane, a scrollbar and a header come first), which
numbers count the table rather than a group, whether a heading reaches the code that reads it
(it did not, for months, behind a caught `ValueError`), and whether an object may be read
again while the reader is somewhere else. Each is written up above. **The vocabulary above the
seam never changed**, which is the thing the two-milestone split was betting on.

The seam turned out to be one that was already there. `flowTableSource` walks
`documentBase.DocumentWithTableNavigation` — which cell is the cursor in, how big is the
table, give me the cell at these coordinates — and that is the whole of what reading a table
needs. A list view is not one of those, so `flowObjectTable` presents one as one, and
everything above the line is unchanged: the measurement, the column plan, the pinned header,
the key column, the page turns, the window, the band. `tableDocumentFor` said this was what it
was betting on before there was anything to bet, and the bet paid.

What the stand-in answers, and where each answer comes from:

- **The rows are the list's own children, and its shape is its own answer.** `rowCount` and
  `columnCount`, which NVDA's list view gets from one window message each; counting children
  would build every item to learn a number the control already knows.
- **A cell is `_getColumnContent`.** `NVDAObjects.behaviors.RowWithoutCellObjects` is the
  contract — a row whose cells are not objects — and `sysListView32` implements it through
  NVDA's own in-process helper. The contract is asked for rather than tested with `isinstance`,
  so an application module answering it on a class of its own works too.
- **A header is `_getColumnHeader`**, handed on as the same `table-columnheadertext` control
  field attribute a browse mode document carries, so `declaredHeader` needs to know nothing
  about where the table came from. A list view has no header *row*, so no item of the list is
  spent on one: `firstRow` stays at one and every item is content.
- **A hidden column is one whose rectangle has no width.** `_getColumnLocation`, which is the
  test `sysListView32` itself uses. Better than the browse mode source's, where a hidden column
  has to be *inferred* from every cell in a sample coming back empty.
- **Which row the reader is on is `rowNumber` or `positionInSet`**, never counting. On a
  mailbox of ten thousand messages that is the difference between a keypress and a pause.
- **Routing into a cell focuses the row.** A document cell can be pointed at; a list view cell
  cannot, because a column is a rectangle on the screen and not a place the keyboard can be
  put. The item is the only thing there is to go to, and going to it is what the reader means.

Two things the seam forced, both worth writing down:

- **A stand-in is not the object.** `TableRow` carries the object NVDA compares a region
  against, and the stand-in is not anything NVDA has heard of. The source now keeps the real
  object — the document for a page, the list for a list view — and hands that to the row.
- **Identity is not everybody's answer to "the same table".** A stand-in is built afresh each
  time the reader is asked where they are, over an `NVDAObject` that NVDA also builds afresh
  whenever it is asked. Comparing by identity said "a different table" on every arrow key, so
  the layout would have been given back each time the reader moved to the next message. A
  thing with a better answer says so by offering `sameAs`; `sameTable` asks. NVDA's own answer
  for two objects is equality, so that is what a list view's stand-in uses.

### There are two shapes of table object, and Windows 11 has the other one

The first version of this knew only `RowWithoutCellObjects`, and the reader tried it in the
two places it was written for. Both said *"not in a table."*

`NVDAObjects.behaviors.RowWithFakeNavigation` is the other shape — "the cells must be exposed
as children and they must support the table cell properties" — and it is what both of those
places actually are. Outlook's message list rows are `outlook.UIAGridRow`, whose children are
the fields; File Explorer's file list is a UIA grid whose cells carry `GridItemPattern` and
`TableItemPattern`. `RowWithoutCellObjects` is the classic Win32 list view, which is still
there and still worth reading, but it is no longer the common case.

**Where the focus lands differs and there is no arranging that.** Outlook focuses the row;
File Explorer focuses one *property* of the file — `appModules.explorer.UIProperty`. So
`tableFor` asks three questions in turn — is this a row that answers for its own cells, is it
a cell, is it a row whose cells are objects — and the first yes decides both the row and the
shape. A recognition that only knew how to spot rows said "not in a table" while the reader
was standing in one.

Everything the second shape needs is an NVDA property already: `columnNumber` from
`GridItemPattern`, `columnHeaderText` from `TableItemPattern`, resolved to the header
elements' text by NVDA — the same work `appModules/outlook.py` does by hand when it names a
row for speech.

Two decisions inside it are worth keeping:

- **A cell is found by its own `columnNumber`, not by position.** Position is what is left for
  a row that numbers nothing, and where a row *does* number its cells and none of them is the
  one asked for, the cell is genuinely missing — falling through to position there would hand
  back a neighbour under this column's name.
- **A name that is only the column's header is a label, and the value is the content.** File
  Explorer's property cells are named "Status" and valued "Always available on this device". A
  column already says what it is — that is the whole argument for laying a table out
  spatially — so a name that only repeats the header says nothing. The two are told apart by
  asking, not by knowing about File Explorer.

Three more things the file list taught, all of them the same lesson — **the number a control
answers is not always the number the reader is standing in**:

- **How many rows there are is what the row says it is one of.** File Explorer answered
  fourteen to `childCount` while the reader stood on item fifty of seventy-nine: the platform
  builds the items on screen and no more. `positionInfo["similarItemsInGroup"]` is the whole
  list, and it is what NVDA speaks. The band had shown one row and said the table ended.
- **A row is reached by stepping, not by indexing.** Asking for the sixtieth child of a list
  that has built fourteen answers nothing at all. Stepping is what NVDA's own object
  navigation does — and what this add-on's run-of-objects flow was already doing successfully
  in the very same list, in the very same report. Rows stepped past are remembered, so reading
  on costs one step; a jump too long to walk is handed back to the table by number.
- **A cell's value is its content and its name is its label.** `explorer.UIProperty` is
  documented as "used for columns in Windows Explorer Details view", and one of them is named
  "Status" with the value "Always available on this device". NVDA speaks both, because on one
  line the label says what you are hearing; a column has already said that. The first attempt
  told the two apart by comparing the name against `columnHeaderText` — which came back as
  "Column left" — so it drew the column's own heading as its content on every row. A cell that
  carries both has also *named its own column*, and that first-hand answer is now preferred
  over resolving whatever elements the platform points at as headers.

The next report settled the rest of it in one reading, which is what the diagnostic was for:

- **`rowCount` is not safe either.** The file list answered *fourteen* to `rowCount` and
  seventeen to `childCount` while the reader stood on item fifty-two of seventy-nine. Trying
  it first was the previous fix, and this report disproved it. The row's own "one of N" comes
  first now, and the shape is never smaller than the row the reader is standing in — a table
  cannot have fewer rows than that, and a count saying otherwise is counting something else.
- **A list has no header row, so its first item is never pinned as one.** With the headers
  read correctly from the cells this no longer bit, but the fallback was still there waiting:
  a table that declared nothing would have had one of the reader's own files drawn above the
  rest and called a heading, and the item under it dropped from the stream. Whether row one
  may be read as headings is now asked of whatever is navigating the table — a document says
  nothing and gets the assumption `HEADER_ROW` is named for, a list view says no.
- **A row's children are its cells when the table says how wide it is.** Outlook's message
  list rows are `outlook.UIAGridRow`, a `RowWithFakeNavigation` whose contract says outright
  that "the cells must be exposed as children" — and the row carries `GridItemPattern` while
  its children carry nothing. Asking only the children said "not a table" about a table whose
  own row had just said it was one. Either answer will now do, and an ordinary list is still
  left alone because its parent has no column count.

And the diagnostic that should have existed first: `ObjectTable.describe` writes what it
found — the shape and which of the three places it came from, the table and row objects, and
each column's text and header. The report before it took a guess at which of four answers was
the wrong one. This is the sixth time this project has been told that when reasoning cannot
settle a question, the thing to improve is the diagnostic.

### Excel: a third shape, and the first application module

**A spreadsheet is neither of the two shapes already built.** A row whose cells are objects is
walked child by child; a row that answers for its own cells is asked column by column. A sheet
is neither — its rows are not children of anything — and a cell is reached by saying which one
you want. That is exactly the question `_getTableCellAt` asks, so it is the closest of the
three to what the rest of the add-on already wanted and the smallest to write:
`flowObjectTable.SheetTable` answers coordinates outright and overrides away everything the
other two work out by walking. There is no run of children to count, no decorations to skip,
and no `positionInfo` to interrogate about whether it numbers the table or a group of it. A
spreadsheet says which row and column a cell is, and means it.

It is also the one place NVDA has no generic answer, and that is what decides the design.
Everything else a table is asked — what a cell says, which row and column it is, what its
column's header is, where the caret goes when a routing key lands on it — is already on
`NVDAObject`, implemented once per accessibility API and tuned per application, and is asked
of the object exactly as it is for a list view. *The cell at (row, column)* exists only on
`DocumentWithTableNavigation`, for text documents. Supplying it, per application, is the whole
of what an adapter is for.

**So Excel lives in an application module, and it is the only file in the add-on that knows
Excel exists.** The reader asked whether this could be done instead of loading it into the
global plugin, and it can, for a reason worth stating: a global plugin is resident for the
session and asked about every object in every application, while an application module is
loaded when its application runs and unloaded when it stops. What makes the split possible is
that the seam is *one method*. An overlay class on a worksheet cell offers `brlMultilineSheet`;
`flowObjectTable.sheetOf` asks every object it meets whether it has that name, which is one
attribute lookup that fails on everything else; what comes back answers four questions —
`obj`, `shape()`, `where()`, `cellAt(row, column)`. `flowObjectTable` mentions no application,
and `appModules/excel.py` mentions no braille.

Two details that had to be checked rather than assumed:

- **An add-on's application module replaces the built-in one of the same name**, so ours
  extends NVDA's rather than standing beside it: `from nvdaBuiltin.appModules.excel import
  AppModule as ExcelAppModule`, subclass, and call `super` in
  `chooseNVDAObjectOverlayClasses`. `nvdaBuiltin` exists for exactly this. Everything NVDA's
  module does — the formula bar redirect, the broken data validation list, the UI Automation
  window choices — goes on happening.
- **Overlay classes reach cells that NVDA builds directly**, not only ones that arrive from an
  event. `DynamicNVDAObjectType.__call__` runs the overlay selection for every instantiation,
  so a cell built by `ExcelWorksheet._get_firstChild` gets the overlay as surely as the focused
  one does. Without that the seam would have had nowhere to attach.

**Two object models, and only one of them can be read this way.** By default NVDA reaches
Excel through its COM object model: `NVDAObjects.window.excel.ExcelCell` carries
`excelCellObject`, its parent worksheet carries `excelWorksheetObject`, and that answers
`cells(row, column)` — the same call NVDA's own `_get_firstChild` makes, wrapped in NVDA's own
`ExcelCell` so that every later question is NVDA's answer. With "use UI Automation to access
Microsoft Excel spreadsheet controls when available" turned on — off by default,
`config.conf["UIA"]["useInMSExcelWhenAvailable"]` — the cells are `NVDAObjects.UIA.excel`
instead, and there is no equivalent: NVDA does not wrap the grid pattern's item lookup, and a
sheet's cells cannot be walked as children. Such a cell is recognised as *not* readable this
way and left alone, so the reader keeps NVDA's ordinary reading of it rather than a layout
drawn from numbers this could not check. The recognition is by what makes the reading possible
rather than by class, so it cannot half-work.

**Coordinates are Excel's own, counted from A1.** The shape comes from the used range —
`usedRange.row + usedRange.rows.count - 1`, and the same for columns — rather than from the
sheet's million rows, which is what makes a layout possible at all. Counting from A1 rather
than from the corner of the used range means a sheet whose data starts at C5 has four empty
rows above it and they are read as four empty rows; the alternative is an offset every
coordinate in the add-on would have to carry, to save the reader a scroll they can make in one
keystroke. And because the numbers are Excel's, the `rowNumber` and `columnNumber` NVDA reports
on a cell are the same numbers `cells(row, column)` takes, so nothing has to be translated.

**Headers are the reader's own.** A spreadsheet declares none, and `hasHeaderRow` is false:
row one is data until somebody says otherwise. In NVDA somebody does — a reader marks a header
row or column themselves, and every cell of the column then answers `columnHeaderText`. That
reaches the pinned row through `headerTextOf` exactly as a list view's declared header does, so
there is nothing to borrow from row one and nothing to guess.

**What the first review of it found**, all of it in the application module and none of it in
the shape, which is the split working: the generic half had tests and the Excel half had
none. Every one of these is now reachable with fake COM objects, and none of them needs Excel.

- **Routing did nothing at all, silently.** `NVDAObject.setFocus` is empty by default and an
  Excel cell inherits it, so a routing key over a column moved nothing and said nothing. NVDA's
  own Excel navigation says what going to a cell means — select it, activate it, fire
  `gainFocus` — and the overlay does that now.
- **The reader could stand outside the table.** Arrowing about a blank sheet does not enlarge
  the used range: the active cell can be D20 while the used range is still A1, and the reading
  was then one row by one column with the reader at row 20 column 4 — nowhere to put them. The
  shape reaches at least as far as they do.
- **A bloated used range could stop NVDA.** Excel's used range grows to whatever has ever been
  written in *or formatted* and does not shrink when the content goes, so a fill colour once
  applied to a row reports sixteen thousand columns. The measurement reads a bandful of rows
  across every column, which is a hundred and fifty thousand calls and translations on the
  thread NVDA answers on. A sheet wider than `MAX_COLUMNS` is refused and the reader keeps
  NVDA's ordinary cell-at-a-time reading, which is the right answer rather than a wait with
  nothing to explain it. Reaching a distant active cell cannot get round the bound either.
- **Every fetched cell built a worksheet.** `ExcelCell._get_parent` makes a new
  `ExcelWorksheet` for each cell asked, and a worksheet's header tracker is populated by
  walking every defined name in the workbook — so asking a bandful of cells for their column's
  header walked the workbook once per cell. The worksheet already in hand is assigned to each
  constructed cell, which stops the getter running at all.
- **A missing coordinate became A1.** NVDA's `rowNumber` and `columnNumber` come from its Excel
  helper and can be silent while the COM range beside them knows perfectly well where it is.
  Excel's own answer is taken second, and where neither will say the reading is declined —
  since attaching the whole thing to the wrong place while saying nothing is the worst of the
  three outcomes.

One more the review found next door, in the designer: **a column named as the repeated one and
then hidden**. The two decisions cannot both be met and the planner settles it by repeating the
first drawn column, so the record named one column while the display repeated another. The
chooser now offers only drawn columns, hiding the chosen one takes it out of the choice, and
neither `repeatedColumn` nor the saved record names a column that is not showing.

### What the first sheet on hardware found

A worksheet of nine rows, the reader standing on empty A10 below them, twenty-one columns of
used range. The command refused with "this table could not be laid out in columns", and the
log had an eleven second freeze in front of it: watchdog recovery started ten seconds in, the
refusal was logged two tenths of a second later, and the whole thing recovered a second after
that. Four separate faults, and the ordering of those three lines is what tells them apart.

**Measuring cost eight or so cross-process calls per cell, and there is no need for any of
them.** The sample was two rows — the caret's, which was the last, and row one — across
twenty-one columns. Forty-two cells, ten seconds, about a quarter of a second each. Each cell
was a coordinate lookup, an `NVDAObject` built with its overlay classes chosen, and
`_get_excelCellInfo`, which is three COM calls (`address`, `Application`, `International`)
before an in-process fetch of that one cell's text, address, states, comments, formula and
coordinates. Standing anywhere higher in the same sheet would have made it eight rows rather
than two.

NVDA never does this. When it wants many cells it fetches a *range*:
`ExcelCellInfoQuicknavIterator.iterate` hands the helper an address and a count and reads back
an array. So the `Sheet` protocol gained an optional `textRow(row, first, last)` — a row of
strings for a span of columns, or None meaning "ask me the ordinary way" — and the measurement
uses it wherever it is offered. One call for a row instead of a hundred and seventy. Objects
are still built for the cells that are drawn and routed into, which is where an object is what
is wanted; measuring wants text and throws everything else away.

The text is NVDA's own displayed text, which is why this goes through the helper rather than
`Range.Value2`. A one-call value read exists and hands back what is *stored* — a date as a
serial number, a percentage as a fraction — and a column sized from that is sized for
something the reader will never feel.

**A column's name was asked once per cell.** `SheetTable.cellOf` resolved `columnHeaderText`
for every cell it built, and on a worksheet that is `fetchAssociatedHeaderCellText` walking the
marked header ranges with the cell's coordinates in hand. A declared header belongs to the
column, so it is now asked once per column — the same thing `_headerFor` already did for a list
view. And the sheet is asked first: `Sheet.columnHeaders` is optional and Excel answers it from
NVDA's header cell tracker, so a worksheet nobody has marked up says "no column declares
anything" without a single cell being built, which is the entire cost of that answer on a wide
sheet.

**A cancelled read looked exactly like an empty cell**, and that is what turned a slow command
into a wrong message. Once the watchdog starts recovering a frozen core it cancels every COM
call the main thread makes; NVDA's comtypes patch raises `exceptions.CallCancelled`, which is
not a `COMError` and not an `OSError`. Both `ExcelSheet.cellAt` and `flowTableSource.cellRegion`
caught broadly and answered "there is no cell at this coordinate" — which is what a merged cell
looks like — so all twenty-one columns measured nothing, the plan came out empty, and the
arithmetic got the blame. It is now caught by name and re-raised, and the build says so.

**And the two refusals are told apart.** "Could not be laid out in columns" is about
arithmetic: these columns, this band, no arrangement fits, which is a thing to change the
layout about. Nothing having been read is not that. `flowBuild.Unreadable` is a note that is
also a kind — a `str` subclass, so the log is unchanged — `flowBand.NOT_READ` is the third
`tableProblem`, and the command says "nothing could be read from this table" instead, which is
often a moment that has passed and is worth asking for again.

**The report could not describe a single cell.** `SheetTable` passed `row=None` to its base, so
`describeCells` said "no row in hand, so there are no cells to describe" for every sheet — and
the one report written to explain why this sheet would not lay out explained nothing. A sheet
has a row *number* where the other two shapes have a row object, and that is what stands in for
one everywhere else in the class; it now stands in here too, and `cellObject` is answered so
the three places a header can come from are written down as they are for a list view.

### What the second sheet on hardware found

The measuring half worked: the same sheet came back as nine rows by twenty-one columns, the
plan named five columns with real widths over seven pages, and the command said the columns
were showing. **The display was blank.** Every drawn cell had come back as "no cell at row R
column C", which is `SheetTable.cellOf` saying `Sheet.cellAt` answered None.

**NVDA's objects are composed, and this asked for a composition of a composition.**
`DynamicNVDAObjectType.__call__` builds an object from an API class, asks the application
module which overlay classes belong on it, and mutates the object into a new type whose bases
are the overlays in front of that API class. So the cell the reader stands on is a
`Dynamic_SpreadsheetCellExcelCell` — the log says so on every line that names the focus.

`cellAt` asked for another cell by writing `type(self.cell)(...)`, which is that composite. The
metaclass then did the whole thing again: it offered the composite as the only class, this
module inserted its overlay in front of it, and the bases came out as (overlay, (overlay,
cell)). That pair has no consistent method resolution order, `type` raised `TypeError`, the
broad catch in `cellAt` turned it into None, and a reader with a correct layout got an empty
band.

The fix is what NVDA does everywhere it reaches for a cell — `ExcelWorksheet._get_firstChild`
and `ExcelBrowseModeTreeInterceptor.navigationHelper` both name `ExcelCell` outright. The API
class is what a composition is made *from*; the overlay is put back on the result by the same
metaclass, so the cell that comes back still answers `brlMultilineSheet` and `setFocus`. A
second guard sits beside it: a class list already holding a class made of this overlay is not
offered it again, whatever path handed it over.

The stand-ins had not been able to show any of this, because they were plain classes and
constructing one just constructed it. They now compose the way NVDA does, including its cache
of composed classes by their bases, and `type(self.cell)` fails in the tests exactly as it
failed on hardware.

**And recognising a cell was building a worksheet per cell.** `readsByCoordinate` proved the
worksheet was reachable by reading `obj.parent`, and it runs inside the metaclass for every
object NVDA builds — so `ExcelCell._get_parent` made a fresh `ExcelWorksheet`, and populated
its header tracker by walking every defined name in the workbook, *before* `cellAt` could hand
over the worksheet it already had. The sharing arranged after the first review was undone once
per cell of every band by the recognition in front of it. The COM model's cell is now told from
the UI Automation model's by class, which is exact — they are on different branches, `Window`
against `UIA` — and costs nothing. The parent check proved nothing anyway: a COM model cell's
parent is an `ExcelWorksheet` by construction.

### What the third sheet on hardware found

Data on the display at last, and then two faults in how it moved — both of them the same
fault, and neither of them about Excel.

**Every arrow key rebuilt the whole reading.** A focus change reaches the band as
`showObject(force=True)`, and `force` means "make this reading again". In browse mode that is
right and harmless: the focus stays on the page while the caret walks it, so a table is
redrawn through the live pass, which follows the cursor. In a spreadsheet — or a list, or a
message list — *every* arrow key is a focus change, to a different cell or a different row.
So the layout was measured and planned afresh on each keypress, and the window was placed
afresh with it: the row the reader had just moved to went to the **top** of the band with the
rest of the table below it, instead of coming on at the bottom. The display jumped a page at
a time.

The same rebuild is why stepping onto the first blank row under the data blanked the band. A
window entered afresh at the last row of a table has nothing after it to fill with and
nothing above it that it kept, so all that was left was the pinned header.

The table path already had the better question and was not being allowed to ask it:
`isStillHere` says whether the reader is in the same table. A focus change now says so —
`focusMoved` — and where it is the same table the cursor is followed, exactly as a caret move
in browse mode always was. Where it is not, it rebuilds as before. The saved layout, the
pinned header and the reader's page all stay put, and a bandful of cells is no longer
measured per keypress, which on a worksheet is a cross-process read per cell.

**An empty cell was not a place.** It draws nothing, so it left no position anywhere on the
row — and a position is what the cursor is found by and what a routing key is turned back
into. A reader standing in an empty cell therefore had no cursor at all, no way to feel which
column they were in, and no routing key that would take them into one. On the blank row under
a sheet's data every cell is empty, so the whole row was unreachable and unmarked and they
were left with speech.

Each drawn column now marks the band cells it occupies even when the cell it holds is empty:
nothing to read, but somewhere to be. The cursor lands there and a routing key over it reaches
that cell — which is also what makes an empty cell somewhere you can go in order to type in it.
Reporting is unchanged, because a cell that reads as nothing is left out of the row's text
rather than joined in as an empty string.

**The column's whole width, and the first attempt marked only its start.** That fixed the
cursor and did not fix routing, which is what the reader reported next: on a blank row nothing
is drawn, so there is no telling which single cell of thirty-two is the live one, and every
press either side of it still reached nothing. The way a reader aims at a column they cannot
feel is the pinned header — press under the heading you want — and that only works if the whole
of the column answers. Every band cell the column occupies now carries it.

All of them at offset zero rather than at their own offsets, because a table cell is routed to
as a place and not at the character under the finger — `TableCellRegion.routeTo` says so and
ignores the offset — and one position for the whole of a cell also keeps the cursor at the
column's start, since `FlowController.cursorCell` takes the first band cell that matches.

### One cursor, one expanded word

NVDA has a setting called "expand to computer braille for the word at the cursor". It is on by
default and it is what makes routing into a word to edit it work: the word under the cursor is
written out uncontracted so that cells and characters line up.

It is applied in `braille.regions.base.Region.update`, which asks liblouis for
`COMPBRL_AT_CURSOR` whenever the setting is on **and the region has a cursor**. And every block
of a flow has one, by construction: a block reads a fixed position, and `TextInfoRegion.update`
asks the region where the selection is and lays the block out around the answer. Each block
answers with its own position, so each has a cursor at character zero, so each has its first
word expanded. The reader's Favorites page came out with every line beginning in computer
braille.

The blocks were already clearing the cursor — in `FlowRegion.update`, straight after
`super().update()`. That is one liblouis call too late: the cells have been made. The fix is to
refuse the cursor *before* the translation, which means making it a property rather than an
attribute, since the value is set inside NVDA's own update between the two moments.

`CursorOnlyWhereTheReaderIs` is that property, with the subclass answering `holdsTheCursor`:
the active block of a live band for a flow, and the caret's own line for the document lines
drawn around it. Both had the fault and both had it for the same reason.

**The harness could not have caught it**, which is the more useful lesson. The stand-in
`TextInfoRegion` set its cursor *after* calling `Region.update` — the reverse of NVDA — so
clearing a cursor after translating looked identical to never having one. It now works the
cursor out first, and the stand-in `Region` records whether the translation it just did was
asked for with a cursor. That is what the tests assert: eight rows, one expanded word.

Left alone: a pinned region still expands at its own cursor. A pin is a reading position the
reader chose and pans and routes within, and it draws a cursor today; taking the expansion away
would take the cursor with it.

### The answer the designer did not have

The reader asked for "none" among the choices of which column is repeated on every page, and
the interesting part is that nothing needed building underneath it. `TableLayout.pinKey` has
been there since the record was designed — `YES`, `NO`, or follow the setting — and `planFor`
has always honoured it. Nothing wrote it but the settings dialog, which decides for every table
at once. So the feature existed at both ends and had no way in.

Two halves of one question, and the dialog was asking half: `keyColumn` says *which* column and
`pinKey` says *whether* there is one. Asked as one control, the answers are the first column
shown, none at all, or a named column — and "none" outranks a named column, because a record
can hold both and the reader's later word is the one that counts.

Choosing a column deliberately does not turn the repeat *on*. A table that was following the
setting goes on following it; only a reader moving off "none" has said anything about whether,
and then it is written down as yes. The rule is the one this dialog already lives by: it must
not decide what it was not asked, and opening it over a table and pressing OK must change
nothing.

The logic sits on `Arrangement` — what the answers are, which one this table amounts to, and
what choosing one means — rather than in the dialog, because that class is the half with no wx
in it and the question is identical on a worksheet and on a web page. The dialog sets a list of
strings and reports an index.

### What a review of the Excel work found

Thirteen findings, three of them urgent, and the shape of them is worth keeping: almost every
one is *a question asked of Excel more often than it needs to be*, or *an answer believed
without being checked*. Both are the same mistake at different ends.

**Reading in bulk stopped at the measuring.** `Sheet.textRow` was added to measure a table
without building a cell object per cell, and the rows the reader actually feels went on being
built one cell at a time — twelve objects for one re-read of a four by three window, repeated
on every live pass. What made it fixable is that the object is not needed to *draw* a cell,
only to *go* to one: `ObjectCellInfo` now takes a way to fetch its object instead of the
object, and nothing calls it until a routing key lands on that one cell.

**The header question was the expensive one.** Asked cell by cell it is an `ExcelCell` and a
header search per column, three times over for a column that says nothing — sixty three cell
objects on a twenty one column sheet nobody has marked up, when forty two had already been
enough to pass the watchdog. It is now asked of the sheet: NVDA's header cell tracker says
which cell heads which columns, and what that cell says is one read of the heading row.

That is the seam that took the reader's header row off the display two rounds earlier, so it
comes back with the checking it lacked. The tracker is built by walking the workbook's defined
names and NVDA keeps whatever that walk produced, including nothing where it failed part way —
so an empty tracker is worth one witness cell before it is believed, and the three states
(`None`, `{}`, a mapping) mean what they say again everywhere they are read.

**An empty column the reader is standing in.** Hiding an empty column is what keeps a
watchlist's column of unreadable icons off the band, and read the same way on a spreadsheet it
takes the reader's own cursor with it: a grid is a plane, and the column beside the data is
where they go to write the next one. So the table is asked — `emptyColumnsArePlaces` — rather
than the rule being applied to both.

**Two answers that were not checked before they were believed.** Excel's batch fetch stops at
the first cell it cannot reach, and a short answer was padded with empty strings, so columns
holding values measured as columns holding nothing. And the row one question compared only the
columns that declared a heading, so a third column holding "Important" and declaring nothing
was dropped along with the heading row.

**And several places where a cancelled read came back as an answer.** Recognising the table,
asking how far a worksheet goes, a used range that would not answer at all. Each was swallowed
by a broad catch and reported as "not a table", "no headings", a one by one sheet. Every stage
of building a table flow is now inside one catch that says what actually happened.

**What the second review of it found.** Three things, and two of them were fixes that stopped
short of the fault they were aimed at.

*A witness that shares the poisoned well.* The empty header tracker was to be checked against
one cell, and a cell resolves its column's header through its worksheet's tracker — the same
one, since `cellAt` hands out cells carrying this very worksheet. Asking one was asking the
same silence twice. The answer is not a second opinion but a fresh walk: `_trackerNow` fills a
tracker of its own from the workbook's defined names, so the walk either finishes — and an
empty tracker then means what it says — or raises, which is a different answer. The finished
tracker is put on the worksheet, where NVDA keeps its own, so the two paths cannot disagree and
a worksheet left with a half-built one is mended. The stand-in was wrong in the same way and
was making the old fix look tested: it let a cell answer from a map the tracker knew nothing
about. It now resolves both through the one tracker, as NVDA does.

*A boundary the reads never reached.* Cancellation was caught around building the table flow,
and recognising the table, naming it and looking up what the reader saved for it all happen
before that. Each answered "not a table" — the one thing a reader looking at a table can see is
untrue. They re-raise now, and the boundary is around the whole of the band's table operation
and around the command's own first read.

*A count that moves when the reader does.* Rebuilding on a changed row count is right for a
sheet that gained rows and wrong for the number that was being compared: `shape` reaches at
least as far as the reader, so on a blank sheet D20 to D21 and back was two rebuilds. How far a
grid is *written in* is now a separate question — `usedShape` — and the answer is taken once,
onto the handle, because two handles are what get compared and a number read at comparing time
is the same number twice.

Two findings are left undone deliberately, and both are the same shape: they need work that
cannot be checked without Excel in front of it.

- **Hidden and filtered rows and columns.** The adapter does not consult Excel's hidden state,
  so a hidden column with text in it is measured and drawn, and rows an AutoFilter has hidden
  are fetched by coordinate and read as part of the flow. The column half could be done with
  one `SpecialCells(xlCellTypeVisible)` call over the heading row; the row half cannot, because
  the source walks `row + 1` bounded by the row count and skipping hidden rows means a
  `rowAfter`/`rowBefore` contract for every shape of table, not just this one. Writing either
  against a fake and shipping it unverified risks the thing this whole area has been getting
  wrong — columns silently missing — so it waits for hardware.

- **A used range inflated downwards.** Formatting a whole column and clearing it leaves Excel
  reporting a million rows, which is not a freeze but is an endless stream of blank rows to pan
  through. `MAX_COLUMNS` has no vertical twin, and the honest fix is a last-content-row query
  that has to be tried against a real workbook.

### The header row that was drawn twice, and the sample that read nothing

Two reports off one worksheet, and both were about the same thing: a table is asked questions
from wherever the reader happens to be standing, and both answers depended on that.

**The pinned row and row one were the same row.** Row one used to be dropped from the stream
only where it was *borrowed* as the headings — a table that declares nothing, where
`firstRowIsHeadings` says row one may be read as its names. Where the table declares its
headers, the reasoning was that they may be two rows deep, in a column rather than a row, or
nowhere near the top at all; drop row one and a table loses a row of data to a guess the
document had already contradicted. Which is right, and is not the whole answer: on a
spreadsheet what the reader marks as the header row is row one, so the declared headings and
row one are the same text, and both were drawn. It showed the moment they arrowed up onto row
one, since that is what put that row on the band, and it stayed there afterwards.

Nothing says which row a declared header lives on. NVDA resolves it from wherever the markup
points — a marked range on a worksheet, a `<th>` in a page — and reports the text and not the
place. So the only witness is the row itself: if every column on the page holds, in row one,
exactly what that column declares as its heading, then row one is the header row. One read of
one row, which on a sheet is one call, and false for anything that cannot be read or cannot be
decided — because drawing a heading twice costs a row and dropping a row of data loses one.

That row is now `pinnedIsRowOne`, and it decides two things rather than one. It skips row one
in the stream, and it lets the pinned row claim the cursor: once the row is above the band and
no longer in it, the pinned row is the only place the reader's cursor can be, and
`FlowController._pinnedCursor` asks the pinned region and nothing else. The report says which
row the headings came off, since the reader cannot see that and neither could the log.

**And the widths were measured from wherever the reader stood.** `_sampleRows` read a bandful
forward from the caret, which is the right instinct — what the widths are decided from should
be what the reader is about to feel — and it has no answer at the end of a table. Standing on
the blank row under a nine row sheet, the sample was two rows: the header row, which is always
read, and their own empty one. Every column was then sized to its heading alone and every
value in the table wrapped, while the same sheet laid out from the top came out right. The
reader asked why the arithmetic differed when the data had not, which is the right question:
the data had not been read. The rows behind are as much a description of the table as the rows
ahead, so the sample fills backwards when there are not enough ahead, at the same size.

What is still not answered is what an empty cell *says*. Nothing is drawn in one, so a blank
row is felt as a blank row: the cursor says which column you are in and the pinned header says
what it is called, and beyond that the columns are invisible until something is written in
them. A marker would show them, and it is still one setting and one line in the renderer —
see M7, where it belongs to the table rather than to the add-on.

Both were found by walking a stand-in table one focus change at a time and printing where the
band put each row, which is the shape the tests now take. Neither needed Excel; both had been
reachable from the day object tables were added, and neither showed up because browse mode
never takes that path.

### Following the columns, and scrolling them one at a time

Three things asked for together, and the first two are the same axis.

**The columns are followed on the focus path as well.** Browse mode reaches `_recheckTable`,
which follows both axes; a spreadsheet or a list reaches `_showTable` instead, and that path
called `setCurrent` and then `followCursor` — so by the time the live pass ran, the source had
already moved and the check that would have looked at the column found nothing changed. The
band followed the reader down the rows and left them behind across the columns. It now asks
`_showColumn` there too, which is the same call `_recheckTable` makes.

**And the columns scroll rather than turn.** The columns were dealt into fixed pages and the
band showed one whole page at a time, so a caret moving one column past the right edge
replaced everything under the reader's hands at once. That is not how the row axis has ever
worked — a row past the bottom comes on at the bottom and one row goes off the top — and it is
not what reading column by column wants.

So `ColumnPlan.page` became `ColumnPlan.at`: an offset into the run of columns rather than an
index of a leaf. `placements()` packs from there, `startShowing` is the column axis of
`FlowWindow.ensureVisible` — already showing moves nothing, off the left becomes the leftmost,
off the right comes on by the smallest scroll that reaches it — and `turnedBy` moves a whole
bandful of *whatever is showing now*. That last part is what keeps the two from getting out of
step: there is only one number to be in, so a reader who has scrolled two columns along and
then asks for the next page gets the next band's worth from where they are.

The deal into pages still decides the widths, which is what it was always for, and the one
break a reader names is still kept — carried on `Column.startsAPage` rather than in the
assignment, because a break has to travel with its column once the band can start anywhere.
Bandfuls counted from the first column survive as `pageStarts`, `pages` and `numPages`; the
reader is never told a page number, because after a one-column scroll the band sits between
two of them. What they are told is which columns are showing: "columns 6 to 9 of 21", which is
true wherever the band is.

**And a row that has just become theirs is shown whole.** A block is brought on by the line
the cursor is on, which is the right rule for keeping the cursor in view and the wrong amount
to show when the block has just arrived: scrolling down onto a record two lines tall put its
first line on the bottom row and left the second off the band, so the values in the columns
that had wrapped were exactly the ones the reader could not read. The same on a list of files
whose names run to two lines. `syncToCursor` now asks for the far end of the block as well,
where the whole of it fits on the band; where the block is taller than the band it is refused,
because reaching its far end would scroll the cursor's own row off, and panning is what a
block taller than the band is read by. A rendering that is only a chunk of its block — a long
edit, sixty-four rows at a time — is refused for the same reason.

**A header row that could not be read again was being thrown away.** `headerBlock` answers
None both for "this table has no headers" and for "I could not read them just now", and it
swallows its own failures to do it. Two callers took that None at face value — the live pass,
which re-reads the pinned row so a renamed heading reaches the display, and the page turn,
which reads the headings of the columns that have just come on. Either could take the header
row off the band, and nothing put it back until the layout was made afresh. On a spreadsheet
that is a live risk rather than a theoretical one: every cell fetch crosses a process boundary
and the watchdog cancels the lot when the core is busy. A header is a property of the table,
so what stands now is the row that was there — at worst out of date, at best exactly right —
and the report says "pinned: held but not drawn" when a flow is holding a header row it did
not draw, because a missing line reads the same whether there never was one or it was lost.

That was not it. The next report answered the question outright, in three numbers: the window
was **eight** rows rather than seven, no `pinned:` line at all, and the source saying "nothing:
this table declares no headers and its first row is not headings" — while the same report
listed every column of the same table under a header it had read from the cell. The band had
never been given a header row. Nothing lost it; it was never built.

**Because this module answered a question it could not know the answer to.**
`ExcelSheet.columnHeaders` said "no column of this sheet declares a header" whenever
`ExcelWorksheet.headerCellTracker` was empty, to save building a cell per column to be told
nothing — on a sheet twenty-one columns wide, the whole cost of that answer. The tracker is
NVDA's own, private, and populated lazily by walking every defined name in the workbook, so an
empty one means "I know of none just now". Answered as though it meant "there are none", it
suppressed the per-column ask outright: the measurement marked no column as declaring
anything, `flowBuild` was handed an empty `declared`, `headerBlock` had nothing to build a row
from, and the band gave the header's row back to the table.

Three changes, and the first is the general one:

- **An empty answer no longer stops the ask**, in the measurement and in the drawing alike. A
  mapping that comes back empty says "I know of none", which is not "there are none", and it
  is not worth the reads it saves.
- **The Excel side no longer answers the seam at all.** One read per column, once per layout,
  is a small price for not guessing about somebody else's private state. The seam stays for an
  adapter that can answer authoritatively.
- **And a sheet no longer builds two cells for every one it reads.** `SheetTable.selection`
  fetched the cell the reader is standing in so that it could be handed to `_getTableCellAt`
  as the position to read from — which every shape here ignores, since the coordinates are the
  arguments beside it. So each cell of a band cost two coordinate lookups and two
  `NVDAObject`s with their overlay classes chosen. It is a coordinate now, and nothing is
  fetched for it.

**And the report's title was talking about something else.** "Flow dry run: nothing here can be
flowed" is about the dry run's own flow, built beside the band to compare against — and in
Excel the ordinary reading of a worksheet is a tree interceptor with no text in it, so the
title sat on top of a full account of a table the reader could feel under their fingers. It
now says what the band is reading when the two disagree: "nothing here can be flowed on its
own, though the band is reading a table in columns (1 to 5 of 19)".

That was not the whole of it either: the next report, from the new build, still had an eight
row window, no pinned row and the same "this table declares no headers" beside a full list of
columns the same report had read headings for. So the measurement is still concluding that a
table which plainly names its columns names none.

**The round trip through a control field is the remaining suspect, and it has been taken out
of the path.** An object table's cell holds its column's name as a string —
`ObjectCellInfo.header`, resolved once per column from the object — and then encodes it into a
`ControlField` so that it looks like a document's cell to everything downstream.
`declaredHeader` decoded it back out, guarded, so any failure in that round trip comes back as
"no header" rather than as an error. Where the answer is already in hand it is now taken
directly, and only a document's cell — where the field really is the only place it lives — is
read through the fields.

Whether that is what was losing it is not yet proven, so the build now writes down what the
header question found: **"Columns that name themselves: {...}"**, or "none". That line sits
between the two that were disagreeing, and the next report will say which side of it the
answer is lost on — whether the measurement never found the headings, or found them and
something after it refused the row.

**And the same rule, a third time, in the place that made it permanent.**
`TableFlowSource._headersForThisPage` answered nothing at all when it started with no headers
in hand — on the grounds that a table which declares none has nothing to look up. That turned
one empty measurement into a table with no headings for as long as the layout lived: the
worksheet's every cell could name its column, and not one of them was ever asked. The columns
are asked once each now, whatever the measurement thought, and `_headersAsked` is what keeps
it to once. So the header row comes back even where the measurement is what went wrong, which
is the difference between a fault and a fault that cannot recover.

Three times now the same shape has been the bug: an empty answer taken as a settled one. It is
worth stating as a rule for this area — **"I know of none" is not "there are none"** — because
each of the three read perfectly reasonably in isolation.

The diagnostic that should have found this sooner was written to the wrong place: the build's
notes are printed only when a layout *fails*, and a layout that comes out wrong is not one that
failed. The report now carries them whenever the band is reading a table, under "Band layout,
as it was made".

### The header row, found at last

The report that had the layout's own notes in it named the fault in two lines:

	Reading a table: <TableHandle 9x21 at row 1 column 1>
	Columns that name themselves: none

**Row one.** The reader puts the cursor at the top of their worksheet — the row they had
marked as its headings — and asks for the columns. And a header cell has no header above it,
so the first cell of every column answered nothing.

`SheetTable._headerOf` remembered that as *the* answer. It was written to ask once per column,
because a declared header belongs to the column and a second cell of it cannot answer
differently — which is true, and is exactly wrong about a cell that is not a witness. All five
columns were settled before a single data cell was asked; the measurement found no headings,
no header row was built, and the band gave that row back to the table. The same report listed
the headings on the next line, because the stand-in that printed them was built later, from a
data row.

So an answer is remembered and a silence is only counted: a column that says nothing is asked
again, from another cell, `HEADER_TRIES` times over. That is the same bounded strategy
`flowTableSource.declaredHeaders` uses one level up and for the same reason — the first cell
read is not always a good witness — and the number now lives in one place for both.

Three kinds of bad witness have now been met, and they are written where the number is
defined: a half-built row of a virtualised list, a merged cell where its neighbours hold real
ones, and a cell in the header row itself.

**Four rounds, and the same shape every time: an empty answer taken as a settled one.** The
tracker that knew of no headers, the sheet that answered none, the measurement that found
none, and now the cell that had none. Each read perfectly sensibly on its own. The rule for
this area is worth stating once: **"I know of none" is not "there are none"** — and the way to
be sure is to ask something else, a bounded number of times.

The three fixes made along the way stay. They were real, they are each tested, and the last of
them — the source asking the cells even when the measurement found nothing — is what makes
this recoverable rather than permanent if it ever happens again.

Not yet read on hardware since.

### Following NVDA's own Document Formatting settings

A requirement the original plan did not have, and it applies to what is already built as
well as to M5. **NVDA's Document Formatting settings are not only about browse mode.** An
application module reads them for its own objects too: `appModules/outlook.py` asks
`reportTableHeaders` before it fetches a message's column headers, and
`NVDAObjects/IAccessible/sysListView32.py` asks the same before it builds a list item's name.
That is how the reader turns off "From" and "Received" being announced before every message
in a list they already know the shape of.

Those settings are the reader's answer to a question this add-on asks again in a different
shape. Where the question is the same, the answer is taken rather than asked for twice.

**The shape is not the same, and that is why it is following rather than obeying.** NVDA's
settings decide what is *said with each thing you touch*, and repetition is most of what a
reader turns off. A spatial layout does not repeat: a header row is drawn once at the top and
a key column once at the left, however many rows are under them. So each setting is read as
"does the reader want this kind of header at all", and a reader who wants it in braille having
turned it off for speech says so per display. Three states — follow, always, never —
defaulting to follow. `bmConfig.FOLLOWING`.

The mapping is exact, and finding that it was exact is what settled the design:

- **`reportTableHeaders` is one setting with two axes, and this add-on has one feature for
  each of them.** Columns means the pinned header row (`flowTableHeaders`); rows means the
  repeated key column (`flowTablePinKey`), because a row header is the thing that says which
  row you are on, and in a spatial layout that is the column pinned at the left. A reader who
  asks NVDA for row headers and not column headers gets the key column and no header row.
- **`reportTables`** decides whether a table is offered as a table — a layout this add-on
  proposes on its own account, and anything it says about being in one. It does not decide
  whether the reader's own command to lay a table out in columns works. NVDA draws the same
  line in code: `documentBase._tableMovementScriptHelper` copies the format configuration and
  forces `reportTables` on before speaking a cell, because a reader pressing a table
  navigation key has asked.
- **`reportTableCellCoords`** decides the coordinates a page turn *reports* — "columns five
  to nine of twenty-nine". What is drawn is not a coordinate: a column under the reader's
  finger is its own answer to where it is, which is the whole argument for laying a table out
  spatially, and this does not suppress it.
- **`includeLayoutTables`** decides whether a table used to arrange a page is offered at all.
  Its cells are a banner, a sidebar and an article; there are no columns there worth having.

Read on every call rather than cached, because NVDA has commands that cycle them while the
reader is in the table — `globalCommands.script_toggleReportTableHeaders` — and a value read
once at start-up would be the wrong one by the time it mattered. A setting that cannot be read
takes NVDA's own default, so that an NVDA without it behaves as one that has it unset.

### Ask NVDA's object, never the API underneath it

The reader's instruction, and it decides M5's shape: *"use NVDA API wherever possible
especially in Office, they've tried optimizing UIAutomation, MSAA and IAccessible2 already and
I doubt we can do it better."*

The whole table vocabulary already exists on `NVDAObject`, implemented once per accessibility
API and tuned per application: `rowNumber`, `columnNumber`, `rowSpan`, `columnSpan`,
`rowCount`, `columnCount`, `rowHeaderText`, `columnHeaderText`, `table`, `tableID`. M5's
source asks those. An adapter may reach for a UIA pattern or an IA2 interface only where NVDA
has no answer at all, and then it belongs in that adapter and nowhere else.

Three things this turns up that are better than what the browse mode source does today:

- **`NVDAObjects.behaviors.RowWithoutCellObjects` is the abstraction M5 needs**, already
  written. A row whose cells are not objects — a list view, the message list — answers
  `_getColumnContent(column)`, `_getColumnHeader(column)` and `_getColumnLocation(column)`,
  and makes a real cell object on demand. Row-major, which is what a page of columns wants: a
  page for one row is a few calls on a row object the flow already holds.
- **A hidden column is one whose location has no width.** `sysListView32` skips exactly that.
  The browse mode source works it out by sampling cells for content, which is a guess that
  cost a hardware report to get right; where the row can be asked, ask it, and keep the sample
  for sources that cannot.
- **The header is whatever the object says it is, not row one.** `columnHeaderText` and
  `rowHeaderText` are answers; `flowTableSource.HEADER_ROW = 1` was an assumption. See below:
  browse mode answers the same question, and the assumption is now gone from both.

For Excel, NVDA's own `ExcelWorksheet` reaches a cell as
`excelWorksheetObject.cells(row, column)` wrapped in `ExcelCell` — see its `_get_firstChild` —
and `ExcelCell` then answers the whole vocabulary above. That wrapping is the adapter's job
and the only Office-specific code M5 should contain.

The one thing NVDA does not offer generically is *the cell at (row, column)*:
`DocumentWithTableNavigation._getTableCellAt` is for text documents and has no object-side
equivalent. Supplying that, per implementation, is what an object table adapter *is*.

### Browse mode declares its headers too, and row one was always a guess

The reader asked the obvious question of the section above — *"isn't NVDA able to get that
when the table is designed right?"* — and the answer is yes, in browse mode as much as for
objects. It had simply never been looked for.

The chain: a virtual buffer backend asks IAccessible2 for a cell's header cells —
`gecko_ia2.cpp` calls `IAccessibleTableCell::get_columnHeaderCells`, which is what `<th>`,
`scope=` and `headers=` come out as — and records their node identifiers on the cell as
`table-columnheadercells`. `VirtualBuffer._normalizeControlField` then resolves those to text
and hands them out as `table-columnheadertext` on the cell's own control field. Speech has
been reading it all along; `speech.py` gates it on `reportTableHeaders` exactly as this add-on
now does. The same attribute arrives from UIA and from Word by other routes, so a page, a
document and a spreadsheet all answer the same question the same way.

So `flowTableSource.declaredHeader` asks the cell, and `HEADER_ROW = 1` is demoted from the
answer to the fallback — what is left for a table that declares nothing.

Three things fall out of it, and the third is the one that would have been a bug for ever:

- **The measurement plans the column for the header it will actually show.** A declared header
  is a string rather than a cell, so it is measured by translating it, which is the same
  honest measurement every cell gets.
- **A column that declares nothing is left blank rather than filled in from row one.** Mixing
  the two would put a guess beside an answer and give the reader no way to tell which was
  which.
- **Row one is skipped from the stream only where row one is what was pinned.** A table that
  declares its headers may have them two rows deep, in a column rather than a row, or nowhere
  near the top — and then row one is data. Dropping it would lose a row of the table to a guess
  the document had already contradicted, and it would have been silent. `TableFlowSource` now
  decides its own `firstRow` for that reason: it is the only thing that knows where the pinned
  row came from.

### A number is only a coordinate if you know what it counts

A review of M5 found one mistake wearing two faces: **the numbers NVDA hands over are answers
to NVDA's questions, not to this add-on's.**

`positionInfo["indexInGroup"]` is what NVDA speaks as "fifty-two of seventy-nine", and this
add-on read it as a row number because for a flat list it is one — a UIA list item's is filled
in from the selection container, which is the whole list, and it is the only count that
survives virtualisation. But it is an index **within a group**, and the table is only one of
the things a group can be. File Explorer grouped by type, Outlook grouped by date: the
numbering restarts at one in every group. Read as a row number it names two rows the same;
read as a count it ends the table at the bottom of whichever group the reader is standing in.

Two things say the group is not the table, and either is enough to refuse:

- **A level below the first.** NVDA reports a level for controls with a structure and for no
  others, so a row at level two is a row of a branch.
- **A table admitting to room for another whole group.** A grouped table holds more than one
  group, so what says a count is a group's is the table admitting to at least *twice* it. The
  test is one sided on purpose: a virtualised list admits to *fewer* children than it holds —
  File Explorer's answered fourteen while the reader stood on item fifty-two of seventy-nine —
  and that is the case this whole module was built for. **Fewer is a list that has not been
  built.**

Refusing means there is no row number, which is `tableAt` saying the reader is not in a table,
which leaves them NVDA's ordinary reading of the control. That is the right outcome: an honest
"no" beats a layout drawn confidently from numbers that mean something else. The report says
which of the two it read, because nothing else in it distinguishes them — both answer a row
number and both answer a count.

**Twice, and not one more, and the reader's report is why.** The first cut of the second sign
refused on any excess at all, and that reads a disagreement between two counts as evidence of
a shape. It is the wrong reading in this module above all others, since this module exists
*because* those counts disagree: the same file list answers `rowCount` fourteen, `childCount`
seventeen and "one of seventy-nine" at the same moment. One row of excess took the whole
feature away in File Explorer's Details view — the column command answered "not in a table"
about a table whose every cell the same code read perfectly. Grouping is a shape, and a shape
has to show as one.

### A grid's rows carry the same properties its cells do

The Outlook half of the same report, and a worse fault than the count one. Every row of a UIA
grid carries `GridItemPattern` — that is what made File Explorer's Details view recognisable at
all — and every row of a grid is in column one. So by the cell properties alone:

- the message row said which column it was in, so it read as a **cell** of the list above it;
- the list, whose children all said which column *they* were in, read as a **row of cells**;
- and the pane above that became the **table**.

The reader's log caught it in the act: a `CellObjectTable` 17 by 15 over a `PANE` named "Inbox
- …", whose row in hand was a `TABLE` named "Table View" and whose first cell was a whole
message. Nothing in that shape can say which row the reader is on, so `tableAt` raised and the
command said "not in a table" about the table they were standing in and had just asked about.

Three things answer it, and each alone would have:

- **A thing that says it holds rows is never the row.** `CONTAINER_ROLES` — table, list,
  grouping, pane, window and their kin, matched by NVDA's own role name. One property read.
- **Cells of a row are in different columns of it.** Children that all name the same column are
  naming their container's column, and that makes them rows.
- **A row of a grid that will not say how wide it is still holds a place in one.** The third
  way of being sure, for the case where the row's children carry nothing and the list answers
  no `columnCount`: the row's own grid place, plus the thing above it saying it holds rows. A
  Details view *cell* claims a grid place too, and what it is in is one file rather than the
  list, so the two cannot be confused.

**And the walk that asked is bounded.** "Is this object a row" was answered by reading every
child it had, and the object it was asked about turned out to be a message list — one call into
the application per message, between a key press and what the display says, where NVDA's
watchdog calls half a second a freeze. A row of a table has columns, not hundreds of them:
`MAX_ROW_CELLS`, checked against `childCount` first so that a container that will say how many
it holds is never built to find out.

### A list has no header row, and its columns are not named after its first file

Two halves of the same mistake, reported from the same session as the Outlook one.

`TableFlowSource` asks whether row one may be read as headings before pinning it, and so never
pinned a file over a file list. `measure` did not ask at all: with nothing declared it took row
one's *text* as each column's label. Nothing on the display showed it, because the label is not
drawn — it is what the paging command reads out. So turning the page in File Explorer's Details
view answered with the reader's own first row, "report.docx, Yesterday 4:32 PM", as the names
of the columns those values sit in. One reader for the question now, `firstRowIsHeadings`, and
both callers ask it.

What is left where a table declares no headings is the column's number, and a bare number is
not an answer either: paging Outlook's inbox said "six, seven". A column with no heading is now
named as a column — "column 6" — which is a place in a table rather than a number with nothing
attached to it.

**Where the heading comes from is NVDA's answer, and the developer info settled it.** A cell of
File Explorer's Details view is `appModules.explorer.UIProperty`, and its developer info reads
`name: 'Date modified'`, `value: '8/28/2026 9:41 AM'`. The name *is* the column heading. That is
also how NVDA itself reports one: `RowWithFakeNavigation._moveToColumn` calls
`speech.speakObject(cell)`, and what that speaks is the cell's name and its value. So the answer
was never a header control to go hunting for — it is the object, and `headerTextOf` already
prefers exactly this, with NVDA's `columnHeaderText` (the `TableItemPattern` header items) behind
it.

**And what threw the answer away was a type check.** `textInfos.FieldCommand` validates what it
is given — `"controlStart"` with anything that is not a `ControlField` raises `ValueError` — and
`ObjectCellInfo.getTextWithFields` built its field out of a plain dictionary. So the call raised
on every cell of every object table in NVDA, `declaredHeader` caught it, wrote a debug line, and
returned "" — which every layer above reads as "this column declares no header". No header row
was ever pinned over a list view, and the paging command named the columns after the reader's
first row and then by number. The reader's report showed both halves on the same page: every
cell listed with its heading, and two lines below, "this table declares no headers".

It survived because **the stand-in was more forgiving than the thing it stands in for**. The
test double took any dictionary, so the suite was green about a call that could not succeed
once. It now refuses exactly what NVDA's refuses, and reverting the fix fails ten tests.

What was also wrong was **asking once**. A declared header belongs to the column, so any cell of it
answers — but only a cell that answers at all, and the first row read is not always a good
witness: a row of a virtualised list may be half built, and a document's row may hold a merged
cell where its neighbours hold real ones. One such cell was the whole of what a column had ever
said. `HEADER_TRIES` cells are asked now, stopping at the first that answers, which for a table
that declares anything is the first cell and costs exactly what asking once cost.

### The children of a list are not all rows

The same report, one line further down: "inside the table, first few: PANE; SCROLLBAR; HEADER;
LISTITEM…". File Explorer's file list holds a pane, a horizontal scrollbar and the column header
before its first file, and Outlook's message list holds a pane called "Vertical". Two things
were counting them as rows.

`rowObject` reached for `getChild(row - 1)` when a row was too far to step to, so row one was a
scrollbar and every row fetched that way was three files late. The offset is counted once now,
by stepping over the front of the list until something that looks like a row turns up — where
"looks like a row" is the role of the row already in hand, so it cannot disagree with `tableFor`
about what a row of this table is.

`childrenAdmittedTo` compared `childCount` against the group size, and three decorations made a
folder of two files admit to five — room for another whole group, so the grouping test refused
it and the whole feature disappeared in a small folder while working in a large one. Nobody
chose that threshold. The decorations are taken off the count now.

### An object is read while the reader is on it, and not again afterwards

Reported from hardware, in Outlook's inbox read as a run of objects: arrowing onto an unread
message put "unread" on the message above it too, which was read and not flagged. Both rows
claimed the flag and nothing said which of them owned it.

The rows had been read correctly. What happened afterwards was the live pass reading them
again. A run of objects learnt to answer `blockAt` so that a pinned list could keep up, and
`FlowBand._canReadAgain` admits any source that answers it — so every visible row was re-read
on the poll. And `appModules.outlook.UIAGridRow._get_name` builds a row's name from
`activeExplorer().selection`: the unread flag, the attachment flag and the importance belong to
whatever is *selected*, not to the row being named. Asked about a row the reader has left, it
answers about the row they moved to.

**An object's text is not always a property of the object**, and the first answer to that was
worse than the fault. `ObjectFlowSource.blockAt` was made to answer for the current object
only — which froze every other row on the band: a deleted message stayed on the display, a row
that changed did not, and a reader watching a list they were not standing in watched nothing.
A quirk of one application had been turned into a rule about every list in Windows. It was
reverted within the day, on the reader's report and a reviewer's, and it is worth writing down
as the shape of that mistake: **the narrowest true statement was "Outlook names a row after the
selection", and what was written was "an object cannot be read twice".**

What stands now is two things, each the size of the fault:

- **`namedFromTheSelection` refuses the out-of-context re-read for that kind of object alone.**
  An application declares it with `brlMultilineFlowNamedFromSelection`, in the same inert way it
  declares a run; Outlook's rows do not declare it, so they are recognised here by NVDA's own
  name for the class — `appName` "outlook" and `UIAGridRow` in the object's `mro`. If NVDA
  renames that class the old fault comes back in Outlook alone, which is a better failure than
  a rule that freezes every list. Everything else is read again exactly as before.
- **`FlowController.rereadArrival` reads the block the reader has just arrived on.** The one
  moment an application answers about an object with certainty is while the reader is on it, so
  that is when it is asked — one call, for the one block their hand is about to land on. This
  is what fixes the other half of the same report: a message that *was* unread had been walked
  onto the band while a read message was selected, and so arrived saying nothing.

New content was never part of this: a message arriving in a chat is a new block past the end,
not a re-read of an old one.

**And refusing the re-read was only ever half of it.** A row *walked* onto the band while a
different message is selected was never right in the first place — refusing to ask again
only preserved the wrong answer. Hardware showed what that costs beyond an unread flag: a
reader whose selected message was replied to or forwarded saw "replied" and "forwarded" on
messages that were neither, which is not merely wrong but alarming, since nothing on the
display tells it from the truth.

So a row like that is now **built from its own cells**, by the cell reader that already
exists: `flowObjectTable.rowTextOf` calls `_childrenOf`, `cellText` and `headerTextOf` —
the same three the spatial layout calls — and joins them into the one line a run shows. That
is the generalisation the reader asked for: the row builder written for File Explorer's
Details view now serves a list that is not laid out in columns at all.

Three things make it honest:

- **The row the reader is on keeps NVDA's own name.** There the selection *is* that row, so
  every word of it is true — and "unread" lives nowhere else: it is not a cell, and no
  reading of cells could recover it. Landing on a row therefore tells you more about it than
  passing it does, which is the right way round.
- **A row that cannot say what its cells are is read the old way.** Nothing is lost where the
  new reading has nothing to offer.
- **The stand-in lives for one reading.** `_NamedAs` answers `name` and forwards everything
  else, exists inside `FlowObjectRegion.update`, and is gone before anything can route into
  or act on the region — which is the reviewer's "a stand-in is not the object", respected by
  keeping it to one property and one call. What it buys is NVDA's own presentation: a dozen
  properties decide how an object reads in braille and only the name of these rows is
  untrustworthy, so NVDA says the rest.

What it costs is reading, and reading here is calls into an application: the children of a
row and a property or two per cell, against one property for a name. A column's header is
asked once per list rather than once per cell — a header belongs to the column — and the
fetch budget cuts a band short as it does anywhere else. It can also be more verbose than
NVDA, which leaves out an unflagged flag column by asking the selection whether it is
flagged: the very question this exists to stop trusting. A cell that says nothing but its own
header is dropped, which is the part of that noise we can be sure about.

**And it cost the band, until the next hardware report.** `rereadArrival` ends by drawing the
band again, and it handed the drawing the block it had re-read — while `FlowWindow.replaceBlocks`
takes what it is given as *the window*. So the window was left holding one block. In a list
nobody noticed: the reader was on the last row read and the fill put the neighbours back under
them. In a tree it was the reported symptom — stepping from a folder into the first thing inside
it emptied the band, the child became the top row, and the folder it came out of was gone. The
same symptom, from a different cause, as the one `ensureVisible` was fixed for in M2a.

`_redrawBlocks` now takes no list and always draws the whole window, because every caller
wanted the whole window and only one of them had to remember to say so. A method whose
argument must always be the same value is an argument that can be got wrong, and this one was.

The re-read also stopped drawing the band at all when the answer has not changed, which is
every arrow key down an ordinary list: the block already held is kept — and with it the region
NVDA may have queued for update — and only a row that now reads differently, which is the
Outlook case this exists for, costs a redraw.

### Five from the review of that work

Each of these was reproduced by the reviewer before it was reported, and each is the same
shape of mistake: an answer that was right about the case in front of it and wrong about the
next one.

**A count that disagrees is ambiguous, and ambiguity is refused.** The grouping test had been
relaxed to twice the group size; a list of ten in groups of six and four then read as a flat
table of six, with four of the reader's rows behind an end that was not there. It is strict
again — see the section above, which is where the decorations that made it look wrong are
taken off the count instead.

**A walk counts rows, and steps over what is not one.** `walkTo` treated every sibling as
another row, so a heading between two groups was handed back as the row after them, and the
check that `childAt` had learnt was bypassed whenever the walk succeeded. Non-rows are stepped
over without counting now, with the physical steps bounded separately — `MAX_STEPS_PER_ROW` —
so a stretch of things that are not rows cannot turn a walk of five into a walk of a mailbox.

**A column is remembered, not a cell.** Routing into a cell that cannot take the focus focuses
the row and puts the navigator object back on the cell once the focus has arrived — but NVDA
hands out a fresh wrapper for the row, and its cells are fresh objects too, so what was being
restored was a cell of the row as it was *before* the move. The column number is remembered now
and the cell is resolved against the row that arrived, which is what NVDA's own
`RowWithFakeNavigation` does with `_savedColumnNumber`.

**One reading of the headers, not two.** The measurement asks up to `HEADER_TRIES` cells per
column; `TableFlowSource` asked one cell at the caret's row and took its silence for the
column's answer. A table whose first row was half built therefore had headers in its layout and
no header row above them. The measurement now says which labels the table *declared*, as
against which it read off row one — `flowTable.Measurement.declared` — and `buildTableController`
hands those to the source, so the pinned row comes from the same reading the columns were named
from.

**A band that fills exactly has never asked what follows.** Filling stops when the rows run
out, so a pin whose run fills its band was never told the run ends there, and `hasBeenToTheEnd`
— what tells content that is growing from content that is merely long — stayed false. The fifth
message then arrived below the display and nothing brought it on. A pin asks once as it is
built: `FlowController.lookPastTheEnd`, one call, and what it fetches is thrown away, because
this is a question about the edge rather than a fetch for the window.

### A message about the display must not be written to the display

`ui.message` speaks *and* brailles, and for a message about what the display is showing that is
the worst of both. The reader turns to the next page of columns; the display flashes the
sentence describing the page; the page itself — the thing they pressed the key to feel —
arrives when the flash times out. The words are a description of what their hands were already
on, and they are in the way of it.

So `GlobalPlugin.reportAboutTheDisplay` speaks and does not braille, and the messages that
describe a layout go through it: the columns turned on, turned off, the page turned, and the
end of the pages. In browse mode as much as in a list view — the flash is not less unwelcome
over a web page's table.

The refusals keep `ui.message`. "Not in a table" and "no table columns are showing" are about
something the display is *not* showing, and for a reader who is not listening the flash is the
whole message.

### The four ways of saying no were one sentence

The same report showed a second fault, and it is the reason the first one took a reading of the
source to find rather than a line of the log. "Not in a table" is said for a control that is
not a table, for a table whose row number could not be worked out, for a table no column layout
fits, and for a table whose first row could not be read. They are four different faults in four
different places and the reader was given one sentence for all of them, with nothing written
anywhere.

So: `flowTableSource.explain` asks the three questions again, one at a time, with nothing
swallowed, and adds `ObjectTable.describe` where a stand-in is what answered — which carries
the numbers that decided the group question and now names the sign that settled it.
`FlowBand.layOutTable` keeps `buildTableController`'s own notes, which name the step that
stopped a build and which nothing outside the dry run had ever read. Both go to the log at
`info`, because the reader who hits this is standing in front of hardware and asking them to
turn on debug logging and do it again is asking them to reproduce what they have reproduced
already. And the command now tells a table it could not lay out apart from a control that is
not a table, since those send the reader to look in two different places.

### A stand-in is not the object, and NVDA's own cell is not private

Three more from the same review, each an instance of preferring NVDA's public answer to a
guess of ours:

- **`RowWithFakeNavigation` focuses the row and moves the navigator object to the cell**, and
  says so in its own code. Routing into a cell called `setFocus` on the cell, which in
  Outlook's message list is a text element that cannot take the focus — so a routing key over
  a subject line would have moved nothing and said nothing. The answer is NVDA's: focus what
  `isFocusable` says can be focused, and take `api.setNavigatorObject` the rest of the way.
  File Explorer's Details view cells *can* be focused, and are.
- **`RowWithoutCellObjects.getChild` is the public way to a column.** It makes a cell object
  that answers `name`, `columnHeaderText` and `location` by calling the underscored methods
  itself. Calling those from here was depending on API the NVDA developer guide says is
  private; asking the cell is the same work through the front door, and the underscored calls
  remain only for a row that makes no cell.
- **A bound per node is not a bound.** The search for a declared run allowed five hundred
  children at each of four levels, which is five hundred to the fourth and not five hundred.
  One budget for the whole walk, and `firstChild` and `next` are calls into an application
  that may fail like any other.

And one that is not about NVDA at all: **a row reserved for a header is spent whether or not
there is a header to put on it.** The band's height has to be decided before the columns are
planned, and whether a table has headings cannot be known until they are asked for — so the
row is reserved, the header asked for, and the whole arrangement made again at full height
when the answer is that this table has none. A list view is exactly that case.

**M6 — saved layouts.** BUILT, ON HARDWARE. Confirmed by the reader: a table remembered with
one keystroke comes up in columns when they walk back into it, in browse mode and in File
Explorer's Details view. What that took beyond the record itself is written up below —
automatic application when the caret walks in, and the two faults that came of saving a
measurement as though it were a decision. The record, the identity, automatic
matching, and the two commands.

**An identity has two parts because they cost different amounts.** `where` is one attribute —
a page's `documentConstantIdentifier`, which is the URL NVDA itself remembers a caret position
against across loads, or an application and window class for a control. `what` is the columns
by the headings they declare, which is the half that survives a page being regenerated, and it
costs a read per column: so it is asked for **only when `where` already has something saved
against it**, which is never until the reader saves their first layout. A reader who saves
nothing pays one attribute read per table they walk into.

**What is saved is the reader's choices, not the arithmetic.** A plan holds widths in cells and
cells belong to the display they were fitted to; this reader carries a Monarch and a Focus 80.
So the record holds which columns, in what order, how tall a row may be, whether cells are cut,
whether the key column repeats, whether headers are pinned — and every field can say "not my
business", so a setting the reader changes later still reaches a table they saved before it.
The widths are worked out again by `planFor` on whatever band the table lands on.

Stored as one JSON string in the add-on's own configuration section, per profile: the keys are
URLs and window classes, and a `configobj` key may be neither.

**Turning a remembered layout off has to outlive the redraw.** The command drops the request,
and a saved layout would put it straight back — a toggle that does nothing. `tableRefused`
holds the refusal until the reader leaves the table, since coming back to it is asking again,
and `script_forgetTableLayout` is how they say it for good.

Open, and for hardware: whether automatic application wants a setting of its own. It is
currently on for anything saved, which is what saving means, but a reader who wants a layout
for the command to apply and not for the page to apply on its own has nowhere to say so.

### Six from the review of M6 and the pin probe

Each was reproduced by the reviewer before it was reported, and the first would have shown up
on hardware as a display that stopped following a chat some minutes after it was pinned.

- **A probe that started a budget and never finished it.** `lookPastTheEnd` called the source
  outside `FlowController.operation`, and a source starts its budget on the first call. Every
  fetch afterwards was then part of one operation that never ended: once its allowance was
  spent, the next arriving message was refused, the edge went to DEFERRED, and nothing reset
  it. The probe runs inside an operation now, and the test that covers it lets time pass
  between operations rather than adding the message immediately, which no allowance refuses.
- **A cell whose value happens to equal its column's name is data.** The rule that drops an
  icon column saying nothing but "Flag" was written against the *text*, so a file named "Name"
  in the Name column went off the display. The test is the value: a cell that has one has said
  something, and only a cell with no value whose name repeats its header is a label.
- **A forgiving parser has to be forgiving all the way down.** `fromRecord` was careful and
  everything above it took the file on trust, so a stored `1` — or a place holding a string —
  raised inside `layoutFor` while the reader walked into a table, which is a braille refresh
  that stops rather than an error anybody sees. The store is checked at every level now, one
  bad entry costs that entry, and `columns` is read only when it is a list.
- **The same question asked four times.** One automatic layout resolved the table four times
  and looked the saved layout up twice, each a read of the document at the caret. Both are
  found once and carried into `buildTableController`, which now takes the handle. What remains
  is the one `_recheckTable` asks after every redraw, which is a different question.
- **Identity inherited a weakness measurement had already fixed.** `signatureOf` asked one row
  for the headings that name a table; a half-built row would have named it something else and
  the layout saved against it would not be found. `declaredHeaders` now tries a few rows for
  the columns that have not answered, stopping as soon as they all have — the strategy
  `measure` was given on hardware, in the one place all three callers share.
- **The store no longer carries the reader's URLs.** An identity is a URL with its query
  string, a local path, or the headings of a table they have open, and it was written into the
  configuration file as a key. It is a digest now: it matches exactly as the text did and says
  nothing about what was matched, while the log still names tables in full on the reader's own
  screen.

And one policy stated rather than changed: the store is trimmed by age of last *saving*, not
of last reading. A lookup happens every time the reader walks into a table, and writing the
configuration from a braille refresh to record a read is a constant cost for an eviction that
happens once in two hundred layouts.

**The other half of applying one, found on hardware.** `_showTable` offers a saved layout
whenever the band is *built* — arriving on a page, a focus change, a table being given up — and
in browse mode a reader walks into a table without any of those: the caret moves, the focus
object is still the document, and nothing asks. So saving a layout, arrowing out of the table
and arrowing back gave the ordinary reading, which is the watchlist-comes-up-laid-out promise
refused at the moment the reader would notice it.

`FlowBand._walkedIntoASavedTable` is the twin of `_recheckTable`, which is what notices the
caret *leaving* one, and it costs the same: one recognition per redraw, and only for a reader
who has saved something — an empty store answers without looking at the object, and a table
whose columns they have just turned off answers before it too. The layout is offered once per
table, so one that will not fit this band is not rebuilt on every redraw; the offer comes round
again when they leave and come back.

### A list whose items are grouped is still one run

Reported as a Monarch panning fault in Outlook's message list, and it was not one: the display
was refusing nothing. The list is grouped by day, and at each boundary the run simply ended —
`ObjectFlowSource._step` says "the next object is beside this run rather than in it", the edge
closes, and panning has nowhere to go.

Both halves of `_isSameRun` fail there at once, which is why it looked like a wall rather than
a bug. The heading of the next day is a `GROUPING`, so it fails the role test; the first
message under that heading has a different parent, so it fails the sibling test. Neither
refusal is wrong on its own — a grouping *is* a boundary in most controls, and a list box holds
buttons as well as items — and the table walker, which already knows how to step over group
headings, is not available here because Outlook numbers its rows per group and
`positionNumbersTheTable` refuses that numbering for good reasons of its own.

So a third adapter rather than a weaker second one. `GROUPED_LIST` matches only the shape it is
written for — a run member inside a grouping inside a list — and reads that list down the
screen the way `VISIBLE_TREE` reads a tree: through a group, across to the next group's
heading, through that one. Four things follow from what the reader actually has:

- **The heading is a row.** "Yesterday" arriving under the hand is the reason the boundary is
  worth crossing rather than hiding.
- **A closed group is a heading and no rows**, exactly as it is on the screen.
- **Membership is the list they are all in**, not the parent they happen to share, which is
  what stops the climb at the end of the last group instead of walking into the toolbar.
- **Everything else reads as it did.** The adapter is asked before `SIBLING_RUN` and answers no
  for a list item with no grouping above it, so ordinary lists, menus and tab strips are
  untouched.

A review then found the match itself too narrow in one place. A heading was admitted only if
it had a child the reader could reach as well as a list holding it — so a *collapsed* day was
not a grouped list at all, the generic run took it instead, and that run began at a message
Outlook still keeps in the tree behind the closed group. The walk across the days was lost at
exactly the row that names them. A heading matches on the list that holds it and nothing else
now; a group with nothing open under it is a row of its own, and the step from it is the day
beside it, which `_groupedNext` already did.

### A list item does not always say it is one

Hardware again, and from the add-on's own dialog: the table designer's list of columns would
not flow at all. The band read the one item under the reader as though it were a document, and
panning did nothing — the dry run showed a `DocumentFlowSource` over a `sysListView32.ListItem`
with no adapter note above it.

The role was the answer. A list view with checkboxes reports every item as a `CHECKBOX`, which
is MSAA's answer rather than NVDA's and is the right one for saying "ticked" — but `RUN_ROLES`
is a set of role names, so the run was tested for by asking each object what it called itself
and no run was found. The reader's question was whether this wanted a new adapter. It does not:
the walk, the membership and the reading are all exactly the sibling run's, and only the way in
was wrong. So `_isRunMember` asks a second question — *what holds it* — against a set narrower
than the one that bounds the grouped walk: `RUN_PARENTS` is a list and a list box, and neither
a pane (which holds anything at all, so one child says nothing about the next), nor a tree
(only a top-level node has the tree for a parent, and a checkable tree wants `VISIBLE_TREE`
rather than one generation of siblings), nor a table (whose rows are `flowTableSource`'s
business). `_isSameRun` asks the same question of a candidate, and still requires the roles to
be of a kind, which is what keeps the Close button out of a list of ticked items.

The review noted a second shape this does not cover: a provider that gives each day a
different parent *without* a grouping between. That falls back to today's behaviour, and the
new boundary log is what will say which shape a given Outlook is — `_sayWhereItEnded` records
the adapter, both objects, their roles and their parents whenever a run ends, because the end
of a run is a normal answer and a reader cannot tell "the list ends here" from "panning is
broken" without it.

**M6b — applying a favourite.** DEFERRED, and not merely unstarted. Choosing a saved layout by
name while sitting in a table that has none.

Two things are wrong with it as designed. There is no way to *give* a layout a name — layouts are
saved against the table they were made for, not typed in — so the thing being chosen from has no
names to show. And the case it was for has not appeared in practice: a reader who meets a table
nothing recognises reaches for the band commands and makes a layout, temporary or otherwise, for
that table. Borrowing another table's arrangement turns out to be a worse fit than arranging the
one in front of you, because the columns are not the same columns.

Left in the plan rather than struck out, because the underlying question — how a layout reaches a
second table that should read like the first — is real even if this answer to it is not. It wants
a reader who has actually wanted it to say what they wanted.

### What a layout can hold: the shape of M7

The reader's own case, and it is not one the current record can express: a table whose first
column carries six lines of help text somebody thought was useful, so the *end* of that cell is
what identifies the column and the beginning is noise. Truncation as it stands cuts the end
off, which keeps precisely the wrong half — and the header of that column has the same problem
independently of its data.

So M7 is not only "choose the columns". It is a vocabulary of decisions about a table, and the
record has to carry each of them. What follows is what that vocabulary should be, what each
part costs against the code as it stands, and the order to build it in.

**The rule that governs all of it, learnt the hard way.** Every field is optional and means
"not my business" when unset — see `flowTableLayouts.TableLayout` — and nothing that is a
*measurement* is ever written down as though it were a decision. A reader who has said nothing
about a column gets whatever the measurement and the settings give, on whatever display they
are on. That is what keeps a layout saved on a Monarch working on a Focus 80, and it is what
went wrong when the remember command recorded the columns as drawn.

**Table-wide, of which four exist today**: which columns and in what order; how tall a row may
be; whether cells are cut or wrapped; whether the key column repeats; whether a header row is
pinned. Two more are worth having:

- **Which column is the key column.** It is the first drawn one today. On a watchlist that is
  the symbol and right; on a file list the reader may want Name rather than whatever sorts
  first, and on a table whose first column is an icon it is wrong by construction.
- **What an empty cell says.** A blank cell and a short one feel alike under a hand. A marker
  is one setting and one line in the renderer, and it may be wrong for tables that are mostly
  sparse — so it is a table's own decision rather than a global one.

**Per column, which is where the reader's case lives**:

- **Shown or hidden**, and where in the order — the part M7 is usually described as.
- **Cut or wrapped**, per column rather than per table. `Column.overflow` is already per column
  and the plan simply fills it in uniformly, so this is a map instead of a value.
- **Which end to keep when it is cut.** New, and the reader's actual need: keep the start (what
  cutting means today), keep the end, or keep the ends and elide the middle. The renderer takes
  `line[: width]`; the other two are `line[-width:]` and a join with a marker.
- **A label of the reader's own.** The strongest answer to six lines of help text is not to cut
  it but to *name* the column: the header is orientation, it is drawn once at the top, and a
  reader who calls it "Status" has said everything they need. `Column.label` already exists and
  is filled from what the table declares.
- **How the header is treated, separately from the data.** Which end of the *header* to keep,
  and whether this column contributes a heading at all. The pinned row is drawn with
  `ColumnPlan.cutting`, so it is already a separate rendering of the same columns.
- **Width, where the reader wants to say.** Auto is right almost always — it measures what is
  there — but "at least this many cells" and "at most this many" are the two that matter, and
  they are what a reader means by "give the description room". A fixed width is the same
  mechanism with both bounds equal.

**Arrangement**: which columns share a page. `ColumnPlan.assignment` is already a field for
exactly this and is honoured wherever it is set, so "these four together, those three after
them" needs a way to say it rather than new machinery underneath. A reader with a
twenty-nine column watchlist and six pages is the case: the four they read together should be
page one whatever the packing would have chosen.

**How it reaches the reader.** Two ways, and both are wanted:

- **A dialog**, opened from a command while in a table: the columns in a list with their
  headings, moved and toggled, and a properties pane for the column in hand. It is the only
  place a whole arrangement can be seen at once.
- **Commands on the band**, for the changes made while reading — hide the column the cursor is
  in, cut it from the other end, give it more room. A reader who has to open a dialog to drop a
  column will not drop it, and these are three lines each on top of the record.

**The order to build it**, cheapest and most valuable first:

1. Per-column overflow and which end to keep, plus the reader's own label. This is the reported
   case, and every piece of it is a field the plan already carries.
2. The columns to show and their order, through the dialog. The record and
   `_asTheReaderWantsThem` already do the work; what is missing is the way to say it.
3. Header treatment per column, and the key column choice.
4. Width bounds, then the page assignment.
5. The empty-cell marker, if it still seems worth it once the rest is in use.

**What has to be got right in the record**, whichever order it is built in: a per-column
decision is stored against the *column's own number*, and a table that gains or loses a column
must not shift everybody's settings. The number is what `flowTableSource` reports and what the
existing `columns` field already uses, so the shape is `{"2": {"cut": "end"}}` rather than a
list by position — and a column the table no longer has is dropped on reading, exactly as a
named column already is.

**M7 — arranging a table.** BUILT, ON HARDWARE. The vocabulary, the commands on the band, and
the dialog. With M6 behind it this is what makes tables feature complete for now: a layout can be
made from the band or from the dialog, saved, and applied by itself when that table is met
again.

**Three commands and a dialog, because they answer different questions.** The commands are for
the first minute in a table nobody has arranged — hide the column the cursor is in, cycle how
it is cut, give the table back as it comes — and the reader asked for them in those words:
"they may help with exploring a new table". The dialog is for a table they come back to, where
what is wanted is a name for a column, a floor and a ceiling on its width, which end of its
heading survives, and where a page begins. Neither could stand in for the other: a reader who
must open a dialog to drop a column will not drop it, and a keystroke cannot type a label.

**What a column can be told**, all of it optional and all of it "not my business" when unset:
its own name; wrapped or cut; which end a cut keeps; which end its *heading* keeps, separately,
because a column of short values under an unreadable heading is the reader's own case; a floor
and a ceiling on its width; whether a page of columns begins at it; and whether it is drawn
without capital signs. A table can also be told which column is repeated at the left of every
later page — the first drawn one is the symbol on a watchlist and an icon on the table beside
it.

### Capital signs cost a stock symbol two cells of seven

The last of those came from hardware and is worth its own note, because the obvious ways to do
it are all wrong. In a six dot table an all-capitals word carries the capitals-word indicator
in front of it — dot 6 twice — so `AAPL` is six cells and `aapl` is four, and `BRK.B` is eight
against five. On a column sized for a ticker that is the difference between the value fitting
and being cut, and the reader was spending two cells of seven on it on every row.

There are three places the indicator could be removed and two of them are closed. **A mode
flag** does not exist: NVDA passes liblouis `compbrlAtCursor` and `partialTrans` and nothing
else, and the indicator comes from the table's own `capsletter` and `begcapsword` opcodes. **A
different braille table for the column** the reader ruled out, and rightly — it would change
the contractions as well as the capitals. **Stripping the cells after translation** means
knowing which cell is an indicator in every table there is, and would leave the position maps
pointing at cells that are no longer there. What is left is the text handed to the translator,
so the text is lowered and the table is left to answer the question itself.

Measured before it was built, against the real `liblouis.dll` and NVDA's own tables. Twenty
three tickers, company names and headings translated both ways in `en-ueb-g2`: in every case
the lowercase cells were the uppercase cells with the dot 6 cells taken out, nothing came out
longer, `CHTR` kept its `ch` contraction, and `T` kept the grade one indicator that stops it
reading as the shortform "that". On an eight dot computer braille table it changes nothing,
because there a capital is a dot inside the cell.

Two things follow from where it had to go. It is applied **where the text becomes a region**,
which is a fresh region on every read — so a live re-read lowers what it has just read, where
a value lowered once and cached would have its capitals back the next time the poll came round.
And the **measurement** is told as well as the drawing: a column measured with the indicator in
it is sized for two cells that will not be drawn, which are exactly the two the setting exists
to save. Routing is unaffected, since a cell is routed to as a place rather than at the
character under the finger. It covers the column's heading as well as its values, at the
reader's own request — "so it is just one column setting" — and because the heading is cut to
the same width and pays the same two cells.

**"Leave it alone" has to say what it is leaving alone.** Every per-column question offers
one option that means "I have not decided", and all of them were labelled "follow the table
setting". A reader asked what that meant and there was no honest answer:

- The **cutting** does follow a real setting — "Cut table cells that are too long, instead of
  wrapping them", which is this add-on's own and applies to every table — and this table's
  saved record may override it through `TableLayout.truncateOr`. The label now names which of
  the two it is, and either way says what it currently comes out as.
- **Which end a heading keeps** followed nothing whatever. There is no setting for it anywhere;
  a heading that will not fit is cut at its end because `flowTable._asChosen` says so when
  nobody has said otherwise. It is called a default now, which is what it is.

The option is still worth having rather than folded into its answer, and that is the reason to
keep both: a default is not written into the record and follows a change of mind about the
setting, while a decision is written down and does not.

**Shown or hidden is a checkbox on the line.** The first cut was a plain list with a "Show
or hide" button beside it, and the reader who used it named what was wrong: the state was in
the line's text and the way to change it was somewhere else, so arrowing the list meant hearing
"hidden" as a word rather than hearing a control answer for itself. A checked list says both in
one place, space toggles it where the reader already is, and the word comes out of the line.
The one answer that can be refused stays refused — the last showing column is not something to
take away — and then the tick goes back and they are told why, rather than a clear box left
standing over a column that is still drawn.

The control is a **list view with checkboxes**, not the `wx.CheckListBox` that is the obvious
answer and the wrong one: its boxes are drawn by wx rather than by the system, so no state
reaches a screen reader and the reader hears a list of names with nothing said about any of
them. It is a long-standing wx limitation rather than anything NVDA can be asked to fix. A
list view's checkbox is the system's own, and is reported like every other one.

**A hidden column keeps its name.** It has no place in the plan — the plan is what is on the
display — so nothing on the display could say what it was called, and it came back as
"column 19". That is a position, not a name, and it is exactly the wrong answer for the reader
deciding whether to show it again. The dialog asks the table for the headings of the columns it
is not drawing, once, when it opens: a few cells at the reader's own row rather than measuring
the whole table again on a keystroke. Their own name for a column comes first where they gave
one, since that needs nothing looked up and is what they chose to call it.

**The arrangement is a plain object and the dialog is a shell over it.**
`flowTableDesigner.Arrangement` knows nothing about wx: the columns in order, what was decided
about each, and a `TableLayout` at the end of it. That is the same split `flowTable` makes
against `flowRender`, and it is what lets every decision be tested where a test run has no
display. The list is the dialog: one line per column saying everything decided about it, so a
reader arrows down it and hears the arrangement rather than opening each column in turn.

**What is arranged is what is remembered.** The commands and the dialog write into the layout
*in force* for the table on the band — `FlowBand.tableLayoutInForce`, kept beside `tableWanted`
on the plugin for the same reason — and `script_rememberTableLayout` saves that. So what the
reader feels and what is written down cannot part company, and one press of remember with
nothing arranged still saves the request alone, which is the fix that came of saving a
measurement as though it were a decision.

Two things deliberately not built: an empty-cell marker, which was the last tier of the plan
and is worth deciding on after the rest has been lived with; and any way to *choose* a page
assignment beyond "a page begins here", since the packing already decides the rest well.

**What the first review of it found, and what changed.** All of it was about state ownership
rather than about the arithmetic, and all of it is the same mistake in four places: something
that belongs to one table, kept where it could be read for another.

- *An arrangement outliving its table.* The layout in force was stored on the plugin with
  nothing saying which table it was for, and four paths drop the request without dropping it —
  leaving the arrangement waiting for whatever table was laid out next. Hidden columns and
  widths measured from a watchlist, applied to a message list, are worse than no columns,
  because nothing the reader can feel says that is what happened. The key is stored with the
  layout now and checked on the way out, and the four paths go through one `_forgetTheTable`.
- *The dialog opened over the wrong layout.* It was given only what had been arranged, and a
  remembered table normally has nothing arranged — so a table the reader came back to opened
  on an empty dialog with "remember this" already ticked, and pressing OK wrote that emptiness
  over the record. It is given what the table is *being read with* now, and `asLayout` replaces
  fields on that record rather than building a new one, so the row height, the table-wide
  cutting, and the two pinning settings — none of which the dialog asks about — come back out
  untouched.
- *The remember box was a button rather than a state.* Unticking it on a remembered table did
  nothing, so the table came back laid out by a layout the reader had just said they were
  finished with; it forgets now. A table nothing can name says so, instead of the save failing
  in silence.
- *A column measured blank became a column hidden.* The list held the drawn columns and the
  excluded ones and not the ones the measurement found nothing in, so hiding any *other* column
  wrote a list of survivors without them — freezing a column out of every later reading over a
  decision nobody made. That is the same fault, in the designer, that `omitted` and `excluded`
  were separated to prevent.
- *The controls described something other than the display.* An untouched column read "Wrapped"
  in a table that was cutting, and the column being repeated had no box ticked although one was
  being repeated. Every per-column question has an explicit "follow the table setting" that
  names what following means, the repeated column is one choice with "the first column shown"
  as an answer, and the line for a column says which end its heading keeps and whether it is
  the repeated one. A width floor above its ceiling is refused with a message rather than
  normalised, because which of the two numbers was meant is not ours to guess.
- *The dialog bypassed NVDA's popup lifecycle*, so the previous focus and the foreground were
  not restored around it. `gui.mainFrame.prePopup()` and `postPopup()` surround it now.

Hardware then found the one thing no review had: **the dialog was opened from inside the
script**, and a script runs inside `queueHandler.pumpAll`. A modal dialog shown there never
gives the pump back, so NVDA's core stopped turning the moment the designer came up — the
display froze, the watchdog logged "Core frozen in stack!" every fifteen seconds for ninety
seconds, and the reader had to kill it. The script asks for the dialog and returns now;
`wx.CallAfter` shows it on the next turn of the event loop, which is what NVDA's own commands
do for every settings dialog and what the add-on's two other dialogs already did. What the
reader is arranging is still read in the script, while the table is certainly on the display;
only the showing is put off. On the way back out, the band is changed only if it is still
reading the table the designer was opened over — the dialog stays up as long as the reader
wants it — while what they asked to remember is saved against that table wherever they are by
then.

One finding was not about the designer at all: the **page planner** predicted a row's height
from the table-wide cutting while the drawing used the column's own, so the two could disagree
in both directions — a wrapping table with columns cut by hand was given a page it did not
need, and a truncating table with columns wrapped by hand was given one page whose rows came
out twice the target. Both ask `flowTable.effectiveOverflow` now, which is one function so
that they cannot part company again.

### The blanks are one of Chrome's two browse modes, not one of ours

Two dry runs of the same page, one reading correctly and one full of blank rows, and the
difference is on the second line of each:

```
Band source: <DocumentFlowSource <NVDAObjects.UIA.chromium.ChromiumUIATreeInterceptor ...>>
Band source: <DocumentFlowSource <NVDAObjects.IAccessible.chromium.ChromeVBuf ...>>
```

**They are different browse mode implementations of the same page.** Under the UIA tree
interceptor the document's lines are the cells — `'%Change'`, `'Open'`, `'High'` — one after
another with nothing between them. Under the IA2 virtual buffer each cell is followed by a
line of its own holding a single space, and NVDA puts that space in *the same cell* as the
text before it: offsets 296 to 302 and 302 to 303 are both row 1 column 2. That is the
separator after a cell's text, given a line by the buffer's line offsets.

Which implementation a Chromium document gets is `UIAHandler._isUIAWindowHelper`: a
`Chrome_RenderWidgetHostHWND` is treated as **non-UIA** — the IA2 buffer — when NVDA can
inject in-process and IA2 is reachable, unless the reader has set "Yes" for UIA in Chromium.
The answer is cached per window for half a second. So the reader's pattern is explained
exactly: fresh NVDA, the injection into that renderer is not established, the document comes
up on UIA and reads cleanly; switch away, come back, refresh, injection is in place, the same
page comes up on the IA2 buffer, and every cell now has a one-space line after it. Nothing an
add-on can reach is in that decision, and NVDA's own down arrow walks those lines the same
way the flow does — `expand`, `collapse`, `move` — which is why speech says "row 1 column 2
blank" on the same offsets the band draws a row for.

What a reader can do about it is NVDA's setting, in Advanced: UIA in Chromium set to *Yes*
pins the implementation their good log shows.

The rule that skipped those lines was removed rather than kept behind a switch, and the
reader's reasoning for that stands whatever produces them: a rule that hides a row the arrow
keys still land on makes the display disagree with the caret. If it is ever wanted, the log
now gives it an exact form rather than a guess — a whitespace-only line whose cell is the cell
of the line above it is that cell's separator, and nothing else answers that description,
since an empty cell reports a column of its own.

The audit that came with the removal is worth keeping. The add-on writes to a document in four
places, all of them things the reader pressed: a routing key (`FlowRegion._setCursor`,
`TableCellRegion.routeTo`, the two `setFocus` calls in the object readers), a pan
(`_cursorToTop`), and a line command (`_stepBlock`). Nothing on a timer, on a live update or
on a redraw. The one path outside that list is NVDA's own: `BrailleBuffer.scrollForward` calls
`regions[-1].nextLine()` when the buffer cannot scroll, and for a browse mode region that
moves the cursor a line on — reached by a panning key, never by an arrow key.

### A row taken away from under the reader

Outlook, reported twice: two messages showing, the reader on the top one, delete it — the
focus moves to the message below and the deleted one stays on the display.

The band had no way to know. Every block it holds was read correctly when it was read, the
object the reader arrived on is in the run, and a run being *shorter* than it was is not
something either of those shows. It had been covered by accident: the re-read on arrival
emptied the window to a single block and filled it again, so the deleted row fell out on the
way past. Fixing that fault — it was what threw a tree reader onto the top row — took the
accident with it, which is why the two reports arrived together.

`FlowController.runStillHoldsTheBand` asks it deliberately now, and asks the least that
answers it: is the block above the reader still what the run puts before them, and the block
below still what it puts after. Two steps in the run per focus move, against walking the whole
run on every arrow key — and it catches the change that matters, because the reader's own hand
is what made it. When the answer is no, the run is read again by the path
`_runHasChangedShape` already uses for a node being opened.

What it does not catch is a row taken away at the far end of the band while the reader stands
still; the live pass covers that, and any keystroke reads the band afresh.

### Braille was not told what speech was told

Reported while the Excel application module was being finished, and not about the flow at
all: on a sheet whose header row the reader has marked, NVDA speaks "9/4/2026  A2  Date" and
brailles "9/4/2026  A2". The header is fetched, it is spoken, and the display does not get
it — so the reader who works in braille has to leave the cell and come back to find out what
the column is called.

It is not a policy anybody set. `braille.regions.properties.getPropertiesBraille` has a
`columnHeaderText` and appends it exactly where speech does; it only ever looks for one
beside a `columnNumber`, and `NVDAObjectRegion.update` sends neither. It sends
`cellCoordsText` and stops. Speech reaches the same function by a different road — the
property collection that `speakObject` makes — and that road carries the headers.

So the module that already replaces NVDA's Excel module puts them back, on the region that
draws a cell. `appModules.excel.HeadersInBraille.getBrailleRegions` is the seam: NVDA asks an
object for its own braille regions before it builds any, and for a worksheet cell one region
is the whole answer — a cell is not editable text, has no navigable text and carries no tree
interceptor, so `getFocusRegions` makes exactly one `NVDAObjectRegion` for it anyway.

Three decisions worth writing down.

**The header goes through `appendText`, not into `rawText`.** `rawText` is what has already
been handed to liblouis; changing it after `update` means translating the line twice, which
is the mistake the cursor work in this same session had just finished undoing elsewhere.
`appendText` is concatenated by the base *before* translating, so the header costs nothing —
and it is put back afterwards, because a region is updated again whenever the cell changes
underneath it and a header appended twice would show twice.

**Braille does not go quiet on the second cell of a column.** Speech says a header only when
the row or column changed, and it has to: it is a stream of announcements and a header
repeated on every arrow key would be unbearable. Braille is a standing description of where
the reader is. A header that vanished after the first cell would be a header the reader
cannot read, since reading it is exactly what they do after the display settles.

**It is a second overlay class, not a third method on the first.** `SpreadsheetCell` says
"this cell can be read by coordinate, and here is the seam the flow uses". What a cell's
column is called is a different claim and belongs to NVDA's own line of braille: a sheet too
wide to lay out, or a cell this add-on would refuse, still deserves its header. So
`HeadersInBraille` goes on any COM model cell, and `SpreadsheetCell` on the ones that can
actually be read.

Left where it is: the UI Automation model's cells. They are a different class on a different
branch, the header commands the reader marks with are on the COM branch, and this is the same
line the module already draws for reading.

The stand-in region had to be corrected first, which is the recurring lesson in a new shape.
It drew "name role" and no coordinates, so it could have agreed with a header put anywhere at
all. It now puts the coordinates last, as `getPropertiesBraille` does, and concatenates the
appended text rather than joining it — because those two facts are the whole question.

### The arrow keys, inside a table

Asked for directly: in a browse mode table, up and down should move by row, left and right by
cell, home and end to the ends of the row — and the table must not trap the reader.

The reading problem is real and old. A table is two dimensional and browse mode's reading
keys are not, so following a column downwards means reading every cell of every row on the
way, and the one thing a table exists for is the one thing the reading keys cannot do. NVDA
answers it with a second set of commands, control+alt+arrows, which works and costs a four
finger chord per cell. Other screen readers answer it with a mode, entered and left by hand.

**The design is one sentence: it is not a mode, and it never traps.** Being in a table is
already the state — the reader arrows in and the arrows read the table, arrows out and they
read the page — and the thing that makes that safe is that a key is taken *only when there is
a cell to move to*. Where there is not, nothing here happens at all and the key is NVDA's
own: the down arrow on the last row leaves the table, the right arrow in the last cell walks
on into the text after it. There is no edge announcement and no boundary case, because the
fallback is not a fallback — it is the key doing what that key does.

That inverts where the complexity usually goes. A navigation mode has to define what happens
at every edge; this has to define only when it acts, and everything it does not act on is
already defined by NVDA.

`tableArrows` wraps six of `cursorManager.CursorManager`'s movement scripts — the ones browse
mode binds the plain arrows, home and end to, and the only class in NVDA that defines them.
Shift and control with the arrows are different scripts, so selecting and moving by word are
untouched by construction rather than by a rule.

**The movement is NVDA's and not this add-on's.** `_tableFindNewCell(raiseOnEdge=True)` is
what control+alt+arrow moves with, and `raiseOnEdge` is precisely the difference this needed:
the same arithmetic — merged cells, missing cells, the reader's layout table setting — with
the edge raising instead of announcing itself. What is reported on arrival is what NVDA's own
table movement reports, in the order it reports it: spoken before the selection is set,
because setting the selection can move the focus and rebuild the document under the position
just found. So a reader who uses both gets one behaviour, and this module holds no opinion
about what a table is.

Five refusals, each a case where moving by cell is the wrong answer rather than one where it
would fail: a key with a selection under it, a key resuming say all, a backed up key queue
(NVDA's own policy for both kinds of movement), focus mode, and a reader who has turned
tables off in Document Formatting — the last because taking the arrow keys over is something
this add-on does on its own account, which is exactly what that setting governs.

Home and end carry one extra rule: if the first or last cell of the row is the cell the
reader is already in, the key is not taken. A key that would move nothing is not a key worth
keeping, and NVDA's own start of line is more use than nothing at all.

**What the harness could not answer.** The document stand-in reproduces `_tableFindNewCell`'s
*contract* — it raises at an edge and otherwise answers with a cell, a position and NVDA's
selection record — and deliberately not its arithmetic, which is NVDA's to get right and is
shared with a command that already works. Two tests are therefore written against the log
rather than against behaviour: reaching the edge of a table must not be written down as a
fault, and something that is not a table navigating document must be *recognised* rather than
merely survived. Both would pass by accident against a broad catch, which is what the second
one caught: with the check for the kind of document removed, asking a review cursor for its
cells raised, the catch turned that into no movement, and the reader got the right key for
the wrong reason.

## Open questions

1. **What re-reads a tree when a node is expanded?** A state change event is the obvious
   answer, but it arrives for a great many things and the flow must not re-read on all of
   them.

2. **How is "the cells still fit" decided, for decision 7?** Answered by M1:
   `MAX_INDENT_SHARE`, a fixed share of the row, decided once per plan rather than per fill.
   `IndentPlan.noteLevel` says when there is something to announce.

   **Settled by the reader: the note says the level counted from the top of the tree, not a
   position relative to the band.** "lvl5" means level five of the structure — what
   `positionInfo["level"]` reports and what the tree itself would say — and never "five
   indents from the margin". A relative number would be a second numbering for the reader to
   hold in their head beside the one the application already uses, which is the thing the
   indent exists to save them.

   Which row's level it is remains the margin's, as `IndentPlan.noteLevel` already holds: the
   margin is what every other row on the band is drawn relative to, so saying what the margin
   means makes all of them readable, and on the common case — a plan just made — it is the top
   row's own level anyway. What is still owed is the drawing, in the indent cells of the top
   row, which is the one place this has to reach into assembled cells rather than a block's
   own rendering.

3. **Does rebasing move items enough to be felt?** Decision 5 accepts that an item already
   on the display shifts when the band rebases. It is the right trade on paper and it is
   still a hardware question; the answer decides whether decision 5 needs an escape hatch.

4. **How wide is a column by default?** Even division of the band across shown columns is
   the obvious first answer and is probably wrong for a watchlist, where the symbol needs
   six cells and the change needs eight. Proportional to the header length is a second
   guess. This may simply be what the reader sets and saves.

5. **Where does a merged cell go?** `_getTableCellCoords` reports row and column spans, so
   the information is there. A column layout has to decide what to do with a cell that
   spans three of them.

6. **Is the identity key stable enough in practice?** The header-signature idea for web
   tables is untested. It fails on a table whose headers are localised or reordered, and it
   is the only thing standing between the reader and re-applying a layout by hand.
