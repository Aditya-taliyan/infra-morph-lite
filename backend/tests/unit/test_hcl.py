import pytest

from app.generators.hcl import body, quote, value
from app.providers.base import Block, Expression


def test_plain_string_is_quoted():
    assert quote("web-sg") == '"web-sg"'


def test_double_quote_is_escaped():
    assert quote('say "hi"') == r'"say \"hi\""'


def test_backslash_is_escaped():
    assert quote("C:\\temp\\") == r'"C:\\temp\\"'


def test_interpolation_is_made_literal():
    assert quote('${file("/etc/passwd")}') == r'"$${file(\"/etc/passwd\")}"'


def test_template_directive_is_made_literal():
    assert quote("%{ if true }x%{ endif }") == '"%%{ if true }x%%{ endif }"'


def test_newlines_and_tabs_are_escaped():
    assert quote("a\nb\r\tc") == r'"a\nb\r\tc"'


def test_breakout_attempt_stays_one_string():
    # Trying to close the string and start a new attribute on the next line.
    text = 'x"\n  ami = "evil'
    assert quote(text) == r'"x\"\n  ami = \"evil"'
    assert "\n" not in quote(text)


def test_lone_dollar_and_percent_are_unchanged():
    assert quote("50% off $5") == '"50% off $5"'


def test_values():
    assert value(True, 0) == "true"
    assert value(False, 0) == "false"
    assert value(-1, 0) == "-1"
    assert value(Expression("aws_vpc.x.id"), 0) == "aws_vpc.x.id"
    assert value([Expression("a.b.id"), "c"], 0) == '[a.b.id, "c"]'
    assert value({}, 0) == "{}"


def test_unsupported_type_fails_loudly():
    with pytest.raises(TypeError):
        value(1.5, 0)


def test_map_keys_are_quoted_unless_identifiers():
    assert value({"Name": "x", "my key": "y", 'k"${x}': "z"}, 0) == (
        "{\n"
        '  Name       = "x"\n'
        '  "my key"   = "y"\n'
        '  "k\\"$${x}" = "z"\n'
        "}"
    )


def test_body_aligns_groups_and_separates_maps_and_blocks():
    attributes = {
        "vpc_id": Expression("aws_vpc.v.id"),
        "cidr_block": "10.0.0.0/16",
        "route": Block("route", {"a": 1}),
        "tags": {"Name": "x"},
    }
    assert body(attributes, 1) == (
        "  vpc_id     = aws_vpc.v.id\n"
        '  cidr_block = "10.0.0.0/16"\n'
        "\n"
        "  route {\n"
        "    a = 1\n"
        "  }\n"
        "\n"
        "  tags = {\n"
        '    Name = "x"\n'
        "  }\n"
    )
