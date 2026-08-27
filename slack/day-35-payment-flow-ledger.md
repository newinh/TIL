# System Design Daily

## Day 35/40: 결제의 진실은 상태가 아니라 원장에 남는다

### 오늘의 질문

- PSP 결제는 성공했는데 판매자 지갑 갱신이 실패했다면, 돈이 실제로 어디에 있는지 무엇으로 증명할 수 있을까요?

### 결제는 한 번의 API 호출이 아니다

결제 화면에서 사용자는 버튼 한 번을 누르지만 내부에서는 여러 시스템이 움직입니다. 결제 서비스가 주문을 만들고, Payment Executor가 PSP를 호출하고, PSP는 카드 네트워크와 은행을 거칩니다. 성공 뒤에는 판매자 Wallet과 회계 Ledger도 갱신해야 합니다.

```text
구매자 -> 결제 서비스 -> Payment Executor -> PSP -> 카드 네트워크
                      -> Ledger
                      -> Seller Wallet
```

이 호출을 하나의 분산 트랜잭션으로 묶을 수 없습니다. PSP는 외부 시스템이고 응답과 Webhook이 지연되거나 중복될 수 있습니다. 따라서 결제를 상태 머신과 불변 금융 기록으로 설계해야 합니다.

### Payment Event와 Payment Order

Checkout 하나에 판매자가 여러 명이면 결제 이벤트 하나가 여러 Payment Order를 가질 수 있습니다.

```text
payment_event
- checkout_id
- buyer_id
- status

payment_order
- payment_order_id
- checkout_id
- seller_account
- amount_minor
- currency
- status
- psp_reference
- ledger_status
- wallet_status
```

금액은 `double`로 저장하지 않습니다. `10.1 - 10.0` 같은 연산이 정확한 0.1이 아닐 수 있습니다. 통화의 최소 단위 정수인 `amount_minor`와 `currency`를 함께 저장하거나 정확한 Decimal 타입을 사용합니다.

카드 번호도 직접 저장하지 않습니다. PSP가 제공하는 Hosted Payment Page나 Token을 사용해 PCI 범위와 유출 위험을 줄입니다.

### 결제 상태 머신

```text
NOT_STARTED -> EXECUTING -> SUCCEEDED
                         -> FAILED
                         -> PENDING
```

`PENDING`은 실패가 아닙니다. 3D Secure, 위험 검토, 은행 지연으로 몇 시간 뒤에 끝날 수 있습니다. 사용자에게는 처리 중 상태와 조회 방법을 제공하고, PSP Webhook을 기다리거나 필요할 때 조회 API를 Polling합니다.

상태 전이는 단방향으로 제한하고 원인, PSP 응답 코드, 시각을 기록합니다. 단순 Boolean `is_paid`만 두면 승인, 정산, 취소, 환불, Wallet 반영 중 어디까지 끝났는지 알 수 없습니다.

### 이중부기 원장

원장은 돈의 이동을 두 계정에 같은 금액으로 기록합니다.

| Account | Debit | Credit |
|---|---:|---:|
| PSP clearing | 1000 | 0 |
| Seller payable | 0 | 1000 |

한 거래의 Debit 합과 Credit 합은 같아야 합니다. 이 불변식 덕분에 한쪽 기록이 빠진 오류를 찾고, 언제 어떤 거래가 잔액을 바꿨는지 추적할 수 있습니다.

Wallet의 잔액은 빠른 조회를 위한 현재 상태입니다. Ledger는 금융 사건의 근거입니다. Wallet 업데이트가 실패해도 원장 항목과 PSP 결과가 있으면 다시 반영할 수 있습니다. 반대로 Wallet 숫자만 고쳐 놓고 원장 기록이 없다면 왜 잔액이 바뀌었는지 증명하기 어렵습니다.

원장 행은 수정하거나 삭제하기보다 반대 방향의 보정 거래를 추가합니다. 감사 기록을 보존하면서 현재 잔액을 바로잡을 수 있습니다.

### 동기 경로를 짧게, 후속 처리는 비동기로

사용자 응답 전에 모든 내부 서비스를 동기로 호출하면 하나의 장애가 전체 Checkout을 멈춥니다. PSP 결과를 내구성 있게 기록한 뒤 Ledger와 Wallet 반영 이벤트를 발행하면 각 서비스가 독립적으로 재시도할 수 있습니다.

비동기 전환은 공짜가 아닙니다. 잠시 상태가 다를 수 있으므로 각 단계의 처리 상태, 중복 제거 키, 재시도 정책, Dead Letter Queue가 필요합니다. 사용자에게 성공을 보여 주는 시점도 PSP 승인인지 내부 원장 반영 완료인지 계약으로 정해야 합니다.

### Reconciliation이 마지막 안전망이다

매일 PSP 정산 파일과 내부 Payment Order, Ledger, Wallet을 대조합니다.

- PSP 성공, 내부 결제 실패
- 내부 성공, PSP 기록 없음
- Ledger는 있음, Wallet 반영 없음
- 금액이나 통화 불일치

Reconciliation은 오류를 자동으로 숨기는 작업이 아닙니다. 알려진 유형은 표준 보정 절차로 처리하고, 분류할 수 없는 차이는 재무팀이 조사할 수 있도록 원본 근거와 연결합니다.

### 함정 체크

- 낮은 TPS라고 결제가 쉬운 것은 아닙니다. 처리량보다 정확성과 추적성이 먼저입니다.
- Wallet 잔액을 원장 대신 진실의 원천으로 쓰면 보정 근거를 잃습니다.
- `double`로 금액을 저장하면 반올림 오차가 회계 불일치가 됩니다.
- PSP Redirect만 믿으면 사용자가 브라우저를 닫았을 때 결과를 놓칩니다. 서버 Webhook이나 조회가 필요합니다.
- Ledger와 Wallet을 동기 호출 하나로 묶으면 외부 지연이 사용자 요청에 전파됩니다.

### 오늘의 한 문장

> 결제 상태는 지금 어디까지 처리됐는지 말하고, 원장은 돈이 왜 움직였는지 증명합니다.

### 30초 확인 문제

PSP는 결제 성공을 알렸고 Ledger 기록도 생성됐지만 Seller Wallet 갱신이 실패했습니다. 결제 전체를 PSP에 다시 요청해야 할까요?

### 정답과 해설

PSP 결제를 다시 요청하면 안 됩니다. 외부 돈 이동은 이미 성공했습니다. `payment_order_id`로 PSP 결과와 Ledger 항목을 확인하고, 실패한 Wallet 반영만 멱등하게 재시도합니다. Reconciliation은 Ledger와 Wallet의 차이를 찾아 누락된 반영을 복구합니다.

### 더 보기

![Day 35 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-35-payment-flow-ledger.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-35-payment-flow-ledger.md)
- [원문: Chapter 26, Payment System](https://github.com/liquidslr/system-design-notes/tree/main/26.%20Payment%20System)
