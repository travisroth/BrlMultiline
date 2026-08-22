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

Nothing here is built. Read it before extending `flowObjects.py` or `flowRender.py`.

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

4. **Indent is relative, and the item on the band's top row is the baseline.** Absolute
   indent from the root spends cells a 32-column display does not have once the reader is
   six levels into a folder tree. The top row's depth is therefore drawn at zero and
   everything below it is indented from that. An application module may override this.

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

**M1 — depth and indent.** BUILT, AWAITING HARDWARE except the orientation note. What
landed: `SourceBlock.depth` and `RenderedBlock.depth`, `RenderKey.indent`, `depthOf` on
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

Still owed: the orientation note. It is deferred rather than dropped — `IndentPlan.noteLevel`
already says when there is something to announce, and the dry run reports it; what is missing
is drawing it. It is the least settled part of the design (see open question 2) and it is the
only part that has to reach into assembled cells rather than a block's own rendering.

**M2 — presentation registry and the visible-tree walk.** Recognition, the `Presentation`
record, the registry with its four-step lookup, and the application module registration API.
The indent policy from M1 moves into it, so the registry has two real consumers the day it
is written rather than one speculative one. The visible-order traversal and the
descendant-based `admits` land here, and Outlook's Go To Folder is the acceptance test.

**M3 — table layout vocabulary, browse mode.** Column plans, per-column widths, hidden
columns, wrap versus truncate, row height, reading by column. Reading order remains the
default. No persistence and no pinned headers yet.

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

2. **How is "the cells still fit" decided, for decision 7?** The note is shown when drawing
   the absolute depth would cost too much, and "too much" has to be measured against
   something — the widest item on the band, or the active one, or a fixed share of the
   width. Cheap to compute matters as much as right, since it is asked on every fill.

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
