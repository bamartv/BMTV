name: Update M3U Playlist

on:
  schedule:
    - cron: '0 */6 * * *'
  workflow_dispatch:

permissions:
  contents: write          # ← FIX 1: allow the bot to push

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.x'

      - name: Install dependencies     # ← FIX 2: install python-dotenv
        run: pip install python-dotenv

      - name: Run script
        env:
          MY_SECRET: ${{ secrets.MY_SECRET }}
          PLAYLIST_URL: ${{ secrets.PLAYLIST_URL }}
        run: python .github/scripts/sayan.py

      - name: Commit and push changes
        run: |
          git config --local user.email "github-actions[bot]@users.noreply.github.com"
          git config --local user.name "github-actions[bot]"
          git add Sayan.m3u
          git diff --quiet && git diff --staged --quiet || (git commit -m "Update Sayan.m3u [skip ci]" && git push)
