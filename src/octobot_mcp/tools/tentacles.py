"""Tentacle config + logs, including the honest Tier-C stubs (milestone 13, docs/tasklist.md item 13).

`get_tentacle_config`/`update_tentacle_config`/`export_logs` all map to
OctoBot's confirmed JSON/binary routes (Tier A, ADR-0006) -- verified
against source (the web interface controllers/models this project has
always read from `Drakkar-Software/OctoBot`'s monorepo path,
`packages/tentacles/Services/Interfaces/web_interface/`, which is kept in
sync with the standalone `Drakkar-Software/OctoBot-Tentacles` repo; both
serve identical content, confirmed this session) AND reproduced live
against the OctoBot 2.1.1 test instance this session, since none of the
three had been read/verified by this project before this milestone. `list_tentacles`/
`get_logs` are Tier C (ADR-0006): OctoBot has no JSON API for enumerating
every tentacle or listing log entries at all (only per-tentacle config
read/write, and a whole-log-file export), so both return a static,
structured "not available" explanation instead of a scraper -- ADR-0006's
"Alternatives considered" explicitly rejected scraping every HTML-only page
as multiplying fragile surface for marginal value. Neither ever makes an
OctoBot HTTP call or raises.

## `get_tentacle_config`/`update_tentacle_config`: a genuine spec correction

The spec's `docs/specs/octobot-mcp-tool-spec.md` "Tentacles & logs" table
names `GET /config_tentacle`/`POST /config_tentacle` for both tools. Reading
the actual route handler
(`Services/Interfaces/web_interface/controllers/tentacles_config.py::
config_tentacle()`) shows this is only half right:

- **`GET /config_tentacle` has NO JSON response path at all.** Its GET
  branch (confirmed by reading the full function body) always calls
  `flask.render_template('config_tentacle.html', ...)` -- an HTML page
  built for the browser UI, with or without query parameters. There is no
  code path in this route that returns JSON.
- The actual JSON-returning read route (confirmed against source AND
  reproduced live: `GET /config_tentacle_edit_details/DailyTradingMode` on
  the OctoBot 2.1.1 test instance -> HTTP 200, `{"name":
  "DailyTradingMode", "config": {...100+ real fields...}, "displayed_elements":
  {...}}`) is a **different, dedicated route**:
  `GET /config_tentacle_edit_details/<tentacle>`, where `tentacle` is a URL
  **path segment** (the tentacle's class name), not a query parameter.
  `get_tentacle_config` maps to this route instead of the spec's named one.
- **`POST /config_tentacle` (the write side) is correctly named in the
  spec**, but its exact shape needed the same live/source verification: the
  tentacle name is the query parameter `name` (`flask.request.args.get
  ("name")`), paired with `action=update`; the JSON request body is the raw
  patch dict itself, `flask.request.get_json()`, passed straight through to
  `models.update_tentacle_config(tentacle_name, request_data, ...)` --
  **not** wrapped in any envelope key. Confirmed live this session
  (`DailyTradingMode`'s `disable_buy_orders` field toggled `false -> true`
  and restored `true -> false` against the OctoBot 2.1.1 test instance).
- **The spec's "batch variant" question, resolved**: a separate route,
  `POST /config_tentacles` (plural), does exist and does accept multiple
  tentacles' patches keyed by tentacle name in one call (confirmed against
  source, same controller module) -- but nothing in this tool's spec entry
  or `docs/tasklist.md` asks for a batch-update capability, and the
  single-tentacle route above is sufficient for `update_tentacle_config`'s
  one-tentacle-at-a-time contract. This module deliberately does not expose
  the batch route as a second tool.

Both routes share the same "the `Content-Type: application/json` response
header lies about the body's actual encoding on failure" quirk already
documented throughout `profiles.py`/`exchanges.py`: OctoBot's own success
bodies are real JSON (a bare string via `flask.jsonify(...)`, confirmed live:
`"DailyTradingMode updated"`), but its failure bodies
(`util.get_rest_reply(<plain str>, 500)`) are raw, unquoted text --
confirmed live for both routes this session (`"Can't find tentacle:
<name>"` for the GET route, `"Can't find <name> class"` for the POST
route). This module's own `_extract_error_text` helper (mirroring
`profiles.py`'s/`exchanges.py`'s identically-named, independently
duplicated helpers -- this project's established convention, not shared
across modules) handles this the same way: try a JSON body first, fall back
to raw text.

## `export_logs`: verified as the exact same binary-download pattern as `export_profile`

`GET /export_logs` (confirmed against source
`Services/Interfaces/web_interface/controllers/logs.py::export_logs()` AND
reproduced live: a real ~10MB zip returned by the OctoBot 2.1.1 test
instance) returns raw zip bytes via `flask.send_file`, exactly like
`octobot_mcp.tools.profiles.export_profile`'s `/profiles_management/export`
-- so this tool reuses that same pattern, including the shared
`octobot_mcp.tools._shared._parse_content_disposition_filename` helper
(moved there this milestone specifically so both consumers share one
implementation; see that helper's docstring). Unlike `export_profile`, the
downloaded filename is not dynamic: OctoBot's own controller always calls
`flask_util.send_and_remove_file(file_path, "logs_export.zip")` with a
hardcoded `download_name` -- confirmed against source and reproduced live
(`Content-Disposition: attachment; filename=logs_export.zip`, unquoted
since that literal name is already a bare HTTP token). This tool still
reads it off the response header rather than hardcoding it locally, so a
future OctoBot version changing that hardcoded name is still reflected
correctly.

No `from __future__ import annotations` here: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring for why
postponed annotations would silently break this module's Context
injection.
"""

