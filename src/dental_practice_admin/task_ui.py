"""Staff controls for local drafts, approved tasks, reviews and schedules."""
from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, HTTPException, Request
from jsonschema import ValidationError as SchemaError

from dental_practice_admin import saved_scripts, schedules, scripts, task_files
from dental_practice_admin.audit import Audit
from dental_practice_admin.auth import CurrentStaff
from dental_practice_admin.principle import PrincipleClient, PrincipleError

router = APIRouter()
DAYS = {"mon-fri": "Weekdays", "*": "Every day", "mon": "Monday", "tue": "Tuesday",
        "wed": "Wednesday", "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}


@router.get("/")
@router.get("/tasks/manage")
def manage(request: Request, staff: CurrentStaff) -> Any:
    """Show practice reports, scripts, recent results and automatic runs."""
    from dental_practice_admin.app import TEMPLATES
    settings = request.app.state.settings
    task_files.cleanup(settings)
    checked = schedules.launcher_checked(settings)
    launcher_recent = schedules.launcher_recent(checked)
    installed = []
    for pointer in (settings.data_dir / "installed").glob("*/current"):
        revision = pointer.read_text(encoding="utf-8")
        definition, _ = task_files.installed(settings, pointer.parent.name, revision)
        installed.append({**definition.model_dump(), "revision": revision, "reviewed": True})
    for path in (settings.data_dir / "saved").glob("*/*/task.json"):
        definition = task_files.Definition.model_validate_json(path.read_text(encoding="utf-8"))
        if any(task["reviewed"] and task["name"] == definition.name and
               (settings.data_dir / "installed" / task["name"] / task["revision"] / "source.txt"
                ).read_text(encoding="utf-8") == (path.parent / "source.txt").read_text(
                    encoding="utf-8")
               and task_files.Definition.model_validate(task) == definition for task in installed):
            continue
        review = path.parent / "review.json"
        installed.append({**definition.model_dump(), "revision": path.parent.name,
                          "reviewed": False, "review_url": json.loads(review.read_text(
                              encoding="utf-8"))["url"] if review.exists() else ""})
    titles = {task["name"]: task["title"] for task in installed}
    with schedules.open_schedules(settings) as scheduler:
        jobs = []
        for j in scheduler.get_jobs():
            interval = isinstance(j.trigger, IntervalTrigger)
            fields = {} if interval else {f.name: str(f) for f in j.trigger.fields}
            jobs.append({"id": j.id, "name": j.name, "title": titles[j.name],
                "inputs": j.args[1],
                "next": j.next_run_time.strftime("%a %d %b, %I:%M %p")
                    if j.next_run_time else "Paused",
                "minutes": int(j.trigger.interval.total_seconds() / 60) if interval else 0,
                "at": "16:00" if interval else
                    f"{int(fields['hour']):02}:{int(fields['minute']):02}",
                "weekdays": "*" if interval else fields["day_of_week"]})
    from dental_practice_admin.storage import Storage
    store = Storage(settings.database_path)
    recent = [run for run in store.latest_runs_by_task() if not run.task.startswith("draft:")]
    attention = schedules.attention(settings, store)
    interface_warnings = store.interface_warnings()
    store.close()
    return TEMPLATES.TemplateResponse(request, "tasks.html", {
        "staff": staff, "is_fake": settings.environment.value == "fake", "attention": attention,
        "interface_warnings": interface_warnings,
        "days": DAYS, "installed": installed, "jobs": jobs, "recent": recent, "titles": titles,
        "launcher_recent": launcher_recent, "launcher_checked": checked})


@router.get("/tasks/practitioners")
async def practitioners(request: Request, staff: CurrentStaff) -> list[str]:
    """The practice's practitioners by name, for a report input with `format: practitioner`."""
    try:
        async with PrincipleClient(request.app.state.settings) as client:
            rows = [row async for row in client.rows("listPractitioners")]
    except (PrincipleError, httpx.HTTPError) as error:
        raise HTTPException(502, "Principle's list of practitioners could not be read") from error
    return sorted(str(row["name"]) for row in rows)


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
        if action == "submit":
            if payload.get("checked_for_patient_data") is not True:
                raise ValueError("Confirm the script contains no patient data")
            return {"review_url": await asyncio.to_thread(saved_scripts.publish_review, settings,
                payload["name"], payload["revision"], True, staff.email)}
        if action == "review":
            return {"url": await asyncio.to_thread(task_files.publish, settings,
                     task_files.Review.model_validate(payload), staff.email)}
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
    from contextlib import closing

    from dental_practice_admin.storage import Storage
    with closing(Storage(settings.database_path)) as store:
        row = store.db.execute("SELECT run_id FROM task_runs WHERE task=? "
            "ORDER BY started_at DESC LIMIT 1", ("draft:" + identifier,)).fetchone()
        latest = store.run(row[0]) if row else None
        attention = schedules.attention(settings, store)
    return TEMPLATES.TemplateResponse(request, "prepare.html", {
        "staff": staff, "is_fake": settings.environment.value == "fake", "candidate": data,
        "attention": attention,
        "identifier": identifier, "latest": latest,
        "tested": saved_scripts.tested(settings, identifier, script)})
