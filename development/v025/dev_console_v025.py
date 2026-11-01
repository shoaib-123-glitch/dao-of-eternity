"""Developer testing console for an explicitly authorized v0.25 sandbox only.

Disabled unless the gated launcher supplies a complete console configuration,
the target is the v0.25 candidate sandbox and the database carries the sandbox
marker comment. Actions come from a fixed registry of bounded edits to the
acting developer's or an allowlisted test account's own rows. There is no SQL
text, shell, arbitrary field path or multiplayer shortcut. Every committed
action is receipted by interaction ID, audited and marks the character as
developer-modified so it is never mistaken for real progression.
"""
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MARKER = 'dao-v025-developer-sandbox'
FLAG = 'sandbox-only-explicitly-authorized'
ENV = ('DAO_DEV_CONSOLE', 'DAO_DEV_CONSOLE_DATABASE', 'DAO_DEV_CONSOLE_SYSTEM_IDENTIFIER',
       'DAO_DEV_CONSOLE_GUILD_ID', 'DAO_DEV_CONSOLE_DEVELOPER_IDS', 'DAO_DEV_CONSOLE_TARGET_IDS')
SCHEMA = '''CREATE TABLE IF NOT EXISTS dev_console_actions (
 guild_id BIGINT NOT NULL, event_id BIGINT NOT NULL,
 developer_id BIGINT NOT NULL, target_id BIGINT NOT NULL,
 action TEXT NOT NULL, params JSONB NOT NULL, response TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY (guild_id, event_id));'''
IDENTITY_SQL = '''SELECT pg_catalog.current_database()::text AS database_name,
 (SELECT system_identifier::text FROM pg_catalog.pg_control_system()) AS system_identifier,
 COALESCE(pg_catalog.shobj_description(d.oid, 'pg_database'), '') AS marker
 FROM pg_catalog.pg_database d WHERE d.datname = pg_catalog.current_database()'''
UNAVAILABLE = 'Developer console unavailable.'
# Same global order as gameplay: combat -> character -> companion -> inventory.
# The sect hall row is locked last and only by actions that need nothing else.
LOCK_ORDER = ('combat', 'character', 'living', 'inventory', 'hall')
CATEGORIES = {'cooldown': 'Cooldowns', 'resource': 'Test resources', 'combat': 'Combat scenarios',
              'companion': 'Companion', 'story': 'Story', 'sect': 'Sect preparation',
              'progress': 'Goals, abode and omen'}
_ACTIVE = None


class ConsoleRejected(ValueError):
    """Fixed, player-safe messages; never formatted from stored data or SQL."""


@dataclass(frozen=True)
class ConsoleSettings:
    guild_id: int
    database: str
    system_identifier: int
    developers: frozenset
    targets: frozenset

    def may_target(self, user):
        return type(user) is int and (user in self.developers or user in self.targets)


def _ids(raw, low, high):
    parts = raw.split(',') if type(raw) is str and raw else []
    if type(raw) is not str or not low <= len(parts) <= high:
        raise ValueError
    if any(not re.fullmatch('[1-9][0-9]{0,19}', part) for part in parts):
        raise ValueError
    values = frozenset(int(part) for part in parts)
    if len(values) != len(parts) or any(value >= 2**64 for value in values):
        raise ValueError
    return values


def settings_from_environment(config, env):
    """None when no console variable exists; any partial setup rejects startup."""
    from private_playtest import PlaytestRejected, PrivatePlaytestConfig
    if not any(name in env for name in ENV):
        return None
    try:
        if env.get('DAO_DEV_CONSOLE') != FLAG:
            raise ValueError
        if type(config) is not PrivatePlaytestConfig or config.readonly:
            raise ValueError
        if env.get('APP_ENV') != 'private-playtest' or env.get('DAO_PLAYTEST_AUTHORIZED') != 'writes-and-discord':
            raise ValueError
        from candidate_policy_v025 import authorize
        authorize(config, env)
        if (env.get('DAO_DEV_CONSOLE_DATABASE') != config.database
                or env.get('DAO_DEV_CONSOLE_SYSTEM_IDENTIFIER') != str(config.system_identifier)
                or env.get('DAO_DEV_CONSOLE_GUILD_ID') != str(config.guild_id)):
            raise ValueError
        developers = _ids(env.get('DAO_DEV_CONSOLE_DEVELOPER_IDS'), 1, 3)
        targets = _ids(env.get('DAO_DEV_CONSOLE_TARGET_IDS', ''), 0, 10)
    except (ValueError, TypeError, PlaytestRejected):
        raise PlaytestRejected('DEV_CONSOLE_CONFIGURATION_REJECTED') from None
    return ConsoleSettings(config.guild_id, config.database, config.system_identifier, developers, targets)


