# Braille viewer and simulated displays

Plan for showing a sighted person, such as a teacher or a tester, what BrlMultiline is putting on
the displays: every row, every segment, every member of a combined display, and the pins of a
drawing. And for a simulated display, so that a tester with no multi row hardware can run the
add-on as if a Monarch, a Focus 80, or both were connected.

Read [nvda-api-notes.md](nvda-api-notes.md), [architecture.md](architecture.md) and
[virtual-display-plan.md](virtual-display-plan.md) first. Line numbers are from NVDA master in
`C:\code\nvda` (2026.3.0dev) and will drift.

Status: design only. Nothing here is built, and the findings below come from reading the code,
not from a run.

## Verdict

1. NVDA's Braille Viewer cannot be extended to do this, and while it is open it breaks a multi
   row display. The add-on needs a viewer of its own.
2. Most of what the viewer needs already exists: NVDA's `pre_writeCells` carries the whole cell
   array, the device map gives each member's band, the container gives each segment's rectangle,
   and NVDA's viewer module has a braille font, hover routing and a routing gesture to borrow.
3. The simulated display is a member of `brlMultilineVirtual`, not a driver of its own in NVDA's
   list and not a filter on NVDA's display size. Choosing the BrlMultiline display with no
   displays configured is simulated mode. A member needs to tell BrlMultiline nothing: the
   add-on already decides everything, Flow included, from the display's geometry and its
   members. A filter would have had to be taught to every subsystem one by one, and still could
   not draw or take part in a combined display. See "Why a member and not a filter".

## What NVDA's viewer does

`brailleViewer/__init__.py` and `brailleViewer/brailleViewerGui.py`.

1. `createBrailleViewerTool` registers two handlers: `_getDisplayDimensions` on
   `braille.extensions.filter_displayDimensions`, and the frame's `updateBrailleDisplayed` on
   `braille.extensions.pre_writeCells`.
2. `_getDisplayDimensions` returns `DisplayDimensions(numRows=1, numCols=displaySize)`, or 40
   cells when there is no display (`brailleViewer.defaultCellCount`). It always returns one row.
3. `BrailleHandler._writeCells` (`braille/brailleHandler.py`, line 597) notifies `pre_writeCells`
   with the cells, the raw text and the handler's cell count, and only then checks whether a
   physical display is there to write to. That is why the viewer works with no display at all.
4. The window is a single line `wx.TextCtrl` of Unicode braille in the FreeMono-FixedBraille font,
   with the raw text in a second one below it.
5. Routing is by hovering the mouse over a cell, which runs `BrailleViewerGesture_RouteTo(index)`
   through `inputCore`. Clicking is avoided on purpose: it would move the focus to the viewer
   window and change what is on the display.

### What that does to BrlMultiline

The one row filter applies to everything that reads `braille.handler.displayDimensions`, and
BrlMultiline reads it to build its view (`__init__.py`, `_displayDimensions`;
`container.py`, `DisplayContainer.__init__`). With a Monarch at 8 rows of 32:

1. Opening the viewer makes the handler report 1 row of 256, and raise `displaySizeChanged`.
2. The plugin answers by building a one row view of 256 cells.
3. `_normalizeCellArraySize` reshapes 1 row of 256 onto the Monarch's 8 rows of 32 by copying
   the first 32 cells to row 0 and blanking rows 1 to 7. See "Simulating a multi row display" in
   [nvda-api-notes.md](nvda-api-notes.md); this is the same truncation in the other direction.

So the reader loses seven rows and most of the content while a teacher has the viewer open.
Expected, not yet seen on hardware. The note in [virtual-display-plan.md](virtual-display-plan.md)
that the viewer "should simply show more rows" is wrong for this reason.

The upstream fix is for NVDA's viewer to stop forcing one row when the display has more. Worth
proposing, but it would still not show segments, members or pins, so it does not replace this
plan.

## The simulated display

### Why a member and not a filter

A filter on `filter_displayDimensions` with no display connected would make the handler report,
say, 8 rows of 32 under the `noBraille` driver. The view and Flow would follow, since they are
built from geometry. Everything that is built from the driver would not:

1. Settings are kept per display under `bmConfig.getDisplayKey`, the driver name plus the
   geometry. `noBraille_8x32` would work, but it is no display a tester means, and it is the
   name several places treat as "nothing here" (`keyLayerCommands.py`, `keyLayerDialog.py`,
   `settingsPanel.py`, `__init__.py` near line 3560).
