from __future__ import annotations

import sys
import types


if "dotenv" not in sys.modules:
    dotenv_module = types.ModuleType("dotenv")

    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False

    dotenv_module.load_dotenv = load_dotenv
    sys.modules["dotenv"] = dotenv_module
