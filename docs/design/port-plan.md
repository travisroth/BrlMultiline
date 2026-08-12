# Port plan and status

Living document. Update the status markers as work lands.

Target: NVDA 2026.3 or later. The floor is set by the braille package refactor, which split
`source/braille.py` into `source/braille/` in commit 54ccd097f on 2026-06-22. That is after
the 2026.1 release (2026-05-06) and after the 2026.2 beta branch was cut, so neither of
those releases has the package: the add-on's imports fail outright on both. 2026.3 has no
beta yet, so development and testing run against NVDA alpha snapshots for now.

Multi row display support (`DisplayDimensions`, `numRows`/`numCols`) is older and shipped in
2026.1, so it does not affect the floor.

## Status at a glance

1. Milestone 1, scaffold — DONE
2. Milestone 2, segments and container — VERIFIED ON A FOCUS 80, single row
3. Milestone 3, configuration and commands — VERIFIED ON A FOCUS 80, single row
4. Milestone 4, TextInfoPositionRegion — CODE COMPLETE, UNVERIFIED ON HARDWARE
5. Milestone 5, ObjectMonitor — VERIFIED IN PART, one defect open: see the log below
6. Milestone 6, views — CODE COMPLETE, UNVERIFIED ON HARDWARE
7. Milestone 7, panels and composition — CODE COMPLETE, UNVERIFIED ON HARDWARE
8. Milestone 8, lifecycle defects and the plugin harness — CODE COMPLETE, UNVERIFIED ON HARDWARE

What "code complete" means here: the add-on builds to an installable `.nvda-addon`, ruff
passes clean, and 289 unit tests over geometry, panels, views, the container, the document
line regions and the global plugin pass. Nothing has been run inside NVDA or against a display. Do not
treat any of it as working until the list under "Verification owed" has been worked
through. That list is the next task.

## Milestone 1 — Scaffold

Fill in `buildVars.py`, create the add-on package, get a build producing an installable
`.nvda-addon`.

Done:

- `buildVars.py` — name `BrlMultiline`, version 0.1.0, `pythonSources` pointing at
  `addon/globalPlugins/brlMultiline/*.py`, minimum and last-tested NVDA 2026.3.0,
  GPL 2, dev update channel. `excludedFiles` keeps `__pycache__` out of the build, since
  the unit tests import add-on modules directly and leave bytecode behind.
- `addon/globalPlugins/brlMultiline/` package created.

### Naming, and why it is not the 2023 name

The add-on is called `BrlMultiline`, deliberately distinct from the 2023 prototype's
`brailleMultiline`. Three names had to move together for that separation to be real, and
all three would otherwise have collided if both add-ons were ever installed at once:

1. `addon_name` in `buildVars.py`, which is the add-on identity in NVDA and the store.
2. The global plugin package, `addon/globalPlugins/brlMultiline/`. Two add-ons offering a
   `globalPlugins` package of the same name collide on import, and only one would win.
3. `bmConfig.CONFIG_SECTION`, now `BrlMultiline`. The 2023 prototype stored its settings
   under `brailleMultiline` in `nvda.ini`, with a completely different set of keys
   (`numberOfLines_*`, `focusLine*`). Sharing the section would mean validating this
   add-on's spec against the old prototype's leftovers.

User visible strings — the settings category, the input gestures category, and the
messages — read `BrlMultiline` to match.
- Root `readme.md` rewritten as user documentation. The build copies it to
  `addon/doc/en/readme.md`.
- `changelog.md` started.

### Development environment

The template is a `uv` project. Neither `uv` nor `scons` was present on this machine
initially; Python 3.13.0 was, which matches `.python-version`.

Setup performed:

```
python -m pip install uv
python -m uv sync
```

That creates `.venv` with the pinned tooling (scons 4.10.1, ruff 0.14.5, pyright 1.1.407,
prek, and the l10n tools). Thereafter:

- Build: `python -m uv run scons`
- Lint: `python -m uv run ruff check .` and `python -m uv run ruff format --check .`
- Unit tests: `python -m uv run python -m unittest discover -s tests`

Note `uv` is invoked as `python -m uv` because it was installed into the system Python
rather than onto `PATH`.

## Milestone 2 — Segments and container

The core. Everything else depends on it.

Built:

- `_SegmentHandlerProxy` — reports the segment's rectangle as the display dimensions,
  forwards everything else to the real handler, and resolves the `updateDisplay` identity
  check.
- `BrailleBufferSegment` — thin `BrailleBuffer` subclass holding its rectangle and
  `isFocusBuffer`.
- `BrailleBufferContainer` — compositor based cell assembly, cursor mapping, region
  dispatch by `targetSegment`, position dispatch by row and column, per-segment scrolling.
- `FakeRegionsList` — complete sequence proxy onto the focus segment's regions.
- `_doNewObject` replacement, re-derived from NVDA 2026.3 source.
- Install and uninstall symmetric, driven from the global plugin's `terminate`.

All eight defects catalogued in [legacy-inventory.md](legacy-inventory.md) are fixed or
made unreachable by the rewrite. Defect 6, the swallowed routing key at cell 79, has a
regression test.

Unit tests cover the geometry: even and uneven division, tiling without gaps or overlaps,
routing key mapping on both target displays, and a round trip between segment position and
window position over every cell of both displays.

## Milestone 3 — Configuration and commands

Built:

- Per-display config, keyed on display name plus rows plus columns, using a `__many__`
  section so keys are structured rather than string-formatted as in 2023.
- Settings panel with validation that rejects sizes which do not fit, and a focus segment
  that does not exist, before saving.
- Layout expressed as a segment size list, or a count meaning "divide equally".
- Per-segment scroll scripts, generated for all 8 possible segments, unbound by default so
  they can be assigned to display keys through Input Gestures.
- Reverse panning buttons, per display. NVDA has no such setting of its own; this
  implements it by swapping `BrailleHandler.scrollForward` and `scrollBack`. See
  [architecture.md](architecture.md).
- Saving settings rebuilds the buffer immediately, via `postSave`.
- A display change rebuilds the buffer, deferred through `wx.CallAfter` because the
  notification fires from inside the handler's `displayDimensions` property and reading
  those dimensions from the handler would re-enter it.

## Milestone 4 — TextInfoPositionRegion

Built. A `TextInfoRegion` subclass showing the line at a fixed offset from the caret, so
the segments above and below the focus segment show the surrounding lines of a document.

Overrides only `_getSelection`, `update`, `routeTo`, `nextLine`, and `previousLine`. No
copied `update()` body: the offset range is computed first and handed to NVDA's own
`update` through a cached info, so upstream changes to that method are inherited rather
than diverged from.

Refreshed from a patch on `_handlePendingUpdate` rather than a timer. Off by default.

Line assignment is fixed (segment k shows offset k minus the focus segment index); the
auto-shifting reading window is deliberately out of scope, see the ScrollingManager entry
in [legacy-inventory.md](legacy-inventory.md).

## Milestone 5 — ObjectMonitor

Built. Pins an object to a chosen segment so it stays visible while focus moves elsewhere.
Generates real regions with `targetSegment` set, rather than aliasing another segment's
region list as the 2023 version did.

Pinned segments survive focus changes because the `_doNewObject` replacement clears only
the segments that are receiving new regions. Pinning to the focus segment is refused, and
monitored segments are excluded from the document lines feature.

## Milestone 6 — Views

Built after the first five, in response to a design question about whether the segment
machinery could support arbitrary rectangles for component driven display work (a table
reader showing several cells at once on a Monarch).

It could: the compositor, routing, cursor mapping and per segment buffers were all
rectangle based already. What was missing was a way to express anything other than a one
dimensional division, and anywhere to hang the changed behaviour such a view would need.

Built:

- `SegmentView` in `views.py`. Everything is now a view, including the plain single
  segment arrangement and the layout the user configures. One construction path.
