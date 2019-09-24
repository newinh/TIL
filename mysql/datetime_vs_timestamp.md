MySQL datatype 중 datetime 과 timestamp 차이점

datetime	|	timestamp

8bytes		| 	4bytes

index x		|	index o

character	|	integer

- timestamp 는 utc를 기준으로 저장되어, timezone 설정에 따라 출력값이 다르다!
