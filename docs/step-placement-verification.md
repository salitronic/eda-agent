# STEP placement candidate acceptance

Baseline: upstream 920eed73b3c00ce08797b79e1dc9bde29abe7b71. Tested 2026-11-08.

## Contract and patch

Avoid unsupported IPCB_ComponentBody X/Y reads on AD21. Preserve the model's authored coordinates and translate by requested X/Y. Verify all four bounding rectangle deltas within 0.01 mil. Register the body on the board before modifying it, retaining BeginModify/EndModify around movement. Generic body X/Y reads and absolute-position writes are unavailable; Bounds* remains available.

Use Model.SetState(RotX, RotY, RotZ, Dz) for the requested Z translation before model binding. standoff_height means additional model Z translation, not physical underside clearance. Runtime position_verified and success reflect XY verification only; standoff_applied indicates the setter ran, not independent Z readback. Layer-write failures remain a pre-existing limitation; acceptance independently checked the requested layer.

## Native acceptance

Altium Designer 21.4.1.30 on Windows. Only the candidate PCB_Place3DBody and PCBPrimitivePos routines were substituted into the installed compatibility bridge, with unique running-unit stamps verified before tests. This is not a full current-upstream deployment test. Candidate Python tools were loaded from the isolated worktree.

One disposable board and a neutral asymmetric cuboid authored at X=10..12 mm, Y=20..23 mm, Z=1..5 mm. All cases used Mechanical16:

| Translation (mils) X/Y/Z | Bounds L/B/R/T (mils) | Native underside/top (mils) |
| --- | --- | --- |
| 0 / 0 / 0 | 393.7008 / 787.4016 / 472.4409 / 905.5118 | 39.3701 / 196.8504 |
| 100.25 / 200.5 / 50.25 | 493.9508 / 987.9016 / 572.6909 / 1106.0118 | 89.6201 / 247.1004 |
| -100.25 / -200.5 / -25.5 | 293.4508 / 586.9016 / 372.1909 / 705.0118 | 13.8701 / 171.3504 |

All four XY bounds and native heights persisted after save, close and reopen. Read-only inspection of both saved body streams independently confirmed MODEL.2D.X/Y and MODEL.3D.DZ equal the requested values. The authored height remained 157.4803 mil; Z was not normalized to zero.

Missing model file and unknown layer returned FILE_NOT_FOUND and UNKNOWN_LAYER. The subsequent mutation matched exactly the original three bodies. Generic X/Y queries returned empty values without halting; a separate Y write returned success=false with unreadable Y and unchanged bounds. A combined X/Y write attempt was rejected, but its delimiter was not suitable for proving each individual write path.

Native screen inspection showed all three distinct solids rendered in 3D after a 2D/3D refresh. The initial scripted switch produced a black viewport, so its success response alone was not treated as visual evidence. In 2D, translated outlines responded to mouse highlighting at their new locations; persistent Selected flags were false, so this is not a full spatial-index selection acceptance. Pixels establish visibility, not numerical XYZ precision.

Original installed files were restored byte-for-byte; original polling stamps verified. All protected design/project files remained unchanged. Scratch document closed without saving UI state changes.

## Limits

No AD25/26 host acceptance, rotated/mirrored/imported assembly geometry, arbitrary layers, or separate zero-authored-origin model was tested. No crash-inducing old handler was deliberately rerun. Independent code review found no must-fix defect; reviewer did not independently repeat native tests. Automated results are recorded separately from native evidence. This record describes pre-publication testing; no merge was performed.

## Automated acceptance

18 focused tests passed. Required `scripts/check.py --base HEAD` passed: lint 0 errors/0 warnings, 171 selected test files, 2159 passed and 61 skipped (12 existing invalid-escape warnings). This is the repository-selected suite, not the full test suite.

Publication recheck: rebased onto upstream 094ae0690aced6040861045f2345da7cb88564a8; intervening changes did not alter either tested routine. Required `scripts/check.py --base upstream/main` passed again: lint clean, 2160 passed and 61 skipped across 171 selected test files (12 existing warnings). Native acceptance above remains the original routine-level test, not a fresh full-upstream deployment.
