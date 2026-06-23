# Mobile Doubao Runner And Skill Design

## Goal

Build a mobile Doubao automation line that mirrors the desktop Doubao CDP skill pattern, but uses Python plus `adb.exe` to control an Android emulator.

The final user workflow is:

```text
User gives direct questions or a question file
-> OpenCode skill creates task JSON
-> skill runs python runner.py --task <task.json>
-> runner opens Doubao on Android
-> runner asks every question in a fresh chat
-> runner captures answer text
-> runner extracts real source URLs through share-copy-paste-read
-> runner writes structured result JSON
-> skill reads result JSON and reports answers plus real URLs
```

This design does not include Feishu/Lark integration.

## Hard Decisions

### Runtime

Use Python as the mobile runner runtime.

```text
Python -> adb.exe -> Android emulator -> Doubao app
```

Do not use Node.js for the mobile runner. Node.js remains only the desktop CDP runner runtime.

### Automation Layer

Use direct ADB commands as the first implementation layer.

The current validated probe already works with:

```text
/mnt/d/CursorProjects/mobile-auto-doubao/scripts/probe_source_urls_by_share_copy_adb.py
```

`uiautomator2` is not required for the first formal runner because current ADB can already:

- tap controls
- press key events
- dump UI XML
- capture screenshots
- paste clipboard content into focused input
- read the pasted URL from UI XML

### Source URL Extraction

Do not try to read source URLs from the source list UI XML. It only exposes source titles.

Use the verified route:

```text
source list item
-> open source page
-> tap share button
-> tap 复制链接
-> return to Doubao chat
-> focus input box
-> paste clipboard without sending
-> read URL from input box UI XML
-> clear input box
```

Direct clipboard reading is not promised. On the current emulator, `dumpsys clipboard` and `service call clipboard` do not provide the copied text to ADB shell.

### Fresh Chat Rule

Every question must run in a fresh Doubao conversation.

If task JSON contains multiple questions, the runner still isolates each question. Previous answers must not influence later questions.

### Skill Responsibility

The skill does not implement mobile UI automation. It only:

- receives direct questions or a file path
- converts them into task JSON
- runs `python runner.py --task <task.json>`
- reads the configured result JSON
- reports concise results

## Existing Evidence

### Mobile Probe

File:

```text
/mnt/d/CursorProjects/mobile-auto-doubao/scripts/probe_source_urls_by_share_copy_adb.py
```

Important constants already validated:

```text
REFERENCE_CONTENT_ID = com.larus.nova:id/tv_reference_content
INPUT_ID = com.larus.nova:id/input_text
SHARE_BUTTON_ID = com.larus.nova:id/btn_share
```

Verified clean result:

```text
/mnt/d/CursorProjects/mobile-auto-doubao/outputs/source-share-copy-clean/source-share-copy-20260603-150950.json
```

That run found 17 visible source titles, attempted the first 3, and extracted 3 real links.

### Desktop Pattern To Mirror

Task JSON pattern:

```text
/mnt/d/CursorProjects/doubao-cdp-test/doubao-task.example.json
```

Result JSON pattern:

```text
/mnt/d/CursorProjects/doubao-cdp-test/results/doubao-thinking-mode-test.json
```

The mobile runner should keep the same broad shape:

```text
taskName
mode
startedAt
finishedAt
totalSessions
totalQuestions
sessions[].sessionName
sessions[].results[]
results[].question
results[].answer
results[].sources[]
results[].status
results[].debug
```

## Project Layout

Target structure:

```text
mobile-auto-doubao/
  runner.py
  mobile_auto_doubao/
    __init__.py
    adb_client.py
    task_schema.py
    ui_xml.py
    doubao_app.py
    answer_capture.py
    source_links.py
    result_writer.py
  tasks/
    example.json
  results/
  scripts/
    probe_current_ui_urls_adb.py
    probe_source_urls_by_click_adb.py
    probe_source_urls_by_share_copy_adb.py
  docs/
    mobile-runner-skill-design.md
```

Future skill structure:

```text
~/.config/opencode/skills/doubao-mobile-automation/
  SKILL.md
  references/mobile-auto-doubao/
```

The skill can either reference the project path directly during local development or bundle a copy under `references/` later.

## Task JSON Contract

The runner accepts:

```json
{
  "taskName": "doubao-mobile-run",
  "mode": "separate",
  "device": {
    "adb": "/mnt/c/Users/Administrator/AppData/Local/Android/Sdk/platform-tools/adb.exe",
    "serial": "emulator-5556"
  },
  "sessions": [
    {
      "sessionName": "q1",
      "newChat": true,
      "questions": ["问题 1"]
    },
    {
      "sessionName": "q2",
      "newChat": true,
      "questions": ["问题 2"]
    }
  ],
  "options": {
    "sourceLimit": 5,
    "waitStableSeconds": 5,
    "intervalMs": 3000,
    "timeoutMs": 180000
  },
  "output": "results/doubao-mobile-run.json"
}
```

Rules:

- `sessions` must be non-empty.
- every session must contain non-empty `questions`.
- question text must be preserved exactly.
- `newChat` defaults to `true`.
- mobile runner forces fresh chat even when multiple questions exist.
- `sourceLimit` limits how many visible sources are opened per answer.
- `output` defaults to `results/<taskName>-<timestamp>.json` if omitted.

## Result JSON Contract

Result JSON follows the desktop session/result shape:

