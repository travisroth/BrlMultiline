# Spatial reading: presenting content across the rows

Read [architecture.md](architecture.md) first, and the "Document lines" section of it in
particular. This document proposes the next layer: showing content across the whole
display as one continuous piece, rather than showing the focused line and reading a fixed
offset into every other segment.

This is the design, the decisions taken so far, the order of work, and the questions
still open. Every milestone is now built and unit tested. Milestones 0 to 3 and 5 to 7 have
been run on hardware and corrected by what it found, and milestone 7's numbers are in: the
budget is settled and is no longer a factor. The milestone list says precisely what each of those means,
including what each hardware run got wrong — which has twice been a constant somebody
guessed, and twice been two different things sharing one flag.

## The problem

The add-on can already divide the display and fill the segments around the focus. What it
fills them with is one line each, at a fixed offset: the segment two above the focus
segment reads the line two above the caret. That was the right first step and it works,
but it is a grid of independent lines, and a document is not a grid of independent lines.

Browse mode on a Monarch shows what is missing. NVDA hands braille one reading unit — the
line or paragraph the browse mode cursor is in — and the fixed offset model then asks the
document for the units around it. Four things go wrong, and all four have the same cause:

1. A unit is not a row. A paragraph is one unit and may need five rows. A heading is one
   unit and needs one. Assigning one unit per segment either wastes the rows a paragraph
   needed or leaves the display mostly blank after a heading.
2. Content should keep going. After the heading's row, the display should carry on with
   whatever follows it in the document. Filling the rest of the display with blanks, or
   with a line counted from the caret rather than from the heading, both read wrongly.
3. Panning back does not undo panning forward. Pan through a paragraph, cross into the
   next element, then pan back: the paragraph is rendered again from its own start and the
   window lands on an aligned boundary somewhere in its middle. What the reader expects is
   the tail of the paragraph sitting at the bottom of the display, and to keep going back
   from there.
4. When the cursor moves onto a heading that is already on the display, nothing should
   move. The fixed offset model recomputes every segment from the new position, so the
   display jumps and the reader loses their place in a page they were reading straight
   through.

The goal behind all of it is spatial context: showing where a thing sits among the things
around it, which is what a single line has never been able to do.

## The reframing

Stop treating the display as several places to put a line. Treat it as a window onto one
stream of rows.

- A **block** is one piece of content: a reading unit in a document, a control with its
  label, one item in a list.
- A block is **rendered** into one or more **rows** of the window's width, together with
  the mapping from each cell back to the position in the block it came from.
- Blocks are packed in reading order into a **row stream**. A block always starts on a new
  row, and never starts a new window. That one rule produces the behaviour asked for in
  problems 1 and 2 without any per role special casing: a heading occupies its one row and
  the paragraph after it starts on the next row, whether or not the window boundary falls
  in between. ("Window" here means a display's worth of rows, not a piece of hardware. The
  hardware constraint is separate and is stated under the physical band below.)
- The window shows some number of consecutive rows of the stream, held as an **anchor** and
  an **entry direction**, which is what makes panning reversible.

This is a different presentation from the fixed offset one, not a replacement for it. On a
single row display split into columns, or on a layout whose free segments are not
contiguous rows, the fixed offset model is still the sensible one and stays.

## Decisions taken

These were settled before the work starts and are recorded here so they are not re-argued.

1. **In browse mode the flow is the focus segment itself.** The whole display, or the band
   given to it, is the document, and NVDA's own cursor sits on whichever row the browse
   mode cursor is in. A flow that is *not* the focus segment is a viewer: it shows no
   cursor and keeps a position of its own. There is never more than one cursor on the
   display. Components other than browse mode documents — lists, and things browse mode
   drops into focus mode for — may want a viewer band instead, which is open question 1.
2. **Panning the focus flow moves the browse mode cursor.** This is what NVDA already does
   with braille panning, and the reasons carry over unchanged: speech and braille stay in
   agreement, cursor routing keeps working so a link can be clicked or a field entered, and
   browse mode makes its best effort to move the application's own caret so that links and
   form fields remain interactive. This is the opposite of `objectMonitor`, deliberately:
   a pin is a viewer, and a viewer must not move anything.
3. **A block is a line or a paragraph according to NVDA's read-by-paragraph setting.** The
   user's existing choice for braille reading applies here too. Both must work, and which
   is better is a question about the display rather than about the document — see below.
4. **On entry, the display shows the caret's block with its context above it.** For a plain
   document that context is nothing, so the block sits at the top and the display fills
   downward. For a control it is the label or prompt — see the section on controls below.
5. **Panning moves a whole window; line scroll moves one block.** Scrolling forward and
   back is display sized, which is what those commands are for. NVDA's separate line
   commands move one reading unit, which is a block here.
6. **Blank blocks collapse when reading, and never when writing.** A run of blank blocks
   shows as one blank row while the reader is reading content, and as one row each while
   they are working inside it. The test is interaction, not role.
7. **A flow renders its own rows.** NVDA's buffer concatenates regions and cannot express
   "this block starts a new row", so the flow renders each block to rows itself and
   assembles them. See the next section, which is the largest correction to this plan and
   the reason for milestone 0.

### Why both reading units have to work

A line in browse mode is not the browser's rendered line. It is NVDA's own, cut at the
**Browse Mode Maximum number of characters on one line** setting, which defaults to 100 and
is meant to sit at or a little past the visual line so that speech follows what is on the
screen. That is why the dry runs showed lines of about ninety-five characters, and why the
number is the reader's to change rather than ours.

The consequence for a band is arithmetic. A block always starts on a new row, so a unit
that overshoots the band's width by a little wastes most of a row: a ninety-five character
line on an eighty cell band is a full row and a stub, and the band ends up little more than
half full. The same line on a Monarch's thirty-two cells is three nearly full rows, so the
waste is slight. Paragraphs pay that cost once per paragraph instead of once per line,
which is why they suit narrow rows.

Against that, a paragraph on a single row display, or in a single row segment, drifts out
of step with what speech is saying, which is confusing in a way that a line never is. So
neither unit is the right default for every arrangement, the setting stays the reader's,
and the dry run reports how much of the band each one actually fills.

## What NVDA's buffer does not give us

The first draft of this plan assumed a flow could be a segment holding a list of ordinary
NVDA regions, one per block, with the flow choosing which were present. Reading
`braille/buffers.py` and `braille/regions/textInfo.py` says otherwise, in four separate
ways. All four were confirmed against NVDA master.

1. **Regions concatenate; they do not stack.** `BrailleBuffer.update` builds one flat cell
   list — `self.brailleCells.extend(cells)` for each visible region — and rows are then cut
   out of that flat buffer by `_calculateWindowRowBufferOffsets`. A ten cell heading
   followed by a paragraph puts the paragraph at cell eleven of the same row, which is
   precisely the thing this design exists to prevent. Padding each region out to a multiple
   of the width does not fix it either, because word boundary wrapping means rendered rows
   are not all the same length.
2. **`hidePreviousRegions` can blank everything above.** `TextInfoRegion.update` sets it
   whenever its reading position is not at the start of the object, and
   `BrailleBuffer._get_visibleRegions` yields *only* the last region when the last region
   has it set. A flow whose bottom block is anywhere but the document's first line would
   therefore show that block and nothing else.
3. **The cursor belongs to whichever region reported one last.** `update` assigns
   `self.cursorPos` from every region that has a `brailleCursorPos`, so the last one wins.
   Every block holding a collapsed position looks like a cursor, so the cursor would land
   in the bottom block rather than in the reader's actual block.
4. **`regions[-1]` is load bearing in more places than the line commands.** The earlier
   draft caught `script_braille_nextLine`. It is also used for focus placement in
   `_doNewObject`, caret updates, braille input, "braille to focus", and the scroll
   fallbacks in `scrollForward` and `scrollBack`. On a single line display the last region
   is the only thing on show and is therefore the cursor's block; in a flow it is the block
   at the bottom of the window, which usually is not.

Consequently the flow owns rendering:

- **`RenderedBlock`** — a neutral snapshot of one block: its rows of cells, the raw text,
  and the mapping from each cell back to a position within the block. It is data, not a
  region, so `flow.py` can consume it with no NVDA import and be unit tested like
  `layout.py`.
- **`FlowRenderer`** — the NVDA facing half. It takes a block's region, renders it at the
  band's width under the current translation settings, and produces a `RenderedBlock`.
  Translation, contraction, wrapping and continuation marks stay NVDA's, and none of that
  logic is copied.
