# System Design Daily

## Day 38/40: 지갑 잔액은 이벤트에서 다시 만들 수 있어야 한다

### 오늘의 질문

- 코드 변경 뒤 현재 잔액이 맞다는 사실을 어떻게 증명하고, 어제 오후 3시의 잔액을 어떻게 재현할까요?

### 현재 값만 저장하면 이유를 잃는다

`wallet.balance = 54000`만 남으면 조회는 빠릅니다. 하지만 54,000원이 어떤 이체들의 결과인지, 과거 값이 맞았는지, 새 코드가 같은 거래에서 같은 결과를 만드는지 증명하기 어렵습니다.

Event Sourcing은 현재 상태 대신 상태를 바꾼 사실을 불변 로그로 남깁니다. 현재 잔액은 이벤트를 순서대로 적용한 결과입니다.

### Command, Event, State를 구분한다

```text
Command: A에서 B로 1000원을 보내라
Event: A에서 1000원이 차감됐다
Event: B에 1000원이 입금됐다
State: A와 B의 현재 잔액
```

Command는 의도이므로 실패할 수 있습니다. 잔액 부족, 계정 정지, 중복 Transaction ID로 거부될 수 있습니다. 외부 조회나 위험 판단 때문에 결과가 달라질 수도 있습니다.

Event는 이미 발생한 사실입니다. 한 번 기록한 Event를 수정하면 과거 재현과 감사가 깨집니다. 잘못된 결과는 기존 Event 삭제가 아니라 보상 Event로 고칩니다.

State Machine은 Command를 검증해 Event를 만들고, Event를 적용해 State를 갱신합니다. Event 적용 함수는 결정적이어야 합니다.

```text
new_state = apply(previous_state, event)
```

시간, 난수, 외부 API 응답을 `apply` 안에서 다시 읽으면 재생할 때 다른 결과가 나옵니다. 필요한 값은 Event 생성 시 확정해 Event 본문에 기록합니다.

### 전역 순서보다 필요한 순서를 정의한다

모든 지갑 거래를 하나의 전역 FIFO로 세우면 순서는 단순하지만 100만 TPS를 한 파티션이 감당해야 합니다. 실제로는 같은 계정에 영향을 주는 Event의 순서가 핵심입니다. 계정이나 관련 이체 단위로 Partition을 정하되, 두 계정 이체는 분산 트랜잭션 Coordinator가 각 Partition의 결과를 묶어야 합니다.

순서 번호와 Transaction ID를 Event에 넣어 누락, 중복, 역순 적용을 감지합니다.

### CQRS로 쓰기와 읽기를 나눈다

Event Log는 감사와 재생에 좋지만 매 조회마다 처음부터 계산할 수는 없습니다. 읽기 전용 Projection이 Event를 소비해 잔액, 거래 내역, 일별 집계를 만듭니다.

```text
Command -> Write State Machine -> Event Log
                               -> Balance Projection
                               -> History Projection
                               -> Audit Projection
```

Projection은 지연될 수 있습니다. Command 성공 직후 잔액 조회가 이전 값을 보여 줄 수 있으므로 API는 처리 상태를 제공하거나, 해당 Event Sequence까지 반영됐는지 확인하는 Read-your-writes 전략을 둡니다.

### Snapshot은 재생 시간을 줄인다

이벤트가 수십억 개면 서비스 시작 때 처음부터 재생할 수 없습니다. 특정 Sequence의 State Snapshot을 저장하고 그 뒤 Event만 적용합니다.

```text
state_at_10M = snapshot
current_state = replay(events after 10M)
```

Snapshot은 파생 데이터이므로 Event Log가 진실의 원천입니다. Snapshot 버전과 State Machine 버전을 기록하고 검증에 실패하면 이전 Snapshot이나 Event부터 다시 만듭니다.

### 무엇을 가장 강하게 복제할까

State와 Projection, Snapshot은 Event에서 다시 만들 수 있습니다. Command는 외부 I/O와 검증 결과 때문에 같은 Event를 다시 만든다고 보장할 수 없습니다. 따라서 가장 강한 내구성과 순서 보장이 필요한 것은 확정된 Event Log입니다.

Leader가 Event를 순서대로 기록하고 다수 Replica에 복제한 뒤 Commit하면 장애 전환 후에도 같은 Log를 재생할 수 있습니다. 모든 Node는 같은 Event와 같은 결정적 State Machine으로 같은 State를 만듭니다.

### 코드 변경은 과거 데이터로 검증한다

새 State Machine 버전을 과거 Event 복사본에 실행해 기존 결과와 비교할 수 있습니다. 차이가 의도한 정책 변경인지 Bug인지 확인합니다. Event Schema도 오래 보존되므로 Upcaster나 버전별 Reader가 필요합니다. 과거 Event를 새 필드가 항상 있다고 가정해 읽으면 재생이 중단됩니다.

### 함정 체크

- Command Log만 있으면 Event를 재현할 수 있다고 가정하면 안 됩니다. Command 처리에는 외부 결과가 섞일 수 있습니다.
- Event 적용 중 현재 시각이나 외부 API를 읽으면 재생이 결정적이지 않습니다.
- Event를 수정하거나 삭제하면 감사와 과거 재현이 깨집니다.
- Projection 지연을 숨기면 사용자는 성공 직후 이전 잔액을 봅니다.
- Snapshot을 진실의 원천으로 취급하면 손상됐을 때 복구 경로가 없습니다.
- Event Schema 변경의 하위 호환성을 준비하지 않으면 오래된 로그를 재생할 수 없습니다.

### 오늘의 한 문장

> Event Sourcing에서 잔액은 저장된 정답이 아니라, 순서가 보장된 사실을 결정적으로 재생한 결과입니다.

### 30초 확인 문제

환율 API 응답을 Event 적용 시점마다 다시 조회하도록 구현했습니다. 과거 Event를 재생하면 어떤 문제가 생길까요?

### 정답과 해설

같은 Event도 재생 시점의 환율에 따라 다른 잔액을 만들 수 있어 결정성이 깨집니다. Command 처리 시 사용한 환율과 통화 변환 결과를 Event에 기록하고, `apply`는 Event 안의 값만 사용해야 합니다. 외부 I/O는 Event 생성 전에 끝내고 사실로 고정합니다.

### 더 보기

![Day 38 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-38-wallet-event-sourcing.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-38-wallet-event-sourcing.md)
- [원문: Chapter 27, Digital Wallet](https://github.com/liquidslr/system-design-notes/tree/main/27.%20%20Digital%20Wallet)
