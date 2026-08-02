import os
import asyncio
from telegram import Bot

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

async def send_test():
    bot = Bot(token=TOKEN)
    
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "🚨 BTC Scalper AI TEST\n\n"
            "✅ Collegamento Telegram riuscito\n"
            "📊 Sistema pronto per sviluppo segnali BTCUSD\n"
        )
    )

if __name__ == "__main__":
    asyncio.run(send_test())
