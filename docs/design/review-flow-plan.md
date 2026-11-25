# Flow and the review cursor

Plan for a flow that follows NVDA's review cursor when braille is tethered to review, as it
follows the caret when braille is tethered to focus. The case that drives it is Windows Terminal
and the other consoles, where the caret sits at the prompt and the reader reviews the output
above it, a line at a time, while more output arrives.

Read [spatial-reading-plan.md](spatial-reading-plan.md) first, and
[flow-last-region-audit.md](flow-last-region-audit.md), whose "Review tether" item this plan
answers.

Status: planned. Nothing below is built.

## What happens today

The flow is bound to the caret everywhere it asks where the reader is, and never asks NVDA where
the review cursor is. Panning works under any tether, because panning is the band's own operation
over blocks it has already read. Everything else follows the caret:

1. **Blocks are built from caret regions.** `flowBuild` asks `getFocusRegions(obj, review=False)`
   for the region class to read with, so blocks are `TextInfoRegion` and `CursorManagerRegion`
   kinds, whose `_getSelection` is the caret. NVDA's review counterparts,
   `ReviewTextInfoRegion` and `ReviewCursorManagerRegion`, answer `_getSelection` with
   `api.getReviewPosition()` and `_setCursor` with `api.setReviewPosition`.
2. **The source finds the reader by the caret.** `DocumentFlowSource.blockAtCursor` and
   `caretPosition` read `POSITION_SELECTION`, and `FlowRegion._liveCursorHere` reads the region's
   own `_getSelection`.
3. **A review move does reach the flow, and is answered from the caret.** NVDA's
   `handleReviewMove` (`brailleHandler.py`, around line 951) takes the buffer's last region,
   which `FlowBufferSegment._syncRegions` makes the active block. In a terminal its object is the
   review position's object, so NVDA marks it for a caret update. The re-read sets the region
   `dirty`, `FlowBufferSegment._followIfRead` calls `followCursor`, and `_arrive` reads the caret,
   which has not moved. So nothing moves. The wiring is already there; only the position read is
   wrong.
4. **A review move to another object rebuilds the regions** through `_doNewObject`, which the
   band answers through `handleFocusRegions` and `showObject`. It shows the right object, read
   from that object's caret.
5. **New output is answered as a caret move.** A terminal's `event_textChange` calls
   `braille.handler.handleUpdate`, which re-reads the active block. `FlowRegion.update` sets
   `dirty` on every re-read, so the flow follows the caret back to the prompt. Under a review
   tether that is the display moving when the reader did not.
6. **Panning and routing move the caret.** `FlowRegion.takeCursor` and the live `routeTo` call
   the caret region's `_setCursor`, whatever the tether.
7. **The tether is never asked.** The only call to `getTether` in the add-on is the
   focus-to-hard-left rule in `patches.py`. The audit's safe fallback, a flow that stands down
   while tethered to review, was not built either.

## Verdict

Worth doing, and smaller than it looks, because NVDA already brings review moves to the active
block (item 3). The work is one idea applied in the places listed above: the flow reads and moves
**the reader's cursor**, which is the caret under a focus tether and the review position under a
review tether.

## Decisions

1. **The tether decides, and nothing else.** The flow reads by review exactly when
   `braille.handler.getTether()` is review, whether the reader chose it (NVDA+Control+T) or NVDA
   chose it automatically. No setting of the add-on's own. A reader who wants the flow to follow
   the caret in a terminal tethers to focus, as they would without the add-on.
2. **One question, asked in one place.** A small module, `flowCursor.py`, answers "where is the
   reader" and "put the reader here": `position(obj)` and `move(info)`. Under a focus tether it is
   what the code does now. Under a review tether it is `api.getReviewPosition()` and
   `api.setReviewPosition(info)`. `blockAtCursor`, `caretPosition`, `_liveCursorHere`,
   `takeCursor` and routing ask it, rather than each choosing. Keeping it in one module is what
   stops half the paths following review and half the caret, which is today's fault in miniature.
3. **What is read follows the review position's object.** Under a review tether the band's
   target is `api.getReviewPosition().obj`, not the focus object, so object navigation brings the
   band onto what the reader moved to. `FlowBand._target` asks `flowCursor` too.
4. **Review moves are followed as caret moves are.** The least movement that shows the review
   cursor's block, grounding only on a jump. Line, word and character review keys therefore move
   the window as the arrow keys do in browse mode, with nothing new on the band's side.
