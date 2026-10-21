# App layouts

Plan for letting an application's app module arrange the display it is shown on: the chat
history of a Teams conversation in the top six rows of a Monarch with the compose box on the
rows below it, or an Outlook message list beside its reading pane, each coming up when the
focus enters the place it is for and going away when the focus leaves.

Status: milestones 1 to 4 are built and unit tested, with decisions 6 and 7 below added after
trying them. The contract is in `appLayout.py`, the lifecycle in `appLayoutHost.py`, and the
Teams add-on offers a chat layout. Tried in NVDA with Teams, on a Focus 80 alone and on a Focus
80 and a Monarch combined: the layout comes up from the compose box, goes when the focus moves
into the history, keeps the focus on its display, and spreads. A secondary segment whose object
is replaced, as an Outlook reading pane's would be, is unit tested only, since no app module
offers one yet.

Read [architecture.md](architecture.md) for views, panels and segments, and the developer
section of the readme for the flow attributes this extends. This document assumes both.

## What already exists, and what it lacks

Almost all of the machinery is in place, which is what makes this a contract problem rather
than a rendering one.

1. **Arranging the display from code.** `activateView` and `activatePanel` in the global
   plugin let code replace the arrangement or claim part of it, with validation against the
   hardware behind a composite display. `activatePanel` is marked experimental, and the
   reason it gives is the one this plan has to solve: an owner is not told when its claim is
   rebuilt or dropped.
2. **Showing an object that does not have the focus.** `ObjectMonitor` reads a pinned object
   into a segment, as a flow when the segment has room, and `patches` re-reads every pin on a
   0.4 second timer that writes nothing when nothing changed. A pinned run container, such as
   the Teams message list, already reads its messages and follows new ones at the end.
3. **Telling the add-on how to read an application.** The flow attributes
   (`brlMultilineFlowRun` and the rest) are read off objects by name, with nothing to import.

What is missing is the part in the middle: a way for an app module to say *this is the
arrangement for where the focus is now*, a rule for when that arrangement applies and when it
stops, and a pin whose object is a question asked again rather than an answer kept.

## The reader's side

One setting, **Let applications arrange this display**, in the BrlMultiline panel. It sits
under `displays.<key>` in the configuration like every other per display setting, which makes
it per display and per profile for free: a reader can allow it in a Teams profile and nowhere
else, or allow it on the Monarch and not on the Focus 80.

Off by default. It is a privilege the reader grants, not something an app module takes.

While an app layout is up, the configured arrangement is set aside rather than changed, and
it returns the moment the layout goes.

**The reader's pins come first.** An app layout is given only the rows the reader has not
pinned anything to, and never moves, covers or releases a pin. A reader who pinned a clock to
the bottom row of the Monarch still has it there in Teams, and the chat layout is built in the
rows above it. Setting pins aside while a layout is up and putting them back afterwards was
considered and deferred. It would have to be another choice for the reader, and it is not
worth building until someone needs it.

## The app module's side

One method on the app module, found by name like the flow attributes, so an app module keeps
working unchanged with BrlMultiline absent, disabled or older:

```python
def brlMultilineLayout(self, focus, display):
	...
```

It is called with the new focus and a description of the display the focus segment is on,
and returns either `None`, meaning no layout here, or a description of one:

```python
return {
	"name": "chat",
	"segments": [
		{"size": display.rows - 2, "show": self._messageList},
		{"show": "focus"},
	],
}
```

1. **`name`** identifies the layout. A focus change that returns the same name keeps the
   layout as it is, so panning positions survive moving between the compose box and the
   message list. A different name replaces it, and `None` takes it down.
2. **`segments`** run top to bottom on a display with more than one row, and left to right
   on a single row display such as a Focus 80. That is the rule the configured arrangement
   already follows, and it is why a Monarch never gets side by side segments: rows are what
   make scrolling work.
3. **`size`** is in rows on a multi row display and in cells on a single row one. A segment
   without a size takes what is left, and exactly one segment may omit it.
