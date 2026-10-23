# Gera o .env com segredos fortes (não sobrescreve valores que já existem).
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

function New-Hex { $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b | ForEach-Object { $_.ToString('x2') }) -join '' }
function New-Hex16 { $b = New-Object byte[] 16; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b | ForEach-Object { $_.ToString('x2') }) -join '' }
function New-Fernet { $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b).Replace('+', '-').Replace('/', '_') }

$lines = [Collections.Generic.List[string]](Get-Content .env)
function Set-IfEmpty($key, $value) {
    $i = $lines.FindIndex({ param($l) $l -match "^$key=" })
    if ($i -ge 0 -and $lines[$i] -match "^$key=.+") { Write-Host "  ${key}: ja definido, mantido"; return }
    if ($i -ge 0) { $lines[$i] = "$key=$value" } else { $lines.Add("$key=$value") }
    Write-Host "  ${key}: gerado"
}

Write-Host 'Configurando .env'
Set-IfEmpty 'ADMIN_TOKEN' (New-Hex)
Set-IfEmpty 'HANGAR_SECRET_KEY' (New-Fernet)
Set-IfEmpty 'INTERNAL_SECRET' (New-Hex)
Set-IfEmpty 'POSTGRES_PASSWORD' (New-Hex)
# Activepieces (docker compose --profile activepieces): so usados se voce ligar o profile
Set-IfEmpty 'ACTIVEPIECES_ENCRYPTION_KEY' (New-Hex16)
Set-IfEmpty 'ACTIVEPIECES_JWT_SECRET' (New-Hex)
Set-IfEmpty 'ACTIVEPIECES_POSTGRES_PASSWORD' (New-Hex)
# Memoria dos agentes (docker compose --profile memory): so usados se voce ligar o profile
Set-IfEmpty 'MEMORY_TOKEN' (New-Hex)
Set-IfEmpty 'NEO4J_PASSWORD' (New-Hex)
Set-IfEmpty 'FALKORDB_PASSWORD' (New-Hex)
Set-IfEmpty 'MEM0_PG_PASSWORD' (New-Hex)
[IO.File]::WriteAllLines((Resolve-Path .env), $lines)
Write-Host ''
Write-Host 'Pronto. Suba com:  docker compose up -d --build'
Write-Host 'Harnesses reais:   docker compose --profile harness build   (e --profile hermes)'
Write-Host 'UI:                http://localhost:8090  (token: valor de ADMIN_TOKEN no .env)'
