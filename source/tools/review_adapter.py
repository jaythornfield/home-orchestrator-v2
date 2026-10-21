"""Producer-specific factories: explicit safe scalars, never raw IO sanitization."""
import hashlib
import json
import time
from app.safe_receipts import SafeReceiptWriter, EmergencyReceiptWriter, Alias, issue_alias, PROFILES, STOP_REASONS

def safe_reason(reason):
    if reason is None: return 'NONE'
    if reason in STOP_REASONS: return reason
    return {'SERVICE_ERROR':'WORKER_FAILURE','LAUNCHER_ERROR':'INTERNAL_ERROR',
            'STARTUP_DEADLINE':'STARTUP_FAILURE','STARTUP_ADMISSION_DEADLINE':'STARTUP_FAILURE',
            'READY_IDENTITY_ERROR':'SAFETY_CONTROL_FAILURE','CONTROL_INPUT_ERROR':'SAFETY_CONTROL_FAILURE',
            'CONTROL_INPUT_UNAVAILABLE':'SAFETY_CONTROL_FAILURE','USER_INTERRUPT':'SAFETY_CONTROL_FAILURE'}.get(reason,'UNKNOWN')

def requester(reason,source):
    if reason is None: return 'NONE'
    if source == 'PARENT': return 'PARENT'
    if reason == 'OPERATOR_STOP': return 'USER_CONSOLE' if source == 'CONSOLE' else 'TEST'
    return {'PAGE_STOP':'PAGE','NATURAL_DEADLINE':'DEADLINE','AUDIT_FAILURE':'AUDIT',
            'PARENT_INPUT_EOF':'PARENT'}.get(reason,'SAFETY')

class LifecycleReview:
    def __init__(self, root, run_alias, build_id, control, role):
        # Metadata contains no credential or native identity. ZIP SHA is bound by the external observer.
        manifest_bytes=(root/'PACKAGE_INTEGRITY.json').read_bytes()
        manifest=json.loads(manifest_bytes)
        self.control=control
        self.context=dict(build_id=build_id,source_sha256=manifest['source_aggregate_sha256'],
            package_sha256=hashlib.sha256(manifest_bytes).hexdigest().upper(),case_id='LIFECYCLE',
            run_alias=Alias('run',run_alias))
        self.root=root/'.local/review_safe'/run_alias/role
        self.emergency=EmergencyReceiptWriter(self.root,**self.context)
        self.process=issue_alias('process')
        self.artifact=issue_alias('artifact')
        self.worker_created=role=='worker'
        try:
            self.writer=SafeReceiptWriter(self.root,**self.context)
        except Exception:
            self.emergency.record(requested_by='AUDIT',initiating_reason='AUDIT_FAILURE',forwarded_reason='NONE',
                observed_reason='AUDIT_FAILURE',acknowledged=False,stage='REQUESTED',cleanup_complete=False,
                failure_stage='CONSTRUCT',failure_code='WRITE_FAILED',artifact_alias=self.artifact)
            raise OSError('SAFE_RECEIPT_CONSTRUCTION_FAILED') from None
        if not self.writer.emit('BASELINE','APPLICATION',default_deadline_seconds=1800,
                deadline_clock='LAUNCHER_START',accelerated_test=control.deadline_seconds!=1800,phase='PREPARING'):
            self.emergency.record(requested_by='AUDIT',initiating_reason='AUDIT_FAILURE',forwarded_reason='NONE',
                observed_reason='AUDIT_FAILURE',acknowledged=False,stage='REQUESTED',cleanup_complete=False,
                failure_stage='WRITE',failure_code=self.writer.last_error or 'WRITER_FAILED',artifact_alias=self.artifact)
            raise OSError('SAFE_BASELINE_WRITE_FAILED')

    def stop(self, reason, *, stage, acknowledged, cleanup, forwarded=False, observed_reason=None, forwarded_reason=None):
        normalized=safe_reason(reason)
        fields=dict(requested_by=requester(reason,self.control.source),initiating_reason=normalized,
            forwarded_reason=safe_reason(forwarded_reason) if forwarded_reason is not None else normalized if self.control.source=='PARENT' or forwarded else 'NONE',
            observed_reason=safe_reason(observed_reason) if observed_reason is not None else normalized,acknowledged=acknowledged,stage=stage,cleanup_complete=cleanup)
        ok=self.writer.emit('STOP','APPLICATION',**fields,worker_created=self.worker_created,prepared_files_checked=getattr(self.control,'prepared_files_checked',0),operator_stop_applicable=reason in {'OPERATOR_STOP','PAGE_STOP'} and self.worker_created)
        if not ok:
            self.emergency.record(**fields)
        return ok

    def native(self, exit_code):
        return self.writer.emit('NATIVE','OS_HANDLE',process_alias=self.process,
            observation_method='CREATOR_PROCESS_HANDLE',native_exit_observed=True,native_exit_code=exit_code)

    def port(self, classification, actual_errno, duration_ms, socket_closed):
        fields=dict(observation_method='LOOPBACK_CONNECT',classification=classification,
            duration_ms=duration_ms,socket_closed=socket_closed)
        if actual_errno is not None: fields['actual_errno']=actual_errno
        return self.writer.emit('PORT','DERIVED',**fields)

