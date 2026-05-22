@echo off
if not exist "%CD%\.harness\harness.py" (
    echo Error: No .harness/harness.py found in current directory. Run from a project with harness installed.
    exit /b 1
)
python "%CD%\.harness\harness.py" %*
