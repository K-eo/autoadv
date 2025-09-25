import discord
from discord.ext import commands, tasks
from discord import app_commands, Interaction, TextStyle
from discord.ui import View, Button, Modal, TextInput
import sqlite3, asyncio, os, json, aiohttp
from datetime import datetime, timedelta

BASE_DIR = "stuff"
os.makedirs(BASE_DIR, exist_ok=True)

with open(os.path.join(BASE_DIR, "config.json"), "r", encoding="utf-8") as f:
    config = json.load(f)

BOT_TOKEN = os.getenv("bot_token")
ADMIN_IDS = config.get("admins", [])
PING_SELLER = config.get("ping_seller")
API_URL = "http://45.13.225.195:6262/senduwu"
SECRET_KEY = "uwusecret123"

db_path = os.path.join(BASE_DIR, "database.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS keys(
    key TEXT PRIMARY KEY, plan TEXT, accs INTEGER, duration_hours INTEGER,
    channels_allowed INTEGER, created_at TEXT, used INTEGER DEFAULT 0, user_id TEXT)""")
c.execute("""CREATE TABLE IF NOT EXISTS users(
    user_id TEXT PRIMARY KEY, plan TEXT, accs INTEGER, expire_time TEXT)""")
c.execute("""CREATE TABLE IF NOT EXISTS accounts(
    user_id TEXT, acc_number INTEGER, token TEXT, server_id TEXT,
    channel_name TEXT, message_content TEXT, delay INTEGER,
    configured INTEGER DEFAULT 0, PRIMARY KEY(user_id, acc_number))""")
conn.commit()

def generate_key(length=12):
    import random, string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def add_key(key, plan, accs, duration_hours, channels_allowed):
    c.execute("INSERT INTO keys(key, plan, accs, duration_hours, channels_allowed, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (key, plan, accs, duration_hours, channels_allowed, datetime.now().isoformat()))
    conn.commit()

def redeem_key(key, user_id):
    c.execute("SELECT * FROM keys WHERE key=? AND used=0", (key,))
    result = c.fetchone()
    if not result:
        return None
    plan, accs, duration_hours, channels_allowed = result["plan"], result["accs"], result["duration_hours"], result["channels_allowed"]
    expire_time = datetime.now() + timedelta(hours=duration_hours)
    c.execute("INSERT OR REPLACE INTO users(user_id, plan, accs, expire_time) VALUES (?, ?, ?, ?)",
              (user_id, plan, accs, expire_time.isoformat()))
    c.execute("UPDATE keys SET used=1, user_id=? WHERE key=?", (user_id, key))
    conn.commit()
    return {"plan": plan, "accs": accs, "expire_time": expire_time, "channels_allowed": channels_allowed}

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents)

async def send_api_request(endpoint, payload):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"{API_URL}/{endpoint}", json=payload) as resp:
                if resp.status != 200:
                    return {"status": "error", "reason": f"HTTP {resp.status}"}
                return await resp.json()
        except:
            return {"status": "error", "reason": "API unreachable"}

async def check_loops():
    await bot.wait_until_ready()
    while True:
        c.execute("SELECT * FROM users")
        users = c.fetchall()
        for user in users:
            expire_time = datetime.fromisoformat(user["expire_time"])
            user_id = user["user_id"]
            if datetime.now() > expire_time:
                c.execute("SELECT * FROM accounts WHERE user_id=? AND configured=1", (user_id,))
                accounts = c.fetchall()
                for acc in accounts:
                    token = acc["token"]
                    await send_api_request("stop", {"token": token, "secret_key": SECRET_KEY})
                    c.execute("UPDATE accounts SET configured=0 WHERE user_id=? AND acc_number=?",
                              (user_id, acc["acc_number"]))
                    try:
                        guild = bot.guilds[0]
                        member = guild.get_member(int(user_id))
                        channel_name = f"setup-{member.name.lower()}"
                        channel = discord.utils.get(guild.text_channels, name=channel_name)
                        if channel:
                            embed = discord.Embed(
                                title="Account Stopped",
                                description=f"All accounts expired for your key.\nToken: `{token}`",
                                color=discord.Color.red()
                            )
                            await channel.send(f"<@{user_id}>", embed=embed)
                            for item in channel.last_message.embeds:
                                pass
                    except:
                        pass
                c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
                conn.commit()
        c.execute("SELECT * FROM accounts WHERE configured=1")
        rows = c.fetchall()
        for row in rows:
            token = row["token"]
            payload = {"token": token, "secret_key": SECRET_KEY}
            res = await send_api_request("check", payload)
            if res.get("status") != "ok":
                c.execute("UPDATE accounts SET configured=0 WHERE user_id=? AND acc_number=?",
                          (row["user_id"], row["acc_number"]))
                conn.commit()
                try:
                    guild = bot.guilds[0]
                    member = guild.get_member(int(row["user_id"]))
                    channel_name = f"setup-{member.name.lower()}"
                    channel = discord.utils.get(guild.text_channels, name=channel_name)
                    if channel:
                        embed = discord.Embed(
                            title="Account Stopped",
                            description=f"Account {row['acc_number']} stopped.\nReason: {res.get('reason')}\nToken: `{token}`",
                            color=discord.Color.red()
                        )
                        await channel.send(f"<@{row['user_id']}>", embed=embed)
                        for item in channel.last_message.embeds:
                            pass
                except:
                    pass
        await asyncio.sleep(10)

class AccountSetupModal(Modal):
    def __init__(self, acc_number, setup_message, setup_view, channels_allowed):
        super().__init__(title=f"Account {acc_number} Setup")
        self.acc_number = acc_number
        self.setup_message = setup_message
        self.setup_view = setup_view
        self.channels_allowed = channels_allowed
        self.token = TextInput(label="Token", style=TextStyle.short)
        self.delay = TextInput(label="Delay (seconds)", style=TextStyle.short)
        self.server_id = TextInput(label="Server ID", style=TextStyle.short)
        self.channel_name = TextInput(label=f"Channel Names (comma-separated, up to {channels_allowed})", style=TextStyle.short)
        self.message_content = TextInput(label="Message content", style=TextStyle.paragraph)
        self.add_item(self.token)
        self.add_item(self.delay)
        self.add_item(self.server_id)
        self.add_item(self.channel_name)
        self.add_item(self.message_content)

    async def on_submit(self, interaction: Interaction):
        user_id = str(interaction.user.id)
        channels_list = [ch.strip() for ch in self.channel_name.value.split(",") if ch.strip()]
        if len(channels_list) > self.channels_allowed:
            await interaction.response.send_message(f"You can only use up to {self.channels_allowed} channel(s).", ephemeral=True)
            return
        c.execute("""INSERT OR REPLACE INTO accounts(user_id, acc_number, token, server_id, 
                     channel_name, message_content, delay, configured) VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                  (user_id, self.acc_number, self.token.value, self.server_id.value,
                   ",".join(channels_list), self.message_content.value, int(self.delay.value)))
        conn.commit()
        for item in self.setup_view.children:
            if isinstance(item, Button) and item.label == f"Setup Acc {self.acc_number}":
                item.disabled = True
        await self.setup_message.edit(view=self.setup_view)
        payload = {
            "token": self.token.value,
            "message": self.message_content.value,
            "channels": channels_list,
            "delay": int(self.delay.value),
            "server": self.server_id.value,
            "secret_key": SECRET_KEY
        }
        res = await send_api_request("start", payload)
        if res.get("status") != "ok":
            for item in self.setup_view.children:
                if isinstance(item, Button) and item.label == f"Setup Acc {self.acc_number}":
                    item.disabled = False
            await self.setup_message.edit(view=self.setup_view)
            await interaction.response.send_message(f"Error: {res.get('reason')}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Account {self.acc_number} started and locked.", ephemeral=False)

