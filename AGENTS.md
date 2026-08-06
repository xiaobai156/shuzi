# 杀数字_修复版项目专属规则

本项目继承上级 `..\AGENTS.md`，上级统一规则优先。本文件只规定“杀数字_修复版”的专属数据、入口、配置、缓存、输出、解析器和验收差异。

## 1. 项目范围与正式文件

项目根目录为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀数字_修复版`

正式文件：

- 配置：`targets.json`
- 禁抓/封存记录：`forbidden.json`
- 正式爬虫入口：`crawler.py`
- 正式重复检测：`check_duplicates.py`
- 失败站点独立验证：`validate_failed_sites.py`
- 正式缓存：`recent_10_cache.json`
- 单期入口：`run_crawler_prompt.py`
- 多期入口：`run_crawler_multi_prompt.py`
- 单期 BAT：`爬虫-每天杀数字.bat`
- 多期 BAT：`爬虫-每天杀数字-多期.bat`
- 判重 BAT：`爬虫-每天杀数字 - 检测重复.bat`
- 项目锁：`.crawler-and-duplicates.lock`

## 2. 杀数字数据契约

每条结果的号码必须是 `01` 至 `49`，对应整数范围 `1` 至 `49`；不得出现 `00`、大于 `49`、非数字或无法证明为目标号码的内容。

默认禁止同一期号码重复。只有配置明确设置 `allow_duplicate_numbers: true` 时才允许重复；该配置不能用于绕过栏目、期数、数量或候选冲突校验。

每个站点的号码数量由 `targets.json` 当前条目的 `count` 决定，必须整组严格等于该值。当前配置使用的有效数量包括 `5`、`6`、`7`、`8` 和 `10`；不能按站点名称、关键词或页面实际返回数量猜测数量，也不能跨组拼接凑足数量。

`targets.json` 中 `disabled: true` 的条目不属于活跃抓取目标，不得抓取、写入成功结果、更新缓存或参加判重。除非用户明确授权恢复，不得重新启用。

## 3. 配置身份与专属字段

新增或修复必须以 `targets.json` 中的真实 `url`、topic/文章 ID、`name`、栏目关键词、`anchor`、`stop_anchor`、`count` 和 `region` 共同确认身份。

普通目标应配置真实栏目关键词和正文 `anchor`；存在同页多栏目、文章列表、动态文章、历史周期、脚本数据或尾部栏目时，必须使用更窄边界或现有专属解析器。不能用目录名或宽泛“杀”“码”等关键词代替站点身份。

当前已使用的站点级专属解析器及硬约束包括：

- `top_article_history`：必须使用 `top` 或 `bottom`、正文锚点和文章标题锚点；方向决定文章历史候选窗口。
- `top_article_history_current_cycle`：对应“不可或缺”，使用 `top` 或 `bottom`、`准杀八码`、`count=8`、标题/正文边界和窗口 `5`；必须区分当前周期与旧周期。
- `huxin_xiaozhu_stable_10`：`top`、专属锚点、`count=10`。
- `fengwu_jiutian_bottom_10`：对应“凤舞九天”，固定 `bottom`、`count=10`、起止锚点和窗口 `3`。
- `identity_article_bottom_10`：动态文章身份解析，固定 `bottom`、`count=10`、`anchor == article_identity`、窗口 `10`，并要求配置 `api_url` 或 `/article/admin/` 文章入口。
- `shanshui_xiangfeng_top_10`：对应“山水相逢”，固定 `top`、`count=10`、专属标题/正文边界和窗口 `10`。
- `xinzhu_forum_stable_10`：对应“新竹论坛”，固定 `top`、专属锚点、`count=10`。
- `white_tiger_stable_10`：对应“白虎玄机”，固定 `top`、`count=10`，从当前期“稳杀10码”标题到“上一篇”之间取 `绝杀十码`，窗口固定为 `5`。
- `zuojianzifu_link_chain`：对应“作茧自缚”，固定 `top`、`count=10`、窗口 `3`；必须先在列表页按指定期数和“作茧自缚”唯一锁定详情链接，再进入同一期详情的“杀特十码”区块，禁止跨详情拼接。
- `zuibaxian_top7`：对应“醉八仙”；当前代码硬编码唯一 HTML 区块 ID `top_7`、关键词 `不买十码`、`count=10`、`top` 和窗口 `3`。虽然配置中存在 `section_id`，解析器并未读取它，不得把该字段描述为可配置边界。
- `macau_baoma`：对应“澳门报码”，使用站点专属报码结构。
- `babu_maoge_must_ten`：对应“八步毛哥必杀”，必须匹配 `必杀十码` 和 `top` 专属栏目。
- `majing_forum_bottom_10`：固定 `bottom`、`count=10`、专属锚点。
- `chunyin_qiushe_bottom_10`：固定 `bottom`、`count=10`、专属锚点。
- `shita_top_10`：对应“师太”，使用 `大家发(绝杀10码)` 至 `最早发表在` 的专属区块，固定 `top`、`count=10`、窗口 `10`。
- `qiancai_liangde_bottom_10`：对应“钱彩两得”，固定 `bottom`、`count=10`、`澳彩总站` 至 `提示!` 的边界、文章身份和 `gb18030` 编码。

专属解析失败时不得回退通用解析器，不得跨文档、跨栏目或跨周期补数。

## 4. region 与位置窗口

合法配置值为 `top`、`bottom`、`上`、`下`、`顶部`、`尾部`、`底部`。解析时统一归一化为 `top` 或 `bottom`。

`top` 和 `bottom` 只能作用于同一权威文档、同一目标栏目、同一明确区块内的高可信有效候选：

- `top` 只允许目标区块靠前的有效候选。
- `bottom` 只允许目标区块靠后的有效候选。
- 无效数量、重复号码、错误关键词或错误期数的候选不能占用方向窗口。
- 指定期数不在配置窗口内必须失败，即使页面其他位置存在该期也不能补抓。
- 同期多个不同合法号码组必须失败，不能用默认首条、末条或方向选择静默消除冲突。

通用候选窗口默认由 `crawler.py` 的 `CANDIDATE_REGION_WINDOW = 5` 控制。`targets.json` 中显式 `issue_position_window` 的站点必须严格使用配置值；当前配置存在 `3`、`4`、`5`、`10` 和 `30` 等站点专属窗口，不能运行时调整。

## 5. 单期抓取

正式单期入口为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀数字_修复版\爬虫-每天杀数字.bat`

