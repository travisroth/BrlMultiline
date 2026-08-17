# Architecture

Read [nvda-api-notes.md](nvda-api-notes.md) first; this document assumes the facts
established there.

## Module map

- `layout.py` — rectangle geometry, coverage rules, and the fill-rows arithmetic. Imports
  nothing from NVDA, so it can be unit tested.
- `routing.py` — `RoutingPolicy` and its variants. Separate from `views.py` so that
  `panels.py` can carry a policy without importing the layer built on top of it.
- `panels.py` — `SegmentSpec` and `BraillePanel` with its subclasses. Also NVDA-free.
- `views.py` — `SegmentView`, composition, and the configured, single segment and composite
  display views.
- `devices.py` — reading the device map of a display that is several physical displays
  combined. The only module above the braille display driver that knows a composite is one.
  See [virtual-display-plan.md](virtual-display-plan.md).
- `segments.py` — `RectHandlerProxy`, `_SegmentHandlerProxy` and `BrailleBufferSegment`.
- `messages.py` — `MessageBuffer`, NVDA's flash messages confined to one segment. The other
  user of `RectHandlerProxy`, and the one path a container cannot cover, since
  `BrailleHandler.message` swaps the handler's buffer out from under it.
- `container.py` — `DisplayContainer` and `FakeRegionsList`.
- `patches.py` — the patches to `BrailleHandler`, installed and removed together — and taken
  back only where the method is still the one this add-on installed.
- `documentLines.py` — `TextInfoPositionRegion` and the code that fills free segments.
- `objectMonitor.py` — pinning an object to a segment.
- `panning.py` — which physical display's panning key is running the scroll happening now,
  and therefore whose panning direction and whose segment apply. See the module docstring for
  why it wraps NVDA's two panning commands rather than watching gestures pass.
- `bmConfig.py` — per display configuration. Named to avoid shadowing NVDA's `config`.
- `settingsPanel.py` — the NVDA settings category.
- `__init__.py` — the global plugin: lifetime, claims, commands, and object monitor state.

## The problem in one paragraph

`BrailleHandler` owns exactly one `mainBuffer` and treats the display as one window onto
it. To get several independent areas on the display, something has to stand in the place
of that single buffer, present the combined result as if it were one buffer, and fan the
individual operations out to the real buffers behind it. That stand-in is
`DisplayContainer`, and the real buffers behind it are `BrailleBufferSegment`s.

## Three concepts, distinguished by what each is the unit of

The layering exists because three different questions need three different answers. Each
concept is the unit of exactly one thing:

1. **`SegmentView` is the unit of activation.** One at a time. It is a recipe rather than
   live state: named, built from configuration or from code, validated against a display
   size, and handed to the plugin. It owns the panel list and names the segment that
   follows the focus.
2. **`BraillePanel` is the unit of reservation and composition.** One owner, one rectangle,
   and a rule for subdividing it. This is what code hands the plugin when it wants part of
   the display: a table reader claims rows without knowing or caring what holds the rest.
3. **`BrailleBufferSegment` is the unit of content.** A real NVDA `BrailleBuffer` with its
   own regions, its own window, and its own policies. This is what scrolls, what a routing
   key hits, and what a region is targeted at.

You compose panels; you activate a view. A panel cannot be a view because a panel does not
know about the rest of the display, and that ignorance is the point.

There is no separate "plain" path. The single segment arrangement that matches NVDA's own
behaviour is a view; the layout configured in the settings dialog is a view; a table
reader's grid is a panel laid over one. One construction path, one place to validate.

## Panels dissolve before the display runs

`SegmentView.flatten()` turns the panels into a flat list of `SegmentSpec`s, sorted into
display order, and that list is what `DisplayContainer` builds from. Panels do not survive
into the running display.

