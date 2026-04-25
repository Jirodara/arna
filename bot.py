#!/usr/bin/env python3
# ARNA Bot - index.html sisteminin birebir Python karşılığı
# Telefon kapalıyken de 7/24 çalışır

import requests, time, json, hmac, hashlib, threading, math
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── DOSYALAR ──
CONFIG_FILE = '/root/arna_config.json'
STATE_FILE  = '/root/arna_state.json'

def load_config():
    try:
        with open(CONFIG_FILE) as f: return json.load(f)
    except: return {}

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f: json.dump(state, f, ensure_ascii=False)
    except Exception as e: print('save_state hata:', e)

def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except:
        return {
            'demo_bal': 2000, 'demo_pnl': 0,
            'positions': [], 'history': [],
            'signals': [], 'logs': [],
            'scanning': False, 'auto_scan': False,
            'auto_trade': False, 'last_scan': 0,
            'mode': 'demo', 'pos_exit_threshold': -5
        }

def log_msg(state, msg, cls=''):
    t = datetime.now().strftime('%H:%M:%S')
    state['logs'].insert(0, {'time': t, 'msg': msg, 'cls': cls})
    state['logs'] = state['logs'][:200]
    print(f"[{t}] {msg}")

# ── BİNANCE API ──
BASES = [
    'https://data-api.binance.vision/api/v3',
    'https://api.binance.com/api/v3',
    'https://api1.binance.com/api/v3',
]

def binance_get(path, params=None):
    for base in BASES:
        try:
            r = requests.get(base + path, params=params, timeout=10)
            if r.ok: return r.json()
        except: continue
    return None

def binance_signed(method, path, params, api_key, api_secret):
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = 5000
    query = '&'.join(f'{k}={v}' for k,v in params.items())
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    query += f'&signature={sig}'
    headers = {'X-MBX-APIKEY': api_key}
    url = 'https://api.binance.com/api/v3' + path
    try:
        if method == 'GET':
            r = requests.get(url + '?' + query, headers=headers, timeout=10)
        else:
            r = requests.post(url, data=query, headers=headers, timeout=10)
        return r.json()
    except Exception as e: return {'error': str(e)}

def get_price(symbol):
    d = binance_get('/ticker/price', {'symbol': symbol})
    return float(d['price']) if d and 'price' in d else None

def get_klines(symbol, interval, limit=60):
    d = binance_get('/klines', {'symbol': symbol, 'interval': interval, 'limit': limit})
    if not d: return []
    return [{'o':float(k[1]),'h':float(k[2]),'l':float(k[3]),'c':float(k[4]),'v':float(k[5])} for k in d]

# ── TEKNİK GÖSTERGELER ── (index.html ile birebir)

def sma(values, n):
    if len(values) < n: return values[-1] if values else 0
    return sum(values[-n:]) / n

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return 100 - 100 / (1 + ag/al)

def calc_macd(closes):
    if len(closes) < 26: return {'line':0,'sig':0,'hist':0}
    def ema(data, n):
        k = 2/(n+1); e = data[0]
        for v in data[1:]: e = v*k + e*(1-k)
        return e
    line = ema(closes, 12) - ema(closes, 26)
    sig  = line * 0.2
    return {'line': line, 'sig': sig, 'hist': line - sig}

def boll_calc(closes):
    p = 20
    if len(closes) < p:
        v = closes[-1] if closes else 1
        return {'up': v*1.02, 'mid': v, 'dn': v*0.98}
    sl  = closes[-p:]
    mid = sum(sl) / p
    std = math.sqrt(sum((x-mid)**2 for x in sl) / p)
    return {'up': mid+2*std, 'mid': mid, 'dn': mid-2*std}

def calc_vwap(klines):
    k = klines[-20:] if len(klines) >= 20 else klines
    num = sum(((x['h']+x['l']+x['c'])/3) * x['v'] for x in k)
    den = sum(x['v'] for x in k)
    return num/den if den > 0 else 0

def swing_low(klines, n=10):
    k = klines[-(n+1):-1]
    return min(x['l'] for x in k) if k else None

# ── FİLTRELER (index.html birebir) ──

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
            if wick_ratio > 2.5: fake_count += 1
    return fake_count >= 2

def is_peak_recovery(klines, price, high24h):
    if high24h <= 0: return {'is_peak': False}
    drop_from_high = (high24h - price) / high24h * 100
    if drop_from_high < 10: return {'is_peak': False, 'drop': drop_from_high}
    recent_high = max(k['h'] for k in klines[-5:]) if klines else price
    dist_from_recent = (recent_high - price) / recent_high * 100
    if dist_from_recent > 5:
        return {'is_peak': True, 'drop': drop_from_high}
    return {'is_peak': False, 'drop': drop_from_high}

def trend_filt(closes):
    ma7  = sma(closes, 7)
    ma30 = sma(closes, 30)
    mc   = calc_macd(closes)
    up_trend  = ma7 > ma30
    macd_up   = mc['line'] > mc['sig']
    score = 30 if (up_trend and macd_up) else 15 if up_trend else 0
    return {'pass': up_trend and macd_up, 'score': score,
            'ma7': ma7, 'ma30': ma30,
            'macd_line': mc['line'], 'macd_sig': mc['sig'], 'macd_hist': mc['hist']}

def rsi_filt(closes):
    r = calc_rsi(closes, 14)
    if r >= 50 and r <= 70:   return {'pass': True,  'rsi': r, 'score': 15}
    elif r > 45 and r < 50:   return {'pass': True,  'rsi': r, 'score': 6}
    elif r < 45:               return {'pass': False, 'rsi': r, 'score': 0}
    elif r > 75:               return {'pass': False, 'rsi': r, 'score': 0}
    else:                      return {'pass': True,  'rsi': r, 'score': 4}

