from app.core.version import __version__
from app.infrastructure.terraform_runner import terraform_available


def health_status() -> dict:
    return {"version": __version__, "terraform_available": terraform_available()}
