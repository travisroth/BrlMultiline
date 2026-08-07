# Legacy inventory: the 2023 brailleMultiline prototype

The prototype lives at
`C:\AccessibilityPartner Dropbox\Code\brailleMultiline`, last commit August 2023
(`c6e6d8f`). It was written against NVDA 2023.x, when braille was a single
`source/braille.py`. This document records what was in it, what became of each piece, and
the specific defects found — so that a decision to revisit an idea can be made on
evidence rather than memory.

Four commits total: initial commit, initial global plugin, focus tracking fixes plus
ObjectMonitor, and the single-buffer-adds-a-second option.

## Files

1. `brailleBufferMultiline.py`, 1089 lines — concepts carried over, code rewritten.
2. `objectMonitor.py`, 67 lines — concept carried over, rewritten from scratch.
3. `__init__.py`, 249 lines — global plugin, settings, scripts; rewritten.
4. `b.py`, `i.py`, `log.txt` — not code. UTF-16 `git diff` and `git log` dumps saved with a
   `.py` extension. Discarded.

## Feature by feature

### BrailleBufferSegment — concept kept, implementation discarded

A `BrailleBuffer` subclass carrying its own `segmentSize`, overriding `_get_windowEndPos`,
`_set_windowEndPos`, `focus`, and `_get_windowBrailleCells` with copied bodies edited to
substitute `segmentSize` for `handler.displaySize`.

All four overrides became unnecessary under the handler proxy approach. They were also a
maintenance liability: each was a snapshot of NVDA internals that upstream has since
rewritten substantially (row splitting, continuation marks, syllable hyphenation).

### BrailleBufferContainer — kept, rebuilt

The right idea, in draft condition. Defects found:

1. `sef.focusBufferNumber` in `append` — typo, raises `NameError` on every non-iterable
   append.
2. `append`'s `else` branch references `r`, which is not defined in that scope; it should
   be `region`. The single-region path was dead.
3. `self.bufferSegments[ssegment]` in `_nextWindow` — typo.
4. `scrollBack`'s focus tracking branch is inverted relative to `scrollForward`: when a
   scroller exists it calls the plain default, and when one does not it dereferences
   `self.scroller.mainLineIndex` on `None`. Guaranteed crash.
5. `clear(segment=None)` never assigns `trackingFocus`, so the trailing
   `if trackingFocus` raises `UnboundLocalError`.
6. `routeTo` contains `if braillePos == 79: self.debugBuffer(); return` — a debug escape
   hatch that swallows the last routing key on an 80 cell display.
7. Bare `except: pass` in `focus`, `scrollTo`, `saveWindow`, `restoreWindow`. These are
   why problems surfaced later as wrong output rather than as tracebacks.
8. Cell assembly by concatenation, which is only correct for a single row display.

### FakeRegionsList — kept

Still necessary; NVDA still manipulates `mainBuffer.regions` directly. The 2023 version
implemented only `__getitem__`, `__len__`, and `append`; the rewrite makes it a complete
sequence proxy.

### Routing key mapping — concept kept, math rebuilt

`getBufferSegment`, `getWindowLeadingCells`, `routeTo`. The arithmetic assumed a flat
single row display and branched on `type(self.segments) == int` versus `== list`. Replaced
by rectangle geometry.

### Settings and per-display config — kept, tightened

Config keyed on `display.name + str(displaySize)`. Now keyed on name plus rows plus
columns, since displaySize alone cannot distinguish geometries.

Validation was placeholder grade: bare `int()` on raw text with no exception handling, and
silent rejection of out-of-range values.

Three declared keys were never read anywhere in the 2023 code:

- `reverseScrollBtns` — **implemented in the rewrite.** Not dead; it was a planned feature
  that never got written. NVDA has no such setting of its own (verified — see
  [nvda-api-notes.md](nvda-api-notes.md)), so the add-on provides it, per display, because
  it is wanted on the Focus 80 and not on the Monarch.
- `backup_tetherTo`, `backup_autoTether` — dropped. These were state-restoration scaffolding
  for the `shouldAutoTether` patch, which is itself dropped (below).

### Per-segment scroll scripts — kept, generalised

Eight near-identical scripts, `scrollBackLineZero` through `scrollForwardLineThree`, none
with a default gesture. Now generated rather than hand-written.

### ObjectMonitor — concept kept, code discarded

Intent: pin an object to a chosen segment so it stays visible while focus moves elsewhere.
Essentially unimplemented:

- `loadBuffer` does `segment[n].regions = segment[focus].regions`, which aliases the same
  list object rather than copying, so the two segments then mutate each other.
- `getRegions` — the method that does the right thing, generating fresh regions with
  `targetSegment` set — is never called.
- `saveBuffer` is dead code wrapped in a bare except.
- `startObjectMonitoring` always stores into `objToMonitor[1]` regardless of the requested
  buffer number, while `stopObjectMonitoring` deletes by the requested number. They do not
  match.

### TextInfoPositionRegion — concept kept, re-derived

A `TextInfoRegion` rendering the line at ±N from the caret. Valuable: this is what puts the
lines around the caret on a multi row display.

The 2023 implementation copied the entire body of `TextInfoRegion.update()` into the
add-on in order to splice in a `wx.CallLater(500)` delay waiting for `textInfo.move` to
settle. That copy will not even import against NVDA 2026.3 — the method now maintains
`_languageIndexes`, calls `_getDefaultRegionLanguage`, and imports braille input from
`braille.input` rather than `brailleInput`.

The rewrite subclasses properly, overriding only `_getSelection`, `nextLine`, and
`previousLine`, and takes its timing from NVDA's `_regionsPendingUpdate` mechanism (which
did not exist in 2023) instead of a fixed timer.

### ScrollingManager — dropped for now

Owned N `TextInfoPositionRegion`s and tried to shift which one was "main" as the caret
moved. Not a `Region`, but passed around as one, which forced both the `getFocusRegions`
patch and special-case unpacking inside `_doNewObject`.

It was unfinished, by its author's own annotations: `swapLines` is called from nowhere live
(the call site in `updateCallback` is commented out), the real list swap is disabled with
the comment "this makes NVDA mad", `_log` is a debug string accumulated on every update and
dumped by a keystroke, and `initializeLines` computes its `diff` with opposite sign in the
two versions present in the tree — so even the line ordering was unsettled.

Dropping it also removes the only reason for the `getFocusRegions` patch, which has become
harder upstream (the handler imports the name directly at module load).

Worth revisiting only after the container is solid and `TextInfoPositionRegion` is proven
with fixed line assignment.

### Monkey patches

- `BrailleHandler._doNewObject` → sorts regions per segment. **Kept**, re-derived from
  current source (which now opens with `self.autoScroll(enable=False)`).
- `braille.getFocusRegions` → **dropped** along with ScrollingManager.
- `BrailleHandler._get_shouldAutoTether` → **dropped, it was a live bug.** The replacement
  returns `isFocus and self.enabled and ...` where `isFocus` comes from
  `hasattr(self, "isFocusBuffer")`. But `self` is the *handler*, which never has that
  attribute, so the function returned `False` unconditionally and silently disabled auto
  tethering for all of NVDA. A strong candidate for "it stopped following things properly".
- `monkey_handleCaretMove`, `monkey_handlePendingCaretUpdate`, `monkey_doCursorMove`,
  `scrollForwardMonkey` → **dropped.** All commented out at the patch site; debug
  scaffolding, one of which called `speech.speakMessage` on every scroll.
