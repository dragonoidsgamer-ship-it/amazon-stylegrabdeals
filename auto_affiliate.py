import os, re, time, html, requests
from bs4 import BeautifulSoup
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
TG_SESSION = os.environ['TG_SESSION']
BOT_TOKEN = os.environ['BOT_TOKEN']

MY_TG_CHANNEL = os.environ.get('MY_TG_CHANNEL', '@StyleGrabDeals').strip()
if not MY_TG_CHANNEL.startswith('@') and not MY_TG_CHANNEL.startswith('-100'):
    MY_TG_CHANNEL = '@' + MY_TG_CHANNEL

MY_AFFILIATE_TAG = os.environ['MY_AFFILIATE_TAG'].strip()
AMAZON_DOMAIN = "www.amazon.in"

# High-volume source channels to supply 100+ unique deals without repeats
SOURCE_CHANNELS = [
    "lootdeals_official",
    "indiadesire_deals",
    "desidime",
    "indsfdeals",
    "trickxpert",
    "loot_deals_24x7",
    "bestdealsindia",
    "amazon_loot_deals",
    "crazydeals",
    "online_loot_deals"
]

HISTORY_FILE = "posted_asins.txt"
MAX_POSTS_PER_RUN = 100
COOLDOWN_SECONDS = 90 * 24 * 3600  # 90 days (3 months) strict no-repeat cooldown
MAX_RUNTIME_SECONDS = 510          # 8.5 minutes safety cutoff to commit memory cleanly

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7"
}

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(HEADERS)

