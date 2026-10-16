"""Unit tests for milestone 20's `get_evaluation_matrix` (docs/tasklist.md
item 20, ADR-0011, FR-34/NFR-20).

See `src/octobot_mcp/tools/trading_config.py`'s module docstring (the
`get_evaluation_matrix` section) for the exact request/response shape these
tests assert, verified against ADR-0011's live-confirmed DOM shape.

`MATRIX_HTML`/`tests/fixtures/matrix_valid.html` is a real, trimmed
snapshot of the live test instance's own `/advanced/matrix` response
(OctoBot 2.1.1) -- 8 real rows across 4 evaluators, including both
documented real non-numeric cases (a literal `"None"` value and an empty
`Time frame` cell) -- not a hand-authored guess. The broken-fixture tests
mutate specific, known substrings of that same real snapshot to violate
exactly one structural assumption each, per NFR-20's acceptance criterion.
"""

import inspect
from pathlib import Path

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.trading_config import get_evaluation_matrix
from tests.test_tools_shared import make_ctx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MATRIX_HTML = (FIXTURES_DIR / "matrix_valid.html").read_text()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _matrix_html_handler(html: str, *, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/advanced/matrix"
        return httpx.Response(status_code, text=html)

    return handler


@pytest.mark.anyio
async def test_get_evaluation_matrix_extracts_all_8_rows():
    result = await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(MATRIX_HTML))))
    assert len(result["entries"]) == 8
    assert result["entries"][0] == {
        "evaluator": "BBMomentumEvaluator",
        "value": 0.03535867173532168,
        "time_frame": "4h",
        "symbol": "BTC/EUR",
        "exchange": "binance",
    }


@pytest.mark.anyio
async def test_get_evaluation_matrix_maps_literal_none_text_to_null():
    result = await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(MATRIX_HTML))))
    entry = next(e for e in result["entries"] if e["evaluator"] == "InstantFluctuationsEvaluator")
    assert entry["value"] is None
    assert entry["time_frame"] == "1m"


@pytest.mark.anyio
async def test_get_evaluation_matrix_maps_empty_time_frame_cell_to_null():
    result = await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(MATRIX_HTML))))
    entry = next(e for e in result["entries"] if e["evaluator"] == "MoveSignalsStrategyEvaluator")
    assert entry["time_frame"] is None
    assert entry["value"] == 0.004291017452765465


@pytest.mark.anyio
async def test_get_evaluation_matrix_passes_through_non_numeric_non_none_value_as_raw_string():
    # Not observed live, but not ruled out either (ADR-0011 Decision 5) --
    # any other non-numeric, non-"None" cell text is passed through as the
    # raw string rather than dropped or coerced.
    weird_html = MATRIX_HTML.replace(
        "<td>0.004291017452765465</td>", "<td>pending</td>", 1
    )
    result = await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(weird_html))))
    entry = next(e for e in result["entries"] if e["evaluator"] == "MoveSignalsStrategyEvaluator")
    assert entry["value"] == "pending"


@pytest.mark.anyio
async def test_get_evaluation_matrix_does_not_raise_on_zero_entries():
    # A legitimately empty matrix (no evaluations run yet) must NOT raise
    # (ADR-0011 Decision 3) -- an intact table/thead with zero body rows.
    zero_rows_html = MATRIX_HTML.replace(
        MATRIX_HTML[MATRIX_HTML.index("<tbody>") + len("<tbody>") : MATRIX_HTML.index("</tbody>")],
        "",
    )
    result = await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(zero_rows_html))))
    assert result["entries"] == []


@pytest.mark.anyio
async def test_get_evaluation_matrix_raises_when_table_is_missing():
    broken_html = MATRIX_HTML.replace('id="matrixDataTable"', 'id="matrixDataTable-renamed"')
    with pytest.raises(ToolError) as exc_info:
        await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(broken_html))))
    assert "EvaluationMatrixScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_evaluation_matrix_raises_when_thead_is_reordered():
    broken_html = MATRIX_HTML.replace(
        '<th scope="col">Value</th>\n        <th scope="col">Time frame</th>',
        '<th scope="col">Time frame</th>\n        <th scope="col">Value</th>',
    )
    with pytest.raises(ToolError) as exc_info:
        await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(broken_html))))
    assert "EvaluationMatrixScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_evaluation_matrix_raises_when_thead_is_renamed():
    broken_html = MATRIX_HTML.replace('<th scope="col">Evaluator</th>', '<th scope="col">Evaluators</th>')
    with pytest.raises(ToolError) as exc_info:
        await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(broken_html))))
    assert "EvaluationMatrixScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_evaluation_matrix_raises_when_a_row_has_the_wrong_cell_count():
    broken_html = MATRIX_HTML.replace("<td>binance</td></tr>", "</tr>", 1)
    with pytest.raises(ToolError) as exc_info:
        await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler(broken_html))))
    assert "EvaluationMatrixScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_evaluation_matrix_never_returns_a_partial_result_on_broken_scrape():
    broken_html = MATRIX_HTML.replace('id="matrixDataTable"', 'id="matrixDataTable-renamed"')
    with pytest.raises(ToolError):
        result_that_should_never_exist = None
        try:
            result_that_should_never_exist = await get_evaluation_matrix(
                make_ctx(httpx.MockTransport(_matrix_html_handler(broken_html)))
            )
        finally:
            assert result_that_should_never_exist is None


@pytest.mark.anyio
async def test_get_evaluation_matrix_classifies_5xx_as_tool_error():
    with pytest.raises(ToolError):
        await get_evaluation_matrix(make_ctx(httpx.MockTransport(_matrix_html_handler("", status_code=500))))


def test_get_evaluation_matrix_has_no_confirm_parameter():
    # Read-only, not confirm-gated (ADR-0003).
    params = inspect.signature(get_evaluation_matrix).parameters
    assert "confirm" not in params
    assert list(params) == ["ctx"]
