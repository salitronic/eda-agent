# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A one-output OutJob that prints ONE named document to PDF.

``proj_export_image`` with ``document_path`` prints a schematic sheet or
a board whatever document Altium has in front. Plain
``WorkspaceManager:Print`` with ``FileName=`` opens the modal print
preview on AD 25, so the export goes through a Publish (PDF) container,
which main's ``project.run_outjob`` runs with ``DisableDialog=True``.
Measured on AD 25.8 (issue #361):

- an output's ``OutputDocumentPathN`` scopes it to one document, loaded
  or not, and nothing else is printed;
- the OutJob must be a document of that document's project: one in
  another folder, never added, stops on an Altium "Missing file" error;
- page settings copied from another OutJob can stop a PCB print with
  "Wrong page settings", so this file writes its own;
- adding the OutJob, running it and removing it again leaves the .PrjPcb
  as a plain save of the project would.

This module only writes text and renders the result; the tool does the
Altium calls.
"""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from typing import Any

#: File name stem of the temporary OutJob, so a leftover one is recognisable.
TEMP_PREFIX = "__eda_export_"

#: The container the OutJob holds; ``project.run_outjob`` takes its name.
CONTAINER = "PDF"

_KINDS = {".schdoc": "sch", ".pcbdoc": "pcb"}

# Letter, landscape, fitted to the page: Altium's own default page record.
_PAGE = ("Record=PageOptions|CenterHorizontal=True|CenterVertical=True|"
         "PrintScale=1.00|XCorrection=1.00|YCorrection=1.00|PrintKind=1|"
         "BorderSize=5000000|LeftOffset=0|BottomOffset=0|Orientation=2|"
         "PaperLength=1000|PaperWidth=1000|Scale=100|PaperSource=7|"
         "PrintQuality=-3|MediaType=1|DitherType=10|PrintScaleMode=1|"
         "PaperKind=Letter|PaperIndex=1")

_SCH_VIEW = ("Record=SchPrintView|ShowNoERC=True|ShowParamSet=True|"
             "ShowProbe=True|ShowBlanket=True|ShowNote=True|"
             "ShowNoteCollapsed=True|ShowOpenEnds=False|"
             "ExpandDesignator=True|ExpandNetLabel=False|ExpandPort=False|"
             "ExpandSheetNum=False|ExpandDocNum=False|PrintArea=0|"
             "PrintAreaRect.X1=0|PrintAreaRect.Y1=0|PrintAreaRect.X2=0|"
             "PrintAreaRect.Y2=0")

_PCB_VIEW = ("DesignatorDisplayMode=Physical|PrintArea=DesignExtent|"
             "PrintAreaLowerLeftCornerX=0|PrintAreaLowerLeftCornerY=0|"
             "PrintAreaUpperRightCornerX=0|PrintAreaUpperRightCornerY=0|"
             "Record=PcbPrintView")

#: The board's two sides, one page each, as the board is seen from them.
PCB_SIDES = (
    ("Top", False, ("TopLayer", "TopOverlay", "MultiLayer", "KeepOutLayer")),
    ("Bottom", True, ("BottomLayer", "BottomOverlay", "MultiLayer",
                      "KeepOutLayer")),
)

_ALL_FULL = ("CArc=Full|CFill=Full|Comment=Full|Coordinate=Full|CPad=Full|"
             "CRegion=Full|CText=Full|CTrack=Full|CVia=Full|DDSymbolKind=0|"
             "DDSymbolSize=500000|DDSymbolSortKind=0|Designator=Full|"
             "Dimension=Full|DLayer1=TopLayer|DLayer2=BottomLayer|FArc=Full|"
             "FFill=Full|FPad=Full|FRegion=Full|FText=Full|FTrack=Full|"
             "FVia=Full|Polygon=Full")


def kind_of(document_path: str) -> str | None:
    """``"sch"`` for a schematic sheet, ``"pcb"`` for a board, else None."""
    return _KINDS.get(PureWindowsPath(str(document_path)).suffix.lower())


def find_projects(document_path: str, levels: int = 2) -> list[str]:
    """The .PrjPcb files in the document's folder and up to ``levels``
    folders above it whose [DocumentN] sections name the document."""
    from ..prjpcb import lists_document

    found: list[str] = []
    folder = Path(document_path).parent
    for _ in range(levels + 1):
        try:
            candidates = sorted(folder.glob("*.PrjPcb"))
        except OSError:
            candidates = []
        for prj in candidates:
            try:
                if lists_document(str(prj), document_path):
                    found.append(str(prj))
            except OSError:
                continue
        if folder.parent == folder:
            break
        folder = folder.parent
    return found


def relative_to_project(document_path: str, project_path: str) -> str:
    """The document as the OutJob names it: relative to the project's
    folder, as Altium writes OutputDocumentPath, else the full path."""
    try:
        return os.path.relpath(document_path, os.path.dirname(project_path))
    except ValueError:              # another drive
        return str(document_path)


def _pcb_items() -> list[str]:
    items = [_PCB_VIEW]
    for index, (name, mirror, layers) in enumerate(PCB_SIDES):
        items.append(
            "IncludeBottomLayerComponents=%s|IncludeMultiLayerComponents=True|"
            "IncludeTopLayerComponents=%s|IncludeViewports=True|Index=%d|"
            "Mirror=%s|Name=%s|PadNumberFontSize=14|Record=PcbPrintOut|"
            "ShowHoles=False|ShowPadNets=False|ShowPadNumbers=False|"
            "SubstituteFonts=False" % (mirror, not mirror, index, mirror,
                                       name))
        for layer in layers:
            items.append("%s|Layer=%s|PrintOutIndex=%d|Record=PcbPrintLayer"
                         % (_ALL_FULL, layer, index))
    return items


def build_outjob(document: str, kind: str, output_dir: str,
                 file_name: str) -> str:
    """The OutJob text: one output on one PDF container, scoped to
    ``document`` (as relative_to_project gives it), writing
    ``output_dir``\\``file_name`` with no viewer and no overwrite prompt."""
    if kind not in ("sch", "pcb"):
        raise ValueError(f"kind must be 'sch' or 'pcb', not {kind!r}")
    if any(ch in str(document) for ch in "\r\n"):
        raise ValueError("the document path holds a line break")
    out_dir = str(output_dir).rstrip("\\/") + "\\"
    if kind == "sch":
        out_type, out_name, items = "Schematic Print", "Schematic Prints", [
            _SCH_VIEW]
    else:
        out_type, out_name, items = "PCB Print", "PCB Prints", _pcb_items()
    lines = [
        "[OutputJobFile]",
        "Version=1.0",
        "Caption=",
        "Description=Temporary OutJob written by proj_export_image; "
        "removed after the run",
        "",
        "[OutputGroup1]",
        "Name=" + TEMP_PREFIX.strip("_"),
        "Description=",
        "TargetOutputMedium=" + CONTAINER,
        "VariantName=[No Variations]",
        "VariantScope=1",
        "CurrentConfigurationName=",
        "TargetPrinter=",
        "PrinterOptions=Record=PrinterOptions|Copies=1|Duplex=1|"
        "TrueTypeOptions=3|Collate=1|PrintJobKind=1|PrintWhat=1",
        "OutputMedium1=" + CONTAINER,
        "OutputMedium1_Type=Publish",
        "OutputType1=" + out_type,
        "OutputName1=" + out_name,
        "OutputCategory1=Documentation",
        "OutputDocumentPath1=" + str(document),
        "OutputVariantName1=",
        "OutputEnabled1=1",
        "OutputEnabled1_OutputMedium1=1",
        "OutputDefault1=0",
        "PageOptions1=" + _PAGE,
    ]
    for n, item in enumerate(items, 1):
        lines.append(f"Configuration1_Name{n}=OutputConfigurationParameter{n}")
        lines.append(f"Configuration1_Item{n}={item}")
    lines += [
        "",
        "[PublishSettings]",
        "OutputFilePath1=" + out_dir + file_name,
        "ReleaseManaged1=0",
        "OutputBasePath1=" + out_dir,
        "OutputPathMedia1=",
        "OutputPathMediaValue1=",
        "OutputPathOutputer1=",
        "OutputPathOutputerPrefix1=",
        "OutputPathOutputerValue1=",
        "OutputFileName1=" + file_name,
        "OutputFileNameMulti1=",
        "UseOutputNameForMulti1=0",
        "OutputFileNameSpecial1=",
        "OpenOutput1=0",
        "PromptOverwrite1=0",
        "PublishMethod1=0",
        "ZoomLevel1=50",
        "FitSCHPrintSizeToDoc1=1",
        "FitPCBPrintSizeToDoc1=1",
        "GenerateNetsInfo1=0",
        "MarkPins1=0",
        "MarkNetLabels1=0",
        "MarkPortsId1=0",
        "GenerateTOC1=0",
        "ShowComponentParameters1=0",
        "GlobalBookmarks1=0",
        "PDFACompliance1=Disabled",
        "PDFVersion1=Default",
        "",
    ]
    return "\r\n".join(lines)


def pdf_pages(pdf_path: str) -> int | None:
    """Page count of a written PDF, or None when it cannot be read."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        doc = pdfium.PdfDocument(pdf_path)
    except Exception:                                # noqa: BLE001
        return None
    try:
        return len(doc)
    finally:
        doc.close()


def render_page(pdf_path: str, image_path: str, dpi: int = 150,
                page: int = 1) -> dict[str, Any]:
    """Render one page (1-based) of the PDF to ``image_path``; the format
    follows its extension (.png, .jpg, .bmp)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return {"ok": False, "reason": "rendering a PDF page needs pypdfium2 "
                                       "(pip install pypdfium2)"}
    if dpi < 20 or dpi > 1200:
        return {"ok": False, "reason": f"dpi {dpi} is outside 20..1200"}
    doc = pdfium.PdfDocument(pdf_path)
    try:
        count = len(doc)
        if page < 1 or page > count:
            return {"ok": False, "pages": count,
                    "reason": f"page {page} is not in the PDF (1..{count})"}
        image = doc[page - 1].render(scale=dpi / 72).to_pil()
        if PureWindowsPath(image_path).suffix.lower() in (".jpg", ".jpeg",
                                                           ".bmp"):
            image = image.convert("RGB")
        image.save(image_path)
        return {"ok": True, "pages": count, "page": page, "dpi": dpi,
                "width_px": image.width, "height_px": image.height}
    finally:
        doc.close()
