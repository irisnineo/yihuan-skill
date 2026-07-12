#!/usr/bin/env python3
"""
异环资源规划 - 资源拆分与计算脚本

按系统日期将 resources.md 中的资源拆分为「已开放」和「未开放」，
支持用户库存、卡池截止、目标消耗、好感度和交易所自动计算。

用法:
  # 基础拆分
  python3 resource-split.py --version v1.2 --tier 大小月卡党

  # 完整规划
  python3 resource-split.py --version v1.2 --tier 大小月卡党 \\
    --draw-mode expected --profile nineo \\
    --inventory "环石:9069,限定骰子:16,三重钥匙:10,方斯:9519534" --safe-margin 7200 \\
    --target "伊洛伊:0+1" --event --include-exchange --include-affection

  # 终端表格输出（默认输出 JSON）
  python3 resource-split.py --version v1.2 --tier 大小月卡党 --format table
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path


# ── 常量 ──
CORE_TYPES = ['环石', '限定骰子', '三重钥匙', '常驻骰子', '方斯']
DRAW_COUNTS = {
    'expected': {'限定棋盘': 45, '弧盘研摹': 60},
    'max': {'限定棋盘': 90, '弧盘研摹': 80},
}


def parse_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def is_separator_row(stripped: str) -> bool:
    for ch in stripped:
        if ch not in '|-: ':
            return False
    return True


def safe_int(s: str) -> int:
    try:
        return int(s.replace(',', ''))
    except ValueError:
        return 0


def strict_int(s: str, field_name: str) -> int:
    """解析候选方案成本；格式错误时拒绝把数据静默当作 0。"""
    normalized = s.replace(',', '').strip()
    if not re.fullmatch(r'\d+', normalized):
        raise ValueError(f'{field_name}不是有效整数: {s}')
    return int(normalized)


# ── Markdown 表格解析 ──

def extract_section(text: str, section_title: str) -> str:
    """提取 ## <section_title> 到下一个 ## 之间的文本块"""
    pattern = rf'^##\s+{re.escape(section_title)}\s*$'
    lines = text.split('\n')
    start = -1
    for i, line in enumerate(lines):
        if re.match(pattern, line.strip()):
            start = i
            break
    if start == -1:
        return ''
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r'^#+\s', lines[i].strip()):
            end = i
            break
    return '\n'.join(lines[start:end])


def parse_table(text_block: str) -> list[list[str]]:
    """从文本块中提取第一个表格的数据行"""
    rows = []
    in_table = False
    for line in text_block.split('\n'):
        s = line.strip()
        if not s.startswith('|'):
            if in_table:
                break
            continue
        if not in_table:
            in_table = True
            continue
        if is_separator_row(s):
            continue
        cells = [c.strip() for c in s.split('|')]
        if cells and cells[0] == '':
            cells = cells[1:]
        if cells and cells[-1] == '':
            cells = cells[:-1]
        if cells:
            rows.append(cells)
    return rows


def parse_kv_line(text: str, key: str) -> str | None:
    """从 markdown 表格中提取键值对，如 | 版本天数 | 42 |"""
    for line in text.split('\n'):
        if f'| {key} |' in line:
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) >= 2:
                return parts[1]
    return None


# ── 单表解析器 ──

def parse_banner_rows(rows: list[list[str]]) -> list[dict]:
    """卡池信息: 类型 | 名称 | 内容 | 开始 | 结束"""
    results = []
    for row in rows:
        if len(row) >= 5:
            results.append({
                'type': row[0], 'name': row[1], 'content': row[2],
                'start': row[3], 'end': row[4],
            })
    return results


def parse_daily_rows(rows: list[list[str]]) -> list[dict]:
    results = []
    for row in rows:
        if len(row) < 6:
            continue
        results.append({
            'source': row[0], 'type': row[1], 'daily_qty': safe_int(row[2]),
            'start_date': row[3], 'end_date': row[4], 'tier': row[5],
            'note': row[6] if len(row) > 6 else '',
        })
    return results


def parse_periodic_rows(rows: list[list[str]]) -> list[dict]:
    results = []
    for row in rows:
        if len(row) < 5:
            continue
        results.append({
            'source': row[0], 'type': row[1], 'qty': safe_int(row[2]),
            'date': row[3], 'tier': row[4],
            'note': row[5] if len(row) > 5 else '',
        })
    return results


