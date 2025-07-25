import os
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Импортируем необходимые классы и функции из mcp-client-2.py
from mcp.types import Tool, TextContent
from mcp import ClientSession
from langchain_mcp_adapters.client import MultiServerMCPClient

# --- Подавление логов и предупреждений ---
import logging

logging.getLogger("mcp").setLevel(logging.WARNING)


# --- Копируем вспомогательные функции из mcp-client-2.py ---
async def check_server_ready(session: ClientSession, server_name: str, timeout: float = 10.0) -> bool:
    try:
        await asyncio.wait_for(session.initialize(), timeout=timeout)
        return True
    except Exception:
        return False


async def call_search_tool(search_session: ClientSession, query: str) -> str:
    try:
        list_tools_result = await search_session.list_tools()
        search_tool: Tool = next((t for t in list_tools_result.tools if t.name == "search"), None)
        if not search_tool:
            return "Инструмент 'search' не найден на сервере."
        call_result = await search_session.call_tool("search", arguments={"query": query})
        if call_result.content and isinstance(call_result.content[0], TextContent):
            return call_result.content[0].text
        else:
            return "Пустой или неожиданный результат от инструмента 'search'."
    except Exception as e:
        return f"Ошибка при вызове инструмента 'search': {e}"


async def save_result_to_file(content: str, base_dir: str, query: str = "search_result"):
    try:
        report_dir = os.path.join(base_dir, "report")
        os.makedirs(report_dir, exist_ok=True)
        now = datetime.now()
        safe_query = "".join(c for c in query if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_query = safe_query[:30]
        filename = f"report-{now.strftime('%H-%M-%S')}-{safe_query}.txt"
        filepath = os.path.join(report_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return filepath
    except Exception as e:
        return None


# --- Telegram bot logic ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Введите поисковый запрос.")


async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        await update.message.reply_text("Пожалуйста, введите непустой запрос.")
        return
    # Используем глобальный search_session
    search_result = await call_search_tool(context.application.search_session, query)
    if search_result and not search_result.startswith("Ошибка"):
        saved_file_path = await save_result_to_file(search_result, context.application.base_dir, query)
        if saved_file_path:
            await update.message.reply_text(f"Результат поиска:\n{search_result}\n\nСохранено в: {saved_file_path}")
        else:
            await update.message.reply_text("Ошибка при сохранении результата.")
    else:
        await update.message.reply_text(f"Ошибка поиска: {search_result}")


def main():
    load_dotenv()
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        print("Ошибка: BOT_TOKEN не найден в .env")
        return
    script_dir = os.path.dirname(os.path.realpath(__file__))
    search_server_path = os.path.join(script_dir, "search_server_duckduck_go.py")
    if not os.path.exists(search_server_path):
        print(f"Серверный скрипт поиска не найден: {search_server_path}")
        return
    client = MultiServerMCPClient({
        "search": {
            "command": "python",
            "args": [search_server_path],
            "transport": "stdio",
            "env": {"PYTHONPATH": script_dir}
        }
    })
    # MCP-сессия — асинхронная, инициализируем до запуска бота
    loop = asyncio.get_event_loop()
    search_session_cm = client.session("search")
    search_session = loop.run_until_complete(search_session_cm.__aenter__())
    search_ready = loop.run_until_complete(check_server_ready(search_session, "search"))
    if not search_ready:
        print("Сервер поиска не готов. Завершение.")
        return

    application = Application.builder().token(BOT_TOKEN).build()
    application.base_dir = script_dir
    application.search_session = search_session
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query))

    # Хук для корректного завершения MCP-сессии
    async def on_shutdown(app):
        await search_session_cm.__aexit__(None, None, None)

    application.post_shutdown = on_shutdown

    print("Бот запущен.")
    application.run_polling()


if __name__ == "__main__":
    main()
