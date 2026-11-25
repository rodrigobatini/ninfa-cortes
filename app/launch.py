"""Run the studio with a graceful shutdown callable for the local UI."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def hide_worker_consoles():
    # The editor, clipper and downloader all start console tools via Popen.
    class BackgroundProcess(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
            super().__init__(*args, **kwargs)

    subprocess.Popen = BackgroundProcess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--log", action="store_true")
    args = parser.parse_args()
    if os.name == "nt" and Path(sys.executable).stem.lower() == "pythonw":
        hide_worker_consoles()
    if args.log or sys.stdout is None:
        directory = Path(os.environ["BATINI_CLIP_DATA"])
        directory.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = (directory / "portable.log").open("w", encoding="utf-8", buffering=1)
    import uvicorn
    from server import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, timeout_graceful_shutdown=10))
    app.state.stop = lambda: setattr(server, "should_exit", True)
    server.run()


if __name__ == "__main__":
    main()
