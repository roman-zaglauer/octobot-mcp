"""Unit tests for milestone 20's `get_portfolio_snapshot` (docs/tasklist.md
item 20, ADR-0011, FR-33/NFR-19).

See `src/octobot_mcp/tools/portfolio.py`'s module docstring (the
`get_portfolio_snapshot` section) for the exact request/response shape
these tests assert, verified against ADR-0011's live-confirmed DOM shape.

`PORTFOLIO_HTML`/`tests/fixtures/portfolio_valid.html` is a real, trimmed
snapshot of the live test instance's own `/portfolio` response (OctoBot
2.1.1) -- 2 real holdings (BTC, EUR), both with the single confirmed
`"<Exchange>: <amount>"` tooltip shape -- not a hand-authored guess. The
broken-fixture tests mutate specific, known substrings of that same real
snapshot to violate exactly one structural assumption each, per NFR-19's
acceptance criterion.
"""

import inspect
from pathlib import Path

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.portfolio import get_portfolio_snapshot
from tests.test_tools_shared import make_ctx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PORTFOLIO_HTML = (FIXTURES_DIR / "portfolio_valid.html").read_text()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _portfolio_html_handler(html: str, *, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/portfolio"
        return httpx.Response(status_code, text=html)

    return handler


@pytest.mark.anyio
async def test_get_portfolio_snapshot_extracts_reference_currency_and_assets():
    result = await get_portfolio_snapshot(make_ctx(httpx.MockTransport(_portfolio_html_handler(PORTFOLIO_HTML))))
    assert result["reference_currency"] == "EUR"
    assets_by_symbol = {a["asset"]: a for a in result["assets"]}
    assert set(assets_by_symbol) == {"BTC", "EUR"}

    btc = assets_by_symbol["BTC"]
    assert btc["total"] == {
        "rounded": 0.00010725,
        "exact": 0.00010725,
        "per_exchange": [{"exchange": "Binance", "amount": 0.00010725}],
    }
    assert btc["value_in_reference_currency"] == {"rounded": 7.87631505375}
    assert "exact" not in btc["value_in_reference_currency"]
    assert btc["available"] == {
        "rounded": 0.00000725,
        "exact": 0.00000725,
        "per_exchange": [{"exchange": "Binance", "amount": 0.00000725}],
    }
    assert btc["locked_in_orders"] == {
        "rounded": 0.00010000,
        "exact": 0.00010000,
        "per_exchange": [{"exchange": "Binance", "amount": 0.00010000}],
    }

    eur = assets_by_symbol["EUR"]
    assert eur["locked_in_orders"] == {
        "rounded": 0.0,
        "exact": 0.0,
        "per_exchange": [{"exchange": "Binance", "amount": 0.0}],
    }


@pytest.mark.anyio
async def test_get_portfolio_snapshot_does_not_raise_on_zero_assets():
    # Zero holdings is a legitimate OctoBot state (ADR-0011 Decision 3) --
    # an intact card/table with no tr.symbol-holding rows must NOT raise.
    zero_assets_html = PORTFOLIO_HTML.replace(
        PORTFOLIO_HTML[PORTFOLIO_HTML.index("<tbody>") + len("<tbody>") : PORTFOLIO_HTML.index("</tbody>")],
        "",
    )
    result = await get_portfolio_snapshot(make_ctx(httpx.MockTransport(_portfolio_html_handler(zero_assets_html))))
    assert result["reference_currency"] == "EUR"
    assert result["assets"] == []


@pytest.mark.anyio
async def test_get_portfolio_snapshot_degrades_gracefully_on_unparseable_tooltip():
    # A tooltip that doesn't match the one confirmed single-exchange
    # "<Exchange>: <amount>" shape must degrade to exact/per_exchange: null
    # -- never raise, never guess a split (ADR-0011 Decision 4, open
    # question #15).
    broken_html = PORTFOLIO_HTML.replace(
        'title="Binance: 0.00010725"', 'title="Binance: 0.00005; Kraken: 0.00005"'
    )
    result = await get_portfolio_snapshot(make_ctx(httpx.MockTransport(_portfolio_html_handler(broken_html))))
    btc = next(a for a in result["assets"] if a["asset"] == "BTC")
    assert btc["total"]["rounded"] == 0.00010725
    assert btc["total"]["exact"] is None
    assert btc["total"]["per_exchange"] is None


@pytest.mark.anyio
async def test_get_portfolio_snapshot_raises_when_holdings_table_is_missing():
    broken_html = PORTFOLIO_HTML.replace('id="holdings-table"', 'id="holdings-table-renamed"')
    with pytest.raises(ToolError) as exc_info:
        await get_portfolio_snapshot(make_ctx(httpx.MockTransport(_portfolio_html_handler(broken_html))))
    assert "PortfolioScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_portfolio_snapshot_raises_when_reference_market_attribute_is_missing():
    broken_html = PORTFOLIO_HTML.replace(' reference_market="EUR"', "")
    with pytest.raises(ToolError) as exc_info:
        await get_portfolio_snapshot(make_ctx(httpx.MockTransport(_portfolio_html_handler(broken_html))))
    assert "PortfolioScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_portfolio_snapshot_raises_when_a_row_is_missing_its_symbol_span():
    broken_html = PORTFOLIO_HTML.replace('<span class="symbol">BTC</span>', "", 1)
    with pytest.raises(ToolError) as exc_info:
        await get_portfolio_snapshot(make_ctx(httpx.MockTransport(_portfolio_html_handler(broken_html))))
    assert "PortfolioScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_portfolio_snapshot_never_returns_a_partial_result_on_broken_scrape():
    broken_html = PORTFOLIO_HTML.replace('id="portfoliosCard"', 'id="portfoliosCard-renamed"')
    with pytest.raises(ToolError):
        result_that_should_never_exist = None
        try:
            result_that_should_never_exist = await get_portfolio_snapshot(
                make_ctx(httpx.MockTransport(_portfolio_html_handler(broken_html)))
            )
        finally:
            assert result_that_should_never_exist is None


@pytest.mark.anyio
async def test_get_portfolio_snapshot_classifies_5xx_as_tool_error():
    with pytest.raises(ToolError):
        await get_portfolio_snapshot(make_ctx(httpx.MockTransport(_portfolio_html_handler("", status_code=500))))


def test_get_portfolio_snapshot_has_no_confirm_parameter():
    # Read-only, not confirm-gated (ADR-0003).
    params = inspect.signature(get_portfolio_snapshot).parameters
    assert "confirm" not in params
    assert list(params) == ["ctx"]
