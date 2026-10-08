# Chart points: stepping and guide lines

Plan for two additions to the charts in the graphics mode:

1. **Point stepping.** Commands that move through a chart one data point at a time — one
   bar, one day of a line chart, one period of a price chart — and speak what that point
   is, the same words a routing press on it gives.
2. **Guide lines.** While a point is marked, the chart shows where it is: a sparse line
   across the plot at its value, and a sparse line up and down the plot at its position. A
   finger finds the guide at either edge and follows it to the point, and the level line
   shows every other bar or day that is above or below it.

Read [tactile-graphics-plan.md](tactile-graphics-plan.md) first, and the `Drawing` and
`GraphicsMode` docstrings in `graphicsMode.py`. This plan leans on how a chart is redrawn
for a window rather than magnified, and on how a press is turned into a source dot.

**Status, 8 October 2026: phases 1 to 4 are built and unit tested, and stepping is confirmed on
the Monarch.** Tested on hardware on 7 October: the step keys on line and price charts, page
turns, stepping from the edges of the panel after a pan, the mark following a zoom, and a
routing press marking the point it lands on (open question 2). The stepping keys first reached
only a chart layer saved after they were added; shipped layer revision 4 in `keyLayers.py`
gives them to older saved layers. The phase 3 texture trial is still to do, so `GUIDE_SPACING`
in `chartDraw.py` is a first guess. Where the build differs from the design below:

1. A cut lowers a pin only where the pins above and below it are raised too, so a bar exactly
   as tall as the marked one keeps its top and only a taller one is notched. The design said
   only "cut into the bar", which would have shortened an equal bar by one pin.
2. The level line's edge ticks are drawn only where the pins are free, so a tall first or last
   bar keeps its notch rather than having it filled in by a tick.
3. `Drawing` also takes `levelNames`, the lines a level can follow, and `PointMark` carries the
   level's value and its line's name, which is what "next line for the level" says.
4. The tests are in `tests/unit/brlMultiline/test_chartPoints.py` for the charts and the guides,
   and `TestSteppingThroughAChart` in `test_graphics.py` for the mode.

## Where the idea came from

Dot's NVDA add-on for the Dot Pad (`utils/chart.py`, `utils/chartAxis.py` and
`utils/drawing.py` in their package) charts Excel and PowerPoint chart objects as bar
charts. The Dot Pad has no touch sensing, so its way into a chart is the keys. The moving
itself is NVDA's: with a chart focused in Excel, NVDA's own chart support
(`NVDAObjects/window/_msOfficeChart.py`) moves between series with up and down arrow and
between a series' points with left and right arrow, speaking each one. Dot's add-on watches
which point NVDA's navigator object is on (its `arg1` and `arg2`, the series and point
numbers) and redraws: that point's letter is underlined on a category axis, and a
horizontal "trace line" is drawn from the value axis across to the top of its bar. F1 and
F4 only page the chart sideways when it has more bars than fit, and the view also follows
the point when the arrows take it off the edge.

Two things are worth taking from it, and one is not:

1. **Taken: stepping with keys.** Pointing is the best way into a chart on a Monarch, but
   it is not the only one a reader wants. Stepping is exact where a fingertip is not,
   reaches a point that shares a pin with its neighbours, and works on a display that can
   draw but cannot report a touched pin.
2. **Taken: a line from the edge to the point.** Their trace line connects the point to
   the axis labels. We have no axis labels up the side (see `writeFrame` in `chartDraw.py`
   for why), so a line that stopped at the point would lead nowhere. Drawn the whole width
   of the plot instead, it becomes a level: a reader sweeping along it feels which bars
   reach past it and which fall short, which is the comparison the marked point is usually
   wanted for.
3. **Not taken: a drawn value axis.** Their value labels cost over a quarter of a 60 dot
   wide panel, and their scale has a fault that crushes small values flat. Our corner
   labels and press-to-read stay as they are.

## What the reader gets

With a chart up:

1. **Graphics: Next point on a chart** marks the next point and speaks it. The first
   press, with nothing marked, marks a point without moving past it: the point last
   pressed with a routing key if there was one, otherwise the first point on the display.
2. **Graphics: Previous point on a chart**, the same backwards. With nothing marked it
   starts from the last point on the display.
3. **Graphics: First point on a chart** and **Graphics: Last point on a chart** go to the
   ends of the whole chart, not of the window.
