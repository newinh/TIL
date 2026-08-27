# System Design Daily

## Day 09/40: 빨리 수집하기보다 예의 바르게 다시 찾기가 어렵다

### 오늘의 질문

인터넷을 예의 바르고 중복 없이 수집하려면 어떻게 해야 할까요?

Web Crawler는 URL을 받아 HTML을 내려받고 새 링크를 찾는 반복 작업입니다. 단순 반복문은 몇 페이지에서 끝납니다. 한 달에 10억 페이지를 수집하려면 우선순위, Host별 속도 제한, 중복 제거, 재수집 시점, 실패 격리를 함께 설계해야 합니다.

### WHY: 처리량만 높이면 공격 도구가 된다

목표가 평균 400 pages/s, 피크 800 pages/s라고 해도 한 사이트에 800건을 몰아 보내면 안 됩니다. 전체 처리량과 개별 Host에 대한 예의는 다른 제약입니다. `robots.txt`를 지키고 같은 Host의 요청 간격을 조절해야 합니다.

또한 모든 URL의 가치가 같지 않습니다. 자주 바뀌는 뉴스 홈과 몇 년째 그대로인 문서를 같은 주기로 방문하면 자원을 낭비합니다. 빨리 많이 받는 것보다 무엇을 언제 다시 받을지 정하는 일이 더 어렵습니다.

### WHAT: 수집 파이프라인

기본 흐름은 다음과 같습니다.

```text
Seed URLs
  -> URL Frontier
  -> DNS Resolver
  -> HTML Downloader
  -> Parser
  -> Content Deduplication
  -> URL Extractor
  -> URL Filter
  -> URL Deduplication
  -> URL Frontier
```

**Seed URL**은 탐색의 시작점입니다. 지역, 언어, 주제별로 나눠 넓은 범위를 덮습니다.

**URL Frontier**는 앞으로 방문할 URL을 보관합니다. 단순 FIFO만 쓰면 낮은 품질의 링크가 큐를 채우고 중요한 페이지가 늦어집니다.

**Downloader와 DNS Resolver**는 실제 네트워크 I/O를 맡습니다. DNS Cache와 짧은 Timeout으로 느린 외부 시스템이 Worker를 오래 붙잡지 않게 합니다.

**Parser와 Extractor**는 잘못된 HTML을 격리하고 상대 URL을 절대 URL로 정규화한 뒤 새 링크를 뽑습니다.

**Content Seen**은 페이지 본문 Hash로 같은 콘텐츠를 반복 저장하지 않게 합니다. **URL Seen**은 이미 방문하거나 예약한 URL을 다시 큐에 넣지 않게 합니다. URL 중복과 콘텐츠 중복은 서로 다른 문제입니다.

### HOW: Front Queue와 Back Queue를 나눈다

Front Queue는 중요도를 관리합니다. PageRank, 변경 빈도, 사이트 품질, 마지막 방문 시각에 따라 우선순위를 계산합니다. 높은 우선순위 큐를 더 자주 선택하되 낮은 우선순위도 굶지 않게 합니다.

Back Queue는 Politeness를 관리합니다. 같은 Host의 URL을 같은 큐에 넣고 Worker가 순차로 가져갑니다.

```text
front queues: priority
queue router: hostname -> back queue
back queues: per-host FIFO and next-fetch time
workers: fetch only when host delay has passed
```

이 구조를 쓰면 전체 Worker 수를 늘려 처리량을 높이면서도 한 Host에는 정해진 간격으로만 요청할 수 있습니다. `robots.txt` 결과도 Host별로 캐시하되 만료와 재확인 정책을 둡니다.

### Freshness와 재수집

처음 방문한 페이지를 저장하는 것으로 끝나지 않습니다. 변경 이력을 보고 다음 방문 시각을 정합니다. 자주 바뀌고 중요한 페이지는 빨리 다시 방문하고, 변화가 없는 페이지는 간격을 늘립니다.

HTTP의 `ETag`와 `Last-Modified`를 활용하면 본문 전체를 다시 받지 않고 변경 여부를 확인할 수 있습니다. 삭제된 페이지, Redirect Chain, Canonical URL도 상태로 관리해야 합니다.

### 실무 예와 트레이드오프

검색 엔진은 중요한 페이지의 Freshness를 우선할 수 있고, 웹 아카이브는 시점별 원본 보존을 우선할 수 있습니다. 저작권 감시 Crawler는 특정 도메인과 이미지 Fingerprint에 자원을 집중합니다. 같은 Crawler 구조라도 우선순위 함수와 보관 정책이 다릅니다.

정확한 URL Seen 집합은 메모리를 많이 씁니다. Bloom Filter를 쓰면 공간을 줄일 수 있지만 False Positive 때문에 처음 보는 URL을 이미 본 것으로 오판할 수 있습니다. 검색 범위 누락이 허용되는지에 따라 선택해야 합니다.

### 실패 모드

Spider Trap은 달력의 다음 달 링크나 무한한 Query Parameter처럼 끝없는 URL을 만듭니다. URL 길이, 경로 깊이, 같은 패턴의 Parameter 수, 도메인별 페이지 한도를 둡니다.

잘못된 HTML과 압축 폭탄은 Parser와 메모리를 공격할 수 있습니다. 응답 크기 제한, Content Type 검사, 격리된 Parser, Timeout이 필요합니다. 한 URL의 실패가 Worker 전체를 죽이지 않게 해야 합니다.

DNS가 느리거나 특정 Host가 응답하지 않으면 Worker가 묶입니다. Host별 Circuit Breaker와 재시도 Backoff를 두고, 실패 URL은 별도 Queue에서 처리합니다.

### 함정 체크

- 전체 QPS만 제한하고 Host별 요청 간격을 생략하지 않습니다.
- BFS FIFO 하나로 중요도와 Politeness를 모두 해결하려 하지 않습니다.
- URL 중복과 콘텐츠 중복을 같은 검사로 취급하지 않습니다.
- URL 정규화 없이 Query 순서와 Fragment가 다른 주소를 모두 새 페이지로 넣지 않습니다.
- 재시도를 즉시 반복해 느린 사이트를 더 압박하지 않습니다.
- JavaScript Rendering이 필요한 범위를 합의하지 않고 모든 페이지에 Browser를 띄우지 않습니다.

### 오늘의 한 문장

> Web Crawler의 성능은 초당 다운로드 수보다, Host별 예의와 우선순위와 재수집 시점을 얼마나 잘 분리했는지에 달려 있습니다.

### 30초 확인 문제

전체 처리량 목표는 800 pages/s인데 한 뉴스 사이트에서 수십만 개의 URL이 발견됐습니다. Worker를 늘려 모두 빨리 받으면 왜 안 되며, URL Frontier를 어떻게 구성해야 할까요?

### 정답과 해설

한 Host에 요청을 몰면 상대 서버를 과부하시키고 차단될 수 있습니다. `robots.txt`와 Crawl Delay도 위반할 수 있습니다.

중요도별 Front Queue와 Host별 Back Queue를 나눕니다. Front Queue가 어떤 URL을 먼저 볼지 정하고, Queue Router가 Host별 Back Queue로 보냅니다. Worker는 각 Host의 다음 요청 가능 시각이 지난 뒤 한 건씩 처리합니다. 다른 Host를 병렬로 수집해 전체 처리량을 유지합니다.

### 더 보기

![Day 09 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-09-web-crawler.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-09-web-crawler.md)
- [원문: Chapter 9, Design a Web Crawler](https://github.com/liquidslr/system-design-notes/tree/main/09.%20Web%20Crawler)
