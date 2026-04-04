import asyncio
import os
import time

import discord
from discord import app_commands
from django.core.management.base import BaseCommand

from bot import services
from bot.ollama_client import validate_ollama_startup


LLM_THINKING_THRESHOLD = int(os.environ.get('LLM_THINKING_THRESHOLD', '5'))


def message_starts_with_other_mention(message: discord.Message, bot_user: discord.ClientUser) -> bool:
    content = message.content.strip()
    if not content.startswith('<@'):
        return False
    end = content.find('>')
    if end == -1:
        return False
    mention_id = content[2:end].lstrip('!')
    try:
        mentioned_id = int(mention_id)
    except ValueError:
        return False
    return mentioned_id != bot_user.id


class MoveProjectView(discord.ui.View):
    def __init__(self, user_discord_id: str, username: str):
        super().__init__(timeout=60)
        self.user_discord_id = user_discord_id
        self.username = username

    @discord.ui.button(label='Move to another project', style=discord.ButtonStyle.secondary)
    async def move_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot.models import DiscordUser, Project, ProjectMember
        try:
            user = DiscordUser.objects.get(discord_id=self.user_discord_id)
            misc = services.get_miscellaneous_project(user)
            other_projects = [p for p in services.get_user_projects(user) if p.id != misc.id]
            if other_projects:
                names = '\n'.join(f'• {p.name}' for p in other_projects)
                await interaction.response.send_message(
                    f"Your other projects:\n{names}\n\n(Full move support coming soon.)",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You have no other projects to move this task to.",
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)


def build_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    @client.event
    async def on_ready():
        await tree.sync()
        print(f'Bot ready: {client.user} (ID: {client.user.id})', flush=True)

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        if message_starts_with_other_mention(message, client.user):
            return

        loop = asyncio.get_event_loop()
        thinking_message = None
        sent_thinking = False
        start = time.monotonic()

        async def poll_thinking():
            nonlocal thinking_message, sent_thinking
            while True:
                await asyncio.sleep(0.5)
                elapsed = time.monotonic() - start
                if elapsed >= LLM_THINKING_THRESHOLD and not sent_thinking:
                    thinking_message = await message.channel.send('Thinking...')
                    sent_thinking = True
                    break

        poll_task = asyncio.create_task(poll_thinking())

        try:
            response_text, needs_move_button = await loop.run_in_executor(
                None,
                services.process_message,
                str(message.author.id),
                str(message.author.name),
                message.content,
                str(message.channel.id),
            )
        finally:
            poll_task.cancel()

        if response_text is None:
            if thinking_message:
                await thinking_message.delete()
            return

        view = MoveProjectView(str(message.author.id), str(message.author.name)) if needs_move_button else None

        if thinking_message:
            await thinking_message.edit(content=response_text, view=view)
        else:
            await message.channel.send(response_text, view=view)

    @tree.command(name='authorize', description='Authorize a Discord user to use the bot')
    @app_commands.describe(user='The user to authorize')
    async def authorize(interaction: discord.Interaction, user: discord.Member):
        if not services.is_admin(str(interaction.user.id)):
            await interaction.response.send_message('You do not have permission to do this.', ephemeral=True)
            return
        try:
            loop = asyncio.get_event_loop()
            discord_user, created = await loop.run_in_executor(
                None, services.authorize_user,
                str(interaction.user.id), str(user.id), str(user.name),
            )
            status = 'authorized' if created else 'already existed and has been authorized'
            await interaction.response.send_message(
                f'{user.mention} has been {status}.', ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @tree.command(name='add_collaborator', description='Add a collaborator to a project')
    @app_commands.describe(user='The user to add', project='The project name')
    async def add_collaborator(interaction: discord.Interaction, user: discord.Member, project: str):
        try:
            from bot.models import DiscordUser
            loop = asyncio.get_event_loop()

            def _run():
                actor = DiscordUser.objects.get(discord_id=str(interaction.user.id))
                target = DiscordUser.objects.get(discord_id=str(user.id))
                services.add_collaborator(actor, project, target)

            await loop.run_in_executor(None, _run)
            await interaction.response.send_message(
                f'{user.mention} added to project "{project}".', ephemeral=True
            )
        except PermissionError as e:
            await interaction.response.send_message(f'Permission denied: {e}', ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @tree.command(name='link_project', description='Link this channel to a project')
    @app_commands.describe(project='The project name to link to this channel')
    async def link_project(interaction: discord.Interaction, project: str):
        try:
            from bot.models import DiscordUser
            loop = asyncio.get_event_loop()

            def _run():
                actor = DiscordUser.objects.get(discord_id=str(interaction.user.id))
                services.link_channel_to_project(actor, project, str(interaction.channel_id))

            await loop.run_in_executor(None, _run)
            await interaction.response.send_message(
                f'This channel is now linked to project "{project}".', ephemeral=True
            )
        except PermissionError as e:
            await interaction.response.send_message(f'Permission denied: {e}', ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @tree.command(name='help', description='Show how to use the bot')
    async def help_command(interaction: discord.Interaction):
        text = (
            "**Project & Task Bot**\n\n"
            "Just talk to me naturally — I'll figure out what to do.\n\n"
            "**Examples:**\n"
            "• `Create a project called Website Redesign`\n"
            "• `Add a task to write the homepage copy`\n"
            "• `Mark task 3 as done`\n"
            "• `What are my open tasks?`\n"
            "• `Show tasks for Website Redesign`\n\n"
            "**Slash commands:**\n"
            "• `/authorize @user` — grant a user access (admins only)\n"
            "• `/add_collaborator @user <project>` — add someone to a project\n"
            "• `/link_project <project>` — link this channel to a project so tasks land here by default\n"
        )
        await interaction.response.send_message(text, ephemeral=True)

    return client


class Command(BaseCommand):
    help = 'Run the Discord bot'

    def handle(self, *args, **options):
        validate_ollama_startup()
        token = os.environ['DISCORD_BOT_TOKEN']
        client = build_client()
        client.run(token)
