#!/usr/bin/env bash
# Publish the reference-library files to an Azure Files share that the app
# mounts as its media root, and bind them to the Azure database rows.
#
# Steps (see contrib/cosmos/reference_library/README.md, "Azure Files"):
#   1. check the share is reachable;
#   2. attach_reference_library --with-files through $CONTAINER (rows + local
#      media copy), gated by --backup-ref;
#   3. copy exactly those files into the share at the paths the rows record,
#      after checking each sha256 against the attachment metadata;
#   4. list the share to confirm every file and size.
#
# Usage:
#   STORAGE_ACCOUNT=<name> BACKUP_REF=<backup file> \
#     contrib/cosmos/reference_library/publish_media_to_azure_files.sh [plan|apply]
#
# Optional: RESOURCE_GROUP (EpconChat), SHARE (aimms-media),
#   CONTAINER (aimms-azure-server), MEDIA_HOST_DIR (host path of that
#   container's media root, default data/media), SKIP_UPLOAD=1 (stage and
#   verify only).
# The storage key is read into the environment of the az calls and never
# printed. Needs "Storage Account Contributor" (or listKeys) on the account.
set -euo pipefail

MODE="${1:-plan}"
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
RESOURCE_GROUP="${RESOURCE_GROUP:-EpconChat}"
SHARE="${SHARE:-aimms-media}"
CONTAINER="${CONTAINER:-aimms-azure-server}"
MEDIA_HOST_DIR="${MEDIA_HOST_DIR:-$REPO/data/media}"
SKIP_UPLOAD="${SKIP_UPLOAD:-0}"
WORKBOOK="${WORKBOOK:-/home/inventree/data/devscratch/CEDAR_CREEK_AIMMS_UI_POPULATION_MASTER_2026-10-08.xlsx}"
PACK="${PACK:-/home/inventree/data/devscratch/refpack}"
REVIEW="/home/inventree/contrib/cosmos/reference_library/attachment_review.json"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$REPO/data/devscratch/reference-media-$STAMP"
mkdir -p "$OUT"

die() { echo "error: $*" >&2; exit 1; }
[[ "$MODE" == plan || "$MODE" == apply ]] || die "mode must be plan or apply"

manage() {
  docker exec -w /home/inventree/src/backend/InvenTree "$CONTAINER" python manage.py "$@"
}

# 1. share reachable --------------------------------------------------------
if [[ "$SKIP_UPLOAD" != 1 ]]; then
  [[ -n "${STORAGE_ACCOUNT:-}" ]] || die "STORAGE_ACCOUNT is required"
  AZURE_STORAGE_KEY="$(az storage account keys list -g "$RESOURCE_GROUP" \
    -n "$STORAGE_ACCOUNT" --query '[0].value' -o tsv)" \
    || die "cannot read the account key (need Storage Account Contributor)"
  export AZURE_STORAGE_ACCOUNT="$STORAGE_ACCOUNT" AZURE_STORAGE_KEY
  [[ "$(az storage share exists --name "$SHARE" --query exists -o tsv)" == true ]] \
    || die "share $SHARE not found on $STORAGE_ACCOUNT"
  echo "share $STORAGE_ACCOUNT/$SHARE reachable"
fi

ARGS=("$WORKBOOK" --pack "$PACK" --review "$REVIEW" --with-files)
if [[ "$MODE" == plan ]]; then
  manage attach_reference_library "${ARGS[@]}" 2>/dev/null | grep -E 'totals|exceeds|Workbook'
  echo "plan only; re-run with 'apply' and BACKUP_REF set"
  exit 0
fi

# 2. rows + local media -------------------------------------------------------
[[ -n "${BACKUP_REF:-}" ]] || die "BACKUP_REF (the dbbackup file taken first) is required"
[[ -f "$REPO/data/backup/$BACKUP_REF" ]] || die "backup $BACKUP_REF not in data/backup"
manage attach_reference_library "${ARGS[@]}" --apply --allow-remote --shared-media \
  --backup-ref "$BACKUP_REF" --json 2>/dev/null >"$OUT/apply.json"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print('apply', d['counts'], len(d['created']), 'written')" "$OUT/apply.json"

# 3. stage the exact files, verify, upload -----------------------------------
manage shell -c "
import json
from common.models import Attachment
rows = []
for a in Attachment.objects.filter(metadata__has_key='aimms_reference_pack').exclude(attachment=''):
    rows.append({'pk': a.pk, 'name': a.attachment.name, 'size': a.file_size,
                 'sha256': a.metadata['aimms_reference_pack']['sha256']})
print('ROWS=' + json.dumps(rows))
" 2>/dev/null | sed -n 's/^ROWS=//p' >"$OUT/rows.json"

STAGE="$OUT/stage"
python3 - "$OUT/rows.json" "$MEDIA_HOST_DIR" "$STAGE" <<'PY'
import hashlib, json, os, shutil, sys
rows, media, stage = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
if not rows:
    sys.exit('no attachment rows with files')
for r in rows:
    src = os.path.join(media, r['name'])
    digest = hashlib.sha256(open(src, 'rb').read()).hexdigest()
    if digest != r['sha256']:
        sys.exit(f"sha256 mismatch for attachment {r['pk']} ({r['name']})")
    dst = os.path.join(stage, r['name'])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
print(f'staged {len(rows)} files, all sha256 verified')
PY

if [[ "$SKIP_UPLOAD" == 1 ]]; then
  echo "SKIP_UPLOAD=1: staged in $STAGE, nothing uploaded"
  exit 0
fi
az storage file upload-batch --destination "$SHARE" --source "$STAGE" \
  --no-progress --only-show-errors -o none

# 4. confirm ---------------------------------------------------------------------
python3 - "$OUT/rows.json" "$SHARE" <<'PY'
import json, subprocess, sys
rows, share = json.load(open(sys.argv[1])), sys.argv[2]
bad = 0
for r in rows:
    size = subprocess.run(
        ['az', 'storage', 'file', 'show', '--share-name', share, '--path', r['name'],
         '--query', 'properties.contentLength', '-o', 'tsv'],
        capture_output=True, text=True).stdout.strip()
    if size != str(r['size']):
        bad += 1
        print(f"MISSING/SIZE {r['name']}: share={size or 'absent'} db={r['size']}")
print(f'{len(rows) - bad}/{len(rows)} files present on the share with matching size')
sys.exit(1 if bad else 0)
PY
echo "done; records in $OUT"
