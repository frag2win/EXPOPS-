"""Single-process Windows launcher for the FastAPI application.

Psycopg 3's asynchronous API does not support Windows' default Proactor loop.
This entry point runs Uvicorn's server coroutine with a scoped Selector loop;
it does not change asyncio's process-wide event-loop policy.
"""

import argparse

from uvicorn import Config, Server

from app.db.asyncio_runner import run_with_database_event_loop


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ExperimentOps on Windows")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    config = Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        workers=1,
    )
    server = Server(config)
    try:
        run_with_database_event_loop(server.serve())
    except KeyboardInterrupt:
        # Uvicorn has already completed lifespan shutdown on Ctrl+C.
        return


if __name__ == "__main__":
    main()