- **`FlowBuffer`** — assembles the window's rows from `RenderedBlock`s, exposes exactly one
  active block as the cursor's, and answers routing by mapping a pressed cell through the
  active row's mapping back into the right block. Every `regions[-1]` dependency listed
  above is audited against it, and the ones that must reach the reader's block are routed
  to the active block instead.
- **Blocks are position bound.** A `FlowTextInfoRegion` answers `_getSelection` from the
  `TextInfo` or bookmark assigned to it, not from the object's live selection. Without this
  every `CursorManagerRegion` over one document returns the same `obj.selection` and every
  block renders the caret's block. Only the active block may expose a braille cursor;
  the others suppress theirs.

The live and viewer distinction survives this, but it is about *writing*, not about which
class is used: a live block writes a new position back to the real cursor when panning or
routing moves it, and a viewer block keeps the move to itself.

## The window is an anchor, not a page number

The stream cannot be paginated up front. Its length is unknown, re-rendering can change a
block's row count, and the reader can enter it from either end. So the window is held as
an anchor plus a direction of entry:

- Anchor: a block identity and a row index within that block.
- Entry: whether the anchor names the **top** row of the window or the **bottom** row.

Panning forward sets the top anchor to the row after the current bottom row. Panning back
sets the bottom anchor to the row before the current top row. That symmetry is the whole
of problem 3. When a block has to be re-rendered and its row count changes, the window is
rebuilt from the surviving anchor and the entry it was given, so the edge the reader last
moved toward is the edge that stays put.

Forward and back are exact inverses only while the source generation and the render key
below are unchanged. A cursor move can run page script, and live content can rewrite itself
underneath the reader; when that happens the window is reconciled by the recovery rules
rather than pretending the stream is the same one.

The consequence is that windows are path dependent: the same block can appear at different
row positions depending on how it was arrived at. That is deliberate. The alternative — a
fixed page grid anchored to the start of the document — is stable but reintroduces problem
3 exactly, and it also requires knowing where the document starts, which we do not.

There is already a small version of this in the code, and it is the precedent to
generalise: `DisplayContainer._panPinnedContent` places the window at the end of the line
it has just moved back onto, "so that a document reads the same in both directions rather
than skipping every line's tail". The flow window is that rule, applied to a run of blocks
instead of to one region.

### Identity, and what happens when the anchor goes

The anchor is only as good as the identity it names, so both are defined explicitly.

- **`BlockId`** is the source generation, a bookmark for the block's start, and the reading
  unit it was fetched with. Two blocks are the same block when all three match.
- **The render key** is everything that changes cells without changing content: band width,
  output table, wrapping policy, continuation marks, formatting configuration, cursor
  expansion, and the configuration profile generation. A changed render key invalidates
  every `RenderedBlock` while leaving block identity intact, so the anchor survives a
  translation table change.

Recovery rules, applied in order:

1. If the anchored block is gone, take its surviving successor when the anchor was a top
   anchor, and its predecessor when it was a bottom anchor, so the window keeps growing in
   the direction the reader was going.
2. If the block survives but has fewer rows than the anchored index, clamp toward the
   anchored edge.
3. If a bookmark cannot be resolved at all after a document change, re-enter deliberately
   at the live cursor by the entry rule in decision 4, rather than silently choosing some
   cached block.
4. A collapsed run of blank blocks keeps the identity of its first member, which is the one
   that owns the visible row.

## Rules for filling the display

1. Pack blocks continuously. A block starts on a new row; it does not start a new window.
2. A block longer than the window fills it and pans within itself. Entering such a block
   from below shows its last rows.
3. Blank rows at the bottom mean the stream genuinely ended. They never mean the add-on
   gave up fetching — see the source result states below.
4. An empty block renders as one blank row, and a run of them may collapse to a single
   blank row, by the rule below.
5. A block may ask for a blank row before or after it, which is how a form field is
   separated from what follows. This is explicit spacing carried on the block, not a
   by-product of packing, which inserts nothing.
6. The stream is only ever materialised for the rows the window can show plus a small
   margin either side. Nothing renders a whole document.

   The budget that bounds this is sized to the band, and that is not a detail. Filling eight
   rows costs at least eight blocks, more where blank lines are collapsed or a prompt is
   read above a control, so a budget near the band's own height is one the ordinary case
   trips over — and tripping over it looks like a display of markers saying there is more we
   have not read. A budget the ordinary case meets is not a safety limit, it is a fault. It
   is twice the band plus a margin, and reaching it means something is wrong with the page
   rather than tall about the display.
7. Routing into padding — the blank cells after a block's last row, or a deliberately blank
   row — does nothing. NVDA's usual behaviour of mapping trailing blank columns to the last
   content cell must not be inherited here, or a press in the gap after a short field would
   activate the end of that field.

## Two movement commands, and what each means in a flow

NVDA has both, and they should stay distinct rather than collapsing into one another on a
multi row display.

**Scroll forward and scroll back** move a whole window. That is what the commands are for
and what the anchor rules above describe: a display's worth at a time, with the entry
direction making the two inverses of each other.

**Next line and previous line** move one block. In NVDA these call `nextLine` and
`previousLine` on the region, which move by the reading unit — the same unit an arrow key
moves by, not one row of the display — and take the cursor with them. A flow keeps that
meaning: one block forward or back, cursor included, which then goes through the following
rule below. On a multi row display this produces something the single line display cannot:
if the next block is already on the display, the window does not move at all and only the
cursor advances. The reader steps through the content they can already feel.

Both commands, and every other caller listed in the `regions[-1]` audit, act on the active
block rather than on the last one rendered.

## Where the cursor goes when the display is panned

Decision 2 says panning moves the browse mode cursor, and on a multi row display one pan
can cross several blocks, so the cursor's destination has to be stated rather than
inherited from NVDA's one-line behaviour.

The rule: after a pan in either direction, the cursor goes to the **topmost block in the
new window**. Reading onward with the arrow keys then continues from the top of what is
under the reader's hands, in both directions, and what speech says matches where braille
starts. Everything below the top block is on the display without having been spoken, which
is the point of the extra rows.

This holds wherever the reading position is the reader's to move. It does not hold for a
run of objects, where the reading position is a *selection*: see the object source below.
There the window moves and the reader's place does not, and the display shows no cursor
until they pan back to it. Making the new top block active there claimed a move the reader
had not made — and since the focus had of course not moved, the next refresh followed it
and dragged the display back. On hardware that read as "the second pan bounces back to the
top", and the second pan was simply the first one that had to fetch.

Two consequences worth stating, both to be checked on hardware:

- Panning forward across four short blocks speaks the first of them, not the last. That is
  intended. The single line behaviour of speaking whatever you land on cannot be preserved
  when you land on eight rows at once.
- Because moving the cursor makes NVDA rebuild, the rebuild must not be allowed to
  reposition the window. The anchor and entry direction are set by the pan, and the rebuild
  re-renders into them. This is the one place where the ordering of our work against NVDA's
  is delicate, and it is what milestone 3 exists to prove.

## Following the cursor

When the cursor moves for a reason other than panning — an arrow key, a quick navigation
key, a focus change — ask first whether its block is inside the current window. If it is,
do nothing at all: the rows the reader has under their fingers do not move, and the cursor
simply appears on the row where that block already sits. This is problem 4, and it is
answered by asking the question in the right order rather than by a rule about headings.

If the cursor's block is outside the window, scroll by the smallest amount that brings it
in, in the direction of travel: moving forward, its block sits at the bottom of the window
with what preceded it above; moving back, it sits at the top. A jump — a move of more than
the cache can reach — abandons the window and rebuilds it by the entry rule in decision 4.

**A focus change is not by itself a jump.** An earlier draft said it was, and the hardware
disagreed: Tab moves within a page the reader has their hands on, and rebuilding the window
around each target moved the display to something they could already feel. Three questions
have to be kept apart, and collapsing any two of them has now caused a fault:

- *Where they arrived.* Any focus target the document can place — a link as much as an edit
  field — is read at its own place rather than at the cursor, because a focus event can
  arrive before the browse mode cursor has caught up. Reading at the cursor then showed the
  line they were on before they pressed Tab, which on hardware looked like a blank row.
- *What kind of thing it is.* Only a form control brings the prompt and spacing rules
  below. Deciding this from *whether an object had been passed at all* made a link into a
  control the moment links began to be passed.
- *Why they moved.* Only a jump by structure grounds. Tab and the arrow keys sync.

Within the cursor's own block, the cursor's row must stay visible. That is what makes a
long paragraph, or a multi line edit field being typed into, behave as described below.

