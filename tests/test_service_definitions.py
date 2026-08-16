"""Consistency checks across the three places a service has to be declared.

A service only works properly in Home Assistant if it appears in all of:

* ``services.py`` — the handler and its voluptuous schema
* ``services.yaml`` — the UI form (selectors, required flags)
* ``strings.json`` / ``translations/en.json`` — the labels

Miss one and the failure is quiet rather than loud: the service still
registers, but shows up unlabelled, or with a field the schema drops on the
floor. hassfest doesn't cross-check these against each other, so this does.

Everything here is parsed from the files rather than imported — the component
imports Home Assistant, which isn't installed in this test environment (same
reasoning as test_timeutil.py).
"""
import json
import re
from pathlib import Path

import pytest
import yaml

_COMPONENT = (
    Path(__file__).resolve().parent.parent / "custom_components" / "babytracker"
)

_SERVICES_PY = (_COMPONENT / "services.py").read_text()
_CONST_PY = (_COMPONENT / "const.py").read_text()
_SERVICES_YAML = yaml.safe_load((_COMPONENT / "services.yaml").read_text())
_STRINGS = json.loads((_COMPONENT / "strings.json").read_text())
_EN = json.loads((_COMPONENT / "translations" / "en.json").read_text())


def _service_names() -> dict[str, str]:
    """SERVICE_* constant -> service name, from both modules that define them."""
    pattern = r'^(SERVICE_[A-Z_]+) = "([a-z_]+)"'
    names = dict(re.findall(pattern, _SERVICES_PY, re.M))
    names.update(dict(re.findall(pattern, _CONST_PY, re.M)))
    return names


def _registered_service_names() -> set[str]:
    """The names actually wired into the _SERVICES registration tuple."""
    names = _service_names()
    consts = re.findall(r"\(\s*(SERVICE_[A-Z_]+),", _SERVICES_PY)
    return {names[c] for c in consts if c in names}


REGISTERED = _registered_service_names()


def test_every_registered_service_is_declared_everywhere():
    assert REGISTERED, "no services found — the parser is out of date"
    assert REGISTERED == set(_SERVICES_YAML), (
        f"services.yaml out of sync: "
        f"missing={REGISTERED - set(_SERVICES_YAML)} "
        f"extra={set(_SERVICES_YAML) - REGISTERED}"
    )
    assert REGISTERED == set(_STRINGS["services"]), (
        f"strings.json out of sync: "
        f"missing={REGISTERED - set(_STRINGS['services'])} "
        f"extra={set(_STRINGS['services']) - REGISTERED}"
    )


def test_strings_and_en_translation_agree():
    # en.json is the shipped copy of strings.json; drift between them means
    # the UI shows raw keys for whichever side is behind.
    assert _STRINGS == _EN, "strings.json and translations/en.json have diverged"


@pytest.mark.parametrize("service", sorted(REGISTERED))
def test_yaml_and_strings_describe_the_same_fields(service):
    yaml_fields = set((_SERVICES_YAML[service] or {}).get("fields") or {})
    string_fields = set(_STRINGS["services"][service].get("fields") or {})
    assert yaml_fields == string_fields, (
        f"{service}: yaml-only={yaml_fields - string_fields} "
        f"strings-only={string_fields - yaml_fields}"
    )


@pytest.mark.parametrize("service", sorted(REGISTERED))
def test_every_service_has_a_name_and_description(service):
    entry = _STRINGS["services"][service]
    assert entry.get("name"), f"{service} has no name"
    assert entry.get("description"), f"{service} has no description"


def test_log_milk_waste_schema_matches_its_form():
    """The one service built without _base_schema, so its fields are spelled
    out twice and can drift. It skips _base_schema deliberately: that adds a
    `tags` field, and milk_waste carries no tags on the backend.
    """
    block = re.search(
        r"SCHEMA_LOG_MILK_WASTE = vol\.Schema\(\{(.*?)^\}\)",
        _SERVICES_PY,
        re.S | re.M,
    )
    assert block, "SCHEMA_LOG_MILK_WASTE not found or no longer a plain vol.Schema"
    schema_fields = set(
        re.findall(r'vol\.(?:Required|Optional)\("(\w+)"', block.group(1))
    )
    assert schema_fields == set(_SERVICES_YAML["log_milk_waste"]["fields"])
    assert "tags" not in schema_fields, (
        "milk_waste has no tag support on the backend — offering the field "
        "would silently discard whatever the user typed"
    )


def test_milk_waste_webhook_event_is_mapped():
    """The backend fires milk_waste.created; without a mapping it is received,
    acknowledged and silently dropped."""
    webhook_py = (_COMPONENT / "webhook.py").read_text()
    assert '"milk_waste.created"' in webhook_py
    assert "EVENT_NEW_MILK_WASTE" in _CONST_PY
