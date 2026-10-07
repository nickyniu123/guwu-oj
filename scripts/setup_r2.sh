#!/usr/bin/env bash
# Provision the Cloudflare R2 bucket used by Guwu OJ (problem images + avatars)
# and mint the S3 credentials Django needs.
#
#   CF_API_KEY=<global-api-key> bash scripts/setup_r2.sh              # provision, print .env block
#   CF_API_KEY=<global-api-key> bash scripts/setup_r2.sh --write-env  # ...and append it to .env
#   bash scripts/setup_r2.sh --dry-run                                # show the plan, call nothing
#
# What it does (all idempotent — safe to re-run):
#   1. create the bucket                          (POST /accounts/{id}/r2/buckets)
#   2. attach the custom domain media.guwu.camluni.cn, DNS + TLS are created
#      automatically because the zone lives in the same account
#                                                 (POST .../domains/custom)
#   3. mint an R2 API token scoped to this bucket (POST /user/tokens) and derive
#      the S3 Access Key ID (= token id) and Secret Access Key (= SHA-256 of the
#      token value) from it.
#
# R2 itself must be activated once from the dashboard (Billing > R2); the API
# returns error 10042 until then and this script will tell you so.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${OJ_SETUP_ENV:-$PROJECT_DIR/.env}"

# ----- configuration (override via environment) -----------------------------
CF_API_EMAIL="${CF_API_EMAIL:-oscar.liu@student.isb.cn}"
CF_API_KEY="${CF_API_KEY:-}"
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-388b80c5f4b9fa360483a0eef2883776}"
CF_ZONE_ID="${CF_ZONE_ID:-83f08a9bc34aa1428d421cdb58c35be5}"
R2_BUCKET="${R2_BUCKET:-guwu-oj-media}"
R2_DOMAIN="${R2_DOMAIN:-media.guwu.camluni.cn}"
CF_API="https://api.cloudflare.com/client/v4"

WRITE_ENV=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --write-env) WRITE_ENV=1 ;;
    --dry-run|-n) DRY_RUN=1 ;;
    --help|-h) grep '^#' "${BASH_SOURCE[0]}" | head -22 | cut -c3-; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"; }

# cf <method> <path> [json-body] -> raw JSON response on stdout
cf() {
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -m 30 -X "$method" \
      -H "X-Auth-Email: $CF_API_EMAIL" -H "X-Auth-Key: $CF_API_KEY" \
      -H 'Content-Type: application/json' --data "$body" "$CF_API$path"
  else
    curl -sS -m 30 -X "$method" \
      -H "X-Auth-Email: $CF_API_EMAIL" -H "X-Auth-Key: $CF_API_KEY" "$CF_API$path"
  fi
}

# jget <json> <python-expression over `d`>  — small JSON reader, no jq needed
jget() {
  python3 -c 'import json,sys
d=json.loads(sys.stdin.read() or "{}")
print(eval(sys.argv[1], {"__builtins__": {}}, {"d": d}) or "")' "$2" <<<"$1"
}

# fail unless the response says success:true
require_ok() {
  local resp="$1" what="$2"
  [ "$(jget "$resp" 'd.get("success")')" = "True" ] || {
    printf '%s\n' "$resp" >&2
    die "$what failed"
  }
}

# ---------------------------------------------------------------------------
if [ "$DRY_RUN" = 1 ]; then
  info "DRY RUN — no API calls will be made"
  printf '  account   : %s\n  zone      : %s\n  bucket    : %s\n  domain    : %s\n' \
    "$CF_ACCOUNT_ID" "$CF_ZONE_ID" "$R2_BUCKET" "$R2_DOMAIN"
  printf '\nWould: create bucket -> attach custom domain -> mint R2 token -> print R2_* env block.\n'
  exit 0
fi

need curl
need python3
need sha256sum
[ -n "$CF_API_KEY" ] || die "CF_API_KEY is required (set it in the environment)."

info "Verifying Cloudflare credentials"
acct="$(cf GET /accounts)"
require_ok "$acct" "GET /accounts"
ok "authenticated as $CF_API_EMAIL"

# --- 1. R2 activation check + bucket creation ------------------------------
info "Checking R2 is activated and bucket '$R2_BUCKET' exists"
buckets="$(cf GET "/accounts/$CF_ACCOUNT_ID/r2/buckets")"
if [ "$(jget "$buckets" 'd.get("success")')" != "True" ]; then
  if printf '%s' "$buckets" | grep -q '10042'; then
    cat >&2 <<'EOF'
error: R2 is not activated on this account yet.

R2 is a paid product that must be enabled once from the dashboard — the API
cannot turn it on. Open:

    https://dash.cloudflare.com/388b80c5f4b9fa360483a0eef2883776/r2

click "Purchase R2 Plan" (the free tier includes 10 GB storage / month), then
re-run this script. Everything else — bucket, custom domain, API token — is
fully automated from here.
EOF
    exit 1
  fi
  printf '%s\n' "$buckets" >&2
  die "listing R2 buckets failed"
