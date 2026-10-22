# fix-report: v2 Spec P2 경계 오류 최소 수정

## 실행 주체·인증
- Developer: Claude Code CLI, 모델 `claude-opus-5-5` (Opus 5.5). 하위 agent 미사용.
- `rtk proxy claude auth status`: `loggedIn=true`, `authMethod=claude.ai`, `apiProvider=firstParty`, `subscriptionType=max`. `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`/Bedrock/Vertex 환경변수 없음(`CLAUDE_CODE_ENTRYPOINT=cli`만 존재).
- 요청된 `tdd`·`claude-implementer` 스킬은 이 세션의 스킬 목록에 없다. 그래서 red→green 절차를 수동으로 따랐다. 테스트는 승인된 공개 seam `run_policy(..., policy_factory=make_policy)`(`test_synthetic_v2.run`)만 사용한다.

## 변경 경로
- `experiments/decision-focused-placement/policies_v2.py` — `CancelCommonRoundRobin.choose`, `INF` import.
- `experiments/decision-focused-placement/test_synthetic_v2.py` — 회귀 테스트 3개 추가(기존 테스트는 수정하지 않음).
- `experiments/decision-focused-placement/README.md` — `cancel_common_round_robin` 규칙 설명 한 문장 교체.
- 신규 artifact: `artifacts/decision-focused-placement/synthetic-v2-reviewfix/`(중간 시도 1), `artifacts/decision-focused-placement/synthetic-v2-final/`(최종, coordinator가 추가 승인).
- fixture·config·seed·다른 소스·기존 artifact(`synthetic-v2/` 등)는 바꾸지 않았다. 커밋·push도 하지 않았다.

## 확정 동작(수정 후)
- 제외 집합 = 공통 좌표 중 현재 구간 상한이 유한한 좌표. 다른 좌표의 구간이 무한이어도 이 좌표는 복원하지 않는다.
- 순환 대상 = 사용 좌표 중 제외 집합에 들지 않는 좌표(비공통 좌표 + 상한 무한 공통 좌표)를 원래 ID 순서로 둔 것.
- 순환 대상이 비면(모든 사용 좌표가 유한 공통인데 미인증) 사용 좌표 전체를 순환하고 rule은 `round_robin`이다. 기존 fallback과 같다.
- rule 라벨: 위 fallback이거나, 제외할 좌표가 없고 무한 구간이 있으면 `round_robin`(v1 공통 규칙과 같은 순환·라벨)이다. 그 밖에는 `cancel_common_round_robin`이다.
- 모형·통계·종료 규칙과 runner 인증 계산은 바꾸지 않았다.

## red → green
1. 신규 테스트 2개(test 1·2)를 먼저 추가하고 실행: **FAILED (failures=2)**
   - `test_finite_common_term_stays_excluded_when_only_non_common_support_is_unknown`: 관측 `(['z','y'], 5.0)`, 기대 `(['y','y'], 2.0)`(review 재현과 같음).
   - `test_only_finite_common_terms_are_excluded_alongside_infinite_common_terms`(w 유한 공통, z 무한 공통, y 비공통): 관측 `['w','z','y','w']`, 기대 `['z','y','z','y']`.
2. 1차 수정 후 전체 suite **70 tests OK**.
3. 1차 수정본으로 `synthetic-v2-reviewfix`를 실행했다. 동결 v2와 비교하니 rule 라벨만 달랐다(아래 "중간 불일치"). 라벨 회귀 테스트 `test_rule_label_without_common_terms_matches_frozen_v2`를 추가하자 **FAILED (1)**(관측 `['round_robin','round_robin']`)였다.
4. 라벨 로직을 고친 뒤 전체 suite `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py'` → **Ran 71 tests in 11.727s, OK**. 기존 무한 공통 테스트 2개(`..._unknown_support_fall_back_to_round_robin`, `test_infinite_common_interval_is_still_measured`)와 frozen-v1 호환 재현도 통과했다.

