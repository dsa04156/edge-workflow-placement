# E0 HTTP 연결·복사 측정 계약

확인일: 2026-10-16. 연구 범위는 공식 문서·태그 소스 확인이다. 실제 지연 원인이나 약 40ms 지연의 원인은 **확인하지 않았다**. 클러스터·코드는 변경하지 않았다.

## 공식 구현에서 확인한 사항

- CPython 3.11.14와 3.12.11의 `HTTPConnection.connect()`는 연결 직후 `TCP_NODELAY=1`을 설정한다. 기본 클라이언트를 Nagle 허용군으로 부르면 안 된다. [3.11 소스](https://github.com/python/cpython/blob/v3.11.14/Lib/http/client.py#L959-L969), [3.12 소스](https://github.com/python/cpython/blob/v3.12.11/Lib/http/client.py#L1000-L1010)
- 서버가 상속하는 `StreamRequestHandler`는 `disable_nagle_algorithm=False`, `wbufsize=0`이다. 기본 `wfile`은 `_SocketWriter`이며 `write()`가 `sendall()`을 호출한다. 서버의 accepted socket은 측정 전에 `setsockopt(IPPROTO_TCP,TCP_NODELAY,0 또는 1)`로 명시하고 양쪽 `getsockopt` 결과를 기록한다. [서버 기반 구현](https://github.com/python/cpython/blob/v3.11.14/Lib/socketserver.py#L778-L836)
- `BaseHTTPRequestHandler.protocol_version` 기본값은 HTTP/1.0이다. HTTP/1.1 재사용에는 정확한 응답 `Content-Length`가 필요하며, 이 실험은 모든 정상 응답에 byte 길이를 명시한다. 요청 본문도 선언 길이만큼 소비한다. `Connection: close`는 종료를 요청하며 handler의 `close_connection`이 반복 여부를 결정한다. [문서](https://docs.python.org/3.11/library/http.server.html#http.server.BaseHTTPRequestHandler.protocol_version), [종료 처리 소스](https://github.com/python/cpython/blob/v3.12.11/Lib/http/server.py#L362-L367)
- `ThreadingHTTPServer`는 `ThreadingMixIn`을 사용한다. accepted TCP 연결마다 thread가 생기며, 한 keepalive 연결의 순차 요청들은 같은 handler의 `handle_one_request()` 반복에서 처리된다. HTTP/1.1을 설정해도 요청마다 새 thread가 생기는 것은 아니다. [thread 생성](https://github.com/python/cpython/blob/v3.11.14/Lib/socketserver.py#L684-L705), [HTTP handler 반복](https://github.com/python/cpython/blob/v3.12.11/Lib/http/server.py#L432-L438)
- 클라이언트는 다음 요청 전에 응답을 끝까지 읽어야 한다. 같은 `HTTPConnection` 객체라도 서버 종료 뒤 자동 재접속할 수 있으므로 객체 재사용만으로 TCP 연결 재사용을 판정하지 않는다. [클라이언트 계약](https://docs.python.org/3.12/library/http.client.html#http.client.HTTPConnection.getresponse)

## 제안: 2×2 통제 실험

| 연결 정책 | Nagle 허용 | Nagle 비활성 |
|---|---|---|
| 매 요청 신규 TCP | client/server `TCP_NODELAY=0/0` | client/server `1/1` |
| 동일 TCP 순차 재사용 | client/server `0/0` | client/server `1/1` |

- 클라이언트는 매 `connect()` **뒤**, 첫 요청 **전**에 값을 덮어쓴다. 재접속 때도 반복 적용한다. 서버는 각 accepted connection에서 적용한다. 위 2×2는 양끝을 함께 바꾸므로 송신 방향별 원인 분리는 못 한다. 필요하면 `0/1`, `1/0`을 후속 조건으로 추가한다.
- 네 조건 모두 HTTP/1.1·동일 본문·응답 크기·쓰기 순서·동시성 1·timeout을 유지한다. 신규군은 한 응답을 완독한 뒤 연결을 닫고, 재사용군은 워밍업 후 같은 소켓을 유지한다. 서버 connection ID와 client/server 주소·포트, 요청 번호, 재접속·오류를 기록한다.
- 신규군은 `connect_ms`와 `request→response 완독_ms`를 별도 기록하고 합산값도 제시한다. 재사용군 첫 연결/첫 요청은 warm 측정과 분리한다. 조건 실행 순서는 교차하고 반복 수·payload·모델 warm 상태를 고정한다.
- CPU 대상은 같은 edge 노드의 별도 worker Pod 및 x86 서버로 구분하고, GPU/NPU는 직전 검증 경로를 유지한다. endpoint·worker·모델/artifact·입력 hash·backend를 고정한다. 이 문서에서 새 실행 결과를 만들지 않았다.

## 헤더·본문 쓰기와 지연 해석

- `end_headers()`는 header buffer를 `wfile.write()`하고, 이어지는 body write는 별도 호출이다. 기본 unbuffered writer에서 `flush()` 추가만으로 둘이 합쳐지지 않는다. write 호출 경계와 실제 TCP packet 경계는 동일하지 않다. [헤더 소스](https://github.com/python/cpython/blob/v3.12.11/Lib/http/server.py#L534-L543), [writer 소스](https://github.com/python/cpython/blob/v3.11.14/Lib/socketserver.py#L822-L836)
- RFC는 미확인 데이터가 있을 때 Nagle이 작은 전송을 보류할 수 있고 delayed ACK와 상호작용할 수 있다고 설명한다. 그러나 ACK 지연을 40ms로 규정하지 않는다. `TCP_NODELAY=1`도 ACK 정책·혼잡·스케줄링·프록시 지연을 제거하지 않는다. [RFC Nagle](https://www.rfc-editor.org/rfc/rfc9293.html#section-3.7.4), [delayed ACK](https://www.rfc-editor.org/rfc/rfc9293.html#section-3.8.6.3)
- **해석 한계:** 조건별 RTT 차이는 해당 socket 설정에 대한 민감도다. 원인 확정에는 송신·ACK 시각 등 추가 관측이 필요하다. client wall time은 요청 전송·서버 대기/처리·응답 수신을 포함하며 one-way tensor 전송시간이 아니다. 헤더/본문 결합 쓰기는 별도 요인이므로 이번 2×2와 동시에 바꾸지 않는다.

## CUDA 복사·추론 구간

- PyTorch GPU 연산은 비동기이므로 host timer만 둘러싸면 enqueue 시간일 수 있다. 같은 stream에서 `start.record(); copy_or_compute(); end.record(); end.synchronize(); start.elapsed_time(end)`로 완료 후 event 시간을 읽는다. 다른 stream 작업은 명시적 dependency 없이 포함되지 않는다. [PyTorch 2.9.1 CUDA semantics](https://github.com/pytorch/pytorch/blob/v2.9.1/docs/source/notes/cuda.rst), [Event API 소스](https://github.com/pytorch/pytorch/blob/v2.9.1/torch/cuda/streams.py)
- H2D·inference·D2H 각각 event 쌍을 두고 H2D 입력/출력 버퍼 준비·pinning·할당은 구간 밖에 둔다. pinned 여부·`non_blocking`·stream을 기록하고 D2H 완료 전 CPU에서 결과를 읽지 않는다. event 값은 지정 stream의 구간 시간이며 Python 준비·직렬화·HTTP·전체 host 대기시간을 대신하지 않는다. [공식 async/pinned-memory 설명](https://github.com/pytorch/pytorch/blob/v2.9.1/docs/source/notes/cuda.rst)
- **제안:** host 관점 총비용은 시작 전 이전 GPU 작업 완료→host monotonic timer→H2D/추론/D2H→완료 동기화→timer로 따로 측정한다. 전체 구간에 이전 작업이 섞이지 않게 하고 세부 event 시간과 함께 제시한다. event 구간을 검증 없이 순수 DMA 시간으로 부르지 않는다.
