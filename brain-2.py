#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARNA BRAIN v3.0 — Canlı, Öğrenen, Kalıcı Beyin
Tüm veriler /root/brain_data.json'da — deploy, restart, kapanış hiçbir şeyi silmez.
Pozisyon: her 5sn | Genel analiz: her 60sn | Öğrenme: gelişmiş pattern
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

TRADE_COST       = 1.60
POS_INTERVAL     = 5
ANALYSIS_INTERVAL= 60
BRAIN_PORT       = 8766

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
    'version': '3.0',
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
    }
}

def load_brain():
    data = load_json(BRAIN_DATA, {})
    # Eksik alanları default ile tamamla — veri kaybolmaz
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
# GELİŞMİŞ ÖĞRENME
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
    # avg win/loss per tf
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

    # Yüksek skor (>=85) kazandırıyor mu?
    high_score = [h for h in closed if h.get('score',0)>=85]
    patterns['high_score_win']  = len([h for h in high_score if h['result']=='win'])
    patterns['high_score_loss'] = len([h for h in high_score if h['result']=='lose'])
    patterns['high_score_wr']   = round(patterns['high_score_win']/len(high_score)*100,1) if high_score else 0

    # Düşük RSI (<50) işlemler
    low_rsi = [h for h in closed if h.get('rsi_ok')==False]
    patterns['low_rsi_win']  = len([h for h in low_rsi if h['result']=='win'])
    patterns['low_rsi_loss'] = len([h for h in low_rsi if h['result']=='lose'])

    # Hacim onaylı işlemler
    high_vol = [h for h in closed if h.get('vol_ok')==True]
    patterns['high_vol_win']  = len([h for h in high_vol if h['result']=='win'])
    patterns['high_vol_loss'] = len([h for h in high_vol if h['result']=='lose'])
    patterns['high_vol_wr']   = round(patterns['high_vol_win']/len(high_vol)*100,1) if high_vol else 0

    # Trend onaylı işlemler
    trend_ok = [h for h in closed if h.get('trend_pass')==True]
    patterns['trend_win']  = len([h for h in trend_ok if h['result']=='win'])
    patterns['trend_loss'] = len([h for h in trend_ok if h['result']=='lose'])
    patterns['trend_wr']   = round(patterns['trend_win']/len(trend_ok)*100,1) if trend_ok else 0

    # Tüm indikatörler onaylı
    all_ok = [h for h in closed if h.get('trend_pass') and h.get('vol_ok') and h.get('rsi_ok') and h.get('boll_ok')]
    patterns['all_ok_win']  = len([h for h in all_ok if h['result']=='win'])
    patterns['all_ok_loss'] = len([h for h in all_ok if h['result']=='lose'])
    patterns['all_ok_wr']   = round(patterns['all_ok_win']/len(all_ok)*100,1) if all_ok else 0

    # Expectancy per pattern
    if wins:
        patterns['avg_win_usd']  = round(sum(h['pnl'] for h in wins)/len(wins),2)
    if losses:
        patterns['avg_loss_usd'] = round(sum(h['pnl'] for h in losses)/len(losses),2)

    # Maliyet dahil gerçek expectancy
    if closed:
        wr  = len(wins)/len(closed)
        exp = wr*patterns.get('avg_win_usd',0) + (1-wr)*patterns.get('avg_loss_usd',0)
        patterns['real_expectancy'] = round(exp - TRADE_COST, 2)
        patterns['win_rate']        = round(wr*100, 1)

# ══════════════════════════════════════════════════
# PERFORMANS ANALİZİ
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
# CANLI POZİSYON TAKİBİ
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

                    # RR oranı
                    sl_dist = entry - trail_sl if trail_sl > 0 else 0
                    tp_dist = target_tp - entry if target_tp > 0 else 0
                    rr = tp_dist/sl_dist if sl_dist > 0 else 0

                    # Log kaydı
                    log_entry = {
                        'time': now_str(),
                        'coin': name,
                        'pnl_pct': round(pnl_pct,2),
                        'pnl_usd': round(pnl_usd,2),
                        'rr': round(rr,2),
                        'live': live
                    }
                    pos_log.insert(0, log_entry)

                    # Uyarılar
                    # 1) Zarar maliyet aşmış, SL uzak
                    if pnl_usd < -TRADE_COST and sl_dist > 0:
                        sl_dist_pct = sl_dist/entry*100
                        if sl_dist_pct > 2.5:
                            alerts.append({
                                'coin':name,'type':'WARNING',
                                'msg':f'{name} zararda (${pnl_usd:.2f}) ama SL %{sl_dist_pct:.1f} uzakta. Dikkat.',
                                'time':now_str()
                            })

                    # 2) RR kötü
                    if 0 < rr < 1.5 and pnl_pct > 0:
                        alerts.append({
                            'coin':name,'type':'INFO',
                            'msg':f'{name} RR oranı düşük: {rr:.1f}:1 (min 2:1 önerilir)',
                            'time':now_str()
                        })

                    # 3) TP'ye çok yakın ama trend zayıflıyor olabilir
                    if tp_pct > 0 and pnl_pct >= tp_pct*0.85:
                        alerts.append({
                            'coin':name,'type':'INFO',
                            'msg':f'{name} TP hedefine %{((pnl_pct/tp_pct)*100):.0f} yakın (${pnl_usd:+.2f})',
                            'time':now_str()
                        })

                    # 4) Çok hızlı zarar
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
# ÖNERİ ÜRETİCİ
# ══════════════════════════════════════════════════

