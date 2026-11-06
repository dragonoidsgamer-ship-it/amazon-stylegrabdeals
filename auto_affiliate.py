import os, re, requests
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
TG_SESSION = os.environ['TG_SESSION']
BOT_TOKEN = os.environ['BOT_TOKEN']
MY_TG_CHANNEL = os.environ['MY_TG_CHANNEL']
MY_AFFILIATE_TAG = os.environ['MY_AFFILIATE_TAG']
AMAZON_DOMAIN = "www.amazon.in"

WHAPI_TOKEN = os.environ.get('WHAPI_TOKEN', '')
WA_CHANNEL_ID = os.environ.get('WA_CHANNEL_ID', '')

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

def extract_and_convert(text, posted_asins):
    urls = re.findall(r'(https?://\S+)', text)
    found_new_asin = None
    headers = {"User-Agent": "Mozilla/5.0"}

    for url in urls:
        clean_url = url.rstrip(').,!*')
        try:
            res = requests.get(clean_url, allow_redirects=True, timeout=10, headers=headers)
            final_url = res.url
            asin_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', final_url)
            if asin_match:
                asin = asin_match.group(1)
                if asin in posted_asins:
                    return None, None
                my_link = f"https://{AMAZON_DOMAIN}/dp/{asin}/?tag={MY_AFFILIATE_TAG}"
                text = text.replace(clean_url, my_link)
                found_new_asin = asin
        except Exception:
            continue

    clean_text = re.sub(r'@\w+', '', text).strip()
    return clean_text, found_new_asin

def main():
    posted_asins = load_posted_asins()
    posts_sent = 0

    with TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH) as client:
        for channel in SOURCE_CHANNELS:
            if posts_sent >= 3:
                break
            try:
                for message in client.iter_messages(channel, limit=6):
                    if not message.text:
                        continue
                    new_caption, asin = extract_and_convert(message.text, posted_asins)
                    if not asin:
                        continue

                    if message.photo:
                        photo_path = message.download_media()
                        with open(photo_path, 'rb') as img:
                            requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                                data={"chat_id": MY_TG_CHANNEL, "caption": new_caption[:1024]},
                                files={"photo": img}
                            )
                        os.remove(photo_path)
                    else:
                        requests.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            data={"chat_id": MY_TG_CHANNEL, "text": new_caption}
                        )

                    if WHAPI_TOKEN and WA_CHANNEL_ID:
                        requests.post(
                            "https://gate.whapi.cloud/messages/text",
                            json={"to": WA_CHANNEL_ID, "body": new_caption},
                            headers={"Authorization": f"Bearer {WHAPI_TOKEN}"}
                        )

                    save_posted_asin(asin)
                    posted_asins.add(asin)
                    posts_sent += 1
                    if posts_sent >= 3:
                        break
            except Exception as e:
                print(f"Skipped {channel}: {e}")

if __name__ == "__main__":
    main()
