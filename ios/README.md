# LAPIO BTC — iOS App

WKWebView wrapper + native chat + push notifications for the LAPIO bitcoin miner advisor.

---

## Prerequisites

- Xcode 15+
- [XcodeGen](https://github.com/yonaskolb/XcodeGen) — `brew install xcodegen`
- An Apple Developer account ($99/yr) enrolled in the Apple Developer Program

---

## One-time setup

### 1. Edit Config.swift

Open `LapioBTC/Config.swift` and set:

```swift
enum Config {
    static let baseURL    = "https://lapio.dev"       // your deployment URL
    static let appPassword = "your-web-login-password" // APP_PASSWORD from .env
}
```

### 2. Generate the Xcode project

```bash
cd ios/
xcodegen generate
open LapioBTC.xcodeproj
```

### 3. Set your Team ID in Xcode

In Xcode → Project → Signing & Capabilities:
- Set **Team** to your Apple Developer team.
- **Bundle Identifier** is `com.lapio.btc` — change if needed (must match APNS setup).

### 4. Add an App Icon

Replace the placeholder in `LapioBTC/Assets.xcassets/AppIcon.appiconset/` with a
1024×1024 PNG named `AppIcon-1024.png`, then update `Contents.json`:

```json
{
  "images": [
    {
      "filename": "AppIcon-1024.png",
      "idiom": "universal",
      "platform": "ios",
      "size": "1024x1024"
    }
  ],
  ...
}
```

---

## APNs Push Notification Setup

### Step 1 — Create an APNs Auth Key

1. Go to [Apple Developer portal](https://developer.apple.com) → Certificates, Identifiers & Profiles → **Keys**
2. Click **+** → Name: `LAPIO APNs Key` → enable **Apple Push Notifications service (APNs)**
3. Click **Register** → **Download** the `.p8` file (you can only download once — save it safely)
4. Note your **Key ID** and **Team ID** (top-right of the portal)

### Step 2 — Upload key to server

```bash
scp AuthKey_XXXXXXXXXX.p8 root@172.233.136.138:/home/miner/apns_key.p8
ssh root@172.233.136.138 "chmod 600 /home/miner/apns_key.p8"
```

### Step 3 — Add env vars to server .env

```
APNS_TEAM_ID=XXXXXXXXXX
APNS_KEY_ID=XXXXXXXXXX
APNS_KEY_FILE=/home/miner/apns_key.p8
APNS_BUNDLE_ID=com.lapio.btc
```

Then restart the server:

```bash
ssh root@172.233.136.138 "systemctl restart miner-advisor"
```

### Step 4 — Register the App ID for push

In the Apple Developer portal → **Identifiers** → find or create `com.lapio.btc` →
enable **Push Notifications** capability → save.

---

## Building & running

```bash
# Simulator (push notifications won't work — use a real device)
# Select iPhone simulator in Xcode → Cmd+R

# Real device (push works)
# Connect iPhone → select it in Xcode → Cmd+R
# Accept the "Trust This Computer" prompt on the phone
```

---

## Verifying the backend

```bash
# Test chat send/receive
curl -s -X POST https://lapio.dev/api/chat/send \
  -H "Content-Type: application/json" \
  -H "X-App-Password: your-password" \
  -d '{"text": "hello"}' | jq .

# Check stored messages
curl -s https://lapio.dev/api/chat/messages \
  -H "X-App-Password: your-password" | jq .

# Register a test push token
curl -s -X POST https://lapio.dev/api/push/register \
  -H "Content-Type: application/json" \
  -H "X-App-Password: your-password" \
  -d '{"token": "test-token-abc123"}' | jq .
```

---

## App Store submission (when ready)

1. Xcode → Product → **Archive**
2. Distribute App → App Store Connect → upload
3. In App Store Connect, fill in metadata, screenshots, privacy info
4. Submit for review
