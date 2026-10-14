# NVDA braille API notes (NVDA 2026.3)

Findings from reading NVDA master (`C:\code\nvda`, `source/braille/`), which reports itself
as 2026.3.0dev. These are the facts the add-on depends on. When NVDA is updated, re-check
the items marked FRAGILE. None of this applies to 2026.1 or 2026.2, which still have the
single `source/braille.py` module.

## The braille package layout

`source/braille.py` no longer exists. It is a package:

- `braille/__init__.py` — holds `handler`, plus a deprecation shim mapping old names to
  new homes.
- `braille/brailleHandler.py` — `BrailleHandler`.
- `braille/buffers.py` — `BrailleBuffer`, `_WindowRowPositions`.
- `braille/regions/base.py` — `Region`, `TextRegion`, `RegionWithPositions`, `rindex`.
- `braille/regions/textInfo.py` — `TextInfoRegion`, `CursorManagerRegion`, review variants.
- `braille/regions/NVDAObject.py` — `NVDAObjectRegion`, `NVDAObjectHasUsefulText`.
- `braille/regions/focus.py` — `getFocusRegions`, `getFocusContextRegions`.
- `braille/display/` — `BrailleDisplayDriver`, `DisplayDimensions`.
- `braille/extensions.py` — extension points.
- `braille/constants.py` — `TEXT_SEPARATOR`, `CONTEXTPRES_*`, `CONTINUATION_SHAPE`, etc.

**FRAGILE.** The deprecation shim in `braille/__init__.py` is a module level
`__getattr__`. It resolves reads of old names, but it is a redirect only. Assigning to
`braille.SomeOldName` does **not** patch the real symbol, and importing from the new
location is required for `isinstance` checks to behave. Always import from the concrete
module.

## Display dimensions

`BrailleDisplayDriver` exposes `numRows` (default 1) and `numCols`; `numCells` is derived
as `numRows * numCols`. Drivers in tree that report more than one row: `dotPad`,
`hidBrailleStandard` (which is how a Monarch presents over HID braille), `brltty`.

`handler.displayDimensions` returns a `DisplayDimensions(numRows, numCols)` namedtuple,
and `handler.displaySize` is derived from it. Both are read only properties that raise
`AttributeError` on assignment; the sanctioned way to change them is the
`filter_displayDimensions` extension point in `braille/extensions.py`.
`filter_displaySize` still exists but is deprecated and forces `numRows` to 1.

Both properties are cached (`_cache_displayDimensions = True`), which matters if the
add-on ever needs a change to take effect immediately.

## BrailleBuffer is already row aware

This is the single biggest change since the 2023 prototype. `BrailleBuffer` in
`buffers.py` splits its window into rows itself:

- `_windowRowBufferOffsets` is a list of `_WindowRowPositions(start, end, showContinuationMark)`,
  one per display row.
- `_calculateWindowRowBufferOffsets` fills that list, honouring the `textWrap` feature
  flag: `NONE`, `MARK_WORD_CUTS`, `AT_WORD_BOUNDARIES`, `AT_WORD_OR_SYLLABLE_BOUNDARIES`.
  The syllable mode uses `textUtils.hyphenation`.
- `_get_windowBrailleCells` emits cells row by row, padding each row to `numCols` and
  appending `CONTINUATION_SHAPE` where a word was cut.
- `bufferPosToWindowPos` and `windowPosToBufferPos` translate through `divmod` on
  `numCols`, so a window position encodes both row and column.

So NVDA already flows *one* buffer across rows. What it does not do, and what this add-on
adds, is *several independent buffers* occupying different parts of the display.

### Which buffer methods consult display geometry

Verified by reading every use of `self.handler` in `buffers.py`:

- `bufferPosToWindowPos` — `handler.displayDimensions.numCols`
- `windowPosToBufferPos` — `handler.displaySize`, `handler.displayDimensions.numCols`
- `_calculateWindowRowBufferOffsets` — `handler.displayDimensions.numRows` and `numCols`
- `_set_windowEndPos` — `handler.displaySize`
- `focus` — `handler.displaySize`
- `_get_windowBrailleCells` — `handler.displayDimensions.numCols`
- `updateDisplay` — `handler.buffer` (identity check), `handler.update`

Nothing else. This is what makes the handler proxy approach in
[architecture.md](architecture.md) work: give a buffer a fake handler that reports a
smaller rectangle, and every one of these methods does the right thing for a segment
with no overriding at all.

Regions do **not** consult display geometry. The only `handler.` references in
`regions/base.py` and `regions/textInfo.py` are `handler.table` (braille table) and
`handler.autoScroll`.

## Handler behaviour the add-on has to cooperate with

### The handler pokes at `buffer.regions` directly

`BrailleHandler._doNewObject` does `self.mainBuffer.regions.append(region)`, and
`handleCaretMove` does `self.mainBuffer.regions[-1]`. It does not go through a method.
This is why the add-on needs a list-like proxy object (`FakeRegionsList`) standing in for
`regions` on the container, forwarding to whichever segment currently holds focus.

**FRAGILE.** If upstream ever routes these through a method, the proxy can be simplified.

### Region updates are batched

