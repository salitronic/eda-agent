---
name: Bug report
about: Report a defect in the MCP server or the Altium bridge
title: "[bug] "
labels: bug
assignees: ''
---

## Summary

A clear, one-paragraph description of what is wrong.

## Steps to reproduce

1.
2.
3.

## Expected behaviour

What you expected to happen.

## Actual behaviour

What actually happened. Paste error messages verbatim inside fenced code
blocks.

## Environment

- `eda-agent` version: <output of `eda-agent --version`>
- Python version: <output of `python --version`>
- EDA tool and version: <Altium Designer: Help > About; KiCad or
  EasyEDA Pro: their About dialog>
- Windows version:
- MCP client (Claude Code / Claude Desktop / other):

## Workspace artefacts (if applicable)

If a tool call failed, paste its line from `activity.log` in
`%USERPROFILE%\EDA Agent\workspace\`: the command and Altium's full
reply. For a crash, also the record under `logs\crashes` there. Redact
any proprietary path or design data first.

```text
// activity.log line
```

## Additional context

Logs, screenshots, related issues.
