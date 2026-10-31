#!/usr/bin/env bash
# Publish a single, reconciled copy: GitHub site + live Timeweb source + user-supplied screenshot.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_IMAGE="$ROOT/ai-education/docs/site-preview.png"
LIVE_ARCHIVE="${LIVE_ARCHIVE:-$HOME/Documents/ChatGPT/antonova-ai-audit/timeweb-live-source.tar.gz}"
REMOTE="${GIT_REMOTE:-git@github.com:Ar4i23/ai-education.git}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/antonova-sync.XXXXXXXX")"
trap 'rm -rf "$TMP"' EXIT

command -v git >/dev/null
command -v python3 >/dev/null
[ -s "$SOURCE_IMAGE" ] || { echo "ERROR: screenshot missing" >&2; exit 1; }

echo '=== 1/5: obtain current production source (code only) ==='
if [ ! -s "$LIVE_ARCHIVE" ]; then
  echo 'No saved archive found. Fetching live source over SSH (password may be requested)...'
  LIVE_ARCHIVE="$TMP/timeweb-live-source.tar.gz"
  ssh root@109.196.103.68 'set -e; cd /opt/antonova-ai/telegram_bot-next; find . \( -type d \( -name .venv -o -name .git -o -name __pycache__ -o -name backups \) -prune \) -o \( -type f \( -name "*.py" -o -name "requirements.txt" \) -print0 \) | tar --null -czf - -T -' > "$LIVE_ARCHIVE"
fi
[ -s "$LIVE_ARCHIVE" ] || { echo 'ERROR: empty server source archive'; exit 1; }

# Inspect and extract only code files; never import .env, databases, keys, logs, or archives.
python3 - "$LIVE_ARCHIVE" "$TMP/live" <<'PYCODE'
import ast, re, sys, tarfile
from pathlib import Path
src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True)
files = 0
with tarfile.open(src, 'r:gz') as tar:
    for item in tar.getmembers():
        if not item.isfile():
            continue
        name = Path(item.name.lstrip('./'))
        if name.is_absolute() or '..' in name.parts or any(p.startswith('.') for p in name.parts) or len(name.parts)>6:
            continue
        if name.suffix != '.py' and name.name != 'requirements.txt':
            continue
        if item.size > 1_000_000:
            raise SystemExit(f'Unusually large source file: {name}')
        raw = tar.extractfile(item).read()
        if name.suffix=='.py':
            code = raw.decode('utf-8')
            ast.parse(code, filename=str(name))
            if re.search(r'\b\d{7,12}:[A-Za-z0-9_-]{30,}\b', code):
                raise SystemExit(f'Potential hardcoded Telegram token: {name}')
        dest = out/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(raw)
        files+=1
required=['production_runner.py','config.py','database.py','integrations/robokassa.py','integrations/payment_callback.py','integrations/join_requests.py','integrations/admin_notifications.py']
missing=[x for x in required if not (out/x).is_file()]
if missing:raise SystemExit('STOP: live source lacks required files: '+', '.join(missing))
if files<25:raise SystemExit(f'STOP: suspiciously few files: {files}')
print(f'Live source validated: {files} source files')
PYCODE

echo '=== 2/5: fresh GitHub clone ==='
git clone --quiet --branch main "$REMOTE" "$TMP/repo"
cd "$TMP/repo"
[ -z "$(git status --porcelain)" ] || { echo 'STOP: clone is dirty'; exit 1; }

echo '=== 3/5: replace legacy bot, update real preview ==='
git rm -qr --ignore-unmatch telegram_bot
mkdir -p telegram_bot docs
cp -R "$TMP/live/." telegram_bot/
cp "$ROOT/ai-education/telegram_bot/.env.example" telegram_bot/.env.example
cp "$ROOT/ai-education/telegram_bot/README_PRODUCTION.md" telegram_bot/README_PRODUCTION.md
cp "$ROOT/ai-education/README.md" README.md
cp "$ROOT/ai-education/.gitignore" .gitignore
mkdir -p tools
cp "$ROOT/publish_from_timeweb.sh" tools/publish_from_timeweb.sh
cp "$SOURCE_IMAGE" docs/site-preview.png

python3 - <<'PYCODE'
from pathlib import Path
p=Path('index.html')
s=p.read_text()
s=s.replace('bottom-row__cta-price">2499', 'bottom-row__cta-price">3499')
assert 'bottom-row__cta-price">3499' in s
p.write_text(s)

# Keep the server source as-is; reject obsolete payment dependencies, never modify the live server.
bot=Path('telegram_bot')
for p in bot.rglob('*'):
    if p.is_file() and p.name.lower().startswith('yookassa'):
        raise SystemExit('STOP: obsolete payment code in server snapshot')
for p in bot.rglob('*'):
    if p.is_file() and (p.suffix in ('.db','.sqlite','.sqlite3') or p.name=='.env'):
        raise SystemExit(f'STOP: secret/data file {p}')
print('Website price: 3499 RUB; preview: user screenshot; bot: live Timeweb source')
PYCODE

git add -A -- index.html README.md .gitignore docs/site-preview.png telegram_bot tools/publish_from_timeweb.sh
if git diff --cached --quiet; then
  echo 'No changes. Repository already synchronized.'
  exit 0
fi

git diff --cached --check
python3 - <<'PYCODE'
import ast
from pathlib import Path
for f in Path('telegram_bot').rglob('*.py'):
    ast.parse(f.read_text(),filename=str(f))
assert not (Path('telegram_bot')/'.env').exists()
assert not (Path('telegram_bot')/'yookassa_api.py').exists()
assert b'3499 <strong' in Path('index.html').read_bytes()
print('STATIC CHECK: PASS')
PYCODE

# Show what changes without printing tokens or customer data.
echo '=== 4/5: files to publish ==='
git diff --cached --stat
if git diff --cached --name-only | grep -E '(^|/)(\.env$|[^/]+\.(db|sqlite|sqlite3)$)' ; then
  echo 'STOP: sensitive file staged' >&2; exit 1
fi

echo '=== 5/5: commit and publish to GitHub ==='
git -c user.name="Antonova AI" -c user.email="noreply@users.noreply.github.com" commit -qm 'Sync live Robokassa bot and update landing preview'
git push origin HEAD:main

echo 'GITHUB UPDATE: SUCCESS'
echo 'REPO: https://github.com/Ar4i23/ai-education'
echo 'PRODUCTION TIMEWEB: NOT MODIFIED'