New since the 2023 prototype. `handleCaretMove` no longer updates immediately; it sets
`region.pendingCaretUpdate = True` and adds the region to `handler._regionsPendingUpdate`.
`_handlePendingUpdate` then runs once per core cycle (driven from `braille.pumpAll`) and
does the `saveWindow` / `update` / `restoreWindow` / `scrollToCursorOrSelection` dance for
the whole set at once.

This is the correct hook for anything that needs to observe caret movement, and it is the
right replacement for the 2023 prototype's `wx.CallLater(500)` timing hack.

### `_doNewObject` clears the whole main buffer

`_doNewObject` starts with `self.autoScroll(enable=False)` and `self.mainBuffer.clear()`,
then appends every region from the iterator, updates, focuses the last region, and scrolls
to the cursor. With multiple segments this is wrong: regions belonging to different
segments must be sorted first, and only the segments actually receiving new regions may be
cleared. Hence the add-on's replacement.

### `getFocusRegions` is imported by name

`brailleHandler.py` does `from .regions.focus import getFocusRegions` at module scope.
Patching `braille.getFocusRegions` or `braille.regions.focus.getFocusRegions` therefore has
no effect on the handler; the binding to patch is
`braille.brailleHandler.getFocusRegions`. The add-on currently avoids needing this patch at
all — see the ScrollingManager entry in [legacy-inventory.md](legacy-inventory.md).

## Cell output and the physical display

`BrailleHandler.update` reads `buffer.windowBrailleCells` and `buffer.windowRawText`, pads
to `displaySize`, sets `_cursorPos` from `buffer.cursorWindowPos`, and writes.

`_writeCells` then calls `_normalizeCellArraySize(cells, handlerCellCount,
displayDimensions.numRows, displayCellCount, display.numRows)` to reshape the handler's
row layout onto the physical display's row layout.

### Simulating a multi row display: it does not work

`_normalizeCellArraySize` iterates `for rowIndex in range(newNumRows)` where `newNumRows`
is the **physical** display's row count. Rows beyond that are never emitted.

So registering a `filter_displayDimensions` handler to tell NVDA that an 80 cell single
row Focus 80 is "2 rows of 40" does not produce two visible rows. Handler row 0 is written
to the display's only row, padded out to 80 cells, and handler row 1 is discarded — half
the display goes blank and half the content disappears.

The Focus 80 must therefore be driven as what it is: one row of 80 cells, divided into
column segments. That is a real use case, not a fallback, and it exercises the same
segment and container code paths as the Monarch does.

## Things that do not exist in NVDA, despite expectation

- **Reverse panning buttons.** There is no `reverseScrollBtns` or equivalent setting.
  Confirmed absent from `config/configSpec.py`, `gui/settingsDialogs.py`, every driver in
  `brailleDisplayDrivers/`, and the user guide. The scroll commands are
  `globalCommands.py` `script_braille_scrollBack` / `script_braille_scrollForward`, which
  call `handler.scrollBack()` / `handler.scrollForward()` with no direction option
  anywhere in the path. This add-on implements the feature itself, per display.

## Translation is per module

`addonHandler.initTranslation()` points `_`, `ngettext`, `pgettext` and `npgettext` at the
add-on's own translations for the module that calls it, and for no other: it sets them on the
calling frame's module (`addonHandler/__init__.py`). A module that uses `_` without calling it
gets NVDA's builtin `_`, which looks its words up in NVDA's own translations, which do not have
them. The build still extracts them into the .pot, so they would be translated and never shown.

Found 14 October 2026: eighteen modules, about 370 strings (the layered keys dialog, graphics mode,
the table manager and designer, the substitutions dialog, the chart and picture modules), had
never called it; only `__init__.py` and `settingsPanel.py` did. So:

1. **Every module with words to translate calls `addonHandler.initTranslation()`** after its
   imports. `tests/unit/brlMultiline/test_translations.py` fails for one that does not.
2. **The modules that also run without NVDA** (the layout overview's `layoutRegions`,
   `layoutDraw` and `appRegions`, tested against saved pages and loaded by the probe spikes)
   import the functions from `i18n.py` instead, which calls `initTranslation()` inside NVDA and
   gives English back anywhere else. `layoutRegions`, which the probe loads on its own from its
   file, falls back to English when the relative import fails.
3. **Words are translated when asked, not at import**, where a test needs to see them: the
   layout overview's kind names and braille box labels are functions (`kindName`, `shortLabel`),
   so a test can put in a translation of its own and check every word reaches the reader
   through it.
4. **`_` is never a throwaway name** in a module that translates: `for _ in range(...)` inside a
   function that also calls `_()` makes `_` local to it, and the call fails.
5. **Words a reader may have written are not translated as ours.** The shipped key layers' names
   are said in the reader's language only while a layer still has the English name it shipped
   with (`keyLayerDispatch._shippedName`); a layer the reader made or renamed keeps their name.
6. **Messages built from parts are built where they are said.** A pure module returns what is
   wrong as data (`keyLayers.LayerIssue`), with an English sentence for the log, and the dialog
   says it in the reader's language with layers called by their names, rather than putting an
   English reason inside a translated sentence.
7. **Words are matched in any script, in composed form, a combining mark belonging to its word**
   (`layoutRegions.words`): an accent can arrive as a mark of its own, and a vowel sign is part of
   the word, not a break in it.
