# Campus Nexus — Workspace Environment Profile & Execution Rules

## 1. Verified Environment Profile

| Property | Value |
|---|---|
| **Operating System** | Windows (x64) |
| **Shell** | Windows PowerShell 5.1 (`powershell.exe`) |
| **Repository Root** | `S:\Campus-Nexus-Development` |
| **Project Virtualenv Python** | `S:\Campus-Nexus-Development\venv\Scripts\python.exe` (or `.\venv\Scripts\python.exe`) |
| **Python Version** | 3.14.3 |
| **Django Version** | 6.0.5 |
| **Node.js Version** | v24.14.1 (`node.exe` in PATH) |
| **Git Path** | `C:\Program Files\Git\cmd\git.exe` (`git` in PATH) |

---

## 2. Command Tool Availability & Alternatives

| Intent | Available / Preferred Command | Notes |
|---|---|---|
| **Run Django Management** | `.\venv\Scripts\python.exe manage.py <cmd>` | **Never** use bare `python` (global python lacks project venv packages) |
| **Run Tests** | `.\venv\Scripts\python.exe manage.py test <app> --settings=campus.settings.test` | Always use `--settings=campus.settings.test` for test execution |
| **Search Tracked Files** | `git grep -n '<pattern>'` | Fast and accurate for all tracked codebase files |
| **Search Arbitrary Files** | `Get-ChildItem -Recurse -File \| Select-String -Pattern '<pattern>'` | Use when untracked files must be included |
| **File Reading / Writing** | Agent native `read_file`, `write_file`, `edit_file` tools | Preferred over shell pipes |
| **Directory Contents** | `Get-ChildItem` | Native PowerShell |
| **Unavailable Unix Tools** | `rg`, `fd`, `grep`, `sed`, `awk`, `curl` | **Do not attempt** to call these executables directly |

---

## 3. Terminal Execution Guidelines

1. **Always use the virtual environment executable**:
   Use `.\venv\Scripts\python.exe` for all Python scripts, migrations, tests, and CLI tasks.
2. **Respect PowerShell syntax**:
   - Single-quote patterns in `git grep`: `git grep -n 'total_spots' coordinate/`
   - Use semicolons `;` instead of `&&` for sequencing commands when executing via PowerShell.
   - Use Windows backslashes `\` or forward slashes `/` consistently without Unix shell assumptions.
3. **Handle failures intelligently**:
   - Inspect error traces completely before retrying.
   - Never repeat a command that failed due to a missing utility or syntax error without fixing the root cause.
4. **Verify before and after changes**:
   - Run relevant unit tests via `.\venv\Scripts\python.exe manage.py test <test_label> --settings=campus.settings.test`.
   - Confirm status codes and side-effects.
