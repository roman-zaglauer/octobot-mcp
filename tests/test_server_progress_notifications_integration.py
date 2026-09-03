"""End-to-end MCP protocol-level tests for milestone 11's opportunistic
progress notifications (docs/tasklist.md item 11) -- driven through the SDK's
own real `Client`/`MCPServer` pair, the same technique
`test_server_tools_integration.py` already established for this project,
rather than a bespoke mock standing in for the SDK's own notification-
delivery mechanism.

**Why `mode="legacy"` specifically** (this file's one real departure from
`test_server_tools_integration.py`'s own `Client(server, raise_exceptions=True)`
calls): confirmed live during this milestone's own step-0 research (see
`octobot_mcp.tools.backtesting`'s module-level "Milestone 11" comment block
for the full writeup) that the SDK's DEFAULT in-process wiring for a raw
`Server`/`MCPServer` (used by every other test in this project, `mode="auto"`)
goes through `mcp.server.runner.modern_on_request`'s connectionless
`DirectDispatcher` pairing, which builds a brand-new `Connection` with NO
back-channel for literally every request -- a notification sent from a
background task AFTER the original request has already returned is silently
dropped there, regardless of anything this project's own code does. Passing
`mode="legacy"` is the one `Client` mode that instead routes through
`mcp.client._memory.InMemoryTransport` (a genuine duplex in-memory stream
pair, driving the SDK's real legacy-handshake serving loop), which DOES give
the server connection a real, persistent, forwarding back-channel -- exactly
what this project's own production server (real stdio, ADR-0001) always has
regardless of which protocol era a real client opens with. This is a
test-harness-only distinction; see the comment block above for why it is not
a production concern.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2 as httpx
import mcp_types as types
import pytest
from mcp import Client
from mcp.server import MCPServer

import octobot_mcp.tools.backtesting as backtesting_module
from octobot_mcp.context import AppContext
from octobot_mcp.tools import register_all
from tests.test_tools_backtesting import _FakeSocketIOClient, _sequential_factory


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _build_test_server(handler) -> MCPServer:
    @asynccontextmanager
    async def test_lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
        client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
        try:
            yield AppContext(client=client)
        finally:
            await client.aclose()

    server = MCPServer("OctoBot-test", lifespan=test_lifespan)
    register_all(server)
    return server


def _patch_single_job_watcher(monkeypatch: pytest.MonkeyPatch, fake_sio: _FakeSocketIOClient) -> None:
    """Substitute a fake Socket.IO client + a zero watcher interval into every
    `start_backtest` call for the duration of one test, the same technique
    `tests/test_tools_backtesting.py`'s own
    `test_start_backtest_returns_queued_immediately_without_awaiting_the_watcher`
    already established for isolating `start_backtest` from a real Socket.IO
    connection attempt."""
    real_run_backtest_watcher = backtesting_module._run_backtest_watcher

    async def _fast(client, store, job_id, spec, **kwargs):
        kwargs.setdefault("socketio_client_factory", lambda: fake_sio)
        kwargs["interval_seconds"] = 0
        return await real_run_backtest_watcher(client, store, job_id, spec, **kwargs)

    monkeypatch.setattr(backtesting_module, "_run_backtest_watcher", _fast)


def _patch_compare_strategies_watcher(monkeypatch: pytest.MonkeyPatch, fakes: list[_FakeSocketIOClient]) -> None:
    real_run_compare_strategies = backtesting_module._run_compare_strategies

    async def _fast(client, store, parent_id, child_ids, configs, specs, **kwargs):
        kwargs.setdefault("socketio_client_factory", _sequential_factory(fakes))
        kwargs["interval_seconds"] = 0
        return await real_run_compare_strategies(client, store, parent_id, child_ids, configs, specs, **kwargs)

    monkeypatch.setattr(backtesting_module, "_run_compare_strategies", _fast)


def _handler_completing_immediately(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        return httpx.Response(200, json="Backtesting started")
    return httpx.Response(200, json={"report": {}, "trades": []})


async def _wait_for_terminal_state(client: Client, job_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Poll `get_job_status` through the REAL protocol stack until the job
    reaches a terminal state -- proving polling keeps working exactly as
    before, whether or not this test also supplied a `progressToken`
    (milestone 11's own explicit "polling must be completely unaffected"
    requirement)."""

    async def _poll() -> dict[str, Any]:
        while True:
            result = await client.call_tool("get_job_status", {"job_id": job_id})
            payload = json.loads(result.content[0].text)
            if payload["state"] in ("completed", "failed", "cancelled"):
                return payload
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout=timeout)


def _progress_notifications(received: list[Any]) -> list[Any]:
    return [message for message in received if type(message).__name__ == "ProgressNotification"]


