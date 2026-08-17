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

**Configuring it.** From Phase 3 the device list is chosen in NVDA menu, Preferences,
Settings, BrlMultiline displays. It can also be set from the Python console, which is how the
Phase 1 and 2 hardware runs were driven and remains the way to set a port:

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

What is lost is narrower than "the command stops working", and the distinction is worth
keeping. Each of those three binds its script through `__gestures` on its own class, so the
binding lives in the member's own `_gestureMap` — and dispatch reaches it, because
`gestures.scriptForMember` hands the gesture to the member. A HandyTech member keeps its
braille input toggle on space with b1, b3 and b4, and on the five other chords it ships with.
What cannot be done is rebinding it, because it is not listed to be rebound.

The mechanism, for whoever reads this next. `_AllGestureMappingsRetriever.__init__` fills the
dialog from two independent passes: `addGlobalMap(braille.handler.display.gestureMap)` at line
814, which sees our live `MemberGestureMap` and so still lists everything the members bind
through their maps; and `addObj(braille.handler.display)` at line 828, which is the one that
breaks. `addObj` walks `obj.__class__.__mro__` for `script_` methods and then reads
`obj._gestureMap` for their default bindings, and the object it is handed is this driver, whose
MRO contains no member. `makeNormalScriptInfo` is why the blast radius is three drivers: it
returns None when a script has no `__doc__`, so every undescribed driver script was already
absent from that dialog.

Not fixed because the honest fixes are both bad: synthesising a class that inherits from the
member drivers would drag in their `__init__` and `display`, and copying script attributes
across would bind them to the wrong object. It wants either an NVDA change — the dialog asking
the display for its scriptable objects rather than assuming there is one — or a considered
design here, and neither belongs in a phase about gesture plumbing.

Accepted as a limitation by the author, who owns a HandyTech display and does not normally
drive it from NVDA. Recorded so the decision does not have to be made twice.

496 tests, one expected failure. The three covering the install window were checked against
the unfixed code and fail there, which is the only way to know a regression test regresses.

**Phase 3, add-on integration — CODE COMPLETE, UNVERIFIED ON HARDWARE.** The device map
becomes a base view with one panel per band and blank masking of dead columns. Settings panel
for choosing members and their order. Success looks like: the secondary display holds a
pinned object while focus moves on the primary — with no new code in `objectMonitor.py`,
`documentLines.py` or `panels.py`, because all of it is rectangle based already.

That last prediction held. Nothing in `objectMonitor.py`, `documentLines.py`, `panels.py`,
`container.py`, `segments.py` or `routing.py` was touched. What landed is one new pure
function in `layout.py`, one new module, one new function in `views.py`, four lines in the
plugin, and the settings work.

- **`devices.py`** reads the device map off `braille.handler.display` and gives back
  `DeviceInfo` records. It is the only place above the driver that knows the composite is a
  composite. It deliberately does not import the driver: that would pull `hwIo`, `inputCore`
  and the driver base class into the view layer for the sake of a string and three integers,
  and would make the whole view layer fail to import if the driver ever failed to.
- **`layout.deviceBandRects`** splits each band into the part that reaches hardware and the
  part that does not. Pure, and unit tested against the 9 by 80 case.
- **`views.deviceView`** builds the arrangement: one `SinglePanel` per segment, and a
  `BlankPanel` over each band's dead columns.

### The composite has no segment count of its own

The decision worth recording, because it is not what the settings dialog would have suggested.

A segment may not straddle two displays: half its cells would be on hardware the other half
is not on, which is the dead column trap wearing a different hat. So the composite is
*always* divided at the band boundaries, and its own `segmentsEnabled`, `segmentCount` and
`segmentSizes` are simply unused. Each display is then divided within its own band.

By what? By the settings that display already had. `DeviceInfo.displayKey` composes
`driverName_RxC` from the member's own geometry, which is exactly the key
`bmConfig.getDisplayKey` composes when NVDA is driving that display on its own. A Monarch the
user divided into three segments stays three segments as a band of a composite, with nothing
to configure and nothing to explain. This is the "maintain the defaults NVDA already has"
requirement applied to the add-on's own settings, and it fell out of the existing key scheme
rather than needing a new one.

