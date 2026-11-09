# The Config tab

The Config tab on the Altium status panel holds settings that only the user
changes. They are stored in `preferences.json` in the workspace, and each one
takes effect on the next command, with no restart of Altium, the bridge or
the AI client.

An agent reads this page when a setting stops something the user asked for.
Its job is to tell the user which setting it is and what it does. It never
changes a setting itself, and never works around one.

## Permissions

| Setting | Choices | What it does |
|---|---|---|
| Edits | Allowed / Read-only | Read-only refuses every command that changes a design. Reads, checks and exports still run. |
| Deleting | Allowed / Refused | Refused turns away every command that deletes or removes. Other edits still run. |
| Closing | Allowed / Refused | Refused stops the AI closing a document or a project. |
| UI automation | On / Off | Off stops menus, dialog buttons and keystrokes. Some tasks have no other route. |

## View, values and dialogs

| Setting | Choices | What it does |
|---|---|---|
| Keep board in view | Off / On | On fits the window to the whole board or sheet after each edit, at most every 2 seconds. |
| Component value format | None / 4K7 2u2 / 4.7k 2.2u / with unit | How a value is written when it is cleaned up. None leaves values as they are. |
| Dialogs | Auto / Manual | Manual leaves the dialogs a tool opens (change orders, teardrops) for the user to answer. |

## Checks

Each check runs before a part is built, or before fab files are written, and
refuses the build when what was made does not match its source. All are on by
default.

| Row on the tab | What it checks | When a user may want past it |
|---|---|---|
| Footprint vs drawing | the footprint against its datasheet drawing | lands deliberately changed from the vendor's pattern (longer for hand soldering, a house rule) |
| Symbol vs pin table | the symbol's pins against the datasheet pin table | a pin table that is a picture, or names deliberately written another way |
| Symbol pins vs pads | the symbol's pin numbers against the footprint's pads | a footprint held in an installed library or vault, or named pads deliberately left off the symbol |
| 3D body vs drawing | the 3D body against the datasheet outline | a deliberately simplified body, or a part with no outline drawing |
| SPICE model file | the SPICE model file | the user's own model (measured, or written in-house) rather than a vendor's |
| Fab export | the board and parts against the fab before export | a limit the fab agreed for this order (a fab with no data here takes fab "custom" with the user's own limits instead) |

### When a check refuses and the user insists

A refusal is the check doing its job. The refusal names the check
(`check`), says what differs (`reason`), and carries `if_the_user_insists`,
which says what to do next:

1. Show the user what differs, in their terms: which land, pin, dimension or
   node, and by how much.
2. If they still want it built as it is, ask them to switch that row off on
   the Config tab. Only the user does this.
3. Build it again. The result carries `unchecked`, naming the check that was
   off, so the part is never taken for a checked one.
4. Remind the user to switch the row back on when they are done.

Never pass a check by another route: not a different tool, not pads or pins
placed one by one, not an edit after the build. That gives the user the part
without the record that it differs from its source.
