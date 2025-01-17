"""Logging module for Auto-GPT."""
from __future__ import annotations

import enum
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from auto_gpt_plugin_template import AutoGPTPluginTemplate
from openai._base_client import log as openai_logger

if TYPE_CHECKING:
    from autogpt.config import Config
    from autogpt.speech import TTSConfig

from autogpt.core.configuration import SystemConfiguration, UserConfigurable
from autogpt.core.runner.client_lib.logging import BelowLevelFilter

from .formatters import AutoGptFormatter, StructuredLoggingFormatter
from .handlers import TTSHandler, TypingConsoleHandler

LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_FILE = "activity.log"
DEBUG_LOG_FILE = "debug.log"
ERROR_LOG_FILE = "error.log"

SIMPLE_LOG_FORMAT = "%(asctime)s %(levelname)s  %(title)s%(message)s"
DEBUG_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(filename)s:%(lineno)d" "  %(title)s%(message)s"
)

SPEECH_OUTPUT_LOGGER = "VOICE"
USER_FRIENDLY_OUTPUT_LOGGER = "USER_FRIENDLY_OUTPUT"

_chat_plugins: list[AutoGPTPluginTemplate] = []


class LogFormatName(str, enum.Enum):
    SIMPLE = "simple"
    DEBUG = "debug"
    STRUCTURED = "structured_google_cloud"


TEXT_LOG_FORMAT_MAP = {
    LogFormatName.DEBUG: DEBUG_LOG_FORMAT,
    LogFormatName.SIMPLE: SIMPLE_LOG_FORMAT,
}


class LoggingConfig(SystemConfiguration):
    level: int = UserConfigurable(
        default=logging.INFO,
        from_env=lambda: logging.getLevelName(os.getenv("LOG_LEVEL", "INFO")),
    )

    # Console output
    log_format: LogFormatName = UserConfigurable(
        default=LogFormatName.SIMPLE, from_env="LOG_FORMAT"
    )
    plain_console_output: bool = UserConfigurable(
        default=False,
        from_env=lambda: os.getenv("PLAIN_OUTPUT", "False") == "True",
    )

    # File output
    log_dir: Path = LOG_DIR
    log_file_format: Optional[LogFormatName] = UserConfigurable(
        default=LogFormatName.SIMPLE,
        from_env=lambda: os.getenv(
            "LOG_FILE_FORMAT", os.getenv("LOG_FORMAT", "simple")
        ),
    )


