import json
import os
import re
import sys

from . import __version__
from .client import DEFAULT_BASE, BambooClient, BambooError, expand_files, resolve_key, scan_file, sign_files

HELP = """bamboo-sign %s  Scan and code-sign Windows binaries via Bamboo Deploy

Usage:
  bamboo-sign sign <file>... [--out <path>] [--timeout 900] [--interval 10]
  bamboo-sign scan <file> [--json] [--fail-on-findings]
  bamboo-sign status [app-id]
  bamboo-sign delete <app-id>
  bamboo-sign config

Also: python -m bamboodeploy <command>

Options:
  --api-key <key>   API key (else BAMBOO_API_KEY env or .bamboorc)
  --out <path>      Where to write the signed file (default: replace input).
                    With several files, a folder
  --timeout <s>     Max seconds to wait for signing (default 900)
  --interval <s>    Poll interval in seconds (default 10)
  --json            Machine-readable output
  --quiet           No progress output
  --api-base <url>  API base URL (else BAMBOO_API_BASE)

Sign several files at once: bamboo-sign sign dist/app.exe "dist/*.dll"

Exit codes: 0 ok, 1 error, 2 premium required, 3 verification required, 4 timeout or pending review, 5 scan findings""" % __version__

VALUED = {"out", "timeout", "interval", "limit", "api-key", "api-base"}
INSTALLER = re.compile(r"NSIS|Inno|MSI|Installer|\.NET|dotnet|Electron|Squirrel|WiX", re.I)


def _parse(argv):
    pos, opt = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            opt[k] = v
        elif a.startswith("--"):
            k = a[2:]
            if k in VALUED and i + 1 < len(argv):
                i += 1
                opt[k] = argv[i]
            else:
                opt[k] = True
        else:
            pos.append(a)
        i += 1
    return (pos[0] if pos else None), pos[1:], opt


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd, pos, opt = _parse(argv)
    as_json = "json" in opt
    log = (lambda m: None) if ("quiet" in opt or as_json) else (lambda m: print(m, file=sys.stderr))
    common = dict(api_key=opt.get("api-key"), api_base=opt.get("api-base"), log=log)
    try:
        if cmd in (None, "help") or "help" in opt:
            print(HELP)
            return 0
        if cmd == "sign":
            if not pos:
                raise BambooError("Usage: bamboo-sign sign <file>...", "usage")
            r = sign_files(expand_files(pos), out=opt.get("out"), timeout=int(opt.get("timeout", 900)),
                           interval=int(opt.get("interval", 10)), **common)
            if as_json:
                print(json.dumps({"ok": True, **r[0], "files": r} if len(r) == 1 else {"ok": True, "files": r}))
            else:
                for x in r:
                    print("Signed: %s" % x["out"])
            return 0
        if cmd == "scan":
            if not pos:
                raise BambooError("Usage: bamboo-sign scan <file>", "usage")
            r = scan_file(pos[0], timeout=int(opt.get("timeout", 600)), **common)
            s = r["summary"]
            packer, yara, cs = s.get("packer"), s.get("yara_matches"), s.get("code_signing")
            findings = bool(yara) or bool(packer and not INSTALLER.search(packer))
            if as_json:
                print(json.dumps({"ok": True, "app_id": r["app_id"], "findings": findings, "summary": s, "report": r["report"]}))
            else:
                print(r["report"].strip())
                print()
                print("Code signing: %s%s%s" % (cs or "unknown", "\nPacker: " + packer if packer else "", "\nYARA: " + yara if yara else ""))
                if cs and "not signed" in cs.lower():
                    print("This build is unsigned, so Windows SmartScreen will warn users. Sign it with: bamboo-sign sign <file>")
            return 5 if findings and "fail-on-findings" in opt else 0
        if cmd == "status":
            c = BambooClient(**common)
            if pos:
                print(json.dumps(c.status(pos[0]), indent=2))
            else:
                r = c.list(int(opt.get("limit", 20)))
                if as_json:
                    print(json.dumps(r))
                else:
                    for u in r["uploads"]:
                        print("%s  %-32s  scan:%-7s sign:%s" % (u["id"], u["file_name"], u["scan"]["status"], u["sign"]["status"]))
            return 0
        if cmd == "delete":
            if not pos:
                raise BambooError("Usage: bamboo-sign delete <app-id>", "usage")
            BambooClient(**common).delete(pos[0])
            print("Deleted")
            return 0
        if cmd == "config":
            k = resolve_key(opt.get("api-key"))
            print("API base: %s" % (opt.get("api-base") or os.environ.get("BAMBOO_API_BASE") or DEFAULT_BASE))
            print("API key:  %s" % (k[:12] + "..." + k[-4:] if k else "(none) set BAMBOO_API_KEY or create .bamboorc"))
            return 0
        raise BambooError("Unknown command: %s\n\n%s" % (cmd, HELP), "usage")
    except BambooError as e:
        code = {"premium_required": 2, "connections_required": 3, "timeout": 4}.get(e.code, 1)
        if as_json:
            print(json.dumps({"ok": False, "error": e.code, "message": str(e), **(e.body or {})}))
        else:
            print("Error: %s" % e, file=sys.stderr)
            if e.code == "premium_required":
                print("Upgrade at https://www.bamboodeploy.com/dashboard/#billing", file=sys.stderr)
            elif e.code == "connections_required":
                print("Verify your publisher connections at https://www.bamboodeploy.com/dashboard/", file=sys.stderr)
            elif e.code == "timeout" and (e.body.get("sign") or {}).get("pending_reason") == "operator_review":
                print("The job is waiting for operator review. Re-run once approved.", file=sys.stderr)
        return code


if __name__ == "__main__":
    sys.exit(main())
