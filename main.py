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
from vibe.sentry_integration import init_sentry


def _build_logging_handlers(logs_dir: str) -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.FileHandler(f"{logs_dir}/vibe_remote.log")]
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

    # On Windows, shutil.which("claude") resolves to claude.bat.
    # Python subprocess wraps .bat via cmd.exe /c, which buffers stdin/stdout
    # and breaks the SDK's JSON pipe protocol (causes "control request timeout").
    # Fix: intercept _build_command and expand .bat to node.exe + cli.js directly,
    # matching the approach used by the Electron terminal mode.
    if os.name == "nt":
        # Pre-resolve paths from CLAUDE_RESOURCES_PATH set by Electron,
        # falling back to deriving from the .bat location via PATH.
        _resources_path = os.environ.get("CLAUDE_RESOURCES_PATH")
        if _resources_path:
            _resolved_node = os.path.join(_resources_path, "bin", "node", "node.exe")
            _resolved_cli_js = os.path.join(_resources_path, "node_modules", "@anthropic-ai", "claude-code", "cli.js")
            if os.path.isfile(_resolved_node) and os.path.isfile(_resolved_cli_js):
                _win_node_exe = _resolved_node
                _win_cli_js = _resolved_cli_js
                logger.info("Windows CLI paths from CLAUDE_RESOURCES_PATH: %s", _resources_path)
            else:
                _win_node_exe = None
                logger.warning(
                    "CLAUDE_RESOURCES_PATH set but files missing "
                    "(node=%s, cli=%s)", _resolved_node, _resolved_cli_js,
                )
        else:
            _win_node_exe = None
            logger.info("CLAUDE_RESOURCES_PATH not set, will derive from .bat location")

        _original_build_command = subprocess_cli.SubprocessCLITransport._build_command

        def _patched_build_command(self):
            cmd = _original_build_command(self)
            if cmd and cmd[0].lower().endswith(".bat"):
                if _win_node_exe:
                    patched = [_win_node_exe, _win_cli_js] + cmd[1:]
                    logger.info(
                        "Windows .bat expanded via CLAUDE_RESOURCES_PATH: %s",
                        patched[0],
                    )
                    return patched
                from pathlib import Path as _P
                bat_dir = str(_P(cmd[0]).resolve().parent)
                node_exe = os.path.join(bat_dir, "node", "node.exe")
                cli_js = os.path.normpath(os.path.join(bat_dir, "..", "node_modules", "@anthropic-ai", "claude-code", "cli.js"))
                if os.path.isfile(node_exe) and os.path.isfile(cli_js):
                    patched = [node_exe, cli_js] + cmd[1:]
                    logger.info("Windows .bat expanded via .bat location: %s", patched[0])
                    return patched
                logger.warning(
                    "Windows .bat detected but cannot resolve node.exe/cli.js "
                    "(node_exe=%s, cli_js=%s)", node_exe, cli_js,
                )
            return cmd

        subprocess_cli.SubprocessCLITransport._build_command = _patched_build_command
        logger.info("Patched SubprocessCLITransport._build_command for Windows .bat workaround")

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
