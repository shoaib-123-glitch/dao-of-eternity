"""Private-guild Discord app. Token and IDs only come from environment variables."""
import os
import logging
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from storage import Repository
from tribulation import CHOICES, PHASES
from awakening import ROOTS, BACKGROUNDS, profile_lines
from rapid import CHOICES as RAPID_CHOICES

load_dotenv()
TOKEN = os.environ.get('DISCORD_TOKEN')
GUILD_ID = int(os.environ.get('DISCORD_GUILD_ID', '0'))
DSN = os.environ.get('DATABASE_URL')
if not TOKEN or not DSN or not GUILD_ID:
    raise RuntimeError('Set DISCORD_TOKEN, DISCORD_GUILD_ID and DATABASE_URL in .env')
GUILD = discord.Object(id=GUILD_ID)
DEV_MODE = os.getenv('DEV_MODE', 'false').strip().lower() == 'true'
DEV_USER_ID = int(os.getenv('DEV_USER_ID', '0'))


class DaoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.repo = None

    async def setup_hook(self):
        self.repo = await Repository.connect(DSN)
        from combat_store import setup as setup_combat
        await setup_combat(self.repo)
        from living_dao import setup as setup_living_dao
        await setup_living_dao(self.repo)
        from sect_halls import setup as setup_sect_halls
        await setup_sect_halls(self.repo)
        from sect_v022 import setup as setup_sect_v022
        await setup_sect_v022(self.repo)
        from bonds_v022 import setup as setup_bonds_v022
        await setup_bonds_v022(self.repo)
        from golden_sandbox_v022 import setup as setup_golden_sandbox_v022
        await setup_golden_sandbox_v022(self.repo)
        self.tree.copy_global_to(guild=GUILD)
        synced = await self.tree.sync(guild=GUILD)
        logging.info('Synced %s guild-only commands',len(synced))
        if not qi_event_announcer.is_running(): qi_event_announcer.start()

    async def close(self):
        if qi_event_announcer.is_running(): qi_event_announcer.cancel()
        if self.repo:
            await self.repo.close()
        await super().close()

bot = DaoBot()

def authorized(interaction:discord.Interaction):
    return interaction.guild_id == GUILD_ID

