"""Overrated local app: address search, property verdict, appeal pipeline, case tracker, report.

Run: .venv/bin/uvicorn demo.server:app --host 127.0.0.1 --port 8100
(start it with `env -u ANTHROPIC_API_KEY -u ANTHROPIC_WORKSPACE_ID` so Claude calls use the logged-in profile)

Local only. VOA data is licensed for rating purposes only, so this app binds to 127.0.0.1 and also
refuses any request whose Host is not local or that arrives through a proxy or tunnel (ngrok sets
X-Forwarded-For). Supabase and data-service keys stay in this process; the page only sees /api JSON.
"""
import logging
import os
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from demo import pipeline as P
from demo import report

log = logging.getLogger("overrated.demo")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
UARN_RE = re.compile(r"^\d{1,15}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

app = FastAPI(title="Overrated", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.headers.get("host", "").lower()
    name = host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]
    proxied = any(h in request.headers for h in ("x-forwarded-for", "x-forwarded-host", "forwarded"))
    if name not in LOCAL_HOSTS or proxied:
        return JSONResponse({"detail": "This app only serves local requests."}, status_code=403)
    return await call_next(request)


@app.exception_handler(P.DemoError)
async def demo_error(request: Request, exc: P.DemoError):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    parts = []
    for e in exc.errors()[:3]:
        where = ".".join(str(x) for x in e.get("loc", ())[1:]) or "request"
        parts.append(f"{where}: {e.get('msg')}")
    return JSONResponse({"detail": "; ".join(parts) or "Invalid request."}, status_code=400)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": exc.detail if isinstance(exc.detail, str) else "Error."},
                        status_code=exc.status_code)


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong on our side."}, status_code=500)


def check_uarn(uarn) -> str:
    uarn = str(uarn).strip()
    if not UARN_RE.match(uarn):
        raise P.BadRequest("uarn must be digits.")
    return str(int(uarn))


def check_uuid(value, what) -> str:
    value = str(value).strip().lower()
    if not UUID_RE.match(value):
        raise P.BadRequest(f"{what} must be a UUID.")
    return value


# --- pages ---------------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    path = os.path.join(STATIC, "index.html")
    if not os.path.exists(path):
        raise P.NotFound("demo/static/index.html is missing.")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/report/{case_id}", response_class=HTMLResponse)
def report_page(case_id: str):
    return HTMLResponse(report.render(check_uuid(case_id, "case_id")))


# --- API ----------------------------------------------------------------------------------
@app.get("/api/search")
def api_search(q: str = ""):
    return P.search(q[:200])


@app.get("/api/property/{uarn}")
def api_property(uarn: str):
    return P.property_view(check_uarn(uarn))


class AppealBody(BaseModel):
    uarn: str | int


@app.post("/api/appeal")
def api_appeal(body: AppealBody):
    kind, value = P.start_appeal(check_uarn(body.uarn))
    if kind == "case":
        return JSONResponse({"case_id": value}, status_code=200)
    return JSONResponse({"job_id": value.job_id, "candidate_id": value.candidate_id}, status_code=202)


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    return P.get_job(check_uuid(job_id, "job_id"))


@app.get("/api/case/{case_id}")
def api_case(case_id: str):
    return P.case_view(check_uuid(case_id, "case_id"))


@app.post("/api/case/{case_id}/approve")
def api_approve(case_id: str):
    return P.approve(check_uuid(case_id, "case_id"))


class SimulateBody(BaseModel):
    reply: str


@app.post("/api/case/{case_id}/simulate")
def api_simulate(case_id: str, body: SimulateBody):
    """MOCK ONLY: pretend the VOA replied. Nothing is sent to or received from the VOA."""
    return P.simulate(check_uuid(case_id, "case_id"), body.reply)


@app.get("/api/events")
def api_events(case_id: str = "", candidate_id: str = "", limit: int = 50):
    limit = max(1, min(int(limit), 200))
    return P.list_events(case_id=check_uuid(case_id, "case_id") if case_id else None,
                         candidate_id=check_uuid(candidate_id, "candidate_id") if candidate_id else None,
                         limit=limit)


@app.get("/api/area")
def api_area():
    return P.area()


app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
