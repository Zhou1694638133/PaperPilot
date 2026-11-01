"""Run separately from FastAPI: python -m app.worker (global concurrency: one)."""
from __future__ import annotations

import argparse
from contextlib import closing
import logging
import signal
import threading
import uuid

from . import jobs, llm
from .repository import init_db
from .supervisor import execute

logger = logging.getLogger("paperpilot.worker")
SCHEDULE_INTERVAL = 30


def schedule_once() -> None:
    from .subscriptions import enqueue_due
    try:
        enqueue_due()
    except Exception as exc:
        logger.warning("Subscription scheduler failed (%s)", type(exc).__name__)


def schedule_loop(stop: threading.Event) -> None:
    # Only enqueue here. The existing global worker slot still executes one task.
    while not stop.is_set():
        schedule_once()
        stop.wait(SCHEDULE_INTERVAL)


def process_one(owner: str) -> bool:
    record = jobs.claim_job(owner)
    if record is None:
        return False
    job_id = record["id"]
    try:
        result, checkpoint_id = execute(job_id, owner)
        jobs.finish_job(job_id, owner, result, checkpoint_id)
    except jobs.Cancelled:
        jobs.fail_job(job_id, owner, "用户已取消；远端已发送请求无法撤回，已保存检查点保留。", cancelled=True)
    except jobs.LeaseLost:
        logger.warning("Task lease lost: %s", job_id)
    except llm.ModelError as exc:
        jobs.fail_job(job_id, owner, str(exc), ambiguous=exc.ambiguous)
    except ValueError as exc:
        jobs.fail_job(job_id, owner, str(exc))
    except Exception as exc:
        # Never log provider responses, credentials or full prompt data.
        logger.error("Task %s failed (%s)", job_id, type(exc).__name__)
        jobs.fail_job(job_id, owner, f"任务内部错误（{type(exc).__name__}），请检查服务配置与依赖；已保存检查点保留。")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="PaperPilot durable worker")
    parser.add_argument("--once", action="store_true", help="Process one queued job and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    jobs.init_jobs()
    owner = "worker-" + str(uuid.uuid4())
    stop = threading.Event()
    pulse_stop = threading.Event()

    def stop_signal(signum, frame):
        logger.info("Stopping after the current step; checkpoints are retained.")
        stop.set()

    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)

    def pulse():
        while not pulse_stop.is_set():
            try:
                jobs.heartbeat(owner)
            except Exception as exc:
                logger.warning("Heartbeat failed (%s)", type(exc).__name__)
            pulse_stop.wait(5)

    thread = threading.Thread(target=pulse, name="paperpilot-heartbeat", daemon=True)
    thread.start()
    logger.info("Worker ready; persistent queue, global concurrency = 1")
    scheduler = None
    if args.once:
        schedule_once()
    else:
        scheduler = threading.Thread(target=schedule_loop, args=(pulse_stop,), name="paperpilot-scheduler", daemon=True)
        scheduler.start()
    try:
        while not stop.is_set():
            processed = process_one(owner)
            if args.once:
                break
            if not processed:
                stop.wait(1)
    finally:
        stop.set()
        pulse_stop.set()
        thread.join(timeout=6)
        if scheduler is not None:
            scheduler.join(timeout=6)
        with closing(jobs.connect()) as conn, conn:
            conn.execute("DELETE FROM worker_heartbeats WHERE id=?", (owner,))


if __name__ == "__main__":
    main()


