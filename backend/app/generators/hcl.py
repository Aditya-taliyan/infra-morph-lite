"""Render context values as HCL text.

Pure functions, nothing provider-specific. Output is already in
`terraform fmt` style (2-space indent, `=` aligned within a group of
single-line attributes) so golden files stay readable.

Only Expression values are written raw. Every str is quoted and escaped, so
text from the spec (tags, descriptions) cannot end a string early or inject
an interpolation.
"""

import re
from typing import Any

from app.providers.base import Block, Expression

INDENT = "  "
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote(text: str) -> str:
    """A safe HCL string literal for any text."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        # "${" and "%{" start interpolation / template directives; doubling
        # the first character makes them literal.
        .replace("${", "$${")
        .replace("%{", "%%{")
    )
    return f'"{escaped}"'


def _key(name: str) -> str:
    return name if _IDENTIFIER.match(name) else quote(name)


def value(item: Any, depth: int) -> str:
    """One value; `depth` is the indent level of the line it starts on."""
    if isinstance(item, Expression):
        return item.text
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, int):
        return str(item)
    if isinstance(item, str):
        return quote(item)
    if isinstance(item, list):
        return "[" + ", ".join(value(element, depth) for element in item) + "]"
    if isinstance(item, dict):
        if not item:
            return "{}"
        inner = body({_key(k): v for k, v in item.items()}, depth + 1)
        return "{\n" + inner + INDENT * depth + "}"
    raise TypeError(f"Cannot render {type(item).__name__} as HCL")


def body(attributes: dict[str, Any], depth: int) -> str:
    """Attribute lines and nested blocks at `depth`, each ending in a newline.

    Single-line attributes form alignment groups, like terraform fmt. A map
    or block gets a blank line before it so it starts a new group.
    """
    out: list[str] = []
    group: list[tuple[str, str]] = []
    pad = INDENT * depth

    def flush() -> None:
        width = max((len(k) for k, _ in group), default=0)
        out.extend(f"{pad}{k.ljust(width)} = {v}\n" for k, v in group)
        group.clear()

    for key, item in attributes.items():
        if isinstance(item, Block):
            flush()
            if out:
                out.append("\n")
            out.append(f"{pad}{item.type} {{\n{body(item.attributes, depth + 1)}{pad}}}\n")
        elif isinstance(item, dict) and item:
            flush()
            if out:
                out.append("\n")
            out.append(f"{pad}{key} = {value(item, depth)}\n")
        else:
            group.append((key, value(item, depth)))
    flush()
    return "".join(out)
