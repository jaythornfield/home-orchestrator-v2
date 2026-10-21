"""Bounded product preview source → actual R3 autonomy → minimal public view."""
import asyncio
from uuid import uuid4

from app.application.synthetic_autonomy import SyntheticHouseholdAutonomyExecutor
from app.devices.synthetic_only import SyntheticOnlyPort
from app.domain.synthetic_autonomy import WORLD, RESOURCE, ZONE, BACKGROUND, MEMBERS, ISSUERS, SETTINGS

S5_PROFILES = frozenset({'S5_THIRD_A_REVOKE', 'S5_THIRD_A', 'S5_EXACT_OBJECTION_LIFT', 'S5_EXACT_CHILD_LIFT', 'S5_THIRD_A_UNKNOWN', 'S5_EXACT_OBJECTION', 'S5_EXACT_GUEST', 'S5_EXACT_GUEST_LIFT', 'S5_EXACT_CHILD'})
PROFILES = frozenset({'AUTONOMY', 'OBJECTION', 'GUEST', 'CHILD', 'REVOKE', 'UNKNOWN', 'FENCED_RECOVERY', 'REVOKE_UNKNOWN'}) | S5_PROFILES
PRIVATE_BODY = 'D2_SYNTHETIC_OWNER_ONLY_REASON'
PRIVATE_LOCATOR = 'd2_synthetic_private_source'


