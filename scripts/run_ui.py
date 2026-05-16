"""Launch the Streamlit chat UI.

Usage:
    uv run python scripts/run_ui.py
    # or directly:
    streamlit run src/ui/app.py
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

if __name__ == "__main__":
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(REPO_ROOT / "src" / "ui" / "app.py")],
        cwd=str(REPO_ROOT),
    )
