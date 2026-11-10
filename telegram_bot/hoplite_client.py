"""Minimal async client for the Hoplite thread API (https://hoplite.sh/docs/api)."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

import httpx

# Thread statuses after which the agent no longer works without new input.
SETTLED_STATUSES = {"ready", "failed", "waiting", "blocked", "archived"}


class HopliteError(RuntimeError):
    pass


@dataclass
class RunResult:
    thread_id: str
    status: str
    replies: list[str]


class HopliteClient:
    def __init__(
        self,
        api_key: str,
        project_id: str,
        base_url: str = "https://api.hoplite.sh",
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.project_id = project_id
        self.model = model
        self.poll_interval = poll_interval
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if method != "GET":
            kwargs.setdefault("headers", {})["Idempotency-Key"] = uuid.uuid4().hex
        resp = await self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise HopliteError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def create_thread(self, prompt: str, title: str | None = None) -> tuple[str, str]:
        body: dict = {"projectId": self.project_id, "prompt": prompt}
        if title:
            body["title"] = title[:100]
        if self.model:
            body["model"] = self.model
        data = await self._request("POST", "/api/threads", json=body)
        return data["thread"]["id"], data.get("run", {}).get("id") or ""

    async def get_thread(self, thread_id: str) -> dict:
        return (await self._request("GET", f"/api/threads/{thread_id}"))["thread"]

    async def send_message(self, thread_id: str, content: str) -> None:
        body: dict = {"content": content}
        if self.model:
            body["model"] = self.model
        await self._request("POST", f"/api/threads/{thread_id}/messages", json=body)

    async def list_messages(self, thread_id: str, limit: int = 100) -> list[dict]:
        data = await self._request(
            "GET", f"/api/threads/{thread_id}/messages", params={"limit": limit, "activityLimit": 0}
        )
        return sorted(data["messages"], key=lambda m: (m.get("createdAt") or "", m.get("id") or ""))

    async def wait_for_run(
        self, thread_id: str, previous_run_id: str | None, timeout: float
    ) -> str:
        """Wait until a run newer than previous_run_id has settled; return the thread status."""
        deadline = time.monotonic() + timeout
        while True:
            thread = await self.get_thread(thread_id)
            started = thread.get("latestRunId") != previous_run_id
            if started and thread.get("status") in SETTLED_STATUSES:
                return thread["status"]
            if time.monotonic() > deadline:
                return "timeout"
            await asyncio.sleep(self.poll_interval)

    async def ask(self, thread_id: str | None, text: str, timeout: float = 3600) -> RunResult:
        """Send text to a new or existing thread and return the agent's replies to it."""
        if thread_id is None:
            thread_id, _ = await self.create_thread(text, title=text.splitlines()[0])
            previous_run_id = None
        else:
            previous_run_id = (await self.get_thread(thread_id)).get("latestRunId")
            await self.send_message(thread_id, text)
        status = await self.wait_for_run(thread_id, previous_run_id, timeout)
        return RunResult(thread_id, status, extract_replies(await self.list_messages(thread_id)))


def extract_replies(messages: list[dict]) -> list[str]:
    """Assistant chat messages after the latest user message (messages sorted oldest first)."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return [
        m["content"].strip()
        for m in messages[last_user + 1 :]
        if m.get("role") == "assistant"
        and m.get("kind", "chat") == "chat"
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]