def configure_logging(
    debug: bool = False,
    level: Optional[int | str] = None,
    log_dir: Optional[Path] = None,
    log_format: Optional[LogFormatName | str] = None,
    log_file_format: Optional[LogFormatName | str] = None,
    plain_console_output: Optional[bool] = None,
    tts_config: Optional[TTSConfig] = None,
) -> None:
    """Configure the native logging module, based on the environment config and any
    specified overrides.

    Arguments override values specified in the environment.

    Should be usable as `configure_logging(**config.logging.dict())`, where
    `config.logging` is a `LoggingConfig` object.
    """
    if debug and level:
        raise ValueError("Only one of either 'debug' and 'level' arguments may be set")

    # Parse arguments
    if isinstance(level, str):
        if type(_level := logging.getLevelName(level.upper())) is int:
            level = _level
        else:
            raise ValueError(f"Unknown log level '{level}'")
    if isinstance(log_format, str):
        if log_format in LogFormatName._value2member_map_:
            log_format = LogFormatName(log_format)
        elif not isinstance(log_format, LogFormatName):
            raise ValueError(f"Unknown log format '{log_format}'")
    if isinstance(log_file_format, str):
        if log_file_format in LogFormatName._value2member_map_:
            log_file_format = LogFormatName(log_file_format)
        elif not isinstance(log_file_format, LogFormatName):
            raise ValueError(f"Unknown log format '{log_format}'")

    config = LoggingConfig.from_env()

    # Aggregate arguments + env config
    level = logging.DEBUG if debug else level or config.level
    log_dir = log_dir or config.log_dir
    log_format = log_format or (LogFormatName.DEBUG if debug else config.log_format)
    log_file_format = log_file_format or log_format or config.log_file_format
    plain_console_output = (
        plain_console_output
        if plain_console_output is not None
        else config.plain_console_output
    )

    # Structured logging is used for cloud environments,
    # where logging to a file makes no sense.
    if log_format == LogFormatName.STRUCTURED:
        plain_console_output = True
        log_file_format = None

    # create log directory if it doesn't exist
    if not log_dir.exists():
        log_dir.mkdir()

    log_handlers: list[logging.Handler] = []

    if log_format in (LogFormatName.DEBUG, LogFormatName.SIMPLE):
        console_format_template = TEXT_LOG_FORMAT_MAP[log_format]
        console_formatter = AutoGptFormatter(console_format_template)
    else:
        console_formatter = StructuredLoggingFormatter()
        console_format_template = SIMPLE_LOG_FORMAT

    # Console output handlers
    stdout = logging.StreamHandler(stream=sys.stdout)
    stdout.setLevel(level)
    stdout.addFilter(BelowLevelFilter(logging.WARNING))
    stdout.setFormatter(console_formatter)
    stderr = logging.StreamHandler()
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(console_formatter)
    log_handlers += [stdout, stderr]

    # Console output handler which simulates typing
    typing_console_handler = TypingConsoleHandler(stream=sys.stdout)
    typing_console_handler.setLevel(logging.INFO)
    typing_console_handler.setFormatter(console_formatter)

    # User friendly output logger (text + speech)
    user_friendly_output_logger = logging.getLogger(USER_FRIENDLY_OUTPUT_LOGGER)
    user_friendly_output_logger.setLevel(logging.INFO)
    user_friendly_output_logger.addHandler(
        typing_console_handler if not plain_console_output else stdout
    )
    if tts_config:
        user_friendly_output_logger.addHandler(TTSHandler(tts_config))
    user_friendly_output_logger.addHandler(stderr)
    user_friendly_output_logger.propagate = False

    # File output handlers
    if log_file_format is not None:
        if level < logging.ERROR:
            file_output_format_template = TEXT_LOG_FORMAT_MAP[log_file_format]
            file_output_formatter = AutoGptFormatter(
                file_output_format_template, no_color=True
            )

            # INFO log file handler
            activity_log_handler = logging.FileHandler(log_dir / LOG_FILE, "a", "utf-8")
            activity_log_handler.setLevel(level)
            activity_log_handler.setFormatter(file_output_formatter)
            log_handlers += [activity_log_handler]
            user_friendly_output_logger.addHandler(activity_log_handler)

        # ERROR log file handler
        error_log_handler = logging.FileHandler(log_dir / ERROR_LOG_FILE, "a", "utf-8")
        error_log_handler.setLevel(logging.ERROR)
        error_log_handler.setFormatter(
            AutoGptFormatter(DEBUG_LOG_FORMAT, no_color=True)
        )
        log_handlers += [error_log_handler]
        user_friendly_output_logger.addHandler(error_log_handler)

    # Configure the root logger
    logging.basicConfig(
        format=console_format_template,
        level=level,
        handlers=log_handlers,
    )

    # Speech output
    speech_output_logger = logging.getLogger(SPEECH_OUTPUT_LOGGER)
    speech_output_logger.setLevel(logging.INFO)
    if tts_config:
        speech_output_logger.addHandler(TTSHandler(tts_config))
    speech_output_logger.propagate = False

    # JSON logger with better formatting
    json_logger = logging.getLogger("JSON_LOGGER")
    json_logger.setLevel(logging.DEBUG)
    json_logger.propagate = False

    # Disable debug logging from OpenAI library
    openai_logger.setLevel(logging.WARNING)


def configure_chat_plugins(config: Config) -> None:
    """Configure chat plugins for use by the logging module"""

    logger = logging.getLogger(__name__)

    # Add chat plugins capable of report to logger
    if config.chat_messages_enabled:
        if _chat_plugins:
            _chat_plugins.clear()

        for plugin in config.plugins:
            if hasattr(plugin, "can_handle_report") and plugin.can_handle_report():
                logger.debug(f"Loaded plugin into logger: {plugin.__class__.__name__}")
                _chat_plugins.append(plugin)