async def _verify(con, settings, error):
    row = await con.fetchrow(IDENTITY_SQL)
    if (row is None or row['database_name'] != settings.database
            or row['system_identifier'] != str(settings.system_identifier) or row['marker'] != MARKER):
        raise error


async def activate(repo, settings):
    """Verify the sandbox marker before creating the audit table or enabling."""
    from private_playtest import PlaytestRejected
    global _ACTIVE
    _ACTIVE = None
    if type(settings) is not ConsoleSettings:
        raise PlaytestRejected('DEV_CONSOLE_CONFIGURATION_REJECTED')
    async with repo.pool.acquire() as con:
        async with con.transaction():
            await _verify(con, settings, PlaytestRejected('DEV_CONSOLE_SANDBOX_IDENTITY_REJECTED'))
            await con.execute(SCHEMA)
    _ACTIVE = settings
    return settings


def deactivate():
    global _ACTIVE
    _ACTIVE = None


def active():
    return _ACTIVE


def authorized(guild_id, user_id):
    """Server-side check for every command, button, select and modal."""
    settings = _ACTIVE
    return (settings is not None and type(guild_id) is int and guild_id == settings.guild_id
            and type(user_id) is int and user_id in settings.developers)


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    low: int = 0
    high: int = 0
    choices: tuple = ()

    def parse(self, raw):
        if self.choices:
            raw = raw.strip().lower() if type(raw) is str else raw
            if raw not in self.choices:
                raise ConsoleRejected(f'{self.label}: choose one of {", ".join(self.choices)}.')
            return raw
        if type(raw) is str and re.fullmatch('[0-9]{1,6}', raw.strip()):
            raw = int(raw.strip())
        if type(raw) is not int or not self.low <= raw <= self.high:
            raise ConsoleRejected(f'{self.label}: enter a whole number from {self.low} to {self.high}.')
        return raw


@dataclass(frozen=True)
class DevAction:
    key: str
    label: str
    category: str
    rows: tuple
    apply: object
    params: tuple = ()
    help: str = ''


REGISTRY = {}


def register(action):
    """Future systems add bounded actions here instead of new slash commands."""
    if (type(action) is not DevAction or not re.fullmatch('[a-z][a-z0-9_]{2,40}', action.key)
            or action.key in REGISTRY or action.category not in CATEGORIES
            or 'character' not in action.rows or len(set(action.rows)) != len(action.rows)
            or tuple(row for row in LOCK_ORDER if row in action.rows) != tuple(action.rows)
            or ('hall' in action.rows and action.rows != ('character', 'hall'))
            or len(action.params) > 5 or not callable(action.apply)
            or not 1 <= len(action.label) <= 80 or len(action.help) > 100
            or any(type(p) is not Param for p in action.params)):
        raise ValueError('Invalid developer action registration.')
    REGISTRY[action.key] = action
    return action


def _int(value, default=0):
    if type(value) is bool:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _living(rows):
    if rows.get('living') is None:
        raise ConsoleRejected('That account has no Living Dao record yet. Explore once with it first.')
    return rows['living']


def _beast(rows, now):
    from beast_v022 import settle
    if not _living(rows).get('companion'):
        raise ConsoleRejected('That account has no companion. Adopt one normally first.')
    rows['living'] = settle(rows['living'], now)
    beast = rows['living'].get('beast_cultivation')
    if not isinstance(beast, dict):
        raise ConsoleRejected('Open /beast_cultivation once with that account first.')
    return beast


def _battle(rows):
    battle = rows.get('combat') or {}
    if battle.get('status') != 'active':
        raise ConsoleRejected('No active battle. Start a hunt with that account, then stage the scenario.')
    battle['dev_staged'] = True
    return battle


def _cleared(mapping, keys, label, rule):
    removed = [key for key in keys if isinstance(mapping, dict) and mapping.pop(key, None) is not None]
    return f'{label} was already ready; nothing changed.' if not removed else f'{label} is ready now. {rule}'


def _story(rows):
    from living_world_v025 import _story as normalize
    living = _living(rows)
    story = normalize(living)
    living['living_world_v025'] = story
    return story


# ---- cooldowns -------------------------------------------------------------

def _practice_ready(rows, params, now, target):
    info = rows['character'].get('cultivation_v023')
    return _cleared(info, ('practice_next_at',), 'Cultivation practice', 'The normal 6-hour rule applies after the next practice.')


def _focus_ready(rows, params, now, target):
    info = rows['character'].get('cultivation_v023')
    return _cleared(info, ('focus_next_at',), 'Cultivation focus change', 'The normal 6-hour rule applies afterwards.')


def _companion_ready(rows, params, now, target):
    info = _living(rows).get('companion_v024')
    return _cleared(info, ('last_at',), 'Free companion activity',
                    'This also ends any current 12-hour battle attunement.')


def _recovery_ready(rows, params, now, target):
    info = (rows.get('combat') or {}).get('trail_v024')
    return _cleared(info, ('rest_next_at',), 'Camp breathing (/recover)', 'The normal 2-minute rule applies afterwards.')