- `calculateGridRects` for grids, and `validateRects` which is now the only limit on how
  many segments there may be.
- `MAX_UI_SEGMENTS` replaces `MAX_SEGMENTS`, constraining the settings dialog only.
- `RoutingPolicy`, with a hook in `container.routeTo` and a worked example that turns the
  top and bottom rows of a segment into scroll keys.
- `fillRows` and `markCuts`, filling each row of a segment completely rather than wrapping
  at word boundaries, since narrow segments have no cells to spare.
- `activateView` and `restoreConfiguredView` on the plugin, with a single active view
  rather than a stack, and automatic fallback when a view no longer fits the display.

Superseded in part by milestone 7: `SegmentView` no longer carries the routing and
wrapping policies described here, and `activateView` is no longer the recommended way for
code to take part of the display. Read milestone 7 before relying on anything in this
section.

Unit tests: 67 total, covering grid geometry in reading order, gap and overlap validation,
routing across a 12 segment grid with a round trip over every covered cell and a check that
no two display cells claim the same segment position, and the fill-rows arithmetic
including cut marking and buffer end handling.

Deliberately not built: any actual component. The table reader is later work, and the open
question below is its first design decision.

## Milestone 7 — Panels and composition

Built after milestone 6, in response to a design question that milestone 6 could not
answer: what happens when an object is pinned to row 0 and a table then wants the rows
below it?

Under milestone 6 the answer was "the pin is lost". `activateView` replaced the whole
arrangement, `rebuildBuffer` cleared every monitor, and segments were identified by index,
so nothing could survive a layout change. Worse, `routingPolicy`, `fillRows` and `markCuts`
lived on the view as single display-wide values, so a table wanting selection-style routing
and a status row wanting ordinary routing could not coexist at all.

The missing concept turned out to be a **claim**: an owner able to say "these cells are
mine, do not fill them with document lines, do not renumber them out from under me, do not
let another view take them silently".

Built:

- `SegmentSpec` in `panels.py`. Per-segment rectangle, stable key, owner, wrapping rule and
  routing policy. Policy moved off the view and onto the segment, which is what allows two
  behaviours on one display. Making it per segment did not expose it to the user; the
  settings dialog is unchanged.
- `BraillePanel` with `SinglePanel`, `RowsPanel`, `GridPanel` and `BlankPanel`. A claim on
  a rectangle plus a rule for subdividing it, dissolved by `SegmentView.flatten()` before
  the container is built, so nothing walks a tree at runtime.
- `SegmentView.withPanel` and `withoutPanel` for composition. Panels the claim touches are
  evicted whole, freed cells are given to blank panels, untouched panels keep their keys.
- `validateCoverage` and `remainderRects` in `layout.py`. Panels must tile the display, so
  every cell has a named owner; segments within a panel need not, so gutters still work.
- Stable segment keys throughout. `_monitors` is keyed by them, `_carryOverMonitors` keeps
  pins whose segment survived, `region.targetSegment` accepts a key or a number, and the
  view names its focus segment by key.
- `plugin.activatePanel` and `deactivatePanel`, with `_activePanels` re-composed on every
  rebuild so a claim survives a settings change or a display swap.
- `documentLines.isFree` as the single place deciding which segments may be written into,
  reading the panel reservation and the runtime pin set together.
- `RoutingPolicy` moved to `routing.py`, so `panels.py` can carry a policy without a cycle.

Renames, all mechanical: `BrailleBufferContainer` is now `DisplayContainer`, since it holds
segments and a view rather than buffers, and the word "buffer" already meant three things.
`plugin.monitoredSegments` still exists but now resolves keys to current indices;
`monitoredKeys` is the underlying store.

Also fixed along the way: the configured view now builds **one panel per segment** rather
than one display-wide panel. With a single panel any claim at all would have evicted the
whole display, which defeated the point of composition. This was caught by the tests, not
by inspection.

