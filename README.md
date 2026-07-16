# audit_dashboard

회계법인 감사본부 지원용 포트폴리오 프로젝트. DART(전자공시시스템) Open API로 상장기업 재무제표를 불러와
**감사 실무 관점의 1차 스크리닝 도구** 두 가지를 제공합니다.

## 기능

### 1. 재무제표 이상징후 탐지 (`index.html`)
- 매출채권회전율·재고자산회전율의 전기 대비 급변(±30% 이상) 탐지
- 발생액비율(Accruals Ratio, (당기순이익−영업활동현금흐름)/자산총계) 과다 탐지
- Altman Z''-Score(비상장·이머징마켓용 부실위험 예측모형, Altman·Hartzell·Peck 1995) 산정
  및 안전/회색지대/위험 3단계 판정
- DART 정정공시(사업·반기·분기보고서 [기재정정]) 이력 조회 — 재무제표 신뢰성 참고 지표

### 2. 중요성금액 & 표본추출 계산기 (`materiality.html`)
- ISA 320 기반 전체 중요성금액(OM)·수행중요성(PM)·명백한 사소 금액 계산
- 금액단위표본(MUS)·속성표본 표본수 계산 (신뢰계수 RF = −ln(1−신뢰수준), 포아송 근사)
- 회사 검색으로 재무 데이터 자동 불러오기 지원

### 3. 관련 뉴스 스캔 (`index.html`)
- 회사 선택 시 네이버 뉴스 검색 API로 최신 관련 뉴스(제목·요약·링크)를 함께 표시
- 감사 착수 전 클라이언트 관련 이슈(소송, 실적, 오너 리스크 등)를 빠르게 스캔하는 용도

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env   # DART_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 입력
python dev_server.py
# http://localhost:8000
```

네이버 API 키는 [네이버 개발자센터](https://developers.naver.com/apps/#/register)에서
"검색" API를 선택해 애플리케이션을 등록하면 무료로 즉시 발급됩니다.

## 배포

Vercel의 Python 서버리스 함수(`api/audit.py`, `api/news.py`)로 배포합니다. 환경변수
`DART_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`을 Vercel 프로젝트 설정에 등록하세요.

## 면책 조항

본 프로젝트는 학습·포트폴리오 목적의 1차 스크리닝 참고자료이며, 실제 감사 절차나
부정 판단, 투자 판단의 근거로 사용할 수 없습니다.