def boll_filt(closes, price):
    b   = boll_calc(closes)
    pct = (price - b['dn']) / (b['up'] - b['dn'] + 0.0001)
    if price > b['mid'] and pct < 0.85: return {'pass': True,  'score': 15, 'pos': pct, **b}
    elif price > b['mid']:              return {'pass': True,  'score': 8,  'pos': pct, **b}
    else:                               return {'pass': False, 'score': 0,  'pos': pct, **b}

def vol_filt(klines):
    closed = klines[:-1]
    vols   = [k['v'] for k in closed]
    if len(vols) < 12: return {'pass': False, 'score': 0, 'ratio': 0}
    avg5  = sum(vols[-5:]) / 5
    avg3  = sum(vols[-3:]) / 3
    ma5   = sma(vols[:-5], 5)
    ma10  = sma(vols[:-3], 10)
    r10   = avg5/ma10 if ma10 > 0 else 0
    r5    = avg5/ma5  if ma5  > 0 else 0
    if avg5 < ma10: return {'pass': False, 'score': 0, 'ratio': r10}
    if avg5 < ma5:  return {'pass': False, 'score': 0, 'ratio': r10}
    if avg3 < ma10 * 0.8: return {'pass': False, 'score': 0, 'ratio': r10}
    last3v = vols[-3:]
    rising2 = vols[-1] > vols[-2] > vols[-3] if len(vols) >= 3 else False
    all_rising3 = len(last3v)==3 and last3v[2]>last3v[1]>last3v[0]
    score = 18 if r10 >= 2.0 else 14 if r10 >= 1.5 else 10 if r10 >= 1.0 else 0
    if all_rising3: score = min(20, score+3)
    elif rising2:   score = min(20, score+1)
    if avg5 > ma5:  score = min(20, score+1)
    return {'pass': True, 'score': score, 'ratio': r10}

def price_score(change):
    if 3 <= change <= 5: return 20
    if 2 <= change < 3:  return 12
    if 5 < change <= 7:  return 10
    return 4

def total_score(tr, rsi, bol, vol, chg):
    s = tr['score'] + rsi['score'] + bol['score'] + (vol['score'] if vol['pass'] else 0) + price_score(chg)
    return max(0, min(100, s))

def calc_tp(price, score):
    pct = 4.0 if score>=90 else 3.0 if score>=85 else 2.0 if score>=75 else 1.5
    return {'tp': price*(1+pct/100), 'pct': pct}

# ── ÜST TF ANALİZİ ──
UPPER_TF_MAP = {
    '1m':'3m','3m':'5m','5m':'15m','15m':'30m',
    '30m':'1h','1h':'2h','2h':'4h','4h':'6h'
}
UPPER_TFS_DOUBLE = {
    '1m':['3m','15m'],'3m':['15m','1h'],'5m':['15m','1h'],
    '15m':['30m','1h'],'30m':['1h','2h'],'1h':['2h','4h'],
    '2h':['4h','6h'],'4h':['6h',None]
}

def analyze_tf(symbol, tf):
    if not tf: return None
    try:
        klines = get_klines(symbol, tf, 40)
        if len(klines) < 15: return None
        closes = [k['c'] for k in klines]
        tr  = trend_filt(closes)
        vol = vol_filt(klines)
        rsi = calc_rsi(closes, 14)
        mc  = calc_macd(closes)
        strong    = tr['pass'] and vol['pass'] and 45 <= rsi <= 75
        weakening = (not tr['pass'] and not vol['pass']) or rsi > 78 or (rsi < 40 and not tr['pass'])
        return {'tf': tf, 'strong': strong, 'weakening': weakening,
                'trend_pass': tr['pass'], 'vol_pass': vol['pass'],
                'rsi': rsi, 'macd_hist': mc['hist'],
                'ma7': tr['ma7'], 'ma30': tr['ma30']}
    except: return None

# ── ÖĞRENME SİSTEMİ ──
LEARN_FILE = '/root/arna_learn.json'

def load_learn():
    try:
        with open(LEARN_FILE) as f: return json.load(f)
    except:
        return {
            'total_trades': 0, 'wins': 0,
            'trades': [],
            'weights': {'trend':1.0,'rsi':1.0,'vol':1.0,'boll':1.0},
            'tf_stats': {},
            'pos_exit_threshold': -5
        }

def save_learn(learn):
    try:
        with open(LEARN_FILE, 'w') as f: json.dump(learn, f)
    except: pass

def update_weights(learn):
    trades = learn['trades']
    if len(trades) < 5: return
    keys = ['trend','rsi','vol','boll']
    for k in keys:
        with_ind    = [t for t in trades if t.get(k)==1]
        without_ind = [t for t in trades if t.get(k)==0]
        if len(with_ind) < 3: continue
        wr_with    = sum(1 for t in with_ind if t['result']=='win') / len(with_ind)
        wr_without = sum(1 for t in without_ind if t['result']=='win') / len(without_ind) if without_ind else 0
        if wr_with > wr_without + 0.1:
            learn['weights'][k] = min(1.5, learn['weights'][k] + 0.05)
        elif wr_with < wr_without - 0.1:
            learn['weights'][k] = max(0.5, learn['weights'][k] - 0.05)

