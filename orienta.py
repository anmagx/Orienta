import multiprocessing
import sys


def check_python_version():
    if sys.version_info < (3, 8):
        print("Error: Python 3.8 or higher is required")
        print(f"Current version: {sys.version}")
        sys.exit(1)


def main():
    check_python_version()
    multiprocessing.set_start_method("spawn", force=True)

    from src.workers.process_man import ProcessHandler

    handler = ProcessHandler()
    handler.start_workers()
    try:
        # Main loop: exit and stop workers when the shared stop_event is set
        while not handler.stop_event.wait(timeout=0.5):
            pass  # Event.wait() returns immediately when set
    except KeyboardInterrupt:
        print("[Main] KeyboardInterrupt, shutting down...")
    finally:
        handler.stop_workers()

if __name__ == "__main__":
    # Must run before startup work so frozen worker processes dispatch correctly.
    multiprocessing.freeze_support()
    main()