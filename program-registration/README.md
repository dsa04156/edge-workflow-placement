# EdgeFlow 프로그램 등록 준비본

- [복사해서 입력할 의뢰서](submission/프로그램등록의뢰서_최종.md)
- [일반 텍스트](submission/등록입력문구.txt)
- [최신 전체 소스 ZIP](submission/EdgeFlow_ready_source.zip): 39개 파이썬 파일, 4,195줄, 106,719바이트
- [상세 실행 안내](ready-source/README.md)
- [별도 실행 자료](runtime-assets/EdgeFlow_runtime_assets.tar): 모델·입력·기준 출력·설치 패키지, 약 1.05 GB
- [파일별 행 수와 해시](submission/ready-source-inventory.json)
- [검증 결과](submission/ready-validation.json)
- [윈도우 재압축 스크립트](submission/Repack-Ready-On-Windows.ps1)

소스 ZIP과 실행 자료를 함께 사용한다. 실행 자료는 등록 소스 첨부란이나 공개 Git에 올리지 않는다. 클러스터 접근 설정과 접근 가능한 컨테이너 이미지는 실행 환경에 맞게 준비한다.

실제 모델을 사용한 로컬 입력 두 건·실행 기록 여섯 건을 검증하고 정상 종료를 확인했다. 클러스터에서는 읽기 전용 사전 검사를 수행했으며 이번 패키징에서 전체 성능 비교를 새로 실행하지 않았다.

이전 `EdgeFlowPlan_source.zip`과 `EdgeFlow_full_source.zip`은 과거 준비본으로 보존한다. 최신 제출 준비본은 `EdgeFlow_ready_source.zip`이다. 현재 ZIP은 리눅스에서 생성했다. 신청 화면의 윈도우 생성 조건에 따라 재압축하고 최종 바이트 크기를 의뢰서에 입력한다.
