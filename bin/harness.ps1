$script = Join-Path $PWD ".harness\harness.py"
if (-not (Test-Path $script)) {
    Write-Error "No .harness/harness.py found in current directory. Run from a project with harness installed."
    exit 1
}
python $script @args
