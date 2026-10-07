"""分配规则的 pytest 对拍:小输入穷举 + 随机生成,与暴力枚举参照实现比对。

被测实现 audit.solve 用动态规划;参照实现 brute_force 直接枚举每门课的
全部归属(含不使用),丢弃违反互斥组的组合,按同一规则取最优,两者互相独立。
"""

import itertools
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

import audit

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PY = ROOT / "audit.py"


# ---------------------------------------------------------------- 暴力参照

def brute_force(modules, courses, groups=None, previous=None, locked=frozenset()):
    """独立参照:枚举所有归属组合,依次比计入总学分、改动门数、归属记号序列。

    给出 previous(覆盖全部课程的 {课程id: 模块id或None})与 locked 时,
    锁定课程只枚举其原归属;无任何可行组合(锁定到不合格模块或同组两门
    锁定到模块)时返回 "LOCK_CONFLICT" 哨兵。
    """
    reqs = [m["required"] for m in modules]
    index = {m["id"]: i for i, m in enumerate(modules)}
    ordered = sorted(courses, key=lambda c: c["id"])
    locked = set(locked)

    best = None  # ((-total, changes, seq), combo, total)
    option_lists = []
    for course in ordered:
        if course["id"] in locked:
            option_lists.append([previous[course["id"]]])
        else:
            option_lists.append([None, *course["modules"]])
    feasible = False
    for combo in itertools.product(*option_lists):
        assigned_ids = {
            course["id"] for course, choice in zip(ordered, combo) if choice is not None
        }
        if groups:
            # 互斥约束:同组不得有两门及以上同时获得模块归属。
            if any(
                sum(1 for cid in group if cid in assigned_ids) > 1 for group in groups
            ):
                continue
        # 锁定(或任何枚举)归属必须在当前认可清单内。
        if any(
            choice is not None and choice not in course["modules"]
            for course, choice in zip(ordered, combo)
        ):
            continue
        feasible = True
        sums = [0] * len(modules)
        for course, choice in zip(ordered, combo):
            if choice is not None:
                sums[index[choice]] += course["credits"]
        total = sum(min(req, got) for req, got in zip(reqs, sums))
        changes = 0 if previous is None else sum(
            1 for course, choice in zip(ordered, combo) if choice != previous[course["id"]]
        )
        # 与 audit.py 相同的记号:未使用 (1, "") 排在任何模块 id (0, mid) 之后。
        seq = tuple((1, "") if choice is None else (0, choice) for choice in combo)
        key = (-total, changes, seq)
        if best is None or key < best[0]:
            best = (key, combo, total)

    if not feasible:
        return "LOCK_CONFLICT"
    assignment = {course["id"]: choice for course, choice in zip(ordered, best[1])}
    return render(modules, ordered, assignment, groups, previous)


def render(modules, ordered_courses, assignment, groups=None, previous=None):
    """把归属方案渲染成与 audit.solve 相同结构的结果,便于整体比对。"""
    sums = {m["id"]: 0 for m in modules}
    for course in ordered_courses:
        choice = assignment[course["id"]]
        if choice is not None:
            sums[choice] += course["credits"]
    module_results = []
    for m in modules:
        counted = min(m["required"], sums[m["id"]])
        module_results.append({
            "id": m["id"],
            "required": m["required"],
            "assigned": sums[m["id"]],
            "counted": counted,
            "shortfall": m["required"] - counted,
        })
    result = {
        "status": "PASS" if all(m["shortfall"] == 0 for m in module_results) else "SHORTFALL",
        "total_required": sum(m["required"] for m in modules),
        "total_counted": sum(m["counted"] for m in module_results),
        "assignments": [
            {"course": c["id"], "module": assignment[c["id"]]} for c in ordered_courses
        ],
        "modules": module_results,
    }
    if groups is not None:
        result["exclusiveGroups"] = [
            {
                "courses": list(group),
                "counted": next(
                    (cid for cid in group if assignment[cid] is not None), None
                ),
            }
            for group in groups
        ]
    if previous is not None:
        changes = [
            {"course": c["id"], "from": previous[c["id"]], "to": assignment[c["id"]]}
            for c in ordered_courses
            if previous[c["id"]] != assignment[c["id"]]
        ]
        result["changedCount"] = len(changes)
        result["changes"] = changes
    return result


# ---------------------------------------------------------------- 规则用例

