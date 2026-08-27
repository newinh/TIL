# System Design Daily

## Day 37/40: 지갑 이체는 차감과 입금을 하나의 상태 머신으로 묶는다

### 오늘의 질문

- A의 지갑과 B의 지갑이 다른 샤드에 있을 때, A에서 돈만 빠지고 B에는 들어오지 않는 상태를 어떻게 복구할까요?

### 한 번의 이체에는 두 개의 쓰기가 있다

지갑 이체는 겉으로 한 작업이지만 내부에서는 두 계정이 바뀝니다.

```text
A balance = A balance - 1000
B balance = B balance + 1000
```

두 지갑이 한 관계형 데이터베이스에 있으면 로컬 트랜잭션으로 묶을 수 있습니다. 100만 TPS를 위해 계정별로 샤딩하면 두 쓰기가 다른 데이터베이스에 갈 수 있습니다. Redis에서 각 잔액을 빠르게 바꾸는 것만으로는 이 원자성을 얻지 못합니다.

이체 API에는 `transaction_id`를 받아 재시도 중복도 막아야 합니다.

```http
POST /v1/wallet/balance-transfer

{
  "from_account": "A",
  "to_account": "B",
  "amount_minor": 1000,
  "currency": "KRW",
  "transaction_id": "tx-20260826-001"
}
```

### 2PC: 데이터베이스가 Commit을 함께 결정한다

Two-Phase Commit에서는 Coordinator가 각 데이터베이스에 Prepare를 요청합니다. 모두 준비됐을 때 Commit하고 하나라도 실패하면 Abort합니다.

```text
Prepare A(-1000), B(+1000)
-> 모두 YES
-> Commit A, Commit B
```

강한 원자성을 제공하지만 Prepare 동안 Lock을 오래 잡고 Coordinator 장애에 영향을 받습니다. 참여 샤드가 많거나 지연이 크면 처리량이 떨어집니다. 데이터베이스와 드라이버가 분산 트랜잭션을 제대로 지원해야 합니다.

### TC/C: 예약과 보상을 비즈니스 로직으로 만든다

Try-Confirm/Cancel은 두 단계 모두 독립적인 로컬 트랜잭션입니다.

```text
Try:     A에서 1000을 예약하거나 차감한다.
Confirm: B에 1000을 입금한다.
Cancel:  A의 예약을 풀거나 1000을 되돌린다.
```

먼저 차감하는 이유가 중요합니다. 중간 상태에서 사용자에게 없는 돈이 생기면 다시 쓸 수 있습니다. 입금을 먼저 하지 않고, 차감된 금액도 진행 중 이체로 표시해 사용 가능한 잔액에서 제외합니다.

Coordinator는 각 단계 상태를 내구성 있게 저장해야 합니다.

```text
transaction_id
try_status
second_phase = CONFIRM | CANCEL
second_phase_status
out_of_order_flag
```

Coordinator가 죽었다가 살아나면 이 표에서 중단 지점을 찾아 같은 명령을 재전송합니다. 각 단계는 `transaction_id`로 멱등해야 합니다.

### 순서가 뒤집히는 실패도 설계한다

네트워크 지연으로 `Cancel`이 `Try`보다 먼저 도착할 수 있습니다. Cancel을 무시하면 나중에 도착한 Try가 돈을 차감한 채 남습니다. 참여자는 아직 Try가 없다는 사실과 선행 Cancel을 기록합니다. 뒤늦은 Try가 오면 거부하거나 즉시 상쇄합니다.

이런 실패는 정상 요청 코드만 테스트해서는 찾기 어렵습니다. 단계별 중복, 지연, 순서 변경, Coordinator 재시작을 주입해 확인해야 합니다.

### Saga: 긴 작업을 순서와 보상으로 푼다

Saga는 독립된 로컬 트랜잭션을 순서대로 실행하고 실패하면 역순으로 보상합니다. Choreography는 서비스가 이벤트를 이어받고, Orchestration은 중앙 Coordinator가 다음 단계를 지시합니다.

지갑에서는 전체 이체 상태를 한곳에서 보고 재시도하기 쉬운 Orchestration이 보통 낫습니다. TC/C는 Try 작업을 병렬화할 수 있어 낮은 지연에 유리하고, Saga는 단계가 긴 업무 흐름을 표현하기 쉽습니다. 둘 다 중간 불일치를 허용하므로 보상과 사용자 노출 규칙이 필요합니다.

### 샤딩 키가 이체 비용을 결정한다

계정 ID 해시는 부하를 고르게 나누지만 대부분의 이체가 분산 트랜잭션이 됩니다. 같은 사용자나 같은 가맹점 안의 이체가 많다면 관련 계정을 함께 배치해 로컬 트랜잭션 비율을 높일 수 있습니다. 반대로 한 대형 가맹점에 몰아넣으면 핫샤드가 됩니다.

처리량 계산은 API 요청 수가 아니라 원장 Leg와 참여 샤드 수로 해야 합니다. 100만 이체 TPS는 최소 200만 잔액 변경과 상태 기록, 복제 트래픽을 만듭니다.

### 함정 체크

- 두 Redis 명령을 연달아 보내는 것은 분산 원자성이 아닙니다.
- 보상은 과거를 지우는 Rollback이 아니라 반대 거래를 새로 실행하는 것입니다.
- 입금을 먼저 하면 중간 실패 때 존재하지 않는 돈을 사용자가 다시 쓸 수 있습니다.
- Coordinator 상태를 메모리에만 두면 재시작 후 Confirm과 Cancel 중 무엇을 해야 할지 모릅니다.
- 각 단계가 멱등하지 않으면 복구 재시도가 중복 차감이나 중복 입금을 만듭니다.

### 오늘의 한 문장

> 분산 지갑 이체의 원자성은 동시에 쓰는 데서 나오지 않고, 중간 상태를 기록하고 끝까지 Confirm하거나 Cancel하는 데서 나옵니다.

### 30초 확인 문제

TC/C에서 A 차감은 성공했고 B 입금 전 Coordinator가 죽었습니다. 복구한 Coordinator는 무엇을 근거로 어떤 작업을 해야 할까요?

### 정답과 해설

내구성 있는 이체 상태 표에서 Try 성공과 두 번째 단계 미완료를 확인해야 합니다. 전체 Try가 성공한 거래라면 같은 `transaction_id`로 B의 Confirm을 재시도합니다. 다른 참여자의 Try가 실패했다면 A의 Cancel을 재시도합니다. 참여자는 명령을 여러 번 받아도 한 번만 적용해야 합니다.

### 더 보기

![Day 37 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-37-wallet-transactions.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-37-wallet-transactions.md)
- [원문: Chapter 27, Digital Wallet](https://github.com/liquidslr/system-design-notes/tree/main/27.%20%20Digital%20Wallet)