4. **`show`** is `"focus"` for the segment that follows the focus, which exactly one segment
   must be; an `NVDAObject`, which is pinned as it is; or a callable taking no arguments and
   returning an `NVDAObject` or `None`, which is asked again whenever the segment is
   refreshed. The callable is what the reading pane needs.

`display` carries `rows` and `columns`, in the spirit of `braille.handler.displayDimensions`,
but counting only the free area: the rows not holding a pin on a multi row display, and the
cells not holding one on a single row display. That is everything an app module needs to
choose an arrangement. One row means divide by cells, more than one means divide by rows, and
none means there is no room and the method should return `None`. It does not say which
displays are connected or where one ends and the next begins. On a Monarch and a Focus 80
combined, the app module sees eleven rows and lays out eleven rows; BrlMultiline knows the
tenth is the Monarch's last and the eleventh is the Focus. That keeps app modules from having
to track hardware, at the cost of the boundary question below.

The free area is the unbroken run of free rows the focus segment is in, since a layout arranges
the focus among other things and has to be where it is. A reader whose pins leave two separate
gaps gets the one holding the focus for the app layout, and the other stays as configured.
Splitting one layout across a pin is not worth what it would cost to explain. The first version
took the longest run; showing the layout found that its focus segment would then not be the
focus whenever the focus was in the shorter one.

Plain dictionaries rather than classes so that nothing has to be imported. A small helper
module may come later for app modules willing to take the dependency, as `register` is for
object adapters today.

## When a layout applies

The layout follows the focus, which is item 3 of the request and the part that needs most
care.

1. **Asked on every focus change, and on a change of application.** In `event_gainFocus`,
   after `nextHandler`, as the column and chart work already does. Only when the setting is
   on for the display NVDA is using, which for a combined display is the combined display's
   own section, and the focus's app module has the method, so every other focus change costs
   one `getattr`.
2. **Settled before it is shown.** Focus in a web application passes through containers on
   its way to where it is going, and a layout that came up and went down for each of them
   would rebuild the display several times for one keypress. A change of layout waits for the
   focus to stay put for a short interval; staying on the same layout is immediate because
   nothing is rebuilt.
3. **Gone when the application is.** Leaving the app module takes the layout down without
   asking it, so an app module that raises or forgets cannot leave its arrangement on the
   display of another application.
4. **Failure is the configured view.** A layout that does not validate on this display, a
   method that raises, or a `show` callable that keeps raising, puts the reader back on their
   own arrangement and logs why. The reader is never left with a broken display.

## Keeping a secondary segment current

Item 4 of the request, and the Outlook reading pane is the hard case. Selecting another
message does not change the pane's content in place: the document object is replaced, and a
pin on the old one would go on showing the old message or nothing.

1. **A `show` callable is asked again on every focus change within the layout**, which is
   when the selection changes in a message list, and on the pin timer at a slower rate, since
   the pane loads after the selection does.
2. **A different answer replaces the pin.** The monitor is rebuilt on the new object and its
   reading position starts at the top, which is what a new message wants. The same answer
   keeps it, so a reader partway down a long message is not thrown back to the start by an
   unrelated focus event.
3. **An unchanged object is re-read as pins already are.** The timer's cheap unchanged test
   is what keeps a live chat current today, and nothing here changes it.
4. **`None` blanks the segment** rather than leaving stale content up. "No message selected"
   is a true thing for the pane to say.

Comparing answers needs care: two `NVDAObject` instances for the same element are often not
the same Python object, and on UI Automation two reads of one element compare equal only
through NVDA's own `__eq__`. The comparison uses `==` and falls back to treating the answer
as new, which costs a reset of reading position and never shows the wrong content.

## Milestones

1. **The contract, without the focus.** The setting, the toggle, the method, the dictionary
   format and its validation, the free area and the boundary adjustment, and a debug command that asks the current app module for its layout and
   shows it. Unit tested against stubs.
2. **Following the focus.** Asking on focus and application changes, settling, keeping the
   same layout by name, taking it down on leaving. This is where `activatePanel` gets the
   lease it is waiting for, because the layout is its first real owner.
3. **Resolved pins.** `show` callables, replacement and blanking. Tried against Outlook's
   reading pane.