def _exploration_ready(rows, params, now, target):
    living = _living(rows)
    if not living.get('last_explore'):
        return 'Exploration was already ready; nothing changed.'
    living['last_explore'] = None
    return 'Exploration is ready now. The normal 5-minute rule applies afterwards.'


def _expedition_return_now(rows, params, now, target):
    import expeditions_v026 as expeditions
    trip = expeditions._trip(_living(rows))
    if not trip['route']:
        return 'The companion is not on a trip; nothing changed.'
    hours = expeditions.ROUTES[trip['route']][1]
    _living(rows)['expedition_v026']['sent_at'] = (now - timedelta(hours=hours)).isoformat()
    return 'The companion is back now. Welcome it home with /companion; the haul is unchanged.'


def _harvest_ready(rows, params, now, target):
    info = _living(rows).get('abode_v025')
    return _cleared(info, ('harvest_day',), 'Herb garden harvest', 'The once-per-UTC-day rule applies afterwards.')


def _all_cooldowns_ready(rows, params, now, target):
    """Every waiting timer at once, so a tester can keep going after a scenario is verified."""
    done = []
    for label, helper in (('exploration', _exploration_ready), ('practice', _practice_ready), ('focus', _focus_ready),
                          ('companion activity', _companion_ready), ('camp breathing', _recovery_ready),
                          ('teacher lesson', _lesson_ready), ('companion trip', _expedition_return_now),
                          ('garden harvest', _harvest_ready)):
        try:
            if 'nothing changed' not in helper(rows, params, now, target):
                done.append(label)
        except ConsoleRejected:
            continue
    if not done:
        return 'Everything was already ready; nothing changed.'
    return 'Ready now: ' + ', '.join(done) + '. Normal rules apply afterwards. Daily bonuses and lore are unchanged.'


def _world_daily_ready(rows, params, now, target):
    info = _living(rows).get('world_v024')
    return _cleared(info, ('chapter_day', 'rare_day'), 'Featured-region and rare daily bonuses',
                    'Each can occur once more this UTC day.')


def _lesson_ready(rows, params, now, target):
    story = _story(rows)
    return _cleared(story, ('training_day',), 'Teacher lesson', 'The shared once-per-UTC-day rule applies afterwards.')


def _lore_ready(rows, params, now, target):
    story = _story(rows)
    return _cleared(story, ('lore_day',), 'Rare story lore', 'It remains cosmetic and capped at 30.')


def _commission_ready(rows, params, now, target):
    records = rows['hall'].get('commissions')
    member = records.get(str(target)) if isinstance(records, dict) else None
    if not isinstance(member, dict) or (not member.get('last_at') and not _int(member.get('count'))):
        return 'Sect commissions were already ready; nothing changed.'
    member.pop('last_at', None)
    member['count'] = 0
    return ('Your own sect commission cooldown and daily count are cleared. '
            'Other members, renown and shared projects are unchanged; projects still need genuine distinct players.')


# ---- resources -------------------------------------------------------------

MATERIALS = ('wolf_fang', 'stone_core', 'serpent_scale', 'dragon_scale', 'heavenly_spark')


def _set_qi(rows, params, now, target):
    from game import unpack_player
    character = rows['character']
    if character.get('trial') is not None:
        raise ConsoleRejected('A breakthrough trial is active. Finish or abandon it first.')
    try:
        player = unpack_player(character['player'])
    except (KeyError, TypeError, ValueError):
        raise ConsoleRejected('That character has no readable cultivation record.') from None
    if params['qi'] > player.capacity:
        raise ConsoleRejected(f'Stage {player.stage} holds at most {player.capacity} Qi.')
    character['player']['qi'] = params['qi']
    return f'Cultivation Qi set to {params["qi"]}/{player.capacity}. Daily earning limits were not used or changed.'


def _set_camp_hp(rows, params, now, target):
    battle = rows.get('combat') or {}
    info = battle.get('trail_v024')
    if battle.get('status') == 'active' or not isinstance(info, dict):
        raise ConsoleRejected('Camp HP needs a finished hunt and no active battle.')
    maximum = _int(info.get('maximum'), 95)
    if params['hp'] > maximum:
        raise ConsoleRejected(f'Camp HP cannot exceed {maximum}.')
    info['hp'] = params['hp']
    info['last_at'] = now.isoformat()  # natural healing restarts from now
    return f'Camp HP set to {params["hp"]}/{maximum}. Natural recovery continues at 1 HP per 5 seconds.'


def _set_battle_hp(rows, params, now, target):
    battle = _battle(rows)
    if params['hp'] > battle['max_hp']:
        raise ConsoleRejected(f'Battle HP cannot exceed {battle["max_hp"]}.')
    battle['hp'] = params['hp']
    return f'Battle HP set to {battle["hp"]}/{battle["max_hp"]}.'


