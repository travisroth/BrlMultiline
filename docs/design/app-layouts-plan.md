# App layouts

Plan for letting an application's app module arrange the display it is shown on: the chat
history of a Teams conversation in the top six rows of a Monarch with the compose box on the
rows below it, or an Outlook message list beside its reading pane, each coming up when the
focus enters the place it is for and going away when the focus leaves.

Status: milestones 1 to 4 are built and unit tested. The contract is in `appLayout.py`, the
lifecycle in `appLayoutHost.py`, and the Teams add-on offers a chat layout. Nothing has been
tried in a running NVDA or on hardware, and the Teams layout's search for the history beside
the compose box is written against the Teams accessibility tree as the add-on already knows it,
not checked against Teams itself.

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
takes the reader out of the compose box.

## Decided

1. The setting is off by default, per display and per profile.
2. The app module is told the free rows and columns, not the displays. A combined display is
   one tall display to it.
3. Pins are never touched. The layout gets only the free area. Setting pins aside is deferred
   until it is needed.
4. Routing in a secondary segment moves within it only.
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
