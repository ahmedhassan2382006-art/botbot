# -*- coding: utf-8 -*-
import os
import re
import json
import uuid
import base64
import logging
import shutil
import asyncio
import subprocess
import tempfile
import sqlite3
from datetime import datetime, timedelta
import aiohttp
from gtts import gTTS
import edge_tts
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters
)

# ============ مكتبات اختيارية ============
try:
    from langdetect import detect_langs
    HAS_LANGDETECT = True
except ImportError:
    HAS_LANGDETECT = False

try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    from telegram.constants import ChatAction
except ImportError:
    ChatAction = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ إعدادات البيئة والتوكنات ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")
ADM_TOKEN = os.environ.get("ADM", "").strip()  # توكن جيت هب المخزن في Secrets
BACKUP_REPO_OWNER = "ahmedhassan2382006-art"
BACKUP_REPO_NAME = "data"

CHANNELS_TO_CHECK = [
    {"name": "قناة الشروحات التقنية 💡", "id": "@Basira_Tech", "link": "https://t.me/Basira_Tech"},
    {"name": "قناة مشاريع المطور 👨‍💻", "id": "@AhmedHassanProjects", "link": "https://t.me/AhmedHassanProjects"},
]

GROUP_CHAT_ID = -1003997349654
GROUP_INVITE_LINK = "https://t.me/+6e6e1QVB4z8xMGE0"

DB_FILE = "bot_database.db"
USER_ASSETS_DIR = "user_audio_assets"
os.makedirs(USER_ASSETS_DIR, exist_ok=True)

FILE_NEURAL_JSON = "neural_voices.json"
FILE_FAST_JSON = "fast_voices.json"
FILE_CREATIVE_JSON = "creative_voices.json"

MAX_TEXT_LEN = 30000
MAX_PARALLEL_RENDERS = 2
GLOBAL_TTS_SEMAPHORE = None
GLOBAL_TASK_QUEUE = None
GLOBAL_HTTP_SESSION = None

(
    STEP_ENGINE, STEP_LANG, STEP_DIALECT, STEP_VOICE,
    STEP_STORY_MENU, STEP_MULTILANG_MENU, STEP_SPEED,
    STEP_FORMAT, STEP_TEXT, STEP_VOICE_CUSTOMIZE,
    STEP_WAIT_FX_AUDIO
) = range(11)

DEFAULT_CONFIG = {
    "mode": "single",
    "engine": "neural",
    "voice": "ar-EG-SalmaNeural",
    "voice_name": "Salma (👩)",
    "audio_format": "mp3",
    "speed": 1.0,
    "pitch": "normal",
    "volume": 1.0,
    "story_slots": [],
    "multilang_slots": [],
    "user_fx": {}
}

# ============ نظام النسخ الاحتياطي لمستودع data عبر توكن ADM ============
async def github_api_request(method: str, endpoint: str, payload: dict = None, accept_header: str = None):
    if not ADM_TOKEN:
        return None
    url = f"https://api.github.com/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {ADM_TOKEN}",
        "Accept": accept_header or "application/vnd.github+json",
        "User-Agent": "Telegram-TTS-Bot-Backup",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request(method, url, headers=headers, json=payload) as resp:
                if resp.status in [200, 201]:
                    if accept_header == "application/vnd.github.raw+json":
                        return await resp.read()
                    return await resp.json()
                elif resp.status == 404:
                    return {"status": 404}
                else:
                    err_txt = await resp.text()
                    logger.warning(f"GitHub API [{method} {endpoint}] Error ({resp.status}): {err_txt}")
                    return None
        except Exception as e:
            logger.error(f"GitHub connection error: {e}")
            return None

async def github_get_authenticated_user() -> str:
    res = await github_api_request("GET", "user")
    if res and isinstance(res, dict) and "login" in res:
        return res["login"]
    return BACKUP_REPO_OWNER

async def github_ensure_repo_exists(owner: str):
    res = await github_api_request("GET", f"repos/{owner}/{BACKUP_REPO_NAME}")
    if res and isinstance(res, dict) and res.get("status") == 404:
        logger.info(f"📁 المستودع {BACKUP_REPO_NAME} غير موجود، جاري إنشاؤه تلقائياً...")
        await github_api_request("POST", "user/repos", {
            "name": BACKUP_REPO_NAME,
            "private": True,
            "description": "Auto backup storage for Telegram TTS Bot"
        })

async def github_restore_latest_db():
    if not ADM_TOKEN:
        logger.warning("⚠️ لم يتم تعيين توكن ADM، سيعمل البوت بالتخزين المحلي المؤقت.")
        return

    owner = await github_get_authenticated_user()
    await github_ensure_repo_exists(owner)
    contents = await github_api_request("GET", f"repos/{owner}/{BACKUP_REPO_NAME}/contents/")
    
    if isinstance(contents, list):
        db_files = [f for f in contents if f.get("name", "").startswith("backup_") and f.get("name", "").endswith(".db")]
        if db_files:
            db_files.sort(key=lambda x: x["name"])
            latest = db_files[-1]
            file_name = latest["name"]
            logger.info(f"📥 جاري استرجاع أحدث ملف بيانات: {file_name}...")
            
            # جلب المحتوى مع ترويسة التوثيق للمستودع الخاص
            raw_data = await github_api_request(
                "GET", 
                f"repos/{owner}/{BACKUP_REPO_NAME}/contents/{file_name}",
                accept_header="application/vnd.github.raw+json"
            )
            if raw_data and isinstance(raw_data, (bytes, bytearray)):
                with open(DB_FILE, "wb") as f:
                    f.write(raw_data)
                logger.info(f"✅ تم بنجاح استرجاع وتثبيت أحدث نسخة من البيانات ({file_name})!")
                return
            else:
                # محاولة بديلة عبر فك Base64
                file_info = await github_api_request("GET", f"repos/{owner}/{BACKUP_REPO_NAME}/contents/{file_name}")
                if file_info and isinstance(file_info, dict) and "content" in file_info:
                    decoded = base64.b64decode(file_info["content"])
                    with open(DB_FILE, "wb") as f:
                        f.write(decoded)
                    logger.info(f"✅ تم بنجاح استرجاع البيانات وفك تشفيرها ({file_name})!")
                    return
    logger.info("ℹ️ لم يتم العثور على نسخ سابقة، سيتم إنشاء قاعدة بيانات جديدة والبدء من الصفر.")

async def github_upload_backup():
    if not ADM_TOKEN or not os.path.exists(DB_FILE):
        return

    owner = await github_get_authenticated_user()
    await github_ensure_repo_exists(owner)
    
    # تنظيف الخاملين قبل رفع النسخة
    db_prune_inactive(days=10)

    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"backup_{timestamp}.db"
    
    try:
        with open(DB_FILE, "rb") as f:
            file_bytes = f.read()
        b64_content = base64.b64encode(file_bytes).decode("utf-8")

        payload = {
            "message": f"Auto-backup at {timestamp} [skip ci]",
            "content": b64_content
        }
        
        res = await github_api_request("PUT", f"repos/{owner}/{BACKUP_REPO_NAME}/contents/{file_name}", payload)
        if res and isinstance(res, dict) and "content" in res:
            logger.info(f"💾 تم رفع نسخة احتياطية جديدة بنجاح إلى مستودع {BACKUP_REPO_NAME}: {file_name}")
        else:
            logger.warning(f"⚠️ لم تتم عملية رفع النسخة الاحتياطية بنجاح إلى مستودع {BACKUP_REPO_NAME}")
    except Exception as e:
        logger.error(f"خطأ أثناء قراءة أو رفع النسخة الاحتياطية: {e}")