def _set_battle_qi(rows, params, now, target):
    battle = _battle(rows)
    if params['qi'] > battle['max_qi']:
        raise ConsoleRejected(f'Combat Qi cannot exceed {battle["max_qi"]}.')
    battle['qi'] = params['qi']
    return f'Combat Qi set to {battle["qi"]}/{battle["max_qi"]}.'


def _set_marks(rows, params, now, target):
    rows['inventory']['marks'] = params['marks']
    return f'Spirit Stones set to {params["marks"]} on this developer-marked test account.'


def _set_material(rows, params, now, target):
    from inventory import ITEMS
    rows['inventory']['holdings'][params['item']] = params['quantity']
    return f'{ITEMS[params["item"]]} set to {params["quantity"]}.'


def _set_beast_qi(rows, params, now, target):
    from beast_v022 import cap
    beast = _beast(rows, now)
    limit = cap(_int(beast.get('stage'), 1))
    if params['qi'] > limit:
        raise ConsoleRejected(f'This beast stage holds at most {limit} Qi.')
    beast['qi'] = params['qi']
    return f'Beast Qi set to {params["qi"]}/{limit}. Banked Qi is unchanged.'


# ---- combat scenarios --------------------------------------------------------

def _heavy_next(rows, params, now, target):
    battle = _battle(rows)
    battle['turn'] = _int(battle.get('turn')) + (2 - _int(battle.get('turn')) % 3) % 3
    return 'The enemy telegraphs a heavy attack next turn. Test Guard, Second Wind and Assisted pausing.'


def _second_wind(rows, params, now, target):
    battle = _battle(rows)
    battle.update(hp=1, qi=0, second_wind_used=False)
    return 'Staged: 1 HP, 0 Combat Qi, Second Wind unused. Automation should choose Second Wind.'


def _near_victory(rows, params, now, target):
    battle = _battle(rows)
    battle['enemy_hp'] = 1
    return 'Staged: the enemy has 1 HP. The next hit tests the normal victory payout.'


def _near_defeat(rows, params, now, target):
    battle = _battle(rows)
    battle.update(hp=1, second_wind_used=True, guard=False)
    return 'Staged: 1 HP and Second Wind spent. The next enemy hit tests defeat; Retreat tests the no-reward exit.'


# ---- companion ---------------------------------------------------------------

def _companion_trust(rows, params, now, target):
    from companion_bonds_v024 import TRUST_PER_LEVEL
    if not _living(rows).get('companion'):
        raise ConsoleRejected('That account has no companion. Adopt one normally first.')
    if params['trust'] >= TRUST_PER_LEVEL:
        raise ConsoleRejected(f'Companion trust must be below {TRUST_PER_LEVEL}.')
    rows['living'].setdefault('companion_v024', {})['trust'] = params['trust']
    return f'Companion trust set to {params["trust"]}/{TRUST_PER_LEVEL}.'


def _bond_level(rows, params, now, target):
    if not _living(rows).get('companion'):
        raise ConsoleRejected('That account has no companion. Adopt one normally first.')
    rows['living']['bond'] = params['level']
    return f'Bond level set to {params["level"]}/5.'


def _beast_stage(rows, params, now, target):
    beast = _beast(rows, now)
    beast['stage'] = params['stage']
    beast['qi'] = 0
    return f'Spirit beast set to Stage {params["stage"]}/9 with 0 Qi. Advancing still uses /beast_cultivation.'


def _prepare_evolution(rows, params, now, target):
    from beast_v022 import cap
    beast = _beast(rows, now)
    if _int(beast.get('evolutions')) >= 2:
        raise ConsoleRejected('This beast has reached its highest evolution.')
    beast['stage'] = 9
    beast['qi'] = cap(9)
    return 'Spirit beast set to Stage 9 with full Qi. Evolve it manually with the normal command.'


# ---- story -------------------------------------------------------------------

NPC_KEYS = ('jian', 'yue', 'zhao')


def _region_proof(rows, params, now, target):
    import arc2_v026 as arc2
    from living_world_v025 import encounter, panel, region_name
    if arc2.unlocked(_living(rows)):
        view = arc2.panel(rows['living'])
        if view['complete']:
            raise ConsoleRejected('Both arcs are complete. Rewind first.')
        rows['living'], _ = arc2.encounter(rows['living'], view['region'], now)
        return f'Region proof granted for {view["title"]} ({region_name(view["region"])}). No expedition rewards or cooldown used.'
    view = panel(_living(rows))
    if view['complete']:
        raise ConsoleRejected('The arc is complete. Rewind it first.')
    rows['living'], _ = encounter(rows['living'], view['region'], now)
    return f'Region proof granted for {view["title"]} ({region_name(view["region"])}). No expedition rewards or cooldown used.'


