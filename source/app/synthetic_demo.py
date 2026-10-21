"""Default-off, loopback-only D2 preview composition root. No old product DB."""
import argparse
import asyncio
import time
from typing import Literal
from uuid import uuid4
import hmac
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr, Field

from app.application.synthetic_demo_service import SyntheticDemoService, PROFILES
from app.domain.synthetic_autonomy import WORLD, RESOURCE, ZONE, BACKGROUND

ROOT = Path(__file__).resolve().parents[1]
COOKIE = 'd2_local_preview_session'


def valid_preview_key(value):
    return (type(value) is str and 32 <= len(value) <= 128
            and value.isascii() and all('!' <= char <= '~' for char in value))


@dataclass(frozen=True)
class DemoConfiguration:
    enabled: bool = False
    host: str = '127.0.0.1'
    port: int = 8474
    world: str = WORLD
    resource: str = RESOURCE
    zone: str = ZONE
    background: str = BACKGROUND
    interval: float = 1.0
    control_key: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if (self.host != '127.0.0.1' or type(self.port) is not int or not 1024 <= self.port <= 65535
                or self.world != WORLD or self.resource != RESOURCE or self.zone != ZONE
                or self.background != BACKGROUND or type(self.enabled) is not bool
                or type(self.interval) not in (int, float) or not 0.01 <= self.interval <= 5):
            raise ValueError('Only the exact adopted loopback synthetic configuration is supported.')
        if self.enabled and not valid_preview_key(self.control_key):
            raise ValueError('Explicit process-local preview control key required.')


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    runtime_id: str = Field(min_length=1, max_length=80)
    preview_key: SecretStr = Field(min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")


class StartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    runtime_id: str = Field(min_length=1, max_length=80)
    profile: str = 'AUTONOMY'
    correlation_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")


class ClientEventRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    runtime_id: str = Field(min_length=1, max_length=80)
    correlation_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    interface: Literal['SESSION','START']
    callback: Literal['SESSION_SUBMIT','START_CLICK','START_FORM_SUBMIT_BLOCKED']
    submitter: Literal['UNLOCK_BUTTON','START_BUTTON','IMPLICIT','OTHER']
    gesture: bool


def create_synthetic_demo_app(configuration=None, *, audit=None, shutdown=None, session_ttl=1800, build_id='UNFROZEN', instance_id=None, archive=None, stopping_requested=None, review=None, safety_status=None):
    cfg = configuration if configuration is not None else DemoConfiguration()
    if type(cfg) is not DemoConfiguration:
        raise ValueError('Typed isolated configuration required; adapters are not accepted.')
    service = SyntheticDemoService(interval=cfg.interval) if cfg.enabled else None
    sessions = {}
    stopping = False
    instance_id = instance_id or uuid4().hex
    archived_scenes = []
    scene_generation = 0
    cookie_name = COOKIE + "_" + instance_id

    def is_stopping():
        return stopping or bool(stopping_requested is not None and stopping_requested())
    start_audit_lock = asyncio.Lock()
    audit_started = time.monotonic()
    audit_failed = False
    if audit is not None and not callable(audit):
        raise ValueError('Callable local audit sink required.')

    def record(event, interface, correlation_id=None, *, scene_match=False, **facts):
        nonlocal audit_failed
        if audit_failed:
            raise HTTPException(503, 'local_audit_unavailable')
        value = dict(event=event, interface=interface,
                     correlation_id=correlation_id or str(uuid4()),
                     runtime_id=service.runtime_id if service else None,
                     relative_ms=round((time.monotonic()-audit_started)*1000, 3), **facts)
        try:
            if audit is not None:
                audit(value)
            if review is not None and interface not in {'SOURCE'}:
                review.request(event, interface, value['correlation_id'], result=facts.get('result'),
                               profile=facts.get('profile'), generation=scene_generation,scene_match=scene_match)
        except Exception:
            audit_failed = True
            raise HTTPException(503, 'local_audit_unavailable') from None

    def source_tick(tick):
        nonlocal audit_failed
        if is_stopping():
            raise HTTPException(409, 'service_stopping')
        record('source_tick_admitted', 'SOURCE', result=str(tick))
        if review is not None:
            try:
                review.tick(scene_generation,tick,service.profile)
            except Exception:
                audit_failed = True
                raise HTTPException(503, 'local_audit_unavailable') from None

    def review_state(value):
        if review is not None:
            try:
                review.business(generation=scene_generation, phase=value['phase'], profile=value['source_profile'],
                    effects=value['autonomous_effects'], roots=value['distinct_command_roots'],
                    approvals=value['per_action_approvals'], revision=value['revision'],
                    version=value['resource_state']['version'], setting=value['resource_state']['setting'],
                    decision=value['safety_decision'])
                for action in value['actions']:
                    review.action(generation=scene_generation, root=action['root'], episode=action['episode'],
                        effect=action['effect'], status=action['status'], submissions=action['submissions'],
                        queries=action['queries'], confirmed_version=action['confirmed_version'])
            except Exception:
                nonlocal audit_failed
                audit_failed = True
                raise HTTPException(503, 'local_audit_unavailable') from None

    def source_state(value):
        record('source_state', 'SOURCE', snapshot=value)
        review_state(value)

    if service is not None:
        service.before_step = source_tick
        service.after_step = source_state

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if service is not None:
                await service.stop()
            sessions.clear()

    app = FastAPI(title='Home Orchestrator · Synthetic Autonomy Preview',
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.synthetic_demo = service
    app.state.configuration = cfg
    app.state.session_ttl = session_ttl
    app.state.archived_scenes = archived_scenes

    async def audit_failure_response():
        if service is not None:
            await service.stop()
        facts = await service.status() if service is not None else None
        # Fixed fact projection only; never return the complete service snapshot on failure.
        return JSONResponse(status_code=503, content={'error':'local_audit_unavailable',
            'phase':'FAILED_HOLD',
            'autonomous_effects':facts['autonomous_effects'] if facts is not None else 0,
            'distinct_command_roots':facts['distinct_command_roots'] if facts is not None else 0,
            'per_action_approvals':facts['per_action_approvals'] if facts is not None else 0})

    @app.exception_handler(HTTPException)
    async def http_failure(request, exc):
        if audit_failed:
            return await audit_failure_response()
        return JSONResponse(status_code=exc.status_code, content={'detail':exc.detail}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Do not reflect credentials, supplied PRIVATE text or unknown payload.
        interfaces={'/api/demo/session':'SESSION','/api/demo/start':'START','/api/demo/client-event':'CLIENT_EVENT',
                    '/api/demo/reset':'RESET','/api/demo/stop':'STOP_DEMO','/api/demo/shutdown':'STOP_APP'}
        if request.url.path in interfaces:
            interface = interfaces[request.url.path]
            try:
                record('request_validation_rejected', interface, result='INVALID_SCHEMA')
            except HTTPException:
                return await audit_failure_response()
        return JSONResponse(status_code=422, content={'error':'invalid_demo_request'})

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        nonlocal audit_failed
        async def boundary_failure(status, error):
            nonlocal audit_failed
            if review is not None:
                try:
                    review.http(request.url.path,status,generation=scene_generation)
                except Exception:
                    audit_failed = True
            return JSONResponse(status_code=status,content={'error':error})
        expected = f'127.0.0.1:{cfg.port}'
        if not request.client or request.client.host != '127.0.0.1' or request.headers.get('host') != expected:
            return await boundary_failure(403,'loopback_only')
        if request.method not in {'GET','HEAD'}:
            if request.headers.get('origin') != 'http://'+expected:
                return await boundary_failure(403,'same_origin_required')
            if request.headers.get('content-type','').split(';')[0].strip().lower() != 'application/json':
                return await boundary_failure(415,'json_required')
        if request.method not in {'GET', 'HEAD'}:
            try:
                body = await asyncio.wait_for(request.body(), timeout=3)
            except (asyncio.TimeoutError, Exception):
                return await boundary_failure(400,'request_body_unavailable')
            if len(body) > 8192:
                return await boundary_failure(413,'request_too_large')
        if audit_failed and request.url.path != '/api/demo/shutdown':
            return await audit_failure_response()
        response = await call_next(request)
        if review is not None and not getattr(request.state,'review_http_recorded',False):
            try:
                review.http(request.url.path,response.status_code,
                    entered_session=getattr(request.state,'entered_session',False),
                    entered_start=getattr(request.state,'entered_start',False),
                    scene_match=getattr(request.state,'scene_match',False),generation=scene_generation)
            except Exception:
                audit_failed = True
                if request.url.path != '/api/demo/shutdown':
                    return await audit_failure_response()
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.get('/healthz')
    async def health():
        return {'family':'D2_LOCAL_PRODUCT_WIRING_SMOKE','enabled':cfg.enabled,
                'runtime_id':service.runtime_id if service else None,'formal_gate_e':'NOT_EXECUTED', 'build_id':build_id, 'instance_id':instance_id}

    if not cfg.enabled:
        return app

    def check_runtime(runtime_id):
        if runtime_id != service.runtime_id:
            raise HTTPException(410, 'old_runtime_not_resumable')

    def operator(request):
        token = request.cookies.get(cookie_name)
        expires = sessions.get(token, 0)
        if expires <= time.monotonic():
            sessions.pop(token, None)
            raise HTTPException(401, 'preview_control_required')

    @app.get('/')
    async def page():
        return FileResponse(ROOT/'app/templates/synthetic_demo.html')

    @app.get('/assets/{name}')
    async def asset(name: str):
        if name not in {'rev2.css','synthetic_demo.css','synthetic_demo.js'}:
            raise HTTPException(404)
        return FileResponse(ROOT/'app/static'/name)

    @app.get('/api/demo/status')
    async def status(request: Request):
        value = await service.status()
        value.update(build_id=build_id, service_state='STOPPING' if is_stopping() else 'READY',
                     control_unlocked=sessions.get(request.cookies.get(cookie_name), 0) > time.monotonic(),
                     scene_generation=scene_generation)
        if safety_status is not None:
            value['safety_limit'] = safety_status()
        if review is not None and not audit_failed:
            review_state(value)
        if audit_failed:
            value.update(phase='FAILED_HOLD', safety_decision='AUDIT_UNAVAILABLE_HOLD')
        return value

    @app.post('/api/demo/client-event')
    async def client_event(payload: ClientEventRequest):
        # Client-reported callback metadata is observability, not a signed human gesture.
        check_runtime(payload.runtime_id)
        record('client_callback', payload.interface, payload.correlation_id,
               callback=payload.callback, submitter=payload.submitter,
               client_reported_gesture=payload.gesture, provenance='CLIENT_REPORTED_UNVERIFIED')
        return {'recorded':True}

    @app.post('/api/demo/session')
    async def session(payload: SessionRequest, request: Request):
        request.state.entered_session = True
        request.state.scene_match = payload.runtime_id == service.runtime_id
        cid = payload.correlation_id or str(uuid4())
        record('session_request_received', 'SESSION', cid,scene_match=request.state.scene_match)
        if payload.runtime_id != service.runtime_id:
            record('session_rejected', 'SESSION', cid, result='RUNTIME_MISMATCH')
            check_runtime(payload.runtime_id)
        supplied = payload.preview_key.get_secret_value()
        if (not valid_preview_key(supplied)
                or not hmac.compare_digest(supplied.encode('ascii'), cfg.control_key.encode('ascii'))):
            record('session_rejected', 'SESSION', cid, result='AUTH_REJECTED',scene_match=request.state.scene_match)
            raise HTTPException(401, 'preview_control_required')
        record('session_accepted', 'SESSION', cid, result='AUTH_AND_RUNTIME_VALID',scene_match=request.state.scene_match)
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        for old, expiry in list(sessions.items()):
            if expiry <= now:
                sessions.pop(old, None)
        if len(sessions) >= 32:
            sessions.pop(next(iter(sessions)))
        sessions[token] = now + app.state.session_ttl
        response = JSONResponse({'control':'LOCAL_PREVIEW_ONLY','member_identity':'NOT_ASSERTED',
                                 'runtime_id':service.runtime_id,'scene_generation':scene_generation})
        response.set_cookie(cookie_name, token, httponly=True, samesite='strict', max_age=1800)
        return response

    @app.post('/api/demo/start')
    async def start(payload: StartRequest, request: Request):
        request.state.entered_start = True
        request.state.scene_match = payload.runtime_id == service.runtime_id
        cid = payload.correlation_id or str(uuid4())
        profile = payload.profile if payload.profile in PROFILES else 'UNSUPPORTED'
        record('start_request_received', 'START', cid, profile=profile,scene_match=request.state.scene_match)
        try:
            operator(request)
        except HTTPException:
            record('start_rejected', 'START', cid, profile=profile, result='AUTH_REJECTED',scene_match=request.state.scene_match)
            raise
        record('start_auth_valid', 'START', cid, profile=profile,scene_match=request.state.scene_match)
        if payload.runtime_id != service.runtime_id:
            record('start_rejected', 'START', cid, profile=profile, result='RUNTIME_MISMATCH')
            check_runtime(payload.runtime_id)
        if payload.profile not in PROFILES:
            record('start_rejected', 'START', cid, profile=profile, result='PROFILE_REJECTED',scene_match=request.state.scene_match)
            raise HTTPException(422, 'unsupported_source_profile')
        async with start_audit_lock:
            operator(request)
            request.state.scene_match = payload.runtime_id == service.runtime_id
            check_runtime(payload.runtime_id)
            if is_stopping():
                raise HTTPException(409, 'service_stopping')
            created = service.port is None
            record('start_admitted', 'START', cid, profile=profile, result='VALIDATED',scene_match=request.state.scene_match)
            try:
                value = await service.start(payload.profile)
            except ValueError:
                record('start_rejected', 'START', cid, profile=profile, result='ALREADY_STARTED_OTHER_PROFILE',scene_match=request.state.scene_match)
                raise HTTPException(409, 'runtime_already_started') from None
            try:
                record('source_created' if created else 'source_reused', 'START', cid,
                       profile=profile, result='CREATED' if created else 'IDEMPOTENT_EXISTING',scene_match=request.state.scene_match)
                record('start_accepted', 'START', cid, profile=profile, result='ACCEPTED',scene_match=request.state.scene_match)
            except HTTPException:
                # File and in-memory creation are not one atomic transaction.
                # Stop this source; already completed effects are retained in HOLD.
                await service.stop()
                raise
            return value

    class RuntimeRequest(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        runtime_id: str = Field(min_length=1, max_length=80)

    @app.post('/api/demo/stop')
    async def stop_demo(payload: RuntimeRequest, request: Request):
        request.state.scene_match = payload.runtime_id == service.runtime_id
        operator(request)
        check_runtime(payload.runtime_id)
        await service.stop()
        return await service.status()

    @app.post('/api/demo/reset')
    async def reset_demo(payload: RuntimeRequest, request: Request):
        nonlocal service, audit_failed, scene_generation
        request.state.scene_match = payload.runtime_id == service.runtime_id
        operator(request)
        check_runtime(payload.runtime_id)
        async with start_audit_lock:
            operator(request)
            check_runtime(payload.runtime_id)
            if is_stopping() or audit_failed or service.phase not in {'FINISHED', 'STOPPED'}:
                raise HTTPException(409, 'finish_or_stop_before_new_scene')
            await service.stop()
            if is_stopping() or audit_failed:
                raise HTTPException(409, 'service_stopping_or_failed')
            # Preserve previous journal in the audit sink; never mutate its ledger.
            snapshot = await service.status()
            try:
                if archive is not None:
                    archive(snapshot)
                archived_scenes.append(snapshot)
                record('scene_closed', 'RESET', snapshot=snapshot,scene_match=request.state.scene_match)
            except Exception:
                audit_failed = True
                raise HTTPException(503, 'scene_archive_unavailable') from None
            prepared = SyntheticDemoService(interval=cfg.interval, before_step=source_tick, after_step=source_state)
            next_generation = scene_generation + 1
            if review is not None:
                try:
                    # The audit decision is the reset linearization point. No await occurs between it and memory commit.
                    review.scene(next_generation)
                    request.state.review_http_recorded = True
                except Exception:
                    audit_failed = True
                    raise HTTPException(503,'scene_audit_unavailable') from None
            service = prepared
            app.state.synthetic_demo = service
            sessions.clear()
            scene_generation = next_generation
            return {'runtime_id':service.runtime_id, 'scene_generation':scene_generation,
                    'control':'REUNLOCK_REQUIRED'}

    @app.post('/api/demo/shutdown')
    async def stop_app(payload: RuntimeRequest, request: Request):
        nonlocal stopping
        request.state.scene_match = payload.runtime_id == service.runtime_id
        operator(request)
        check_runtime(payload.runtime_id)
        # STOP must work even when the audit sink has failed.
        stopping = True
        await service.stop()
        if shutdown is not None:
            asyncio.get_running_loop().call_later(0.2, shutdown, 'PAGE_STOP')
        return {'service_state':'STOPPING', 'runtime_id':service.runtime_id}

    return app


def main():
    parser = argparse.ArgumentParser(description='Default-off isolated D2 synthetic preview.')
    parser.add_argument('--enable-synthetic-demo', action='store_true')
    parser.add_argument('--control-key-file', type=Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8474)
    parser.add_argument('--interval', type=float, default=1.0)
    args = parser.parse_args()
    if args.host != '127.0.0.1': parser.error('Only127.0.0.1 is allowed.')
    key = None
    if args.enable_synthetic_demo:
        if args.control_key_file is None: parser.error('Fresh local preview control key required.')
        key = args.control_key_file.read_text(encoding='utf-8')
    try:
        cfg = DemoConfiguration(enabled=args.enable_synthetic_demo, host=args.host,
                                port=args.port, interval=args.interval, control_key=key)
    except ValueError as exc: parser.error(str(exc))
    import uvicorn
    # No forwarded identity, dotenv, old DB, arbitrary adapter or remote bind.
    uvicorn.run(create_synthetic_demo_app(cfg), host='127.0.0.1', port=cfg.port,
                proxy_headers=False, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
