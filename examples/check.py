"""Run independent example environments; external databases require opt-in."""

import argparse
import os
import subprocess
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "names", nargs="*", help="Directory names; omit for all local examples"
    )
    parser.add_argument("--include-services", action="store_true")
    parser.add_argument(
        "--check-only", action="store_true", help="Django checks, without pytest"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    projects = {path.parent.name: path.parent for path in root.glob("*/pyproject.toml")}
    unknown = set(args.names) - projects.keys()
    if unknown:
        parser.error(f"Unknown examples: {', '.join(sorted(unknown))}")
    failed = []
    for name in sorted(args.names or projects):
        path = projects[name]
        config = tomllib.loads((path / "pyproject.toml").read_text())
        service = (
            config.get("tool", {}).get("aiodrf-example", {}).get("external-service")
        )
        if service and not args.include_services:
            print(
                f"SKIP {name}: requires {service}; use --include-services", flush=True
            )
            continue
        profiles = (
            config.get("tool", {})
            .get("aiodrf-example", {})
            .get("settings-profiles", [None])
        )
        environment = os.environ.copy()
        # A parent `uv run` or nox session must not select this project's
        # installer target or Django settings.
        for key in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "DJANGO_SETTINGS_MODULE"):
            environment.pop(key, None)
        executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        setup = []
        if not (path / ".venv/pyvenv.cfg").is_file():
            setup.append(["uv", "venv"])
        setup.append(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(path / ".venv" / executable),
                "-r",
                "pyproject.toml",
                "--group",
                "test",
                "-e",
                str(root.parent),
            ]
        )
        for command in setup:
            result = subprocess.run(  # noqa: S603 -- fixed commands, repository-owned projects.
                command, cwd=path, env=environment, check=False, timeout=300
            )
            if result.returncode:
                failed.append(name + ":setup")
                break
        else:
            result = None
        if result is not None:
            continue
        commands = [["uv", "run", "--no-sync", "python", "manage.py", "check"]]
        if not args.check_only:
            commands.append(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "pytest",
                    "-q",
                    "--tb=short",
                ]
            )
        for profile in profiles:
            env = environment.copy()
            if profile:
                env["DJANGO_SETTINGS_MODULE"] = profile
            for command in commands:
                print(
                    f"{name} ({profile or 'default'}): {' '.join(command)}", flush=True
                )
                result = subprocess.run(  # noqa: S603 -- commands and project paths are owned by this repository.
                    command, cwd=path, env=env, check=False, timeout=120
                )
                if result.returncode:
                    failed.append(name + (":" + profile if profile else ""))
                    break
    if failed:
        raise SystemExit(f"Failed examples: {', '.join(failed)}")


if __name__ == "__main__":
    main()