4. **Teams.** The ms-teams app module gains a chat layout, which doubles as the readme
   example, and is tried on the Monarch and the Focus 80.

**Routing in a secondary segment moves within what is shown there** and nothing else, as in a
pin today. The focus stays where the reader put it, so routing into the chat history never
takes the reader out of the compose box. *Superseded by As built item 11: a press there now goes
to what it is on, as a click would.*

## Decided

1. The setting is off by default, per display and per profile.
2. The app module is told the free rows and columns, not the displays. A combined display is
   one tall display to it.
3. Pins are never touched. The layout gets only the free area. Setting pins aside is deferred
   until it is needed.
4. Routing in a secondary segment moves within it only. Reversed; see As built item 11.
5. **A segment across a display boundary is adjusted quietly.** On a Monarch and a Focus 80
   combined, a layout asking for six rows and then five would put a segment over the Monarch's
   last four rows and the Focus's one, and a segment cannot span two pieces of hardware of
   different widths. BrlMultiline moves one edge of the segment onto the boundary: its lower
   edge if anything comes after it, its upper edge if it is the last. The rows it gives up go
   to the segment without a size, or, if that is the one being cut or is on the wrong side, to
   the neighbour across the moved edge. `placeSegments` in `appLayout.py` has the exact rule and
   its examples. The app module is not told, since it cannot see the boundary it
   crossed. Refusing the layout was considered and rejected for that reason.
6. **Nothing is announced.** The display changing is the signal, and the focus change that
   caused it has already spoken. The debug command in milestone 1 says what layout is up and
   why.
7. **A toggle sets app layouts aside for now.** An unassigned command, "App layouts: Toggle
   for now", turns them off and on until NVDA restarts, for the time the reader wants their
   own arrangement back in one window without going to the settings. It reports "app layouts
   off" and "app layouts on", and turning them off takes down any layout that is up.

## Open questions

None at present. New ones go here as the milestones turn them up.

## As built

What milestones 2 to 4 settled that the design above left open.

1. **The claim.** `AppLayoutPanel` claims the free area and offers its focus segment, so the
   reader's focus segment, which is always inside the area, is evicted and replaced. The
   object segments are reserved and the focus segment is not, exactly as with the reader's own
   arrangement. Ranked `PRIORITY_APP_LAYOUT`, between the flow and a drawing.
2. **The flow waits.** While a layout is up, `_applyFlow` leaves the flow alone and
   `_carryOverFlow` suspends it, as for a drawing, so its controller and the reader's place
   survive. The layout's focus segment is an ordinary segment, not a flow.
3. **Two dictionaries of monitors.** The reader's pins stay in the plugin's `_monitors`; the
   layout's are in `AppLayoutHost`. `_everyMonitor` joins them for refreshing only, so the
   layout's segments are never counted, carried or rehomed as pins.
4. **The clock.** The pin timer runs while a layout is up even with every segment blank, and
   asks the callables again every `RESOLVE_SECONDS`.
5. **A rebuild.** `AppLayoutHost.onRebuilt` runs before the flow is carried over. A claim the
   rebuild dropped forgets the layout; a setting turned off takes it down after the rebuild.
6. **The focus stays on its display.** Found on hardware: with a Focus 80 stacked above a
   Monarch and the focus on the Focus, the Teams layout, history then focus, put the history on
   the Focus row and moved the focus onto the Monarch. Top to bottom is how the hardware is
   stacked, not a choice anyone made, so `planFor` decides by the display holding the reader's
   focus segment instead. A focus display with rows to spare takes the whole layout and the
   claim is that display only. A one row focus display takes the focus segment alone, whatever
   size it asked for, and the other segments go on the tallest other display, the one without
   a size taking what is left there. The boundary rule of decision 5 now applies only when no
   focus rectangle is given. A separate `grow` key was considered and not added: the segment
   without a size already says which one grows. Handling a one row area and a tall one is the
   app module's business, as the Teams layout does; one that only works with rows returns
   None for a single row.
