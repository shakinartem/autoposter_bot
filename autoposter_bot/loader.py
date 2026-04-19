from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from autoposter_bot.models import MediaItem, PostJob, Target


def load_job(path: str | Path) -> PostJob:
    job_path = Path(path)
    payload = json.loads(job_path.read_text(encoding="utf-8-sig"))
    media_items_payload = payload.get("media_items")
    if media_items_payload:
        media_items = [
            MediaItem(
                source=item.get("source") or item.get("path"),
                media_type=item.get("media_type", "auto"),
                order_index=int(item.get("order_index", index)),
                options=item.get("options", {}),
            )
            for index, item in enumerate(media_items_payload)
        ]
    else:
        media_path = payload.get("media_path")
        media_items = [MediaItem(source=media_path, media_type="auto")] if media_path else []

    scheduled_at = payload.get("scheduled_at")
    return PostJob(
        post_id=payload["post_id"],
        content_type=payload.get("content_type", "generic"),
        text=payload["text"],
        media_items=media_items,
        scheduled_at=datetime.fromisoformat(scheduled_at) if scheduled_at else None,
        targets=[
            Target(
                platform=item["platform"],
                destination=item.get("destination"),
                account_name=item.get("account_name"),
                options=item.get("options", {}),
            )
            for item in payload["targets"]
        ],
        metadata=payload.get("metadata", {}),
    )


def discover_jobs(queue_dir: str | Path) -> list[Path]:
    root = Path(queue_dir)
    if not root.exists():
        return []
    return sorted(root.glob("*.json"))
