from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local AI Agent backend.")
    parser.add_argument("--host", default=os.environ.get("APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("APP_PORT", "8000")))
    parser.add_argument("--app-data-dir", default=os.environ.get("APP_DATA_DIR"))
    parser.add_argument("--environment", default=os.environ.get("ENVIRONMENT"))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.app_data_dir:
        os.environ["APP_DATA_DIR"] = args.app_data_dir
    if args.environment:
        os.environ["ENVIRONMENT"] = args.environment

    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
