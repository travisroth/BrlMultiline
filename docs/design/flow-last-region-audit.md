# Audit: what NVDA does with the last region

Part of milestone 0 of [spatial-reading-plan.md](spatial-reading-plan.md). Read that first.

On a single line display, `mainBuffer.regions[-1]` is the last region in the buffer, which
is the only substantial thing on show and is therefore the region the cursor is in. NVDA
relies on that equivalence in nine places. A flow breaks it: the last region is the block
at the **bottom of the window**, and the cursor is usually somewhere else.

Worse, the usual guard does not catch it. Most of these sites check `region.obj == obj`
before acting, and in a flow every block over one document has the *same* `obj` — the tree
interceptor — so the guard passes on the wrong region rather than failing safe. A flow that
did nothing about this would not break loudly; it would quietly act on the bottom block.

Every line number below is NVDA master as of 2026.3.0dev, checked against
`C:\code\nvda\source`. Re-check them when NVDA is updated; this whole file is FRAGILE in
the sense used by [nvda-api-notes.md](nvda-api-notes.md).

## The call sites

1. **`brailleHandler.handleCaretMove`, `brailleHandler.py:848`.** Takes the last region,
   and if its `obj` matches, marks it `pendingCaretUpdate` and queues it for refresh.
   In a flow the `obj` matches for every block, so the bottom block would be refreshed and
   given the caret update while the reader's own block went stale. **Must act on the active
   block.**
2. **`brailleHandler.handleReviewMove`, `brailleHandler.py:961`.** The same shape for the
   review cursor, with the same failure. **Must act on the active block**, and a flow
   tethered to review is a case not yet designed — see the open question below.
3. **`BrailleBuffer._get_visibleRegions`, `buffers.py:87`.** If the last region has
   `hidePreviousRegions`, *only* that region is yielded. `TextInfoRegion.update` sets that
   flag whenever its reading position is not the start of its object, which for every block
   below the document's first line is always. A flow assembled as a region list would
   therefore show its bottom block alone. **The flow assembles its own rows and does not go
   through `visibleRegions` at all.**
4. **`BrailleBuffer._calculateWindowRowBufferOffsets`, `buffers.py:339`.** Reads
   `regions[-1].rawText` to decide whether to show the paragraph start marker. Harmless in
   a flow — a wrong answer costs one indicator — but it is reading the bottom block's text
   to describe the window, so **the flow computes the marker per block** if it supports it
   at all.
5. **`BrailleBuffer.scrollForward`, `buffers.py:359`.** When the window cannot scroll, calls
   `regions[-1].nextLine()`. **The flow does not use this fallback**: panning is the
   anchor's business, and moving to the next block is a flow operation.
6. **`BrailleBuffer.scrollBack`, `buffers.py:374`.** The same, with `previousLine`. Same
   answer.
7. **`brailleInput.updateDisplay`, `input/inputHandler.py:305`.** Sends untranslated braille
   input to the last region if it is a `TextInfoRegion`. With the cursor in a middle block,
   typed braille would be shown against the bottom block. **Must act on the active block.**
   This is the one most likely to be noticed as garbled typing rather than as a wrong row.
8. **`brailleInput.eraseLastCell`, `input/inputHandler.py:325`.** Checks the character
   before `region.cursorPos` on the last region to decide whether a backspace erased what it
   expected. Against the wrong block this check is meaningless and may refuse a legitimate
   erase. **Must act on the active block.**
9. **`script_braille_toFocus`, `globalCommands.py:4357`.** Takes the last region, and if its
   `obj` matches the focus, focuses the buffer on it and scrolls to its cursor. In a flow
   this would jump the window to the bottom block. **Must re-enter at the cursor's block by
   the entry rule**, which is what the command means on a multi row display anyway.
10. **`script_braille_previousLine` and `script_braille_nextLine`,
    `globalCommands.py:4326` and `4335`.** Call `previousLine(start=True)` and `nextLine()`
    on `buffer.regions[-1]`. These move by a reading unit and take the cursor with them.
    **Must act on the active block**, and in a flow they mean "step one block", which
    `FlowWindow.stepBlock` provides.

## The rule that follows

A flow exposes exactly one **active block**: the one holding the cursor. Every site above
that must reach the reader's own content is routed to the active block, and only the active
block exposes a braille cursor position. The rest are suppressed, because a collapsed
position in every block would otherwise make every block look like it holds the cursor —
`BrailleBuffer.update` assigns `cursorPos` from each region that reports one, so the last
one would win.

## Still to decide

- **Review tether.** Sites 2 and 9 behave differently when braille is tethered to review.
  A flow while tethered to review is not designed; the safe first behaviour is for the flow
  to stand down and let NVDA present review as it does today.
- **Braille input into a flow.** Sites 7 and 8 are correct once routed to the active block,
  but typing into a multi row window also grows the block being typed into, which is the
  "cursor's row stays visible" rule from milestone 4. Until that lands, braille input in a
  flow band should be treated as untested rather than supported.
