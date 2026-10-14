import os
import json
import time
import pathlib
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# ==========================================
# CONFIGURATION - BOT INTRADAY (1H)
# ==========================================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN_INTRADAY')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID_INTRADAY')

TIMEFRAME_ANALYSE = '1h'
CACHE_FILE = pathlib.Path("signals_cache.json")
CACHE_TTL_HOURS = 6  # Un signal identique n'est pas renvoyé avant 6h

# ==========================================
# CACHE ANTI-SPAM
# ==========================================

def load_cache():
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}

def save_cache(data):
    try:
        CACHE_FILE.write_text(json.dumps(data))
    except Exception as e:
        print(f"Erreur cache: {e}")

def should_send_signal(cache, symbol, direction, level, tol=0.005):
    """Retourne True si le signal est nouveau (ou expiré)."""
    key = f"{symbol}_{direction}"
    entry = cache.get(key)
    now_ts = time.time()
    if entry:
        last_ts = entry.get('ts', 0)
        last_level = entry.get('level', 0)
        age_h = (now_ts - last_ts) / 3600
        # Même direction, même niveau, encore frais → skip
        if age_h < CACHE_TTL_HOURS and last_level > 0 and abs(last_level - level) / last_level < tol:
            return False
    cache[key] = {'ts': now_ts, 'level': level}
    return True

# ==========================================
# DÉTECTION DE LA SESSION
# ==========================================

def get_current_session():
    now_utc = datetime.now(timezone.utc)
    total_min = now_utc.hour * 60 + now_utc.minute

    if total_min < 8*60 + 30:
        return {"name": "ASIATIQUE", "emoji": "🌏", "pivot": 5, "tol": 0.008,
                "vol": 10_000_000, "rr": 2.5, "max": 2,
                "desc": "Volume faible. On ne prend que les setups parfaits."}
    elif total_min < 13*60 + 30:
        return {"name": "LONDRES", "emoji": "🇬🇧", "pivot": 4, "tol": 0.012,
                "vol": 5_000_000, "rr": 2.0, "max": 4,
                "desc": "Volume moyen. Les tendances se dessinent."}
    elif total_min < 16*60 + 30:
        return {"name": "OVERLAP LONDRES/US", "emoji": "🔥", "pivot": 3, "tol": 0.015,
                "vol": 5_000_000, "rr": 2.0, "max": 5,
                "desc": "Meilleure liquidité de la journée. Les meilleurs mouvements se dessinent."}
    elif total_min < 21*60:
        return {"name": "US", "emoji": "🇺🇸", "pivot": 3, "tol": 0.015,
                "vol": 5_000_000, "rr": 2.5, "max": 5,
                "desc": "Forte volatilité. Mouvements explosifs. TP plus ambitieux."}
    else:
        return {"name": "NUIT", "emoji": "🌙", "pivot": 5, "tol": 0.005,
                "vol": 20_000_000, "rr": 3.0, "max": 1,
                "desc": "Volume très faible. On attend la session asiatique."}

# ==========================================
# TELEGRAM
# ==========================================

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Erreur : Identifiants Telegram Intraday manquants.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Découpe si > 4000 caractères
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    for chunk in chunks:
        payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': chunk, 'parse_mode': 'Markdown'}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Erreur Telegram : {e}")

# ==========================================
# DONNÉES
# ==========================================

def get_dynamic_altcoins(exchange, top_n=80):
    print("Récupération liste dynamique...")
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {'vs_currency': 'usd', 'order': 'market_cap_desc',
              'per_page': top_n, 'page': 1, 'sparkline': 'false'}
    try:
        response = requests.get(url, params=params, timeout=15)
        coins = response.json()
        if not isinstance(coins, list):
            raise ValueError("Réponse CoinGecko invalide")
    except Exception as e:
        print(f"Erreur CoinGecko : {e}")
        return ['BTC/USDT', 'SOL/USDT', 'XRP/USDT', 'BNB/USDT', 'DOGE/USDT', 'ADA/USDT']

    try:
        markets = exchange.load_markets()
    except Exception:
        return []

    altcoins = []
    EXCLUDE_EXACT = {
        'usdt','usdc','dai','fdusd','tusd','usde','pyusd','busd',
        'usd1','usdd','frax','lusd','gho','crvusd','usds','usdf',
        'usdx','dola','usdn','susd','alusd','bold','fxusd','usdt0',
        'wbtc','weth','steth','wsteth','reth','cbeth','wbeth',
        'meth','sfrxeth','frxeth','wmatic','wavax','wbnb'
    }
    for coin in coins:
        symbol = coin['symbol'].upper()
        if symbol.lower() in EXCLUDE_EXACT:
            continue
        if symbol.lower().startswith('usd'):
            continue
        pair = f"{symbol}/USDT"
        if pair in markets and markets[pair].get('active', True):
            altcoins.append(pair)
    print(f"✅ {len(altcoins)} paires récupérées.")
    return altcoins

