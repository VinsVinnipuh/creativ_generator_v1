
---

## `bot/main.py`

```python
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Message, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


@dataclass
class UserBrief:
    texts: List[str] = field(default_factory=list)
    image_urls: List[str] = field(default_factory=list)

    def reset(self) -> None:
        self.texts.clear()
        self.image_urls.clear()


USER_DATA: Dict[int, UserBrief] = {}


def get_user_brief(user_id: int) -> UserBrief:
    if user_id not in USER_DATA:
        USER_DATA[user_id] = UserBrief()
    return USER_DATA[user_id]


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message is None:
        return

    await update.message.reply_text(
        "Привіт! Я допоможу згенерувати креативи для Instagram.\n\n"
        "1) Надішли текстовий бриф (можна кількома повідомленнями).\n"
        "2) Надішли фото-референси.\n"
        "3) Виконай /generate для отримання варіантів.\n\n"
        "Команди:\n"
        "/new — очистити поточний контекст\n"
        "/generate — згенерувати креативи"
    )


async def new_brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    brief = get_user_brief(user_id)
    brief.reset()

    await update.message.reply_text(
        "Готово ✅ Контекст очищено. Надішли новий бриф і референси."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    brief = get_user_brief(user_id)

    text = (update.message.text or "").strip()
    if not text:
        return

    brief.texts.append(text)
    await update.message.reply_text("Текст додано до брифу ✅")


async def _build_file_url(message: Message, context: ContextTypes.DEFAULT_TYPE) -> str:
    if not message.photo:
        raise ValueError("У повідомленні немає фото.")

    photo = message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)
    bot_token = get_required_env("TELEGRAM_BOT_TOKEN")

    return f"https://api.telegram.org/file/bot{bot_token}/{telegram_file.file_path}"


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    brief = get_user_brief(user_id)

    try:
        file_url = await _build_file_url(update.message, context)
    except Exception as exc:
        logger.exception("Failed to process photo")
        await update.message.reply_text(f"Не вдалося обробити фото: {exc}")
        return

    brief.image_urls.append(file_url)
    await update.message.reply_text("Фото-референс додано ✅")


def build_openai_prompt(brief: UserBrief, variants: int) -> str:
    text_block = (
        "\n".join(f"- {item}" for item in brief.texts)
        if brief.texts
        else "- (немає текстового брифу)"
    )

    return (
        "Ти senior creative strategist для Instagram. "
        "На базі брифу та візуальних референсів створи декілька варіантів креативів.\n\n"
        f"Кількість варіантів: {variants}.\n"
        "Вимоги до кожного варіанту:\n"
        "1) Назва ідеї\n"
        "2) Головний хук (1 речення)\n"
        "3) Текст на креативі (до 15 слів)\n"
        "4) Короткий caption для поста (до 300 символів)\n"
        "5) Візуальна концепція: композиція, кольори, стиль\n"
        "6) CTA\n"
        "7) Чому це спрацює для ЦА\n\n"
        "Бриф користувача:\n"
        f"{text_block}\n\n"
        "Відповідай українською мовою. Форматуй чітко, з заголовками."
    )


def generate_creatives(client: OpenAI, model: str, brief: UserBrief, variants: int) -> str:
    content: List[dict] = [
        {
            "type": "input_text",
            "text": build_openai_prompt(brief, variants),
        }
    ]

    for image_url in brief.image_urls:
        content.append(
            {
                "type": "input_image",
                "image_url": image_url,
            }
        )

    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
    )

    return response.output_text.strip()


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    brief = get_user_brief(user_id)

    if not brief.texts and not brief.image_urls:
        await update.message.reply_text(
            "Поки немає даних для генерації. Надішли текст брифу та/або фото-референси."
        )
        return

    variants = int(os.getenv("CREATIVE_VARIANTS", "3"))
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    try:
        client = OpenAI(api_key=get_required_env("OPENAI_API_KEY"))
        await update.message.reply_text("Генерую креативи... ⏳")
        result = generate_creatives(client, model, brief, variants)
    except Exception as exc:
        logger.exception("Failed to generate creatives")
        await update.message.reply_text(f"Сталася помилка при генерації: {exc}")
        return

    if not result:
        await update.message.reply_text(
            "Модель повернула порожню відповідь. Спробуй уточнити бриф."
        )
        return

    max_chunk = 3500
    for i in range(0, len(result), max_chunk):
        await update.message.reply_text(result[i:i + max_chunk])


def main() -> None:
    load_dotenv()

    telegram_token = get_required_env("TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(telegram_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new", new_brief))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running...")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()