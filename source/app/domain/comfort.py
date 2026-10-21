"""Bounded current-activity comfort intent, independent of authorization."""
from dataclasses import dataclass
from datetime import datetime
from app.domain.shared.errors import Validation

CHOICES = {"NEEDED", "NOT_NEEDED", "WITHDRAW"}
PRIORITY = ("Safety", "Explicit valid authorization", "Household shared rule",
            "Current shared goal", "Current explicit personal intent", "Explicit preference",
            "Learned routine", "Model inference")


def validate_choice(value):
    if type(value) is not str or value not in CHOICES:
        raise Validation("Choose NEEDED, NOT_NEEDED or WITHDRAW.")
    return value


@dataclass(frozen=True)
class ComfortConstraint:
    household_id: str
    activity_id: str
    subject_ids: tuple[str, ...]
    layer: str
    needed: bool
    effective_at: datetime
    valid_until: datetime
    source_ref: str
    side_effect: int = 0
    valid: bool = True
    visible: bool = True
    # Permission alone is never a command to create a need.
    constrains_comfort: bool = True


def resolve(constraints, *, household_id, activity_id, subject_id, now):
    applicable = [c for c in constraints if c.valid and c.visible and c.constrains_comfort
                  and c.source_ref and c.household_id == household_id and c.activity_id == activity_id
                  and subject_id in c.subject_ids and c.effective_at <= now < c.valid_until]
    if not applicable:
        return "UNSTATED"
    def rank(c):
        if c.layer not in PRIORITY or type(c.needed) is not bool:
            raise Validation()
        return (PRIORITY.index(c.layer), len(c.subject_ids), -c.effective_at.timestamp(), c.side_effect)
    best = min(rank(c) for c in applicable)
    choices = {c.needed for c in applicable if rank(c) == best}
    return "ASK" if len(choices) != 1 else "NEEDED" if choices == {True} else "NOT_NEEDED"


def shared_disposition(subject_ids, dispositions):
    """Only a genuinely common action compares its participants together."""
    values = {dispositions.get(s, "UNSTATED") for s in subject_ids}
    if "ASK" in values or len(subject_ids) > 1 and "NOT_NEEDED" in values and values != {"NOT_NEEDED"}:
        return "ASK"
    return "NOT_NEEDED" if values == {"NOT_NEEDED"} else "NEEDED" if values == {"NEEDED"} else "UNSTATED"


def goal_subjects(goal):
    from app.domain.plan.add_contract import goal_duration
    if goal_duration(goal) is None:
        return ()
    # Extract the subject of the already-validated original explicit Goal grammar.
    text = goal.statement
    subject = text.split("到家舒适准备时长为")[0] if "到家舒适准备时长为" in text else text.removeprefix("Prepare arrival comfort for ").rsplit(" for ", 1)[0]
    return (subject,) if subject in goal.subject_ids else ()


def activity_for_goal(goal):
    from app.domain.shared.canonical import digest
    return goal.activity_id or "plan_" + digest({"household": goal.household_id, "goal": goal.goal_id})[:24]