def _resolve_chapter(rows, params, now, target):
    import arc2_v026 as arc2
    from living_world_v025 import CHAPTERS, choose, encounter, panel
    if arc2.unlocked(_living(rows)):
        view = arc2.panel(rows['living'])
        if view['complete']:
            raise ConsoleRejected('Both arcs are complete. Rewind first.')
        if not view['ready']:
            rows['living'], _ = arc2.encounter(rows['living'], view['region'], now)
        index = view['chapter'] - arc2.OFFSET
        choice = arc2.CHAPTERS[index]['choices'][params['option'] - 1][0]
        rows['living'], reply, _ = arc2.choose(rows['living'], view['chapter'], choice, now)
        return 'Resolved through the normal Arc II rules (Arc II never pays Spirit Stones).\n' + reply
    view = panel(_living(rows))
    if view['complete']:
        raise ConsoleRejected('The arc is complete. Rewind it first.')
    if not view['ready']:
        rows['living'], _ = encounter(rows['living'], view['region'], now)
    choice = CHAPTERS[view['chapter']]['choices'][params['option'] - 1][0]
    rows['living'], reply, _ = choose(rows['living'], view['chapter'], choice, now)
    return 'Resolved through the normal story rules; developer resolutions pay no Spirit Stones.\n' + reply


def _rewind(rows, params, now, target):
    import arc2_v026 as arc2
    chapter = params['chapter']
    if chapter >= arc2.OFFSET:
        if not arc2.unlocked(_living(rows)):
            raise ConsoleRejected('Arc II is not unlocked yet. Use Resolve chapter to advance.')
        arc = arc2._arc(rows['living'])
        index = chapter - arc2.OFFSET
        if index > arc['chapter']:
            raise ConsoleRejected('Rewind only moves backwards. Use Resolve chapter to advance.')
        rows['living']['arc2_v026'] = {'choices': {k: v for k, v in arc['choices'].items() if int(k) < index},
                                       'titles': [c['honor'] for c in arc2.CHAPTERS[:index] if c['honor'] in arc['titles']]}
        return (f'Arc II rewound to chapter {chapter + 1}. Titles from later chapters are removed; '
                'NPC trust and memories are kept. Arc II never pays Spirit Stones.')
    _living(rows).pop('arc2_v026', None)  # Rewinding into Arc I restarts Arc II too.
    story = _story(rows)
    if chapter > story['chapter']:
        raise ConsoleRejected('Rewind only moves backwards. Use Resolve chapter to advance.')
    story['chapter'] = chapter
    story['choices'] = {key: value for key, value in story['choices'].items() if _int(key, 9) < chapter}
    story['fragments'] = chapter
    story.pop('encounter', None)
    if chapter < 3:
        story.pop('resolution', None)
    if chapter < 2:
        story.pop('technique', None)
        story.pop('technique_style', None)
    # story['paid'] is deliberately kept: replaying a chapter never pays its marks again.
    return (f'Story rewound to chapter {chapter + 1}. NPC trust and memories are kept; adjust them separately. '
            'Chapters already paid stay paid, so replaying them earns no story Spirit Stones.')


def _npc_trust(rows, params, now, target):
    from living_world_v025 import MAX_TRUST, NPCS
    story = _story(rows)
    story['npcs'].setdefault(params['npc'], {'memories': []})['trust'] = min(MAX_TRUST, params['trust'])
    return f'{NPCS[params["npc"]]["name"]} trust set to {min(MAX_TRUST, params["trust"])}/{MAX_TRUST}.'


def _clear_memories(rows, params, now, target):
    from living_world_v025 import NPCS
    story = _story(rows)
    story['npcs'].setdefault(params['npc'], {'trust': 0})['memories'] = []
    return f'{NPCS[params["npc"]]["name"]} memories cleared. Trust is unchanged.'


def _set_form(rows, params, now, target):
    from living_world_v025 import TEACHERS
    story = _story(rows)
    if story['chapter'] < 2:
        raise ConsoleRejected('Personal forms unlock after the Stonepeak chapter.')
    name = next(form for _, form, style in TEACHERS.values() if style == params['style'])
    story['technique'] = name
    story['technique_style'] = params['style']
    return f'Personal form set to {name}. New battles snapshot it; active battles keep their old snapshot.'


def _form_practice(rows, params, now, target):
    info = rows['character'].setdefault('dao_practice_v025', {})
    info['practices'] = params['count']
    return f'Form preparation set to {params["count"]}/9 (rank {min(3, 1 + params["count"] // 4)}).'


def _complete_goals(rows, params, now, target):
    import goals_v025 as goals
    living = _living(rows)
    state = goals._goals(living, now)
    if params['scope'] == 'daily':
        for kind, need, _ in goals.daily_goals(now):
            state['daily'][kind] = max(state['daily'][kind], need)
    else:
        for kind, need, _ in goals.WEEKLY:
            state['weekly'][kind] = max(state['weekly'][kind], need)
    living['goals_v025'] = state
    return (f'{params["scope"].title()} goals are now complete but unclaimed. Claim them with '
            f'/quest_claim quest:{params["scope"].title()} goals to test the real payout.')


