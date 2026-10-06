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

def brute_force(modules, courses, groups=None):
    """独立参照:枚举所有归属组合,先比计入总学分,再比归属记号序列。"""
    reqs = [m["required"] for m in modules]
    index = {m["id"]: i for i, m in enumerate(modules)}
    ordered = sorted(courses, key=lambda c: c["id"])

    best = None  # (total, seq, combo)
    option_lists = [[None, *course["modules"]] for course in ordered]
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
        if best is None or total > best[0] or (total == best[0] and seq < best[1]):
            best = (total, seq, combo)

    assignment = {course["id"]: choice for course, choice in zip(ordered, best[2])}
    return render(modules, ordered, assignment, groups)


def render(modules, ordered_courses, assignment, groups=None):
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
