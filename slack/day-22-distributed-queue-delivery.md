# System Design Daily

## Day 22/40: 재처리는 오프셋과 멱등성의 문제다

### 오늘의 질문

소비자가 결제를 완료한 직후 죽고 offset을 기록하지 못했다면, 메시지를 잃지 않으면서 이중 결제도 막을 수 있을까요?

### 전달 보장은 처리 순서에서 결정된다

큐가 메시지를 소비자에게 건넸다는 사실과 업무 처리가 끝났다는 사실은 다릅니다. 둘 사이에서 장애가 나면 메시지를 잃거나 다시 처리합니다. 소비 로직에서 **업무 처리와 offset commit의 순서**가 전달 보장을 결정합니다.

```text
1. fetch message
2. process business logic
3. commit offset
```

처리 전에 offset을 먼저 commit하면 소비자가 죽었을 때 메시지를 잃을 수 있습니다. 처리 후 commit하면 메시지는 잃지 않지만, 처리 직후 죽으면 같은 메시지를 다시 읽습니다.

### 세 가지 전달 보장

**At-most-once**는 최대 한 번 전달합니다. 소비자가 처리 전에 offset을 commit하고 실패 시 재시도하지 않습니다. 중복은 피하지만 유실을 허용합니다. 최신 상태가 곧 다시 들어오는 비핵심 메트릭처럼 일부 손실을 견딜 수 있을 때 선택할 수 있습니다.

**At-least-once**는 적어도 한 번 처리합니다. 생산자는 확인 응답을 받지 못하면 다시 보내고, 소비자는 업무 처리 후 offset을 commit합니다. 유실을 줄이는 대신 중복을 허용합니다. 대부분의 업무 시스템은 이 방식과 멱등 처리를 조합합니다.

**Exactly-once**는 결과가 한 번만 반영되게 합니다. 표현은 단순하지만 큐, 소비자 상태, 외부 데이터베이스의 원자적 갱신이 필요합니다. 모든 경계를 한 트랜잭션으로 묶기 어렵고 비용도 큽니다. 그래서 실무의 목표는 흔히 "메시지가 한 번만 도착"이 아니라 **중복 도착해도 결과가 한 번만 반영**되는 것입니다.

### 멱등성이 재처리를 안전하게 만든다

결제 승인 이벤트에 고유한 `event_id`가 있다고 가정합니다. 소비자는 결과를 쓸 때 같은 트랜잭션에서 처리 이력을 남깁니다.

```sql
INSERT INTO processed_event(event_id)
VALUES (:event_id);

UPDATE payment
SET status = 'APPROVED'
WHERE payment_id = :payment_id;
```

`processed_event.event_id`에 unique constraint를 두면 같은 이벤트가 다시 와도 두 번째 반영을 막을 수 있습니다. 상태 변경 자체를 조건부 갱신으로 만드는 방법도 있습니다.

```sql
UPDATE payment
SET status = 'APPROVED'
WHERE payment_id = :payment_id
  AND status = 'PENDING';
```

외부 API 호출처럼 데이터베이스 트랜잭션에 묶을 수 없는 작업은 대상 API에 idempotency key를 전달하거나, outbox에 실행 의도를 기록한 뒤 별도 전달자가 재시도하게 합니다.

### 순서 보장은 실패 경로까지 포함한다

파티션 안의 기록 순서가 같아도 처리 완료 순서는 달라질 수 있습니다. 소비자가 메시지를 병렬 처리하거나 실패한 메시지만 retry topic으로 보내면 뒤 메시지가 먼저 완료됩니다.

예를 들어 `ORDER_CREATED`가 실패해 재시도 중인데 `ORDER_CANCELLED`가 먼저 처리되면 상태 전이가 깨질 수 있습니다. 선택지는 세 가지입니다.

- 같은 키의 메시지는 한 작업 흐름에서 직렬 처리합니다.
- 상태 전이에 version을 두고 오래된 이벤트를 거부합니다.
- retry topic에서도 원래 키를 유지하고, 재시도 중인 키의 후속 처리를 잠시 막습니다.

전체 처리량을 위해 모든 메시지를 멈추는 대신, 순서가 필요한 키만 제한하는 편이 낫습니다.

### 재시도와 격리

일시적인 네트워크 오류는 지수 backoff로 재시도할 수 있습니다. 하지만 스키마 오류나 잘못된 데이터는 백 번 재시도해도 성공하지 않습니다. 이런 poison message는 재시도 횟수를 제한하고 dead-letter queue로 옮겨야 합니다.

```text
main topic -> retry 10s -> retry 1m -> retry 10m -> DLQ
```

DLQ는 쓰레기통이 아닙니다. 원본 topic, partition, offset, 실패 원인, 시도 횟수, 마지막 오류를 남겨야 운영자가 수정 후 재투입할 수 있습니다. 재투입 역시 같은 멱등 키를 유지해야 합니다.

### 생산자 확인 응답도 트레이드오프다

리더만 기록하면 성공으로 볼지, 동기화된 복제본까지 기록해야 성공으로 볼지에 따라 지연과 내구성이 달라집니다.

- `ACK=0`은 빠르지만 생산자가 기록 성공 여부를 모릅니다.
- `ACK=1`은 리더 기록까지 기다립니다. 리더 장애 시 복제되지 않은 메시지를 잃을 수 있습니다.
- `ACK=all`은 동기화된 복제본의 확인을 기다립니다. 더 안전하지만 느린 복제본이 지연을 키웁니다.

중요한 결제 이벤트와 손실을 허용하는 디버그 이벤트에 같은 설정을 강요할 필요는 없습니다.

### 함정 체크

- Exactly-once를 브로커 설정 하나로 해결할 수 있다고 생각하면 안 됩니다.
- offset을 commit했다고 업무 결과까지 저장된 것은 아닙니다.
- 무한 재시도는 장애를 복구하지 않고 큐 지연과 외부 시스템 부하만 키울 수 있습니다.
- retry topic으로 옮긴 메시지는 원래 파티션의 순서 보장을 자동으로 이어받지 않습니다.

### 오늘의 한 문장

**유실을 막으려면 재처리를 받아들이고, 중복 결과를 막으려면 업무 처리를 멱등하게 만들어야 합니다.**

### 30초 확인 문제

소비자가 재고를 1개 차감한 뒤 offset commit 전에 죽었습니다. 재시작 후 같은 메시지가 다시 오면 어떤 문제가 생기며, 가장 현실적인 방어는 무엇일까요?

### 정답과 해설

그대로 다시 처리하면 재고가 2개 차감됩니다. `event_id` 처리 이력과 재고 갱신을 같은 데이터베이스 트랜잭션에 넣거나, 재고 갱신을 event version에 대한 조건부 연산으로 만들어야 합니다. 큐는 at-least-once로 운용하되 결과 반영을 멱등하게 만드는 방식이 현실적입니다.

### 더 보기

![Day 22 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-22-distributed-queue-delivery.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-22-distributed-queue-delivery.md)
- [원문: Chapter 19: Distributed Message Queue](https://github.com/liquidslr/system-design-notes/tree/main/19.%20Distributed%20Message%20Queue)
