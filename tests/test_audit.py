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
    """独立参照:枚举所有归属组合,三级目标依次比:
    计入总学分、相对上次归属的改动门数、归属记号序列。

    锁定课只枚举其上次归属;调用方须先用 audit.find_lock_conflicts 排除
    LOCK_CONFLICT(无合法组合)情形,与 audit.solve 的行为边界保持一致。
    """
    reqs = [m["required"] for m in modules]
    index = {m["id"]: i for i, m in enumerate(modules)}
    ordered = sorted(courses, key=lambda c: c["id"])

    best = None  # (key, combo)
    option_lists = []
    for course in ordered:
        if course["id"] in locked:
            option_lists.append([previous[course["id"]]])
        else:
            option_lists.append([None, *course["modules"]])
    for combo in itertools.product(*option_lists):
        if groups:
            # 互斥约束:同组不得有两门及以上同时获得模块归属。
            assigned_ids = {
                course["id"] for course, choice in zip(ordered, combo) if choice is not None
            }
            if any(
                sum(1 for cid in group if cid in assigned_ids) > 1 for group in groups
            ):
                continue
        sums = [0] * len(modules)
        for course, choice in zip(ordered, combo):
            if choice is not None:
                sums[index[choice]] += course["credits"]
        total = sum(min(req, got) for req, got in zip(reqs, sums))
        # 与 audit.py 相同的记号:未使用 (1, "") 排在任何模块 id (0, mid) 之后。
        seq = tuple((1, "") if choice is None else (0, choice) for choice in combo)
        changes = (
            -1
            if previous is None
            else sum(
                choice != previous[course["id"]]
                for course, choice in zip(ordered, combo)
            )
        )
        key = (-total, changes, seq)
        if best is None or key < best[0]:
            best = (key, combo)

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
    if previous is not None:
        result["changes"] = [
            {"course": c["id"], "from": previous[c["id"]], "to": assignment[c["id"]]}
            for c in ordered_courses
            if previous[c["id"]] != assignment[c["id"]]
        ]
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


# ------------------------------------------------- 上次归属 / 锁定规则用例

def test_previous_assignment_kept_on_total_tie():
    """总学分并列时最小化改动:旧归属 X->B 被保留,哪怕字典序上 X->A 更小。"""
    modules = [{"id": "A", "required": 2}, {"id": "B", "required": 1}]
    courses = [
        {"id": "X", "credits": 2, "modules": ["A", "B"]},
        {"id": "Y", "credits": 1, "modules": ["A"]},
    ]
    # 无上次归属:字典序取 X->A。
    assert audit.solve(modules, courses)["assignments"] == [
        {"course": "X", "module": "A"},
        {"course": "Y", "module": "A"},
    ]
    previous = {"X": "B", "Y": "A"}
    result = audit.solve(modules, courses, None, previous, frozenset())
    assert result["total_counted"] == 2
    assert result["assignments"] == [
        {"course": "X", "module": "B"},
        {"course": "Y", "module": "A"},
    ]
    assert result["changes"] == []


def test_total_credits_outweighs_keeping_previous():
    """第一级优先:为了多计学分必须改门,且 changes 如实列出前后归属。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    previous = {"C1": "M1", "C2": "M1"}  # 旧方案只计 2(全堆 M1)
    result = audit.solve(modules, courses, None, previous, frozenset())
    assert result["total_counted"] == 4
    assert result["assignments"] == [
        {"course": "C1", "module": "M2"},
        {"course": "C2", "module": "M1"},
    ]
    assert result["changes"] == [{"course": "C1", "from": "M1", "to": "M2"}]


def test_changes_includes_null_transitions_both_directions():
    """改动门数对 null<->模块 与模块<->模块一视同仁,changes 逐门列前后值。"""
    modules = [{"id": "A", "required": 2}, {"id": "B", "required": 2}]
    courses = [{"id": "X", "credits": 2, "modules": ["A", "B"]}]
    # 上次未使用 -> 这次必须归属才能拿学分(第一级),改动计 1 门。
    result = audit.solve(modules, courses, None, {"X": None}, frozenset())
    assert result["changes"] == [{"course": "X", "from": None, "to": "A"}]


def test_locked_course_kept_even_when_it_flips_pass_to_shortfall():
    """锁定课保持原归属:可达标的最优被禁,达标状态翻转为 SHORTFALL。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    previous = {"C1": "M1", "C2": "M1"}
    unlocked = audit.solve(modules, courses, None, previous, frozenset())
    assert unlocked["status"] == "PASS" and unlocked["total_counted"] == 4
    locked = audit.solve(modules, courses, None, previous, frozenset(["C1"]))
    assert locked["status"] == "SHORTFALL"
    assert locked["total_counted"] == 2
    # C1 锁定;C2->M1 与上次一致且同为总分 2 下 0 改动,故整体无变化门。
    assert locked["assignments"] == [
        {"course": "C1", "module": "M1"},
        {"course": "C2", "module": "M1"},
    ]
    assert locked["changes"] == []