该入口通过 `run_crawler_prompt.py` 调用：

`python crawler.py --workers 16 --issues 指定期数`

单期模式只能解析用户指定期数。页面只出现相邻期、旧期或其他栏目时必须失败。

单期成功文件中的每条记录当前由爬虫写为：

`号码整串 站点名称`

成功文件不带期数后缀；失败文件必须包含失败分类、站点名称、URL 和具体原因。手动指定期数不生成 `.bak` 文件。

单期实时抓取、解析和统一校验不得读取 `recent_10_cache.json` 参与补数、判期、判方向、解冲突或成功/失败裁决。成功/失败 TXT 和实时健康结果定稿后，才允许滚动更新缓存；成功结果写入 `records`，失败站点写入 `failures` 状态并清除同站同期旧成功值，不能用旧缓存冒充本期成功。缓存写入失败必须明确报告“缓存更新未完成”，但不得撤销已生成的 TXT 或改判本轮实时结果。

## 6. 多期抓取

正式多期入口为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀数字_修复版\爬虫-每天杀数字-多期.bat`

该入口调用 `run_crawler_multi_prompt.py`，接收任意多个期数，并逐期执行：

`python crawler.py --workers 16 --issues 单一期数 --no-cache-update`

每期必须独立完成期数、栏目、数量、方向、边界、唯一性和冲突校验。每期生成原有单期成功/失败文件，并生成：

`N期-M期-杀数字-多期汇总失败.txt`

汇总报告只列所有指定期数均失败的活跃目录，并逐期记录失败原因。任意一期成功的目录不列入汇总失败报告。

多期模式永远不得更新 `recent_10_cache.json`，不得以多期结果替代需要缓存验收的普通单期流程。

## 7. 正式缓存

正式缓存绝对路径为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀数字_修复版\recent_10_cache.json`

