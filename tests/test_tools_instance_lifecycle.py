"""Unit tests for task 18's `restart_octobot`/`wait_for_octobot_ready`
(docs/tasklist.md item 18, ADR-0010).

Per the tasklist's explicit "deferred-step-0 substitute" instructions for
this implementation round, EVERY test here mocks the HTTP transport
(`httpx2.MockTransport`) -- none exercises a live OctoBot instance, and no
test issues a real `GET /wait_reboot?reboot=true` against a live instance.

`_profile_card`/`_profiles_selector_html` mirror
`tests/test_tools_profiles_gated.py`'s own minimal fixture builders
(duplicated, not imported -- this project's established per-module test
duplication convention, see e.g. `tests/test_tools_data_collection.py`'s
own docstring for the same precedent) so `restart_octobot`'s reused
`/profiles_selector` scrape can be driven the same way that module's own
tests already do.
"""

import time

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.jobs import JobStore
from octobot_mcp.tools.instance_lifecycle import (
    _LIVE_OR_INCONCLUSIVE_RESTART_REASON,
    _SIMULATOR_RESTART_REASON,
    restart_octobot,
    wait_for_octobot_ready,
)
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _profile_card(
    profile_id: str, name: str, *, selected: bool = False, profile_type_badge: str = "Simulated trading"
) -> str:
    """One minimal `profile-overview` card satisfying every fail-loud assertion
    `_parse_profiles_selector_html` makes (see that function's docstring)."""
    selected_class = " profile-overview-selected" if selected else ""
    active_badge = '<span class="badge badge-danger">active</span>' if selected else ""
    return f"""
<div class="col-12 col-md-6 col-lg-4 px-1 profile-overview{selected_class}">
  <div class="profile-details d-flex pb-1" data-id="{profile_id}">
    <span id="profile-name-{profile_id}">{name}</span>
    <span id="profile-description-{profile_id}">{name} description</span>
    <label class="my-auto" for="{profile_id}profile-complexity">Complexity:</label>
    <span class="badge">Easy</span>
    <label class="my-auto" for="{profile_id}profile-risk">Risk:</label>
    <span class="badge">Low</span>
    <span class="float-right badge badge-info">{profile_type_badge}</span>
    {active_badge}
  </div>
</div>
"""


def _profiles_selector_html(*cards: str) -> str:
    return f"<!DOCTYPE html><html><body>{''.join(cards)}</body></html>"


_SIMULATOR_HTML = _profiles_selector_html(
    _profile_card("sim-profile", "Sim Profile", selected=True, profile_type_badge="Simulated trading"),
)
_LIVE_HTML = _profiles_selector_html(
    _profile_card("live-profile", "Live Profile", selected=True, profile_type_badge="Real trading"),
)
_BROKEN_HTML = "<!DOCTYPE html><html><body>no profile cards here</body></html>"


