import os
import asyncio
import logging
import random
from pathlib import Path

import discord

from dotenv import load_dotenv

load_dotenv(override=True)

TOKEN = os.getenv("DISCORD_TOKEN")
SOUNDS_DIR = Path(__file__).parent / "sounds"
DEFAULT_SOUND_INTERVAL = (30, 120)
ALONE_DISCONNECT_DELAY = 5
SOUND_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
LOGGER = logging.getLogger("discord.random_sounds_bot")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
voice_tasks: dict[int, asyncio.Task[None]] = {}
alone_disconnect_tasks: dict[int, asyncio.Task[None]] = {}


def get_sounds() -> list[Path]:
    if not SOUNDS_DIR.is_dir():
        return []

    return sorted(
        sound
        for sound in SOUNDS_DIR.iterdir()
        if sound.is_file() and sound.suffix.lower() in SOUND_EXTENSIONS
    )


def parse_interval_args(args: str) -> tuple[int, int]:
    values = args.split()
    if len(values) > 2:
        raise ValueError("Indica al massimo l'intervallo minimo e massimo.")

    try:
        min_interval = int(values[0]) if values else DEFAULT_SOUND_INTERVAL[0]
        max_interval = int(values[1]) if len(values) > 1 else DEFAULT_SOUND_INTERVAL[1]
    except ValueError as error:
        raise ValueError("Gli intervalli devono essere numeri interi di secondi.") from error

    if min_interval < 1 or max_interval < 1:
        raise ValueError("Gli intervalli devono essere almeno di 1 secondo.")
    if min_interval > max_interval:
        raise ValueError("L'intervallo minimo non può superare quello massimo.")

    return min_interval, max_interval


async def play_random_sounds(
    voice_client: discord.VoiceClient,
    sounds: list[Path],
    intervals: tuple[int, int],
) -> None:
    loop = asyncio.get_running_loop()

    while voice_client.is_connected():
        finished = asyncio.Event()
        playback_errors: list[Exception] = []

        def after_playback(error: Exception | None) -> None:
            if error is not None:
                playback_errors.append(error)
            loop.call_soon_threadsafe(finished.set)

        sound = random.choice(sounds)
        LOGGER.info(
            "Riproduzione di %s nel server %s",
            sound.name,
            voice_client.guild.id,
        )
        voice_client.play(
            discord.FFmpegPCMAudio(str(sound)),
            after=after_playback,
        )
        await finished.wait()

        if playback_errors:
            raise RuntimeError(
                f"Errore durante la riproduzione di {sound.name}"
            ) from playback_errors[0]

        await asyncio.sleep(random.randint(*intervals))


def on_voice_task_done(guild_id: int, task: asyncio.Task[None]) -> None:
    if voice_tasks.get(guild_id) is task:
        del voice_tasks[guild_id]

    if task.cancelled():
        return

    error = task.exception()
    if error is not None:
        LOGGER.error(
            "Riproduzione casuale terminata nel server %s: %s",
            guild_id,
            error,
        )


def bot_is_alone(voice_client: discord.VoiceClient) -> bool:
    if client.user is None:
        return False

    members = voice_client.channel.members
    return (
        any(member.id == client.user.id for member in members)
        and all(member.id == client.user.id for member in members)
    )


async def disconnect_voice(
    guild: discord.Guild,
    voice_client: discord.VoiceClient,
) -> None:
    timer = alone_disconnect_tasks.get(guild.id)
    if timer is not None and timer is not asyncio.current_task():
        timer.cancel()

    playback_task = voice_tasks.pop(guild.id, None)
    if playback_task is not None and not playback_task.done():
        playback_task.cancel()

    if voice_client.is_playing():
        voice_client.stop()

    if playback_task is not None and not playback_task.done():
        try:
            await playback_task
        except asyncio.CancelledError:
            pass

    if voice_client.is_connected():
        await voice_client.disconnect(force=True)


async def disconnect_if_alone(
    guild: discord.Guild,
    voice_client: discord.VoiceClient,
) -> None:
    try:
        await asyncio.sleep(ALONE_DISCONNECT_DELAY)
        if (
            guild.voice_client is voice_client
            and voice_client.is_connected()
            and bot_is_alone(voice_client)
        ):
            LOGGER.info(
                "Disconnessione automatica: nessun altro utente nel server %s",
                guild.id,
            )
            await disconnect_voice(guild, voice_client)
    finally:
        current_task = asyncio.current_task()
        if alone_disconnect_tasks.get(guild.id) is current_task:
            del alone_disconnect_tasks[guild.id]


# =========================
# COMMAND HANDLERS
# =========================

