"""JSONC parser tests — comments, trailing commas, and the loud failures.

Contract: ``//`` and ``/* */`` comments are allowed; a comma that
directly closes a ``]``/``}`` container is tolerated; everything else
malformed still fails loudly. Commas inside string literals are never
touched.
"""

from __future__ import annotations

import json

import pytest
from devin_switch.jsonc import (
    load_jsonc,
    strip_jsonc,
    try_load_json_bytes,
)

# -- comments (unchanged contract) -------------------------------------------


def test_comments_still_stripped():
    text = '{// line\n"a": 1, /* block */ "b": "http://x"}'
    assert json.loads(strip_jsonc(text)) == {"a": 1, "b": "http://x"}


def test_comment_markers_inside_strings_survive():
    text = '{"url": "a//b", "glob": "a/*b*/c"}'
    assert json.loads(strip_jsonc(text)) == {
        "url": "a//b",
        "glob": "a/*b*/c",
    }


# -- trailing commas -----------------------------------------------------------


def test_trailing_comma_in_object():
    assert json.loads(strip_jsonc('{"a": 1, "b": 2,}')) == {
        "a": 1,
        "b": 2,
    }


def test_trailing_comma_in_array():
    assert json.loads(strip_jsonc("[1, 2, 3,]")) == [1, 2, 3]


def test_trailing_comma_whitespace_and_comment_before_close():
    text = """{
        "a": 1 ,   // trailing comma, then a comment
        "b": [
            2,
            3 ,
        ],
    }"""
    assert json.loads(strip_jsonc(text)) == {"a": 1, "b": [2, 3]}


def test_nested_trailing_commas():
    text = '{"a": {"b": [1,],},}'
    assert json.loads(strip_jsonc(text)) == {"a": {"b": [1]}}


def test_commas_inside_strings_never_stripped():
    text = '{"a": "x,}", "b": "y,]", "c": 1,}'
    obj = json.loads(strip_jsonc(text))
    assert obj == {"a": "x,}", "b": "y,]", "c": 1}


def test_comma_before_quote_not_close_is_kept():
    # a comma followed by another element is a normal separator
    assert json.loads(strip_jsonc('{"a": 1,"b": 2}')) == {"a": 1, "b": 2}


def test_escaped_quote_then_trailing_comma():
    # the comma after an escaped-quote string still closes the object
    text = '{"a": "he said \\"hi\\"",}'
    assert json.loads(strip_jsonc(text)) == {"a": 'he said "hi"'}


def test_empty_containers_and_singles():
    assert json.loads(strip_jsonc("{}")) == {}
    assert json.loads(strip_jsonc("[]")) == []
    assert json.loads(strip_jsonc('{"a": 1}')) == {"a": 1}


# -- malformed input still fails loudly ----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "{,}",            # leading comma, not trailing
        "[,]",            # leading comma
        '{"a": 1,,}',     # double comma
        "[1,,2]",         # hole in the middle
        '{"a" 1}',        # missing colon
        '{"a": 1',        # unclosed object
        '{"a": "unclosed}',  # unclosed string
        "{'a': 1}",       # single quotes are not JSON
        '{"a": ,}',       # missing value
    ],
)
def test_malformed_jsonc_fails_loudly(text):
    with pytest.raises(json.JSONDecodeError):
        json.loads(strip_jsonc(text))
    assert try_load_json_bytes(text.encode("utf-8")) is None


def test_load_jsonc_round_trip(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(
        '{// c\n"mcpServers": {"a": {"command": "python",},},\n}',
        encoding="utf-8",
    )
    assert load_jsonc(p) == {
        "mcpServers": {"a": {"command": "python"}}
    }
