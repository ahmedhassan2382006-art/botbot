import os
import re
import json
import uuid
import base64
import logging
import shutil
import asyncio
import subprocess
import aiohttp
from gtts import gTTS
import edge_tts
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# التوكن الخاص بك
BOT_TOKEN = os.environ.get("BOT_TOKEN")

FILE_NEURAL_JSON = "neural_voices.json"
FILE_FAST_JSON = "fast_voices.json"
FILE_CREATIVE_JSON = "creative_voices.json"

(
    STEP_ENGINE,
    STEP_LANG,
    STEP_DIALECT,
    STEP_VOICE,
    STEP_STORY_MENU,
    STEP_MULTILANG_MENU,
    STEP_FORMAT,
    STEP_TEXT,
) = range(8)

DB_NEURAL = {}
DB_FAST = {}
DB_CREATIVE = {}

NATIVE_LANG_NAMES = {
    "ar": "العربية",
    "en": "English",
    "tr": "Türkçe",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Português",
    "ru": "Русский",
    "zh": "中文",
    "ja": "日本語",
    "ko": "한국어",
    "hi": "हिन्दी",
    "ur": "اردو",
    "fa": "فارسی",
    "nl": "Nederlands",
    "pl": "Polski",
    "sv": "Svenska",
    "id": "Bahasa Indonesia",
    "el": "Ελληνικά",
    "uk": "Українська",
    "vi": "Tiếng Việt",
    "th": "ไทย",
    "ro": "Română",
    "cs": "Čeština",
    "hu": "Magyar"
}

