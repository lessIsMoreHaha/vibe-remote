#!/usr/bin/env python3
import os
import signal
import sys
import logging
import asyncio
import threading
from config.paths import ensure_data_dirs, get_logs_dir
from config.v2_config import V2Config
from core.controller import Controller
from core.windows_claude import resolve_windows_claude_command, resolve_windows_claude_command_from_bat
from vibe.sentry_integration import init_sentry


def _configure_stdout_encoding() -> None:
    if os.name != "nt":
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


def _build_logging_handlers(logs_dir: str) -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.FileHandler(f"{logs_dir}/vibe_remote.log", encoding="utf-8")]
    if os.environ.get("VIBE_DISABLE_STDOUT_LOGGING", "").lower() not in {"1", "true", "yes"}:
        handlers.insert(0, logging.StreamHandler(sys.stdout))
    return handlers


def setup_logging(level: str = "INFO"):
    """Setup logging configuration with file location and line numbers"""
    # Create a custom formatter with file location
    log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(funcName)s() - %(message)s'

    # For development, you can use this more detailed format:
    # log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(pathname)s:%(lineno)d] - %(funcName)s() - %(message)s'

    ensure_data_dirs()
    logs_dir = str(get_logs_dir())
    _configure_stdout_encoding()

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        handlers=_build_logging_handlers(logs_dir),
    )


def apply_claude_sdk_patches():
    """Apply runtime patches for third-party SDK limits."""
    logger = logging.getLogger(__name__)
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli
    except Exception as exc:
        logger.warning(f"Claude SDK patch skipped: {exc}")
        return

    buffer_size = 16 * 1024 * 1024
    previous = getattr(subprocess_cli, "_MAX_BUFFER_SIZE", None)
    subprocess_cli._MAX_BUFFER_SIZE = buffer_size
    if previous != buffer_size:
        logger.info(
            "Patched claude_agent_sdk _MAX_BUFFER_SIZE from %s to %s bytes",
            previous,
            buffer_size,
        )

    if os.name == "nt":
        _resources_path = os.environ.get("CLAUDE_RESOURCES_PATH")
        _resolved_windows_cmd, _ = resolve_windows_claude_command(_resources_path)
        if _resolved_windows_cmd:
            logger.info("Windows Claude command resolved from CLAUDE_RESOURCES_PATH: %s", _resolved_windows_cmd[0])
        elif _resources_path:
            logger.warning("CLAUDE_RESOURCES_PATH set but no packaged Claude command found: %s", _resources_path)
        else:
            logger.info("CLAUDE_RESOURCES_PATH not set, will derive from .bat location")

        _original_build_command = subprocess_cli.SubprocessCLITransport._build_command

        def _patched_build_command(self):
            cmd = _original_build_command(self)
            if not cmd:
                return cmd
            if cmd[0].lower().endswith(".bat"):
                if _resolved_windows_cmd:
                    patched = [*_resolved_windows_cmd, *cmd[1:]]
                    logger.info("Windows .bat replaced with packaged Claude command: %s", patched[0])
                    return patched
                fallback = resolve_windows_claude_command_from_bat(cmd[0])
                if fallback:
                    patched = [*fallback, *cmd[1:]]
                    logger.info("Windows .bat expanded via .bat location: %s", patched[0])
                    return patched
                logger.warning(
                    "Windows .bat detected but cannot resolve packaged Claude command or node.exe/cli.js (bat=%s)",
                    cmd[0],
                )
            return cmd

        subprocess_cli.SubprocessCLITransport._build_command = _patched_build_command
        logger.info("Patched SubprocessCLITransport._build_command for Windows Claude launch resolution")

        import subprocess as _sp
        _CREATE_NO_WINDOW = 0x08000000
        _original_popen_init = _sp.Popen.__init__

        def _patched_popen_init(self, *args, **kwargs):
            creationflags = kwargs.get("creationflags", 0)
            kwargs["creationflags"] = creationflags | _CREATE_NO_WINDOW
            return _original_popen_init(self, *args, **kwargs)

        _sp.Popen.__init__ = _patched_popen_init
        logger.info("Patched subprocess.Popen to use CREATE_NO_WINDOW on Windows")


def _start_ui_server_thread(host: str, port: int):
    """Start Flask UI server in a background thread."""
    from vibe.ui_server import run_ui_server
    t = threading.Thread(target=run_ui_server, args=(host, port), daemon=True, name="ui-server")
    t.start()
    return t


def main():
    """Main entry point"""
    try:
        # Load configuration
        config = V2Config.load()

        # Setup logging
        setup_logging(config.runtime.log_level)
        logger = logging.getLogger(__name__)

        apply_claude_sdk_patches()
        init_sentry(config, component="service")

        logger.info("Starting vibe-remote service...")
        logger.info(f"Working directory: {config.runtime.default_cwd}")

        # Start Flask UI server in background thread
        ui_host = config.ui.setup_host
        ui_port = config.ui.setup_port
        _start_ui_server_thread(ui_host, ui_port)
        logger.info(f"Web UI starting at http://{ui_host}:{ui_port}")

        # Create and run controller
        from config.v2_compat import to_app_config

        controller = Controller(to_app_config(config))

        shutdown_initiated = False

        def _handle_shutdown(signum, frame):
            nonlocal shutdown_initiated
            if shutdown_initiated:
                return
            shutdown_initiated = True
            try:
                logger.info(f"Received signal {signum}, shutting down...")
            except Exception:
                pass
            try:
                controller.cleanup_sync()
            except Exception as cleanup_err:
                logger.error(f"Cleanup failed: {cleanup_err}")
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)

        controller.run()

    except Exception as e:
        logging.error(f"Failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
