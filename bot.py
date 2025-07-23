import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)

ARTICLES_FILE = "articles.json"
RESULT_FILE = "confirmed_ranking.json"

AUTO_CONFIRM_TIME = 30        # seconds for test
USER_INACTIVITY_TIME = 15     # seconds for test

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

def clear_jobs_and_pointers(context):
    for key in ["auto_confirm_job", "countdown_job", "tweak_timer_job"]:
        job = context.chat_data.get(key)
        if job:
            try:
                job.schedule_removal()
            except Exception:
                pass
            del context.chat_data[key]
    for key in ["panel_message_id", "tweaking_message_id", "last_confirm_message_id"]:
        if key in context.chat_data:
            del context.chat_data[key]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_jobs_and_pointers(context)
    articles = load_articles()
    context.user_data.clear()
    context.user_data["articles"] = articles
    context.user_data["picked"] = {}
    context.user_data["deleted"] = []
    context.user_data["midnight"] = []
    await show_confirm_panel(update.effective_chat.id, context, articles, AUTO_CONFIRM_TIME)

async def show_confirm_panel(chat_id, context, articles, countdown):
    clear_jobs_and_pointers(context)
    context.chat_data["main_countdown"] = countdown
    text = f"**Current ranking:**\n{original_ranking_text(articles)}\n\n" \
           f"✅ Confirm auto-sends in {countdown}s..."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_original"),
         InlineKeyboardButton("🔄 Tweak Ranking", callback_data="start_tweak")]
    ])
    msg = await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="Markdown"
    )
    context.chat_data["panel_message_id"] = msg.message_id
    jq = context.application.job_queue
    countdown_job = jq.run_repeating(update_main_countdown,
                                     interval=1, first=1,
                                     chat_id=chat_id)
    context.chat_data["countdown_job"] = countdown_job
    job = jq.run_once(main_auto_confirm, countdown, chat_id=chat_id)
    context.chat_data["auto_confirm_job"] = job

async def update_main_countdown(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    countdown = context.chat_data.get("main_countdown", AUTO_CONFIRM_TIME)
    countdown -= 1
    context.chat_data["main_countdown"] = countdown
    articles = context.chat_data.get("articles", load_articles())
    text = f"**Current ranking:**\n{original_ranking_text(articles)}\n\n" \
           f"✅ Confirm auto-sends in {countdown}s..."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_original"),
         InlineKeyboardButton("🔄 Tweak Ranking", callback_data="start_tweak")]
    ])
    msg_id = context.chat_data.get("panel_message_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=msg_id, reply_markup=keyboard, parse_mode="Markdown"
            )
        except Exception:
            pass