# مهمة النسخ الاحتياطي التلقائي الدوري كل 7 دقائق
async def periodic_backup_task():
    # انتظار دقيقة واحدة بعد الإقلاع للتأكد من حفظ الإعدادات ورفع نسخة فورية
    await asyncio.sleep(60)
    try:
        await github_upload_backup()
    except Exception as e:
        logger.error(f"خطأ أثناء مهمة النسخ الاحتياطي الأولى: {e}")

    while True:
        await asyncio.sleep(420)  # 7 دقائق = 420 ثانية
        try:
            await github_upload_backup()
        except Exception as e:
            logger.error(f"خطأ أثناء مهمة النسخ الاحتياطي الدوري: {e}")

# ============ إدارة قاعدة بيانات SQLite ============
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            config_json TEXT,
            last_active TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def db_touch_user(user_id: str, config: dict = None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    if config:
        clean_cfg = {}
        for k, v in config.items():
            if k == "user_fx":
                continue
            if isinstance(v, set):
                clean_cfg[k] = list(v)
            else:
                clean_cfg[k] = v
        cur.execute("""
            INSERT INTO users (user_id, config_json, last_active)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET config_json=excluded.config_json, last_active=excluded.last_active
        """, (str(user_id), json.dumps(clean_cfg, ensure_ascii=False, default=str), now))
    else:
        cur.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (now, str(user_id)))
    conn.commit()
    conn.close()

def db_get_config(user_id: str) -> dict:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT config_json FROM users WHERE user_id = ?", (str(user_id),))
    row = cur.fetchone()
    conn.close()
    merged = dict(DEFAULT_CONFIG)
    if row and row[0]:
        try:
            merged.update(json.loads(row[0]))
        except Exception:
            pass
    merged["user_fx"] = {}
    return merged

def db_prune_inactive(days: int = 10):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        threshold = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cur.execute("SELECT user_id FROM users WHERE last_active < ?", (threshold,))
        expired = [r[0] for r in cur.fetchall()]
        for uid in expired:
            cur.execute("DELETE FROM users WHERE user_id = ?", (uid,))
            udir = os.path.join(USER_ASSETS_DIR, str(uid))
            if os.path.exists(udir):
                shutil.rmtree(udir, ignore_errors=True)
        conn.commit()
        conn.close()
        if expired:
            logger.info(f"🧹 تم حذف إعدادات ومخلفات {len(expired)} حسابات خاملة منذ 10 أيام.")
    except Exception as e:
        logger.error(f"خطأ تنظيف الحسابات: {e}")

# ============ فحص الاشتراك الصارم ============
async def check_user_subscription(bot, user_id: int) -> tuple[bool, list]:
    missing = []
    
    # 1. فحص القنوات
    for ch in CHANNELS_TO_CHECK:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status in [ChatMember.LEFT, ChatMember.BANNED]:
                missing.append(ch)
            elif member.status not in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.RESTRICTED]:
                missing.append(ch)
        except Exception as e:
            logger.error(f"⚠️ تعذر فحص القناة {ch['id']}: {e}")
            missing.append(ch)

    # 2. فحص المجموعة بالمعرف الثابت
    try:
        g_member = await bot.get_chat_member(chat_id=GROUP_CHAT_ID, user_id=user_id)
        if g_member.status in [ChatMember.LEFT, ChatMember.BANNED]:
            missing.append({"name": "مجموعة النقاش والتعليقات 💬", "link": GROUP_INVITE_LINK})
        elif g_member.status not in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.RESTRICTED]:
            missing.append({"name": "مجموعة النقاش والتعليقات 💬", "link": GROUP_INVITE_LINK})
    except Exception as e:
        logger.error(f"⚠️ تعذر فحص المجموعة {GROUP_CHAT_ID}: {e}")
        missing.append({"name": "مجموعة النقاش والتعليقات 💬", "link": GROUP_INVITE_LINK})

    return (len(missing) == 0, missing)

