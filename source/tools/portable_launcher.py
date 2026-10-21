"""Portable lifecycle owner with one retained native worker handle. No installer, external Python, PowerShell or device IO."""
import os
import sys
import json
import time
import secrets
import threading
import socket
import hashlib
import ctypes
import math
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FAILED = threading.Event()
CONSOLE_OUTPUT = None


def atomic_json(path, value):
    # Bounded basename avoids MAX_PATH growth from repeating a long target name.
    temporary = path.with_name('.w_' + uuid4().hex[:12] + '.tmp')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def safe_print(message):
    output = CONSOLE_OUTPUT
    if output is not None:
        if not output.message(message):
            OUTPUT_FAILED.set()
        return
    try:
        print(message, flush=True)
    except (OSError, UnicodeError):
        OUTPUT_FAILED.set()


class StopControl:
    """One finite, non-blocking stdin consumer, before application imports.

    The thread only communicates intent; the loop owns task/socket shutdown.
    No process signal, job or force-exit operation exists here.
    """
    def __init__(self, deadline=1800, start_token=None, accept_forwarded=False):
        self.event = threading.Event()
        self.start_token = start_token
        self.accept_forwarded = accept_forwarded
        self.source = None
        self.interval = 1.0
        self.browser = True
        self.review_alias = None
        self.admitted = threading.Event()
        if start_token is None:
            self.admitted.set()
        self.closed = threading.Event()
        self.initialized = threading.Event()
        self.lock = threading.Lock()
        self.reason = None
        self.requested_at = None
        self.started = time.monotonic()
        self.deadline_seconds = deadline
        self.deadline = self.started + deadline
        self.thread = threading.Thread(target=self._read, name='console-stop', daemon=False)

    def request(self, reason, source='INTERNAL'):
        permitted = {'OPERATOR_STOP','PAGE_STOP','NATURAL_DEADLINE','PARENT_INPUT_EOF',
            'CONTROL_INPUT_UNAVAILABLE','CONTROL_INPUT_ERROR','USER_INTERRUPT','STARTUP_DEADLINE',
            'STARTUP_ADMISSION_DEADLINE','READY_IDENTITY_ERROR','SERVICE_ERROR','LAUNCHER_ERROR',
            'AUDIT_FAILURE','SAFETY_CONTROL_FAILURE','PREPARATION_CANCELLED'}
        if reason not in permitted:
            reason = 'SAFETY_CONTROL_FAILURE'
        with self.lock:
            if self.reason is None:
                self.reason = reason
                self.source = source
                self.requested_at = time.monotonic()
                self.event.set()

    def _read_console(self, msvcrt):
        global CONSOLE_OUTPUT
        from tools.console_commands import CommandEditor, ConsoleOutput, console_output_available
        editor = CommandEditor()
        output = ConsoleOutput(sys.stdout) if console_output_available(sys.stdout) else None
        self.console_echo_available = output is not None
        self.input_kind = 'CONSOLE'
        if output is not None:
            CONSOLE_OUTPUT = output
        try:
            if output is not None and not output.draw(editor.display()):
                OUTPUT_FAILED.set()
                self.request('SAFETY_CONTROL_FAILURE')
            self.initialized.set()
            while not self.closed.wait(.03) and not self.event.is_set():
                if time.monotonic() >= self.deadline:
                    self.request('NATURAL_DEADLINE')
                    return
                if not msvcrt.kbhit():
                    continue
                char = msvcrt.getwch()
                if char == '\x03':
                    self.request('USER_INTERRUPT')
                    return
                result = editor.feed(char)
                if result == 'ACCEPT':
                    # Stop intent is visible to the owner before any fallible output.
                    self.request('OPERATOR_STOP', 'CONSOLE')
                if output is not None:
                    ok = output.submitted(result) if result in {'ACCEPT','HELP','EMPTY'} else output.draw(editor.display()) if result=='DRAW' else True
                    if not ok:
                        OUTPUT_FAILED.set()
                        self.request('SAFETY_CONTROL_FAILURE')
                if result == 'ACCEPT':
                    return
        finally:
            if output is not None:
                if not output.close():
                    OUTPUT_FAILED.set()
                    self.request('SAFETY_CONTROL_FAILURE')
                if CONSOLE_OUTPUT is output:
                    CONSOLE_OUTPUT = None

    def _read(self):
        import msvcrt
        from ctypes import wintypes
        try:
            handle = msvcrt.get_osfhandle(sys.stdin.fileno())
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetFileType.argtypes = [wintypes.HANDLE]
            kernel.GetFileType.restype = wintypes.DWORD
            kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            kind = kernel.GetFileType(handle)
            if kind not in (2, 3):
                self.request('CONTROL_INPUT_UNAVAILABLE')
                self.initialized.set()
                return
            if kind == 2:
                mode = wintypes.DWORD()
                kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                    self.request('CONTROL_INPUT_UNAVAILABLE')
                    self.initialized.set()
                    return
            else:
                initial_count = wintypes.DWORD()
                if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(initial_count), None):
                    self.request('PARENT_INPUT_EOF')
                    self.initialized.set()
                    return
            if kind == 2 and self.start_token is None and not self.accept_forwarded:
                return self._read_console(msvcrt)
            self.initialized.set()
            pending = ''
            while not self.closed.wait(0.03):
                if time.monotonic() >= self.deadline:
                    self.request('NATURAL_DEADLINE')
                    return
                value = ''
                self.input_kind = 'CONSOLE' if kind == 2 else 'PIPE'
                if kind == 2:
                    if msvcrt.kbhit():
                        value = msvcrt.getwch()
                        if value in ('\x00', '\xe0'):
                            if msvcrt.kbhit():
                                msvcrt.getwch()
                            continue
                        if value == '\x03':
                            self.request('USER_INTERRUPT')
                            return
                        safe_print('') if value in ('\r', '\n') else None
                else:
                    count = wintypes.DWORD()
                    if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(count), None):
                        self.request('PARENT_INPUT_EOF')
                        return
                    if count.value:
                        value = os.read(sys.stdin.fileno(), min(count.value, 4096)).decode('ascii', errors='replace')
                        if not value:
                            self.request('PARENT_INPUT_EOF')
                            return
                for char in value:
                    if char in '\r\n':
                        fields = pending.split(' ')
                        if self.start_token is not None and len(fields) in (2,7) and fields[:2] == ['GO',self.start_token]:
                            if len(fields) == 7:
                                absolute_deadline, interval = float(fields[2]), float(fields[3])
                                if (not math.isfinite(absolute_deadline) or not time.monotonic() < absolute_deadline <= time.monotonic()+1800
                                        or not .01 <= interval <= 5 or fields[4] not in ('0','1')
                                        or len(fields[5]) != 36 or not fields[5].startswith('run_')):
                                    self.request('SAFETY_CONTROL_FAILURE')
                                    return
                                configured = float(fields[6])
                                if not 1 <= configured <= 1800:
                                    self.request('SAFETY_CONTROL_FAILURE')
                                    return
                                self.deadline_seconds = configured
                                self.deadline = absolute_deadline
                                self.interval = interval
                                self.browser = fields[4] == '1'
                                self.review_alias = fields[5]
                            self.admitted.set()
                        elif self.accept_forwarded and len(fields) == 2 and fields[0] == 'STOP':
                            self.request(fields[1], 'PARENT')
                        elif pending.strip(' \t') == 'STOP':
                            self.request('OPERATOR_STOP', 'CONSOLE' if kind == 2 else 'PIPE')
                            safe_print('STOP received. Closing this app only...')
                        elif pending.strip(' \t'):
                            safe_print('Type STOP then Enter to close this app.')
                        pending = ''
                    elif char == '\b':
                        pending = pending[:-1]
                    elif len(pending) < 256:
                        pending += char
        except (OSError, ValueError):
            self.request('CONTROL_INPUT_ERROR')
            self.initialized.set()

    def start(self):
        self.thread.start()
        if not self.initialized.wait(2) or self.event.is_set():
            raise RuntimeError("Control input could not be initialized; no service was created.")

    def close(self):
        self.closed.set()
        if self.thread.ident is None:
            return True
        self.thread.join(2)
        return not self.thread.is_alive()


