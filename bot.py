# -*- coding: utf-8 -*-
import os
import re
import json
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
    MessageHandler, ContextTypes, filters
)

try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ إعدادات البيئة ومستودع GitHub ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADM_TOKEN = os.environ.get("ADM", "").strip()

BACKUP_REPO_OWNER = "ahmedhassan2382006-art"
BACKUP_REPO_NAME = "data"
BACKUP_BRANCH = "main"

CHANNELS_TO_CHECK = [
    {"name": "قناة الشروحات التقنية 💡", "id": "@Basira_Tech", "link": "https://t.me/Basira_Tech"},
    {"name": "قناة مشاريع المطور 👨‍💻", "id": "@AhmedHassanProjects", "link": "https://t.me/AhmedHassanProjects"},
]

GROUP_CHAT_ID = -1003997349654
GROUP_INVITE_LINK = "https://t.me/+6e6e1QVB4z8xMGE0"

DB_FILE = "bot_database.db"

FILE_NEURAL_JSON = "neural_voices.json"
FILE_FAST_JSON = "fast_voices.json"
FILE_CREATIVE_JSON = "creative_voices.json"

MAX_PARALLEL_RENDERS = 2
GLOBAL_TTS_SEMAPHORE = None
GLOBAL_TASK_QUEUE = None
GLOBAL_HTTP_SESSION = None

# ============ قائمة الـ 30 لغة المعتمدة ============
LANGUAGES_30 = {
    "ar": {"ar": "العربية", "en": "Arabic"},
    "en": {"ar": "الإنجليزية", "en": "English"},
    "es": {"ar": "الإسبانية", "en": "Spanish"},
    "fr": {"ar": "الفرنسية", "en": "French"},
    "de": {"ar": "الألمانية", "en": "German"},
    "it": {"ar": "الإيطالية", "en": "Italian"},
    "pt": {"ar": "البرتغالية", "en": "Portuguese"},
    "ru": {"ar": "الروسية", "en": "Russian"},
    "zh": {"ar": "الصينية", "en": "Chinese"},
    "ja": {"ar": "اليابانية", "en": "Japanese"},
    "ko": {"ar": "الكورية", "en": "Korean"},
    "tr": {"ar": "التركية", "en": "Turkish"},
    "hi": {"ar": "الهندية", "en": "Hindi"},
    "ur": {"ar": "الأردية", "en": "Urdu"},
    "fa": {"ar": "الفارسية", "en": "Persian"},
    "id": {"ar": "الإندونيسية", "en": "Indonesian"},
    "nl": {"ar": "الهولندية", "en": "Dutch"},
    "pl": {"ar": "البولندية", "en": "Polish"},
    "sv": {"ar": "السويدية", "en": "Swedish"},
    "vi": {"ar": "الفيتنامية", "en": "Vietnamese"},
    "th": {"ar": "التايلاندية", "en": "Thai"},
    "el": {"ar": "اليونانية", "en": "Greek"},
    "he": {"ar": "العبرية", "en": "Hebrew"},
    "uk": {"ar": "الأوكرانية", "en": "Ukrainian"},
    "ro": {"ar": "الرومانية", "en": "Romanian"},
    "cs": {"ar": "التشيكية", "en": "Czech"},
    "hu": {"ar": "المجرية", "en": "Hungarian"},
    "ms": {"ar": "الملايو", "en": "Malay"},
    "tl": {"ar": "الفلبينية", "en": "Filipino"},
    "bn": {"ar": "البنغالية", "en": "Bengali"}
}

UI_TEXTS = {
    "ar": {
        "welcome_saved": "أهلاً بك مجدداً يا **{name}**! 🎙️🌸\n\n✅ **إعداداتك الحالية جاهزة ومحفوظة:**\n• الوضع: `{mode}`\n• الصيغة: `{fmt}`\n\n📝 **أرسل النص مباشرة الآن (أو ملف .txt / .docx / .srt) وسأقوم بتحويله فوراً!**\nأو اضغط أدناه لتعديل الإعدادات:",
        "welcome_new": "مرحباً بك يا **{name}** في استوديو هندسة الصوت الذكي! 🎙️",
        "menu_main_single": "⚙️ **لوحة التحكم السريعة (المحرك الفردي):**\nاضغط على أي عنصر لتعديله مباشرة ثم اضغط حفظ:",
        "menu_main_multi": "🌐 **لوحة تحكم المحرك متعدد اللغات الذكي:**\n• يتعرف تلقائياً على كل لغة بالنص ويخصص لها الصوت المناسب فوراً وبدون أي تأخير!\n• اضغط على أي صوت بالأسفل لتخصيصه:",
        "btn_engine": "⚙️ المحرك: {val}",
        "btn_lang": "🌐 اللغة: {val}",
        "btn_voice": "🗣️ الصوت: {val}",
        "btn_speed": "⚡ سرعة الصوت: {val}x",
        "btn_pitch": "🎵 نبرة/حدة الصوت: {val}",
        "btn_format": "📁 صيغة الملف: {val}",
        "btn_bot_lang": "🌍 لغة البوت: العربية 🇸🇦",
        "btn_save": "💾 حفظ الإعدادات والبدء ✅",
        "btn_open_settings": "⚙️ فتح لوحة الإعدادات",
        "btn_back": "🔙 رجوع",
        "btn_prev": "⬅️ السابق",
        "btn_next": "التالي ➡️",
        "saved_success": "✅ **تم حفظ إعداداتك بنجاح تام!**\n\n🎙️ أرسل الآن أي نص أو مستند وسأقوم بهندسته وتوليده فوراً وفق هذه الإعدادات.",
        "pick_engine_title": "⚙️ **اختر المحرك الصوتي المطلوب:**",
        "pick_lang_title": "🌐 **اختر اللغة المطلوبة (من أهم 30 لغة مدعومة):**",
        "pick_voice_title": "🗣️ **اختر الصوت المطلوب:**",
        "pick_speed_title": "⚡ **اختر مستوى سرعة الصوت:**",
        "pick_pitch_title": "🎵 **اختر حدة/نبرة الصوت المطلوبة:**",
        "pick_format_title": "📁 **اختر صيغة الملف المستخرج:**",
        "status_wait": "⏳ **جاري معالجة طلبك وهندسة الصوت...** 🌸",
        "status_rendering": "⏳ **بدأت معالجة وتوليد الصوت بأعلى جودة...**",
        "err_render": "⚠️ حدث خطأ أثناء التوليد، يرجى المحاولة لاحقاً.",
        "complete_audio": "🎬 **تم اكتمال توليد وهندسة الصوت بنجاح!**",
        "complete_voice": "✅ تم توليد الرسالة الصوتية بنجاح!",
        "unsupported_doc": "❌ صيغة غير مدعومة. المقبول: .txt, .docx, .srt",
        "sub_required": "أهلاً بك يا مبدع **{name}** في استوديو الصوت الذكي! 🎙️🌸\n\nيرجى التكرم بالانضمام إلى قنواتنا ومجموعتنا أدناه لتفعيل البوت مجاناً:\n",
        "btn_recheck": "🔄 تم الاشتراك، تفعيل البوت الآن ✅",
        "sub_verified": "🎉 **تم التحقق بنجاح!** أرسل /start لتبدأ فوراً."
    },
    "en": {
        "welcome_saved": "Welcome back, **{name}**! 🎙️🌸\n\n✅ **Your settings are saved & ready:**\n• Mode: `{mode}`\n• Format: `{fmt}`\n\n📝 **Send your text or document now (.txt / .docx / .srt) to generate audio instantly!**\nOr click below to customize settings:",
        "welcome_new": "Welcome **{name}** to the Smart Audio Engineering Studio! 🎙️",
        "menu_main_single": "⚙️ **Quick Settings Dashboard (Single Engine):**\nClick any item to customize it, then click Save:",
        "menu_main_multi": "🌐 **Smart Multilingual Engine Dashboard:**\n• Automatically detects text languages and assigns matching voices seamlessly without delays!\n• Click on any voice below to customize it:",
        "btn_engine": "⚙️ Engine: {val}",
        "btn_lang": "🌐 Language: {val}",
        "btn_voice": "🗣️ Voice: {val}",
        "btn_speed": "⚡ Speed: {val}x",
        "btn_pitch": "🎵 Pitch: {val}",
        "btn_format": "📁 Format: {val}",
        "btn_bot_lang": "🌍 Bot Language: English 🇬🇧",
        "btn_save": "💾 Save Settings & Start ✅",
        "btn_open_settings": "⚙️ Open Settings",
        "btn_back": "🔙 Back",
        "btn_prev": "⬅️ Previous",
        "btn_next": "Next ➡️",
        "saved_success": "✅ **Settings saved successfully!**\n\n🎙️ Send any text or document now, and I will generate your audio directly using these settings.",
        "pick_engine_title": "⚙️ **Select the desired audio engine:**",
        "pick_lang_title": "🌐 **Select language (from the top 30 supported languages):**",
        "pick_voice_title": "🗣️ **Select the voice:**",
        "pick_speed_title": "⚡ **Select speech speed level:**",
        "pick_pitch_title": "🎵 **Select voice pitch level:**",
        "pick_format_title": "📁 **Select output file format:**",
        "status_wait": "⏳ **Processing your request and synthesizing audio...** 🌸",
        "status_rendering": "⏳ **Generating high quality audio now...**",
        "err_render": "⚠️ An error occurred during generation. Please try again later.",
        "complete_audio": "🎬 **Audio synthesized & mastered successfully!**",
        "complete_voice": "✅ Voice note generated successfully!",
        "unsupported_doc": "❌ Unsupported format. Accepted: .txt, .docx, .srt",
        "sub_required": "Welcome **{name}** to the Smart Audio Studio! 🎙️🌸\n\nPlease join our official channels and group below to activate the bot for free:\n",
        "btn_recheck": "🔄 Joined, Activate Bot Now ✅",
        "sub_verified": "🎉 **Verified successfully!** Send /start to begin."
    }
}

