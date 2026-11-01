"""Dao of Eternity v0.4 — deterministic Qi Refining rules engine.
All public actions receive server-controlled UTC-aware timestamps in production.
Prototype game times use UTC, with daily quotas resetting at 00:00 UTC.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

UTC = timezone.utc
DAILY_QI_LIMITS = {'active': 80, 'offline': 80, 'training': 40}
COOLDOWN_MINUTES = {'active': 15, 'training': 30}
OPEN_MERIDIAN_MAX_QI_REFINING = 6

def qi_cost(stage: int) -> int:
    if not 1 <= stage <= 8:
        raise ValueError('Minor-stage cost only defined for stages 1 to 8')
    return 100 + 20 * (stage - 1) + 5 * (stage - 1)**2

def clamp(value: int, lo: int=0, hi: int=100) -> int:
    return max(lo, min(hi, value))

@dataclass
class Cultivator:
    stage: int = 1
    qi: int = 0
    stability: int = 80
    foundation_quality: int = 60
    impurity: int = 0
    meridian_integrity: int = 100
    open_meridians: int = 0
    training_seals: int = 0
    foundation_essences: int = 0
    foundation_refinements_week: int = 0
    active_slots: int = 0
    training_slots: int = 0
    earned_today: dict = field(default_factory=lambda: dict.fromkeys(DAILY_QI_LIMITS, 0))
    day_key: Optional[str] = None
    week_key: Optional[str] = None
    last_active: Optional[datetime] = None
    last_training: Optional[datetime] = None
    offline_cursor: Optional[datetime] = None
    failure_count: int = 0
    advanced_to_foundation: bool = False

    def __post_init__(self):
        if not 1 <= self.stage <= 9:
            raise ValueError('Qi Refining stage must be 1–9')

    @property
    def capacity(self):
        return 600 if self.stage == 9 else qi_cost(self.stage)

    def _rollover(self, now: datetime):
        if now.tzinfo is None:
            raise ValueError('Timezone-aware datetime required')
        now = now.astimezone(UTC)
        day = now.date().isoformat()
        week = f'{now.isocalendar().year}-W{now.isocalendar().week:02d}'
        if self.day_key != day:
            self.day_key = day
            self.earned_today = dict.fromkeys(DAILY_QI_LIMITS, 0)
            self.active_slots = 0
            self.training_slots = 0
        if self.week_key != week:
            self.week_key = week
            self.foundation_refinements_week = 0
        if self.offline_cursor is None:
            self.offline_cursor = now
        return now

    def _gain(self, source: str, requested: int) -> int:
        if requested < 0 or source not in DAILY_QI_LIMITS:
            raise ValueError('Invalid Qi request')
        awarded = max(0, min(requested, DAILY_QI_LIMITS[source] - self.earned_today[source], self.capacity - self.qi))
        self.qi += awarded
        self.earned_today[source] += awarded
        return awarded

    def _slot(self, source: str, now: datetime):
        if self.advanced_to_foundation:
            raise ValueError('Foundation Establishment is beyond v0.4 engine scope')
        if source == 'active':
            if self.active_slots >= 4: raise ValueError('Daily active sessions exhausted')
            if self.last_active and now - self.last_active < timedelta(minutes=15):
                raise ValueError('Active meditation cooldown')
            self.active_slots += 1
            self.last_active = now
        elif source == 'training':
            if self.training_slots >= 2: raise ValueError('Daily training sessions exhausted')
            if self.last_training and now - self.last_training < timedelta(minutes=30):
                raise ValueError('Training cooldown')
            self.training_slots += 1
            self.last_training = now
        else:
            raise ValueError('Unknown action slot')

    def active_meditation(self, now: datetime) -> int:
        now = self._rollover(now)
        self._slot('active', now)
        return self._gain('active', 20)

    def training(self, now: datetime) -> int:
        now = self._rollover(now)
        self._slot('training', now)
        # Quest resources are limited to rewarded training completions.
        self.training_seals += 2
        self.foundation_essences += 1
        return self._gain('training', 20)

    def stabilize(self, now: datetime) -> int:
        now = self._rollover(now)
        self._slot('active', now)
        before = self.stability
        self.stability = clamp(self.stability + 8)
        return self.stability-before

    def recover_meridians(self, now: datetime) -> int:
        now = self._rollover(now)
        self._slot('training', now)
        before = self.meridian_integrity
        self.meridian_integrity = clamp(self.meridian_integrity + 12)
        return self.meridian_integrity-before

    def purify(self, now: datetime) -> int:
        now = self._rollover(now)
        self._slot('training', now)
        before = self.impurity
        self.impurity = clamp(self.impurity - 6)
        return before-self.impurity

    def claim_offline(self, now: datetime) -> int:
        """Claim whole-hour offline accrual; unclaimed partial hour rolls forward.
        Only up to 8h can accrue from each claim interval, preventing long absence accumulation.
        For a full production game use server-side interval ledger and day-partition settlement.
        """
        now = self._rollover(now)
        if now < self.offline_cursor: raise ValueError('Time cannot run backwards')
        hours = min(8, int((now-self.offline_cursor).total_seconds()//3600))
        if hours == 0: return 0
        self.offline_cursor += timedelta(hours=hours)
        if now-self.offline_cursor > timedelta(hours=8):
            # Discard stale offline time so it cannot be claimed in many back-to-back requests.
            self.offline_cursor = now
        return self._gain('offline', hours*10)

    def minor_breakthrough(self) -> bool:
        if self.stage == 9: return False
        if self.stability < 40 or self.meridian_integrity < 50: return False
        if self.qi < self.capacity: return False
        self.qi -= self.capacity
        self.stage += 1
        return True

    def open_meridian(self) -> bool:
        if (self.open_meridians >= OPEN_MERIDIAN_MAX_QI_REFINING or
            self.stability < 60 or self.meridian_integrity < 70):
            return False
        cost = self.open_meridians + 3  # next meridian n costs 2+n
        if self.training_seals < cost: return False
        self.training_seals -= cost
        self.open_meridians += 1
        return True

    def refine_foundation(self, now: datetime) -> bool:
        self._rollover(now)
        if (self.foundation_essences < 3 or self.stability < 80 or
            self.meridian_integrity < 80 or self.foundation_quality >= 100 or
            self.foundation_refinements_week >= 4): return False
        self.foundation_essences -= 3
        self.foundation_quality = clamp(self.foundation_quality+2)
        self.foundation_refinements_week += 1
        return True

    def foundation_probability(self, prepared: bool=True, protective_aid: bool=False) -> float:
        raw = (50 + .3*(self.stability-70) + .2*(self.foundation_quality-50)
               + (10 if prepared else 0) + (5 if protective_aid else 0)
               - (8 if self.impurity >= 40 else 0) + 15*self.failure_count)
        return 100.0 if self.failure_count >= 3 else float(max(20, min(95, raw)))

    def attempt_foundation(self, roll: float, *, prepared: bool=True, protective_aid: bool=False) -> bool:
        """0 <= roll < 100; server supplies secure RNG in real Discord handler."""
        if not 0 <= roll < 100: raise ValueError('Roll must be within [0, 100)')
        if (self.advanced_to_foundation or self.stage != 9 or self.qi < 600 or
            self.stability < 60 or self.meridian_integrity < 60 or self.impurity >= 60 or
            not prepared): return False
        chance = self.foundation_probability(prepared, protective_aid)
        if roll < chance:
            self.qi -= 600
            self.advanced_to_foundation = True
            return True
        self.qi -= 180  # 30% of 600 reserved Qi
        self.stability = clamp(self.stability-10)
        self.meridian_integrity = clamp(self.meridian_integrity-5)
        self.impurity = clamp(self.impurity+6)
        self.failure_count = min(3,self.failure_count+1)
        return False
