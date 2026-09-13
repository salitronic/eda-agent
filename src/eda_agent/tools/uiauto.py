# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tools that drive the Altium GUI, for operations with no API.

Altium exposes a great deal only as menus and dialogs, so this is a
general driver rather than a one-off: discover a menu, invoke it, read
whatever dialog it raises, set the controls inside, press a button.

NONE OF IT USES THE BRIDGE. Every tool here talks to Win32 and to the
accessible layer, and only asks the bridge for Altium's process id,
which is a process scan rather than IPC. That is what makes these the
tools that still answer when a modal has the scripting engine blocked,
and it is why invoking a command and driving the dialog it opens can
be a single call: nothing in that sequence needs the bridge to be free.

Everything here presses buttons in a GUI, which is a different kind of
operation from the rest of the surface, and the separate module is so
nobody reaches one by accident while looking for the other.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..ui import controls, dialog_driver, dialog_report, menu, windows

#: Menu path per target. The only thing that still differs between the
#: schematic and board variants: everything after the menu fires is
#: decided from the dialogs that actually appear.
_MENUS = {
    "schematic": "Tools|Update From Libraries",
    "pcb": "Tools|Update From PCB Libraries",
}

#: Full menu path per target, matched by NAME at every level.
#: MEASURED off the live Tools menu, which lists sixteen entries
#: including several starting "Update", so the full caption is used.
_MENU_PATHS = {
    "schematic": "Tools|Update From Libraries...",
    "pcb": "Tools|Update From PCB Libraries...",
}


def _altium_pid():
    """(pid, None) or (None, error-dict).

    Resolved through the bridge's process scan, NOT through IPC, so it
    still answers while Altium is blocked on a modal. Every tool in
    this module needs it and each used to inline the same six lines.
    """
    from ..bridge import get_bridge
    from ..ui import windows as _win

    # THE KILL SWITCH IS CHECKED HERE because every tool in this module
    # calls this first, so one check covers the whole surface. The
    # primitives check again for anything reaching past the tools.
    if not _win.automation_enabled():
        return None, {"ok": False, "reason": (
            f"UI automation is disabled by {_win.UI_AUTOMATION_ENV}. It is "
            f"on by default; unset that variable, or set it to 1, to allow "
            f"it. See docs/ui-automation.md for what it is for and what it "
            f"can do")}

    status = get_bridge().get_altium_status()
    # Before the not-running check, because an ambiguous status also has
    # no pid, and "Altium is not running" would be false.
    if status.get("ambiguous"):
        return None, {"ok": False, "reason": status.get("reason"),
                      "candidate_pids": status.get("candidate_pids", [])}
    if not status.get("running") or not status.get("pid"):
        return None, {"ok": False, "reason": "Altium is not running"}
    return int(status["pid"]), None


def _resolve_dialog_control(pid, control_label: str, dialog_title: str):
    """One control in one open dialog: (info, None) or (None, refusal).

    Factored out of app_set_dialog_control so the tools added alongside
    it resolve a control the SAME way rather than each carrying a copy.
    Two copies of a lookup drift, and the failure when they do is a tool
    that cannot find a control another tool can see.
    """
    if not controls.available():
        return None, {"ok": False, "reason": (
            "reading dialog controls needs pywin32 and comtypes")}

    open_dialogs = windows.dialogs(pid)
    if dialog_title:
        open_dialogs = [d for d in open_dialogs
                        if dialog_title.lower() in (d.title or "").lower()]
    if not open_dialogs:
        return None, {"ok": False, "reason": (
            "no matching dialog is open. Nothing can be set on a dialog "
            "that is not on screen")}
    dialog = open_dialogs[0]

    found = controls.find(dialog, control_label)
    if found is None:
        # NOT EVERY CONTROL HAS A LABEL. Altium's grids and property
        # editors leave many unnamed, and a label was the only way to
        # address one, so an unlabelled control was unreachable however
        # plainly it was on screen.
        #
        # Two more spellings, both stable enough to act on:
        #   "role#2"  the third control of that role, in tree order
        #   "#7"      the eighth control, in tree order
        # An index is worse than a name and better than nothing; it is
        # offered only after the name has failed.
        every = controls.describe_all(dialog)
        spelling = str(control_label or "").strip().lower()
        picked = None
        if spelling.startswith("#") and spelling[1:].isdigit():
            index = int(spelling[1:])
            if index < len(every):
                picked = every[index]
        elif "#" in spelling:
            role, _, num = spelling.partition("#")
            if num.isdigit():
                same = [c for c in every if c["role"] == role.strip()]
                if int(num) < len(same):
                    picked = same[int(num)]
        if picked is not None:
            return picked, None

        labels = [c["label"] or c["name"] for c in every]
        return None, {"ok": False,
                      "reason": f"{control_label!r} is not in {dialog.title!r}",
                      "offered": [x for x in labels if x],
                      "note": ("unlabelled controls can be addressed as "
                               "'#N' for the Nth control or 'role#N' for "
                               "the Nth of that role; app_set_dialog_control "
                               "with list_only=true numbers them in order")}
    return found, None