def t(key: str, lang: str = "ar", **kwargs) -> str:
    lang = lang if lang in UI_TEXTS else "ar"
    msg = UI_TEXTS[lang].get(key, UI_TEXTS["ar"].get(key, key))
    if kwargs:
        return msg.format(**kwargs)
    return msg

SPEED_LEVELS = [0.5, 0.75, 0.9, 1.0, 1.25, 1.5, 2.0]
PITCH_LEVELS = [
    {"id": "whisper", "name_ar": "🤫 همس", "name_en": "🤫 Whisper"},
    {"id": "low", "name_ar": "📉 منخفض", "name_en": "📉 Low"},
    {"id": "normal", "name_ar": "🎵 عادي", "name_en": "🎵 Normal"},
    {"id": "high", "name_ar": "📈 مرتفع", "name_en": "📈 High"},
    {"id": "sharp", "name_ar": "⚡ حاد", "name_en": "⚡ Sharp"}
]

ENGINE_NAMES = {
    "neural": {"ar": "المحرك العصبي (Studio)", "en": "Neural Engine (Studio)"},
    "fast": {"ar": "المحرك السريع (Google)", "en": "Fast Engine (Google)"},
    "creative": {"ar": "المحرك التعبيري (TikTok)", "en": "Creative Engine (TikTok)"},
    "multilang": {"ar": "متعدد اللغات الذكي 🌐", "en": "Smart Multilingual 🌐"}
}

DEFAULT_CONFIG = {
    "bot_lang": "ar",
    "mode": "single",
    "engine": "neural",
    "lang": "ar",
    "voice": "ar-EG-SalmaNeural",
    "voice_name": "Salma (👩)",
    "speed": 1.0,
    "pitch": "normal",
    "audio_format": "mp3",
    "settings_saved": False,
    "multilang_slots": [
        {"engine": "neural", "lang": "ar", "voice": "ar-EG-SalmaNeural", "voice_name": "Salma (🇪🇬)", "speed": 1.0, "pitch": "normal"},
        {"engine": "creative", "lang": "en", "voice": "en_us_006", "voice_name": "Joey (🇺🇸)", "speed": 1.0, "pitch": "normal"},
        {"engine": "neural", "lang": "zh", "voice": "zh-CN-XiaoxiaoNeural", "voice_name": "Xiaoxiao (🇨🇳)", "speed": 1.0, "pitch": "normal"}
    ]
}

# ============ نظام النسخ الاحتياطي التلقائي لمستودع data عبر GitHub API ============
async def github_api_request(method: str, endpoint: str, payload: dict = None, accept_header: str = None):
    if not ADM_TOKEN:
        logger.error("❌ توكن ADM غير موجود في بيئة التشغيل! لن يتم الحفظ في جيت هب.")
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
                    logger.warning(f"GitHub API [{method} {endpoint}] ({resp.status}): {err_txt}")
                    return None
        except Exception as e:
            logger.error(f"GitHub connection error: {e}")
            return None