def _set_abode_level(rows, params, now, target):
    import abode_v025 as abode
    living = _living(rows)
    state = abode._abode(living)
    state['levels'][params['room']] = params['level']
    living['abode_v025'] = state
    total, title = abode.rank(living)
    return f'{abode.ROOMS[params["room"]][0]} set to level {params["level"]}. Abode rank: {title} ({total}). Nothing was paid.'


def _set_omen_share(rows, params, now, target):
    import omen_v026 as omen
    living = _living(rows)
    share = omen._share(living, now)
    share['count'] = params['count']
    living['omen_v026'] = share
    return (f'Your Heavenly Omen share this week is {params["count"]}/{omen.PERSONAL_CAP}. The server total is the sum of '
            f'every player\'s share (tiers at {", ".join(str(n) for n, _ in omen.TIERS)}); use test accounts to reach them.')


def _reset_omen_claims(rows, params, now, target):
    import omen_v026 as omen
    living = _living(rows)
    share = omen._share(living, now)
    if not share['claimed']:
        return 'No Heavenly Omen tiers were claimed this week; nothing changed.'
    share['claimed'] = 0
    living['omen_v026'] = share
    return 'Heavenly Omen claims reset for this week, so the tier payout can be tested again.'


def _set_sect_merit(rows, params, now, target):
    import sect_merit_v025 as merit
    member = merit._member(rows['hall'], target)
    member['merit'] = params['merit']
    rows['hall'].update(merit._store(rows['hall'], target, member))
    return f'Your sect merit set to {params["merit"]}. Shop purchases and titles are unchanged.'


