import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN            = os.getenv("DISCORD_TOKEN")
GATE_CHANNEL_ID  = int(os.getenv("GATE_CHANNEL_ID", 0))   # channel to receive join alerts
HOLDING_ROLE_ID  = int(os.getenv("HOLDING_ROLE_ID", 0))   # role given on join (no permissions)
MEMBER_ROLE_ID   = int(os.getenv("MEMBER_ROLE_ID",  0))   # role given on Allow
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ── Join alert embed + buttons ────────────────────────────────────────────────

class GateView(discord.ui.View):
    """Persistent view so buttons survive bot restarts."""

    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id
        # Give each button a unique custom_id so Discord can route interactions
        # after a restart (requires add_view in on_ready).
        self.allow_button.custom_id = f"gate_allow_{target_user_id}"
        self.kick_button.custom_id  = f"gate_kick_{target_user_id}"

    # ── Allow ─────────────────────────────────────────────────────────────────
    @discord.ui.button(label="✅  Allow", style=discord.ButtonStyle.success)
    async def allow_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild  = interaction.guild
        member = guild.get_member(self.target_user_id)

        if member is None:
            await interaction.response.send_message(
                "⚠️ Member not found – they may have already left.", ephemeral=True
            )
            return

        # Swap holding role → member role
        holding_role = guild.get_role(HOLDING_ROLE_ID)
        member_role  = guild.get_role(MEMBER_ROLE_ID)

        try:
            if holding_role and holding_role in member.roles:
                await member.remove_roles(holding_role, reason="Gated entry – approved")
            if member_role:
                await member.add_roles(member_role, reason="Gated entry – approved")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Missing permissions to manage roles.", ephemeral=True
            )
            return

        # Update the embed
        await _mark_resolved(interaction, member, "✅ Allowed", discord.Color.green())

    # ── Kick ──────────────────────────────────────────────────────────────────
    @discord.ui.button(label="🚫  Kick", style=discord.ButtonStyle.danger)
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild  = interaction.guild
        member = guild.get_member(self.target_user_id)

        if member is None:
            await interaction.response.send_message(
                "⚠️ Member not found – they may have already left.", ephemeral=True
            )
            return

        try:
            await member.kick(reason=f"Gated entry – denied by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Missing permissions to kick members.", ephemeral=True
            )
            return

        await _mark_resolved(interaction, member, "🚫 Kicked", discord.Color.red())


async def _mark_resolved(
    interaction: discord.Interaction,
    member: discord.Member,
    action_label: str,
    colour: discord.Color,
):
    """Edit the original embed to show who took action and disable the buttons."""
    embed = interaction.message.embeds[0]
    embed.colour = colour
    embed.set_footer(
        text=f"{action_label} by {interaction.user} ({interaction.user.id})"
    )

    # Disable both buttons
    view = interaction.message.components  # raw components – easiest to just rebuild
    disabled_view = discord.ui.View()
    for row in interaction.message.components:
        for component in row.children:
            btn = discord.ui.Button(
                label=component.label,
                style=component.style,
                custom_id=component.custom_id,
                disabled=True,
            )
            disabled_view.add_item(btn)

    await interaction.response.edit_message(embed=embed, view=disabled_view)


def build_join_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🔔 New Member Joined",
        description=(
            f"**{member.mention}** (`{member.name}#{member.discriminator}`) "
            "just joined the server.\n\n"
            "Review their profile and decide whether to allow or kick them."
        ),
        color=discord.Color.blurple(),
        timestamp=member.joined_at,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Account ID",      value=str(member.id),                        inline=True)
    embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
    embed.add_field(name="Joined Server",   value=discord.utils.format_dt(member.joined_at,  "R"), inline=True)
    embed.set_footer(text="Pending review")
    return embed


# ── Bot events ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    await bot.tree.sync()


@bot.event
async def on_member_join(member: discord.Member):
    # Assign the holding role so the user can't see any channels yet
    guild = member.guild
    holding_role = guild.get_role(HOLDING_ROLE_ID)
    if holding_role:
        try:
            await member.add_roles(holding_role, reason="Pending gate review")
        except discord.Forbidden:
            print(f"[WARN] Could not assign holding role to {member}")

    # Post alert in gate channel
    gate_channel = guild.get_channel(GATE_CHANNEL_ID)
    if gate_channel is None:
        print(f"[WARN] Gate channel {GATE_CHANNEL_ID} not found.")
        return

    embed = build_join_embed(member)
    view  = GateView(target_user_id=member.id)
    await gate_channel.send(embed=embed, view=view)


# ── Optional slash command: manually review a member ─────────────────────────

@bot.tree.command(name="review", description="Manually post a gate review for an existing member.")
@app_commands.describe(member="The member to review")
@app_commands.checks.has_permissions(kick_members=True)
async def review(interaction: discord.Interaction, member: discord.Member):
    embed = build_join_embed(member)
    view  = GateView(target_user_id=member.id)
    await interaction.response.send_message(embed=embed, view=view)


# ── Run ───────────────────────────────────────────────────────────────────────
bot.run(TOKEN)