4. **Graphics: Unmark the point on a chart** takes the mark and its guides off.

What is spoken for a point is exactly what a press on it says, so a reader learns one
vocabulary:

1. A bar: "Wednesday, 330".
2. A line chart: every line being shown, at that point: "Close 96.3, Lower 98.5, day 149".
   A line with no value there says so: "Close 96.3, Lower no value, day 3".
3. A price chart: the period with its four prices.

At either end the point does not move or wrap, and "first point" or "last point" is said
on its own, with no reading, so it is unmistakably a refusal and not another point.

On a picture, which has no points, the commands say "Only a chart has points to step
through."

### Keeping the point on the display

When the next point is off the edge of a zoomed chart, **the window turns a page** rather
than creeping one point at a time: the new point goes to the edge the reader is moving
away from, with the rest of the window ahead of it. Moving right, the point lands at the
left edge and the next window's worth of points are unread. This is the same choice
"Full display scroll for caret" makes for text, and for the same reason: every row, or
here every point, on the display after a scroll is one the reader has not felt yet.

Creeping, which is what Dot's `_ensureActiveBarVisible` does, would redraw the whole panel
on every press once the point reaches the edge, and the reader would feel the entire chart
shift by one point each time. A page turn costs one redraw per window's width.

The whole-chart view shows every point, so it never has to move.

### What the guides look like

Drawn only while a point is marked, over the chart as it was composed:

1. **The level line** runs across the whole plot at the row of the marked point's value.
   It is a sparse line, a pin every fourth column, so it cannot be mistaken for a bar, a
   data line or the baseline. Where it crosses a filled bar it is **cut into the bar**
   instead: those pins are lowered, so a bar taller than the marked one has a notch at
   the marked height, which a finger reads as "this one goes past it". It never lowers a
   pin of a data line or a price bar, which are one pin wide and would break.
2. **The point line** runs up and down the plot at the marked point's column, in the same
   sparse texture. For a bar it goes in the gap column just left of the bar, so the bar
   itself is untouched. For a line chart and a price chart it goes through the point's own
   column.
3. **Edge ticks.** Two solid pins at each end of the level line, at the very left and
   right of the plot, and at the top and bottom of the point line. Sweeping down either
   side of the plot finds the level; sweeping along the top or bottom finds the point.

Which value the level is drawn at:

1. A bar: its value, so the level is the bar's tip.
2. A price chart: the close.
3. A line chart: the line chosen for the level, by default the first line being shown
   (the thick one where there are three or more). See phase 4 for changing it. A gap at
   that point draws the point line and no level.

### Pressing and stepping together

A routing press on a chart marks the point it landed on and draws its guides, as well as
saying it. Press on a bar, then press next point, and the bar after it is marked.

This plan first had the press only remember the point, so the panel would not redraw under
the finger that pressed. Decided on hardware, 7 October 2026: a reader who pressed a point
and then stepped expected to step from it, and a mark left elsewhere stepped from somewhere
else. See open question 2.

## Design

### What a chart offers: additions to `Drawing`

Four new optional arguments, in the same spirit as `describeAt` and `redraw`: a drawing
either can do this or cannot, and the mode asks rather than checking what kind of thing it
is.

1. `firstPoint: int = 0`. Which point of the whole chart this drawing starts at. Zero for
   the whole chart. A window composed by a `redraw` closure sets it to the `first` it cut
   the data at, which each reframer already computes (`windowOf`, and `_snapped` for a line
   chart). This is what lets the mode hold the marked point as an index into the whole
   chart while the drawing on the panel is a window of it.
2. `pointAt(x) -> Optional[int]`. Which point, local to this drawing, a source column
   belongs to, or None between points. Each chart already has this logic inside its
   describer: the bar loop in `chart._describer`, `_nearestPoint` in `chartLine`, and the
   inverse of `_edges` in `chartPrice`. Pulled out so both use it.
3. `markFor(index, series) -> Optional[PointMark]`. Where a local point is drawn, for the
   guides. `PointMark` is a new named tuple in `chartDraw`:
   - `column`: where the point line goes.
   - `row`: the row of the level line, or None for no level (a gap in a line).
   - `plotTop`, `plotBottom`: the rows the guides span, so they stay out of the writing.
   - `cuts`: whether the level line notches filled shapes (True for bars only).
