#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARNA Bot — TEK BEYİN MİMARİSİ
Tüm alım/satım kararları burada verilir.
index.html sadece görüntüler ve komut gönderir.
Telefon kapalıyken 7/24 çalışır.
"""

import json, time, hmac, hashlib, threading, math, requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from urllib.parse import urlencode

# ── DOSYA YOLLARI ──
STATE_FILE  = '/root/arna_state.json'
CONFIG_FILE = '/root/arna_config.json'
LEARN_FILE  = '/root/arna_learn.json'

# ── BİNANCE API ──
BASE_URL = 'https://api.binance.com/api/v3'
FALLBACK_URLS = [
    'https://data-api.binance.vision/api/v3',
    'https://api.binance.com/api/v3',
    'https://api1.binance.com/api/v3',
]

def binance_get(path, params=None):
    for base in FALLBACK_URLS:
        try:
            r = requests.get(base + path, params=params or {}, timeout=10)
            if r.status_code == 200:
                return r.json()
        except:
            continue
    return None

def binance_signed(method, path, params, api_key, api_secret):
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 5000
    query = urlencode(params)
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    query += '&signature=' + sig
    headers = {'X-MBX-APIKEY': api_key}
    url = BASE_URL + path
    try:
        if method == 'GET':
            r = requests.get(url + '?' + query, headers=headers, timeout=10)
        else:
            r = requests.post(url + '?' + query, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {'error': str(e)}

def get_klines(symbol, tf, limit=62):
    data = binance_get('/klines', {'symbol': symbol, 'interval': tf, 'limit': limit})
    if not data:
        return []
    return [{'o': float(k[1]), 'h': float(k[2]), 'l': float(k[3]), 'c': float(k[4]), 'v': float(k[5]),
             'open_time': k[0], 'close_time': k[6]} for k in data]

def get_price(symbol):
    for base in FALLBACK_URLS:
        try:
            r = requests.get(base + '/ticker/price', params={'symbol': symbol}, timeout=5)
            if r.status_code == 200:
                return float(r.json()['price'])
        except:
            continue
    return None

# ── STATE / CONFIG ──
state_lock    = threading.Lock()
position_lock = threading.Lock()

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            'positions': [], 'signals': [], 'history': [],
            'demo_bal': 2000, 'demo_pnl': 0,
            'mode': 'demo', 'auto_scan': False, 'auto_trade': False,
            'scanning': False, 'logs': [],
            'pos_exit_threshold': -5,
            'consecutive_losses': 0,
            'kill_switch': False,
            'server_activities': [],
            'bot_sessions': [],
            'persistent_blacklist': {},
            'active_hours': {'start': 9, 'end': 23}
        }

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, ensure_ascii=False)
    except:
        pass

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except:
        return {'tf': '5m', 'min_score': 80, 'pos_usd': 400, 'trail_pct': 1.5,
                'pct_filter_on': True, 'min_change': 3, 'max_change': 5,
                'api_key': '', 'api_secret': ''}

def log_msg(state, msg, cls=''):
    t = datetime.now().strftime('%H:%M:%S')
    entry = {'msg': msg, 'cls': cls, 'time': t}
    if 'logs' not in state:
        state['logs'] = []
    state['logs'].insert(0, entry)
    if len(state['logs']) > 80:
        state['logs'] = state['logs'][:80]
    print(f'[{t}] {msg}')

# ══════════════════════════════════════════════════
# GÖSTERGELER — index.html ile birebir aynı
# ══════════════════════════════════════════════════

def sma(arr, period):
    if not arr or len(arr) < period:
        return arr[-1] if arr else 0
    return sum(arr[-period:]) / period

def calc_atr(klines, period=14):
    if not klines or len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        high = klines[i]['h']; low = klines[i]['l']; prev_close = klines[i-1]['c']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

def calc_adx(klines, period=14):
    if not klines or len(klines) < period * 2:
        return None
    plus_dms, minus_dms, trs = [], [], []
    for i in range(1, len(klines)):
        high = klines[i]['h']; prev_high = klines[i-1]['h']
        low  = klines[i]['l']; prev_low  = klines[i-1]['l']
        prev_close = klines[i-1]['c']
        plus_dm  = max(high - prev_high, 0) if (high - prev_high) > (prev_low - low) else 0
        minus_dm = max(prev_low - low, 0)   if (prev_low - low) > (high - prev_high) else 0
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        plus_dms.append(plus_dm); minus_dms.append(minus_dm); trs.append(tr)
    def smooth(arr, p):
        s = sum(arr[:p]); result = [s]
        for x in arr[p:]:
            s = s - s/p + x; result.append(s)
        return result
    str14  = smooth(trs, period)
    spdm14 = smooth(plus_dms, period)
    smdm14 = smooth(minus_dms, period)
    dxs = []
    for i in range(len(str14)):
        if str14[i] == 0: continue
        pdi = 100 * spdm14[i] / str14[i]
        mdi = 100 * smdm14[i] / str14[i]
        dx  = 100 * abs(pdi - mdi) / (pdi + mdi + 0.0001)
        dxs.append(dx)
    if len(dxs) < period:
        return None
    adx = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx


def calc_rsi(closes, period=14):
    if not closes or len(closes) < period + 1:
        return 50
    ag, al = 0, 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d > 0: ag += d
        else: al += abs(d)
    ag /= period
    al /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = (ag * (period - 1) + (d if d > 0 else 0)) / period
        al = (al * (period - 1) + (abs(d) if d < 0 else 0)) / period
    return 100 if al == 0 else 100 - 100 / (1 + ag / al)

def calc_macd(closes):
    if not closes or len(closes) < 35:
        return {'line': 0, 'sig': 0, 'hist': 0}
    e12 = sum(closes[:12]) / 12
    e26 = sum(closes[:26]) / 26
    ml = []
    for i in range(1, len(closes)):
        e12 = closes[i] * (2/13) + e12 * (11/13)
        if i >= 25:
            e26 = closes[i] * (2/27) + e26 * (25/27)
            ml.append(e12 - e26)
    if len(ml) < 9:
        return {'line': ml[-1] if ml else 0, 'sig': 0, 'hist': 0}
    sg = sum(ml[:9]) / 9
    for i in range(9, len(ml)):
        sg = ml[i] * (2/10) + sg * (8/10)
    last = ml[-1]
    return {'line': last, 'sig': sg, 'hist': last - sg}

def boll_calc(closes, period=20):
    if not closes or len(closes) < period:
        l = closes[-1] if closes else 1
        return {'up': l*1.02, 'mid': l, 'dn': l*0.98}
    sl = closes[-period:]
    mid = sum(sl) / period
    std = math.sqrt(sum((x - mid)**2 for x in sl) / period)
    return {'up': mid + 2*std, 'mid': mid, 'dn': mid - 2*std}

# ── FİLTRELER (index.html ile birebir) ──

def trend_filt(closes):
    ma7  = sma(closes, 7)
    ma30 = sma(closes, 30)
    mc   = calc_macd(closes)
    up_trend = ma7 > ma30
    macd_up  = mc['line'] > mc['sig']
    macd_hist_pos = mc['hist'] > 0
    if up_trend and macd_up and macd_hist_pos:
        score = 25
    elif up_trend and macd_up:
        score = 20
    elif up_trend:
        score = 12
    else:
        score = 0
    return {
        'pass': up_trend and macd_up, 'score': score,
        'ma7': ma7, 'ma30': ma30,
        'macd_line': mc['line'], 'macd_sig': mc['sig'], 'macd_hist': mc['hist']
    }

def rsi_filt(closes):
    r = calc_rsi(closes, 14)
    if r >= 50 and r <= 70:   return {'pass': True,  'rsi': r, 'score': 15, 'note': 'İdeal bölge'}
    elif r > 45 and r < 50:   return {'pass': True,  'rsi': r, 'score': 6,  'note': 'Zayıf momentum'}
    elif r < 45:               return {'pass': False, 'rsi': r, 'score': 0,  'note': 'RSI riskli'}
    elif r > 75:               return {'pass': False, 'rsi': r, 'score': 0,  'note': 'Aşırı alım'}
    else:                      return {'pass': True,  'rsi': r, 'score': 4,  'note': 'Kabul edilebilir'}

def boll_filt(closes, price):
    b   = boll_calc(closes)
    pct = (price - b['dn']) / (b['up'] - b['dn'] + 0.0001)
    if price > b['mid'] and pct < 0.85:
        return {'pass': True,  'score': 15, 'zone': 'MB üstü', 'pos': pct}
    elif price > b['mid']:
        return {'pass': True,  'score': 8,  'zone': 'Üst banda yakın', 'pos': pct}
    else:
        return {'pass': False, 'score': 0,  'zone': 'MB altı', 'pos': pct}

def vol_filt(klines):
    closed = klines[:-1]
    vols   = [k['v'] for k in closed]
    if len(vols) < 12:
        return {'pass': False, 'score': 0, 'ratio': 0, 'note': 'Yetersiz veri'}
    avg5 = sum(vols[-5:]) / 5
    avg3 = sum(vols[-3:]) / 3
    ma5  = sma(vols[:-5], 5)
    ma10 = sma(vols[:-3], 10)
    r5  = avg5 / ma5  if ma5  > 0 else 0
    r10 = avg5 / ma10 if ma10 > 0 else 0
    if avg5 < ma10:
        return {'pass': False, 'score': 0, 'ratio': r10, 'note': 'Son 5 mum MA10 altı'}
    if avg5 < ma5:
        return {'pass': False, 'score': 0, 'ratio': r10, 'note': 'Son 5 mum MA5 altı'}
    if avg3 < ma10 * 1.0:
        return {'pass': False, 'score': 0, 'ratio': r10, 'note': 'Son 3 mum MA10 %100 altı'}
    last3v = vols[-3:]
    all_rising3 = len(last3v) >= 3 and last3v[2] > last3v[1] > last3v[0]
    rising2     = len(vols) >= 3 and vols[-1] > vols[-2] > vols[-3]
    score = 18 if r10 >= 2.0 else 14 if r10 >= 1.5 else 10 if r10 >= 1.0 else 0
    if all_rising3: score = min(20, score + 3)
    elif rising2:   score = min(20, score + 1)
    if avg5 > ma5:  score = min(20, score + 1)
    return {'pass': True, 'score': score, 'ratio': r10, 'note': f'MA10:{r10:.1f}x'}

def price_score(change):
    if 3 <= change <= 5: return 5
    if 2 <= change < 3:  return 3
    if 5 < change <= 7:  return 2
    return 1

def total_score(tr, rsi, bol, vol, chg):
    s = tr['score'] + rsi['score'] + bol['score'] + (vol['score'] if vol['pass'] else 0) + price_score(chg)
    return max(0, min(100, s))

def calc_tp(price, score, sl_price=None):
    pct = 4.0 if score >= 90 else 3.0 if score >= 85 else 2.0 if score >= 75 else 1.5
    if sl_price and sl_price > 0 and price > sl_price:
        sl_dist_pct = (price - sl_price) / price * 100
        min_tp_pct  = sl_dist_pct * 2.0
        high_tp_pct = sl_dist_pct * 2.5
        if score >= 88:
            pct = max(pct, high_tp_pct)
        else:
            pct = max(pct, min_tp_pct)
        pct = min(pct, 8.0)
    return {'tp': price * (1 + pct/100), 'pct': pct}

def is_peak_recovery(klines, price, high24h):
    recent_high      = max(k['h'] for k in klines[-20:]) if klines else price
    drop_from_high   = (high24h - price) / high24h * 100
    drop_from_recent = (recent_high - price) / recent_high * 100
    if drop_from_high > 10 and drop_from_recent > 5:
        return {'is_peak': True, 'drop': drop_from_high}
    return {'is_peak': False, 'drop': drop_from_high}

def has_fake_wick(klines, n=5):
    closed = klines[:-1][-n:]
    fake_count = 0
    for k in closed:
        body  = abs(k['c'] - k['o'])
        rng   = k['h'] - k['l']
        upper = k['h'] - max(k['c'], k['o'])
        lower = min(k['c'], k['o']) - k['l']
        if rng > 0:
            wick_ratio = (upper + lower) / (body + 0.000001)
            if wick_ratio > 2.5:
                fake_count += 1
    return fake_count >= 2

def swing_low(klines, n=10):
    closed = klines[:-1]
    if not closed:
        return 0
    recent = closed[-n:]
    price  = closed[-1]['c']
    sl     = min(k['l'] for k in recent)
    if (price - sl) / price < 0.005:
        wider = closed[-20:]
        return min(k['l'] for k in wider)
    return sl

# ── ÜST TF MAP ──
UPPER_TF_MAP = {
    '1m': '3m', '3m': '5m', '5m': '15m', '15m': '30m',
    '30m': '1h', '1h': '2h', '2h': '4h', '4h': '6h'
}
UPPER_TFS_DOUBLE = {
    '1m':  ['3m',  '15m'],
    '3m':  ['15m', '1h'],
    '5m':  ['15m', '1h'],
    '15m': ['30m', '1h'],
    '30m': ['1h',  '2h'],
    '1h':  ['2h',  '4h'],
    '2h':  ['4h',  '6h'],
    '4h':  ['6h',  None],
}

def analyze_tf(symbol, tf):
    if not tf:
        return None
    try:
        klines = get_klines(symbol, tf, 40)
        if len(klines) < 15:
            return None
        closes = [k['c'] for k in klines]
        tr  = trend_filt(closes)
        vol = vol_filt(klines)
        rsi = calc_rsi(closes, 14)
        mc  = calc_macd(closes)
        strong    = tr['pass'] and vol['pass'] and 45 <= rsi <= 75
        weakening = (not tr['pass'] and not vol['pass']) or rsi > 78 or (rsi < 40 and not tr['pass'])
        return {
            'tf': tf, 'strong': strong, 'weakening': weakening,
            'trend_pass': tr['pass'], 'vol_pass': vol['pass'],
            'rsi': rsi, 'macd_hist': mc['hist'],
            'ma7': tr['ma7'], 'ma30': tr['ma30']
        }
    except:
        return None

# ── ÖĞRENME SİSTEMİ ──

def load_learn():
    try:
        with open(LEARN_FILE) as f:
            return json.load(f)
    except:
        return {
            'trades': [], 'weights': {'trend': 1.0, 'rsi': 1.0, 'vol': 1.0, 'boll': 1.0},
            'total_trades': 0, 'wins': 0, 'tf_stats': {},
            'pos_exit_threshold': -5
        }

def save_learn(learn):
    try:
        with open(LEARN_FILE, 'w') as f:
            json.dump(learn, f)
    except:
        pass

def update_weights(learn):
    trades = learn['trades']
    if len(trades) < 5:
        return
    for k in ['trend', 'rsi', 'vol', 'boll']:
        with_ind    = [t for t in trades if t.get(k) == 1]
        without_ind = [t for t in trades if t.get(k) == 0]
        if len(with_ind) < 3:
            continue
        wr_with    = sum(1 for t in with_ind if t['result'] == 'win') / len(with_ind)
        wr_without = sum(1 for t in without_ind if t['result'] == 'win') / len(without_ind) if without_ind else 0
        if wr_with > wr_without + 0.1:
            learn['weights'][k] = min(1.5, learn['weights'][k] + 0.05)
        elif wr_with < wr_without - 0.1:
            learn['weights'][k] = max(0.5, learn['weights'][k] - 0.05)

def apply_learning(score, tr, rsi, bol, vol, learn):
    if learn['total_trades'] < 5:
        return score
    w   = learn['weights']
    adj = score
    if tr['pass']  and w['trend'] < 0.8: adj -= 5
    if rsi['pass'] and w['rsi']   < 0.8: adj -= 3
    if vol['pass'] and w['vol']   < 0.8: adj -= 5
    if bol['pass'] and w['boll']  < 0.8: adj -= 3
    if tr['pass']  and w['trend'] > 1.2: adj += 3
    if vol['pass'] and w['vol']   > 1.2: adj += 3
    return max(0, min(100, round(adj)))

def record_trade(learn, result, tr_pass, rsi_ok, vol_ok, boll_ok, pnl_pct, tf):
    learn['total_trades'] += 1
    if result == 'win':
        learn['wins'] += 1
    hour = datetime.now().hour
    session = 'EU' if 9 <= hour < 17 else 'US' if 17 <= hour < 23 else 'ASIA'
    r_multiple = round(pnl_pct / abs(pnl_pct) if pnl_pct != 0 else 0, 2)
    entry = {
        'result': result, 'trend': 1 if tr_pass else 0,
        'rsi': 1 if rsi_ok else 0, 'vol': 1 if vol_ok else 0,
        'boll': 1 if boll_ok else 0, 'pnl_pct': pnl_pct,
        'tf': tf, 'hour': hour, 'session': session,
        'ts': int(time.time() * 1000)
    }
    learn['trades'].append(entry)
    if len(learn['trades']) > 200:
        learn['trades'].pop(0)
    update_weights(learn)
    if 'tf_stats' not in learn:
        learn['tf_stats'] = {}
    if tf not in learn['tf_stats']:
        learn['tf_stats'][tf] = {'wins': 0, 'losses': 0, 'total_pnl': 0}
    if result == 'win':
        learn['tf_stats'][tf]['wins'] += 1
    else:
        learn['tf_stats'][tf]['losses'] += 1
    learn['tf_stats'][tf]['total_pnl'] += pnl_pct
    save_learn(learn)

# ── KARA LİSTE ──
blacklist = {}

def clean_blacklist():
    now = time.time()
    to_del = [sym for sym, e in list(blacklist.items())
              if now - e['ts'] > (600 if e.get('permanent') else 120)]
    for sym in to_del:
        del blacklist[sym]
    # Kalıcı kara listeden süresi dolmuşları da temizle
    if 'persistent_blacklist' in _state:
        expired = [s for s, t in list(_state['persistent_blacklist'].items())
                   if now - t > 7200]  # 2 saat
        for s in expired:
            del _state['persistent_blacklist'][s]

def add_blacklist(sym, permanent=False):
    blacklist[sym] = {'ts': time.time(), 'permanent': permanent}
    # SL ile kapanan coinleri kalıcı kara listeye de ekle
    if permanent and _state:
        if 'persistent_blacklist' not in _state:
            _state['persistent_blacklist'] = {}
        _state['persistent_blacklist'][sym] = time.time()

def is_blacklisted(sym):
    if sym in blacklist:
        return True
    if _state and sym in _state.get('persistent_blacklist', {}):
        if time.time() - _state['persistent_blacklist'][sym] < 7200:
            return True
    return False

# ── MUM ZAMANLAMA ──
def check_candle_timing(symbol, tf):
    try:
        data = binance_get('/klines', {'symbol': symbol, 'interval': tf, 'limit': 2})
        if not data or len(data) < 2:
            return True, 0
        open_candle     = data[-1]
        candle_open     = open_candle[0]
        candle_close    = open_candle[6]
        now_ms          = int(time.time() * 1000)
        candle_duration = candle_close - candle_open
        elapsed         = now_ms - candle_open
        elapsed_pct     = elapsed / candle_duration
        remaining_min   = round((candle_close - now_ms) / 60000)
        if elapsed_pct >= 0.85 or elapsed_pct <= 0.05:
            return True, remaining_min
        return False, remaining_min
    except:
        return True, 0

def check_and_enter(state, config, signal, learn):
    try:
        data = binance_get('/klines', {'symbol': signal['symbol'], 'interval': signal['tf'], 'limit': 4})
        if not data or len(data) < 3:
            open_position(state, config, signal, learn)
            return
        new_candle  = data[-1]
        prev_candle = data[-2]
        new_open    = float(new_candle[1])
        prev_close  = float(prev_candle[4])
        closes      = [float(k[4]) for k in data]
        new_rsi     = calc_rsi(closes, min(14, len(closes) - 1))
        price_diff = abs(new_open - signal['price']) / signal['price'] * 100
        if price_diff > 1:
            log_msg(state, f'❌ OTO: {signal["display_name"]} fiyat kayması ({price_diff:.2f}%) — iptal', 'gold')
            return
        if new_open < prev_close * 0.999:
            log_msg(state, f'❌ OTO: {signal["display_name"]} yeni mum kırmızı — iptal', 'gold')
            return
        if new_rsi < 40 or new_rsi > 78:
            log_msg(state, f'❌ OTO: {signal["display_name"]} RSI bozuldu ({new_rsi:.0f}) — iptal', 'gold')
            return
        vols = [float(k[5]) for k in data]
        if len(vols) >= 4:
            avg_vol3 = sum(vols[:3]) / 3
            if vols[-1] < avg_vol3 * 0.5:
                log_msg(state, f'❌ OTO: {signal["display_name"]} hacim çöktü — iptal', 'gold')
                return
        log_msg(state, f'✅ OTO: {signal["display_name"]} mum kapandı, kontroller geçti — GİRİŞ!', 'up')
        open_position(state, config, signal, learn)
    except Exception as e:
        log_msg(state, f'⚠️ check_and_enter hata: {str(e)[:40]}', 'gold')
        open_position(state, config, signal, learn)

# ── STABLECOIN FİLTRE ──
STABLECOINS = {'BUSDUSDT', 'USDCUSDT', 'TUSDUSDT', 'FDUSDUSDT', 'DAIUSDT',
               'USDPUSDT', 'EURUSDT', 'GBPUSDT', 'BUSDTRY', 'USDCTRY', 'DAITRY'}

# ══════════════════════════════════════════════════
# TARAMA
# ══════════════════════════════════════════════════

def scan_market(state, config):
    learn = load_learn()
    log_msg(state, 'Tarama başladı...', 'blue')
    state['scanning'] = True
    save_state(state)

    tf         = config.get('tf', '5m')
    min_score  = int(config.get('min_score', 80))
    pct_on     = config.get('pct_filter_on', True)
    min_change = float(config.get('min_change', 3))
    max_change = float(config.get('max_change', 5))

    try:
        tickers = binance_get('/ticker/24hr')
        if not tickers:
            log_msg(state, 'Binance bağlantı hatası', 'dn')
            return

        usd_try = 38.5
        try:
            fx = binance_get('/ticker/price', {'symbol': 'USDTTRY'})
            if fx:
                usd_try = float(fx['price'])
            log_msg(state, f'💱 USD/TRY kuru: {usd_try:.2f}', 'blue')
        except:
            pass

        cands = []
        for raw in tickers:
            sym     = raw['symbol']
            is_usdt = sym.endswith('USDT')
            is_try  = sym.endswith('TRY')
            if not is_usdt:  # Sadece USDT çiftleri — TRY çiftleri fiyat dönüşümünde hata yapıyor
                continue
            if sym in STABLECOINS:
                continue
            vol_usd = float(raw['quoteVolume'])
            if is_try:
                vol_usd /= usd_try
            if vol_usd < 5_000_000:
                continue
            if int(raw['count']) < 10000:
                continue
            price = float(raw['lastPrice'])
            if is_try:
                price /= usd_try
            change = float(raw['priceChangePercent'])
            if pct_on and (change < min_change or change > max_change):
                continue
            if price < 0.0005:
                continue
            high24h = float(raw['highPrice'])
            if is_try:
                high24h /= usd_try
            symbol_usdt = sym.replace('TRY', 'USDT') if is_try else sym
            cands.append({
                'symbol': symbol_usdt, '_original': sym,
                'price': price, 'change': change,
                'volume': vol_usd, 'high24h': high24h
            })

        cands.sort(key=lambda x: x['volume'], reverse=True)
        filter_info = f'+{min_change}–{max_change}%' if pct_on else 'tüm coinler'
        log_msg(state, f'Filtre 1: {len(cands)} coin | {tf} | {filter_info}', 'blue')

        clean_blacklist()
        results = []
        total_cands = len(cands)

        for ci, c in enumerate(cands):
            sym = c['symbol']
            state['scan_progress'] = {
                'current': ci + 1,
                'total': total_cands,
                'pct': round((ci + 1) / total_cands * 100) if total_cands else 0,
                'symbol': sym.replace('USDT', '')[:8]
            }
            if is_blacklisted(sym):
                continue
            try:
                klines = get_klines(sym, tf, 62)
                if len(klines) < 32:
                    continue

                closes = [k['c'] for k in klines]
                price  = c['price']
                change = c['change']

                upper_tf = UPPER_TF_MAP.get(tf)
                if upper_tf:
                    try:
                        u_klines = get_klines(sym, upper_tf, 35)
                        if u_klines and len(u_klines) >= 30:
                            u_closes = [k['c'] for k in u_klines]
                            u_tr  = trend_filt(u_closes)
                            ma7u  = sma(u_closes, 7)
                            ma25u = sma(u_closes, 25)
                            strong_down = (not u_tr['pass'] and
                                           u_tr['macd_hist'] < 0 and
                                           ma7u < ma25u * 0.99)
                            if strong_down:
                                base = sym.replace('USDT', '').replace('TRY', '')
                                add_blacklist(base + 'USDT', False)
                                add_blacklist(base + 'TRY', False)
                                add_blacklist(sym, False)
                                log_msg(state, f'🚫 {base[:6]} — üst TF ({upper_tf}) aşağı', 'gold')
                                continue
                    except:
                        pass

                peak = is_peak_recovery(klines, price, c['high24h'])
                if peak['is_peak']:
                    add_blacklist(sym, True)
                    log_msg(state, f'🚫 {sym.replace("USDT","")[:6]} — zirve', 'gold')
                    continue

                if has_fake_wick(klines, 5):
                    add_blacklist(sym, True)
                    log_msg(state, f'🚫 {sym.replace("USDT","")[:6]} — fitil', 'gold')
                    continue

                tr    = trend_filt(closes)
                rsi_r = rsi_filt(closes)
                bol   = boll_filt(closes, price)
                vol   = vol_filt(klines)

                if rsi_r['rsi'] < 35:
                    add_blacklist(sym, False)
                    log_msg(state, f'🚫 {sym.replace("USDT","")[:6]} — RSI düşük ({rsi_r["rsi"]:.0f})', 'gold')
                    continue

                if not vol['pass'] and vol['ratio'] < 0.5:
                    add_blacklist(sym, False)
                    log_msg(state, f'🚫 {sym.replace("USDT","")} — hacimsiz', 'gold')
                    continue

                sc  = total_score(tr, rsi_r, bol, vol, change)
                adj = apply_learning(sc, tr, rsi_r, bol, vol, learn)
                sl  = swing_low(klines, 10)
                tp  = calc_tp(price, adj, sl_price=sl)

                if tf not in ('1m', '3m', '5m'):
                    candle_ok, remaining_min = check_candle_timing(sym, tf)
                    if not candle_ok:
                        log_msg(state, f'⏳ {sym.replace("USDT","")[:6]} — mum ortası ({remaining_min}dk kaldı)', 'gold')
                        continue

                try:
                    adx_val = calc_adx(klines, 14)
                    if adx_val is not None and adx_val < 18:
                        log_msg(state, f'🌀 {sym.replace("USDT","")[:6]} — sideways (ADX:{adx_val:.0f})', 'gold')
                        add_blacklist(sym, False)
                        continue
                except:
                    pass

                if adj >= min_score:
                    chart_closes = [round(k['c'], 6) for k in klines[-30:]]
                    results.append({
                        'symbol': sym,
                        'display_name': sym.replace('USDT', ''),
                        'price': price, 'change': change, 'volume': c['volume'],
                        'score': adj, 'verdict': 'BUY',
                        'rsi': round(rsi_r['rsi'], 1),
                        'trend_pass': tr['pass'], 'rsi_ok': rsi_r['pass'],
                        'vol_ok': vol['pass'], 'boll_ok': bol['pass'],
                        'vol_ratio': round(vol.get('ratio', 0), 1),
                        'target_tp': tp['tp'], 'tp_pct': tp['pct'],
                        'swing_low': sl, 'tf': tf,
                        'chart_closes': chart_closes,
                        'scanned_at': int(time.time() * 1000)
                    })

            except Exception:
                continue

        best_by_base = {}
        for r in results:
            base = r['display_name']
            if base not in best_by_base or r['score'] > best_by_base[base]['score']:
                best_by_base[base] = r
        final = sorted(best_by_base.values(), key=lambda x: x['score'], reverse=True)

        now_ms = int(time.time() * 1000)
        bad_coins = {h['symbol'] for h in state.get('history', [])
                     if h.get('result') == 'lose' and h.get('exit_reason') == 'SL'
                     and now_ms - h.get('exit_time', 0) < 3600000}
        if bad_coins:
            before = len(final)
            final  = [r for r in final if r['symbol'] not in bad_coins]
            if before > len(final):
                log_msg(state, f'🚫 Son 1 saatte SL ile kapanan {before-len(final)} coin filtrelendi', 'gold')

        state['signals'] = final
        buy_n = sum(1 for s in final if s['verdict'] == 'BUY')
        log_msg(state, f'Tarama tamam: {len(final)} sinyal | {buy_n} AL', 'up' if final else 'gold')

        if state.get('auto_trade') and final and not state.get('positions'):
            # Yasak saat kontrolü — her zaman yap
            hours = state.get('active_hours', {'start': 0, 'end': 24, 'active': False})
            current_hour = datetime.utcnow().hour
            in_pause = False
            if hours.get('active', False):
                s, e = hours['start'], hours['end']
                if s < e:
                    in_pause = s <= current_hour < e
                else:
                    in_pause = current_hour >= s or current_hour < e

            if in_pause:
                log_msg(state, f'⏸️ OTO AL: Yasak saat (UTC {current_hour}:xx, {hours["start"]}–{hours["end"]} arası dur) — giriş yapılmadı', 'gold')
            else:
                best = final[0]
                candle_ok, remaining_min = check_candle_timing(best['symbol'], best['tf'])
                if candle_ok:
                    open_position(state, config, best, learn)
                else:
                    log_msg(state, f'⏳ OTO: {best["display_name"]} mum kapanışı bekleniyor ({remaining_min}dk)', 'gold')
                    def delayed_enter_main(sig, rem):
                        time.sleep(rem * 60 + 5)
                        with state_lock:
                            if state.get('auto_trade') and not state.get('positions'):
                                h2 = state.get('active_hours', {'start': 0, 'end': 24, 'active': False})
                                if h2.get('active', False):
                                    s2, e2 = h2['start'], h2['end']
                                    if s2 < e2:
                                        paused2 = s2 <= datetime.utcnow().hour < e2
                                    else:
                                        paused2 = datetime.utcnow().hour >= s2 or datetime.utcnow().hour < e2
                                    if paused2:
                                        log_msg(state, f'⏸️ OTO AL (delayed): Yasak saat — {sig["display_name"]} pas geçildi', 'gold')
                                        return
                                check_and_enter(state, config, sig, load_learn())
                    threading.Thread(target=delayed_enter_main, args=(best, remaining_min), daemon=True).start()

    except Exception as e:
        log_msg(state, f'Tarama hata: {str(e)[:60]}', 'dn')
    finally:
        state['scanning'] = False
        state['last_scan'] = int(time.time())
        save_state(state)

# ══════════════════════════════════════════════════
# POZİSYON AÇMA/KAPAMA
# ══════════════════════════════════════════════════

MAX_POSITIONS = 2

def open_position(state, config, signal, learn=None):
    with position_lock:
        if learn is None:
            learn = load_learn()
        if state.get('kill_switch') == True:
            log_msg(state, f'⛔ Kill Switch aktif — {signal["display_name"]} alım engellendi', 'dn')
            return
        if len(state.get('positions', [])) >= MAX_POSITIONS:
            log_msg(state, 'Max pozisyon sayısına ulaşıldı (2)', 'gold')
            return
        if any(p['symbol'] == signal['symbol'] for p in state.get('positions', [])):
            log_msg(state, f'{signal["display_name"]} zaten açık', 'gold')
            return

        mode      = state.get('mode', 'demo')
        pos_usd   = float(config.get('pos_usd', 400))
        trail_pct = float(config.get('trail_pct', 1.5))
        commission = pos_usd * 0.001

        if mode == 'live':
            cfg = load_config()
            order = binance_signed('POST', '/order',
                {'symbol': signal['symbol'], 'side': 'BUY', 'type': 'MARKET',
                 'quoteOrderQty': round(pos_usd, 2)},
                cfg.get('api_key', ''), cfg.get('api_secret', ''))
            if 'orderId' not in order:
                log_msg(state, f'Emir hatası: {order.get("msg","?")}', 'dn')
                return
            qty   = float(order['executedQty'])
            entry = float(order['cummulativeQuoteQty']) / qty
        else:
            qty   = (pos_usd - commission) / signal['price']
            entry = signal['price']

        tp_pct     = signal.get('tp_pct', 1.5)
        sl_price   = signal.get('swing_low', 0)
        atr_trail_pct = trail_pct
        try:
            klines_atr = get_klines(signal['symbol'], signal.get('tf', '5m'), 20)
            atr_val    = calc_atr(klines_atr, 14)
            if atr_val and entry > 0:
                atr_pct = (atr_val / entry) * 100
                atr_trail_pct = max(1.0, min(4.0, atr_pct * 1.5))
                log_msg(state, f'📐 ATR Stop: %{atr_trail_pct:.2f} (ATR={atr_pct:.2f}%)', 'blue')
        except:
            pass
        trail_sl   = entry * (1 - atr_trail_pct / 100)
        swing_diff = (entry - sl_price) / entry * 100 if sl_price else 999
        init_sl    = max(sl_price, trail_sl) if (sl_price and swing_diff <= 3) else trail_sl

        pos = {
            'id':           int(time.time() * 1000),
            'symbol':       signal['symbol'],
            'display_name': signal['display_name'],
            'entry':        entry, 'live_price': entry,
            'qty':          qty, 'pos_usd': pos_usd - commission,
            'gross_usd':    pos_usd, 'commission': commission,
            'trail_pct':    trail_pct, 'trail_sl': init_sl, 'init_sl': init_sl,
            'target_tp':    entry * (1 + tp_pct / 100), 'tp_pct': tp_pct,
            'score':        signal['score'], 'tf': signal['tf'],
            'entry_time':   int(time.time() * 1000),
            'is_live':      mode == 'live',
            'trend_pass':   signal.get('trend_pass', False),
            'rsi_ok':       signal.get('rsi_ok', False),
            'vol_ok':       signal.get('vol_ok', False),
            'boll_ok':      signal.get('boll_ok', False),
            'last_upper_action': 0,
            'pos_check_decisions': [],
            '_momentum_prices': []
        }

        if mode != 'live':
            state['demo_bal'] = state.get('demo_bal', 2000) - pos_usd

        if 'positions' not in state:
            state['positions'] = []
        state['positions'].append(pos)

        if 'history' not in state:
            state['history'] = []
        state['history'].insert(0, {**pos, 'result': 'open', 'pnl': None})
        if len(state['history']) > 200:
            state['history'] = state['history'][:200]

        log_msg(state, f'✅ GİRİŞ: {pos["display_name"]} @ ${entry:.4f} | ${pos_usd:.0f} | SL ${init_sl:.4f}', 'up')

        if 'server_activities' not in state:
            state['server_activities'] = []
        state['server_activities'].insert(0, {
            'type': 'opened',
            'coin': signal['display_name'],
            'symbol': signal['symbol'],
            'entry': entry,
            'tf': signal['tf'],
            'score': signal.get('score', 0),
            'time': int(time.time() * 1000),
            'time_str': datetime.now().strftime('%H:%M'),
        })
        state['server_activities'] = state['server_activities'][:50]
        save_state(state)

def close_position(state, config, pos, exit_price, reason, learn=None):
    if not any(p['id'] == pos['id'] for p in state.get('positions', [])):
        return
    if learn is None:
        learn = load_learn()

    commission = exit_price * pos['qty'] * 0.001
    pnl  = (exit_price - pos['entry']) / pos['entry'] * pos['pos_usd'] - commission
    mode = state.get('mode', 'demo')

    if pos.get('is_live') and mode == 'live':
        cfg = load_config()
        binance_signed('POST', '/order',
            {'symbol': pos['symbol'], 'side': 'SELL', 'type': 'MARKET',
             'quantity': round(pos['qty'], 6)},
            cfg.get('api_key', ''), cfg.get('api_secret', ''))
        try:
            acc  = binance_signed('GET', '/account', {}, cfg.get('api_key', ''), cfg.get('api_secret', ''))
            usdt = next((b for b in acc.get('balances', []) if b['asset'] == 'USDT'), None)
            if usdt:
                state['demo_bal'] = float(usdt['free'])
        except:
            pass
    else:
        state['demo_bal'] = state.get('demo_bal', 2000) + pos['pos_usd'] + pnl
        state['demo_pnl'] = state.get('demo_pnl', 0) + pnl

    state['positions'] = [p for p in state['positions'] if p['id'] != pos['id']]

    rec = next((h for h in state.get('history', []) if h['id'] == pos['id']), None)
    if rec:
        rec.update({
            'result': 'win' if pnl >= 0 else 'lose',
            'pnl': round(pnl, 2),
            'exit_price': exit_price,
            'exit_reason': reason,
            'exit_time': int(time.time() * 1000)
        })

    icon = '💰' if pnl >= 0 else '⚠️'
    log_msg(state, f'{icon} ÇIKIŞ ({reason}): {pos["display_name"]} | {pnl:+.2f}$',
            'up' if pnl >= 0 else 'dn')

    if reason == 'SL':
        state['consecutive_losses'] = (state.get('consecutive_losses') or 0) + 1
        if state['consecutive_losses'] >= 3:
            state['kill_switch'] = True
            state['auto_trade']  = False
            log_msg(state, '⛔ KILL SWITCH: 3 ardışık SL — OTO AL durduruldu!', 'dn')
    else:
        state['consecutive_losses'] = 0

    record_trade(learn, 'win' if pnl >= 0 else 'lose',
                 pos.get('trend_pass', False), pos.get('rsi_ok', False),
                 pos.get('vol_ok', False), pos.get('boll_ok', False),
                 (pnl / pos['pos_usd']) * 100, pos.get('tf', '?'))

    if 'server_activities' not in state:
        state['server_activities'] = []
    state['server_activities'].insert(0, {
        'type': 'win' if pnl >= 0 else 'lose',
        'coin': pos.get('display_name', pos['symbol']),
        'symbol': pos['symbol'],
        'entry': pos['entry'],
        'exit_price': exit_price,
        'pnl': round(pnl, 2),
        'reason': reason,
        'tf': pos.get('tf', '?'),
        'time': int(time.time() * 1000),
        'time_str': datetime.now().strftime('%H:%M'),
        'duration_min': round((time.time()*1000 - pos.get('entry_time', time.time()*1000)) / 60000)
    })
    state['server_activities'] = state['server_activities'][:50]
    save_state(state)

    threading.Thread(
        target=post_trade_track,
        args=(state, pos['symbol'], pos.get('display_name'), exit_price, pnl, reason, pos.get('tf','1h')),
        daemon=True
    ).start()

# ── ÇIKIŞ SONRASI TAKİP ──
POST_TRACK_DURATION = {
    '1m': 10*60, '3m': 20*60, '5m': 30*60,
    '15m': 60*60, '30m': 2*60*60, '1h': 4*60*60,
    '2h': 6*60*60, '4h': 12*60*60
}
POST_TRACK_INTERVAL = {
    '1m': 30, '3m': 60, '5m': 60,
    '15m': 180, '30m': 300, '1h': 600,
    '2h': 900, '4h': 1800
}

def post_trade_track(state, symbol, display_name, exit_price, pnl, reason, tf):
    duration = POST_TRACK_DURATION.get(tf, 1800)
    interval = POST_TRACK_INTERVAL.get(tf, 60)
    end_time = time.time() + duration
    prices   = []

    log_msg(state, f'📡 {display_name} çıkış sonrası takip başladı ({round(duration/60)}dk)', 'blue')

    while time.time() < end_time:
        time.sleep(interval)
        price = get_price(symbol)
        if price:
            prices.append(price)

    if not prices:
        return

    max_price      = max(prices)
    min_price      = min(prices)
    up_from_exit   = (max_price - exit_price) / exit_price * 100
    down_from_exit = (exit_price - min_price) / exit_price * 100

    if up_from_exit > 1.5 and reason != 'TP':
        verdict = 'early_exit'
        log_msg(state, f'📊 {display_name} takip bitti → ⚠️ Erken çıkış! +%{up_from_exit:.1f} daha yükseldi', 'gold')
    elif down_from_exit > 1.0:
        verdict = 'correct_exit'
        log_msg(state, f'📊 {display_name} takip bitti → ✅ Doğru çıkış! -%{down_from_exit:.1f} düştü', 'up')
    else:
        verdict = 'neutral'
        log_msg(state, f'📊 {display_name} takip bitti → Nötr', 'blue')

    with state_lock:
        for h in state.get('history', []):
            if h.get('symbol') == symbol and h.get('exit_reason') == reason:
                age = time.time()*1000 - h.get('exit_time', 0)
                if age < (duration+60)*1000:
                    h['_post_track'] = {
                        'verdict': verdict,
                        'up_from_exit': round(up_from_exit, 1),
                        'down_from_exit': round(down_from_exit, 1),
                        'duration': round(duration/60)
                    }
                    break
        for a in state.get('server_activities', []):
            if a.get('symbol') == symbol and a.get('reason') == reason:
                a['track_verdict'] = verdict
                a['up_from_exit']   = round(up_from_exit, 1)
                a['down_from_exit'] = round(down_from_exit, 1)
                break
        save_state(state)

# ══════════════════════════════════════════════════
# FİYAT TAKİBİ
# ══════════════════════════════════════════════════

def update_positions(state, config):
    if not state.get('positions'):
        return False
    learn   = load_learn()
    changed = False

    for pos in list(state['positions']):
        price = get_price(pos['symbol'])
        if not price:
            continue

        last_price     = pos.get('_last_price', 0)
        pos['live_price'] = price
        changed = True

        pnl_pct_now = (price - pos['entry']) / pos['entry'] * 100
        tp_activation = pos['tp_pct'] * 0.4
        if pnl_pct_now >= tp_activation:
            new_sl = price * (1 - pos['trail_pct'] / 100)
            if new_sl > pos['trail_sl']:
                pos['trail_sl'] = new_sl

        pnl_pct = (price - pos['entry']) / pos['entry'] * 100
        tp_pct  = pos['tp_pct']

        pnl_usd = (price - pos['entry']) / pos['entry'] * pos['pos_usd']
        if pnl_usd >= 1.70 and pos['trail_sl'] < pos['entry']:
            pos['trail_sl'] = pos['entry'] * 1.002
            log_msg(state, f'🔒 BEP kilidi: $1.70 kâr aşıldı → SL girişe çekildi ({pos["display_name"]})', 'gold')

        if pnl_pct >= tp_pct:
            lock_sl = pos['entry'] + (price - pos['entry']) * 0.6
            if lock_sl > pos['trail_sl']:
                pos['trail_sl'] = lock_sl

        if last_price > 0:
            drop_pct = (last_price - price) / last_price * 100
            now = time.time()
            last_sq = pos.get('_last_sl_squeeze', 0)
            if drop_pct >= 1.5 and now - last_sq > 30:
                pos['_last_sl_squeeze'] = now
                tighter_sl = price * (1 - (pos['trail_pct'] * 0.3) / 100)
                if tighter_sl > pos['trail_sl']:
                    pos['trail_sl'] = tighter_sl
                    log_msg(state, f'⚡ ANİ DÜŞÜŞ (-%{drop_pct:.1f}%) → SL sıkıştırıldı: ${tighter_sl:.4f}', 'gold')

        pos['_last_price'] = price

        if '_momentum_prices' not in pos:
            pos['_momentum_prices'] = []
        pos['_momentum_prices'].append(price)
        if len(pos['_momentum_prices']) > 20:
            pos['_momentum_prices'].pop(0)
        check_momentum_exit(state, config, pos)

        if price <= pos['trail_sl']:
            log_msg(state, f'🔴 SL: {pos["display_name"]} @ ${price:.4f}', 'dn')
            close_position(state, config, pos, price, 'SL', learn)
            return True

        if price >= pos['target_tp']:
            log_msg(state, f'💰 TP: {pos["display_name"]} @ ${price:.4f}', 'up')
            close_position(state, config, pos, price, 'TP', learn)
            return True

    if changed:
        save_state(state)
    return changed

def check_momentum_exit(state, config, pos):
    prices = pos.get('_momentum_prices', [])
    if len(prices) < 10:
        return
    lp      = pos.get('live_price', pos['entry'])
    pnl_pct = (lp - pos['entry']) / pos['entry'] * 100
    # Sadece TP'nin %80'ine ulaşıldıysa devreye gir (önceki %60'tı, çok erken tetikleniyordu)
    if pnl_pct < pos['tp_pct'] * 0.9:
        return
    recent5  = sum(prices[-5:]) / 5
    older5   = sum(prices[-10:-5]) / 5 if len(prices) >= 10 else recent5
    momentum = (recent5 - older5) / older5 * 100 if older5 > 0 else 0
    now      = time.time()
    last_cd  = pos.get('_momentum_cd', 0)
    # Cooldown 60 saniyeye çıkarıldı (önceki 30'du, çok sık tetikleniyordu)
    if now - last_cd > 60:
        if momentum < -0.3:
            pos['_momentum_cd'] = now
            smart_tp = lp * 1.005  # Önceki 1.003'tü, biraz daha alan bırak
            if smart_tp < pos['target_tp']:
                pos['target_tp'] = smart_tp
                log_msg(state, f'📉 Momentum kırıldı → TP sıkıştırıldı: ${smart_tp:.4f}', 'gold')
        elif momentum > 0.5 and pnl_pct > pos['tp_pct']:
            pos['_momentum_cd'] = now
            new_tp = pos['target_tp'] * 1.008
            if new_tp > pos['target_tp']:
                pos['target_tp'] = new_tp
                log_msg(state, f'🚀 Momentum güçlü → TP yükseltildi: ${new_tp:.4f}', 'up')

# ══════════════════════════════════════════════════
# POZİSYON ANALİZİ
# ══════════════════════════════════════════════════

POS_CHECK_INTERVALS = {
    '1m': 60, '3m': 120, '5m': 180, '15m': 300,
    '30m': 600, '1h': 900, '2h': 1200, '4h': 1800
}

def check_position(state, config, pos):
    sym     = pos['symbol']
    tf      = pos.get('tf', '1h')
    lp      = pos.get('live_price', pos['entry'])
    pnl_pct = (lp - pos['entry']) / pos['entry'] * 100

    klines = get_klines(sym, tf, 30)
    if len(klines) < 20:
        return

    closes = [k['c'] for k in klines]
    vols   = [k['v'] for k in klines]

    last5        = klines[-6:-1]
    green_count  = sum(1 for k in last5 if k['c'] > k['o'])
    red_count    = sum(1 for k in last5 if k['c'] <= k['o'])
    last_candle  = last5[-1]
    body         = abs(last_candle['c'] - last_candle['o'])
    rng          = last_candle['h'] - last_candle['l']
    wick_ratio   = (rng - body) / rng if rng > 0 else 0
    has_long_wick = wick_ratio > 0.6

    vol_now     = sum(vols[-3:]) / 3
    vol_prev    = sum(vols[-8:-3]) / 5 if len(vols) >= 8 else vol_now
    vol_rising  = vol_now > vol_prev * 1.1
    vol_falling = vol_now < vol_prev * 0.8

    rsi            = calc_rsi(closes, 14)
    rsi_ok         = 45 <= rsi <= 72
    rsi_overbought = rsi > 75
    rsi_weak       = rsi < 45

    macd           = calc_macd(closes)
    macd_positive  = macd['hist'] > 0
    macd_divergence = macd['hist'] < 0 and macd['line'] > 0

    boll       = boll_calc(closes)
    boll_pct   = (lp - boll['dn']) / (boll['up'] - boll['dn'] + 0.0001)
    near_upper = boll_pct > 0.85
    above_mid  = boll_pct > 0.5

    vwap_data = klines[-20:]
    vwap_num  = sum(((k['h']+k['l']+k['c'])/3) * k['v'] for k in vwap_data)
    vwap_den  = sum(k['v'] for k in vwap_data)
    vwap      = vwap_num / vwap_den if vwap_den > 0 else lp
    above_vwap = lp > vwap

    score   = 0
    reasons = []
    if green_count >= 3:  score += 2
    if vol_rising:        score += 2;  reasons.append('hacim artıyor')
    if rsi_ok:            score += 2
    if macd_positive:     score += 2
    if above_mid:         score += 1
    if above_vwap:        score += 2;  reasons.append('VWAP üstü')
    if red_count >= 4:    score -= 3;  reasons.append('mumlar kırmızı')
    if has_long_wick:     score -= 2;  reasons.append('uzun fitil')
    if vol_falling:       score -= 2;  reasons.append('hacim düşüyor')
    if rsi_overbought:    score -= 3;  reasons.append('RSI aşırı alım')
    if rsi_weak:          score -= 2;  reasons.append('RSI zayıf')
    if macd_divergence:   score -= 2;  reasons.append('MACD zayıflıyor')
    if near_upper:        score -= 2;  reasons.append('üst banda dayandı')
    if not above_vwap:    score -= 2;  reasons.append('VWAP altı')
    if pnl_pct > 1.5:     score += 1
    if pnl_pct > 2.5:     score += 1

    threshold = state.get('pos_exit_threshold', -5)

    if score >= 4:
        decision = 'Devam Et';    action = 'hold'
    elif score > threshold:
        decision = 'Dikkatli Ol'; action = 'tighten'
    else:
        decision = 'Çık Önerilir'; action = 'exit'

    last_dec = None
    if pos.get('pos_check_decisions'):
        last_dec = pos['pos_check_decisions'][-1].get('decision')
    if last_dec != decision:
        icon       = '🟢' if action == 'hold' else '🟡' if action == 'tighten' else '🔴'
        reason_txt = ' · ' + ', '.join(reasons[:2]) if reasons else ''
        log_msg(state,
                f'{icon} Pozisyon: {decision} (skor:{score}) RSI:{rsi:.0f} MACD:{"↑" if macd_positive else "↓"}{reason_txt}',
                'up' if action == 'hold' else 'gold' if action == 'tighten' else 'dn')

    if 'pos_check_decisions' not in pos:
        pos['pos_check_decisions'] = []
    pos['pos_check_decisions'].append({
        'time': int(time.time() * 1000), 'decision': decision,
        'action': action, 'score': score
    })
    if len(pos['pos_check_decisions']) > 20:
        pos['pos_check_decisions'].pop(0)

    # Pozisyon analiz sonucunu state'e yaz — index bunu okur
    pos['_last_analysis'] = {
        'decision': decision, 'action': action, 'score': score,
        'rsi': round(rsi, 1), 'macd_up': macd_positive,
        'reasons': reasons[:2],
        'time': datetime.now().strftime('%H:%M'),
        'vwap_above': above_vwap
    }

    if action == 'hold' and score >= 6 and pnl_pct > 0.5:
        hold_sl = pos['entry'] + (lp - pos['entry']) * 0.40
        if hold_sl > pos['trail_sl']:
            pos['trail_sl'] = hold_sl
            log_msg(state, f'🟢 Devam Et → SL yukarı: ${hold_sl:.4f}', 'up')

    elif action == 'tighten':
        tighter_sl = lp * (1 - (pos['trail_pct'] * 0.5) / 100)
        if tighter_sl > pos['trail_sl']:
            pos['trail_sl'] = tighter_sl
            log_msg(state, f'⚡ SL sıkıştırıldı: ${tighter_sl:.4f}', 'gold')

    elif action == 'exit':
        # Çift onay: bir önceki karar da 'exit' ise çık
        prev_decisions = pos.get('pos_check_decisions', [])
        prev_exit = len(prev_decisions) >= 2 and prev_decisions[-2].get('action') == 'exit'
        if pnl_pct > 0.3 and prev_exit:
            close_position(state, config, pos, lp, 'ANALYSIS')
        elif pnl_pct > 0.3 and not prev_exit:
            log_msg(state, f'⏳ Çıkış bekleniyor — bir sonraki kontrolde de "çık" derse çıkılacak ({pos["display_name"]})', 'gold')
        elif pnl_pct < -0.3:
            tighter_sl = lp * (1 - (pos['trail_pct'] * 0.3) / 100)
            if tighter_sl > pos['trail_sl']:
                pos['trail_sl'] = tighter_sl
                log_msg(state, f'⚡ Zayıf sinyal → SL sıkıştırıldı: ${tighter_sl:.4f}', 'gold')

# ── ÜST TF TAKİBİ ──

def check_upper_tfs(state, config, pos):
    tf      = pos.get('tf', '1h')
    tfs     = UPPER_TFS_DOUBLE.get(tf, ['2h', '4h'])
    sym     = pos['symbol']
    lp      = pos.get('live_price', pos['entry'])
    pnl_pct = (lp - pos['entry']) / pos['entry'] * 100

    tf1 = analyze_tf(sym, tfs[0])
    tf2 = analyze_tf(sym, tfs[1]) if tfs[1] else None
    if not tf1:
        return

    tf1_txt = f'{tf1["tf"]}: {"💪" if tf1["strong"] else "⚠️"} RSI={tf1["rsi"]:.0f}'
    tf2_txt = f'{tf2["tf"]}: {"💪" if tf2["strong"] else "⚠️"} RSI={tf2["rsi"]:.0f}' if tf2 else '—'
    log_msg(state, f'📊 TF Takip | {tf1_txt} | {tf2_txt}', 'blue')

    now = time.time()
    if now - pos.get('last_upper_action', 0) < 600:
        return

    both_strong = tf1['strong'] and (not tf2 or tf2['strong'])
    both_weak   = tf1['weakening'] and (not tf2 or tf2['weakening'])
    conflict    = (tf1['strong'] and tf2 and tf2['weakening']) or \
                  (tf1['weakening'] and tf2 and tf2['strong'])

    if conflict:
        log_msg(state, f'⏳ TF çelişkisi ({tfs[0]} vs {tfs[1]}) — bekleniyor', 'gold')
        return

    if both_weak and pnl_pct > 0.2:
        pos['last_upper_action'] = now
        safe_tp = lp * 1.002
        pos['target_tp'] = safe_tp
        log_msg(state, f'⚠️ Her iki TF zayıf → TP kısıldı: ${safe_tp:.4f}', 'gold')
    elif both_weak and pnl_pct <= 0:
        pos['last_upper_action'] = now
        tighter_sl = lp * (1 - (pos['trail_pct'] * 0.5) / 100)
        if tighter_sl > pos['trail_sl']:
            pos['trail_sl'] = tighter_sl
            log_msg(state, f'⚠️ Her iki TF zayıf + zarar — SL sıkıştırıldı: ${tighter_sl:.4f}', 'gold')
    elif both_strong and pnl_pct > 0:
        pos['last_upper_action'] = now
        new_tp = pos['target_tp'] * 1.008
        pos['target_tp'] = new_tp
        log_msg(state, f'💪 Her iki TF güçlü → TP yükseltildi: ${new_tp:.4f}', 'up')

# ══════════════════════════════════════════════════
# WEB API — index.html ile konuşur
# ══════════════════════════════════════════════════

_state  = None
_config = None

class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        # index.html'i de serve et
        if self.path in ('/', '/index.html'):
            try:
                with open('/root/arna/index.html', 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)
            except:
                self.send_response(404)
                self.end_headers()
            return

        if self.path in ('/brain', '/brain/state'):
            import urllib.request as ur
            try:
                r = ur.urlopen('http://localhost:8766/brain', timeout=3)
                data = r.read()
            except:
                data = b'{}'
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin','*')
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path in ('/state', '/api/state', '/api'):
            with state_lock:
                data = json.dumps(_state, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        elif self.path == '/config':
            with state_lock:
                data = json.dumps(_config, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global _state, _config
        if self.path in ('/brain', '/brain/state'):
            import urllib.request as ur
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                req = ur.Request('http://localhost:8766/brain', data=body,
                    method='POST', headers={'Content-Type':'application/json'})
                r = ur.urlopen(req, timeout=3)
                data = r.read()
            except:
                data = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*')
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path not in ('/', '/api', '/state'):
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        try:
            cmd = json.loads(body)
        except:
            self.send_response(400)
            self.end_headers()
            return

        with state_lock:
            action = cmd.get('action')

            if action == 'scan':
                if not _state.get('scanning'):
                    threading.Thread(target=scan_market, args=(_state, _config), daemon=True).start()

            elif action == 'set_auto_scan':
                _state['auto_scan'] = cmd.get('value', False)
                save_state(_state)

            elif action == 'set_auto_trade':
                _state['auto_trade'] = cmd.get('value', False)
                save_state(_state)

            elif action == 'set_mode':
                _state['mode'] = cmd.get('value', 'demo')
                save_state(_state)

            elif action == 'set_config':
                _config.update(cmd.get('config', {}))
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(_config, f)
                # Aktif saatler state'e kaydedilir
                if 'active_hours' in cmd.get('config', {}):
                    _state['active_hours'] = cmd['config']['active_hours']
                    save_state(_state)

            elif action == 'open_position':
                sig = cmd.get('signal')
                if sig:
                    threading.Thread(
                        target=open_position,
                        args=(_state, _config, sig),
                        daemon=True
                    ).start()

            elif action == 'close_position':
                pos_id = cmd.get('pos_id')
                pos = next((p for p in _state.get('positions', []) if p['id'] == pos_id), None)
                if pos:
                    price = get_price(pos['symbol']) or pos['live_price']
                    threading.Thread(
                        target=close_position,
                        args=(_state, _config, pos, price, 'MANUAL'),
                        daemon=True
                    ).start()

            elif action == 'tighten_sl':
                pos_id = cmd.get('pos_id')
                new_sl = cmd.get('new_sl')
                pos = next((p for p in _state.get('positions', []) if p['id'] == pos_id), None)
                if pos and new_sl and float(new_sl) > pos['trail_sl']:
                    pos['trail_sl'] = float(new_sl)
                    log_msg(_state, f'⚡ SL sıkıştırıldı (index): ${float(new_sl):.4f} — {pos["display_name"]}', 'gold')
                    save_state(_state)

            elif action == 'set_api_keys':
                _config['api_key']    = cmd.get('api_key', '')
                _config['api_secret'] = cmd.get('api_secret', '')
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(_config, f)
                log_msg(_state, 'API key kaydedildi', 'up')
                save_state(_state)

            elif action == 'sync_state':
                incoming = cmd.get('state', {})
                for key in ['demo_bal', 'demo_pnl', 'mode',
                            'auto_scan', 'auto_trade', 'pos_exit_threshold']:
                    if key in incoming:
                        _state[key] = incoming[key]
                save_state(_state)

            elif action == 'reset_kill_switch':
                _state['kill_switch'] = False
                _state['consecutive_losses'] = 0
                _state['auto_trade'] = True
                log_msg(_state, '✅ Kill Switch sıfırlandı — OTO AL tekrar aktif', 'up')
                save_state(_state)

            elif action == 'reset_demo':
                # Demo bakiye ve geçmişi sıfırla
                _state['demo_bal']  = 2000
                _state['demo_pnl']  = 0
                _state['history']   = []
                _state['positions'] = []
                _state['signals']   = []
                _state['server_activities'] = []
                _state['logs']      = []
                log_msg(_state, '🔄 Demo sıfırlandı — $2000', 'gold')
                save_state(_state)

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

def run_api():
    server = HTTPServer(('0.0.0.0', 8765), APIHandler)
    print('API sunucu başlatıldı: port 8765')
    server.serve_forever()

# ══════════════════════════════════════════════════
# ANA DÖNGÜ
# ══════════════════════════════════════════════════

def main():
    global _state, _config
    print('ARNA Bot başlatıldı — TEK BEYİN modu')
    _state  = load_state()
    _config = load_config()

    # scanning sıfırla, auto ayarları KORU
    _state['scanning'] = False
    if 'auto_trade' not in _state:
        _state['auto_trade'] = False
    if 'auto_scan' not in _state:
        _state['auto_scan'] = False

    bot_start_time = int(time.time() * 1000)
    if 'bot_sessions' not in _state:
        _state['bot_sessions'] = []
    _state['bot_sessions'].insert(0, {
        'start': bot_start_time,
        'start_str': datetime.now().strftime('%d.%m.%Y %H:%M'),
        'end': None, 'duration_min': None
    })
    if len(_state['bot_sessions']) > 50:
        _state['bot_sessions'] = _state['bot_sessions'][:50]

    log_msg(_state, f'ARNA Bot | oto_tara={_state["auto_scan"]} | oto_al={_state["auto_trade"]}', 'up')
    save_state(_state)

    threading.Thread(target=run_api, daemon=True).start()

    scan_interval     = 120
    price_interval    = 5
    upper_tf_interval = 180

    last_scan      = 0
    last_price     = 0
    last_pos_check = 0
    last_upper_tf  = 0

    pos_last_check = {}

    while True:
        try:
            now = time.time()
            _config = load_config() or _config

            # Fiyat takibi — her 5 saniye
            if now - last_price >= price_interval:
                with state_lock:
                    update_positions(_state, _config)
                last_price = now

            # Pozisyon analizi — TF'ye göre
            if now - last_pos_check >= 30:
                with state_lock:
                    for pos in list(_state.get('positions', [])):
                        tf_interval = POS_CHECK_INTERVALS.get(pos.get('tf', '1h'), 60)
                        pid = pos['id']
                        if now - pos_last_check.get(pid, 0) >= tf_interval:
                            try:
                                check_position(_state, _config, pos)
                                pos_last_check[pid] = now
                            except Exception as e:
                                print(f'check_position hata: {e}')
                    save_state(_state)
                last_pos_check = now

            # Üst TF takibi — her 3 dakika
            if now - last_upper_tf >= upper_tf_interval:
                with state_lock:
                    for pos in list(_state.get('positions', [])):
                        try:
                            check_upper_tfs(_state, _config, pos)
                        except Exception as e:
                            print(f'upper_tf hata: {e}')
                    save_state(_state)
                last_upper_tf = now

            # OTO TARA — her 2 dakika
            if _state.get('auto_scan') and not _state.get('scanning'):
                if now - last_scan >= scan_interval:
                    threading.Thread(
                        target=scan_market, args=(_state, _config), daemon=True
                    ).start()
                    last_scan = now

            time.sleep(1)

        except KeyboardInterrupt:
            print('Bot durduruldu')
            break
        except Exception as e:
            print(f'Ana döngü hata: {e}')
            time.sleep(5)

if __name__ == '__main__':
    main()
