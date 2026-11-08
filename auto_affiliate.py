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

def extract_asin_and_prices(text):
    if not text:
        return None, None, None
    
    # Extract Amazon ASIN
    urls = re.findall(r'(https?://\S+)', text)
    asin = None
    for url in urls:
        clean_url = url.rstrip(').,!*')
        asin_match = re.search(r'/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})', clean_url)
        if asin_match:
            asin = asin_match.group(1)
            break
            
    if not asin:
        asin_match = re.search(r'\b([A-Z0-9]{10})\b', text)
        if asin_match:
            asin = asin_match.group(1)

    # Extract Prices from text
    prices = re.findall(r'[₹Rs\. ]*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)', text)
    cleaned_prices = []
    for p in prices:
        val = p.replace(',', '')
        try:
            if '.' in val:
                cleaned_prices.append(float(val))
            else:
                cleaned_prices.append(int(val))
        except ValueError:
            continue
            
    mrp = max(cleaned_prices) if cleaned_prices else "Check Link"
    price = min(cleaned_prices) if cleaned_prices and len(cleaned_prices) > 1 else "Check Link"
    
    return asin, mrp, price

def main():
    posted_asins = load_posted_asins()

    with TelegramClient(StringSession(TG_SESSION), API_ID, API_HASH) as client:
        for channel in SOURCE_CHANNELS:
            try:
                for message in client.iter_messages(channel, limit=15):
                    if not message.text:
                        continue
                        
                    asin, mrp, price = extract_asin_and_prices(message.text)
                    if asin and asin not in posted_asins:
                        # Your exclusive Amazon affiliate link
                        affiliate_link = f"https://{AMAZON_DOMAIN}/dp/{asin}/?tag={MY_AFFILIATE_TAG}"
                        
                        caption = (
                            f"🔥 **Hot Loot Deal** 🔥\n\n"
                            f"💰 **MRP:** ₹{mrp}\n"
                            f"⚡ **Deal Price:** ₹{price}\n\n"
                            f"🛒 **Buy Now:** {affiliate_link}"
                        )

                        # Post with product photo
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
                        return  # Posts 1 unique deal per run every 20 minutes
            except Exception as e:
                print(f"Skipped {channel}: {e}")

if __name__ == "__main__":
    main()
