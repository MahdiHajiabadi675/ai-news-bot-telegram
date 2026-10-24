"""
واژه‌های وزن‌دار برای تشخیص «خبر دربارهٔ هوش مصنوعی» — این فایل را آزادانه ویرایش کن.

هر عنصر: (الگو، وزن)
  • وزن ۳  = قوی: خودش به‌تنهایی نشان می‌دهد خبر دربارهٔ AI است (OpenAI، ChatGPT، LLM، …)
  • وزن ۲  = متوسط: معمولاً AI است ولی نه همیشه (Nvidia، speech recognition، …)
  • وزن ۱  = ضعیف: فقط در کنار نشانهٔ دیگر معنا دارد (robot، algorithm، agent، …)

الگوها regex هستند (حروف بزرگ/کوچک مهم نیست).
  • انگلیسی: با مرز کلمه تطبیق داده می‌شود ("ai" داخل "said" پیدا نمی‌شود).
  • فارسی: روی متن نرمال‌شده تطبیق داده می‌شود؛ فاصله = «فاصله یا نیم‌فاصله یا هیچ»،
    پس «هوش مصنوعی»، «هوش‌مصنوعی» و «هوشمصنوعی» یکی هستند. فقط مرز «ابتدای کلمه» چک
    می‌شود تا پسوندها («رباتها»، «هوش مصنوعی‌های …») درست کار کنند.

امتیاز نهایی (در classifier.ai_relevance):
    هر واژهٔ تطبیق‌خورده در «عنوان یا تگ‌های RSS» ×۲  +  در «خلاصه» ×۱  (هر واژه یک بار)
    امتیاز ≥ PASS_SCORE (۳) → خبر مرتبط است.
مثال: یک واژهٔ قوی در خلاصه (۳) کافی است؛ یک واژهٔ ضعیف فقط در عنوان (۲) کافی نیست؛
عنوان + خلاصه (۲+۱) کافی است. پس لازم نیست «هوش مصنوعی» حتماً در متن تکرار شود.
"""

PASS_SCORE = 3

# ───────────── انگلیسی ─────────────
EN_TERMS = [
    # شرکت‌ها و محصولات AI
    (r"openai", 3), (r"anthropic", 3), (r"deepmind", 3), (r"hugging\s?face", 3),
    (r"x\.?ai", 2), (r"mistral(?:\s?ai)?", 3), (r"cohere", 2), (r"perplexity", 3),
    (r"midjourney", 3), (r"stability\s?ai", 3), (r"stable\s?diffusion", 3),
    (r"chat-?gpt", 3), (r"gpt-?\d[\w.\-]*", 3), (r"\bgpt\b", 3), (r"sora", 2),
    (r"claude", 3), (r"gemini", 3), (r"llama[\s\-]?\d*", 3), (r"qwen", 3), (r"grok", 3),
    (r"deepseek", 3), (r"copilot", 3), (r"codex", 2), (r"cursor\s?ai", 3),
    # مفاهیم AI
    (r"ai", 3), (r"a\.i\.", 3), (r"artificial\s+intelligence", 3), (r"machine\s+learning", 3),
    (r"deep\s+learning", 3), (r"neural\s+nets?(?:works?)?", 3), (r"llms?", 3),
    (r"(?:large\s+)?language\s+models?", 3), (r"foundation\s+models?", 3),
    (r"generative", 3), (r"gen-?ai", 3), (r"chatbots?", 3), (r"agentic", 3),
    (r"ai\s+agents?", 3), (r"diffusion\s+models?", 3), (r"transformer\s+models?", 3),
    (r"fine-?tun(?:e|ed|ing)", 3), (r"prompt\s+injection", 3), (r"deepfakes?", 3),
    (r"computer\s+vision", 3), (r"natural\s+language\s+processing", 3), (r"nlp", 2),
    (r"reinforcement\s+learning", 3), (r"open-?source\s+models?", 2),
    (r"synthetic\s+data", 2), (r"speech\s+recognition", 2), (r"text-to-(?:image|video|speech)", 3),
    (r"image\s+generat\w+", 2), (r"video\s+generat\w+", 2),
    # افراد شاخص AI (وزن ۲: در عنوان کافی‌اند، در خلاصه‌ی تنها نه)
    (r"sam\s+altman", 2), (r"demis\s+hassabis", 2), (r"dario\s+amodei", 2),
    (r"jensen\s+huang", 2), (r"ilya\s+sutskever", 2), (r"yann\s+lecun", 2),
    (r"geoffrey\s+hinton", 2), (r"andrej\s+karpathy", 2), (r"mustafa\s+suleyman", 2),
    # سخت‌افزار و زیرساخت
    (r"nvidia", 2), (r"h[12]00", 2), (r"b200", 2), (r"blackwell", 2), (r"tpus?", 1),
    (r"gpus?", 1), (r"cuda", 2), (r"data\s+cent(?:er|re)s?", 1), (r"inference", 1),
    # نشانه‌های ضعیف
    (r"robots?", 1), (r"robotics?", 1), (r"humanoids?", 1), (r"algorithms?", 1),
    (r"autonomous", 1), (r"self-?driving", 1), (r"driverless", 1), (r"automation", 1),
    (r"agents?", 1), (r"prompts?", 1), (r"bots?", 1),
]

# ───────────── فارسی (روی متن نرمال‌شده؛ فاصله = فاصله/نیم‌فاصله/هیچ) ─────────────
FA_TERMS = [
    ("هوش مصنوعی", 3), ("یادگیری ماشین", 3), ("یادگیری عمیق", 3), ("شبکه عصبی", 3),
    ("مدل زبانی", 3), ("مدل های زبانی", 3), ("مدل زبان بزرگ", 3), ("پردازش زبان طبیعی", 3),
    ("چت بات", 3), ("چت جی پی تی", 3), ("جی پی تی", 3), ("جمینای", 3), ("دیپ سیک", 3),
    ("اوپن ای آی", 3), ("آنتروپیک", 3), ("اپن ای آی", 3), ("دیپ مایند", 3), ("میدجورنی", 3),
    ("دیپ فیک", 3), ("کوپایلت", 3), ("پرپلکسیتی", 3), ("گراک", 3), ("هوش مصنوعی مولد", 3),
    ("ای آی", 3), ("سم آلتمن", 2), ("جنسن هوانگ", 2),
    ("انویدیا", 2), ("پردازنده گرافیکی", 1), ("تشخیص چهره", 2), ("تشخیص گفتار", 2),
    ("تولید تصویر", 1), ("تولید ویدیو", 1),
    ("ربات", 1), ("الگوریتم", 1), ("خودران", 1), ("اتوماسیون", 1), ("عامل هوشمند", 2),
]
