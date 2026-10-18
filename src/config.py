import os
from pathlib import Path

# Telegram
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))

# LLM (Gemini free tier)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Behavior
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "15"))
COLLECT_INTERVAL_MIN = int(os.getenv("COLLECT_INTERVAL_MIN", "45"))

# Scoring weights
SCORE_SOURCE_WEIGHT = 20
SCORE_IMPORTANCE_WEIGHT = 30
SCORE_CONFIDENCE_WEIGHT = 30
SCORE_CATEGORY_WEIGHT = 20

# Thresholds
AUTO_PUBLISH_THRESHOLD = 70
AUTO_REJECT_THRESHOLD = 30

# Auto-publish settings
AUTO_PUBLISH_ENABLED = os.getenv("AUTO_PUBLISH_ENABLED", "true").lower() == "true"

# Fast-track settings
FAST_TRACK_ENABLED = os.getenv("FAST_TRACK_ENABLED", "true").lower() == "true"

# AI Keywords for fast-track filtering
AI_KEYWORDS = [
    "هوش مصنوعی", "یادگیری ماشین", "مدل", "GPT", "LLM", "شبکه عصبی",
    "deepseek", "chatgpt", "gemini", "claude", "anthropic", "openai",
    "مصنوعی", "ماشین", "الگوریتم",
    "AI", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "LLM", "large language model", "GPT", "transformer",
    "diffusion", "generative", "inference", "training", "fine-tuning",
    "benchmark", "model release", "AI chip", "GPU", "CUDA",
]

# Auto-reject keywords
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
    "Anthropic": 5,
    "Meta AI": 5,
    "MIT Tech Review": 4,
    "Ars Technica AI": 4,
    "TechCrunch AI": 4,
    "The Verge AI": 3,
    "Wired AI": 3,
    "VentureBeat AI": 3,
    "AI News": 3,
    "دیجیاتو": 3,
    "تک‌راسا": 3,
}

# Category weights for scoring
CATEGORY_WEIGHTS = {
    "model_release": 1.0,
    "benchmark": 0.9,
    "funding": 0.8,
    "research": 0.7,
    "policy": 0.6,
    "tool_release": 0.8,
    "industry": 0.5,
    "other": 0.3,
}
