# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Project checkpoint / restore: the session safety net (roadmap 1.1).

An AI session can issue rapid, irreversible edits to a live Altium design.
This module snapshots the project directory so any session is revertible in
one step, turning the README's "back up first" caveat into "checkpointed by
default".

Design: a content-addressed snapshot store, hermetic and dependency-free
(no git subprocess, no external tools). Each checkpoint copies the project's
design files into a blob store keyed by SHA-256, so unchanged files (a large
untouched .PcbLib) are stored once and shared across checkpoints; a manifest
records the file->hash map. Restore copies blobs back to their paths.

The store lives under the workspace (``workspace/checkpoints/``):

    checkpoints/
      blobs/<sha256>            deduplicated file contents
      manifests/<id>.json       one per checkpoint

This module is Altium-agnostic and fully unit-testable; the MCP tool layer
supplies the live project path (via ``project.get_project_path``) and wires
``app_checkpoint`` / ``app_restore_checkpoint`` onto it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from eda_agent.atomicfile import replace_with_retry

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

    def create(
        self,
        project_dir: Path,
        project_file: str = "",
        label: str = "",
        *,
        now: Optional[datetime] = None,
        checkpoint_id: Optional[str] = None,
    ) -> CheckpointInfo:
        """Snapshot ``project_dir`` into the store and return its manifest."""
        project_dir = Path(project_dir)
        if not project_dir.is_dir():
            raise NotADirectoryError(f"project dir does not exist: {project_dir}")
        self._ensure_dirs()

        now = now or datetime.now()
        cid = checkpoint_id or now.strftime("%Y%m%d-%H%M%S-%f")

        files: dict[str, dict] = {}
        total = 0
        skipped: list[str] = []
        for path in _iter_project_files(project_dir):
            size = path.stat().st_size
            if size > self.max_file_bytes:
                skipped.append(path.relative_to(project_dir).as_posix())
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
            rel = path.relative_to(project_dir).as_posix()
            files[rel] = {"hash": digest, "size": size}
            total += size

        info = CheckpointInfo(
            id=cid,
            created=now.isoformat(timespec="seconds"),
            label=label,
            project_file=project_file,
            file_count=len(files),
            total_bytes=total,
            files=files,
            skipped_large=skipped,
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
            blob = self.blobs / meta["hash"]
            if not blob.exists():
                missing_blobs.append(rel)
                continue
            dest = project_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".ckpt-tmp")
            shutil.copyfile(blob, tmp)
            replace_with_retry(tmp, dest)
            restored += 1

        removed = []
        if prune_added:
            # A file skipped for size at checkpoint time existed then; it is
            # "known", not "added since", so it must survive the revert.
            known_rels = set(info.files) | set(info.skipped_large)
            for path in _iter_project_files(project_dir):
                rel = path.relative_to(project_dir).as_posix()
                if rel not in known_rels:
                    path.unlink()
                    removed.append(rel)

        return {
            "checkpoint_id": checkpoint_id,
            "restored": restored,
            "removed": removed,
            "missing_blobs": missing_blobs,
        }

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
            referenced.update(m["hash"] for m in c.files.values())
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
