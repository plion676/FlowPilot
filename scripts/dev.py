"""Run one local service with the root .env; secrets are never printed."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="OpsPilot 本地开发服务启动器")
parser.add_argument(
    "service",
    choices=[
        "business",
        "mcp",
        "agent",
        "web",
        "verify-bindings",
        "verify-insight",
        "index-sop",
    ],
)
args = parser.parse_args()
load_dotenv(root / ".env", override=False)
commands = {
    "index-sop": ("agent-api", [sys.executable, "-m", "app.rag.ingest"]),
    "business": ("business-service", ["go", "run", "./cmd/server"]),
    "mcp": ("mcp-server", ["node", "dist/index.js"]),
    "agent": (
        "agent-api",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
    ),
    "web": ("web", ["pnpm", "dev"]),
    "verify-bindings": (
        "agent-api",
        [sys.executable, "-m", "pytest", "tests/test_direct_integration.py", "-q"],
    ),
    "verify-insight": (
        "agent-api",
        [sys.executable, "-m", "pytest", "tests/test_insight_integration.py", "-q"],
    ),
}
if args.service in {"verify-bindings", "verify-insight"}:
    for target, source in {
        "OPSPILOT_TEST_MCP_URL": "MCP_URL",
        "OPSPILOT_TEST_MCP_SECRET": "MCP_CALL_SECRET",
        "OPSPILOT_TEST_BUSINESS_URL": "BUSINESS_BASE_URL",
        "OPSPILOT_TEST_SERVICE_TOKEN": "INTERNAL_SERVICE_TOKEN",
    }.items():
        value = os.getenv(source)
        if not value:
            parser.error(f"缺少环境变量 {source}")
        os.environ[target] = value
directory, command = commands[args.service]
os.chdir(root / "apps" / directory)
os.execvp(command[0], command)
