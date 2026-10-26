"""Owner-bound authored story controls. No configuration, database or AI API."""
from datetime import datetime, timezone
import discord
from living_world_v025 import panel, region_name, status, training_panel

TACTICS=('guard','strike','feint')


def _now(now):
    return now if isinstance(now,datetime) and now.tzinfo else datetime.now(timezone.utc)


def training_text(state,now=None):
    lesson=training_panel(state,_now(now))
    lines=[lesson['text']]
    if lesson['unlocked'] and not lesson['ready'] and lesson['next_at']:
        stamp=int(datetime.fromisoformat(lesson['next_at']).timestamp())
        lines.append(f'Next free lesson <t:{stamp}:R> (next UTC day).')
    if lesson['current_style']:
        lines.append('Personal form: '+lesson['current_style']+'.')
    return ' '.join(lines)[:1000]


def story_embed(state,now=None):
    embed=discord.Embed(title='The Living World · Echoes of the Fallen Heaven',
        description=status(state)[:4000],color=0x72C8B0)
    embed.add_field(name='Teachers',value=training_text(state,now),inline=False)
    embed.set_footer(text='v0.25 candidate · Your choices are saved · No expiry or paid paths')
    return embed


class OwnerView(discord.ui.View):
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('Open your own story from /guide.',ephemeral=True)
            return False
        return True


class StoryView(OwnerView):
    def __init__(self,owner_id,state,*,choose,refresh,explore,train=None,now=None):
        super().__init__(timeout=300)
        self.owner_id=owner_id
        self.chapter=panel(state)['chapter']
        self.callbacks=dict(choose=choose,refresh=refresh,explore=explore,train=train)
        current=panel(state)
        for option in current['choices']:
            button=discord.ui.Button(label=option['label'][:80],style=discord.ButtonStyle.primary,row=0)
            button.callback=self.choose_callback(option['key'])
            self.add_item(button)
        refresh_button=discord.ui.Button(label='Refresh story',style=discord.ButtonStyle.secondary,row=1)
        refresh_button.callback=refresh
        self.add_item(refresh_button)
        if not current['complete'] and not current['ready']:
            button=discord.ui.Button(label='Explore '+region_name(current['region']),
                style=discord.ButtonStyle.success,row=1)
            async def expedition(interaction):
                await explore(interaction,current['region'])
            button.callback=expedition
            self.add_item(button)
        self.lesson=training_panel(state,_now(now))
        if train is not None and self.lesson['unlocked']:
            for teacher in self.lesson['teachers']:
                button=discord.ui.Button(label=(teacher['name']+' · '+teacher['lesson'])[:80],
                    style=discord.ButtonStyle.primary if self.lesson['ready'] else discord.ButtonStyle.secondary,
                    disabled=not self.lesson['ready'],row=2)
                button.callback=self.duel_callback() if teacher['key']=='zhao' else self.train_callback(teacher['key'])
                self.add_item(button)

    def choose_callback(self,choice):
        async def callback(interaction):
            await self.callbacks['choose'](interaction,self.chapter,choice)
        return callback

    def train_callback(self,teacher):
        async def callback(interaction):
            await self.callbacks['train'](interaction,teacher,None)
        return callback

    def duel_callback(self):
        async def callback(interaction):
            await interaction.response.send_message(
                'Zhao: Three exchanges, no injuries. Watch how I move before each one, then choose your answer.',
                view=DuelView(self.owner_id,train=self.callbacks['train'],tells=self.lesson['tells']),ephemeral=True)
        return callback


class DuelView(OwnerView):
    """Three tactic selects plus one submit; nothing is spent until all three are chosen."""
    def __init__(self,owner_id,*,train,tells):
        super().__init__(timeout=300)
        self.owner_id=owner_id
        self.train=train
        self.tactics=[None,None,None]
        self.menus=[]
        for index,tell in enumerate(tells):
            menu=discord.ui.Select(placeholder=f'Exchange {index+1} · {tell}'[:150],row=index,
                options=[discord.SelectOption(label=move.title(),value=move) for move in TACTICS])
            menu.callback=self.pick(index,menu)
            self.add_item(menu)
            self.menus.append(menu)
        begin=discord.ui.Button(label='Begin duel',style=discord.ButtonStyle.success,row=3)
        begin.callback=self.begin
        self.add_item(begin)

    def pick(self,index,menu):
        async def callback(interaction):
            value=menu.values[0] if menu.values else None
            self.tactics[index]=value if value in TACTICS else None
            for option in menu.options:option.default=option.value==self.tactics[index]
            await interaction.response.defer()
        return callback

    def ready(self):
        return all(move in TACTICS for move in self.tactics)

    async def begin(self,interaction):
        if not self.ready():
            missing=', '.join(str(i+1) for i,move in enumerate(self.tactics) if move not in TACTICS)
            await interaction.response.send_message(
                f'Choose a tactic for every exchange first (missing: {missing}). Nothing was spent.',ephemeral=True)
            return
        await self.train(interaction,'zhao',list(self.tactics))