5. **Content updates under a review tether do not move the window.** A re-read that was not a
   review move refreshes the blocks in place, as `rereadContent` does for a live table, and the
   window stays where the reader is. To tell the two apart, the flow notes the review position it
   last followed (`_reviewMark`, compared by bookmark) and follows only when NVDA's review
   position differs from it. A block that output pushed past the top of the window is the
   reader's to pan back to; the flow does not chase the output. A review position that moved
   only because the caret did is not a review move either; see decision 8.
6. **Panning, stepping and routing move the review cursor under a review tether.** `takeCursor`
   becomes `flowCursor.move`, so after a pan the review cursor is on the top block, as the caret
   is now under a focus tether. A routing press sets the review position where it lands, and
   moves the caret only when NVDA's "move system caret when routing review cursor" setting says
   so, through NVDA's `_routingShouldMoveSystemCaret`, as `ReviewTextInfoRegion` does. A press on
   the cursor activates, as NVDA's does.
7. **Moving the review cursor must not send the flow round again.** `api.setReviewPosition`
   calls `handleReviewMove` itself, which marks the active block and reaches `followCursor`. The
   move first updates `_reviewMark`, so that pass finds nothing to follow and the window stays
   where the pan put it. This is the review counterpart of `_caretIsWhereThePanLeftIt`.
8. **In a terminal, the flow holds against output, and NVDA's tether cannot do it alone.** This
   is the case Braille Essentials issue 6 describes, where output keeps pulling the display back
   while the reader is reading. The plan first relied on NVDA's automatic tether for it; phase 0
   showed it cannot work. Windows Terminal moves the caret for every line of output, and NVDA
   answers each caret move in two ways: `handleCaretMove` takes an automatic tether back to focus,
   and, with "review cursor follows system caret" on, NVDA moves the review position to the caret.
   The second happens under a manual review tether too, and reaches the flow as
   `handleReviewMove(shouldAutoTether=False)`, where review commands and object navigation call
   it with True. So a reader reviewing output loses both the tether and their review position
   once a second, with or without the add-on.

   The flow therefore keeps a hold of its own, in a terminal only (an object with NVDA's `Terminal`
   behaviour), since elsewhere the caret moves only when the reader moves it:

   1. A review move by the reader (a review command, a pan, a routing press) starts the hold,
      and notes the review position as `_reviewMark`.
   2. While it holds, a caret move with no key pressed since the hold began is output. The flow
      does not follow it, and puts NVDA's review position back to `_reviewMark` if the caret
      dragged it away, so the reader's next review key starts from where they were reading.
      Under an automatic tether it also keeps the tether on review.
   3. Any key the reader presses ends the hold, and from then on the flow follows the caret, as
      NVDA would.

   How the flow learns that a key was pressed is open question 5.
9. **Where review cannot be flowed, the flow stands down.** Screen review, and an object whose
   review position is a display model rather than the object's own text, are shown by NVDA as it
   does today: the band hands the regions back from `handleFocusRegions`, as it already does for a
   focus that cannot be read as a flow. This is also the first thing built, so that a review
   tether is never worse than NVDA's own presentation while the rest lands.
10. **Tables, the row on one line, quick navigation and editing stay with the caret.** They are
    browse mode and edit features driven by the caret, and under a review tether the band reads
    such content as lines, by review. Whether a table laid out in columns should follow the
    review cursor is an open question, not part of this plan.

## Where each piece goes

1. `flowCursor.py`, new: the tether question, `position`, `move`, `mark`, and `isReviewing`.
   NVDA free where it can be, with `api` and the handler passed in, so it is unit testable.
2. `flowSources.DocumentFlowSource`: `blockAtCursor` and `caretPosition` read from `flowCursor`.
3. `flowSources.FlowRegion`: `_liveCursorHere`, `takeCursor` and `routeTo` through `flowCursor`.
   Blocks keep their caret classes; only the cursor they ask about changes, so a change of tether
   needs no rebuild of the blocks, only a follow.
4. `flowControl.FlowController`: `_reviewMark`, the follow-or-refresh split of decision 5, and the
   pan guard of decision 7.
5. `flowSegment.FlowBufferSegment._followIfRead`: asks the controller whether a re-read was a
   review move before following.
