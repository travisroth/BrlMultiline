# Virtual braille display driver

Plan for driving several physical braille displays as one, so that a secondary display can
serve as a BrlMultiline segment.

Read [nvda-api-notes.md](nvda-api-notes.md) and [architecture.md](architecture.md) first.
This document assumes the facts established there, and adds new findings from NVDA master
(`C:\code\nvda`, reporting 2026.3.0dev) about driver loading, gesture dispatch and cell
writing. Line numbers are from that tree and will drift.

## Verdict

It is possible. Nothing in NVDA prevents a braille display driver from owning several
sub-drivers, and none of the machinery is private in a way that blocks this.

The reason it works is that NVDA's single display assumption lives almost entirely in
`BrailleHandler`, and `BrailleHandler` talks to hardware through a narrow interface. Read
every use of `self.display` in `brailleHandler.py` and the whole contract is:

- `display.numRows`, `display.numCols`, `display.numCells` — geometry, read in
  `_get_displayDimensions` and `_writeCells`.
- `display.display(cells)` — one flat row major cell array, called from `_writeCells` and
  `_bgThreadExecutor`.
- `display.isThreadSafe`, `display.receivesAckPackets`, `display._awaitingAck`,
  `display.timeout` — write scheduling.
- `display.terminate()`, `display.initSettings()`, `display.name`, `display.description`,
  `display._suppressDisplayClear`.
- `display.gestureMap` and `display._getModifierGestures()`, read from `scriptHandler` and
  `inputCore` rather than from the handler.

Every one of those can be implemented by a driver that owns a list of other drivers. NVDA
continues to believe it has exactly one display, its normal driver loading runs, and the
whole braille code path above the driver is untouched.

Input needs more than delegation, and that is where the real work is. See "Gestures" below.

### Why a driver rather than more patches

The alternative is to leave `handler.display` pointing at the primary hardware, register a
`filter_displayDimensions` handler to enlarge the reported geometry, and patch
`BrailleHandler._writeCells` to split the cell array.

That does not work, and [nvda-api-notes.md](nvda-api-notes.md) already records why:
`_normalizeCellArraySize` iterates `range(newNumRows)` where `newNumRows` is the *physical*
display's row count, so rows past the primary display's own are discarded before any patch
of ours could see them. Patching `_writeCells` as well would mean owning the padding,
reshaping and background write logic anyway — which is most of what the driver has to do —
while also having to survive NVDA switching displays underneath us.

The driver is the sanctioned extension point, it is the boundary NVDA already maintains,
and it puts the multi device code where a reader will look for it.

## Facts established by reading NVDA

These are the load bearing ones. Items marked FRAGILE should be re-checked when NVDA moves.

### An add-on can ship a braille display driver

`addonHandler.Addon.addToPackagePath` (`addonHandler/__init__.py:718`) documents
`brailleDisplayDrivers` as one of the four extension packages, and
`addonHandler.packaging.initializeModulePackagePaths` adds every running add-on to all of
them at once.

Ordering matters and is favourable. In `core.py`, `addonHandler.initialize()` is line 763
and `braille.initialize()` is line 829, so add-on driver paths are in place before the
braille handler loads a display. `globalPluginHandler.initialize()` is line 928, *after*
braille — so the driver must not depend on the global plugin having started. It may still
import from `globalPlugins.brlMultiline`, because `initializeModulePackagePaths` extends the
`globalPlugins` package path at line 763 too. Importing a module is not the same as the
plugin running.

`_getDisplayDriver` imports `brailleDisplayDrivers.<name>` and reads `BrailleDisplayDriver`
off it, and `Driver.name` is documented as "must be the original module file name". So the
module file name, the class `name` attribute, the value stored in
`config.conf["braille"]["display"]`, and the gesture source prefix are all the same string.

### Sub-drivers can be constructed directly

`BrailleHandler._setDisplay` does nothing privileged. It resolves a port, calls
`extensionPoints.callWithSupportedKwargs(newDisplay.__init__, port=...)`, then
`newDisplay.initSettings()`. A driver instance is an ordinary object; there is no registry,
no singleton, and no global state claimed at construction beyond the config subsection named
after the driver.

`AutoSettings` keys that subsection on `cls.name`, so two different drivers keep independent
settings. Two instances of the *same* driver would share one subsection. See "Known limits".

### Writing is a single flat array

`BrailleHandler.update` reads `buffer.windowBrailleCells`, pads to `displaySize`, sets
`_cursorPos`, and `_displayWithCursor` ORs the cursor shape into one cell before calling
`_writeCells`. So the cursor is not a separate concept at the driver boundary; it is just a
dot pattern in the array.

`_writeCells` calls `_normalizeCellArraySize(cells, handlerCellCount, handlerNumRows,
displayCellCount, displayNumRows)`. When the virtual driver reports the geometry the handler
is using — which it does, since `_get_displayDimensions` reads it straight off the display —
that call is a no-op and the array arrives unchanged.

Note the write rate. `_updateDisplay` starts a blink timer, and `_blink` calls
`_displayWithCursor` which calls `_writeCells` on every blink. NVDA rewrites the entire
display several times a second whenever the cursor is shown. Fanning that out unfiltered
would put constant traffic on a Bluetooth secondary that is showing something static. Per
device change detection is therefore not an optimisation, it is a requirement.

### Acknowledgement handling is handler global, and members lose their flow control

Revised after the Phase 0 run, which did not reproduce the collision this section originally
predicted. The corrected picture:

`BrailleDisplayDriver._handleAck` (`braille/display/driver.py:353`) cancels
`braille.handler.ackTimerHandle` and calls `braille.handler._writeCellsInBackground()`. There
is one such timer and one such queued write on the handler, which reads as a collision
waiting to happen between two ack driven members.

It is not, and the reason is worth stating precisely because it removes a whole piece of
work. `_awaitingAck` is set to True in exactly one place — `_bgThreadExecutor` line 1115 —
and only ever for `self.display`. Under the virtual driver `self.display` is the virtual
driver, which reports `receivesAckPackets = False`, so the handler's ack timer is never armed
by anything. A member cancelling it is cancelling nothing. The collision is structurally
impossible, not merely unobserved.

Three in tree drivers are ack driven: `freedomScientific`, `handyTech`, and `eurobraille` at
firmware 3.0 and above.

What actually breaks is the opposite of what was predicted. Because nothing sets an ack
driven member's `_awaitingAck`, that member never waits: `freedomScientific.display` takes
its send immediately branch every time, and its `_pendingCells` queue is never used. The
protocol's pacing is simply gone. Whether that matters is untested — the Phase 0 run wrote
single frames by hand, where NVDA writes at cursor blink rate — but it is the risk that
remains.

So the virtual driver still presents itself as `isThreadSafe = True, receivesAckPackets =
False` and still owns its per device queue, but the ack half of that is now **pacing, not
damage control, and can be staged**:

1. First cut: per device queue and write scheduling, sending immediately, with the base class
   `_handleAck` patched only to stop members making a pointless
   `braille.handler._writeCellsInBackground()` call on every acknowledged packet. At blink
   rate that is a lot of wasted APCs, which is reason enough to patch it, but nothing is
   incorrect without it.
2. Only if sustained writing to an ack driven member drops frames: add the per device
   waitable timer and `_awaitingAck` handling, transcribed from `_bgThreadExecutor` and
   `_ackTimeoutResetter` using `winKernel.createWaitableTimer()` and
   `hwIo.bgThread.setWaitableTimer`.

Test for step 2 rather than assuming it: drive an ack driven member at cursor blink rate with
the cursor visible and watch for dropped or stale frames.

### Gestures already arrive without help

A driver dispatches input by constructing a `BrailleDisplayGesture` subclass and calling
`inputCore.manager.executeGesture` from its own read loop. Nothing in that path consults
`braille.handler.display`. A secondary display's keys therefore reach `inputCore`
unmodified, with `br(<its own driver name>):<id>` identifiers, which means **every default
gesture map NVDA already ships keeps working on both displays**. That is the single most
important finding for the "maintain the defaults" requirement.

Three places do consult `braille.handler.display` during dispatch, and each needs an answer:

1. `scriptHandler.getGlobalMapScripts` (line 99) and
   `inputCore._AllGestureMappingsRetriever` (line 812) read
   `braille.handler.display.gestureMap` — one map, from the virtual driver.
2. `scriptHandler._yieldObjectsForFindScript` (line 195) yields `braille.handler.display` if
   it is a `ScriptableObject`, which is how drivers such as `freedomScientific` provide their
   own scripts. `BrailleDisplayGesture._get_scriptableObject` returns the same object.
3. `BrailleDisplayGesture._get_script` calls
   `braille.handler.display._getModifierGestures(self.model)` when resolving a multi key
   gesture into an emulated keyboard modifier combination.

`BrailleDisplayGesture.getDisplayTextForIdentifier` also compares
`braille.handler.display.name` against the identifier source, but only as an optimisation;
the miss path imports the real driver by name and produces the right description. No change
needed.

### Routing keys carry physical cell indexes

`globalCommands.script_braille_routeTo` (line 4277) passes `gesture.cellIndexes[0]` straight
to `braille.handler.routeTo`. `script_braille_reportFormatting` and
`script_braille_selectRange` do the same with `min`/`max`. Those indexes are relative to the
device that produced them, so a press on the secondary display must be translated into the
virtual display's coordinate space before the script runs.

`inputCore.decide_executeGesture` (line 510, applied at line 558) is the hook. It runs before
`gesture.script` is resolved and before any identifier is consumed, and NVDA's own remote
client already uses it to intercept braille gestures (`_remoteClient/session.py:612`), so
this is a supported pattern rather than a trick.

`InputGesture.cachePropertiesByDefault` is `True` (`inputCore.py:104`), so identifiers may
already be cached by the time we mutate. `Getter` (`baseObject.py:24`) defines only
`__get__`, making it a non data descriptor — an instance attribute shadows it. So
`gesture._cellIndexesStr = <string built from the original indexes>` freezes the identifier
before `cellIndexes` is rewritten, and `br(freedomScientific):routing12` keeps meaning the
twelfth cell of the Focus rather than the twelfth cell of the composite. A
`gesture.invalidateCache()` after mutating covers anything already read.

### Drivers that reach back into the handler

Six in tree, found by grepping `braille.handler` under `brailleDisplayDrivers/`:

- `baum.py:418` and `nlseReaderZoomax.py:272` build routing indexes with
  `range(braille.handler.display.numCells)`. Under a virtual driver that range is too long,
  but each is masked by `groupKeysDown & (1 << index)`, so the result is unchanged in
  practice. Harmless, and worth re-checking if either driver is used as a member.
- `albatross/driver.py:579` calls `braille.handler._displayWithCursor()`, `eurobraille` calls
  `handler.update()`, `freedomScientific` calls `handler.message()`. All three go through the
  virtual driver on the way back out, which is correct.