### A block starts where its reading unit starts, and one block is read from the cursor

Two rules, and the second is an exception to everything else this design says about pinned
positions. Both were wrong on hardware, and together they were "the cursor sits on the first
cell of an edit field and stays there while you type".

**A multi line edit being written in is its own document.** Everything else here is about
a document and a block of it; a textarea is both at once, and the page cannot present it. A
virtual buffer holds the field's text as its own buffer lines *and* answers "where is this
field" with one of them, so a flow that placed the field among those lines showed the
reader's line twice — once as the field, once as the page — and the buffer's caret does not
move within the field at all, so neither copy followed what they typed. While browse mode
has stood aside for a multi line edit, therefore, the edit is the document: its own lines
are the blocks and its own caret is the reading position.

A single line field is not, and the difference is the point. It *is* one line of the page,
the page has its label and what follows it, and the only thing it needs from the control is
the live caret — which is what the interactive region above gives it.

Both toggles happen without a focus change: Escape gives browse mode back with the focus
still on the field, and Enter takes it away again. So the band re-asks what it should be
reading before each redraw, and acts only on that one disagreement — a page and something
inside that page — because a focus that has gone somewhere else entirely is a focus change,
and NVDA reports those through regions that are a better account of where it went than the
focus object is.

**What NVDA queues for the next event is what the buffer is holding now.**
`BrailleHandler.handleCaretMove` takes `mainBuffer.regions[-1]` and marks *that* region
pending. A flow band puts the active block's region there — see the last-region audit — and
it has to put the current one there, after following as well as before.

Left holding the block the reader had just left, the next caret move queued a region the
band was no longer showing; the update that followed found nothing dirty and did nothing;
and the update after that worked again. The band answered one keypress in two. Under the
fingers that is a cursor which moves a press late and a letter which appears only once the
next one has been typed, and both were reported from a comment box before the cause was
visible.

It only began to bite when writing started re-reading the whole band, because that replaces
the active region on every keystroke. Before it, the same region object usually stayed
active and the staleness could not be seen. A rule about ordering that is only sometimes
observable is worth stating rather than leaving to be rediscovered: **sync the last region
after every change to which block is active, not only before reading it.**

**A block's identity rests on `TextInfo.bookmark`, and a document that will not give one
needs an answer.** Nearly every document gives one, including those that hand back a copy of
the position itself — those compare by where they are. A document whose `bookmark` raises
leaves only the position object, which has no equality of its own, so every reading of one
line would be a different block and nothing would ever be recognised. There, two positions
are the same block when their reading units start in the same place: `compareEndPoints`,
wrapped in something that answers `__eq__` with it. See `flowSources.PositionMark`.

**A block walked forward starts after the one it was walked from.** The invariant the whole
stream rests on, and it went unstated for six milestones because every document tested had
honoured it. A rich editor does not: at a position just past a break, asked to expand the
reading unit, it reaches back across the break — so the block found starts where the block
already on the display starts, and the display shows the same text twice.

That is the whole of the duplicate row, in both units. It also explains why stepping over
the break made paragraph reading worse rather than better: the blank row that went was
replaced by a copy of the line above it, because the position past the break normalised
backwards onto the previous paragraph.

A step that does not advance is refused and the stream ends there. Blank rows are the honest
answer — the reader is at the end of what can be read reliably — and the next keystroke reads
the document afresh, which the logs show it doing every time.

**Where a reading unit is not one, the band cannot be either.** A block is a reading unit,
and the whole packing rule — a block starts a new row — assumes a unit read by line holds one
line. Chromium's rich editor does not always answer that way: at the position left by a
return at the end of the text, expanding the line gave everything from the start of the
document up to the caret. Two returns in and the band showed lines one and two on a single
row, and line two again below it as a block of its own, because the next block was walked
from the over-reaching one.

The probe settled it: NVDA's own region shows the same merged text for the same object at
the same moment, so the flow is faithful and the fix cannot be in the packing. What is ours
is the *repeat* below it — a band that walks on from an over-reaching block fetches the line
that block already contains. So a block read by line whose text holds a line break has
nothing after it to fetch. The rows below go blank rather than showing it twice, and the
reader's next keystroke reads the document afresh, which the log shows it doing.

Only for lines. A paragraph may hold soft breaks and asking the same of one would end every
reading at the first of them.

The same run measured the alternative, and it is the better one. Read by paragraph, this
control answers cleanly — every unit is one line, with no terminator and no merging, at every
step, including the ones where asking for the line returns a single character. Its only
fault was the *walk*, which landed on the break between two paragraphs so that an empty unit
appeared between each pair and one return read as two rows. A paragraph break is the row
boundary; giving it a row as well says the reader pressed return twice. Exactly one empty
unit is stepped over, which keeps the distinction: one return leaves one and it goes, two
leave two and the second is a paragraph they meant.

So **a multi line edit being written in is read by paragraph**, whatever the reader's own
setting says, and a page is read their way. A paragraph is what a writer typed; a line is
what the control's wrapping made of it. The evidence is stronger than the argument — a unit
that is only sometimes a unit cannot be a block — but the argument stands on its own.

**One unit, told to everything.** The hardware run after the setting shipped found the flow
split in two: the source identified and walked blocks by its chosen unit while every region
still rendered by NVDA's own read-by-paragraph setting — `TextInfoRegion._getReadingUnit`
reads the configuration, and nothing had told it otherwise. A block identified as a
paragraph but rendered as a line showed only the line at the paragraph's start; the wrapped
rest of it was silently gone; and the caret on a wrapped line was ruled outside its own
block, because the containment test expanded by the wrong unit too. In the very editor the
paragraph choice exists for, the line at a paragraph start is transiently everything before
it, so paragraph mode reproduced the merged rows it was chosen to avoid. Now the source
stamps its unit on every region it builds, and `FlowRegion._getReadingUnit` answers with it.

**A swallowed mark is evidence with a shelf life.** The same run showed an edit reading as
its first line and nothing else, surviving focus changes. The mark that ends the walk after
a block which swallowed its successor was kept by bookmark and never taken back, so the
truncation outlived the transient that justified it. Two repairs: a block whose re-read
comes back clean clears its own mark, and the re-read while writing calls `forget()` first —
everything the source remembers about a document being typed into is an answer the edit may
just have changed, the cached exits and positions included, so none of it is kept.

**A walk lands where its unit starts, or it is not believed — in either direction.** The
run after the unit fix found the third line of an edit breaking the band in two different
ways: one editor showed the reader's first lines merged above the caret, another pinned the
line being typed to the top row with everything above it gone. Both were the restore that
follows every keystroke — put the band back by the block that was on its top row — walking
*backward* over a freshly made boundary, where the transient expansion reaches back.
`_advanced` cannot catch that direction: the reach-back does land earlier, and earlier is
what walking back means. So while writing, a walked-to position whose unit does not start
where the walk landed is refused, both ways. And the pinning was sticky by construction —
the next re-read found the caret's block on the top row and kept it, so one refusal lasted
until the focus changed. The restore now falls back to reaching back for up to half a band
of context above the caret, asked afresh on every re-read, so the band heals the moment the
editor answers again.

**The top row is only believed when it is not the caret's own block.** The probe over a
plain textarea caught the mechanism behind the third-and-fourth-line failures: the moment
return is pressed, the editor answers the paragraph at the caret as everything from an
earlier point — sometimes the start of the document, sometimes partway — so the block built
at the caret is merged and takes the top row under an identity that was never really a top.
A restore that trusted the top row then anchored the band there on every later keystroke,
and the reader's first lines scrolled off an eight row band with four lines in it. The
controller now keeps the last top block that was *not* the caret's own, and restores to
that whenever the top row is suspect; the claim survives a failed restore, because a
restore refused this moment may succeed on the next keystroke.

Confirmed on hardware: the reader's first lines now stay put while further lines are
typed under them.

**A keystroke into an edit earns a second look.** Every transient state the probe caught
heals on the next re-read — which used to arrive only with the next keystroke. A reader who
pauses right after pressing return is reading the display, and that was exactly the moment
the merged block and its duplicate sat under their fingers. The band now schedules a settle
pass 150 ms after each writing re-read, restarted per keystroke so a steady typist pays for
one pass per pause; it redraws only when the second reading differs from what is shown, so
a settle that changes nothing ends the exchange rather than perpetuating it.

Confirmed on hardware, together with the stable-top rule above: a glitch after a return
corrects itself within a beat rather than waiting for the next keystroke.