def parse_event_rows(rows: list[list[str]]) -> list[dict]:
    results = []
    for row in rows:
        if len(row) < 5:
            continue
        results.append({
            'name': row[0], 'type': row[1], 'qty': safe_int(row[2]),
            'start_date': row[3], 'end_date': row[4],
            'note': row[5] if len(row) > 5 else '',
        })
    return results


def parse_onetime_rows(rows: list[list[str]]) -> list[dict]:
    results = []
    for row in rows:
        if len(row) < 4:
            continue
        results.append({
            'source': row[0], 'type': row[1], 'qty': safe_int(row[2]), 'date': row[3],
        })
    return results


def parse_event_plan_rows(rows: list[list[str]]) -> list[dict]:
    """候选方案: 方案 | 活动环石 | 活动方斯 | 适合情况。"""
    results = []
    for row in rows:
        if len(row) < 4:
            continue
        results.append({
            'name': row[0],
            'ring_cost': strict_int(row[1], f'{row[0]}的活动环石'),
            'fs_cost': strict_int(row[2], f'{row[0]}的活动方斯'),
            'suitable_for': row[3],
        })
    return results


# ── 键值参数解析 ──

def parse_kv_pairs(s: str) -> dict[str, int]:
    """解析 '环石:9069,限定骰子:16' → {'环石': 9069, '限定骰子': 16}"""
    result = defaultdict(int)
    if not s:
        return dict(result)
    for part in s.split(','):
        part = part.strip()
        if ':' in part:
            k, v = part.split(':', 1)
            result[k.strip()] += safe_int(v)
    return dict(result)


# ── 档案解析 ──

def parse_profile_affection(text: str) -> list[dict]:
    block = extract_section(text, '好感度')
    rows = parse_table(block)
    results = []
    for row in rows:
        if len(row) < 4:
            continue
        ps = row[2].replace(',', '').replace('万', '0000')
        try:
            price = int(ps)
        except ValueError:
            price = 0
        try:
            times = int(row[3])
        except ValueError:
            times = 0
        results.append({
            'character': row[0], 'affection': row[1],
            'price': price, 'times': times,
            'daily_cost': price * times,
        })
    return results


def parse_profile_exchange_cost(text: str) -> int:
    total = 0
    for m in re.finditer(r'消耗\s*([\d,.]+)\s*万\s*方斯', text):
        total += int(float(m.group(1).replace(',', '')) * 10000)
    for m in re.finditer(r'消耗\s*([\d,]+)\s*方斯(?!\s*万)', text):
        total += int(m.group(1).replace(',', ''))
    return total


# ── 待确认项扫描 ──

def scan_pending_items(text: str) -> list[str]:
    """扫描待确认项中的估算标记"""
    block = extract_section(text, '待确认项')
    if not block:
        return []
    warnings = []
    for line in block.split('\n'):
        if line.lstrip().startswith('#'):
            continue
        if re.search(r'暂填|估算|待确认|待补', line):
            item = re.sub(r'^[\s\-*]+', '', line).strip()
            if item and len(item) > 3:
                warnings.append(item[:80])
    return warnings


# ── 过滤与分类 ──

def should_include(tier: str, item_tier: str) -> bool:
    if item_tier == '全档位':
        return True
    if tier == '零氪':
        return False
    # 小月卡党/大小月卡党 → 两种月卡党都包括
    if item_tier == '小月卡党/大小月卡党':
        return tier in ('小月卡党', '大小月卡党')
    # 仅大小月卡党 → 只有大小月卡党才有
    if item_tier == '仅大小月卡党':
        return tier == '大小月卡党'
    # 其他含"小月卡"的档位
    if '小月卡' in item_tier and tier in ('小月卡党', '大小月卡党'):
        return True
    return False


def parse_note_extras(note: str) -> dict[str, int]:
    """解析备注中的额外资源，如 '另含 300 异晶立领' → {'环石': 300}"""
    extras = defaultdict(int)
    m = re.search(r'另含\s*([\d,]+)\s*异晶', note)
    if m:
        extras['环石'] += safe_int(m.group(1))
    m = re.search(r'另含\s*([\d,]+)\s*环石', note)
    if m:
        extras['环石'] += safe_int(m.group(1))
    return dict(extras)


def is_exploration(source: str) -> bool:
    """判断一次性资源是否为探索奖励"""
    return any(kw in source for kw in ['探索', '新区域', '新地图'])