缓存版本为 `1`，`recent_count` 正式值为 `10`。成功记录字段为 `name`、`url`、`issue`、`numbers`；失败状态单独写入 `failures`，字段为 `name`、`url`、`issue`、`status`、`reason`，失败状态不得包含 `numbers`。

每个活跃站点必须独立按自身 `region` 维护近十期，不得以全站统一最新期替代。缓存记录必须保留号码原始顺序，不得排序、去重或混合上下方向。

缓存只供新增站点正式重复检测使用；空缓存、缺失期、失败状态、无效身份或不足连续近10期时，判重必须报告“检测未完成”，不得输出“不重复”。

`check_duplicates.py` 当前还对 5 个站点使用不足 10 期的内置覆盖：`葡京爆杀`、`首丘之思` 使用 8 期；`一语中的`、`摇钱树`、`澹台明镜` 使用 9 期。这些是现实现状，不符合统一的正式 10 期基准，不能据此放宽新增准入或输出正式“不重复”。

`forbidden.json` 当前版本为 `1`、`recent_count` 为 `10`、`records` 为空。源代码未读取该文件，因此它不能代替 `disabled`、缓存或正式拒收判断；其中记录为空时不得推断不存在封存站点。

## 8. 正式重复检测

现有判重 BAT 为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀数字_修复版\爬虫-每天杀数字 - 检测重复.bat`

现有 BAT 命令为：

`python check_duplicates.py --latest 最新期号 --recent 10 --workers 8 --write-cache --cache recent_10_cache.json`

该 BAT/命令强制使用一个全局 `--latest`，与每个站点按自身 `region` 确定最新期的统一规则冲突；目前禁止用它生成正式判重结论。`check_duplicates.py` 的按站点实时路径也尚未覆盖全部专属解析器。在全局期数、专属解析覆盖和有效 10 期缓存全部修复前，本项目只能报告“检测未完成”。

缺失专属覆盖的解析器为：`babu_maoge_must_ten`、`macau_baoma`、`top_article_history`、`top_article_history_current_cycle`、`white_tiger_stable_10`、`zuibaxian_top7`、`zuojianzifu_link_chain`。涉及这些站点时，当前实时判重不得输出“不重复”。

两个站点只有在相同期号、号码整串完全一致、原始顺序完全一致且期号连续时才构成重复：

- 连续 `1` 至 `2` 期：不重复。
- 连续 `3` 至 `5` 期：疑似重复，暂停并人工审核。
- 连续 `6` 期及以上：拒收，不得添加。
- 部分相同、顺序不同、长度不同或排序后相同均不算整串重复。
- 抓取失败、无共同期号、缓存无效或数据不足时，检测未完成，不能报告为不重复。

判重输出默认文件为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀数字_修复版\重复检测结果.txt`

## 9. 失败站点独立验证

