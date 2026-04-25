"""Bootstrap — loads .env before any module reads os.getenv.

Importing this module (side-effect only) populates the process env from
the project-root `.env` file. Entry points import it first:

    import bootstrap  # noqa: F401  — must be first non-stdlib import
"""

from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
