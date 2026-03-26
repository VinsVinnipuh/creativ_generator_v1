import logging
import os
import re
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
    generated_variants: List[str] = field(default_factory=list)
    approved_variants: List[str] = field(default_factory=list)

    def reset(self) -> None:
        self.texts.clear()
        self.image_urls.clear()
        self.generated_variants.clear()
        self.approved_variants.clear()


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
        "1) Надішли текстовий бриф.\n"
        "2) Надішли фото-референси.\n"
        "3) Виконай /generate.\n\n"
        "Команди:\n"
        "/new — очистити контекст\n"
        "/generate — згенерувати креативи\n"
        "/approve N — затвердити варіант (наприклад /approve 1)\n"
        "/carousel — зібрати карусель із затверджених креативів"
    )


async def new_brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.effective_user is None or update.message is None:
        return

    brief = get_user_brief(update.effective_user.id)
    brief.reset()
    await update.message.reply_text("Готово ✅ Контекст очищено.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.effective_user is None or update.message is None:
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    brief = get_user_brief(update.effective_user.id)
    brief.texts.append(text)
    await update.message.reply_text("Текст додано до брифу ✅")


async def _build_file_url(message: Message, context: ContextTypes.DEFAULT_TYPE) -> str:
    if not message.photo:
        raise ValueError("У повідомленні немає фото.")

    photo = message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)
    bot_token = get_required_env("TELEGRAM_BOT_TOKEN")
    file_path = telegram_file.file_path

    if not file_path:
        raise ValueError("Telegram не повернув file_path.")

    return f"https://api.telegram.org/file/bot{bot_token}/{file_path}"


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    brief = get_user_brief(update.effective_user.id)

    try:
        file_url = await _build_file_url(update.message, context)
        logger.info("Saved image URL: %s", file_url)
        brief.image_urls.append(file_url)
    except Exception as exc:
        logger.exception("Failed to process photo")
        await update.message.reply_text(f"Не вдалося обробити фото: {exc}")
        return

    await update.message.reply_text("Фото-референс додано ✅")


def build_openai_prompt(brief: UserBrief, variants: int) -> str:
    text_block = "\n".join(f"- {item}" for item in brief.texts) if brief.texts else "- (немає текстового брифу)"
    return (
        "Ти senior creative strategist для Instagram. "
        "На базі брифу та візуальних референсів створи декілька варіантів креативів.\n\n"
        f"Кількість варіантів: {variants}.\n"
        "Для кожного варіанту дай:\n"
        "1) Назва ідеї\n"
        "2) Головний хук\n"
        "3) Текст на креативі (до 15 слів)\n"
        "4) Короткий caption\n"
        "5) Візуальна концепція\n"
        "6) CTA\n"
        "7) Чому це спрацює\n\n"
        "Бриф користувача:\n"
        f"{text_block}\n\n"
        "Відповідай українською мовою."
    )


def generate_creatives(client: OpenAI, model: str, brief: UserBrief, variants: int) -> str:
    content = [
        {"type": "input_text", "text": build_openai_prompt(brief, variants)}
    ]

    for image_url in brief.image_urls:
        content.append(
            {
                "type": "input_image",
                "image_url": image_url,
            }
        )

    logger.info("Sending image URLs to OpenAI: %s", brief.image_urls)

    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
    )

    return response.output_text.strip()


def split_variants(raw_text: str) -> List[str]:
    chunks = [item.strip() for item in re.split(r"\n(?=\d+[.)]\s)", raw_text) if item.strip()]
    if len(chunks) <= 1:
        chunks = [item.strip() for item in re.split(r"\n\s*\n", raw_text) if item.strip()]
    return chunks if chunks else [raw_text.strip()]


def generate_carousel(client: OpenAI, model: str, brief: UserBrief) -> str:
    approved = "\n\n".join(f"Креатив {i + 1}:\n{item}" for i, item in enumerate(brief.approved_variants))
    prompt = (
        "Створи Instagram-карусель на базі затверджених креативів.\n"
        "Дай 5-8 слайдів. Для кожного слайду вкажи:\n"
        "1) Заголовок слайду\n"
        "2) Текст на слайді (коротко)\n"
        "3) Ідею візуалу (композиція/акцент)\n"
        "4) Примітку для дизайнера\n\n"
        "Затверджені креативи:\n"
        f"{approved}\n\n"
        "Відповідай українською мовою."
    )

    content = [{"type": "input_text", "text": prompt}]
    for image_url in brief.image_urls:
        content.append({"type": "input_image", "image_url": image_url})

    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
    )
    return response.output_text.strip()


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.effective_user is None or update.message is None:
        return

    brief = get_user_brief(update.effective_user.id)

    if not brief.texts and not brief.image_urls:
        await update.message.reply_text(
            "Поки немає даних для генерації. Надішли текст і/або фото."
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
        await update.message.reply_text("Модель повернула порожню відповідь.")
        return

    brief.generated_variants = split_variants(result)
    brief.approved_variants.clear()

    await update.message.reply_text(
        "Готово ✅ Щоб затвердити варіант, надішли /approve N (наприклад /approve 1)."
    )

    max_chunk = 3500
    for i in range(0, len(result), max_chunk):
        await update.message.reply_text(result[i:i + max_chunk])


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    brief = get_user_brief(update.effective_user.id)
    if not brief.generated_variants:
        await update.message.reply_text("Немає згенерованих варіантів. Спочатку виконай /generate.")
        return

    if not context.args:
        await update.message.reply_text("Вкажи номер: /approve 1")
        return

    arg = context.args[0].lower()
    if arg == "all":
        brief.approved_variants = list(brief.generated_variants)
        await update.message.reply_text(f"Затверджено всі варіанти ({len(brief.approved_variants)}) ✅")
        return

    if not arg.isdigit():
        await update.message.reply_text("Невірний формат. Використай /approve N або /approve all.")
        return

    index = int(arg) - 1
    if index < 0 or index >= len(brief.generated_variants):
        await update.message.reply_text(f"Номер поза діапазоном 1..{len(brief.generated_variants)}.")
        return

    selected = brief.generated_variants[index]
    if selected not in brief.approved_variants:
        brief.approved_variants.append(selected)
    await update.message.reply_text(
        f"Варіант {index + 1} затверджено ✅. Тепер можна викликати /carousel."
    )


async def carousel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.effective_user is None or update.message is None:
        return

    brief = get_user_brief(update.effective_user.id)
    if not brief.approved_variants:
        await update.message.reply_text("Немає затверджених креативів. Використай /approve N.")
        return

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    try:
        client = OpenAI(api_key=get_required_env("OPENAI_API_KEY"))
        await update.message.reply_text("Генерую карусель із затверджених креативів... ⏳")
        result = generate_carousel(client, model, brief)
    except Exception as exc:
        logger.exception("Failed to generate carousel")
        await update.message.reply_text(f"Сталася помилка при генерації каруселі: {exc}")
        return

    if not result:
        await update.message.reply_text("Модель повернула порожню відповідь для каруселі.")
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
    application.add_handler(CommandHandler("approve", approve))
    application.add_handler(CommandHandler("carousel", carousel))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running...")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
