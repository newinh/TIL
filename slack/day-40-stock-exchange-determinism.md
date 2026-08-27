# System Design Daily

## Day 40/40: 거래소의 공정성과 복구는 같은 순서에서 나온다

### 오늘의 질문

- Primary Matching Engine이 체결 직후 죽었을 때, Standby가 중복 체결 없이 정확히 다음 주문부터 이어가려면 무엇을 공유해야 할까요?

### 결정성은 고가용성의 전제다

Standby가 Primary와 같은 코드와 같은 시작 상태를 갖고 있어도 입력 순서가 다르면 Order Book과 Fill이 달라집니다. 거래소는 모든 입력 명령과 출력 Fill에 Sequence를 부여합니다.

```text
Input:  seq 701 NEW A
Input:  seq 702 NEW B
Output: seq 990 FILL A,B,100
```

State Machine이 외부 시각이나 난수에 의존하지 않고 Sequence 순서만 따라가면 같은 로그를 재생한 Replica가 같은 상태를 만듭니다. 이것이 Functional Determinism입니다.

시간은 감사 정보로 기록할 수 있지만 체결 순서의 근거는 Sequencer가 만든 순서여야 합니다. 같은 가격에서는 이 순서가 Price-Time Priority의 Time 역할을 합니다.

### Event Sourcing과 단일 Writer

거래소는 주문, 취소, Fill을 Append-only Event Log에 남깁니다. Order Manager와 Matching Engine, Market Data는 이 로그를 소비해 자기 상태를 만듭니다.

낮은 지연을 위해 모든 Critical Component를 한 큰 서버에 두고 `mmap` 기반 Event Store로 통신할 수 있습니다. 각 Component는 CPU Core에 고정한 Event Loop로 동작해 Lock, Context Switch, Network Hop을 줄입니다.

이 설계는 Scale-out보다 Scale-up을 택합니다. Microservice를 많이 나누는 것이 항상 좋은 설계는 아닙니다. 수십 마이크로초와 안정적인 꼬리 지연이 목표라면 프로세스 경계를 줄이는 편이 합리적입니다. 대신 서버 장애의 영향이 커지므로 복제와 복구가 더 중요해집니다.

### Active와 Standby는 같은 로그를 처리한다

Stateful Component의 Standby도 입력 Event를 계속 처리해 Primary와 상태를 맞춥니다. 단, Leader만 외부로 Fill과 시장 데이터를 발행합니다. 둘 다 발행하면 중복 체결 결과가 나갑니다.

```text
Sequenced Log -> Active State Machine -> publish
              -> Standby State Machine -> suppress output
```

Heartbeat로 장애를 감지하고 새 Leader를 고릅니다. 새 Leader는 마지막으로 Commit된 Input Sequence와 발행된 Output Sequence를 확인한 뒤 이어갑니다. 로그 복제 전 외부 응답을 내보내면 Primary 장애 때 승인한 주문을 Standby가 잃을 수 있습니다.

Failover 설계에는 숫자가 필요합니다.

- **RPO**: 체결 데이터 손실을 얼마나 허용하는가. 거래소는 보통 0을 요구합니다.
- **RTO**: 몇 초 안에 거래를 재개해야 하는가.
- **Degraded Mode**: 신규 주문은 막고 취소만 받을지, 시장을 잠시 중단할지 정합니다.

Split Brain은 가장 위험한 실패입니다. 두 Engine이 자신을 Leader라고 믿으면 서로 다른 Fill을 발행합니다. Quorum과 Fencing Token으로 이전 Leader의 발행 권한을 끊어야 합니다.

### 공정성은 주문 순서와 배포 순서 모두에 필요하다

주문 접수 순서만 공정해도 시장 데이터가 일부 참여자에게 먼저 가면 정보 우위가 생깁니다. Multicast는 동일 데이터를 여러 Subscriber에게 비슷한 경로로 보낼 수 있습니다. UDP는 손실될 수 있으므로 Sequence Gap 감지와 Retransmission Channel을 함께 둡니다.

