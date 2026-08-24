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

**Status: M0, M1, M2a, M3a, M3b and M3c are built. M0, M1 and M2a are confirmed on
hardware; M3 has been through several hardware rounds and is being used. M2b, M4 (pinned
headers), M5 (tables as objects), M6 (saved layouts) and M7 (the designer) are not built.** The milestones below say which,
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

**M3a — the column vocabulary.** DONE, NOT YET ON HARDWARE. `flowTable.py`: `Column`,
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

**M3b — drawing a row in columns.** DONE, NOT YET ON HARDWARE. `TableRow`, `RowCell`, and
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

**M4 — pinned headers.** The band change. Behind a setting, and last of the display work,
because it is the only part that disturbs window arithmetic the hardware has already
signed off.

**M5 — tables as objects.** Excel, list views, the message list. Same vocabulary, second
source.

**M6 — saved layouts.** The profile record, automatic matching by identity, applying a
favourite by command.

**M7 — the table designer dialog.** On top of M6's record, once the record has been lived
with.

## Open questions

1. **What re-reads a tree when a node is expanded?** A state change event is the obvious
   answer, but it arrives for a great many things and the flow must not re-read on all of
   them.

2. **How is "the cells still fit" decided, for decision 7?** Answered by M1:
   `MAX_INDENT_SHARE`, a fixed share of the row, decided once per plan rather than per fill.
   `IndentPlan.noteLevel` says when there is something to announce.

   **What remains open is what the note says.** Two candidates, and they are not the same
   number once the plan is held across a move: the level the *margin* stands for, or the
   level of the item on the *top row*. `noteLevel` currently holds the first. They agree
   whenever the plan was just made, because the plan is made from the shallowest visible
   depth — but `shouldRebase` keeps a plan while it still works, and a kept plan can have a
   baseline that no visible row is at any more. That is the whole point of keeping it: the
   margin stops twitching. So the note has to say which of the two it means, and say it in
   the four cells it has. This must be settled before anything is drawn.

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
