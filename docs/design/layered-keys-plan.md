# Layered keys

Plan for giving each braille display, and the keyboard, layers of key bindings the reader
turns on and off: the Monarch's arrow keys pan a drawing while its graphics layer is on and
go back to being NVDA's arrow keys when it is off, and none of that touches the Focus 80
sitting beside it.

Read [nvda-api-notes.md](nvda-api-notes.md) and the gesture section of
[virtual-display-plan.md](virtual-display-plan.md) first. This document assumes how NVDA
resolves a gesture to a script and how a member display's keys reach NVDA through
`brlMultilineVirtual`, and it leans on both.

Named "key layers" in code and "layered keys" to the reader, never plain "layers". The word
layer is already taken in [architecture.md](architecture.md), where views, panels and
segments are called layers of the display, and a module called `layers.py` would be read as
belonging to that.

## Verdict

Worth doing, and the hard part is not the dispatch. NVDA already offers a way to decide, per
keypress and before any lookup, which script a gesture runs; the add-on already uses the
same trick to rewrite a gesture's cell indexes in flight. What makes this larger than other
add-ons' layer code is the rest of the requirement:

1. **Nothing is predefined.** Every display names its keys differently and the add-on does
   not know which of them a reader has free, so the bindings are the reader's, and that
   means an editor.
2. **Any NVDA command, not only this add-on's.** Say line, a browse mode command, an
   emulated keyboard key. `bindGesture` cannot do that, which rules out the usual approach.
3. **Per device, at the same time.** Two displays driven as one are two keyboards, each with
   its own layer state.
4. **Context.** The layer that comes up should depend on what is on the display.
5. **Profiles.** A layer set for Excel can differ from the one for a browser.

## Why not `bindGesture` and `clearGestureBindings`

The usual pattern is to call `bindGestures` on the global plugin when a layer comes on and
`clearGestureBindings` when it goes off. It fails three of the requirements here, and it
is worth writing down why so that nobody reaches for it later:

1. **It can only bind the object's own scripts.** `ScriptableObject.bindGesture` looks the
   script up with `getattr(self.__class__, "script_" + name)` and raises `LookupError`
   otherwise. A layer on the plugin cannot reach `globalCommands.GlobalCommands.sayLine`,
   and a reader asked for exactly that.
2. **It loses to the reader's own gesture map.** `scriptHandler._getObjScript` walks the
   global gesture maps before it asks `obj.getScript`, and the braille display driver's own
   map is one of them. A Monarch arrow key that `hidBrailleStandard` binds to review
   navigation is found there first for any object whose class matches, so a binding made
   with `bindGesture` is not guaranteed to run.
3. **`clearGestureBindings` clears everything.** Every default gesture from every
   `@script` decorator on the plugin goes with it and has to be bound again on the way out,
   on every toggle.

And a fourth, specific to this add-on: bindings made that way show up in NVDA's Input
Gestures dialog as if they were permanent while the layer is on, and vanish when it is off.

## The mechanism: choose the script before NVDA looks

`inputCore.InputManager.executeGesture` does this, in order:

1. Asks every handler registered on `inputCore.decide_executeGesture`. Any `False` drops the
   gesture.
2. Reads `gesture.script`, which is an auto property whose getter calls
   `scriptHandler.findScript` and caches the answer on the instance.
3. Sleep mode, say all, speech cancel, the capture function (input help, and the Input
   Gestures dialog when it is waiting for a key), speak command keys.
4. Queues the script, or raises `NoInputGestureAction` so the key goes to Windows.

The `script` getter is a non data descriptor — `baseObject.Getter` defines only `__get__`,
which is the same fact `_rebaseCellIndexes` in the virtual driver relies on — so **an
instance attribute assigned in step 1 shadows it**. A decider that finds a layer binding
assigns `gesture.script` and returns `True`. Everything after that runs exactly as it would
for a key bound in Input Gestures:

1. Input help reports the layer's command by its own description, because
   `_inputHelpCaptor` reads `gesture.script`.
2. Pressing a key twice counts as a repeat, so say line pressed twice spells, provided the
   script assigned is the target's own bound method rather than a wrapper. `executeScript`
   compares `script.__func__`.
3. Sleep mode, speech cancel and say all resume behave as NVDA intends.
4. The reader's gesture map, the display driver's map, and the add-on's own `@script`
   gestures are all bypassed for that one keypress and untouched otherwise. Nothing is
   bound or cleared, so there is nothing to put back.

A key nothing in the layer's chain binds is left alone: the decider returns `True` without
assigning, and NVDA's lookup happens as it always does.

**FRAGILE.** Three things this depends on that are not public API. Re-check them on every
NVDA update:

1. `gesture.script` being settable by assignment before `executeGesture` reads it.
2. The order in `executeGesture`: decider before `gesture.script` before `_captureFunc`.
3. `inputCore.manager._captureFunc`, read to stand aside while a dialog is capturing.

### Safety checks the decider must repeat

`scriptHandler.findScript` does one thing beyond lookup, and assigning the script skips it:
on the Windows lock screen it refuses any script not in `utils.security.getSafeScripts()`.
The decider must apply the same rule, or a layer becomes a way round the lock screen. It is
one line and it gets a test.

It also stands aside, doing nothing at all, when:

1. The gesture is a modifier.
2. A capture function is set and it is not input help. That is NVDA's Input Gestures dialog,
   or this add-on's layer editor, waiting for a key. A layer that answered then would bind
   the wrong thing, and a one shot layer would spend its key on the capture.
3. `watchdog.isAttemptingRecovery`, which `executeGesture` checks before the decider anyway.

## Which device pressed the key

`BrailleDisplayGesture.source` is the driver name. Through the virtual display a member's
gestures arrive carrying their own driver's name, which the virtual display plan confirmed
on hardware, so the decider needs no knowledge of composites at all: `brlMultilineMonarch`
and `freedomScientific` are two sources and have two layer states.

A keyboard gesture has no source; it is the device `keyboard`. Anything else, touch or a
future input type, is ignored.

**Layers are keyed by driver name, not by display key.** Everything else per display in
`bmConfig` is keyed `driver_RxC`, because a layout is a fact about geometry. Which key is
the arrow key is a fact about hardware, and the Monarch's geometry changes with its 8 or 10
row pitch setting. Keyed by display key, switching pitch would lose every layer.

## The model

NVDA free, in `keyLayers.py`, so it unit tests like `layout.py` does.

**A binding** maps one normalized gesture identifier to one of:

1. A script, as NVDA's gesture maps name one: the full module and class name, and the script
   name without `script_`. `globalCommands.GlobalCommands` and `sayLine`. The same form
   `gesture.ini` uses, so anyone who has read one understands the other.
2. An emulated key, `kb:downArrow`, exactly as the Input Gestures dialog's "Emulated system
   keyboard keys" category stores it.
3. Blocked: the key does nothing while the layer is on. For a key that is too easy to hit by
   accident in a mode where it would do harm.

One target per key within a layer. NVDA's global maps allow one gesture to name scripts on
several classes, because they are resolved against whichever object is present; a layer is
the reader saying "this key means this", and a second target would be a conflict the editor
should ask about rather than keep.

**A layer** has:

1. An id, stable, and a name the reader chose.
2. A context, or none. See below.
3. Whether it comes on by itself when its context appears.
4. How long it stays on, one of the two styles below.
5. Exit keys: extra gestures that turn it off, beyond the escape rule below.
6. Optionally a layer it falls through to for keys it does not bind.
7. Its bindings.

**A device** has a default layer, always present and with no context, plus any number of
the reader's own. Only one layer per device is on at a time, but a layer that is on brings
its fall through chain with it, down to the default layer. See below.

### Keys a layer does not bind are transparent

Decided with the reader, 14 September 2026. An unbound key is never a reason to leave a
layer. It is looked up in the next layer down, and the next, all the way to the device's
default layer, and if nothing in the chain binds it, it goes to NVDA untouched and does what
it always does.

That settles braille typing without a special case. A dot chord the graphics layer does not
bind types, or runs its ordinary command, and the layer stays on. The mechanical keyboard
name for this is a transparent key; here every unbound key is one.

### How long a layer stays on

Two styles, per layer:

1. **One shot.** The next key runs what the chain binds, or passes through to NVDA if
   nothing does, and the layer turns off after that one key either way. **The default for
   a layer with no context.** It is the cheapest way to put twenty extra commands behind
   one key: layer key, then the command, and the reader is back where they were without
   having to remember to leave.
2. **Stays on.** Keys run what the chain binds or pass through, and the layer stays on
   until the layer key, the escape rule, or one of its exit keys turns it off. **The default
   for a layer with a context**, because graphics is somewhere the reader stays for a while
   and still reads while they are there.

An earlier draft had "pass through and leave" and "swallow and leave" styles as well. The
first is what transparent keys replace; the second is better served by binding the one
harmful key as blocked than by making every unbound key leave.

### Escape leaves

Whatever the style, pressing escape turns the layer off and the escape goes no further.
Escape means the device's escape, recognised three ways:

1. **The keyboard's escape key.** `kb:escape`, with no modifiers. A rule of its own, found on
   hardware in phase 1: the first draft expected the next rule to cover it, but the escape key
   has no NVDA command, it goes straight to Windows, so there is no ordinary script to find.
2. **The ordinary script is escape.** A key whose normal lookup, run only for keys the chain
   does not bind, finds the emulated `kb:escape`: on a braille display, whatever chord that
   display's gesture map or the reader's own map gives escape. `scriptHandler._makeKbEmulateScript`
   names such a script `script_kb:escape`, which is what the check reads.
3. **Space with z.** Dots 1, 3, 5 and 6 with space is escape on most braille displays, but
   `hidBrailleStandard` maps escape to space with e, so a Monarch would not be caught by the
   first rule. Space with z is added as an exit key on every braille device's new layers,
   and can be removed in Properties like any other exit key.

A layer that binds escape itself takes the key, and says so in the dialog, because the
reader chose that.

**Routing keys never cause an exit** either, under any style. In graphics a routing press
asks what is under the finger. A one shot layer therefore ignores a routing press when
counting its one key, unless the layer binds routing explicitly. Same for the virtual
display's panning keys, since `panning.py` already decides whose segment they move.

### The fall through chain

Every layer's chain ends at its device's default layer. A layer may name one layer to fall
through to before that: a chart layer that binds only the chart's own commands and gets pan
and zoom from the graphics layer is less to set up and cannot drift. That layer may name
another. Cycles are refused when saving, and the default layer falls through to nothing but
NVDA.

The worry with a stack is that the reader has to hold it in their head with nothing to look
at. The dialog answers that: a command whose key comes from further down the chain is shown
with where it came from, "Space dot 1 (from Graphics layer)", and Change on it offers to
override it in this layer rather than editing the other one.

## Context

`keyLayerContexts.py` holds a short fixed list, from most to least specific:

1. **chart** — graphics mode is showing a chart drawing.
2. **picture** — graphics mode is showing a captured picture.
3. **graphics** — graphics mode is on, whatever it shows. Covers the glyph catalogue and the
   test figure.
4. **table** — the focus segment is showing a table the flow recognised, or the browse mode
   caret is in a table cell.
5. none.

Each entry says how to test for it against the plugin. Graphics mode already knows its
source drawing, so the first three are cheap attribute reads.

**The layer key is context aware.** When a device has no layer on, pressing it finds the
most specific context present that the device has a layer for, and turns that on. With
nothing matching, it turns on the default layer. Pressing it with a layer on turns that
layer off. So a reader with a chart on the Monarch and a chart layer defined gets the chart
layer, and the same key on a web page gets the default. A chart with no chart layer of its
own falls back along the list to graphics.

**Auto enable is for modes, not for places.** Graphics mode is a mode: the reader asked for
it with a command, and the layer coming up with it is answering the same request.
`GraphicsMode.enter`, `leave` and `replaceSource` notify an `extensionPoints.Action` the
layer manager registers on. A layer marked auto enable comes on with its context and goes
off with it, and an auto layer is always one that stays on, whatever style it was given.

Contexts are per device, like everything else here. A keyboard layer with the graphics
context and auto enable follows the drawing exactly as the Monarch's does, and the two are
on together, independently.

Tables do not get auto enable, and the reason is already written down in `tableArrows.py`:
a table is somewhere the caret passes through, and keys that change meaning as the caret
crosses a boundary are a mode the reader did not ask for and must track. The table context
is honoured when the reader presses the layer key, and not otherwise.

Nor is a table layer turned off when the caret leaves the table, for the same reason: that would
mean reading the caret on every key. It stays on until the reader turns it off, as a layer they
chose does. Built this way in phase 3.

