import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ---------- CONFIG ----------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "state.json"

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")  # set this in Render
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # your Telegram user ID
GROUP_ID = int(os.getenv("GROUP_ID", "0"))  # main public group chat id (optional)

CASH_TAG = "$MichaelThornton40"  # your payout / buy-in tag
TABLE_SIZE = 5
BUY_IN = 5
WIN_PRIZE = 20
HOUSE_CUT = 5
PROMO_BONUS = 2.0  # $2 per active referred player


# ---------- STATE MANAGEMENT ----------

def load_state() -> Dict[str, Any]:
    DATA_DIR.mkdir(exist_ok=True)
    if not STATE_FILE.exists():
        return {
            "tables": {},        # table_id -> table data
            "next_table_id": 1,
            "players": {},       # user_id -> player info
            "promoters": {},     # user_id -> promoter info
        }
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "tables": {},
            "next_table_id": 1,
            "players": {},
            "promoters": {},
        }


def save_state(state: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_or_create_player(state: Dict[str, Any], user) -> Dict[str, Any]:
    uid = str(user.id)
    players = state.setdefault("players", {})
    if uid not in players:
        players[uid] = {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "joined_tables": 0,
            "wins": 0,
            "referred_by": None,
            "promo_code": None,
        }
    return players[uid]


def get_or_create_promoter(state: Dict[str, Any], user) -> Dict[str, Any]:
    uid = str(user.id)
    promoters = state.setdefault("promoters", {})
    if uid not in promoters:
        promoters[uid] = {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "promo_code": f"promo_{user.id}",
            "referred_players": 0,
            "pending_payout": 0.0,
            "total_paid": 0.0,
        }
    return promoters[uid]


def find_waiting_table(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for table in state["tables"].values():
        if table["status"] == "waiting" and len(table["players"]) < TABLE_SIZE:
            return table
    return None


def create_table(state: Dict[str, Any]) -> Dict[str, Any]:
    table_id = state["next_table_id"]
    state["next_table_id"] += 1
    table = {
        "id": table_id,
        "status": "waiting",  # waiting | running | finished
        "buy_in": BUY_IN,
        "players": [],
        "winner_id": None,
        "promoters": {},  # promoter_user_id -> count of referred players in this table
    }
    state["tables"][str(table_id)] = table
    return table


# ---------- COMMAND HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    user = update.effective_user
    args = context.args

    player = get_or_create_player(state, user)

    # Handle referral: /start promo_12345
    if args and args[0].startswith("promo_") and player["referred_by"] is None:
        promoter_id = args[0].split("_", 1)[1]
        if promoter_id != str(user.id):  # no self-ref
            player["referred_by"] = promoter_id
            # register promoter
            promoters = state.setdefault("promoters", {})
            if promoter_id not in promoters:
                promoters[promoter_id] = {
                    "id": int(promoter_id),
                    "username": None,
                    "first_name": None,
                    "promo_code": f"promo_{promoter_id}",
                    "referred_players": 0,
                    "pending_payout": 0.0,
                    "total_paid": 0.0,
                }
            promoters[promoter_id]["referred_players"] += 1

    save_state(state)

    text = (
        "🎱 Welcome to the $5 Pool Tournament!\n\n"
        "• 5 players per table\n"
        f"• Buy-in: ${BUY_IN} (send to {CASH_TAG})\n"
        f"• Winner gets: ${WIN_PRIZE}\n"
        f"• House keeps: ${HOUSE_CUT}\n"
        "• Promoters earn $2 per active player they bring in\n\n"
        "Use /join in the public group to join the next table.\n"
        "Use /promo here to get your personal referral link."
    )
    await update.message.reply_text(text)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎱 Commands:\n"
        "/join – join the next $5 table (in the group)\n"
        "/promo – get your referral link\n"
        "/status – see your stats\n\n"
        "Admin only:\n"
        "/tables – list tables\n"
        "/winner <table_id> @user – mark winner and close table\n"
        "/promostats – show promoter balances\n"
    )
    await update.message.reply_text(text)


async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    user = update.effective_user
    promoter = get_or_create_promoter(state, user)
    # sync username/name
    promoter["username"] = user.username
    promoter["first_name"] = user.first_name
    save_state(state)

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={promoter['promo_code']}"

    text = (
        "💸 Your promoter details:\n\n"
        f"Referral link:\n{link}\n\n"
        f"Pay-in cash tag: {CASH_TAG}\n"
        "You earn $2 for each referred player who plays a table."
    )
    await update.message.reply_text(text)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    user = update.effective_user
    player = get_or_create_player(state, user)
    promoters = state.get("promoters", {})
    promoter_info = promoters.get(str(user.id))

    text = (
        f"🏆 Your stats, {user.first_name}:\n\n"
        f"Tables joined: {player['joined_tables']}\n"
        f"Wins: {player['wins']}\n"
        f"Referred by: {player['referred_by'] or 'None'}\n\n"
    )

    if promoter_info:
        text += (
            "🎯 Promoter stats:\n"
            f"Referred players: {promoter_info['referred_players']}\n"
            f"Pending payout: ${promoter_info['pending_payout']:.2f}\n"
            f"Total paid: ${promoter_info['total_paid']:.2f}\n"
        )

    await update.message.reply_text(text)


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Player joins the next waiting table."""
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /join in the main group chat.")
        return

    state = load_state()
    user = update.effective_user
    player = get_or_create_player(state, user)

    table = find_waiting_table(state)
    if not table:
        table = create_table(state)

    uid = str(user.id)
    if uid in table["players"]:
        await update.message.reply_text("You are already in this table.")
        return

    table["players"].append(uid)
    player["joined_tables"] += 1
    save_state(state)

    current = len(table["players"])
    remaining = TABLE_SIZE - current

    await update.message.reply_text(
        f"🎱 {user.first_name} joined table #{table['id']} "
        f"({current}/{TABLE_SIZE} players)."
    )

    if remaining <= 0:
        table["status"] = "running"
        save_state(state)
        # Announce table start
        mentions = []
        for pid in table["players"]:
            p = state["players"].get(pid)
            if not p:
                continue
            uname = p.get("username")
            if uname:
                mentions.append(f"@{uname}")
            else:
                mentions.append(p.get("first_name", "Player"))

        text = (
            f"🔥 Table #{table['id']} is FULL and now RUNNING!\n\n"
            f"Players: {', '.join(mentions)}\n\n"
            "Play your 1v1 games and report the FINAL WINNER.\n"
            f"Admin: use /winner {table['id']} @username when done.\n\n"
            f"Buy-in: ${BUY_IN} to {CASH_TAG}\n"
            f"Winner gets: ${WIN_PRIZE}"
        )
        await update.message.chat.send_message(text)


async def tables(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    state = load_state()
    if not state["tables"]:
        await update.message.reply_text("No tables yet.")
        return

    lines = []
    for t_id, t in state["tables"].items():
        lines.append(
            f"Table #{t['id']} – {t['status']} – players: {len(t['players'])}"
        )
    await update.message.reply_text("📋 Tables:\n" + "\n".join(lines))


async def winner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin marks winner: /winner <table_id> @username"""
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /winner <table_id> @username"
        )
        return

    table_id_str = context.args[0]
    mention = context.args[1]

    state = load_state()
    table = state["tables"].get(table_id_str)
    if not table:
        await update.message.reply_text("Table not found.")
        return

    if table["status"] != "running":
        await update.message.reply_text("Table is not running.")
        return

    # try to match winner by username
    winner_uid = None
    for pid in table["players"]:
        p = state["players"].get(pid)
        if not p:
            continue
        uname = p.get("username")
        if uname and ("@" + uname.lower() == mention.lower()):
            winner_uid = pid
            break

    if winner_uid is None:
        await update.message.reply_text("Winner not found in this table.")
        return

    table["status"] = "finished"
    table["winner_id"] = winner_uid

    # increment winner's stats
    winner_player = state["players"][winner_uid]
    winner_player["wins"] += 1

    # pay promoter logic: if winner has a "referred_by" promoter, add $2
    promoter_id = winner_player.get("referred_by")
    if promoter_id:
        promoters = state.setdefault("promoters", {})
        prom = promoters.get(promoter_id)
        if prom:
            prom["pending_payout"] += PROMO_BONUS

    save_state(state)

    winner_name = winner_player.get("first_name") or winner_player.get("username") or "Winner"

    text = (
        f"🏆 Table #{table['id']} finished!\n"
        f"Winner: {winner_name}\n\n"
        f"Prize: ${WIN_PRIZE}\n"
        f"House keeps: ${HOUSE_CUT}\n"
        f"Promoter bonus (if any): ${PROMO_BONUS:.2f}\n\n"
        "Run /tables for status or /promostats for promoter balances (admin)."
    )
    await update.message.reply_text(text)


async def promostats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    state = load_state()
    promoters = state.get("promoters", {})
    if not promoters:
        await update.message.reply_text("No promoters yet.")
        return

    lines = []
    for p in promoters.values():
        name = p.get("first_name") or p.get("username") or str(p["id"])
        lines.append(
            f"{name}: referred={p['referred_players']}, "
            f"pending=${p['pending_payout']:.2f}, "
            f"paid=${p['total_paid']:.2f}"
        )

    await update.message.reply_text("📣 Promoter stats:\n" + "\n".join(lines))


# ---------- MAIN ----------

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set in environment variables")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("promo", promo))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("join", join))
    application.add_handler(CommandHandler("tables", tables))
    application.add_handler(CommandHandler("winner", winner))
    application.add_handler(CommandHandler("promostats", promostats))

    print("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
