#!/usr/bin/env python3
"""毕业审核审计器:把每门课完整分给至多一个合格模块(或不使用),使计入总学分最大。

可选的 exclusiveGroups 声明互斥课程组(同一课程的多次修读或互斥替代课):
同组至多一门课获得模块归属,其余必须标为未使用;组占用状态与各模块
封顶学分在同一优化中联合求解,而不是先求旧最优分配再删去互斥课程。

可选的 previousAssignments + lockedCourses 用于培养方案调整后的重新审核:
previousAssignments 给出覆盖全部课程的上次归属(未使用为 null),lockedCourses
中的课程必须保持原归属;锁定、组占用与模块封顶在同一次状态搜索里联合转移,
三级目标依次为:最大化封顶后计入总学分、最小化相对上次归属的改动门数、
沿用课程 id 顺序的归属序列字典序裁决。锁定项若不再具备当前认可资格或
同组有两门锁定课同时归属,返回 LOCK_CONFLICT。

用法:
    python3 audit.py              从标准输入读取 JSON
    python3 audit.py input.json   从文件读取 JSON

退出码: 0 = 正常完成审核(结果为 PASS 或 SHORTFALL);2 = 输入非法,整份拒绝;
3 = LOCK_CONFLICT,锁定课程无法在当前规则下保持原归属,不输出部分分配。
"""

from __future__ import annotations

import json
import sys

MODULES_MIN, MODULES_MAX = 2, 5
COURSES_MIN, COURSES_MAX = 1, 16
REQUIRED_MIN, REQUIRED_MAX = 1, 10
CREDITS_MIN, CREDITS_MAX = 1, 4
GROUPS_MAX = 3
GROUP_COURSES_MIN, GROUP_COURSES_MAX = 2, 3

TOP_FIELDS = {"modules", "courses", "exclusiveGroups", "previousAssignments",
              "lockedCourses"}
MODULE_FIELDS = {"id", "required"}
COURSE_FIELDS = {"id", "credits", "modules"}
GROUP_FIELDS = {"courses"}
PREV_FIELDS = {"course", "module"}

# 并列比较用的记号:已归属 -> (0, 模块id);未使用 -> (1, ""),排在所有模块 id 之后。
UNUSED_TOKEN = (1, "")


class InputError(Exception):
    """输入不合法,整份拒绝。"""


class LockConflict(Exception):
    """锁定课程无法在当前规则下保持原归属,不输出部分分配。"""

    def __init__(self, message, conflicts=None):
        super().__init__(message)
        self.conflicts = conflicts or []


def _is_id(value):
    return isinstance(value, str) and value != "" and value.isascii()


def _is_int(value):
    # bool 是 int 的子类,明确排除。
    return isinstance(value, int) and not isinstance(value, bool)


def _check_fields(obj, allowed, what, optional=()):
    if not isinstance(obj, dict):
        raise InputError(f"{what} must be a JSON object")
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise InputError(f"{what} has unexpected field(s): {', '.join(unknown)}")
    missing = sorted(set(allowed) - set(optional) - set(obj))
    if missing:
        raise InputError(f"{what} is missing field(s): {', '.join(missing)}")


