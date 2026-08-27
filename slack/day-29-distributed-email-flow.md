# System Design Daily

## Day 29/40: 이메일은 전송, 수신, 저장을 분리해야 버틴다

### 오늘의 질문

수신 서버가 30분 동안 응답하지 않아도 사용자의 "보내기" 요청을 빨리 끝내고, 메일을 잃지 않으려면 어떤 경계를 비동기로 나눠야 할까요?

### 이메일 한 통은 여러 시스템을 지난다

사용자가 보내기 버튼을 눌렀다고 메일이 상대 받은편지함에 즉시 저장되는 것은 아닙니다. 발신 요청 수락, 스팸 검사, 상대 도메인 조회, SMTP 전송, 수신 측 검사, 메타데이터 저장, 첨부 저장, 검색 색인, 실시간 알림이 차례로 이어집니다.

이 모든 일을 HTTP 요청 안에서 동기로 처리하면 상대 서버 한 곳의 장애가 web server thread와 사용자 요청을 붙잡습니다. 핵심은 메일을 안전하게 받아 queue에 기록한 시점과 실제 전달 완료 시점을 분리하는 것입니다.

### 발신 흐름

```text
Webmail
-> API Gateway
-> Web Server
-> Outgoing Queue
-> SMTP Worker
-> Recipient Mail Server
```

Web Server는 인증, 수신자 형식, 크기 제한, rate limit을 검사합니다. 첨부 파일은 먼저 object storage에 올리고 메시지에는 object key와 checksum만 넣습니다. 검증을 통과한 메일을 내구성 있는 outgoing queue에 기록한 뒤 사용자에게 "전송 접수"를 반환합니다.

SMTP worker는 DNS에서 수신 도메인의 MX record를 찾고, 스팸과 바이러스 검사를 거쳐 상대 서버로 보냅니다. 상대 서버가 일시적으로 실패하면 지수 backoff로 재시도합니다. 영구 실패라면 bounce 상태를 만들고 발신자에게 알려야 합니다.

같은 서비스 내부 사용자에게 보내는 메일은 외부 SMTP 왕복 없이 내부 수신 흐름으로 넘길 수 있습니다. 그래도 정책 검사와 감사 기록을 건너뛰면 안 됩니다.

### 수신 흐름

```text
Internet SMTP
-> SMTP Load Balancer
-> SMTP Server
-> Incoming Queue
-> Mail Processing Worker
-> Metadata Store and Attachment Store
-> Realtime Notification
```

SMTP Server는 수신 도메인과 수신자, 기본 정책을 확인한 뒤 메시지를 queue에 넣습니다. Processing worker가 MIME을 파싱하고 악성 첨부를 검사하며, 본문과 header를 metadata store에 기록합니다. 큰 첨부는 object storage에 둡니다.

저장 성공 전에 상대 SMTP 서버에 성공을 응답하면 이후 장애 때 메일을 잃을 수 있습니다. 반대로 모든 색인과 알림까지 끝난 뒤 응답하면 느린 부가 기능이 SMTP 연결을 막습니다. 최소한 재처리 가능한 내구성 경계까지 저장한 뒤 수락하고, 검색 색인과 실시간 알림은 비동기로 처리합니다.

### 저장소를 역할별로 나눈다

**Metadata Store**는 subject, from, to, body 위치, folder, read 상태, 생성 시각을 저장합니다. 대부분의 조회가 한 사용자 mailbox 안에서 일어나므로 `user_id`를 partition key로 쓰기 좋습니다.

**Attachment Store**는 크기가 큰 binary를 저장합니다. Object storage는 대용량 blob, 복제, 수명 주기에 알맞습니다. 같은 첨부를 여러 DB row에 복사하지 않고 immutable object로 참조합니다.

**Search Store**는 본문과 header의 inverted index를 가집니다. 메타데이터 저장과 검색 색인을 한 쓰기로 묶으려 하면 결합이 커집니다. queue를 통해 변경 이벤트를 전달하고, 색인이 늦을 때 UI가 이를 견디게 합니다.

**Cache**는 최근 mailbox 목록과 자주 여는 메일을 빠르게 보여 줍니다. 원본이 아니므로 cache 장애나 eviction이 메일 유실로 이어져서는 안 됩니다.

### 한 메일이 사용자마다 다른 상태를 가진다

같은 메시지를 여러 수신자가 받아도 folder와 read 상태는 사용자별입니다. 하나의 공유 메시지 본문과 사용자별 mailbox entry를 분리할 수 있습니다. 다만 `user_id` shard에 데이터를 함께 두는 단순한 모델은 한 메일을 여러 사용자와 공유하기 어렵습니다.

처음부터 전역 중복 제거를 넣으면 reference count, 삭제 정책, 보안 경계가 복잡해집니다. 저장 비용이 실제 병목인지 확인한 뒤 적용해야 합니다.

### Queue를 운영하는 기준

Outgoing queue가 커지는 이유는 두 가지입니다. 상대 서버가 느리거나 worker 처리량이 부족한 경우입니다. queue length만 보면 둘을 구분하기 어렵습니다.

- 목적지 도메인별 retry 수와 응답 코드
- 가장 오래된 미전송 메일의 나이
- SMTP worker 처리율과 실패율
- incoming queue에서 저장 완료까지의 지연
- bounce와 complaint 비율

특정 도메인이 느릴 때 전체 queue를 막지 않도록 목적지별 동시성 제한과 circuit breaker를 둘 수 있습니다.

### 함정 체크

- API가 200을 반환한 것을 최종 전달 성공으로 표현하면 안 됩니다.
- 첨부 binary를 queue message와 metadata DB에 반복 복사하면 전송량과 저장 비용이 커집니다.
- 검색 색인 실패를 메일 저장 실패와 같은 트랜잭션으로 처리하면 핵심 수신 경로가 취약해집니다.
- SMTP 수락 전에 재처리 가능한 저장을 하지 않으면 메일을 잃을 수 있습니다.

### 오늘의 한 문장

**분산 이메일은 메일을 안전하게 접수하는 경로와 전송, 저장, 색인, 알림 경로를 queue로 분리해야 버팁니다.**

### 30초 확인 문제

수신 메일의 metadata 저장은 성공했지만 검색 색인과 WebSocket 알림이 실패했습니다. SMTP 수신 전체를 실패시켜야 할까요?

### 정답과 해설

메일 원본과 재처리 이벤트가 내구성 있게 저장됐다면 SMTP 수신은 성공 처리할 수 있습니다. 검색 색인과 실시간 알림은 비동기 consumer가 재시도하게 합니다. 사용자는 새 메일 목록을 HTTP로 다시 조회할 수 있고, 색인은 잠시 늦을 수 있습니다. 핵심 저장과 파생 기능의 실패 경계를 분리해야 합니다.

### 더 보기

![Day 29 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-29-distributed-email-flow.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-29-distributed-email-flow.md)
- [원문: Chapter 23: Distributed Email Service](https://github.com/liquidslr/system-design-notes/tree/main/23.%20Distributed%20Email%20Service)