class SyntheticDemoService:
    """One new world per process. The browser cues a fixed source, not rights.

    The authority/Need schedule is explicitly synthetic and deterministic. It
    contains no Command roots, successful Receipt data or effect state writes.
    All effects are produced by the existing R3 executor and independent Port.
    """
    def __init__(self, *, interval=1.0, before_step=None, after_step=None):
        self.runtime_id = 'd2_preview_' + uuid4().hex
        self.interval = interval
        self.lock = asyncio.Lock()
        self.port = None
        self.executor = None
        self.profile = None
        self.phase = 'NOT_STARTED'
        self.task = None
        self.failure = None
        self.before_step = before_step
        self.after_step = after_step
        self.revision = 0

    async def start(self, profile):
        if profile not in PROFILES:
            raise ValueError('Unsupported bounded source profile.')
        async with self.lock:
            if self.port is not None:
                if profile != self.profile:
                    raise ValueError('This runtime has already started a different source profile.')
                return self._project()
            if self.phase != "NOT_STARTED":
                raise ValueError("This source is stopped; prepare a new scene.")
            self.profile = profile
            self.port = SyntheticOnlyPort(enabled=True)
            if profile in {'UNKNOWN', 'FENCED_RECOVERY', 'REVOKE_UNKNOWN'}:
                self.port.delivery = 'NO_ACK'
            for member in MEMBERS:
                self.port.issue(member, ISSUERS[member], allowed=SETTINGS)
            self.executor = SyntheticHouseholdAutonomyExecutor(self.port)
            self.port.observe('Alex', choice='NOT_NEEDED')
            self.phase = 'RUNNING'
            self._step(0)
            self.task = asyncio.create_task(self._run(), name=self.runtime_id)
            return self._project()

    def _step(self, tick):
        if self.before_step is not None:
            self.before_step(tick)
        self.revision += 1
        if self.profile in S5_PROFILES:
            self._step_s5(tick)
            if self.after_step is not None:
                self.after_step(self._project())
            return
        p, x = self.port, self.executor
        p.advance(tick)
        p.refresh_state()
        if tick in (0, 1):
            p.observe('Jordan', choice='NEEDED', targets=['COMFORT_A'],
                      observation_id=PRIVATE_LOCATOR if tick == 0 else None,
                      body=PRIVATE_BODY if tick == 0 else None)
        if tick == 2:
            if self.profile == 'OBJECTION': p.objection('Alex', allowed={'REST'})
            if self.profile in {'GUEST', 'CHILD'}: p.occupancy(MEMBERS + (self.profile.title(),))
            if self.profile in {'REVOKE', 'REVOKE_UNKNOWN'}: p.revoke('Alex', ISSUERS['Alex'])
            if self.profile == 'FENCED_RECOVERY':
                root = next(iter(x.journal.roots))
                p.settle(root, 'NOT_APPLIED', fence=True)
                p.delivery = 'APPLY'
        if tick in ((3, 4) if self.profile == 'FENCED_RECOVERY' else (2, 3)):
            p.observe('Jordan', choice='NEEDED', targets=['COMFORT_B'])
        if tick == 4 and self.profile == 'REVOKE_UNKNOWN':
            root = next(iter(x.journal.roots))
            p.settle(root, 'NOT_APPLIED', fence=True)
        x.on_tick()
        if self.after_step is not None:
            self.after_step(self._project())

    def _step_s5(self, tick):
        """Fixed trusted source: A/B first, then genuinely new A and one blocker.

        Occupant departures and objection withdrawal are authority facts;
        they never issue Guest/Child rights or change a Command/effect ledger.
        """
        p, x = self.port, self.executor
        p.advance(tick)
        if tick == 4:
            if self.profile.startswith('S5_EXACT_OBJECTION'):
                p.objection('Alex', allowed={'REST'})
            if self.profile.startswith('S5_EXACT_GUEST'):
                p.occupancy(MEMBERS + ('Guest',))
            if self.profile.startswith('S5_EXACT_CHILD'):
                p.occupancy(MEMBERS + ('Child',))
            if self.profile == 'S5_THIRD_A_REVOKE':
                p.revoke('Alex', ISSUERS['Alex'])
            if self.profile == 'S5_THIRD_A_UNKNOWN':
                p.delivery = 'NO_ACK'
        if tick == 7 and self.profile.endswith('_LIFT'):
            if self.profile == 'S5_EXACT_OBJECTION_LIFT':
                p.objection('Alex', allowed=SETTINGS)
            else:
                p.occupancy(MEMBERS)
        # Current authority state follows any occupancy change, including a
        # lawful departure; the executor then rechecks every current gate.
        p.refresh_state()
        if tick in (0, 1):
            p.observe('Jordan', choice='NEEDED', targets=['COMFORT_A'],
                      observation_id=PRIVATE_LOCATOR if tick == 0 else None,
                      body=PRIVATE_BODY if tick == 0 else None)
        if tick in (2, 3):
            p.observe('Jordan', choice='NEEDED', targets=['COMFORT_B'])
        if tick in (4, 5):
            p.observe('Jordan', choice='NEEDED', targets=['COMFORT_A'])
        x.on_tick()

    async def _run(self):
        try:
            for tick in range(1, 10 if self.profile in S5_PROFILES else 6):
                await asyncio.sleep(self.interval)
                async with self.lock:
                    self._step(tick)
            async with self.lock:
                self.phase = 'FINISHED'
                self.revision += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never convert an exception into success or trust a UI result.
            async with self.lock:
                self.phase = 'FAILED_HOLD'
                self.failure = 'trusted_source_failed'

    async def stop(self):
        # This runs on the service event loop: stop is linearized before cancel.
        self.phase = 'STOPPED'
        self.revision += 1
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass

    async def status(self):
        async with self.lock:
            return self._project()

    def _project(self):
        base = dict(family='D2_LOCAL_PRODUCT_WIRING_SMOKE', runtime_id=self.runtime_id,
                    label='OFFLINE REPLAY · SYNTHETIC ONLY', world=WORLD, resource=RESOURCE,
                    zone=ZONE, background=BACKGROUND, members=list(MEMBERS), phase=self.phase,
                    formal_gate_e='NOT_EXECUTED', product_autonomy_proven=False,
                    private_viewer_authentication='NOT_PROVEN_NOT_OFFERED')
        base['revision'] = self.revision
        if self.port is None:
            return dict(base, authorization=None, current_need=None, resource_state={'setting':'REST','version':0},
                        autonomous_effects=0, per_action_approvals=0, distinct_command_roots=0,
                        actions=[], audit=[], safety_decision='NOT_STARTED', source_profile=None)
        p, j = self.port, self.executor.journal
        s = p.snapshot()
        actions = []
        for root, r in j.roots.items():
            applied = next((e for e in p.events_after(0) if e.get('root') == root and e['kind'] == 'APPLIED'), None)
            actions.append(dict(runtime_id=self.runtime_id, root=root, episode=r.command.episode, effect=r.command.effect,
                                status=r.status, submissions=r.submissions, queries=r.queries,
                                state_before=r.command.state_version,
                                confirmed_version=applied['version'] if applied else None,
                                confirmation_tick=applied['confirmation_tick'] if applied else None,
                                actual_affected=list(r.command.actual_affected)))
        goal = j.goals.get('Jordan')
        current_need = dict(subject='Jordan', choice=goal['signature'][0], targets=goal['signature'][1],
                            source_category='TRUSTED_SYNTHETIC_OBSERVATION',
                            satisfied=s['setting'] in goal['signature'][1]) if goal else None
        # Explicit per-field allowlist, including audit. No raw observations,
        # private bodies/locators/digests or operator session values can escape.
        audit = []
        for e in p.events_after(0):
            row = dict(sequence=e['sequence'], tick=e['tick'], kind=e['kind'],
                       source_category='SYNTHETIC_STATE_AUTHORITY' if e['kind'] in {'ACK','APPLIED','NOT_APPLIED'} else 'TRUSTED_SYNTHETIC_EVENT_SOURCE')
            for key in ('subject', 'choice', 'targets', 'root', 'effect', 'version', 'attempt'):
                if key in e: row[key] = e[key]
            audit.append(row)
        for e in j.log:
            if e['kind'] in {'NEW_ROOT_RESERVED', 'RECOVERY_RESERVED', 'ORIGINAL_ROOT_QUERY'}:
                row = {k: e[k] for k in ('tick','kind','root','episode','effect','version','query') if k in e}
                row['source_category'] = 'AUTONOMY_EXECUTOR_DECISION'
                audit.append(row)
        priority = {'NOT_APPLIED': 1, 'NEW_ROOT_RESERVED': 2, 'RECOVERY_RESERVED': 3, 'SUBMITTED': 4, 'ACK': 5, 'APPLIED': 6, 'ORIGINAL_ROOT_QUERY': 7}
        audit.sort(key=lambda r: (r['tick'], priority.get(r['kind'], 0), r.get('sequence', 0)))
        env = p.envelope
        authorization = dict(envelope=env.envelope_id, issuers=list(env.issuer_ids),
                             holder=env.holder, start=env.start, end=env.end, tick=p.tick,
                             remaining_roots=p.config.roots-len(j.roots),
                             remaining_effects=p.config.effects-sum(r.potential_effect for r in j.roots.values()),
                             covered=list(env.covered), actual_affected=list(s['actual_affected']),
                             revoked=sorted(s['revoked']), simulated_only=True)
        return dict(base, authorization=authorization, current_need=current_need,
                    resource_state={'setting':p.setting,'version':p.version},
                    autonomous_effects=p.effects, per_action_approvals=p.per_action_approvals,
                    distinct_command_roots=len(j.roots), actions=actions, audit=audit,
                    safety_decision=j.disposition, source_profile=self.profile, error=self.failure)
