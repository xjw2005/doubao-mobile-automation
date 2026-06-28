# Doubao Mobile Automation Migration README

This reference explains how to move the entire Doubao mobile automation integration to another agent or computer and run it with minimal context loss.

The integration has **two cooperating modules**:

1. **跑移动端 (Doubao Mobile Runner)** — a Python package (`mobile_auto_doubao/`) plus a top-level `runner.py`. It drives the Doubao Android app (`com.larus.nova`) via ADB: opens chats, types questions with ADB Keyboard, waits for answers, captures expert/thinking content and the answer share link, and (optionally) writes results back to Feishu.
2. **豆包来源提取 (Doubao Share-Page Extractor)** — a Node.js script (`doubao-source-extractor/`) that opens a Doubao answer share page through Chrome DevTools Protocol (CDP), separates thinking from the final answer, and extracts real source URLs.

The Python runner is the orchestrator. With `--extract-sources --link-only`, it captures the answer share link on the phone, hands it to the JS extractor, and fills answer / thinking / sources from the share-page snapshot.

## What This Skill Contains

```text
references/
  README.md                       # this migration guide
  mobile-auto-doubao/             # runnable Doubao project snapshot (copy this to a workspace)
    runner.py                     # entry point: python runner.py
    requirements.txt
    mobile_auto_doubao/           # Python package: ADB driver, capture, Feishu writeback
    doubao-source-extractor/      # share-page extractor: run.js, extract-sources.js, write-feishu.js
    configs/                      # feishu-doubao-example.json (externalized table IDs)
  scripts/                        # Doubao probe / debug scripts (operational playbook)
  tasks/                          # Doubao task JSON examples
  tools/                          # keyboardservice-debug.apk (ADB Keyboard)
  docs/                           # ADR 0002 + design / sharing notes
```

Everything under `references/` is Doubao-only. The runnable project snapshot is `references/mobile-auto-doubao/`; debug probes stay under `references/scripts/` because they are part of the operational playbook for future agents.

## Restore On A New Computer

1. Copy the skill folder to the new agent's skill directory, or keep it in the project and explicitly tell the agent to use it.
2. Create a workspace folder, for example `D:\CursorProjects\mobile-auto-doubao`.
3. Copy everything from `references/mobile-auto-doubao/` into that workspace.
4. Create runtime folders if they are missing:

```powershell
New-Item -ItemType Directory -Force -Path tasks, results, outputs
```

5. Copy task examples:

```powershell
Copy-Item <skill>\references\tasks\*.json .\tasks\
```

6. Optional: copy debug scripts into the workspace:

```powershell
New-Item -ItemType Directory -Force -Path scripts
Copy-Item <skill>\references\scripts\*.py .\scripts\
```

7. Install Python dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The current project keeps `uiautomator2` and `uiautodev` for compatibility with existing probes and local development.
The main runner uses direct ADB commands.

## Android And ADB Setup

Install Android platform tools and locate `adb`. Common paths:

- Windows: `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`
- macOS: `~/Library/Android/sdk/platform-tools/adb`
- Linux: `~/Android/Sdk/platform-tools/adb`

Check devices:

```powershell
adb devices
```

Expected output contains at least one `device` row, for example:

```text
emulator-5556    device
100.76.50.7:6666 device
```

Use the exact serial in task JSON or pass it with `--serial` / `--device`.

For network ADB devices, connect first:

```powershell
adb connect <host>:<port>
adb devices
```

If the device shows `unauthorized`, unlock the device and accept the USB/network debugging prompt.

## ADB Keyboard Setup

Chinese question input depends on ADB Keyboard:

```text
com.android.adbkeyboard/.AdbIME
```

Install the bundled APK when needed:

```powershell
adb -s <serial> install -r <skill>\references\tools\keyboardservice-debug.apk
adb -s <serial> shell ime list -s
adb -s <serial> shell ime set com.android.adbkeyboard/.AdbIME
```

If live runs fail with `adb_keyboard_not_installed`, install the APK and set the IME again.

Some emulator ROMs block `ime enable` and `ime set` from shell. If that happens:

1. Open the Android input-method settings screen.
2. Enable `ADB Keyboard` there.
3. Use root on the device if necessary.
4. Verify with `adb shell dumpsys input_method`.
5. If the built-in IME keeps being restored, temporarily disable it during the run.

Some ROMs also block `adb shell input swipe` with `INJECT_EVENTS` errors.
The runner falls back to `PAGE_UP` / `PAGE_DOWN` key events for the answer/source scrolling paths.

