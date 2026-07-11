import logging
import os
import sys


def _build_logger() -> logging.Logger:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    log = logging.getLogger("petshop")
    if log.handlers:
        return log
    log.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    log.addHandler(handler)
    log.propagate = False
    return log


logger = _build_logger()
