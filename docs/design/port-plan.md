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
2. Milestone 2, segments and container — CODE COMPLETE, UNVERIFIED ON HARDWARE
3. Milestone 3, configuration and commands — CODE COMPLETE, UNVERIFIED ON HARDWARE
4. Milestone 4, TextInfoPositionRegion — CODE COMPLETE, UNVERIFIED ON HARDWARE
5. Milestone 5, ObjectMonitor — CODE COMPLETE, UNVERIFIED ON HARDWARE
6. Milestone 6, views — CODE COMPLETE, UNVERIFIED ON HARDWARE

What "code complete" means here: the add-on builds to an installable `.nvda-addon`, ruff
passes clean, and 66 unit tests over the segment geometry pass. Nothing has been run
inside NVDA or against a display. Do not treat any of it as working until the list under
"Verification owed" has been worked through. That list is the next task.

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

Unit tests: 67 total, covering grid geometry in reading order, gap and overlap validation,
routing across a 12 segment grid with a round trip over every covered cell and a check that
no two display cells claim the same segment position, and the fill-rows arithmetic
including cut marking and buffer end handling.

Deliberately not built: any actual component. The table reader is later work, and the open
question below is its first design decision.

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

The first thing to try, being the simplest, is a Focus 80 split into two segments with
nothing else enabled.

Note on type checking: `python -m uv run pyright` reports a large number of findings, most
of which are strict-mode noise (missing annotations on wx and NVDA call sites, `wx` not
resolvable). One category is a genuine artifact worth not chasing — see the static analysis
note in [architecture.md](architecture.md).

## Open design questions

1. In a table view, which segment follows the focus? A view must designate one — see the
   reasoning in [architecture.md](architecture.md) — and the natural answer is whichever
   cell is current. This is the first design decision of the table component work.
2. Should the edge row scroll policy give up rows per segment or per display? Giving up
   two rows of a three row segment to scrolling is a lot. Wants deciding against real
   content rather than in the abstract.
3. Sub-row column splits stay out of the settings dialog, by decision: whole row groups on
   a multi row display, column slices on a single row display. Grids are reachable from
   code only.
4. Should the focus segment follow the caret on a multi row display — that is, should the
   ScrollingManager idea come back in a simpler form, moving which segment holds focus
   rather than shuffling regions between segments? Worth considering once milestone 4 has
   real use.