@pytest.mark.anyio
async def test_start_backtest_with_progress_token_delivers_real_mcp_progress_notifications(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_sio = _FakeSocketIOClient(
        [
            {"status": "computing", "progress": 10, "errors": 0},
            {"status": "computing", "progress": 55, "errors": 0},
            {"status": "finished", "progress": 100, "errors": 0},
        ]
    )
    _patch_single_job_watcher(monkeypatch, fake_sio)
    server = _build_test_server(_handler_completing_immediately)
    received: list[Any] = []

    async def message_handler(message: Any) -> None:
        received.append(message)

    async with Client(server, raise_exceptions=True, message_handler=message_handler, mode="legacy") as client:
        result = await client.call_tool(
            "start_backtest",
            {"mode": "data_files", "files": ["a.data"]},
            meta=types.RequestParamsMeta(progress_token="tok-live-1"),
        )
        job_id = json.loads(result.content[0].text)["job_id"]

        status = await _wait_for_terminal_state(client, job_id)
        assert status["state"] == "completed"
        assert status["progress_percent"] == 100.0

    progress_notifications = _progress_notifications(received)
    assert [n.params.progress for n in progress_notifications] == [10.0, 55.0, 100.0]
    assert all(n.params.progress_token == "tok-live-1" for n in progress_notifications)
    assert all(n.params.total == 100.0 for n in progress_notifications)


@pytest.mark.anyio
async def test_start_backtest_without_progress_token_sends_no_notifications_but_polling_still_works(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_sio = _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}])
    _patch_single_job_watcher(monkeypatch, fake_sio)
    server = _build_test_server(_handler_completing_immediately)
    received: list[Any] = []

    async def message_handler(message: Any) -> None:
        received.append(message)

    async with Client(server, raise_exceptions=True, message_handler=message_handler, mode="legacy") as client:
        # No `meta=` at all -- the common, fully-supported case.
        result = await client.call_tool("start_backtest", {"mode": "data_files", "files": ["a.data"]})
        job_id = json.loads(result.content[0].text)["job_id"]

        status = await _wait_for_terminal_state(client, job_id)
        assert status["state"] == "completed"

    assert _progress_notifications(received) == []


@pytest.mark.anyio
async def test_compare_strategies_with_progress_token_reports_combined_progress_over_the_real_protocol(
    monkeypatch: pytest.MonkeyPatch,
):
    fakes = [
        _FakeSocketIOClient(
            [
                {"status": "computing", "progress": 40, "errors": 0},
                {"status": "finished", "progress": 100, "errors": 0},
            ]
        ),
        _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}]),
    ]
    _patch_compare_strategies_watcher(monkeypatch, fakes)
    server = _build_test_server(_handler_completing_immediately)
    received: list[Any] = []

    async def message_handler(message: Any) -> None:
        received.append(message)

    async with Client(server, raise_exceptions=True, message_handler=message_handler, mode="legacy") as client:
        result = await client.call_tool(
            "compare_strategies",
            {
                "configs": [
                    {"mode": "data_files", "files": ["a.data"]},
                    {"mode": "data_files", "files": ["b.data"]},
                ]
            },
            meta=types.RequestParamsMeta(progress_token="tok-parent-1"),
        )
        job_id = json.loads(result.content[0].text)["job_id"]

        status = await _wait_for_terminal_state(client, job_id)
        assert status["state"] == "completed"

    progress_notifications = _progress_notifications(received)
    progresses = [n.params.progress for n in progress_notifications]
    # Same combined-progress arithmetic as this milestone's unit-level test
    # (tests/test_tools_backtesting_progress_notifications.py) -- see that
    # test's own comment for the full derivation.
    assert progresses == [20.0, 50.0, 100.0]
    assert 40.0 not in progresses  # never the raw single-child percentage
    assert all(n.params.progress_token == "tok-parent-1" for n in progress_notifications)


@pytest.mark.anyio
async def test_notification_send_failure_never_fails_the_job_even_over_the_real_protocol(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_sio = _FakeSocketIOClient(
        [
            {"status": "computing", "progress": 10, "errors": 0},
            {"status": "finished", "progress": 100, "errors": 0},
        ]
    )
    _patch_single_job_watcher(monkeypatch, fake_sio)

    async def _raising_send_progress_notification(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated failure: client disconnected / SDK-level error")

    monkeypatch.setattr(
        "mcp.server.session.ServerSession.send_progress_notification",
        _raising_send_progress_notification,
    )

    server = _build_test_server(_handler_completing_immediately)

    async with Client(server, raise_exceptions=True, mode="legacy") as client:
        result = await client.call_tool(
            "start_backtest",
            {"mode": "data_files", "files": ["a.data"]},
            meta=types.RequestParamsMeta(progress_token="tok-fail-1"),
        )
        job_id = json.loads(result.content[0].text)["job_id"]

        # Must not hang or raise, even though EVERY notification-send
        # attempt for this job's token raises inside the real SDK method.
        status = await _wait_for_terminal_state(client, job_id)

    assert status["state"] == "completed"
    assert status["progress_percent"] == 100.0
