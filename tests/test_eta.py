"""Unit tests for `octobot_mcp.eta` (milestone 9, docs/tasklist.md item 9).

Pure function tests -- no `ctx`, no HTTP, no Socket.IO, no `JobStore`
anywhere in this file (that wiring is covered separately in
`tests/test_jobs_store.py`). Exercises exactly the synthetic sequences the
tasklist names: linear-ish progress, stalled-at-0%, single-sample, a
slowdown-then-speedup sequence, and a mid-run stall -- plus the 20-entry/
60-second window bound, and a demo replay of a real captured progress
sequence (milestone 9's own "not a live-instance demo" instruction).
"""

from collections import deque

import pytest

from octobot_mcp.eta import (
    MAX_SAMPLE_WINDOW_SECONDS,
    MAX_SAMPLES,
    STALENESS_MULTIPLE,
    WATCHER_INTERVAL_SECONDS,
    bound_samples,
    estimate_eta,
)

# --- linear-ish progress -------------------------------------------------------


def test_linear_progress_starts_low_confidence_with_few_samples():
    # 2 samples, ~1.5s apart, low progress -- step 3 threshold cleared (>=2
    # samples) but every "low" trigger in step 6 still applies.
    samples = [(0.0, 0.0), (1.5, 5.0)]
    eta_seconds, confidence = estimate_eta(samples, 1.5)
    assert confidence == "low"
    # Still a numeric estimate (INV-2 only forces None for insufficient
    # data/staleness, not merely a "low" confidence tier from step 6).
    assert eta_seconds is not None
    assert eta_seconds > 0


def test_linear_progress_reaches_medium_confidence_with_3_to_9_samples_and_span_over_5s():
    # 5 samples, steady linear rate of ~2%/s, span 8s, progress well above 2%.
    samples = [(0.0, 0.0), (2.0, 4.0), (4.0, 8.0), (6.0, 12.0), (8.0, 16.0)]
    eta_seconds, confidence = estimate_eta(samples, 8.0)
    assert confidence == "medium"
    rate = (16.0 - 0.0) / (8.0 - 0.0)
    assert eta_seconds == pytest.approx((100.0 - 16.0) / rate)


def test_linear_progress_reaches_high_confidence_with_10_plus_samples_and_span_over_30s():
    # 11 samples, 4s apart (span 40s), steady 2%/s rate.
    samples = [(float(i * 4), float(i * 4) * 2.0) for i in range(11)]
    eta_seconds, confidence = estimate_eta(samples, samples[-1][0])
    assert confidence == "high"
    first_elapsed, first_progress = samples[0]
    last_elapsed, last_progress = samples[-1]
    rate = (last_progress - first_progress) / (last_elapsed - first_elapsed)
    assert eta_seconds == pytest.approx((100.0 - last_progress) / rate)


def test_confidence_is_low_when_progress_is_below_2_percent_regardless_of_sample_count():
    # 10 samples spanning 40s (would otherwise qualify as "high"), but
    # progress has barely moved off 0% -- step 6's <2% rule still forces low.
    samples = [(float(i * 4), float(i) * 0.1) for i in range(10)]
    _, confidence = estimate_eta(samples, samples[-1][0])
    assert confidence == "low"


# --- stalled at 0% --------------------------------------------------------------


def test_stalled_at_zero_percent_with_only_one_sample_is_none_low():
    # Dedup (JobStore.record_watcher_tick) would never append a second
    # identical 0% sample, so a "stalled at 0%" run naturally has just one
    # sample in `samples` -- falls out of step 3's len(samples) < 2 guard.
    samples = [(0.0, 0.0)]
    eta_seconds, confidence = estimate_eta(samples, 10.0)
    assert eta_seconds is None
    assert confidence == "low"


# --- single sample ---------------------------------------------------------------


def test_single_sample_is_none_low():
    eta_seconds, confidence = estimate_eta([(3.0, 42.0)], 3.0)
    assert eta_seconds is None
    assert confidence == "low"


def test_empty_samples_is_none_low():
    eta_seconds, confidence = estimate_eta([], 0.0)
    assert eta_seconds is None
    assert confidence == "low"


# --- slowdown-then-speedup: window-based rate, not whole-run average ------------


