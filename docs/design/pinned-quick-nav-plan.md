# Quick navigation in a pinned document

Plan for moving through a pinned document by its structure, from the display it is on: with the
Outlook reading pane on the Monarch and the focus in the message list, `h` on the Monarch's keys
puts the next heading of the message at the top of the Monarch, and the focus, the caret and the
Focus 80 are left exactly where they were.

Read [layered-keys-plan.md](layered-keys-plan.md) for layers and how a command learns which
display it was pressed on, and [app-layouts-plan.md](app-layouts-plan.md) for monitored segments.
This document assumes both.

## Verdict

Worth doing, and NVDA has already done the hard part. Every browse mode quick navigation key ends
in `BrowseModeTreeInterceptor._quickNavScript`, which does three things: it searches with
`_iterNodesByType(itemType, direction, position)`, it speaks what it found, and it calls `moveTo`
on it. The search takes any position, not only the caret. A pin searches from its own reading
position and never calls `moveTo`, so nothing the reader is working in moves.

Every kind of browse mode document implements the search: virtual buffers for web pages, Word
through its object model and through UI Automation (which is the Outlook reading pane), Excel and
Kindle. A type a document does not support raises `NotImplementedError`, which NVDA already
answers with "Not supported in this document".

The parts that are this add-on's are finding the pin a key is meant for, and moving a pin's
reading position without moving anything else.

## The pin's side

1. **Only a pin read as a browse mode document.** A pin's document is its flow source's object,
   or for a pin read as regions its region's object, and it has to have `_iterNodesByType`. A run
   of objects, such as a chat, has no positions to search between, and says "Not supported in this
   document" like any document that cannot answer.
2. **Searched from where the reader is.** For a flow, the text at the start of the top row, read
   off its first cell through the region it came from, so a wrapped block panned part way gives
   the row shown. Where the last search put the pin and the reader has not moved it since, from
   exactly what was found. A pin read as regions has a position of its own in its region. See
   the review of 814bf39 below.
3. **Grounded at what was found.** The found item's position becomes a block, through the same
   `_blockAt` the source uses for everything else, and the band is put down at it with
   `groundAt`, so the heading is at the top and the document runs on from it. That is what the
   band itself does for a structural quick navigation key; see `flowQuickNav`. A pin read as
   regions has its region's position set to it.
4. **Spoken as NVDA speaks it.** `item.report()`, which is what `_quickNavScript` says, without
   the move. Nothing found says NVDA's own message, "no next heading" and the rest, in NVDA's own
   translation.

**A browse mode document may not exist yet.** NVDA makes one when something gets the focus. Found
on hardware: the Outlook reading pane shown by the Outlook layout reads from its Word document
object, an `OutlookUIAWordDocument`, with no browse mode document behind it, though braille shows
its links and headings as browse mode would. It looked like one and was not, and the layer key
found nothing to search.

So a document object with a browse mode class, what NVDA+Space would make of it, is searched
through one made over it for the purpose and never registered. NVDA routes nothing through it and
makes its own when the reader moves in, as it always did. Its positions wrap the object's own
(`RootProxyTextInfo`), so the pin's position goes in wrapped and what is found comes out as its
`innerTextInfo`, which the pin reads. Registering one with `treeInterceptorHandler.update(obj,
force=True)` was the alternative, and was not chosen: it would change what NVDA does when the
reader moves into the pane, and how the pin reads.

## Which pin a key is for

The tricky half, and the reader named it: a key has to reach the pin on the display it was
pressed on, and a display can hold more than one.

1. **The display: from the key.** Layered keys are per device already, so the commands live in a
   layer, and a layer's command learns its display from `keyLayerDispatch.displayFor`, as paging
   a table in columns already does.
2. **The segment: the one last used.** Of the pinned documents on the pressed display, the one
   the reader last panned or routed in. The container tells the plugin each segment a routing key
   or a pan reaches, through a hook like `segmentRouter`, and the plugin remembers the latest for
   each display. Failing that, the first in display order, which on a display showing one pin is
   the only one there is.
3. **Changing it.** A command moves the target to the next pinned document on the pressed display
   and says its name.
4. **Never the focus segment.** NVDA's own quick navigation works there.

## The layer

**Pinned document**, shipped for the Monarch, and in the dialog for any other display:

1. Browse mode's letters: h heading, k link, t table, l list, i list item, g graphic, f form
   field, d landmark, q block quote. With dot 7, the previous one, as the chart layer's v is.
2. s for the next pinned document on this display.
3. Stays on until turned off, and falls through to the default layer, so the d-pad still reads.
4. **The layer key turns it on** where the pressed display holds a pinned document and nothing
   more specific is up. A new context, `pinned`, least specific of all and looked for only when
   the layer key is pressed, as a table is, and for that display only. Not followed: letters stop
   typing while the layer is on, so it never comes on by itself.

## Deferred

1. Quick navigation in a pin from the keyboard. The keyboard's letters belong to the focus, and
   in the Outlook layout that is the message list's own first letter navigation. It would need a
   keyboard layer and a choice of display.
2. Heading levels. The digits need the number sign on a braille keyboard, and none of the
   letters is free in browse mode's set.

## Milestones

1. Searching and grounding a pin: the source and controller methods, and the module that finds the
   document and the position, searches and moves. Unit tested against stubs.
2. The target: the container hook, the last used segment per display, and the switching command.
3. The layer and its context, shipped for the Monarch as a revision of the shipped layers, so a set
   saved before it is given the layer once.
4. The readme, and trying it on the Monarch with the Outlook layout and with a pinned web page.

## Review of 814bf39

Five faults found by review, each reproduced by the reviewer's failing test before it was fixed.
The tests are in `test_review814bf39.py`.

1. **A repeated key found the same item again.** The search started at the top of the window,
   which after a jump is the start of the block the item is in, so two links in one line found the
   first for ever. And panning a wrapped block left the search at its start. The pin now keeps
   where it was put and where its window was then (`windowMark`), and searches from exactly there
   until the window moves; `topPosition` reads the top row's first cell through its region.
2. **An ordinary display was never found.** The layer context and the last used segment both
   looked displays up in the combined display's map of members, which is empty for a display
   connected on its own. Such a display now has every segment, under its own driver name, as the
   per display layer key command already treated it.
3. **Building a searcher could disturb NVDA.** A virtual buffer records itself in NVDA's lookup of
   documents as it is constructed, so building one to search would take a live one's place. NVDA's
   own browse mode document is used when it is ready, and otherwise only the kinds in
   `SAFE_TO_BUILD`, UI Automation's and Word's, are built.
4. **A table or a list was spoken whole.** NVDA speaks a line of a table, a list or a landmark when
   quick navigation finds one. Each type now carries NVDA's reading unit, and what is spoken is a
   copy of the position, since reporting may move it.
5. **A pin that could not move said nothing.** `placeAt` now says whether the pin moved, a failure
   or an error there is said, "Cannot show that in this pin", and the search's position is read
   inside the same guard as the rest.
