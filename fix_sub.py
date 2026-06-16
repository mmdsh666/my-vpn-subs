import base64

# خواندن فایل فعلی
with open("farahvpn_subscription.txt", "r", encoding="utf-8") as f:
    encoded = f.read().strip()

# Decode کردن
plain = base64.b64decode(encoded).decode("utf-8")

# نوشتن فایل جدید (هر کانفیگ در خط جدا)
with open("farahvpn_subscription_fixed.txt", "w", encoding="utf-8") as f:
    f.write(plain)

print("✅ فایل fixed با موفقیت ساخته شد!")
print("فایل جدید: farahvpn_subscription_fixed.txt")