**An editor is what NVDA says it is, not what its roles say.** Windows 11 Notepad's editor
answers UIA with role DOCUMENT and neither an EDITABLE nor a MULTILINE state, so every
role-and-state test refused it and the flow read classic. NVDA itself types into it,
because NVDA recorded its judgement in the class it built — the `editableText.EditableText`
behaviour. `flowForms.isEditableObject` now accepts that authority, and an editable
DOCUMENT is taken as multi line without the state: a document is lines by nature, and a
single line field never calls itself one.

Confirmed on hardware: Notepad and the Outlook classic message pane are both recognised
and both read as their own lines.

It is a setting rather than a rule, `flowWriteByParagraph`, on by default. The evidence
above comes from one rich editor, and the whole point of having it is to find out whether
it holds in a plain edit, in a code editor, in a terminal-like control. Turning it off puts
an edit back on the same footing as a page, which is the comparison to make; leaving it on
is the reading this run showed to be sound. A setting whose default was chosen from one
control should be a setting.

**While the reader is writing, a caret update reads the whole band again.** Every other
rule here assumes the document holds still while it is read, and an edit being typed into
does not. A block's position is an offset: insert a line and every offset after it moves,
delete a selection and most of what the band was holding does not exist any more.
Re-rendering only the block the caret is in leaves the rest reading at offsets that have
gone — on hardware, a line copied onto the row above it, and lines removed by select-all and
overtype still under the reader's fingers until the focus changed and forced a rebuild.

So the band is re-read from the caret and then put back where the reader had it, by the
block that was on its top row. Typing must not scroll the display: what was above them stays
above them, and adding a line moves the caret down a row rather than throwing the rest of
the band upward. A top block the edit removed cannot be gone back to, and there the caret's
own block is the anchor — which is what select-all and overtype leaves.

It costs a band's worth of reads on every caret update, which is what the budget bounds and
what the cost command measures. Nothing cheaper is honest: there is no way to know which of
the other rows survived without reading them.

**A block's position is the start of its reading unit**, not the offset the cursor happened
to be at when the block was built. NVDA's bookmark for a virtual buffer is a pair of
offsets rather than a line number, so a block built at the caret had a different identity
for every character the reader passed: the flow could not recognise the block it was already
showing, and answered by rebuilding the window around the cursor. Within a line that is
invisible; in a field being typed into it is every keystroke, and it takes the label above
the field with it.

**The active block of a live flow is read from the live cursor**, while the cursor is still
inside it. Every other block must be read from its own position — a stock region over a
document answers from the live cursor, so a run of them would all render the cursor's block
rather than the blocks around it, which is why the pinning exists at all. But the active
block *is* the cursor's block, so for it the live cursor is not a hazard but the answer:
`TextInfoRegion.update` asks once and lays the whole block out around what it is given, so a
region answering with its own start shows a cursor at its own start and nowhere else. The
containment test is what keeps the exception narrow: a cursor that has left belongs to
another block, and rendering that block's line under this block's identity would put content
where it does not belong until the flow noticed.

## Controls, labels and edit fields

A form is where spatial context pays best. Most of it follows from the packing rules, but
two parts genuinely need more, and the first draft of this plan was wrong to claim
otherwise.

- **A control's prompt is context, and context goes above it.** A radio button, a combo
  box or an edit field is presented with its label, which is one or two rows at the top of
  the window with the control below it. NVDA already assembles name, role, value and state
  into regions for the focused object; the flow's job is to place them above the block
  holding the editable text, not to re-derive them.
- **A short answer does not deserve the whole display.** A single line edit holding "Ada"
  is one block of one row, and what follows it in the form appears below with a blank row
  between. The blank row is rule 5 above — spacing declared by the block — not something
  packing produces on its own.
- **A multi line edit grows into the space and pushes the rest off.** It starts as a small
  block with whatever follows it visible below. As the reader types, the block grows, the
  cursor's row must stay visible, and the bottom entry rule keeps the growing end at the
  bottom of the window — so the content that followed the field flows off the display and
  what has been written becomes the display. This part *is* the existing rules.

The harder half is reach. In browse mode the whole form is in one virtual buffer, so the
next prompt is simply the next block and the document source finds it. Once browse mode
drops into focus mode, the control's `TextInfo` usually cannot reach the next object at
all, and getting there means object traversal or an adapter. So the two cases are separated:
browse mode forms are milestone 5, and focused control presentation waits for
`ObjectFlowSource` and the adapter protocol in milestone 6.

## Grounding a jump

Following the cursor and grounding on it are two different answers to a cursor move, and
which one is right depends on why the cursor moved.

Arrowing, tabbing and shift-tabbing are the reader working through what is in front of
them, and there the rules above hold: a block already on the display moves nothing, and a
block past the edge scrolls by the smallest amount that brings it back. A quick navigation
key is not that. Pressing `h` skips a section the reader has decided they are done with,
and their old window nudged along says nothing about where they have arrived. So the
target goes to the top of the band and the document runs on from it, which is a place to
start reading.

Not every quick navigation key means a jump, which is why this is a policy rather than a
rule. `l` moves to the next list, a change of section; `i` moves to the next list item, a
step within one that reads like arrowing. The difference cannot be inferred, so it is
stated in `flowQuickNav.GROUNDING_TYPES`: headings at every level, tables, lists,
landmarks, frames, articles, groupings, block quotes, separators, figures and non-link
blocks ground; list items, links, form fields, buttons and the paragraph moves do not.

NVDA makes this cheap to hear, through two seams rather than one. Every quick navigation
script — around forty of them, generated at import time — funnels through
`BrowseModeTreeInterceptor._quickNavScript`, which carries the item type; and a search that
actually found something goes on to call `TextInfoQuickNavItem.moveTo`. Hearing only the
first is not enough, because a search that finds nothing returns before moving anything, and
the note it left would then ground the reader's next ordinary arrow key. So the item type is
remembered while the script runs and the note is made at the move.

Only browsers were ever going to be exact here, and that is enough: a virtual buffer's items
are `TextInfoQuickNavItem`s, as are Word's. Excel's are not, so a quick navigation key there
does not ground — the window tracks the cursor as it always has, which is a degradation and
not a fault.

Both seams are patched only while a flow is on the display and given back when the flow
stops, by identity, so that a patch someone else installed on top is never taken for ours. A
note of a grounding move is consumed by the caret move it causes, and expires after a second,
so that a stale keypress cannot jump the display under an ordinary arrow key.

The setting `flowGroundOnQuickNav` turns the whole behaviour off, per display, and
defaults to on. Which moves count as structural is deliberately not a setting: it is a
judgement about what reading is like, and a reader who disagrees with one entry is better
served by the whole thing being off than by a list of forty checkboxes. That can be
revisited once there is any evidence of anyone wanting it.

## Blank lines: collapsed when reading, kept when writing

Whether a run of blank lines is noise or content depends on what the reader is doing with
it, and not on what kind of object it is. The same textarea is both, at different moments.

- **Reading.** In browse mode, several blank lines in a row are layout, not meaning, and
  spending four of eight rows on them wastes the display. A run collapses to one blank row.
  This holds for everything browse mode renders, including a textarea or a rich editor
  sitting in the page that the reader has not entered — browse mode is presenting it as
  part of the document, so it reads as part of the document.
- **Writing.** Once the reader is inside a multi line edit, a textarea or a rich editor,
  the blank lines *are* the document: they are paragraph breaks the reader put there and
  will navigate by. Nothing collapses, and every blank line is its own row.

So the test is interaction — browse mode has gone to focus mode or pass through for this
control, or the caret is in an editable control the reader is working in — and not the
role of the object. Two consequences:

1. Collapsing belongs to the source, which is the only layer that knows whose position it
   is reading and in what mode. The flow packs whatever blocks it is given.
2. The answer can change while content is on the display, when the reader presses enter to
   enter a field or escape to leave it. Row counts then change under the window, which is
   the case the anchor was designed for.

## Reading beyond the focus without making NVDA slow

The concern is real, and it is worth being precise about which calls are expensive, because
the answer is not the same for all of them.

- Moving a `TextInfo` inside a browse mode document is cheap. The virtual buffer's text is
  already in NVDA's process; `move`, `expand` and `text` on a copy do not cross to the
  application. This is the same primitive `TextInfoPositionRegion` already uses to read
  the line at an offset, and it is what the document flow source will use.
- Walking `NVDAObject` relations — `next`, `previous`, `parent`, `firstChild` — is the
  expensive one, because each is a cross process accessibility call, and lists and trees
  are where we would want to do it. This is where a budget is not optional.
