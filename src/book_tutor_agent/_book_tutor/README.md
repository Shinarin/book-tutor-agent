# book-tutor

把一本书（Markdown 格式）喂给 AI，自动产出一整套学习材料：逐章总结、深入浅出的教案、核心知识点，乃至全书长文、有声书文稿和语音。

每个阶段都是一个独立的命令行脚本，可以单独运行，也可以串成流水线。底层基于 [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) 驱动 agent 逐章读原文、写产物，并可选切换到 Codex 作为后端。

## 示例产出

| 书名 | 产出 |
| --- | --- |
| 《国富论》 | [examples/the-wealth-of-nations](examples/the-wealth-of-nations/) |

## 工作流

```
原书.md
  ├─ book_summarize  逐章总结        → summaries/
  ├─ book_teach      生成教案        → <章节>.md
  ├─ book_keypoints  提炼核心知识点   → keypoints/ + 00-全书核心知识点.md
  ├─ book_skeleton   抽取目录骨架
  ├─ book_breakdown  拆解全书框架
  ├─ book_article    生成全书长文
  ├─ book_restyle    调整长文文风
  ├─ book_audiobook  改写为听感文稿
  └─ book_tts        合成 .mp3 语音
```

`summarize` 是其余多数阶段的基础：它会切分章节、生成 `summaries/progress.json`，后续脚本据此定位每章在原文中的行号范围。

## 安装

需要 Python 3.10+。

```bash
pip install -r requirements.txt
```

## 用法

只支持 `.md` 输入。先总结，再按需运行后续阶段：

```bash
# 1. 逐章总结
python book_summarize.py path/to/book.md --context-size 200k

# 2. 生成教案
python book_teach.py path/to/book.md

# 3. 提炼知识点
python book_keypoints.py path/to/book.md
```

常用参数（多数脚本通用）：

- `--seqs 1,3,5` —— 只处理指定章节
- `--start-seq N` —— 从第 N 章开始（断点续传）
- `--output-dir` / `--summary-dir` —— 自定义输入输出目录

各脚本的完整参数见文件顶部 docstring 或 `python <script>.py -h`。

## 参考来源

`prompts/book_breakdown.md` 基于 [lijigang/ljg-skills](https://github.com/lijigang/ljg-skills) 中的 skill 修改而来。
