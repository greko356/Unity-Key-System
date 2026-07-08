import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import random
import string
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ATHENS_TZ = ZoneInfo("Europe/Athens")
DB_PATH = "data/keys.db"

if not os.path.exists("data"):
    os.makedirs("data")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

# ====================== DATABASE ======================
cursor.execute("PRAGMA journal_mode=WAL")
cursor.execute("PRAGMA synchronous=NORMAL")
cursor.execute("PRAGMA foreign_keys=ON")

cursor.execute("""
CREATE TABLE IF NOT EXISTS keys (
    key TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    added_by TEXT NOT NULL,
    hwid TEXT,
    hwid_bound_at TEXT,
    hwid_reset_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    duration_value INTEGER NOT NULL,
    duration_type TEXT NOT NULL
)
""")

cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON keys(user_id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_expires_at ON keys(expires_at)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_hwid ON keys(hwid)")
conn.commit()

# ====================== LOGGING ======================
def log_action(action: str, details: str = ""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if details:
        print(f"[{timestamp}] [{action}] {details}")
    else:
        print(f"[{timestamp}] [{action}]")

# ====================== HELPER FUNCTIONS ======================
def generate_key(length=10):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def calculate_expiration(value: int, unit: str):
    now = datetime.utcnow()
    if unit == "hour":
        return now + timedelta(hours=value)
    elif unit == "day":
        return now + timedelta(days=value)
    elif unit == "week":
        return now + timedelta(weeks=value)
    elif unit == "year":
        return now + timedelta(days=value * 365)
    return now

def get_time_left(expires_at: str):
    try:
        exp = datetime.fromisoformat(expires_at)
        now = datetime.utcnow()
        remaining = exp - now
        if remaining.total_seconds() <= 0:
            return "Expired"
        days = remaining.days
        hours = remaining.seconds // 3600
        return f"{days}d {hours}h"
    except:
        return "Unknown"

def format_athens_time(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
        athens_time = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ATHENS_TZ)
        return athens_time.strftime("%d/%m/%Y %I:%M %p")
    except:
        return dt_str

def delete_expired_keys():
    cursor.execute("SELECT key, expires_at FROM keys")
    rows = cursor.fetchall()
    deleted = 0
    for key, expires_at in rows:
        if get_time_left(expires_at) == "Expired":
            cursor.execute("DELETE FROM keys WHERE key = ?", (key,))
            deleted += 1
    if deleted > 0:
        conn.commit()
        log_action("CLEANUP", f"Deleted {deleted} expired keys")
    return deleted

# ====================== COOLDOWN ======================
reset_cooldowns = {}

def check_reset_cooldown(user_id: int) -> bool:
    if user_id not in reset_cooldowns:
        return True
    if datetime.utcnow() - reset_cooldowns[user_id] > timedelta(minutes=10):
        return True
    return False

# ====================== API VALIDATION FUNCTION ======================
def check_key_and_hwid(key: str, hwid: str):
    """
    Ελέγχει το key + hwid και επιστρέφει αποτέλεσμα για το Roblox.
    """
    delete_expired_keys()

    cursor.execute("""
        SELECT user_id, username, hwid, hwid_bound_at, expires_at 
        FROM keys 
        WHERE key = ?
    """, (key,))
    row = cursor.fetchone()

    if not row:
        return {
            "status": "error",
            "message": "Invalid key"
        }

    user_id, username, stored_hwid, hwid_bound_at, expires_at = row

    # Έλεγχος λήξης
    if get_time_left(expires_at) == "Expired":
        return {
            "status": "error",
            "message": "Key has expired"
        }

    # Αν δεν έχει HWID ακόμα → Δέσμευση
    if stored_hwid is None:
        cursor.execute("""
            UPDATE keys 
            SET hwid = ?, hwid_bound_at = ? 
            WHERE key = ?
        """, (hwid, datetime.utcnow().isoformat(), key))
        conn.commit()

        log_action("HWID_BOUND", f"Key={key} | HWID={hwid} | User={username}")

        return {
            "status": "success",
            "message": "Key activated successfully",
            "bound": True,
            "time_left": get_time_left(expires_at)
        }

    # Αν έχει ήδη HWID → Έλεγχος
    if stored_hwid == hwid:
        return {
            "status": "success",
            "message": "Key is valid",
            "bound": True,
            "time_left": get_time_left(expires_at)
        }
    else:
        return {
            "status": "error",
            "message": "This key is bound to another device"
        }

# ====================== COG ======================
class KeySystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        delete_expired_keys()
        log_action("SYSTEM", "KeySystem cog loaded successfully")

    # ====================== /add_key ======================
    @app_commands.command(name="add_key", description="Create a new key for a user")
    @app_commands.describe(user="Target user", time="Duration value", time2="Time unit")
    @app_commands.choices(time2=[
        app_commands.Choice(name="Hour", value="hour"),
        app_commands.Choice(name="Day", value="day"),
        app_commands.Choice(name="Week", value="week"),
        app_commands.Choice(name="Year", value="year"),
    ])
    async def add_key(self, interaction: discord.Interaction, user: discord.Member, time: int, time2: str):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You do not have permission.", ephemeral=True)

        key = generate_key()
        expires = calculate_expiration(time, time2)
        added_by = str(interaction.user)

        cursor.execute("""
            INSERT INTO keys (key, user_id, username, added_by, hwid, hwid_bound_at, hwid_reset_count, created_at, expires_at, duration_value, duration_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (key, user.id, str(user), added_by, None, None, 0, datetime.utcnow().isoformat(), expires.isoformat(), time, time2))
        conn.commit()

        log_action("KEY_CREATED", f"Key={key} | User={user} | By={added_by}")

        embed = discord.Embed(title="Key Created Successfully", color=0x2ecc71)
        embed.add_field(name="Key", value=f"`{key}`", inline=False)
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Duration", value=f"{time} {time2}", inline=True)
        embed.add_field(name="Added By", value=added_by, inline=True)
        embed.add_field(name="Expires", value=format_athens_time(expires.isoformat()), inline=False)
        await interaction.response.send_message(embed=embed)

    # ====================== /info_mykey ======================
    @app_commands.command(name="info_mykey", description="View your key information (DM only)")
    async def info_mykey(self, interaction: discord.Interaction):
        if interaction.guild is not None:
            return await interaction.response.send_message("This command can only be used in DMs.", ephemeral=True)

        delete_expired_keys()
        user_id = interaction.user.id

        cursor.execute("""
            SELECT key, added_by, hwid, hwid_reset_count, expires_at 
            FROM keys 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()

        if not row:
            return await interaction.response.send_message("No active key found for your account.", ephemeral=True)

        key, added_by, hwid, reset_count, expires_at = row
        time_left = get_time_left(expires_at)
        hwid_status = f"Bound ({reset_count} resets)" if hwid else "Not Bound"

        embed = discord.Embed(title="Your Key Information", color=0x3498db)
        embed.add_field(name="Key", value=f"`{key}`", inline=False)
        embed.add_field(name="Added By", value=added_by, inline=True)
        embed.add_field(name="HWID Status", value=hwid_status, inline=True)
        embed.add_field(name="Time Left", value=time_left, inline=True)
        embed.add_field(name="Expires", value=format_athens_time(expires_at), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ====================== /reset_hwid ======================
    @app_commands.command(name="reset_hwid", description="Reset your HWID (DM only, 10 min cooldown)")
    async def reset_hwid(self, interaction: discord.Interaction, key: str):
        if interaction.guild is not None:
            return await interaction.response.send_message("This command can only be used in DMs.", ephemeral=True)

        user_id = interaction.user.id

        cursor.execute("SELECT user_id FROM keys WHERE key = ?", (key,))
        row = cursor.fetchone()

        if not row or row[0] != user_id:
            return await interaction.response.send_message("You can only reset HWID for keys that belong to you.", ephemeral=True)

        if not check_reset_cooldown(user_id):
            remaining = 10 - int((datetime.utcnow() - reset_cooldowns[user_id]).total_seconds() / 60)
            return await interaction.response.send_message(
                f"You can reset your HWID again in **{remaining} minutes**.",
                ephemeral=True
            )

        cursor.execute("""
            UPDATE keys 
            SET hwid = NULL, hwid_bound_at = NULL, hwid_reset_count = hwid_reset_count + 1 
            WHERE key = ?
        """, (key,))
        conn.commit()

        reset_cooldowns[user_id] = datetime.utcnow()
        log_action("HWID_RESET", f"Key={key} | User={interaction.user}")

        embed = discord.Embed(title="HWID Reset Successful", color=0x2ecc71)
        embed.add_field(name="Key", value=f"`{key}`", inline=False)
        embed.add_field(name="Status", value="HWID has been reset. You can now bind this key to a new device.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ====================== /leaderboard_key ======================
    @app_commands.command(name="leaderboard_key", description="View all active keys (paginated)")
    async def leaderboard_key(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You do not have permission.", ephemeral=True)

        delete_expired_keys()

        cursor.execute("SELECT key, username, added_by, hwid, hwid_reset_count, expires_at FROM keys ORDER BY expires_at ASC")
        all_keys = cursor.fetchall()

        if not all_keys:
            return await interaction.response.send_message("No active keys found.", ephemeral=True)

        per_page = 8
        pages = [all_keys[i:i + per_page] for i in range(0, len(all_keys), per_page)]

        class LeaderboardView(discord.ui.View):
            def __init__(self, pages):
                super().__init__(timeout=180)
                self.pages = pages
                self.current_page = 0

            async def update_embed(self, interaction: discord.Interaction):
                embed = self.create_embed()
                await interaction.response.edit_message(embed=embed, view=self)

            def create_embed(self):
                embed = discord.Embed(
                    title=f"Active Keys — Page {self.current_page + 1}/{len(self.pages)}",
                    color=0x9b59b6
                )
                for key, username, added_by, hwid, reset_count, expires_at in self.pages[self.current_page]:
                    time_left = get_time_left(expires_at)
                    hwid_status = f"Bound ({reset_count} resets)" if hwid else "Not Bound"
                    embed.add_field(
                        name=key,
                        value=f"**User:** {username}\n**Added By:** {added_by}\n**HWID:** {hwid_status}\n**Time Left:** {time_left}",
                        inline=False
                    )
                embed.set_footer(text="Use the buttons to navigate between pages")
                return embed

            @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
            async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current_page > 0:
                    self.current_page -= 1
                    await self.update_embed(interaction)
                else:
                    await interaction.response.defer()

            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current_page < len(self.pages) - 1:
                    self.current_page += 1
                    await self.update_embed(interaction)
                else:
                    await interaction.response.defer()

        view = LeaderboardView(pages)
        embed = view.create_embed()
        await interaction.response.send_message(embed=embed, view=view)

    # ====================== /delete_key ======================
    @app_commands.command(name="delete_key", description="Delete a key (with confirmation)")
    async def delete_key(self, interaction: discord.Interaction, key: str):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You do not have permission.", ephemeral=True)

        cursor.execute("SELECT username, added_by, expires_at FROM keys WHERE key = ?", (key,))
        row = cursor.fetchone()

        if not row:
            return await interaction.response.send_message(f"Key `{key}` not found.", ephemeral=True)

        username, added_by, expires_at = row

        embed = discord.Embed(title="Confirm Key Deletion", color=0xe74c3c)
        embed.add_field(name="Key", value=f"`{key}`", inline=False)
        embed.add_field(name="User", value=username, inline=True)
        embed.add_field(name="Added By", value=added_by, inline=True)
        embed.add_field(name="Expires", value=format_athens_time(expires_at), inline=False)

        class ConfirmDelete(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)

            @discord.ui.button(label="Delete Key", style=discord.ButtonStyle.danger)
            async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("Only the command user can confirm.", ephemeral=True)

                cursor.execute("DELETE FROM keys WHERE key = ?", (key,))
                conn.commit()
                log_action("KEY_DELETED", f"Key={key} | By={interaction.user}")
                await btn_interaction.response.edit_message(content=f"Key `{key}` has been successfully deleted.", embed=None, view=None)

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                await btn_interaction.response.edit_message(content="Key deletion cancelled.", embed=None, view=None)

        await interaction.response.send_message(embed=embed, view=ConfirmDelete(), ephemeral=True)

    # ====================== /update_key ======================
    @app_commands.command(name="update_key", description="Update the expiration of a key")
    @app_commands.describe(key="The key to update", time="New duration value", time2="Time unit")
    @app_commands.choices(time2=[
        app_commands.Choice(name="Hour", value="hour"),
        app_commands.Choice(name="Day", value="day"),
        app_commands.Choice(name="Week", value="week"),
        app_commands.Choice(name="Year", value="year"),
    ])
    async def update_key(self, interaction: discord.Interaction, key: str, time: int, time2: str):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You do not have permission.", ephemeral=True)

        cursor.execute("SELECT username, expires_at FROM keys WHERE key = ?", (key,))
        row = cursor.fetchone()

        if not row:
            return await interaction.response.send_message(f"Key `{key}` not found.", ephemeral=True)

        username, old_expires = row
        new_expires = calculate_expiration(time, time2)

        embed = discord.Embed(title="Confirm Key Update", color=0xf39c12)
        embed.add_field(name="Key", value=f"`{key}`", inline=False)
        embed.add_field(name="User", value=username, inline=True)
        embed.add_field(name="Current Expires", value=format_athens_time(old_expires), inline=False)
        embed.add_field(name="New Duration", value=f"{time} {time2}", inline=True)
        embed.add_field(name="New Expires", value=format_athens_time(new_expires.isoformat()), inline=False)

        class ConfirmUpdate(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)

            @discord.ui.button(label="Update Key", style=discord.ButtonStyle.primary)
            async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("Only the command user can confirm.", ephemeral=True)

                cursor.execute("""
                    UPDATE keys 
                    SET expires_at = ?, duration_value = ?, duration_type = ?
                    WHERE key = ?
                """, (new_expires.isoformat(), time, time2, key))
                conn.commit()
                log_action("KEY_UPDATED", f"Key={key} | New Duration={time} {time2} | By={interaction.user}")
                await btn_interaction.response.edit_message(content=f"Key `{key}` has been successfully updated.", embed=None, view=None)

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                await btn_interaction.response.edit_message(content="Key update cancelled.", embed=None, view=None)

        await interaction.response.send_message(embed=embed, view=ConfirmUpdate(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(KeySystem(bot))