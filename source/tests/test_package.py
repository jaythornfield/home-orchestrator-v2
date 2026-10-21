"""New-package HTTP/private-pipe acceptance. Never claims visible desktop evidence."""
import argparse,concurrent.futures,hashlib,http.cookiejar,json,time,urllib.error,urllib.request
from pathlib import Path
from app.safe_receipts import SafeReceiptWriter,issue_alias,export_review_safe,Alias
from tools.native_supervisor import OwnedProcess

def require(condition,code):
    if not condition: raise RuntimeError(code)

class PackageRun:
    pending=None
    def __init__(self,package,output,case,*,deadline=1800,interval=.1):
        self.package=package;self.output=output;self.case=case;self.deadline=deadline
        manifest_bytes=(package/'PACKAGE_INTEGRITY.json').read_bytes();manifest=json.loads(manifest_bytes)
        self.build=manifest['build_id'];self.started=time.monotonic();self.expected_code=0
        self.request_count=0;self.samples=0;self.checks=[]
        command="import sys;from tools.portable_launcher import main;sys.exit(main(deadline_seconds="+str(deadline)+",browser=False,interval="+str(interval)+",control_client=True))"
        self.owned=OwnedProcess([str(package/'runtime/python.exe'),'-I','-X','utf8','-c',command],str(package))
        PackageRun.pending=self
        require(self.owned.identity_error is None,'NATIVE_LAUNCHER_IDENTITY')
        end=time.monotonic()+25
        while time.monotonic()<end and self.owned.process.poll() is None:
            if self.owned.private_ready is not None and self.owned.key:
                break
            time.sleep(.03)
        require(self.owned.private_ready is not None and self.owned.key is not None,'NO_PRIVATE_READY')
        self.ready=self.owned.private_ready
        self.runtime=self.ready['runtime_id'];self.run_alias=self.ready['run_alias']
        self.url='http://127.0.0.1:'+str(self.ready['port'])
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.observer=SafeReceiptWriter(output/'review_safe'/self.run_alias/'observer',build_id=self.build,
            source_sha256=manifest['source_aggregate_sha256'],package_sha256=hashlib.sha256(manifest_bytes).hexdigest().upper(),
            case_id=case,run_alias=Alias('run',self.run_alias))

    def request(self,path,body=None,*,expected=200,origin=None):
        data=json.dumps(body).encode('utf-8') if body is not None else None
        req=urllib.request.Request(self.url+path,data=data,headers={'Content-Type':'application/json','Origin':origin or self.url})
        try:
            with self.opener.open(req,timeout=4) as response:
                status=response.status;raw=response.read(256*1024)
        except urllib.error.HTTPError as response:
            status=response.code;raw=response.read(256*1024)
        self.request_count+=1
        require(status==expected,'HTTP_STATUS_'+str(status)+'_EXPECTED_'+str(expected))
        return json.loads(raw) if raw else None

    def status(self):
        value=self.request('/api/demo/status');self.samples+=1
        return value

    def unlock(self):
        return self.request('/api/demo/session',{'runtime_id':self.runtime,'preview_key':self.owned.key})

    def start(self,profile='S5_THIRD_A'):
        return self.request('/api/demo/start',{'runtime_id':self.runtime,'profile':profile})

    def finished(self,profile):
        deadline=time.monotonic()+15;hold_seen=False
        while time.monotonic()<deadline:
            value=self.status()
            if value['autonomous_effects']==2 and 'HOLD' in value['safety_decision']:hold_seen=True
            if value['phase'] in {'FINISHED','FAILED_HOLD'}:
                require(value['phase']=='FINISHED','SOURCE_FAILED')
                expected=3 if profile in {'S5_THIRD_A','S5_EXACT_OBJECTION_LIFT'} else 2
                require(value['autonomous_effects']==expected,'EFFECT_COUNT')
                if expected==3:
                    require((value['distinct_command_roots'],value['per_action_approvals'])==(3,0),'ROOT_APPROVAL_COUNT')
                    require([a['confirmed_version'] for a in value['actions']]==[1,2,3],'AUTHORITY_CONFIRMATIONS')
                if profile=='S5_EXACT_OBJECTION_LIFT': require(hold_seen,'OBJECTION_HOLD_NOT_OBSERVED')
                if profile=='S5_THIRD_A_UNKNOWN':
                    require(value['actions'][-1]['submissions']==1 and value['actions'][-1]['queries']==2,'UNKNOWN_RETRY_BUDGET')
                self.checks.append({'profile':profile,'effects':expected,'hold_observed':hold_seen,'pass':True})
                return value
            time.sleep(.03)
        raise RuntimeError('SOURCE_COMPLETION_DEADLINE')

    def reset(self):
        value=self.request('/api/demo/reset',{'runtime_id':self.runtime})
        self.runtime=value['runtime_id']
        # Deliberately reauthenticate before any new status poll, using the same in-memory process code.
        self.unlock()
        state=self.status()
        require((state['phase'],state['autonomous_effects'],state['distinct_command_roots'])==('NOT_STARTED',0,0),'RESET_AUTOSTART')

    def idle(self,seconds):
        end=time.monotonic()+seconds;start=time.monotonic()
        while time.monotonic()<end:
            value=self.status()
            require((value['phase'],value['autonomous_effects'],value['distinct_command_roots'])==('NOT_STARTED',0,0),'UNLOCK_AUTOSTART')
            time.sleep(.25)
        self.checks.append({'assertion':'UNLOCK_IDLE_NO_START','seconds':round(time.monotonic()-start,3),'pass':True})

    def close(self,mode='PIPE',expected_code=0):
        stop_started=time.monotonic()
        page_ack_delay=None
        if self.owned.process.poll() is None:
            if mode=='PAGE':
                self.request('/api/demo/shutdown',{'runtime_id':self.runtime})
                page_ack_delay=time.monotonic()-stop_started
            elif mode=='PIPE':self.owned.send_stop()
            elif mode=='EOF':self.owned.process.stdin.close()
        result=self.owned.wait(25 if mode!='DEADLINE' else max(25,self.deadline+5))
        close_delay=result['observed_at']-stop_started
        ack_delay=(self.owned.control_ack_at-stop_started) if self.owned.control_ack_at is not None else page_ack_delay
        if mode in {'PIPE','PAGE'}:
            require(ack_delay is not None and ack_delay<=2,'STOP_ACK_DEADLINE')
            require(close_delay<=20,'STOP_NATIVE_EXIT_DEADLINE')
        require(self.observer.emit('NATIVE','OS_HANDLE',process_alias=issue_alias('process'),
            observation_method='CREATOR_PROCESS_HANDLE',native_exit_observed=True,native_exit_code=result['native_exit_code']), 'SAFE_NATIVE_WRITE')
        # Only source-safe producer files are read for evidence. No raw/local_only receipt parsing.
        exported=[]
        for role in ('launcher','worker'):
            source=self.package/'.local/review_safe'/self.run_alias/role
            destination=self.output/'exported'/self.run_alias/role
            destination.parent.mkdir(parents=True,exist_ok=True)
            exported.extend(export_review_safe(source,destination))
        safe_worker=self.output/'exported'/self.run_alias/'worker/events.jsonl'
        worker_events=[json.loads(line) for line in safe_worker.read_text(encoding='ascii').splitlines()]
        launcher_events=[json.loads(line) for line in (self.output/'exported'/self.run_alias/'launcher/events.jsonl').read_text(encoding='ascii').splitlines()]
        require(result['native_exit_code']==expected_code,'NATIVE_LAUNCHER_EXIT_CODE')
        worker_native=[r['fields']['native_exit_code'] for r in launcher_events if r['event']=='NATIVE' and r['fields']['native_exit_observed']]
        require(worker_native==[expected_code],'NATIVE_WORKER_EXIT_CODE')
        require(any(r['event']=='PORT' and r['fields']['classification']=='CONNECTION_REFUSED' and r['fields'].get('actual_errno')==10061 for r in launcher_events),'PORT_RELEASE_NOT_10061')
        final=[r['fields'] for r in worker_events if r['event']=='STOP' and r['fields']['stage']=='CLEANED']
        require(final and final[-1]['cleanup_complete'],'WORKER_CLEANUP_NOT_RECORDED')
        expected_reason={'PAGE':'PAGE_STOP','PIPE':'OPERATOR_STOP','DEADLINE':'NATURAL_DEADLINE','EOF':'PARENT_INPUT_EOF'}[mode]
        require(final[-1]['initiating_reason']==expected_reason,'STOP_CAUSE_CHANGED')
        PackageRun.pending=None
        return {'run_alias':self.run_alias,'case':self.case,'build_id':self.build,'checks':self.checks,
            'native_launcher_exit_code':result['native_exit_code'],'native_worker_exit_code':worker_native[0],
            'stop_mode':mode,'stop_reason':expected_reason,'cleanup_complete':True,'port_10061':True,
            'stop_to_native_exit_seconds':round(close_delay,4),'input_or_page_response_ack_seconds':round(ack_delay,4) if ack_delay is not None else None,
            'ack_evidence':'PRIVATE_TYPED_LAUNCHER_INPUT_FRAME' if mode=='PIPE' else 'HTTP_STOPPING_RESPONSE' if mode=='PAGE' else 'NOT_APPLICABLE',
            'HTTP_requests':self.request_count,'samples':self.samples,'duration_seconds':round(time.monotonic()-self.started,3),
            'evidence_layer':'HTTP_PRIVATE_PIPE_NATIVE_HANDLES_NOT_DESKTOP','exported_files':exported}

    def fail_cleanup(self):
        if self.owned.process.poll() is None and self.owned.identity_error is None:
            self.owned.send_stop()
        try:
            value=self.owned.wait(25)
            return {'native_exit_observed':True,'native_exit_code':value['native_exit_code'],'forced_cleanup':False}
        except Exception:
            return {'native_exit_observed':False,'status':'NEW_OWNED_PROCESS_HOLD','forced_cleanup':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('package',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--quick',action='store_true');parser.add_argument('--deadline-case',action='store_true')
    args=parser.parse_args();package=args.package.resolve(strict=True);output=args.output
    output.mkdir(exist_ok=False)
    summary={'kind':'R6_PACKAGE_REAL_HTTP_NATIVE_NOT_UI','pass':False,'runs':[],'desktop_evidence':False}
    try:
        if args.deadline_case:
            run=PackageRun(package,output,'T15',deadline=3,interval=.1)
            summary['runs'].append(run.close('DEADLINE',expected_code=2))
        else:
            first=PackageRun(package,output,'CORE')
            first.unlock();first.idle(1 if args.quick else 30)
            for bad in ['中文',' ','SYNTHETIC_WRONG_VALUE','x'*129]:
                first.request('/api/demo/session',{'runtime_id':first.runtime,'preview_key':bad},expected=422 if len(bad)>128 else 401)
            first.request('/api/demo/start',{'runtime_id':'old-scene','profile':'S5_THIRD_A'},expected=410)
            first.request('/api/demo/start',{'runtime_id':first.runtime},expected=403,origin='http://wrong.invalid')
            for index,profile in enumerate(['S5_THIRD_A','S5_EXACT_OBJECTION_LIFT','S5_EXACT_GUEST','S5_EXACT_CHILD','S5_THIRD_A_UNKNOWN']):
                if index:first.reset()
                if index==1 and not args.quick:first.idle(30)
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    list(pool.map(lambda _:first.start(profile),range(3)))
                first.finished(profile)
            old_key=first.owned.key
            first_run=first.run_alias
            summary['runs'].append(first.close('PAGE'))
            second=PackageRun(package,output,'T13');require(second.owned.key!=old_key,'HOT_REUSED_CODE');require(second.run_alias!=first_run,'HOT_REUSED_RUN')
            old_key=None
            second.unlock();second.start();second.finished('S5_THIRD_A');summary['runs'].append(second.close('PIPE'))
            third=PackageRun(package,output,'T11');third.unlock();third.start();time.sleep(.05)
            require(third.status()['phase']=='RUNNING','RUNNING_STOP_NOT_RUNNING')
            summary['runs'].append(third.close('PIPE'))
        summary['pass']=True
    except Exception as error:
        # The category is useful; never export free exception text, request values or private stdout.
        summary['error_category']=type(error).__name__
        if isinstance(error,RuntimeError) and str(error).replace('_','').isalnum() and len(str(error))<80:
            summary['assertion_code']=str(error)
        if PackageRun.pending is not None:
            summary['failure_cleanup']=PackageRun.pending.fail_cleanup()
    with (output/'SUMMARY.json').open('x',encoding='utf-8') as out:json.dump(summary,out,indent=2)
    print(json.dumps({'pass':summary['pass'],'completed_runs':len(summary['runs']),
        'error_category':summary.get('error_category'),'assertion':summary.get('assertion_code'),
        'hold':summary.get('failure_cleanup',{}).get('status'),'desktop_evidence':False}))
    return 0 if summary['pass'] else 1
if __name__=='__main__':raise SystemExit(main())