正式验证入口为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀数字_修复版\validate_failed_sites.py`

独立验证只允许处理用户本次明确指定的站点和期数。不得自动读取失败 TXT 扩大范围。当前 CLI 会把 8 个静态 `FAILED_SITE_NAMES` 与所有 `--name` 参数合并，因此不能直接靠 `--name` 缩小范围；必须在隔离副本或临时导入式验证工具中把清单替换为本次站点，禁止为阶段一验证改写正式配置、缓存或 TXT。

当前人工清单位于 `validate_failed_sites.py` 的 `FAILED_SITE_NAMES`，实验配置只能放入 `EXPERIMENTAL_TARGET_OVERRIDES`，实验解析只能放入 `EXPERIMENTAL_RUNNERS`。隔离后的命令格式为 `python validate_failed_sites.py --issues <目标期,相邻期,不存在期> --name <站点> --workers <并发数>`；`--name` 可重复，但原实现只会追加，不能代替清空静态清单。

独立验证必须真实访问页面或接口，并检查目标期、相邻期、明确不存在期、栏目边界、号码数量、合法值、重复号码、方向窗口、同期候选冲突和动态文章身份。独立验证不得写正式成功/失败 TXT、`targets.json`、`crawler.py` 或 `recent_10_cache.json`。

实验通过不等于正式修复完成。正式迁移必须经过再次授权，并只迁移已验证的最小改动；迁移后必须执行正式单期流程、缓存检查和相关测试。

## 10. 正式输出路径

`crawler.py` 的生产输出目录为：

`C:\Users\Administrator\Desktop\每天工具\数据系列\大围杀号生肖数据统一归纳`

单期输出：

- `N期-杀数字-成功.txt`
- `N期-杀数字-失败.txt`
- `N期报告.txt`

多期汇总输出：

- `N期-M期-杀数字-多期汇总失败.txt`

输出只能写入通过完整校验的数据。失败报告必须区分网络、HTTP/SSL、锚点、关键词、指定期缺失、数量错误、方向越界、候选冲突、数据不足和其他解析失败。

## 11. 自适应与已知实现冲突

自适应结构恢复默认关闭。当前项目没有可作为正式业务裁决的自适应开关；不得新增自适应补数、自动猜方向或自动猜栏目逻辑。任何结构学习只能隔离验证，不能保存业务号码或期数。

源实现与上级规则存在以下已知冲突，执行时以上级规则和本文件为准：

- `run_crawler_prompt.py` 接受多个期数，但调用 `crawler.py` 时未自动传入 `--no-cache-update`；因此多期输入不得通过该单期入口执行，必须使用多期 BAT。
- `check_duplicates.py --latest` 会把一个全局最新期转换为统一期数范围；正式规则要求各站点按自身 `region` 独立识别最新期，不能以全局期号覆盖站点事实。
- `check_duplicates.py` 的按站点实时路径未为 `babu_maoge_must_ten`、`macau_baoma`、`top_article_history`、`top_article_history_current_cycle`、`white_tiger_stable_10`、`zuibaxian_top7`、`zuojianzifu_link_chain` 调用各自专属解析；这些站点当前不能完成正式判重。
- `recent_10_cache.json` 的 `failures` 只作状态记录，判重仅读取有效 `records`；失败状态不能作为号码候选。
- `crawler.py` 的缓存更新发生在 TXT 定稿之后；缓存更新异常只报告“缓存更新未完成”，不得回滚或改写本轮实时结果。
- `crawler.py` 的生产输出目录在项目根目录之外；不得把外部输出目录误认为项目内输出，也不得擅自改写为其他路径。
- `forbidden.json` 未被正式代码加载；其内容不能替代封存、禁抓或拒收状态。
- `validate_failed_sites.py` 的 8 站静态清单会与 `--name` 合并，且只验证显式传入 `--issues` 的期数，不会自动增加相邻期或不存在期。必须用隔离清单并显式传入全部负向期数。
- 正式 `crawler.py` 的风险预检可能在运行时自动补规则并写回 `targets.json`。未经本次明确授权，该自动写回不得执行，相关运行结果也不能作为正式成功。
- 用户页抓取会把同一用户的多个论坛帖子内容拼成一个解析文档，无法证明候选来自同一帖子区块时必须失败，不能把该合并结果作为正式成功。
- 动态文章专属 API 即使发生非 404 错误，代码仍会先尝试 landing 数据；API/landing 未命中后还会尝试原始页和浏览器。除统一规则允许的 API 404 或 HTTP 200 空壳外，这些兜底结果不得作为正式成功。
- `macau_baoma` 专属解析器只校验号码范围、数量和候选唯一性，没有检查同组号码重复；出现重复号码时必须失败，当前结果不能直接满足默认禁重契约。