Unit tests: 237 total. The new files are `test_coverage.py` (tiling and remainder
geometry), `test_panels.py`, `test_views.py` (composition, including the monitor-plus-table
scenario end to end), `test_container.py` and `test_documentLines.py`. The last three run
against stand-in NVDA modules in `tests/unit/brlMultiline/_stubs.py`, which supplies a
crude but honest `BrailleBuffer` so that layers above `layout.py` can be tested outside a
running screen reader.

Deliberately not built: any actual component. The table reader is still later work, but
`GridPanel` plus `activatePanel` is now the interface it would use.

## Milestone 8 — Lifecycle defects and the plugin test harness

Built in response to a second review of milestone 7. Four defects, one of which was
reproduced before being fixed.

Fixed:

- **Document line offsets were derived from display order.** A grid puts several segments
  on one physical row, so nine cells over three rows consumed nine indices and inflated the
  offsets of everything below. Reproduced: a segment seven rows above the focus read ten
  lines back. `SegmentSpec.documentContextIndex` now states the reading order instead. It
  cannot be derived from the row either, because a single row display divided into columns
  puts every segment on row 0 and still wants them consecutive. If the focus segment has no
  context, document lines are withdrawn rather than computed against a fiction.
- **A pin could become the focus segment.** `startMonitoring` refuses the focus segment,
  but `_carryOverMonitors` did not, so changing "segment that follows the focus" in settings
  to an already pinned segment let `refreshMonitors` overwrite the focus content.
- **A rebuild queued before termination still ran.** `wx.CallAfter` could fire after
  `terminate` handed NVDA's buffer back, reinstalling a container over it. Guarded by
  `_terminated`.
- **Configuration profile switches were ignored.** Settings are read through `config.conf`,
  which is profile aware, so a profile switch could change the layout with no braille event
  to announce it. `config.post_configProfileSwitch` is now registered.
- **`targetSegment` meant two things.** It was both an owner's explicit destination and
  this add-on's note of where an untargeted NVDA region was placed, because the
  `_doNewObject` patch stamped it onto NVDA's own regions. That made a dead key impossible
  to reject safely. Split into `targetSegment` (a claim; a dead key now fails delivery
  rather than falling back to the focus segment) and `_brlMultilineSegmentKey`
  (bookkeeping). `focus` and `scrollTo` use `findContainingSegment`, which searches by
  identity before consulting the recorded key.
- **A rebuild put pins back on a display showing speech.** `rebuildBuffer` ended by calling
  `refreshMonitors` unconditionally. In speech output mode NVDA's `handleGainFocus` draws
  nothing, so the rebuild left the pinned object beside the speech with nothing to refresh
  or remove it — and the profile switch listener above made that easy to reach.
  `bmConfig.isSpeechOutputMode` is now the one place the mode is read, and the two paths
  that write content of the add-on's own consult it: `refreshMonitors`, which is also how
  `startMonitoring` draws, and `_populateDocumentLines`. Registrations are kept either way,
  so nothing is lost by being unable to draw.

Note that rejecting a dead key only in `FakeRegionsList.append` would not have been enough:
`_doNewObjectMultiSegment` places regions by calling the container directly. Both paths now
go through `resolvePlacementTarget`.

Tests: 304, up from 237. `test_plugin.py` is new and covers the layer skipped in milestone
7 — container installation and teardown, monitor carry-over in all four of its outcomes,
the deferred rebuild race, profile switches, panel claims surviving rebuilds and being
dropped when they no longer fit, speech output mode, and the installed `_doNewObject`
replacement driven against a real container. `_stubs.loadPlugin` executes the add-on's
`__init__.py` under a name that keeps its relative imports pointing at the single copy of
each module.

The speech output tests now cover both directions. What the add-on refuses to draw while
the mode is on is settled behaviour. What happens on the way back is not: a pin's content
does not return until something redraws it, and a panel has no recovery path at all. The
tests that leave the mode record that gap rather than endorse it, and should be revisited
with the lease API.