def apply_learning(score, tr, rsi, bol, vol, learn):
    if learn['total_trades'] < 5: return score
    w = learn['weights']
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
    if result == 'win': learn['wins'] += 1
    hour = datetime.now().hour
    session = 'EU' if 9<=hour<17 else 'US' if 17<=hour<23 else 'ASIA'
    entry = {
        'result': result, 'trend': 1 if tr_pass else 0,
        'rsi': 1 if rsi_ok else 0, 'vol': 1 if vol_ok else 0,
        'boll': 1 if boll_ok else 0, 'pnl_pct': pnl_pct,
        'tf': tf, 'hour': hour, 'session': session,
        'ts': int(time.time()*1000)
    }
    learn['trades'].append(entry)
    if len(learn['trades']) > 200: learn['trades'].pop(0)
    update_weights(learn)
    # TF stats
    if 'tf_stats' not in learn: learn['tf_stats'] = {}
    if tf not in learn['tf_stats']: learn['tf_stats'][tf] = {'wins':0,'losses':0,'total_pnl':0}
    if result == 'win': learn['tf_stats'][tf]['wins'] += 1
    else: learn['tf_stats'][tf]['losses'] += 1
    learn['tf_stats'][tf]['total_pnl'] += pnl_pct
    save_learn(learn)

# ── MUM KAPANIŞI KONTROLÜ (waitForCandleClose birebir) ──
def check_candle_timing(symbol, tf):
    """Mum son %15'inde veya yeni açıldıysa True, ortasındaysa False döner"""
    try:
        klines_raw = binance_get('/klines', {'symbol': symbol, 'interval': tf, 'limit': 2})
        if not klines_raw or len(klines_raw) < 2: return True, 0
        open_candle = klines_raw[-1]
        candle_open_time  = open_candle[0]
        candle_close_time = open_candle[6]
        now_ms = int(time.time() * 1000)
        candle_duration = candle_close_time - candle_open_time
        elapsed = now_ms - candle_open_time
        elapsed_pct = elapsed / candle_duration
        remaining_min = round((candle_close_time - now_ms) / 60000)
        # Son %15 veya ilk %5 → direkt gir
        if elapsed_pct >= 0.85 or elapsed_pct <= 0.05:
            return True, remaining_min
        return False, remaining_min
    except: return True, 0

def check_and_enter(state, config, signal, learn):
    """Mum kapandıktan sonra 4 kontrol yap, geçerse giriş yap"""
    try:
        klines_raw = binance_get('/klines', {'symbol': signal['symbol'], 'interval': signal['tf'], 'limit': 4})
        if not klines_raw or len(klines_raw) < 3: 
            open_position(state, config, signal, learn); return
        new_candle  = klines_raw[-1]
        prev_candle = klines_raw[-2]
        new_open    = float(new_candle[1])
        prev_close  = float(prev_candle[4])
        closes = [float(k[4]) for k in klines_raw]
        new_rsi = calc_rsi(closes, min(14, len(closes)-1))

        # 1. Fiyat kayması ±%1
        price_diff = abs(new_open - signal['price']) / signal['price'] * 100
        if price_diff > 1:
            log_msg(state, f'❌ OTO: {signal["display_name"]} fiyat kayması ({price_diff:.2f}%) — iptal', 'gold')
            return

        # 2. Yeni mum yeşil
        if new_open < prev_close * 0.999:
            log_msg(state, f'❌ OTO: {signal["display_name"]} yeni mum kırmızı — iptal', 'gold')
            return

        # 3. RSI hala iyi
        if new_rsi < 40 or new_rsi > 78:
            log_msg(state, f'❌ OTO: {signal["display_name"]} RSI bozuldu ({new_rsi:.0f}) — iptal', 'gold')
            return

        # 4. Hacim çökmüş mü
        vols = [float(k[5]) for k in klines_raw]
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

# ── MOMENTUM TAKİBİ ──
def check_momentum_exit(state, config, pos, momentum_prices):
    """checkMomentumExit birebir — TP geçildiyse momentum takip et"""
    if len(momentum_prices) < 10: return
    lp = pos.get('live_price', pos['entry'])
    pnl_pct = (lp - pos['entry']) / pos['entry'] * 100
    if pnl_pct < pos['tp_pct'] * 0.6: return

    recent5 = sum(momentum_prices[-5:]) / 5
    older5  = sum(momentum_prices[-10:-5]) / 5 if len(momentum_prices) >= 10 else recent5
    momentum = (recent5 - older5) / older5 * 100 if older5 > 0 else 0

    now = time.time()
    last_cd = pos.get('_momentum_cd', 0)
    if now - last_cd > 30:
        if momentum < -0.2:
            pos['_momentum_cd'] = now
            pnl = (lp - pos['entry']) / pos['entry'] * pos['pos_usd']
            smart_tp = lp * 1.003
            if smart_tp < pos['target_tp']:
                pos['target_tp'] = smart_tp
                log_msg(state, f'📉 Momentum kırıldı → TP sıkıştırıldı: ${smart_tp:.4f} (kar: +${pnl:.2f})', 'gold')
        elif momentum > 0.5 and pnl_pct > pos['tp_pct']:
            pos['_momentum_cd'] = now
            new_tp = pos['target_tp'] * 1.008
            if new_tp > pos['target_tp']:
                pos['target_tp'] = new_tp
                log_msg(state, f'🚀 Momentum güçlü → TP yükseltildi: ${new_tp:.4f}', 'up')
blacklist = {}  # {symbol: {'ts': time, 'permanent': bool}}

def clean_blacklist():
    now = time.time()
    to_del = []
    for sym, entry in blacklist.items():
        ttl = 600 if entry.get('permanent') else 120
        if now - entry['ts'] > ttl: to_del.append(sym)
    for sym in to_del: del blacklist[sym]

def add_blacklist(sym, permanent=False):
    blacklist[sym] = {'ts': time.time(), 'permanent': permanent}

# ── TARAMA ──
STABLECOINS = {'BUSDUSDT','USDCUSDT','TUSDUSDT','FDUSDUSDT','DAIUSDT','USDPUSDT','EURUSDT','GBPUSDT'}