- Decision 3 means lines rather than paragraphs will often be the unit, so there will be
  more fetches per display than a paragraph based design would need. The budget matters
  more for that reason, not less.

The rules that keep it bounded:

1. Fetch on demand only. A pan or a cursor move that exposes rows the cache does not have
   fetches one block at a time until the window is full, and stops.
2. Keep a margin of one window's worth of rows either side, no more. On a Monarch that is
   at most sixteen rows outside the window, which is a handful of blocks. Everything beyond
   that is dropped.
3. Cap the work in one operation: a fixed maximum number of blocks fetched per pan, and a
   time budget checked between fetches. A budget between blocks cannot make a single slow
   `update` fast, so per block latency is recorded from milestone 2 even though tuning
   waits until the end.
4. Never refresh the whole stream on a timer. The block containing the cursor refreshes on
   the `_handlePendingUpdate` path the add-on already patches. Other visible blocks
   refresh only when the source says the content changed.
5. Invalidate on identity, not on time: a different tree interceptor, a different root
   object, or a document change event bumps the source generation and drops the cache.
6. Instrument it. Debug level timing around fetch and render, so that a slow site can be
   identified from a log rather than from an impression.

### A source result is not just a block

Running out of budget and running out of document must not look the same on the display,
or the reader is told spatially that a page ended when it merely took too long. A source
therefore answers with one of four things:

- **block** — here it is.
- **endOfStream** — there is genuinely no more in this direction. Blank rows are correct.
- **deferred** — the budget stopped us, with a resume token so the next pan carries on. The
  window keeps what it had and the unfilled rows carry a recognisable status marker rather
  than reading as emptiness.
- **error** — the source failed. Logged, shown as a status marker, and not retried in a
  loop.

## Sources, in two flavours

A **flow source** answers three questions and nothing else: give me the block at the
cursor; give me the block after this one; give me the block before this one. Each answer is
a result as above, carrying a region bound to its own position and a `BlockId`.

The flavours differ in whether a move is written back:

- **Live**, for a flow that is the focus segment. Panning and routing write the new position
  to the object's or the tree interceptor's own cursor, so browse mode follows and routing
  activates what it lands on. Only the active block exposes a cursor.
- **Viewer**, for a flow that is not the focus segment. It keeps a position of its own,
  moves nothing outside the band, and shows no cursor.

### Viewer routing has to be chosen, not inherited

`PinnedRegion` replaces cursor *writing* only. It inherits `_routeToTextInfo`, which
activates the position when a routing key is pressed where the region already thinks its
cursor is — and since a pinned region's private position is collapsed, it does have a
cursor position even though the container shows no cursor for that segment. So routing
twice in a pinned browse mode document can call `activate()` on the pinned position and
follow a link in a document the reader is only watching. That is current behaviour in the
shipped pinning code, not only a hazard for flows, and it is worth testing on hardware
independently of this work.

The policy for viewers, to be implemented explicitly rather than inherited: a routing press
moves the viewer's own reading position and never activates. Promoting a viewer to the
focus flow, if that is ever wanted, is a separate deliberate command.

### A run of objects is a third flavour, and the difference is what the cursor *is*

A list, a menu, a tree, the choices of an open combo box: one object is one block, and its
braille is NVDA's own `NVDAObjectRegion`. Two rules follow from what an object is, and
neither is a matter of taste.

**The reading position is a selection, so the flow never moves it.** Panning moves the
window and nothing else; the focused item keeps the cursor, so the reader can feel which of
the eight the arrow keys will act on; and only a focus event may move that.

A routing key is the braille equivalent of clicking on what is under your finger, so what it
does is what a mouse click does to that kind of control. Clicking a menu item invokes it and
clicking a tab chooses it, so those act — the focus is taken first and the action follows,
which is NVDA's own `ReviewNVDAObjectRegion`. Clicking a list item or a tree item selects it
and waits, so those only take the focus. Which way round is the adapter's to say.

**Walking the siblings finds more than the run.** NVDA's own navigation of a list walks the
items the reader can arrive at; this walks the accessibility siblings, which also holds the
button under a list box and the separators in a menu. So every step is classified before it
is read:

- *Content*, admitted by the adapter: same parent, and the same kind of role — with a menu's
  check items, radio items and plain commands counted as one kind, since they are one menu.
- *Decoration*: a separator, drawn as a line of dots 7 and 8 across the band and walked
  through, because the groups either side of a line are one menu. Handed to
  `NVDAObjectRegion` it read on hardware as "unavailable" and its dashes, which is true and
  useless. Disabled is never the test on its own: a command that is temporarily unavailable
  is content, and saying so is the whole point of the word. The role is the test, with a
  narrow fallback for a toolkit that exposes its line as an unfocusable, actionless menu
  item named with nothing but dashes. It was a blank row first, and the reader asked for the
  line: braille readers like decoration too, and the grouping is worth feeling rather than
  merely not being lied to about. Nothing routes into it — there is nothing there to go to.
- *Beside the run*: anything else ends it.

**An application may declare its own run, without importing anything.** The sibling walk and
the visible-order tree walk are guesses about a kind of control, and some controls are not
shaped like any guess. Microsoft Teams' chat history is the worked case: its messages report
the role GROUPING, each sits in a wrapper of its own so that no two messages are siblings,
and beside each is a timestamp and an unnamed element — so `next` is None, `previous` is the
timestamp, and NVDA's own `simpleNext` leaves the history altogether and lands on the compose
box. Only the app module knows how to step it.

So the adapter protocol is looked up on the object before the registry, which is what this
plan said it should be from the start. An app module sets attributes on its overlay class:
`brlMultilineFlowRun = True` to declare the run, and optionally `brlMultilineFlowNext`,
`brlMultilineFlowPrevious` and `brlMultilineFlowAdmits` to supply the walk and the membership
test. Membership defaults to "anything else bearing the declaration" rather than to a shared
parent, because in the case it exists for no two members share one.

Attributes rather than a `register` call, and the difference is not style. Registration means
an app module importing a global plugin from another add-on: a load-order dependency, and a
hard failure when the add-on is absent, disabled, or a version that has never heard of the
call. A declaration is inert data that only this add-on reads, so the app module behaves
identically whether or not BrlMultiline is installed. `register` stays for code that wants to
supply a whole adapter for controls it does not own.

An application that declares a run and supplies no walk is saying its members are siblings.
If they are not, the step lands outside the run and the admission test above ends the reading
there — a short run, never wrong content. A walk that raises ends the run for the same
reason: falling back to siblings would step somewhere the application has already said its
run is not joined together.

The same admission test answers the band's question on every focus change — is this still
the run we are showing? — so arrowing through a list keeps everything already walked. What
the run was found from is fixed for the life of the source and is never replaced by where
the reader has since moved: replacing it turned an open combo box into one of its own
choices, whose children are nothing, and the display lost the list.

## Where `getBrailleRegions` fits

NVDA's own `getBrailleRegions` hook is a different thing from what this needs, and both
ends of it matter:

- **Consuming it.** `getFocusRegions` lets an object override how it is presented, and
  MathCAT is the one in-tree user. Where an object provides it, the flow source should take
  what it returns as one block rather than reading text underneath it, so that an object
  which has already decided how it wants to be brailled keeps that decision.
- **Not extending it.** It returns a flat list of regions with no notion of rows, of order
  outside the focus, or of which segment content belongs in, so it cannot express "this
  object wants four rows laid out this way". The add-on's own hook should be separate and
  additive: an adapter protocol, looked up on the object first and then in a registry keyed
  by role and application, returning either a block list or a panel specification.

## Fitting the existing architecture

### The band is a rectangle on one display

A flow band is a `SegmentRect` inside a single physical display's live band, not a range of
rows. `validateAgainstHardware` already refuses a segment that reaches dead columns or
straddles two displays, and a full width band across the Monarch's rows in a Monarch and
Focus composite would do exactly the first of those: the Monarch's rows are 32 cells of an
80 cell composite, so 48 dead columns lie inside any full width band. Configuration
therefore names a display and a row range within it, and the first version keeps one flow
on one display, which is what the Monarch case wants anyway. A flow spanning two displays
would need several segments coordinated by one controller with a render cache per width,
and is not attempted.

### Ownership, and what the claim contract still owes

`architecture.md` already says the claim contract cannot yet host owner content and NVDA
focus content in the same reserved segment: "Reserved does not mean exclusive... Those two
producers will fight", and the fix it predicts is splitting `owner` into reserved,
`hostsSystemFocus` and exclusive. The focus flow is the real consumer that was waiting for,
and it needs three things before milestone 3:

1. The flow controller is *the* producer of content for its band. Ordinary untargeted focus
   regions are intercepted and handed to the controller rather than written into the segment
   alongside it.
2. `SegmentSpec` gains the explicit concepts — reserved, `hostsSystemFocus`, exclusive — so
   that "this segment holds the focus and one owner draws it" is expressible rather than
   implied.
3. The controller gets a lifecycle: `onRebuilt` to redraw after geometry is recomposed,
   `onEvicted` when its claim no longer fits, and a defined termination. Geometry survives
   a rebuild today and content does not, which for a pin is recoverable and for a flow is
   a blank band.

### Modules

- `flow.py` — the row stream, the anchor arithmetic, recovery and the tracking rules, over
  `RenderedBlock` data. No NVDA import, unit tested like `layout.py`.
- `flowRender.py` — `FlowRenderer` and `FlowBuffer`: block to rows, row assembly, active
  block and cursor ownership, routing and the `regions[-1]` audit.
- `flowSources.py` — the sources, the result states, block identity and collapsing.
- `flowControl.py` — `FlowController`: driving a window over a source, arriving, filling,
  panning, following the cursor, and re-reading the band while the reader writes.
- `flowBuild.py` — turning "the reader is on this object" into a flow: what to read, the
  reading unit, the region classes, and `buildController`, which assembles all of it. Used
  by the live band and by the dry run alike, so the diagnostic reports the construction the
  reader actually gets rather than a second one that could drift from it.
- `flowBand.py` — the claim on the display: which content flows, what the band reads now,
  and the settle pass after a keystroke.
- `flowSegment.py` — the segment a flow is drawn into, and where NVDA's commands reach it.
- `flowForms.py` — what counts as a control, an editor, a multi line editor.
- `flowObjects.py` — reading a run of objects: a list, a menu, a combo box's choices.
- `flowQuickNav.py` — noticing a browse mode jump, so a structural one can ground the band.
- `flowDryRun.py` — the diagnostics only: `dryRun`, `report`, `describeCost`. It built the
  live flow's front end until that was split into `flowBuild`, which left the live path
  living in a file named for a diagnostic.
- `FlowPanel` in `panels.py` — geometry and ownership only.
- `documentLines.py` — unchanged, and still what fills free segments outside a flow band.
- Commands: scroll the band, line scroll within it, and a way back to the cursor.
- Configuration: per display, naming the display and the rows of the band, and choosing
  what fills the segments around the focus — nothing, fixed offset lines, or a flow.

## Non-goals

- Rotating which segment is current, or shuffling regions between segments as the cursor
  moves. This is the 2023 `ScrollingManager` idea and it stays rejected; the window moves,
  the segments do not.
- Rendering a whole document, or anything proportional to document length.
- Re-implementing translation, wrapping, hyphenation or continuation marks. Blocks are
  rendered through NVDA's regions; only their assembly into rows is ours.
- Two cursors on one display, ever.
- Moving or activating anything outside a viewer band from within it.
- One flow spanning two physical displays, for now.

## Milestones

0. **Contracts.** DONE. The types everything else depends on, with no display work at all:
   `RenderedBlock` and `FlowRenderer`; `BlockId` and the render key; the source result
   states; active block and cursor ownership, with the `regions[-1]` audit written down as
   a list of call sites and what each must do; the `SegmentSpec` ownership split and the
   controller lifecycle; and the band as a rectangle on one display. Milestone 3 was not
   implementable without these, which is what the review of this plan established.
   Landed as the contracts at the top of `flow.py`, the `hostsSystemFocus` and `exclusive`
   fields on `SegmentSpec` with `FlowPanel` and `PanelOwner` in `panels.py`, and
   [flow-last-region-audit.md](flow-last-region-audit.md). The band needed no new
   validation: `validateAgainstHardware` already refuses a segment that reaches dead
   columns or straddles two displays, and a flow band is one segment.
1. **Flow core.** DONE. `flow.py` against fake rendered blocks: packing, the anchor, forward and
   reverse panning, re-render with a changed row count, the recovery rules, the in-window
   test and the minimal scroll. Reverse symmetry gets its own tests, since it is the defect
   this exists to fix. 42 tests in `test_flow.py`, including assembly and routing: every
   row is padded to the band width, which is what stops the block after a short one sharing
   its row, and routing into padding does nothing.
2. **Document source and renderer.** SOURCE DONE, RENDERER NOT STARTED. `DocumentFlowSource` in both flavours over a tree
   interceptor, and `FlowRenderer` against real translation. Budget, result states and
   invalidation from the start. Both reading units, blank collapsing with its interaction
   test, and per block latency recorded. Renderer tests cover short blocks followed by
   others, contracted braille, every text wrap mode, empty blocks, exactly full rows,
   continuation marks, and routing into padding.

   What landed: `flowSources.py` — position bound regions in both flavours, the cursor
   suppressed on every block but the active one, `hidePreviousRegions` cleared, viewer
   routing that moves its own position and never activates, the four result states, the
   budget with an injectable clock and a recorded slowest block, and blank runs collapsed
   by interaction rather than by role, resumable across fetches and keeping the run's first
   member as their identity. 26 tests in `test_flowSources.py`.

   What is left: `FlowRenderer`, turning a block's region into a `RenderedBlock` at the
   band's width. It is the half that needs real translation, so it wants writing against
   NVDA's own row splitting rather than against the test harness's deliberately crude
   buffer.
