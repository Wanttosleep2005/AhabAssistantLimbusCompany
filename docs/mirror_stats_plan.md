# 镜牢运行统计 Excel 报告 - 设计方案

## 一、目标

在镜牢任务全部结束后，自动生成一份 `.xlsx` 报告，包含：
- 摘要数据
- 每次镜牢的详细记录
- 多种图表可视化
- 队伍性能对比

报告保存到 `./logs/mirror_stats_<时间戳>.xlsx`，运行结束弹出 InfoBar 提示文件名。

---

## 二、数据来源

每次镜牢完成后，`Mirror.get_run_stats()` 返回一个 dict：

| 字段 | 类型 | 说明 |
|---|---|---|
| `team` | int | 队伍编号 |
| `team_name` | str | 队伍备注名 |
| `hard` | bool | 是否困难镜牢 |
| `floor` | int | 到达楼层 |
| `pass_coins` | int | 通行证经验数量 |
| `battle_time` | float | 战斗总耗时(秒) |
| `event_time` | float | 事件总耗时(秒) |
| `event_count` | int | 事件次数 |
| `shop_time` | float | 商店总耗时(秒) |
| `find_road_time` | float | 寻路总耗时(秒) |
| `elapsed` | float | 本次总耗时(秒) |
| `timestamp` | str | 完成时间 ISO 字符串 |

`Mirror_task()` 收集所有记录到 `run_records` 列表，结束后调用 `generate_mirror_stats_excel(run_records)` 生成报告。

---

## 三、Excel 结构

### Sheet 1: 摘要

| 指标 | 数值 |
|---|---|
| 镜牢次数 | 12 |
| 困难镜牢 | 5 次 |
| 普通镜牢 | 7 次 |
| 总通行证经验 | 3250 |
| 总耗时 | 245分30秒 |
| 平均每轮耗时 | 20分27秒 |
| 总事件次数 | 86 |
| 最高楼层 | 5 |
| 通行证经验/分钟 | 13.2 |

### Sheet 2: 详细记录

每行一次镜牢，含所有字段。表头蓝底白字。

### Sheet 3: 图表（新增）

集中放置所有图表，便于一眼查看。

### Sheet 4: 队伍对比（新增）

按队伍分组的统计表 + 对比图。

---

## 四、图表清单

### 4.1 通用图表（单队伍也能画）

| # | 图表类型 | 标题 | 数据 | 用途 |
|---|---|---|---|---|
| 1 | 柱状图 | 每次镜牢耗时(秒) | elapsed × 序号 | 看单次波动 |
| 2 | 折线图 | 通行证经验累计趋势 | 累加 pass_coins × 序号 | 看收益增长 |
| 3 | 饼图 | 时间分布占比 | battle/event/shop/find_road 总和 | 看时间花在哪 |
| 4 | 面积图 | 累计耗时增长 | 累加 elapsed × 序号 | 看总投入时间趋势 |
| 5 | 散点图 | 耗时 vs 通行证经验 | elapsed × pass_coins | 看耗时和收益是否相关 |
| 6 | 雷达图 | 镜牢综合评分 | 速度/收益/事件效率/楼层/稳定性 | 一张图看整体表现 |

### 4.2 多队伍图表（≥2支队伍才有意义）

| # | 图表类型 | 标题 | 数据 | 用途 |
|---|---|---|---|---|
| 7 | 柱状图 | 队伍平均耗时对比 | 每队伍 avg(elapsed) | 看哪队最快 |
| 8 | 柱状图 | 队伍通行证收益对比 | 每队伍 sum(pass_coins) | 看哪队收益高 |
| 9 | 雷达图 | 队伍多维对比 | 速度/收益/事件/楼层 | 多维度看哪队综合强 |

### 4.3 困难/普通图表（两种难度都跑才有意义）

| # | 图表类型 | 标题 | 数据 | 用途 |
|---|---|---|---|---|
| 10 | 柱状图 | 困难 vs 普通平均耗时 | avg(elapsed) by hard | 看难度差异 |
| 11 | 柱状图 | 困难 vs 普通通行证经验 | avg(pass_coins) by hard | 看哪个难度划算 |

### 4.4 图表自动裁剪规则

代码运行时根据实际数据**动态决定画哪些图**：

| 条件 | 画哪些图 |
|---|---|
| 只有1次记录 | 只画饼图(时间分布) |
| ≥2次记录 | + 柱状图/折线图/面积图/散点图 |
| ≥2支队伍 | + 队伍对比图 |
| 困难+普通都有 | + 难度对比图 |
| ≥3次且≥2维度有数据 | + 雷达图 |

