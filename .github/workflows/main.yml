name: TMP Multi-Account Automation

on:
  workflow_dispatch:
  schedule:
    # Once per night — midnight Kigali time (UTC+2 → 22:00 UTC)
    # All accounts run in parallel; 30 tasks each finishes in ~30 min.
    - cron: "0 22 * * *"

jobs:
  run-bot:
    runs-on: ubuntu-22.04
    timeout-minutes: 60

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      # Fresh browser install every run — avoids stale binary issues
      - name: Install Playwright browsers
        run: playwright install --with-deps chromium

      - name: Run bot
        env:
          TMP_ACCOUNTS: ${{ secrets.TMP_ACCOUNTS }}
          # 30 = full daily quota in one run.
          # Lower to 15 here if you ever need to revert to a two-run split.
          TMP_RUN_LIMIT: "30"
        run: python -u bot.py

      # ── JOB SUMMARY — visible in the GitHub Actions UI ──────────────────────
      - name: Write job summary
        if: always()
        run: |
          echo "## TMP Bot — $(date -u '+%Y-%m-%d %H:%M UTC')" >> $GITHUB_STEP_SUMMARY
          if [ "${{ job.status }}" = "success" ]; then
            echo "✅ All accounts finished successfully." >> $GITHUB_STEP_SUMMARY
          else
            echo "❌ Run failed or timed out — check logs and debug artifacts below." >> $GITHUB_STEP_SUMMARY
          fi

      # Upload screenshots only when something goes wrong
      - name: Upload debug artifacts
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: bot-debug-${{ github.run_id }}
          path: |
            *.png
            *.zip
            *.html
          if-no-files-found: ignore

      # ── STORAGE CLEANUP — keeps GitHub well under the 500 MB limit ───────────
      - name: Delete artifacts older than 3 days
        if: always()
        run: |
          echo "🧹 Cleaning artifacts older than 3 days…"
          gh api repos/${{ github.repository }}/actions/artifacts --paginate \
            --jq '.artifacts[] | select(.expired == false) | [.id, .created_at] | @tsv' | \
          while IFS=$'\t' read -r id created; do
            age=$(( ( $(date +%s) - $(date -d "$created" +%s) ) / 86400 ))
            if [ "$age" -gt 3 ]; then
              gh api -X DELETE "repos/${{ github.repository }}/actions/artifacts/$id" \
                && echo "  Deleted artifact $id (${age}d old)"
            fi
          done
        env:
          GH_TOKEN: ${{ github.token }}
        continue-on-error: true

      - name: Delete caches older than 3 days
        if: always()
        run: |
          echo "🧹 Cleaning caches older than 3 days…"
          CUTOFF=$(date -d '3 days ago' -u +%Y-%m-%dT%H:%M:%SZ)
          gh cache list --limit 100 --json id,lastAccessedAt 2>/dev/null | \
          jq -r --arg cutoff "$CUTOFF" \
            '.[] | select(.lastAccessedAt < $cutoff) | .id' | \
          while read -r id; do
            gh cache delete "$id" && echo "  Deleted cache $id"
          done
        env:
          GH_TOKEN: ${{ github.token }}
        continue-on-error: true