def test_contested_course_defeats_module_order_greedy():
    """一门课被两个模块争抢:按模块顺序贪心会把 C1 分给 M1,导致 M2 缺口误判。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    result = audit.solve(modules, courses)
    assert result["status"] == "PASS"
    assert result["total_counted"] == 4
    assert result["assignments"] == [
        {"course": "C1", "module": "M2"},
        {"course": "C2", "module": "M1"},
    ]
    assert [m["shortfall"] for m in result["modules"]] == [0, 0]


def test_total_credits_enough_but_no_legal_assignment():
    """总学分 6 >= 要求 6,但 4 学分的课只能完整归属一个模块,无法同时填满。"""
    modules = [{"id": "M1", "required": 3}, {"id": "M2", "required": 3}]
    courses = [
        {"id": "C1", "credits": 4, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    result = audit.solve(modules, courses)
    assert result["status"] == "SHORTFALL"
    assert result["total_required"] == 6
    assert result["total_counted"] == 5
    assert result["assignments"] == [
        {"course": "C1", "module": "M2"},
        {"course": "C2", "module": "M1"},
    ]
    shortfalls = {m["id"]: m["shortfall"] for m in result["modules"]}
    assert shortfalls == {"M1": 1, "M2": 0}


def test_tie_break_prefers_lexicographically_smaller_module():
    """计入总学分相同时,按课程 id 顺序取归属模块 id 字典序最小者。"""
    modules = [{"id": "A", "required": 1}, {"id": "B", "required": 1}]
    courses = [{"id": "X", "credits": 1, "modules": ["A", "B"]}]
    result = audit.solve(modules, courses)
    assert result["assignments"] == [{"course": "X", "module": "A"}]
    assert result["total_counted"] == 1
    assert result["status"] == "SHORTFALL"


def test_tie_break_uses_string_order_not_numeric():
    """模块 id 按字符串字典序比较:"M10" < "M2"。"""
    modules = [{"id": "M2", "required": 1}, {"id": "M10", "required": 1}]
    courses = [{"id": "X", "credits": 1, "modules": ["M2", "M10"]}]
    result = audit.solve(modules, courses)
    assert result["assignments"] == [{"course": "X", "module": "M10"}]


def test_unused_is_last_resort_so_overflow_course_still_assigned():
    """未使用排在最后:归属到已满模块也比不使用字典序小,因此 Y 仍归到 A。"""
    modules = [{"id": "A", "required": 1}, {"id": "B", "required": 1}]
    courses = [
        {"id": "X", "credits": 1, "modules": ["A"]},
        {"id": "Y", "credits": 1, "modules": ["A"]},
        {"id": "Z", "credits": 1, "modules": ["B"]},
    ]
    result = audit.solve(modules, courses)
    assert result["status"] == "PASS"
    assert result["assignments"] == [
        {"course": "X", "module": "A"},
        {"course": "Y", "module": "A"},
        {"course": "Z", "module": "B"},
    ]
    a = next(m for m in result["modules"] if m["id"] == "A")
    assert a["assigned"] == 2 and a["counted"] == 1


def test_counted_credits_capped_at_requirement():
    """模块计入学分以要求值封顶,超出部分不计入。"""
    modules = [{"id": "A", "required": 2}, {"id": "B", "required": 1}]
    courses = [
        {"id": "X", "credits": 4, "modules": ["A"]},
        {"id": "Y", "credits": 1, "modules": ["B"]},
    ]
    result = audit.solve(modules, courses)
    assert result["status"] == "PASS"
    a = next(m for m in result["modules"] if m["id"] == "A")
    assert a["assigned"] == 4 and a["counted"] == 2 and a["shortfall"] == 0


def test_module_breakdown_follows_input_order():
    """各模块缺口按输入顺序输出,与 id 排序无关。"""
    modules = [{"id": "Z", "required": 1}, {"id": "A", "required": 1}]
    courses = [{"id": "X", "credits": 1, "modules": ["Z", "A"]}]
    result = audit.solve(modules, courses)
    assert [m["id"] for m in result["modules"]] == ["Z", "A"]
    # 字典序最小归属仍是 "A"。
    assert result["assignments"] == [{"course": "X", "module": "A"}]


# ------------------------------------------------------------- 互斥组规则用例

def test_exclusive_group_reoptimizes_instead_of_deleting():
    """互斥约束与模块封顶联合优化:不能在旧最优分配完成后删去互斥课程。

    无互斥时旧最优为 C1→M1、C2→M2、C3→M1;若只是删去 C2,M2 会空出、
    总学分掉到 2。联合优化把 C3 改派到 M2,总学分仍是 4。
    """
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M2"]},
        {"id": "C3", "credits": 2, "modules": ["M1", "M2"]},
    ]
    assert audit.solve(modules, courses)["assignments"] == [
        {"course": "C1", "module": "M1"},
        {"course": "C2", "module": "M2"},
        {"course": "C3", "module": "M1"},
    ]
    result = audit.solve(modules, courses, [["C1", "C2"]])
    assert result["status"] == "PASS"
    assert result["total_counted"] == 4
    assert result["assignments"] == [
        {"course": "C1", "module": "M1"},
        {"course": "C2", "module": None},
        {"course": "C3", "module": "M2"},
    ]
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C1"}]


def test_exclusive_group_tie_break_prefers_smaller_module_for_survivor():
    """组内二选一并列:幸存组员归属按模块 id 字符串字典序最小("M10" < "M2")。"""
    modules = [{"id": "M2", "required": 2}, {"id": "M10", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M2", "M10"]},
        {"id": "C2", "credits": 2, "modules": ["M2"]},
    ]
    result = audit.solve(modules, courses, [["C1", "C2"]])
    assert result["total_counted"] == 2
    assert result["assignments"] == [
        {"course": "C1", "module": "M10"},
        {"course": "C2", "module": None},
    ]
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C1"}]


def test_exclusive_group_member_cannot_overflow_into_capped_module():
    """学分封顶与互斥联合:无互斥时 Y 会溢出归到 A,入组后只能标为未使用。"""
    modules = [{"id": "A", "required": 1}, {"id": "B", "required": 1}]
    courses = [
        {"id": "X", "credits": 1, "modules": ["A"]},
        {"id": "Y", "credits": 1, "modules": ["A"]},
        {"id": "Z", "credits": 1, "modules": ["B"]},
    ]
    result = audit.solve(modules, courses, [["X", "Y"]])
    assert result["status"] == "PASS"
    assert result["assignments"] == [
        {"course": "X", "module": "A"},
        {"course": "Y", "module": None},
        {"course": "Z", "module": "B"},
    ]
    a = next(m for m in result["modules"] if m["id"] == "A")
    assert a["assigned"] == 1 and a["counted"] == 1


def test_exclusive_group_with_unreachable_requirement():
    """不可达标情形:互斥进一步压缩可计入学分,各模块缺口与总学分如实保留。"""
    modules = [{"id": "M1", "required": 3}, {"id": "M2", "required": 3}]
    courses = [
        {"id": "C1", "credits": 4, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    result = audit.solve(modules, courses, [["C1", "C2"]])
    assert result["status"] == "SHORTFALL"
    assert result["total_required"] == 6
    assert result["total_counted"] == 3
    assert result["assignments"] == [
        {"course": "C1", "module": "M1"},
        {"course": "C2", "module": None},
    ]
    shortfalls = {m["id"]: m["shortfall"] for m in result["modules"]}
    assert shortfalls == {"M1": 0, "M2": 3}
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C1"}]


def test_multiple_exclusive_groups_enforced_jointly():
    """多个互斥组同时生效:每组各计入一门,组间互不影响。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
        {"id": "C3", "credits": 2, "modules": ["M2"]},
        {"id": "C4", "credits": 2, "modules": ["M2"]},
    ]
    groups = [["C1", "C2"], ["C3", "C4"]]
    result = audit.solve(modules, courses, groups)
    assert result["status"] == "PASS"
    assert result["total_counted"] == 4
    assert result["assignments"] == [
        {"course": "C1", "module": "M1"},
        {"course": "C2", "module": None},
        {"course": "C3", "module": "M2"},
        {"course": "C4", "module": None},
    ]
    assert result["exclusiveGroups"] == [
        {"courses": ["C1", "C2"], "counted": "C1"},
        {"courses": ["C3", "C4"], "counted": "C3"},
    ]