def validate(data):
    """校验已解析的 JSON,返回 (modules, courses, groups);不合法则抛 InputError。

    groups 为 None 表示输入未传 exclusiveGroups;传入空数组则为 []。
    """
    _check_fields(
        data, TOP_FIELDS, "input",
        optional=("exclusiveGroups", "previousAssignments", "lockedCourses"),
    )

    raw_modules = data["modules"]
    raw_courses = data["courses"]
    if not isinstance(raw_modules, list) or not MODULES_MIN <= len(raw_modules) <= MODULES_MAX:
        raise InputError(f"modules must be a list of {MODULES_MIN} to {MODULES_MAX} entries")
    if not isinstance(raw_courses, list) or not COURSES_MIN <= len(raw_courses) <= COURSES_MAX:
        raise InputError(f"courses must be a list of {COURSES_MIN} to {COURSES_MAX} entries")

    modules = []
    module_ids = set()
    for entry in raw_modules:
        _check_fields(entry, MODULE_FIELDS, "module")
        mid, req = entry["id"], entry["required"]
        if not _is_id(mid):
            raise InputError("module id must be a non-empty ASCII string")
        if mid in module_ids:
            raise InputError(f"duplicate module id: {mid!r}")
        if not _is_int(req) or not REQUIRED_MIN <= req <= REQUIRED_MAX:
            raise InputError(
                f"module {mid!r}: required must be an integer in "
                f"[{REQUIRED_MIN}, {REQUIRED_MAX}]"
            )
        module_ids.add(mid)
        modules.append({"id": mid, "required": req})

    courses = []
    course_ids = set()
    for entry in raw_courses:
        _check_fields(entry, COURSE_FIELDS, "course")
        cid, credits, elig = entry["id"], entry["credits"], entry["modules"]
        if not _is_id(cid):
            raise InputError("course id must be a non-empty ASCII string")
        if cid in course_ids:
            raise InputError(f"duplicate course id: {cid!r}")
        if not _is_int(credits) or not CREDITS_MIN <= credits <= CREDITS_MAX:
            raise InputError(
                f"course {cid!r}: credits must be an integer in "
                f"[{CREDITS_MIN}, {CREDITS_MAX}]"
            )
        if not isinstance(elig, list) or not elig:
            raise InputError(f"course {cid!r}: modules must be a non-empty list")
        seen = set()
        for mid in elig:
            if not _is_id(mid):
                raise InputError(f"course {cid!r}: module id must be a non-empty ASCII string")
            if mid not in module_ids:
                raise InputError(f"course {cid!r}: unknown module {mid!r}")
            if mid in seen:
                raise InputError(f"course {cid!r}: duplicate module {mid!r} in modules list")
            seen.add(mid)
        course_ids.add(cid)
        courses.append({"id": cid, "credits": credits, "modules": list(elig)})

    groups = None
    if "exclusiveGroups" in data:
        raw_groups = data["exclusiveGroups"]
        if not isinstance(raw_groups, list) or len(raw_groups) > GROUPS_MAX:
            raise InputError(
                f"exclusiveGroups must be a list of at most {GROUPS_MAX} entries"
            )
        groups = []
        used = set()
        for entry in raw_groups:
            _check_fields(entry, GROUP_FIELDS, "exclusive group")
            cids = entry["courses"]
            if (
                not isinstance(cids, list)
                or not GROUP_COURSES_MIN <= len(cids) <= GROUP_COURSES_MAX
            ):
                raise InputError(
                    f"exclusive group: courses must be a list of "
                    f"{GROUP_COURSES_MIN} to {GROUP_COURSES_MAX} entries"
                )
            seen = set()
            for cid in cids:
                if not _is_id(cid):
                    raise InputError(
                        "exclusive group: course id must be a non-empty ASCII string"
                    )
                if cid not in course_ids:
                    raise InputError(f"exclusive group: unknown course {cid!r}")
                if cid in seen:
                    raise InputError(f"exclusive group: duplicate course {cid!r}")
                if cid in used:
                    raise InputError(
                        f"exclusive group: course {cid!r} already appears "
                        "in another group"
                    )
                seen.add(cid)
                used.add(cid)
            groups.append(list(cids))

    return modules, courses, groups