def _router(profiles_html: str, restart_response: httpx.Response | None = None):
    """One handler dispatching on path -- `/profiles_selector` for the
    confirm-gating reason-string scrape, `/wait_reboot` for the restart call
    itself. `restart_octobot` must never call `/wait_reboot` at all in the
    refusal tests, so its absence (a `None` default that raises if hit) is
    itself an assertion.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=profiles_html)
        if request.url.path == "/wait_reboot":
            if restart_response is None:
                raise AssertionError(
                    "restart_octobot must not call /wait_reboot on this path"
                )
            assert request.url.params.get("reboot") == "true"
            return restart_response
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    return handler


# --- confirm=false / confirm omitted: refusal, reason strings differ -------


@pytest.mark.anyio
async def test_restart_refuses_without_confirm_for_simulator_profile():
    ctx = make_ctx(httpx.MockTransport(_router(_SIMULATOR_HTML)))
    result = await restart_octobot(ctx)
    assert result["confirmed"] is False
    assert result["action"] == "restart_octobot"
    assert result["reason"] == _SIMULATOR_RESTART_REASON
    assert "confirm=true" in result["message"]


@pytest.mark.anyio
async def test_restart_refuses_when_confirm_is_explicitly_false():
    ctx = make_ctx(httpx.MockTransport(_router(_SIMULATOR_HTML)))
    result = await restart_octobot(ctx, confirm=False)
    assert result["confirmed"] is False
    assert result["reason"] == _SIMULATOR_RESTART_REASON


@pytest.mark.anyio
async def test_restart_refuses_without_confirm_for_live_profile_with_different_reason():
    ctx = make_ctx(httpx.MockTransport(_router(_LIVE_HTML)))
    result = await restart_octobot(ctx)
    assert result["confirmed"] is False
    assert result["reason"] == _LIVE_OR_INCONCLUSIVE_RESTART_REASON
    # The two reason strings must actually differ, not just both refuse.
    assert result["reason"] != _SIMULATOR_RESTART_REASON
    assert "LIVE" in result["reason"]


@pytest.mark.anyio
async def test_restart_refuses_without_confirm_using_cautious_reason_when_scrape_is_inconclusive():
    # An empty/broken scrape (ProfileScrapeContractBrokenError) must fail
    # toward the MORE detailed/cautious message, never toward a silent
    # generic/lenient one (ADR-0010 Decision 3).
    ctx = make_ctx(httpx.MockTransport(_router(_BROKEN_HTML)))
    result = await restart_octobot(ctx, confirm=False)
    assert result["confirmed"] is False
    assert result["reason"] == _LIVE_OR_INCONCLUSIVE_RESTART_REASON


# --- confirm=true: active-job refusal (both kinds) --------------------------


@pytest.mark.anyio
async def test_restart_refuses_outright_with_active_backtest_job():
    jobs = JobStore()
    job = jobs.create_job("backtest")
    jobs.start(job.job_id)

    ctx = make_ctx(httpx.MockTransport(_router(_SIMULATOR_HTML)), jobs=jobs)
    result = await restart_octobot(ctx, confirm=True)

    assert result["restart_triggered"] is False
    assert result["active_jobs"] == [{"job_id": job.job_id, "kind": "backtest", "state": "running"}]
    assert job.job_id in result["message"]


@pytest.mark.anyio
async def test_restart_refuses_outright_with_active_data_collection_job():
    jobs = JobStore()
    job = jobs.create_job("data_collection")
    # Left `queued` (never started) -- still counts, per ADR-0010 Decision 4.

    ctx = make_ctx(httpx.MockTransport(_router(_SIMULATOR_HTML)), jobs=jobs)
    result = await restart_octobot(ctx, confirm=True)

    assert result["restart_triggered"] is False
    assert result["active_jobs"] == [{"job_id": job.job_id, "kind": "data_collection", "state": "queued"}]


@pytest.mark.anyio
async def test_restart_proceeds_when_only_terminal_jobs_exist():
    jobs = JobStore()
    job = jobs.create_job("backtest")
    jobs.start(job.job_id)
    jobs.complete(job.job_id, result={})

    restart_ok = httpx.Response(200, text="<html>restarting</html>")

    ctx = make_ctx(httpx.MockTransport(_router(_SIMULATOR_HTML, restart_ok)), jobs=jobs)
    result = await restart_octobot(ctx, confirm=True)
    assert result["restart_triggered"] is True


# --- confirm=true, no active jobs: the one live call ------------------------


@pytest.mark.anyio
async def test_restart_succeeds_on_200_and_never_touches_the_profile_scrape():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            raise AssertionError(
                "restart_octobot must not scrape profile_type at all once confirm=true"
            )
        assert request.url.path == "/wait_reboot"
        assert request.url.params.get("reboot") == "true"
        return httpx.Response(200, text="<html>restarting</html>")

    ctx = make_ctx(httpx.MockTransport(handler))
    result = await restart_octobot(ctx, confirm=True)
    assert result == {
        "restart_triggered": True,
        "message": (
            "OctoBot restart scheduled (~2s delay); the server will be "
            "briefly unreachable. Call wait_for_octobot_ready() next, then "
            "re-confirm the active profile via "
            "get_profile()/list_profiles() before starting a backtest."
        ),
    }


@pytest.mark.anyio
async def test_restart_raises_restart_failed_error_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    ctx = make_ctx(httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc_info:
        await restart_octobot(ctx, confirm=True)
    assert "RestartFailedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_restart_raises_restart_failed_error_on_connection_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    ctx = make_ctx(httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc_info:
        await restart_octobot(ctx, confirm=True)
    assert "RestartFailedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_restart_raises_restart_failed_error_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    ctx = make_ctx(httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc_info:
        await restart_octobot(ctx, confirm=True)
    assert "RestartFailedError" in str(exc_info.value)


# --- wait_for_octobot_ready --------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_octobot_ready_times_out_honestly_against_an_always_unreachable_mock():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    ctx = make_ctx(httpx.MockTransport(handler))
    start = time.monotonic()
    result = await wait_for_octobot_ready(ctx, timeout_seconds=0.05, poll_interval_seconds=0.01)
    real_elapsed = time.monotonic() - start

    assert result["ready"] is False
    assert result["attempts"] >= 1
    assert result["elapsed_seconds"] >= 0
    # Never blocks past its own configured timeout (NFR-17) -- generous
    # slack so this isn't flaky under CI scheduling jitter, but still
    # nowhere near the real 60s default this would take if the bound were
    # broken.
    assert real_elapsed < 2.0


@pytest.mark.anyio
async def test_wait_for_octobot_ready_reports_success_after_transient_failures():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"version": "OctoBot 2.1.1"})

    ctx = make_ctx(httpx.MockTransport(handler))
    result = await wait_for_octobot_ready(ctx, timeout_seconds=5.0, poll_interval_seconds=0.0)

    assert result["ready"] is True
    assert result["attempts"] == 3
    assert result["elapsed_seconds"] >= 0
    assert call_count == 3


@pytest.mark.anyio
async def test_wait_for_octobot_ready_treats_any_status_code_as_reachable():
    # "Ready" means only "the HTTP server answered", per this tool's own
    # docstring -- even a non-2xx response counts, since it proves the
    # transport is back up. One leading connection failure is included so
    # this also satisfies the "ready requires an observed prior outage"
    # invariant below -- without it, this would exercise the exact
    # first-poll-races-the-restart false positive that invariant exists to
    # prevent, not the "non-2xx still counts" behavior this test targets.
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(404, text="not found")

    ctx = make_ctx(httpx.MockTransport(handler))
    result = await wait_for_octobot_ready(ctx, timeout_seconds=1.0, poll_interval_seconds=0.0)
    assert result["ready"] is True
    assert result["attempts"] == 2


@pytest.mark.anyio
async def test_wait_for_octobot_ready_does_not_report_ready_on_first_success_without_a_prior_outage():
    # Regression test for a real bug found via live testing against the
    # OctoBot 2.1.1 test instance: restart_octobot's route schedules the
    # actual kill with a confirmed ~2s delay, so a poll issued immediately
    # after triggering a restart can land within that grace window and hit
    # the OLD, not-yet-killed process -- a false "ready" that never observed
    # the real restart at all (confirmed live, twice: the naive first-
    # success-wins version of this loop returned ready=True in ~0.05s both
    # times, entirely before the real ~2.1s-8.9s outage window even began).
    # A mock that ALWAYS succeeds, with no prior failure ever observed, must
    # now time out honestly rather than report a false positive on its very
    # first poll.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "OctoBot 2.1.1"})

    ctx = make_ctx(httpx.MockTransport(handler))
    result = await wait_for_octobot_ready(ctx, timeout_seconds=0.05, poll_interval_seconds=0.01)
    assert result["ready"] is False
    assert result["attempts"] >= 1
