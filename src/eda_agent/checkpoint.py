# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Project checkpoint / restore: the session safety net (roadmap 1.1).

An AI session can issue rapid, irreversible edits to a live Altium design.
This module snapshots the project directory so any session is revertible in
one step, turning the README's "back up first" caveat into "checkpointed by
default".

Design: a content-addressed snapshot store, hermetic and dependency-free
(no git subprocess, no external tools). Each checkpoint stores the project's
files in a blob store keyed by SHA-256, so unchanged files (a large
untouched .PcbLib) are stored once and shared across checkpoints; a manifest
records the file->hash map.

``take`` and ``put_back`` are the live route. Altium writes every design
document (``ALTIUM_DOCUMENTS``) both ways: a checkpoint has Altium save a
copy of each one into the store, an open document as it is in the editor,
and a restore closes the project without saving and has Altium save each
changed snapshot back under its own name. A document whose size and time
are those of the last checkpoint is not opened again. The project file and
the other files in the folder (models, datasheets) are stored and put back
as bytes, the project file only while the project is closed.

The store lives under the workspace (``workspace/checkpoints/``):

    checkpoints/
      blobs/<sha256>[.ext]      deduplicated file contents
      manifests/<id>.json       one per checkpoint
      staging/                  copies Altium is writing

``create`` and ``restore`` are the store alone, with no Altium.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from eda_agent.atomicfile import replace_with_retry

#: Documents Altium itself writes into a checkpoint and back out of it, with
#: the kind Altium opens each as where it is known; the rest Altium opens
#: by its path.
_DOCUMENT_KINDS = {".schdoc": "SCH", ".pcbdoc": "PCB", ".schlib": "SCHLIB",
                   ".pcblib": "PCBLIB", ".outjob": "OUTPUTJOB",
                   ".harness": "", ".pcbdwf": ""}
ALTIUM_DOCUMENTS = frozenset(_DOCUMENT_KINDS)


def is_altium_document(path) -> bool:
    return Path(path).suffix.lower() in ALTIUM_DOCUMENTS

# Directories never worth snapshotting: regenerable outputs, Altium's own
# history/preview caches, VCS metadata. Matched case-insensitively against
# any path component.
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        "history",
        "__previews",
        "project logs for",
        "project outputs for",
    }
)

# Byte cap per file so a stray multi-GB artifact can't blow up the store.
_DEFAULT_MAX_FILE_BYTES = 256 * 1024 * 1024


@dataclass
class CheckpointInfo:
    id: str
    created: str          # ISO-8601 local time
    label: str
    project_file: str     # relative to project_dir, "" if unknown
    file_count: int
    total_bytes: int
    files: dict = field(default_factory=dict)  # relpath -> {hash,size}
    # Files present at checkpoint time but too large to snapshot (over
    # max_file_bytes). Recorded so a prune_added restore treats them as
    # "known, intentionally not stored" and does NOT delete them, otherwise
    # an oversize .PcbLib / 3D model would be silently lost on revert.
    skipped_large: list = field(default_factory=list)  # relpaths
    project_dir: str = ""
    # Design documents Altium did not write a copy of: relpath -> why.
    # They are not in the checkpoint, and a restore leaves them as they are.
    not_written: dict = field(default_factory=dict)

    def summary(self) -> dict:
        """Manifest without the per-file map (for listings)."""
        d = asdict(self)
        d.pop("files", None)
        return d


