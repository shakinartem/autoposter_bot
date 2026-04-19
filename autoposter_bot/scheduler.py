from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from autoposter_bot.db import Database
from autoposter_bot.loader import discover_jobs, load_job
from autoposter_bot.service import AutoposterService


def process_queue(service: AutoposterService, queue_dir: Path, dry_run: bool = False) -> list[str]:
    processed: list[str] = []
    for job_path in discover_jobs(queue_dir):
        job = load_job(job_path)
        if job.scheduled_at and job.scheduled_at > datetime.now(job.scheduled_at.tzinfo):
            continue

        results = service.publish_job(job, dry_run=dry_run)
        if all(result.ok for result in results):
            processed.append(job.post_id)
            if not dry_run:
                done_path = queue_dir / "processed"
                done_path.mkdir(parents=True, exist_ok=True)
                job_path.replace(done_path / job_path.name)
    return processed


def process_due_db_jobs(service: AutoposterService, db: Database, dry_run: bool = False) -> list[str]:
    processed: list[str] = []
    for job in db.get_due_jobs(datetime.now()):
        results = service.publish_job(job, dry_run=dry_run)
        if job_id := job.metadata.get("job_id"):
            if all(result.ok for result in results):
                processed.append(job.post_id)
                if not dry_run:
                    db.set_job_status(int(job_id), "published")
            elif not dry_run:
                db.set_job_status(int(job_id), "failed")
    return processed


def run_polling_loop(
    service: AutoposterService,
    db: Database,
    interval_seconds: int,
    dry_run: bool = False,
) -> None:
    while True:
        process_due_db_jobs(service, db, dry_run=dry_run)
        time.sleep(interval_seconds)
