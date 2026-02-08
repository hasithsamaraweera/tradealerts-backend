import os, json, asyncio, datetime
from telethon import TelegramClient, events
import pytesseract, cv2, numpy as np
import firebase_admin
from firebase_admin import credentials, firestore

print("✅ main.py started (container is running)")

# ===============================
# CONFIG
# ===============================
api_id = 12835147
api_hash = "c5c7a2582f1f32c244c5ef465e13fbfc"
group_username = -4877905193  # replace with @username if handler doesn't fire

# ✅ Let pytesseract use PATH
pytesseract.pytesseract.tesseract_cmd = "tesseract"

# Firebase setup
firebase_json = os.getenv("FIREBASE_JSON")
if firebase_json:
    cred = credentials.Certificate(json.loads(firebase_json))
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    db = None

loss_count = 0
last_entry_text = None
last_reset_date = datetime.date.today()

# ===============================
# OCR + RESULT
# ===============================
def extract_profit_number_from_bytes(img_bytes):
    np_img = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
    if img is None: return None
    h, w, _ = img.shape
    x_ratio, y_ratio, w_ratio, h_ratio = 0.722, 0.443, 0.213, 0.106
    x, y = int(x_ratio * w), int(y_ratio * h)
    crop_w, crop_h = int(w_ratio * w), int(h_ratio * h)
    cropped = img[y:y+crop_h, x:x+crop_w]
    gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3,3), 0)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    raw_text = pytesseract.image_to_string(thresh, config="--psm 7 -c tessedit_char_whitelist=0123456789.$-")
    cleaned = ''.join(ch for ch in raw_text if ch.isdigit() or ch in ".-+$")
    return cleaned.strip() if cleaned else None

def determine_result(value):
    if value is None: return "UNKNOWN"
    value = value.replace("$","").replace(",","")
    try:
        v = float(value)
        return "WIN" if v > 0 else "LOSS"
    except:
        return "UNKNOWN"

# ===============================
# TELETHON HANDLER
# ===============================
client = TelegramClient("session", api_id, api_hash)

@client.on(events.NewMessage(chats=[group_username]))
async def handler(event):
    global loss_count, last_entry_text

    print("📩 Handler triggered")
    print("Raw message:", event.message.to_dict())

    if event.message.message:
        lines = event.message.message.split("\n")
        if len(lines) >= 2:
            entry_text = lines[1].strip().split()[-1]
            last_entry_text = entry_text
            print(f"New entry: {entry_text}")

    if event.message.media:
        img_bytes = await event.message.download_media(bytes)
        extracted = extract_profit_number_from_bytes(img_bytes)
        print("OCR extracted:", extracted)
        if extracted and last_entry_text:
            result_text = determine_result(extracted)
            print(f"Result: {result_text}")

            if result_text == "LOSS":
                loss_count += 1
            elif result_text == "WIN":
                loss_count = 0
                if db:
                    set_current_alert(active=False)

            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            if db:
                db.collection("signals").document(today_str).collection("entries").add({
                    "entry": last_entry_text,
                    "result": result_text,
                    "timestamp": int(now.timestamp() * 1000),
                    "time_str": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "loss_count": loss_count
                })

            last_entry_text = None
            threshold = get_alert_threshold()
            if loss_count >= threshold:
                set_current_alert(
                    active=True,
                    message=f"{threshold} or more consecutive losses detected!",
                    loss_count=loss_count
                )

# ===============================
# RUN
# ===============================
async def main():
    print("🔑 Starting Telegram client...")
    await client.start()
    print("🚀 Connected to Telegram, listening for signals...")
    await client.run_until_disconnected()

asyncio.run(main())