def verify_package(control=None, progress=None):
    lock = json.loads((ROOT / 'PACKAGE_INTEGRITY.json').read_text(encoding='utf-8'))
    for checked,row in enumerate(lock['files'],1):
        if control is not None and control.event.is_set():
            raise PreparationCancelled()
        relative = Path(row['path'])
        if relative.is_absolute() or relative.drive or '..' in relative.parts:
            raise ValueError('Invalid package manifest path')
        path = ROOT / relative
        if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
            raise ValueError('Required package file missing or replaced: ' + row['path'])
        content = path.read_bytes()
        if len(content) != row['bytes'] or hashlib.sha256(content).hexdigest().upper() != row['sha256']:
            raise ValueError('Package verification failed: ' + row['path'] + '. Extract the original ZIP again.')
        if control is not None:
            control.prepared_files_checked = checked
        if progress is not None:
            progress(checked,control)
    return lock['build_id']


class PreparationCancelled(Exception):
    pass


def classify_port_result(code):
    if code == 0: return 'CONNECTED'
    if code == 10061: return 'CONNECTION_REFUSED'
    if code in (10013,13): return 'ACCESS_DENIED'
    if code in (10060,110): return 'TIMEOUT'
    return 'OTHER_ERROR'


def observe_port(port):
    started = time.monotonic()
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as check:
            check.settimeout(3)
            code = check.connect_ex(('127.0.0.1',port))
        classification = classify_port_result(code)
    except (socket.timeout,TimeoutError):
        code, classification = None,'TIMEOUT'
    except OSError as error:
        code = error.winerror if getattr(error,'winerror',None) is not None else error.errno
        classification = classify_port_result(code)
    return {'classification':classification,'actual_errno':code,'duration_ms':round((time.monotonic()-started)*1000)}


