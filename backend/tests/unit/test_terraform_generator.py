from pathlib import Path

import pytest
from jinja2 import UndefinedError

from app.generators.terraform_generator import FILES, TerraformGenerator
from app.providers.aws import AWSProvider
from app.providers.base import Local, ProviderConfig, RenderContext, TerraformResource
from tests.helpers import load_spec


def render(spec) -> dict[str, str]:
    return TerraformGenerator("aws").render(AWSProvider().build_context(spec))


def test_render_produces_the_four_files_in_order():
    assert tuple(render(load_spec("demo1"))) == (
        "providers.tf",
        "main.tf",
        "variables.tf",
        "outputs.tf",
    )


def test_generating_demo5_twice_is_byte_identical():
    first = render(load_spec("demo5"))
    second = render(load_spec("demo5"))
    assert {k: v.encode() for k, v in first.items()} == {k: v.encode() for k, v in second.items()}


def test_providers_tf_uses_mock_credentials_and_skips():
    providers = render(load_spec("demo5"))["providers.tf"]
    for line in (
        'region     = "us-east-1"',
        'access_key = "mock"',
        'secret_key = "mock"',
        "skip_credentials_validation = true",
        "skip_requesting_account_id  = true",
        "skip_metadata_api_check     = true",
        'source  = "hashicorp/aws"',
        'version = "~> 5.0"',
    ):
        assert line in providers
    assert "never runs apply" in providers


def test_no_data_sources_are_generated():
    for content in render(load_spec("demo5")).values():
        assert 'data "' not in content


def test_spec_without_instances_has_no_locals_and_empty_variables_tf():
    files = render(load_spec("demo1", lambda d: d.update(resources=d["resources"][:1])))
    assert "no input variables" in files["variables.tf"]
    assert "locals" not in files["main.tf"]


def test_user_text_is_escaped_in_output():
    files = render(load_spec("demo1", lambda d: d["tags"].update({"Note": 'a"b ${x}'})))
    assert r'Note        = "a\"b $${x}"' in files["main.tf"]


def test_missing_template_value_fails_loudly(tmp_path, monkeypatch):
    # StrictUndefined: a template asking for something the context lacks raises.
    (tmp_path / "broken").mkdir()
    for name in FILES:
        (tmp_path / "broken" / f"{name}.j2").write_text("{{ provider.missing }}\n")
    monkeypatch.setattr("app.generators.terraform_generator.TEMPLATES_DIR", tmp_path)
    context = RenderContext(provider=ProviderConfig("aws", "hashicorp/aws", "~> 5.0", {}))
    with pytest.raises(UndefinedError):
        TerraformGenerator("broken").render(context)


def test_locals_and_resources_layout():
    context = RenderContext(
        provider=ProviderConfig("aws", "hashicorp/aws", "~> 5.0", {"region": "us-east-1"}),
        locals=[Local("x", 1, "a comment")],
        resources=[TerraformResource("aws_vpc", "v", {"cidr_block": "10.0.0.0/16"})],
    )
    main = TerraformGenerator("aws").render(context)["main.tf"]
    assert "locals {\n  # a comment\n  x = 1\n}\n" in main
    assert 'resource "aws_vpc" "v" {\n  cidr_block = "10.0.0.0/16"\n}\n' in main


def test_write_creates_files_with_lf_newlines(tmp_path: Path):
    files = render(load_spec("demo2"))
    written = TerraformGenerator.write(files, tmp_path / "run")
    assert [p.name for p in written] == list(FILES)
    for path in written:
        assert path.read_bytes() == files[path.name].encode("utf-8")
        assert b"\r\n" not in path.read_bytes()
