#!/usr/bin/env python3
"""
AIMMS Environment Switch CLI

Command-line tool to switch between demo and live data modes.
This is a standalone script that doesn't require the full AIMMS package.

Usage:
    # Check current mode
    python -m ai.core.switch_mode status

    # Switch to demo mode
    python -m ai.core.switch_mode demo

    # Switch to live mode
    python -m ai.core.switch_mode live

    # Toggle between modes
    python -m ai.core.switch_mode toggle
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ANSI color codes for terminal output
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


def get_env_file_path() -> Path:
    """Get the path to the .env file."""
    # Start from current directory and look for .env
    current = Path.cwd()

    # Check current directory
    env_path = current / ".env"
    if env_path.exists():
        return env_path

    # Check parent directories
    for parent in current.parents:
        env_path = parent / ".env"
        if env_path.exists():
            return env_path
        # Stop at the project root (where pyproject.toml is)
        if (parent / "pyproject.toml").exists():
            break

    # Default to current directory
    return Path.cwd() / ".env"


def read_env_value(key: str, env_path: Path) -> str | None:
    """Read a value from the .env file."""
    if not env_path.exists():
        return None

    with Path(env_path).open("r") as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1].strip()
                # Remove quotes if present
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                return value

    return None


def update_env_value(key: str, value: str, env_path: Path) -> bool:
    """Update a value in the .env file."""
    if not env_path.exists():
        print(f"{Colors.RED}Error: .env file not found at {env_path}{Colors.END}")
        return False

    lines = []
    found = False

    with Path(env_path).open("r") as f:
        for line in f:
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}\n")
                found = True
            else:
                lines.append(line)

    if not found:
        # Add the key if it doesn't exist
        lines.append(f"\n{key}={value}\n")

    with Path(env_path).open("w") as f:
        f.writelines(lines)

    return True


def get_current_mode(env_path: Path) -> str:
    """Get the current data mode."""
    value = read_env_value("USE_DEMO_DATASET", env_path)

    if value is None:
        return "unknown"

    return "demo" if value.lower() == "true" else "live"


def print_status(env_path: Path) -> None:
    """Print the current status."""
    mode = get_current_mode(env_path)
    demo_path = read_env_value("DEMO_DATASET_PATH", env_path) or "./inventree-demo-dataset"
    demo_json = (
        read_env_value("DEMO_DATASET_JSON", env_path)
        or "./inventree-demo-dataset/inventree_data.json"
    )
    inventree_url = read_env_value("INVENTREE_URL", env_path) or "Not configured"

    print(
        f"\n{Colors.BOLD}╔══════════════════════════════════════════════════════════════╗{Colors.END}"
    )
    print(
        f"{Colors.BOLD}║             AIMMS Data Environment Status                     ║{Colors.END}"
    )
    print(
        f"{Colors.BOLD}╚══════════════════════════════════════════════════════════════╝{Colors.END}\n"
    )

    if mode == "demo":
        print(f"  {Colors.BOLD}Current Mode:{Colors.END}  {Colors.GREEN}🧪 DEMO{Colors.END}")
        print(f"  {Colors.BOLD}Description:{Colors.END}   Using static demo dataset (no API calls)")
        print(f"  {Colors.BOLD}Dataset Path:{Colors.END}  {demo_path}")
        print(f"  {Colors.BOLD}JSON File:{Colors.END}     {demo_json}")

        # Check if demo file exists
        json_path = Path(demo_json)
        if not json_path.exists():
            # Try relative to project root
            json_path = Path.cwd() / demo_json

        if json_path.exists():
            with Path(json_path).open("r") as f:
                data = json.load(f)

            # Handle Django fixtures format (list of objects with 'model' key)
            if isinstance(data, list) and len(data) > 0 and "model" in data[0]:
                parts = len([item for item in data if item.get("model") == "part.part"])
                stock = len([item for item in data if item.get("model") == "stock.stockitem"])
                categories = len([
                    item for item in data if item.get("model") == "part.partcategory"
                ])
            else:
                # Handle simple dict format
                parts = len(data.get("part", []))
                stock = len(data.get("stock_stockitem", []))
                categories = len(data.get("part_partcategory", []))

            print(f"\n  {Colors.BOLD}Dataset Statistics:{Colors.END}")
            print(f"    • Parts:      {Colors.CYAN}{parts}{Colors.END}")
            print(f"    • Stock:      {Colors.CYAN}{stock}{Colors.END}")
            print(f"    • Categories: {Colors.CYAN}{categories}{Colors.END}")
        else:
            print(f"\n  {Colors.YELLOW}⚠ Warning: Demo dataset file not found!{Colors.END}")

    elif mode == "live":
        print(f"  {Colors.BOLD}Current Mode:{Colors.END}  {Colors.RED}🔴 LIVE{Colors.END}")
        print(f"  {Colors.BOLD}Description:{Colors.END}   Using live InvenTree API")
        print(f"  {Colors.BOLD}InvenTree URL:{Colors.END} {inventree_url}")

        # Check if token is configured
        token = read_env_value("INVENTREE_TOKEN", env_path)
        if token and token != "your-inventree-api-token":
            print(
                f"  {Colors.BOLD}API Token:{Colors.END}     {Colors.GREEN}Configured ✓{Colors.END}"
            )
        else:
            print(
                f"  {Colors.BOLD}API Token:{Colors.END}     {Colors.YELLOW}Not configured ⚠{Colors.END}"
            )

    else:
        print(f"  {Colors.BOLD}Current Mode:{Colors.END}  {Colors.YELLOW}UNKNOWN{Colors.END}")
        print(f"  {Colors.YELLOW}USE_DEMO_DATASET not set in .env file{Colors.END}")

    print(f"\n  {Colors.BOLD}Config File:{Colors.END}   {env_path}")
    print()
    print(f"  {Colors.BLUE}Commands:{Colors.END}")
    print("    python -m ai.core.switch_mode demo    # Switch to demo mode")
    print("    python -m ai.core.switch_mode live    # Switch to live mode")
    print("    python -m ai.core.switch_mode toggle  # Toggle mode")
    print()


def switch_to_demo(env_path: Path) -> None:
    """Switch to demo mode."""
    current = get_current_mode(env_path)

    if current == "demo":
        print(f"{Colors.YELLOW}Already in demo mode.{Colors.END}")
        return

    if update_env_value("USE_DEMO_DATASET", "true", env_path):
        print(f"\n{Colors.GREEN}✓ Switched to DEMO mode{Colors.END}")
        print("  Workflows will use static data from: ./inventree-demo-dataset/")
        print(
            f"\n{Colors.YELLOW}Note: Restart the server for changes to take effect.{Colors.END}\n"
        )


def switch_to_live(env_path: Path) -> None:
    """Switch to live mode."""
    current = get_current_mode(env_path)

    if current == "live":
        print(f"{Colors.YELLOW}Already in live mode.{Colors.END}")
        return

    # Check if InvenTree is configured
    inventree_url = read_env_value("INVENTREE_URL", env_path)
    inventree_token = read_env_value("INVENTREE_TOKEN", env_path)

    if not inventree_url or inventree_url == "http://localhost:8000/api/":
        print(
            f"\n{Colors.YELLOW}⚠ Warning: InvenTree URL may not be configured properly.{Colors.END}"
        )

    if not inventree_token or inventree_token == "your-inventree-api-token":
        print(f"\n{Colors.YELLOW}⚠ Warning: InvenTree API token is not configured.{Colors.END}")
        print("  Please set INVENTREE_TOKEN in .env before using live mode.\n")

    if update_env_value("USE_DEMO_DATASET", "false", env_path):
        print(f"\n{Colors.GREEN}✓ Switched to LIVE mode{Colors.END}")
        print("  Workflows will use the InvenTree API")
        print(
            f"\n{Colors.YELLOW}Note: Restart the server for changes to take effect.{Colors.END}\n"
        )


def toggle_mode(env_path: Path) -> None:
    """Toggle between demo and live modes."""
    current = get_current_mode(env_path)

    if current == "demo":
        switch_to_live(env_path)
    else:
        switch_to_demo(env_path)


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Switch between demo and live data modes for AIMMS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m ai.core.switch_mode status   # Show current mode
  python -m ai.core.switch_mode demo     # Use demo dataset
  python -m ai.core.switch_mode live     # Use live InvenTree API
  python -m ai.core.switch_mode toggle   # Toggle between modes
        """,
    )

    parser.add_argument(
        "command",
        choices=["status", "demo", "live", "toggle"],
        help="Command to execute",
    )

    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to .env file (auto-detected if not specified)",
    )

    args = parser.parse_args()

    # Get .env file path
    env_path = args.env_file or get_env_file_path()

    if not env_path.exists():
        print(f"{Colors.RED}Error: .env file not found at {env_path}{Colors.END}")
        print("Please create a .env file or specify the path with --env-file")
        sys.exit(1)

    # Execute command
    if args.command == "status":
        print_status(env_path)
    elif args.command == "demo":
        switch_to_demo(env_path)
    elif args.command == "live":
        switch_to_live(env_path)
    elif args.command == "toggle":
        toggle_mode(env_path)


if __name__ == "__main__":
    main()
