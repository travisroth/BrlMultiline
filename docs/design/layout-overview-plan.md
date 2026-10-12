# Layout overview: where things are on the page

Read [tactile-graphics-plan.md](tactile-graphics-plan.md) first, since this feature draws
through the graphics mode it describes, and the browse mode sections of
[nvda-api-notes.md](nvda-api-notes.md). This document proposes a picture of a page's or a
window's main areas, drawn as labelled boxes in their real positions. The probe in
`spikes/layoutOverviewProbe.py` has been run on nine pages and has answered the open questions.
The region rules (step 2 of the order of work) and the text description (step 3) are built in
`layoutRegions.py` and unit tested against those pages; nothing yet runs inside the add-on.

## The problem

Browse mode presents a web page as one line of reading order. That is the right way to read
a page, but it loses the one thing a sighted reader gets at a glance: the shape of the page.
Is the navigation a bar across the top or a column down the left? Is the page in two columns
or three? Where is the search field? A braille reader learns none of this from reading order,
and it matters in three situations:

1. Talking with other people. "It's in the top right" or "use the menu on the left" means
   nothing if you don't know what is in the top right.
2. Accessibility testing. Whether a layout makes sense, whether content sits outside every
   landmark, and whether the reading order matches the visual order are all questions about
   positions.
3. Making a page yourself and wanting to check, roughly, that it looks the way you meant.

Reformatting the page into its visual layout is not the answer. Even a Monarch has nowhere
near the space for that. What fits is an overview: a few boxes, each named, in roughly the
right places relative to each other.

## What it looks like

A drawing on the pin surface. Each main area is a rectangle outline with a short braille label
inside, for example: a tall narrow box down the left labelled nav, a small box at top right
labelled srch, and a large box filling the rest labelled main, divided inside into two column
boxes labelled bkmk and cont. The drawing shows one or two levels of the containment tree at a
time, never the whole tree, and never more than about a dozen boxes.

The interaction does most of the work, not the picture:

1. Touching a box, or pressing a routing key on it, speaks and brailles its full description:
   "navigation, Main menu, 14 links, left side, full height".
2. Activating a box moves the browse mode cursor to the start of that area and leaves the
   overview. That turns the overview into a way to navigate by position.
3. Drilling into a box redraws the overview with that box as the whole surface, showing its
   children. Backing out goes up a level.
4. A key switches between the visible screen and the whole page. The visible screen is the
   default, because "top right" in conversation means the top right of what is on screen. The
   whole page is squeezed vertically, and says so.

Displays that cannot draw get the same data as text, in reading order of position: "Banner
across the top. Search at top right. Navigation down the left, full height. Main content in
the center, in two columns: Bookmarks, then Content. Footer across the bottom." That also
works in speech, so the feature is useful with no multi-line display at all.

## Finding the areas, without AI

Everything here runs on the device, from what the browser already reports to NVDA. An option
to send a picture to a model for a description may come later, but it is out of scope here.

### Positions

Every NVDA object has a `location`, its rectangle on screen, and browse mode content is no
exception: a quick navigation item's `obj` is an IAccessible object made from the buffer node's
identifier (`getNVDAObjectFromIdentifier`), and its location comes from `accLocation`. Browsers
also report rectangles for content scrolled out of view, above or below the viewport, which is
what makes a whole page view possible. All coordinates are taken relative to the document
root's own location, which is the visible part of the page.

The cost is calls across processes. Making the object, reading its location and reading its
attributes is three calls into the browser per element. That is nothing for thirty landmarks
and too slow for three thousand links, so the design asks for rectangles of big things first
and only goes finer when it has to. How fast this is in practice is open question 4.

### Signals, most trusted first

1. Landmarks and the HTML5 elements that map to them: navigation from nav, banner from a top
   level header, contentinfo from a top level footer, main, complementary from aside, search
   from the search element or role search, and form and region only when they have a name.
   NVDA already finds these: the Elements List uses `_iterNodesByType("landmark")` on the
   virtual buffer, and so will this.
2. Other structure that is not a landmark: search fields (text input type search, or a field
   whose name, placeholder, id or class says search), headings of levels 1 to 3, frames, data
   tables and articles. A heading inside a box is also the best source of that box's name.
