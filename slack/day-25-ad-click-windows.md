# System Design Daily

## Day 25/40: 시간 창은 질문의 모양을 결정한다

### 오늘의 질문

"최근 5분간 클릭 수"와 "매분 직전 5분의 인기 광고"는 같은 집계일까요?

### 무한한 이벤트를 유한하게 자른다

광고 클릭은 끝없이 들어오는 stream입니다. 합계나 순위를 계산하려면 어디서부터 어디까지를 한 결과로 볼지 정해야 합니다. 이 경계가 window입니다.

하루 10억 건의 클릭에서 광고별 최근 클릭 수와 Top N을 매번 원본으로 계산하면 느리고 비쌉니다. stream processor가 작은 시간 구간의 상태를 메모리에 유지하고, window가 닫힐 때 집계 결과를 내보내면 질의는 미리 계산된 결과를 읽을 수 있습니다.

```text
AdClickEvent(ad_id, event_time, user_id, ip, country)
-> partition by ad_id
-> window
-> count or Top N
-> aggregated store
```

### Event time과 processing time

클릭이 발생한 시각은 event time이고 집계 서버가 받은 시각은 processing time입니다. queue 지연과 네트워크 재시도로 둘은 달라집니다.

과금과 보고에는 클릭이 실제로 발생한 시각이 더 알맞습니다. processing time을 쓰면 12:00:59에 발생한 클릭이 지연 때문에 12:01 구간에 들어갈 수 있습니다. 하지만 event time은 클라이언트 시계가 틀리거나 조작될 수 있습니다. 서버가 이벤트 수신 시각과 event time을 함께 기록하고 허용 가능한 차이를 검증해야 합니다.

### Tumbling window

Tumbling window는 겹치지 않는 고정 구간입니다. 1분 단위 광고별 클릭 수에 잘 맞습니다.

```text
[12:00:00, 12:01:00)
[12:01:00, 12:02:00)
[12:02:00, 12:03:00)
```

각 이벤트는 정확히 한 window에 속합니다. 구현과 저장이 단순하고 결과를 합산하기 쉽습니다. 대신 "지금부터 직전 5분"처럼 계속 움직이는 질문에는 경계 오차가 생깁니다.

### Hopping window

Hopping window는 window 크기와 이동 간격을 따로 둡니다. 5분 window를 1분마다 계산하면 서로 겹칩니다.

```text
size = 5m, hop = 1m
12:00 기준: [11:55, 12:00)
12:01 기준: [11:56, 12:01)
```

"매분 최근 5분 Top 100"에 알맞습니다. 한 이벤트가 여러 window에 포함되므로 단순하게 구현하면 계산량이 window 크기만큼 늘어납니다. 1분 tumbling 집계를 먼저 만든 뒤 최근 다섯 bucket을 더하거나, 들어온 bucket을 더하고 만료된 bucket을 빼는 방식으로 줄일 수 있습니다.

### Sliding window

Sliding window는 새 이벤트나 질의 시점에 맞춰 경계가 연속적으로 움직입니다. 정확한 최근 N분을 표현하지만 상태 관리가 더 복잡합니다. 이벤트 timestamp queue를 유지하고 경계 밖 이벤트를 제거하는 방식이 대표적입니다.

Top N은 단순 count보다 어렵습니다. 광고별 count를 갱신하면서 순위 heap을 함께 유지하거나, 각 shard가 local Top N을 만든 뒤 reducer가 global Top N을 계산할 수 있습니다. 인기 광고 하나가 특정 shard에 몰리면 hotspot도 고려해야 합니다.

### Session window

Session window는 고정 시간이 아니라 활동 간격으로 묶습니다. 사용자가 30분 이상 활동하지 않으면 한 세션을 닫는 식입니다. 광고별 분당 집계에는 맞지 않지만 사용자 여정이나 캠페인 내 행동 묶음에 유용합니다.

### 어떤 window를 선택할까

- 분 단위 과금 원장은 1분 tumbling window가 단순합니다.
- 매분 최근 10분 인기 광고는 size 10분, hop 1분의 hopping window가 맞습니다.
- 사용자의 연속 클릭 행동은 session window로 볼 수 있습니다.
- 실시간 UI의 정확한 직전 60초는 sliding window가 적합하지만 비용이 큽니다.

질문이 같아 보여도 갱신 주기, 경계 포함 규칙, 허용 지연이 다르면 결과가 달라집니다. API에서 `from`, `to`, timezone, 경계의 inclusive 여부를 명확히 해야 합니다.

### 미리 집계할 차원

국가, IP 대역, 사용자 유형으로 필터링해야 한다면 자주 쓰는 dimension을 미리 집계할 수 있습니다. 질의는 빨라지지만 조합 수만큼 bucket이 늘어납니다. `user_id`처럼 cardinality가 큰 차원을 모두 미리 집계하면 저장량이 폭발합니다.

원본 이벤트는 cold storage에 남겨 버그 수정과 backfill에 쓰고, 서비스 질의는 집계 테이블을 읽는 구성이 현실적입니다.

### 함정 체크

- "5분 집계"만 말하고 window 크기와 이동 간격을 구분하지 않으면 안 됩니다.
- processing time으로 과금 결과를 만들면 queue 지연이 집계 구간을 바꿉니다.
- Top N을 모든 원본에서 매분 다시 정렬하면 규모가 커질수록 비용이 감당되지 않습니다.
- 모든 필터 조합을 미리 계산하면 차원 수의 곱만큼 상태가 늘어납니다.

### 오늘의 한 문장

**시간 창은 구현 세부가 아니라, 어떤 이벤트를 같은 답으로 묶을지 정하는 제품 계약입니다.**

### 30초 확인 문제

매분 "직전 10분간 클릭이 많은 광고 100개"를 보여 줘야 합니다. 어떤 window를 쓰고, 매번 10분 원본을 다시 읽지 않으려면 어떻게 계산할까요?

### 정답과 해설

크기 10분, 이동 간격 1분인 hopping window를 씁니다. 1분 tumbling bucket별 광고 count를 먼저 만들고, 최근 10개 bucket을 합칩니다. 새 bucket을 더하고 10분이 지난 bucket을 빼는 incremental 방식과 shard별 local Top N 후 global reduce를 조합하면 계산량을 줄일 수 있습니다.

### 더 보기

![Day 25 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-25-ad-click-windows.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-25-ad-click-windows.md)
- [원문: Chapter 21: Ad Click Event Aggregation](https://github.com/liquidslr/system-design-notes/tree/main/21.%20Ad%20Click%20Event%20Aggregation)
