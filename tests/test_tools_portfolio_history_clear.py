"""Unit tests for the milestone-12 confirm-gated history-clear tools (docs/tasklist.md item 12).

`clear_orders_history`/`clear_trades_history`/`clear_portfolio_history`/
`clear_transactions_history` -- see `src/octobot_mcp/tools/portfolio.py`'s
per-tool docstrings for the exact request/response shapes, re-verified
against OctoBot source this session. Each tool gets a refusal test
(asserting no HTTP call at all reaches OctoBot when `confirm` is omitted or
`false`), a success test (with `confirm=true`, passthrough per NFR-8), and
one error-classification test, per ADR-0003 and this project's established
pattern (`tests/test_tools_profiles_gated.py`).
"""

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.portfolio import (
    clear_orders_history,
    clear_portfolio_history,
    clear_trades_history,
    clear_transactions_history,
)
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


# Table of (tool, action_name, target, path, success_title) shared across the
# parametrized-by-hand tests below -- kept as plain functions (not
# `pytest.mark.parametrize`) so each failure clearly names the offending tool
# in its own test id, matching this project's established style.
_TOOLS = [
    (clear_orders_history, "clear_orders_history", "orders history", "/api/clear_orders_history", "Cleared orders history"),
    (clear_trades_history, "clear_trades_history", "trades history", "/api/clear_trades_history", "Cleared trades history"),
    (clear_portfolio_history, "clear_portfolio_history", "portfolio history", "/api/clear_portfolio_history", "Cleared portfolio history"),
    (clear_transactions_history, "clear_transactions_history", "transactions history", "/api/clear_transactions_history", "Cleared transactions history"),
]


@pytest.mark.anyio
async def test_all_four_refuse_without_confirm_and_make_no_octobot_call():
    for tool, action, target, _path, _title in _TOOLS:

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"OctoBot should never be called for {action} when confirm is not true")

        result = await tool(make_ctx(httpx.MockTransport(handler)))
        assert result["confirmed"] is False
        assert result["action"] == action
        assert result["target"] == target
        assert "cannot be undone" in result["reason"]
        assert "confirm=true" in result["message"]


@pytest.mark.anyio
async def test_all_four_refuse_when_confirm_is_explicitly_false():
    for tool, _action, _target, _path, _title in _TOOLS:

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("OctoBot should never be called when confirm=false")

        result = await tool(make_ctx(httpx.MockTransport(handler)), confirm=False)
        assert result["confirmed"] is False


@pytest.mark.anyio
async def test_all_four_succeed_when_confirmed_and_pass_through_octobots_response():
    for tool, _action, _target, path, title in _TOOLS:
        seen: dict = {}

        def handler(request: httpx.Request, path=path, title=title) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, json={"title": title})

        result = await tool(make_ctx(httpx.MockTransport(handler)), confirm=True)
        assert seen == {"method": "POST", "path": path}
        assert result == {"title": title}


@pytest.mark.anyio
async def test_all_four_classify_5xx_as_a_tool_error():
    for tool, _action, _target, _path, _title in _TOOLS:

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json="Internal error")

        with pytest.raises(ToolError) as exc_info:
            await tool(make_ctx(httpx.MockTransport(handler)), confirm=True)
        assert "OctoBotServerError" in str(exc_info.value)


@pytest.mark.anyio
async def test_all_four_classify_4xx_as_a_tool_error():
    for tool, _action, _target, _path, _title in _TOOLS:

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json="Not found")

        with pytest.raises(ToolError) as exc_info:
            await tool(make_ctx(httpx.MockTransport(handler)), confirm=True)
        assert "OctoBotClientError" in str(exc_info.value)