def classify_daily(item: dict, query_date: date, pool_end: date | None = None) -> dict:
    start = parse_date(item['start_date'])
    end = parse_date(item['end_date'])
    total_days = (end - start).days + 1
    effective = min(end, pool_end) if pool_end and pool_end < end else end
    effective_total = (effective - start).days + 1 if effective >= start else 0

    if query_date < start:
        ob_days, rem_days = 0, effective_total
    elif query_date > effective:
        ob_days, rem_days = effective_total, 0
    else:
        ob_days = (query_date - start).days + 1
        rem_days = effective_total - ob_days

    daily = item['daily_qty']
    result = {
        'source': item['source'], 'type': item['type'],
        'daily_qty': daily,
        'obtained_days': ob_days, 'remaining_days': rem_days,
        'obtained_qty': ob_days * daily, 'remaining_qty': rem_days * daily,
        'total_qty': total_days * daily, 'tier': item['tier'],
    }
    extras = parse_note_extras(item['note'])
    if extras:
        result['notes_extra'] = extras
    return result


def classify_dated(item: dict, date_key: str, query_date: date,
                   pool_end: date | None = None) -> dict:
    d = parse_date(item[date_key])
    item['is_open'] = d <= query_date
    item['before_pool_end'] = not (pool_end and d > pool_end)
    return item


def sum_by_type(items: list[dict]) -> dict[str, int]:
    s = defaultdict(int)
    for it in items:
        s[it['type']] += it['qty']
    return dict(s)


def merge_summaries(*ds: dict) -> dict[str, int]:
    m = defaultdict(int)
    for d in ds:
        for k, v in d.items():
            m[k] += v
    return dict(m)


def sorted_types(summary: dict) -> list[str]:
    known = [t for t in CORE_TYPES if t in summary]
    unknown = sorted(t for t in summary if t not in CORE_TYPES)
    return known + unknown


# ── 目标匹配 ──

def match_target(target_str: str, banners: list[dict], draw_mode: str) -> dict | None:
    """
    解析目标并匹配卡池信息。
    target_str: '伊洛伊:0+1' → 角色0命+1专武
    返回角色、卡池截止日期和所需抽数。
    """
    m = re.match(r'(.+?):(\d+)\+(\d+)', target_str)
    if not m:
        return None
    name = m.group(1).strip()
    cons = int(m.group(2))
    weps = int(m.group(3))
    char_count = cons + 1
    wep_count = weps

    # 找角色限定棋盘
    char_banner = next((b for b in banners
                        if b['type'] == '限定棋盘' and b['content'] == name), None)
    # 找同期的弧盘研摹
    wep_banner = None
    if char_banner:
        wep_banner = next((b for b in banners
                           if b['type'] == '弧盘研摹' and b['start'] == char_banner['start']), None)

    return {
        'character': name,
        'constellation': cons,
        'weapon': weps,
        'char_count': char_count,
        'wep_count': wep_count,
        'char_banner_name': char_banner['name'] if char_banner else None,
        'char_banner_end': char_banner['end'] if char_banner else None,
        'wep_banner_name': wep_banner['name'] if wep_banner else None,
        'wep_banner_end': wep_banner['end'] if wep_banner else None,
        'char_draws_needed': char_count * DRAW_COUNTS[draw_mode]['限定棋盘'],
        'wep_draws_needed': wep_count * DRAW_COUNTS[draw_mode]['弧盘研摹'],
    }


def compute_consumption(target_info: dict, avail_ltd: int, avail_key: int,
                        avail_stone: int, safe_margin: int | None) -> dict:
    """计算目标消耗和结余"""
    char_gap = max(0, target_info['char_draws_needed'] - avail_ltd)
    wep_gap = max(0, target_info['wep_draws_needed'] - avail_key)
    stone_needed = char_gap * 160 + wep_gap * 160
    stone_left = avail_stone - stone_needed
    safe_ok = None if safe_margin is None else stone_left >= safe_margin

    return {
        'char_draw_gap': char_gap,
        'wep_draw_gap': wep_gap,
        'stone_needed': stone_needed,
        'stone_remaining': stone_left,
        'safe_margin_ok': safe_ok,
    }


