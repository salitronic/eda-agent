# Contributing to eda-agent

Thanks for your interest. This project is an early-stage MCP server that
bridges large language models to Altium Designer through a DelphiScript
side-channel. It is single-maintainer and Windows-only by necessity (Altium
runs on Windows).

Before opening a non-trivial change, please file an issue first so we can
discuss scope. Drive-by patches that change architecture or rename public
APIs are unlikely to land without prior agreement.

## Ground rules

- Be respectful. See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
- Security-sensitive issues do **not** go in public issues. See
  [`SECURITY.md`](SECURITY.md).
- By contributing you agree your contribution is licensed under
  Apache-2.0 (the project licence).
- Every edit to a design or library file goes through the Delphi bridge and
  Altium. Modifying a file directly is not accepted unless it is strictly
  unavoidable or well constrained, and reading design files directly is
  discouraged too; the few exceptions are agreed in an issue first.

## Development environment

Required:

- Windows 10 / 11
- A licensed Altium Designer install (the Pascal side is tested against
  recent versions, but the API is stable across releases)
- Python 3.11 or newer
- `pip install -e .[dev]` from the repository root

Optional but recommended:

- Free Pascal (`fpc`) for the offline Pascal cross-validation tests under
  `tests/cross_validate_pascal.pas`
- An IDE that understands `pyproject.toml` (VS Code, PyCharm)

## Running the agent locally

1. Install the package in editable mode: `pip install -e .[dev]`
2. Install the Altium-side scripts: `eda-agent install-scripts`
3. In Altium, open `Altium_API.PrjScr` and run `Dispatcher > StartMCPServer`
4. From an MCP client, connect to the `eda-agent` stdio server

The Python side polls a workspace directory for JSON responses from the
Pascal side; the workspace pointer lives at
`C:\ProgramData\eda-agent\workspace-path.txt`.

## Tests

- `pytest` runs the offline suite, and is safe with Altium open
- `EDA_AGENT_INTEGRATION=1 pytest` adds the live-Altium tests
- `python tests/test_cross_validate.py` runs the offline Pascal validator
  (requires Free Pascal in PATH)

**A plain `pytest` does not touch a running Altium.** The nine tests
under `tests/integration/` drive a real session, and they are skipped at
COLLECTION unless `EDA_AGENT_INTEGRATION=1`, so no fixture runs, no
bridge is built and no request file is written.

That gate is recent. Before it, those tests reached the skip only after
`real_bridge` had already pinged, and `fixture_project_loaded` called
`project.open` with no skip in front of it at all, so running the suite
against a healthy polling loop would have opened the fixture project in
whatever Altium you had in front of you.
`tests/test_integration_tests_are_opt_in.py` holds the line, and checks
it end to end by running the directory in a subprocess with the
workspace redirected and asserting nothing was written there.

Once you opt in, those tests still only read: they open and compile a
project and query it, and send no command that changes the design. A
test that would is rejected by
`tests/test_integration_suite_is_non_destructive.py`. Verification that
has to modify something belongs in `docs/RELEASE_VERIFICATION.md`.

The Pascal scripts cannot be fully unit-tested without a running Altium
instance; cross-validation runs the same logic compiled by `fpc` against
mocked Altium objects and is the only honest pre-Altium check.

### Writing a guard

A good part of this suite is guards: tests that compare a fact stated in
one place against the code that decides it, because the two drift and
nothing else notices. If you add one, four things have caught real
mistakes here and are worth copying.

**Mutate the defect it exists to catch.** A guard that has never failed
has not been tested. Break the thing on purpose, confirm the guard
fails, put it back. Several guards in this suite passed on their first
run while checking nothing, and only mutation found that.

**Assert the check found something.** If the guard parses a table, a
document or a registry, assert the parse was non-empty and roughly the
expected size. A renamed heading otherwise turns the guard into a test
that passes because it read zero rows. Existing examples:
`test_the_scan_sees_what_it_claims_to`,
`test_the_widened_scan_actually_sees_something`,
`test_the_check_can_actually_fail`.

**Do not let the guard match its own explanation.** If it searches for a
literal and a nearby comment names that literal, the comment satisfies
the search. `tests/test_no_em_dashes.py` builds its characters with
`chr()` for this reason, and the CI check in
`tests/test_version_is_unreleased.py` ignores comment lines because its
own rationale contains the string it looks for.

**Prefer behaviour to literals, and remember a count cannot see a name.**
`tests/test_unit_conversions_agree.py` converts values rather than
comparing constants, because keeping the constant and flipping the
operation is the likelier mistake. `tests/test_readme_names_real_tools.py`
exists because the count guard beside it cannot tell a correct total
from a table naming a tool nobody wrote.

## Pull requests

- Keep PRs focused. One concern per PR.
- Include a clear description of the problem and the chosen approach.
- If you touch Pascal: Altium keeps compiled script units. Re-running
  StartMCPServer does not recompile an edited unit, and restarting Altium
  has not always done so either. Run `eda-agent install-scripts --force`,
  then close the script project in the Projects panel (right-click
  `Altium_API.PrjScr`, Close Project), reopen it and run StartMCPServer.
  `app_ping` names any unit still running an older compile in
  `stale_units`.
- Add or update tests when behaviour changes.
- Run `pytest --ignore=tests/integration` locally before requesting review.

## Commit messages

Write the subject as a plain imperative sentence saying what the commit
changes, wrapping the body at ~72 columns:

```
Keep the test suite away from the machine-global workspace pointer

Longer body if needed: what was wrong, and why this is the fix.
```

Do not use a `type(scope):` prefix. This file previously documented that
convention; the repository no longer uses it.

Do not write housekeeping messages. Mechanical tidying goes into the
commit that makes the substantive change, and is not mentioned in it.

## Reporting bugs

See [`.github/ISSUE_TEMPLATE/bug_report.md`](.github/ISSUE_TEMPLATE/bug_report.md).
Include the Altium version, the `eda-agent --version` output, and, if you
can, the failing call's line from the workspace `activity.log`, which
holds the command and Altium's full reply.

## Suggesting features

See [`.github/ISSUE_TEMPLATE/feature_request.md`](.github/ISSUE_TEMPLATE/feature_request.md).
Concrete use cases beat speculative API additions.
