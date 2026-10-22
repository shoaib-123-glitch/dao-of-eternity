"""Dao of Eternity v0.5 - interactive major realm trial, compatible with v0.4 Cultivator.

Pure game domain rules only: Discord UI, persistence, locks, and user identity are external.
Call start_trial/choose_phase/finish_trial under an atomic player-level lock.
All randomness provided by caller: production must use a server-side secrets.SystemRandom.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from engine import Cultivator, clamp

PHASES = ('body', 'heart_demon', 'heavenly_dao')
CHOICES = {
    'body': {
        'endure': ('Body Resilience', 18, 0, 0),
        'ward': ('Protective Formation', 22, 8, 1),
        'redirect': ('Qi Circulation', 15, 3, 0),
    },
    'heart_demon': {
        'accept': ('Face Your Regrets', 15, 0, 0),
        'sever': ('Sever Attachment', 19, 3, 0),
        'remember': ('Anchor in a Memory', 18, 6, 0),
    },
    'heavenly_dao': {
        'comprehend': ('Study the Dao', 18, 0, 0),
        'resist': ('Defy the Heavens', 13, 7, 0),
        'harmonize': ('Harmonize with Qi', 21, 3, 0),
    },
}

@dataclass
class TrialState:
    phase_index: int = 0
    endurance: int = 65
    choices: list[str] = field(default_factory=list)
    losses: list[int] = field(default_factory=list)
    insight: int = 0
    completed: bool = False
    outcome: Optional[str] = None
    prepared: bool = True
    aid_reserved: bool = False
    # Pinned startup data to detect externally changed character / dirty concurrent updates
    initial_failure_count: int = 0
    initial_qi: int = 600
    initial_stability: int = 80
    initial_integrity: int = 100
    initial_impurity: int = 0
    initial_quality: int = 60

    @property
    def phase(self) -> Optional[str]:
        return None if self.phase_index == 3 else PHASES[self.phase_index]


def _eligible(p: Cultivator) -> bool:
    return (not p.advanced_to_foundation and p.stage == 9 and p.qi >= 600
            and p.stability >= 60 and p.meridian_integrity >= 60 and p.impurity < 60)


def start_trial(p: Cultivator, *, prepared: bool = True, protective_aid: bool = False) -> TrialState:
    """Start a Foundation trial. Availability of aid is assumed prevalidated by the caller.

    For v0.5, aid is a binary quest preparation flag, not a purchasable consumable.
    No stats or resources change until finish_trial, to prevent mid-trial double spending.
    """
    if not prepared or not _eligible(p):
        raise ValueError('Not eligible for Foundation breakthrough; preparation required')
    return TrialState(
        prepared=prepared, aid_reserved=protective_aid,
        initial_failure_count=p.failure_count, initial_qi=p.qi,
        initial_stability=p.stability, initial_integrity=p.meridian_integrity,
        initial_impurity=p.impurity, initial_quality=p.foundation_quality,
    )


def choose_phase(t: TrialState, choice: str, *, body_resilience: int = 50,
                 dao_comprehension: int = 50) -> dict:
    """Choose an action and apply deterministic endurance damage.

    Formula loss = max(5, difficulty - resistance - action_defense).
    Attributes 0..100. No random roll during phase choices.
    """
    if t.completed or t.phase is None:
        raise ValueError('Trial already concluded')
    if not (0 <= body_resilience <= 100 and 0 <= dao_comprehension <= 100):
        raise ValueError('Attribute out of range')
    phase = t.phase
    if choice not in CHOICES[phase]:
        raise ValueError('Unavailable action for current phase')
    _, defense, difficulty_bonus, _ = CHOICES[phase][choice]
    challenge = {'body': 45, 'heart_demon': 43, 'heavenly_dao': 47}[phase]
    resistance = ((body_resilience // 5) if phase == 'body'
                  else (dao_comprehension // 5) if phase == 'heart_demon'
                  else ((body_resilience + dao_comprehension) // 10))
    loss = max(5, challenge + difficulty_bonus - resistance - defense)
    t.endurance = max(0, t.endurance - loss)
    t.losses.append(loss)
    t.choices.append(choice)
    if phase == 'heart_demon' and choice == 'accept' and t.endurance > 0:
        t.insight += 1  # flavor/unlock progression only, not permanent stat inflation
    t.phase_index += 1
    return {'phase': phase, 'choice': choice, 'damage': loss,
            'endurance': t.endurance, 'survived': t.endurance > 0,
            'next_phase': t.phase}


def _unchanged(p: Cultivator, t: TrialState) -> bool:
    return (p.failure_count == t.initial_failure_count and p.qi == t.initial_qi
            and p.stability == t.initial_stability and p.meridian_integrity == t.initial_integrity
            and p.impurity == t.initial_impurity and p.foundation_quality == t.initial_quality)


def finish_trial(p: Cultivator, t: TrialState, *, roll: float) -> dict:
    """Finalize a completed trial. Roll must be a random number in [0,100) from server.

    Endurance modifies the ORIGINAL v0.4 success threshold through a bounded
    roll adjustment, so original pity and resource-loss logic remain authoritative.
    A collapsed trial counts as an unsuccessful eligible attempt, even if the
    random success roll would have passed, except on the guaranteed fourth attempt.
    """
    if t.completed:
        raise ValueError('This trial was already resolved')
    if t.phase_index != 3:
        raise ValueError('Complete all three phases first')
    if not 0 <= roll < 100:
        raise ValueError('Roll must be in [0,100)')
    if not _eligible(p) or not _unchanged(p, t):
        raise ValueError('Player state changed during trial; cannot resolve')
    base = p.foundation_probability(prepared=t.prepared, protective_aid=t.aid_reserved)
    # Survival advantage bounded to 0..10 percentage points, never more.
    survival_bonus = min(10, max(0, t.endurance // 6))
    adjusted = max(0., roll - survival_bonus) if t.endurance > 0 else 99.999
    # Guaranteed fourth attempt overrides endurance collapse.
    if p.failure_count >= 3:
        adjusted = 0.
    success = p.attempt_foundation(adjusted, prepared=t.prepared,
                                   protective_aid=t.aid_reserved)
    t.completed = True
    t.outcome = 'ascended' if success else 'failed'
    return {'success': success, 'outcome': t.outcome,
            'endurance': t.endurance, 'chance_base': base,
            'survival_bonus': survival_bonus, 'roll': roll,
            'failure_count': p.failure_count, 'qi': p.qi,
            'insight': t.insight}
