from __future__ import annotations

import argparse
import logging
import logging.handlers
import subprocess
import sys
import time
from pathlib import Path

from vocalize.config import CONFIG_PATH, load_config

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _setup_logging() -> None:
    """Configure console logging + crash hooks. File handler added per component."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console: INFO+
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console)

    # Catch unhandled exceptions
    def _exception_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("CRASH").critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _exception_hook

    # Catch unhandled exceptions in threads
    import threading

    def _thread_exception_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        logging.getLogger("CRASH").critical(
            "Unhandled exception in thread %s",
            args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_exception_hook


def _add_file_logging(component: str) -> None:
    """Add rotating file handler: logs/server.log or logs/client.log."""
    LOG_DIR.mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / f"{component}.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(file_handler)


_setup_logging()
logger = logging.getLogger(__name__)



def run_server() -> None:
    _add_file_logging("server")
    import uvicorn

    config = load_config()

    # Auto-pull Ollama model if any LLM pipeline steps are enabled
    llm_steps_enabled = any(
        s.type == "llm_rewrite" and s.enabled for s in config.pipeline.steps
    )
    if llm_steps_enabled:
        from vocalize.server.ollama import ensure_model

        ok = ensure_model(config.llm.base_url, config.llm.model)
        if not ok:
            logger.warning(
                "Could not ensure Ollama model '%s' is available. "
                "LLM post-processing will fail until the model is pulled.",
                config.llm.model,
            )

    from vocalize.server.app import create_app

    app = create_app(config)
    uvicorn.run(app, host=config.server.host, port=config.server.port)


def run_client_ui() -> None:
    _add_file_logging("client-ui")
    from vocalize.client_pyside6.app import VocalizeApp

    config = load_config()
    app = VocalizeApp(config)
    app.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Vocalize")
    parser.add_argument(
        "command",
        nargs="?",
        default="both",
        choices=["server", "client", "both"],
        help="Component to run (default: both)",
    )
    args = parser.parse_args()

    if args.command == "server":
        run_server()
    elif args.command == "client":
        run_client_ui()
    else:
        # Launch server as subprocess, then run client
        config = load_config()
        server_proc = subprocess.Popen(
            [sys.executable, "-m", "vocalize.cli", "server"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # Wait for server to be ready via health check polling
            import httpx

            server_url = f"http://{config.server.host}:{config.server.port}"
            server_ready = False
            for _ in range(60):
                try:
                    resp = httpx.get(f"{server_url}/api/health", timeout=1)
                    if resp.status_code == 200:
                        server_ready = True
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.5)
            if not server_ready:
                logger.error("Server did not start in time.")
                sys.exit(1)
            run_client_ui()
        finally:
            server_proc.terminate()
            server_proc.wait(timeout=5)


def run_live_captions() -> None:
    _add_file_logging("live-captions")
    # Silence console — only transcription output (plain print) should appear
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler):
            h.setLevel(logging.CRITICAL)
    from vocalize.captions import main as captions_main

    captions_main()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "both"
    if cmd == "server":
        run_server()
    elif cmd == "client":
        run_client_ui()
    else:
        main()