I18N = {
    "ar": {
        "welcome": (
            "مرحباً بك في بوت تحويل النص إلى صوت الذكي!\n"
            "المطور: **أحمد حسن عبد النبي** 👨‍💻\n\n"
            "🔹 **الخطوة 1:** اختر المحرك الصوتي المطلوب:"
        ),
        "step_engine": "🔹 **الخطوة 1:** اختر المحرك الصوتي:",
        "step_lang": "🔹 **الخطوة 2:** اختر اللغة:",
        "step_dialect": "🔹 **الخطوة 3:** اختر اللهجة / الدولة:",
        "step_voice": "🔹 **الخطوة 4:** اختر الصوت:",
        "step_format": "🔹 **الخطوة 5:** اختر صيغة الملف الصوتي:",
        "step_text": "🔹 **الخطوة 6:** أرسل النص المراد تحويله إلى صوت الآن:",
        
        "story_intro": "🎬 **محرك القصص والحوارات**\nاختر من 2 إلى 5 شخصيات.\nاختر المحرك الصوتي للشخصية الأولى (v1):",
        "story_slot_prompt": "🔹 اختر المحرك الصوتي للشخصية (v{num}):",
        "story_summary": "🎙️ **طاقم الحوار الحالي:**\nاضغط على أي شخصية أدناه إذا أردت **تعديلها**:",
        "story_btn_add": "➕ إضافة شخصية جديدة (v{num})",
        "story_btn_finish": "🎬 انتهاء الحوار (متابعة للصيغة والنص)",
        "story_btn_reset": "🔄 إعادة تعيين الشخصيات",
        "story_step_text": (
            "🔹 **أرسل نص سيناريو الحوار.**\n\n"
            "💡 **طريقة الكتابة:** ضع رمز الشخصية `(vالرقم)` في بداية كلامها:\n\n"
            "*(v1) مرحباً يا أحمد، هل أتممت كتابة التقرير؟*\n"
            "*(v2) نعم يا سارة، انتهيت منه للتو وسأرسله لكِ الآن.*\n"
            "*(v1) ممتاز جداً، أشكرك!*"
        ),

        "multilang_intro": (
            "🌐 **محرك متعدد اللغات التلقائي**\n\n"
            "يتعرف تلقائياً على لغات النص ويوزع كل لغة على صوتها الأصلي في ملف واحد!\n"
            "⚠️ **الشرط:** (من 2 إلى 3 أصوات كحد أقصى) وكل صوت للغة مختلفة تماماً.\n\n"
            "🔹 اختر المحرك الصوتي للغة الأولى:"
        ),
        "multilang_slot_prompt": "🔹 اختر المحرك الصوتي للغة التالية:",
        "multilang_summary": "🌐 **اللغات والأصوات المختارة حالياً:**\nاضغط على أي صوت أدناه إذا أردت **تعديله**:",
        "multilang_btn_add": "➕ إضافة لغة وصوت إضافي (متبقي {num})",
        "multilang_btn_finish": "➡️ اعتماد اللغات والانتقال للصيغة",
        "multilang_btn_reset": "🔄 إعادة ضبط اللغات",
        "multilang_step_text": (
            "🔹 **أرسل النص المختلط باللغات المتعددة الآن.**\n\n"
            "سيتعرف البوت تلقائياً على كل فقرة وجملة ولغتها، وينطقها بالصوت المخصص لها ثم يدمج الكل في ملف صوتي واحد متناسق."
        ),

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
    },
    "en": {
        "welcome": (
            "Welcome to the Smart Text-to-Speech Bot!\n"
            "Developer: **Ahmed Hassan Abdelnaby** 👨‍💻\n\n"
            "🔹 **Step 1:** Select the audio engine:"
        ),
        "step_engine": "🔹 **Step 1:** Select the audio engine:",
        "step_lang": "🔹 **Step 2:** Select language:",
        "step_dialect": "🔹 **Step 3:** Select dialect / region:",
        "step_voice": "🔹 **Step 4:** Select voice:",
        "step_format": "🔹 **Step 5:** Select audio format / extension:",
        "step_text": "🔹 **Step 6:** Send the text to convert to audio now:",
        
        "story_intro": "🎬 **Dialogue & Story Engine**\nChoose 2 to 5 characters.\nSelect engine for (v1):",
        "story_slot_prompt": "🔹 Select engine for character (v{num}):",
        "story_summary": "🎙️ **Current Story Cast:**\nTap any character below to **edit** it:",
        "story_btn_add": "➕ Add another character (v{num})",
        "story_btn_finish": "🎬 Finish Dialogue (Proceed to format & text)",
        "story_btn_reset": "🔄 Reset Cast",
        "story_step_text": (
            "🔹 **Send your dialogue script.**\n\n"
            "💡 **Format:** Prefix turns with `(vNumber)`:\n\n"
            "*(v1) Hello friend, how are you?*\n"
            "*(v2) I'm doing great, thank you! How about you?*\n"
            "*(v1) Wonderful to hear that!*"
        ),

        "multilang_intro": (
            "🌐 **Automatic Multilingual Engine**\n\n"
            "Automatically detects languages in a single text and switches voices natively!\n"
            "⚠️ **Rule:** (2 to 3 voices max), each voice must belong to a different language.\n\n"
            "🔹 Select engine for the first language:"
        ),
        "multilang_slot_prompt": "🔹 Select engine for the next language:",
        "multilang_summary": "🌐 **Configured languages & voices:**\nTap any voice below to **edit** it:",
        "multilang_btn_add": "➕ Add another language voice ({num} left)",
        "multilang_btn_finish": "➡️ Proceed with selected languages",
        "multilang_btn_reset": "🔄 Reset languages",
        "multilang_step_text": (
            "🔹 **Send your mixed-language text now.**\n\n"
            "The bot will automatically detect each language segment and read it natively into one combined audio."
        ),

        "generating": "⏳ **Analyzing languages, synthesizing voices, and merging audio...**",
        "done": "✅ Audio generated and merged successfully!",
        "new_text_hint": "\n\n💡 Send a new text to convert with the same settings, or type /start to restart.",
        "back": "🔙 Back",
        "eng_neural": "Ultra Neural Engine (Studio Quality)",
        "eng_fast": "Fast Standard Engine (Lightweight)",
        "eng_creative": "Expressive Creative Engine (Characters)",
        "eng_story": "Story & Dialogue Engine (2-5 Voices) 🎬",
        "eng_multilang": "Multilingual Engine (Auto Language Switch) 🌐",
        "error": "⚠️ An error occurred while generating audio. Please check your text.",
    }
}

def t(update: Update, key: str) -> str:
    user_lang = "ar"
    if update.effective_user and update.effective_user.language_code:
        if not update.effective_user.language_code.startswith("ar"):
            user_lang = "en"
    return I18N[user_lang].get(key, I18N["ar"].get(key, ""))