This is deliberate. Every operation the container performs is leaf-oriented —
`findSegmentAtWindowPos` locates a leaf, `windowBrailleCells` composites leaves, `update`
and `saveWindow` iterate leaves. Not one of them wants an intermediate node. Keeping the
tier at runtime would force every method to decide whether it walks panels or segments, and
that decision would eventually be made inconsistently.

The panels remain reachable through `container.view.panels`, for composing the next view
and for reporting the layout, but they carry no live state.

### A `SegmentSpec` is everything that does not change

```
SegmentSpec(rect, key, owner, fillRows, markCuts, routingPolicy)
```

A panel stamps its defaults onto the specs it produces via `buildSpec`. The container
builds one segment per spec, and the segment exposes `key`, `owner`, `isReserved` and
`routingPolicy` straight off it.

Crucially, **a value living on a segment is not a value exposed to the user.** The settings
dialog still sets only the layout and the focus segment; a configured view stamps one
uniform policy across its segments. Code composing a panel gets full per-segment control
without any of it reaching the dialog.

## Panels must tile the display; their segments need not

`validateCoverage` requires the panels of a view to cover every cell exactly once.
`validateRects`, which segments are held to, requires only that they fit and do not overlap.

The asymmetry is what makes composition safe. If every cell belongs to exactly one panel,
then "who owns this cell" always has an answer, and a new claim can only take cells from a
panel that can be named and evicted — rather than quietly landing on unowned ground.

Gaps remain possible, and remain useful for putting a blank row between segments, but they
now live *inside* a panel. The blank cells belong to the panel that chose to leave them.
`BlankPanel` is the degenerate case: it claims cells and produces no segments at all, so no
buffer is allocated and the cells composite blank. `remainderRects` computes the filler
automatically, so no caller has to do the arithmetic.

`BlankPanel` earned its keep on a display that is several displays combined. Such a display
is as wide as its widest member, so a narrower member has cells on its rows that reach no
hardware at all. A blank panel over each of them is what stops NVDA flowing text into cells
nobody can read — the one case in the add-on where an unowned cell would lose content rather
than merely look untidy. See `views.deviceView`.

### Composition: `withPanel`

`view.withPanel(panel, numRows, numCols)` returns a new view. Panels the claim touches are
evicted **whole** — a panel is a rectangle and has to stay one, so there is no partial
eviction. Cells freed but not wanted are given to blank panels. Panels the claim does not
touch are carried over untouched, keeping their segment keys.

That last sentence is the entire point. It is what lets an object pinned in row 0 survive a
table claiming rows 2 to 7: the panel holding it was never involved.

Because the configured view puts **one panel per segment**, claims can be precise. Had it
been a single display-wide panel, any claim at all would have evicted the whole thing.

### Keys, not numbers, are identity

`SegmentSpec.key` is stable across a rebuild; the index is display order and is reassigned
whenever the layout changes. Anything that must survive a rebuild holds a key: the view's
focus segment, a pinned object's segment, a region's `targetSegment`.

Indices have not gone away. `flatten()` still numbers segments in display order, so
`script_scrollSegment3Forward` and the settings dialog keep counting as before. Keys exist
alongside them, not instead of them — there is no user-visible name for a segment, and
inventing one would be worse than counting.

`getSegmentNumberForRegion` accepts either, so a region targeted the old way still works.

### A view must have a focus segment

Deliberate constraint, worth revisiting when there is a real case against it. NVDA reads
`mainBuffer.regions[-1]` in `handleCaretMove`, `handleReviewMove`, and two global commands,
so there always has to be a segment for untargeted content to land in. A view with no focus
segment would leave those reads with nothing to resolve to.

This becomes a live question during composition. If a claim would evict the focus segment,
the claim must offer a replacement through `BraillePanel.focusSegmentKey`, or `withPanel`
raises `LookupError`. `GridPanel` offers its first cell by default, which is probably the
right behaviour for a table anyway rather than a workaround.

### How many segments

There is no fixed limit. The real constraint is geometric, and `validateCoverage` and
`validateRects` enforce it between them.