def test_window_based_rate_differs_materially_from_whole_run_average_after_a_slowdown_then_speedup():
    # Fast start (0 -> 40% over 10s = 4%/s), then a sharp slowdown (40% ->
    # 42% over the next 40s = 0.05%/s), then a speedup again (42% -> 60%
    # over the last 6s = 3%/s). The bounded window only ever holds the most
    # recent MAX_SAMPLES entries, so by the time the speedup phase's
    # samples fill the window, window-based rate should reflect the RECENT
    # rate (~3%/s), not the whole-run average rate.
    all_samples = [
        (0.0, 0.0),
        (2.5, 10.0),
        (5.0, 20.0),
        (7.5, 30.0),
        (10.0, 40.0),
        (20.0, 40.5),
        (30.0, 41.0),
        (40.0, 41.5),
        (50.0, 42.0),
        (52.0, 48.0),
        (54.0, 54.0),
        (56.0, 60.0),
    ]
    whole_run_rate = (all_samples[-1][1] - all_samples[0][1]) / (all_samples[-1][0] - all_samples[0][0])
    whole_run_eta = (100.0 - all_samples[-1][1]) / whole_run_rate

    # Simulate the bounded window (MAX_SAMPLES=20 never trims here since
    # there are only 12 entries, so instead trim explicitly to the last 4
    # samples -- the recent speedup phase -- to model what the 60s window
    # bound would eventually converge to once older entries age out).
    window = all_samples[-4:]
    eta_seconds, _ = estimate_eta(window, window[-1][0])

    recent_rate = (window[-1][1] - window[0][1]) / (window[-1][0] - window[0][0])
    assert recent_rate == pytest.approx(3.0)
    expected_window_eta = (100.0 - window[-1][1]) / recent_rate
    assert eta_seconds == pytest.approx(expected_window_eta)

    # The whole point: the window-based estimate is materially different
    # from (here, much smaller than) the naive whole-run-average estimate,
    # proving the window actually changes the answer, not just that some
    # number comes out.
    assert eta_seconds < whole_run_eta * 0.5


# --- mid-run stall: staleness forces None/"low" even over confident samples ------


def test_mid_run_stall_forces_none_low_overriding_an_otherwise_confident_estimate():
    # 10 samples over 40s, steady rate, progress at 45% -- would otherwise
    # be a confident "high" estimate (regression-proof: confirm this first).
    samples = [(float(i * 4), float(i * 4) * 1.125) for i in range(10)]
    assert samples[-1] == (36.0, 40.5)
    current_elapsed_no_gap = samples[-1][0] + WATCHER_INTERVAL_SECONDS
    eta_before, confidence_before = estimate_eta(samples, current_elapsed_no_gap)
    assert confidence_before == "high"
    assert eta_before is not None
    assert eta_before > 0

    # Now simulate the exact same samples, but real time has moved on by
    # more than STALENESS_MULTIPLE * WATCHER_INTERVAL_SECONDS (15s at the
    # default 1.5s interval) since the last sample arrived, with no new
    # sample recorded (a mid-run stall while the job stays "running").
    stale_gap = STALENESS_MULTIPLE * WATCHER_INTERVAL_SECONDS + 1.0
    current_elapsed_stalled = samples[-1][0] + stale_gap
    eta_after, confidence_after = estimate_eta(samples, current_elapsed_stalled)

    assert eta_after is None
    assert confidence_after == "low"


def test_staleness_threshold_is_exactly_10x_the_watcher_interval():
    assert STALENESS_MULTIPLE == 10
    assert WATCHER_INTERVAL_SECONDS == 1.5

    samples = [(0.0, 10.0), (10.0, 45.0)]
    just_under = samples[-1][0] + (STALENESS_MULTIPLE * WATCHER_INTERVAL_SECONDS)
    eta_seconds, _confidence = estimate_eta(samples, just_under)
    assert eta_seconds is not None  # not forced stale yet (staleness == threshold, not >)

    just_over = samples[-1][0] + (STALENESS_MULTIPLE * WATCHER_INTERVAL_SECONDS) + 0.001
    eta_seconds, confidence = estimate_eta(samples, just_over)
    assert eta_seconds is None
    assert confidence == "low"


# --- other guards ------------------------------------------------------------


def test_rate_less_than_or_equal_zero_is_none_low():
    samples = [(0.0, 50.0), (5.0, 50.0)]  # no progress movement (shouldn't normally
    # happen since dedup prevents identical consecutive samples, but guarded anyway)
    eta_seconds, confidence = estimate_eta(samples, 5.0)
    assert eta_seconds is None
    assert confidence == "low"


def test_negative_rate_is_none_low():
    samples = [(0.0, 50.0), (5.0, 30.0)]
    eta_seconds, confidence = estimate_eta(samples, 5.0)
    assert eta_seconds is None
    assert confidence == "low"


def test_zero_span_window_is_none_low():
    samples = [(3.0, 10.0), (3.0, 20.0)]  # same elapsed, different progress (edge case)
    eta_seconds, confidence = estimate_eta(samples, 3.0)
    assert eta_seconds is None
    assert confidence == "low"


# --- 20-entry / 60-second window bounding ---------------------------------------


def test_bound_samples_caps_at_max_entries():
    samples = deque((float(i), float(i)) for i in range(MAX_SAMPLES + 5))
    bound_samples(samples)
    assert len(samples) == MAX_SAMPLES
    # Keeps the most recent entries, drops the oldest.
    assert samples[0] == (5.0, 5.0)
    assert samples[-1] == (float(MAX_SAMPLES + 4), float(MAX_SAMPLES + 4))