6. `flowBand.FlowBand`: `_target` through `flowCursor`, and standing down for decision 9.
7. A change of tether clears NVDA's buffer (`setTether` calls `mainBuffer.clear()`), and the next
   focus or review move rebuilds it. The band then follows the new cursor. Worth confirming in
   phase 0 that the container survives the clear as it survives a display change.

## Order of work

1. **Phase 0, a probe.** Log, with the flow on, what reaches `handleReviewMove`, `handleUpdate`,
   `_followIfRead` and `_doNewObject` for: review by line, word and character in Windows Terminal
   and in a classic console; object navigation out of the terminal and back; output arriving
   while reviewing; NVDA+Control+T both ways; and the same review keys in Notepad and a web page.
   Confirm items 3 and 5 above on hardware, and whether Windows Terminal's text range is cheap
   enough to read a band's worth of review lines on each move. `spikes/reviewFlowProbe.py` is
   the probe; its docstring is the runbook. Done, apart from a console in its own conhost window
   and screen review: see "Phase 0 results" below.
2. **Phase 1, stand down.** Decision 9 alone, so a review tether shows NVDA's own review until the
   rest is built. Unit tested against stand-ins for the tether.
3. **Phase 2, read by review.** `flowCursor`, decisions 1 to 4, and the source and region changes.
   Review keys move the window. Hardware check: review by line through a long command's output in
   Windows Terminal on the Monarch, then in Notepad and a web page.
4. **Phase 3, hold still and move the review cursor.** Decisions 5 to 7. Hardware check: start a
   command that prints a line a second, tether to review, review up through the output and pan,
   and confirm the display does not move until the reader does; route to a line and confirm the
   review cursor is there.
5. **Phase 4, holding against output in a terminal.** Decision 8. Hardware check: with a loop
   printing a line a second, under an automatic tether and then a manual one, review up through
   the output and pan; confirm the display and the review cursor hold while output arrives, the
   next review key moves on from where the reader was, and a keystroke returns the display to the
   prompt.

## Phase 0 results

### First run, 25 November 2026

On the Monarch, with the flow on and tethering automatic. The probe then kept only its last 600
records, which dropped the first six minutes of the run: the Windows Terminal scenarios for
object navigation, output arriving, and NVDA+Control+T, and the timing of reading review lines.
What survived is review by line in Windows Terminal, in Notepad and on a web page in Chrome. The
scenario marked "classic console" ran in Windows Terminal too, by the class NVDA reported, so a
console in its own conhost window is still untried. The limit is now 20000 records.