**A layer goes with its context.** Built 14 September 2026, ahead of the rest of this section,
after the phase 1 hardware run found a graphics layer still on with the drawing gone. However a
layer with a context was turned on, by the layer key or from the list, it turns off when that
context goes. `GraphicsMode` tells the plugin whenever a figure goes up, comes down, is replaced
or is evicted, but not at shutdown, and the plugin ends every layer whose context is no longer
present and says so. Taking the drawing off with its command says both in one message: "Drawing
off, Graphics layer off". A layer for a context that is not followed, tables, is never ended this way,
since its context is not watched going. A hook on the plugin
rather than an extension point, since the plugin is the only listener and owns the mode.

**A reader who turns an auto layer off keeps it off** until the context goes away and comes
back. Turning it back on under them the next time a drawing is redrawn would be the add-on
overruling them.

## Finding the script to run

A binding names a class and a script. The decider has to find a live object of that class
to call it on. It walks the same objects `scriptHandler._yieldObjectsForFindScript` does, in
the same order, and takes the first that is an instance of the class:

1. Global plugins.
2. The focus's app module.
3. The braille display. Through the virtual display, also the member that raised the
   gesture, because a Focus 80's own wiz wheel scripts live on the member and
   `gestures.scriptForMember` already explains why the virtual display fails `isinstance`.
4. Vision enhancement providers.
5. The tree interceptor, subject to NVDA's own rule that its scripts do not run in focus
   mode unless marked to.
6. The focus object, then focus ancestors for scripts that can propagate.
7. `globalCommands.configProfileActivationCommands`, then `globalCommands.commands`.

Written as a copy of that order rather than by calling the private generator, so an
upstream rename breaks a unit test here rather than every layer at runtime. It is short.

**When no object matches**, the command is not available here: a browse mode command bound
in a layer and pressed in Notepad. The key is consumed and the reader is told, naming the
command: "Next heading is not available here." Silence would read as a broken key, which
is the lesson the zoom refusal messages already learned.

An emulated key is run as `scriptHandler._makeKbEmulateScript` runs one, through
`inputCore.manager.emulateGesture`, with the same description so input help says the same
thing.

## Keyboard layers can act for a display

Decided with the reader, 14 September 2026. A keyboard layer can bind any command a display
layer can, including this add-on's graphics commands and a display driver's own. The case
that asked for it: one hand on the tactile area and the other on a numeric keypad, panning
and zooming from the keypad while the finger stays where it is.

Most of that needs nothing, because a script is a script whichever key ran it. Three things
do need care:

1. **Commands that ask which display was pressed.** Several of this add-on's commands read
   `gesture.source`: panning, to know whose direction and whose segment apply; the layer
   key, to know whose layer to toggle. A keypad key has no source, so a keyboard binding may
   name a display it **acts for**, set in the dialog when the key is added. The dispatcher
   records that on the gesture object as a private attribute, because the script runs later
   from NVDA's queue with that same object, and a helper in `keyLayerDispatch` answers
   "which display is this for" from the recorded one or the gesture's own source. Commands
   here that read `source` move to the helper. `source` itself is not assigned on a
   keyboard gesture: keyboard gestures do not have one, and code outside this add-on may
   reasonably take its presence to mean a braille display sent the key.
2. **A display driver's own commands.** Found on the member the binding acts for, the same
   way `gestures.scriptForMember` finds them for a key the member raised. With no display
   named, the first connected member of the right class.
3. **The dialog has to offer them.** NVDA's command gathering asks only
   `braille.handler.display`, which through a composite is the virtual display, so the
   Focus 80's own commands never appear. The keyboard layer's tree adds every connected
   member's commands, each labelled with its display.

With acts for, the layer key bound in a keyboard layer toggles the named display's layer, so
a keypad key can bring up the Monarch's graphics layer without a hand leaving the keypad.

## Commands

Scripts on the plugin, in the BrlMultiline category of Input Gestures, like every other
command here:

1. **Layered keys: toggle the layer for this device.** The layer key. Uses the source of the
   gesture that ran it, so one script bound to a Monarch chord and a keyboard shortcut
   toggles the Monarch's layer from the one and the keyboard's from the other. Context aware.
2. **Layered keys: toggle the layer on the first, second, third display.** Generated per
   display ordinal, as the scrolling commands are, for a keyboard user who wants to toggle a
   display's layer without touching the display.
3. **Layered keys: choose a layer.** A list of this device's layers, for when context is not
   the answer.
4. **Layered keys: report layers.** Which layer is on for each device, and on which
   contexts.
5. **Layered keys: all layers off.**
6. **Layered keys: open the layered keys dialog.**

While a layer is on, a key whose ordinary script is one of these always reaches it. The
decider checks this only for an unbound key, after the layer lookup, so the common path pays
nothing.

**Feedback.** Turning a layer on or off says its name: "Graphics layer on." A one shot layer
turning off after its key is quiet by design, since the command it ran is already speaking,
with an optional low tone in settings. When a layer goes off because its drawing did, that is said once alongside
"Drawing off" rather than as a second message.

## Storage and profiles

In `bmConfig`, where all configuration access lives:

```
"keyLayerDevices": {
	"__many__": {
		"layers": 'string(default="")',
	},
},
```

One section per device, named by driver name or `keyboard`, holding the layer set as JSON.
JSON for the reason `tableLayouts` gives: the keys are gesture identifiers and module paths,
and a nest of `configobj` sections keyed on them is fragile. A section per device rather
than one string for all of them, because a profile overrides single values: an Excel profile
can give the Monarch a different layer set without restating the Focus 80's.

The unit a profile replaces is therefore a device's whole layer set. Merging layers across
profiles key by key was considered and rejected: a reader who opened the dialog in the Excel
profile and saw a mixture of two profiles' bindings would have no way to tell which came from
where.

The stored form carries a version number. Identifiers are stored normalized, through
`inputCore.normalizeGestureIdentifier`. A binding to a class or script that no longer
exists is kept, not dropped, and shown in the editor as unavailable; an add-on that is
disabled for a week should not cost the reader their bindings.

**Saved layers replace the shipped ones whole, so they do not get later improvements.** Decided
with the reader, 14 September 2026, to leave as it is for now. Once a device's layers are saved, its
default layers are not read for it at all, so a change to what ships — a new layer, a new key, a
setting such as a layer coming on by itself — never reaches a reader who saved before it. Found on
the phase 3 hardware run: the Monarch's layers had been saved during phase 2's run, before the
graphics layer came on by itself, and so it did not. Reset to factory defaults brings a device up
to date, at the cost of the reader's own changes; setting the one property by hand is the other
way. The fix, storing only a reader's differences from what ships, is in phase 4's list.

