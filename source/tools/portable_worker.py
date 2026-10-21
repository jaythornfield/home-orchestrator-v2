"""One private worker; source-safe receipts are separate from private control state."""
import sys
import time
from tools.portable_launcher import ROOT, StopControl, serve, atomic_json, verify_package, PreparationCancelled

def main():
    run_id = sys.argv[1] if len(sys.argv)==2 else ''
    if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):
        return 1
    run_dir = ROOT/'.local/local_only/runs'/run_id
    if not run_dir.is_dir() or run_dir.resolve()!=run_dir.absolute():
        return 1
    control = StopControl(deadline=15,start_token=run_id,accept_forwarded=True)
    receipt = {'launcher_run_id':run_id,'service_started':False,'socket_closed':True,'serve_returned':False}
    code = 1
    review = None
    try:
        control.start()
        print('WORKER_CONTROL_READY',flush=True)
        deadline = time.monotonic()+10
        while not control.admitted.wait(.03) and not control.event.is_set() and time.monotonic()<deadline:
            pass
        if not control.admitted.is_set() or control.event.is_set():
            control.request('STARTUP_ADMISSION_DEADLINE')
            raise PreparationCancelled()
        build_id = verify_package(control)
        key = (run_dir/'preview-control.key').read_text(encoding='ascii')
        if len(key)!=64 or any(c not in '0123456789ABCDEF' for c in key):
            raise ValueError('INVALID_NEW_LOCAL_CODE_FORMAT')
        receipt['build_id'] = build_id
        receipt['review_run_alias'] = control.review_alias
        from tools.review_adapter import AppReview
        review = AppReview(ROOT,control.review_alias,build_id,control)
        import asyncio
        with asyncio.Runner() as runtime:
            runtime.run(serve(run_dir,build_id,control,key,receipt,ROOT/'.local/local_only/instance.json',
                              browser=control.browser,interval=control.interval,review=review))
        receipt['loop_closed'] = True
        cleaned = (receipt['service_started'] and receipt.get('lifespan_shutdown_complete')
                   and receipt['serve_returned'] and receipt['socket_closed'])
        code = (0 if control.reason in {'OPERATOR_STOP','PAGE_STOP'} else
                2 if control.reason=='NATURAL_DEADLINE' else 1) if cleaned else 1
    except BaseException as error:
        receipt['error_type'] = type(error).__name__
        code = 1
    finally:
        receipt['control_joined'] = control.close()
        if not receipt['control_joined']:
            code = 1
        receipt.update(stop_reason=control.reason,stop_requested_at=control.requested_at,
                       return_code=code,completed_at=time.monotonic())
        if review is not None:
            cleanup=all(bool(receipt.get(flag)) for flag in ('loop_closed','socket_closed','serve_returned','lifespan_shutdown_complete','control_joined'))
            review.stop(control.reason,stage='CLEANED' if cleanup else 'EVIDENCE_MISSING',
                        acknowledged=receipt.get('stop_consumed_at') is not None,cleanup=cleanup)
        try:
            atomic_json(run_dir/'worker-result.json',receipt)
        except OSError:
            code = 1
    return code

if __name__ == '__main__':
    sys.exit(main())
