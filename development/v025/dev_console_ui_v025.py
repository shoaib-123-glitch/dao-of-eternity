"""Developer console controls. Every component re-checks authorization server-side."""
import discord
import dev_console_v025 as console


def console_embed():
    settings = console.active()
    targets = len(settings.targets) if settings else 0
    embed = discord.Embed(title='Developer Testing Console · sandbox only', color=0xB04A4A,
        description=('Choose a category, an action and a target, then Run. Actions are bounded, '
                     'receipted and audited, and mark the target as developer-modified. '
                     'Normal players keep every cooldown and limit.'))
    embed.add_field(name='Targets', value=f'You and {targets} allowlisted test account(s).', inline=False)
    embed.set_footer(text='Never restore this sandbox into a real progression database.')
    return embed


class ParamsModal(discord.ui.Modal):
    def __init__(self, action, submit):
        super().__init__(title=action.label[:45], timeout=300)
        self.action = action
        self.submit = submit
        self.inputs = {}
        for param in action.params:
            hint = '/'.join(param.choices) if param.choices else f'{param.low}–{param.high}'
            field = discord.ui.TextInput(label=param.label[:45], placeholder=hint[:100], max_length=20, required=True)
            self.inputs[param.name] = field
            self.add_item(field)

    async def interaction_check(self, interaction):
        return await deny_unless_developer(interaction)

    async def on_submit(self, interaction):
        await self.submit(interaction, {name: field.value for name, field in self.inputs.items()})


async def deny_unless_developer(interaction, owner_id=None):
    if console.authorized(interaction.guild_id, interaction.user.id) and owner_id in (None, interaction.user.id):
        return True
    await interaction.response.send_message(console.UNAVAILABLE, ephemeral=True)
    return False


class ConsoleView(discord.ui.View):
    def __init__(self, owner_id, *, run, audit, category='cooldown'):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.run, self.audit = run, audit
        self.category = category
        self.action = None
        self.target = owner_id
        settings = console.active()
        self.category_menu = discord.ui.Select(placeholder='Category', row=0, options=[
            discord.SelectOption(label=label, value=key, default=key == category) for key, label in console.CATEGORIES.items()])
        self.category_menu.callback = self.choose_category
        self.add_item(self.category_menu)
        self.action_menu = discord.ui.Select(placeholder='Action', row=1, options=self.action_options())
        self.action_menu.callback = self.choose_action
        self.add_item(self.action_menu)
        targets = sorted(settings.targets - {owner_id}) if settings else []
        self.target_menu = discord.ui.Select(placeholder='Target: yourself', row=2, options=[
            discord.SelectOption(label='Yourself', value=str(owner_id), default=True)] + [
            discord.SelectOption(label=f'Test account {user}', value=str(user)) for user in targets[:24]])
        self.target_menu.callback = self.choose_target
        self.add_item(self.target_menu)
        run_button = discord.ui.Button(label='Run', style=discord.ButtonStyle.danger, row=3)
        run_button.callback = self.run_selected
        self.add_item(run_button)
        audit_button = discord.ui.Button(label='Recent audit', style=discord.ButtonStyle.secondary, row=3)
        audit_button.callback = self.show_audit
        self.add_item(audit_button)

    def action_options(self):
        return [discord.SelectOption(label=action.label, value=action.key)
                for action in console.REGISTRY.values() if action.category == self.category][:25]

    async def interaction_check(self, interaction):
        return await deny_unless_developer(interaction, self.owner_id)

    async def choose_category(self, interaction):
        self.category = self.category_menu.values[0]
        self.action = None
        for option in self.category_menu.options:
            option.default = option.value == self.category
        self.action_menu.options = self.action_options()
        await interaction.response.edit_message(view=self)

    async def choose_action(self, interaction):
        self.action = self.action_menu.values[0]
        for option in self.action_menu.options:
            option.default = option.value == self.action
        await interaction.response.edit_message(view=self)

    async def choose_target(self, interaction):
        value = self.target_menu.values[0]
        self.target = int(value) if value.isdigit() else self.owner_id
        for option in self.target_menu.options:
            option.default = option.value == value
        await interaction.response.edit_message(view=self)

    async def run_selected(self, interaction):
        action = console.REGISTRY.get(self.action)
        if action is None:
            await interaction.response.send_message('Choose an action first.', ephemeral=True)
            return
        if action.params:
            async def submit(modal_interaction, values):
                await self.run(modal_interaction, action.key, self.target, values)
            await interaction.response.send_modal(ParamsModal(action, submit))
        else:
            await self.run(interaction, action.key, self.target, {})

    async def show_audit(self, interaction):
        await self.audit(interaction)