`MAX_UI_SEGMENTS` is 8, and it constrains only the settings dialog, the config spec, and
how many per segment commands are generated. A view built in code may have more segments
than that; a 4 by 3 grid on a Monarch has twelve. Note that segments past the eighth have
no generated scroll commands, which is part of why routing policies exist.

### Routing policies are per segment

`RoutingPolicy.route(container, segmentNumber, segmentPos)` decides what a routing key
press does. The policy lives on the segment, not on the view, so a table cell can treat a
press as a selection while the segment beside it routes into text in the ordinary way. In
practice a policy is chosen once per panel and cascades to that panel's segments.

`EdgeRowScrollRoutingPolicy` is a worked example for panels with more segments than there
are keys to drive them: a press in a segment's top row scrolls it back, one in its bottom
row scrolls it forward, anything between routes normally. It is a starting point, not a
settled design — whether the edge rows should be per segment or per panel, and whether
giving up two rows of three is acceptable, wants deciding against real content.

The hook lives in `container.routeTo`, so no additional patch to NVDA was needed:
`handler.routeTo` already delegates straight to the buffer. A press landing in space a
panel is keeping blank finds no segment and is ignored, which is not an error.

## A segment is a rectangle

A segment occupies a rectangle of the display, described by an origin `(row, col)` and a
size `(numRows, numCols)`. Two shapes matter in practice:

1. Whole row groups on a multi row display. On a Monarch (8 rows of 32), a segment might
   be rows 0 to 1, full width.
2. Column slices of a single row. On a Focus 80 (1 row of 80), a segment might be row 0,
   columns 0 to 39.

The rectangle abstraction covers both, and also covers a general grid should that ever be
wanted. There is no separate code path for the two displays.

## Segments believe they own the display

`BrailleBufferSegment` subclasses `BrailleBuffer`. The interesting part is that it barely
overrides anything. Instead of teaching the buffer that it is small, the add-on lies to it
about the size of the display:

```
segment = BrailleBufferSegment(_SegmentHandlerProxy(realHandler, numRows, numCols), ...)
```

`_SegmentHandlerProxy` overrides exactly two things — `displayDimensions` and
`displaySize` — and forwards every other attribute to the real handler via `__getattr__`.

Because every geometry-dependent method in `BrailleBuffer` reads its dimensions from
`self.handler` and nothing else (the full list is in
[nvda-api-notes.md](nvda-api-notes.md)), stock NVDA code then does the right thing inside
each segment, unmodified. The segment gets NVDA's per-row word wrapping, syllable
boundary hyphenation, and continuation marks for free, and none of that logic is copied
into the add-on where it would rot.

The proxy also fixes a wart. `BrailleBuffer.updateDisplay` guards with
`if self is self.handler.buffer`, which would be false for a segment, because the real
handler's `buffer` is the container. The proxy's `buffer` property returns the segment
when the real handler's buffer is the container, so the guard passes and the segment can
refresh the display normally.

### What the segment actually adds

- Its rectangle (origin and size), for the compositor.
- `isFocusBuffer`, so it knows whether it is the one tracking system focus.
- A real `append` method, since `BrailleBuffer` has none.
- The fill-rows override, below.

### Filling rows instead of wrapping words

The one place the proxy is not enough. `_calculateWindowRowBufferOffsets` reads
`config.conf["braille"]["textWrap"]` from the global configuration, not through
`self.handler`, so a segment cannot be handed a different wrapping rule the way it is
handed different dimensions.

A view with `fillRows` set therefore overrides two methods. `_calculateWindowRowBufferOffsets`
delegates to `calculateFilledRowOffsets` in `layout.py` — pure arithmetic, unit tested, and
not a copy of anything upstream, because the fill case is genuinely trivial: fill each row
to its full width, carry straight on, stop at the end of the buffer. `_set_windowEndPos`
places the window a whole segment back rather than snapping to a word.

