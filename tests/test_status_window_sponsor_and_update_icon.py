# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The status window's sponsor note and update icon.

The update icon is driven by a flag file the server writes after each
update check; the form only tests that the file exists. These tests pin
both ends to the same file name and check the server writes and removes
it with the news.
"""
from pathlib import Path

from eda_agent import updates

ROOT = Path(__file__).resolve().parents[1]
PAS = (ROOT / "scripts" / "altium" / "StatusForm.pas").read_text(encoding="utf-8")
DFM = (ROOT / "scripts" / "altium" / "StatusForm.dfm").read_text(encoding="utf-8")


def test_form_and_server_agree_on_the_flag_name():
    assert f"UPDATE_FLAG_FILE     = '{updates.UPDATE_FLAG}';" in PAS


def test_flag_written_while_an_update_is_out(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "_cache_path", lambda: tmp_path / "update_check.json")
    monkeypatch.setattr(updates, "notice",
                        lambda: {"update_available": True, "next_step": "3 behind"})
    updates._sync_flag()
    assert (tmp_path / updates.UPDATE_FLAG).read_text(encoding="utf-8") == "3 behind"


def test_flag_removed_once_current(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "_cache_path", lambda: tmp_path / "update_check.json")
    (tmp_path / updates.UPDATE_FLAG).write_text("old", encoding="utf-8")
    monkeypatch.setattr(updates, "notice", lambda: None)
    updates._sync_flag()
    assert not (tmp_path / updates.UPDATE_FLAG).exists()


def test_flag_failure_never_raises(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("git gone")
    monkeypatch.setattr(updates, "_cache_path", lambda: tmp_path / "update_check.json")
    monkeypatch.setattr(updates, "notice", boom)
    updates._sync_flag()


def test_update_icon_hidden_until_the_flag_appears():
    block = DFM.split("object btn_Update: TPanel", 1)[1].split("    end", 1)[0]
    assert "Visible = False" in block
    assert "UpdateUpdateBadge(0);" in PAS


def test_sponsor_link_matches_funding_file():
    funding = (ROOT / ".github" / "FUNDING.yml").read_text(encoding="utf-8")
    assert "github: salitronic" in funding
    assert "SPONSOR_URL          = 'https://github.com/sponsors/salitronic';" in PAS


def test_sponsor_note_closes_for_good_and_only_shows_on_the_log_tab():
    assert "FileExists(WorkspaceDir + SPONSOR_CLOSED_FILE)" in PAS
    assert "WriteFileContent(WorkspaceDir + SPONSOR_CLOSED_FILE" in PAS
    assert "pnl_Sponsor.Visible := mmo_Log.Visible And Not SponsorNoteClosed(0);" in PAS


def test_every_new_handler_is_defined():
    for name in ("btn_SponsorClick", "btn_SponsorEnter", "btn_SponsorLeave",
                 "btn_SponsorCloseClick", "btn_SponsorCloseEnter",
                 "btn_SponsorCloseLeave", "btn_SponsorHeartEnter",
                 "btn_SponsorHeartLeave", "btn_UpdateClick",
                 "btn_UpdateEnter", "btn_UpdateLeave"):
        assert f"= {name}" in DFM
        assert f"Procedure {name}(Sender : TObject);" in PAS