4. `sayPoint(index) -> str`. The reading for a local point. Refactored out of each
   describer so the press and the step use the same function and cannot drift apart. For
   a line chart it reads the lines being shown, which it already knows from `shown`.

`points` already exists and already means how many points the drawing covers.

The charts keep knowing nothing about NVDA, the mode, or the cursor. They describe their
geometry; the mode decides what to mark.

### What the mode holds: `GraphicsMode`

New state, all in whole-chart point indexes:

1. `_marked: Optional[int]`. The marked point.
2. `_levelSeries: int`. Which line the level is drawn for, on a line chart.

Lifetime:

1. `enter` with a new drawing clears all three, as it already resets the zoom and origin.
2. `replaceSource` keeps them, because it is used for a change of view on the same chart
   (and for a change of style on a picture, which has no points). A marked index past the
   new source's `points` is cleared rather than clamped.
3. `leave` and `onEvicted` clear them.

New methods:

1. `stepPoint(delta) -> str`, `pointToEnd(last: bool) -> str`, `unmarkPoint() -> str`.
   Each returns what to speak, so the scripts in `__init__.py` stay as thin as the zoom
   ones.
2. `_showPoint(index) -> bool`. If the point is in the current drawing, render. If not,
   turn a page: find the point's column in the whole figure with
   `self._source.markFor(index, ...)`, set `_originX` so that column sits at the near edge
   of the visible span, clamp, and render. Then check that the new drawing really contains
   the point (`firstPoint <= index < firstPoint + points`). Fractions, rounding in
   `windowOf` and the even spacing in `_snapped` can leave it one point outside, so nudge
   the origin by one point's width toward it and try again, at most three times, then
   fall back to centring on it. On failure, restore the previous state, as `_zoomTo`
   does.
3. Drawing the guides goes in `render`, after `buffer.blit(self._drawing.buffer)`, and
   only when the figure redraws itself (`_windows`) and something is marked. A sampled
   picture is never given guides. Drawing them on the panel's buffer rather than inside
   the chart means a step within the window is one render with no recompose: the
   composed chart is reused and only the guides move.

`describe` adds the marked point when there is one ("point 12 of 250"), so "Reports the
drawing on the display" says where the reader is in the data as well as in the window.

### Commands and keys

Six scripts in `__init__.py`, category BrlMultiline, all with the `Graphics:` prefix:
next point, previous point, first point, last point, unmark point, and (phase 4) next line
for the level.

Default keys go in the Monarch's chart layer in `keyLayers.defaultLayers`, beside o and v.
Proposed, to be tried on hardware:

1. Next point: space with dot 4.
2. Previous point: space with dot 1.
3. First point: space with dots 1, 2 and 3.
4. Last point: space with dots 4, 5 and 6.
5. Unmark: u (dots 1, 3 and 6).
6. Next line for the level: l (dots 1, 2 and 3) is taken by single lines on a picture but
   free on a chart; use l.

Space with dot 1 and dot 4 are the chords Monarch users know for previous and next line,
which is the closest thing a chart has. These shadow nothing the chart layer has today; the
graphics layer it falls through to binds the left d-pad, the zoom keys and space chords
with dot 7 or dot 8, none of which collide.

The left d-pad keeps panning. Once stepping exists, left and right on the d-pad might be
better spent on previous and next point, since a chart only pans sideways and stepping
pans for you. That is a question for hardware, not for this plan; see open questions.

## Phases

Each phase leaves the add-on working and is unit tested before the next starts.

### Phase 1: charts describe their points

1. Add `PointMark` to `chartDraw`.
2. Add `firstPoint`, `pointAt`, `markFor` and `sayPoint` to `Drawing`, all optional,
   documented in its docstring.
3. Bar chart: extract the bar lookup and the reading from `_describer`; supply all four;
   set `firstPoint` in `_reframer`. `markFor` gives the column left of the bar
   (`bar.left - 1`, or `bar.left` for the first bar) and the row from `_tipRow`, with
   `cuts=True`.
4. Line chart: extract `_nearestPoint` use and the per-point reading; `markFor` gives
   `columns[index]` and `scale.row(value)` for the chosen line, None for a gap; set
   `firstPoint` after `_snapped`.
5. Price chart: extract the `_edges` inverse and the reading; `markFor` gives the stem
   column and the close's row.
