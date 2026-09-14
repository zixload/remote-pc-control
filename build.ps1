# Reconstruit les deux moities de l'application :
#   dist\remote-pc-server.exe   le serveur Python, embarque par PyInstaller
#   shell\...\Remote PC.exe     l'interface WPF, c'est elle qu'on epingle
#
# Les chemins sont ancres sur le dossier du script : lance-le d'ou tu veux.
param(
    [switch]$ServerOnly,
    [switch]$ShellOnly,
    [string]$Configuration = "Release"
)

# Pas de $ErrorActionPreference = "Stop" : PyInstaller ecrit toute sa trace
# d'avancement sur stderr, et Windows PowerShell 5.1 transforme alors chaque
# ligne en erreur terminale. Les codes de retour sont testes explicitement.
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not $ShellOnly) {
    & $py -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) { & $py -m pip install pyinstaller }

    & $py -m PyInstaller --noconfirm `
        --distpath (Join-Path $PSScriptRoot "dist") `
        --workpath (Join-Path $PSScriptRoot "build") `
        (Join-Path $PSScriptRoot "remote-pc-server.spec")
    if ($LASTEXITCODE -ne 0) { throw "La construction du serveur a echoue." }

    Write-Host "`nServeur   : $(Join-Path $PSScriptRoot 'dist\remote-pc-server.exe')"
}

if (-not $ServerOnly) {
    & dotnet build (Join-Path $PSScriptRoot "shell\RemotePcShell.csproj") `
        -c $Configuration -v quiet --nologo
    if ($LASTEXITCODE -ne 0) { throw "La construction de l'interface a echoue." }

    $shell = Join-Path $PSScriptRoot "shell\bin\$Configuration\net10.0-windows10.0.19041.0\Remote PC.exe"
    Write-Host "Interface : $shell"
}
