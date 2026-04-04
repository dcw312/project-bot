# Discord Bot Setup

## 1. Create the Application

- [ ] Go to https://discord.com/developers/applications
- [ ] Click **New Application**
- [ ] Give it a name (e.g. `project-bot`)
- [ ] Click **Create**

## 2. Create the Bot User

- [ ] In the left sidebar, click **Bot**
- [ ] Click **Add Bot** → **Yes, do it!**
- [ ] Set a username if desired
- [ ] Under **Token**, click **Reset Token** and copy it — save this as `DISCORD_BOT_TOKEN` in your `.env`

## 3. Configure Privileged Intents

- [ ] On the **Bot** page, scroll to **Privileged Gateway Intents**
- [ ] Enable **Message Content Intent** (required to read message text)
- [ ] Enable **Server Members Intent** (optional, needed if you look up member info)
- [ ] Click **Save Changes**

## 4. Set Bot Permissions

- [ ] In the left sidebar, click **OAuth2 → URL Generator**
- [ ] Under **Scopes**, check `bot` and `applications.commands`
- [ ] Under **Bot Permissions**, check:
  - [ ] `Send Messages`
  - [ ] `Read Message History`
  - [ ] `Use Slash Commands`
- [ ] Copy the generated URL at the bottom (https://discord.com/oauth2/authorize?client_id=1489986645058125854&permissions=2815078332319808&integration_type=0&scope=bot+applications.commands)

## 5. Invite the Bot to Your Server

- [ ] Open the generated invite URL in your browser
- [ ] Select your development Discord server
- [ ] Click **Authorize**
- [ ] Confirm the bot now appears in the server's member list

## 6. Configure Your Environment

- [ ] Copy `.env.example` to `.env` (or create `.env` if it doesn't exist)
- [ ] Add the following:

```
DISCORD_BOT_TOKEN=your_token_here
SMOKE_TEST_RECIPIENT=your_discord_user_id
```

> To find your Discord user ID: **Settings → Advanced → enable Developer Mode**, then right-click your username → **Copy User ID**.

---

## Smoke Test

Verifies the token, opens a DM channel, and sends a test message to `SMOKE_TEST_RECIPIENT`.

```bash
export $(grep -v '^#' .env | xargs) && python3 -c "
import urllib.request, urllib.error, json, os, sys

token = os.environ['DISCORD_BOT_TOKEN']
user_id = os.environ['SMOKE_TEST_RECIPIENT']

def api(method, path, body=None):
    req = urllib.request.Request(
        f'https://discord.com/api/v10{path}',
        headers={
            'Authorization': f'Bot {token}',
            'Content-Type': 'application/json',
            'User-Agent': 'DiscordBot (project-bot, 1.0)'
        },
        method=method,
        data=json.dumps(body).encode() if body else None
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return (json.loads(raw) if raw else {}), r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw), e.code
        except Exception:
            return {'raw': raw.decode()}, e.code

data, status = api('GET', '/users/@me')
print(f'[1/3] Bot identity: {data.get(\"username\")} (status {status})')
if status != 200: sys.exit(1)

data, status = api('POST', '/users/@me/channels', {'recipient_id': user_id})
print(f'[2/3] Open DM channel: status={status}')
if status != 200: print(f'      Error: {data}'); sys.exit(1)

data, status = api('POST', f'/channels/{data[\"id\"]}/messages', {'content': 'smoke test — project-bot is alive!'})
print(f'[3/3] Send message: status={status}')
if status == 200: print(f'      OK: message id={data[\"id\"]}')
else: print(f'      Error: {data}'); sys.exit(1)
"
```

**Expected output:**
```
[1/3] Bot identity: project-bot (status 200)
[2/3] Open DM channel: status=200
[3/3] Send message: status=200
      OK: message id=...
```

> **Note:** The bot must share a server with the recipient before DMs are allowed.