What does belong to the composite is what there is only one of: which segment follows the
focus (counted across every display), the panning direction, and whether the free segments
show the document lines.

Segment keys are `device.<driverName>.<n>` rather than the configured view's `display.<n>`.
Named after the hardware, so a pin on the secondary survives a settings change on the primary.
The two key schemes are deliberately not interchangeable: a pin does not carry from a
composite to a single display, and it should not, because those cells have moved to different
hardware.

### The settings

Two categories now, because they answer different questions.

**BrlMultiline** arranges the display connected now. When that display is a composite it
grows a "Segment settings for" chooser listing the displays behind it, and the division
controls under it apply to whichever is chosen. Edits to a display that is not being shown are
held in the panel rather than written as the user switches, so cancelling the dialog still
cancels everything, and a layout that does not fit is reported against the display it was
typed for — with that display selected, so the user is looking at what the message is about.

**BrlMultiline displays** chooses which physical displays are combined and in what order,
which is a standing arrangement rather than a property of what is connected. It is therefore
editable whether or not the composite is in use — which it has to be, since the composite
cannot be selected in NVDA's braille settings until it has members to open. Every driver is
offered, including those whose hardware is switched off, for the same reason. No port is
offered: members are detected afresh, per `DEFAULT_PORT`. Changing the list while the
composite is live reopens it through `wx.CallAfter`, since the members are opened when the
driver is constructed and nothing happens until it is constructed again.

Both panels are unit tested without wx, by building them with `object.__new__` and standing
recording objects in for the controls. That reaches everything between the controls and the
configuration, which is where a bug would be: writing one display's layout into another's
section is exactly the failure this design makes possible and the tests make loud.

### Review found five things, and one of them was the guarantee itself

- **The dead column mask could be evicted.** `deviceView` claims the dead columns with a
  `BlankPanel`, but a blank panel is an ordinary panel, and `SegmentView.withPanel` evicts
  every panel a new claim intersects. A claim spanning the full width of the Monarch's rows
  therefore took the mask with it and put a segment over cells that reach nothing. An
  activated view bypassed `deviceView` altogether. Reproduced with an 8 by 80 claim.

  The correction is that dead cells are an invariant of whatever is finally shown, not a
  property of the base view. `views.validateAgainstHardware` checks every segment of the
  composed view against the dead rectangles, and it is applied at all four places a view can
  be settled: activating a view, activating a panel, revalidating an activated view on
  rebuild, and composing the stored claims. The two answers differ deliberately —
  `activatePanel` and `activateView` raise, because the caller has asked for something
  impossible and should be told; the rebuild loop drops, because a claim that was fine when
  it was made can stop being possible when the displays behind the composite change.

- **Reselecting the live display defeated the install window guard.** `_isDisplayInstalled`
  tested object identity, and NVDA's `sameDisplayReInit` path terminates and reruns `__init__`
  on the very instance `braille.handler.display` already points at. Identity therefore never
  stops being true while the members, the bands and the geometry are all replaced. Reopening
  the composite after editing its member list takes exactly that path, and so does a user
  typing `setDisplayByName` twice, which is how the Phase 1 hardware run went.

  The driver now carries `_installed`, set in `initSettings` — which `_switchDisplay` calls
  last — and cleared at the top of `terminate`. That is the earliest honest answer to "is this
  display in place". The test harness's `build` was calling neither `initSettings` nor
  anything else NVDA does last; it does now, which is what made the guard testable at all.

  One gap remains and is inherent rather than overlooked: between `initSettings` and the
  plugin's deferred rebuild, the driver is current and the container is not. That window
  belongs to every display change, not to this one, and it closes within an event loop turn.

- **Explicit ports were discarded by opening the panel.** The panel offers no port control,
  because members are detected afresh, but it was rebuilding every entry as `DeviceSpec(name)`
  on save — so a port set from the console became automatic detection if the user so much as
  opened the category and pressed OK. The panel now holds the specification each display was
  configured as and reuses it. A value it cannot show is not a value it may quietly discard.

