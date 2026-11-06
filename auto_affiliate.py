import os, re, requests
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
TG_SESSION = os.environ['TG_SESSION']
BOT_TOKEN = os.environ['BOT_TOKEN']

MY_TG_CHANNEL = "@StyleGrabDeals"
MY_AFFILIATE_TAG = os.environ['MY_AFFILIATE_TAG']
AMAZON_DOMAIN = "www.amazon.in"

SOURCE_CHANNELS = ["lootdeals_official", "indiadesire_deals", "desidime"]
HISTORY_FILE = "posted_asins.txt"

def load_posted_asins():
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted_asin(asin):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{asin}\n")

def extract_asin_only(text):
    urls = re.findall(r'(https?://\S+)', text)
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in urls:
        clean_url = url.rstrip(').,!*')
        try:
            res = requests.get(clean_url, allow_redirects=True, timeout=8, headers=headers)
            asin_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', res.url)
            if asin_match:
                return asin_match.group(1)
        except Exception:
            continue
    return None

def main():
    posted_asins = load_posted_asins()
    collected_links = []

    with TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH) as client:
        for channel in SOURCE_CHANNELS:
            if len(collected_links) >= 20:
                break
            try:
                for message in client.iter_messages(channel, limit=30):
                    if not message.text:
                        continue
                    asin = extract_asin_only(message.text)
                    if asin and asin not in posted_asins:
                        short_link = f"https://{AMAZON_DOMAIN}/dp/{asin}/?tag={MY_AFFILIATE_TAG}"
                        collected_links.append(short_link)
                        save_posted_asin(asin)
                        posted_asins.add(asin)
                        if len(collected_links) >= 20:
                            break
            except Exception as e:
                print(f"Skipped {channel}: {e}")

    if collected_links:
        message_body = "🔥 **Latest Loot Deals** 🔥\n\n" + "\n".join(collected_links)
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": MY_TG_CHANNEL, "text": message_body}
        )

if __name__ == "__main__":
    main()
