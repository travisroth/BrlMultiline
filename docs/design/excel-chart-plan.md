# Excel charts: drawing the chart itself, and following NVDA through it

Plan for drawing an Excel chart on the pins from the chart's own definition, not from cells the
reader selected, and for keeping the chart stepper on the same point as NVDA's chart navigation.

Read [chart-point-plan.md](chart-point-plan.md) first, and the module docstring of
`addon/appModules/excel.py`. This plan adds a second seam beside the sheet seam described there,
and leans on the stepper, the guide lines and the level line from the chart point plan.

**Status, 8 October 2026: all four steps of the build order are built and unit tested, and none
of it has been on hardware.** Where the build differs from the design below:

1. **Excel's chart types are mapped in `appModules/excel.py`**, not in `chartMenu`, so the global
   plugin never sees an Excel constant: the reading arrives with a neutral `kind` (`line`, `bars`,
   `ohlc`, `hlc`) or an empty one and a refusal. `chartMenu.offerForChart` draws the kinds.
2. **The reading is a `ChartReading` defined in the Excel module**, field for field the
   `chartSource.ChartDefinition` the plugin documents, since the application module does not
   import the global plugin. Likewise `PointReading` and `ChartPoint`.
3. **Categories are read one cell at a time**, `Range.Item(i).Text`, as NVDA's point reads its
   one. The plan hoped for one call per column; Excel's `Text` of a range of several cells is
   empty unless they all agree, so there is no such call. Up to 400 COM calls on a long chart,
   to be timed on hardware.
4. **Axis titles are not yet added to what a press or a step says.** The title is in the name.
5. **Following uses the series name** to move the level line, matched against the drawing's
   `levelNames`, rather than the series number, since a view can leave lines out.
6. **A chart drawn from Excel does not change which kind the "Which chart?" dialog opens on**
   (`drawChart(remember=False)`).
7. **NVDA's own point speech is given clean numbers**, an addition found on hardware: NVDA's
   chart navigation said "increased by 3.0000000000000004", the raw binary arithmetic of 344.37
   less 341.37, and spoke every value to sixteen digits. The subtraction is inline in
   `OfficeChartElementPoint._getChartElementText`, so there is no smaller function to replace.
   Instead the overlay's `initOverlayClass`, which NVDA runs after its own constructor has
   stored the chart, gives each point the chart through `ChartWithChartNumbers`, whose series
   values are `ChartNumber`s: floats that subtract to ten significant digits and print without
   the noise. NVDA's wording, order and translations are untouched. Worth reporting to NV Access
   as well, since every NVDA user meets it. Tested by `TestWhatNvdaSaysOfAPointIsWithoutNoise`,
   and confirmed in speech on hardware on 8 October.
8. The tests are `TestReadingAnExcelChart` and `TestTheChartObjectsOfferTheChart` in
   `test_excelAppModule.py`, `TestDrawingAnApplicationsOwnChart` in `test_chartMenu.py`,
   `TestAHighLowCloseBar` in `test_chartPrice.py`, the `markPoint` tests in
   `TestSteppingThroughAChart` in `test_graphics.py`, and `TestFollowingAnApplicationsChart` in
   `test_plugin.py`.

## What NVDA already does, and what this does not repeat

NVDA has a working model of Office charts in `NVDAObjects/window/_msOfficeChart.py`, used by
Excel, Word and PowerPoint. Read against NVDA 2026.3 in `C:\code\nvda\source`:

1. **Reaching a chart.** NVDA does not find a chart from the focus. `ExcelBase._getSelection`
   builds an `OfficeChart` when Excel's window has an `ActiveChart` and no cells are selected,
   and the Charts list in the elements list (`ExcelChartQuickNavItem.moveTo`) activates a chart
   object and queues a gain focus event for that selection. So the reader gets to a chart the
   way NVDA already teaches, and the focus object is then an `OfficeChart` holding
   `officeChartObject`, Excel's own `Chart`.