def test_three_course_group_counts_exactly_one():
    """三门课的组:恰好一门获得归属,平局时按课程 id 顺序裁决。"""
    modules = [{"id": "A", "required": 2}, {"id": "B", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["A"]},
        {"id": "C2", "credits": 2, "modules": ["A", "B"]},
        {"id": "C3", "credits": 2, "modules": ["B"]},
    ]
    result = audit.solve(modules, courses, [["C1", "C2", "C3"]])
    assert result["total_counted"] == 2
    assert result["assignments"] == [
        {"course": "C1", "module": "A"},
        {"course": "C2", "module": None},
        {"course": "C3", "module": None},
    ]
    assert result["exclusiveGroups"] == [
        {"courses": ["C1", "C2", "C3"], "counted": "C1"}
    ]


def test_empty_exclusive_groups_adds_empty_output_list():
    """传入空数组:等价于无约束,但输出带 "exclusiveGroups": []。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [{"id": "C1", "credits": 2, "modules": ["M1", "M2"]}]
    result = audit.solve(modules, courses, [])
    plain = audit.solve(modules, courses)
    assert "exclusiveGroups" not in plain
    assert result == {**plain, "exclusiveGroups": []}


# --------------------------------------------------- 培养方案调整:归属与锁定

def test_previous_assignment_minimizes_changes_on_tie():
    """二级目标压过字典序:总学分并列时保留上次归属,哪怕字典序更差。"""
    modules = [{"id": "A", "required": 1}, {"id": "B", "required": 1}]
    courses = [{"id": "X", "credits": 1, "modules": ["A", "B"]}]
    plain = audit.solve(modules, courses)
    assert plain["assignments"] == [{"course": "X", "module": "A"}]
    result = audit.solve(modules, courses, None, {"X": "B"})
    assert result["assignments"] == [{"course": "X", "module": "B"}]
    assert result["changedCount"] == 0
    assert result["changes"] == []


def test_previous_assignment_does_not_sacrifice_counted_credits():
    """一级目标优先:为保留归属而少计学分不允许,必须改动以最大化总学分。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    result = audit.solve(modules, courses, None, {"C1": "M1", "C2": "M1"})
    # 保留 C1->M1 只能计 2;改派 C1->M2 计 4,故必须改 1 门。
    assert result["total_counted"] == 4
    assert result["changedCount"] == 1
    assert result["changes"] == [{"course": "C1", "from": "M1", "to": "M2"}]


def test_changes_listed_with_before_and_after_in_course_id_order():
    """输出只列实际变动,给出前后归属,并按课程 id 排序。"""
    modules = [{"id": "A", "required": 1}, {"id": "B", "required": 1}]
    courses = [
        {"id": "C1", "credits": 1, "modules": ["A"]},
        {"id": "C2", "credits": 1, "modules": ["A", "B"]},
    ]
    # 无 revision 时 C2->A(溢出);上次 C2->B 必须改回 A? 不:C2->B 也计满,
    # 总学分相同且零改动,故保留 C2->B,changes 为空。
    kept = audit.solve(modules, courses, None, {"C1": "A", "C2": "B"})
    assert kept["changes"] == [] and kept["changedCount"] == 0
    # 上次 C1->A、C2 未使用:C2 需改派到 B 才能达标(1 门变动,含 null 前后值)。
    result = audit.solve(modules, courses, None, {"C1": "A", "C2": None})
    assert result["status"] == "PASS"
    assert result["changedCount"] == 1
    assert result["changes"] == [{"course": "C2", "from": None, "to": "B"}]


def test_lexicographic_rule_still_breaks_remaining_ties():
    """改动门数也并列时,沿用既有字典序裁决。"""
    modules = [{"id": "M2", "required": 1}, {"id": "M10", "required": 1}]
    courses = [{"id": "X", "credits": 1, "modules": ["M2", "M10"]}]
    # 上次未使用:无论派到哪个模块都恰好改 1 门,字典序选 "M10" < "M2"。
    result = audit.solve(modules, courses, None, {"X": None})
    assert result["assignments"] == [{"course": "X", "module": "M10"}]
    assert result["changes"] == [{"course": "X", "from": None, "to": "M10"}]


