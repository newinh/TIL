# System Design Daily

## Day 30/40: 받은편지함 도착은 전송 성공보다 어렵다

### 오늘의 질문

상대 SMTP 서버가 `250 OK`를 반환했는데도 메일이 스팸함에 들어갔다면, 우리 시스템은 성공한 걸까요?

### 전달 성공에는 단계가 있다

이메일 전송은 단순한 네트워크 요청이 아닙니다. 최소 세 단계를 구분해야 합니다.

- **Accepted**: 우리 서비스가 발신 요청을 접수했습니다.
- **Delivered**: 상대 SMTP 서버가 메시지를 받았습니다.
- **Inbox placement**: 상대 서비스가 받은편지함에 배치했습니다.

SMTP 성공 응답은 보통 두 번째 단계까지만 뜻합니다. 스팸함 분류, 이후 bounce, 사용자의 complaint는 별도 신호입니다. 제품 상태와 운영 지표에서 이 단계를 하나의 "sent"로 뭉치면 전달률 문제를 찾기 어렵습니다.

### 평판은 공유 자원이다

수신 사업자는 발신 IP와 domain의 과거 행동을 보고 신뢰도를 판단합니다. 새 IP에서 갑자기 대량 전송하면 정상 메일도 스팸으로 분류될 수 있습니다.

- 전용 IP를 사용해 다른 발신자의 평판과 분리합니다.
- 새 IP는 2주에서 6주 동안 전송량을 서서히 늘립니다.
- 거래 메일과 마케팅 메일을 IP pool과 domain으로 분리합니다.
- bounce와 complaint가 높은 계정을 빨리 제한합니다.
- ISP feedback loop를 받아 신고한 수신자에게 다시 보내지 않습니다.

격리는 비용을 늘립니다. 모든 고객에게 전용 IP를 주는 대신 트래픽과 위험 수준에 따라 shared pool, high-reputation pool, dedicated pool을 나눌 수 있습니다.

### 인증은 누가 보냈는지 증명한다

SPF는 어떤 서버가 domain을 대신해 보낼 수 있는지 DNS에 선언합니다. DKIM은 발신 domain의 개인 키로 메일에 서명해 변조 여부와 발신 권한을 검증하게 합니다. DMARC는 SPF와 DKIM 결과가 header의 From domain과 맞는지 확인하고 실패 메일 처리 정책을 알립니다.

인증이 통과해도 내용과 평판이 나쁘면 스팸함에 갈 수 있습니다. 반대로 인증이 없으면 정상 메일도 피싱으로 의심받습니다. 인증은 충분조건이 아니라 기본 조건입니다.

### 스팸 방어는 수신과 발신 양쪽에 있다

수신 경로는 악성 URL, 첨부, 대량 발신 패턴, 위조 header를 검사합니다. 검사 결과에 따라 거부, 격리, 스팸함 배치를 선택합니다. 모든 검사를 SMTP 연결 안에서 끝내면 지연이 커지므로 빠른 정책과 무거운 분석의 경계를 나눕니다.

발신 경로도 계정 탈취와 스팸 발송을 막아야 합니다. 사용자별 rate limit, 새 수신자 비율, bounce 급증, 비정상 로그인 같은 신호를 risk engine에 넣습니다. 한 계정의 남용이 shared IP 전체 평판을 떨어뜨릴 수 있기 때문입니다.

### 검색은 사용자별 write-heavy index다

이메일 검색은 웹 검색과 다릅니다. 범위는 한 사용자의 mailbox이고, 새 메일과 read 상태, folder 변경이 빠르게 반영되어야 합니다. 검색 횟수보다 메일 변경에 따른 index write가 더 많을 수 있습니다.

Elasticsearch 같은 검색 cluster를 쓰면 full-text와 from, to, subject, unread 필터를 빠르게 지원할 수 있습니다. `user_id`를 routing key로 사용하면 사용자별 검색 범위를 줄일 수 있습니다.

```text
metadata change -> Kafka -> indexer -> search store
search request -> search service -> user shard
```

