name: Weekly Competitor Report

on:
  schedule:
    - cron: '0 0 * * 1'
  workflow_dispatch: {}

permissions:
  contents: write

jobs:
  send-report:
    runs-on: ubuntu-latest
    steps:
      - name: 저장소 체크아웃
        uses: actions/checkout@v4

      - name: 파이썬 설치
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: 의존성 설치
        run: pip install -r requirements.txt

      - name: 리포트 생성 및 Slack 전송
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
        run: python main.py

      - name: 이번 주 데이터 저장 (다음 주 비교용)
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          if [ -f data/last_snapshot.json ]; then
            git add data/last_snapshot.json
            git commit -m "chore: 주간 스냅샷 업데이트 $(date +'%Y-%m-%d')" || echo "변경사항 없음"
            git push
          fi
