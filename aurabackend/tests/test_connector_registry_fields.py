"""BUG-166: a connector spec must list each field key exactly once.

postgresql and mysql built their field list as `_DB_FIELDS + [port-with-default]`,
and `_DB_FIELDS` already contained a `port`, so GET /connectors/registry served
two `port` fields: the shared one (no default) and the connector's own (default
5432 / 3306). Any generic consumer of the registry -- it is documented as the
single source of truth the UI renders from -- got a duplicate form key and a
blank required Port next to a defaulted one.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.registry import (  # noqa: E402
    ConnectorField,
    ConnectorSpec,
    available_connectors,
    get_connector,
    register_connector,
    unregister_connector,
)


def _keys(spec):
    return [f.key for f in spec.fields]


@pytest.mark.parametrize("spec", available_connectors(include_unavailable=True), ids=lambda s: s.id)
def test_every_registered_connector_lists_each_field_once(spec):
    keys = _keys(spec)
    assert len(keys) == len(set(keys)), f"{spec.id} repeats a field key: {keys}"


@pytest.mark.parametrize("connector_id, default_port", [("postgresql", 5432), ("mysql", 3306)])
def test_relational_connectors_have_one_defaulted_required_port_in_place(connector_id, default_port):
    spec = get_connector(connector_id)
    ports = [f for f in spec.fields if f.key == "port"]
    assert len(ports) == 1
    assert ports[0].default == default_port
    assert ports[0].required is True
    # Overridden where the shared field sat (right after host), not appended.
    assert _keys(spec)[:3] == ["host", "port", "database"]


def test_the_wire_form_has_no_duplicate_keys():
    """What GET /connectors/registry actually serves, not just the dataclass."""
    for spec in available_connectors(include_unavailable=True):
        wire_keys = [f["key"] for f in spec.to_dict()["fields"]]
        assert len(wire_keys) == len(set(wire_keys)), spec.id
        assert len(spec.to_dict()["config_required"]) == len(set(spec.to_dict()["config_required"])), spec.id


def test_registering_a_spec_with_a_repeated_field_key_is_rejected():
    """Guard so the class of bug cannot come back through any registration path."""
    bad = ConnectorSpec(
        id="dup_field_probe", name="Dup", description="d", kind="relational",
        fields=[ConnectorField("host", "Host", "string"), ConnectorField("host", "Host again", "string")],
    )
    try:
        with pytest.raises(ValueError, match="host"):
            register_connector(bad)
        assert get_connector("dup_field_probe") is None, "a rejected spec must not be registered"
    finally:
        unregister_connector("dup_field_probe")
