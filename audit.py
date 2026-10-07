#!/usr/bin/env python3
"""毕业审核审计器:把每门课完整分给至多一个合格模块(或不使用),使计入总学分最大。

可选的 exclusiveGroups 声明互斥课程组(同一课程的多次修读或互斥替代课):
同组至多一门课获得模块归属,其余必须标为未使用;组占用状态与各模块
封顶学分在同一优化中联合求解,而不是先求旧最优分配再删去互斥课程。

培养方案调整后,可通过 previousAssignments 携带覆盖全部课程的上次归属、
lockedCourses 携带正式锁定(必须保持原归属)的课程 id。锁定、组占用与
模块封顶在同一状态搜索中联合处理:锁定课程只保留原归属这一条转移,
其余课程在当前认可清单与互斥组约束下重新联合分配。目标依次为:
  1. 最大化封顶后计入总学分;
  2. 最小化相对上次归属的改动门数;
  3. 沿用既有字典序裁决(未使用排在所有模块 id 之后)。
锁定项违反当前认可资格或彼此互斥时给出 LOCK_CONFLICT,不输出部分分配。

用法:
    python3 audit.py              从标准输入读取 JSON
    python3 audit.py input.json   从文件读取 JSON

退出码: 0 = 正常完成审核(结果为 PASS 或 SHORTFALL);2 = 输入非法,整份拒绝;
        3 = LOCK_CONFLICT,锁定归属与当前规则冲突,不产生方案。
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
LOCKED_MIN, LOCKED_MAX = 0, 16

TOP_FIELDS = {"modules", "courses", "exclusiveGroups", "previousAssignments", "lockedCourses"}
MODULE_FIELDS = {"id", "required"}
COURSE_FIELDS = {"id", "credits", "modules"}
GROUP_FIELDS = {"courses"}
PREVIOUS_FIELDS = {"course", "module"}

# 并列比较用的记号:已归属 -> (0, 模块id);未使用 -> (1, ""),排在所有模块 id 之后。
UNUSED_TOKEN = (1, "")


class InputError(Exception):
    """输入不合法,整份拒绝。"""


class LockConflict(Exception):
    """输入结构合法,但锁定归属与当前认可资格或互斥组冲突。"""

    def __init__(self, message, ineligible=None, groups=None):
        super().__init__(message)
        self.ineligible = ineligible or []
        self.groups = groups or []


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
    注意:本函数只做结构校验,previousAssignments / lockedCourses 不在此处理;
    入口 main 使用 validate_full 一并校验它们。
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


def validate_full(data):
    """整份输入校验,返回 (modules, courses, groups, previous, locked)。

    previous 为 None 表示未传 previousAssignments;否则为 {课程id: 模块id或None},
    覆盖全部课程。locked 为课程 id 集合(未传 lockedCourses 时为空集)。
    结构问题抛 InputError;结构合法但锁定项违反当前认可资格或彼此互斥时
    抛 LockConflict(LOCK_CONFLICT,不输出部分分配)。
    """
    modules, courses, groups = validate(data)
    module_ids = {m["id"] for m in modules}
    course_ids = [c["id"] for c in courses]
    course_set = set(course_ids)
    elig = {c["id"]: set(c["modules"]) for c in courses}

    previous = None
    if "previousAssignments" in data:
        raw_previous = data["previousAssignments"]
        if not isinstance(raw_previous, list):
            raise InputError("previousAssignments must be a list")
        previous = {}
        for entry in raw_previous:
            _check_fields(entry, PREVIOUS_FIELDS, "previous assignment")
            cid, mid = entry["course"], entry["module"]
            if not _is_id(cid):
                raise InputError(
                    "previous assignment: course id must be a non-empty ASCII string"
                )
            if cid not in course_set:
                raise InputError(f"previous assignment: unknown course {cid!r}")
            if cid in previous:
                raise InputError(f"previous assignment: duplicate course {cid!r}")
            if mid is not None and not _is_id(mid):
                raise InputError(
                    "previous assignment: module must be a module id or null"
                )
            if mid is not None and mid not in module_ids:
                raise InputError(f"previous assignment: unknown module {mid!r}")
            previous[cid] = mid
        missing = sorted(course_set - set(previous))
        if missing:
            raise InputError(
                f"previousAssignments must cover every course; missing: "
                f"{', '.join(missing)}"
            )

    locked = set()
    if "lockedCourses" in data:
        raw_locked = data["lockedCourses"]
        if not isinstance(raw_locked, list) or not LOCKED_MIN <= len(raw_locked) <= LOCKED_MAX:
            raise InputError(
                f"lockedCourses must be a list of at most {LOCKED_MAX} entries"
            )
        for cid in raw_locked:
            if not _is_id(cid):
                raise InputError(
                    "lockedCourses: course id must be a non-empty ASCII string"
                )
            if cid not in course_set:
                raise InputError(f"lockedCourses: unknown course {cid!r}")
            if cid in locked:
                raise InputError(f"lockedCourses: duplicate course {cid!r}")
            locked.add(cid)
        if locked and previous is None:
            raise InputError(
                "lockedCourses requires previousAssignments covering every course"
            )

    if locked:
        # 锁定课程必须保持原归属:原归属模块已不在当前认可清单 -> LOCK_CONFLICT。
        ineligible = sorted(
            cid for cid in locked if previous[cid] is not None
            and previous[cid] not in elig[cid]
        )
        # 同互斥组两门及以上锁定课都被锁定到某个模块 -> 彼此冲突。
        group_conflicts = []
        for group in groups or []:
            survivors = sorted(cid for cid in group if cid in locked
                               and previous[cid] is not None)
            if len(survivors) >= 2:
                group_conflicts.append({"courses": list(group), "locked": survivors})
        if ineligible or group_conflicts:
            raise LockConflict(
                "locked assignments conflict with current eligibility or "
                "exclusive groups",
                ineligible=ineligible,
                groups=group_conflicts,
            )

    return modules, courses, groups, previous, locked


def solve(modules, courses, groups=None, previous=None, locked=None):
    """返回审核结果字典。

    规则:每门课只能完整分给一个合格模块或不使用;各模块计入学分以要求值封顶;
    同一互斥组内至多一门课获得模块归属,其余必须标为未使用。

    previous 为 {课程id: 模块id或None} 时携带上次归属,locked 为锁定课程 id
    集合(锁定课只允许保留原归属这一条转移)。锁定、组占用与模块封顶在同一
    状态搜索中联合转移,而非先求旧最优再把课程挪回去。目标依次为:
      1. 最大化计入总学分(各模块计入学分之和);
      2. 最小化相对上次归属的改动门数;
      3. 按课程 id 顺序取归属模块 id 序列字典序最小者(未使用排在最后)。

    groups 为 None 表示输入未传 exclusiveGroups,输出不带该键;
    传入空列表则输出 "exclusiveGroups": []。previous/locked 给出时输出
    额外带 changedCount 与 changes(实际变动及其前后归属)。
    """
    reqs = [m["required"] for m in modules]
    index = {m["id"]: i for i, m in enumerate(modules)}
    ordered = sorted(courses, key=lambda c: c["id"])
    locked = locked or set()

    # 每门课所在的互斥组下标(不在任何组内为 -1)。
    group_of = {}
    for gi, group in enumerate(groups or []):
        for cid in group:
            group_of[cid] = gi

    # 动态规划:状态 = (各模块已计入(封顶后)学分元组, 组占用位掩码)
    #   -> 达到该状态的 (改动门数, 归属记号序列) 中字典序最小者。
    # 位掩码第 g 位为 1 表示第 g 组已有一门课获得归属,同组其余课只能未使用;
    # 锁定课程只保留原归属一条转移;组占用、锁定与模块封顶在同一状态里联合
    # 转移,而非先求旧最优再把锁定课程挪回去。同一状态只需保留 (改动数, 序列)
    # 最小的前缀:后续可选转移只取决于状态,而改动数与序列按课程可加、按前缀
    # 单调,被淘汰前缀不可能在更长课程上反超。
    dp = {((0,) * len(modules), 0): (0, ())}
    for course in ordered:
        cid = course["id"]
        credits = course["credits"]
        gi = group_of.get(cid, -1)
        if cid in locked:
            # 正式锁定:仅保留原归属这一条(资格/互斥冲突已由 validate_full 预检)。
            old = previous[cid]
            options = [(UNUSED_TOKEN, None)] if old is None else [((0, old), index[old])]
        else:
            options = [(UNUSED_TOKEN, None)]
            options += [((0, mid), index[mid]) for mid in course["modules"]]
        nxt = {}
        for (state, mask), (changes, seq) in dp.items():
            for token, mi in options:
                if mi is not None and gi >= 0 and (mask >> gi) & 1:
                    continue  # 同组已有一门课归属模块,本课只能未使用
                if previous is not None and cid not in locked:
                    old_token = UNUSED_TOKEN if previous[cid] is None else (0, previous[cid])
                    delta = 0 if token == old_token else 1
                else:
                    delta = 0
                cand_changes = changes + delta
                cand_seq = seq + (token,)
                if mi is None:
                    key = (state, mask)
                else:
                    grown = state[mi] + credits
                    cap = reqs[mi]
                    new_state = state[:mi] + (grown if grown < cap else cap,) + state[mi + 1:]
                    key = (new_state, mask | (1 << gi) if gi >= 0 else mask)
                cand = (cand_changes, cand_seq)
                prev_best = nxt.get(key)
                if prev_best is None or cand < prev_best:
                    nxt[key] = cand
        dp = nxt

    # 三级裁决:总学分最大 -> 改动门数最少 -> 归属记号序列字典序最小。
    best_total = max(sum(state) for state, _ in dp)
    best_changes, best_seq = min(
        value for (state, _), value in dp.items() if sum(state) == best_total
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
        # 实际变动及其前后归属;锁定课保持原归属,必然不在其中。
        changes = [
            {"course": c["id"], "from": previous[c["id"]], "to": assignment[c["id"]]}
            for c in ordered
            if previous[c["id"]] != assignment[c["id"]]
        ]
        result["changedCount"] = best_changes
        result["changes"] = changes
    return result


def _fail(message):
    sys.stderr.write(json.dumps({"error": message}, ensure_ascii=False) + "\n")
    return 2


def _lock_conflict(exc):
    # LOCK_CONFLICT:结构合法,但锁定归属与当前规则冲突;stdout 不产生部分分配。
    sys.stderr.write(json.dumps({
        "error": "LOCK_CONFLICT",
        "message": str(exc),
        "ineligible": exc.ineligible,
        "groups": exc.groups,
    }, ensure_ascii=False) + "\n")
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
        modules, courses, groups, previous, locked = validate_full(data)
    except InputError as exc:
        return _fail(str(exc))
    except LockConflict as exc:
        return _lock_conflict(exc)

    json.dump(
        solve(modules, courses, groups, previous, locked),
        sys.stdout, ensure_ascii=False, indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
