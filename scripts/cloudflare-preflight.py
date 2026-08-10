#!/usr/bin/env python3
"""Fail-fast validation for an AutoReach Cloudflare production release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SECRETS = {"AUTOREACH_API_TOKEN", "AUTOREACH_INTERNAL_HMAC_SECRET"}
REQUIRED_MIGRATIONS = {
    "0001_core_durable_state.sql",
    "0002_application_state.sql",
    "0003_email_writer_state.sql",
    "0004_research_profile_index.sql",
    "0005_production_cutover.sql",
}


def fail(message: str, errors: list[str]) -> None:
    errors.append(message)


def run(*args: str) -> str:
    completed = subprocess.run(
        args, cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"{' '.join(args)} failed: {detail}")
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-placeholder",
        action="store_true",
        help="Allow the checked-in D1 placeholder for local/CI verification.",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Also verify minimum Worker Secrets and remote migration state.",
    )
    args = parser.parse_args()
    errors: list[str] = []

    wrangler = (ROOT / "wrangler.jsonc").read_text(encoding="utf-8")
    worker = (ROOT / "src" / "index.ts").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    if "REPLACE_WITH_D1_DATABASE_ID" in wrangler and not args.allow_placeholder:
        fail("Replace REPLACE_WITH_D1_DATABASE_ID before deployment.", errors)
    for binding in (
        '"AUTOREACH_DB"', '"AUTOREACH_ARTIFACTS"', '"AUTOREACH_JOBS"',
        '"AUTOREACH_FOLLOWUPS"', '"AutoReachContainer"', '"max_instances": 1',
    ):
        if binding not in wrangler:
            fail(f"Wrangler is missing {binding}.", errors)
    for setting in (
        'AUTOREACH_STORAGE_BACKEND: "d1"',
        'AUTOREACH_LEAD_PIPELINE_BACKEND: "d1"',
        'AUTOREACH_ARTIFACT_BACKEND: "r2"',
        'AUTOREACH_EMAIL_WRITER_STORAGE_BACKEND: "d1"',
        'AUTOREACH_SENDER_STORAGE_BACKEND: "d1"',
        'AUTOREACH_REPLY_STORAGE_BACKEND: "d1"',
    ):
        if setting not in worker:
            fail(f"Container production environment is missing {setting}.", errors)
    if "EXPOSE 8000" not in dockerfile or '"${PORT:-8000}"' not in (
        ROOT / "scripts" / "start-api.sh"
    ).read_text(encoding="utf-8"):
        fail("Container must expose and listen on PORT 8000.", errors)

    migration_names = {path.name for path in (ROOT / "migrations").glob("*.sql")}
    missing = REQUIRED_MIGRATIONS - migration_names
    if missing:
        fail(f"Missing migrations: {', '.join(sorted(missing))}.", errors)

    if args.remote:
        try:
            raw = run("npx", "wrangler", "secret", "list", "--format", "json")
            listed = json.loads(raw)
            names = {
                str(item.get("name"))
                for item in listed
                if isinstance(item, dict) and item.get("name")
            }
            missing_secrets = REQUIRED_SECRETS - names
            if missing_secrets:
                fail(f"Missing Worker Secrets: {', '.join(sorted(missing_secrets))}.", errors)
        except (RuntimeError, json.JSONDecodeError) as exc:
            fail(str(exc), errors)
        try:
            pending = run("npx", "wrangler", "d1", "migrations", "list", "autoreach", "--remote")
            unapplied = sorted(set(re.findall(r"\b\d{4}_[\w-]+\.sql\b", pending)))
            if unapplied:
                fail(f"Unapplied remote D1 migrations: {', '.join(unapplied)}.", errors)
        except RuntimeError as exc:
            fail(str(exc), errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Cloudflare production preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