- **A composite with every display undivided skipped focus validation.** The early return was
  right for an ordinary display, whose single segment makes the focus number moot. A composite
  still has one segment per display, because the hardware boundaries are not optional, so the
  number still has to name one. Now gated on there being a single target.

- **The member list was stored per profile.** Since resolved; see the known limits below
  for what base only storage needed.

579 tests, one expected failure. Every fix above was checked against the unfixed code and
fails there — including the four dead column tests, which is the point of the exercise: a
guarantee with no test that fails without it is a comment.

### The hardware run found the one path a view cannot cover

Phases 1 to 3 passed on a Monarch above a Focus 80: geometry, routing on both displays across
rows, the Focus's wiz wheel rebinding live, and multi key modifiers staying scoped to the
display that raised them. What the run also found was flash messages appearing on the Monarch
rather than beside the focus.

The placement is not a bug and the focus segment was never going to catch them.
`BrailleHandler.message` swaps `handler.buffer` for `handler.messageBuffer`, a plain
`BrailleBuffer` built in the handler's constructor, so while a message is showing the display
container is bypassed entirely and NVDA's own flat buffer drives every cell. A flat buffer
starts at cell 0, and on a composite cell 0 is the top left of the upper display. The focus
segment is a property of `mainBuffer`; a message is never in it.

Behind the placement was the defect. `BrailleBuffer._get_windowBrailleCells` pads every row
to `handler.displayDimensions.numCols`, which is the composite's 80, while the Monarch's rows
have 32 cells that reach hardware — so up to 48 characters of every row were written into
cells that display nothing, and the rest of a long message read as though it had never been
sent. The dead column trap, reappearing through the one path `deviceView` cannot reach,
because that path replaces the buffer the view built.

`messages.MessageBuffer` answers both by doing to the message buffer what `segments` does to
the main one: a `RectHandlerProxy` so NVDA's own wrapping lays the message out at the
segment's width, and a composite step that places the result at the segment's position.
Nothing can reach a dead column because nothing is laid out wider than the hardware it is
going to. Which segment is now a setting, `messageSegment`, defaulting to -1 for "follow the
focus" — which is where a message lands on an undivided display and therefore what a user
changing nothing should get.

Two details worth keeping:

- **The object is retargeted, never replaced.** The handler decides whether a message is
  showing by comparing `buffer is messageBuffer`, in six places. Swapping the object those
  comparisons point at while a message was up would leave one that nothing could dismiss.
  `setRect` moves the rectangle and keeps the identity.
- **`routeTo` is translated even though nothing observes it.** NVDA dismisses the message
  immediately after routing into it and `TextRegion.routeTo` does nothing, so the translation
  has no effect today. It is written correctly because the day a message holds something
  routable, an untranslated position acts on a cell the user did not press — the failure this
  driver already met once in cell index rebasing.

This also forced a fidelity fix in the test stubs. NVDA's `BrailleBuffer` is an
`AutoPropertyObject`, and the stub declared its geometry with `@property`. A subclass
overriding `_get_windowBrailleCells` overrides nothing against that, so every test of the
override would have passed while exercising the stub. The stub now defines its geometry in
`_get_` form, and `test_messages.TestStubFidelity` asserts the wiring so it cannot drift back.

608 tests, one expected failure.

### Review found six more things, and the panning hook was the wrong one

None was a P1 and one of them was the design rather than a slip.

**The display behind a panning key was read at the wrong moment.** The first version noted the
display as the gesture passed `inputCore.decide_executeGesture` and read it back at the
scroll. That is one hook too early, and reading `executeGesture` again says why twice over.
It does not run the script; it calls `scriptHandler.queueScript`, so the script runs later on
the main thread while the decider ran on the thread the driver dispatched from — two displays
are two threads, and a pan on each could both be decided before either script ran, leaving one
scroll acting on the wrong display and the other on none. And three paths between the decider
and the script abandon the gesture outright: the capture function at line 625, which is input
help and which returns before the script; sleep mode at line 573; a modifier at line 633. Each
left a display recorded that no scroll would collect, waiting for the next scroll with no key
press behind it — automatic scroll — to act on.

