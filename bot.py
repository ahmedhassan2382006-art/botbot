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
import time
import aiohttp
from datetime import datetime
from gtts import gTTS
import edge_tts
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters,
)

# ============ مكتبات اختيارية (تعمل بتراجع آمن إذا غابت) ============
try:
    from langdetect import detect_langs, LangDetectException
    HAS_LANGDETECT = True
except ImportError:
    HAS_LANGDETECT = False

try:
    from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
    from sqlalchemy.orm import declarative_base, sessionmaker
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    try:
        from PyPDF2 import PdfReader
        HAS_PYPDF = True
    except ImportError:
        HAS_PYPDF = False

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

BOT_TOKEN = os.environ.get("BOT_TOKEN")

FILE_NEURAL_JSON = "neural_voices.json"
FILE_FAST_JSON = "fast_voices.json"
FILE_CREATIVE_JSON = "creative_voices.json"
BGM_FILE = "background_music.mp3"  # اختياري: ضع ملف موسيقى بهذا الاسم لتفعيل الخلفية

# ============ الحالات ============
(
    STEP_ENGINE, STEP_LANG, STEP_DIALECT, STEP_VOICE,
    STEP_STORY_MENU, STEP_MULTILANG_MENU, STEP_SPEED,
    STEP_FORMAT, STEP_TEXT,
) = range(9)

# ============ قواعد البيانات ============
DB_NEURAL = {}
DB_FAST = {}
DB_CREATIVE = {}

# ============ التحكم بالمعدل والأقفال ============
USER_LAST_REQUEST = {}
RATE_LIMIT_SECONDS = 5
USER_LOCKS = {}

if HAS_SQLALCHEMY:
    engine_db = create_engine("sqlite:///bot_data.db", connect_args={"check_same_thread": False})
    Base = declarative_base()

    class UserPreset(Base):
        __tablename__ = "user_presets"
        id = Column(Integer, primary_key=True, autoincrement=True)
        user_id = Column(String, index=True)
        name = Column(String)
        config_json = Column(Text)
        created_at = Column(DateTime, default=datetime.utcnow)

    class UserStats(Base):
        __tablename__ = "user_stats"
        user_id = Column(String, primary_key=True)
        request_count = Column(Integer, default=0)
        last_request = Column(DateTime, default=datetime.utcnow)

    Base.metadata.create_all(engine_db)
    SessionLocal = sessionmaker(bind=engine_db)

# ============ أسماء اللغات ============
NATIVE_LANG_NAMES = {
    "ar": "العربية", "en": "English", "tr": "Türkçe", "es": "Español",
    "fr": "Français", "de": "Deutsch", "it": "Italiano", "pt": "Português",
    "ru": "Русский", "zh": "中文", "ja": "日本語", "ko": "한국어",
    "hi": "हिन्दी", "ur": "اردو", "fa": "فارسی", "nl": "Nederlands",
    "pl": "Polski", "sv": "Svenska", "id": "Bahasa Indonesia", "el": "Ελληνικά",
    "uk": "Українська", "vi": "Tiếng Việt", "th": "ไทย", "ro": "Română",
    "cs": "Čeština", "hu": "Magyar"
}

