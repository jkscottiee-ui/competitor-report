# 경쟁사 주간 동향 자동 리포트 (매주 월요일 → Slack)

`폴인퍼니 / 영가구 / 에이비퍼니처 / 위드퍼니처`의 최신 뉴스·신제품·프로모션 동향을
매주 월요일 오전 9시(KST)에 자동으로 조사하여 Slack 채널로 전송합니다.

---

## 전체 흐름
1. GitHub Actions가 매주 월요일 자동 실행
2. `main.py`가 Anthropic Claude API(웹 검색 기능 포함)로 각 회사 동향을 조사·요약
3. 결과를 Slack Incoming Webhook으로 전송

---

## 1단계. Slack Webhook URL 발급받기 (관리자 권한 필요)

1. https://api.slack.com/apps 접속 → **Create New App** → **From scratch**
2. App 이름 입력 (예: `경쟁사 리포트봇`) → 워크스페이스 선택 → **Create App**
3. 왼쪽 메뉴에서 **Incoming Webhooks** 클릭 → 토글 **On**
4. 하단 **Add New Webhook to Workspace** 클릭
5. 메시지를 받을 채널 선택 (예: `#시장조사`) → **허용**
6. 생성된 `https://hooks.slack.com/services/...` 형태의 URL을 복사 → 이 값이 `SLACK_WEBHOOK_URL`

---

## 2단계. Anthropic API 키 발급받기

1. https://console.anthropic.com 접속 → 로그인/가입
2. **API Keys** 메뉴 → **Create Key**
3. 생성된 키 복사 (한 번만 표시되니 안전한 곳에 저장) → 이 값이 `ANTHROPIC_API_KEY`
4. 참고: API는 종량제 과금이며, 이 스크립트는 주 1회, 회사 4곳 조사 기준으로 비용이 크지 않습니다.

---

## 3단계. GitHub에 저장소 만들기

1. https://github.com 가입 (계정이 없다면 무료로 생성)
2. 우측 상단 **+** → **New repository**
3. 저장소 이름 입력 (예: `competitor-report`) → **Private** 권장 → **Create repository**
4. 이 폴더 안의 파일들(`main.py`, `requirements.txt`, `.github/workflows/weekly-report.yml`, `README.md`)을
   그대로 저장소에 업로드
   - 웹에서 **Add file → Upload files**로 드래그 앤 드롭하면 됩니다.
   - `.github/workflows/weekly-report.yml` 경로(폴더 구조)는 반드시 그대로 유지해야 합니다.

---

## 4단계. GitHub에 비밀 값(Secrets) 등록하기

1. 저장소 페이지 → **Settings** 탭
2. 왼쪽 메뉴 **Secrets and variables** → **Actions**
3. **New repository secret** 클릭 후 아래 2개를 각각 등록:
   - Name: `ANTHROPIC_API_KEY` / Value: (2단계에서 발급받은 키)
   - Name: `SLACK_WEBHOOK_URL` / Value: (1단계에서 발급받은 Webhook URL)

---

## 5단계. 정상 작동 테스트

1. 저장소 → **Actions** 탭 → **Weekly Competitor Report** 워크플로우 선택
2. 우측 **Run workflow** 버튼 클릭 → 즉시 수동 실행
3. 몇 십 초 후 Slack 채널에 메시지가 도착하는지 확인
4. 실패 시 Actions 탭의 로그를 확인 (Secrets 이름 오타, API 키 오류 등이 흔한 원인)

정상 작동이 확인되면 이후로는 **매주 월요일 오전 9시(KST)에 자동으로** 실행됩니다.

---

## 추적 대상 회사 수정하기

`main.py` 상단의 아래 부분을 수정하면 됩니다:

```python
COMPANIES = ["폴인퍼니", "영가구", "에이비퍼니처", "위드퍼니처"]
```

회사를 추가/삭제하고 저장 후 GitHub에 다시 업로드(커밋)하면 다음 실행부터 반영됩니다.

## 실행 시간 변경하기

`.github/workflows/weekly-report.yml`의 `cron: '0 0 * * 1'` 값을 수정하세요.
(GitHub Actions의 cron은 UTC 기준이며, KST는 UTC+9시간입니다.)