## Design

### Geometry: devices stack into rows

The virtual display is a rectangle, because NVDA's cell array is row major over
`numRows * numCols` and nothing else is expressible.

Devices are stacked vertically, in configured order:

- `numRows` is the sum of the members' `numRows`.
- `numCols` is the largest member's `numCols`.

Each device owns a contiguous band of virtual rows. Within its band, columns `0` to its own
`numCols` are real; anything past that is dead and never written to hardware.

A Monarch (8 by 32) above a Focus 80 (1 by 80) gives a 9 by 80 virtual display, in which the
Monarch's band has 48 dead columns per row. Two Focus 40s give a clean 2 by 40 with none.

Dead columns are a real hazard, not a rounding error: NVDA's ordinary single buffer flow
would write text into cells that no hardware ever receives, and the text would simply
disappear. The add-on already has the tool for this. The driver publishes a device map, and
the base view is built with one panel per device band plus a `BlankPanel` over each band's
dead columns — which is exactly what `BlankPanel` and `remainderRects` exist for, per
[architecture.md](architecture.md). When members have unequal widths, the add-on must build
that view rather than leaving the display as a single segment.

Side by side stacking of two single row displays into one wide row is deliberately not in
version one. Vertical stacking is uniform, and a per device band is a better fit for
segments anyway. A `layout` option can be added later if a use case appears.

### Writing

`display(cells)` receives the flat virtual array and, for each device slot, gathers rows
`rowStart` to `rowStart + numRows`, takes the first `numCols` of each, and concatenates.

Before dispatching, compare against the last array sent to that device and skip if
unchanged. This is what keeps cursor blink traffic off a static secondary.

Dispatch then mirrors the handler: a thread safe member gets a queued write and an APC on
`hwIo.bgThread`; a member that is not thread safe is called directly. Acknowledgements are
handled per device as described above.

A member that raises is dropped from the map rather than taking the whole display down.
NVDA's `handleDisplayUnavailable` would fall back to `noBraille` and lose both displays; per
device degradation means the primary survives a flat secondary. The geometry change is then
handled the same way a hot unplug is.

### Gestures

Four changes, all inside the driver:

1. **Cell index translation.** Register a handler on `inputCore.decide_executeGesture`. For a
   `BrailleDisplayGesture` whose `source` names a member and which has `cellIndexes`: freeze
   `_cellIndexesStr` from the original values, then map each index through
   `virtual = (rowStart + i // dev.numCols) * virtualNumCols + (i % dev.numCols)`, then
   `invalidateCache()`, then return `True`. Multi cell gestures translate index by index, so
   `selectRange` stays correct as long as both ends are on one device, which they must be.

2. **Merged gesture map.** `GlobalGestureMap` has `export()` and `update()`, so the virtual
   driver's `gestureMap` is built at init by updating an empty map from each member class's
   map. Entries are keyed by full `br(driverName):id` identifiers, so the merge is collision
   free across different drivers.

3. **Script delegation.** The virtual driver subclasses `baseObject.ScriptableObject` and
   overrides `getScript(gesture)` to look up the member matching `gesture.source` and, if
   that member is itself a `ScriptableObject`, delegate to it. This is what keeps a driver's
   own scripts working.

4. **Modifier gestures.** Define `_getModifierGestures(model=None)` as an instance method
   chaining every member class's classmethod. Each member filters on its own `br(name)`
   prefix, so the union is safe.

Braille input — typing on a display's braille keyboard — needs nothing. `braille.input`
never consults `handler.display`.

### Configuration

A section of its own, registered by the driver package at import:

```
[BrlMultilineVirtualDisplay]
	devices = list of "driverName" or "driverName|port", in stacking order, top first
```

Its own section rather than a subsection of `BrlMultiline`, for the same timing reason the
driver does not live under `globalPlugins`. `bmConfig.initialize` assigns the whole
`BrlMultiline` specification at once, at plugin start, which would replace anything the
driver had added to it a hundred lines of `core.py` earlier. Two sections, and no ordering
hazard to remember.

The separator is `|` rather than `:` because configobj splits list items on commas and a
port may be a device path full of colons and backslashes.

A driver name with no port means detect afresh, and that is the default. It is deliberately
*not* the port NVDA has stored for that driver: Phase 0 found a configured port outliving
the connection it described, so that the driver reported finding no display while the
hardware was plainly present and `bdDetect` could see it.

Duplicate driver names are refused when the list is read or written, rather than warned
about, because Phase 0 confirmed on hardware that two displays sharing a driver cannot be
told apart.

`bmConfig.getDisplayKey()` composes driver name with geometry, so the virtual display gets
keys such as `brlMultilineVirtual_9x80` and each combination of hardware keeps its own
layout automatically. No change needed there, though a key naming the members would read
better in the settings panel.

The virtual driver's own `getPossiblePorts` returns nothing: NVDA's port control has no
meaning here, and members are chosen in the add-on's settings panel.

`check()` returns `True` so the driver appears in NVDA's braille display list. Selecting it
turns NVDA's own auto detection off, which is inherent — auto detection sets
`handler.display`, and that slot is taken.

### Module layout

The driver is a package under `addon/brailleDisplayDrivers/`, entirely separate from the
global plugin:

- `brlMultilineVirtual/__init__.py` — the driver class. `_getDisplayDriver` imports
  `brailleDisplayDrivers.brlMultilineVirtual` and reads `BrailleDisplayDriver` off it, and a
  package answers that as well as a module does; `dotPad`, `eurobraille` and `albatross` are
  packages already.