def test_locked_course_has_only_its_old_assignment_in_the_joint_search():
    """锁定课在联合搜索中只剩原归属一条转移,其余课程围绕它重新分配。

    不能先求无锁定最优(C1->M1、C3->M2、C2 未使用)再把 C2 挪回 M2:
    那样同组两门同时归属。联合结果必须是 C2->M2(锁定)、C3->M1、C1 未使用。
    """
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M2"]},
        {"id": "C3", "credits": 2, "modules": ["M1", "M2"]},
    ]
    groups = [["C1", "C2"]]
    previous = {"C1": "M1", "C2": "M2", "C3": "M1"}
    result = audit.solve(modules, courses, groups, previous, {"C2"})
    assert result["status"] == "PASS"
    assert result["total_counted"] == 4
    assert result["assignments"] == [
        {"course": "C1", "module": None},
        {"course": "C2", "module": "M2"},
        {"course": "C3", "module": "M1"},
    ]
    # 只改了 C1 一门;锁定的 C2 不出现在 changes 中。
    assert result["changedCount"] == 1
    assert result["changes"] == [{"course": "C1", "from": "M1", "to": None}]
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C2"}]


def test_lock_can_flip_pass_into_shortfall():
    """锁定不保证达标:锁到低价值归属时总学分下降,达标状态翻转。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    plain = audit.solve(modules, courses)
    assert plain["status"] == "PASS"
    # C1 锁到 M1:M1 被 C1/C2 填满到 2(封顶),M2 无课可填 -> SHORTFALL。
    result = audit.solve(
        modules, courses, None, {"C1": "M1", "C2": "M1"}, {"C1"}
    )
    assert result["status"] == "SHORTFALL"
    assert result["total_counted"] == 2
    assert [a["module"] for a in result["assignments"]] == ["M1", "M1"]
    # 锁定课不能动,changes 为空(方案变化来自约束,不来自归属改动)。
    assert result["changes"] == []


def test_locked_null_assignment_is_kept_unused():
    """上次未使用且被正式锁定:该课必须保持未使用,由其余课补救。"""
    modules = [{"id": "A", "required": 1}, {"id": "B", "required": 1}]
    courses = [
        {"id": "X", "credits": 1, "modules": ["A", "B"]},
        {"id": "Y", "credits": 1, "modules": ["B"]},
    ]
    result = audit.solve(
        modules, courses, None, {"X": None, "Y": "B"}, {"X"}
    )
    assert result["assignments"] == [
        {"course": "X", "module": None},
        {"course": "Y", "module": "B"},
    ]
    assert result["status"] == "SHORTFALL"
    assert result["changes"] == []


def test_lock_conflict_ineligible_raises_and_others_remain_free():
    """锁定模块已移出认可清单 -> LockConflict(ineligible),与互斥无关。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    previous = {"C1": "M1", "C2": "M1"}  # C1 上次在 M1,新清单只认可 M2
    with pytest.raises(audit.LockConflict) as exc:
        audit.validate_full({
            "modules": modules,
            "courses": courses,
            "previousAssignments": [
                {"course": cid, "module": previous[cid]} for cid in previous
            ],
            "lockedCourses": ["C1"],
        })
    assert exc.value.ineligible == ["C1"]
    assert exc.value.groups == []


def test_lock_conflict_within_exclusive_group_reports_survivors():
    """同互斥组两门课都锁定到模块 -> 彼此冲突,LOCK_CONFLICT 指明双方。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1"]},
        {"id": "C2", "credits": 2, "modules": ["M2"]},
    ]
    with pytest.raises(audit.LockConflict) as exc:
        audit.validate_full({
            "modules": modules,
            "courses": courses,
            "exclusiveGroups": [{"courses": ["C1", "C2"]}],
            "previousAssignments": [
                {"course": "C1", "module": "M1"},
                {"course": "C2", "module": "M2"},
            ],
            "lockedCourses": ["C1", "C2"],
        })
    assert exc.value.groups == [{"courses": ["C1", "C2"], "locked": ["C1", "C2"]}]
    assert exc.value.ineligible == []


def test_locking_one_group_member_to_null_is_not_a_group_conflict():
    """组内只有一门锁定到模块(另一门锁定为未使用)不构成彼此冲突。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    _, _, groups, previous, locked = audit.validate_full({
        "modules": modules,
        "courses": courses,
        "exclusiveGroups": [{"courses": ["C1", "C2"]}],
        "previousAssignments": [
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": None},
        ],
        "lockedCourses": ["C1", "C2"],
    })
    assert locked == {"C1", "C2"}
    result = audit.solve(modules, courses, groups, previous, locked)
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C1"}]
    assert result["changes"] == []


# ---------------------------------------------------------------- 穷举对拍

def test_exhaustive_small_inputs():
    """2 个模块、至多 3 门课的全部输入组合穷举对拍(332 例)。"""
    course_types = [
        (credits, elig)
        for credits in (1, 2)
        for elig in (("A",), ("B",), ("A", "B"))
    ]
    checked = 0
    for req_a in (1, 2):
        for req_b in (1, 2):
            modules = [{"id": "A", "required": req_a}, {"id": "B", "required": req_b}]
            for n_courses in (1, 2, 3):
                for combo in itertools.combinations_with_replacement(course_types, n_courses):
                    courses = [
                        {"id": f"C{i}", "credits": credits, "modules": list(elig)}
                        for i, (credits, elig) in enumerate(combo)
                    ]
                    assert audit.solve(modules, courses) == brute_force(modules, courses)
                    checked += 1
    assert checked == 4 * (6 + 21 + 56)


