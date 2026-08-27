# System Design Daily

## Day 27/40: 호텔은 객실 번호가 아니라 날짜별 객실 유형 재고를 판다

### 오늘의 질문

고객이 디럭스룸을 3박 예약할 때, 객실 1203호 한 행을 잡아야 할까요, 날짜별 디럭스룸 재고를 차감해야 할까요?

### 예약 대상부터 정확히 잡는다

호텔 고객은 보통 특정 객실 번호를 고르지 않습니다. 객실 유형과 숙박 기간을 고르고, 실제 객실 번호는 체크인 때 배정받습니다. 따라서 `room_id` 하나에 예약 상태를 두는 모델은 호텔 운영과 맞지 않습니다.

예약의 핵심 자원은 `(hotel_id, room_type_id, date)`별 판매 가능 수량입니다. 3박 예약은 하나의 객실을 잡는 연산이 아니라, 숙박하는 각 날짜의 재고를 동시에 확인하고 차감하는 연산입니다.

### 읽기와 쓰기의 성격이 다르다

호텔 상세, 사진, 편의 시설은 자주 읽히고 거의 바뀌지 않습니다. CDN과 cache에 오래 둘 수 있습니다. 객실 가격은 날짜와 판매율에 따라 바뀌고, 재고는 예약과 취소 때마다 바뀝니다. 같은 "객실 정보"라도 일관성 요구가 다릅니다.

서비스 경계도 이 차이를 반영할 수 있습니다.

- Hotel Service는 호텔과 객실 유형의 정적 정보를 제공합니다.
- Rate Service는 날짜별 가격을 계산합니다.
- Reservation Service는 예약과 날짜별 재고를 같은 일관성 경계에서 처리합니다.
- Payment Service는 결제를 처리하고 예약 상태 전이를 알립니다.

마이크로서비스로 나눈다는 이유로 재고와 예약을 다른 데이터베이스에 먼저 분리하면 원자성을 지키기 어려워집니다. 같이 바뀌어야 하는 데이터는 같은 트랜잭션에 두는 편이 단순합니다.

### 날짜별 재고 모델

```text
room_type_inventory
- hotel_id
- room_type_id
- date
- total_inventory
- total_reserved
```

예를 들어 6월 1일부터 4일까지 숙박하면 6월 1일, 2일, 3일의 행을 확인합니다. `end_date`는 체크아웃 날짜이므로 보통 재고 차감에서 제외합니다.

```sql
SELECT date, total_inventory, total_reserved
FROM room_type_inventory
WHERE hotel_id = :hotel_id
  AND room_type_id = :room_type_id
  AND date >= :start_date
  AND date < :end_date;
```

날짜별 한 행을 두면 가용성 질의와 취소 복원이 단순해집니다. 향후 2년치를 미리 만들면 호텔 5천 개, 호텔당 객실 유형 20개 기준 약 7천3백만 행입니다. 관계형 데이터베이스가 충분히 다룰 수 있는 규모이고, ACID 트랜잭션도 활용할 수 있습니다.

### Overbooking도 정책으로 모델링한다

취소와 no-show를 예상해 10% 초과 예약을 허용할 수 있습니다.

```text
total_reserved + requested <= total_inventory * 1.10
```

비율을 코드에 고정하면 호텔과 날짜별 정책을 반영하기 어렵습니다. 실제 모델에는 판매 가능 한도나 overbooking policy version을 두는 편이 낫습니다. 객실 공사로 판매 중지된 수량도 `total_inventory` 계산에서 빼야 합니다.

Overbooking은 기술적 여유분이 아니라 사업 정책입니다. 너무 높이면 매출은 늘 수 있지만 실제 객실 부족으로 보상 비용과 신뢰 손실이 생깁니다.

### 가격과 예약 금액을 분리한다

Rate Service의 현재 가격만 참조하면 나중에 예약 내역의 금액이 바뀝니다. 예약 시점에 날짜별 적용 가격, 세금, 할인, 통화를 snapshot으로 저장해야 합니다.

```text
reservation
- reservation_id
- user_id
- hotel_id
- room_type_id
- start_date
- end_date
- room_count
- status
- quoted_total
- currency
```

가격 조회와 결제 사이에도 시간이 흐릅니다. quote에 만료 시각과 version을 두고, 예약 확정 시 다시 검증해야 합니다.

### 관계형 데이터베이스를 고르는 이유

평균 예약 TPS가 낮더라도 성수기 인기 호텔에는 쓰기가 몰립니다. 이 문제의 핵심은 전체 처리량보다 같은 재고 행에 대한 경합입니다. 관계형 데이터베이스는 unique constraint, transaction, row update를 이용해 예약과 재고를 함께 보호합니다.

조회 트래픽은 read replica와 cache로 분산할 수 있습니다. 단, cache의 "객실 있음"은 안내일 뿐 확정이 아닙니다. 최종 예약은 원본 데이터베이스에서 다시 확인해야 합니다.

데이터가 한 cluster를 넘으면 대부분 질의에 포함되는 `hotel_id`로 shard할 수 있습니다. 같은 호텔의 날짜별 재고와 예약을 한 shard에 두면 단일 예약 트랜잭션을 유지하기 쉽습니다.

### 실패 모드

- 체크아웃 날짜까지 차감해 하루를 더 판매 중지합니다.
- 객실 유형이 아닌 객실 번호를 미리 고정해 운영 유연성을 잃습니다.
- 취소 시 일부 날짜 재고만 복원해 가용 수량이 어긋납니다.
- cache가 늦게 갱신되어 화면에는 재고가 보이지만 예약은 실패합니다.
- 가격을 참조만 해 과거 예약 금액을 재현하지 못합니다.

### 함정 체크

- 평균 TPS가 낮다는 이유로 동시성 문제도 작다고 결론 내리면 안 됩니다.
- `total_inventory`는 물리 객실 수와 같지 않을 수 있습니다. 판매 중지 객실과 overbooking 정책을 반영해야 합니다.
- 예약, 재고, 결제를 무조건 한 분산 트랜잭션으로 묶기보다 예약과 재고의 강한 일관성 경계를 먼저 정해야 합니다.
- 가용성 검색 결과를 예약 확정으로 간주하면 안 됩니다.

### 오늘의 한 문장

**호텔 예약의 재고 단위는 객실 번호가 아니라 호텔, 객실 유형, 숙박 날짜의 조합입니다.**

### 30초 확인 문제

6월 1일 체크인, 6월 4일 체크아웃으로 디럭스룸 2개를 예약합니다. 어떤 재고 행을 확인하고 차감해야 할까요?

### 정답과 해설

6월 1일, 2일, 3일의 `(hotel_id, deluxe_room_type_id, date)` 행을 모두 확인하고 각 행의 `total_reserved`를 2씩 늘립니다. 6월 4일은 체크아웃 날짜이므로 차감하지 않습니다. 세 날짜 중 하나라도 판매 한도를 넘으면 전체 예약을 실패시켜 부분 예약을 막아야 합니다.

### 더 보기

![Day 27 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-27-hotel-reservation-model.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-27-hotel-reservation-model.md)
- [원문: Chapter 22: Hotel Reservation System](https://github.com/liquidslr/system-design-notes/tree/main/22.%20Hotel%20Reservation%20System)
