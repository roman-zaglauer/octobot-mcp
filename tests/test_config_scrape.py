"""Unit tests for the generic `.config-element` Tier-B scrape helper
(task 21, docs/tasklist.md item 21, ADR-0012, `octobot_mcp.tools._config_scrape`).

Written first (TDD, per project standards): these drive the helper's own
design before any of the four read tools that will consume it exist.
"""

import pytest

from octobot_mcp.errors import ConfigElementScrapeContractBrokenError
from octobot_mcp.tools._config_scrape import (
    find_config_container,
    find_entities_with_config_elements,
    scrape_config_elements,
    strip_prefix,
)


def _html(body: str) -> str:
    return f"<!DOCTYPE html><html><body>{body}</body></html>"


# --- find_config_container ---------------------------------------------------


def test_find_config_container_returns_the_matching_element():
    soup_container = find_config_container(
        _html('<div id="panelNotifications"><span>hi</span></div>'),
        "panelNotifications",
        page_path="/accounts",
    )
    assert soup_container.get("id") == "panelNotifications"


def test_find_config_container_raises_when_missing():
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        find_config_container(_html("<div>nothing here</div>"), "panelNotifications", page_path="/accounts")


# --- scrape_config_elements: per-data-type value parsing ---------------------


def test_scrape_config_elements_parses_bool():
    container = find_config_container(
        _html(
            '<div id="c"><input class="config-element" config-key="k" '
            'config-type="global_config" config-value="True" data-type="bool"></div>'
        ),
        "c",
        page_path="/x",
    )
    elements = scrape_config_elements(container)
    assert elements == [{"config_key": "k", "data_type": "bool", "value": True}]


def test_scrape_config_elements_parses_bool_false():
    container = find_config_container(
        _html(
            '<div id="c"><input class="config-element" config-key="k" '
            'config-value="False" data-type="bool"></div>'
        ),
        "c",
        page_path="/x",
    )
    assert scrape_config_elements(container)[0]["value"] is False


def test_scrape_config_elements_raises_for_unparseable_bool():
    container = find_config_container(
        _html(
            '<div id="c"><input class="config-element" config-key="k" '
            'config-value="yes" data-type="bool"></div>'
        ),
        "c",
        page_path="/x",
    )
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        scrape_config_elements(container)


def test_scrape_config_elements_parses_number_int():
    container = find_config_container(
        _html(
            '<div id="c"><a class="config-element" config-key="k" '
            'config-value="5001" data-type="number"></a></div>'
        ),
        "c",
        page_path="/x",
    )
    value = scrape_config_elements(container)[0]["value"]
    assert value == 5001
    assert isinstance(value, int)


def test_scrape_config_elements_parses_number_float():
    container = find_config_container(
        _html(
            '<div id="c"><a class="config-element" config-key="k" '
            'config-value="1.5" data-type="number"></a></div>'
        ),
        "c",
        page_path="/x",
    )
    assert scrape_config_elements(container)[0]["value"] == 1.5


def test_scrape_config_elements_raises_for_unparseable_number():
    container = find_config_container(
        _html(
            '<div id="c"><a class="config-element" config-key="k" '
            'config-value="not-a-number" data-type="number"></a></div>'
        ),
        "c",
        page_path="/x",
    )
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        scrape_config_elements(container)


def test_scrape_config_elements_parses_text_verbatim():
    container = find_config_container(
        _html(
            '<div id="c"><a class="config-element" config-key="k" '
            'config-value="0000000000:FAKE-TOKEN-VALUE" data-type="text"></a></div>'
        ),
        "c",
        page_path="/x",
    )
    assert scrape_config_elements(container)[0]["value"] == "0000000000:FAKE-TOKEN-VALUE"


def test_scrape_config_elements_parses_list_python_repr():
    # Confirmed live (this milestone's own fixture capture): `data-type="list"`
    # config-values are rendered as a Python repr (single-quoted), not JSON.
    container = find_config_container(
        _html(
            "<div id=\"c\"><select class=\"config-element\" config-key=\"k\" "
            "config-value=\"[&#39;BTC/EUR&#39;]\" data-type=\"list\"></select></div>"
        ),
        "c",
        page_path="/x",
    )
    assert scrape_config_elements(container)[0]["value"] == ["BTC/EUR"]