## Doubao App Preconditions

- The package name is `com.larus.nova`.
- The app must already be installed and logged in.
- Login, captcha, risk prompts, and account blocks are not automated.
- Keep the device awake and unlocked during live runs.
- Avoid manually touching the device during a run.

## Task JSON Mode

Run validation only:

```powershell
python runner.py --task tasks\example.json --dry-run
```

Run live:

```powershell
python runner.py --task tasks\example.json
```

Override device settings without editing JSON:

```powershell
python runner.py --task tasks\<task>.json --adb "<adb-path>" --serial <serial>
```

If you want to keep artifacts, add `--debug`.

For parallel execution, always add a unique `--output` per process:

```powershell
python runner.py --task tasks\<task>.json --serial emulator-5556 --output results\run-emulator-5556.json
python runner.py --task tasks\<task>.json --serial 100.76.50.7:6666 --output results\run-100.76.50.7-6666.json
```

Minimal task shape:

```json
{
  "taskName": "doubao-mobile-run",
  "mode": "separate",
  "thinking": true,
  "device": {
    "adb": "C:\\Users\\Administrator\\AppData\\Local\\Android\\Sdk\\platform-tools\\adb.exe",
    "serial": "emulator-5556"
  },
  "sessions": [
    {
      "sessionName": "q1",
      "newChat": true,
      "thinking": true,
      "questions": ["问题 1"]
    }
  ],
  "options": {
    "sourceLimit": "all",
    "waitStableSeconds": 5,
    "intervalMs": 0,
    "timeoutMs": 240000,
    "debug": {
      "enabled": false,
      "screenshots": false,
      "currentFocus": false
    }
  },
  "output": "results/doubao-mobile-run.json"
}
```

Useful task options:

- `sourceLimit`: number of sources to open, or `"all"`.
- `waitStableSeconds`: answer stability wait.
- `intervalMs`: delay between questions in the same task.
- `timeoutMs`: answer wait timeout.
- `debug.enabled`: when `true`, keep XML / screenshot / current-focus artifacts.
- `debug.screenshots`: set `true` when diagnosing UI state; set `false` for faster normal runs.
- `debug.currentFocus`: set `true` when diagnosing focus/navigation; set `false` for faster normal runs.
- `expertAnswerTopScrolls`: extra upward scrolls before the expert-answer collector runs.
- `expertAnswerMaxScrolls`: expert-answer scroll budget.
- `answerShareMaxScrolls`: answer share button search budget.
- `answerShareWaitSeconds`: wait after tapping the answer share button.
- `sourcePageWaitSeconds`: wait after opening a source page.
- `sourceShareWaitSeconds`: wait after tapping a source page share button.

## Feishu Base Mode

Preview selected rows by row range:

```powershell
python runner.py --base-url "<base-url>" --base-start 1 --base-end 10 --dry-run
```

Preview selected rows with a JSON config file:

```powershell
python runner.py --feishu-config configs\feishu-doubao-example.json --base-start 1 --base-end 10 --dry-run
```

Run and write back:

```powershell
python runner.py --base-url "<base-url>" --base-start 1 --base-end 10 --writeback --mark-collected --collect-account 18870501682
```

Run and write back with the config file and Doubao share-page extraction:

```powershell
python runner.py --feishu-config configs\feishu-doubao-example.json --base-start 1 --base-end 10 --writeback --mark-collected --extract-sources --link-only --cdp-url http://127.0.0.1:9222
```

`--base-start` and `--base-end` are 1-based and inclusive.
The runner reads that row range first, then only keeps rows whose `是否本次采集` value is `是`.

`--base-limit` is still accepted as a fallback when `--base-start` and `--base-end` are omitted, but the row-range flags are the current mode.

Input fields expected by the project:

```text
问题
关联自然问句
是否开启深度思考
是否本次采集
```

When field names stay the same and only table IDs change, prefer the JSON config mode.
The config file can set the input question table and the two writeback table IDs:

```json
{
  "input": {
    "baseUrl": "https://example.feishu.cn/base/<baseToken>?table=<questionTableId>&view=<viewId>"
  },
  "writeback": {
    "answerTableId": "<answerTableId>",
    "sourceTableId": "<sourceTableId>"
  },
  "collectAccount": "18870501682"
}
```

`input.baseUrl` may be replaced by `input.baseToken`, `input.tableId`, and optional `input.viewId`.
`writeback.answerTableId` points to the `AI回答采集`-style answer table.
`writeback.sourceTableId` points to the `引用源明细` / AI source table.
The answer and source field lists are still fixed in code and expected to match the existing schema.