# ======================== 1. إدارة ملفات الجيسون ========================

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
            db[lang_code] = {
                "name": NATIVE_LANG_NAMES.get(lang_code, lang_code.upper()),
                "dialects": {}
            }

        region_clean = friendly.split("(")[-1].replace(")", "").strip() if "(" in friendly else loc
        if loc not in db[lang_code]["dialects"]:
            db[lang_code]["dialects"][loc] = {
                "name": region_clean,
                "voices": []
            }

        voice_person = short_name.split("-")[-1].replace("Neural", "")
        gender_sign = "👩" if gender == "Female" else "👨"
        db[lang_code]["dialects"][loc]["voices"].append({
            "id": short_name,
            "name": f"{voice_person} ({gender_sign})"
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
        clean_name = NATIVE_LANG_NAMES.get(code, original_name)
        db[code] = {
            "name": clean_name,
            "code": code
        }

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
        "en": {
            "name": "English",
            "voices": [
                {"id": "en_us_001", "name": "Narrator (Female)"},
                {"id": "en_us_006", "name": "Joey (Male)"},
                {"id": "en_us_007", "name": "Professor (Male)"},
                {"id": "en_us_009", "name": "Scientist (Male)"},
                {"id": "en_male_narration", "name": "Storyteller (Male)"},
                {"id": "en_male_funny", "name": "Wacky (Male)"},
                {"id": "en_female_emotional", "name": "Peaceful (Female)"},
                {"id": "en_us_ghostface", "name": "Ghostface"},
                {"id": "en_us_chewbacca", "name": "Chewbacca"}
            ]
        },
        "fr": {
            "name": "Français",
            "voices": [
                {"id": "fr_001", "name": "Voix 1 (Male)"},
                {"id": "fr_002", "name": "Voix 2 (Male)"}
            ]
        },
        "es": {
            "name": "Español",
            "voices": [
                {"id": "es_001", "name": "Voz 1 (Male)"},
                {"id": "es_mx_002", "name": "Voz Mexicana (Male)"}
            ]
        },
        "de": {
            "name": "Deutsch",
            "voices": [
                {"id": "de_001", "name": "Stimme 1 (Female)"},
                {"id": "de_002", "name": "Stimme 2 (Male)"}
            ]
        },
        "ja": {
            "name": "日本語",
            "voices": [
                {"id": "jp_001", "name": "女性 1 (Female)"},
                {"id": "jp_005", "name": "男性 (Male)"}
            ]
        }
    }
    with open(FILE_CREATIVE_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    DB_CREATIVE = db

# ======================== 2. دورة عمل البوت ========================

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
    total_pages = (len(lang_keys) + page_size - 1) // page_size
    current_keys = lang_keys[page * page_size : (page + 1) * page_size]

    buttons = []
    row = []
    for k in current_keys:
        row.append(InlineKeyboardButton(db_dict[k]["name"], callback_data=f"lang_{k}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
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
        # إخفاء اللغات المستخدمة مسبقاً لمنع التكرار نهائياً
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
        buttons = []
        row = []
        for loc_code, loc_data in dialects.items():
            row.append(InlineKeyboardButton(loc_data["name"], callback_data=f"dia_{loc_code}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
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
        buttons = []
        for v in voices:
            buttons.append([InlineKeyboardButton(v["name"], callback_data=f"voc_{v['id']}")])
        buttons.append([InlineKeyboardButton(t(update, "back"), callback_data="back_to_lang")])
        await query.edit_message_text(t(update, "step_voice"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
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
    buttons = []
    row = []
    for v in voices:
        row.append(InlineKeyboardButton(v["name"], callback_data=f"voc_{v['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(t(update, "back"), callback_data=f"lang_{lang_code}")])
    await query.edit_message_text(t(update, "step_voice"), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return STEP_VOICE

async def select_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    lang_code = context.user_data.get("selected_lang", "ar")
    if query.data == "back_to_lang":
        mode = context.user_data.get("mode", "")
        engine = context.user_data.get("current_sub_engine" if mode in ["story", "multilang"] else "engine", "creative")
        return await setup_language_selection(update, context, engine)

    voice_id = query.data.replace("voc_", "")
    return await on_voice_selected(update, context, voice_id=voice_id, voice_name=voice_id, lang_code=lang_code)

async def on_voice_selected(update: Update, context: ContextTypes.DEFAULT_TYPE, voice_id: str, voice_name: str, lang_code: str) -> int:
    mode = context.user_data.get("mode", "single")
    editing_idx = context.user_data.get("editing_idx", None)

    if mode == "story":
        sub_engine = context.user_data.get("current_sub_engine", "neural")
        slots = context.user_data.get("story_slots", [])
        
        slot_data = {
            "engine": sub_engine,
            "voice": voice_id,
            "lang": lang_code,
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
        used_langs = context.user_data.get("used_langs", set())
        
        lang_label = NATIVE_LANG_NAMES.get(lang_code, lang_code)
        slot_data = {
            "engine": sub_engine,
            "voice": voice_id,
            "lang": lang_code,
            "label": f"{lang_label}: {voice_name}"
        }

        if editing_idx is not None and editing_idx < len(slots):
            slots[editing_idx] = slot_data
            context.user_data["editing_idx"] = None
        else:
            slots.append(slot_data)

        # تحديث اللغات المستخدمة
        used_langs = {s["lang"] for s in slots}
        context.user_data["used_langs"] = used_langs
        context.user_data["multilang_slots"] = slots

        return await show_multilang_menu(update, context)

    else:
        context.user_data["voice"] = voice_id
        return await ask_format(update, context)

# --- قائمة محرك القصص كأزرار تفاعلية للتعديل ---
async def show_story_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    slots = context.user_data.get("story_slots", [])

    buttons = []
    # عرض كل شخصية كزر لتعديلها
    for i, s in enumerate(slots):
        buttons.append([InlineKeyboardButton(f"✏️ (v{i+1}) {s['voice']}", callback_data=f"edit_story_{i}")])

    # زر الإضافة يختفي تماماً إذا اكتمل 5 أصوات
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
        return await ask_format(update, context)

    elif data == "story_reset":
        context.user_data["story_slots"] = []
        context.user_data["editing_idx"] = None
        return await show_sub_engines(update, context, slot_num=1, mode="story")

# --- قائمة محرك متعدد اللغات كأزرار تفاعلية للتعديل ---
async def show_multilang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    slots = context.user_data.get("multilang_slots", [])

    buttons = []
    # عرض كل صوت كزر لتعديله
    for i, s in enumerate(slots):
        buttons.append([InlineKeyboardButton(f"✏️ {i+1}️⃣ {s['label']}", callback_data=f"edit_mlang_{i}")])

    # زر الإضافة يختفي تلقائياً عند اكتمال 3 أصوات
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
        # فك حجز لغة هذا الصوت مؤقتاً لتعديله
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
        return await ask_format(update, context)

    elif data == "mlang_reset":
        context.user_data["multilang_slots"] = []
        context.user_data["used_langs"] = set()
        context.user_data["editing_idx"] = None
        return await show_sub_engines(update, context, slot_num=1, mode="multilang")

# --- اختيار صيغة وامتداد الملف ---
async def ask_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    formats = [
        [InlineKeyboardButton("MP3 (الأكثر توافقاً)", callback_data="fmt_mp3")],
        [InlineKeyboardButton("WAV (صوت نقي عالي الجودة)", callback_data="fmt_wav")],
        [InlineKeyboardButton("OGG (خفيف وسريع)", callback_data="fmt_ogg")],
    ]
    text = t(update, "step_format")
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(formats), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(formats), parse_mode="Markdown")
    return STEP_FORMAT

async def select_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chosen_format = query.data.replace("fmt_", "")
    context.user_data["audio_format"] = chosen_format

    mode = context.user_data.get("mode", "single")
    if mode == "story":
        prompt_text = f"{t(update, 'story_step_text')}\n\n📁 الصيغة المحددة: `({chosen_format.upper()})`"
    elif mode == "multilang":
        prompt_text = f"{t(update, 'multilang_step_text')}\n\n📁 الصيغة المحددة: `({chosen_format.upper()})`"
    else:
        prompt_text = f"{t(update, 'step_text')}\n📁 الصيغة المحددة: `({chosen_format.upper()})`"

    await query.edit_message_text(prompt_text, parse_mode="Markdown")
    return STEP_TEXT

# ======================== 3. التوليد والدمج الذكي الخالي من الأخطاء ========================

async def synthesize_chunk(text: str, engine: str, voice: str, out_file: str):
    """توليد مقطع صوتي نقي ومستقر"""
    clean_text = text.strip()
    if not clean_text or not re.search(r'[\w\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]', clean_text):
        return False

    if engine == "neural":
        comm = edge_tts.Communicate(text=clean_text, voice=voice)
        await comm.save(out_file)
        return True
    elif engine == "fast":
        tts = gTTS(text=clean_text, lang=voice)
        tts.save(out_file)
        return True
    else:
        api_url = "https://tiktok-tts.weilnet.workers.dev/api/generation"
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json={"text": clean_text, "voice": voice}) as resp:
                data = await resp.json()
                if "data" in data and data["data"]:
                    with open(out_file, "wb") as f:
                        f.write(base64.b64decode(data["data"]))
                    return True
                else:
                    raise Exception("Creative TTS error")

async def merge_audio_files(file_list: list, output_file: str):
    """دمج المقاطع الصوتية في ملف نهائي سليم"""
    valid_files = [fp for fp in file_list if os.path.exists(fp) and os.path.getsize(fp) > 0]
    if not valid_files:
        raise Exception("No audio chunks to merge")

    if shutil.which("ffmpeg"):
        try:
            list_txt = f"concat_{uuid.uuid4().hex}.txt"
            with open(list_txt, "w", encoding="utf-8") as f:
                for fp in valid_files:
                    abs_p = os.path.abspath(fp).replace("\\", "/")
                    f.write(f"file '{abs_p}'\n")

            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", output_file]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await proc.communicate()

            if os.path.exists(list_txt):
                os.remove(list_txt)
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                return
        except Exception as e:
            logger.warning(f"ffmpeg merge failed: {e}")

    # الدمج المباشر السلس لتدفقات MP3
    with open(output_file, "wb") as out_f:
        for fp in valid_files:
            with open(fp, "rb") as in_f:
                out_f.write(in_f.read())

def parse_story_dialogue(full_text: str, max_voices: int):
    """تحليل سيناريو الحوار عند كتابة (v1) و (v2)..."""
    pattern = r'[\(\[]\s*[vV]([1-5])\s*[\)\]][: \t]*'
    parts = re.split(pattern, full_text)

    segments = []
    # إذا بدأ النص بكلام قبل كتابة أي رمز نعتبره للشخصية الأولى
    if parts[0].strip():
        segments.append((0, parts[0].strip()))

    for i in range(1, len(parts), 2):
        v_idx = int(parts[i]) - 1
        content = parts[i + 1].strip()
        if content:
            if v_idx >= max_voices:
                v_idx = 0
            segments.append((v_idx, content))

    return segments

def classify_text_language(text: str, candidate_langs: list) -> str:
    """كشف لغة المقطع بدقة بين اللغات المختارة بدون أي لبس"""
    # 1. الأبجدية العربية ومشتقاتها
    if re.search(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]', text):
        for candidate in ["ar", "fa", "ur"]:
            if candidate in candidate_langs:
                return candidate
        return candidate_langs[0]

    # 2. اللغات السيريلية
    if re.search(r'[\u0400-\u04FF]', text) and "ru" in candidate_langs:
        return "ru"

    # 3. الصينية واليابانية والكورية
    if re.search(r'[\u4E00-\u9FFF]', text) and "zh" in candidate_langs:
        return "zh"
    if re.search(r'[\u3040-\u30FF]', text) and "ja" in candidate_langs:
        return "ja"
    if re.search(r'[\uAC00-\uD7AF]', text) and "ko" in candidate_langs:
        return "ko"

    # 4. التفريق الذكي الدقيق بين اللغات المعتمدة على الأبجدية اللاتينية:
    scores = {lang: 0 for lang in candidate_langs}
    lower_text = text.lower()
    words = set(re.findall(r'\b\w+\b', lower_text))

    # التركية (Turkish)
    if "tr" in candidate_langs:
        tr_chars = len(re.findall(r'[çğıöşü]', lower_text))
        tr_words = {"ve", "bir", "bu", "da", "de", "için", "ile", "çok", "gibi", "daha", "kadar", "yok", "var", "ama", "değil", "şunu", "kolay", "zor", "şimdi", "dil", "türkçe", "ingilizce", "biz", "siz", "herkese", "sakınca", "anlatmaya"}
        scores["tr"] = (tr_chars * 10) + (len(words & tr_words) * 5)

    # الإنجليزية (English)
    if "en" in candidate_langs:
        en_words = {"the", "and", "of", "to", "in", "is", "you", "that", "it", "we", "for", "on", "are", "as", "with", "they", "at", "be", "this", "have", "from", "today", "god", "willing", "talk", "speak", "english", "well", "way", "rules", "words", "nouns", "verbs", "game", "hope"}
        scores["en"] = len(words & en_words) * 5

    # الإسبانية (Spanish)
    if "es" in candidate_langs:
        es_chars = len(re.findall(r'[ñáéíóúü¿¡]', lower_text))
        es_words = {"el", "la", "los", "las", "y", "de", "en", "un", "una", "que", "por", "para", "con", "no", "si", "hola", "gracias", "amigo", "como", "pero"}
        scores["es"] = (es_chars * 10) + (len(words & es_words) * 5)

    # الفرنسية (French)
    if "fr" in candidate_langs:
        fr_chars = len(re.findall(r'[çèàêëîïôùû]', lower_text))
        fr_words = {"le", "la", "les", "et", "de", "en", "un", "une", "du", "des", "pour", "avec", "est", "dans", "bonjour", "merci", "nous", "vous"}
        scores["fr"] = (fr_chars * 10) + (len(words & fr_words) * 5)

    # الألمانية (German)
    if "de" in candidate_langs:
        de_chars = len(re.findall(r'[äöüß]', lower_text))
        de_words = {"der", "die", "das", "und", "in", "zu", "den", "nicht", "von", "sie", "ist", "mit", "dem", "dass", "ich", "auf", "ein", "eine"}
        scores["de"] = (de_chars * 10) + (len(words & de_words) * 5)

    # اختيار اللغة صاحبة أعلى نتيجة
    best_lang = max(scores, key=scores.get)
    if scores[best_lang] > 0:
        return best_lang

    # كخيار احتياطي للاتينية
    if "en" in candidate_langs:
        return "en"
    return candidate_langs[0]

def parse_multilang_text(full_text: str, lang_slots: list):
    """تفكيك النص متعدد اللغات إلى مقاطع متماسكة بحسب لغة كل فقرة أو جملة"""
    candidate_langs = [s["lang"] for s in lang_slots]
    lang_to_idx = {s["lang"]: i for i, s in enumerate(lang_slots)}

    segments = []
    # تقسيم النص سطراً بسطر أو فقرة بفقرة
    lines = [line.strip() for line in full_text.splitlines() if line.strip()]

    for line in lines:
        # فحص إذا كان السطر يحتوي على مزيج من العربية واللاتينية معاً
        has_ar = bool(re.search(r'[\u0600-\u06FF]', line))
        has_lat = bool(re.search(r'[a-zA-Z\u00C0-\u024F\u0100-\u017F]', line))

        if has_ar and has_lat:
            # تقسيم السطر الداخلي بين الأبجدية العربية والأبجدية اللاتينية
            sub_chunks = re.findall(r'[\u0600-\u06FF0-9\s،؛؟]+|[^\u0600-\u06FF]+', line)
            for ch in sub_chunks:
                clean_ch = ch.strip()
                if clean_ch and re.search(r'\w', clean_ch):
                    matched_lang = classify_text_language(clean_ch, candidate_langs)
                    idx = lang_to_idx.get(matched_lang, 0)
                    segments.append((idx, clean_ch))
        else:
            matched_lang = classify_text_language(line, candidate_langs)
            idx = lang_to_idx.get(matched_lang, 0)
            segments.append((idx, line))

    # دمج المقاطع المتتالية التي تتبع لنفس الصوت لمنع التقطيع
    merged_segments = []
    for idx, text in segments:
        if merged_segments and merged_segments[-1][0] == idx:
            merged_segments[-1] = (idx, merged_segments[-1][1] + " " + text)
        else:
            merged_segments.append((idx, text))

    return merged_segments

async def generate_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_text = update.message.text
    mode = context.user_data.get("mode", "single")
    audio_format = context.user_data.get("audio_format", "mp3")

    status_msg = await update.message.reply_text(t(update, "generating"), parse_mode="Markdown")

    file_id = str(uuid.uuid4())
    final_filename = f"out_{file_id}.{audio_format}"
    temp_chunks = []

    try:
        if mode == "story":
            slots = context.user_data.get("story_slots", [])
            segments = parse_story_dialogue(user_text, len(slots))

            for i, (v_idx, seg_text) in enumerate(segments):
                slot = slots[v_idx]
                chunk_file = f"chunk_{file_id}_{i}.mp3"
                ok = await synthesize_chunk(seg_text, slot["engine"], slot["voice"], chunk_file)
                if ok:
                    temp_chunks.append(chunk_file)

            await merge_audio_files(temp_chunks, final_filename)
            caption_details = f"🎬 **سيناريو حوار مكتمل ({len(slots)} شخصيات)**"

        elif mode == "multilang":
            slots = context.user_data.get("multilang_slots", [])
            segments = parse_multilang_text(user_text, slots)

            for i, (v_idx, seg_text) in enumerate(segments):
                slot = slots[v_idx]
                chunk_file = f"chunk_{file_id}_{i}.mp3"
                ok = await synthesize_chunk(seg_text, slot["engine"], slot["voice"], chunk_file)
                if ok:
                    temp_chunks.append(chunk_file)

            await merge_audio_files(temp_chunks, final_filename)
            caption_details = f"🌐 **تسجيل متعدد اللغات التلقائي ({len(slots)} لغات)**"

        else:
            engine = context.user_data.get("engine", "neural")
            voice = context.user_data.get("voice", "ar-EG-SalmaNeural")
            await synthesize_chunk(user_text, engine, voice, final_filename)
            caption_details = f"🎙️ `{voice}`"

        await status_msg.delete()

        # إرسال الملف النهائي
        with open(final_filename, "rb") as audio_file:
            caption = f"{t(update, 'done')}\n{caption_details}\n📁 `{audio_format.upper()}`{t(update, 'new_text_hint')}"
            await update.message.reply_audio(
                audio=audio_file,
                title="Audio Recording",
                performer="Smart TTS Engine",
                caption=caption,
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"TTS Generation Error: {e}")
        await status_msg.edit_text(t(update, "error"))
    finally:
        for ch in temp_chunks:
            if os.path.exists(ch):
                os.remove(ch)
        if os.path.exists(final_filename):
            os.remove(final_filename)

    return STEP_TEXT

async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم الإلغاء. اكتب /start للبدء مجدداً.")
    return ConversationHandler.END

async def on_startup(app):
    print("⏳ جاري تحضير وفحص قواعد بيانات المحركات...")
    await init_neural_json()
    init_fast_json()
    init_creative_json()
    print("✅ تم تجهيز قواعد البيانات وتشغيل البوت بنجاح!")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()

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
            STEP_DIALECT: [
                CallbackQueryHandler(select_dialect, pattern="^(dia_|back_to_lang)"),
            ],
            STEP_VOICE: [
                CallbackQueryHandler(select_voice, pattern="^(voc_|back_to_lang)"),
            ],
            STEP_STORY_MENU: [
                CallbackQueryHandler(handle_story_menu, pattern="^(story_|edit_story_)"),
            ],
            STEP_MULTILANG_MENU: [
                CallbackQueryHandler(handle_multilang_menu, pattern="^(mlang_|edit_mlang_)"),
            ],
            STEP_FORMAT: [
                CallbackQueryHandler(select_format, pattern="^fmt_"),
            ],
            STEP_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, generate_audio)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    app.add_handler(conv_handler)
    print("🚀 البوت متصل وجاهز للعمل...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()