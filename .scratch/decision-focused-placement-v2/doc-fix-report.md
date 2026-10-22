# doc-fix report (review round2, 2026-10-21)

## 범위
- 수정 파일: `experiments/decision-focused-placement/README.md` 83행 1개 문장(같은 bullet 안) 뿐. 코드·테스트·artifact·설정·다른 문서는 건드리지 않음.
- 테스트/벤치마크 재실행 없음(문서만 변경).

## 근거 (`policies_v2.py` `CancelCommonRoundRobin.choose`, `synthetic.py:90` `any_infinite`)
- `targets`가 비면(모든 사용 좌표가 유한 공통) → 사용 좌표 전체, rule `round_robin`.
- `obs.any_infinite() and not excluded` → rule `round_robin`. `any_infinite()`는 **사용 좌표** 중 상한이 무한인 구간이 있을 때만 참.
- 그 외(제외 좌표 없음 + 사용 구간 모두 유한 포함) → rule `cancel_common_round_robin` 유지.

## 문장 diff (word-diff)
```
제외할 좌표가 [-없으면-]{+없고 사용 좌표 중 상한이 무한인 구간이 있으면+} v1 `round_robin`과 같은 순환(rule `round_robin`)이고,
{+사용 구간이 모두 유한하면 공통 좌표가 없어도 rule `cancel_common_round_robin`을 유지한다.+}
모든 사용 좌표가 유한 [-공통인데 인증되지 않으면-]{+공통이라 측정 대상이 비면(인증되지 않은 경우)+} 사용 좌표 전체 [-`round_robin`으로-]{+순환(rule `round_robin`)으로+} 되돌아간다.
```

## 레벨
- 문서 수정만: 코드 동작 변경 없음, 테스트 재실행 없음, commit 없음.