def scan_market(state, config):
    learn = load_learn()
    log_msg(state, 'Tarama basladi...', 'blue')
    state['scanning'] = True
    save_state(state)
    tf        = config.get('tf', '5m')
    min_score = int(config.get('min_score', 80))
    try:
        tickers = binance_get('/ticker/24hr')
        if not tickers: log_msg(state, 'Binance baglanti hatasi', 'dn'); return
        try:
            usd_try = float(binance_get('/ticker/price', {'symbol':'USDTTRY'})['price'])
        except: usd_try = 38.5
        log_msg(state, f'USD/TRY kuru: {usd_try:.2f}', 'blue')

        # Ön filtre
        cands = []
        for t in tickers:
            sym = t['symbol']
            if not (sym.endswith('USDT') or sym.endswith('TRY')): continue
            if sym in STABLECOINS: continue
            vol_usd = float(t['quoteVolume'])
            if sym.endswith('TRY'): vol_usd /= usd_try
            if vol_usd < 5_000_000: continue
            if int(t['count']) < 10000: continue
            price = float(t['lastPrice'])
            if sym.endswith('TRY'): price /= usd_try
            high24h = float(t['highPrice'])
            if sym.endswith('TRY'): high24h /= usd_try
            cands.append({'symbol':sym,'price':price,'change':float(t['priceChangePercent']),
                          'volume':vol_usd,'high24h':high24h,'raw_vol':t['quoteVolume']})

        cands.sort(key=lambda x: x['volume'], reverse=True)
        log_msg(state, f'Filtre 1: {len(cands)} coin tum piyasa', 'blue')

        clean_blacklist()
        results = []
        prev_signals = {s['symbol']: s for s in state.get('signals', [])}

        for c in cands:
            sym = c['symbol']

            # Kara liste
            if sym in blacklist: continue

            try:
                klines = get_klines(sym, tf, 60)
                if len(klines) < 32: continue
                closes = [k['c'] for k in klines]
                price  = c['price']
                change = c['change']

                # Üst TF filtresi
                upper_tf = UPPER_TF_MAP.get(tf)
                if upper_tf:
                    u_klines = get_klines(sym, upper_tf, 35)
                    if u_klines and len(u_klines) >= 30:
                        u_closes = [k['c'] for k in u_klines]
                        u_tr = trend_filt(u_closes)
                        ma7u  = sma(u_closes, 7)
                        ma25u = sma(u_closes, 25)
                        strong_down = not u_tr['pass'] and u_tr['macd_hist'] < 0 and ma7u < ma25u * 0.99
                        if strong_down:
                            base = sym.replace('USDT','').replace('TRY','')
                            add_blacklist(base+'USDT', False)
                            add_blacklist(base+'TRY', False)
                            add_blacklist(sym, False)
                            log_msg(state, f'🚫 {base[:6]} — ust TF ({upper_tf}) asagi', 'gold')
                            continue

                # Zirve kontrolü
                peak = is_peak_recovery(klines, price, c['high24h'])
                if peak['is_peak']:
                    add_blacklist(sym, True)
                    log_msg(state, f'🚫 {sym[:8]} — zirve', 'gold')
                    continue

                # Fitil tespiti
                if has_fake_wick(klines, 5):
                    add_blacklist(sym, True)
                    log_msg(state, f'🚫 {sym[:8]} — fitil', 'gold')
                    continue

                # Filtreler
                tr   = trend_filt(closes)
                rsiR = rsi_filt(closes)
                bol  = boll_filt(closes, price)
                vol  = vol_filt(klines)

                # RSI kontrolü
                if rsiR['rsi'] < 35:
                    add_blacklist(sym, False)
                    log_msg(state, f'🚫 {sym[:8]} — RSI dusuk ({rsiR["rsi"]:.0f})', 'gold')
                    continue

                # Hacim kontrolü
                if not vol['pass'] and vol['ratio'] < 0.5:
                    add_blacklist(sym, False)
                    log_msg(state, f'🚫 {sym[:8]} — hacimsiz', 'gold')
                    continue

                sc = total_score(tr, rsiR, bol, vol, change)
                sc = apply_learning(sc, tr, rsiR, bol, vol, learn)  # Öğrenme ağırlıkları uygula
                sl = swing_low(klines, 10)
                tp = calc_tp(price, sc)

                # ★ MUM KAPANIŞI KONTROLÜ (4h ve 1d için)
                if tf in ('4h', '1d'):
                    candle_ok, remaining_min = check_candle_timing(sym, tf)
                    if not candle_ok:
                        log_msg(state, f'⏳ {sym[:8]} — mum ortasi ({remaining_min}dk kaldi)', 'gold')
                        continue

                if sc >= min_score:
                    display = sym.replace('USDT','').replace('TRY','')
                    results.append({
                        'symbol': sym, 'display_name': display,
                        'price': price, 'change': change, 'volume': c['volume'],
                        'score': sc, 'verdict': 'BUY' if sc >= min_score else 'WATCH',
                        'rsi': round(rsiR['rsi'], 1),
                        'trend_pass': tr['pass'], 'rsi_ok': rsiR['pass'],
                        'vol_ok': vol['pass'], 'boll_ok': bol['pass'],
                        'target_tp': tp['tp'], 'tp_pct': tp['pct'],
                        'swing_low': sl, 'tf': tf,
                        'scanned_at': int(time.time() * 1000)
                    })
            except Exception as e:
                continue

        # Duplicate temizle (USDT+TRY)
        best_by_base = {}
        for r in results:
            base = r['display_name']
            if base not in best_by_base or r['score'] > best_by_base[base]['score']:
                best_by_base[base] = r
        final = sorted(best_by_base.values(), key=lambda x: x['score'], reverse=True)

        # Son 1 saatte SL ile kapanan coinleri filtrele
        bad_coins = {h['symbol'] for h in state.get('history',[])
                     if h.get('result')=='lose' and h.get('exit_reason')=='SL'
                     and time.time()*1000 - h.get('exit_time',0) < 3600000}
        if bad_coins:
            before = len(final)
            final = [r for r in final if r['symbol'] not in bad_coins]
            if before > len(final):
                log_msg(state, f'🚫 Son 1 saatte SL ile kapanan {before-len(final)} coin filtrelendi', 'gold')

        # Yüksek skorlu eski sinyaller koru (82+, 4dk)
        MIN_KEEP = 82
        kept = []
        for prev in prev_signals.values():
            if any(r['symbol']==prev['symbol'] for r in final): continue
            if blacklist.get(prev['symbol'],{}).get('permanent'): continue
            age = time.time()*1000 - prev.get('scanned_at', 0)
            if prev['score'] >= MIN_KEEP and age < 240000:
                kept.append({**prev, '_kept': True})
        if kept:
            log_msg(state, f'📌 {len(kept)} yuksek skorlu sinyal korundi', 'blue')
            final = sorted(final + kept, key=lambda x: x['score'], reverse=True)

        state['signals'] = final
        buy_n = sum(1 for s in final if s['verdict']=='BUY')
        log_msg(state, f'Tarama tamam: {len(final)} sinyal | {buy_n} AL', 'up' if final else 'gold')

        # OTO AL/SAT - mum kapanışı bekle
        if state.get('auto_trade') and final and not state.get('positions'):
            best = final[0]
            candle_ok, remaining_min = check_candle_timing(best['symbol'], best['tf'])
            if candle_ok:
                open_position(state, config, best, learn)
            else:
                log_msg(state, f'⏳ OTO: {best["display_name"]} mum kapanisi bekleniyor ({remaining_min}dk)', 'gold')
                # remaining_min dakika sonra tekrar kontrol et
                def delayed_enter(sig, rem):
                    time.sleep(rem * 60 + 5)
                    with state_lock:
                        if state.get('auto_trade') and not state.get('positions'):
                            check_and_enter(state, config, sig, learn)
                threading.Thread(target=delayed_enter, args=(best, remaining_min), daemon=True).start()

    except Exception as e:
        log_msg(state, f'Tarama hata: {str(e)[:60]}', 'dn')
    finally:
        state['scanning'] = False
        state['last_scan'] = int(time.time())
        save_state(state)

