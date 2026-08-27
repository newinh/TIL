# System Design Daily

## Day 23/40: 지표 수집은 관측 대상을 잃지 않는 일이다

### 오늘의 질문

서버 10만 대에서 수백 개 지표를 모을 때, 누가 무엇을 언제 수집해야 누락과 중복을 통제할 수 있을까요?

### 로그가 아니라 시계열을 설계한다

모니터링 시스템의 입력은 보통 시간에 따라 변하는 숫자입니다. 한 데이터 포인트는 metric name, labels, timestamp, value로 표현할 수 있습니다.

```text
http_requests_total service=checkout,region=ap-northeast-2 1710000000 1842
cpu_usage host=web-17,region=ap-northeast-2 1710000000 73.4
```

같은 metric name과 label 조합이 하나의 시계열을 만듭니다. 운영자는 최근 10분의 CPU 평균, 서비스별 오류율, 큐의 consumer lag처럼 시간 구간과 label을 기준으로 묻습니다.

이 구조를 로그와 혼동하면 저장 비용과 질의 모델이 어긋납니다. 로그는 사건의 문맥을 담고, 지표는 반복되는 상태를 숫자로 압축합니다. 모든 요청 ID를 label로 넣는 순간 지표가 로그처럼 변합니다.

### label cardinality가 비용을 정한다

`region`, `service`, `status_code`는 값의 종류가 제한적입니다. 반면 `user_id`, `request_id`, 원문 URL은 값이 계속 늘어납니다. 고유 label 조합이 많아지면 시계열 수도 폭발합니다.

```text
10 services x 5 regions x 20 endpoints x 5 status codes = 5,000 series
```

여기에 수백만 `user_id`를 붙이면 인덱스와 메모리가 감당하기 어려워집니다. 수집 전에 label 허용 목록, 값 길이, 최대 cardinality를 제한해야 합니다. 세부 추적이 필요하면 지표가 아니라 로그나 trace에 남깁니다.

### Pull은 대상을 알고, Push는 대상이 찾아온다

**Pull 모델**에서는 collector가 각 서비스의 `/metrics`를 주기적으로 호출합니다. 서비스 디스커버리가 살아 있는 endpoint와 수집 주기, timeout을 제공합니다.

장점은 디버깅이 쉽다는 점입니다. 사람이 `/metrics`를 직접 열어 원본을 확인할 수 있고, pull 실패 자체가 인스턴스 장애 신호가 됩니다. 단, 짧게 실행되는 batch job은 수집 전에 사라질 수 있고 네트워크 경계를 넘어 모든 endpoint에 접근하기 어렵습니다.

**Push 모델**에서는 애플리케이션이나 sidecar agent가 collector로 지표를 보냅니다. 짧은 작업과 복잡한 네트워크에 유리하고, agent에서 먼저 집계해 전송량을 줄일 수 있습니다. 반면 아무 클라이언트나 데이터를 보내지 못하도록 인증해야 하고, 지표가 오지 않을 때 애플리케이션 장애인지 전송 장애인지 구분하기 어렵습니다.

큰 조직은 하나만 고집하기보다 환경에 따라 둘을 함께 씁니다.

### 수집자를 여러 대로 늘리는 법

collector 한 대가 모든 endpoint를 pull하면 곧 병목과 단일 장애점이 됩니다. 여러 collector가 같은 대상을 읽으면 중복 데이터가 생깁니다. 따라서 대상 집합을 collector에 안정적으로 나눠야 합니다.

consistent hashing을 쓰면 서버와 collector를 ring에 배치하고 각 서버를 한 collector에 할당할 수 있습니다. collector가 늘거나 줄 때 일부 대상만 이동하므로 재배치 비용이 작습니다.

Push 모델에서는 collector를 load balancer 뒤에 두고 자동 확장할 수 있습니다. 이때도 수신 성공과 저장 성공을 같은 것으로 보면 안 됩니다. time-series database가 느려졌을 때 collector가 함께 막히지 않도록 중간 queue를 둡니다.

```text
metrics source -> collector -> queue -> stream processor -> TSDB
```

queue는 순간 부하와 저장소 장애를 흡수합니다. 하지만 운영할 시스템이 하나 더 늘고, queue lag만큼 알림도 늦어질 수 있습니다. 규모가 작고 저장소가 충분히 안정적이라면 직접 쓰기가 더 단순할 수 있습니다.

### 어디에서 집계할 것인가

수집 agent에서 10초 값을 1분 평균으로 합치면 네트워크와 저장 비용이 줄지만 원본을 잃습니다. ingestion pipeline에서 합치면 중앙에서 정책을 통제할 수 있지만 stream processor를 운영해야 합니다. query 시점에 합치면 원본을 보존하지만 장애 중 대시보드 질의가 느려질 수 있습니다.

최근 원본은 보존하고 오래된 데이터는 낮은 해상도로 바꾸는 식으로 단계별 정책을 두는 이유가 여기에 있습니다.

### 실무 실패 모드

- collector가 서비스 디스커버리의 오래된 endpoint를 계속 긁어 실패 요청을 만듭니다.
- 두 collector가 같은 대상을 맡아 counter 증가량이 두 배로 보입니다.
- Push client의 시간 오차로 데이터가 과거나 미래 구간에 기록됩니다.
- 새 배포가 `customer_id` label을 추가해 시계열 수와 저장 비용을 폭발시킵니다.
- queue가 버티고 있다는 이유로 consumer lag를 놓쳐 알림이 수십 분 늦습니다.

### 함정 체크

- 수집 성공률만 보면 안 됩니다. 수집부터 저장까지의 지연도 함께 봐야 합니다.
- metric name과 label schema를 팀별 자유 형식으로 두면 질의와 비용을 통제하기 어렵습니다.
- UDP처럼 빠른 전송을 쓴다고 전체 모니터링 지연이 자동으로 줄지는 않습니다.
- 일부 지표 손실을 허용하더라도, 모니터링 시스템 전체가 멈춘 사실은 반드시 별도로 감지해야 합니다.

### 오늘의 한 문장

**지표 수집의 핵심은 숫자를 많이 받는 일이 아니라, 관측 대상과 수집 책임을 안정적으로 연결하는 일입니다.**

### 30초 확인 문제

수명이 20초인 batch job의 성공 횟수를 1분마다 pull하는 collector로 모으고 있습니다. 어떤 문제가 생기며 무엇을 바꾸는 편이 나을까요?

### 정답과 해설

job이 수집 주기 사이에 시작하고 끝나면 지표를 한 번도 읽지 못합니다. job이 push gateway나 collector로 결과를 보내게 하거나, node agent가 프로세스 종료 전에 지표를 전달하게 해야 합니다. Pull을 유지하려면 job보다 짧은 수집 주기가 필요하지만 대상 수가 많을수록 비용이 커집니다.

### 더 보기

![Day 23 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-23-monitoring-collection.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-23-monitoring-collection.md)
- [원문: Chapter 20: Metrics Monitoring and Alerting System](https://github.com/liquidslr/system-design-notes/tree/main/20.%20Metrics%20Monitoring%20and%20Alerting%20System)
