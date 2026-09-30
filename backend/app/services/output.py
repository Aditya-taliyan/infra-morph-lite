from pathlib import Path


class TerraformOutputService:
    def write(self, content: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")