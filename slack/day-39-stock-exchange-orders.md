# System Design Daily

## Day 39/40: 거래소는 주문을 검증한 뒤 한 줄로 세운다

### 오늘의 질문

- 같은 가격의 매수 주문 두 개가 거의 동시에 들어왔을 때, 어떤 주문이 먼저 체결될지 모든 서버가 같은 답을 내게 하려면 무엇이 필요할까요?

### 거래소의 Critical Path

거래소는 매수자와 매도자의 주문을 빠르고 정확하게 연결합니다. 주문 접수부터 체결 결과 반환까지가 Critical Path입니다. 시장 데이터 집계와 리포팅도 중요하지만 같은 지연 목표를 적용하면 체결 경로가 불필요하게 느려집니다.

```text
Broker
-> Client Gateway
-> Order Manager
-> Sequencer
-> Matching Engine
-> Sequencer
-> Order Manager
-> Broker
```

Client Gateway는 인증, Rate Limit, 프로토콜 검증을 합니다. Order Manager는 위험 한도와 사용 가능 자금을 확인하고 주문 상태를 관리합니다. Sequencer는 주문에 순서를 부여합니다. Matching Engine은 그 순서대로 Order Book을 바꾸고 Fill을 만듭니다.

### 주문 접수 전에 확인할 것

Limit Order에는 종목, 매수 또는 매도, 가격, 수량이 필요합니다. 가격과 수량은 부동소수점보다 최소 Tick과 최소 수량 단위의 정수로 표현합니다.

```json
{
  "client_order_id": "broker7-9912",
  "symbol": "ACME",
  "side": "BUY",
  "price_ticks": 10250,
  "quantity": 300,
  "type": "LIMIT"
}
```

Order Manager는 다음을 확인합니다.

- 중복 `client_order_id`인지 확인합니다.
- 사용자와 Broker의 거래 권한을 확인합니다.
- 일일 수량과 금액 한도를 검사합니다.
- 매수 자금이나 매도 가능 수량을 예약합니다.
- 거래 시간과 종목 상태를 확인합니다.

자금은 주문이 열려 있는 동안 예약해야 합니다. 잔액 확인만 하고 예약하지 않으면 같은 돈으로 여러 주문을 넣을 수 있습니다. 부분 체결과 취소 때 예약 금액도 비례해 해제해야 합니다.

### Sequencer가 공정한 입력 순서를 만든다

두 Gateway의 로컬 시계로 순서를 정하면 Clock Skew와 네트워크 지연 때문에 서버마다 판단이 달라질 수 있습니다. Sequencer는 Matching Engine에 들어가는 모든 명령에 단조 증가 Sequence를 부여합니다.

```text
10421 NEW order-A
10422 NEW order-B
10423 CANCEL order-A
```

Matching Engine은 다음 Sequence만 받습니다. 번호가 빠지거나 역순이면 처리하지 않고 누락분을 복구합니다. 이 입력 로그는 장애 뒤 같은 Order Book을 재생하는 근거가 됩니다.

### Order Book 자료구조

Order Book은 종목별 Buy와 Sell Price Level을 가집니다. Buy는 가장 높은 가격이, Sell은 가장 낮은 가격이 우선입니다. 같은 가격에서는 먼저 들어온 주문을 먼저 체결하는 Price-Time Priority를 사용합니다.

```text
OrderBook
- buy price levels, high to low
- sell price levels, low to high
- order_id -> order node
```

각 Price Level의 주문을 Doubly Linked List로 두면 새 주문을 Tail에 `O(1)`로 추가하고, 체결할 주문을 Head에서 `O(1)`로 제거할 수 있습니다. `order_id`에서 List Node로 가는 Hash Map을 두면 취소도 `O(1)`에 가깝게 처리합니다.

Matching Engine은 반대편 최우선 가격부터 수량을 소진합니다. 한 주문이 여러 상대 주문과 부분 체결될 수 있으며 각 체결마다 매수와 매도 양쪽 Fill을 만듭니다.

### 단일 Writer가 Lock을 없앤다

Matching Engine을 여러 Thread가 같은 Order Book에 쓰게 하면 Lock과 경합, 비결정적 순서가 생깁니다. 종목이나 Partition마다 Single Writer Event Loop를 두면 한 Core에서 명령을 순서대로 처리할 수 있습니다. Context Switch와 Lock을 줄여 꼬리 지연도 안정됩니다.

Scale-out은 Order Book 하나를 여러 Writer가 공유하는 방식이 아니라 종목 집합을 다른 Engine Partition에 배치하는 방식으로 합니다. 인기 종목 하나는 단일 Partition의 한계를 만들 수 있으므로 종목별 최대 부하를 기준으로 용량을 잡아야 합니다.

### 체결 밖의 흐름을 분리한다

Matching Engine의 Fill Stream을 Market Data Publisher와 Reporter가 소비합니다. Publisher는 Order Book과 Candlestick을 만들고, Reporter는 거래 내역과 규제 보고를 저장합니다. 이 작업이 느려져도 체결을 막지 않게 별도 경로와 Buffer를 둡니다. 단, 결과 누락은 허용하지 않으므로 Sequence 기반 재생이 가능해야 합니다.

### 함정 체크

- Client Gateway에 위험 계산과 데이터베이스 조회를 과도하게 넣으면 Critical Path가 길어집니다.
- 잔액 확인만 하고 자금을 예약하지 않으면 같은 돈으로 여러 주문을 만들 수 있습니다.
- 서버 Timestamp로 주문 순서를 정하면 Clock Skew 때문에 공정성을 증명하기 어렵습니다.
- Order Book을 여러 Thread가 Lock으로 공유하면 평균 지연보다 꼬리 지연이 크게 흔들립니다.
- 취소가 도착했을 때 이미 전량 체결된 주문일 수 있습니다. 상태 전이를 명확히 해야 합니다.
- 리포팅 실패를 체결 실패로 연결하면 비핵심 경로가 시장을 멈춥니다.

### 오늘의 한 문장

> 거래소의 Matching Engine은 빠른 검색기보다, 검증된 주문을 하나의 순서로 적용하는 결정적 상태 머신입니다.

### 30초 확인 문제

같은 가격의 매수 주문 A와 B가 서로 다른 Gateway에서 동시에 들어왔습니다. Gateway의 수신 Timestamp로 우선순위를 정해도 될까요?

### 정답과 해설

안 됩니다. Gateway 시계와 네트워크 경로가 달라 Timestamp를 공정하게 비교할 수 없습니다. 중앙 Sequencer나 종목 Partition의 단일 순서 부여자가 A와 B에 Sequence를 매기고 Matching Engine은 그 순서를 따라야 합니다. 같은 입력 로그를 재생하면 같은 체결 결과가 나와야 합니다.

### 더 보기

![Day 39 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-39-stock-exchange-orders.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-39-stock-exchange-orders.md)
- [원문: Chapter 28, Stock Exchange](https://github.com/liquidslr/system-design-notes/tree/main/28.%20Stock%20Exchange)
