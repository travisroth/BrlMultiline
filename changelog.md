# Changelog

## 0.1.0, in development

First version built against NVDA 2026.3, replacing an earlier prototype written for
NVDA 2023. 2026.3 is the first release to carry the `braille` package that this add-on is
written against, so until it ships, installing and testing means running an alpha snapshot.

Added:

- Divides a braille display into independent segments, each with its own content and its
  own scrolling. Segments are groups of whole rows on a multi row display, and column
  slices on a single row display.
- Per display configuration: whether to divide the display at all, segment count or
  explicit segment sizes, and which segment follows the focus.
- A switch, per display, for turning the configured layout off and on without disturbing
  it, as a setting and as a command. While it is off the display behaves as it does without
  the add-on, and objects cannot be pinned. A view or panel activated by code is unaffected,
  since that is a claim about what the display is being used for rather than a preference.
- Reversed panning keys, stored per display. NVDA has no such setting of its own.
- A command that moves the focus onto another of several displays driven as one: a toggle
  when there are two, and a list to choose from when there are more. It keeps how far down
  its display the focus segment was, and stores the answer in the profile in force. A pinned
  object sitting where the focus is going trades places with it rather than being released,
  since pressing a key to move the focus is a decision about the focus and not about the
  pin. The segment the focus is leaving counts as somewhere to put it even when the flow
  band has claimed it, since the band follows the focus and gives those rows back, and a pin
  carried onto a segment the rebuilding dissolves is re-homed rather than released. Where
  there is nowhere to put what is displaced, or more than one thing is, the reader chooses
  what to keep or cancels the move. What became of a carried pin is reported after the move
  rather than promised before it, including when it lands somewhere with too few rows to
  read it as a flow — which a run of objects no longer needs, since one row of a chat, a
  list or a menu is one message, one item, one entry, with panning moving to the next. A
  document or a table still needs two.
- Per segment panning commands, unbound by default.
- Pinning the navigator object to a segment, so it stays visible while the focus moves. A
  pin survives a layout change as long as its segment does. A pinned segment can be panned
  line by line through the object, and does so without moving the caret, the browse mode
  cursor or the focus. A browse mode document is pinned through its tree interceptor, so a
  pinned web page covers the page rather than the one element the cursor was in. Pins are
  re-read several times a second, so a pinned status line or clock keeps up, and re-reading
  neither moves the reading position nor disturbs the window the user panned to.
- Optionally filling the segments around the focus segment with the document lines above
  and below the caret.

- Reading a document as a flow, on a band of the display: a heading followed by what comes
  after it, a paragraph running on across the rows, panning that moves a whole display's
  worth and is reversible, and a cursor move within what is shown moving nothing. Tabbing
  to something already on the display leaves the display where it is; only a jump by
  structure rehomes it. In browse mode, a control's prompt is placed above it, a field is
  separated from the next prompt, and the real edit control owns the active row so its
  caret and typed value stay live without losing the page around it. Standalone editable
  documents such as Notepad can use the same flow through a separate opt-in setting. Long
  edits keep the caret visible even beyond the renderer's 64-row working chunk. A multi line
  edit you are writing in is read as a document of its own, so the lines around the one you
  are on are on the display; leaving it gives the page back. Such an edit is cut into
  paragraphs rather than into wrapped lines, which is now a setting of its own — on by
  default, and worth turning off to try an editor that reads oddly. Every block is rendered
  by the same unit it was cut by, a wrapped paragraph whole rather than its first line; a
  line that transiently swallowed its successor stops truncating the edit once it reads
  cleanly again; and the reading allowance funds the full re-read a keystroke costs, so
  typing no longer fills the band with the marker meaning there is more unread. A walked
  block whose unit does not start where the walk landed is refused in both directions while
  writing, and a re-read that cannot restore the band's old top row reaches back for
  context above the caret instead of pinning the typed line to the top row. Editors NVDA
  knows are editable but whose roles and states do not say so — Windows 11 Notepad — are
  recognised by NVDA's own behaviour class, and an editable document counts as multi line
  without the state. The band's top row is only trusted when it is not the caret's own
  block, so a rich editor's transient merged answer at a return cannot become the window
  the reader is put back to; and a keystroke into an edit earns a settle pass a moment
  later, so a reader who pauses right after pressing return is not the one who has to
  supply the re-read that heals the display. Quick navigation keys all move
  the display: structural ones ground, and everything else brings what you jumped to into
  view. Off until it is turned on, per display and per configuration profile. Experimental.
- Reading a list, a menu, a tree or a combo box's choices the same way: the items around
  the one you are on, rather than that one alone. It never moves your selection — panning
  reads past where you are, and the cursor stays on the item the arrow keys will act on. A
  routing key does what a click would: it invokes a menu item, chooses a tab, and moves the
  focus to a list or tree item without selecting it. A menu's separators are drawn as a line
  of dots 7 and 8 rather than read out as unavailable commands. Off until it is turned on,
  separately from browse mode. Experimental.

