"""Tests for flight_blender.conformance.conformance_state_helper."""

import pytest

from flight_blender.conformance.conformance_state_helper import ConformanceChecksList


class TestStatusCodeBase:
    def test_list_returns_dicts(self):
        result = ConformanceChecksList.list()
        assert isinstance(result, list)
        assert len(result) > 0
        first = result[0]
        assert "key" in first
        assert "name" in first
        assert "label" in first

    def test_keys_contains_all_checks(self):
        keys = ConformanceChecksList.keys()
        # C2=2 through C11=13
        assert 2 in keys
        assert 13 in keys

    def test_names_maps_attr_to_value(self):
        names = ConformanceChecksList.names()
        assert "C2" in names
        assert names["C2"] == 2
        assert "C11" in names
        assert names["C11"] == 13

    def test_labels_returns_all_label_strings(self):
        labels = list(ConformanceChecksList.labels())
        assert "Flight Auth not granted" in labels
        assert "No Flight Authorization" in labels

    def test_items_are_key_label_pairs(self):
        items = dict(ConformanceChecksList.items())
        assert items[2] == "Flight Auth not granted"
        assert items[13] == "No Flight Authorization"

    def test_label_returns_correct_string(self):
        assert ConformanceChecksList.label(2) == "Flight Auth not granted"
        assert ConformanceChecksList.label(3) == "Telemetry Auth mismatch"
        assert ConformanceChecksList.label(9) == "Geofence breached"

    def test_label_returns_key_for_unknown(self):
        # The base label() returns the key itself when not found
        assert ConformanceChecksList.label(999) == 999

    def test_text_returns_label_for_known_key(self):
        assert ConformanceChecksList.text(2) == "Flight Auth not granted"

    def test_text_returns_none_for_unknown_key(self):
        assert ConformanceChecksList.text(999) is None

    def test_dict_returns_full_structure(self):
        d = ConformanceChecksList.dict()
        assert "C2" in d
        entry = d["C2"]
        assert entry["key"] == 2
        assert entry["name"] == "C2"
        assert entry["label"] == "Flight Auth not granted"

    def test_state_code_returns_name(self):
        assert ConformanceChecksList.state_code(2) == "C2"
        assert ConformanceChecksList.state_code(13) == "C11"

    def test_state_code_raises_for_unknown(self):
        with pytest.raises(ValueError, match="Key not found"):
            ConformanceChecksList.state_code(999)

    def test_value_by_lowercase_label(self):
        result = ConformanceChecksList.value("flight auth not granted")
        assert result == 2

    def test_value_by_integer(self):
        # When label is int, compares directly via lower() with the stored label
        # The options dict maps int keys to string values.
        # value() iterates and compares label.lower() == supplied_label.lower()
        result = ConformanceChecksList.value("no flight authorization")
        assert result == 13

    def test_value_raises_for_unknown_label(self):
        with pytest.raises(ValueError, match="Label not found"):
            ConformanceChecksList.value("nonexistent label xyz")

    def test_all_checks_have_options(self):
        for key in ConformanceChecksList.keys():
            label = ConformanceChecksList.label(key)
            assert isinstance(label, str), f"Key {key} missing label"

    def test_c7a_7b_map_correctly(self):
        names = ConformanceChecksList.names()
        assert names.get("C7a") == 7
        assert names.get("C7b") == 8
