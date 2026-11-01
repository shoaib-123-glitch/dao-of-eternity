"""Shattered Heavens dashboard and owner-bound controls; no bot/config startup."""
from io import BytesIO
from math import cos, sin, pi

import discord
from PIL import Image, ImageDraw

from dashboard_v020 import font
from eternal import rate_now
from game import unpack_player
from heavens import boost_summary, relevant_root
from rapid import foundation_capacity


COLORS = {'Fire': '#ef996d', 'Water': '#72bde7', 'Wood': '#80c99a', 'Earth': '#d1b17a',
          'Wind': '#a3e5d9', 'Metal': '#d2dbea', 'Lightning': '#c3a0f0', 'Ice': '#a0dce6'}
GOLD = '#e7c98c'
WHITE = '#edf2f0'
MUTED = '#a8b7bf'


def dashboard_model(state, root, now, guild_id=0, companion='No bonded beast yet. Use /adopt.'):
    from shattered_heavens_v023 import cultivation_summary
    p = unpack_player(state['player'])
    if state.get('golden_core'):
        from golden_core_v022 import capacity
        realm = f'Golden Core · Stage {state.get("golden_stage", 1)}'
        current, cap = state.get('golden_qi', 0), capacity(state.get('golden_stage', 1))
    elif p.advanced_to_foundation:
        realm = f'Foundation Establishment · Stage {state.get("foundation_stage", 1)}'
        current, cap = state.get('foundation_qi', 0), foundation_capacity(state.get('foundation_stage', 1))
    else:
        realm, current, cap = f'Qi Refining · Stage {p.stage}', p.qi, p.capacity
    base, factor, rate = rate_now(state, now, guild_id, root)
    info = state.get('eternal') or {}
    fraction = max(0, min(1, current / max(1, cap)))
    eta = 'Ready — advance manually' if current >= cap else f'~{max(0, cap-current)/max(rate, .001):.1f}h at current rate'
    identity = f'{root or "Unawakened"} Root'
    if state.get('secondary_root'):
        identity += f' + {state["secondary_root"]}'
    identity += ' · ' + str(state.get('cultivation_path') or 'Choose a path with /choose_path')
    from onboarding_v024 import breakthrough_command
    influences=boost_summary(state,guild_id,root,now)
    timed=[s for s in influences if 'until ' in s]
    return dict(realm=realm, identity=identity, current=current, cap=cap, fraction=fraction,
        rate=rate, base=base, factor=factor, eta=eta, bank=info.get('bank_qi', 0),
        earned=info.get('total_earned', 0), influences=influences,
        journey=cultivation_summary(state, root, now), companion=companion,
        hall_level=state.get('sect_qi_level', 0), accent=COLORS.get(relevant_root(root), '#91d7bd'),
        advance=breakthrough_command(state), timed_boosts=timed)


def dashboard_embed(model, *, detailed=False):
    escape = discord.utils.escape_markdown
    embed = discord.Embed(title='The Living Dao · Cultivator’s sanctuary',
        description=f'**{model["realm"]}**\n{escape(model["identity"])}\nPermanent cultivation · online and offline · no expiry',
        color=int(model['accent'][1:], 16))
    embed.add_field(name='DANTIAN / Spiritual Qi', value=(
        f'**{model["current"]:,} / {model["cap"]:,} Qi** · {model["fraction"]:.0%}\n'
        + (f'{max(0,model["cap"]-model["current"]):,} Qi to the next stage' if model['current']<model['cap']
           else 'Dantian full. Your next step is a manual breakthrough.')), inline=False)
    flow=f'**{model["rate"]:g} Qi / hour** · {model["eta"]}\nManual: {model.get("advance","/breakthrough")}'
    embed.add_field(name='Circulation', value=flow, inline=False)
    embed.add_field(name='Eternal reserve', value=f'**{model["bank"]:,} Qi** banked\nLifetime passive: {model["earned"]:,}', inline=False)
    journey=model['journey']
    training=journey if detailed else journey[:2]+[s for s in journey[2:] if 'ready' in s]
    embed.add_field(name='Your cultivation', value='\n'.join(training)[:1024] or '/cultivation_practice', inline=False)
    if detailed:
        embed.add_field(name='Heavenly influences', value='\n'.join('• ' + s for s in model['influences'])[:1024], inline=False)
        companion=str(model['companion'])
    else:
        companion='\n'.join(str(model['companion']).splitlines()[:2])
        if 'Spirit Beast Stage' in companion:companion+='\n18 Beast Qi/hour · /companion'
    embed.add_field(name='Alongside you', value=escape(companion)[:1024], inline=False)
    today=f'/world_event · /sect_domain (Hall Lv{model["hall_level"]})\n/sect_commission · /sect_project'
    embed.add_field(name='Today', value=today, inline=False)
    if not detailed and model.get('timed_boosts'):
        embed.description+='\n**Temporary boost active** · Details shows expiry.'
    embed.set_footer(text='v0.24 · /guide opens your trail · Earned growth, no paid Qi or streak penalties')
    return embed


