# 📦 Footage Offloader

A Raspberry Pi-based appliance for automatically copying footage from USB SSDs to your NAS over SMB. Designed for video production workflows — plug in your SSD, select files, and offload to your server with progress tracking and Discord notifications.

## Features

- **Auto SSD detection** — Plug in any USB SSD and it's ready to browse
- **Dark, minimal UI** — Mobile and desktop responsive web interface
- **Smart file filtering** — Only shows real files (no `.DS_Store`, `Thumbs.db`, metadata)
- **SMB/CIFS** — Copy to any NAS or server with SMB shares
- **Tailscale ready** — Access remotely via Tailscale network
- **Local hostname** — Reachable via `hostname.local` (mDNS/Avahi)
- **Speed test** — Measure transfer speed and get ETA estimates
- **Background copy** — Copies continue even after closing the browser
- **Discord notifications** — Get notified at 25%, 50%, 75%, and 100%
- **Project folders** — Select or create destination subfolders per project
- **Confirmation dialogs** — Start and cancel with confirmation prompts

## Quick Start

### 1. Flash Raspberry Pi OS

Flash **Raspberry Pi OS Lite (64-bit)** to your SD card. Enable SSH during setup.

### 2. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/footage-offloader.git
cd footage-offloader
sudo bash install.sh
```

The installer will:
- Install system dependencies (`cifs-utils`, `smbclient`, filesystem drivers)
- Install Tailscale (if not present)
- Set up the Python application
- Create a systemd service (auto-start on boot)
- Configure udev rules (auto-detect USB SSD)
- Enable mDNS (`.local` hostname)

### 3. Configure

Open the web UI and go to **Settings**:

```
http://raspberrypi.local:5000
```

Set your NAS connection details:
- **Server Address** — IP or hostname of your NAS (use Tailscale IP for remote)
- **Share Name** — The SMB share name
- **Username / Password** — SMB credentials

Optionally configure **Discord notifications** with a webhook URL.

### 4. Use

1. Plug in your SSD via USB
2. Open the web UI
3. Browse and select files
4. Choose a destination project folder
5. Start the copy

## Updating

From GitHub:
```bash
sudo bash update.sh https://github.com/YOUR_USERNAME/footage-offloader.git
```

## Architecture

```
footage-offloader/
├── app.py                 # Flask application entry point
├── offloader/
│   ├── routes.py          # API endpoints
│   ├── ssd.py             # SSD detection & file listing
│   ├── smb.py             # SMB mount & management
│   ├── copy_manager.py    # Background copy with progress
│   ├── discord_notify.py  # Discord webhook integration
│   └── speedtest.py       # Write speed measurement
├── static/
│   ├── css/style.css      # Dark theme styles
│   └── js/app.js          # Frontend logic
├── templates/
│   ├── index.html         # Main page
│   └── settings.html      # Settings page
├── systemd/               # Systemd service file
├── udev/                  # Auto-detect USB rules
├── install.sh             # One-line installer
└── update.sh              # GitHub update script
```

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/ssd/info` | GET | SSD mount status & usage |
| `/api/ssd/mount` | POST | Mount detected USB SSD |
| `/api/ssd/unmount` | POST | Safely eject SSD |
| `/api/ssd/files?path=` | GET | List files at path |
| `/api/smb/test` | POST | Test SMB connection |
| `/api/smb/mount` | POST | Mount SMB share |
| `/api/smb/subfolders` | GET | List destination subfolders |
| `/api/smb/create-subfolder` | POST | Create new project folder |
| `/api/copy/start` | POST | Start background copy |
| `/api/copy/status` | GET | Get copy progress |
| `/api/copy/cancel` | POST | Cancel running copy |
| `/api/speedtest` | POST | Run write speed test |
| `/api/config` | GET/POST | Read/write settings |
| `/api/system/info` | GET | Hostname, Tailscale IP, uptime |

## Requirements

- Raspberry Pi 4 (or newer)
- Raspberry Pi OS (64-bit recommended)
- USB-C SSD
- Network connection (Ethernet recommended for speed)
- NAS with SMB share

## License

MIT