def validate_revision(data, modules, courses):
    """校验可选的 previousAssignments / lockedCourses。

    返回 (previous, locked):previous 为 {课程id: 模块id 或 None};
    未提供 previousAssignments 时返回 (None, frozenset())。
    缺项、重复、未知 id 等结构问题一律 InputError(退出码 2);
    锁定资格与互斥冲突在求解前另行检测,抛 LockConflict。
    """
    course_ids = {c["id"] for c in courses}
    module_ids = {m["id"] for m in modules}

    if "previousAssignments" not in data:
        if "lockedCourses" in data:
            raise InputError("lockedCourses requires previousAssignments")
        return None, frozenset()

    raw_prev = data["previousAssignments"]
    if not isinstance(raw_prev, list):
        raise InputError("previousAssignments must be a list")
    previous = {}
    for entry in raw_prev:
        _check_fields(entry, PREV_FIELDS, "previous assignment")
        cid, mid = entry["course"], entry["module"]
        if not _is_id(cid):
            raise InputError(
                "previous assignment: course id must be a non-empty ASCII string"
            )
        if cid not in course_ids:
            raise InputError(f"previous assignment: unknown course {cid!r}")
        if cid in previous:
            raise InputError(f"previous assignment: duplicate course {cid!r}")
        if mid is not None:
            if not _is_id(mid):
                raise InputError(
                    f"previous assignment of course {cid!r}: "
                    "module must be a module id or null"
                )
            if mid not in module_ids:
                raise InputError(
                    f"previous assignment of course {cid!r}: unknown module {mid!r}"
                )
        previous[cid] = mid

    if len(previous) != len(courses):
        missing = sorted(course_ids - set(previous))
        raise InputError(
            "previousAssignments must cover every course; "
            f"missing: {', '.join(missing)}"
        )

    locked = set()
    if "lockedCourses" in data:
        raw_locked = data["lockedCourses"]
        if not isinstance(raw_locked, list):
            raise InputError("lockedCourses must be a list")
        for cid in raw_locked:
            if not _is_id(cid):
                raise InputError(
                    "lockedCourses: course id must be a non-empty ASCII string"
                )
            if cid not in course_ids:
                raise InputError(f"lockedCourses: unknown course {cid!r}")
            if cid in locked:
                raise InputError(f"lockedCourses: duplicate course {cid!r}")
            locked.add(cid)

    return previous, frozenset(locked)


def find_lock_conflicts(courses, groups, previous, locked):
    """检测锁定归属相对当前规则的冲突,返回冲突列表(空列表表示无冲突)。

    两类冲突:
    - ineligible:锁定课原归属模块已不在该课当前认可清单中(原归属为未使用不算);
    - exclusiveGroup:同一互斥组内有两门锁定课的原归属都不是未使用,
      组占用无法同时满足。
    """
    eligible = {c["id"]: set(c["modules"]) for c in courses}
    conflicts = []
    for cid in sorted(locked):
        mid = previous[cid]
        if mid is not None and mid not in eligible[cid]:
            conflicts.append({"course": cid, "reason": "ineligible", "module": mid})
    for group in groups or []:
        locked_assigned = sorted(
            cid for cid in group if cid in locked and previous[cid] is not None
        )
        if len(locked_assigned) >= 2:
            conflicts.append({
                "reason": "exclusiveGroup",
                "courses": list(group),
                "locked": locked_assigned,
            })
    return conflicts


