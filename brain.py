#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARNA BRAIN v4.0 — Tam Otonom, Öğrenen, Kendi Kendini Değerlendiren Beyin
Tüm veriler /root/brain_data.json'da — deploy, restart, kapanış hiçbir şeyi silmez.
Pozisyon: her 5sn | Genel analiz: her 60sn | Öğrenme: gelişmiş pattern

YENİ v4.0:
  - Demo/Gerçek ayrımı (demo öğrenir ama durdurmuyor)
  - Brain → Bot komut kanalı (config değiştir, OTO AL aç/kapat)
  - Karar günlüğü (her karar + gerekçe kaydedilir)
  - Öz değerlendirme (kararın iyi mi kötü mü sonradan kontrol edilir)
  - 3 hatalı karar → kendini durdur
"""

import json, time, os, threading, hashlib, math
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request as ur

BRAIN_DATA  = '/root/brain_data.json'
BRAIN_LOG   = '/root/brain_log.txt'
STATE_FILE  = '/root/arna_state.json'
CONFIG_FILE = '/root/arna_config.json'
BOT_FILE    = '/root/arna/bot.py'
BOT_API     = 'http://localhost:8765'   # Brain → Bot komut kanalı

TRADE_COST        = 1.60
POS_INTERVAL      = 5
ANALYSIS_INTERVAL = 60
BRAIN_PORT        = 8766

brain_lock = threading.Lock()
_active    = True

# ══════════════════════════════════════════════════
# KALICI VERİ YÖNETİMİ
# ══════════════════════════════════════════════════

def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def gen_id():
    return hashlib.md5(f"{time.time()}{os.urandom(4)}".encode()).hexdigest()[:8]

def blog(msg, level='INFO'):
    line = f'[{now_str()}] [{level}] {msg}\n'
    try:
        with open(BRAIN_LOG, 'a') as f: f.write(line)
    except: pass
    print(line.strip())

def load_json(path, default=None):
    try:
        with open(path) as f: return json.load(f)
    except: return default if default is not None else {}

def save_json(path, data):
    try:
        with open(path, 'w') as f: json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except: return False

BRAIN_DEFAULT = {
    'version': '4.0',
    'created': '',
    'active': True,
    'emergency_stop': False,
    'pending_proposals': [],
    'approved_proposals': [],
    'rejected_proposals': [],
    'conversations': [],
    'analysis_history': [],
    'code_reviews': [],
    'manual_change_log': [],
    'position_log': [],
    'position_alerts': [],
    # ── YENİ v4.0 ──
    'autonomous_decisions': [],    # Brain'in kendi aldığı kararlar
    'decision_evaluations': [],    # Kararların değerlendirmesi
    'bad_decision_count': 0,       # Üst üste hatalı karar sayısı
    'brain_self_stopped': False,   # Brain kendini durdurdu mu
    'last_config_applied': {},     # Son uygulanan config
    'scan_reports': [],            # Tarama raporları
    'filter_stats': {              # Filtre etkinlik istatistikleri
        'trend': {'pass': 0, 'fail': 0},
        'rsi':   {'pass': 0, 'fail': 0},
        'vol':   {'pass': 0, 'fail': 0},
        'boll':  {'pass': 0, 'fail': 0},
    },
    'interventions': [],           # Brain müdahaleleri
    'mode_awareness': {            # Demo/Gerçek farkındalığı
        'current_mode': 'demo',
        'demo_trades': 0,
        'real_trades': 0,
        'demo_pnl': 0.0,
        'real_pnl': 0.0,
    },
    'learning': {
        'total_trades': 0,
        'total_wins': 0,
        'total_losses': 0,
        'best_hours': [],
        'worst_hours': [],
        'best_tfs': [],
        'worst_tfs': [],
        'best_sessions': [],
        'worst_sessions': [],
        'hour_stats': {},
        'tf_stats': {},
        'session_stats': {},
        'score_stats': {},
        'exit_stats': {},
        'patterns': {
            'high_score_win': 0,
            'high_score_loss': 0,
            'low_rsi_win': 0,
            'low_rsi_loss': 0,
            'high_vol_win': 0,
            'high_vol_loss': 0,
        },
        'last_updated': ''
    },
    'system_state': {
        'trading_blocked': False,
        'block_reasons': [],
        'last_check': '',
        'last_pos_check': '',
        'code_ok': True,
        'warnings': [],
        'last_config_snapshot': {}
    },
    'stats': {
        'total_analyses': 0,
        'total_pos_checks': 0,
        'proposals_made': 0,
        'proposals_approved': 0,
        'proposals_rejected': 0,
        'autonomous_actions': 0,   # YENİ: Brain'in kendi aldığı aksiyonlar
        'correct_decisions': 0,    # YENİ: Doğru kararlar
        'wrong_decisions': 0,      # YENİ: Hatalı kararlar
    }
}

def load_brain():
    data = load_json(BRAIN_DATA, {})
    def merge(base, default):
        for k, v in default.items():
            if k not in base:
                base[k] = v
            elif isinstance(v, dict) and isinstance(base[k], dict):
                merge(base[k], v)
        return base
    data = merge(data, BRAIN_DEFAULT)
    if not data.get('created'):
        data['created'] = now_str()
    return data

def save_brain(brain):
    save_json(BRAIN_DATA, brain)

# ══════════════════════════════════════════════════
# YENİ v4.0 — BRAIN → BOT KOMUT KANALI
# ══════════════════════════════════════════════════

def bot_command(action, extra={}):
    """Brain'den bot'a doğrudan komut gönder"""
    try:
        data = json.dumps({'action': action, **extra}).encode()
        req  = ur.Request(f'{BOT_API}/', data=data,
                          headers={'Content-Type': 'application/json'}, method='POST')
        r = ur.urlopen(req, timeout=5)
        return r.status == 200
    except Exception as e:
        blog(f'Bot komut hatası ({action}): {e}', 'ERROR')
        return False

def bot_set_config(config_dict, reason=''):
    """Brain bot config'ini değiştirir ve kaydeder"""
    ok = bot_command('set_config', {'config': config_dict})
    if ok:
        blog(f'⚙️ Config güncellendi: {config_dict} | Neden: {reason}', 'AUTONOMOUS')
    return ok

def bot_set_auto_trade(value, reason=''):
    """Brain OTO AL'ı açar veya kapatır"""
    ok = bot_command('set_auto_trade', {'value': value})
    if ok:
        durum = 'AÇILDI' if value else 'KAPATILDI'
        blog(f'🤖 OTO AL {durum} | Neden: {reason}', 'AUTONOMOUS')
    return ok

def bot_set_auto_scan(value, reason=''):
    """Brain OTO TARA'yı açar veya kapatır"""
    ok = bot_command('set_auto_scan', {'value': value})
    if ok:
        durum = 'AÇILDI' if value else 'KAPATILDI'
        blog(f'🔄 OTO TARA {durum} | Neden: {reason}', 'AUTONOMOUS')
    return ok

# ══════════════════════════════════════════════════
# YENİ v4.1 — TARAMA RAPORU İŞLEYİCİ
# ══════════════════════════════════════════════════

def process_scan_report(brain, report):
    """Bot'tan gelen tarama raporunu işle, filtreleri analiz et"""
    signals  = report.get('signals', [])
    tf       = report.get('tf', '?')
    total    = report.get('total_scanned', 0)
    found    = report.get('signals_found', 0)
    config   = report.get('config', {})
    ts       = report.get('time', int(time.time()*1000))

    # Tarama raporunu kaydet
    brain['scan_reports'].insert(0, {
        'time': now_str(),
        'ts': ts,
        'tf': tf,
        'total_scanned': total,
        'signals_found': found,
        'config': config,
        'top_signals': signals[:5],
    })
    if len(brain['scan_reports']) > 100:
        brain['scan_reports'] = brain['scan_reports'][:100]

    # Filtre istatistiklerini güncelle
    fs = brain.setdefault('filter_stats', {
        'trend': {'pass': 0, 'fail': 0},
        'rsi':   {'pass': 0, 'fail': 0},
        'vol':   {'pass': 0, 'fail': 0},
        'boll':  {'pass': 0, 'fail': 0},
    })
    for s in signals:
        for filt in ['trend', 'rsi', 'vol', 'boll']:
            key = f'{filt}_pass' if filt != 'trend' else 'trend_pass'
            if filt == 'rsi': key = 'rsi_ok'
            if filt == 'vol': key = 'vol_ok'
            if filt == 'boll': key = 'boll_ok'
            if s.get(key): fs[filt]['pass'] += 1
            else:          fs[filt]['fail'] += 1

    blog(f'📊 Tarama raporu alındı: {found}/{total} sinyal | TF:{tf}', 'SCAN')

def record_intervention(brain, action, reason, result=None, mode='demo'):
    """Brain müdahalesini kaydet — ne yaptı, neden, sonucu ne oldu"""
    iv = {
        'id': gen_id(),
        'time': now_str(),
        'ts': int(time.time() * 1000),
        'action': action,
        'reason': reason,
        'result': result,       # Sonradan doldurulur
        'mode': mode,
        'outcome': None,        # 'good', 'bad', 'neutral' — sonradan
    }
    brain.setdefault('interventions', []).insert(0, iv)
    if len(brain['interventions']) > 100:
        brain['interventions'] = brain['interventions'][:100]
    blog(f'🔧 Müdahale: {action} | {reason}', 'INTERVENTION')
    return iv['id']

# ══════════════════════════════════════════════════
# YENİ v4.0 — OTONOM KARAR SİSTEMİ
# ══════════════════════════════════════════════════

