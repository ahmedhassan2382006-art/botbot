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

# ============ مكتبات اختيارية ============
try:
    from langdetect import detect_langs, LangDetectException
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

BOT_TOKEN = os.environ.get("BOT_TOKEN")

FILE_NEURAL_JSON = "neural_voices.json"
FILE_FAST_JSON = "fast_voices.json"
FILE_CREATIVE_JSON = "creative_voices.json"
USER_CONFIG_FILE = "users_config.json"
BGM_FILE = "background_music.mp3"

# ============ الحالات ============
(
    STEP_ENGINE, STEP_LANG, STEP_DIALECT, STEP_VOICE,
    STEP_STORY_MENU, STEP_MULTILANG_MENU, STEP_SPEED,
    STEP_FORMAT, STEP_TEXT, STEP_VOICE_CUSTOMIZE,
) = range(10)

# ============ قواعد البيانات ============
DB_NEURAL = {}
DB_FAST = {}
DB_CREATIVE = {}

# ============ طابور المعالجة وإدارة العمليات ============
USER_TASK_QUEUES = {}
USER_CURRENT_TASK = {}
USER_WORKERS = {}

MAX_CONCURRENT_TTS = 3
GLOBAL_TTS_SEMAPHORE = None
GLOBAL_HTTP_SESSION = None

# ============ الإعدادات الافتراضية ============
DEFAULT_CONFIG = {
    "mode": "single",
    "engine": "neural",
    "voice": "ar-EG-SalmaNeural",
    "voice_name": "Salma (👩)",
    "audio_format": "mp3",
    "speed": 1.0,
    "pitch": "normal",
    "volume": 1.0,
    "pause_duration": 0.5,
    "story_slots": [],
    "multilang_slots": [],
    "bgm_enabled": False,
    "bgm_mode": "loop",
    "bgm_duration": 30,
}

# ============ إدارة الإعدادات الدائمة ============
def load_all_user_configs() -> dict:
    if os.path.exists(USER_CONFIG_FILE):
        try:
            with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_user_config(user_id: str, config: dict):
    configs = load_all_user_configs()
    clean_config = {}
    for k in (
        "mode", "engine", "voice", "voice_name", "selected_lang", "selected_dia",
        "audio_format", "speed", "pitch", "volume", "pause_duration",
        "story_slots", "multilang_slots", "bgm_enabled", "bgm_mode", "bgm_duration"
    ):
        if k in config:
            clean_config[k] = config[k]
    configs[str(user_id)] = clean_config
    try:
        with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(configs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save user config: {e}")

def get_saved_user_config(user_id: str) -> dict:
    configs = load_all_user_configs()
    saved = configs.get(str(user_id), {})
    merged = dict(DEFAULT_CONFIG)
    merged.update(saved)
    return merged

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
        "welcome": ("مرحباً بك يا **{name}** في بوت تحويل النص إلى صوت الذكي!\n"
                    "المطور: **أحمد حسن عبد النبي** 👨‍💻\n\n"
                    "⚙️ **لوحة التحكم السريعة متاحة دائماً عبر الأمر:** /settings\n\n"
                    "🔹 **الخطوة 1:** اختر المحرك الصوتي المطلوب:"),
        "step_engine": "🔹 **الخطوة 1:** اختر المحرك الصوتي:",
        "step_lang": "🔹 **الخطوة 2:** اختر اللغة:",
        "step_dialect": "🔹 **الخطوة 3:** اختر اللهجة / الدولة:",
        "step_voice": "🔹 **الخطوة 4:** اختر الصوت:",
        "step_speed": "🔹 **اضبط إعدادات الصوت العامة وموسيقى الخلفية**:",
        "step_format": "🔹 **الخطوة 5:** اختر صيغة الملف الصوتي:",
        "step_text": "🔹 **أرسل النص الآن (أو مستند .docx/.txt/.srt):**",
        "story_intro": "🎬 **محرك السيناريو والقصص**\nاختر شخصياتك من (2 إلى 5 شخصيات).\nاختر محرك الشخصية الأولى (v1):",
        "story_slot_prompt": "🔹 اختر المحرك الصوتي للشخصية (v{num}):",
        "story_summary": "🎙️ **طاقم السيناريو الحالي:**\nاضغط على أي شخصية لضبط صوتها أو تخصيص (السرعة/النبرة/الهمس/الصوت):",
        "story_btn_add": "➕ إضافة شخصية جديدة (v{num})",
        "story_btn_finish": "🎬 اعتماد الطاقم والانتقال للخيارات المتقدمة",
        "story_btn_reset": "🔄 إعادة تعيين الشخصيات",
        "story_step_text": ("🔹 **أرسل نص سيناريو الحوار.**\n\n"
                            "💡 **طريقة الكتابة:** ضع رمز الشخصية `(vالرقم)` في بداية كلامها:\n\n"
                            "*(v1) مرحباً يا أحمد، هل أتممت كتابة التقرير؟*\n"
                            "*(v2) بصوت خافت: نعم يا سارة، انتهيت منه للتو.*"),
        "multilang_intro": ("🌐 **محرك متعدد اللغات التلقائي**\n\n"
                            "يتعرف تلقائياً على اللغات ويوزع كل لغة على صوتها الأصلي بدقة!\n"
                            "🔹 اختر المحرك الصوتي للغة الأولى:"),
        "multilang_slot_prompt": "🔹 اختر المحرك الصوتي للغة التالية:",
        "multilang_summary": "🌐 **اللغات والأصوات المختارة:**\nاضغط على أي لغة لضبط سرعتها ونبرتها وصوتها:",
        "multilang_btn_add": "➕ إضافة لغة وصوت إضافي (متبقي {num})",
        "multilang_btn_finish": "➡️ اعتماد اللغات والانتقال للخيارات المتقدمة",
        "multilang_btn_reset": "🔄 إعادة ضبط اللغات",
        "multilang_step_text": "🔹 **أرسل النص الآن (أو مستند .docx/.txt/.srt).**\nسيتعرف البوت بذكاء على اللغات وينطقها بسلاسة.",
        "generating": "⏳ **جاري معالجة وتوليد الصوت بدقة استوديو...**",
        "queue_waiting": "⏳ **طلبك مسجل في قائمة الانتظار، سيبدأ فوراً...**",
        "queue_multi_file": "⏳ هناك عملية جارية على ملف **{curr}**.\nتم وضع ملفك **{new}** في الانتظار وسيبدأ بعده مباشرة.",
        "done": "✅ تم إنشاء الملف الصوتي بنجاح!",
        "new_text_hint": "\n\n💡 أرسل نصاً أو ملفاً جديداً في أي وقت بالدردشة وسأحوله لك بنفس هذا الضبط فوراً!",
        "back": "🔙 رجوع",
        "eng_neural": "المحرك العصبي الفائق (جودة استوديو)",
        "eng_fast": "المحرك السريع القياسي (خفيف)",
        "eng_creative": "المحرك التعبيري الحديث (شخصيات)",
        "eng_story": "محرك القصص والسيناريو (تخصيص كامل للأصوات) 🎬",
        "eng_multilang": "محرك متعدد اللغات الذكي 🌐",
        "error": "⚠️ حدث خطأ أثناء معالجة الصوت، يرجى المحاولة لاحقاً.",
        "doc_unsupported": "❌ صيغة الملف غير مدعومة. الصيغ المعتمدة: .docx .txt .srt",
        "doc_no_text": "❌ لم يتم استخراج أي نص من الملف.",
        "doc_processing": "📥 جاري قراءة الملف بالخلفية...",
        "preview_caption": "🔊 عينة من الصوت المختار",
        "preview_generating": "⏳ جاري تجهيز المعاينة...",
        "preview_only_single": "🔊 المعاينة متاحة في وضع الصوت الفردي.",
        "continue_btn": "✅ متابعة",
        "preview_btn": "🔊 معاينة الصوت",
        "need_start": "🔹 مرحباً بك! يرجى إرسال /start أو ضبط الإعدادات عبر /settings أولاً.",
        "part_label": "📦 **الجزء ({part} من {total})**\n",
    }
}

