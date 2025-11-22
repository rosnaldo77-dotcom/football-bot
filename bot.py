# bot.py  (moderado) - usa python-telegram-bot v20 async + API-Football
import os
import logging
import datetime
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
API_KEY = os.environ.get("API_KEY")
TOKEN = os.environ.get("TOKEN")
API_URL = "https://v3.football.api-sports.io"

if not TOKEN or not API_KEY:
    raise RuntimeError("Set environment variables TOKEN and API_KEY before running.")

HEADERS = {"x-apisports-key": API_KEY}

def today_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

# --- Utilities to call API-Football ---
def api_get(path, params=None):
    url = f"{API_URL}{path}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.exception("API call failed")
        return None

def find_team_id(name):
    r = api_get("/teams", {"search": name})
    if not r or "response" not in r or not r["response"]:
        return None
    return r["response"][0]["team"]["id"]

def last_fixtures(team_id, last=5):
    r = api_get("/fixtures", {"team": team_id, "last": last})
    if not r or "response" not in r:
        return []
    return r["response"]

def summarize(team_id, last=5):
    fixtures = last_fixtures(team_id, last)
    if not fixtures:
        return None
    played = 0
    wins = draws = losses = 0
    gf = ga = 0
    for f in fixtures:
        score_home = f["score"]["fulltime"]["home"]
        score_away = f["score"]["fulltime"]["away"]
        if score_home is None or score_away is None:
            continue
        played += 1
        home_id = f["teams"]["home"]["id"]
        away_id = f["teams"]["away"]["id"]
        if team_id == home_id:
            team_goals = score_home
            opp_goals = score_away
        else:
            team_goals = score_away
            opp_goals = score_home
        gf += team_goals
        ga += opp_goals
        if team_goals > opp_goals:
            wins += 1
        elif team_goals == opp_goals:
            draws += 1
        else:
            losses += 1
    if played == 0:
        return None
    return {"played": played, "wins": wins, "draws": draws, "losses": losses,
            "avg_for": round(gf/played,2), "avg_against": round(ga/played,2)}

def simple_probs(a_id, b_id):
    sa = summarize(a_id, 5)
    sb = summarize(b_id, 5)
    if not sa or not sb:
        return None
    score_a = sa["avg_for"] + 0.5 * (sa["wins"]/sa["played"])
    score_b = sb["avg_for"] + 0.5 * (sb["wins"]/sb["played"])
    total = score_a + score_b
    if total == 0:
        return {"home":40,"draw":20,"away":40}
    ph = round((score_a/total)*100,1)
    pa = round((score_b/total)*100,1)
    pd = round(max(5.0, 100 - (ph+pa)),1)
    s = ph+pd+pa
    ph = round(ph*100/s,1); pd = round(pd*100/s,1); pa = round(pa*100/s,1)
    return {"home":ph,"draw":pd,"away":pa}

# --- Bot handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Jogos de Hoje", callback_data="hoje"),
         InlineKeyboardButton("Próximos Jogos", callback_data="proximos")],
        [InlineKeyboardButton("Pesquisar Time", callback_data="pesquisar")]
    ]
    await update.message.reply_text("⚽ Rosck Bot — Estatísticas\nEscolhe uma opção:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "hoje":
        await send_games_today(q)
    elif q.data == "proximos":
        await send_next(q)
    elif q.data == "pesquisar":
        await q.edit_message_text("Envia na conversa o nome do time (ex: Benfica) para ver estatísticas.")

async def send_games_today(query):
    today = today_iso()
    r = api_get("/fixtures", {"date": today})
    if not r or "response" not in r or not r["response"]:
        await query.edit_message_text("❌ Não foram encontrados jogos hoje ou API indisponível.")
        return
    lines = [f"📅 Jogos de hoje ({today}):\n"]
    for f in r["response"][:30]:
        home = f["teams"]["home"]["name"]; away = f["teams"]["away"]["name"]
        time_str = f["fixture"]["date"][11:16]
        lines.append(f"⏰ {time_str} — {home} vs {away}")
    await query.edit_message_text("\n".join(lines))

async def send_next(query):
    r = api_get("/fixtures", {"next":20})
    if not r or "response" not in r:
        await query.edit_message_text("Sem dados de próximos jogos.")
        return
    lines = ["📅 Próximos jogos:\n"]
    for f in r["response"][:30]:
        dt = f["fixture"]["date"].replace("T"," ")[:16]
        lines.append(f"📌 {dt} — {f['teams']['home']['name']} vs {f['teams']['away']['name']}")
    await query.edit_message_text("\n".join(lines))

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("Uso: /estatisticas <nome time>")
        return
    tid = find_team_id(text)
    if not tid:
        await update.message.reply_text("Time não encontrado.")
        return
    s = summarize(tid, 5)
    if not s:
        await update.message.reply_text("Sem dados suficientes.")
        return
    await update.message.reply_text(f"📊 {text} (últimos {s['played']}):\nVitórias:{s['wins']} Empates:{s['draws']} Derrotas:{s['losses']}\nMédia g: {s['avg_for']}  sofridos: {s['avg_against']}")

async def probs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body = update.message.text.partition(" ")[2].strip()
    if "|" in body:
        a,b = [x.strip() for x in body.split("|",1)]
    else:
        parts = body.split()
        if len(parts) < 2:
            await update.message.reply_text("Uso: /probabilidades timeA | timeB")
            return
        a = parts[0]; b = " ".join(parts[1:])
    ida = find_team_id(a); idb = find_team_id(b)
    if not ida or not idb:
        await update.message.reply_text("Não localizei um dos times.")
        return
    p = simple_probs(ida,idb)
    if not p:
        await update.message.reply_text("Sem dados para calcular.")
        return
    await update.message.reply_text(f"🔮 {a} vs {b}\nCasa: {p['home']}%  Empate: {p['draw']}%  Fora: {p['away']}%")

# --- main ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(CommandHandler("estatisticas", stats_command))
    app.add_handler(CommandHandler("probabilidades", probs_command))
    # run polling
    app.run_polling()

if __name__ == "__main__":
    main()