`panning` now wraps `globalCommands.GlobalCommands.script_braille_scrollForward` and
`script_braille_scrollBack` instead, which is where the gesture and the call to
`BrailleHandler.scrollForward` are in the same stack frame. The record and its only reader are
one synchronous call on one thread, so the module global needs no locking and no consuming:
set on entry, restored on exit. Wrapping the two commands also drops the `inputCore` dependency
and the `isinstance` filter, since nothing but those two commands can set anything now.
`functools.wraps` keeps the name, documentation and script attributes, so input help and the
Input Gestures dialog still describe NVDA's own commands.

The tests are what this bought. `_stubs.InputManager` reproduces the order — decide, capture,
queue — and `runQueued` stands in for the main thread's queue, so "both displays pressed before
either command runs" is a test rather than a paragraph. Reinstating the old mechanism fails
five of them: the input help leak, the cancelled gesture leak, and all three orderings.

**A message handed back mid flash left braille stranded.** `_restoreMessageBuffer` cleared
NVDA's message buffer and made it the handler's buffer, which is not what dismissing a message
is. `_dismissMessage` clears it, returns the display to `mainBuffer`, stops `_messageCallLater`
and notifies `_post_dismissBrailleMessage`; doing part of it left the display on an emptied
buffer belonging to nobody, until the next key press moved it on — and with messages configured
to be shown indefinitely there is no next anything. The add-on now calls NVDA's own dismissal
while its buffer is still installed, and the same applies where NVDA has rebuilt its message
buffer underneath us. The old test asserted `buffer is messageBuffer` afterwards, which
encoded the wrong contract; it now asserts both buffers, the display showing the main one, and
the timeout stopped. The timeout is the part that is not merely untidy: left running it fires
into `_dismissMessage`, whose precondition is that a message is showing, and clears the main
buffer some seconds later.

**Moving a live message did not redraw it.** `setRect` laid the message out again but never
wrote it, and nothing else would: a message moves because the layout was rebuilt, and the
rebuild redraws through `handleGainFocus`, which deliberately leaves the display alone while a
message is showing. So the reader went on feeling the message where it was while routing and
the cursor had moved to where it now is. One `updateDisplay()`, which asks whether this buffer
is the one being shown and so costs nothing between messages.

**Segments were allowed to straddle two displays.** `validateAgainstHardware` only looked for
dead columns, so a 32 cell segment covering the Monarch's last row and the Focus's row passed —
every cell of it live — and an all equal width composite returned before checking anything. It
now requires each segment to be contained in exactly one live device rectangle, which subsumes
the dead column rule, and `segmentsForDevice` uses containment rather than the segment's first
row. Nothing is lost by a straddling segment; what breaks is everything that treats a segment
as belonging to a display, which after Phase 3 is panning, pinning and every display relative
command. `segmentsForDevice` answering "no display" is deliberate for the single segment the
plugin falls back to when a view cannot be shown: it covers the whole composite, so it is on
no one display, and native panning is left to NVDA, which does the right thing with one
segment.

**The base only migration only looked at the active profile stack.** `config.conf.profiles` is
what is in force now, so a device list saved under an application profile that happened not to
be triggered was invisible — and once reads went to base, permanently so. It now also walks
`listProfiles()` and loads each one. Profiles agreeing is one answer; profiles disagreeing
migrates nothing and says so, naming them, because there is no telling which the user meant and
a composite of displays nobody chose is worse than choosing again.

**An older composite's reversed panning was orphaned.** Reversal moved from the composite to
each physical display, and the old value is not read anywhere, so an existing user's panning
would silently swap back to the default — the kind of thing a reader blames on themselves for a
while before blaming the software. `bmConfig.migrateReverseScrollButtons` copies a stored
composite value onto every member, once, recording `reverseScrollBtnsMigrated` against the
composite. Recorded rather than inferred: there is no telling a member's stored `False` from a
member that has never been asked, so a migration that ran on every rebuild would keep putting
the composite's old answer back over the setting the user had just made.