def t(update: Update, key: str, **kwargs) -> str:
    user_lang = "ar"
    text = I18N[user_lang].get(key, I18N["ar"].get(key, ""))
    if kwargs:
        return text.format(**kwargs)
    return text

# ============ تحضير قواعد بيانات الأصوات ============
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
    db = {code: {"name": NATIVE_LANG_NAMES.get(code, name), "code": code} for code, name in available.items()}
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

# ============ أدوات FFmpeg الاحترافية ============
async def run_ffmpeg(args: list, timeout: int = 180) -> bool:
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

async def apply_audio_filters(input_file: str, output_file: str, speed: float = 1.0, volume: float = 1.0) -> bool:
    filters = []
    if abs(speed - 1.0) >= 0.01:
        rem = speed
        while rem > 2.0:
            filters.append("atempo=2.0"); rem /= 2.0
        while rem < 0.5:
            filters.append("atempo=0.5"); rem *= 2.0
        filters.append(f"atempo={rem}")

    if abs(volume - 1.0) >= 0.01:
        filters.append(f"volume={volume:.2f}")

    if not filters:
        try:
            shutil.copy(input_file, output_file)
            return True
        except Exception:
            return False

    return await run_ffmpeg([
        "-i", input_file, "-filter:a", ",".join(filters),
        "-c:a", "libmp3lame", "-q:a", "2", "-y", output_file
    ])

async def create_silence(duration: float, output_file: str) -> bool:
    if duration <= 0.01:
        return False
    return await run_ffmpeg([
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", f"{duration:.2f}", "-c:a", "libmp3lame", "-q:a", "9", "-y", output_file
    ])