def register_uiauto_tools(mcp):
    """Register the dialog-driving tools. Windows and Altium only."""

    @mcp.tool()
    async def app_update_from_libraries(
        target: str = "schematic",
        launch: str = "already_open",
        dry_run: bool = True,
        confirm_execute_eco: bool = False,
        answer_confirmations: bool = True,
        menu_may_steal_focus: bool = False,
        first_step_timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Drive Tools > Update From Libraries, ECO included.

        THIS PRESSES BUTTONS IN THE GUI. Update From Libraries has no
        API: Altium documents it as a dialog, there is no process that
        launches it, and the operation ends in an Engineering Change
        Order that only a click can execute. So this matches windows
        belonging to the Altium process and presses named buttons on
        them.

        WHAT IT WILL DO AT THE END. The last steps press Validate
        Changes and then EXECUTE CHANGES on the ECO. That applies every
        pending change to the design and is not undoable through this
        channel. Take ``app_checkpoint`` first.

        WHAT IT CANNOT TELL YOU. While the wizard is open Altium's
        script engine is blocked, so the bridge cannot be asked
        anything, and an ECO's pending changes sit in a grid that
        exposes no text to Windows. The reply records the windows and
        buttons seen; it does NOT contain the change list. Read the
        Messages panel afterwards for that.

        NOTHING IS SCRIPTED. There is no list of expected dialogs. Each
        one that appears is read, classified, and answered by the ROLE
        of the buttons it offers, then the screen is read again. That is
        deliberate: a scripted version failed on live Altium because the
        editor legitimately shows dialogs no list contained, and waited
        for a change order that was never coming while an unlisted modal
        blocked the session.

        It STOPS rather than guessing when a dialog reports an error,
        asks a question it was not sent to answer, or offers no button
        whose meaning is recognised.

        RUN IT DRY FIRST, WHICH IS THE DEFAULT. A dry run reads the
        first dialog, reports what it is and what it would press, and
        presses nothing.

        LAUNCHING. ``already_open`` (the default) attaches to a wizard
        you have opened yourself, which avoids depending on a menu path
        nobody has verified. ``menu`` asks the bridge to fire the menu
        first; that request will not return until the wizard closes,
        which is expected and is why it is issued without waiting.

        Args:
            target: ``schematic`` for Tools > Update From Libraries, or
                ``pcb`` for Tools > Update From PCB Libraries.
            launch: ``already_open`` or ``menu``.
            dry_run: report without pressing. Default true.
            confirm_execute_eco: must be true for a real run, and it is
                named for the specific consequence rather than being a
                general "confirm", because the irreversible part is the
                ECO and not the wizard.
            answer_confirmations: answer routine "carry on?" prompts.
                Altium asks "Continue and create ECO?" before every
                change order, so with this off the run stops there and
                can never finish. A confirmation mentioning anything
                irreversible (delete, overwrite, cannot be undone) is
                still left alone whatever this is set to.
            first_step_timeout: seconds to wait for the FIRST dialog to
                appear. Raise it when launching by menu on a large
                project.

        Returns:
            Dict with ``ok``, ``dry_run``, ``committed``,
            ``stopped_for_a_human``, ``finished`` explaining how the run
            ended, and ``steps``: for every dialog, what it was, what it
            said, what it offered, what was pressed and WHY.
        """
        if target not in _MENUS:
            return {"ok": False, "reason": (
                f"target must be schematic or pcb, not {target!r}")}
        if launch not in ("already_open", "menu"):
            return {"ok": False, "reason": (
                f"launch must be already_open or menu, not {launch!r}")}
        if launch == "menu" and not menu_may_steal_focus:
            return {"ok": False, "reason": (
                "launch='menu' has to drive Altium's menu bar with real "
                "keyboard and mouse input, because nothing scriptable "
                "opens this wizard: the bridge's menu call reports "
                "success and invokes nothing, and Altium's menus expose "
                "no working accessible action. That means bringing "
                "Altium to the front and briefly moving the pointer, so "
                "it is not done unless asked. Pass "
                "menu_may_steal_focus=True, or open Tools > "
                + _MENUS[target].split("|", 1)[1] +
                " yourself and use launch='already_open'.")}
        if not windows.available():
            return {"ok": False, "reason": (
                "pywin32 is not importable, so no dialog can be driven "
                "from this host")}
        # Deliberately NOT refusing a real run that lacks
        # confirm_execute_eco. That blanket refusal made the tool
        # useless in the most common case: with the design already in
        # sync there is no change order at all, only a "no differences"
        # dialog that needs dismissing, and refusing to run left that
        # modal blocking the editor.
        #
        # The commit is gated where the commit happens instead. The
        # driver will drive everything up to Execute Changes and then
        # stop, naming what it refused, so a run without authorisation
        # is useful AND cannot alter the design.

        pid, problem = _altium_pid()
        if problem:
            return problem

        opened = None
        if launch == "menu":
            # The bridge is NOT used for this. Its menu call reports
            # success and invokes nothing (#83), so the menu bar is
            # driven directly. Run off-thread: it sleeps between the
            # keystroke and the click.
            loop = asyncio.get_running_loop()
            opened = await loop.run_in_executor(
                None, menu.click_path, pid, _MENU_PATHS[target])
            if not opened.get("ok"):
                return {"ok": False, "launch": "menu",
                        "reason": opened.get("reason"),
                        "offered": opened.get("offered"),
                        "note": ("nothing was opened, so nothing was "
                                 "driven and no dialog is waiting")}

        loop = asyncio.get_running_loop()
        report = await loop.run_in_executor(
            None, lambda: dialog_driver.drive(
                pid, intent="proceed",
                allow_commit=bool(confirm_execute_eco),
                allow_confirm=bool(answer_confirmations),
                wait_first=float(first_step_timeout),
                dry_run=dry_run))

        if opened is not None:
            report["opened_by_menu"] = opened
        report["target"] = target
        report["launch"] = launch
        report["altium_pid"] = pid
        return report

    @mcp.tool()
    async def app_set_dialog_control(
        control_label: str,
        checked: bool | None = None,
        text: str | None = None,
        select_row: str | None = None,
        dialog_title: str = "",
        list_only: bool = False,
    ) -> dict[str, Any]:
        """Read or CHANGE a control inside whatever dialog is open.

        The other half of driving a dialog. Buttons are pressed by the
        dialog driver; this is for the checkboxes, radios and fields
        that decide what those buttons will DO.

        FIND BY LABEL, not position. ``"Include Variants"``. Positions
        move between versions and contexts, captions do not.

        EVERY CHANGE IS VERIFIED. The new state is read back, and a
        click that silently did nothing is reported as a failure rather
        than as success. That matters here more than anywhere: a caller
        that believes an option was applied will go on to press
        something irreversible.

        IT REFUSES RATHER THAN GUESSING when a control is DISABLED
        (Altium greys options that do not apply, and clicking one is at
        best a no-op), and when asked to switch a radio button OFF,
        which is not a thing a radio does.

        SETTING IS NOT A TOGGLE. Asking for a state the control is
        already in reports ``changed: false`` and clicks nothing, so
        calling twice is safe.

        Args:
            control_label: the control's visible label.
            checked: desired state for a checkbox.
            text: desired contents for an edit field.
            select_row: for a list, grid or tree, the row to select by
                name. Rows are matched by name for the same reason menu
                items are: positions shift and an index would pick the
                wrong one the first time the contents changed.
            dialog_title: substring to pick one dialog when several are
                open. Empty uses the first.
            list_only: change nothing, just report every control in the
                dialog with its role, state and whether it is enabled.

        Returns:
            For a read, the control list. For a change, ``ok``,
            ``changed`` and the state read back afterwards.
        """
        if not controls.available():
            return {"ok": False, "reason": (
                "reading dialog controls needs pywin32 and comtypes")}

        pid, problem = _altium_pid()
        if problem:
            return problem

        open_dialogs = windows.dialogs(pid)
        if dialog_title:
            open_dialogs = [d for d in open_dialogs
                            if dialog_title.lower() in (d.title or "").lower()]
        if not open_dialogs:
            return {"ok": False, "reason": (
                "no matching dialog is open. Nothing can be set on a "
                "dialog that is not on screen")}
        dialog = open_dialogs[0]

        if list_only:
            return {"ok": True, "dialog": dialog.title,
                    "controls": controls.describe_all(dialog)}

        found = controls.find(dialog, control_label)
        if found is None:
            labels = [c["label"] or c["name"]
                      for c in controls.describe_all(dialog)]
            return {"ok": False,
                    "reason": (f"{control_label!r} is not in "
                               f"{dialog.title!r}"),
                    "offered": [x for x in labels if x]}

        if checked is not None:
            result = controls.set_checked(found["hwnd"], bool(checked))
        elif text is not None:
            result = controls.set_text(found["hwnd"], text)
        elif select_row is not None:
            result = controls.select_row(found["hwnd"], select_row)
        else:
            info = dict(found)
            if info["role"] in ("list", "outline", "table"):
                info["rows"] = controls.list_items(found["hwnd"])
            return {"ok": True, "control": info,
                    "note": ("nothing to change; pass checked, text or "
                             "select_row")}
        result["dialog"] = dialog.title
        result["control_label"] = control_label
        return result

    @mcp.tool()
    async def app_click_menu(menu_path: str,
                             may_steal_focus: bool = False,
                             list_only: bool = False) -> dict[str, Any]:
        """Invoke ANY Altium menu command by path, by driving the menu.

        Use this when ``app_run_menu`` will not do, which is more often
        than it looks: that tool reports success for commands it never
        invokes, because the process ids it dispatches cannot report
        failure and some of them are not real (task #83). This one
        clicks the actual menu, so a command either runs or the reply
        says why not.

        PATH BY NAME. ``"Design|Rules..."``, ``"Tools|Update From
        Libraries..."``. Accelerator markers and a trailing ellipsis are
        ignored, so ``"Design|Rules"`` works too. Nested submenus work
        by adding levels. Names are used rather than positions because
        entries move with context and separators are invisible.

        IT NEEDS THE FOREGROUND. Altium has no Win32 menu and its
        DevExpress bars expose no working accessible action, so the only
        thing that opens them is real input: this brings Altium to the
        front and briefly moves the pointer, putting it back afterwards.
        Every other press in this package is addressed to a window
        handle and depends on none of that, which is why this one is
        gated behind ``may_steal_focus`` and never happens by default.

        THE MENU BAR IS PER EDITOR. With a board focused you get the PCB
        menus, with a schematic the schematic ones. Focus the matching
        document first, or the reply will tell you the item was not
        there and list what was.

        WHAT HAPPENS NEXT IS NOT THIS TOOL'S JOB. Many commands open a
        dialog. Read it with ``app_list_open_dialogs``; the dialog
        drivers handle the rest.

        Args:
            menu_path: pipe-separated path, top level first.
            may_steal_focus: required to actually click anything.
            list_only: do not invoke. Reports the top-level menus
                currently on screen, which is the way to find out what
                this editor context offers.

        Returns:
            Dict with ``ok``; on failure ``reason`` and ``offered``
            listing what that level actually contained.
        """
        if not menu.available():
            return {"ok": False, "reason": (
                "menu driving needs pywin32 and comtypes on Windows")}

        pid, problem = _altium_pid()
        if problem:
            return problem

        loop = asyncio.get_running_loop()
        if list_only:
            # Listing needs the SAME activation as clicking, and an
            # empty path means the menu bar. Altium lays its bars out on
            # activation, so a read without it fails whenever Altium is
            # not already foreground, which is precisely when a caller
            # needs to ask what the menus are.
            if not may_steal_focus:
                return {"ok": False, "reason": (
                    "reading the menu bar needs Altium activated, "
                    "because it only lays its bars out then. Pass "
                    "may_steal_focus=True.")}
            return await loop.run_in_executor(
                None, menu.list_path, pid, menu_path)

        if not may_steal_focus:
            return {"ok": False, "reason": (
                "clicking a menu needs real keyboard and mouse input, "
                "because Altium exposes no scriptable or accessible way "
                "to open one. That means bringing Altium to the front "
                "and briefly moving the pointer. Pass "
                "may_steal_focus=True if that is acceptable now.")}

        return await loop.run_in_executor(
            None, menu.click_path, pid, menu_path)

    @mcp.tool()
    async def app_list_open_dialogs() -> dict[str, Any]:
        """What Altium has on screen, what it says, and if it is stuck.

        THE TOOL TO REACH FOR WHEN THE BRIDGE GOES QUIET. A silent
        bridge looks identical whether Altium is showing a modal, busy
        compiling, or dead. This answers it directly: ``blocked`` is
        read from the main window being disabled, which is what Windows
        does while a modal is up, and it works precisely when no other
        tool can because it does not use the bridge at all. It reads the
        Win32 process, so a blocked Altium answers it normally.

        FOR EACH DIALOG you get the caption, the message text, every
        button with its enabled state, and a ``kind``:

        * ``error`` / ``warning`` / ``confirm`` -- something went wrong
          or a decision is being asked. These set ``needs_a_human``.
        * ``nothing_to_do`` -- the operation completed with no work, for
          instance "Comparator Results (No Differences)" when the
          schematic and board already agree. A success, not a failure.
        * ``engineering_change_order`` -- an ECO awaiting Validate and
          Execute.
        * ``wizard`` / ``progress`` / ``unknown``.

        WHAT IT STILL CANNOT READ. Grids and owner-drawn lists hold
        their content internally and expose no window text, so an ECO's
        pending-change list is not readable. That is reported per dialog
        as ``has_unreadable_content`` rather than being passed off as a
        dialog with nothing in it. Read the Messages panel for the
        change list.

        Reads only. Presses nothing.

        Returns:
            Dict with ``blocked``, ``dialog_count``, ``dialogs``,
            ``needs_a_human``, and a one-sentence ``summary``.
        """
        if not windows.available():
            return {"ok": False, "reason": (
                "pywin32 is not importable, so no dialog can be inspected "
                "from this host")}

        pid, problem = _altium_pid()
        if problem:
            return problem

        return dialog_report.report(pid)

    @mcp.tool()
    async def app_press_dialog_button(button_caption: str,
                                      dialog_title: str = "",
                                      allow_irreversible: bool = False
                                      ) -> dict[str, Any]:
        """Press ONE named button in an open Altium dialog.

        The missing primitive. Dialogs could be listed, read and their
        checkboxes set, but the only code that pressed anything was
        buried inside a single task-specific driver, so answering an
        arbitrary dialog meant writing a script.

        BY CAPTION, not position. Accelerator ampersands and a trailing
        ellipsis are ignored, so "Report Changes" finds
        "&Report Changes...".

        IT WORKS WHILE ALTIUM IS BLOCKED, because it addresses the
        button's window handle over Win32 and never touches the bridge.
        That is the whole point: when a modal has the scripting engine
        stuck, this is what gets it unstuck.

        IRREVERSIBLE PRESSES ARE GATED. A caption that commits a change
        order or applies a wizard is refused unless
        ``allow_irreversible`` is set, so a driver walking an unfamiliar
        dialog cannot execute one by reflex.

        For a whole sequence rather than one press, use
        ``app_drive_dialogs``, which decides each step from what is on
        screen instead of from a script.

        Args:
            button_caption: the visible caption.
            dialog_title: substring picking one dialog when several are
                open. Empty uses the first.
            allow_irreversible: permit a committing press.

        Returns:
            ``ok``, the dialog and button actually matched, and whether
            the dialog closed afterwards.
        """
        pid, problem = _altium_pid()
        if problem:
            return problem

        def press():
            open_dialogs = windows.dialogs(pid)
            if not open_dialogs:
                return {"ok": False, "reason": "no dialog is open"}
            target = None
            for dialog in open_dialogs:
                title = (dialog.title or "").lower()
                if not dialog_title or dialog_title.lower() in title:
                    target = dialog
                    break
            if target is None:
                return {"ok": False,
                        "reason": f"no open dialog matches {dialog_title!r}",
                        "open": [d.title for d in open_dialogs]}

            button = target.find_button(button_caption)
            if button is None:
                offered = [b.text for b in target.buttons()]
                if not offered:
                    # NO BUTTON HANDLES AT ALL: a WPF dialog. Its buttons
                    # are not child windows, so there is nothing to click
                    # and nothing to list. MEASURED on "Unsaved Changes":
                    # this branch used to refuse and leave the bridge
                    # blocked, while a plain Enter cleared it instantly.
                    #
                    # Only the two keys that map to a dialog's own
                    # defaults are offered, and the caption still decides
                    # which, so "Cancel" cannot silently become a save.
                    role = dialog_driver.role_of(button_caption)
                    key = "escape" if role == "dismiss" else "enter"
                    if role in dialog_driver.COMMITTING \
                            and not allow_irreversible:
                        return {"ok": False, "role": role, "reason": (
                            f"{button_caption!r} commits a change. Pass "
                            f"allow_irreversible=True if that is intended.")}
                    windows.press_key(target.hwnd, key)
                    shut = windows.wait_for_close(target.hwnd, timeout=5.0)
                    return {
                        "ok": shut, "dialog": target.title,
                        "pressed": button_caption, "method": "keyboard",
                        "key": key, "dialog_closed": shut,
                        "reason": None if shut else (
                            f"sent {key} because {target.title!r} exposes no "
                            f"button handles, and the dialog is still open. "
                            f"Its captions are only readable by OCR, so the "
                            f"key is a guess at the default action rather "
                            f"than a press of the named button."),
                    }
                return {"ok": False,
                        "reason": (f"{button_caption!r} is not a button on "
                                   f"{target.title!r}"),
                        "offered": offered}
            if not button.enabled:
                return {"ok": False, "reason": (
                    f"{button.text!r} is disabled, so the dialog is not in "
                    f"the state this press assumed")}

            role = dialog_driver.role_of(button.text)
            if role in dialog_driver.COMMITTING and not allow_irreversible:
                return {"ok": False, "role": role, "reason": (
                    f"{button.text!r} commits a change to the design. Pass "
                    f"allow_irreversible=True if that is intended.")}

            windows.click(button)
            closed = windows.wait_for_close(target.hwnd, timeout=5.0)

            # A press that changed nothing is not a success. MEASURED
            # 2026-08-18 on a wedged Altium: the only button was OK, the
            # message went out, the dialog stayed, and this returned
            # ok true. That is the same defect as a handler asserting an
            # outcome it never checked, in the tool written to avoid it.
            #
            # Staying open is NORMAL for some presses: Validate Changes
            # leaves the change order up on purpose, and a wizard page
            # advances without closing its window. So the verdict turns
            # on the ROLE, not on closure alone. Only a press whose
            # whole job is to dismiss can be judged by the window going
            # away, and only when it was the sole way out.
            dismissing = role in ("dismiss", "advance")
            only_way_out = len(target.buttons()) == 1
            if not closed and dismissing and only_way_out:
                return {
                    "ok": False,
                    "dialog": target.title,
                    "pressed": button.text,
                    "role": role,
                    "dialog_closed": False,
                    "reason": (
                        f"{button.text!r} was the only button on "
                        f"{target.title!r} and the dialog is still open, so "
                        f"the press did not take. It was sent as a posted "
                        f"message, and some Altium prompts have been "
                        f"reported to ignore one while still answering "
                        f"other input. app_invoke_element presses the same "
                        f"caption through UI Automation instead, and is the "
                        f"next thing to try."),
                }
            return {"ok": True, "dialog": target.title,
                    "pressed": button.text, "role": role,
                    "dialog_closed": closed,
                    "outcome_verified": bool(closed) or not dismissing}

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, press)

    @mcp.tool()
    async def app_drive_dialogs(intent: str = "proceed",
                                allow_commit: bool = False,
                                allow_confirm: bool = False,
                                dry_run: bool = True,
                                wait_first: float = 30.0,
                                budget: float = 300.0) -> dict[str, Any]:
        """Answer dialogs reactively until none is left.

        The reactive driver, which existed but was reachable only
        through the one wizard tool that used it. It reads whatever is
        on screen, classifies it, decides a press from that, and
        repeats. There is no scripted sequence, so a dialog appearing in
        a different order, or not at all, is handled rather than
        breaking a plan.

        IT STOPS RATHER THAN GUESSING on a dialog it cannot classify,
        and on anything that needs a human. ``dry_run`` is the DEFAULT:
        it reports every decision and presses nothing, which is how to
        see what a sequence would do before letting it.

        THE TWO GATES ARE SEPARATE ON PURPOSE. ``allow_confirm`` answers
        a yes/no question that moves an operation along;
        ``allow_commit`` permits the press that changes the design.
        Named for their consequences, because a caller reaching for one
        rarely means the other.

        Args:
            intent: "proceed" to carry the operation forward, or
                "cancel" to back out of whatever is open.
            allow_commit: permit the design-changing press.
            allow_confirm: permit answering a confirmation.
            dry_run: decide and report, press nothing. Default True.
            wait_first: seconds to wait for the first dialog, since a
                caller fires a command and then drives.
            budget: seconds before giving up on a wedged editor.

        Returns:
            Every observation and decision, plus ``committed``,
            ``stopped_for_a_human`` and why it finished.
        """
        pid, problem = _altium_pid()
        if problem:
            return problem

        loop = asyncio.get_running_loop()

        def run():
            return dialog_driver.drive(
                pid, intent=intent, allow_commit=allow_commit,
                allow_confirm=allow_confirm, dry_run=dry_run,
                wait_first=wait_first, budget=budget)

        return await loop.run_in_executor(None, run)

    @mcp.tool()
    async def app_run_ui_command(menu_path: str,
                                 allow_commit: bool = False,
                                 allow_confirm: bool = True,
                                 dry_run: bool = False,
                                 may_steal_focus: bool = True,
                                 wait_first: float = 30.0,
                                 budget: float = 300.0) -> dict[str, Any]:
        """Invoke a menu command AND answer the dialogs it raises.

        The composite, and usually the one to reach for. Firing a menu
        command that opens a modal blocks Altium's scripting engine, so
        the two halves cannot be separate bridge calls: the second would
        never be answered. Both halves here are Win32, so they run in
        one call and the modal is no obstacle.

        Doing this by hand meant arming a watcher process before firing
        the command, which is not something a caller should have to
        assemble.

        THE GATES ARE THE DIALOG DRIVER'S. ``allow_confirm`` defaults ON
        because a command invoked deliberately usually needs its "yes,
        go ahead" answered, while ``allow_commit`` defaults OFF, so a
        change order is presented rather than executed. ``dry_run``
        defaults OFF here, unlike ``app_drive_dialogs``: naming a
        command to run and having nothing happen is not what the caller
        asked for.

        Args:
            menu_path: pipe-separated, for example
                "Tools|Annotation|Force Annotate All Schematics...".
                Discover paths with app_click_menu(list_only=True).
            allow_commit: permit a design-changing press.
            allow_confirm: permit answering confirmations.
            dry_run: invoke the command but press nothing.
            may_steal_focus: opening a menu needs real input.
            wait_first: seconds to wait for the first dialog.
            budget: seconds before giving up.

        Returns:
            ``ok``, the ``menu`` result, and the full ``dialogs`` record.
            ``ok`` is true only when the menu click AND the drive both
            succeeded, so a command that opened something nobody could
            answer does not read as a success.
        """
        pid, problem = _altium_pid()
        if problem:
            return problem
        if not may_steal_focus:
            return {"ok": False, "reason": (
                "opening a menu needs real keyboard and mouse input, so "
                "Altium has to come to the front. Pass "
                "may_steal_focus=True if that is acceptable now.")}

        loop = asyncio.get_running_loop()
        clicked = await loop.run_in_executor(
            None, menu.click_path, pid, menu_path)
        if not clicked.get("ok"):
            return {"ok": False, "stage": "menu", "menu": clicked}

        def run():
            return dialog_driver.drive(
                pid, intent="proceed", allow_commit=allow_commit,
                allow_confirm=allow_confirm, dry_run=dry_run,
                wait_first=wait_first, budget=budget)

        driven = await loop.run_in_executor(None, run)
        ok = bool(driven.get("ok", True)) and not driven.get(
            "stopped_for_a_human")
        return {"ok": ok, "menu": clicked, "dialogs": driven}

    @mcp.tool()
    async def app_list_panels(offered_too: bool = False) -> dict[str, Any]:
        """Altium's dockable panels that are currently on screen.

        PANELS ARE NOT DIALOGS and were unreachable until now. A dialog
        is a top-level window; a docked panel is a child of the main
        frame, so ``app_list_open_dialogs`` never saw one. Projects,
        Navigator, PCB, Properties and Messages all live here.

        Args:
            offered_too: also read what View > Panels lists, which is
                what COULD be opened rather than what is open. That
                needs a real menu click and steals focus, so it is off
                by default.

        Returns:
            ``{"ok": true, "panels": [{name, rect, holder_class}],
            "count": N}``, plus ``offered`` when asked for.
        """
        from ..ui import panels

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not panels.available():
            return {"ok": False, "reason": "UI automation is unavailable here"}

        open_now = panels.list_open(pid)
        out: dict[str, Any] = {
            "ok": True,
            "count": len(open_now),
            "panels": [{"name": p["name"], "rect": p["rect"],
                        "holder_class": p["holder_class"]} for p in open_now],
        }
        if offered_too:
            out["offered"] = panels.offered(pid)
        return out

    @mcp.tool()
    async def app_open_panel(name: str, settle: float = 1.2) -> dict[str, Any]:
        """Open a dockable panel by name, and confirm it appeared.

        Goes through View > Panels, because Altium exposes no scripted
        way to show a panel and ``application.execute_menu`` reports
        success while invoking nothing. So this STEALS FOCUS, like every
        menu click. Reading a panel afterwards does not.

        A panel that is already open is reported as such and NOT clicked.
        Those menu entries are toggles, so clicking one you already have
        would close it, and asking for a panel is not a request to lose
        it.

        Args:
            name: the panel's name as View > Panels spells it, e.g.
                "Projects", "Messages", "PCB", "Properties".
            settle: how long to allow for it to appear.

        Returns:
            ``{"ok": true, "name": ..., "opened": bool, "rect": ...}``.
            ``opened`` is false when it was already there.
        """
        from ..ui import panels

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        return panels.open_panel(pid, name, settle=settle)

    @mcp.tool()
    async def app_read_panel(name: str) -> dict[str, Any]:
        """Read what is inside an open panel.

        Takes no focus and moves nothing, so it is safe while a user is
        working. The panel must already be open: use ``app_open_panel``
        first, or ``app_list_panels`` to see what is there.

        Reads the WHOLE panel. It is not fast: Altium materialises its
        accessible tree lazily, so the first touch of a node costs about
        116 ms and 162 rows is roughly thirty seconds. Slow and complete
        beats fast and partial.

        Returns:
            ``{"ok": true, "name": ..., "row_total": N,
            "grids": [{control, hwnd, row_count, rows}]}``. Grids are
            reported separately because a panel holds more than one and
            they mean different things.
        """
        from ..ui import panels

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        return panels.read_panel(pid, name)

    @mcp.tool()
    async def app_set_dropdown(
        control_label: str,
        option: str,
        dialog_title: str = "",
        list_only: bool = False,
    ) -> dict[str, Any]:
        """Choose an option in a combo box or drop list, and verify it.

        THESE WERE READABLE AND UNSETTABLE. combobox and droplist have
        been reported by ``app_list_open_dialogs`` from the start, so a
        caller could see a combo and its current value with no way to
        change it, which is the same shape as a property that reads and
        will not write.

        Three routes are tried, cheapest first: the option's own default
        action, an accessible selection, then a real click on it. The
        value is READ BACK afterwards, so a route that runs and changes
        nothing is reported rather than believed.

        Args:
            control_label: the combo's label.
            option: the option to choose, matched by name.
            dialog_title: which dialog, when several are open.
            list_only: report the options and change nothing.

        Returns:
            ``{"ok": true, "value": ..., "how": ..., "was": ...}``, or
            ``ok: false`` with ``offered`` listing what is there.
        """
        from ..ui import controls

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        target, problem = _resolve_dialog_control(pid, control_label, dialog_title)
        if problem is not None:
            return problem
        if list_only:
            return {"ok": True, "control": control_label,
                    "options": [c["name"] for c in
                                controls.list_choices(target["hwnd"])]}
        return controls.select_item(target["hwnd"], option)

    @mcp.tool()
    async def app_expand_tree_node(
        control_label: str,
        node_name: str,
        expand: bool = True,
        dialog_title: str = "",
    ) -> dict[str, Any]:
        """Open or close a node in a tree, and verify the state changed.

        Trees were readable and could not be OPENED, so anything nested
        inside one did not exist as far as a caller was concerned.

        Args:
            control_label: the tree's label.
            node_name: the node to open, matched by name.
            expand: True to open, False to close.
            dialog_title: which dialog, when several are open.

        Returns:
            ``{"ok": true, "node": ..., "expanded": bool,
            "changed": bool}``. ``changed`` is false when it was already
            in that state, which is a success rather than a no-op to
            worry about.
        """
        from ..ui import controls

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        target, problem = _resolve_dialog_control(pid, control_label, dialog_title)
        if problem is not None:
            return problem
        return controls.expand(target["hwnd"], node_name, want=expand)

    @mcp.tool()
    async def app_set_grid_cell(
        control_label: str,
        row: str,
        column: str,
        text: str,
        dialog_title: str = "",
        list_only: bool = False,
    ) -> dict[str, Any]:
        """Type into one cell of a grid, and read it back.

        ALTIUM'S RULE AND PROPERTY EDITORS TAKE THEIR INPUT THIS WAY.
        Selecting a row was possible before; changing anything in it was
        not.

        A grid cell is not an edit control until it is being edited, so
        the cell is activated first and the text goes to whatever editor
        the grid puts there. The value is then read back off the CELL,
        because the editor disappears when the edit commits.

        Args:
            control_label: the grid's label.
            row: the row, matched by name.
            column: the column name, or a numeric index. These grids
                leave many cells unnamed, and an index is then the only
                handle there is.
            text: what to type.
            dialog_title: which dialog, when several are open.
            list_only: report the row's cells and change nothing. Worth
                doing first, since it shows which columns have names.

        Returns:
            ``{"ok": true, "row": ..., "column": ..., "value": ...}``,
            or ``ok: false`` when the cell reads back differently, which
            happens on read-only columns and on ones that validate and
            revert.
        """
        from ..ui import controls

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        target, problem = _resolve_dialog_control(pid, control_label, dialog_title)
        if problem is not None:
            return problem
        if list_only:
            return controls.list_cells(target["hwnd"], row)
        return controls.set_cell(target["hwnd"], row, column, text)

    @mcp.tool()
    async def app_send_keys(sequence: str) -> dict[str, Any]:
        """Send a key sequence to whatever currently has focus.

        THE THINGS THAT TAKE NEITHER A CONTROL NOR A MENU: a canvas
        shortcut, a grid that commits on Enter, a dialog answered with
        Escape, a chord like Ctrl+Shift+D. Every other tool here
        addresses a control; this addresses the keyboard.

        Space separated, each item a key or a '+' chord:

            "escape"              one key
            "ctrl+s"              a chord
            "tab tab enter"       three in order

        Modifiers: ctrl, alt, shift. Keys: enter, escape, tab, space,
        backspace, delete, the arrows, home, end, pageup, pagedown and
        F1 to F12. An unknown name is refused and the known ones listed,
        rather than sent as nothing.

        THIS GOES WHEREVER FOCUS IS. Establish focus first, with
        app_click_menu or app_open_panel, and check the result
        afterwards: a keystroke has no read-back, so the reply says what
        was sent and never that it worked.
        """
        from ..ui import windows as win

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not win.available():
            return {"ok": False, "reason": "UI automation is unavailable here"}
        # Aimed at Altium's frame, so every key is checked against it and
        # a stray keystroke cannot reach another application.
        target = menu.frame(pid)
        try:
            return win.send_keys(
                sequence, target=target.hwnd if target else None)
        except win.ForegroundLost as exc:
            return {"ok": False, "reason": str(exc)}

    @mcp.tool()
    async def app_type_text(text: str) -> dict[str, Any]:
        """Type literal text into whatever has focus.

        For a field that HAS a handle, app_set_dialog_control is better:
        it addresses the control by name and reads the value back. This
        is for the places with no handle to address, where typing is the
        only route in.

        Carries punctuation and case, since it sends characters rather
        than virtual keys. No read-back, for the same reason as
        app_send_keys.
        """
        from ..ui import windows as win

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not win.available():
            return {"ok": False, "reason": "UI automation is unavailable here"}
        target = menu.frame(pid)
        try:
            return win.type_text(text, target=target.hwnd if target else None)
        except win.ForegroundLost as exc:
            return {"ok": False, "reason": str(exc)}

    @mcp.tool()
    async def app_context_menu(x: int, y: int,
                               item: str = "") -> dict[str, Any]:
        """Right click at a point: read the context menu, or pick from it.

        CONTEXT MENUS WERE ENTIRELY UNREACHABLE, and Altium puts a great
        deal in them that no top-level menu duplicates: what you can do
        to a component, a net, a polygon, a row in a panel. Driving the
        menu bar while ignoring these leaves most of the editor shut.

        With no ``item`` this OPENS the menu, reads what it offers and
        CLOSES it again, invoking nothing. Leaving a popup dropped
        blocks the next operation and looks to a user like a hang.

        Args:
            x, y: screen coordinates to right click. Panel and control
                rectangles from app_list_panels and
                app_set_dialog_control(list_only=true) give you these.
            item: the entry to choose. Nested entries take a '|' path,
                matched by name at each level like the menu bar.

        Returns:
            Reading: ``{"ok": true, "items": [...], "count": N}``.
            Choosing: ``{"ok": true, "item": ...}``, or ``ok: false``
            with ``offered`` listing what was actually there.
        """
        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if item:
            return menu.context_click(pid, x, y, item)
        return menu.context_menu(pid, x, y)

    @mcp.tool()
    async def app_toolbar(name: str = "") -> dict[str, Any]:
        """List toolbar buttons, or press one by name.

        Toolbars are the other half of the bar framework the menus use
        and were not addressed at all. Many carry a command with no menu
        equivalent, or reach in one click what the menu takes three to.

        With no ``name`` this lists every button it can see with its
        rectangle. With one, it presses that button and reports whether
        a menu dropped as a result, since many toolbar buttons open one
        rather than acting.
        """
        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if name:
            return menu.click_toolbar(pid, name)
        return menu.toolbars(pid)

    @mcp.tool()
    async def app_dialog_tab(name: str = "",
                             dialog_title: str = "") -> dict[str, Any]:
        """List a dialog's tab pages, or switch to one.

        PROPERTY DIALOGS HIDE MOST OF THEMSELVES BEHIND TABS. Nothing
        could switch one, so app_set_dialog_control only ever saw the
        front page and reported it as the whole dialog: a control on any
        other page read as a control the dialog does not have.

        With no ``name`` this lists the pages and which is selected.
        """
        from ..ui import controls as ctl

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        open_dialogs = windows.dialogs(pid)
        if dialog_title:
            open_dialogs = [d for d in open_dialogs
                            if dialog_title.lower() in (d.title or "").lower()]
        if not open_dialogs:
            return {"ok": False, "reason": "no matching dialog is open"}
        dialog = open_dialogs[0]
        if not name:
            tabs = ctl.list_tabs(dialog)
            return {"ok": True, "dialog": dialog.title,
                    "tabs": [{"name": t["name"], "selected": t["selected"]}
                             for t in tabs]}
        return ctl.select_tab(dialog, name)

    @mcp.tool()
    async def app_scroll_control(control_label: str, lines: int = 3,
                                 dialog_title: str = "") -> dict[str, Any]:
        """Scroll a list, grid or tree so more of it comes into view.

        A LONG LIST HIDES ITS OWN CONTENTS. A virtualised DevExpress
        grid exposes roughly what is on screen, so a row further down
        did not exist as far as a caller was concerned, and the reply
        gave no hint that anything was missing.

        Positive scrolls down, negative up. The reply reports whether
        the rows actually changed: ``moved: false`` at the end of a list
        is a real answer rather than a failure.
        """
        from ..ui import controls as ctl

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        target, problem = _resolve_dialog_control(pid, control_label,
                                                  dialog_title)
        if problem is not None:
            return problem
        return ctl.scroll(target["hwnd"], lines)

    @mcp.tool()
    async def app_activate_row(control_label: str, row: str,
                               double: bool = False,
                               dialog_title: str = "") -> dict[str, Any]:
        """Invoke a row, rather than merely selecting it.

        ``app_set_dialog_control(select_row=...)`` highlights a row.
        This ACTS on it, which in a panel is the difference between
        pointing at a document and opening it.

        A click has no read-back, so the reply reports what was done and
        not what it caused.
        """
        from ..ui import controls as ctl

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        target, problem = _resolve_dialog_control(pid, control_label,
                                                  dialog_title)
        if problem is not None:
            return problem
        return ctl.activate_row(target["hwnd"], row, double=double)

    @mcp.tool()
    async def app_screenshot(path: str = "",
                             dialog_title: str = "") -> dict[str, Any]:
        """Capture Altium, or one dialog, to a PNG.

        THE AGENT COULD NOT SEE THE UI. ui/ocr.py has been able to
        capture and read a window since it was written and none of it
        was exposed, so every judgement about what was on screen came
        from an accessible tree that omits anything drawn rather than
        placed. A picture settles what a tree cannot: which page is in
        front, whether a control is greyed, what a rendered dialog
        actually says.

        Args:
            path: where to write the PNG. Defaults to the workspace
                directory, so the file is somewhere findable.
            dialog_title: capture that dialog rather than the main
                window.

        Returns:
            ``{"ok": true, "path": ..., "hwnd": ...}``. Read the file to
            look at it.
        """
        import os
        import time as _time

        from ..ui import ocr

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not ocr.available():
            return {"ok": False, "reason": (
                "screen capture needs pywin32 and Pillow")}

        target = None
        if dialog_title:
            for dialog in windows.dialogs(pid):
                if dialog_title.lower() in (dialog.title or "").lower():
                    target = dialog.hwnd
                    break
            if target is None:
                return {"ok": False,
                        "reason": f"no dialog matching {dialog_title!r}"}
        else:
            frame = menu.frame(pid)
            if frame is None:
                return {"ok": False, "reason": "no Altium main window"}
            target = frame.hwnd

        if not path:
            from ..config import get_config
            path = os.path.join(str(get_config().workspace_dir),
                                f"altium_{int(_time.time())}.png")
        if not ocr.capture_png(target, path):
            return {"ok": False, "reason": "capture failed", "path": path}
        return {"ok": True, "path": path, "hwnd": target,
                "note": "read the file to look at it"}

    @mcp.tool()
    async def app_canvas(x: int, y: int, to_x: int = 0, to_y: int = 0,
                         double: bool = False) -> dict[str, Any]:
        """Click or drag on the editor canvas, at screen coordinates.

        THE CANVAS HAS NO NAMED CONTROLS, so every other tool here could
        not touch it. Placing, moving, rubber-band selecting and routing
        are pointer gestures and there was no way to make one.

        With ``to_x``/``to_y`` this DRAGS, moving in steps rather than
        jumping, because a jump reads as a click at the destination.
        Without them it clicks.

        NO READ-BACK IS POSSIBLE. The reply says what gesture was made
        and never what it did. Confirm with a bridge read, which can see
        the document, rather than trusting this.
        """
        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not windows.available():
            return {"ok": False, "reason": "UI automation is unavailable here"}
        if not menu.bring_to_front(pid):
            return {"ok": False, "reason": "could not focus Altium"}
        target = menu.frame(pid)
        hwnd = target.hwnd if target else None
        try:
            if to_x or to_y:
                return windows.drag(x, y, to_x, to_y, target=hwnd)
            return windows.click_at(x, y, double=double, target=hwnd)
        except windows.ForegroundLost as exc:
            return {"ok": False, "reason": str(exc)}

    @mcp.tool()
    async def app_wait_for_dialog(title: str = "",
                                  timeout: float = 30.0) -> dict[str, Any]:
        """Block until a dialog appears, and report which one.

        A COMMAND THAT RAISES A DIALOG RETURNS BEFORE THE DIALOG EXISTS.
        A caller that acted and then looked found nothing and concluded
        none was coming; the alternative was a fixed sleep long enough
        to be safe, which is the pattern this layer spent the day
        removing everywhere else.
        """
        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not windows.available():
            return {"ok": False, "reason": "UI automation is unavailable here"}
        return windows.wait_for_dialog(pid, title, timeout)

    @mcp.tool()
    async def app_ui_snapshot() -> dict[str, Any]:
        """Everything on screen, in one call: menus, panels, dialogs, toolbars.

        ORIENTATION IS THE EXPENSIVE PART. Finding out what is available
        took four calls and knowing which four to make, and a caller who
        guessed wrong concluded a capability was missing. This is the UI
        equivalent of app_context: one call that answers "what am I
        looking at".

        Reads only. Takes no focus, moves no pointer, changes nothing,
        so it is safe while somebody is working. The menu BAR is listed
        because reading it needs no click; menu contents are not,
        because opening one does.
        """
        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not windows.available():
            return {"ok": False, "reason": "UI automation is unavailable here"}

        from ..ui import panels

        out: dict[str, Any] = {"ok": True, "altium_pid": pid}
        try:
            out["menu_bar"] = sorted(menu.bar_items(pid))
        except Exception as exc:                 # noqa: BLE001
            out["menu_bar"] = []
            out["menu_bar_error"] = str(exc)

        open_dialogs = windows.dialogs(pid)
        out["dialogs"] = [{"title": d.title, "class": d.class_name,
                           "buttons": [b.text for b in d.buttons()]}
                          for d in open_dialogs]
        out["dialog_count"] = len(open_dialogs)

        try:
            out["panels"] = [p["name"] for p in panels.list_open(pid)]
        except Exception as exc:                 # noqa: BLE001
            out["panels"] = []
            out["panel_error"] = str(exc)

        try:
            bar = menu.toolbars(pid)
            out["toolbar_buttons"] = ([b["name"] for b in bar.get("buttons", [])]
                                      if bar.get("ok") else [])
        except Exception as exc:                 # noqa: BLE001
            out["toolbar_buttons"] = []
            out["toolbar_error"] = str(exc)

        out["next_step"] = (
            "app_click_menu walks a menu, app_open_panel opens a panel, "
            "app_context_menu right clicks a point, app_screenshot shows "
            "you what a tree cannot")
        return out

    @mcp.tool()
    async def app_read_window(dialog_title: str = "",
                              with_text: bool = True) -> dict[str, Any]:
        """Read a window through UI Automation, which sees what MSAA cannot.

        THE LAYER THAT WAS MISSING. This project read Altium three ways:
        window text, which returns nothing for anything without a handle;
        MSAA, which handles the VCL dialogs well and the WPF ones not at
        all; and OCR on the pixels. UIA sits between the second and the
        third.

        It matters most for the case that forced OCR into existence.
        Altium's newer WPF dialogs have buttons that are NOT child
        windows, so EnumChildWindows finds nothing and MSAA has nothing
        to press. WPF is UIA-native, so those same buttons are ordinary
        elements here, with names, and they can be pressed properly with
        app_invoke_element.

        WHAT IT STILL WILL NOT SEE. Delphi's TLabel owns no handle and
        publishes no accessible object, so its text exists only as
        pixels. Use app_screenshot there; that is what OCR is for and
        why it stays.

        Returns:
            ``{"ok": true, "count": N, "elements": [{name, type,
            automation_id, enabled, rect}], "text": [...]}``.
        """
        from ..ui import uia

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not uia.available():
            return {"ok": False, "reason": (
                "UI Automation is unavailable here; it needs comtypes and "
                "UIAutomationCore")}

        target = None
        if dialog_title:
            for dialog in windows.dialogs(pid):
                if dialog_title.lower() in (dialog.title or "").lower():
                    target = dialog.hwnd
                    break
            if target is None:
                return {"ok": False,
                        "reason": f"no dialog matching {dialog_title!r}"}
        else:
            frame = menu.frame(pid)
            if frame is None:
                return {"ok": False, "reason": "no Altium main window"}
            target = frame.hwnd

        out = uia.describe_window(target)
        if out.get("ok") and with_text:
            out["text"] = uia.text_of(target)
        return out

    @mcp.tool()
    async def app_invoke_element(name: str, dialog_title: str = "",
                                 text: str = "",
                                 expand: str = "") -> dict[str, Any]:
        """Press, set or expand an element through UI Automation patterns.

        PATTERNS ARE NOT A SIMULATED CLICK. MSAA offers little beyond a
        default action, which is why the rest of this layer falls back to
        moving a real mouse. UIA exposes what a control actually
        implements: Invoke to press, Value to set text, ExpandCollapse to
        open a node. Those are supported operations, so they move no
        pointer, take no focus, and cannot land on whatever happens to be
        under the cursor.

        That makes this the right tool for a WPF dialog, and for anything
        you want done while the user keeps working.

        Args:
            name: the element's name or automation id.
            dialog_title: which dialog. Empty uses the main window.
            text: set this value instead of pressing.
            expand: "true" or "false" to open or close a node instead.

        Returns:
            ``ok`` with what was done, or a refusal naming which pattern
            the element lacks and its rectangle, so a real click remains
            possible as a fallback.
        """
        from ..ui import uia

        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        if not uia.available():
            return {"ok": False, "reason": "UI Automation is unavailable here"}

        target = None
        if dialog_title:
            for dialog in windows.dialogs(pid):
                if dialog_title.lower() in (dialog.title or "").lower():
                    target = dialog.hwnd
                    break
            if target is None:
                return {"ok": False,
                        "reason": f"no dialog matching {dialog_title!r}"}
        else:
            frame = menu.frame(pid)
            if frame is None:
                return {"ok": False, "reason": "no Altium main window"}
            target = frame.hwnd

        if text:
            return uia.set_value(target, name, text)
        if expand:
            return uia.expand(target, name,
                              want=str(expand).strip().lower()
                              in ("1", "true", "yes"))
        return uia.invoke(target, name)
