# At Your Service Discord Bot

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)

[繁體中文](README.md) | [English](README.en.md)

A multifunctional Discord bot built with Python 3.11 and discord.py. It combines server administration, moderation, new-member verification, ticketing, custom interaction panels, scheduled reminders, chat summarization, and speech-to-text. Data is persisted in SQLite, and the user interface supports Traditional Chinese, Simplified Chinese, and English.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Discord Setup](#discord-setup)
- [Environment Variables](#environment-variables)
- [Application Configuration](#application-configuration)
- [Running the Bot](#running-the-bot)
- [Slash Commands](#slash-commands)
- [Localization](#localization)
- [Data and Logs](#data-and-logs)
- [Database Backups](#database-backups)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Features

### Administration and Automation

- Interactive server settings, log-channel settings, and member-count channels.
- Bulk message deletion, announcements, chat export, and deleted-message logs.
- Custom text triggers, welcome messages, and scheduled reminders.
- Automatic member-count updates.

### Protection

- Honeypot channels that delete violations and ban users when permissions allow.
- Cross-channel and same-channel duplicate-message spam detection.
- Anti-raid detection for a burst of member joins within a short time window.
- New-member verification: passive risk scoring plus button verification, with high-risk members routed to a private human-review channel. Enabling the feature can grandfather in existing members and lock channel send permissions in one confirmed action.
- Malicious URL checks through the Google Safe Browsing API and a customizable keyword blocklist.
- Image-based scam detection: perceptual-hash matching against a known-scam-image database, plus QR code decoding on image attachments (any URL found is routed through the same URL-safety check).
- User allowlists and Discord moderation notifications.
- Important protection events and outcomes are written to a database audit log and rotating local logs.

### Tickets and Custom Panels

- Persistent ticket panels with open, close, delete, and transcript actions.
- Custom embeds, buttons, forms, role actions, and review workflows.
- Persistent Discord views are registered again after a restart.

### AI Features

- Chat summaries powered by Groq.
- Speech-to-text for `ogg`, `m4a`, `mp3`, `wav`, `flac`, and `aac` files.
- Missing Groq credentials disable only AI features; other modules can still run.

To summarize a conversation, reply to its starting message and mention the bot. To transcribe audio, mention the bot while attaching an audio file, or reply to a message containing audio and mention the bot.

## Requirements

- Python 3.11. The development environment uses Python 3.11.9.
- A Discord bot token.
- A Groq API key for AI features.
- A Google Safe Browsing API key for malicious-link checks.

## Installation

### Windows PowerShell

```powershell
git clone <repository-url>
cd At_Your_Service_Discord_Bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

### Linux

The following example targets Ubuntu/Debian. `libzbar0` is required by `pyzbar` for QR code decoding.

```bash
sudo apt update
sudo apt install -y git python3.11 python3.11-venv python3.11-dev libzbar0
git clone <repository-url>
cd At_Your_Service_Discord_Bot
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example token.env
```

Edit `token.env`, then start the bot:

```bash
python bot.py
```

If your distribution does not provide `python3.11` in its package repositories, install Python 3.11 using the distribution's recommended method first, then continue from the venv step.

The project does not currently include a dependency lock file. Python packages are installed from `pyproject.toml`.

## Discord Setup

1. Create an application and bot in the Discord Developer Portal.
2. Enable Server Members Intent and Message Content Intent.
3. Include the `bot` and `applications.commands` scopes in the invite URL.
4. Grant permissions required by enabled features: View Channels, Send Messages, Read Message History, Manage Messages, Moderate Members, Ban Members, Manage Channels, Manage Roles, Attach Files, and Embed Links.
5. Place the bot's role above members and roles it needs to manage.

## Environment Variables

Windows PowerShell:

```powershell
Copy-Item .env.example token.env
```

Linux:

```bash
cp .env.example token.env
```

Edit `token.env`:

```dotenv
DISCORD_BOT_TOKEN=your_discord_bot_token
GROQ_API_KEY=your_groq_api_key
GOOGLE_SAFE_BROWSING_KEY=your_google_safe_browsing_key
```

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | Yes | Starts the Discord bot |
| `GROQ_API_KEY` | No | Chat summaries and speech-to-text |
| `GOOGLE_SAFE_BROWSING_KEY` | No | Malicious URL checks |
| `BACKUP_RCLONE_REMOTE` | No | rclone remote name for offsite database backup sync to Google Drive, see Database Backups |

`token.env` is excluded by `.gitignore` and must not be committed.

## Application Configuration

Global settings are stored in `config/config.json`.

| Setting | Description |
| --- | --- |
| `anti_spam.time_window_seconds` | Spam detection window in seconds |
| `anti_spam.channel_threshold` | Cross-channel duplicate threshold |
| `anti_spam.same_channel_threshold` | Same-channel duplicate threshold |
| `anti_spam.timeout_hours` | Timeout duration after detection |
| `anti_spam.cleanup_interval_minutes` | History cleanup interval |
| `ai_settings.chat_summary_model` | Groq chat-summary model |
| `ai_settings.voice_transcribe_model` | Groq transcription model |
| `ai_settings.chat_history_limit` | Maximum messages included in a summary |
| `people_counting.update_interval_minutes` | Member-count update interval |
| `anti_raid.join_window_seconds` | Anti-raid detection window in seconds |
| `anti_raid.join_threshold` | Join count within the window that triggers an alert |
| `verification.new_account_days` | Account age (days) below which an account is treated as "new" for risk scoring |
| `verification.risk_threshold` | Risk score at or above which a member is routed to human review |

Verify that your Groq account can access a model before changing its name.

## Running the Bot

Windows PowerShell or a Linux shell with the venv activated:

```powershell
python bot.py
```

The bot loads all cogs and synchronizes global slash commands at startup. Discord may take some time to display new global commands in every server.

## Slash Commands

| Command | Permission | Description |
| --- | --- | --- |
| `/server_setting` | Administrator | General server settings |
| `/anti_fraud_setting` | Administrator | Honeypot, allowlist, anti-spam, link checks, anti-raid, and new-member verification |
| `/trigger_setting` | Administrator | Manage text triggers |
| `/custom_panel` | Administrator | Create custom interaction panels |
| `/welcome_setting` | Administrator | Configure welcome messages |
| `/warning_setting` | Administrator | Manage scheduled reminders |
| `/set_language` | Administrator | Change the interface language |
| `/delete` | Administrator | Delete up to 100 recent messages |
| `/announcement` | Administrator | Send a bot announcement |
| `/export_chat` | User | Export messages from the current channel |
| `/ticket` | Administrator | Create a ticket panel |

The new-member verification system has no dedicated command; it is configured and enabled from the menu inside `/anti_fraud_setting`.

Link-checker keyword blocklist management, GDPR audit-log deletion, and the known-scam-image hash database are not exposed as Discord commands. They're operated by the bot owner typing commands directly into the terminal running the bot (`admin keyword list/add/remove`, `admin gdpr delete <user_id>`, `admin scamimage list/add/remove/sync`); the terminal prints full usage on startup. Wrap paths or keywords containing spaces in double quotes, e.g. `admin scamimage sync "C:\path\scam image"`.

## Localization

Supported locales are `zh-TW`, `zh-CN`, and `en-US`. Translations are stored in `locales/languages.json`. Administrators can change the locale with `/set_language` or the settings panel.

## Data and Logs

All persistent data lives in the SQLite database `data/bot.db` (WAL mode), including text triggers, link-checker keywords, audit logs, verification records, tickets and panels, custom panels, scheduled reminders, and guild settings. Some features (tickets, custom panels, reminders, guild settings) additionally keep an in-memory cache for fast reads; writes update both the database and the cache.

| Path | Contents |
| --- | --- |
| `data/bot.db` | Primary database (SQLite); all persistent bot data |
| `logs/bot.log` | Errors and important events |

Logs rotate at 5 MB and retain up to five backups. Log files are ignored by Git. They may contain server, channel, and user IDs, so review them before sharing.

SQLite runtime files and legacy JSON data files are ignored by Git and must not be committed. Run only one bot process against a data directory to avoid concurrent database writes. See Database Backups below for backing up `data/bot.db`.

## Database Backups

`scripts/backup_databases.py` provides a scheduled daily backup script with optional offsite sync to Google Drive.

### How it works

- Backs up using SQLite's official Online Backup API (`Connection.backup()`), not a plain file copy - `data/bot.db` runs in WAL mode, and copying the file directly risks grabbing a torn write mid-checkpoint. `backup()` guarantees a consistent snapshot even while the bot is actively writing.
- Local output goes to `backups/bot_db/<date>.db`, keeping the last 7 daily backups plus every 1st-of-month snapshot indefinitely (never rotated away).
- Run logs go to `logs/backup.log` (kept separate from `logs/bot.log` so scheduled runs don't interfere with the bot's own log rotation timing). The script exits with a non-zero code if any backup fails, so schedulers can detect failures.

### Run it once manually

```bash
python scripts/backup_databases.py
```

### Schedule it daily

Windows (Task Scheduler):

```powershell
schtasks /create /tn "HoneypotBotBackup" /tr "python C:\path\to\repo\scripts\backup_databases.py" /sc daily /st 03:00
```

Linux (cron, `crontab -e`):

```
0 3 * * * cd /path/to/repo && /path/to/venv/bin/python scripts/backup_databases.py
```

### Offsite sync to Google Drive (optional, strongly recommended)

Local backups alone don't protect against the disk itself failing - offsite sync is the real protection. Steps:

1. Install [rclone](https://rclone.org/downloads/).
2. Run `rclone config` and choose, in order: `n` (new remote) -> a name of your choice (e.g. `gdrive`) -> storage type `Google Drive` -> keep defaults for the remaining prompts -> answer `y` to "Use auto config?", which opens a browser to sign in and authorize your Google account.
3. Run `rclone lsd gdrive:` to confirm it can list folders in your Drive - that confirms authorization worked.
4. Add to `token.env`:

   ```dotenv
   BACKUP_RCLONE_REMOTE=gdrive:honeypot-bot-backups
   ```

5. From then on, every run of `backup_databases.py` automatically `rclone copy`s the entire `backups/` folder there after the local backup succeeds. **Deliberately `copy`, not `sync`**: `sync` would delete remote files that local rotation already removed, meaning the offsite copy would inherit the 7-day local retention instead of being an independent safety net. `copy` only uploads incrementally and never deletes existing remote files - how long the offsite copies are kept is up to you to manage in Drive.
6. If you run this on multiple machines (e.g. local development plus a production host), run `rclone config` separately on each machine, or securely copy over the configured `rclone.conf` file.

If `BACKUP_RCLONE_REMOTE` is unset, or the `rclone` binary isn't found, the script only performs the local backup and this is not treated as a failure.

### Restore drill

An unverified backup is not a backup. Periodically rehearse a real restore:

1. Stop the bot.
2. Overwrite `data/bot.db` with the backup you want to restore, and delete any leftover `data/bot.db-wal` / `data/bot.db-shm` files.
3. Restart the bot and confirm the data is intact.

## Project Structure

```text
honeypot-discord-bot/
|-- bot.py                 # Entry point and extension loading
|-- pyproject.toml         # Python version, dependencies, and development tools
|-- core/                  # Configuration, database, i18n, logging, and lifecycle
|-- features/              # Feature-oriented cogs, panels, services, and repositories
|-- hubs/                  # Cross-feature settings entry points and panels
|-- admin/                 # Local console administration tools
|-- dev/                   # Local development tools excluded from version control
|-- config/config.json     # Global configuration
|-- data/                  # SQLite persistent data
|-- scripts/               # Scheduled/ops scripts (e.g. database backups)
|-- locales/languages.json # Localized text
|-- .env.example           # Environment variable template
|-- LICENSE                # GPL-3.0 license
|-- README.md              # Traditional Chinese documentation
`-- README.en.md           # English documentation
```

## Troubleshooting

### Slash Commands Do Not Appear

Confirm that the invite includes `applications.commands`, check startup logs for synchronization errors, and allow time for Discord's global synchronization.

### The Bot Cannot Read Messages

Enable Message Content Intent and grant View Channels and Read Message History.

### Moderation or Role Actions Fail

Grant the relevant permission and place the bot's highest role above the target. A bot cannot manage the server owner or members with higher roles.

### AI or Link Checks Do Not Work

Check the corresponding API key in `token.env`, verify model availability, and inspect the terminal and `logs/bot.log`.

## Contributing

Issues and pull requests are welcome. Pull requests should:

- Run on Python 3.11.
- Contain no tokens, API keys, logs, or private data.
- Add `zh-TW`, `zh-CN`, and `en-US` translations for new user-facing text.
- Include validation appropriate to the scope of the change.

## Security

- Never commit tokens, API keys, logs, or private exports.
- Rotate any credential that has appeared in Git history; deleting the file is not sufficient.
- Update dependencies, back up data, and review bot permissions regularly.

## License

This project is licensed under the GNU General Public License v3.0. See `LICENSE` for the complete terms.

## Acknowledgements

Thanks to Linvin for the honeypot concept and related source-code references.
