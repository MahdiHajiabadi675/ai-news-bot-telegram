import os
from pathlib import Path

# Telegram
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))

# LLM (Gemini free tier)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Behavior
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "15"))
COLLECT_INTERVAL_MIN = int(os.getenv("COLLECT_INTERVAL_MIN", "45"))

# Auto-publish settings
AUTO_PUBLISH_TRUSTED = os.getenv("AUTO_PUBLISH_TRUSTED", "true").lower() == "true"
AUTO_PUBLISH_CONFIRMED = os.getenv("AUTO_PUBLISH_CONFIRMED", "true").lower() == "true"

# Fast-track settings (بدون Gemini — فقط ترجمه/فرمت)
FAST_TRACK_ENABLED = os.getenv("FAST_TRACK_ENABLED", "true").lower() == "true"

# AI Keywords for fast-track filtering (Persian & English)
AI_KEYWORDS = [
    # Persian
    "هوش مصنوعی", "یادگیری ماشین", "مدل", "GPT", "LLM", "شبکه عصبی",
    "deepseek", "chatgpt", "gemini", "claude", "anthropic", "openai",
    "مصنوعی", "ماشین", "الگوریتم",
    # English
    "AI", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "LLM", "large language model", "GPT", "transformer",
    "diffusion", "generative", "inference", "training", "fine-tuning",
    "benchmark", "model release", "AI chip", "GPU", "CUDA",
]

# Auto-reject keywords (اگر در عنوان باشد → رد خودکار)
REJECT_KEYWORDS = [
    "crypto", "cryptocurrency", "bitcoin", "blockchain", "nft", "token sale",
    "casino", "betting", "gambling", "forex", "trading signals",
    "giveaway", "free money", "click here", "limited offer",
]

# Source trust scores (1-5)
TRUST_SCORES = {
    "OpenAI Blog": 5,
    "Google AI": 5,
    "HuggingFace": 5,
    "MIT Tech Review": 4,
    "Ars Technica AI": 4,
    "TechCrunch AI": 4,
    "VentureBeat AI": 3,
    "The Verge AI": 3,
    "Wired AI": 3,
    "AI News": 3,
    "دیجیاتو": 3,
    "تک‌راسا": 3,
}
