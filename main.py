#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARNA ENGINEER v1.0 — Tam Mühendislik Sistemi
Deterministic, rule-based, AI içermez.
Port: 8767
"""

import json, os, hashlib, time, shutil, re, threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ── KLASÖR YAPISI ──
BASE_DIR     = Path('/root/arna-engineer')
MEMORY_DIR   = BASE_DIR / 'memory'
VERSIONS_DIR = MEMORY_DIR / 'versions'
REPORTS_DIR  = BASE_DIR / 'reports'
WORKSPACE    = BASE_DIR / 'workspace'

for d in [MEMORY_DIR, VERSIONS_DIR, REPORTS_DIR, WORKSPACE]:
    d.mkdir(parents=True, exist_ok=True)

PROJECT_FILE   = MEMORY_DIR / 'project.json'
CHECKSUMS_FILE = MEMORY_DIR / 'checksums.json'
DEPMAP_FILE    = MEMORY_DIR / 'dependency_map.json'

PORT = 8767

# ════════════════════════════════════════════════
# PROJE HAFIZASI
# ════════════════════════════════════════════════

def load_project():
    try:
        return json.loads(PROJECT_FILE.read_text())
    except:
        return {
            'files': {},        # {name: content}
            'meta': {},         # {name: {role, lines, uploaded, changed}}
            'created': now_str(),
            'last_updated': now_str(),
        }

def save_project(p):
    PROJECT_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=2))

def load_checksums():
    try:
        return json.loads(CHECKSUMS_FILE.read_text())
    except:
        return {}

def save_checksums(c):
    CHECKSUMS_FILE.write_text(json.dumps(c, indent=2))

def load_versions():
    versions = []
    if not VERSIONS_DIR.exists():
        return versions
    for f in sorted(VERSIONS_DIR.glob('*.json'), reverse=True):
        try:
            versions.append(json.loads(f.read_text()))
        except:
            pass
    return versions

def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def checksum(content):
    return hashlib.md5(content.encode('utf-8', errors='ignore')).hexdigest()

# ════════════════════════════════════════════════
# DOSYA ROL TESPİTİ
# ════════════════════════════════════════════════

def get_role(name):
    roles = {
        'bot.py':    'Trade motoru — tarama, alım, satım, risk',
        'brain.py':  'Otonom beyin — öğrenme, analiz, kararlar',
        'index.html':'Web arayüzü — kullanıcı paneli',
        'manifest.json': 'PWA Manifest',
        'sw.js':     'Service Worker — PWA offline',
        'icon.svg':  'İkon / Görsel',
    }
    if name in roles:
        return roles[name]
    if name.endswith('.py'):   return 'Python modülü'
    if name.endswith('.html'): return 'HTML arayüzü'
    if name.endswith('.json'): return 'JSON veri/config'
    if name.endswith('.js'):   return 'JavaScript modülü'
    return 'Sistem dosyası'

# ════════════════════════════════════════════════
# FUNCTION MAP — Fonksiyonları indeksle
# ════════════════════════════════════════════════

def extract_functions(name, content):
    funcs = []
    if name.endswith('.py'):
        for m in re.finditer(r'^def\s+(\w+)\s*\(', content, re.MULTILINE):
            funcs.append(m.group(1))
    elif name.endswith('.html') or name.endswith('.js'):
        for m in re.finditer(r'function\s+(\w+)\s*\(', content):
            funcs.append(m.group(1))
    return funcs

def extract_imports(name, content):
    imports = []
    if name.endswith('.py'):
        for m in re.finditer(r'^import\s+(\w+)|^from\s+(\w+)', content, re.MULTILINE):
            imports.append(m.group(1) or m.group(2))
    return imports

def extract_endpoints(content):
    endpoints = []
    for m in re.finditer(r"path\s*==\s*['\"]([^'\"]+)['\"]|elif.*path.*['\"]([^'\"]+)['\"]", content):
        ep = m.group(1) or m.group(2)
        if ep:
            endpoints.append(ep)
    return list(set(endpoints))

def extract_html_ids(content):
    return re.findall(r'id=["\']([^"\']+)["\']', content)

# ════════════════════════════════════════════════
# CHECKSUM ENGINE — Değişiklik tespiti
# ════════════════════════════════════════════════

def detect_changes(project, checksums):
    changes = {}
    for name, content in project['files'].items():
        cs = checksum(content)
        old_cs = checksums.get(name, '')
        if cs != old_cs:
            changes[name] = {
                'type': 'new' if not old_cs else 'modified',
                'old_checksum': old_cs,
                'new_checksum': cs,
            }
    # Silinen dosyalar
    for name in checksums:
        if name not in project['files']:
            changes[name] = {'type': 'deleted'}
    return changes

# ════════════════════════════════════════════════
# DEPENDENCY MAP — Bağımlılık haritası
# ════════════════════════════════════════════════

KNOWN_DEPS = [
    {'from': 'index.html', 'to': 'bot.py',     'why': 'GET /state · POST /api',    'port': '8765'},
    {'from': 'index.html', 'to': 'brain.py',   'why': 'GET /brain · POST /brain',  'port': '8766'},
    {'from': 'brain.py',   'to': 'bot.py',     'why': 'localhost:8765 config',      'port': '8765'},
    {'from': 'bot.py',     'to': 'brain.py',   'why': 'localhost:8766 scan raporu', 'port': '8766'},
    {'from': 'bot.py',     'to': 'state',      'why': '/root/arna_state.json',      'port': None},
    {'from': 'brain.py',   'to': 'brain_data', 'why': '/root/brain_data.json',      'port': None},
]

def build_dependency_map(project):
    files = list(project['files'].keys())
    active_deps = []
    for dep in KNOWN_DEPS:
        if any(f.startswith(dep['from'].replace('.html','').replace('.py','')) for f in files):
            active_deps.append(dep)
    return active_deps

# ════════════════════════════════════════════════
# IMPACT ANALYZER — Değişikliğin etkisi
# ════════════════════════════════════════════════

def analyze_impact(changed_files, project):
    """Değişen dosyaların diğer dosyalara etkisini analiz et"""
    impacts = []

    for name in changed_files:
        content = project['files'].get(name, '')

        # bot.py değişince
        if name == 'bot.py':
            # Brain port kontrolü
            brain_content = project['files'].get('brain.py', '')
            if brain_content:
                bot_port_m = re.search(r"HTTPServer\([^,]+,\s*(\d+)", content)
                if bot_port_m:
                    bot_port = bot_port_m.group(1)
                    if f'localhost:{bot_port}' not in brain_content:
                        impacts.append({
                            'from': 'bot.py', 'to': 'brain.py',
                            'type': 'warning',
                            'msg': f'brain.py bot portunu ({bot_port}) referans almıyor',
                            'fixable': False,
                        })

            # index.html kontrolü
            index_content = project['files'].get('index.html', '')
            if index_content:
                # Yeni action'lar var mı?
                bot_actions = set(re.findall(r"action\s*==\s*['\"](\w+)['\"]", content))
                idx_actions = set(re.findall(r"action['\"]?\s*:\s*['\"](\w+)['\"]", index_content))
                missing = bot_actions - idx_actions
                if missing:
                    impacts.append({
                        'from': 'bot.py', 'to': 'index.html',
                        'type': 'warning',
                        'msg': f'index.html eksik action: {", ".join(list(missing)[:5])}',
                        'fixable': False,
                    })

        # brain.py değişince
        if name == 'brain.py':
            bot_content = project['files'].get('bot.py', '')
            if bot_content:
                brain_port_m = re.search(r"BRAIN_PORT\s*=\s*(\d+)|HTTPServer\([^,]+,\s*(\d+)", content)
                if brain_port_m:
                    port = brain_port_m.group(1) or brain_port_m.group(2)
                    if port and port not in bot_content:
                        impacts.append({
                            'from': 'brain.py', 'to': 'bot.py',
                            'type': 'warning',
                            'msg': f'bot.py brain portunu ({port}) referans almıyor',
                            'fixable': False,
                        })

        # index.html değişince
        if name == 'index.html':
            bot_content = project['files'].get('bot.py', '')
            if bot_content:
                # index'teki action'lar bot'ta var mı?
                idx_actions = set(re.findall(r"action['\"]?\s*:\s*['\"](\w+)['\"]", content))
                bot_handlers = set(re.findall(r"action\s*==\s*['\"](\w+)['\"]", bot_content))
                missing = idx_actions - bot_handlers - {''}
                if missing:
                    impacts.append({
                        'from': 'index.html', 'to': 'bot.py',
                        'type': 'warning',
                        'msg': f'bot.py eksik handler: {", ".join(list(missing)[:5])}',
                        'fixable': False,
                    })

    return impacts

# ════════════════════════════════════════════════
# DOSYA ANALİZİ — Tek dosya hata tespiti
# ════════════════════════════════════════════════

def analyze_file(name, content):
    issues = []
    fixes  = []
    fixed  = content

    if name.endswith('.py'):
        # 1. Parantez dengesi
        opens = content.count('(')
        closes = content.count(')')
        if opens != closes:
            issues.append({'t': 'error', 'msg': f'Parantez dengesi bozuk: {opens} açık, {closes} kapalı'})

        # 2. import json eksik
        if 'json.' in content and 'import json' not in content:
            issues.append({'t': 'error', 'msg': 'import json eksik ama json. kullanılıyor'})
            fixed = 'import json\n' + fixed
            fixes.append('import json eklendi')

        # 3. import time eksik
        if 'time.' in content and 'import time' not in content:
            issues.append({'t': 'error', 'msg': 'import time eksik ama time. kullanılıyor'})
            fixed = 'import time\n' + fixed
            fixes.append('import time eklendi')

        # 4. import threading eksik
        if 'threading.' in content and 'import threading' not in content:
            issues.append({'t': 'error', 'msg': 'import threading eksik'})
            fixed = 'import threading\n' + fixed
            fixes.append('import threading eklendi')

        # 5. bot.py özel kontroller
        if name == 'bot.py':
            required = {
                'kill_switch':    'Kill switch mekanizması',
                'calc_atr':       'ATR hesaplama',
                'calc_adx':       'ADX sideways filtresi',
                'trail_sl':       'Trailing stop',
                'consecutive':    'Ardışık SL sayacı',
                'scan_market':    'Tarama fonksiyonu',
                'open_position':  'Pozisyon açma',
                'close_position': 'Pozisyon kapama',
                'update_positions': 'Pozisyon güncelleme',
            }
            for key, desc in required.items():
                if key not in content:
                    issues.append({'t': 'warning', 'msg': f'Eksik: {desc} ({key})'})

        # 6. brain.py özel kontroller
        if name == 'brain.py':
            required = {
                'save_brain':  'save_brain fonksiyonu',
                'load_brain':  'load_brain fonksiyonu',
                'analysis_loop': 'Analiz döngüsü',
                'handle_chat': 'Sohbet handler',
                'update_learning': 'Öğrenme sistemi',
            }
            for key, desc in required.items():
                if key not in content:
                    issues.append({'t': 'warning', 'msg': f'Eksik: {desc} ({key})'})

        # 7. Syntax — basit kontrol (def/class dengesi)
        def_count   = len(re.findall(r'^def\s+', content, re.MULTILINE))
        class_count = len(re.findall(r'^class\s+', content, re.MULTILINE))
        if def_count == 0 and len(content) > 1000:
            issues.append({'t': 'warning', 'msg': 'Hiç fonksiyon tanımı (def) bulunamadı'})

    if name.endswith('.html'):
        # 1. DOCTYPE
        if '<!DOCTYPE html' not in content and '<!doctype html' not in content:
            issues.append({'t': 'error', 'msg': 'DOCTYPE eksik'})
            fixed = '<!DOCTYPE html>\n' + fixed
            fixes.append('DOCTYPE eklendi')

        # 2. charset
        if 'charset' not in content:
            issues.append({'t': 'warning', 'msg': 'Charset tanımı eksik'})

        # 3. Duplicate HTML ID kontrolü
        ids = re.findall(r'id=["\']([^"\']+)["\']', content)
        seen = set()
        for id_ in ids:
            if id_ in seen:
                issues.append({'t': 'error', 'msg': f'Duplicate HTML ID: #{id_}'})
            seen.add(id_)

        # 4. Tag dengesi
        for tag in ['div', 'script', 'style', 'head', 'body', 'html']:
            op = len(re.findall(f'<{tag}[\\s>]', content, re.IGNORECASE))
            cl = len(re.findall(f'</{tag}>', content, re.IGNORECASE))
            if op != cl and op > 0:
                issues.append({'t': 'warning', 'msg': f'<{tag}> dengesi: {op} açık, {cl} kapalı'})

        # 5. index.html özel
        if name == 'index.html':
            if '/api' not in content and '/state' not in content:
                issues.append({'t': 'error', 'msg': 'Bot API bağlantısı (/api veya /state) bulunamadı'})
            if 'brain' not in content and '8766' not in content:
                issues.append({'t': 'warning', 'msg': 'Brain bağlantısı bulunamadı'})

    return {
        'name': name,
        'issues': issues,
        'fixes': fixes,
        'fixed_content': fixed if fixed != content else None,
        'auto_fixed': fixed != content,
        'has_errors': any(i['t'] == 'error' for i in issues),
        'has_warnings': any(i['t'] == 'warning' for i in issues),
        'clean': len(issues) == 0,
    }

# ════════════════════════════════════════════════
# ARCHITECTURE VALIDATOR
# ════════════════════════════════════════════════

def validate_architecture(project):
    issues = []
    files = project['files']

    # 1. snake_case kontrolü (Python dosyaları)
    for name, content in files.items():
        if name.endswith('.py'):
            # camelCase fonksiyon tespiti
            camel = re.findall(r'^def\s+([a-z]+[A-Z]\w+)\s*\(', content, re.MULTILINE)
            if camel:
                issues.append({'t': 'style', 'file': name,
                    'msg': f'camelCase fonksiyon var (snake_case kullan): {", ".join(camel[:3])}'})

    # 2. Duplicate HTML ID (tüm HTML dosyaları)
    all_ids = {}
    for name, content in files.items():
        if name.endswith('.html'):
            ids = re.findall(r'id=["\']([^"\']+)["\']', content)
            for id_ in ids:
                if id_ in all_ids:
                    issues.append({'t': 'error', 'file': name,
                        'msg': f'Duplicate HTML ID #{id_} ({all_ids[id_]} ile çakışıyor)'})
                all_ids[id_] = name

    # 3. Tek config standardı — priceScore uyumsuzluğu gibi
    if 'bot.py' in files and 'index.html' in files:
        # priceScore kontrol
        bot_ps = re.search(r'def price_score.*?return (\d+)', files['bot.py'], re.DOTALL)
        idx_ps = re.search(r'priceScore.*?return (\d+)', files['index.html'], re.DOTALL)
        if bot_ps and idx_ps:
            if bot_ps.group(1) != idx_ps.group(1):
                issues.append({'t': 'config', 'file': 'bot.py + index.html',
                    'msg': f'priceScore uyumsuzluğu: bot.py={bot_ps.group(1)}, index.html={idx_ps.group(1)}'})

    # 4. PORT tutarlılığı
    if 'bot.py' in files:
        bot_port = re.search(r"HTTPServer\([^,]+,\s*(\d+)", files['bot.py'])
        if bot_port:
            port = bot_port.group(1)
            if 'index.html' in files and port not in files['index.html']:
                issues.append({'t': 'warning', 'file': 'index.html',
                    'msg': f'bot.py port ({port}) index.html\'de bulunamadı'})

    if 'brain.py' in files:
        brain_port = re.search(r"BRAIN_PORT\s*=\s*(\d+)|HTTPServer\([^,]+,\s*(\d+)", files['brain.py'])
        if brain_port:
            port = brain_port.group(1) or brain_port.group(2)
            if port and 'index.html' in files and port not in files['index.html']:
                issues.append({'t': 'warning', 'file': 'index.html',
                    'msg': f'brain.py port ({port}) index.html\'de bulunamadı'})

    return issues

# ════════════════════════════════════════════════
# SNAPSHOT ENGINE — Versiyon yönetimi
# ════════════════════════════════════════════════

def save_snapshot(project, desc, changed_files, added_files):
    versions = load_versions()
    v_num    = len(versions) + 1
    v_id     = f"v{v_num}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    snapshot = {
        'id':         v_id,
        'version':    v_num,
        'label':      f'v{v_num}',
        'description': desc,
        'date':       now_str(),
        'ts':         int(time.time() * 1000),
        'files':      {**project['files']},
        'changed':    changed_files,
        'added':      added_files,
        'file_count': len(project['files']),
        'checksums':  {n: checksum(c) for n, c in project['files'].items()},
    }

    snap_file = VERSIONS_DIR / f'{v_id}.json'
    snap_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))

    # Rapor oluştur
    report = generate_report(snapshot)
    (REPORTS_DIR / f'{v_id}_report.txt').write_text(report)

    return snapshot

def generate_report(snapshot):
    lines = [
        f"ARNA ENGINEER — PATCH REPORT",
        f"{'='*40}",
        f"Version:  {snapshot['label']}",
        f"Date:     {snapshot['date']}",
        f"Desc:     {snapshot['description']}",
        f"",
        f"Changed Files ({len(snapshot['changed'])}):",
    ]
    for f in snapshot['changed']:
        lines.append(f"  • {f} [MODIFIED]")
    for f in snapshot['added']:
        lines.append(f"  • {f} [NEW]")
    unchanged = [f for f in snapshot['files'] if f not in snapshot['changed'] and f not in snapshot['added']]
    for f in unchanged:
        lines.append(f"  • {f} [UNCHANGED]")
    lines += ['', f"Total: {snapshot['file_count']} files", '='*40]
    return '\n'.join(lines)

# ════════════════════════════════════════════════
# ANA ANALİZ — Dosya yüklenince çalışır
# ════════════════════════════════════════════════

def full_analysis(project, changed_files):
    result = {
        'file_analyses': {},
        'impacts': [],
        'arch_issues': [],
        'dependency_map': [],
        'functions': {},
        'endpoints': {},
        'checksums_updated': {},
        'summary': {},
    }

    # 1. Her dosyayı analiz et
    for name in changed_files:
        content = project['files'].get(name, '')
        if content:
            result['file_analyses'][name] = analyze_file(name, content)
            result['functions'][name]     = extract_functions(name, content)
            if name == 'bot.py':
                result['endpoints'][name] = extract_endpoints(content)

    # 2. Cross-file impact analizi
    result['impacts'] = analyze_impact(changed_files, project)

    # 3. Mimari doğrulama
    result['arch_issues'] = validate_architecture(project)

    # 4. Dependency map
    result['dependency_map'] = build_dependency_map(project)

    # 5. Özet
    total_errors   = sum(1 for a in result['file_analyses'].values() if a['has_errors'])
    total_warnings = sum(1 for a in result['file_analyses'].values() if a['has_warnings'])
    total_impacts  = len(result['impacts'])
    total_arch     = len(result['arch_issues'])

    result['summary'] = {
        'files_analyzed': len(changed_files),
        'errors':   total_errors,
        'warnings': total_warnings,
        'impacts':  total_impacts,
        'arch_issues': total_arch,
        'clean':    total_errors == 0 and total_warnings == 0 and total_impacts == 0,
        'time':     now_str(),
    }

    return result

# ════════════════════════════════════════════════
# WEB API
# ════════════════════════════════════════════════

_lock = threading.Lock()

class EngineerAPI(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        # Arayüzü serve et
        if self.path in ('/', '/engineer', '/engineer.html'):
            try:
                html = (BASE_DIR / 'engineer.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(html)
            except:
                self.send_response(404)
                self.end_headers()
            return

        if self.path == '/api/state':
            with _lock:
                project  = load_project()
                versions = load_versions()
                self._json({
                    'files':    list(project['files'].keys()),
                    'meta':     project['meta'],
                    'versions': [{'label':v['label'],'date':v['date'],'desc':v['description'],
                                  'changed':v['changed'],'added':v['added'],'file_count':v['file_count']}
                                 for v in versions[:20]],
                    'file_count': len(project['files']),
                    'version_count': len(versions),
                    'last_updated': project.get('last_updated',''),
                })
            return

        if self.path.startswith('/api/version/'):
            idx = int(self.path.split('/')[-1])
            versions = load_versions()
            if idx < len(versions):
                self._json(versions[idx])
            else:
                self._json({'error': 'not found'}, 404)
            return

        if self.path == '/api/versions':
            versions = load_versions()
            self._json([{
                'label': v['label'], 'date': v['date'],
                'desc': v['description'], 'changed': v['changed'],
                'added': v.get('added',[]), 'file_count': v['file_count'],
                'id': v['id']
            } for v in versions])
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        try:
            cmd = json.loads(body)
        except:
            self._json({'error': 'bad json'}, 400)
            return

        action = cmd.get('action', '')

        with _lock:

            # ── DOSYA YÜKLE ──
            if action == 'upload_files':
                files   = cmd.get('files', {})   # {name: content}
                project = load_project()
                checksums = load_checksums()

                changed = []
                added   = []

                for name, content in files.items():
                    is_new   = name not in project['files']
                    is_changed = not is_new and project['files'][name] != content

                    project['files'][name] = content
                    project['meta'][name]  = {
                        'role':     get_role(name),
                        'lines':    content.count('\n') + 1,
                        'size':     len(content),
                        'uploaded': now_str(),
                        'changed':  now_str() if is_changed else project['meta'].get(name, {}).get('changed'),
                        'functions': extract_functions(name, content),
                        'imports':   extract_imports(name, content),
                    }
                    checksums[name] = checksum(content)

                    if is_new:     added.append(name)
                    elif is_changed: changed.append(name)

                project['last_updated'] = now_str()
                save_project(project)
                save_checksums(checksums)

                # Tam analiz yap
                all_changed = changed + added
                analysis = full_analysis(project, all_changed) if all_changed else {
                    'file_analyses': {}, 'impacts': [], 'arch_issues': [],
                    'dependency_map': build_dependency_map(project),
                    'functions': {}, 'endpoints': {},
                    'summary': {'files_analyzed':0,'errors':0,'warnings':0,'impacts':0,'arch_issues':0,'clean':True,'time':now_str()}
                }

                self._json({
                    'ok': True,
                    'uploaded': len(files),
                    'changed':  changed,
                    'added':    added,
                    'analysis': analysis,
                })

            # ── DOSYAYI DÜZELT (auto-fix uygula) ──
            elif action == 'apply_fix':
                name    = cmd.get('name')
                project = load_project()
                content = project['files'].get(name)
                if not content:
                    self._json({'error': 'Dosya bulunamadı'}, 404)
                    return
                result = analyze_file(name, content)
                if result['fixed_content']:
                    project['files'][name] = result['fixed_content']
                    project['meta'][name]['lines'] = result['fixed_content'].count('\n') + 1
                    project['meta'][name]['changed'] = now_str()
                    project['last_updated'] = now_str()
                    save_project(project)
                    self._json({'ok': True, 'fixed': result['fixes'], 'name': name})
                else:
                    self._json({'ok': False, 'msg': 'Otomatik düzeltme yapılamadı'})

            # ── VERSİYON KAYDET ──
            elif action == 'save_version':
                desc    = cmd.get('desc', 'Güncelleme')
                project = load_project()
                checksums = load_checksums()
                changes = detect_changes(project, checksums)

                changed = [n for n,c in changes.items() if c['type'] == 'modified']
                added   = [n for n,c in changes.items() if c['type'] == 'new']

                snapshot = save_snapshot(project, desc, changed, added)

                # Checksum'ları güncelle
                for name, content in project['files'].items():
                    checksums[name] = checksum(content)
                save_checksums(checksums)

                self._json({
                    'ok': True,
                    'version': snapshot['label'],
                    'id': snapshot['id'],
                    'changed': changed,
                    'added': added,
                    'file_count': snapshot['file_count'],
                })

            # ── VERSİYONA DÖNÜŞ ──
            elif action == 'restore_version':
                v_id    = cmd.get('version_id')
                versions = load_versions()
                ver = next((v for v in versions if v['id'] == v_id), None)
                if not ver:
                    self._json({'error': 'Versiyon bulunamadı'}, 404)
                    return

                project = load_project()
                project['files'] = {**ver['files']}
                project['meta']  = {}
                for name, content in project['files'].items():
                    project['meta'][name] = {
                        'role': get_role(name),
                        'lines': content.count('\n') + 1,
                        'size': len(content),
                        'uploaded': ver['date'],
                        'changed': None,
                    }
                project['last_updated'] = now_str()
                save_project(project)

                # Checksum'ları güncelle
                checksums = {n: checksum(c) for n, c in project['files'].items()}
                save_checksums(checksums)

                self._json({'ok': True, 'restored': ver['label'], 'file_count': len(project['files'])})

            # ── DOSYAYI İNDİR (tek) ──
            elif action == 'get_file':
                name    = cmd.get('name')
                project = load_project()
                content = project['files'].get(name)
                if content:
                    self._json({'ok': True, 'name': name, 'content': content})
                else:
                    self._json({'error': 'Dosya bulunamadı'}, 404)

            # ── ANALİZ ──
            elif action == 'analyze':
                name    = cmd.get('name')
                project = load_project()
                content = project['files'].get(name, '')
                if not content:
                    self._json({'error': 'Dosya bulunamadı'}, 404)
                    return
                result = analyze_file(name, content)
                # Impact da ekle
                impacts = analyze_impact([name], project)
                self._json({'ok': True, 'analysis': result, 'impacts': impacts})

            # ── TAM SİSTEM ANALİZİ ──
            elif action == 'full_analysis':
                project = load_project()
                all_files = list(project['files'].keys())
                result = full_analysis(project, all_files)
                self._json({'ok': True, 'analysis': result})

            # ── DOSYA SİL ──
            elif action == 'delete_file':
                name    = cmd.get('name')
                project = load_project()
                if name in project['files']:
                    del project['files'][name]
                    del project['meta'][name]
                    save_project(project)
                    self._json({'ok': True})
                else:
                    self._json({'error': 'Dosya bulunamadı'}, 404)

            else:
                self._json({'error': f'Bilinmeyen action: {action}'}, 400)

# ════════════════════════════════════════════════
# BAŞLAT
# ════════════════════════════════════════════════

def main():
    print(f'ARNA ENGINEER v1.0 — Port {PORT}')
    print(f'Workspace: {BASE_DIR}')
    print(f'Hafıza: {MEMORY_DIR}')

    server = HTTPServer(('0.0.0.0', PORT), EngineerAPI)
    print(f'Sunucu başlatıldı: http://0.0.0.0:{PORT}')
    server.serve_forever()

if __name__ == '__main__':
    main()