def fetch_ohlcv(exchange, symbol, timeframe, limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not data:
            return None
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
    except Exception as e:
        print(f"fetch_ohlcv {symbol}: {e}")
        return None

def fetch_ticker_volume(exchange, symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return ticker.get('quoteVolume') or 0
    except Exception:
        return 0

# ==========================================
# INDICATEURS
# ==========================================

def compute_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = -delta.clip(upper=0).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_atr(df, period=14):
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ==========================================
# PIVOTS
# ==========================================

def detect_pivots(df, window=5):
    """Détecte les pivots SANS center=True pour ne pas perdre les dernières bougies."""
    w = window
    df['pivot_high'] = (
        (df['high'] == df['high'].rolling(w*2+1, center=False).max().shift(-w))
    )
    df['pivot_low'] = (
        (df['low'] == df['low'].rolling(w*2+1, center=False).min().shift(-w))
    )
    highs = df[df['pivot_high']].tail(2)
    lows = df[df['pivot_low']].tail(2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    pos_map = {ts: i for i, ts in enumerate(df.index)}
    return {
        'pH1': highs['high'].iloc[-1], 'pH2': highs['high'].iloc[-2],
        'pH1b': pos_map[highs.index[-1]], 'pH2b': pos_map[highs.index[-2]],
        'pL1': lows['low'].iloc[-1], 'pL2': lows['low'].iloc[-2],
        'pL1b': pos_map[lows.index[-1]], 'pL2b': pos_map[lows.index[-2]]
    }

def analyze_channel(df, pivot_data):
    if pivot_data is None:
        return None
    pH1, pH2 = pivot_data['pH1'], pivot_data['pH2']
    pH1b, pH2b = pivot_data['pH1b'], pivot_data['pH2b']
    pL1, pL2 = pivot_data['pL1'], pivot_data['pL2']
    pL1b, pL2b = pivot_data['pL1b'], pivot_data['pL2b']
    current_idx = len(df) - 1
    current_close = df['RS'].iloc[-1]

    bullChan = (pL1 > pL2) and (pH1 > pH2)
    bearChan = (pL1 < pL2) and (pH1 < pH2)

    if bullChan:
        slope = (pL1 - pL2) / (pL1b - pL2b) if (pL1b - pL2b) != 0 else 0
        supNow = pL1 + slope * (current_idx - pL1b)
        dist = abs(current_close - supNow) / current_close if current_close else 0
        if current_close < supNow:
            return {"dir": "SHORT", "type": "CASSÉ", "level": supNow, "status": "PREPARATION"}
        elif dist < 0.02:
            return {"dir": "SHORT", "type": "SURVEILLANCE", "level": supNow, "status": "SURVEILLANCE"}
    elif bearChan:
        slope = (pH1 - pH2) / (pH1b - pH2b) if (pH1b - pH2b) != 0 else 0
        resNow = pH1 + slope * (current_idx - pH1b)
        dist = abs(current_close - resNow) / current_close if current_close else 0
        if current_close > resNow:
            return {"dir": "LONG", "type": "CASSÉ", "level": resNow, "status": "PREPARATION"}
        elif dist < 0.02:
            return {"dir": "LONG", "type": "SURVEILLANCE", "level": resNow, "status": "SURVEILLANCE"}
    return None

def calculate_levels(df, signal, pivot_data, rr_ratio, atr):
    entry_price = df['close'].iloc[-1]
    if signal['dir'] == 'LONG':
        sl_price = min(pivot_data['pL1'], signal['level']) - (atr * 0.5)
        risk = entry_price - sl_price
        tp1 = entry_price + (risk * rr_ratio)
        tp2 = entry_price + (risk * rr_ratio * 2)
    else:
        sl_price = max(pivot_data['pH1'], signal['level']) + (atr * 0.5)
        risk = sl_price - entry_price
        tp1 = entry_price - (risk * rr_ratio)
        tp2 = entry_price - (risk * rr_ratio * 2)
    return entry_price, sl_price, tp1, tp2

# ==========================================
# LOGIQUE PRINCIPALE
# ==========================================

def run_bot():
    session = get_current_session()
    print(f"BOT INTRADAY - Session {session['name']} - {datetime.now()}")

    cache = load_cache()

    send_telegram_message(
        f"{session['emoji']} *BOT INTRADAY - Session {session['name']}*\n"
        f"_{session['desc']}_\n"
        f"⚙️ RR: 1:{session['rr']} | Vol min: {session['vol']//1_000_000}M$ | Max: {session['max']}"
    )

    exchange = ccxt.mexc()
    df_btc = fetch_ohlcv(exchange, 'BTC/USDT', TIMEFRAME_ANALYSE)
    if df_btc is None:
        send_telegram_message("❌ *[INTRADAY]* Erreur BTC.")
        return

    # Tendance BTC (filtre d'alignement)
    btc_rsi = compute_rsi(df_btc['close']).iloc[-1]
    btc_close = df_btc['close'].iloc[-1]
    btc_ema50 = df_btc['close'].ewm(span=50).mean().iloc[-1]
    btc_bull = btc_close > btc_ema50

    dynamic_alts = get_dynamic_altcoins(exchange, top_n=80)
    if not dynamic_alts:
        send_telegram_message("❌ *[INTRADAY]* Erreur liste altcoins.")
        return

    setups_valides = []
    preparations = []
    surveillances = []

    for symbol in dynamic_alts:
        # Filtre volume AVANT de charger l'OHLCV (économie de requêtes)
        qvol = fetch_ticker_volume(exchange, symbol)
        if qvol < session['vol']:
            continue

        df_alt = fetch_ohlcv(exchange, symbol, TIMEFRAME_ANALYSE)
        if df_alt is None:
            continue

        # ✅ FIX : merge sur l'index, pas sur une colonne
        df = pd.merge(
            df_alt, df_btc,
            left_index=True, right_index=True,
            suffixes=('_alt', '_btc'),
            how='inner'
        )
        if len(df) < 50:
            continue

        # Renomme les colonnes alt en noms "propres"
        df = df.rename(columns={
            'open_alt': 'open', 'high_alt': 'high', 'low_alt': 'low',
            'close_alt': 'close', 'volume_alt': 'volume'
        })
        # Pour BTC (déjà dans df_btc sans suffixe ? ici elles ont _btc)
        # Assure-toi que close_btc existe
        if 'close_btc' not in df.columns:
            continue

        # RS vs BTC
        if symbol == 'BTC/USDT':
            df['RS'] = df['close']
        else:
            df['RS'] = df['close'] / df['close_btc']

        # ATR correct
        atr_series = compute_atr(df, 14)
        atr = atr_series.iloc[-1]
        if pd.isna(atr) or atr <= 0:
            continue

        # RSI de l'alt
        rsi_alt = compute_rsi(df['close']).iloc[-1]

        pivot_data = detect_pivots(df, window=session['pivot'])
        signal = analyze_channel(df, pivot_data)

        if signal:
            # Filtre RSI : on évite les extrêmes
            if signal['dir'] == 'LONG' and (pd.notna(rsi_alt) and rsi_alt > 75):
                continue
            if signal['dir'] == 'SHORT' and (pd.notna(rsi_alt) and rsi_alt < 25):
                continue

            # Filtre alignement BTC
            if symbol != 'BTC/USDT':
                if signal['dir'] == 'LONG' and not btc_bull:
                    continue
                if signal['dir'] == 'SHORT' and btc_bull:
                    continue

            if signal['status'] == 'PREPARATION':
                dist = abs(df['close'].iloc[-1] - signal['level']) / df['close'].iloc[-1]
                if dist < session['tol']:
                    entry, sl, tp1, tp2 = calculate_levels(
                        df, signal, pivot_data, session['rr'], atr
                    )
                    # ✅ Anti-spam
                    if should_send_signal(cache, symbol, signal['dir'], signal['level']):
                        setups_valides.append({
                            'symbol': symbol, 'dir': signal['dir'],
                            'entry': entry, 'sl': sl, 'tp1': tp1, 'tp2': tp2,
                            'rsi': rsi_alt
                        })
                else:
                    preparations.append({
                        'symbol': symbol, 'dir': signal['dir'],
                        'level': signal['level']
                    })
            elif signal['status'] == 'SURVEILLANCE':
                surveillances.append({
                    'symbol': symbol, 'dir': signal['dir'],
                    'level': signal['level']
                })

        time.sleep(0.3)

    save_cache(cache)

    # -------- RAPPORT --------
    report = f"⚡ *RAPPORT INTRADAY (1H) - {session['emoji']} {session['name']}*\n"
    report += f"🕐 {datetime.now().strftime('%H:%M')} | 🔍 _{len(dynamic_alts)} altcoins_\n"
    report += f"📊 BTC : {'🟢 Bull' if btc_bull else '🔴 Bear'} (RSI {btc_rsi:.0f})\n\n"

    if setups_valides:
        report += f"🚀 *SETUPS VALIDÉS (max {session['max']})*\n"
        for s in setups_valides[:session['max']]:
            rsi_txt = f" | RSI {s['rsi']:.0f}" if pd.notna(s.get('rsi')) else ""
            report += f"• *{s['symbol']}* ({s['dir']}){rsi_txt}\n"
            report += f"  Entrée : {s['entry']:.4f} | SL : {s['sl']:.4f}\n"
            report += f"  TP1 : {s['tp1']:.4f} | TP2 : {s['tp2']:.4f}\n\n"
    else:
        report += "🚀 *SETUPS VALIDÉS* : Aucun.\n\n"

    if preparations:
        report += "⏳ *EN PRÉPARATION*\n"
        for p in preparations[:5]:
            report += f"• *{p['symbol']}* ({p['dir']}) - Niveau : {p['level']:.4f}\n"
        report += "\n"

    if surveillances:
        report += "👀 *SOUS SURVEILLANCE*\n"
        for s in surveillances[:5]:
            report += f"• *{s['symbol']}* ({s['dir']}) - Ligne : {s['level']:.4f}\n"

    send_telegram_message(report)
    print("Rapport Intraday envoyé.")

if __name__ == "__main__":
    run_bot()