One stub had to become more faithful for the last of those. `_stubs.DisplaysSection` resolved
every display key to the one `CONFIG` dictionary, on the grounds that tests care about settings
rather than keys — true until a change is entirely about which key a value lands under. Each
display now gets a section of its own that falls back to `CONFIG` for anything unwritten, so
the ordinary test still sets one value and has every display see it.

728 tests, one expected failure. Every one of the six was confirmed to regress when its fix
was removed.


### A second pass, and the emergency path nobody had looked at

Four more, two of them worth the round on their own.

**The fallback was the last place still ignoring the hardware.** When `DisplayContainer`
construction fails, the plugin falls back to a simple arrangement — and that arrangement was
`singleSegmentView`, one segment across the whole composite. Which is to say: the containment
rule was enforced everywhere except the path taken when something has already gone wrong, and
on a Monarch above a Focus that segment covers 48 dead columns per row. An emergency is a poor
moment to start losing text silently. `views.deviceFallbackView` now gives a composite one
segment per physical display with the dead columns masked, and reads no settings while doing
it, since a setting is the likeliest reason the other view could not be built. Its keys are the
ones `deviceView` gives a display's first segment, so a pin can survive the fall back.
`singleSegmentView` stays for ordinary displays and for the case where the bands do not
describe the display at all — there being nothing then to mask them with.

The new test forces the first container to fail by patching `DisplayContainer.__init__` rather
than the name in the plugin module: the plugin finds its own container with
`isinstance(buffer, DisplayContainer)`, so the class has to stay the class.

**`isSet` does tell a stored `False` from a default, and I said it did not.**
`AggregatedSection.isSet` walks the profiles and reports whether the key is stored in any of
them, which is exactly the distinction the reversal migration claimed was unavailable. So the
composite's old value is now copied only onto members with no setting of their own, and a
member the user answered for by hand — including with `False` — is left alone. The marker also
moved to after the writes: an attempt that fell over part way is better retried than remembered
as finished.

**Removing the panning wrappers could undo another add-on.** `globalCommands.GlobalCommands` is
a class attribute anyone can reach, and restoring NVDA's own over whatever is there now throws
away a replacement made after ours. Each command now goes back only if it is still the wrapper
this module installed. The cost of leaving one alone is that the wrapper stays reachable inside
someone else's chain, where it sets a global nothing reads any more.

`patches` had the same property against `BrailleHandler` and was swept the same way, with one
addition it needs and `panning` does not: its replacements delegate to the method they replaced,
so a method left in place keeps its entry in `_originals` — both because it is still being
delegated to, and because that entry is what stops a later install putting a second copy on top.
Without that guard, an add-on that *wrapped* one of these methods rather than replacing it would
leave the two calling each other until the stack ran out.

**A replaced message buffer was dismissed but not remembered.** The path exists for a message
buffer that is not ours being found in place, and the plugin took it down without noting that
it, rather than the buffer saved at plugin startup, is what termination owes. It is saved now.
The comment and test explaining that path said NVDA rebuilds `messageBuffer` when braille is
reinitialised; it does not. `messageBuffer` is assigned once, at `brailleHandler.py` line 144
in `BrailleHandler.__init__`, so a rebuilt one arrives with a whole new handler. Another add-on
is the realistic occupant, and both now say so.

743 tests, one expected failure.


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

**The member list is base only, and getting there took more than a name in a set.** Resolved;
recorded because the reasoning is not obvious from the code.

`BrlMultilineVirtualDisplay` was an ordinary configuration section, so NVDA wrote it to
whichever profile was last active. Nothing acted on a profile's copy: `_switchDisplay` is not
called when the driver name has not changed, so the composite went on driving the members it
opened while the configuration and the settings panel both described a different set. Three
states, two of them wrong.

Base only is the right contract, and the alternative is worse on its own terms: honouring a
profile's list would mean closing and reopening two Bluetooth displays every time a profile
triggered. The list is which pieces of hardware are wired together, not a per-application
preference. Note the scoping — the `BrlMultiline` section holding the per-display layouts stays
profile-specific, which is a feature.

Three things had to be dealt with, all following from one fact: everything NVDA does for a base
only section happens while the base configuration is being loaded, and this driver registers at
braille init, long afterwards.

