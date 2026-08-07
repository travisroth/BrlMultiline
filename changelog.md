# Changelog

## 0.1.0, in development

First version built against NVDA 2026.3, replacing an earlier prototype written for
NVDA 2023. 2026.3 is the first release to carry the `braille` package that this add-on is
written against, so until it ships, installing and testing means running an alpha snapshot.

Added:

- Divides a braille display into independent segments, each with its own content and its
  own scrolling. Segments are groups of whole rows on a multi row display, and column
  slices on a single row display.
- Per display configuration: segment count or explicit segment sizes, and which segment
  follows the focus.
- Reversed panning keys, stored per display. NVDA has no such setting of its own.
- Per segment panning commands, unbound by default.
- Pinning the navigator object to a segment, so it stays visible while the focus moves.
- Optionally filling the segments around the focus segment with the document lines above
  and below the caret.

Not yet verified on hardware. See `docs/design/port-plan.md`.
