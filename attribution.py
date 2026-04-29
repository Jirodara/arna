#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARNA Alpha Attribution Analizi
Her filtrenin gerçek katkısını ölçer.
Kullanım: python3 /root/attribution.py
"""

import json, math
from datetime import datetime

STATE_FILE = '/root/arna_state.json'
LEARN_FILE = '/root/arna_learn.json'

def load():
    try:
        with open(STATE_FILE) as f: state = json.load(f)
    except: state = {}
    try:
        with open(LEARN_FILE) as f: learn = json.load(f)
    except: learn = {}
    return state, learn

def expectancy(trades):
    if not trades: return 0, 0, 0, 0
    wins   = [t for t in trades if t.get('result') == 'win']
    losses = [t for t in trades if t.get('result') == 'lose']
    if not wins and not losses: return 0, 0, 0, 0
    wr      = len(wins) / len(trades)
    avg_win  = sum(t.get('pnl_pct', 0) for t in wins)  / len(wins)  if wins   else 0
    avg_loss = sum(t.get('pnl_pct', 0) for t in losses) / len(losses) if losses else 0
    exp     = (wr * avg_win) + ((1 - wr) * avg_loss)
    pf      = abs(sum(t.get('pnl_pct',0) for t in wins) / sum(t.get('pnl_pct',0) for t in losses)) if losses and sum(t.get('pnl_pct',0) for t in losses) != 0 else 0
    return exp, wr * 100, avg_win, avg_loss

def profit_factor(trades):
    wins   = sum(t.get('pnl_pct', 0) for t in trades if t.get('result') == 'win')
    losses = abs(sum(t.get('pnl_pct', 0) for t in trades if t.get('result') == 'lose'))
    return wins / losses if losses > 0 else 0

def analyze_filter(all_trades, filter_key, filter_name):
    with_filter    = [t for t in all_trades if t.get(filter_key) == 1]
    without_filter = [t for t in all_trades if t.get(filter_key) == 0]
    
    exp_w, wr_w, aw_w, al_w = expectancy(with_filter)
    exp_wo, wr_wo, aw_wo, al_wo = expectancy(without_filter)
    pf_w  = profit_factor(with_filter)
    pf_wo = profit_factor(without_filter)
    
    contribution = exp_w - exp_wo
    
    return {
        'name': filter_name,
        'key': filter_key,
        'with': {
            'count': len(with_filter),
            'wr': wr_w,
            'expectancy': exp_w,
            'avg_win': aw_w,
            'avg_loss': al_wo,
            'profit_factor': pf_w
        },
        'without': {
            'count': len(without_filter),
            'wr': wr_wo,
            'expectancy': exp_wo,
            'avg_win': aw_wo,
            'avg_loss': al_wo,
            'profit_factor': pf_wo
        },
        'contribution': contribution
    }

def session_analysis(trades):
    sessions = {}
    for t in trades:
        s = t.get('session', 'UNKNOWN')
        if s not in sessions: sessions[s] = []
        sessions[s].append(t)
    result = {}
    for s, ts in sessions.items():
        exp, wr, aw, al = expectancy(ts)
        result[s] = {'count': len(ts), 'wr': wr, 'expectancy': exp, 'profit_factor': profit_factor(ts)}
    return result

def tf_analysis(trades):
    tfs = {}
    for t in trades:
        tf = t.get('tf', '?')
        if tf not in tfs: tfs[tf] = []
        tfs[tf].append(t)
    result = {}
    for tf, ts in tfs.items():
        exp, wr, aw, al = expectancy(ts)
        result[tf] = {'count': len(ts), 'wr': wr, 'expectancy': exp, 'profit_factor': profit_factor(ts)}
    return result

def hour_analysis(trades):
    hours = {}
    for t in trades:
        h = t.get('hour', -1)
        if h not in hours: hours[h] = []
        hours[h].append(t)
    result = {}
    for h, ts in hours.items():
        exp, wr, aw, al = expectancy(ts)
        result[h] = {'count': len(ts), 'wr': wr, 'expectancy': exp}
    return result

def state_history_analysis(history):
    """arna_state.json'daki history'den analiz"""
    closed = [h for h in history if h.get('result') in ['win','lose'] and h.get('pnl') is not None]
    if not closed:
        return None
    wins   = [h for h in closed if h['result'] == 'win']
    losses = [h for h in closed if h['result'] == 'lose']
    avg_win  = sum(h['pnl'] for h in wins)   / len(wins)   if wins   else 0
    avg_loss = sum(h['pnl'] for h in losses) / len(losses) if losses else 0
    wr = len(wins) / len(closed)
    exp = (wr * avg_win) + ((1-wr) * avg_loss)
    pf  = abs(sum(h['pnl'] for h in wins) / sum(h['pnl'] for h in losses)) if losses and sum(h['pnl'] for h in losses) != 0 else 0

    # Çıkış nedeni analizi
    exit_reasons = {}
    for h in closed:
        r = h.get('exit_reason', '?')
        if r not in exit_reasons: exit_reasons[r] = {'count':0,'pnl':0}
        exit_reasons[r]['count'] += 1
        exit_reasons[r]['pnl']   += h['pnl']

    # TF analizi
    tf_stats = {}
    for h in closed:
        tf = h.get('tf', '?')
        if tf not in tf_stats: tf_stats[tf] = {'wins':0,'losses':0,'pnl':0}
        if h['result'] == 'win': tf_stats[tf]['wins'] += 1
        else: tf_stats[tf]['losses'] += 1
        tf_stats[tf]['pnl'] += h['pnl']

    return {
        'total': len(closed), 'wins': len(wins), 'losses': len(losses),
        'win_rate': wr * 100, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'expectancy': exp, 'profit_factor': pf,
        'exit_reasons': exit_reasons, 'tf_stats': tf_stats
    }

def print_report(state, learn):
    trades  = learn.get('trades', [])
    history = state.get('history', [])
    
    print("=" * 60)
    print("   ARNA ALPHA ATTRIBUTION RAPORU")
    print(f"   {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 60)

    # State history analizi
    sh = state_history_analysis(history)
    if sh:
        print(f"\n📊 GENEL PERFORMANS ({sh['total']} işlem)")
        print(f"   Win Rate  : %{sh['win_rate']:.1f}")
        print(f"   Avg Win   : ${sh['avg_win']:.2f}")
        print(f"   Avg Loss  : ${sh['avg_loss']:.2f}")
        print(f"   Expectancy: ${sh['expectancy']:.2f} per trade")
        print(f"   Profit Factor: {sh['profit_factor']:.2f}")

        print(f"\n📤 ÇIKIŞ NEDENLERİ")
        for r, d in sorted(sh['exit_reasons'].items(), key=lambda x: x[1]['count'], reverse=True):
            avg = d['pnl'] / d['count'] if d['count'] else 0
            print(f"   {r:12s}: {d['count']:3d} işlem | Toplam: ${d['pnl']:+.2f} | Avg: ${avg:+.2f}")

        print(f"\n⏱️  ZAMAN DİLİMİ PERFORMANSI")
        for tf, d in sorted(sh['tf_stats'].items()):
            total_tf = d['wins'] + d['losses']
            wr_tf = d['wins']/total_tf*100 if total_tf else 0
            avg_tf = d['pnl']/total_tf if total_tf else 0
            print(f"   {tf:6s}: {total_tf:3d} işlem | WR: %{wr_tf:.0f} | Toplam: ${d['pnl']:+.2f} | Avg: ${avg_tf:+.2f}")

    # Learn trades analizi
    if not trades:
        print("\n⚠️  learn.json'da yeterli trade verisi yok")
        return

    print(f"\n🔬 FİLTRE ATTRIBUTION ANALİZİ ({len(trades)} trade)")
    print("-" * 60)

    filters = [
        ('trend', 'Trend (MA+MACD)'),
        ('rsi',   'RSI Filtresi'),
        ('vol',   'Hacim Filtresi'),
        ('boll',  'Bollinger Filtresi'),
    ]

    results = []
    for key, name in filters:
        r = analyze_filter(trades, key, name)
        results.append(r)

    # Katkıya göre sırala
    results.sort(key=lambda x: x['contribution'], reverse=True)

    for r in results:
        w  = r['with']
        wo = r['without']
        contrib = r['contribution']
        icon = "✅" if contrib > 0 else "⚠️" if contrib > -0.5 else "❌"
        
        print(f"\n{icon} {r['name']}")
        print(f"   AKTİF   ({w['count']:3d} trade): WR=%{w['wr']:.0f} | Exp={w['expectancy']:+.2f}% | PF={w['profit_factor']:.2f}")
        print(f"   PASİF   ({wo['count']:3d} trade): WR=%{wo['wr']:.0f} | Exp={wo['expectancy']:+.2f}% | PF={wo['profit_factor']:.2f}")
        print(f"   KATKI   : {contrib:+.2f}% {'⬆ EDGEKATKIsı' if contrib > 0.5 else '➡ Nötr' if contrib > -0.5 else '⬇ EDGE ÖLDÜRÜYOR?'}")

    # Session analizi
    print(f"\n🌍 SEANS ANALİZİ")
    print("-" * 60)
    sessions = session_analysis(trades)
    for s, d in sorted(sessions.items(), key=lambda x: x[1]['expectancy'], reverse=True):
        icon = "✅" if d['expectancy'] > 0 else "❌"
        print(f"   {icon} {s:6s}: {d['count']:3d} trade | WR=%{d['wr']:.0f} | Exp={d['expectancy']:+.2f}% | PF={d['profit_factor']:.2f}")

    # TF analizi (learn)
    print(f"\n⏱️  TF ATTRIBUTION (learn verisi)")
    print("-" * 60)
    tfs = tf_analysis(trades)
    for tf, d in sorted(tfs.items(), key=lambda x: x[1]['expectancy'], reverse=True):
        icon = "✅" if d['expectancy'] > 0 else "❌"
        print(f"   {icon} {tf:6s}: {d['count']:3d} trade | WR=%{d['wr']:.0f} | Exp={d['expectancy']:+.2f}% | PF={d['profit_factor']:.2f}")

    # Saat analizi
    print(f"\n🕐 SAAT ATTRIBUTION (En iyi/kötü 5)")
    print("-" * 60)
    hours = hour_analysis(trades)
    sorted_hours = sorted(hours.items(), key=lambda x: x[1]['expectancy'], reverse=True)
    print("   En iyi saatler:")
    for h, d in sorted_hours[:5]:
        if d['count'] >= 2:
            print(f"   ✅ UTC {h:02d}:xx: {d['count']:3d} trade | WR=%{d['wr']:.0f} | Exp={d['expectancy']:+.2f}%")
    print("   En kötü saatler:")
    for h, d in sorted_hours[-5:]:
        if d['count'] >= 2:
            print(f"   ❌ UTC {h:02d}:xx: {d['count']:3d} trade | WR=%{d['wr']:.0f} | Exp={d['expectancy']:+.2f}%")

    # Ablation testi
    print(f"\n🧪 ABLATION TESTİ")
    print("   (Filtre olmasaydı ne olurdu?)")
    print("-" * 60)
    all_exp, all_wr, _, _ = expectancy(trades)
    print(f"   Tüm filtrelerle : Exp={all_exp:+.2f}% | WR=%{all_wr:.0f}")
    
    for r in results:
        # Bu filtreyi çıkar, kalanları değerlendir
        remaining = [t for t in trades if t.get(r['key']) == 1 or True]  # tüm tradeler
        # Sadece bu filtre 0 olanları filtrele
        ablated = [t for t in trades if t.get(r['key']) != 0]
        exp_a, wr_a, _, _ = expectancy(ablated)
        diff = exp_a - all_exp
        icon = "🟢" if diff > 0.1 else "🔴" if diff < -0.1 else "⚪"
        print(f"   {icon} {r['name']:25s} çıkarılırsa: Exp={exp_a:+.2f}% (fark: {diff:+.2f}%)")

    # Öğrenme ağırlıkları
    weights = learn.get('weights', {})
    if weights:
        print(f"\n🧠 ÖĞRENME AĞIRLIKLARI")
        print("-" * 60)
        for k, v in weights.items():
            bar = "█" * int(v * 10)
            status = "güçlenmiş" if v > 1.1 else "zayıflamış" if v < 0.9 else "nötr"
            print(f"   {k:8s}: {v:.2f} {bar} ({status})")

    print("\n" + "=" * 60)
    print("   SONUÇ VE ÖNERİLER")
    print("=" * 60)
    
    if sh:
        if sh['expectancy'] > 0:
            print("   ✅ Pozitif expectancy — edge işaretleri var")
        else:
            print("   ❌ Negatif expectancy — sistem zarar üretiyor")
            print("   → En kötü katkılı filtreyi gevşet")
            print("   → Min skoru 80'den 75'e dene")
        
        if sh['profit_factor'] < 1.0:
            print("   ⚠️  Profit Factor < 1.0 — kayıplar kazançları geçiyor")
            print("   → TP/SL oranını gözden geçir")
        
        sl_data = sh['exit_reasons'].get('SL', {})
        tp_data = sh['exit_reasons'].get('TP', {})
        if sl_data.get('count',0) > tp_data.get('count',0):
            print(f"   ⚠️  SL ({sl_data.get('count',0)}) > TP ({tp_data.get('count',0)}) — çok fazla stop yeniyor")

    print("\n   Veri: attribution.py | ARNA Bot")
    print("=" * 60)

if __name__ == '__main__':
    state, learn = load()
    print_report(state, learn)