def group_configs(course_ids):
    """给定课程 id,枚举全部合法组配置:None(未传)、[](空数组)、单个 2–3 门组。

    课程至多 3 门,放不下两个不共享课程的组,故多组情形由随机对拍覆盖。
    """
    configs = [None, []]
    for size in (2, 3):
        for combo in itertools.combinations(course_ids, size):
            configs.append([list(combo)])
    return configs


def test_exhaustive_small_inputs_with_exclusive_groups():
    """2 模块、至多 3 门课 × 全部合法互斥组配置穷举对拍(1644 例)。

    同一批输入同时覆盖互斥、学分封顶、平局裁决与不可达标情形。
    """
    course_types = [
        (credits, elig)
        for credits in (1, 2)
        for elig in (("A",), ("B",), ("A", "B"))
    ]
    checked = 0
    for req_a in (1, 2):
        for req_b in (1, 2):
            modules = [{"id": "A", "required": req_a}, {"id": "B", "required": req_b}]
            for n_courses in (1, 2, 3):
                for combo in itertools.combinations_with_replacement(course_types, n_courses):
                    courses = [
                        {"id": f"C{i}", "credits": credits, "modules": list(elig)}
                        for i, (credits, elig) in enumerate(combo)
                    ]
                    ids = [c["id"] for c in courses]
                    for groups in group_configs(ids):
                        assert audit.solve(modules, courses, groups) == brute_force(
                            modules, courses, groups
                        )
                        checked += 1
    assert checked == 4 * (6 * 2 + 21 * 3 + 56 * 6)


def _all_lock_subsets(ids):
    return [
        set(combo)
        for k in range(len(ids) + 1)
        for combo in itertools.combinations(ids, k)
    ]


def test_exhaustive_revisions_with_locks_and_groups():
    """穷举对拍三级目标:小课程集合 × 全部组配置 × 全部上次归属 × 全部锁定子集。

    previous 的模块取 None / "A" / "B"(可能不是该课当前认可模块,以覆盖
    锁定到不合格模块的 LOCK_CONFLICT);锁定与互斥组交叉时暴力参照返回
    "LOCK_CONFLICT",validate_full 必须同样识别,其余情形与 DP 整体比对。
    同时统计达标状态翻转(PASS <-> SHORTFALL),要求穷举中确有发生。
    """
    course_types = [
        (credits, elig)
        for credits in (1, 2)
        for elig in (("A",), ("B",), ("A", "B"))
    ]
    checked = 0
    conflicts = 0
    flips = 0
    pass_to_shortfall = 0
    for req_a in (1, 2):
        for req_b in (1, 2):
            modules = [{"id": "A", "required": req_a}, {"id": "B", "required": req_b}]
            for n_courses in (1, 2, 3):
                for combo in itertools.combinations_with_replacement(
                    course_types, n_courses
                ):
                    courses = [
                        {"id": f"C{i}", "credits": credits, "modules": list(elig)}
                        for i, (credits, elig) in enumerate(combo)
                    ]
                    ids = [c["id"] for c in courses]
                    for groups in group_configs(ids):
                        plain = audit.solve(modules, courses, groups)
                        for prev_combo in itertools.product(
                            (None, "A", "B"), repeat=n_courses
                        ):
                            previous = dict(zip(ids, prev_combo))
                            for locked in _all_lock_subsets(ids):
                                expected = brute_force(
                                    modules, courses, groups, previous, locked
                                )
                                if expected == "LOCK_CONFLICT":
                                    conflicts += 1
                                    payload = {
                                        "modules": modules,
                                        "courses": courses,
                                        "previousAssignments": [
                                            {"course": cid, "module": previous[cid]}
                                            for cid in ids
                                        ],
                                        "lockedCourses": sorted(locked),
                                    }
                                    if groups is not None:
                                        payload["exclusiveGroups"] = [
                                            {"courses": g} for g in groups
                                        ]
                                    with pytest.raises(audit.LockConflict):
                                        audit.validate_full(payload)
                                    continue
                                actual = audit.solve(
                                    modules, courses, groups, previous, locked
                                )
                                assert actual == expected, json.dumps(
                                    {
                                        "modules": modules,
                                        "courses": courses,
                                        "groups": groups,
                                        "previous": previous,
                                        "locked": sorted(locked),
                                    },
                                    ensure_ascii=False,
                                )
                                checked += 1
                                if actual["status"] != plain["status"]:
                                    flips += 1
                                    if plain["status"] == "PASS":
                                        pass_to_shortfall += 1
    # 量级固定,显式断言以防枚举被意外收窄;翻转必须真实发生(双向)。
    assert checked == 199888
    assert conflicts == 99776
    assert flips > 0
    assert pass_to_shortfall > 0


MODULE_ID_POOL = ["A", "B", "C", "M1", "M10", "M2", "z"]
COURSE_ID_POOL = ["C1", "C2", "C10", "c3", "X", "Y", "Z", "w"]


def random_instance(rng):
    module_ids = rng.sample(MODULE_ID_POOL, rng.randint(2, 3))
    modules = [{"id": mid, "required": rng.randint(1, 6)} for mid in module_ids]
    course_ids = rng.sample(COURSE_ID_POOL, rng.randint(1, 6))
    courses = [
        {
            "id": cid,
            "credits": rng.randint(1, 4),
            "modules": rng.sample(module_ids, rng.randint(1, len(module_ids))),
        }
        for cid in course_ids
    ]
    return modules, courses


def test_randomized_differential_against_brute_force():
    """随机小输入对拍:id 池含乱序与字符串序陷阱("M10" < "M2")。"""
    rng = random.Random(20260924)
    for _ in range(300):
        modules, courses = random_instance(rng)
        actual = audit.solve(modules, courses)
        expected = brute_force(modules, courses)
        assert actual == expected, json.dumps(
            {"modules": modules, "courses": courses}, ensure_ascii=False
        )


