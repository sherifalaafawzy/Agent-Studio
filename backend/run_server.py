"""Server entry point of the installed desktop app (started by electron/main.js with the bundled Python).

Prints progress lines while the modules load, so the window can show that a slow first start is still moving."""
import os

from app import boot

if __name__ == "__main__":
    boot.line("starting")
    stop = boot.heartbeat()
    import uvicorn

    from app.main import create_app

    app = create_app()
    boot.line(f"ready to listen after {stop():.1f} s")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("AGENT_STUDIO_PORT", "8765")), log_level="info")
