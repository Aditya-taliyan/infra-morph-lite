import copy
import json
from pathlib import Path

from app.domain.spec import InfrastructureSpecification

FIXTURES = Path(__file__).parent / "fixtures"


def load_spec(name: str, mutate=None) -> InfrastructureSpecification:
    """Parse fixtures/specs/<name>.json, optionally after changing a copy of it."""
    data = json.loads((FIXTURES / "specs" / f"{name}.json").read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    if mutate:
        mutate(data)
    return InfrastructureSpecification.model_validate(data)


def resource(data: dict, name: str) -> dict:
    return next(r for r in data["resources"] if r["name"] == name)