```json
{
  "taskName": "doubao-mobile-run",
  "mode": "separate",
  "startedAt": "2026-06-03T00:00:00.000Z",
  "finishedAt": "2026-06-03T00:01:00.000Z",
  "totalSessions": 2,
  "totalQuestions": 2,
  "sessions": [
    {
      "sessionName": "q1",
      "results": [
        {
          "index": 1,
          "question": "问题 1",
          "askedAt": "2026-06-03T00:00:10.000Z",
          "finishedAt": "2026-06-03T00:00:45.000Z",
          "answer": "豆包回答正文",
          "sources": [
            {
              "title": "来源标题",
              "url": "https://www.iesdouyin.com/share/video/...",
              "method": "share_copy_paste_read",
              "status": "success"
            }
          ],
          "status": "success",
          "error": null,
          "debug": {
            "newChatCreated": true,
            "sourceCount": 1,
            "sourceLimit": 5,
            "screenshot": "results/snapshots/...png",
            "uiXml": "results/snapshots/...xml"
          }
        }
      ]
    }
  ]
}
```

Status values:

```text
success       answer captured; source extraction completed as requested
partial       answer captured; some source URLs unsupported or missing
blocked       login, captcha, app block, or manual action required
failed        device, app, selector, or timeout failure
unsupported   requested source operation is unavailable on the current page
```

## Runner Modules

### `adb_client.py`

Responsibilities:

- resolve default `adb.exe`
- detect connected device
- run ADB commands
- tap coordinates
- press key events
- dump UI XML
- capture screenshot

Do not implement business flow here.

### `task_schema.py`

Responsibilities:

- load task JSON
- validate required fields
- normalize defaults
- generate dry-run summary

### `ui_xml.py`

Responsibilities:

- parse Android UI XML
- parse bounds
- find nodes by resource id
- find nodes by text
- extract URL text
- list visible source items

### `doubao_app.py`

Responsibilities:

- ensure Doubao is foreground
- detect blocked/login states
- create fresh chat
- focus input
- type question
- send question
- navigate back safely

Login and captcha are never automated.

### `answer_capture.py`

Responsibilities:

- wait for response generation to finish
- use UI XML samples to detect answer stabilization
- extract latest answer text for the current question

### `source_links.py`

Responsibilities:

- find visible source entries
- open each source
- tap share
- tap `复制链接`
- return to chat
- paste copied value into input without sending
- read URL from input UI XML
- clear input

This module should reuse the verified logic from `probe_source_urls_by_share_copy_adb.py`.

### `result_writer.py`

Responsibilities:

- create output directory
- write aggregate result JSON after each question
- keep partial results when later questions fail
- store artifact paths in debug fields

## Runner Flow

### Dry Run

Command:

```bash
python runner.py --task tasks/example.json --dry-run
```

Behavior:

- validate task JSON
- print summary
- do not touch the emulator UI
- do not send questions

### Live Run

Command:

```bash
python runner.py --task tasks/example.json
```

For each question:

```text
resolve device
ensure Doubao foreground
create fresh chat
input question verbatim
send question
wait for answer stable
capture answer
open source panel/list
extract source titles
for each source up to sourceLimit:
  open source
  share
  copy link
  return
  paste-read URL
  clear input
write result JSON
wait intervalMs
```

## Skill Design

Skill name:

```text
doubao-mobile-automation
```

Skill responsibilities:

```text
1. Accept questions directly or from a file.
2. Convert questions into task JSON.
3. Force every question into its own fresh-chat session.
4. Run python runner.py --task <task.json>.
5. Read output JSON.
6. Report answer and real source URLs.
```

Skill input modes:

```text
Direct questions:
- question 1
- question 2

File input:
path/to/questions.txt
path/to/questions.json
```

Text file convention:

```text
one non-empty line = one question
```

JSON file convention can either be:

```json
{"questions": ["问题 1", "问题 2"]}
```

or a full runner task JSON.

Skill output summary:

```text
已跑完。
- 输出：results/xxx.json
- 成功：N/M
- 每题新会话：是
- 真实来源 URL：Q1 x 个，Q2 y 个
- 阻塞：无 / login_required / selector_missing / timeout
```

## Acceptance Criteria

Design is implementation-ready when these are true:

- The runner contract supports direct and file-generated task JSON.
- The runner is Python plus ADB only.
- Every question runs in a fresh chat.
- Question text is preserved exactly.
- The output schema mirrors desktop `sessions[].results[]` where practical.
- Source URLs use `share_copy_paste_read`.
- Direct clipboard read is not required or promised.
- Login/captcha are detected as `blocked`.
- Feishu/Lark is absent.
- Skill wrapper only creates task JSON, invokes runner, and reads results.

## Test Plan

### Unit Tests

- task JSON validation
- default normalization
- bounds parsing
- UI XML node lookup
- URL extraction
- result aggregation

### Dry Run QA

```bash
python runner.py --task tasks/example.json --dry-run
```

Expected:

```text
valid task summary
no emulator UI operation
```

### Mobile Smoke QA

```bash
python runner.py --task tasks/example.json
```

Expected:

```text
one fresh chat per question
answer captured
source URLs captured when share-copy is available
result JSON written after each question
```

### Skill QA

Direct questions:

```text
用手机端豆包问：问题 1；问题 2
```

File questions:

```text
读取 questions.txt，用手机端豆包逐个新会话提问
```

Expected:

```text
skill creates task JSON
skill runs Python runner
skill reads result JSON
skill reports answers and real URLs
```

## Out Of Scope

- Feishu/Lark table reading or writing
- desktop CDP execution
- JavaScript mobile runner
- `uiautomator2` as a required dependency
- login automation
- captcha automation
- direct clipboard read guarantee
- source URL fabrication from titles

## Implementation Order

1. Add schema fixtures and dry-run behavior.
2. Extract ADB and UI XML helpers from the verified probe.
3. Implement one-question fresh-chat runner.
4. Implement answer stabilization and extraction.
5. Integrate share-copy-paste-read source extraction.
6. Add batch task execution and incremental result writing.
7. Add OpenCode skill wrapper.
8. Run end-to-end QA and review.
