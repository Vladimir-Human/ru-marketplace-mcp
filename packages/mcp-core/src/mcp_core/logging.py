# START_MODULE_CONTRACT
# PURPOSE: Emit structured diagnostics without corrupting stdio JSON-RPC stdout.
# SCOPE: Logger configuration and JSON event serialization; callers redact fields.
# DEPENDS: stdlib json,logging,sys,functools,typing.
# LINKS: M-LOGGING; M-CORE; M-RUNTIME; M-REDACT.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# make_stderr_logger: Reuse a logger or configure its stderr JSON formatter.
# log_event: Serialize an event and caller fields at the requested log level.
# END_MODULE_MAP
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