async def handle_start(message, args):
    guild = message.guild
    if guild is None:
        await message.reply("Questo comando può essere usato solo in un server.")
        return

    if not isinstance(message.author, discord.Member):
        await message.reply("Non riesco a trovare il tuo stato vocale nel server.")
        return

    try:
        intervals = parse_interval_args(args)
    except ValueError as error:
        await message.reply(
            f"{error}\nUso: `!start [min-interval] [max-interval]`"
        )
        return

    voice_state = message.author.voice
    if voice_state is None or voice_state.channel is None:
        await message.reply("Entra in un canale vocale prima di usare `!start`.")
        return

    sounds = get_sounds()
    if not sounds:
        await message.reply("Non ho trovato file audio nella cartella `sounds`.")
        return

    voice_client = guild.voice_client

    try:
        if voice_client is None or not voice_client.is_connected():
            voice_client = await voice_state.channel.connect()
        elif voice_client.channel != voice_state.channel:
            await voice_client.move_to(voice_state.channel)
    except (asyncio.TimeoutError, discord.ClientException, discord.Forbidden):
        LOGGER.exception(
            "Impossibile connettersi al canale vocale nel server %s",
            guild.id,
        )
        await message.reply(
            "Non riesco a entrare nel canale vocale. Controlla i permessi del bot."
        )
        return

    bot_member = guild.me
    bot_voice_state = bot_member.voice if bot_member is not None else None
    if bot_voice_state is not None:
        if bot_voice_state.mute:
            LOGGER.error("Il bot risulta silenziato dal server.")
        if bot_voice_state.self_mute:
            LOGGER.error("Il bot risulta silenziato da solo.")

    task = voice_tasks.get(guild.id)
    if task is not None and not task.done():
        task.cancel()
        if voice_client.is_playing():
            voice_client.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(play_random_sounds(voice_client, sounds, intervals))
    voice_tasks[guild.id] = task
    task.add_done_callback(
        lambda completed_task: on_voice_task_done(guild.id, completed_task)
    )
    await message.reply(
        f"Connesso a {voice_state.channel.mention}. Riprodurrò suoni casuali "
        f"ogni {intervals[0]}-{intervals[1]} secondi."
    )


async def handle_stop(message, _args):
    guild = message.guild
    if guild is None:
        await message.reply("Questo comando può essere usato solo in un server.")
        return

    voice_client = guild.voice_client
    if voice_client is None or not voice_client.is_connected():
        await message.reply("Non sono connesso a un canale vocale.")
        return

    await disconnect_voice(guild, voice_client)
    await message.reply("Mi sono disconnesso dal canale vocale.")


async def handle_list(message, _args):
    sounds = get_sounds()
    if not sounds:
        await message.reply("Non ho trovato file audio nella cartella `sounds`.")
        return

    lines = [f"- {sound.name}" for sound in sounds]
    chunks: list[str] = []
    current_chunk = "**Suoni disponibili:**\n"
    for line in lines:
        if len(current_chunk) + len(line) + 1 > 1900:
            chunks.append(current_chunk)
            current_chunk = ""
        current_chunk += f"{line}\n"
    chunks.append(current_chunk)

    for chunk in chunks:
        await message.reply(chunk)


async def handle_help(message, _args):

    await message.reply(
        """
**Comandi disponibili**

`!start [min-interval] [max-interval]`
Entra nel tuo canale vocale e riproduce suoni casuali da `sounds/`.
Gli intervalli sono in secondi; il valore predefinito è `30 120`.

`!stop`
Ferma la riproduzione e disconnette il bot.

`!list`
Elenca i suoni disponibili.

`!help`
Mostra questo messaggio.
        """
    )


# =========================
# DISPATCHER
# =========================

COMMANDS = {
    "start": handle_start,
    "stop": handle_stop,
    "list": handle_list,
    "help": handle_help,
}


# =========================
# EVENTS
# =========================

@client.event
async def on_ready():
    print(f"✅ Bot online: {client.user}")


@client.event
async def on_voice_state_update(member, _before, _after):
    guild = member.guild
    voice_client = guild.voice_client
    if voice_client is None or not voice_client.is_connected():
        return

    timer = alone_disconnect_tasks.get(guild.id)
    if not bot_is_alone(voice_client):
        if timer is not None:
            timer.cancel()
            alone_disconnect_tasks.pop(guild.id, None)
        return

    if timer is None or timer.done():
        task = asyncio.create_task(disconnect_if_alone(guild, voice_client))
        alone_disconnect_tasks[guild.id] = task


@client.event
async def on_message(message):

    if message.author.bot:
        return

    if not message.content.startswith("!"):
        return

    parts = message.content[1:].split(
        maxsplit=1
    )

    command = parts[0].lower()

    args = (
        parts[1].strip()
        if len(parts) > 1
        else ""
    )

    handler = COMMANDS.get(command)

    if handler:
        await handler(message, args)
    else:
        await message.reply(
            f"❓ Comando sconosciuto: `{command}`\nUsa `!help`."
        )


client.run(TOKEN)