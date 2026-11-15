"""Entry point used by PyInstaller (and handy for `python run_service.py`)."""
import multiprocessing

from app.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