2. Graphics are found by duck typing the driver (`graphics._isDrawable`). `noBraille` cannot draw.
3. A combined display is built from drivers by name (`brlMultilineVirtual`, `_openDriver`). A
   filter cannot be a member.
4. Key layers and gestures belong to a driver. A filter has no keys.

Each of those would need a special case taught that a simulation is running. A member of the
combined display is all of them at once, through paths that are already tested on hardware.
The plugin sees a display arrive, reads its size, looks up its settings and builds its view,
exactly as it does for a Monarch and a Focus 80 together. Nothing has to be registered with
BrlMultiline.

### Why not a driver of its own

The first version of this plan made the simulator a driver in NVDA's display list, which a
tester would choose instead of the BrlMultiline display. Decided against with the reader,
2 November 2026: the add-on already puts one display in NVDA's list, and a second one that a
reader cannot use is a trap in that list. So the simulator lives inside `brlMultilineVirtual`,
and NVDA's list does not change.

### Simulated mode

`brlMultilineVirtual` with no displays configured is in simulated mode. Today it raises
`RuntimeError` in its constructor when `vdConfig.getDevices` is empty, which NVDA takes as "no
display here". Instead, it drives one simulated Monarch at 8 rows of 32 as its only member, and
says so in the log and once in speech.

Simulated mode is only for an empty list. A list of real displays none of which is here stays as
it is today: the driver fails, and the reconnect poll is not started. Turning a reader's switched
off display into a simulation would hide the fault from the person who has it.

A tester can also list simulated members alongside real ones, through the add-on's combined
display settings, for example a simulated Monarch above a real Focus 80.

### The simulated member

A class in `brlMultilineVirtual/simulated.py`, a `BrailleDisplayDriver` subclass that NVDA never
loads by name, since it is not at the top of a `brailleDisplayDrivers` package. The composite
constructs it directly.

1. It has a reserved driver name, `simulated1`, and a second, `simulated2`. Member names have to
   be unique (`vdConfig.getDevices` refuses one driver twice), and key layers and gestures are
   keyed on them. `vdConfig` refuses the reserved names as real drivers.
2. Its shape is a driver setting: a model to imitate, with a custom size besides. The models to
   start with:
   1. Monarch at 8 rows of 32, drawable at 96 by 40 pins.
   2. Monarch at 10 rows of 32, drawable at 96 by 40 pins.
   3. Focus 80, 1 row of 80.
   4. 40 cells, 1 row, which is NVDA's viewer's default.
   5. Custom rows and columns, through `NumericDriverSetting`, not drawable.
   The setting reaches NVDA's braille settings with no new code: the composite already offers
   every member's settings there, named for the member (`_get_supportedSettings`).
3. Changing the shape sets `numRows` and `numCols` and asks the composite to lay itself out
   again, through a callback the composite gives it. The composite's `_relayout` then raises
   `displaySizeChanged` and `membersChanged`, and the plugin rebuilds. The Monarch driver's own
   `_announceGeometry` does nothing inside a composite, because `handler.display` is the
   composite, not the Monarch; whether a pitch change on a combined Monarch is followed today is
   worth checking while this is built.
4. `display(cells)` keeps the cells and does nothing else. `isThreadSafe` is False, since there is
   no I/O to move off the main thread. It has no `_dev`, so `DeviceSlot.watchForDisconnect`
   already leaves it alone.
5. A drawable model publishes the graphics capability: `graphicsSize`, `newGraphicsBuffer`,
   `setGraphicsOverlay`, `clearGraphicsOverlay` and `lastTouch`. The buffer is the Monarch
   driver's `PinBuffer`, which imports nothing and can be shared. The driver composites overlays
   over the cells as the Monarch driver does, and keeps the resulting pin image for the viewer.
6. It raises its keys as `br(simulated1):<key>` through `inputCore`, as a real member does from
   its read loop, with the Monarch's key names whatever its shape. `gestures.py` already rebases
   a member's routing index onto the composite by its slot. See "Input".

### What changes in `brlMultilineVirtual`

Not a significant change. The composite already treats a member as any object with a driver's
interface in a slot, and every part that matters reads the slot, not NVDA's driver list. The
changes:

1. The constructor: an empty list means the default simulated member, not `RuntimeError`.
2. `_openDriver`: a reserved name constructs the simulated class rather than calling
   `_getDisplayDriver`. `_isPresent` answers True for it without asking `bdDetect`.
3. `vdConfig`: reserve the two names.
4. The relayout callback for a simulated member whose shape changes.
5. The new `simulated.py`.

Already right, with nothing to change: `DeviceSlot` writes, change detection and the guard; the
settings proxies; the gesture map and routing rebase; `_relayout` and `membersChanged`;
`graphics.findSurface`, which looks for a drawable member among the slots; `devices.deviceMap`.

Outside the driver package:

1. The combined display settings (`settingsPanel._addable`) list NVDA's drivers from
   `getDisplayList`. The two simulated members are added to that list, with descriptions, and
   `settingsPanel.displayDescriptions` learns their names for everywhere else that names a member.
2. `keyLayers.defaultLayers` gives a simulated member the Monarch's layers, under its own name.
3. Tests that expect an empty list to raise are changed to expect simulated mode.

One consequence to know about: the add-on keeps settings per display under the driver name and
geometry (`bmConfig.getDisplayKey`), and the composite's name is `brlMultilineVirtual`. A
simulated Monarch therefore shares `brlMultilineVirtual_8x32` with a real Monarch used as the only
member of the combined display. For a tester imitating a reader that is the point; it is written
here so it is not mistaken for a fault.

### Who it is for

Testers and development. Readers meet it only by choosing the BrlMultiline display without
configuring any displays for it, and are told that it is simulated.

## The viewer

A window of the add-on's own, "BrlMultiline Viewer", opened from the add-on's menu and by an
unassigned command. It works with real displays, simulated ones and any mixture.

### What it shows

1. The cells, as a grid of the handler's rows by columns.
2. Where each physical display is, from `devices.deviceMap`: a gap and a label between members,
   and the dead columns a narrower member leaves greyed out. On a single display, nothing extra.
3. Where each segment is, from the live container's segment rectangles: an outline, and on a one
   row display, a mark between the column segments.
4. The raw text under each segment's rectangle, from that segment's own `windowRawText`. The
   container's `windowRawText` joins every segment's text into one string and cannot be laid out
   by row. Contracted braille will misalign the text with the cells now and then, as it does in
   NVDA's viewer.
5. The cursor, where the handler's cursor position puts it.
6. The pins of a drawing, where a display has one up. See "Pins".

### Where the cells come from

`pre_writeCells`, as NVDA's viewer does, but without the one row filter. The cells are the whole
array the handler writes, which on a combined display is the whole composite. The rows and
columns come from `braille.handler.displayDimensions`. The handler writes on every cursor blink,
so the viewer keeps the last frame and redraws only when it differs, on a timer, as NVDA's does.

### Pins

Pins never pass through `pre_writeCells`: the add-on sends them to the driver as an overlay
(`graphics.GraphicsSurface.show` and `hide`). The viewer needs to hear about them.

1. `graphics.py` gets an `extensionPoints.Action` notified from `show` and `hide` with the
   surface, the key, the pin rectangle and the buffer. The viewer composites what it is told over
   that member's band.
2. A simulated display already holds the composited pin image, so for it the viewer can read the
   driver's image directly and the two must agree. The tests check that they do.

The pin grid is drawn as dots at the member's own pitch, `GraphicsSurface.pinsPerRow` and
`pinsPerCol`, so a Monarch at 8 lines shows the blank dot row between braille lines.

### Drawing it

A custom painted `wx.Panel`, not a text control. A text control cannot draw outlines, grey cells,
labels or pins, and NVDA's viewer already works around its sizing (`_calculateBrailleOutputSize`).
Cells are painted in the FreeMono-FixedBraille font that `fonts.importFonts()` installs, so the
viewer looks like NVDA's.

### What is borrowed from NVDA's viewer

1. The font and `fonts.importFonts()`.
2. Hover routing: `TextCellHover` and `CharCellBackgroundColorAnimation`, which take a text
   control and will need a small adapter for a painted panel, or a copy kept in step.
3. `BrailleViewerGesture_RouteTo(index)`. The index is a position on the whole display, and
   `DisplayContainer.routeTo` already turns that into a segment and a position within it, so
   routing across rows, segments and members needs no new routing code.
