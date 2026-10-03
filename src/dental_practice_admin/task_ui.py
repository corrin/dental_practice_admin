"""Staff controls for local drafts, approved tasks, reviews and schedules."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, HTTPException, Request
from jsonschema import ValidationError as SchemaError

from dental_practice_admin import saved_scripts, schedules, scripts, task_files
from dental_practice_admin.audit import Audit
from dental_practice_admin.auth import CurrentStaff

router = APIRouter()
DAYS = {"mon-fri": "Weekdays", "*": "Every day", "mon": "Monday", "tue": "Tuesday",
        "wed": "Wednesday", "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}


@router.get("/tasks/manage")
def manage(request: Request, staff: CurrentStaff) -> Any:
    """Show practice reports, scripts, recent results and automatic runs."""
    from dental_practice_admin.app import TEMPLATES
    settings = request.app.state.settings
    task_files.cleanup(settings)
    heartbeat = settings.data_dir / "audits" / "launcher.jsonl"
    checked = datetime.fromtimestamp(heartbeat.stat().st_mtime, UTC) if heartbeat.exists() else None
    launcher_recent = checked and (
        datetime.now(UTC) - checked).total_seconds() <= 2 * schedules.POLL_SECONDS
    installed = []
    for path in (settings.data_dir / "installed").glob("*/*/task.json"):
        definition, _ = task_files.installed(settings, path.parent.parent.name, path.parent.name)
        installed.append({**definition.model_dump(), "revision": path.parent.name,
                          "reviewed": True})
    for path in (settings.data_dir / "saved").glob("*/*/task.json"):
        definition = task_files.Definition.model_validate_json(path.read_text(encoding="utf-8"))
        if any(task["reviewed"] and task["name"] == definition.name and
               (settings.data_dir / "installed" / task["name"] / task["revision"] / "source.txt"
                ).read_bytes() == (path.parent / "source.txt").read_bytes()
               and task_files.Definition.model_validate(task) == definition for task in installed):
            continue
        installed.append({**definition.model_dump(), "revision": path.parent.name,
                          "reviewed": False,
                          "requested": (path.parent / "review-request.json").exists()})
    titles = {task["name"]: task["title"] for task in installed}
    with schedules.open_schedules(settings) as scheduler:
        jobs = []
        for j in scheduler.get_jobs():
            interval = isinstance(j.trigger, IntervalTrigger)
            fields = {} if interval else {f.name: str(f) for f in j.trigger.fields}
            jobs.append({"id": j.id, "name": j.name, "title": titles[j.name],
                "revision": j.args[1], "inputs": j.args[2],
                "next": j.next_run_time.strftime("%a %d %b, %I:%M %p")
                    if j.next_run_time else "Paused",
                "minutes": int(j.trigger.interval.total_seconds() / 60) if interval else 0,
                "at": "16:00" if interval else
                    f"{int(fields['hour']):02}:{int(fields['minute']):02}",
                "weekdays": "*" if interval else fields["day_of_week"]})
    from dental_practice_admin.storage import Storage
    store = Storage(settings.database_path)
    recent = [run for run in store.latest_runs_by_task() if not run.task.startswith("draft:")]
    store.close()
    return TEMPLATES.TemplateResponse(request, "tasks.html", {
        "staff": staff, "is_fake": settings.environment.value == "fake",
        "days": DAYS, "installed": installed, "jobs": jobs, "recent": recent, "titles": titles,
        "launcher_recent": launcher_recent, "launcher_checked": checked})


@router.post("/tasks/{action}")
async def act(action: str, payload: dict[str, Any], request: Request, staff: CurrentStaff) -> Any:
    """JSON-only authenticated actions; schedule mutation never changes approved code."""
    import asyncio
    settings = request.app.state.settings
    try:
        if action in {"save", "test"}:
            script, data = saved_scripts.candidate(settings, payload["draft_id"], staff.email)
            if action == "save":
                saved_scripts.save(settings, payload["draft_id"], staff.email, payload["title"])
                return {"url": "/tasks/manage"}
            from jsonschema import FormatChecker, validate
            validate(payload["inputs"], data["definition"]["inputs"],
                     format_checker=FormatChecker())
            script.inputs = payload["inputs"]
            await scripts.run(settings, script, "draft:" + payload["draft_id"])
            return {"url": "/tasks/prepare/" + payload["draft_id"]}
        if action == "request-review":
            import json
            path = saved_scripts.folder(settings, payload["name"], payload["revision"])
            (path / "review-request.json").write_text(json.dumps({"initiator": staff.email,
                "at": datetime.now(UTC).isoformat()}), encoding="utf-8")
            return {"saved": True}
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
            script = saved_scripts.load(settings, payload["name"], payload["revision"],
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


@router.get("/tasks/prepare/{identifier}")
def prepare_page(identifier: str, request: Request, staff: CurrentStaff) -> Any:
    """Show the named candidate and explicit testing and saving actions."""
    from dental_practice_admin.app import TEMPLATES
    settings = request.app.state.settings
    try:
        script, data = saved_scripts.candidate(settings, identifier, staff.email)
    except FileNotFoundError as error:
        raise HTTPException(404) from error
    return TEMPLATES.TemplateResponse(request, "prepare.html", {
        "staff": staff, "is_fake": settings.environment.value == "fake", "candidate": data,
        "identifier": identifier, "tested": saved_scripts.tested(settings, identifier, script)})
