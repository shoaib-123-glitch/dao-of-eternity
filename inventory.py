"""Database-backed collectible items and gear (v0.17).
All grants, crafting and equip actions happen inside SQL transactions.
"""
import json
from copy import deepcopy

ITEMS = {
    'mistwood_blade': 'Mistwood Blade',
    'traveler_wraps': 'Traveler Wraps',
    'wolf_fang': 'Spirit Wolf Fang',
    'stone_core': 'Stone Guardian Core',
    'serpent_scale': 'Thunder Serpent Scale',
    'dragon_scale': 'Thunder Dragon Scale',
    'heavenly_spark': 'Heavenly Spark',
    'thunderfang_sword': 'Thunderfang Sword',
    'stormguard_robe': 'Stormguard Robe',
    'stormheart_charm': 'Stormheart Charm',
}
DROPS = {
    'spirit_wolf': {'wolf_fang': 1},
    'stone_guardian': {'stone_core': 1},
    'thunder_serpent': {'serpent_scale': 1},
    'thunder_dragon': {'dragon_scale': 1, 'heavenly_spark': 1},
}
# A successful battle grants these combat marks once; crafted gear costs marks and drops.
RECIPES = {
    'mistwood_blade': {'slot': 'weapon', 'marks': 4, 'ingredients': {'wolf_fang': 1}, 'attack': 2},
    'traveler_wraps': {'slot': 'robe', 'marks': 6, 'ingredients': {'wolf_fang': 2}, 'hp': 8, 'ward': 1},
    'thunderfang_sword': {'slot': 'weapon', 'marks': 40, 'ingredients': {'dragon_scale': 1, 'heavenly_spark': 1}, 'attack': 5},
    'stormguard_robe': {'slot': 'robe', 'marks': 24, 'ingredients': {'stone_core': 2, 'serpent_scale': 1}, 'hp': 20, 'ward': 2},
    'stormheart_charm': {'slot': 'charm', 'marks': 20, 'ingredients': {'wolf_fang': 3, 'serpent_scale': 1}, 'hp': 10, 'attack': 2},
}
SCHEMA = """CREATE TABLE IF NOT EXISTS player_inventory (
 guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 holdings JSONB NOT NULL DEFAULT '{}'::jsonb, marks BIGINT NOT NULL DEFAULT 0,
 equipped JSONB NOT NULL DEFAULT '{}'::jsonb,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (guild_id,user_id));"""

def normalized(raw):
    if isinstance(raw,str): return json.loads(raw)
    return dict(raw or {})

def empty(): return {'holdings':{},'marks':0,'equipped':{}}

def award(state, enemy, marks):
    if enemy not in DROPS: raise ValueError('Unrecognized enemy drop table.')
    s=deepcopy(state)
    s['marks']+=marks
    for key, count in DROPS[enemy].items():
        s['holdings'][key]=s['holdings'].get(key,0)+count
    return s

def craft(state, item):
    if item not in RECIPES: raise ValueError('Unknown crafting recipe. See /recipes.')
    s=deepcopy(state); recipe=RECIPES[item]
    if s['marks']<recipe['marks']: raise ValueError(f'Need {recipe["marks"]} combat marks to craft {ITEMS[item]}.')
    for key, qty in recipe['ingredients'].items():
        if s['holdings'].get(key,0)<qty:
            raise ValueError(f'Need {qty}x {ITEMS[key]} to craft {ITEMS[item]}.')
    s['marks']-=recipe['marks']
    for key, qty in recipe['ingredients'].items(): s['holdings'][key]-=qty
    s['holdings'][item]=s['holdings'].get(item,0)+1
    return s

def equip(state, item):
    if item not in RECIPES: raise ValueError('That item cannot be equipped.')
    s=deepcopy(state)
    if s['holdings'].get(item,0)<1: raise ValueError(f'You do not own {ITEMS[item]}.')
    s['equipped'][RECIPES[item]['slot']]=item
    return s

