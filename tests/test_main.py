from __future__ import annotations

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as main_module


class _DummyController:
    def __init__(self):
        self.cleaned = False
        self.ran = False

    def cleanup_sync(self):
        self.cleaned = True

    def run(self):
        self.ran = True


class _DummyConfig:
    class Runtime:
        log_level = "INFO"
        default_cwd = "/tmp/workdir"

    class UI:
        setup_host = "127.0.0.1"
        setup_port = 7788

    runtime = Runtime()
    ui = UI()


def test_main_starts_ui_server_before_controller_run(monkeypatch):
    events = []
    controller = _DummyController()

    monkeypatch.setattr(main_module.V2Config, "load", lambda: _DummyConfig())
    monkeypatch.setattr(main_module, "setup_logging", lambda level: events.append(("logging", level)))
    monkeypatch.setattr(main_module, "apply_claude_sdk_patches", lambda: events.append(("patches", None)))
    monkeypatch.setattr(main_module, "init_sentry", lambda config, component=None: events.append(("sentry", component)))
    monkeypatch.setattr(main_module, "_start_ui_server_thread", lambda host, port: events.append(("ui", host, port)))
    monkeypatch.setattr(main_module, "Controller", lambda config: controller)

    compat_module = types.ModuleType("config.v2_compat")
    compat_module.to_app_config = lambda config: events.append(("to_app_config", config)) or {"config": config}
    monkeypatch.setitem(sys.modules, "config.v2_compat", compat_module)

    main_module.main()

    ui_index = next(i for i, item in enumerate(events) if item[0] == "ui")
    adapt_index = next(i for i, item in enumerate(events) if item[0] == "to_app_config")
    assert ui_index < adapt_index
    assert ("ui", "127.0.0.1", 7788) in events
    assert controller.ran is True


def test_apply_claude_sdk_patches_updates_max_buffer(monkeypatch):
    fake_subprocess_cli = types.SimpleNamespace(_MAX_BUFFER_SIZE=1024)
    fake_subprocess_cli.SubprocessCLITransport = type(
        "Transport",
        (),
        {"_build_command": staticmethod(lambda self: ["claude"])}
    )

    transport_module = types.ModuleType("claude_agent_sdk._internal.transport")
    transport_module.subprocess_cli = fake_subprocess_cli
    monkeypatch.setitem(sys.modules, "claude_agent_sdk._internal.transport", transport_module)

    monkeypatch.setattr(main_module.os, "name", "posix", raising=False)
    main_module.apply_claude_sdk_patches()

    assert fake_subprocess_cli._MAX_BUFFER_SIZE == 16 * 1024 * 1024


def test_apply_claude_sdk_patches_prefers_packaged_native_claude_exe(monkeypatch):
    class Transport:
        def _build_command(self):
            return [r"C:\Tools\claude.bat", "--print"]

    fake_subprocess_cli = types.SimpleNamespace(
        _MAX_BUFFER_SIZE=1024,
        SubprocessCLITransport=Transport,
    )
    transport_module = types.ModuleType("claude_agent_sdk._internal.transport")
    transport_module.subprocess_cli = fake_subprocess_cli
    monkeypatch.setitem(sys.modules, "claude_agent_sdk._internal.transport", transport_module)

    monkeypatch.setattr(main_module.os, "name", "nt", raising=False)
    monkeypatch.setenv("CLAUDE_RESOURCES_PATH", r"C:\Claude")
    native_exe = os.path.join(
        r"C:\Claude",
        "app.asar.unpacked",
        "claudecodeui",
        "node_modules",
        "@anthropic-ai",
        "claude-code-win32-x64",
        "claude.exe",
    )
    monkeypatch.setattr(main_module.os.path, "isfile", lambda path: path == native_exe)

    import subprocess as _sp
    original_popen_init = _sp.Popen.__init__
    try:
        main_module.apply_claude_sdk_patches()
        command = fake_subprocess_cli.SubprocessCLITransport()._build_command()
        assert command == [native_exe, "--print"]
    finally:
        _sp.Popen.__init__ = original_popen_init


def test_apply_claude_sdk_patches_uses_packaged_node_cli_when_native_exe_missing(monkeypatch):
    class Transport:
        def _build_command(self):
            return [r"C:\Tools\claude.bat", "--print"]

    fake_subprocess_cli = types.SimpleNamespace(
        _MAX_BUFFER_SIZE=1024,
        SubprocessCLITransport=Transport,
    )
    transport_module = types.ModuleType("claude_agent_sdk._internal.transport")
    transport_module.subprocess_cli = fake_subprocess_cli
    monkeypatch.setitem(sys.modules, "claude_agent_sdk._internal.transport", transport_module)

    monkeypatch.setattr(main_module.os, "name", "nt", raising=False)
    monkeypatch.setenv("CLAUDE_RESOURCES_PATH", r"C:\Claude")
    monkeypatch.setattr(
        main_module.os.path,
        "isfile",
        lambda path: path in {
            os.path.join(r"C:\Claude", "bin", "node", "node.exe"),
            os.path.join(r"C:\Claude", "node_modules", "@anthropic-ai", "claude-code", "cli.js"),
        },
    )

    import subprocess as _sp
    original_popen_init = _sp.Popen.__init__
    try:
        main_module.apply_claude_sdk_patches()
        command = fake_subprocess_cli.SubprocessCLITransport()._build_command()
        assert command == [
            os.path.join(r"C:\Claude", "bin", "node", "node.exe"),
            os.path.join(r"C:\Claude", "node_modules", "@anthropic-ai", "claude-code", "cli.js"),
            "--print",
        ]
    finally:
        _sp.Popen.__init__ = original_popen_init


def test_apply_claude_sdk_patches_falls_back_to_bat_location(monkeypatch):
    class Transport:
        def _build_command(self):
            return [r"C:\Tools\claude.bat", "--version"]

    fake_subprocess_cli = types.SimpleNamespace(
        _MAX_BUFFER_SIZE=1024,
        SubprocessCLITransport=Transport,
    )
    transport_module = types.ModuleType("claude_agent_sdk._internal.transport")
    transport_module.subprocess_cli = fake_subprocess_cli
    monkeypatch.setitem(sys.modules, "claude_agent_sdk._internal.transport", transport_module)

    monkeypatch.setattr(main_module.os, "name", "nt", raising=False)
    monkeypatch.delenv("CLAUDE_RESOURCES_PATH", raising=False)

    fake_path = type(
        "FakePath",
        (),
        {
            "resolve": lambda self: type("Resolved", (), {"parent": r"C:\Tools"})(),
        },
    )
    monkeypatch.setattr("pathlib.Path", lambda *_args, **_kwargs: fake_path())

    expected_node = os.path.join(r"C:\Tools", "node", "node.exe")
    expected_cli = os.path.normpath(os.path.join(r"C:\Tools", "..", "node_modules", "@anthropic-ai", "claude-code", "cli.js"))
    monkeypatch.setattr(main_module.os.path, "isfile", lambda path: path in {expected_node, expected_cli})

    import subprocess as _sp
    original_popen_init = _sp.Popen.__init__
    try:
        main_module.apply_claude_sdk_patches()
        command = fake_subprocess_cli.SubprocessCLITransport()._build_command()
        assert command == [expected_node, expected_cli, "--version"]
    finally:
        _sp.Popen.__init__ = original_popen_init


def test_apply_claude_sdk_patches_sets_create_no_window(monkeypatch):
