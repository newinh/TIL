# System Design Daily

## Day 10/40: 발송 요청과 실제 전송을 분리하라

### 오늘의 질문

알림 채널이 늘어도 발송 흐름을 어떻게 단순하게 유지할까요?

알림 시스템은 Push, SMS, Email처럼 서로 다른 외부 Provider를 호출합니다. 주문 완료 알림처럼 즉시 보내는 이벤트도 있고, 예약 발송도 있습니다. 모든 발송을 하나의 서버가 동기식으로 처리하면 한 Provider의 지연이 전체 요청을 막고 트래픽 폭주를 흡수하지 못합니다.

### WHY: 알림은 외부 시스템의 실패를 품는다

알림 요청을 받은 순간과 사용자가 실제 메시지를 받은 순간은 다릅니다. APNS, FCM, SMS, Email Provider는 각자 속도 제한과 장애 방식을 갖습니다. 네트워크 Timeout 뒤 실제 발송 여부를 알 수 없는 경우도 있습니다.

따라서 "API가 성공하면 알림이 정확히 한 번 도착한다"는 약속은 현실적이지 않습니다. 요청을 잃지 않고, 중복 가능성을 줄이고, 실패를 재처리하며, 사용자 설정을 지키는 것이 핵심입니다.

### WHAT: 공통 접수와 채널별 전송

알림 API는 Trigger Service가 채널의 세부 구현을 몰라도 되게 합니다.

```json
{
  "eventId": "order-123-shipped",
  "userId": "42",
  "templateId": "order-shipped-v3",
  "channels": ["push", "email"],
  "data": {"orderId": "123"},
  "scheduledAt": null
}
```

Notification Server는 요청을 인증하고 검증합니다. 사용자 연락처, Device Token, 언어, 수신 설정, Template을 조회한 뒤 채널별 이벤트를 Queue에 넣습니다.

```text
Trigger Services
  -> Notification API
  -> validation and preference check
  -> notification log
  -> channel queues
  -> channel workers
  -> APNS / FCM / SMS / Email providers
```

Queue는 순간 트래픽을 흡수하고 채널 간 장애를 분리합니다. SMS Provider가 느려져도 Push Worker는 계속 처리할 수 있습니다. Worker 수도 Queue Lag에 맞춰 독립적으로 늘립니다.

### HOW: 신뢰성은 상태와 재처리에서 나온다

먼저 Notification Log에 이벤트와 상태를 저장합니다.

```text
accepted -> queued -> provider_accepted -> delivered/failed
```

Provider가 최종 Delivered 상태를 주지 않는 채널도 있으므로 `provider_accepted`와 사용자 도착을 같은 의미로 쓰면 안 됩니다. 채널별로 확인 가능한 상태를 정의합니다.

재시도는 지수 Backoff와 최대 횟수를 사용합니다. 영구 실패인 잘못된 전화번호와 일시 실패인 `503`을 구분합니다. 최대 재시도를 넘긴 이벤트는 Dead Letter Queue로 보내 원인과 재처리 여부를 확인합니다.

중복을 줄이려면 `eventId`를 Idempotency Key로 씁니다. 같은 이벤트가 다시 들어오면 이미 만든 알림을 반환하거나 버립니다. 다만 확인 직후 장애가 발생할 수 있어 외부 Provider까지 완벽한 Exactly Once를 보장하기는 어렵습니다. 메시지 내용과 사용자 경험도 중복에 안전하게 설계해야 합니다.

### 사용자 설정과 Rate Limit

사용자는 채널과 알림 종류별로 수신을 끌 수 있어야 합니다. 결제 보안 알림과 마케팅 Email은 같은 설정으로 묶으면 안 됩니다. 발송 직전에 최신 설정을 다시 확인하면 Queue 대기 중 사용자가 Opt-out한 경우를 반영할 수 있습니다.

사용자당 빈도 제한도 필요합니다. 시스템이 만든 이벤트가 정상이어도 짧은 시간에 알림 100개가 도착하면 제품 실패입니다. 같은 주문 상태 변경을 묶거나, 낮은 우선순위 알림을 Digest로 보내거나, 조용한 시간대를 적용할 수 있습니다.

### 실무 예와 트레이드오프

배송 상태가 짧은 시간에 여러 번 바뀌면 모든 중간 상태를 Push로 보낼 필요가 없습니다. 최신 상태만 남기는 Coalescing이 사용자 경험과 비용을 개선합니다. 반면 보안 로그인 알림은 지연과 누락 위험을 낮추기 위해 별도 우선순위 Queue와 강한 재시도 정책을 둘 수 있습니다.

Template을 중앙 관리하면 문구와 다국어 처리가 일관되지만 잘못된 Template 배포가 대량 발송에 영향을 줍니다. 버전, 미리보기, 변수 검증, 단계적 배포가 필요합니다.

### 실패 모드

Queue에 넣기 전에 API가 성공 응답을 보내면 프로세스 장애 때 알림을 잃습니다. 데이터베이스 기록과 Queue 발행 사이의 이중 쓰기도 문제입니다. Outbox Pattern이나 내구성 있는 접수 Log로 두 상태를 연결해야 합니다.

Provider Timeout 뒤 무조건 재시도하면 이미 발송된 메시지가 중복될 수 있습니다. Provider의 Idempotency 기능을 쓰고, 결과 조회가 가능하면 먼저 확인합니다. 그렇지 않으면 중복을 허용한 At Least Once로 계약을 명확히 합니다.

만료된 Device Token을 계속 재시도하면 Queue와 비용을 낭비합니다. Provider 응답을 받아 Token을 비활성화하고 재등록 흐름을 둡니다.

### 함정 체크

- 외부 Provider 호출을 사용자 요청 안에서 동기식으로 끝내려 하지 않습니다.
- API 접수 성공과 사용자 도착을 같은 상태로 표시하지 않습니다.
- 모든 실패를 같은 간격으로 무한 재시도하지 않습니다.
- 중복 제거 확인과 상태 저장을 분리해 경쟁 조건을 만들지 않습니다.
- Queue에 들어간 뒤 바뀐 Opt-out 설정을 무시하지 않습니다.
- 채널별 Queue Lag, Provider 오류율, 재시도 수, Dead Letter Queue를 모니터링합니다.

### 오늘의 한 문장

> 알림 시스템은 메시지를 보내는 코드가 아니라, 발송 요청과 외부 Provider의 실패를 분리해 끝까지 추적하는 파이프라인입니다.

### 30초 확인 문제

SMS Provider 호출이 Timeout돼 Worker가 성공 여부를 모릅니다. 즉시 다시 보내면 어떤 문제가 생기며, 어떻게 대응해야 할까요?

### 정답과 해설

Provider가 실제로는 메시지를 접수했지만 응답만 유실됐을 수 있습니다. 즉시 재시도하면 같은 SMS가 두 번 갈 수 있습니다.

Provider가 Idempotency Key를 지원하면 같은 `eventId`로 재시도합니다. 발송 상태 조회 API가 있다면 먼저 확인합니다. 둘 다 없다면 지수 Backoff로 재시도하되 At Least Once 특성과 중복 가능성을 계약과 지표에 명시합니다. 결제 안내처럼 중복 피해가 큰 메시지는 내용에 고유 거래 번호를 넣어 사용자가 같은 사건임을 알게 합니다.

### 더 보기

![Day 10 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-10-notification-system.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-10-notification-system.md)
- [원문: Chapter 10, Design a Notification System](https://github.com/liquidslr/system-design-notes/tree/main/10.%20Notification%20System)