## Verification owed

Nothing here has been run against a real display or a running NVDA. Before this is
trustworthy:

1. Configuration round trip. Open the settings panel with a display connected that has no
   stored settings, change something, save, reopen. This exercises the `__many__` config
   section, which is assumed to return spec defaults when a display's subsection does not
   exist yet — the same pattern NVDA uses for `config.conf["braille"][name]["port"]`.
   Verify it rather than assume it, since every other setting depends on it.
2. Focus 80, one row of 80 cells, split into two 40 cell segments. Check focus tracking in
   the focus segment, routing keys landing in the correct segment, and per-segment
   scrolling.
3. Focus 80, reverse panning on. Check that the left hand panning key moves forward, and
   that the Monarch is unaffected.
4. Monarch, 8 rows of 32. Check segment layouts by row group, routing keys across rows,
   and that a segment spanning several rows wraps within its own rows.
5. `TextInfoPositionRegion` in a document — Notepad, a browser, and VS Code, which was the
   case that motivated the 2023 timing hack.
6. ObjectMonitor pinned to a segment while focus moves elsewhere.
7. Uninstall and reload NVDA with the add-on disabled, confirming the patches are removed
   cleanly and braille behaves normally.
8. Swap displays while NVDA is running, confirming the buffer rebuilds with the other
   display's settings and does not recurse.
9. Composition, once the above is trusted. Pin an object to segment 0, then activate a
   `GridPanel` over the remaining rows from the Python console, and confirm the pin is
   still readable and still updating. Then `deactivatePanel` and confirm the configured
   view comes back with the pin intact. This is milestone 7's central claim and nothing
   short of hardware proves it.
10. Two routing policies at once: a grid cell and an ordinary segment on the same display,
    confirming a routing press does the right thing in each.

The first thing to try, being the simplest, is a Focus 80 split into two segments with
nothing else enabled.

## Hardware verification log

NVDA from source, Focus 80 over the `freedomScientific` driver, reporting one row of 80
cells. The display key written to nvda.ini is `freedomScientific_1x80`.

2026-08-12, items 1, 2, 3 and part of 6:

1. Configuration round trip — PASS. A display with no stored settings no longer raises
   `KeyError` on first read; the section is created and specced on demand. A saved setting
   survives an NVDA restart and reappears correctly in the panel, and nvda.ini holds
   `[BrlMultiline] [[displays]] [[[freedomScientific_1x80]]] reverseScrollBtns = True`.
2. Two segments on the Focus 80 — PASS. Even split and an explicit `30, 50` split both
   arrange correctly. Focus tracking, caret tracking, edits and cursor routing all land in
   the focus segment. A focus segment of -1 and of 0 both behave; 0 is the better default
   on this display, since the reader's hands rest at the left.
3. Reverse panning — PASS, both paths. NVDA's own panning keys are swapped by the
   `BrailleHandler` patches, and the per segment scroll commands are swapped independently
   by `GlobalPlugin.scrollSegment`. The two do not compound, as intended: they act on
   different classes.
4. Segment layout report — PASS. Reports the segment count and the focus segment
   correctly.
6. ObjectMonitor — PASS for an editable text object. A Notepad document pinned to a
   segment stays readable and pannable while the focus moves to another application
   entirely. FAILS for a browse mode document: see below.

### Defect found and fixed: a pinned browse mode document could not be panned

Pinning a Chrome page announced the page and showed the line the browse mode cursor was
on, correctly, but the segment would not pan. Three separate causes, all now addressed and
all awaiting a hardware re-test:

1. `ObjectMonitor` passed the raw navigator object to `getFocusRegions`, where NVDA's own
   `BrailleHandler.handleGainFocus` substitutes the tree interceptor when there is one that
   is ready and not in pass through. Without that step the regions were built over the one
   element the browse mode cursor was in, which reads correctly — the content shown was
   right — and then has nothing to pan into, because that element's text ends where the
   element does. `objectMonitor.resolveTarget` now makes the same substitution.