def test_locked_course_is_reoptimized_around_jointly():
    """锁定、互斥组占用、模块封顶同一次搜索:不能先求旧最优再把锁定课挪回。

    无锁旧最优 C1->M1、C2->M2、C3->M1;C1/C2 互斥。锁定 C1->M1 后,
    联合优化必须把 C2 置未使用、C3 改派 M2,总学分维持 4(PASS)。
    """
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M2"]},
        {"id": "C3", "credits": 2, "modules": ["M1", "M2"]},
    ]
    groups = [["C1", "C2"]]
    previous = {"C1": "M1", "C2": "M2", "C3": "M1"}
    result = audit.solve(modules, courses, groups, previous, frozenset(["C1"]))
    assert result["status"] == "PASS"
    assert result["total_counted"] == 4
    assert result["assignments"] == [
        {"course": "C1", "module": "M1"},
        {"course": "C2", "module": None},
        {"course": "C3", "module": "M2"},
    ]
    assert result["changes"] == [
        {"course": "C2", "from": "M2", "to": None},
        {"course": "C3", "from": "M1", "to": "M2"},
    ]
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C1"}]


def test_locked_unused_member_leaves_group_slot_for_others():
    """锁定为未使用的组员不占组名额:同组另一门仍可获得归属。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    groups = [["C1", "C2"]]
    previous = {"C1": None, "C2": "M1"}
    result = audit.solve(modules, courses, groups, previous, frozenset(["C1"]))
    assert result["assignments"] == [
        {"course": "C1", "module": None},
        {"course": "C2", "module": "M1"},
    ]
    assert result["exclusiveGroups"] == [{"courses": ["C1", "C2"], "counted": "C2"}]
    assert result["changes"] == []


def test_lock_conflict_lost_eligibility():
    """锁定课的原归属模块已不在当前认可清单:LOCK_CONFLICT,不产生方案。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1"]},
    ]
    # 未锁定时同样的上次归属只是必改项,不报错。
    previous = {"C1": "M1", "C2": "M2"}
    assert audit.solve(modules, courses, None, previous, frozenset())["status"]
    with pytest.raises(audit.LockConflict) as exc:
        audit.solve(modules, courses, None, previous, frozenset(["C2"]))
    assert exc.value.conflicts == [
        {"course": "C2", "reason": "ineligible", "module": "M2"}
    ]


