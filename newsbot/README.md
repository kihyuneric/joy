# newsbot — 부동산·경매 주요뉴스 → 카카오톡 오픈채팅 메시지

네이버 뉴스 검색 API로 오늘의 부동산/경매 기사를 모아 10개를 골라 아래 형식의 텍스트를 만든다.

```
26년 10월 16일 금요일 부동산 주요뉴스

<제목>
https://n.news.naver.com/mnews/article/...
(×10)
```

## 사용
```bash
pip install -r requirements.txt
export NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=...
python -m newsbot --out news.txt          # 화면 출력 + 파일 저장
python -m newsbot --date 2026-10-16       # 헤더 날짜 지정(전날 밤 미리 생성용)
python -m newsbot --count 10 --auction-slots 4
```
출력된 텍스트를 오픈채팅방에 붙여넣으면 된다.

## 선정 방식
- 검색어: 부동산 경매 / 법원경매 / 경매 낙찰가율 / 부동산 / 아파트 집값 / 부동산 대책
- `n.news.naver.com` 링크 기사만 사용, 오늘(KST) 기사 우선(부족하면 36시간까지 확장)
- 제목 키워드 점수화(경매·낙찰·공매·NPL 가중), 유사 제목 중복 제거, `[기고]`·`[포토]` 등은 감점
- 경매 관련 기사를 최소 4건(`--auction-slots`) 우선 배정, 나머지는 점수순

## 참고
카카오톡 오픈채팅방에는 공식 메시지 전송 API가 없어 자동 게시는 되지 않는다.
매일 아침 실행(cron/GitHub Actions)해 결과를 확인 후 붙여넣는 방식을 권장한다.
