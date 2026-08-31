"""FastAPI app: job create/list/download over a thin REST API + a Web UI page.

Jobs are simple in-memory records tracked in a thread-safe dict. The API is
intentionally minimal: create a job, list jobs, get/delete by id, download
the resulting CSV. Auto OpenAPI docs at /docs.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..config import AppConfig


class JobCreate(BaseModel):
    client_name: str = "campaign"
    queries: list[str]


_JOB_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}


def _job_record(job_id: str, client_name: str, queries: list[str]) -> dict:
    return {
        "id": job_id,
        "client_name": client_name,
        "queries": queries,
        "status": "pending",
        "created_at": time.time(),
    }


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="Advance B2B GMS", version="1.0.0")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _WEB_UI_HTML

    @app.post("/api/v1/jobs")
    def create_job(body: JobCreate):
        job_id = uuid.uuid4().hex[:12]
        with _JOB_LOCK:
            _JOBS[job_id] = _job_record(job_id, body.client_name, body.queries)
        return _JOBS[job_id]

    @app.get("/api/v1/jobs")
    def list_jobs():
        with _JOB_LOCK:
            return list(_JOBS.values())

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str):
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.delete("/api/v1/jobs/{job_id}")
    def delete_job(job_id: str):
        with _JOB_LOCK:
            job = _JOBS.pop(job_id, None)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {"deleted": job_id}

    @app.get("/api/v1/jobs/{job_id}/download")
    def download_job(job_id: str):
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        out = Path(config.job.output_dir) / job["client_name"] / "leads.csv"
        if not out.exists():
            raise HTTPException(status_code=404, detail="output not ready")
        return FileResponse(str(out), filename=f"{job['client_name']}-leads.csv")

    return app


_WEB_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Advance B2B GMS</title>
<style>
  body{font-family:system-ui,sans-serif;margin:2rem;color:#111}
  h1{font-size:1.5rem} input,button{padding:.5rem;margin:.25rem 0}
  #jobs{list-style:none;padding:0} li{padding:.5rem;border-bottom:1px solid #eee}
</style>
</head>
<body>
<h1>Advance B2B GMS — Job Console</h1>
<form id="f">
  <input id="client" placeholder="client name" value="campaign">
  <input id="queries" placeholder="queries (comma-separated)" size="40">
  <button type="submit">Create Job</button>
</form>
<ul id="jobs"></ul>
<script>
async function refresh(){
  const r = await fetch('/api/v1/jobs');
  const jobs = await r.json();
  const el = document.getElementById('jobs');
  el.innerHTML = '';
  jobs.forEach(j => {
    const li = document.createElement('li');
    li.textContent = j.id + ' — ' + j.client_name + ' (' + j.status + ')';
    el.appendChild(li);
  });
}
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const client = document.getElementById('client').value;
  const queries = document.getElementById('queries').value.split(',').map(s=>s.trim()).filter(Boolean);
  await fetch('/api/v1/jobs', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({client_name: client, queries: queries})});
  refresh();
};
refresh();
</script>
</body>
</html>
"""
