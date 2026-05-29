from dotenv import load_dotenv
load_dotenv()
import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart


API_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL_SECONDS = 60
USD_XML_URL = "https://www.cbr-xml-daily.ru/daily.xml"

logging.basicConfig(level=logging.INFO)


@dataclass
class ChatSettings:
    lower: float
    upper: float
    last_state: str = "normal"  # normal | low | high


chat_settings: Dict[int, ChatSettings] = {}


def get_usd_rate() -> float:
    response = requests.get(USD_XML_URL, timeout=10)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    for valute in root.findall("Valute"):
        char_code = valute.findtext("CharCode")
        if char_code == "USD":
            value = valute.findtext("Value")
            if value is None:
                raise ValueError("Не найдено значение курса USD.")
            return float(value.replace(",", "."))

    raise ValueError("USD не найден в ответе ЦБ РФ.")


def define_state(rate: float, lower: float, upper: float) -> str:
    if rate < lower:
        return "low"
    if rate > upper:
        return "high"
    return "normal"


if not API_TOKEN:
    raise RuntimeError("Не задан токен бота. Установите переменную окружения BOT_TOKEN.")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def process_start_command(message: types.Message):
    text = (
        "Привет! Я отслеживаю курс доллара (USD) по данным ЦБ РФ.\n\n"
        "Команды:\n"
        "/set <нижняя> <верхняя> — установить границы, например: /set 85 95\n"
        "/bounds — показать текущие границы\n"
        "/rate — показать текущий курс\n"
        "/check — выполнить проверку прямо сейчас"
    )
    await message.answer(text)


@dp.message(Command("help"))
async def process_help_command(message: types.Message):
    await process_start_command(message)


@dp.message(Command("set"))
async def process_set_command(message: types.Message):
    if not message.text:
        await message.answer("Формат: /set <нижняя> <верхняя>\nПример: /set 85 95")
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /set <нижняя> <верхняя>\nПример: /set 85 95")
        return

    try:
        lower = float(parts[1].replace(",", "."))
        upper = float(parts[2].replace(",", "."))
    except ValueError:
        await message.answer("Границы должны быть числами. Пример: /set 85 95")
        return

    if lower >= upper:
        await message.answer("Нижняя граница должна быть меньше верхней.")
        return

    chat_settings[message.chat.id] = ChatSettings(lower=lower, upper=upper)
    await message.answer(f"Границы установлены: нижняя = {lower:.2f}, верхняя = {upper:.2f}")


@dp.message(Command("bounds"))
async def process_bounds_command(message: types.Message):
    settings = chat_settings.get(message.chat.id)
    if settings is None:
        await message.answer("Границы ещё не заданы. Используйте: /set <нижняя> <верхняя>")
        return

    await message.answer(
        f"Текущие границы:\nнижняя = {settings.lower:.2f}\nверхняя = {settings.upper:.2f}"
    )


@dp.message(Command("rate"))
async def process_rate_command(message: types.Message):
    try:
        rate = get_usd_rate()
    except Exception as e:
        logging.exception("Ошибка получения курса USD")
        await message.answer(f"Не удалось получить курс USD: {e}")
        return

    await message.answer(f"Текущий курс USD: {rate:.2f} ₽")


@dp.message(Command("check"))
async def process_check_command(message: types.Message):
    settings = chat_settings.get(message.chat.id)
    if settings is None:
        await message.answer("Сначала задайте границы командой: /set <нижняя> <верхняя>")
        return

    try:
        rate = get_usd_rate()
    except Exception as e:
        logging.exception("Ошибка проверки курса USD")
        await message.answer(f"Не удалось проверить курс USD: {e}")
        return

    state = define_state(rate, settings.lower, settings.upper)
    if state == "low":
        await message.answer(
            f"⚠️ Курс USD ниже нижней границы!\nТекущий: {rate:.2f} ₽\nНижняя граница: {settings.lower:.2f}"
        )
    elif state == "high":
        await message.answer(
            f"⚠️ Курс USD выше верхней границы!\nТекущий: {rate:.2f} ₽\nВерхняя граница: {settings.upper:.2f}"
        )
    else:
        await message.answer(f"✅ Курс USD в пределах границ: {rate:.2f} ₽")


async def monitor_usd_rate():
    await asyncio.sleep(2)
    while True:
        if chat_settings:
            try:
                rate = get_usd_rate()
                for chat_id, settings in chat_settings.items():
                    current_state = define_state(rate, settings.lower, settings.upper)

                    if current_state != "normal" and current_state != settings.last_state:
                        if current_state == "low":
                            text = (
                                "⚠️ Курс USD ниже нижней границы!\n"
                                f"Текущий: {rate:.2f} ₽\n"
                                f"Нижняя граница: {settings.lower:.2f}"
                            )
                        else:
                            text = (
                                "⚠️ Курс USD выше верхней границы!\n"
                                f"Текущий: {rate:.2f} ₽\n"
                                f"Верхняя граница: {settings.upper:.2f}"
                            )
                        await bot.send_message(chat_id, text)

                    if current_state == "normal" and settings.last_state != "normal":
                        await bot.send_message(chat_id, f"✅ Курс USD снова в пределах: {rate:.2f} ₽")

                    settings.last_state = current_state
            except Exception:
                logging.exception("Ошибка в фоновом мониторинге курса USD")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def on_startup(bot: Bot):
    asyncio.create_task(monitor_usd_rate())
    logging.info("Бот запущен и мониторинг курса активен.")


async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
