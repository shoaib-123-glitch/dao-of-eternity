"""Economy v1 simulation: Spirit Stones earned vs spent over 30 days for three player types.

Pure and deterministic: reads the real reward and price tables, touches no database.
Run `python -B simulate_economy_v025.py` to print the report (it also writes
ECONOMY_SIMULATION.md). Win rate and activity counts are assumptions, stated below.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import goals_v025 as goals
from combat import ENEMIES
from combat_journey_v024 import FIRST_CLEAR
from heavens import ELIXIRS
from inventory import RECIPES
from living_dao import COMPANIONS, QUESTS, REGIONS

START = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)  # Launch day (a Friday).
DAYS = 30
WIN_RATE = 0.85
COMMISSION, TRIAL, CHAPTER, SECT_CREATE = 6, 10, 3, 20
DUAL_ROOT = 80

# Per-day activity. Regions/enemies unlock as the player grows (day index -> key).
PROFILES = {
    'Casual (15 min/day)': {'explore': 3, 'hunt': 2, 'practice': 1, 'commission': 1, 'trial': 0, 'elixirs': 1,
                            'region': ((0, 'mistwood'), (5, 'stonepeak')),
                            'enemy': ((0, 'spirit_wolf'), (4, 'stone_guardian'), (12, 'thunder_serpent'))},
    'Regular (45 min/day)': {'explore': 6, 'hunt': 5, 'practice': 2, 'commission': 2, 'trial': 1, 'elixirs': 2,
                             'region': ((0, 'mistwood'), (2, 'stonepeak'), (8, 'thunder_rift')),
                             'enemy': ((0, 'spirit_wolf'), (2, 'stone_guardian'), (6, 'thunder_serpent'))},
    'Hardcore (3 h/day)': {'explore': 15, 'hunt': 12, 'practice': 4, 'commission': 3, 'trial': 4, 'elixirs': 4,
                           'region': ((0, 'stonepeak'), (2, 'thunder_rift')),
                           'enemy': ((0, 'stone_guardian'), (1, 'thunder_serpent'), (10, 'thunder_dragon'))},
}
# One-time purchases in the order a typical player makes them.
MILESTONES = (
    ('Jade Spirit Fox', COMPANIONS['jade_fox'][1]),
    ('Mistwood Blade', RECIPES['mistwood_blade']['marks']),
    ('Found a sect', SECT_CREATE),
    ('Traveler Wraps', RECIPES['traveler_wraps']['marks']),
    ('Bond training 1→5', 8 + 16 + 24 + 32),
    ('Stormheart Charm', RECIPES['stormheart_charm']['marks']),
    ('Stormguard Robe', RECIPES['stormguard_robe']['marks']),
    ('Thunderfang Sword', RECIPES['thunderfang_sword']['marks']),
    ('Dual Root attunement', DUAL_ROOT),
)


def _stage(table, day):
    return [key for start, key in table if day >= start][-1]


def simulate(profile):
    balance, earned, spent, from_goals = 0, 0, 0, 0
    reached, pending = {}, list(MILESTONES)
    weekly = {'explore': 0, 'hunt_win': 0, 'days': 0}
    week = None
    claimed_once = set()
    first_clear = set()
    for day in range(DAYS):
        now = START + timedelta(days=day)
        if now.isocalendar()[1] != week:
            week, weekly, weekly_paid = now.isocalendar()[1], {'explore': 0, 'hunt_win': 0, 'days': 0}, set()
        income = 0
        region = _stage(profile['region'], day)
        income += profile['explore'] * REGIONS[region][1]
        enemy = _stage(profile['enemy'], day)
        wins = round(profile['hunt'] * WIN_RATE)
        income += wins * ENEMIES[enemy]['reward']
        if wins and enemy in FIRST_CLEAR and enemy not in first_clear:
            first_clear.add(enemy)
            income += FIRST_CLEAR[enemy]
        income += profile['commission'] * COMMISSION + profile['trial'] * TRIAL
        if day < 3:
            income += CHAPTER  # One story chapter per day for the first three days.
        for key, (_, metric, need, reward) in QUESTS.items():  # One-time starter quests.
            if key not in claimed_once and (metric == 'explore' and profile['explore'] * (day + 1) >= need
                                            or metric == 'contribute' and day >= 1):
                claimed_once.add(key)
                income += reward
        counts = {'explore': profile['explore'], 'hunt_win': wins, 'practice': profile['practice']}
        goal_income = 0
        done = [counts[kind] >= need for kind, need, _ in goals.daily_goals(now)]
        goal_income += sum(r for (kind, need, r), ok in zip(goals.daily_goals(now), done) if ok)
        if all(done):
            goal_income += goals.DAILY_BONUS
            weekly['days'] += 1
        weekly['explore'] += profile['explore']
        weekly['hunt_win'] += wins
        for kind, need, reward in goals.WEEKLY:
            if kind not in weekly_paid and weekly[kind] >= need:
                weekly_paid.add(kind)
                goal_income += reward
        income += goal_income
        from_goals += goal_income
        balance += income
        earned += income
        while pending and balance >= pending[0][1]:
            name, price = pending.pop(0)
            balance -= price
            spent += price
            reached[name] = day + 1
        if not pending:  # Only after gear: daily Minor Qi Elixirs as a repeatable sink.
            cost = profile['elixirs'] * ELIXIRS['minor'][3]
            if balance >= cost:
                balance -= cost
                spent += cost
    return {'earned': earned, 'spent': spent, 'balance': balance, 'per_day': earned / DAYS,
            'goal_share': from_goals / earned if earned else 0, 'reached': reached,
            'missing': [name for name, _ in pending]}


def report():
    lines = ['# Economy v1 simulation — Spirit Stones over 30 days', '',
             f'Generated by `simulate_economy_v025.py` from the real reward and price tables. Assumes a {int(WIN_RATE * 100)}% '
             'hunt win rate, one story chapter per day for the first three days, and the activity counts listed in the script. '
             'It is a model, not live data.', '',
             '| Player | Earned / day | Earned (30 d) | Spent | Balance on day 30 | Share from goals |',
             '|---|---|---|---|---|---|']
    results = {name: simulate(profile) for name, profile in PROFILES.items()}
    for name, r in results.items():
        lines.append(f'| {name} | {r["per_day"]:.0f} | {r["earned"]} | {r["spent"]} | {r["balance"]} | {r["goal_share"]:.0%} |')
    lines += ['', '## Day each purchase becomes affordable', '',
              '| Purchase | Price | ' + ' | '.join(results) + ' |', '|---|---|' + '---|' * len(results)]
    for item, price in MILESTONES:
        lines.append(f'| {item} | {price} | ' + ' | '.join(str(r['reached'].get(item, '—')) for r in results.values()) + ' |')
    hoard = [name for name, r in results.items() if r['balance'] > 1000]
    lines += ['', '## Findings', '',
              '- Daily goals add a steady but modest share of income, so skipping them is never punishing.',
              ('- **Sink needed:** ' + ', '.join(hoard) + ' end the month with over 1,000 unspent Spirit Stones. '
               'Once gear is finished, the only repeatable sink is elixirs. The planned **cave abode** upgrades should be '
               'the main long-term sink.') if hoard else '- No player type hoards more than 1,000 Spirit Stones by day 30.',
              '']
    return '\n'.join(lines), results


if __name__ == '__main__':
    text, _ = report()
    Path(__file__).with_name('ECONOMY_SIMULATION.md').write_text(text, encoding='utf-8', newline='\n')
    print(text)
