import os, re, time, html, hashlib, requests
from bs4 import BeautifulSoup
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

API_ID = int(os.environ['TG_API_ID'])
API_HASH = os.environ['TG_API_HASH']
TG_SESSION = os.environ['TG_SESSION']
BOT_TOKEN = os.environ['BOT_TOKEN']

MY_TG_CHANNEL = os.environ.get('MY_TG_CHANNEL', '@StyleGrabDeals').strip()
if not MY_TG_CHANNEL.startswith('@') and not MY_TG_CHANNEL.startswith('-100'):
    MY_TG_CHANNEL = '@' + MY_TG_CHANNEL

MY_AFFILIATE_TAG = os.environ['MY_AFFILIATE_TAG'].strip()
AMAZON_DOMAIN = "www.amazon.in"

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
COOLDOWN_SECONDS = 90 * 24 * 3600  # 90 days (3 months) strict cooldown
MAX_RUNTIME_SECONDS = 510          # 8.5 minutes cutoff so git push always finishes

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7"
}

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(HEADERS)

GENERIC_TITLES = {"amazon hot loot deal", "hot loot deal", "loot deal", "amazon deal"}

def title_fingerprint(title):
    if not title:
        return None
    clean = re.sub(r'[^a-z0-9 ]', '', title.lower()).strip()
    if clean in GENERIC_TITLES:
        return None
    words = clean.split()
    # Only fingerprint specific titles with at least 4 words so generic fallbacks never block posts
    if len(words) < 4:
        return None
    core = " ".join(words[:7])
    return "TTL_" + hashlib.md5(core.encode()).hexdigest()[:10].upper()

