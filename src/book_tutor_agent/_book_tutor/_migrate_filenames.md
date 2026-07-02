# 迁移老的 summaries/skeleton 文件名到纯 title 命名

## 背景

`book_summarize.py` 和 `book_skeleton.py` 改了命名规则：

- **旧**：文件名带序号前缀，如 `01-前言.md`、`02-第一章 介绍.md`
- **新**：文件名为纯章节标题，如 `前言.md`、`第一章 介绍.md`
- **新**：目录下额外有一份 `index.md`，由 Python 在跑完后按 seq 顺序生成，作为章节次序的唯一权威

`progress.json` 里 `file_name` 字段也要同步改成新文件名，否则下游脚本（book_teach / book_keypoints / book_audiobook / book_tts）按 progress 查表会找不到文件。

## 你的任务

接受一个参数 `<book_dir>`（一本书所在目录），把这本书目录下的 `summaries/` 和 `skeleton/` 两个子目录(如果存在的话)按新规则迁移：

### 对每个子目录 `<book_dir>/summaries/` 和 `<book_dir>/skeleton/`：

1. **跳过条件**：目录不存在就跳过，不要报错
2. **检查 progress.json**（仅 summaries/ 需要）：读 `<dir>/progress.json`
3. **按 seq 排序遍历**：对每个 seq 条目：
   - 取出 `title` 和旧 `file_name`
   - 用下面的 `sanitize` 规则从 `title` 算出新文件名 `<new_name>.md`
   - 如果旧文件 `<dir>/<old_file_name>` 存在且新文件名不同，rename
   - 把 progress.json 里这一条的 `file_name` 改成 `<new_name>.md`
4. **skeleton/ 单独处理**：skeleton 目录下没有自己的 progress.json，但文件名应该跟 summaries 一一对应。读 summaries/progress.json 拿到每个 seq 的 `title` 和旧 `file_name`（已是迁移前的旧名），把 skeleton/ 下对应的旧名文件 rename 成 `sanitize(title).md`
5. **生成 index.md**：在每个迁移后的目录下生成 `index.md`，按 seq 顺序列出，格式：

   ```markdown
   # 全书 {summaries|skeleton} 章节次序

   按原书顺序列出。决定章节先后、相邻关系时以此为准。

   1. [章节标题](章节标题.md)  [L{start_line}-L{end_line}]
   2. [...](....md)  [L...-L...]
   ```

   行号 `start_line` / `end_line`：seq=1 的 `start_line = 1`，其他 seq 的 `start_line = prev_seq.next_offset + 1`；`end_line = this_seq.next_offset`（如果 `next_offset == "END"`，用书文件总行数）。注意：progress.json 里的 `next_offset` 是 0-based 行号，输出 index 时转成 1-based。

### sanitize 规则（必须跟 filename_utils.py 完全一致）

输入字符串 `name`，按以下步骤处理，返回结果作为文件名 stem：

1. 把字符 `< > : " / \ | ? *` 和控制字符 `\x00-\x1f` 替换为 `_`
2. 连续空白折叠成单个空格
3. 去掉首尾空格
4. 去掉尾部的点号 `.` 和空格
5. 如果结果为空、或大写后（取第一个 `.` 前的部分）属于 Windows 保留名 `{CON, PRN, AUX, NUL, COM1-9, LPT1-9}`，报错让用户人工处理（不要静默 fallback）
6. 截到 120 字符

最终文件名是 `<sanitized>.md`。

### 安全性要求

- **干跑优先**：先把所有计划做的 rename 操作列成一张表打印出来给我看（旧名 -> 新名），等我确认 "go" 之后再实际执行
- **跳过同名**：如果旧名 == 新名（说明已经是迁移后的状态），跳过
- **冲突保护**：rename 之前检查目标文件是否已存在，如果存在且不是同一个文件（按 inode 或先比对内容），停下来报错让我人工处理
- **progress.json 备份**：改 progress.json 之前先复制一份到 `progress.json.bak`
- **顺序**：先 rename 所有文件，再写 progress.json，再写 index.md。任何一步失败就停，不要继续

## 参数

`<book_dir>`：（在这里填入要迁移的书目录绝对路径）

## 开工

按上面流程做。先打印干跑计划，等我说 "go"。
