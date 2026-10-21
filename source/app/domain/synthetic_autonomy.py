"""Adopted synthetic-only G3-B2/SA-1/R2 contract; no production authority."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
import hashlib
import json

from app.domain.shared.canonical import digest
from app.domain.shared.errors import Validation

WORLD = 'ML07B_SYNTHETIC_LOUNGE_S0_S7'
RESOURCE = 'SYNTHETIC_SHARED_LOUNGE_COMFORT'
ZONE = 'ONE_VIRTUAL_LIVING_ROOM'
BACKGROUND = 'Dinner'
RECOVERY_READER = 'SYNTHETIC_ROOT_RECOVERY_COORDINATOR'
HOLDER = 'SYNTHETIC_HOUSEHOLD_AUTONOMY_EXECUTOR'
MEMBERS = ('Alex', 'Jordan')
SETTINGS = frozenset({'REST', 'COMFORT_A', 'COMFORT_B'})
ISSUERS = {'Alex': 'SYNTHETIC_ALEX_AUTHORIZER', 'Jordan': 'SYNTHETIC_JORDAN_AUTHORIZER'}
SPEC_SHA = '07C426A63D352C7899CD977A90B5BC0A313000ED3A9CC0AA1C334CA7D42805D4'


def tick_value(value):
    if type(value) is not int or value < 0:
        raise Validation('A nonnegative trusted logical tick is required.')
    return value


@dataclass(frozen=True)
class ScopedConfig:
    ttl: int = 32
    roots: int = 4
    effects: int = 4
    cooldown: int = 2
    observations: int = 2
    max_age: int = 4
    phase_timeout: int = 2
    queries: int = 2
    submissions: int = 2
    unsettled: int = 1

    @staticmethod
    def specification():
        b = Path(__file__).with_name('synthetic_autonomy_contract.json').read_bytes()
        if hashlib.sha256(b).hexdigest().upper() != SPEC_SHA:
            raise Validation('Frozen contract specification drift.')
        return json.loads(b)

    def __post_init__(self):
        spec = self.specification()
        rows = spec['numeric_candidate_values']
        if ([r['value_id'] for r in rows] != [f'CFG-{i:02}' for i in range(1, 11)]
                or tuple(r['candidate_value'] for r in rows) != tuple(asdict(self).values())
                or any(type(v) is not int for v in asdict(self).values())
                or any(not r['meaning'] or not r['unit'] or not r['rule'] for r in rows)):
            raise Validation('Only the exact adopted CFG specification is supported.')
        # Paper status/source metadata is never consumed as runtime authorization.


@dataclass(frozen=True)
class ResourceEffectContract:
    world: str = WORLD
    resource: str = RESOURCE
    zone: str = ZONE
    background: str = BACKGROUND
    settings: tuple[str, ...] = ('REST', 'COMFORT_A', 'COMFORT_B')
    virtual_only: bool = True
    side_effects_known: bool = True

    def valid(self):
        return (self.world == WORLD and self.resource == RESOURCE and self.zone == ZONE
                and self.background == BACKGROUND
                and set(self.settings) == SETTINGS and self.virtual_only is True
                and self.side_effects_known is True)


@dataclass(frozen=True)
class AuthorityEnvelope:
    envelope_id: str
    world: str
    resource: str
    holder: str
    start: int
    end: int
    covered: tuple[str, ...]
    issuer_ids: tuple[str, ...]


@dataclass(frozen=True)
class TrustedEpisodeAndCommand:
    root: str
    episode: str
    envelope_id: str
    world: str
    resource: str
    effect: str
    benefited: tuple[str, ...]
    actual_affected: tuple[str, ...]
    state_version: int
    need_revisions: tuple[tuple[str, int], ...]

    @classmethod
    def create(cls, envelope, effect, benefited, affected, version, revisions):
        identity = dict(world=WORLD, resource=RESOURCE, effect=effect,
                        benefited=sorted(benefited), affected=sorted(affected),
                        state_version=version, need_revisions=sorted(revisions))
        episode = 'episode_' + digest(identity)
        root = 'command_' + digest(dict(episode=episode, envelope=envelope.envelope_id))
        return cls(root, episode, envelope.envelope_id, WORLD, RESOURCE, effect,
                   tuple(sorted(benefited)), tuple(sorted(affected)), version, tuple(sorted(revisions)))


@dataclass
class RootRecord:
    command: TrustedEpisodeAndCommand
    status: str = 'RESERVED'
    submissions: int = 0
    queries: int = 0
    submission_anchor: int | None = None
    ack_anchor: int | None = None
    attempt: int = 0
    attempt_fenced: bool = False
    potential_effect: bool = True
    closed: bool = False


@dataclass
class AutonomyJournal:
    """Persistable trusted application journal, separate from Port effect truth.

    A restart reattaches the same store or an identical persisted snapshot.
    Caller supplied replacement/rollback journals are rejected by the Port.
    """
    roots: dict[str, RootRecord] = field(default_factory=dict)
    goals: dict = field(default_factory=dict)
    cursor: int = 0
    last_tick: int = 0
    last_confirmation: int | None = None
    log: list[dict] = field(default_factory=list)
    disposition: str = 'HOLD_NO_NEED'

    def dump(self):
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    @classmethod
    def restore(cls, text):
        v = json.loads(text)
        roots = {}
        for k, r in v.pop('roots').items():
            c = r.pop('command')
            for f in ('benefited', 'actual_affected'):
                c[f] = tuple(c[f])
            c['need_revisions'] = tuple(tuple(x) for x in c['need_revisions'])
            roots[k] = RootRecord(command=TrustedEpisodeAndCommand(**c), **r)
        return cls(roots=roots, **v)

    def record(self, tick, kind, **minimal):
        self.log.append(dict(tick=tick, kind=kind, **minimal))