3. **The focus flow on the display.** DONE. `FlowPanel` as the focus segment, the configuration,
   both movement commands routed to the active block, and the cursor-on-pan rule. First
   hardware run on the Monarch in browse mode, testing with the cursor in a middle block:
   typing, untranslated braille input, caret events, routing, line commands and braille to
   focus. This milestone proves NVDA's rebuild can be made to land in our window rather
   than resetting it.

   What landed: `flowSegment.py` — a segment drawn by its controller, refusing the four
   calls with which NVDA moves a buffer's window (`saveWindow`, `restoreWindow`, `scrollTo`
   and `focus`), and holding one `FlowCommandRegion` so that NVDA's reach for `regions[-1]`
   finds something that routes to the active block. `flowBand.py` claims the band, builds
   the controller and answers `onRebuilt`, `onEvicted` and `onTerminate`, which is the first
   use of the lifecycle the claim contract was missing. The container builds a flow segment
   for any specification whose owner draws the focus, and `_doNewObjectMultiSegment` hands a
   focus change to such an owner instead of clearing the band and writing NVDA's regions
   into it. 21 tests in `test_flowSegment.py`.

   Corrected after review, all confirmed against the code: a flow claiming the whole
   display is one segment, and the patch handed every one segment container to NVDA, so no
   focus change ever reached the band — NVDA cleared it and appended its own regions.
   Stepping a block moved the add-on's idea of which block was active without moving the
   browse mode cursor. And a stand-in region of our own was the wrong shape: braille input
   only writes to the last region when it is a `TextInfoRegion`, so untranslated dots went
   nowhere and the erase that checks them could flush the input buffer. The band now puts
   the active block's own region there, which is what a single line display has in that
   place, and a live region calls back when something moves it so the window follows.

   Also fixed: panning could loop for ever on a block with a declared gap, since a gap
   cannot hold an anchor and mapped back to the block it belongs to; and a budget stop was
   drawn as blank cells, so under the fingers it was indistinguishable from the end of the
   document, which is the confusion the result states exist to prevent.

   Still owed, and not blockers for a first hardware run:

   Since fixed: **long blocks are read in chunks** rather than cut off at sixty-four rows.
   A row is named by its place in the whole block, not in the chunk it happens to be in,
   which is what lets the anchor survive a chunk changing underneath it; and a new chunk
   overlaps the old by a window, so the reader's place is inside it either way. The
   controller renders the next chunk before it will fetch the next block, or a long
   paragraph would be stepped over half read. Sixty-four rows is now a working set rather
   than a limit.

   Since fixed: **the budget belongs to an operation** — arriving, filling, panning,
   following the cursor — rather than to each source call underneath it. Per call it
   bounded nothing, as a band four rows tall makes four calls; a budget of one block now
   stops after one and the band says on the display that there is more it has not read.
   Laying a block out is charged for as well as reading it, since on a heavy page either
   can be the slow one.
   Since fixed: the flow follows the focus from document to document. The band takes its
   target from the object NVDA built the focus regions for, rather than asking the system
   again and racing the change; it keeps the controller it has when that resolves to the
   document already being read, and builds a new one otherwise. Each reading gets its own
   generation, so a bookmark from a document that has been left can never match a block in
   the one now being read. Targeting starts from the focus object, since the band is the
   focus segment, with the navigator object as the fallback.

   That also answers open question 2 for the focus flow. When the focus lands on something
   with no lines to read — a button in a dialog — the band gives itself back: it becomes an
   ordinary segment and NVDA presents the focus in it exactly as it always has, rather than
   leaving the reader with a blank display until they find their way to a document. Coming
   back to a document starts reading it again.

   Blank line collapsing now follows the same signal. A control the reader is working
   inside keeps every blank line, because there the blank lines are the document; browse
   mode presenting a document collapses a run of them, because there they are layout.

   Found on hardware and fixed: the band claimed the whole display, which on a composite is
   not a rectangle any hardware has. A Monarch and a Focus 80 driven as one display are
   nine rows of eighty cells, of which the Monarch's eight have only thirty-two, so the
   claim reached forty-eight dead columns and `validateAgainstHardware` refused it — every
   time, on that hardware, which is why nothing ever flowed there. The band is now the
   tallest physical display's own live rectangle, because rows are what a flow spends. This
   was the constraint stated two sections above and then not honoured by the code that
   claims the band.

   Second hardware run, on a Monarch: reading, panning in both directions, the cursor
   following a tab within the window, tabbing past the window, and typing into a form field
   all behaved. Two faults found and fixed.

   A jump backwards showed no cursor until the reader tabbed forward again. A block is read
   before anything knows whether it is the one the cursor is in, and a block that is not
   active suppresses its cursor, so a path that activated a block without reading it again
   left none — which is every path ending in `enterAtCursor`. The block that becomes active
   is now read again for its cursor. The same run also stopped a jump backwards throwing
   the window away: looking for the cursor's block now tries both directions, since when
   the block is not in the window there is nothing to say which way it went.

   A flow followed the focus out of the browser into Notepad and grew a row at a time as
   the reader typed. Browse mode is what a flow has been designed and tested against, so
   that is now the test: the object belongs to a browse mode document, or it is one.
   Whether browse mode is presenting it at this moment is deliberately not the test — a
   form field the reader has entered inside a page is still that page, and reads well as a
   flow, which the same run confirmed. Anything else is left to NVDA until a flow knows how
   to read objects, which is milestone 6.

   Third run, and a behaviour asked for from it: a browse mode quick navigation key that
   skips a section should set the reader down at what it found, with the document running
   on from there, rather than nudging their old window along. Grounding is what a reader
   wants after leaving a section behind; tracking is what they want while working through
   one. See "Grounding a jump" below.

   Known incomplete, and confirmed as such on that run: a control the reader has entered
   loses its prompt, and a combo box shows no choices. Those are milestones 5 and 6. The
   prompt is answered below; the choices are not.

   Since done: the settings dialog, which is what turned the flow from a command into a
   feature. `BrlMultiline flow` is a settings category of its own, because it answers a
   different question from the segment layout — how content is presented rather than how the
   display is divided — and because the reader's answer changes from one application to the
   next. It holds the switch, one checkbox per kind of content with browse mode as the only
   one so far, which physical display the band goes on, how many of its rows it takes, and
   whether a quick navigation jump re-grounds it.

   Two consequences worth stating. The band is now claimed for as long as the flow is on,
   whether or not there is anything to read as one at that moment: a band with nothing to
   flow presents the focus exactly as an undivided display would, and lights up by itself at
   the next document. Giving it back each time was what made the reader re-enable the flow
   after every dialog — the "reflow still has to be manually enabled when I go back to
   Chrome" of the third hardware run. And the setting is read on every rebuild, which is what
   makes a configuration profile the tool for "in this browser and not that one": NVDA
   switches profile when the foreground application changes, the switch rebuilds the display,
   and the rebuild claims or gives back the band with nothing to press.

   The band is no longer always a whole physical display. `flowRows` takes some of one
   display's rows from the top, leaving the rest to the segment layout, which is the same
   code with a smaller rectangle as this plan said it would be. Milestone 3 is complete.

   Two things the persistent claim brought with it, both found in review. A claim carries the
   rectangle it was activated with, and a rebuild re-composes that same claim — so changing
   the rows or the display in the settings left the band exactly where it was. An enabled
   band is now compared against what the settings ask for on every rebuild, and reclaimed
   when the two differ. And a band that is claimed but not flowing still has to be told where
   its focus changes go: `onRebuilt` used to return early when nothing was flowing, which
   meant a band rebuilt while the reader was in a dialog never heard them come back to a
   document. That path also must not clear the segment, since a rebuild has already drawn
   NVDA's own focus content into the new one.
4. **Following.** The in-window test, minimal scroll, entry context, and keeping the
   cursor's row visible inside a growing block. Includes what a flow does when the focus
   leaves it, which entering and leaving focus mode exercises directly.
5. **Browse mode forms.** SECOND HARDWARE RUN DONE, CORRECTED. Label
   above control, declared spacing after a short field, a multi line edit growing into the
   window.

   What landed: `flowForms.py`, holding the two decisions that are policy rather than
   arithmetic — which roles read as controls, and how many rows of a prompt belong above
   one. A block is recognised as a control from the field commands of its own text, which
   in a browse mode document are already in NVDA's process; fetching an `NVDAObject` per
   block is the reading this design has avoided from the start, and would have made a heavy
   page unreadable to pay for a row of context.

   A control declares a blank row after itself, which is rule 5 — spacing declared by the
   block — and is why the source classifies every block it walks rather than only the one at
   the cursor. Arriving at a control places the window so that what precedes it is above it,
   capped at half the band so the field itself is never pushed off; a long label is shown by
   its last rows, which are the ones that say what the field is for.

   That capping needed something the window did not have. `_fill` makes up a shortfall, and
   a window anchored at its own top row has none — it is full, and there is nothing above
   it — so reading rows the window does not need is `rowsAbove` and `_reachBack`, asked by
   row count rather than by shortfall.

   The growing edit turned out not to be quite "the existing rules" after all. Following the
   cursor brought the *block* into view, not the cursor's row within it, so a field taller
   than the band would have been shown by its top while the reader was writing at its
   bottom. The cursor's row is now what `syncToCursor` keeps visible, and a block that grew
   under the window follows its growing end down. The same correction is what keeps a reader
   at the bottom of a long paragraph from being thrown back to its top.

   The hardware run then found all of that wrong, in two ways worth keeping written down.

   **A control is recognised from the object, not from the document.** Reading the field
   commands of each block looked cheap — those fields are in NVDA's process — but it ran on
   every block of every document, a tax on all reading to answer a question that only
   matters at the block the reader arrived at. On an eight row band that spent the fetch
   budget, and the display filled with the marker meaning "there is more I have not read".
   Worse, it was wrong: tabbing into an edit field or a combo box drops browse mode into
   focus mode, so the reading position is *inside* the control and the line carries the
   control's field as an enclosing one. Every test of "is this block a control", by
   `_startOfNode` or otherwise, therefore answered no for exactly the two controls a reader
   most wants a prompt for. A checkbox, which does not enter focus mode, worked — which is
   what the hardware run reported. The question is now asked of the focus object's role,
   which is free and right in both modes.

   **A control is read at its own place, not at the cursor.** The same focus mode fact says
   where to read: with the cursor inside the field, the line is the value being edited, and
   for an empty field that is nothing at all — the blank row the run found where a tabbed-to
   item should have been. Asking the document where the control is (`makeTextInfo(obj)`,
   which a virtual buffer answers) gives the line browse mode itself would show for it,
   carrying the name, the role and the state. That is also the answer to "we want the
   accessible name when it is set": the flow no longer presents anything of its own here, it
   presents the line browse mode presents.

   Also from that run: the prompt cost four fetches to use one, because it asked for half a
   band of *rows* before looking at what was above. It asks for one block now.

   *What the second hardware run found.* One thing, and it is the other half of reading a
   control at its own place: the cursor sat on the first cell of the field and stayed there
   as the reader typed, and a field longer than the band never scrolled to what was being
   written. Reading a block from a stored position is what a flow does, and the block the
   reader is *in* is the one exception — see "a block starts where its reading unit starts"
   above, which also fixes the block identity that made every keystroke rebuild the window
   and take the label with it.

   Worth recording why the unit tests did not catch it: the test harness's region took its
   cursor from the object's caret rather than from the position the region actually reads,
   so every block tracked the caret and the pinning was invisible. The harness now models
   an offset within a line, and the three growing-edit tests written for milestone 4 fail
   against the old code — which is what they were for.

   *What the next cursor run found.* The active browse-mode form block was still bound to
   the tree interceptor. NVDA reports a focus-mode caret move against the real edit object
   and queues a braille region only when that region's `obj` equals the event object, so the
   flow never heard the move. The document now continues to own the block's identity,
   placement, label and neighbours, while that one active block is rendered through the
   focused edit's own `TextInfoRegion`. Entering and leaving the edit replaces the region
   without changing the document bookmark. This is a hybrid deliberately: using only the
   edit loses page context, while using only the tree interceptor loses live caret events.

   The same document source now supports standalone editable objects behind their own
   opt-in mode. There the edit is the document, so its lines naturally become spatial
   blocks as they do in Notepad and multiline desktop editors. A long active block also
   locates the caret's actual rendering chunk instead of clamping an unseen cursor to row
   63; the 64-row value remains a memory working set rather than a maximum field length.

   Still owed: a second hardware run on a real form, and what a combo box does — its choices
   are an object question, which is milestone 6.
