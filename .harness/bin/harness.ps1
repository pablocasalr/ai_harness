# Ejecuta el CLI del harness desde un directorio incluido en PATH.
$script = Join-Path $PSScriptRoot '..\harness.py'
python $script @args
