import requests, time, json, hmac, hashlib, threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

CONFIG_FILE = '/root/arna_config.json'
STATE_FILE = '/root/arna_state.json'

def load_config():
    try:
        with open(CONFIG_FILE) as f: return json.load(f)
    except: return {}

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f: json.dump(state, f)
    except: pass

def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except:
        return {'demo_bal':2000,'demo_pnl':0,'positions':[],'history':[],
                'signals':[],'logs':[],'scanning':False,'auto_scan':False,
                'auto_trade':False,'last_scan':0,'mode':'demo'}

def log_msg(state, msg, cls=''):
    t = datetime.now().strftime('%H:%M:%S')
    state['logs'].insert(0,{'time':t,'msg':msg,'cls':cls})
    state['logs'] = state['logs'][:100]
    print(f"[{t}] {msg}")

BINANCE_BASE='https://api.binance.com/api/v3'
VISION_BASE='https://data-api.binance.vision/api/v3'

def binance_get(path, params=None):
    for base in [VISION_BASE, BINANCE_BASE]:
        try:
            r = requests.get(base+path, params=params, timeout=10)
            if r.ok: return r.json()
        except: continue
    return None

def binance_signed(method, path, params, api_key, api_secret):
    params['timestamp'] = int(time.time()*1000)
    params['recvWindow'] = 5000
    query = '&'.join(f'{k}={v}' for k,v in params.items())
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    query += f'&signature={sig}'
    headers = {'X-MBX-APIKEY': api_key}
    url = BINANCE_BASE + path
    try:
        if method == 'GET':
            r = requests.get(url+'?'+query, headers=headers, timeout=10)
        else:
            r = requests.post(url, data=query, headers=headers, timeout=10)
        return r.json()
    except Exception as e: return {'error':str(e)}

def get_price(symbol):
    d = binance_get('/ticker/price', {'symbol':symbol})
    return float(d['price']) if d and 'price' in d else None

def get_klines(symbol, interval, limit=60):
    d = binance_get('/klines', {'symbol':symbol,'interval':interval,'limit':limit})
    if not d: return []
    return [{'o':float(k[1]),'h':float(k[2]),'l':float(k[3]),'c':float(k[4]),'v':float(k[5])} for k in d]

def sma(v, n):
    return sum(v[-n:])/n if len(v)>=n else (v[-1] if v else 0)

