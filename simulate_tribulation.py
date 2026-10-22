"""Repeatable 10,000-trial statistical balance sample for EACH build."""
import csv
import random
from engine import Cultivator
from tribulation import start_trial, choose_phase, finish_trial

RNG = random.Random(20261019)
N = 10000
BUILDS = {
    'underprepared': dict(stability=65, foundation_quality=45, body=20, dao=20,
                          choices=('redirect','sever','resist'), aid=False),
    'regular': dict(stability=80, foundation_quality=65, body=55, dao=55,
                    choices=('endure','accept','comprehend'), aid=False),
    'specialist': dict(stability=90, foundation_quality=85, body=85, dao=85,
                       choices=('ward','remember','harmonize'), aid=True),
}


def simulate():
    rows=[]
    for name, build in BUILDS.items():
        wins=0; collapsed=0; endurance_sum=0
        for _ in range(N):
            p=Cultivator(stage=9,qi=600,stability=build['stability'],
                         meridian_integrity=95,foundation_quality=build['foundation_quality'])
            t=start_trial(p,protective_aid=build['aid'])
            for choice in build['choices']:
                choose_phase(t,choice,body_resilience=build['body'],dao_comprehension=build['dao'])
            collapsed += (t.endurance == 0)
            endurance_sum += t.endurance
            wins += finish_trial(p,t,roll=RNG.random()*100)['success']
        rows.append({'profile':name,'trials':N,'success_pct':round(wins*100/N,2),
                     'collapsed_pct':round(collapsed*100/N,2),
                     'average_endurance':round(endurance_sum/N,2)})
    return rows

if __name__=='__main__':
    rows=simulate()
    with open('tribulation_simulation.csv','w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    for row in rows: print(row)
