"""
Centralized Logging System for TestGen
======================================
Sets up comprehensive logging to both console and a timestamped log file located in:
  <User Documents Folder>/Logs/testgen_YYYYMMDD_HHMMSS.log

Logs:
  - Processed files and subfolder paths
  - Lines covered vs total lines and exact uncovered line numbers
  - Test outcomes, execution times, compiler errors, assertion messages
  - API key rotations and rate limit exhaustion
  - Checkpoint saves and loads
"""
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Global logger instance
_logger: Optional[logging.Logger] = None
_log_file_path: Optional[Path] = None


def get_logs_directory() -> Path:
    """
    Returns the user's Documents/Logs directory across any Windows (or Unix) machine.
    Windows: C:\\Users\\<Username>\\Documents\\Logs
    Fallback: ~/Documents/Logs
    """
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        docs_dir = Path(user_profile) / "Documents"
    else:
        docs_dir = Path.home() / "Documents"

    logs_dir = docs_dir / "Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def setup_logger(
    session_name: str = "testgen",
    log_level: int = logging.INFO,
    custom_log_dir: Optional[Path] = None,
) -> logging.Logger:
    """
    Initializes and configures the centralized logger with dual handlers:
    1. StreamHandler (Console with clean format)
    2. FileHandler (UTF-8, timestamped log file in Documents/Logs/)
    """
    global _logger, _log_file_path

    if _logger is not None:
        return _logger

    logs_dir = custom_log_dir or get_logs_directory()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_session = session_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    log_file_name = f"{sanitized_session}_{timestamp}.log"
    _log_file_path = logs_dir / log_file_name

    logger = logging.getLogger("testgen")
    logger.setLevel(log_level)
    logger.handlers.clear()  # Prevent duplicate handlers on re-init
    logger.propagate = False

    # Detailed formatter for file
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Clean formatter for console
    console_formatter = logging.Formatter("%(message)s")

    # File Handler
    try:
        file_handler = logging.FileHandler(
            str(_log_file_path),
            mode="a",
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"[WARNING] Could not create file log handler at '{_log_file_path}': {e}", file=sys.stderr)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    _logger = logger

    logger.info("=" * 70)
    logger.info("  AI C# TEST GENERATOR - EXECUTION LOG SESSION")
    logger.info(f"  Log File: {_log_file_path}")
    logger.info(f"  Started:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 70)

    return logger


def get_logger() -> logging.Logger:
    """Returns the configured logger, or initializes a default one if not yet set up."""
    global _logger
    if _logger is None:
        return setup_logger()
    return _logger


def get_current_log_file() -> Optional[Path]:
    """Returns the active log file path."""
    return _log_file_path
