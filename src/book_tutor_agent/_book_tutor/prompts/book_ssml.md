读 `{audiobook_path}`，给文中**真正会被 TTS 读错**的多音字、数字、英文缩写、易粘连分词加上 SSML 标注，**直接覆盖原文件**。

## 背景

下游用 Azure 中文 TTS (zh-CN voice) 朗读这份稿件。Azure 中文 TTS 在多音字判别和分词上有已知的弱点。你的任务是**最小化干预**：只在容易读错的地方加标签，其他地方原文照搬，**一个字都不要改**。

## 允许使用的 SSML 标签

只能用下面这几种，别的标签不要加。

### `<say-as>` —— 数字 / 日期 / 字符的读法

格式：`<say-as interpret-as="TYPE">内容</say-as>`

常用 type：
- `cardinal` 基数 (一二三)：`<say-as interpret-as="cardinal">2024</say-as>` → "二零二四" / "两千零二十四"
- `date` 日期：`<say-as interpret-as="date" format="ymd">2024-01-15</say-as>`
- `characters` 逐字符念：`<say-as interpret-as="characters">USB</say-as>` → "U-S-B"
- `telephone` 电话号

只在数字含义模糊时用。比如 "GPT-4" 不需要标 (TTS 能读对)，但 "1949 年" 要标成 date 或在前后判断 cardinal。

### `<break>` —— 插入停顿，消除分词歧义

格式：`<break time="200ms"/>` 或 `<break strength="weak"/>`

- 易粘连的边界插一个短停顿，让 TTS 不会把两边粘成一个错词
- time 推荐 100ms ~ 300ms。**别用太长**，否则朗读不自然
- 典型场景："没<break time="150ms"/>收入" 防止 TTS 读成"没收-入"

## 标注原则

### 只标真正会错的

不要把所有多音字都标。判断标准：
- 字本身有 2+ 个常用读音
- **当前上下文**和 TTS 默认倾向不一致
- 例如："行业" 不用标 (TTS 默认就读 hang2)；但 "一行代码" 里的"行"容易被读成 xing2，要标

如果你不确定 TTS 默认读什么，倾向于**不标**，由 TTS 自己处理 —— 过度标注比漏标风险更高 (标错读音 vs 偶尔读错一个字)。

### 容易粘连的分词边界

- "没收入"：人想说 "没/收入"，TTS 容易读成 "没收/入"。要么 phoneme 标"没"为 mei2，要么 break 隔开
- "一行代码"：TTS 容易把 "一行" 当成"行走"。phoneme 标"行"为 hang2
- "下面" / "下午" / "下载"：通常没问题，但有时 break 可以救
- 数字+单位粘连：偶尔需要

优先用 phoneme (更精确)，break 是兜底。

### 数字

- 年份：`<say-as interpret-as="date" format="y">1949</say-as>`
- 大数字、序号：通常 TTS 处理 OK，不用标
- 版本号 "GPT-4"、"Python 3.11"：不用标

## 输出格式

- 输出仍然是 .md 文件
- **原文内容一字不改**，只在需要的地方**插入** SSML 标签
- 标签必须自闭合或正确闭合，不要嵌套 phoneme/say-as
- 标签里的属性值必须用双引号
- 不要在标签里加空格、换行
- 不要包 `<speak>` 或 `<voice>` 外层标签 (下游会加)
- 不要输出任何"标注说明""修订列表"之类的元信息

## 自检

写完后扫一遍你加的每个标签：
- ph 拼音是否带数字声调，是否对应正确的字
- say-as 的 interpret-as 是否合法
- sub 的 alias 读出来是否真的更准
- break 的 time 是否在 100-300ms

如果一段话从头到尾你都没动过，那就保持原样。**宁缺勿滥**。