6. Tests in `test_chart.py`, `test_chartLine.py` and `test_chartPrice.py`: for every
   point of a whole chart and of a window, `pointAt(markFor(i).column)` is `i`; `sayPoint`
   equals what `describeAt` says at that point's column; `firstPoint` of a window matches
   the slice the reframer cut, including a window against the right-hand end.

Nothing visible changes in this phase. The describer refactor is checked by the existing
press tests passing unchanged.

### Phase 2: stepping, spoken only

1. Mode state and `stepPoint`, `pointToEnd`, `unmarkPoint`, `_showPoint` as above, with no
   guides drawn yet.
2. `reportPress` marks the point it lands on, through `pointAt` and `firstPoint`.
3. The scripts, and the chart layer keys.
4. Tests in `test_graphics.py`, using the fake surface it already has:
   - The first step marks without moving, from the press if there was one.
   - Stepping off a zoomed window turns a page in both directions, and the new drawing
     contains the point, including at both ends of a line chart zoomed to one pin per
     point (the case `_snapped` adjusts) and a bar chart whose window does not divide
     evenly.
   - The ends refuse without moving and say so.
   - A change of view keeps the mark; a new drawing clears it.
   - A picture refuses.
5. Hardware: step through a year of prices at one pin per point and at the whole-chart
   view, and a bar chart of a dozen bars. Is the page turn the right size, and is the
   reading enough to follow without guides?

### Phase 3: guides

1. Draw the level line, point line and edge ticks in `render`. A helper in `chartDraw`
   takes a buffer and a `PointMark` and draws them, so it can be unit tested without the
   mode.
2. `describe` reports the marked point.
3. Tests: the guide never lowers a pin outside a filled shape when `cuts` is set, never
   lowers any pin when it is not, stays between `plotTop` and `plotBottom`, and is absent
   when nothing is marked.
4. **Texture trial on hardware before settling the spacing.** The line chart already uses
   a dotted line, a pin every third column, and a guide every fourth may be too close to
   tell apart. Try, with the test figure and a real chart:
   - a pin every fourth column;
   - pairs of pins with three gaps (1, 1, 0, 0, 0);
   - edge ticks alone, with no line across.
   Keep whichever reads as "not data" fastest. If none does on a line chart, line charts
   get edge ticks only and bars keep the full level line, where it does not compete with
   a dotted series.

### Phase 4: follow-ups

1. **Zoom about the marked point.** `_zoomTo` keeps the middle of the view in the middle.
   With a point marked, keep the point in the middle instead, so "find the point, then
   magnify around it" is two commands, not three plus panning.
2. **Next line for the level** on a line chart with several lines: cycles `_levelSeries`
   through the lines being shown, says the line's name and its value at the point, and
   redraws the guide.
3. Readme: a section under Drawings and charts, and the new keys in the chart layer list.

## Open questions

1. **The d-pad.** Should left and right on the left d-pad step points on a chart, with
   panning left to the space chords? Stepping does the panning a reader of a chart
   usually wants. Decide after phase 2 on hardware.
2. **Press marks the point?** Decided on hardware, 7 October 2026: yes. A press marks the
   point and draws its guides, so stepping goes on from where the reader pressed. Tested on
   the Monarch: the refresh under the finger is no problem, since it raises and lowers only
   the pins that change.
3. **Price chart level at the close.** Close is the price most often compared against.
   A reader studying ranges might want the high or the low. Leave it at close unless asked.
4. **Bar chart with values written over the bars.** The level line passes through the
   value row only if a bar's tip reaches it, which it never does since the value row is
   outside the plot. Confirm on hardware that the guides staying inside
   `plotTop`..`plotBottom` leaves all the writing readable.
5. **A display that draws but cannot report a touch.** Stepping is most valuable there,
   since pointing then works only to the nearest cell. The Dot Pad is the obvious case. Not
   in this plan, but nothing here depends on touch, so a Dot Pad driver offering the
   drawing surface would get stepping and guides with no further work.
6. **A point line in a one pin gap.** A bar chart has a single pin of gap between bars, and
   the point line goes in it, so every fourth row it touches both neighbours and the two bars
   feel joined by a rung there. Seen in a simulated render, not yet felt. If it reads badly,
   the alternatives are the point line through the bar's own middle column, cut into it the
   way the level line is, or no point line on a bar chart at all, since the marked bar is the
   one whose top the level line starts from.