This is the same behaviour as NVDA's `BrailleTextWrapFlag.NONE`. `markCuts` adds the
equivalent of `MARK_WORD_CUTS`, spending one cell on a continuation mark where a row was
cut mid word — worth it on a wide segment, probably not on one 8 cells across.

Two deliberate simplifications, both stated in the code: fill mode ignores NVDA's rule that
scrolling back should not pass the start of a region marked `focusToHardLeft`, which is
about keeping focus context off the display and is not a concern for a segment showing one
chosen thing; and a view that wants genuine word wrapping simply does not set `fillRows`,
inheriting the user's own setting rather than trying to force a different one.

## The container composites, it does not concatenate

`DisplayContainer` presents the `BrailleBuffer` interface to `BrailleHandler`
without inheriting from it (it derives from `baseObject.AutoPropertyObject`, so NVDA's
`_get_` / `_set_` auto property protocol works).

Its central job is producing `windowBrailleCells`. The 2023 prototype concatenated each
segment's cells end to end. That is correct only when the display is a single row: NVDA's
cell array is row major, so on a multi row display a segment narrower than the display
would land in the wrong place.

Instead the container allocates a blank `numRows * numCols` array and blits each segment's
window cells into it at the segment's origin. One routine, correct for column slices,
whole row groups, and grids alike. Cells no segment covers stay blank, which is how a panel
that claims space in order to keep it empty gets its way, and which also means the container
already pads a short write — there is no separate padding step to arrange.

Cursor position maps the same way. A segment reports a cursor position in its own window
coordinates; the container converts that to `(row, col)` within the segment, offsets by the
segment origin, and flattens back to a display-global index. `BrailleHandler` only knows
about one cursor, so the container reports the focus segment's.

## Fanning operations out

Everything else on the container is dispatch. Methods that NVDA calls without knowing
about segments fall into three groups:

1. **Whole display operations.** `update`, `saveWindow`, `restoreWindow`,
   `windowBrailleCells`, `windowRawText` — applied to every segment, results combined.
2. **Region-directed operations.** `focus`, `scrollTo` — the region carries a
   `targetSegment` attribute, so the container looks it up and dispatches there, falling
   back to the focus segment.
3. **Position-directed operations.** `routeTo`, `getTextInfoForWindowPos` — the window
   position identifies a row and column, which identifies a segment, and the position is
   rebased into that segment's coordinates.

Scroll operations take an optional explicit segment, by number or by key, which is how the
per-segment scroll commands reach a segment that does not have focus. `clear` accepts
either too.

## Which segment has focus

`focusSegmentNumber` selects the segment that receives regions with no explicit
`targetSegment`, and whose cursor is reported to the handler. It is configurable per
display; `-1` means the last segment, matching the 2023 convention. Internally the
container always stores a resolved, non-negative index, so that `-1` is a sentinel at the
boundary rather than a value that has to be interpreted everywhere.

The view names its focus segment by key rather than by number, and the container resolves
it once at construction. `focusSegmentKey` reports it back. That is what lets composition
reason about whether a claim would strand the focus.

`FakeRegionsList` is the list-like proxy that stands in for `container.regions`, because
`BrailleHandler` appends to and indexes that attribute directly rather than calling a
method. It resolves to the focus segment's real region list on every access, so that
changing the focus segment does not leave a stale binding behind.

## Directing content to a segment

A region carries an optional `targetSegment` attribute naming the segment it belongs to.
The replacement for `BrailleHandler._doNewObject` sorts an incoming region iterator by that
attribute, clears only the segments that are actually receiving regions, and then runs the
rest of NVDA's `_doNewObject` logic per segment. Regions without the attribute go to the
focus segment.

This is what preserves NVDA's `focusToHardLeft` behaviour, which depends on seeing a
region's position within its own group rather than within a mixed pile.