async def merge_audio_files(file_list: list, output_file: str, audio_format: str = "mp3",
                            pause_duration: float = 0.5, bgm_config: dict = None):
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

    normalized = []
    for i, fp in enumerate(valid):
        nfp = os.path.join(tmpdir, f"norm_{i}_{uuid.uuid4().hex[:6]}.mp3")
        ok = await run_ffmpeg([
            "-i", fp, "-ar", "44100", "-ac", "1",
            "-c:a", "libmp3lame", "-q:a", "2", "-y", nfp
        ])
        normalized.append(nfp if ok and os.path.exists(nfp) else fp)

    interleaved = []
    if pause_duration > 0.05 and len(normalized) > 1:
        silence_path = os.path.join(tmpdir, f"silence_{uuid.uuid4().hex[:6]}.mp3")
        if await create_silence(pause_duration, silence_path):
            for i, nf in enumerate(normalized):
                interleaved.append(nf)
                if i < len(normalized) - 1:
                    interleaved.append(silence_path)
        else:
            interleaved = normalized
    else:
        interleaved = normalized

    list_file = os.path.join(tmpdir, f"concat_{uuid.uuid4().hex}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for fp in interleaved:
            f.write(f"file '{os.path.abspath(fp).replace(chr(92), '/')}'\n")

    merged_mp3 = os.path.join(tmpdir, f"merged_{uuid.uuid4().hex[:6]}.mp3")
    if not await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-y", merged_mp3]):
        raise Exception("Merge failed")

    # ============ دمج الموسيقى الخلفية ============
    if bgm_config and bgm_config.get("enabled") and os.path.exists(BGM_FILE):
        bgm_mixed = os.path.join(tmpdir, f"bgm_{uuid.uuid4().hex[:6]}.mp3")
        mode = bgm_config.get("mode", "loop")
        dur = int(bgm_config.get("duration", 30))

        if mode == "loop":
            ok = await run_ffmpeg([
                "-i", merged_mp3,
                "-stream_loop", "-1", "-i", BGM_FILE,
                "-filter_complex",
                "[0:a]volume=1.0[v];[1:a]volume=0.10[m];[v][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
                "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", "-y", bgm_mixed
            ])
        else:
            fade_start = max(1, dur - 3)
            ok = await run_ffmpeg([
                "-i", merged_mp3,
                "-i", BGM_FILE,
                "-filter_complex",
                f"[1:a]atrim=0:{dur},afade=t=out:st={fade_start}:d=3,volume=0.10[m];[0:a]volume=1.0[v];[v][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", "-y", bgm_mixed
            ])
        if ok and os.path.exists(bgm_mixed):
            merged_mp3 = bgm_mixed

    if audio_format == "wav":
        await run_ffmpeg(["-i", merged_mp3, "-c:a", "pcm_s16le", "-y", output_file])
    elif audio_format == "ogg":
        await run_ffmpeg(["-i", merged_mp3, "-c:a", "libopus", "-b:a", "64k", "-y", output_file])
    else:
        shutil.copy(merged_mp3, output_file)

# ============ توليد مقطع صوتي لكل صوت بإعداداته الخاصة ============
async def synthesize_chunk(text: str, engine: str, voice: str, out_file: str,
                           speed: float = 1.0, pitch: str = "normal", volume: float = 1.0) -> bool:
    clean_text = text.strip()
    if not clean_text or not re.search(r'[\w\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]', clean_text):
        return False

    if engine == "neural":
        rate_str = f"{int(round((speed - 1.0) * 100)):+d}%"
        vol_pct = f"{int(round((volume - 1.0) * 100)):+d}%"
        pitch_map = {
            "whisper": "-40Hz",
            "low": "-20Hz",
            "normal": "+0Hz",
            "high": "+20Hz"
        }
        pitch_str = pitch_map.get(pitch, "+0Hz")
        comm = edge_tts.Communicate(text=clean_text, voice=voice, rate=rate_str, pitch=pitch_str, volume=vol_pct)
        await comm.save(out_file)
        return True

    elif engine == "fast":
        tts = gTTS(text=clean_text, lang=voice)
        await asyncio.to_thread(tts.save, out_file)
        if abs(speed - 1.0) > 0.01 or abs(volume - 1.0) > 0.01:
            tmp_flt = out_file + ".flt.mp3"
            if await apply_audio_filters(out_file, tmp_flt, speed=speed, volume=volume):
                os.replace(tmp_flt, out_file)
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
                if abs(speed - 1.0) > 0.01 or abs(volume - 1.0) > 0.01:
                    tmp_flt = out_file + ".flt.mp3"
                    if await apply_audio_filters(out_file, tmp_flt, speed=speed, volume=volume):
                        os.replace(tmp_flt, out_file)
                return True
            raise Exception("Creative TTS error")

# ============ التقطيع الذكي ============
def _sync_split_text_semantically(text: str, max_chars: int = 1000) -> list:
    clean_text = text.strip()
    if not clean_text:
        return []
    paragraphs = [p.strip() for p in clean_text.splitlines() if p.strip()] or [clean_text]
    result, current_chunk, current_len = [], [], 0

    for p in paragraphs:
        if len(p) > max_chars:
            sentences = re.split(r'(?<=[.!?؟،؛\n])\s+', p)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if current_len + len(s) > max_chars and current_chunk:
                    result.append(" ".join(current_chunk))
                    current_chunk, current_len = [s], len(s)
                else:
                    current_chunk.append(s)
                    current_len += len(s)
        else:
            if current_len + len(p) > max_chars and current_chunk:
                result.append("\n".join(current_chunk))
                current_chunk, current_len = [p], len(p)
            else:
                current_chunk.append(p)
                current_len += len(p)

    if current_chunk:
        result.append("\n".join(current_chunk))
    return [r.strip() for r in result if r.strip()]

async def split_text_semantically(text: str, max_chars: int = 1000) -> list:
    return await asyncio.to_thread(_sync_split_text_semantically, text, max_chars)

def partition_chunks_by_target_size(chunk_paths: list, audio_format: str) -> list:
    MAX_SAFE_BYTES = 46 * 1024 * 1024
    max_batch = MAX_SAFE_BYTES / 5.5 if audio_format == "wav" else MAX_SAFE_BYTES * 0.95
    batches, current_batch, current_size = [], [], 0
    for cp in chunk_paths:
        if not os.path.exists(cp):
            continue
        c_size = os.path.getsize(cp)
        if current_batch and (current_size + c_size > max_batch):
            batches.append(current_batch)
            current_batch, current_size = [cp], c_size
        else:
            current_batch.append(cp)
            current_size += c_size
    if current_batch:
        batches.append(current_batch)
    return batches or [chunk_paths]

# ============ تمييز وتحليل اللغات ============
def _sync_classify_language(text: str, candidate_langs: list) -> str:
    if re.search(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]', text):
        return "ar" if "ar" in candidate_langs else candidate_langs[0]
    if HAS_LANGDETECT and len(text.strip()) >= 15:
        try:
            probs = detect_langs(text)
            for p in probs:
                if p.lang in candidate_langs and p.prob > 0.3:
                    return p.lang
        except Exception:
            pass
    if "en" in candidate_langs:
        return "en"
    return candidate_langs[0]

def _sync_parse_multilang_text(full_text: str, lang_slots: list):
    candidate_langs = [s["lang"] for s in lang_slots]
    lang_to_idx = {s["lang"]: i for i, s in enumerate(lang_slots)}
    segments = []
    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    for line in lines:
        sub_sentences = re.split(r'(?<=[.!?،؟])\s+', line)
        for sub in sub_sentences:
            sub = sub.strip()
            if not sub:
                continue
            has_ar = bool(re.search(r'[\u0600-\u06FF]', sub))
            has_lat = bool(re.search(r'[a-zA-Z]', sub))
            if has_ar and has_lat:
                chunks = re.findall(r'[\u0600-\u06FF0-9\s،؛؟]+|[^\u0600-\u06FF]+', sub)
                for ch in chunks:
                    c = ch.strip()
                    if c and re.search(r'\w', c):
                        matched = _sync_classify_language(c, candidate_langs)
                        segments.append((lang_to_idx.get(matched, 0), c))
            else:
                matched = _sync_classify_language(sub, candidate_langs)
                segments.append((lang_to_idx.get(matched, 0), sub))
    return segments

async def parse_multilang_text(full_text: str, lang_slots: list):
    return await asyncio.to_thread(_sync_parse_multilang_text, full_text, lang_slots)

def parse_story_dialogue(full_text: str, max_voices: int):
    parts = re.split(r'[\(\[]\s*[vV]([1-5])\s*[\)\]][: \t]*', full_text)
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

# ============ استخراج النصوص ============
def _sync_extract_text(path: str, ext: str) -> str:
    try:
        if ext == ".txt":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        if ext == ".docx" and HAS_DOCX:
            doc = DocxDocument(path)
            return "\n".join(p.text for p in doc.paragraphs)
        if ext == ".srt":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lines = [l.strip() for l in content.splitlines() if l.strip() and not re.match(r'^\d+$|^\d{2}:\d{2}', l)]
            return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Extract error: {e}")
    return ""

async def extract_text_from_file(path: str, ext: str) -> str:
    return await asyncio.to_thread(_sync_extract_text, path, ext)

# ============ إدارة الطابور لكل مستخدم ============
def get_user_queue(user_id: int) -> asyncio.Queue:
    if user_id not in USER_TASK_QUEUES:
        USER_TASK_QUEUES[user_id] = asyncio.Queue()
    return USER_TASK_QUEUES[user_id]

def get_tts_semaphore():
    global GLOBAL_TTS_SEMAPHORE
    if GLOBAL_TTS_SEMAPHORE is None:
        GLOBAL_TTS_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_TTS)
    return GLOBAL_TTS_SEMAPHORE

