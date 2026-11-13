"""Account login for the materials platform.

Two roles: ``viewer`` may browse and search; ``admin`` may also manage
accounts and replace data files. Accounts live in ``config/auth.sqlite``
(PBKDF2 password hashes, hashed session tokens); nothing secret is kept in code.
The very first admin can only be created from the computer that runs the
service (http://127.0.0.1:8886/setup) or with ``manage_users.py``.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import importlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
AUTH_DB = Path(os.environ.get("MATERIALS_AUTH_DB", ROOT / "config" / "auth.sqlite"))
COOKIE = "mp_session"
SESSION_DAYS = 14
ITERATIONS = 240_000
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]{2,32}$")
ROLES = {"viewer": "普通账号", "admin": "管理员"}

# Files an admin may replace from the web page, and where they live.
DATA_FILES = {
    "oer.sqlite": ("data/oer.sqlite", "催化材料 · 预测构型库"),
    "literature.sqlite": ("data/literature.sqlite", "催化材料 · 文献与实验数据"),
    "glass_catalog.sqlite": ("data/glass_catalog.sqlite", "玻璃 · 主目录（检索用）"),
    "glass.sqlite": ("data/glass.sqlite", "玻璃 · 性质记录"),
    "glass_archive.sqlite": ("data/glass_archive.sqlite", "玻璃 · 原始资料"),
    "catalog_summary.json": ("data/catalog_summary.json", "玻璃 · 统计摘要"),
    "molecules.json": ("frontend/dist/silane/molecules.json", "硅烷偶联剂 · 分子数据"),
}
UPLOAD_DIR = ROOT / "data" / ".uploads"
BACKUP_DIR = ROOT / "data" / "backups"
MAX_UPLOAD = 4 * 1024**3

PUBLIC_EXACT = {"/login", "/api/auth/login", "/api/auth/logout", "/api/health", "/setup",
                "/api/auth/setup", "/favicon.ico", "/__auth/bar.js", "/__auth/auth.css"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  username TEXT PRIMARY KEY COLLATE NOCASE,
  role TEXT NOT NULL CHECK(role IN ('viewer','admin')),
  salt BLOB NOT NULL, pw_hash BLOB NOT NULL,
  created REAL NOT NULL, last_login REAL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY,
  username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
  expires REAL NOT NULL);
"""


# ---------------------------------------------------------------- storage
@contextmanager
def connect():
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(AUTH_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)


def check_password_rules(password: str):
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    if len(password) > 128:
        raise ValueError("密码太长")


def create_user(username: str, password: str, role: str = "viewer"):
    username = username.strip()
    if not NAME_RE.match(username):
        raise ValueError("账号名为 2–32 位中文、字母、数字或 _ . -")
    if role not in ROLES:
        raise ValueError("角色无效")
    check_password_rules(password)
    salt = secrets.token_bytes(16)
    with connect() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("账号已存在")
        c.execute("INSERT INTO users(username,role,salt,pw_hash,created) VALUES(?,?,?,?,?)",
                  (username, role, salt, _hash(password, salt), time.time()))


def set_password(username: str, password: str):
    check_password_rules(password)
    salt = secrets.token_bytes(16)
    with connect() as c:
        n = c.execute("UPDATE users SET salt=?, pw_hash=? WHERE username=?",
                      (salt, _hash(password, salt), username)).rowcount
        if not n:
            raise KeyError("账号不存在")
        c.execute("DELETE FROM sessions WHERE username=?", (username,))


def verify(username: str, password: str):
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not row:
        _hash(password, b"0" * 16)  # keep timing similar
        return None
    if hmac.compare_digest(_hash(password, row["salt"]), row["pw_hash"]):
        return row
    return None


def user_count() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def new_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with connect() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO sessions VALUES(?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), username, time.time() + SESSION_DAYS * 86400))
        c.execute("UPDATE users SET last_login=? WHERE username=?", (time.time(), username))
    return token