def sum_resources_between(daily_items: list[dict], periodic_items: list[dict],
                          event_items: list[dict], onetime_items: list[dict],
                          start_exclusive: date, end_inclusive: date) -> dict[str, int]:
    """汇总 (start_exclusive, end_inclusive] 内新开放的资源。"""
    if end_inclusive <= start_exclusive:
        return {}

    summary = defaultdict(int)

    for item in daily_items:
        item_start = parse_date(item['start_date'])
        item_end = parse_date(item['end_date'])
        first_day = max(item_start, start_exclusive + timedelta(days=1))
        last_day = min(item_end, end_inclusive)
        if first_day <= last_day:
            summary[item['type']] += ((last_day - first_day).days + 1) * item['daily_qty']

    for item in periodic_items:
        available_date = parse_date(item['date'])
        if start_exclusive < available_date <= end_inclusive:
            summary[item['type']] += item['qty']

    # 活动奖励仍沿用当前规则：活动开始日视为全部可获得（分阶段开放暂不实现）。
    for item in event_items:
        available_date = parse_date(item['start_date'])
        if start_exclusive < available_date <= end_inclusive:
            summary[item['type']] += item['qty']

    for item in onetime_items:
        available_date = parse_date(item['date'])
        if start_exclusive < available_date <= end_inclusive:
            summary[item['type']] += item['qty']

    return dict(summary)


# ── 格式化 ──

def fmt_num(n: int) -> str:
    """格式化数字：>=10000 用万"""
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return f"{n:,}"