I18N = {
    "ar": {
        "welcome": ("مرحباً بك في بوت تحويل النص إلى صوت الذكي!\n"
                    "المطور: **أحمد حسن عبد النبي** 👨‍💻\n\n"
                    "🔹 **الخطوة 1:** اختر المحرك الصوتي المطلوب:"),
        "step_engine": "🔹 **الخطوة 1:** اختر المحرك الصوتي:",
        "step_lang": "🔹 **الخطوة 2:** اختر اللغة:",
        "step_dialect": "🔹 **الخطوة 3:** اختر اللهجة / الدولة:",
        "step_voice": "🔹 **الخطوة 4:** اختر الصوت:",
        "step_speed": "🔹 **اضبط إعدادات الصوت** (السرعة والنبرة):",
        "step_format": "🔹 **الخطوة 5:** اختر صيغة الملف الصوتي:",
        "step_text": "🔹 **الخطوة 6:** أرسل النص (أو مستند .txt/.pdf/.docx/.srt):",
        "story_intro": "🎬 **محرك القصص والحوارات**\nاختر من 2 إلى 5 شخصيات.\nاختر المحرك الصوتي للشخصية الأولى (v1):",
        "story_slot_prompt": "🔹 اختر المحرك الصوتي للشخصية (v{num}):",
        "story_summary": "🎙️ **طاقم الحوار الحالي:**\nاضغط على أي شخصية أدناه إذا أردت **تعديلها**:",
        "story_btn_add": "➕ إضافة شخصية جديدة (v{num})",
        "story_btn_finish": "🎬 انتهاء الحوار (متابعة للصيغة والنص)",
        "story_btn_reset": "🔄 إعادة تعيين الشخصيات",
        "story_step_text": ("🔹 **أرسل نص سيناريو الحوار.**\n\n"
                            "💡 **طريقة الكتابة:** ضع رمز الشخصية `(vالرقم)` في بداية كلامها:\n\n"
                            "*(v1) مرحباً يا أحمد، هل أتممت كتابة التقرير؟*\n"
                            "*(v2) نعم يا سارة، انتهيت منه للتو وسأرسله لكِ الآن.*\n"
                            "*(v1) ممتاز جداً، أشكرك!*"),
        "multilang_intro": ("🌐 **محرك متعدد اللغات التلقائي**\n\n"
                            "يتعرف تلقائياً على لغات النص ويوزع كل لغة على صوتها الأصلي في ملف واحد!\n"
                            "⚠️ **الشرط:** (من 2 إلى 3 أصوات كحد أقصى) وكل صوت للغة مختلفة تماماً.\n\n"
                            "🔹 اختر المحرك الصوتي للغة الأولى:"),
        "multilang_slot_prompt": "🔹 اختر المحرك الصوتي للغة التالية:",
        "multilang_summary": "🌐 **اللغات والأصوات المختارة حالياً:**\nاضغط على أي صوت أدناه إذا أردت **تعديلها**:",
        "multilang_btn_add": "➕ إضافة لغة وصوت إضافي (متبقي {num})",
        "multilang_btn_finish": "➡️ اعتماد اللغات والانتقال للصيغة",
        "multilang_btn_reset": "🔄 إعادة ضبط اللغات",
        "multilang_step_text": ("🔹 **أرسل النص المختلط باللغات المتعددة الآن.**\n\n"
                                "سيتعرف البوت تلقائياً على كل فقرة وجملة ولغتها، وينطقها بالصوت المخصص لها ثم يدمج الكل في ملف صوتي واحد متناسق."),
        "generating": "⏳ **جاري تحليل لغات النص ومعالجة المقاطع الصوتية ودمجها بدقة...**",
        "done": "✅ تم إنشاء ودمج الملف الصوتي بنجاح!",
        "new_text_hint": "\n\n💡 أرسل نصاً جديداً بنفس الإعدادات أو أرسل /start للبدء من جديد.",
        "back": "🔙 رجوع",
        "eng_neural": "المحرك العصبي الفائق (جودة استوديو)",
        "eng_fast": "المحرك السريع القياسي (خفيف)",
        "eng_creative": "المحرك التعبيري الحديث (شخصيات)",
        "eng_story": "محرك القصص والحوارات (سيناريو 2-5 أصوات) 🎬",
        "eng_multilang": "محرك متعدد اللغات (نطق آلي للغات بنص واحد) 🌐",
        "error": "⚠️ حدث خطأ أثناء معالجة النص، تأكد من أن النص يحتوي على كلمات صالحة.",
        "rate_limit": "⚠️ الرجاء الانتظار {wait} ثانية قبل إرسال طلب جديد.",
        "busy": "⏳ لديك طلب قيد المعالجة حالياً، يرجى الانتظار.",
        "preset_saved": "✅ تم حفظ القالب: **{name}**",
        "preset_not_found": "❌ لم يتم العثور على القالب.",
        "preset_loaded": "✅ تم تحميل القالب. أرسل النص الآن لتحويله:",
        "no_presets": "📋 لا توجد قوالب محفوظة بعد. استخدم `/savepreset اسم` لحفظ الإعدادات الحالية.",
        "preset_list": "📋 **قوالبك المحفوظة:**",
        "doc_unsupported": "❌ صيغة الملف غير مدعومة. المدعوم: .txt .pdf .docx .srt",
        "doc_no_text": "❌ لم يتم استخراج نص من الملف.",
        "doc_processing": "📥 جاري تنزيل الملف واستخراج النص...",
        "preview_caption": "🔊 عينة من الصوت المختار",
        "preview_generating": "⏳ جاري توليد عينة...",
        "preview_only_single": "🔊 المعاينة متاحة فقط في وضع الصوت الواحد.",
        "speed_label": "⚡ السرعة:",
        "pitch_label": "🎵 النبرة:",
        "continue_btn": "✅ متابعة",
        "preview_btn": "🔊 معاينة الصوت",
        "bgm_on": "🎵 موسيقى الخلفية: ✅ مفعلة",
        "bgm_off": "🎵 موسيقى الخلفية: ❌ معطلة",
    },
    "en": {
        "welcome": ("Welcome to the Smart Text-to-Speech Bot!\n"
                    "Developer: **Ahmed Hassan Abdelnaby** 👨‍💻\n\n"
                    "🔹 **Step 1:** Select the audio engine:"),
        "step_engine": "🔹 **Step 1:** Select the audio engine:",
        "step_lang": "🔹 **Step 2:** Select language:",
        "step_dialect": "🔹 **Step 3:** Select dialect / region:",
        "step_voice": "🔹 **Step 4:** Select voice:",
        "step_speed": "🔹 **Audio Settings** (speed & pitch):",
        "step_format": "🔹 **Step 5:** Select audio format:",
        "step_text": "🔹 **Step 6:** Send the text (or a .txt/.pdf/.docx/.srt document):",
        "story_intro": "🎬 **Dialogue & Story Engine**\nChoose 2 to 5 characters.\nSelect engine for (v1):",
        "story_slot_prompt": "🔹 Select engine for character (v{num}):",
        "story_summary": "🎙️ **Current Story Cast:**\nTap any character below to **edit** it:",
        "story_btn_add": "➕ Add another character (v{num})",
        "story_btn_finish": "🎬 Finish Dialogue (Proceed to format & text)",
        "story_btn_reset": "🔄 Reset Cast",
        "story_step_text": ("🔹 **Send your dialogue script.**\n\n"
                            "💡 **Format:** Prefix turns with `(vNumber)`:\n\n"
                            "*(v1) Hello friend, how are you?*\n"
                            "*(v2) I'm doing great, thank you!*\n"
                            "*(v1) Wonderful to hear that!*"),
        "multilang_intro": ("🌐 **Automatic Multilingual Engine**\n\n"
                            "Automatically detects languages and switches voices natively!\n"
                            "⚠️ **Rule:** (2 to 3 voices max), each voice different language.\n\n"
                            "🔹 Select engine for the first language:"),
        "multilang_slot_prompt": "🔹 Select engine for the next language:",
        "multilang_summary": "🌐 **Configured languages & voices:**\nTap any voice below to **edit** it:",
        "multilang_btn_add": "➕ Add another language voice ({num} left)",
        "multilang_btn_finish": "➡️ Proceed with selected languages",
        "multilang_btn_reset": "🔄 Reset languages",
        "multilang_step_text": ("🔹 **Send your mixed-language text now.**\n\n"
                                "The bot will automatically detect each language segment and read it natively into one combined audio."),
        "generating": "⏳ **Analyzing languages, synthesizing voices, and merging audio...**",
        "done": "✅ Audio generated and merged successfully!",
        "new_text_hint": "\n\n💡 Send a new text with the same settings, or type /start to restart.",
        "back": "🔙 Back",
        "eng_neural": "Ultra Neural Engine (Studio Quality)",
        "eng_fast": "Fast Standard Engine (Lightweight)",
        "eng_creative": "Expressive Creative Engine (Characters)",
        "eng_story": "Story & Dialogue Engine (2-5 Voices) 🎬",
        "eng_multilang": "Multilingual Engine (Auto Language Switch) 🌐",
        "error": "⚠️ An error occurred while generating audio.",
        "rate_limit": "⚠️ Please wait {wait}s before your next request.",
        "busy": "⏳ You already have a request in progress. Please wait.",
        "preset_saved": "✅ Preset saved: **{name}**",
        "preset_not_found": "❌ Preset not found.",
        "preset_loaded": "✅ Preset loaded. Send the text to convert now:",
        "no_presets": "📋 No presets saved yet. Use `/savepreset name` to save current settings.",
        "preset_list": "📋 **Your saved presets:**",
        "doc_unsupported": "❌ Unsupported format. Use: .txt .pdf .docx .srt",
        "doc_no_text": "❌ No text could be extracted from the file.",
        "doc_processing": "📥 Downloading file & extracting text...",
        "preview_caption": "🔊 Sample of the selected voice",
        "preview_generating": "⏳ Generating preview...",
        "preview_only_single": "🔊 Preview is only available in single-voice mode.",
        "speed_label": "⚡ Speed:",
        "pitch_label": "🎵 Pitch:",
        "continue_btn": "✅ Continue",
        "preview_btn": "🔊 Preview Voice",
        "bgm_on": "🎵 Background Music: ✅ ON",
        "bgm_off": "🎵 Background Music: ❌ OFF",
    }
}

def t(update: Update, key: str) -> str:
    user_lang = "ar"
    try:
        if update.effective_user and update.effective_user.language_code:
            if not update.effective_user.language_code.startswith("ar"):
                user_lang = "en"
    except Exception:
        pass
    return I18N[user_lang].get(key, I18N["ar"].get(key, ""))