Colocation은 Broker 서버를 거래소와 같은 데이터센터에 두어 지연을 줄입니다. 이를 제공한다면 케이블 길이, Gateway 처리, Market Data 배포 정책까지 측정하고 공개된 규칙으로 운영해야 합니다.

### 평균보다 꼬리 지연의 결정성을 본다

Functional Determinism은 같은 결과를 뜻합니다. Latency Determinism은 지연이 예측 가능한 범위에 머무는지를 뜻합니다. 평균 20마이크로초여도 99.99 percentile이 수십 밀리초로 튀면 일부 주문이 계속 불리해집니다.

지연 Spike의 원인은 GC Pause, Page Fault, CPU Migration, Cache Miss, Buffer Allocation, 로그 I/O가 될 수 있습니다. 그래서 Critical Path에서는 다음 선택을 합니다.

- 메모리를 미리 할당한 Ring Buffer를 사용합니다.
- Event Loop를 CPU Core에 고정합니다.
- 런타임 GC와 동적 할당을 줄입니다.
- 동기 로그와 리포팅을 Critical Path에서 뺍니다.
- 평균뿐 아니라 p99, p99.9, p99.99와 Sequence Gap을 봅니다.

최적화는 감사 가능성을 없애는 방향으로 하면 안 됩니다. Critical Path에서 텍스트 로그를 빼더라도 Sequenced Event Log는 남겨 사후 재생과 조사 근거를 보존합니다.

### 장애 복구를 실제로 검증한다

Replica가 있다는 사실만으로 복구를 믿으면 안 됩니다. 주문 유입 중 Process Kill, Network Partition, 느린 Replica, 중복 Packet을 주입해 다음을 확인합니다.

- Commit된 주문이 사라지지 않는가
- 같은 Fill이 두 번 발행되지 않는가
- Standby 재생 결과의 State Hash가 Active와 같은가
- Sequence Gap을 탐지하고 복구하는가
- Fencing 뒤 이전 Leader가 발행하지 못하는가

자동 Failover는 실패 모드를 충분히 이해한 뒤 켭니다. 초기에는 수동 전환과 Rehearsal로 증거를 모으는 편이 잘못된 자동 전환보다 안전할 수 있습니다.

### 함정 체크

- Replica가 같은 데이터만 가지면 된다고 생각하면 안 됩니다. 같은 순서와 Commit 경계가 필요합니다.
- Heartbeat Timeout만으로 Leader를 바꾸면 느린 Primary와 새 Leader가 동시에 발행할 수 있습니다.
- 로그 복제 전에 성공 응답을 보내면 장애 때 인정한 주문을 잃습니다.
- Multicast를 쓰면 동시에 도착한다고 가정하면 안 됩니다. 손실과 재전송 경로가 필요합니다.
- 평균 지연만 보면 일부 사용자가 반복해서 겪는 긴 Pause를 놓칩니다.
- 성능을 위해 모든 로그를 제거하면 체결 분쟁을 재현할 수 없습니다.

### 오늘의 한 문장

> 거래소는 하나의 순서를 공정성의 기준, 복제의 단위, 장애 복구의 출발점으로 함께 사용합니다.

### 30초 확인 문제

Standby가 Primary와 같은 입력을 처리하지만 외부 환율 API와 현재 시각을 Matching 중 읽습니다. 안전한 Failover가 가능할까요?

### 정답과 해설

불가능합니다. 외부 값이 달라 같은 입력 Sequence에서도 다른 체결 결과를 만들 수 있습니다. Matching State Machine은 Sequenced Event와 기존 State만 사용해야 합니다. 필요한 외부 판단은 주문이 Sequencer에 들어가기 전에 확정해 입력 Event에 기록해야 Active와 Standby가 같은 결과를 냅니다.

### 더 보기

![Day 40 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-40-stock-exchange-determinism.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-40-stock-exchange-determinism.md)
- [원문: Chapter 28, Stock Exchange](https://github.com/liquidslr/system-design-notes/tree/main/28.%20Stock%20Exchange)
