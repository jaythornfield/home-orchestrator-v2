"""Real AppReview + ASGI + actual safe-file IO; all credentials are fictional."""
import asyncio,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from app.synthetic_demo import DemoConfiguration,create_synthetic_demo_app
from app import safe_receipts as sr
from tools.portable_launcher import StopControl
from tools.review_adapter import AppReview,LifecycleReview
from tools.export_review import collect_plans
KEY='A'*64
BASE='http://127.0.0.1:18474'

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.manifest={'build_id':'HO2-R6-UNIT','source_aggregate_sha256':'A'*64}
        (self.root/'PACKAGE_INTEGRITY.json').write_text(json.dumps(self.manifest),encoding='utf-8')
        self.control=StopControl()
        self.run_alias=sr.issue_alias('run').value
        self.review=AppReview(self.root,self.run_alias,'HO2-R6-UNIT',self.control)
        self.stops=[]
        self.app=create_synthetic_demo_app(DemoConfiguration(enabled=True,port=18474,interval=.01,control_key=KEY),
            review=self.review,shutdown=self.stops.append,build_id='HO2-R6-UNIT')
        self.life=self.app.router.lifespan_context(self.app);await asyncio.wait_for(self.life.__aenter__(),3)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app,client=('127.0.0.1',32100)),
            base_url=BASE,headers={'Origin':BASE},timeout=3)
        self.runtime=(await self.req('GET','/api/demo/status')).json()['runtime_id']
    async def asyncTearDown(self):
        await self.client.aclose();await asyncio.wait_for(self.life.__aexit__(None,None,None),3);self.temp.cleanup()
    async def req(self,method,path,**kwargs):
        async with asyncio.timeout(3):return await self.client.request(method,path,**kwargs)
    async def unlock(self):
        self.assertEqual((await self.req('POST','/api/demo/session',json={'runtime_id':self.runtime,'preview_key':KEY})).status_code,200)
    def records(self):
        return [json.loads(line) for line in (self.review.root/'events.jsonl').read_text(encoding='ascii').splitlines()]
    async def finish(self):
        for _ in range(100):
            if self.app.state.synthetic_demo.phase=='FINISHED':return
            await asyncio.sleep(.01)
        self.fail('BOUNDED_SOURCE_COMPLETION')
    def deny_main_event_writes(self):
        original=Path.open;target=self.review.root/'events.jsonl'
        def controlled(path,*args,**kwargs):
            mode=args[0] if args else kwargs.get('mode','r')
            if path==target and mode=='ab':raise OSError('SYNTHETIC_WRITE_FAILURE')
            return original(path,*args,**kwargs)
        return patch.object(Path,'open',controlled)
    async def test_invalid_session_never_marks_start_entered(self):
        response=await self.req('POST','/api/demo/session',json={'runtime_id':self.runtime})
        self.assertEqual(response.status_code,422)
        rows=[r['fields'] for r in self.records() if r['event']=='REQUEST' and r['fields']['http_status']==422]
        self.assertTrue(rows);self.assertTrue(all(r['route']=='SESSION' and not r['entered_session'] and not r['entered_start'] for r in rows))
    async def test_invalid_auxiliary_never_marks_actual_session_or_start(self):
        response=await self.req('POST','/api/demo/client-event',json={'runtime_id':self.runtime})
        self.assertEqual(response.status_code,422)
        rows=[r['fields'] for r in self.records() if r['event']=='REQUEST' and r['fields']['http_status']==422]
        self.assertTrue(rows);self.assertTrue(all(r['route']=='CLIENT_EVENT' and not r['entered_session'] and not r['entered_start'] for r in rows))
        await self.unlock();self.assertEqual(self.app.state.synthetic_demo.phase,'NOT_STARTED')
    async def test_wrong_code_has_actual_session_entry_but_no_start(self):
        response=await self.req('POST','/api/demo/session',json={'runtime_id':self.runtime,'preview_key':'B'*64})
        self.assertEqual(response.status_code,401)
        rows=[r['fields'] for r in self.records() if r['event']=='REQUEST' and r['fields']['http_status']==401]
        self.assertTrue(rows);self.assertTrue(all(r['entered_session'] and not r['entered_start'] for r in rows))
    async def test_real_writer_failure_creates_fixed_emergency_and_stop_still_works(self):
        await self.unlock()
        with self.deny_main_event_writes():
            response=await self.req('POST','/api/demo/start',json={'runtime_id':self.runtime,'profile':'S5_THIRD_A'})
            self.assertEqual(response.status_code,503)
            before=(await self.app.state.synthetic_demo.status())['autonomous_effects']
            await asyncio.sleep(.05)
            self.assertEqual((await self.app.state.synthetic_demo.status())['autonomous_effects'],before)
            stop=await self.req('POST','/api/demo/shutdown',json={'runtime_id':self.runtime})
            self.assertEqual(stop.status_code,200)
            await asyncio.sleep(.25)
            self.assertEqual(self.stops,['PAGE_STOP'])
        emergency=json.loads((self.review.root/'emergency.json').read_text(encoding='ascii'))['fields']
        self.assertEqual((emergency['initiating_reason'],emergency['failure_stage'],emergency['failure_code']),('AUDIT_FAILURE','WRITE','WRITE_FAILED'))
    async def test_scene_audit_failure_is_503_without_half_committed_runtime(self):
        await self.unlock()
        self.assertEqual((await self.req('POST','/api/demo/start',json={'runtime_id':self.runtime,'profile':'S5_THIRD_A'})).status_code,200)
        await self.finish()
        old=self.app.state.synthetic_demo
        original_emit=self.review.writer.emit
        def fail_scene(event,origin,**fields):
            if event=='SCENE':
                self.review.writer.last_error='WRITE_FAILED';self.review.writer.failed=True;return False
            return original_emit(event,origin,**fields)
        with patch.object(self.review.writer,'emit',side_effect=fail_scene):
            response=await self.req('POST','/api/demo/reset',json={'runtime_id':self.runtime})
        self.assertEqual(response.status_code,503)
        self.assertIs(self.app.state.synthetic_demo,old)
        self.assertEqual(self.app.state.synthetic_demo.runtime_id,self.runtime)
        self.assertEqual((await old.status())['autonomous_effects'],3)
    async def test_confirmed_versions_belong_to_each_action(self):
        await self.unlock();await self.req('POST','/api/demo/start',json={'runtime_id':self.runtime,'profile':'S5_THIRD_A'});await self.finish()
        rows=[r['fields'] for r in self.records() if r['event']=='BUSINESS' and r['fields'].get('status')=='APPLIED']
        confirmations={r['root_alias']:r['confirmed_version'] for r in rows}
        self.assertEqual(sorted(confirmations.values()),[1,2,3])
    async def test_export_refuses_different_worker_context_in_same_directory(self):
        LifecycleReview(self.root,self.run_alias,'HO2-R6-UNIT',self.control,'launcher')
        expected=dict(self.review.writer.context);expected.pop('schema_version');expected.pop('run_alias')
        plans=collect_plans(self.root/'.local/review_safe',expected)
        self.assertEqual(len(plans),2)
        alien=sr.issue_alias('run').value
        # Change only a fictional safe identity file in a new synthetic fixture.
        identity=self.review.root/'identity.json';value=json.loads(identity.read_text(encoding='ascii'));value['run_alias']=alien
        identity.write_text(json.dumps(value),encoding='ascii')
        with self.assertRaises(sr.ReceiptError):collect_plans(self.root/'.local/review_safe',expected)
    async def test_UNC_and_ADS_are_lexically_rejected_without_filesystem_query(self):
        for raw in ('\\\\fictional.invalid\\share\\review_safe','\\\\?\\C:\\review_safe','C:\\review_safe\\events.jsonl:stream'):
            with patch.object(Path,'lstat') as metadata,patch.object(Path,'exists') as existence:
                with self.assertRaises(sr.ReceiptError):sr._safe_path(raw,review=True)
                metadata.assert_not_called();existence.assert_not_called()
    async def test_unstarted_control_close_does_not_join_unstarted_thread(self):
        self.assertTrue(StopControl().close())

    async def test_mismatched_scene_received_record_is_false_before_rejection(self):
        response=await self.req('POST','/api/demo/session',json={'runtime_id':'not-this-scene','preview_key':KEY})
        self.assertEqual(response.status_code,410)
        rows=[r['fields'] for r in self.records() if r['event']=='REQUEST' and r['fields']['route']=='SESSION']
        self.assertTrue(rows);self.assertTrue(all(not row['scene_match'] for row in rows))
    async def test_reset_has_no_second_required_write_after_commit_record(self):
        await self.unlock();await self.req('POST','/api/demo/start',json={'runtime_id':self.runtime,'profile':'S5_THIRD_A'});await self.finish()
        original=self.review.http
        def reject_second_reset_write(path,status,**facts):
            if path=='/api/demo/reset':raise OSError('SYNTHETIC_SECOND_WRITE_MUST_NOT_BE_CALLED')
            return original(path,status,**facts)
        with patch.object(self.review,'http',side_effect=reject_second_reset_write):
            response=await self.req('POST','/api/demo/reset',json={'runtime_id':self.runtime})
        self.assertEqual(response.status_code,200)
        self.assertNotEqual(self.app.state.synthetic_demo.runtime_id,self.runtime)
    async def test_parent_stop_cause_keeps_observed_worker_reason_separate(self):
        parent=LifecycleReview(self.root,self.run_alias,'HO2-R6-UNIT',self.control,'launcher')
        self.control.request('OPERATOR_STOP','PIPE')
        parent.worker_created=True
        self.assertTrue(parent.stop('OPERATOR_STOP',stage='CLEANED',acknowledged=True,cleanup=True,
                                   forwarded_reason='OPERATOR_STOP',observed_reason='NATURAL_DEADLINE'))
        value=json.loads((parent.root/'events.jsonl').read_text(encoding='ascii').splitlines()[-1])['fields']
        self.assertEqual((value['initiating_reason'],value['forwarded_reason'],value['observed_reason']),
                         ('OPERATOR_STOP','OPERATOR_STOP','NATURAL_DEADLINE'))


    async def test_source_admission_failure_precedes_first_effect(self):
        await self.unlock()
        original=self.review.writer.emit
        def fail_source(event,origin,**fields):
            if event=='SOURCE':
                self.review.writer.last_error='WRITE_FAILED';self.review.writer.failed=True;return False
            return original(event,origin,**fields)
        with patch.object(self.review.writer,'emit',side_effect=fail_source):
            response=await self.req('POST','/api/demo/start',json={'runtime_id':self.runtime,'profile':'S5_THIRD_A'})
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json(),{'error':'local_audit_unavailable','phase':'FAILED_HOLD',
                'autonomous_effects':0,'distinct_command_roots':0,'per_action_approvals':0})
            await asyncio.sleep(.05)
            again=await self.req('GET','/api/demo/status')
            self.assertEqual(again.status_code,503)
            self.assertEqual(again.json()['autonomous_effects'],0)
        self.assertEqual((await self.app.state.synthetic_demo.status())['autonomous_effects'],0)

    async def test_source_admission_is_recorded_before_each_effect(self):
        await self.unlock();await self.req('POST','/api/demo/start',json={'runtime_id':self.runtime,'profile':'S5_THIRD_A'});await self.finish()
        rows=self.records()
        admissions=[r for r in rows if r['event']=='SOURCE']
        self.assertTrue(admissions)
        self.assertEqual([r['fields']['tick'] for r in admissions],sorted({r['fields']['tick'] for r in admissions}))
        for row in rows:
            if row['event']=='BUSINESS' and row['fields'].get('status')=='APPLIED':
                self.assertTrue(any(s['seq']<row['seq'] for s in admissions))
        self.assertTrue(all(r['fields']['profile']=='S5_THIRD_A' and r['fields']['admitted'] for r in admissions))

if __name__=='__main__':unittest.main(verbosity=2)

