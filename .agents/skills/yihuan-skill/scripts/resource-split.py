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
    --profile nineo --inventory "环石:9069,限定骰子:16,三重钥匙:10,方斯:9519534" \\
    --target "伊洛伊:0+1" --include-affection --include-exchange

  # JSON 输出
  python3 resource-split.py --version v1.2 --tier 大小月卡党 --format json
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


# ── 常量 ──
CORE_TYPES = ['环石', '限定骰子', '三重钥匙', '常驻骰子', '方斯']
EXPECTED_PULLS = {'限定棋盘': 45, '弧盘研摹': 60}
SAFE_MARGIN_STONE = 7200


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
    if tier in ('小月卡党', '大小月卡党') and '小月卡' in item_tier:
        return True
    if tier == '大小月卡党' and ('仅大小月卡党' in item_tier or '大小月卡' in item_tier):
        return True
    if tier == '小月卡党' and item_tier == '小月卡党/大小月卡党':
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

def match_target(target_str: str, banners: list[dict]) -> dict | None:
    """
    解析目标并匹配卡池信息。
    target_str: '伊洛伊:0+1' → 角色0命+1专武
    返回 {character, char_banner_end, weapon_banner_end, char_pulls, wep_pulls}
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
        'char_pulls_needed': char_count * EXPECTED_PULLS['限定棋盘'],
        'wep_pulls_needed': wep_count * EXPECTED_PULLS['弧盘研摹'],
    }


def compute_consumption(target_info: dict, avail_ltd: int, avail_key: int,
                        avail_stone: int) -> dict:
    """计算目标消耗和结余"""
    char_gap = max(0, target_info['char_pulls_needed'] - avail_ltd)
    wep_gap = max(0, target_info['wep_pulls_needed'] - avail_key)
    stone_needed = char_gap * 160 + wep_gap * 160
    stone_left = avail_stone - stone_needed
    safe_ok = stone_left >= SAFE_MARGIN_STONE

    return {
        'char_pull_gap': char_gap,
        'wep_pull_gap': wep_gap,
        'stone_needed': stone_needed,
        'stone_remaining': max(0, stone_left),
        'safe_margin_ok': safe_ok,
    }


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
    parser.add_argument('--format', default='table', choices=['table', 'json'], help='输出格式')
    parser.add_argument('--packs', default='', help='礼包，如 "限定骰子:10,环石:300"')
    parser.add_argument('--costs', default='', help='方斯消耗，如 "方斯:2000000"')
    parser.add_argument('--pool-end', default='', help='卡池截止日期')
    parser.add_argument('--profile', default='', help='用户档案名')
    parser.add_argument('--include-affection', action='store_true', help='计入好感度成本')
    parser.add_argument('--include-exchange', action='store_true', help='计入交易所成本')
    parser.add_argument('--inventory', default='', help='当前库存，如 "环石:9069,限定骰子:16"')
    parser.add_argument('--target', default='', help='抽取目标，如 "伊洛伊:0+1"')
    args = parser.parse_args()

    query_date = parse_date(args.date)
    pool_end = parse_date(args.pool_end) if args.pool_end else None
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

    # ── 目标匹配 → 自动设 pool_end ──
    target_info = None
    if args.target:
        target_info = match_target(args.target, banners)
        if target_info:
            # 取角色和武器卡池中较早结束的作为 pool_end
            ends = [d for d in [target_info['char_banner_end'], target_info['wep_banner_end']] if d]
            if ends and not args.pool_end:
                pool_end = parse_date(min(ends))
                args.pool_end = min(ends)

    # ── 分类 ──
    cd = [classify_daily(it, query_date, pool_end) for it in fd]
    cp = [classify_dated(it, 'date', query_date, pool_end) for it in fp]
    ce = [classify_dated(it, 'start_date', query_date, pool_end) for it in event_items]
    co = [classify_dated(it, 'date', query_date, pool_end) for it in onetime_items]

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

    # ── 目标消耗计算（自动加入 cost_items）──
    target_result = None
    if target_info and inventory_available:
        avail_ltd = inventory_available.get('限定骰子', 0)
        avail_key = inventory_available.get('三重钥匙', 0)
        all_avail_stone = inventory_available.get('环石', 0)
        target_result = compute_consumption(target_info, avail_ltd, avail_key, all_avail_stone)
        if target_result['stone_needed'] > 0:
            cost_items['环石'] = cost_items.get('环石', 0) + target_result['stone_needed']
    elif target_info and not inventory:
        avail_ltd = obtained.get('限定骰子', 0) + remaining.get('限定骰子', 0)
        avail_key = obtained.get('三重钥匙', 0) + remaining.get('三重钥匙', 0)
        net_stone = total.get('环石', 0)
        target_result = compute_consumption(target_info, avail_ltd, avail_key, net_stone)
        if target_result['stone_needed'] > 0:
            cost_items['环石'] = cost_items.get('环石', 0) + target_result['stone_needed']

    # ── 最终结余（所有成本扣完后）──
    base = inventory_available if inventory_available else merge_summaries(obtained, remaining)
    balance = dict(base)
    for rt, qty in cost_items.items():
        balance[rt] = max(0, balance.get(rt, 0) - qty)

    # ═══════════════════════════════
    #  JSON 输出
    # ═══════════════════════════════
    if args.format == 'json':
        output = {
            'version': args.version,
            'query_date': args.date,
            'tier': args.tier,
            'pool_end': args.pool_end or None,
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
            'target': target_result,
            'target_info': {
                'character': target_info['character'],
                'constellation': target_info['constellation'],
                'weapon': target_info['weapon'],
                'char_pulls_needed': target_info['char_pulls_needed'],
                'wep_pulls_needed': target_info['wep_pulls_needed'],
                'char_banner_end': target_info['char_banner_end'],
                'wep_banner_end': target_info['wep_banner_end'],
            } if target_info else None,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # ═══════════════════════════════
    #  表格输出
    # ═══════════════════════════════
    print(f"{'='*70}")
    print(f"  异环 {args.version} 资源拆分")
    parts = [f"查询日期: {args.date}", f"收入档位: {args.tier}"]
    if pool_end:
        parts.append(f"卡池截止: {args.pool_end}")
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

    # ── 最终汇总 ──
    src = inventory_available if inventory_available else obtained
    label = "库存可用" if inventory_available else "已开放"

    all_types = sorted_types(merge_summaries(src, remaining, balance))

    print(f"\n{'='*70}")
    print(f"  📊 最终汇总")
    print(f"{'='*70}")
    print(f"{'资源':14s} {label:>12s} {'未开放':>12s} {'可动用':>12s} {'结余':>12s}")
    print('-' * 66)
    for rt in all_types:
        src_v = src.get(rt, 0)
        rem = remaining.get(rt, 0)
        avl = (inventory_available if inventory_available else merge_summaries(obtained, remaining)).get(rt, 0)
        bal = balance.get(rt, 0)
        print(f"{rt:14s} {src_v:>12,} {rem:>12,} {avl:>12,} {bal:>12,}")

    # 好感度
    if affection_items:
        print(f"\n💝 好感度（{profile_name}）: 每日 {daily_affection:,} 方斯 × 剩余 {remaining_days} 天 = {total_affection_cost:,} 方斯")
        print(f"   交易所: {exchange_cost:,} 方斯" if exchange_cost else "")

    # 目标
    if target_result:
        ti = target_info
        print(f"\n🎯 目标: {ti['character']} {ti['constellation']}+{ti['weapon']}")
        print(f"   角色: {ti['char_pulls_needed']}抽 → 可用道具={inventory.get('限定骰子', obtained.get('限定骰子', 0))+remaining.get('限定骰子',0)} → 缺口{target_result['char_pull_gap']}抽 → 环石{target_result['char_pull_gap']*160}")
        print(f"   专武: {ti['wep_pulls_needed']}抽 → 可用道具={inventory.get('三重钥匙', obtained.get('三重钥匙', 0))+remaining.get('三重钥匙',0)} → 缺口{target_result['wep_pull_gap']}抽 → 环石{target_result['wep_pull_gap']*160}")
        print(f"   总环石消耗: {target_result['stone_needed']:,}")
        print(f"   结余环石: {target_result['stone_remaining']:,}  (安全垫 7,200: {'✅' if target_result['safe_margin_ok'] else '❌'})")

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
