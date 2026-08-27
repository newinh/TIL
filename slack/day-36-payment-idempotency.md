# System Design Daily

## Day 36/40: 결제 재시도는 같은 결과로 끝나야 한다

### 오늘의 질문

- 카드 승인은 성공했지만 네트워크가 끊겨 사용자가 다시 결제 버튼을 눌렀습니다. 재시도는 어떻게 중복 청구가 아니라 같은 결제 조회가 될까요?

### Exactly-once는 전달 방식이 아니라 처리 결과다

네트워크에서는 요청과 응답이 사라질 수 있습니다. 결제 서버가 요청을 받지 못했는지, PSP 승인은 끝났지만 응답만 잃었는지 클라이언트는 구분할 수 없습니다. 재시도를 막으면 성공한 줄 모르는 결제가 남고, 무조건 재시도하면 중복 청구가 생깁니다.

실무에서 Exactly-once 처리는 두 성질을 조합합니다.

- **At-least-once**: 실패한 요청을 재시도해 결국 처리합니다.
- **At-most-once**: 같은 비즈니스 작업은 한 번만 실행합니다.

첫 번째는 Retry가, 두 번째는 Idempotency가 담당합니다.

### Idempotency Key의 계약

클라이언트는 결제 시도 하나에 UUID를 만들고 모든 재시도에 같은 키를 보냅니다.

```http
POST /v1/payments
Idempotency-Key: 6f53e0b4-31a1-4d24-a21d-f40a6f7d56e2
```

서버는 키를 Payment Order의 고유 제약으로 저장합니다.

```sql
INSERT INTO payment_order(idempotency_key, request_hash, status)
VALUES (?, ?, 'EXECUTING');
```

처음 요청이면 새 Order를 만들고 실행합니다. 같은 키가 다시 오면 기존 Order의 상태와 응답을 반환합니다. 동시에 같은 키가 도착해도 데이터베이스 Unique Constraint가 하나만 생성되게 해야 합니다. 애플리케이션의 사전 조회만으로는 두 요청이 모두 없다고 판단하는 경쟁 조건을 막지 못합니다.

키만 같고 금액이나 판매자가 다르면 거부해야 합니다. `request_hash`를 저장해 원래 요청과 재시도가 같은 의미인지 확인합니다. 그렇지 않으면 오래된 키를 잘못 재사용한 요청이 엉뚱한 결제 결과를 받을 수 있습니다.

### PSP까지 같은 키를 전달한다

내부에서 중복 Order 생성을 막아도 PSP 호출 직후 장애가 나면 다시 호출할 수 있습니다. PSP가 지원하는 Idempotency Key나 Merchant Reference에 내부 `payment_order_id`를 전달해야 합니다.

```text
Client key -> Payment Order ID -> PSP idempotency key
```

이 연결이 끊기면 내부에는 한 Order만 있어도 PSP에서 두 번 승인될 수 있습니다. PSP가 멱등성을 지원하지 않으면 상태 조회 API로 기존 승인 여부를 확인하고, 불확실한 거래를 자동 재실행하지 않는 보수적인 정책이 필요합니다.

### Retry는 오류 종류를 구분한다

모든 실패를 재시도하면 안 됩니다.

- Timeout, 429, 일시적 5xx는 지수 Backoff와 Jitter로 재시도합니다.
- 잘못된 카드 정보, 잔액 부족 같은 최종 오류는 재시도하지 않습니다.
- 서버가 `Retry-After`를 주면 그 시간을 따릅니다.
- 최대 횟수를 넘긴 작업은 Dead Letter Queue와 운영 조사로 보냅니다.

즉시 재시도는 장애 난 PSP에 더 많은 요청을 보내 복구를 늦춥니다. Jitter가 없으면 많은 Worker가 같은 시점에 다시 몰리는 Thundering Herd가 생깁니다.

### 지연 응답은 실패로 바꾸지 않는다

3D Secure나 수동 위험 검토는 몇 시간 걸릴 수 있습니다. API 연결을 계속 열어 둘 수 없으므로 `PENDING` 상태와 조회 URL을 반환합니다.

```http
HTTP/1.1 202 Accepted
Location: /v1/payments/pay_123

{"payment_id":"pay_123","status":"PENDING"}
```

PSP Webhook이 오면 상태를 갱신합니다. Webhook도 중복, 지연, 순서 변경이 발생합니다. PSP Event ID를 중복 제거하고 허용된 상태 전이만 적용해야 합니다. 예를 들어 이미 `SUCCEEDED`인 결제를 오래된 `PENDING` 이벤트가 되돌리면 안 됩니다.

Redirect 결과는 사용자 경험을 위한 신호일 뿐 금융 기록의 근거가 아닙니다. 브라우저 값은 조작되거나 유실될 수 있으므로 서버 간 Webhook과 PSP 조회 결과를 기준으로 확정합니다.

### Reconciliation은 불확실성을 닫는다

Retry와 Idempotency를 구현해도 외부와 내부 상태가 영원히 같다는 보장은 없습니다. 정산 파일을 기준으로 다음 차이를 찾습니다.

```text
PSP=SUCCEEDED, internal=PENDING
PSP=FAILED, internal=SUCCEEDED
PSP amount != internal amount
Ledger entry missing
```

자동으로 고칠 수 있는 유형은 멱등한 보정 작업을 실행하고, 나머지는 원본 요청과 PSP Reference, 상태 전이 기록을 묶어 조사합니다.

### 함정 체크

- Idempotency Key를 요청마다 새로 만들면 재시도 중복을 막지 못합니다.
- 사전 `SELECT` 뒤 `INSERT`만으로는 동시 요청 경쟁을 막을 수 없습니다.
- 같은 키에 다른 요청 본문을 허용하면 결제 결과가 잘못 연결됩니다.
- 내부 키를 PSP에 전달하지 않으면 외부 중복 승인을 막지 못합니다.
- Timeout을 실패로 확정하면 실제 성공한 결제를 다시 실행할 수 있습니다.
- Webhook 도착 순서를 신뢰하면 오래된 이벤트가 최신 상태를 덮습니다.

### 오늘의 한 문장

> 안전한 결제 재시도는 요청을 한 번만 보내는 것이 아니라, 여러 번 보내도 같은 Payment Order로 수렴하게 하는 것입니다.

### 30초 확인 문제

같은 Idempotency Key로 금액이 10,000원인 요청과 12,000원인 요청이 들어왔습니다. 두 번째 요청에 기존 결제 결과를 반환해도 될까요?

### 정답과 해설

안 됩니다. 키는 같지만 비즈니스 요청이 다릅니다. 서버는 최초 요청의 핵심 필드로 만든 `request_hash`를 비교하고 충돌 오류를 반환해야 합니다. 기존 결과를 반환하면 사용자는 12,000원을 요청했는데 10,000원 결제 결과를 성공으로 오해할 수 있습니다.

### 더 보기

![Day 36 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-36-payment-idempotency.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-36-payment-idempotency.md)
- [원문: Chapter 26, Payment System](https://github.com/liquidslr/system-design-notes/tree/main/26.%20Payment%20System)
