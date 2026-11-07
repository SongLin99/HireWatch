import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from .collectors import SOURCES, collect
from .models import now
from .notify import Notifier

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, store):
        self.store = store
        self.notifier = Notifier(store)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.next_run = time.time()
        self.current_sources = []
        self.last_finished = None
        self.last_interval = self.store.settings()["interval_minutes"]

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="hirewatch-scheduler", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def state(self):
        return {"running": self.lock.locked(), "current_sources": self.current_sources[:],
                "next_run": (datetime.fromisoformat(now()) + timedelta(seconds=max(0, self.next_run-time.time()))).isoformat(timespec="seconds"),
                "last_finished": self.last_finished}

    def trigger(self):
        # Reserve synchronously so a double click cannot queue multiple scans.
        if not self.lock.acquire(blocking=False):
            return False
        threading.Thread(target=self._scan_reserved, name="hirewatch-manual", daemon=True).start()
        return True

    def _scan_reserved(self):
        try:
            sources = self.store.settings()["enabled_sources"]
            self.current_sources = sources[:]
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {pool.submit(collect, source): source for source in sources}
                for future in as_completed(futures):
                    source = futures[future]
                    try:
                        result = future.result()
                        self.store.snapshot(source, result.jobs, allow_empty=result.empty_verified,coverage=result.coverage)
                    except Exception as exc:
                        # Collector errors are sanitized and contain no configuration credentials.
                        message = str(exc) if isinstance(exc, ValueError) else f"采集失败（{type(exc).__name__}），请核对官网"
                        self.store.source_error(source, message)
                    finally:
                        self.current_sources.remove(source)
            self.last_finished = now()
            self.notifier.dispatch()
        except Exception:
            log.exception("监控循环异常")
        finally:
            self.next_run = time.time() + self.store.settings()["interval_minutes"] * 60
            self.current_sources = []
            self.lock.release()

    def _loop(self):
        while not self.stop_event.is_set():
            interval = self.store.settings()["interval_minutes"]
            if interval != self.last_interval:
                self.next_run = min(self.next_run, time.time() + interval * 60)
                self.last_interval = interval
            if time.time() >= self.next_run and self.lock.acquire(blocking=False):
                self._scan_reserved()
            else:
                try:
                    self.notifier.dispatch()
                except Exception:
                    log.exception("通知队列处理异常")
            self.stop_event.wait(5)