def record_autonomous_decision(brain, decision_type, action, reason, config_before, config_after, mode):
    """Her otonom kararı kaydet"""
    decision = {
        'id': gen_id(),
        'time': now_str(),
        'ts': int(time.time() * 1000),
        'type': decision_type,       # 'config_change', 'stop_trading', 'start_trading'
        'action': action,            # Ne yaptı
        'reason': reason,            # Neden yaptı
        'config_before': config_before,
        'config_after': config_after,
        'mode': mode,                # 'demo' veya 'real'
        'evaluation': None,          # Sonradan doldurulacak
        'evaluated_at': None,
        'outcome': None,             # 'good', 'bad', 'neutral'
    }
    brain['autonomous_decisions'].insert(0, decision)
    if len(brain['autonomous_decisions']) > 200:
        brain['autonomous_decisions'] = brain['autonomous_decisions'][:200]
    brain['stats']['autonomous_actions'] = brain['stats'].get('autonomous_actions', 0) + 1
    blog(f'📋 Otonom Karar: [{decision_type}] {action} | {reason}', 'AUTONOMOUS')
    return decision['id']

def evaluate_interventions(brain, state):
    """Müdahalelerin sonuçlarını değerlendir — iyi mi kötü mü?"""
    history = state.get('history', [])
    closed  = [h for h in history if h.get('result') in ['win','lose'] and h.get('pnl') is not None]

    for iv in brain.get('interventions', []):
        if iv.get('outcome') is not None:
            continue
        iv_ts  = iv.get('ts', 0)
        after  = [h for h in closed if (h.get('entry_time') or 0) > iv_ts]
        if len(after) < 3:
            continue

        sample    = after[:5]
        wins_after = sum(1 for h in sample if h['result'] == 'win')
        pnl_after  = sum(h.get('pnl', 0) for h in sample)
        wr_after   = wins_after / len(sample) * 100

        if pnl_after > 0 and wr_after >= 50:
            iv['outcome'] = 'good'
            iv['result']  = f'✅ Sonraki {len(sample)} işlem: WR%{wr_after:.0f} PnL:+${pnl_after:.2f}'
        elif pnl_after < 0:
            iv['outcome'] = 'bad'
            iv['result']  = f'❌ Sonraki {len(sample)} işlem: WR%{wr_after:.0f} PnL:${pnl_after:.2f}'
        else:
            iv['outcome'] = 'neutral'
            iv['result']  = f'➖ Sonraki {len(sample)} işlem: WR%{wr_after:.0f} PnL:${pnl_after:.2f}'

        blog(f'🔧 Müdahale değerlendirildi: {iv["action"]} → {iv["outcome"]}', 'EVAL')

def evaluate_past_decisions(brain, state):
    """
    Brain'in geçmiş kararlarını değerlendir.
    Karar verildikten 10 işlem sonra: durum iyileşti mi kötüleşti mi?
    """
    decisions = brain.get('autonomous_decisions', [])
    history   = state.get('history', [])
    closed    = [h for h in history if h.get('result') in ['win','lose'] and h.get('pnl') is not None]

    for dec in decisions:
        if dec.get('evaluation') is not None:
            continue  # Zaten değerlendirilmiş

        # Karardan sonra en az 5 işlem geçmiş mi?
        dec_ts = dec.get('ts', 0)
        after  = [h for h in closed if (h.get('entry_time') or 0) > dec_ts]
        if len(after) < 5:
            continue  # Henüz yeterli veri yok

        # Sonraki 5 işlemin performansı
        sample   = after[:5]
        wins_after = sum(1 for h in sample if h['result'] == 'win')
        pnl_after  = sum(h.get('pnl', 0) for h in sample)
        wr_after   = wins_after / len(sample) * 100

        # Karara göre değerlendir
        if dec['type'] == 'stop_trading':
            # Doğru karar: durdurduktan sonra piyasa kötüydü (wr < 40)
            outcome = 'good' if wr_after < 40 else 'bad'
        elif dec['type'] == 'config_change':
            # Doğru karar: değişiklikten sonra wr arttı veya PnL pozitif
            outcome = 'good' if pnl_after > 0 and wr_after >= 50 else ('neutral' if wr_after >= 40 else 'bad')
        elif dec['type'] == 'start_trading':
            # Doğru karar: açtıktan sonra kârlı işlemler
            outcome = 'good' if pnl_after > 0 else 'bad'
        else:
            outcome = 'neutral'

        dec['evaluation'] = {
            'wr_after': round(wr_after, 1),
            'pnl_after': round(pnl_after, 2),
            'trades_checked': len(sample),
        }
        dec['evaluated_at'] = now_str()
        dec['outcome'] = outcome

        # İstatistikleri güncelle
        if outcome == 'good':
            brain['stats']['correct_decisions'] = brain['stats'].get('correct_decisions', 0) + 1
            brain['bad_decision_count'] = 0  # İyi karar — sayacı sıfırla
            blog(f'✅ Karar değerlendirmesi: DOĞRU ({dec["action"]}) | WR:{wr_after:.0f}% PnL:${pnl_after:.2f}', 'EVAL')
        elif outcome == 'bad':
            brain['stats']['wrong_decisions'] = brain['stats'].get('wrong_decisions', 0) + 1
            brain['bad_decision_count'] = brain.get('bad_decision_count', 0) + 1
            blog(f'❌ Karar değerlendirmesi: YANLIŞ ({dec["action"]}) | WR:{wr_after:.0f}% PnL:${pnl_after:.2f}', 'EVAL')

            # 3 üst üste hatalı karar → kendini durdur
            if brain['bad_decision_count'] >= 3:
                brain['brain_self_stopped'] = True
                brain['active'] = False
                blog('🛑 BEYİN KENDİNİ DURDURDU: 3 ardışık hatalı karar!', 'CRITICAL')
        else:
            blog(f'➖ Karar değerlendirmesi: NÖTR ({dec["action"]})', 'EVAL')

        # Değerlendirmeyi kaydet
        brain['decision_evaluations'].insert(0, {
            'decision_id': dec['id'],
            'time': now_str(),
            'outcome': outcome,
            'action': dec['action'],
            'wr_after': round(wr_after, 1),
            'pnl_after': round(pnl_after, 2),
        })
        if len(brain['decision_evaluations']) > 100:
            brain['decision_evaluations'] = brain['decision_evaluations'][:100]

# ══════════════════════════════════════════════════
# YENİ v4.0 — DEMO/GERÇEK AYRIMI
# ══════════════════════════════════════════════════

def update_mode_awareness(brain, state):
    """Demo ve gerçek işlemleri ayrı takip et"""
    history = state.get('history', [])
    closed  = [h for h in history if h.get('result') in ['win','lose'] and h.get('pnl') is not None]
    mode    = state.get('mode', 'demo')

    ma = brain.setdefault('mode_awareness', {})
    ma['current_mode'] = mode

    demo_trades = [h for h in closed if not h.get('is_live', False)]
    real_trades = [h for h in closed if h.get('is_live', False)]

    ma['demo_trades'] = len(demo_trades)
    ma['real_trades'] = len(real_trades)
    ma['demo_pnl']    = round(sum(h.get('pnl', 0) for h in demo_trades), 2)
    ma['real_pnl']    = round(sum(h.get('pnl', 0) for h in real_trades), 2)

    if demo_trades:
        dw = sum(1 for h in demo_trades if h['result'] == 'win')
        ma['demo_wr'] = round(dw / len(demo_trades) * 100, 1)
    if real_trades:
        rw = sum(1 for h in real_trades if h['result'] == 'win')
        ma['real_wr'] = round(rw / len(real_trades) * 100, 1)

    return mode

def is_real_mode(state):
    """Şu an gerçek modda mı?"""
    return state.get('mode', 'demo') == 'live'

# ══════════════════════════════════════════════════
# YENİ v4.0 — OTONOM PARAMETRE OPTİMİZASYONU
# ══════════════════════════════════════════════════