- **Registration is too late.** `config.configSections.registerSection` takes `isBaseOnly`, but
  it persists to a YAML file that `_loadCustomSections` reads at the *next* start — so it would
  take effect a session late, and would outlive the add-on, needing an uninstall hook.
  `_makeBaseOnly` adds the name to `ConfigManager.BASE_ONLY_SECTIONS` directly, which the
  upstream docstring sanctions, and re-registers every session instead.
- **The destination may not exist.** `_initBaseConf` creates and validates each base only
  section, looping over those registered at that moment. Ours is not among them, so
  `config.conf[section]` would raise `KeyError`. `_baseSection` creates it in `profiles[0]`
  itself, and going there directly also means the list is written to base whether or not the
  registration succeeded — which matters on the first read of every session, before
  `_makeBaseOnly` has run.
- **Validation will not have run.** Nothing applies `string_list(default=list())`, and configobj
  hands back a *bare string* for a list of exactly one entry that has not been validated. A
  single member composite would have read as one driver name per character. `_readEntries`
  coerces it, tolerates a missing key and a missing section, and reports anything else without
  raising.

That last point is the hardening, and it is deliberate about what still raises. A section that
cannot be read is a plumbing failure and degrades to "no displays configured", which the driver
reports usefully. A list naming one driver twice is the user's own error and still raises,
because the message is worth more than the silence.

`_migrateFromProfiles` copies a list stored under a profile into base, once, before
`_makeBaseOnly` redirects the reads — that ordering is the whole of it, and a test asserts it
directly. It only ever copies up, never overwrites, and leaves a profile's stale copy in place
rather than editing someone's saved profile, reporting it so that a puzzling `.ini` has an
explanation.

Testing it needed a profile aware configuration stub: base only resolution, an active profile
shadowing base, and writes landing in the active profile. Without that the migration could not
be tested at all, and the four states — nothing stored, stored in base, stored in a profile,
stored in both — are exactly where a mistake would cost the user their device list.


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
in dead columns. The base view must mask them, and from Phase 3 it does — but only while the
add-on's global plugin is running. A user who disables the plugin and leaves the virtual
display selected gets NVDA's own single buffer across the whole rectangle, and the trap back.
Nothing currently notices that, and the driver cannot mask the columns itself: it is handed
cells that have already been laid out.

## Open questions

1. Should the focus segment be pinned to the primary display, or should it be free to sit on
   the secondary? Free is more expressive and costs nothing in code, since the focus segment
   is already named by key. It may be confusing in use. Decide against real use.
2. ~~Should reverse panning stay per virtual display, or become per member?~~ **Settled by
   the hardware run: per member, keyed on the display whose key was pressed.** The proposal
   here — look it up from the device owning the segment being scrolled — is wrong, and the
   run showed why. Reversal is a fact about where the two keys sit on a piece of hardware, so
   pressing the Monarch's panning key wants the Monarch's setting even when the segment that
   moves is on the Focus. Segment ownership is only a proxy, right when the two coincide.

   Getting the pressed display is harder than it looks and both obvious routes fail.
   The thread local that `brlMultilineVirtual.gestures` uses for modifier scoping is empty by
   the time panning reads it, because `InputManager.executeGesture` ends in
   `scriptHandler.queueScript` and the script runs on the main thread while the decider ran on
   the driver's. Noting it in the decider and reading it back at the scroll fails too, for the
   reasons in the review round above. `panning` wraps NVDA's own two panning commands, which
   are the last place the gesture and the scroll are in one call.

   The settings panel had the matching bug: the checkbox sat below the "Segment settings for"
   chooser and saved against the connected display, so it read as though it followed the
   chooser and did not. It is now part of the per display group in both senses.
3. What should happen to content on a device that disappears? The geometry shrinks and the
   container rebuilds, and `_carryOverMonitors` already drops pins whose segment is gone. But
   a display coming back should probably restore what it had, and nothing currently remembers
   that. This is the same gap as the panel redraw problem in
   [architecture.md](architecture.md), and probably wants the same answer.