def bonuses(state):
    result={'attack':0,'hp':0,'ward':0}
    for slot,item in state.get('equipped',{}).items():
        recipe=RECIPES.get(item)
        if not recipe or recipe['slot']!=slot or state['holdings'].get(item,0)<1: continue
        for stat in result: result[stat]+=recipe.get(stat,0)
    return result

def format_inventory(state):
    lines=[f'Combat marks: {state["marks"]}', 'Materials and equipment:']
    stock=[f'• {ITEMS[k]} ×{q}' for k,q in sorted(state['holdings'].items()) if q>0 and k in ITEMS]
    lines+=stock or ['• Empty — defeat beasts to earn drops.']
    lines.append('Equipped: '+(', '.join(f'{slot}: {ITEMS.get(item,item)}' for slot,item in sorted(state['equipped'].items())) or 'none'))
    stats=bonuses(state)
    lines.append(f'Bonuses: +{stats["attack"]} damage, +{stats["hp"]} HP, -{stats["ward"]} incoming damage')
    return '\n'.join(lines)

def format_recipes():
    lines=['Crafting recipes (materials and marks earned through gameplay):']
    for key, spec in RECIPES.items():
        ingredients=', '.join(f'{qty}× {ITEMS[material]}' for material,qty in spec['ingredients'].items())
        bonus=', '.join(f'+{spec[stat]} {stat}' for stat in ('attack','hp','ward') if spec.get(stat))
        lines.append(f'• {ITEMS[key]}: {ingredients} + {spec["marks"]} marks ({spec["slot"]}; {bonus})')
    return '\n'.join(lines)

async def setup(repo):
    async with repo.pool.acquire() as con: await con.execute(SCHEMA)

async def _locked(con,guild_id,user_id):
    await con.execute('INSERT INTO player_inventory (guild_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING',guild_id,user_id)
    row=await con.fetchrow('SELECT holdings,marks,equipped FROM player_inventory WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',guild_id,user_id)
    return {'holdings':normalized(row['holdings']),'marks':int(row['marks']),'equipped':normalized(row['equipped'])}

async def _save(con,guild_id,user_id,state):
    await con.execute('UPDATE player_inventory SET holdings=$3::jsonb, marks=$4, equipped=$5::jsonb, updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',guild_id,user_id,json.dumps(state['holdings']),state['marks'],json.dumps(state['equipped']))

async def current(repo,guild_id,user_id):
    async with repo.pool.acquire() as con:
        row=await con.fetchrow('SELECT holdings,marks,equipped FROM player_inventory WHERE guild_id=$1 AND user_id=$2',guild_id,user_id)
        if row is None: return empty()
        return {'holdings':normalized(row['holdings']),'marks':int(row['marks']),'equipped':normalized(row['equipped'])}

async def change(repo,guild_id,user_id,operation,item,*,event_id=None):
    async with repo.pool.acquire() as con:
        async with con.transaction():
            if not await con.fetchval('SELECT 1 FROM characters WHERE guild_id=$1 AND user_id=$2',guild_id,user_id):
                raise ValueError('Create a character with /start first.')
            state=await _locked(con,guild_id,user_id)
            # Serialize receipt lookup with the inventory mutation. Production
            # Discord callers supply their interaction ID; old Python callers
            # retain the existing API. No new table or migration is needed.
            if event_id is not None:
                prior=await con.fetchval('SELECT response FROM processed_actions WHERE guild_id=$1 AND user_id=$2 AND event_id=$3',guild_id,user_id,event_id)
                if prior is not None:return prior
            if operation=='craft': state=craft(state,item)
            elif operation=='equip': state=equip(state,item)
            else: raise ValueError('Unknown inventory operation.')
            await _save(con,guild_id,user_id,state)
            reply=f'{"Crafted" if operation=="craft" else "Equipped"} {ITEMS[item]}!\n'+format_inventory(state)
            if event_id is not None:
                await con.execute('INSERT INTO processed_actions VALUES ($1,$2,$3,$4)',guild_id,user_id,event_id,reply)
            return reply