async def create_setup_channel(user: discord.User, accs: int, channels_allowed: int, old_ticket=None):
    guild = old_ticket.guild if old_ticket else bot.guilds[0]
    if old_ticket:
        try: await old_ticket.delete()
        except: pass
    cats = [c for c in guild.categories if c.name.startswith("Setup")]
    category = cats[-1] if cats else await guild.create_category("Setup")
    if cats and len(category.text_channels) >= 50:
        category = await guild.create_category(f"Setup {len(cats)+1}")
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    channel = await guild.create_text_channel(f"setup-{user.name.lower()}", category=category, overwrites=overwrites)
    try:
        with open(os.path.join(BASE_DIR, "instructions.json"), "r", encoding="utf-8") as f:
            instructions_data = json.load(f)
            instructions_embed = discord.Embed.from_dict(instructions_data)
    except:
        instructions_embed = discord.Embed(title="Instructions", description="No instructions set.", color=discord.Color.green())
    await channel.send(f"<@{user.id}>")
    await channel.send(embed=instructions_embed)
    view = View(timeout=None)
    setup_message = await channel.send(embed=discord.Embed(title="Setup Here", description="Click a button to configure your accounts.", color=discord.Color.blue()), view=view)
    for i in range(1, accs + 1):
        c.execute("SELECT configured FROM accounts WHERE user_id=? AND acc_number=?", (str(user.id), i))
        r = c.fetchone()
        btn = Button(label=f"Setup Acc {i}", style=discord.ButtonStyle.green, disabled=(r and r["configured"] == 1))
        async def btn_callback(interaction, acc_num=i, setup_msg=setup_message, setup_v=view):
            modal = AccountSetupModal(acc_num, setup_msg, setup_v, channels_allowed)
            await interaction.response.send_modal(modal)
        btn.callback = btn_callback
        view.add_item(btn)
    admin_edit_btn = Button(label="Admin Edit Config", style=discord.ButtonStyle.red)
    async def admin_edit_callback(interaction: Interaction):
        if interaction.user.id not in ADMIN_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        c.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY acc_number", (str(user.id),))
        accounts = c.fetchall()
        if not accounts:
            await interaction.response.send_message("No accounts found.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Accounts for {user.name}", color=discord.Color.orange())
        for acc in accounts:
            embed.add_field(name=f"Account {acc['acc_number']}", value=f"Token: `{acc['token']}`\nServer: {acc['server_id']}\nDelay: {acc['delay']}\nChannels: {acc['channel_name']}\nMessage: {acc['message_content']}\nConfigured: {acc['configured']}", inline=False)
        view2 = View(timeout=None)
        for acc in accounts:
            edit_btn = Button(label=f"Edit Acc {acc['acc_number']}", style=discord.ButtonStyle.blurple)
            async def edit_callback(interaction2, acc_num=acc['acc_number']):
                class AdminEditModal(Modal):
                    def __init__(self):
                        super().__init__(title=f"Edit Account {acc_num}")
                        self.token = TextInput(label="Token", style=TextStyle.short, default=acc['token'])
                        self.delay = TextInput(label="Delay", style=TextStyle.short, default=str(acc['delay']))
                        self.server_id = TextInput(label="Server ID", style=TextStyle.short, default=acc['server_id'])
                        self.channels = TextInput(label="Channels", style=TextStyle.short, default=acc['channel_name'])
                        self.message = TextInput(label="Message", style=TextStyle.paragraph, default=acc['message_content'])
                        self.add_item(self.token)
                        self.add_item(self.delay)
                        self.add_item(self.server_id)
                        self.add_item(self.channels)
                        self.add_item(self.message)
                    async def on_submit(self2, interaction3: Interaction):
                        token_old = acc['token']
                        c.execute("UPDATE accounts SET token=?, delay=?, server_id=?, channel_name=?, message_content=?, configured=0 WHERE user_id=? AND acc_number=?",
                                  (self2.token.value, int(self2.delay.value), self2.server_id.value, self2.channels.value, self2.message.value, str(user.id), acc_num))
                        conn.commit()
                        payload_stop = {"token": token_old, "secret_key": SECRET_KEY}
                        await send_api_request("stop", payload_stop)
                        payload_start = {"token": self2.token.value, "message": self2.message.value, "channels": [ch.strip() for ch in self2.channels.value.split(",") if ch.strip()], "delay": int(self2.delay.value), "server": self2.server_id.value, "secret_key": SECRET_KEY}
                        await send_api_request("start", payload_start)
                        await interaction3.response.send_message(f"Account {acc_num} updated and restarted.", ephemeral=True)
                await interaction2.response.send_modal(AdminEditModal())
            edit_btn.callback = edit_callback
            view2.add_item(edit_btn)
        await interaction.response.send_message(embed=embed, view=view2, ephemeral=True)
    admin_edit_btn.callback = admin_edit_callback
    view.add_item(admin_edit_btn)
    await setup_message.edit(view=view)