6. **Adapters, viewer bands and objects.** FIRST HARDWARE RUN DONE, CORRECTED. The protocol
   and registry, `ObjectFlowSource` for lists and trees, and the decision in open question 1.

   What landed: `flowObjects.py`. One object is one block, and its braille is
   `NVDAObjectRegion` — NVDA's own presentation, with the name, role, value, states and
   position information the reader already knows. Nothing there re-derives any of it, so a
   block says exactly what a single line display would say about that object and the flow's
   whole contribution is that seven of its neighbours are under the same fingers.

   Three things differ from a document source, and all three follow from what an object is.

   *Walking is expensive.* Stepping a line in a virtual buffer stays in NVDA's process;
   stepping to the next object is a call into the application. So nothing reads ahead and
   the operation budget bounds the walk. This is the case the budget was written for, and
   the first where it earns its keep rather than getting in the way.

   *Nothing moves.* Panning a document flow moves the browse mode cursor, which is a reading
   position with no consequences. The equivalent for a list is moving the selection, and a
   selection is application state: it fires events, it changes what is shown, and in a menu
   it can act. An object flow moves the window and nothing else. That is the answer to open
   question 1 for every run of objects, and it is a stronger answer than "a viewer band":
   the band is still the focus segment, still shows where the reader is, and simply never
   writes back.

   *The focused object still shows the cursor*, so that the reader can feel which of the
   eight items on the display the arrow keys will act on. Without it a list of eight reads
   as eight equal things and they have lost the one fact they had before.

   Routing follows NVDA's own precedent in `ReviewNVDAObjectRegion`: a press where the
   reader already is does what it always did, and a press elsewhere moves the focus rather
   than acting. A finger landing on an item they were reading past must not activate it.

   Which objects read this way is a registry — `ObjectAdapter`, `register` — rather than a
   list of roles inside a condition, because the answer is a judgement about a kind of
   control and an add-on that owns one has a better judgement than this module does. Two
   built-in adapters: a run of siblings for list, tree, menu and tab items, and the children
   of a container the reader is choosing from, which is what a combo box's choices are.

   A document wins wherever there is one. A list inside a web page is part of that page, and
   the page has the more useful context: its heading, what came before the list, what
   follows it.

   Not done: `getBrailleRegions`. Nothing in NVDA implements it but a math presentation
   provider, so there is no protocol there to consume yet; when a component offers one, it
   is an adapter that returns regions rather than objects and the registry is where it goes.

   One thing the last-region audit predicted and this milestone met: `script_braille_nextLine`
   calls `regions[-1].nextLine()` with no check beyond the list being non-empty, and an
   object region has no such method — so a display whose line command is bound would have
   raised where the reader pressed a key. Object blocks answer it by moving the window one
   item and taking nothing with them, which is what the line commands can mean where the
   cursor is a selection.

   Off by default, unlike browse mode. The reading is new and turning the flow on should not
   change how a reader's dialogs behave until they ask for it.

   *What the first hardware run found.* An NVDA settings list, the NVDA menu, the Add-on
   Store's list and its tab strip, and a combo box on a real page all flowed, with no
   responsiveness complaint. Five things were wrong, and four of them were one thing wearing
   four hats: a distinction the code had collapsed into a flag that already meant something
   else.

   - *Panning bounced back to the focused item*, on the second pan — which was the first
     that had to fetch. `live` meant both "show the reader a cursor" and "the flow may move
     the reader's place", and a run needs the first without the second. Split into `live`
     and `movesCursor`. A block the flow itself fetched or re-read is no longer marked as
     news either: `dirty` means *NVDA* re-read the region, which is how a caret move inside
     a document reaches a flow, and a block set down by our own fetch read as a move the
     reader had made.
   - *Routing to an item activated it.* The controller made the block active and then told
     the region, so the region — whose whole rule is "a press where the reader already is
     acts" — was told they had been there all along. The region is told first now, and the
     flow's own place is set afterwards.
   - *A tab strip should activate*, and does: see the adapter's `activates` above. This was
     the reader's call, and it is the right one — a tab focused but not chosen shows nothing
     new.
   - *An open combo box lost its choices* once the focus moved onto one of them. What the
     run was found from is now fixed for the life of the source.
   - *A separator read as "unavailable"* followed by its dashes. Classified as decoration and
     drawn as a line of dots 7 and 8 across the band; see the object source section above.

   And what the second run found, all three of them decisions rather than defects: a menu
   item should be invoked by a routing key rather than merely reached, which settles the
   rule as "what would a click do"; a separator should be a line rather than a blank row,
   because braille readers like decoration too; and the cursor in an edit field must follow
   the caret, which is the block position and live cursor rules above.

   Two things the reviewer found that hardware would have found later: `MAX_CHILDREN` was
   applied after `obj.children` had already built the whole list, so it bounded the loop and
   not the work — the children are walked from `firstChild` now; and the source kept every
   object it had ever read, for the life of the run. It keeps none: a block's bookmark *is*
   its object, so there was never anything for the table to answer.
7. **Cost.** Answered: settled, pass. Measurement on heavy pages against the latency
   recorded since milestone 2, and whatever the numbers say.

   The instrument landed rather than the answer, because the answer is a hardware question.
   `FetchBudget` now keeps what each operation cost as well as what each block did: how many
   operations have run, how long the worst was and over how many blocks, the average, and —
   the number that matters most — how many ran out of blocks, since running out means the
   reader was shown less than the band could hold. The "Reports what the flow on the display
   has cost so far" command says the headline and writes the rest to the log.

   That last number is the one to watch, and the reason this milestone exists at all. Twice
   now a guessed constant has turned out to be the fault rather than the safety limit: fifty
   milliseconds per operation, and twelve blocks on a band that needs eight to fill. Both
   were reported by a reader as "the display shows dashes", which is a long way from the
   cause. A reader who can press one key and say "nine operations in a hundred ran out"
   closes that gap.

   The count itself was wrong on both sides at first, and an instrument that miscounts is
   worse than none. It counted an operation that had *used* its allowance, so one whose last
   permitted block filled the last row of the band — which stopped nothing and wanted
   nothing more — counted as a stop; and it never counted a stop by the clock at all, which
   is the one case where a larger allowance would only buy a longer wait. A stop is now
   recorded where a fetch is actually refused, with the reason kept separately.

   What the numbers should decide: whether `budgetForBand`'s allowance is right, whether
   the time limit is doing anything at all beyond the block count, and whether an object run
   — where every step is a call into the application — wants a budget of its own rather than
   the document's.

**Answer:** Claude reported the following: 
Your budget worries are settled: 63 operations, zero cut short, slowest block 4.59 milliseconds against a 250 millisecond allowance. The budget is no longer a factor.


## Open questions

1. **Which components want a viewer band rather than the focus flow?** ANSWERED for runs of
   objects, and the answer turned out not to be a viewer band at all. A list reads in the
   focus band, showing where the reader is, and simply never writes back: the thing that
   made a viewer seem necessary was the fear of moving something, and moving nothing is a
   property of the source rather than of the band. What is still open is the other half —
   whether anything wants a *second* band beside the focus, which is a question about
   display geometry and wants asking of a reader with rows to spare.
2. **What speech says on a multi row pan.** The cursor-on-pan rule speaks the top block of
   the new window. Whether that is right, or whether a pan should be silent and leave
   speech to the arrow keys, is a hardware question.
3. **What the deferred marker looks like.** A budget stop needs to read as "more is
   coming", distinct from both blank content and the end of the document, in whatever
   cells one row can spare.