**Which layer is on is not stored.** It is session state. On `post_configProfileSwitch`,
which the plugin already handles, the layer sets are read again; a device whose active layer
still exists by id keeps it on, and one whose layer has gone turns it off and says so.

## The layered keys dialog

Built to look and work like NVDA's Input Gestures dialog, because that is the dialog every
reader already knows for this job. A `gui.settingsDialogs.SettingsDialog`, opened from the
NVDA menu under Preferences beside Input Gestures, from the command above, and from a
button in the BrlMultiline settings panel. The title names the profile being edited, as
NVDA's settings dialog does.

Top to bottom:

1. **Device** combo box. Every member of the composite, or the one display, plus Keyboard,
   plus devices that have saved layers but are not connected, marked as such. A device that
   is not connected can be reviewed and cleared but not given new keys, since there is
   nothing to press.
2. **Layer** combo box, with New, Rename, Delete and Properties buttons beside it.
   Properties opens a small dialog: name, context, comes on by itself, one shot or stays
   on, exit keys, falls through to. The default layer cannot be deleted or renamed.
3. **Filter by** edit, exactly as Input Gestures has it.
4. **Only show commands with keys in this layer** check box. Off by default, so the dialog
   opens looking like the one the reader knows; on, it is the review view.
5. **The tree.** Categories, then commands, then the keys bound to each command in this
   layer. The same categories and the same command names as Input Gestures, gathered the
   same way: `inputCore.manager.getAllGestureMappings` against `gui.mainFrame.prevFocus`, so
   app module and browse mode commands appear when the dialog is opened from there. Plus
   the Emulated system keyboard keys category. Key names formatted as Input Gestures formats
   them, main part then source.
6. **Add, Change, Remove** buttons, the context menu, and Delete on a key, as Input Gestures
   has them. **Reset to factory defaults**, as Input Gestures names it, puts the device's
   shipped layers back; "Clear this layer" empties the one selected.
7. **OK and Cancel.** No Apply, as Input Gestures has none: its accelerator would be Add's.

### Differences from Input Gestures, each for a reason

1. **Capture only takes keys from the device being edited.** A press from another display
   says "That key is on the Focus 80. This layer is for the Monarch" and keeps waiting.
2. **No identifier choice for a display.** A Monarch press offers both
   `br(brlMultilineMonarch)` and `br(hidBrailleStandard)` forms, and Input Gestures asks
   which. In a layer the device is already fixed, so the specific one is stored without
   asking. The keyboard still offers its layout choice, since that choice means something.
3. **A key already bound in this layer asks before moving.** "Space dot 1 is Next heading
   in this layer. Use it for Say line instead?" Input Gestures allows the duplicate because
   a global map resolves by class; a layer does not.
4. **The layer key and exit keys cannot be bound as commands.** Refused with the reason.
5. **Commands not available from here.** A layer may hold a binding to a browse mode command
   while the dialog was opened from the desktop. It appears under an "Unavailable from here"
   category rather than disappearing, so it can still be removed.
6. **Escape on the keyboard stops waiting for a display's key.** Input Gestures takes whatever
   is pressed next, and a reader editing a display layer who cannot reach the display, or who
   changed their mind, would otherwise be stuck. A key from the wrong device says so and says
   escape stops waiting.
7. **"Does nothing in this layer" is a command** in the BrlMultiline category, which is how a
   key is blocked.
8. **A keyboard key for a command that can ask which display it is for asks which display it
   acts for**: this add-on's commands, a braille display driver's, and NVDA's braille commands,
   when a display is connected. "No display" is first. Other commands never ask.
9. **A combined display's members' own commands are listed**, each named with its display,
   because NVDA's gathering sees only the virtual display and not what is behind it.

### A view model, so it can be tested

NVDA's dialog separates `_InputGesturesViewModel` from the wx tree, and that separation is
worth copying rather than the classes themselves, which are private. `keyLayerDialog.py`
holds `LayerEditor`, with no wx in it — categories, commands, the prompt while a key is awaited,
refusals and conflicts, layers and their properties, commit — and the dialog over it, the split
`flowTableDesigner` makes. The editor gets the unit tests; the dialog is built and driven with
real wx by `tests/nvdareal.py --dialog`, and read with NVDA on hardware.

## Default layers

**The add-on ships layers for the Monarch and the keyboard.** Decided with the reader,
14 September 2026, replacing the earlier plan of suggestions offered from Reset and never
applied: BrlMultiline should work on a Monarch out of the box, and the layout can be tuned from
there. A device has its default layers until the reader saves layers of their own, and reset to
factory defaults puts them back. A display this add-on has no layout for starts with an empty
default layer. They are `keyLayers.defaultLayers`.

### The Monarch's keys that are made for this

The Monarch has two d-pads and two keys its own software uses for zoom. The zoom keys reach
NVDA as braille usages 0x220 and 0x221, one past the last usage `hidBrailleStandard` has a
name for, so they arrive as `brailleUsage544` and `brailleUsage545`. The author's own gesture
map already binds them to `flowNextColumns` and `flowPreviousColumns`, which turns a table's
pages. That is the layering case in one pair of keys: outside a layer they turn table pages,
and in the graphics layer they zoom.

**What input help showed, 14 September 2026.** `brailleUsage544` is zoom in, and the author
already has it turning to the next page of columns; `brailleUsage545` is zoom out, turning
back. Both d-pads report the same four identifiers, `dpadUp`, `dpadDown`, `dpadLeft` and
`dpadRight`, and `hidBrailleStandard` binds all four to the emulated arrow keys.

### Telling the two d-pads apart

**They can be, and the Monarch already does.** A raw report run on 14 September 2026, up on
one pad then up on the other through the virtual display:

	report 0x20: 20 00 80 00 ...   usage 0x216  data index 18, collection 3 (link usage 0x20D)
	report 0x20: 20 00 08 00 ...   usage 0x216  data index 14, collection 2 (link usage 0x20E)

Same usage, `BRAILLE_DPAD_UP`, declared twice: once under left controls (0x20D) and once
under right controls (0x20E), each with its own data index. The firmware says which pad is
which. NVDA throws that away: `hidBrailleStandard.InputGesture` names a key from its usage
alone and only puts the collection into routing key names. JAWS telling them apart is
therefore no mystery.

Still to confirm by hand: that the pad reported under left controls is the one physically on
the left.

### Naming the Monarch's keys properly

