from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from reporting import export_csv, export_xlsx

from .auth import (
    AuthConfig,
    AuthService,
    AuthUser,
    AuthenticationError,
    AuthenticationRateLimitError,
)
from .data_source import DataSourceUnavailableError
from .quota import DemoQuotaConfig, DemoQuotaExceededError, DemoQuotaService
from .run_manager import RunNotFoundError
from .service import AskDataApplicationService, RunAccessError, RunNotReadyError


BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = BASE_DIR / "web"
RUNTIME_DIR = Path(os.getenv("ASKDATA_RUNTIME_DIR", BASE_DIR / "runtime_data"))


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=128)
    enable_long_term: bool = False
    generate_chart: Optional[bool] = None


class DrilldownRequest(BaseModel):
    parent_run_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=4000)
    direction: Literal["down", "up"]
    generate_chart: Optional[bool] = None


class SaveAnalysisRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class CreateDashboardRequest(BaseModel):
    name: str = Field(default="我的仪表盘", max_length=80)
    description: str = Field(default="", max_length=300)


class AddDashboardCardRequest(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=120)


def create_app(
    service: Optional[AskDataApplicationService] = None,
    auth_service: Optional[AuthService] = None,
) -> FastAPI:
    app = FastAPI(title="AskData Agent API", version="0.9.0")
    app.state.askdata = service or AskDataApplicationService(runtime_dir=RUNTIME_DIR)
    app.state.auth = auth_service or AuthService(AuthConfig.from_environment(RUNTIME_DIR))
    app.state.quota = DemoQuotaService(DemoQuotaConfig.from_environment(RUNTIME_DIR))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in os.getenv(
            "ASKDATA_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
        ).split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.9.0"}

    def request_subject(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        connecting_ip = request.headers.get("cf-connecting-ip", "").strip()
        client_ip = request.client.host if request.client else "unknown"
        return connecting_ip or forwarded or client_ip

    def quota_status(request: Request) -> dict:
        return app.state.quota.status(
            subject=request_subject(request),
            tester_token=request.headers.get("x-askdata-test-token", ""),
        )

    def consume_quota(request: Request) -> dict:
        try:
            return app.state.quota.consume(
                subject=request_subject(request),
                tester_token=request.headers.get("x-askdata-test-token", ""),
            )
        except DemoQuotaExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc))

    def require_user(request: Request) -> AuthUser:
        token = request.cookies.get(app.state.auth.config.cookie_name, "")
        user = app.state.auth.get_user_for_token(token)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return user

    def require_owned_run(run_id: str, user: AuthUser):
        try:
            record = app.state.askdata.runs.get(run_id)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="运行记录不存在")
        if record.user_id != user.id:
            raise HTTPException(status_code=403, detail="无权访问该运行记录")
        return record

    def require_admin(user: AuthUser = Depends(require_user)) -> AuthUser:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="仅管理员可以管理数据源")
        return user

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request, response: Response):
        source = request.client.host if request.client else "unknown"
        try:
            user, token = app.state.auth.login(
                email=body.email,
                password=body.password,
                source=source,
            )
        except AuthenticationRateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        except AuthenticationError:
            raise HTTPException(status_code=401, detail="邮箱或密码错误")
        response.set_cookie(
            key=app.state.auth.config.cookie_name,
            value=token,
            max_age=app.state.auth.config.session_ttl_seconds,
            httponly=True,
            secure=app.state.auth.config.cookie_secure,
            samesite="lax",
            path="/",
        )
        return {"user": user.to_dict()}

    @app.get("/api/auth/me")
    def current_user(user: AuthUser = Depends(require_user)):
        return {"user": user.to_dict()}

    @app.get("/api/health")
    def api_health(request: Request, user: AuthUser = Depends(require_user)):
        del user
        quota = quota_status(request)
        return {
            "status": "ok",
            "version": "0.9.0",
            "tester_mode": quota["unlimited"],
            "quota": quota,
        }

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, user: AuthUser = Depends(require_user)):
        del user
        token = request.cookies.get(app.state.auth.config.cookie_name, "")
        app.state.auth.logout(token)
        response = Response(status_code=204)
        response.delete_cookie(app.state.auth.config.cookie_name, path="/")
        return response

    @app.post("/api/chat", status_code=202)
    def submit_chat(
        body: ChatRequest,
        request: Request,
        user: AuthUser = Depends(require_user),
    ):
        if not app.state.askdata.data_source_status().get("ready"):
            raise HTTPException(status_code=503, detail="数据源尚未连接，请联系管理员同步 Schema")
        quota = consume_quota(request)
        record = app.state.askdata.submit_chat(
            user_id=user.id,
            session_id=body.session_id,
            query=body.query.strip(),
            enable_long_term=body.enable_long_term,
            generate_chart=body.generate_chart,
        )
        return {
            "run_id": record.run_id,
            "status": record.status,
            "events_url": f"/api/runs/{record.run_id}/events",
            "result_url": f"/api/runs/{record.run_id}",
            "quota": quota,
        }

    @app.post("/api/drilldown", status_code=202)
    def submit_drilldown(
        body: DrilldownRequest,
        request: Request,
        user: AuthUser = Depends(require_user),
    ):
        if not app.state.askdata.data_source_status().get("ready"):
            raise HTTPException(status_code=503, detail="数据源尚未连接")
        parent = require_owned_run(body.parent_run_id, user)
        if parent.status != "completed" or parent.result is None:
            raise HTTPException(status_code=409, detail="父分析尚未完成")
        quota = consume_quota(request)
        try:
            record = app.state.askdata.submit_drilldown(
                user_id=user.id,
                parent_run_id=body.parent_run_id,
                query=body.query.strip(),
                direction=body.direction,
                generate_chart=body.generate_chart,
            )
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="父分析不存在")
        except RunAccessError:
            raise HTTPException(status_code=403, detail="无权钻取该分析")
        except RunNotReadyError:
            raise HTTPException(status_code=409, detail="父分析尚未完成")
        return {
            "run_id": record.run_id,
            "status": record.status,
            "events_url": f"/api/runs/{record.run_id}/events",
            "result_url": f"/api/runs/{record.run_id}",
            "parent_run_id": body.parent_run_id,
            "direction": body.direction,
            "quota": quota,
        }

    @app.get("/api/data-source/status")
    def data_source_status(
        request: Request,
        user: AuthUser = Depends(require_user),
    ):
        del user
        status = app.state.askdata.data_source_status()
        quota = quota_status(request)
        status.update(
            query_limit=quota["limit"],
            query_remaining=quota["remaining"],
            query_unlimited=quota["unlimited"],
        )
        return status

    @app.post("/api/data-source/test")
    def test_data_source(admin: AuthUser = Depends(require_admin)):
        del admin
        try:
            return app.state.askdata.test_data_source()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)[:500])

    @app.post("/api/data-source/sync")
    def sync_data_source(admin: AuthUser = Depends(require_admin)):
        del admin
        try:
            return app.state.askdata.sync_data_source()
        except DataSourceUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:500])
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)[:500])

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, user: AuthUser = Depends(require_user)):
        require_owned_run(run_id, user)
        return app.state.askdata.runs.snapshot(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str, user: AuthUser = Depends(require_user)):
        require_owned_run(run_id, user)
        return app.state.askdata.runs.cancel(run_id)

    @app.get("/api/runs/{run_id}/events")
    def stream_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        user: AuthUser = Depends(require_user),
    ):
        require_owned_run(run_id, user)

        def generate():
            cursor = after
            while True:
                events, terminal = app.state.askdata.runs.events_after(run_id, cursor, timeout=15)
                if not events:
                    yield ": keepalive\n\n"
                for event in events:
                    cursor = event["sequence"]
                    yield (
                        f"id: {cursor}\n"
                        f"event: {event['event']}\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
                if terminal:
                    break

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/conversations")
    def list_conversations(
        limit: int = Query(default=50, ge=1, le=200),
        user: AuthUser = Depends(require_user),
    ):
        return {"items": app.state.askdata.list_conversations(user_id=user.id, limit=limit)}

    @app.get("/api/conversations/{session_id}")
    def get_conversation(session_id: str, user: AuthUser = Depends(require_user)):
        return {"items": app.state.askdata.get_conversation(user_id=user.id, session_id=session_id)}

    @app.post("/api/analyses", status_code=201)
    def save_analysis(body: SaveAnalysisRequest, user: AuthUser = Depends(require_user)):
        try:
            return app.state.askdata.save_analysis(
                user_id=user.id,
                run_id=body.run_id,
                title=body.title,
            )
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="运行记录不存在")
        except RunAccessError:
            raise HTTPException(status_code=403, detail="无权保存该运行结果")
        except RunNotReadyError:
            raise HTTPException(status_code=409, detail="分析尚未成功完成")

    @app.get("/api/analyses")
    def list_saved_analyses(
        limit: int = Query(default=50, ge=1, le=200),
        user: AuthUser = Depends(require_user),
    ):
        return {
            "items": app.state.askdata.list_saved_analyses(
                user_id=user.id,
                limit=limit,
            )
        }

    @app.get("/api/analyses/{analysis_id}")
    def get_saved_analysis(analysis_id: str, user: AuthUser = Depends(require_user)):
        analysis = app.state.askdata.get_saved_analysis(
            user_id=user.id,
            analysis_id=analysis_id,
        )
        if analysis is None:
            raise HTTPException(status_code=404, detail="已保存分析不存在")
        return analysis

    def saved_export_result(analysis_id: str, user: AuthUser) -> dict:
        analysis = app.state.askdata.get_saved_analysis(
            user_id=user.id,
            analysis_id=analysis_id,
        )
        if analysis is None:
            raise HTTPException(status_code=404, detail="已保存分析不存在")
        return analysis["result"]

    @app.get("/api/analyses/{analysis_id}/export.csv")
    def download_saved_csv(analysis_id: str, user: AuthUser = Depends(require_user)):
        content = export_csv(saved_export_result(analysis_id, user))
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="askdata-{analysis_id}.csv"'},
        )

    @app.get("/api/analyses/{analysis_id}/export.xlsx")
    def download_saved_xlsx(analysis_id: str, user: AuthUser = Depends(require_user)):
        content = export_xlsx(saved_export_result(analysis_id, user))
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="askdata-{analysis_id}.xlsx"'},
        )

    @app.delete("/api/analyses/{analysis_id}", status_code=204)
    def delete_saved_analysis(analysis_id: str, user: AuthUser = Depends(require_user)):
        deleted = app.state.askdata.delete_saved_analysis(
            user_id=user.id,
            analysis_id=analysis_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="已保存分析不存在")
        return Response(status_code=204)

    @app.post("/api/dashboards", status_code=201)
    def create_dashboard(
        body: CreateDashboardRequest,
        user: AuthUser = Depends(require_user),
    ):
        return app.state.askdata.create_dashboard(
            user_id=user.id,
            name=body.name,
            description=body.description,
        )

    @app.get("/api/dashboards")
    def list_dashboards(
        limit: int = Query(default=50, ge=1, le=100),
        user: AuthUser = Depends(require_user),
    ):
        return {
            "items": app.state.askdata.list_dashboards(
                user_id=user.id,
                limit=limit,
            )
        }

    @app.get("/api/dashboards/{dashboard_id}")
    def get_dashboard(
        dashboard_id: str,
        user: AuthUser = Depends(require_user),
    ):
        dashboard = app.state.askdata.get_dashboard(
            user_id=user.id,
            dashboard_id=dashboard_id,
        )
        if dashboard is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        return dashboard

    @app.post("/api/dashboards/{dashboard_id}/cards", status_code=201)
    def add_dashboard_card(
        dashboard_id: str,
        body: AddDashboardCardRequest,
        user: AuthUser = Depends(require_user),
    ):
        card = app.state.askdata.add_dashboard_card(
            user_id=user.id,
            dashboard_id=dashboard_id,
            analysis_id=body.analysis_id,
            title=body.title,
        )
        if card is None:
            raise HTTPException(status_code=404, detail="仪表盘或已保存分析不存在")
        return card

    @app.delete(
        "/api/dashboards/{dashboard_id}/cards/{card_id}",
        status_code=204,
    )
    def remove_dashboard_card(
        dashboard_id: str,
        card_id: str,
        user: AuthUser = Depends(require_user),
    ):
        deleted = app.state.askdata.remove_dashboard_card(
            user_id=user.id,
            dashboard_id=dashboard_id,
            card_id=card_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="仪表盘卡片不存在")
        return Response(status_code=204)

    @app.delete("/api/dashboards/{dashboard_id}", status_code=204)
    def delete_dashboard(
        dashboard_id: str,
        user: AuthUser = Depends(require_user),
    ):
        deleted = app.state.askdata.delete_dashboard(
            user_id=user.id,
            dashboard_id=dashboard_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        return Response(status_code=204)

    def export_result(run_id: str, user: AuthUser) -> dict:
        try:
            return app.state.askdata.get_export_result(user_id=user.id, run_id=run_id)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="运行记录不存在")
        except RunAccessError:
            raise HTTPException(status_code=403, detail="无权导出该运行结果")
        except RunNotReadyError:
            raise HTTPException(status_code=409, detail="分析尚未成功完成")

    @app.get("/api/runs/{run_id}/export.csv")
    def download_csv(run_id: str, user: AuthUser = Depends(require_user)):
        content = export_csv(export_result(run_id, user))
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="askdata-{run_id}.csv"'},
        )

    @app.get("/api/runs/{run_id}/export.xlsx")
    def download_xlsx(run_id: str, user: AuthUser = Depends(require_user)):
        content = export_xlsx(export_result(run_id, user))
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="askdata-{run_id}.xlsx"'},
        )

    if WEB_DIR.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

        @app.get("/styles.css", include_in_schema=False)
        def styles():
            return FileResponse(WEB_DIR / "styles.css")

        @app.get("/dashboard.css", include_in_schema=False)
        def dashboard_styles():
            return FileResponse(WEB_DIR / "dashboard.css")

        @app.get("/preview-bootstrap.js", include_in_schema=False)
        def preview_bootstrap():
            return FileResponse(WEB_DIR / "preview-bootstrap.js")

        @app.get("/app.js", include_in_schema=False)
        def web_app_script():
            return FileResponse(WEB_DIR / "app.js")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(WEB_DIR / "index.html")

    @app.on_event("shutdown")
    def shutdown():
        app.state.askdata.close()

    return app


app = create_app()