# ══════════════════════════════════════════════
#  main
# ══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='异环资源拆分与计算')
    parser.add_argument('--version', required=True, help='版本目录名，如 v1.2')
    parser.add_argument('--date', default=datetime.now().strftime('%Y-%m-%d'), help='查询日期，默认今天')
    parser.add_argument('--tier', default='大小月卡党', choices=['零氪', '小月卡党', '大小月卡党'], help='收入档位')
    parser.add_argument('--draw-mode', default='expected', choices=['expected', 'max'], help='抽数计算模式：expected=期望，max=保底；默认 expected')
    parser.add_argument('--format', default='json', choices=['json', 'table'], help='输出格式，默认 json')
    parser.add_argument('--profile', default='', help='用户档案名')
    parser.add_argument('--inventory', default='', help='当前库存，如 "环石:9069,限定骰子:16"')
    parser.add_argument('--safe-margin', type=int, default=None, help='用户要求的环石安全垫；不传表示未设置')
    parser.add_argument('--packs', default='', help='礼包，如 "限定骰子:10,环石:300"')
    parser.add_argument('--target', action='append', help='抽取目标，可多次使用，如 --target "真红:0+1" --target "伊洛伊:0+1"')
    parser.add_argument('--event', action='store_true', help='参与当前版本活动并计算全部候选方案')
    parser.add_argument('--include-exchange', action='store_true', help='计入交易所成本')
    parser.add_argument('--include-affection', action='store_true', help='计入好感度成本')
    parser.add_argument('--costs', default='', help='额外资源消耗，如 "方斯:2000000"')
    args = parser.parse_args()

    if args.safe_margin is not None and args.safe_margin < 0:
        parser.error('--safe-margin 不能为负数')

    query_date = parse_date(args.date)
    pack_items = parse_kv_pairs(args.packs)
    cost_items = parse_kv_pairs(args.costs)
    inventory = parse_kv_pairs(args.inventory)

    # ── 读 resources.md ──
    script_dir = Path(__file__).parent.parent
    rpath = script_dir / 'references' / 'versions' / args.version / 'resources.md'
    if not rpath.exists():
        print(f"❌ 未找到: {rpath}", file=sys.stderr)
        sys.exit(1)
    text = rpath.read_text(encoding='utf-8')

    event_plans = []
    if args.event:
        event_path = script_dir / 'references' / 'versions' / args.version / 'events.md'
        if not event_path.exists():
            parser.error(f'当前版本缺少活动资料: {event_path}')
        event_text = event_path.read_text(encoding='utf-8')
        candidate_block = extract_section(event_text, '候选方案')
        if not candidate_block:
            parser.error(f'{event_path} 缺少“## 候选方案”')
        try:
            event_plans = parse_event_plan_rows(parse_table(candidate_block))
        except ValueError as exc:
            parser.error(f'{event_path} 的候选方案数据错误: {exc}')
        if not event_plans:
            parser.error(f'{event_path} 的候选方案表为空或格式不正确')

    version_days = safe_int(parse_kv_line(text, '版本天数') or '0')

    # ── 读取各 section ──
    daily_block = extract_section(text, '每日资源')
    periodic_block = extract_section(text, '周期资源')
    event_block = extract_section(text, '活动资源')
    onetime_block = extract_section(text, '一次性资源')
    banner_block = extract_section(text, '卡池信息')
    pending_block = extract_section(text, '待确认项')

    # ── 卡池表（用于 --target）──
    banners = parse_banner_rows(parse_table(banner_block))

    # ── 解析资源明细 ──
    daily_items = parse_daily_rows(parse_table(daily_block))
    periodic_items = parse_periodic_rows(parse_table(periodic_block))
    event_items = parse_event_rows(parse_table(event_block))
    onetime_items = parse_onetime_rows(parse_table(onetime_block))
    summary_rows = parse_table(extract_section(text, '收益汇总'))

    # ── 档位筛选 ──
    fd = [it for it in daily_items if should_include(args.tier, it['tier'])]
    fp = [it for it in periodic_items if should_include(args.tier, it['tier'])]

    # ── 多目标匹配 ──
    target_infos = []
    if args.target:
        for t_str in args.target:
            ti = match_target(t_str.strip(), banners, args.draw_mode)
            if ti:
                target_infos.append(ti)
        # 按卡池结束时间升序排列
        target_infos.sort(key=lambda t: t.get('char_banner_end') or '9999-12-31')

    # ── 分类 ──
    cd = [classify_daily(it, query_date) for it in fd]
    cp = [classify_dated(it, 'date', query_date) for it in fp]
    ce = [classify_dated(it, 'start_date', query_date) for it in event_items]
    co = [classify_dated(it, 'date', query_date) for it in onetime_items]

    # ── 分池 ──
    daily_ob = defaultdict(int)
    daily_rem = defaultdict(int)
    daily_tot = defaultdict(int)
    notes_extras = defaultdict(int)

    for d in cd:
        daily_ob[d['type']] += d['obtained_qty']
        daily_rem[d['type']] += d['remaining_qty']
        daily_tot[d['type']] += d['total_qty']
        if 'notes_extra' in d:
            for rt, qty in d['notes_extra'].items():
                notes_extras[rt] += qty
                daily_ob[rt] += qty
                daily_tot[rt] += qty

    def avail(it): return it.get('is_open', True) and it.get('before_pool_end', True)

    per_ob = [p for p in cp if avail(p)]
    per_future = [p for p in cp if not p['is_open'] and p.get('before_pool_end', True)]
    ev_ob = [e for e in ce if avail(e)]
    ev_future = [e for e in ce if not e['is_open'] and e.get('before_pool_end', True)]
    ev_excluded = [e for e in ce if not e.get('before_pool_end', True)]
    on_ob = [o for o in co if avail(o)]
    on_future = [o for o in co if not o['is_open'] and o.get('before_pool_end', True)]

    # 探索奖励检测
    expl_items = [o for o in onetime_items if is_exploration(o['source'])]

    # ── 汇总 ──
    obtained = merge_summaries(dict(daily_ob), sum_by_type(per_ob),
                                sum_by_type(ev_ob), sum_by_type(on_ob))
    remaining = merge_summaries(dict(daily_rem), sum_by_type(per_future),
                                 sum_by_type(ev_future), sum_by_type(on_future))
    total = merge_summaries(dict(daily_tot), sum_by_type(cp),
                             sum_by_type(ce), sum_by_type(co))

    # 礼包
    for rt, qty in pack_items.items():
        obtained[rt] += qty

    # 用户库存代替理论已开放
    inventory_available = None
    if inventory:
        inventory_available = merge_summaries(inventory, remaining)
        for rt, qty in pack_items.items():
            inventory_available[rt] = inventory_available.get(rt, 0) + qty

    # ── 已过天数与好感度 ──
    obtained_days = cd[0]['obtained_days'] if cd else 0
    remaining_days = cd[0]['remaining_days'] if cd else 0

    # ── 档案 ──
    profile_name = None
    affection_items = []
    exchange_cost = 0
    if args.profile:
        profile_name = args.profile
        pp = script_dir / 'profiles' / f'{args.profile}.md'
        if pp.exists():
            pt = pp.read_text(encoding='utf-8')
            affection_items = parse_profile_affection(pt)
            exchange_cost = parse_profile_exchange_cost(pt)

    # 好感度按剩余天数算
    daily_affection = sum(a['daily_cost'] for a in affection_items)
    total_affection_cost = daily_affection * remaining_days

    if exchange_cost and args.include_exchange:
        cost_items['方斯'] = cost_items.get('方斯', 0) + exchange_cost
    if affection_items and args.include_affection:
        cost_items['方斯'] = cost_items.get('方斯', 0) + total_affection_cost

    # ── 待确认项 ──
    pending_warnings = scan_pending_items(text)

    # ── 多目标按各自卡池截止时间滚动计算（自动加入 cost_items）──
    target_results = []
    if inventory:
        target_pool = dict(inventory)
        for rt, qty in pack_items.items():
            target_pool[rt] = target_pool.get(rt, 0) + qty
    else:
        target_pool = dict(obtained)

    # 消费顺序固定为：抽卡 → 活动 → 好感度/交易所/房产等长期消费。
    # 活动和其他成本已记录在 cost_items，留到所有抽卡目标完成后统一扣除。
    previous_cutoff = query_date

    for ti in target_infos:
        if not ti['char_banner_end']:
            print(f"❌ 未找到角色 {ti['character']} 的限定棋盘，无法确定资源截止日期", file=sys.stderr)
            sys.exit(2)

        cutoff = parse_date(ti['char_banner_end'])
        newly_available = sum_resources_between(
            fd, fp, event_items, onetime_items, previous_cutoff, cutoff)
        target_pool = merge_summaries(target_pool, newly_available)

        avail_ltd = max(0, target_pool.get('限定骰子', 0))
        avail_key = max(0, target_pool.get('三重钥匙', 0))
        avail_ring = target_pool.get('环石', 0)
        tr = compute_consumption(ti, avail_ltd, avail_key, avail_ring, args.safe_margin)
        tr['cutoff_date'] = ti['char_banner_end']
        tr['newly_available'] = newly_available

        # 角色先使用限定骰子、武器先使用三重钥匙，仅对不足抽数使用环石补差。
        used_ltd = min(avail_ltd, ti['char_draws_needed'])
        used_key = min(avail_key, ti['wep_draws_needed'])
        tr['limited_dice_used'] = used_ltd
        tr['triple_keys_used'] = used_key
        target_results.append({'info': ti, 'result': tr})

        cost_items['限定骰子'] = cost_items.get('限定骰子', 0) + used_ltd
        cost_items['三重钥匙'] = cost_items.get('三重钥匙', 0) + used_key
        if tr['stone_needed'] > 0:
            cost_items['环石'] = cost_items.get('环石', 0) + tr['stone_needed']
        target_pool['环石'] = tr['stone_remaining']
        target_pool['限定骰子'] = avail_ltd - used_ltd
        target_pool['三重钥匙'] = avail_key - used_key
        previous_cutoff = max(previous_cutoff, cutoff)

    # ── 最终结余（所有成本扣完后）──
    base = inventory_available if inventory_available else merge_summaries(obtained, remaining)
    balance = dict(base)
    for rt, qty in cost_items.items():
        balance[rt] = balance.get(rt, 0) - qty

    # 每个活动候选都从同一份不含活动成本的余额独立计算，不在候选之间滚动扣除。
    event_results = []
    for plan in event_plans:
        plan_balance = dict(balance)
        plan_balance['环石'] = plan_balance.get('环石', 0) - plan['ring_cost']
        plan_balance['方斯'] = plan_balance.get('方斯', 0) - plan['fs_cost']
        shortages = {
            resource_type: -qty
            for resource_type, qty in plan_balance.items()
            if qty < 0
        }
        event_results.append({
            **plan,
            'balance': plan_balance,
            'shortages': shortages,
            'safe_margin_ok': (
                None if args.safe_margin is None
                else plan_balance.get('环石', 0) >= args.safe_margin
            ),
        })

    # ═══════════════════════════════
    #  JSON 输出
    # ═══════════════════════════════
    if args.format == 'json':
        output = {
            'version': args.version,
            'query_date': args.date,
            'tier': args.tier,
            'draw_mode': args.draw_mode,
            'safe_margin': args.safe_margin,
            'daily_remaining_days': remaining_days,
            'daily_obtained_days': obtained_days,
            'inventory': inventory if inventory else None,
            'packs': pack_items,
            'costs': cost_items,
            'notes_extras': dict(notes_extras) if notes_extras else None,
            'pending_warnings': pending_warnings if pending_warnings else None,
            'exploration_extra': sum_by_type(expl_items) if expl_items else None,
            'daily': {
                'obtained': dict(daily_ob),
                'remaining': dict(daily_rem),
            },
            'summary': {
                'obtained': dict(obtained),
                'remaining': dict(remaining),
                'total': dict(total),
                'inventory_available': dict(inventory_available) if inventory_available else None,
                'balance': dict(balance),
                'balance_scope': '不含活动成本' if args.event else '全部消费后',
                'final_safe_margin_ok': (
                    None if args.event or args.safe_margin is None
                    else balance.get('环石', 0) >= args.safe_margin
                ),
            },
            'profile': {
                'name': profile_name,
                'version_days': version_days,
                'obtained_days': obtained_days,
                'remaining_days': remaining_days,
                'affection': [
                    {'character': a['character'], 'price': a['price'],
                     'times': a['times'], 'daily_cost': a['daily_cost']}
                    for a in affection_items
                ] if affection_items else None,
                'affection_daily_total': daily_affection,
                'affection_remaining_total': total_affection_cost,
                'exchange_cost': exchange_cost or None,
            } if profile_name else None,
            'target': [{
                'character': tr['info']['character'],
                'constellation': tr['info']['constellation'],
                'weapon': tr['info']['weapon'],
                'char_draws_needed': tr['info']['char_draws_needed'],
                'wep_draws_needed': tr['info']['wep_draws_needed'],
                'char_banner_end': tr['info']['char_banner_end'],
                'wep_banner_end': tr['info']['wep_banner_end'],
                'cutoff_date': tr['result']['cutoff_date'],
                'newly_available': tr['result']['newly_available'],
                'limited_dice_used': tr['result']['limited_dice_used'],
                'triple_keys_used': tr['result']['triple_keys_used'],
                'char_draw_gap': tr['result']['char_draw_gap'],
                'wep_draw_gap': tr['result']['wep_draw_gap'],
                'stone_needed': tr['result']['stone_needed'],
                'stone_remaining': tr['result']['stone_remaining'],
            } for tr in target_results] if target_results else None,
            'event': {
                'participated': True,
                'plans': event_results,
            } if args.event else None,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # ═══════════════════════════════
    #  表格输出
    # ═══════════════════════════════
    print(f"{'='*70}")
    print(f"  异环 {args.version} 资源拆分")
    parts = [f"查询日期: {args.date}", f"收入档位: {args.tier}",
             f"抽数模式: {args.draw_mode}"]
    if inventory:
        parts.append(f"已输库存")
    print("  |  ".join(parts))
    print(f"  已过 {obtained_days} 天 / 剩余 {remaining_days} 天")
    print(f"{'='*70}")

    # 警告
    if pending_warnings:
        print(f"\n⚠️  待确认项警告 ({len(pending_warnings)} 条估算数据):")
        for w in pending_warnings[:3]:
            print(f"  ⚠  {w[:70]}")
        if len(pending_warnings) > 3:
            print(f"  ... 及 {len(pending_warnings)-3} 条")

    # 每日
    print(f"\n📅 每日资源")
    print(f"{'来源':20s} {'类型':10s} {'日量':>8s} {'已得':>8s} {'剩余':>8s} {'合计':>8s}")
    print('-' * 66)
    for d in cd:
        print(f"{d['source']:20s} {d['type']:10s} {d['daily_qty']:>8,} {d['obtained_qty']:>8,} {d['remaining_qty']:>8,} {d['total_qty']:>8,}")
    if notes_extras:
        print(f"  → 备注附加: {dict(notes_extras)}")

    # 周期
    print(f"\n🔄 周期资源已开放 / 未开放: {len(per_ob)} / {len(per_future)} 条")

    # 活动
    print(f"\n🎯 活动已开放 / 未开放: {len([e for e in ev_ob if e['qty']>0])} / {len(ev_future)} 条")
    if ev_excluded:
        print(f"  ✗ 卡池截止排除了 {len(ev_excluded)} 条")

    # 探索奖励
    if expl_items:
        print(f"\n🗺️  探索奖励（额外预期）: {sum_by_type(expl_items)}")

    # 一次性
    print(f"\n📦 一次性已开放 / 未开放: {len(on_ob)} / {len(on_future)} 条")

    # ── 汇总 ──
    src = inventory_available if inventory_available else obtained
    label = "库存可用" if inventory_available else "已开放"

    all_types = sorted_types(merge_summaries(src, remaining, balance))

    print(f"\n{'='*70}")
    print(f"  📊 {'不含活动成本汇总' if args.event else '最终汇总'}")
    print(f"{'='*70}")
    balance_label = '不含活动' if args.event else '结余'
    print(f"{'资源':14s} {label:>12s} {'未开放':>12s} {'可动用':>12s} {balance_label:>12s}")
    print('-' * 66)
    for rt in all_types:
        src_v = src.get(rt, 0)
        rem = remaining.get(rt, 0)
        avl = (inventory_available if inventory_available else merge_summaries(obtained, remaining)).get(rt, 0)
        bal = balance.get(rt, 0)
        print(f"{rt:14s} {src_v:>12,} {rem:>12,} {avl:>12,} {bal:>12,}")

    final_ring = balance.get('环石', 0)
    if args.safe_margin is not None:
        safe_label = '不含活动安全垫' if args.event else '全部消费后安全垫'
        print(f"\n🛡️  {safe_label}: {'✅' if final_ring >= args.safe_margin else '❌'} "
              f"（环石 {final_ring:,} / 要求 {args.safe_margin:,}）")
    else:
        print("\n🛡️  安全垫：未设置")

    # 好感度
    if affection_items:
        print(f"\n💝 好感度（{profile_name}）: 每日 {daily_affection:,} 方斯 × 剩余 {remaining_days} 天 = {total_affection_cost:,} 方斯")
        print(f"   交易所: {exchange_cost:,} 方斯" if exchange_cost else "")

    # 多目标
    for tr in target_results:
        ti = tr['info']
        tr_res = tr['result']
        print(f"\n🎯 目标: {ti['character']} {ti['constellation']}+{ti['weapon']}")
        print(f"   角色: {ti['char_draws_needed']}抽 → 缺口{tr_res['char_draw_gap']}抽 → 环石{tr_res['char_draw_gap']*160}")
        print(f"   专武: {ti['wep_draws_needed']}抽 → 缺口{tr_res['wep_draw_gap']}抽 → 环石{tr_res['wep_draw_gap']*160}")
        print(f"   总环石消耗: {tr_res['stone_needed']:,}")
        target_safe = ('未设置' if tr_res['safe_margin_ok'] is None
                       else '✅' if tr_res['safe_margin_ok'] else '❌')
        print(f"   结余环石: {tr_res['stone_remaining']:,}（安全垫: {target_safe}）")

    # 活动方案
    if args.event:
        print("\n🎪 活动候选方案")
        print(f"{'方案':18s} {'活动环石':>12s} {'活动方斯':>14s} {'版本末环石':>12s} {'版本末方斯':>14s} {'安全垫':>8s}")
        print('-' * 88)
        for plan in event_results:
            plan_balance = plan['balance']
            plan_safe = ('—' if plan['safe_margin_ok'] is None
                         else '✅' if plan['safe_margin_ok'] else '❌')
            print(f"{plan['name']:18s} {plan['ring_cost']:>12,} {plan['fs_cost']:>14,} "
                  f"{plan_balance.get('环石', 0):>12,} {plan_balance.get('方斯', 0):>14,} "
                  f"{plan_safe:>8s}")

    # 与收益汇总验证
    if summary_rows:
        tier_map = {'零氪': 0, '小月卡党': 1, '大小月卡党': 2}
        idx = tier_map.get(args.tier, 2)
        if idx < len(summary_rows):
            exp = summary_rows[idx]
            if len(exp) >= 6:
                diffs = []
                for name, col in [('环石', 1), ('限定骰子', 2), ('常驻骰子', 3), ('三重钥匙', 4), ('方斯', 5)]:
                    ev = safe_int(exp[col])
                    av = total.get(name, 0)
                    if av != ev:
                        diffs.append(f"{name} {'+' if av>ev else ''}{av-ev}")
                if diffs:
                    print(f"\n📋 验证差异: {'; '.join(diffs)}（多为备注资源）")
                else:
                    print(f"\n📋 收益汇总验证 ✅")

    print(f"\n{'='*70}")


if __name__ == '__main__':
    main()