A segment that receives no regions is not cleared, which is what lets a pinned object
survive a focus change with no further intervention.

### A note on static analysis

The container does not inherit from `BrailleBuffer`, which is right at runtime but makes
pyright noisy. Narrowing `handler.mainBuffer` (declared as `BrailleBuffer`) with
`isinstance(..., DisplayContainer)` makes pyright synthesise an intersection of the
two classes, and method resolution in that synthetic class can pick `BrailleBuffer`'s
version of a same-named method. It reports, for instance, that `container.clear(index)`
passes too many arguments, having resolved `clear` to NVDA's no-argument version. These
are artifacts, not defects. Do not "fix" them by changing the call sites.

## Claims outlive a view

Two kinds of claim survive a rebuild, and the plugin owns both.

**Panels activated by code** are stored in `_activePanels` and re-composed onto the base
view on every rebuild, rather than being baked into a stored view. A settings change or a
display swap is therefore answered by rebuilding the same claims against the new base. A
claim that no longer fits is dropped and reported, not silently kept.

**Pinned objects** are held in `_monitors`, keyed by segment key. `_carryOverMonitors` keeps
every pin whose segment survived into the new container and drops the rest. A pin is
released only when its segment is genuinely gone — not merely because something else on the
display changed.

`plugin.activatePanel(panel)` is the entry point for code wanting part of the display, and
`plugin.deactivatePanel(name)` gives it back. `plugin.activateView(view)` still exists for
replacing the whole arrangement, but a panel should be preferred: a view replaces
everything, including segments other code is relying on.

A pin is released when its segment is gone, when a claim has taken over a segment of the
same key, or when its segment now follows the focus. The last is not hypothetical: changing
"segment that follows the focus" in settings to a segment that already held a pin would
otherwise let `refreshMonitors` clear the freshly drawn focus content and write the pinned
object over it.

Pinning into a segment another panel has reserved is refused with a spoken message, because
the owner would keep redrawing over it and the user would not be told why.

### What the claim contract does not yet do

`activatePanel` is marked experimental, and these are the reasons:

- **Geometry survives a rebuild; content does not.** A claim is re-composed, so its cells
  come back, but they come back empty and nothing tells the owner to redraw them. Speech
  output mode has the same shape: `_set_regions` empties every segment, and a pin can be
  restored with `refreshMonitors` while a panel has no equivalent at all.
- **Eviction is a log line.** An owner whose claim no longer fits is not told, and may go
  on producing regions targeted at segments that have gone.
- **Reserved does not mean exclusive.** A panel hosting the focus is both reserved for its
  owner and the destination for NVDA's untargeted regions, and `_doNewObjectMultiSegment`
  clears the focus segment on every focus change. Those two producers will fight. The
  likely answer is to split the single `owner` flag into reserved, hostsSystemFocus and
  exclusive.
- **Panels are held by reference.** `_activePanels` re-reads them on every rebuild, so
  mutating a panel after activation silently changes the view later.

All four want designing against a real consumer rather than in the abstract, which is why
the table reader comes before the lease API rather than after it.

## Reverse panning, per display

NVDA has no reverse panning setting (see [nvda-api-notes.md](nvda-api-notes.md)), so the
add-on provides one, stored per display.

The implementation swaps at the handler level: `BrailleHandler.scrollForward` and
`scrollBack` are patched so that, when the setting is on for the display whose key was
pressed, each delegates to the other's original implementation. Swapping there rather than in
the container means it applies to whatever gesture the display driver maps to panning,
including the built-in `braille_scrollForward` and `braille_scrollBack` commands, without
the add-on needing to know the driver's gesture names. It is safe to swap at that level
because the two handler methods are otherwise identical in their side effects (message
buffer timer reset, auto scroll timer reset).

Per display, not per arrangement: which of the two keys means forward is a fact about where
they sit on a piece of hardware. So when several displays are driven as one, the setting that
applies is the pressed display's, whichever display holds the segment that moves. `panning`
is how the pressed display is known, and the same answer picks the segment: the focus segment
when that display holds it, otherwise that display's first segment.