async def github_restore_latest_db():
    if not ADM_TOKEN:
        logger.warning("⚠️ سكرت ADM غير موجود، سيتم التخزين محلياً فقط.")
        return

    logger.info(f"🔍 فحص مستودع {BACKUP_REPO_OWNER}/{BACKUP_REPO_NAME} (الفرع {BACKUP_BRANCH}) لاسترجاع أحدث بيانات...")
    contents = await github_api_request("GET", f"repos/{BACKUP_REPO_OWNER}/{BACKUP_REPO_NAME}/contents/?ref={BACKUP_BRANCH}")
    
    if isinstance(contents, list):
        db_files = [f for f in contents if f.get("name", "").startswith("backup_") and f.get("name", "").endswith(".db")]
        if db_files:
            db_files.sort(key=lambda x: x["name"])
            latest = db_files[-1]
            file_name = latest["name"]
            logger.info(f"📥 جاري استرجاع أحدث نسخة بيانات: {file_name}...")
            
            raw_data = await github_api_request(
                "GET", 
                f"repos/{BACKUP_REPO_OWNER}/{BACKUP_REPO_NAME}/contents/{file_name}?ref={BACKUP_BRANCH}",
                accept_header="application/vnd.github.raw+json"
            )
            if raw_data and isinstance(raw_data, (bytes, bytearray)):
                with open(DB_FILE, "wb") as f:
                    f.write(raw_data)
                logger.info(f"✅ تم بنجاح تثبيت أحدث قاعدة بيانات من مستودع data: ({file_name})!")
                return
            else:
                file_info = await github_api_request("GET", f"repos/{BACKUP_REPO_OWNER}/{BACKUP_REPO_NAME}/contents/{file_name}?ref={BACKUP_BRANCH}")
                if file_info and isinstance(file_info, dict) and "content" in file_info:
                    decoded = base64.b64decode(file_info["content"])
                    with open(DB_FILE, "wb") as f:
                        f.write(decoded)
                    logger.info(f"✅ تم بنجاح فك تشفير وتثبيت قاعدة البيانات: ({file_name})!")
                    return
    logger.info("ℹ️ مستودع data لا يحتوي بعد على نسخ سابقة، سيتم إنشاء قاعدة بيانات جديدة.")

async def github_upload_backup():
    if not ADM_TOKEN:
        logger.error("❌ توكن ADM فارغ! لا يمكن رفع النسخة الاحتياطية.")
        return
    if not os.path.exists(DB_FILE):
        return

    db_prune_inactive(days=10)
    now_dt = datetime.utcnow()
    timestamp = now_dt.strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"backup_{timestamp}.db"

    try:
        with open(DB_FILE, "rb") as f:
            file_bytes = f.read()
        b64_content = base64.b64encode(file_bytes).decode("utf-8")

        # 1. رفع النسخة المؤرخة
        payload = {
            "message": f"Auto-backup at {timestamp} [skip ci]",
            "content": b64_content,
            "branch": BACKUP_BRANCH
        }
        endpoint = f"repos/{BACKUP_REPO_OWNER}/{BACKUP_REPO_NAME}/contents/{file_name}"
        res = await github_api_request("PUT", endpoint, payload)
        
        # 2. تحديث النسخة الدائمة bot_database.db
        curr_main = await github_api_request("GET", f"repos/{BACKUP_REPO_OWNER}/{BACKUP_REPO_NAME}/contents/bot_database.db?ref={BACKUP_BRANCH}")
        main_payload = {
            "message": f"Update bot_database.db [skip ci]",
            "content": b64_content,
            "branch": BACKUP_BRANCH
        }
        if curr_main and isinstance(curr_main, dict) and "sha" in curr_main:
            main_payload["sha"] = curr_main["sha"]
        await github_api_request("PUT", f"repos/{BACKUP_REPO_OWNER}/{BACKUP_REPO_NAME}/contents/bot_database.db", main_payload)

        if res and isinstance(res, dict) and "content" in res:
            logger.info(f"💾 [GitHub Backup Success] تم بنجاح رفع النسخة الاحتياطية إلى مستودع {BACKUP_REPO_NAME}: {file_name}")
        else:
            logger.warning(f"⚠️ فشل رفع النسخة الاحتياطية إلى مستودع {BACKUP_REPO_NAME}")
    except Exception as e:
        logger.error(f"خطأ أثناء قراءة أو رفع النسخة الاحتياطية: {e}")

async def periodic_backup_task():
    # رفع فوري بعد 30 ثانية للتأكد من الاتصال التام
    await asyncio.sleep(30)
    try:
        await github_upload_backup()
    except Exception as e:
        logger.error(f"خطأ أثناء مهمة النسخ الاحتياطي الأولى: {e}")

    while True:
        await asyncio.sleep(420)  # كل 7 دقائق = 420 ثانية
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
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if row and row[0]:
        try:
            saved_dict = json.loads(row[0])
            merged.update(saved_dict)
        except Exception:
            pass
    return merged

def db_prune_inactive(days: int = 10):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        threshold = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cur.execute("DELETE FROM users WHERE last_active < ?", (threshold,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"خطأ تنظيف الحسابات: {e}")

# ============ فحص الاشتراك الإجباري ============
async def check_user_subscription(bot, user_id: int) -> tuple[bool, list]:
    missing = []
    for ch in CHANNELS_TO_CHECK:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status in [ChatMember.LEFT, ChatMember.BANNED]:
                missing.append(ch)
            elif member.status not in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.RESTRICTED]:
                missing.append(ch)
        except Exception as e:
            logger.error(f"تعذر فحص القناة {ch['id']}: {e}")
            missing.append(ch)

    try:
        g_member = await bot.get_chat_member(chat_id=GROUP_CHAT_ID, user_id=user_id)
        if g_member.status in [ChatMember.LEFT, ChatMember.BANNED]:
            missing.append({"name": "مجموعة النقاش والتعليقات 💬", "link": GROUP_INVITE_LINK})
        elif g_member.status not in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.RESTRICTED]:
            missing.append({"name": "مجموعة النقاش والتعليقات 💬", "link": GROUP_INVITE_LINK})
    except Exception as e:
        logger.error(f"تعذر فحص المجموعة {GROUP_CHAT_ID}: {e}")
        missing.append({"name": "مجموعة النقاش والتعليقات 💬", "link": GROUP_INVITE_LINK})

    return (len(missing) == 0, missing)

