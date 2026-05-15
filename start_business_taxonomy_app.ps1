$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

if (Test-Path -LiteralPath ".\venv\Scripts\python.exe") {
    $pythonExe = (Resolve-Path ".\venv\Scripts\python.exe").Path
} else {
    $pythonExe = "python"
}

& $pythonExe -m streamlit run ".\src\business_taxonomy_app.py"