async def main_auto_confirm(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    articles = context.chat_data.get("articles", load_articles())
    save_final_result([a["id"] for a in articles], [], [], articles)
    msg_id = context.chat_data.get("panel_message_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"Ranking auto-confirmed and exported! ✅",
                reply_markup=None
            )
        except Exception:
            pass
    clear_jobs_and_pointers(context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id
    articles = context.chat_data.get("articles", load_articles())
    # Clean up jobs/pointers for every interaction.
    clear_jobs_and_pointers(context)

    if data == "confirm_original":
        save_final_result([a["id"] for a in articles], [], [], articles)
        try:
            await query.edit_message_text("Ranking confirmed and exported! ✅")
        except Exception:
            pass
        clear_jobs_and_pointers(context)
        return
    if data == "start_tweak":
        context.user_data["picked"] = {}
        context.user_data["deleted"] = []
        context.user_data["midnight"] = []
        await begin_tweaking(chat_id, context, articles)
        return
    if data == "redo_all":
        context.user_data["picked"] = {}
        context.user_data["deleted"] = []
        context.user_data["midnight"] = []
        await begin_tweaking(chat_id, context, articles)
        return
    if data.startswith("pick:") or data.startswith("delete:") or data.startswith("midnight:"):
        cmd, aid = data.split(":")
        aid = int(aid)
        picked = context.user_data.get("picked", {})
        deleted = context.user_data.get("deleted", [])
        midnight = context.user_data.get("midnight", [])
        left = [a for a in articles if a["id"] not in picked and a["id"] not in deleted and a["id"] not in midnight]

        if cmd == "pick":
            if aid not in picked:
                picked[aid] = len(picked) + 1
        elif cmd == "delete":
            if aid not in deleted:
                deleted.append(aid)
        elif cmd == "midnight":
            if aid not in midnight:
                midnight.append(aid)

        context.user_data["picked"] = picked
        context.user_data["deleted"] = deleted
        context.user_data["midnight"] = midnight

        reset_tweak_timer(chat_id, context)

        # Now check if all are handled
        all_handled = (len(picked) + len(deleted) + len(midnight)) == len(articles)
        if all_handled:
            # Show summary and confirmation panel
            final_text = final_confirm_text(articles, picked, deleted, midnight)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm", callback_data="confirm_tweak"),
                 InlineKeyboardButton("🔄 Do-over", callback_data="redo_all")]
            ])
            msg = await context.bot.send_message(
                chat_id=chat_id, text=final_text, reply_markup=keyboard, parse_mode="Markdown"
            )
            context.chat_data["last_confirm_message_id"] = msg.message_id
            cancel_tweak_timer(context)
            return
        else:
            try:
                await query.edit_message_text(
                    tweaking_status_text(articles, picked, deleted, midnight),
                    reply_markup=build_tweak_keyboard(articles, picked, deleted, midnight),
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        return
    if data == "confirm_tweak":
        picked = context.user_data.get("picked", {})
        deleted = context.user_data.get("deleted", [])
        midnight = context.user_data.get("midnight", [])
        save_final_result(
            sorted(picked, key=lambda x: picked[x]), deleted, midnight, articles
        )
        msg_id = context.chat_data.get("last_confirm_message_id")
        if msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text="Tweaked ranking confirmed and exported! ✅",
                    reply_markup=None)
            except Exception:
                pass
        clear_jobs_and_pointers(context)
        return
    elif data.startswith("noop"):
        pass

async def begin_tweaking(chat_id, context, articles):
    clear_jobs_and_pointers(context)
    picked = context.user_data.get("picked", {})
    deleted = context.user_data.get("deleted", [])
    midnight = context.user_data.get("midnight", [])

    text = tweaking_status_text(articles, picked, deleted, midnight) + "\n\n(Inactivity will reset this panel after 15 seconds)"
    msg = await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=build_tweak_keyboard(articles, picked, deleted, midnight), parse_mode="Markdown"
    )
    context.chat_data["tweaking_message_id"] = msg.message_id
    reset_tweak_timer(chat_id, context)

def reset_tweak_timer(chat_id, context):
    cancel_tweak_timer(context)
    jq = context.application.job_queue
    job = jq.run_once(tweak_inactivity_timeout, USER_INACTIVITY_TIME, chat_id=chat_id)
    context.chat_data["tweak_timer_job"] = job

def cancel_tweak_timer(context):
    job = context.chat_data.get("tweak_timer_job")
    if job:
        try:
            job.schedule_removal()
        except Exception:
            pass
        del context.chat_data["tweak_timer_job"]

async def tweak_inactivity_timeout(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    articles = context.chat_data.get("articles", load_articles())
    msg_id = context.chat_data.get("tweaking_message_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text="Tweak canceled due to inactivity. Resetting...",
                reply_markup=None
            )
        except Exception:
            pass
    clear_jobs_and_pointers(context)
    await show_confirm_panel(chat_id, context, articles, AUTO_CONFIRM_TIME)

def main():
    app = ApplicationBuilder().token('7645636529:AAH_XBlWYAcpqP1kmZThWbvHJsfSY_K5tf8').build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