def random_instance_with_groups(rng):
    """在随机输入上叠加互斥组:0–3 组、每组 2–3 门、组间不共享课程。

    约四分之一的情形返回 groups=None(未传字段),其余返回列表(可能为空)。
    """
    modules, courses = random_instance(rng)
    if rng.random() < 0.25:
        return modules, courses, None
    pool = [c["id"] for c in courses]
    rng.shuffle(pool)
    groups = []
    for _ in range(rng.randint(0, 3)):
        size = rng.randint(2, 3)
        if len(pool) < size:
            break
        groups.append([pool.pop() for _ in range(size)])
    return modules, courses, groups


def test_randomized_differential_with_exclusive_groups():
    """随机小输入 + 互斥组对拍:联合优化的结果须与独立暴力枚举一致。"""
    rng = random.Random(20260926)
    for _ in range(300):
        modules, courses, groups = random_instance_with_groups(rng)
        actual = audit.solve(modules, courses, groups)
        expected = brute_force(modules, courses, groups)
        assert actual == expected, json.dumps(
            {"modules": modules, "courses": courses, "exclusiveGroups": groups},
            ensure_ascii=False,
        )


def random_revision(rng):
    """随机培养方案调整:0–3 个互斥组 + 覆盖全部课程的上次归属 + 随机锁定。

    上次归属允许引用已声明但当前不认可该课的模块(仅锁定时才冲突),
    以便同时覆盖 LOCK_CONFLICT 与不锁定时重新分配两条路径。
    """
    modules, courses = random_instance(rng)
    mids = [m["id"] for m in modules]
    pool = [c["id"] for c in courses]
    rng.shuffle(pool)
    groups = []
    for _ in range(rng.randint(0, 3)):
        size = rng.randint(2, 3)
        if len(pool) < size:
            break
        groups.append([pool.pop() for _ in range(size)])
    previous = {c["id"]: rng.choice([None, *mids]) for c in courses}
    locked = {c["id"] for c in courses if rng.random() < 0.35}
    return modules, courses, groups, previous, locked


def test_randomized_differential_with_revisions_and_locks():
    """随机调整对拍:三级目标、组占用与锁定在同一搜索中的结果须同暴力枚举一致。"""
    rng = random.Random(20261007)
    for _ in range(200):
        modules, courses, groups, previous, locked = random_revision(rng)
        expected = brute_force(modules, courses, groups, previous, locked)
        if expected == "LOCK_CONFLICT":
            payload = {
                "modules": modules,
                "courses": courses,
                "exclusiveGroups": [{"courses": g} for g in groups],
                "previousAssignments": [
                    {"course": cid, "module": previous[cid]}
                    for cid in sorted(previous)
                ],
                "lockedCourses": sorted(locked),
            }
            with pytest.raises(audit.LockConflict):
                audit.validate_full(payload)
            continue
        actual = audit.solve(modules, courses, groups, previous, locked)
        assert actual == expected, json.dumps(
            {
                "modules": modules,
                "courses": courses,
                "exclusiveGroups": groups,
                "previous": previous,
                "locked": sorted(locked),
            },
            ensure_ascii=False,
        )


# ---------------------------------------------------------------- 输入校验

def base_payload():
    return {
        "modules": [{"id": "M1", "required": 3}, {"id": "M2", "required": 2}],
        "courses": [{"id": "C1", "credits": 2, "modules": ["M1", "M2"]}],
    }


def _invalid_payloads():
    cases = {}

    def add(name, mutate):
        payload = base_payload()
        mutate(payload)
        cases[name] = payload

    add("unknown module in course", lambda p: p["courses"][0].update(modules=["M1", "NOPE"]))
    add("duplicate module id", lambda p: p["modules"].append({"id": "M1", "required": 1}))
    add("duplicate course id",
        lambda p: p["courses"].append({"id": "C1", "credits": 1, "modules": ["M2"]}))
    add("extra top-level field", lambda p: p.update(semester=1))
    add("missing courses", lambda p: p.pop("courses"))
    add("extra module field", lambda p: p["modules"][0].update(label="x"))
    add("missing module required", lambda p: p["modules"][0].pop("required"))
    add("extra course field", lambda p: p["courses"][0].update(note="x"))
    add("missing course modules", lambda p: p["courses"][0].pop("modules"))
    add("only one module", lambda p: p["modules"].pop())
    add("six modules",
        lambda p: p["modules"].extend({"id": f"M{i}", "required": 1} for i in range(3, 7)))
    add("no courses", lambda p: p["courses"].clear())
    add("required zero", lambda p: p["modules"][0].update(required=0))
    add("required eleven", lambda p: p["modules"][0].update(required=11))
    add("required string", lambda p: p["modules"][0].update(required="3"))
    add("required bool", lambda p: p["modules"][0].update(required=True))
    add("credits zero", lambda p: p["courses"][0].update(credits=0))
    add("credits five", lambda p: p["courses"][0].update(credits=5))
    add("credits float", lambda p: p["courses"][0].update(credits=2.0))
    add("credits bool", lambda p: p["courses"][0].update(credits=False))
    add("empty eligibility", lambda p: p["courses"][0].update(modules=[]))
    add("eligibility not a list", lambda p: p["courses"][0].update(modules="M1"))
    add("duplicate module in eligibility", lambda p: p["courses"][0].update(modules=["M1", "M1"]))
    add("non-ascii module id", lambda p: p["modules"][0].update(id="模块"))
    add("empty module id", lambda p: p["modules"][0].update(id=""))
    add("numeric course id", lambda p: p["courses"][0].update(id=7))
    cases["top level list"] = []
    cases["top level string"] = "hello"
    return cases


