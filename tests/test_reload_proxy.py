# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The development proxy restarts the server on a code change and the
client's connection carries on.

The proxy runs for real, over pipes, with a stand-in server as its child:
the stand-in answers initialize and tools/list with its own process id,
so a restart shows as a new id, and "slow" takes a second to answer.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

FAKE_SERVER = textwrap.dedent('''
    import json, os, sys, time
    if os.path.exists(os.path.join(sys.argv[1], "broken")):
        sys.stderr.write("SyntaxError: half-written edit\\n")
        sys.exit(1)
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg.get("method")
        if method == "initialize":
            result = {"capabilities": {"tools": {}}, "serverInfo": {"name": str(os.getpid())}}
        elif method == "tools/list":
            result = {"tools": [{"name": "pid" + str(os.getpid())}]}
        elif method == "slow":
            time.sleep(1.0)
            result = {"pid": os.getpid()}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
''')

DRIVER = textwrap.dedent('''
    import sys
    from pathlib import Path
    import eda_agent.devproxy as d
    d.POLL_S, d.SETTLE_S = 0.1, 0.2
    watched = Path(sys.argv[1])
    d.Proxy([], sys.stdin.buffer, sys.stdout.buffer,
            child_command=[sys.executable, sys.argv[2], str(watched)],
            root=watched).run()
''')


class Client:
    def __init__(self, tmp_path: Path):
        self.watched = tmp_path / "pkg"
        self.watched.mkdir()
        (self.watched / "mod.py").write_text("x = 1\n")
        fake = tmp_path / "fake_server.py"
        fake.write_text(FAKE_SERVER)
        driver = tmp_path / "driver.py"
        driver.write_text(DRIVER)
        self.proc = subprocess.Popen(
            [sys.executable, str(driver), str(self.watched), str(fake)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self.next_id = 0

    def send(self, method, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
        self.proc.stdin.flush()
        return msg.get("id")

    def read(self):
        return json.loads(self.proc.stdout.readline())

    def call(self, method):
        request_id = self.send(method)
        while True:
            msg = self.read()
            if msg.get("id") == request_id:
                return msg

    def change_source(self):
        time.sleep(0.05)
        (self.watched / "mod.py").write_text(f"x = {time.time()}\n")

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(10)


@pytest.fixture
def client(tmp_path):
    c = Client(tmp_path)
    init = c.call("initialize")
    c.send("notifications/initialized", notify=True)
    c.init = init
    yield c
    c.close()


def test_the_client_is_told_the_tool_list_can_change(client):
    assert client.init["result"]["capabilities"]["tools"]["listChanged"] is True


def test_a_source_change_restarts_the_server_on_the_same_connection(client):
    before = client.call("tools/list")["result"]["tools"][0]["name"]
    client.change_source()
    notice = client.read()
    assert notice["method"] == "notifications/tools/list_changed"
    after = client.call("tools/list")["result"]["tools"][0]["name"]
    assert after != before, "the new child must answer"


def test_a_call_in_flight_finishes_on_the_old_server(client):
    first_pid = client.call("tools/list")["result"]["tools"][0]["name"][3:]
    request_id = client.send("slow")
    client.change_source()
    messages = [client.read(), client.read()]
    reply = next(m for m in messages if m.get("id") == request_id)
    assert str(reply["result"]["pid"]) == first_pid
    assert messages[-1].get("method") == "notifications/tools/list_changed", (
        "the restart comes after the call it waited for")


def test_a_call_sent_during_the_restart_goes_to_the_new_server(client):
    old = client.call("tools/list")["result"]["tools"][0]["name"]
    client.send("slow")
    client.change_source()
    time.sleep(0.6)  # the restart is now waiting for the slow call
    late = client.send("tools/list")
    replies = {}
    while late not in replies:
        msg = client.read()
        if "id" in msg:
            replies[msg["id"]] = msg
    assert "error" not in replies[late], replies[late]
    assert replies[late]["result"]["tools"][0]["name"] != old


def test_a_server_that_fails_to_start_says_why_and_recovers(client):
    (client.watched / "broken").write_text("")
    client.change_source()
    client.read()  # the list-changed notice
    deadline = time.monotonic() + 10
    reply = client.call("tools/list")
    while "error" not in reply and time.monotonic() < deadline:
        reply = client.call("tools/list")
    assert "half-written edit" in reply["error"]["message"]
    (client.watched / "broken").unlink()
    client.change_source()
    assert client.read()["method"] == "notifications/tools/list_changed"
    assert "result" in client.call("tools/list")
