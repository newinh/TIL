# System Design Daily

## Day 28/40: 마지막 한 방은 동시성 제어가 막는다

### 오늘의 질문

남은 객실이 하나일 때 두 고객이 동시에 예약 버튼을 누르면, 둘 다 "재고 있음"을 읽고 결제하는 일을 어떻게 막을까요?

### 두 종류의 중복을 구분한다

첫 번째는 같은 사용자가 버튼을 두 번 누르거나 timeout 뒤 요청을 재전송하는 경우입니다. 두 번째는 서로 다른 사용자가 같은 날짜의 마지막 재고를 경쟁하는 경우입니다. 원인은 다르므로 방어도 다릅니다.

같은 요청의 반복은 idempotency key로 막습니다. 서로 다른 요청의 경합은 데이터베이스의 원자적 조건과 동시성 제어로 막습니다.

### 요청 중복은 idempotency key로 막는다

예약 화면에 들어올 때 서버가 `reservation_id`를 만들고 같은 예약 시도에는 같은 값을 사용합니다. 데이터베이스에 unique constraint를 둡니다.

```sql
ALTER TABLE reservation
ADD CONSTRAINT uq_reservation_id UNIQUE (reservation_id);
```

두 번째 요청은 새 예약을 만들지 않고 첫 요청의 결과를 반환해야 합니다. 단순히 "duplicate error"를 보내면 클라이언트는 결제가 됐는지 알 수 없습니다. idempotency record에는 처리 상태와 응답을 저장해야 합니다.

키 재사용 범위도 중요합니다. 사용자가 날짜나 객실 유형을 바꿨다면 새 예약 시도로 보아 새 키를 써야 합니다.

### 경쟁 예약은 read-check-write 사이에서 생긴다

두 트랜잭션이 모두 `total_reserved = 99`를 읽고 각각 1을 더하면, 둘 다 성공했다고 판단할 수 있습니다. 애플리케이션에서 먼저 조회하고 나중에 UPDATE하는 것만으로는 부족합니다.

가장 단순한 방어는 조건부 UPDATE입니다.

```sql
UPDATE room_type_inventory
SET total_reserved = total_reserved + :count
WHERE hotel_id = :hotel_id
  AND room_type_id = :room_type_id
  AND date = :date
  AND total_reserved + :count <= sell_limit;
```

영향받은 행 수가 0이면 재고가 없거나 경합에서 진 것입니다. 여러 날짜를 예약한다면 모든 날짜 갱신을 한 트랜잭션에 넣고 하나라도 실패할 때 rollback해야 합니다.

### 비관적 잠금

`SELECT ... FOR UPDATE`로 재고 행을 잠그면 다른 트랜잭션이 기다립니다. 충돌이 잦고 짧은 트랜잭션이라면 이해하기 쉽고 확실합니다.

하지만 숙박 기간의 여러 날짜 행을 잠글 때 순서가 다르면 deadlock이 생길 수 있습니다. 모든 트랜잭션이 날짜 오름차순으로 잠그고, 결제 API 호출처럼 느린 외부 작업을 잠금 안에서 실행하지 않아야 합니다. 인기 호텔에 요청이 몰리면 lock wait가 지연을 키웁니다.

### 낙관적 잠금

재고 행에 `version`을 두고 읽은 version과 같을 때만 갱신합니다.

```sql
UPDATE room_type_inventory
SET total_reserved = total_reserved + :count,
    version = version + 1
WHERE inventory_id = :id
  AND version = :expected_version;
```

충돌이 적으면 잠금 대기 없이 빠릅니다. 충돌하면 다시 읽고 재시도해야 하므로 인기 행사처럼 경합이 높을 때 retry storm이 생길 수 있습니다. 재시도에는 횟수 제한과 jitter가 필요합니다.

### Database constraint는 마지막 안전망이다

```sql
CHECK (total_reserved <= sell_limit)
```

애플리케이션 버그나 다른 쓰기 경로가 잘못된 값을 넣어도 데이터베이스가 막습니다. Constraint만으로 사용자에게 좋은 오류를 주기는 어렵지만, 불변식을 데이터 가까이에 두는 마지막 방어선이 됩니다.

### 결제와 재고의 긴 트랜잭션을 피한다

외부 결제 승인을 기다리는 동안 재고 row lock을 잡으면 처리량이 급격히 떨어집니다. 보통 재고를 짧게 hold하고 결제를 시도한 뒤 성공하면 확정하고, 실패하거나 hold가 만료되면 재고를 되돌립니다.

```text
AVAILABLE -> HELD -> CONFIRMED
                 -> EXPIRED
                 -> CANCELLED
```

이 흐름은 단일 ACID 트랜잭션이 아니라 상태 머신과 보상 작업을 요구합니다. 만료 worker가 실패해도 다시 실행할 수 있도록 hold 해제는 멱등해야 합니다. 결제 성공 이벤트와 예약 확정 사이 장애도 reconciliation 대상입니다.

### Cache는 확정 판단을 하지 않는다

Redis 재고는 검색 화면을 빠르게 만들 수 있습니다. CDC로 데이터베이스 변경을 반영하면 cache가 잠깐 오래된 값을 보여 줄 수 있습니다. 사용자가 품절 객실을 클릭하는 불편은 생기지만, 최종 데이터베이스가 잘못된 예약을 막으면 금전적 불일치는 피할 수 있습니다.

Cache에서 먼저 차감하고 나중에 DB를 맞추는 설계는 cache 장애와 이중 쓰기 문제를 새로 만듭니다. 정말 필요한 규모인지 증명하기 전에는 원본 DB를 예약 확정의 기준으로 두는 편이 낫습니다.

### 함정 체크

- 버튼을 비활성화하는 것만으로 중복 요청을 막을 수 없습니다.
- `SELECT`로 확인한 값은 `UPDATE` 시점까지 유효하다고 보장되지 않습니다.
- row lock을 잡은 채 외부 결제를 호출하면 안 됩니다.
- 낙관적 잠금은 충돌이 많을수록 rollback과 재시도가 급증합니다.
- 여러 날짜 중 일부만 갱신하고 실패하면 유령 재고가 생깁니다.

### 오늘의 한 문장

**예약 시스템은 중복 요청을 멱등하게 만들고, 재고 불변식을 데이터베이스의 원자적 갱신으로 지켜야 합니다.**

### 30초 확인 문제

3박 예약에서 첫째 날과 둘째 날 재고 갱신은 성공했지만 셋째 날은 품절로 실패했습니다. 어떤 처리가 필요하며, 결제 호출은 언제 해야 할까요?

### 정답과 해설

세 날짜 갱신을 한 데이터베이스 트랜잭션으로 묶어 전체 rollback해야 합니다. 부분 성공을 남기면 실제 예약 없이 재고만 줄어듭니다. 결제를 위해 오래 row lock을 잡지 말고, 짧은 트랜잭션으로 재고 hold를 만든 뒤 결제를 호출하고 성공 시 확정하는 상태 머신을 사용합니다.

### 더 보기

![Day 28 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-28-hotel-reservation-concurrency.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-28-hotel-reservation-concurrency.md)
- [원문: Chapter 22: Hotel Reservation System](https://github.com/liquidslr/system-design-notes/tree/main/22.%20Hotel%20Reservation%20System)
