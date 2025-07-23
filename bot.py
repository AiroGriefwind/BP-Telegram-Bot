import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)

ARTICLES_FILE = "articles.json"
RESULT_FILE = "confirmed_ranking.json"

def load_articles():
    with open(ARTICLES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_final_ranking(id_order, articles):
    ordered = []
    id_to_article = {a["id"]: a for a in articles}
    for idx in id_order:
        ordered.append(id_to_article[idx])
    # Save to file
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)

def original_ranking_text(articles):
    lines = [f"{i+1}. {a['title']}" for i, a in enumerate(articles)]
    return "\n".join(lines)

def build_keyboard(articles, picked):
    # picked: dict mapping article id to its chosen rank, e.g. {2:1, 4:2, ...}
    buttons = []
    next_pick = len(picked) + 1
    for art in articles:
        if art["id"] in picked:
            btn = InlineKeyboardButton(
                f"#{picked[art['id']]}: {art['title']}",
                callback_data=f"noop:{art['id']}"
            )
        else:
            btn = InlineKeyboardButton(
                f"{art['title']} (Pick as #{next_pick})",
                callback_data=f"pick:{art['id']}"
            )
        buttons.append([btn])  # One button per row
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    articles = load_articles()
    context.user_data["articles"] = articles
    context.user_data["picked"] = {}
    # Show current ranking and prompt for action
    text = "**Current ranking:**\n" + original_ranking_text(articles)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_original"),
         InlineKeyboardButton("🔄 Tweak Ranking", callback_data="start_tweak")]
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    articles = context.user_data.get("articles", load_articles())

    if data == "confirm_original":
        # Confirm original order
        save_final_ranking([a["id"] for a in articles], articles)
        await query.edit_message_text("Ranking confirmed and exported! ✅")
        return

    if data == "start_tweak":
        # Start tweak mode: reset picks, show picking panel
        context.user_data["picked"] = {}
        await query.edit_message_text(
            "Tap the articles in your desired order, one by one:",
            reply_markup=build_keyboard(articles, {})
        )
        return

    if data.startswith("pick:"):
        art_id = int(data.split(":")[1])
        picked = context.user_data.get("picked", {})
        if art_id in picked:
            # Already picked, ignore
            return

        picked[art_id] = len(picked) + 1
        context.user_data["picked"] = picked
        # If all are picked, show final ranking & ask for confirmation
        if len(picked) == len(articles):
            order = sorted(picked, key=lambda k: picked[k])
            titles = [a["title"] for i in order for a in articles if a["id"] == i]
            final = "\n".join(f"{i+1}. {title}" for i, title in enumerate(titles))
            final_ids = order
            # Save to JSON file
            save_final_ranking(final_ids, articles)
            await query.edit_message_text(
                f"Your final ranking:\n{final}\n\nRanking confirmed and exported! ✅"
            )
        else:
            # Show updated keyboard after picking
            await query.edit_message_reply_markup(
                reply_markup=build_keyboard(articles, picked)
            )

    elif data.startswith("noop"):
        # button was already picked; do nothing
        pass

def main():
    app = ApplicationBuilder().token('7645636529:AAH_XBlWYAcpqP1kmZThWbvHJsfSY_K5tf8').build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
