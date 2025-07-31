import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)

ARTICLES_FILE = "articles.json"
RESULT_FILE = "confirmed_ranking.json"
AUTO_CONFIRM_TIME = 30  # seconds for test
USER_INACTIVITY_TIME = 15  # seconds for test

def load_articles():
    with open(ARTICLES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_final_result(ranking_ids, deleted_ids, midnight_ids, articles):
    by_id = {a["id"]: a for a in articles}
    data = {
        "ranking": [by_id[i] for i in ranking_ids],
        "deleted": [by_id[i] for i in deleted_ids],
        "save_for_midnight": [by_id[i] for i in midnight_ids],
    }
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def original_ranking_text(articles):
    lines = [f"{i+1}. {a['title']}" for i, a in enumerate(articles)]
    return "\n".join(lines)

def tweaking_status_text(articles, picked, deleted, midnight):
    left = [a for a in articles if a["id"] not in picked and a["id"] not in deleted and a["id"] not in midnight]
    lines = ["**Ranked so far:**"]
    for idx, aid in enumerate([k for k in sorted(picked, key=lambda x: picked[x])], 1):
        lines.append(f"{idx}. {next(a['title'] for a in articles if a['id']==aid)}")
    if deleted:
        lines.append("\n**Marked for Deletion:**")
        for aid in deleted:
            lines.append(f"- {next(a['title'] for a in articles if a['id']==aid)}")
    if midnight:
        lines.append("\n**Saved For Midnight:**")
        for aid in midnight:
            lines.append(f"- {next(a['title'] for a in articles if a['id']==aid)}")
    if left:
        lines.append("\n**To be handled:**")
        for a in left:
            lines.append(f"- {a['title']}")
    return "\n".join(lines)

def final_confirm_text(articles, picked, deleted, midnight):
    lines = []
    if picked:
        lines.append("**Final Ranking:**")
        for idx, aid in enumerate([k for k in sorted(picked, key=lambda x: picked[x])], 1):
            lines.append(f"{idx}. {next(a['title'] for a in articles if a['id']==aid)}")
    if deleted:
        lines.append("\n**Deleted:**")
        for aid in deleted:
            lines.append(f"- {next(a['title'] for a in articles if a['id']==aid)}")
    if midnight:
        lines.append("\n**Saved For Midnight:**")
        for aid in midnight:
            lines.append(f"- {next(a['title'] for a in articles if a['id']==aid)}")
    return "\n".join(lines) or "Nothing was picked"

def build_tweak_keyboard(articles, picked, deleted, midnight):
    left = [a for a in articles if a["id"] not in picked and a["id"] not in deleted and a["id"] not in midnight]
    next_pick = len(picked) + 1
    buttons = []
    for art in left:
        pick_btn = InlineKeyboardButton(
            f"#{next_pick} {art['title']}", callback_data=f"pick:{art['id']}"
        )
        del_btn = InlineKeyboardButton(
            "❌ Delete", callback_data=f"delete:{art['id']}"
        )
        save_btn = InlineKeyboardButton(
            "🌙 Save for Midnight", callback_data=f"midnight:{art['id']}"
        )
        buttons.append([pick_btn, del_btn, save_btn])
    if picked or deleted or midnight:
        buttons.append([
            InlineKeyboardButton("⏮️ Do Over", callback_data="redo_all")
        ])
    return InlineKeyboardMarkup(buttons)

def clear_panel_jobs(context, keep=None):
    # keep: list of job keys to keep, e.g. ['countdown_job']
    all_job_keys = ["auto_confirm_job", "countdown_job", "tweak_timer_job"]
    for key in all_job_keys:
        if keep and key in keep:
            continue
        job = context.chat_data.get(key)
        if job:
            try: job.schedule_removal()
            except: pass
            del context.chat_data[key]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_panel_jobs(context)
    articles = load_articles()
    context.chat_data.clear()
    context.chat_data["articles"] = articles
    context.chat_data["picked"] = {}
    context.chat_data["deleted"] = []
    context.chat_data["midnight"] = []
    await show_confirm_panel(update.effective_chat.id, context, articles, AUTO_CONFIRM_TIME, reset_message=True)

async def show_confirm_panel(chat_id, context, articles, countdown, reset_message=False):
    # When opening confirm panel, kill only tweak count jobs, keep any home panel jobs alive
    clear_panel_jobs(context, keep=['countdown_job','auto_confirm_job'])

    context.chat_data["main_countdown"] = countdown

    text = f"**Current ranking:**\n{original_ranking_text(articles)}\n\n" \
           f"✅ Confirm auto-sends in {countdown}s..."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_original"),
         InlineKeyboardButton("🔄 Tweak Ranking", callback_data="start_tweak")]
    ])

    # Detect whether to edit or send (on inactivity, may want to create new message)
    if not reset_message and "active_message_id" in context.chat_data:
        msg_id = context.chat_data["active_message_id"]
        try:
            msg = await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=text, reply_markup=keyboard, parse_mode="Markdown"
            )
            # Store to ensure active id stays
            context.chat_data["active_message_id"] = msg_id
        except Exception:
            # Fallback: send new message and track id
            msg = await context.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="Markdown"
            )
            context.chat_data["active_message_id"] = msg.message_id
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="Markdown"
        )
        context.chat_data["active_message_id"] = msg.message_id

    msg_id = context.chat_data["active_message_id"]

    # Cancel old home panel jobs (if any), then create fresh ones for auto-confirm and countdown
    jq = context.application.job_queue

    if "countdown_job" in context.chat_data:
        old_job = context.chat_data["countdown_job"]
        try: old_job.schedule_removal()
        except: pass
        del context.chat_data["countdown_job"]
    countdown_job = jq.run_repeating(update_main_countdown, interval=1, first=1, chat_id=chat_id)
    context.chat_data["countdown_job"] = countdown_job

    if "auto_confirm_job" in context.chat_data:
        old_job = context.chat_data["auto_confirm_job"]
        try: old_job.schedule_removal()
        except: pass
        del context.chat_data["auto_confirm_job"]
    job = jq.run_once(main_auto_confirm, countdown, chat_id=chat_id)
    context.chat_data["auto_confirm_job"] = job

