"""One background-event autonomy chain for the named isolated synthetic world.

This explicit library entry is deliberately not registered with product UI,
HTTP, MCP, agents, SQLite or a physical DeviceAdapter.
"""
from app.domain.shared.errors import Validation
from app.domain.synthetic_autonomy import (
    WORLD, RESOURCE, HOLDER, MEMBERS, SETTINGS, ScopedConfig, AutonomyJournal,
    RootRecord, TrustedEpisodeAndCommand,
)
from app.devices.synthetic_only import SyntheticOnlyPort, SyntheticRootRecoveryCoordinator


class SyntheticHouseholdAutonomyExecutor:
    def __init__(self, port, *, journal=None):
        if type(port) is not SyntheticOnlyPort:
            raise Validation('Only the fixed SyntheticOnlyPort is supported; no fallback.')
        self.port = port
        self.recovery = SyntheticRootRecoveryCoordinator(port)
        self.config = ScopedConfig()
        self.journal = journal if journal is not None else (port.journal or AutonomyJournal())
        port.bind_journal(self.journal)

    def _ingest(self):
        j = self.journal
        for e in self.port.events_after(j.cursor):
            if e['world'] != WORLD or e['resource'] != RESOURCE:
                raise Validation('Trusted event boundary mismatch.')
            j.cursor = e['sequence']
            if e['kind'] == 'OBSERVATION':
                subject = e['subject']
                old = j.goals.get(subject)
                signature = [e['choice'], e['targets']]
                if old is None or old['signature'] != signature:
                    old = dict(signature=signature, revision=(old['revision']+1 if old else 1), observations=[])
                    j.goals[subject] = old
                old['observations'].append(dict(id=e['observation_id'], tick=e['tick'], version=e['version']))
                # PRIVATE body is never copied; internal source IDs stay in
                # trusted journal only and are omitted from all public projections.
            if 'root' not in e:
                continue
            r = j.roots.get(e['root'])
            if r is None or e['effect'] != r.command.effect:
                raise Validation('Authority evidence must bind the existing immutable root.')
            if e['kind'] == 'SUBMITTED':
                r.attempt = e['attempt']
                r.submissions = max(r.submissions, e['attempt'])
                if r.submission_anchor is None:
                    r.submission_anchor = e['tick']
                r.attempt_fenced = False
                r.status = 'SUBMITTED'
            elif e['kind'] == 'ACK' and e['attempt'] == r.attempt and not r.closed:
                if r.ack_anchor is None:
                    r.ack_anchor = e['anchor']
                if not r.attempt_fenced:
                    r.status = 'ACKNOWLEDGED'
            elif e['kind'] == 'APPLIED':
                if not r.closed:
                    r.status = 'APPLIED'
                    r.closed = True
                    r.potential_effect = True
                    j.last_confirmation = e['confirmation_tick']
                    j.record(e['tick'], 'TRUSTED_EFFECT_CONFIRMED', root=e['root'],
                             effect=e['effect'], version=e['version'], envelope=r.command.envelope_id,
                             episode=r.command.episode)
            elif e['kind'] == 'NOT_APPLIED' and e['attempt'] == r.attempt and not r.closed:
                if e['fence'] is not True:
                    raise Validation('Authority Attempt fence is required.')
                r.attempt_fenced = True
                r.potential_effect = False
                r.closed = e['closed']
                r.status = 'CLOSED_NOT_APPLIED' if r.closed else 'ATTEMPT_NOT_APPLIED'
                j.record(e['tick'], 'TRUSTED_ATTEMPT_FENCED', root=e['root'], attempt=e['attempt'])

    def _candidate(self, s):
        j, c = self.journal, self.config
        if not s['available']:
            return 'UNAVAILABLE_SYNTHETIC_ONLY', None
        env = s['envelope']
        if env is None or env.holder != HOLDER or env.world != WORLD or env.resource != RESOURCE:
            return 'HOLD_COVERAGE', None
        affected = set(s['actual_affected'])
        if not affected <= set(env.covered) or any(m not in s['issuances'] for m in affected):
            return 'HOLD_AFFECTED_COVERAGE', None
        if affected & (s['revoked'] | s['paused']):
            return 'HOLD_REVOKED_OR_PAUSED', None
        if not env.start <= s['tick'] < env.end:
            return 'HOLD_EXPIRED', None
        if s['tick'] - s['state_seen_at'] > c.max_age:
            return 'HOLD_STALE_STATE', None
        active = {m: g for m, g in j.goals.items() if m in affected and g['signature'][0] == 'NEEDED'}
        if not active:
            return 'HOLD_NO_NEED', None
        targets = set(SETTINGS)
        for g in active.values():
            targets &= set(g['signature'][1])
        if s['setting'] in targets:
            return 'SATISFIED_NO_NEW_EPISODE', None
        allowed = set(SETTINGS)
        for m in affected:
            allowed &= set(s['issuances'][m]['allowed']) & s['limits'][m]
        legal = targets & allowed
        if not legal:
            return 'HOLD_PHYSICAL_CONFLICT', None
        if len(legal) != 1:
            return 'HOLD_NO_UNIQUE_LEGAL_ALTERNATIVE', None
        for g in active.values():
            obs = [o for o in g['observations'] if o['version'] == s['version']
                   and 0 <= s['tick']-o['tick'] <= c.max_age]
            if len({o['id'] for o in obs}) < c.observations or max(o['tick'] for o in obs)-min(o['tick'] for o in obs) < 1:
                return 'HOLD_UNSTABLE_OR_STALE_NEED', None
        if j.last_confirmation is not None and s['tick']-j.last_confirmation < c.cooldown:
            return 'HOLD_COOLDOWN', None
        command = TrustedEpisodeAndCommand.create(env, next(iter(legal)), active.keys(), affected,
                                                  s['version'], [(m, g['revision']) for m, g in active.items()])
        return 'ALLOW', command

    def _submit(self, command, *, recovery=False):
        j, c = self.journal, self.config
        def admit(s):
            # Re-read current trusted facts while holding Port authority order.
            self._ingest()
            reason, current = self._candidate(s)
            if reason != 'ALLOW':
                return reason
            if current != command:
                return 'HOLD_COMMIT_CONTEXT_CHANGED'
            open_roots = [r for r in j.roots.values() if not r.closed]
            reserved = sum(r.potential_effect for r in j.roots.values())
            if recovery:
                r = j.roots[command.root]
                if r.closed or not r.attempt_fenced:
                    return 'HOLD_RECOVERY_NOT_FENCED_OR_CLOSED'
                if r.submissions >= c.submissions:
                    return 'HOLD_SUBMISSION_LIMIT'
                if reserved >= c.effects:
                    return 'HOLD_EFFECT_BUDGET'
                r.potential_effect = True
            else:
                if command.root in j.roots:
                    return 'HISTORY_ONLY'
                if len(open_roots) >= c.unsettled:
                    return 'HOLD_RESOURCE_LOCK'
                if len(j.roots) >= c.roots:
                    return 'HOLD_ROOT_BUDGET'
                if reserved >= c.effects:
                    return 'HOLD_EFFECT_BUDGET'
                j.roots[command.root] = RootRecord(command)
            j.record(s['tick'], 'RECOVERY_RESERVED' if recovery else 'NEW_ROOT_RESERVED',
                     root=command.root, episode=command.episode, envelope=command.envelope_id,
                     effect=command.effect, benefited=list(command.benefited),
                     actual_affected=list(command.actual_affected), version=command.state_version)
            return 'ALLOW'
        result = self.port.commit(command, admit)
        self._ingest()
        return result

    def on_tick(self):
        """Background trusted-event entry; callers do not request a specific action."""
        with self.port.lock:
            j, c, tick = self.journal, self.config, self.port.tick
            if tick < j.last_tick:
                raise Validation('Journal logical clock rollback.')
            j.last_tick = tick
            # Boundary arrivals precede timeout and automatic reevaluation.
            self._ingest()
            pending = [r for r in j.roots.values() if not r.closed]
            if pending:
                r = pending[0]
                if r.attempt_fenced:
                    j.disposition = self._submit(r.command, recovery=True)
                    return j.disposition
                if r.ack_anchor is not None and tick-r.ack_anchor >= c.phase_timeout:
                    r.status = 'EFFECT_UNKNOWN'
                elif r.ack_anchor is None and r.submission_anchor is not None and tick-r.submission_anchor >= c.phase_timeout:
                    r.status = 'SUBMISSION_UNKNOWN'
                if r.status in {'EFFECT_UNKNOWN', 'SUBMISSION_UNKNOWN'}:
                    if r.queries < c.queries:
                        result, evidence = self.recovery.query(r.command.root)
                        if result == 'QUERIED':
                            # Returned evidence is authority-issued original-root
                            # history, not a locally fabricated completion.
                            if any(e.get('root') != r.command.root for e in evidence):
                                raise Validation('Recovery evidence root mismatch.')
                            r.queries += 1
                            j.record(tick, 'ORIGINAL_ROOT_QUERY', root=r.command.root, query=r.queries)
                        j.disposition = 'HOLD_UNKNOWN_RECONCILE_FIRST' if result == 'QUERIED' else result
                    else:
                        j.disposition = 'HOLD_QUERY_LIMIT'
                else:
                    j.disposition = 'HOLD_RESOURCE_LOCK'
                return j.disposition
            reason, command = self._candidate(self.port.snapshot())
            j.disposition = reason if reason != 'ALLOW' else self._submit(command)
            return j.disposition

    def retry(self, root, *, effect):
        """Same-root retries never allocate another episode or root."""
        with self.port.lock:
            self._ingest()
            r = self.journal.roots.get(root)
            if r is None:
                return 'HOLD_UNLINKED_ROOT'
            if r.command.effect != effect:
                raise Validation('IMMUTABLE_EFFECT_CONFLICT')
            if r.closed:
                return 'HISTORY_ONLY'
            if not r.attempt_fenced:
                return 'HOLD_UNKNOWN_RECONCILE_FIRST'
            return self._submit(r.command, recovery=True)

    def public_view(self):
        # No private body, observation locator, body digest, owner reason, or
        # internal goal/command correlation is exposed to public consumers.
        return dict(self.port.public_view(), disposition=self.journal.disposition)
