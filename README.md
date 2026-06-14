针对educoder的信息提取脚本，能做到在打开课堂实验的前提下，通过运行该脚本可以获取全部章节的全部关卡的信息，同时可以生成相关的json文件，从而后面配合ai进行总结，如果可以，建议配合codex，claudecode等智能体使用，本文件目前只适用于头歌，且输入网址是进入课堂实验后的网址。

本目录中的 `collect_educoder_report.py` 用于从 Educoder 实训页面自动采集实验报告素材。脚本会打开浏览器，进入课堂实验页面，然后进入各个章节，点击“查看实战/进入实战”，逐关提取关卡标题、任务要求、代码区内容和页面截图，并保存为 JSON 文件，供后续生成实验
报告使用。

  ## 1. 环境准备

  脚本依赖 Playwright 和 Edge 浏览器。

  首次使用前安装依赖：

  ```cmd
  python -m pip install playwright
  python -m playwright install msedge

  ## 2. 基本运行方式

  python .\scripts\collect_educoder_report.py --url ""

  运行后脚本会打开 Edge。若 Educoder 要求登录，请先在浏览器中手动登录。确认目标页面已经显示章节列表或关卡页面后，回到终端按 Enter，脚本才会开始采集。

  ## 3. 常用参数

  ┌──────────────────┬─────────────────────────────────────────────────────────┐
  │ 参数             │ 说明                                                    │
  ├──────────────────┼─────────────────────────────────────────────────────────┤
  │ --url            │ 必填。Educoder 入口页、章节页或具体关卡页 URL。         │
  │ --chapter-range  │ 多章节页面中要采集的章节范围，例如 "1"、"2-5"。         │
  │ --single-chapter │ 将传入 URL 当作单章节或单关卡处理，不自动扫描章节列表。 │
  │ --chapter        │ 单章节模式下写入 JSON 的章节名。                        │
  │ --max-challenges │ 最多采集多少个关卡，默认 50。                           │
  │ --once           │ 只采集当前页面，不继续点击上一关。                      │
  │ --delay          │ 每次翻关后的等待时间，默认 1.5 秒。                     │
  │ --output         │ 输出 JSON 文件路径。                                    │
  │ --screenshot-dir │ 截图保存目录。                                          │
  │ --profile-dir    │ Edge 登录状态目录。                                     │
  │ --debug          │ 输出章节匹配、点击和页面跳转调试信息。                  │
  └──────────────────┴─────────────────────────────────────────────────────────┘

  ## 4. 采集指定章节(推荐)

  采集第 1 章：

  python .\scripts\collect_educoder_report.py ^
    --url "https://www.educoder.net/classrooms/zj254bqn/shixun_homework" ^
    --chapter-range "1" 

  采集第 2 到第 5 章：

  python .\scripts\collect_educoder_report.py ^
    --url "https://www.educoder.net/classrooms/zj254bqn/shixun_homework" ^
    --chapter-range "2-5"

  ## 5. 采集单个章节或单个关卡

  如果传入的是某个具体章节页或关卡页，可以使用 --single-chapter：

  python .\scripts\collect_educoder_report.py 
    --url "具体章节或关卡URL" 
    --single-chapter 
    --chapter "MySQL-数据库、表与完整性约束的定义(Create)"

  只采集当前页面，不继续翻关：

  python .\scripts\collect_educoder_report.py ^
    --url "具体关卡URL" ^
    --single-chapter ^
    --chapter "MySQL-数据库、表与完整性约束的定义(Create)" ^
    --once

  ## 6. 脚本执行流程

  脚本执行过程如下：

  1. 打开 Edge 浏览器。
  2. 用户手动登录 Educoder。
  3. 用户确认页面加载完成后，在终端按 Enter。
  4. 脚本自动识别章节列表。
  5. 根据 --chapter-range 筛选需要采集的章节。
  6. 点击章节卡片中的“开始学习/继续学习”。
  7. 进入章节详情页后，点击“查看实战/开始实战/进入实战/继续实战”。
  8. 进入具体关卡页面后，采集标题、任务要求、代码内容和截图。
  9. 当前 Educoder 页面进入实战后通常从最后一关开始，因此脚本会点击“上一关”继续采集。
  10. 本章采集结束后，脚本会将本章记录反转为正常顺序，使 index=1 对应第一关。

  ## 7. 输出文件

  默认输出 JSON：

  实验报告/educoder_collected_report_data.json

  默认截图目录：

  实验报告/screenshots/

  JSON 中每条记录格式如下：

  {
    "chapter": "1. MySQL-数据库、表与完整性约束的定义(Create)（6分）",
    "index": 1,
    "title": "关卡标题",
    "requirement": "任务要求文本",
    "code": "代码区内容",
    "screenshot": "截图路径"
  }

  ## 8. 注意事项

  - 多章节入口页筛选章节应使用 --chapter-range，不是 --chapter。
  - 如果 Educoder 打开新标签页，脚本会自动切换到新页面继续处理。
  - 如果采集结果中 code 为空，通常说明代码编辑器结构发生变化，需要调整脚本中的代码区选择器。
  - 如果章节点击或实战入口识别失败，建议加 --debug 查看脚本识别到的章节卡片和页面 URL。
  - 采集过程中不要手动关闭脚本打开的 Edge 页面，否则可能导致采集中断。
