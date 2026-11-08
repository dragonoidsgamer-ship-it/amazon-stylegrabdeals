import os, re, time, requests
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

SOURCE_CHANNELS = ["lootdeals_official", "indiadesire_deals", "desidime"]
HISTORY_FILE = "posted_asins.txt"
MAX_POSTS_PER_RUN = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7"
}

def load_posted_asins():
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted_asin(asin):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{asin}\n")

def extract_asin(text):
    if not text:
        return None
    urls = re.findall(r'(https?://\S+)', text)
    for url in urls:
        clean_url = url.rstrip(').,!*\'"]')
        direct_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', clean_url)
        if direct_match:
            return direct_match.group(1)
        try:
            res = requests.get(clean_url, allow_redirects=True, timeout=8, headers=HEADERS)
            redirect_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', res.url)
            if redirect_match:
                return redirect_match.group(1)
        except Exception:
            continue
    return None

def get_deal_details(asin, fallback_text):
    url = f"https://{AMAZON_DOMAIN}/dp/{asin}"
    title, mrp, deal_price, discount_pct, img_url = None, None, None, None, None

    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            
            title_el = soup.find(id="productTitle")
            if title_el:
                title = title_el.get_text().strip()[:100]

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

    # Fallback to Telegram message text if Amazon blocks pricing or title
    if not title and fallback_text:
        lines = [line.strip() for line in fallback_text.split('\n') if line.strip() and not line.strip().startswith('http')]
        title = lines[0][:100] if lines else "Amazon Hot Loot Deal"
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

def main():
    posted_asins = load_posted_asins()
    posted_count = 0

    with TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH) as client:
        for channel in SOURCE_CHANNELS:
            if posted_count >= MAX_POSTS_PER_RUN:
                break
            try:
                for message in client.iter_messages(channel, limit=50):
                    if posted_count >= MAX_POSTS_PER_RUN:
                        break

                    msg_text = message.text or message.message or ""
                    asin = extract_asin(msg_text)

                    if asin and asin not in posted_asins:
                        title, mrp, deal_price, discount_pct, img_url = get_deal_details(asin, msg_text)
                        affiliate_link = f"https://{AMAZON_DOMAIN}/dp/{asin}/?tag={MY_AFFILIATE_TAG}"

                        lines = [f"🔥 *{title}* 🔥\n"]
                        if mrp:
                            lines.append(f"❌ *MRP:* ~₹{mrp}~")
                        if deal_price:
                            lines.append(f"✅ *Deal Price:* ₹{deal_price}")
                        if discount_pct:
                            lines.append(f"📉 *Discount:* {discount_pct}% OFF")
                        lines.append(f"\n🛒 *Buy Now:* {affiliate_link}")

                        caption = "\n".join(lines)
                        sent_successfully = False

                        # 1. Try sending with source Telegram photo
                        if message.photo:
                            photo_path = client.download_media(message.media, file=f"temp_{asin}.jpg")
                            if photo_path and os.path.exists(photo_path):
                                with open(photo_path, "rb") as photo_file:
                                    res = requests.post(
                                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                                        data={"chat_id": MY_TG_CHANNEL, "caption": caption, "parse_mode": "Markdown"},
                                        files={"photo": photo_file}
                                    )
                                    sent_successfully = res.ok
                                os.remove(photo_path)

                        # 2. Try sending with Amazon scraped image URL
                        if not sent_successfully and img_url:
                            res = requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                                data={"chat_id": MY_TG_CHANNEL, "photo": img_url, "caption": caption, "parse_mode": "Markdown"}
                            )
                            sent_successfully = res.ok

                        # 3. Fallback to text message with automatic link preview card
                        if not sent_successfully:
                            res = requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                json={
                                    "chat_id": MY_TG_CHANNEL,
                                    "text": caption,
                                    "parse_mode": "Markdown",
                                    "disable_web_page_preview": False
                                }
                            )
                            sent_successfully = res.ok

                        if sent_successfully:
                            save_posted_asin(asin)
                            posted_asins.add(asin)
                            posted_count += 1
                            print(f"[{posted_count}/{MAX_POSTS_PER_RUN}] Posted ASIN: {asin}")
                            time.sleep(3)  # Pause 3 seconds so Telegram doesn't rate-limit your bot
                        else:
                            print(f"Failed to post {asin}: {res.text}")

            except Exception as e:
                print(f"Error reading channel {channel}: {e}")

if __name__ == "__main__":
    main()