for _action in (
    DevAction('practice_ready', 'Cultivation practice ready', 'cooldown', ('character',), _practice_ready),
    DevAction('focus_ready', 'Cultivation focus change ready', 'cooldown', ('character',), _focus_ready),
    DevAction('companion_activity_ready', 'Companion activity ready', 'cooldown', ('character', 'living'), _companion_ready),
    DevAction('recovery_ready', 'Camp breathing ready', 'cooldown', ('combat', 'character'), _recovery_ready),
    DevAction('exploration_ready', 'Exploration ready', 'cooldown', ('character', 'living'), _exploration_ready),
    DevAction('all_cooldowns_ready', 'Skip all waits (exploration, practice, trips…)', 'cooldown',
              ('combat', 'character', 'living'), _all_cooldowns_ready),
    DevAction('expedition_return_now', 'Companion trip returns now', 'cooldown', ('character', 'living'), _expedition_return_now),
    DevAction('abode_harvest_ready', 'Garden harvest ready', 'cooldown', ('character', 'living'), _harvest_ready),
    DevAction('world_daily_ready', 'Featured and rare daily bonus ready', 'cooldown', ('character', 'living'), _world_daily_ready),
    DevAction('teacher_lesson_ready', 'Teacher lesson ready', 'cooldown', ('character', 'living'), _lesson_ready),
    DevAction('story_lore_ready', 'Rare story lore ready', 'cooldown', ('character', 'living'), _lore_ready),
    DevAction('set_cultivation_qi', 'Set cultivation Qi', 'resource', ('character',), _set_qi,
              (Param('qi', 'Cultivation Qi', 0, 600),)),
    DevAction('set_camp_hp', 'Set camp HP', 'resource', ('combat', 'character'), _set_camp_hp,
              (Param('hp', 'Camp HP', 1, 999),)),
    DevAction('set_battle_hp', 'Set battle HP', 'resource', ('combat', 'character'), _set_battle_hp,
              (Param('hp', 'Battle HP', 1, 999),)),
    DevAction('set_battle_qi', 'Set Combat Qi', 'resource', ('combat', 'character'), _set_battle_qi,
              (Param('qi', 'Combat Qi', 0, 30),)),
    DevAction('set_marks', 'Set Spirit Stones', 'resource', ('character', 'inventory'), _set_marks,
              (Param('marks', 'Spirit Stones', 0, 500),)),
    DevAction('set_material', 'Set a material', 'resource', ('character', 'inventory'), _set_material,
              (Param('item', 'Material', choices=MATERIALS), Param('quantity', 'Quantity', 0, 20))),
    DevAction('set_beast_qi', 'Set beast Qi', 'resource', ('character', 'living'), _set_beast_qi,
              (Param('qi', 'Beast Qi', 0, 400),)),
    DevAction('stage_heavy_next', 'Heavy attack next turn', 'combat', ('combat', 'character'), _heavy_next),
    DevAction('stage_second_wind', 'Second Wind emergency', 'combat', ('combat', 'character'), _second_wind),
    DevAction('stage_near_victory', 'Enemy at 1 HP', 'combat', ('combat', 'character'), _near_victory),
    DevAction('stage_near_defeat', 'Player at 1 HP, Second Wind spent', 'combat', ('combat', 'character'), _near_defeat),
    DevAction('set_companion_trust', 'Set companion trust', 'companion', ('character', 'living'), _companion_trust,
              (Param('trust', 'Companion trust', 0, 19),)),
    DevAction('set_bond_level', 'Set bond level', 'companion', ('character', 'living'), _bond_level,
              (Param('level', 'Bond level', 1, 5),)),
    DevAction('set_beast_stage', 'Set beast stage', 'companion', ('character', 'living'), _beast_stage,
              (Param('stage', 'Beast stage', 1, 9),)),
    DevAction('prepare_evolution', 'Meet evolution prerequisites', 'companion', ('character', 'living'), _prepare_evolution),
    DevAction('story_region_proof', 'Grant current region proof', 'story', ('character', 'living'), _region_proof),
    DevAction('story_resolve_chapter', 'Resolve current chapter', 'story', ('character', 'living'), _resolve_chapter,
              (Param('option', 'Choice number', 1, 3),)),
    DevAction('story_rewind', 'Rewind story', 'story', ('character', 'living'), _rewind,
              (Param('chapter', 'Chapter index (0 = I Mistwood ... 5 = VI Thunder)', 0, 5),)),
    DevAction('story_set_trust', 'Set NPC trust', 'story', ('character', 'living'), _npc_trust,
              (Param('npc', 'NPC', choices=NPC_KEYS), Param('trust', 'Trust', 0, 12))),
    DevAction('story_clear_memories', 'Clear NPC memories', 'story', ('character', 'living'), _clear_memories,
              (Param('npc', 'NPC', choices=NPC_KEYS),)),
    DevAction('story_set_form', 'Set personal form', 'story', ('character', 'living'), _set_form,
              (Param('style', 'Form style', choices=('guard', 'flow', 'strike')),)),
    DevAction('set_form_practice', 'Set form preparation', 'story', ('character',), _form_practice,
              (Param('count', 'Practices', 0, 9),)),
    DevAction('commission_ready', 'Own sect commission ready', 'sect', ('character', 'hall'), _commission_ready),
    DevAction('set_sect_merit', 'Set own sect merit', 'sect', ('character', 'hall'), _set_sect_merit,
              (Param('merit', 'Sect merit', 0, 500),)),
    DevAction('complete_goals', 'Complete daily or weekly goals (unclaimed)', 'progress', ('character', 'living'), _complete_goals,
              (Param('scope', 'Goals', choices=('daily', 'weekly')),)),
    DevAction('set_abode_level', 'Set an abode room level', 'progress', ('character', 'living'), _set_abode_level,
              (Param('room', 'Room', choices=('garden', 'training', 'hall')), Param('level', 'Level', 0, 10))),
    DevAction('set_omen_share', 'Set own Heavenly Omen share', 'progress', ('character', 'living'), _set_omen_share,
              (Param('count', 'Explorations counted', 0, 15),)),
    DevAction('reset_omen_claims', 'Reset own Heavenly Omen claims', 'progress', ('character', 'living'), _reset_omen_claims),
):
    register(_action)


def _json(raw):
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def _observe(kind, developer, guild, outcome, reason=None):
    try:
        import security_observer_v025 as observer
        observer.record(kind, actor=developer, guild=guild, operation='command', outcome=outcome, reason=reason)
    except Exception:
        pass


