# System Design Daily

## Day 24/40: 오래된 지표는 낮은 해상도로, 알림은 상태로 관리한다

### 오늘의 질문

1년치 지표를 저장하면서 최근 장애 대시보드는 빠르게 보여 주고, 같은 장애로 호출을 수백 번 보내지 않으려면 어떻게 설계해야 할까요?

### 저장소는 시간 순서 질의에 맞춘다

지표 시스템은 쓰기가 계속 들어오고 읽기는 평소 적다가 장애 때 몰립니다. 운영자는 보통 최근 구간을 가장 많이 조회하고 metric name과 label로 시계열을 고른 뒤 시간 범위를 집계합니다.

범용 관계형 데이터베이스에도 저장할 수 있지만, 고속 append, 시간 범위 압축, label index, rollup을 직접 튜닝해야 합니다. 규모가 크면 time-series database를 선택하는 편이 낫습니다. 핵심은 제품 이름이 아니라 다음 접근 패턴입니다.

- 시간 순서로 쓰고 최근 구간을 자주 읽습니다.
- 동일 시계열의 timestamp와 value 변화량이 작아 압축하기 좋습니다.
- 오래된 원본보다 장기 추세가 중요합니다.
- 장애 때 최근 데이터 질의가 갑자기 많아집니다.

### 보존 기간과 해상도를 분리한다

1초 단위 데이터를 1년 내내 보관하면 비싸고, 대부분 다시 읽지 않습니다. 그래서 시간에 따라 해상도를 낮춥니다.

```text
0일에서 7일: 원본 해상도
8일에서 30일: 1분 단위 avg, min, max, count
31일에서 1년: 1시간 단위 avg, min, max, count
```

평균만 저장하면 1분 동안의 짧은 spike가 사라집니다. 운영 지표는 평균과 함께 최댓값, 최솟값, 개수, 필요하면 histogram bucket을 보존해야 합니다. percentile을 단순 평균 내면 잘못된 결과가 되므로 집계 가능한 분포 표현을 써야 합니다.

차분 인코딩으로 timestamp 전체 대신 이전 값과의 차이를 저장하고, 오래된 block을 cold storage로 옮기면 비용을 더 줄일 수 있습니다. 대신 cold data 조회는 느려지고 복구 절차가 필요합니다.

### Query Service를 둘지 결정한다

대시보드와 alert manager가 TSDB를 직접 호출하면 구조가 단순합니다. 선택한 TSDB의 질의 언어와 plugin이 충분하면 이 방식이 합리적입니다.

별도 Query Service를 두면 인증, query limit, 여러 저장소 통합, cache, API 안정성을 중앙에서 관리할 수 있습니다. 저장소 교체도 클라이언트에서 숨길 수 있습니다. 반면 서비스 하나를 더 운영하고, TSDB 기능을 얕게 다시 구현할 위험이 있습니다.

장애 중에는 모든 대시보드가 같은 최근 구간을 반복 조회합니다. 짧은 TTL cache와 동시 요청 합치기는 TSDB를 보호합니다. 다만 alert query가 오래된 cache를 읽으면 탐지가 늦으므로 대시보드와 alert 경로의 cache 정책을 분리해야 합니다.

### 알림은 조건식이 아니라 상태 머신이다

`cpu > 90`만 평가해 바로 호출하면 순간 spike와 매 평가 주기마다 알림이 쏟아집니다. 규칙에는 지속 시간과 상태가 필요합니다.

```yaml
alert: instance_down
expr: up == 0
for: 5m
severity: page
```

한 대상의 알림은 보통 `OK`, `PENDING`, `FIRING`, `RESOLVED` 상태를 오갑니다. 5분 동안 조건이 유지되면 FIRING으로 바꾸고, 같은 fingerprint의 알림은 하나로 합칩니다. 서비스 전체 장애 때 인스턴스 500개의 개별 page 대신 상위 원인으로 묶는 inhibition도 필요합니다.

### 알림 전송을 비동기로 분리한다

Alert manager는 규칙을 읽고 Query Service를 주기적으로 호출합니다. 조건이 맞으면 alert event를 저장하고 queue에 넣습니다. Email, SMS, PagerDuty, webhook consumer가 각 채널로 전송합니다.

```text
rule evaluator -> alert store -> alert queue -> channel workers
```

이 구조는 느린 외부 채널이 규칙 평가를 막지 않게 합니다. 전송은 at-least-once가 현실적이므로 channel worker도 alert ID를 기준으로 중복을 줄여야 합니다. 외부 API가 실패하면 backoff와 최대 재시도 횟수를 적용하고, 영구 실패는 운영자가 확인할 수 있게 남깁니다.

### 모니터링 시스템도 모니터링한다

가장 위험한 실패는 서비스가 멈췄는데 모니터링도 함께 멈춰 조용한 상태입니다. 외부 위치에서 수집 지연, rule evaluation 지연, notification 성공률을 확인하는 dead man's switch가 필요합니다.

실무에서는 다음 지표를 따로 둡니다.

- 수집 시각과 저장 시각의 차이
- queue lag와 가장 오래된 미처리 이벤트 나이
- rule evaluation 소요 시간과 실패 수
- 알림 생성부터 채널 전송까지의 지연
- label cardinality와 저장 증가율

### 직접 만들지 않을 선택

Grafana 같은 시각화와 검증된 alert manager는 이미 많은 실패 모드를 다룹니다. 사내 요구가 특별하지 않다면 수집과 데이터 정책에 집중하고 기성 도구를 조합하는 편이 낫습니다. 직접 구현할 근거는 규모 자체가 아니라 기존 도구로 충족할 수 없는 요구여야 합니다.

### 함정 체크

- 오래된 데이터를 평균 하나로만 rollup하면 spike와 분포를 잃습니다.
- 알림 조건이 참일 때마다 새 알림을 만들면 장애가 커질수록 대응이 더 어려워집니다.
- 전송 queue만 내구성 있게 만들고 alert 상태를 저장하지 않으면 재시작 후 중복과 누락을 판정하기 어렵습니다.
- 대시보드가 빠르다는 이유로 alert query에도 같은 cache를 쓰면 탐지가 늦을 수 있습니다.

### 오늘의 한 문장

**지표 저장은 시간에 따라 정확도와 비용을 교환하고, 알림은 조건의 참과 거짓이 아니라 지속되는 상태를 관리합니다.**

### 30초 확인 문제

CPU 사용률을 1분 평균으로만 1년 보관합니다. 10초 동안 100%였던 spike를 나중에 찾을 수 있을까요? 알림 중복은 무엇으로 묶어야 할까요?

### 정답과 해설

평균만 남겼다면 짧은 spike가 희석되어 찾기 어렵습니다. rollup에 min, max, count 또는 분포 정보를 함께 저장해야 합니다. 알림은 rule, 대상 label, 조건을 정규화해 만든 fingerprint로 묶고 상태 저장소에서 FIRING 여부를 관리해야 합니다.

### 더 보기

![Day 24 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-24-monitoring-storage-alerting.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-24-monitoring-storage-alerting.md)
- [원문: Chapter 20: Metrics Monitoring and Alerting System](https://github.com/liquidslr/system-design-notes/tree/main/20.%20Metrics%20Monitoring%20and%20Alerting%20System)