- `brlMultilineVirtual/deviceSlot.py` — one member: its driver, its band, its write queue.
- `brlMultilineVirtual/virtualLayout.py` — pure arithmetic and parsing. NVDA free, so it is
  unit tested, in the same spirit as the plugin's `layout.py`.
- `brlMultilineVirtual/vdConfig.py` — the device list.
- `brlMultilineVirtual/ackPatch.py` — keeping member acknowledgements out of the handler.
- `brlMultilineVirtual/handover.py` — taking a display over from NVDA, and giving it back.
- `brlMultilineVirtual/gestures.py` — the decider handler, the live gesture map, script and
  modifier delegation.

**Why not under `globalPlugins/brlMultiline/`**, as this section originally proposed. Add-on
package paths are all registered together at `addonHandler.initialize`, so a driver *can*
import from `globalPlugins`. But importing `globalPlugins.brlMultiline.anything` first runs
that package's `__init__.py`, which is the global plugin: it imports `gui`, `wx`,
`globalPluginHandler` and the settings panel at module level. Braille initialises at
`core.py` line 829 and global plugins at line 928, so that would drag the plugin's whole
import graph into braille startup, a hundred lines early. Even if it works today it is
fragile, and a failure there costs the user braille entirely rather than costing them one
add-on. A self contained driver package has no such coupling, and the plugin can import
`brailleDisplayDrivers.brlMultilineVirtual` freely in the other direction, since by plugin
time braille is long up.

The build zips all of `addon/`, so the new directory needed only an entry in
`buildVars.pythonSources` for translation extraction.

## Order of work

**Phase 0, spike — PASSED, complete. See the results below.** Before anything else, prove on
real hardware that two drivers can be constructed and written to at the same time. This is
the assumption everything else rests on, it is cheap to test, and it is the only one that
cannot be checked by reading source. Everything below assumes it passes.

`spikes/virtualDisplaySpike.py` is the experiment. It is imported from the NVDA Python
console rather than installed; `spikes/` sits outside `addon/`, so the build never sees it.
It constructs a second driver by exactly the path `BrailleHandler._setDisplay` uses, writes
cells to it, records braille gestures through `inputCore.decide_executeGesture`, and puts
the base class `_handleAck` patch of the "Acknowledgement handling" section above in place
so that a secondary cannot cancel the primary's ack timer. Its module docstring is the
runbook; `stop()` undoes everything.

Five questions it is there to answer, and what an answer looks like:

1. Two drivers live at once — `start` returns and `info` reports a sensible cell count.
2. Writing works — `ruler` and `rows` put feelable content on the secondary while the
   primary goes on showing NVDA's own output. `rows` also confirms the flat cell array is
   row major on real hardware, which the stacking model depends on.
3. Input works — `report` shows gestures whose source is the secondary's driver name.
4. No interference — `pokePrimary` still reaches the primary after a spell of pressing keys
   on the secondary. Running once with `start(..., isolateAck=False)` should show the
   collision the plan predicts, which is worth seeing rather than assuming.
5. Same driver twice — `report` prints a thread name and any driver reference found on each
   gesture. If neither varies by device, the "one display per driver name" limit below
   stands as written.

### Phase 0 results, run on 2026-08-13

Primary: Focus 80 (`freedomScientific`, 1 row of 80). Secondary: Monarch over Bluetooth
(`hidBrailleStandard`, 8 rows of 32, 256 cells), started with no port.

Passed:

- **Two drivers live at once.** The Monarch initialised and reported its geometry while the
  Focus remained `braille.handler.display`. This is the assumption the whole design rests
  on, and it holds.
- **Writing works, and is row major.** `rows()` put "row 0" through "row 7" on the Monarch
  in order, top to bottom. The stacking model is correct as written.
- **Full width is driven.** `ruler()` filled all 256 cells.
- **The primary is undisturbed.** NVDA went on writing to the Focus in real time throughout,
  and `pokePrimary` reached it.
- **The secondary's content persists.** Nothing overwrites the Monarch, because NVDA does
  not know it exists.
- **Input arrives from both displays.** 29 gestures recorded, sources `freedomScientific`
  and `hidBrailleStandard`, with no configuration of any kind. The default gesture maps
  needed nothing.
- **Braille keyboard input arrives too.** `dot1` and `dot1+dot2` came through from the
  Monarch, confirming `braille.input` needs no changes.

Confirmed rather than merely predicted:

- **Two displays of one driver cannot be distinguished.** Every gesture from both displays
  arrived on the single thread `hwIo.ioThread.IoThread`, and no gesture carried a reference
  back to the driver instance that made it. Both of the escape routes the plan speculated
  about are closed. The "one display per driver name" limit is a fact, not a caution.

New, and it changes a detail of the Phase 2 design:

- **Routing gesture ids differ by driver.** The Focus emits `routing`; the Monarch emits
  `routerSet1_routerKey`. So the decider handler must key off the presence of `cellIndexes`
  and never off the gesture id. It already does, but the reason is now evidence rather than
  caution.
- **Cell indexes are flat across a multi row display.** The Monarch reported indexes 39, 102
  and 2 for presses spread over its rows — 102 being row 3, column 6 of an 8 by 32 display.
  That is exactly the device local flat index the translation formula takes as input, so the
  formula is right.
