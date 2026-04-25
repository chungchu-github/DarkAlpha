from __future__ import annotations

import logging

from .config import load_settings
from .service import SignalService


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = load_settings()
    service = SignalService(settings)
    service.run_forever()


if __name__ == "__main__":
    main()