def test_lock_conflict_two_assigned_members_in_same_group():
    """同组两门锁定课的旧归属都非空:互斥无法同时满足,LOCK_CONFLICT。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
        {"id": "C2", "credits": 2, "modules": ["M1", "M2"]},
    ]
    groups = [["C1", "C2"]]
    previous = {"C1": "M1", "C2": "M2"}
    # 只锁一门不冲突。
    audit.solve(modules, courses, groups, previous, frozenset(["C1"]))
    with pytest.raises(audit.LockConflict) as exc:
        audit.solve(modules, courses, groups, previous, frozenset(["C1", "C2"]))
    assert exc.value.conflicts == [{
        "reason": "exclusiveGroup",
        "courses": ["C1", "C2"],
        "locked": ["C1", "C2"],
    }]


def test_lock_conflict_three_course_group_lists_all_locked():
    """三门组里两门锁定归属即冲突,冲突项按 id 排序列出。"""
    modules = [{"id": "A", "required": 2}, {"id": "B", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["A"]},
        {"id": "C2", "credits": 2, "modules": ["A", "B"]},
        {"id": "C3", "credits": 2, "modules": ["B"]},
    ]
    groups = [["C1", "C2", "C3"]]
    previous = {"C1": "A", "C2": "B", "C3": None}
    with pytest.raises(audit.LockConflict) as exc:
        audit.solve(modules, courses, groups, previous, frozenset(["C1", "C2"]))
    assert exc.value.conflicts[0]["locked"] == ["C1", "C2"]


def test_locked_null_is_always_eligible_and_never_conflicts():
    """锁定为未使用不要求资格,也不占用互斥组名额。"""
    modules = [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}]
    courses = [
        {"id": "C1", "credits": 2, "modules": ["M1"]},
        {"id": "C2", "credits": 2, "modules": ["M2"]},
    ]
    groups = [["C1", "C2"]]
    previous = {"C1": None, "C2": None}
    result = audit.solve(
        modules, courses, groups, previous, frozenset(["C1", "C2"])
    )
    assert result["assignments"] == [
        {"course": "C1", "module": None},
        {"course": "C2", "module": None},
    ]
    assert result["changes"] == []
    assert result["exclusiveGroups"] == [
        {"courses": ["C1", "C2"], "counted": None}
    ]


def test_empty_locked_set_with_previous_still_minimizes_changes():
    """提供 previousAssignments 但 lockedCourses 为空:只启用二级目标,不锁定。"""
    modules = [{"id": "A", "required": 2}, {"id": "B", "required": 1}]
    courses = [
        {"id": "X", "credits": 2, "modules": ["A", "B"]},
        {"id": "Y", "credits": 1, "modules": ["A"]},
    ]
    previous = {"X": "B", "Y": "A"}
    payload = {
        "modules": modules,
        "courses": courses,
        "previousAssignments": [
            {"course": "X", "module": "B"}, {"course": "Y", "module": "A"}
        ],
        "lockedCourses": [],
    }
    prev, locked = audit.validate_revision(payload, modules, courses)
    assert locked == frozenset()
    result = audit.solve(modules, courses, None, prev, locked)
    assert [a["module"] for a in result["assignments"]] == ["B", "A"]
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


# ------------------------------------------- 上次归属 / 锁定穷举与随机对拍

PREV_CHOICES = (None, "A", "B")


def all_previous(course_ids):
    """枚举结构合法的上次归属:每门课取未使用或任一已声明模块(可能不在其认可
    清单内——结构仍合法,仅在锁定时构成 ineligible 冲突)。"""
    return [
        dict(zip(course_ids, combo))
        for combo in itertools.product(PREV_CHOICES, repeat=len(course_ids))
    ]


def lock_subsets(course_ids):
    """全部锁定子集(含空集),用于锁定 × 互斥组交叉穷举。"""
    return [frozenset(combo) for r in range(len(course_ids) + 1)
            for combo in itertools.combinations(course_ids, r)]


def _check_one_revision_case(modules, courses, groups, previous, locked, counters):
    """对单个 (实例 × 上次归属 × 锁定集合) 做对拍,并登记覆盖计数。"""
    conflicts = audit.find_lock_conflicts(courses, groups, previous, locked)
    if conflicts:
        with pytest.raises(audit.LockConflict):
            audit.solve(modules, courses, groups, previous, locked)
        counters["conflict"] += 1
        if any(c["reason"] == "ineligible" for c in conflicts):
            counters["conflict_ineligible"] += 1
        if any(c["reason"] == "exclusiveGroup" for c in conflicts):
            counters["conflict_group"] += 1
        return

    actual = audit.solve(modules, courses, groups, previous, locked)
    expected = brute_force(modules, courses, groups, previous, locked)
    assert actual == expected, json.dumps({
        "modules": modules, "courses": courses, "groups": groups,
        "previous": previous, "locked": sorted(locked),
    }, ensure_ascii=False)

    if locked:
        unlocked = audit.solve(modules, courses, groups, previous, frozenset())
        for cid in locked:
            assert actual_assign(actual, cid) == previous[cid]
        counters["locked"] += 1
        if unlocked["status"] == "PASS" and actual["status"] == "SHORTFALL":
            counters["status_flip"] += 1
        if unlocked["total_counted"] > actual["total_counted"]:
            counters["costly_lock"] += 1


def actual_assign(result, cid):
    return next(a["module"] for a in result["assignments"] if a["course"] == cid)


def test_exhaustive_revision_locks_cross_exclusive_groups():
    """2 模块 3 门课 × 互斥组配置 × 全部上次归属 × 全部锁定子集穷举对拍。

    覆盖:三级目标(总学分、改动门数、字典序)、互斥组与锁定交叉、
    锁定导致的达标状态翻转(PASS->SHORTFALL)与学分损失、
    LOCK_CONFLICT 的两种原因(失去资格 / 同组双锁)。
    """
    course_types = [
        (credits, elig)
        for credits in (1, 2)
        for elig in (("A",), ("B",), ("A", "B"))
    ]
    # 具有代表性的多重集下标组合(从 6 种课型中可重复取 3 门),兼顾全弹性、
    # 单一资格、封顶溢出与争抢。
    curated = [
        (2, 2, 2), (5, 5, 5), (0, 1, 2), (3, 4, 5),
        (0, 4, 2), (3, 1, 5), (0, 0, 5), (3, 3, 2),
    ]
    counters = {
        "conflict": 0, "conflict_ineligible": 0, "conflict_group": 0,
        "locked": 0, "status_flip": 0, "costly_lock": 0,
    }
    for req_a, req_b in ((1, 1), (2, 2)):
        modules = [{"id": "A", "required": req_a}, {"id": "B", "required": req_b}]
        for idxs in curated:
            courses = [
                {"id": f"C{i}", "credits": course_types[j][0],
                 "modules": list(course_types[j][1])}
                for i, j in enumerate(idxs)
            ]
            ids = [c["id"] for c in courses]
            for groups in (None, [], [["C0", "C1"]], [["C0", "C1", "C2"]]):
                for previous in all_previous(ids):
                    for locked in lock_subsets(ids):
                        _check_one_revision_case(
                            modules, courses, groups, previous, locked, counters
                        )
    # 交叉与翻转必须真实发生,而不是全部悄悄走同一分支。
    assert counters["conflict_ineligible"] > 0
    assert counters["conflict_group"] > 0
    assert counters["status_flip"] > 0
    assert counters["costly_lock"] > 0
    assert counters["locked"] > 0


def test_exhaustive_two_course_revision_all_instances():
    """2 门课的全部 21 种课型多重集 × 4 种要求 × 上次归属/锁定全组合对拍。"""
    course_types = [
        (credits, elig)
        for credits in (1, 2)
        for elig in (("A",), ("B",), ("A", "B"))
    ]
    counters = {"conflict": 0, "status_flip": 0,
                "conflict_ineligible": 0, "conflict_group": 0,
                "locked": 0, "costly_lock": 0}
    for req_a in (1, 2):
        for req_b in (1, 2):
            modules = [{"id": "A", "required": req_a}, {"id": "B", "required": req_b}]
            for combo in itertools.combinations_with_replacement(range(6), 2):
                courses = [
                    {"id": f"C{i}", "credits": course_types[j][0],
                     "modules": list(course_types[j][1])}
                    for i, j in enumerate(combo)
                ]
                ids = [c["id"] for c in courses]
                for groups in (None, [["C0", "C1"]]):
                    for previous in all_previous(ids):
                        for locked in lock_subsets(ids):
                            _check_one_revision_case(
                                modules, courses, groups, previous, locked, counters
                            )
    assert counters["status_flip"] > 0


def random_revision_instance(rng):
    """在随机实例上叠加互斥组、上次归属与锁定集合。"""
    modules, courses = random_instance(rng)
    mids = [m["id"] for m in modules]
    pool = [c["id"] for c in courses]
    rng.shuffle(pool)
    groups = []
    grouped = set()
    for _ in range(rng.randint(0, 3)):
        size = rng.randint(2, 3)
        if len(pool) < size:
            break
        members = [pool.pop() for _ in range(size)]
        grouped.update(members)
        groups.append(members)
    if rng.random() < 0.2:
        groups = None  # 未传互斥组
    # 上次归属:多取已声明模块(可能不在该课认可清单内),约 1/4 取未使用。
    previous = {}
    for c in courses:
        if rng.random() < 0.25:
            previous[c["id"]] = None
        else:
            previous[c["id"]] = rng.choice(mids)
    # 锁定集合:覆盖空集、全锁与随机子集。
    ids = [c["id"] for c in courses]
    pick = rng.randint(0, 3)
    if pick == 0:
        locked = frozenset()
    elif pick == 1:
        locked = frozenset(ids)
    else:
        locked = frozenset(rng.sample(ids, rng.randint(1, len(ids))))
    return modules, courses, groups, previous, locked


def test_randomized_differential_with_revision_and_locks():
    """随机实例 × 互斥组 × 上次归属 × 锁定对拍三级目标与 LOCK_CONFLICT 边界。"""
    rng = random.Random(20261006)
    for _ in range(400):
        modules, courses, groups, previous, locked = random_revision_instance(rng)
        conflicts = audit.find_lock_conflicts(courses, groups, previous, locked)
        if conflicts:
            with pytest.raises(audit.LockConflict):
                audit.solve(modules, courses, groups, previous, locked)
            continue
        actual = audit.solve(modules, courses, groups, previous, locked)
        expected = brute_force(modules, courses, groups, previous, locked)
        assert actual == expected, json.dumps({
            "modules": modules, "courses": courses, "groups": groups,
            "previous": previous, "locked": sorted(locked),
        }, ensure_ascii=False)


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


# ------------------------------------------------- 上次归属 / 锁定输入校验

def revision_payload():
    return {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
            {"id": "C2", "credits": 2, "modules": ["M2"]},
        ],
        "previousAssignments": [
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": None},
        ],
        "lockedCourses": ["C1"],
    }


def _invalid_revision_payloads():
    cases = {}

    def add(name, mutate):
        payload = revision_payload()
        mutate(payload)
        cases[name] = payload

    add("previous missing course",
        lambda p: p["previousAssignments"].pop())
    add("previous duplicate course", lambda p: p.update(previousAssignments=[
        {"course": "C1", "module": "M1"},
        {"course": "C1", "module": "M2"},
        {"course": "C2", "module": None},
    ]))
    add("previous unknown course", lambda p: p.update(previousAssignments=[
        {"course": "C1", "module": "M1"}, {"course": "GHOST", "module": "M1"},
    ]))
    add("previous unknown module", lambda p: p.update(previousAssignments=[
        {"course": "C1", "module": "GHOST"}, {"course": "C2", "module": "M2"},
    ]))
    add("previous module wrong type", lambda p: p.update(previousAssignments=[
        {"course": "C1", "module": 7}, {"course": "C2", "module": None},
    ]))
    add("previous empty course id", lambda p: p.update(previousAssignments=[
        {"course": "", "module": "M1"}, {"course": "C2", "module": None},
    ]))
    add("previous not a list", lambda p: p.update(previousAssignments={}))
    add("previous entry not object", lambda p: p.update(previousAssignments=["C1"]))
    add("previous extra field", lambda p: p["previousAssignments"][0].update(x=1))
    add("previous missing module field",
        lambda p: p["previousAssignments"][0].pop("module"))
    add("locked unknown course", lambda p: p.update(lockedCourses=["GHOST"]))
    add("locked duplicate course", lambda p: p.update(lockedCourses=["C1", "C1"]))
    add("locked not a list", lambda p: p.update(lockedCourses="C1"))
    add("locked entry not string", lambda p: p.update(lockedCourses=[1]))
    add("locked empty id", lambda p: p.update(lockedCourses=[""]))
    add("locked without previous", lambda p: p.pop("previousAssignments"))
    return cases


INVALID_REVISION_PAYLOADS = _invalid_revision_payloads()


def test_validate_accepts_revision_payload():
    payload = revision_payload()
    modules, courses, _ = audit.validate(payload)
    previous, locked = audit.validate_revision(payload, modules, courses)
    assert previous == {"C1": "M1", "C2": None}
    assert locked == frozenset({"C1"})


def test_validate_revision_optional_independently():
    """只有 previousAssignments 合法(空锁定);空 lockedCourses 也是空集合。"""
    payload = revision_payload()
    payload.pop("lockedCourses")
    modules, courses, _ = audit.validate(payload)
    previous, locked = audit.validate_revision(payload, modules, courses)
    assert previous == {"C1": "M1", "C2": None} and locked == frozenset()
    payload["lockedCourses"] = []
    _, locked = audit.validate_revision(payload, modules, courses)
    assert locked == frozenset()


@pytest.mark.parametrize(
    "payload", INVALID_REVISION_PAYLOADS.values(), ids=list(INVALID_REVISION_PAYLOADS)
)
def test_validate_rejects_invalid_revision(payload):
    modules, courses, _ = audit.validate(payload)
    with pytest.raises(audit.InputError):
        audit.validate_revision(payload, modules, courses)


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


# ----------------------------------------- 上次归属 / 锁定命令行行为

def test_cli_revision_minimizes_changes_and_lists_them():
    payload = {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
            {"id": "C2", "credits": 2, "modules": ["M1"]},
        ],
        "previousAssignments": [
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": "M1"},
        ],
    }
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["total_counted"] == 4
    assert result["changes"] == [{"course": "C1", "from": "M1", "to": "M2"}]
    modules, courses, groups = audit.validate(payload)
    previous, locked = audit.validate_revision(payload, modules, courses)
    assert result == audit.solve(modules, courses, groups, previous, locked)


def test_cli_lock_conflict_exit_code_3_no_partial_output():
    """锁定失去认可资格:退出码 3,stderr 明确 LOCK_CONFLICT,stdout 无部分分配。"""
    payload = {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
            {"id": "C2", "credits": 2, "modules": ["M1"]},
        ],
        "previousAssignments": [
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": "M2"},
        ],
        "lockedCourses": ["C2"],
    }
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 3
    assert proc.stdout == ""
    error = json.loads(proc.stderr)
    assert error["error"] == "LOCK_CONFLICT"
    assert error["conflicts"] == [
        {"course": "C2", "reason": "ineligible", "module": "M2"}
    ]


def test_cli_lock_conflict_group_double_lock():
    """同组两门锁定课同时归属:退出码 3,冲突原因为 exclusiveGroup。"""
    payload = {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
            {"id": "C2", "credits": 2, "modules": ["M1", "M2"]},
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
    error = json.loads(proc.stderr)
    assert error["error"] == "LOCK_CONFLICT"
    assert error["conflicts"][0]["reason"] == "exclusiveGroup"


def test_cli_rejects_malformed_previous_assignments():
    """上次归属缺项:输入非法,退出码 2 而非 3。"""
    payload = {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
            {"id": "C2", "credits": 2, "modules": ["M1"]},
        ],
        "previousAssignments": [{"course": "C1", "module": "M1"}],
    }
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "missing" in json.loads(proc.stderr)["error"]


def test_cli_locked_flips_pass_to_shortfall():
    """锁定使达标翻转:同一份课程在无锁时 PASS,锁定后 SHORTFALL。"""
    payload = {
        "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
        "courses": [
            {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
            {"id": "C2", "credits": 2, "modules": ["M1"]},
        ],
        "previousAssignments": [
            {"course": "C1", "module": "M1"},
            {"course": "C2", "module": "M1"},
        ],
    }
    unlocked = run_cli(stdin_text=json.dumps(payload))
    assert json.loads(unlocked.stdout)["status"] == "PASS"
    payload["lockedCourses"] = ["C1"]
    proc = run_cli(stdin_text=json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "SHORTFALL"
    assert result["assignments"][0] == {"course": "C1", "module": "M1"}
    assert result["changes"] == []