- **Out of range routing does not raise.** Index 102 reached `handler.routeTo` against an 80
  cell window and was absorbed quietly. Good for safety, bad for noticing: an untranslated
  index will be silently wrong rather than loud, so Phase 2 needs its own test rather than
  relying on a crash.

The acknowledgement collision did not happen, and the reason matters:

The reverse pairing was run — Monarch primary, Focus secondary, `freedomScientific` being
one of only three ack driven drivers in tree (with `handyTech`, and `eurobraille` at
firmware 3.0 and above). The Monarch went on updating at full speed under sustained key
pressing on the Focus, both with `isolateAck=False` and with the patch installed. Writing to
the Focus worked throughout.

**This is not luck, and it is not a "does not happen" result either.** Reading back:
`_awaitingAck = True` is set in exactly one place, `BrailleHandler._bgThreadExecutor` line
1115, and only ever for `self.display`. So in the spike the Focus's flag was never set by
anyone, its `display()` always took the send immediately branch, and its `_handleAck` reached
`super()._handleAck()` to cancel a handler timer that the Monarch — not being ack driven —
had never armed. Two no-ops in a row. The pairing tested could not have produced a collision.

The correction this forces on the design is in the plan's favour, and is applied in the
"Acknowledgement handling" section above: under the virtual driver the handler's ack timer is
*never* armed, because it is armed only for `handler.display`, which will be the virtual
driver reporting `receivesAckPackets = False`. The collision is structurally impossible
rather than merely unobserved.

What is left is a different and smaller problem, described in that section: an ack driven
member gets no flow control from anyone, because the only code that would set its
`_awaitingAck` is looking at the virtual driver instead. That is untested in the regime that
matters — the spike wrote single frames by hand, where NVDA writes at cursor blink rate.

One incidental finding, which belongs to Phase 4:

- **A device NVDA has just released may not open on the first attempt.** After switching
  NVDA's display from the Focus to the Monarch, `start("freedomScientific")` failed twice
  with "No Freedom Scientific display found" while `devices` showed the hardware plainly
  present and `probe` confirmed the driver had a port to try. The same call succeeded
  unchanged a short time later. So presence in Windows' device enumeration does not imply
  openability, and the reconnect logic in Phase 4 needs retry with backoff rather than a
  single attempt — as does any handover between NVDA owning a display and the virtual driver
  owning it.

**Phase 1, the driver — CODE COMPLETE, UNVERIFIED ON HARDWARE.** Config section, device
slots, geometry, fan out writes with per device change detection, per device write queue,
terminate. Per device ack pacing is staged out of this phase; see the acknowledgement
section for what to build now and what to build only on evidence. Success looks like: both
displays show the halves of one flat buffer, and NVDA is unaware.

What landed, beyond the list above:

- `virtualLayout` also carries `deviceCellIndexToVirtual`, which nothing calls until Phase 2.
  It is geometry, it belongs beside the rest of the geometry, and Phase 0 handed over real
  cell indexes to check it against — leaving a validated formula unwritten would have been
  the odd choice.
- Opening a member retries three times at 0.3 second intervals, from the Phase 0 finding
  that a display NVDA has just released may refuse to open and then open normally moments
  later. This runs on the main thread during braille startup, so the attempt count is
  deliberately small.
- A member that will not open is dropped and the rest are used. Only when *no* member opens
  does the driver raise, which is the contract every display driver has for "there is no
  display here" and leaves NVDA to fall back to no braille and tell the user.
- A member raising while displaying is marked failed and skipped from then on; its band goes
  dark. Rebuilding the geometry around a member that has gone is Phase 4.

Not done, and visible to anyone testing this phase: routing keys on a member route to the
wrong character, members' gesture maps are not merged, and a member that is a
`ScriptableObject` loses its own scripts. All three are Phase 2, and all three are called out
in the driver's own module docstring so that a tester meeting them knows they are expected.

### The display handover problem, and the one patch it needs

Found in review of the first Phase 1 draft, and it was a blocker rather than a polish item.

`BrailleHandler._switchDisplay` constructs the incoming driver *before* terminating the
outgoing one::

	newDisplay = newDisplayClass.__new__(newDisplayClass)
	extensionPoints.callWithSupportedKwargs(newDisplay.__init__, **kwargs)
	if not sameDisplayReInit:
		if oldDisplay:
			oldDisplay.terminate()

For every other driver in NVDA that ordering is harmless, because two different drivers do
not normally want the same hardware. For this one it is the ordinary case: the display the
user is switching away from is very often a member of the virtual display they are switching
to. Switching from a Focus to the virtual display had it try to open the Focus while NVDA
still held it, spend all three retries, and come up without the display the user had been
using a second earlier.

Both directions need answering, and they need different answers:

- **Into the virtual display.** `braille.handler.display` is still the outgoing driver while
  our constructor runs, because `_setDisplay` assigns the new one only afterwards. So the
  constructor finds it, closes it, and replaces its `terminate` with a no-op on that instance
  so NVDA closing it again a moment later does nothing rather than failing against a closed
  device. No patch needed.
- **Out of the virtual display.** Here this driver is the *old* one and the replacement is
  built before we are told anything, so there is no method of ours to intervene in. That
  needs `_switchDisplay` itself, patched while a virtual display is live, to close a
  departing virtual display before building its replacement.

The patch is worth its cost: no extension point covers this, the alternative is telling users
to select "no braille" between every display change, and the patched behaviour is only what
the unpatched code already does a few lines later. It is installed on construction and
removed on termination — and because termination happens *inside* the patched function, the
replacement captures the original before doing anything, so it survives removing itself.

