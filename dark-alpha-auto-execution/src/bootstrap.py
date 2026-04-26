"""Bootstrap — loads .env before any module reads os.getenv.

Importing this module (side-effect only) populates the process env from
the local package `.env` and workspace-root `.env` files. Entry points import it first:

    import bootstrap  # noqa: F401  — must be first non-stdlib import
"""

from pathlib import Path

from dotenv import load_dotenv

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE_ROOT = _PACKAGE_ROOT.parent

# Preserve existing values: package .env wins, workspace .env fills missing keys.
load_dotenv(_PACKAGE_ROOT / ".env", override=False)
load_dotenv(_WORKSPACE_ROOT / ".env", override=False)
