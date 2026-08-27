# System Design Daily

## Day 32/40: 큰 객체와 삭제는 수명 주기로 관리한다

### 오늘의 질문

- 100GB 업로드가 99%에서 끊겼을 때 처음부터 다시 보내지 않고도, 미완성 조각을 영원히 남기지 않으려면 무엇이 필요할까요?

### 왜 한 번의 PUT으로는 부족한가

큰 파일을 한 HTTP 요청으로 보내면 연결 실패의 비용이 큽니다. 마지막 1GB에서 실패해도 100GB 전체를 다시 보내야 합니다. 프록시와 로드밸런서의 요청 제한에도 걸리기 쉽고, 한 연결의 속도가 전체 업로드 시간을 결정합니다.

Multipart Upload는 객체를 독립적인 part로 나눕니다. 실패한 part만 다시 보내고 여러 part를 병렬 업로드할 수 있습니다. 대신 서비스는 업로드 세션이라는 새 상태를 관리해야 합니다.

### Multipart Upload 상태 머신

```text
INITIATED -> UPLOADING -> COMPLETING -> COMPLETED
                         -> ABORTED
```

1. 클라이언트가 업로드를 시작하고 `upload_id`를 받습니다.
2. 각 part를 `upload_id`, `part_number`와 함께 업로드합니다.
3. 서버는 part마다 ETag와 크기를 반환합니다.
4. 클라이언트가 part 번호와 ETag 목록으로 완료를 요청합니다.
5. 서버는 누락, 중복, 순서, 체크섬을 검증한 뒤 하나의 객체 버전을 공개합니다.

```http
POST /bucket/video.mp4?uploads
PUT /bucket/video.mp4?uploadId=u123&partNumber=7
POST /bucket/video.mp4?uploadId=u123
```

완료 요청은 멱등해야 합니다. 서버가 조립을 끝낸 직후 응답이 끊길 수 있기 때문입니다. 같은 완료 요청이 다시 오면 새 객체를 만들지 말고 이미 완성된 버전과 결과를 반환해야 합니다.

### 버전은 덮어쓰기를 새 쓰기로 바꾼다

객체 저장소는 기존 바이트를 제자리에서 수정하지 않습니다. 같은 객체 이름에 새 내용을 올리면 새로운 `object_id`와 버전을 만듭니다. 최신 버전을 가리키는 메타데이터만 바뀝니다.

```text
(bucket, key, version_id) -> object_id, checksum, state
```

삭제도 즉시 모든 바이트를 지우지 않습니다. 최신 위치에 delete marker를 추가해 기본 조회가 404를 반환하게 합니다. 이전 버전은 보존 정책에 따라 복구하거나 나중에 삭제합니다.

이 방식은 쓰기 경로를 단순하게 하고 복구 가능성을 높이지만 저장 비용이 계속 늘어납니다. 그래서 버전 보존 기간, 최대 버전 수, 법적 보존 잠금을 명시해야 합니다.

### Garbage Collection이 필요한 이유

객체 저장소에는 정상 API만으로 지워지지 않는 데이터가 생깁니다.

- 중간에 포기한 Multipart part
- 메타데이터 연결에 실패한 고아 본문
- delete marker 뒤에 남은 오래된 버전
- 체크섬 검증에 실패해 교체된 손상 조각
- 재복제 뒤에 남은 이전 복제본

GC는 메타데이터가 참조하는 살아 있는 객체를 기준으로 나머지를 회수합니다. 그러나 즉시 삭제하면 진행 중인 업로드나 지연된 복제와 경합할 수 있습니다. `created_at + grace_period`를 지난 데이터만 후보로 삼고, 현재 업로드 세션과 법적 보존 상태를 다시 확인해야 합니다.

### Append-only 파일의 삭제는 Compaction이다

작은 객체를 큰 파일에 묶어 저장하면 객체 하나만 물리적으로 잘라낼 수 없습니다. 살아 있는 객체를 새 파일로 복사한 뒤 색인을 원자적으로 교체하는 Compaction이 필요합니다.

```text
1. 오래된 묶음 파일에서 live 객체만 새 파일로 복사한다.
2. 새 위치와 체크섬을 검증한다.
3. object_mapping을 트랜잭션으로 새 위치에 바꾼다.
4. 독자가 이전 위치를 쓰지 않게 된 뒤 이전 파일을 삭제한다.
```

Compaction은 공간을 되찾지만 디스크 대역폭을 크게 씁니다. 사용자 트래픽이 높은 시간에는 지연을 악화시킬 수 있으므로 I/O 예산, 후보 파일의 garbage ratio, 동시 실행 수를 제한해야 합니다.

### 실무 예

동영상 업로드에서 각 64MB part를 병렬 전송합니다. 모바일 네트워크가 끊기면 완료된 part 목록을 조회해 나머지만 재개합니다. 24시간 동안 완료되지 않은 업로드는 GC 후보가 되지만, 실행 중인 세션과 최근 갱신 시각을 다시 확인한 뒤 삭제합니다. 완성 객체는 30일간 모든 버전을 보존하고 이후 최신 버전만 남기는 정책을 둘 수 있습니다.

### 함정 체크

- ETag를 언제나 전체 객체의 MD5라고 가정하면 안 됩니다. Multipart 객체의 ETag 의미는 구현에 따라 다릅니다.
- 완료 요청의 재시도를 새 객체 생성으로 처리하면 중복 버전이 생깁니다.
- delete marker는 물리 삭제가 아닙니다. 용량과 규제 요구를 따로 추적해야 합니다.
- GC가 메타데이터 지연을 모르면 아직 참조될 데이터를 지울 수 있습니다.
- Compaction 중 색인을 먼저 바꾸거나 이전 파일을 일찍 지우면 읽기 실패가 발생합니다.

### 오늘의 한 문장

> 객체 저장소의 삭제는 한 번의 명령이 아니라, 참조를 끊고 안전을 확인한 뒤 공간을 회수하는 수명 주기입니다.

### 30초 확인 문제

Multipart 완료 처리 후 객체는 만들어졌지만 응답이 끊겼습니다. 클라이언트가 같은 완료 요청을 다시 보냈을 때 서버는 어떻게 해야 할까요?

### 정답과 해설

`upload_id`와 완료 요청의 part 목록을 기준으로 기존 완료 결과를 찾아 같은 객체 버전과 응답을 반환해야 합니다. 새 버전을 만들거나 part를 다시 조립하면 중복 객체와 불필요한 I/O가 생깁니다. 완료 상태와 결과 객체 ID를 내구성 있게 저장해야 재시도에도 같은 결과를 보장할 수 있습니다.

### 더 보기

![Day 32 다이어그램](https://raw.githubusercontent.com/newinh/TIL/orca/sys-design/diagrams/day-32-object-storage-lifecycle.png)

- [전체 강의](https://github.com/newinh/TIL/blob/orca/sys-design/lessons/day-32-object-storage-lifecycle.md)
- [원문: Chapter 24, S3-like Object Storage](https://github.com/liquidslr/system-design-notes/tree/main/24.%20S3-like%20Object%20Storage)
