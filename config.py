import os

# --- Model IDs (change one env var to upgrade any agent) ---
RESEARCHER_SYNTHESIS_MODEL = os.getenv("RESEARCHER_MODEL", "gpt-4o-mini")
ANALYST_MODEL              = os.getenv("ANALYST_MODEL",    "gpt-4o-mini")
AUDITOR_MODEL              = os.getenv("AUDITOR_MODEL",    "claude-haiku-4-5-20251001")
PERPLEXITY_MODEL           = os.getenv("PERPLEXITY_MODEL", "sonar")

# --- Slack ---
SLACK_CHANNEL_ID  = os.getenv("SLACK_CHANNEL_ID")
SLACK_BOT_TOKEN   = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN   = os.getenv("SLACK_APP_TOKEN")   # xapp-... for Socket Mode

# --- Pipeline limits ---
MAX_REVISIONS = int(os.getenv("MAX_REVISIONS", "2"))

# --- API keys (read once here so other modules import from config) ---
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
PERPLEXITY_API_KEY  = os.getenv("PERPLEXITY_API_KEY")

# --- SEC EDGAR ---
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "StockAnalyzer contact@example.com")
