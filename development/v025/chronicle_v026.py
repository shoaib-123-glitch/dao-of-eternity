"""Chronicle card: a short, shareable summary of a player's journey.

Pure: reads the Living Dao JSON only and returns plain text. Nothing here is
secret; the card shows story progress, bonds, titles, abode and companion, never
balances or Discord IDs. The story panel posts it publicly only when its owner
presses "Share Chronicle".
"""
import abode_v025 as abode
import arc2_v026 as arc2
import living_world_v025 as world


def titles(state):
    found = list(arc2._arc(state)['titles'])
    extra = state.get('titles_v026') if isinstance(state, dict) and isinstance(state.get('titles_v026'), list) else []
    for title in extra:
        if isinstance(title, str) and title not in found:
            found.append(title)
    return found[:12]


def _chapter_line(state):
    view = arc2.panel(state)
    if view['complete'] and view['chapter'] >= arc2.OFFSET + len(arc2.CHAPTERS):
        return 'Story: both arcs complete (6 of 6 chapters).'
    done = min(view['chapter'], arc2.OFFSET + len(arc2.CHAPTERS))
    return f'Story: {done} of {arc2.OFFSET + len(arc2.CHAPTERS)} chapters · now {view["title"]}.'


def card(state, name):
    """Returns (title, lines). `name` is the display name the player already shows in Discord."""
    from living_dao import COMPANIONS, REGIONS
    lines = [_chapter_line(state)]
    story = world._story(state)
    bonds = []
    for key, npc in world.NPCS.items():
        trust = story['npcs'].get(key, {}).get('trust', 0)
        bonds.append(f'{npc["name"].split()[0]} {trust}')
    lines.append('Bonds: ' + ' · '.join(bonds) + f' (of {world.MAX_TRUST}).')
    companion = state.get('companion') if isinstance(state, dict) else None
    if companion in COMPANIONS:
        trips = state.get('expedition_v026', {}).get('count', 0) if isinstance(state.get('expedition_v026'), dict) else 0
        lines.append(f'Companion: {COMPANIONS[companion][0]}, Bond Level {state.get("bond", 1)} · {trips if type(trips) is int else 0} companion trips.')
    total, rank = abode.rank(state)
    lines.append(f'Cave abode: {rank} (rank {total}).')
    explored = state.get('explorations') if type(state.get('explorations')) is int else 0
    region = state.get('region') if state.get('region') in REGIONS else None
    lines.append(f'Explorations: {explored}' + (f' · last seen in {world.region_name(region)}.' if region else '.'))
    earned = titles(state)
    lines.append('Titles: ' + (', '.join(earned) if earned else 'none yet') + '.')
    return f'Chronicle of {str(name)[:60]}', lines
