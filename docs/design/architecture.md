# Architecture

Read [nvda-api-notes.md](nvda-api-notes.md) first; this document assumes the facts
established there.

## Module map

- `layout.py` — segment geometry and the fill-rows arithmetic. Imports nothing from NVDA,
  so it can be unit tested.
- `views.py` — `SegmentView`, routing policies, and the configured and single segment views.
- `segments.py` — `_SegmentHandlerProxy` and `BrailleBufferSegment`.
- `container.py` — `BrailleBufferContainer` and `FakeRegionsList`.
- `patches.py` — the three patches to `BrailleHandler`, installed and removed together.
- `documentLines.py` — `TextInfoPositionRegion` and the code that fills free segments.
- `objectMonitor.py` — pinning an object to a segment.
- `bmConfig.py` — per display configuration. Named to avoid shadowing NVDA's `config`.
- `settingsPanel.py` — the NVDA settings category.
- `__init__.py` — the global plugin: lifetime, commands, and object monitor state.

## The problem in one paragraph

`BrailleHandler` owns exactly one `mainBuffer` and treats the display as one window onto
it. To get several independent areas on the display, something has to stand in the place
of that single buffer, present the combined result as if it were one buffer, and fan the
individual operations out to the real buffers behind it. That stand-in is
`BrailleBufferContainer`, and the real buffers behind it are `BrailleBufferSegment`s.

## Everything on the display is a view

A `SegmentView` is a complete way of using the display. It carries four things:

1. the rectangle each segment occupies,
2. which segment follows the system focus,
3. whether segments fill their rows or wrap at word boundaries,
4. what a cursor routing key press does.

There is no separate "plain" path. The single segment arrangement that matches NVDA's own
behaviour is a view; the layout the user configures in the settings dialog is a view; a
table reader's grid would be a view. One construction path, one place to validate, one
thing to swap.

`viewFromConfig` builds the user's configured view, and `singleSegmentView` builds the
fallback used when anything does not fit. Code driving the display directly builds its own
and calls `plugin.activateView(view)`; `plugin.restoreConfiguredView()` hands the display
back. Only one activated view exists at a time — activating a second replaces the first,
rather than stacking, because out-of-order teardown between two components that each think
they own the display is a bug waiting to happen.

If the display changes under an activated view, the view is validated against the new
geometry and dropped if it no longer fits. Rectangles are geometry-specific, so a view
built for a Monarch is almost never valid on a Focus 80.

### A view must have a focus segment

Deliberate constraint, worth revisiting when there is a real case against it. NVDA reads
`mainBuffer.regions[-1]` in `handleCaretMove`, `handleReviewMove`, and two global commands,
so there always has to be a segment for untargeted content to land in. A view with no focus
segment would leave those reads with nothing to resolve to.

A table view is expected to designate whichever cell is current, which is probably the
right behaviour anyway rather than a workaround.

### How many segments

There is no fixed limit. The real constraint is geometric — segments must fit inside the
display and not overlap — and `validateRects` enforces exactly that. Gaps are allowed and
render blank, which is how a view puts gutters between segments.

`MAX_UI_SEGMENTS` is 8, and it constrains only the settings dialog, the config spec, and
how many per segment commands are generated. A view built in code may have more segments
than that; a 4 by 3 grid on a Monarch has twelve. Note that segments past the eighth have
no generated scroll commands, which is part of why routing policies exist.

### Routing policies

`RoutingPolicy.route(container, segmentNumber, segmentPos)` decides what a routing key
press does. The default routes within the segment pressed, which is ordinary display
behaviour, and is what every view gets unless it says otherwise.

`EdgeRowScrollRoutingPolicy` is a worked example for views with more segments than there
are keys to drive them: a press in a segment's top row scrolls it back, one in its bottom
row scrolls it forward, anything between routes normally. It is a starting point, not a
settled design — whether the edge rows should be per segment or per display, and whether
giving up two rows of three is acceptable, wants deciding against real content.

The hook lives in `container.routeTo`, so no additional patch to NVDA was needed:
`handler.routeTo` already delegates straight to the buffer.

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

`BrailleBufferContainer` presents the `BrailleBuffer` interface to `BrailleHandler`
without inheriting from it (it derives from `baseObject.AutoPropertyObject`, so NVDA's
`_get_` / `_set_` auto property protocol works).

Its central job is producing `windowBrailleCells`. The 2023 prototype concatenated each
segment's cells end to end. That is correct only when the display is a single row: NVDA's
cell array is row major, so on a multi row display a segment narrower than the display
would land in the wrong place.

Instead the container allocates a blank `numRows * numCols` array and blits each segment's
window cells into it at the segment's origin. One routine, correct for column slices,
whole row groups, and grids alike.

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

Scroll operations take an optional explicit segment number, which is how the per-segment
scroll commands reach a segment that does not have focus.

## Which segment has focus

`focusSegmentNumber` selects the segment that receives regions with no explicit
`targetSegment`, and whose cursor is reported to the handler. It is configurable per
display; `-1` means the last segment, matching the 2023 convention. Internally the
container always stores a resolved, non-negative index, so that `-1` is a sentinel at the
boundary rather than a value that has to be interpreted everywhere.

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
`isinstance(..., BrailleBufferContainer)` makes pyright synthesise an intersection of the
two classes, and method resolution in that synthetic class can pick `BrailleBuffer`'s
version of a same-named method. It reports, for instance, that `container.clear(index)`
passes too many arguments, having resolved `clear` to NVDA's no-argument version. These
are artifacts, not defects. Do not "fix" them by changing the call sites.

## Reverse panning, per display

NVDA has no reverse panning setting (see [nvda-api-notes.md](nvda-api-notes.md)), so the
add-on provides one, stored per display.

The implementation swaps at the handler level: `BrailleHandler.scrollForward` and
`scrollBack` are patched so that, when the setting is on for the current display, each
delegates to the other's original implementation. Swapping there rather than in the
container means it applies to whatever gesture the display driver maps to panning,
including the built-in `braille_scrollForward` and `braille_scrollBack` commands, without
the add-on needing to know the driver's gesture names. It is safe to swap at that level
because the two handler methods are otherwise identical in their side effects (message
buffer timer reset, auto scroll timer reset).

## Document lines

When enabled, the segments that are neither the focus segment nor holding a pinned object
are filled with `TextInfoPositionRegion`s reading the document at a fixed line offset:
segment k shows the line `k - focusSegmentNumber` away from the caret.

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