async def update_main_countdown(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    if "main_countdown" not in context.chat_data:
        context.chat_data["main_countdown"] = AUTO_CONFIRM_TIME
    countdown = context.chat_data["main_countdown"]
    countdown -= 1
    context.chat_data["main_countdown"] = countdown
    articles = context.chat_data.get("articles", load_articles())
    text = f"**Current ranking:**\n{original_ranking_text(articles)}\n\n" \
           f"✅ Confirm auto-sends in {countdown}s..."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_original"),
         InlineKeyboardButton("🔄 Tweak Ranking", callback_data="start_tweak")]
    ])
    msg_id = context.chat_data.get("active_message_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=msg_id,
                reply_markup=keyboard, parse_mode="Markdown"
            )
        except Exception:
            pass
    if countdown <= 0:
        # No need to do anything, auto-confirm will fire

        # Optionally, cancel the countdown job to prevent further firing
        if "countdown_job" in context.chat_data:
            job = context.chat_data["countdown_job"]
            try: job.schedule_removal()
            except: pass
            del context.chat_data["countdown_job"]

async def main_auto_confirm(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    articles = context.chat_data.get("articles", load_articles())
    save_final_result([a["id"] for a in articles], [], [], articles)
    msg_id = context.chat_data.get("active_message_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"Ranking auto-confirmed and exported! ✅",
                reply_markup=None
            )
        except Exception:
            pass
    clear_panel_jobs(context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id
    articles = context.chat_data.get("articles", load_articles())
    msg_id = context.chat_data.get("active_message_id")
    if not msg_id:
        return  # Safety

    # On any button press, kill jobs from the *opposite* panel only
    if data in ["start_tweak", "redo_all"]:
        clear_panel_jobs(context, keep=['tweak_timer_job'])
    elif data in ["confirm_original"]:
        clear_panel_jobs(context, keep=['countdown_job','auto_confirm_job'])
    # For tweaks, keep tweak inactivity job running

    if data == "confirm_original":
        save_final_result([a["id"] for a in articles], [], [], articles)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text="Ranking confirmed and exported! ✅",
                reply_markup=None
            )
        except Exception:
            pass
        clear_panel_jobs(context)
        return

    if data == "start_tweak" or data == "redo_all":
        context.chat_data["picked"] = {}
        context.chat_data["deleted"] = []
        context.chat_data["midnight"] = []
        await begin_tweaking(chat_id, context, articles, msg_id)
        return

    if data.startswith(("pick:","delete:","midnight:")):
        cmd, aid = data.split(":")
        aid = int(aid)
        picked = context.chat_data.get("picked", {})
        deleted = context.chat_data.get("deleted", [])
        midnight = context.chat_data.get("midnight", [])
        if cmd == "pick" and aid not in picked:
            picked[aid] = len(picked) + 1
        elif cmd == "delete" and aid not in deleted:
            deleted.append(aid)
        elif cmd == "midnight" and aid not in midnight:
            midnight.append(aid)
        context.chat_data["picked"] = picked
        context.chat_data["deleted"] = deleted
        context.chat_data["midnight"] = midnight

        reset_tweak_timer(chat_id, context)

        all_handled = (len(picked) + len(deleted) + len(midnight)) == len(articles)
        if all_handled:
            final_text = final_confirm_text(articles, picked, deleted, midnight)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm", callback_data="confirm_tweak"),
                 InlineKeyboardButton("🔄 Do-over", callback_data="redo_all")]
            ])
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=final_text, reply_markup=keyboard, parse_mode="Markdown"
                )
            except Exception:
                pass
            cancel_tweak_timer(context)
            return
        else:
            text = tweaking_status_text(articles, picked, deleted, midnight) + "\n\n(Inactivity will reset this panel after 15 seconds)"
            keyboard = build_tweak_keyboard(articles, picked, deleted, midnight)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=text, reply_markup=keyboard, parse_mode="Markdown"
                )
            except Exception:
                pass
            return

    if data == "confirm_tweak":
        picked = context.chat_data.get("picked", {})
        deleted = context.chat_data.get("deleted", [])
        midnight = context.chat_data.get("midnight", [])
        save_final_result(
            [k for k in sorted(picked, key=lambda x: picked[x])], deleted, midnight, articles
        )
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text="Tweaked ranking confirmed and exported! ✅",
                reply_markup=None
            )
        except Exception:
            pass
        clear_panel_jobs(context)
        return