INVALID_PAYLOADS = _invalid_payloads()


def test_validate_accepts_base_payload():
    modules, courses, groups = audit.validate(base_payload())
    assert [m["id"] for m in modules] == ["M1", "M2"]
    assert [c["id"] for c in courses] == ["C1"]
    # 未传 exclusiveGroups 时 groups 为 None。
    assert groups is None


@pytest.mark.parametrize("payload", INVALID_PAYLOADS.values(), ids=list(INVALID_PAYLOADS))
def test_validate_rejects_invalid_input(payload):
    with pytest.raises(audit.InputError):
        audit.validate(payload)


# ------------------------------------------------------------- 互斥组输入校验

def group_payload():
    return {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
            {"id": "C2", "credits": 2, "modules": ["M2"]},
            {"id": "C3", "credits": 2, "modules": ["M1", "M2"]},
        ],
        "exclusiveGroups": [{"courses": ["C1", "C2"]}],
    }


def _invalid_group_payloads():
    cases = {}

    def add(name, mutate):
        payload = group_payload()
        mutate(payload)
        cases[name] = payload

    add("group unknown course",
        lambda p: p["exclusiveGroups"][0].update(courses=["C1", "NOPE"]))
    add("group duplicate course",
        lambda p: p["exclusiveGroups"][0].update(courses=["C1", "C1"]))
    add("course shared across groups",
        lambda p: p.update(exclusiveGroups=[
            {"courses": ["C1", "C2"]}, {"courses": ["C2", "C3"]},
        ]))
    add("group extra field", lambda p: p["exclusiveGroups"][0].update(label="x"))
    add("group missing courses", lambda p: p["exclusiveGroups"][0].clear())
    add("group not an object", lambda p: p.update(exclusiveGroups=[["C1", "C2"]]))
    add("group too small", lambda p: p["exclusiveGroups"][0].update(courses=["C1"]))
    add("group too large",
        lambda p: p["exclusiveGroups"][0].update(courses=["C1", "C2", "C3", "C1"]))
    add("too many groups", lambda p: p.update(exclusiveGroups=p["exclusiveGroups"] * 4))
    add("exclusiveGroups not a list", lambda p: p.update(exclusiveGroups="C1"))
    add("group course id not a string",
        lambda p: p["exclusiveGroups"][0].update(courses=["C1", 7]))
    return cases


INVALID_GROUP_PAYLOADS = _invalid_group_payloads()


def test_validate_accepts_exclusive_groups():
    modules, courses, groups = audit.validate(group_payload())
    assert groups == [["C1", "C2"]]
    # 空数组合法:零个互斥组。
    payload = group_payload()
    payload["exclusiveGroups"] = []
    assert audit.validate(payload)[2] == []


@pytest.mark.parametrize(
    "payload", INVALID_GROUP_PAYLOADS.values(), ids=list(INVALID_GROUP_PAYLOADS)
)
def test_validate_rejects_invalid_exclusive_groups(payload):
    with pytest.raises(audit.InputError):
        audit.validate(payload)


# ------------------------------------------------------- 调整配置的输入校验

def revision_payload():
    return {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
            {"id": "C2", "credits": 2, "modules": ["M2"]},
            {"id": "C3", "credits": 2, "modules": ["M1"]},
        ],
        "previousAssignments": [
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": "M2"},
            {"course": "C3", "module": None},
        ],
        "lockedCourses": ["C1"],
    }


def _invalid_revision_payloads():
    cases = {}

    def add(name, mutate):
        payload = revision_payload()
        mutate(payload)
        cases[name] = payload

    add("previous not a list", lambda p: p.update(previousAssignments={"C1": "M1"}))
    add("previous missing course coverage",
        lambda p: p["previousAssignments"].pop())
    add("previous duplicate course",
        lambda p: p.update(previousAssignments=[
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": "M2"},
            {"course": "C3", "module": None},
            {"course": "C3", "module": "M1"},
        ]))
    add("previous unknown course",
        lambda p: p["previousAssignments"][2].update(course="C9"))
    add("previous unknown module",
        lambda p: p["previousAssignments"][0].update(module="M9"))
    add("previous non-string module",
        lambda p: p["previousAssignments"][0].update(module=7))
    add("previous non-string course",
        lambda p: p["previousAssignments"][0].update(course=7))
    add("previous entry extra field",
        lambda p: p["previousAssignments"][0].update(note="x"))
    add("previous entry missing module",
        lambda p: p["previousAssignments"][0].pop("module"))
    add("previous entry not an object",
        lambda p: p.update(previousAssignments=[["C1", "M1"], [], []]))
    add("locked not a list", lambda p: p.update(lockedCourses="C1"))
    add("locked unknown course", lambda p: p.update(lockedCourses=["C9"]))
    add("locked duplicate course", lambda p: p.update(lockedCourses=["C1", "C1"]))
    add("locked non-string course", lambda p: p.update(lockedCourses=[7]))
    add("locked too many", lambda p: p.update(lockedCourses=["C1"] * 17))
    add("locked without previous", lambda p: p.pop("previousAssignments"))
    add("top-level extra field", lambda p: p.update(term="2026"))
    return cases


INVALID_REVISION_PAYLOADS = _invalid_revision_payloads()


def test_validate_full_accepts_revision():
    modules, courses, groups, previous, locked = audit.validate_full(
        revision_payload()
    )
    assert groups is None
    assert previous == {"C1": "M1", "C2": "M2", "C3": None}
    assert locked == {"C1"}