7. **Spreading is the reader's choice.** Also found on hardware: with the focus on the Monarch,
   the whole Teams layout stayed there, and the reader wanted the history on the Focus 80. No
   rule can know that, and settings in each app module would make every add-on answer it
   differently, so it is one BrlMultiline setting, `appLayoutSpread`, per display and per
   profile, which makes it per application through NVDA's own profiles. With it on, `planFor`
   splits the layout even when the focus display has rows to spare: the focus takes its whole
   display, the rest goes on the tallest other one. On a one row other display the others share
   its cells evenly, since their sizes were rows. An unassigned command turns it over and
   writes to the profile in force, so pressing it in Teams answers for Teams. A change, by the
   command, the settings or a profile switch, places the layout up again at once, asking the
   app module afresh because it is told a different area.

8. **Responsiveness.** Found on hardware: new Teams messages reached the history segment
   slowly. The segments were pins as far as the clock was concerned, and a pin waits up to
   `MONITOR_QUIET_AGE`, two seconds, while the reader types, so a reply arriving while they
   typed waited for them to stop. That rule is for windows the reader is not in, and a
   layout's segments are in the one they are, so they now have `LAYOUT_QUIET_AGE`, the
   ordinary interval, and the reader's pins keep theirs. For the rest of the wait, an app
   module that knows something arrived calls `GlobalPlugin.brlMultilineContentChanged`, found
   by name like everything else in the contract; calls are folded together for
   `CHANGE_SETTLE_SECONDS`. BrlMultiline watching events itself was considered and left
   undecided: which events mean "content changed" differs by application, Teams uses live
   regions while focused and others UIA events or none, and watching busy events would cost
   on every one of them. The app module receives its own events already and knows which matter.
9. **A focus segment may ask for the flow.** Found on hardware with Outlook: spread across a
   Monarch and a Focus 80, the message list had the whole Monarch and showed one message,
   because the flow waits while a layout is up (item 2). A list wants the flow there and a
   compose box does not, so it is the app module's choice, `"flow": True` on the focus
   segment, honoured with at least `FLOW_MIN_ROWS` rows. `AppLayoutPanel` then marks that
   segment as the flow band's own is, hosting the focus with its owner drawing it, and
   `FlowBand.segment` answers it while the layout is up, so the waiting flow draws there with
   its controller and the reader's place, and goes back to its band when the layout goes. A
   reader without the flow loses nothing: no owner registers, and NVDA's focus content is
   written in as before.
10. **A key for a reused element.** Also from Outlook: its reading pane keeps one document
   window for every message, so `==` saw one object and the reader's place carried from
   message to message. An object a `show` callable returns may carry `brlMultilineShowKey`;
   different keys make two answers different. Equal keys still need `==`, so a key cannot
   keep a replaced object.
11. **Routing goes where it points.** Decision 4 kept a press inside a secondary segment, and on
   hardware that meant it did nothing: a pinned region keeps its own reading position and never
   passes it to the application. The reader asked for a press to act as a click, which also
   answers moving into the Outlook reading pane at the top of the message rather than where they
   had panned to. It is done for every monitored segment, a reader's pin or a layout's, in
   `monitorRouting`, reached through `container.segmentRouter` so the container stays ignorant
   of monitors. A single press: the window comes forward, and after `FOREGROUND_SETTLE_MS`
   Windows is asked what is at the point, the start of the text pressed or the middle of the
   object. The click goes ahead only if that is the target's window, since a pin may be covered
   or scrolled out of view and a blind click lands on whatever is on top. Otherwise the object
   is focused and, after `CARET_SETTLE_MS` so the application's own placing on focus comes first,
   its caret or browse mode cursor is put where the reader pressed. Two presses, as the layout
   overview has, was considered and not chosen. The focus segment routes as it always has.

## Review of ca7f5fc

Six faults found by review, each reproduced by a failing test before it was fixed. The tests
are `TestAppLayoutReviewCa7f5fc`, `TestTheFocusDisplayCommandMovesALayout` and
`TestOneFreeRowOnANarrowDisplay`.

1. **A pin or claim made while a layout waited was covered.** The room was taken when the focus
   arrived and used when it settled. Now only the intent waits; `_placeFor` works the room out
   and asks the app module again when the focus settles.
