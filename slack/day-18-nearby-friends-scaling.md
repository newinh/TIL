# System Design Daily

## Day 18/40: 실시간 위치 확장은 구독 이동 비용과 싸운다

### 오늘의 질문

- Redis Pub/Sub 서버를 추가하면 처리량은 늘어나는데 왜 위치 업데이트를 놓칠 수 있을까요?

### WHY: 병목은 메모리보다 팬아웃 CPU다

주변 친구 기능에서 한 사용자는 평균 400명의 친구를 가질 수 있고, 그중 10%가 동시에 온라인이라고 가정합니다. 초당 약 33만 위치 업데이트가 들어오면 친구에게 전달할 이벤트는 초당 1천만 건을 훌쩍 넘습니다. 채널 메타데이터는 몇 대 서버의 메모리에 들어가도 publish와 subscriber 전달 CPU는 훨씬 많은 노드를 요구합니다.

Redis Pub/Sub은 메시지를 보관하지 않습니다. 구독자가 잠시 끊기거나 채널이 다른 노드로 이동하는 동안 발생한 이벤트는 다시 읽을 수 없습니다. 확장은 단순한 노드 추가가 아니라 채널 소유권과 구독을 안전하게 옮기는 작업입니다.

### WHAT: 분산 채널과 Service Discovery

사용자 채널을 여러 Redis 노드에 나눕니다. WebSocket Server는 어떤 채널이 어느 노드에 있는지 알아야 합니다. Service Discovery에 채널 분배 정보를 두고, 각 WebSocket Server는 이를 메모리에 캐시합니다.

```text
channel(user_id) -> hash(user_id) -> Redis node
WebSocket Server
  -> hash ring 조회
  -> 해당 Redis node 구독
```

노드를 추가하거나 제거할 때 단순 나머지 연산을 쓰면 거의 모든 채널이 이동합니다. Consistent Hashing을 사용하면 일부 채널만 새 노드로 이동합니다. 가상 노드를 충분히 두어 부하 분포를 고르게 하고, 실제 publish 처리량을 기준으로 불균형을 확인합니다.

### HOW: 구독 이전을 운영 절차로 만든다

채널 이동에는 세 단계가 필요합니다.

1. 새 노드를 준비하고 health check를 통과시킵니다.
2. Hash ring의 다음 버전을 배포하고 WebSocket Server가 새 채널을 구독하게 합니다.
3. 새 구독이 확인된 뒤 이전 구독과 기존 노드를 제거합니다.

이전 구독을 먼저 끊으면 공백 동안 이벤트를 잃습니다. 잠시 양쪽을 구독하면 중복 이벤트가 올 수 있으므로 위치 이벤트에 `user_id`, `timestamp`, `sequence`를 넣고 최신값 기준으로 중복을 제거합니다. 이 시스템은 일부 위치 유실을 허용하지만, 재구독 실패율과 공백 시간은 측정해야 합니다.

### WebSocket Server도 상태를 가진다

WebSocket Server를 종료할 때 바로 프로세스를 죽이면 연결과 구독이 한꺼번에 끊깁니다. Load Balancer에서 `draining` 상태로 바꾸고 새 연결을 보내지 않은 뒤, 기존 클라이언트가 다른 서버로 재연결하도록 시간을 줍니다.

클라이언트가 새 서버에 붙으면 친구 목록을 다시 읽고 채널을 재구독하며 최신 위치 캐시로 화면을 채웁니다. Pub/Sub 이벤트를 놓쳐도 최신값 캐시가 복구 기준이 됩니다. Pub/Sub은 변화 전달 최적화이고 Redis Location Cache가 현재 상태의 기준입니다.

### 용량 계획과 핫 사용자

평균 친구 수만 보고 서버 수를 정하면 친구가 수천 명인 사용자에게 부하가 몰릴 수 있습니다. 한 WebSocket Server가 담당하는 총 연결 수뿐 아니라 구독 수, 초당 수신 이벤트 수, 거리 계산량을 함께 제한합니다.

트래픽 패턴이 예측 가능하면 낮은 시간대에 증설하고 여유 용량을 둡니다. 자동 확장만 믿으면 채널 재배치 자체가 부하를 키워 연쇄 장애를 만들 수 있습니다.

### 함정 체크

- Redis Pub/Sub을 durable queue처럼 취급하면 재구독 중 누락을 복구할 수 없습니다.
- 노드 수만 늘리고 채널 위치 정보를 공유하지 않으면 WebSocket Server가 구독 대상을 찾지 못합니다.
- 기존 구독을 먼저 끊으면 채널 이동 중 이벤트 공백이 생깁니다.
- 연결 수만 기준으로 WebSocket Server를 분배하면 구독이 많은 사용자가 CPU 핫스팟을 만듭니다.

### 오늘의 한 문장

> 실시간 Pub/Sub 확장의 어려움은 노드를 추가하는 데 있지 않고 채널 소유권과 구독을 빈틈 없이 옮기는 데 있습니다.

### 30초 확인 문제

Redis 노드를 추가한 직후 일부 사용자가 위치를 두 번 받고 일부는 한 번도 받지 못했습니다. 어떤 전환 방식이 필요할까요?

### 정답과 해설

새 구독을 먼저 만들고 확인한 뒤 기존 구독을 제거하는 겹침 전환이 필요합니다. 겹치는 동안 중복 이벤트가 올 수 있으므로 사용자별 sequence나 timestamp로 최신 이벤트 하나만 적용합니다. 누락된 현재 상태는 Location Cache에서 다시 읽어 복구합니다. Pub/Sub 자체에는 재생 기능이 없으므로 캐시 기반 초기화가 반드시 남아야 합니다.

### 더 보기

![Day 18 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-18-nearby-friends-scaling.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-18-nearby-friends-scaling.md)
- [원문: Chapter 17: Nearby Friends](https://github.com/liquidslr/system-design-notes/tree/main/17.%20Nearby%20Friends)