2. `DisplayContainer._scroll` panned a segment out of focus with `_nextWindow` and
   `_previousWindow` only, and those move within the text already rendered. The rendered
   text is one reading unit, so a pin could never leave the line it was made on. It looked
   like panning worked whenever that line was longer than the segment, which is why Notepad
   passed, and like nothing happened at all when it was shorter, which is why the web page
   failed. `_panPinnedContent` now falls through to a line change, but only for regions
   that can make one safely, which is what the new `pinnedRegions` module provides: a
   region that keeps its own `TextInfo` and answers `_getSelection` and `_setCursor` from
   it. NVDA's `nextLine` and `previousLine` then work unaltered and move only the pin's own
   position, so the objection that stopped `_scroll` falling through — that it would move a
   caret in an object the user is not in, and drag the focus with it — no longer applies.
   Ordinary regions are still refused.
3. Monitors were refreshed only from `rebuildBuffer` and `startMonitoring`, so a pin was a
   snapshot of the moment it was made. `patches._refreshPinnedObjects` now re-reads them
   from the core cycle, throttled to `MONITOR_REFRESH_INTERVAL`, and `ObjectMonitor.refresh`
   writes to the display only when what it would show has changed, keeping the reading
   position and the window position across the redraw.

Re-tested together and PASSING: panning a pin across lines works in both Chrome and
Notepad, and a pinned taskbar clock updates on its own.

### Not yet verified

The switch over the configured layout — the `segmentsEnabled` setting, the checkbox, and
the toggle command — is code complete and unit tested but has not been run on hardware.
Worth checking in particular that a claim made by code while the display is undivided
behaves: the one segment holds the focus, so every claim touching it has to offer a focus
segment of its own, where a divided display only asks that of a claim that actually evicts
the focus segment.

Note on type checking: `python -m uv run pyright` reports a large number of findings, most
of which are strict-mode noise (missing annotations on wx and NVDA call sites, `wx` not
resolvable). One category is a genuine artifact worth not chasing — see the static analysis
note in [architecture.md](architecture.md).

## Open design questions

1. ~~In a table view, which segment follows the focus?~~ Answered in milestone 7. A panel
   carries `focusSegmentKey`, naming the segment it offers if a claim would evict the
   view's own; `GridPanel` offers its first cell. A claim that would strand the focus
   without offering a replacement is refused. What remains open is whether a table should
   *move* that designation as the current cell changes, which needs a real component to
   decide against.
2. Should the edge row scroll policy give up rows per segment or per display? Giving up
   two rows of a three row segment to scrolling is a lot. Wants deciding against real
   content rather than in the abstract. Now that policy is per segment, a mixed answer is
   at least expressible: only the panel that needs it pays.
3. Sub-row column splits stay out of the settings dialog, by decision: whole row groups on
   a multi row display, column slices on a single row display. Grids are reachable from
   code only.
5. `MAX_UI_SEGMENTS` is a flat 8, but a Monarch exposes up to ten content rows depending on
   line spacing, so a ten row display cannot be divided into ten segments from the dialog.
   It should become geometry aware. The config spec bound and the dialog can both take the
   connected display's row count; the generated per segment commands cannot, because they
   are created at import time and their gesture bindings must be stable across displays. So
   the likely answer is a geometry aware dialog bound plus a fixed, larger command count.
6. What should be redrawn when the display comes back out of speech output mode? Going in
   is settled: the display is emptied and the add-on draws nothing while the mode is on. A
   pin returns at the next rebuild or object update, but nothing announces the mode ending,
   so it may be a while; a panel's content is lost outright. See the characterisation tests
   in `test_plugin.py`. Probably part of the lease API rather than separate.
4. Should the focus segment follow the caret on a multi row display — that is, should the
   ScrollingManager idea come back in a simpler form, moving which segment holds focus
   rather than shuffling regions between segments? Worth considering once milestone 4 has
   real use.
