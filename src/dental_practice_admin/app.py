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

import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated

from chatkit.server import StreamingResult
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from dental_practice_admin.auth import AccessControl, CurrentStaff, build_oauth
from dental_practice_admin.auth import router as auth_router
from dental_practice_admin.chat import ChatDeps, StaffChatServer, model_for
from dental_practice_admin.chat_store import SqliteChatStore
from dental_practice_admin.config import Environment, Settings, SignIn, current_settings
from dental_practice_admin.storage import Storage, TaskRun

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# A signed-in session lasts a working day. Long enough that nobody signs in twice during a shift,
# short enough that a laptop left at home is not signed in next week.
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60

# What the task list shows. A task exists here when a command implements it; the schedule
# that fires it lives in Windows.
CONFIGURED_TASKS = (
    {
        "name": "daily_diary",
        "title": "Daily diary",
        "command": "dental-practice-admin diary",
        "description": "Tomorrow's appointments grouped by practitioner.",
    },
)


settings = current_settings


def storage(
    configured: Annotated[Settings, Depends(settings)],
) -> Iterator[Storage]:
    """A connection for one request, opened on the thread that will use it."""
    store = Storage(configured.database_path)
    try:
        yield store
    finally:
        store.close()


router = APIRouter()


def create_app(configured: Settings | None = None) -> FastAPI:
    """Resolve one configuration and protect all routes before accepting requests."""
    configured = configured if configured is not None else Settings()
    configured.require_web_configured()
    app = FastAPI(title="Massey Smiles Admin")
    app.state.settings = configured
    app.add_middleware(AccessControl, settings=configured)
    if configured.sign_in is SignIn.GOOGLE:
        app.state.oauth = build_oauth(configured)
        app.add_middleware(
            SessionMiddleware,
            secret_key=configured.session_secret.get_secret_value(),
            https_only=True,
            same_site="lax",
            max_age=SESSION_MAX_AGE_SECONDS,
        )
    else:
        logging.getLogger(__name__).warning(
            "GOOGLE LOGIN DISABLED: anyone reaching this application has developer access"
        )
    app.include_router(auth_router)
    app.include_router(router)
    return app


@router.get("/health")
def health(
    request: Request, configured: Annotated[Settings, Depends(settings)]
) -> dict[str, object]:
    """Readiness for scripts/verify.ps1.

    `baseUrl` is the origin this process believes staff reach it on. Behind Caddy it must be the
    public hostname; if it reports the socket, uvicorn is not honouring the proxy headers and the
    Google redirect will be built wrong.
    """
    store = Storage(configured.database_path)
    store.close()
    return {
        "status": "ok",
        "principle": configured.environment.value,
        "baseUrl": configured.public_origin(request),
    }


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    staff: CurrentStaff,
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
            "staff": staff,
            "environment": configured.environment,
            "is_fake": configured.environment is Environment.FAKE,
        },
    )


@router.get("/chat", response_class=HTMLResponse)
def chat_page(
    request: Request,
    staff: CurrentStaff,
    configured: Annotated[Settings, Depends(settings)],
) -> HTMLResponse:
    """The page hosting the ChatKit web component."""
    return TEMPLATES.TemplateResponse(
        request,
        "chat.html",
        {
            "staff": staff,
            "chatkit_domain_key": configured.chatkit_domain_key,
            "environment": configured.environment,
            "is_fake": configured.environment is Environment.FAKE,
        },
    )


@router.post("/chatkit")
async def chatkit(
    request: Request,
    staff: CurrentStaff,
    configured: Annotated[Settings, Depends(settings)],
) -> Response:
    """The ChatKit protocol endpoint. The signed-in staff member is the whole request context.

    The store is opened here rather than by a dependency, for two reasons that both showed up as
    a hung request:

      * This handler is async, so the connection is created on the event loop thread -- the same
        thread that later iterates the response body. A SQLite connection may only be used from
        the thread that made it, and a sync dependency would have made it on a threadpool thread.
      * A dependency's teardown runs when the handler returns, which for a streaming response is
        *before* the body has been sent. The connection has to outlive the handler and close when
        the stream does.

    Streaming also needs buffering disabled explicitly: a reverse proxy that holds SSE until the
    response completes turns a live conversation into one silent pause and then a wall of text.
    """
    store = SqliteChatStore(configured.database_path)
    server = StaffChatServer(store, ChatDeps(settings=configured, model=model_for(configured)))
    try:
        result = await server.process(await request.body(), staff)
    except Exception:
        store.close()
        raise

    if isinstance(result, StreamingResult):
        return StreamingResponse(
            _closing(result, store),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Content-Encoding": "identity",
            },
        )
    store.close()
    return Response(content=result.json, media_type="application/json")


async def _closing(result: StreamingResult, store: SqliteChatStore) -> AsyncIterator[str]:
    """Stream the result, then close the store -- including when the client disconnects."""
    try:
        async for event in result:
            yield event
    finally:
        store.close()


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(
    request: Request,
    run_id: str,
    staff: CurrentStaff,
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
            "staff": staff,
            "environment": configured.environment,
            "is_fake": configured.environment is Environment.FAKE,
        },
    )