**不画空图**——数据不够的图表自动跳过，不会出现空坐标轴。

---

## 五、项目约束

### 5.1 依赖

| 约束 | 说明 |
|---|---|
| 仅新增 `openpyxl>=3.1.0` | 不引入 pandas / matplotlib / xlsxwriter |
| 写入 `pyproject.toml` | dependencies 数组追加 |
| `requirements.txt` | 由 `uv export` 重新生成，不手动编辑 |

### 5.2 代码规范

| 约束 | 说明 |
|---|---|
| 遵循 ruff 规则 | E(pycodestyle) / F(pyflakes) / I(isort) / T201(禁print) |
| 行宽 ≤120 | 符合 `pyproject.toml` 中 `line-length = 120` |
| 目标 Python 3.12+ | 使用 `dict` / `list` 而非 `Dict` / `List` |
| 不添加无关注释 | 只在新增功能块加分隔注释 |
| 不修改已有逻辑 | 只追加，不重构 |

### 5.3 文件改动范围

| 文件 | 允许改动 | 禁止改动 |
|---|---|---|
| `tasks/mirror/mirror.py` | 新增 `get_run_stats()` 方法 | 不改主循环 `run()` |
| `tasks/base/script_task_scheme.py` | `onetime_mir_process` 返回值加 stats；新增 `generate_mirror_stats_excel()` | 不改任务调度顺序 |
| `app/mediator.py` | 新增 `mirror_stats_signal` | 不改现有信号 |
| `app/page_card.py` | 新增 `_show_mirror_stats()` | 不改现有 UI 组件 |
| `pyproject.toml` | dependencies 追加 openpyxl | 不改其他配置 |
| `README.md` | 不改 | — |

### 5.4 运行时约束

| 约束 | 说明 |
|---|---|
| 失败不阻塞 | Excel 生成失败只记 `log.warning`，不影响任务流程 |
| 不弹模态框 | 只用 `BaseInfoBar` 非阻塞提示，不弹 MessageBox |
| 文件命名 | `mirror_stats_YYYYMMDD_HHMMSS.xlsx`，不覆盖已有文件 |
| 输出目录 | `./logs/`（已被 `.gitignore` 忽略） |
| 单次运行≤1个文件 | 一次 `Mirror_task()` 结束只生成一份报告 |
| 内存安全 | 生成完毕后释放 workbook 对象 |

### 5.5 图表约束

| 约束 | 说明 |
|---|---|
| 仅用 `openpyxl.chart` | BarChart / LineChart / PieChart / AreaChart / ScatterChart / RadarChart |
| 图表尺寸 | 宽 20cm × 高 12cm（统一） |
| 样式 | `chart.style = 10`（蓝色系） |
| 数据不足不画 | 避免空坐标轴 |
| 图表放独立 Sheet | 不和详细记录混排 |

### 5.6 不做的事

- ❌ 不做纽/经验卡 OCR（结算界面识别不稳定，需要截图素材）
- ❌ 不做实时统计面板（复杂度高，收益低）
- ❌ 不引入 pandas/matplotlib（体积大，启动慢）
- ❌ 不修改现有任务调度逻辑
- ❌ 不做历史报告管理（不索引旧报告，用户自己看 logs 目录）
- ❌ 不做 CSV 导出（Excel 已足够）
- ❌ 不做自动打开 Excel（只提示文件名）

---

## 六、文件改动

| 文件 | 改动 |
|---|---|
| `tasks/mirror/mirror.py` | 新增 `get_run_stats()` 方法（~15行） |
| `tasks/base/script_task_scheme.py` | `onetime_mir_process` 返回 stats；`Mirror_task` 收集记录；新增 `generate_mirror_stats_excel()`（~100行） |
| `app/mediator.py` | 新增 `mirror_stats_signal = Signal(str)` |
| `app/page_card.py` | 新增 `_show_mirror_stats()` 显示文件路径 |
| `pyproject.toml` | dependencies 新增 `openpyxl>=3.1.0` |
| `requirements.txt` | 重新生成 |

---

## 七、不做的事

- ❌ 不做纽/经验卡 OCR（结算界面识别不稳定）
- ❌ 不做实时统计面板（复杂度高，收益低）
- ❌ 不引入 pandas/matplotlib（体积大）
- ❌ 不修改现有任务调度逻辑
- ❌ 不修改 `onetime_mir_process` 的外部调用签名（返回值改为 tuple 但兼容旧用法）