# ── POZİSYON YÖNETİMİ ──

def open_position(state, config, signal, learn=None):
    if learn is None: learn = load_learn()
    mode    = state.get('mode', 'demo')
    pos_usd = float(config.get('pos_usd', 400))
    trail_pct = float(config.get('trail_pct', 1.5))
    commission = pos_usd * 0.001

    if state.get('positions') and len(state['positions']) >= 2:
        log_msg(state, 'Max pozisyon sayisina ulasildi (2)', 'gold'); return
    if any(p['symbol'] == signal['symbol'] for p in state.get('positions',[])):
        log_msg(state, f'{signal["display_name"]} zaten acik', 'gold'); return

    if mode == 'live':
        cfg = load_config()
        order = binance_signed('POST', '/order',
            {'symbol': signal['symbol'], 'side': 'BUY', 'type': 'MARKET',
             'quoteOrderQty': round(pos_usd, 2)},
            cfg.get('api_key',''), cfg.get('api_secret',''))
        if 'orderId' not in order:
            log_msg(state, f'Emir hatasi: {order.get("msg","?")}', 'dn'); return
        qty   = float(order['executedQty'])
        entry = float(order['cummulativeQuoteQty']) / qty
    else:
        qty   = (pos_usd - commission) / signal['price']
        entry = signal['price']

    tp_pct    = signal.get('tp_pct', 1.5)
    sl_price  = signal.get('swing_low')
    trail_sl  = entry * (1 - trail_pct/100)
    swing_diff= (entry - sl_price) / entry * 100 if sl_price else 999
    init_sl   = max(sl_price, trail_sl) if (sl_price and swing_diff <= 3) else trail_sl

    pos = {
        'id': int(time.time()*1000),
        'symbol': signal['symbol'],
        'display_name': signal['display_name'],
        'entry': entry, 'live_price': entry,
        'qty': qty, 'pos_usd': pos_usd - commission,
        'gross_usd': pos_usd, 'commission': commission,
        'trail_pct': trail_pct, 'trail_sl': init_sl, 'init_sl': init_sl,
        'target_tp': entry * (1 + tp_pct/100), 'tp_pct': tp_pct,
        'score': signal['score'], 'tf': signal['tf'],
        'entry_time': int(time.time()*1000),
        'is_live': mode == 'live',
        'trend_pass': signal.get('trend_pass', False),
        'rsi_ok': signal.get('rsi_ok', False),
        'vol_ok': signal.get('vol_ok', False),
        'boll_ok': signal.get('boll_ok', False),
        'last_upper_action': 0,
        'pos_check_decisions': []
    }

    if mode != 'live': state['demo_bal'] -= pos_usd
    if 'positions' not in state: state['positions'] = []
    state['positions'].append(pos)
    state['history'].insert(0, {**pos, 'result': 'open', 'pnl': None})
    log_msg(state, f'✅ GIRIS: {pos["display_name"]} @ ${entry:.4f} | ${pos_usd:.0f} | SL ${init_sl:.4f}', 'up')
    save_state(state)