def duplicate_page(metadata, build_id):
    import urllib.request
    import webbrowser
    value = json.loads(metadata.read_text(encoding='utf-8'))
    port = value.get('port')
    if value.get('build_id') != build_id or type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Another instance is preparing or stopping. Wait for its launch window.')
    url = 'http://127.0.0.1:' + str(port)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url + '/healthz', timeout=2) as response:
        health = json.load(response)
    if health.get('build_id') != build_id or health.get('instance_id') != value.get('instance_id'):
        raise ValueError('Existing instance identity could not be confirmed. No new server was started.')
    if not webbrowser.open(url):
        safe_print('Browser did not open. Open this local address: ' + url)
    safe_print('This package is already running. Reopened the same local page; no second business run was created.')


async def serve(run_dir, build_id, control, key, receipt, metadata, *, browser=True, interval=1.0, review=None):
    import asyncio
    if control.event.is_set():
        return
    import uvicorn
    from app.synthetic_demo import DemoConfiguration, create_synthetic_demo_app
    origins = {}
    for name in ('app', 'fastapi', 'starlette', 'pydantic', 'pydantic_core', 'uvicorn'):
        module = sys.modules[name]
        origin = Path(module.__file__).resolve()
        try:
            origins[name] = origin.relative_to(ROOT).as_posix()
        except ValueError:
            raise RuntimeError('Module outside this portable package') from None
    receipt['module_origins_relative'] = origins
    receipt['python_isolated'] = bool(sys.flags.isolated and sys.flags.no_user_site)
    if not receipt['python_isolated']:
        raise RuntimeError('Isolated interpreter required')
    sock = None
    serving = None
    watch = None
    app = None
    server = None

    try:
        if control.event.is_set():
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        sock.setblocking(False)
        port = sock.getsockname()[1]
        receipt['port'] = port
        receipt['socket_exclusive'] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
        app = create_synthetic_demo_app(
            DemoConfiguration(enabled=True, port=port, control_key=key, interval=interval),
            shutdown=control.request, build_id=build_id, instance_id=receipt["launcher_run_id"],
            review=review, stopping_requested=control.event.is_set,
            safety_status=lambda:{"default_seconds":1800,"remaining_seconds":max(0,control.deadline-time.monotonic()),
                                  "origin":"APP_LAUNCH","accelerated_test":control.deadline_seconds!=1800})
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, lifespan='on',
            proxy_headers=False, access_log=False, log_level='error',
            timeout_graceful_shutdown=8, timeout_keep_alive=2))

        async def watch_stop():
            while not control.event.is_set():
                if OUTPUT_FAILED.is_set():
                    control.request('SAFETY_CONTROL_FAILURE')
                    break
                await asyncio.sleep(0.03)
            receipt['stop_consumed_at'] = time.monotonic()
            receipt['stop_reason'] = control.reason
            if review is not None:
                review.stop(control.reason, stage='CONSUMED', acknowledged=True, cleanup=False)
            await app.state.synthetic_demo.stop()
            server.should_exit = True

        watch = asyncio.create_task(watch_stop())
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        while not serving.done() and not server.started:
            await asyncio.sleep(0.03)
        if server.started and not control.event.is_set():
            receipt.update(service_started=True, runtime_id=app.state.synthetic_demo.runtime_id,
                           ready_at=time.monotonic(), listener='127.0.0.1',
                           interpreter_relative='runtime/python.exe', build_id=build_id)
            public = {name:receipt[name] for name in ('build_id', 'runtime_id', 'port')}
            public['instance_id'] = receipt['launcher_run_id']
            atomic_json(metadata, public)
            atomic_json(run_dir / 'ready.json', public)
            url = 'http://127.0.0.1:' + str(port)
            safe_print('Home Orchestrator v2 | OFFLINE REPLAY | ' + build_id)
            safe_print('Local page: ' + url)
            # Operator-only display. Never saved in normal logs, URLs or receipts.
            safe_print('Local preview code: ' + key)
            safe_print('Unlock does not start a demo. Choose a scene, then explicitly Start.')
            safe_print('Type STOP then Enter to close this app. Safety limit: 30 minutes from app launch; scenes do not restart it.')
            if browser:
                import webbrowser
                try:
                    receipt['browser_dispatch'] = bool(webbrowser.open(url))
                except Exception:
                    receipt['browser_dispatch'] = False
                if not receipt['browser_dispatch']:
                    safe_print('Browser could not open. Use the local page address above.')
        await serving
        receipt['serve_returned'] = True
        life = server.lifespan
        receipt['lifespan_shutdown_complete'] = life.shutdown_event.is_set() and not life.shutdown_failed
        receipt['business_final'] = await app.state.synthetic_demo.status()
    finally:
        if serving is not None and not serving.done():
            control.request('SERVICE_ERROR')
            if app is not None:
                await app.state.synthetic_demo.stop()
            server.should_exit = True
            await serving
        if watch is not None:
            watch.cancel()
            try:
                await watch
            except asyncio.CancelledError:
                pass
        if sock is not None:
            sock.close()
            receipt['socket_closed'] = sock.fileno() == -1