# ============ بطاقة عرض الإعدادات المستقلة في سجل الرسائل ============
def render_settings_card(config: dict) -> tuple:
    mode = config.get("mode", "single")
    fmt = config.get("audio_format", "mp3").upper()
    pause = config.get("pause_duration", 0.5)

    mode_titles = {
        "single": "🎙️ صوت فردي قياسي",
        "story": "🎬 محرك السيناريو والقصص (متعدد الشخصيات)",
        "multilang": "🌐 محرك متعدد اللغات الذكي"
    }

    text = f"⚙️ **سجل الإعدادات الحالية المشغلة:**\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📌 **الوضع النشط:** {mode_titles.get(mode, mode)}\n"
    text += f"📁 **صيغة الصوت:** `{fmt}`\n"

    if mode in ["story", "multilang"]:
        text += f"⏱️ **السكتة بين الأصوات:** `{pause:.1f} ثانية`\n"

    if mode == "story":
        slots = config.get("story_slots", [])
        text += f"\n🎭 **أصوات الشخصيات ({len(slots)}):**\n"
        for i, s in enumerate(slots, 1):
            p_label = "همس" if s.get("pitch") == "whisper" else s.get("pitch", "normal")
            vol_pct = int(round(s.get("volume", 1.0) * 100))
            text += f"  • `(v{i})` {s.get('voice')} | ⚡{s.get('speed', 1.0)}x | 🎵{p_label} | 🔊{vol_pct}%\n"

        bgm_on = config.get("bgm_enabled", False)
        if bgm_on:
            b_mode = "تكرار مستمر طوال السيناريو" if config.get("bgm_mode") == "loop" else f"محدد بـ {config.get('bgm_duration', 30)} ثانية"
            text += f"\n🎵 **موسيقى خلفية:** ✅ مفعلة ({b_mode})\n"
        else:
            text += f"\n🎵 **موسيقى خلفية:** ❌ معطلة\n"

    elif mode == "multilang":
        slots = config.get("multilang_slots", [])
        text += f"\n🌐 **الأصوات واللغات المحددة ({len(slots)}):**\n"
        for i, s in enumerate(slots, 1):
            p_label = "همس" if s.get("pitch") == "whisper" else s.get("pitch", "normal")
            vol_pct = int(round(s.get("volume", 1.0) * 100))
            text += f"  • [{s.get('lang')}] {s.get('voice')} | ⚡{s.get('speed', 1.0)}x | 🎵{p_label} | 🔊{vol_pct}%\n"

    else:
        v_name = config.get("voice_name", config.get("voice", "ar-EG-SalmaNeural"))
        p_label = "همس" if config.get("pitch") == "whisper" else config.get("pitch", "normal")
        vol_pct = int(round(config.get("volume", 1.0) * 100))
        text += f"🎙️ **الصوت:** `{v_name}`\n"
        text += f"⚡ **السرعة:** `{config.get('speed', 1.0)}x` | 🎵 **النبرة:** `{p_label}` | 🔊 **الارتفاع:** `{vol_pct}%`\n"

    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += "💡 *يمكنك تعديل أي خيار من الأزرار بالأسفل، أو إرسال أي نص/ملف وسيعمل فوراً بهذا الضبط.*"

    keyboard = []
    row1 = [
        InlineKeyboardButton("🔀 تغيير المحرك", callback_data="cfg_change_engine"),
        InlineKeyboardButton(f"📁 الصيغة ({fmt})", callback_data="cfg_change_format"),
    ]
    keyboard.append(row1)

    if mode in ["story", "multilang"]:
        row2 = [
            InlineKeyboardButton(f"⏱️ السكتة ({pause:.1f}s)", callback_data="cfg_cycle_pause"),
            InlineKeyboardButton("🎛️ تخصيص الأصوات", callback_data="cfg_open_customizer"),
        ]
        keyboard.append(row2)
        if mode == "story":
            keyboard.append([
                InlineKeyboardButton(
                    "🎵 موسيقى الخلفية (مفعلة)" if config.get("bgm_enabled") else "🎵 موسيقى الخلفية (معطلة)",
                    callback_data="cfg_toggle_bgm"
                ),
                InlineKeyboardButton("⏱️ مدة الموسيقى", callback_data="cfg_cycle_bgm_dur")
            ])
    else:
        keyboard.append([
            InlineKeyboardButton("⚡ ضبط السرعة والنبرة", callback_data="cfg_speed_pitch")
        ])

    return text, InlineKeyboardMarkup(keyboard)

# ============ المعالج الرئيسي للأمر /settings ============
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    cfg = get_saved_user_config(user_id)
    context.user_data.update(cfg)
    text, markup = render_settings_card(context.user_data)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