def close_position(state, config, pos, exit_price, reason, learn=None):
    if learn is None: learn = load_learn()
    commission = exit_price * pos['qty'] * 0.001
    pnl = (exit_price - pos['entry']) / pos['entry'] * pos['pos_usd'] - commission
    mode = state.get('mode', 'demo')
    commission = exit_price * pos['qty'] * 0.001
    pnl = (exit_price - pos['entry']) / pos['entry'] * pos['pos_usd'] - commission
    mode = state.get('mode', 'demo')

    if pos.get('is_live') and mode == 'live':
        cfg = load_config()
        binance_signed('POST', '/order',
            {'symbol': pos['symbol'], 'side': 'SELL', 'type': 'MARKET',
             'quantity': round(pos['qty'], 6)},
            cfg.get('api_key',''), cfg.get('api_secret',''))
        # Bakiye güncelle
        try:
            acc = binance_signed('GET', '/account', {}, cfg.get('api_key',''), cfg.get('api_secret',''))
            usdt = next((b for b in acc.get('balances',[]) if b['asset']=='USDT'), None)
            if usdt: state['demo_bal'] = float(usdt['free'])
        except: pass
    else:
        state['demo_bal'] += pos['pos_usd'] + pnl
        state['demo_pnl'] += pnl

    state['positions'] = [p for p in state['positions'] if p['id'] != pos['id']]
    rec = next((h for h in state['history'] if h['id'] == pos['id']), None)
    if rec:
        rec.update({'result': 'win' if pnl >= 0 else 'lose',
                    'pnl': round(pnl, 2), 'exit_price': exit_price,
                    'exit_reason': reason, 'exit_time': int(time.time()*1000)})
    icon = '💰' if pnl >= 0 else '⚠️'
    log_msg(state, f'{icon} CIKIS ({reason}): {pos["display_name"]} | {pnl:+.2f}$', 'up' if pnl >= 0 else 'dn')
    record_trade(learn, 'win' if pnl>=0 else 'lose',
                 pos.get('trend_pass',False), pos.get('rsi_ok',False),
                 pos.get('vol_ok',False), pos.get('boll_ok',False),
                 (pnl/pos['pos_usd'])*100, pos.get('tf','?'))
    save_state(state)

# ── POZİSYON ANALİZİ (checkPosition birebir) ──

def check_position(state, config, pos):
    sym = pos['symbol']
    tf  = pos.get('tf', '1h')
    lp  = pos.get('live_price', pos['entry'])
    pnl_pct = (lp - pos['entry']) / pos['entry'] * 100

    klines = get_klines(sym, tf, 30)
    if len(klines) < 20: return
    closes = [k['c'] for k in klines]
    vols   = [k['v'] for k in klines]

    # 1. Mum analizi
    last5 = klines[-6:-1]
    green_count = sum(1 for k in last5 if k['c'] > k['o'])
    red_count   = sum(1 for k in last5 if k['c'] <= k['o'])
    last_candle = last5[-1]
    body  = abs(last_candle['c'] - last_candle['o'])
    rng   = last_candle['h'] - last_candle['l']
    wick_ratio   = (rng - body) / rng if rng > 0 else 0
    has_long_wick = wick_ratio > 0.6

    # 2. Hacim trendi
    vol_now  = sum(vols[-3:]) / 3
    vol_prev = sum(vols[-8:-3]) / 5 if len(vols) >= 8 else vol_now
    vol_rising  = vol_now > vol_prev * 1.1
    vol_falling = vol_now < vol_prev * 0.8

    # 3. RSI
    rsi = calc_rsi(closes, 14)
    rsi_ok         = 45 <= rsi <= 72
    rsi_overbought = rsi > 75
    rsi_weak       = rsi < 45

    # 4. MACD
    macd = calc_macd(closes)
    macd_positive   = macd['hist'] > 0
    macd_divergence = macd['hist'] < 0 and macd['line'] > 0

    # 5. Bollinger
    boll = boll_calc(closes)
    boll_pct     = (lp - boll['dn']) / (boll['up'] - boll['dn'] + 0.0001)
    near_upper   = boll_pct > 0.85
    above_mid    = boll_pct > 0.5

    # 6. VWAP
    vwap_data = klines[-20:]
    vwap_num  = sum(((k['h']+k['l']+k['c'])/3) * k['v'] for k in vwap_data)
    vwap_den  = sum(k['v'] for k in vwap_data)
    vwap      = vwap_num / vwap_den if vwap_den > 0 else lp
    above_vwap = lp > vwap
    vwap_diff  = (lp - vwap) / vwap * 100

    # Skor
    score   = 0
    reasons = []
    if green_count >= 3:  score += 2
    if vol_rising:        score += 2; reasons.append('hacim artiyor')
    if rsi_ok:            score += 2
    if macd_positive:     score += 2
    if above_mid:         score += 1
    if above_vwap:        score += 2; reasons.append('VWAP ustu')
    if red_count >= 4:    score -= 3; reasons.append('mumlar kirmizi')
    if has_long_wick:     score -= 2; reasons.append('uzun fitil')
    if vol_falling:       score -= 2; reasons.append('hacim dusuyor')
    if rsi_overbought:    score -= 3; reasons.append('RSI asiri alim')
    if rsi_weak:          score -= 2; reasons.append('RSI zayif')
    if macd_divergence:   score -= 2; reasons.append('MACD zayifliyor')
    if near_upper:        score -= 2; reasons.append('ust banda dayandi')
    if not above_vwap:    score -= 2; reasons.append('VWAP alti')
    if pnl_pct > 1.5:     score += 1
    if pnl_pct > 2.5:     score += 1

    threshold = state.get('pos_exit_threshold', -5)

    if score >= 4:
        decision = 'Devam Et'; action = 'hold'
    elif score > threshold:
        decision = 'Dikkatli Ol'; action = 'tighten'
    else:
        decision = 'Cik Onerilir'; action = 'exit'

    # Karar değişince log at
    last_dec = pos.get('pos_check_decisions', [{}])[-1].get('decision') if pos.get('pos_check_decisions') else None
    if last_dec != decision:
        icon = '🟢' if action=='hold' else '🟡' if action=='tighten' else '🔴'
        reason_txt = ' · ' + ', '.join(reasons[:2]) if reasons else ''
        log_msg(state, f'{icon} Pozisyon: {decision} (skor:{score}) RSI:{rsi:.0f} MACD:{"↑" if macd_positive else "↓"}{reason_txt}',
                'up' if action=='hold' else 'gold' if action=='tighten' else 'dn')

    if 'pos_check_decisions' not in pos: pos['pos_check_decisions'] = []
    pos['pos_check_decisions'].append({'time': int(time.time()*1000), 'decision': decision, 'action': action, 'score': score})
    if len(pos['pos_check_decisions']) > 20: pos['pos_check_decisions'].pop(0)

    # Eylem
    if action == 'hold' and score >= 6 and pnl_pct > 0.5:
        hold_sl = pos['entry'] + (lp - pos['entry']) * 0.40
        if hold_sl > pos['trail_sl']:
            pos['trail_sl'] = hold_sl
            log_msg(state, f'🟢 Devam Et → SL yukari: ${hold_sl:.4f}', 'up')

    elif action == 'tighten':
        tighter_sl = lp * (1 - (pos['trail_pct'] * 0.5) / 100)
        if tighter_sl > pos['trail_sl']:
            pos['trail_sl'] = tighter_sl
            log_msg(state, f'⚡ SL sikistirildi: ${tighter_sl:.4f}', 'gold')

    elif action == 'exit':
        pnl = (lp - pos['entry']) / pos['entry'] * pos['pos_usd']
        if pnl_pct > 0.3:
            close_position(state, config, pos, lp, 'ANALYSIS')
            return
        elif pnl_pct < -0.3:
            tighter_sl = lp * (1 - (pos['trail_pct'] * 0.3) / 100)
            if tighter_sl > pos['trail_sl']:
                pos['trail_sl'] = tighter_sl
                log_msg(state, f'⚡ Zayif sinyal → SL sikistirildi: ${tighter_sl:.4f}', 'gold')