def generate_proposals(brain, state, config, perf):
    if not perf:
        return []
    proposals = []
    pending_subjects = {p['subject'] for p in brain['pending_proposals']}

    # 1) Sistem zarar üretiyor
    if perf['real_expectancy'] < -2.0 and perf['total'] >= 10:
        s = 'critical_stop_trading'
        if s not in pending_subjects:
            proposals.append({
                'id':gen_id(),'subject':s,'type':'CRITICAL','priority':1,
                'title':'🚨 SİSTEM ZARAR ÜRETİYOR — Alım Durdurulsun mu?',
                'detail':(
                    f'Maliyet dahil expectancy: ${perf["real_expectancy"]:.2f}/trade\n'
                    f'Win Rate: %{perf["win_rate"]} | PF: {perf["profit_factor"]}\n'
                    f'Toplam zarar: ${perf["gross_loss"]-perf["gross_win"]:.2f}\n'
                    f'Onaylarsan OTO AL durur.'
                ),
                'action':'stop_auto_trade','confidence':95,
                'created':now_str(),'status':'pending',
                'blocking':True,'effect':'OTO AL kapatılır'
            })
            blog('ACİL öneri: sistem zarar üretiyor','CRITICAL')

    # 2) SL/TP oranı berbat
    if perf['sl_count'] > perf['tp_count']*4 and perf['total']>=8:
        s = 'sl_tp_bad'
        if s not in pending_subjects:
            proposals.append({
                'id':gen_id(),'subject':s,'type':'WARNING','priority':2,
                'title':'⚠️ SL/TP Oranı Kritik',
                'detail':f'SL:{perf["sl_count"]} / TP:{perf["tp_count"]} / Manuel:{perf["mn_count"]}\nStop çok sık yeniyor. Trailing veya giriş kalitesi gözden geçirilmeli.',
                'action':None,'confidence':85,'created':now_str(),'status':'pending',
                'blocking':False,'effect':'Bilgi amaçlı'
            })

    # 3) Profit factor < 1
    if perf['profit_factor'] < 1.0 and perf['total']>=10:
        s = 'pf_negative'
        if s not in pending_subjects:
            proposals.append({
                'id':gen_id(),'subject':s,'type':'WARNING','priority':2,
                'title':'⚠️ Profit Factor 1.0 Altında',
                'detail':f'PF: {perf["profit_factor"]}\nKazanç: ${perf["gross_win"]:.2f} / Kayıp: ${perf["gross_loss"]:.2f}\nKayıplar kazançları geçiyor.',
                'action':None,'confidence':80,'created':now_str(),'status':'pending',
                'blocking':False,'effect':'Bilgi amaçlı'
            })

    # 4) TF zarar
    for tf, stats in perf.get('tf_stats',{}).items():
        if stats['count']>=5 and stats['pnl']<-8.0:
            s = f'tf_losing_{tf}'
            if s not in pending_subjects:
                proposals.append({
                    'id':gen_id(),'subject':s,'type':'SUGGESTION','priority':3,
                    'title':f'📊 {tf} TF Zarar Üretiyor',
                    'detail':f'{tf}: {stats["count"]} işlem, ${stats["pnl"]:.2f} toplam\nBu TF\'yi değiştirmeyi düşünün.',
                    'action':None,'confidence':75,'created':now_str(),'status':'pending',
                    'blocking':False,'effect':'TF değişikliği önerisi'
                })

    # 5) ASIA seansı kötü
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

    # 6) Pattern öğrenmesinden öneri
    patterns = brain['learning'].get('patterns',{})
    # Tüm indikatörler onaylıyken WR yüksekse öneri
    all_ok_wr = patterns.get('all_ok_wr', 0)
    if all_ok_wr > 60 and patterns.get('all_ok_win',0)+patterns.get('all_ok_loss',0) >= 5:
        s = 'all_indicators_good'
        if s not in pending_subjects:
            total_aok = patterns.get('all_ok_win',0)+patterns.get('all_ok_loss',0)
            proposals.append({
                'id':gen_id(),'subject':s,'type':'INSIGHT','priority':4,
                'title':f'💡 Tüm İndikatörler Onaylıyken WR %{all_ok_wr}',
                'detail':f'{total_aok} işlemde tüm filtreler geçti, WR %{all_ok_wr}\nMin skoru yükseltmek edge artırabilir.',
                'action':None,'confidence':70,'created':now_str(),'status':'pending',
                'blocking':False,'effect':'Skor optimizasyonu önerisi'
            })

    return proposals