async def run_op(interaction, op, **kwargs):
    if not authorized(interaction):
        await interaction.response.send_message('This game is private to its configured test server.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        now = datetime.now(timezone.utc)
        reply = await bot.repo.execute(interaction.guild_id, interaction.user.id, interaction.id,op,now,**kwargs)
        await interaction.followup.send(reply,ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('Unexpected game error')
        await interaction.followup.send('An internal error occurred. Your action was not confirmed; check /profile.',ephemeral=True)

async def run_dev(interaction: discord.Interaction, op: str, **kwargs):
    if not (authorized(interaction) and DEV_MODE and DEV_USER_ID > 0
            and interaction.user.id == DEV_USER_ID):
        await interaction.response.send_message('Developer command unavailable.', ephemeral=True)
        return
    await run_op(interaction, op, **kwargs)

def is_developer(interaction):
    return authorized(interaction) and DEV_MODE and DEV_USER_ID > 0 and interaction.user.id == DEV_USER_ID

@bot.tree.command(description='[DEV] Reset separate trial sandbox; real character unchanged')
async def dev_trial_reset(interaction: discord.Interaction):
    if not is_developer(interaction):
        await interaction.response.send_message('Developer command unavailable.',ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await bot.repo.sandbox_reset(interaction.guild_id,interaction.user.id,datetime.now(timezone.utc))
        await interaction.followup.send(reply,ephemeral=True)
    except Exception:
        logging.exception('Sandbox reset failure')
        await interaction.followup.send('Sandbox reset failed. Check bot terminal.',ephemeral=True)

@bot.tree.command(description='[DEV] Begin sandbox Foundation trial (no live progress changed)')
async def dev_trial_start(interaction:discord.Interaction):
    if not is_developer(interaction):
        await interaction.response.send_message('Developer command unavailable.',ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await bot.repo.sandbox_execute(interaction.guild_id,interaction.user.id,interaction.id,
            'trial_start',datetime.now(timezone.utc))
        await interaction.followup.send(reply,view=TrialView(interaction.user.id,0,sandbox=True),ephemeral=True)
    except ValueError as error:
        await interaction.followup.send(str(error),ephemeral=True)
    except Exception:
        logging.exception('Sandbox trial start failure')
        await interaction.followup.send('Sandbox trial start failed.',ephemeral=True)

@bot.tree.command(description='[DEV] Show or resume separate sandbox trial')
async def dev_trial_status(interaction:discord.Interaction):
    if not is_developer(interaction):
        await interaction.response.send_message('Developer command unavailable.',ephemeral=True)
        return
    state=await bot.repo.sandbox_get_state(interaction.guild_id,interaction.user.id)
    if state is None:
        await interaction.response.send_message('Run /dev_trial_reset first.',ephemeral=True)
        return
    reply=await bot.repo.sandbox_execute(interaction.guild_id,interaction.user.id,interaction.id,
        'profile',datetime.now(timezone.utc))
    view=TrialView(interaction.user.id,state['trial']['phase_index'],sandbox=True) if state['trial'] else None
    if view is None:
        await interaction.response.send_message('[SANDBOX]\n'+reply,ephemeral=True)
    else:
        await interaction.response.send_message('[SANDBOX]\n'+reply,view=view,ephemeral=True)

@bot.tree.command(description='[DEV] Add up to 600 test Qi (private owner only)')
@app_commands.describe(amount='Qi to add, 1 to 600; capped at your current stage maximum')
async def dev_addqi(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 600]):
    await run_dev(interaction, 'dev_addqi', amount=amount)

@bot.tree.command(description='[DEV] Set Qi Refining stage 1–9 (resets Qi)')
@app_commands.describe(stage='Qi Refining stage 1 to 9')
async def dev_setstage(interaction: discord.Interaction, stage: app_commands.Range[int, 1, 9]):
    await run_dev(interaction, 'dev_setstage', stage=stage)

@bot.tree.command(description='Create your private cultivation character')
async def start(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    reply = await bot.repo.start(interaction.guild_id,interaction.user.id,interaction.id,datetime.now(timezone.utc))
    await interaction.followup.send(reply,ephemeral=True)


@bot.tree.command(description='Awaken a permanent spiritual root and choose a character origin')
@app_commands.choices(root=[app_commands.Choice(name=k, value=k) for k in ROOTS],
                      background=[app_commands.Choice(name=k, value=k) for k in BACKGROUNDS])
async def awaken(interaction: discord.Interaction, root: str, background: str):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        reply = await bot.repo.awaken(interaction.guild_id, interaction.user.id, root, background)
        await interaction.followup.send(reply, ephemeral=True)
    except Exception:
        logging.exception('Awakening error')
        await interaction.followup.send('Awakening failed; check bot terminal.', ephemeral=True)

@bot.tree.command(description='Show your spiritual roots and cultivation in an RPG character card')
async def character(interaction: discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    state = await bot.repo.get_state(interaction.guild_id, interaction.user.id)
    if not state:
        await interaction.response.send_message('Use /start first.', ephemeral=True)
        return
    origin = await bot.repo.get_origin(interaction.guild_id, interaction.user.id)
    lines = profile_lines(state, origin)
    embed = discord.Embed(title='✦ Dao of Eternity · Cultivator', description=lines['heading'], color=0x5478D4)
    embed.add_field(name='Cultivation', value=lines['progress'], inline=False)
    embed.add_field(name='Spiritual Identity', value=lines['origin'], inline=False)
    embed.add_field(name='Condition', value=lines['condition'], inline=False)
    embed.set_footer(text='Character art and Dao technique bonuses coming in later releases')
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(description='See permanent passive Qi earnings and banked overflow')
async def cultivation_status(interaction:discord.Interaction):
    await run_op(interaction, 'cultivation_status')

@bot.tree.command(description='Show your cultivation progress')
async def profile(interaction:discord.Interaction): await run_op(interaction,'profile')
class CultivationView(discord.ui.View):
    def __init__(self, owner: int):
        super().__init__(timeout=900)
        self.owner = owner
        for key, (title, detail) in RAPID_CHOICES.items():
            button = discord.ui.Button(label=title, style=discord.ButtonStyle.primary)
            button.callback = self.make_callback(key)
            self.add_item(button)

    def make_callback(self, choice: str):
        async def callback(interaction: discord.Interaction):
            if not authorized(interaction) or interaction.user.id != self.owner:
                await interaction.response.send_message('Only the cultivator can choose.', ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                reply = await bot.repo.execute(
                    interaction.guild_id, interaction.user.id, interaction.id,
                    ('foundation_cultivate' if (await bot.repo.get_state(interaction.guild_id, interaction.user.id))['player']['advanced_to_foundation'] else 'rapid_cultivate'), datetime.now(timezone.utc), choice=choice
                )
                await interaction.followup.send(reply, view=CultivationView(self.owner), ephemeral=True)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
            except Exception:
                logging.exception('Quick cultivation failure')
                await interaction.followup.send('Cultivation could not be confirmed. Check /profile.', ephemeral=True)
        return callback

@bot.tree.command(description='Choose quick Qi circulation, Dao study, or purification')
async def cultivate(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    await interaction.response.send_message(
        'Choose cultivation: Circulate Qi (+Stability), Study the Dao (+Insight), or Purify Qi (-Impurity). '
        'Qi rewards share a short stage-based cooldown.',
        view=CultivationView(interaction.user.id), ephemeral=True
    )

class EncounterView(discord.ui.View):
    def __init__(self, owner: int, key: str):
        super().__init__(timeout=900)
        from encounters import ENCOUNTERS
        self.owner = owner
        for option, (label, _) in ENCOUNTERS[key][2].items():
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            button.callback = self.callback_for(option)
            self.add_item(button)

    def callback_for(self, option: str):
        async def callback(interaction: discord.Interaction):
            if not authorized(interaction) or interaction.user.id != self.owner:
                await interaction.response.send_message('Only the encounter owner can respond.', ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                response=await bot.repo.execute(interaction.guild_id, interaction.user.id, interaction.id,
                    'encounter_choice', datetime.now(timezone.utc), choice=option)
                # Never send view=None; the event is finished after a valid choice.
                await interaction.followup.send(response, ephemeral=True)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
            except Exception:
                logging.exception('Encounter action failure')
                await interaction.followup.send('Encounter action could not be confirmed. Check /profile.', ephemeral=True)
        return callback

@bot.tree.command(description='Discover a cultivation encounter with meaningful choices')
async def encounter(interaction: discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        reply=await bot.repo.execute(interaction.guild_id, interaction.user.id, interaction.id,
            'encounter_start', datetime.now(timezone.utc))
        state=await bot.repo.get_state(interaction.guild_id, interaction.user.id)
        if state and state.get('encounter'):
            view=EncounterView(interaction.user.id, state['encounter']['key'])
            await interaction.followup.send(reply, view=view, ephemeral=True)
        else:
            await interaction.followup.send(reply, ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
    except Exception:
        logging.exception('Encounter start failure')
        await interaction.followup.send('Could not start encounter. Check bot terminal.', ephemeral=True)

@bot.tree.command(description='Resume your active cultivation encounter')
async def encounter_resume(interaction: discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    from datetime import datetime as dt
    state=await bot.repo.get_state(interaction.guild_id, interaction.user.id)
    event=state.get('encounter') if state else None
    if not event or datetime.now(timezone.utc)>=dt.fromisoformat(event['expires_at']):
        await interaction.response.send_message('No active encounter. Use /encounter when ready.', ephemeral=True)
        return
    from encounters import ENCOUNTERS
    key=event['key']
    await interaction.response.send_message(f'{ENCOUNTERS[key][0]}: {ENCOUNTERS[key][1]}',
        view=EncounterView(interaction.user.id,key), ephemeral=True)

@bot.tree.command(description='Advance a completed Foundation Establishment stage')
async def foundation_advance(interaction: discord.Interaction):
    await run_op(interaction, 'foundation_advance')

class FastSandboxView(discord.ui.View):
    def __init__(self, owner: int):
        super().__init__(timeout=900)
        for key, (title, _) in RAPID_CHOICES.items():
            button = discord.ui.Button(label=title, style=discord.ButtonStyle.primary)
            async def cb(interaction: discord.Interaction, choice=key):
                if not is_developer(interaction) or interaction.user.id != owner:
                    await interaction.response.send_message('Developer only.', ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True, thinking=True)
                try:
                    reply = await bot.repo.fast_execute(interaction.guild_id, interaction.user.id,
                        interaction.id, 'rapid_cultivate', datetime.now(timezone.utc), choice=choice)
                    await interaction.followup.send(reply, ephemeral=True)
                except ValueError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                except Exception:
                    logging.exception('Fast sandbox click failure')
                    await interaction.followup.send('Check the bot terminal.', ephemeral=True)
            button.callback = cb
            self.add_item(button)

@bot.tree.command(description='[DEV] Reset separate Qi Refining speed sandbox')
async def dev_fast_reset(interaction: discord.Interaction):
    if not is_developer(interaction):
        await interaction.response.send_message('Developer only.', ephemeral=True)
        return
    reply = await bot.repo.fast_reset(interaction.guild_id, interaction.user.id, datetime.now(timezone.utc))
    await interaction.response.send_message(reply, ephemeral=True)

@bot.tree.command(description='[DEV] Cultivate in isolated fast sandbox')
async def dev_fast_cultivate(interaction: discord.Interaction):
    if not is_developer(interaction):
        await interaction.response.send_message('Developer only.', ephemeral=True)
        return
    await interaction.response.send_message('Select a sandbox cultivation action.',
        view=FastSandboxView(interaction.user.id), ephemeral=True)

@bot.tree.command(description='[DEV] Advance the fast sandbox cultivation stage')
async def dev_fast_advance(interaction: discord.Interaction):
    if not is_developer(interaction):
        await interaction.response.send_message('Developer only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        reply = await bot.repo.fast_execute(interaction.guild_id, interaction.user.id,
            interaction.id, 'minor', datetime.now(timezone.utc))
        await interaction.followup.send(reply, ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)

@bot.tree.command(description='[DEV] Show isolated fast sandbox profile')
async def dev_fast_status(interaction: discord.Interaction):
    if not is_developer(interaction):
        await interaction.response.send_message('Developer only.', ephemeral=True)
        return
    state = await bot.repo.fast_get_state(interaction.guild_id, interaction.user.id)
    if not state:
        await interaction.response.send_message('Run /dev_fast_reset first.', ephemeral=True)
        return
    from game import format_profile, unpack_player
    await interaction.response.send_message('[FAST SANDBOX]\n' + format_profile(
        unpack_player(state['player']), state, datetime.now(timezone.utc)), ephemeral=True)

@bot.tree.command(description='Start the original 15-minute meditation (legacy mode)')
async def legacy_meditate(interaction:discord.Interaction):
    await run_op(interaction, 'start_meditation')
@bot.tree.command(description='Claim completed meditation Qi')
async def claim(interaction:discord.Interaction): await run_op(interaction,'claim_meditation')
@bot.tree.command(description='Complete a daily cultivation training task')
async def train(interaction:discord.Interaction): await run_op(interaction,'training')
@bot.tree.command(description='Claim your capped offline cultivation Qi')
async def offline(interaction:discord.Interaction): await run_op(interaction,'offline')
@bot.tree.command(description='Improve foundation stability')
async def stabilize(interaction:discord.Interaction): await run_op(interaction,'stabilize')
@bot.tree.command(description='Heal damaged meridians')
async def heal(interaction:discord.Interaction): await run_op(interaction,'heal')
@bot.tree.command(description='Purify impurities')
async def purify(interaction:discord.Interaction): await run_op(interaction,'purify')
@bot.tree.command(description='Open the next meridian with training seals')
async def meridian(interaction:discord.Interaction): await run_op(interaction,'meridian')
@bot.tree.command(description='Refine foundation using earned Essences')
async def refine(interaction:discord.Interaction): await run_op(interaction,'refine')

class TrialView(discord.ui.View):
    def __init__(self, owner:int, phase_index:int, sandbox:bool=False):
        super().__init__(timeout=1800)
        self.owner=owner
        self.phase_index=phase_index
        self.sandbox=sandbox
        phase=PHASES[phase_index]
        for key, data in CHOICES[phase].items():
            b=discord.ui.Button(label=data[0][:80], style=discord.ButtonStyle.primary,
                                custom_id=f'dao:{"test" if sandbox else "live"}:{owner}:{phase_index}:{key}')
            b.callback=self.callback_for(key)
            self.add_item(b)

    def callback_for(self,choice):
        async def cb(interaction:discord.Interaction):
            if interaction.user.id != self.owner:
                await interaction.response.send_message('Only the trial owner can choose.',ephemeral=True)
                return
            if not authorized(interaction) or (self.sandbox and not is_developer(interaction)):
                await interaction.response.send_message('Private test server only.',ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True,thinking=True)
            try:
                executor=bot.repo.sandbox_execute if self.sandbox else bot.repo.execute
                reader=bot.repo.sandbox_get_state if self.sandbox else bot.repo.get_state
                reply=await executor(interaction.guild_id,interaction.user.id,interaction.id,
                                             'trial_choice',datetime.now(timezone.utc),
                                             choice=choice,phase_index=self.phase_index)
                state=await reader(interaction.guild_id,interaction.user.id)
                if state and state['trial']:
                    view=TrialView(self.owner,state['trial']['phase_index'],sandbox=self.sandbox)
                else:
                    view=None
                if view is None:
                    await interaction.followup.send(reply, ephemeral=True)
                else:
                    await interaction.followup.send(reply, view=view, ephemeral=True)
            except ValueError as exc:
                await interaction.followup.send(str(exc),ephemeral=True)
            except Exception:
                logging.exception('Trial click failure')
                await interaction.followup.send('Trial action failed; use /trial_resume.',ephemeral=True)
        return cb

@bot.tree.command(description='Attempt Foundation Establishment at Qi Refining Stage 9')
async def breakthrough(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.',ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await bot.repo.execute(interaction.guild_id,interaction.user.id,interaction.id,
                                     'trial_start',datetime.now(timezone.utc))
        await interaction.followup.send(reply,view=TrialView(interaction.user.id,0),ephemeral=True)
    except ValueError as exc:
        # If not ready for major breakthrough, allow ordinary stage progression.
        state=await bot.repo.get_state(interaction.guild_id,interaction.user.id)
        if state and not state['trial'] and state['player']['stage']<9:
            try:
                reply=await bot.repo.execute(interaction.guild_id,interaction.user.id,interaction.id,
                                             'minor',datetime.now(timezone.utc))
                await interaction.followup.send(reply,ephemeral=True)
                return
            except ValueError as minor_exc:
                await interaction.followup.send(str(minor_exc),ephemeral=True)
                return
        await interaction.followup.send(str(exc),ephemeral=True)

@bot.tree.command(description='Resume the active breakthrough trial after reopening Discord')
async def trial_resume(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.',ephemeral=True)
        return
    state=await bot.repo.get_state(interaction.guild_id,interaction.user.id)
    if not state or not state['trial']:
        await interaction.response.send_message('No active trial.',ephemeral=True)
        return
    await interaction.response.send_message('Your active trial:',
                                            view=TrialView(interaction.user.id,state['trial']['phase_index']),ephemeral=True)

@bot.tree.command(description='Abandon a trial without spending Qi')
async def trial_abandon(interaction:discord.Interaction): await run_op(interaction,'trial_abandon')

# v0.14: one engine for manual and automatic combat, saved independently.
from combat import ENEMIES, ACTIONS, display as battle_display, unlocked, mastery_tier
from combat_store import execute as combat_execute, current as combat_current

class BattleView(discord.ui.View):
    def __init__(self, owner, battle=None):
        super().__init__(timeout=600)
        self.owner=owner
        for key,label in ACTIONS.items():
            if key=='beast_skill' and (not battle or not battle.get('beast')):continue
            if key=='secondary_art' and (not battle or not battle.get('secondary_root')):
                continue
            if key=='thunder_judgment' and (not battle or not unlocked(battle,key)):
                continue
            button=discord.ui.Button(label=label,style=discord.ButtonStyle.primary)
            button.callback=self.callback_for(key)
            self.add_item(button)
        button=discord.ui.Button(label='Auto Finish',style=discord.ButtonStyle.secondary)
        button.callback=self.callback_for(None)
        self.add_item(button)
        if battle and battle.get('enemy')=='thunder_dragon':
            assist_button=discord.ui.Button(label='Assist Until Danger',style=discord.ButtonStyle.secondary)
            assist_button.callback=self.callback_for('assist_mode')
            self.add_item(assist_button)

    def callback_for(self,action):
        async def callback(interaction):
            if not authorized(interaction) or interaction.user.id!=self.owner:
                await interaction.response.send_message('Only the battle owner can play.',ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True,thinking=True)
            try:
                reply=await combat_execute(bot.repo,interaction.guild_id,interaction.user.id,interaction.id,
                    'assist' if action=='assist_mode' else ('turn' if action else 'auto'),
                    **({'action':action} if action and action!='assist_mode' else {}))
                b=await combat_current(bot.repo,interaction.guild_id,interaction.user.id)
                if b and b['status'] == 'active':
                    await interaction.followup.send(reply, view=BattleView(self.owner,b), ephemeral=True)
                else:
                    await interaction.followup.send(reply, ephemeral=True)
            except ValueError as error:
                await interaction.followup.send(str(error),ephemeral=True)
            except Exception:
                logging.exception('Combat button failure')
                await interaction.followup.send('Combat action failed. Check /battle_status.',ephemeral=True)
        return callback

@bot.tree.command(description='Hunt a spirit beast in manual or auto mode (cultivation safe)')
@app_commands.choices(enemy=[app_commands.Choice(name=ENEMIES[k]['name'],value=k) for k in ENEMIES],
                      mode=[app_commands.Choice(name='Manual',value='manual'),app_commands.Choice(name='Auto',value='auto'),
                            app_commands.Choice(name='Assisted',value='assisted')])
async def hunt(interaction: discord.Interaction, enemy: str='spirit_wolf', mode: str='manual'):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.',ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        msg=await combat_execute(bot.repo,interaction.guild_id,interaction.user.id,interaction.id,'start',enemy=enemy)
        if mode in ('auto','assisted'):
            # Distinct deterministic action ID from the start interaction.
            msg=await combat_execute(bot.repo,interaction.guild_id,interaction.user.id,-interaction.id,
                'auto' if mode=='auto' else 'assist')
        if mode != 'auto':
            b=await combat_current(bot.repo,interaction.guild_id,interaction.user.id)
            if b and b['status']=='active':
                await interaction.followup.send(msg, view=BattleView(interaction.user.id,b), ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except ValueError as error:
        await interaction.followup.send(str(error),ephemeral=True)
    except Exception:
        logging.exception('Hunt failure')
        await interaction.followup.send('Hunt failed; check the terminal.',ephemeral=True)

@bot.tree.command(description='View combat technique mastery and unlocked abilities')
async def techniques(interaction: discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.',ephemeral=True)
        return
    b=await combat_current(bot.repo,interaction.guild_id,interaction.user.id)
    if not b or not b.get('status'):
        await interaction.response.send_message('No combat record yet. Use /hunt first.',ephemeral=True)
        return
    mastery=b.get('mastery',{})
    text='Combat technique mastery (persists between battles):\n'
    for action,label in ACTIONS.items():
        uses=mastery.get(action,0)
        state='LOCKED (6 Thunder Palm uses)' if action=='thunder_judgment' and not unlocked(b,action) else f'Rank {mastery_tier(uses)}'
        text+=f'{label}: {uses} uses | {state}\n'
    text+='Combo: Thunder Palm -> Basic Strike triggers Storm Break.\nEnemy heavy attack occurs every third turn.'
    await interaction.response.send_message(text,ephemeral=True)

@bot.tree.command(description='Resume an unfinished battle or see the latest result')
async def battle_status(interaction: discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.',ephemeral=True)
        return
    b=await combat_current(bot.repo,interaction.guild_id,interaction.user.id)
    if not b or not b.get('status'):
        await interaction.response.send_message('No battle yet. Use /hunt to begin.',ephemeral=True)
        return
    if b['status'] == 'active':
        await interaction.response.send_message(battle_display(b), view=BattleView(interaction.user.id, await combat_current(bot.repo,interaction.guild_id,interaction.user.id)), ephemeral=True)
    else:
        await interaction.response.send_message(battle_display(b), ephemeral=True)

# v0.17: persistent rewards and craftable equipment. Commands remain private-guild only.
import inventory as inventory_system

@bot.tree.command(description='See your saved battle loot, marks, and equipped gear')
async def inventory(interaction: discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    state=await inventory_system.current(bot.repo,interaction.guild_id,interaction.user.id)
    await interaction.response.send_message(inventory_system.format_inventory(state),ephemeral=True)

@bot.tree.command(description='Show available equipment crafting recipes')
async def recipes(interaction: discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    await interaction.response.send_message(inventory_system.format_recipes(),ephemeral=True)

@bot.tree.command(description='Craft equipment from saved battle loot and combat marks')
@app_commands.choices(item=[app_commands.Choice(name=inventory_system.ITEMS[k],value=k) for k in inventory_system.RECIPES])
async def craft(interaction: discord.Interaction, item: str):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await inventory_system.change(bot.repo,interaction.guild_id,interaction.user.id,'craft',item)
        await interaction.followup.send(reply,ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('Crafting failed')
        await interaction.followup.send('Crafting could not be confirmed. Check /inventory.',ephemeral=True)

@bot.tree.command(description='Equip a crafted weapon, robe or charm')
@app_commands.choices(item=[app_commands.Choice(name=inventory_system.ITEMS[k],value=k) for k in inventory_system.RECIPES])
async def equip(interaction: discord.Interaction, item: str):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await inventory_system.change(bot.repo,interaction.guild_id,interaction.user.id,'equip',item)
        await interaction.followup.send(reply,ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('Equipment change failed')
        await interaction.followup.send('Equip could not be confirmed. Check /inventory.',ephemeral=True)


# v0.19: ALL requested cultivation, affinity, event, and alchemy mechanics in one release.
from heavens import (COMMON, RARE, PATHS, ELIXIRS, current_event, world_for_day,
                     active_effects, boost_summary, dt, relevant_root)
from eternal import rate_now
from game import unpack_player
from rapid import foundation_capacity
from datetime import timedelta

@bot.tree.command(description='Live Qi dashboard: rates, ETA, overflow, elixirs and Heavenly events')
async def qi(interaction: discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private guild only.', ephemeral=True);return
    await interaction.response.defer(ephemeral=True, thinking=True)
    state=await bot.repo.get_state(interaction.guild_id,interaction.user.id)
    if state is None:
        await interaction.followup.send('Use /start to begin your cultivation.',ephemeral=True);return
    origin=await bot.repo.get_origin(interaction.guild_id,interaction.user.id)
    root=origin['root'] if origin else None
    player=unpack_player(state['player'])
    if state.get('golden_core'):
        from golden_core_v022 import capacity as golden_capacity
        realm=f'Golden Core {state.get("golden_stage",1)}'
        current=state.get('golden_qi',0);cap=golden_capacity(state.get('golden_stage',1))
    elif player.advanced_to_foundation:
        realm=f'Foundation Establishment {state.get("foundation_stage",1)}'
        current=state.get('foundation_qi',0)
        cap=foundation_capacity(state.get('foundation_stage',1))
    else:
        realm=f'Qi Refining {player.stage}'
        current=player.qi;cap=player.capacity
    now=datetime.now(timezone.utc)
    base,factor,rate=rate_now(state,now,interaction.guild_id,root)
    info=state.get('eternal') or {};bank=info.get('bank_qi',0)
    remaining=max(0,cap-current)
    if current>=cap: eta='Ready — breakthrough required'
    else: eta=f'~{remaining/max(rate,0.001):.1f} hours at current rate (bonuses may expire)'
    progress=round(current/max(cap,1)*100,1)
    filled=min(16,round(16*current/max(cap,1)))
    embed=discord.Embed(title='☯ Eternal Qi · Dao of Eternity',description=(
        f'**{realm}** | {root or "Unawakened"}'+(f' + {state["secondary_root"]}' if state.get('secondary_root') else '')+f' Root | {state.get("cultivation_path","No path")}'
        f'\n**Cultivating permanently** · Never expires'),color=0x6964DF)
    embed.add_field(name='Spiritual Qi',value=f'**{current:,} / {cap:,}** ({progress}%)\n'+'█'*filled+'░'*(16-filled),inline=False)
    embed.add_field(name='⚡ Effective Qi Rate',value=f'**{rate:g} Qi/hour**\nBase {base} × {factor/1000:g}',inline=True)
    embed.add_field(name='⏳ Time to Breakthrough',value=eta,inline=True)
    embed.add_field(name='♾️ Banked Qi',value=f'{bank:,}',inline=True)
    embed.add_field(name='📈 Lifetime Passive Qi',value=f'{info.get("total_earned",0):,}',inline=True)
    embed.add_field(name='🔮 Current Multipliers',value='\n'.join('• '+x for x in boost_summary(state,interaction.guild_id,root,now)),inline=False)
    queue=state.get('elixir_queue',[])
    embed.add_field(name='🧪 Elixir Queue',value=(f'{len(queue)} active/queued • /elixirs to inspect' if queue else 'Empty • use /brew_elixir'),inline=False)
    embed.set_footer(text='Automatic offline gains • global world events apply to all players • manual breakthroughs')
    # Rich art and an accessible text embed are sent together.
    # If rendering is unavailable, the text remains usable.
    try:
        from dashboard_v020 import render
        art=render({'realm':realm,'identity':f'{root or "Unawakened"} Root · {state.get("cultivation_path","No path")}',
                    'current':current,'cap':cap,'rate':rate,'rate_note':f'Base {base} × {factor/1000:g}',
                    'eta':eta,'bank':bank,'earned':info.get('total_earned',0),
                    'boosts':' · '.join(boost_summary(state,interaction.guild_id,root,now))[:68],
                    'elixirs':f'{len(queue)} elixir(s) active / queued'})
        file=discord.File(art,filename='eternal_qi.png')
        embed.set_image(url='attachment://eternal_qi.png')
        await interaction.followup.send(embed=embed,file=file,ephemeral=True)
    except Exception:
        logging.exception('Qi artwork unavailable; falling back to text')
        await interaction.followup.send(embed=embed,ephemeral=True)

@bot.tree.command(description='List all eight common elemental affinities and their powers')
async def affinities(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private guild only.',ephemeral=True);return
    info='\n'.join(f'**{k}:** {v[1]} • Encounter: {v[0]} • Tradeoff: {v[2]}' for k,v in COMMON.items())
    await interaction.response.send_message('**Eight elemental roots** (equal +10% awakened Qi bonus)\n'+info+'\n\nRare lottery roots: '+', '.join(RARE)+' (3% total chance).\nUse /awaken for a chosen common root or /root_lottery for a permanent random root.',ephemeral=True)

@bot.tree.command(description='Free one-time root lottery: 3% rare, otherwise a common root')
async def root_lottery(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private guild only.',ephemeral=True);return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await bot.repo.choose_root_lottery(interaction.guild_id,interaction.user.id)
        await interaction.followup.send(reply,ephemeral=True)
    except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('Root lottery error');await interaction.followup.send('Root lottery failed. Check the terminal.',ephemeral=True)

@bot.tree.command(description='Choose your permanent cultivation path independently from your root')
@app_commands.choices(path=[app_commands.Choice(name=k,value=k) for k in PATHS])
async def choose_path(interaction:discord.Interaction,path:str):
    await run_op(interaction,'path_select',path=path)

@bot.tree.command(description='Seek a rare personal affinity-themed Qi encounter every two hours')
@app_commands.choices(approach=[app_commands.Choice(name='Safe (22% boon chance)',value='safe'),
                                 app_commands.Choice(name='Bold (35% boon chance)',value='bold')])
async def omen(interaction:discord.Interaction,approach:str='safe'):
    await run_op(interaction,'omen_seek',approach=approach)

@bot.tree.command(description='See the current global Qi phenomenon affecting all players')
async def world_event(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private guild only.',ephemeral=True);return
    now=datetime.now(timezone.utc);event=current_event(interaction.guild_id,now)
    if event:
        remain=int((event['end']-now).total_seconds())//60
        reply=(f'🌌 **{event["name"]}** is ACTIVE across this server!\n'
            f'All players: {event["mult"]/1000:g}x Qi until {event["end"].strftime("%H:%M UTC")} '
            f'(~{remain}m). {event["affinity"]} resonance: +20% event affinity bonus. '
            'Also applies retroactively while you are offline.')
    else:reply='🌌 The heavens are calm. World Qi surges appear on unpredictable days; offline players still receive all event-period Qi.'
    await interaction.response.send_message(reply,ephemeral=True)

@bot.tree.command(description='View active elixir and automatic sequential elixir queue')
async def elixirs(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private guild only.',ephemeral=True);return
    state=await bot.repo.get_state(interaction.guild_id,interaction.user.id)
    if not state:
        await interaction.response.send_message('Use /start first.',ephemeral=True);return
    queue=state.get('elixir_queue',[])
    lines=['**Automatic Qi Elixir Queue** (never needs manual reactivation)']
    for i,x in enumerate(queue,1):
        lines.append(f'{i}. {x["name"]} ({x["mult"]/1000:g}x) — {"ACTIVE" if dt(x["start_at"])<=datetime.now(timezone.utc)<dt(x["end_at"]) else "QUEUED"} until {dt(x["end_at"]).strftime("%d %b %H:%M UTC")}')
    if not queue:lines.append('Empty. Win battles for marks, then /brew_elixir to queue a boost.')
    lines.append('Recipes: '+', '.join(f'{name} {mult/1000:g}x / {mins}m / {cost} marks' for name,mult,mins,cost in ELIXIRS.values()))
    await interaction.response.send_message('\n'.join(lines),ephemeral=True)

@bot.tree.command(description='Spend battle marks on an elixir, automatically queued after the prior one')
@app_commands.choices(elixir=[app_commands.Choice(name=f'{label} ({mult/1000:g}x, {price} marks)',value=k) for k,(label,mult,duration,price) in ELIXIRS.items()])
async def brew_elixir(interaction:discord.Interaction,elixir:str):
    if not authorized(interaction):
        await interaction.response.send_message('Private guild only.',ephemeral=True);return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await bot.repo.brew_elixir(interaction.guild_id,interaction.user.id,interaction.id,elixir,datetime.now(timezone.utc))
        await interaction.followup.send(reply,ephemeral=True)
    except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('Elixir brewing failure');await interaction.followup.send('Elixir brewing could not be confirmed. Inspect /elixirs and /inventory.',ephemeral=True)

@bot.tree.command(description='Unlock a permanent secondary common root with F2P boss rewards')
@app_commands.choices(root=[app_commands.Choice(name=k,value=k) for k in COMMON])
async def dual_root(interaction:discord.Interaction,root:str):
    if not authorized(interaction):
        await interaction.response.send_message('Private guild only.',ephemeral=True);return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        msg=await bot.repo.choose_dual_root(interaction.guild_id,interaction.user.id,root)
        await interaction.followup.send(msg,ephemeral=True)
    except ValueError as error:await interaction.followup.send(str(error),ephemeral=True)
    except Exception:
        logging.exception('Dual root unlock failed')
        await interaction.followup.send('Dual root unlock failed. See bot terminal.',ephemeral=True)

@bot.tree.command(description='Set the channel for rare Heavenly Qi event announcements (server managers)')
async def set_qi_channel(interaction:discord.Interaction):
    if not authorized(interaction):
        await interaction.response.send_message('Private guild only.',ephemeral=True);return
    if not interaction.user.guild_permissions.manage_guild and not is_developer(interaction):
        await interaction.response.send_message('Only server managers can configure the event channel.',ephemeral=True);return
    if not isinstance(interaction.channel,discord.TextChannel):
        await interaction.response.send_message('Run this in the text channel where you want announcements.',ephemeral=True);return
    await bot.repo.set_world_channel(interaction.guild_id,interaction.channel.id)
    await interaction.response.send_message('🌌 Heavenly world events will be announced in this channel when the bot is online. Offline events still reward all players.',ephemeral=True)

@tasks.loop(minutes=5)
async def qi_event_announcer():
    if bot.repo is None:return
    now=datetime.now(timezone.utc)
    event=current_event(GUILD_ID,now)
    if not event:return
    channel_id=await bot.repo.get_world_channel(GUILD_ID)
    if not channel_id:return
    if not await bot.repo.mark_world_announcement(GUILD_ID,event['id']):return
    try:
        channel=bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        await channel.send(f'🌌 **{event["name"]} — HEAVENLY QI SURGE!**\n'
          f'All cultivators receive **{event["mult"]/1000:g}x passive Qi** until <t:{int(event["end"].timestamp())}:R>.\n'
          f'**{event["affinity"]} affinity** resonates with this phenomenon (+20% event bonus). '
          'Automatic boosts apply even to offline players. Check `/qi` and `/world_event`.')
    except Exception:
        await bot.repo.unmark_world_announcement(GUILD_ID,event['id'])
        logging.exception('Heavenly announcement failed; will retry later')

@qi_event_announcer.before_loop
async def before_qi_announcer():
    await bot.wait_until_ready()



# v0.20 Living Dao Expansion: all new state lives in separate additive tables.
from living_dao import REGIONS,COMPANIONS,QUESTS,action as living_action,read as living_read,view_sect,view_quests,view_companion

async def living_run(interaction,operation,argument=None):
    if not authorized(interaction):
        await interaction.response.send_message('Private server only.',ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        result=await living_action(bot.repo,interaction.guild_id,interaction.user.id,interaction.id,operation,argument)
        await interaction.followup.send(result,ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('Living Dao action error')
        await interaction.followup.send('Could not confirm action. Check your inventory and status.',ephemeral=True)

@bot.tree.command(description='Explore a region for materials and marks; five-minute cooldown')
@app_commands.choices(region=[app_commands.Choice(name=REGIONS[k][0],value=k) for k in REGIONS])
async def explore(interaction:discord.Interaction,region:str):
    await living_run(interaction,'explore',region)

@bot.tree.command(description='View missions and progress toward permanent rewards')
async def quests(interaction:discord.Interaction):
    if not authorized(interaction):return await interaction.response.send_message('Private server only.',ephemeral=True)
    await interaction.response.send_message(await view_quests(bot.repo,interaction.guild_id,interaction.user.id),ephemeral=True)

@bot.tree.command(description='Claim a completed Living Dao quest')
@app_commands.choices(quest=[app_commands.Choice(name=v[0],value=k) for k,v in QUESTS.items()])
async def quest_claim(interaction:discord.Interaction,quest:str):
    await living_run(interaction,'quest',quest)

@bot.tree.command(description='List your companion or find adoptable spirit beasts')
async def companion(interaction:discord.Interaction):
    if not authorized(interaction):return await interaction.response.send_message('Private server only.',ephemeral=True)
    await interaction.response.send_message(await view_companion(bot.repo,interaction.guild_id,interaction.user.id),ephemeral=True)

@bot.tree.command(description='Adopt a spirit beast with gameplay-earned combat marks')
@app_commands.choices(beast=[app_commands.Choice(name=v[0],value=k) for k,v in COMPANIONS.items()])
async def adopt(interaction:discord.Interaction,beast:str):
    await living_run(interaction,'adopt',beast)

@bot.tree.command(description='Train your spirit beast bond with earned marks')
async def bond(interaction:discord.Interaction):
    await living_run(interaction,'bond')

@bot.tree.command(description='View sect membership and available sects')
async def sect(interaction:discord.Interaction):
    if not authorized(interaction):return await interaction.response.send_message('Private server only.',ephemeral=True)
    await interaction.response.send_message(await view_sect(bot.repo,interaction.guild_id,interaction.user.id),ephemeral=True)

@bot.tree.command(description='Create a sect with 20 earned marks')
async def sect_create(interaction:discord.Interaction,name:str):
    await living_run(interaction,'sect_create',name)

@bot.tree.command(description='Join an existing cultivation sect')
async def sect_join(interaction:discord.Interaction,name:str):
    await living_run(interaction,'sect_join',name)

@bot.tree.command(description='Contribute earned marks to your sect')
async def sect_contribute(interaction:discord.Interaction,marks:app_commands.Range[int,1,100]):
    await living_run(interaction,'contribute',marks)



# v0.21 Sect Domain commands — additive persistent progression.
from sect_halls import HALLS, act as hall_act, overview as hall_overview

@bot.tree.command(description='See your sect halls, contributed treasury and cooperative trials')
async def sect_domain(interaction: discord.Interaction):
    if not authorized(interaction):
        return await interaction.response.send_message('Private server only.',ephemeral=True)
    await interaction.response.send_message(await hall_overview(bot.repo,interaction.guild_id,interaction.user.id),ephemeral=True)

async def hall_run(interaction, operation, argument=None):
    if not authorized(interaction):
        return await interaction.response.send_message('Private server only.',ephemeral=True)
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await hall_act(bot.repo,interaction.guild_id,interaction.user.id,interaction.id,operation,argument)
        await interaction.followup.send(reply,ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('Sect hall failure')
        await interaction.followup.send('Could not confirm the operation. Check /sect_domain.',ephemeral=True)

@bot.tree.command(description='Founder: upgrade a sect hall using contributed prosperity')
@app_commands.choices(hall=[app_commands.Choice(name=v[0],value=k) for k,v in HALLS.items()])
async def sect_upgrade(interaction:discord.Interaction,hall:str):
    await hall_run(interaction,'upgrade',hall)

@bot.tree.command(description='Complete a sect challenge to earn marks; 30-minute sect cooldown')
async def sect_trial(interaction:discord.Interaction):
    await hall_run(interaction,'trial')


# v0.22 — Golden Core, companion realms, consent-based cultivation bonds, and sect roles/raids.
from golden_core_v022 import CHOICES as GOLDEN_CHOICES
from bonds_v022 import (NPCS, ROLES as HOUSE_ROLES, view as household_view,apply as household_act)
from sect_v022 import (overview as sect_v022_overview,act as sect_v022_act,ROLES as SECT_ROLES)
from beast_v022 import view as beast_view,action as beast_action

async def v22_call(interaction,fn,*args,**kwargs):
    if not authorized(interaction):
        return await interaction.response.send_message('Private server only.',ephemeral=True)
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        result=await fn(bot.repo,interaction.guild_id,interaction.user.id,interaction.id,*args,**kwargs)
        await interaction.followup.send(result,ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('v0.22 action error')
        await interaction.followup.send('Action could not be confirmed. Check your character/status and bot terminal.',ephemeral=True)

@bot.tree.command(description='Begin the three-phase Golden Core ritual after completing Foundation Stage 9')
async def golden_core_trial(interaction:discord.Interaction):
    await run_op(interaction,'golden_core_trial')

@bot.tree.command(description='Choose the next stage of your active Golden Core ritual')
@app_commands.choices(choice=[app_commands.Choice(name=label,value=key) for key,label in GOLDEN_CHOICES.items()])
async def golden_choice(interaction:discord.Interaction,choice:str):
    await run_op(interaction,'golden_choice',choice=choice)

@bot.tree.command(description='Advance from Golden Core Stage 1 through Stage 9')
async def golden_advance(interaction:discord.Interaction):
    await run_op(interaction,'golden_advance')

@bot.tree.command(description="See your spirit beast's independent eternal cultivation and evolution")
async def beast_cultivation(interaction:discord.Interaction):
    if not authorized(interaction):return await interaction.response.send_message('Private server only.',ephemeral=True)
    await interaction.response.send_message(await beast_view(bot.repo,interaction.guild_id,interaction.user.id),ephemeral=True)

@bot.tree.command(description='Advance your spirit beast when its personal Qi is full')
async def beast_advance(interaction:discord.Interaction):
    await v22_call(interaction,beast_action,'advance')

@bot.tree.command(description='Evolve a fully cultivated Stage-9 spirit beast')
async def beast_evolve(interaction:discord.Interaction):
    await v22_call(interaction,beast_action,'evolve')

@bot.tree.command(description='View your household, optional NPC roles, player partnerships and Dao bonds')
async def household(interaction:discord.Interaction):
    if not authorized(interaction):return await interaction.response.send_message('Private server only.',ephemeral=True)
    await interaction.response.send_message(await household_view(bot.repo,interaction.guild_id,interaction.user.id),ephemeral=True)

@bot.tree.command(description='Recruit a fictional NPC spouse, consort, retainer or disciple with earned marks')
@app_commands.choices(npc=[app_commands.Choice(name=v[0],value=k) for k,v in NPCS.items()],
                      role=[app_commands.Choice(name=r.title(),value=r) for r in HOUSE_ROLES])
async def household_recruit(interaction:discord.Interaction,npc:str,role:str):
    await v22_call(interaction,household_act,'recruit',npc=npc,role=role)

@bot.tree.command(description='Advance an NPC member after independent offline cultivation reaches its stage cap')
@app_commands.choices(npc=[app_commands.Choice(name=v[0],value=k) for k,v in NPCS.items()])
async def household_advance(interaction:discord.Interaction,npc:str):
    await v22_call(interaction,household_act,'npc_advance',npc=npc)

@bot.tree.command(description='Give a consent-friendly household NPC gift to improve bond')
@app_commands.choices(npc=[app_commands.Choice(name=v[0],value=k) for k,v in NPCS.items()])
async def household_gift(interaction:discord.Interaction,npc:str):
    await v22_call(interaction,household_act,'gift',npc=npc)

@bot.tree.command(description='Assign household retainers or disciples to an earned-mark task')
async def household_task(interaction:discord.Interaction):
    await v22_call(interaction,household_act,'task')

@bot.tree.command(description='Practice non-explicit dual cultivation with an NPC partner')
async def dual_cultivate(interaction:discord.Interaction):
    await v22_call(interaction,household_act,'npc_dual')

@bot.tree.command(description='Solo cultivation alternative with the same Qi bonus as partnership cultivation')
async def solo_meditation(interaction:discord.Interaction):
    await v22_call(interaction,household_act,'solo')

@bot.tree.command(description='Invite another player to a consensual cultivation partnership')
@app_commands.choices(role=[app_commands.Choice(name='Spouse',value='spouse'),app_commands.Choice(name='Consort',value='consort')])
async def partner_propose(interaction:discord.Interaction,player:discord.Member,role:str):
    await v22_call(interaction,household_act,'propose',target=player.id,role=role)

@bot.tree.command(description='Explicitly accept a partnership invitation from another player')
async def partner_accept(interaction:discord.Interaction,from_player:discord.Member):
    await v22_call(interaction,household_act,'accept',target=from_player.id)

@bot.tree.command(description='Decline a partnership invitation')
async def partner_decline(interaction:discord.Interaction,from_player:discord.Member):
    await v22_call(interaction,household_act,'decline',target=from_player.id)

@bot.tree.command(description='End a player partnership without losing any character progress')
async def partner_end(interaction:discord.Interaction,player:discord.Member):
    await v22_call(interaction,household_act,'end',target=player.id)

@bot.tree.command(description='Offer a dual-cultivation session to an established partner')
async def dual_offer(interaction:discord.Interaction,player:discord.Member):
    await v22_call(interaction,household_act,'dual_offer',target=player.id)

@bot.tree.command(description='Accept a session and grant both partners a timed Qi boost')
async def dual_accept(interaction:discord.Interaction,from_player:discord.Member):
    await v22_call(interaction,household_act,'dual_accept',target=from_player.id)

@bot.tree.command(description='Inspect sect roles, banner, raid state and membership')
async def sect_roster(interaction:discord.Interaction):
    if not authorized(interaction):return await interaction.response.send_message('Private server only.',ephemeral=True)
    try:await interaction.response.send_message(await sect_v022_overview(bot.repo,interaction.guild_id,interaction.user.id),ephemeral=True)
    except ValueError as exc:await interaction.response.send_message(str(exc),ephemeral=True)

@bot.tree.command(description='Founder: set an original description or motto for your sect')
async def sect_banner(interaction:discord.Interaction,motto:str):
    await v22_call(interaction,sect_v022_act,'banner',argument=motto)

@bot.tree.command(description='Founder: appoint an Elder, Inner Disciple, or Outer Disciple')
@app_commands.choices(rank=[app_commands.Choice(name=r,value=r) for r in SECT_ROLES])
async def sect_rank(interaction:discord.Interaction,player:discord.Member,rank:str):
    await v22_call(interaction,sect_v022_act,'rank',argument=rank,target=player.id)

@bot.tree.command(description='Founder or Elder: open a shared Ancient Wraith raid')
async def sect_raid_start(interaction:discord.Interaction):
    await v22_call(interaction,sect_v022_act,'raid_start')

@bot.tree.command(description="Join your sect's cooperative raid roster")
async def sect_raid_join(interaction:discord.Interaction):
    await v22_call(interaction,sect_v022_act,'raid_join')

@bot.tree.command(description='Founder or Elder: launch raid with at least two real members')
async def sect_raid_launch(interaction:discord.Interaction):
    await v22_call(interaction,sect_v022_act,'raid_launch')

@bot.tree.command(description='Contribute your attack to the shared sect raid; rewards are distributed once')
async def sect_raid_strike(interaction:discord.Interaction):
    await v22_call(interaction,sect_v022_act,'raid_strike')


from golden_sandbox_v022 import run as golden_sandbox_run

async def sandbox_gold(interaction,op,choice=None):
    if not is_developer(interaction):
        return await interaction.response.send_message('Developer-only sandbox unavailable.',ephemeral=True)
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        reply=await golden_sandbox_run(bot.repo,interaction.guild_id,interaction.user.id,interaction.id,op,choice)
        await interaction.followup.send(reply,ephemeral=True)
    except ValueError as exc:await interaction.followup.send(str(exc),ephemeral=True)
    except Exception:
        logging.exception('Golden sandbox error')
        await interaction.followup.send('Sandbox failed. Check bot terminal.',ephemeral=True)

@bot.tree.command(description='[DEV] Prepare a separate Golden Core breakthrough trial character')
async def dev_golden_prepare(interaction:discord.Interaction):
    await sandbox_gold(interaction,'reset')

@bot.tree.command(description='[DEV] Start the separate Golden Core breakthrough trial')
async def dev_golden_trial(interaction:discord.Interaction):
    await sandbox_gold(interaction,'trial')

@bot.tree.command(description='[DEV] Choose a Golden Core trial phase in the separate sandbox')
@app_commands.choices(choice=[app_commands.Choice(name=label,value=key) for key,label in GOLDEN_CHOICES.items()])
async def dev_golden_choice(interaction:discord.Interaction,choice:str):
    await sandbox_gold(interaction,'choice',choice)

@bot.tree.command(description='[DEV] Advance isolated Golden Core sandbox stages without touching your character')
async def dev_golden_advance(interaction:discord.Interaction):
    await sandbox_gold(interaction,'advance')

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    bot.run(TOKEN,log_handler=None)
