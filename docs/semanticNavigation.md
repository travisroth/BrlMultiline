# Semantic Navigation for Pinned Braille

## Problem Statement and Current Limitations

`ObjectMonitor` pins an object to one segment of the braille display and exposes it through
the ordinary panning commands.  Panning moves the display window by one window-width at a
time; when that window has scrolled to the edge of the current line it advances by one line
(`PinnedRegion.panLine`).  This means navigating a long document one line at a time, with
no way to jump by paragraph or heading.

In browse mode (virtual buffer) contexts NVDA already has rich semantic navigation: quick-nav
commands jump by heading, paragraph, landmark, and so on.  A pinned braille segment has no
equivalent, so a user reading a web page in a pinned segment and a web page in the focus
segment has only one efficient navigation path.

---

## Proposed Navigation Abstraction API

`SemanticNavigator` (`semanticNav.py`) wraps a pinned segment and exposes a single entry
point:

```python
result: NavResult = navigator.move(unit, forward)
```

`NavUnit` is a `str` enum with four values:

| Value | Description |
|-------|-------------|
| `NavUnit.DISPLAY` | Advance or retreat one display-width within the current line content |
| `NavUnit.LINE` | Move to the next or previous line in the text |
| `NavUnit.PARAGRAPH` | Move to the next or previous paragraph |
| `NavUnit.HEADING` | Move to the next or previous heading (browse mode only) |

`NavResult` is a named tuple:

```python
class NavResult(NamedTuple):
    moved: bool      # whether the reading position changed
    unit_used: str   # the unit that was actually applied
    reason: str      # explanation when fallback or failure occurred
```

---

## Supported Units and Capability Detection

`SemanticNavigator` exposes two capability queries before attempting a move:

```python
navigator.supportsHeading()    # True only for PinnedCursorManagerRegion whose
                               # tree interceptor exposes _iterNodesByType
navigator.supportsParagraph()  # True whenever there is a PinnedRegion in the segment
```

Internally, capability is inferred from the type of the last region in the segment:

- `PinnedCursorManagerRegion` — browse mode document (tree interceptor).  Supports heading
  when `treeInterceptor._iterNodesByType` is present; always supports paragraph and line.
- `PinnedTextInfoRegion` — editable control, terminal, or similar text provider.  Supports
  paragraph and line; heading is not meaningful here.
- Any other or absent region — only display-width panning is safe.

---

## Fallback Behavior

If the requested unit is not supported in the current context, the navigator tries coarser
units in order until one succeeds:

```
heading  →  paragraph  →  line  →  display
paragraph  →  line  →  display
line  →  display
display  →  (no further fallback)
```

`NavResult.unit_used` records which unit was applied, and `NavResult.reason` carries a
human-readable explanation when the result differs from what was requested.  The plugin
reports this to the user as a brief non-intrusive message.

---

## Key Integration Points and Command Bindings

`GlobalPlugin.moveMonitorInSegment(segmentNumber, unit, forward)` is the high-level
command handler.  It obtains or creates a `SemanticNavigator` for the named segment and
calls `navigator.move`, then optionally speaks a brief feedback string when fallback or
failure occurs.

Per-segment scripts are generated at import time for `LINE`, `PARAGRAPH`, and `HEADING`
units in both directions, following the same pattern as the existing scroll and monitor
scripts.  They are not bound to any gesture by default; users assign gestures through
NVDA's Input Gestures dialog.

Script names follow the pattern:

```
script_moveMonitorLineForward0   script_moveMonitorLineBack0
script_moveMonitorParagraphForward0   script_moveMonitorParagraphBack0
script_moveMonitorHeadingForward0   script_moveMonitorHeadingBack0
```

(0 … MAX_UI_SEGMENTS − 1 for each)

Existing scroll scripts (`script_scrollSegment*`) are not changed.

---

## Low-Level Movement Primitives

Two new methods are added to `PinnedRegion` (in `pinnedRegions.py`):

### `panUnit(unit, forward)`

Moves the private position by one unit of an arbitrary `textInfos` granularity
(`textInfos.UNIT_PARAGRAPH`, for example) and re-renders the region.  Follows the same
logic as the existing `panLine`.

### `panHeading(forward)` — `PinnedCursorManagerRegion` only

Tries the tree interceptor's `_iterNodesByType("heading", ...)` heading iterator first.
Falls back to `panUnit(textInfos.UNIT_PARAGRAPH, ...)` when heading iteration is
unavailable or raises.  Falls back further to `panLine` when paragraph movement also fails.

---

## Testing Approach

Unit tests (`tests/unit/brlMultiline/test_semanticNav.py`) cover:

- `NavUnit` enum values and `NavResult` fields.
- `SemanticNavigator` capability detection against the stub region types.
- Successful line, paragraph, and display-width moves.
- Fallback from heading to paragraph to line when the context does not support the
  requested unit — using the `FakeTreeInterceptor` stub, which has no heading iterator.
- Boundary behaviour (at start or end of document, move returns `moved=False`).
- `PinnedRegion.panUnit` with paragraph unit.

Heading iteration against a real NVDA tree interceptor (`_iterNodesByType`) cannot be
exercised by the offline test suite; it is covered by the manual checklist below.

---

## Manual Test Checklist

The following should be verified against a running NVDA installation:

- [ ] Open a web page with headings in a browser.
- [ ] Pin the document to a segment that is not the focus segment.
- [ ] Use the heading-forward script; confirm the pinned segment jumps to the next heading.
- [ ] Use the heading-back script; confirm the segment returns to the previous heading.
- [ ] Repeat on a page with no headings; confirm the script falls back to paragraph and
      says so briefly.
- [ ] Use the paragraph-forward/back scripts on a long web page; confirm paragraph-level
      jumps.
- [ ] Use the line-forward/back scripts; confirm line-level movement (same as existing
      scroll behavior).
- [ ] Pin an edit field; confirm heading scripts fall back and say so.
- [ ] Pin a control with no text info; confirm all semantic scripts fall back to display pan.
- [ ] Toggle the segment layout off and on; confirm all scripts are still unbound (as before)
      and can be re-assigned in Input Gestures.
- [ ] Use the existing scroll scripts; confirm they still work exactly as before.

---

## Known Limitations

- **Heading movement** requires `treeInterceptor._iterNodesByType`, which is a NVDA
  internal API and is not guaranteed to be present in every virtual buffer implementation.
  The fallback to paragraph covers the gap silently.

- **Landmark and other quick-nav types** are not yet surfaced.  The architecture is open to
  adding more `NavUnit` values by extending the fallback chain and adding matching methods.

- **Paragraph boundary detection** relies on `textInfos.UNIT_PARAGRAPH` support in the
  underlying text provider.  Edit fields with flat text models and remote/UIA controls with
  coarse text info may not honour it.

- **No scroll-wheel or hardware binding by default** — gestures must be assigned
  explicitly.
