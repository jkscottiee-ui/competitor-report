"""
매주 월요일, 지정된 경쟁사들의 최근 시장 동향(뉴스/신제품/프로모션 등)을
Claude API(웹 검색 기능 포함)로 조사·요약하고, Slack으로 전송하는 스크립트.

필요한 환경변수:
- ANTHROPIC_API_KEY : Anthropic API 키
- SLACK_WEBHOOK_URL : Slack Incoming Webhook URL
"""

import os
import sys
import datetime
import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# ▼▼▼ 추적하고 싶은 경쟁사 이름을 여기에 자유롭게 추가/수정하세요 ▼▼▼
COMPANIES = ["폴인퍼니", "영가구", "에이비퍼니처", "위드퍼니처"]
# ▲▲▲ ▲▲▲

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"


def check_env():
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not SLACK_WEBHOOK_URL:
        missing.append("SLACK_WEBHOOK_URL")
    if missing:
        print(f"[오류] 다음 환경변수가 설정되지 않았습니다: {', '.join(missing)}")
        sys.exit(1)


def get_company_summary(company: str) -> str:
    """Claude API의 웹 검색 기능을 사용해 최근 1주일 내 회사 동향을 조사/요약."""
    prompt = f"""
'{company}' (가구/인테리어 관련 회사)에 대한 최근 7일 이내의 뉴스, 신제품 출시,
프로모션/할인, 채용 공고, SNS·커뮤니티 반응 등 시장 동향을 웹 검색으로 조사해줘.

아래 형식을 반드시 지켜서 한국어로 간결하게 정리해줘:
- 불필요한 서론 없이 바로 항목만 작성
- 특별한 소식이 없으면 "이번 주 특이 동향 없음" 한 줄만 작성
- 각 항목은 1줄로, 최대 4개까지
"""
    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            },
            timeout=90,
        )
        response.raise_for_status()
        data = response.json()

        texts = [block["text"] for block in data.get("content", []) if block.get("type") == "text"]
        summary = "\n".join(texts).strip()
        return summary if summary else "이번 주 특이 동향 없음"

    except Exception as e:
        print(f"[경고] '{company}' 조사 중 오류 발생: {e}")
        return "⚠️ 조사 중 오류가 발생했습니다. (로그 확인 필요)"


def build_slack_message(summaries: dict) -> dict:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    lines = [f"*:mag: 경쟁사 주간 동향 리포트 ({today})*", ""]
    for company, summary in summaries.items():
        lines.append(f"*[{company}]*")
        lines.append(summary)
        lines.append("")
    return {"text": "\n".join(lines)}


def send_to_slack(message: dict):
    resp = requests.post(SLACK_WEBHOOK_URL, json=message, timeout=30)
    resp.raise_for_status()
    print("Slack 전송 완료")


def main():
    check_env()
    summaries = {}
    for company in COMPANIES:
        print(f"조사 중: {company}")
        summaries[company] = get_company_summary(company)

    message = build_slack_message(summaries)
    send_to_slack(message)


if __name__ == "__main__":
    main()
