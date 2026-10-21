"""Actual ASGI lifespan tests; no hand-written HTTP fixture and no listener."""
import asyncio
import unittest
import uuid
import httpx
from app.synthetic_demo import DemoConfiguration, create_synthetic_demo_app, COOKIE

KEY = 'A' * 64
BASE = 'http://127.0.0.1:18474'


class BoundedASGIClient(httpx.AsyncClient):
    async def request(self, *args, **kwargs):
        # httpx transport timeout alone does not bound an in-process ASGI await.
        async with asyncio.timeout(3):
            return await super().request(*args, **kwargs)


class BackendAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.audit_broken = False
        self.stops = []
        self.shutdown_flag = False
        def sink(value):
            if self.audit_broken:
                raise OSError('injected audit failure')
            self.events.append(value)
        self.app = create_synthetic_demo_app(DemoConfiguration(enabled=True, port=18474,
            interval=0.01, control_key=KEY), audit=sink, shutdown=self.stops.append,
            build_id='BACKEND_TEST_ONLY', stopping_requested=lambda:self.shutdown_flag)
        self.life = self.app.router.lifespan_context(self.app)
        await asyncio.wait_for(self.life.__aenter__(), 3)
        transport = httpx.ASGITransport(app=self.app, client=('127.0.0.1', 32100))
        self.client = BoundedASGIClient(transport=transport, base_url=BASE,
            headers={'Origin':BASE}, timeout=3)
        self.runtime = (await self.status())['runtime_id']

    async def asyncTearDown(self):
        await self.client.aclose()
        await asyncio.wait_for(self.life.__aexit__(None, None, None), 3)

    async def status(self):
        response = await self.client.get('/api/demo/status')
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def unlock(self):
        response = await self.client.post('/api/demo/session', json={'runtime_id':self.runtime,'preview_key':KEY})
        self.assertEqual(response.status_code, 200)

    async def start(self, profile='S5_THIRD_A'):
        response = await self.client.post('/api/demo/start', json={'runtime_id':self.runtime,
            'profile':profile,'correlation_id':str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def finish(self):
        for _ in range(100):
            value = await self.status()
            if value['phase'] in {'FINISHED','FAILED_HOLD'}:
                return value
            await asyncio.sleep(.01)
        self.fail('Source did not finish within a bounded second')

    async def test_T05_unlock_has_no_source_or_effect(self):
        await self.unlock()
        for _ in range(3):
            await self.client.get('/')
            await self.unlock()
        await asyncio.sleep(30)
        value = await self.status()
        self.assertEqual(value['phase'], 'NOT_STARTED')
        self.assertEqual((value['distinct_command_roots'],value['autonomous_effects']),(0,0))
        self.assertFalse(any(e['event'].startswith('source_') for e in self.events))

    async def test_T06_T07_concurrent_start_same_world_three_real_roots(self):
        await self.unlock()
        results = await asyncio.gather(*(self.start() for _ in range(4)))
        self.assertEqual(len({r['runtime_id'] for r in results}),1)
        value = await self.finish()
        self.assertEqual((value['distinct_command_roots'],value['autonomous_effects'],value['per_action_approvals']),(3,3,0))
        self.assertEqual([a['effect'] for a in value['actions']],['COMFORT_A','COMFORT_B','COMFORT_A'])
        self.assertEqual([a['confirmed_version'] for a in value['actions']],[1,2,3])
        self.assertTrue(all(a['status']=='APPLIED' and a['submissions']==1 for a in value['actions']))
        self.assertEqual(sum(e['event']=='source_created' for e in self.events),1)

    async def test_T08_objection_lifts_without_new_approval(self):
        # Preserve the original state assertion; give the ASGI poller a real observable HOLD window.
        self.app.state.synthetic_demo.interval = .1
        await self.unlock()
        await self.start('S5_EXACT_OBJECTION_LIFT')
        observed = []
        for _ in range(100):
            value = await self.status()
            observed.append(value)
            if value['phase']=='FINISHED':
                break
            await asyncio.sleep(.005)
        diagnostic = {'polled':[(v['authorization']['tick'],v['autonomous_effects'],v['safety_decision']) for v in observed if v['authorization']],
            'audited':[(e['snapshot']['authorization']['tick'],e['snapshot']['autonomous_effects'],e['snapshot']['safety_decision']) for e in self.events if e['event']=='source_state']}
        self.assertTrue(any(v['authorization'] and v['authorization']['tick'] in (5,6)
            and v['autonomous_effects']==2 and 'HOLD' in v['safety_decision'] for v in observed),diagnostic)
        self.assertEqual(observed[-1]['autonomous_effects'],3)
        self.assertEqual(observed[-1]['per_action_approvals'],0)

    async def test_T08_guest_child_unknown_independent(self):
        await self.unlock()
        for profile in ['S5_EXACT_GUEST','S5_EXACT_CHILD','S5_THIRD_A_UNKNOWN']:
            await self.start(profile)
            value = await self.finish()
            self.assertEqual(value['autonomous_effects'],2)
            if profile.endswith('UNKNOWN'):
                pending = value['actions'][-1]
                self.assertEqual(pending['submissions'],1)
                self.assertNotEqual(pending['status'],'APPLIED')
                self.assertLessEqual(pending['queries'],2)
            else:
                self.assertEqual(value['distinct_command_roots'],2)
                self.assertIn('HOLD',value['safety_decision'])
            old = self.runtime
            reset = await self.client.post('/api/demo/reset',json={'runtime_id':old})
            self.assertEqual(reset.status_code,200)
            self.runtime = reset.json()['runtime_id']
            self.assertNotEqual(old,self.runtime)
            self.assertEqual((await self.status())['autonomous_effects'],0)
            await self.unlock()
        self.assertEqual(len(self.app.state.archived_scenes),3)

    async def test_T09_T16_input_origin_old_runtime_and_cookie_boundaries(self):
        for bad in ['中文',' ', 'x'*129,'incorrect-code']:
            r = await self.client.post('/api/demo/session',json={'runtime_id':self.runtime,'preview_key':bad})
            self.assertIn(r.status_code,(401,422))
            self.assertNotIn(bad,r.text)
        r = await self.client.post('/api/demo/session',content='not-json',headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,422)
        await self.unlock()
        r = await self.client.post('/api/demo/start',json={'runtime_id':'old-run','profile':'S5_THIRD_A'})
        self.assertEqual(r.status_code,410)
        r = await self.client.post('/api/demo/shutdown',json={'runtime_id':'old-run'})
        self.assertEqual(r.status_code,410)
        self.assertEqual(self.stops,[])
        r = await self.client.post('/api/demo/start',json={'runtime_id':self.runtime},headers={'Origin':'http://evil.invalid'})
        self.assertEqual(r.status_code,403)
        self.assertEqual((await self.status())['autonomous_effects'],0)

    async def test_T10_T12_server_expiry_reunlock_no_start_and_page_stop(self):
        self.app.state.session_ttl = .01
        await self.unlock()
        await asyncio.sleep(.02)
        self.assertFalse((await self.status())['control_unlocked'])
        response = await self.client.post('/api/demo/start',json={'runtime_id':self.runtime})
        self.assertEqual(response.status_code,401)
        self.app.state.session_ttl = 1800
        await self.unlock()
        self.assertEqual((await self.status())['distinct_command_roots'],0)
        response = await self.client.post('/api/demo/shutdown',json={'runtime_id':self.runtime})
        self.assertEqual(response.status_code,200)
        await asyncio.sleep(.25)
        self.assertEqual(self.stops,['PAGE_STOP'])
        response = await self.client.post('/api/demo/start',json={'runtime_id':self.runtime})
        self.assertEqual(response.status_code,409)

    async def test_T14_audit_failure_blocks_effects_but_stop_survives(self):
        await self.unlock()
        await self.start()
        self.audit_broken = True
        await asyncio.sleep(.06)
        response = await self.client.get('/api/demo/status')
        self.assertEqual(response.status_code,503)
        value = response.json()
        self.assertEqual(set(value),{'error','phase','autonomous_effects','distinct_command_roots','per_action_approvals'})
        before = value['autonomous_effects']
        self.assertEqual(value['phase'],'FAILED_HOLD')
        self.assertLess(before,3)
        await asyncio.sleep(.05)
        response = await self.client.get('/api/demo/status')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['autonomous_effects'],before)
        r = await self.client.post('/api/demo/start',json={'runtime_id':self.runtime})
        self.assertEqual(r.status_code,503)
        r = await self.client.post('/api/demo/shutdown',json={'runtime_id':self.runtime})
        self.assertEqual(r.status_code,200)
        await asyncio.sleep(.25)
        self.assertEqual(self.stops,['PAGE_STOP'])

    async def test_T12_T16_console_intent_blocks_new_source_and_reset(self):
        await self.unlock()
        await self.start()
        self.shutdown_flag = True
        await self.app.state.synthetic_demo.stop()
        before = (await self.status())['autonomous_effects']
        reset = await self.client.post('/api/demo/reset',json={'runtime_id':self.runtime})
        self.assertEqual(reset.status_code,409)
        start = await self.client.post('/api/demo/start',json={'runtime_id':self.runtime})
        self.assertEqual(start.status_code,409)
        await asyncio.sleep(.05)
        value = await self.status()
        self.assertEqual(value['runtime_id'],self.runtime)
        self.assertEqual(value['autonomous_effects'],before)
        self.assertEqual(value['service_state'],'STOPPING')

    async def test_T16_reset_rechecks_late_console_intent(self):
        await self.unlock()
        await self.start()
        await self.finish()
        old = self.app.state.synthetic_demo
        original_stop = old.stop
        entered, release = asyncio.Event(), asyncio.Event()
        async def gated_stop():
            entered.set()
            await asyncio.wait_for(release.wait(),1)
            await original_stop()
        old.stop = gated_stop
        pending = asyncio.create_task(self.client.post('/api/demo/reset',json={'runtime_id':self.runtime}))
        await asyncio.wait_for(entered.wait(),1)
        self.shutdown_flag = True
        release.set()
        response = await asyncio.wait_for(pending,3)
        self.assertEqual(response.status_code,409)
        self.assertEqual((await self.status())['runtime_id'],self.runtime)

    async def test_T16_old_start_queued_across_reset_is_rejected(self):
        await self.unlock()
        await self.start()
        await self.finish()
        old = self.app.state.synthetic_demo
        original_stop = old.stop
        entered, release = asyncio.Event(), asyncio.Event()
        async def gated_stop():
            entered.set()
            await asyncio.wait_for(release.wait(),1)
            await original_stop()
        old.stop = gated_stop
        reset = asyncio.create_task(self.client.post('/api/demo/reset',json={'runtime_id':self.runtime}))
        await asyncio.wait_for(entered.wait(),1)
        baseline = sum(e['event']=='start_request_received' for e in self.events)
        stale = asyncio.create_task(self.client.post('/api/demo/start',json={'runtime_id':self.runtime,'profile':'S5_THIRD_A'}))
        for _ in range(100):
            if sum(e['event']=='start_request_received' for e in self.events)>baseline:
                break
            await asyncio.sleep(.005)
        self.assertGreater(sum(e['event']=='start_request_received' for e in self.events),baseline)
        release.set()
        reset_response, stale_response = await asyncio.gather(reset,stale)
        self.assertEqual(reset_response.status_code,200)
        self.assertIn(stale_response.status_code,(401,410))
        value = await self.status()
        self.assertNotEqual(value['runtime_id'],self.runtime)
        self.assertEqual(value['phase'],'NOT_STARTED')
        self.assertEqual((value['autonomous_effects'],value['distinct_command_roots']),(0,0))

    async def test_T12_stop_running_is_linearized_and_reset_is_new_scene(self):
        await self.unlock()
        await self.start()
        r = await self.client.post('/api/demo/stop',json={'runtime_id':self.runtime})
        self.assertEqual(r.status_code,200)
        before = (await self.status())['autonomous_effects']
        await asyncio.sleep(.15)
        value = await self.status()
        self.assertEqual(value['phase'],'STOPPED')
        self.assertEqual(value['autonomous_effects'],before)
        r = await self.client.post('/api/demo/reset',json={'runtime_id':self.runtime})
        self.assertEqual(r.status_code,200)
        self.assertNotEqual(r.json()['runtime_id'],self.runtime)
        self.assertEqual(len(self.app.state.archived_scenes),1)
        self.assertEqual((await self.status())['phase'],'NOT_STARTED')


    async def test_R6_reset_same_process_key_reunlock_before_next_status(self):
        await self.unlock()
        await self.start()
        await self.finish()
        original_runtime=self.runtime
        response=await self.client.post('/api/demo/reset',json={'runtime_id':self.runtime})
        self.assertEqual(response.status_code,200)
        self.runtime=response.json()['runtime_id']
        self.assertNotEqual(self.runtime,original_runtime)
        self.assertEqual(response.json()['scene_generation'],1)
        # Same process code, new scene, old cookie invalidated, no status poll prerequisite.
        denied=await self.client.post('/api/demo/start',json={'runtime_id':self.runtime,'profile':'S5_THIRD_A'})
        self.assertEqual(denied.status_code,401)
        await self.unlock()
        await asyncio.sleep(30)
        value=await self.status()
        self.assertEqual((value['phase'],value['autonomous_effects'],value['distinct_command_roots']),('NOT_STARTED',0,0))
        await self.start('S5_EXACT_OBJECTION_LIFT')
        self.assertEqual((await self.finish())['autonomous_effects'],3)

    async def test_R6_duplicate_reset_serializes_without_invisible_scene(self):
        await self.unlock();await self.start();await self.finish()
        responses=await asyncio.gather(*(self.client.post('/api/demo/reset',json={'runtime_id':self.runtime}) for _ in range(2)))
        self.assertEqual(sum(r.status_code==200 for r in responses),1)
        self.assertTrue(all(r.status_code in (200,401,410) for r in responses))
        value=await self.status()
        self.assertEqual(value['scene_generation'],1)
        self.assertEqual((value['autonomous_effects'],value['distinct_command_roots']),(0,0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