def solve(modules, courses, groups=None, previous=None, locked=frozenset()):
    """返回审核结果字典。

    规则:每门课只能完整分给一个合格模块或不使用;各模块计入学分以要求值封顶;
    同一互斥组内至多一门课获得模块归属,其余必须标为未使用。三级目标依次为:
    1. 最大化封顶后计入总学分;
    2. previous 给定时,最小化相对上次归属的改动门数;
    3. 按课程 id 顺序取归属模块 id 序列字典序最小者(未使用排在最后)。

    locked 中的课程只有一条合法转移(保持 previous 中的原归属),锁定约束与
    组占用、模块封顶在同一次状态搜索里联合转移,而不是先求无锁定最优再把
    锁定课挪回去。锁定归属不合法(失去认可资格或同组双锁)时抛 LockConflict。

    groups 为 None 表示输入未传 exclusiveGroups,输出不带该键;
    传入空列表则输出 "exclusiveGroups": []。previous 给定时输出额外带
    "changes"(相对上次归属实际变动的课程,按课程 id 排序)。
    """
    reqs = [m["required"] for m in modules]
    index = {m["id"]: i for i, m in enumerate(modules)}
    ordered = sorted(courses, key=lambda c: c["id"])

    if previous is not None and locked:
        conflicts = find_lock_conflicts(courses, groups, previous, locked)
        if conflicts:
            raise LockConflict("locked assignments conflict with current rules",
                               conflicts)

    # 每门课所在的互斥组下标(不在任何组内为 -1)。
    group_of = {}
    for gi, group in enumerate(groups or []):
        for cid in group:
            group_of[cid] = gi

    # 动态规划:状态 = (各模块已计入(封顶后)学分元组, 组占用位掩码)
    #   -> 达到该状态的最小 (相对上次归属的改动门数, 归属记号序列)。
    # 位掩码第 g 位为 1 表示第 g 组已有一门课获得归属,同组其余课只能未使用;
    # 锁定课程只允许保持原归属这一条转移。组占用、模块封顶与锁定在同一状态里
    # 联合转移,而非先求旧最优再把锁定课挪回去。
    # 同一状态保留字典序最小的 (改动门数, 前缀) 即可,因为后续课程的可选转移
    # 只取决于状态,且追加记号不会改变此前的改动计数。
    dp = {((0,) * len(modules), 0): (0, ())}
    for course in ordered:
        cid = course["id"]
        credits = course["credits"]
        gi = group_of.get(cid, -1)
        if cid in locked:
            # 锁定课:唯一合法转移;同组占用冲突已由 find_lock_conflicts 拦截。
            old_mid = previous[cid]
            options = [(UNUSED_TOKEN, None)] if old_mid is None else [
                ((0, old_mid), index[old_mid])
            ]
        else:
            options = [(UNUSED_TOKEN, None)]
            options += [((0, mid), index[mid]) for mid in course["modules"]]
        nxt = {}
        for (state, mask), (changes, seq) in dp.items():
            for token, mi in options:
                if mi is not None and gi >= 0 and (mask >> gi) & 1:
                    continue  # 同组已有一门课归属模块,本课只能未使用
                if previous is not None:
                    old_mid = previous[cid]
                    old_token = UNUSED_TOKEN if old_mid is None else (0, old_mid)
                    changed = changes + (token != old_token)
                else:
                    changed = 0
                cand = (changed, seq + (token,))
                if mi is None:
                    key = (state, mask)
                else:
                    grown = state[mi] + credits
                    cap = reqs[mi]
                    new_state = state[:mi] + (grown if grown < cap else cap,) + state[mi + 1:]
                    key = (new_state, mask | (1 << gi) if gi >= 0 else mask)
                prev_cand = nxt.get(key)
                if prev_cand is None or cand < prev_cand:
                    nxt[key] = cand
        dp = nxt

    def total_of(key):
        return sum(key[0])

    best_total = max(total_of(key) for key in dp)
    _, best_seq = min(
        value for key, value in dp.items() if total_of(key) == best_total
    )

    assignment = {}
    raw_sums = [0] * len(modules)
    for course, token in zip(ordered, best_seq):
        if token == UNUSED_TOKEN:
            assignment[course["id"]] = None
        else:
            mid = token[1]
            assignment[course["id"]] = mid
            raw_sums[index[mid]] += course["credits"]

    module_results = []
    for m, raw in zip(modules, raw_sums):
        counted = raw if raw < m["required"] else m["required"]
        module_results.append({
            "id": m["id"],
            "required": m["required"],
            "assigned": raw,
            "counted": counted,
            "shortfall": m["required"] - counted,
        })

    result = {
        "status": "PASS" if all(m["shortfall"] == 0 for m in module_results) else "SHORTFALL",
        "total_required": sum(reqs),
        "total_counted": sum(m["counted"] for m in module_results),
        "assignments": [
            {"course": c["id"], "module": assignment[c["id"]]} for c in ordered
        ],
        "modules": module_results,
    }
    if previous is not None:
        result["changes"] = [
            {"course": c["id"], "from": previous[c["id"]], "to": assignment[c["id"]]}
            for c in ordered
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


def _fail(message):
    sys.stderr.write(json.dumps({"error": message}, ensure_ascii=False) + "\n")
    return 2


def _lock_conflict(exc):
    sys.stderr.write(
        json.dumps(
            {"error": "LOCK_CONFLICT", "message": str(exc), "conflicts": exc.conflicts},
            ensure_ascii=False,
        )
        + "\n"
    )
    return 3


def main(argv):
    if len(argv) > 2:
        return _fail("usage: audit.py [input.json]  (JSON is read from stdin when no file is given)")
    try:
        if len(argv) == 2:
            with open(argv[1], "r", encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            data = json.load(sys.stdin)
    except OSError as exc:
        return _fail(f"cannot read input: {exc}")
    except ValueError as exc:
        return _fail(f"input is not valid JSON: {exc}")

    try:
        modules, courses, groups = validate(data)
        previous, locked = validate_revision(data, modules, courses)
    except InputError as exc:
        return _fail(str(exc))

    try:
        result = solve(modules, courses, groups, previous, locked)
    except LockConflict as exc:
        return _lock_conflict(exc)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
