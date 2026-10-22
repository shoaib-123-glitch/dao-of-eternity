"""Run: python simulate.py  (standard library only)."""
from datetime import datetime, timedelta, timezone
from engine import Cultivator

PROFILES = {
    'Casual (10–15 min/day)': (1, 1, 4),
    'Regular (30 min/day)': (3, 2, 8),
    'Highly active (60+ min/day)': (4, 2, 8),
}

def run_profile(active_count, training_count, offline_hours, days=30):
    p = Cultivator()
    start = datetime(2026, 10, 19, tzinfo=timezone.utc)
    p.offline_cursor = start
    total_earned = 0
    stage9_day = None
    ready_day = None
    daily = []
    for day in range(days):
        base = start + timedelta(days=day)
        earned = 0
        for i in range(active_count):
            earned += p.active_meditation(base+timedelta(hours=8, minutes=20*i))
            if p.minor_breakthrough() and p.stage==9 and stage9_day is None: stage9_day=day+1
        for i in range(training_count):
            earned += p.training(base+timedelta(hours=12,minutes=35*i))
            if p.minor_breakthrough() and p.stage==9 and stage9_day is None: stage9_day=day+1
        # To simulate actual eligible offline hours, settlement after 20:00 UTC.
        earned += p.claim_offline(base+timedelta(hours=20))
        if p.minor_breakthrough() and p.stage==9 and stage9_day is None: stage9_day=day+1
        if p.stage==9 and p.qi==600 and ready_day is None: ready_day=day+1
        total_earned += earned
        daily.append((day+1,p.stage,p.qi,earned))
    return p, total_earned, stage9_day, ready_day, daily

if __name__=='__main__':
    print('PROFILE | STAGE9 DAY | FOUNDATION READY DAY | DAY30 STAGE | DAY30 QI | TOTAL AWARDED')
    for name,counts in PROFILES.items():
        p,earned,stage9,ready,_ = run_profile(*counts)
        print(f'{name} | {stage9} | {ready} | {p.stage} | {p.qi} | {earned}')