def autonomous_optimize(brain, state, config, perf, mode):
    """
    Brain kendi kendine parametreleri optimize eder.
    Demo'da öğrenir, gerçekte uygular.
    """
    if not perf or perf['total'] < 10:
        return  # Yeterli veri yok

    learn   = brain.get('learning', {})
    actions = []  # Yapılacak değişiklikler

    # ── 1) KÖTÜ TF OTOMATİK DEĞİŞTİR ──
    current_tf   = config.get('tf', '1h')
    tf_stats     = learn.get('tf_stats', {})
    current_data = tf_stats.get(current_tf, {})
    worst_tfs    = learn.get('worst_tfs', [])
    best_tfs     = learn.get('best_tfs', [])

    if (current_tf in worst_tfs and
        current_data.get('count', 0) >= 5 and
        current_data.get('pnl', 0) < -10):

        # En iyi TF'ye geç
        candidate_tfs = ['4h', '1h', '2h', '15m', '30m', '5m', '3m', '1m']
        new_tf = None
        for tf in candidate_tfs:
            if tf not in worst_tfs and tf != current_tf:
                tf_data = tf_stats.get(tf, {})
                if tf_data.get('count', 0) >= 3 and tf_data.get('pnl', 0) > 0:
                    new_tf = tf
                    break
        if not new_tf:
            for tf in candidate_tfs:
                if tf != current_tf and tf not in worst_tfs:
                    new_tf = tf
                    break

        if new_tf and new_tf != current_tf:
            reason = (f'{current_tf} TF {current_data.get("count",0)} işlemde '
                      f'${current_data.get("pnl",0):.2f} zarar üretti')
            actions.append({
                'type': 'config_change',
                'config': {'tf': new_tf},
                'reason': reason,
                'desc': f'TF {current_tf} → {new_tf}',
            })

    # ── 2) KÖTÜ SAAT OTOMATİK YASAK SAATİ GÜNCELLESİN ──
    worst_hours = learn.get('worst_hours', [])
    current_ah  = state.get('active_hours', {})

    if len(worst_hours) >= 2 and not current_ah.get('active', False):
        # Yasak saatleri en kötü saatlerden hesapla
        wh_sorted = sorted(worst_hours)
        start_h   = wh_sorted[0]
        end_h     = (wh_sorted[-1] + 1) % 24
        reason    = f'Saatler {wh_sorted} sürekli zararlı — otomatik yasak saat ekleniyor'
        actions.append({
            'type': 'config_change',
            'config': {'active_hours': {'start': start_h, 'end': end_h, 'active': True}},
            'reason': reason,
            'desc': f'Yasak saat {start_h}:00–{end_h}:00 UTC eklendi',
        })

    # ── 3) MIN SKOR OPTİMİZASYONU ──
    score_stats    = learn.get('score_stats', {})
    current_score  = config.get('min_score', 80)

    # 80-84 aralığı çok kötüyse skoru artır
    low_bucket = score_stats.get('80-84', {})
    if (low_bucket.get('count', 0) >= 5 and
        low_bucket.get('wins', 0) / max(low_bucket.get('count', 1), 1) < 0.35 and
        current_score <= 82):
        new_score = min(current_score + 5, 90)
        reason    = f'Skor 80-84 arası işlemlerin %{round(low_bucket.get("wins",0)/max(low_bucket.get("count",1),1)*100)}u kazanıyor — min skor artırılıyor'
        actions.append({
            'type': 'config_change',
            'config': {'min_score': new_score},
            'reason': reason,
            'desc': f'Min skor {current_score} → {new_score}',
        })

    # ── 4) TRAILING STOP OPTİMİZASYONU ──
    sl_ratio = perf.get('sl_count', 0) / max(perf.get('total', 1), 1)
    current_trail = config.get('trail_pct', 1.5)

    if sl_ratio > 0.6 and current_trail < 2.5:
        # SL çok sık yeniyor — trailing'i genişlet
        new_trail = min(current_trail + 0.3, 3.0)
        reason    = f'İşlemlerin %{round(sl_ratio*100)}i SL ile kapanıyor — trailing genişletiliyor'
        actions.append({
            'type': 'config_change',
            'config': {'trail_pct': round(new_trail, 1)},
            'reason': reason,
            'desc': f'Trailing %{current_trail} → %{round(new_trail,1)}',
        })
    elif sl_ratio < 0.2 and perf.get('profit_factor', 0) > 1.5 and current_trail > 1.2:
        # Sistem iyi çalışıyor, trailing biraz daralt
        new_trail = max(current_trail - 0.2, 1.0)
        reason    = f'Sistem iyi çalışıyor (PF:{perf["profit_factor"]}) — trailing daraltılıyor'
        actions.append({
            'type': 'config_change',
            'config': {'trail_pct': round(new_trail, 1)},
            'reason': reason,
            'desc': f'Trailing %{current_trail} → %{round(new_trail,1)}',
        })

    # ── UYGULA — Demo ve Gerçekte de uygula ──
    if not actions:
        return

    mode_label = 'DEMO' if not is_real_mode(state) else 'GERÇEK'

    for a in actions:
        config_before = {k: config.get(k) for k in a['config']}
        ok = bot_set_config(a['config'], a['reason'])
        if ok:
            record_autonomous_decision(
                brain, a['type'], a['desc'], a['reason'],
                config_before, a['config'],
                'demo' if not is_real_mode(state) else 'real'
            )
            record_intervention(
                brain, a['desc'], a['reason'], None,
                'demo' if not is_real_mode(state) else 'real'
            )
            blog(f'✅ [{mode_label} OTONOM] Uygulandı: {a["desc"]}', 'AUTONOMOUS')
        else:
            blog(f'❌ [{mode_label} OTONOM] Uygulanamadı: {a["desc"]}', 'ERROR')

# ══════════════════════════════════════════════════
# YENİ v4.0 — OTO AL YÖNETIMI (Demo/Gerçek ayrımı)
# ══════════════════════════════════════════════════

def autonomous_trade_control(brain, state, perf, mode):
    """
    Demo'da: Öğren ama OTO AL'ı durdurma
    Gerçekte: Zarar varsa OTO AL'ı durdur
    """
    if not perf or perf['total'] < 10:
        return

    sys_s = brain['system_state']

    if mode == 'demo':
        # Demo'da öğren, ama sistemi durdurma
        if sys_s.get('trading_blocked') and not state.get('is_live'):
            # Demo zararda diye gerçek alım engelleniyorsa kaldır
            sys_s['trading_blocked'] = False
            sys_s['block_reasons']   = []
            blog('ℹ️ Demo modu: Alım engeli kaldırıldı (demo zarar gerçeği etkilemez)', 'INFO')
        return

    # Gerçek mod — koruyucu önlemler
    real_closed = [h for h in state.get('history', [])
                   if h.get('result') in ['win','lose']
                   and h.get('pnl') is not None
                   and h.get('is_live', False)]

    if len(real_closed) < 5:
        return  # Gerçek işlem sayısı yetersiz

    real_wins = [h for h in real_closed if h['result'] == 'win']
    real_wr   = len(real_wins) / len(real_closed) * 100
    real_pnl  = sum(h.get('pnl', 0) for h in real_closed)

    # Gerçek modda sistem zarar üretiyorsa durdur
    if real_pnl < -20 and real_wr < 35 and not sys_s.get('trading_blocked'):
        sys_s['trading_blocked'] = True
        sys_s['block_reasons']   = [f'Gerçek işlem PnL: ${real_pnl:.2f}, WR: %{real_wr:.0f}']
        bot_set_auto_trade(False, f'Gerçek zarar: ${real_pnl:.2f} | WR: %{real_wr:.0f}')
        record_autonomous_decision(
            brain, 'stop_trading',
            f'OTO AL durduruldu (gerçek zarar ${real_pnl:.2f})',
            f'WR %{real_wr:.0f} ve toplam kayıp ${abs(real_pnl):.2f}',
            {}, {}, 'real'
        )
        blog(f'⛔ GERÇEK MOD: OTO AL durduruldu | PnL:${real_pnl:.2f} WR:%{real_wr:.0f}', 'CRITICAL')

    # Toparlandıysa tekrar aç
    elif real_pnl > 0 and real_wr >= 55 and sys_s.get('trading_blocked'):
        sys_s['trading_blocked'] = False
        sys_s['block_reasons']   = []
        bot_set_auto_trade(True, f'Sistem toparlandı: WR %{real_wr:.0f}')
        record_autonomous_decision(
            brain, 'start_trading',
            f'OTO AL yeniden açıldı (toparlandı)',
            f'WR %{real_wr:.0f} ve PnL ${real_pnl:.2f}',
            {}, {}, 'real'
        )
        blog(f'✅ GERÇEK MOD: OTO AL yeniden açıldı | WR:%{real_wr:.0f}', 'INFO')

# ══════════════════════════════════════════════════
# GELİŞMİŞ ÖĞRENME (mevcut kod korundu)
# ══════════════════════════════════════════════════

