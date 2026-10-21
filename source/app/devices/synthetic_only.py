"""Independent in-process synthetic state authority. No device/network fallback.

Only a deliberately enabled instance can create isolated unit-test facts.
The executor may read/query/commit; only the authority's controlled event source
may issue rights, observe Need, change occupancy, or settle an Attempt.
"""
from copy import deepcopy
from threading import RLock

from app.domain.comfort import validate_choice
from app.domain.shared.canonical import digest
from app.domain.shared.errors import Validation
from app.domain.synthetic_autonomy import (
    WORLD, RESOURCE, ZONE, HOLDER, MEMBERS, SETTINGS, ISSUERS, RECOVERY_READER,
    ScopedConfig, ResourceEffectContract, AuthorityEnvelope, tick_value,
)


class SyntheticOnlyPort:
    def __init__(self, *, enabled=False, mode='OFFLINE_REPLAY', contract=None):
        self.config = ScopedConfig()
        self.contract = contract or ResourceEffectContract()
        self.enabled = enabled is True and mode == 'OFFLINE_REPLAY' and self.contract.valid()
        self.lock = RLock()
        self.tick = 0
        self.setting = 'REST'
        self.version = 0
        self.state_seen_at = 0
        self.occupants = MEMBERS
        self.issuances = {}
        self.envelope = None
        self.revoked = set()
        self.paused = set()
        self.limits = {m: set(SETTINGS) for m in MEMBERS}
        self.readers = set(MEMBERS)
        self.events = []
        self._issued_events = {}
        self.recovery_read_authorized = True
        self.ledger = {}
        self.private = {}
        self.journal = None
        self.delivery = 'APPLY'
        self.effects = 0
        self.initial_endorsements = 0
        self.per_action_approvals = 0

    def _require_enabled(self):
        if not self.enabled or not self.contract.valid():
            raise Validation('UNAVAILABLE_SYNTHETIC_ONLY_DEFAULT_DENY')

    def _event(self, kind, **data):
        event = dict(sequence=len(self.events) + 1, tick=self.tick, kind=kind,
                     world=WORLD, resource=RESOURCE, **data)
        self.events.append(event)
        self._issued_events[event['sequence']] = deepcopy(event)
        return deepcopy(event)

    def advance(self, tick):
        with self.lock:
            tick_value(tick)
            if tick < self.tick:
                raise Validation('Logical event time cannot move backwards.')
            self.tick = tick

    def refresh_state(self):
        with self.lock:
            self._require_enabled()
            self.state_seen_at = self.tick
            self._event('STATE', version=self.version, setting=self.setting)

    def issue(self, subject, issuer, *, allowed, start=None):
        with self.lock:
            self._require_enabled()
            if (subject not in MEMBERS or issuer != ISSUERS[subject]
                    or subject in self.issuances or self.envelope is not None
                    or not set(allowed) or not set(allowed) <= SETTINGS):
                raise Validation('Only one explicit own-scope virtual issuance per member.')
            start = self.tick if start is None else tick_value(start)
            if start != self.tick:
                raise Validation('No retroactive or future activation.')
            if self.issuances and start != next(iter(self.issuances.values()))['start']:
                raise Validation('Both endorsements bind the same activation window.')
            self.issuances[subject] = dict(issuer=issuer, allowed=sorted(set(allowed)), start=start)
            self.initial_endorsements += 1
            if set(self.issuances) == set(MEMBERS):
                eid = 'envelope_' + digest(dict(world=WORLD, resource=RESOURCE, holder=HOLDER,
                                                issuances=self.issuances))
                self.envelope = AuthorityEnvelope(eid, WORLD, RESOURCE, HOLDER, start,
                                                 start + self.config.ttl, MEMBERS,
                                                 tuple(ISSUERS[m] for m in MEMBERS))
            self._event('AUTHORITY', subject=subject)

    def revoke(self, subject, issuer, *, pause=False):
        with self.lock:
            self._require_enabled()
            if subject not in MEMBERS or issuer != ISSUERS[subject]:
                raise Validation('Untrusted revocation/representative.')
            (self.paused if pause else self.revoked).add(subject)
            self._event('PAUSE' if pause else 'REVOKE', subject=subject)

    def revoke_scope(self, *, source):
        with self.lock:
            if source != 'USER_ADOPTED_SYNTHETIC_AUTHORITY_EVENT_STREAM':
                raise Validation('Scope revocation requires the adopted authority event source.')
            self.revoked.update(MEMBERS)
            self._event('REVOKE_SCOPE')

    def objection(self, subject, *, allowed):
        with self.lock:
            self._require_enabled()
            if subject not in MEMBERS or not set(allowed) <= SETTINGS:
                raise Validation('Unknown objection scope.')
            self.limits[subject] = set(allowed)
            self._event('OBJECTION', subject=subject)

    def occupancy(self, members):
        with self.lock:
            self._require_enabled()
            if not members or len(set(members)) != len(members) or not set(members) <= set(MEMBERS + ('Guest', 'Child')):
                raise Validation('Occupancy must explicitly enumerate the virtual zone.')
            self.occupants = tuple(sorted(members))
            self._event('OCCUPANCY')

    def observe(self, subject, *, choice, targets=(), observation_id=None, body=None):
        with self.lock:
            self._require_enabled()
            validate_choice(choice)
            if subject not in MEMBERS or not set(targets) <= SETTINGS or (choice == 'NEEDED' and not targets):
                raise Validation('Unsupported current Need fact.')
            oid = observation_id or f'observation_{len(self.events)+1}'
            if any(e.get('observation_id') == oid for e in self.events):
                raise Validation('A distinct trusted source observation is required.')
            # Body never participates in a public digest, command, query, or log.
            if body is not None:
                self.private[oid] = dict(owner=subject, body=body)
            return self._event('OBSERVATION', subject=subject, choice=choice,
                               targets=sorted(set(targets)) if choice == 'NEEDED' else [],
                               observation_id=oid, version=self.version)

    def private_view(self, principal, observation_id):
        with self.lock:
            row = self.private.get(observation_id)
            if row is None or principal not in self.readers or principal != row['owner']:
                raise Validation('PRIVATE_NOT_VISIBLE')
            return row['body']

    def events_after(self, cursor):
        with self.lock:
            if len(self.events) != len(self._issued_events):
                raise Validation('Unissued or missing authority event.')
            batch = self.events[cursor:]
            for index, event in enumerate(batch, cursor+1):
                if event.get('sequence') != index or self._issued_events.get(index) != event:
                    raise Validation('Untrusted receipt or modified authority event.')
            return deepcopy(batch)

    def snapshot(self):
        with self.lock:
            return dict(tick=self.tick, setting=self.setting, version=self.version,
                        state_seen_at=self.state_seen_at, actual_affected=self.occupants,
                        envelope=self.envelope, issuances=deepcopy(self.issuances),
                        revoked=set(self.revoked), paused=set(self.paused),
                        limits=deepcopy(self.limits), available=self.enabled and self.contract.valid())

    def bind_journal(self, journal):
        with self.lock:
            if self.journal is not None and self.journal is not journal:
                if self.journal.dump() != journal.dump():
                    raise Validation('Replacement/rollback journal is forbidden.')
            self.journal = journal

    def commit(self, command, admit):
        """Same authority order for final validation, reservation and effect submit."""
        with self.lock:
            self._require_enabled()
            if command.world != WORLD or command.resource != RESOURCE or command.effect not in SETTINGS:
                raise Validation('No real or unregistered virtual effect is supported.')
            previous = self.ledger.get(command.root)
            if previous and previous['command'] != command:
                raise Validation('IMMUTABLE_EFFECT_CONFLICT')
            if previous and (previous['applied'] or previous['closed']):
                return 'HISTORY_ONLY'
            if previous and not previous['attempts'][-1]['fenced']:
                return 'HOLD_UNFENCED_ATTEMPT'
            if previous and len(previous['attempts']) >= self.config.submissions:
                return 'HOLD_SUBMISSION_LIMIT'
            # This callback ingests any newly arrived facts and evaluates all
            # gates under this same lock, then atomically reserves journal quotas.
            reason = admit(self.snapshot())
            if reason != 'ALLOW':
                return reason
            if previous is None:
                previous = dict(command=command, attempts=[], applied=False, closed=False, queries=0)
                self.ledger[command.root] = previous
            attempt = len(previous['attempts']) + 1
            previous['attempts'].append(dict(id=attempt, submitted_tick=self.tick, ack=None,
                                            terminal=None, fenced=False))
            self._event('SUBMITTED', root=command.root, attempt=attempt, effect=command.effect,
                        version=command.state_version)
            if self.delivery in {'APPLY', 'ACK_ONLY'}:
                self.ack(command.root)
            if self.delivery == 'APPLY':
                self.settle(command.root, 'APPLIED')
            return 'SUBMITTED'

    def ack(self, root, *, attempt=None):
        with self.lock:
            row = self.ledger[root]
            a = row['attempts'][(attempt or len(row['attempts']))-1]
            if a['terminal']:
                return
            if a['ack'] is None:
                a['ack'] = self.tick
            self._event('ACK', root=root, attempt=a['id'], effect=row['command'].effect,
                        anchor=a['ack'], version=row['command'].state_version)

    def settle(self, root, status, *, attempt=None, fence=False, close=False):
        with self.lock:
            self._require_enabled()
            row = self.ledger[root]
            a = row['attempts'][(attempt or len(row['attempts']))-1]
            if status not in {'APPLIED', 'NOT_APPLIED'}:
                raise Validation('Only trusted actual Attempt terminal evidence is supported.')
            if status == 'NOT_APPLIED' and fence is not True:
                raise Validation('NOT_APPLIED requires an irreversible Attempt fence.')
            if a['terminal'] and a['terminal'] != status:
                raise Validation('A fenced Attempt terminal is immutable.')
            if a['terminal']:
                return
            if row['closed'] or row['applied']:
                raise Validation('A finalized root cannot be revived.')
            if status == 'APPLIED':
                self.setting = row['command'].effect
                self.version += 1
                self.state_seen_at = self.tick
                self.effects += 1
                row['applied'] = row['closed'] = True
            a['terminal'] = status
            a['fenced'] = status == 'NOT_APPLIED'
            row['closed'] = row['closed'] or close
            self._event(status, root=root, attempt=a['id'], effect=row['command'].effect,
                        version=self.version, confirmation_tick=self.tick, fence=a['fenced'],
                        closed=row['closed'])

    def query(self, root, *, principal=RECOVERY_READER):
        """Read-only original-root evidence; its lifetime count cannot be reset."""
        with self.lock:
            row = self.ledger.get(root)
            if principal != RECOVERY_READER or row is None or not self.recovery_read_authorized:
                return 'HOLD_READ_AUTHORITY', []
            if row['queries'] >= self.config.queries:
                return 'HOLD_QUERY_LIMIT', []
            # The read-only capability must authenticate independently of
            # executor ingestion, before quota consumption or evidence return.
            batch = self.events_after(0)
            evidence = [e for e in batch if e.get('root') == root]
            row['queries'] += 1
            return 'QUERIED', evidence

    def public_view(self):
        # All public consumers receive the same minimal effect projection.
        with self.lock:
            return dict(resource=RESOURCE, setting=self.setting, version=self.version,
                        result='AVAILABLE' if self.enabled and self.contract.valid() else 'UNAVAILABLE')


class SyntheticRootRecoveryCoordinator:
    """Read capability only: no authority issuance, commit or effect settlement.

    This Python facade separates application responsibilities in the synthetic
    library; it is not an OS sandbox against arbitrary Python introspection.
    """
    __slots__ = ('__query',)

    def __init__(self, port):
        if type(port) is not SyntheticOnlyPort:
            raise Validation('Synthetic recovery read capability only.')
        self.__query = port.query

    def query(self, root):
        return self.__query(root, principal=RECOVERY_READER)
