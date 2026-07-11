#!/usr/bin/env bash
set -euo pipefail

BASE_URL="http://localhost:8000"
TEST_VIDEO="/tmp/test_video.mp4"
PASSWORD="demo-password"
POLL_INTERVAL=3
POLL_TIMEOUT=60
declare -a RESULTS=()

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

_ok()   { echo -e "${GREEN}PASS${NC} $*"; RESULTS+=("PASS: $1");  }
_fail() { echo -e "${RED}FAIL${NC} $*"; RESULTS+=("FAIL: $1"); return 1; }

_json_get() {
    local key="$1"
    if command -v jq &>/dev/null; then
        jq -r ".${key}" 2>/dev/null
    else
        python3 -c "import sys,json; print(json.load(sys.stdin).get('${key#.}', ''))"
    fi
}

# ─── Step 1: Header ──────────────────────────────────────────────────────────

echo "=== RoboData E2E Test ==="

# ─── Step 2: Server reachable ─────────────────────────────────────────────────

echo -n "[Step 2] Server reachable… "
if curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/" | grep -q '^2' 2>/dev/null; then
    _ok "Server is reachable"
else
    _fail "Server not reachable at ${BASE_URL}"
    echo "Summary:"
    for r in "${RESULTS[@]}"; do echo "  $r"; done
    exit 1
fi

# ─── Step 3: Login ────────────────────────────────────────────────────────────

echo -n "[Step 3] Login… "
LOGIN_RESP=$(curl -s -X POST "${BASE_URL}/api/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"password\": \"${PASSWORD}\"}")
TOKEN=$(echo "$LOGIN_RESP" | _json_get "access_token")
if [[ -n "$TOKEN" && "$TOKEN" != "null" ]]; then
    _ok "Login succeeded, token received"
else
    _fail "Login failed — response: $LOGIN_RESP"
    echo "Summary:"
    for r in "${RESULTS[@]}"; do echo "  $r"; done
    exit 1
fi

AUTH_HEADER="Authorization: Bearer ${TOKEN}"

# ─── Step 4: Verify token ─────────────────────────────────────────────────────

echo -n "[Step 4] Verify token… "
VERIFY_RESP=$(curl -s -H "${AUTH_HEADER}" "${BASE_URL}/api/auth/verify")
VALID=$(echo "$VERIFY_RESP" | _json_get "valid")
if [[ "$VALID" == "True" ]]; then
    _ok "Token is valid"
else
    _fail "Token verification failed — response: $VERIFY_RESP"
fi

# ─── Step 5: Frontend served ──────────────────────────────────────────────────

echo -n "[Step 5] Frontend serves HTML… "
HTML=$(curl -s "${BASE_URL}/")
if echo "$HTML" | grep -q "RoboData"; then
    _ok "Frontend HTML contains 'RoboData'"
else
    _fail "Frontend HTML does not contain 'RoboData'"
fi

# ─── Step 6: Create test video ────────────────────────────────────────────────

echo -n "[Step 6] Test video… "
if [[ -f "$TEST_VIDEO" ]]; then
    echo "already exists at ${TEST_VIDEO}, skipping creation"
    RESULTS+=("PASS: step6 (test video already existed)")
else
    if command -v ffmpeg &>/dev/null; then
        if ffmpeg -y -f lavfi -i "testsrc=duration=3:size=320x240:rate=30" \
            -f lavfi -i "sine=frequency=440:duration=3" \
            -c:v libx264 -pix_fmt yuv420p -shortest "${TEST_VIDEO}" &>/dev/null; then
            _ok "Test video created at ${TEST_VIDEO}"
        else
            _fail "ffmpeg failed to create test video"
        fi
    else
        _fail "ffmpeg not found — cannot create test video"
    fi
fi

# ─── Step 7: Upload video ─────────────────────────────────────────────────────

echo -n "[Step 7] Upload video… "
UPLOAD_RESP=$(curl -s -X POST "${BASE_URL}/api/jobs/upload" \
    -H "${AUTH_HEADER}" \
    -F "video=@${TEST_VIDEO}")
JOB_ID=$(echo "$UPLOAD_RESP" | _json_get "job_id")
if [[ -n "$JOB_ID" && "$JOB_ID" != "null" ]]; then
    _ok "Upload succeeded, job_id=${JOB_ID}"
else
    _fail "Upload failed — response: $UPLOAD_RESP"
    echo "Summary:"
    for r in "${RESULTS[@]}"; do echo "  $r"; done
    exit 1
fi

# ─── Step 8: Poll job status ──────────────────────────────────────────────────

echo -n "[Step 8] Poll job status… "
ELAPSED=0
FINAL_STATUS=""
while [[ $ELAPSED -lt $POLL_TIMEOUT ]]; do
    JOB_RESP=$(curl -s -H "${AUTH_HEADER}" "${BASE_URL}/api/jobs/${JOB_ID}")
    STATUS=$(echo "$JOB_RESP" | _json_get "status")
    PROGRESS=$(echo "$JOB_RESP" | _json_get "progress")
    STAGE=$(echo "$JOB_RESP" | _json_get "current_stage")
    echo ""
    echo "  [t=${ELAPSED}s] status=${STATUS} progress=${PROGRESS} stage=${STAGE}"
    if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
        FINAL_STATUS="$STATUS"
        break
    fi
    sleep "$POLL_INTERVAL"
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

if [[ "$FINAL_STATUS" == "completed" ]]; then
    _ok "Job completed successfully"
elif [[ "$FINAL_STATUS" == "failed" ]]; then
    MESSAGE=$(echo "$JOB_RESP" | _json_get "message")
    _fail "Job failed — message: ${MESSAGE}"
else
    _fail "Job timed out after ${POLL_TIMEOUT}s — last status: ${FINAL_STATUS:-unknown}"
fi

# ─── Step 9: Download dataset ─────────────────────────────────────────────────

if [[ "$FINAL_STATUS" == "completed" ]]; then
    echo -n "[Step 9] Download dataset… "
    DOWNLOAD_HTTP=$(curl -s -o /dev/null -w '%{http_code}' \
        -H "${AUTH_HEADER}" "${BASE_URL}/api/jobs/${JOB_ID}/download")
    if [[ "$DOWNLOAD_HTTP" == "200" ]]; then
        _ok "Download returned HTTP 200"
    else
        _fail "Download returned HTTP ${DOWNLOAD_HTTP}"
    fi
else
    RESULTS+=("SKIP: step9 (job not completed)")
fi

# ─── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "=== E2E Test Summary ==="
PASS_COUNT=0
FAIL_COUNT=0
for r in "${RESULTS[@]}"; do
    if [[ "$r" == PASS:* ]]; then
        echo -e "  ${GREEN}${r}${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    elif [[ "$r" == FAIL:* ]]; then
        echo -e "  ${RED}${r}${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    else
        echo "  $r"
    fi
done
echo ""
echo -e "Total: ${GREEN}${PASS_COUNT} passed${NC}, ${RED}${FAIL_COUNT} failed${NC}"

if [[ $FAIL_COUNT -gt 0 ]]; then
    exit 1
fi