- Tables in columns, with the layout remembered against the table. A command lays the table
  you are in out across the display, another moves through the pages of a wide one, and the
  first column can be repeated on every page so you know whose row you are reading. The page
  you turn to stays while the cell your cursor is in is on it, and the display comes back to
  your cursor when it moves somewhere the page does not show. What each
  column is called is taken from the table itself and can be held on a row above the band.
  Two more commands remember the layout for this table and forget it again: a remembered
  table comes up laid out with no keystroke at all, and turning the columns off leaves the
  memory alone until you come back to it. What is remembered is that this table is read in
  columns; which columns fit and how wide they are is worked out afresh for the display you
  are on. Web pages, File
  Explorer's Details view and Outlook's message list are all read this way.

- The columns of a table can be arranged, from the band or from a dialog. On the band: hide the
  column you are in, cycle it between wrapped, cut keeping the start and cut keeping the end,
  and give the table back as it comes. In the dialog: the columns as a list to reorder, a name
  of your own for any of them, a floor and a ceiling on its width, which end of its heading
  survives, where a page of columns begins, and which column is repeated on every page — with
  "the first column shown" as its own answer, since that is what a table does when you have
  named none. Every per-column question also has "follow the table setting", which names what
  the table is currently doing. What you arrange is what the remember command writes down, and
  the box that remembers it is a state rather than a button: unticking it on a table you had
  remembered deletes what was saved. The dialog opens on NVDA's event loop rather than inside
  the command, which is what stopped NVDA responding the first time it was opened.

- The cursor stays on the row you are on in a list. The live re-read replaces a row's
  region every couple of seconds, and a fresh region does not know it is the one you are on —
  so the cursor quietly disappeared from File Explorer and from the add-on's own dialogs a
  moment after you arrived. Worst on a flat list, where no indent marker is left to say which
  row is which.

- A list whose items are grouped reads as one run. Outlook's message list grouped by day
  used to end at each boundary, so panning stopped at the last message of the day; the walk
  now crosses into the next group, and the day heading is a row of its own. A closed group
  shows its heading and nothing under it.

- A message row in Outlook's list says what is true of *it*. NVDA builds part of such a row's
  name from whichever message is selected, so rows on the display could carry another
  message's "unread", "replied" or "forwarded". Rows you are not on are now built from their
  own fields instead; the row you are on still says everything NVDA says about it, which is
  where those flags are true.

For add-on authors and component work:

- Panels. Code can claim part of the display, subdivide it as it likes, and hand it back,
  without disturbing the segments it did not claim. `plugin.activatePanel(panel)` and
  `plugin.deactivatePanel(name)`.
- An application can declare that its objects read as a run, so they fill the display the
  way a list does, by setting attributes on its NVDA overlay class: `brlMultilineFlowRun`,
  and optionally `brlMultilineFlowNext`, `brlMultilineFlowPrevious` and
  `brlMultilineFlowAdmits` where the run is not a plain row of siblings. No import of this
  add-on and no dependency on it: an app module that sets them behaves identically when
  BrlMultiline is absent or disabled. A container holding such a run declares itself with
  `brlMultilineFlowRunContainer` and says where to begin with `brlMultilineFlowRunStart`,
  which is what lets an object pinned to a segment be the list rather than one of its
  members. A pinned flow is panned by the command for its segment: its rows come from its
  controller rather than from the buffer's window, so moving the window moved nothing and
  a pinned document, list or table would not scroll. Content that arrives after a run has
  been read to its end — a message in a pinned chat history — is now found: panning forward
  asks the source once more before refusing, and a pin whose reader can feel the last row
  that has been read asks on its own refresh tick. The tail of what has been read rather
  than a declared end, since a band that has just taken an arrival is full and open rather
  than ended, and watching for the end alone stopped watching after the first message. What
  arrives scrolls onto a full display, the oldest row moving off, while the reader is at the
  end of something whose end they have reached before — a setting per display and per
  profile, on by default, so a chat can be followed where a page that rewrites itself is
  not. Panning back into the history stops it and reaching the end again resumes it, and
  content that is merely longer than the band is never followed, so a pinned page cannot
  walk itself through a document on a timer. Editing the row under a caret that has not moved since a
  pan — forward Delete, or an editor rewriting the line — reads the band again when that
  row changed length, since every position below it has shifted; the band stays on the row
  the reader panned to. For controls an author does not own,
  `flowObjects.register` still takes a whole adapter.
- Wrapping and cursor routing behaviour are per segment, so a claimed area can behave
  differently from the display around it. Neither is exposed in the settings dialog.

Verified on a Focus 80 so far: configuration, two segment layouts, focus and caret
tracking, cursor routing, reversed panning, and pinning. Everything else is unverified. See
`docs/design/port-plan.md`.
