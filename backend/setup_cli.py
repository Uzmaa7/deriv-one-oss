#!/usr/bin/env python3
"""Interactive terminal setup for the platform — writes platform.env before first boot.

Shares the same draft → env pipeline as the web setup wizard (platform_setup_store).
Run from the repo root via scripts/setup.sh or:

    cd backend && .venv/bin/python setup_cli.py
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent

load_dotenv(_BACKEND_DIR / ".env")

DEFAULT_DRAFT: dict[str, Any] = {
    "deployment_mode": "personal",
    "branding": {
        "app_name": "Platform",
        "app_short_name": "Platform",
        "app_logo_url": "/app-logo-icon.svg",
        "org_noun": "employee",
        "brand_color": "#FF444F",
        "brand_color_hover": "#e03d47",
        "brand_color_soft": "#FFF0F0",
    },
    "core": {
        "app_base_url": "http://localhost:3000",
        "resource_prefix": "platform",
        "app_env": "development",
    },
    "models": {
        "gateway_url": "http://127.0.0.1:4000/v1",
        "gateway_api_key": "",
        "encryption_key": "",
    },
    "identity": {
        "mock_user_email": "dev@localhost.local",
        "mock_user_groups": "platform-admins,platform-users",
        "oidc_issuer": "",
        "policy_aud": "",
        "oidc_jwks_url": "",
        "admin_groups": "platform-admins",
        "member_groups": "platform-users",
    },
    "directory": {"provider": "claims", "file_content": ""},
    "slack": {
        "mode": "skip",
        "config_collaborator": "",
        "config_access_token": "",
        "config_refresh_token": "",
        "ip_allowlist": "",
    },
}


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _prompt(label: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    value = input(f"{label}{hint}: ").strip()
    return value if value else default


def _prompt_secret(label: str) -> str:
    return getpass.getpass(f"{label}: ").strip()


def _prompt_choice(label: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    _say(label)
    for i, (_, text) in enumerate(options):
        suffix = " (default)" if i == default_index else ""
        _say(f"  {i + 1}) {text}{suffix}")
    while True:
        raw = input(f"Choose 1-{len(options)} [{default_index + 1}]: ").strip()
        if not raw:
            return options[default_index][0]
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        _say("Invalid choice — try again.")


def _validate_gateway(url: str, api_key: str) -> tuple[bool, str]:
    from model_gateway import probe_gateway

    ok, msg, _count = probe_gateway(url, api_key)
    return ok, msg


def _collect_models(draft: dict[str, Any], test: bool) -> None:
    models = draft["models"]
    _say("\n── Models ──")
    models["gateway_url"] = _prompt("Model gateway URL", models["gateway_url"])
    models["gateway_api_key"] = (
        _prompt_secret("Model gateway API key (optional for LM Studio / Ollama on localhost)")
        or models["gateway_api_key"]
    )
    from platform_setup_store import generate_encryption_key

    if not models["encryption_key"]:
        models["encryption_key"] = generate_encryption_key()
        _say("Generated a new secret encryption key.")
    else:
        regen = _prompt("Generate a new encryption key? (y/N)", "n").lower()
        if regen in ("y", "yes"):
            models["encryption_key"] = generate_encryption_key()
            _say("Generated a new secret encryption key.")

    if test:
        ok, msg = _validate_gateway(models["gateway_url"], models["gateway_api_key"])
        _say(f"Gateway test: {'OK — ' + msg if ok else 'FAILED — ' + msg}")


def _collect_branding(draft: dict[str, Any]) -> None:
    branding = draft["branding"]
    _say("\n── Branding ──")
    name = _prompt("App name", branding["app_name"])
    branding["app_name"] = name
    branding["app_short_name"] = _prompt("Short name", name)
    branding["app_logo_url"] = _prompt("Logo URL", branding["app_logo_url"])
    branding["org_noun"] = _prompt("Org noun (for prompts)", branding["org_noun"])
    draft["core"]["app_base_url"] = _prompt("Public app URL", draft["core"]["app_base_url"])


def _collect_identity(draft: dict[str, Any]) -> None:
    identity = draft["identity"]
    mode = draft["deployment_mode"]
    _say("\n── Identity ──")
    if mode == "personal":
        _say("Personal mode uses mock authentication for local development.")
        identity["mock_user_email"] = _prompt("Mock user email", identity["mock_user_email"])
        identity["mock_user_groups"] = _prompt(
            "Mock user groups (comma-separated)", identity["mock_user_groups"]
        )
        draft["core"]["app_env"] = "development"
    else:
        identity["oidc_issuer"] = _prompt("OIDC issuer URL", identity["oidc_issuer"]).rstrip("/")
        identity["policy_aud"] = _prompt("Policy audience (POLICY_AUD)", identity["policy_aud"])
        identity["oidc_jwks_url"] = _prompt(
            "JWKS URL (optional, auto-discovered if empty)", identity["oidc_jwks_url"]
        )
        identity["admin_groups"] = _prompt("Admin groups", identity["admin_groups"])
        identity["member_groups"] = _prompt("Member groups", identity["member_groups"])
        draft["core"]["app_env"] = "production"


def _collect_directory(draft: dict[str, Any]) -> None:
    _say("\n── Directory ──")
    provider = _prompt_choice(
        "How should access approvals resolve people?",
        [("claims", "From JWT claims (default)"), ("file", "YAML file on disk")],
        default_index=0,
    )
    draft["directory"]["provider"] = provider
    if provider == "file":
        _say("Paste directory YAML (end with a blank line):")
        lines: list[str] = []
        while True:
            line = sys.stdin.readline()
            if line == "\n" and lines:
                break
            lines.append(line)
        draft["directory"]["file_content"] = "".join(lines).strip()


def _collect_slack(draft: dict[str, Any]) -> None:
    _say("\n── Slack (optional) ──")
    mode = _prompt_choice(
        "Slack integration",
        [
            ("skip", "Skip for now"),
            ("manual", "Manual Slack apps later"),
            ("auto_create", "Auto-create via App Configuration tokens"),
        ],
        default_index=0,
    )
    slack = draft["slack"]
    slack["mode"] = mode
    if mode == "auto_create":
        slack["config_collaborator"] = _prompt("Config collaborator (Slack username)")
        slack["config_access_token"] = _prompt_secret("App config access token")
        slack["config_refresh_token"] = _prompt_secret("App config refresh token")
        slack["ip_allowlist"] = _prompt("IP allowlist CIDRs (comma-separated, optional)")


def _validate_draft(draft: dict[str, Any]) -> None:
    models = draft.get("models") or {}
    if not str(models.get("gateway_url") or "").strip():
        raise SystemExit("Model gateway URL is required.")
    api_key = str(models.get("gateway_api_key") or "").strip()
    if not api_key:
        ok, msg = _validate_gateway(str(models.get("gateway_url") or ""), "")
        if not ok:
            raise SystemExit(
                "Model gateway API key is required for this URL, or the gateway must be "
                f"reachable without auth. ({msg})"
            )
    if not str(models.get("encryption_key") or "").strip():
        raise SystemExit("Secret encryption key is required.")
    if draft.get("deployment_mode") == "team":
        identity = draft.get("identity") or {}
        if not str(identity.get("oidc_issuer") or "").strip():
            raise SystemExit("OIDC issuer is required for team mode.")
        if not str(identity.get("policy_aud") or "").strip():
            raise SystemExit("Policy audience is required for team mode.")
    directory = draft.get("directory") or {}
    if directory.get("provider") == "file" and not str(directory.get("file_content") or "").strip():
        raise SystemExit("Directory file content is required when using the file provider.")


def _print_start_commands(backend_port: int) -> None:
    _say("\n" + "═" * 60)
    _say("Start the stack manually:\n")
    _say(f"  npm run dev:stack")
    _say(f"  # or: DEV_API_PORT={backend_port} bash scripts/dev.sh")
    _say("\n  Then open http://localhost:3000")
    _say("\nTip: set SKILLS_STORE_ENABLED=0 in backend/.env to disable the 60s")
    _say("skills sync during local development.")
    _say("═" * 60)


def _start_stack(backend_port: int) -> None:
    dev_sh = _REPO_ROOT / "scripts" / "dev.sh"
    if not dev_sh.is_file():
        _say("scripts/dev.sh not found — start manually:")
        _print_start_commands(backend_port)
        return
    env = os.environ.copy()
    env["DEV_API_PORT"] = str(backend_port)
    _say("\nLaunching backend and frontend… (Ctrl+C to stop both)\n")
    os.execve("/bin/bash", ["bash", str(dev_sh)], env)


def _offer_start_stack(backend_port: int, auto_start: bool, no_start: bool) -> None:
    if no_start:
        _print_start_commands(backend_port)
        return
    if not auto_start:
        if _prompt("Start backend and frontend now? (Y/n)", "y").lower() in ("n", "no"):
            _print_start_commands(backend_port)
            return
    _start_stack(backend_port)


def run_quick(
    backend_port: int,
    test_gateway: bool,
    auto_start: bool,
    no_start: bool,
) -> None:
    draft = {**DEFAULT_DRAFT, "deployment_mode": "personal"}
    _say("Platform — quick setup (personal / local dev)\n")
    _collect_branding(draft)
    _collect_models(draft, test=test_gateway)
    identity = draft["identity"]
    identity["mock_user_email"] = _prompt("Mock user email", identity["mock_user_email"])
    _finalize(draft, backend_port, auto_start, no_start)


def run_interactive(
    backend_port: int,
    test_gateway: bool,
    auto_start: bool,
    no_start: bool,
) -> None:
    draft = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULT_DRAFT.items()}
    _say("Platform — setup wizard\n")
    draft["deployment_mode"] = _prompt_choice(
        "Who is this deployment for?",
        [
            ("personal", "Personal / solo (mock auth)"),
            ("team", "Team / company (OIDC)"),
        ],
        default_index=0,
    )
    _collect_branding(draft)
    _collect_models(draft, test=test_gateway)
    _collect_identity(draft)
    _collect_directory(draft)
    _collect_slack(draft)
    _say("\n── Review ──")
    _say(f"  Mode:      {draft['deployment_mode']}")
    _say(f"  App name:  {draft['branding']['app_name']}")
    _say(f"  Gateway:   {draft['models']['gateway_url']}")
    _say(f"  Directory: {draft['directory']['provider']}")
    _say(f"  Slack:     {draft['slack']['mode']}")
    if _prompt("Write configuration and finish? (Y/n)", "y").lower() in ("n", "no"):
        raise SystemExit("Setup cancelled.")
    _finalize(draft, backend_port, auto_start, no_start)


def _finalize(
    draft: dict[str, Any],
    backend_port: int,
    auto_start: bool,
    no_start: bool,
) -> None:
    _validate_draft(draft)
    from database import init_db
    from platform_setup_store import complete_setup, is_setup_complete

    init_db()
    if is_setup_complete():
        _say("\nNote: setup was already marked complete — this run will overwrite platform.env.")

    result = complete_setup(draft)
    env_path = result.get("env_path", "")
    _say(f"\nWrote configuration to {env_path}")
    if result.get("directory_path"):
        _say(f"Wrote directory file to {result['directory_path']}")
    _offer_start_stack(backend_port, auto_start, no_start)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure the platform before starting the backend and frontend.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Personal/local dev setup with fewer prompts.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Backend port for the printed start commands (default: 8001).",
    )
    parser.add_argument(
        "--test-gateway",
        action="store_true",
        help="Ping the model gateway after entering URL and API key.",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Start backend and frontend immediately after setup (no prompt).",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Only write platform.env; do not start servers.",
    )
    args = parser.parse_args()

    if args.start and args.no_start:
        parser.error("Use only one of --start or --no-start.")

    try:
        if args.quick:
            run_quick(args.port, args.test_gateway, args.start, args.no_start)
        else:
            run_interactive(args.port, args.test_gateway, args.start, args.no_start)
    except KeyboardInterrupt:
        _say("\nSetup cancelled.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
