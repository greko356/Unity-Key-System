import discord
from discord.ext import commands, tasks
import os
import asyncio
import threading
from flask import Flask, request, jsonify

# ====================== CONFIG ======================
TOKEN = "MTUxMzQyNzU5MTk4ODUxMDc4MA.G_LARu.tXQRXoHncaNECRkFvmdF8K2XkHyIK0SB-jogC0"
TWITCH_URL = "https://www.twitch.tv/mrstelios"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.voice_states = True

statuses = [
    discord.Streaming(name="By Mr.Stelios", url=TWITCH_URL),
    discord.Streaming(name="Unity Solutions", url=TWITCH_URL)
]
status_index = 0

# ====================== FLASK API ======================
app = Flask(__name__)

@app.route('/validate', methods=['POST'])
def validate_key():
    data = request.get_json()
    key = data.get('key')
    hwid = data.get('hwid')

    if not key or not hwid:
        return jsonify({"status": "error", "message": "Missing key or hwid"}), 400

    # Καλούμε τη συνάρτηση από το key_system cog
    from cogs.key_system import check_key_and_hwid
    result = check_key_and_hwid(key, hwid)
    return jsonify(result)

def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

# ====================== BOT ======================
async def load_extensions_from_folder(bot, folder_name):
    if not os.path.exists(folder_name):
        print(f"[WARNING] Folder not found: {folder_name}")
        return

    skip_files = {"__init__.py", "roblox_config.py"}

    for filename in os.listdir(folder_name):
        if not filename.endswith(".py"):
            continue
        if filename in skip_files:
            continue

        extension = f"{folder_name}.{filename[:-3]}"
        try:
            await bot.load_extension(extension)
            print(f"[LOADED] {extension}")
        except Exception as e:
            print(f"[ERROR] Failed to load {extension}: {e}")

class MyBot(commands.Bot):
    async def setup_hook(self):
        await load_extensions_from_folder(self, "cogs")
        await load_extensions_from_folder(self, "security")

        try:
            synced = await self.tree.sync()
            print(f"[SYNC] Synced {len(synced)} slash commands.")
        except Exception as e:
            print(f"[ERROR] Failed to sync commands: {e}")

bot = MyBot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

@tasks.loop(seconds=8)
async def change_status():
    global status_index
    await bot.change_presence(
        status=discord.Status.online,
        activity=statuses[status_index]
    )
    status_index = (status_index + 1) % len(statuses)

@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user}")

    if not change_status.is_running():
        change_status.start()

    # Εκκίνηση Flask σε ξεχωριστό thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("[FLASK] API started on port 5000")

async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())