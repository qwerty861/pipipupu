import json

import httpx
import pytest

from bot import split_message
from hoplite_client import HopliteClient, extract_replies


def make_transport():
    """Fake Hoplite API: a thread whose run settles on the second poll."""
    state = {"polls": 0, "run": "run_1", "messages": [], "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer hop_test"
        path = request.url.path
        if request.method == "POST" and path == "/api/threads":
            body = json.loads(request.content)
            assert body["projectId"] == "proj_1" and body["model"] == "claude-opus-5-5"
            state["messages"] = [
                {"id": "m1", "role": "user", "kind": "chat", "content": body["prompt"], "createdAt": "2026-01-01T00:00:01Z"},
            ]
            return httpx.Response(201, json={"ok": True, "thread": {"id": "thr_1"}, "run": {"id": "run_1"}})
        if request.method == "POST" and path == "/api/threads/thr_1/messages":
            body = json.loads(request.content)
            state["run"] = "run_2"
            state["polls"] = 0
            state["messages"].append(
                {"id": "m3", "role": "user", "kind": "chat", "content": body["content"], "createdAt": "2026-01-01T00:00:03Z"}
            )
            return httpx.Response(201, json={"ok": True, "message": {"id": "m3"}, "queued": True})
        if path == "/api/threads/thr_1":
            state["polls"] += 1
            done = state["polls"] >= 2
            if done and state["messages"][-1]["role"] == "user":
                n = len(state["messages"])
                state["messages"].append(
                    {"id": f"m{n + 1}", "role": "assistant", "kind": "chat", "content": f"answer {state['run']}", "createdAt": f"2026-01-01T00:00:0{n + 1}Z"}
                )
            return httpx.Response(200, json={"ok": True, "thread": {"id": "thr_1", "status": "ready" if done else "running", "latestRunId": state["run"]}})
        if path == "/api/threads/thr_1/messages":
            # Newest first, to prove the client sorts.
            return httpx.Response(200, json={"ok": True, "messages": list(reversed(state["messages"])), "hasMore": False})
        return httpx.Response(404)

    return httpx.MockTransport(handler), state


@pytest.mark.asyncio
async def test_new_thread_then_follow_up():
    transport, state = make_transport()
    client = HopliteClient("hop_test", "proj_1", model="claude-opus-5-5", transport=transport, poll_interval=0)

    first = await client.ask(None, "hello", timeout=5)
    assert (first.thread_id, first.status, first.replies) == ("thr_1", "ready", ["answer run_1"])

    second = await client.ask("thr_1", "more", timeout=5)
    assert second.replies == ["answer run_2"]
    assert ("POST", "/api/threads/thr_1/messages") in state["requests"]
    await client.aclose()


def test_extract_replies_skips_tool_messages_and_earlier_turns():
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "kind": "chat", "content": "old"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "kind": "activity", "content": "tool"},
        {"role": "assistant", "kind": "chat", "content": " new "},
    ]
    assert extract_replies(msgs) == ["new"]


def test_split_message():
    parts = split_message("a" * 5000 + "\n" + "b" * 10)
    assert all(len(p) <= 4096 for p in parts) and "".join(parts).replace("\n", "") == "a" * 5000 + "b" * 10