async def gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    db_touch_user(str(user.id))
    ok, missing = await check_user_subscription(context.bot, user.id)
    if not ok:
        blang = context.user_data.get("bot_lang", "ar")
        keyboard = []
        for item in missing:
            keyboard.append([InlineKeyboardButton(f"✨ {item['name']}", url=item["link"])])
        keyboard.append([InlineKeyboardButton(t("btn_recheck", blang), callback_data="btn_recheck_sub")])
        nice_msg = t("sub_required", blang, name=user.first_name)
        if update.callback_query:
            try:
                await update.callback_query.message.edit_text(nice_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            except Exception:
                await update.callback_query.message.reply_text(nice_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(nice_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return False
    return True

# ============ بيانات اللغات وتصفية الـ 30 لغة ============
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
        if lc not in LANGUAGES_30:
            continue
        if lc not in db:
            db[lc] = {"dialects": {}}
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
    db = {}
    for code in LANGUAGES_30.keys():
        if code in available or code == "zh":
            g_code = "zh-CN" if code == "zh" else code
            db[code] = {"code": g_code}
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
        "en": {"voices": [
            {"id": "en_us_001", "name": "Narrator (Female)"},
            {"id": "en_us_006", "name": "Joey (Male)"},
            {"id": "en_us_007", "name": "Professor (Male)"},
            {"id": "en_male_narration", "name": "Storyteller (Male)"},
            {"id": "en_male_funny", "name": "Wacky (Male)"},
            {"id": "en_female_emotional", "name": "Peaceful (Female)"},
        ]},
        "fr": {"voices": [
            {"id": "fr_001", "name": "Voix 1 (Male)"},
            {"id": "fr_002", "name": "Voix 2 (Male)"},
        ]},
        "es": {"voices": [
            {"id": "es_001", "name": "Voz 1 (Male)"},
            {"id": "es_mx_002", "name": "Voz Mexicana (Male)"},
        ]},
        "de": {"voices": [
            {"id": "de_001", "name": "Stimme 1 (Female)"},
            {"id": "de_002", "name": "Stimme 2 (Male)"},
        ]},
        "ja": {"voices": [
            {"id": "jp_001", "name": "女性 1 (Female)"},
            {"id": "jp_005", "name": "男性 (Male)"},
        ]},
        "id": {"voices": [
            {"id": "id_001", "name": "Suara 1 (Female)"}
        ]},
        "pt": {"voices": [
            {"id": "br_005", "name": "Voz 1 (Male)"}
        ]}
    }
    with open(FILE_CREATIVE_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    DB_CREATIVE = db

def get_engine_supported_langs(engine: str) -> list:
    if engine == "creative":
        return [l for l in LANGUAGES_30.keys() if l in DB_CREATIVE]
    elif engine == "fast":
        return [l for l in LANGUAGES_30.keys() if l in DB_FAST]
    return [l for l in LANGUAGES_30.keys() if l in DB_NEURAL]

# ============ اقتصاص الصمت لمنع السكتات ============
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

async def trim_silence(input_file: str, output_file: str) -> bool:
    """إزالة الصمت الهامشي بالكامل لمنع السكتات والتأخير بين الأصوات"""
    filter_chain = (
        "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-45dB:detection=peak,"
        "areverse,"
        "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-45dB:detection=peak,"
        "areverse"
    )
    ok = await run_ffmpeg([
        "-i", input_file, "-af", filter_chain,
        "-c:a", "libmp3lame", "-q:a", "2", "-y", output_file
    ])
    if ok and os.path.exists(output_file) and os.path.getsize(output_file) > 100:
        return True
    shutil.copy(input_file, output_file)
    return True

async def apply_audio_filters(input_file: str, output_file: str, speed: float = 1.0) -> bool:
    filters_list = []
    if abs(speed - 1.0) >= 0.01:
        rem = speed
        while rem > 2.0: filters_list.append("atempo=2.0"); rem /= 2.0
        while rem < 0.5: filters_list.append("atempo=0.5"); rem *= 2.0
        filters_list.append(f"atempo={rem}")
    if not filters_list:
        shutil.copy(input_file, output_file)
        return True
    return await run_ffmpeg([
        "-i", input_file, "-filter:a", ",".join(filters_list),
        "-c:a", "libmp3lame", "-q:a", "2", "-y", output_file
    ])

# ============ توليد أصوات TTS ============
async def synthesize_chunk(text: str, engine: str, voice: str, out_file: str,
                           speed: float = 1.0, pitch: str = "normal") -> bool:
    clean_text = text.strip()
    if not clean_text:
        return False

    if engine == "neural":
        rate_str = f"{int(round((speed - 1.0) * 100)):+d}%"
        pitch_map = {
            "whisper": "-40Hz",
            "low": "-20Hz",
            "normal": "+0Hz",
            "high": "+20Hz",
            "sharp": "+40Hz"
        }
        vol_str = "-25%" if pitch == "whisper" else "+0%"
        comm = edge_tts.Communicate(
            text=clean_text, voice=voice, rate=rate_str,
            pitch=pitch_map.get(pitch, "+0Hz"), volume=vol_str
        )
        await comm.save(out_file)
        return True
    elif engine == "fast":
        tts_lang = "zh-CN" if voice == "zh" else voice
        tts = gTTS(text=clean_text, lang=tts_lang)
        await asyncio.to_thread(tts.save, out_file)
        if abs(speed - 1.0) > 0.01:
            tmp = out_file + ".f.mp3"
            if await apply_audio_filters(out_file, tmp, speed):
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
                if abs(speed - 1.0) > 0.01:
                    tmp = out_file + ".f.mp3"
                    if await apply_audio_filters(out_file, tmp, speed):
                        os.replace(tmp, out_file)
                return True
        return False

# ============ كاشف النصوص التلقائي ============
def detect_token_script(token: str) -> str:
    ar_count = sum(1 for c in token if (0x0600 <= ord(c) <= 0x06FF) or (0x0750 <= ord(c) <= 0x077F) or (0xFB50 <= ord(c) <= 0xFDFF) or (0xFE70 <= ord(c) <= 0xFEFF))
    zh_count = sum(1 for c in token if (0x4E00 <= ord(c) <= 0x9FFF) or (0x3400 <= ord(c) <= 0x4DBF) or (0xF900 <= ord(c) <= 0xFAFF))
    en_count = sum(1 for c in token if ('a' <= c <= 'z') or ('A' <= c <= 'Z'))

    if ar_count >= zh_count and ar_count >= en_count and ar_count > 0:
        return "ar"
    if zh_count >= ar_count and zh_count >= en_count and zh_count > 0:
        return "zh"
    if en_count > 0:
        return "en"
    return "neutral"

def segment_multilingual_text(text: str) -> list[tuple[str, str]]:
    tokens = re.findall(r'[\u4e00-\u9fff]|[^\s\u4e00-\u9fff]+|\s+', text)
    segments = []
    current_lang = "ar"
    current_chunk = []

    for token in tokens:
        if token.isspace():
            if current_chunk:
                current_chunk.append(token)
            continue

        script = detect_token_script(token)
        if script == "neutral":
            current_chunk.append(token)
        else:
            if script != current_lang and current_chunk:
                chunk_str = "".join(current_chunk).strip()
                if chunk_str:
                    segments.append((current_lang, chunk_str))
                current_chunk = [token]
                current_lang = script
            else:
                current_lang = script
                current_chunk.append(token)

    if current_chunk:
        chunk_str = "".join(current_chunk).strip()
        if chunk_str:
            segments.append((current_lang, chunk_str))

    return segments

def get_slot_for_lang(lang_code: str, slots: list) -> dict:
    for s in slots:
        if s.get("lang") == lang_code:
            return s
    for s in slots:
        if s.get("lang", "").startswith(lang_code):
            return s
    if lang_code == "ar" and len(slots) > 0: return slots[0]
    if lang_code == "en" and len(slots) > 1: return slots[1]
    if lang_code == "zh" and len(slots) > 2: return slots[2]
    return slots[0]

# ============ بناء لوحة الإعدادات المبسطة باللغتين ============
def render_settings_keyboard(cfg: dict, active_slot_idx: int = None):
    blang = cfg.get("bot_lang", "ar")
    mode = cfg.get("mode", "single")
    keyboard = []

    if active_slot_idx is not None:
        s = cfg["multilang_slots"][active_slot_idx]
        eng_title = ENGINE_NAMES.get(s["engine"], {}).get(blang, s["engine"])
        p_obj = next((p for p in PITCH_LEVELS if p["id"] == s.get("pitch", "normal")), PITCH_LEVELS[2])
        pitch_name = p_obj[f"name_{blang}"]
        l_name = LANGUAGES_30.get(s.get("lang"), {}).get(blang, s.get("lang"))

        keyboard.append([InlineKeyboardButton(t("btn_engine", blang, val=eng_title), callback_data=f"sub_eng_{active_slot_idx}")])
        keyboard.append([InlineKeyboardButton(t("btn_lang", blang, val=l_name), callback_data=f"sub_lang_{active_slot_idx}")])
        keyboard.append([InlineKeyboardButton(t("btn_voice", blang, val=s.get("voice_name", s.get("voice"))), callback_data=f"sub_voc_{active_slot_idx}")])
        keyboard.append([InlineKeyboardButton(t("btn_speed", blang, val=s.get("speed", 1.0)), callback_data=f"sub_spd_{active_slot_idx}")])
        keyboard.append([InlineKeyboardButton(t("btn_pitch", blang, val=pitch_name), callback_data=f"sub_pit_{active_slot_idx}")])
        keyboard.append([InlineKeyboardButton(t("btn_back", blang), callback_data="menu_main")])
        return InlineKeyboardMarkup(keyboard)

    if mode == "multilang":
        keyboard.append([InlineKeyboardButton(t("btn_engine", blang, val=ENGINE_NAMES["multilang"][blang]), callback_data="pick_engine")])
        slots = cfg.get("multilang_slots", DEFAULT_CONFIG["multilang_slots"])
        for i, s in enumerate(slots):
            l_name = LANGUAGES_30.get(s.get("lang"), {}).get(blang, s.get("lang").upper())
            title = f"🎙️ {l_name}: {s.get('voice_name', s.get('voice'))}"
            keyboard.append([InlineKeyboardButton(title, callback_data=f"edit_slot_{i}")])
        fmt = cfg.get("audio_format", "mp3").upper()
        keyboard.append([InlineKeyboardButton(t("btn_format", blang, val=fmt), callback_data="pick_format")])
        keyboard.append([InlineKeyboardButton(t("btn_bot_lang", blang), callback_data="toggle_bot_lang")])
        keyboard.append([InlineKeyboardButton(t("btn_save", blang), callback_data="btn_save_config")])
    else:
        eng_title = ENGINE_NAMES.get(cfg.get("engine", "neural"), {}).get(blang, "Neural")
        p_obj = next((p for p in PITCH_LEVELS if p["id"] == cfg.get("pitch", "normal")), PITCH_LEVELS[2])
        pitch_name = p_obj[f"name_{blang}"]
        l_name = LANGUAGES_30.get(cfg.get("lang", "ar"), {}).get(blang, cfg.get("lang", "ar"))

        keyboard.append([InlineKeyboardButton(t("btn_engine", blang, val=eng_title), callback_data="pick_engine")])
        keyboard.append([InlineKeyboardButton(t("btn_lang", blang, val=l_name), callback_data="pick_lang")])
        keyboard.append([InlineKeyboardButton(t("btn_voice", blang, val=cfg.get("voice_name", cfg.get("voice"))), callback_data="pick_voice")])
        keyboard.append([InlineKeyboardButton(t("btn_speed", blang, val=cfg.get("speed", 1.0)), callback_data="pick_speed")])
        keyboard.append([InlineKeyboardButton(t("btn_pitch", blang, val=pitch_name), callback_data="pick_pitch")])
        keyboard.append([InlineKeyboardButton(t("btn_format", blang, val=cfg.get("audio_format", "mp3").upper()), callback_data="pick_format")])
        keyboard.append([InlineKeyboardButton(t("btn_bot_lang", blang), callback_data="toggle_bot_lang")])
        keyboard.append([InlineKeyboardButton(t("btn_save", blang), callback_data="btn_save_config")])

    return InlineKeyboardMarkup(keyboard)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, slot_idx: int = None, text_override: str = None):
    cfg = context.user_data
    blang = cfg.get("bot_lang", "ar")
    mode = cfg.get("mode", "single")
    markup = render_settings_keyboard(cfg, slot_idx)

    if slot_idx is not None:
        title = f"🎛️ **Voice {slot_idx+1} Customization:**" if blang == "en" else f"🎛️ **تخصيص إعدادات الصوت رقم ({slot_idx+1}):**"
    else:
        title = t("menu_main_multi", blang) if mode == "multilang" else t("menu_main_single", blang)

    if text_override:
        title = f"{text_override}\n\n{title}"

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(title, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await update.callback_query.message.reply_text(title, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(title, reply_markup=markup, parse_mode="Markdown")

# ============ أوامر البوت ============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    cfg = db_get_config(user_id)

    if not cfg.get("settings_saved", False):
        tg_lang = (update.effective_user.language_code or "ar").lower()
        cfg["bot_lang"] = "ar" if tg_lang.startswith("ar") else "en"

    context.user_data.update(cfg)
    blang = cfg.get("bot_lang", "ar")

    if not await gatekeeper(update, context):
        return

    first_name = update.effective_user.first_name or "صديقي"

    if cfg.get("settings_saved", False):
        mode_desc = ENGINE_NAMES["multilang"][blang] if cfg.get("mode") == "multilang" else f"{cfg.get('voice_name')} ({cfg.get('speed')}x)"
        msg = t("welcome_saved", blang, name=first_name, mode=mode_desc, fmt=cfg.get("audio_format", "mp3").upper())
        btn = [[InlineKeyboardButton(t("btn_open_settings", blang), callback_data="menu_main")]]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
    else:
        await show_main_menu(update, context, text_override=t("welcome_new", blang, name=first_name))

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    cfg = db_get_config(user_id)
    context.user_data.update(cfg)
    if not await gatekeeper(update, context):
        return
    await show_main_menu(update, context)

# ============ معالجة اختيارات الأزرار والقوائم ============
async def handle_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    cfg = context.user_data
    blang = cfg.get("bot_lang", "ar")
    user_id = str(update.effective_user.id)

    if data == "btn_recheck_sub":
        ok, _ = await check_user_subscription(context.bot, update.effective_user.id)
        if ok:
            await query.edit_message_text(t("sub_verified", blang), parse_mode="Markdown")
        else:
            await gatekeeper(update, context)
        return

    if data == "toggle_bot_lang":
        cfg["bot_lang"] = "en" if blang == "ar" else "ar"
        db_touch_user(user_id, cfg)
        await show_main_menu(update, context)
        return

    if data == "menu_main":
        context.user_data.pop("active_slot", None)
        await show_main_menu(update, context)
        return

    if data == "btn_save_config":
        cfg["settings_saved"] = True
        db_touch_user(user_id, cfg)
        await query.edit_message_text(
            t("saved_success", blang),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_open_settings", blang), callback_data="menu_main")]]),
            parse_mode="Markdown"
        )
        return

    if data.startswith("edit_slot_"):
        idx = int(data.replace("edit_slot_", ""))
        context.user_data["active_slot"] = idx
        await show_main_menu(update, context, slot_idx=idx)
        return

    if data == "pick_engine":
        btn = [
            [InlineKeyboardButton(ENGINE_NAMES["neural"][blang], callback_data="set_eng_neural")],
            [InlineKeyboardButton(ENGINE_NAMES["fast"][blang], callback_data="set_eng_fast")],
            [InlineKeyboardButton(ENGINE_NAMES["creative"][blang], callback_data="set_eng_creative")],
            [InlineKeyboardButton(ENGINE_NAMES["multilang"][blang], callback_data="set_eng_multilang")],
            [InlineKeyboardButton(t("btn_back", blang), callback_data="menu_main")]
        ]
        await query.edit_message_text(t("pick_engine_title", blang), reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
        return

    if data.startswith("set_eng_"):
        chosen = data.replace("set_eng_", "")
        if chosen == "multilang":
            cfg["mode"] = "multilang"
        else:
            cfg["mode"] = "single"
            cfg["engine"] = chosen
        db_touch_user(user_id, cfg)
        await show_main_menu(update, context)
        return

    # اختيار السرعة
    if data in ("pick_speed", "sub_spd_0", "sub_spd_1", "sub_spd_2"):
        slot_idx = int(data[-1]) if data.startswith("sub_spd_") else None
        current_spd = cfg["multilang_slots"][slot_idx]["speed"] if slot_idx is not None else cfg.get("speed", 1.0)
        btn, row = [], []
        for s in SPEED_LEVELS:
            mark = "✅ " if abs(s - current_spd) < 0.05 else ""
            cb = f"do_spd_{s}_{slot_idx if slot_idx is not None else 'x'}"
            row.append(InlineKeyboardButton(f"{mark}{s}x", callback_data=cb))
            if len(row) == 3:
                btn.append(row); row = []
        if row: btn.append(row)
        back_cb = f"edit_slot_{slot_idx}" if slot_idx is not None else "menu_main"
        btn.append([InlineKeyboardButton(t("btn_back", blang), callback_data=back_cb)])
        await query.edit_message_text(t("pick_speed_title", blang), reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
        return

    if data.startswith("do_spd_"):
        parts = data.split("_")
        spd_val = float(parts[2])
        slot_str = parts[3]
        if slot_str != "x":
            s_idx = int(slot_str)
            cfg["multilang_slots"][s_idx]["speed"] = spd_val
            db_touch_user(user_id, cfg)
            await show_main_menu(update, context, slot_idx=s_idx)
        else:
            cfg["speed"] = spd_val
            db_touch_user(user_id, cfg)
            await show_main_menu(update, context)
        return

    # اختيار النبرة
    if data in ("pick_pitch", "sub_pit_0", "sub_pit_1", "sub_pit_2"):
        slot_idx = int(data[-1]) if data.startswith("sub_pit_") else None
        current_pit = cfg["multilang_slots"][slot_idx].get("pitch", "normal") if slot_idx is not None else cfg.get("pitch", "normal")
        btn = []
        for p in PITCH_LEVELS:
            mark = "✅ " if p["id"] == current_pit else ""
            cb = f"do_pit_{p['id']}_{slot_idx if slot_idx is not None else 'x'}"
            btn.append([InlineKeyboardButton(f"{mark}{p[f'name_{blang}']}", callback_data=cb)])
        back_cb = f"edit_slot_{slot_idx}" if slot_idx is not None else "menu_main"
        btn.append([InlineKeyboardButton(t("btn_back", blang), callback_data=back_cb)])
        await query.edit_message_text(t("pick_pitch_title", blang), reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
        return

    if data.startswith("do_pit_"):
        parts = data.split("_")
        pit_id = parts[2]
        slot_str = parts[3]
        if slot_str != "x":
            s_idx = int(slot_str)
            cfg["multilang_slots"][s_idx]["pitch"] = pit_id
            db_touch_user(user_id, cfg)
            await show_main_menu(update, context, slot_idx=s_idx)
        else:
            cfg["pitch"] = pit_id
            db_touch_user(user_id, cfg)
            await show_main_menu(update, context)
        return

    # اختيار الصيغة
    if data == "pick_format":
        btn = [
            [InlineKeyboardButton("MP3 (Universal - Standard)", callback_data="do_fmt_mp3")],
            [InlineKeyboardButton("WAV (Studio - Lossless)", callback_data="do_fmt_wav")],
            [InlineKeyboardButton("OGG (Telegram Voice Note)", callback_data="do_fmt_ogg")],
            [InlineKeyboardButton(t("btn_back", blang), callback_data="menu_main")]
        ]
        await query.edit_message_text(t("pick_format_title", blang), reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
        return

    if data.startswith("do_fmt_"):
        fmt = data.replace("do_fmt_", "")
        cfg["audio_format"] = fmt
        db_touch_user(user_id, cfg)
        await show_main_menu(update, context)
        return

    # اختيار المحرك لصوت فرعي
    if data.startswith("sub_eng_"):
        idx = int(data.replace("sub_eng_", ""))
        btn = [
            [InlineKeyboardButton(ENGINE_NAMES["neural"][blang], callback_data=f"set_seng_neural_{idx}")],
            [InlineKeyboardButton(ENGINE_NAMES["fast"][blang], callback_data=f"set_seng_fast_{idx}")],
            [InlineKeyboardButton(ENGINE_NAMES["creative"][blang], callback_data=f"set_seng_creative_{idx}")],
            [InlineKeyboardButton(t("btn_back", blang), callback_data=f"edit_slot_{idx}")]
        ]
        await query.edit_message_text(t("pick_engine_title", blang), reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
        return

    if data.startswith("set_seng_"):
        parts = data.split("_")
        e_type = parts[2]
        s_idx = int(parts[3])
        cfg["multilang_slots"][s_idx]["engine"] = e_type
        supp = get_engine_supported_langs(e_type)
        if cfg["multilang_slots"][s_idx]["lang"] not in supp:
            cfg["multilang_slots"][s_idx]["lang"] = supp[0]
        db_touch_user(user_id, cfg)
        await show_main_menu(update, context, slot_idx=s_idx)
        return

    # اختيار اللغة من الـ 30 لغة
    if data in ("pick_lang", "sub_lang_0", "sub_lang_1", "sub_lang_2"):
        slot_idx = int(data[-1]) if data.startswith("sub_lang_") else None
        target_eng = cfg["multilang_slots"][slot_idx]["engine"] if slot_idx is not None else cfg.get("engine", "neural")
        supp_langs = get_engine_supported_langs(target_eng)
        await render_lang_page(update, supp_langs, page=0, slot_idx=slot_idx, blang=blang)
        return

    if data.startswith("lpg_"):
        parts = data.split("_")
        page = int(parts[1])
        slot_str = parts[2]
        s_idx = int(slot_str) if slot_str != "x" else None
        target_eng = cfg["multilang_slots"][s_idx]["engine"] if s_idx is not None else cfg.get("engine", "neural")
        supp_langs = get_engine_supported_langs(target_eng)
        await render_lang_page(update, supp_langs, page=page, slot_idx=s_idx, blang=blang)
        return

    if data.startswith("set_lang_"):
        parts = data.split("_")
        lang_code = parts[2]
        slot_str = parts[3]
        s_idx = int(slot_str) if slot_str != "x" else None
        target_eng = cfg["multilang_slots"][s_idx]["engine"] if s_idx is not None else cfg.get("engine", "neural")

        if target_eng == "neural":
            dialects = DB_NEURAL.get(lang_code, {}).get("dialects", {})
            btn, row = [], []
            for d_code, d_data in dialects.items():
                row.append(InlineKeyboardButton(d_data["name"], callback_data=f"set_dia_{lang_code}_{d_code}_{slot_str}"))
                if len(row) == 2: btn.append(row); row = []
            if row: btn.append(row)
            back_cb = f"edit_slot_{s_idx}" if s_idx is not None else "menu_main"
            btn.append([InlineKeyboardButton(t("btn_back", blang), callback_data=back_cb)])
            await query.edit_message_text(f"🔹 **{LANGUAGES_30[lang_code][blang]} - Dialects:**", reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
            return
        elif target_eng == "fast":
            v_name = LANGUAGES_30[lang_code][blang]
            if s_idx is not None:
                cfg["multilang_slots"][s_idx].update({"lang": lang_code, "voice": lang_code, "voice_name": v_name})
                await show_main_menu(update, context, slot_idx=s_idx)
            else:
                cfg.update({"lang": lang_code, "voice": lang_code, "voice_name": v_name})
                await show_main_menu(update, context)
            db_touch_user(user_id, cfg)
            return
        else:
            voices = DB_CREATIVE.get(lang_code, {}).get("voices", [])
            await render_voice_picker(update, voices, lang_code, slot_idx=s_idx, page=0, blang=blang)
            return

    if data.startswith("set_dia_"):
        parts = data.split("_")
        lang_code = parts[2]
        dia_code = parts[3]
        slot_str = parts[4]
        s_idx = int(slot_str) if slot_str != "x" else None
        voices = DB_NEURAL[lang_code]["dialects"][dia_code]["voices"]
        await render_voice_picker(update, voices, lang_code, slot_idx=s_idx, page=0, blang=blang)
        return

    # اختيار الصوت
    if data in ("pick_voice", "sub_voc_0", "sub_voc_1", "sub_voc_2"):
        slot_idx = int(data[-1]) if data.startswith("sub_voc_") else None
        s_target = cfg["multilang_slots"][slot_idx] if slot_idx is not None else cfg
        eng = s_target.get("engine", "neural")
        l_code = s_target.get("lang", "ar")

        if eng == "neural":
            voices = []
            for d in DB_NEURAL.get(l_code, {}).get("dialects", {}).values():
                voices.extend(d["voices"])
            await render_voice_picker(update, voices, l_code, slot_idx=slot_idx, page=0, blang=blang)
        elif eng == "creative":
            voices = DB_CREATIVE.get(l_code, {}).get("voices", [])
            await render_voice_picker(update, voices, l_code, slot_idx=slot_idx, page=0, blang=blang)
        else:
            await query.answer("Google Fast engine uses language code directly.")
        return

    if data.startswith("vpg_"):
        parts = data.split("_")
        page = int(parts[1])
        lang_code = parts[2]
        slot_str = parts[3]
        s_idx = int(slot_str) if slot_str != "x" else None
        target_eng = cfg["multilang_slots"][s_idx]["engine"] if s_idx is not None else cfg.get("engine", "neural")
        if target_eng == "neural":
            voices = [v for d in DB_NEURAL.get(lang_code, {}).get("dialects", {}).values() for v in d["voices"]]
        else:
            voices = DB_CREATIVE.get(lang_code, {}).get("voices", [])
        await render_voice_picker(update, voices, lang_code, slot_idx=s_idx, page=page, blang=blang)
        return

    if data.startswith("do_voc_"):
        parts = data.split("_")
        voc_id = parts[2]
        slot_str = parts[3]
        s_idx = int(slot_str) if slot_str != "x" else None

        voc_name = voc_id
        for db in [DB_NEURAL, DB_CREATIVE]:
            for l in db.values():
                for v in (l.get("voices", []) + [v for d in l.get("dialects", {}).values() for v in d["voices"]]):
                    if v["id"] == voc_id:
                        voc_name = v["name"]; break

        if s_idx is not None:
            cfg["multilang_slots"][s_idx]["voice"] = voc_id
            cfg["multilang_slots"][s_idx]["voice_name"] = voc_name
            db_touch_user(user_id, cfg)
            await show_main_menu(update, context, slot_idx=s_idx)
        else:
            cfg["voice"] = voc_id
            cfg["voice_name"] = voc_name
            db_touch_user(user_id, cfg)
            await show_main_menu(update, context)
        return

# دالة عرض الصفحات للغات
async def render_lang_page(update, supp_langs, page: int, slot_idx: int = None, blang: str = "ar"):
    page_size = 8
    total_pages = max(1, (len(supp_langs) + page_size - 1) // page_size)
    cur_langs = supp_langs[page * page_size: (page + 1) * page_size]
    s_tag = slot_idx if slot_idx is not None else "x"

    btn, row = [], []
    for l in cur_langs:
        l_title = LANGUAGES_30.get(l, {}).get(blang, l.upper())
        row.append(InlineKeyboardButton(l_title, callback_data=f"set_lang_{l}_{s_tag}"))
        if len(row) == 2: btn.append(row); row = []
    if row: btn.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(t("btn_prev", blang), callback_data=f"lpg_{page-1}_{s_tag}"))
    nav.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(t("btn_next", blang), callback_data=f"lpg_{page+1}_{s_tag}"))
    if nav: btn.append(nav)

    back_cb = f"edit_slot_{slot_idx}" if slot_idx is not None else "menu_main"
    btn.append([InlineKeyboardButton(t("btn_back", blang), callback_data=back_cb)])

    await update.callback_query.edit_message_text(t("pick_lang_title", blang), reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

# دالة عرض الصفحات للأصوات
async def render_voice_picker(update, voices, lang_code, slot_idx: int = None, page: int = 0, blang: str = "ar"):
    page_size = 8
    total_pages = max(1, (len(voices) + page_size - 1) // page_size)
    cur_v = voices[page * page_size: (page + 1) * page_size]
    s_tag = slot_idx if slot_idx is not None else "x"

    btn, row = [], []
    for v in cur_v:
        row.append(InlineKeyboardButton(v["name"], callback_data=f"do_voc_{v['id']}_{s_tag}"))
        if len(row) == 2: btn.append(row); row = []
    if row: btn.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(t("btn_prev", blang), callback_data=f"vpg_{page-1}_{lang_code}_{s_tag}"))
    nav.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(t("btn_next", blang), callback_data=f"vpg_{page+1}_{lang_code}_{s_tag}"))
    if nav: btn.append(nav)

    back_cb = f"edit_slot_{slot_idx}" if slot_idx is not None else "menu_main"
    btn.append([InlineKeyboardButton(t("btn_back", blang), callback_data=back_cb)])

    await update.callback_query.edit_message_text(t("pick_voice_title", blang), reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")

async def noop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer()
    except Exception: pass

# ============ طابور المعالجة وتوليد الصوت ============
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
        asyncio.create_task(process_task(sem, update, context, text, status_msg, q))

async def process_task(sem, update, context, text, status_msg, q):
    async with sem:
        blang = context.user_data.get("bot_lang", "ar")
        try:
            await status_msg.edit_text(t("status_rendering", blang), parse_mode="Markdown")
            await execute_render(update, context, text, status_msg)
        except Exception as e:
            logger.error(f"Render error: {e}", exc_info=True)
            try: await status_msg.edit_text(t("err_render", blang))
            except Exception: pass
        finally:
            q.task_done()

async def execute_render(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, status_msg):
    cfg = context.user_data
    blang = cfg.get("bot_lang", "ar")
    mode = cfg.get("mode", "single")
    audio_format = cfg.get("audio_format", "mp3")

    with tempfile.TemporaryDirectory() as tmpdir:
        chunks = []

        if mode == "multilang":
            slots = cfg.get("multilang_slots", DEFAULT_CONFIG["multilang_slots"])
            segments = segment_multilingual_text(user_text)

            for idx, (lang_code, chunk_text) in enumerate(segments):
                slot = get_slot_for_lang(lang_code, slots)
                raw_chunk = os.path.join(tmpdir, f"raw_{idx}.mp3")
                trimmed_chunk = os.path.join(tmpdir, f"ml_{idx}.mp3")

                ok = await synthesize_chunk(
                    chunk_text, slot["engine"], slot["voice"], raw_chunk,
                    speed=slot.get("speed", 1.0), pitch=slot.get("pitch", "normal")
                )
                if ok and os.path.exists(raw_chunk) and os.path.getsize(raw_chunk) > 0:
                    # إزالة الصمت لضمان عدم وجود أي سكتات بين اللغات
                    await trim_silence(raw_chunk, trimmed_chunk)
                    chunks.append(trimmed_chunk)
        else:
            engine = cfg.get("engine", "neural")
            voice = cfg.get("voice", "ar-EG-SalmaNeural")
            spd = cfg.get("speed", 1.0)
            pit = cfg.get("pitch", "normal")
            cf = os.path.join(tmpdir, "single.mp3")
            ok = await synthesize_chunk(user_text, engine, voice, cf, speed=spd, pitch=pit)
            if ok and os.path.exists(cf) and os.path.getsize(cf) > 0:
                chunks.append(cf)

        if not chunks:
            raise Exception("لم يتم توليد أي صوت.")

        list_file = os.path.join(tmpdir, "concat.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for c in chunks:
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
                await update.message.reply_voice(voice=af, caption=t("complete_voice", blang))
            else:
                await update.message.reply_audio(audio=af, title="Studio TTS Master", performer="Smart Studio", caption=t("complete_audio", blang))

        try: await status_msg.delete()
        except Exception: pass

# ============ استقبال النصوص والمستندات ============
async def handle_incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context): return
    txt = (update.message.text or "").strip()
    if txt:
        if not context.user_data.get("voice"):
            cfg = db_get_config(str(update.effective_user.id))
            context.user_data.update(cfg)
        blang = context.user_data.get("bot_lang", "ar")
        q = get_global_queue()
        status_msg = await update.message.reply_text(t("status_wait", blang), parse_mode="Markdown")
        await q.put((update, context, txt, status_msg))

async def handle_incoming_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gatekeeper(update, context): return
    doc = update.message.document
    if not doc: return
    blang = context.user_data.get("bot_lang", "ar")
    ext = os.path.splitext(doc.file_name or "f.txt")[1].lower()
    if ext not in (".txt", ".docx", ".srt"):
        await update.message.reply_text(t("unsupported_doc", blang))
        return

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
            if not context.user_data.get("voice"):
                cfg = db_get_config(str(update.effective_user.id))
                context.user_data.update(cfg)
            q = get_global_queue()
            status_msg = await update.message.reply_text(t("status_wait", blang), parse_mode="Markdown")
            await q.put((update, context, text, status_msg))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# تنظيف دوري للبيانات الخاملة كل 24 ساعة
async def background_pruner():
    while True:
        try: db_prune_inactive(days=10)
        except Exception: pass
        await asyncio.sleep(86400)

async def on_startup(app):
    print("=" * 50)
    if ADM_TOKEN:
        print(f"✅ تم تحميل توكن ADM بنجاح (طول التوكن: {len(ADM_TOKEN)} حرف)")
    else:
        print("❌ تحذير حرج: توكن ADM مفقود في المتغيرات! تأكد من تمريره في الـ Workflow.")
    print("=" * 50)

    print("⏳ استعادة أحدث نسخة بيانات من مستودع data (main)...")
    await github_restore_latest_db()
    init_db()
    await init_neural_json()
    init_fast_json()
    init_creative_json()
    asyncio.create_task(background_pruner())
    asyncio.create_task(global_queue_worker())
    asyncio.create_task(periodic_backup_task())  # رفع دوري كل 7 دقائق لمستودع data
    print("🚀 البوت يعمل بنظام الـ 30 لغة وقص السكتات والنسخ الاحتياطي الصارم!")

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(32)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CallbackQueryHandler(noop_cb, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(handle_callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_incoming_doc))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()