async def run(repo, guild_id, developer_id, event_id, key, target_id, raw_params=None):
    """One receipted transaction. Duplicate deliveries return the first reply."""
    settings = _ACTIVE
    if not authorized(guild_id, developer_id):
        _observe('unauthorized_command', developer_id if type(developer_id) is int else None,
                 guild_id if type(guild_id) is int else None, 'denied', 'not_owner')
        raise ConsoleRejected(UNAVAILABLE)
    if type(event_id) is not int or not 0 < event_id < 2**63:
        raise ConsoleRejected('Open the developer console again.')
    action = REGISTRY.get(key) if type(key) is str else None
    if action is None:
        raise ConsoleRejected('Unknown developer action.')
    if not settings.may_target(target_id):
        raise ConsoleRejected('Target yourself or an allowlisted test account only.')
    raw_params = {} if raw_params is None else raw_params
    if not isinstance(raw_params, dict) or set(raw_params) - {p.name for p in action.params}:
        raise ConsoleRejected('Unexpected developer action fields.')
    params = {p.name: p.parse(raw_params.get(p.name)) for p in action.params}
    now = datetime.now(timezone.utc)
    async with repo.pool.acquire() as con:
        async with con.transaction():
            await _verify(con, settings, ConsoleRejected(UNAVAILABLE))
            rows = {}
            if 'combat' in action.rows:
                raw = await con.fetchval('SELECT battle FROM combat_sessions WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',
                                         guild_id, target_id)
                rows['combat'] = _json(raw) if raw is not None else None
            raw = await con.fetchval('SELECT state FROM characters WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',
                                     guild_id, target_id)
            if raw is None:
                raise ConsoleRejected('That account has no character. Use /start with it first.')
            rows['character'] = _json(raw)
            if 'living' in action.rows:
                from living_dao import decode
                raw = await con.fetchval('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2 FOR UPDATE',
                                         guild_id, target_id)
                rows['living'] = decode(raw) if raw is not None else None
            if 'inventory' in action.rows:
                from inventory import _locked
                rows['inventory'] = await _locked(con, guild_id, target_id)
            if 'hall' in action.rows:
                from living_dao import decode
                sect = decode(await con.fetchval('SELECT data FROM living_dao_players WHERE guild_id=$1 AND user_id=$2',
                                                 guild_id, target_id)).get('sect')
                raw = (await con.fetchval('SELECT data FROM sect_hall_state WHERE guild_id=$1 AND sect_name=$2 FOR UPDATE',
                                          guild_id, sect)) if sect else None
                if raw is None:
                    raise ConsoleRejected('That account is not in a sect with a hall yet.')
                rows['hall'] = _json(raw)
            prior = await con.fetchrow('SELECT developer_id, target_id, action, response FROM dev_console_actions '
                                       'WHERE guild_id=$1 AND event_id=$2', guild_id, event_id)
            if prior is not None:
                if prior['developer_id'] != developer_id:
                    raise ConsoleRejected(UNAVAILABLE)
                # A receipt replays only the exact same request; raising rolls back the locks' side effects.
                if prior['target_id'] != target_id or prior['action'] != action.key:
                    raise ConsoleRejected('Open the developer console again.')
                return prior['response']
            before = deepcopy(rows)
            reply = action.apply(rows, params, now, target_id)
            mark = rows['character'].get('dev_console_v025')
            mark = dict(mark) if isinstance(mark, dict) else {}
            mark.setdefault('first_at', now.isoformat())
            mark.update(tainted=True, actions=min(10**6, max(0, _int(mark.get('actions'))) + 1),
                        last_action=action.key, last_at=now.isoformat())
            rows['character']['dev_console_v025'] = mark
            if 'combat' in rows and rows['combat'] is not None and rows['combat'] != before['combat']:
                await con.execute('UPDATE combat_sessions SET battle=$3::jsonb,updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',
                                  guild_id, target_id, json.dumps(rows['combat']))
            await con.execute('UPDATE characters SET state=$3::jsonb, updated_at=NOW() WHERE guild_id=$1 AND user_id=$2',
                              guild_id, target_id, json.dumps(rows['character']))
            if 'living' in rows and rows['living'] is not None and rows['living'] != before['living']:
                await con.execute('UPDATE living_dao_players SET data=$3::jsonb WHERE guild_id=$1 AND user_id=$2',
                                  guild_id, target_id, json.dumps(rows['living']))
            if 'inventory' in rows and rows['inventory'] != before['inventory']:
                from inventory import _save
                await _save(con, guild_id, target_id, rows['inventory'])
            if 'hall' in rows and rows['hall'] != before['hall']:
                await con.execute('UPDATE sect_hall_state SET data=$3::jsonb WHERE guild_id=$1 AND sect_name=$2',
                                  guild_id, sect, json.dumps(rows['hall']))
            reply = f'[Developer · {action.label}] ' + reply
            await con.execute('INSERT INTO dev_console_actions (guild_id,event_id,developer_id,target_id,action,params,response) '
                              'VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7)', guild_id, event_id, developer_id, target_id,
                              action.key, json.dumps(params, sort_keys=True), reply)
    _observe('command_completed', developer_id, guild_id, 'success')
    return reply


async def recent(repo, guild_id, developer_id, limit=5):
    """Audit view for verifying what the console changed; read-only."""
    if not authorized(guild_id, developer_id):
        raise ConsoleRejected(UNAVAILABLE)
    async with repo.pool.acquire() as con:
        records = await con.fetch('SELECT event_id, developer_id, target_id, action, params, created_at FROM dev_console_actions '
                                  'WHERE guild_id=$1 ORDER BY created_at DESC, event_id DESC LIMIT $2', guild_id, max(1, min(10, limit)))
    if not records:
        return 'No developer actions recorded yet.'
    lines = ['**Recent developer actions**']
    for record in records:
        params = _json(record['params'])
        detail = ', '.join(f'{name}={value}' for name, value in sorted(params.items())) or 'no parameters'
        lines.append(f'{record["created_at"]:%Y-%m-%d %H:%M} UTC · <@{record["developer_id"]}> → <@{record["target_id"]}> · '
                     f'{record["action"]} ({detail})')
    return '\n'.join(lines)[:1900]