def _iter_project_files(project_dir: Path):
    for p in sorted(project_dir.rglob("*")):
        if not p.is_file():
            continue
        parts_lower = {part.lower() for part in p.relative_to(project_dir).parts[:-1]}
        if parts_lower & _EXCLUDED_DIR_NAMES:
            continue
        # Also skip an output dir whose name starts with a known prefix.
        rel_parts = p.relative_to(project_dir).parts[:-1]
        if any(
            part.lower().startswith(("project outputs for", "project logs for"))
            for part in rel_parts
        ):
            continue
        yield p


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class CheckpointStore:
    """Content-addressed checkpoint store rooted at a workspace directory."""

    def __init__(self, root: Path, max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES):
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.manifests = self.root / "manifests"
        self.max_file_bytes = max_file_bytes

    def _ensure_dirs(self) -> None:
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)

    def blob_path(self, meta: dict) -> Path:
        return self.blobs / meta.get("blob", meta["hash"])

    def staging_path(self, suffix: str) -> Path:
        """A fresh path in the store for Altium to write a copy to."""
        staging = self.root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        return staging / f"{uuid.uuid4().hex}{suffix}"

    def previous_files(self, project_dir: Path) -> dict[str, dict]:
        """The file map of the newest checkpoint of ``project_dir``."""
        key = os.path.normcase(os.path.normpath(str(project_dir)))
        for c in self.list():
            if c.project_dir and os.path.normcase(os.path.normpath(c.project_dir)) == key:
                return c.files
        return {}

    def unchanged(self, path: Path, meta: Optional[dict]) -> bool:
        """``path`` has the size and time ``meta`` recorded and its blob is
        still stored, so it is the stored file without reading it."""
        if not meta or "mtime" not in meta:
            return False
        try:
            st = path.stat()
        except OSError:
            return False
        return (st.st_size == meta["size"] and st.st_mtime == meta["mtime"]
                and self.blob_path(meta).is_file())

    def _store_blob(self, staged: Path, suffix: str) -> tuple[str, str]:
        """Move a staged file into the store under its hash."""
        digest = _hash_file(staged)
        name = digest + suffix.lower()
        blob = self.blobs / name
        if blob.exists():
            staged.unlink()
        else:
            replace_with_retry(staged, blob)
        return digest, name

    def create(
        self,
        project_dir: Path,
        project_file: str = "",
        label: str = "",
        *,
        now: Optional[datetime] = None,
        checkpoint_id: Optional[str] = None,
        staged: Optional[dict[str, Path]] = None,
        not_written: Optional[dict[str, str]] = None,
        altium_writes: bool = False,
    ) -> CheckpointInfo:
        """Snapshot ``project_dir`` into the store and return its manifest.

        ``staged`` maps a relpath to the copy Altium wrote of it, moved
        into the store. With ``altium_writes`` a design document is never
        read here: it is a staged copy, the last checkpoint's entry when its
        size and time are unchanged, or it is listed in ``not_written``.
        """
        project_dir = Path(project_dir)
        if not project_dir.is_dir():
            raise NotADirectoryError(f"project dir does not exist: {project_dir}")
        self._ensure_dirs()
        staged = dict(staged or {})
        unwritten = dict(not_written or {})

        now = now or datetime.now()
        cid = checkpoint_id or now.strftime("%Y%m%d-%H%M%S-%f")
        previous = self.previous_files(project_dir)

        files: dict[str, dict] = {}
        total = 0
        skipped: list[str] = []
        for path in _iter_project_files(project_dir):
            rel = path.relative_to(project_dir).as_posix()
            st = path.stat()
            if st.st_size > self.max_file_bytes:
                skipped.append(rel)
                continue
            if rel in unwritten:
                continue
            if rel in staged:
                digest, name = self._store_blob(staged.pop(rel), path.suffix)
                files[rel] = {"hash": digest, "blob": name, "mtime": st.st_mtime,
                              "size": self.blob_path({"blob": name, "hash": digest}).stat().st_size,
                              "written_by": "altium"}
                total += files[rel]["size"]
                continue
            if self.unchanged(path, previous.get(rel)):
                files[rel] = dict(previous[rel])
                total += files[rel]["size"]
                continue
            if altium_writes and is_altium_document(path):
                unwritten[rel] = "Altium did not write a copy of it"
                continue
            digest = _hash_file(path)
            blob = self.blobs / digest
            if not blob.exists():
                # Stage then atomic-rename so a concurrent reader never sees a
                # half-written blob. Allowed to raise if the rename keeps
                # losing the race: a checkpoint missing a blob cannot
                # restore, and a backup that silently is not one is worse
                # than a save that failed and said so.
                tmp = self.blobs / (digest + ".tmp")
                shutil.copyfile(path, tmp)
                replace_with_retry(tmp, blob)
            files[rel] = {"hash": digest, "size": st.st_size, "mtime": st.st_mtime}
            total += st.st_size
        for leftover in staged.values():
            Path(leftover).unlink(missing_ok=True)

        info = CheckpointInfo(
            id=cid,
            created=now.isoformat(timespec="seconds"),
            label=label,
            project_file=project_file,
            file_count=len(files),
            total_bytes=total,
            files=files,
            skipped_large=skipped,
            project_dir=str(project_dir),
            not_written=unwritten,
        )
        manifest_path = self.manifests / f"{cid}.json"
        tmp = manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
        replace_with_retry(tmp, manifest_path)
        return info

    def _load(self, checkpoint_id: str) -> CheckpointInfo:
        manifest_path = self.manifests / f"{checkpoint_id}.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"no such checkpoint: {checkpoint_id}")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return CheckpointInfo(**data)

    def list(self) -> list[CheckpointInfo]:
        """All checkpoints, newest first."""
        if not self.manifests.is_dir():
            return []
        out = []
        for m in self.manifests.glob("*.json"):
            try:
                out.append(self._load(m.stem))
            except (ValueError, TypeError, FileNotFoundError):
                continue
        out.sort(key=lambda c: c.id, reverse=True)
        return out

    def restore(
        self, checkpoint_id: str, project_dir: Path, *, prune_added: bool = False
    ) -> dict:
        """Restore a checkpoint's files into ``project_dir``.

        Files present in the checkpoint are overwritten with the snapshot
        contents. With ``prune_added=True``, design files that exist now but
        were absent at checkpoint time are deleted (a true revert); default
        is additive-safe (leaves newer files in place) to avoid surprises.
        """
        info = self._load(checkpoint_id)
        project_dir = Path(project_dir)
        restored, missing_blobs = 0, []
        for rel, meta in info.files.items():
            blob = self.blob_path(meta)
            if not blob.exists():
                missing_blobs.append(rel)
                continue
            dest = project_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".ckpt-tmp")
            shutil.copyfile(blob, tmp)
            replace_with_retry(tmp, dest)
            restored += 1

        removed = self._prune_added(info, project_dir) if prune_added else []

        return {
            "checkpoint_id": checkpoint_id,
            "restored": restored,
            "removed": removed,
            "missing_blobs": missing_blobs,
        }

    @staticmethod
    def _prune_added(info: CheckpointInfo, project_dir: Path) -> list[str]:
        """Delete the files that were not there at checkpoint time."""
        # A file skipped for size, or one Altium did not copy, existed then;
        # it is "known", not "added since", so it must survive the revert.
        known_rels = set(info.files) | set(info.skipped_large) | set(info.not_written)
        removed = []
        for path in _iter_project_files(project_dir):
            rel = path.relative_to(project_dir).as_posix()
            if rel not in known_rels:
                path.unlink()
                removed.append(rel)
        return removed

    def prune(self, keep: int) -> list[str]:
        """Keep the ``keep`` newest checkpoints; delete the rest + orphan blobs.

        Returns the ids removed.
        """
        checkpoints = self.list()
        to_remove = checkpoints[keep:]
        removed_ids = []
        for c in to_remove:
            (self.manifests / f"{c.id}.json").unlink(missing_ok=True)
            removed_ids.append(c.id)
        self._gc_blobs()
        return removed_ids

    def prune_aged(self, keep: int, max_age_days: float,
                   keep_per_project: int = 3) -> list[str]:
        """Delete checkpoints that are past the ``keep`` newest overall,
        older than ``max_age_days`` AND past the ``keep_per_project``
        newest of their own project; then collect orphan blobs.

        Any one condition keeps a checkpoint: recent work is never
        pruned, and a project left alone for months keeps its last few
        restore points however many other projects were checkpointed
        since. A manifest whose date does not parse is kept.
        """
        cutoff = datetime.now().timestamp() - max_age_days * 86400
        removed_ids = []
        seen: dict[str, int] = {}
        for i, c in enumerate(self.list()):
            seen[c.project_file] = seen.get(c.project_file, 0) + 1
            if i < keep or seen[c.project_file] <= keep_per_project:
                continue
            try:
                created = datetime.fromisoformat(c.created).timestamp()
            except (ValueError, TypeError):
                continue
            if created >= cutoff:
                continue
            (self.manifests / f"{c.id}.json").unlink(missing_ok=True)
            removed_ids.append(c.id)
        if removed_ids:
            self._gc_blobs()
        return removed_ids

    #: A blob younger than this is never collected. ``create`` promotes
    #: each blob to its final name BEFORE writing the manifest that
    #: references it, so between those two steps the blob is a real file
    #: that no manifest mentions -- indistinguishable from garbage. A
    #: collector running in that window deletes it and the manifest then
    #: points at nothing, which surfaces only later as a failed restore.
    #:
    #: ``prune`` has no caller in the tool surface today, so the window
    #: is not currently reachable; this is here so wiring one up later
    #: does not quietly introduce data loss. The same shape of bug was
    #: live in the bridge's response sweep, where it destroyed a
    #: concurrent caller's reply.
    _GC_MIN_AGE_SECONDS = 60.0

    def _gc_blobs(self) -> int:
        """Delete blobs no surviving manifest references. Returns count."""
        referenced: set[str] = set()
        for c in self.list():
            referenced.update(m.get("blob", m["hash"]) for m in c.files.values())
        cutoff = time.time() - self._GC_MIN_AGE_SECONDS
        removed = 0
        if self.blobs.is_dir():
            for blob in self.blobs.iterdir():
                if not blob.is_file() or blob.name.endswith(".tmp"):
                    continue
                if blob.name in referenced:
                    continue
                try:
                    if blob.stat().st_mtime >= cutoff:
                        continue  # may be mid-create; see _GC_MIN_AGE_SECONDS
                    blob.unlink()
                except OSError:
                    continue
                removed += 1
        return removed


