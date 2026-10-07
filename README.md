# 毕业审核审计器

一门课可能同时列在多个培养模块的认可清单里,但学分只能用一次。按模块顺序贪心
分配会把本可达标的学生误判为不合格(见 `examples/contested.json`)。本工具对
课程归属做全局优化,输出归属方案、各模块缺口与 `PASS` / `SHORTFALL`。培养方案
调整后还可携带上次归属与锁定课程(见 `examples/revision.json`),在锁定、互斥
与封顶的联合约束下尽量少改动地重新审核。

## 分配规则

- 每门课只能**完整**分给一个合格(在其认可清单内)的模块,或不使用;
- 各模块的计入学分以其要求值**封顶**,超出部分不计入;
- 可选的 `exclusiveGroups` 声明互斥课程组(同一课程的多次修读或互斥替代课):
  同组**至多一门**获得模块归属,其余必须标为未使用;组占用状态与各模块
  封顶学分在**同一个优化**里联合求解——不能先算出旧最优分配再删去互斥课程;
- 在所有合法方案中,先**最大化计入总学分**(各模块计入学分之和);
- 仍有并列时,按课程 id 顺序取**归属模块 id 序列字典序最小**者,
  其中"未使用"排在所有模块 id 之后(模块 id 按字符串字典序比较,如 `"M10" < "M2"`)。

### 培养方案调整:上次归属与锁定

可选用 `previousAssignments` 携带**覆盖全部课程**的上次归属、`lockedCourses`
携带正式锁定的课程 id:

- 锁定课程**必须保持原归属**,搜索中只剩这一条转移;其余课程在当前认可清单
  与互斥组约束下**重新联合分配**——锁定、组占用与模块封顶在同一个状态搜索中
  处理,**不能先求旧最优再把锁定课程挪回去**;
- 目标改为依次:① 最大化封顶后计入总学分;② **最小化相对上次归属的改动门数**;
  ③ 仍并列时沿用上面的字典序裁决(尽量保留已通知学生的归属);
- 输出额外给出 `changedCount` 与 `changes`(仅列实际变动,每条含课程 id 及
  `from`/`to` 前后归属;锁定课必然不在其中);
- 结构合法但锁定项不可行时返回 **`LOCK_CONFLICT`**(退出码 **3**,stderr JSON,
  stdout 不产生部分分配):
  - 锁定的原归属模块已不在该课当前认可清单(`ineligible` 给出课程 id);
  - 同一互斥组内有两门及以上锁定课都被锁定到模块(`groups` 给出组员与锁定者)。
  - 锁定到"未使用"(`null`)不占用模块,不参与上述互斥冲突;
- 未传这两个字段时,旧 CLI 的退出码与 JSON 输出**逐字节不变**。

推论:把课分给任何合格模块都不会减少计入总学分,且字典序上优于"未使用",
因此未传 `exclusiveGroups` 也未传调整配置时,最优方案的输出里不会出现未使用
的课程;传入互斥组后,同组未获归属的课程必须标为未使用(`null`)。同理,无
锁定时每组恰好会有一门课获得归属(全组闲置总可以被严格更优的方案替代),输出
中的 `counted` 仅在格式上保留 `null` 的可能。锁定则可能强制组员全部未使用
或拉低总学分(达标状态由 `PASS` 翻转为 `SHORTFALL`)。

## 输入(仅接受 JSON)

从标准输入或文件读取一个 JSON 对象,任何一处不合法即**整份拒绝**
(退出码 2,错误信息以 JSON 写到 stderr):

```json
{
  "modules": [
    {"id": "M1", "required": 2},
    {"id": "M2", "required": 2}
  ],
  "courses": [
    {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
    {"id": "C2", "credits": 2, "modules": ["M1"]}
  ]
}
```

| 约束 | 说明 |
| --- | --- |
| 模块 | 2–5 个,`id` 为非空 ASCII 字符串且唯一,`required` 为 1–10 的整数 |
| 课程 | 1–16 门,`id` 为非空 ASCII 字符串且唯一,`credits` 为 1–4 的整数 |
| 认可清单 | `modules` 为非空数组,元素必须是已声明的模块 id,且不重复 |
| 互斥组 | 可选 `exclusiveGroups`,至多 3 组;每组为 `{"courses": [...]}`,含 2–3 门**已声明**课程,组内不重复,组间不得共享课程 |
| 上次归属 | 可选 `previousAssignments`,为 `[{"course": ..., "module": ...}, ...]`;必须**恰好覆盖**全部课程且课程不重复;`module` 为已声明模块 id 或 `null`(上次未使用) |
| 锁定 | 可选 `lockedCourses`,为课程 id 数组(允许空数组),元素为已声明课程且不重复;非空时必须同时提供 `previousAssignments` |
| 严格性 | 未知模块/课程、重复 id、多余字段、缺失字段、非 JSON 输入均整份拒绝;上次归属缺项、重复或引用未知 id 同属非法(退出码 2) |

结构合法后,锁定的原归属不在当前认可清单、或同互斥组多门锁定课同时归属模块
时,返回 `LOCK_CONFLICT`(退出码 3),不输出部分分配。

例如声明 C1、C2 互斥(同一课程的两次修读,只能计入一门):

```json
{
  "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
  "courses": [
    {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
    {"id": "C2", "credits": 2, "modules": ["M2"]},
    {"id": "C3", "credits": 2, "modules": ["M1", "M2"]}
  ],
  "exclusiveGroups": [{"courses": ["C1", "C2"]}]
}
```

培养方案调整后重审:C2 被正式锁定在 M2(它与 C1 同属互斥组),联合重新
分配后 C3 填 M1,C1 只能改为未使用,实际只变动 1 门:

```json
{
  "modules": [{"id": "M1", "required": 2}, {"id": "M2", "required": 2}],
  "courses": [
    {"id": "C1", "credits": 2, "modules": ["M1", "M2"]},
    {"id": "C2", "credits": 2, "modules": ["M2"]},
    {"id": "C3", "credits": 2, "modules": ["M1", "M2"]}
  ],
  "exclusiveGroups": [{"courses": ["C1", "C2"]}],
  "previousAssignments": [
    {"course": "C1", "module": "M1"},
    {"course": "C2", "module": "M2"},
    {"course": "C3", "module": "M1"}
  ],
  "lockedCourses": ["C2"]
}
```

## 输出

退出码 0 时 stdout 输出 JSON:

- `status`:所有模块缺口为 0 时 `PASS`,否则 `SHORTFALL`;
- `total_required` / `total_counted`:要求总学分 / 计入总学分;
- `assignments`:按课程 id 排序的归属(`module` 为模块 id,未使用为 `null`);
- `modules`:按输入顺序给出各模块 `required` / `assigned`(原始归属学分)/
  `counted`(封顶后计入学分)/ `shortfall`(缺口);
- `exclusiveGroups`(**仅当输入传入该字段**):按输入顺序给出每组的
  `courses` 与 `counted`(该组最终获得模块归属的课程 id,全组未使用为
  `null`),便于教务员复核每组计入了哪一门。未传该字段时,输出与旧版
  完全一致,不含此键;
- `changedCount` / `changes`(**仅当传入 `previousAssignments` 时**):
  相对上次归属实际改动的门数,以及按课程 id 排序的变动列表
  (`{"course", "from", "to"}`,`null` 表示未使用)。

`LOCK_CONFLICT` 时退出码为 3、stdout 为空,stderr 为:

```json
{
  "error": "LOCK_CONFLICT",
  "message": "locked assignments conflict with current eligibility or exclusive groups",
  "ineligible": ["C1"],
  "groups": [{"courses": ["C1", "C2"], "locked": ["C1", "C2"]}]
}
```

## 运行

```bash
python3 audit.py < examples/contested.json    # 从 stdin 读
python3 audit.py examples/infeasible.json     # 从文件读
python3 audit.py < examples/exclusive.json    # 含互斥组:C1/C2 只计入一门
python3 audit.py < examples/revision.json     # 方案调整:锁定 C2,输出实际变动
```

## Docker Compose

```bash
docker compose build
docker compose run --rm -T audit < examples/contested.json   # 审核
docker compose run --rm test                                 # 运行 pytest 对拍
```

(`run` 需要 `-T` 关闭伪终端,stdin 重定向才能生效。)

## 测试

```bash
python3 -m pytest -q
```

`tests/test_audit.py` 包含:

- **穷举对拍**:2 个模块、至多 3 门课的全部 332 种输入组合;叠加全部合法
  互斥组配置(未传 / 空数组 / 单个 2–3 门组)的 1644 种组合,同时覆盖
  互斥、学分封顶、平局裁决与不可达标情形;
- **调整穷举对拍**:上述小集合 × 全部上次归属(含 `null` 与当前不认可的
  模块)× 全部锁定子集,三级目标(总学分 → 改动门数 → 字典序)与独立暴力
  枚举逐项比对;锁冲突情形与 `validate_full` 的 `LOCK_CONFLICT` 对拍,并断言
  穷举中确有 `PASS`/`SHORTFALL` 达标状态翻转(含 PASS→SHORTFALL);
- **随机对拍**:固定种子的 300 个随机小输入、300 个叠加 0–3 个互斥组的随机
  输入,以及 200 个带上次归属与随机锁定(含组/资格冲突)的随机输入;
- **规则用例**:一门课争抢两个模块(按模块顺序贪心会误判)、总学分足够却无法
  合法分配、字典序并列打破(`"M10" < "M2"`)、未使用排在最后、学分封顶;
  互斥组联合优化(不能在旧最优上删课)、组内并列裁决、封顶模块不再吸收
  组员、多组同时生效、三门组只计一门;上次归属压过字典序、总学分优先于
  少改动、锁定课在联合搜索中只剩原归属、锁定翻转达标状态、锁定 `null`
  保持未使用、资格/组锁定冲突;
- **输入校验**:未知模块、重复 id、多余/缺失字段、越界取值等 28 种非法输入,
  以及组内未知/重复课程、组间共享课程、组字段多余/缺失、组数超限等
  11 种非法互斥组输入;上次归属缺项/重复/未知 id/类型错误、锁定列表
  非法与锁定缺上次归属等 17 种非法调整输入;
- **命令行**:stdin/文件读取、非 JSON 与非法文档的拒绝行为、未传新字段时
  输出逐字节不变、非法互斥组整份拒绝且 stdout 不产生方案;调整成功时输出
  `changes`、`LOCK_CONFLICT` 退出码 3 且 stdout 为空(资格与互斥两种)、
  结构非法仍为退出码 2。

## 实现说明

`audit.py` 仅用标准库。求解用动态规划:状态为(各模块已计入(封顶后)学分
元组, 互斥组占用位掩码),逐门课转移——组内已有一门归属时,其余组员只能
未使用;锁定课程只有原归属一条转移;每个状态保留 (相对上次的改动门数,
归属记号序列) 字典序最小的前缀;最后按 ① 计入总学分最大 ② 改动门数最少
③ 序列最小依次裁决。锁定、组占用与模块封顶因此天然在同一搜索中联合处理。
规模上限(5 模块 × 要求 10、16 门课、至多 3 个互斥组)下最坏亚秒级。