### The `hwIo` finaliser bug, found on hardware

The first Phase 1 hardware run failed, and the cause turned out to be upstream rather than
here. It is written up because anything that closes and reopens a braille display will hit
it, and because the workarounds in this driver make no sense without it.

`hwIo.base.IoBase.close` closes the device handle but never clears `self._file`, and
`IoBase.__del__` calls `close` again. `Bulk.close` and `Hid.close` have the same shape. So
finalising a driver that was already terminated closes its handle a **second** time — and by
then Windows may have handed that handle value to something else.

The log shows it precisely. The Focus was released and reopened successfully, identifying
itself as it should; `handler.display = newDisplay` then dropped the last reference to the
old driver; and the next write failed with "the handle is invalid" nine milliseconds later.
With two members it was the Monarch that died instead, because being opened first after the
release it received the recycled handle value.

Two things are worth stating plainly:

1. **NVDA hits this on its own.** The same log has `setDisplayByName("freedomScientific")`
   failing while the add-on was not involved at all, because `_switchDisplay` terminates and
   reconstructs the same instance when the driver has not changed. This is a defect to report
   upstream, not something to fix here.
2. **This driver provokes it constantly**, because closing one display and opening another is
   its ordinary path rather than a rare event.

Two changes follow, and both are better than what they replace:

- **Members are adopted, not reopened.** When the display NVDA is switching away from is to
  be one of our members, the live instance becomes the member as it stands. Nothing is
  closed, so nothing can be double closed; it is also faster, and it sidesteps the Phase 0
  finding that these devices are slow to reopen. Its `terminate` is replaced by a one-shot
  no-op so that NVDA's post-switch close is swallowed and the real method is then back for
  whoever owns it now.
- **Closed members are kept alive.** `_retiredDrivers` holds every member this driver has
  terminated, so the finaliser never runs while NVDA is up. A deliberate, bounded leak of
  inert objects — a closed driver has had its receive callback cleared and its reads
  cancelled — one per member per display switch. It should be removed once `hwIo` clears its
  handle fields on close.

### Three other things review found

- **The composite now reports `isThreadSafe = False`.** Reporting True had
  `BrailleHandler._writeCells` queue our `display` onto the background I/O thread, from which
  a member that is not thread safe would then have been called — the one thing that flag
  exists to promise will not happen. False puts the fan out on the main thread, where the
  work is slicing an array and comparing it, and leaves each member to be driven as it asked:
  a thread safe one still gets its I/O queued by its slot. Both target displays are thread
  safe so nothing changes for them, but a member list is something the user composes and it
  should not be possible to compose an unsafe one.
- **The virtual driver cannot be its own member.** It passed validation, and opening it would
  have constructed another virtual driver reading the same list, each level retrying every
  member it could not open. Refused where the list is read.
- **Members opened before the layout was built could leak.** They lived in a local until the
  slots existed, so a failure between opening and laying out left them open with nothing
  holding them. Reachable: a `noBraille` member reports zero columns, which the geometry
  refuses. Zero cell members are now dropped and closed individually, and anything opened is
  closed if the layout fails.

A second review round then found the remaining half of that last one, and one compatibility
risk:

- **A member whose *construction* raised was never released.** `_openDriver` discarded the
  instance without terminating it, and because it never returned that instance, no outer
  cleanup could reach it. This is not theoretical: `hidBrailleStandard` and
  `freedomScientific` both assign `self._dev` before their constructors finish, and
  `initSettings` runs entirely after the device is open. Their own handled failure paths do
  close the device, but an unexpected error between opening and returning does not — and the
  next retry would then be competing with the handle the last attempt still held. Each failed
  attempt is now released, best effort, and reported at debug level rather than as an error,
  because a display that is simply not plugged in takes this path three times on every start.
- **Removing a patch could discard a later add-on's.** Both patch modules restored their
  saved method unconditionally, which would erase the wrapper of any add-on that patched the
  same method afterwards. Each now restores only while it is still the outermost patch, and
  otherwise stays in the chain and stands down — still called, still delegating, doing
  nothing of its own. That needed the "installed" and "active" states to come apart, which is
  what the `_active` flag in each module is.

### Test coverage

`test_virtualDriver.py` covers the driver class, `vdConfig` and `ackPatch` against recording
member drivers, which is what review asked for and what would have caught the cleanup and
self-reference cases. The handover tests are the ones worth knowing about: the stub handler
reproduces NVDA's construct-before-terminate ordering rather than the sensible one, so a
member really is still held when the virtual display tries to open it. A stub that quietly
got the ordering right would have tested nothing.

461 tests, one expected failure, which is unchanged from before this work.

Two of those tests exist because a claim in this document was too strong. "Anything opened is
closed" was true only of members that were fully constructed and handed back; a member that
failed part way through was not covered, and the tests for it now distinguish a constructor
failure from an `initSettings` failure, since the two leave the device in different places.

**Configuring it before there is a settings panel.** The panel is Phase 3, so until then the
device list is set from the NVDA Python console:

```
from brailleDisplayDrivers.brlMultilineVirtual import vdConfig
from brailleDisplayDrivers.brlMultilineVirtual.virtualLayout import DeviceSpec
vdConfig.setDevices([DeviceSpec("hidBrailleStandard"), DeviceSpec("freedomScientific")])
```

Then choose "BrlMultiline: several displays as one" in NVDA's braille display list. Order is
top first, so that example puts the Monarch above the Focus, giving 9 rows of 80 with the
Monarch's 48 dead columns per row reported as a warning in the log.

