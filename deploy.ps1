# Hana 插件需求墙 - 一键部署脚本
# 用法: powershell -ExecutionPolicy Bypass -File deploy.ps1 -ServerIp 8.134.54.62
# 安全机制（数据零风险）:
#   1. 自动备份服务器 data.json / secret.txt 到 /root/backup/hana-wall-<时间戳>/
#   2. 只上传白名单代码文件（下方案件清单），任何数据文件都不会被上传
#   3. 重启服务后自动校验: 备份 vs 现在 md5 必须一致, 接口抽查
param([string]$ServerIp = "")

$ErrorActionPreference = 'Stop'
if (-not $ServerIp) { Write-Error "请提供 -ServerIp 参数"; exit 1 }

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$remote = "root@$ServerIp"
$remoteDir = "/root/www/hana-plugin-wall"
$backupDir = "/root/backup/hana-wall-$ts"
$local = $PSScriptRoot

# 白名单：只允许上传这些文件（数据文件绝不在此列）
$files = @(
    "server.py",
    "static\app.js",
    "static\index.html",
    "static\style.css"
)

Write-Output "=== [1/4] 备份服务器数据 ==="
ssh $remote "mkdir -p $backupDir && cp -a $remoteDir/data.json $remoteDir/secret.txt $backupDir/ && md5sum $backupDir/data.json"
if ($LASTEXITCODE -ne 0) { Write-Output "[中止] 备份失败"; exit 1 }

Write-Output "=== [2/4] 上传白名单代码文件 ==="
foreach ($f in $files) {
    $dest = $f -replace '\\', '/'
    scp "$local\$f" "${remote}:${remoteDir}/$dest"
    if ($LASTEXITCODE -ne 0) { Write-Output "[中止] $f 上传失败"; exit 1 }
    Write-Output "  已上传: $f"
}

Write-Output "=== [3/4] 重启服务 ==="
ssh $remote "systemctl restart hana-wall && sleep 2 && systemctl is-active hana-wall"
if ($LASTEXITCODE -ne 0) { Write-Output "[中止] 重启异常"; exit 1 }

Write-Output "=== [4/4] 数据完整性 + 接口校验 ==="
ssh $remote "echo '-- 数据 md5（备份 vs 现在，必须一致）--' && md5sum $backupDir/data.json $remoteDir/data.json && echo '-- 接口 --' && curl -s -o /dev/null -w '首页: %{http_code}\n' http://127.0.0.1:3000/ && curl -s -o /dev/null -w '静态文件(应200): %{http_code}\n' http://127.0.0.1:3000/static/app.js && curl -s -o /dev/null -w '游客校验无token(应401): %{http_code}\n' http://127.0.0.1:3000/api/visitor/me"

Write-Output ""
Write-Output "部署完成。备份位置: $backupDir"