async def gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
        
    db_touch_user(str(user.id))
    
    ok, missing = await check_user_subscription(context.bot, user.id)
    if not ok:
        keyboard = []
        for item in missing:
            keyboard.append([InlineKeyboardButton(f"✨ انضم إلى: {item['name']}", url=item["link"])])
        
        keyboard.append([InlineKeyboardButton("🔄 تم الاشتراك، تفعيل البوت الآن ✅", callback_data="btn_recheck_sub")])
        
        nice_msg = (
            f"أهلاً بك يا مبدع **{user.first_name}** في استوديو الصوت الذكي! 🎙️🌸\n\n"
            f"يسعدنا جداً انضمامك إلينا! لكي نتمكن من إتاحة كافة خوادم توليد الصوت والسيناريو لك **مجاناً وبدون أي قيود**، نرجو منك التكرم بالانضمام إلى قنواتنا الرسمية ومجموعتنا أدناه:\n\n"
            f"👇 **انضم عبر الأزرار، ثم اضغط على زر التفعيل لتنطلق فوراً:**"
        )
        
        if update.callback_query:
            try:
                await update.callback_query.message.edit_text(nice_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            except Exception:
                await update.callback_query.message.reply_text(nice_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(nice_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return False
        
    return True

# ============ اللغات والأصوات ============
NATIVE_LANG_NAMES = {
    "ar": "العربية", "en": "English", "tr": "Türkçe", "es": "Español",
    "fr": "Français", "de": "Deutsch", "it": "Italiano", "pt": "Português",
    "ru": "Русский", "zh": "中文", "ja": "日本語", "ko": "한국어",
    "hi": "हिन्दी", "ur": "اردو", "fa": "فارسی", "nl": "Nederlands",
    "pl": "Polski", "sv": "Svenska", "id": "Bahasa Indonesia", "el": "Ελληνικά",
    "uk": "Українська", "vi": "Tiếng Việt", "th": "ไทย", "ro": "Română",
    "cs": "Čeština", "hu": "Magyar"
}

DB_NEURAL, DB_FAST, DB_CREATIVE = {}, {}, {}

async def init_neural_json():
    global DB_NEURAL
    if os.path.exists(FILE_NEURAL_JSON):
        try:
            with open(FILE_NEURAL_JSON, "r", encoding="utf-8") as f:
                DB_NEURAL = json.load(f); return
        except Exception: pass
    raw = await edge_tts.list_voices()
    db = {}
    for v in raw:
        loc, sn, g, fn = v.get("Locale", ""), v.get("ShortName", ""), v.get("Gender", ""), v.get("FriendlyName", "")
        lc = loc.split("-")[0].lower()
        if lc not in db:
            db[lc] = {"name": NATIVE_LANG_NAMES.get(lc, lc.upper()), "dialects": {}}
        rc = fn.split("(")[-1].replace(")", "").strip() if "(" in fn else loc
        if loc not in db[lc]["dialects"]:
            db[lc]["dialects"][loc] = {"name": rc, "voices": []}
        db[lc]["dialects"][loc]["voices"].append({
            "id": sn, "name": f"{sn.split('-')[-1].replace('Neural','')} ({'👩' if g=='Female' else '👨'})"
        })
    with open(FILE_NEURAL_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    DB_NEURAL = db

def init_fast_json():
    global DB_FAST
    if os.path.exists(FILE_FAST_JSON):
        try:
            with open(FILE_FAST_JSON, "r", encoding="utf-8") as f:
                DB_FAST = json.load(f); return
        except Exception: pass
    from gtts.lang import tts_langs
    available = tts_langs()
    db = {code: {"name": NATIVE_LANG_NAMES.get(code, name), "code": code} for code, name in available.items()}
    with open(FILE_FAST_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    DB_FAST = db

def init_creative_json():
    global DB_CREATIVE
    if os.path.exists(FILE_CREATIVE_JSON):
        try:
            with open(FILE_CREATIVE_JSON, "r", encoding="utf-8") as f:
                DB_CREATIVE = json.load(f); return
        except Exception: pass
    db = {
        "en": {"name": "English", "voices": [
            {"id": "en_us_001", "name": "Narrator (Female)"},
            {"id": "en_us_006", "name": "Joey (Male)"},
            {"id": "en_us_007", "name": "Professor (Male)"},
            {"id": "en_male_narration", "name": "Storyteller (Male)"},
            {"id": "en_male_funny", "name": "Wacky (Male)"},
            {"id": "en_female_emotional", "name": "Peaceful (Female)"},
        ]},
        "fr": {"name": "Français", "voices": [
            {"id": "fr_001", "name": "Voix 1 (Male)"},
            {"id": "fr_002", "name": "Voix 2 (Male)"},
        ]},
        "es": {"name": "Español", "voices": [
            {"id": "es_001", "name": "Voz 1 (Male)"},
            {"id": "es_mx_002", "name": "Voz Mexicana (Male)"},
        ]},
        "de": {"name": "Deutsch", "voices": [
            {"id": "de_001", "name": "Stimme 1 (Female)"},
            {"id": "de_002", "name": "Stimme 2 (Male)"},
        ]},
        "ja": {"name": "日本語", "voices": [
            {"id": "jp_001", "name": "女性 1 (Female)"},
            {"id": "jp_005", "name": "男性 (Male)"},
        ]},
    }
    with open(FILE_CREATIVE_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    DB_CREATIVE = db

# ============ FFmpeg والصوت ============
async def run_ffmpeg(args: list, timeout: int = 240) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", *args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode == 0
    except Exception as e:
        logger.warning(f"ffmpeg error: {e}")
        return False

async def apply_audio_filters(input_file: str, output_file: str, speed: float = 1.0, volume: float = 1.0) -> bool:
    filters = []
    if abs(speed - 1.0) >= 0.01:
        rem = speed
        while rem > 2.0: filters.append("atempo=2.0"); rem /= 2.0
        while rem < 0.5: filters.append("atempo=0.5"); rem *= 2.0
        filters.append(f"atempo={rem}")
    if abs(volume - 1.0) >= 0.01:
        filters.append(f"volume={volume:.2f}")
    if not filters:
        shutil.copy(input_file, output_file)
        return True
    return await run_ffmpeg([
        "-i", input_file, "-filter:a", ",".join(filters),
        "-c:a", "libmp3lame", "-q:a", "2", "-y", output_file
    ])

async def create_silence(duration: float, output_file: str) -> bool:
    if duration <= 0.01: return False
    return await run_ffmpeg([
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", f"{duration:.2f}", "-c:a", "libmp3lame", "-q:a", "9", "-y", output_file
    ])

async def normalize_to_mp3(input_file: str, output_file: str) -> bool:
    return await run_ffmpeg([
        "-i", input_file, "-ar", "44100", "-ac", "2",
        "-c:a", "libmp3lame", "-q:a", "2", "-y", output_file
    ])

async def overlay_bgm_on_speech(speech_file: str, bgm_file: str, output_file: str, bgm_vol: float = 0.18) -> bool:
    if not os.path.exists(bgm_file):
        shutil.copy(speech_file, output_file)
        return True
    return await run_ffmpeg([
        "-i", speech_file,
        "-stream_loop", "-1", "-i", bgm_file,
        "-filter_complex",
        f"[0:a]volume=1.0[s];[1:a]volume={bgm_vol:.2f}[m];[s][m]amix=inputs=2:duration=first:dropout_transition=2[out]",
        "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2", "-y", output_file
    ])

# ============ توليد أصوات TTS ============
async def synthesize_chunk(text: str, engine: str, voice: str, out_file: str,
                           speed: float = 1.0, pitch: str = "normal", volume: float = 1.0) -> bool:
    clean_text = text.strip()
    if not clean_text:
        return False

    if engine == "neural":
        rate_str = f"{int(round((speed - 1.0) * 100)):+d}%"
        vol_pct = f"{int(round((volume - 1.0) * 100)):+d}%"
        pitch_map = {"whisper": "-40Hz", "low": "-20Hz", "normal": "+0Hz", "high": "+20Hz"}
        comm = edge_tts.Communicate(
            text=clean_text, voice=voice, rate=rate_str,
            pitch=pitch_map.get(pitch, "+0Hz"), volume=vol_pct
        )
        await comm.save(out_file)
        return True
    elif engine == "fast":
        tts = gTTS(text=clean_text, lang=voice)
        await asyncio.to_thread(tts.save, out_file)
        if abs(speed - 1.0) > 0.01 or abs(volume - 1.0) > 0.01:
            tmp = out_file + ".f.mp3"
            if await apply_audio_filters(out_file, tmp, speed, volume):
                os.replace(tmp, out_file)
        return True
    else:
        global GLOBAL_HTTP_SESSION
        if GLOBAL_HTTP_SESSION is None or GLOBAL_HTTP_SESSION.closed:
            GLOBAL_HTTP_SESSION = aiohttp.ClientSession()
        api_url = "https://tiktok-tts.weilnet.workers.dev/api/generation"
        async with GLOBAL_HTTP_SESSION.post(api_url, json={"text": clean_text, "voice": voice}) as resp:
            data = await resp.json()
            if "data" in data and data["data"]:
                with open(out_file, "wb") as f:
                    f.write(base64.b64decode(data["data"]))
                return True
        return False

# ============ محلل نصوص السيناريو والمؤثرات ============
def parse_strict_script(script_text: str, mode: str, max_voices: int = 1):
    pattern = re.compile(r'(\(\s*(?:v[1-5]|s\d+(?:\.\d+)?|mf[1-5]|m[1-5]|f[1-5]|f)\s*\))', re.IGNORECASE)
    parts = pattern.split(script_text)

    sequence = []
    cur_voice = 0

    for part in parts:
        token = part.strip()
        if not token:
            continue

        m_tag = re.match(r'^\(\s*([a-zA-Z0-9\.]+)\s*\)$', token)
        if m_tag:
            tag_content = m_tag.group(1).lower()

            if tag_content.startswith('v') and tag_content[1:].isdigit():
                cur_voice = min(int(tag_content[1:]) - 1, max_voices - 1)
                continue

            if tag_content.startswith('s'):
                try:
                    dur = float(tag_content[1:])
                    sequence.append({"type": "silence", "duration": dur})
                    continue
                except ValueError:
                    pass

            if mode in ["story", "multilang"]:
                if tag_content.startswith('mf') and tag_content[2:].isdigit():
                    sequence.append({"type": "mf_sound", "fx_id": tag_content[2:]})
                    continue
                elif tag_content.startswith('m') and tag_content[1:].isdigit():
                    sequence.append({"type": "m_bgm_start", "fx_id": tag_content[1:]})
                    continue
                elif tag_content == 'f' or (tag_content.startswith('f') and tag_content[1:].isdigit()):
                    fx_id = tag_content[1:] if len(tag_content) > 1 else None
                    sequence.append({"type": "m_bgm_stop", "fx_id": fx_id})
                    continue

        sequence.append({"type": "speech", "voice_idx": cur_voice, "text": token})

    return sequence

def _sync_split_text_semantically(text: str, max_chars: int = 1000) -> list:
    clean = text.strip()
    if not clean: return []
    paras = [p.strip() for p in clean.splitlines() if p.strip()] or [clean]
    result, chunk, clen = [], [], 0
    for p in paras:
        if len(p) > max_chars:
            sents = re.split(r'(?<=[.!?؟،؛\n])\s+', p)
            for s in sents:
                if not s.strip(): continue
                if clen + len(s) > max_chars and chunk:
                    result.append(" ".join(chunk)); chunk, clen = [s], len(s)
                else: chunk.append(s); clen += len(s)
        else:
            if clen + len(p) > max_chars and chunk:
                result.append("\n".join(chunk)); chunk, clen = [p], len(p)
            else: chunk.append(p); clen += len(p)
    if chunk: result.append("\n".join(chunk))
    return result

async def split_text_semantically(text: str, max_chars: int = 1000) -> list:
    return await asyncio.to_thread(_sync_split_text_semantically, text, max_chars)

# ============ واجهات وأزرار التحكم السريعة ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gatekeeper(update, context):
        return ConversationHandler.END
        
    user_id = str(update.effective_user.id)
    saved = db_get_config(user_id)
    context.user_data.update(saved)

    first_name = update.effective_user.first_name or "صديقي"
    keyboard = [
        [InlineKeyboardButton("المحرك العصبي الفائق (جودة استوديو)", callback_data="eng_neural")],
        [InlineKeyboardButton("المحرك السريع القياسي (خفيف)", callback_data="eng_fast")],
        [InlineKeyboardButton("المحرك التعبيري الحديث (TikTok TTS)", callback_data="eng_creative")],
        [InlineKeyboardButton("محرك القصص والسيناريو 🎬", callback_data="eng_story")],
        [InlineKeyboardButton("محرك متعدد اللغات الذكي 🌐", callback_data="eng_multilang")],
    ]
    welcome_text = (
        f"مرحباً بك يا **{first_name}** في بوت هندسة الصوت والسيناريو الذكي! 🎙️\n"
        f"المطور: **أحمد حسن عبد النبي** 👨‍💻\n\n"
        f"⚙️ لوحة الإعدادات متاحة دائماً عبر /settings\n\n"
        f"🔹 **الخطوة 1:** اختر المحرك الصوتي المطلوب:"
    )
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return STEP_ENGINE

def build_lang_keyboard(lang_keys: list, db_dict: dict, page: int = 0):
    page_size = 8
    total_pages = max(1, (len(lang_keys) + page_size - 1) // page_size)
    current_keys = lang_keys[page * page_size: (page + 1) * page_size]
    buttons, row = [], []
    for k in current_keys:
        row.append(InlineKeyboardButton(db_dict[k]["name"], callback_data=f"lang_{k}"))
        if len(row) == 2: buttons.append(row); row = []
    if row: buttons.append(row)
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"page_{page+1}"))
    if nav: buttons.append(nav)
    return InlineKeyboardMarkup(buttons)

def build_voice_keyboard(voices: list, page: int = 0, page_size: int = 8):
    total_pages = max(1, (len(voices) + page_size - 1) // page_size)
    current = voices[page * page_size: (page + 1) * page_size]
    buttons, row = [], []
    for v in current:
        row.append(InlineKeyboardButton(v["name"], callback_data=f"voc_{v['id']}"))
        if len(row) == 2: buttons.append(row); row = []
    if row: buttons.append(row)
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"vpage_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"vpage_{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_lang")])
    return InlineKeyboardMarkup(buttons)

async def select_engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    engine = query.data.replace("eng_", "")
    context.user_data["mode"] = engine

    if engine == "story":
        context.user_data["story_slots"] = []
        context.user_data["user_fx"] = {}
        return await show_sub_engines(update, context, slot_num=1, mode="story")
    elif engine == "multilang":
        context.user_data["multilang_slots"] = []
        context.user_data["user_fx"] = {}
        context.user_data["used_langs"] = []
        return await show_sub_engines(update, context, slot_num=1, mode="multilang")
    else:
        context.user_data["engine"] = engine
        return await setup_language_selection(update, context, engine)

async def show_sub_engines(update: Update, context: ContextTypes.DEFAULT_TYPE, slot_num: int, mode: str):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("المحرك العصبي (استوديو)", callback_data="subeng_neural")],
        [InlineKeyboardButton("المحرك السريع (خفيف)", callback_data="subeng_fast")],
        [InlineKeyboardButton("محرك TikTok", callback_data="subeng_creative")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    title = f"🎙️ اختر المحرك الصوتي للشخصية رقم (v{slot_num}):" if mode == "story" else f"🌐 اختر المحرك الصوتي للغة رقم ({slot_num}):"
    await query.edit_message_text(title, reply_markup=markup, parse_mode="Markdown")
    return STEP_LANG

async def select_sub_engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    sub_engine = query.data.replace("subeng_", "")
    context.user_data["current_sub_engine"] = sub_engine
    return await setup_language_selection(update, context, sub_engine)

async def setup_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, engine: str):
    query = update.callback_query
    priority = ["ar", "en", "tr", "fr", "es", "de", "ru", "it"]
    if engine == "neural":
        all_keys = sorted(DB_NEURAL.keys(), key=lambda x: priority.index(x) if x in priority else 999)
        db_ref = DB_NEURAL
    elif engine == "fast":
        all_keys = sorted(DB_FAST.keys(), key=lambda x: priority.index(x) if x in priority else 999)
        db_ref = DB_FAST
    else:
        all_keys = list(DB_CREATIVE.keys())
        db_ref = DB_CREATIVE

    mode = context.user_data.get("mode", "")
    if mode == "multilang":
        used = set(context.user_data.get("used_langs", []))
        keys = [k for k in all_keys if k not in used]
    else:
        keys = all_keys

    context.user_data["lang_keys"] = keys
    markup = build_lang_keyboard(keys, db_ref, 0)
    await query.edit_message_text("🔹 **اختر اللغة المطلوبة:**", reply_markup=markup, parse_mode="Markdown")
    return STEP_LANG

async def paginate_languages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.replace("page_", ""))
    mode = context.user_data.get("mode", "")
    engine = context.user_data.get("current_sub_engine" if mode in ["story", "multilang"] else "engine", "neural")
    keys = context.user_data.get("lang_keys", [])
    db_map = {"neural": DB_NEURAL, "fast": DB_FAST, "creative": DB_CREATIVE}
    markup = build_lang_keyboard(keys, db_map[engine], page)
    await query.edit_message_text("🔹 **اختر اللغة المطلوبة:**", reply_markup=markup, parse_mode="Markdown")
    return STEP_LANG

async def select_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang_code = query.data.replace("lang_", "")
    context.user_data["selected_lang"] = lang_code
    mode = context.user_data.get("mode", "")
    engine = context.user_data.get("current_sub_engine" if mode in ["story", "multilang"] else "engine", "neural")

    if engine == "neural":
        dialects = DB_NEURAL[lang_code]["dialects"]
        buttons, row = [], []
        for loc_code, loc_data in dialects.items():
            row.append(InlineKeyboardButton(loc_data["name"], callback_data=f"dia_{loc_code}"))
            if len(row) == 2: buttons.append(row); row = []
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_lang")])
        await query.edit_message_text("🔹 **اختر اللهجة / الدولة:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return STEP_DIALECT
    elif engine == "fast":
        lang_name = DB_FAST[lang_code]["name"]
        return await on_voice_selected(update, context, voice_id=lang_code, voice_name=lang_name, lang_code=lang_code)
    else:
        voices = DB_CREATIVE[lang_code]["voices"]
        context.user_data["voice_list"] = voices
        context.user_data["voice_page"] = 0
        markup = build_voice_keyboard(voices, 0)
        await query.edit_message_text("🔹 **اختر الصوت التعبيري:**", reply_markup=markup, parse_mode="Markdown")
        return STEP_VOICE

async def select_dialect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "back_to_lang":
        mode = context.user_data.get("mode", "")
        engine = context.user_data.get("current_sub_engine" if mode in ["story", "multilang"] else "engine", "neural")
        return await setup_language_selection(update, context, engine)

    dia_code = query.data.replace("dia_", "")
    context.user_data["selected_dia"] = dia_code
    lang_code = context.user_data.get("selected_lang", "ar")
    voices = DB_NEURAL[lang_code]["dialects"][dia_code]["voices"]
    context.user_data["voice_list"] = voices
    context.user_data["voice_page"] = 0
    markup = build_voice_keyboard(voices, 0)
    await query.edit_message_text("🔹 **اختر الصوت:**", reply_markup=markup, parse_mode="Markdown")
    return STEP_VOICE

async def paginate_voices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.replace("vpage_", ""))
    context.user_data["voice_page"] = page
    voices = context.user_data.get("voice_list", [])
    markup = build_voice_keyboard(voices, page)
    await query.edit_message_text("🔹 **اختر الصوت:**", reply_markup=markup, parse_mode="Markdown")
    return STEP_VOICE

async def select_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "back_to_lang":
        mode = context.user_data.get("mode", "")
        engine = context.user_data.get("current_sub_engine" if mode in ["story", "multilang"] else "engine", "creative")
        return await setup_language_selection(update, context, engine)

    voice_id = query.data.replace("voc_", "")
    lang_code = context.user_data.get("selected_lang", "ar")
    return await on_voice_selected(update, context, voice_id=voice_id, voice_name=voice_id, lang_code=lang_code)

async def on_voice_selected(update: Update, context: ContextTypes.DEFAULT_TYPE, voice_id: str, voice_name: str, lang_code: str) -> int:
    mode = context.user_data.get("mode", "single")
    slot = {
        "engine": context.user_data.get("current_sub_engine", "neural"),
        "voice": voice_id,
        "lang": lang_code,
        "speed": 1.0,
        "pitch": "normal",
        "volume": 1.0,
        "label": f"{voice_name}"
    }

    if mode == "story":
        slots = context.user_data.get("story_slots", [])
        slots.append(slot)
        context.user_data["story_slots"] = slots
        return await show_story_menu(update, context)
    elif mode == "multilang":
        slots = context.user_data.get("multilang_slots", [])
        slots.append(slot)
        context.user_data["multilang_slots"] = slots
        context.user_data["used_langs"] = list({s["lang"] for s in slots})
        return await show_multilang_menu(update, context)
    else:
        context.user_data["voice"] = voice_id
        context.user_data["voice_name"] = voice_name
        return await ask_speed_single(update, context)

# ============ شاشة طاقم العمل والمؤثرات ============
async def show_story_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    slots = context.user_data.get("story_slots", [])
    user_fx = context.user_data.get("user_fx", {})

    text = "🎬 **لوحة طاقم السيناريو والمؤثرات الصوتية:**\n━━━━━━━━━━━━━━━━━━━━\n"
    text += "🎙️ **الأصوات المحددة:**\n"
    for i, s in enumerate(slots, 1):
        p_desc = "همس" if s.get("pitch") == "whisper" else s.get("pitch", "normal")
        text += f"  • `(v{i})` {s['voice']} | ⚡{s.get('speed', 1.0)}x | 🎵{p_desc}\n"

    text += f"\n🎵 **المؤثرات الصوتية والموسيقى المرفوعة ({len(user_fx)} / 5):**\n"
    if not user_fx:
        text += "  *(لم يتم رفع أي مؤثرات صوتية - اختياري)*\n"
    else:
        for fx_id in sorted(user_fx.keys(), key=lambda x: int(x)):
            text += f"  • `(m{fx_id})` لبدء الموسيقى / `(mf{fx_id})` لفاصل: مؤثر {fx_id} ✅\n"

    buttons = []
    if len(slots) < 5:
        buttons.append([InlineKeyboardButton(f"➕ إضافة شخصية جديدة (v{len(slots)+1})", callback_data="story_add_char")])

    if len(user_fx) < 5:
        next_fx = len(user_fx) + 1
        buttons.append([InlineKeyboardButton(f"➕ إضافة مؤثر صوتي ({next_fx}/5)", callback_data="btn_add_fx")])

    buttons.append([InlineKeyboardButton("🎛️ ضبط السرعة والنبرة للأصوات", callback_data="btn_tune_voices")])

    if len(slots) >= 1:
        buttons.append([InlineKeyboardButton("✅ اعتماد الطاقم والانتقال للصيغة", callback_data="btn_finish_crew")])
    buttons.append([InlineKeyboardButton("🔄 إعادة تعيين الطاقم", callback_data="story_reset")])

    markup = InlineKeyboardMarkup(buttons)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return STEP_STORY_MENU

async def show_multilang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    slots = context.user_data.get("multilang_slots", [])
    user_fx = context.user_data.get("user_fx", {})

    text = "🌐 **لوحة محرك اللغات المتعددة:**\n━━━━━━━━━━━━━━━━━━━━\n"
    for i, s in enumerate(slots, 1):
        text += f"  • [{s.get('lang')}] {s['voice']} | ⚡{s.get('speed', 1.0)}x\n"

    text += f"\n🎵 **المؤثرات الصوتية ({len(user_fx)} / 1):**\n"
    if user_fx:
        text += "  • `(m1)` لبدء الموسيقى أو `(mf1)` لفاصل: مؤثر 1 جاهز ✅\n"
    else:
        text += "  *(يمكنك إضافة صوت مؤثر 1 اختياري)*\n"

    buttons = []
    if len(slots) < 3:
        buttons.append([InlineKeyboardButton(f"➕ إضافة لغة أخرى ({len(slots)+1}/3)", callback_data="mlang_add_char")])
    if len(user_fx) < 1:
        buttons.append([InlineKeyboardButton("➕ إضافة مؤثر صوتي (1/1)", callback_data="btn_add_fx")])
    buttons.append([InlineKeyboardButton("🎛️ ضبط السرعة والنبرة للأصوات", callback_data="btn_tune_voices")])
    if len(slots) >= 1:
        buttons.append([InlineKeyboardButton("✅ اعتماد والانتقال للصيغة", callback_data="btn_finish_crew")])
    buttons.append([InlineKeyboardButton("🔄 إعادة الضبط", callback_data="mlang_reset")])

    markup = InlineKeyboardMarkup(buttons)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return STEP_MULTILANG_MENU

async def handle_story_menu_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    mode = context.user_data.get("mode", "story")

    if data in ("story_add_char", "mlang_add_char"):
        slots = context.user_data.get("story_slots" if mode == "story" else "multilang_slots", [])
        return await show_sub_engines(update, context, slot_num=len(slots)+1, mode=mode)

    elif data == "btn_add_fx":
        user_fx = context.user_data.get("user_fx", {})
        fx_num = len(user_fx) + 1
        context.user_data["expecting_fx_id"] = str(fx_num)
        msg = (
            f"📥 **أرسل الآن الملف الصوتي للمؤثر رقم ({fx_num}):**\n\n"
            f"• للتشغيل كموسيقى خلفية مرافقة: استخدم `(m{fx_num})` وإيقافها بـ `(f{fx_num})`.\n"
            f"• للتشغيل كمقطع مستقل ينتهي أولاً ثم يبدأ الحوار: استخدم `(mf{fx_num})`."
        )
        await query.edit_message_text(msg, parse_mode="Markdown")
        return STEP_WAIT_FX_AUDIO

    elif data == "btn_tune_voices":
        slots = context.user_data.get("story_slots" if mode == "story" else "multilang_slots", [])
        buttons = []
        for i, s in enumerate(slots):
            buttons.append([InlineKeyboardButton(f"🎛️ ضبط صوت (v{i+1}): {s['voice']}", callback_data=f"tune_voice_{i}")])
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_crew_menu")])
        await query.edit_message_text("🔹 **اختر الصوت أو الشخصية التي تريد تعديل سرعتها ونبرتها:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return STEP_VOICE_CUSTOMIZE

    elif data == "btn_finish_crew":
        return await ask_format(update, context)

    elif data in ("story_reset", "mlang_reset"):
        context.user_data["story_slots"] = []
        context.user_data["multilang_slots"] = []
        context.user_data["user_fx"] = {}
        context.user_data["used_langs"] = []
        return await show_sub_engines(update, context, slot_num=1, mode=mode)

    return STEP_STORY_MENU

async def receive_fx_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gatekeeper(update, context):
        return STEP_TEXT

    audio_obj = update.message.audio or update.message.voice
    if not audio_obj:
        await update.message.reply_text("❌ يرجى إرسال ملف صوتي أو فويس نوت فقط!")
        return STEP_WAIT_FX_AUDIO

    user_id = str(update.effective_user.id)
    fx_id = context.user_data.get("expecting_fx_id", "1")

    udir = os.path.join(USER_ASSETS_DIR, user_id)
    os.makedirs(udir, exist_ok=True)
    dest_path = os.path.join(udir, f"fx_{fx_id}.mp3")

    file_info = await audio_obj.get_file()
    tmp_dl = os.path.join(udir, f"tmp_{fx_id}")
    await file_info.download_to_drive(tmp_dl)
    await normalize_to_mp3(tmp_dl, dest_path)
    if os.path.exists(tmp_dl):
        os.remove(tmp_dl)

    user_fx = context.user_data.get("user_fx", {})
    user_fx[str(fx_id)] = dest_path
    context.user_data["user_fx"] = user_fx

    await update.message.reply_text(f"✅ تم حفظ المؤثر الصوتي بنجاح! وسومه: `(m{fx_id})` أو `(mf{fx_id})`", parse_mode="Markdown")

    mode = context.user_data.get("mode", "story")
    if mode == "story":
        return await show_story_menu(update, context)
    else:
        return await show_multilang_menu(update, context)

async def handle_voice_tuning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    mode = context.user_data.get("mode", "story")
    slots = context.user_data.get("story_slots" if mode == "story" else "multilang_slots", [])

    if data == "back_to_crew_menu":
        return await (show_story_menu(update, context) if mode == "story" else show_multilang_menu(update, context))

    if data.startswith("tune_voice_"):
        idx = int(data.replace("tune_voice_", ""))
        context.user_data["active_tune_idx"] = idx
        return await render_voice_tuning_card(update, context, idx)

    idx = context.user_data.get("active_tune_idx", 0)
    if data.startswith("t_spd_"):
        slots[idx]["speed"] = float(data.replace("t_spd_", ""))
        return await render_voice_tuning_card(update, context, idx)
    elif data.startswith("t_pit_"):
        slots[idx]["pitch"] = data.replace("t_pit_", "")
        return await render_voice_tuning_card(update, context, idx)
    elif data == "t_done":
        return await (show_story_menu(update, context) if mode == "story" else show_multilang_menu(update, context))

    return STEP_VOICE_CUSTOMIZE

async def render_voice_tuning_card(update: Update, context: ContextTypes.DEFAULT_TYPE, idx: int):
    query = update.callback_query
    mode = context.user_data.get("mode", "story")
    slots = context.user_data.get("story_slots" if mode == "story" else "multilang_slots", [])
    s = slots[idx]
    spd, pit = s.get("speed", 1.0), s.get("pitch", "normal")

    title = (
        f"🎛️ **تخصيص سرعة ونبرة الصوت (v{idx+1}):** `{s.get('voice')}`\n"
        f"⚡ السرعة الحالية: `{spd}x` | 🎵 النبرة الحالية: `{pit}`"
    )
    spd_row = [
        InlineKeyboardButton(f"{'✅ ' if abs(spd-0.8)<0.05 else ''}0.8x", callback_data="t_spd_0.8"),
        InlineKeyboardButton(f"{'✅ ' if abs(spd-1.0)<0.05 else ''}1.0x", callback_data="t_spd_1.0"),
        InlineKeyboardButton(f"{'✅ ' if abs(spd-1.25)<0.05 else ''}1.25x", callback_data="t_spd_1.25"),
    ]
    pit_row = [
        InlineKeyboardButton(f"{'✅ ' if pit=='whisper' else ''}🤫 همس", callback_data="t_pit_whisper"),
        InlineKeyboardButton(f"{'✅ ' if pit=='normal' else ''}عادي", callback_data="t_pit_normal"),
        InlineKeyboardButton(f"{'✅ ' if pit=='high' else ''}مرتفع", callback_data="t_pit_high"),
    ]
    back_row = [InlineKeyboardButton("✅ حفظ والعودة للطاقم", callback_data="t_done")]
    await query.edit_message_text(title, reply_markup=InlineKeyboardMarkup([spd_row, pit_row, back_row]), parse_mode="Markdown")
    return STEP_VOICE_CUSTOMIZE

async def ask_speed_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    spd = context.user_data.get("speed", 1.0)
    pit = context.user_data.get("pitch", "normal")

    title = (
        f"⚡ **اضبط سرعة ونبرة الصوت الفردي:**\n"
        f"💡 السكتات يمكنك كتابتها مباشرة بالنص مثل `(s3)`.\n"
        f"السرعة الحالية: `{spd}x` | النبرة: `{pit}`"
    )
    spd_row = [
        InlineKeyboardButton(f"{'✅ ' if abs(spd-0.75)<0.05 else ''}0.75x", callback_data="spd_0.75"),
        InlineKeyboardButton(f"{'✅ ' if abs(spd-1.0)<0.05 else ''}1.0x", callback_data="spd_1.0"),
        InlineKeyboardButton(f"{'✅ ' if abs(spd-1.25)<0.05 else ''}1.25x", callback_data="spd_1.25"),
        InlineKeyboardButton(f"{'✅ ' if abs(spd-1.5)<0.05 else ''}1.5x", callback_data="spd_1.5"),
    ]
    pit_row = [
        InlineKeyboardButton(f"{'✅ ' if pit=='whisper' else ''}🤫 همس", callback_data="pit_whisper"),
        InlineKeyboardButton(f"{'✅ ' if pit=='low' else ''}خفيض", callback_data="pit_low"),
        InlineKeyboardButton(f"{'✅ ' if pit=='normal' else ''}عادي", callback_data="pit_normal"),
        InlineKeyboardButton(f"{'✅ ' if pit=='high' else ''}مرتفع", callback_data="pit_high"),
    ]
    action_row = [
        InlineKeyboardButton("🔊 معاينة", callback_data="spd_preview"),
        InlineKeyboardButton("✅ متابعة", callback_data="spd_go")
    ]
    markup = InlineKeyboardMarkup([spd_row, pit_row, action_row])
    await query.edit_message_text(title, reply_markup=markup, parse_mode="Markdown")
    return STEP_SPEED

async def handle_speed_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("spd_") and data not in ("spd_go", "spd_preview"):
        context.user_data["speed"] = float(data.replace("spd_", ""))
        return await ask_speed_single(update, context)
    elif data.startswith("pit_"):
        context.user_data["pitch"] = data.replace("pit_", "")
        return await ask_speed_single(update, context)
    elif data == "spd_preview":
        return await preview_voice(update, context)
    elif data == "spd_go":
        return await ask_format(update, context)
    return STEP_SPEED

async def preview_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("⏳ جاري تجهيز المعاينة...")
    eng = context.user_data.get("engine", "neural")
    voc = context.user_data.get("voice", "ar-EG-SalmaNeural")
    spd = context.user_data.get("speed", 1.0)
    pit = context.user_data.get("pitch", "normal")

    sample = "مرحباً بك، هذه عينة تجريبية للصوت المختار."
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = os.path.join(tmpdir, "preview.mp3")
            if await synthesize_chunk(sample, eng, voc, p, speed=spd, pitch=pit):
                with open(p, "rb") as f:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id, audio=f,
                        title="معاينة الصوت", performer="Smart Studio"
                    )
    except Exception as e:
        logger.error(f"Preview error: {e}")
    return STEP_SPEED

async def ask_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    formats = [
        [InlineKeyboardButton("MP3 (شامل التوافقية - خفيف)", callback_data="fmt_mp3")],
        [InlineKeyboardButton("WAV (جودة نقية غير مضغوطة)", callback_data="fmt_wav")],
        [InlineKeyboardButton("OGG (رسالة صوتية Voice Note)", callback_data="fmt_ogg")],
    ]
    markup = InlineKeyboardMarkup(formats)
    msg = "📁 **اختر صيغة الملف الصوتي المستخرج:**"
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")
    return STEP_FORMAT

async def select_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chosen = query.data.replace("fmt_", "")
    context.user_data["audio_format"] = chosen

    db_touch_user(str(update.effective_user.id), context.user_data)

    mode = context.user_data.get("mode", "single")
    info = (
        "🎬 **أرسل نص السيناريو الآن (أو مستند .docx/.txt/.srt)**\n\n"
        "💡 **الوسوم المدعومة:**\n"
        "• `(v1)` إلى `(v5)` للشخصيات أو اللغات.\n"
        "• `(s2)` لصمت 2 ثانية.\n"
        "• `(m1)` لبدء الموسيقى الخلفية المرافقة، و`(f1)` أو `(f)` لإيقافها.\n"
        "• `(mf1)` لتشغيل المؤثر بالكامل قبل مواصلة الحوار."
        if mode in ("story", "multilang") else
        "🔹 **أرسل النص المطلوب تحويله الآن (أو مستند):**"
    )
    await query.edit_message_text(f"{info}\n\n📁 الصيغة المحددة: `({chosen.upper()})`", parse_mode="Markdown")
    return STEP_TEXT

# ============ طابور المعالجة الهادئ بدون مصطلحات الطابور ============
def get_global_queue():
    global GLOBAL_TASK_QUEUE
    if GLOBAL_TASK_QUEUE is None:
        GLOBAL_TASK_QUEUE = asyncio.Queue()
    return GLOBAL_TASK_QUEUE

def get_tts_semaphore():
    global GLOBAL_TTS_SEMAPHORE
    if GLOBAL_TTS_SEMAPHORE is None:
        GLOBAL_TTS_SEMAPHORE = asyncio.Semaphore(MAX_PARALLEL_RENDERS)
    return GLOBAL_TTS_SEMAPHORE

async def global_queue_worker():
    q = get_global_queue()
    sem = get_tts_semaphore()
    while True:
        update, context, text, status_msg = await q.get()
        asyncio.create_task(process_queued_task(sem, update, context, text, status_msg, q))

async def process_queued_task(sem, update, context, text, status_msg, q):
    async with sem:
        try:
            await status_msg.edit_text("⏳ **بدأت معالجة وهندسة المقطع الصوتي الآن بأعلى جودة...**", parse_mode="Markdown")
            await _do_generate(update, context, text, status_msg)
        except Exception as e:
            logger.error(f"Render error: {e}", exc_info=True)
            try: await status_msg.edit_text("⚠️ حدث خطأ أثناء التوليد، يرجى المحاولة لاحقاً.")
            except Exception: pass
        finally:
            user_id = update.effective_user.id
            udir = os.path.join(USER_ASSETS_DIR, str(user_id))
            if os.path.exists(udir):
                shutil.rmtree(udir, ignore_errors=True)
            context.user_data["user_fx"] = {}
            q.task_done()

async def enqueue_task(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    q = get_global_queue()
    status_msg = await update.message.reply_text(
        "⏳ **جاري استلام طلبك وهندسة الصوت بدقة عالية...**\n"
        "يرجى الانتظار قليلاً، سوف يستغرق التوليد بضع لحظات وسنرسل لك المقطع الصوتي مباشرة 🌸",
        parse_mode="Markdown"
    )
    await q.put((update, context, text, status_msg))

async def _do_generate(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, status_msg):
    mode = context.user_data.get("mode", "single")
    audio_format = context.user_data.get("audio_format", "mp3")
    user_fx = context.user_data.get("user_fx", {})

    with tempfile.TemporaryDirectory() as tmpdir:
        chunks = []
        active_bgm_file = None

        if mode == "story":
            slots = context.user_data.get("story_slots", [])
            if not slots:
                slots = [{"engine": "neural", "voice": "ar-EG-SalmaNeural", "speed": 1.0, "pitch": "normal", "volume": 1.0}]
            sequence = parse_strict_script(user_text, mode="story", max_voices=len(slots))

            for idx, item in enumerate(sequence):
                cf = os.path.join(tmpdir, f"st_{idx}.mp3")

                if item["type"] == "m_bgm_start":
                    active_bgm_file = user_fx.get(str(item["fx_id"]))
                    continue
                elif item["type"] == "m_bgm_stop":
                    active_bgm_file = None
                    continue
                elif item["type"] == "silence":
                    if await create_silence(item["duration"], cf):
                        chunks.append(cf)
                elif item["type"] == "mf_sound":
                    fx_path = user_fx.get(str(item["fx_id"]))
                    if fx_path and os.path.exists(fx_path):
                        if await normalize_to_mp3(fx_path, cf):
                            chunks.append(cf)
                elif item["type"] == "speech":
                    slot = slots[item["voice_idx"]]
                    pieces = await split_text_semantically(item["text"])
                    for j, p in enumerate(pieces):
                        sub_raw = os.path.join(tmpdir, f"raw_{idx}_{j}.mp3")
                        sub_final = os.path.join(tmpdir, f"sp_{idx}_{j}.mp3")
                        if await synthesize_chunk(p, slot["engine"], slot["voice"], sub_raw, speed=slot.get("speed", 1.0), pitch=slot.get("pitch", "normal")):
                            if active_bgm_file and os.path.exists(active_bgm_file):
                                await overlay_bgm_on_speech(sub_raw, active_bgm_file, sub_final, bgm_vol=0.18)
                            else:
                                shutil.copy(sub_raw, sub_final)
                            chunks.append(sub_final)

        elif mode == "multilang":
            slots = context.user_data.get("multilang_slots", [])
            sequence = parse_strict_script(user_text, mode="multilang", max_voices=len(slots))
            for idx, item in enumerate(sequence):
                cf = os.path.join(tmpdir, f"ml_{idx}.mp3")
                if item["type"] == "m_bgm_start":
                    active_bgm_file = user_fx.get(str(item["fx_id"]))
                    continue
                elif item["type"] == "m_bgm_stop":
                    active_bgm_file = None
                    continue
                elif item["type"] == "silence":
                    if await create_silence(item["duration"], cf): chunks.append(cf)
                elif item["type"] == "mf_sound":
                    fx_path = user_fx.get(str(item["fx_id"]))
                    if fx_path and os.path.exists(fx_path):
                        if await normalize_to_mp3(fx_path, cf): chunks.append(cf)
                elif item["type"] == "speech":
                    slot = slots[item["voice_idx"]]
                    pieces = await split_text_semantically(item["text"])
                    for j, p in enumerate(pieces):
                        sub_raw = os.path.join(tmpdir, f"raw_ml_{idx}_{j}.mp3")
                        sub_final = os.path.join(tmpdir, f"sp_ml_{idx}_{j}.mp3")
                        if await synthesize_chunk(p, slot["engine"], slot["voice"], sub_raw, speed=slot.get("speed", 1.0), pitch=slot.get("pitch", "normal")):
                            if active_bgm_file and os.path.exists(active_bgm_file):
                                await overlay_bgm_on_speech(sub_raw, active_bgm_file, sub_final, bgm_vol=0.18)
                            else:
                                shutil.copy(sub_raw, sub_final)
                            chunks.append(sub_final)
        else:
            engine = context.user_data.get("engine", "neural")
            voice = context.user_data.get("voice", "ar-EG-SalmaNeural")
            spd = context.user_data.get("speed", 1.0)
            pit = context.user_data.get("pitch", "normal")
            sequence = parse_strict_script(user_text, mode="single", max_voices=1)
            for idx, item in enumerate(sequence):
                cf = os.path.join(tmpdir, f"sg_{idx}.mp3")
                if item["type"] == "silence":
                    if await create_silence(item["duration"], cf): chunks.append(cf)
                elif item["type"] == "speech":
                    pieces = await split_text_semantically(item["text"])
                    for j, p in enumerate(pieces):
                        sub_cf = os.path.join(tmpdir, f"sp_sg_{idx}_{j}.mp3")
                        if await synthesize_chunk(p, engine, voice, sub_cf, speed=spd, pitch=pit):
                            chunks.append(sub_cf)

        if not chunks:
            raise Exception("لم يتم توليد أي صوت.")

        list_file = os.path.join(tmpdir, "concat.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for c in chunks:
                if os.path.exists(c) and os.path.getsize(c) > 0:
                    f.write(f"file '{os.path.abspath(c).replace(chr(92), '/')}'\n")

        final_out = os.path.join(tmpdir, f"out.{audio_format}")
        if audio_format == "wav":
            await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file, "-c:a", "pcm_s16le", "-y", final_out])
        elif audio_format == "ogg":
            await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file, "-c:a", "libopus", "-b:a", "64k", "-y", final_out])
        else:
            await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-y", final_out])

        with open(final_out, "rb") as af:
            if audio_format == "ogg":
                await update.message.reply_voice(voice=af, caption="✅ تم توليد الرسالة الصوتية بنجاح!")
            else:
                await update.message.reply_audio(audio=af, title="Studio TTS Master", performer="Smart Studio", caption="🎬 **تم اكتمال هندسة السيناريو والمؤثرات بنجاح!**")
        try:
            await status_msg.delete()
        except Exception:
            pass

# ============ استقبال النصوص والملفات ============
async def generate_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gatekeeper(update, context): return STEP_TEXT
    txt = (update.message.text or "").strip()
    if txt:
        await enqueue_task(update, context, txt)
    return STEP_TEXT

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gatekeeper(update, context): return STEP_TEXT
    doc = update.message.document
    if not doc: return STEP_TEXT
    ext = os.path.splitext(doc.file_name or "f.txt")[1].lower()
    if ext not in (".txt", ".docx", ".srt"):
        await update.message.reply_text("❌ صيغة غير مدعومة. المقبول: .txt, .docx, .srt")
        return STEP_TEXT

    tmpdir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmpdir, doc.file_name)
    try:
        f = await doc.get_file()
        await f.download_to_drive(tmp_path)
        text = ""
        if ext == ".txt":
            with open(tmp_path, "r", encoding="utf-8", errors="ignore") as tf: text = tf.read()
        elif ext == ".docx" and HAS_DOCX:
            d = DocxDocument(tmp_path)
            text = "\n".join(p.text for p in d.paragraphs)
        elif ext == ".srt":
            with open(tmp_path, "r", encoding="utf-8", errors="ignore") as tf:
                text = "\n".join([l.strip() for l in tf.splitlines() if l.strip() and not re.match(r'^\d+$|^\d{2}:\d{2}', l)])
        if text.strip():
            await enqueue_task(update, context, text)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return STEP_TEXT

# ============ أوامر الإعدادات ============
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context): return
    cfg = db_get_config(str(update.effective_user.id))
    context.user_data.update(cfg)
    msg = (
        f"⚙️ **لوحة التحكم السريعة:**\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 الوضع النشط: `{cfg.get('mode')}`\n"
        f"📁 الصيغة: `{cfg.get('audio_format', 'mp3').upper()}`\n"
        f"🎙️ الصوت: `{cfg.get('voice', 'Salma')}` | ⚡ السرعة: `{cfg.get('speed', 1.0)}x`\n\n"
        f"💡 لتغيير الصوت أو المحرك بالكامل أرسل: /start"
    )
    btn = [[InlineKeyboardButton("🔀 بدء التخصيص من جديد", callback_data="cfg_restart")]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "btn_recheck_sub":
        ok, _ = await check_user_subscription(context.bot, update.effective_user.id)
        if ok:
            await query.edit_message_text("🎉 **أهلاً بك معنا! تم التحقق من اشتراكك بنجاح.**\nأرسل /start أو أرسل نصك/ملفك الصوتي الآن لتبدأ!", parse_mode="Markdown")
        else:
            await gatekeeper(update, context)
            
    elif query.data == "cfg_restart":
        return await start(update, context)

async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer()
    except Exception: pass

# تنظيف دوري للبيانات الخاملة كل 24 ساعة
async def background_pruner():
    while True:
        try: db_prune_inactive(days=10)
        except Exception: pass
        await asyncio.sleep(86400)

async def on_startup(app):
    print("⏳ جاري فحص واسترجاع أحدث نسخة بيانات من مستودع data...")
    await github_restore_latest_db()
    init_db()
    await init_neural_json()
    init_fast_json()
    init_creative_json()
    asyncio.create_task(background_pruner())
    asyncio.create_task(global_queue_worker())
    asyncio.create_task(periodic_backup_task())  # تشغيل النسخ الاحتياطي التلقائي الدوري كل 7 دقائق
    print("🚀 البوت متصل بنظام الحفظ التلقائي لمستودع data ونظام المعالجة الهادئ!")

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(32)
        .post_init(on_startup)
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            STEP_ENGINE: [CallbackQueryHandler(select_engine, pattern="^eng_")],
            STEP_LANG: [
                CallbackQueryHandler(select_sub_engine, pattern="^subeng_"),
                CallbackQueryHandler(paginate_languages, pattern="^page_"),
                CallbackQueryHandler(noop, pattern="^noop$"),
                CallbackQueryHandler(select_language, pattern="^lang_"),
            ],
            STEP_DIALECT: [CallbackQueryHandler(select_dialect, pattern="^(dia_|back_to_lang)")],
            STEP_VOICE: [
                CallbackQueryHandler(paginate_voices, pattern="^vpage_"),
                CallbackQueryHandler(select_voice, pattern="^(voc_|back_to_lang)"),
                CallbackQueryHandler(noop, pattern="^noop$"),
            ],
            STEP_STORY_MENU: [CallbackQueryHandler(handle_story_menu_callbacks, pattern="^(story_|mlang_|btn_)")],
            STEP_MULTILANG_MENU: [CallbackQueryHandler(handle_story_menu_callbacks, pattern="^(story_|mlang_|btn_)")],
            STEP_WAIT_FX_AUDIO: [MessageHandler(filters.AUDIO | filters.VOICE, receive_fx_audio)],
            STEP_VOICE_CUSTOMIZE: [CallbackQueryHandler(handle_voice_tuning, pattern="^(tune_|t_|back_)")],
            STEP_SPEED: [CallbackQueryHandler(handle_speed_selection, pattern="^(spd_|pit_)")],
            STEP_FORMAT: [CallbackQueryHandler(select_format, pattern="^fmt_")],
            STEP_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, generate_audio),
                MessageHandler(filters.Document.ALL, handle_document),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CallbackQueryHandler(handle_callbacks, pattern="^(btn_recheck_sub|cfg_restart)"))
    app.add_handler(conv_handler)
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()