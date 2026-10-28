# Pin places

Plan for pins that make their own room. Today a reader who wants more than one pin has to set
up segments and their sizes beforehand, in the settings, and every pin goes into one of those
segments. That is hard to remember, and the right size depends on what is pinned: a web page
may want the whole Monarch one day and half of it the next.

Status: designed, nothing built. Builds on the pinned objects dialog (port-plan.md, The pinned
objects dialog) and reuses the placement half of the app layout contract
([app-layouts-plan.md](app-layouts-plan.md)).

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
4. The flow band shrinks to the rows left to it and stops below `FLOW_MIN_ROWS`. The Rows
   control should say so when a size would stop it.
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

1. Whether a pin's reading position can survive a change of size. To be found out when it is
   tried; not needed for a first version.