def test_bound_samples_drops_entries_older_than_max_span_seconds():
    # 5 samples spread across 100 seconds -- only entries within the last
    # MAX_SAMPLE_WINDOW_SECONDS of the newest one should survive.
    samples = deque([(0.0, 1.0), (20.0, 2.0), (50.0, 3.0), (80.0, 4.0), (100.0, 5.0)])
    bound_samples(samples)
    newest = samples[-1][0]
    assert all(newest - elapsed <= MAX_SAMPLE_WINDOW_SECONDS for elapsed, _ in samples)
    assert (0.0, 1.0) not in samples  # 100s old relative to newest -- evicted
    assert (20.0, 2.0) not in samples  # 80s old -- evicted
    assert samples[-1] == (100.0, 5.0)


def test_bound_samples_never_empties_a_non_empty_deque():
    samples = deque([(0.0, 1.0), (200.0, 2.0)])
    bound_samples(samples)
    assert len(samples) >= 1


def test_bound_samples_is_a_noop_when_within_both_bounds():
    samples = deque([(0.0, 1.0), (5.0, 2.0), (10.0, 3.0)])
    bound_samples(samples)
    assert list(samples) == [(0.0, 1.0), (5.0, 2.0), (10.0, 3.0)]


# --- demo: replay a real captured progress sequence -----------------------------

#: Captured live against the OctoBot 2.1.1 test instance
#: (http://192.168.178.100:5001) for this milestone's own demo criterion, by
#: reusing this project's own `list_profiles`/`select_profile`/
#: `start_backtest`-shaped Socket.IO round trip against the same
#: `ExchangeHistoryDataCollector_..._data` BTC/EUR-on-binance data file
#: milestone 8's own smoke test used, watched at the real
#: `WATCHER_INTERVAL_SECONDS` (1.5s) cadence: 15 real
#: `(elapsed_seconds, progress_percent)` ticks, "starting" -> "computing"
#: -> "finished" over ~21 seconds.
REAL_CAPTURED_SEQUENCE: list[tuple[float, float]] = [
    (0.003, 0.0),
    (1.521, 1.22446895424837),
    (3.032, 9.061478758169939),
    (4.543, 16.509395424836605),
    (6.05, 24.6905637254902),
    (7.57, 32.99428104575164),
    (9.081, 41.31638071895425),
    (10.597, 48.98386437908496),
    (12.108, 56.499183006535944),
    (13.619, 64.8406862745098),
    (15.131, 73.19342320261437),
    (16.642, 81.47058823529412),
    (18.148, 88.90829248366013),
    (19.659, 96.47875816993464),
    (21.17, 100.0),
]


def test_demo_replays_a_real_captured_progress_sequence_and_produces_sane_etas():
    """Replays the real sequence through the estimator tick-by-tick, exactly
    as `JobStore.record_watcher_tick` would (bounding + estimating after
    each new sample), and sanity-checks the resulting ETAs: never negative,
    never wildly larger than the run's own real total duration, and trending
    toward 0 as progress approaches 100%.
    """
    samples: deque[tuple[float, float]] = deque()
    results: list[tuple[float, float | None, str]] = []
    real_total_duration = REAL_CAPTURED_SEQUENCE[-1][0]

    for elapsed, progress in REAL_CAPTURED_SEQUENCE:
        samples.append((elapsed, progress))
        bound_samples(samples)
        eta_seconds, confidence = estimate_eta(samples, elapsed)
        results.append((elapsed, eta_seconds, confidence))

    # Sanity: no negative ETA, ever.
    for _elapsed, eta_seconds, _confidence in results:
        if eta_seconds is not None:
            assert eta_seconds >= 0

    # Sanity: once confidence is above "low" (i.e. the estimate is meant to
    # be trusted, not just an early rough guess off <2% progress), the ETA
    # should be in the same order of magnitude as the real remaining run
    # time -- not nonsense (e.g. not 100x the actual total duration).
    for elapsed, eta_seconds, confidence in results:
        if eta_seconds is not None and confidence != "low":
            assert eta_seconds < real_total_duration * 5

    # Midway through the run (progress ~41%, t=9.081s), a sensible non-null
    # estimate should already exist given the mostly-linear real progress.
    midpoint = next(r for r in results if r[0] == 9.081)
    assert midpoint[1] is not None
    assert midpoint[1] > 0

    # Near the end (progress ~96%), remaining time should be small.
    near_end = next(r for r in results if r[0] == 19.659)
    assert near_end[1] is not None
    assert near_end[1] < real_total_duration

    # Confidence should never regress to "low" purely from more data
    # accumulating on this steadily-advancing real sequence (only the
    # early low-sample-count/low-progress ticks should be "low").
    confidences = [confidence for _, _, confidence in results]
    assert confidences[0] == "low"  # first tick: len(samples) < 2
    assert "medium" in confidences or "high" in confidences