# ============ بداية معالج المحادثة ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    first_name = (update.effective_user.first_name or "صديقي") if update.effective_user else "صديقي"
    user_id = str(update.effective_user.id)
    saved = get_saved_user_config(user_id)
    context.user_data.update(saved)

    keyboard = [
        [InlineKeyboardButton(t(update, "eng_neural"), callback_data="eng_neural")],
        [InlineKeyboardButton(t(update, "eng_fast"), callback_data="eng_fast")],
        [InlineKeyboardButton(t(update, "eng_creative"), callback_data="eng_creative")],
        [InlineKeyboardButton(t(update, "eng_story"), callback_data="eng_story")],
        [InlineKeyboardButton(t(update, "eng_multilang"), callback_data="eng_multilang")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    welcome_msg = t(update, "welcome", name=first_name)
    if update.message:
        await update.message.reply_text(welcome_msg, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=markup, parse_mode="Markdown")
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
        title = t(update, "story_intro") if mode == "story" and slot_num == 1 else (
            t(update, "story_slot_prompt", num=slot_num) if mode == "story" else (
                t(update, "multilang_intro") if slot_num == 1 else t(update, "multilang_slot_prompt")
            )
        )
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
    engine = context.user_data.get("current_sub_engine" if mode in ["story", "multilang"] else "engine", "neural")
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
    engine = context.user_data.get("current_sub_engine" if mode in ["story", "multilang"] else "engine", "neural")

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

    slot_payload = {
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
        if editing_idx is not None and editing_idx < len(slots):
            slot_payload["speed"] = slots[editing_idx].get("speed", 1.0)
            slot_payload["pitch"] = slots[editing_idx].get("pitch", "normal")
            slot_payload["volume"] = slots[editing_idx].get("volume", 1.0)
            slots[editing_idx] = slot_payload
            context.user_data["editing_idx"] = None
        else:
            slots.append(slot_payload)
        context.user_data["story_slots"] = slots
        return await show_story_menu(update, context)

    elif mode == "multilang":
        slots = context.user_data.get("multilang_slots", [])
        lang_lbl = NATIVE_LANG_NAMES.get(lang_code, lang_code)
        slot_payload["label"] = f"{lang_lbl}: {voice_name}"
        if editing_idx is not None and editing_idx < len(slots):
            slot_payload["speed"] = slots[editing_idx].get("speed", 1.0)
            slot_payload["pitch"] = slots[editing_idx].get("pitch", "normal")
            slot_payload["volume"] = slots[editing_idx].get("volume", 1.0)
            slots[editing_idx] = slot_payload
            context.user_data["editing_idx"] = None
        else:
            slots.append(slot_payload)
        context.user_data["used_langs"] = {s["lang"] for s in slots}
        context.user_data["multilang_slots"] = slots
        return await show_multilang_menu(update, context)

    else:
        context.user_data["voice"] = voice_id
        context.user_data["voice_name"] = voice_name
        return await ask_speed(update, context)

# ============ قوائم السيناريو ومتعدد اللغات ============
async def show_story_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    slots = context.user_data.get("story_slots", [])
    buttons = []
    for i, s in enumerate(slots):
        p_desc = "همس" if s.get("pitch") == "whisper" else s.get("pitch", "normal")
        vol_pct = int(round(s.get("volume", 1.0) * 100))
        btn_txt = f"🎛️ (v{i+1}) {s['voice']} | ⚡{s.get('speed', 1.0)}x | 🔊{vol_pct}% | 🎵{p_desc}"
        buttons.append([InlineKeyboardButton(btn_txt, callback_data=f"custom_voice_{i}")])

    if len(slots) < 5:
        buttons.append([InlineKeyboardButton(t(update, "story_btn_add", num=len(slots) + 1), callback_data="story_add")])
    if len(slots) >= 2:
        buttons.append([InlineKeyboardButton(t(update, "story_btn_finish"), callback_data="story_finish")])
    buttons.append([InlineKeyboardButton(t(update, "story_btn_reset"), callback_data="story_reset")])
    await query.edit_message_text(t(update, "story_summary"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return STEP_STORY_MENU

async def handle_story_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("custom_voice_"):
        idx = int(data.replace("custom_voice_", ""))
        context.user_data["active_custom_idx"] = idx
        return await show_voice_customizer(update, context, idx, "story")
    elif data == "story_add":
        slot_num = len(context.user_data.get("story_slots", [])) + 1
        return await show_sub_engines(update, context, slot_num=slot_num, mode="story")
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
        p_desc = "همس" if s.get("pitch") == "whisper" else s.get("pitch", "normal")
        vol_pct = int(round(s.get("volume", 1.0) * 100))
        btn_txt = f"🎛️ {s['label']} | ⚡{s.get('speed', 1.0)}x | 🔊{vol_pct}% | 🎵{p_desc}"
        buttons.append([InlineKeyboardButton(btn_txt, callback_data=f"custom_voice_{i}")])

    if len(slots) < 3:
        remaining = 3 - len(slots)
        buttons.append([InlineKeyboardButton(t(update, "multilang_btn_add", num=remaining), callback_data="mlang_add")])
    if len(slots) >= 2:
        buttons.append([InlineKeyboardButton(t(update, "multilang_btn_finish"), callback_data="mlang_finish")])
    buttons.append([InlineKeyboardButton(t(update, "multilang_btn_reset"), callback_data="mlang_reset")])
    await query.edit_message_text(t(update, "multilang_summary"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return STEP_MULTILANG_MENU

async def handle_multilang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("custom_voice_"):
        idx = int(data.replace("custom_voice_", ""))
        context.user_data["active_custom_idx"] = idx
        return await show_voice_customizer(update, context, idx, "multilang")
    elif data == "mlang_add":
        slot_num = len(context.user_data.get("multilang_slots", [])) + 1
        return await show_sub_engines(update, context, slot_num=slot_num, mode="multilang")
    elif data == "mlang_finish":
        return await ask_speed(update, context)
    elif data == "mlang_reset":
        context.user_data["multilang_slots"] = []
        context.user_data["used_langs"] = set()
        context.user_data["editing_idx"] = None
        return await show_sub_engines(update, context, slot_num=1, mode="multilang")

# ============ شاشة تخصيص الصوت المنفرد (سرعة / نبرة همس / ارتفاع) ============
async def show_voice_customizer(update: Update, context: ContextTypes.DEFAULT_TYPE, idx: int, mode: str):
    query = update.callback_query
    slots = context.user_data.get("story_slots" if mode == "story" else "multilang_slots", [])
    if idx >= len(slots):
        return await (show_story_menu(update, context) if mode == "story" else show_multilang_menu(update, context))

    s = slots[idx]
    spd = s.get("speed", 1.0)
    pit = s.get("pitch", "normal")
    vol = s.get("volume", 1.0)

    title = (
        f"🎛️ **تخصيص صوت الشخصية (v{idx+1}):** `{s.get('voice')}`\n"
        f"⚡ **السرعة:** `{spd}x` | 🎵 **النبرة:** `{pit}` | 🔊 **الارتفاع:** `{int(vol*100)}%`\n\n"
        f"اختر ما تريد تعديله لهذا الصوت بعينه:"
    )

    spd_row = [
        InlineKeyboardButton(f"{'✅ ' if abs(spd-0.8)<0.05 else ''}0.8x", callback_data=f"cv_{idx}_spd_0.8"),
        InlineKeyboardButton(f"{'✅ ' if abs(spd-1.0)<0.05 else ''}1.0x", callback_data=f"cv_{idx}_spd_1.0"),
        InlineKeyboardButton(f"{'✅ ' if abs(spd-1.25)<0.05 else ''}1.25x", callback_data=f"cv_{idx}_spd_1.25"),
    ]

    pit_row = [
        InlineKeyboardButton(f"{'✅ ' if pit=='whisper' else ''}🤫 همس", callback_data=f"cv_{idx}_pit_whisper"),
        InlineKeyboardButton(f"{'✅ ' if pit=='low' else ''}خفيض", callback_data=f"cv_{idx}_pit_low"),
        InlineKeyboardButton(f"{'✅ ' if pit=='normal' else ''}عادي", callback_data=f"cv_{idx}_pit_normal"),
        InlineKeyboardButton(f"{'✅ ' if pit=='high' else ''}مرتفع", callback_data=f"cv_{idx}_pit_high"),
    ]

    vol_row = [
        InlineKeyboardButton(f"{'✅ ' if abs(vol-0.5)<0.05 else ''}🔉 خافت 50%", callback_data=f"cv_{idx}_vol_0.5"),
        InlineKeyboardButton(f"{'✅ ' if abs(vol-1.0)<0.05 else ''}🔊 عادي 100%", callback_data=f"cv_{idx}_vol_1.0"),
        InlineKeyboardButton(f"{'✅ ' if abs(vol-1.4)<0.05 else ''}📢 جهوري 140%", callback_data=f"cv_{idx}_vol_1.4"),
    ]

    back_btn = [
        InlineKeyboardButton("🔄 تغيير هذا الصوت كلياً", callback_data=f"reselect_voice_{idx}"),
        InlineKeyboardButton("✅ حفظ والعودة", callback_data="cv_done")
    ]

    markup = InlineKeyboardMarkup([spd_row, pit_row, vol_row, back_btn])
    await query.edit_message_text(title, reply_markup=markup, parse_mode="Markdown")
    return STEP_VOICE_CUSTOMIZE

async def handle_voice_customizer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    mode = context.user_data.get("mode", "story")
    slots_key = "story_slots" if mode == "story" else "multilang_slots"
    slots = context.user_data.get(slots_key, [])
    idx = context.user_data.get("active_custom_idx", 0)

    if data == "cv_done":
        return await (show_story_menu(update, context) if mode == "story" else show_multilang_menu(update, context))
    elif data.startswith(f"reselect_voice_{idx}"):
        context.user_data["editing_idx"] = idx
        return await show_sub_engines(update, context, slot_num=idx + 1, mode=mode)
    elif data.startswith(f"cv_{idx}_spd_"):
        val = float(data.split("_")[-1])
        if idx < len(slots):
            slots[idx]["speed"] = val
        return await show_voice_customizer(update, context, idx, mode)
    elif data.startswith(f"cv_{idx}_pit_"):
        val = data.split("_")[-1]
        if idx < len(slots):
            slots[idx]["pitch"] = val
        return await show_voice_customizer(update, context, idx, mode)
    elif data.startswith(f"cv_{idx}_vol_"):
        val = float(data.split("_")[-1])
        if idx < len(slots):
            slots[idx]["volume"] = val
        return await show_voice_customizer(update, context, idx, mode)

    return await (show_story_menu(update, context) if mode == "story" else show_multilang_menu(update, context))

# ============ الإعدادات الشاملة (السرعة، النبرة، السكتة، BGM) ============
async def ask_speed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    mode = context.user_data.get("mode", "single")
    speed = context.user_data.get("speed", 1.0)
    pitch = context.user_data.get("pitch", "normal")
    pause = context.user_data.get("pause_duration", 0.5)
    bgm_enabled = context.user_data.get("bgm_enabled", False)
    bgm_mode = context.user_data.get("bgm_mode", "loop")
    bgm_dur = context.user_data.get("bgm_duration", 30)

    buttons = []
    if mode == "single":
        speed_row = [
            InlineKeyboardButton(f"{'✅ ' if abs(speed-0.75)<0.05 else ''}0.75x", callback_data="spd_0.75"),
            InlineKeyboardButton(f"{'✅ ' if abs(speed-1.0)<0.05 else ''}1.0x", callback_data="spd_1.0"),
            InlineKeyboardButton(f"{'✅ ' if abs(speed-1.25)<0.05 else ''}1.25x", callback_data="spd_1.25"),
            InlineKeyboardButton(f"{'✅ ' if abs(speed-1.5)<0.05 else ''}1.5x", callback_data="spd_1.5"),
        ]
        pitch_row = [
            InlineKeyboardButton(f"{'✅ ' if pitch=='whisper' else ''}🤫 همس", callback_data="pit_whisper"),
            InlineKeyboardButton(f"{'✅ ' if pitch=='low' else ''}خفيض", callback_data="pit_low"),
            InlineKeyboardButton(f"{'✅ ' if pitch=='normal' else ''}عادي", callback_data="pit_normal"),
            InlineKeyboardButton(f"{'✅ ' if pitch=='high' else ''}مرتفع", callback_data="pit_high"),
        ]
        buttons.extend([speed_row, pitch_row])

    if mode in ["story", "multilang"]:
        pause_row = [
            InlineKeyboardButton(f"{'✅ ' if abs(pause-0.0)<0.05 else ''}بدون سكتة 0s", callback_data="pse_0.0"),
            InlineKeyboardButton(f"{'✅ ' if abs(pause-0.3)<0.05 else ''}0.3s", callback_data="pse_0.3"),
            InlineKeyboardButton(f"{'✅ ' if abs(pause-0.7)<0.05 else ''}0.7s", callback_data="pse_0.7"),
            InlineKeyboardButton(f"{'✅ ' if abs(pause-1.5)<0.05 else ''}1.5s", callback_data="pse_1.5"),
        ]
        buttons.append(pause_row)

    if mode == "story":
        bgm_btn = InlineKeyboardButton(
            "🎵 موسيقى الخلفية: ✅ مفعلة" if bgm_enabled else "🎵 موسيقى الخلفية: ❌ معطلة",
            callback_data="bgm_toggle"
        )
        bgm_mode_btn = InlineKeyboardButton(
            f"🔄 تكرار مستمر" if bgm_mode == "loop" else f"⏱️ محددة ({bgm_dur} ثانية)",
            callback_data="bgm_mode_toggle"
        )
        buttons.append([bgm_btn, bgm_mode_btn])

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
        context.user_data["speed"] = float(data.replace("spd_", ""))
        return await ask_speed(update, context)
    elif data.startswith("pit_"):
        context.user_data["pitch"] = data.replace("pit_", "")
        return await ask_speed(update, context)
    elif data.startswith("pse_"):
        context.user_data["pause_duration"] = float(data.replace("pse_", ""))
        return await ask_speed(update, context)
    elif data == "bgm_toggle":
        context.user_data["bgm_enabled"] = not context.user_data.get("bgm_enabled", False)
        return await ask_speed(update, context)
    elif data == "bgm_mode_toggle":
        cur_mode = context.user_data.get("bgm_mode", "loop")
        if cur_mode == "loop":
            context.user_data["bgm_mode"] = "timed"
            context.user_data["bgm_duration"] = 15
        elif context.user_data.get("bgm_duration", 15) == 15:
            context.user_data["bgm_duration"] = 30
        elif context.user_data.get("bgm_duration", 30) == 30:
            context.user_data["bgm_duration"] = 60
        else:
            context.user_data["bgm_mode"] = "loop"
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
    volume = context.user_data.get("volume", 1.0)

    sample = "مرحباً بك، هذه عينة تجريبية للصوت المختار."
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "preview.mp3")
            ok = await synthesize_chunk(sample, engine, voice, path, speed=speed, pitch=pitch, volume=volume)
            if ok and os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, "rb") as f:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id, audio=f,
                        title="معاينة الصوت", performer="Smart Studio",
                        caption=t(update, "preview_caption")
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
    text = t(update, "step_format")
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

    save_user_config(str(update.effective_user.id), context.user_data)

    mode = context.user_data.get("mode", "single")
    prompt_text = (
        f"{t(update, 'story_step_text')}\n\n📁 الصيغة المحددة: `({chosen_format.upper()})`"
        if mode == "story" else (
            f"{t(update, 'multilang_step_text')}\n\n📁 الصيغة المحددة: `({chosen_format.upper()})`"
            if mode == "multilang" else
            f"{t(update, 'step_text')}\n📁 الصيغة المحددة: `({chosen_format.upper()})`"
        )
    )
    prompt_text += "\n\n💡 *تم حفظ هذا الضبط في سجل رسائلك، ويمكنك مراجعته وتعديله بأي وقت عبر /settings*"
    await query.edit_message_text(prompt_text, parse_mode="Markdown")
    return STEP_TEXT

# ============ أزرار التحكم من داخل سجل الإعدادات /settings ============
async def handle_settings_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(update.effective_user.id)
    cfg = get_saved_user_config(user_id)
    context.user_data.update(cfg)

    if data == "cfg_change_engine":
        return await start(update, context)

    elif data == "cfg_change_format":
        fmts = ["mp3", "wav", "ogg"]
        cur = context.user_data.get("audio_format", "mp3")
        nxt = fmts[(fmts.index(cur) + 1) % len(fmts)]
        context.user_data["audio_format"] = nxt
        save_user_config(user_id, context.user_data)

    elif data == "cfg_cycle_pause":
        pauses = [0.0, 0.3, 0.7, 1.5, 2.5]
        cur = round(context.user_data.get("pause_duration", 0.5), 1)
        closest = min(pauses, key=lambda x: abs(x - cur))
        nxt = pauses[(pauses.index(closest) + 1) % len(pauses)]
        context.user_data["pause_duration"] = nxt
        save_user_config(user_id, context.user_data)

    elif data == "cfg_toggle_bgm":
        context.user_data["bgm_enabled"] = not context.user_data.get("bgm_enabled", False)
        save_user_config(user_id, context.user_data)

    elif data == "cfg_cycle_bgm_dur":
        cur_mode = context.user_data.get("bgm_mode", "loop")
        if cur_mode == "loop":
            context.user_data["bgm_mode"] = "timed"
            context.user_data["bgm_duration"] = 15
        elif context.user_data.get("bgm_duration", 15) == 15:
            context.user_data["bgm_duration"] = 30
        elif context.user_data.get("bgm_duration", 30) == 30:
            context.user_data["bgm_duration"] = 60
        else:
            context.user_data["bgm_mode"] = "loop"
        save_user_config(user_id, context.user_data)

    elif data == "cfg_open_customizer":
        mode = context.user_data.get("mode", "story")
        return await (show_story_menu(update, context) if mode == "story" else show_multilang_menu(update, context))

    elif data == "cfg_speed_pitch":
        return await ask_speed(update, context)

    text, markup = render_settings_card(context.user_data)
    await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")

# ============ معالجة الطابور والتنفيذ ============
async def user_queue_worker(user_id: int):
    q = get_user_queue(user_id)
    while True:
        task_data = await q.get()
        if task_data is None:
            break
        update, context, text, task_label = task_data
        USER_CURRENT_TASK[user_id] = task_label
        try:
            await execute_generation(update, context, text)
        except Exception as e:
            logger.error(f"Queue error: {e}", exc_info=True)
            try:
                await update.message.reply_text(t(update, "error"))
            except Exception:
                pass
        finally:
            USER_CURRENT_TASK.pop(user_id, None)
            q.task_done()

def ensure_user_worker(user_id: int):
    worker = USER_WORKERS.get(user_id)
    if worker is None or worker.done():
        USER_WORKERS[user_id] = asyncio.create_task(user_queue_worker(user_id))

async def enqueue_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, task_label: str):
    user_id = update.effective_user.id
    ensure_user_worker(user_id)
    if user_id in USER_CURRENT_TASK:
        curr_label = USER_CURRENT_TASK[user_id]
        await update.message.reply_text(
            t(update, "queue_multi_file", curr=curr_label, new=task_label),
            parse_mode="Markdown"
        )
    q = get_user_queue(user_id)
    await q.put((update, context, text, task_label))

async def execute_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    user_id = str(update.effective_user.id)
    saved = get_saved_user_config(user_id)
    context.user_data.update(saved)

    sem = get_tts_semaphore()
    status_msg = await update.message.reply_text(t(update, "generating"), parse_mode="Markdown")

    async with sem:
        try:
            await _do_generate(update, context, user_text, status_msg)
        except Exception as e:
            logger.error(f"Generation error: {e}", exc_info=True)
            try:
                await status_msg.edit_text(t(update, "error"))
            except Exception:
                pass

async def _do_generate(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, status_msg):
    mode = context.user_data.get("mode", "single")
    audio_format = context.user_data.get("audio_format", "mp3")
    pause_duration = context.user_data.get("pause_duration", 0.5)

    bgm_config = {
        "enabled": context.user_data.get("bgm_enabled", False),
        "mode": context.user_data.get("bgm_mode", "loop"),
        "duration": context.user_data.get("bgm_duration", 30)
    }

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
            if not slots:
                raise Exception("No character slots configured")
            segments = parse_story_dialogue(user_text, len(slots))
            for i, (v_idx, seg_text) in enumerate(segments):
                slot = slots[v_idx]
                sub_pieces = await split_text_semantically(seg_text, max_chars=1000)
                for j, sp in enumerate(sub_pieces):
                    cf = os.path.join(tmpdir, f"chunk_{i}_{j}.mp3")
                    if await synthesize_chunk(
                        sp, slot["engine"], slot["voice"], cf,
                        speed=slot.get("speed", 1.0),
                        pitch=slot.get("pitch", "normal"),
                        volume=slot.get("volume", 1.0)
                    ):
                        chunks.append(cf)
            caption_details = f"🎬 **سيناريو: {len(slots)} شخصيات** | ⏱️ سكتة: `{pause_duration}s`"

        elif mode == "multilang":
            slots = context.user_data.get("multilang_slots", [])
            if not slots:
                raise Exception("No multilang slots configured")
            segments = await parse_multilang_text(user_text, slots)
            for i, (v_idx, seg_text) in enumerate(segments):
                slot = slots[v_idx]
                sub_pieces = await split_text_semantically(seg_text, max_chars=1000)
                for j, sp in enumerate(sub_pieces):
                    cf = os.path.join(tmpdir, f"chunk_ml_{i}_{j}.mp3")
                    if await synthesize_chunk(
                        sp, slot["engine"], slot["voice"], cf,
                        speed=slot.get("speed", 1.0),
                        pitch=slot.get("pitch", "normal"),
                        volume=slot.get("volume", 1.0)
                    ):
                        chunks.append(cf)
            caption_details = f"🌐 **متعدد لغات: {len(slots)} أصوات** | ⏱️ سكتة: `{pause_duration}s`"

        else:
            engine = context.user_data.get("engine", "neural")
            voice = context.user_data.get("voice", "ar-EG-SalmaNeural")
            spd = context.user_data.get("speed", 1.0)
            pit = context.user_data.get("pitch", "normal")
            vol = context.user_data.get("volume", 1.0)
            text_chunks = await split_text_semantically(user_text, max_chars=1000)
            for i, tch in enumerate(text_chunks):
                cf = os.path.join(tmpdir, f"chunk_{i}.mp3")
                if await synthesize_chunk(tch, engine, voice, cf, speed=spd, pitch=pit, volume=vol):
                    chunks.append(cf)
            caption_details = f"🎙️ `{voice}` | ⚡{spd}x | 🎵{pit}"

        if not chunks:
            raise Exception("No audio chunks generated")

        batches = partition_chunks_by_target_size(chunks, audio_format)
        total_parts = len(batches)

        for part_idx, batch_chunks in enumerate(batches, 1):
            part_path = os.path.join(tmpdir, f"part_{part_idx}.{audio_format}")
            await merge_audio_files(
                batch_chunks, part_path, audio_format=audio_format,
                pause_duration=pause_duration, bgm_config=bgm_config
            )

            part_label = t(update, "part_label", part=part_idx, total=total_parts) if total_parts > 1 else ""
            is_last = (part_idx == total_parts)
            hint = t(update, 'new_text_hint') if is_last else ""
            caption = f"{t(update, 'done')}\n{part_label}{caption_details}\n📁 `{audio_format.upper()}`{hint}"

            with open(part_path, "rb") as audio_file:
                if audio_format == "ogg":
                    await update.message.reply_voice(voice=audio_file, caption=caption, parse_mode="Markdown")
                else:
                    title_str = f"Studio Part {part_idx}" if total_parts > 1 else "Smart TTS Studio"
                    await update.message.reply_audio(
                        audio=audio_file, title=title_str,
                        performer="Smart TTS Engine", caption=caption, parse_mode="Markdown"
                    )

        try:
            await status_msg.delete()
        except Exception:
            pass

# ============ استقبال النصوص والمستندات ============
async def generate_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_text = (update.message.text or "").strip()
    if not user_text:
        return STEP_TEXT
    label = user_text[:20] + "..." if len(user_text) > 20 else user_text
    await enqueue_generation(update, context, user_text, task_label=label)
    return STEP_TEXT

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        return STEP_TEXT
    file_name = doc.file_name or "file.txt"
    ext = os.path.splitext(file_name)[1].lower()

    if ext not in (".txt", ".docx", ".srt"):
        await update.message.reply_text(t(update, "doc_unsupported"))
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
        text = await extract_text_from_file(tmp_path, ext)
        if not text.strip():
            await status.edit_text(t(update, "doc_no_text"))
            return STEP_TEXT
        await status.delete()
        await enqueue_generation(update, context, text, task_label=file_name)
        return STEP_TEXT
    except Exception as e:
        logger.error(f"Doc error: {e}")
        try:
            await status.edit_text(t(update, "error"))
        except Exception:
            pass
        return STEP_TEXT
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ============ استجابة مباشرة خارج دورة start ============
async def direct_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    saved = get_saved_user_config(user_id)
    context.user_data.update(saved)
    return await generate_audio(update, context)

async def direct_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    saved = get_saved_user_config(user_id)
    context.user_data.update(saved)
    return await handle_document(update, context)

async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.callback_query.answer()
    except Exception:
        pass

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم إلغاء المسار. أرسل /settings لضبط إعداداتك أو أرسل نصك مباشرة.")
    return ConversationHandler.END

# ============ بدء التشغيل والإغلاق ============
async def on_startup(app):
    print("⏳ جاري فحص وتحضير مكتبات الصوت...")
    await init_neural_json()
    init_fast_json()
    init_creative_json()
    print("✅ تم بنجاح تشغيل البوت الذكي بنظام سجل الرسائل والتخصيص الكامل!")

async def on_shutdown(app):
    global GLOBAL_HTTP_SESSION
    if GLOBAL_HTTP_SESSION and not GLOBAL_HTTP_SESSION.closed:
        await GLOBAL_HTTP_SESSION.close()
    for worker in USER_WORKERS.values():
        worker.cancel()

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(16)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(60)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
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
            STEP_STORY_MENU: [CallbackQueryHandler(handle_story_menu, pattern="^(story_|custom_voice_)")],
            STEP_MULTILANG_MENU: [CallbackQueryHandler(handle_multilang_menu, pattern="^(mlang_|custom_voice_)")],
            STEP_VOICE_CUSTOMIZE: [CallbackQueryHandler(handle_voice_customizer, pattern="^(cv_|reselect_voice_)")],
            STEP_SPEED: [CallbackQueryHandler(handle_speed_selection, pattern="^(spd_|pit_|pse_|bgm_)")],
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

    # معالج أمر الإعدادات المستقل وزر تحكم سجل الرسائل
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CallbackQueryHandler(handle_settings_callbacks, pattern="^cfg_"))

    app.add_handler(conv_handler)
    # معالجات مباشرة تسمح بالتحويل الفوري لأي نص يرسل مباشرة بدون إعادة الإعدادات
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, direct_message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, direct_document_handler))

    print("🚀 البوت متصل وجاهز للعمل بأقصى سرعة وإبداع...")
    app.run_polling(close_loop=False, drop_pending_updates=True)

if __name__ == "__main__":
    main()