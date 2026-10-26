"""Short, adaptive guide pages. Navigation never awards rewards."""

SECTIONS = {'trail':'Your next steps', 'cultivation':'Cultivation', 'combat':'Hunts and equipment',
            'companion':'Your companion', 'world':'Explore the world', 'sect':'Sect life'}


def breakthrough_command(state):
    if state.get('golden_core'):return '/golden_advance'
    if state.get('player',{}).get('advanced_to_foundation'):return '/foundation_advance'
    return '/breakthrough'

def next_step(state, root=None):
    if not root:
        return 'Awaken: /awaken — choose an elemental root and origin (permanent).'
    if not state.get('cultivation_path'):
        return 'Choose your path: /choose_path — Spirit/Alchemy favor Qi; Sword/Body/Formation favor combat.'
    if not state.get('cultivation_v023', {}).get('focus'):
        return 'Choose /cultivation_focus — Gather for Qi, Insight for mastery, Resonance for encounters.'
    return 'Practice with /cultivation_practice, or /hunt enemy:spirit_wolf mode:assisted. /guide shows every next step.'


def guide(state, root=None, living=None, stock=None, section='trail'):
    if section not in SECTIONS:raise ValueError('Choose a guide topic from the menu.')
    if state is None:
        return '**The Living Dao · First steps**\nUse /start to create your cultivator. Permanent passive Qi begins automatically.'
    living, stock = living or {}, stock or {}
    marks = max(0, int(stock.get('marks', 0)))
    adoption = ('/beast_bond activity:listen — free time with your companion.' if living.get('companion') else
        f'/adopt beast:jade_fox — 16 earned Spirit Stones; {max(0,16-marks)} more needed.')
    advance = breakthrough_command(state)
    lines = [f'**The Living Dao · {SECTIONS[section]}**']
    if section=='trail':
        lines += [next_step(state,root),
            f'\n**Home** /qi · /cultivation_focus · /journey\nAdvance manually when full: {advance}. Passive Qi never expires.',
            '**Adventure** /hunt enemy:spirit_wolf mode:assisted\n/recover is free; /recipes → /craft → /equip prepares tougher hunts.',
            '**Alongside you** '+adoption,
            '**World & sect** /world_event · /explore · /sect_commission · /sect_project',
            '\nChoose a topic below for a short walkthrough.']
    elif section=='cultivation':
        lines += [next_step(state,root),
            '/qi shows Qi, reserve and readiness. Details explains boosts; /journey explains root mastery.',
            '/cultivation_practice is free every 6 hours. Fire trains Ember Refinement; other roots have their own benefits.',
            f'Full Qi does not advance you automatically: use {advance}. Overflow stays in reserve.',
            'Change /cultivation_focus after its 6-hour commitment. Temporary boosts expire; permanent passive Qi does not.']
    elif section=='combat':
        blade=stock.get('holdings',{}).get('mistwood_blade',0)
        equipped=stock.get('equipped',{}).get('weapon')
        prep=('Your Mistwood Blade is ready — /equip item:mistwood_blade.' if blade and not equipped else
              'Weapon equipped. /inventory shows your current bonuses.' if equipped else
              '/craft item:mistwood_blade costs 4 Spirit Stones + 1 Wolf Fang; then /equip item:mistwood_blade.')
        lines += ['/hunt enemy:spirit_wolf mode:assisted is the beginner trail. Manual and Auto remain available.',
            'Thunder Palm (8 Combat Qi) → Basic Strike (+4 Qi) detonates Storm Charge. /techniques explains every move.',
            'Assisted pauses BEFORE heavy attacks. Guard (5 Qi) reduces the next blow; it does not prevent all damage.',
            'Second Wind heals up to 18 HP once in a fight; the enemy still attacks. Retreat earns nothing and keeps your progress.',
            '/recover is free at camp: up to 60% HP every 2 minutes. Natural recovery: 1 HP/5 seconds.',
            prep,
            'First v0.24 Wolf win: 12 Spirit Stones, later wins: 8. Two wins can fund a blade and a 16-Spirit-Stone Fox. Defeat/retreat give no Spirit Stones.',
            'Guardian: Stage 2+ and a weapon. Serpent: Stage 3+, weapon and robes. These are recommendations, not guaranteed victories.']
    elif section=='companion':
        lines += [adoption]
        if living.get('companion'):
            from companion_bonds_v024 import PROFILES
            temperament,favorite,_=PROFILES.get(living['companion'],('Gentle and watchful','meditate',''))
            info=living.get('companion_v024',{})
            lines += [f'{temperament}. Favorite activity: {favorite}. Trust {info.get("trust",0)}/20 · Bond {living.get("bond",1)}/5.',
                '/beast_bond is free every 6 hours: trust, 6 Beast Qi and 12 hours of battle attunement. /companion shows readiness and your last memory.']
        lines += ['/beast_cultivation grows permanently at 18 Beast Qi/hour. /beast_advance and /beast_evolve stay manual.',
            'Bond never decays. /bond is optional training with earned Spirit Stones; no feeding cost or required purchase.']
    elif section=='world':
        lines += ['/world_event names today’s shared chapter and featured region.',
            '/explore the featured region for +1 local material once per UTC day. A rare trail story can add +1 more material once per day.',
            'Expeditions share a 5-minute cooldown. Replaying an interaction never rolls a second reward.',
            '/quests → /quest_claim offers earned exploration goals. Rare encounters are optional; nothing requires a streak.']
    else:
        sect=living.get('sect')
        lines += [f'Your sect: {sect}.' if sect else '/sect lists communities. /sect_join joins one; /sect_create costs 20 earned Spirit Stones.',
            '/sect_domain shows treasury, halls, doctrine and renown.',
            '/sect_commission: patrol, archives or gardens. Each gives 6 Spirit Stones and 5 renown (+1 matching doctrine); 6-hour personal cooldown, 3 per UTC day.',
            '/sect_project needs TWO distinct players and all three activity types. Coordinate who does what before commissioning.',
            'Completion: +15 shared renown and a 2-Spirit-Stone voucher per contributor, once per UTC day. Claim with /sect_project; vouchers never expire.',
            'Commissions do not refill the treasury. /sect_contribute funds halls; /sect_raid_start offers an existing cooperative challenge.']
    return '\n'.join(lines)