# ══════════════════════════════════════════════════
# MANUEL DEĞİŞİKLİK TAKİBİ
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

    # TF
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

    # Trailing
    if prev.get('trail_pct') != config.get('trail_pct'):
        old_t, new_t = prev.get('trail_pct','?'), config.get('trail_pct','?')
        try:
            nt = float(new_t)
            if nt > 3.5:
                warnings.append({'type':'WARNING','msg':f'⚠️ Trailing %{old_t}→%{new_t}: Çok geniş, gürültüde SL yenmez ama kâr geride kalır.','time':now_str()})
            elif nt < 0.8:
                warnings.append({'type':'WARNING','msg':f'⚠️ Trailing %{old_t}→%{new_t}: Çok dar, anlık dalgalanmada SL yenebilir.','time':now_str()})
            else:
                warnings.append({'type':'OK','msg':f'✅ Trailing %{old_t}→%{new_t}: Makul.','time':now_str()})
        except: pass

    # Min skor
    if prev.get('min_score') != config.get('min_score'):
        old_s, new_s = prev.get('min_score','?'), config.get('min_score','?')
        try:
            ns = int(new_s)
            if ns < 75:
                warnings.append({'type':'WARNING','msg':f'⚠️ Min Skor {old_s}→{new_s}: Çok düşük, kalitesiz sinyaller girebilir.','time':now_str()})
            elif ns > 92:
                warnings.append({'type':'WARNING','msg':f'⚠️ Min Skor {old_s}→{new_s}: Çok yüksek, sinyal kuruyabilir.','time':now_str()})
            else:
                warnings.append({'type':'OK','msg':f'✅ Min Skor {old_s}→{new_s}: Makul.','time':now_str()})
        except: pass

    # Pozisyon büyüklüğü
    if prev.get('pos_usd') != config.get('pos_usd'):
        old_p, new_p = prev.get('pos_usd','?'), config.get('pos_usd','?')
        try:
            if float(new_p) > 600:
                warnings.append({'type':'WARNING','msg':f'⚠️ Pozisyon ${old_p}→${new_p}: Büyük pozisyon riski artırır.','time':now_str()})
            else:
                warnings.append({'type':'OK','msg':f'✅ Pozisyon ${old_p}→${new_p}','time':now_str()})
        except: pass

    # OTO AL değişimi
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
# KOD BÜTÜNLÜĞÜ
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
# BLOCKING
# ══════════════════════════════════════════════════