import base64
from typing import Any

import httpx2 as httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import get_client
from octobot_mcp.errors import (
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotServerError,
    OctoBotTimeoutError,
    TentacleConfigUpdateRejectedError,
)
from octobot_mcp.tools._shared import _parse_content_disposition_filename, with_error_handling

_CONFIG_TENTACLE_PATH = "/config_tentacle"
_EXPORT_LOGS_PATH = "/export_logs"


async def _request_raw(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    """Perform one request without decoding JSON, classifying only transport failures.

    Same reason and shape as `octobot_mcp.tools.profiles._request_raw`/
    `octobot_mcp.tools.exchanges._request_raw` (independently duplicated in
    each module by this project's established convention, not shared):
    depending on the route and outcome, these routes return a bare JSON
    string, a raw unquoted error string with a lying `Content-Type`, or
    binary zip bytes -- never a single uniform shape
    `octobot_mcp.client.request_json` could decode. Only transport-level
    failures are classified here; every call site classifies its own status
    codes.
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    Mirrors `octobot_mcp.tools.profiles._extract_error_text`/
    `octobot_mcp.tools.exchanges._extract_error_text`: tries a JSON body
    first, falling back to the raw response text -- both `config_tentacle`
    routes' failure bodies (confirmed against source and live) are plain,
    unquoted text despite a `Content-Type: application/json` header, so the
    JSON decode attempt always falls through to the raw-text branch for
    these two specific routes, but the JSON-first attempt is kept for
    consistency with every other module's identical helper and in case a
    future OctoBot version starts returning real JSON error bodies here.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


@with_error_handling
async def get_tentacle_config(ctx: Context, tentacle: str) -> Any:
    """Fetch one tentacle's current configuration, schema, and display metadata.

    Maps to `GET /config_tentacle_edit_details/<tentacle>` -- see this
    module's docstring for the spec correction (the spec named `GET
    /config_tentacle`, which has no JSON response path at all). `tentacle`
    is the tentacle's class name (e.g. `"DailyTradingMode"`), confirmed live
    against the OctoBot 2.1.1 test instance.

    Returns OctoBot's own JSON body unchanged (NFR-8): `{"name": str,
    "config": object, "displayed_elements": object}`. `config` holds this
    tentacle's actual configuration values (the same values
    `update_tentacle_config`'s `patch` argument would merge into);
    `displayed_elements` is the JSON-schema-shaped UI-form metadata
    OctoBot's own config page renders from, passed through unsimplified.

    Read-only, not confirm-gated. An unknown `tentacle` name raises an
    uncaught exception inside OctoBot's model layer, surfaced as
    `OctoBotServerError` (confirmed live: HTTP 500, plain-text body `"Can't
    find tentacle: <tentacle>"`, despite a `Content-Type: application/json`
    header that doesn't actually describe the body -- handled via this
    module's `_extract_error_text`, same as `update_tentacle_config`).
    """
    client = get_client(ctx)
    response = await _request_raw(client, "GET", f"/config_tentacle_edit_details/{tentacle}")
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} fetching "
            f"config for tentacle={tentacle!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} fetching "
            f"config for tentacle={tentacle!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for "
            f"GET /config_tentacle_edit_details/{tentacle}."
        ) from exc


@with_error_handling
async def update_tentacle_config(ctx: Context, tentacle: str, patch: dict[str, Any]) -> Any:
    """Update one tentacle's configuration for the currently active profile.

    Maps to `POST /config_tentacle?name=<tentacle>&action=update`, JSON
    body = `patch` (the raw patch dict, unwrapped) -- see this module's
    docstring for the full source-verified/live-reproduced derivation of
    this exact shape, including why a distinct batch route
    (`POST /config_tentacles`) exists but is deliberately not exposed here.

    OctoBot's own success response body is the literal JSON string
    `"<tentacle> updated"` (confirmed live: `DailyTradingMode`, toggled and
    restored during this milestone's verification). Failure (confirmed live
    with an unrecognized tentacle name: HTTP 500, plain-text body `"Can't
    find <tentacle> class"`) is handled via this module's
    `_extract_error_text`, since that body is not actually JSON despite the
    `Content-Type: application/json` header OctoBot still sends.

    Output: `{"tentacle": str, "updated_fields": patch, "message": str}` --
    `message` is OctoBot's own decoded success string; `updated_fields`
    echoes the `patch` this tool sent (same idiom as
    `octobot_mcp.tools.profiles.update_profile`).

    Not confirm-gated (ADR-0003): mutates tentacle configuration, does not
    destroy data, expose credentials, or enable live trading. Raises
    `TentacleConfigUpdateRejectedError` on any non-2xx response.
    """
    client = get_client(ctx)
    response = await _request_raw(
        client,
        "POST",
        _CONFIG_TENTACLE_PATH,
        params={"name": tentacle, "action": "update"},
        json=patch,
    )
    if response.status_code >= 400:
        raise TentacleConfigUpdateRejectedError(
            f"OctoBot rejected updating tentacle={tentacle!r}: {_extract_error_text(response)}"
        )
    try:
        message = response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for "
            f"POST {_CONFIG_TENTACLE_PATH}?name={tentacle}&action=update."
        ) from exc
    return {"tentacle": tentacle, "updated_fields": patch, "message": message}


@with_error_handling
async def export_logs(ctx: Context) -> Any:
    """Export OctoBot's full logs as a zip archive, base64-encoded.

    Maps to `GET /export_logs` -- the exact same binary-file-download
    pattern as `octobot_mcp.tools.profiles.export_profile` (`flask.send_file`,
    not JSON): this tool bypasses `octobot_mcp.client.request_json` for the
    same reason and base64-encodes the raw response body. See this module's
    docstring for the source/live verification detail, including why the
    downloaded filename (unlike `export_profile`'s) is not dynamic.

    `filename` is read from the response's `Content-Disposition` header via
    the shared `octobot_mcp.tools._shared._parse_content_disposition_filename`
    helper -- the same one `export_profile` uses -- rather than hardcoding
    OctoBot's own literal `"logs_export.zip"` locally.

    On failure, OctoBot's own controller (confirmed against source) catches
    every export exception internally, flashes an HTML-only session
    message, and 302-redirects back to `/logs` -- never a 4xx/5xx status.
    This tool cannot distinguish that redirect from a genuine success at
    the HTTP-status level alone, so -- the same fail-loud approach
    `export_profile` uses for its own missing-header case -- it raises
    `OctoBotMalformedResponseError` whenever no parsable `filename` is found
    in the response, rather than ever returning a fabricated filename or
    empty content.

    Output: `{"filename": str, "content_base64": str}`. Read-only, not
    confirm-gated (ADR-0003).
    """
    client = get_client(ctx)
    response = await _request_raw(client, "GET", _EXPORT_LOGS_PATH)
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} exporting logs: "
            f"{_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} exporting logs: "
            f"{_extract_error_text(response)}",
            status_code=response.status_code,
        )

    content_disposition = response.headers.get("content-disposition", "")
    filename = _parse_content_disposition_filename(content_disposition)
    if not filename:
        raise OctoBotMalformedResponseError(
            "OctoBot's export_logs response had no parsable filename in its "
            f"Content-Disposition header ({content_disposition!r}) -- this can "
            "also mean OctoBot's own export failed internally and silently "
            "redirected to /logs instead of returning a file (confirmed "
            "against source, see this module's docstring)."
        )
    return {
        "filename": filename,
        "content_base64": base64.b64encode(response.content).decode("ascii"),
    }


# --- Tier C stubs (ADR-0006): no JSON API exists for either, ever ----------

_LIST_TENTACLES_UNAVAILABLE: dict[str, Any] = {
    "available": False,
    "reason": (
        "OctoBot has no JSON API for enumerating every tentacle it knows "
        "about (ADR-0006, Tier C): the only routes for a full tentacle "
        "listing render an HTML page for the browser UI, not a JSON "
        "response. Building an HTML scraper for this was deliberately "
        "rejected (see docs/adr/0006-read-model-acquisition-strategy.md's "
        "'Alternatives considered') as multiplying fragile, "
        "OctoBot-UI-version-coupled surface for marginal value. This "
        "includes evaluators specifically: a dedicated third scrape "
        "adapter to enumerate evaluators/their activation state for "
        "`update_evaluator_config` was also considered and rejected, same "
        "reasoning (see docs/adr/0009-evaluator-discovery-third-tier-b-"
        "adapter.md) -- obtain evaluator class names/state from the "
        "OctoBot web UI's `/advanced/evaluator_config` page, OctoBot's own "
        "tentacle documentation, or the user."
    ),
    "alternative": "get_tentacle_config",
}

_GET_LOGS_UNAVAILABLE: dict[str, Any] = {
    "available": False,
    "reason": (
        "OctoBot has no JSON API for listing/querying individual log "
        "entries (ADR-0006, Tier C): `/logs` only renders an HTML page for "
        "the browser UI, and the only machine-readable log route "
        "(`/export_logs`) returns the entire log history as one zip "
        "archive, not structured/filterable entries. Building an HTML "
        "scraper for this was deliberately rejected (see "
        "docs/adr/0006-read-model-acquisition-strategy.md's 'Alternatives "
        "considered') as multiplying fragile, OctoBot-UI-version-coupled "
        "surface for marginal value."
    ),
    "alternative": "export_logs",
}


@with_error_handling
async def list_tentacles(ctx: Context) -> Any:
    """Tier C stub (ADR-0006): tentacle enumeration has no JSON API in OctoBot.

    Never makes an HTTP call to OctoBot and never raises -- this is a
    static, instant response explaining the gap, not a failure (`ctx` is
    accepted only for interface consistency with every other registered
    tool; it is never read). Use `get_tentacle_config(tentacle)` instead if
    you already know the tentacle's class name.
    """
    return dict(_LIST_TENTACLES_UNAVAILABLE)


@with_error_handling
async def get_logs(ctx: Context) -> Any:
    """Tier C stub (ADR-0006): structured/filterable log listing has no JSON API in OctoBot.

    Never makes an HTTP call to OctoBot and never raises -- this is a
    static, instant response explaining the gap, not a failure (`ctx` is
    accepted only for interface consistency with every other registered
    tool; it is never read). Use `export_logs()` instead to retrieve
    OctoBot's full log history as a downloadable zip archive.
    """
    return dict(_GET_LOGS_UNAVAILABLE)


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(get_tentacle_config)
    mcp.tool()(update_tentacle_config)
    mcp.tool()(list_tentacles)
    mcp.tool()(get_logs)
    mcp.tool()(export_logs)
