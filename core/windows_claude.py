import os
from pathlib import Path


def _iter_windows_native_claude_candidates(resources_path: str) -> list[str]:
    candidates: list[str] = []
    roots = [
        Path(resources_path) / "app.asar.unpacked" / "claudecodeui" / "node_modules" / "@anthropic-ai",
        Path(resources_path) / "node_modules" / "@anthropic-ai",
    ]
    for root in roots:
        for package_dir in sorted(root.glob("claude-code-win32-*/claude.exe")):
            candidates.append(str(package_dir))
    return candidates


def resolve_windows_claude_command(resources_path: str | None = None) -> tuple[list[str] | None, str | None]:
    if os.name != "nt":
        return None, None

    resolved_resources = resources_path or os.environ.get("CLAUDE_RESOURCES_PATH")
    if not resolved_resources:
        return None, None

    for candidate in _iter_windows_native_claude_candidates(resolved_resources):
        if os.path.isfile(candidate):
            return [candidate], candidate

    node_exe = os.path.join(resolved_resources, "bin", "node", "node.exe")
    cli_js = os.path.join(resolved_resources, "node_modules", "@anthropic-ai", "claude-code", "cli.js")
    if os.path.isfile(node_exe) and os.path.isfile(cli_js):
        return [node_exe, cli_js], cli_js

    return None, None


def resolve_windows_claude_command_from_bat(bat_path: str) -> list[str] | None:
    bat_dir = str(Path(bat_path).resolve().parent)
    node_exe = os.path.join(bat_dir, "node", "node.exe")
    cli_js = os.path.normpath(os.path.join(bat_dir, "..", "node_modules", "@anthropic-ai", "claude-code", "cli.js"))
    if os.path.isfile(node_exe) and os.path.isfile(cli_js):
        return [node_exe, cli_js]
    return None