3. Content patterns, taken from reader mode and boilerplate removal, which have worked without
   machine learning for twenty years. Link density, the number of links per hundred characters
   of text, is the strongest: a block that is mostly links is navigation, a block of long
   paragraphs with few links is content. A short block at the bottom with many small links or a
   copyright line is a footer. Link counts are cheap, since they come from the buffer alone with
   no browser calls: the probe collects every link's offset once and counts them by offset range.
4. Position. A full width band at the top is likely a banner, a tall narrow block at either
   side a sidebar, the largest central block the main content. Weak alone, it settles ties.
5. Class and id names: nav, menu, sidebar, header, footer, search, content, main and the like.
   Two doubts make this the weakest signal until the probe says otherwise. Whether browsers
   expose class at all through IAccessible2 is open question 2, and whether the layout divs that
   carry those names survive into the accessibility tree, rather than being pruned as generic
   containers, is open question 3.

### When there are no landmarks: splitting by geometry

Many pages have no landmarks, or one main around everything. For those, the classic method is
recursive XY-cut: take the rectangles of visible content, find the widest gap of empty space
that runs right across or right down the region, split there, and repeat on each part until the
parts are small or no clean gap remains. VIPS, vision based page segmentation, refines the same
idea with visual separators. Both are plain geometry. The parts are then named by signals 2 to
4.

The rule for using it: when the landmarks found cover less than about half the page's area,
fall back to geometry for the uncovered part. A geometric block outside every landmark is drawn
as an unnamed region, which is exactly what axe's region rule reports. That makes the overview
an accessibility check as well as a picture, with no extra work.

The input is the rectangles of leaves: links, headings, graphics and form fields. How many can
be fetched in an acceptable time decides whether this runs on the whole page or only on what is
visible, and is part of open question 4.

## From areas to a drawing

1. Build the containment tree from buffer offsets, which nest, rather than from rectangles,
   which overlap when a page uses sticky headers or negative margins.
2. Drop what is too small to matter, merge siblings that are too small to label, and pick the
   level to show so that there are no more than about a dozen boxes.
3. Scale to the pin surface and snap to it, keeping relative positions rather than exact
   proportions. Every box has to be large enough for its label. On the Monarch a braille cell
   takes 3 pin columns and 4 pin rows, so a three letter label needs a box about 11 by 6 pins
   with its outline. Boxes too small for that are grown, and their neighbors pushed, before
   anything is drawn.
4. Label the way tactile graphics transcribers do. A short label goes inside the box when it
   fits. When it does not, a number does, with a key available from the overview; BANA's
   tactile graphics guidelines use the same approach.
5. Draw with what graphics mode already has: rectangle outlines and `labelCells` for the
   labels. Touch at pin resolution, which the Monarch driver provides, maps a touch back to the
   box under it.

## Windows applications

Windows applications are easier than web pages. UI Automation gives every element a bounding
rectangle, and applications use roles that already say what an area is: menu bar, toolbar,
ribbon, tree (usually a navigation pane), list, document, tab and status bar. A walk of the
foreground window's children, a few levels deep, gives File Explorer as an address bar and
search at the top, a navigation pane on the left, the file list in the center and a status bar
at the bottom. The source differs; the tree, the layout and the drawing are the same code. Web
first, since it is the harder case and the one asked for.

## Testing

`layoutOverviewProbe.save()` writes a JSON file with the candidates, the accessible tree and
the leaf rectangles for one page. Saved from a handful of real sites, those files become unit
test fixtures: the naming rules, the geometric split, the containment tree and the layout onto
pins can all be tested offline against real pages, with no browser and no NVDA. That is the
same pattern the chart work uses.

## Order of work

1. Probe. Run `spikes/layoutOverviewProbe.py` on Chrome and Firefox against five or six sites
   chosen to differ: a news site, GitHub, a shop such as Amazon, a WordPress blog, an older site
   laid out with tables, and a site with a sticky header. Save each. Answer the open questions
   below and record the answers here.
2. Data model and rules, pure Python with no NVDA imports: regions, the containment tree,
   naming, and the geometric fallback, tested against the saved fixtures.
3. Text summary. The spoken and brailled description from the same model. Useful on every
   display, and the cheapest way to judge whether the rules find the right areas before any
   drawing exists.
4. Drawing. Layout onto pins, labels, and the key, through graphics mode.
5. Interaction. Touch to describe, activate to move there, drill in and back out, screen and
   page views.