def load_recent_keys():
    now = int(time.time())
    recent_keys = {}
    if not os.path.exists(HISTORY_FILE):
        return recent_keys

    with open(HISTORY_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line == "INIT":
                continue
            if "|" in line:
                parts = line.split("|", 1)
                key = parts[0].strip().upper()
                try:
                    ts = int(parts[1].strip())
                except ValueError:
                    ts = now
            else:
                key = line.strip().upper()
                ts = now

            # Ignore old generic TTL_ hashes that blocked previous runs
            if key.startswith("TTL_"):
                continue

            if now - ts < COOLDOWN_SECONDS:
                recent_keys[key] = ts

    return recent_keys

def append_key_to_file(key, ts):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{key}|{ts}\n")
        f.flush()

def extract_all_urls(message):
    urls = []
    text = message.text or message.message or ""
    if text:
        urls.extend(re.findall(r'(https?://[^\s)\]>✨🔥🛒]+)', text))

    # Also extract URLs hidden inside clickable "Buy Now" text links
    if message.entities:
        for ent in message.entities:
            if isinstance(ent, MessageEntityTextUrl) and ent.url:
                urls.append(ent.url)

    # Remove duplicates while preserving order
    seen = set()
    clean_urls = []
    for u in urls:
        u_clean = u.rstrip(').,!*\'"]')
        if u_clean not in seen:
            seen.add(u_clean)
            clean_urls.append(u_clean)
    return clean_urls

def extract_asins_from_message(message):
    urls = extract_all_urls(message)
    found_asins = []

    for url in urls:
        direct_match = re.search(r'/(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?&#]|$)', url, re.IGNORECASE)
        if direct_match:
            asin = direct_match.group(1).upper()
            if asin not in found_asins:
                found_asins.append(asin)
            continue

        # Expand short links (amzn.to, bit.ly, amzlinks.in, etc.)
        try:
            res = HTTP_SESSION.get(url, allow_redirects=True, timeout=4)
            redirect_match = re.search(r'/(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?&#]|$)', res.url, re.IGNORECASE)
            if redirect_match:
                asin = redirect_match.group(1).upper()
                if asin not in found_asins:
                    found_asins.append(asin)
        except Exception:
            continue

    return found_asins

def get_deal_details(asin, fallback_text):
    url = f"https://{AMAZON_DOMAIN}/dp/{asin}"
    title, mrp, deal_price, discount_pct, img_url = None, None, None, None, None
    canonical_asin = asin

    # 1. Parse Telegram message text first (fast and immune to Amazon CAPTCHA)
    if fallback_text:
        clean_lines = []
        for line in fallback_text.split('\n'):
            s = re.sub(r'https?://\S+', '', line).strip()
            s = re.sub(r'[*_~`]', '', s).strip()
            if len(s) > 8 and not re.match(r'^(buy now|grab now|loot deal|link|price|mrp|deal)', s.lower()):
                clean_lines.append(s)
        if clean_lines:
            title = clean_lines[0][:110]

        prices = re.findall(r'(?:₹|Rs\.?|INR|@|Price[:\s]*|MRP[:\s]*)\s*([0-9,]+)', fallback_text, re.IGNORECASE)
        if not prices:
            prices = re.findall(r'\b([0-9]{3,6})\b', fallback_text)
        cleaned = [int(p.replace(',', '')) for p in prices if p.replace(',', '').isdigit() and 25 <= int(p.replace(',', '')) <= 250000]
        if cleaned:
            deal_price = min(cleaned)
            if len(cleaned) > 1 and max(cleaned) > deal_price:
                mrp = max(cleaned)

    # 2. Try enriching from Amazon page (if Amazon doesn't CAPTCHA-block the IP)
    try:
        res = HTTP_SESSION.get(url, timeout=5)
        if res.status_code == 404:
            return False, canonical_asin, None, None, None, None, None

        if res.status_code == 200 and "api-services-support@amazon.com" not in res.text:
            soup = BeautifulSoup(res.text, 'html.parser')

            canon_link = soup.find("link", rel="canonical")
            if canon_link and canon_link.get("href"):
                c_match = re.search(r'/dp/([A-Z0-9]{10})', canon_link["href"], re.IGNORECASE)
                if c_match:
                    canonical_asin = c_match.group(1).upper()

            avail_el = soup.find(id="availability") or soup.find(id="outOfStock")
            if avail_el:
                avail_text = avail_el.get_text().strip().lower()
                unavailable_phrases = [
                    "currently unavailable",
                    "out of stock",
                    "temporarily out of stock",
                    "we don't know when or if this item will be back"
                ]
                if any(phrase in avail_text for phrase in unavailable_phrases):
                    print(f"Skipping {asin}: Explicitly Out of Stock on Amazon.")
                    return False, canonical_asin, None, None, None, None, None

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

    if not title:
        title = f"Amazon Deal ({canonical_asin})"

    if mrp and deal_price and mrp > deal_price:
        discount_pct = round(((mrp - deal_price) / mrp) * 100)

    return True, canonical_asin, title, mrp, deal_price, discount_pct, img_url

def send_telegram_request(method, data=None, json_payload=None, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    for attempt in range(3):
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
    recent_keys = load_recent_keys()
    print(f"Loaded {len(recent_keys)} locked ASINs in 90-day cooldown.")
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
                    asins = extract_asins_from_message(message)
                    if not asins:
                        continue

                    for asin in asins:
                        if posted_count >= MAX_POSTS_PER_RUN or (time.time() - start_time) > MAX_RUNTIME_SECONDS:
                            break

                        if asin in recent_keys:
                            continue

                        is_available, canonical_asin, title, mrp, deal_price, discount_pct, img_url = get_deal_details(asin, msg_text)
                        now_ts = int(time.time())

                        if canonical_asin in recent_keys:
                            recent_keys[asin] = now_ts
                            append_key_to_file(asin, now_ts)
                            continue

                        if not is_available:
                            recent_keys[asin] = now_ts
                            append_key_to_file(asin, now_ts)
                            continue

                        t_hash = title_fingerprint(title)
                        if t_hash and t_hash in recent_keys:
                            recent_keys[asin] = now_ts
                            append_key_to_file(asin, now_ts)
                            continue

                        affiliate_link = f"https://{AMAZON_DOMAIN}/dp/{canonical_asin}/?tag={MY_AFFILIATE_TAG}"

                        safe_title = html.escape(title)
                        lines = [f"🔥 <b>{safe_title}</b> 🔥\n"]
                        if mrp:
                            lines.append(f"❌ <b>MRP:</b> <s>₹{mrp}</s>")
                        if deal_price:
                            lines.append(f"✅ <b>Deal Price:</b> ₹{deal_price}")
                        else:
                            lines.append(f"✅ <b>Deal Price:</b> Check Live Offer on Amazon")
                        if discount_pct:
                            lines.append(f"📉 <b>Discount:</b> {discount_pct}% OFF")
                        lines.append(f"\n🛒 <b>Buy Now:</b> {affiliate_link}")

                        caption = "\n".join(lines)
                        sent_successfully = False

                        # 1. Send with source Telegram photo if only 1 product in message
                        if message.photo and len(asins) == 1:
                            photo_path = client.download_media(message.media, file=f"temp_{canonical_asin}.jpg")
                            if photo_path and os.path.exists(photo_path):
                                with open(photo_path, "rb") as photo_file:
                                    res = send_telegram_request(
                                        "sendPhoto",
                                        data={"chat_id": MY_TG_CHANNEL, "caption": caption, "parse_mode": "HTML"},
                                        files={"photo": photo_file}
                                    )
                                    sent_successfully = res.ok
                                os.remove(photo_path)

                        # 2. Send with Amazon scraped image URL
                        if not sent_successfully and img_url:
                            res = send_telegram_request(
                                "sendPhoto",
                                data={"chat_id": MY_TG_CHANNEL, "photo": img_url, "caption": caption, "parse_mode": "HTML"}
                            )
                            sent_successfully = res.ok

                        # 3. Fallback to rich link preview card (Telegram fetches Amazon thumbnail automatically)
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
                            for key in filter(None, {asin, canonical_asin, t_hash}):
                                recent_keys[key] = now_ts
                                append_key_to_file(key, now_ts)

                            posted_count += 1
                            print(f"[{posted_count}/{MAX_POSTS_PER_RUN}] Posted unique ASIN: {canonical_asin}")
                            time.sleep(3.2)
                        else:
                            print(f"Telegram API error for {canonical_asin}: {res.text}")

            except Exception as e:
                print(f"Skipped channel {channel}: {e}")

    print(f"Run finished. Total unique deals posted this cycle: {posted_count}")

if __name__ == "__main__":
    main()