def test_validate_full_empty_locked_list_without_previous_is_valid():
    """空的 lockedCourses 不依赖 previousAssignments。"""
    payload = base_payload()
    payload["lockedCourses"] = []
    _, _, _, previous, locked = audit.validate_full(payload)
    assert previous is None and locked == set()


@pytest.mark.parametrize(
    "payload", INVALID_REVISION_PAYLOADS.values(), ids=list(INVALID_REVISION_PAYLOADS)
)
def test_validate_rejects_invalid_revision(payload):
    with pytest.raises(audit.InputError):
        audit.validate_full(payload)


def test_lock_conflict_is_distinct_from_input_error():
    """资格冲突是结构合法后的独立失败类型,不应当作 InputError。"""
    payload = revision_payload()
    payload["courses"][0]["modules"] = ["M2"]  # C1 锁定在 M1 但新清单只认可 M2
    with pytest.raises(audit.LockConflict) as exc:
        audit.validate_full(payload)
    assert exc.value.ineligible == ["C1"]


# ---------------------------------------------------------------- 命令行

def run_cli(args=(), stdin_text=None):
    return subprocess.run(
        [sys.executable, str(AUDIT_PY), *args],
        input=stdin_text, capture_output=True, text=True, timeout=60,
    )


def test_cli_reads_stdin_and_writes_json():
    payload = base_payload()
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == audit.solve(*audit.validate(payload))


def test_cli_reads_file_argument(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(base_payload()), encoding="utf-8")
    proc = run_cli([str(path)])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] in {"PASS", "SHORTFALL"}


def test_cli_rejects_non_json():
    proc = run_cli(stdin_text="this is not json {")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "error" in json.loads(proc.stderr)


def test_cli_rejects_invalid_document():
    payload = base_payload()
    payload["courses"][0]["modules"] = ["M1", "GHOST"]
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "unknown module" in json.loads(proc.stderr)["error"]


def test_cli_rejects_missing_file():
    proc = run_cli(["/nonexistent/input.json"])
    assert proc.returncode == 2
    assert "error" in json.loads(proc.stderr)


def test_cli_output_unchanged_without_exclusive_groups():
    """未传新字段时,stdout 逐字节等于旧格式输出,且不含 exclusiveGroups 键。"""
    payload = base_payload()
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    expected = json.dumps(
        audit.solve(*audit.validate(payload)), ensure_ascii=False, indent=2
    ) + "\n"
    assert proc.stdout == expected
    assert "exclusiveGroups" not in json.loads(proc.stdout)


def test_cli_with_exclusive_groups():
    payload = group_payload()
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result == audit.solve(*audit.validate(payload))
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C1"}]
    assert result["status"] == "PASS"
    assert result["total_counted"] == 4


def test_cli_rejects_invalid_exclusive_groups():
    """组内引用未知课程:整份拒绝,退出码 2 且 stdout 不产生方案。"""
    payload = group_payload()
    payload["exclusiveGroups"][0]["courses"] = ["C1", "GHOST"]
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "unknown course" in json.loads(proc.stderr)["error"]


def test_cli_revision_outputs_changes_and_matches_solve():
    """携带上次归属:正常审核,输出含 changedCount / changes。"""
    payload = revision_payload()
    payload.pop("lockedCourses")
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result == audit.solve(*audit.validate_full(payload))
    assert "changedCount" in result and "changes" in result
    assert "exclusiveGroups" not in result


def test_cli_lock_conflict_returns_3_without_partial_assignment():
    """LOCK_CONFLICT:退出码 3、stdout 为空、stderr 给出明确结构。"""
    payload = revision_payload()
    payload["courses"][0]["modules"] = ["M2"]  # C1 锁在 M1 但新清单只认可 M2
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 3
    assert proc.stdout == ""
    err = json.loads(proc.stderr)
    assert err["error"] == "LOCK_CONFLICT"
    assert err["ineligible"] == ["C1"]
    assert err["groups"] == []


def test_cli_lock_conflict_within_exclusive_group_returns_3():
    """互斥组内两门锁定课彼此冲突:退出码 3,groups 指明冲突组员。"""
    payload = {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1"]},
            {"id": "C2", "credits": 2, "modules": ["M2"]},
        ],
        "exclusiveGroups": [{"courses": ["C1", "C2"]}],
        "previousAssignments": [
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": "M2"},
        ],
        "lockedCourses": ["C1", "C2"],
    }
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 3
    assert proc.stdout == ""
    err = json.loads(proc.stderr)
    assert err["error"] == "LOCK_CONFLICT"
    assert err["groups"] == [{"courses": ["C1", "C2"], "locked": ["C1", "C2"]}]


def test_cli_malformed_revision_still_returns_2():
    """上次归属缺项属于非法输入(2),而非锁定冲突(3)。"""
    payload = revision_payload()
    payload["previousAssignments"].pop()
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "error" in json.loads(proc.stderr)


def test_cli_locked_feasible_revision_lists_only_actual_changes():
    """锁定可行时方案正常输出,changes 列出前后归属且不含锁定课。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M2"]},
        {"id": "C3", "credits": 2, "modules": ["M1", "M2"]},
    ]
    payload = {
        "modules": modules,
        "courses": courses,
        "exclusiveGroups": [{"courses": ["C1", "C2"]}],
        "previousAssignments": [
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": "M2"},
            {"course": "C3", "module": "M1"},
        ],
        "lockedCourses": ["C2"],
    }
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "PASS"
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C2"}]
    assert result["changedCount"] == 1
    assert result["changes"] == [{"course": "C1", "from": "M1", "to": None}]