def test_scrape_config_elements_parses_empty_list():
    container = find_config_container(
        _html(
            '<div id="c"><select class="config-element" config-key="k" '
            'config-value="[]" data-type="list"></select></div>'
        ),
        "c",
        page_path="/x",
    )
    assert scrape_config_elements(container)[0]["value"] == []


def test_scrape_config_elements_parses_multi_item_list():
    container = find_config_container(
        _html(
            "<div id=\"c\"><select class=\"config-element\" config-key=\"k\" "
            "config-value=\"[&#39;web&#39;, &#39;telegram&#39;]\" data-type=\"list\"></select></div>"
        ),
        "c",
        page_path="/x",
    )
    assert scrape_config_elements(container)[0]["value"] == ["web", "telegram"]


def test_scrape_config_elements_raises_for_unparseable_list():
    container = find_config_container(
        _html(
            '<div id="c"><select class="config-element" config-key="k" '
            'config-value="not a list" data-type="list"></select></div>'
        ),
        "c",
        page_path="/x",
    )
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        scrape_config_elements(container)


def test_scrape_config_elements_raises_when_list_value_is_not_actually_a_list():
    container = find_config_container(
        _html(
            '<div id="c"><select class="config-element" config-key="k" '
            'config-value="5" data-type="list"></select></div>'
        ),
        "c",
        page_path="/x",
    )
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        scrape_config_elements(container)


# --- scrape_config_elements: fail-loud structural assumptions ----------------


def test_scrape_config_elements_raises_when_config_key_missing():
    container = find_config_container(
        _html('<div id="c"><input class="config-element" data-type="bool" config-value="True"></div>'),
        "c",
        page_path="/x",
    )
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        scrape_config_elements(container)


def test_scrape_config_elements_raises_when_data_type_missing():
    container = find_config_container(
        _html('<div id="c"><input class="config-element" config-key="k" config-value="True"></div>'),
        "c",
        page_path="/x",
    )
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        scrape_config_elements(container)


def test_scrape_config_elements_raises_for_unrecognized_data_type():
    container = find_config_container(
        _html(
            '<div id="c"><input class="config-element" config-key="k" '
            'config-value="x" data-type="weird"></div>'
        ),
        "c",
        page_path="/x",
    )
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        scrape_config_elements(container)


def test_scrape_config_elements_returns_empty_list_for_an_intact_but_empty_container():
    # NFR-21: zero matched elements inside an intact container is NOT an error.
    container = find_config_container(_html('<div id="c"></div>'), "c", page_path="/x")
    assert scrape_config_elements(container) == []


# --- find_entities_with_config_elements: per-entity (name-attributed) grouping ----


def test_find_entities_with_config_elements_groups_by_name_attribute():
    container = find_config_container(
        _html(
            '<div id="c">'
            '<div class="card"><div class="card-body">Add a new thing (no name attr, ignored)</div></div>'
            '<div class="card-body" name="binance"><a class="config-element" config-key="exchanges_binance_enabled" '
            'config-value="True" data-type="bool"></a></div>'
            '<div class="card-body" name="kraken"><a class="config-element" config-key="exchanges_kraken_enabled" '
            'config-value="False" data-type="bool"></a></div>'
            "</div>"
        ),
        "c",
        page_path="/x",
    )
    entities = find_entities_with_config_elements(container)
    names = {name for name, _tag in entities}
    assert names == {"binance", "kraken"}


def test_find_entities_with_config_elements_returns_empty_for_zero_entities():
    container = find_config_container(_html('<div id="c"></div>'), "c", page_path="/x")
    assert find_entities_with_config_elements(container) == []


# --- strip_prefix -------------------------------------------------------------


def test_strip_prefix_removes_the_exact_prefix():
    assert strip_prefix("exchanges_binance_api-key", "exchanges_binance_", context="x") == "api-key"


def test_strip_prefix_raises_when_prefix_does_not_match():
    with pytest.raises(ConfigElementScrapeContractBrokenError):
        strip_prefix("services_telegram_token", "exchanges_binance_", context="x")
