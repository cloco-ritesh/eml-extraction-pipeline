"""
Folder monitor: watches for new .eml files and dispatches them to the pipeline.
"""

import asyncio
import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer

from ingestion.pipeline import process_eml

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.5


class EmlHandler(FileSystemEventHandler):
    """Handle new .eml files by dispatching to the async pipeline."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._loop = loop

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        if not event.src_path.endswith(".eml"):
            return

        path = Path(event.src_path)
        logger.info("Detected new file: %s", path.name)

        # Debounce: wait for file write to complete
        time.sleep(DEBOUNCE_SECONDS)

        asyncio.run_coroutine_threadsafe(process_eml(path), self._loop)


def start_watching(folder: str) -> None:
    """Start watching a folder for new .eml files. Blocks indefinitely."""
    folder_path = Path(folder)
    folder_path.mkdir(parents=True, exist_ok=True)

    loop = asyncio.new_event_loop()

    handler = EmlHandler(loop)
    observer = Observer()
    observer.schedule(handler, str(folder_path), recursive=False)
    observer.start()

    logger.info("Watching %s for new .eml files... (Ctrl+C to stop)", folder_path.resolve())

    # Run the asyncio event loop in the main thread
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down watcher...")
    finally:
        observer.stop()
        observer.join()
        loop.close()
