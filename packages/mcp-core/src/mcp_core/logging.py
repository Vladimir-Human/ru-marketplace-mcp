from __future__ import annotations

import json
import logging
import sys
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def make_stderr_logger(name: str = "mcp_connector") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)s}',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(event: str, *, level: int = logging.INFO, logger_name: str = "mcp_connector", **fields: Any) -> None:
    logger = make_stderr_logger(logger_name)
    payload = json.dumps({"event": event, **fields}, default=str, ensure_ascii=False)
    logger.log(level, payload)