@bot.event
async def on_ready():
    await bot.tree.sync()
    bot.loop.create_task(check_loops())
    print(f"Logged in as {bot.user} ({bot.user.id})")

@bot.tree.command(name="panel", description="Open main panel")
async def panel(interaction: Interaction):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    try:
        with open(os.path.join(BASE_DIR, "panel.json"), "r", encoding="utf-8") as f:
            panel_data = json.load(f)
            embed = discord.Embed.from_dict(panel_data)
    except:
        embed = discord.Embed(title="Panel", description="No panel set.", color=discord.Color.blue())
    view = View(timeout=None)
    ticket_btn = Button(label="Open Ticket", style=discord.ButtonStyle.green)
    async def ticket_callback(interaction):
        cat = discord.utils.get(interaction.guild.categories, name="Tickets")
        if not cat:
            cat = await interaction.guild.create_category("Tickets")
        overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                      interaction.user: discord.PermissionOverwrite(read_messages=True),
                      interaction.guild.me: discord.PermissionOverwrite(read_messages=True)}
        ticket_channel = await interaction.guild.create_text_channel(f"ticket-{interaction.user.name.lower()}", category=cat, overwrites=overwrites)
        await ticket_channel.send(embed=discord.Embed(title="Ticket Created", description="Your ticket will be checked soon.", color=discord.Color.green()))
        await ticket_channel.send(f"<@{PING_SELLER}>")
    ticket_btn.callback = ticket_callback
    view.add_item(ticket_btn)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="gen_key", description="Generate new key")