**Phase 2, gestures — CODE COMPLETE, UNVERIFIED ON HARDWARE.** Decider translation, gesture
map, script delegation, modifier gestures. Success looks like: routing on the secondary
routes to the right character, and the primary's own display specific commands still work.

All of it lives in `gestures.py`. Three departures from this plan as written are worth
recording:

- **The gesture map is a live view, not a merge.** `freedomScientific` rewrites its own map
  whenever the user cycles what the wiz wheels do — `gestureMap.add(..., replace=True)` in
  `script_toggleLeftWizWheelAction` — so a copy taken when the display opened would be wrong
  the first time that command was used. `MemberGestureMap` subclasses
  `inputCore.GlobalGestureMap` and overrides only `getScriptsForGesture` and
  `getScriptsForAllGestures`, chaining the members' own maps at lookup time. Subclassing
  rather than duck typing keeps any `isinstance` check honest; identifiers are namespaced by
  driver name, so chaining is the whole of the merge.
- **The identifier is frozen, not recomputed.** `_get__cellIndexesStr` builds `routing103`
  and the like out of `cellIndexes`, so rebasing without freezing would rewrite the
  identifier the user's own gesture map is keyed on — a routing key bound by number would
  stop matching, and would then start matching a *different* cell. Reading it before the
  rebase and assigning it back works because `baseObject.Getter` defines only `__get__`, so
  an instance attribute shadows it. No cache invalidation is needed afterwards: if
  `identifiers` was already built, it was built from the string just frozen.
- **No `isinstance(obj, cls)` gap after all.** The plan worried that a member's gesture map
  entry naming the member's own driver class would fail to resolve, because
  `scriptHandler._getObjScript` tests `isinstance(obj, cls)` against the virtual driver.
  Checked against every driver in tree: none name a driver class as a script owner; they all
  point at `globalCommands.GlobalCommands`. The concern was theoretical and no work was done
  for it.

The stub `baseObject.Getter` in the tests is reproduced exactly rather than approximated with
`property`, because that difference is the whole point: `property` is a data descriptor and
takes precedence over the instance dictionary, so the identifier freeze would raise against
it. A stub using `property` would fail the test it exists for, and a plain attribute would
pass it vacuously.

Review then found four more things, two of which were real hazards rather than tidiness:

- **The decider had to go first.** `extensionPoints.Decider` stops at the first handler
  returning False, and NVDA Remote registers on this same extension point and returns False
  for every braille gesture it forwards. Registered after Remote — which happens whenever a
  Remote session is connected before the virtual display is selected — the translator would
  never run, and a routing key on the second display would be forwarded carrying the first
  display's cell index. `HandlerRegistrar.moveToEnd(handler, last=False)` is NVDA's own way
  of going first, so no ingenuity was required. It is also the right order on its own terms:
  Remote should forward the composite position, because the composite is the display it has
  told the other machine about.
- **Modifier gestures had to be scoped to one member.** Chaining every member looked right,
  since each one's `_getModifierGestures` filters its own map by its own `br(name):` prefix.
  But what it *yields* is stripped of that namespace — bare sets of key names — and
  `BrailleDisplayGesture._get_script` then matches on key names alone. So a Monarch mapping
  and a Focus gesture sharing a key name would combine and execute a keyboard shortcut
  nobody asked for. The source is now noted as the gesture passes through the decider and
  read back during the same synchronous `executeGesture` call. When the source is unknown,
  nothing is yielded rather than everything: a gesture that does nothing beats one that does
  something else.
- **An unrebasable cell now cancels the gesture.** Leaving it unchanged was not the safe
  option it looked like. An out of range index on a narrow member is still a perfectly valid
  index into the composite, so an untouched gesture would route confidently to some other
  display's cell.
- **User bindings to a member's own scripts.** `scriptForMember` now mirrors
  `scriptHandler._getObjScript` with the member standing in for the object NVDA would have
  offered, rather than only calling `getScript`. Built-in member scripts worked already —
  confirmed on hardware — but a user who had bound a key to a member driver's own script had
  written a global map entry whose `isinstance` check fails against the virtual display.
- **Nothing is translated until NVDA has installed the display.** `gestures.install` runs
  from the constructor, but `_setDisplay` assigns `braille.handler.display` only after that
  constructor returns, the outgoing display is terminated and `initSettings` has run. An
  adopted Focus is dispatching input throughout that window, and rebasing its cell indexes
  there would route them against a buffer still sized for the display the user is leaving.
  `_isDisplayInstalled` excludes the window; the outgoing display goes on interpreting its
  own indexes and answering for its own modifiers, which is what NVDA still expects of it.

### A Phase 2 limitation, stated rather than fixed

**A member's own scripts do not appear in the Input Gestures dialog** while the virtual
display is active. `inputCore._AllGestureMappingsRetriever.addObj` enumerates
`obj.__class__.__mro__` for `script_` methods, and the object NVDA offers is the virtual
display, whose MRO contains no member.