2. **The focus display command moved nothing while a layout was up.** The room took its focus
   rectangle from the container, which with a layout up is the layout's own focus segment. It
   now comes from `configuredFocusRect`, the reader's arrangement under the claims, and a
   rebuild that changes it places the layout again.
3. **An unreadable replacement left a segment stuck.** The old monitor was let go and the new
   object recorded before the new monitor was built, so a vanished element left no monitor and
   an answer the next pass thought was shown. `_showIn` is now all or nothing, and its failures
   count towards `MAX_FAILURES` as a raising callable's do.
4. **A layout drew beside speech.** `_showIn` refreshed directly, outside the guard the pin
   timer has. Beside speech it now empties the segment and keeps the monitor, and the timer
   draws it once braille is back.
5. **One free row on a narrower display was offered at the composite's width.** `onHardware`
   clips a one row area to its display before it is offered or divided.
6. **Asking the callables ran outside the refresh allowance.** They now share the timer's
   deadline, and a pass that runs out carries on from where it stopped.

## Review of bdd4c49

Four more, each reproduced first, in `TestAppLayoutReviewBdd4c49`. Three were one fault: the
layout was refreshed by three paths, the pin timer, the pass while the reader types and a
change an app module reported, and only one of them was budgeted and fair. They now share one
scheduler, `AppLayoutHost.refreshInTurn`.

1. **A failed redraw left a segment with no monitor.** A replacement that built but failed to
   draw had already let the old monitor go while the old object stayed recorded, so a return
   to the old object was taken as already shown. `_showIn` now puts the old monitor back and
   draws it again, leaving the record, the monitor and the display in agreement.
2. **The pass while typing always started at the first segment,** and the timer ignored its
   unfinished answer. `refreshInTurn` keeps a rotation, and `patches._layoutUnfinished` brings
   an unfinished pass back on the next cycle.
3. **A reported change was read without a budget, and a replacement was read twice.** A change
   now marks the layout dirty and asks for a prompt pass of the same scheduler, one slice at a
   time. A segment the callables' phase has just drawn is not read again in that pass.
4. **A slice the callables spent could still start a read.** The deadline is checked before
   every item of both phases, so a slice that ran out leaves the reads for the next pass.

## Review of 64197a4

Five faults found by review, each reproduced by the reviewer's failing test before it was fixed.
The tests are in `test_review64197a4.py`.

1. **The click was checked against the window, not the control.** In a browser, and in any
   application drawing its own controls, every control shares one window, so a matching window
   said nothing about which control the click would reach. `isTheTargetsAt` still checks the
   window first, then asks accessibility what is at the point (`objectFromPoint`) and clicks
   only if that is the object expected, the one the text starts in or the item pressed, or
   something inside it within `HIT_TEST_DEPTH` parents. A container around it is not enough,
   and objects that cannot be compared count as different. Anything unverified takes the focus
   and caret path, which clicks nothing.
2. **A sliced pass never finished, and a slow layout starved the pins.** Each continuation began
   another full rotation, so with every read slower than the slice no pass ended, and a single
   change notice re-armed its 1 ms continuation for ever. Both schedulers now settle what a pass
   reads when it starts (`_toRead` in the host, `_pinsToRead` in the plugin) and end once each
   has had a turn. The plugin alternates whole passes, the layout's then the pins', so a slow
   layout pane can no longer take every slice before the reader's clock is reached.
3. **A key changed on the same Python object went unseen.** The key was read off the old object
   when comparing, and an application returning one object with its key changed shows the new
   key on both sides. The host now keeps the key each object had when it was shown, in
   `Shown.keys`, and `sameAnswer` compares that with the answer's key before the objects.
4. **Text that could not say where it was clicked the middle of its editor.** That puts the caret
   somewhere else in the editor, not where the reader pressed. Text is now clicked only at its
   own point; without one it is gone to by focus and caret. An item is still clicked in its
   middle.
5. **Turning the flow on in a layout asking for it did nothing.** `_applyFlow` returned whenever a
   layout was up. Where the layout's focus segment asked for the flow, the setting is now
   answered there and then, starting or stopping the flow without leaving the layout. A layout
   that did not ask keeps the flow waiting, as before.
