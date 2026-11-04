# Pin places

Plan for pins that make their own room. Today a reader who wants more than one pin has to set
up segments and their sizes beforehand, in the settings, and every pin goes into one of those
segments. That is hard to remember, and the right size depends on what is pinned: a web page
may want the whole Monarch one day and half of it the next.

Status: built and unit tested, not yet tried in NVDA or on hardware. Builds on the pinned
objects dialog (port-plan.md, The pinned objects dialog). The places are in `pinPlaces.py`, the
plugin's side in `GlobalPlugin.pinToPlace` and the methods after it, and the dialog in
`pinDialog.py`. As built, below, says where the build differs from this design.

## The idea

A pin made from the dialog can go to a **place** instead of a configured segment. The rows of
a place are divided again whenever a pin is added to it, released from it, resized or moved
in it. Nothing is locked: pinning a second object to a place that one pin fills shares the
place between them.

Configured segments stay. A reader who always wants set sizes keeps setting up segments and
pinning to them, as now. Places are for the reader who has not planned it out.

## Places

1. **The other display**, on a combined display: the whole of a display that does not hold the
   focus segment. One entry per such display, named as the display relative commands name it.
2. **Above the focus** and **below the focus**: rows taken from the run of rows the focus
   segment is in, on a display with more than one row. On a display of one row, before and
   after the focus, in cells.

A place on the focus's own display treats the focus as one more Fill segment that cannot be
released, so it always keeps at least one row.

## Sizes

1. Each pin in a place has a size: **Fill**, the default, or a number of rows. On a display of
   one row the number is in cells.
2. Fixed sizes are given first. The Fill pins share what is left equally, and a row left over
   goes to the earlier pin. So three Fill pins on a ten row Monarch get 4, 3 and 3, and a pin of
   3 rows beside one Fill pin leaves the Fill pin 7.
3. Every pin keeps at least one row, or `MIN_PLACE_SHARE` of the row on a display of one row. A new
   pin, or a new size, that would take a pin below that is refused, saying how much is free:
   "Only 2 rows are free on the other display".
4. The app layout contract allows only one segment without a size. Places need their own rule
   for sharing, but placing on the hardware, the boundary adjustment and keeping the focus on
   its display, is `appLayout.placeSegments` and `planFor` as they are.

## The dialog

1. The segment combo box lists the free configured segments, then the places. A place that
   holds pins says so: "The other display (holds 1 pin, will share)".
2. A **Rows** control (cells on a one row display), enabled for a place: Fill or a number.
3. A **Position** control, enabled for a place: after the pins already there, the default, or
   before them. On a Monarch that is below or above.
4. The pinned objects list shows where each pin is and how it is sized: "the news page: rows 1
   to 5 of the Monarch (fill)". For a pin in a place, the selected pin can be given another
   size, and moved up or down, without releasing it.

## Inside

1. Pins in places are the reader's pins, in the plugin's `_monitors`, so refreshing, routing,
   quick navigation, hiding under the lock screen and release all work as they do.
2. A reader owned claim, one per place, shows the place's pins. A rebuild of the place moves
   each monitor to its new segment with `ObjectMonitor.moveTo`, and a pin whose size changed is
   built again at that size as a resized segment's is now.
3. App layouts see these pins as the reader's pins and arrange only what is left, as the app
   layout plan's first rule says. A place that fills a display leaves an app layout nothing
   there, which is meant.
4. The flow band shrinks to the rows left to it and stops below `FLOW_MIN_ROWS`. The reader
   is told when a pin or a new size stops it.
5. Segment numbers change as places are divided again, so the per segment commands are mainly
   for configured segments. The dialog is how a reader manages places.

## Decided

1. **Fill pins share equally.** A new Fill pin does not take half of one pin; the place is
   divided equally among all its Fill pins.
2. **Places remember nothing.** When the last pin in a place is released, its rows go back to
   the focus or the configured arrangement, and the next pin there starts with nothing. A reader
   who wants the same sizes every time sets up segments.
3. **Pins do not outlive NVDA,** as now.
4. **Pins come before app layouts,** as they do now. A place's rows are the reader's, and an app
   layout is offered only what is left around the focus. A spike against `activatePanel` should
   confirm that a place's claim and an app layout's settle that way when both reach into the
   focus's run.
5. **The minimum on a one row display is a share of the row,** `MIN_PLACE_SHARE`, a quarter,
   rounded down: 20 cells on a Focus 80, 10 on a 40 cell display. A fixed count would suit
   one width and not the other, and 40 cell displays are common.
6. **Setting pins aside keeps them,** and gives every row they held, in places and in configured
   segments, to the focus or an app layout. A pin that cannot be read when they come back is
   released. See Setting pins aside for now.

## Setting pins aside for now

A reader sometimes wants the display without their pins for a moment, to see the focus, the
flow or an app layout across the whole display, and then wants the pins back as they were.
Suspending pins was avoided before because a pinned object may go away. That worry is about
putting pins back after they are gone, for example after a restart, which would mean finding
each object again. Setting pins aside for now is a different thing: the pins are never let go,
only not shown.

1. **A command toggles it,** unassigned like "App layouts: Toggle for now", and the pinned
   objects dialog gets a matching check box. It reports "pins set aside" and "pins shown".
   Until NVDA restarts, as the app layout toggle is.
2. **While set aside, every monitor is kept.** Each keeps its object, its reading position and
   its size in its place. The timer does not read them, so a set aside pin costs nothing.