If `lark-cli` is not on `PATH`, pass `--lark-cli <path>`.
On Windows, use the actual `.cmd` or `.exe` path instead of the PowerShell shim name, because Python `subprocess` may not resolve the shim the same way the shell does.

Example:

```powershell
python runner.py --base-url "<base-url>" --base-start 1 --base-end 10 --writeback --mark-collected --lark-cli "<lark-cli-path>"
```

`--collect-account` overrides the `采集账号` field in the answer writeback table.
This is the preferred way to distinguish multiple operators or devices.

`--force-quick` forces Feishu Base sessions to use quick mode even when the row says deep thinking.

Feishu credentials and `lark-cli` setup are environment-specific and are not bundled in this skill.

## Doubao Share-Page Extraction

The current recommended Doubao mobile collection route is:

```powershell
python runner.py --feishu-config configs\feishu-doubao-example.json --base-limit 10 --writeback --mark-collected --extract-sources --link-only --cdp-url http://127.0.0.1:9222
```

`--extract-sources` invokes `doubao-source-extractor/run.js` after the mobile app copies the Doubao answer share link.
`--link-only` skips mobile expert/source scraping and replaces answer, thinking content, and sources with the share-page snapshot.
The extractor parses `https://www.doubao.com/thread/...` pages, reads the latest assistant message, separates thinking text from final answer, and writes source rows to the configured source table.

Safety guard:

```text
No verified share URL or no verified share-page answer => no answer writeback and no mark-collected update.
```

This prevents stale visible answers from being written when the mobile send/share flow fails.

## Output Contract

The runner writes aggregate JSON to the task `output` path and updates it incrementally after each question.
It also writes a companion `*-debug.json` file and includes its path in `debugResult`.

Key fields:

```text
sessions[].results[].question
sessions[].results[].answer
sessions[].results[].thinkingContent
sessions[].results[].sources[]
sessions[].results[].answerShareUrl
sessions[].results[].status
sessions[].results[].error
sessions[].results[].debug
```

Status meanings:

```text
success  answer, expert answer extraction, source links, and answer share link succeeded
partial  answer exists, but some source/share/expert extraction step failed or was incomplete
blocked  login, captcha, account, or manual app state blocks execution
failed   device, selector, send, answer, or timeout failure
unsupported  source/share operation is unavailable in the current UI state
```

When reporting results, include the output path, success count, partial/failed/blocked count, answer share URL count, and real source URL count.

## Source URL Extraction Model

Do not infer URLs from visible source titles.
The reliable route is:

```text
expand reference panel
tap source item
tap share
tap 复制链接
return to chat
read clipboard or paste into input
extract http(s) URL
clear input
```

The runner contains optimized logic for this in `mobile_auto_doubao/source_links.py`.
Older probes in `references/scripts/` preserve the debugging history and are useful when Doubao UI changes.

## Quick Validation Checklist

```powershell
python runner.py --task tasks\example.json --dry-run
adb devices
adb -s <serial> shell ime list -s
adb -s <serial> shell monkey -p com.larus.nova -c android.intent.category.LAUNCHER 1
```

Only run live collection after all checks pass and Doubao is logged in.

## Troubleshooting

`No connected adb device found.`
: Run `adb devices`, reconnect the emulator/device, or pass `--serial`.

`Multiple adb devices are connected.`
: Pass an explicit serial with `--serial` or set `device.serial` in the task JSON.

`unauthorized`
: Unlock the phone/emulator and accept the debugging prompt.

`adb_keyboard_not_installed`
: Install `references/tools/keyboardservice-debug.apk` and set `com.android.adbkeyboard/.AdbIME`.

`new_chat_failed`
: The sidebar/create-chat selector changed or Doubao is on an unexpected screen. Use `scripts/probe_ui_layout.py`.

`share_button_not_found` or `copy_link_not_found`
: The source page/share sheet changed. Use `scripts/test_source_links_snake.py` and compare `mobile_auto_doubao/constants.py`.

No source URLs but source titles exist
: This is expected for UI XML alone. Use share-copy extraction; do not treat titles as URLs.

Slow runs
: Disable screenshots and current focus in task `options.debug`, lower wait values carefully, and test with one question first.

Feishu rows not selected
: Check `是否本次采集` and run Feishu mode with `--dry-run` to inspect selected/skipped rows. Use `--base-start` and `--base-end` to narrow the inspected row range.