async def begin_tweaking(chat_id, context, articles, msg_id):
    clear_panel_jobs(context, keep=['tweak_timer_job'])
    picked = context.chat_data.get("picked", {})
    deleted = context.chat_data.get("deleted", [])
    midnight = context.chat_data.get("midnight", [])
    text = tweaking_status_text(articles, picked, deleted, midnight) + "\n\n(Inactivity will reset this panel after 15 seconds)"
    keyboard = build_tweak_keyboard(articles, picked, deleted, midnight)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=text, reply_markup=keyboard, parse_mode="Markdown"
        )
    except Exception:
        pass
    reset_tweak_timer(chat_id, context)

def reset_tweak_timer(chat_id, context):
    cancel_tweak_timer(context)
    jq = context.application.job_queue
    job = jq.run_once(tweak_inactivity_timeout, USER_INACTIVITY_TIME, chat_id=chat_id)
    context.chat_data["tweak_timer_job"] = job

def cancel_tweak_timer(context):
    job = context.chat_data.get("tweak_timer_job")
    if job:
        try: job.schedule_removal()
        except Exception: pass
        del context.chat_data["tweak_timer_job"]

async def tweak_inactivity_timeout(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    msg_id = context.chat_data.get("active_message_id")
    articles = context.chat_data.get("articles", load_articles())
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text="Tweak canceled due to inactivity. Resetting...",
            reply_markup=None
        )
    except Exception:
        pass

    context.chat_data["picked"] = {}
    context.chat_data["deleted"] = []
    context.chat_data["midnight"] = []
    await show_confirm_panel(chat_id, context, articles, AUTO_CONFIRM_TIME)

def main():
    app = ApplicationBuilder().token('7645636529:AAH_XBlWYAcpqP1kmZThWbvHJsfSY_K5tf8').build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