3. **The display is shown as if nothing were pinned,** which is the point: the focus, or an app
   layout, takes the rows the pins had. Places give their rows back, and the segments that held
   pins in the configured arrangement are given to the focus as well, by setting that
   arrangement aside the way an app layout does. App layouts are offered the whole area the
   pins left, and placed again when the pins come back, as `AppLayoutHost.placeAgain` already
   does for a change of room.
4. **Showing them again rebuilds the places as they were.** Same pins, same sizes, same order.
   A pin whose object can no longer be read is released, saying so as the release command does,
   since there is nothing to bring back.
5. **Pinning or releasing while pins are set aside** shows them again first, so the reader
   always sees what the dialog is changing. The dialog's list still lists set aside pins, so
   one can be released without showing them.

## Open questions

None at present.

## As built

What building it settled that the design left open, and where it differs.

1. **Places are part of the reader's arrangement, not a claim.** `GlobalPlugin.baseView` lays
   the places into the configured view (`pinPlaces.withPlaces`) before any claim is composed
   over it. A pin in a place is an unreserved segment like one of the reader's own, so
   refreshing, routing, quick navigation, the lock screen, the per segment commands and the
   app layout's room all work unchanged. A claim was tried on paper first and dropped: a claim
   is evicted whole by any claim that touches it, so a place and an app layout could not share
   the focus's rows.
2. **The app layout's placement is not reused.** A place is one display's rows or cells, the
   focus segment or a whole other display, so nothing ever crosses a boundary and
   `placeSegments` has nothing to do. Dividing is `pinPlaces.divide`.
3. **The focus place is the focus segment,** not the run of free rows around it. A reader with
   segments of their own keeps them; the place divides only the one that follows the focus.
4. **Another display's place takes every segment on that display.** It is offered only while
   none of the reader's own segments there holds a pin and the flow is not there, and a place
   under another claim, such as a drawing, is not offered at all. With every pin sized and none
   filling, the rows left are blank. A reader's segment that is the whole of such a display, as
   with a segment count of 1, is offered only as the place, and typing its number chooses the
   place: they are the same rows, and only the place can be shared or resized later.
5. **A pin's key outlives the division.** Keys are `place.<id>.<n>`, numbered as pins are made,
   so dividing a place again changes rectangles and not keys, and a monitor stays filed under
   its key. A pin read as a flow is rebuilt at its new size and put back where the reader had
   panned it to: `FlowController.readingPlace` takes the position at the top of the old flow,
   for a run of objects the object at the top, and for a table its row and page of columns, and
   `enterAtPlace` enters the new one there without moving the caret. An object or table row
   wrapped over several rows keeps the row it was panned to, by the first cell of its region on
   the top row, which is a place in its content and so outlives a change of width. In a paragraph wrapped over
   several rows, the row holding that position goes to the top, not the paragraph's first row;
   past the first chunk of a long paragraph, its cell is found by halving through the region
   and only the chunk around it is rendered, as for a caret (`_renderActiveChunk`). Found in testing, where a page shrinking from eight rows to four went
   back to where the caret was.
6. **Pins are held while the display makes way for them.** The flow band and an app layout were
   claimed for the arrangement before the change, and for one rebuild they can cover a pin's new
   segment. `GlobalPlugin._rearrange` holds every pin that rebuild drops and files it again once
   the flow has moved (`GlobalPlugin.roomForFlow`, through `FlowBand.bandRect`) and an app layout
   has been taken down. The layout is then asked for again from the focus, since its room
   changed. The flow loses its reader's place when it moves, as it does for any other change of
   band.
7. **Set aside, each display is one segment** (`pinPlaces.undividedView`), the focus on the one
   it was on. The reader's segments that held no pin go too, since there is no other way to give
   the focus the rows of a segment that is not beside it, and an empty segment shows nothing.
   While pins are set aside the dialog offers only places, since segment numbers would be those
   of the undivided display.
8. **Gone is unreadable.** `ObjectMonitor.isGone` is an object that raises when read, or whose
   window Windows no longer has. A page pinned in browse mode is held by the page's root object
   and its tree interceptor, not by the link or button the reader was on, so a page whose links
   change is not taken for gone.
9. **Commands.** Setting pins aside has its own unassigned command, beside the dialog's check
   box. The per segment release command releases a pin in a place too, and the place shares its
   room among those left. The per segment pin and release commands show pins set aside again
   first, as the dialog does when pinning, since their numbers are those of the arrangement the
   pins come back to.
10. **A change is checked before it is kept.** `GlobalPlugin.doPlacesFit` lays the places out
    before a new pin or a new size is kept, since a place that cannot be laid out is left out
    and every pin in it released. A change that does not fit is refused, and the pins stay as
    they were.
11. **The focus moving between displays takes the places with it.** Places are laid over the
    configured view, so which displays the focus can move to is asked of that view. The focus
    place goes with the focus, and the place on the display the focus arrives at goes to the
    display it left, the two trading as a pin in a segment does (`Places.followFocus`). Both go
    back to sharing equally, since a size in rows means nothing on a display of one row. The
    flow band or an app layout on the display the focus leaves covers where the pins are going
    until the move has carried it along, so the move rebuilds inside `GlobalPlugin.holdingPins`,
    as `_rearrange` does, and a pin is released only if it has no segment once it is over. An app
    layout is taken down and asked for again only while a place holds a pin. A pin
    with no room where it lands is released, the last first, and the reader is told.
12. **The dialog chooses by key.** `PinManager` keeps the targets it listed, and a name chosen
    from them is checked again by its key or place, so segments numbered again while the dialog
    is up do not turn the choice into another segment. A typed segment number or size is a bare
    whole number, or a size as the list words it; anything else is refused rather than read as
    some other number.
