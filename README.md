# O2Ring Auto Downloader & Manager

A simple tool to automatically download your sleep data from Viatom/Wellue servers and prepare it for use in programs like **OSCAR**.

## 🌟 What this tool does
- **Automatic Downloads**: No need to manually export files from your phone or PC app.
- **Smart Merging**: Automatically combines long sleep sessions that the device might have split into multiple files.
- **Note & Label Sync**: Syncs your "Remarks" (notes) and "Stars" (flags) from the Viatom cloud directly into the filenames.
- **OSCAR Ready**: Can automatically generate CSV files that OSCAR can read instantly.
- **Clean Data**: Automatically ignores very short sessions (like brief tests or accidental starts).

---

## 🚀 Quick Start (Windows)

### 1. Install `uv` (The Easy Way)
`uv` is a modern tool that handles Python and dependencies for you. You don't need to manually install Python!

1. Open **PowerShell** (Click Start, type `PowerShell`, and press Enter).
2. Copy and paste this command, then press Enter:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
3. Close and reopen PowerShell to finish the setup.

### 2. Set Up the Project
The best way to get the project and keep it updated is using **Git**:

1. In PowerShell, navigate to where you want the project (e.g., `cd C:\`).
2. Run:
   ```powershell
   git clone https://github.com/AJolly/O2RingCloudDownloader
   cd O2RingCloudDownloader
   ```
*(If you don't have Git, you can also just download and extract the ZIP file from GitHub, but Git makes updates much easier!)*

3. In your folder, find `o2_config.sample.ini`.
4. Copy (or rename) it to `o2_config.ini`.

### 3. Run the Downloader
Navigate to your folder in PowerShell and run:
```powershell
uv run o2_downloader.py
```
*The first time you run this, it will attempt to find your login details from the official "O2 Insight Pro" PC app. If it finds them, it will save them to your `o2_config.ini` automatically.*

---

## ⚙️ Configuration (`o2_config.ini`)
Open `o2_config.ini` in Notepad to customize how the tool works:

- **`email` / `password`**: Your Viatom/Wellue account login.
- **`output_dir`**: Where to save the files (default is a folder named `data`).
- **`generate_csv`**: Set to `true` to create files that OSCAR can read.
- **`skip_short_sessions_under_mins`**: Ignores sessions shorter than this (default is 60 minutes).
- **`launch_after`**: (Optional) Put the path to OSCAR here to open it automatically after downloading.
  *Example:* `launch_after = C:\Program Files\OSCAR\OSCAR.exe`

---

## 📂 Managing Your Data

### The `data` Folder
All your downloaded files go here. 
- `.bin` or `.dat` files are the raw data.
- `.csv` files are the ones you import into OSCAR.

### Ignoring Sessions
If there's a specific session you never want to see again, you can add its ID or its timestamp (the numbers at the start of the filename) to a file named `ignored_sessions.txt` in the main folder.

### Automatic Merging
If you have a 10-hour sleep that the O2Ring split into three 3-hour files, this tool will detect they belong together and create a single `_merged.csv` file for you.

---

## ❓ Troubleshooting

**"Login failed"**
Double-check your email and password in `o2_config.ini`. If you use the PC app, make sure you've logged in there at least once.

**"No sessions found"**
Ensure your ring has synced with your phone app recently. The data must be in the "Cloud" for this tool to see it.

**"uv is not recognized"**
Make sure you restarted your PowerShell window after installing `uv`.