## 중간 불일치(시도 1, `synthetic-v2-reviewfix/`, 삭제하지 않고 보존)
- 1차 수정은 "제외 좌표가 없으면 `round_robin` 라벨"로 정했다. 그 결과 공통항이 없는 12개 fixture(critical_path_switch, near_tie, unequal_probe_cost, no_advantage, timeout_censoring, 그 label-swap 4개, three_way_p/q/r_optimal)에서 `cancel_common_round_robin` 정책 run 120개(12×10 seed)의 `probe_rules` 및 `probe_records[].rule` 10,650개가 `cancel_common_round_robin` → `round_robin`으로 바뀌었다.
- probe_sequence·raw batch 표본·비용·인증·종료·선택 등 다른 비시간 필드는 750 run 모두 동일했다. 원인은 라벨 산정 규칙뿐이고 행동 변화는 없다.
- 비교 기록: `artifacts/decision-focused-placement/synthetic-v2-reviewfix/compare-to-synthetic-v2.json`(non-time mismatches 120). 이 디렉터리의 `results.json` 소스 hash는 1차 수정본(`policies_v2.py` `52fab94d…`)이다. 최종 코드와 다르다.

## 최종 실행(`synthetic-v2-final/`)
- 명령(override 없음): Python `subprocess`로 `python3 experiments/decision-focused-placement/run_synthetic_v2.py --output artifacts/decision-focused-placement/synthetic-v2-final`을 실행했다. stdout은 같은 디렉터리의 `stdout.log`에 기록했다(stderr 없음). rc 0이었다.
- 출력: `runs={'expected': 750, 'completed': 750, 'skipped': 0} swap_compared=250 swap_mismatches=0`. 종료 분포는 certified 446 / budget_probes 254 / censored_uncertified 50으로 review 기록과 같다.
- `results.json` sha256 `0e4fbeaebf296fdd6c9c328783b88104fe09cb93365d1282d01ec8fc318db3ae`.
- 동결 원본 `synthetic-v2/results.json` sha256 `d20a941d…f2bb`: developer-report 값과 일치하고 변경되지 않았다.

### 동결 v2 대비 비교 (`synthetic-v2-final/compare-to-synthetic-v2.json`)
- 제외한 시간 필드: `started_at`, `finished_at`, `total_wall_time_s`, `runs[].planner_wall_time_s`, `runs[].probe_records[].planner_time_used_before_s`.
- 750 run 키 집합이 동일하다. **run 단위 비시간 불일치 0건**이다. `probe_records`의 raw `batch` 표본·rule·구간도 포함해 비교했고, probe_records 총 55,925 = 55,925다.
- 최상위 차이는 다음 셋이다.
  - `environment.argv`: output 경로만 다르다.
  - `summary`: 75개 셀의 `planner_wall_time_s_mean`만 다르다(시간 필드).
  - `source_files`: `policies_v2.py`(`8831ea64…` → `986e1d45ff7e2e1c09101129adcad1c5724495d29db6c1aa708732aca31af73b`)와 `test_synthetic_v2.py`(`2143e549…` → `eb0cd68864518ba73b40bb24e5e782ddc2e541f63793ec5510cf88b6b683e017`)만 다르다. 나머지 10개 hash는 동일하다.
- 최종 `results.json`의 12개 source hash는 현재 디스크 파일과 모두 일치한다(최종 71-test 소스로 동결한 실행).

## 한계·알려진 사항
- 15-fixture 행렬에는 "유한 공통항 + 무한 구간" 조합이 없다. 그래서 행렬 결과가 같다는 것은 수정된 경계를 실행하지 않았다는 뜻이다. 수정 경계는 단위 테스트 2개로만 검증했다.
- `synthetic-v2-reviewfix/`는 최종 코드가 아닌 중간 시도의 원시 기록이다. 결론 인용에는 `synthetic-v2-final/`을 사용해야 한다.
- 상한 무한 공통 좌표를 측정 대상으로 남기는 것은 runner 인증 gate 호환을 위한 예외이고, 비용 최적성 주장은 아니다.
