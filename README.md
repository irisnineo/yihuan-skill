# yihuan-skill

一个用于生成《异环》资源分析报告的 skill。

它会根据玩家库存、版本收入、角色武器目标、都市目标和活动档位，计算版本可用资源、目标消耗、联动支付路线和版本末余量，并默认生成 HTML 报告。

## 使用

作为 skill 调用：

```text
[$yihuan-skill](.agents/skills/yihuan-skill/SKILL.md)
我当前资源：方斯 435w，环石 16598，限定骰子 10，钥匙 46。
版本：1.1。
目标：角色全拿，武器 2 把，联动满。
```

报告默认生成到：

```text
reports/name-date.html
```

## 内容

- `.agents/skills/yihuan-skill/SKILL.md`：规划流程。
- `references/fixed-rules.md`：通用规则。
- `references/version-1.1.md`：1.1 版本数据。
- `profiles/`：玩家档案。
- `templates/resource-report.html`：HTML 报告模板。
- `reports/`：生成的报告。
- `sources/`：原始资料、截图和参考文档。

## 说明

这是非官方工具，数据和活动规则需要随游戏实装更新。

## 赞赏

万一呢？

<img src="sponsor.jpg" alt="九原的赞赏码" width="320">