4. The window behaviour: shown without activating, stays on top, saves its position per monitor
   layout, shows at startup if asked, clears on the lock screen and is blocked on the secure
   desktop (`gui.blockAction.Context.SECURE_MODE`).

### NVDA's own viewer

While BrlMultiline is running on a display with more than one row, opening NVDA's viewer
collapses the layout (see "What that does to BrlMultiline"). The add-on registers on
`brailleViewer.postBrailleViewerToolToggledAction`, and when NVDA's viewer opens on such a
display, says once that it shows one row and points to the BrlMultiline viewer. It does not close
NVDA's viewer or remove its filter: both are NVDA's private state, and a reader on a one row
display may want NVDA's.

## Input

A tester needs to press keys on a simulated display, not only look at it.

1. Routing is hover, as above. On a simulated display the gesture is raised as the simulated
   driver's own routing gesture, not NVDA's viewer gesture, so the display's key layers apply as
   they would on hardware. On a real display it is NVDA's viewer gesture, since the real keys are
   there.
2. Touch on a drawable simulated display is hover over a pin, which sets `lastTouch` as a touch on
   the Monarch does, so the graphics mode's pointing can be tested.
3. The display's other keys come from a keyboard layer. See "The keyboard layer". Buttons on the
   viewer are not used: clicking moves the focus, which is the thing being watched.

### The keyboard layer

Decided with the reader, 2 November 2026. A keyboard layer, "Simulated display", acting for the
simulated display through `actsFor`, so that the layered keys editor is how a tester changes it
and the panning and per display commands know which display the key was for. See "Keyboard
layers can act for a display" in [layered-keys-plan.md](layered-keys-plan.md).

It ships with few bindings:

1. Up and down arrow scroll back and forward, NVDA's `braille_scrollBack` and
   `braille_scrollForward`.
2. Left and right arrow are the Monarch's zoom keys, out and in. They do what the zoom keys do
   in the layer that is on: in a table they turn its pages (`flowPreviousColumns`,
   `flowNextColumns`), with a drawing they zoom (`graphicsZoomOut`, `graphicsZoomIn`), and on
   unwrapped lines they pan them (`flowPanLinesLeft`, `flowPanLinesRight`).

For the second, the arrows press the simulated display's own `zoomOut` and `zoomIn` keys rather
than naming the commands, so the context rules stay in one place: the simulated display has the
Monarch's key names whatever its shape, and ships the Monarch's layers. That needs a layer
target that presses a display's key, where `Target.key` today presses only keyboard keys. If that
target turns out to be more than it is worth, the fallback is a keyboard layer per context
mirroring the Monarch's table, graphics and unwrapped layers, at the cost of a second copy of
those rules.

The layer is turned on and off with the keyboard's layer key, and escape leaves it, as for the
keyboard's other layers. It does not come on by itself, since while it is on the arrows do not
move the caret.

## Order of work

0. Correct the note in [virtual-display-plan.md](virtual-display-plan.md), and add the message
   when NVDA's viewer opens on a multi row display.
1. Simulated mode in `brlMultilineVirtual`, with the one row and multi row models, cells only.
   Unit tests that the plugin builds its view, finds its settings and runs a Flow on it with no
   other help.
2. The viewer: cells, member bands, segment outlines, raw text, cursor, hover routing.
3. Graphics: the drawable models, the action in `graphics.py`, and pins in the viewer.
4. Input: simulated keys and touch, the target that presses a display's key, and the keyboard
   layer.
5. Simulated members in the combined display settings, the second simulated member, and a
   combined display of two simulated members, or a simulated and a real one.

Steps 1 and 2 are each useful alone: the simulator for testing with NVDA's speech and log, the
viewer for a teacher beside a reader with real hardware.

## Open questions

1. ~~Should the simulator be hidden from NVDA's display list behind a setting?~~ **Settled: it
   is not in the list at all.** It is a member of the BrlMultiline display, and simulated mode is
   that display with none configured. See "Why not a driver of its own".
2. Two reserved names for two simulated members follow a rule that exists for real hardware. If
   testers want three, add `simulated3`. Wait for a tester who wants three.
3. ~~Which keyboard keys stand for a simulated display's keys.~~ **Settled: a keyboard layer.**
   See "The keyboard layer".
4. ~~Whether the viewer should show the speech too.~~ **Settled: no.** NVDA's Speech Viewer
   already shows it, beside this one, and the viewer does not duplicate it.