def _compact(number):
    for scale, suffix in ((10**12, 'T'), (10**9, 'B'), (10**6, 'M'), (10**3, 'K')):
        if abs(number) >= scale:
            return f'{number/scale:.1f}{suffix}'
    return f'{number:,}'


def render_dashboard(model):
    """Procedural UI artwork; exact values and all details remain in the embed."""
    im = Image.new('RGB', (1200, 800), '#0b111d')
    draw = ImageDraw.Draw(im)
    accent = model['accent']
    # Broken celestial meridians: an original constellation behind the panels.
    points = [(54, 110), (190, 66), (324, 104), (470, 46), (688, 102), (897, 57), (1132, 104)]
    draw.line(points, fill='#26334c', width=2)
    for x, y in points:
        draw.ellipse((x-3, y-3, x+3, y+3), fill=GOLD)
    draw.rounded_rectangle((24, 24, 1176, 776), radius=25, outline='#43506b', width=2)
    def text(x, y, value, size=20, color=WHITE, width=1050, bold=False):
        value = str(value)
        face = font(size, bold)
        if draw.textbbox((0, 0), value, font=face)[2] > width:
            left, right = 0, len(value)
            while left < right:
                middle = (left + right + 1) // 2
                if draw.textbbox((0, 0), value[:middle] + '…', font=face)[2] <= width:
                    left = middle
                else:
                    right = middle - 1
            value = value[:left] + '…'
        draw.text((x, y), value, font=face, fill=color)
    text(62, 58, 'DAO OF ETERNITY', 15, GOLD, bold=True)
    text(60, 88, 'THE SHATTERED HEAVENS', 38, WHITE, bold=True)
    text(62, 145, model['realm'], 24, accent, bold=True)
    text(62, 183, model['identity'], 17, MUTED)
    draw.line((62, 221, 1136, 221), fill='#33405a', width=2)
    cx, cy, r = 238, 415, 128
    for radius in (r+18, r+32):
        draw.arc((cx-radius, cy-radius, cx+radius, cy+radius), 15, 310, fill='#29364d', width=2)
    for index in range(12):
        a = index*pi/6
        x, y = cx+157*cos(a), cy+157*sin(a)
        draw.ellipse((x-2, y-2, x+2, y+2), fill=GOLD)
    draw.ellipse((cx-r, cy-r, cx+r, cy+r), outline='#27354b', width=15)
    draw.arc((cx-r, cy-r, cx+r, cy+r), -90, -90+360*model['fraction'], fill=accent, width=15)
    draw.text((cx, cy-42), 'DANTIAN', font=font(17, True), anchor='mm', fill=MUTED)
    draw.text((cx, cy), _compact(model['current']), font=font(43, True), anchor='mm', fill=WHITE)
    draw.text((cx, cy+46), f'/ {_compact(model["cap"])} QI', font=font(18), anchor='mm', fill=GOLD)
    text(100, 596, 'ETERNAL CULTIVATION', 16, accent, width=300, bold=True)
    text(104, 626, 'Never expires. Every hour counts.', 15, MUTED, width=320)
    def tile(x, y, label, value, note):
        draw.rounded_rectangle((x, y, x+304, y+133), radius=14, fill='#151f32', outline='#35425b')
        text(x+18, y+16, label, 13, MUTED, width=269, bold=True)
        text(x+18, y+46, value, 27, GOLD, width=269, bold=True)
        text(x+18, y+95, note, 13, accent, width=269)
    tile(478, 260, 'QI CIRCULATION', f'{model["rate"]:g} / hour', f'Base {model["base"]} · {model["factor"]/1000:g}×')
    tile(805, 260, 'NEXT BREAKTHROUGH', model['eta'], 'Breakthroughs remain manual')
    tile(478, 414, 'ETERNAL RESERVE', _compact(model['bank']), 'Overflow is kept, never discarded')
    tile(805, 414, 'LIFETIME PASSIVE QI', _compact(model['earned']), 'Earned through cultivation')
    text(478, 579, 'YOUR PATH THROUGH THE HEAVENS', 15, accent, width=630, bold=True)
    for index, line in enumerate(model['journey'][:3]):
        text(478, 612+index*27, line, 15, WHITE if index == 0 else MUTED, width=630)
    draw.line((62, 716, 1136, 716), fill='#33405a')
    text(62, 736, 'CULTIVATE  /  EXPLORE  /  GROW TOGETHER', 14, GOLD, width=750, bold=True)
    text(946, 736, 'v0.23 · F2P', 14, accent, width=180)
    out = BytesIO()
    im.save(out, format='PNG', optimize=True)
    out.seek(0)
    return out