# ── ÜST TF TAKİBİ ──

def check_upper_tfs(state, config, pos):
    tf  = pos.get('tf', '1h')
    tfs = UPPER_TFS_DOUBLE.get(tf, ['2h', '4h'])
    sym = pos['symbol']
    lp  = pos.get('live_price', pos['entry'])
    pnl_pct = (lp - pos['entry']) / pos['entry'] * 100

    tf1 = analyze_tf(sym, tfs[0])
    tf2 = analyze_tf(sym, tfs[1]) if tfs[1] else None

    if not tf1: return

    tf1_txt = f'{tf1["tf"]}: {"💪" if tf1["strong"] else "⚠️"} RSI={tf1["rsi"]:.0f}'
    tf2_txt = f'{tf2["tf"]}: {"💪" if tf2["strong"] else "⚠️"} RSI={tf2["rsi"]:.0f}' if tf2 else '—'
    log_msg(state, f'📊 TF Takip | {tf1_txt} | {tf2_txt}', 'blue')

    now = time.time()
    if now - pos.get('last_upper_action', 0) < 600: return

    both_strong   = tf1['strong'] and (not tf2 or tf2['strong'])
    both_weak     = tf1['weakening'] and (not tf2 or tf2['weakening'])
    conflict      = (tf1['strong'] and tf2 and tf2['weakening']) or (tf1['weakening'] and tf2 and tf2['strong'])

    if conflict:
        log_msg(state, f'⏳ TF celiskisi ({tfs[0]} vs {tfs[1]}) — bekleniyor', 'gold'); return

    if both_weak and pnl_pct > 0.2:
        pos['last_upper_action'] = now
        safe_tp = lp * 1.002
        pos['target_tp'] = safe_tp
        log_msg(state, f'⚠️ Her iki TF zayif → TP kisaldi: ${safe_tp:.4f}', 'gold')

    elif both_weak and pnl_pct <= 0:
        pos['last_upper_action'] = now
        tighter_sl = lp * (1 - (pos['trail_pct'] * 0.5) / 100)
        if tighter_sl > pos['trail_sl']:
            pos['trail_sl'] = tighter_sl
            log_msg(state, f'⚠️ Her iki TF zayif + zarar — SL sikistirildi: ${tighter_sl:.4f}', 'gold')

    elif both_strong and pnl_pct > 0:
        pos['last_upper_action'] = now
        new_tp = pos['target_tp'] * 1.008
        pos['target_tp'] = new_tp
        log_msg(state, f'💪 Her iki TF guclu → TP yukseldi: ${new_tp:.4f}', 'up')

# ── FİYAT TAKİBİ ──

