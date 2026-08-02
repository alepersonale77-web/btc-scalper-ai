import os
import asyncio
from telegram import Bot

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


async def main():
    bot = Bot(token=TOKEN)

    print("🚀 BTC Scalper AI avviato")

    while True:
        # Qui inseriremo il motore segnali BTCUSD
        # Per ora il bot resta acceso senza inviare messaggi

        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
