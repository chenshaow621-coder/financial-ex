from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
APP_FILE = SRC_DIR / "business_taxonomy_app.py"


def main() -> None:
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    os.chdir(PROJECT_ROOT)

    try:
        from streamlit.web import cli as stcli
    except ImportError:  # pragma: no cover
        from streamlit import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        str(APP_FILE),
        "--server.runOnSave=true",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=developer",
        *sys.argv[1:],
    ]
    raise SystemExit(stcli.main())


if __name__ == "__main__":
    main()
