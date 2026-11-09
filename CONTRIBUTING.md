# Contributing to eda-agent

Thanks for your interest. This project is an early-stage MCP server that
bridges large language models to Altium Designer through a DelphiScript
side-channel. It is single-maintainer and Windows-only by necessity (Altium
runs on Windows).

Before opening a larger change, please file an issue first so we can
agree the scope together; for a change to the architecture or a public
tool's name, that conversation comes first.

## Ground rules

- Be respectful. See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
- Security-sensitive issues do **not** go in public issues. See
  [`SECURITY.md`](SECURITY.md).
- By contributing you agree your contribution is licensed under
  Apache-2.0 (the project licence).
- Everything goes through Altium. Every write to a design or library
  file, and every read of one, is made by Altium through its API at the
  bridge's request, never by opening the file directly, from Python or
  from the bridge's Pascal. A direct read can occasionally be justified
  and is agreed in an issue first; a direct write is very hard to justify
  and needs a strong case in that issue.

## How we work

Contributions of every size are welcome, from a typo fix to a new tool.
You do not need to get everything below right on the first try: these are
the things a review looks for, written down so there are no surprises.
Where a change needs adjusting to fit, the maintainer will usually do that
part during intake rather than send it back.

### How a pull request lands

- Your work is credited to you. Pull requests are not merged with the
  merge button; the maintainer cherry-picks your commits, keeping you as
  the git author, so GitHub lists you as a contributor. Any adapting and
  extra testing goes into a separate follow-up commit. Merge-from-main
  commits on your branch are not needed.
- For a small fix, just open the pull request. For something larger, an
  issue first saves you time: it is the place to agree the approach,
  especially when a change alters what an existing tool's argument or
  reply means. Mention the issue in the PR (`Closes #N`).
- Bug reports and feature requests are both welcome as issues. Either
  way, the maintainer decides what is built and how: a bug is confirmed
  first, a feature is weighed against the project's direction, and the
  design that lands may differ from the one proposed.
- You can leave `SCRIPT_VERSION` and the generated
  `scripts/altium/Altium_MCP.pas` alone. The maintainer bumps the version
  and rebuilds the bundle at intake, so parallel PRs do not conflict on
  them.

### What the tools aim for

These come from things that went wrong in real sessions, so each has a
reason behind it.

- **Read and write through Altium.** Design and library files are read
  and changed by Altium itself, through its own API (see Ground rules), so
  what a tool reports is what Altium holds and Altium always knows what
  changed. Opening the file directly is not the route, whether from Python
  or from the bridge's own Pascal (a file stream, a text read or rewrite
  of a .SchDoc or .PrjPcb): the bridge asks Altium to read it, or to make
  the change and save it.
- **API first.** The scripting API is preferred over an editor process
  (`RunProcess`), and a process over driving a dialog. When a process is
  the only route, a comment saying why and a read-back of the result help
  a lot.
- **Name the document.** A tool acts on the document it was given, or the
  only one it can mean. With several open it lists the candidates rather
  than guessing from whichever one is in front.
- **Read back the write.** A reply says what Altium holds after the call,
  and a check that could not run says so rather than reporting a pass.
- **Keep capabilities.** A hazard is documented or warned about rather
  than removed, and a workaround for one Altium version is applied on that
  version.
- **Stay neutral on style.** The tools serve many teams with different
  house rules, so a change that imposes a particular style, preference or
  rule (a grid, a naming scheme, a pin layout, a check on a design), even
  one a standard requires, needs a switch on the Config tab so a user can
  turn it off. You do not need to build the switch yourself: say in the PR
  that the change imposes a choice, and the maintainer adds it.
- **Prefer a warning to a refusal,** unless the call would act on the
  wrong object or write something Altium will not keep. A refusal says why
  and what to do instead.
- **Read only proven members.** A DelphiScript `Try` does not catch an
  undeclared identifier, so reading a member a type lacks stops the
  script. Ask a member only of the types where it was seen to exist.
- **Dependencies.** The project uses no third-party engines beyond ngspice
  and openEMS, and every new import is declared in `pyproject.toml`
  (`tests/test_imports_are_declared.py` checks this).

### Writing style

- Tool docstrings describe what the tool does now, without history or
  issue numbers: the model reads them every session, and each has a
  3000-character budget (`tests/test_tool_description_budget.py`). The
  commit message is the place for the story.
- Keep library, project, customer and personal names and private paths
  out of code, tests and fixtures.
- When you measured something in Altium, say which version; it is just as
  useful to say which versions you could not test.

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
- `python scripts/check.py` runs the fast checks before a push: the Pascal
  lint, the generated command rules, the repo-wide guards (copyright
  lines, dispatch tables, declared imports, docs counts) and the tests
  that name what your branch changed. Run
  `git config core.hooksPath .githooks` once and the pre-push hook runs it
  for you.

The command rules (read-only, deleting, closing) live in
`src/eda_agent/safety.py`. `StatusForm.pas` carries a generated copy: edit
the Python, then run `python scripts/altium/sync_rules.py`.

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
- Run `python scripts/check.py` before pushing, and
  `pytest --ignore=tests/integration` before requesting review.

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
