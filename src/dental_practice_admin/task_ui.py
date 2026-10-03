"""Staff controls for local drafts, approved tasks, reviews and schedules."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, HTTPException, Request
from jsonschema import ValidationError as SchemaError

from dental_practice_admin import schedules, scripts, task_files
from dental_practice_admin.audit import Audit
from dental_practice_admin.auth import CurrentStaff

router = APIRouter()


@router.get("/tasks/manage")
def manage(request: Request, staff: CurrentStaff) -> Any:
    """Show only the caller's drafts and practice-wide installed tasks and schedules."""
    from dental_practice_admin.app import TEMPLATES
    settings = request.app.state.settings
    task_files.cleanup(settings)
    heartbeat = settings.data_dir / "audits" / "launcher.jsonl"
    checked = datetime.fromtimestamp(heartbeat.stat().st_mtime, UTC) if heartbeat.exists() else None
    recent = checked and (datetime.now(UTC) - checked).total_seconds() <= 2 * schedules.POLL_SECONDS
    drafts = {}
    paths = (settings.data_dir / "drafts").glob("*.json")
    for path in sorted(paths, key=lambda p: p.stat().st_mtime):
        draft = scripts.Script.model_validate_json(path.read_text(encoding="utf-8"))
        if not draft.task_id or draft.owner != staff.email:
            continue
        folder = task_files.folder_for(settings, draft.task_id)
        if not (folder / "expired").exists() and not (folder / "installed").exists():
            review = folder / "review.json"
            drafts[draft.task_id] = {"id": path.stem, **draft.model_dump(),
                "review": json.loads(review.read_text()) if review.exists() else None}
    installed = []
    for path in (settings.data_dir / "installed").glob("*/*/task.json"):
        definition, _ = task_files.installed(settings, path.parent.parent.name, path.parent.name)
        installed.append({**definition.model_dump(), "revision": path.parent.name})
    with schedules.open_schedules(settings) as scheduler:
        jobs = []
        for j in scheduler.get_jobs():
            interval = isinstance(j.trigger, IntervalTrigger)
            fields = {} if interval else {f.name: str(f) for f in j.trigger.fields}
            jobs.append({"id": j.id, "name": j.name, "revision": j.args[1], "inputs": j.args[2],
                "timing": str(j.trigger), "next": str(j.next_run_time or "Paused"),
                "minutes": int(j.trigger.interval.total_seconds() / 60) if interval else 0,
                "at": "16:00" if interval else
                    f"{int(fields['hour']):02}:{int(fields['minute']):02}",
                "weekdays": "*" if interval else fields["day_of_week"]})
    return TEMPLATES.TemplateResponse(request, "tasks.html", {
        "staff": staff, "is_fake": settings.environment.value == "fake",
        "drafts": list(drafts.values()), "installed": installed, "jobs": jobs,
        "launcher_recent": recent, "launcher_checked": checked})


@router.post("/tasks/{action}")
async def act(action: str, payload: dict[str, Any], request: Request, staff: CurrentStaff) -> Any:
    """JSON-only authenticated actions; schedule mutation never changes approved code."""
    import asyncio
    settings = request.app.state.settings
    try:
        if action == "review":
            return {"url": await asyncio.to_thread(task_files.publish, settings,
                     task_files.Review.model_validate(payload), staff.email)}
        if action == "install-existing":
            return {"revision": await asyncio.to_thread(task_files.install_existing, settings,
                                                         payload["name"], int(payload["number"]))}
        if action == "install":
            return {"revision": await asyncio.to_thread(task_files.install, settings,
                                                         payload["task_id"], staff.email)}
        if action == "run":
            script = task_files.load(settings, payload["name"], payload["revision"],
                                     payload["inputs"], staff.email)
            return {"url": "/runs/" + await scripts.run(settings, script, payload["name"])}
        if action == "schedule":
            schedules.save(settings, schedules.Schedule.model_validate(payload), staff.email)
        elif action in {"pause", "resume", "remove"}:
            with schedules.open_schedules(settings) as scheduler:
                Audit(settings, "schedules").write(action, initiator=staff.email, id=payload["id"])
                getattr(scheduler, action + "_job")(payload["id"])
        else:
            raise HTTPException(404)
        return {"saved": True}
    except SchemaError as error:
        raise HTTPException(422, "Inputs do not match this task's input contract") from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except (KeyError, FileNotFoundError) as error:
        raise HTTPException(404, "Task or review unavailable") from error