def load_recent_asins():
    now = int(time.time())
    recent_asins = {}
    if not os.path.exists(HISTORY_FILE):
        return recent_asins

    with open(HISTORY_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line == "INIT":
                continue
            if "|" in line:
                parts = line.split("|", 1)
                asin = parts[0].strip().upper()
                try:
                    ts = int(parts[1].strip())
                except ValueError:
                    ts = now
            else:
                asin = line.strip().upper()
                ts = now

            # Keep ASIN locked in memory if it was posted within the last 90 days
            if now - ts < COOLDOWN_SECONDS:
                recent_asins[asin] = ts

    return recent_asins

def save_recent_asins(recent_asins):
    with open(HISTORY_FILE, "w") as f:
        for asin, ts in recent_asins.items():
            f.write(f"{asin}|{ts}\n")

def extract_asin(text):
    if not text:
        return None
    urls = re.findall(r'(https?://\S+)', text)
    for url in urls:
        clean_url = url.rstrip(').,!*\'"]')
        direct_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', clean_url, re.IGNORECASE)
        if direct_match:
            return direct_match.group(1).upper()
        try:
            res = HTTP_SESSION.get(clean_url, allow_redirects=True, timeout=5)
            redirect_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', res.url, re.IGNORECASE)
            if redirect_match:
                return redirect_match.group(1).upper()
        except Exception:
            continue
    return None

def get_deal_details(asin, fallback_text):
    url = f"https://{AMAZON_DOMAIN}/dp/{asin}"
    title, mrp, deal_price, discount_pct, img_url = None, None, None, None, None

    try:
        res = HTTP_SESSION.get(url, timeout=6)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')

            title_el = soup.find(id="productTitle")
            if title_el:
                title = title_el.get_text().strip()[:110]

            price_el = soup.find("span", class_="a-price-whole")
            if price_el:
                p_str = re.sub(r'[^\d]', '', price_el.get_text())
                if p_str:
                    deal_price = int(p_str)

            mrp_el = soup.find("span", class_="a-text-price")
            if mrp_el:
                m_match = re.search(r'[\d,]+', mrp_el.get_text())
                if m_match:
                    m_str = m_match.group(0).replace(',', '')
                    if m_str:
                        mrp = int(m_str)

            img_el = soup.find(id="landingImage")
            if img_el and img_el.get("src"):
                img_url = img_el.get("src")
    except Exception as e:
        print(f"Amazon scrape notice for {asin}: {e}")

    if not title and fallback_text:
        lines = [line.strip() for line in fallback_text.split('\n') if line.strip() and not line.strip().startswith('http')]
        title = lines[0][:110] if lines else "Amazon Hot Loot Deal"
    elif not title:
        title = "Amazon Hot Loot Deal"

    if not deal_price and fallback_text:
        prices = re.findall(r'(?:₹|Rs\.?|INR|@)\s*([0-9,]+)', fallback_text, re.IGNORECASE)
        if not prices:
            prices = re.findall(r'\b([0-9]{2,6})\b', fallback_text)
        cleaned = [int(p.replace(',', '')) for p in prices if p.replace(',', '').isdigit() and int(p.replace(',', '')) > 20]
        if cleaned:
            deal_price = min(cleaned)
            if len(cleaned) > 1 and max(cleaned) > deal_price:
                mrp = max(cleaned)

    if mrp and deal_price and mrp > deal_price:
        discount_pct = round(((mrp - deal_price) / mrp) * 100)

    return title, mrp, deal_price, discount_pct, img_url

def send_telegram_request(method, data=None, json_payload=None, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    for attempt in range(2):
        res = requests.post(url, data=data, json=json_payload, files=files, timeout=15)
        if res.status_code == 429:
            retry_after = res.json().get("parameters", {}).get("retry_after", 10)
            print(f"Telegram rate limit hit. Waiting {retry_after + 1}s...")
            time.sleep(retry_after + 1)
            continue
        return res
    return res

def main():
    start_time = time.time()
    recent_asins = load_recent_asins()
    print(f"Loaded {len(recent_asins)} unique ASINs locked in 90-day (3-month) cooldown.")
    posted_count = 0

    with TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH) as client:
        for channel in SOURCE_CHANNELS:
            if posted_count >= MAX_POSTS_PER_RUN or (time.time() - start_time) > MAX_RUNTIME_SECONDS:
                break
            print(f"Scanning channel: {channel}...")
            try:
                for message in client.iter_messages(channel, limit=250):
                    if posted_count >= MAX_POSTS_PER_RUN or (time.time() - start_time) > MAX_RUNTIME_SECONDS:
                        break

                    msg_text = message.text or message.message or ""
                    asin = extract_asin(msg_text)

                    if asin and asin not in recent_asins:
                        title, mrp, deal_price, discount_pct, img_url = get_deal_details(asin, msg_text)
                        affiliate_link = f"https://{AMAZON_DOMAIN}/dp/{asin}/?tag={MY_AFFILIATE_TAG}"

                        safe_title = html.escape(title)
                        lines = [f"🔥 <b>{safe_title}</b> 🔥\n"]
                        if mrp:
                            lines.append(f"❌ <b>MRP:</b> <s>₹{mrp}</s>")
                        if deal_price:
                            lines.append(f"✅ <b>Deal Price:</b> ₹{deal_price}")
                        if discount_pct:
                            lines.append(f"📉 <b>Discount:</b> {discount_pct}% OFF")
                        lines.append(f"\n🛒 <b>Buy Now:</b> {affiliate_link}")

                        caption = "\n".join(lines)
                        sent_successfully = False

                        # 1. Try sending with source Telegram photo
                        if message.photo:
                            photo_path = client.download_media(message.media, file=f"temp_{asin}.jpg")
                            if photo_path and os.path.exists(photo_path):
                                with open(photo_path, "rb") as photo_file:
                                    res = send_telegram_request(
                                        "sendPhoto",
                                        data={"chat_id": MY_TG_CHANNEL, "caption": caption, "parse_mode": "HTML"},
                                        files={"photo": photo_file}
                                    )
                                    sent_successfully = res.ok
                                os.remove(photo_path)

                        # 2. Try sending with Amazon image URL
                        if not sent_successfully and img_url:
                            res = send_telegram_request(
                                "sendPhoto",
                                data={"chat_id": MY_TG_CHANNEL, "photo": img_url, "caption": caption, "parse_mode": "HTML"}
                            )
                            sent_successfully = res.ok

                        # 3. Fallback to text message with link preview card
                        if not sent_successfully:
                            res = send_telegram_request(
                                "sendMessage",
                                json_payload={
                                    "chat_id": MY_TG_CHANNEL,
                                    "text": caption,
                                    "parse_mode": "HTML",
                                    "disable_web_page_preview": False
                                }
                            )
                            sent_successfully = res.ok

                        if sent_successfully:
                            recent_asins[asin] = int(time.time())
                            save_recent_asins(recent_asins)
                            posted_count += 1
                            print(f"[{posted_count}/{MAX_POSTS_PER_RUN}] Posted unique ASIN (90d lock): {asin}")
                            time.sleep(3.5)
                        else:
                            print(f"Telegram API error for {asin}: {res.text}")

            except Exception as e:
                print(f"Skipped channel {channel}: {e}")

    save_recent_asins(recent_asins)
    print(f"Run finished. Total unique deals posted this cycle: {posted_count}")

if __name__ == "__main__":
    main()
