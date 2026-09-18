# bamboodeploy

Scan and code-sign Windows `.exe` and `.msi` files from PyInstaller, Nuitka, cx_Freeze, or any build script. No certificate to buy, no hardware token, no Windows VM. Files are signed with the Bamboo Deploy certificate after a malware scan, so the publisher shown to users is Bamboo Deploy.

```bash
pip install bamboodeploy
bamboo-sign sign dist/myapp.exe
```

Upload, sign, download the signed file in place. Pure standard library, Python 3.8 or later, works on Windows, macOS, and Linux. `python -m bamboodeploy` works too.

## Why PyInstaller builds need this

A PyInstaller exe unpacks a Python runtime into a temp folder at start, which is the same shape as a lot of malware droppers, so heuristic antivirus engines flag unsigned PyInstaller output more than almost anything else. A valid signature is the main remedy. Check a build before you ship it, free:

```bash
bamboo-sign scan dist/myapp.exe --fail-on-findings
```

## Setup

1. Create an API key in the [Bamboo Deploy dashboard](https://www.bamboodeploy.com/dashboard/) under **API Keys**.
2. Export it as `BAMBOO_API_KEY`, or put `BAMBOO_API_KEY=bd_live_...` in a `.bamboorc` file in the project or home directory.
3. Uploads and scans work on the free tier. Signing needs a Premium subscription.

## In a build script

```bash
pyinstaller --onefile app.py
bamboo-sign sign dist/app.exe
```

Or from Python:

```python
from bamboodeploy import sign_file, scan_file

sign_file("dist/app.exe", out="dist/app-signed.exe", log=print)
result = scan_file("dist/app.exe")
print(result["summary"])
```

## Commands

```
bamboo-sign sign <file> [--out path] [--timeout 900]   sign, replace in place by default
bamboo-sign scan <file> [--json] [--fail-on-findings]  free: scan report + SmartScreen readiness
bamboo-sign status [app-id]                            list uploads, or one app's scan + sign state
bamboo-sign delete <app-id>
bamboo-sign config                                     show which key and API base are in use
```

Exit codes: `0` ok, `1` error, `2` Premium required, `3` publisher verification required, `4` timeout or waiting for review, `5` scan findings (with `--fail-on-findings`).

More: [developer docs](https://www.bamboodeploy.com/docs/).

## License

MIT