def calc_rsi(closes, p=14):
    if len(closes)<p+1: return 50
    gains=[max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
    losses=[max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
    ag=sum(gains[-p:])/p; al=sum(losses[-p:])/p
    return 100 if al==0 else 100-100/(1+ag/al)

def calc_macd(closes):
    if len(closes)<26: return 0,0,0
    def ema(d,n):
        k=2/(n+1); e=d[0]
        for v in d[1:]: e=v*k+e*(1-k)
        return e
    line=ema(closes[-26:],12)-ema(closes[-26:],26)
    return line, line*0.2, line*0.8

def calc_vwap(klines):
    k=klines[-20:] if len(klines)>=20 else klines
    num=sum(((x['h']+x['l']+x['c'])/3)*x['v'] for x in k)
    den=sum(x['v'] for x in k)
    return num/den if den>0 else 0

def calc_boll(closes):
    p=20
    if len(closes)<p: v=closes[-1]; return v*1.02,v,v*0.98
    sl=closes[-p:]; mid=sum(sl)/p
    std=(sum((x-mid)**2 for x in sl)/p)**0.5
    return mid+2*std,mid,mid-2*std

STABLE={'BUSDUSDT','USDCUSDT','TUSDUSDT','FDUSDUSDT','DAIUSDT','USDPUSDT','EURUSDT','GBPUSDT'}

def scan_market(state, config):
    log_msg(state,'Tarama basladi...','blue')
    state['scanning']=True; save_state(state)
    tf=config.get('tf','5m'); min_score=int(config.get('min_score',80))
    try:
        tickers=binance_get('/ticker/24hr')
        if not tickers: log_msg(state,'Binance hata','dn'); return
        try: usd_try=float(binance_get('/ticker/price',{'symbol':'USDTTRY'})['price'])
        except: usd_try=38.5
        cands=[]
        for t in tickers:
            sym=t['symbol']
            if not (sym.endswith('USDT') or sym.endswith('TRY')): continue
            if sym in STABLE: continue
            vol=float(t['quoteVolume'])
            if sym.endswith('TRY'): vol/=usd_try
            if vol<5000000 or int(t['count'])<10000: continue
            price=float(t['lastPrice'])
            if sym.endswith('TRY'): price/=usd_try
            cands.append({'symbol':sym,'price':price,'change':float(t['priceChangePercent']),'volume':vol})
        cands.sort(key=lambda x:x['volume'],reverse=True)
        log_msg(state,f'Filtre 1: {len(cands)} coin','blue')
        signals=[]
        for c in cands[:100]:
            sym=c['symbol']
            try:
                klines=get_klines(sym,tf,60)
                if len(klines)<30: continue
                closes=[k['c'] for k in klines]
                price=closes[-1]; rsi=calc_rsi(closes)
                if rsi<35: continue
                _,_,macd_h=calc_macd(closes)
                ma7=sma(closes,7); ma30=sma(closes,30)
                bu,bm,bd=calc_boll(closes); vwap=calc_vwap(klines)
                vols=[k['v'] for k in klines]; va=sma(vols[:-1],10)
                score=0
                if ma7>ma30 and macd_h>0: score+=30
                elif ma7>ma30: score+=15
                if 50<=rsi<=70: score+=15
                elif 45<rsi<50: score+=6
                if price>bm and (price-bd)/(bu-bd+0.0001)<0.85: score+=15
                if vols[-1]>va*0.8: score+=20
                if price>vwap: score+=10
                if rsi>75 or ma7<ma30: score-=10
                if score>=min_score:
                    tp=4.0 if score>=90 else 3.0 if score>=85 else 2.0 if score>=75 else 1.5
                    signals.append({'symbol':sym,'display':sym.replace('USDT','').replace('TRY',''),
                                    'price':price,'score':score,'rsi':round(rsi,1),
                                    'change':c['change'],'volume':c['volume'],'tp_pct':tp,'tf':tf,
                                    'scanned_at':int(time.time()*1000)})
            except: continue
        signals.sort(key=lambda x:x['score'],reverse=True)
        state['signals']=signals
        log_msg(state,f'Tarama tamam: {len(signals)} sinyal','up' if signals else 'gold')
        if state.get('auto_trade') and signals and not state.get('positions'):
            open_position(state,config,signals[0])
    except Exception as e: log_msg(state,f'Hata: {str(e)[:50]}','dn')
    finally: state['scanning']=False; state['last_scan']=int(time.time()); save_state(state)

def open_position(state, config, signal):
    mode=state.get('mode','demo')
    pos_usd=float(config.get('pos_usd',400)); trail_pct=float(config.get('trail_pct',1.5))
    commission=pos_usd*0.001
    if mode=='live':
        cfg=load_config()
        order=binance_signed('POST','/order',{'symbol':signal['symbol'],'side':'BUY','type':'MARKET','quoteOrderQty':round(pos_usd,2)},cfg.get('api_key',''),cfg.get('api_secret',''))
        if 'orderId' not in order: log_msg(state,f'Emir hatasi: {order.get("msg","?")}','dn'); return
        qty=float(order['executedQty']); entry=float(order['cummulativeQuoteQty'])/qty
    else:
        qty=(pos_usd-commission)/signal['price']; entry=signal['price']
    tp_pct=signal.get('tp_pct',1.5)
    pos={'id':int(time.time()*1000),'symbol':signal['symbol'],'display':signal['display'],
         'entry':entry,'live_price':entry,'qty':qty,'pos_usd':pos_usd-commission,
         'gross_usd':pos_usd,'commission':commission,'trail_pct':trail_pct,
         'trail_sl':entry*(1-trail_pct/100),'target_tp':entry*(1+tp_pct/100),
         'tp_pct':tp_pct,'score':signal['score'],'tf':signal['tf'],
         'entry_time':int(time.time()*1000),'is_live':mode=='live'}
    if mode!='live': state['demo_bal']-=pos_usd
    state['positions'].append(pos)
    state['history'].insert(0,{**pos,'result':'open','pnl':None})
    log_msg(state,f'GIRIS: {pos["display"]} @ ${entry:.4f}','up')
    save_state(state)

def close_position(state, config, pos, exit_price, reason):
    commission=exit_price*pos['qty']*0.001
    pnl=(exit_price-pos['entry'])/pos['entry']*pos['pos_usd']-commission
    mode=state.get('mode','demo')
    if pos.get('is_live') and mode=='live':
        cfg=load_config()
        binance_signed('POST','/order',{'symbol':pos['symbol'],'side':'SELL','type':'MARKET','quantity':round(pos['qty'],6)},cfg.get('api_key',''),cfg.get('api_secret',''))
    else:
        state['demo_bal']+=pos['pos_usd']+pnl; state['demo_pnl']+=pnl
    state['positions']=[p for p in state['positions'] if p['id']!=pos['id']]
    rec=next((h for h in state['history'] if h['id']==pos['id']),None)
    if rec: rec.update({'result':'win' if pnl>=0 else 'lose','pnl':round(pnl,2),'exit_price':exit_price,'exit_reason':reason,'exit_time':int(time.time()*1000)})
    log_msg(state,f'{"KAR" if pnl>=0 else "ZARAR"} ({reason}): {pos["display"]} | {pnl:+.2f}$','up' if pnl>=0 else 'dn')
    save_state(state)

def update_positions(state, config):
    if not state.get('positions'): return
    for pos in list(state['positions']):
        price=get_price(pos['symbol'])
        if not price: continue
        pos['live_price']=price
        new_sl=price*(1-pos['trail_pct']/100)
        if new_sl>pos['trail_sl']: pos['trail_sl']=new_sl
        if price<=pos['trail_sl']:
            close_position(state,config,pos,price,'SL'); return
        if price>=pos['target_tp']:
            close_position(state,config,pos,price,'TP'); return
    save_state(state)

state_lock=threading.Lock()
_state=None; _config=None

class APIHandler(BaseHTTPRequestHandler):
    def log_message(self,format,*args): pass
    def do_GET(self):
        if self.path=='/state':
            with state_lock: data=json.dumps(_state).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*')
            self.end_headers(); self.wfile.write(data)
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        global _state,_config
        length=int(self.headers.get('Content-Length',0))
        body=self.rfile.read(length)
        try: cmd=json.loads(body)
        except: self.send_response(400); self.end_headers(); return
        with state_lock:
            action=cmd.get('action')
            if action=='scan':
                threading.Thread(target=scan_market,args=(_state,_config),daemon=True).start()
            elif action=='set_auto_scan': _state['auto_scan']=cmd.get('value',False); save_state(_state)
            elif action=='set_auto_trade': _state['auto_trade']=cmd.get('value',False); save_state(_state)
            elif action=='set_mode': _state['mode']=cmd.get('value','demo'); save_state(_state)
            elif action=='set_config':
                _config.update(cmd.get('config',{}))
                with open(CONFIG_FILE,'w') as f: json.dump(_config,f)
            elif action=='open_position':
                sig=cmd.get('signal')
                if sig: threading.Thread(target=open_position,args=(_state,_config,sig),daemon=True).start()
            elif action=='close_position':
                pos_id=cmd.get('pos_id')
                pos=next((p for p in _state['positions'] if p['id']==pos_id),None)
                if pos:
                    price=get_price(pos['symbol']) or pos['live_price']
                    threading.Thread(target=close_position,args=(_state,_config,pos,price,'MANUAL'),daemon=True).start()
            elif action=='set_api_keys':
                _config['api_key']=cmd.get('api_key','')
                _config['api_secret']=cmd.get('api_secret','')
                with open(CONFIG_FILE,'w') as f: json.dump(_config,f)
                log_msg(_state,'API key kaydedildi','up'); save_state(_state)
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Access-Control-Allow-Origin','*')
        self.end_headers(); self.wfile.write(b'{"ok":true}')
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.end_headers()

def run_api():
    server=HTTPServer(('0.0.0.0',8765),APIHandler)
    print('API port 8765')
    server.serve_forever()

def main():
    global _state,_config
    print('ARNA Bot baslatildi')
    _state=load_state()
    _config=load_config() or {'tf':'5m','min_score':80,'pos_usd':400,'trail_pct':1.5}
    log_msg(_state,'ARNA Bot baslatildi','up')
    save_state(_state)
    threading.Thread(target=run_api,daemon=True).start()
    last_scan=0; last_price=0
    while True:
        try:
            now=time.time()
            _config=load_config() or _config
            if now-last_price>=5:
                with state_lock: update_positions(_state,_config)
                last_price=now
            if _state.get('auto_scan') and not _state.get('scanning'):
                if now-last_scan>=120:
                    threading.Thread(target=scan_market,args=(_state,_config),daemon=True).start()
                    last_scan=now
            time.sleep(1)
        except KeyboardInterrupt: print('Durduruldu'); break
        except Exception as e: print(f'Hata: {e}'); time.sleep(5)

if __name__=='__main__': main()