# ============ إدارة ملفات JSON ============
async def init_neural_json():
    global DB_NEURAL
    if os.path.exists(FILE_NEURAL_JSON):
        try:
            with open(FILE_NEURAL_JSON, "r", encoding="utf-8") as f:
                DB_NEURAL = json.load(f)
            return
        except Exception:
            pass
    raw_voices = await edge_tts.list_voices()
    db = {}
    for v in raw_voices:
        loc = v.get("Locale", "")
        short_name = v.get("ShortName", "")
        gender = v.get("Gender", "")
        friendly = v.get("FriendlyName", "")
        lang_code = loc.split("-")[0].lower()
        if lang_code not in db:
            db[lang_code] = {"name": NATIVE_LANG_NAMES.get(lang_code, lang_code.upper()), "dialects": {}}
        region_clean = friendly.split("(")[-1].replace(")", "").strip() if "(" in friendly else loc
        if loc not in db[lang_code]["dialects"]:
            db[lang_code]["dialects"][loc] = {"name": region_clean, "voices": []}
        voice_person = short_name.split("-")[-1].replace("Neural", "")
        gender_sign = "👩" if gender == "Female" else "👨"
        db[lang_code]["dialects"][loc]["voices"].append({
            "id": short_name, "name": f"{voice_person} ({gender_sign})"
        })
    with open(FILE_NEURAL_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    DB_NEURAL = db

def init_fast_json():
    global DB_FAST
    if os.path.exists(FILE_FAST_JSON):
        try:
            with open(FILE_FAST_JSON, "r", encoding="utf-8") as f:
                DB_FAST = json.load(f)
            return
        except Exception:
            pass
    from gtts.lang import tts_langs
    available = tts_langs()
    db = {}
    for code, original_name in available.items():
        db[code] = {"name": NATIVE_LANG_NAMES.get(code, original_name), "code": code}
    with open(FILE_FAST_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    DB_FAST = db

def init_creative_json():
    global DB_CREATIVE
    if os.path.exists(FILE_CREATIVE_JSON):
        try:
            with open(FILE_CREATIVE_JSON, "r", encoding="utf-8") as f:
                DB_CREATIVE = json.load(f)
            return
        except Exception:
            pass
    db = {
        "en": {"name": "English", "voices": [
            {"id": "en_us_001", "name": "Narrator (Female)"},
            {"id": "en_us_006", "name": "Joey (Male)"},
            {"id": "en_us_007", "name": "Professor (Male)"},
            {"id": "en_us_009", "name": "Scientist (Male)"},
            {"id": "en_male_narration", "name": "Storyteller (Male)"},
            {"id": "en_male_funny", "name": "Wacky (Male)"},
            {"id": "en_female_emotional", "name": "Peaceful (Female)"},
            {"id": "en_us_ghostface", "name": "Ghostface"},
            {"id": "en_us_chewbacca", "name": "Chewbacca"},
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

# ============ دوال مساعدة FFmpeg ============
async def run_ffmpeg(args: list, timeout: int = 180) -> bool:
    """تشغيل ffmpeg بشكل غير متزامن"""
    if not shutil.which("ffmpeg"):
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", *args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return False
        return proc.returncode == 0
    except Exception as e:
        logger.warning(f"ffmpeg error: {e}")
        return False

async def apply_speed_ffmpeg(input_file: str, output_file: str, speed: float) -> bool:
    """تطبيق تغيير السرعة عبر atempo"""
    if abs(speed - 1.0) < 0.01:
        try:
            shutil.copy(input_file, output_file)
            return True
        except Exception:
            return False
    filters = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining *= 2.0
    filters.append(f"atempo={remaining}")
    return await run_ffmpeg([
        "-i", input_file, "-filter:a", ",".join(filters),
        "-c:a", "libmp3lame", "-q:a", "2", "-y", output_file
    ])

async def create_silence(duration: float, output_file: str) -> bool:
    return await run_ffmpeg([
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(duration), "-c:a", "libmp3lame", "-q:a", "9", "-y", output_file
    ])

async def merge_audio_files(file_list: list, output_file: str, audio_format: str = "mp3",
                            include_silence_between: bool = False, add_bgm: bool = False):
    """توحيد المقاطع (Normalize) + الدمج + تحويل الصيغة النهائية بأمان"""
    valid = [fp for fp in file_list if os.path.exists(fp) and os.path.getsize(fp) > 0]
    if not valid:
        raise Exception("No valid audio chunks to merge")

    tmpdir = os.path.dirname(output_file) or "."
    if not shutil.which("ffmpeg"):
        with open(output_file, "wb") as out:
            for fp in valid:
                with open(fp, "rb") as f:
                    out.write(f.read())
        return

    # 1) إعادة ترميز كل مقطع بمواصفات موحدة
    normalized = []
    for i, fp in enumerate(valid):
        nfp = os.path.join(tmpdir, f"norm_{i}_{uuid.uuid4().hex[:6]}.mp3")
        ok = await run_ffmpeg([
            "-i", fp, "-ar", "44100", "-ac", "1",
            "-c:a", "libmp3lame", "-q:a", "2", "-y", nfp
        ])
        if ok and os.path.exists(nfp) and os.path.getsize(nfp) > 0:
            normalized.append(nfp)
        else:
            normalized.append(fp)

    if not normalized:
        raise Exception("Normalization failed")

    # 2) فترات صمت طبيعية بين الأدوار (لمحرك القصص)
    interleaved = []
    if include_silence_between and len(normalized) > 1:
        silence_path = os.path.join(tmpdir, f"silence_{uuid.uuid4().hex[:6]}.mp3")
        if await create_silence(0.7, silence_path):
            for i, nf in enumerate(normalized):
                interleaved.append(nf)
                if i < len(normalized) - 1:
                    interleaved.append(silence_path)
        else:
            interleaved = normalized
    else:
        interleaved = normalized

    # 3) الدمج عبر concat demuxer
    list_file = os.path.join(tmpdir, f"concat_{uuid.uuid4().hex}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for fp in interleaved:
            f.write(f"file '{os.path.abspath(fp).replace(chr(92), '/')}'\n")

    merged_mp3 = os.path.join(tmpdir, f"merged_{uuid.uuid4().hex[:6]}.mp3")
    if not await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-y", merged_mp3]):
        raise Exception("Merge failed")

    # 4) إضافة موسيقى الخلفية (BGM) إن وُجدت ومطلوبة
    if add_bgm and os.path.exists(BGM_FILE):
        bgm_mixed = os.path.join(tmpdir, f"bgm_{uuid.uuid4().hex[:6]}.mp3")
        ok = await run_ffmpeg([
            "-i", merged_mp3,
            "-stream_loop", "-1", "-i", BGM_FILE,
            "-filter_complex",
            "[0:a]volume=1.0[v];[1:a]volume=0.12[m];[v][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
            "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", "-y", bgm_mixed
        ])
        if ok and os.path.exists(bgm_mixed):
            merged_mp3 = bgm_mixed

    # 5) التحويل إلى الصيغة المطلوبة رسمياً
    if audio_format == "mp3":
        shutil.copy(merged_mp3, output_file)
    elif audio_format == "wav":
        await run_ffmpeg(["-i", merged_mp3, "-c:a", "pcm_s16le", "-y", output_file])
    elif audio_format == "ogg":
        await run_ffmpeg(["-i", merged_mp3, "-c:a", "libopus", "-b:a", "64k", "-y", output_file])
    else:
        shutil.copy(merged_mp3, output_file)

# ============ توليد مقطع صوتي ============
async def synthesize_chunk(text: str, engine: str, voice: str, out_file: str,
                           speed: float = 1.0, pitch: str = "normal") -> bool:
    clean_text = text.strip()
    if not clean_text or not re.search(r'[\w\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]', clean_text):
        return False

    if engine == "neural":
        rate_str = f"{int(round((speed - 1.0) * 100)):+d}%"
        pitch_str = {"low": "-20Hz", "high": "+20Hz"}.get(pitch, "+0Hz")
        comm = edge_tts.Communicate(text=clean_text, voice=voice, rate=rate_str, pitch=pitch_str)
        await comm.save(out_file)
        return True

    elif engine == "fast":
        tts = gTTS(text=clean_text, lang=voice)
        await asyncio.to_thread(tts.save, out_file)
        if abs(speed - 1.0) > 0.01:
            spd_out = out_file + ".speed.mp3"
            if await apply_speed_ffmpeg(out_file, spd_out, speed):
                os.replace(spd_out, out_file)
        return True

    else:  # creative
        api_url = "https://tiktok-tts.weilnet.workers.dev/api/generation"
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json={"text": clean_text, "voice": voice}) as resp:
                data = await resp.json()
                if "data" in data and data["data"]:
                    with open(out_file, "wb") as f:
                        f.write(base64.b64decode(data["data"]))
                    if abs(speed - 1.0) > 0.01:
                        spd_out = out_file + ".speed.mp3"
                        if await apply_speed_ffmpeg(out_file, spd_out, speed):
                            os.replace(spd_out, out_file)
                    return True
                raise Exception("Creative TTS error")

# ============ دوال التقطيع الذكي والحصص القصوى ============
def split_text_semantically(text: str, max_chars: int = 1500) -> list:
    """تقطيع النص إلى فقرات وجمل كاملة دون قطع في منتصف الكلمة أو الجملة"""
    clean_text = text.strip()
    if not clean_text:
        return []

    paragraphs = [p.strip() for p in clean_text.splitlines() if p.strip()]
    if not paragraphs:
        paragraphs = [clean_text]

    result = []
    current_chunk = []
    current_len = 0

    for p in paragraphs:
        if len(p) > max_chars:
            # تقطيع عند علامات الترقيم ونهايات الجمل
            sentences = re.split(r'(?<=[.!?؟،؛\n])\s+', p)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if current_len + len(s) > max_chars and current_chunk:
                    result.append(" ".join(current_chunk))
                    current_chunk = [s]
                    current_len = len(s)
                else:
                    current_chunk.append(s)
                    current_len += len(s)
        else:
            if current_len + len(p) > max_chars and current_chunk:
                result.append("\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)
            else:
                current_chunk.append(p)
                current_len += len(p)

    if current_chunk:
        result.append("\n".join(current_chunk))

    return [r.strip() for r in result if r.strip()]

def partition_chunks_by_target_size(chunk_paths: list, audio_format: str) -> list:
    """تقسيم المقاطع لمجموعات تستغل الحد الأقصى المسموح (حوالي 46 ميجابايت) دون تجاوزه"""
    MAX_SAFE_BYTES = 46 * 1024 * 1024  # سقف أمان دون حد 50 ميجا المسموح للبوتات

    if audio_format == "wav":
        # ملفات WAV غير مضغوطة وحجمها أكبر بنحو 5.5 مرة من MP3
        max_batch_mp3_bytes = MAX_SAFE_BYTES / 5.5
    elif audio_format == "ogg":
        max_batch_mp3_bytes = MAX_SAFE_BYTES * 1.5
    else:  # mp3
        max_batch_mp3_bytes = MAX_SAFE_BYTES * 0.95

    batches = []
    current_batch = []
    current_size = 0

    for cp in chunk_paths:
        if not os.path.exists(cp):
            continue
        c_size = os.path.getsize(cp)
        if current_batch and (current_size + c_size > max_batch_mp3_bytes):
            batches.append(current_batch)
            current_batch = [cp]
            current_size = c_size
        else:
            current_batch.append(cp)
            current_size += c_size

    if current_batch:
        batches.append(current_batch)

    return batches or [chunk_paths]

# ============ كشف اللغة والتحليل ============
def _classify_text_language_fallback(text: str, candidate_langs: list) -> str:
    if re.search(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]', text):
        for candidate in ["ar", "fa", "ur"]:
            if candidate in candidate_langs:
                return candidate
        return candidate_langs[0]
    if re.search(r'[\u0400-\u04FF]', text) and "ru" in candidate_langs:
        return "ru"
    if re.search(r'[\u4E00-\u9FFF]', text) and "zh" in candidate_langs:
        return "zh"
    if re.search(r'[\u3040-\u30FF]', text) and "ja" in candidate_langs:
        return "ja"
    if re.search(r'[\uAC00-\uD7AF]', text) and "ko" in candidate_langs:
        return "ko"

    scores = {lang: 0 for lang in candidate_langs}
    lower_text = text.lower()
    words = set(re.findall(r'\b\w+\b', lower_text))

    if "tr" in candidate_langs:
        tr_chars = len(re.findall(r'[çğıöşü]', lower_text))
        tr_words = {"ve", "bir", "bu", "da", "de", "için", "ile", "çok", "gibi", "daha", "kadar", "yok", "var", "ama", "değil", "şimdi", "dil", "türkçe", "ingilizce", "biz", "siz"}
        scores["tr"] = tr_chars * 10 + len(words & tr_words) * 5
    if "en" in candidate_langs:
        en_words = {"the", "and", "of", "to", "in", "is", "you", "that", "it", "we", "for", "on", "are", "as", "with", "they", "at", "be", "this", "have", "from", "today", "god", "willing", "talk", "speak", "english", "well", "way", "rules", "words", "nouns", "verbs"}
        scores["en"] = len(words & en_words) * 5
    if "es" in candidate_langs:
        es_chars = len(re.findall(r'[ñáéíóúü¿¡]', lower_text))
        es_words = {"el", "la", "los", "las", "y", "de", "en", "un", "una", "que", "por", "para", "con", "no", "si", "hola", "gracias", "amigo", "como", "pero"}
        scores["es"] = es_chars * 10 + len(words & es_words) * 5
    if "fr" in candidate_langs:
        fr_chars = len(re.findall(r'[çèàêëîïôùû]', lower_text))
        fr_words = {"le", "la", "les", "et", "de", "en", "un", "une", "du", "des", "pour", "avec", "est", "dans", "bonjour", "merci", "nous", "vous"}
        scores["fr"] = fr_chars * 10 + len(words & fr_words) * 5
    if "de" in candidate_langs:
        de_chars = len(re.findall(r'[äöüß]', lower_text))
        de_words = {"der", "die", "das", "und", "in", "zu", "den", "nicht", "von", "sie", "ist", "mit", "dem", "dass", "ich", "auf", "ein", "eine"}
        scores["de"] = de_chars * 10 + len(words & de_words) * 5

    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    if "en" in candidate_langs:
        return "en"
    return candidate_langs[0]

def classify_text_language(text: str, candidate_langs: list) -> str:
    """كشف اللغة: langdetect أولاً ثم fallback"""
    if HAS_LANGDETECT and len(text.strip()) >= 15:
        try:
            probs = detect_langs(text)
            for p in probs:
                if p.lang in candidate_langs and p.prob > 0.3:
                    return p.lang
        except LangDetectException:
            pass
    return _classify_text_language_fallback(text, candidate_langs)

def parse_story_dialogue(full_text: str, max_voices: int):
    pattern = r'[\(\[]\s*[vV]([1-5])\s*[\)\]][: \t]*'
    parts = re.split(pattern, full_text)
    segments = []
    if parts[0].strip():
        segments.append((0, parts[0].strip()))
    for i in range(1, len(parts), 2):
        v_idx = int(parts[i]) - 1
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            if v_idx >= max_voices:
                v_idx = 0
            segments.append((v_idx, content))
    return segments

def parse_multilang_text(full_text: str, lang_slots: list):
    candidate_langs = [s["lang"] for s in lang_slots]
    lang_to_idx = {s["lang"]: i for i, s in enumerate(lang_slots)}
    segments = []
    raw_lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    for line in raw_lines:
        sub_sentences = re.split(r'(?<=[.!?،؟])\s+', line)
        for sub in sub_sentences:
            sub = sub.strip()
            if not sub:
                continue
            has_ar = bool(re.search(r'[\u0600-\u06FF]', sub))
            has_lat = bool(re.search(r'[a-zA-Z\u00C0-\u024F\u0100-\u017F]', sub))
            if has_ar and has_lat:
                sub_chunks = re.findall(r'[\u0600-\u06FF0-9\s،؛؟]+|[^\u0600-\u06FF]+', sub)
                for ch in sub_chunks:
                    clean_ch = ch.strip()
                    if clean_ch and re.search(r'\w', clean_ch):
                        matched = classify_text_language(clean_ch, candidate_langs)
                        idx = lang_to_idx.get(matched, 0)
                        segments.append((idx, clean_ch))
            else:
                matched = classify_text_language(sub, candidate_langs)
                idx = lang_to_idx.get(matched, 0)
                segments.append((idx, sub))

    merged = []
    for idx, text in segments:
        if merged and merged[-1][0] == idx:
            merged[-1] = (idx, merged[-1][1] + " " + text)
        else:
            merged.append((idx, text))
    return merged

# ============ استخراج النص من المستندات ============
def extract_text_from_file(path: str, ext: str) -> str:
    try:
        if ext == ".txt":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        if ext == ".pdf" and HAS_PYPDF:
            reader = PdfReader(path)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if ext == ".docx" and HAS_DOCX:
            doc = DocxDocument(path)
            return "\n".join(p.text for p in doc.paragraphs)
        if ext == ".srt":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lines = []
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                if re.match(r'^\d+$', line):
                    continue
                if re.match(r'^\d{2}:\d{2}:\d{2}', line):
                    continue
                lines.append(line)
            return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Text extraction error: {e}")
    return ""

# ============ الأقفال والحدود ============
async def get_user_lock(user_id):
    if user_id not in USER_LOCKS:
        USER_LOCKS[user_id] = asyncio.Lock()
    return USER_LOCKS[user_id]

# ============ دورة عمل البوت ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton(t(update, "eng_neural"), callback_data="eng_neural")],
        [InlineKeyboardButton(t(update, "eng_fast"), callback_data="eng_fast")],
        [InlineKeyboardButton(t(update, "eng_creative"), callback_data="eng_creative")],
        [InlineKeyboardButton(t(update, "eng_multilang"), callback_data="eng_multilang")],
        [InlineKeyboardButton(t(update, "eng_story"), callback_data="eng_story")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(t(update, "welcome"), reply_markup=markup, parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(t(update, "welcome"), reply_markup=markup, parse_mode="Markdown")
    return STEP_ENGINE

def build_lang_keyboard(lang_keys: list, db_dict: dict, page: int = 0):
    page_size = 8
    total_pages = max(1, (len(lang_keys) + page_size - 1) // page_size)
    current_keys = lang_keys[page * page_size: (page + 1) * page_size]
    buttons, row = [], []
    for k in current_keys:
        row.append(InlineKeyboardButton(db_dict[k]["name"], callback_data=f"lang_{k}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page_{page+1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(buttons)

def build_voice_keyboard(voices: list, page: int = 0, page_size: int = 8):
    total_pages = max(1, (len(voices) + page_size - 1) // page_size)
    current = voices[page * page_size: (page + 1) * page_size]
    buttons, row = [], []
    for v in current:
        row.append(InlineKeyboardButton(v["name"], callback_data=f"voc_{v['id']}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"vpage_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"vpage_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_lang")])
    return InlineKeyboardMarkup(buttons)

async def select_engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    engine = query.data.replace("eng_", "")
    context.user_data["mode"] = engine

    if engine == "story":
        context.user_data["story_slots"] = []
        return await show_sub_engines(update, context, slot_num=1, mode="story")
    elif engine == "multilang":
        context.user_data["multilang_slots"] = []
        context.user_data["used_langs"] = set()
        return await show_sub_engines(update, context, slot_num=1, mode="multilang")
    else:
        context.user_data["engine"] = engine
        return await setup_language_selection(update, context, engine)

async def show_sub_engines(update: Update, context: ContextTypes.DEFAULT_TYPE, slot_num: int, mode: str):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton(t(update, "eng_neural"), callback_data="subeng_neural")],
        [InlineKeyboardButton(t(update, "eng_fast"), callback_data="subeng_fast")],
        [InlineKeyboardButton(t(update, "eng_creative"), callback_data="subeng_creative")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    editing_idx = context.user_data.get("editing_idx", None)
    if editing_idx is not None:
        title = f"✏️ **تعديل الصوت رقم {editing_idx + 1}**\nاختر المحرك الصوتي الجديد:"
    else:
        if mode == "story":
            title = t(update, "story_intro") if slot_num == 1 else t(update, "story_slot_prompt").format(num=slot_num)
        else:
            title = t(update, "multilang_intro") if slot_num == 1 else t(update, "multilang_slot_prompt")
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
        used_langs = context.user_data.get("used_langs", set())
        keys = [k for k in all_keys if k not in used_langs]
    else:
        keys = all_keys

    context.user_data["lang_keys"] = keys
    markup = build_lang_keyboard(keys, db_ref, 0)
    await query.edit_message_text(t(update, "step_lang"), reply_markup=markup, parse_mode="Markdown")
    return STEP_LANG

async def paginate_languages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.replace("page_", ""))
    mode = context.user_data.get("mode", "")
    if mode in ["story", "multilang"]:
        engine = context.user_data.get("current_sub_engine", "neural")
    else:
        engine = context.user_data.get("engine", "neural")
    keys = context.user_data.get("lang_keys", [])
    db_map = {"neural": DB_NEURAL, "fast": DB_FAST, "creative": DB_CREATIVE}
    markup = build_lang_keyboard(keys, db_map[engine], page)
    await query.edit_message_text(t(update, "step_lang"), reply_markup=markup, parse_mode="Markdown")
    return STEP_LANG

async def select_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang_code = query.data.replace("lang_", "")
    context.user_data["selected_lang"] = lang_code
    mode = context.user_data.get("mode", "")
    if mode in ["story", "multilang"]:
        engine = context.user_data.get("current_sub_engine", "neural")
    else:
        engine = context.user_data.get("engine", "neural")

    if engine == "neural":
        dialects = DB_NEURAL[lang_code]["dialects"]
        buttons, row = [], []
        for loc_code, loc_data in dialects.items():
            row.append(InlineKeyboardButton(loc_data["name"], callback_data=f"dia_{loc_code}"))
            if len(row) == 2:
                buttons.append(row); row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton(t(update, "back"), callback_data="back_to_lang")])
        await query.edit_message_text(t(update, "step_dialect"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return STEP_DIALECT

    elif engine == "fast":
        lang_name = DB_FAST[lang_code]["name"]
        return await on_voice_selected(update, context, voice_id=lang_code, voice_name=lang_name, lang_code=lang_code)

    else:
        voices = DB_CREATIVE[lang_code]["voices"]
        context.user_data["voice_list"] = voices
        context.user_data["voice_page"] = 0
        markup = build_voice_keyboard(voices, 0)
        await query.edit_message_text(t(update, "step_voice"), reply_markup=markup, parse_mode="Markdown")
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
    await query.edit_message_text(t(update, "step_voice"), reply_markup=markup, parse_mode="Markdown")
    return STEP_VOICE

async def paginate_voices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.replace("vpage_", ""))
    context.user_data["voice_page"] = page
    voices = context.user_data.get("voice_list", [])
    markup = build_voice_keyboard(voices, page)
    await query.edit_message_text(t(update, "step_voice"), reply_markup=markup, parse_mode="Markdown")
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
    editing_idx = context.user_data.get("editing_idx", None)

    if mode == "story":
        sub_engine = context.user_data.get("current_sub_engine", "neural")
        slots = context.user_data.get("story_slots", [])
        slot_data = {
            "engine": sub_engine, "voice": voice_id, "lang": lang_code,
            "label": f"{voice_name} ({sub_engine})"
        }
        if editing_idx is not None and editing_idx < len(slots):
            slots[editing_idx] = slot_data
            context.user_data["editing_idx"] = None
        else:
            slots.append(slot_data)
        context.user_data["story_slots"] = slots
        return await show_story_menu(update, context)

    elif mode == "multilang":
        sub_engine = context.user_data.get("current_sub_engine", "neural")
        slots = context.user_data.get("multilang_slots", [])
        lang_label = NATIVE_LANG_NAMES.get(lang_code, lang_code)
        slot_data = {
            "engine": sub_engine, "voice": voice_id, "lang": lang_code,
            "label": f"{lang_label}: {voice_name}"
        }
        if editing_idx is not None and editing_idx < len(slots):
            slots[editing_idx] = slot_data
            context.user_data["editing_idx"] = None
        else:
            slots.append(slot_data)
        used_langs = {s["lang"] for s in slots}
        context.user_data["used_langs"] = used_langs
        context.user_data["multilang_slots"] = slots
        return await show_multilang_menu(update, context)

    else:
        context.user_data["voice"] = voice_id
        context.user_data["voice_name"] = voice_name
        return await ask_speed(update, context)

async def show_story_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    slots = context.user_data.get("story_slots", [])
    buttons = []
    for i, s in enumerate(slots):
        buttons.append([InlineKeyboardButton(f"✏️ (v{i+1}) {s['voice']}", callback_data=f"edit_story_{i}")])
    if len(slots) < 5:
        buttons.append([InlineKeyboardButton(t(update, "story_btn_add").format(num=len(slots) + 1), callback_data="story_add")])
    if len(slots) >= 2:
        buttons.append([InlineKeyboardButton(t(update, "story_btn_finish"), callback_data="story_finish")])
    buttons.append([InlineKeyboardButton(t(update, "story_btn_reset"), callback_data="story_reset")])
    await query.edit_message_text(t(update, "story_summary"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return STEP_STORY_MENU

async def handle_story_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("edit_story_"):
        idx = int(data.replace("edit_story_", ""))
        context.user_data["editing_idx"] = idx
        return await show_sub_engines(update, context, slot_num=idx + 1, mode="story")
    elif data == "story_add":
        slot_num = len(context.user_data.get("story_slots", [])) + 1
        return await show_sub_engines(update, context, slot_num, mode="story")
    elif data == "story_finish":
        return await ask_speed(update, context)
    elif data == "story_reset":
        context.user_data["story_slots"] = []
        context.user_data["editing_idx"] = None
        return await show_sub_engines(update, context, slot_num=1, mode="story")

async def show_multilang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    slots = context.user_data.get("multilang_slots", [])
    buttons = []
    for i, s in enumerate(slots):
        buttons.append([InlineKeyboardButton(f"✏️ {i+1}️⃣ {s['label']}", callback_data=f"edit_mlang_{i}")])
    if len(slots) < 3:
        remaining = 3 - len(slots)
        buttons.append([InlineKeyboardButton(t(update, "multilang_btn_add").format(num=remaining), callback_data="mlang_add")])
    if len(slots) >= 2:
        buttons.append([InlineKeyboardButton(t(update, "multilang_btn_finish"), callback_data="mlang_finish")])
    buttons.append([InlineKeyboardButton(t(update, "multilang_btn_reset"), callback_data="mlang_reset")])
    await query.edit_message_text(t(update, "multilang_summary"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return STEP_MULTILANG_MENU

async def handle_multilang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("edit_mlang_"):
        idx = int(data.replace("edit_mlang_", ""))
        context.user_data["editing_idx"] = idx
        slots = context.user_data.get("multilang_slots", [])
        if idx < len(slots):
            current_lang = slots[idx]["lang"]
            used_langs = context.user_data.get("used_langs", set())
            used_langs.discard(current_lang)
            context.user_data["used_langs"] = used_langs
        return await show_sub_engines(update, context, slot_num=idx + 1, mode="multilang")
    elif data == "mlang_add":
        slot_num = len(context.user_data.get("multilang_slots", [])) + 1
        return await show_sub_engines(update, context, slot_num, mode="multilang")
    elif data == "mlang_finish":
        return await ask_speed(update, context)
    elif data == "mlang_reset":
        context.user_data["multilang_slots"] = []
        context.user_data["used_langs"] = set()
        context.user_data["editing_idx"] = None
        return await show_sub_engines(update, context, slot_num=1, mode="multilang")

async def ask_speed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    speed = context.user_data.get("speed", 1.0)
    pitch = context.user_data.get("pitch", "normal")
    add_bgm = context.user_data.get("add_bgm", False)
    mode = context.user_data.get("mode", "single")

    def smark(label, val):
        return f"✅ {label}" if abs(float(val) - float(speed)) < 0.01 else label

    def pmark(label, val):
        return f"✅ {label}" if val == pitch else label

    speed_row = [
        InlineKeyboardButton(smark("0.75x", 0.75), callback_data="spd_0.75"),
        InlineKeyboardButton(smark("1.0x", 1.0), callback_data="spd_1.0"),
        InlineKeyboardButton(smark("1.25x", 1.25), callback_data="spd_1.25"),
        InlineKeyboardButton(smark("1.5x", 1.5), callback_data="spd_1.5"),
    ]
    pitch_row = [
        InlineKeyboardButton(pmark("Low", "low"), callback_data="pit_low"),
        InlineKeyboardButton(pmark("Normal", "normal"), callback_data="pit_normal"),
        InlineKeyboardButton(pmark("High", "high"), callback_data="pit_high"),
    ]
    buttons = [speed_row, pitch_row]

    if os.path.exists(BGM_FILE):
        buttons.append([InlineKeyboardButton(
            t(update, "bgm_on") if add_bgm else t(update, "bgm_off"),
            callback_data="bgm_toggle"
        )])

    action_row = []
    if mode == "single":
        action_row.append(InlineKeyboardButton(t(update, "preview_btn"), callback_data="spd_preview"))
    action_row.append(InlineKeyboardButton(t(update, "continue_btn"), callback_data="spd_go"))
    buttons.append(action_row)

    await query.edit_message_text(t(update, "step_speed"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return STEP_SPEED

async def handle_speed_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("spd_") and data not in ("spd_go", "spd_preview"):
        try:
            context.user_data["speed"] = float(data.replace("spd_", ""))
        except ValueError:
            pass
        return await ask_speed(update, context)
    elif data.startswith("pit_"):
        context.user_data["pitch"] = data.replace("pit_", "")
        return await ask_speed(update, context)
    elif data == "bgm_toggle":
        context.user_data["add_bgm"] = not context.user_data.get("add_bgm", False)
        return await ask_speed(update, context)
    elif data == "spd_preview":
        return await preview_voice(update, context)
    elif data == "spd_go":
        return await ask_format(update, context)
    return STEP_SPEED

async def preview_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    mode = context.user_data.get("mode", "single")
    if mode != "single":
        await query.answer(t(update, "preview_only_single"), show_alert=True)
        return STEP_SPEED
    await query.answer(t(update, "preview_generating"))
    engine = context.user_data.get("engine", "neural")
    voice = context.user_data.get("voice", "ar-EG-SalmaNeural")
    speed = context.user_data.get("speed", 1.0)
    pitch = context.user_data.get("pitch", "normal")

    sample = "مرحباً بك، هذه عينة قصيرة من الصوت المختار. أتمنى أن تنال إعجابك."

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "preview.mp3")
            ok = await synthesize_chunk(sample, engine, voice, path, speed=speed, pitch=pitch)
            if ok and os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, "rb") as f:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id, audio=f,
                        title="Voice Preview", performer="Smart TTS",
                        caption=t(update, "preview_caption")
                    )
            else:
                await query.message.reply_text("❌ Preview generation failed.")
    except Exception as e:
        logger.error(f"Preview error: {e}")
    return STEP_SPEED

async def ask_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    formats = [
        [InlineKeyboardButton("MP3 (الأكثر توافقاً)", callback_data="fmt_mp3")],
        [InlineKeyboardButton("WAV (صوت نقي عالي الجودة)", callback_data="fmt_wav")],
        [InlineKeyboardButton("OGG (تسجيل صوتي / Voice Note)", callback_data="fmt_ogg")],
    ]
    text = t(update, "step_format")
    markup = InlineKeyboardMarkup(formats)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return STEP_FORMAT

async def select_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chosen_format = query.data.replace("fmt_", "")
    context.user_data["audio_format"] = chosen_format
    mode = context.user_data.get("mode", "single")
    if mode == "story":
        prompt_text = f"{t(update, 'story_step_text')}\n\n📁 الصيغة: `({chosen_format.upper()})`"
    elif mode == "multilang":
        prompt_text = f"{t(update, 'multilang_step_text')}\n\n📁 الصيغة: `({chosen_format.upper()})`"
    else:
        prompt_text = f"{t(update, 'step_text')}\n📁 الصيغة: `({chosen_format.upper()})`"
    await query.edit_message_text(prompt_text, parse_mode="Markdown")
    return STEP_TEXT

# ============ التوليد الرئيسي ============
async def generate_audio(update: Update, context: ContextTypes.DEFAULT_TYPE, text_override: str = None) -> int:
    user_id = update.effective_user.id
    user_text = (text_override if text_override is not None else (update.message.text or "")).strip()
    if not user_text:
        await update.message.reply_text("⚠️ Empty text / النص فارغ.")
        return STEP_TEXT

    now = time.monotonic()
    last = USER_LAST_REQUEST.get(user_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        wait = int(RATE_LIMIT_SECONDS - (now - last)) + 1
        await update.message.reply_text(t(update, "rate_limit").format(wait=wait))
        return STEP_TEXT
    USER_LAST_REQUEST[user_id] = now

    lock = await get_user_lock(user_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        await update.message.reply_text(t(update, "busy"))
        return STEP_TEXT

    try:
        await _do_generate(update, context, user_text)
    except Exception as e:
        logger.error(f"Generation error: {e}", exc_info=True)
        try:
            await update.message.reply_text(t(update, "error"))
        except Exception:
            pass
    finally:
        lock.release()
    return STEP_TEXT

async def _do_generate(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    mode = context.user_data.get("mode", "single")
    audio_format = context.user_data.get("audio_format", "mp3")
    speed = context.user_data.get("speed", 1.0)
    pitch = context.user_data.get("pitch", "normal")
    add_bgm = context.user_data.get("add_bgm", False)

    status_msg = await update.message.reply_text(t(update, "generating"), parse_mode="Markdown")

    try:
        if ChatAction:
            try:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.RECORD_VOICE)
            except Exception:
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            chunks = []
            caption_details = ""

            if mode == "story":
                slots = context.user_data.get("story_slots", [])
                segments = parse_story_dialogue(user_text, len(slots))
                for i, (v_idx, seg_text) in enumerate(segments):
                    slot = slots[v_idx]
                    # التقطيع الذكي داخل كل حوار طويل
                    sub_pieces = split_text_semantically(seg_text, max_chars=1200)
                    for j, sp in enumerate(sub_pieces):
                        cf = os.path.join(tmpdir, f"chunk_{i}_{j}.mp3")
                        if await synthesize_chunk(sp, slot["engine"], slot["voice"], cf, speed=speed, pitch=pitch):
                            chunks.append(cf)
                if not chunks:
                    raise Exception("No chunks generated")
                caption_details = f"🎬 **{len(slots)} characters** | ⚡{speed}x"

            elif mode == "multilang":
                slots = context.user_data.get("multilang_slots", [])
                segments = parse_multilang_text(user_text, slots)
                for i, (v_idx, seg_text) in enumerate(segments):
                    slot = slots[v_idx]
                    cf = os.path.join(tmpdir, f"chunk_{i}.mp3")
                    if await synthesize_chunk(seg_text, slot["engine"], slot["voice"], cf, speed=speed, pitch=pitch):
                        chunks.append(cf)
                if not chunks:
                    raise Exception("No chunks generated")
                caption_details = f"🌐 **{len(slots)} languages** | ⚡{speed}x"

            else:
                engine = context.user_data.get("engine", "neural")
                voice = context.user_data.get("voice", "ar-EG-SalmaNeural")
                # التقطيع الذكي عند الفقرات والجمل
                text_chunks = split_text_semantically(user_text, max_chars=1200)
                if not text_chunks:
                    raise Exception("Empty text")
                for i, tch in enumerate(text_chunks):
                    cf = os.path.join(tmpdir, f"chunk_{i}.mp3")
                    if await synthesize_chunk(tch, engine, voice, cf, speed=speed, pitch=pitch):
                        chunks.append(cf)
                if not chunks:
                    raise Exception("Synthesis failed")
                caption_details = f"🎙️ `{voice}` | ⚡{speed}x"

            # تقسيم المقاطع لاستغلال الحد الأقصى للملف الواحد (حتى 46MB بأمان)
            batches = partition_chunks_by_target_size(chunks, audio_format)
            total_parts = len(batches)

            for part_idx, batch_chunks in enumerate(batches, 1):
                part_path = os.path.join(tmpdir, f"part_{part_idx}.{audio_format}")
                include_silence = (mode == "story")
                await merge_audio_files(
                    batch_chunks, part_path, audio_format=audio_format,
                    include_silence_between=include_silence, add_bgm=add_bgm
                )

                part_label = f"📦 **الجزء ({part_idx} من {total_parts})**\n" if total_parts > 1 else ""
                is_last = (part_idx == total_parts)
                hint = t(update, 'new_text_hint') if is_last else ""
                caption = f"{t(update, 'done')}\n{part_label}{caption_details}\n📁 `{audio_format.upper()}`{hint}"

                with open(part_path, "rb") as audio_file:
                    if audio_format == "ogg":
                        await update.message.reply_voice(voice=audio_file, caption=caption, parse_mode="Markdown")
                    else:
                        title_str = f"Audio Part {part_idx}" if total_parts > 1 else "Audio Recording"
                        await update.message.reply_audio(
                            audio=audio_file, title=title_str,
                            performer="Smart TTS Engine", caption=caption, parse_mode="Markdown"
                        )

            # حذف رسالة الانتظار فقط بعد اكتمال الإرسال بنجاح
            try:
                await status_msg.delete()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"TTS generation error: {e}", exc_info=True)
        try:
            await status_msg.edit_text(t(update, "error"))
        except Exception:
            try:
                await update.message.reply_text(t(update, "error"))
            except Exception:
                pass

# ============ معالجة المستندات ============
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        return STEP_TEXT
    file_name = doc.file_name or "file.txt"
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in (".txt", ".pdf", ".docx", ".srt"):
        await update.message.reply_text(t(update, "doc_unsupported"))
        return STEP_TEXT
    if ext == ".pdf" and not HAS_PYPDF:
        await update.message.reply_text("❌ pypdf غير مثبت لقراءة PDF.")
        return STEP_TEXT
    if ext == ".docx" and not HAS_DOCX:
        await update.message.reply_text("❌ python-docx غير مثبت لقراءة DOCX.")
        return STEP_TEXT

    status = await update.message.reply_text(t(update, "doc_processing"))
    tmpdir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmpdir, file_name)
    try:
        file = await doc.get_file()
        await file.download_to_drive(tmp_path)
        text = extract_text_from_file(tmp_path, ext)
        if not text.strip():
            await status.edit_text(t(update, "doc_no_text"))
            return STEP_TEXT
        await status.delete()
        return await generate_audio(update, context, text_override=text)
    except Exception as e:
        logger.error(f"Document error: {e}")
        try:
            await status.edit_text(t(update, "error"))
        except Exception:
            pass
        return STEP_TEXT
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ============ القوالب (Presets) ============
async def save_preset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HAS_SQLALCHEMY:
        await update.message.reply_text("❌ Database unavailable (SQLAlchemy missing).")
        return
    user_id = str(update.effective_user.id)
    name = " ".join(context.args).strip() if context.args else f"Preset_{int(time.time())}"

    config = {}
    for k in ("mode", "engine", "voice", "voice_name", "selected_lang", "selected_dia",
              "audio_format", "speed", "pitch", "add_bgm", "story_slots", "multilang_slots"):
        if k in context.user_data:
            config[k] = context.user_data[k]

    with SessionLocal() as db:
        preset = UserPreset(user_id=user_id, name=name, config_json=json.dumps(config, ensure_ascii=False))
        db.add(preset)
        db.commit()
    await update.message.reply_text(t(update, "preset_saved").format(name=name), parse_mode="Markdown")

async def presets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HAS_SQLALCHEMY:
        await update.message.reply_text("❌ Database unavailable.")
        return
    user_id = str(update.effective_user.id)
    with SessionLocal() as db:
        presets = db.query(UserPreset).filter_by(user_id=user_id).all()
        if not presets:
            await update.message.reply_text(t(update, "no_presets"))
            return
        buttons = [[InlineKeyboardButton(f"📁 {p.name}", callback_data=f"loadpreset_{p.id}")] for p in presets]
    await update.message.reply_text(t(update, "preset_list"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def load_preset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if not HAS_SQLALCHEMY:
        await query.edit_message_text("❌ Database unavailable.")
        return ConversationHandler.END
    try:
        preset_id = int(query.data.replace("loadpreset_", ""))
    except ValueError:
        return ConversationHandler.END
    user_id = str(query.from_user.id)
    with SessionLocal() as db:
        preset = db.query(UserPreset).filter_by(id=preset_id, user_id=user_id).first()
        if not preset:
            await query.edit_message_text(t(update, "preset_not_found"))
            return ConversationHandler.END
        config = json.loads(preset.config_json or "{}")

    context.user_data.clear()
    for k, v in config.items():
        if v is not None:
            context.user_data[k] = v
    if context.user_data.get("multilang_slots"):
        context.user_data["used_langs"] = {s["lang"] for s in context.user_data["multilang_slots"]}

    await query.edit_message_text(t(update, "preset_loaded"))
    return STEP_TEXT

# ============ لا شيء / إلغاء ============
async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.callback_query.answer()
    except Exception:
        pass

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم الإلغاء. اكتب /start للبدء مجدداً.")
    return ConversationHandler.END

# ============ التشغيل ============
async def on_startup(app):
    print("⏳ جاري تحضير وفحص قواعد بيانات المحركات...")
    await init_neural_json()
    init_fast_json()
    init_creative_json()
    print("✅ تم تجهيز قواعد البيانات وتشغيل البوت بنجاح!")

def main():
    # تمديد مهل الاتصال والرفع (Timeouts) للتعامل السلس مع الملفات الصوتية الكبيرة
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(60)
        .post_init(on_startup)
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(load_preset_callback, pattern=r"^loadpreset_\d+$"),
        ],
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
            STEP_STORY_MENU: [CallbackQueryHandler(handle_story_menu, pattern="^(story_|edit_story_)")],
            STEP_MULTILANG_MENU: [CallbackQueryHandler(handle_multilang_menu, pattern="^(mlang_|edit_mlang_)")],
            STEP_SPEED: [CallbackQueryHandler(handle_speed_selection, pattern="^(spd_|pit_|bgm_)")],
            STEP_FORMAT: [CallbackQueryHandler(select_format, pattern="^fmt_")],
            STEP_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, generate_audio),
                MessageHandler(filters.Document.ALL, handle_document),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("savepreset", save_preset_cmd))
    app.add_handler(CommandHandler("presets", presets_cmd))

    print("🚀 البوت متصل وجاهز للعمل...")
    app.run_polling(close_loop=False, drop_pending_updates=True)

if __name__ == "__main__":
    main()