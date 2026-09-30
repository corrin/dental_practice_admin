"""Staff pages: configured tasks, recent runs, and one run's result.

Deliberately absent: a next-run time. Windows Task Scheduler owns the schedule, and a time
computed here would be a second schedule definition that drifts from the real one. The pages
show task identity and run history, which is what they can honestly read.

A storage connection is opened per request. These endpoints are sync, so FastAPI serves each
one on a threadpool thread, and a SQLite connection may only be used from the thread that
created it -- a connection shared across requests raises as soon as two threads are involved.
Opening one per request costs nothing next to a page render.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from principle_admin.config import Environment, Settings
from principle_admin.storage import Storage, TaskRun

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# What the task list shows. A task exists here when a command implements it; the schedule
# that fires it lives in Windows.
CONFIGURED_TASKS = (
    {
        "name": "daily_diary",
        "title": "Daily diary",
        "command": "principle-admin diary",
        "description": "Tomorrow's appointments grouped by practitioner.",
    },
)


def settings() -> Settings:
    """Resolved configuration, read once per request so a restart is not needed to change it."""
    return Settings()


def storage(
    configured: Annotated[Settings, Depends(settings)],
) -> Iterator[Storage]:
    """A connection for one request, opened on the thread that will use it."""
    store = Storage(configured.database_path)
    try:
        yield store
    finally:
        store.close()


app = FastAPI(title="Principle admin")


@app.get("/health")
def health(configured: Annotated[Settings, Depends(settings)]) -> dict[str, object]:
    """Readiness for deploy/verify.ps1: which Principle, and is the database writable."""
    store = Storage(configured.database_path)
    runs = len(store.recent_runs(limit=1))
    store.close()
    return {
        "status": "ok",
        "principle": configured.environment.value,
        "database": str(configured.database_path),
        "hasRuns": bool(runs),
    }


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    configured: Annotated[Settings, Depends(settings)],
    store: Annotated[Storage, Depends(storage)],
) -> HTMLResponse:
    """The configured tasks and the most recent runs."""
    return TEMPLATES.TemplateResponse(
        request,
        "runs.html",
        {
            "tasks": CONFIGURED_TASKS,
            "runs": store.recent_runs(),
            "environment": configured.environment,
            "is_fake": configured.environment is Environment.FAKE,
        },
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(
    request: Request,
    run_id: str,
    configured: Annotated[Settings, Depends(settings)],
    store: Annotated[Storage, Depends(storage)],
) -> HTMLResponse:
    """One run's result, with its coverage stated rather than implied."""
    run: TaskRun | None = store.run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id}")
    return TEMPLATES.TemplateResponse(
        request,
        "run.html",
        {
            "run": run,
            "environment": configured.environment,
            "is_fake": configured.environment is Environment.FAKE,
        },
    )