def update_learning(brain, state):
    """Her trade'den tam pattern çıkar"""
    history = state.get('history', [])
    closed  = [h for h in history if h.get('result') in ['win','lose'] and h.get('pnl') is not None]
    learn   = brain['learning']

    learn['total_trades']  = len(closed)
    learn['total_wins']    = len([h for h in closed if h['result']=='win'])
    learn['total_losses']  = len([h for h in closed if h['result']=='lose'])
    learn['last_updated']  = now_str()

    if len(closed) < 3:
        return

    wins   = [h for h in closed if h['result']=='win']
    losses = [h for h in closed if h['result']=='lose']

    # ── SAAT BAZLI ──
    hour_stats = {}
    for h in closed:
        et = h.get('entry_time', 0)
        if et:
            hr = datetime.fromtimestamp(et/1000).hour
            s  = hour_stats.setdefault(str(hr), {'wins':0,'losses':0,'pnl':0.0,'count':0})
            s['count'] += 1
            s['pnl']   += h.get('pnl', 0)
            if h['result']=='win': s['wins'] += 1
            else: s['losses'] += 1
    learn['hour_stats'] = hour_stats
    sorted_h = sorted(hour_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    learn['best_hours']  = [int(h) for h,s in sorted_h if s['count']>=2 and s['pnl']>0][:5]
    learn['worst_hours'] = [int(h) for h,s in sorted_h[::-1] if s['count']>=2 and s['pnl']<0][:3]

    # ── TF BAZLI ──
    tf_stats = {}
    for h in closed:
        tf = h.get('tf','?')
        s  = tf_stats.setdefault(tf, {'wins':0,'losses':0,'pnl':0.0,'count':0,'avg_win':0,'avg_loss':0})
        s['count'] += 1
        s['pnl']   += h.get('pnl', 0)
        if h['result']=='win': s['wins'] += 1
        else: s['losses'] += 1
    for tf in tf_stats:
        tf_wins   = [h['pnl'] for h in closed if h.get('tf')==tf and h['result']=='win']
        tf_losses = [h['pnl'] for h in closed if h.get('tf')==tf and h['result']=='lose']
        tf_stats[tf]['avg_win']  = sum(tf_wins)/len(tf_wins)     if tf_wins   else 0
        tf_stats[tf]['avg_loss'] = sum(tf_losses)/len(tf_losses) if tf_losses else 0
        n = tf_stats[tf]['count']
        tf_stats[tf]['win_rate'] = round(tf_stats[tf]['wins']/n*100, 1) if n else 0
    learn['tf_stats'] = tf_stats
    sorted_tf = sorted(tf_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    learn['best_tfs']  = [tf for tf,s in sorted_tf if s['count']>=3 and s['pnl']>0]
    learn['worst_tfs'] = [tf for tf,s in sorted_tf[::-1] if s['count']>=3 and s['pnl']<0]

    # ── SEANS BAZLI ──
    session_stats = {}
    for h in closed:
        et = h.get('entry_time', 0)
        if et:
            hr = datetime.fromtimestamp(et/1000).hour
            sess = 'EU' if 8<=hr<17 else 'US' if 17<=hr<23 else 'ASIA'
            s = session_stats.setdefault(sess, {'wins':0,'losses':0,'pnl':0.0,'count':0})
            s['count'] += 1
            s['pnl']   += h.get('pnl', 0)
            if h['result']=='win': s['wins'] += 1
            else: s['losses'] += 1
    learn['session_stats'] = session_stats
    sorted_sess = sorted(session_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    learn['best_sessions']  = [s for s,d in sorted_sess if d['count']>=2 and d['pnl']>0]
    learn['worst_sessions'] = [s for s,d in sorted_sess[::-1] if d['count']>=2 and d['pnl']<0]

    # ── SKOR BAZLI ──
    score_stats = {}
    for h in closed:
        sc = h.get('score', 0)
        if sc:
            bucket = f"{(sc//5)*5}-{(sc//5)*5+4}"
            s = score_stats.setdefault(bucket, {'wins':0,'losses':0,'pnl':0.0,'count':0})
            s['count'] += 1
            s['pnl']   += h.get('pnl', 0)
            if h['result']=='win': s['wins'] += 1
            else: s['losses'] += 1
    learn['score_stats'] = score_stats

    # ── ÇIKIŞ NEDENİ BAZLI ──
    exit_stats = {}
    for h in closed:
        reason = h.get('exit_reason', '?')
        s = exit_stats.setdefault(reason, {'count':0,'pnl':0.0,'wins':0,'losses':0})
        s['count'] += 1
        s['pnl']   += h.get('pnl', 0)
        if h['result']=='win': s['wins'] += 1
        else: s['losses'] += 1
    learn['exit_stats'] = exit_stats

    # ── PATTERN ÖĞRENME ──
    patterns = learn.setdefault('patterns', {})

    high_score = [h for h in closed if h.get('score',0)>=85]
    patterns['high_score_win']  = len([h for h in high_score if h['result']=='win'])
    patterns['high_score_loss'] = len([h for h in high_score if h['result']=='lose'])
    patterns['high_score_wr']   = round(patterns['high_score_win']/len(high_score)*100,1) if high_score else 0

    low_rsi = [h for h in closed if h.get('rsi_ok')==False]
    patterns['low_rsi_win']  = len([h for h in low_rsi if h['result']=='win'])
    patterns['low_rsi_loss'] = len([h for h in low_rsi if h['result']=='lose'])

    high_vol = [h for h in closed if h.get('vol_ok')==True]
    patterns['high_vol_win']  = len([h for h in high_vol if h['result']=='win'])
    patterns['high_vol_loss'] = len([h for h in high_vol if h['result']=='lose'])
    patterns['high_vol_wr']   = round(patterns['high_vol_win']/len(high_vol)*100,1) if high_vol else 0

    trend_ok = [h for h in closed if h.get('trend_pass')==True]
    patterns['trend_win']  = len([h for h in trend_ok if h['result']=='win'])
    patterns['trend_loss'] = len([h for h in trend_ok if h['result']=='lose'])
    patterns['trend_wr']   = round(patterns['trend_win']/len(trend_ok)*100,1) if trend_ok else 0

    all_ok = [h for h in closed if h.get('trend_pass') and h.get('vol_ok') and h.get('rsi_ok') and h.get('boll_ok')]
    patterns['all_ok_win']  = len([h for h in all_ok if h['result']=='win'])
    patterns['all_ok_loss'] = len([h for h in all_ok if h['result']=='lose'])
    patterns['all_ok_wr']   = round(patterns['all_ok_win']/len(all_ok)*100,1) if all_ok else 0

    if wins:
        patterns['avg_win_usd']  = round(sum(h['pnl'] for h in wins)/len(wins),2)
    if losses:
        patterns['avg_loss_usd'] = round(sum(h['pnl'] for h in losses)/len(losses),2)

    if closed:
        wr  = len(wins)/len(closed)
        exp = wr*patterns.get('avg_win_usd',0) + (1-wr)*patterns.get('avg_loss_usd',0)
        patterns['real_expectancy'] = round(exp - TRADE_COST, 2)
        patterns['win_rate']        = round(wr*100, 1)

# ══════════════════════════════════════════════════
# PERFORMANS ANALİZİ (mevcut kod korundu)
# ══════════════════════════════════════════════════

def analyze_performance(state):
    history = state.get('history', [])
    closed  = [h for h in history if h.get('result') in ['win','lose'] and h.get('pnl') is not None]
    if len(closed) < 3:
        return None

    wins   = [h for h in closed if h['result']=='win']
    losses = [h for h in closed if h['result']=='lose']
    total  = len(closed)
    wr     = len(wins)/total

    avg_win  = sum(h['pnl'] for h in wins)/len(wins)     if wins   else 0
    avg_loss = sum(h['pnl'] for h in losses)/len(losses) if losses else 0
    exp      = wr*avg_win + (1-wr)*avg_loss
    real_exp = exp - TRADE_COST

    gross_w = sum(h['pnl'] for h in wins)
    gross_l = abs(sum(h['pnl'] for h in losses))
    pf      = gross_w/gross_l if gross_l>0 else 0

    sl_count = len([h for h in closed if h.get('exit_reason')=='SL'])
    tp_count = len([h for h in closed if h.get('exit_reason')=='TP'])
    mn_count = len([h for h in closed if h.get('exit_reason')=='MANUAL'])

    tf_stats = {}
    for h in closed:
        tf = h.get('tf','?')
        s  = tf_stats.setdefault(tf,{'wins':0,'losses':0,'pnl':0.0,'count':0})
        s['count']+=1; s['pnl']+=h['pnl']
        if h['result']=='win': s['wins']+=1
        else: s['losses']+=1

    session_stats = {}
    for h in closed:
        et = h.get('entry_time',0)
        if et:
            hr   = datetime.fromtimestamp(et/1000).hour
            sess = 'EU' if 8<=hr<17 else 'US' if 17<=hr<23 else 'ASIA'
            s    = session_stats.setdefault(sess,{'wins':0,'losses':0,'pnl':0.0})
            s['pnl']+=h['pnl']
            if h['result']=='win': s['wins']+=1
            else: s['losses']+=1

    return {
        'total':total,'win_rate':round(wr*100,1),
        'avg_win':round(avg_win,2),'avg_loss':round(avg_loss,2),
        'expectancy':round(exp,2),'real_expectancy':round(real_exp,2),
        'profit_factor':round(pf,2),
        'sl_count':sl_count,'tp_count':tp_count,'mn_count':mn_count,
        'tf_stats':tf_stats,'session_stats':session_stats,
        'gross_win':round(gross_w,2),'gross_loss':round(gross_l,2)
    }

# ══════════════════════════════════════════════════
# CANLI POZİSYON TAKİBİ (mevcut kod korundu)
# ══════════════════════════════════════════════════

def monitor_positions_live(brain):
    """Her 5 saniyede açık pozisyonları izle"""
    while _active:
        try:
            with brain_lock:
                state    = load_json(STATE_FILE, {})
                positions= state.get('positions', [])
                alerts   = []
                pos_log  = brain.setdefault('position_log', [])
                sys_s    = brain['system_state']
                sys_s['last_pos_check'] = now_str()
                brain['stats']['total_pos_checks'] = brain['stats'].get('total_pos_checks',0)+1

                for pos in positions:
                    entry     = pos.get('entry', 0)
                    live      = pos.get('live_price', entry)
                    trail_sl  = pos.get('trail_sl', 0)
                    target_tp = pos.get('target_tp', 0)
                    name      = pos.get('display_name','?')
                    pos_usd   = pos.get('pos_usd', 400)
                    tp_pct    = pos.get('tp_pct', 2.0)

                    if entry <= 0: continue

                    pnl_pct = (live-entry)/entry*100
                    pnl_usd = (live-entry)/entry*pos_usd

                    sl_dist = entry - trail_sl if trail_sl > 0 else 0
                    tp_dist = target_tp - entry if target_tp > 0 else 0
                    rr = tp_dist/sl_dist if sl_dist > 0 else 0

                    log_entry = {
                        'time': now_str(),
                        'coin': name,
                        'pnl_pct': round(pnl_pct,2),
                        'pnl_usd': round(pnl_usd,2),
                        'rr': round(rr,2),
                        'live': live
                    }
                    pos_log.insert(0, log_entry)

                    if pnl_usd < -TRADE_COST and sl_dist > 0:
                        sl_dist_pct = sl_dist/entry*100
                        if sl_dist_pct > 2.5:
                            alerts.append({
                                'coin':name,'type':'WARNING',
                                'msg':f'{name} zararda (${pnl_usd:.2f}) ama SL %{sl_dist_pct:.1f} uzakta. Dikkat.',
                                'time':now_str()
                            })

                    if 0 < rr < 1.5 and pnl_pct > 0:
                        alerts.append({
                            'coin':name,'type':'INFO',
                            'msg':f'{name} RR oranı düşük: {rr:.1f}:1 (min 2:1 önerilir)',
                            'time':now_str()
                        })

                    if tp_pct > 0 and pnl_pct >= tp_pct*0.85:
                        alerts.append({
                            'coin':name,'type':'INFO',
                            'msg':f'{name} TP hedefine %{((pnl_pct/tp_pct)*100):.0f} yakın (${pnl_usd:+.2f})',
                            'time':now_str()
                        })

                    recent_logs = [l for l in pos_log if l.get('coin')==name][:5]
                    if len(recent_logs)>=3:
                        pnls = [l['pnl_usd'] for l in recent_logs]
                        if all(pnls[i]<pnls[i+1] for i in range(len(pnls)-1)) and pnl_usd < -1:
                            alerts.append({
                                'coin':name,'type':'CRITICAL',
                                'msg':f'{name} sürekli düşüyor! ${pnl_usd:.2f} zarar.',
                                'time':now_str()
                            })

                brain['position_alerts'] = alerts[:10]
                if len(pos_log) > 500:
                    brain['position_log'] = pos_log[:500]

                save_brain(brain)

        except Exception as e:
            blog(f'Pozisyon takip hata: {e}', 'ERROR')

        time.sleep(POS_INTERVAL)

# ══════════════════════════════════════════════════
# ÖNERİ ÜRETİCİ — Demo ayrımı eklendi
# ══════════════════════════════════════════════════

def generate_proposals(brain, state, config, perf, mode='demo'):
    if not perf:
        return []
    proposals = []
    pending_subjects = {p['subject'] for p in brain['pending_proposals']}

    # Demo modda kritik öneriler üretme — sadece bilgi ver
    if mode == 'demo':
        # Demo'da sadece öğrenme önerileri
        if perf['real_expectancy'] < -2.0 and perf['total'] >= 10:
            s = 'demo_learning_note'
            if s not in pending_subjects:
                proposals.append({
                    'id':gen_id(),'subject':s,'type':'INFO','priority':4,
                    'title':f'📚 DEMO Öğrenme: Sistem %{perf["win_rate"]} başarı ile çalışıyor',
                    'detail':(
                        f'Demo expectancy: ${perf["real_expectancy"]:.2f}/trade\n'
                        f'Bu sadece öğrenme verisi — gerçek alım etkilenmez.\n'
                        f'Brain parametreleri optimize ediyor...'
                    ),
                    'action': None, 'confidence': 100,
                    'created': now_str(), 'status': 'pending',
                    'blocking': False, 'effect': 'Sadece bilgi'
                })
        return proposals

    # Gerçek mod — asıl öneriler
    if perf['real_expectancy'] < -2.0 and perf['total'] >= 10:
        s = 'critical_stop_trading'
        if s not in pending_subjects:
            proposals.append({
                'id':gen_id(),'subject':s,'type':'CRITICAL','priority':1,
                'title':'🚨 GERÇEK MOD: Sistem zarar üretiyor',
                'detail':(
                    f'Maliyet dahil expectancy: ${perf["real_expectancy"]:.2f}/trade\n'
                    f'Win Rate: %{perf["win_rate"]} | PF: {perf["profit_factor"]}\n'
                    f'Toplam zarar: ${perf["gross_loss"]-perf["gross_win"]:.2f}\n'
                    f'Brain OTO AL\'ı otomatik durdurdu.'
                ),
                'action':'stop_auto_trade','confidence':95,
                'created':now_str(),'status':'pending',
                'blocking':True,'effect':'OTO AL kapatıldı'
            })
            blog('ACİL öneri: gerçek sistem zarar üretiyor','CRITICAL')

    if perf['sl_count'] > perf['tp_count']*4 and perf['total']>=8:
        s = 'sl_tp_bad'
        if s not in pending_subjects:
            proposals.append({
                'id':gen_id(),'subject':s,'type':'WARNING','priority':2,
                'title':'⚠️ SL/TP Oranı Kritik',
                'detail':f'SL:{perf["sl_count"]} / TP:{perf["tp_count"]} / Manuel:{perf["mn_count"]}\nStop çok sık yeniyor.',
                'action':None,'confidence':85,'created':now_str(),'status':'pending',
                'blocking':False,'effect':'Bilgi amaçlı'
            })

    if perf['profit_factor'] < 1.0 and perf['total']>=10:
        s = 'pf_negative'
        if s not in pending_subjects:
            proposals.append({
                'id':gen_id(),'subject':s,'type':'WARNING','priority':2,
                'title':'⚠️ Profit Factor 1.0 Altında',
                'detail':f'PF: {perf["profit_factor"]}\nKazanç: ${perf["gross_win"]:.2f} / Kayıp: ${perf["gross_loss"]:.2f}',
                'action':None,'confidence':80,'created':now_str(),'status':'pending',
                'blocking':False,'effect':'Bilgi amaçlı'
            })

    for tf, stats in perf.get('tf_stats',{}).items():
        if stats['count']>=5 and stats['pnl']<-8.0:
            s = f'tf_losing_{tf}'
            if s not in pending_subjects:
                proposals.append({
                    'id':gen_id(),'subject':s,'type':'SUGGESTION','priority':3,
                    'title':f'📊 {tf} TF Zarar Üretiyor',
                    'detail':f'{tf}: {stats["count"]} işlem, ${stats["pnl"]:.2f} toplam',
                    'action':None,'confidence':75,'created':now_str(),'status':'pending',
                    'blocking':False,'effect':'TF değişikliği önerisi'
                })

    asia = perf.get('session_stats',{}).get('ASIA',{})
    asia_total = asia.get('wins',0)+asia.get('losses',0)
    if asia_total>=5 and asia.get('pnl',0)<-10:
        s = 'asia_bad'
        if s not in pending_subjects:
            proposals.append({
                'id':gen_id(),'subject':s,'type':'SUGGESTION','priority':3,
                'title':'🌏 ASIA Seansı Zarar Üretiyor',
                'detail':f'ASIA: ${asia.get("pnl",0):.2f} toplam\nUTC 22-08 yasak saat eklenmesi önerilir.',
                'action':None,'confidence':80,'created':now_str(),'status':'pending',
                'blocking':False,'effect':'Yasak saat önerisi'
            })

    patterns = brain['learning'].get('patterns',{})
    all_ok_wr = patterns.get('all_ok_wr', 0)
    if all_ok_wr > 60 and patterns.get('all_ok_win',0)+patterns.get('all_ok_loss',0) >= 5:
        s = 'all_indicators_good'
        if s not in pending_subjects:
            total_aok = patterns.get('all_ok_win',0)+patterns.get('all_ok_loss',0)
            proposals.append({
                'id':gen_id(),'subject':s,'type':'INSIGHT','priority':4,
                'title':f'💡 Tüm İndikatörler OK → WR %{all_ok_wr}',
                'detail':f'{total_aok} işlemde tüm filtreler geçti, WR %{all_ok_wr}',
                'action':None,'confidence':70,'created':now_str(),'status':'pending',
                'blocking':False,'effect':'Skor optimizasyonu önerisi'
            })

    return proposals

# ══════════════════════════════════════════════════
# MANUEL DEĞİŞİKLİK TAKİBİ (mevcut kod korundu)
# ══════════════════════════════════════════════════

def monitor_manual_changes(brain, state, config):
    sys_s = brain['system_state']
    prev  = sys_s.get('last_config_snapshot', {})
    learn = brain['learning']
    warnings = []

    if not prev:
        sys_s['last_config_snapshot'] = {
            'tf':config.get('tf'), 'trail_pct':config.get('trail_pct'),
            'min_score':config.get('min_score'), 'pos_usd':config.get('pos_usd'),
            'auto_trade':state.get('auto_trade'),
            'active_hours':state.get('active_hours',{})
        }
        return []

    if prev.get('tf') != config.get('tf'):
        old_tf, new_tf = prev.get('tf','?'), config.get('tf','?')
        bad  = learn.get('worst_tfs',[])
        good = learn.get('best_tfs',[])
        if new_tf in bad:
            w = f'⚠️ TF {old_tf}→{new_tf}: Veriye göre {new_tf} zarar üretiyor!'
            warnings.append({'type':'WARNING','msg':w,'time':now_str()})
        elif new_tf in good:
            warnings.append({'type':'OK','msg':f'✅ TF {old_tf}→{new_tf}: Kârlı TF, iyi seçim.','time':now_str()})
        else:
            warnings.append({'type':'INFO','msg':f'ℹ️ TF {old_tf}→{new_tf}: Veri toplanıyor.','time':now_str()})

    if prev.get('trail_pct') != config.get('trail_pct'):
        old_t, new_t = prev.get('trail_pct','?'), config.get('trail_pct','?')
        try:
            nt = float(new_t)
            if nt > 3.5:
                warnings.append({'type':'WARNING','msg':f'⚠️ Trailing %{old_t}→%{new_t}: Çok geniş.','time':now_str()})
            elif nt < 0.8:
                warnings.append({'type':'WARNING','msg':f'⚠️ Trailing %{old_t}→%{new_t}: Çok dar.','time':now_str()})
            else:
                warnings.append({'type':'OK','msg':f'✅ Trailing %{old_t}→%{new_t}: Makul.','time':now_str()})
        except: pass

    if prev.get('min_score') != config.get('min_score'):
        old_s, new_s = prev.get('min_score','?'), config.get('min_score','?')
        try:
            ns = int(new_s)
            if ns < 75:
                warnings.append({'type':'WARNING','msg':f'⚠️ Min Skor {old_s}→{new_s}: Çok düşük.','time':now_str()})
            elif ns > 92:
                warnings.append({'type':'WARNING','msg':f'⚠️ Min Skor {old_s}→{new_s}: Çok yüksek.','time':now_str()})
            else:
                warnings.append({'type':'OK','msg':f'✅ Min Skor {old_s}→{new_s}: Makul.','time':now_str()})
        except: pass

    if prev.get('pos_usd') != config.get('pos_usd'):
        old_p, new_p = prev.get('pos_usd','?'), config.get('pos_usd','?')
        try:
            if float(new_p) > 600:
                warnings.append({'type':'WARNING','msg':f'⚠️ Pozisyon ${old_p}→${new_p}: Büyük pozisyon riski artırır.','time':now_str()})
            else:
                warnings.append({'type':'OK','msg':f'✅ Pozisyon ${old_p}→${new_p}','time':now_str()})
        except: pass

    if prev.get('auto_trade') != state.get('auto_trade'):
        if not state.get('auto_trade'):
            warnings.append({'type':'INFO','msg':'ℹ️ OTO AL kapatıldı.','time':now_str()})
        else:
            warnings.append({'type':'INFO','msg':'ℹ️ OTO AL açıldı.','time':now_str()})

    if warnings:
        brain['manual_change_log'].insert(0,{
            'time':now_str(),
            'warnings':[w['msg'] for w in warnings]
        })
        if len(brain['manual_change_log'])>100:
            brain['manual_change_log'] = brain['manual_change_log'][:100]
        ws = sys_s.setdefault('warnings',[])
        for w in warnings:
            ws.insert(0, w)
        sys_s['warnings'] = ws[:30]

    sys_s['last_config_snapshot'] = {
        'tf':config.get('tf'), 'trail_pct':config.get('trail_pct'),
        'min_score':config.get('min_score'), 'pos_usd':config.get('pos_usd'),
        'auto_trade':state.get('auto_trade'),
        'active_hours':state.get('active_hours',{})
    }
    return warnings

# ══════════════════════════════════════════════════
# KOD BÜTÜNLÜĞÜ (mevcut kod korundu)
# ══════════════════════════════════════════════════

REQUIRED = {
    'calc_atr':'ATR dinamik stop','calc_adx':'ADX regime filtresi',
    'kill_switch':'Kill switch','consecutive':'Ardışık SL sayacı',
    'sideways':'Sideways filtresi','0.0005':'Min fiyat filtresi',
    'is_usdt':'USDT filtresi','in_pause':'Yasak saat kontrolü',
    'tp_activation':'Trailing aktivasyon','atr_trail':'ATR trailing',
    'r_multiple':'R-multiple tracking'
}

def check_code_integrity():
    try:
        with open(BOT_FILE) as f: content = f.read()
        missing = [{'key':k,'desc':d} for k,d in REQUIRED.items() if k not in content]
        return {'ok':len(missing)==0,'missing':missing,'total':len(REQUIRED),'present':len(REQUIRED)-len(missing)}
    except Exception as e:
        return {'ok':False,'missing':[],'error':str(e),'total':len(REQUIRED),'present':0}

def review_code(code_content, filename):
    issues, warnings, goods = [], [], []
    if filename=='bot.py':
        for k,d in REQUIRED.items():
            if k in code_content: goods.append(f'✅ {d}')
            else: issues.append(f'❌ EKSİK: {d}')
        if 'endswith(\'TRY\')' in code_content and 'if not is_usdt' not in code_content:
            warnings.append('⚠️ TRY filtresi eksik')
        if 'in_pause = False' in code_content:
            goods.append('✅ Yasak saat her zaman kontrol ediliyor')
    elif filename=='index.html':
        html_f = {
            'resetKillSwitch':'Kill switch reset','renderRiskEngine':'Risk engine',
            'savePauseHours':'Yasak saatler kayıt','brainFetch':'Brain bağlantısı',
            'pg-brain':'Brain paneli','renderPerfSummary':'Performans özeti'
        }
        for k,d in html_f.items():
            if k in code_content: goods.append(f'✅ {d}')
            else: issues.append(f'❌ EKSİK: {d}')
    approved = len(issues)==0
    return {
        'id':gen_id(),'filename':filename,'time':now_str(),
        'issues':issues,'warnings':warnings,'goods':goods,
        'approved':approved,
        'summary':f'✅ SORUNSUZ ({len(goods)} özellik)' if approved else f'❌ {len(issues)} SORUN VAR',
        'recommendation':'GitHub\'a yükleyebilirsin.' if approved else 'Sorunları düzelt, sonra yükle.'
    }

# ══════════════════════════════════════════════════
# BLOCKING — Demo ayrımı eklendi
# ══════════════════════════════════════════════════

def apply_blocking(brain, mode='demo'):
    # Demo modda blocking yapma
    if mode == 'demo':
        sys_s = brain['system_state']
        if sys_s.get('trading_blocked'):
            sys_s['trading_blocked'] = False
            sys_s['block_reasons']   = []
            blog('ℹ️ Demo mod: Blocking kaldırıldı', 'INFO')
        return

    blocking = [p for p in brain['pending_proposals'] if p.get('blocking') and p['status']=='pending']
    sys_s = brain['system_state']
    if blocking:
        if not sys_s.get('trading_blocked'):
            sys_s['trading_blocked'] = True
            sys_s['block_reasons']   = [p['title'] for p in blocking]
            blog(f'🚨 ALIM DURDURULDU: {blocking[0]["title"]}','CRITICAL')
            try:
                s = load_json(STATE_FILE, {})
                s['auto_trade'] = False
                save_json(STATE_FILE, s)
            except: pass
    else:
        if sys_s.get('trading_blocked'):
            sys_s['trading_blocked'] = False
            sys_s['block_reasons']   = []
            blog('✅ Alım engeli kalktı','INFO')

# ══════════════════════════════════════════════════
# SOHBET — Yeni komutlar eklendi
# ══════════════════════════════════════════════════

def handle_chat(msg, brain):
    msg_l  = msg.lower().strip()
    state  = load_json(STATE_FILE, {})
    config = load_json(CONFIG_FILE, {})
    perf   = analyze_performance(state)
    learn  = brain.get('learning',{})
    sys_s  = brain.get('system_state',{})
    stats  = brain.get('stats',{})
    patterns = learn.get('patterns',{})
    ma     = brain.get('mode_awareness', {})

    if any(w in msg_l for w in ['durum','nasıl','özet','ne var']):
        pending  = len(brain.get('pending_proposals',[]))
        blocked  = sys_s.get('trading_blocked',False)
        pos_count= len(state.get('positions',[]))
        mode     = ma.get('current_mode','demo')
        bad_dec  = brain.get('bad_decision_count', 0)
        self_stop= brain.get('brain_self_stopped', False)
        auto_dec = stats.get('autonomous_actions', 0)
        return (
            f"🧠 Sistem Durumu [{now_str()}]\n\n"
            f"📋 Bekleyen öneri: {pending}\n"
            f"💼 Açık pozisyon: {pos_count}\n"
            f"🎮 Mod: {mode.upper()}\n"
            f"🤖 Alım: {'⛔ DURDURULDU' if blocked else '✅ Aktif'}\n"
            f"🔧 Kod: {'✅ Tam' if sys_s.get('code_ok',True) else '❌ Eksik'}\n"
            f"⚡ Otonom karar: {auto_dec} toplam\n"
            f"❌ Üst üste hatalı: {bad_dec}/3\n"
            f"{'🛑 BEYİN KENDİNİ DURDURDU!' if self_stop else ''}\n"
            f"📊 Toplam analiz: {stats.get('total_analyses',0)}"
        )

    elif any(w in msg_l for w in ['mod','demo','gerçek']):
        return (
            f"🎮 Mod Farkındalığı\n\n"
            f"Şu an: {ma.get('current_mode','?').upper()}\n\n"
            f"Demo: {ma.get('demo_trades',0)} işlem | PnL: ${ma.get('demo_pnl',0):.2f} | WR: %{ma.get('demo_wr',0)}\n"
            f"Gerçek: {ma.get('real_trades',0)} işlem | PnL: ${ma.get('real_pnl',0):.2f} | WR: %{ma.get('real_wr',0)}\n\n"
            f"Demo'da: Öğrenirim, durdurmam\n"
            f"Gerçekte: Öğrenirim + uygularım + durdururum"
        )

    elif any(w in msg_l for w in ['karar','otonom','yaptığım','değişiklik']):
        decisions = brain.get('autonomous_decisions', [])[:10]
        if not decisions:
            return "Henüz otonom karar verilmedi."
        resp = f"⚡ Son {len(decisions)} Otonom Karar:\n\n"
        for d in decisions:
            icon = '✅' if d.get('outcome')=='good' else '❌' if d.get('outcome')=='bad' else '⏳'
            resp += f"{icon} [{d['type']}] {d['action']}\n   → {d['reason']}\n   Mod: {d['mode']} | {d['time'][:16]}\n\n"
        return resp

    elif any(w in msg_l for w in ['müdahale','müdahaleler','ne yaptı','yaptıkları']):
        ivs = brain.get('interventions', [])
        if not ivs:
            return "Henüz hiç müdahale yapılmadı."
        resp = f"🔧 Son {min(len(ivs),10)} Müdahale:\n\n"
        for iv in ivs[:10]:
            icon = '✅' if iv.get('outcome')=='good' else '❌' if iv.get('outcome')=='bad' else '⏳'
            resp += f"{icon} {iv['action']}\n"
            resp += f"   Neden: {iv['reason'][:60]}\n"
            if iv.get('result'):
                resp += f"   Sonuç: {iv['result']}\n"
            resp += f"   Mod: {iv['mode']} | {iv['time'][:16]}\n\n"
        good = sum(1 for iv in ivs if iv.get('outcome')=='good')
        bad  = sum(1 for iv in ivs if iv.get('outcome')=='bad')
        total_ev = sum(1 for iv in ivs if iv.get('outcome'))
        if total_ev:
            resp += f"📊 Doğruluk: {good}/{total_ev} → %{round(good/total_ev*100)}"
        return resp

    elif any(w in msg_l for w in ['filtre','filtreler','etkinlik','hangi filtre']):
        fs = brain.get('filter_stats', {})
        sr = brain.get('scan_reports', [])
        last_scan = sr[0] if sr else {}
        resp = "🔍 Filtre Etkinlik Analizi\n\n"
        for filt, data in fs.items():
            total = data['pass'] + data['fail']
            if total == 0:
                continue
            pct = round(data['pass'] / total * 100)
            bar = '█' * (pct // 10) + '░' * (10 - pct // 10)
            resp += f"{filt.upper()}: {bar} %{pct} geçti ({data['pass']}/{total})\n"
        if last_scan:
            resp += f"\nSon tarama: {last_scan.get('time','?')[:16]}\n"
            resp += f"Taranan: {last_scan.get('total_scanned',0)} coin\n"
            resp += f"Sinyal: {last_scan.get('signals_found',0)}\n"
            resp += f"TF: {last_scan.get('tf','?')}\n"
            cfg = last_scan.get('config', {})
            resp += f"Config: MinSkor:{cfg.get('min_score','?')} Trail:%{cfg.get('trail_pct','?')}"
        return resp

    elif any(w in msg_l for w in ['istatistik','güvenilir']):
        decisions = brain.get('autonomous_decisions', [])
        evaluated = [d for d in decisions if d.get('outcome')]
        total_dec = len(decisions)
        good  = sum(1 for d in evaluated if d['outcome'] == 'good')
        bad   = sum(1 for d in evaluated if d['outcome'] == 'bad')
        neut  = sum(1 for d in evaluated if d['outcome'] == 'neutral')
        acc   = round(good / len(evaluated) * 100, 1) if evaluated else 0
        demo_dec = sum(1 for d in decisions if d.get('mode') == 'demo')
        real_dec = sum(1 for d in decisions if d.get('mode') == 'real')
        by_type = {}
        for d in evaluated:
            t = d.get('type', '?')
            e = by_type.setdefault(t, {'good':0,'bad':0,'neutral':0})
            e[d['outcome']] += 1
        type_lines = ''
        for t, e in by_type.items():
            tot = e['good']+e['bad']+e['neutral']
            wr = round(e['good']/tot*100) if tot else 0
            type_lines += f"  {t}: {e['good']}✅ {e['bad']}❌ → %{wr} doğru\n"
        return (
            f"📊 Brain Karar İstatistiği\n\n"
            f"Toplam karar: {total_dec}\n"
            f"  Demo: {demo_dec} | Gerçek: {real_dec}\n\n"
            f"Değerlendirilen: {len(evaluated)}\n"
            f"  ✅ Doğru: {good}\n"
            f"  ❌ Yanlış: {bad}\n"
            f"  ➖ Nötr: {neut}\n"
            f"  🎯 Doğruluk: %{acc}\n\n"
            f"Karar tiplerine göre:\n{type_lines}\n"
            f"Üst üste hatalı: {brain.get('bad_decision_count',0)}/3\n"
            f"{'🛑 BEYİN KENDİNİ DURDURDU' if brain.get('brain_self_stopped') else '✅ Brain aktif'}"
        )

    elif any(w in msg_l for w in ['değerlendirme','doğru','yanlış','performansım']):
        evals = brain.get('decision_evaluations', [])[:5]
        correct = stats.get('correct_decisions', 0)
        wrong   = stats.get('wrong_decisions', 0)
        total_e = correct + wrong
        acc     = round(correct/total_e*100, 1) if total_e > 0 else 0
        resp = (
            f"📊 Beyin Öz Değerlendirmesi\n\n"
            f"Toplam değerlendirilen karar: {total_e}\n"
            f"Doğru: {correct} | Hatalı: {wrong}\n"
            f"Doğruluk: %{acc}\n"
            f"Üst üste hatalı: {brain.get('bad_decision_count',0)}/3\n\n"
        )
        if evals:
            resp += "Son değerlendirmeler:\n"
            for e in evals:
                icon = '✅' if e['outcome']=='good' else '❌' if e['outcome']=='bad' else '➖'
                resp += f"{icon} {e['action']} | WR:%{e['wr_after']} PnL:${e['pnl_after']}\n"
        return resp

    elif any(w in msg_l for w in ['performans','sonuç','kazanç','zarar','expectancy','para']):
        if not perf:
            return "Henüz yeterli trade verisi yok (min 3 işlem)."
        verdict = "🔴 Zarar üretiyor" if perf['real_expectancy']<0 else "🟢 Kâr üretiyor"
        return (
            f"📊 Performans\n\n"
            f"Toplam: {perf['total']} işlem\n"
            f"Win Rate: %{perf['win_rate']}\n"
            f"Avg Win: ${perf['avg_win']:.2f}\n"
            f"Avg Loss: ${perf['avg_loss']:.2f}\n"
            f"Expectancy: ${perf['expectancy']:.2f}\n"
            f"Maliyet dahil: ${perf['real_expectancy']:.2f}\n"
            f"Profit Factor: {perf['profit_factor']}\n"
            f"SL/TP/Manuel: {perf['sl_count']}/{perf['tp_count']}/{perf['mn_count']}\n\n"
            f"{verdict}"
        )

    elif any(w in msg_l for w in ['pattern','öğren','istatistik','analiz']):
        wr  = patterns.get('win_rate',0)
        exp = patterns.get('real_expectancy',0)
        return (
            f"🧠 Öğrenme & Pattern\n\n"
            f"Toplam trade: {learn.get('total_trades',0)}\n"
            f"Genel WR: %{wr}\n"
            f"Maliyet dahil Exp: ${exp:.2f}\n\n"
            f"✅ En iyi saatler (UTC): {learn.get('best_hours',[])[:4]}\n"
            f"❌ En kötü saatler: {learn.get('worst_hours',[])[:3]}\n\n"
            f"✅ En iyi TF: {learn.get('best_tfs',[])[:3]}\n"
            f"❌ En kötü TF: {learn.get('worst_tfs',[])[:3]}\n\n"
            f"Tüm filtreler OK WR: %{patterns.get('all_ok_wr',0)}\n"
            f"Hacim onaylı WR: %{patterns.get('high_vol_wr',0)}\n"
            f"Trend onaylı WR: %{patterns.get('trend_wr',0)}\n"
            f"Yüksek skor (85+) WR: %{patterns.get('high_score_wr',0)}"
        )

    elif any(w in msg_l for w in ['öneri','bekleyen','onay']):
        pending = brain.get('pending_proposals',[])
        if not pending:
            return "✅ Bekleyen öneri yok."
        resp = f"📋 {len(pending)} bekleyen öneri:\n"
        for i,p in enumerate(pending[:5],1):
            resp += f"\n{i}) [{p['type']}] {p['title']}\n   Güven: %{p['confidence']}\n"
        return resp

    elif any(w in msg_l for w in ['pozisyon','açık','coin']):
        positions = state.get('positions',[])
        if not positions:
            return "Açık pozisyon yok."
        resp = f"💼 {len(positions)} açık:\n"
        for pos in positions:
            entry = pos.get('entry',0)
            live  = pos.get('live_price',entry)
            pnl   = (live-entry)/entry*pos.get('pos_usd',400) if entry>0 else 0
            resp += f"\n• {pos.get('display_name','?')}: ${live:.4f} | ${pnl:+.2f}"
        return resp

    elif any(w in msg_l for w in ['kod','eksik','bütünlük']):
        i = check_code_integrity()
        if i['ok']:
            return f"✅ Kod tam. {i['present']}/{i['total']} özellik mevcut."
        return f"❌ Eksik ({len(i['missing'])}):\n" + '\n'.join([f"• {m['desc']}" for m in i['missing']])

    elif any(w in msg_l for w in ['ayar','config','parametre']):
        ah = state.get('active_hours',{})
        return (
            f"⚙️ Ayarlar\n\n"
            f"TF: {config.get('tf','?')}\n"
            f"Min Skor: {config.get('min_score','?')}\n"
            f"Trailing: %{config.get('trail_pct','?')}\n"
            f"Pozisyon: ${config.get('pos_usd','?')}\n"
            f"OTO TARA: {'Açık' if state.get('auto_scan') else 'Kapalı'}\n"
            f"OTO AL: {'Açık' if state.get('auto_trade') else 'Kapalı'}\n"
            f"Yasak Saat: {'Açık' if ah.get('active') else 'Kapalı'} ({ah.get('start','?')}-{ah.get('end','?')} UTC)"
        )

    elif any(w in msg_l for w in ['uyarı','warning']):
        ws = sys_s.get('warnings',[])[:5]
        if not ws:
            return "✅ Aktif uyarı yok."
        return "⚠️ Son uyarılar:\n" + '\n'.join([f"• {w['msg']}" for w in ws])

    else:
        return (
            f"🧠 Şunları sorabilirsin:\n\n"
            f"• 'durum' — sistem özeti\n"
            f"• 'mod' — demo/gerçek durumu\n"
            f"• 'müdahaleler' — brain ne yaptı, sonucu ne oldu\n"
            f"• 'filtreler' — hangi filtre ne kadar etkili\n"
            f"• 'karar' — otonom kararlar\n"
            f"• 'istatistik' — kaç karar doğru/yanlış\n"
            f"• 'değerlendirme' — beyin öz değerlendirmesi\n"
            f"• 'performans' — trade sonuçları\n"
            f"• 'pattern' — öğrenme & istatistik\n"
            f"• 'öneri' — bekleyen öneriler\n"
            f"• 'pozisyon' — açık işlemler\n"
            f"• 'kod' — kod bütünlüğü\n"
            f"• 'ayarlar' — parametreler\n"
            f"• 'uyarılar' — aktif uyarılar"
        )

# ══════════════════════════════════════════════════
# ANA ANALİZ DÖNGÜSÜ — Yeni adımlar eklendi
# ══════════════════════════════════════════════════

def analysis_loop():
    global _active
    blog('='*50,'START')
    blog('ARNA BRAIN v4.0 — Tam Otonom Mod başladı','START')

    while _active:
        try:
            with brain_lock:
                brain  = load_brain()

                # Brain kendini durdurduysa devam etme
                if brain.get('emergency_stop') or not brain.get('active',True):
                    if brain.get('brain_self_stopped'):
                        blog('🛑 Brain kendini durdurdu — analiz duraklatıldı', 'WARN')
                    time.sleep(ANALYSIS_INTERVAL)
                    continue

                state  = load_json(STATE_FILE,{})
                config = load_json(CONFIG_FILE,{})

                brain['system_state']['last_check'] = now_str()
                brain['stats']['total_analyses'] = brain['stats'].get('total_analyses',0)+1

                # 1) Demo/Gerçek mod farkındalığı
                mode = update_mode_awareness(brain, state)

                # 2) Öğren
                update_learning(brain, state)

                # 3) Performans analizi
                perf = analyze_performance(state)
                if perf:
                    brain['analysis_history'].insert(0,{'time':now_str(),**perf})
                    if len(brain['analysis_history'])>200:
                        brain['analysis_history'] = brain['analysis_history'][:200]
                    blog(f"Analiz [{mode.upper()}]: WR=%{perf['win_rate']} RealExp=${perf['real_expectancy']:.2f} PF={perf['profit_factor']}",'INFO')

                # 4) Manuel değişiklik takibi
                monitor_manual_changes(brain, state, config)

                # 5) Öneriler — demo ayrımı ile
                props = generate_proposals(brain, state, config, perf, mode)
                for p in props:
                    brain['pending_proposals'].append(p)
                    brain['stats']['proposals_made'] = brain['stats'].get('proposals_made',0)+1
                    blog(f"Öneri [{mode.upper()}]: {p['title']}",'PROPOSAL')

                # 6) Kod bütünlüğü
                integrity = check_code_integrity()
                brain['system_state']['code_ok'] = integrity['ok']

                # 7) Blocking — demo ayrımı ile
                apply_blocking(brain, mode)

                # 8) YENİ: Otonom trade kontrolü (demo/gerçek ayrımı)
                autonomous_trade_control(brain, state, perf, mode)

                # 9) YENİ: Otonom parametre optimizasyonu
                autonomous_optimize(brain, state, config, perf, mode)

                # 10) YENİ: Geçmiş kararları değerlendir
                evaluate_past_decisions(brain, state)

                # 11) YENİ: Müdahale sonuçlarını değerlendir
                evaluate_interventions(brain, state)

                save_brain(brain)

        except Exception as e:
            blog(f'Analiz döngü hata: {e}','ERROR')

        time.sleep(ANALYSIS_INTERVAL)

# ══════════════════════════════════════════════════
# WEB API — Yeni endpoint'ler eklendi
# ══════════════════════════════════════════════════

class BrainAPI(BaseHTTPRequestHandler):
    def log_message(self,*a): pass

    def _ok(self, data, ct='application/json'):
        self.send_response(200)
        self.send_header('Content-Type', ct+'; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin','*')
        self.end_headers()
        self.wfile.write(data if isinstance(data,bytes) else data.encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path in ('/brain','/brain/state'):
            with brain_lock:
                self._ok(json.dumps(load_brain(),ensure_ascii=False).encode())
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length',0))
        body   = self.rfile.read(length)
        try: cmd = json.loads(body)
        except: self.send_response(400); self.end_headers(); return

        reply = {'ok':True}

        with brain_lock:
            brain  = load_brain()
            action = cmd.get('action','')

            if action == 'approve_proposal':
                pid = cmd.get('proposal_id')
                for p in brain['pending_proposals']:
                    if p['id']==pid:
                        p['status']='approved'; p['approved_time']=now_str()
                        brain['approved_proposals'].insert(0,p)
                        brain['stats']['proposals_approved']=brain['stats'].get('proposals_approved',0)+1
                        blog(f'✅ Onaylandı: {p["title"]}','APPROVED')
                        if p.get('action')=='stop_auto_trade':
                            s=load_json(STATE_FILE,{}); s['auto_trade']=False; save_json(STATE_FILE,s)
                        break
                brain['pending_proposals']=[p for p in brain['pending_proposals'] if p['id']!=pid]

            elif action == 'reject_proposal':
                pid=cmd.get('proposal_id'); reason=cmd.get('reason','')
                for p in brain['pending_proposals']:
                    if p['id']==pid:
                        p['status']='rejected'; p['rejected_time']=now_str(); p['reject_reason']=reason
                        brain['rejected_proposals'].insert(0,p)
                        brain['stats']['proposals_rejected']=brain['stats'].get('proposals_rejected',0)+1
                        blog(f'❌ Reddedildi: {p["title"]}','REJECTED')
                        break
                brain['pending_proposals']=[p for p in brain['pending_proposals'] if p['id']!=pid]

            elif action == 'chat':
                msg=cmd.get('message','')
                resp=handle_chat(msg,brain)
                brain['conversations'].insert(0,{'time':now_str(),'user':msg,'brain':resp})
                if len(brain['conversations'])>300: brain['conversations']=brain['conversations'][:300]
                save_brain(brain)
                self._ok(json.dumps({'reply':resp}).encode()); return

            elif action == 'review_code':
                code=cmd.get('code',''); fname=cmd.get('filename','bot.py')
                result=review_code(code,fname)
                brain['code_reviews'].insert(0,result)
                if len(brain['code_reviews'])>50: brain['code_reviews']=brain['code_reviews'][:50]
                save_brain(brain)
                self._ok(json.dumps(result).encode()); return

            elif action == 'toggle_brain':
                brain['active']=not brain.get('active',True)
                brain['brain_self_stopped'] = False  # Manuel açılışta sıfırla
                brain['bad_decision_count'] = 0
                blog(f'Brain {"aktif" if brain["active"] else "pasif"}','INFO')

            elif action == 'emergency_stop':
                brain['emergency_stop']=not brain.get('emergency_stop',False)
                blog(f'Acil durdurma: {"AKTİF" if brain["emergency_stop"] else "KAPALI"}','CRITICAL')

            elif action == 'reset_brain':
                blog('🔄 Brain sıfırlandı','RESET')
                brain={**BRAIN_DEFAULT,'created':now_str(),'version':'4.0'}

            # YENİ: Bot'tan tarama raporu
            elif action == 'scan_report':
                process_scan_report(brain, cmd)
                save_brain(brain)
                self._ok(json.dumps({'ok': True}).encode()); return

            # YENİ: Müdahaleleri getir
            elif action == 'get_interventions':
                ivs = brain.get('interventions', [])[:20]
                save_brain(brain)
                self._ok(json.dumps({'interventions': ivs}).encode()); return

            # YENİ: Filtre istatistikleri
            elif action == 'get_filter_stats':
                fs = brain.get('filter_stats', {})
                sr = brain.get('scan_reports', [])[:5]
                self._ok(json.dumps({'filter_stats': fs, 'recent_scans': sr}).encode()); return

            # YENİ: Brain'i kendisi durdurduysa manuel kurtarma
            elif action == 'recover_brain':
                brain['brain_self_stopped'] = False
                brain['bad_decision_count'] = 0
                brain['active'] = True
                blog('🔄 Brain kurtarıldı — manuel reset', 'RESET')

            # YENİ: Otonom kararları görüntüle
            elif action == 'get_decisions':
                decisions = brain.get('autonomous_decisions', [])[:20]
                save_brain(brain)
                self._ok(json.dumps({'decisions': decisions}).encode()); return

            save_brain(brain)

        self._ok(json.dumps(reply).encode())

def run_api():
    server=HTTPServer(('0.0.0.0',BRAIN_PORT),BrainAPI)
    blog(f'Brain API port {BRAIN_PORT}','START')
    server.serve_forever()

def main():
    global _active
    blog('ARNA BRAIN v4.0 başlatılıyor...','START')
    blog(f'Veri dosyası: {BRAIN_DATA}','START')
    blog('YENİ: Demo/Gerçek ayrımı | Otonom karar | Öz değerlendirme | 3 hatalı → dur','START')

    threading.Thread(target=run_api, daemon=True).start()

    brain = load_brain()
    threading.Thread(target=monitor_positions_live, args=(brain,), daemon=True).start()

    try:
        analysis_loop()
    except KeyboardInterrupt:
        _active=False
        blog('Brain durduruldu','STOP')

if __name__=='__main__':
    main()
