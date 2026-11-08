import os, re, requests
from bs4 import BeautifulSoup
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
    if not text:
        return None
    urls = re.findall(r'(https?://\S+)', text)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for url in urls:
        clean_url = url.rstrip(').,!*')
        try:
            res = requests.get(clean_url, allow_redirects=True, timeout=6, headers=headers)
            asin_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', res.url)
            if asin_match:
                return asin_match.group(1)
        except Exception:
            continue
    return None

def fetch_amazon_details(asin):
    url = f"https://{AMAZON_DOMAIN}/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code != 200:
            return None, None, None, None
        
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Title
        title_el = soup.find(id="productTitle")
        title = title_el.get_text().strip() if title_el else "Amazon Loot Deal"
        
        # Price
        price_el = soup.find("span", class_="a-price-whole")
        offered_price = price_el.get_text().strip().replace(",", "") if price_el else None
        
        # MRP
        mrp_el = soup.find("span", class_="a-text-price")
        mrp = None
        if mrp_el:
            mrp_text = mrp_el.get_text()
            mrp_match = re.search(r'[\d,]+(?:\.\d+)?', mrp_text)
            if mrp_match:
                mrp = mrp_match.group(0).replace(",", "")
        
        if not offered_price:
            return None, None, None, title

        # Calculate discount percentage
        op_val = float(offered_price)
        mrp_val = float(mrp) if mrp else op_val
        if mrp_val > op_val:
            discount_pct = round(((mrp_val - op_val) / mrp_val) * 100)
        else:
            discount_pct = 0
            mrp = mrp_val

        return mrp, op_val, discount_pct, title
    except Exception:
        return None, None, None, None

def main():
    posted_asins = load_posted_asins()

    with TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH) as client:
        for channel in SOURCE_CHANNELS:
            try:
                for message in client.iter_messages(channel, limit=20):
                    asin = extract_asin_only(message.text)
                    if asin and asin not in posted_asins:
                        # Fetch pricing info from Amazon
                        mrp, price, discount, title = fetch_amazon_details(asin)
                        if not price:
                            continue

                        affiliate_link = f"https://{AMAZON_DOMAIN}/dp/{asin}/?tag={MY_AFFILIATE_TAG}"
                        
                        caption = (
                            f"🔥 **{title}**\n\n"
                            f"💰 **MRP:** ~₹{mrp}~\n"
                            f"⚡ **Deal Price:** ₹{price}\n"
                            f"📉 **Discount:** {discount}% OFF!\n\n"
                            f"🛒 **Buy Now:** {affiliate_link}"
                        )

                        # Post with photo if available in source message
                        if message.photo:
                            photo_path = client.download_media(message.media, file="temp_deal.jpg")
                            if photo_path and os.path.exists(photo_path):
                                with open(photo_path, "rb") as photo_file:
                                    requests.post(
                                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                                        data={"chat_id": MY_TG_CHANNEL, "caption": caption, "parse_mode": "Markdown"},
                                        files={"photo": photo_file}
                                    )
                                os.remove(photo_path)
                        else:
                            requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                data={"chat_id": MY_TG_CHANNEL, "text": caption, "parse_mode": "Markdown"}
                            )

                        save_posted_asin(asin)
                        return  # Post strictly 1 unique product per run, repeating every 20 minutes automatically
            except Exception as e:
                print(f"Skipped {channel}: {e}")

if __name__ == "__main__":
    main()