색인은 파생 데이터입니다. 색인 consumer가 실패해도 메일 원본은 metadata store에 남아야 합니다. offset과 index write를 멱등하게 처리하고, 사용자나 시간 구간 단위로 reindex할 수 있어야 합니다.

### 검색 저장소의 트레이드오프

기성 검색 엔진은 개발 시간을 줄이고 풍부한 질의를 제공합니다. 대신 metadata store와 search store 두 곳을 운영하고 일시적 불일치를 감수합니다.

직접 만든 index는 이메일의 사용자별 쓰기 패턴에 맞출 수 있습니다. LSM tree처럼 메모리에 모은 변경을 순차적으로 disk에 flush하고 나중에 merge하면 random write를 줄일 수 있습니다. 하지만 compaction, 삭제, ranking, tokenizer, 장애 복구를 모두 책임져야 합니다. 특별한 규모와 요구가 증명되지 않았다면 기성 검색 엔진이 현실적입니다.

### 일관성과 가용성 선택

Mailbox metadata는 유실되면 안 됩니다. 네트워크 partition에서 오래된 replica가 쓰기를 받아 충돌시키는 것보다 일부 사용자의 수정 요청을 잠시 막는 선택을 할 수 있습니다. 반면 검색 색인은 eventual consistency를 허용할 수 있습니다.

같은 이메일 시스템 안에서도 데이터마다 선택이 다릅니다.

- 메일 원본과 folder 상태는 강한 내구성과 일관성이 우선입니다.
- 검색 색인과 cache는 재생성할 수 있어 가용성과 지연을 우선할 수 있습니다.
- 전달률 통계는 약간 늦어도 되지만 누락된 feedback은 평판을 해칩니다.

### 실패 모드와 관측

- 한 마케팅 고객의 complaint 급증이 shared IP 평판을 망칩니다.
- DKIM key 교체 중 DNS와 signer 배포 순서가 어긋나 인증이 실패합니다.
- 검색 index lag가 길어져 사용자가 방금 받은 메일을 찾지 못합니다.
- 삭제 이벤트가 indexer에서 누락돼 검색 결과에 삭제된 메일이 남습니다.
- hard bounce 주소에 계속 재시도해 평판과 비용을 동시에 잃습니다.

운영자는 domain별 delivery rate, bounce code, complaint rate, spam placement 추정, queue age, index lag를 함께 봐야 합니다.

### 함정 체크

- SMTP `250 OK`를 받은편지함 도착으로 해석하면 안 됩니다.
- SPF, DKIM, DMARC를 적용했다고 스팸 분류가 사라지는 것은 아닙니다.
- 마케팅과 비밀번호 재설정 메일을 같은 평판 pool에서 보내면 핵심 메일까지 영향을 받습니다.
- 검색 색인을 원본으로 취급하면 index 장애나 rebuild 때 메일을 잃습니다.

### 오늘의 한 문장

**이메일 품질은 SMTP 연결보다 발신 평판, 인증, 남용 통제, 재생성 가능한 검색 색인에서 결정됩니다.**

### 30초 확인 문제

마케팅 캠페인 이후 complaint rate가 급증했고 비밀번호 재설정 메일까지 스팸함에 들어갑니다. 가장 먼저 바꿔야 할 구조는 무엇일까요?

### 정답과 해설

거래 메일과 마케팅 메일의 발신 domain과 IP pool을 분리해 평판 장애를 격리해야 합니다. 동시에 캠페인 발신을 줄이고 complaint feedback을 반영해 신고 수신자를 차단하며, SPF, DKIM, DMARC 정합성을 확인합니다. 전송 재시도만 늘리면 complaint와 평판 손상이 더 커집니다.

### 더 보기

![Day 30 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-30-distributed-email-deliverability.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-30-distributed-email-deliverability.md)
- [원문: Chapter 23: Distributed Email Service](https://github.com/liquidslr/system-design-notes/tree/main/23.%20Distributed%20Email%20Service)
