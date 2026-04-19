from __future__ import annotations

from pathlib import Path


def set_env_values(env_file_path: Path, updates: dict[str, str]) -> None:
    env_file_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = []
    if env_file_path.exists():
        existing_lines = env_file_path.read_text(encoding="utf-8").splitlines()

    pending = dict(updates)
    output_lines: list[str] = []
    for line in existing_lines:
        replaced = False
        for key, value in list(pending.items()):
            prefix = f"{key}="
            if line.startswith(prefix):
                output_lines.append(f"{key}={value}")
                pending.pop(key, None)
                replaced = True
                break
        if not replaced:
            output_lines.append(line)

    if pending:
        if output_lines and output_lines[-1] != "":
            output_lines.append("")
        for key, value in pending.items():
            output_lines.append(f"{key}={value}")

    env_file_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