def update_positions(state, config):
    if not state.get('positions'): return False
    learn = load_learn()
    changed = False
    for pos in list(state['positions']):
        price = get_price(pos['symbol'])
        if not price: continue
        last_price = pos.get('_last_price', 0)
        pos['live_price'] = price
        changed = True

        # Trailing SL güncelle
        new_sl = price * (1 - pos['trail_pct'] / 100)
        if new_sl > pos['trail_sl']:
            pos['trail_sl'] = new_sl

        # BEP kilidi: TP'nin %50'sine ulaşınca SL giriş fiyatına çek
        tp_pct  = pos['tp_pct']
        pnl_pct = (price - pos['entry']) / pos['entry'] * 100
        if pnl_pct >= tp_pct * 0.5 and pos['trail_sl'] < pos['entry']:
            pos['trail_sl'] = pos['entry'] * 1.002
            log_msg(state, f'🔒 BEP kilidi: SL girise cekidi ({pos["display_name"]})', 'gold')

        # TP kilidi: TP'ye ulaşınca kârın %60'ını koru
        if pnl_pct >= tp_pct:
            lock_sl = pos['entry'] + (price - pos['entry']) * 0.6
            if lock_sl > pos['trail_sl']:
                pos['trail_sl'] = lock_sl

        # ANİ DÜŞÜŞ TESPİTİ — %1.5+ ani düşüş → SL sıkıştır
        if last_price > 0:
            drop_pct = (last_price - price) / last_price * 100
            now = time.time()
            last_squeeze = pos.get('_last_sl_squeeze', 0)
            if drop_pct >= 1.5 and now - last_squeeze > 30:
                pos['_last_sl_squeeze'] = now
                tighter_sl = price * (1 - (pos['trail_pct'] * 0.3) / 100)
                if tighter_sl > pos['trail_sl']:
                    pos['trail_sl'] = tighter_sl
                    log_msg(state, f'⚡ ANI DUSUS (-%{drop_pct:.1f}%) → SL sikistirildi: ${tighter_sl:.4f}', 'gold')

        pos['_last_price'] = price

        # Momentum takibi
        if '_momentum_prices' not in pos: pos['_momentum_prices'] = []
        pos['_momentum_prices'].append(price)
        if len(pos['_momentum_prices']) > 20: pos['_momentum_prices'].pop(0)
        check_momentum_exit(state, config, pos, pos['_momentum_prices'])

        # SL kontrolü
        if price <= pos['trail_sl']:
            close_position(state, config, pos, price, 'SL', learn)
            return True

        # TP kontrolü
        if price >= pos['target_tp']:
            close_position(state, config, pos, price, 'TP', learn)
            return True

    return changed

# ── WEB API ──
state_lock = threading.Lock()
_state  = None
_config = None

class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        if self.path == '/state':
            with state_lock:
                data = json.dumps(_state, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        global _state, _config
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        try: cmd = json.loads(body)
        except:
            self.send_response(400); self.end_headers(); return

        with state_lock:
            action = cmd.get('action')
            if action == 'scan':
                threading.Thread(target=scan_market, args=(_state, _config), daemon=True).start()
            elif action == 'set_auto_scan':
                _state['auto_scan'] = cmd.get('value', False); save_state(_state)
            elif action == 'set_auto_trade':
                _state['auto_trade'] = cmd.get('value', False); save_state(_state)
            elif action == 'set_mode':
                _state['mode'] = cmd.get('value', 'demo'); save_state(_state)
            elif action == 'set_config':
                _config.update(cmd.get('config', {}))
                with open(CONFIG_FILE, 'w') as f: json.dump(_config, f)
            elif action == 'open_position':
                sig = cmd.get('signal')
                if sig:
                    threading.Thread(target=open_position, args=(_state, _config, sig), daemon=True).start()
            elif action == 'close_position':
                pos_id = cmd.get('pos_id')
                pos = next((p for p in _state.get('positions',[]) if p['id'] == pos_id), None)
                if pos:
                    price = get_price(pos['symbol']) or pos['live_price']
                    threading.Thread(target=close_position, args=(_state, _config, pos, price, 'MANUAL'), daemon=True).start()
            elif action == 'set_api_keys':
                _config['api_key']    = cmd.get('api_key', '')
                _config['api_secret'] = cmd.get('api_secret', '')
                with open(CONFIG_FILE, 'w') as f: json.dump(_config, f)
                log_msg(_state, 'API key kaydedildi', 'up'); save_state(_state)

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
    print('API sunucu baslatildi: port 8765')
    server.serve_forever()

# ── ANA DÖNGÜ ──
def main():
    global _state, _config
    print('ARNA Bot baslatildi - 7/24 mod')
    _state  = load_state()
    _config = load_config() or {'tf': '5m', 'min_score': 80, 'pos_usd': 400, 'trail_pct': 1.5}
    log_msg(_state, 'ARNA Bot baslatildi', 'up')
    save_state(_state)

    threading.Thread(target=run_api, daemon=True).start()

    scan_interval       = 120   # 2 dakika
    price_interval      = 5     # 5 saniye
    pos_check_interval  = 60    # 1 dakika
    upper_tf_interval   = 180   # 3 dakika
    last_scan       = 0
    last_price      = 0
    last_pos_check  = 0
    last_upper_tf   = 0

    while True:
        try:
            now = time.time()
            _config = load_config() or _config

            # Fiyat takibi (5sn)
            if now - last_price >= price_interval:
                with state_lock:
                    update_positions(_state, _config)
                last_price = now

            # Pozisyon analizi (1dk)
            if now - last_pos_check >= pos_check_interval:
                with state_lock:
                    for pos in list(_state.get('positions', [])):
                        try: check_position(_state, _config, pos)
                        except Exception as e: print(f'check_position hata: {e}')
                    save_state(_state)
                last_pos_check = now

            # Üst TF takibi (3dk)
            if now - last_upper_tf >= upper_tf_interval:
                with state_lock:
                    for pos in list(_state.get('positions', [])):
                        try: check_upper_tfs(_state, _config, pos)
                        except Exception as e: print(f'upper_tf hata: {e}')
                    save_state(_state)
                last_upper_tf = now

            # OTO TARA (2dk)
            if _state.get('auto_scan') and not _state.get('scanning'):
                if now - last_scan >= scan_interval:
                    threading.Thread(target=scan_market, args=(_state, _config), daemon=True).start()
                    last_scan = now

            time.sleep(1)

        except KeyboardInterrupt:
            print('Bot durduruldu')
            break
        except Exception as e:
            print(f'Ana dongu hata: {e}')
            time.sleep(5)

if __name__ == '__main__':
    main()
