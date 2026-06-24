import platform
import random
from datetime import datetime
from pathlib import Path
from time import sleep, time

from playsound3 import playsound
from PySide6.QtCore import QT_TRANSLATE_NOOP, QMutex, QThread

from app import mediator
from app.windows_toast import TemplateToast, send_toast
from module.automation import auto
from module.config import TeamSetting, cfg
from module.decorator.decorator import begin_and_finish_time_log
from module.game_and_screen import game_process, screen
from module.logger import log
from module.my_error.my_error import (
    backMainWinError,
    cannotOperateGameError,
    netWorkUnstableError,
    notWaitError,
    unableToFindTeamError,
    unexpectNumError,
    userStopError,
    withOutAdminError,
    withOutGameWinError,
    withOutPicError,
)
from module.system_actions import (
    apply_power_keep_awake,
    execute_after_completion,
    get_after_completion_config,
)
from tasks.base.back_init_menu import back_init_menu
from tasks.base.make_enkephalin_module import (
    lunacy_to_enkephalin,
    make_enkephalin_module,
)
from tasks.battle import battle
from tasks.daily.get_prize import get_mail_prize, get_pass_prize
from tasks.daily.luxcavation import EXP_luxcavation, thread_luxcavation
from tasks.mirror.mirror import Mirror
from tasks.teams.team_formation import select_battle_team
from utils.path_manager import path_manager
from utils.utils import calculate_the_teams, check_hard_mirror_time, get_day_of_week

# onetime_mir_process 被 @begin_and_finish_time_log 装饰器包裹，
# 装饰器会丢弃函数返回值。因此通过此变量传出统计数据。
_last_mirror_stats: dict = {}
# 持久化每轮镜牢记录
_MIRROR_RECORDS_PATH = Path("./logs/mirror_records.json")
_CLEANUP_KEY = "__cleanup_ts"


def _should_cleanup() -> bool:
    """检查是否需要周四清理（副本重置日）"""
    now = datetime.now()
    if now.weekday() != 3:  # 0=周一, 3=周四
        return False
    # 检查上次清理是否在本周四之前
    try:
        import json
        if _MIRROR_RECORDS_PATH.exists():
            data = json.loads(_MIRROR_RECORDS_PATH.read_text(encoding="utf-8"))
            meta = data[-1] if isinstance(data, list) and data and isinstance(data[-1], dict) else {}
            last_cleanup = meta.get(_CLEANUP_KEY, "")
            if last_cleanup:
                last_dt = datetime.fromisoformat(last_cleanup)
                return last_dt.date() < now.date()
    except Exception:
        pass
    return True  # 首次运行，执行清理


