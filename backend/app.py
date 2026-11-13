"""Same-origin local API and the built frontend."""
import hashlib
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.bundle import ACCEPTED_FILES
from backend.exports import csv_export, structures_export
from backend.importer import Importer
from backend.literature import LiteratureStore, normalize_reactions
from backend.store import Store

ROOT = Path(__file__).resolve().parents[1]
INSTANCE_ID = hashlib.sha256(os.path.normcase(str(ROOT)).encode('utf-8')).hexdigest()


class StructureExport(BaseModel):
    ids: list[str] = Field(min_length=0, max_length=10000)


def filters(elements: str = "", match: str = "exact", eta_min: float | None = None, eta_max: float | None = None,
            sort: str = "eta", order: str = "asc", offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=500), method_id: str | None = None):
    if any(v is not None and not math.isfinite(v) for v in (eta_min, eta_max)):
        raise HTTPException(400, "过电位筛选必须是有限数值")
    return {"elements": [s.strip() for s in elements.split(",") if s.strip()], "match": match,
            "eta_min": eta_min, "eta_max": eta_max, "sort": sort, "order": order,
            "offset": offset, "limit": limit, "method_id": method_id or None}


def create_app(db_path=None, staging_root=None):
    store = Store(db_path or ROOT / "data" / "oer.sqlite")
    staging = Path(staging_root or ROOT / "data" / "staging")
    importer = Importer(store, staging)
    literature = LiteratureStore(store.path.parent / "literature.sqlite")
    app = FastAPI(title="OER 构型工作台", docs_url=None, redoc_url=None)
    app.state.store, app.state.importer = store, importer
    app.state.literature = literature
    allowed_hosts = [h.strip() for h in os.environ.get(
        "MATERIALS_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver"
    ).split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def local_requests(request, call_next):
        if os.environ.get("MATERIALS_READ_ONLY") == "1" and request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/imports"):
            return JSONResponse({"detail": "公开站点为只读，已关闭导入。"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            parsed = urlparse(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请在本机工具页面中操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": str(exc.args[0])}, status_code=404)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "application": "materials-silane-library",
            "version": "1.0.0",
            "instance_id": INSTANCE_ID,
        }

    @app.get("/api/summary")
    def summary():
        return store.summary()

    @app.get("/api/configurations")
    def configurations(params: Annotated[dict, Depends(filters)]):
        return store.query(**params)

    @app.get("/api/configurations/{config_id}")
    def detail(config_id: str):
        return store.detail(config_id)

    @app.get("/api/configurations/{config_id}/structures/{state}")
    def structure(config_id: str, state: str):
        return store.structure(config_id, state)[0]

    @app.get("/api/configurations/{config_id}/structures/{state}/download")
    def download(config_id: str, state: str):
        _, raw = store.structure(config_id, state)
        return Response(raw, media_type="chemical/x-xyz", headers={"Content-Disposition": f'attachment; filename="{config_id}_{state}.extxyz"'})

    @app.get("/api/export/csv")
    def export_csv(params: Annotated[dict, Depends(filters)]):
        return Response(csv_export(store, params), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="OER_filtered_configurations.csv"'})

    @app.post("/api/export/structures")
    def export_structures(body: StructureExport):
        return Response(structures_export(store, body.ids), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="OER_four_state_structures.zip"'})

    @app.get("/api/batches")
    def batches():
        return {"items": store.batches()}

    @app.post("/api/imports/preview")
    def preview(files: Annotated[list[UploadFile], File()], batch_name: Annotated[str, Form()] = "新增完整四态数据"):
        # Store only the documented input files; never extract client-supplied paths.
        with tempfile.TemporaryDirectory(prefix="upload-", dir=staging) as directory:
            seen, total = set(), 0
            try:
                for upload in files:
                    name = (upload.filename or "").replace("\\", "/").split("/")[-1]
                    if name not in ACCEPTED_FILES:
                        continue
                    if name in seen:
                        raise ValueError(f"存在同名输入文件 {name}，请只选择一个数据批次")
                    seen.add(name)
                    size = 0
                    with (Path(directory) / name).open("wb") as out:
                        while chunk := upload.file.read(1024 * 1024):
                            size += len(chunk)
                            total += len(chunk)
                            cap = 2 * 1024**3 if name.endswith(".tar.gz") else 256 * 1024**2
                            if size > cap or total > 3 * 1024**3:
                                raise ValueError("上传批次过大，请分批导入")
                            out.write(chunk)
                return importer.preview(directory, batch_name)
            finally:
                for upload in files:
                    upload.file.close()

    @app.post("/api/imports/{token}/commit")
    def commit(token: str):
        return importer.commit(token)

    @app.get("/api/import-template")
    def template():
        path = ROOT / "templates" / "OER_import_template.zip"
        if not path.is_file():
            raise HTTPException(404, "导入模板尚未生成")
        return FileResponse(path, filename="OER_import_template.zip")

    def literature_filters(reaction: str = "OER", elements: str = "", match: str = "contains",
                           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                           q: str = Query("", max_length=200), completeness: str = "all",
                           status: str = "all", electrolyte: str = "", source_id: str = "",
                           eta_min: float | None = None, eta_max: float | None = None,
                           exp_eta_min: float | None = None, exp_eta_max: float | None = None,
                           pH_min: float | None = None, pH_max: float | None = None,
                           exclude_elements: str = "", exp_j: float | None = None, data_class: str = ""):
        if any(v is not None and not math.isfinite(v) for v in (eta_min, eta_max, exp_eta_min, exp_eta_max, pH_min, pH_max, exp_j)):
            raise HTTPException(400, "过电位或pH筛选必须是有限数值")
        return {"reaction": normalize_reactions(reaction), "elements": [s.strip() for s in elements.split(",") if s.strip()],
                "exclude_elements": [s.strip() for s in exclude_elements.split(",") if s.strip()],
                "match": match, "offset": offset, "limit": limit, "q": q, "completeness": completeness,
                "status": status, "electrolyte": electrolyte, "source_id": source_id,
                "eta_min": eta_min, "eta_max": eta_max, "exp_eta_min": exp_eta_min, "exp_eta_max": exp_eta_max,
                "pH_min": pH_min, "pH_max": pH_max, "exp_j": exp_j, "data_class": data_class}

    @app.get("/api/literature/summary")
    def public_summary(reaction: str = "OER"):
        return literature.summary(normalize_reactions(reaction))

    @app.get("/api/literature/materials")
    def public_materials(params: Annotated[dict, Depends(literature_filters)]):
        return literature.query(**params)

    @app.get("/api/literature/materials/{material_id}")
    def public_detail(material_id: str):
        return literature.detail(material_id)

    @app.get("/api/literature/materials/{material_id}/structures/{state}")
    def public_structure(material_id: str, state: str):
        return literature.structure(material_id, state)[0]

    @app.get("/api/literature/materials/{material_id}/structures/{state}/download")
    def public_download(material_id: str, state: str):
        _, raw, fmt = literature.structure(material_id, state)
        return Response(raw, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{material_id}_{state}.{fmt}"'})

    @app.get("/api/literature/export/csv")
    def public_csv(params: Annotated[dict, Depends(literature_filters)]):
        return Response(literature.csv_export(**params), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="published_materials.csv"'})

    from backend.glass import register
    register(app)
    from backend.oer_search import register as register_oer
    register_oer(app, store, literature)
    from backend.auth import install as install_auth
    install_auth(app)
    frontend = ROOT / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    else:
        @app.get("/")
        def building():
            return JSONResponse({"message": "页面尚未构建，请先完成本机安装。"}, status_code=503)
    return app