2. **Moving through it.** On the chart, up and down arrow move through the series and then a
   "Chart Elements" group (title, axes, chart and plot areas, legend, data table). On a series,
   left and right arrow move through its points and then its trendlines, and wrap at the ends.
   Each move calls `select()` on the element in Excel and queues `gainFocus` for it, through
   `OfficeChartElementList.navigateToElement`.
3. **A point.** `OfficeChartElementPoint` carries `arg1`, the series number, and `arg2`, the
   point number, both counting from 1. Its name gives the category (read as the cell's text
   through the series formula, so a date reads as a date), the value, the axis titles, and on a
   line chart the change from the point before. NVDA+D and NVDA+F report more and the colour.
4. **The chart's name.** `OfficeChart._get_name` gives the title and the type, from
   `chartTypeDict`, NVDA's translated names for every `xl` chart type constant.
5. **Leaving.** Escape on the chart calls `focusOnActiveDocument` and the focus goes back to the
   active cell.

None of that is rebuilt here. NVDA keeps the navigation, the speech, the element list, the
colours and the way in and out. What this adds is the one thing speech cannot give: the shape of
the whole chart under the hands, with the point NVDA is on marked in it.

Two of NVDA's pieces are reused directly, from a private module, so both are imported in one
place with a fallback (see risks):

1. The chart type constants (`xlLine`, `xlStockOHLC` and the rest), to decide what to draw.
2. `chartTypeDict`, to say the chart type in NVDA's own translated words.

## Decisions

1. **The chart is read once, in bulk, from Excel's object model.** NVDA's point objects ask
   Excel one value at a time as the reader arrives, which is right for speech and wrong for a
   drawing of 277 days. `SeriesCollection(i).Values` gives a whole series in one call.
2. **Excel decides the chart type, so nothing is asked.** The "Which chart?" dialog exists
   because a selection does not say what it is. A chart does. The chart command on a chart
   draws that chart, or says why it cannot.
3. **Drawn with the drawings we have.** Line charts, single series column and bar charts, and
   Excel's stock charts map onto the line, bar and price charts, with their windowing, zoom,
   stepping and guides. Everything else is refused with a reason that names the type in NVDA's
   words. New shapes are a later plan; see out of scope.
4. **Following is silent.** When NVDA moves to a point, the mark moves on the pins and nothing
   is said, since NVDA has just said it. Two voices for one key press would talk over each
   other. Our stepper keys go on speaking our own words, since they are our key presses.
5. **The pins follow NVDA, and NVDA follows the pins.** Arrowing in NVDA's chart navigation
   marks the same point on the display. Stepping with our keys, or pressing a routing key on a
   point, moves NVDA's place in the chart as well, so the next arrow press continues from the
   point under the reader's finger. One place in the chart, not two.

## Design

### The seam: `brlMultilineChart`

The same shape as `brlMultilineSheet`, for the same reasons the module docstring of
`appModules/excel.py` gives: only that file knows Excel exists.

1. `appModules/excel.py` adds an overlay, `SpreadsheetChart`, to `OfficeChart` and to every
   `OfficeChartElementBase` and `OfficeChartElementList` object whose `officeChartObject`
   belongs to Excel. NVDA builds these through `DynamicNVDAObjectType`, which asks the
   application module for overlays exactly as it does for a cell, so `OVERLAYS` gains one entry.
   Recognising one must not touch COM: `isinstance` and `hasattr` only, as `readsByCoordinate`
   explains.
2. The overlay offers `brlMultilineChart()`, returning an `ExcelChart` reader, and
   `brlMultilinePoint()`, returning `(series, point)` counting from 0, or None on anything that
   is not a point (a series, an axis, the legend).
3. A new `flowObjectTable`-style accessor in the global plugin, `chartOf(obj)`, mirrors
   `sheetOf`: it asks for the attribute and turns a failure into None with a debug line, and lets
   `CallCancelled` through so the reader is told to try again rather than told there is no
   chart.

### Reading a chart: `ExcelChart`

In `appModules/excel.py`, beside `ExcelSheet`. It returns a plain description that knows
nothing of COM, defined in `chartSource.py` so the global plugin and its tests can use it:

1. `ChartDefinition`: `title`, `kind` (the Excel type constant), `typeName` (from
   `chartTypeDict`), `categories` (one label per point), `series` (each a name and its values),
   `categoryTitle` and `valueTitle` (axis titles or empty), and `key`, which identifies this
   chart for following (see below).
2. Values from `SeriesCollection(i).Values`, one call per series. Empty cells and #N/A come back
   as something that is not a number and become gaps, which the line chart already draws as
   gaps.
3. Categories read the way NVDA reads them, as the cells' text through the series formula, so a
   date category is the date the sheet shows and not a serial number. Read for the whole range
   in one `Range(...).Text`-style call per column rather than one per point, with
   `XValues` as the fallback where the formula has no category range, exactly as NVDA falls
   back. NVDA's version of this is inline in `_getChartElementText` and cannot be called, so
   this is the one place the plan repeats NVDA, deliberately, and keeps to its logic.
4. The series share the categories. Series of different lengths are refused for now, since
   every drawing assumes one category per point.
5. Read on the main thread, as the sheet is. A chart of four series is about a dozen COM calls.

### What gets drawn

`chartMenu` gains `offerForChart(definition)`, returning one `Offer` or a refusal with its
reason. Mapping by type:

1. **Line**, line with markers, and their stacked forms drawn unstacked: the line chart, up to
   its four lines. More series than that are refused with the count, as a selection would be.
   A Bollinger chart in Excel is this case.
2. **Clustered column and clustered bar with one series**: the bar chart.
3. **Open-high-low-close stock chart** (`xlStockOHLC`): the price chart, series 1 to 4 taken as
   open, high, low and close, which is the order Excel itself requires for that type.
4. **Volume-open-high-low-close** (`xlStockVOHLC`): the price chart from series 2 to 5, with the
   volume left out and the description saying so.
5. **High-low-close** (`xlStockHLC`, `xlStockVHLC`): the price chart's open, high, low, close
   bars with the open tick left off, series 1 to 3 as high, low and close (2 to 4 with volume).
   Decided 8 October 2026. That needs the price chart to take a period with no open: `Period`
   gains an optional open, `_drawBar` skips the left tick when there is none, and a press or a
   step says high, low and close only. Candlesticks are not offered for these, since a candle's
   body is the open to the close and there is no open to draw it from.
6. **Anything else**, and any chart whose series do not all share one type (a combination
   chart), or that puts a series on a secondary axis: refused, naming the type: "Scatter charts
   cannot be drawn yet." The volume stock chart above is the one exception, since its secondary
   axis carries only the volume, which is left out.

The drawing is named from the chart's title, and the axis titles are added to what a press and
a step say, so the pins speak the same vocabulary as NVDA's chart navigation.

### The chart command on a chart

`script_chartSelection` asks the focus for a chart before it asks for a sheet. If there is one,
it draws it with no dialog, through `drawChart`. If the focus is a cell, nothing changes from
today. The command keeps its name and key, so "chart this" means whichever of the two the reader
is on.

Getting onto a chart stays NVDA's job: select it, or the Charts list in the elements list.

### Following NVDA's chart navigation

The plugin's `event_gainFocus`, which already runs after `nextHandler` for list view columns,
gains a second short check:

1. Is a chart drawing up, and is the new focus a point of that same chart? The drawing records
   the `ChartDefinition.key` it was made from; the point's chart is asked for its key. The key
   is the workbook's full name, the sheet's name and the chart object's name, since two COM
   wrappers of one chart do not compare equal.
2. If so, `GraphicsMode.markPoint(index, level)`, a new method that marks a point without
   speaking, turning a page if the point is off the panel, through the same `_showPoint` a step
   uses. `index` is NVDA's point number less 1. `level` is the series NVDA is on, found in the
   drawing's `levelNames`, so the level line follows the series the reader is arrowing through.
   A series not in the current view leaves the level where it is.
3. If the focus is on the chart but not on a point (a series, the legend, an axis), the mark is
   left alone.

Cost on every other focus change: one `getattr` that finds nothing.

### NVDA following the pins

When our stepper moves, or a routing press marks a point, NVDA's place in the chart moves too:

1. The plugin finds NVDA's series object for the drawing's chart from the focus: the focus
   itself if it is a series, its parent if it is a point.
2. It sets that series' `activeElement` to the point object at the new index from its
   `elementList`, and calls `select()` on it, which selects the point in Excel. No focus event
   is queued, so NVDA says nothing; our step has already spoken.
3. The next left or right arrow then continues from that point, because `navigateToElement`
   moves from `activeElement`.

If the focus is not in the chart, for instance the reader pressed Escape and is back on a cell,
nothing is done, and the drawing stays up as it does today.

This reaches into NVDA's objects (`elementList`, `activeElement`), which no API promises. It is
isolated in one function in `appModules/excel.py`, wrapped so that a change in NVDA costs only
the following and never the step, and logged once when it fails. See risks.

## Build order

1. **Read.** `ChartDefinition`, `ExcelChart`, the overlay and `chartOf`. Unit tested with a fake
   `officeChartObject`: series, values with gaps, a formula with and without a category range,
   axis titles, and a dead chart raising `COMError`.
2. **Draw.** `offerForChart` and the chart command on a chart. Unit tested for each mapped type
   and each refusal. Hardware check: a line chart, a single series column chart and an OHLC
   stock chart from `charting.xlsx`, compared against charting the same cells.
3. **Follow.** `markPoint` in `GraphicsMode`, the check in `event_gainFocus`, and the level
   following the series. Unit tested with fake point objects. Hardware check: arrow through a
   line chart's points and feel the mark move, across a page turn, then up arrow to another
   series and check the level line follows it.
4. **Lead.** NVDA following the stepper and a routing press. Hardware check: step with Space and
   dot 4, then press right arrow, and hear the point after the one stepped to.

## Testing on hardware

The charts used are added to `charting.xlsx` beside the sheets already there: a line chart of
the Bollinger sheet (four series), a column chart of a short single series, and an
open-high-low-close stock chart of the OHLC sheet. Each check is done on the Monarch with the
focus on the Focus 80, and again with the Monarch alone, since the braille line beside the
drawing is the focus line in the second case.

## Risks

1. **NVDA's chart module is private.** `_msOfficeChart` has a leading underscore, and the
   following reaches into `elementList` and `activeElement`. Both are imported and touched in
   `appModules/excel.py` only. The constants are copied as a fallback if the import fails,
   since they are Excel's numbers and do not change; following is simply off if the attributes
   are missing.
2. **Point numbering.** The plan assumes NVDA's point numbers are the categories in order, which
   holds for the chart types mapped. It does not hold for a chart with hidden or filtered rows
   if Excel's `Points()` and `Values` count differently; checked in step 2 on hardware.
3. **UI Automation for Excel.** With that setting on, NVDA does not build `OfficeChart` at all,
   and this does nothing, the same as the sheet seam today.
4. **Big charts.** NVDA's series object builds one point object per point when the reader lands
   on the series. That is NVDA's cost, already paid by every NVDA user, and nothing here adds to
   it.

## Open questions

1. **High-low-close charts.** Decided 8 October 2026: price bars with no open tick. See
   "What gets drawn".
2. **Charts in Word and PowerPoint.** NVDA uses the same chart objects there. The reading in
   `ExcelChart` is Office-wide except the category text, which uses Excel's ranges. Worth doing
   once Excel is on hardware, through those applications' own modules.
3. **Which way the level line goes on the chart elements.** When NVDA is on the "Chart
   Elements" group, should anything be shown? Probably not; left as it is.
4. **Following when the drawing is not of the focused chart.** A reader could have one chart on
   the pins and arrow through another. The key check means nothing happens. Saying so once
   might help; decide on hardware.

## Out of scope

New drawings: multi-series columns and bars, stacked columns, scatter, pie and area. Each needs
its own answer to what a finger can tell apart, and gets its own plan. A scatter chart in
particular plots pairs of measured numbers, not values over categories in order, so points can
share a pin column and "next point" in row order would jump about the panel. Stepping, panning
and the one pin per point zoom all assume points in order, so a scatter chart is a new kind of
drawing rather than a variant of the line chart.