def session_user(token: str | None):
    if not token or len(token) > 100:
        return None
    with connect() as c:
        return c.execute(
            "SELECT u.username, u.role FROM sessions s JOIN users u ON u.username=s.username "
            "WHERE s.token_hash=? AND s.expires>?",
            (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()


def drop_session(token: str | None):
    if token:
        with connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


# ---------------------------------------------------------------- helpers
def _headers(scope) -> dict:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def _cookie(headers: dict) -> str | None:
    raw = headers.get("cookie")
    if not raw:
        return None
    try:
        jar = SimpleCookie()
        jar.load(raw)
        return jar[COOKIE].value if COOKIE in jar else None
    except Exception:
        return None


def is_local(request: Request) -> bool:
    h = request.headers
    if any(k in h for k in ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-real-ip")):
        return False
    return (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost")


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    if request.headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    return '"https"' in request.headers.get("cf-visitor", "")


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return False
    return urlparse(origin).netloc == request.headers.get("host")


_fail_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _too_many(ip: str) -> bool:
    now = time.time()
    with _fail_lock:
        recent = [t for t in _failures.get(ip, []) if now - t < 900]
        _failures[ip] = recent
        return len(recent) >= 10


def _record_fail(ip: str):
    with _fail_lock:
        _failures.setdefault(ip, []).append(time.time())


def safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


# ---------------------------------------------------------------- middleware
BAR_TAG = b'<script src="/__auth/bar.js?v=1" defer></script>'


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "/")
        headers = _headers(scope)
        if path in PUBLIC_EXACT:
            return await self.app(scope, receive, send)
        user = session_user(_cookie(headers))
        if user is None:
            return await self._deny(scope, send, path)
        if (path.startswith("/api/admin") or path == "/admin") and user["role"] != "admin":
            body = json.dumps({"detail": "需要管理员账号"}, ensure_ascii=False).encode()
            return await _respond(send, 403, body, b"application/json; charset=utf-8")
        scope.setdefault("state", {})["user"] = {"username": user["username"], "role": user["role"]}
        if path.startswith("/api/"):
            return await self.app(scope, receive, send)
        return await self.app(scope, receive, _inject_bar(send))

    async def _deny(self, scope, send, path):
        if path.startswith("/api/"):
            body = json.dumps({"detail": "请先登录"}, ensure_ascii=False).encode()
            return await _respond(send, 401, body, b"application/json; charset=utf-8")
        target = path + (("?" + scope["query_string"].decode("latin-1")) if scope.get("query_string") else "")
        location = "/login?next=" + quote(safe_next(target), safe="")
        await send({"type": "http.response.start", "status": 302,
                    "headers": [(b"location", location.encode("latin-1")), (b"cache-control", b"no-store"),
                                (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})


async def _respond(send, status, body, ctype):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


def _inject_bar(send):
    state = {"html": False, "start": None, "chunks": []}

    async def wrapped(message):
        if message["type"] == "http.response.start":
            ctype = dict(message.get("headers", [])).get(b"content-type", b"")
            if ctype.startswith(b"text/html") and message.get("status", 200) == 200:
                state["html"], state["start"] = True, message
                return
            return await send(message)
        if message["type"] == "http.response.body" and state["html"]:
            state["chunks"].append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(state["chunks"])
            if b"/__auth/bar.js" not in body:
                idx = body.lower().rfind(b"</body>")
                body = body[:idx] + BAR_TAG + body[idx:] if idx >= 0 else body + BAR_TAG
            start = state["start"]
            hdrs = [(k, v) for k, v in start.get("headers", []) if k.lower() not in (b"content-length", b"etag", b"last-modified")]
            hdrs += [(b"content-length", str(len(body)).encode()), (b"cache-control", b"no-cache")]
            await send({**start, "headers": hdrs})
            return await send({"type": "http.response.body", "body": body})
        return await send(message)

    return wrapped


# ---------------------------------------------------------------- pages
CSS = """
:root{--ink:#173647;--teal:#087c80;--bg:#f3f6f8;--nav:#102b3a;--line:#d8e2e8;--muted:#5e7380;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.auth-card{width:100%;max-width:400px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:36px 32px;box-shadow:0 10px 30px rgba(16,43,58,.08)}
.auth-card h1{margin:0 0 4px;font-size:28px}.auth-card .sub{margin:0 0 24px;color:var(--muted);font-size:15px}
label{display:block;margin:14px 0 6px;font-weight:600;font-size:15px}
input,select{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:8px;font-size:16px;background:#fff;color:var(--ink)}
input:focus,select:focus{outline:2px solid rgba(8,124,128,.25);border-color:var(--teal)}
button{cursor:pointer;border:0;border-radius:8px;background:var(--teal);color:#fff;font-size:16px;padding:11px 18px;font-weight:600}
button.secondary{background:#e7eef2;color:var(--ink)}button.danger{background:#fdecea;color:var(--bad)}
.auth-card button[type=submit]{width:100%;margin-top:22px}
.msg{min-height:22px;margin-top:12px;font-size:14px;color:var(--bad)}.msg.ok{color:var(--teal)}
.page{max-width:1080px;margin:0 auto;padding:28px 24px 60px}
.top{display:flex;align-items:center;justify-content:space-between;background:var(--nav);color:#fff;padding:14px 24px}
.top a{color:#fff;text-decoration:none;font-weight:600}.top h1{margin:0;font-size:20px}
.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-top:22px}
.panel h2{margin:0 0 14px;font-size:22px}
table{width:100%;border-collapse:collapse;font-size:15px}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600}td button{padding:6px 10px;font-size:14px;margin-right:6px}
.row{display:grid;grid-template-columns:1fr 1fr 160px auto;gap:12px;align-items:end}.row label{margin-top:0}
.muted{color:var(--muted);font-size:14px}progress{width:100%;height:10px}
@media(max-width:720px){.row{grid-template-columns:1fr}}
"""


def _page(title: str, body: str, script: str = "") -> HTMLResponse:
    doc = (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" '
           f'content="width=device-width,initial-scale=1"><title>{html.escape(title)} · 材料数据库平台</title>'
           f'<link rel="stylesheet" href="/__auth/auth.css?v=1"></head><body>{body}'
           f'<script>{script}</script></body></html>')
    return HTMLResponse(doc, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


JS_COMMON = """
const $=s=>document.querySelector(s);
async function api(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});
let d={};try{d=await r.json()}catch(e){}if(r.status===401){location.href='/login?next='+encodeURIComponent(location.pathname);}
if(!r.ok)throw new Error(d.detail||('请求失败 '+r.status));return d}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
"""

LOGIN_BODY = """<div class="auth-wrap"><form class="auth-card" id="f"><h1>材料数据库平台</h1><p class="sub">请登录后查看</p>
<label for="u">账号</label><input id="u" autocomplete="username" required autofocus>
<label for="p">密码</label><input id="p" type="password" autocomplete="current-password" required>
<button type="submit">登录</button><div class="msg" id="m"></div></form></div>"""
LOGIN_JS = JS_COMMON + """
$('#f').onsubmit=async e=>{e.preventDefault();$('#m').textContent='';
try{await api('/api/auth/login',{method:'POST',body:JSON.stringify({username:$('#u').value,password:$('#p').value})});
const n=new URLSearchParams(location.search).get('next')||'/';location.href=(n.startsWith('/')&&!n.startsWith('//'))?n:'/';}
catch(err){$('#m').textContent=err.message}};"""

SETUP_BODY = """<div class="auth-wrap"><form class="auth-card" id="f"><h1>创建管理员账号</h1><p class="sub">首次使用：只能在运行服务的这台电脑上创建</p>
<label for="u">管理员账号</label><input id="u" autocomplete="username" required autofocus>
<label for="p">密码（至少 8 位）</label><input id="p" type="password" autocomplete="new-password" required>
<label for="p2">再输一次密码</label><input id="p2" type="password" autocomplete="new-password" required>
<button type="submit">创建并登录</button><div class="msg" id="m"></div></form></div>"""
SETUP_JS = JS_COMMON + """
$('#f').onsubmit=async e=>{e.preventDefault();if($('#p').value!==$('#p2').value){$('#m').textContent='两次密码不一致';return}
try{await api('/api/auth/setup',{method:'POST',body:JSON.stringify({username:$('#u').value,password:$('#p').value})});location.href='/admin'}
catch(err){$('#m').textContent=err.message}};"""

ACCOUNT_BODY = """<div class="auth-wrap"><form class="auth-card" id="f"><h1>修改密码</h1><p class="sub" id="who"></p>
<label for="o">当前密码</label><input id="o" type="password" autocomplete="current-password" required>
<label for="p">新密码（至少 8 位）</label><input id="p" type="password" autocomplete="new-password" required>
<label for="p2">再输一次新密码</label><input id="p2" type="password" autocomplete="new-password" required>
<button type="submit">保存新密码</button><div class="msg" id="m"></div><p class="muted" style="text-align:center"><a href="/">返回平台</a></p></form></div>"""
ACCOUNT_JS = JS_COMMON + """
api('/api/auth/me').then(d=>$('#who').textContent='当前账号：'+d.username);
$('#f').onsubmit=async e=>{e.preventDefault();$('#m').className='msg';if($('#p').value!==$('#p2').value){$('#m').textContent='两次密码不一致';return}
try{await api('/api/auth/password',{method:'POST',body:JSON.stringify({old_password:$('#o').value,new_password:$('#p').value})});
$('#m').className='msg ok';$('#m').textContent='已修改，请用新密码重新登录';setTimeout(()=>location.href='/login',1200)}catch(err){$('#m').textContent=err.message}};"""

ADMIN_BODY = """<div class="top"><h1>材料数据库平台 · 管理</h1><a href="/">返回平台 →</a></div><div class="page">
<section class="panel"><h2>账号管理</h2>
<form id="add" class="row"><div><label for="nu">账号名</label><input id="nu" required></div>
<div><label for="np">初始密码（至少 8 位）</label><input id="np" type="password" autocomplete="new-password" required></div>
<div><label for="nr">类型</label><select id="nr"><option value="viewer">普通账号（只看）</option><option value="admin">管理员</option></select></div>
<div><button type="submit">添加账号</button></div></form><div class="msg" id="am"></div>
<table><thead><tr><th>账号</th><th>类型</th><th>上次登录</th><th></th></tr></thead><tbody id="users"></tbody></table></section>
<section class="panel"><h2>更新数据</h2><p class="muted">选择要替换的数据文件并上传。新文件会先检查格式和表结构，通过后才替换；旧文件自动保留一份备份。大文件会分段上传，请保持页面打开。</p>
<div class="row" style="grid-template-columns:1fr 1fr auto"><div><label for="tg">替换哪个文件</label><select id="tg"></select></div>
<div><label for="fl">选择新文件</label><input id="fl" type="file"></div><div><button id="up">上传并替换</button></div></div>
<progress id="pg" value="0" max="100" hidden></progress><div class="msg" id="um"></div>
<table><thead><tr><th>数据</th><th>文件</th><th>大小</th><th>更新时间</th></tr></thead><tbody id="files"></tbody></table></section></div>"""
ADMIN_JS = JS_COMMON + r"""
const fmt=t=>t?new Date(t*1000).toLocaleString('zh-CN',{hour12:false}):'—';
const size=b=>b>1073741824?(b/1073741824).toFixed(2)+' GB':b>1048576?(b/1048576).toFixed(1)+' MB':(b/1024).toFixed(0)+' KB';
let me='';
async function loadUsers(){const d=await api('/api/admin/users');me=d.me;
$('#users').innerHTML=d.users.map(u=>`<tr><td>${esc(u.username)}${u.username===me?'（我）':''}</td><td>${u.role==='admin'?'管理员':'普通账号'}</td><td>${fmt(u.last_login)}</td>
<td><button class="secondary" data-act="pw" data-u="${esc(u.username)}">重设密码</button><button class="secondary" data-act="role" data-u="${esc(u.username)}" data-r="${u.role}">${u.role==='admin'?'改为普通':'设为管理员'}</button>${u.username===me?'':`<button class="danger" data-act="del" data-u="${esc(u.username)}">删除</button>`}</td></tr>`).join('')}
$('#users').onclick=async e=>{const b=e.target.closest('button');if(!b)return;const u=b.dataset.u;$('#am').className='msg';$('#am').textContent='';
try{if(b.dataset.act==='pw'){const p=prompt('为 '+u+' 设置新密码（至少 8 位）');if(!p)return;await api('/api/admin/users/'+encodeURIComponent(u),{method:'PATCH',body:JSON.stringify({password:p})})}
if(b.dataset.act==='role'){await api('/api/admin/users/'+encodeURIComponent(u),{method:'PATCH',body:JSON.stringify({role:b.dataset.r==='admin'?'viewer':'admin'})})}
if(b.dataset.act==='del'){if(!confirm('确定删除账号 '+u+'？'))return;await api('/api/admin/users/'+encodeURIComponent(u),{method:'DELETE'})}
$('#am').className='msg ok';$('#am').textContent='已保存';loadUsers()}catch(err){$('#am').textContent=err.message}};
$('#add').onsubmit=async e=>{e.preventDefault();$('#am').className='msg';try{await api('/api/admin/users',{method:'POST',body:JSON.stringify({username:$('#nu').value,password:$('#np').value,role:$('#nr').value})});
$('#am').className='msg ok';$('#am').textContent='已添加';$('#add').reset();loadUsers()}catch(err){$('#am').textContent=err.message}};
async function loadFiles(){const d=await api('/api/admin/datafiles');
$('#tg').innerHTML=d.files.map(f=>`<option value="${esc(f.key)}">${esc(f.label)}（${esc(f.key)}）</option>`).join('');
$('#files').innerHTML=d.files.map(f=>`<tr><td>${esc(f.label)}</td><td>${esc(f.key)}</td><td>${f.exists?size(f.size):'不存在'}</td><td>${fmt(f.mtime)}</td></tr>`).join('')}
$('#up').onclick=async()=>{const file=$('#fl').files[0],key=$('#tg').value;$('#um').className='msg';$('#um').textContent='';
if(!file){$('#um').textContent='请先选择文件';return}if(!confirm(`用 ${file.name} 替换「${$('#tg').selectedOptions[0].textContent}」？`))return;
$('#up').disabled=true;$('#pg').hidden=false;$('#pg').value=0;
try{const s=await api('/api/admin/uploads',{method:'POST',body:JSON.stringify({target:key,size:file.size})});const CH=32*1024*1024;
for(let off=0;off<file.size||off===0;off+=CH){const part=file.slice(off,off+CH);let ok=false,last;
for(let t=0;t<4&&!ok;t++){try{const r=await fetch(`/api/admin/uploads/${s.id}/chunk?offset=${off}`,{method:'PUT',credentials:'same-origin',body:part});
if(r.ok){ok=true}else{last=(await r.json().catch(()=>({}))).detail||r.status}}catch(err){last=err.message}}
if(!ok)throw new Error('上传中断：'+last);$('#pg').value=Math.min(100,Math.round((off+part.size)/Math.max(file.size,1)*100));$('#um').className='msg ok';$('#um').textContent='正在上传 '+$('#pg').value+'%';if(file.size===0)break}
$('#um').textContent='上传完成，正在检查并替换…';await api(`/api/admin/uploads/${s.id}/finish`,{method:'POST'});let r;
while(true){await new Promise(z=>setTimeout(z,2000));r=await api(`/api/admin/uploads/${s.id}/status`);if(r.done)break}
if(!r.ok)throw new Error(r.message);$('#um').className='msg ok';$('#um').textContent=r.message;loadFiles()}catch(err){$('#um').className='msg';$('#um').textContent=err.message}
finally{$('#up').disabled=false}};
loadUsers();loadFiles();"""

BAR_JS = r"""(function(){if(window.__mpAuthBar)return;window.__mpAuthBar=1;
const of=window.fetch;window.fetch=async function(){const r=await of.apply(this,arguments);if(r.status===401){location.href='/login?next='+encodeURIComponent(location.pathname+location.search+location.hash)}return r};
of('/api/auth/me',{credentials:'same-origin'}).then(r=>r.ok?r.json():null).then(me=>{if(!me)return;
const b=document.createElement('div');b.id='mp-auth-bar';
b.style.cssText='position:fixed;right:16px;bottom:16px;z-index:9999;display:flex;gap:12px;align-items:center;background:#102b3a;color:#fff;border-radius:999px;padding:8px 16px;font:14px/1.4 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;box-shadow:0 6px 18px rgba(16,43,58,.25)';
const t=s=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const a='color:#9fe3e2;text-decoration:none';
b.innerHTML=`<span>${t(me.username)}</span>${me.role==='admin'?`<a style="${a}" href="/admin">管理</a>`:''}<a style="${a}" href="/account">改密码</a><a style="${a}" href="#" id="mp-logout">退出</a>`;
document.body.appendChild(b);document.getElementById('mp-logout').onclick=async e=>{e.preventDefault();await of('/api/auth/logout',{method:'POST',credentials:'same-origin'});location.href='/login'}})})();"""


# ---------------------------------------------------------------- models
class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NewUser(Login):
    role: str = "viewer"


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserPatch(BaseModel):
    password: str | None = Field(default=None, max_length=256)
    role: str | None = None


class UploadStart(BaseModel):
    target: str
    size: int = Field(ge=0)


# ---------------------------------------------------------------- data swap
_upload_lock = threading.Lock()
_uploads: dict[str, dict] = {}


def _sqlite_tables(path: Path) -> dict[str, set[str]]:
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        out = {}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"):
            out[name] = {r[1] for r in con.execute(f'PRAGMA table_info("{name}")')}
        return out
    finally:
        con.close()


def validate_upload(key: str, new: Path, current: Path) -> str:
    if key.endswith(".sqlite"):
        with new.open("rb") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                raise ValueError("这不是 SQLite 数据库文件")
        con = sqlite3.connect(new.as_uri() + "?mode=ro", uri=True)
        try:
            res = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
        if res != "ok":
            raise ValueError("数据库文件已损坏：" + str(res)[:200])
        if current.exists():
            old, fresh = _sqlite_tables(current), _sqlite_tables(new)
            missing = [t for t in old if t not in fresh]
            if missing:
                raise ValueError("新文件缺少原有的表：" + "、".join(missing[:8]) + "。表结构不一致，网页会出错，已取消替换。")
            cols = [f"{t}.{c}" for t in old for c in old[t] - fresh.get(t, set())]
            if cols:
                raise ValueError("新文件缺少原有的字段：" + "、".join(cols[:8]) + "。已取消替换。")
        return "数据库检查通过"
    try:
        data = json.loads(new.read_text("utf-8-sig"))
    except Exception as exc:
        raise ValueError("JSON 文件无法解析：" + str(exc)[:160])
    if current.exists():
        old = json.loads(current.read_text("utf-8-sig"))
        if type(old) is not type(data):
            raise ValueError("新 JSON 的整体结构与原文件不同，已取消替换")
        if isinstance(old, dict) and not set(old) <= set(data):
            raise ValueError("新 JSON 缺少原有字段：" + "、".join(sorted(set(old) - set(data))[:8]))
        if isinstance(old, list) and old and data and isinstance(old[0], dict) and isinstance(data[0], dict):
            lost = set(old[0]) - set(data[0])
            if lost:
                raise ValueError("新 JSON 的条目缺少原有字段：" + "、".join(sorted(lost)[:8]))
    return "JSON 检查通过"


def _replace_with_retry(src: Path, dst: Path):
    for attempt in range(40):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(0.25)
    os.replace(src, dst)


def clear_caches():
    for name, mod in list(sys.modules.items()):
        if not name.startswith("backend.") or mod is None:
            continue
        for obj in list(vars(mod).values()):
            fn = getattr(obj, "cache_clear", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass


# ---------------------------------------------------------------- routes
def install(app):
    init_db()

    def current(request: Request) -> dict:
        user = request.scope.get("state", {}).get("user")
        if not user:
            raise HTTPException(401, "请先登录")
        return user

    def require_same_origin(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "请在平台页面中操作")

    def set_cookie(resp: Response, request: Request, token: str):
        resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=is_https(request), path="/")

    @app.get("/__auth/auth.css")
    def auth_css():
        return Response(CSS, media_type="text/css; charset=utf-8", headers={"Cache-Control": "max-age=300"})

    @app.get("/__auth/bar.js")
    def auth_bar():
        return Response(BAR_JS, media_type="application/javascript; charset=utf-8", headers={"Cache-Control": "max-age=300"})

    @app.get("/login")
    def login_page(request: Request):
        if user_count() == 0:
            msg = ('<div class="auth-wrap"><div class="auth-card"><h1>尚未设置管理员</h1><p class="sub">'
                   '请在运行服务的电脑上打开 http://127.0.0.1:8886/setup 创建第一个管理员账号。</p></div></div>')
            return _page("登录", msg)
        return _page("登录", LOGIN_BODY, LOGIN_JS)

    @app.post("/api/auth/login")
    def login(body: Login, request: Request):
        require_same_origin(request)
        ip = client_ip(request)
        if _too_many(ip):
            raise HTTPException(429, "尝试次数过多，请 15 分钟后再试")
        row = verify(body.username, body.password)
        if not row:
            _record_fail(ip)
            raise HTTPException(401, "账号或密码不正确")
        resp = JSONResponse({"username": row["username"], "role": row["role"]})
        set_cookie(resp, request, new_session(row["username"]))
        return resp

    @app.post("/api/auth/logout")
    def logout(request: Request):
        drop_session(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/auth/me")
    def me(request: Request):
        return current(request)

    @app.get("/setup")
    def setup_page(request: Request):
        if user_count() > 0:
            return _page("设置", '<div class="auth-wrap"><div class="auth-card"><h1>已设置管理员</h1>'
                                 '<p class="sub"><a href="/login">去登录</a></p></div></div>')
        if not is_local(request):
            return _page("设置", '<div class="auth-wrap"><div class="auth-card"><h1>请在本机设置</h1><p class="sub">'
                                 '第一个管理员只能在运行服务的电脑上，通过 http://127.0.0.1:8886/setup 创建。</p></div></div>')
        return _page("创建管理员", SETUP_BODY, SETUP_JS)

    @app.post("/api/auth/setup")
    def setup(body: Login, request: Request):
        require_same_origin(request)
        if not is_local(request):
            raise HTTPException(403, "只能在运行服务的电脑上创建第一个管理员")
        with _upload_lock:
            if user_count() > 0:
                raise HTTPException(409, "已经有管理员了")
            create_user(body.username, body.password, "admin")
        resp = JSONResponse({"ok": True})
        set_cookie(resp, request, new_session(body.username.strip()))
        return resp

    @app.get("/account")
    def account_page():
        return _page("修改密码", ACCOUNT_BODY, ACCOUNT_JS)

    @app.post("/api/auth/password")
    def change_password(body: PasswordChange, request: Request):
        require_same_origin(request)
        user = current(request)
        if not verify(user["username"], body.old_password):
            raise HTTPException(400, "当前密码不正确")
        set_password(user["username"], body.new_password)
        return {"ok": True}

    @app.get("/admin")
    def admin_page():
        return _page("管理", ADMIN_BODY, ADMIN_JS)

    @app.get("/api/admin/users")
    def list_users(request: Request):
        with connect() as c:
            rows = c.execute("SELECT username, role, created, last_login FROM users ORDER BY role DESC, username").fetchall()
        return {"me": current(request)["username"], "users": [dict(r) for r in rows]}

    @app.post("/api/admin/users")
    def add_user(body: NewUser, request: Request):
        require_same_origin(request)
        create_user(body.username, body.password, body.role)
        return {"ok": True}

    def _admins(c) -> int:
        return c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]

    @app.patch("/api/admin/users/{username}")
    def patch_user(username: str, body: UserPatch, request: Request):
        require_same_origin(request)
        if body.role is not None:
            if body.role not in ROLES:
                raise ValueError("角色无效")
            with connect() as c:
                row = c.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
                if not row:
                    raise KeyError("账号不存在")
                if row["role"] == "admin" and body.role != "admin" and _admins(c) <= 1:
                    raise ValueError("至少要保留一个管理员")
                c.execute("UPDATE users SET role=? WHERE username=?", (body.role, username))
        if body.password:
            set_password(username, body.password)
        return {"ok": True}

    @app.delete("/api/admin/users/{username}")
    def delete_user(username: str, request: Request):
        require_same_origin(request)
        if username.lower() == current(request)["username"].lower():
            raise ValueError("不能删除自己正在使用的账号")
        with connect() as c:
            row = c.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if not row:
                raise KeyError("账号不存在")
            if row["role"] == "admin" and _admins(c) <= 1:
                raise ValueError("至少要保留一个管理员")
            c.execute("DELETE FROM sessions WHERE username=?", (username,))
            c.execute("DELETE FROM users WHERE username=?", (username,))
        return {"ok": True}

    @app.get("/api/admin/datafiles")
    def datafiles():
        out = []
        for key, (rel, label) in DATA_FILES.items():
            p = ROOT / rel
            st = p.stat() if p.exists() else None
            out.append({"key": key, "label": label, "exists": bool(st),
                        "size": st.st_size if st else 0, "mtime": st.st_mtime if st else None})
        return {"files": out}

    @app.post("/api/admin/uploads")
    def start_upload(body: UploadStart, request: Request):
        require_same_origin(request)
        if body.target not in DATA_FILES:
            raise ValueError("不支持替换这个文件")
        if body.size > MAX_UPLOAD:
            raise ValueError("文件超过 4 GB")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        uid = secrets.token_hex(12)
        tmp = UPLOAD_DIR / f"{uid}.part"
        tmp.write_bytes(b"")
        with _upload_lock:
            for old_id, info in list(_uploads.items()):
                if time.time() - info["touched"] > 6 * 3600:
                    Path(info["tmp"]).unlink(missing_ok=True)
                    _uploads.pop(old_id, None)
            _uploads[uid] = {"target": body.target, "size": body.size, "tmp": str(tmp),
                             "received": 0, "touched": time.time(), "user": current(request)["username"]}
        return {"id": uid}

    @app.put("/api/admin/uploads/{uid}/chunk")
    async def upload_chunk(uid: str, offset: int, request: Request):
        info = _uploads.get(uid)
        if not info:
            raise HTTPException(404, "上传已失效，请重新开始")
        tmp = Path(info["tmp"])
        if offset != info["received"]:
            if offset < info["received"]:  # retried chunk that already arrived
                with tmp.open("r+b") as fh:
                    fh.truncate(offset)
                info["received"] = offset
            else:
                raise HTTPException(409, "分段顺序错误")
        written = 0
        with tmp.open("ab") as fh:
            async for part in request.stream():
                written += len(part)
                if written > 64 * 1024**2 or info["received"] + written > info["size"]:
                    raise HTTPException(400, "分段大小异常")
                fh.write(part)
        info["received"] += written
        info["touched"] = time.time()
        return {"received": info["received"]}

    _results: dict[str, dict] = {}

    @app.post("/api/admin/uploads/{uid}/finish")
    def finish_upload(uid: str, request: Request):
        require_same_origin(request)
        info = _uploads.pop(uid, None)
        if not info:
            raise HTTPException(404, "上传已失效，请重新开始")
        _results[uid] = {"done": False, "message": "正在检查并替换…"}

        def work():
            try:
                _results[uid] = {"done": True, "ok": True, "message": _finish(info)}
            except Exception as exc:
                _results[uid] = {"done": True, "ok": False, "message": str(exc.args[0] if exc.args else exc)}
        threading.Thread(target=work, daemon=True).start()
        return {"id": uid}

    @app.get("/api/admin/uploads/{uid}/status")
    def upload_status(uid: str):
        if uid not in _results:
            raise HTTPException(404, "没有这个上传任务")
        return _results[uid]

    def _finish(info):
        tmp = Path(info["tmp"])
        try:
            if info["received"] != info["size"] or tmp.stat().st_size != info["size"]:
                raise ValueError("文件没有完整上传，请重试")
            rel, label = DATA_FILES[info["target"]]
            target = ROOT / rel
            note = validate_upload(info["target"], tmp, target)
            with _upload_lock:
                if target.exists():
                    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                    backup = BACKUP_DIR / (info["target"] + ".previous")
                    backup.unlink(missing_ok=True)
                    _replace_with_retry(target, backup)
                _replace_with_retry(tmp, target)
            clear_caches()
            with (ROOT / "logs" / "data-updates.log").open("a", encoding="utf-8") as log:
                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{info['user']}\t{info['target']}\t{info['size']}\n")
            return f"{note}，「{label}」已更新。别人刷新页面就能看到新数据；旧文件已备份为 data/backups/{info['target']}.previous"
        finally:
            tmp.unlink(missing_ok=True)

    app.add_middleware(AuthMiddleware)
