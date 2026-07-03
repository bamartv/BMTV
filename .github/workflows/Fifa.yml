name: FIFA AUTOUPDATE
on:
  schedule:
    - cron: '*/7 * * * *'
  workflow_dispatch:
  push:
    branches:
      - main
jobs:
  update-file:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Configure Git User
        run: |
          git config --global user.name "sportlive18"
          git config --global user.email "271363488+sportlive18@users.noreply.github.com"
      - name: Sync and Update Log
        run: |
          export TZ="Asia/Kolkata"
          # Safe rebase with fallback
          git pull origin main --rebase || (git rebase --abort && git reset --hard origin/main)
          # Download latest papaos.m3u8
          curl -sS -o papaos_new.m3u8 "https://raw.githubusercontent.com/opensourceflix/OpenSourceFlix/refs/heads/main/papaos.m3u8"
          # Always overwrite to force change detection
          mv papaos_new.m3u8 papaos.m3u8
          # Always write a new timestamped log line
          echo "🚀 FIFA Synced | IST: $(date +'%Y-%m-%d %H:%M:%S') | UUID: $(cat /proc/sys/kernel/random/uuid)" >> fifa.txt
      - name: Commit and Push
        run: |
          export TZ="Asia/Kolkata"
          git add papaos.m3u8 fifa.txt
          # Show what changed (for debug visibility in Actions log)
          git diff --cached --stat
          # Always commit since fifa.txt UUID guarantees a change
          git commit -m "FIFA Database Updated ✅ [$(date +'%d-%m-%Y %H:%M')]" || echo "Nothing to commit"
          # Push with retry logic
          for i in 1 2 3; do
            git push origin main && break
            echo "Push failed, retrying ($i/3)..."
            sleep 3
            git pull origin main --rebase
          done
