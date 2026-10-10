"""Shapes are what the fake is checked against, so the shape arithmetic is tested on its own."""

from __future__ import annotations

from scripts.record_principle_wire import covers, joined, shape_of, union


def test_firestore_values_reduce_to_their_types() -> None:
    document = {"name": {"stringValue": "x"}, "count": {"integerValue": "3"},
                "tags": {"arrayValue": {"values": [{"mapValue": {"fields": {
                    "ref": {"referenceValue": "p/x"}}}}]}}}
    assert shape_of(document) == {"name": "<string>", "count": "<integer>",
                                  "tags": [{"ref": "<reference>"}]}


def test_a_field_one_document_lacks_keeps_its_shape_from_the_other() -> None:
    joined_shape = joined([{"a": "<string>"}, {"a": "<string>", "b": {"c": "<string>"}}])
    assert joined_shape == [{"a": "<string>", "b": {"c": "<string>"}}]
    assert union({"b": {"c": "<string>"}}, None) == {"b": {"c": "<string>"}}


def test_differing_types_are_both_kept() -> None:
    assert union("<string>", "<null>") == "<null|string>"


def test_the_fake_may_omit_a_field_but_never_invent_one() -> None:
    recorded = {"a": "<string>", "b": [{"c": "<integer>"}]}
    assert covers(recorded, {"a": "<string>"}) == []
    assert covers(recorded, {"z": "<string>"}) == [".z: Principle never sent this field"]
    assert covers(recorded, {"b": [{"c": "<string>"}]}) == [
        ".b[].c: the fake sends <string>, Principle <integer>"]