def _save_cleanup_mark():
    """在记录末尾追加清理标记（直接读写文件，避免递归）"""
    try:
        import json
        existing = []
        if _MIRROR_RECORDS_PATH.exists():
            existing = json.loads(_MIRROR_RECORDS_PATH.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        # 已在 _load_mirror_records 中过滤，只需追加标记
        existing.append({_CLEANUP_KEY: datetime.now().isoformat(timespec="seconds")})
        _MIRROR_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_MIRROR_RECORDS_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_mirror_records() -> list[dict]:
    """从持久化文件加载镜牢历史记录（周四自动清理）"""
    import json
    if _MIRROR_RECORDS_PATH.exists():
        try:
            with open(_MIRROR_RECORDS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            # 过滤掉清理标记，只返回有效记录
            records = [r for r in data if _CLEANUP_KEY not in r]
            # 周四清理逻辑
            if _should_cleanup():
                today = datetime.now().strftime("%Y-%m-%d")
                log.info(f"周四镜牢数据自动清理: {today}, 清理前共 {len(records)} 条")
                now = datetime.now()
                records = [r for r in records
                           if datetime.fromisoformat(r.get("timestamp", "2000-01-01")).date() >= now.date()]
                # 同步清理 charts 目录
                charts_dir = Path("./logs/charts")
                if charts_dir.exists():
                    for old_chart in charts_dir.glob("chart_*.png"):
                        try:
                            old_chart.unlink()
                        except OSError:
                            pass
                    log.info("周四已清理 charts 目录")
                _save_cleanup_mark()
                log.info(f"清理完成，保留 {len(records)} 条（仅保留今日）")
            return records
        except Exception:
            pass
    return []


def _save_mirror_records(new_records: list[dict]):
    """合并保存镜牢记录到持久化文件（加载已有 + 追加新记录）"""
    try:
        import json
        # 加载已有记录
        existing = _load_mirror_records()
        # 追加新记录（去重：同 timestamp + team_name 视为重复）
        existing_ts = {(r.get("timestamp", ""), r.get("team_name", "")) for r in existing}
        for rec in new_records:
            slim = {
                "timestamp": rec.get("timestamp", ""),
                "team_name": rec.get("team_name", ""),
                "team": rec.get("team", 0),
                "hard": rec.get("hard", False),
                "floor": rec.get("floor", 0),
                "pass_coins": rec.get("pass_coins", 0),
                "elapsed": round(rec.get("elapsed", 0)),
                "battle_time": round(rec.get("battle_time", 0)),
                "event_time": round(rec.get("event_time", 0)),
                "event_count": rec.get("event_count", 0),
                "shop_time": round(rec.get("shop_time", 0)),
                "find_road_time": round(rec.get("find_road_time", 0)),
            }
            key = (slim["timestamp"], slim["team_name"])
            if key not in existing_ts:
                existing.append(slim)
                existing_ts.add(key)
        _MIRROR_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_MIRROR_RECORDS_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        log.debug(f"已保存 {len(existing)} 条镜牢记录到 {_MIRROR_RECORDS_PATH}")
    except Exception as e:
        log.warning(f"保存镜牢记录失败: {e}")


@begin_and_finish_time_log(task_name="一次经验本")
# 一次经验本的过程
def onetime_EXP_process(combat_count: int = 1):
    if cfg.targeted_teaming_EXP:
        team = cfg.get_value(f"EXP_day_{calculate_the_teams()}")
    else:
        team = cfg.daily_teams
    EXP_luxcavation(combat_count)
    select_battle_team(team)
    if battle.to_battle() is False:
        return False
    battle.fight(combat_count=combat_count)
    back_init_menu()
    make_enkephalin_module()


@begin_and_finish_time_log(task_name="一次纽本")
# 一次纽本的过程
def onetime_thread_process(combat_count: int = 1):
    if cfg.targeted_teaming_thread:
        team = cfg.get_value(f"thread_day_{get_day_of_week()}")
    else:
        team = cfg.daily_teams
    thread_luxcavation(combat_count)
    select_battle_team(team)
    if battle.to_battle() is False:
        return False
    battle.fight(combat_count=combat_count)
    back_init_menu()
    make_enkephalin_module()


@begin_and_finish_time_log(task_name="一次镜牢")
# 一次镜牢的过程
def onetime_mir_process(team_setting: TeamSetting, team_num: int):
    """返回 True/False 表示是否成功。完成后通过 onetime_mir_process.last_mirror_stats 获取统计数据"""
    global _last_mirror_stats
    _last_mirror_stats = {}
    # 实时检查是否需要切换到困难镜牢
    if cfg.auto_hard_mirror and check_hard_mirror_time():
        log.info("检测到新的困牢周期，实时切换困难镜牢，设置困牢次数为3")
        cfg.set_value("last_auto_change", datetime.now().timestamp())
        cfg.set_value("hard_mirror", True)
        cfg.set_value("hard_mirror_chance", 3)

    # 进行一次镜牢
    try:
        mirror_adventure = Mirror(team_setting, team_num)
        result = mirror_adventure.run()
        _last_mirror_stats = mirror_adventure.get_run_stats()
        del mirror_adventure
        mirror_adventure = None
        if result:
            back_init_menu()
            make_enkephalin_module()
            return True
        else:
            return False
    except Exception as e:
        log.exception(f"镜牢行动出错: {e}")
        return False


def to_get_reward():
    if cfg.set_get_prize == 0:
        back_init_menu()
        get_pass_prize()
        back_init_menu()
        get_mail_prize()
        back_init_menu()
    elif cfg.set_get_prize == 1:
        back_init_menu()
        get_pass_prize()
        back_init_menu()
    else:
        back_init_menu()
        get_mail_prize()
        back_init_menu()


def init_game():
    log.debug("初始化游戏")
    if cfg.simulator:
        if cfg.simulator_type == 0:
            mumu_instance_number = 0
            if cfg.simulator_port == 0 and cfg.mumu_instance_number == -1:
                log.info("未设置模拟器端口或实例编号，使用默认mumu模拟器")
            elif cfg.simulator_port != 0:
                if cfg.simulator_port == 16384 or (cfg.simulator_port - 16384) % 32 == 0:
                    mumu_instance_number = 0 if cfg.simulator_port == 16384 else (cfg.simulator_port - 16384) // 32
                    log.debug(f"使用mumu模拟器实例号为 {mumu_instance_number}")
                else:
                    log.info("设置的模拟器端口非常用默认端口，使用默认mumu模拟器")
            elif cfg.mumu_instance_number != -1:
                mumu_instance_number = cfg.mumu_instance_number
            log.debug(
                f"init_game: 模拟器类型=Mumu, 实例编号={mumu_instance_number}, "
                f"simulator_port={cfg.simulator_port}, mumu_instance_number={cfg.mumu_instance_number}"
            )
            from module.automation.input_handlers.simulator.mumu_control import (
                MumuControl,
            )

            MumuControl(instance_number=mumu_instance_number)
        else:
            from module.automation.input_handlers.simulator.simulator_control import (
                SimulatorControl,
            )

            # 启动时先清理旧连接
            SimulatorControl.clean_connect()
            SimulatorControl()
    auto.init_input()
    if cfg.simulator:
        if cfg.simulator_type == 0:
            from module.automation.input_handlers.simulator.mumu_control import (
                MumuControl,
            )

            MumuControl.connection_device.start_game()
        else:
            from module.automation.input_handlers.simulator.simulator_control import (
                SimulatorControl,
            )

            SimulatorControl.connection_device.start_game()
    else:
        game_process.start_game()
        while not screen.init_handle():
            sleep(10)
        if cfg.set_windows:
            screen.set_win()


def Resonate_with_Ahab():
    random_number = random.randint(1, 4)
    playsound(f"assets/audio/This_is_all_your_fault_{random_number}.mp3", block=False)


def _get_game_rendering_scale() -> int | None:
    """读取非模拟器模式下 Limbus 的渲染比例设置。"""
    try:
        import json
        import winreg

        root = winreg.HKEY_CURRENT_USER
        sub_key = r"Software\ProjectMoon\LimbusCompany"
        value_name = "LocalSave.LocalGameOptionData_h467498167"
        with winreg.OpenKey(root, sub_key, 0, winreg.KEY_READ) as key:
            raw_data, reg_type = winreg.QueryValueEx(key, value_name)

        if reg_type != winreg.REG_BINARY:
            log.debug(f"游戏设置注册表值类型为 {reg_type}，预期为 REG_BINARY")
            return None

        json_str = raw_data.rstrip(b"\x00").decode("utf-8")
        game_config = json.loads(json_str)
        return game_config.get("_renderingScale")
    except FileNotFoundError:
        log.debug(r"游戏设置注册表路径不存在: HKEY_CURRENT_USER\Software\ProjectMoon\LimbusCompany")
    except PermissionError:
        log.debug("读取游戏设置注册表时权限不足")
    except Exception as e:
        log.debug(f"读取游戏渲染比例失败: {e}")
    return None


def _batch_combat(process_fn, times, max_times):
    """按 max_times 分批执行战斗"""
    if times <= 0:
        return
    if times > max_times:
        once = max_times
        total = times // max_times
        last = times % max_times
    else:
        once = times
        total = 0
        last = times
    for _ in range(total):
        process_fn(once)
    if last > 0:
        process_fn(last)


def _single_combat_run(exp_times, thread_times):
    for _ in range(exp_times):
        onetime_EXP_process()
    for _ in range(thread_times):
        onetime_thread_process()


def Daily_task_wrapper(get_reward=None):
    def wrapper():
        back_init_menu()
        make_enkephalin_module()
        exp_times = cfg.set_EXP_count
        if get_reward and get_reward == "EXP":
            exp_times -= 1
        thread_times = cfg.set_thread_count
        if get_reward and get_reward == "thread":
            thread_times -= 1
        if cfg.config.use_continuous_combat and cfg.use_continuous_combat_select > 0:
            max_times = cfg.use_continuous_combat_select
            _batch_combat(onetime_EXP_process, exp_times, max_times)
            _batch_combat(onetime_thread_process, thread_times, max_times)
        else:
            _single_combat_run(exp_times, thread_times)

    return wrapper


def Buy_enkephalin():
    times = cfg.set_lunacy_to_enkephalin
    if times == 0:
        return
    back_init_menu()
    lunacy_to_enkephalin(times=times)


def _save_mirror_records(records: list[dict]) -> None:
    """将本轮 run_records 追加保存到 logs/mirror_history.json（持久化）"""
    import json
    history_path = Path("./logs/mirror_history.json")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if history_path.exists():
        try:
            existing = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    existing.extend(records)
    history_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_mirror_records() -> list[dict]:
    """从 logs/mirror_history.json 加载历史镜牢记录"""
    import json
    history_path = Path("./logs/mirror_history.json")
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def Mirror_task():
    # 判断执行镜牢任务的次数
    mir_times = cfg.set_mirror_count
    if cfg.infinite_dungeons:
        mir_times = 9999
    if cfg.save_rewards and cfg.hard_mirror:
        mir_times = 1
    finish_times = 0
    run_records = []  # 收集每次镜牢的详细数据
    mediator.mirror_signal.emit(0, mir_times)
    cfg.normalize_and_sync_team_state(persist=False)
    # 开始执行镜牢任务
    while mir_times > 0:
        # 检测配置的队伍能否顺利执行
        useful = False
        hard = bool(cfg.hard_mirror)
        teams_be_select = cfg.get_value("teams_be_select")
        for index in (i for i, t in enumerate(teams_be_select) if t is True):
            team_setting = cfg.config.teams[f"{index + 1}"]
            if team_setting.fixed_team_use is False:
                useful = True
                break
            if team_setting.fixed_team_use_select == 1 and hard is False:
                useful = True
                break
            if team_setting.fixed_team_use_select == 0 and hard is True:
                useful = True
                break
        if useful is False:
            break

        if not cfg.teams_active_queue:
            break

        team_num = cfg.teams_active_queue[0]
        team_setting = cfg.config.teams[f"{team_num}"]
        # 如果该队伍固定了用途，且用途不符合当前情况，将队首队伍轮转到队尾
        if team_setting.fixed_team_use:
            if (team_setting.fixed_team_use_select == 0 and not cfg.hard_mirror) or (
                team_setting.fixed_team_use_select == 1 and cfg.hard_mirror
            ):
                cfg.rotate_team_queue()
                continue
        # 执行一次镜牢任务，根据执行结果进行处理
        mirror_result = onetime_mir_process(team_setting, team_num)
        stats = _last_mirror_stats
        if stats:
            stats["timestamp"] = datetime.now().isoformat(timespec="seconds")
            stats["team_name"] = team_setting.remark_name or f"队伍{team_num}"
            run_records.append(stats)
        if mirror_result:
            cfg.rotate_team_queue()
            mir_times -= 1
            if cfg.hard_mirror and cfg.auto_hard_mirror:
                chance = cfg.hard_mirror_chance - 1
                cfg.set_value("hard_mirror_chance", chance)
                if chance == 0:
                    cfg.set_value("hard_mirror", False)

            # 更新进度条
            finish_times += 1
            mediator.mirror_signal.emit(finish_times, mir_times)
            msg = f"已完成 {finish_times} 次镜牢"
            log.info(msg)
            # 每轮结束输出摘要日志（实时可看）
            if stats:
                diff_label = "困难" if stats.get("hard") else "普通"
                summary = (f"  第{finish_times}轮 | {stats.get('team_name', '?')} | {diff_label} | "
                           f"耗时{_format_mmss(stats.get('elapsed', 0))} | "
                           f"{stats.get('pass_coins', 0)}经验 | {stats.get('floor', 0)}层")
                log.info(summary)
            if finish_times == 1 and cfg.re_claim_rewards:  # 完成第一次镜牢后重新领取奖励
                to_get_reward()

    if run_records:
        log.info(f"开始生成镜牢统计，共 {len(run_records)} 条记录")
        filepath = None
        chart_files: list[str] = []
        try:
            filepath = generate_mirror_stats_excel(run_records)
            log.debug("Excel 统计报告生成完毕")
        except Exception:
            log.exception("生成 Excel 统计报告失败，继续生成图表")
        try:
            chart_files = generate_mirror_charts(run_records)
            log.debug(f"图表生成完毕，共 {len(chart_files)} 张")
        except Exception:
            log.exception("生成图表失败")
        _save_mirror_records(run_records)
        # 用全量历史数据重新生成图表（包含之前累积的记录）
        all_records = _load_mirror_records()
        if len(all_records) > len(run_records):
            try:
                chart_files = generate_mirror_charts(all_records)
                log.debug(f"基于全量 {len(all_records)} 条历史记录重新生成 {len(chart_files)} 张图表")
            except Exception:
                log.exception("生成全量图表失败")
        data = {"excel": filepath or "", "charts": chart_files}
        log.info(f"镜牢统计已导出: {filepath}")
        mediator.mirror_stats_signal.emit(data)

    mediator.mirror_bar_kill_signal.emit()
    if cfg.re_claim_rewards and finish_times > 0:
        to_get_reward()


def _blue_header(ws, row, col, value):
    """写入蓝底白字表头"""
    from openpyxl.styles import Font, PatternFill
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=True, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="4472C4")
    return c


def _fmt_seconds(sec: float) -> str:
    """格式化秒数为 分:秒"""
    return f"{int(sec // 60)}分{int(sec % 60)}秒"


def _has_multi_teams(records: list[dict]) -> bool:
    """是否有 ≥2 支不同队伍"""
    teams = {r.get("team") for r in records}
    return len(teams) >= 2


def _has_both_difficulties(records: list[dict]) -> bool:
    """是否同时跑了困难和普通"""
    modes = {r.get("hard") for r in records}
    return True in modes and False in modes


def _build_team_sheet(wb, records: list[dict]):
    """构建队伍对比 Sheet，返回 worksheet 或 None"""
    if not _has_multi_teams(records):
        return None

    from openpyxl.styles import Font, PatternFill
    from collections import defaultdict
    groups: dict[int, dict] = defaultdict(lambda: {"times": 0, "elapsed": [], "pass_coins": []})
    for r in records:
        t = r.get("team", 0)
        groups[t]["times"] += 1
        groups[t]["elapsed"].append(r.get("elapsed", 0))
        groups[t]["pass_coins"].append(r.get("pass_coins", 0))
        groups[t]["name"] = r.get("team_name", f"队伍{t}")

    ws = wb.create_sheet("队伍对比")
    headers = ["队伍名称", "次数", "平均耗时(秒)", "总通行证经验", "平均耗时", "平均经验/次"]
    for col, h in enumerate(headers, 1):
        _blue_header(ws, 1, col, h)

    row = 2
    for team_id in sorted(groups):
        g = groups[team_id]
        avg_t = sum(g["elapsed"]) / g["times"]
        avg_p = sum(g["pass_coins"]) / g["times"]
        ws.cell(row=row, column=1, value=g["name"])
        ws.cell(row=row, column=2, value=g["times"])
        ws.cell(row=row, column=3, value=round(avg_t))
        ws.cell(row=row, column=4, value=sum(g["pass_coins"]))
        ws.cell(row=row, column=5, value=_fmt_seconds(avg_t))
        ws.cell(row=row, column=6, value=round(avg_p))
        row += 1

    for col in range(1, len(headers) + 1):
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(col)].width = 16

    # 队伍对比柱状图
    if len(groups) >= 2:
        from openpyxl.chart import BarChart, Reference
        chart = BarChart()
        chart.title = "队伍平均耗时对比"
        chart.style = 10
        chart.width = 20; chart.height = 12
        data = Reference(ws, min_col=3, max_col=3, min_row=1, max_row=len(groups) + 1)
        cats = Reference(ws, min_col=1, min_row=2, max_row=len(groups) + 1)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        ws.add_chart(chart, f"A{row + 2}")

    return ws


