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
- [ ] Copy the generated URL at the bottom

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
```

---

## Smoke Test

Verify the token is valid and the bot can connect to Discord:

```bash
python -c "
import os, urllib.request, json, sys

token = os.environ.get('DISCORD_BOT_TOKEN')
if not token:
    print('ERROR: DISCORD_BOT_TOKEN not set')
    sys.exit(1)

req = urllib.request.Request(
    'https://discord.com/api/v10/users/@me',
    headers={'Authorization': f'Bot {token}'}
)
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    print(f'OK: Connected as {data[\"username\"]}#{data[\"discriminator\"]} (id={data[\"id\"]})')
except urllib.error.HTTPError as e:
    print(f'FAILED: HTTP {e.code} - check your token')
    sys.exit(1)
"
```

Run it with:

```bash
DISCORD_BOT_TOKEN=your_token_here python -c "..."
```

Or if your `.env` is loaded:

```bash
export $(grep -v '^#' .env | xargs) && python -c "..."
```

**Expected output:**
```
OK: Connected as project-bot#1234 (id=123456789012345678)
```