**Built, 14 September 2026.** See "Key names" in
[monarch-driver-plan.md](monarch-driver-plan.md) for where it lives; the design below is what
was built. Confirmed by hand that the pad under left controls is the one on the left, and on
hardware the same day: input help reported `leftDpadUp` and `rightDpadUp`, both still bound to
the up arrow, and `zoomIn` and `zoomOut` still paging table columns.

A change to `brlMultilineMonarch`, independent of layers and worth landing first, because
Input Gestures benefits from it straight away: the two pads can be bound separately there
today, with no layer involved.

1. **When the device opens**, next to where the driver already builds
   `_inputButtonCapsByDataIndex`, find every braille page usage that is declared in more than
   one link collection and note a side for each of its data indices from the collection's
   link usage: left controls 0x20D, right controls 0x20E, top controls 0x20F, face controls
   0x20C. Worked out from the descriptor rather than hard coded to the d-pad, so a joystick
   or rocker declared twice gets the same treatment, and a key declared once is never renamed.
2. **Dots and spaces are never renamed** (usages 0x201 to 0x20B), whatever the descriptor
   says, because braille input identifiers and every chord in NVDA's map depend on them.
   Routing keys are left alone too, since they already carry their collection.
3. **Usages 0x220 and 0x221 are named** `zoomIn` and `zoomOut`.
4. **In `InputGesture`**, a key with a side is named `leftDpadUp` or `rightDpadUp`, and a
   gesture containing any renamed key gets a named id alongside the plain one NVDA would
   have built. Identifiers are offered most specific first:
   1. `br(brlMultilineMonarch):rightDpadUp`
   2. `br(brlMultilineMonarch):dpadUp`
   3. `br(hidBrailleStandard):dpadUp`

   The standard driver never produces a side name, so no `hidBrailleStandard` form of one is
   offered. A chord gets the same treatment, since a gesture's id is its keys joined:
   `space+rightDpadUp` first, `space+dpadUp` after it.

**Nothing that works today stops working.** `scriptHandler.getGlobalMapScripts` searches the
user's map with every identifier, then the locale map, then the driver's, so:

1. Both pads stay the arrow keys through `hidBrailleStandard`'s `dpadUp` entries, until the
   reader binds a side.
2. A binding the reader makes to `rightDpadUp` is found before the driver's `dpadUp`,
   because the user's map is searched first.
3. Existing bindings to `dpadUp`, or to `brailleUsage544` like the author's column paging,
   keep matching.

Input Gestures' capture offers the choice of identifiers when a pad is pressed, as it already
does for the `hidBrailleStandard` alias, so a reader chooses "this pad" or "either pad". The
layer editor, which does not ask, stores the most specific.

Unit tests, in `test_monarchDriver.py` with a descriptor stub declaring the d-pad twice:
each side names correctly; a usage declared once is not renamed; dots, spaces and routing are
untouched; every old identifier form is still offered, in order; and the zoom keys have
their names and keep their numbers.

### What ships

1. **Monarch, graphics.** The left d-pad pans in four directions. Zoom in (`brailleUsage544`)
   magnifies and zoom out (`brailleUsage545`) shrinks. The right d-pad is left transparent, so it
   is still the arrow keys while a drawing is up. Space with z leaves. Graphics mode's existing
   chords stay where they are. **Comes on by itself** with a drawing, since phase 3: putting a
   drawing up is already asking for it, which is what the reader said at the start.

   There is no centre press to report the drawing with. The Monarch's d-pads have none:
   pressing the middle gives two directions at once, such as `leftDpadLeft+leftDpadUp`, and
   which two depends on where the finger lands. It is not a key and should not be offered as one.
2. **Monarch, default layer.** One shot, and it reads: the layer key, then up on the left d-pad
   says the line, down says all, left reports the focus, right reports the window title.
3. **Keyboard.** A one shot default layer with keypad 5 saying the line, and a graphics layer in
   which the keypad pans and zooms acting for the Monarch.
4. **Monarch, table.** Shipped in phase 3. Stays on, from the layer key only. The left d-pad
   moves by table cell with NVDA's own table navigation, the commands control+alt+arrows run;
   zoom in turns to the next page of columns and zoom out to the previous one, as the author
   already has them bound globally.

### Names for the zoom keys

"brailleUsage544 (Monarch)" in the dialog is a number, not a name. `brlMultilineMonarch`
already offers extra identifiers on its gestures, the `hidBrailleStandard` aliases after its
own, and the same mechanism can offer `zoomIn` for usage 544 and `zoomOut` for usage 545
**first**, with the usage
number forms kept after them. Existing bindings such as the ones in the author's gesture map
keep matching, because lookup tries every identifier a gesture offers; new bindings, and the
dialog, use the name. Confined to the driver package and small, but it changes a driver's
identifiers, so it gets its own test that every old form still matches.

## Module map

1. `keyLayers.py` — the model, parsing, validation, and the pure decision: given a device's
   layer state and a gesture's identifiers, what happens. NVDA free.
2. `keyLayerContexts.py` — the context list and the tests for each.
3. `keyLayerDispatch.py` — the decider, the runtime state per device, finding the live
   script, emulated keys, announcements, profile switches, graphics mode notifications.
4. `keyLayerDialog.py` — the view model and the dialog.
5. `bmConfig.py` — the specification and accessors.
6. `__init__.py` — the commands, the menu item, installing and removing the decider with
   the other patches.
7. `graphicsMode.py` — the change notification.
8. `brlMultilineMonarch` — names for the zoom keys and for each d-pad, landed before phase 0.
9. `panning.py` and the layer key — reading the display through the acts for helper rather
   than `gesture.source`.

## Plan

### Phase 0, the mechanism on hardware

**Done, 14 September 2026, except the lock screen.** See "What the hardware run found" below.

A layer hard coded in `keyLayerDispatch.py`: one of the Monarch's d-pads pans a drawing and
the zoom keys zoom. No model, no storage, no dialog. Answers, on a Monarch and a Focus 80
driven together:

1. Does assigning `gesture.script` in the decider run the target, for a braille key from a
   member and for a keyboard key?
2. Does say line bound in the layer run, and spell on a double press?
3. Does input help report the layer's command?
4. Does the Focus 80 stay entirely unaffected while the Monarch's layer is on?
5. **Answered:** both d-pads report `dpadUp` and the rest to NVDA, but the raw reports
   differ by collection, so the driver can name them. See "Naming the Monarch's keys properly".