1. **Item 3 is confirmed, in all three.** Every review move by line reached the flow's active
   block. `handleReviewMove` found the buffer's last region was the active block
   (`FlowTextInfoRegion` in the terminal and Notepad, `FlowCursorManagerRegion` in Chrome), the
   automatic tether went to review, the block was re-read and `dirty`, and `_followIfRead` called
   `followCursor`. The review line changed from one move to the next (in the terminal, "Count:
   33", "Count: 32", "Count: 31" and back) while the band's top row stayed where it was. The
   display does not follow the review cursor; it follows the caret, which had not moved.
2. **The first review move in the terminal moved the band, though not to the review line.** The
   top row went from the prompt to a blank row when the tether went to review, and stayed there
   for every later move. It is the re-read of a block whose position is the caret's, laid out
   again; phase 2 replaces it.
3. **The container survives a change of tether.** After every `setTether` the buffer was still a
   `DisplayContainer`, so where-each-piece-goes item 7 needs no work.
4. **A review move costs the flow 21 to 25 ms in Windows Terminal**, and 40 to 55 ms in Notepad,
   for a re-read and a `followCursor` that moved nothing. That is the cost of following before
   any review line is read, and sets the baseline phase 2 is measured against.
5. **A caret move tethers back to focus at once.** In each of the three scenarios, the first
   caret move after the review moves took the tether from review back to focus, through
   `setTether('focus', auto=True)`, as decision 8 relies on.

### Second run, 25 November 2026

Nothing new. The console still had the first copy of the probe loaded, since an import of a
module Python already holds does not read the file again, so the 600 record limit still applied.
The 600 kept were almost all caret moves in Notepad, with one focus move into Windows Terminal and
no review moves there. The timing ran while the console was open, so it timed the console's own
output, one line in 0.2 ms, which says nothing about the terminal. The runbook now reloads the
probe, the probe says its version when it starts and in its report, and the timing waits five
seconds before reading, so the reader can leave the console first.

### Third run, 25 November 2026

Probe version 2, 2071 records with none dropped, in Windows Terminal running PowerShell, with
tethering automatic except where NVDA+Control+T set it.

1. **Review by line, word and character confirms the first run.** 17 review moves through the
   output ("Count: 15" down to "Count: 12" and back) reached the active block, each costing the
   flow 22 to 27 ms, and the band's top row stayed put. The first review move shifted the band from
   the prompt to a blank row, as in the first run.
2. **Output moves the caret for every line, and that defeats review.** With a loop printing a line
   a second, Windows Terminal sent a caret move for each line, about 33 in the scenario, along with
   188 updates. Each caret move took the automatic tether back to focus (`setTether('focus',
   auto=True)`), and with "review cursor follows system caret" on, moved the review position to
   the new caret line. The reader's review keys moved the review cursor up ("Count: 6", "Count:
   7", ...) and within a second the next line of output had put it back on the caret. This is
   NVDA's behaviour, not the flow's, and it is why decision 8 now keeps a hold of its own.
3. **A review position dragged by the caret reaches the flow as
   `handleReviewMove(shouldAutoTether=False)`.** Review commands and object navigation call it
   with True. Under the manual review tether, typing at the prompt moved the review position to the
   caret line by that path, and the tether stayed on review. The loop had stopped by then, so
   output under a manual tether was not seen, but it takes the same path in NVDA's code
   (`api.setReviewPosition` with `isCaret=True`), so it would drag the review cursor the same way.
4. **NVDA+Control+T works both ways and the container survives it.** `setTether` with `auto=False`
   both ways, followed by `handleGainFocus` or `handleReviewMove`; the buffer stayed a
   `DisplayContainer`.
5. **Object navigation already stands down where there is no text.** Moving the review cursor by
   object to the terminal's scroll bar buttons and other objects without text gave NVDA
   `ReviewNVDAObjectRegion`s, and the band handed them back, so NVDA showed them itself. Moving
   back to the terminal's text object, the band took it again and drew the prompt, which is where
   NVDA had put the review cursor, read by the caret. Decision 9 is therefore partly there already
   for objects; screen review is still untried.
6. **Reading review lines is cheap.** `timeReviewLines(8)` read 2 lines, the review cursor being
   near the end of the output, in 0.9 ms, about half a millisecond a line. Reading a band's worth
   of review lines on each move is not where the cost is; the flow's own 22 to 27 ms per move is.
7. **A console in its own conhost window could not be tried.** Windows Terminal is the default
   terminal on this machine, so cmd.exe opened in Windows Terminal too. Worth trying where conhost
   is the default, or with Windows Terminal set not to be.

Phase 0 is done, apart from conhost and screen review.

## Open questions

1. **Should a table laid out in columns follow the review cursor?** Decision 10 keeps it with the
   caret. A reader who reviews a web table by object navigation may want the columns to come
   along. Wait for a case.
2. **Is a pan without the review cursor wanted?** Decision 6 moves the review cursor on every pan,
   as NVDA's review region does. A reader may want to glance at the output and come back to the
   same review position. If so, it is a keyboard or display key, "back to the review cursor",
   rather than a change to panning.
3. **What does the band show when output scrolls the review position's line out of the
   terminal's buffer?** A terminal with a limited scrollback can drop the line under the review
   cursor. NVDA's review position is then wherever the terminal's text range leaves it. Phase 0
   should note what happens; the likely answer is to follow NVDA's review position as it stands.
4. **Does a pin want this?** A pin is never tethered and reads from its own position, so it is
   out of scope. A pinned terminal already shows its own lines and pans on its own.
5. **How does the flow learn that the reader pressed a key, for decision 8's hold?** It must see
   keyboard and braille keys alike, and only the reader's, not keys NVDA sends itself, and it
   must be cheap enough to ask on every caret move. A key that passes through to the terminal
   (typing) ends the hold, and so should one NVDA handles as a command other than review and
   panning. Settle against NVDA's input code before phase 4, and check on hardware that output
   arriving between two keystrokes is still held.
6. **Should putting the review cursor back be an option?** Decision 8 undoes NVDA's "review
   cursor follows system caret" while the hold lasts, in a terminal only. That is the point of the
   hold, but it overrides a setting the reader chose. If a reader wants NVDA's behaviour, the
   answer is probably a setting for the hold as a whole rather than for this one part.