## Document lines

When enabled, the **free** segments are filled with `TextInfoPositionRegion`s reading the
document at a fixed line offset.

`documentLines.isFree` is the single place that decides what is free. Four things put a
segment out of bounds, and they arrive by different routes:

1. It follows the system focus, so it already shows the caret's own line.
2. `spec.owner` reserves it for a panel — a claim made when the view was composed. A
   table's grid cells are excluded this way, which is what stops nine cells being flooded
   with document lines the moment a table appears.
3. Its key is in the runtime-claimed set, because an object is pinned there. Pinning
   happens long after the view was composed, so it cannot be carried on the spec.
4. It has no `documentContextIndex`, so there is no line it could be showing.

### The offset is stated, not inferred

`SegmentSpec.documentContextIndex` gives a segment its place in the reading order, and the
offset between two segments is the difference between theirs. It cannot be derived:

- **Not from display order.** A panel may put several segments on one physical row. A three
  column grid consumes three indices per row band, so a segment below it would read three
  times too many lines away. This was a real defect: a segment seven rows above the focus
  read ten lines back.
- **Not from the row.** A single row display divided into columns puts every segment on row
  0, and those segments still want to read consecutively.

So the configured view's panels each carry their ordinal, and claimed panels carry `None`
by default. If the focus segment itself has no context — the focus moved into a table cell,
say — there is no caret line for anything to be relative to, and the document lines are
withdrawn rather than computed against a fiction.

## Two things a region can be told, and why they are separate

A region can carry two different pieces of information about segments, and conflating them
caused a real problem:

1. `targetSegment` is **an owner's explicit destination**. It is a claim. If it names a
   segment that no longer exists, the panel that owned it has gone, and the content has
   nowhere legitimate to go — so `resolvePlacementTarget` returns None and delivery fails.
   Falling back to the focus segment would paint a departed panel's content over the user's
   ordinary braille.
2. `_brlMultilineSegmentKey` is **this add-on's own note** of where an untargeted region was
   put, written by `recordPlacement`. It is bookkeeping, never a destination, and
   `resolvePlacementTarget` does not consult it.

Originally both were stored in `targetSegment`, because the `_doNewObject` patch stamped it
onto NVDA's own regions. That made every NVDA focus region indistinguishable from a panel's
claim, which is why a dead key could not simply be rejected.

`findContainingSegment` answers the different question of where a region *is*, for `focus`
and `scrollTo`. It searches by identity (`is`, never equality) before consulting the
recorded key, because identity is the only answer that cannot go stale.

The offsets never change. Nothing rotates as the caret moves; each region simply re-reads
its own line. This is the deliberate simplification against the 2023 ScrollingManager,
which tried to shuffle which region was current and was never made to work.

That leaves one problem: NVDA marks only the region belonging to the caret as needing an
update, so the offset regions would never refresh. Hence the third patch, on
`_handlePendingUpdate`. It runs the original, then refreshes any offset regions present.
`_handlePendingUpdate` fires once per core cycle and only when something is pending, which
is exactly when the caret has moved, so this is cheap and correctly timed. It replaces the
2023 prototype's fixed `wx.CallLater(500)`, which was a race rather than a fix.

A region whose offset falls outside the document renders blank rather than duplicating the
first or last line. Offset regions refuse `routeTo`, `nextLine`, and `previousLine`, so
that reading around the caret can never move the caret.

## Configuration

Settings are stored per display, keyed on display name plus rows plus columns, so that a
Monarch and a Focus 80 keep independent layouts and independent reverse panning settings.

The layout is a list of segment sizes: in rows when the display has more than one row, in
cells when it has one. A count alone means "divide equally into N".

Default is a single segment, so installing the add-on changes nothing until it is
configured.