6. Windows applications, through UI Automation.

Steps 1 to 4 are done, and the drawing has been tried on a Monarch: see "The drawing and the
collector as built" below. Step 5 is built: a press naming the box under it, a second press
moving to it, and drilling in and backing out.

**Drilling in and backing out** goes into the area last pressed. A press records the area
(`_LayoutView.pressed`, set as the drawing describes the point), and dot 8 redraws the overview
with that area as the whole (`compose`'s `focus`, with `focusWindow`): its inside drawn three
levels deep, grown taller if it needs to be, positions said within it, and on the screen view
only the part of it on screen. Dot 7 goes back along the trail of areas drilled through, to the
whole page or screen, and leaves the area it came out of as the one pressed, so dot 8 goes
straight back in. Both redraw from the same reading of the page rather than reading it again.
The keys are dot 8 and dot 7 alone in the Monarch's graphics layer, enter and backspace when
typing braille, since nothing is typed into a drawing; shipped revision 6 gives them to graphics
layers saved before. With another drawing up, or nothing pressed, or an area with nothing inside,
they say so.

**Moving to an area** is a second routing press in the same box. The first version wanted the
same cell within NVDA's multiple press timeout, and never fired on a Monarch: a second routing
press there is slow, and a finger seldom comes down on the same cell twice. So graphics mode now
records what a press landed on (`GraphicsMode._isSecondPress`), and the next press acts if it
lands on the same thing, with no time limit; a press anywhere else describes that and becomes the
new first press. The same thing is the same area where the drawing can say (`Drawing.areaAt`; the
overview's is the region, so it holds across zooms and moves) and the same cell where not. NVDA's
repeat count would not do either way, since every routing key is the one script to it. A drawing
opts in with `Drawing.activateAt`, and every other drawing goes on describing on every press.

The overview's moves the browse mode cursor to `Region.start` the way quick navigation does
(`_set_selection` with the quick navigation reason) and says the area's name and then the line.
The drawing comes down first only when its display also shows the focus
(`GraphicsMode.holdsFocus`), as one display doing everything does. With the focus on another
display, such as a Focus 80 beside a Monarch, taking the drawing down would do nothing for the
reader, since the line arrives on the other display, and would lose the drawing; so it stays up
for the next area. It refuses, saying why, for an area with no start (found from the accessible
tree, on a page without landmarks) or when the page under the focus is no longer the one the
layout was read from.

## The drawing and the collector as built

`layoutCollect.py` gathers the rules' input live, in the shape the probe saves, so what the
add-on finds on a page is what a saved copy gives. It works in batches of 25 calls into the
browser with a return to NVDA's message loop between, and stops at limits: 300 leaves, a tree of
600 objects 12 deep, and 8 seconds in all. Two savings on what the probe does: headings of level
3 are not located, and a button is only located when its label in the buffer says search.

`layoutDraw.py` draws the regions on a buffer, pure Python like the rules. The decisions:

1. Each axis is a warped ruler. Every box's edges are marks on it; the stretches between marks
   start in proportion to their length and boxes too small for a label are grown out of the
   others. Order and adjacency are exact, sizes only as true as the panel allows.
2. Two levels are drawn: the main areas and the areas inside them. A box whose inner areas
   cannot each be made big enough is drawn without them, and the layout is done again with the
   room that frees.
3. A label is the kind's short word (nav, main, side, foot, find) in the reader's braille table,
   in the first corner of the box clear of the boxes inside it. A box with no room for its word
   gets a key letter, written in uncontracted letters so a contracted table cannot turn it into
   a word sign, and the key is spoken after the drawing.
4. A press names the innermost box under it, with where it is on the page and all it holds.
5. Where a box already has room, the link count a press would say is drawn with the label:
   on its line after a blank cell first ("nav 8 links", which reads as one phrase), else a line
   of its own a blank row under it (or over it, for a label in a bottom corner), clear of the
   boxes inside. Boxes are never grown for it: the rulers are laid out for labels alone, and a box
   without room keeps its label or key letter and leaves the count to a press. Asked on
   hardware, because a press is a question the reader has to think to ask.
6. **What a box holds, as counts.** Every area counts its links, form fields, buttons, tabs and
   the expandable headers of accordions ("sections"). The collector counts them exactly from
   browse mode's text, where each starts, with no call into the browser; a button among the
   form fields is counted as a button. Blocks found by geometry count what was sampled in them,
   which on a busy page is fewer than there are. The order shown and said is most telling
   first: tabs and sections say what kind of area it is, fields and buttons what can be done in
   it, links where it leads (`Region.counts`). A box shows as many as already fit, in that order,
   on the label's line and then on lines under it, and stops at the first that will not fit so
   that a count is never shown while a more telling one is hidden. A press says them all.
7. **Tab strips and accordions are areas** (`_widgets`), asked for on hardware. Two or more tabs
   in a run along a row or down a column are a tab strip, drawn around the tabs. Two or more
   expandable buttons stacked with their left edges together and much the same width are an
   accordion, drawn from the first header to the last so it holds whichever sections are open;
   expandable buttons in a row, such as a site's menus, are not. The collector locates up to 40
   tabs and 60 expandable buttons, which browse mode marks as collapsed or expanded. The area
   around a strip or an accordion does not count its tabs or sections again, and a strip shows
   just its number, since its label says tabs.
8. **A form with three or more fields is an area** even without a name (`FORM_MIN_FIELDS`), named
   by its first heading. A form around one field, or around a whole page with a search box in
   it, is still not.

9. **The screen view grows rather than squeezes** (`composeFitting`), found on the Wells Fargo
   sign on page in a maximized window. Squeezed onto one panel it left out the header's
   navigation, the sign on form and two areas beside it, and said nothing, which reads as
   suspicious to a braille reader who knows braille takes more room than print, and defeats
   checking what a screenshot shows. Now a view that does not fit is drawn half a panel taller at
   a time, up to three panels, until nothing in it is left out or drawn too small, and opens at
   its top like the whole page. What still does not fit is counted and said
   (`Overview.unreadable`). Three things made it work: a box inside another asks the ruler for
   the two pins the outline around it takes, at every level, without which a banner's
   navigation was too small at any height; three levels are drawn instead of two; and forms,
   search, tab strips, accordions and landmarks found deeper than that are drawn inside the
   deepest box that is (`MAJOR_KINDS`), since splitting by gaps nests blocks in blocks and buried
   the form four levels down. On the saved pages, Wells Fargo now fits in a panel and a half,
   WSJ in two and the inaccessible demonstration page in a panel and a half, nothing left out.

The plugin's three commands are unbound: draw the screen's worth, draw the whole page, and show
the overview as text with `ui.browseableMessage`. Gathering is cancelled by anything else that
cancels a pending picture.

**Tried on a Monarch, on Fabula Coffee.** The screen view and a press naming the box under it
worked. Two things did not, and changed the design:

1. The whole page squeezed onto one panel kept only its four main areas; everything inside
   main was dropped as too small to find. Now the whole page is composed tall, a screen of the
   page to a panel's height, so the areas inside have room, and it opens a panel wide at its
   top (`Drawing.opensAcross`, new in graphics mode). Two new graphics commands move a whole
   panel up or down (`GraphicsMode.panelStep`); the old ones still move a quarter. They are on
   the Monarch's panning keys in the graphics layer, which did nothing while a drawing was up
   and are the keys a reader already uses to move through text a display at a time; shipped
   revision 5 gives them to graphics layers saved before. Shrinking still shows the whole page
   squeezed.
2. Zooming magnified the drawing, braille labels and all, into blobs of dots. Now the overview
   supplies `Drawing.redraw`, as charts and pictures do, so a zoomed or moved view is composed
   again at the panel's own size with labels at their own size. The view's window is read back
   through the rulers of the tall drawing (`Overview.windowAt`), so a zoom shows exactly what
   was under the hand. An area keeps its key letter in every view, and each view's key is its
   note, said after every move.

A general answer for drawings that are magnified rather than redrawn, such as the glyph
catalogue, would keep labels in a layer of their own that is placed at the magnified position
but drawn at the panel's size. Not built: everything with labels that is used while reading
already redraws.

The screen view draws each region where it is on screen (`Region.screenBox`), so a sticky
header is at the top, and what it covers is drawn from below it. When even the main areas will
not all fit, the smallest unnamed area found by geometry is left out first, since the text
overview still lists it.

Known gaps in this step: regions from the accessible tree, on pages without landmarks, have no
start, so they cannot be moved to.

## The region rules as built

`layoutRegions.py` is pure Python. Its input is a tree of `Node`, what the browser reported
about each element; its output is a tree of `Region`, each an area with a kind, a label and a
box on the page. `describe` turns that into the text overview, and `fromProbe` builds the input
from a file the probe saved. `tests/unit/brlMultiline/test_layoutRegions.py` holds a test per
rule and a test per saved page, and the pages are copied into `tests/fixtures/layout`.
`probe.overview(focus)` runs the same rules live on any page in NVDA, and `probe.save` now puts
the overview at the top of its report, so a new page can be judged without a unit test.

The rules are listed in the module's docstring in the order they run. Four decisions in them
came from pages breaking an earlier version, and are worth keeping in mind when changing them:

1. A landmark keeps its own rectangle, but anything else is drawn around its content. Seeking
   Alpha's sidebar is a full height column with its links at the top, and a sighted reader sees
   the column. The favorites page's form is as wide as the page with its content in 320 pixels
   at the left, and a sighted reader sees the narrow column.
2. The geometric split cuts around regions already found, keeping them whole, rather than
   ignoring them. Ignoring them gave blocks that overlapped the tables in Seeking Alpha's main.
3. A gutter may be crossed by 3 percent of the items around it, and a part's box comes from
   what lies wholly inside it. WSJ's lead picture spans two of its four columns, and without
   both of these the two columns merge, or one is drawn twice as wide as it is.
4. Beside the page's own landmarks (banner, navigation, main, complementary, content info),
   only the content outside every landmark is split by geometry, and the areas found are set
   beside the landmarks. Splitting everything wrapped WSJ's landmarks in invented header and
   main boxes; not splitting at all lost whatever no landmark marked.

A review of the first commit found six faults, each now a test in `TestReviewFindings`:

1. One landmark hid all unmarked content, since geometry was skipped beside any landmark. Now
   the uncovered content is split on its own, as in point 4 above. On WSJ this finds two strips
   of links the landmarks leave out, one between the top navigation and the banner and one
   under the banner.
2. A cut could leave a region found already outside the block it was put in: a wide table was
   left out of the gutter search like a wide picture. Regions are now barriers that no cut may
   cross, and a block's box always includes the regions in it. On Seeking Alpha a wide table
   now stops the two columns at the top of main being cut down the page's whole length, so
   they are found one level down, in the band above it.
3. Sticky header repair moved any element that came before its neighbor in reading order but
   after it on screen, with a gap the right size. Ordinary sections shown out of reading order
   were moved, hiding the very mismatch a tester wants to find. Now the element must also be
   pinned within 120 pixels of the top of the screen and look like a header: a banner or
   navigation landmark, a header or nav element, or an id saying so.
4. Repeat suppression dropped any six unnamed areas of one size, including six titled
   sections. Now only rows and list items with no heading of their own count as records.
5. One wide hidden control at minus 9999 set the scroll estimate. Now only containers count,
   a top with nothing starting within a screen of where it ends is an outlier, and a caller
   that knows the scroll can pass it.
6. "Research" matched search. Matching is now by word: a word that is search or starts with it,
   from names split at spaces and identifiers split at case changes and punctuation. The rules,
   the collector and the probe share the one function, `mentionsSearch`.

A second review, of those fixes, found three more, each a test in `TestReviewFollowUps`:

1. Content on every side of a landmark could still vanish. The fix for the first fault divided a
   block crossing a landmark only at its top and bottom edges, so content to its left and right
   stayed one block that crossed it, and was dropped. Now a crossing block is divided into what
   is above, below, left and right of the landmark, and again around any other it still
   crosses. Content is divided, never dropped for its bounding box.
2. Areas found by geometry inside a sticky header had no node, so they missed the header's move
   and their screen boxes were a page's scroll above the screen. Now an area without a node
   takes the move of the region holding it.
3. A block found by geometry could start after its first child: its start was taken from its
   children before the children's own starts were set from their nodes. Now regions take their
   node's start when they are made, keep the earlier start when they collapse, and every start
   is settled bottom up at the end, as the earliest of a region's own and its children's.

A third review, of the live collection and drawing at commit 7808507, found six more. The
reviewer's own tests for them are kept as `tests/unit/brlMultiline/test_layoutLiveReview.py`:

1. An old drawing could move into a buffer that had been replaced: browse mode can read a page
   again, or a page can become another, under the same document object. Now the layout records
   the page's address and its buffer when it is read (`_pageIdentity`), every element keeps
   browse mode's identifier for it, and an area keeps the identifier of the element it starts at
   (`Region.anchor`). A move refuses another page, looks the element up where it now is, and
   refuses if it is gone; an offset alone is used only while the buffer is the one it came from.
2. A form wholly under a sticky header was drawn filling the main area: the main area was cut
   below the header but what was inside it was not, and a box outside its parent was stretched
   to the parent's whole inside. Now what can be seen of an area is clipped to what can be seen
   of the area around it, all the way down (`_clipInside`), and a box outside its parent is not
   drawn.
3. Tabs were counted as fields: browse mode's form fields include its tabs as well as its
   buttons. Now both are taken out of the count, and a sampled tab stays a tab.
4. Blocks made beside landmarks counted their links and not their fields or buttons. Both ways
   blocks are made now share one function (`_fillBlock`).
5. The form role alone, without the landmark attribute some browsers add, did not get the form
   rule. Now both go through `_classifyForm`.
6. Tabs each in a plain wrapper, such as a list item, made no strip. Now tab strips and
   accordions are looked for through plain wrappers, which are absorbed into what is found;
   tabs inside a strip found further down are not found again a level up; a tab saved twice is
   one tab; and a named area that is all tab strip keeps the strip inside it rather than merging
   it away.

The first review asked for three more things, done as well: regions carry where they start in
browse mode's text (`Region.start`) for a later command that moves to one; the collector shares
its leaf budget out across links, headings, pictures and fields, spread through the page,
instead of taking links first; and the collector reports what it cut short, which the plugin
says after the overview unless it was only the normal sampling of a busy page.

Known weak spots, from the same pages:

1. Long pages give many bands. A cut makes at most five parts, at the widest gaps, and each is
   cut again, but Fabula Coffee's main still reads as five bands of three or four blocks each.
   That is a fair picture of a long shop page, but the drawing will have to show one screen of
   it at a time.
2. Geometric blocks are named by their first heading, which on a shop or news page is often a
   product or a story rather than the block's subject. Fine for telling blocks apart, weak for
   knowing what one is.
3. The tree path depends on how deep the tree was walked. The inaccessible demonstration page's
   navigation links are 13 levels down, so at 12 its columns are found but their link counts
   are zero.
4. Only Chrome and Firefox on Windows have been seen, and only nine pages.

## Probe findings so far

The first run was fabulacoffee.com in Chrome, a Shopify shop, at 1920 by 911, scrolled down one screen.
Saved in `spikes/layoutProbeOut`. One site is not a pattern, so these are leads, not answers.

1. Rectangles are good, and scrolled out content is reported: the page measured 8.3 screens,
   with the footer at 6871 pixels and the announcement bar at minus 912.
2. Sticky headers report their position on screen, not on the page. The banner said top 0 while
   everything else said the page was scrolled 912 pixels. In page coordinates it lands on top of
   main. The way out is in the data: in reading order the banner comes after the announcement
   bar (page top 0, height 35) and before main (page top 128), and the gap between them, 93
   pixels, is exactly the banner's height. Rule to try: an element whose page rectangle collides
   with what comes after it in reading order is fixed or sticky, and goes in the gap before it.
3. Chrome sends class, tag and display for almost everything (class on 33 of 36 objects), id
   less often, and xml-roles only on landmarks. Layout divs are not pruned: the tree walk found
   199 of 300 objects with a class, including the header's inner wrappers.
4. Class names are as weak as expected. They are mostly BEM and framework names (shopify-section,
   swiper-slide), and the hint words misfire: banner-item__title is the hero image, not the
   banner landmark, and slide-content is a review carousel. Use them only to break ties.
5. Search was a button that opens a drawer, not a field, so the first version of the probe
   missed it. The probe now counts a button whose name, id or class says search.
6. Link density separates navigation from content cleanly here: banner and navigation about 8
   links per 100 characters, footer about 3, main about 0.2.
7. Inside main the real structure is a stack of full width sections, each a section element
   with an id, plus a five across product carousel. Headings level 3 were mostly product card
   titles and are noise for the overview. The overview of this page is: announcement bar, banner
   with navigation and search, a stack of bands in main, footer.
8. Cost: about 6 milliseconds per object located (36 in 208 milliseconds). Leaves were 154 in
   0.84 seconds, and the tree walk 300 objects in 2.7 seconds. Landmarks and headings are
   affordable on demand. A geometric split of a busy page with a thousand links would take
   seconds, so it has to be limited to the visible part, or to the parts landmarks do not cover,
   or done in stages. A full tree walk is too slow to do on a key press.

A second pair of runs on the same page, in Chrome scrolled as before and in Firefox at the top
of the page, settled three things:

1. The sticky header rule holds. In Firefox, unscrolled, the banner reported top 35 and main
   top 128: the announcement bar's height, then the banner's 93 pixels, exactly the gap
   predicted from the scrolled Chrome run.
2. Firefox gives the same rectangles to within a few pixels, and the same tag, class, id and
   display, but none of Chrome's extras (name-from, text-align, the container live region keys).
   It adds child-item-count. So tag, class, id, display and xml-roles are the common set.
3. Firefox prunes more layout divs than Chrome: 34 divs in the first 300 objects against
   Chrome's 64. The header's wrapper div with its long class list is missing in Firefox, its
   children moved up a level. Rules must not depend on a particular wrapper being present.

Checking buttons for search added cost: 61 to 66 objects located instead of 36, because every
button is located before its name is checked. The overview itself should check the buffer's
label first, which costs nothing, and locate only the buttons that pass.

Three more Chrome runs: wsj.com, seekingalpha.com, and the author's hand written favorites page
(a local file with no classes, ids or landmarks beyond an unnamed form).

1. Seeking Alpha is the textbook case, and landmarks alone draw it: complementary as a 240
   pixel column down the left for the full 16,000 pixel page, with navigation inside it; banner
   across the top to the right of it; main filling the rest. Inside main, two pairs of tables
   sit side by side at the same top (left edges 296 and 920), which is a two column band found
   from rectangles with no help from markup.
2. Landmarks need filtering. Seeking Alpha's Highcharts charts are named regions, so the chart
   and each series inside it came through as landmarks; their tags are div and the SVG g.
   WSJ has nine navigation landmarks inside main that are 11 pixels tall with no links, the
   breadcrumb labels on stories. Rules to try: drop a landmark smaller than a label could
   ever fit in, drop a navigation with no links, and drop regions whose tag is an SVG element.
3. Visually hidden things report real rectangles. Seeking Alpha's h1 is 1 by 1 (class
   sr-only). WSJ's skip to search button sits at top minus 939 on a page that was not scrolled.
   Anything 1 or 2 pixels in size, or wholly above the top of the page, is hidden and must be
   dropped. That needs the scroll position, which the browser does not report. Estimate it
   from the topmost sizeable landmark: when that is at 0 or below, the page is at the top.
4. WSJ's front page is four columns of stories inside main with no markup for the columns.
   The leaves show them plainly: left edges cluster at about 320, 640, 960 and 1280 pixels and
   right edges at about 600, 920, 1240 and 1560, so there are 40 pixel gutters between them. A
   geometric split would find these. Landmarks alone would draw main as one box.
5. Iframes are mostly ads. WSJ's two frames are an advert and a subscription banner. Draw a
   frame only when it is large, and say "frame" rather than guessing.
6. Containers are bigger than their content. The favorites form is 1889 pixels wide, but
   everything in it is in a 320 pixel column at the left. A box must be drawn at the union of
   its content's rectangles, not its own rectangle, or the overview would show a full width
   box where the page has a narrow column.
7. Class names: Tailwind utility classes (Seeking Alpha) are layout instructions, not names,
   but some are telling, such as sticky and top-0 on the banner. WSJ's are generated
   (css-100otit-MainHeader), though the component name after the hash is readable. The
   favorites page has none. That page also shows why the fallback matters: its only landmark is
   an unnamed form, and the structure is headings with lists of links under them.
8. Cost on busy pages: 109 and 113 objects located in about 0.65 seconds, most of them
   headings level 3 (64 on WSJ). Leaves hit the 400 limit, at 2.1 to 2.3 seconds. Locating
   level 3 headings should be left until a box is drilled into, and the geometric split must
   work on the visible part, or on one landmark at a time.

Three runs of old style pages in Chrome: Hacker News, and the W3C Before and After Demonstration
home page in both versions. None has a single real landmark, only unnamed forms.

1. Without landmarks the candidate pass finds almost nothing: one table and a form on Hacker
   News, a single heading on the inaccessible page. Everything there has to come from the tree
   and from geometry. These pages are the case for the fallback, not an edge case.
2. Hand written ids are the best signal on hand built pages. The accessible version is laid out
   with divs whose ids are header, main, nav, content and footer, inside page and border
   wrappers, and that is the page's structure exactly: header across the top, then a 137 pixel
   nav column and a 603 pixel content column, side by side. Generated class names were weak on
   modern sites; plain ids on old ones are strong. Score ids above classes.
3. Container rectangles lie here too. The accessible page's footer div reports the same top as
   main and 637 pixels of height, because main's columns are floated and the footer starts
   behind them. Drawing at the union of content, as already decided, fixes it.
4. The inaccessible version is nested layout tables, and the probe's tree walk stopped at depth
   5, just at the inner 801 pixel table, so its rows and cells were not seen. Its leaves still
   show the layout: left edges cluster at about 560, 720 and 1180 pixels, a left navigation
   column, the content, and a right column. Geometry finds what markup hides.
5. Layout tables never reach the candidate list, because NVDA leaves them out of table
   navigation by default (`includeLayoutTables`). On Hacker News the outer hnmain table, 1607
   pixels wide and centered, is a layout table; the story list inside it was judged a data table
   and came through. For the overview the outer table is a structure worth drawing, so tables
   have to be read from the tree, not only from quick navigation.
6. Hacker News's search field has no name, placeholder, id or class. The only clue is the text
   "Search:" beside it inside its form. A field whose form's own text says search should count.
7. Off screen skip links report absurd rectangles: list items 15,994 pixels wide, and links at
   left minus 15,440. Anything that extends far past the page's edges is hidden and dropped.
8. Hacker News's shape is simple and geometry agrees: an orange bar across the top 24 pixels
   tall, a single column of stories down the left of a centered table, and the footer with the
   search form below. Its leaves' left edges sit between 140 and 440 pixels with nothing to the
   right, which is a single column.

The inaccessible page again with the tree walked 12 deep (219 objects in 1.5 seconds) settled
the last question: Chrome keeps every nested layout table, row and cell as an object with a
rectangle. Four tables deep, the page's whole layout is there:

1. A centered 801 pixel table holding a header row (the logo cell 444 pixels wide, a picture,
   and a QUICKMENU cell at the right) above a row of three cells: a 156 pixel navigation column
   at the left, a 435 pixel content cell in the middle, and a 152 pixel column at the right
   holding four stacked promotions.
2. Inside the content cell, three news headline divs sit side by side at the same top, with
   three images under them and a news bar below: a three column band, from rectangles alone.
3. The ids help here too: the content cell is td id main, and inside it div id content.

So a layout table needs no geometric split at all: a row with several sizeable cells is a set
of columns, and a table with several sizeable rows is a stack of bands. Table structure is the
first thing to try on a page with no landmarks, and XY-cut is the fallback after that. The walk
only has to visit tables, rows, cells and divs, never text and links, which keeps it near half
of the 219 objects visited here.

## Open questions, for the probe to answer

1. Rectangles. Do Chrome, Edge and Firefox give sensible rectangles for landmarks, including
   ones scrolled out of view? Does a sticky or fixed header report its on screen position or its
   place in the page, and does it change as the page scrolls?
2. Attributes. Which IAccessible2 object attributes arrive: tag, id, class, display,
   xml-roles, text-input-type? The probe prints every key it sees, with a count.
3. Pruning. Does a layout div with a telling class name remain an object of its own in the
   accessible tree, or does the browser remove it and keep only its children? `probe.tree()`
   walks the tree rather than the buffer to show this.
4. Cost. How long does one object and location take, and how does that grow with the number of
   elements? `probe.dump()` and `probe.leaves()` both time their calls. This decides whether the
   geometric fallback can work on the whole page or only the visible part, and whether any of
   it has to happen off the main thread.
5. Odd structure. How common are a nav nested inside the banner, a main wrapped around the whole
   page, or landmarks that overlap? The probe's indented outline shows nesting by offset.
