# random-sounds-bot
Very easy discord bot that randomly make sounds. 

Run `uv sync` to install the Python dependencies. The bot also requires FFmpeg
to be installed and available on `PATH`.

Use these commands in a server:

- `!start` joins your voice channel and plays a random sound immediately, then
  repeats after random intervals of 30-120 seconds.
- `!start <min-interval> <max-interval>` sets the random interval in seconds,
  for example `!start 10 45`.
- `!stop` stops playback and disconnects the bot.
- `!list` lists the audio files in `sounds/`.

The bot needs permission to connect and speak in the voice channel. It
disconnects automatically if no other user is in the channel for 5 seconds.
The bundled MP3s are short clips (about 0.3-2 seconds).