Exactly three drivers in tree are affected, and they are the three that give their scripts a
description: `alva` ("Toggles HID keyboard simulation"), `handyTech` ("Toggle braille
input") and `eurobraille` ("Toggle HID keyboard simulation"). `makeNormalScriptInfo` returns
None for a script with no `__doc__`, so everything else was already absent from that dialog.
In particular the Focus's wiz wheel toggles have no description and never appeared there,
with or without a virtual display — they are the wrong example to reach for, however
naturally they come to mind.

What *does* still appear is everything reachable through the members' gesture maps, because
`addGlobalMap` consults `braille.handler.display.gestureMap` and that is now a live view over
the members. That covers the ordinary case — panning, routing, and every global command a
display binds by default.

Not fixed because the honest fixes are both bad: synthesising a class that inherits from the
member drivers would drag in their `__init__` and `display`, and copying script attributes
across would bind them to the wrong object. It wants either an NVDA change or a considered
design, and neither belongs in a phase about gesture plumbing.

496 tests, one expected failure. The three covering the install window were checked against
the unfixed code and fail there, which is the only way to know a regression test regresses.

**Phase 3, add-on integration.** The device map becomes a base view with one panel per band
and blank masking of dead columns. Settings panel for choosing members and their order.
Success looks like: the secondary display holds a pinned object while focus moves on the
primary — with no new code in `objectMonitor.py`, `documentLines.py` or `panels.py`, because
all of it is rectangle based already.

**Phase 4, resilience.** Per device failure, a reconnect poll using
`bdDetect.getConnectedUsbDevicesForDriver` and `getPossibleBluetoothDevicesForDriver`, and a
geometry change path: `braille.handler.invalidateCache()` clears the cached
`displayDimensions`, `displaySize` and `enabled` properties, `displaySizeChanged` fires from
`_get_displayDimensions` on its own, and the plugin rebuilds the container.

**Phase 5, polish.** Expose member driver settings, which NVDA's braille settings dialog
cannot reach once the virtual driver is selected. Documentation and translations.

Phases 1 and 2 are the whole feasibility question. Phase 3 is expected to be small precisely
because of the work already done on panels.

## Testing

`geometry.py` is pure and carries the arithmetic most likely to be wrong: the slicing of a
virtual array into per device arrays, and the device to virtual cell index translation.
Unit test both directly, including the unequal width case and a device whose band is more
than one row.

A recording member driver — a driver that accepts cells and stores them — lets the whole
stack be exercised without hardware, including a two device virtual display running against
one real display and one recorder. Worth building early; it is a few dozen lines and it makes
Phase 1 testable before Phase 0's hardware is at hand.

Hardware matrix: Monarch alone, Focus alone, Monarch above Focus, Focus above Monarch, and
each with the other unplugged mid session.

## Known limits, to be stated rather than discovered

**One display per driver name.** Two Focus displays cannot be told apart. Their gestures
carry identical `source` strings, the gesture objects hold no reference to the instance that
made them, and both are dispatched from the shared `hwIo` thread, so neither the identifier
nor thread identity discriminates. They would also share one `config.conf["braille"]
["freedomScientific"]` settings subsection. Version one should validate the configured device
list and refuse duplicates with a clear message.

Both halves of this were confirmed on hardware in Phase 0: every gesture from two different
displays arrived on the one `hwIo.ioThread.IoThread`, and none carried a driver reference.

There is a plausible route out for later: `BrailleDisplayGesture` emits `br(name.model):id`
identifiers *in addition to* the plain ones, so giving each slot a distinct `model` would
disambiguate while leaving default maps working through the unmodelled identifier. Injecting
`model` means subclassing the member driver per slot and reaching into how it builds
gestures, which is driver specific. Not version one.

**Member driver settings are unreachable from NVDA's dialog.**
`gui/settingsDialogs.py:5237` returns `braille.handler.display`, so the braille settings panel
shows the virtual driver's settings. Dot firmness, HID input simulation and the braille input
toggle for each member need the add-on's own panel. The metadata is available —
`AutoSettings.supportedSettings` on each member instance — so this is UI work, not discovery
work.

**Secure desktop and lock screen transitions re-open every device.**
`_onSecureDesktopStateChanged` sets `_suppressDisplayClear` and switches to `noBraille`,
then switches back afterwards. The virtual driver must propagate `_suppressDisplayClear` to
its members, and every member is re-initialised on the main thread on the way back. With two
Bluetooth displays this could be slow enough to notice. Measure it; if it hurts, the answer
is probably to keep members alive across the switch rather than to speed up init.

**Braille viewer and remote braille are untested in combination.** Both hang off
`pre_writeCells`, which now carries the full virtual array and virtual dimensions. The viewer
should simply show more rows. `_remoteClient` sends `displayDimensions.numCols`, which will
be the composite width — plausible but unverified.

**The dead column trap.** Stated above and repeated here because it is the failure that will
look like a bug in something else: with members of unequal width, any arrangement that lets
NVDA flow one buffer across the full virtual rectangle will silently lose the text that lands
in dead columns. The base view must mask them.

## Open questions

1. Should the focus segment be pinned to the primary display, or should it be free to sit on
   the secondary? Free is more expressive and costs nothing in code, since the focus segment
   is already named by key. It may be confusing in use. Decide against real use.
2. Should reverse panning stay per virtual display, or become per member? The setting exists
   because the comfortable panning key differs by hardware, which argues for per member. But
   panning acts on a segment, and a segment belongs to a band, so per member is expressible
   without a new concept: the reversal could be looked up from the device that owns the
   segment being scrolled.
3. What should happen to content on a device that disappears? The geometry shrinks and the
   container rebuilds, and `_carryOverMonitors` already drops pins whose segment is gone. But
   a display coming back should probably restore what it had, and nothing currently remembers
   that. This is the same gap as the panel redraw problem in
   [architecture.md](architecture.md), and probably wants the same answer.