def apply_blocking(brain):
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
# SOHBET
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

    if any(w in msg_l for w in ['durum','nasıl','özet','ne var']):
        pending  = len(brain.get('pending_proposals',[]))
        blocked  = sys_s.get('trading_blocked',False)
        pos_count= len(state.get('positions',[]))
        return (
            f"🧠 Sistem Durumu [{now_str()}]\n\n"
            f"📋 Bekleyen öneri: {pending}\n"
            f"💼 Açık pozisyon: {pos_count}\n"
            f"🤖 Alım: {'⛔ DURDURULDU' if blocked else '✅ Aktif'}\n"
            f"🔧 Kod: {'✅ Tam' if sys_s.get('code_ok',True) else '❌ Eksik'}\n"
            f"📊 Toplam analiz: {stats.get('total_analyses',0)}\n"
            f"👁 Pozisyon kontrol: {stats.get('total_pos_checks',0)}\n"
            f"🕐 Son kontrol: {sys_s.get('last_check','?')}\n"
            f"🕐 Son pos kontrol: {sys_s.get('last_pos_check','?')}"
        )

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
            f"Maliyet (${TRADE_COST}) dahil: ${perf['real_expectancy']:.2f}\n"
            f"Profit Factor: {perf['profit_factor']}\n"
            f"SL/TP/Manuel: {perf['sl_count']}/{perf['tp_count']}/{perf['mn_count']}\n"
            f"Kazanç: ${perf['gross_win']:.2f} / Kayıp: ${perf['gross_loss']:.2f}\n\n"
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
            f"✅ En iyi seans: {learn.get('best_sessions',[])[:3]}\n\n"
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
        alerts = brain.get('position_alerts',[])
        if alerts:
            resp += f"\n\n⚠️ {len(alerts)} uyarı aktif"
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

    elif any(w in msg_l for w in ['maliyet','komisyon','fee']):
        return (
            f"💰 Maliyet Analizi\n\n"
            f"İşlem başı maliyet: ${TRADE_COST}\n"
            f"Kâr için min: ${TRADE_COST:.2f} üzeri kazanç\n"
            f"Mevcut Avg Win: ${perf['avg_win'] if perf else '?':.2f if perf else ''}\n"
            f"Maliyet sonrası Avg Win: ${(perf['avg_win']-TRADE_COST) if perf else '?':.2f if perf else ''}"
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
            f"• 'performans' — trade sonuçları\n"
            f"• 'pattern' — öğrenme & istatistik\n"
            f"• 'öneri' — bekleyen öneriler\n"
            f"• 'pozisyon' — açık işlemler\n"
            f"• 'kod' — kod bütünlüğü\n"
            f"• 'ayarlar' — parametreler\n"
            f"• 'maliyet' — işlem maliyeti\n"
            f"• 'uyarılar' — aktif uyarılar"
        )

# ══════════════════════════════════════════════════
# ANA ANALİZ DÖNGÜSÜ (60 saniye)
# ══════════════════════════════════════════════════

def analysis_loop():
    global _active
    blog('='*50,'START')
    blog('ARNA BRAIN v3.0 — Analiz döngüsü başladı','START')

    while _active:
        try:
            with brain_lock:
                brain  = load_brain()
                if brain.get('emergency_stop') or not brain.get('active',True):
                    time.sleep(ANALYSIS_INTERVAL)
                    continue

                state  = load_json(STATE_FILE,{})
                config = load_json(CONFIG_FILE,{})

                brain['system_state']['last_check'] = now_str()
                brain['stats']['total_analyses'] = brain['stats'].get('total_analyses',0)+1

                # 1) Öğren
                update_learning(brain, state)

                # 2) Performans analizi
                perf = analyze_performance(state)
                if perf:
                    brain['analysis_history'].insert(0,{'time':now_str(),**perf})
                    if len(brain['analysis_history'])>200:
                        brain['analysis_history'] = brain['analysis_history'][:200]
                    blog(f"Analiz: WR=%{perf['win_rate']} RealExp=${perf['real_expectancy']:.2f} PF={perf['profit_factor']}",'INFO')

                # 3) Manuel değişiklik takibi
                monitor_manual_changes(brain, state, config)

                # 4) Öneriler
                props = generate_proposals(brain, state, config, perf)
                for p in props:
                    brain['pending_proposals'].append(p)
                    brain['stats']['proposals_made'] = brain['stats'].get('proposals_made',0)+1
                    blog(f"Öneri: {p['title']}",'PROPOSAL')

                # 5) Kod bütünlüğü
                integrity = check_code_integrity()
                brain['system_state']['code_ok'] = integrity['ok']

                # 6) Blocking
                apply_blocking(brain)

                save_brain(brain)

        except Exception as e:
            blog(f'Analiz döngü hata: {e}','ERROR')

        time.sleep(ANALYSIS_INTERVAL)

# ══════════════════════════════════════════════════
# WEB API
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
                blog(f'Brain {"aktif" if brain["active"] else "pasif"}','INFO')

            elif action == 'emergency_stop':
                brain['emergency_stop']=not brain.get('emergency_stop',False)
                blog(f'Acil durdurma: {"AKTİF" if brain["emergency_stop"] else "KAPALI"}','CRITICAL')

            elif action == 'reset_brain':
                blog('🔄 Brain sıfırlandı','RESET')
                brain={**BRAIN_DEFAULT,'created':now_str(),'version':'3.0'}

            save_brain(brain)

        self._ok(json.dumps(reply).encode())

def run_api():
    server=HTTPServer(('0.0.0.0',BRAIN_PORT),BrainAPI)
    blog(f'Brain API port {BRAIN_PORT}','START')
    server.serve_forever()

def main():
    global _active
    blog('ARNA BRAIN v3.0 başlatılıyor...','START')
    blog(f'Veri dosyası: {BRAIN_DATA}','START')
    blog('Bu dosya silinmediği sürece tüm veriler korunur.','START')

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
