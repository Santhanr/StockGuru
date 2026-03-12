FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source (.env is gitignored and never copied)
COPY . .

# Runtime: run the Slack bot
CMD ["python", "slack_bot.py"]
