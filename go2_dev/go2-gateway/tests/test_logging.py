from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Settings
from app.main import SafeRotatingFileHandler, configure_logging


def test_configure_logging_reuses_gateway_file_handler():
    logger = logging.getLogger("go2_gateway")
    log_path = Path("logs/go2-gateway.log").resolve()

    configure_logging(Settings(mode="mock"))
    configure_logging(Settings(mode="mock"))

    handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == log_path
    ]
    assert len(handlers) == 1


def test_safe_rotating_handler_ignores_windows_rollover_lock(tmp_path):
    log_path = tmp_path / "go2-gateway.log"
    log_path.write_text("existing log", encoding="utf-8")
    handler = SafeRotatingFileHandler(log_path, maxBytes=1, backupCount=1, encoding="utf-8")
    handler.rotate = lambda source, dest: (_ for _ in ()).throw(PermissionError("locked"))
    try:
        handler.doRollover()
        assert handler.stream is not None
        handler.emit(logging.makeLogRecord({"levelno": logging.INFO, "levelname": "INFO", "msg": "after lock"}))
    finally:
        handler.close()
