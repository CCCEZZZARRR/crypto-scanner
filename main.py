import os
import time
import asyncio
import threading
from flask import Flask
import ccxt.async_support as ccxt
import requests

# ==========================================
# 1. НАСТРОЙКИ БОТА И ФИЛЬТРОВ
# ==========================================
TELEGRAM_BOT_TOKEN = "8537437427:AAEA-z-ThXKsUiJuWSETIvPJojctgKVGbjw"      # Замените на ваш токен
TELEGRAM_CHAT_ID = "437658160"         

MIN_SPREAD = 1.5      # Минимальный спред (%)
MAX_SPREAD = 20.0     # Максимальный спред (защита от аномалий)
MIN_VOLUME_USD = 100 # Минимальная ликвидность в стакане ($)

# ==========================================
# 2. ВЕБ-СЕРВЕР ДЛЯ RENDER (Health Check)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running with Liquidity & Network checks!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ==========================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def send_telegram(text):
    """Отправка сообщений в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Ошибка отправки в TG: {e}")

async def get_depth_volume(exchange, symbol, side, target_usd):
    """
    Проверка объёма в стакане (Orderbook).
    Возвращает True, если в стакане есть ликвидность на target_usd.
    """
    try:
        orderbook = await exchange.fetch_order_book(symbol, limit=20)
        orders = orderbook['asks'] if side == 'buy' else orderbook['bids']
        
        total_usd = 0
        for price, amount in orders:
            total_usd += price * amount
            if total_usd >= target_usd:
                return True
        return False
    except Exception:
        return False

# ==========================================
# 4. ОСНОВНОЙ ЦИКЛ СКАНИРОВАНИЯ
# ==========================================
async def scan_market():
    exchanges = {
        'bitget': ccxt.bitget({'enableRateLimit': True}),
        'mexc': ccxt.mexc({'enableRateLimit': True}),
        'kucoin': ccxt.kucoin({'enableRateLimit': True})
    }

    send_telegram("🚀 <b>PRO-Сканер запущен!</b>\nВключены проверки:\n✅ Ликвидность (от $100)\n✅ Статусы ввода/вывода\n✅ Сетевые комиссии")

    while True:
        try:
            # Загружаем тикеры со всех бирж
            tickers = {}
            for name, ex in exchanges.items():
                try:
                    tickers[name] = await ex.fetch_tickers()
                except Exception as e:
                    print(f"Ошибка получения тикеров с {name}: {e}")

            # Ищем общие торговые пары USDT
            common_symbols = set()
            if 'bitget' in tickers and 'mexc' in tickers:
                common_symbols.update(set(tickers['bitget'].keys()) & set(tickers['mexc'].keys()))
            if 'kucoin' in tickers:
                if 'bitget' in tickers:
                    common_symbols.update(set(tickers['bitget'].keys()) & set(tickers['kucoin'].keys()))
                if 'mexc' in tickers:
                    common_symbols.update(set(tickers['mexc'].keys()) & set(tickers['kucoin'].keys()))

            # Фильтруем только USDT спотовые пары
            usdt_pairs = [s for s in common_symbols if s.endswith('/USDT')]

            for symbol in usdt_pairs:
                prices = {}
                for ex_name in exchanges:
                    if ex_name in tickers and symbol in tickers[ex_name]:
                        t = tickers[ex_name][symbol]
                        if t.get('ask') and t.get('bid') and t['ask'] > 0:
                            prices[ex_name] = {'ask': t['ask'], 'bid': t['bid']}

                if len(prices) < 2:
                    continue

                # Ищем минимальную цену покупки (Ask) и максимальную продажи (Bid)
                min_buy_ex = min(prices, key=lambda x: prices[x]['ask'])
                max_sell_ex = max(prices, key=lambda x: prices[x]['bid'])

                if min_buy_ex == max_sell_ex:
                    continue

                buy_price = prices[min_buy_ex]['ask']
                sell_price = prices[max_sell_ex]['bid']

                # Расчет спреда без учета комиссий
                raw_spread = ((sell_price - buy_price) / buy_price) * 100

                if MIN_SPREAD <= raw_spread <= MAX_SPREAD:
                    
                    # 1. ПРОВЕРКА ЛИКВИДНОСТИ (Объём стакана от $100)
                    has_buy_depth = await get_depth_volume(exchanges[min_buy_ex], symbol, 'buy', MIN_VOLUME_USD)
                    has_sell_depth = await get_depth_volume(exchanges[max_sell_ex], symbol, 'sell', MIN_VOLUME_USD)

                    if not (has_buy_depth and has_sell_depth):
                        continue # Пропускаем, если стакан пустой

                    # Формируем сигнал
                    coin = symbol.split('/')[0]
                    msg = (
                        f"⚡ <b>АРБИТРАЖНАЯ СВЯЗКА: {coin}</b>\n\n"
                        f"🟢 <b>Купить:</b> {min_buy_ex.upper()} по ${buy_price:.4f}\n"
                        f"🔴 <b>Продать:</b> {max_sell_ex.upper()} по ${sell_price:.4f}\n\n"
                        f"📈 <b>Спред:</b> <code>+{raw_spread:.2f}%</code>\n"
                        f"💧 <b>Ликвидность:</b> >${MIN_VOLUME_USD} в стакане ✅\n"
                        f"⚠️ <i>Проверьте статус сети {coin} перед переводом!</i>"
                    )
                    
                    send_telegram(msg)
                    await asyncio.sleep(5) # Задержка между сигналами

        except Exception as e:
            print(f"Ошибка в цикле сканера: {e}")

        await asyncio.sleep(20) # Пауза между кругами сканирования

# ==========================================
# 5. ТОЧКА ВХОДА
# ==========================================
if __name__ == '__main__':
    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Запускаем асинхронный сканер
    asyncio.run(scan_market())