class AppReview(LifecycleReview):
    def __init__(self,root,run_alias,build_id,control):
        super().__init__(root,run_alias,build_id,control,'worker')
        self.scenes={0:issue_alias('scene')}
        self.roots={}
        self.episodes={}
        self.requests={}
        self.last_business=None
        self.last_business_signature=None
        self.last_actions={}
        self.last_status_at=0

    def scene_alias(self,generation):
        if generation not in self.scenes: self.scenes[generation]=issue_alias('scene')
        return self.scenes[generation]

    def require(self,ok):
        if not ok:
            error=self.writer.last_error or 'WRITER_FAILED'
            self.emergency.record(requested_by='AUDIT',initiating_reason='AUDIT_FAILURE',
                forwarded_reason='NONE',observed_reason='AUDIT_FAILURE',acknowledged=False,stage='REQUESTED',cleanup_complete=False,
                failure_stage='WRITE' if error in {'WRITE_FAILED','WRITER_FAILED','SIZE_LIMIT','RECORD_LIMIT'} else 'VALIDATE',
                failure_code=error,artifact_alias=self.artifact)
            raise OSError('SAFE_AUDIT_UNAVAILABLE')

    def request(self,event,interface,correlation,*,result=None,profile=None,generation=0,scene_match=False):
        if event=='client_callback':
            self.require(self.writer.emit('CLIENT','CLIENT',scene_alias=self.scene_alias(generation),generation=generation,
                gesture='UNLOCK' if interface=='SESSION' else 'START',result='ACCEPTED'))
            return
        if interface not in {'SESSION','START','RESET','CLIENT_EVENT','STOP_DEMO','STOP_APP'}: return
        key=correlation
        if key not in self.requests:
            if len(self.requests)>=256: self.requests.clear()
            self.requests[key]=issue_alias('request')
        outcomes={'AUTH_AND_RUNTIME_VALID':'ACCEPTED','VALIDATED':'ACCEPTED','ACCEPTED':'ACCEPTED',
            'AUTH_REJECTED':'AUTH_REJECTED','RUNTIME_MISMATCH':'RUNTIME_MISMATCH','INVALID_SCHEMA':'INVALID_SCHEMA',
            'PROFILE_REJECTED':'PROFILE_REJECTED','ALREADY_STARTED_OTHER_PROFILE':'ALREADY_STARTED_OTHER_PROFILE',
            'CREATED':'CREATED','IDEMPOTENT_EXISTING':'IDEMPOTENT_EXISTING'}
        outcome=outcomes.get(result,'RECEIVED' if event.endswith('received') else 'ACCEPTED')
        status={'AUTH_REJECTED':401,'RUNTIME_MISMATCH':410,'INVALID_SCHEMA':422,'PROFILE_REJECTED':422,
            'ALREADY_STARTED_OTHER_PROFILE':409}.get(outcome,0 if outcome=='RECEIVED' else 200)
        self.require(self.writer.emit('REQUEST','SERVER',scene_alias=self.scene_alias(generation),generation=generation,
            route=interface,result=outcome,http_status=status,
            entered_session=event in {'session_request_received','session_rejected','session_accepted'},
            entered_start=event in {'start_request_received','start_auth_valid','start_rejected','start_admitted','source_created','source_reused','start_accepted'},
            scene_match=scene_match,request_alias=self.requests[key]))

    def http(self,path,status,*,entered_session=False,entered_start=False,scene_match=False,generation=0):
        route={'/api/demo/session':'SESSION','/api/demo/start':'START','/api/demo/reset':'RESET',
            '/api/demo/status':'STATUS','/api/demo/stop':'STOP_DEMO','/api/demo/shutdown':'STOP_APP',
            '/api/demo/client-event':'CLIENT_EVENT','/healthz':'HEALTH'}.get(path,'OTHER')
        if route in {'STATUS','HEALTH','OTHER'} and status==200:
            if time.monotonic()-self.last_status_at<5: return
            self.last_status_at=time.monotonic()
        outcome={401:'AUTH_REJECTED',410:'RUNTIME_MISMATCH',422:'INVALID_SCHEMA',503:'AUDIT_UNAVAILABLE',
                 403:'ORIGIN_REJECTED',409:'STOPPING'}.get(status,'ACCEPTED' if status<400 else 'REJECTED')
        self.require(self.writer.emit('REQUEST','SERVER',scene_alias=self.scene_alias(generation),generation=generation,
            route=route,result=outcome,http_status=status,entered_session=entered_session,
            entered_start=entered_start,scene_match=scene_match))

    def business(self,*,generation,phase,profile,effects,roots,approvals,revision,version,setting,decision):
        signature=(generation,phase,profile,effects,roots,approvals,revision,version,setting,decision)
        if signature == self.last_business_signature: return
        self.last_business_signature=signature
        self.last_business=dict(profile=profile if profile in PROFILES else 'NONE',phase=phase,effects=effects,
            roots=roots,approvals=approvals,version=version)
        self.require(self.writer.emit('BUSINESS','SERVER',scene_alias=self.scene_alias(generation),generation=generation,
            **self.last_business,decision=self.decision(decision),setting={'COMFORT_A':'A','COMFORT_B':'B'}.get(setting,'NONE')))

    def decision(self,value):
        if value in {'NOT_STARTED',None}: return 'NONE'
        if 'OBJECTION' in value: return 'OBJECTION_HOLD'
        if 'AFFECTED' in value or 'OCCUP' in value: return 'AFFECTED_HOLD'
        if 'UNKNOWN' in value: return 'UNKNOWN_HOLD'
        if 'REVOK' in value: return 'REVOKED'
        if 'FENCE' in value: return 'FENCE_REQUIRED'
        if 'HOLD' in value: return 'OTHER_HOLD'
        return 'ALLOW'

    def action(self,*,generation,root,episode,effect,status,submissions,queries,confirmed_version):
        signature=(generation,episode,effect,status,submissions,queries,confirmed_version)
        if self.last_actions.get(root)==signature: return
        self.last_actions[root]=signature
        if root not in self.roots: self.roots[root]=issue_alias('root')
        if episode not in self.episodes: self.episodes[episode]=issue_alias('episode')
        state=dict(self.last_business)
        if confirmed_version is not None: state['confirmed_version']=confirmed_version
        self.require(self.writer.emit('BUSINESS','SERVER',scene_alias=self.scene_alias(generation),generation=generation,
            **state,root_alias=self.roots[root],episode_alias=self.episodes[episode],
            action={'COMFORT_A':'A','COMFORT_B':'B'}.get(effect,'NONE'),
            status=status if status in {'APPLIED','NOT_APPLIED','UNKNOWN','PENDING','HOLD','REJECTED'} else 'PENDING',
            queries=queries,submissions=submissions))

    def tick(self,generation,tick,profile):
        self.require(self.writer.emit('SOURCE','SERVER',scene_alias=self.scene_alias(generation),
            generation=generation,tick=tick,profile=profile,admitted=True))

    def scene(self,generation):
        self.require(self.writer.emit('SCENE','SERVER',scene_alias=self.scene_alias(generation),generation=generation,
            transition='RESET_COMMITTED',profile='NONE',sessions_invalidated=True))