def _key(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


async def _open_documents(bridge) -> dict[str, str]:
    """The documents open in Altium: compared key -> path as Altium says it."""
    reply = await bridge.send_command_async("application.get_open_documents", {})
    rows = reply.get("result", reply) if isinstance(reply, dict) else reply
    return {_key(r["file_path"]): str(r["file_path"]) for r in rows or []
            if isinstance(r, dict) and r.get("file_path")}


async def _altium_copy(bridge, source: Path, target: Path) -> str:
    """Have Altium save the document at ``source`` under ``target``; ''
    when it did, else why not."""
    try:
        reply = await bridge.send_command_async(
            "application.save_document_copy",
            {"file_path": str(source), "copy_path": str(target),
             "kind": _DOCUMENT_KINDS.get(target.suffix.lower(), "")}, timeout=180.0)
    except Exception as exc:                                  # noqa: BLE001
        return str(exc) or type(exc).__name__
    if isinstance(reply, dict) and reply.get("written") and target.is_file():
        return ""
    if isinstance(reply, dict):
        return str(reply.get("error") or reply.get("message")
                   or "Altium did not write the copy")
    return "Altium did not write the copy"


async def take(bridge, store: CheckpointStore, project_dir: Path, *,
               project_file: str = "", label: str = "") -> CheckpointInfo:
    """A checkpoint whose design documents Altium wrote.

    A document open in Altium is copied as it is in the editor, unsaved
    edits included. One that is not open and has the size and time of the
    last checkpoint is that checkpoint's entry; any other is opened by
    Altium unfocused, copied and closed.
    """
    project_dir = Path(project_dir)
    open_now = await _open_documents(bridge)
    previous = store.previous_files(project_dir)
    staged: dict[str, Path] = {}
    not_written: dict[str, str] = {}
    for path in _iter_project_files(project_dir):
        if not is_altium_document(path):
            continue
        rel = path.relative_to(project_dir).as_posix()
        if path.stat().st_size > store.max_file_bytes:
            continue
        if _key(path) not in open_now and store.unchanged(path, previous.get(rel)):
            continue
        target = store.staging_path(path.suffix)
        why = await _altium_copy(bridge, path, target)
        if why:
            target.unlink(missing_ok=True)
            not_written[rel] = why
        else:
            staged[rel] = target
    return store.create(project_dir, project_file=project_file, label=label,
                        staged=staged, not_written=not_written, altium_writes=True)


async def put_back(bridge, store: CheckpointStore, checkpoint_id: str,
                   project_dir: Path, *, project_path: str = "",
                   prune_added: bool = False) -> dict[str, Any]:
    """Restore a checkpoint with Altium writing every design document.

    Only files that differ from the checkpoint are restored. The project
    is closed without saving first, so nothing Altium holds is written
    under, and opened again afterwards. Each changed design document is
    opened from the store by Altium and saved under its own name; the file
    it replaces is kept aside until that save is seen, and put back when
    it is not. The project file and the other files are put back as bytes
    while the project is closed.
    """
    info = store._load(checkpoint_id)
    project_dir = Path(project_dir)
    todo = {rel: meta for rel, meta in info.files.items()
            if not store.unchanged(project_dir / rel, meta)}
    out: dict[str, Any] = {"checkpoint_id": checkpoint_id, "restored": 0,
                           "unchanged": len(info.files) - len(todo), "removed": [],
                           "missing_blobs": [], "not_restored": {},
                           "not_in_checkpoint": dict(info.not_written)}
    if not todo and not prune_added:
        return out
    closed = False
    inside = _key(project_dir) + os.sep
    out["were_open"] = sorted(path for key, path in (await _open_documents(bridge)).items()
                              if key.startswith(inside))
    if project_path:
        reply = await bridge.send_command_async(
            "project.close", {"project_path": project_path, "save": "false"},
            timeout=180.0)
        closed = isinstance(reply, dict) and bool(reply.get("closed"))
        if not closed:
            out["error"] = ("the project could not be closed without saving, so "
                            "nothing was restored")
            out["close"] = reply
            return out
    open_now = await _open_documents(bridge)
    for rel, meta in todo.items():
        dest = project_dir / rel
        blob = store.blob_path(meta)
        if not blob.is_file():
            out["missing_blobs"].append(rel)
            continue
        if _key(dest) in open_now:
            out["not_restored"][rel] = ("it is open in Altium outside the project; "
                                        "close it and restore again")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not is_altium_document(dest):
            tmp = dest.with_name(dest.name + ".ckpt-tmp")
            shutil.copyfile(blob, tmp)
            replace_with_retry(tmp, dest)
            out["restored"] += 1
            continue
        source = blob
        if blob.suffix.lower() != dest.suffix.lower():
            source = store.staging_path(dest.suffix)
            shutil.copyfile(blob, source)
        aside = dest.with_name(dest.name + ".ckpt-old") if dest.exists() else None
        if aside is not None:
            replace_with_retry(dest, aside)
        why = await _altium_copy(bridge, source, dest)
        if source != blob:
            source.unlink(missing_ok=True)
        if why:
            dest.unlink(missing_ok=True)
            if aside is not None:
                replace_with_retry(aside, dest)
            out["not_restored"][rel] = why
            continue
        if aside is not None:
            aside.unlink(missing_ok=True)
        out["restored"] += 1
    if prune_added:
        out["removed"] = store._prune_added(info, project_dir)
    if closed:
        reopened = await bridge.send_command_async(
            "project.open", {"project_path": project_path}, timeout=180.0)
        out["reopened"] = not (isinstance(reopened, dict)
                               and reopened.get("success") is False)
    return out
