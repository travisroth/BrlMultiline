# BrlMultiline: instructions for agents

An NVDA add-on that divides a braille display into independent segments, flows documents
across rows, lays tables out in columns, and draws graphics on pin displays.

## Where things are

- `addon/globalPlugins/brlMultiline/` is the add-on. `__init__.py` is the global plugin.
- `docs/design/` is the project's durable memory: designs, decisions, hardware findings and
  test results. Start with `docs/design/README.md`, which gives the reading order.
- `tests/unit/brlMultiline/` is the unit test suite. NVDA is stubbed in `_stubs.py`.

## Checks

Run both before calling a change done:

- Unit tests: `uv run python -m unittest discover -s tests`
- Lint: `uv run ruff check .`

## Comments and docstrings

Code files hold the contract and the minimum reasoning needed to change the code safely.
Everything else belongs in `docs/design/` or in the commit message.

1. A docstring starts with a one-line summary of what the thing does or is. Then the contract
   where it is not obvious: `:param:`, `:return:`, `:raises:`.
2. A reason that is not obvious from the code gets one short paragraph at most, written as a
   fact about the code as it is now. A longer explanation goes in the relevant design doc,
   and the code points to it: "See docs/design/spatial-reading-plan.md, Cursor following."
3. No history in code. Do not write what a bug looked like, what an earlier version did, what
   a review or a hardware run found, probe output, or test results. Those go in the commit
   message, and in the design doc when they shaped a decision.
4. Say a reason once. Other places that rely on it say "See `name`" instead of repeating it.
5. An inline comment explains why, never what the next line does. Leave it out when the code
   and its names already say it.
6. An attribute docstring is one line unless the attribute has a rule a reader would break
   without knowing it.
7. Keep every `# Translators:` comment. NVDA's translation tooling reads them.
8. Docstrings and comments are not where descriptions shown to the user live. Script
   descriptions come from the `@script(description=...)` argument.

## Naming

NVDA's style: camelCase for functions, methods and variables, PascalCase for classes.

1. Methods and functions are named for what they do, as a verb or a verb and its object:
   `refreshActive`, `reportCarriedPins`, `fetchOne`.
2. Something that answers a yes or no question starts with `is`, `has`, `can`, `should` or
   `wants`: `isShowingTheTail`, `hasMoreToFetch`.
3. Do not name methods as sentences such as `_whatBecameOfTheCarriedPins` or
   `_caretIsWhereThePanLeftIt`. Short, predictable names are easier to search for.
4. A name other modules call is public, without a leading underscore. Do not reach into
   another object's underscore members from outside its class.
5. Do not rename existing code just for this. Rename when you are already changing the code,
   and update every caller and test in the same commit.

## Commits

A short subject line in the imperative, then a few paragraphs saying what changed and why.
Commit only when asked.