6. **Answered:** `brailleUsage544` is zoom in and `brailleUsage545` is zoom out.
7. Does a keypad key bound to pan, acting for the Monarch, pan the Monarch's drawing?
8. Does the lock screen refuse an unsafe layer command?

Exit criterion: all eight answered on hardware, and the answers written here.

#### What phase 0 built

`keyLayerDispatch.py`, 14 September 2026. A decider registered at plugin start that does
nothing while no layer is on, two hard coded layers, and two commands:

1. **Layered keys: toggle the test layer**, bound to space with l and dot 7 on the Monarch
   and NVDA+control+shift+l on the keyboard. It toggles the layer of the device it was pressed
   on.
2. **Layered keys: report which test layers are on**, unbound.

The Monarch's layer: the left d-pad pans, zoom in and zoom out zoom.
The right d-pad is unbound and stays the arrow keys. The keyboard's layer: keypad 8, 2, 4 and 6
pan, keypad plus and minus zoom, all acting for the Monarch, and keypad 5 is say line. Both
spellings of each keypad key are bound, num lock off and on. Space with z leaves the Monarch's
layer, escape leaves the keyboard's, and so does any key whose ordinary command is escape.

Built beyond the hard coding, because phase 1 needs them unchanged: finding the live object
in NVDA's order, including a member display and NVDA's tree interceptor and ancestor rules;
"not available here"; the lock screen rule; standing aside for Input Gestures while keeping
input help; acts for.

**One design point settled while building it.** State changes only in scripts. The decider
runs on the thread the driver dispatched from, the script later on the main thread, and input
help or sleep mode can abandon a gesture in between, which is how `panning.py` went wrong when
it recorded things at decision time. The price is that a key pressed before the toggle's script
has run is decided against the old state. Watch for it on hardware; it has not been seen.

Every decision is logged at info, starting `BrlMultiline key layers:`, naming the key, the
command, and the object it ran on. Keys the layer does not bind are logged at debug only.

31 unit tests. `tests/nvdareal.py` checks against the real NVDA that an assigned script
overrides the lookup, that `executeGesture` decides before it reads the script and before it
captures, and that the capture function, input help and lock screen rule are where this reads
them.

#### How to run it

With the Monarch and the Focus 80 combined, and a drawing up so panning has something to move:

1. Press space with l and dot 7 on the Monarch: "Test layer on". Pan with the left d-pad, zoom
   with the zoom keys, and check the right d-pad still moves like arrow keys. (Question 1, a
   member's key.)
2. Press keypad 5 twice quickly on a line of text, with the keyboard's layer on; it should
   spell. (Question 2.)
3. Turn input help on and press the left d-pad: it should name the pan command. (Question 3.)
4. Use the Focus 80's keys, including its panning keys, with the Monarch's layer on; nothing
   should differ. (Question 4.)
5. Press NVDA+control+shift+l on the keyboard, then keypad 8, 2, 4 and 6: the Monarch's drawing
   pans, and the log says "acting for brlMultilineMonarch". (Questions 1 and 7.)
6. With the Monarch's layer on, lock Windows and press the left d-pad on the lock screen. The
   log should say "refused on the lock screen" and nothing should pan. (Question 8.)
7. Space with z, and escape on the keyboard, each turn their own layer off.

#### What the hardware run found

14 September 2026, a Monarch and a Focus 80 combined, NVDA's laptop keyboard layout.

1. **Yes, for both.** The Monarch's left d-pad ran the four pan commands and its zoom keys the
   two zoom commands on the plugin, through the virtual display. The keypad ran the same
   commands. The log named each command and the object it ran on.
2. **Say line runs from the layer.** Keypad 5 ran `GlobalCommands.reportCurrentLine` on NVDA's
   own global commands: a command `bindGesture` could never have reached. The double press was
   not recorded separately; the unit tests hold that the script assigned is the command's own
   bound method, which is what `executeScript` compares to count a repeat. The original test,
   the d-pad centre, turned out not to be a key; see "What ships".
3. **Yes.** Input help named `graphicsPanUp` and the rest for the left d-pad and the keypad,
   and the log shows the decider ran first for each, as the stand aside rule intends.
4. **Yes.** With the Monarch's layer on, the Focus 80's rocker and advance bars ran their usual
   commands, and the right d-pad stayed the up and down arrows.
7. **Yes.** Every keypad pan logged "acting for brlMultilineMonarch" and moved the Monarch's
   drawing.
8. **Not run.** JAWS is set to run on the lock screen on this machine, so NVDA was not there
   to test. The rule is unit tested, and `tests/nvdareal.py` checks it is where it is read
   from. Carried into phase 1's exit criterion.

Also seen: space with z left the Monarch's layer and keyboard escape left the keyboard's, each
leaving the other's layer on; the keyboard's escape arrived as `kb(laptop):escape` and matched
the layout free `kb:escape`, as normalized identifiers should. Nothing was decided against a
stale state, though no key was pressed hard on the heels of a toggle to try.

### Phase 1, layers without an editor

**Done, 14 September 2026, except the lock screen.** See "What the phase 1 hardware run found" below.

The model, storage, dispatch, transparency down to the default layer, both styles, the
escape rule, routing exemptions, acts for, commands, profile switch handling, and the zoom
key names in the Monarch driver. The Monarch and keyboard layers shipped as defaults, so there is
something to use with no editor.

Unit tests for the decision function cover both styles against a key bound in the layer,
bound further down the chain, bound nowhere, a routing key, escape found by its ordinary
script, space with z, a layer that binds escape itself, and the layer key; plus the lock
screen and capture rules, and every old identifier form of the zoom keys still matching.

Exit criterion: a reader can toggle a Monarch layer, pan and zoom a drawing from the Monarch
and from the keypad, type a dot chord without leaving the layer, say line from it, leave it
by the layer key and by space with z, use a one shot layer, and switch profile with a layer
on. And phase 0's lock screen question answered on hardware, on a machine where NVDA runs
there.

#### What phase 1 built

- `keyLayers.py`, the model, NVDA free: `Target` (a script, an emulated key, or blocked, with
  acts for), `Layer`, `LayerSet` with the fall through chain, the stored form, the default
  layers, and `decide`, which is every rule above as one pure function.
- `keyLayerContexts.py`: chart, picture and graphics, read off graphics mode.
- `keyLayerDispatch.py`, rewritten from phase 0's: reads each device's layers, keeps which one is
  on, carries out a decision, and answers `displayFor`.
- `bmConfig`: a `keyLayerDevices` section per driver name, holding the JSON. Nothing stored, or
  something stored that cannot be read at all, reads as the device's default layers.
- `panning.py` and the table column paging ask `displayFor` instead of reading `gesture.source`,
  so a keyboard key acting for the Monarch pans and pages the Monarch.
- Commands, replacing phase 0's two: the layer key (space with l and dot 7 on the Monarch,
  NVDA+control+shift+l on the keyboard, unchanged), one per display position for the keyboard,
  choose a layer, report layers, and all layers off. A command that added suggested layers was
  built and then removed the same day, when the layouts became defaults instead.