def main(*, deadline_seconds=1800, browser=True, interval=1.0, control_client=False, preparation_hook=None):
    import msvcrt
    if os.name != 'nt':
        safe_print('This portable build supports Windows 11 x64 only.')
        return 1
    lock_stream = None
    control = None
    run_dir = None
    child = None
    last_run_path = None
    spawn_attempted = False
    lifecycle_review = None
    receipt = {'service_started':False, 'native_worker_exit_observed':False}
    code = 1
    try:
        if str(ROOT).startswith('\\\\'):
            raise ValueError('Extract to a writable local drive, not a network share.')
        if Path(sys.executable).resolve() != (ROOT / 'runtime/python.exe').resolve():
            raise ValueError('Use this package runtime through START_DEMO.cmd.')
        control = StopControl(deadline=deadline_seconds)
        # This fixed-schema PREPARING record precedes payload verification, and claims no verified payload.
        from app.safe_receipts import issue_alias
        from tools.review_adapter import LifecycleReview
        declared = json.loads((ROOT/'PACKAGE_INTEGRITY.json').read_text(encoding='utf-8'))
        run_alias = issue_alias('run')
        lifecycle_review = LifecycleReview(ROOT,run_alias.value,declared['build_id'],control,'launcher')
        try:
            control.start()
        except RuntimeError:
            if control.reason == 'OPERATOR_STOP':
                raise PreparationCancelled() from None
            raise
        build_id = verify_package(control,progress=preparation_hook)
        if control.event.is_set():
            raise PreparationCancelled()
        state = ROOT / '.local/local_only'
        for candidate in (state.parent,state):
            if candidate.is_symlink() or (candidate.exists() and candidate.resolve()!=candidate.absolute()):
                raise ValueError('REDIRECTED_LOCAL_STATE')
        state.parent.mkdir(exist_ok=True)
        state.mkdir(exist_ok=True)
        if state.is_symlink() or state.resolve() != state.absolute():
            raise ValueError('Local data path is redirected; use a fresh local extraction.')
        for name in ('instance.lock','instance.json','last-run.json','runs'):
            local_object = state/name
            if (local_object.exists() or local_object.is_symlink()) and local_object.resolve()!=local_object.absolute():
                raise ValueError('Local control object is redirected; preserve it for review.')
        lock_stream = (state / 'instance.lock').open('a+b')
        if lock_stream.seek(0, 2) == 0:
            lock_stream.write(b'0')
            lock_stream.flush()
        lock_stream.seek(0)
        try:
            msvcrt.locking(lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            duplicate_page(state / 'instance.json', build_id)
            code = 0
            return 0
        last_run_path = state/'last-run.json'
        if last_run_path.exists():
            prior = json.loads(last_run_path.read_text(encoding='utf-8'))
            if prior.get('status')!='CLOSED' or not (prior.get('native_worker_exit_observed') or prior.get('worker_creation_attempted') is False):
                raise ValueError('HOLD: this package has an unconfirmed prior worker. Preserve its records; do not start another instance.')
        elif (state/'instance.json').exists() or ((state/'runs').is_dir() and any((state/'runs').iterdir())):
            raise ValueError('HOLD: prior state has no retained native exit record. No new worker was created.')
        run_id = uuid4().hex
        runs = state / 'runs'
        runs.mkdir(exist_ok=True)
        run_dir = runs / run_id
        run_dir.mkdir(exist_ok=False)
        with (run_dir / 'preview-control.key').open('x', encoding='ascii') as secret:
            secret.write(secrets.token_hex(32).upper())
        receipt.update(build_id=build_id, launcher_run_id=run_id, started_at=time.monotonic())
        from tools.native_supervisor import OwnedProcess
        receipt['review_run_alias'] = run_alias.value
        atomic_json(last_run_path,{'build_id':build_id,'run_id':run_id,'status':'STARTING',
            'worker_creation_attempted':False,'native_worker_exit_observed':False})
        command = [sys.executable,'-I',str(ROOT/'tools/portable_worker.py'),run_id]
        spawn_attempted = True
        atomic_json(last_run_path,{'build_id':build_id,'run_id':run_id,'status':'STARTING',
            'worker_creation_attempted':True,'native_worker_exit_observed':False})
        child = OwnedProcess(command,str(ROOT))
        lifecycle_review.worker_created = True
        atomic_json(run_dir/'ownership.json',{'build_id':build_id,'run_id':run_id,
            'identity':child.identity,'identity_error':child.identity_error})
        receipt['native_worker_identity'] = child.identity
        receipt['native_identity_error'] = child.identity_error
        if child.identity_error is None:
            child.send_line('GO '+run_id+' '+repr(control.deadline)+' '+str(interval)+' '+str(int(browser))+' '+run_alias.value+' '+str(deadline_seconds))
        else:
            safe_print('Worker identity check failed. Waiting for its bounded pre-service exit.')
        ready_path = run_dir/'ready.json'
        startup_deadline = time.monotonic()+20
        stop_sent = False
        displayed = False
        while child.process.poll() is None:
            if OUTPUT_FAILED.is_set():
                control.request('SAFETY_CONTROL_FAILURE')
            if control.event.is_set() and not stop_sent:
                child.send_stop(control.reason)
                if lifecycle_review is not None:
                    lifecycle_review.stop(control.reason,stage='REQUESTED',acknowledged=False,cleanup=False,forwarded=True)
                if control_client:
                    safe_print('PRIVATE_ACK '+json.dumps({'kind':'LAUNCHER_INPUT_CONSUMED','reason':control.reason},separators=(',',':')))
                receipt['console_stop_input_at'] = control.requested_at
                receipt['worker_stop_sent_at'] = child.stop_input_at
                receipt['launcher_stop_reason'] = control.reason
                stop_sent = True
            if not displayed and ready_path.is_file() and child.key:
                ready = json.loads(ready_path.read_text(encoding='utf-8'))
                if ready.get('instance_id') != run_id or ready.get('build_id') != build_id:
                    control.request('READY_IDENTITY_ERROR')
                    continue
                receipt.update(service_started=True,ready_at=time.monotonic(),runtime_id=ready['runtime_id'],port=ready['port'])
                safe_print('Home Orchestrator v2 | OFFLINE REPLAY | '+build_id)
                safe_print('Local page: http://127.0.0.1:'+str(ready['port']))
                safe_print('Local preview code: '+child.key)
                safe_print('Unlock does not start a demo. Choose a scene, then explicitly Start.')
                safe_print('Type STOP then Enter to close this app. Safety limit: 30 minutes from app launch; scenes do not restart it.')
                if control_client:
                    safe_print('PRIVATE_READY '+json.dumps({'port':ready['port'],'runtime_id':ready['runtime_id'],'run_alias':run_alias.value},separators=(',',':')))
                displayed = True
            if not displayed and time.monotonic()>startup_deadline and not stop_sent:
                control.request('STARTUP_DEADLINE')
            if stop_sent and time.monotonic()-child.stop_input_at>20:
                receipt['normal_stop_deadline_failed'] = True
                safe_print('HOLD: normal stop was not confirmed. No process will be forced closed.')
                # Keep the original owned handle. Natural/cooperative worker exit remains available.
                result = child.wait(1820)
                receipt['native_worker_result'] = result
                break
            time.sleep(.03)
        if 'native_worker_result' not in receipt:
            receipt['native_worker_result'] = child.wait(2)
        receipt['native_worker_exit_observed'] = True
        worker = json.loads((run_dir/'worker-result.json').read_text(encoding='utf-8'))
        receipt['worker_result'] = worker
        port = receipt.get('port')
        if port is not None:
            receipt['port_observation'] = observe_port(port)
            receipt['port_released'] = receipt['port_observation']['classification'] == 'CONNECTION_REFUSED'
            if lifecycle_review is not None:
                if not lifecycle_review.port(**receipt['port_observation'],socket_closed=bool(worker.get('socket_closed'))):
                    receipt['review_evidence_missing'] = True
        code = 0 if (receipt['service_started'] and child.identity_error is None
            and receipt['native_worker_result']['native_exit_code']==0
            and worker.get('return_code')==0 and receipt.get('port_released')
            and all(worker.get(flag) for flag in ('socket_closed','serve_returned','lifespan_shutdown_complete','loop_closed','control_joined'))
            and receipt['native_worker_result'].get('output_drain_joined')
            and not receipt.get('normal_stop_deadline_failed')
            and worker.get('stop_reason') in {'OPERATOR_STOP','PAGE_STOP'}
            and control.reason in {None,'OPERATOR_STOP'}) else 1
        if (receipt['service_started'] and receipt['native_worker_result']['native_exit_code']==2
                and worker.get('stop_reason')=='NATURAL_DEADLINE' and worker.get('return_code')==2
                and all(worker.get(flag) for flag in ('socket_closed','serve_returned','lifespan_shutdown_complete','loop_closed','control_joined'))
                and receipt['native_worker_result'].get('output_drain_joined')
                and receipt.get('port_released') and control.reason in {None,'NATURAL_DEADLINE'}):
            code = 2
    except PreparationCancelled:
        receipt['preparation_cancelled'] = True
        receipt['worker_creation_attempted'] = False
        receipt['initiating_reason'] = control.reason if control else 'PREPARATION_CANCELLED'
        code = 3
    except KeyboardInterrupt:
        receipt['error'] = 'USER_INTERRUPT'
        if child is not None and child.process.poll() is None:
            child.send_stop('USER_INTERRUPT')
            receipt['worker_stop_sent_at'] = child.stop_input_at
            receipt['launcher_stop_reason'] = 'USER_INTERRUPT'
            try:
                receipt['native_worker_result'] = child.wait(20)
                receipt['native_worker_exit_observed'] = True
            except Exception:
                receipt['status'] = 'NEW_OWNED_PROCESS_HOLD'
    except Exception as exc:
        receipt['error'] = type(exc).__name__
        safe_print('Startup or shutdown failed ('+type(exc).__name__+'). No other process was stopped.')
        if isinstance(exc,ValueError):
            safe_print(str(exc))
        if child is not None:
            try:
                if child.process.poll() is None and child.identity_error is None:
                    child.send_stop('LAUNCHER_ERROR')
                    receipt['worker_stop_sent_at'] = child.stop_input_at
                    receipt['launcher_stop_reason'] = 'LAUNCHER_ERROR'
                receipt['native_worker_result'] = child.wait(20)
                receipt['native_worker_exit_observed'] = True
            except Exception:
                receipt['status'] = 'NEW_OWNED_PROCESS_HOLD'
    finally:
        if control is not None:
            receipt['input_thread_joined'] = control.close()
            if not receipt['input_thread_joined']:
                code = 1
        if lifecycle_review is not None:
            if receipt.get('native_worker_exit_observed') and 'native_worker_result' in receipt:
                # Same retained handle observation, including exception exits; emitted exactly once.
                if not lifecycle_review.native(receipt['native_worker_result']['native_exit_code']):
                    receipt['review_evidence_missing'] = True
            observed_reason=receipt.get('worker_result',{}).get('stop_reason') or 'NONE'
            worker_facts=receipt.get('worker_result',{})
            cleanup=(bool(receipt.get('preparation_cancelled')) or
                (bool(receipt.get('native_worker_exit_observed') and receipt.get('port_released'))
                 and all(worker_facts.get(flag) for flag in ('socket_closed','serve_returned','lifespan_shutdown_complete','loop_closed','control_joined'))
                 and bool(receipt.get('native_worker_result',{}).get('output_drain_joined'))))
            cleanup=cleanup and bool(receipt.get('input_thread_joined'))
            final_review_saved=lifecycle_review.stop(control.reason if control else None,stage='CLEANED' if cleanup else 'EVIDENCE_MISSING',
                acknowledged=worker_facts.get('stop_consumed_at') is not None,cleanup=cleanup,
                forwarded_reason=receipt.get('launcher_stop_reason','NONE'),
                observed_reason='PREPARATION_CANCELLED' if receipt.get('preparation_cancelled') else observed_reason)
            if not final_review_saved or receipt.get('review_evidence_missing'):
                receipt['review_evidence_missing'] = True
                code = 1
        receipt['return_code'] = code
        receipt['completed_at'] = time.monotonic()
        if run_dir is not None:
            try:
                atomic_json(run_dir/'result.json',receipt)
            except OSError:
                safe_print('Local receipt could not be written. Do not infer receipt success.')
                code = 1
        if last_run_path is not None and run_dir is not None:
            try:
                closed = receipt.get('native_worker_exit_observed',False) or not spawn_attempted
                atomic_json(last_run_path,{'build_id':receipt.get('build_id'),
                    'run_id':receipt.get('launcher_run_id'),'status':'CLOSED' if closed else 'HOLD',
                    'worker_creation_attempted':spawn_attempted,
                    'native_worker_exit_observed':receipt.get('native_worker_exit_observed',False),
                    'worker_identity':receipt.get('native_worker_identity'),
                    'native_worker_exit_code':receipt.get('native_worker_result',{}).get('native_exit_code'),
                    'return_code':code})
            except OSError:
                code = 1
                safe_print('HOLD: final lifecycle record could not be saved. Preserve this extraction.')
        if lock_stream is not None:
            lock_stream.close()
        if code == 0:
            safe_print('App closed normally after a requested stop.')
        elif code == 2:
            safe_print('Reached the safety limit; this instance has closed. This was not an operator STOP test.')
        elif code == 3:
            safe_print('Preparation cancelled; no product worker was created.')
        elif receipt.get('review_evidence_missing') and receipt.get('native_worker_exit_observed'):
            safe_print('App closed, but review receipts are incomplete. Preserve this extraction.')
        else:
            safe_print('App could not confirm complete cleanup. Preserve this instance for review.')
    return code


if __name__ == '__main__':
    sys.exit(main())
