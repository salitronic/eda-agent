# Native Altium IPC batch generation

`lib_generate_ipc_batch` imports an existing native IPC batch CSV/XLS/XLSX through Altium itself. No mouse, keyboard, menu automation or custom pad calculator is used.

```json
{"input_file":"C:\\EDA\\parts.csv","output_dir":"C:\\EDA\\new-library","require_step":true,"remove_designator":true,"timeout":120}
```

The output folder must be new or empty. Altium and its MCP script bridge must be running. Restart the MCP service after installing this extension.

The discovered native process is `FootprintWizard:BatchPCBLibCreate`, with parameters `FileName=<absolute input>|OutputFolder=<absolute output>|EnableStepModel=True`. The installed FootprintWizard.Ins declares the command; DLL strings identified the parameter names. Real calls on AD 24 verified the route. Omitting EnableStepModel produced 2D-only output in the control run.

The tool reads back every footprint, checks pads and CSV row count, rejects reported native errors, removes Designator text when its text layer contains no unrelated text, saves the library and verifies embedded STEP checksums and file headers. It refuses unsafe text removal. Dispatch alone is never success. STEP files use per-record directories so duplicate original filenames remain distinct. Native naming/report preferences still apply; Altium may display its own report.

Validated on 2026-11-05 with AD 24.0.1.36: one-row and two-row QFN CSVs through a real MCP stdio client. Each footprint had 21 pads, one STEP body and no Designator; all exported models passed checksum verification. Nine automated tests cover success, missing outputs, native errors, missing STEP, unrelated text, overwrite prevention, delimiter injection, density validation and duplicate model filenames. Native warnings about pad trimming are returned. Other AD versions, QFP/SOP and Excel inputs remain unverified.

Run tests with `python -m unittest discover -s tests -p test_ipc_batch.py` using an environment with this source installed.

This is a local extension of salitronic/eda-agent; upstream license and copyright notices are retained.