def generate_mirror_stats_excel(run_records: list[dict]) -> str | None:
    """生成镜牢统计 Excel 报告（含自动裁剪的图表）"""
    try:
        import openpyxl
        from openpyxl.chart import (
            AreaChart, BarChart, LineChart, PieChart, RadarChart,
            ScatterChart, Reference, series,
        )
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        log.warning("openpyxl 未安装，无法生成 Excel，请运行: pip install openpyxl")
        return None

    output_dir = Path("./logs")
    output_dir.mkdir(exist_ok=True)
    filename = "mirror_stats.xlsx"  # 固定文件名，每轮覆盖更新（全量快照）
    filepath = str(output_dir / filename)

    wb = openpyxl.Workbook()
    n = len(run_records)
    total_coins = sum(r.get("pass_coins", 0) for r in run_records)
    total_time = sum(r.get("elapsed", 0) for r in run_records)

    # ============================
    # Sheet 1: 摘要
    # ============================
    ws1 = wb.active
    ws1.title = "摘要"
    ws1.merge_cells("A1:D1")
    c = ws1["A1"]; c.value = "镜牢运行统计报告"; c.font = Font(bold=True, size=14)
    c.alignment = Alignment(horizontal="center")
    ws1["A2"].value = f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ws1["A2"].font = Font(color="666666")

    for col, h in enumerate(["指标", "数值"], 1):
        _blue_header(ws1, 4, col, h)

    hard_n = sum(1 for r in run_records if r.get("hard"))
    summary = [
        ("镜牢次数", n), ("困难镜牢", f"{hard_n} 次"), ("普通镜牢", f"{n - hard_n} 次"),
        ("总通行证经验", total_coins), ("总耗时", _fmt_seconds(total_time)),
        ("平均每轮耗时", _fmt_seconds(total_time / n) if n else "-"),
        ("总事件次数", sum(r.get("event_count", 0) for r in run_records)),
        ("通行证经验/分钟", round(total_coins / (total_time / 60), 1) if total_time else "-"),
    ]
    for i, (k, v) in enumerate(summary, 5):
        ws1.cell(row=i, column=1, value=k); ws1.cell(row=i, column=2, value=v)
    ws1.column_dimensions["A"].width = 20; ws1.column_dimensions["B"].width = 20

    # ============================
    # Sheet 2: 详细记录
    # ============================
    ws2 = wb.create_sheet("详细记录")
    headers = ["序号", "时间", "队伍", "难度", "楼层", "通行证经验", "耗时(秒)", "战斗时间", "事件次数", "商店时间", "寻路时间"]
    for col, h in enumerate(headers, 1):
        _blue_header(ws2, 1, col, h)

    for i, rec in enumerate(run_records, 2):
        ws2.cell(row=i, column=1, value=i - 1)
        ws2.cell(row=i, column=2, value=rec.get("timestamp", ""))
        ws2.cell(row=i, column=3, value=rec.get("team_name", ""))
        ws2.cell(row=i, column=4, value="困难" if rec.get("hard") else "普通")
        ws2.cell(row=i, column=5, value=rec.get("floor", 0))
        ws2.cell(row=i, column=6, value=rec.get("pass_coins", 0))
        ws2.cell(row=i, column=7, value=round(rec.get("elapsed", 0)))
        ws2.cell(row=i, column=8, value=round(rec.get("battle_time", 0)))
        ws2.cell(row=i, column=9, value=rec.get("event_count", 0))
        ws2.cell(row=i, column=10, value=round(rec.get("shop_time", 0)))
        ws2.cell(row=i, column=11, value=round(rec.get("find_road_time", 0)))
    for col in range(1, len(headers) + 1):
        ws2.column_dimensions[get_column_letter(col)].width = 14 if col > 1 else 6

    # ============================
    # Sheet 3: 图表
    # ============================
    ws3 = wb.create_sheet("图表")
    chart_row = 1

    def _add_chart(chart, title=""):
        nonlocal chart_row
        chart.title = title or chart.title
        chart.style = 10
        chart.width = 20; chart.height = 12
        ws3.add_chart(chart, f"A{chart_row}")
        chart_row += 18

    # 1. 每次耗时柱状图 (≥2次)
    if n >= 2:
        bc = BarChart()
        bc.title = "每次镜牢耗时 (秒)"
        bc.y_axis.title = "秒"
        bc.add_data(Reference(ws2, min_col=7, min_row=1, max_row=n + 1), titles_from_data=True)
        bc.set_categories(Reference(ws2, min_col=1, min_row=2, max_row=n + 1))
        _add_chart(bc)

    # 2. 通行证经验累计折线图 (≥2次)
    if n >= 2:
        # 在 ws2 末尾追加累计列
        cum_col = len(headers) + 1
        ws2.cell(row=1, column=cum_col, value="累计通行证经验")
        cum = 0
        for i, rec in enumerate(run_records, 2):
            cum += rec.get("pass_coins", 0)
            ws2.cell(row=i, column=cum_col, value=cum)

        lc = LineChart()
        lc.title = "通行证经验累计趋势"
        lc.y_axis.title = "累计通行证经验"
        lc.add_data(Reference(ws2, min_col=cum_col, min_row=1, max_row=n + 1), titles_from_data=True)
        lc.set_categories(Reference(ws2, min_col=1, min_row=2, max_row=n + 1))
        _add_chart(lc)

    # 3. 时间分布饼图
    battle_sum = sum(r.get("battle_time", 0) for r in run_records)
    event_sum = sum(r.get("event_time", 0) for r in run_records)
    shop_sum = sum(r.get("shop_time", 0) for r in run_records)
    road_sum = sum(r.get("find_road_time", 0) for r in run_records)
    if any([battle_sum, event_sum, shop_sum, road_sum]):
        pie = PieChart()
        pie.title = "时间分布占比"
        # 写入临时数据到 ws3
        pie_labels = ["战斗", "事件", "商店", "寻路"]
        pie_values = [battle_sum, event_sum, shop_sum, road_sum]
        for i, (lb, vl) in enumerate(zip(pie_labels, pie_values), 1):
            ws3.cell(row=chart_row + 1, column=1, value=lb)
            ws3.cell(row=chart_row + 1, column=2, value=round(vl))
            chart_row += 1  # 临时移动
        chart_row -= 4
        pie.add_data(Reference(ws3, min_col=2, min_row=chart_row + 1, max_row=chart_row + 4))
        pie.set_categories(Reference(ws3, min_col=1, min_row=chart_row + 1, max_row=chart_row + 4))
        pie.width = 20; pie.height = 12; pie.style = 10
        ws3.add_chart(pie, f"A{chart_row + 6}")
        chart_row += 22

    # 4. 累计耗时面积图 (≥2次)
    if n >= 2:
        cum_col2 = len(headers) + 2
        ws2.cell(row=1, column=cum_col2, value="累计耗时(秒)")
        cum_t = 0
        for i, rec in enumerate(run_records, 2):
            cum_t += rec.get("elapsed", 0)
            ws2.cell(row=i, column=cum_col2, value=round(cum_t))

        ac = AreaChart()
        ac.title = "累计耗时增长"
        ac.add_data(Reference(ws2, min_col=cum_col2, min_row=1, max_row=n + 1), titles_from_data=True)
        ac.set_categories(Reference(ws2, min_col=1, min_row=2, max_row=n + 1))
        _add_chart(ac)

    # 5. 散点图：耗时 vs 通行证经验 (≥2次)
    if n >= 2:
        # 临时数据列
        sc_col = len(headers) + 3
        ws2.cell(row=1, column=sc_col, value="散点_耗时")
        sc_col2 = len(headers) + 4
        ws2.cell(row=1, column=sc_col2, value="散点_经验")
        for i, rec in enumerate(run_records, 2):
            ws2.cell(row=i, column=sc_col, value=round(rec.get("elapsed", 0)))
            ws2.cell(row=i, column=sc_col2, value=rec.get("pass_coins", 0))

        sc = ScatterChart()
        sc.title = "耗时 vs 通行证经验"
        sc.x_axis.title = "耗时(秒)"; sc.y_axis.title = "通行证经验"
        sc.add_data(Reference(ws2, min_col=sc_col2, min_row=1, max_row=n + 1), titles_from_data=True)
        sc.set_categories(Reference(ws2, min_col=sc_col, min_row=1, max_row=n + 1))
        _add_chart(sc)

    # 6. 雷达图：综合评分 (≥3次)
    if n >= 3:
        avg_elapsed = total_time / n
        avg_coins = total_coins / n
        avg_events = sum(r.get("event_count", 0) for r in run_records) / n
        avg_floor = sum(r.get("floor", 0) for r in run_records) / n

        # 标准化到 0-100（反向指标如耗时越低越好）
        max_e = max(r.get("elapsed", 1) for r in run_records)
        max_p = max(1, max(r.get("pass_coins", 1) for r in run_records))

        # 写入雷达数据
        cat_row = chart_row
        val_row = chart_row + 1
        categories = ["速度得分", "收益得分", "事件得分", "楼层得分"]
        scores = [
            round((1 - avg_elapsed / max_e) * 100),  # 速度（耗时越短越高）
            round(avg_coins / max_p * 100),            # 收益
            round(min(avg_events / 15 * 100, 100)),    # 事件
            round(avg_floor / 5 * 100),                 # 楼层
        ]
        ws3.cell(row=cat_row, column=1, value="维度")
        ws3.cell(row=val_row, column=1, value="综合评分")
        for j, (cat, score) in enumerate(zip(categories, scores), 2):
            ws3.cell(row=cat_row, column=j, value=cat)
            ws3.cell(row=val_row, column=j, value=score)

        rc = RadarChart()
        rc.title = "镜牢综合评分"
        rc.add_data(Reference(ws3, min_col=1, max_col=len(categories) + 1, min_row=val_row, max_row=val_row))
        rc.set_categories(Reference(ws3, min_col=2, max_col=len(categories) + 1, min_row=cat_row, max_row=cat_row))
        rc.width = 20; rc.height = 12; rc.style = 10
        ws3.add_chart(rc, f"A{val_row + 3}")
        chart_row = val_row + 19

    # ============================
    # 多队伍图表
    # ============================
    _build_team_sheet(wb, run_records)

    # ============================
    # 困难 vs 普通图表
    # ============================
    if _has_both_difficulties(run_records):
        ws_d = wb.create_sheet("难度对比")
        _blue_header(ws_d, 1, 1, "难度"); _blue_header(ws_d, 1, 2, "次数"); _blue_header(ws_d, 1, 3, "平均耗时(秒)")
        _blue_header(ws_d, 1, 4, "平均通行证经验")

        for mode_label, mode_flag in [("困难", True), ("普通", False)]:
            subset = [r for r in run_records if r.get("hard") == mode_flag]
            if not subset:
                continue
            cnt = len(subset)
            avg_t = sum(r.get("elapsed", 0) for r in subset) / cnt
            avg_p = sum(r.get("pass_coins", 0) for r in subset) / cnt
            row = 2 if mode_flag else 3
            ws_d.cell(row=row, column=1, value=mode_label)
            ws_d.cell(row=row, column=2, value=cnt)
            ws_d.cell(row=row, column=3, value=round(avg_t))
            ws_d.cell(row=row, column=4, value=round(avg_p))

        for col in range(1, 5):
            ws_d.column_dimensions[get_column_letter(col)].width = 18

        # 对比柱状图
        bc2 = BarChart()
        bc2.title = "困难 vs 普通 平均耗时(秒)"
        bc2.style = 10; bc2.width = 20; bc2.height = 12
        bc2.add_data(Reference(ws_d, min_col=3, max_col=3, min_row=1, max_row=3), titles_from_data=True)
        bc2.set_categories(Reference(ws_d, min_col=1, min_row=2, max_row=3))
        ws_d.add_chart(bc2, "A6")

        bc3 = BarChart()
        bc3.title = "困难 vs 普通 平均通行证经验"
        bc3.style = 10; bc3.width = 20; bc3.height = 12
        bc3.add_data(Reference(ws_d, min_col=4, max_col=4, min_row=1, max_row=3), titles_from_data=True)
        bc3.set_categories(Reference(ws_d, min_col=1, min_row=2, max_row=3))
        ws_d.add_chart(bc3, "A24")

    # ============================
    # 保存
    # ============================
    wb.save(filepath)
    return filepath