@app_commands.describe(duration="Duration in hours", accs="Number of accounts", channels="Max channels per account")
async def gen_key(interaction: Interaction, duration: int, accs: int, channels: int):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    key = generate_key()
    add_key(key, plan="Custom", accs=accs, duration_hours=duration, channels_allowed=channels)
    embed = discord.Embed(title="Key Generated", description=f"`{key}` for {accs} accounts, {duration} hours, up to {channels} channels per account.", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="redeem", description="Redeem key to start setup")
@app_commands.describe(key="The key to redeem")
async def redeem(interaction: Interaction, key: str):
    result = redeem_key(key, str(interaction.user.id))
    if not result:
        await interaction.response.send_message(embed=discord.Embed(
            title="Invalid Key",
            description="This key is invalid or already used.",
            color=discord.Color.red()
        ), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    old_ticket = None
    for ch in interaction.guild.text_channels:
        if ch.name.startswith(f"ticket-{interaction.user.name.lower()}"):
            old_ticket = ch
            break
    await create_setup_channel(interaction.user, accs=result["accs"], channels_allowed=result["channels_allowed"], old_ticket=old_ticket)
    try:
        await interaction.followup.send(embed=discord.Embed(
            title="Key Redeemed",
            description="Setup channel created.",
            color=discord.Color.green()
        ), ephemeral=True)
    except discord.errors.NotFound:
        pass

bot.run(BOT_TOKEN)
