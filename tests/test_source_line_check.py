from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_source_line_check_rejects_temporary_file_over_limit(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.py"
    oversized.write_text("pass\n" * 1001, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/check_source_lines.py", str(oversized)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "1001 lines exceeds 1000" in result.stdout
