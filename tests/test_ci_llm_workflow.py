"""Execute the Linux CI shell with offline pytest substitutes."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.skipif(sys.platform == "win32", reason="Ubuntu CI uses POSIX executables")
@pytest.mark.parametrize("failed_suite", ["", "hermes", "quality"])
def test_python_llm_suites_start_together_and_preserve_failures(tmp_path, failed_suite):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    step = next(s for s in workflow["jobs"]["e2e-llm"]["steps"] if s.get("name") == "E2E — Hermes LLM flow")
    executable = tmp_path / "pytest"
    executable.write_text("""#!/usr/bin/env python3
import os, sys, time
from pathlib import Path
suite = 'hermes' if 'test_hermes_llm' in sys.argv[1] else 'quality'
root = Path(os.environ['MARKER_DIR'])
(root / suite).touch()
deadline = time.monotonic() + 3
while not all((root / name).exists() for name in ('hermes', 'quality')):
    if time.monotonic() >= deadline:
        sys.exit(88)
    time.sleep(0.01)
sys.exit(int(os.environ['FAIL_SUITE'] == suite))
""")
    executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://example.invalid",
            "OPENAI_MODEL": "test-model",
            "MARKER_DIR": str(tmp_path),
            "FAIL_SUITE": failed_suite,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (1 if failed_suite else 0), result.stderr
    assert (tmp_path / "hermes").exists() and (tmp_path / "quality").exists()