fi

if printf '%s' "$buckets" | grep -q "\"$R2_BUCKET\""; then
  ok "bucket already exists"
else
  resp="$(cf POST "/accounts/$CF_ACCOUNT_ID/r2/buckets" "{\"name\":\"$R2_BUCKET\"}")"
  require_ok "$resp" "create bucket $R2_BUCKET"
  ok "bucket created"
fi

# --- 2. custom domain ------------------------------------------------------
info "Attaching custom domain $R2_DOMAIN"
domains="$(cf GET "/accounts/$CF_ACCOUNT_ID/r2/buckets/$R2_BUCKET/domains/custom")"
if printf '%s' "$domains" | grep -q "\"$R2_DOMAIN\""; then
  ok "custom domain already attached"
else
  resp="$(cf POST "/accounts/$CF_ACCOUNT_ID/r2/buckets/$R2_BUCKET/domains/custom" \
    "{\"domain\":\"$R2_DOMAIN\",\"enabled\":true,\"zoneId\":\"$CF_ZONE_ID\"}")"
  if [ "$(jget "$resp" 'd.get("success")')" = "True" ]; then
    ok "custom domain attached (DNS + certificate provisioning in the background)"
  else
    printf '%s\n' "$resp" >&2
    warn "could not attach the custom domain automatically."
    warn "Add it by hand: R2 > $R2_BUCKET > Settings > Custom Domains > Add '$R2_DOMAIN'."
  fi
fi

# --- 3. bucket-scoped R2 API token ----------------------------------------
info "Minting a bucket-scoped R2 API token"

token_name="guwu-oj-media-$(date +%Y%m%d)"
pgs="$(cf GET "/user/tokens/permission_groups?per_page=200")"
require_ok "$pgs" "list permission groups"
read -r pg_read pg_write < <(python3 - "$pgs" <<'PY'
import json, sys
groups = {g["name"]: g["id"] for g in json.loads(sys.argv[1])["result"]}
print(groups.get("Workers R2 Storage Bucket Item Read", "-"),
      groups.get("Workers R2 Storage Bucket Item Write", "-"))
PY
)
[ "$pg_read" != "-" ] && [ "$pg_write" != "-" ] \
  || die "could not find the R2 bucket-item permission groups"

# Non-jurisdictional buckets use the "default" jurisdiction segment.
resource="com.cloudflare.edge.r2.bucket.${CF_ACCOUNT_ID}_default_${R2_BUCKET}"
payload="$(python3 - "$token_name" "$resource" "$pg_read" "$pg_write" <<'PY'
import json, sys
name, resource, read_id, write_id = sys.argv[1:5]
print(json.dumps({
    "name": name,
    "policies": [{
        "effect": "allow",
        "resources": {resource: "*"},
        "permission_groups": [{"id": read_id}, {"id": write_id}],
    }],
}))
PY
)"

token="$(cf POST /user/tokens "$payload")"
require_ok "$token" "create R2 API token"

access_key_id="$(jget "$token" 'd["result"]["id"]')"
token_value="$(jget "$token" 'd["result"]["value"]')"
[ -n "$access_key_id" ] && [ -n "$token_value" ] || die "token response missing id/value"
# R2 S3 credentials: Access Key ID = token id, Secret Access Key = SHA-256(token).
secret_access_key="$(printf '%s' "$token_value" | sha256sum | cut -d' ' -f1)"
ok "token '$token_name' created"

# --- 4. emit / persist the Django configuration ----------------------------
env_block="$(cat <<EOF
# --- Cloudflare R2 (problem images + user avatars) ---
R2_ACCOUNT_ID=$CF_ACCOUNT_ID
R2_BUCKET=$R2_BUCKET
R2_ACCESS_KEY_ID=$access_key_id
R2_SECRET_ACCESS_KEY=$secret_access_key
R2_CUSTOM_DOMAIN=$R2_DOMAIN
EOF
)"

printf '\n%s\n' "$env_block"

if [ "$WRITE_ENV" = 1 ]; then
  if grep -q '^R2_ACCESS_KEY_ID=' "$ENV_FILE" 2>/dev/null; then
    warn "$ENV_FILE already contains R2_ACCESS_KEY_ID — not overwriting."
    warn "Replace the R2_* lines manually with the block above."
  else
    printf '\n%s\n' "$env_block" >>"$ENV_FILE"
    ok "appended the R2_* block to $ENV_FILE"
  fi
else
  info "add the block above to $ENV_FILE (or re-run with --write-env)"
fi

cat <<EOF

Next steps:
  1. ensure the R2_* block above is in $ENV_FILE
  2. ./venv/bin/python manage.py migrate            # applies users 0015
  3. ./venv/bin/python manage.py migrate_media_to_r2 --dry-run
  4. ./venv/bin/python manage.py migrate_media_to_r2
  5. restart the web/WS services
EOF