- The profile switch reads the layers again, keeps a layer on by id, and says which went.

The default layers, which a device has until the reader saves its own:

- **Monarch, default layer, one shot:** the left d-pad reads. Up says the line, down says all,
  left reports the focus, right reports the window title. So the layer key and then a direction
  is a reading command, and the layer is off again.
- **Monarch, graphics layer, stays on:** the left d-pad pans and the zoom keys zoom.
- **Keyboard, default layer, one shot:** keypad 5 says the line.
- **Keyboard, graphics layer, stays on:** the keypad pans and zooms acting for the Monarch, and
  keypad 5 says the line.

**Three decisions made while building it.**

1. **The context aware layer key came forward from phase 3**, for the three graphics contexts
   only. Without it the graphics layer, a stays on layer, could only be reached through the
   choose command's list, and a reader panning a drawing would have had to open a dialog to start.
   Tables, automatic enabling and the dialog's "from Graphics layer" labels stay in phase 3.
2. **A one shot layer ends when its key is decided**, the one exception to state changing only in
   scripts. The key it waits for may have no script of this add-on's to end it from: an unbound
   key passing through to Windows has none at all, and wrapping every script NVDA runs would cost
   the repeat count. It is not ended while any capture function is set, so input help does not
   spend the shot.
3. **Every layered keys command is named `script_keyLayer...`**, which is how the decider knows a
   key's ordinary command is one of them and lets it through without ending a layer. A test on
   the plugin and a check against the real NVDA hold every command to it.

The fall through chain is complete in the model and used at runtime; only its presentation waits
for the dialog. 100 unit tests between the model, the dispatch and the plugin, and
`tests/nvdareal.py` checks that every command a default layer names exists, that the layer key
keeps its gestures, and that layers store and list against NVDA's real configuration. That last
one caught a bug the stubs could not: NVDA's `AggregatedSection` has no `keys()`, so the device
list came back empty. Layers still loaded when first used, so nothing visible broke, but phase 2's
device list is built on it.

#### How to run phase 1

With the Monarch and the Focus 80 combined:

1. Nothing to set up: the default layers are there.
2. With nothing drawn, press space with l and dot 7: "Default layer on". Press left d-pad up: it
   says the line, and the layer is off. (One shot.)
3. Put a drawing up and press the layer key: "Graphics layer on". Pan with the left d-pad, zoom
   with the zoom keys, type a dot chord and check the layer is still on, then press the layer key
   again: "Graphics layer off".
4. Turn it on again and leave it with space with z.
5. Turn on the keyboard's graphics layer with NVDA+control+shift+l while a drawing is up, and pan
   and zoom from the keypad.
6. With a layer on, switch profile: the layer stays on, because both profiles read the default
   layers and the layer is still there by id. That a profile with different layers turns a missing
   one off and says so is covered by unit tests until the dialog can save such a profile.
7. The lock screen, carried over from phase 0, on a machine where NVDA runs there.

#### What the phase 1 hardware run found

14 September 2026, a Monarch and a Focus 80 combined, the laptop keyboard layout, the default
layers as shipped.

1. **The one shot default layers work.** On the Monarch, the layer key then left d-pad up said the
   line and left d-pad right reported the title, and the layer was off after each. On the
   keyboard, the layer key then keypad 5 said the line.
2. **The graphics layer works.** With a drawing up the layer key chose it, the left d-pad panned,
   zoom in zoomed, and the layer key and space with z each turned it off.
3. **The keypad works, acting for the Monarch.** Keypad 4, 5 and 6 ran their commands from the
   keyboard's graphics layer, logged as acting for the Monarch.
4. **Escape did not leave a keyboard layer.** Fixed the same day; see "Escape leaves". The escape
   key has no NVDA command, so the rule that recognised escape by its ordinary command could never
   find it. Confirmed fixed on hardware.
5. **A profile switch with a layer on keeps it on**, when both profiles have the layer: with the
   shipped defaults, every profile does. A profile without the layer turning it off is unit tested;
   it can be tried by hand by saving an empty layer set into a profile from the Python console.
6. **The lock screen was not tried**, still, for the reason phase 0 gives.

7. **Dot chords pass through** with the graphics layer on, and the layer stays on. Checked by hand.
8. **Taking the drawing off left the graphics layer on**, with nothing left for it to do. Fixed
   the same day, ahead of phase 3; see "A layer goes with its context".

### Phase 2, the dialog

**Built, 14 September 2026.** On hardware so far: adding a key works, and a key already bound to
another command asks before it is moved. The rest of "How to run phase 2" has not been reported.

The view model with tests, then the dialog, including acts for on keyboard bindings and
members' own commands in the tree. Exit criterion: a reader builds a layer for a display and
for the keyboard from nothing without reading documentation, and the dialog reads with NVDA
the way Input Gestures does.

#### What phase 2 built

- `keyLayerDialog.py`: `LayerEditor` and the dialog over it, as "The layered keys dialog"
  describes, with the differences from Input Gestures listed there.
- **Opened three ways**: NVDA menu, Preferences, "BrlMultiline layered keys...", appended after
  Input Gestures and left out in a secure session as Input Gestures is; the command "Layered
  keys: Opens the layered keys dialog", unassigned; and a "Layered keys..." button in the
  BrlMultiline settings panel, which opens it modally over the settings, as NVDA's own panels
  open their sub-dialogs.
- **Saving** goes through `keyLayerDispatch.save`, into the profile in force, and only for the
  devices changed. A device that cannot be saved, such as one whose layers fall through in a
  circle, is named and the dialog stays open.
- The fall through labels, "from Graphics layer", were left for phase 3 with the rest of context.

Two bugs found building it. An emulated key, once a key was bound to it, was listed as a command
not available from here: an emulated key is always available. And the dialog's editor was made
before wx had made the dialog, which wx does not allow; it is made in `makeSettings` now.