def _format_mmss(seconds: float) -> str:
    """秒数格式化为 mm:ss"""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def generate_mirror_charts(run_records: list[dict]) -> list[str]:
    """生成镜牢统计 PNG 图表，返回文件路径列表"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib 未安装，无法生成图表")
        return []

    # 中文字体回退
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False

    output_dir = Path("./logs/charts")
    output_dir.mkdir(parents=True, exist_ok=True)
    # 每次生成前清空旧图表（覆盖而非累积）
    for old_chart in output_dir.glob("chart_*.png"):
        try:
            old_chart.unlink()
        except OSError:
            pass
    chart_files = []
    n = len(run_records)
    if n == 0:
        return []

    # 颜色
    blue = "#4472C4"
    colors = ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#5B9BD5", "#70AD47"]

    # ---- 1. 每次耗时柱状图（X轴 = team_name）----
    chart_w = max(6, min(12, n * 1.5))
    fig, ax = plt.subplots(figsize=(chart_w, 7))
    names = [r.get("team_name", f"#{i}") for i, r in enumerate(run_records, 1)]
    max_name_len = 8 if n <= 2 else 12
    names = [s if len(s) <= max_name_len else s[:max_name_len - 1] + "…" for s in names]
    elapsed = [r.get("elapsed", 0) for r in run_records]
    x = list(range(len(names)))
    bar_w = 0.4 if n == 1 else 0.6
    bars = ax.bar(x, elapsed, width=bar_w, color=blue, edgecolor="white")
    for bar, val in zip(bars, elapsed):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(elapsed) * 0.02,
                _format_mmss(val), ha="center", fontsize=9)
    ax.set_title("每次镜牢耗时", fontsize=14, fontweight="bold", pad=20)
    ax.set_xlabel("队伍", labelpad=12)
    ax.set_ylabel("耗时 (秒)", labelpad=12)
    ax.set_xticks(x)
    rot = 0 if n <= 2 else (15 if n <= 4 else 25)
    ax.set_xticklabels(names, rotation=rot, ha="center", fontsize=10)
    ax.set_xlim(-0.6, len(names) - 0.4)
    fp = str(output_dir / "chart_1_elapsed.png")
    fig.savefig(fp, dpi=120, bbox_inches="tight", pad_inches=0.6)
    plt.close(fig)
    chart_files.append(fp)

    # ---- 2. 通行证经验累计趋势图（Y轴从第一条经验起始）----
    fig, ax = plt.subplots(figsize=(chart_w, 7))
    cumulative = []
    acc = 0
    for r in run_records:
        acc += r.get("pass_coins", 0)
        cumulative.append(acc)
    y_bottom = max(0, cumulative[0] * 0.85 if cumulative[0] > 0 else 0)
    y_top = max(cumulative) * 1.18 if max(cumulative) > 0 else 100
    ax.set_ylim(y_bottom, y_top)
    ms = 12 if n <= 2 else 7
    ax.plot(x, cumulative, "o-", color=colors[1], linewidth=2.5, markersize=ms,
            markerfacecolor="white", markeredgewidth=2)
    ax.fill_between(x, cumulative, alpha=0.15, color=colors[1])
    increments = [cumulative[0]] + [cumulative[i] - cumulative[i - 1] for i in range(1, n)]
    for i, inc in enumerate(increments):
        mid_y = (cumulative[i] + (cumulative[i - 1] if i > 0 else y_bottom)) / 2
        ax.annotate(f"+{inc}", (i, mid_y), ha="center", fontsize=8, color="#888", fontstyle="italic")
    for i, v in enumerate(cumulative):
        ax.text(i, v + (y_top - y_bottom) * 0.025, str(v), ha="center", fontsize=10, fontweight="bold")
    ax.set_title("通行证经验累计趋势", fontsize=14, fontweight="bold", pad=20)
    ax.set_xlabel("队伍", labelpad=12)
    ax.set_ylabel("累计通行证经验", labelpad=12)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=rot, ha="center", fontsize=10)
    ax.set_xlim(-0.6, len(names) - 0.4)
    fp = str(output_dir / "chart_2_coins.png")
    fig.savefig(fp, dpi=120, bbox_inches="tight", pad_inches=0.6)
    plt.close(fig)
    chart_files.append(fp)

    # ---- 3. 时间分布饼图 ----
    battle_sum = sum(r.get("battle_time", 0) for r in run_records)
    event_sum = sum(r.get("event_time", 0) for r in run_records)
    shop_sum = sum(r.get("shop_time", 0) for r in run_records)
    road_sum = sum(r.get("find_road_time", 0) for r in run_records)
    if any([battle_sum, event_sum, shop_sum, road_sum]):
        fig, ax = plt.subplots(figsize=(9, 9))
        labels = ["战斗", "事件", "商店", "寻路"]
        sizes = [battle_sum, event_sum, shop_sum, road_sum]
        explode = (0.02, 0.02, 0.02, 0.02)
        wedges, texts, autotexts = ax.pie(sizes, explode=explode, labels=labels,
            colors=colors[:4], autopct="%1.1f%%", startangle=90,
            textprops={"fontsize": 11})
        for t in autotexts:
            t.set_fontsize(12)
            t.set_fontweight("bold")
        ax.set_title("时间分布占比", fontsize=14, fontweight="bold", pad=25)
        fp = str(output_dir / "chart_3_pie.png")
        fig.savefig(fp, dpi=120, bbox_inches="tight", pad_inches=0.6)
        plt.close(fig)
        chart_files.append(fp)

    # ---- 4. 队伍对比（≥2队）----
    teams_map: dict[str, list] = {}
    for r in run_records:
        tn = r.get("team_name", f"队伍{r.get('team')}")
        if tn not in teams_map:
            teams_map[tn] = []
        teams_map[tn].append(r.get("elapsed", 0))
    if len(teams_map) >= 2:
        team_w = max(6, len(teams_map) * 1.6)
        fig, ax = plt.subplots(figsize=(team_w, 7))
        tnames = list(teams_map.keys())
        tnames = [s if len(s) <= 8 else s[:7] + "…" for s in tnames]
        avgs = [sum(v) / len(v) for v in teams_map.values()]
        bars = ax.bar(range(len(tnames)), avgs, width=0.45, color=colors[:len(tnames)], edgecolor="white")
        for bar, val in zip(bars, avgs):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(avgs) * 0.02,
                    _format_mmss(val), ha="center", fontsize=10)
        ax.set_title("队伍平均耗时对比", fontsize=14, fontweight="bold", pad=20)
        ax.set_ylabel("平均耗时 (秒)", labelpad=12)
        ax.set_xticks(range(len(tnames)))
        rot4 = 0 if len(tnames) <= 3 else 15
        ax.set_xticklabels(tnames, rotation=rot4, ha="center", fontsize=10)
        fp = str(output_dir / "chart_4_teams.png")
        fig.savefig(fp, dpi=120, bbox_inches="tight", pad_inches=0.6)
        plt.close(fig)
        chart_files.append(fp)

    # ---- 5. 困难 vs 普通 ----
    hard_recs = [r for r in run_records if r.get("hard")]
    normal_recs = [r for r in run_records if not r.get("hard")]
    if hard_recs and normal_recs:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 7))
        h_avg_time = sum(r["elapsed"] for r in hard_recs) / len(hard_recs)
        n_avg_time = sum(r["elapsed"] for r in normal_recs) / len(normal_recs)
        ax1.bar(["困难", "普通"], [h_avg_time, n_avg_time], color=[colors[1], colors[0]], edgecolor="white")
        ax1.set_title("平均耗时对比", fontweight="bold", pad=15)
        ax1.set_ylabel("秒", labelpad=10)
        h_avg_coins = sum(r["pass_coins"] for r in hard_recs) / len(hard_recs)
        n_avg_coins = sum(r["pass_coins"] for r in normal_recs) / len(normal_recs)
        ax2.bar(["困难", "普通"], [h_avg_coins, n_avg_coins], color=[colors[1], colors[0]], edgecolor="white")
        ax2.set_title("平均通行证经验对比", fontweight="bold", pad=15)
        ax2.set_ylabel("经验", labelpad=10)
        fig.suptitle("困难 vs 普通镜牢", fontsize=14, fontweight="bold")
        fig.subplots_adjust(top=0.85)
        fp = str(output_dir / "chart_5_difficulty.png")
        fig.savefig(fp, dpi=120, bbox_inches="tight", pad_inches=0.6)
        plt.close(fig)
        chart_files.append(fp)

    # ---- 6. 耗时 vs 经验散点图（效率分布）----
    if n >= 3:
        fig, ax = plt.subplots(figsize=(10, 8))
        for r in run_records:
            marker = "D" if r.get("hard") else "o"
            c = colors[1] if r.get("hard") else blue
            lb = r.get("team_name", "")[:6]
            ax.scatter(r.get("elapsed", 0), r.get("pass_coins", 0),
                       s=120, c=c, marker=marker, edgecolors="white", linewidth=0.8, zorder=5)
            ax.annotate(lb, (r.get("elapsed", 0), r.get("pass_coins", 0)),
                        textcoords="offset points", xytext=(8, 6), fontsize=8,
                        fontweight="bold", color="#333")
        ax.set_title("耗时 vs 通行证经验（效率分布）", fontsize=14, fontweight="bold", pad=20)
        ax.set_xlabel("耗时 (秒)", labelpad=12)
        ax.set_ylabel("通行证经验", labelpad=12)
        ax.grid(True, alpha=0.3, linestyle="--")
        fp = str(output_dir / "chart_6_scatter.png")
        fig.savefig(fp, dpi=120, bbox_inches="tight", pad_inches=0.6)
        plt.close(fig)
        chart_files.append(fp)

    # ---- 7. 每次用时构成堆叠柱状图（≥2次）----
    if n >= 2:
        fig, ax = plt.subplots(figsize=(chart_w, 7))
        battle = [r.get("battle_time", 0) for r in run_records]
        event_t = [r.get("event_time", 0) for r in run_records]
        shop = [r.get("shop_time", 0) for r in run_records]
        road = [r.get("find_road_time", 0) for r in run_records]
        bar_w2 = 0.4 if n <= 3 else 0.6
        ax.bar(x, battle, width=bar_w2, label="战斗", color=colors[0])
        ax.bar(x, event_t, width=bar_w2, bottom=battle, label="事件", color=colors[1])
        bottom2 = [a + b for a, b in zip(battle, event_t)]
        ax.bar(x, shop, width=bar_w2, bottom=bottom2, label="商店", color=colors[2])
        bottom3 = [a + b for a, b in zip(bottom2, shop)]
        ax.bar(x, road, width=bar_w2, bottom=bottom3, label="寻路", color=colors[3])
        ax.set_title("每次用时构成", fontsize=14, fontweight="bold", pad=20)
        ax.set_xlabel("队伍", labelpad=12)
        ax.set_ylabel("耗时 (秒)", labelpad=12)
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=rot, ha="center", fontsize=10)
        ax.set_xlim(-0.6, len(names) - 0.4)
        ax.legend(loc="upper right", fontsize=9)
        fp = str(output_dir / "chart_7_stack.png")
        fig.savefig(fp, dpi=120, bbox_inches="tight", pad_inches=0.6)
        plt.close(fig)
        chart_files.append(fp)

    # ---- 8. 队伍耗时箱线图（≥2队，≥4次）----
    if len(teams_map) >= 2 and n >= 4:
        box_labels_all = list(teams_map.keys())
        box_labels_all = [s if len(s) <= 8 else s[:7] + "…" for s in box_labels_all]
        box_data = [vals for vals in teams_map.values() if len(vals) >= 2]
        box_labels = [name for name, vals in zip(box_labels_all, teams_map.values()) if len(vals) >= 2]
        if len(box_data) >= 2:
            box_w = max(6, len(box_data) * 1.6)
            fig, ax = plt.subplots(figsize=(box_w, 7))
            bp = ax.boxplot(box_data, labels=box_labels, patch_artist=True,
                            showmeans=True, meanprops={"marker": "D", "markerfacecolor": "red"})
            for patch, c in zip(bp["boxes"], colors[:len(box_data)]):
                patch.set_facecolor(c)
                patch.set_alpha(0.6)
            ax.set_title("队伍耗时分布（箱线图）", fontsize=14, fontweight="bold", pad=20)
            ax.set_ylabel("耗时 (秒)", labelpad=12)
            ax.tick_params(axis="x", rotation=15 if len(box_data) <= 4 else 25, labelsize=10)
            fp = str(output_dir / "chart_8_boxplot.png")
            fig.savefig(fp, dpi=120, bbox_inches="tight", pad_inches=0.6)
            plt.close(fig)
            chart_files.append(fp)

    return chart_files


def script_task() -> None | int:
    start_time = time()
    # 获取（启动）游戏对游戏窗口进行设置
    init_game()

    if cfg.skip_enkephalin:
        log.info("设置了跳过合成脑啡肽，将不会自动合成\nSet to skip make enkephalin, it will not to do")
    if not cfg.simulator:
        if _get_game_rendering_scale() == 2:
            log.warning("当前游戏渲染比例为低, 可能会导致识别错误, 建议设置为中或更高")
        if cfg.set_win_size == 720:
            log.warning("当前游戏分辨率为1280*720, 可能会导致识别错误或卡死, 建议设置为更高分辨率")

    path_manager.initialize_paths()
    auto.clear_img_cache()
    log.debug(f"初始化图片路径: {path_manager.pic_path}")

    if cfg.resonate_with_Ahab:
        Resonate_with_Ahab()

    # 如果是战斗中，先处理战斗
    get_reward = None
    if auto.click_element("battle/turn_assets.png", take_screenshot=True):
        get_reward = battle.fight()

    task_list = []
    # 执行日常刷本任务
    if cfg.daily_task:
        task_list.append(Daily_task_wrapper(get_reward=get_reward))

    # 执行奖励领取任务
    if cfg.get_reward:
        task_list.append(to_get_reward)

    # 执行狂气换饼任务
    if cfg.buy_enkephalin:
        task_list.append(Buy_enkephalin)

    # 执行镜牢任务
    if cfg.mirror:
        task_list.append(Mirror_task)

    for task in task_list:
        task()

    if cfg.set_reduce_miscontact and not cfg.simulator:
        # 任务已结束，这里只恢复游戏窗口样式，避免把前台重新切回游戏。
        screen.reset_win(activate=False)

    log.info("脚本任务已经完成")
    QT_TRANSLATE_NOOP("WindowsToast", "AALC 运行结束")
    QT_TRANSLATE_NOOP("WindowsToast", "所有任务已完成")
    dt_start = datetime.fromtimestamp(start_time)
    dt_end = datetime.fromtimestamp(time())
    duration = dt_end - dt_start
    secends = duration.total_seconds()
    minutes, seconds = divmod(secends, 60)
    hours, minutes = divmod(minutes, 60)
    run_time = f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"
    send_toast(
        "AALC 运行结束",
        ["所有任务已完成", run_time],
        template=TemplateToast.NormalTemplate,
    )
    if cfg.resonate_with_Ahab:
        Resonate_with_Ahab()

    should_exit_aalc = False
    if platform.system() == "Windows":
        actions, power_action = get_after_completion_config()
        try:
            should_exit_aalc = execute_after_completion(actions, power_action)
        except Exception:
            log.exception("脚本结束后的操作失败")

    if cfg.simulator:
        if cfg.simulator_type == 0:
            from module.automation.input_handlers.simulator.mumu_control import (
                MumuControl,
            )

            MumuControl.clean_connect()

    if should_exit_aalc:
        return 0


class my_script_task(QThread):
    def __init__(self):
        # 初始化，构造函数
        super().__init__()
        self.mutex = QMutex()

    def run(self):
        self.mutex.lock()

        try:
            self._run()
        except (
            ConnectionError,
            userStopError,
            unableToFindTeamError,
            unexpectNumError,
            cannotOperateGameError,
            netWorkUnstableError,
            backMainWinError,
            withOutGameWinError,
            notWaitError,
            withOutPicError,
            withOutAdminError,
        ) as e:
            self.exception = e
        except Exception as e:
            self.exception = e
            log.exception("脚本线程执行失败")
        finally:
            self.mutex.unlock()

        mediator.script_finished.emit()

    """def stop(self):
        self.running=False
        self.finished_signal.emit()"""

    def _run(self):
        keep_awake_enabled = bool(cfg.get_value("experimental_keep_screen_awake", False))
        try:
            if keep_awake_enabled:
                apply_power_keep_awake(True)
            ret = script_task()
            if ret == 0:
                mediator.kill_signal.emit()
        finally:
            if keep_awake_enabled:
                # 先切回 AALC 再释放线程级防息屏，避免游戏仍持有前台时继续阻止息屏。
                mediator.request_focus.emit()
                self.msleep(800)  # 覆盖 WinRT toast 异步归还焦点（延迟约 600ms），再释放防息屏
                apply_power_keep_awake(False)
            auto.clear_img_cache()