class CultivationView(discord.ui.View):
    def __init__(self, owner_id, *, refresh, practice, journal, guide=None, hunt=None, companion=None, details=None):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.callbacks = {'refresh': refresh, 'practice': practice, 'journal': journal,
                          'guide':guide, 'hunt':hunt, 'companion':companion, 'details':details}
        self.detailed=False
        for button,key in ((self.open_guide,'guide'),(self.first_hunt,'hunt'),(self.visit_companion,'companion'),(self.show_details,'details')):
            if self.callbacks[key] is None:self.remove_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('Open your own cultivation dashboard with /qi.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Refresh Qi', style=discord.ButtonStyle.secondary)
    async def refresh_qi(self, interaction, button):
        await self.callbacks['refresh'](interaction)

    @discord.ui.button(label='Cultivate', style=discord.ButtonStyle.success)
    async def practice_qi(self, interaction, button):
        await self.callbacks['practice'](interaction)

    @discord.ui.button(label='Journey', style=discord.ButtonStyle.secondary)
    async def show_journal(self, interaction, button):
        await self.callbacks['journal'](interaction)

    @discord.ui.button(label='Guide', style=discord.ButtonStyle.secondary, row=1)
    async def open_guide(self, interaction, button):
        await self.callbacks['guide'](interaction)

    @discord.ui.button(label='Hunt · Assisted', style=discord.ButtonStyle.primary, row=1)
    async def first_hunt(self, interaction, button):
        await self.callbacks['hunt'](interaction)

    @discord.ui.button(label='Companion', style=discord.ButtonStyle.secondary, row=1)
    async def visit_companion(self, interaction, button):
        await self.callbacks['companion'](interaction)

    @discord.ui.button(label='Details', style=discord.ButtonStyle.secondary)
    async def show_details(self, interaction, button):
        self.detailed=not self.detailed
        button.label='Compact' if self.detailed else 'Details'
        await self.callbacks['details'](interaction,self.detailed)


class TrailView(discord.ui.View):
    """Shortcuts dispatch existing protected commands; they award nothing themselves."""
    def __init__(self, owner_id, callbacks, *, row=None):
        super().__init__(timeout=180)
        self.owner_id=owner_id
        for label,callback in callbacks.items():
            button=discord.ui.Button(label=label,style=discord.ButtonStyle.secondary,row=row)
            button.callback=callback
            self.add_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('Open your own trail with /guide.',ephemeral=True)
            return False
        return True


class GuideView(TrailView):
    """Topic pages are built once; changing topics needs no database or image work."""
    def __init__(self, owner_id, pages, callbacks):
        super().__init__(owner_id,callbacks,row=1)
        from onboarding_v024 import SECTIONS
        self.pages=pages
        menu=discord.ui.Select(placeholder='Choose a short guide',row=0,
            options=[discord.SelectOption(label=label,value=key) for key,label in SECTIONS.items()])
        menu.callback=self.choose_topic
        self.add_item(menu)
        self.menu=menu

    async def choose_topic(self, interaction):
        topic=self.menu.values[0]
        for option in self.menu.options:option.default=option.value==topic
        await interaction.response.edit_message(content=self.pages[topic],view=self,
            allowed_mentions=discord.AllowedMentions.none())