A third found on hardware: Add raised an `IndexError` selecting the "Enter input gesture" prompt
under a command the tree had not expanded, since a virtual tree has no rows for an unexpanded
item's children. The key was still added. The prompt is now selected like any key, after its
command is expanded, and the `--dialog` check collapses the tree before adding, which it did not
before, which is why it passed.

Also seen working on that run: a key already bound to another command asked before moving.

42 unit tests for the editor and the gathering. `tests/nvdareal.py --dialog`, opt in because it
makes a wx application, builds the real dialog and its properties dialog and drives the tree,
the prompt, and a layer and device change.

#### How to run phase 2

1. Open NVDA menu, Preferences, BrlMultiline layered keys. The title names the profile. Device
   is the Monarch, layer is Default, and the tree reads as Input Gestures does.
2. Check "Only show commands with keys in this layer": the four reading commands the Monarch's
   default layer ships with, each with its left d-pad key.
3. Layer, Graphics: the pan and zoom commands with their keys.
4. New layer, "Reading". Filter by "say all", select it, Add, press right d-pad down on the
   Monarch. Press a Focus 80 key while it waits and check it says the key is on the wrong device.
5. Properties on Reading: one shot, space with z listed as an exit key.
6. Device, Keyboard. Find a BrlMultiline graphics command, Add, press a keypad key: it should ask
   which display the key acts for.
7. OK, then use the new Reading layer with the choose a layer command, and the keyboard key.
8. Reopen and Reset to factory defaults on the Monarch, OK: the Reading layer is gone and the
   shipped layers are back.
9. Open the dialog from the BrlMultiline settings panel's button, and with its command.

### Phase 3, context

The context list, the context aware layer key, auto enable on graphics mode, named fall
through layers and the "from Graphics layer" labels in the dialog. Exit criterion: with a
chart layer and a graphics layer defined, the layer key picks the chart layer on a chart and
the graphics layer on a picture, the chart layer gets pan and zoom from the graphics layer,
and an auto layer on the Monarch and one on the keyboard both follow drawings on and off.

**Built, 14 September 2026, partly tested on hardware.** A drawing going up turned the Monarch's
graphics layer on by itself, once that layer was set to come on by itself in the dialog's
Properties: the reader's layers had been saved in phase 2, before the default changed, so the new
default did not reach them. See "Saved layers replace the shipped ones whole". Setting it by hand
also exercised Properties. The rest of "How to run phase 3" has not been reported.

The context aware layer key for the
graphics contexts, and layers going with their context, came earlier; see phase 1 and "A layer
goes with its context".

#### What phase 3 built

- **The table context**, in `keyLayerContexts`: a table laid out in columns on the display, or the
  browse mode caret in a table cell, asked the way `tableArrows` asks. Looked for only when the
  layer key is pressed. Graphics mode changes do not read the caret.
- **Layers come on by themselves**, in `keyLayerDispatch.autoEnable`, whenever graphics mode
  changes: on every connected display and the keyboard, for each device with no layer on, the layer
  marked to come on by itself for the most specific context present. It stays on whatever its
  style, since its context is what ends it.
- **Turned off by the reader, it stays off** until its context goes and comes back: by the layer
  key, an exit key, all layers off, or choosing another layer. Not when it goes with its drawing.
- **What is said.** The drawing command says it once: "a chart of ..., Graphics layer on", and
  "Drawing off, Graphics layer off". A drawing changed by any other command has its layers named
  after that command's own message, not before it.
- **Shipped.** The Monarch's graphics layer comes on by itself. The keyboard's does not: the keypad
  is the review cursor on a desktop layout, and a drawing going up is no reason to take it. The
  Monarch has a table layer, from the layer key only.
- **The dialog shows keys from further down the chain** under their commands, "(from Graphics
  layer)". Change is for the layer's own keys. Remove on a key from below offers to make it do
  nothing in this layer instead, since removing it where it is would change that layer too. Adding
  a key that comes from below says where it comes from before overriding it here.

#### How to run phase 3

1. Put a drawing up: "..., Graphics layer on". The left d-pad pans with no layer key pressed.
   Take it off: "Drawing off, Graphics layer off".
2. Put it up, turn the layer off with the layer key, then change the picture style: the layer stays
   off. Take the drawing off and put it up again: the layer comes back.
3. In the dialog, make a Chart layer for charts that comes on by itself and falls through to
   Graphics, with one key of its own. Its tree shows the pan keys "(from Graphics layer)". Chart a
   selection: "Chart layer on", the most specific layer that comes on by itself, and the d-pad
   still pans. Draw a picture instead: "Graphics layer on". With a chart up and no layer on, the
   layer key also chooses Chart; with a layer already on, it turns that layer off, as always.
4. On a web page with the caret in a table, press the layer key: "Table layer on". The left d-pad
   moves by cell, and the zoom keys turn column pages where a table is laid out in columns.
5. Make the keyboard's graphics layer come on by itself in Properties, and put a drawing up: both
   layers come on, named once.

### Phase 4, only if wanted

1. Per layer activation keys, so a layer can be reached directly without the context
   deciding.
2. Export and import of a device's layer set to a file, to share a Monarch setup.
3. A layer indicator on the display itself, perhaps a glyph in the status cells.
4. Turning a layer off after a period with no key pressed.
5. Storing only a reader's differences from the shipped layers, so an improvement to what ships
   reaches a reader who has saved layers of their own. See "Saved layers replace the shipped ones
   whole". It changes the stored form, and what clearing a layer and deleting one mean.

## Decided with the reader, 14 September 2026

1. **A new layer with no context is one shot**: the next key runs its binding or passes
   through, and the layer leaves either way. A layer with a context stays on.
2. **Unbound keys are transparent**, down the chain to the default layer and then to NVDA,
   and never turn a layer off. Braille typing needs no special case. Escape is the
   exception, and space with z counts as escape on a braille display.
3. **Keyboard layers can bind display and BrlMultiline commands**, acting for a named
   display where the command needs one.
4. **The Monarch and keyboard layouts ship as defaults**, and reset to factory defaults in the
   dialog puts them back. This replaced the first answer, suggested keys offered from Reset and
   never applied, later the same day: BrlMultiline should work on a Monarch out of the box.
5. **The default layer never has a context.** The bottom of every chain is always the same
   layer.
6. **The Monarch's second d-pad stays transparent in graphics.** No finer pan step for now.
7. **Escape only leaves the layer**, in a one shot layer as in one that stays on. It is never
   also sent on, because that would close a dialog the reader only meant to back out of the
   layer from.

## Open questions

None at present. Which pad is physically left was confirmed by hand: the pad reported under
left controls is the left one.
