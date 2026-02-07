import os, json, asyncio, datetime, threading
from telethon import TelegramClient, events
import pytesseract, cv2, numpy as np
import firebase_admin
from firebase_admin import credentials, firestore

# ===============================
# CONFIG (hardcoded for local run)
# ===============================
api_id = 12835147
api_hash = "c5c7a2582f1f32c244c5ef465e13fbfc"
group_username = -4877905193  # group ID

# ✅ Explicitly set tesseract binary path
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

# Firebase setup (skip locally if not needed)
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
# ALERT DOC HELPERS
# ===============================
def set_current_alert(active: bool, message: str = "", loss_count: int = 0):
    if not db: return
    now = datetime.datetime.now()
    db.collection("alerts").document("current").set({
        "active": active,
        "message": message,
        "loss_count": loss_count,
        "timestamp": int(now.timestamp() * 1000),
        "time_str": now.strftime("%Y-%m-%d %H:%M:%S"),
        "type": "LOSS_ALERT"
    })

def get_alert_threshold():
    if not db: return 2
    doc = db.collection("settings").document("config").get()
    if doc.exists:
        return doc.to_dict().get("alert_threshold", 2)
    return 2

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
# DAILY RESET
# ===============================
def check_daily_reset():
    global loss_count, last_reset_date
    today = datetime.date.today()
    if today != last_reset_date:
        loss_count = 0
        last_reset_date = today
        print("🔄 Daily reset at midnight")

def schedule_midnight_reset():
    now = datetime.datetime.now()
    tomorrow = now + datetime.timedelta(days=1)
    midnight = datetime.datetime.combine(tomorrow.date(), datetime.time.min)
    seconds_until_midnight = (midnight - now).total_seconds()

    def reset_task():
        check_daily_reset()
        schedule_midnight_reset()

    threading.Timer(seconds_until_midnight, reset_task).start()

# ===============================
# TELETHON HANDLER
# ===============================
client = TelegramClient("session", api_id, api_hash)

@client.on(events.NewMessage(chats=group_username))
async def handler(event):
    global loss_count, last_entry_text
    check_daily_reset()

    if event.message.message:
        lines = event.message.message.split("\n")
        if len(lines)>=2:
            entry_text = lines[1].strip().split()[-1]
            last_entry_text = entry_text
            print(f"New entry: {entry_text}")

    if event.message.media:
        img_bytes = await event.message.download_media(bytes)
        extracted = extract_profit_number_from_bytes(img_bytes)
        if extracted and last_entry_text:
            result_text = determine_result(extracted)
            print(f"Result: {result_text}")

            if result_text=="LOSS":
                loss_count+=1
            elif result_text=="WIN":
                loss_count=0
                set_current_alert(active=False)

            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            if db:
                db.collection("signals").document(today_str).collection("entries").add({
                    "entry": last_entry_text,
                    "result": result_text,
                    "timestamp": int(now.timestamp()*1000),
                    "time_str": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "loss_count": loss_count
                })

            last_entry_text=None
            threshold=get_alert_threshold()
            if loss_count>=threshold:
                set_current_alert(
                    active=True,
                    message=f"{threshold} or more consecutive losses detected!",
                    loss_count=loss_count
                )

# ===============================
# RUN
# ===============================
async def main():
    await client.start()
    print("🚀 Listening for signals...")
    await client.run_until_disconnected()

threading.Thread(target=lambda: asyncio.run(main()),daemon=True).start()
schedule_midnight_reset()
