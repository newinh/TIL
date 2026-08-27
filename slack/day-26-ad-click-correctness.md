# System Design Daily

## Day 26/40: 정확한 집계는 늦고 중복된 이벤트를 다루는 일이다

### 오늘의 질문

광고 클릭이 늦게 도착하거나 두 번 들어와도 과금 금액을 맞추려면 stream 집계를 어떻게 복구해야 할까요?

### 빠른 집계와 정확한 집계는 충돌한다

광고 클릭 집계는 몇 분 안에 대시보드에 보여야 하지만 청구 금액도 맞아야 합니다. 모든 이벤트를 오래 기다리면 정확도는 올라가지만 결과가 늦습니다. window를 빨리 닫으면 응답은 빠르지만 지연 이벤트를 놓칩니다.

따라서 하나의 숫자에 모든 책임을 몰지 않습니다. 실시간 경로는 빠른 잠정 집계를 만들고, 원본 이벤트를 보존한 batch reconciliation이 확정값을 맞춥니다.

### Watermark는 기다림의 상한이다

Event time 기준 12:00에서 12:01까지의 window가 끝나도 곧바로 닫지 않고 일정 시간 늦게 온 이벤트를 받습니다. "12:03이 되었으면 12:01 이전 이벤트는 대부분 도착했다"고 판단하는 기준이 watermark입니다.

```text
window end = 12:01
allowed lateness = 2m
finalize at processing time 12:03
```

Watermark가 짧으면 결과가 빠르지만 누락이 늘고, 길면 정확도는 높아지지만 상태를 오래 들고 있어야 합니다. 국가 간 네트워크, 모바일 offline, queue 장애에 따라 지연 분포가 다르므로 추측이 아니라 실제 p95, p99 지연을 보고 정해야 합니다.

Watermark 뒤에 온 이벤트를 버릴 필요는 없습니다. 별도 late-event stream에 기록해 보정 집계나 일 마감 reconciliation에 반영할 수 있습니다.

### 중복은 정상적인 실패 결과다

클라이언트가 확인 응답을 못 받아 재전송하거나, consumer가 결과를 쓴 뒤 offset commit 전에 죽으면 같은 클릭이 다시 처리됩니다. At-least-once 전달에서는 중복이 예외가 아니라 정상 경로입니다.

이벤트 생성 시 안정적인 `event_id`를 부여하고 일정 기간 본 ID를 dedup state에 저장할 수 있습니다.

```text
if seen(event_id):
    ignore
else:
    aggregate(event)
    remember(event_id, ttl)
```

문제는 하루 10억 건 규모에서 모든 ID를 영원히 저장할 수 없다는 점입니다. dedup TTL은 최대 재시도 기간과 late-event 허용 범위를 포함해야 합니다. Bloom filter는 메모리를 줄이지만 false positive 때문에 정상 클릭을 버릴 수 있어 과금 원장의 최종 판정에는 조심해야 합니다.

### Offset과 결과를 함께 다룬다

집계 결과 저장과 offset commit이 따로 일어나면 둘 사이 장애가 중복이나 누락을 만듭니다.

```text
1. offset 100 읽기
2. count 증가
3. 결과 저장 성공
4. offset commit 실패
5. offset 100 재처리
```

가능하다면 집계 결과와 처리 offset을 같은 트랜잭션으로 저장합니다. Kafka 내부 결과라면 transactional write를 고려할 수 있습니다. 외부 데이터베이스라면 결과 key를 `(ad_id, window_start, filter_id)`로 고정하고 version 또는 source offset을 조건으로 upsert해 재실행을 멱등하게 만듭니다.

"Exactly-once"라는 이름보다 어느 경계까지 원자성이 이어지는지 확인해야 합니다. queue 안에서 한 번이어도 외부 DB write가 두 번이면 과금 결과는 두 번 반영됩니다.

### 원본과 집계본을 함께 보관한다

집계 테이블은 빠르게 조회할 수 있지만 계산 로직 버그를 스스로 고치지 못합니다. 원본 클릭은 object storage나 write-heavy store에 보관하고, 집계 결과는 별도 store에 둡니다.

버그를 발견하면 실시간 consumer와 경쟁하지 않는 전용 recalculation job이 원본을 다시 읽습니다. 결과는 같은 멱등 key와 더 높은 revision으로 써야 과거 backfill이 최신 실시간 값을 덮어쓰지 않습니다.

### 일 마감 reconciliation

실시간 집계와 원본 batch 집계를 비교하면 누락과 중복을 찾을 수 있습니다. 차이가 허용 범위를 넘으면 해당 광고와 window를 다시 계산합니다.

관찰할 지표는 처리량만이 아닙니다.

- event time에서 집계 저장까지의 지연
- watermark 뒤에 도착한 이벤트 비율
- dedup으로 제거한 이벤트 수
- consumer lag와 가장 오래된 이벤트 나이
- 실시간 집계와 batch 집계의 차이
- backfill이 수정한 record 수

### Hotspot과 복구 상태

인기 광고 하나가 같은 partition과 aggregation node에 몰릴 수 있습니다. key를 임시 shard로 나눠 local count를 만든 뒤 최종 합산하면 부하를 분산할 수 있습니다.

집계 node가 메모리에 Top N 상태를 들고 있다면 주기적으로 snapshot과 source offset을 함께 저장해야 합니다. 장애 후 새 node는 snapshot을 읽고 그 offset 다음부터 재생합니다. snapshot과 offset의 기준이 다르면 일부 구간을 잃거나 두 번 더합니다.

### 함정 체크

- Watermark를 늘리면 모든 지연 이벤트를 잡는다고 생각하면 안 됩니다.
- dedup ID의 TTL이 재시도 기간보다 짧으면 늦은 중복이 다시 반영됩니다.
- queue의 exactly-once 설정만으로 외부 데이터베이스 결과까지 보장되지 않습니다.
- backfill이 실시간 결과를 무조건 overwrite하면 더 오래된 계산이 최신값을 되돌릴 수 있습니다.

### 오늘의 한 문장

**정확한 stream 집계는 중복을 멱등하게 흡수하고, 늦은 이벤트를 보정하며, 원본으로 결과를 다시 만들 수 있어야 합니다.**

### 30초 확인 문제

허용 지연 5분, 재시도 최대 기간 24시간인 시스템에서 dedup ID를 10분만 보관하면 어떤 문제가 생길까요?

### 정답과 해설

10분 뒤 재전송된 같은 이벤트를 새 이벤트로 보고 다시 집계합니다. dedup 보존 기간은 허용 지연뿐 아니라 생산자와 소비자의 최대 재시도 기간을 포함해야 합니다. 저장 비용이 크다면 기간별 저장 계층이나 멱등 upsert를 함께 사용하고, 최종 결과는 reconciliation으로 검증해야 합니다.

### 더 보기

![Day 26 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-26-ad-click-correctness.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-26-ad-click-correctness.md)
- [원문: Chapter 21: Ad Click Event Aggregation](https://github.com/liquidslr/system-design-notes/tree/main/21.%20Ad%20Click%20Event%20Aggregation)
